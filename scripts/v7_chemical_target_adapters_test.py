#!/usr/bin/env python3
"""Scoped offline tests for schema-v7 chemical and target adapters."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from v7_case_model import build_case_bundle, canonical_bytes
from v7_chemical_target_adapters import (
    BindingDbAdapter,
    ChemblAdapter,
    ChemicalTargetAdapterError,
    HttpResponse,
    OpenTargetsAdapter,
    OpenTargetsEntityKind,
    PubChemAdapter,
    PubChemQueryKind,
    UniChemCrossReference,
    UniChemIdentityResolution,
    UniChemIdentityResolver,
    make_bindingdb_plan,
    make_chembl_plan,
    make_open_targets_plan,
    make_pubchem_plan,
    make_unichem_identity_resolution,
    union_cross_adapter_chemicals,
)
from v7_discovery import ChemicalUniverse, DevelopmentStatus
from v7_retrieval_adapter import (
    ContentAddressedRetrievalCache,
    CoverageState,
    RecordDisposition,
    RetrievalContractError,
    execute_query_plan,
    validate_coverage_proof,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "schema_v7"
    / "chemical_target_adapters"
    / "frozen_responses.json"
)
EXPECTED_FIXTURE_SHA256 = "D9AC1B423524DCEAA507ED8D6F86AD3EF2932DB1C19857E84392D9642CDC6868"


class DeterministicClock:
    def __init__(self, microsecond: int = 0) -> None:
        self.microsecond = microsecond

    def __call__(self) -> str:
        result = f"2026-07-20T12:00:00.{self.microsecond:06d}Z"
        self.microsecond += 1
        return result


class FrozenTransport:
    def __init__(self, responses: list[Any], *, enabled: bool = True) -> None:
        self.responses = [
            value if isinstance(value, bytes) else canonical_bytes(value)
            for value in responses
        ]
        self.enabled = enabled
        self.calls: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        if not self.enabled:
            raise AssertionError("network transport was called during replay")
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError("no frozen response remains for this request")
        return HttpResponse(status=200, headers={}, body=self.responses.pop(0))


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _execute(case: Any, plan: Any, adapter: Any, *, cache: Any = None, replay_only: bool = False):
    return execute_query_plan(
        case,
        plan,
        adapter,
        cache=cache,
        replay_only=replay_only,
        sleeper=lambda _: None,
        clock=DeterministicClock(),
    )


class OfficialSourceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _fixture()
        cls.case = build_case_bundle(cls.fixture["case_input"]).case_revision
        cls.endpoint_id = cls.case.endpoints[0].endpoint_id
        cls.responses = cls.fixture["responses"]

    def test_frozen_response_checksum_and_plans_are_target_first(self) -> None:
        self.assertEqual(
            hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest().upper(),
            EXPECTED_FIXTURE_SHA256,
        )
        plan = make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-20T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(self.endpoint_id,),
            disease_state_id="MONDO_0004979",
        )
        self.assertIn("drugAndClinicalCandidates", plan.exact_request_parameters["graphql_query"])
        self.assertEqual(plan.source_universe.source_side_filters["entity_kind"], "target")
        with self.assertRaisesRegex(ChemicalTargetAdapterError, "candidate-name"):
            make_chembl_plan(
                source_release="ChEMBL 36",
                source_snapshot_at="2026-07-20T00:00:00Z",
                resource="molecule",
                filters={"pref_name__icontains": "aspirin"},
                endpoint_ids=(self.endpoint_id,),
                origin_kind="target",
                origin_ids=("CHEMBL-TARGET-1",),
            )

    def test_open_targets_maps_small_molecules_and_ledgers_type_exclusions(self) -> None:
        transport = FrozenTransport([self.responses["open_targets_target"]])
        plan = make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-20T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(self.endpoint_id,),
            disease_state_id="MONDO_0004979",
        )
        proof = _execute(self.case, plan, OpenTargetsAdapter("26.06", transport=transport))
        self.assertEqual(proof.coverage_state, CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE)
        self.assertEqual(proof.reconciliation.source_reported_total, 3)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        self.assertEqual(
            {row.disposition for row in proof.normalized_records},
            {RecordDisposition.EMITTED_SEEDS, RecordDisposition.NON_INTERVENTION_TYPE_EXCLUDED},
        )
        seeds = {row.seed.compound_hint.value: row.seed for row in proof.seed_emissions}
        self.assertEqual(
            seeds["CHEMBL25"].chemical_universes,
            (ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,),
        )
        self.assertEqual(
            seeds["CHEMBL900002"].chemical_universes,
            (ChemicalUniverse.CLINICAL_STAGE_ASSETS,),
        )
        assertions = [
            assertion
            for record in proof.normalized_records
            for assertion in record.seed_assertions
        ]
        self.assertTrue(all(assertion.activity_observations for assertion in assertions))
        aspirin = next(
            assertion
            for assertion in assertions
            if assertion.compound_hint.value == "CHEMBL25"
        )
        self.assertTrue(
            any(
                context.target_id == "ENSG00000141510"
                and context.disease_id == "MONDO_0004979"
                for context in aspirin.mapping_contexts
            )
        )
        self.assertEqual(transport.calls[0][0], "POST")
        body = json.loads((transport.calls[0][3] or b"").decode("utf-8"))
        self.assertEqual(body["variables"], {"ensemblId": "ENSG00000141510"})
        self.assertEqual(body["query"], plan.exact_request_parameters["graphql_query"])

    def test_chembl_enumerates_target_mechanism_molecule_and_all_activity_pages(self) -> None:
        common = {
            "source_release": "ChEMBL 36",
            "source_snapshot_at": "2026-07-20T00:00:00Z",
            "endpoint_ids": (self.endpoint_id,),
            "origin_kind": "target",
            "origin_ids": ("CHEMBL-TARGET-1",),
            "target_id": "CHEMBL-TARGET-1",
            "target_organism": "Homo sapiens",
            "disease_state_id": "MONDO_0004979",
        }
        scenarios = {
            "target": (self.responses["chembl_target_pages"], {"target_chembl_id": "CHEMBL-TARGET-1"}),
            "mechanism": (self.responses["chembl_mechanism_pages"], {"target_chembl_id": "CHEMBL-TARGET-1"}),
            "molecule": (self.responses["chembl_molecule_pages"], {"molecule_chembl_id__in": "CHEMBL900003"}),
            "activity": (self.responses["chembl_activity_pages"], {"target_chembl_id": "CHEMBL-TARGET-1"}),
        }
        proofs = {}
        for resource, (pages, filters) in scenarios.items():
            with self.subTest(resource=resource):
                transport = FrozenTransport(pages)
                plan = make_chembl_plan(
                    resource=resource,
                    filters=filters,
                    page_size=2 if resource == "activity" else 1000,
                    **common,
                )
                proof = _execute(self.case, plan, ChemblAdapter("ChEMBL 36", transport=transport))
                proofs[resource] = proof
                self.assertIn(
                    proof.coverage_state,
                    {
                        CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
                        CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY,
                    },
                )
                validate_coverage_proof(self.case, proof)
        self.assertEqual(proofs["target"].reconciliation.normalized_record_count, 2)
        self.assertEqual(proofs["target"].reconciliation.emitted_seed_count, 0)
        self.assertEqual(proofs["mechanism"].reconciliation.emitted_seed_count, 1)
        self.assertEqual(proofs["molecule"].reconciliation.emitted_seed_count, 1)
        activities = proofs["activity"]
        self.assertEqual(activities.reconciliation.retrieved_page_count, 2)
        self.assertEqual(activities.reconciliation.source_reported_total, 3)
        self.assertEqual(activities.reconciliation.emitted_seed_count, 3)
        observations = [
            assertion.activity_observations[0]
            for record in activities.normalized_records
            for assertion in record.seed_assertions
        ]
        self.assertEqual({row.activity_type for row in observations}, {"IC50", "Ki", "EC50"})
        self.assertEqual({row.units for row in observations}, {"nM", "uM"})
        self.assertEqual({row.target_organism for row in observations}, {"Homo sapiens", "Mus musculus"})
        self.assertEqual({row.confidence_scale for row in observations}, {"ChEMBL assay confidence score"})

        withdrawn_payload = {
            "molecules": [
                {
                    "molecule_chembl_id": "CHEMBL-WITHDRAWN-1",
                    "pref_name": "Public withdrawn asset",
                    "max_phase": 2,
                    "withdrawn_flag": True,
                }
            ],
            "page_meta": {
                "limit": 1000,
                "next": None,
                "offset": 0,
                "previous": None,
                "total_count": 1,
            },
        }
        withdrawn_plan = make_chembl_plan(
            source_release="ChEMBL 36",
            source_snapshot_at="2026-07-20T00:00:00Z",
            resource="molecule",
            filters={"withdrawn_flag": True},
            endpoint_ids=(self.endpoint_id,),
            origin_kind="source_mapping",
            origin_ids=("withdrawn-asset-inventory",),
            disease_state_id="MONDO_0004979",
        )
        withdrawn_proof = _execute(
            self.case,
            withdrawn_plan,
            ChemblAdapter(
                "ChEMBL 36", transport=FrozenTransport([withdrawn_payload])
            ),
        )
        withdrawn_seed = withdrawn_proof.seed_emissions[0].seed
        self.assertEqual(
            withdrawn_seed.development_status_hint.value,
            DevelopmentStatus.WITHDRAWN,
        )
        self.assertEqual(
            withdrawn_seed.chemical_universes,
            (ChemicalUniverse.SHELVED_OR_FAILED_ASSETS,),
        )
        flagged_activity_payload = {
            "activities": [
                {
                    "activity_id": 990001,
                    "molecule_chembl_id": "CHEMBL-NOT-WITHDRAWN-BY-ACTIVITY",
                    "target_chembl_id": "CHEMBL-TARGET-1",
                    "assay_chembl_id": "CHEMBL-ASSAY-990001",
                    "standard_type": "IC50",
                    "standard_value": "10",
                    "standard_units": "nM",
                    "max_phase": 2,
                    "withdrawn_flag": True,
                }
            ],
            "page_meta": {
                "limit": 1000,
                "next": None,
                "offset": 0,
                "previous": None,
                "total_count": 1,
            },
        }
        flagged_activity_plan = make_chembl_plan(
            source_release="ChEMBL 36",
            source_snapshot_at="2026-07-20T00:00:00Z",
            resource="activity",
            filters={"target_chembl_id": "CHEMBL-TARGET-1"},
            endpoint_ids=(self.endpoint_id,),
            origin_kind="target",
            origin_ids=("CHEMBL-TARGET-1",),
            target_id="CHEMBL-TARGET-1",
            disease_state_id="MONDO_0004979",
        )
        flagged_activity = _execute(
            self.case,
            flagged_activity_plan,
            ChemblAdapter(
                "ChEMBL 36",
                transport=FrozenTransport([flagged_activity_payload]),
            ),
        ).seed_emissions[0].seed
        self.assertNotIn(
            ChemicalUniverse.SHELVED_OR_FAILED_ASSETS,
            flagged_activity.chemical_universes,
        )

    def test_chembl_mechanism_record_ids_do_not_collapse_distinct_assertions(self) -> None:
        first = {
            "record_id": 2473324,
            "molecule_chembl_id": "CHEMBL3787344",
            "target_chembl_id": "CHEMBL203",
            "action_type": "INHIBITOR",
            "mechanism_of_action": "Epidermal growth factor receptor erbB1 inhibitor",
        }
        second = {
            **first,
            "mechanism_of_action": "Epidermal growth factor receptor inhibitor",
        }
        payload = {
            "mechanisms": [first, second, second],
            "page_meta": {
                "limit": 250,
                "next": None,
                "offset": 0,
                "previous": None,
                "total_count": 3,
            },
        }
        plan = make_chembl_plan(
            source_release="ChEMBL 36",
            source_snapshot_at="2026-07-21T00:00:00Z",
            resource="mechanism",
            filters={"target_chembl_id": "CHEMBL203"},
            endpoint_ids=(self.endpoint_id,),
            origin_kind="target",
            origin_ids=("CHEMBL203",),
            target_id="CHEMBL203",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0005233",
        )
        proof = _execute(
            self.case,
            plan,
            ChemblAdapter("ChEMBL 36", transport=FrozenTransport([payload])),
        )
        self.assertEqual(proof.reconciliation.normalized_record_count, 3)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        self.assertEqual(len({row.native_record_id for row in proof.normalized_records}), 3)
        self.assertEqual(
            sum(
                row.disposition is RecordDisposition.NO_INTERVENTION_MAPPING
                for row in proof.normalized_records
            ),
            1,
        )

    def test_bindingdb_preserves_affinity_and_documents_missing_fields(self) -> None:
        transport = FrozenTransport([self.responses["bindingdb"]])
        plan = make_bindingdb_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T00:00:00Z",
            uniprot_id="P04637",
            endpoint_ids=(self.endpoint_id,),
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0004979",
        )
        proof = _execute(self.case, plan, BindingDbAdapter("2026-07-20", transport=transport))
        self.assertEqual(proof.reconciliation.source_reported_total, 2)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        observations = [
            record.seed_assertions[0].activity_observations[0]
            for record in proof.normalized_records
        ]
        self.assertEqual({row.activity_type for row in observations}, {"Ki", "IC50"})
        self.assertTrue(all(row.units == "" for row in observations))
        self.assertTrue(any("units" in value for value in proof.source_specific_limitations))

    def test_pubchem_enumerates_assays_before_compounds_and_keeps_inactive_rows(self) -> None:
        aids_transport = FrozenTransport([self.responses["pubchem_gene_aids"]])
        aids_plan = make_pubchem_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T00:00:00Z",
            query_kind=PubChemQueryKind.GENE_ASSAY_IDS,
            identifier="7157",
            endpoint_ids=(self.endpoint_id,),
            target_id="7157",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0004979",
        )
        aids = _execute(self.case, aids_plan, PubChemAdapter("2026-07-20", transport=aids_transport))
        self.assertEqual(aids.reconciliation.normalized_record_count, 2)
        self.assertEqual(aids.reconciliation.emitted_seed_count, 0)
        self.assertTrue(all(row.disposition is RecordDisposition.NO_INTERVENTION_MAPPING for row in aids.normalized_records))

        concise_transport = FrozenTransport([self.responses["pubchem_assay_concise"]])
        concise_plan = make_pubchem_plan(
            source_release="2026-07-20",
            source_snapshot_at="2026-07-20T00:00:00Z",
            query_kind=PubChemQueryKind.ASSAY_CONCISE,
            identifier="1001",
            endpoint_ids=(self.endpoint_id,),
            target_id="7157",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0004979",
        )
        concise = _execute(self.case, concise_plan, PubChemAdapter("2026-07-20", transport=concise_transport))
        self.assertEqual(concise.reconciliation.normalized_record_count, 3)
        self.assertEqual(concise.reconciliation.emitted_seed_count, 2)
        observations = [
            record.seed_assertions[0].activity_observations[0]
            for record in concise.normalized_records
            if record.seed_assertions
        ]
        self.assertTrue(any("Inactive" in row.assay_context for row in observations))
        self.assertEqual({row.units for row in observations}, {"uM"})

    def test_pubchem_accepts_current_information_list_gene_assay_shape(self) -> None:
        payload = {
            "InformationList": {
                "Information": [
                    {"GeneID": 285175, "AID": [1904, 624099]},
                    {"GeneID": 285175, "AID": [651810]},
                ]
            }
        }
        plan = make_pubchem_plan(
            source_release="2026-07-21",
            source_snapshot_at="2026-07-21T00:00:00Z",
            query_kind=PubChemQueryKind.GENE_ASSAY_IDS,
            identifier="285175",
            endpoint_ids=(self.endpoint_id,),
            target_id="285175",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0014777",
        )
        proof = _execute(
            self.case,
            plan,
            PubChemAdapter("2026-07-21", transport=FrozenTransport([payload])),
        )
        self.assertEqual(proof.reconciliation.normalized_record_count, 3)
        self.assertEqual(
            {row.native_record_id for row in proof.normalized_records},
            {"AID:1904", "AID:624099", "AID:651810"},
        )
        self.assertTrue(
            all(
                row.disposition is RecordDisposition.NO_INTERVENTION_MAPPING
                for row in proof.normalized_records
            )
        )

    def test_pubchem_concise_rows_with_repeated_sid_retain_distinct_row_identity(self) -> None:
        payload = {
            "Table": {
                "Columns": {
                    "Column": [
                        "AID",
                        "SID",
                        "CID",
                        "Activity Outcome",
                        "Target GeneID",
                    ]
                },
                "Row": [
                    {"Cell": ["624099", "56478464", "", "Inactive", "208"]},
                    {"Cell": ["624099", "56478464", "", "Inactive", "1489088"]},
                ],
            }
        }
        plan = make_pubchem_plan(
            source_release="2026-07-21",
            source_snapshot_at="2026-07-21T00:00:00Z",
            query_kind=PubChemQueryKind.ASSAY_CONCISE,
            identifier="624099",
            endpoint_ids=(self.endpoint_id,),
            target_id="285175",
            target_organism="Homo sapiens",
            disease_state_id="MONDO_0014777",
        )
        proof = _execute(
            self.case,
            plan,
            PubChemAdapter("2026-07-21", transport=FrozenTransport([payload])),
        )
        self.assertEqual(proof.reconciliation.normalized_record_count, 2)
        self.assertEqual(len({row.native_record_id for row in proof.normalized_records}), 2)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 0)

    def test_unichem_exact_cross_references_are_bounded_and_query_preserved(self) -> None:
        transport = FrozenTransport([self.responses["unichem"]])
        resolver = UniChemIdentityResolver("2026-07-20", transport=transport)
        result = resolver.resolve_inchikey(
            input_seed_id="SEED-EXAMPLE",
            inchikey="BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        )
        self.assertEqual(
            {(row.namespace, row.identifier) for row in result.references},
            {("CHEMBL", "CHEMBL25"), ("PUBCHEM", "2244")},
        )
        self.assertTrue(result.exact_query_url.endswith("BSYNRYMUTXBXSQ-UHFFFAOYSA-N"))
        self.assertEqual(result.response_sha256, hashlib.sha256(canonical_bytes(self.responses["unichem"])).hexdigest().upper())
        self.assertTrue(any("active-moiety" in value for value in result.limitations))

    def test_frozen_response_cache_replays_without_transport(self) -> None:
        plan = make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-20T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(self.endpoint_id,),
            disease_state_id="MONDO_0004979",
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = ContentAddressedRetrievalCache(directory)
            online_transport = FrozenTransport([self.responses["open_targets_target"]])
            first = _execute(
                self.case,
                plan,
                OpenTargetsAdapter("26.06", transport=online_transport),
                cache=cache,
            )
            offline_transport = FrozenTransport([], enabled=False)
            replay = _execute(
                self.case,
                plan,
                OpenTargetsAdapter("26.06", transport=offline_transport),
                cache=cache,
                replay_only=True,
            )
        self.assertEqual(first.coverage_proof_id, replay.coverage_proof_id)
        self.assertEqual(first.normalized_records, replay.normalized_records)
        self.assertNotEqual(first.execution_trace_id, replay.execution_trace_id)
        self.assertEqual(len(online_transport.calls), 1)
        self.assertEqual(len(offline_transport.calls), 0)

    def test_source_total_tampering_remains_fail_closed(self) -> None:
        transport = FrozenTransport(self.responses["chembl_activity_pages"])
        plan = make_chembl_plan(
            source_release="ChEMBL 36",
            source_snapshot_at="2026-07-20T00:00:00Z",
            resource="activity",
            filters={"target_chembl_id": "CHEMBL-TARGET-1"},
            endpoint_ids=(self.endpoint_id,),
            origin_kind="target",
            origin_ids=("CHEMBL-TARGET-1",),
            target_id="CHEMBL-TARGET-1",
            page_size=2,
        )
        proof = _execute(self.case, plan, ChemblAdapter("ChEMBL 36", transport=transport))
        tampered = replace(
            proof,
            content_receipts=(
                replace(proof.content_receipts[0], provider_total=999),
                *proof.content_receipts[1:],
            ),
        )
        with self.assertRaises(RetrievalContractError):
            validate_coverage_proof(self.case, tampered)


class CrossAdapterUnionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = _fixture()
        cls.case = build_case_bundle(cls.fixture["case_input"]).case_revision
        cls.endpoint_id = cls.case.endpoints[0].endpoint_id

    def _large_proofs(self):
        spec = self.fixture["large_union"]
        ot_start, ot_end = spec["open_targets_range"]
        ot_rows = [
            {
                "id": f"OT-LARGE-{logical:06d}",
                "maxClinicalStage": "PHASE_2",
                "drug": {
                    "id": f"CHEMBL{logical:06d}",
                    "name": f"OT compound {logical:06d}",
                    "drugType": "Small molecule",
                    "maximumClinicalStage": "PHASE_2",
                },
                "diseases": [],
            }
            for logical in range(ot_start, ot_end + 1)
        ]
        ot_payload = {
            "data": {
                "target": {
                    "id": "ENSG00000141510",
                    "approvedSymbol": "TP53",
                    "drugAndClinicalCandidates": {"count": len(ot_rows), "rows": ot_rows},
                }
            }
        }
        ot_plan = make_open_targets_plan(
            source_release="26.06-large",
            source_snapshot_at="2026-07-20T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(self.endpoint_id,),
        )
        ot = _execute(
            self.case,
            ot_plan,
            OpenTargetsAdapter("26.06-large", transport=FrozenTransport([ot_payload])),
        )

        ch_start, ch_end = spec["chembl_range"]
        molecules = [
            {
                "molecule_chembl_id": f"CHEMBL{logical:06d}",
                "pref_name": f"ChEMBL compound {logical:06d}",
                "max_phase": 0,
            }
            for logical in range(ch_start, ch_end + 1)
        ]
        chembl_payload = {
            "molecules": molecules,
            "page_meta": {
                "limit": 1000,
                "next": None,
                "offset": 0,
                "previous": None,
                "total_count": len(molecules),
            },
        }
        chembl_plan = make_chembl_plan(
            source_release="ChEMBL 36-large",
            source_snapshot_at="2026-07-20T00:00:00Z",
            resource="molecule",
            filters={"molecule_chembl_id__isnull": False},
            endpoint_ids=(self.endpoint_id,),
            origin_kind="source_mapping",
            origin_ids=(ot_plan.query_plan_id,),
            target_id="ENSG00000141510",
        )
        chembl = _execute(
            self.case,
            chembl_plan,
            ChemblAdapter("ChEMBL 36-large", transport=FrozenTransport([chembl_payload])),
        )

        pc_start, pc_end = spec["pubchem_logical_range"]
        columns = [
            "AID", "SID", "CID", "Activity Outcome", "Target Accession",
            "Target GeneID", "Activity Value [uM]", "Activity Name", "Assay Name",
            "Assay Type", "PubMed ID", "RNAi",
        ]
        pubchem_rows = [
            {
                "Cell": [
                    "9001", str(300000 + logical), str(100000 + logical), "Active",
                    "P04637", "7157", "1", "IC50", "Large frozen assay",
                    "Confirmatory", "", "",
                ]
            }
            for logical in range(pc_start, pc_end + 1)
        ]
        pubchem_payload = {"Table": {"Columns": {"Column": columns}, "Row": pubchem_rows}}
        pubchem_plan = make_pubchem_plan(
            source_release="2026-07-20-large",
            source_snapshot_at="2026-07-20T00:00:00Z",
            query_kind=PubChemQueryKind.ASSAY_CONCISE,
            identifier="9001",
            endpoint_ids=(self.endpoint_id,),
            target_id="7157",
        )
        pubchem = _execute(
            self.case,
            pubchem_plan,
            PubChemAdapter("2026-07-20-large", transport=FrozenTransport([pubchem_payload])),
        )

        bd_start, bd_end = spec["bindingdb_logical_range"]
        affinities = [
            {
                "bdb.monomerid": 200000 + logical,
                "bdb.smile": f"C{logical}N",
                "bdb.affinity_type": "Ki",
                "bdb.affinity": str(logical),
            }
            for logical in range(bd_start, bd_end + 1)
        ]
        binding_payload = {
            "getLindsByUniprotResponse": {
                "bdb.hit": str(len(affinities)),
                "bdb.length": "NA",
                "bdb.uniprot_length": "393",
                "bdb.primary": "P04637",
                "bdb.alternative": ["P04637"],
                "bdb.affinities": affinities,
            }
        }
        binding_plan = make_bindingdb_plan(
            source_release="2026-07-20-large",
            source_snapshot_at="2026-07-20T00:00:00Z",
            uniprot_id="P04637",
            endpoint_ids=(self.endpoint_id,),
        )
        binding = _execute(
            self.case,
            binding_plan,
            BindingDbAdapter("2026-07-20-large", transport=FrozenTransport([binding_payload])),
        )
        return spec, (ot, chembl, pubchem, binding)

    def test_large_cross_adapter_universe_survives_exact_dedup_and_retains_provenance(self) -> None:
        spec, proofs = self._large_proofs()
        pubchem_by_cid = {
            emission.seed.compound_hint.value: emission.seed.seed_id
            for emission in proofs[2].seed_emissions
        }
        binding_by_id = {
            emission.seed.compound_hint.value: emission.seed.seed_id
            for emission in proofs[3].seed_emissions
        }
        resolutions: list[UniChemIdentityResolution] = []
        for logical in range(251, 301):
            seed_id = pubchem_by_cid[str(100000 + logical)]
            references = (
                UniChemCrossReference(
                    namespace="CHEMBL",
                    identifier=f"CHEMBL{logical:06d}",
                    source_id=1,
                    source_url="",
                ),
                UniChemCrossReference(
                    namespace="PUBCHEM",
                    identifier=str(100000 + logical),
                    source_id=22,
                    source_url="",
                ),
            )
            resolutions.append(
                make_unichem_identity_resolution(
                    input_seed_id=seed_id,
                    query_identifier=f"FROZEN-PC-{logical:06d}",
                    exact_query_url=f"https://example.invalid/unichem/pc/{logical}",
                    source_release="2026-07-20-large",
                    response_sha256="0" * 64,
                    references=references,
                    limitations=("Frozen deterministic union fixture.",),
                )
            )
        for logical in range(401, 451):
            seed_id = binding_by_id[str(200000 + logical)]
            references = (
                UniChemCrossReference(
                    namespace="BINDINGDB",
                    identifier=str(200000 + logical),
                    source_id=31,
                    source_url="",
                ),
                UniChemCrossReference(
                    namespace="PUBCHEM",
                    identifier=str(100000 + logical),
                    source_id=22,
                    source_url="",
                ),
            )
            resolutions.append(
                make_unichem_identity_resolution(
                    input_seed_id=seed_id,
                    query_identifier=f"FROZEN-BDB-{logical:06d}",
                    exact_query_url=f"https://example.invalid/unichem/bdb/{logical}",
                    source_release="2026-07-20-large",
                    response_sha256="0" * 64,
                    references=references,
                    limitations=("Frozen deterministic union fixture.",),
                )
            )
        union = union_cross_adapter_chemicals(proofs, unichem_resolutions=resolutions)
        self.assertEqual(union.input_seed_count, spec["expected_input_seeds"])
        self.assertEqual(union.union_record_count, spec["expected_union_records"])
        self.assertEqual(union.provenance_edge_count, spec["expected_input_seeds"])
        self.assertEqual(
            {seed_id for row in union.records for seed_id in row.seed_ids},
            {emission.seed.seed_id for proof in proofs for emission in proof.seed_emissions},
        )
        self.assertTrue(any(set(row.source_ids) >= {"open-targets-platform", "chembl"} for row in union.records))
        self.assertTrue(any(set(row.source_ids) >= {"chembl", "pubchem"} for row in union.records))
        self.assertTrue(any(set(row.source_ids) >= {"pubchem", "bindingdb"} for row in union.records))
        replay = union_cross_adapter_chemicals(tuple(reversed(proofs)), unichem_resolutions=reversed(resolutions))
        self.assertEqual(union, replay)


if __name__ == "__main__":
    unittest.main(verbosity=2)
