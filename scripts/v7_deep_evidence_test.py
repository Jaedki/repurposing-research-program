#!/usr/bin/env python3
"""Focused offline tests for schema-v7 deep evidence and identity."""

from __future__ import annotations

import unittest
from dataclasses import replace

from v7_deep_evidence import (
    ActiveMoietyMapping,
    ChemicalEntityKind,
    ClaimCalibration,
    ClaimPolarity,
    ClaimReportingStatus,
    ClaimScope,
    Comparator,
    CompositionStatus,
    CompoundOrigin,
    CompoundOriginAssertion,
    ContentVerificationMethod,
    CorrectionAction,
    CorrectionTargetKind,
    DeepEndpointAssessment,
    DeepEvidenceError,
    DevelopmentStatusAssertion,
    EffectMagnitude,
    EndpointDeepStatus,
    EvidenceSupportKind,
    ExperimentalModelKind,
    HumanUseStatus,
    HumanUseStatusAssertion,
    IdentityRelationship,
    IdentityRelationshipType,
    IdentityResolutionStatus,
    ObservedEffectDirection,
    PopulationOrExperimentalModel,
    ReportedValueStatus,
    RetrievalMethod,
    RiskOfBiasAssessment,
    RiskOfBiasLevel,
    SourceContentScope,
    StatisticalUncertainty,
    StereochemistryStatus,
    StructuredEvidencePointer,
    StudyDesign,
    VerificationMode,
    bind_atomic_claim,
    make_atomic_claim_core,
    make_deep_evidence_package,
    make_deep_evidence_path,
    make_deep_evidence_record,
    make_deep_source_record,
    make_evidence_span,
    make_record_correction,
    make_registry_identity_assertion,
    normalize_authoritative_identity,
    promote_deep_candidate,
    reported_quantity,
    reported_text,
    validate_deep_evidence_package,
    validate_evidence_span,
)
from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
    make_structured_route,
    not_applicable_node,
    known_node,
)
from v7_seed_funnel import ScreenedCandidateRecord, SeedIdentityStatus


ENDPOINT_ID = "ENDPOINT-TEST-BENEFIT"
CASE_REVISION_ID = "CASE-REVISION-TEST"
ETHANOL_KEY = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
ETHANOL_SMILES = "CCO"
ETHANOL_INCHI = "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"


def _candidate(suffix: str = "A") -> ScreenedCandidateRecord:
    candidate_id = f"SCREENED-CANDIDATE-{suffix}"
    route = make_structured_route(
        case_revision_id=CASE_REVISION_ID,
        intervention_id=candidate_id,
        causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
        disease_state_node=known_node("DISEASE:TEST", "test disease"),
        intervention_target=known_node("TARGET:TEST", "test target"),
        action=InterventionAction.INHIBIT,
        direction=EffectDirection.DECREASE,
        intermediate_state=not_applicable_node("Direct route."),
        endpoint_id=ENDPOINT_ID,
        evidence_ids=("EVIDENCE-DISCOVERY-1",),
    )
    return ScreenedCandidateRecord(
        screened_candidate_id=candidate_id,
        case_id="CASE-TEST",
        case_revision_id=CASE_REVISION_ID,
        lane="repurposing",
        screening_intervention_id=f"PROVISIONAL-{suffix}",
        verified_normalized_intervention_id=None,
        active_moiety_id=None,
        identity_status=SeedIdentityStatus.PROVISIONAL,
        identity_verified=False,
        representative_seed_id=f"SEED-{suffix}",
        endpoint_ids=(ENDPOINT_ID,),
        structured_routes=(route,),
        evidence_modalities=(EvidenceModality.CLINICAL_INTERVENTION,),
        chemical_universes=(ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,),
        source_seed_ids=(f"SEED-{suffix}",),
        source_mapping_ids=(f"MAP-{suffix}",),
        discovery_route_ids=(f"DISCOVERY-ROUTE-{suffix}",),
        alias_ids=(),
    )


def _source(
    label: str,
    payload: bytes,
    *,
    scope: SourceContentScope = SourceContentScope.ORIGINAL_DATABASE_RECORD,
) -> tuple[object, dict[str, bytes]]:
    locator = f"raw/{label}.txt"
    source = make_deep_source_record(
        source_id=f"SOURCE-{label}",
        source_release="2026-07-20",
        native_record_id=f"NATIVE-{label}",
        retrieval_content_receipt_id=f"RECEIPT-{label}",
        retained_payload_locator=locator,
        raw_content=payload,
        content_scope=scope,
        retrieval_method=RetrievalMethod.LOCAL_FROZEN_FIXTURE,
        verification_method=ContentVerificationMethod.RETAINED_PAYLOAD_SHA256,
    )
    return source, {locator: payload}


def _identity_sources(
    candidate: ScreenedCandidateRecord,
    *,
    key: str = ETHANOL_KEY,
    smiles: str = ETHANOL_SMILES,
    inchi: str = ETHANOL_INCHI,
    entity_kind: ChemicalEntityKind = ChemicalEntityKind.SINGLE_COMPOUND,
    stereochemistry_status: StereochemistryStatus = StereochemistryStatus.NOT_APPLICABLE,
    stereochemistry_descriptor: str = "not_applicable",
    authority_count: int = 2,
    source_scope: SourceContentScope = SourceContentScope.ORIGINAL_DATABASE_RECORD,
    active_moiety_mappings=(),
):
    payload_a = (
        f"Registry A identifier A-1 exact identity: {smiles}; {inchi}; {key}. "
        "Synthetic origin; human marketed use."
    ).encode()
    payload_b = f"Registry B identifier B-1 exact identity: {smiles}; {inchi}; {key}.".encode()
    source_a, retained_a = _source("REG-A-" + candidate.screened_candidate_id, payload_a, scope=source_scope)
    source_b, retained_b = _source("REG-B-" + candidate.screened_candidate_id, payload_b)
    span_a = make_evidence_span(
        source_a,
        claim_id="IDENTITY-CLAIM-" + candidate.screened_candidate_id,
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="identity-line-a",
        exact_excerpt=payload_a.decode(),
    )
    span_b = make_evidence_span(
        source_b,
        claim_id="IDENTITY-CLAIM-" + candidate.screened_candidate_id,
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="identity-line-b",
        exact_excerpt=payload_b.decode(),
    )
    assertion_a = make_registry_identity_assertion(
        authority="Registry A",
        authority_release="2026-07-20",
        source_record_id=source_a.source_record_id,
        evidence_span_id=span_a.evidence_span_id,
        entity_kind=entity_kind,
        registry_identifiers={"REG-A": "A-1"},
        canonical_smiles=smiles,
        standard_inchi=inchi,
        inchikey=key,
        stereochemistry_status=stereochemistry_status,
        stereochemistry_descriptor=stereochemistry_descriptor,
    )
    assertion_b = make_registry_identity_assertion(
        authority="Registry B",
        authority_release="2026-07-20",
        source_record_id=source_b.source_record_id,
        evidence_span_id=span_b.evidence_span_id,
        entity_kind=entity_kind,
        registry_identifiers={"REG-B": "B-1"},
        canonical_smiles=smiles,
        standard_inchi=inchi,
        inchikey=key,
        stereochemistry_status=stereochemistry_status,
        stereochemistry_descriptor=stereochemistry_descriptor,
    )
    assertions = (assertion_a, assertion_b)[:authority_count]
    identity = normalize_authoritative_identity(
        candidate,
        raw_reported_identity="Test exact intervention",
        entity_kind=entity_kind,
        registry_assertions=assertions,
        active_moiety_mappings=active_moiety_mappings,
        compound_origin_assertions=(
            CompoundOriginAssertion(
                CompoundOrigin.SYNTHETIC,
                source_a.source_record_id,
                span_a.evidence_span_id,
                "Registry describes synthetic origin.",
            ),
        ),
        human_use_status_assertions=(
            HumanUseStatusAssertion(
                HumanUseStatus.MARKETED_HUMAN_PRODUCT,
                "GB",
                "other indication",
                "2026-07-20",
                source_a.source_record_id,
                span_a.evidence_span_id,
            ),
        ),
        development_status_assertions=(
            DevelopmentStatusAssertion(
                DevelopmentStatus.APPROVED,
                "GB",
                "other indication",
                "2026-07-20",
                source_a.source_record_id,
                span_a.evidence_span_id,
            ),
        ),
    )
    return identity, (source_a, source_b), (span_a, span_b), {**retained_a, **retained_b}


def _package(
    *,
    identity_authority_count: int = 2,
    key: str = ETHANOL_KEY,
    identity_scope: SourceContentScope = SourceContentScope.ORIGINAL_DATABASE_RECORD,
):
    candidate = _candidate()
    identity, identity_sources, identity_spans, retained = _identity_sources(
        candidate,
        authority_count=identity_authority_count,
        key=key,
        source_scope=identity_scope,
    )
    study_payload = b"Original study result: treatment reduced the endpoint by 20% versus placebo."
    study_source, study_retained = _source("STUDY", study_payload, scope=SourceContentScope.ORIGINAL_FULL_TEXT)
    retained.update(study_retained)
    scope = ClaimScope(
        case_revision_id=CASE_REVISION_ID,
        population="Adults with test disease",
        disease_stage="established disease",
        tissue_or_cell_type="systemic",
        dose_or_concentration="10 mg daily",
        administration_route="oral",
        duration_or_timepoint="28 days",
        endpoint_id=ENDPOINT_ID,
    )
    core = make_atomic_claim_core(
        candidate_id=candidate.screened_candidate_id,
        proposition="The exact intervention reduced the prespecified endpoint versus placebo.",
        polarity=ClaimPolarity.SUPPORTS,
        reporting_status=ClaimReportingStatus.REPORTED,
        evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
        scope=scope,
        calibration=ClaimCalibration.SUPPORTED_WITH_QUALIFIER,
        uncertainty=("Single synthetic development fixture.",),
    )
    study_span = make_evidence_span(
        study_source,
        claim_id=core.claim_id,
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="results/primary-endpoint",
        exact_excerpt=study_payload.decode(),
    )
    evidence = make_deep_evidence_record(
        core,
        study_source,
        study_span,
        study_design=StudyDesign.RANDOMIZED_CONTROLLED_TRIAL,
        population_or_experimental_model=PopulationOrExperimentalModel(
            ExperimentalModelKind.HUMAN,
            "Adults with test disease",
            "Homo sapiens",
            "prespecified inclusion criteria",
            "established disease",
        ),
        sample_size=reported_quantity("120", "participants"),
        comparator=Comparator(
            ReportedValueStatus.REPORTED,
            "placebo",
            "matched treatment schedule",
        ),
        dose=reported_quantity("10", "mg/day"),
        administration_route=reported_text("oral"),
        duration=reported_text("28 days"),
        tissue_or_cell_type=reported_text("systemic"),
        exposure_or_concentration=reported_quantity("10", "mg/day administered"),
        endpoint_measure="mean relative endpoint change",
        effect_direction=ObservedEffectDirection.BENEFIT,
        effect_magnitude=EffectMagnitude(
            ReportedValueStatus.REPORTED,
            "relative change",
            "-20",
            "%",
            "day 28",
            "unadjusted",
        ),
        statistical_uncertainty=StatisticalUncertainty(
            ReportedValueStatus.REPORTED,
            "95% CI -30% to -10%",
            None,
            "0.01",
            "prespecified analysis",
        ),
        study_limitations=("Short follow-up.",),
        risk_of_bias_assessment=RiskOfBiasAssessment(
            RiskOfBiasLevel.SOME_CONCERNS,
            "RoB 2-like fixture assessment",
            ("missing outcome data", "randomization process"),
            "Short follow-up limits directness.",
        ),
    )
    claim = bind_atomic_claim(core, evidence_record_ids=(evidence.deep_evidence_record_id,))
    path = make_deep_evidence_path(
        candidate_id=candidate.screened_candidate_id,
        structured_route_id=candidate.structured_routes[0].route_id,
        endpoint_id=ENDPOINT_ID,
        claim_ids=(claim.claim_id,),
        evidence_record_ids=(evidence.deep_evidence_record_id,),
    )
    package = make_deep_evidence_package(
        candidate,
        identity_records=(identity,),
        current_identity_record_id=identity.identity_record_id,
        sources=(*identity_sources, study_source),
        evidence_spans=(*identity_spans, study_span),
        evidence_records=(evidence,),
        claims=(claim,),
        paths=(path,),
        endpoint_assessments=(
            DeepEndpointAssessment(
                ENDPOINT_ID,
                EndpointDeepStatus.ASSESSED,
                "Endpoint has claim-specific original-content evidence.",
                (claim.claim_id,),
            ),
        ),
    )
    return package, retained


class DeepEvidenceIdentityTests(unittest.TestCase):
    def test_complete_deep_record_promotes_only_with_original_content(self):
        package, retained = _package()
        validate_deep_evidence_package(
            package,
            verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
            retained_payloads=retained,
        )
        promoted = promote_deep_candidate(package, retained_payloads=retained)
        evidence = package.evidence_records[0]
        self.assertEqual(promoted.candidate_id, package.screened_candidate.screened_candidate_id)
        self.assertEqual(evidence.sample_size.value, "120")
        self.assertEqual(evidence.effect_magnitude.estimate, "-20")
        self.assertEqual(evidence.claim_calibration, ClaimCalibration.SUPPORTED_WITH_QUALIFIER)
        tampered = dict(retained)
        tampered[package.sources[-1].retained_payload_locator] = b"tampered original content"
        with self.assertRaisesRegex(DeepEvidenceError, "payload hash mismatch"):
            promote_deep_candidate(package, retained_payloads=tampered)

    def test_regex_valid_fake_inchikey_does_not_establish_identity(self):
        package, retained = _package(
            identity_authority_count=1,
            key="AAAAAAAAAAAAAA-BBBBBBBBBB-C",
        )
        identity = package.identity_records[0]
        self.assertEqual(identity.resolution_status, IdentityResolutionStatus.UNRESOLVED)
        self.assertFalse(identity.deep_identity_eligible)
        with self.assertRaisesRegex(DeepEvidenceError, "exact, authoritative"):
            promote_deep_candidate(package, retained_payloads=retained)

    def test_metadata_only_receipt_cannot_self_attest_deep_verification(self):
        package, retained = _package(identity_scope=SourceContentScope.METADATA_ONLY)
        validate_deep_evidence_package(package)
        with self.assertRaisesRegex(DeepEvidenceError, "metadata/abstract-only"):
            promote_deep_candidate(package, retained_payloads=retained)

    def test_parent_and_salt_are_distinct_but_share_verified_breadth_group(self):
        parent_candidate = _candidate("PARENT")
        parent, _, _, _ = _identity_sources(parent_candidate)
        self.assertIsNotNone(parent.normalized_intervention_id)
        salt_candidate = _candidate("SALT")
        salt_payload_a = b"Registry A SALT-A: CCO.Cl; InChI=1S/C2H6O.ClH; AAAAAAAAAAAAAA-CCCCCCCCCC-D."
        salt_payload_b = b"Registry B SALT-B: CCO.Cl; InChI=1S/C2H6O.ClH; AAAAAAAAAAAAAA-CCCCCCCCCC-D."
        source_a, _ = _source("SALT-A", salt_payload_a)
        source_b, _ = _source("SALT-B", salt_payload_b)
        span_a = make_evidence_span(
            source_a,
            claim_id="IDENTITY-SALT",
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator="salt-a",
            exact_excerpt=salt_payload_a.decode(),
        )
        span_b = make_evidence_span(
            source_b,
            claim_id="IDENTITY-SALT",
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator="salt-b",
            exact_excerpt=salt_payload_b.decode(),
        )
        assertions = tuple(
            make_registry_identity_assertion(
                authority=f"Registry {letter}",
                authority_release="2026-07-20",
                source_record_id=source.source_record_id,
                evidence_span_id=span.evidence_span_id,
                entity_kind=ChemicalEntityKind.SALT,
                registry_identifiers={f"REG-{letter}": f"SALT-{letter}"},
                canonical_smiles="CCO.Cl",
                standard_inchi="InChI=1S/C2H6O.ClH",
                inchikey="AAAAAAAAAAAAAA-CCCCCCCCCC-D",
                stereochemistry_status=StereochemistryStatus.NOT_APPLICABLE,
            )
            for letter, source, span in (("A", source_a, span_a), ("B", source_b, span_b))
        )
        mapping = ActiveMoietyMapping(
            parent.normalized_intervention_id or "",
            IdentityRelationshipType.DELIVERS_ACTIVE_MOIETY,
            source_a.source_record_id,
            span_a.evidence_span_id,
            "Exact salt delivers the parent active moiety; no evidence transfer implied.",
        )
        salt = normalize_authoritative_identity(
            salt_candidate,
            raw_reported_identity="Test compound hydrochloride",
            entity_kind=ChemicalEntityKind.SALT,
            registry_assertions=assertions,
            active_moiety_mappings=(mapping,),
            relationships=(
                IdentityRelationship(
                    IdentityRelationshipType.SALT_OF,
                    parent.normalized_intervention_id or "",
                    source_a.source_record_id,
                    span_a.evidence_span_id,
                    "Exact salt-to-parent relationship.",
                ),
            ),
        )
        self.assertNotEqual(parent.normalized_intervention_id, salt.normalized_intervention_id)
        self.assertEqual(parent.breadth_group_id, salt.breadth_group_id)

    def test_stereoisomers_remain_distinct(self):
        left, _, _, _ = _identity_sources(
            _candidate("LEFT"),
            key="HEFNNWSXXWATRW-ZDUSSCGKSA-N",
            smiles="C[C@@H](C(=O)O)c1ccc(cc1)CC(C)C",
            inchi="InChI=1S/stereo-left",
            stereochemistry_status=StereochemistryStatus.FULLY_SPECIFIED,
            stereochemistry_descriptor="S",
        )
        right, _, _, _ = _identity_sources(
            _candidate("RIGHT"),
            key="HEFNNWSXXWATRW-SNVBAGLBSA-N",
            smiles="C[C@H](C(=O)O)c1ccc(cc1)CC(C)C",
            inchi="InChI=1S/stereo-right",
            stereochemistry_status=StereochemistryStatus.FULLY_SPECIFIED,
            stereochemistry_descriptor="R",
        )
        self.assertNotEqual(left.normalized_intervention_id, right.normalized_intervention_id)
        self.assertNotEqual(left.breadth_group_id, right.breadth_group_id)

    def test_undefined_mixture_is_preserved_unresolved_without_invented_structure(self):
        candidate = _candidate("MIXTURE")
        identity = normalize_authoritative_identity(
            candidate,
            raw_reported_identity="Undefined botanical extract",
            entity_kind=ChemicalEntityKind.MIXTURE,
            registry_assertions=(),
            composition_status=CompositionStatus.UNDEFINED,
        )
        self.assertEqual(identity.retained_seed_ids, candidate.source_seed_ids)
        self.assertEqual(identity.resolution_status, IdentityResolutionStatus.UNRESOLVED)
        self.assertIsNone(identity.canonical_structure)
        self.assertIsNone(identity.normalized_intervention_id)

    def test_conflicting_authoritative_registries_force_quarantine_state(self):
        candidate = _candidate("CONFLICT")
        identity, sources, spans, _ = _identity_sources(candidate)
        first = next(row for row in identity.registry_assertions if row.authority == "Registry A")
        conflicting = make_registry_identity_assertion(
            authority="Registry B",
            authority_release="2026-07-20",
            source_record_id=sources[1].source_record_id,
            evidence_span_id=spans[1].evidence_span_id,
            entity_kind=ChemicalEntityKind.SINGLE_COMPOUND,
            registry_identifiers={"REG-B": "B-2"},
            canonical_smiles="CCN",
            standard_inchi="InChI=1S/C2H7N",
            inchikey="QUSNBJAOOMFDIB-UHFFFAOYSA-N",
            stereochemistry_status=StereochemistryStatus.NOT_APPLICABLE,
        )
        result = normalize_authoritative_identity(
            candidate,
            raw_reported_identity="Conflicting compound",
            entity_kind=ChemicalEntityKind.SINGLE_COMPOUND,
            registry_assertions=(first, conflicting),
        )
        self.assertEqual(result.resolution_status, IdentityResolutionStatus.CONFLICTING)
        self.assertTrue(result.identity_conflicts)
        self.assertFalse(result.deep_identity_eligible)

    def test_evidence_spans_are_claim_specific(self):
        package, _ = _package()
        source = package.sources[-1]
        first_claim = package.claims[0]
        other_core = make_atomic_claim_core(
            candidate_id=first_claim.candidate_id,
            proposition="A distinct claim requires its own source coordinates.",
            polarity=ClaimPolarity.NULL,
            reporting_status=ClaimReportingStatus.REPORTED,
            evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
            scope=first_claim.scope,
            calibration=ClaimCalibration.UNRESOLVED,
            uncertainty=("Fixture uncertainty.",),
        )
        wrong_span = make_evidence_span(
            source,
            claim_id=other_core.claim_id,
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator="other-claim",
            exact_excerpt="Original study result: treatment reduced the endpoint by 20% versus placebo.",
        )
        original_core = make_atomic_claim_core(
            candidate_id=first_claim.candidate_id,
            proposition=first_claim.proposition,
            polarity=first_claim.polarity,
            reporting_status=first_claim.reporting_status,
            evidence_modality=first_claim.evidence_modality,
            scope=first_claim.scope,
            calibration=first_claim.calibration,
            uncertainty=first_claim.uncertainty,
        )
        with self.assertRaisesRegex(DeepEvidenceError, "different atomic claim"):
            make_deep_evidence_record(
                original_core,
                source,
                wrong_span,
                study_design=package.evidence_records[0].study_design,
                population_or_experimental_model=package.evidence_records[0].population_or_experimental_model,
                sample_size=package.evidence_records[0].sample_size,
                comparator=package.evidence_records[0].comparator,
                dose=package.evidence_records[0].dose,
                administration_route=package.evidence_records[0].administration_route,
                duration=package.evidence_records[0].duration,
                tissue_or_cell_type=package.evidence_records[0].tissue_or_cell_type,
                exposure_or_concentration=package.evidence_records[0].exposure_or_concentration,
                endpoint_measure=package.evidence_records[0].endpoint_measure,
                effect_direction=package.evidence_records[0].effect_direction,
                effect_magnitude=package.evidence_records[0].effect_magnitude,
                statistical_uncertainty=package.evidence_records[0].statistical_uncertainty,
                study_limitations=package.evidence_records[0].study_limitations,
                risk_of_bias_assessment=package.evidence_records[0].risk_of_bias_assessment,
            )

    def test_structured_table_pointer_verifies_against_retained_original(self):
        payload = b"Table 2\nTreatment row | endpoint change | -20%\n"
        source, retained = _source(
            "TABLE-POINTER", payload, scope=SourceContentScope.ORIGINAL_TABLE
        )
        pointer = StructuredEvidencePointer(
            artifact_label="Table 2",
            page_or_section="results/table-2",
            coordinates="row 2, column 3",
            cell_or_region="Treatment row / endpoint change",
            extracted_value="-20%",
            extraction_method="frozen table parser v1",
        )
        span = make_evidence_span(
            source,
            claim_id="DEEP-CLAIM-TABLE",
            support_kind=EvidenceSupportKind.STRUCTURED_TABLE_POINTER,
            stable_locator="table-2/row-2/column-3",
            structured_pointer=pointer,
        )
        validate_evidence_span(
            span,
            source,
            verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
            retained_payloads=retained,
        )

    def test_source_and_claim_corrections_retain_history_and_supersede_cleanly(self):
        package, retained = _package()
        candidate = package.screened_candidate
        old_payload = b"Original source incorrectly reported an increase."
        new_payload = b"Corrected source reports a decrease after erratum."
        old_source, old_retained = _source("OLD-SOURCE", old_payload, scope=SourceContentScope.ORIGINAL_FULL_TEXT)
        new_source, new_retained = _source("NEW-SOURCE", new_payload, scope=SourceContentScope.ORIGINAL_FULL_TEXT)
        retained.update(old_retained)
        retained.update(new_retained)
        scope = package.claims[0].scope
        old_core = make_atomic_claim_core(
            candidate_id=candidate.screened_candidate_id,
            proposition="The source reported an increase before correction.",
            polarity=ClaimPolarity.REFUTES,
            reporting_status=ClaimReportingStatus.REPORTED,
            evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
            scope=scope,
            calibration=ClaimCalibration.CONTRADICTED,
            uncertainty=("Superseded source version.",),
        )
        new_core = make_atomic_claim_core(
            candidate_id=candidate.screened_candidate_id,
            proposition="The corrected source reported a decrease.",
            polarity=ClaimPolarity.SUPPORTS,
            reporting_status=ClaimReportingStatus.REPORTED,
            evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
            scope=scope,
            calibration=ClaimCalibration.SUPPORTED_WITH_QUALIFIER,
            uncertainty=("Correction retained.",),
        )
        old_span = make_evidence_span(
            old_source,
            claim_id=old_core.claim_id,
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator="old/result",
            exact_excerpt=old_payload.decode(),
        )
        new_span = make_evidence_span(
            new_source,
            claim_id=new_core.claim_id,
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator="erratum/result",
            exact_excerpt=new_payload.decode(),
        )
        template = package.evidence_records[0]
        old_evidence = make_deep_evidence_record(
            old_core,
            old_source,
            old_span,
            study_design=template.study_design,
            population_or_experimental_model=template.population_or_experimental_model,
            sample_size=template.sample_size,
            comparator=template.comparator,
            dose=template.dose,
            administration_route=template.administration_route,
            duration=template.duration,
            tissue_or_cell_type=template.tissue_or_cell_type,
            exposure_or_concentration=template.exposure_or_concentration,
            endpoint_measure=template.endpoint_measure,
            effect_direction=ObservedEffectDirection.HARM,
            effect_magnitude=template.effect_magnitude,
            statistical_uncertainty=template.statistical_uncertainty,
            study_limitations=template.study_limitations,
            risk_of_bias_assessment=template.risk_of_bias_assessment,
        )
        new_evidence = make_deep_evidence_record(
            new_core,
            new_source,
            new_span,
            study_design=template.study_design,
            population_or_experimental_model=template.population_or_experimental_model,
            sample_size=template.sample_size,
            comparator=template.comparator,
            dose=template.dose,
            administration_route=template.administration_route,
            duration=template.duration,
            tissue_or_cell_type=template.tissue_or_cell_type,
            exposure_or_concentration=template.exposure_or_concentration,
            endpoint_measure=template.endpoint_measure,
            effect_direction=ObservedEffectDirection.BENEFIT,
            effect_magnitude=template.effect_magnitude,
            statistical_uncertainty=template.statistical_uncertainty,
            study_limitations=template.study_limitations,
            risk_of_bias_assessment=template.risk_of_bias_assessment,
        )
        old_claim = bind_atomic_claim(old_core, evidence_record_ids=(old_evidence.deep_evidence_record_id,))
        new_claim = bind_atomic_claim(new_core, evidence_record_ids=(new_evidence.deep_evidence_record_id,))
        new_path = make_deep_evidence_path(
            candidate_id=candidate.screened_candidate_id,
            structured_route_id=candidate.structured_routes[0].route_id,
            endpoint_id=ENDPOINT_ID,
            claim_ids=(new_claim.claim_id,),
            evidence_record_ids=(new_evidence.deep_evidence_record_id,),
        )
        source_correction = make_record_correction(
            target_kind=CorrectionTargetKind.SOURCE,
            target_id=old_source.source_record_id,
            action=CorrectionAction.SUPERSEDE,
            replacement_id=new_source.source_record_id,
            reason="Erratum supersedes the original source payload.",
            provenance_source_ids=(new_source.source_record_id,),
            provenance_evidence_span_ids=(new_span.evidence_span_id,),
        )
        claim_correction = make_record_correction(
            target_kind=CorrectionTargetKind.CLAIM,
            target_id=old_claim.claim_id,
            action=CorrectionAction.SUPERSEDE,
            replacement_id=new_claim.claim_id,
            reason="Corrected claim replaces the source-error claim.",
            provenance_source_ids=(new_source.source_record_id,),
            provenance_evidence_span_ids=(new_span.evidence_span_id,),
        )
        corrected = make_deep_evidence_package(
            candidate,
            identity_records=package.identity_records,
            current_identity_record_id=package.current_identity_record_id,
            sources=(*package.sources, old_source, new_source),
            evidence_spans=(*package.evidence_spans, old_span, new_span),
            evidence_records=(*package.evidence_records, old_evidence, new_evidence),
            claims=(*package.claims, old_claim, new_claim),
            paths=(*package.paths, new_path),
            endpoint_assessments=(
                DeepEndpointAssessment(
                    ENDPOINT_ID,
                    EndpointDeepStatus.ASSESSED,
                    "Corrected claim retained alongside immutable history.",
                    tuple(sorted((package.claims[0].claim_id, new_claim.claim_id))),
                ),
            ),
            corrections=(source_correction, claim_correction),
        )
        validate_deep_evidence_package(
            corrected,
            verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
            retained_payloads=retained,
        )
        self.assertIn(old_source, corrected.sources)
        self.assertIn(new_source, corrected.sources)

    def test_later_review_can_quarantine_identity_and_path_without_deleting_them(self):
        package, retained = _package()
        provenance_source = package.sources[-1]
        provenance_span = package.evidence_spans[-1]
        identity_correction = make_record_correction(
            target_kind=CorrectionTargetKind.IDENTITY,
            target_id=package.current_identity_record_id or "",
            action=CorrectionAction.QUARANTINE,
            replacement_id=None,
            reason="Later review found the exact identity decision-changing and unresolved.",
            provenance_source_ids=(provenance_source.source_record_id,),
            provenance_evidence_span_ids=(provenance_span.evidence_span_id,),
        )
        path_correction = make_record_correction(
            target_kind=CorrectionTargetKind.PATH,
            target_id=package.paths[0].path_id,
            action=CorrectionAction.QUARANTINE,
            replacement_id=None,
            reason="Later review found the causal path inapplicable.",
            provenance_source_ids=(provenance_source.source_record_id,),
            provenance_evidence_span_ids=(provenance_span.evidence_span_id,),
        )
        quarantined = make_deep_evidence_package(
            package.screened_candidate,
            identity_records=package.identity_records,
            current_identity_record_id=None,
            sources=package.sources,
            evidence_spans=package.evidence_spans,
            evidence_records=package.evidence_records,
            claims=package.claims,
            paths=package.paths,
            endpoint_assessments=package.endpoint_assessments,
            corrections=(identity_correction, path_correction),
        )
        validate_deep_evidence_package(
            quarantined,
            verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
            retained_payloads=retained,
        )
        self.assertIn(package.identity_records[0], quarantined.identity_records)
        self.assertIn(package.paths[0], quarantined.paths)
        with self.assertRaisesRegex(DeepEvidenceError, "no active authoritative identity"):
            promote_deep_candidate(quarantined, retained_payloads=retained)


if __name__ == "__main__":
    unittest.main(verbosity=2)
