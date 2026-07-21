#!/usr/bin/env python3
"""Scoped schema-v7 audit, council, correction, and portfolio tests."""

from __future__ import annotations

import unittest

from v7_audit_portfolio import (
    AuditCorrectionAction,
    AuditDecisionEffect,
    AuditOutcome,
    AuditPortfolioError,
    AuditSelectionStatus,
    AuditStratum,
    CorrectionAuthorityField,
    CouncilDisposition,
    CouncilFinding,
    CouncilIssueKind,
    DecisionImpact,
    PortfolioCandidateFrame,
    PortfolioDisposition,
    PortfolioSelectionStatus,
    apply_audit_corrections,
    build_audit_candidate_frames,
    build_audit_plan,
    derive_mechanism_clusters,
    derive_scaffold_clusters,
    make_audit_correction,
    make_audit_record,
    make_audit_sampling_policy,
    make_council_assessment,
    make_council_issue,
    make_council_record,
    make_diversity_features,
    make_portfolio_policy,
    make_scaffold_descriptor,
    select_diversified_portfolio,
)
from v7_triage_ranking import (
    RankingPreparationRecord,
    ResearchPriorityTier,
    TherapeuticConfidenceTier,
    TriageCategory,
)


def _preparation(
    candidate_id: str,
    *,
    therapeutic_tier: TherapeuticConfidenceTier = TherapeuticConfidenceTier.MODERATE,
    therapeutic_rank: int = 1,
    research_tier: ResearchPriorityTier = ResearchPriorityTier.MODERATE,
    research_rank: int = 1,
    support: str = "moderate",
    novelty: str = "emerging",
    uncertainty: str = "moderate",
    triage: TriageCategory = TriageCategory.DEEP_REVIEW,
) -> RankingPreparationRecord:
    return RankingPreparationRecord(
        preparation_id=f"PREP-{candidate_id}",
        candidate_id=candidate_id,
        profile_id=f"PROFILE-{candidate_id}",
        primary_endpoint_id="EP-1",
        triage_category=triage,
        therapeutic_confidence_tier=therapeutic_tier,
        therapeutic_rank_within_tier=therapeutic_rank,
        research_priority_tier=research_tier,
        research_rank_within_tier=research_rank,
        therapeutic_ordering_bands=(
            support,
            "moderate",
            "coherent",
            "supportive_observational",
            "single_context",
            "direct_primary",
            uncertainty,
        ),
        research_ordering_bands=(
            "moderate",
            novelty,
            "coherent",
            support,
            uncertainty,
        ),
        deterministic_tie_breaker=candidate_id,
        ordering_rule_version="schema-v7-separate-pre-audit-orders-v1",
    )


def _selected_assignment(candidate_id: str = "C-AUDIT"):
    frames = build_audit_candidate_frames(
        (_preparation(candidate_id),), provisional_finalist_ids=(candidate_id,)
    )
    policy = make_audit_sampling_policy(
        seed="audit-seed", novel_sample_size=0, uncertain_sample_size=0, tail_sample_size=0
    )
    return build_audit_plan(frames, policy).assignments[0]


def _support_audit(assignment, *, outcome=AuditOutcome.SUPPORT, effect=AuditDecisionEffect.NO_CHANGE, ranking_revision_id=None, corrections=()):
    return make_audit_record(
        assignment,
        outcome=outcome,
        decision_effect=effect,
        audited_subject_ids=(f"CLAIM-{assignment.candidate_id}",),
        corrections=corrections,
        checked_source_ids=(f"SRC-{assignment.candidate_id}",),
        checked_evidence_span_ids=(f"SPAN-{assignment.candidate_id}",),
        independent_search_receipt_ids=(f"SEARCH-{assignment.candidate_id}",),
        claim_author_ids=("claim-author",),
        auditor_id="independent-auditor",
        rationale="Independent original-content verification and counterevidence search completed.",
        ranking_revision_id=ranking_revision_id,
    )


def _diversity(
    candidate_id: str,
    *,
    target: str,
    mechanism: str,
    route: str,
    scaffold: str | None,
    modality: str,
    endpoint: str,
    development: str,
    uncertainty: str,
):
    descriptor = make_scaffold_descriptor(
        scaffold_key=scaffold,
        method="bemis_murcko",
        version="1",
        identity_record_ids=(f"IDENTITY-{candidate_id}",),
    )
    return make_diversity_features(
        candidate_id=candidate_id,
        target_ids=(target,) if target else (),
        mechanism_ids=(mechanism,) if mechanism else (),
        causal_route_ids=(route,),
        scaffold=descriptor,
        evidence_modalities=(modality,),
        endpoint_ids=(endpoint,),
        development_statuses=(development,),
        uncertainty_bands=(uncertainty,),
    )


class AuditSamplingTests(unittest.TestCase):
    def test_stratified_sampling_is_deterministic_and_explicitly_retains_unaudited(self) -> None:
        preparations = (
            _preparation("C1", therapeutic_rank=1),
            _preparation("C2", therapeutic_rank=2),
            _preparation("C3", support="conflicting", uncertainty="decision_blocking"),
            _preparation("C4", novelty="novel_hypothesis", research_rank=1),
            _preparation("C5", novelty="underexplored", research_rank=2),
            _preparation("C6", uncertainty="high"),
            _preparation("C7"),
            _preparation("C8"),
        )
        frames = build_audit_candidate_frames(
            preparations, provisional_finalist_ids=("C1", "C2")
        )
        policy = make_audit_sampling_policy(
            seed="case-42",
            novel_sample_size=1,
            uncertain_sample_size=1,
            tail_sample_size=1,
        )
        first = build_audit_plan(frames, policy)
        second = build_audit_plan(reversed(frames), policy)
        self.assertEqual(first, second)
        assignments = {row.candidate_id: row for row in first.assignments}
        self.assertIn(AuditStratum.FINALIST_CENSUS, assignments["C1"].strata)
        self.assertIn(AuditStratum.FINALIST_CENSUS, assignments["C2"].strata)
        self.assertIn(AuditStratum.MATERIAL_CONFLICT_CENSUS, assignments["C3"].strata)
        self.assertEqual(
            sum(AuditStratum.NOVEL_UNDEREXPLORED_SAMPLE in row.strata for row in first.assignments),
            1,
        )
        self.assertEqual(
            sum(AuditStratum.UNCERTAINTY_SAMPLE in row.strata for row in first.assignments),
            1,
        )
        self.assertEqual(
            sum(AuditStratum.SEEDED_TAIL_SAMPLE in row.strata for row in first.assignments),
            1,
        )
        unaudited = [
            row for row in first.assignments if row.selection_status is AuditSelectionStatus.UNAUDITED
        ]
        self.assertTrue(unaudited)
        self.assertTrue(all(row.strata == (AuditStratum.UNAUDITED,) for row in unaudited))

    def test_every_supported_audit_outcome_is_typed_and_self_approval_is_refused(self) -> None:
        assignment = _selected_assignment()
        outcome_effects = {
            AuditOutcome.SUPPORT: AuditDecisionEffect.NO_CHANGE,
            AuditOutcome.QUALIFY: AuditDecisionEffect.QUALIFIED,
            AuditOutcome.CONTRADICT: AuditDecisionEffect.BLOCKED_UNRESOLVED,
            AuditOutcome.UNRESOLVED: AuditDecisionEffect.BLOCKED_UNRESOLVED,
        }
        for outcome, effect in outcome_effects.items():
            with self.subTest(outcome=outcome):
                record = _support_audit(assignment, outcome=outcome, effect=effect)
                self.assertEqual(record.outcome, outcome)

        action_cases = {
            AuditOutcome.CORRECT: (AuditCorrectionAction.CORRECT, AuditDecisionEffect.RERANKED),
            AuditOutcome.SUPERSEDE: (AuditCorrectionAction.SUPERSEDE, AuditDecisionEffect.RERANKED),
            AuditOutcome.QUARANTINE: (AuditCorrectionAction.QUARANTINE, AuditDecisionEffect.QUARANTINED),
            AuditOutcome.REJECT: (AuditCorrectionAction.REJECT, AuditDecisionEffect.REJECTED),
        }
        for outcome, (action, effect) in action_cases.items():
            with self.subTest(outcome=outcome):
                replacement = action in {AuditCorrectionAction.CORRECT, AuditCorrectionAction.SUPERSEDE}
                correction = make_audit_correction(
                    candidate_id=assignment.candidate_id,
                    assignment_id=assignment.assignment_id,
                    authority_field=CorrectionAuthorityField.CLAIM_STATEMENT,
                    target_record_id="CLAIM-v1",
                    action=action,
                    prior_value={"statement": "old"},
                    replacement_record_id="CLAIM-v2" if replacement else None,
                    replacement_value={"statement": "new"} if replacement else None,
                    provenance_source_ids=("SRC-ERRATUM",),
                    provenance_evidence_span_ids=("SPAN-ERRATUM",),
                    rationale="Audit located a decision-changing original-content correction.",
                )
                revision = "RANK-REV-2" if effect is AuditDecisionEffect.RERANKED else None
                record = _support_audit(
                    assignment,
                    outcome=outcome,
                    effect=effect,
                    ranking_revision_id=revision,
                    corrections=(correction,),
                )
                self.assertEqual(record.outcome, outcome)
        with self.assertRaisesRegex(AuditPortfolioError, "self-approve"):
            make_audit_record(
                assignment,
                outcome=AuditOutcome.SUPPORT,
                decision_effect=AuditDecisionEffect.NO_CHANGE,
                audited_subject_ids=("CLAIM-1",),
                checked_source_ids=("SRC-1",),
                checked_evidence_span_ids=("SPAN-1",),
                independent_search_receipt_ids=("SEARCH-1",),
                claim_author_ids=("same-agent",),
                auditor_id="same-agent",
                rationale="Invalid self approval.",
            )


class CorrectionAuthorityTests(unittest.TestCase):
    def test_correction_authority_covers_every_requested_decision_field(self) -> None:
        self.assertEqual(
            {row.value for row in CorrectionAuthorityField},
            {
                "chemical_identity",
                "active_moiety_mapping",
                "claim_statement",
                "direction",
                "human_relevance",
                "causal_path",
                "endpoint",
                "candidate_class",
                "exposure",
                "safety",
                "ranking_feature",
            },
        )

    def test_correction_and_supersession_preserve_original_and_full_chain(self) -> None:
        assignment = _selected_assignment("C-CORRECT")
        first = make_audit_correction(
            candidate_id="C-CORRECT",
            assignment_id=assignment.assignment_id,
            authority_field=CorrectionAuthorityField.CHEMICAL_IDENTITY,
            target_record_id="IDENTITY-v1",
            action=AuditCorrectionAction.CORRECT,
            prior_value={"inchikey": "OLD"},
            replacement_record_id="IDENTITY-v2",
            replacement_value={"inchikey": "CORRECTED"},
            provenance_source_ids=("SRC-1",),
            provenance_evidence_span_ids=("SPAN-1",),
            rationale="Authority erratum corrects the exact identity.",
        )
        second = make_audit_correction(
            candidate_id="C-CORRECT",
            assignment_id=assignment.assignment_id,
            authority_field=CorrectionAuthorityField.CHEMICAL_IDENTITY,
            target_record_id="IDENTITY-v2",
            action=AuditCorrectionAction.SUPERSEDE,
            prior_value={"inchikey": "CORRECTED"},
            replacement_record_id="IDENTITY-v3",
            replacement_value={"inchikey": "CURRENT"},
            parent_correction_id=first.correction_id,
            provenance_source_ids=("SRC-2",),
            provenance_evidence_span_ids=("SPAN-2",),
            rationale="A later authoritative release supersedes the corrected record.",
        )
        state = apply_audit_corrections(
            "C-CORRECT",
            {
                CorrectionAuthorityField.CHEMICAL_IDENTITY: (
                    "IDENTITY-v1",
                    {"inchikey": "OLD"},
                )
            },
            (second, first),
        )
        field = state.fields[0]
        self.assertEqual(field.original.record_id, "IDENTITY-v1")
        self.assertEqual(field.current.record_id, "IDENTITY-v3")
        self.assertEqual(field.correction_ids, (first.correction_id, second.correction_id))
        self.assertEqual(len(state.corrections), 2)

    def test_silent_destructive_replacement_is_refused(self) -> None:
        assignment = _selected_assignment("C-BAD")
        bad = make_audit_correction(
            candidate_id="C-BAD",
            assignment_id=assignment.assignment_id,
            authority_field=CorrectionAuthorityField.DIRECTION,
            target_record_id="DIRECTION-v1",
            action=AuditCorrectionAction.CORRECT,
            prior_value={"direction": "wrong-prior"},
            replacement_record_id="DIRECTION-v2",
            replacement_value={"direction": "supports"},
            provenance_source_ids=("SRC-1",),
            provenance_evidence_span_ids=("SPAN-1",),
            rationale="The prior value must match before applying.",
        )
        with self.assertRaisesRegex(AuditPortfolioError, "prior value"):
            apply_audit_corrections(
                "C-BAD",
                {CorrectionAuthorityField.DIRECTION: ("DIRECTION-v1", {"direction": "opposes"})},
                (bad,),
            )


class CouncilTests(unittest.TestCase):
    def test_typed_council_review_deduplicates_evidence_ancestry_not_agents(self) -> None:
        assignment = _selected_assignment("C-COUNCIL")
        audit = _support_audit(assignment)
        issue_one = make_council_issue(
            candidate_id="C-COUNCIL",
            issue_kind=CouncilIssueKind.CLAIM_DIRECTION,
            decision_impact=DecisionImpact.ORDERING,
            subject_ids=("CLAIM-1",),
            audit_record_ids=(audit.audit_record_id,),
            source_ids=("SRC-1",),
            evidence_span_ids=("SPAN-1",),
            evidence_ancestry_cluster_ids=("ANCESTRY-SHARED",),
            rationale="Direction can change ordering.",
        )
        issue_two = make_council_issue(
            candidate_id="C-COUNCIL",
            issue_kind=CouncilIssueKind.ENDPOINT,
            decision_impact=DecisionImpact.ELIGIBILITY,
            subject_ids=("ENDPOINT-1",),
            audit_record_ids=(audit.audit_record_id,),
            source_ids=("SRC-2",),
            evidence_span_ids=("SPAN-2",),
            evidence_ancestry_cluster_ids=("ANCESTRY-SHARED",),
            rationale="Endpoint applicability can change eligibility.",
        )
        assessments = (
            make_council_assessment(
                issue_one,
                finding=CouncilFinding.CONFIRMED,
                evidence_ancestry_cluster_ids=("ANCESTRY-SHARED",),
                reviewer_id="reviewer-a",
                rationale="Grounded direction confirmed.",
            ),
            make_council_assessment(
                issue_two,
                finding=CouncilFinding.CONFIRMED,
                evidence_ancestry_cluster_ids=("ANCESTRY-SHARED",),
                reviewer_id="reviewer-b",
                rationale="Grounded endpoint applicability confirmed.",
            ),
        )
        record = make_council_record(
            (issue_one, issue_two),
            assessments,
            disposition=CouncilDisposition.RETAIN,
            rationale="Both decision-changing issues are confirmed.",
        )
        self.assertEqual(record.independent_evidence_cluster_ids, ("ANCESTRY-SHARED",))
        self.assertEqual(len(record.typed_findings), 2)

    def test_unresolved_council_conflict_cannot_be_silently_retained(self) -> None:
        assignment = _selected_assignment("C-UNRESOLVED")
        audit = _support_audit(
            assignment,
            outcome=AuditOutcome.UNRESOLVED,
            effect=AuditDecisionEffect.BLOCKED_UNRESOLVED,
        )
        issue = make_council_issue(
            candidate_id="C-UNRESOLVED",
            issue_kind=CouncilIssueKind.UNRESOLVED_CONFLICT,
            decision_impact=DecisionImpact.ELIGIBILITY,
            subject_ids=("CLAIM-CONFLICT",),
            audit_record_ids=(audit.audit_record_id,),
            source_ids=("SRC-CONFLICT",),
            evidence_span_ids=("SPAN-CONFLICT",),
            evidence_ancestry_cluster_ids=("ANCESTRY-1",),
            rationale="The material conflict remains decision-changing.",
        )
        assessment = make_council_assessment(
            issue,
            finding=CouncilFinding.UNRESOLVED,
            evidence_ancestry_cluster_ids=("ANCESTRY-1",),
            reviewer_id="reviewer",
            rationale="Independent evidence does not resolve the conflict.",
        )
        with self.assertRaisesRegex(AuditPortfolioError, "silently retained"):
            make_council_record(
                (issue,),
                (assessment,),
                disposition=CouncilDisposition.RETAIN,
                rationale="Invalid retention.",
            )


class ClusteringAndPortfolioTests(unittest.TestCase):
    def test_mechanism_clustering_is_transitive_and_deterministic(self) -> None:
        features = (
            _diversity("A", target="T1", mechanism="", route="R1", scaffold="S1", modality="M1", endpoint="E1", development="D1", uncertainty="low"),
            _diversity("B", target="T1", mechanism="MECH1", route="R1", scaffold="S2", modality="M1", endpoint="E1", development="D1", uncertainty="low"),
            _diversity("C", target="", mechanism="MECH1", route="R2", scaffold="S3", modality="M2", endpoint="E2", development="D2", uncertainty="high"),
            _diversity("D", target="T2", mechanism="MECH2", route="R3", scaffold="S4", modality="M3", endpoint="E3", development="D3", uncertainty="moderate"),
        )
        clusters = derive_mechanism_clusters(reversed(features))
        members = {cluster.candidate_ids for cluster in clusters}
        self.assertIn(("A", "B", "C"), members)
        self.assertIn(("D",), members)

    def test_scaffold_clustering_groups_exact_keys_and_does_not_reward_missingness(self) -> None:
        features = (
            _diversity("A", target="T1", mechanism="M1", route="R1", scaffold="SAME", modality="M1", endpoint="E1", development="D1", uncertainty="low"),
            _diversity("B", target="T2", mechanism="M2", route="R2", scaffold="SAME", modality="M2", endpoint="E2", development="D2", uncertainty="high"),
            _diversity("C", target="T3", mechanism="M3", route="R3", scaffold=None, modality="M3", endpoint="E3", development="D3", uncertainty="moderate"),
            _diversity("D", target="T4", mechanism="M4", route="R4", scaffold=None, modality="M4", endpoint="E4", development="D4", uncertainty="moderate"),
        )
        clusters = derive_scaffold_clusters(features)
        members = {cluster.candidate_ids for cluster in clusters}
        self.assertEqual(members, {("A", "B"), ("C", "D")})

    def test_portfolio_emits_three_ranks_and_selects_diverse_members(self) -> None:
        preparations = {
            "A": _preparation(
                "A",
                therapeutic_tier=TherapeuticConfidenceTier.HIGH,
                therapeutic_rank=1,
                research_tier=ResearchPriorityTier.LOW,
                research_rank=1,
            ),
            "B": _preparation(
                "B",
                therapeutic_tier=TherapeuticConfidenceTier.HIGH,
                therapeutic_rank=2,
                research_tier=ResearchPriorityTier.LOW,
                research_rank=2,
            ),
            "C": _preparation(
                "C",
                therapeutic_tier=TherapeuticConfidenceTier.MODERATE,
                therapeutic_rank=1,
                research_tier=ResearchPriorityTier.HIGH,
                research_rank=1,
                novelty="novel_hypothesis",
                uncertainty="high",
            ),
        }
        diversity = {
            "A": _diversity("A", target="T1", mechanism="MECH1", route="R1", scaffold="S1", modality="clinical", endpoint="E1", development="marketed", uncertainty="low"),
            "B": _diversity("B", target="T1", mechanism="MECH1", route="R1", scaffold="S1", modality="clinical", endpoint="E1", development="marketed", uncertainty="low"),
            "C": _diversity("C", target="T2", mechanism="MECH2", route="R2", scaffold="S2", modality="omics", endpoint="E2", development="preclinical", uncertainty="high"),
        }
        frames = build_audit_candidate_frames(
            preparations.values(), provisional_finalist_ids=("A", "B", "C")
        )
        audit_plan = build_audit_plan(
            frames,
            make_audit_sampling_policy(
                seed="portfolio", novel_sample_size=0, uncertain_sample_size=0, tail_sample_size=0
            ),
        )
        assignments = {row.candidate_id: row for row in audit_plan.assignments}
        portfolio_frames = tuple(
            PortfolioCandidateFrame(
                candidate_id=candidate_id,
                preparation=preparations[candidate_id],
                diversity=diversity[candidate_id],
                audit_assignment=assignments[candidate_id],
                audit_record=_support_audit(assignments[candidate_id]),
                council_record=None,
                ranking_revision_id=None,
            )
            for candidate_id in ("A", "B", "C")
        )
        selection = select_diversified_portfolio(
            portfolio_frames,
            make_portfolio_policy(
                finalist_capacity=2,
                reserve_capacity=1,
                evidence_weight=2,
                information_weight=0,
                diversity_weight=10,
            ),
        )
        self.assertEqual(selection.status, PortfolioSelectionStatus.COMPLETE)
        self.assertEqual(selection.finalist_ids, ("A", "C"))
        self.assertEqual(selection.reserve_ids, ("B",))
        records = {row.candidate_id: row for row in selection.records}
        self.assertEqual(records["A"].evidence_strength_rank, 1)
        self.assertEqual(records["C"].novelty_information_value_rank, 1)
        self.assertEqual(records["A"].diversified_portfolio_rank, 1)
        self.assertEqual(records["C"].diversified_portfolio_rank, 2)
        self.assertGreater(records["C"].diversity_component, records["B"].diversity_component)

    def test_unaudited_capacity_candidate_forces_explicit_escalation(self) -> None:
        preparations = {
            "A": _preparation("A", therapeutic_tier=TherapeuticConfidenceTier.HIGH),
            "B": _preparation("B", therapeutic_tier=TherapeuticConfidenceTier.MODERATE),
        }
        audit_frames = build_audit_candidate_frames(
            preparations.values(), provisional_finalist_ids=("A",)
        )
        plan = build_audit_plan(
            audit_frames,
            make_audit_sampling_policy(
                seed="escalate", novel_sample_size=0, uncertain_sample_size=0, tail_sample_size=0
            ),
        )
        assignments = {row.candidate_id: row for row in plan.assignments}
        frames = (
            PortfolioCandidateFrame(
                candidate_id="A",
                preparation=preparations["A"],
                diversity=_diversity("A", target="T1", mechanism="M1", route="R1", scaffold="S1", modality="clinical", endpoint="E1", development="marketed", uncertainty="low"),
                audit_assignment=assignments["A"],
                audit_record=_support_audit(assignments["A"]),
                council_record=None,
                ranking_revision_id=None,
            ),
            PortfolioCandidateFrame(
                candidate_id="B",
                preparation=preparations["B"],
                diversity=_diversity("B", target="T2", mechanism="M2", route="R2", scaffold="S2", modality="omics", endpoint="E2", development="preclinical", uncertainty="high"),
                audit_assignment=assignments["B"],
                audit_record=None,
                council_record=None,
                ranking_revision_id=None,
            ),
        )
        selection = select_diversified_portfolio(
            frames,
            make_portfolio_policy(
                finalist_capacity=2,
                reserve_capacity=0,
                diversity_weight=10,
            ),
        )
        self.assertEqual(selection.status, PortfolioSelectionStatus.NEEDS_ADDITIONAL_AUDIT)
        self.assertEqual(selection.additional_audit_required_ids, ("B",))
        records = {row.candidate_id: row for row in selection.records}
        self.assertEqual(records["B"].disposition, PortfolioDisposition.UNAUDITED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
