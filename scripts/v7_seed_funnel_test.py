#!/usr/bin/env python3
"""Focused tests for the schema-v7 lightweight seed/funnel data model."""

from __future__ import annotations

import random
import unittest
from dataclasses import fields as dataclass_fields, replace
from typing import Any

from v7_case_model import CaseStatus, build_case_bundle
from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
    known_node,
    make_structured_route,
    not_applicable_node,
)
from v7_seed_funnel import (
    AliasAssertionStatus,
    AliasKind,
    CandidateSeed,
    CanonicalDisposition,
    CompoundHintKind,
    DetailedDisposition,
    EndpointScreenStatus,
    IdentityAssertionStatus,
    IdentityResolutionRecord,
    ScreeningDecision,
    SeedAlias,
    SeedFunnelError,
    SeedIdentityStatus,
    SeedSourceMapping,
    SeedUncertainty,
    UncertaintyKind,
    UncertaintyLevel,
    build_seed_funnel,
    make_candidate_seed,
    make_compound_hint,
    make_discovery_route,
    make_endpoint_assessment,
    make_identity_assertion,
    make_identity_resolution,
    make_screening_decision,
    make_seed_alias,
    make_source_mapping,
    provisional_screening_intervention_id,
    unknown_development_status,
    validate_seed_funnel,
    verified_normalized_intervention_id,
)


def endpoint_input(
    stable_key: str = "benefit",
    construct: str = "HP:0001250",
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "stable_key": stable_key,
        "display_label": f"{stable_key.title()} endpoint",
        "construct": construct,
        "role": "benefit",
        "endpoint_type": "clinical_outcome",
        "population": "Adults",
        "disease_stage": "Established disease",
        "timeframe": "24 weeks",
        "measurement": "Declared measure",
        "disease_context": "Target disease",
        "direction": "decrease_is_benefit",
        "priority": "high" if required else "exploratory",
        "required": required,
        "relationships": [],
    }


def ready_case(*, two_endpoints: bool = False) -> Any:
    endpoints = [endpoint_input()]
    if two_endpoints:
        endpoints.append(endpoint_input("biomarker", "LOINC:4548-4", required=False))
        endpoints[-1]["role"] = "biomarker"
        endpoints[-1]["endpoint_type"] = "biomarker"
    case = build_case_bundle(
        {"gene": "TP53", "disease": "MONDO:0004979", "endpoints": endpoints}
    ).case_revision
    if case.case_status is not CaseStatus.READY:
        raise AssertionError("test fixture must be READY")
    return case


IDENTITY_UNCERTAINTY = SeedUncertainty(
    kind=UncertaintyKind.IDENTITY,
    level=UncertaintyLevel.MEDIUM,
    note="Identity is suitable only for lightweight screening until independently resolved.",
)
CAUSAL_UNCERTAINTY = SeedUncertainty(
    kind=UncertaintyKind.CAUSAL,
    level=UncertaintyLevel.HIGH,
    note="The source provides a route hypothesis rather than a complete causal path.",
)


def base_records(
    case: Any,
    index: int,
    *,
    name: str | None = None,
    hint_kind: CompoundHintKind = CompoundHintKind.DATABASE_IDENTIFIER,
    hint_namespace: str = "TESTDB",
    identity_status: SeedIdentityStatus = SeedIdentityStatus.PROVISIONAL,
    disposition: DetailedDisposition = DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
    source_id: str = "frozen-test-source",
    query_id: str = "query-1",
) -> tuple[SeedSourceMapping, Any, CandidateSeed, IdentityResolutionRecord, ScreeningDecision]:
    compound_name = name or f"Compound-{index:06d}"
    mapping = make_source_mapping(
        case,
        source_id=source_id,
        source_release="2026-07-19",
        native_record_id=f"REC-{index:06d}",
        assertion_locator=f"REC-{index:06d}#compound-1",
        raw_intervention_assertion=compound_name,
    )
    route = make_discovery_route(
        mapping,
        query_id=query_id,
        query_record_locator=f"{query_id}/REC-{index:06d}",
        retrieval_content_receipt_id=f"RCP-{query_id}-{index:06d}",
    )
    hint = make_compound_hint(
        hint_kind,
        compound_name if hint_kind is CompoundHintKind.NAME_HINT else f"DB-{index:06d}",
        namespace="" if hint_kind is CompoundHintKind.NAME_HINT else hint_namespace,
    )
    seed = make_candidate_seed(
        case,
        mapping,
        endpoint_ids=(endpoint.endpoint_id for endpoint in case.endpoints),
        compound_hint=hint,
        discovery_route_ids=(route.route_id,),
        structured_routes=tuple(
            make_structured_route(
                case_revision_id=case.case_revision_id,
                intervention_id=mapping.seed_id,
                causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
                disease_state_node=known_node("TEST:DISEASE-STATE"),
                intervention_target=known_node("TEST:TARGET"),
                action=InterventionAction.UNKNOWN,
                direction=EffectDirection.UNKNOWN,
                intermediate_state=not_applicable_node(
                    "A direct route has no required intermediate state."
                ),
                endpoint_id=endpoint.endpoint_id,
                evidence_ids=(f"EVIDENCE-{index:06d}",),
            )
            for endpoint in case.endpoints
        ),
        evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
        chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
        development_status_hint=unknown_development_status(
            "No development status was reported by the source."
        ),
        identity_status=identity_status,
        uncertainty=(IDENTITY_UNCERTAINTY, CAUSAL_UNCERTAINTY),
    )
    if identity_status is SeedIdentityStatus.PROVISIONAL:
        identity = make_identity_resolution(
            seed,
            status=identity_status,
            screening_intervention_id=provisional_screening_intervention_id(seed),
        )
    elif identity_status is SeedIdentityStatus.UNRESOLVED:
        identity = make_identity_resolution(
            seed,
            status=identity_status,
            decision_changing_ambiguity=True,
        )
    elif identity_status is SeedIdentityStatus.CONFLICTING:
        identity = make_identity_resolution(
            seed,
            status=identity_status,
            decision_changing_ambiguity=True,
            conflict_values=("TESTDB:ONE", "TESTDB:TWO"),
        )
    else:
        identity = make_identity_resolution(seed, status=identity_status)

    assessments = ()
    if disposition is DetailedDisposition.RETAINED_FOR_DEEP_REVIEW:
        assessments = tuple(
            make_endpoint_assessment(
                endpoint.endpoint_id,
                EndpointScreenStatus.INSUFFICIENT,
                reason="No human evidence is required for a lightweight screen-pass hypothesis.",
                uncertainty=(CAUSAL_UNCERTAINTY,),
            )
            for endpoint in case.endpoints
        )
    unresolved_fields = (
        ("/identity",)
        if disposition
        in {
            DetailedDisposition.IDENTITY_UNRESOLVED,
            DetailedDisposition.RETAINED_FOR_IDENTITY_RESOLUTION,
        }
        else ()
    )
    decision = make_screening_decision(
        seed,
        identity,
        disposition=disposition,
        reason=f"Fixture disposition: {disposition.value}.",
        endpoint_assessments=assessments,
        unresolved_fields=unresolved_fields,
    )
    return mapping, route, seed, identity, decision


def build_from_rows(case: Any, rows: list[tuple[Any, ...]], aliases: list[SeedAlias] | None = None) -> Any:
    return build_seed_funnel(
        case,
        source_mappings=[row[0] for row in rows],
        discovery_routes=[row[1] for row in rows],
        seeds=[row[2] for row in rows],
        aliases=aliases or [],
        identity_resolutions=[row[3] for row in rows],
        screening_decisions=[row[4] for row in rows],
    )


class ReconciliationScaleTests(unittest.TestCase):
    def test_one_thousand_retrieved_mappings_have_complete_seed_and_disposition_coverage(self) -> None:
        case = ready_case()
        rows = [base_records(case, index) for index in range(1000)]
        snapshot = build_from_rows(case, rows)

        self.assertEqual(snapshot.reconciliation.retrieved_mapping_count, 1000)
        self.assertEqual(snapshot.reconciliation.seed_count, 1000)
        self.assertEqual(snapshot.reconciliation.current_disposition_count, 1000)
        self.assertEqual(snapshot.reconciliation.admit_count, 1000)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 1000)
        self.assertEqual(snapshot.reconciliation.seed_candidate_link_count, 1000)
        mapping_to_seed = {mapping.mapping_id: mapping.seed_id for mapping in snapshot.source_mappings}
        seed_to_mapping = {seed.seed_id: seed.source_mapping_id for seed in snapshot.seeds}
        disposition_seed_ids = {decision.seed_id for decision in snapshot.screening_decisions}
        self.assertEqual(set(mapping_to_seed.values()), set(seed_to_mapping))
        self.assertEqual(set(seed_to_mapping), disposition_seed_ids)
        self.assertTrue(
            all(seed_to_mapping[seed_id] == mapping_id for mapping_id, seed_id in mapping_to_seed.items())
        )
        validate_seed_funnel(case, snapshot)

    def test_small_and_zero_declared_results_have_no_candidate_quota(self) -> None:
        case = ready_case()
        rows = [base_records(case, index) for index in range(17)]
        snapshot = build_from_rows(case, rows)
        self.assertEqual(snapshot.reconciliation.retrieved_mapping_count, 17)
        self.assertEqual(snapshot.reconciliation.seed_count, 17)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 17)
        self.assertFalse(any("quota" in field.name for field in dataclass_fields(snapshot.reconciliation)))

        empty = build_seed_funnel(
            case,
            source_mappings=[],
            discovery_routes=[],
            seeds=[],
            aliases=[],
            identity_resolutions=[],
            screening_decisions=[],
        )
        self.assertEqual(empty.reconciliation.seed_count, 0)
        self.assertEqual(empty.reconciliation.screened_candidate_count, 0)


class AliasAndIdentityTests(unittest.TestCase):
    def test_verified_duplicate_alias_uses_one_deterministic_representative_and_preserves_provenance(self) -> None:
        case = ready_case()
        first = list(base_records(case, 1, name="Compound Alpha"))
        second = list(base_records(case, 2, name="AlphaSyn"))
        representative, duplicate = (
            (first, second) if first[2].seed_id < second[2].seed_id else (second, first)
        )
        alias = make_seed_alias(
            duplicate[2],
            alias_kind=AliasKind.SYNONYM,
            raw_alias=duplicate[2].compound_hint.value,
            assertion_status=AliasAssertionStatus.VERIFIED,
            equivalent_seed_id=representative[2].seed_id,
            authority="curated-resolver",
            authority_release="2026-07-19",
        )
        representative[4] = make_screening_decision(
            representative[2],
            representative[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Deterministic admitted representative.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Human evidence is not required at this tier.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        duplicate[4] = make_screening_decision(
            duplicate[2],
            duplicate[3],
            disposition=DetailedDisposition.DUPLICATE_ALIAS,
            reason="Curated authority confirms an alias of the representative.",
            representative_seed_id=representative[2].seed_id,
        )

        snapshot = build_from_rows(case, [tuple(first), tuple(second)], [alias])
        self.assertEqual(snapshot.reconciliation.seed_count, 2)
        self.assertEqual(snapshot.reconciliation.admit_count, 1)
        self.assertEqual(snapshot.reconciliation.merge_count, 1)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 1)
        candidate = snapshot.screened_candidates[0]
        self.assertEqual(set(candidate.source_seed_ids), {first[2].seed_id, second[2].seed_id})
        self.assertEqual(
            set(candidate.source_mapping_ids), {first[0].mapping_id, second[0].mapping_id}
        )
        self.assertEqual(set(candidate.discovery_route_ids), {first[1].route_id, second[1].route_id})
        self.assertIn(alias.alias_id, candidate.alias_ids)
        self.assertEqual(len(snapshot.seed_candidate_mappings), 2)

    def test_salts_and_formulations_are_not_collapsed_from_names_or_provisional_aliases(self) -> None:
        case = ready_case()
        rows = [
            list(base_records(case, 10, name="Compound Beta")),
            list(base_records(case, 11, name="Compound Beta hydrochloride")),
            list(base_records(case, 12, name="Compound Beta extended release")),
        ]
        base_seed = min((row[2] for row in rows), key=lambda seed: seed.seed_id)
        aliases = [
            make_seed_alias(
                row[2],
                alias_kind=(
                    AliasKind.SALT_OR_SOLVATE if "hydrochloride" in row[0].raw_intervention_assertion
                    else AliasKind.FORMULATION
                ),
                raw_alias=row[0].raw_intervention_assertion,
                assertion_status=AliasAssertionStatus.PROVISIONAL,
                equivalent_seed_id=base_seed.seed_id if row[2].seed_id != base_seed.seed_id else None,
            )
            for row in rows
            if row[2].seed_id != base_seed.seed_id
        ]
        snapshot = build_from_rows(case, [tuple(row) for row in rows], aliases)
        self.assertEqual(snapshot.reconciliation.admit_count, 3)
        self.assertEqual(snapshot.reconciliation.merge_count, 0)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 3)
        self.assertEqual(
            len({candidate.screening_intervention_id for candidate in snapshot.screened_candidates}),
            3,
        )

        formulation_row = next(
            row for row in rows if "extended release" in row[0].raw_intervention_assertion
        )
        representative_row, duplicate_row = sorted(
            (rows[0], formulation_row), key=lambda row: row[2].seed_id
        )
        provisional_formulation_link = make_seed_alias(
            duplicate_row[2],
            alias_kind=AliasKind.FORMULATION,
            raw_alias=duplicate_row[0].raw_intervention_assertion,
            assertion_status=AliasAssertionStatus.PROVISIONAL,
            equivalent_seed_id=representative_row[2].seed_id,
        )
        duplicate_row[4] = make_screening_decision(
            duplicate_row[2],
            duplicate_row[3],
            disposition=DetailedDisposition.DUPLICATE_FORMULATION,
            reason="A provisional name hint must not be enough to merge.",
            representative_seed_id=representative_row[2].seed_id,
        )
        with self.assertRaisesRegex(SeedFunnelError, "verified equivalence"):
            build_from_rows(
                case,
                [tuple(row) for row in rows],
                [*aliases, provisional_formulation_link],
            )

    def test_unresolved_identity_is_quarantined_without_candidate_promotion(self) -> None:
        case = ready_case()
        row = base_records(
            case,
            20,
            name="Ambiguous database entry",
            identity_status=SeedIdentityStatus.UNRESOLVED,
            disposition=DetailedDisposition.IDENTITY_UNRESOLVED,
        )
        snapshot = build_from_rows(case, [row])
        self.assertEqual(snapshot.reconciliation.quarantine_count, 1)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 0)
        self.assertEqual(snapshot.seed_candidate_mappings, ())
        self.assertEqual(len(snapshot.unresolved_or_quarantined_seeds), 1)
        quarantine = snapshot.unresolved_or_quarantined_seeds[0]
        self.assertFalse(quarantine.can_advance)
        self.assertEqual(quarantine.disposition, DetailedDisposition.IDENTITY_UNRESOLVED)
        self.assertEqual(quarantine.source_mapping_ids, (row[0].mapping_id,))
        self.assertEqual(quarantine.discovery_route_ids, (row[1].route_id,))

    def test_decision_changing_identity_ambiguity_cannot_be_rejected_or_merged_away(self) -> None:
        case = ready_case()
        row = list(
            base_records(
                case,
                21,
                identity_status=SeedIdentityStatus.UNRESOLVED,
                disposition=DetailedDisposition.IDENTITY_UNRESOLVED,
            )
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.WRONG_DIRECTION,
            reason="Identity ambiguity cannot be hidden behind a scientific rejection.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.CONTRADICTORY,
                    reason="The proposed direction conflicts with the endpoint direction.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        with self.assertRaisesRegex(SeedFunnelError, "must remain quarantined"):
            build_from_rows(case, [tuple(row)])

    def test_provisional_identity_key_is_seed_scoped_and_name_only_cannot_advance(self) -> None:
        case = ready_case()
        row = list(base_records(case, 22))
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.PROVISIONAL,
            screening_intervention_id="CHEMBL25",
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="An arbitrary external token cannot replace the deterministic provisional ID.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Identity support remains provisional.",
                    uncertainty=(IDENTITY_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        with self.assertRaisesRegex(SeedFunnelError, "provisional identity is overstated"):
            build_from_rows(case, [tuple(row)])

        name_row = base_records(
            case,
            23,
            name="Name hint without identifier",
            hint_kind=CompoundHintKind.NAME_HINT,
            hint_namespace="",
        )
        with self.assertRaisesRegex(SeedFunnelError, "provisional identity is overstated"):
            build_from_rows(case, [name_row])

    def test_resolved_identity_id_is_derived_from_verified_assertions(self) -> None:
        case = ready_case()
        row = list(base_records(case, 24))
        row[2] = replace(row[2], identity_status=SeedIdentityStatus.RESOLVED)
        assertion = make_identity_assertion(
            row[2],
            authority="curated-registry",
            authority_release="2026-07-19",
            identifier_type="registry_identifier",
            identifier="CURATED:000024",
            assertion_status=IdentityAssertionStatus.VERIFIED,
        )
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.RESOLVED,
            screening_intervention_id="NORMALIZED-WRONG",
            verified_normalized_intervention_id="NORMALIZED-WRONG",
            identity_verified=True,
            assertions=(assertion,),
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Resolved identity fixture.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Identity verification does not imply human evidence.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        with self.assertRaisesRegex(SeedFunnelError, "resolved identity lacks"):
            build_from_rows(case, [tuple(row)])

        normalized_id = verified_normalized_intervention_id((assertion,))
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.RESOLVED,
            screening_intervention_id=normalized_id,
            verified_normalized_intervention_id=normalized_id,
            identity_verified=True,
            assertions=(assertion,),
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Resolved identity fixture.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Identity verification does not imply human evidence.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        snapshot = build_from_rows(case, [tuple(row)])
        self.assertTrue(snapshot.screened_candidates[0].identity_verified)
        self.assertEqual(
            snapshot.screened_candidates[0].verified_normalized_intervention_id,
            normalized_id,
        )

    def test_conflicting_identity_assertions_cannot_hide_inside_resolved_status(self) -> None:
        case = ready_case()
        row = list(base_records(case, 240))
        row[2] = replace(row[2], identity_status=SeedIdentityStatus.RESOLVED)
        verified = make_identity_assertion(
            row[2],
            authority="resolver-a",
            authority_release="2026-07-19",
            identifier_type="registry_identifier",
            identifier="REG:A",
            assertion_status=IdentityAssertionStatus.VERIFIED,
        )
        conflicting = make_identity_assertion(
            row[2],
            authority="resolver-b",
            authority_release="2026-07-19",
            identifier_type="registry_identifier",
            identifier="REG:B",
            assertion_status=IdentityAssertionStatus.CONFLICTING,
        )
        normalized_id = verified_normalized_intervention_id((verified, conflicting))
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.RESOLVED,
            screening_intervention_id=normalized_id,
            verified_normalized_intervention_id=normalized_id,
            identity_verified=True,
            assertions=(verified, conflicting),
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Conflicting assertions cannot be hidden.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Scientific evidence remains insufficient.",
                    uncertainty=(IDENTITY_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        with self.assertRaisesRegex(SeedFunnelError, "require conflicting status"):
            build_from_rows(case, [tuple(row)])

    def test_duplicate_resolved_identity_is_rejected_across_all_admitted_screen_outcomes(self) -> None:
        case = ready_case()
        rows = [list(base_records(case, 241)), list(base_records(case, 242))]
        for index, row in enumerate(rows):
            row[2] = replace(row[2], identity_status=SeedIdentityStatus.RESOLVED)
            assertion = make_identity_assertion(
                row[2],
                authority="shared-resolver",
                authority_release="2026-07-19",
                identifier_type="registry_identifier",
                identifier="SHARED:IDENTITY",
                assertion_status=IdentityAssertionStatus.VERIFIED,
            )
            normalized_id = verified_normalized_intervention_id((assertion,))
            row[3] = make_identity_resolution(
                row[2],
                status=SeedIdentityStatus.RESOLVED,
                screening_intervention_id=normalized_id,
                verified_normalized_intervention_id=normalized_id,
                identity_verified=True,
                assertions=(assertion,),
            )
            disposition = (
                DetailedDisposition.RETAINED_FOR_DEEP_REVIEW
                if index == 0
                else DetailedDisposition.WRONG_DIRECTION
            )
            endpoint_status = (
                EndpointScreenStatus.INSUFFICIENT
                if index == 0
                else EndpointScreenStatus.CONTRADICTORY
            )
            row[4] = make_screening_decision(
                row[2],
                row[3],
                disposition=disposition,
                reason="Shared resolved identity fixture.",
                endpoint_assessments=tuple(
                    make_endpoint_assessment(
                        endpoint.endpoint_id,
                        endpoint_status,
                        reason="Outcome differs, but Stage 4 identity must still deduplicate.",
                        uncertainty=(CAUSAL_UNCERTAINTY,),
                    )
                    for endpoint in case.endpoints
                ),
            )
        with self.assertRaisesRegex(SeedFunnelError, "share one screening identity"):
            build_from_rows(case, [tuple(row) for row in rows])

    def test_verified_equivalence_cannot_leave_both_seeds_admitted(self) -> None:
        case = ready_case()
        rows = [list(base_records(case, 25)), list(base_records(case, 26))]
        representative, duplicate = sorted(rows, key=lambda row: row[2].seed_id)
        alias = make_seed_alias(
            duplicate[2],
            alias_kind=AliasKind.SYNONYM,
            raw_alias="Curated equivalent",
            assertion_status=AliasAssertionStatus.VERIFIED,
            equivalent_seed_id=representative[2].seed_id,
            authority="curated-resolver",
            authority_release="2026-07-19",
        )
        with self.assertRaisesRegex(SeedFunnelError, "matching merge disposition"):
            build_from_rows(case, [tuple(row) for row in rows], [alias])

    def test_verified_alias_can_merge_into_a_baseline_representative(self) -> None:
        case = ready_case()
        rows = [list(base_records(case, 27)), list(base_records(case, 28))]
        representative, duplicate = sorted(rows, key=lambda row: row[2].seed_id)
        representative[4] = make_screening_decision(
            representative[2],
            representative[3],
            disposition=DetailedDisposition.BASELINE_CARE,
            reason="The exact intervention is retained in the separate baseline lane.",
        )
        alias = make_seed_alias(
            duplicate[2],
            alias_kind=AliasKind.REGISTRY_IDENTIFIER,
            raw_alias=duplicate[2].compound_hint.value,
            assertion_status=AliasAssertionStatus.VERIFIED,
            equivalent_seed_id=representative[2].seed_id,
            authority="curated-resolver",
            authority_release="2026-07-19",
        )
        duplicate[4] = make_screening_decision(
            duplicate[2],
            duplicate[3],
            disposition=DetailedDisposition.DUPLICATE_ALIAS,
            reason="Verified alias of the baseline representative.",
            representative_seed_id=representative[2].seed_id,
        )
        snapshot = build_from_rows(case, [tuple(row) for row in rows], [alias])
        self.assertEqual(snapshot.reconciliation.baseline_count, 1)
        self.assertEqual(snapshot.reconciliation.merge_count, 1)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 0)
        self.assertEqual(snapshot.seed_candidate_mappings, ())


class RecallAndLightweightAdmissionTests(unittest.TestCase):
    def test_obscure_database_only_compound_and_candidate_without_human_evidence_are_valid(self) -> None:
        case = ready_case(two_endpoints=True)
        row = base_records(
            case,
            30,
            name="DB-only-00030",
            hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
            hint_namespace="OBSCUREDB",
            source_id="obscure-database-snapshot",
        )
        snapshot = build_from_rows(case, [row])
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 1)
        self.assertEqual(
            {assessment.status for assessment in row[4].endpoint_assessments},
            {EndpointScreenStatus.INSUFFICIENT},
        )
        seed_fields = {field.name for field in dataclass_fields(CandidateSeed)}
        prohibited_heavy_fields = {
            "established_human_use",
            "complete_causal_path",
            "active_moiety_id",
            "score",
            "human_clinical_evidence",
            "safety_proof",
            "exposure_proof",
            "audit_status",
        }
        self.assertTrue(prohibited_heavy_fields.isdisjoint(seed_fields))
        self.assertEqual(row[2].development_status_hint.status.value, "unknown")
        self.assertFalse(snapshot.screened_candidates[0].identity_verified)
        self.assertIsNone(snapshot.screened_candidates[0].active_moiety_id)

    def test_all_requested_detailed_dispositions_are_controlled(self) -> None:
        required = {
            "retained_for_identity_resolution",
            "retained_for_deep_review",
            "duplicate_alias",
            "duplicate_formulation",
            "wrong_direction",
            "unrelated_endpoint",
            "prohibited_intervention_type",
            "identity_unresolved",
            "evidence_insufficient_but_preserved",
            "exposure_infeasible",
            "safety_mismatch",
            "quarantined_invalid_source",
        }
        self.assertTrue(required.issubset({value.value for value in DetailedDisposition}))

    def test_screen_rejection_is_reconciled_after_seed_admission(self) -> None:
        case = ready_case()
        row = list(base_records(case, 31))
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.WRONG_DIRECTION,
            reason="The proposed action direction contradicts the endpoint direction.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.CONTRADICTORY,
                    reason="Direction is contradictory for this endpoint.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        snapshot = build_from_rows(case, [tuple(row)])
        self.assertEqual(snapshot.reconciliation.admit_count, 1)
        self.assertEqual(snapshot.reconciliation.screen_rejected_count, 1)
        self.assertEqual(snapshot.reconciliation.screened_count, 0)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 0)
        self.assertEqual(snapshot.seed_candidate_mappings, ())

    def test_screening_technical_failure_is_distinct_from_seed_processing_failure(self) -> None:
        case = ready_case()
        row = list(base_records(case, 32))
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.SCREENING_TECHNICAL_FAILURE,
            reason="The admitted seed's lightweight screen could not be completed technically.",
        )
        snapshot = build_from_rows(case, [tuple(row)])
        self.assertEqual(snapshot.reconciliation.admit_count, 1)
        self.assertEqual(snapshot.reconciliation.screen_failed_count, 1)
        self.assertEqual(snapshot.reconciliation.failed_count, 0)
        self.assertEqual(snapshot.reconciliation.screened_candidate_count, 0)

    def test_raw_assertion_and_alias_text_are_preserved_exactly(self) -> None:
        case = ready_case()
        raw = "  βeta\u00a0salt  "
        mapping = make_source_mapping(
            case,
            source_id="raw-source",
            source_release="2026-07-19",
            native_record_id="RAW-1",
            assertion_locator="RAW-1#compound",
            raw_intervention_assertion=raw,
        )
        route = make_discovery_route(
            mapping,
            query_id="raw-query",
            query_record_locator="raw-query/RAW-1",
            retrieval_content_receipt_id="RCP-RAW-1",
        )
        seed = make_candidate_seed(
            case,
            mapping,
            endpoint_ids=(endpoint.endpoint_id for endpoint in case.endpoints),
            compound_hint=make_compound_hint(
                CompoundHintKind.DATABASE_IDENTIFIER, "RAW:1", namespace="RAWDB"
            ),
            discovery_route_ids=(route.route_id,),
            structured_routes=tuple(
                make_structured_route(
                    case_revision_id=case.case_revision_id,
                    intervention_id=mapping.seed_id,
                    causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
                    disease_state_node=known_node("TEST:RAW-DISEASE-STATE"),
                    intervention_target=known_node("TEST:RAW-TARGET"),
                    action=InterventionAction.UNKNOWN,
                    direction=EffectDirection.UNKNOWN,
                    intermediate_state=not_applicable_node(
                        "A direct route has no required intermediate state."
                    ),
                    endpoint_id=endpoint.endpoint_id,
                    evidence_ids=("EVIDENCE-RAW-1",),
                )
                for endpoint in case.endpoints
            ),
            evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
            chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
            development_status_hint=unknown_development_status("Status was not reported."),
            identity_status=SeedIdentityStatus.PROVISIONAL,
            uncertainty=(IDENTITY_UNCERTAINTY,),
        )
        identity = make_identity_resolution(
            seed,
            status=SeedIdentityStatus.PROVISIONAL,
            screening_intervention_id=provisional_screening_intervention_id(seed),
        )
        decision = make_screening_decision(
            seed,
            identity,
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Raw-text preservation fixture.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Evidence remains insufficient.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        alias = make_seed_alias(
            seed,
            alias_kind=AliasKind.SOURCE_NAME,
            raw_alias=raw,
            assertion_status=AliasAssertionStatus.UNVERIFIED,
        )
        snapshot = build_seed_funnel(
            case,
            source_mappings=[mapping],
            discovery_routes=[route],
            seeds=[seed],
            aliases=[alias],
            identity_resolutions=[identity],
            screening_decisions=[decision],
        )
        self.assertEqual(snapshot.source_mappings[0].raw_intervention_assertion, raw)
        self.assertEqual(snapshot.aliases[0].raw_alias, raw)
        self.assertEqual(snapshot.aliases[0].comparison_value, "βeta salt")


class DeterminismAndGateTests(unittest.TestCase):
    def test_seed_replay_and_input_order_are_idempotent_but_conflicts_fail(self) -> None:
        case = ready_case()
        rows = [base_records(case, index) for index in range(40, 50)]
        first = build_from_rows(case, rows)

        mappings = [row[0] for row in rows]
        routes = [row[1] for row in rows]
        seeds = [row[2] for row in rows]
        identities = [row[3] for row in rows]
        decisions = [row[4] for row in rows]
        random.Random(7).shuffle(mappings)
        random.Random(8).shuffle(routes)
        random.Random(9).shuffle(seeds)
        replayed = build_seed_funnel(
            case,
            source_mappings=[*mappings, *mappings],
            discovery_routes=[*routes, *routes],
            seeds=[*seeds, *seeds],
            aliases=[],
            identity_resolutions=[*identities, *identities],
            screening_decisions=[*decisions, *decisions],
        )
        self.assertEqual(first, replayed)
        self.assertEqual(first.snapshot_id, replayed.snapshot_id)

        conflicting = replace(mappings[0], raw_intervention_assertion="conflicting replay")
        with self.assertRaisesRegex(SeedFunnelError, "idempotency conflict"):
            build_seed_funnel(
                case,
                source_mappings=[mappings[0], conflicting],
                discovery_routes=routes,
                seeds=seeds,
                aliases=[],
                identity_resolutions=identities,
                screening_decisions=decisions,
            )

    def test_query_overlap_adds_routes_without_inflating_seeds(self) -> None:
        case = ready_case()
        mapping, first_route, seed, identity, _ = base_records(case, 60)
        second_route = make_discovery_route(
            mapping,
            query_id="query-overlap",
            query_record_locator="query-overlap/REC-000060",
            retrieval_content_receipt_id="RCP-query-overlap-000060",
        )
        seed = replace(
            seed,
            discovery_route_ids=tuple(sorted((first_route.route_id, second_route.route_id))),
        )
        identity = make_identity_resolution(
            seed,
            status=SeedIdentityStatus.PROVISIONAL,
            screening_intervention_id=provisional_screening_intervention_id(seed),
        )
        decision = make_screening_decision(
            seed,
            identity,
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="Both queries preserve one source assertion seed.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Evidence depth is not a seed requirement.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        snapshot = build_seed_funnel(
            case,
            source_mappings=[mapping],
            discovery_routes=[first_route, second_route],
            seeds=[seed],
            aliases=[],
            identity_resolutions=[identity],
            screening_decisions=[decision],
        )
        self.assertEqual(snapshot.reconciliation.retrieved_mapping_count, 1)
        self.assertEqual(snapshot.reconciliation.seed_count, 1)
        self.assertEqual(len(snapshot.discovery_routes), 2)
        self.assertEqual(
            set(snapshot.screened_candidates[0].discovery_route_ids),
            {first_route.route_id, second_route.route_id},
        )

    def test_unresolved_case_and_unknown_endpoint_fail_closed(self) -> None:
        unresolved_case = build_case_bundle({"gene": "TP53"}).case_revision
        with self.assertRaisesRegex(SeedFunnelError, "READY case"):
            build_seed_funnel(
                unresolved_case,
                source_mappings=[],
                discovery_routes=[],
                seeds=[],
                aliases=[],
                identity_resolutions=[],
                screening_decisions=[],
            )

        case = ready_case()
        row = list(base_records(case, 70))
        row[2] = replace(row[2], endpoint_ids=("EP-NOT-IN-CASE",))
        with self.assertRaisesRegex(SeedFunnelError, "unknown endpoint"):
            build_from_rows(case, [tuple(row)])

    def test_seed_without_any_discovery_route_fails_closed(self) -> None:
        case = ready_case()
        row = list(base_records(case, 71))
        row[2] = replace(row[2], discovery_route_ids=())
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.PROVISIONAL,
            screening_intervention_id=provisional_screening_intervention_id(row[2]),
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
            reason="A lineage-free seed must fail.",
            endpoint_assessments=tuple(
                make_endpoint_assessment(
                    endpoint.endpoint_id,
                    EndpointScreenStatus.INSUFFICIENT,
                    reason="Route lineage is structurally missing.",
                    uncertainty=(CAUSAL_UNCERTAINTY,),
                )
                for endpoint in case.endpoints
            ),
        )
        with self.assertRaisesRegex(SeedFunnelError, "discovery-route provenance is incomplete"):
            build_seed_funnel(
                case,
                source_mappings=[row[0]],
                discovery_routes=[],
                seeds=[row[2]],
                aliases=[],
                identity_resolutions=[row[3]],
                screening_decisions=[row[4]],
            )

    def test_any_decision_changing_identity_ambiguity_requires_quarantine(self) -> None:
        case = ready_case()
        row = list(base_records(case, 72))
        row[2] = replace(row[2], identity_status=SeedIdentityStatus.UNASSESSED)
        row[3] = make_identity_resolution(
            row[2],
            status=SeedIdentityStatus.UNASSESSED,
            decision_changing_ambiguity=True,
        )
        row[4] = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.PROHIBITED_INTERVENTION_TYPE,
            reason="A scope decision cannot hide decision-changing identity ambiguity.",
        )
        with self.assertRaisesRegex(SeedFunnelError, "must remain quarantined"):
            build_from_rows(case, [tuple(row)])

    def test_duplicate_or_missing_current_dispositions_fail(self) -> None:
        case = ready_case()
        row = base_records(case, 80)
        with self.assertRaisesRegex(SeedFunnelError, "exactly one current disposition"):
            build_seed_funnel(
                case,
                source_mappings=[row[0]],
                discovery_routes=[row[1]],
                seeds=[row[2]],
                aliases=[],
                identity_resolutions=[row[3]],
                screening_decisions=[],
            )
        duplicate = make_screening_decision(
            row[2],
            row[3],
            disposition=DetailedDisposition.SAFETY_MISMATCH,
            reason="A second current disposition is not allowed.",
        )
        with self.assertRaisesRegex(SeedFunnelError, "exactly one current disposition"):
            build_seed_funnel(
                case,
                source_mappings=[row[0]],
                discovery_routes=[row[1]],
                seeds=[row[2]],
                aliases=[],
                identity_resolutions=[row[3]],
                screening_decisions=[row[4], duplicate],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
