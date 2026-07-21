#!/usr/bin/env python3
"""Scoped schema-v7 triage/ranking acceptance tests."""

from __future__ import annotations

import unittest
from dataclasses import replace

from v7_case_model import EndpointPriority, EndpointRole, EndpointType
from v7_deep_evidence import (
    ClaimCalibration,
    ClaimPolarity,
    ClaimReportingStatus,
    DevelopmentStatus,
    EndpointDeepStatus,
    ExperimentalModelKind,
    HumanUseStatus,
    IdentityResolutionStatus,
    ObservedEffectDirection,
    RiskOfBiasLevel,
    StudyDesign,
    reported_quantity,
)
from v7_discovery import EvidenceModality
from v7_triage_ranking import (
    CandidateEvidenceInput,
    CaseApplicability,
    DoseContext,
    EndpointFeature,
    EvidenceAncestry,
    EvidenceFeature,
    ExposureEvidence,
    FrequencyBand,
    LiteratureLandscape,
    MechanisticRouteFeature,
    PharmacokineticBasis,
    SafetyCausality,
    SafetyEvidence,
    SafetyEvidenceKind,
    SafetyFinding,
    SafetySeverity,
    ScopeEligibility,
    TargetDiseaseDevelopment,
    TherapeuticConfidenceTier,
    TissueApplicability,
    TriageCategory,
    derive_candidate_profile,
    make_exposure_evidence,
    make_safety_evidence,
    rank_candidate_profiles,
)


def _evidence(
    suffix: str,
    *,
    candidate_id: str,
    endpoint_id: str = "EP-BENEFIT",
    polarity: ClaimPolarity = ClaimPolarity.SUPPORTS,
    model: ExperimentalModelKind = ExperimentalModelKind.HUMAN_PATIENT_CELL,
    design: StudyDesign = StudyDesign.CELLULAR,
    effect: ObservedEffectDirection = ObservedEffectDirection.BENEFIT,
    source_id: str | None = None,
) -> EvidenceFeature:
    del candidate_id
    return EvidenceFeature(
        evidence_record_id=f"EVID-{suffix}",
        claim_id=f"CLAIM-{suffix}",
        endpoint_id=endpoint_id,
        source_id=source_id or f"SRC-{suffix}",
        evidence_span_id=f"SPAN-{suffix}",
        polarity=polarity,
        reporting_status=ClaimReportingStatus.REPORTED,
        calibration=ClaimCalibration.SUPPORTED_WITH_QUALIFIER,
        evidence_modality=(
            EvidenceModality.CLINICAL_INTERVENTION
            if model is ExperimentalModelKind.HUMAN
            else EvidenceModality.MOLECULAR_FUNCTIONAL
        ),
        study_design=design,
        model_kind=model,
        species="Homo sapiens",
        effect_direction=effect,
        risk_of_bias=RiskOfBiasLevel.LOW,
    )


def _ancestry(
    record: EvidenceFeature,
    *,
    cohort: str,
    lab: str,
    common: str = "",
) -> EvidenceAncestry:
    return EvidenceAncestry(
        evidence_record_id=record.evidence_record_id,
        source_ids=(record.source_id,),
        cohort_ids=(cohort,),
        laboratory_ids=(lab,),
        dataset_ids=(f"DATA-{cohort}",),
        common_ancestry_ids=(common,) if common else (),
    )


def _exposure(candidate_id: str, *, achieved: str = "10", required: str = "2") -> ExposureEvidence:
    return make_exposure_evidence(
        candidate_id=candidate_id,
        exact_intervention_id=f"NI-{candidate_id}",
        dose=reported_quantity("100", "mg"),
        dose_context=DoseContext.CLINICALLY_ATTAINABLE,
        administration_route="oral",
        duration="28 days",
        population="case population",
        target_tissue="target tissue",
        tissue_applicability=TissueApplicability.MATCHED,
        pk_basis=PharmacokineticBasis.MEASURED_HUMAN,
        achieved_concentration=reported_quantity(achieved, "uM"),
        required_effect_concentration=reported_quantity(required, "uM"),
        source_record_ids=(f"SRC-EXP-{candidate_id}",),
        evidence_span_ids=(f"SPAN-EXP-{candidate_id}",),
    )


def _safety(
    candidate_id: str,
    *,
    serious: bool = False,
) -> SafetyEvidence:
    return make_safety_evidence(
        candidate_id=candidate_id,
        exact_intervention_id=f"NI-{candidate_id}",
        evidence_kind=(SafetyEvidenceKind.CONTRAINDICATION if serious else SafetyEvidenceKind.ADVERSE_EVENT),
        finding=SafetyFinding.RISK if serious else SafetyFinding.NO_MATERIAL_RISK,
        severity=SafetySeverity.SERIOUS if serious else SafetySeverity.NONE,
        causality=SafetyCausality.ESTABLISHED,
        frequency=FrequencyBand.COMMON if serious else FrequencyBand.NOT_REPORTED,
        case_applicability=CaseApplicability.DIRECT,
        dose=reported_quantity("100", "mg"),
        administration_route="oral",
        duration="28 days",
        population="case population",
        reversibility="irreversible" if serious else "reversible",
        finding_code="CASE-CONTRAINDICATION" if serious else "NO-MATERIAL-RISK",
        source_record_ids=(f"SRC-SAFE-{candidate_id}",),
        evidence_span_ids=(f"SPAN-SAFE-{candidate_id}",),
    )


def _candidate(
    candidate_id: str,
    *,
    evidence: tuple[EvidenceFeature, ...],
    ancestry: tuple[EvidenceAncestry, ...],
    exposure: tuple[ExposureEvidence, ...] | None = None,
    safety: tuple[SafetyEvidence, ...] | None = None,
    publications: int = 0,
    trials: int = 0,
    primary_endpoint_id: str = "EP-BENEFIT",
    identity_status: IdentityResolutionStatus = IdentityResolutionStatus.RESOLVED,
) -> CandidateEvidenceInput:
    endpoint_claims: dict[str, tuple[str, ...]] = {}
    for row in evidence:
        endpoint_claims.setdefault(row.endpoint_id, tuple())
        endpoint_claims[row.endpoint_id] = tuple(sorted((*endpoint_claims[row.endpoint_id], row.claim_id)))
    endpoint_ids = sorted({"EP-BENEFIT", "EP-SECONDARY", *endpoint_claims})
    endpoints = tuple(
        EndpointFeature(
            endpoint_id=endpoint_id,
            role=EndpointRole.BENEFIT,
            endpoint_type=(EndpointType.CLINICAL_OUTCOME if endpoint_id == "EP-BENEFIT" else EndpointType.FUNCTIONAL_OUTCOME),
            priority=EndpointPriority.HIGH if endpoint_id == "EP-BENEFIT" else EndpointPriority.EXPLORATORY,
            required=endpoint_id == "EP-BENEFIT",
            deep_status=(EndpointDeepStatus.ASSESSED if endpoint_claims.get(endpoint_id) else EndpointDeepStatus.INSUFFICIENT),
            claim_ids=endpoint_claims.get(endpoint_id, ()),
            relationship_types=(),
        )
        for endpoint_id in endpoint_ids
    )
    routes = tuple(
        MechanisticRouteFeature(
            route_id=f"ROUTE-{endpoint_id}",
            endpoint_id=endpoint_id,
            direction_known=True,
            evidence_ids=(f"ROUTE-EVIDENCE-{endpoint_id}",),
        )
        for endpoint_id in sorted(endpoint_claims)
    )
    return CandidateEvidenceInput(
        schema_version=7,
        model_version="schema-v7-triage-ranking-v1",
        candidate_id=candidate_id,
        case_revision_id="CASE-REV-TEST",
        normalized_intervention_id=(f"NI-{candidate_id}" if identity_status is IdentityResolutionStatus.RESOLVED else None),
        identity_status=identity_status,
        deep_identity_eligible=identity_status is IdentityResolutionStatus.RESOLVED,
        scope_eligibility=ScopeEligibility.ELIGIBLE,
        scope_reason="within declared pharmacologic scope",
        primary_endpoint_id=primary_endpoint_id,
        endpoints=endpoints,
        evidence=tuple(sorted(evidence, key=lambda row: row.evidence_record_id)),
        ancestry=tuple(sorted(ancestry, key=lambda row: row.evidence_record_id)),
        routes=routes,
        exposure=tuple(sorted(exposure if exposure is not None else (_exposure(candidate_id),), key=lambda row: row.exposure_record_id)),
        safety=tuple(sorted(safety if safety is not None else (_safety(candidate_id),), key=lambda row: row.safety_record_id)),
        human_use_statuses=(HumanUseStatus.MARKETED_HUMAN_PRODUCT,),
        development_statuses=(DevelopmentStatus.APPROVED,),
        formulation_routes=("oral",),
        allowed_routes=("oral",),
        excluded_routes=(),
        route_constraints_known=True,
        literature_landscape=LiteratureLandscape(
            direct_target_disease_publication_count=publications,
            direct_target_disease_trial_count=trials,
            development_in_target_disease=TargetDiseaseDevelopment.NONE_FOUND,
            earliest_direct_evidence_year=2025 if publications else None,
            source_record_ids=(f"SRC-LAND-{candidate_id}",),
        ),
        explicit_uncertainties=(),
        expert_assessments=(),
    )


class SchemaV7TriageRankingTests(unittest.TestCase):
    def test_sparse_literature_candidate_is_preserved_without_popularity_bias(self) -> None:
        evidence = (_evidence("SPARSE", candidate_id="SPARSE"),)
        ancestry = (_ancestry(evidence[0], cohort="COHORT-SPARSE", lab="LAB-SPARSE"),)
        sparse = derive_candidate_profile(_candidate("SPARSE", evidence=evidence, ancestry=ancestry, publications=0))
        popular = derive_candidate_profile(_candidate("POPULAR", evidence=evidence, ancestry=ancestry, publications=500))
        self.assertEqual(sparse.therapeutic_support, replace(popular.therapeutic_support))
        self.assertEqual(sparse.triage.category, TriageCategory.EVIDENCE_FOLLOW_UP)
        self.assertEqual(sparse.novelty_underexploration.band, "novel_hypothesis")
        self.assertEqual(popular.novelty_underexploration.band, "established")

    def test_patient_cell_only_is_not_human_clinical_evidence(self) -> None:
        evidence = (_evidence("PATIENT-CELL", candidate_id="PCELL"),)
        profile = derive_candidate_profile(
            _candidate(
                "PCELL",
                evidence=evidence,
                ancestry=(_ancestry(evidence[0], cohort="COHORT-PCELL", lab="LAB-PCELL"),),
            )
        )
        self.assertEqual(profile.human_clinical_evidence.band, "absent")
        self.assertEqual(profile.human_derived_model_evidence.band, "single_context")
        self.assertEqual(profile.triage.category, TriageCategory.EVIDENCE_FOLLOW_UP)

    def test_infeasible_exposure_is_derived_from_margin(self) -> None:
        evidence = (_evidence("EXPOSURE", candidate_id="LOWEXP"),)
        value = _candidate(
            "LOWEXP",
            evidence=evidence,
            ancestry=(_ancestry(evidence[0], cohort="COHORT-LOWEXP", lab="LAB-LOWEXP"),),
            exposure=(_exposure("LOWEXP", achieved="0.2", required="2"),),
        )
        profile = derive_candidate_profile(value)
        self.assertEqual(profile.exposure_feasibility.band, "infeasible")
        self.assertEqual(profile.triage.category, TriageCategory.REJECTED_OR_QUARANTINED)
        self.assertEqual(profile.triage.reason_code, "exposure_infeasible")

    def test_serious_population_safety_mismatch_is_structured(self) -> None:
        evidence = (_evidence("SAFETY", candidate_id="RISK"),)
        value = _candidate(
            "RISK",
            evidence=evidence,
            ancestry=(_ancestry(evidence[0], cohort="COHORT-RISK", lab="LAB-RISK"),),
            safety=(_safety("RISK", serious=True),),
        )
        profile = derive_candidate_profile(value)
        self.assertEqual(profile.safety_and_tolerability.band, "serious_mismatch")
        self.assertEqual(profile.triage.category, TriageCategory.REJECTED_OR_QUARANTINED)
        self.assertEqual(profile.triage.reason_code, "serious_safety_mismatch")

    def test_multiple_endpoints_retain_primary_and_secondary(self) -> None:
        benefit = _evidence("BENEFIT", candidate_id="MULTI")
        secondary = _evidence("SECONDARY", candidate_id="MULTI", endpoint_id="EP-SECONDARY")
        value = _candidate(
            "MULTI",
            evidence=(benefit, secondary),
            ancestry=(
                _ancestry(benefit, cohort="COHORT-B", lab="LAB-B"),
                _ancestry(secondary, cohort="COHORT-S", lab="LAB-S"),
            ),
            primary_endpoint_id="EP-BENEFIT",
        )
        profile = derive_candidate_profile(value)
        self.assertEqual(profile.primary_endpoint_id, "EP-BENEFIT")
        self.assertEqual([row.endpoint_id for row in profile.endpoint_assessments], ["EP-BENEFIT", "EP-SECONDARY"])
        self.assertEqual(sum(row.primary for row in profile.endpoint_assessments), 1)

    def test_duplicated_cohort_and_laboratory_do_not_create_independence(self) -> None:
        first = _evidence("DUP-A", candidate_id="DUP", source_id="SRC-DUP-A")
        second = _evidence("DUP-B", candidate_id="DUP", source_id="SRC-DUP-B")
        value = _candidate(
            "DUP",
            evidence=(first, second),
            ancestry=(
                _ancestry(first, cohort="COHORT-SHARED", lab="LAB-SHARED", common="ANCESTRY-SHARED"),
                _ancestry(second, cohort="COHORT-SHARED", lab="LAB-SHARED", common="ANCESTRY-SHARED"),
            ),
        )
        profile = derive_candidate_profile(value)
        self.assertEqual(len(profile.independence_clusters), 1)
        self.assertEqual(profile.therapeutic_support.band, "limited")
        self.assertEqual(profile.independence_clusters[0].shared_cohort_ids, ("COHORT-SHARED",))
        self.assertEqual(profile.independence_clusters[0].shared_laboratory_ids, ("LAB-SHARED",))

    def test_conflicting_directions_are_not_averaged_away(self) -> None:
        supportive = _evidence("DIR-SUPPORT", candidate_id="CONFLICT")
        refuting = _evidence(
            "DIR-REFUTE",
            candidate_id="CONFLICT",
            polarity=ClaimPolarity.REFUTES,
            effect=ObservedEffectDirection.HARM,
        )
        profile = derive_candidate_profile(
            _candidate(
                "CONFLICT",
                evidence=(supportive, refuting),
                ancestry=(
                    _ancestry(supportive, cohort="COHORT-1", lab="LAB-1"),
                    _ancestry(refuting, cohort="COHORT-2", lab="LAB-2"),
                ),
            )
        )
        self.assertEqual(profile.therapeutic_support.band, "conflicting")
        self.assertEqual(profile.triage.category, TriageCategory.EVIDENCE_FOLLOW_UP)

    def test_unresolved_identity_routes_to_identity_follow_up(self) -> None:
        evidence = (_evidence("IDENTITY", candidate_id="IDENTITY"),)
        profile = derive_candidate_profile(
            _candidate(
                "IDENTITY",
                evidence=evidence,
                ancestry=(_ancestry(evidence[0], cohort="COHORT-ID", lab="LAB-ID"),),
                identity_status=IdentityResolutionStatus.UNRESOLVED,
            )
        )
        self.assertEqual(profile.triage.category, TriageCategory.IDENTITY_FOLLOW_UP)

    def test_unsupported_hypothesis_is_deferred_but_preserved(self) -> None:
        profile = derive_candidate_profile(_candidate("DEFER", evidence=(), ancestry=()))
        self.assertEqual(profile.therapeutic_support.band, "insufficient")
        self.assertEqual(profile.triage.category, TriageCategory.DEFERRED_PRESERVED)

    def test_reproducible_ties_use_candidate_id_without_universal_score(self) -> None:
        def strong(candidate_id: str):
            rct = _evidence(
                f"{candidate_id}-RCT",
                candidate_id=candidate_id,
                model=ExperimentalModelKind.HUMAN,
                design=StudyDesign.RANDOMIZED_CONTROLLED_TRIAL,
            )
            cell = _evidence(f"{candidate_id}-CELL", candidate_id=candidate_id)
            return derive_candidate_profile(
                _candidate(
                    candidate_id,
                    evidence=(rct, cell),
                    ancestry=(
                        _ancestry(rct, cohort=f"COHORT-{candidate_id}-1", lab=f"LAB-{candidate_id}-1"),
                        _ancestry(cell, cohort=f"COHORT-{candidate_id}-2", lab=f"LAB-{candidate_id}-2"),
                    ),
                    publications=2,
                )
            )

        alpha, beta = strong("ALPHA"), strong("BETA")
        first = rank_candidate_profiles((beta, alpha))
        second = rank_candidate_profiles((alpha, beta))
        self.assertEqual(first, second)
        by_id = {row.candidate_id: row for row in first}
        self.assertEqual(by_id["ALPHA"].therapeutic_rank_within_tier, 1)
        self.assertEqual(by_id["BETA"].therapeutic_rank_within_tier, 2)
        self.assertEqual(by_id["ALPHA"].therapeutic_confidence_tier, TherapeuticConfidenceTier.HIGH)
        self.assertEqual(alpha.triage.category, TriageCategory.DEEP_REVIEW)
        self.assertNotIn("novel_hypothesis", by_id["ALPHA"].therapeutic_ordering_bands)
        self.assertNotIn("marketed_repurposing_ready", by_id["ALPHA"].therapeutic_ordering_bands)


if __name__ == "__main__":
    unittest.main()
