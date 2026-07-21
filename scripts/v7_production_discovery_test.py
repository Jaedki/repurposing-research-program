#!/usr/bin/env python3
"""Production acceptance tests for the whole-case schema-v7 discovery aggregate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from v7_case_model import build_case_bundle
from v7_chemical_target_adapters import (
    OpenTargetsEntityKind,
    PubChemQueryKind,
    make_chembl_plan,
    make_open_targets_plan,
    make_pubchem_plan,
)
from v7_discovery import CausalRoute
from v7_extended_discovery_adapters import (
    ClinicalTrialsBranch,
    make_clinical_trials_plan,
)
from v7_production_discovery import (
    DiscoveryAggregateConflictError,
    V7DiscoveryAdapter,
    validate_discovery_aggregate,
)


ROOT = Path(__file__).resolve().parents[1]
CHEMICAL_FIXTURE = ROOT / "benchmarks" / "schema_v7" / "chemical_target_adapters" / "frozen_responses.json"
EXTENDED_FIXTURE = ROOT / "benchmarks" / "schema_v7" / "extended_discovery_adapters" / "frozen_responses.json"


class ProductionDiscoveryAggregateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.chemical = json.loads(CHEMICAL_FIXTURE.read_text(encoding="utf-8"))
        cls.extended = json.loads(EXTENDED_FIXTURE.read_text(encoding="utf-8"))
        cls.case = build_case_bundle(cls.chemical["case_input"]).case_revision
        cls.endpoint_id = cls.case.endpoints[0].endpoint_id

    @staticmethod
    def source_plan(revision: str, *branches: tuple[str, str, Any], gaps: list[Any] | None = None):
        return {
            "source_plan_revision": revision,
            "branches": [
                {"branch_id": branch_id, "adapter_id": adapter_id, "query_plan": plan}
                for branch_id, adapter_id, plan in branches
            ],
            "explicit_gaps": gaps or [],
        }

    def open_targets_plan(self, entity_id: str, *, kind: OpenTargetsEntityKind = OpenTargetsEntityKind.TARGET):
        return make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-21T00:00:00Z",
            entity_kind=kind,
            entity_id=entity_id,
            endpoint_ids=(self.endpoint_id,),
            disease_state_id="MONDO_0004979",
        )

    def clinical_plan(self, branch: ClinicalTrialsBranch):
        return make_clinical_trials_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T14:00:00Z",
            branch=branch,
            condition_query="Rareopathy",
            endpoint_ids=(self.endpoint_id,),
            causal_route=CausalRoute.DOWNSTREAM_OR_BYPASS_RESTORATION,
        )

    def test_live_response_shapes_pagination_and_lossless_mapping_reconciliation(self) -> None:
        open_targets = self.open_targets_plan("ENSG00000141510")
        chembl = make_chembl_plan(
            source_release="ChEMBL 36",
            source_snapshot_at="2026-07-20T00:00:00Z",
            resource="activity",
            filters={"target_chembl_id": "CHEMBL-TARGET-1"},
            page_size=2,
            endpoint_ids=(self.endpoint_id,),
            origin_kind="target",
            origin_ids=("CHEMBL-TARGET-1",),
            target_id="CHEMBL-TARGET-1",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0004979",
        )
        pubchem_gene = make_pubchem_plan(
            source_release="2026-07-21",
            source_snapshot_at="2026-07-21T00:00:00Z",
            query_kind=PubChemQueryKind.GENE_ASSAY_IDS,
            identifier="285175",
            endpoint_ids=(self.endpoint_id,),
            target_id="285175",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0014777",
        )
        pubchem_concise = make_pubchem_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T00:00:00Z",
            query_kind=PubChemQueryKind.ASSAY_CONCISE,
            identifier="1001",
            endpoint_ids=(self.endpoint_id,),
            target_id="7157",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0004979",
        )
        trials = self.clinical_plan(ClinicalTrialsBranch.INTERVENTION_ENUMERATION)
        failed_trial = self.clinical_plan(ClinicalTrialsBranch.FAILED_TERMINATED_OR_NEGATIVE)
        current_pubchem_shape = {
            "InformationList": {
                "Information": [
                    {"GeneID": 285175, "AID": [1904, 624099]},
                    {"GeneID": 285175, "AID": [651810]},
                ]
            }
        }
        plan = self.source_plan(
            "production-live-shapes-r1",
            ("open-targets", "open-targets-graphql-v4", open_targets),
            ("chembl-activity", "chembl-data-web-services", chembl),
            ("pubchem-gene", "pubchem-pug-rest", pubchem_gene),
            ("pubchem-concise", "pubchem-pug-rest", pubchem_concise),
            ("clinical-trials", "clinicaltrials-gov-api-v2", trials),
            ("failed-trial", "clinicaltrials-gov-api-v2", failed_trial),
        )
        pages = {
            "open-targets": [self.chemical["responses"]["open_targets_target"]],
            "chembl-activity": self.chemical["responses"]["chembl_activity_pages"],
            "pubchem-gene": [current_pubchem_shape],
            "pubchem-concise": [self.chemical["responses"]["pubchem_assay_concise"]],
            "clinical-trials": self.extended["responses"]["clinical_intervention_pages"],
            "failed-trial": [self.extended["responses"]["clinical_failed_page"]],
        }
        with tempfile.TemporaryDirectory() as directory:
            adapter = V7DiscoveryAdapter(directory)
            case_mapping = {
                "original_input": self.chemical["case_input"],
                "case_revision_id": self.case.case_revision_id,
            }
            result = adapter.retrieve_and_seed(case_mapping, plan, pages)
            target = adapter.aggregate_path(result["case_revision_id"], result["source_plan_id"])
            self.assertTrue(target.is_file())
            self.assertTrue(list(target.parent.glob("executions/*.json")))
            self.assertNotIn("execution_receipts", result)
            validate_discovery_aggregate(self.case, result)

        branches = {row["branch_id"]: row for row in result["branches"]}
        self.assertEqual(branches["chembl-activity"]["reconciliation"]["retrieved_page_count"], 2)
        self.assertEqual(branches["clinical-trials"]["reconciliation"]["retrieved_page_count"], 2)
        self.assertEqual(result["reconciliation"]["returned_native_item_count"], 17)
        self.assertEqual(result["reconciliation"]["mapping_outcome_occurrence_count"], 17)
        self.assertEqual(result["reconciliation"]["failed_native_item_count"], 0)
        self.assertEqual(result["reconciliation"]["unreconciled_native_item_count"], 0)
        self.assertEqual(result["reconciliation"]["unique_seed_count"], 11)
        self.assertTrue(result["closure"]["all_declared_branches_complete"])
        self.assertFalse(result["closure"]["global_coverage_claimed"])
        outcomes = result["mapping_outcomes"]
        self.assertTrue(any(row["disposition"] == "non_intervention_type_excluded" for row in outcomes))
        assertions = [
            assertion["assertion"]
            for outcome in outcomes
            for assertion in outcome["assertion_outcomes"]
        ]
        self.assertTrue(
            any(
                "Inactive" in observation["assay_context"]
                for assertion in assertions
                for observation in assertion["activity_observations"]
            )
        )
        self.assertTrue(
            any(
                "no improvement" in annotation["source_text"].casefold()
                for assertion in assertions
                for annotation in assertion["evidence_annotations"]
            )
        )

    def test_declared_query_overlap_reduces_seeds_without_losing_occurrences(self) -> None:
        plan = self.source_plan(
            "overlap-r1",
            ("target-a", "open-targets-graphql-v4", self.open_targets_plan("ENSG-A")),
            ("target-b", "open-targets-graphql-v4", self.open_targets_plan("ENSG-B")),
        )
        payload = self.chemical["responses"]["open_targets_target"]
        with tempfile.TemporaryDirectory() as directory:
            result = V7DiscoveryAdapter(directory).retrieve_and_seed(
                self.case, plan, {"target-a": [payload], "target-b": [payload]}
            )
        reconciliation = result["reconciliation"]
        self.assertEqual(reconciliation["seed_emission_occurrence_count"], 4)
        self.assertEqual(reconciliation["unique_seed_count"], 2)
        self.assertEqual(reconciliation["query_overlap_reduction_count"], 2)
        self.assertEqual(reconciliation["mapping_outcome_occurrence_count"], 6)
        self.assertEqual(len(result["discovery_routes"]), 4)

    def test_failures_and_unsupported_sources_are_explicit_and_prevent_required_closure(self) -> None:
        failed = self.open_targets_plan("ENSG-FAIL")
        unsupported = self.open_targets_plan(
            "EFO-UNSUPPORTED", kind=OpenTargetsEntityKind.DISEASE
        )
        plan = self.source_plan(
            "failure-r1",
            ("failed", "open-targets-graphql-v4", failed),
            ("licensed-source", "licensed-private-source-v1", unsupported),
            gaps=[{"source_id": "commercial-claims", "reason": "Licence was not furnished."}],
        )
        pages = {
            "failed": [
                {
                    "error": {
                        "code": "FROZEN_HTTP_503",
                        "message": "Declared source was unavailable.",
                        "retryable": False,
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            result = V7DiscoveryAdapter(directory).retrieve_and_seed(self.case, plan, pages)
        states = {row["branch_id"]: row["coverage_state"] for row in result["branches"]}
        self.assertEqual(states["failed"], "failed_retrieval")
        self.assertEqual(states["licensed-source"], "unsupported_source_capability")
        self.assertFalse(result["closure"]["required_branches_complete"])
        self.assertIn("diagnostic partial", result["closure"]["statement"])
        gap_branches = {row.get("branch_id") for row in result["explicit_gaps"]}
        self.assertIn("failed", gap_branches)
        self.assertIn("licensed-source", gap_branches)
        self.assertTrue(any(row["gap_kind"] == "declared_plan_gap" for row in result["explicit_gaps"]))

    def test_persisted_replay_is_identical_and_refuses_frozen_input_drift(self) -> None:
        query = self.open_targets_plan("ENSG00000141510")
        plan = self.source_plan(
            "replay-r1", ("open-targets", "open-targets-graphql-v4", query)
        )
        pages = {"open-targets": [self.chemical["responses"]["open_targets_target"]]}
        with tempfile.TemporaryDirectory() as directory:
            adapter = V7DiscoveryAdapter(directory)
            first = adapter.retrieve_and_seed(self.case, plan, pages)
            target = adapter.aggregate_path(first["case_revision_id"], first["source_plan_id"])
            before = target.read_bytes()
            replay = adapter.retrieve_and_seed(self.case, plan, pages)
            self.assertEqual(first, replay)
            self.assertEqual(before, target.read_bytes())
            changed = json.loads(json.dumps(pages))
            changed["open-targets"][0]["data"]["target"]["drugAndClinicalCandidates"]["count"] += 1
            with self.assertRaisesRegex(DiscoveryAggregateConflictError, "different declarations or frozen pages"):
                adapter.retrieve_and_seed(self.case, plan, changed)

    def test_real_adapter_preserves_every_seed_at_500_and_1000_scale(self) -> None:
        for count in (500, 1000):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                rows = [
                    {
                        "id": f"ROW-{index:05d}",
                        "maxClinicalStage": 0,
                        "drug": {
                            "id": f"CHEMBL-SCALE-{index:05d}",
                            "name": f"Database-only scale compound {index:05d}",
                            "drugType": "Small molecule",
                            "maximumClinicalStage": 0,
                        },
                        "diseases": [],
                    }
                    for index in range(count)
                ]
                payload = {
                    "data": {
                        "target": {
                            "id": "ENSG-SCALE",
                            "approvedSymbol": "SCALE",
                            "drugAndClinicalCandidates": {"count": count, "rows": rows},
                        }
                    }
                }
                query = self.open_targets_plan(f"ENSG-SCALE-{count}")
                plan = self.source_plan(
                    f"scale-{count}-r1",
                    ("scale", "open-targets-graphql-v4", query),
                )
                result = V7DiscoveryAdapter(directory).retrieve_and_seed(
                    self.case, plan, {"scale": [payload]}
                )
                reconciliation = result["reconciliation"]
                self.assertEqual(reconciliation["returned_native_item_count"], count)
                self.assertEqual(reconciliation["mapping_outcome_occurrence_count"], count)
                self.assertEqual(reconciliation["eligible_intervention_assertion_occurrence_count"], count)
                self.assertEqual(reconciliation["unique_seed_count"], count)
                self.assertEqual(reconciliation["unreconciled_native_item_count"], 0)
                self.assertEqual(reconciliation["unreconciled_eligible_assertion_count"], 0)
                self.assertEqual(len(result["seeds"]), count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
