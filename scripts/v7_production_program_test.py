#!/usr/bin/env python3
"""End-to-end acceptance for the persisted eight-stage schema-v7 programme."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from v7_case_model import build_case_bundle, initialize_case
from v7_chemical_target_adapters import OpenTargetsEntityKind, make_open_targets_plan
from v7_production_disposition import NORMALIZATION_POLICY_VERSION
from v7_production_portfolio import V7PortfolioAdapter, preview_audit_freeze
from v7_production_portfolio_test import _outcome, _plan
from v7_output_contract import EXPERIMENTAL_USE_POLICY
from v7_production_program import V7ProgramAdapter
from v7_production_screen_deep import (
    DEEP_SELECTION_POLICY_VERSION,
    SCREEN_RULE_VERSION,
    build_screened_candidate,
)
from v7_production_screen_deep_test import STRUCTURES, _screen, _source_identity, make_deep_result
from v7_validation import load_committed_snapshot, validate_run, validate_snapshot


ROOT = Path(__file__).resolve().parents[1]
CHEMICAL_FIXTURE = ROOT / "benchmarks" / "schema_v7" / "chemical_target_adapters" / "frozen_responses.json"


def _source_plan(case: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = make_open_targets_plan(
        source_release="26.06",
        source_snapshot_at="2026-07-21T00:00:00Z",
        entity_kind=OpenTargetsEntityKind.TARGET,
        entity_id="ENSG00000141510",
        endpoint_ids=tuple(row.endpoint_id for row in case.endpoints),
        disease_state_id="MONDO_0004979",
    )
    fixture = json.loads(CHEMICAL_FIXTURE.read_text(encoding="utf-8"))
    return (
        {
            "source_plan_revision": "end-to-end-program-r1",
            "branches": [
                {
                    "branch_id": "open-targets",
                    "adapter_id": "open-targets-graphql-v4",
                    "query_plan": plan,
                }
            ],
            "explicit_gaps": [],
        },
        {"open-targets": [fixture["responses"]["open_targets_target"]]},
    )


def _resolver(case: Any, discovery: Mapping[str, Any]) -> dict[str, Any]:
    source = {
        "resolver_source_id": "PROGRAM-AUTHORITY",
        "authority": "Frozen exact identity authority",
        "authority_release": "2026-07-21",
        "snapshot_id": "PROGRAM-AUTHORITY-SNAPSHOT",
        "snapshot_sha256": "PROGRAM-AUTHORITY-CONTENT-HASH",
        "method": "frozen_authority_record",
        "locator": "frozen://program-authority",
    }
    seed_results = []
    assertions = []
    for seed, structure in zip(sorted(discovery["seeds"], key=lambda row: row["seed_id"]), STRUCTURES):
        entity_kind, smiles, inchi, key = structure
        seed_results.append(
            {
                "seed_id": seed["seed_id"],
                "result_status": "resolved",
                "case_role": "repurposing",
                "reason_code": "authority_resolution_complete",
                "reason": "Frozen authorities resolved one exact intervention.",
                "resolver_source_ids": ["PROGRAM-AUTHORITY"],
            }
        )
        assertions.append(
            {
                "seed_id": seed["seed_id"],
                "resolver_source_id": "PROGRAM-AUTHORITY",
                "authority_record_id": f"PROGRAM-AUTHORITY:{key}",
                "authority_locator": f"frozen://program-authority/{key}",
                "assertion_status": "resolved",
                "reported_identity": seed["compound_hint"]["value"],
                "identity": _source_identity(
                    entity_kind=entity_kind,
                    smiles=smiles,
                    inchi=inchi,
                    inchikey=key,
                ),
                "unresolved_reason": None,
                "candidate_identities": [],
            }
        )
    return {
        "resolver_revision": "end-to-end-program-r1",
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "resolver_sources": [source],
        "seed_results": seed_results,
        "identity_assertions": assertions,
    }


def _evidence(case: Any, disposition: Mapping[str, Any]) -> dict[str, Any]:
    admitted = sorted(
        (
            row for row in disposition["seed_dispositions"]
            if row["canonical_disposition"] == "admit"
        ),
        key=lambda row: row["seed_id"],
    )
    screens = [
        _screen(case, row, index=index, statuses=tuple("supportive" for _ in case.endpoints))
        for index, row in enumerate(admitted)
    ]
    candidates = [
        build_screened_candidate(case, disposition, row["normalized_intervention_id"])
        for row in admitted
    ]
    structure_by_seed = {
        seed["seed_id"]: structure
        for seed, structure in zip(sorted(disposition["seeds"], key=lambda row: row["seed_id"]), STRUCTURES)
    }
    deep_results = [
        make_deep_result(
            case,
            candidate,
            structure_by_seed[candidate.representative_seed_id],
            unsafe=False,
            include_counterevidence=True,
        )
        for candidate in candidates
    ]
    return {
        "evidence_revision": "end-to-end-program-r1",
        "screen_rule_version": SCREEN_RULE_VERSION,
        "candidate_screens": screens,
        "deep_selection_policy": {
            "policy_version": DEEP_SELECTION_POLICY_VERSION,
            "capacity": len(candidates),
            "allocation_rule": "round_robin_declared_strata",
            "tie_rule": "candidate_id_ascending",
            "strata": [
                {
                    "stratum_id": "supportive_or_mixed_evidence",
                    "capacity": len(candidates),
                },
                {"stratum_id": "sparse_or_unknown_evidence", "capacity": 0},
                {"stratum_id": "preclinical_only", "capacity": 0},
            ],
        },
        "deep_results": deep_results,
    }


def _audit(case: Any, deep: Mapping[str, Any]) -> dict[str, Any]:
    plan = _plan(deep, revision="end-to-end-program-r1")
    freeze = preview_audit_freeze(case, deep, plan)
    outcomes = [
        _outcome(row)
        for row in freeze["audit_assignments"]
        if row["selection_status"] == "selected_for_audit"
    ]
    return {
        "plan": plan,
        "audit_outcomes": outcomes,
        "corrections": [],
        "council_reviews": [],
    }


class ProductionProgramTests(unittest.TestCase):
    def test_all_eight_stages_persist_validate_and_replay_without_drift(self) -> None:
        fixture = json.loads(CHEMICAL_FIXTURE.read_text(encoding="utf-8"))
        case = build_case_bundle(fixture["case_input"]).case_revision
        source_plan, pages = _source_plan(case)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            initialize_case(root, fixture["case_input"])
            adapter = V7ProgramAdapter(
                root,
                {
                    "max_active_jobs": 8,
                    "source_budget": 100,
                    "seed_budget": 100,
                    "deep_review_budget": 20,
                    "audit_budget": 100,
                },
            )
            result = dict(
                adapter.execute(case, source_plan, pages, _resolver, _evidence, _audit)
            )

            self.assertEqual(result["stage_status"], {str(index): "pass" for index in range(1, 9)})
            self.assertEqual(result["runtime_status"], "complete")
            self.assertEqual(result["model_version"], "schema-v7-production-program-v2")
            self.assertTrue(result["hypothesis_generation_only"])
            self.assertTrue(result["experimental_use"])
            self.assertEqual(result["experimental_use_policy"], EXPERIMENTAL_USE_POLICY)
            self.assertEqual(validate_run(root, final=True), [])
            snapshot = load_committed_snapshot(root)
            self.assertEqual(validate_snapshot(snapshot), [])
            self.assertEqual(snapshot["output_status"], "complete")
            self.assertEqual(len(snapshot["candidate_seeds"]), 2)
            self.assertEqual(len(snapshot["screening_decisions"]), 2)
            self.assertEqual(len(snapshot["deep_candidates"]), 2)
            self.assertEqual(len(snapshot["portfolio_rank_records"]), 2)
            self.assertTrue((root / "outputs_v7" / "artifact_manifest.json").is_file())

            manifest_bytes = adapter.manifest_path.read_bytes()
            manifest_mtime = adapter.manifest_path.stat().st_mtime_ns
            replay = adapter.execute(case, source_plan, pages, _resolver, _evidence, _audit)
            self.assertEqual(result, replay)
            self.assertEqual(adapter.manifest_path.read_bytes(), manifest_bytes)
            self.assertEqual(adapter.manifest_path.stat().st_mtime_ns, manifest_mtime)

            drift = copy.deepcopy(pages)
            drift["open-targets"][0]["data"]["target"]["approvedSymbol"] = "DRIFT"
            with self.assertRaisesRegex(ValueError, "replayed with different"):
                adapter.execute(case, source_plan, drift, _resolver, _evidence, _audit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
