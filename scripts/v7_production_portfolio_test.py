#!/usr/bin/env python3
"""Production acceptance for the persisted schema-v7 audit/portfolio adapter."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from v7_production_portfolio import (
    AUDIT_CATEGORIES,
    AUDIT_PLAN_VERSION,
    PortfolioAggregateConflictError,
    PortfolioAggregateError,
    V7PortfolioAdapter,
    content_sha256,
    make_frozen_audit_search,
    preview_audit_freeze,
    validate_portfolio_aggregate,
)
from v7_production_screen_deep import V7ScreenDeepAdapter
from v7_production_screen_deep_test import make_production_fixture


def _subject_ids(deep_frame: Mapping[str, Any]) -> list[str]:
    result: set[str] = set()
    for deep in deep_frame["deep_packages"]:
        package = deep["package"]
        result.add(package["current_identity_record_id"])
        result.update(row["claim_id"] for row in package["claims"])
        result.update(row["source_record_id"] for row in package["sources"])
        result.update(row["safety_record_id"] for row in deep["structured_safety"])
        result.update(row["exposure_record_id"] for row in deep["structured_exposure"])
    result.update(row["screen_record_id"] for row in deep_frame["screen_records"])
    admitted = deep_frame["retained_inputs"]["admitted_frame"]
    result.update(row["seed_id"] for row in admitted["seeds"])
    return sorted(result)


def _plan(deep_frame: Mapping[str, Any], *, revision: str = "portfolio-r1") -> dict[str, Any]:
    scaffolds = []
    for index, deep in enumerate(sorted(deep_frame["deep_packages"], key=lambda row: row["candidate_id"]), 1):
        scaffolds.append(
            {
                "candidate_id": deep["candidate_id"],
                "scaffold_key": f"BEMIS-MURCKO-{index}",
                "method": "bemis_murcko",
                "version": "rdkit-policy-2026-07",
                "identity_record_ids": [deep["package"]["current_identity_record_id"]],
            }
        )
    rules = [
        {
            "category": category,
            "risk_level": "high" if category in {"claim_impact", "safety_risk", "identity_uncertainty"} else "moderate",
            "minimum": 1,
            "rate_basis_points": 2500,
            "maximum": 3,
            "acceptance_threshold": 0,
            "escalation_mode": "quarantine_unaudited",
        }
        for category in AUDIT_CATEGORIES
    ]
    return {
        "plan_version": AUDIT_PLAN_VERSION,
        "audit_revision": revision,
        "sampling_seed": "production-portfolio-deterministic-seed",
        "sampling_rules": rules,
        "subject_author_ids": {
            subject_id: [f"author-{content_sha256(subject_id)[:12]}"]
            for subject_id in _subject_ids(deep_frame)
        },
        "portfolio_policy": {
            "finalist_capacity": 1,
            "reserve_capacity": 1,
            "evidence_weight": 5,
            "information_weight": 2,
            "diversity_weight": 3,
            "diversity_dimension_weights": {
                "target_mechanism": 1,
                "causal_route": 1,
                "chemical_scaffold": 1,
                "evidence_modality": 1,
                "endpoint": 1,
                "development_status": 1,
                "uncertainty": 1,
            },
            "allowed_therapeutic_tiers": [
                "high_confidence",
                "moderate_confidence",
                "low_confidence_hypothesis",
            ],
        },
        "scaffolds": scaffolds,
        "supersedes_portfolio_aggregate_id": None,
    }


def _outcome(assignment: Mapping[str, Any], *, outcome: str = "support", effect: str = "no_change") -> dict[str, Any]:
    subject_id = assignment["subject_id"]
    payload = (
        f"Independent original-content audit for {subject_id}. "
        "The retained record was checked against the declared case and counterevidence search."
    )
    support = "The retained record was checked against the declared case and counterevidence search."
    return {
        "assignment_id": assignment["assignment_id"],
        "outcome": outcome,
        "decision_effect": effect,
        "auditor_id": f"auditor-{content_sha256(assignment['assignment_id'])[:12]}",
        "independent_searches": [
            make_frozen_audit_search(
                source_id=f"AUDIT-SOURCE-{content_sha256(subject_id)[:12]}",
                source_release="2026-07-21",
                query=f"independent verification {subject_id}",
                native_record_id=f"AUDIT-NATIVE-{content_sha256(subject_id)[:12]}",
                locator=f"frozen://audit/{subject_id}",
                payload=payload,
                support_text=support,
            )
        ],
        "rationale": "Independent retained-content and counterevidence checks completed.",
        "ranking_revision_id": (
            f"RANKING-REVISION-{content_sha256(subject_id)[:16]}" if effect == "reranked" else None
        ),
    }


def make_portfolio_fixture(
    root: Path,
    *,
    revision: str = "portfolio-r1",
    include_correction: bool = True,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    case, admitted, frozen = make_production_fixture(
        root / "upstream",
        first_deep_counterevidence=False,
        second_deep_unsafe=False,
    )
    deep_frame = dict(
        V7ScreenDeepAdapter(root / "screen-deep").screen_and_deepen(case, admitted, frozen)
    )
    plan = _plan(deep_frame, revision=revision)
    freeze = preview_audit_freeze(case, deep_frame, plan)
    selected = [
        row for row in freeze["audit_assignments"]
        if row["selection_status"] == "selected_for_audit"
    ]
    outcomes = [_outcome(row) for row in selected]
    corrections: list[dict[str, Any]] = []
    council: list[dict[str, Any]] = []
    if include_correction:
        first_candidate = sorted(row["candidate_id"] for row in deep_frame["deep_packages"])[0]
        candidate_assignment = next(
            row for row in selected
            if row["candidate_id"] == first_candidate
            and next(
                unit for unit in freeze["audit_units"]
                if unit["audit_unit_id"] == row["audit_unit_id"]
            )["unit_kind"] == "deep_claim"
        )
        for row in outcomes:
            if row["assignment_id"] == candidate_assignment["assignment_id"]:
                row.update(
                    {
                        "outcome": "correct",
                        "decision_effect": "reranked",
                        "ranking_revision_id": f"RANKING-REVISION-{content_sha256(first_candidate)[:16]}",
                    }
                )
        deep = next(row for row in deep_frame["deep_packages"] if row["candidate_id"] == first_candidate)
        prior = deep["ranking_preparation"]
        replacement = copy.deepcopy(prior)
        replacement["preparation_id"] = f"RANK-PREP-AUDIT-REVISED-{content_sha256(prior)[:12]}"
        corrections.append(
            {
                "assignment_id": candidate_assignment["assignment_id"],
                "candidate_id": first_candidate,
                "authority_field": "ranking_feature",
                "target_record_id": prior["preparation_id"],
                "action": "correct",
                "prior_value_sha256": content_sha256(prior),
                "replacement_record_id": replacement["preparation_id"],
                "replacement_value": replacement,
                "parent_correction_id": None,
                "rationale": "Audit corrects the preparation record identity while preserving every scientific band.",
            }
        )
        claim_subject = candidate_assignment["subject_id"]
        council.append(
            {
                "candidate_id": first_candidate,
                "disposition": "retain",
                "rationale": "The typed audit revision is internally consistent and retains eligibility.",
                "issues": [
                    {
                        "issue_kind": "ranking_feature",
                        "decision_impact": "ordering",
                        "subject_ids": [claim_subject],
                        "finding": "confirmed",
                        "reviewer_id": f"council-reviewer-{content_sha256(first_candidate)[:12]}",
                        "evidence_ancestry_cluster_ids": [f"ANCESTRY-{content_sha256(claim_subject)[:12]}"],
                        "rationale": "Independent evidence ancestry confirms the corrected ordering record.",
                    }
                ],
            }
        )
    bundle = {
        "plan": plan,
        "audit_outcomes": outcomes,
        "corrections": corrections,
        "council_reviews": council,
    }
    return case, deep_frame, bundle


class ProductionPortfolioAcceptanceTests(unittest.TestCase):
    def run_adapter(self) -> tuple[Any, dict[str, Any], dict[str, Any], V7PortfolioAdapter, dict[str, Any]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, deep_frame, bundle = make_portfolio_fixture(root)
        adapter = V7PortfolioAdapter(root / "portfolio")
        aggregate = dict(adapter.audit_and_select(case, deep_frame, bundle))
        validate_portfolio_aggregate(case, aggregate)
        return case, deep_frame, bundle, adapter, aggregate

    def test_complete_retrieval_backed_portfolio_reconciliation(self) -> None:
        case, _, _, adapter, aggregate = self.run_adapter()
        self.assertTrue(aggregate["stage_gate_passed"])
        self.assertEqual(aggregate["aggregate_status"], "complete")
        self.assertEqual(len(aggregate["finalists"]), 1)
        self.assertEqual(len(aggregate["reserves"]), 1)
        self.assertEqual(len(aggregate["portfolio_dispositions"]), 2)
        self.assertEqual(len(aggregate["seven_decision_outputs"]), 2)
        self.assertEqual(len(aggregate["evidence_strength_ranking"]), 2)
        self.assertEqual(len(aggregate["novelty_information_value_ranking"]), 2)
        self.assertEqual(len(aggregate["diversified_portfolio_ranking"]), 2)
        self.assertEqual(len(aggregate["canonical_order"]), 2)
        self.assertTrue(aggregate["council_records"])
        self.assertTrue(aggregate["audit_corrections"])
        self.assertTrue(aggregate["reconciliation"]["portfolio_equation_balanced"])
        self.assertTrue(aggregate["reconciliation"]["audit_coverage_reconciled"])
        self.assertGreater(aggregate["reconciliation"]["explicit_unaudited_count"], 0)
        self.assertTrue(
            any(
                row["selection_status"] == "unaudited"
                for row in aggregate["audit_assignments"]
            )
        )
        self.assertTrue(aggregate["reconciliation"]["seven_outputs_per_deep_candidate"])
        self.assertTrue(aggregate["reconciliation"]["three_rankings_cover_every_deep_candidate"])
        self.assertTrue(
            all(
                row["therapeutic_support_sha256_before_portfolio"]
                == row["therapeutic_support_sha256_after_portfolio"]
                and row["evidence_quality_sha256_before_portfolio"]
                == row["evidence_quality_sha256_after_portfolio"]
                and not row["novelty_or_diversity_modified_therapeutic_support"]
                for row in aggregate["seven_decision_outputs"]
            )
        )
        self.assertTrue(
            adapter.freeze_path(case.case_revision_id, aggregate["portfolio_plan_id"]).is_file()
        )
        self.assertTrue(
            aggregate["frozen_decision_and_audit_plan"]["frozen_before_audit_outcomes"]
        )
        revised = next(row for row in aggregate["package_revisions"] if row["correction_ids"])
        self.assertNotEqual(
            revised["base_deep_record_sha256"], revised["current_deep_record_sha256"]
        )
        self.assertIn(
            aggregate["audit_corrections"][0]["prior_value"]["preparation_id"],
            json.dumps(aggregate["retained_inputs"]["deep_frame"], sort_keys=True),
        )

    def test_selection_and_persisted_replay_are_order_independent(self) -> None:
        case, deep_frame, bundle, adapter, aggregate = self.run_adapter()
        path = adapter.aggregate_path(case.case_revision_id, aggregate["portfolio_plan_id"])
        before = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        replay_bundle = copy.deepcopy(bundle)
        replay_bundle["audit_outcomes"].reverse()
        replay_bundle["council_reviews"].reverse()
        replay = adapter.audit_and_select(case, deep_frame, replay_bundle)
        self.assertEqual(aggregate, replay)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(before_mtime, path.stat().st_mtime_ns)
        drift = copy.deepcopy(bundle)
        drift["audit_outcomes"][0]["rationale"] = "changed under the same audit revision"
        with self.assertRaises(PortfolioAggregateConflictError):
            adapter.audit_and_select(case, deep_frame, drift)

    def test_decision_failure_escalates_and_quarantines_unaudited_strata(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, deep_frame, bundle = make_portfolio_fixture(root, revision="portfolio-escalation", include_correction=False)
        freeze = preview_audit_freeze(case, deep_frame, bundle["plan"])
        assignments = {row["assignment_id"]: row for row in freeze["audit_assignments"]}
        assignment_by_unit = {
            row["audit_unit_id"]: row for row in freeze["audit_assignments"]
        }
        escalation_stratum = next(
            row for row in freeze["audit_stratum_reports"]
            if len(row["population_unit_ids"]) >= 2
            and sum(
                assignment_by_unit[unit_id]["candidate_id"] is None
                for unit_id in row["population_unit_ids"]
            ) >= 2
            and any(
                assignment_by_unit[unit_id]["selection_status"] == "selected_for_audit"
                and assignment_by_unit[unit_id]["candidate_id"] is None
                for unit_id in row["population_unit_ids"]
            )
        )
        seed_assignment = next(
            assignment_by_unit[unit_id]
            for unit_id in escalation_stratum["population_unit_ids"]
            if assignment_by_unit[unit_id]["selection_status"] == "selected_for_audit"
            and assignment_by_unit[unit_id]["candidate_id"] is None
        )
        unaudited_after_failure = next(
            assignment_by_unit[unit_id]
            for unit_id in escalation_stratum["population_unit_ids"]
            if assignment_by_unit[unit_id]["assignment_id"] != seed_assignment["assignment_id"]
            and assignment_by_unit[unit_id]["candidate_id"] is None
        )
        bundle["audit_outcomes"] = [
            row for row in bundle["audit_outcomes"]
            if row["assignment_id"] != unaudited_after_failure["assignment_id"]
        ]
        outcome = next(
            row for row in bundle["audit_outcomes"]
            if row["assignment_id"] == seed_assignment["assignment_id"]
        )
        outcome.update(
            {
                "outcome": "contradict",
                "decision_effect": "blocked_unresolved",
                "ranking_revision_id": None,
            }
        )
        aggregate = V7PortfolioAdapter(root / "portfolio").audit_and_select(case, deep_frame, bundle)
        self.assertGreater(aggregate["reconciliation"]["escalation_quarantine_count"], 0)
        self.assertTrue(any(row["escalation_triggered"] for row in aggregate["audit_stratum_reports"]))
        self.assertTrue(aggregate["audit_report"]["quarantined_unaudited_unit_ids"])
        self.assertEqual(assignments[seed_assignment["assignment_id"]]["selection_status"], "selected_for_audit")

    def test_missing_capacity_audit_stays_explicit_and_prevents_selection(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, deep_frame, bundle = make_portfolio_fixture(root, revision="portfolio-missing", include_correction=False)
        freeze = preview_audit_freeze(case, deep_frame, bundle["plan"])
        second_candidate = sorted(row["candidate_id"] for row in deep_frame["deep_packages"])[1]
        missing_assignment = next(
            row for row in freeze["audit_assignments"]
            if row["candidate_id"] == second_candidate and row["mandatory_census"]
        )
        bundle["audit_outcomes"] = [
            row for row in bundle["audit_outcomes"]
            if row["assignment_id"] != missing_assignment["assignment_id"]
        ]
        aggregate = V7PortfolioAdapter(root / "portfolio").audit_and_select(case, deep_frame, bundle)
        self.assertFalse(aggregate["stage_gate_passed"])
        self.assertEqual(aggregate["aggregate_status"], "diagnostic_partial")
        self.assertEqual(aggregate["finalists"], [])
        self.assertEqual(aggregate["reserves"], [])
        self.assertIn(
            missing_assignment["assignment_id"], aggregate["audit_report"]["missing_assignment_ids"]
        )
        self.assertIn(
            "unaudited",
            {row["disposition"] for row in aggregate["portfolio_dispositions"]},
        )
        self.assertGreater(aggregate["reconciliation"]["N_interim"], 0)

    def test_author_and_council_reviewer_independence_fail_closed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, deep_frame, bundle = make_portfolio_fixture(root, revision="portfolio-independence")
        freeze = preview_audit_freeze(case, deep_frame, bundle["plan"])
        first = bundle["audit_outcomes"][0]
        assignment = next(
            row for row in freeze["audit_assignments"]
            if row["assignment_id"] == first["assignment_id"]
        )
        self_approval = copy.deepcopy(bundle)
        self_approval["audit_outcomes"][0]["auditor_id"] = bundle["plan"]["subject_author_ids"][assignment["subject_id"]][0]
        with self.assertRaisesRegex(PortfolioAggregateError, "self-approve"):
            V7PortfolioAdapter(root / "self-approval").audit_and_select(case, deep_frame, self_approval)

        nonindependent = copy.deepcopy(bundle)
        council_candidate = nonindependent["council_reviews"][0]["candidate_id"]
        candidate_auditor = next(
            row["auditor_id"]
            for row in nonindependent["audit_outcomes"]
            if next(
                assignment for assignment in freeze["audit_assignments"]
                if assignment["assignment_id"] == row["assignment_id"]
            )["candidate_id"] == council_candidate
        )
        nonindependent["council_reviews"][0]["issues"][0]["reviewer_id"] = candidate_auditor
        with self.assertRaisesRegex(PortfolioAggregateError, "not independent"):
            V7PortfolioAdapter(root / "council-independence").audit_and_select(
                case, deep_frame, nonindependent
            )

    def test_conflicts_and_benchmark_labels_cannot_enter_the_aggregate(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, deep_frame, bundle = make_portfolio_fixture(root, revision="portfolio-conflict")
        conflict = copy.deepcopy(bundle)
        conflict["council_reviews"][0]["issues"][0]["finding"] = "unresolved"
        with self.assertRaisesRegex(PortfolioAggregateError, "cannot be silently retained"):
            V7PortfolioAdapter(root / "conflict").audit_and_select(case, deep_frame, conflict)

        leaked = copy.deepcopy(bundle)
        leaked["plan"]["benchmark_label"] = "expected-finalist"
        with self.assertRaisesRegex(PortfolioAggregateError, "invalid field set|benchmark"):
            V7PortfolioAdapter(root / "leak").audit_and_select(case, deep_frame, leaked)

        leaked_value = copy.deepcopy(bundle)
        leaked_value["plan"]["audit_revision"] = "expected_outcome"
        with self.assertRaisesRegex(PortfolioAggregateError, "benchmark"):
            V7PortfolioAdapter(root / "value-leak").audit_and_select(
                case, deep_frame, leaked_value
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
