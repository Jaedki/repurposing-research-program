#!/usr/bin/env python3
"""Deterministic schema-v7 evidence features, triage, and pre-audit ranking.

This module deliberately stops before scientific-audit sampling, council policy,
portfolio membership, and user-facing output construction.  It accepts only typed
facts, derives every ordinal from published decision tables, and emits separate
therapeutic-confidence and research-priority orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable, Mapping

from v7_case_model import (
    CaseRevision,
    EndpointPriority,
    EndpointRelationshipType,
    EndpointRole,
    EndpointType,
    ValueStatus,
    validate_case_revision,
)
from v7_deep_evidence import (
    AuthoritativeIdentityRecord,
    ClaimCalibration,
    ClaimPolarity,
    ClaimReportingStatus,
    DeepEvidencePackage,
    DevelopmentStatus,
    EndpointDeepStatus,
    ExperimentalModelKind,
    HumanUseStatus,
    IdentityResolutionStatus,
    ObservedEffectDirection,
    ReportedQuantity,
    ReportedValueStatus,
    RiskOfBiasLevel,
    StudyDesign,
    VerificationMode,
    validate_deep_evidence_package,
)
from v7_discovery import EffectDirection, EvidenceModality, NodeStatus


SCHEMA_VERSION = 7
TRIAGE_RANKING_MODEL_VERSION = "schema-v7-triage-ranking-v1"


class TriageRankingError(ValueError):
    """Raised when typed ranking inputs or derived outputs are inconsistent."""


class Dimension(str, Enum):
    THERAPEUTIC_SUPPORT = "therapeutic_support"
    EVIDENCE_QUALITY = "evidence_quality"
    MECHANISTIC_COHERENCE = "mechanistic_coherence"
    HUMAN_CLINICAL_EVIDENCE = "human_clinical_evidence"
    HUMAN_DERIVED_MODEL_EVIDENCE = "human_derived_model_evidence"
    ENDPOINT_SPECIFICITY = "endpoint_specificity"
    CLINICAL_TRANSLATABILITY = "clinical_translatability"
    EXPOSURE_FEASIBILITY = "exposure_feasibility"
    SAFETY_AND_TOLERABILITY = "safety_and_tolerability"
    REPURPOSING_READINESS = "repurposing_readiness"
    NOVELTY_UNDEREXPLORATION = "novelty_underexploration"
    UNCERTAINTY = "uncertainty"
    INFORMATION_VALUE = "information_value"


DIMENSION_BANDS: Mapping[Dimension, tuple[str, ...]] = {
    Dimension.THERAPEUTIC_SUPPORT: (
        "strong",
        "moderate",
        "limited",
        "conflicting",
        "refuted_or_null",
        "insufficient",
    ),
    Dimension.EVIDENCE_QUALITY: ("high", "moderate", "low", "very_low", "insufficient"),
    Dimension.MECHANISTIC_COHERENCE: ("coherent", "plausible", "mixed", "incoherent", "unknown"),
    Dimension.HUMAN_CLINICAL_EVIDENCE: (
        "direct_interventional",
        "supportive_observational",
        "mixed",
        "negative_or_null",
        "absent",
    ),
    Dimension.HUMAN_DERIVED_MODEL_EVIDENCE: (
        "replicated",
        "single_context",
        "mixed",
        "negative_or_null",
        "absent",
    ),
    Dimension.ENDPOINT_SPECIFICITY: (
        "direct_primary",
        "direct_secondary",
        "surrogate_linked",
        "nonspecific",
        "unknown",
    ),
    Dimension.CLINICAL_TRANSLATABILITY: ("high", "moderate", "low", "blocked", "unknown"),
    Dimension.EXPOSURE_FEASIBILITY: ("feasible", "borderline", "unknown", "conflicting", "infeasible"),
    Dimension.SAFETY_AND_TOLERABILITY: (
        "acceptable",
        "manageable",
        "unknown",
        "conflicting",
        "serious_mismatch",
    ),
    Dimension.REPURPOSING_READINESS: (
        "marketed_repurposing_ready",
        "human_experience",
        "clinical_asset",
        "preclinical",
        "blocked_or_withdrawn",
        "unknown",
    ),
    Dimension.NOVELTY_UNDEREXPLORATION: (
        "novel_hypothesis",
        "underexplored",
        "emerging",
        "established",
        "unknown",
    ),
    Dimension.UNCERTAINTY: ("low", "moderate", "high", "decision_blocking"),
    Dimension.INFORMATION_VALUE: ("high", "moderate", "low", "not_actionable"),
}


class AssessmentKind(str, Enum):
    DETERMINISTIC = "deterministic"
    EXPERT_ASSESSMENT = "expert_assessment"


class ScopeEligibility(str, Enum):
    ELIGIBLE = "eligible"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class TissueApplicability(str, Enum):
    MATCHED = "matched"
    PLASMA_PROXY = "plasma_proxy"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class PharmacokineticBasis(str, Enum):
    MEASURED_HUMAN = "measured_human"
    MODELED_HUMAN = "modeled_human"
    MEASURED_NONHUMAN = "measured_nonhuman"
    NOT_REPORTED = "not_reported"


class DoseContext(str, Enum):
    CLINICALLY_ATTAINABLE = "clinically_attainable"
    EXCEEDS_TOLERATED = "exceeds_tolerated"
    PRECLINICAL_ONLY = "preclinical_only"
    UNKNOWN = "unknown"


class SafetyEvidenceKind(str, Enum):
    ADVERSE_EVENT = "adverse_event"
    CONTRAINDICATION = "contraindication"
    INTERACTION = "interaction"
    POPULATION_RISK = "population_risk"


class SafetyFinding(str, Enum):
    RISK = "risk"
    NO_MATERIAL_RISK = "no_material_risk"
    UNKNOWN = "unknown"


class SafetySeverity(str, Enum):
    NONE = "none"
    NON_SERIOUS = "non_serious"
    SERIOUS = "serious"
    LIFE_THREATENING = "life_threatening"
    FATAL = "fatal"
    UNKNOWN = "unknown"


class SafetyCausality(str, Enum):
    ESTABLISHED = "established"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"
    UNRELATED = "unrelated"
    UNKNOWN = "unknown"


class CaseApplicability(str, Enum):
    DIRECT = "direct"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class FrequencyBand(str, Enum):
    VERY_COMMON = "very_common"
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very_rare"
    NOT_REPORTED = "not_reported"


class TargetDiseaseDevelopment(str, Enum):
    NONE_FOUND = "none_found"
    PRECLINICAL_ONLY = "preclinical_only"
    CLINICAL = "clinical"
    APPROVED = "approved"
    UNKNOWN = "unknown"


class TriageCategory(str, Enum):
    IDENTITY_FOLLOW_UP = "identity_follow_up"
    EVIDENCE_FOLLOW_UP = "evidence_follow_up"
    DEEP_REVIEW = "deep_review"
    DEFERRED_PRESERVED = "deferred_but_preserved"
    REJECTED_OR_QUARANTINED = "rejected_or_quarantined"


class TerminalDisposition(str, Enum):
    NOT_TERMINAL = "not_terminal"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class TherapeuticConfidenceTier(str, Enum):
    HIGH = "high_confidence"
    MODERATE = "moderate_confidence"
    LOW = "low_confidence_hypothesis"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class ResearchPriorityTier(str, Enum):
    HIGH = "high_information_priority"
    MODERATE = "moderate_information_priority"
    LOW = "low_information_priority"
    NOT_ACTIONABLE = "not_actionable"


@dataclass(frozen=True)
class DecisionTableRow:
    rule_id: str
    outcome: str
    condition: str


DECISION_TABLES: Mapping[str, tuple[DecisionTableRow, ...]] = {
    "therapeutic_support": (
        DecisionTableRow("TS-01", "conflicting", "supporting and refuting/mixed independent ancestry clusters"),
        DecisionTableRow("TS-02", "strong", "at least two supporting independent clusters including direct human evidence and no refuting cluster"),
        DecisionTableRow("TS-03", "moderate", "at least two supporting independent clusters or one supporting human-clinical cluster, with no refuting cluster"),
        DecisionTableRow("TS-04", "limited", "one supporting cluster without refuting evidence"),
        DecisionTableRow("TS-05", "refuted_or_null", "only refuting/null clusters"),
        DecisionTableRow("TS-06", "insufficient", "no directional evidence cluster"),
    ),
    "evidence_quality": (
        DecisionTableRow("EQ-01", "high", "support includes low-bias randomized human evidence and at least two independent clusters"),
        DecisionTableRow("EQ-02", "moderate", "support includes acceptable human interventional/observational evidence or replicated human-derived evidence"),
        DecisionTableRow("EQ-03", "low", "support is grounded but limited to one lower-directness cluster"),
        DecisionTableRow("EQ-04", "very_low", "support is speculative/high-bias or ancestry independence is unresolved"),
        DecisionTableRow("EQ-05", "insufficient", "no supporting evidence"),
    ),
    "mechanistic_coherence": (
        DecisionTableRow("MC-01", "mixed", "structured route exists but therapeutic directions conflict"),
        DecisionTableRow("MC-02", "coherent", "known-direction structural route and supporting evidence agree"),
        DecisionTableRow("MC-03", "plausible", "structural route exists but direction/evidence remains incomplete"),
        DecisionTableRow("MC-04", "incoherent", "route is directionally opposed without support"),
        DecisionTableRow("MC-05", "unknown", "no endpoint-linked structural route"),
    ),
    "human_clinical_evidence": (
        DecisionTableRow("HC-01", "mixed", "human clinical clusters include support and refutation/null"),
        DecisionTableRow("HC-02", "direct_interventional", "supportive human interventional evidence"),
        DecisionTableRow("HC-03", "supportive_observational", "supportive human observational evidence only"),
        DecisionTableRow("HC-04", "negative_or_null", "human clinical evidence is negative/null only"),
        DecisionTableRow("HC-05", "absent", "no human clinical evidence; patient-cell evidence is not clinical evidence"),
    ),
    "human_derived_model_evidence": (
        DecisionTableRow("HM-01", "mixed", "human-derived model clusters include support and refutation/null"),
        DecisionTableRow("HM-02", "replicated", "at least two independent supportive human-derived model clusters"),
        DecisionTableRow("HM-03", "single_context", "one supportive human-derived model cluster"),
        DecisionTableRow("HM-04", "negative_or_null", "human-derived model evidence is negative/null only"),
        DecisionTableRow("HM-05", "absent", "no human-derived model evidence"),
    ),
    "endpoint_specificity": (
        DecisionTableRow("ES-01", "direct_primary", "directional evidence addresses the declared primary clinical/functional/symptom/safety/composite endpoint"),
        DecisionTableRow("ES-02", "direct_secondary", "directional evidence directly addresses a retained secondary clinical/functional/symptom/safety/composite endpoint"),
        DecisionTableRow("ES-03", "surrogate_linked", "biomarker/surrogate evidence has a typed relationship to another endpoint"),
        DecisionTableRow("ES-04", "nonspecific", "directional evidence exists but is not direct or linked"),
        DecisionTableRow("ES-05", "unknown", "no directional endpoint evidence"),
    ),
    "clinical_translatability": (
        DecisionTableRow("CT-01", "blocked", "identity, exposure, safety, or development state blocks translation"),
        DecisionTableRow("CT-02", "high", "human evidence, feasible/borderline exposure, acceptable/manageable safety, and human-use readiness align"),
        DecisionTableRow("CT-03", "moderate", "human-use readiness exists without a hard exposure/safety block"),
        DecisionTableRow("CT-04", "low", "exact identity is eligible but readiness is preclinical/unknown"),
        DecisionTableRow("CT-05", "unknown", "translation facts are insufficient"),
    ),
    "exposure_feasibility": (
        DecisionTableRow("EX-01", "conflicting", "relevant structured exposure records disagree"),
        DecisionTableRow("EX-02", "feasible", "clinically attainable dose, compatible route/tissue, human PK, and achieved/effective concentration margin >= 3"),
        DecisionTableRow("EX-03", "borderline", "same structured requirements with concentration margin >= 1 and < 3"),
        DecisionTableRow("EX-04", "infeasible", "dose exceeds tolerance, route/tissue mismatches, or all comparable margins are < 1"),
        DecisionTableRow("EX-05", "unknown", "dose, human PK, tissue concentration, or effective concentration is missing"),
    ),
    "safety_and_tolerability": (
        DecisionTableRow("SA-01", "conflicting", "directly applicable serious risk and grounded permissive evidence coexist"),
        DecisionTableRow("SA-02", "serious_mismatch", "directly applicable serious adverse event, contraindication, interaction, or population risk"),
        DecisionTableRow("SA-03", "manageable", "only applicable non-serious/reversible risks are present"),
        DecisionTableRow("SA-04", "acceptable", "grounded directly applicable evidence reports no material risk and no material risk record exists"),
        DecisionTableRow("SA-05", "unknown", "structured safety evidence is absent or not applicable to the case"),
    ),
    "repurposing_readiness": (
        DecisionTableRow("RR-01", "blocked_or_withdrawn", "grounded development status is failed/withdrawn/discontinued"),
        DecisionTableRow("RR-02", "marketed_repurposing_ready", "marketed human product without established target-disease use"),
        DecisionTableRow("RR-03", "human_experience", "administered-in-humans evidence exists"),
        DecisionTableRow("RR-04", "clinical_asset", "investigational/phase clinical development exists"),
        DecisionTableRow("RR-05", "preclinical", "preclinical status or no documented human use"),
        DecisionTableRow("RR-06", "unknown", "readiness facts are insufficient"),
    ),
    "novelty_underexploration": (
        DecisionTableRow("NV-01", "novel_hypothesis", "no target-disease development, <=1 direct publication, and zero trials"),
        DecisionTableRow("NV-02", "underexplored", "no target-disease development, <=5 direct publications, and zero trials"),
        DecisionTableRow("NV-03", "emerging", "preclinical target-disease work or <=20 direct publications"),
        DecisionTableRow("NV-04", "established", "clinical/approved target-disease development or a larger direct literature"),
        DecisionTableRow("NV-05", "unknown", "literature/development denominator is unknown"),
    ),
    "uncertainty": (
        DecisionTableRow("UN-01", "decision_blocking", "identity, direction, exposure, or safety has a decision-changing conflict/block"),
        DecisionTableRow("UN-02", "high", "major ancestry, quality, mechanism, safety/exposure, or missingness gaps remain"),
        DecisionTableRow("UN-03", "moderate", "limited support/quality or plausible-only mechanism remains"),
        DecisionTableRow("UN-04", "low", "no typed material uncertainty trigger remains"),
    ),
    "information_value": (
        DecisionTableRow("IV-01", "not_actionable", "confirmed safety/exposure block or refuted/null therapeutic evidence"),
        DecisionTableRow("IV-02", "high", "underexplored plausible signal with resolvable moderate/high uncertainty"),
        DecisionTableRow("IV-03", "moderate", "plausible signal with resolvable moderate/high uncertainty"),
        DecisionTableRow("IV-04", "low", "remaining evidence gap has low expected decision value"),
    ),
    "triage": (
        DecisionTableRow("TR-01", "rejected_or_quarantined", "explicitly prohibited intervention scope"),
        DecisionTableRow("TR-02", "rejected_or_quarantined", "identity is already quarantined"),
        DecisionTableRow("TR-03", "identity_follow_up", "decision-relevant identity is unresolved or conflicting"),
        DecisionTableRow("TR-04", "rejected_or_quarantined", "structured serious safety mismatch or infeasible exposure"),
        DecisionTableRow("TR-05", "rejected_or_quarantined", "structured safety/exposure evidence conflicts"),
        DecisionTableRow("TR-06", "evidence_follow_up", "therapeutic direction conflicts or a decision-changing evidence gap remains"),
        DecisionTableRow("TR-07", "deep_review", "moderate/strong support, coherent/plausible route, and no safety/exposure blocker"),
        DecisionTableRow("TR-08", "evidence_follow_up", "limited/sparse support has high or moderate information value"),
        DecisionTableRow("TR-09", "deferred_but_preserved", "no blocker warrants rejection but current support/information value is insufficient"),
    ),
}


@dataclass(frozen=True)
class EvidenceFeature:
    evidence_record_id: str
    claim_id: str
    endpoint_id: str
    source_id: str
    evidence_span_id: str
    polarity: ClaimPolarity
    reporting_status: ClaimReportingStatus
    calibration: ClaimCalibration
    evidence_modality: EvidenceModality
    study_design: StudyDesign
    model_kind: ExperimentalModelKind
    species: str
    effect_direction: ObservedEffectDirection
    risk_of_bias: RiskOfBiasLevel


@dataclass(frozen=True)
class EvidenceAncestry:
    evidence_record_id: str
    source_ids: tuple[str, ...]
    cohort_ids: tuple[str, ...]
    laboratory_ids: tuple[str, ...]
    dataset_ids: tuple[str, ...]
    common_ancestry_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceIndependenceCluster:
    cluster_id: str
    evidence_record_ids: tuple[str, ...]
    shared_source_ids: tuple[str, ...]
    shared_cohort_ids: tuple[str, ...]
    shared_laboratory_ids: tuple[str, ...]
    shared_dataset_ids: tuple[str, ...]
    shared_ancestry_ids: tuple[str, ...]


@dataclass(frozen=True)
class EndpointFeature:
    endpoint_id: str
    role: EndpointRole | None
    endpoint_type: EndpointType | None
    priority: EndpointPriority | None
    required: bool | None
    deep_status: EndpointDeepStatus
    claim_ids: tuple[str, ...]
    relationship_types: tuple[EndpointRelationshipType, ...]


@dataclass(frozen=True)
class MechanisticRouteFeature:
    route_id: str
    endpoint_id: str
    direction_known: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExposureEvidence:
    exposure_record_id: str
    candidate_id: str
    exact_intervention_id: str
    dose: ReportedQuantity
    dose_context: DoseContext
    administration_route: str
    duration: str
    population: str
    target_tissue: str
    tissue_applicability: TissueApplicability
    pk_basis: PharmacokineticBasis
    achieved_concentration: ReportedQuantity
    required_effect_concentration: ReportedQuantity
    source_record_ids: tuple[str, ...]
    evidence_span_ids: tuple[str, ...]


@dataclass(frozen=True)
class SafetyEvidence:
    safety_record_id: str
    candidate_id: str
    exact_intervention_id: str
    evidence_kind: SafetyEvidenceKind
    finding: SafetyFinding
    severity: SafetySeverity
    causality: SafetyCausality
    frequency: FrequencyBand
    case_applicability: CaseApplicability
    dose: ReportedQuantity
    administration_route: str
    duration: str
    population: str
    reversibility: str
    finding_code: str
    source_record_ids: tuple[str, ...]
    evidence_span_ids: tuple[str, ...]


@dataclass(frozen=True)
class LiteratureLandscape:
    direct_target_disease_publication_count: int | None
    direct_target_disease_trial_count: int | None
    development_in_target_disease: TargetDiseaseDevelopment
    earliest_direct_evidence_year: int | None
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExpertAssessment:
    assessment_id: str
    dimension: Dimension
    endpoint_id: str | None
    selected_band: str
    evidence_record_ids: tuple[str, ...]
    model_id: str
    prompt_template_version: str
    interpretation_question: str
    rationale: str
    cache_key: str
    cached_response_sha256: str
    label: str


@dataclass(frozen=True)
class CandidateEvidenceInput:
    schema_version: int
    model_version: str
    candidate_id: str
    case_revision_id: str
    normalized_intervention_id: str | None
    identity_status: IdentityResolutionStatus
    deep_identity_eligible: bool
    scope_eligibility: ScopeEligibility
    scope_reason: str
    primary_endpoint_id: str
    endpoints: tuple[EndpointFeature, ...]
    evidence: tuple[EvidenceFeature, ...]
    ancestry: tuple[EvidenceAncestry, ...]
    routes: tuple[MechanisticRouteFeature, ...]
    exposure: tuple[ExposureEvidence, ...]
    safety: tuple[SafetyEvidence, ...]
    human_use_statuses: tuple[HumanUseStatus, ...]
    development_statuses: tuple[DevelopmentStatus, ...]
    formulation_routes: tuple[str, ...]
    allowed_routes: tuple[str, ...]
    excluded_routes: tuple[str, ...]
    route_constraints_known: bool
    literature_landscape: LiteratureLandscape
    explicit_uncertainties: tuple[str, ...]
    expert_assessments: tuple[ExpertAssessment, ...]


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: str


@dataclass(frozen=True)
class DimensionAssessment:
    dimension: Dimension
    endpoint_id: str | None
    band: str
    ordinal: int
    decision_rule_id: str
    features: tuple[FeatureValue, ...]
    evidence_record_ids: tuple[str, ...]
    assessment_kind: AssessmentKind
    expert_assessment_ids: tuple[str, ...]


@dataclass(frozen=True)
class EndpointDecisionProfile:
    endpoint_id: str
    primary: bool
    source_status: EndpointDeepStatus
    therapeutic_support: DimensionAssessment
    evidence_quality: DimensionAssessment
    mechanistic_coherence: DimensionAssessment
    human_clinical_evidence: DimensionAssessment
    human_derived_model_evidence: DimensionAssessment
    endpoint_specificity: DimensionAssessment


@dataclass(frozen=True)
class TriageDecision:
    disposition_id: str
    candidate_id: str
    category: TriageCategory
    terminal_disposition: TerminalDisposition
    reason_code: str
    decision_rule_id: str
    primary_endpoint_id: str
    evidence_record_ids: tuple[str, ...]
    feature_projection_sha256: str


@dataclass(frozen=True)
class CandidateDecisionProfile:
    schema_version: int
    model_version: str
    profile_id: str
    candidate_id: str
    case_revision_id: str
    normalized_intervention_id: str | None
    primary_endpoint_id: str
    endpoint_assessments: tuple[EndpointDecisionProfile, ...]
    therapeutic_support: DimensionAssessment
    evidence_quality: DimensionAssessment
    mechanistic_coherence: DimensionAssessment
    human_clinical_evidence: DimensionAssessment
    human_derived_model_evidence: DimensionAssessment
    endpoint_specificity: DimensionAssessment
    clinical_translatability: DimensionAssessment
    exposure_feasibility: DimensionAssessment
    safety_and_tolerability: DimensionAssessment
    repurposing_readiness: DimensionAssessment
    novelty_underexploration: DimensionAssessment
    uncertainty: DimensionAssessment
    information_value: DimensionAssessment
    independence_clusters: tuple[EvidenceIndependenceCluster, ...]
    triage: TriageDecision


@dataclass(frozen=True)
class RankingPreparationRecord:
    preparation_id: str
    candidate_id: str
    profile_id: str
    primary_endpoint_id: str
    triage_category: TriageCategory
    therapeutic_confidence_tier: TherapeuticConfidenceTier
    therapeutic_rank_within_tier: int
    research_priority_tier: ResearchPriorityTier
    research_rank_within_tier: int
    therapeutic_ordering_bands: tuple[str, ...]
    research_ordering_bands: tuple[str, ...]
    deterministic_tie_breaker: str
    ordering_rule_version: str


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=lambda row: str(row[0]))}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(_plain(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(value)[:24]}"


def _without_field(value: Any, field_name: str) -> dict[str, Any]:
    return {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.name != field_name
    }


def _text(value: Any, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise TriageRankingError(f"{label} is required")
    return result


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, label) for value in values}))
    if required and not result:
        raise TriageRankingError(f"{label} must be nonempty")
    return result


def _route_token(value: str) -> str:
    return " ".join(str(value).casefold().replace("_", " ").replace("-", " ").split())


def _ordinal(dimension: Dimension, band: str) -> int:
    try:
        bands = DIMENSION_BANDS[dimension]
        return len(bands) - bands.index(band) - 1
    except (KeyError, ValueError) as exc:
        raise TriageRankingError(f"invalid band {band!r} for {dimension.value}") from exc


def _assessment(
    dimension: Dimension,
    band: str,
    rule_id: str,
    *,
    endpoint_id: str | None = None,
    features: Mapping[str, Any] | None = None,
    evidence_record_ids: Iterable[str] = (),
    assessment_kind: AssessmentKind = AssessmentKind.DETERMINISTIC,
    expert_assessment_ids: Iterable[str] = (),
) -> DimensionAssessment:
    return DimensionAssessment(
        dimension=dimension,
        endpoint_id=endpoint_id,
        band=band,
        ordinal=_ordinal(dimension, band),
        decision_rule_id=_text(rule_id, "decision rule ID"),
        features=tuple(
            FeatureValue(str(key), str(value))
            for key, value in sorted((features or {}).items(), key=lambda row: str(row[0]))
        ),
        evidence_record_ids=_strings(evidence_record_ids, "assessment evidence record ID"),
        assessment_kind=assessment_kind,
        expert_assessment_ids=_strings(expert_assessment_ids, "expert assessment ID"),
    )


def make_expert_assessment(
    *,
    dimension: Dimension,
    endpoint_id: str | None,
    selected_band: str,
    evidence_record_ids: Iterable[str],
    model_id: str,
    prompt_template_version: str,
    interpretation_question: str,
    rationale: str,
) -> ExpertAssessment:
    """Create a cache-addressed assessment record; it is never labeled as fact."""

    _ordinal(dimension, selected_band)
    evidence_ids = _strings(evidence_record_ids, "expert evidence record ID", required=True)
    request = {
        "dimension": dimension,
        "endpoint_id": endpoint_id,
        "evidence_record_ids": evidence_ids,
        "model_id": _text(model_id, "model_id"),
        "prompt_template_version": _text(prompt_template_version, "prompt_template_version"),
        "interpretation_question": _text(interpretation_question, "interpretation_question"),
    }
    response = {
        "selected_band": selected_band,
        "rationale": _text(rationale, "expert assessment rationale"),
        "label": "assessment_not_deterministic_fact",
    }
    body = {
        **request,
        **response,
        "cache_key": _sha256(request),
        "cached_response_sha256": _sha256(response),
    }
    return ExpertAssessment(
        assessment_id=_stable_id("EXPERT-ASSESSMENT", body),
        dimension=dimension,
        endpoint_id=endpoint_id,
        selected_band=selected_band,
        evidence_record_ids=evidence_ids,
        model_id=request["model_id"],
        prompt_template_version=request["prompt_template_version"],
        interpretation_question=request["interpretation_question"],
        rationale=response["rationale"],
        cache_key=body["cache_key"],
        cached_response_sha256=body["cached_response_sha256"],
        label=response["label"],
    )


def make_exposure_evidence(
    *,
    candidate_id: str,
    exact_intervention_id: str,
    dose: ReportedQuantity,
    dose_context: DoseContext,
    administration_route: str,
    duration: str,
    population: str,
    target_tissue: str,
    tissue_applicability: TissueApplicability,
    pk_basis: PharmacokineticBasis,
    achieved_concentration: ReportedQuantity,
    required_effect_concentration: ReportedQuantity,
    source_record_ids: Iterable[str],
    evidence_span_ids: Iterable[str],
) -> ExposureEvidence:
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "exact_intervention_id": _text(exact_intervention_id, "exact_intervention_id"),
        "dose": dose,
        "dose_context": dose_context,
        "administration_route": _route_token(_text(administration_route, "administration_route")),
        "duration": _text(duration, "duration"),
        "population": _text(population, "population"),
        "target_tissue": _text(target_tissue, "target_tissue"),
        "tissue_applicability": tissue_applicability,
        "pk_basis": pk_basis,
        "achieved_concentration": achieved_concentration,
        "required_effect_concentration": required_effect_concentration,
        "source_record_ids": _strings(source_record_ids, "exposure source record ID", required=True),
        "evidence_span_ids": _strings(evidence_span_ids, "exposure evidence span ID", required=True),
    }
    return ExposureEvidence(exposure_record_id=_stable_id("EXPOSURE", body), **body)


def make_safety_evidence(
    *,
    candidate_id: str,
    exact_intervention_id: str,
    evidence_kind: SafetyEvidenceKind,
    finding: SafetyFinding,
    severity: SafetySeverity,
    causality: SafetyCausality,
    frequency: FrequencyBand,
    case_applicability: CaseApplicability,
    dose: ReportedQuantity,
    administration_route: str,
    duration: str,
    population: str,
    reversibility: str,
    finding_code: str,
    source_record_ids: Iterable[str],
    evidence_span_ids: Iterable[str],
) -> SafetyEvidence:
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "exact_intervention_id": _text(exact_intervention_id, "exact_intervention_id"),
        "evidence_kind": evidence_kind,
        "finding": finding,
        "severity": severity,
        "causality": causality,
        "frequency": frequency,
        "case_applicability": case_applicability,
        "dose": dose,
        "administration_route": _route_token(_text(administration_route, "administration_route")),
        "duration": _text(duration, "duration"),
        "population": _text(population, "population"),
        "reversibility": _text(reversibility, "reversibility"),
        "finding_code": _text(finding_code, "finding_code"),
        "source_record_ids": _strings(source_record_ids, "safety source record ID", required=True),
        "evidence_span_ids": _strings(evidence_span_ids, "safety evidence span ID", required=True),
    }
    return SafetyEvidence(safety_record_id=_stable_id("SAFETY", body), **body)


def _validate_reported_quantity(value: ReportedQuantity, label: str) -> None:
    if not isinstance(value, ReportedQuantity) or not isinstance(value.status, ReportedValueStatus):
        raise TriageRankingError(f"{label} is malformed")
    if value.status is ReportedValueStatus.REPORTED:
        _text(value.value, f"{label}.value")
        _text(value.unit, f"{label}.unit")
    elif value.value is not None or value.unit is not None:
        raise TriageRankingError(f"{label} cannot carry a value when it is not reported")
    _text(value.note, f"{label}.note")


def _validate_expert_assessment(value: ExpertAssessment, evidence_ids: set[str]) -> None:
    if value.label != "assessment_not_deterministic_fact":
        raise TriageRankingError("expert judgment must be labeled as an assessment, not deterministic fact")
    _ordinal(value.dimension, value.selected_band)
    if not value.evidence_record_ids or not set(value.evidence_record_ids).issubset(evidence_ids):
        raise TriageRankingError("expert assessment evidence must resolve to grounded candidate evidence")
    request = {
        "dimension": value.dimension,
        "endpoint_id": value.endpoint_id,
        "evidence_record_ids": value.evidence_record_ids,
        "model_id": value.model_id,
        "prompt_template_version": value.prompt_template_version,
        "interpretation_question": value.interpretation_question,
    }
    response = {
        "selected_band": value.selected_band,
        "rationale": value.rationale,
        "label": value.label,
    }
    body = {
        **request,
        **response,
        "cache_key": _sha256(request),
        "cached_response_sha256": _sha256(response),
    }
    if value.cache_key != body["cache_key"] or value.cached_response_sha256 != body["cached_response_sha256"]:
        raise TriageRankingError("expert assessment cache binding is invalid")
    if value.assessment_id != _stable_id("EXPERT-ASSESSMENT", body):
        raise TriageRankingError("expert assessment content-derived ID is invalid")


def validate_candidate_evidence_input(value: CandidateEvidenceInput) -> None:
    if value.schema_version != SCHEMA_VERSION or value.model_version != TRIAGE_RANKING_MODEL_VERSION:
        raise TriageRankingError("candidate evidence input version mismatch")
    _text(value.candidate_id, "candidate_id")
    _text(value.case_revision_id, "case_revision_id")
    _text(value.primary_endpoint_id, "primary_endpoint_id")
    _text(value.scope_reason, "scope_reason")
    if value.normalized_intervention_id is not None:
        _text(value.normalized_intervention_id, "normalized_intervention_id")
    if not isinstance(value.identity_status, IdentityResolutionStatus):
        raise TriageRankingError("identity_status is invalid")
    if value.identity_status is IdentityResolutionStatus.RESOLVED:
        if not value.normalized_intervention_id:
            raise TriageRankingError("resolved identity requires normalized_intervention_id")
    elif value.deep_identity_eligible:
        raise TriageRankingError("unresolved identity cannot be deep_identity_eligible")

    endpoints = {row.endpoint_id: row for row in value.endpoints}
    if len(endpoints) != len(value.endpoints) or not endpoints:
        raise TriageRankingError("endpoint features must be nonempty and unique")
    if value.primary_endpoint_id not in endpoints:
        raise TriageRankingError("primary endpoint must be one retained endpoint assessment")
    if tuple(sorted(endpoints)) != tuple(row.endpoint_id for row in value.endpoints):
        raise TriageRankingError("endpoint features must be in canonical endpoint-ID order")
    for endpoint in value.endpoints:
        if not isinstance(endpoint.deep_status, EndpointDeepStatus):
            raise TriageRankingError("endpoint deep status is invalid")
        if endpoint.claim_ids != _strings(endpoint.claim_ids, "endpoint claim ID"):
            raise TriageRankingError("endpoint claim IDs must be unique and canonical")
        if endpoint.required is True and endpoint.deep_status is EndpointDeepStatus.NOT_ASSESSED:
            raise TriageRankingError("required endpoint cannot be not_assessed")
        if endpoint.deep_status is EndpointDeepStatus.ASSESSED and not endpoint.claim_ids:
            raise TriageRankingError("assessed endpoint requires at least one claim")

    evidence = {row.evidence_record_id: row for row in value.evidence}
    if len(evidence) != len(value.evidence):
        raise TriageRankingError("evidence record IDs must be unique")
    if tuple(sorted(evidence)) != tuple(row.evidence_record_id for row in value.evidence):
        raise TriageRankingError("evidence records must be in canonical order")
    claim_ids = {claim_id for endpoint in value.endpoints for claim_id in endpoint.claim_ids}
    for record in value.evidence:
        if record.endpoint_id not in endpoints:
            raise TriageRankingError("evidence refers to an endpoint outside the retained portfolio")
        if record.claim_id not in claim_ids:
            raise TriageRankingError("evidence claim is absent from endpoint assessments")
        _text(record.source_id, "evidence source ID")
        _text(record.evidence_span_id, "evidence span ID")
        if not isinstance(record.polarity, ClaimPolarity) or not isinstance(record.study_design, StudyDesign):
            raise TriageRankingError("evidence controlled values are malformed")

    ancestry = {row.evidence_record_id: row for row in value.ancestry}
    if len(ancestry) != len(value.ancestry) or set(ancestry) != set(evidence):
        raise TriageRankingError("every evidence record needs exactly one ancestry record")
    for record_id, row in ancestry.items():
        for field_name in (
            "source_ids",
            "cohort_ids",
            "laboratory_ids",
            "dataset_ids",
            "common_ancestry_ids",
        ):
            raw = getattr(row, field_name)
            if raw != _strings(raw, f"ancestry {field_name}"):
                raise TriageRankingError(f"ancestry {field_name} must be canonical")
        if evidence[record_id].source_id not in row.source_ids:
            raise TriageRankingError("ancestry source IDs must include the evidence source")

    route_ids: set[str] = set()
    for route in value.routes:
        if route.route_id in route_ids:
            raise TriageRankingError("mechanistic route IDs must be unique")
        route_ids.add(route.route_id)
        if route.endpoint_id not in endpoints:
            raise TriageRankingError("mechanistic route refers to an unknown endpoint")
        if route.evidence_ids != _strings(route.evidence_ids, "route evidence ID", required=True):
            raise TriageRankingError("route evidence IDs must be canonical and nonempty")
    if tuple(sorted(route_ids)) != tuple(row.route_id for row in value.routes):
        raise TriageRankingError("mechanistic routes must be in canonical order")

    source_record_ids: set[str] = set()
    span_ids: set[str] = set()
    for record in value.exposure:
        if record.candidate_id != value.candidate_id:
            raise TriageRankingError("exposure candidate link mismatch")
        if value.normalized_intervention_id and record.exact_intervention_id != value.normalized_intervention_id:
            raise TriageRankingError("exposure exact intervention differs from candidate identity")
        _validate_reported_quantity(record.dose, "exposure dose")
        _validate_reported_quantity(record.achieved_concentration, "achieved concentration")
        _validate_reported_quantity(record.required_effect_concentration, "required effect concentration")
        _text(record.administration_route, "exposure administration route")
        _text(record.duration, "exposure duration")
        _text(record.population, "exposure population")
        _text(record.target_tissue, "exposure target tissue")
        if record.source_record_ids != _strings(record.source_record_ids, "exposure source record ID", required=True):
            raise TriageRankingError("exposure source IDs must be canonical")
        if record.evidence_span_ids != _strings(record.evidence_span_ids, "exposure span ID", required=True):
            raise TriageRankingError("exposure span IDs must be canonical")
        source_record_ids.update(record.source_record_ids)
        span_ids.update(record.evidence_span_ids)
        if record.exposure_record_id != _stable_id("EXPOSURE", _without_field(record, "exposure_record_id")):
            raise TriageRankingError("exposure content-derived ID is invalid")
    if len({row.exposure_record_id for row in value.exposure}) != len(value.exposure):
        raise TriageRankingError("exposure record IDs must be unique")

    for record in value.safety:
        if record.candidate_id != value.candidate_id:
            raise TriageRankingError("safety candidate link mismatch")
        if value.normalized_intervention_id and record.exact_intervention_id != value.normalized_intervention_id:
            raise TriageRankingError("safety exact intervention differs from candidate identity")
        _validate_reported_quantity(record.dose, "safety dose")
        for name in ("administration_route", "duration", "population", "reversibility", "finding_code"):
            _text(getattr(record, name), f"safety {name}")
        if record.source_record_ids != _strings(record.source_record_ids, "safety source record ID", required=True):
            raise TriageRankingError("safety source IDs must be canonical")
        if record.evidence_span_ids != _strings(record.evidence_span_ids, "safety span ID", required=True):
            raise TriageRankingError("safety span IDs must be canonical")
        source_record_ids.update(record.source_record_ids)
        span_ids.update(record.evidence_span_ids)
        if record.safety_record_id != _stable_id("SAFETY", _without_field(record, "safety_record_id")):
            raise TriageRankingError("safety content-derived ID is invalid")
    if len({row.safety_record_id for row in value.safety}) != len(value.safety):
        raise TriageRankingError("safety record IDs must be unique")

    landscape = value.literature_landscape
    for count, label in (
        (landscape.direct_target_disease_publication_count, "publication count"),
        (landscape.direct_target_disease_trial_count, "trial count"),
    ):
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise TriageRankingError(f"{label} must be a nonnegative integer or unknown")
    if landscape.earliest_direct_evidence_year is not None and not 1800 <= landscape.earliest_direct_evidence_year <= 2200:
        raise TriageRankingError("earliest evidence year is invalid")
    if landscape.source_record_ids != _strings(landscape.source_record_ids, "landscape source ID"):
        raise TriageRankingError("landscape source IDs must be canonical")

    if value.formulation_routes != _strings((_route_token(item) for item in value.formulation_routes), "formulation route"):
        raise TriageRankingError("formulation routes must be normalized and canonical")
    if value.allowed_routes != _strings((_route_token(item) for item in value.allowed_routes), "allowed route"):
        raise TriageRankingError("allowed routes must be normalized and canonical")
    if value.excluded_routes != _strings((_route_token(item) for item in value.excluded_routes), "excluded route"):
        raise TriageRankingError("excluded routes must be normalized and canonical")
    if set(value.allowed_routes).intersection(value.excluded_routes):
        raise TriageRankingError("a route cannot be both allowed and excluded")
    if value.explicit_uncertainties != _strings(value.explicit_uncertainties, "explicit uncertainty"):
        raise TriageRankingError("explicit uncertainties must be canonical")

    expert_ids: set[str] = set()
    for assessment in value.expert_assessments:
        if assessment.assessment_id in expert_ids:
            raise TriageRankingError("expert assessment IDs must be unique")
        expert_ids.add(assessment.assessment_id)
        if assessment.endpoint_id is not None and assessment.endpoint_id not in endpoints:
            raise TriageRankingError("expert assessment endpoint is outside the case portfolio")
        _validate_expert_assessment(assessment, set(evidence))


def derive_independence_clusters(value: CandidateEvidenceInput) -> tuple[EvidenceIndependenceCluster, ...]:
    """Conservatively cluster records sharing source, cohort, lab, dataset, or ancestry."""

    validate_candidate_evidence_input(value)
    ancestry = {row.evidence_record_id: row for row in value.ancestry}
    record_ids = sorted(ancestry)
    parent = {record_id: record_id for record_id in record_ids}

    def find(record_id: str) -> str:
        while parent[record_id] != record_id:
            parent[record_id] = parent[parent[record_id]]
            record_id = parent[record_id]
        return record_id

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parent[second] = first

    indexes: dict[tuple[str, str], str] = {}
    for record_id in record_ids:
        row = ancestry[record_id]
        dimensions = {
            "source": row.source_ids,
            "cohort": row.cohort_ids,
            "laboratory": row.laboratory_ids,
            "dataset": row.dataset_ids,
            "ancestry": row.common_ancestry_ids,
        }
        for dimension, identifiers in dimensions.items():
            for identifier in identifiers:
                key = (dimension, identifier)
                if key in indexes:
                    union(record_id, indexes[key])
                else:
                    indexes[key] = record_id

    groups: dict[str, list[str]] = {}
    for record_id in record_ids:
        groups.setdefault(find(record_id), []).append(record_id)
    clusters: list[EvidenceIndependenceCluster] = []
    for members in groups.values():
        member_ids = tuple(sorted(members))

        def shared(field_name: str) -> tuple[str, ...]:
            counts: dict[str, int] = {}
            for member in member_ids:
                for identifier in getattr(ancestry[member], field_name):
                    counts[identifier] = counts.get(identifier, 0) + 1
            return tuple(sorted(identifier for identifier, count in counts.items() if count > 1))

        body = {
            "evidence_record_ids": member_ids,
            "shared_source_ids": shared("source_ids"),
            "shared_cohort_ids": shared("cohort_ids"),
            "shared_laboratory_ids": shared("laboratory_ids"),
            "shared_dataset_ids": shared("dataset_ids"),
            "shared_ancestry_ids": shared("common_ancestry_ids"),
        }
        clusters.append(
            EvidenceIndependenceCluster(
                cluster_id=_stable_id("EVIDENCE-CLUSTER", body),
                **body,
            )
        )
    return tuple(sorted(clusters, key=lambda row: row.cluster_id))


def _signal(record: EvidenceFeature) -> str:
    if record.polarity is ClaimPolarity.SUPPORTS:
        if record.effect_direction in {ObservedEffectDirection.HARM, ObservedEffectDirection.NO_EFFECT}:
            return "mixed"
        if record.effect_direction in {ObservedEffectDirection.MIXED, ObservedEffectDirection.UNCLEAR}:
            return "mixed"
        return "support"
    if record.polarity is ClaimPolarity.REFUTES:
        return "refute"
    if record.polarity is ClaimPolarity.NULL:
        return "null"
    if record.polarity is ClaimPolarity.MIXED:
        return "mixed"
    return "context"


def _cluster_signal(records: Iterable[EvidenceFeature]) -> str:
    signals = {_signal(record) for record in records}
    if "mixed" in signals or ({"support", "refute"}.issubset(signals)):
        return "mixed"
    if "support" in signals:
        return "support"
    if "refute" in signals:
        return "refute"
    if "null" in signals:
        return "null"
    return "context"


_HUMAN_INTERVENTIONAL_DESIGNS = {
    StudyDesign.RANDOMIZED_CONTROLLED_TRIAL,
    StudyDesign.NONRANDOMIZED_INTERVENTIONAL,
}
_HUMAN_OBSERVATIONAL_DESIGNS = {
    StudyDesign.COHORT,
    StudyDesign.CASE_CONTROL,
    StudyDesign.CROSS_SECTIONAL,
    StudyDesign.CASE_SERIES,
    StudyDesign.CASE_REPORT,
}


def _is_human_clinical(record: EvidenceFeature) -> bool:
    return (
        record.model_kind is ExperimentalModelKind.HUMAN
        and record.study_design in _HUMAN_INTERVENTIONAL_DESIGNS | _HUMAN_OBSERVATIONAL_DESIGNS
    )


def _is_human_derived_model(record: EvidenceFeature) -> bool:
    if record.model_kind is ExperimentalModelKind.HUMAN_PATIENT_CELL:
        return True
    if record.model_kind in {
        ExperimentalModelKind.ORGANOID,
        ExperimentalModelKind.EX_VIVO,
    }:
        return _route_token(record.species) in {"human", "homo sapiens"}
    return False


def _endpoint_cluster_summary(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    clusters: tuple[EvidenceIndependenceCluster, ...],
    *,
    predicate: Any | None = None,
) -> tuple[dict[str, int], tuple[str, ...]]:
    by_id = {record.evidence_record_id: record for record in value.evidence}
    summary = {"support": 0, "refute": 0, "null": 0, "mixed": 0, "context": 0}
    used: set[str] = set()
    for cluster in clusters:
        records = [
            by_id[record_id]
            for record_id in cluster.evidence_record_ids
            if by_id[record_id].endpoint_id == endpoint_id
            and (predicate is None or predicate(by_id[record_id]))
        ]
        if not records:
            continue
        summary[_cluster_signal(records)] += 1
        used.update(record.evidence_record_id for record in records)
    return summary, tuple(sorted(used))


def _therapeutic_support(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    clusters: tuple[EvidenceIndependenceCluster, ...],
) -> DimensionAssessment:
    summary, used = _endpoint_cluster_summary(endpoint_id, value, clusters)
    clinical, _ = _endpoint_cluster_summary(endpoint_id, value, clusters, predicate=_is_human_clinical)
    has_conflict = summary["mixed"] > 0 or (summary["support"] > 0 and summary["refute"] > 0)
    features = {
        "supporting_independent_clusters": summary["support"],
        "refuting_independent_clusters": summary["refute"],
        "null_independent_clusters": summary["null"],
        "mixed_independent_clusters": summary["mixed"],
        "supporting_human_clinical_clusters": clinical["support"],
    }
    if has_conflict:
        return _assessment(Dimension.THERAPEUTIC_SUPPORT, "conflicting", "TS-01", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)
    if summary["support"] >= 2 and clinical["support"] >= 1 and summary["refute"] == 0:
        return _assessment(Dimension.THERAPEUTIC_SUPPORT, "strong", "TS-02", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)
    if (summary["support"] >= 2 or clinical["support"] >= 1) and summary["refute"] == 0:
        return _assessment(Dimension.THERAPEUTIC_SUPPORT, "moderate", "TS-03", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)
    if summary["support"] == 1 and summary["refute"] == 0:
        return _assessment(Dimension.THERAPEUTIC_SUPPORT, "limited", "TS-04", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)
    if summary["refute"] or summary["null"]:
        return _assessment(Dimension.THERAPEUTIC_SUPPORT, "refuted_or_null", "TS-05", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)
    return _assessment(Dimension.THERAPEUTIC_SUPPORT, "insufficient", "TS-06", endpoint_id=endpoint_id, features=features, evidence_record_ids=used)


def _evidence_quality(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    clusters: tuple[EvidenceIndependenceCluster, ...],
) -> DimensionAssessment:
    endpoint_records = [record for record in value.evidence if record.endpoint_id == endpoint_id and _signal(record) == "support"]
    summary, used = _endpoint_cluster_summary(endpoint_id, value, clusters)
    low_bias_rct = any(
        record.study_design is StudyDesign.RANDOMIZED_CONTROLLED_TRIAL
        and record.model_kind is ExperimentalModelKind.HUMAN
        and record.risk_of_bias is RiskOfBiasLevel.LOW
        and record.calibration in {ClaimCalibration.ESTABLISHED, ClaimCalibration.SUPPORTED_WITH_QUALIFIER}
        for record in endpoint_records
    )
    acceptable_human = any(
        _is_human_clinical(record)
        and record.risk_of_bias in {RiskOfBiasLevel.LOW, RiskOfBiasLevel.SOME_CONCERNS}
        and record.calibration in {ClaimCalibration.ESTABLISHED, ClaimCalibration.SUPPORTED_WITH_QUALIFIER}
        for record in endpoint_records
    )
    model_summary, _ = _endpoint_cluster_summary(endpoint_id, value, clusters, predicate=_is_human_derived_model)
    high_bias_or_speculative = bool(endpoint_records) and all(
        record.risk_of_bias in {RiskOfBiasLevel.HIGH, RiskOfBiasLevel.UNCLEAR, RiskOfBiasLevel.NOT_ASSESSED}
        or record.calibration in {
            ClaimCalibration.SPECULATIVE,
            ClaimCalibration.PLAUSIBLE_INFERENCE,
            ClaimCalibration.UNRESOLVED,
        }
        for record in endpoint_records
    )
    ancestry_missing = any(
        not (row.cohort_ids or row.laboratory_ids or row.dataset_ids or row.common_ancestry_ids)
        for row in value.ancestry
        if row.evidence_record_id in {record.evidence_record_id for record in endpoint_records}
    )
    features = {
        "supporting_independent_clusters": summary["support"],
        "low_bias_randomized_human_support": low_bias_rct,
        "acceptable_human_support": acceptable_human,
        "supporting_human_derived_clusters": model_summary["support"],
        "all_support_high_bias_or_speculative": high_bias_or_speculative,
        "ancestry_metadata_incomplete": ancestry_missing,
    }
    if low_bias_rct and summary["support"] >= 2:
        band, rule = "high", "EQ-01"
    elif acceptable_human or model_summary["support"] >= 2:
        band, rule = "moderate", "EQ-02"
    elif endpoint_records and not high_bias_or_speculative and not ancestry_missing:
        band, rule = "low", "EQ-03"
    elif endpoint_records:
        band, rule = "very_low", "EQ-04"
    else:
        band, rule = "insufficient", "EQ-05"
    return _assessment(Dimension.EVIDENCE_QUALITY, band, rule, endpoint_id=endpoint_id, features=features, evidence_record_ids=used)


def _mechanistic_coherence(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    support: DimensionAssessment,
) -> DimensionAssessment:
    routes = [route for route in value.routes if route.endpoint_id == endpoint_id]
    known = sum(1 for route in routes if route.direction_known)
    evidence_ids = sorted({identifier for route in routes for identifier in route.evidence_ids})
    features = {
        "structured_route_count": len(routes),
        "known_direction_route_count": known,
        "therapeutic_support_band": support.band,
    }
    if routes and support.band == "conflicting":
        band, rule = "mixed", "MC-01"
    elif routes and known and support.band in {"strong", "moderate", "limited"}:
        band, rule = "coherent", "MC-02"
    elif routes and support.band not in {"refuted_or_null"}:
        band, rule = "plausible", "MC-03"
    elif routes:
        band, rule = "incoherent", "MC-04"
    else:
        band, rule = "unknown", "MC-05"
    return _assessment(Dimension.MECHANISTIC_COHERENCE, band, rule, endpoint_id=endpoint_id, features=features, evidence_record_ids=evidence_ids)


def _human_clinical_evidence(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    clusters: tuple[EvidenceIndependenceCluster, ...],
) -> DimensionAssessment:
    summary, used = _endpoint_cluster_summary(endpoint_id, value, clusters, predicate=_is_human_clinical)
    interventional_support = any(
        record.evidence_record_id in used
        and _signal(record) == "support"
        and record.study_design in _HUMAN_INTERVENTIONAL_DESIGNS
        for record in value.evidence
    )
    observational_support = any(
        record.evidence_record_id in used
        and _signal(record) == "support"
        and record.study_design in _HUMAN_OBSERVATIONAL_DESIGNS
        for record in value.evidence
    )
    features = {
        "supporting_clusters": summary["support"],
        "refuting_clusters": summary["refute"],
        "null_clusters": summary["null"],
        "mixed_clusters": summary["mixed"],
        "interventional_support": interventional_support,
        "observational_support": observational_support,
    }
    if summary["mixed"] or (summary["support"] and (summary["refute"] or summary["null"])):
        band, rule = "mixed", "HC-01"
    elif interventional_support:
        band, rule = "direct_interventional", "HC-02"
    elif observational_support:
        band, rule = "supportive_observational", "HC-03"
    elif summary["refute"] or summary["null"]:
        band, rule = "negative_or_null", "HC-04"
    else:
        band, rule = "absent", "HC-05"
    return _assessment(Dimension.HUMAN_CLINICAL_EVIDENCE, band, rule, endpoint_id=endpoint_id, features=features, evidence_record_ids=used)


def _human_derived_model_evidence(
    endpoint_id: str,
    value: CandidateEvidenceInput,
    clusters: tuple[EvidenceIndependenceCluster, ...],
) -> DimensionAssessment:
    summary, used = _endpoint_cluster_summary(endpoint_id, value, clusters, predicate=_is_human_derived_model)
    features = {
        "supporting_clusters": summary["support"],
        "refuting_clusters": summary["refute"],
        "null_clusters": summary["null"],
        "mixed_clusters": summary["mixed"],
    }
    if summary["mixed"] or (summary["support"] and (summary["refute"] or summary["null"])):
        band, rule = "mixed", "HM-01"
    elif summary["support"] >= 2:
        band, rule = "replicated", "HM-02"
    elif summary["support"] == 1:
        band, rule = "single_context", "HM-03"
    elif summary["refute"] or summary["null"]:
        band, rule = "negative_or_null", "HM-04"
    else:
        band, rule = "absent", "HM-05"
    return _assessment(Dimension.HUMAN_DERIVED_MODEL_EVIDENCE, band, rule, endpoint_id=endpoint_id, features=features, evidence_record_ids=used)


def _endpoint_specificity(
    endpoint: EndpointFeature,
    *,
    primary_endpoint_id: str,
    evidence: Iterable[EvidenceFeature],
) -> DimensionAssessment:
    records = [record for record in evidence if record.endpoint_id == endpoint.endpoint_id and _signal(record) != "context"]
    used = [record.evidence_record_id for record in records]
    direct_types = {
        EndpointType.CLINICAL_OUTCOME,
        EndpointType.FUNCTIONAL_OUTCOME,
        EndpointType.SYMPTOM_OUTCOME,
        EndpointType.SAFETY_OUTCOME,
        EndpointType.COMPOSITE,
    }
    linked = bool(
        set(endpoint.relationship_types).intersection(
            {EndpointRelationshipType.SURROGATE_FOR, EndpointRelationshipType.SUPPORTS}
        )
    )
    features = {
        "directional_record_count": len(records),
        "endpoint_type": endpoint.endpoint_type.value if endpoint.endpoint_type else "unknown",
        "endpoint_role": endpoint.role.value if endpoint.role else "unknown",
        "primary": endpoint.endpoint_id == primary_endpoint_id,
        "linked_to_other_endpoint": linked,
    }
    if records and endpoint.endpoint_type in direct_types and endpoint.endpoint_id == primary_endpoint_id:
        band, rule = "direct_primary", "ES-01"
    elif records and endpoint.endpoint_type in direct_types:
        band, rule = "direct_secondary", "ES-02"
    elif records and endpoint.endpoint_type in {EndpointType.BIOMARKER, EndpointType.SURROGATE} and linked:
        band, rule = "surrogate_linked", "ES-03"
    elif records:
        band, rule = "nonspecific", "ES-04"
    else:
        band, rule = "unknown", "ES-05"
    return _assessment(Dimension.ENDPOINT_SPECIFICITY, band, rule, endpoint_id=endpoint.endpoint_id, features=features, evidence_record_ids=used)


_CONCENTRATION_TO_MOLAR: Mapping[str, Decimal] = {
    "M": Decimal("1"),
    "mM": Decimal("1e-3"),
    "uM": Decimal("1e-6"),
    "µM": Decimal("1e-6"),
    "μM": Decimal("1e-6"),
    "nM": Decimal("1e-9"),
    "pM": Decimal("1e-12"),
}


def _concentration_molar(value: ReportedQuantity) -> Decimal | None:
    if value.status is not ReportedValueStatus.REPORTED or value.value is None or value.unit is None:
        return None
    if value.unit not in _CONCENTRATION_TO_MOLAR:
        return None
    try:
        parsed = Decimal(value.value)
    except InvalidOperation as exc:
        raise TriageRankingError(f"invalid numeric concentration: {value.value}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise TriageRankingError("concentration must be finite and nonnegative")
    return parsed * _CONCENTRATION_TO_MOLAR[value.unit]


def _route_compatible(value: CandidateEvidenceInput, route: str) -> bool | None:
    normalized = _route_token(route)
    if normalized in set(value.excluded_routes):
        return False
    if value.route_constraints_known and value.allowed_routes:
        return normalized in set(value.allowed_routes)
    if value.route_constraints_known:
        return True
    return None


def _exposure_record_outcome(value: CandidateEvidenceInput, record: ExposureEvidence) -> tuple[str, str]:
    if record.dose.status is not ReportedValueStatus.REPORTED:
        return "unknown", "dose_not_reported"
    if record.dose_context is DoseContext.EXCEEDS_TOLERATED:
        return "infeasible", "dose_exceeds_tolerated"
    route = _route_compatible(value, record.administration_route)
    if route is False:
        return "infeasible", "route_incompatible"
    if record.tissue_applicability is TissueApplicability.MISMATCHED:
        return "infeasible", "target_tissue_mismatch"
    if record.dose_context in {DoseContext.PRECLINICAL_ONLY, DoseContext.UNKNOWN}:
        return "unknown", "dose_context_not_clinically_attainable"
    if record.pk_basis not in {PharmacokineticBasis.MEASURED_HUMAN, PharmacokineticBasis.MODELED_HUMAN}:
        return "unknown", "human_pk_missing"
    if record.tissue_applicability is TissueApplicability.UNKNOWN:
        return "unknown", "tissue_applicability_unknown"
    achieved = _concentration_molar(record.achieved_concentration)
    required = _concentration_molar(record.required_effect_concentration)
    if achieved is None or required is None or required == 0:
        return "unknown", "concentration_margin_unavailable"
    margin = achieved / required
    if margin >= Decimal("3"):
        return "feasible", f"margin={format(margin.normalize(), 'f')}"
    if margin >= Decimal("1"):
        return "borderline", f"margin={format(margin.normalize(), 'f')}"
    return "infeasible", f"margin={format(margin.normalize(), 'f')}"


def _exposure_feasibility(value: CandidateEvidenceInput) -> DimensionAssessment:
    outcomes = [_exposure_record_outcome(value, record) for record in value.exposure]
    outcome_values = {outcome for outcome, _ in outcomes}
    features = {
        "record_count": len(outcomes),
        "feasible_records": sum(outcome == "feasible" for outcome, _ in outcomes),
        "borderline_records": sum(outcome == "borderline" for outcome, _ in outcomes),
        "infeasible_records": sum(outcome == "infeasible" for outcome, _ in outcomes),
        "unknown_records": sum(outcome == "unknown" for outcome, _ in outcomes),
        "record_decisions": "|".join(f"{outcome}:{reason}" for outcome, reason in sorted(outcomes)),
    }
    if "infeasible" in outcome_values and outcome_values.intersection({"feasible", "borderline"}):
        band, rule = "conflicting", "EX-01"
    elif "feasible" in outcome_values:
        band, rule = "feasible", "EX-02"
    elif "borderline" in outcome_values and "infeasible" not in outcome_values:
        band, rule = "borderline", "EX-03"
    elif "infeasible" in outcome_values:
        band, rule = "infeasible", "EX-04"
    else:
        band, rule = "unknown", "EX-05"
    evidence_ids = {identifier for record in value.exposure for identifier in record.evidence_span_ids}
    return _assessment(Dimension.EXPOSURE_FEASIBILITY, band, rule, features=features, evidence_record_ids=evidence_ids)


def _material_safety_risk(record: SafetyEvidence) -> bool:
    return (
        record.finding is SafetyFinding.RISK
        and record.case_applicability in {CaseApplicability.DIRECT, CaseApplicability.PARTIAL}
        and record.severity in {
            SafetySeverity.SERIOUS,
            SafetySeverity.LIFE_THREATENING,
            SafetySeverity.FATAL,
        }
        and record.causality in {
            SafetyCausality.ESTABLISHED,
            SafetyCausality.PROBABLE,
            SafetyCausality.POSSIBLE,
        }
    )


def _safety_and_tolerability(value: CandidateEvidenceInput) -> DimensionAssessment:
    material = [record for record in value.safety if _material_safety_risk(record)]
    manageable = [
        record
        for record in value.safety
        if record.finding is SafetyFinding.RISK
        and record.case_applicability in {CaseApplicability.DIRECT, CaseApplicability.PARTIAL}
        and record.severity is SafetySeverity.NON_SERIOUS
        and record.causality not in {SafetyCausality.UNRELATED, SafetyCausality.UNLIKELY}
    ]
    permissive = [
        record
        for record in value.safety
        if record.finding is SafetyFinding.NO_MATERIAL_RISK
        and record.case_applicability is CaseApplicability.DIRECT
    ]
    features = {
        "record_count": len(value.safety),
        "applicable_serious_risk_records": len(material),
        "applicable_non_serious_risk_records": len(manageable),
        "direct_permissive_records": len(permissive),
        "adverse_event_records": sum(row.evidence_kind is SafetyEvidenceKind.ADVERSE_EVENT for row in value.safety),
        "contraindication_records": sum(row.evidence_kind is SafetyEvidenceKind.CONTRAINDICATION for row in value.safety),
        "interaction_records": sum(row.evidence_kind is SafetyEvidenceKind.INTERACTION for row in value.safety),
        "population_risk_records": sum(row.evidence_kind is SafetyEvidenceKind.POPULATION_RISK for row in value.safety),
    }
    if material and permissive:
        band, rule = "conflicting", "SA-01"
    elif material:
        band, rule = "serious_mismatch", "SA-02"
    elif manageable:
        band, rule = "manageable", "SA-03"
    elif permissive:
        band, rule = "acceptable", "SA-04"
    else:
        band, rule = "unknown", "SA-05"
    evidence_ids = {identifier for record in value.safety for identifier in record.evidence_span_ids}
    return _assessment(Dimension.SAFETY_AND_TOLERABILITY, band, rule, features=features, evidence_record_ids=evidence_ids)


def _repurposing_readiness(value: CandidateEvidenceInput) -> DimensionAssessment:
    human = set(value.human_use_statuses)
    development = set(value.development_statuses)
    target_status = value.literature_landscape.development_in_target_disease
    features = {
        "human_use_statuses": "|".join(sorted(row.value for row in human)) or "none",
        "development_statuses": "|".join(sorted(row.value for row in development)) or "none",
        "target_disease_development": target_status.value,
        "formulation_route_count": len(value.formulation_routes),
    }
    if development.intersection({DevelopmentStatus.FAILED, DevelopmentStatus.WITHDRAWN, DevelopmentStatus.DISCONTINUED}):
        band, rule = "blocked_or_withdrawn", "RR-01"
    elif (
        HumanUseStatus.MARKETED_HUMAN_PRODUCT in human
        and target_status not in {TargetDiseaseDevelopment.APPROVED, TargetDiseaseDevelopment.CLINICAL}
    ):
        band, rule = "marketed_repurposing_ready", "RR-02"
    elif HumanUseStatus.ADMINISTERED_IN_HUMANS in human:
        band, rule = "human_experience", "RR-03"
    elif development.intersection(
        {
            DevelopmentStatus.INVESTIGATIONAL,
            DevelopmentStatus.CLINICAL_STAGE,
            DevelopmentStatus.PHASE_1,
            DevelopmentStatus.PHASE_2,
            DevelopmentStatus.PHASE_3,
        }
    ):
        band, rule = "clinical_asset", "RR-04"
    elif DevelopmentStatus.PRECLINICAL in development or HumanUseStatus.NO_DOCUMENTED_HUMAN_USE in human:
        band, rule = "preclinical", "RR-05"
    else:
        band, rule = "unknown", "RR-06"
    sources = value.literature_landscape.source_record_ids
    return _assessment(Dimension.REPURPOSING_READINESS, band, rule, features=features, evidence_record_ids=sources)


def _novelty_underexploration(value: CandidateEvidenceInput) -> DimensionAssessment:
    landscape = value.literature_landscape
    publications = landscape.direct_target_disease_publication_count
    trials = landscape.direct_target_disease_trial_count
    status = landscape.development_in_target_disease
    features = {
        "direct_target_disease_publication_count": publications if publications is not None else "unknown",
        "direct_target_disease_trial_count": trials if trials is not None else "unknown",
        "development_in_target_disease": status.value,
        "earliest_direct_evidence_year": landscape.earliest_direct_evidence_year or "unknown",
        "publication_count_used_for_therapeutic_support": False,
        "publication_count_used_for_independence": False,
    }
    if publications is None or trials is None or status is TargetDiseaseDevelopment.UNKNOWN:
        band, rule = "unknown", "NV-05"
    elif status is TargetDiseaseDevelopment.NONE_FOUND and publications <= 1 and trials == 0:
        band, rule = "novel_hypothesis", "NV-01"
    elif status is TargetDiseaseDevelopment.NONE_FOUND and publications <= 5 and trials == 0:
        band, rule = "underexplored", "NV-02"
    elif status is TargetDiseaseDevelopment.PRECLINICAL_ONLY or publications <= 20:
        band, rule = "emerging", "NV-03"
    else:
        band, rule = "established", "NV-04"
    return _assessment(
        Dimension.NOVELTY_UNDEREXPLORATION,
        band,
        rule,
        features=features,
        evidence_record_ids=landscape.source_record_ids,
    )


def _clinical_translatability(
    value: CandidateEvidenceInput,
    clinical: DimensionAssessment,
    exposure: DimensionAssessment,
    safety: DimensionAssessment,
    readiness: DimensionAssessment,
) -> DimensionAssessment:
    features = {
        "identity_resolved": value.identity_status is IdentityResolutionStatus.RESOLVED,
        "deep_identity_eligible": value.deep_identity_eligible,
        "human_clinical_band": clinical.band,
        "exposure_band": exposure.band,
        "safety_band": safety.band,
        "readiness_band": readiness.band,
        "route_constraints_known": value.route_constraints_known,
    }
    if (
        not value.deep_identity_eligible
        or exposure.band == "infeasible"
        or safety.band == "serious_mismatch"
        or readiness.band == "blocked_or_withdrawn"
    ):
        band, rule = "blocked", "CT-01"
    elif (
        clinical.band in {"direct_interventional", "supportive_observational"}
        and exposure.band in {"feasible", "borderline"}
        and safety.band in {"acceptable", "manageable"}
        and readiness.band in {"marketed_repurposing_ready", "human_experience", "clinical_asset"}
    ):
        band, rule = "high", "CT-02"
    elif (
        readiness.band in {"marketed_repurposing_ready", "human_experience", "clinical_asset"}
        and exposure.band != "infeasible"
        and safety.band != "serious_mismatch"
    ):
        band, rule = "moderate", "CT-03"
    elif value.deep_identity_eligible and readiness.band in {"preclinical", "unknown"}:
        band, rule = "low", "CT-04"
    else:
        band, rule = "unknown", "CT-05"
    evidence_ids = set(clinical.evidence_record_ids) | set(exposure.evidence_record_ids) | set(safety.evidence_record_ids)
    return _assessment(Dimension.CLINICAL_TRANSLATABILITY, band, rule, features=features, evidence_record_ids=evidence_ids)


def _uncertainty(
    value: CandidateEvidenceInput,
    support: DimensionAssessment,
    quality: DimensionAssessment,
    mechanism: DimensionAssessment,
    exposure: DimensionAssessment,
    safety: DimensionAssessment,
    clusters: tuple[EvidenceIndependenceCluster, ...],
) -> DimensionAssessment:
    ancestry_incomplete = any(
        not (row.cohort_ids or row.laboratory_ids or row.dataset_ids or row.common_ancestry_ids)
        for row in value.ancestry
    )
    decision_blocking = (
        value.identity_status in {
            IdentityResolutionStatus.UNRESOLVED,
            IdentityResolutionStatus.CONFLICTING,
            IdentityResolutionStatus.QUARANTINED,
        }
        or support.band == "conflicting"
        or exposure.band in {"conflicting", "infeasible"}
        or safety.band in {"conflicting", "serious_mismatch"}
    )
    high = (
        ancestry_incomplete
        or exposure.band == "unknown"
        or safety.band == "unknown"
        or quality.band in {"very_low", "insufficient"}
        or mechanism.band in {"mixed", "unknown"}
        or bool(value.explicit_uncertainties)
    )
    moderate = quality.band == "low" or mechanism.band == "plausible" or support.band == "limited"
    features = {
        "identity_status": value.identity_status.value,
        "therapeutic_support_band": support.band,
        "evidence_quality_band": quality.band,
        "mechanistic_coherence_band": mechanism.band,
        "exposure_band": exposure.band,
        "safety_band": safety.band,
        "independent_cluster_count": len(clusters),
        "ancestry_metadata_incomplete": ancestry_incomplete,
        "explicit_uncertainty_count": len(value.explicit_uncertainties),
    }
    if decision_blocking:
        band, rule = "decision_blocking", "UN-01"
    elif high:
        band, rule = "high", "UN-02"
    elif moderate:
        band, rule = "moderate", "UN-03"
    else:
        band, rule = "low", "UN-04"
    evidence_ids = {record.evidence_record_id for record in value.evidence}
    return _assessment(Dimension.UNCERTAINTY, band, rule, features=features, evidence_record_ids=evidence_ids)


def _information_value(
    support: DimensionAssessment,
    mechanism: DimensionAssessment,
    exposure: DimensionAssessment,
    safety: DimensionAssessment,
    novelty: DimensionAssessment,
    uncertainty: DimensionAssessment,
) -> DimensionAssessment:
    hard_block = exposure.band == "infeasible" or safety.band == "serious_mismatch"
    plausible_signal = support.band in {"strong", "moderate", "limited", "conflicting"} or mechanism.band in {
        "coherent",
        "plausible",
        "mixed",
    }
    underexplored = novelty.band in {"novel_hypothesis", "underexplored"}
    features = {
        "therapeutic_support_band": support.band,
        "mechanistic_coherence_band": mechanism.band,
        "novelty_band": novelty.band,
        "uncertainty_band": uncertainty.band,
        "hard_safety_or_exposure_block": hard_block,
        "plausible_signal": plausible_signal,
    }
    if hard_block or support.band == "refuted_or_null":
        band, rule = "not_actionable", "IV-01"
    elif underexplored and plausible_signal and uncertainty.band in {"high", "decision_blocking", "moderate"}:
        band, rule = "high", "IV-02"
    elif plausible_signal and uncertainty.band in {"high", "decision_blocking", "moderate"}:
        band, rule = "moderate", "IV-03"
    else:
        band, rule = "low", "IV-04"
    evidence_ids = set(support.evidence_record_ids) | set(mechanism.evidence_record_ids)
    return _assessment(Dimension.INFORMATION_VALUE, band, rule, features=features, evidence_record_ids=evidence_ids)


_EXPERT_ELIGIBLE_DIMENSIONS = {
    Dimension.MECHANISTIC_COHERENCE,
    Dimension.ENDPOINT_SPECIFICITY,
    Dimension.CLINICAL_TRANSLATABILITY,
    Dimension.NOVELTY_UNDEREXPLORATION,
    Dimension.INFORMATION_VALUE,
}


def _apply_expert_if_unknown(
    deterministic: DimensionAssessment,
    value: CandidateEvidenceInput,
) -> DimensionAssessment:
    """Use a validated cached assessment only for a deterministically unresolved band."""

    if deterministic.dimension not in _EXPERT_ELIGIBLE_DIMENSIONS:
        return deterministic
    if deterministic.band not in {"unknown", "insufficient"}:
        return deterministic
    matches = [
        row
        for row in value.expert_assessments
        if row.dimension is deterministic.dimension and row.endpoint_id == deterministic.endpoint_id
    ]
    if not matches:
        return deterministic
    if len(matches) != 1:
        raise TriageRankingError("an ambiguous field can have at most one current expert assessment")
    expert = matches[0]
    return _assessment(
        deterministic.dimension,
        expert.selected_band,
        "EXPERT-ASSESSMENT-01",
        endpoint_id=deterministic.endpoint_id,
        features={
            "deterministic_band_before_assessment": deterministic.band,
            "assessment_label": expert.label,
            "cache_key": expert.cache_key,
        },
        evidence_record_ids=expert.evidence_record_ids,
        assessment_kind=AssessmentKind.EXPERT_ASSESSMENT,
        expert_assessment_ids=(expert.assessment_id,),
    )


def _triage(
    value: CandidateEvidenceInput,
    *,
    support: DimensionAssessment,
    quality: DimensionAssessment,
    mechanism: DimensionAssessment,
    exposure: DimensionAssessment,
    safety: DimensionAssessment,
    uncertainty: DimensionAssessment,
    information: DimensionAssessment,
) -> TriageDecision:
    if value.scope_eligibility is ScopeEligibility.PROHIBITED:
        category, terminal, reason, rule = (
            TriageCategory.REJECTED_OR_QUARANTINED,
            TerminalDisposition.REJECTED,
            "prohibited_intervention_scope",
            "TR-01",
        )
    elif value.identity_status is IdentityResolutionStatus.QUARANTINED:
        category, terminal, reason, rule = (
            TriageCategory.REJECTED_OR_QUARANTINED,
            TerminalDisposition.QUARANTINED,
            "identity_quarantined",
            "TR-02",
        )
    elif value.identity_status in {IdentityResolutionStatus.UNRESOLVED, IdentityResolutionStatus.CONFLICTING} or not value.deep_identity_eligible:
        category, terminal, reason, rule = (
            TriageCategory.IDENTITY_FOLLOW_UP,
            TerminalDisposition.NOT_TERMINAL,
            "decision_relevant_identity_follow_up",
            "TR-03",
        )
    elif safety.band == "serious_mismatch" or exposure.band == "infeasible":
        category, terminal, reason, rule = (
            TriageCategory.REJECTED_OR_QUARANTINED,
            TerminalDisposition.REJECTED,
            "serious_safety_mismatch" if safety.band == "serious_mismatch" else "exposure_infeasible",
            "TR-04",
        )
    elif safety.band == "conflicting" or exposure.band == "conflicting":
        category, terminal, reason, rule = (
            TriageCategory.REJECTED_OR_QUARANTINED,
            TerminalDisposition.QUARANTINED,
            "conflicting_safety_or_exposure",
            "TR-05",
        )
    elif support.band == "conflicting" or uncertainty.band == "decision_blocking":
        category, terminal, reason, rule = (
            TriageCategory.EVIDENCE_FOLLOW_UP,
            TerminalDisposition.NOT_TERMINAL,
            "conflicting_or_decision_changing_evidence",
            "TR-06",
        )
    elif (
        support.band in {"strong", "moderate"}
        and quality.band in {"high", "moderate", "low"}
        and mechanism.band in {"coherent", "plausible"}
        and safety.band != "serious_mismatch"
        and exposure.band != "infeasible"
    ):
        category, terminal, reason, rule = (
            TriageCategory.DEEP_REVIEW,
            TerminalDisposition.NOT_TERMINAL,
            "evidence_supports_deep_review",
            "TR-07",
        )
    elif information.band in {"high", "moderate"}:
        category, terminal, reason, rule = (
            TriageCategory.EVIDENCE_FOLLOW_UP,
            TerminalDisposition.NOT_TERMINAL,
            "preserved_information_value",
            "TR-08",
        )
    else:
        category, terminal, reason, rule = (
            TriageCategory.DEFERRED_PRESERVED,
            TerminalDisposition.NOT_TERMINAL,
            "insufficient_current_support_preserved",
            "TR-09",
        )
    projection = {
        "candidate_id": value.candidate_id,
        "category": category,
        "terminal_disposition": terminal,
        "reason_code": reason,
        "decision_rule_id": rule,
        "primary_endpoint_id": value.primary_endpoint_id,
        "dimensions": {
            "support": support.band,
            "quality": quality.band,
            "mechanism": mechanism.band,
            "exposure": exposure.band,
            "safety": safety.band,
            "uncertainty": uncertainty.band,
            "information_value": information.band,
        },
    }
    evidence_ids = tuple(sorted({record.evidence_record_id for record in value.evidence}))
    return TriageDecision(
        disposition_id=_stable_id("TRIAGE", projection),
        candidate_id=value.candidate_id,
        category=category,
        terminal_disposition=terminal,
        reason_code=reason,
        decision_rule_id=rule,
        primary_endpoint_id=value.primary_endpoint_id,
        evidence_record_ids=evidence_ids,
        feature_projection_sha256=_sha256(projection),
    )


def derive_candidate_profile(value: CandidateEvidenceInput) -> CandidateDecisionProfile:
    """Derive all dimensions and triage from typed facts without accepting worker scores."""

    validate_candidate_evidence_input(value)
    clusters = derive_independence_clusters(value)
    endpoint_profiles: list[EndpointDecisionProfile] = []
    for endpoint in value.endpoints:
        support = _therapeutic_support(endpoint.endpoint_id, value, clusters)
        quality = _evidence_quality(endpoint.endpoint_id, value, clusters)
        mechanism = _apply_expert_if_unknown(
            _mechanistic_coherence(endpoint.endpoint_id, value, support), value
        )
        clinical = _human_clinical_evidence(endpoint.endpoint_id, value, clusters)
        human_model = _human_derived_model_evidence(endpoint.endpoint_id, value, clusters)
        specificity = _apply_expert_if_unknown(
            _endpoint_specificity(
                endpoint,
                primary_endpoint_id=value.primary_endpoint_id,
                evidence=value.evidence,
            ),
            value,
        )
        endpoint_profiles.append(
            EndpointDecisionProfile(
                endpoint_id=endpoint.endpoint_id,
                primary=endpoint.endpoint_id == value.primary_endpoint_id,
                source_status=endpoint.deep_status,
                therapeutic_support=support,
                evidence_quality=quality,
                mechanistic_coherence=mechanism,
                human_clinical_evidence=clinical,
                human_derived_model_evidence=human_model,
                endpoint_specificity=specificity,
            )
        )
    endpoint_profiles.sort(key=lambda row: row.endpoint_id)
    primary = next(row for row in endpoint_profiles if row.primary)
    exposure = _exposure_feasibility(value)
    safety = _safety_and_tolerability(value)
    readiness = _repurposing_readiness(value)
    novelty = _apply_expert_if_unknown(_novelty_underexploration(value), value)
    translatability = _apply_expert_if_unknown(
        _clinical_translatability(value, primary.human_clinical_evidence, exposure, safety, readiness),
        value,
    )
    uncertainty = _uncertainty(
        value,
        primary.therapeutic_support,
        primary.evidence_quality,
        primary.mechanistic_coherence,
        exposure,
        safety,
        clusters,
    )
    information = _apply_expert_if_unknown(
        _information_value(
            primary.therapeutic_support,
            primary.mechanistic_coherence,
            exposure,
            safety,
            novelty,
            uncertainty,
        ),
        value,
    )
    triage = _triage(
        value,
        support=primary.therapeutic_support,
        quality=primary.evidence_quality,
        mechanism=primary.mechanistic_coherence,
        exposure=exposure,
        safety=safety,
        uncertainty=uncertainty,
        information=information,
    )
    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": TRIAGE_RANKING_MODEL_VERSION,
        "candidate_id": value.candidate_id,
        "case_revision_id": value.case_revision_id,
        "normalized_intervention_id": value.normalized_intervention_id,
        "primary_endpoint_id": value.primary_endpoint_id,
        "endpoint_assessments": tuple(endpoint_profiles),
        "therapeutic_support": primary.therapeutic_support,
        "evidence_quality": primary.evidence_quality,
        "mechanistic_coherence": primary.mechanistic_coherence,
        "human_clinical_evidence": primary.human_clinical_evidence,
        "human_derived_model_evidence": primary.human_derived_model_evidence,
        "endpoint_specificity": primary.endpoint_specificity,
        "clinical_translatability": translatability,
        "exposure_feasibility": exposure,
        "safety_and_tolerability": safety,
        "repurposing_readiness": readiness,
        "novelty_underexploration": novelty,
        "uncertainty": uncertainty,
        "information_value": information,
        "independence_clusters": clusters,
        "triage": triage,
    }
    profile = CandidateDecisionProfile(profile_id=_stable_id("DECISION-PROFILE", body), **body)
    validate_candidate_profile(profile)
    return profile


def _therapeutic_tier(profile: CandidateDecisionProfile) -> TherapeuticConfidenceTier:
    support = profile.therapeutic_support.band
    quality = profile.evidence_quality.band
    clinical = profile.human_clinical_evidence.band
    uncertainty = profile.uncertainty.band
    if (
        support == "strong"
        and quality in {"high", "moderate"}
        and clinical in {"direct_interventional", "supportive_observational"}
        and uncertainty in {"low", "moderate"}
    ):
        return TherapeuticConfidenceTier.HIGH
    if support in {"strong", "moderate"} and quality in {"high", "moderate", "low"}:
        return TherapeuticConfidenceTier.MODERATE
    if support == "conflicting":
        return TherapeuticConfidenceTier.CONFLICTED
    if support == "limited":
        return TherapeuticConfidenceTier.LOW
    return TherapeuticConfidenceTier.INSUFFICIENT


def _research_tier(profile: CandidateDecisionProfile) -> ResearchPriorityTier:
    band = profile.information_value.band
    if band == "high":
        return ResearchPriorityTier.HIGH
    if band == "moderate":
        return ResearchPriorityTier.MODERATE
    if band == "low":
        return ResearchPriorityTier.LOW
    return ResearchPriorityTier.NOT_ACTIONABLE


def _therapeutic_ordering_bands(profile: CandidateDecisionProfile) -> tuple[str, ...]:
    """Exclude readiness, novelty, and information value from therapeutic confidence."""

    return (
        profile.therapeutic_support.band,
        profile.evidence_quality.band,
        profile.mechanistic_coherence.band,
        profile.human_clinical_evidence.band,
        profile.human_derived_model_evidence.band,
        profile.endpoint_specificity.band,
        profile.uncertainty.band,
    )


def _therapeutic_sort_key(profile: CandidateDecisionProfile) -> tuple[Any, ...]:
    dimensions = (
        profile.therapeutic_support,
        profile.evidence_quality,
        profile.mechanistic_coherence,
        profile.human_clinical_evidence,
        profile.human_derived_model_evidence,
        profile.endpoint_specificity,
        profile.uncertainty,
    )
    return (*(-row.ordinal for row in dimensions), profile.candidate_id)


def _research_ordering_bands(profile: CandidateDecisionProfile) -> tuple[str, ...]:
    return (
        profile.information_value.band,
        profile.novelty_underexploration.band,
        profile.mechanistic_coherence.band,
        profile.therapeutic_support.band,
        profile.uncertainty.band,
    )


def _research_sort_key(profile: CandidateDecisionProfile) -> tuple[Any, ...]:
    dimensions = (
        profile.information_value,
        profile.novelty_underexploration,
        profile.mechanistic_coherence,
        profile.therapeutic_support,
    )
    # High uncertainty can increase information value, but does not itself win a tie.
    return (*(-row.ordinal for row in dimensions), -profile.uncertainty.ordinal, profile.candidate_id)


def validate_candidate_profile(profile: CandidateDecisionProfile) -> None:
    if profile.schema_version != SCHEMA_VERSION or profile.model_version != TRIAGE_RANKING_MODEL_VERSION:
        raise TriageRankingError("candidate decision profile version mismatch")
    assessments = (
        profile.therapeutic_support,
        profile.evidence_quality,
        profile.mechanistic_coherence,
        profile.human_clinical_evidence,
        profile.human_derived_model_evidence,
        profile.endpoint_specificity,
        profile.clinical_translatability,
        profile.exposure_feasibility,
        profile.safety_and_tolerability,
        profile.repurposing_readiness,
        profile.novelty_underexploration,
        profile.uncertainty,
        profile.information_value,
    )
    if {row.dimension for row in assessments} != set(Dimension):
        raise TriageRankingError("candidate profile must retain all separate decision dimensions")
    known_rule_ids = {
        row.rule_id
        for table in DECISION_TABLES.values()
        for row in table
    }
    for assessment in assessments:
        if assessment.ordinal != _ordinal(assessment.dimension, assessment.band):
            raise TriageRankingError("dimension ordinal is not derived from its controlled band")
        if assessment.assessment_kind is AssessmentKind.EXPERT_ASSESSMENT:
            if assessment.decision_rule_id != "EXPERT-ASSESSMENT-01" or not assessment.expert_assessment_ids:
                raise TriageRankingError("expert-derived dimension is not explicitly labeled")
        elif assessment.expert_assessment_ids:
            raise TriageRankingError("deterministic dimension cannot cite an expert assessment")
        elif assessment.decision_rule_id not in known_rule_ids:
            raise TriageRankingError("deterministic dimension does not cite a published decision-table row")
    endpoints = {row.endpoint_id: row for row in profile.endpoint_assessments}
    if len(endpoints) != len(profile.endpoint_assessments) or profile.primary_endpoint_id not in endpoints:
        raise TriageRankingError("profile endpoint assessments are incomplete or duplicated")
    if sum(row.primary for row in profile.endpoint_assessments) != 1 or not endpoints[profile.primary_endpoint_id].primary:
        raise TriageRankingError("profile must declare exactly one retained primary endpoint")
    endpoint_dimensions = (
        ("therapeutic_support", Dimension.THERAPEUTIC_SUPPORT),
        ("evidence_quality", Dimension.EVIDENCE_QUALITY),
        ("mechanistic_coherence", Dimension.MECHANISTIC_COHERENCE),
        ("human_clinical_evidence", Dimension.HUMAN_CLINICAL_EVIDENCE),
        ("human_derived_model_evidence", Dimension.HUMAN_DERIVED_MODEL_EVIDENCE),
        ("endpoint_specificity", Dimension.ENDPOINT_SPECIFICITY),
    )
    for endpoint in profile.endpoint_assessments:
        for field_name, dimension in endpoint_dimensions:
            assessment = getattr(endpoint, field_name)
            if (
                assessment.dimension is not dimension
                or assessment.endpoint_id != endpoint.endpoint_id
                or assessment.ordinal != _ordinal(dimension, assessment.band)
            ):
                raise TriageRankingError("endpoint decision dimension is inconsistent")
            if (
                assessment.assessment_kind is AssessmentKind.DETERMINISTIC
                and assessment.decision_rule_id not in known_rule_ids
            ):
                raise TriageRankingError("endpoint dimension lacks a published decision-table row")
    primary = endpoints[profile.primary_endpoint_id]
    if (
        profile.therapeutic_support != primary.therapeutic_support
        or profile.evidence_quality != primary.evidence_quality
        or profile.mechanistic_coherence != primary.mechanistic_coherence
        or profile.human_clinical_evidence != primary.human_clinical_evidence
        or profile.human_derived_model_evidence != primary.human_derived_model_evidence
        or profile.endpoint_specificity != primary.endpoint_specificity
    ):
        raise TriageRankingError("candidate-level therapeutic fields must equal the declared primary endpoint")
    if profile.triage.decision_rule_id not in {row.rule_id for row in DECISION_TABLES["triage"]}:
        raise TriageRankingError("triage decision lacks a published decision-table row")
    body = _without_field(profile, "profile_id")
    if profile.profile_id != _stable_id("DECISION-PROFILE", body):
        raise TriageRankingError("candidate decision profile content-derived ID is invalid")


def rank_candidate_profiles(
    profiles: Iterable[CandidateDecisionProfile],
) -> tuple[RankingPreparationRecord, ...]:
    """Create deterministic within-tier ranks without selecting a portfolio."""

    rows = list(profiles)
    by_candidate = {row.candidate_id: row for row in rows}
    if len(by_candidate) != len(rows):
        raise TriageRankingError("candidate decision profiles must have unique candidate IDs")
    for profile in rows:
        validate_candidate_profile(profile)

    therapeutic_groups: dict[TherapeuticConfidenceTier, list[CandidateDecisionProfile]] = {}
    research_groups: dict[ResearchPriorityTier, list[CandidateDecisionProfile]] = {}
    for profile in rows:
        therapeutic_groups.setdefault(_therapeutic_tier(profile), []).append(profile)
        research_groups.setdefault(_research_tier(profile), []).append(profile)

    therapeutic_rank: dict[str, int] = {}
    for tier in TherapeuticConfidenceTier:
        ordered = sorted(therapeutic_groups.get(tier, []), key=_therapeutic_sort_key)
        therapeutic_rank.update({profile.candidate_id: index for index, profile in enumerate(ordered, 1)})
    research_rank: dict[str, int] = {}
    for tier in ResearchPriorityTier:
        ordered = sorted(research_groups.get(tier, []), key=_research_sort_key)
        research_rank.update({profile.candidate_id: index for index, profile in enumerate(ordered, 1)})

    result: list[RankingPreparationRecord] = []
    for candidate_id in sorted(by_candidate):
        profile = by_candidate[candidate_id]
        therapeutic_tier = _therapeutic_tier(profile)
        research_tier = _research_tier(profile)
        body = {
            "candidate_id": candidate_id,
            "profile_id": profile.profile_id,
            "primary_endpoint_id": profile.primary_endpoint_id,
            "triage_category": profile.triage.category,
            "therapeutic_confidence_tier": therapeutic_tier,
            "therapeutic_rank_within_tier": therapeutic_rank[candidate_id],
            "research_priority_tier": research_tier,
            "research_rank_within_tier": research_rank[candidate_id],
            "therapeutic_ordering_bands": _therapeutic_ordering_bands(profile),
            "research_ordering_bands": _research_ordering_bands(profile),
            "deterministic_tie_breaker": candidate_id,
            "ordering_rule_version": "schema-v7-separate-pre-audit-orders-v1",
        }
        result.append(RankingPreparationRecord(preparation_id=_stable_id("RANK-PREP", body), **body))
    return tuple(result)


def validate_ranking_preparation(
    profiles: Iterable[CandidateDecisionProfile],
    records: Iterable[RankingPreparationRecord],
) -> None:
    expected = rank_candidate_profiles(profiles)
    supplied = tuple(records)
    if supplied != expected:
        raise TriageRankingError("ranking preparation differs from deterministic typed-feature reduction")


def derive_and_rank_candidate_inputs(
    values: Iterable[CandidateEvidenceInput],
) -> tuple[tuple[CandidateDecisionProfile, ...], tuple[RankingPreparationRecord, ...]]:
    """Production entry point: derive profiles before ranking so input scores cannot enter."""

    inputs = list(values)
    profiles = tuple(sorted((derive_candidate_profile(value) for value in inputs), key=lambda row: row.candidate_id))
    if len({row.candidate_id for row in profiles}) != len(profiles):
        raise TriageRankingError("candidate evidence inputs must have unique candidate IDs")
    return profiles, rank_candidate_profiles(profiles)


def _qualified_enum_value(value: Any, enum_type: type[Enum]) -> Any | None:
    if getattr(value, "status", None) is ValueStatus.KNOWN and isinstance(getattr(value, "value", None), enum_type):
        return value.value
    return None


def _qualified_bool(value: Any) -> bool | None:
    if getattr(value, "status", None) is ValueStatus.KNOWN and isinstance(getattr(value, "value", None), bool):
        return value.value
    return None


def _qualified_strings(value: Any) -> tuple[str, ...]:
    if getattr(value, "status", None) is ValueStatus.KNOWN and isinstance(getattr(value, "value", None), tuple):
        return _strings((_route_token(item) for item in value.value), "case route")
    return ()


def build_candidate_evidence_input(
    case: CaseRevision,
    package: DeepEvidencePackage,
    *,
    primary_endpoint_id: str,
    ancestry: Iterable[EvidenceAncestry],
    exposure: Iterable[ExposureEvidence],
    safety: Iterable[SafetyEvidence],
    literature_landscape: LiteratureLandscape,
    scope_eligibility: ScopeEligibility = ScopeEligibility.ELIGIBLE,
    scope_reason: str = "within declared pharmacologic scope",
    explicit_uncertainties: Iterable[str] = (),
    expert_assessments: Iterable[ExpertAssessment] = (),
) -> CandidateEvidenceInput:
    """Flatten validated v7 case/deep records into the deterministic feature contract."""

    validate_case_revision(case)
    validate_deep_evidence_package(package, verification_mode=VerificationMode.STRUCTURAL)
    candidate = package.screened_candidate
    if candidate.case_revision_id != case.case_revision_id:
        raise TriageRankingError("deep package and case revision differ")
    case_endpoints = {endpoint.endpoint_id: endpoint for endpoint in case.endpoints}
    if set(candidate.endpoint_ids) != set(case_endpoints):
        raise TriageRankingError("candidate must retain every case endpoint before ranking")
    if primary_endpoint_id not in case_endpoints:
        raise TriageRankingError("primary decision endpoint is outside the case portfolio")
    deep_assessments = {row.endpoint_id: row for row in package.endpoint_assessments}
    endpoints = tuple(
        EndpointFeature(
            endpoint_id=endpoint_id,
            role=_qualified_enum_value(case_endpoints[endpoint_id].role, EndpointRole),
            endpoint_type=_qualified_enum_value(case_endpoints[endpoint_id].endpoint_type, EndpointType),
            priority=_qualified_enum_value(case_endpoints[endpoint_id].priority, EndpointPriority),
            required=_qualified_bool(case_endpoints[endpoint_id].required),
            deep_status=deep_assessments[endpoint_id].status,
            claim_ids=deep_assessments[endpoint_id].claim_ids,
            relationship_types=tuple(
                sorted(
                    (
                        row.relationship_type
                        for row in (case_endpoints[endpoint_id].relationships.value or ())
                    )
                    if case_endpoints[endpoint_id].relationships.status is ValueStatus.KNOWN
                    else (),
                    key=lambda row: row.value,
                )
            ),
        )
        for endpoint_id in sorted(case_endpoints)
    )
    claims = {row.claim_id: row for row in package.claims}
    evidence = tuple(
        sorted(
            (
                EvidenceFeature(
                    evidence_record_id=record.deep_evidence_record_id,
                    claim_id=record.claim_id,
                    endpoint_id=record.endpoint_id,
                    source_id=record.source_id,
                    evidence_span_id=record.evidence_span_id,
                    polarity=claims[record.claim_id].polarity,
                    reporting_status=claims[record.claim_id].reporting_status,
                    calibration=claims[record.claim_id].calibration,
                    evidence_modality=claims[record.claim_id].evidence_modality,
                    study_design=record.study_design,
                    model_kind=record.population_or_experimental_model.model_kind,
                    species=record.population_or_experimental_model.species,
                    effect_direction=record.effect_direction,
                    risk_of_bias=record.risk_of_bias_assessment.level,
                )
                for record in package.evidence_records
            ),
            key=lambda row: row.evidence_record_id,
        )
    )
    routes = tuple(
        sorted(
            (
                MechanisticRouteFeature(
                    route_id=route.route_id,
                    endpoint_id=route.endpoint_id,
                    direction_known=(
                        route.direction not in {EffectDirection.UNKNOWN, EffectDirection.MIXED}
                        and route.disease_state_node.status is NodeStatus.KNOWN
                        and route.intervention_target.status is NodeStatus.KNOWN
                    ),
                    evidence_ids=route.evidence_ids,
                )
                for route in candidate.structured_routes
            ),
            key=lambda row: row.route_id,
        )
    )
    identity: AuthoritativeIdentityRecord | None = None
    if package.current_identity_record_id is not None:
        identity = next(
            row for row in package.identity_records if row.identity_record_id == package.current_identity_record_id
        )
    identity_status = identity.resolution_status if identity else IdentityResolutionStatus.UNRESOLVED
    normalized_id = identity.normalized_intervention_id if identity else None
    human_use = tuple(sorted({row.status for row in (identity.human_use_status_assertions if identity else ())}, key=lambda row: row.value))
    development = tuple(sorted({row.status for row in (identity.development_status_assertions if identity else ())}, key=lambda row: row.value))
    formulation_routes = (
        _strings((_route_token(route) for route in identity.formulation.administration_routes), "formulation route")
        if identity and identity.formulation
        else ()
    )
    allowed_routes = _qualified_strings(case.target_product_profile.allowed_routes)
    excluded_routes = _qualified_strings(case.target_product_profile.excluded_routes)
    route_constraints_known = (
        case.target_product_profile.allowed_routes.status is ValueStatus.KNOWN
        or case.target_product_profile.excluded_routes.status is ValueStatus.KNOWN
    )
    result = CandidateEvidenceInput(
        schema_version=SCHEMA_VERSION,
        model_version=TRIAGE_RANKING_MODEL_VERSION,
        candidate_id=candidate.screened_candidate_id,
        case_revision_id=case.case_revision_id,
        normalized_intervention_id=normalized_id,
        identity_status=identity_status,
        deep_identity_eligible=bool(identity and identity.deep_identity_eligible),
        scope_eligibility=scope_eligibility,
        scope_reason=_text(scope_reason, "scope reason"),
        primary_endpoint_id=_text(primary_endpoint_id, "primary endpoint ID"),
        endpoints=endpoints,
        evidence=evidence,
        ancestry=tuple(sorted(ancestry, key=lambda row: row.evidence_record_id)),
        routes=routes,
        exposure=tuple(sorted(exposure, key=lambda row: row.exposure_record_id)),
        safety=tuple(sorted(safety, key=lambda row: row.safety_record_id)),
        human_use_statuses=human_use,
        development_statuses=development,
        formulation_routes=formulation_routes,
        allowed_routes=allowed_routes,
        excluded_routes=excluded_routes,
        route_constraints_known=route_constraints_known,
        literature_landscape=literature_landscape,
        explicit_uncertainties=_strings(explicit_uncertainties, "explicit uncertainty"),
        expert_assessments=tuple(sorted(expert_assessments, key=lambda row: row.assessment_id)),
    )
    validate_candidate_evidence_input(result)
    source_ids = {row.source_record_id for row in package.sources}
    span_ids = {row.evidence_span_id for row in package.evidence_spans}
    for record in (*result.exposure, *result.safety):
        if not set(record.source_record_ids).issubset(source_ids):
            raise TriageRankingError("safety/exposure source records must resolve in the deep package")
        if not set(record.evidence_span_ids).issubset(span_ids):
            raise TriageRankingError("safety/exposure evidence spans must resolve in the deep package")
    if not set(literature_landscape.source_record_ids).issubset(source_ids):
        raise TriageRankingError("literature-landscape sources must resolve in the deep package")
    return result


__all__ = [
    "AssessmentKind",
    "CandidateDecisionProfile",
    "CandidateEvidenceInput",
    "CaseApplicability",
    "DECISION_TABLES",
    "Dimension",
    "DimensionAssessment",
    "DoseContext",
    "EndpointFeature",
    "EvidenceAncestry",
    "EvidenceFeature",
    "ExposureEvidence",
    "FrequencyBand",
    "LiteratureLandscape",
    "MechanisticRouteFeature",
    "PharmacokineticBasis",
    "RankingPreparationRecord",
    "ResearchPriorityTier",
    "SafetyCausality",
    "SafetyEvidence",
    "SafetyEvidenceKind",
    "SafetyFinding",
    "SafetySeverity",
    "ScopeEligibility",
    "TargetDiseaseDevelopment",
    "TerminalDisposition",
    "TherapeuticConfidenceTier",
    "TissueApplicability",
    "TriageCategory",
    "TriageDecision",
    "TriageRankingError",
    "build_candidate_evidence_input",
    "derive_and_rank_candidate_inputs",
    "derive_candidate_profile",
    "derive_independence_clusters",
    "make_expert_assessment",
    "make_exposure_evidence",
    "make_safety_evidence",
    "rank_candidate_profiles",
    "validate_candidate_evidence_input",
    "validate_candidate_profile",
    "validate_ranking_preparation",
]
