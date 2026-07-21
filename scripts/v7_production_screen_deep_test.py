#!/usr/bin/env python3
"""Production acceptance tests for persisted schema-v7 screening and depth."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from v7_case_model import build_case_bundle
from v7_deep_evidence import (
    ActiveMoietyMapping,
    ChemicalEntityKind,
    ClaimCalibration,
    ClaimPolarity,
    ClaimReportingStatus,
    ClaimScope,
    Comparator,
    CompositionComponent,
    CompositionStatus,
    CompoundOrigin,
    CompoundOriginAssertion,
    ContentVerificationMethod,
    DeepEndpointAssessment,
    DevelopmentStatusAssertion,
    EffectMagnitude,
    EndpointDeepStatus,
    EvidenceSupportKind,
    ExperimentalModelKind,
    FormulationDescriptor,
    HumanUseStatus,
    HumanUseStatusAssertion,
    IdentityRelationshipType,
    ObservedEffectDirection,
    PopulationOrExperimentalModel,
    ReportedValueStatus,
    RetrievalMethod,
    RiskOfBiasAssessment,
    RiskOfBiasLevel,
    SourceContentScope,
    StatisticalUncertainty,
    StereochemistryStatus,
    StudyDesign,
    bind_atomic_claim,
    make_atomic_claim_core,
    make_deep_evidence_package,
    make_deep_evidence_path,
    make_deep_evidence_record,
    make_deep_source_record,
    make_evidence_span,
    make_registry_identity_assertion,
    missing_quantity,
    normalize_authoritative_identity,
    reported_quantity,
    reported_text,
)
from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
    UncertaintyKind,
    UncertaintyLevel,
    known_node,
    make_structured_route,
    not_applicable_node,
)
from v7_production_disposition import (
    NORMALIZATION_POLICY_VERSION,
    V7DispositionAdapter,
)
from v7_production_screen_deep import (
    DEEP_SELECTION_POLICY_VERSION,
    SCREEN_RULE_VERSION,
    ScreenDeepAggregateConflictError,
    ScreenDeepAggregateError,
    V7ScreenDeepAdapter,
    _exact_identity_bridge,
    _stage4_component_bridge_id,
    build_screened_candidate,
    validate_screen_deep_aggregate,
)
from v7_seed_funnel import (
    CompoundHintKind,
    SeedIdentityStatus,
    SeedUncertainty,
    known_development_status,
    make_candidate_seed,
    make_compound_hint,
    make_discovery_route,
    make_source_mapping,
)
from v7_triage_ranking import (
    CaseApplicability,
    DoseContext,
    EvidenceAncestry,
    FrequencyBand,
    LiteratureLandscape,
    PharmacokineticBasis,
    SafetyCausality,
    SafetyEvidenceKind,
    SafetyFinding,
    SafetySeverity,
    ScopeEligibility,
    TargetDiseaseDevelopment,
    TissueApplicability,
    make_exposure_evidence,
    make_safety_evidence,
)


STRUCTURES = (
    ("single_compound", "CCO", "InChI=1S/C2H6O", "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"),
    ("salt", "CCO.[Cl-]", "InChI=1S/C2H6O.ClH", "AAAAAAAAAAAAAA-BBBBBBBBBB-C"),
    ("single_compound", "CCN", "InChI=1S/C2H7N", "QUSNBJAOOMFDIB-UHFFFAOYSA-N"),
    ("single_compound", "CCC", "InChI=1S/C3H8", "ATUOYWHBWRKTHZ-UHFFFAOYSA-N"),
    ("single_compound", "CCCC", "InChI=1S/C4H10", "IJDNQMDRQITEOD-UHFFFAOYSA-N"),
)


def _endpoint(
    stable_key: str,
    construct: str,
    *,
    required: bool,
    priority: str,
) -> dict[str, Any]:
    return {
        "stable_key": stable_key,
        "display_label": f"{stable_key} endpoint",
        "construct": construct,
        "role": "benefit",
        "endpoint_type": "clinical_outcome" if required else "functional_outcome",
        "population": "Adults",
        "disease_stage": "Established disease",
        "timeframe": "24 weeks",
        "measurement": f"Declared {stable_key} measure",
        "disease_context": "Target disease",
        "direction": "decrease_is_benefit",
        "priority": priority,
        "required": required,
        "relationships": [],
    }


def make_case() -> Any:
    return build_case_bundle(
        {
            "gene": "TP53",
            "disease": "MONDO:0004979",
            "endpoints": [
                _endpoint("primary", "HP:0001250", required=True, priority="high"),
                _endpoint(
                    "secondary", "HP:0001263", required=False, priority="exploratory"
                ),
            ],
        }
    ).case_revision


def _source_identity(
    *, entity_kind: str, smiles: str, inchi: str, inchikey: str
) -> dict[str, Any]:
    return {
        "entity_kind": entity_kind,
        "registry_identifiers": [
            {"namespace": "FROZEN-EXACT", "identifier": inchikey}
        ],
        "canonical_structure": {
            "canonical_smiles": smiles,
            "standard_inchi": inchi,
            "full_inchikey": inchikey,
            "stereochemistry_status": "not_applicable",
            "stereochemistry_descriptor": "not_applicable",
            "canonicalization_method": "authority_reported",
            "canonicalization_version": "2026-07",
        },
        "composition_status": "not_applicable",
        "components": [],
        "product": None,
        "active_moieties": [
            {
                "relationship_type": "self" if entity_kind == "single_compound" else "salt_of",
                "moiety_namespace": "FROZEN-EXACT",
                "moiety_identifier": inchikey if entity_kind == "single_compound" else "PARENT-B",
                "moiety_entity_kind": "single_compound",
                "exact_form_scope": "The exact asserted form only; no evidence transfer.",
            }
        ],
    }


def _seed(case: Any, index: int) -> Any:
    mapping = make_source_mapping(
        case,
        source_id="FROZEN-DISCOVERY",
        source_release="2026-07-21",
        native_record_id=f"NATIVE-{index}",
        assertion_locator=f"records/{index}/intervention",
        raw_intervention_assertion=f"Exact intervention {index}",
    )
    discovery = make_discovery_route(
        mapping,
        query_id="FROZEN-QUERY",
        query_record_locator=f"results/{index}",
        retrieval_content_receipt_id=f"RECEIPT-{index}",
    )
    routes = tuple(
        make_structured_route(
            case_revision_id=case.case_revision_id,
            intervention_id=mapping.seed_id,
            causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
            disease_state_node=known_node("DISEASE:TEST", "target disease"),
            intervention_target=known_node("TARGET:TEST", "target node"),
            action=InterventionAction.INHIBIT,
            direction=EffectDirection.DECREASE,
            intermediate_state=not_applicable_node("Direct route."),
            endpoint_id=endpoint.endpoint_id,
            evidence_ids=(f"DISCOVERY-EVIDENCE-{index}-{endpoint.endpoint_id}",),
        )
        for endpoint in case.endpoints
    )
    preclinical = index == 2
    return make_candidate_seed(
        case,
        mapping,
        endpoint_ids=(endpoint.endpoint_id for endpoint in case.endpoints),
        compound_hint=make_compound_hint(
            CompoundHintKind.DATABASE_IDENTIFIER,
            f"FROZEN-{index}",
            namespace="FROZEN-DB",
        ),
        discovery_route_ids=(discovery.route_id,),
        structured_routes=routes,
        evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
        chemical_universes=(
            ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS
            if preclinical
            else ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,
        ),
        development_status_hint=known_development_status(
            DevelopmentStatus.PRECLINICAL if preclinical else DevelopmentStatus.APPROVED
        ),
        identity_status=SeedIdentityStatus.UNASSESSED,
        uncertainty=(
            SeedUncertainty(
                UncertaintyKind.MISSINGNESS,
                UncertaintyLevel.HIGH if preclinical else UncertaintyLevel.LOW,
                "Sparse evidence remains explicit." if preclinical else "No discovery-depth blocker.",
            ),
        ),
    )


def make_admitted_frame(case: Any, root: Path) -> tuple[dict[str, Any], list[Any]]:
    seeds = [_seed(case, index) for index in range(len(STRUCTURES))]
    source = {
        "resolver_source_id": "FROZEN-AUTHORITY",
        "authority": "Frozen exact identity authority",
        "authority_release": "2026-07-21",
        "snapshot_id": "FROZEN-AUTHORITY-SNAPSHOT",
        "snapshot_sha256": "FROZEN-AUTHORITY-CONTENT-HASH",
        "method": "frozen_authority_record",
        "locator": "frozen://authority",
    }
    seed_results = []
    assertions = []
    for seed, (entity_kind, smiles, inchi, key) in zip(seeds, STRUCTURES):
        seed_results.append(
            {
                "seed_id": seed.seed_id,
                "result_status": "resolved",
                "case_role": "repurposing",
                "reason_code": "authority_resolution_complete",
                "reason": "Frozen authorities resolved one exact intervention.",
                "resolver_source_ids": ["FROZEN-AUTHORITY"],
            }
        )
        assertions.append(
            {
                "seed_id": seed.seed_id,
                "resolver_source_id": "FROZEN-AUTHORITY",
                "authority_record_id": f"AUTHORITY:{key}",
                "authority_locator": f"frozen://authority/{key}",
                "assertion_status": "resolved",
                "reported_identity": seed.compound_hint.value,
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
    bundle = {
        "resolver_revision": "screen-deep-production-r1",
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "resolver_sources": [source],
        "seed_results": seed_results,
        "identity_assertions": assertions,
    }
    aggregate = V7DispositionAdapter(root).normalize_and_dispose(case, seeds, bundle)
    return dict(aggregate), seeds


def _rule(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "evidence_pointer_ids": [],
        "uncertainty": [],
    }


def _screen(
    case: Any,
    disposition: dict[str, Any],
    *,
    index: int,
    statuses: tuple[str, str],
    exposure: str = "feasible",
    safety: str = "acceptable",
    readiness: str = "ready",
) -> dict[str, Any]:
    seed_id = disposition["seed_id"]
    endpoint_rows = []
    for endpoint, status in zip(case.endpoints, statuses):
        endpoint_rows.append(
            {
                "endpoint_id": endpoint.endpoint_id,
                "status": status,
                "reason": "Frozen lightweight endpoint assessment.",
                "applicability_reason": None,
                "evidence_pointer_ids": [f"SCREEN-EVIDENCE-{index}-{endpoint.endpoint_id}"],
                "uncertainty": [
                    {
                        "kind": "missingness" if status == "insufficient" else "effect",
                        "level": "high" if status == "insufficient" else "low",
                        "note": "Sparse evidence retained." if status == "insufficient" else "Typed screen evidence.",
                    }
                ],
            }
        )
    return {
        "normalized_intervention_id": disposition["normalized_intervention_id"],
        "representative_seed_id": seed_id,
        "processing_status": "complete",
        "processing_reason": "Lightweight screen completed deterministically.",
        "rules": {
            "eligibility": _rule("eligible", "Within declared pharmacologic scope."),
            "contraindication": _rule("clear", "No typed screen-level contraindication."),
            "preliminary_safety": _rule(safety, "Frozen preliminary safety state."),
            "preliminary_exposure": _rule(exposure, "Frozen preliminary exposure state."),
            "development_readiness": _rule(readiness, "Frozen development stratum."),
            "case_fit": _rule("plausible", "No typed case-fit blocker."),
        },
        "endpoint_assessments": endpoint_rows,
        "unresolved_fields": ["full_deep_evidence"] if "insufficient" in statuses else [],
    }


def _deep_source(label: str, text: str) -> tuple[Any, dict[str, bytes]]:
    payload = text.encode("utf-8")
    locator = f"retained/{label}.txt"
    source = make_deep_source_record(
        source_id=f"SOURCE-{label}",
        source_release="2026-07-21",
        native_record_id=f"NATIVE-{label}",
        retrieval_content_receipt_id=f"CONTENT-RECEIPT-{label}",
        retained_payload_locator=locator,
        raw_content=payload,
        content_scope=SourceContentScope.ORIGINAL_FULL_TEXT,
        retrieval_method=RetrievalMethod.LOCAL_FROZEN_FIXTURE,
        verification_method=ContentVerificationMethod.RETAINED_PAYLOAD_SHA256,
    )
    return source, {locator: payload}


def make_deep_result(
    case: Any,
    candidate: Any,
    stage_structure: tuple[str, str, str, str],
    *,
    unsafe: bool,
    include_counterevidence: bool,
    stereochemistry_descriptor: str = "not_applicable",
) -> dict[str, Any]:
    entity_kind_text, smiles, inchi, inchikey = stage_structure
    entity_kind = ChemicalEntityKind(entity_kind_text)
    retained: dict[str, bytes] = {}
    identity_sources = []
    identity_spans = []
    identity_assertions = []
    for authority in ("A", "B"):
        text = (
            f"Registry {authority} exact {entity_kind.value}: {smiles}; {inchi}; {inchikey}. "
            "Synthetic origin and human use status are recorded."
        )
        source, payloads = _deep_source(
            f"{candidate.screened_candidate_id}-IDENTITY-{authority}", text
        )
        retained.update(payloads)
        span = make_evidence_span(
            source,
            claim_id=f"IDENTITY-{candidate.screened_candidate_id}",
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator=f"identity/{authority}",
            exact_excerpt=text,
        )
        identity_sources.append(source)
        identity_spans.append(span)
        identity_assertions.append(
            make_registry_identity_assertion(
                authority=f"Registry {authority}",
                authority_release="2026-07-21",
                source_record_id=source.source_record_id,
                evidence_span_id=span.evidence_span_id,
                entity_kind=entity_kind,
                registry_identifiers={f"REG-{authority}": inchikey},
                canonical_smiles=smiles,
                standard_inchi=inchi,
                inchikey=inchikey,
                stereochemistry_status=StereochemistryStatus.NOT_APPLICABLE,
                stereochemistry_descriptor=stereochemistry_descriptor,
            )
        )
    active_moiety = (
        (
            ActiveMoietyMapping(
                "DEEP-ACTIVE-MOIETY-B",
                IdentityRelationshipType.DELIVERS_ACTIVE_MOIETY,
                identity_sources[0].source_record_id,
                identity_spans[0].evidence_span_id,
                "Exact salt scope only; evidence transfer is not permitted.",
            ),
        )
        if entity_kind is ChemicalEntityKind.SALT
        else ()
    )
    identity = normalize_authoritative_identity(
        candidate,
        raw_reported_identity=f"Exact {entity_kind.value}",
        entity_kind=entity_kind,
        registry_assertions=identity_assertions,
        compound_origin_assertions=(
            CompoundOriginAssertion(
                CompoundOrigin.SYNTHETIC,
                identity_sources[0].source_record_id,
                identity_spans[0].evidence_span_id,
                "Frozen registry reports synthetic origin.",
            ),
        ),
        human_use_status_assertions=(
            HumanUseStatusAssertion(
                HumanUseStatus.MARKETED_HUMAN_PRODUCT,
                "GB",
                "other indication",
                "2026-07-21",
                identity_sources[0].source_record_id,
                identity_spans[0].evidence_span_id,
            ),
        ),
        development_status_assertions=(
            DevelopmentStatusAssertion(
                DevelopmentStatus.APPROVED,
                "GB",
                "other indication",
                "2026-07-21",
                identity_sources[0].source_record_id,
                identity_spans[0].evidence_span_id,
            ),
        ),
        active_moiety_mappings=active_moiety,
    )

    sources = list(identity_sources)
    spans = list(identity_spans)
    evidence_records = []
    claims = []
    paths = []
    claims_by_endpoint: dict[str, list[str]] = {
        endpoint.endpoint_id: [] for endpoint in case.endpoints
    }
    for endpoint_index, endpoint in enumerate(case.endpoints):
        polarities = [ClaimPolarity.SUPPORTS]
        if endpoint_index == 0 and include_counterevidence:
            polarities.append(ClaimPolarity.REFUTES)
        endpoint_evidence_ids = []
        endpoint_claim_ids = []
        for polarity_index, polarity in enumerate(polarities):
            text = (
                f"Original study for {endpoint.endpoint_id}: exact intervention "
                f"{'supported benefit' if polarity is ClaimPolarity.SUPPORTS else 'showed no benefit'} "
                "at the reported dose and time point."
            )
            source, payloads = _deep_source(
                f"{candidate.screened_candidate_id}-{endpoint_index}-{polarity_index}", text
            )
            retained.update(payloads)
            sources.append(source)
            scope = ClaimScope(
                case_revision_id=case.case_revision_id,
                population="Adults with target disease",
                disease_stage="established disease",
                tissue_or_cell_type="systemic",
                dose_or_concentration="10 mg daily",
                administration_route="oral",
                duration_or_timepoint="24 weeks",
                endpoint_id=endpoint.endpoint_id,
            )
            core = make_atomic_claim_core(
                candidate_id=candidate.screened_candidate_id,
                proposition=(
                    "The exact intervention supports the declared endpoint."
                    if polarity is ClaimPolarity.SUPPORTS
                    else "The exact intervention did not improve the declared endpoint."
                ),
                polarity=polarity,
                reporting_status=ClaimReportingStatus.REPORTED,
                evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
                scope=scope,
                calibration=(
                    ClaimCalibration.SUPPORTED_WITH_QUALIFIER
                    if polarity is ClaimPolarity.SUPPORTS
                    else ClaimCalibration.CONTRADICTED
                ),
                uncertainty=("Single frozen development study.",),
            )
            span = make_evidence_span(
                source,
                claim_id=core.claim_id,
                support_kind=EvidenceSupportKind.EXACT_EXCERPT,
                stable_locator=f"results/{endpoint.endpoint_id}/{polarity.value}",
                exact_excerpt=text,
            )
            spans.append(span)
            missing_effect = endpoint_index == 1 and polarity is ClaimPolarity.SUPPORTS
            evidence = make_deep_evidence_record(
                core,
                source,
                span,
                study_design=StudyDesign.RANDOMIZED_CONTROLLED_TRIAL,
                population_or_experimental_model=PopulationOrExperimentalModel(
                    ExperimentalModelKind.HUMAN,
                    "Adults with target disease",
                    "Homo sapiens",
                    "prespecified case population",
                    "established disease",
                ),
                sample_size=reported_quantity("80", "participants"),
                comparator=Comparator(
                    ReportedValueStatus.REPORTED,
                    "placebo",
                    "matched schedule",
                ),
                dose=reported_quantity("10", "mg/day"),
                administration_route=reported_text("oral"),
                duration=reported_text("24 weeks"),
                tissue_or_cell_type=reported_text("systemic"),
                exposure_or_concentration=reported_quantity("5", "uM"),
                endpoint_measure=f"measure for {endpoint.endpoint_id}",
                effect_direction=(
                    ObservedEffectDirection.BENEFIT
                    if polarity is ClaimPolarity.SUPPORTS
                    else ObservedEffectDirection.NO_EFFECT
                ),
                effect_magnitude=(
                    EffectMagnitude(
                        ReportedValueStatus.NOT_REPORTED,
                        None,
                        None,
                        None,
                        "week 24",
                        "not reported",
                    )
                    if missing_effect
                    else EffectMagnitude(
                        ReportedValueStatus.REPORTED,
                        "relative change",
                        "-15" if polarity is ClaimPolarity.SUPPORTS else "0",
                        "%",
                        "week 24",
                        "prespecified",
                    )
                ),
                statistical_uncertainty=(
                    StatisticalUncertainty(
                        ReportedValueStatus.NOT_REPORTED,
                        None,
                        None,
                        None,
                        "not reported",
                    )
                    if missing_effect
                    else StatisticalUncertainty(
                        ReportedValueStatus.REPORTED,
                        "95% CI -25% to -5%" if polarity is ClaimPolarity.SUPPORTS else "95% CI -5% to 5%",
                        None,
                        "0.02" if polarity is ClaimPolarity.SUPPORTS else "0.90",
                        "prespecified",
                    )
                ),
                study_limitations=("Frozen development evidence.",),
                risk_of_bias_assessment=RiskOfBiasAssessment(
                    RiskOfBiasLevel.SOME_CONCERNS,
                    "RoB 2-like assessment",
                    ("short follow-up",),
                    "Some concerns remain explicit.",
                ),
            )
            claim = bind_atomic_claim(
                core, evidence_record_ids=(evidence.deep_evidence_record_id,)
            )
            evidence_records.append(evidence)
            claims.append(claim)
            endpoint_evidence_ids.append(evidence.deep_evidence_record_id)
            endpoint_claim_ids.append(claim.claim_id)
            claims_by_endpoint[endpoint.endpoint_id].append(claim.claim_id)
        route = next(
            row for row in candidate.structured_routes if row.endpoint_id == endpoint.endpoint_id
        )
        paths.append(
            make_deep_evidence_path(
                candidate_id=candidate.screened_candidate_id,
                structured_route_id=route.route_id,
                endpoint_id=endpoint.endpoint_id,
                claim_ids=endpoint_claim_ids,
                evidence_record_ids=endpoint_evidence_ids,
            )
        )

    safety_text = "Original safety and exposure record for the exact intervention."
    safety_source, safety_payloads = _deep_source(
        f"{candidate.screened_candidate_id}-SAFETY-EXPOSURE", safety_text
    )
    retained.update(safety_payloads)
    safety_span = make_evidence_span(
        safety_source,
        claim_id=f"SAFETY-EXPOSURE-{candidate.screened_candidate_id}",
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="safety-exposure",
        exact_excerpt=safety_text,
    )
    sources.append(safety_source)
    spans.append(safety_span)
    package = make_deep_evidence_package(
        candidate,
        identity_records=(identity,),
        current_identity_record_id=identity.identity_record_id,
        sources=sources,
        evidence_spans=spans,
        evidence_records=evidence_records,
        claims=claims,
        paths=paths,
        endpoint_assessments=tuple(
            DeepEndpointAssessment(
                endpoint.endpoint_id,
                EndpointDeepStatus.ASSESSED,
                "Endpoint received complete claim-specific deep treatment.",
                tuple(sorted(claims_by_endpoint[endpoint.endpoint_id])),
            )
            for endpoint in case.endpoints
        ),
    )
    exact_id = identity.normalized_intervention_id
    assert exact_id is not None
    exposure = make_exposure_evidence(
        candidate_id=candidate.screened_candidate_id,
        exact_intervention_id=exact_id,
        dose=reported_quantity("10", "mg/day"),
        dose_context=(
            DoseContext.EXCEEDS_TOLERATED if unsafe else DoseContext.CLINICALLY_ATTAINABLE
        ),
        administration_route="oral",
        duration="24 weeks",
        population="case population",
        target_tissue="target tissue",
        tissue_applicability=TissueApplicability.MATCHED,
        pk_basis=PharmacokineticBasis.MEASURED_HUMAN,
        achieved_concentration=reported_quantity("0.1" if unsafe else "10", "uM"),
        required_effect_concentration=reported_quantity("10" if unsafe else "2", "uM"),
        source_record_ids=(safety_source.source_record_id,),
        evidence_span_ids=(safety_span.evidence_span_id,),
    )
    safety = make_safety_evidence(
        candidate_id=candidate.screened_candidate_id,
        exact_intervention_id=exact_id,
        evidence_kind=(
            SafetyEvidenceKind.CONTRAINDICATION
            if unsafe
            else SafetyEvidenceKind.ADVERSE_EVENT
        ),
        finding=SafetyFinding.RISK if unsafe else SafetyFinding.NO_MATERIAL_RISK,
        severity=SafetySeverity.SERIOUS if unsafe else SafetySeverity.NONE,
        causality=SafetyCausality.ESTABLISHED,
        frequency=FrequencyBand.COMMON if unsafe else FrequencyBand.NOT_REPORTED,
        case_applicability=CaseApplicability.DIRECT,
        dose=reported_quantity("10", "mg/day"),
        administration_route="oral",
        duration="24 weeks",
        population="case population",
        reversibility="unknown" if unsafe else "reversible",
        finding_code="CASE-CONTRAINDICATION" if unsafe else "NO-MATERIAL-RISK",
        source_record_ids=(safety_source.source_record_id,),
        evidence_span_ids=(safety_span.evidence_span_id,),
    )
    ancestry = [
        EvidenceAncestry(
            evidence_record_id=record.deep_evidence_record_id,
            source_ids=(record.source_id,),
            cohort_ids=(f"COHORT-{record.deep_evidence_record_id}",),
            laboratory_ids=(f"LAB-{record.deep_evidence_record_id}",),
            dataset_ids=(f"DATA-{record.deep_evidence_record_id}",),
            common_ancestry_ids=(),
        )
        for record in evidence_records
    ]
    counter = []
    for endpoint in case.endpoints:
        counter_claim_ids = sorted(
            claim.claim_id
            for claim in claims
            if claim.scope.endpoint_id == endpoint.endpoint_id
            and claim.polarity in {ClaimPolarity.REFUTES, ClaimPolarity.NULL, ClaimPolarity.MIXED}
        )
        counter.append(
            {
                "endpoint_id": endpoint.endpoint_id,
                "status": "present" if counter_claim_ids else "searched_none_identified",
                "claim_ids": counter_claim_ids,
                "source_record_ids": [
                    record.source_record_id
                    for record in evidence_records
                    if record.endpoint_id == endpoint.endpoint_id
                ],
                "search_scope": "Frozen endpoint-specific support and counterevidence sources.",
                "reason": "Counterevidence retained." if counter_claim_ids else "No counterclaim identified in the frozen scope.",
            }
        )
    applicability = []
    for record in evidence_records:
        applicability.append(
            {
                "evidence_record_id": record.deep_evidence_record_id,
                "axes": {
                    axis: {
                        "status": "direct",
                        "applicability_claim_id": None,
                        "reason": f"The frozen study directly matches the declared {axis} scope.",
                    }
                    for axis in (
                        "identity",
                        "species",
                        "population",
                        "disease_stage",
                        "tissue",
                        "dose_route",
                        "duration_timepoint",
                        "endpoint",
                    )
                },
                "reason": "All transfer axes are explicitly assessed.",
                "uncertainty": ["Frozen development evidence remains subject to audit."],
            }
        )
    return {
        "candidate_id": candidate.screened_candidate_id,
        "status": "completed",
        "reason": "Complete deep package passed exact identity and original-content checks.",
        "package": package,
        "retained_payloads": retained,
        "primary_endpoint_id": case.endpoints[0].endpoint_id,
        "ancestry": ancestry,
        "exposure": [exposure],
        "safety": [safety],
        "literature_landscape": LiteratureLandscape(
            direct_target_disease_publication_count=0 if not unsafe else 250,
            direct_target_disease_trial_count=1,
            development_in_target_disease=TargetDiseaseDevelopment.CLINICAL,
            earliest_direct_evidence_year=2025,
            source_record_ids=(safety_source.source_record_id,),
        ),
        "scope_eligibility": ScopeEligibility.ELIGIBLE.value,
        "scope_reason": "Exact intervention remains within declared pharmacologic scope.",
        "explicit_uncertainties": ["Scientific audit remains downstream."],
        "expert_assessments": [],
        "counterevidence_assessments": counter,
        "applicability_assessments": applicability,
        "missing_fields": [],
    }


def make_production_fixture(
    root: Path,
    *,
    first_deep_stereochemistry_descriptor: str = "not_applicable",
    first_deep_counterevidence: bool = True,
    second_deep_unsafe: bool = True,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    case = make_case()
    admitted, seeds = make_admitted_frame(case, root / "disposition")
    admit_by_seed = {
        row["seed_id"]: row
        for row in admitted["seed_dispositions"]
        if row["canonical_disposition"] == "admit"
    }
    admit_rows = [admit_by_seed[row.seed_id] for row in seeds]
    structure_by_seed = {
        row.seed_id: structure for row, structure in zip(seeds, STRUCTURES)
    }
    screens = [
        _screen(case, admit_rows[0], index=0, statuses=("supportive", "supportive")),
        _screen(case, admit_rows[1], index=1, statuses=("supportive", "supportive")),
        _screen(
            case,
            admit_rows[2],
            index=2,
            statuses=("insufficient", "insufficient"),
            exposure="unknown",
            safety="unknown",
            readiness="preclinical_only",
        ),
        _screen(
            case,
            admit_rows[3],
            index=3,
            statuses=("supportive", "neutral"),
            exposure="infeasible",
        ),
        _screen(
            case,
            admit_rows[4],
            index=4,
            statuses=("supportive", "neutral"),
            safety="conflicting",
        ),
    ]
    policy = {
        "policy_version": DEEP_SELECTION_POLICY_VERSION,
        "capacity": 2,
        "allocation_rule": "round_robin_declared_strata",
        "tie_rule": "candidate_id_ascending",
        "strata": [
            {"stratum_id": "supportive_or_mixed_evidence", "capacity": 2},
            {"stratum_id": "sparse_or_unknown_evidence", "capacity": 1},
            {"stratum_id": "preclinical_only", "capacity": 0},
        ],
    }
    # Candidate IDs are deterministic from Stage 4, so deep packages can be frozen
    # only after the complete screen frame and rule are known.
    passing = [
        build_screened_candidate(case, admitted, row["normalized_intervention_id"])
        for row in admit_rows[:3]
    ]
    supportive = sorted(passing[:2], key=lambda row: row.screened_candidate_id)
    deep_results = [
        make_deep_result(
            case,
            supportive[0],
            structure_by_seed[supportive[0].representative_seed_id],
            unsafe=False,
            include_counterevidence=first_deep_counterevidence,
            stereochemistry_descriptor=first_deep_stereochemistry_descriptor,
        ),
        make_deep_result(
            case,
            supportive[1],
            structure_by_seed[supportive[1].representative_seed_id],
            unsafe=second_deep_unsafe,
            include_counterevidence=False,
        ),
    ]
    frozen = {
        "evidence_revision": "production-screen-deep-r1",
        "screen_rule_version": SCREEN_RULE_VERSION,
        "candidate_screens": screens,
        "deep_selection_policy": policy,
        "deep_results": deep_results,
    }
    return case, admitted, frozen


class ProductionScreenDeepAggregateTests(unittest.TestCase):
    def run_adapter(self) -> tuple[Any, dict[str, Any], dict[str, Any], V7ScreenDeepAdapter, dict[str, Any]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, admitted, frozen = make_production_fixture(root)
        adapter = V7ScreenDeepAdapter(root / "screen-deep")
        aggregate = dict(adapter.screen_and_deepen(case, admitted, frozen))
        validate_screen_deep_aggregate(case, aggregate)
        return case, admitted, frozen, adapter, aggregate

    def test_full_screen_deep_reconciliation_and_scientific_boundaries(self) -> None:
        case, _, _, adapter, aggregate = self.run_adapter()
        counts = aggregate["reconciliation"]
        self.assertEqual(
            {
                name: counts[name]
                for name in (
                    "N_admit",
                    "N_screened",
                    "N_screen_rejected",
                    "N_screen_quarantined",
                    "N_screen_failed",
                    "N_selected_deep",
                    "N_screen_only",
                    "N_deep",
                    "N_deep_quarantined",
                    "N_deep_failed",
                )
            },
            {
                "N_admit": 5,
                "N_screened": 3,
                "N_screen_rejected": 1,
                "N_screen_quarantined": 1,
                "N_screen_failed": 0,
                "N_selected_deep": 2,
                "N_screen_only": 1,
                "N_deep": 2,
                "N_deep_quarantined": 0,
                "N_deep_failed": 0,
            },
        )
        self.assertTrue(counts["screen_equation_balanced"])
        self.assertTrue(counts["selection_equation_balanced"])
        self.assertTrue(counts["deep_equation_balanced"])
        self.assertTrue(aggregate["stage_gate_passed"])
        admitted_ids = {
            row["normalized_intervention_id"]
            for row in aggregate["retained_inputs"]["admitted_frame"]["normalized_interventions"]
        }
        self.assertTrue(
            all(
                row["deep_candidate"]["normalized_intervention_id"] in admitted_ids
                for row in aggregate["deep_packages"]
            )
        )
        self.assertTrue(
            adapter.selection_path(
                case.case_revision_id, aggregate["screen_deep_plan_id"]
            ).is_file()
        )
        self.assertEqual(
            {
                assessment["endpoint_id"]
                for row in aggregate["screen_records"]
                for assessment in row["screening_decision"]["endpoint_assessments"]
            },
            {endpoint.endpoint_id for endpoint in case.endpoints},
        )
        self.assertEqual(len(aggregate["deep_selection"]["ties"]), 1)
        self.assertFalse(
            aggregate["deep_selection"]["popularity_or_publication_count_used"]
        )
        self.assertEqual(
            aggregate["deep_selection"]["screen_only"][0]["scientific_rejection"],
            False,
        )
        deep_rows = aggregate["deep_packages"]
        self.assertEqual(
            {row["package"]["identity_records"][0]["entity_kind"] for row in deep_rows},
            {"single_compound", "salt"},
        )
        self.assertTrue(
            any(
                endpoint["status"] == "present"
                for row in deep_rows
                for endpoint in row["counterevidence_assessments"]
            )
        )
        self.assertTrue(
            all(
                len(row["applicability_assessments"])
                == len(row["package"]["evidence_records"])
                for row in deep_rows
            )
        )
        self.assertTrue(
            any(row["missingness_records"] for row in deep_rows)
        )
        self.assertTrue(
            all(
                set(row["separate_decision_outputs"])
                >= {
                    "therapeutic_support",
                    "evidence_quality",
                    "readiness",
                    "novelty",
                    "uncertainty",
                    "information_value",
                    "portfolio_diversity",
                }
                for row in deep_rows
            )
        )
        self.assertTrue(
            all(
                not row["identity_bridge"]["automatic_evidence_transfer_permitted"]
                for row in deep_rows
            )
        )
        profiles = [row["candidate_decision_profile"] for row in deep_rows]
        self.assertIn("infeasible", {row["exposure_feasibility"]["band"] for row in profiles})
        self.assertIn(
            "serious_mismatch", {row["safety_and_tolerability"]["band"] for row in profiles}
        )

    def test_replay_is_order_independent_and_same_revision_drift_is_refused(self) -> None:
        case, admitted, frozen, adapter, aggregate = self.run_adapter()
        path = adapter.aggregate_path(case.case_revision_id, aggregate["screen_deep_plan_id"])
        before_bytes = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        replay_input = copy.deepcopy(frozen)
        replay_input["candidate_screens"].reverse()
        replay_input["deep_results"].reverse()
        replay = adapter.screen_and_deepen(case, admitted, replay_input)
        self.assertEqual(aggregate, replay)
        self.assertEqual(before_bytes, path.read_bytes())
        self.assertEqual(before_mtime, path.stat().st_mtime_ns)
        drift = copy.deepcopy(frozen)
        drift["deep_selection_policy"]["capacity"] = 1
        with self.assertRaises(ScreenDeepAggregateConflictError):
            adapter.screen_and_deepen(case, admitted, drift)

    def test_broken_original_content_grounding_fails_after_selection_freeze(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, admitted, frozen = make_production_fixture(root)
        first = frozen["deep_results"][0]
        locator = next(iter(first["retained_payloads"]))
        first["retained_payloads"][locator] = b"tampered retained original content"
        adapter = V7ScreenDeepAdapter(root / "screen-deep")
        with self.assertRaisesRegex(Exception, "payload hash mismatch|absent from retained"):
            adapter.screen_and_deepen(case, admitted, frozen)
        plan_roots = list((root / "screen-deep" / case.case_revision_id).glob("*"))
        self.assertEqual(len(plan_roots), 1)
        self.assertTrue((plan_roots[0] / "deep_selection.json").is_file())
        self.assertFalse((plan_roots[0] / "aggregate.json").exists())

    def test_missing_screen_and_unstructured_safety_or_exposure_fail_closed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, admitted, frozen = make_production_fixture(root)
        missing_screen = copy.deepcopy(frozen)
        missing_screen["evidence_revision"] = "missing-screen"
        missing_screen["candidate_screens"].pop()
        with self.assertRaisesRegex(ScreenDeepAggregateError, "exactly one record"):
            V7ScreenDeepAdapter(root / "missing").screen_and_deepen(
                case, admitted, missing_screen
            )
        missing_safety = copy.deepcopy(frozen)
        missing_safety["evidence_revision"] = "missing-safety"
        missing_safety["deep_results"][0]["safety"] = []
        with self.assertRaisesRegex(ScreenDeepAggregateError, "structured exposure and safety"):
            V7ScreenDeepAdapter(root / "missing-safety").screen_and_deepen(
                case, admitted, missing_safety
            )

    def test_exact_stereochemistry_descriptor_drift_fails_closed(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, admitted, frozen = make_production_fixture(
            root, first_deep_stereochemistry_descriptor="descriptor-drift"
        )
        with self.assertRaisesRegex(ScreenDeepAggregateError, "structure/stereochemistry"):
            V7ScreenDeepAdapter(root / "descriptor-drift").screen_and_deepen(
                case, admitted, frozen
            )

    def test_exact_formulation_bridge_uses_product_and_component_identity(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case, admitted, _ = make_production_fixture(root)
        admitted_row = next(
            row
            for row in admitted["seed_dispositions"]
            if row["canonical_disposition"] == "admit"
        )
        candidate = build_screened_candidate(
            case, admitted, admitted_row["normalized_intervention_id"]
        )
        stage4_component = {
            "component_namespace": "FROZEN-COMPONENT",
            "component_identifier": "CMP-1",
            "component_entity_kind": "single_compound",
            "role": "active",
            "amount_or_fraction": "10 mg",
        }
        component_token = _stage4_component_bridge_id(stage4_component)
        assertions = tuple(
            make_registry_identity_assertion(
                authority=f"Product Registry {suffix}",
                authority_release="2026-07-21",
                source_record_id=f"SRC-PRODUCT-{suffix}",
                evidence_span_id=f"SPAN-PRODUCT-{suffix}",
                entity_kind=ChemicalEntityKind.FORMULATION,
                registry_identifiers={"FROZEN-PRODUCT": "PROD-1"},
                canonical_smiles=None,
                standard_inchi=None,
                inchikey=None,
                stereochemistry_status=StereochemistryStatus.NOT_APPLICABLE,
                stereochemistry_descriptor="not_applicable",
            )
            for suffix in ("A", "B")
        )
        identity = normalize_authoritative_identity(
            candidate,
            raw_reported_identity="Exact Product One",
            entity_kind=ChemicalEntityKind.FORMULATION,
            registry_assertions=assertions,
            composition_status=CompositionStatus.EXACT,
            components=(
                CompositionComponent(
                    component_token,
                    "active",
                    "10 mg",
                    "SRC-PRODUCT-A",
                    "SPAN-PRODUCT-A",
                ),
            ),
            formulation=FormulationDescriptor(
                "Exact Product One",
                "tablet",
                "immediate_release",
                ("oral",),
                (component_token,),
                "SRC-PRODUCT-A",
                "SPAN-PRODUCT-A",
            ),
        )
        stage4 = {
            "normalized_intervention_id": "STAGE4-FORMULATION-1",
            "entity_kind": "formulation",
            "registry_identifiers": [
                {"namespace": "FROZEN-PRODUCT", "identifier": "PROD-1"}
            ],
            "canonical_structure": None,
            "composition_status": "exact",
            "components": [stage4_component],
            "product": {
                "product_namespace": "FROZEN-PRODUCT",
                "product_identifier": "PROD-1",
                "dosage_form": "tablet",
                "release_characteristic": "immediate_release",
                "administration_routes": ["oral"],
            },
            "source_reported_identities": ["Exact Product One"],
            "resolver_assertion_ids": ["STAGE4-ASSERTION-1"],
        }
        bridge = _exact_identity_bridge(stage4, identity)
        self.assertEqual(
            bridge["match_basis"],
            "exact_product_identifier_and_qualified_components_with_grounded_deep_details",
        )
        self.assertFalse(bridge["automatic_evidence_transfer_permitted"])
        self.assertEqual(bridge["deep_component_details"][0]["role"], "active")
        self.assertEqual(
            bridge["deep_component_details"][0]["amount_or_fraction"], "10 mg"
        )
        mismatched = copy.deepcopy(stage4)
        mismatched["product"]["administration_routes"] = ["intravenous"]
        with self.assertRaisesRegex(
            ScreenDeepAggregateError, "product/formulation attributes"
        ):
            _exact_identity_bridge(mismatched, identity)
        mismatched = copy.deepcopy(stage4)
        mismatched["components"][0]["role"] = "excipient"
        with self.assertRaisesRegex(
            ScreenDeepAggregateError, "component role or amount/fraction"
        ):
            _exact_identity_bridge(mismatched, identity)
        mismatched = copy.deepcopy(stage4)
        mismatched["components"][0]["amount_or_fraction"] = "999 mg"
        with self.assertRaisesRegex(
            ScreenDeepAggregateError, "component role or amount/fraction"
        ):
            _exact_identity_bridge(mismatched, identity)
        mismatched = copy.deepcopy(stage4)
        mismatched["components"][0]["component_entity_kind"] = "salt"
        with self.assertRaisesRegex(ScreenDeepAggregateError, "component identities"):
            _exact_identity_bridge(mismatched, identity)


if __name__ == "__main__":
    unittest.main(verbosity=2)
