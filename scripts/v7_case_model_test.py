#!/usr/bin/env python3
"""Direct tests for the schema-v7 case model and its isolated CLI boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields as dataclass_fields, replace
from pathlib import Path
from typing import Any
from unittest import mock

import v7_case_model

from v7_case_model import (
    CaseInputError,
    CaseRevision,
    CaseStatus,
    EndpointDirection,
    EndpointPriority,
    EndpointRelationshipType,
    EndpointRole,
    EndpointType,
    GeneDiseaseState,
    ProvenanceClassification,
    QualifiedValue,
    TherapeuticModulation,
    UnresolvedKind,
    V7CompatibilityAdapter,
    ValueStatus,
    build_case_bundle,
    initialize_case,
    inspect_artifact,
    validate_case_revision,
    validation_metadata,
)


SCRIPT_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_ROOT.parent
ORCHESTRATOR = SCRIPT_ROOT / "orchestrate_program.py"
BENCHMARK_ROOT = SKILL_ROOT / "benchmarks" / "schema_v7"
LEGACY_ROOT = BENCHMARK_ROOT / "legacy"


def endpoint_input(
    stable_key: str = "primary",
    display_label: str = "Primary clinical outcome",
    *,
    construct: str = "HP:0001250",
    role: str = "benefit",
    endpoint_type: str = "clinical_outcome",
    relationships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "stable_key": stable_key,
        "display_label": display_label,
        "construct": construct,
        "role": role,
        "endpoint_type": endpoint_type,
        "population": "Adults",
        "disease_stage": "Established disease",
        "timeframe": "24 weeks",
        "measurement": "Validated outcome instrument",
        "disease_context": "Target condition",
        "direction": "decrease_is_benefit",
        "priority": "high",
        "required": True,
        "relationships": [] if relationships is None else relationships,
    }


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file()
    }


def endpoint_by_key(bundle: Any, key: str) -> Any:
    return next(
        endpoint
        for endpoint in bundle.case_revision.endpoints
        if endpoint.stable_key.value == key
    )


def run_cli(*arguments: object) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", str(ORCHESTRATOR), *(str(value) for value in arguments)],
        cwd=SCRIPT_ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class CaseAnchorTests(unittest.TestCase):
    def test_gene_only_case(self) -> None:
        bundle = build_case_bundle(
            {"gene": "TP53", "endpoints": [endpoint_input()]}
        )
        case = bundle.case_revision

        self.assertEqual(case.gene.status, ValueStatus.KNOWN)
        self.assertEqual(case.gene.value.concept.coding.value.namespace, "HGNC_SYMBOL")
        self.assertEqual(case.gene.value.concept.coding.value.identifier, "TP53")
        self.assertEqual(case.disease.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.phenotypes.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.case_status, CaseStatus.READY)

    def test_disease_only_case(self) -> None:
        bundle = build_case_bundle(
            {"disease": "MONDO:0004979", "endpoints": [endpoint_input()]}
        )
        case = bundle.case_revision

        self.assertEqual(case.gene.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.disease.status, ValueStatus.KNOWN)
        self.assertEqual(case.disease.value.coding.value.namespace, "MONDO")
        self.assertEqual(case.disease.value.coding.value.identifier, "0004979")
        self.assertEqual(case.phenotypes.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.case_status, CaseStatus.READY)

    def test_phenotype_only_case_creates_unresolved_endpoint_slot_when_omitted(self) -> None:
        bundle = build_case_bundle({"phenotype": "HP:0001250"})
        case = bundle.case_revision

        self.assertEqual(case.gene.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.disease.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.phenotypes.status, ValueStatus.KNOWN)
        self.assertEqual(len(case.phenotypes.value), 1)
        self.assertEqual(case.phenotypes.value[0].coding.value.identifier, "0001250")
        self.assertEqual(case.case_status, CaseStatus.NEEDS_RESOLUTION)
        self.assertEqual(len(case.endpoints), 1)
        slot = case.endpoints[0]
        self.assertEqual(slot.stable_key.value, "unresolved-input-slot:1")
        self.assertEqual(slot.display_label.status, ValueStatus.UNKNOWN)
        self.assertEqual(slot.construct.status, ValueStatus.UNKNOWN)
        unresolved = [row for row in case.unresolved_inputs if row.path == "/endpoints"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].kind, UnresolvedKind.MISSING)
        self.assertTrue(unresolved[0].blocking)

    def test_ambiguous_disease_and_phenotype_are_explicit_and_blocking(self) -> None:
        bundle = build_case_bundle(
            {
                "disease": {
                    "label": "cardiomyopathy",
                    "candidates": ["MONDO:0004994", "MONDO:0005045"],
                },
                "phenotypes": [
                    {
                        "label": "weakness",
                        "candidates": ["HP:0001324", "HP:0003701"],
                    }
                ],
                "endpoints": [endpoint_input()],
            }
        )
        case = bundle.case_revision

        self.assertEqual(case.disease.value.coding.status, ValueStatus.UNKNOWN)
        self.assertEqual(case.phenotypes.value[0].coding.status, ValueStatus.UNKNOWN)
        ambiguous = {
            row.path: row
            for row in case.unresolved_inputs
            if row.kind is UnresolvedKind.AMBIGUOUS
        }
        self.assertEqual(
            set(ambiguous), {"/disease/coding", "/phenotypes/0/coding"}
        )
        self.assertTrue(all(row.blocking for row in ambiguous.values()))
        self.assertEqual(
            ambiguous["/disease/coding"].candidates,
            ("MONDO:0004994", "MONDO:0005045"),
        )
        self.assertEqual(case.case_status, CaseStatus.NEEDS_RESOLUTION)


class EndpointModelTests(unittest.TestCase):
    def test_multiple_endpoints_have_typed_resolved_relationships(self) -> None:
        primary = endpoint_input(
            "primary",
            "Primary benefit",
            relationships=[
                {
                    "type": "supports",
                    "related_endpoint_key": "safety",
                    "rationale": "Benefit is interpreted with the safety outcome.",
                }
            ],
        )
        safety = endpoint_input(
            "safety",
            "Serious adverse events",
            construct="HP:0001939",
            role="safety",
            endpoint_type="safety_outcome",
            relationships=[
                {
                    "relationship_type": "safety_constraint_for",
                    "target_endpoint_key": "primary",
                    "rationale": "Constrains acceptable benefit.",
                }
            ],
        )
        bundle = build_case_bundle(
            {"disease": "MONDO:0004979", "endpoints": [primary, safety]}
        )
        primary_endpoint = endpoint_by_key(bundle, "primary")
        safety_endpoint = endpoint_by_key(bundle, "safety")

        self.assertEqual(len(bundle.case_revision.endpoints), 2)
        self.assertEqual(primary_endpoint.role.value, EndpointRole.BENEFIT)
        self.assertEqual(primary_endpoint.endpoint_type.value, EndpointType.CLINICAL_OUTCOME)
        self.assertEqual(primary_endpoint.direction.value, EndpointDirection.DECREASE_IS_BENEFIT)
        self.assertEqual(primary_endpoint.priority.value, EndpointPriority.HIGH)
        self.assertEqual(primary_endpoint.relationships.status, ValueStatus.KNOWN)
        self.assertEqual(
            primary_endpoint.relationships.value[0].relationship_type,
            EndpointRelationshipType.SUPPORTS,
        )
        self.assertEqual(
            primary_endpoint.relationships.value[0].related_endpoint_id,
            safety_endpoint.endpoint_id,
        )
        self.assertEqual(
            safety_endpoint.relationships.value[0].relationship_type,
            EndpointRelationshipType.SAFETY_CONSTRAINT_FOR,
        )
        self.assertEqual(
            safety_endpoint.relationships.value[0].related_endpoint_id,
            primary_endpoint.endpoint_id,
        )

    def test_endpoint_id_is_label_independent_and_stable_for_same_key(self) -> None:
        first = build_case_bundle(
            {
                "gene": "TP53",
                "endpoints": [endpoint_input("walk", "Six-minute walk distance")],
            }
        )
        second = build_case_bundle(
            {
                "gene": "TP53",
                "endpoints": [endpoint_input("WALK", "6MWD")],
            }
        )
        first_endpoint = first.case_revision.endpoints[0]
        second_endpoint = second.case_revision.endpoints[0]

        self.assertEqual(first_endpoint.endpoint_id, second_endpoint.endpoint_id)
        self.assertNotEqual(first_endpoint.endpoint_id, first_endpoint.display_label.value)
        self.assertNotEqual(second_endpoint.endpoint_id, second_endpoint.display_label.value)
        self.assertNotEqual(
            first.case_revision.case_revision_id,
            second.case_revision.case_revision_id,
        )

    def test_endpoint_construct_codes_support_clinical_and_extensible_namespaces(self) -> None:
        loinc = endpoint_input("loinc-outcome", "HbA1c")
        loinc["construct"] = {"namespace": "LOINC", "identifier": "4548-4"}
        custom = endpoint_input("custom-outcome", "Custom function score")
        custom["construct"] = {
            "namespace": "CUSTOM_OUTCOME",
            "identifier": "ABC-1",
        }
        bundle = build_case_bundle(
            {"disease": "MONDO:0005015", "endpoints": [loinc, custom]}
        )
        codings = {
            endpoint.stable_key.value: endpoint.construct.value.coding.value
            for endpoint in bundle.case_revision.endpoints
        }
        self.assertEqual(
            (codings["loinc-outcome"].namespace, codings["loinc-outcome"].identifier),
            ("LOINC", "4548-4"),
        )
        self.assertEqual(
            (codings["custom-outcome"].namespace, codings["custom-outcome"].identifier),
            ("CUSTOM_OUTCOME", "ABC-1"),
        )

    def test_supplied_endpoint_without_coded_construct_is_blocking(self) -> None:
        endpoint = endpoint_input()
        endpoint.pop("construct")
        bundle = build_case_bundle({"gene": "TP53", "endpoints": [endpoint]})
        self.assertEqual(bundle.case_revision.case_status, CaseStatus.NEEDS_RESOLUTION)
        blocking_paths = {
            row.path for row in bundle.case_revision.unresolved_inputs if row.blocking
        }
        self.assertIn("/endpoints/0/construct/coding", blocking_paths)


class ContextAndProvenanceTests(unittest.TestCase):
    def test_supplied_normalized_inferred_and_unresolved_provenance(self) -> None:
        bundle = build_case_bundle(
            {"gene": "  tp53  ", "endpoints": [endpoint_input()]}
        )
        entries = bundle.provenance.entries

        gene_entry = next(row for row in entries if row.path == "/gene")
        coding_entry = next(row for row in entries if row.path == "/gene/concept/coding")
        endpoint_id_entry = next(
            row for row in entries if row.path == "/endpoints/0/endpoint_id"
        )
        disease_entry = next(row for row in entries if row.path == "/disease")

        self.assertIn(ProvenanceClassification.USER_SUPPLIED, gene_entry.classifications)
        self.assertEqual(
            coding_entry.classifications,
            (
                ProvenanceClassification.USER_SUPPLIED,
                ProvenanceClassification.NORMALIZED,
            ),
        )
        self.assertEqual(
            endpoint_id_entry.classifications,
            (ProvenanceClassification.INFERRED,),
        )
        self.assertEqual(
            disease_entry.classifications,
            (
                ProvenanceClassification.INFERRED,
                ProvenanceClassification.UNRESOLVED,
            ),
        )

    def test_gene_disease_state_and_desired_modulation_remain_distinct(self) -> None:
        bundle = build_case_bundle(
            {
                "gene": {
                    "identifier": "TP53",
                    "disease_associated_state": "GOF",
                    "desired_therapeutic_modulation": "activate",
                },
                "endpoints": [endpoint_input()],
            }
        )
        gene = bundle.case_revision.gene.value

        self.assertEqual(
            gene.disease_associated_state.value,
            GeneDiseaseState.GAIN_OF_FUNCTION,
        )
        self.assertEqual(
            gene.desired_therapeutic_modulation.value,
            TherapeuticModulation.ACTIVATE,
        )
        self.assertNotEqual(
            gene.disease_associated_state.value.value,
            gene.desired_therapeutic_modulation.value.value,
        )

        omitted = build_case_bundle(
            {
                "gene": {
                    "identifier": "TP53",
                    "disease_associated_state": "loss_of_function",
                },
                "endpoints": [endpoint_input()],
            }
        ).case_revision.gene.value
        self.assertEqual(
            omitted.disease_associated_state.value,
            GeneDiseaseState.LOSS_OF_FUNCTION,
        )
        self.assertEqual(
            omitted.desired_therapeutic_modulation.status,
            ValueStatus.UNKNOWN,
        )

    def test_full_case_context_and_target_product_profile_are_typed(self) -> None:
        endpoint = endpoint_input()
        endpoint.pop("population")
        endpoint.pop("disease_stage")
        endpoint.pop("timeframe")
        bundle = build_case_bundle(
            {
                "gene": "TP53",
                "disease_subtype": "  HER2-positive  ",
                "population": {
                    "description": "Adults with measurable disease",
                    "inclusion": [" ECOG 0-1 ", "Age >= 18", "ecog 0-1"],
                    "exclusion": ["Pregnancy"],
                    "genotype": ["ERBB2 amplified"],
                },
                "tissue": {"target": "Breast", "relevance": "Primary tumour"},
                "stage": {"stage": "Metastatic", "severity": "Advanced"},
                "target_product_profile": {
                    "intended_benefit": "Delay progression",
                    "setting": "Second-line therapy",
                    "route_constraints": [" oral ", "intravenous", "ORAL"],
                    "excluded_routes": ["inhaled"],
                    "regimen_constraints": ["Once daily"],
                    "exposure_constraints": ["CNS exposure not required"],
                    "time_horizon": "24 weeks",
                    "acceptable_risk_constraints": "No grade 4 cardiotoxicity",
                },
                "contraindications": [
                    " renal failure ",
                    "QT prolongation",
                    "RENAL FAILURE",
                ],
                "excluded_intervention_categories": [
                    "gene therapy",
                    "Devices",
                ],
                "endpoints": [endpoint],
            }
        )
        case = bundle.case_revision
        profile = case.target_product_profile
        inherited_endpoint = case.endpoints[0]

        self.assertEqual(case.disease_subtype.value, "HER2-positive")
        self.assertEqual(case.population.description.value, "Adults with measurable disease")
        self.assertEqual(case.population.inclusion.value, ("Age >= 18", "ECOG 0-1"))
        self.assertEqual(case.population.exclusion.value, ("Pregnancy",))
        self.assertEqual(case.population.genotypes.value, ("ERBB2 amplified",))
        self.assertEqual(case.tissue.target.value, "Breast")
        self.assertEqual(case.tissue.relevance.value, "Primary tumour")
        self.assertEqual(case.disease_stage.stage.value, "Metastatic")
        self.assertEqual(case.disease_stage.severity.value, "Advanced")
        self.assertEqual(profile.intended_benefit.value, "Delay progression")
        self.assertEqual(profile.setting.value, "Second-line therapy")
        self.assertEqual(profile.allowed_routes.value, ("intravenous", "oral"))
        self.assertEqual(profile.excluded_routes.value, ("inhaled",))
        self.assertEqual(profile.regimen_constraints.value, ("Once daily",))
        self.assertEqual(profile.exposure_constraints.value, ("CNS exposure not required",))
        self.assertEqual(profile.time_horizon.value, "24 weeks")
        self.assertEqual(profile.acceptable_risk.value, "No grade 4 cardiotoxicity")
        self.assertEqual(case.contraindications.value, ("QT prolongation", "renal failure"))
        self.assertEqual(
            case.excluded_intervention_categories.value,
            ("Devices", "gene therapy"),
        )
        self.assertEqual(
            inherited_endpoint.population.value,
            "Adults with measurable disease",
        )
        self.assertEqual(inherited_endpoint.disease_stage.value, "Metastatic")
        self.assertEqual(inherited_endpoint.timeframe.value, "24 weeks")

    def test_model_and_provenance_are_deeply_immutable_and_schema_is_derived(self) -> None:
        bundle = build_case_bundle(
            {"gene": "TP53", "endpoints": [endpoint_input()]}
        )
        with self.assertRaises(TypeError):
            bundle.case_revision.original_input["gene"] = "BRCA1"
        endpoint_inputs = bundle.case_revision.original_input["endpoints"]
        with self.assertRaises(TypeError):
            endpoint_inputs[0]["stable_key"] = "changed"
        metadata = validation_metadata()
        self.assertEqual(
            metadata["dataclasses"]["CaseRevision"]["required_fields"],
            [field.name for field in dataclass_fields(CaseRevision)],
        )

    def test_typed_validator_rejects_wrong_nested_enum_type(self) -> None:
        case = build_case_bundle(
            {"gene": "TP53", "endpoints": [endpoint_input()]}
        ).case_revision
        invalid_endpoint = replace(
            case.endpoints[0],
            role=QualifiedValue(status=ValueStatus.KNOWN, value="benefit", reason=""),
        )
        invalid_case = replace(case, endpoints=(invalid_endpoint,))
        with self.assertRaisesRegex(CaseInputError, "EndpointRole"):
            validate_case_revision(invalid_case)


class ValidationAndInitializationTests(unittest.TestCase):
    def test_malformed_and_contradictory_inputs_are_rejected(self) -> None:
        duplicate_a = endpoint_input("duplicate", "First")
        duplicate_b = endpoint_input("duplicate", "Second")
        missing_target = endpoint_input(
            relationships=[
                {
                    "type": "supports",
                    "related_endpoint_key": "absent",
                    "rationale": "Invalid target",
                }
            ]
        )
        self_target = endpoint_input(
            relationships=[
                {
                    "type": "supports",
                    "related_endpoint_key": "primary",
                    "rationale": "Invalid self-reference",
                }
            ]
        )
        same_id_and_label = endpoint_input(display_label="ep-primary")
        same_id_and_label["endpoint_id"] = "EP-PRIMARY"
        malformed_id = endpoint_input()
        malformed_id["endpoint_id"] = "not an endpoint id"
        cases = {
            "no anchor": {"endpoints": [endpoint_input()]},
            "wrong schema": {
                "schema_version": 6,
                "gene": "TP53",
                "endpoints": [endpoint_input()],
            },
            "float schema": {
                "schema_version": 7.0,
                "gene": "TP53",
                "endpoints": [endpoint_input()],
            },
            "unknown top-level field": {
                "gene": "TP53",
                "mystery": True,
                "endpoints": [endpoint_input()],
            },
            "contradictory aliases": {
                "gene": "TP53",
                "human_gene": "BRCA1",
                "endpoints": [endpoint_input()],
            },
            "empty endpoint portfolio": {"gene": "TP53", "endpoints": []},
            "empty endpoint object": {"gene": "TP53", "endpoints": [{}]},
            "explicit null": {
                "gene": "TP53",
                "disease_subtype": None,
                "endpoints": [endpoint_input()],
            },
            "overlapping population criteria": {
                "gene": "TP53",
                "population": {"inclusion": ["Adult"], "exclusion": ["adult"]},
                "endpoints": [endpoint_input()],
            },
            "overlapping route constraints": {
                "gene": "TP53",
                "target_product_profile": {
                    "allowed_routes": ["oral"],
                    "excluded_routes": ["ORAL"],
                },
                "endpoints": [endpoint_input()],
            },
            "duplicate endpoint identity": {
                "gene": "TP53",
                "endpoints": [duplicate_a, duplicate_b],
            },
            "missing relationship target": {
                "gene": "TP53",
                "endpoints": [missing_target],
            },
            "self relationship": {"gene": "TP53", "endpoints": [self_target]},
            "endpoint id equals label": {
                "gene": "TP53",
                "endpoints": [same_id_and_label],
            },
            "malformed endpoint id": {
                "gene": "TP53",
                "endpoints": [malformed_id],
            },
            "unknown qualified wrapper field": {
                "gene": "TP53",
                "endpoints": [
                    {
                        **endpoint_input(),
                        "role": {
                            "status": "known",
                            "value": "benefit",
                            "extra": True,
                        },
                    }
                ],
            },
        }
        for name, raw_input in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(CaseInputError):
                    build_case_bundle(raw_input)

    def test_initialization_artifacts_and_manifest_are_byte_deterministic(self) -> None:
        raw_input = {
            "gene": {
                "identifier": "TP53",
                "disease_associated_state": "gain_of_function",
                "desired_therapeutic_modulation": "inhibit",
            },
            "disease": "MONDO:0004979",
            "endpoints": [endpoint_input()],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_root = root / "first"
            second_root = root / "second"
            first_manifest = initialize_case(first_root, raw_input)
            second_manifest = initialize_case(second_root, raw_input)

            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(snapshot(first_root), snapshot(second_root))
            self.assertEqual(
                set(snapshot(first_root)),
                {
                    "case_input.json",
                    "case_model_provenance.json",
                    "case_model_schema.json",
                    "case_revision.json",
                    "schema_manifest.json",
                },
            )
            stored_manifest = json.loads(
                (first_root / "schema_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored_manifest, first_manifest)
            self.assertEqual(stored_manifest["schema_version"], 7)
            self.assertEqual(
                stored_manifest["runtime_state"],
                "not_implemented_in_foundational_case_slice",
            )
            for name, expected_hash in stored_manifest["artifacts"].items():
                actual_hash = hashlib.sha256((first_root / name).read_bytes()).hexdigest().upper()
                self.assertEqual(actual_hash, expected_hash, name)

    def test_invalid_initialization_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            absent_root = base / "absent"
            with self.assertRaises(CaseInputError):
                initialize_case(absent_root, {"endpoints": [endpoint_input()]})
            self.assertFalse(absent_root.exists())

            empty_root = base / "empty"
            empty_root.mkdir()
            with self.assertRaises(CaseInputError):
                initialize_case(empty_root, {"gene": "TP53", "endpoints": []})
            self.assertEqual(list(empty_root.iterdir()), [])

    def test_initialization_failure_does_not_publish_partial_container(self) -> None:
        raw_input = {"gene": "TP53", "endpoints": [endpoint_input()]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case"
            real_write = v7_case_model._atomic_write
            calls = 0

            def fail_third_write(path: Path, payload: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("synthetic staging failure")
                real_write(path, payload)

            with mock.patch.object(
                v7_case_model, "_atomic_write", side_effect=fail_third_write
            ):
                with self.assertRaisesRegex(OSError, "synthetic staging failure"):
                    initialize_case(root, raw_input)
            self.assertFalse(root.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_native_inspection_rejects_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "case"
            initialize_case(root, {"gene": "TP53", "endpoints": [endpoint_input()]})
            payload = json.loads(
                (root / "case_revision.json").read_text(encoding="utf-8")
            )
            payload["schema_version"] = 6
            (root / "case_revision.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(CaseInputError, "hash mismatch"):
                inspect_artifact(root)


class CompatibilityAndCliTests(unittest.TestCase):
    def test_schema_v3_through_v6_standalone_fixtures_remain_byte_identical(self) -> None:
        adapter = V7CompatibilityAdapter()
        checksums = json.loads(
            (BENCHMARK_ROOT / "fixture_checksums.json").read_text(encoding="utf-8")
        )["sha256"]
        for version in (3, 4, 5, 6):
            with self.subTest(schema_version=version):
                path = LEGACY_ROOT / f"schema-v{version}.json"
                before = path.read_bytes()
                relative = f"legacy/schema-v{version}.json"
                self.assertEqual(
                    hashlib.sha256(before).hexdigest().upper(),
                    checksums[relative],
                )
                self.assertEqual(inspect_artifact(path)["schema_version"], version)
                self.assertEqual(adapter.inspect_legacy(path)["mode"], "read_only")
                inspect_decision = adapter.request_legacy_operation(path, "inspect")
                self.assertTrue(inspect_decision["allowed"])
                self.assertEqual(inspect_decision["mode"], "read_only")
                for operation in ("resume", "write", "append", "finalize", "initialize"):
                    decision = adapter.request_legacy_operation(path, operation)
                    self.assertFalse(decision["allowed"], (version, operation, decision))
                    self.assertEqual(decision["mode"], "read_only")
                self.assertEqual(path.read_bytes(), before)

    def test_legacy_run_folder_inspection_and_refusal_are_byte_identical(self) -> None:
        adapter = V7CompatibilityAdapter()
        fixture = (LEGACY_ROOT / "schema-v5.json").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            run_folder = Path(temporary) / "legacy-run"
            run_folder.mkdir()
            (run_folder / "program_state.json").write_bytes(fixture)
            (run_folder / "execution_plan.json").write_bytes(fixture)
            (run_folder / "opaque-payload.bin").write_bytes(b"legacy payload\x00\xff")
            before = snapshot(run_folder)

            inspection = adapter.inspect_legacy(run_folder)
            self.assertEqual(inspection["schema_version"], 5)
            self.assertEqual(inspection["artifact_kind"], "folder")
            self.assertEqual(inspection["mode"], "read_only")
            self.assertEqual(inspection["file_count"], 3)
            for operation in ("resume", "write", "append", "finalize", "initialize"):
                decision = adapter.request_legacy_operation(run_folder, operation)
                self.assertFalse(decision["allowed"], (operation, decision))
                self.assertEqual(decision["mode"], "read_only")

            self.assertEqual(snapshot(run_folder), before)

    def test_incomplete_schema_v6_fixture_cannot_enter_historical_runtime(self) -> None:
        fixture = (LEGACY_ROOT / "schema-v6.json").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            run_folder = Path(temporary) / "legacy-v6-fixture"
            run_folder.mkdir()
            (run_folder / "program_state.json").write_bytes(fixture)
            (run_folder / "execution_plan.json").write_bytes(fixture)
            before = snapshot(run_folder)

            rejected = run_cli("resume", run_folder)
            self.assertEqual(rejected.returncode, 1, rejected.stdout)
            self.assertIn("read-only", rejected.stderr)
            self.assertEqual(snapshot(run_folder), before)

    def test_v7_cli_initializes_inspects_and_blocks_runtime_until_case_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_folder = Path(temporary) / "v7-run"
            initialized = run_cli(
                "init",
                run_folder,
                "--schema-version",
                7,
                "--human-gene",
                "TP53",
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            initialized_payload = json.loads(initialized.stdout)
            self.assertTrue(initialized_payload["ok"])
            self.assertEqual(initialized_payload["result"]["schema_version"], 7)
            self.assertEqual(
                initialized_payload["result"]["case_status"],
                CaseStatus.NEEDS_RESOLUTION.value,
            )
            before = snapshot(run_folder)

            inspected = run_cli("inspect", run_folder)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            inspection_payload = json.loads(inspected.stdout)["result"]
            self.assertEqual(inspection_payload["schema_version"], 7)
            self.assertFalse(inspection_payload["legacy"])
            self.assertEqual(inspection_payload["mode"], "native_read_only_inspection")
            self.assertEqual(snapshot(run_folder), before)

            runtime_status = run_cli("status", run_folder)
            self.assertEqual(runtime_status.returncode, 0, runtime_status.stderr)
            status_payload = json.loads(runtime_status.stdout)["result"]
            self.assertEqual(status_payload["state"]["status"], "blocked")
            self.assertEqual(
                status_payload["state"]["blocked_reason"],
                "case_revision_needs_resolution",
            )
            self.assertEqual(status_payload["job_counts"]["committed"], 1)
            after_status = snapshot(run_folder)
            for name in (
                "case_input.json",
                "case_model_provenance.json",
                "case_model_schema.json",
                "case_revision.json",
                "schema_manifest.json",
            ):
                self.assertEqual(after_status[name], before[name], name)

    def test_cli_init_without_schema_flag_keeps_default_v6_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_folder = Path(temporary) / "default-v6-run"
            initialized = run_cli("init", run_folder, "--human-gene", "TP53")

            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            payload = json.loads(initialized.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["schema_version"], 6)
            self.assertEqual(
                json.loads((run_folder / "program_state.json").read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                6,
            )
            self.assertEqual(
                json.loads((run_folder / "execution_plan.json").read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                6,
            )
            self.assertEqual(
                json.loads((run_folder / "case.json").read_text(encoding="utf-8")),
                {"human_gene": "TP53"},
            )
            self.assertFalse((run_folder / "schema_manifest.json").exists())

    def test_case_file_schema_version_negotiates_or_rejects_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case_file = root / "case-v7.json"
            case_file.write_text(
                json.dumps({"schema_version": 7, "human_gene": "TP53"}),
                encoding="utf-8",
            )
            negotiated_root = root / "negotiated-v7"
            negotiated = run_cli("init", negotiated_root, "--case-file", case_file)
            self.assertEqual(negotiated.returncode, 0, negotiated.stderr)
            self.assertEqual(json.loads(negotiated.stdout)["result"]["schema_version"], 7)
            self.assertTrue((negotiated_root / "schema_manifest.json").is_file())
            self.assertFalse((negotiated_root / "program_state.json").exists())
            self.assertTrue((negotiated_root / "runtime_v7" / "execution_plan.json").is_file())

            conflict_root = root / "conflict"
            conflict = run_cli(
                "init",
                conflict_root,
                "--schema-version",
                6,
                "--case-file",
                case_file,
            )
            self.assertEqual(conflict.returncode, 1, conflict.stdout)
            self.assertIn("conflicts with --schema-version 6", conflict.stderr)
            self.assertFalse(conflict_root.exists())

            duplicate_root = root / "duplicate-anchor"
            duplicate = run_cli(
                "init",
                duplicate_root,
                "--case-file",
                case_file,
                "--human-gene",
                "BRCA1",
            )
            self.assertEqual(duplicate.returncode, 1, duplicate.stdout)
            self.assertIn("conflicts with the command-line value", duplicate.stderr)
            self.assertFalse(duplicate_root.exists())

            missing_root = root / "missing-file"
            missing = run_cli(
                "init",
                missing_root,
                "--schema-version",
                7,
                "--case-file",
                root / "absent.json",
                "--human-gene",
                "TP53",
            )
            self.assertEqual(missing.returncode, 1, missing.stdout)
            self.assertIn("Case file does not exist", missing.stderr)
            self.assertFalse(missing_root.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
