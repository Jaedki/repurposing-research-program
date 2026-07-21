#!/usr/bin/env python3
"""Focused tests for schema-v7 factorized discovery semantics."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from v7_case_model import build_case_bundle, canonical_bytes
from v7_discovery import (
    BROAD_DOMAIN_CONTRACTS,
    BroadDomainContract,
    CaseModelOutputKind,
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    DiscoveryContractError,
    EffectDirection,
    EvidenceModality,
    EvidenceRecord,
    InterventionAction,
    Uncertainty,
    UncertaintyKind,
    UncertaintyLevel,
    build_broad_case_model,
    build_discovery_snapshot,
    cache_model_output,
    deduplicate_discovery_hypotheses,
    deterministic_sample,
    enumerate_discovery_jobs,
    extract_grounded_evidence,
    known_node,
    load_frozen_source_payload,
    materialize_seed_emission,
    make_case_model_record,
    make_direction_conflict,
    make_discovery_hypothesis,
    make_seed_emission,
    make_structured_route,
    normalize_structured_routes,
    not_applicable_node,
    validate_broad_domain_contracts,
)


def ready_case():
    return build_case_bundle(
        {
            "gene": {
                "identifier": "TP53",
                "disease_associated_state": "loss_of_function",
                "desired_therapeutic_modulation": "restore",
            },
            "disease": "MONDO:0004979",
            "endpoints": [
                {
                    "stable_key": "clinical-benefit",
                    "display_label": "Clinical benefit",
                    "construct": "HP:0001250",
                    "role": "benefit",
                    "endpoint_type": "clinical_outcome",
                    "population": "Adults",
                    "disease_stage": "Established disease",
                    "timeframe": "24 weeks",
                    "measurement": "Validated scale",
                    "disease_context": "Target disease",
                    "direction": "decrease_is_benefit",
                    "priority": "critical",
                    "required": True,
                    "relationships": [],
                }
            ],
        }
    ).case_revision


def evidence(evidence_id: str, modality: EvidenceModality) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        modality=modality,
        source_id="SOURCE",
        source_release="2026-07-20",
        native_record_id=f"REC-{evidence_id}",
        locator=f"REC-{evidence_id}#support",
        retrieval_content_receipt_id=f"RCP-{evidence_id}",
        claim_id=f"CLAIM-{evidence_id}",
    )


def route(case, *, route_kind=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION, target="HGNC:11998", evidence_id="E1", target_label="TP53", intervention_id="INTERVENTION-1"):
    return make_structured_route(
        case_revision_id=case.case_revision_id,
        intervention_id=intervention_id,
        causal_route=route_kind,
        disease_state_node=known_node("DISEASE-STATE:1", "disease state"),
        intervention_target=known_node(target, target_label),
        action=InterventionAction.RESTORE,
        direction=EffectDirection.RESTORE,
        intermediate_state=not_applicable_node("The route is direct."),
        endpoint_id=case.endpoints[0].endpoint_id,
        evidence_ids=(evidence_id,),
    )


class FactorizedDimensionTests(unittest.TestCase):
    def test_natural_origin_can_coexist_with_every_causal_route(self) -> None:
        case = ready_case()
        hypotheses = [
            make_discovery_hypothesis(
                case_revision_id=case.case_revision_id,
                intervention_id="NATURAL-1",
                structured_route=route(
                    case,
                    route_kind=route_kind,
                    evidence_id=f"E-{index}",
                    intervention_id="NATURAL-1",
                ),
                evidence_modality=EvidenceModality.BIOACTIVITY,
                chemical_universe=ChemicalUniverse.NATURAL_PRODUCTS,
                development_status=DevelopmentStatus.PRECLINICAL,
                endpoint_id=case.endpoints[0].endpoint_id,
                uncertainty=(
                    Uncertainty(UncertaintyKind.CAUSAL, UncertaintyLevel.MEDIUM, "Translation is uncertain."),
                ),
                source_mapping_ids=(f"MAP-{index}",),
                evidence_ids=(f"E-{index}",),
            )
            for index, route_kind in enumerate(CausalRoute)
        ]
        normalized = deduplicate_discovery_hypotheses(hypotheses)
        self.assertEqual(len(normalized), len(CausalRoute))
        self.assertEqual({row.chemical_universe for row in normalized}, {ChemicalUniverse.NATURAL_PRODUCTS})

    def test_genetics_and_clinical_intervention_are_distinct_modalities(self) -> None:
        self.assertIsNot(EvidenceModality.GENETICS, EvidenceModality.CLINICAL_INTERVENTION)
        case = ready_case()
        broad = build_broad_case_model(case)
        jobs = enumerate_discovery_jobs(case, broad)
        expected = len(CausalRoute) * len(EvidenceModality) * len(ChemicalUniverse)
        self.assertEqual(len(jobs), expected)
        genetics = {row.job_id for row in jobs if row.evidence_modality is EvidenceModality.GENETICS}
        clinical = {
            row.job_id
            for row in jobs
            if row.evidence_modality is EvidenceModality.CLINICAL_INTERVENTION
        }
        self.assertTrue(genetics)
        self.assertTrue(clinical)
        self.assertFalse(genetics & clinical)


class StructuralRouteTests(unittest.TestCase):
    def test_same_compound_can_have_genuinely_distinct_structured_routes(self) -> None:
        case = ready_case()
        direct = route(case, evidence_id="E-DIRECT")
        bypass = route(
            case,
            route_kind=CausalRoute.DOWNSTREAM_OR_BYPASS_RESTORATION,
            target="HGNC:20000",
            evidence_id="E-BYPASS",
            target_label="bypass target",
        )
        rows = [
            make_discovery_hypothesis(
                case_revision_id=case.case_revision_id,
                intervention_id="INTERVENTION-1",
                structured_route=value,
                evidence_modality=EvidenceModality.MOLECULAR_FUNCTIONAL,
                chemical_universe=ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,
                development_status=DevelopmentStatus.APPROVED,
                endpoint_id=case.endpoints[0].endpoint_id,
                uncertainty=(),
                source_mapping_ids=(f"MAP-{index}",),
                evidence_ids=value.evidence_ids,
            )
            for index, value in enumerate((direct, bypass))
        ]
        self.assertEqual(len(deduplicate_discovery_hypotheses(rows)), 2)

    def test_trivial_paraphrasing_does_not_create_false_convergence(self) -> None:
        case = ready_case()
        first = route(case, evidence_id="E1", target_label="TP53 protein")
        second = route(case, evidence_id="E2", target_label="tumour protein p53")
        self.assertEqual(first.route_id, second.route_id)
        normalized = normalize_structured_routes((second, first))
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].evidence_ids, ("E1", "E2"))

    def test_equivalent_structured_routes_deduplicate_order_independently(self) -> None:
        case = ready_case()
        first = route(case, evidence_id="E1")
        second = route(case, evidence_id="E2")
        forward = normalize_structured_routes((first, second))
        reverse = normalize_structured_routes((second, first))
        self.assertEqual(canonical_bytes(forward), canonical_bytes(reverse))
        self.assertEqual(len(forward), 1)


class BroadCaseModelTests(unittest.TestCase):
    def test_broad_domains_have_exclusive_control_of_every_output(self) -> None:
        validate_broad_domain_contracts()
        duplicate = list(BROAD_DOMAIN_CONTRACTS)
        duplicate[-1] = BroadDomainContract(
            domain=duplicate[-1].domain,
            owned_output_fields=("pharmacology_seed_emissions", "disease_mechanisms"),
            prohibited_output_fields=(),
        )
        with self.assertRaisesRegex(DiscoveryContractError, "duplicated"):
            validate_broad_domain_contracts(duplicate)

    def test_structured_case_outputs_and_pharmacology_seed_sources_are_preserved(self) -> None:
        case = ready_case()
        endpoint_id = case.endpoints[0].endpoint_id
        evidence_rows = tuple(
            evidence(f"E{index}", EvidenceModality.AUTHORITATIVE_PHARMACOLOGY)
            for index in range(1, 11)
        )
        kinds = tuple(CaseModelOutputKind)
        records = {
            kind: make_case_model_record(
                output_kind=kind,
                primary_node=known_node(f"NODE:{kind.value}"),
                action=InterventionAction.MODULATE,
                direction=EffectDirection.NORMALIZE,
                endpoint_ids=(endpoint_id,),
                evidence_ids=(f"E{index}",),
                source_mapping_ids=(f"MAP-{index}",),
            )
            for index, kind in enumerate(kinds, 1)
        }
        source_route = make_structured_route(
            case_revision_id=case.case_revision_id,
            intervention_id="SEED-PHARM-1",
            causal_route=CausalRoute.HOST_ENVIRONMENT_OR_INFLAMMATORY_MODULATION,
            disease_state_node=known_node("STATE:INFLAMMATION"),
            intervention_target=known_node("TARGET:CYTOKINE"),
            action=InterventionAction.INHIBIT,
            direction=EffectDirection.DECREASE,
            intermediate_state=known_node("STATE:LOWER-INFLAMMATION"),
            endpoint_id=endpoint_id,
            evidence_ids=("E9",),
        )
        emission = make_seed_emission(
            source_id="PHARMDB",
            source_release="2026-07-20",
            native_record_id="DRUG-1",
            assertion_locator="DRUG-1#target",
            raw_intervention_assertion="Exact compound assertion",
            query_id="QUERY-1",
            query_record_locator="QUERY-1/DRUG-1",
            retrieval_content_receipt_id="RCP-1",
            compound_hint_kind="database_identifier",
            compound_hint_value="PHARMDB:DRUG-1",
            compound_hint_namespace="PHARMDB",
            endpoint_ids=(endpoint_id,),
            structured_routes=(source_route,),
            evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
            chemical_universes=(ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,),
            development_status=DevelopmentStatus.APPROVED,
            uncertainty=(),
            evidence_ids=("E9",),
        )
        conflict = make_direction_conflict(
            subject_node=known_node("HGNC:11998"),
            asserted_directions=(EffectDirection.INCREASE, EffectDirection.DECREASE),
            evidence_ids=("E9", "E10"),
        )
        snapshot = build_broad_case_model(
            case,
            disease_mechanisms=(records[CaseModelOutputKind.DISEASE_MECHANISM],),
            directional_targets=(records[CaseModelOutputKind.DIRECTIONAL_TARGET],),
            phenotypes_and_signatures=(records[CaseModelOutputKind.PHENOTYPE_OR_SIGNATURE],),
            tissues_and_cell_types=(records[CaseModelOutputKind.TISSUE_OR_CELL_TYPE],),
            substrates_and_metabolites=(records[CaseModelOutputKind.SUBSTRATE_OR_METABOLITE],),
            compensatory_nodes=(records[CaseModelOutputKind.COMPENSATORY_NODE],),
            contraindicated_mechanisms=(records[CaseModelOutputKind.CONTRAINDICATED_MECHANISM],),
            endpoint_mappings=(records[CaseModelOutputKind.ENDPOINT_MAPPING],),
            unresolved_direction_conflicts=(conflict,),
            pharmacology_seed_emissions=(emission,),
            evidence_records=evidence_rows,
        )
        self.assertEqual(snapshot.pharmacology_seed_emissions[0].source_id, "PHARMDB")
        self.assertEqual(
            snapshot.pharmacology_seed_emissions[0].raw_intervention_assertion,
            "Exact compound assertion",
        )
        self.assertEqual(len(snapshot.unresolved_direction_conflicts), 1)
        self.assertEqual(len(extract_grounded_evidence(snapshot)), 10)
        mapping, discovery_route, seed = materialize_seed_emission(case, emission)
        self.assertEqual(mapping.source_id, "PHARMDB")
        self.assertEqual(mapping.raw_intervention_assertion, "Exact compound assertion")
        self.assertEqual(discovery_route.retrieval_content_receipt_id, "RCP-1")
        self.assertEqual(seed.source_mapping_id, mapping.mapping_id)
        self.assertEqual(seed.chemical_universes, emission.chemical_universes)
        self.assertTrue(all(getattr(snapshot, field) for field in (
            "disease_mechanisms", "directional_targets", "phenotypes_and_signatures",
            "tissues_and_cell_types", "substrates_and_metabolites", "compensatory_nodes",
            "contraindicated_mechanisms", "endpoint_mappings",
        )))


class DeterministicOperationsTests(unittest.TestCase):
    def test_ranking_sampling_caching_and_frozen_parsing_are_deterministic(self) -> None:
        case = ready_case()
        value = route(case)
        hypothesis = make_discovery_hypothesis(
            case_revision_id=case.case_revision_id,
            intervention_id="INTERVENTION-1",
            structured_route=value,
            evidence_modality=EvidenceModality.GENETICS,
            chemical_universe=ChemicalUniverse.CLINICAL_STAGE_ASSETS,
            development_status=DevelopmentStatus.PHASE_2,
            endpoint_id=case.endpoints[0].endpoint_id,
            uncertainty=(),
            source_mapping_ids=("MAP-1",),
            evidence_ids=("E1",),
        )
        snapshot = build_discovery_snapshot(case.case_revision_id, (hypothesis, hypothesis))
        self.assertEqual(len(snapshot.hypotheses), 1)
        self.assertEqual(deterministic_sample(snapshot.hypotheses, 1, salt="audit"), snapshot.hypotheses)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frozen = root / "source.json"
            frozen.write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")
            first = load_frozen_source_payload(
                frozen, source_id="SOURCE", source_release="2026-07-20"
            )
            second = load_frozen_source_payload(
                frozen, source_id="SOURCE", source_release="2026-07-20"
            )
            self.assertEqual(first, second)
            self.assertEqual(first["record_count"], 2)
            cache_one = cache_model_output(
                root / "cache",
                namespace="discovery",
                inputs={"case": case.case_revision_id},
                source_releases=("SOURCE@2026-07-20",),
                output=snapshot,
            )
            cache_two = cache_model_output(
                root / "cache",
                namespace="discovery",
                inputs={"case": case.case_revision_id},
                source_releases=("SOURCE@2026-07-20",),
                output=snapshot,
            )
            self.assertEqual(cache_one, cache_two)
            self.assertEqual(cache_one.read_bytes(), canonical_bytes(snapshot) + b"\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
