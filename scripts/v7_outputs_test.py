#!/usr/bin/env python3
"""Scoped schema-v7 full-funnel output and modular-validation tests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v7_output_contract import (
    ARTIFACT_SPECS,
    EXPERIMENTAL_USE_POLICY,
    OUTPUT_CONTRACT_VERSION,
    render_reference_contract,
)
from v7_outputs import V7OutputAdapter, V7OutputError, build_full_funnel, write_full_funnel_outputs
from v7_validation import validate_output_artifacts, validate_snapshot
from v7_validation.common import snapshot_sha256
from validate_program import validate_run as validate_public_run


def make_complete_snapshot() -> dict:
    endpoint = {
        "endpoint_id": "ENDPOINT-1",
        "role": {"status": "known", "value": "benefit", "reason": ""},
        "endpoint_type": {"status": "known", "value": "clinical_outcome", "reason": ""},
        "required": {"status": "known", "value": True, "reason": ""},
    }
    universe = {
        "source_universe_id": "UNIVERSE-1",
        "source_id": "SOURCE-1",
        "source_release": "2026-07",
        "source_snapshot_at": "2026-07-20T00:00:00Z",
        "denominator_kind": "exact_declared",
        "declared_total": 5,
    }
    plan = {
        "query_plan_id": "PLAN-1",
        "source_universe": universe,
        "query_family_id": "target_mapping",
        "required": True,
    }
    proof = {
        "coverage_proof_id": "PROOF-1",
        "query_plan": plan,
        "coverage_state": "complete_for_declared_query_and_release",
        "content_receipts": [{"content_receipt_id": "RECEIPT-1", "page_ordinal": 1}],
        "source_specific_limitations": ["declared synthetic source only"],
        "coverage_gaps": [],
        "reconciliation": {
            "returned_native_record_count": 5,
            "normalized_record_count": 5,
            "emitted_seed_count": 5,
            "unvisited_record_count": 0,
            "continuation_exhausted": True,
            "count_reconciliation_ok": True,
        },
    }
    seeds = []
    mappings = []
    routes = []
    for index in range(1, 6):
        seed_id = f"SEED-{index}"
        mapping_id = f"MAP-{index}"
        route_id = f"ROUTE-{index}"
        mappings.append(
            {
                "mapping_id": mapping_id,
                "seed_id": seed_id,
                "case_revision_id": "CASE-R1",
                "source_id": "SOURCE-1",
                "source_release": "2026-07",
                "native_record_id": f"NATIVE-{index}",
                "assertion_locator": f"record:{index}",
                "raw_intervention_assertion": f"Compound {index}",
            }
        )
        routes.append(
            {
                "route_id": route_id,
                "seed_id": seed_id,
                "source_mapping_id": mapping_id,
                "query_id": "PLAN-1",
                "retrieval_content_receipt_id": "RECEIPT-1",
            }
        )
        seeds.append(
            {
                "seed_id": seed_id,
                "case_revision_id": "CASE-R1",
                "endpoint_ids": ["ENDPOINT-1"],
                "compound_hint": {"kind": "name_hint", "value": f"Compound {index}", "namespace": "source"},
                "source_mapping_id": mapping_id,
                "discovery_route_ids": [route_id],
                "structured_routes": [],
                "evidence_modalities": ["authoritative_pharmacology"],
                "chemical_universes": ["preclinical_or_tool_compounds"],
                "development_status_hint": {"status": "known", "value": "preclinical", "reason": ""},
                "uncertainty": [],
            }
        )
    dispositions = {
        "SEED-1": ("admit", "retained_for_deep_review", "screened", None, "eligible representative"),
        "SEED-2": ("merge", "duplicate_alias", "not_screened", "SEED-1", "verified alias of SEED-1"),
        "SEED-3": ("reject", "prohibited_intervention_type", "not_screened", None, "outside declared intervention scope"),
        "SEED-4": ("quarantine", "identity_unresolved", "not_screened", None, "one-to-many identity ambiguity"),
        "SEED-5": ("baseline", "baseline_care", "not_screened", None, "baseline-care lane"),
    }
    decisions = [
        {
            "decision_id": f"DEC-{seed_id}",
            "seed_id": seed_id,
            "canonical_disposition": canonical,
            "disposition": detailed,
            "screening_outcome": screening,
            "representative_seed_id": representative,
            "reason": reason,
            "endpoint_assessments": [],
        }
        for seed_id, (canonical, detailed, screening, representative, reason) in dispositions.items()
    ]
    identities = [
        {
            "identity_resolution_id": "IR-1",
            "seed_id": "SEED-1",
            "status": "resolved",
            "verified_normalized_intervention_id": "NI-1",
            "active_moiety_id": "AM-1",
            "identity_verified": True,
            "conflict_values": [],
            "source_mapping_ids": ["MAP-1"],
        },
        {
            "identity_resolution_id": "IR-2",
            "seed_id": "SEED-2",
            "status": "resolved",
            "verified_normalized_intervention_id": "NI-1",
            "active_moiety_id": "AM-1",
            "identity_verified": True,
            "conflict_values": [],
            "source_mapping_ids": ["MAP-2"],
        },
        {
            "identity_resolution_id": "IR-3",
            "seed_id": "SEED-3",
            "status": "resolved",
            "verified_normalized_intervention_id": "NI-3",
            "active_moiety_id": "AM-3",
            "identity_verified": True,
            "conflict_values": [],
            "source_mapping_ids": ["MAP-3"],
        },
        {
            "identity_resolution_id": "IR-4",
            "seed_id": "SEED-4",
            "status": "unresolved",
            "verified_normalized_intervention_id": None,
            "active_moiety_id": None,
            "identity_verified": False,
            "conflict_values": ["NI-4A", "NI-4B"],
            "source_mapping_ids": ["MAP-4"],
        },
        {
            "identity_resolution_id": "IR-5",
            "seed_id": "SEED-5",
            "status": "resolved",
            "verified_normalized_intervention_id": "NI-5",
            "active_moiety_id": "AM-5",
            "identity_verified": True,
            "conflict_values": [],
            "source_mapping_ids": ["MAP-5"],
        },
    ]
    dimension = lambda name, band: {"dimension": name, "band": band, "ordinal": 1, "decision_rule_id": f"RULE-{name}"}
    profile = {
        "profile_id": "PROFILE-1",
        "candidate_id": "CAND-1",
        "primary_endpoint_id": "ENDPOINT-1",
        "therapeutic_support": dimension("therapeutic_support", "moderate_support"),
        "evidence_quality": dimension("evidence_quality", "moderate"),
        "mechanistic_coherence": dimension("mechanistic_coherence", "coherent"),
        "human_clinical_evidence": dimension("human_clinical_evidence", "limited"),
        "human_derived_model_evidence": dimension("human_derived_model_evidence", "supportive"),
        "endpoint_specificity": dimension("endpoint_specificity", "direct"),
        "clinical_translatability": dimension("clinical_translatability", "plausible"),
        "exposure_feasibility": dimension("exposure_feasibility", "feasible"),
        "safety_and_tolerability": dimension("safety_and_tolerability", "acceptable_with_monitoring"),
        "repurposing_readiness": dimension("repurposing_readiness", "investigational"),
        "novelty_underexploration": dimension("novelty_underexploration", "underexplored"),
        "uncertainty": dimension("uncertainty", "moderate"),
        "information_value": dimension("information_value", "high"),
    }
    package = {
        "package_id": "PACKAGE-1",
        "sources": [{"source_record_id": "SRCREC-1", "source_id": "SOURCE-1"}],
        "evidence_spans": [{"evidence_span_id": "SPAN-1", "source_record_id": "SRCREC-1", "claim_id": "CLAIM-1"}],
        "claims": [{"claim_id": "CLAIM-1", "evidence_record_ids": ["EVIDENCE-1"]}],
        "evidence_records": [{"deep_evidence_record_id": "EVIDENCE-1", "claim_id": "CLAIM-1", "source_record_id": "SRCREC-1", "evidence_span_id": "SPAN-1"}],
        "endpoint_assessments": [{"endpoint_id": "ENDPOINT-1", "status": "assessed", "reason": "grounded evidence", "claim_ids": ["CLAIM-1"]}],
    }
    return {
        "schema_version": 7,
        "snapshot_id": "SNAPSHOT-COMPLETE-1",
        "output_status": "complete",
        "case_revision": {"schema_version": 7, "case_revision_id": "CASE-R1", "case_status": "ready", "endpoints": [endpoint]},
        "source_universes": [universe],
        "query_plans": [plan],
        "coverage_proofs": [proof],
        "source_mappings": mappings,
        "discovery_routes": routes,
        "candidate_seeds": seeds,
        "identity_resolutions": identities,
        "normalized_interventions": [
            {"normalized_intervention_id": "NI-1", "canonical_name": "Compound 1", "breadth_group_id": "BG-1", "active_moiety_id": "AM-1"},
            {"normalized_intervention_id": "NI-3", "canonical_name": "Compound 3", "breadth_group_id": "BG-3", "active_moiety_id": "AM-3"},
            {"normalized_intervention_id": "NI-5", "canonical_name": "Compound 5", "breadth_group_id": "BG-5", "active_moiety_id": "AM-5"},
        ],
        "screening_decisions": decisions,
        "seed_candidate_mappings": [{"link_id": "LINK-1", "seed_id": "SEED-1", "screened_candidate_id": "CAND-1", "representative_seed_id": "SEED-1"}],
        "screened_candidates": [{"screened_candidate_id": "CAND-1", "case_revision_id": "CASE-R1", "normalized_intervention_id": "NI-1", "endpoint_ids": ["ENDPOINT-1"]}],
        "quarantined_seeds": [{"quarantine_id": "QUAR-1", "seed_id": "SEED-4", "disposition": "identity_unresolved", "identity_status": "unresolved", "reason": "one-to-many identity ambiguity", "unresolved_fields": ["normalized_intervention_id"], "source_mapping_ids": ["MAP-4"], "discovery_route_ids": ["ROUTE-4"], "alias_ids": [], "can_advance": False}],
        "deep_selection_records": [{"selection_record_id": "DSEL-1", "screened_candidate_id": "CAND-1", "selection_disposition": "selected_deep", "completion_disposition": "deep", "reason": "selected under frozen deep-review rule", "rule_version": "deep-selection-v1"}],
        "deep_candidates": [{"candidate_id": "CAND-1", "deep_evidence_package_id": "PACKAGE-1", "identity_record_id": "IDENTITY-1", "normalized_intervention_id": "NI-1", "endpoint_ids": ["ENDPOINT-1"], "claim_ids": ["CLAIM-1"], "path_ids": ["PATH-1"]}],
        "deep_evidence_packages": [package],
        "decision_profiles": [profile],
        "ranking_preparation_records": [{"preparation_id": "PREP-1", "candidate_id": "CAND-1", "profile_id": "PROFILE-1", "therapeutic_confidence_tier": "moderate_confidence", "research_priority_tier": "high_information_priority"}],
        "audit_assignments": [{"assignment_id": "ASSIGN-1", "candidate_id": "CAND-1", "selection_status": "selected_for_audit", "strata": ["finalist_census"], "reason": "provisional finalist census"}],
        "audit_corrections": [{"correction_id": "CORR-1", "candidate_id": "CAND-1", "assignment_id": "ASSIGN-1", "authority_field": "claim_statement", "action": "correct", "prior_value_sha256": "A" * 64, "replacement_value_sha256": "B" * 64, "rationale": "qualify wording"}],
        "audit_records": [{"audit_record_id": "AUDIT-1", "assignment_id": "ASSIGN-1", "candidate_id": "CAND-1", "outcome": "qualify", "decision_effect": "qualified", "correction_ids": ["CORR-1"], "checked_source_ids": ["SOURCE-1"], "checked_evidence_span_ids": ["SPAN-1"], "independent_search_receipt_ids": ["RECEIPT-AUDIT-1"]}],
        "council_records": [],
        "portfolio_rank_records": [{"candidate_id": "CAND-1", "evidence_strength_rank": 1, "novelty_information_value_rank": 1, "diversified_portfolio_rank": 1, "disposition": "finalist", "evidence_component": 7, "novelty_information_component": 5, "diversity_component": 4, "total_selection_utility": 16, "diversity_contributions": [{"dimension": "target_mechanism", "new_values": ["TARGET-1"], "weight": 1}], "audit_status": "selected_for_audit", "audit_outcome": "qualify", "council_disposition": None, "reason": "audited finalist"}],
        "provenance": {"canonical_scientific_hash": "F" * 64, "execution_hash": "E" * 64, "commit_ids": ["COMMIT-1", "COMMIT-2"]},
    }


class FullFunnelOutputTests(unittest.TestCase):
    def test_complete_snapshot_builds_every_reconciled_artifact(self) -> None:
        result = V7OutputAdapter().build_full_funnel(make_complete_snapshot())
        self.assertEqual(result["output_contract_version"], OUTPUT_CONTRACT_VERSION)
        self.assertEqual(set(result["artifact_payloads"]), {row.filename for row in ARTIFACT_SPECS})
        self.assertTrue(all(value is True for key, value in result["reconciliation"].items() if key.endswith("_balanced")))
        self.assertEqual(result["reconciliation"]["seed_count"], 5)
        self.assertEqual(result["reconciliation"]["identity_admitted_count"], 1)
        self.assertEqual(result["reconciliation"]["deep_count"], 1)
        self.assertEqual(result["reconciliation"]["finalist_count"], 1)
        summary = result["artifact_payloads"]["full_funnel_summary.md"]
        cards = result["artifact_payloads"]["candidate_evidence_cards.md"]
        self.assertIn(EXPERIMENTAL_USE_POLICY, summary)
        self.assertIn(EXPERIMENTAL_USE_POLICY, cards)

    def test_written_artifacts_validate_hashes_and_ledger_counts(self) -> None:
        snapshot = make_complete_snapshot()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path, _ = write_full_funnel_outputs(root, snapshot)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(validate_output_artifacts(root / "outputs_v7", snapshot, manifest), [])
            tampered = copy.deepcopy(manifest)
            tampered["artifacts"][0]["row_count"] += 1
            self.assertIn("ROW_COUNT", {row.code for row in validate_output_artifacts(root / "outputs_v7", snapshot, tampered)})

    def test_seed_loss_or_disposition_duplication_fails_closed(self) -> None:
        snapshot = make_complete_snapshot()
        snapshot["screening_decisions"].pop()
        with self.assertRaisesRegex(V7OutputError, "DISPOSITION_COVERAGE|RECONCILIATION"):
            build_full_funnel(snapshot)

    def test_merge_quarantine_and_exclusions_remain_visible(self) -> None:
        result = build_full_funnel(make_complete_snapshot())
        identities = result["artifact_payloads"]["identity_normalization_and_merges.jsonl"]
        exclusions = result["artifact_payloads"]["exclusions_and_reasons.csv"]
        unresolved = result["artifact_payloads"]["unresolved_and_quarantined_seeds.csv"]
        self.assertIn('"representative_seed_id":"SEED-1"', identities)
        self.assertIn("prohibited_intervention_type", exclusions)
        self.assertIn("one-to-many identity ambiguity", unresolved)
        self.assertIn("SOURCE-1", result["artifact_payloads"]["candidate_evidence_cards.md"])
        self.assertIn("candidate_uncertainty", result["artifact_payloads"]["uncertainty_and_evidence_gaps.jsonl"])

    def test_complete_status_rejects_interim_portfolio_state(self) -> None:
        snapshot = make_complete_snapshot()
        snapshot["portfolio_rank_records"][0]["disposition"] = "unaudited"
        with self.assertRaisesRegex(V7OutputError, "INTERIM_PORTFOLIO"):
            build_full_funnel(snapshot)

    def test_contract_reference_is_generated_from_typed_specs(self) -> None:
        reference = Path(__file__).resolve().parents[1] / "references" / "outputs-validation.md"
        self.assertEqual(reference.read_text(encoding="utf-8-sig"), render_reference_contract())

    def test_modular_snapshot_validator_reports_domain_codes(self) -> None:
        snapshot = make_complete_snapshot()
        snapshot["case_revision"]["case_status"] = "needs_resolution"
        issues = validate_snapshot(snapshot)
        self.assertIn("case_endpoints", {row.domain for row in issues})
        self.assertIn("CASE_NOT_READY", {row.code for row in issues})

    def test_public_validator_routes_native_v7_and_keeps_one_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "schema_manifest.json").write_text('{"schema_version":7}\n', encoding="utf-8")
            with patch("v7_validation.validate_run", return_value=["v7-routed"]) as routed:
                self.assertEqual(validate_public_run(root), ["v7-routed"])
            routed.assert_called_once_with(root, final=True)

    def test_snapshot_hash_excludes_derived_output_manifest_collection(self) -> None:
        snapshot = make_complete_snapshot()
        before = snapshot_sha256(snapshot)
        snapshot["output_manifests"] = [{"output_manifest_id": "OUTPUT-1"}]
        self.assertEqual(snapshot_sha256(snapshot), before)


if __name__ == "__main__":
    unittest.main()
