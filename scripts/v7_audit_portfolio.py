#!/usr/bin/env python3
"""Deterministic schema-v7 audit, correction, council, and portfolio policy.

The module consumes frozen pre-audit ranking records.  It does not retrieve
evidence, mutate deep packages, persist runtime ledgers, or build final files.
Corrections are append-only overlays; the original record and every replacement
remain addressable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from v7_triage_ranking import (
    RankingPreparationRecord,
    ResearchPriorityTier,
    TherapeuticConfidenceTier,
    TriageCategory,
)


SCHEMA_VERSION = 7
AUDIT_PORTFOLIO_MODEL_VERSION = "schema-v7-audit-council-portfolio-v1"


class AuditPortfolioError(ValueError):
    """Raised when audit or portfolio state is incomplete or inconsistent."""


class AuditStratum(str, Enum):
    FINALIST_CENSUS = "finalist_census"
    MATERIAL_CONFLICT_CENSUS = "material_conflict_census"
    NOVEL_UNDEREXPLORED_SAMPLE = "novel_underexplored_sample"
    UNCERTAINTY_SAMPLE = "uncertainty_sample"
    SEEDED_TAIL_SAMPLE = "seeded_tail_sample"
    UNAUDITED = "unaudited"


class AuditSelectionStatus(str, Enum):
    SELECTED = "selected_for_audit"
    UNAUDITED = "unaudited"


class AuditOutcome(str, Enum):
    SUPPORT = "support"
    QUALIFY = "qualify"
    CONTRADICT = "contradict"
    UNRESOLVED = "unresolved"
    CORRECT = "correct"
    SUPERSEDE = "supersede"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class AuditDecisionEffect(str, Enum):
    NO_CHANGE = "no_change"
    QUALIFIED = "qualified"
    RERANKED = "reranked"
    BLOCKED_UNRESOLVED = "blocked_unresolved"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"


class CorrectionAuthorityField(str, Enum):
    CHEMICAL_IDENTITY = "chemical_identity"
    ACTIVE_MOIETY_MAPPING = "active_moiety_mapping"
    CLAIM_STATEMENT = "claim_statement"
    DIRECTION = "direction"
    HUMAN_RELEVANCE = "human_relevance"
    CAUSAL_PATH = "causal_path"
    ENDPOINT = "endpoint"
    CANDIDATE_CLASS = "candidate_class"
    EXPOSURE = "exposure"
    SAFETY = "safety"
    RANKING_FEATURE = "ranking_feature"


class AuditCorrectionAction(str, Enum):
    CORRECT = "correct"
    SUPERSEDE = "supersede"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class DecisionImpact(str, Enum):
    ELIGIBILITY = "eligibility"
    ORDERING = "ordering"
    LANE = "lane"
    CAPACITY_CUTOFF = "capacity_cutoff"
    TIE_OUTCOME = "tie_outcome"
    SAFETY_OR_EXPOSURE_BLOCK = "safety_or_exposure_block"


class CouncilIssueKind(str, Enum):
    CHEMICAL_IDENTITY = "chemical_identity"
    ACTIVE_MOIETY_MAPPING = "active_moiety_mapping"
    CLAIM_DIRECTION = "claim_direction"
    HUMAN_RELEVANCE = "human_relevance"
    CAUSAL_PATH = "causal_path"
    ENDPOINT = "endpoint"
    CANDIDATE_CLASS = "candidate_class"
    EXPOSURE = "exposure"
    SAFETY = "safety"
    RANKING_FEATURE = "ranking_feature"
    UNRESOLVED_CONFLICT = "unresolved_conflict"
    PORTFOLIO_CUTOFF = "portfolio_cutoff"
    DIVERSITY_TRADEOFF = "diversity_tradeoff"


class CouncilFinding(str, Enum):
    CONFIRMED = "confirmed"
    QUALIFIED = "qualified"
    CORRECTION_REQUIRED = "correction_required"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class CouncilDisposition(str, Enum):
    RETAIN = "retain"
    QUALIFIED = "qualified"
    DEPRIORITIZED = "deprioritized"
    CONFLICT_UNRESOLVED = "conflict_unresolved"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    BASELINE_ONLY = "baseline_only"
    BENCHMARK_ONLY = "benchmark_only"


class DiversityDimension(str, Enum):
    TARGET_MECHANISM = "target_mechanism"
    CAUSAL_ROUTE = "causal_route"
    CHEMICAL_SCAFFOLD = "chemical_scaffold"
    EVIDENCE_MODALITY = "evidence_modality"
    ENDPOINT = "endpoint"
    DEVELOPMENT_STATUS = "development_status"
    UNCERTAINTY = "uncertainty"


class PortfolioDisposition(str, Enum):
    FINALIST = "finalist"
    RESERVE = "reserve"
    NOT_SELECTED = "not_selected"
    UNAUDITED = "unaudited"
    AUDIT_REJECTED = "audit_rejected"
    AUDIT_QUARANTINED = "audit_quarantined"
    COUNCIL_BLOCKED = "council_blocked"
    SELECTION_PENDING = "selection_pending_additional_audit"


class PortfolioSelectionStatus(str, Enum):
    COMPLETE = "complete"
    NEEDS_ADDITIONAL_AUDIT = "needs_additional_audit"


@dataclass(frozen=True)
class AuditSamplingPolicy:
    policy_id: str
    seed: str
    novel_sample_size: int
    uncertain_sample_size: int
    tail_sample_size: int
    novel_bands: tuple[str, ...]
    uncertain_bands: tuple[str, ...]
    sampling_rule_version: str


@dataclass(frozen=True)
class AuditCandidateFrame:
    candidate_id: str
    preparation_id: str
    provisional_finalist: bool
    material_conflict: bool
    novelty_band: str
    uncertainty_band: str
    therapeutic_tier: TherapeuticConfidenceTier
    therapeutic_rank_within_tier: int
    research_tier: ResearchPriorityTier
    research_rank_within_tier: int


@dataclass(frozen=True)
class AuditAssignment:
    assignment_id: str
    policy_id: str
    candidate_id: str
    selection_status: AuditSelectionStatus
    strata: tuple[AuditStratum, ...]
    sample_key: str
    reason: str


@dataclass(frozen=True)
class AuditStratumReport:
    stratum: AuditStratum
    population_candidate_ids: tuple[str, ...]
    population_denominator: int
    mandatory_census_count: int
    planned_sample_count: int
    selected_candidate_ids: tuple[str, ...]
    unaudited_candidate_ids: tuple[str, ...]
    deterministic_sampling_rule: str


@dataclass(frozen=True)
class AuditPlan:
    plan_id: str
    model_version: str
    policy: AuditSamplingPolicy
    frozen_candidate_ids: tuple[str, ...]
    assignments: tuple[AuditAssignment, ...]
    stratum_reports: tuple[AuditStratumReport, ...]


@dataclass(frozen=True)
class AuditCorrection:
    correction_id: str
    candidate_id: str
    assignment_id: str
    authority_field: CorrectionAuthorityField
    target_record_id: str
    action: AuditCorrectionAction
    prior_value_json: str
    prior_value_sha256: str
    replacement_record_id: str | None
    replacement_value_json: str | None
    replacement_value_sha256: str | None
    parent_correction_id: str | None
    provenance_source_ids: tuple[str, ...]
    provenance_evidence_span_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class AuditRecord:
    audit_record_id: str
    assignment_id: str
    candidate_id: str
    audited_subject_ids: tuple[str, ...]
    outcome: AuditOutcome
    decision_effect: AuditDecisionEffect
    correction_ids: tuple[str, ...]
    checked_source_ids: tuple[str, ...]
    checked_evidence_span_ids: tuple[str, ...]
    independent_search_receipt_ids: tuple[str, ...]
    claim_author_ids: tuple[str, ...]
    auditor_id: str
    rationale: str
    ranking_revision_id: str | None


@dataclass(frozen=True)
class RecordSnapshot:
    record_id: str
    value_json: str
    value_sha256: str


@dataclass(frozen=True)
class FieldCorrectionState:
    authority_field: CorrectionAuthorityField
    original: RecordSnapshot
    current: RecordSnapshot | None
    correction_ids: tuple[str, ...]
    terminal_action: AuditCorrectionAction | None


@dataclass(frozen=True)
class CorrectedCandidateState:
    state_id: str
    candidate_id: str
    fields: tuple[FieldCorrectionState, ...]
    corrections: tuple[AuditCorrection, ...]


@dataclass(frozen=True)
class CouncilIssue:
    issue_id: str
    candidate_id: str
    issue_kind: CouncilIssueKind
    decision_impact: DecisionImpact
    subject_ids: tuple[str, ...]
    audit_record_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    evidence_span_ids: tuple[str, ...]
    evidence_ancestry_cluster_ids: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class CouncilAssessment:
    assessment_id: str
    candidate_id: str
    issue_id: str
    issue_kind: CouncilIssueKind
    finding: CouncilFinding
    correction_ids: tuple[str, ...]
    evidence_ancestry_cluster_ids: tuple[str, ...]
    reviewer_id: str
    rationale: str


@dataclass(frozen=True)
class CouncilRecord:
    council_record_id: str
    candidate_id: str
    issue_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    typed_findings: tuple[tuple[CouncilIssueKind, CouncilFinding], ...]
    independent_evidence_cluster_ids: tuple[str, ...]
    correction_ids: tuple[str, ...]
    disposition: CouncilDisposition
    rationale: str


@dataclass(frozen=True)
class ScaffoldDescriptor:
    scaffold_key: str | None
    method: str
    version: str
    identity_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateDiversityFeatures:
    candidate_id: str
    target_ids: tuple[str, ...]
    mechanism_ids: tuple[str, ...]
    causal_route_ids: tuple[str, ...]
    scaffold: ScaffoldDescriptor | None
    evidence_modalities: tuple[str, ...]
    endpoint_ids: tuple[str, ...]
    development_statuses: tuple[str, ...]
    uncertainty_bands: tuple[str, ...]


@dataclass(frozen=True)
class MechanismCluster:
    cluster_id: str
    candidate_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    mechanism_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScaffoldCluster:
    cluster_id: str
    candidate_ids: tuple[str, ...]
    scaffold_key: str | None
    method: str
    version: str


@dataclass(frozen=True)
class PortfolioCandidateFrame:
    candidate_id: str
    preparation: RankingPreparationRecord
    diversity: CandidateDiversityFeatures
    audit_assignment: AuditAssignment
    audit_record: AuditRecord | None
    council_record: CouncilRecord | None
    ranking_revision_id: str | None


@dataclass(frozen=True)
class PortfolioPolicy:
    policy_id: str
    finalist_capacity: int
    reserve_capacity: int
    evidence_weight: int
    information_weight: int
    diversity_weight: int
    diversity_dimension_weights: tuple[tuple[DiversityDimension, int], ...]
    allowed_therapeutic_tiers: tuple[TherapeuticConfidenceTier, ...]
    selection_rule_version: str


@dataclass(frozen=True)
class DiversityContribution:
    dimension: DiversityDimension
    new_values: tuple[str, ...]
    weight: int


@dataclass(frozen=True)
class PortfolioRankRecord:
    candidate_id: str
    evidence_strength_rank: int
    novelty_information_value_rank: int
    diversified_portfolio_rank: int | None
    disposition: PortfolioDisposition
    evidence_component: int
    novelty_information_component: int
    diversity_component: int
    total_selection_utility: int
    diversity_contributions: tuple[DiversityContribution, ...]
    audit_status: AuditSelectionStatus
    audit_outcome: AuditOutcome | None
    council_disposition: CouncilDisposition | None
    reason: str


@dataclass(frozen=True)
class PortfolioSelection:
    selection_id: str
    model_version: str
    policy: PortfolioPolicy
    status: PortfolioSelectionStatus
    records: tuple[PortfolioRankRecord, ...]
    mechanism_clusters: tuple[MechanismCluster, ...]
    scaffold_clusters: tuple[ScaffoldCluster, ...]
    finalist_ids: tuple[str, ...]
    reserve_ids: tuple[str, ...]
    additional_audit_required_ids: tuple[str, ...]


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(value)[:24]}"


def _text(value: Any, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise AuditPortfolioError(f"{label} must be nonempty")
    return text


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, label) for value in values}))
    if required and not result:
        raise AuditPortfolioError(f"{label} must be nonempty")
    return result


def make_audit_sampling_policy(
    *,
    seed: str,
    novel_sample_size: int,
    uncertain_sample_size: int,
    tail_sample_size: int,
    novel_bands: Iterable[str] = ("novel_hypothesis", "underexplored"),
    uncertain_bands: Iterable[str] = ("high", "decision_blocking"),
) -> AuditSamplingPolicy:
    for value, label in (
        (novel_sample_size, "novel_sample_size"),
        (uncertain_sample_size, "uncertain_sample_size"),
        (tail_sample_size, "tail_sample_size"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AuditPortfolioError(f"{label} must be a nonnegative integer")
    body = {
        "seed": _text(seed, "audit seed"),
        "novel_sample_size": novel_sample_size,
        "uncertain_sample_size": uncertain_sample_size,
        "tail_sample_size": tail_sample_size,
        "novel_bands": _strings(novel_bands, "novel band", required=True),
        "uncertain_bands": _strings(uncertain_bands, "uncertain band", required=True),
        "sampling_rule_version": "schema-v7-deterministic-stratified-audit-v1",
    }
    return AuditSamplingPolicy(policy_id=_stable_id("AUDIT-POLICY", body), **body)


def build_audit_candidate_frames(
    preparations: Iterable[RankingPreparationRecord],
    *,
    provisional_finalist_ids: Iterable[str],
) -> tuple[AuditCandidateFrame, ...]:
    rows = list(preparations)
    by_candidate = {row.candidate_id: row for row in rows}
    if len(by_candidate) != len(rows):
        raise AuditPortfolioError("ranking preparations must have unique candidate IDs")
    finalists = set(_strings(provisional_finalist_ids, "provisional finalist ID"))
    if not finalists.issubset(by_candidate):
        raise AuditPortfolioError("provisional finalist does not resolve to the frozen ranking frame")
    result: list[AuditCandidateFrame] = []
    for candidate_id, row in sorted(by_candidate.items()):
        if len(row.therapeutic_ordering_bands) != 7 or len(row.research_ordering_bands) != 5:
            raise AuditPortfolioError("ranking preparation does not expose the frozen v7 band projection")
        support = row.therapeutic_ordering_bands[0]
        uncertainty = row.therapeutic_ordering_bands[6]
        novelty = row.research_ordering_bands[1]
        result.append(
            AuditCandidateFrame(
                candidate_id=candidate_id,
                preparation_id=row.preparation_id,
                provisional_finalist=candidate_id in finalists,
                material_conflict=support == "conflicting" or uncertainty == "decision_blocking",
                novelty_band=novelty,
                uncertainty_band=uncertainty,
                therapeutic_tier=row.therapeutic_confidence_tier,
                therapeutic_rank_within_tier=row.therapeutic_rank_within_tier,
                research_tier=row.research_priority_tier,
                research_rank_within_tier=row.research_rank_within_tier,
            )
        )
    return tuple(result)


def _sample_key(seed: str, stratum: AuditStratum, candidate_id: str) -> str:
    return _sha256({"seed": seed, "stratum": stratum, "candidate_id": candidate_id})


def build_audit_plan(
    frames: Iterable[AuditCandidateFrame], policy: AuditSamplingPolicy
) -> AuditPlan:
    rows = list(frames)
    by_candidate = {row.candidate_id: row for row in rows}
    if len(by_candidate) != len(rows):
        raise AuditPortfolioError("audit frame contains duplicate candidates")
    if not rows:
        raise AuditPortfolioError("audit frame must not be empty")
    selected: dict[str, set[AuditStratum]] = {candidate_id: set() for candidate_id in by_candidate}
    finalists = {row.candidate_id for row in rows if row.provisional_finalist}
    conflicts = {row.candidate_id for row in rows if row.material_conflict}
    for candidate_id in finalists:
        selected[candidate_id].add(AuditStratum.FINALIST_CENSUS)
    for candidate_id in conflicts:
        selected[candidate_id].add(AuditStratum.MATERIAL_CONFLICT_CENSUS)

    mandatory = finalists | conflicts
    novel_population = {row.candidate_id for row in rows if row.novelty_band in policy.novel_bands}
    novel_available = [by_candidate[candidate_id] for candidate_id in novel_population - mandatory]
    novel_available.sort(
        key=lambda row: (
            _RESEARCH_ORDER[row.research_tier],
            row.research_rank_within_tier,
            row.candidate_id,
        )
    )
    novel_sample = {row.candidate_id for row in novel_available[: policy.novel_sample_size]}
    for candidate_id in novel_sample:
        selected[candidate_id].add(AuditStratum.NOVEL_UNDEREXPLORED_SAMPLE)

    uncertainty_population = {
        row.candidate_id for row in rows if row.uncertainty_band in policy.uncertain_bands
    }
    already_selected = {candidate_id for candidate_id, strata in selected.items() if strata}
    uncertainty_available = [
        by_candidate[candidate_id]
        for candidate_id in uncertainty_population - already_selected
    ]
    uncertainty_available.sort(
        key=lambda row: (
            0 if row.uncertainty_band == "decision_blocking" else 1,
            _THERAPEUTIC_ORDER[row.therapeutic_tier],
            row.therapeutic_rank_within_tier,
            row.candidate_id,
        )
    )
    uncertainty_sample = {
        row.candidate_id for row in uncertainty_available[: policy.uncertain_sample_size]
    }
    for candidate_id in uncertainty_sample:
        selected[candidate_id].add(AuditStratum.UNCERTAINTY_SAMPLE)

    already_selected = {candidate_id for candidate_id, strata in selected.items() if strata}
    tail_population = set(by_candidate) - already_selected
    tail_order = sorted(
        tail_population,
        key=lambda candidate_id: (
            _sample_key(policy.seed, AuditStratum.SEEDED_TAIL_SAMPLE, candidate_id),
            candidate_id,
        ),
    )
    tail_sample = set(tail_order[: policy.tail_sample_size])
    for candidate_id in tail_sample:
        selected[candidate_id].add(AuditStratum.SEEDED_TAIL_SAMPLE)

    assignments: list[AuditAssignment] = []
    for candidate_id in sorted(by_candidate):
        strata = tuple(sorted(selected[candidate_id], key=lambda row: row.value))
        status = AuditSelectionStatus.SELECTED if strata else AuditSelectionStatus.UNAUDITED
        assignment_strata = strata if strata else (AuditStratum.UNAUDITED,)
        body = {
            "policy_id": policy.policy_id,
            "candidate_id": candidate_id,
            "selection_status": status,
            "strata": assignment_strata,
            "sample_key": _sample_key(policy.seed, assignment_strata[0], candidate_id),
            "reason": (
                "selected under frozen deterministic strata: "
                + ", ".join(row.value for row in assignment_strata)
                if strata
                else "not selected by any frozen census or sample stratum"
            ),
        }
        assignments.append(AuditAssignment(assignment_id=_stable_id("AUDIT-ASSIGN", body), **body))

    def report(
        stratum: AuditStratum,
        population: set[str],
        stratum_sample: set[str],
        rule: str,
    ) -> AuditStratumReport:
        covered = {candidate_id for candidate_id in population if selected[candidate_id]}
        return AuditStratumReport(
            stratum=stratum,
            population_candidate_ids=tuple(sorted(population)),
            population_denominator=len(population),
            mandatory_census_count=len(population & mandatory),
            planned_sample_count=len(stratum_sample),
            selected_candidate_ids=tuple(sorted(covered)),
            unaudited_candidate_ids=tuple(sorted(population - covered)),
            deterministic_sampling_rule=rule,
        )

    covered_all = {candidate_id for candidate_id, strata in selected.items() if strata}
    reports = (
        report(
            AuditStratum.FINALIST_CENSUS,
            finalists,
            finalists,
            "audit every frozen provisional finalist",
        ),
        report(
            AuditStratum.MATERIAL_CONFLICT_CENSUS,
            conflicts,
            conflicts,
            "audit every material conflict derived from frozen ranking bands",
        ),
        report(
            AuditStratum.NOVEL_UNDEREXPLORED_SAMPLE,
            novel_population,
            novel_sample,
            f"take first {policy.novel_sample_size} non-census records by frozen research tier/rank/candidate ID",
        ),
        report(
            AuditStratum.UNCERTAINTY_SAMPLE,
            uncertainty_population,
            uncertainty_sample,
            f"take first {policy.uncertain_sample_size} remaining uncertain records by severity/rank/candidate ID",
        ),
        report(
            AuditStratum.SEEDED_TAIL_SAMPLE,
            set(by_candidate) - (mandatory | novel_sample | uncertainty_sample),
            tail_sample,
            f"take {policy.tail_sample_size} by SHA-256(seed, stratum, candidate ID)",
        ),
        report(
            AuditStratum.UNAUDITED,
            set(by_candidate) - covered_all,
            set(),
            "retain an explicit unaudited assignment for every unsampled record",
        ),
    )
    body = {
        "model_version": AUDIT_PORTFOLIO_MODEL_VERSION,
        "policy": policy,
        "frozen_candidate_ids": tuple(sorted(by_candidate)),
        "assignments": tuple(assignments),
        "stratum_reports": reports,
    }
    return AuditPlan(plan_id=_stable_id("AUDIT-PLAN", body), **body)


def make_audit_correction(
    *,
    candidate_id: str,
    assignment_id: str,
    authority_field: CorrectionAuthorityField,
    target_record_id: str,
    action: AuditCorrectionAction,
    prior_value: Any,
    replacement_record_id: str | None = None,
    replacement_value: Any = None,
    parent_correction_id: str | None = None,
    provenance_source_ids: Iterable[str],
    provenance_evidence_span_ids: Iterable[str],
    rationale: str,
) -> AuditCorrection:
    prior_json = _canonical_json(prior_value)
    replacement_id = str(replacement_record_id).strip() if replacement_record_id is not None else None
    if action in {AuditCorrectionAction.CORRECT, AuditCorrectionAction.SUPERSEDE}:
        if not replacement_id or replacement_id == str(target_record_id):
            raise AuditPortfolioError("correction/supersession requires a distinct replacement record")
        replacement_json = _canonical_json(replacement_value)
        replacement_sha = _sha256(replacement_json.encode("utf-8"))
    else:
        if replacement_id is not None or replacement_value is not None:
            raise AuditPortfolioError("quarantine/reject cannot silently carry a replacement")
        replacement_json = None
        replacement_sha = None
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "assignment_id": _text(assignment_id, "assignment_id"),
        "authority_field": authority_field,
        "target_record_id": _text(target_record_id, "target_record_id"),
        "action": action,
        "prior_value_json": prior_json,
        "prior_value_sha256": _sha256(prior_json.encode("utf-8")),
        "replacement_record_id": replacement_id,
        "replacement_value_json": replacement_json,
        "replacement_value_sha256": replacement_sha,
        "parent_correction_id": str(parent_correction_id).strip() or None if parent_correction_id is not None else None,
        "provenance_source_ids": _strings(provenance_source_ids, "correction source ID", required=True),
        "provenance_evidence_span_ids": _strings(
            provenance_evidence_span_ids, "correction evidence span ID", required=True
        ),
        "rationale": _text(rationale, "correction rationale"),
    }
    return AuditCorrection(correction_id=_stable_id("AUDIT-CORRECTION", body), **body)


def make_audit_record(
    assignment: AuditAssignment,
    *,
    outcome: AuditOutcome,
    decision_effect: AuditDecisionEffect,
    audited_subject_ids: Iterable[str],
    corrections: Iterable[AuditCorrection] = (),
    checked_source_ids: Iterable[str],
    checked_evidence_span_ids: Iterable[str],
    independent_search_receipt_ids: Iterable[str],
    claim_author_ids: Iterable[str],
    auditor_id: str,
    rationale: str,
    ranking_revision_id: str | None = None,
) -> AuditRecord:
    if assignment.selection_status is not AuditSelectionStatus.SELECTED:
        raise AuditPortfolioError("an explicit unaudited assignment cannot receive an audit verdict")
    correction_rows = tuple(sorted(corrections, key=lambda row: row.correction_id))
    if any(
        row.candidate_id != assignment.candidate_id or row.assignment_id != assignment.assignment_id
        for row in correction_rows
    ):
        raise AuditPortfolioError("audit correction is outside its candidate assignment")
    authors = _strings(claim_author_ids, "claim author ID")
    auditor = _text(auditor_id, "auditor_id")
    if auditor in authors:
        raise AuditPortfolioError("the author of a claim cannot self-approve its scientific audit")
    allowed_effects = {
        AuditOutcome.SUPPORT: {AuditDecisionEffect.NO_CHANGE},
        AuditOutcome.QUALIFY: {AuditDecisionEffect.QUALIFIED, AuditDecisionEffect.RERANKED},
        AuditOutcome.CONTRADICT: {
            AuditDecisionEffect.RERANKED,
            AuditDecisionEffect.BLOCKED_UNRESOLVED,
            AuditDecisionEffect.QUARANTINED,
            AuditDecisionEffect.REJECTED,
        },
        AuditOutcome.UNRESOLVED: {
            AuditDecisionEffect.BLOCKED_UNRESOLVED,
            AuditDecisionEffect.QUARANTINED,
        },
        AuditOutcome.CORRECT: {AuditDecisionEffect.RERANKED},
        AuditOutcome.SUPERSEDE: {AuditDecisionEffect.RERANKED},
        AuditOutcome.QUARANTINE: {AuditDecisionEffect.QUARANTINED},
        AuditOutcome.REJECT: {AuditDecisionEffect.REJECTED},
    }
    if decision_effect not in allowed_effects[outcome]:
        raise AuditPortfolioError("audit outcome and decision effect are inconsistent")
    required_action = {
        AuditOutcome.CORRECT: AuditCorrectionAction.CORRECT,
        AuditOutcome.SUPERSEDE: AuditCorrectionAction.SUPERSEDE,
        AuditOutcome.QUARANTINE: AuditCorrectionAction.QUARANTINE,
        AuditOutcome.REJECT: AuditCorrectionAction.REJECT,
    }.get(outcome)
    if required_action is not None and not any(row.action is required_action for row in correction_rows):
        raise AuditPortfolioError(f"{outcome.value} audit outcome requires its append-only correction action")
    if outcome in {AuditOutcome.SUPPORT, AuditOutcome.QUALIFY, AuditOutcome.CONTRADICT, AuditOutcome.UNRESOLVED} and correction_rows:
        raise AuditPortfolioError("use a correction outcome when an append-only correction is emitted")
    ranking_revision = str(ranking_revision_id).strip() if ranking_revision_id is not None else None
    if (decision_effect is AuditDecisionEffect.RERANKED) != bool(ranking_revision):
        raise AuditPortfolioError("reranked audit effects require exactly one ranking revision ID")
    body = {
        "assignment_id": assignment.assignment_id,
        "candidate_id": assignment.candidate_id,
        "audited_subject_ids": _strings(audited_subject_ids, "audited subject ID", required=True),
        "outcome": outcome,
        "decision_effect": decision_effect,
        "correction_ids": tuple(row.correction_id for row in correction_rows),
        "checked_source_ids": _strings(checked_source_ids, "checked source ID", required=True),
        "checked_evidence_span_ids": _strings(
            checked_evidence_span_ids, "checked evidence span ID", required=True
        ),
        "independent_search_receipt_ids": _strings(
            independent_search_receipt_ids, "independent search receipt ID", required=True
        ),
        "claim_author_ids": authors,
        "auditor_id": auditor,
        "rationale": _text(rationale, "audit rationale"),
        "ranking_revision_id": ranking_revision,
    }
    return AuditRecord(audit_record_id=_stable_id("AUDIT", body), **body)


def _snapshot(record_id: str, value: Any) -> RecordSnapshot:
    value_json = _canonical_json(value)
    return RecordSnapshot(
        record_id=_text(record_id, "record_id"),
        value_json=value_json,
        value_sha256=_sha256(value_json.encode("utf-8")),
    )


def apply_audit_corrections(
    candidate_id: str,
    base_records: Mapping[CorrectionAuthorityField, tuple[str, Any]],
    corrections: Iterable[AuditCorrection],
) -> CorrectedCandidateState:
    """Build a current overlay without deleting or rewriting any prior record."""

    candidate = _text(candidate_id, "candidate_id")
    correction_rows = tuple(sorted(corrections, key=lambda row: row.correction_id))
    if any(row.candidate_id != candidate for row in correction_rows):
        raise AuditPortfolioError("correction ledger mixes candidates")
    grouped: dict[CorrectionAuthorityField, list[AuditCorrection]] = {}
    for row in correction_rows:
        grouped.setdefault(row.authority_field, []).append(row)
    if not set(grouped).issubset(base_records):
        raise AuditPortfolioError("correction authority field has no retained base record")

    states: list[FieldCorrectionState] = []
    for authority_field in sorted(base_records, key=lambda row: row.value):
        base_id, base_value = base_records[authority_field]
        original = _snapshot(base_id, base_value)
        current: RecordSnapshot | None = original
        terminal: AuditCorrectionAction | None = None
        used: list[str] = []
        rows = grouped.get(authority_field, [])
        by_target: dict[str, AuditCorrection] = {}
        for row in rows:
            if row.target_record_id in by_target:
                raise AuditPortfolioError("parallel destructive corrections target the same record")
            by_target[row.target_record_id] = row
        parent_id: str | None = None
        while current is not None and current.record_id in by_target:
            row = by_target[current.record_id]
            if row.parent_correction_id != parent_id:
                raise AuditPortfolioError("correction chain parent does not match retained history")
            if (
                row.prior_value_json != current.value_json
                or row.prior_value_sha256 != current.value_sha256
            ):
                raise AuditPortfolioError("correction prior value does not match the retained target")
            used.append(row.correction_id)
            parent_id = row.correction_id
            if row.action in {AuditCorrectionAction.CORRECT, AuditCorrectionAction.SUPERSEDE}:
                if row.replacement_record_id is None or row.replacement_value_json is None:
                    raise AuditPortfolioError("replacement correction is incomplete")
                current = RecordSnapshot(
                    record_id=row.replacement_record_id,
                    value_json=row.replacement_value_json,
                    value_sha256=str(row.replacement_value_sha256),
                )
            else:
                terminal = row.action
                current = None
        if set(used) != {row.correction_id for row in rows}:
            raise AuditPortfolioError("correction is not on the single append-only current chain")
        states.append(
            FieldCorrectionState(
                authority_field=authority_field,
                original=original,
                current=current,
                correction_ids=tuple(used),
                terminal_action=terminal,
            )
        )
    body = {
        "candidate_id": candidate,
        "fields": tuple(states),
        "corrections": correction_rows,
    }
    return CorrectedCandidateState(state_id=_stable_id("CORRECTED-CANDIDATE", body), **body)


def make_council_issue(
    *,
    candidate_id: str,
    issue_kind: CouncilIssueKind,
    decision_impact: DecisionImpact,
    subject_ids: Iterable[str],
    audit_record_ids: Iterable[str],
    source_ids: Iterable[str],
    evidence_span_ids: Iterable[str],
    evidence_ancestry_cluster_ids: Iterable[str],
    rationale: str,
) -> CouncilIssue:
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "issue_kind": issue_kind,
        "decision_impact": decision_impact,
        "subject_ids": _strings(subject_ids, "council subject ID", required=True),
        "audit_record_ids": _strings(audit_record_ids, "council audit record ID", required=True),
        "source_ids": _strings(source_ids, "council source ID"),
        "evidence_span_ids": _strings(evidence_span_ids, "council evidence span ID"),
        "evidence_ancestry_cluster_ids": _strings(
            evidence_ancestry_cluster_ids, "evidence ancestry cluster ID"
        ),
        "rationale": _text(rationale, "council issue rationale"),
    }
    return CouncilIssue(issue_id=_stable_id("COUNCIL-ISSUE", body), **body)


def make_council_assessment(
    issue: CouncilIssue,
    *,
    finding: CouncilFinding,
    correction_ids: Iterable[str] = (),
    evidence_ancestry_cluster_ids: Iterable[str] = (),
    reviewer_id: str,
    rationale: str,
) -> CouncilAssessment:
    corrections = _strings(correction_ids, "council correction ID")
    if finding is CouncilFinding.CORRECTION_REQUIRED and not corrections:
        raise AuditPortfolioError("typed correction-required assessment needs a correction ID")
    if finding is not CouncilFinding.CORRECTION_REQUIRED and corrections:
        raise AuditPortfolioError("council correction IDs require a correction-required finding")
    ancestry = _strings(evidence_ancestry_cluster_ids, "evidence ancestry cluster ID")
    if not set(ancestry).issubset(issue.evidence_ancestry_cluster_ids):
        raise AuditPortfolioError("council assessment cites ancestry outside the decision issue")
    body = {
        "candidate_id": issue.candidate_id,
        "issue_id": issue.issue_id,
        "issue_kind": issue.issue_kind,
        "finding": finding,
        "correction_ids": corrections,
        "evidence_ancestry_cluster_ids": ancestry,
        "reviewer_id": _text(reviewer_id, "council reviewer ID"),
        "rationale": _text(rationale, "council assessment rationale"),
    }
    return CouncilAssessment(assessment_id=_stable_id("COUNCIL-ASSESSMENT", body), **body)


def make_council_record(
    issues: Iterable[CouncilIssue],
    assessments: Iterable[CouncilAssessment],
    *,
    disposition: CouncilDisposition,
    rationale: str,
) -> CouncilRecord:
    issue_rows = tuple(sorted(issues, key=lambda row: row.issue_id))
    assessment_rows = tuple(sorted(assessments, key=lambda row: row.assessment_id))
    if not issue_rows:
        raise AuditPortfolioError("council review must focus on at least one decision-changing issue")
    candidates = {row.candidate_id for row in issue_rows} | {row.candidate_id for row in assessment_rows}
    if len(candidates) != 1:
        raise AuditPortfolioError("one council record cannot mix candidates")
    issue_by_id = {row.issue_id: row for row in issue_rows}
    if len(issue_by_id) != len(issue_rows):
        raise AuditPortfolioError("council issues must be unique")
    assessment_by_issue = {row.issue_id: row for row in assessment_rows}
    if len(assessment_by_issue) != len(assessment_rows) or set(assessment_by_issue) != set(issue_by_id):
        raise AuditPortfolioError("council must emit one typed assessment per decision issue")
    for issue_id, assessment in assessment_by_issue.items():
        if assessment.issue_kind is not issue_by_id[issue_id].issue_kind:
            raise AuditPortfolioError("council assessment type differs from its issue")
    findings = {row.finding for row in assessment_rows}
    if CouncilFinding.REJECT in findings and disposition is not CouncilDisposition.REJECTED:
        raise AuditPortfolioError("a reject finding requires rejected disposition")
    if (
        CouncilFinding.REJECT not in findings
        and CouncilFinding.QUARANTINE in findings
        and disposition is not CouncilDisposition.QUARANTINED
    ):
        raise AuditPortfolioError("a quarantine finding requires quarantined disposition")
    if (
        not findings & {CouncilFinding.REJECT, CouncilFinding.QUARANTINE}
        and findings & {CouncilFinding.UNRESOLVED, CouncilFinding.CONTRADICTED}
        and disposition not in {
        CouncilDisposition.CONFLICT_UNRESOLVED,
        CouncilDisposition.QUARANTINED,
        CouncilDisposition.REJECTED,
        }
    ):
        raise AuditPortfolioError("unresolved or contradicted council issue cannot be silently retained")
    if disposition in {CouncilDisposition.BASELINE_ONLY, CouncilDisposition.BENCHMARK_ONLY} and not any(
        row.issue_kind is CouncilIssueKind.CANDIDATE_CLASS for row in issue_rows
    ):
        raise AuditPortfolioError("lane-only disposition requires a typed candidate-class issue")
    ancestry = _strings(
        (
            cluster_id
            for row in assessment_rows
            for cluster_id in row.evidence_ancestry_cluster_ids
        ),
        "evidence ancestry cluster ID",
    )
    correction_ids = _strings(
        (correction_id for row in assessment_rows for correction_id in row.correction_ids),
        "council correction ID",
    )
    body = {
        "candidate_id": next(iter(candidates)),
        "issue_ids": tuple(row.issue_id for row in issue_rows),
        "assessment_ids": tuple(row.assessment_id for row in assessment_rows),
        "typed_findings": tuple(
            sorted(
                ((row.issue_kind, row.finding) for row in assessment_rows),
                key=lambda item: (item[0].value, item[1].value),
            )
        ),
        "independent_evidence_cluster_ids": ancestry,
        "correction_ids": correction_ids,
        "disposition": disposition,
        "rationale": _text(rationale, "council record rationale"),
    }
    return CouncilRecord(council_record_id=_stable_id("COUNCIL", body), **body)


def make_scaffold_descriptor(
    *,
    scaffold_key: str | None,
    method: str,
    version: str,
    identity_record_ids: Iterable[str],
) -> ScaffoldDescriptor:
    key = str(scaffold_key).strip() if scaffold_key is not None else None
    return ScaffoldDescriptor(
        scaffold_key=key or None,
        method=_text(method, "scaffold method"),
        version=_text(version, "scaffold version"),
        identity_record_ids=_strings(identity_record_ids, "scaffold identity record ID", required=True),
    )


def make_diversity_features(
    *,
    candidate_id: str,
    target_ids: Iterable[str],
    mechanism_ids: Iterable[str],
    causal_route_ids: Iterable[str],
    scaffold: ScaffoldDescriptor | None,
    evidence_modalities: Iterable[str],
    endpoint_ids: Iterable[str],
    development_statuses: Iterable[str],
    uncertainty_bands: Iterable[str],
) -> CandidateDiversityFeatures:
    return CandidateDiversityFeatures(
        candidate_id=_text(candidate_id, "candidate_id"),
        target_ids=_strings(target_ids, "target ID"),
        mechanism_ids=_strings(mechanism_ids, "mechanism ID"),
        causal_route_ids=_strings(causal_route_ids, "causal route ID", required=True),
        scaffold=scaffold,
        evidence_modalities=_strings(evidence_modalities, "evidence modality", required=True),
        endpoint_ids=_strings(endpoint_ids, "endpoint ID", required=True),
        development_statuses=_strings(development_statuses, "development status", required=True),
        uncertainty_bands=_strings(uncertainty_bands, "uncertainty band", required=True),
    )


def derive_mechanism_clusters(
    features: Iterable[CandidateDiversityFeatures],
) -> tuple[MechanismCluster, ...]:
    rows = list(features)
    by_candidate = {row.candidate_id: row for row in rows}
    if len(by_candidate) != len(rows):
        raise AuditPortfolioError("mechanism clustering received duplicate candidates")
    parent = {candidate_id: candidate_id for candidate_id in by_candidate}

    def find(candidate_id: str) -> str:
        while parent[candidate_id] != candidate_id:
            parent[candidate_id] = parent[parent[candidate_id]]
            candidate_id = parent[candidate_id]
        return candidate_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    token_owner: dict[str, str] = {}
    for candidate_id, row in sorted(by_candidate.items()):
        tokens = [*(f"target:{value}" for value in row.target_ids), *(f"mechanism:{value}" for value in row.mechanism_ids)]
        if not tokens:
            tokens = ["unknown:target_mechanism"]
        for token in tokens:
            if token in token_owner:
                union(candidate_id, token_owner[token])
            else:
                token_owner[token] = candidate_id
    groups: dict[str, list[CandidateDiversityFeatures]] = {}
    for candidate_id, row in by_candidate.items():
        groups.setdefault(find(candidate_id), []).append(row)
    result: list[MechanismCluster] = []
    for group in groups.values():
        candidate_ids = tuple(sorted(row.candidate_id for row in group))
        targets = _strings((value for row in group for value in row.target_ids), "target ID")
        mechanisms = _strings((value for row in group for value in row.mechanism_ids), "mechanism ID")
        body = {
            "candidate_ids": candidate_ids,
            "target_ids": targets,
            "mechanism_ids": mechanisms,
            "unknown": not targets and not mechanisms,
        }
        result.append(
            MechanismCluster(
                cluster_id=_stable_id("MECHANISM-CLUSTER", body),
                candidate_ids=candidate_ids,
                target_ids=targets,
                mechanism_ids=mechanisms,
            )
        )
    return tuple(sorted(result, key=lambda row: row.cluster_id))


def derive_scaffold_clusters(
    features: Iterable[CandidateDiversityFeatures],
) -> tuple[ScaffoldCluster, ...]:
    groups: dict[tuple[str | None, str, str], list[str]] = {}
    for row in features:
        descriptor = row.scaffold
        key = (
            (descriptor.scaffold_key, descriptor.method, descriptor.version)
            if descriptor is not None and descriptor.scaffold_key is not None
            else (None, "unknown", "unknown")
        )
        groups.setdefault(key, []).append(row.candidate_id)
    result: list[ScaffoldCluster] = []
    for (scaffold_key, method, version), candidate_ids in groups.items():
        body = {
            "scaffold_key": scaffold_key,
            "method": method,
            "version": version,
            "candidate_ids": tuple(sorted(candidate_ids)),
        }
        result.append(
            ScaffoldCluster(
                cluster_id=_stable_id("SCAFFOLD-CLUSTER", body),
                candidate_ids=tuple(sorted(candidate_ids)),
                scaffold_key=scaffold_key,
                method=method,
                version=version,
            )
        )
    return tuple(sorted(result, key=lambda row: row.cluster_id))


def make_portfolio_policy(
    *,
    finalist_capacity: int,
    reserve_capacity: int,
    evidence_weight: int = 5,
    information_weight: int = 2,
    diversity_weight: int = 3,
    diversity_dimension_weights: Mapping[DiversityDimension, int] | None = None,
    allowed_therapeutic_tiers: Iterable[TherapeuticConfidenceTier] = (
        TherapeuticConfidenceTier.HIGH,
        TherapeuticConfidenceTier.MODERATE,
        TherapeuticConfidenceTier.LOW,
    ),
) -> PortfolioPolicy:
    for value, label in (
        (finalist_capacity, "finalist_capacity"),
        (reserve_capacity, "reserve_capacity"),
        (evidence_weight, "evidence_weight"),
        (information_weight, "information_weight"),
        (diversity_weight, "diversity_weight"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AuditPortfolioError(f"{label} must be a nonnegative integer")
    if finalist_capacity < 1:
        raise AuditPortfolioError("finalist capacity must be at least one")
    supplied_weights = diversity_dimension_weights or {dimension: 1 for dimension in DiversityDimension}
    if set(supplied_weights) != set(DiversityDimension):
        raise AuditPortfolioError("portfolio policy must weight every diversity dimension explicitly")
    weights = tuple(sorted(((dimension, int(supplied_weights[dimension])) for dimension in DiversityDimension), key=lambda row: row[0].value))
    if any(weight < 0 for _, weight in weights):
        raise AuditPortfolioError("diversity weights must be nonnegative")
    allowed = tuple(sorted(set(allowed_therapeutic_tiers), key=lambda row: row.value))
    if not allowed:
        raise AuditPortfolioError("portfolio needs at least one allowed therapeutic tier")
    body = {
        "finalist_capacity": finalist_capacity,
        "reserve_capacity": reserve_capacity,
        "evidence_weight": evidence_weight,
        "information_weight": information_weight,
        "diversity_weight": diversity_weight,
        "diversity_dimension_weights": weights,
        "allowed_therapeutic_tiers": allowed,
        "selection_rule_version": "schema-v7-decomposed-diversified-greedy-v1",
    }
    return PortfolioPolicy(policy_id=_stable_id("PORTFOLIO-POLICY", body), **body)


_THERAPEUTIC_ORDER = {
    TherapeuticConfidenceTier.HIGH: 0,
    TherapeuticConfidenceTier.MODERATE: 1,
    TherapeuticConfidenceTier.LOW: 2,
    TherapeuticConfidenceTier.CONFLICTED: 3,
    TherapeuticConfidenceTier.INSUFFICIENT: 4,
}
_THERAPEUTIC_POINTS = {tier: 5 - ordinal for tier, ordinal in _THERAPEUTIC_ORDER.items()}
_RESEARCH_ORDER = {
    ResearchPriorityTier.HIGH: 0,
    ResearchPriorityTier.MODERATE: 1,
    ResearchPriorityTier.LOW: 2,
    ResearchPriorityTier.NOT_ACTIONABLE: 3,
}
_RESEARCH_POINTS = {tier: 4 - ordinal for tier, ordinal in _RESEARCH_ORDER.items()}


def _audit_block_reason(frame: PortfolioCandidateFrame) -> tuple[PortfolioDisposition | None, str]:
    record = frame.audit_record
    if record is None:
        return None, "audit outcome pending"
    if record.assignment_id != frame.audit_assignment.assignment_id or record.candidate_id != frame.candidate_id:
        raise AuditPortfolioError("portfolio frame audit record does not match its assignment")
    if record.decision_effect is AuditDecisionEffect.REJECTED:
        return PortfolioDisposition.AUDIT_REJECTED, "audit rejected the candidate"
    if record.decision_effect in {
        AuditDecisionEffect.QUARANTINED,
        AuditDecisionEffect.BLOCKED_UNRESOLVED,
    }:
        return PortfolioDisposition.AUDIT_QUARANTINED, "audit left a blocking conflict or quarantine"
    if record.decision_effect is AuditDecisionEffect.RERANKED:
        if not frame.ranking_revision_id or frame.ranking_revision_id != record.ranking_revision_id:
            raise AuditPortfolioError("audit correction/contradiction has not been deterministically reranked")
    return None, "audited and eligible for portfolio policy"


def _council_block_reason(frame: PortfolioCandidateFrame) -> tuple[PortfolioDisposition | None, str]:
    record = frame.council_record
    if record is None:
        return None, "no decision-changing council issue required review"
    if record.candidate_id != frame.candidate_id:
        raise AuditPortfolioError("portfolio frame council record belongs to another candidate")
    if record.disposition in {
        CouncilDisposition.CONFLICT_UNRESOLVED,
        CouncilDisposition.QUARANTINED,
        CouncilDisposition.REJECTED,
        CouncilDisposition.BASELINE_ONLY,
        CouncilDisposition.BENCHMARK_ONLY,
    }:
        return PortfolioDisposition.COUNCIL_BLOCKED, f"council disposition: {record.disposition.value}"
    return None, f"council disposition: {record.disposition.value}"


def select_diversified_portfolio(
    frames: Iterable[PortfolioCandidateFrame], policy: PortfolioPolicy
) -> PortfolioSelection:
    rows = list(frames)
    by_candidate = {row.candidate_id: row for row in rows}
    if len(by_candidate) != len(rows):
        raise AuditPortfolioError("portfolio frame must contain unique candidates")
    for row in rows:
        if row.preparation.candidate_id != row.candidate_id or row.diversity.candidate_id != row.candidate_id:
            raise AuditPortfolioError("portfolio frame candidate links are inconsistent")
        if row.audit_assignment.candidate_id != row.candidate_id:
            raise AuditPortfolioError("portfolio frame audit assignment belongs to another candidate")

    evidence_order = sorted(
        rows,
        key=lambda row: (
            _THERAPEUTIC_ORDER[row.preparation.therapeutic_confidence_tier],
            row.preparation.therapeutic_rank_within_tier,
            row.candidate_id,
        ),
    )
    evidence_rank = {row.candidate_id: index for index, row in enumerate(evidence_order, 1)}
    information_order = sorted(
        rows,
        key=lambda row: (
            _RESEARCH_ORDER[row.preparation.research_priority_tier],
            row.preparation.research_rank_within_tier,
            row.candidate_id,
        ),
    )
    information_rank = {row.candidate_id: index for index, row in enumerate(information_order, 1)}
    mechanism_clusters = derive_mechanism_clusters(row.diversity for row in rows)
    scaffold_clusters = derive_scaffold_clusters(row.diversity for row in rows)
    mechanism_by_candidate = {
        candidate_id: cluster.cluster_id
        for cluster in mechanism_clusters
        for candidate_id in cluster.candidate_ids
    }
    scaffold_by_candidate = {
        candidate_id: cluster.cluster_id
        for cluster in scaffold_clusters
        for candidate_id in cluster.candidate_ids
    }
    dimension_weights = dict(policy.diversity_dimension_weights)

    blocked: dict[str, tuple[PortfolioDisposition, str]] = {}
    potential: list[PortfolioCandidateFrame] = []
    for row in rows:
        if row.preparation.triage_category is TriageCategory.REJECTED_OR_QUARANTINED:
            blocked[row.candidate_id] = (
                PortfolioDisposition.AUDIT_QUARANTINED,
                "pre-audit triage is rejected or quarantined",
            )
            continue
        audit_block, audit_reason = _audit_block_reason(row)
        if audit_block is not None:
            blocked[row.candidate_id] = (audit_block, audit_reason)
            continue
        council_block, council_reason = _council_block_reason(row)
        if council_block is not None:
            blocked[row.candidate_id] = (council_block, council_reason)
            continue
        if row.preparation.therapeutic_confidence_tier not in policy.allowed_therapeutic_tiers:
            blocked[row.candidate_id] = (
                PortfolioDisposition.NOT_SELECTED,
                "therapeutic confidence tier is outside the frozen eligibility policy",
            )
            continue
        potential.append(row)

    selected_values: dict[DiversityDimension, set[str]] = {
        dimension: set() for dimension in DiversityDimension
    }
    order: list[PortfolioCandidateFrame] = []
    contributions: dict[str, tuple[DiversityContribution, ...]] = {}
    utility: dict[str, tuple[int, int, int, int]] = {}

    def dimension_values(row: PortfolioCandidateFrame) -> dict[DiversityDimension, tuple[str, ...]]:
        return {
            DiversityDimension.TARGET_MECHANISM: (mechanism_by_candidate[row.candidate_id],),
            DiversityDimension.CAUSAL_ROUTE: row.diversity.causal_route_ids,
            DiversityDimension.CHEMICAL_SCAFFOLD: (scaffold_by_candidate[row.candidate_id],),
            DiversityDimension.EVIDENCE_MODALITY: row.diversity.evidence_modalities,
            DiversityDimension.ENDPOINT: row.diversity.endpoint_ids,
            DiversityDimension.DEVELOPMENT_STATUS: row.diversity.development_statuses,
            DiversityDimension.UNCERTAINTY: row.diversity.uncertainty_bands,
        }

    remaining = {row.candidate_id: row for row in potential}
    while remaining:
        choices: list[tuple[tuple[Any, ...], PortfolioCandidateFrame, tuple[DiversityContribution, ...], tuple[int, int, int, int]]] = []
        for row in remaining.values():
            candidate_contributions: list[DiversityContribution] = []
            diversity_component = 0
            for dimension, values in dimension_values(row).items():
                new_values = tuple(sorted(set(values) - selected_values[dimension]))
                weight = dimension_weights[dimension] if new_values else 0
                diversity_component += weight
                candidate_contributions.append(
                    DiversityContribution(dimension=dimension, new_values=new_values, weight=weight)
                )
            evidence_component = _THERAPEUTIC_POINTS[row.preparation.therapeutic_confidence_tier]
            information_component = _RESEARCH_POINTS[row.preparation.research_priority_tier]
            total = (
                evidence_component * policy.evidence_weight
                + information_component * policy.information_weight
                + diversity_component * policy.diversity_weight
            )
            sort_key = (
                -total,
                evidence_rank[row.candidate_id],
                information_rank[row.candidate_id],
                row.candidate_id,
            )
            choices.append(
                (
                    sort_key,
                    row,
                    tuple(candidate_contributions),
                    (evidence_component, information_component, diversity_component, total),
                )
            )
        _, winner, winner_contributions, winner_utility = min(choices, key=lambda item: item[0])
        order.append(winner)
        contributions[winner.candidate_id] = winner_contributions
        utility[winner.candidate_id] = winner_utility
        for dimension, values in dimension_values(winner).items():
            selected_values[dimension].update(values)
        del remaining[winner.candidate_id]

    diversified_rank = {row.candidate_id: index for index, row in enumerate(order, 1)}
    capacity = policy.finalist_capacity + policy.reserve_capacity
    pending_in_capacity = tuple(
        row.candidate_id
        for row in order[:capacity]
        if row.audit_assignment.selection_status is AuditSelectionStatus.UNAUDITED or row.audit_record is None
    )
    status = (
        PortfolioSelectionStatus.NEEDS_ADDITIONAL_AUDIT
        if pending_in_capacity
        else PortfolioSelectionStatus.COMPLETE
    )
    finalist_ids: tuple[str, ...] = ()
    reserve_ids: tuple[str, ...] = ()
    if status is PortfolioSelectionStatus.COMPLETE:
        finalist_ids = tuple(row.candidate_id for row in order[: policy.finalist_capacity])
        reserve_ids = tuple(
            row.candidate_id
            for row in order[
                policy.finalist_capacity : policy.finalist_capacity + policy.reserve_capacity
            ]
        )

    rank_records: list[PortfolioRankRecord] = []
    for row in rows:
        evidence_component = _THERAPEUTIC_POINTS[row.preparation.therapeutic_confidence_tier]
        information_component = _RESEARCH_POINTS[row.preparation.research_priority_tier]
        diversity_component = utility.get(row.candidate_id, (0, 0, 0, 0))[2]
        total = utility.get(row.candidate_id, (0, 0, 0, 0))[3]
        if row.candidate_id in blocked:
            disposition, reason = blocked[row.candidate_id]
        elif status is PortfolioSelectionStatus.NEEDS_ADDITIONAL_AUDIT:
            if row.candidate_id in pending_in_capacity:
                disposition = PortfolioDisposition.UNAUDITED
                reason = "candidate would enter the capacity set and requires audit before selection"
            else:
                disposition = PortfolioDisposition.SELECTION_PENDING
                reason = "portfolio membership is pending required candidate audit"
        elif row.candidate_id in finalist_ids:
            disposition = PortfolioDisposition.FINALIST
            reason = "selected within finalist capacity by the frozen decomposed portfolio policy"
        elif row.candidate_id in reserve_ids:
            disposition = PortfolioDisposition.RESERVE
            reason = "eligible and audited; omitted from finalists only by capacity/diversity policy"
        elif row.audit_assignment.selection_status is AuditSelectionStatus.UNAUDITED:
            disposition = PortfolioDisposition.UNAUDITED
            reason = "explicitly unaudited and outside the current capacity set"
        else:
            disposition = PortfolioDisposition.NOT_SELECTED
            reason = "audited and eligible but outside finalist/reserve capacity"
        rank_records.append(
            PortfolioRankRecord(
                candidate_id=row.candidate_id,
                evidence_strength_rank=evidence_rank[row.candidate_id],
                novelty_information_value_rank=information_rank[row.candidate_id],
                diversified_portfolio_rank=diversified_rank.get(row.candidate_id),
                disposition=disposition,
                evidence_component=evidence_component,
                novelty_information_component=information_component,
                diversity_component=diversity_component,
                total_selection_utility=total,
                diversity_contributions=contributions.get(row.candidate_id, ()),
                audit_status=row.audit_assignment.selection_status,
                audit_outcome=row.audit_record.outcome if row.audit_record else None,
                council_disposition=row.council_record.disposition if row.council_record else None,
                reason=reason,
            )
        )
    rank_records.sort(key=lambda row: row.candidate_id)
    body = {
        "model_version": AUDIT_PORTFOLIO_MODEL_VERSION,
        "policy": policy,
        "status": status,
        "records": tuple(rank_records),
        "mechanism_clusters": mechanism_clusters,
        "scaffold_clusters": scaffold_clusters,
        "finalist_ids": finalist_ids,
        "reserve_ids": reserve_ids,
        "additional_audit_required_ids": pending_in_capacity,
    }
    return PortfolioSelection(selection_id=_stable_id("PORTFOLIO", body), **body)


__all__ = [
    "AUDIT_PORTFOLIO_MODEL_VERSION",
    "AuditAssignment",
    "AuditCandidateFrame",
    "AuditCorrection",
    "AuditCorrectionAction",
    "AuditDecisionEffect",
    "AuditOutcome",
    "AuditPlan",
    "AuditPortfolioError",
    "AuditRecord",
    "AuditSamplingPolicy",
    "AuditSelectionStatus",
    "AuditStratum",
    "CandidateDiversityFeatures",
    "CorrectedCandidateState",
    "CorrectionAuthorityField",
    "CouncilAssessment",
    "CouncilDisposition",
    "CouncilFinding",
    "CouncilIssue",
    "CouncilIssueKind",
    "CouncilRecord",
    "DecisionImpact",
    "DiversityDimension",
    "MechanismCluster",
    "PortfolioCandidateFrame",
    "PortfolioDisposition",
    "PortfolioPolicy",
    "PortfolioSelection",
    "PortfolioSelectionStatus",
    "ScaffoldCluster",
    "ScaffoldDescriptor",
    "apply_audit_corrections",
    "build_audit_candidate_frames",
    "build_audit_plan",
    "derive_mechanism_clusters",
    "derive_scaffold_clusters",
    "make_audit_correction",
    "make_audit_record",
    "make_audit_sampling_policy",
    "make_council_assessment",
    "make_council_issue",
    "make_council_record",
    "make_diversity_features",
    "make_portfolio_policy",
    "make_scaffold_descriptor",
    "select_diversified_portfolio",
]
