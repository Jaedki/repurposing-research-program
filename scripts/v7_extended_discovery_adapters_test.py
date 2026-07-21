#!/usr/bin/env python3
"""Scoped offline tests for schema-v7 multimodal discovery adapters."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any, Mapping

from v7_case_model import build_case_bundle, canonical_bytes
from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
)
from v7_extended_discovery_adapters import (
    BoundedDiscoveryPlanner,
    ChebiOlsAdapter,
    ClinicalTrialsBranch,
    ClinicalTrialsGovAdapter,
    DirectionalAlignment,
    ExtendedDiscoveryError,
    HttpResponse,
    PlannerDisposition,
    PreprintAdapter,
    PreprintServer,
    UnsupportedCapabilityRecord,
    UnsupportedReason,
    build_anti_popularity_discovery_frame,
    make_admission_metadata,
    make_chebi_mapping_plan,
    make_clinical_trials_plan,
    make_gwas_catalog_genetics_planner,
    make_preprint_plan,
    make_pubchem_phenotypic_screen_planner,
    make_recent_preprint_entity_discovery_planner,
    make_signature_reversal_planner,
    make_string_network_proximity_planner,
    make_unsupported_capability_record,
    validate_anti_popularity_discovery_frame,
)
from v7_retrieval_adapter import (
    CoverageState,
    RecordDisposition,
    SourceFindingPolarity,
    execute_query_plan,
    make_publication_density_metadata,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "schema_v7"
    / "extended_discovery_adapters"
    / "frozen_responses.json"
)
EXPECTED_FIXTURE_SHA256 = "468966AC39A66B720E4C9A6F8FDE0E1B30ACDAAD1F3BA6BD84CFB88D8EE8A102"


class DeterministicClock:
    def __init__(self, microsecond: int = 0) -> None:
        self.microsecond = microsecond

    def __call__(self) -> str:
        result = f"2026-07-20T15:00:00.{self.microsecond:06d}Z"
        self.microsecond += 1
        return result


class FrozenTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = [canonical_bytes(value) for value in responses]
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("No frozen response remains")
        return HttpResponse(status=200, headers={}, body=self.responses.pop(0))


def _execute(case: Any, plan: Any, adapter: Any):
    return execute_query_plan(
        case,
        plan,
        adapter,
        sleeper=lambda _: None,
        clock=DeterministicClock(),
    )


class ExtendedDiscoveryAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.case = build_case_bundle(cls.fixture["case_input"]).case_revision
        cls.endpoint_id = cls.case.endpoints[0].endpoint_id
        cls.responses = cls.fixture["responses"]

    def clinical_plan(self, branch: ClinicalTrialsBranch, **kwargs: Any):
        return make_clinical_trials_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T14:00:00Z",
            branch=branch,
            condition_query="Rareopathy",
            endpoint_ids=(self.endpoint_id,),
            causal_route=CausalRoute.DOWNSTREAM_OR_BYPASS_RESTORATION,
            **kwargs,
        )

    def preprint_plan(self):
        return make_preprint_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T14:00:00Z",
            server=PreprintServer.BIORXIV,
            from_date="2026-07-01",
            to_date="2026-07-20",
            intervention_terms=("Novirazole", "FamousWrongDrug"),
            case_terms=("Rareopathy",),
            endpoint_ids=(self.endpoint_id,),
            causal_route=CausalRoute.PHENOTYPE_OR_STATE_REVERSAL,
            evidence_modality=EvidenceModality.OMICS_SIGNATURE,
            chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
            development_status=DevelopmentStatus.PRECLINICAL,
            max_pages=3,
        )

    def test_fixture_checksum_and_kind_are_stable(self) -> None:
        self.assertEqual(
            hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest().upper(),
            EXPECTED_FIXTURE_SHA256,
        )
        self.assertEqual(
            self.fixture["fixture_kind"],
            "synthetic official-shape development fixture",
        )

    def test_clinical_trials_enumerates_interventions_and_ledgers_non_compounds(self) -> None:
        transport = FrozenTransport(self.responses["clinical_intervention_pages"])
        plan = self.clinical_plan(ClinicalTrialsBranch.INTERVENTION_ENUMERATION)
        proof = _execute(
            self.case,
            plan,
            ClinicalTrialsGovAdapter("2026-07-20", transport=transport),
        )
        self.assertEqual(
            proof.coverage_state,
            CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
        )
        self.assertEqual(proof.reconciliation.retrieved_page_count, 2)
        self.assertEqual(proof.reconciliation.normalized_record_count, 4)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 3)
        self.assertTrue(
            any(
                row.disposition is RecordDisposition.NON_INTERVENTION_TYPE_EXCLUDED
                for row in proof.normalized_records
            )
        )
        names = {row.seed.compound_hint.value for row in proof.seed_emissions}
        self.assertEqual(names, {"ObscureDB731", "FamousWrongDrug", "Adjacentol"})
        self.assertIn("pageToken=CT-PAGE-2", transport.calls[1][1])

    def test_failed_trial_and_negative_text_remain_attached_without_efficacy_inference(self) -> None:
        transport = FrozenTransport([self.responses["clinical_failed_page"]])
        plan = self.clinical_plan(
            ClinicalTrialsBranch.FAILED_TERMINATED_OR_NEGATIVE
        )
        proof = _execute(
            self.case,
            plan,
            ClinicalTrialsGovAdapter("2026-07-20", transport=transport),
        )
        self.assertEqual(proof.reconciliation.emitted_seed_count, 1)
        emission = proof.seed_emissions[0]
        self.assertEqual(emission.seed.compound_hint.value, "ShelvedX17")
        self.assertEqual(emission.seed.development_status_hint.status.value, "known")
        self.assertEqual(
            set(emission.seed.chemical_universes),
            {ChemicalUniverse.CLINICAL_STAGE_ASSETS},
        )
        assertion = proof.normalized_records[0].seed_assertions[0]
        self.assertEqual(
            {row.annotation_type for row in assertion.evidence_annotations},
            {
                "failed_or_terminated_trial_status",
                "posted_outcome_result",
                "posted_results_present",
            },
        )
        self.assertTrue(
            any("no improvement" in row.source_text.casefold() for row in assertion.evidence_annotations)
        )
        self.assertTrue(
            all(
                row.finding_polarity is SourceFindingPolarity.NOT_EVALUATED
                for row in assertion.evidence_annotations
            )
        )

    def test_observational_branch_keeps_real_world_distinct_from_clinical_intervention(self) -> None:
        plan = self.clinical_plan(ClinicalTrialsBranch.OBSERVATIONAL_REAL_WORLD)
        proof = _execute(
            self.case,
            plan,
            ClinicalTrialsGovAdapter(
                "2026-07-20",
                transport=FrozenTransport([self.responses["clinical_observational_page"]]),
            ),
        )
        seed = proof.seed_emissions[0].seed
        self.assertEqual(
            seed.evidence_modalities,
            (EvidenceModality.OBSERVATIONAL_REAL_WORLD,),
        )
        self.assertNotIn(EvidenceModality.GENETICS, seed.evidence_modalities)
        self.assertNotIn(EvidenceModality.CLINICAL_INTERVENTION, seed.evidence_modalities)

    def test_adjacent_indication_branch_retains_its_distinct_disease_mapping(self) -> None:
        plan = self.clinical_plan(
            ClinicalTrialsBranch.ADJACENT_INDICATION,
            adjacent_indication_id="MONDO:0099999",
        )
        proof = _execute(
            self.case,
            plan,
            ClinicalTrialsGovAdapter(
                "2026-07-20",
                transport=FrozenTransport(
                    [self.responses["clinical_intervention_pages"][1]]
                ),
            ),
        )
        assertion = proof.normalized_records[0].seed_assertions[0]
        self.assertEqual(assertion.compound_hint.value, "Adjacentol")
        self.assertEqual(
            assertion.mapping_contexts[0].mapping_type,
            "adjacent_indication_intervention",
        )
        self.assertEqual(
            assertion.mapping_contexts[0].disease_id,
            "MONDO:0099999",
        )

    def test_recent_preprints_search_all_pages_without_citation_gating(self) -> None:
        plan = self.preprint_plan()
        proof = _execute(
            self.case,
            plan,
            PreprintAdapter(
                "2026-07-20",
                server=PreprintServer.BIORXIV,
                transport=FrozenTransport(self.responses["preprint_pages"]),
            ),
        )
        self.assertEqual(proof.reconciliation.source_reported_total, 3)
        self.assertEqual(proof.reconciliation.normalized_record_count, 3)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        self.assertEqual(
            {row.seed.compound_hint.value for row in proof.seed_emissions},
            {"Novirazole", "FamousWrongDrug"},
        )
        novirazole = next(
            assertion
            for record in proof.normalized_records
            for assertion in record.seed_assertions
            if assertion.compound_hint.value == "Novirazole"
        )
        self.assertEqual(novirazole.publication_density[0].publication_count, 1)
        self.assertIsNone(novirazole.publication_density[0].citation_count)
        self.assertIn(
            EvidenceModality.OMICS_SIGNATURE, novirazole.evidence_modalities
        )

    def test_natural_origin_is_orthogonal_to_direct_causal_route(self) -> None:
        plan = make_chebi_mapping_plan(
            source_release="ChEBI 2026-07",
            source_snapshot_at="2026-07-20T14:00:00Z",
            query_term="rare nutrient",
            endpoint_ids=(self.endpoint_id,),
            causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
            chemical_universes=(
                ChemicalUniverse.NATURAL_PRODUCTS,
                ChemicalUniverse.ENDOGENOUS_COMPOUNDS_OR_NUTRIENTS,
            ),
            context_id="RHEA:900001",
            rows=1,
            max_pages=2,
        )
        proof = _execute(
            self.case,
            plan,
            ChebiOlsAdapter(
                "ChEBI 2026-07",
                transport=FrozenTransport(self.responses["chebi_pages"]),
            ),
        )
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        for emission in proof.seed_emissions:
            self.assertIn(
                ChemicalUniverse.NATURAL_PRODUCTS,
                emission.seed.chemical_universes,
            )
            self.assertEqual(
                emission.seed.structured_routes[0].causal_route,
                CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
            )
            self.assertEqual(
                emission.seed.structured_routes[0].action,
                InterventionAction.UNKNOWN,
            )
            self.assertEqual(
                emission.seed.structured_routes[0].direction,
                EffectDirection.UNKNOWN,
            )

    def test_accessible_bounded_planners_keep_modalities_and_handoffs_separate(self) -> None:
        phenotypic = make_pubchem_phenotypic_screen_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T14:00:00Z",
            phenotype_query="Rareopathy phenotype",
            maximum_assays=25,
        )
        genetics = make_gwas_catalog_genetics_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="2026-07",
            source_snapshot_at="2026-07-20T14:00:00Z",
            efo_id="MONDO_0004979",
            maximum_pages=5,
        )
        network = make_string_network_proximity_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="STRING 12.0",
            source_snapshot_at="2026-07-20T14:00:00Z",
            versioned_api_url="https://version-12-0.string-db.org",
            identifiers=("9606.ENSP00000269305",),
            required_score=700,
            additional_nodes=10,
        )
        self.assertEqual(
            {row.evidence_modalities[0] for row in (phenotypic, genetics, network)},
            {
                EvidenceModality.PHENOTYPIC_SCREENING,
                EvidenceModality.GENETICS,
                EvidenceModality.NETWORK_COMPUTATIONAL,
            },
        )
        self.assertTrue(
            all(row.disposition is PlannerDisposition.BOUNDED_QUERY_PLAN for row in (phenotypic, genetics, network))
        )
        self.assertIn("separately declared chemical adapter", genetics.downstream_handoff)
        unknown_names = make_recent_preprint_entity_discovery_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T14:00:00Z",
            server=PreprintServer.BIORXIV,
            from_date="2026-07-01",
            to_date="2026-07-20",
            case_terms=("Rareopathy",),
            chemical_dictionary_release_uri="https://example.org/frozen-open-chebi-dictionary.tsv",
            chemical_dictionary_sha256="0" * 64,
            maximum_records=60,
        )
        self.assertIsInstance(unknown_names, BoundedDiscoveryPlanner)
        self.assertEqual(unknown_names.case_revision_id, self.case.case_revision_id)
        self.assertEqual(unknown_names.endpoint_ids, (self.endpoint_id,))
        self.assertIn("not predeclared by name", unknown_names.query_purpose)
        with self.assertRaisesRegex(ExtendedDiscoveryError, "outside the case"):
            make_gwas_catalog_genetics_planner(
                case=self.case,
                endpoint_ids=("EP-NOT-IN-CASE",),
                source_release="2026-07",
                source_snapshot_at="2026-07-20T14:00:00Z",
                efo_id="MONDO_0004979",
            )

    def test_inaccessible_sources_emit_explicit_bounded_gaps(self) -> None:
        signature = make_signature_reversal_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="CLUE 2026",
            source_snapshot_at="2026-07-20T14:00:00Z",
            disease_signature_id="SIG-RARE-1",
            up_gene_ids=("GENE1",),
            down_gene_ids=("GENE2",),
        )
        self.assertIsInstance(signature, UnsupportedCapabilityRecord)
        self.assertEqual(signature.reason, UnsupportedReason.LOCAL_DATA_REQUIRED)
        self.assertIn("No transcriptomic-reversal candidates", signature.preserved_coverage_gap)
        commercial = make_unsupported_capability_record(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_id="commercial-claims-rwe",
            source_release="unknown",
            source_snapshot_at="2026-07-20T14:00:00Z",
            planned_capability="patient-level_real_world_signal_enumeration",
            reason=UnsupportedReason.LICENSE_REQUIRED,
            exact_planned_query={"condition": "Rareopathy"},
            access_requirement="A licensed data enclave and approved query are required.",
            preserved_coverage_gap="No patient-level commercial claims signal was enumerated.",
            authoritative_reference="https://example.invalid/licensed-source-contract",
        )
        self.assertEqual(commercial.disposition, PlannerDisposition.UNSUPPORTED_CAPABILITY)
        self.assertNotEqual(signature.capability_record_id, commercial.capability_record_id)

    def test_open_signature_snapshot_yields_a_plan_not_false_execution(self) -> None:
        planner = make_signature_reversal_planner(
            case=self.case,
            endpoint_ids=(self.endpoint_id,),
            source_release="LINCS-open-2026",
            source_snapshot_at="2026-07-20T14:00:00Z",
            disease_signature_id="SIG-RARE-1",
            up_gene_ids=("GENE1",),
            down_gene_ids=("GENE2",),
            data_release_uri="https://example.org/frozen-open-lincs-matrix.gctx",
            redistribution_status="open",
            maximum_perturbagens=100,
            cell_contexts=("hepatocyte",),
            doses=("1 uM",),
            timepoints=("24 h",),
        )
        self.assertIsInstance(planner, BoundedDiscoveryPlanner)
        self.assertEqual(planner.disposition, PlannerDisposition.BOUNDED_QUERY_PLAN)
        self.assertEqual(planner.evidence_modalities, (EvidenceModality.OMICS_SIGNATURE,))
        self.assertIn("declared frozen signature matrix", planner.allowed_coverage_statement)
        parameters = json.loads(planner.exact_request_parameters_json)
        self.assertEqual(parameters["cell_contexts"], ["hepatocyte"])
        self.assertEqual(parameters["doses"], ["1 uM"])
        self.assertEqual(parameters["timepoints"], ["24 h"])

    def test_anti_popularity_frame_retains_obscure_recent_wrong_and_negative_candidates(self) -> None:
        clinical = _execute(
            self.case,
            self.clinical_plan(ClinicalTrialsBranch.INTERVENTION_ENUMERATION),
            ClinicalTrialsGovAdapter(
                "2026-07-20",
                transport=FrozenTransport(self.responses["clinical_intervention_pages"]),
            ),
        )
        failed = _execute(
            self.case,
            self.clinical_plan(ClinicalTrialsBranch.FAILED_TERMINATED_OR_NEGATIVE),
            ClinicalTrialsGovAdapter(
                "2026-07-20",
                transport=FrozenTransport([self.responses["clinical_failed_page"]]),
            ),
        )
        preprints = _execute(
            self.case,
            self.preprint_plan(),
            PreprintAdapter(
                "2026-07-20",
                server=PreprintServer.BIORXIV,
                transport=FrozenTransport(self.responses["preprint_pages"]),
            ),
        )
        by_name = {
            row.seed.compound_hint.value: row.seed
            for row in clinical.seed_emissions
            if row.seed.compound_hint.value == "ObscureDB731"
        }
        by_name.update(
            {
                row.seed.compound_hint.value: row.seed
                for row in preprints.seed_emissions
            }
        )
        by_name["ShelvedX17"] = failed.seed_emissions[0].seed
        controls = self.fixture["anti_popularity"]["candidates"]
        metadata = []
        for name in ("ObscureDB731", "Novirazole", "FamousWrongDrug", "ShelvedX17"):
            control = controls[name]
            source_id = (
                "biorxiv-api"
                if name in {"Novirazole", "FamousWrongDrug"}
                else "clinicaltrials-gov"
            )
            density = make_publication_density_metadata(
                source_id="frozen-authoritative-citation-snapshot",
                as_of="2026-07-20T14:00:00Z",
                query_scope=f"Exact identity-scoped density for {name}",
                publication_count=control["publication_count"],
                citation_count=control["citation_count"],
                limitations=("Synthetic development control; never used as a live source claim.",),
            )
            metadata.append(
                make_admission_metadata(
                    by_name[name],
                    source_ids=(source_id,),
                    database_only=control["database_only"],
                    most_recent_record_date=control["most_recent_record_date"],
                    directional_alignment=DirectionalAlignment(
                        control["directional_alignment"]
                    ),
                    evidence_signals=(
                        SourceFindingPolarity(value)
                        for value in control["evidence_signals"]
                    ),
                    publication_density=(density,),
                )
            )
        seeds = tuple(by_name[name] for name in ("ObscureDB731", "Novirazole", "FamousWrongDrug", "ShelvedX17"))
        cutoff = self.fixture["anti_popularity"]["recent_cutoff_date"]
        frame = build_anti_popularity_discovery_frame(
            seeds, metadata, recent_cutoff_date=cutoff
        )
        validate_anti_popularity_discovery_frame(
            seeds, frame, recent_cutoff_date=cutoff
        )
        self.assertEqual(set(frame.seed_ids), {seed.seed_id for seed in seeds})
        self.assertIn(by_name["ObscureDB731"].seed_id, frame.reserved_database_only_seed_ids)
        self.assertIn(by_name["Novirazole"].seed_id, frame.reserved_low_publication_seed_ids)
        self.assertIn(by_name["Novirazole"].seed_id, frame.recent_or_uncited_seed_ids)
        self.assertNotIn(by_name["FamousWrongDrug"].seed_id, frame.reserved_low_publication_seed_ids)
        self.assertIn(by_name["FamousWrongDrug"].seed_id, frame.negative_or_null_seed_ids)
        self.assertIn(by_name["ShelvedX17"].seed_id, frame.negative_or_null_seed_ids)
        self.assertIn(by_name["Novirazole"].seed_id, frame.preclinical_seed_ids)
        self.assertFalse(frame.citation_used_for_admission)
        self.assertFalse(frame.citation_chain_used_for_admission)
        self.assertFalse(hasattr(frame, "rank"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
