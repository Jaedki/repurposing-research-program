#!/usr/bin/env python3
"""Production acceptance tests for schema-v7 identity and seed disposition."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from v7_case_model import build_case_bundle
from v7_production_disposition import (
    NORMALIZATION_POLICY_VERSION,
    DispositionAggregateConflictError,
    DispositionAggregateError,
    V7DispositionAdapter,
    validate_disposition_aggregate,
)


def endpoint_input() -> dict[str, Any]:
    return {
        "stable_key": "benefit",
        "display_label": "Benefit endpoint",
        "construct": "HP:0001250",
        "role": "benefit",
        "endpoint_type": "clinical_outcome",
        "population": "Adults",
        "disease_stage": "Established disease",
        "timeframe": "24 weeks",
        "measurement": "Declared measure",
        "disease_context": "Target disease",
        "direction": "decrease_is_benefit",
        "priority": "high",
        "required": True,
        "relationships": [],
    }


def ready_case() -> Any:
    return build_case_bundle(
        {
            "gene": "TP53",
            "disease": "MONDO:0004979",
            "endpoints": [endpoint_input()],
        }
    ).case_revision


def seed(case: Any, index: int, *, raw_name: str | None = None) -> dict[str, Any]:
    seed_id = f"SEED-{index:05d}"
    return {
        "seed_id": seed_id,
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "endpoint_ids": [case.endpoints[0].endpoint_id],
        "compound_hint": {
            "kind": "database_identifier",
            "value": raw_name or f"SOURCE-{index:05d}",
            "namespace": "SOURCE-DB",
        },
        "source_mapping_id": f"MAP-{index:05d}",
        "discovery_route_ids": [f"ROUTE-{index:05d}-A", f"ROUTE-{index:05d}-B"],
        "structured_routes": [],
        "evidence_modalities": ["authoritative_pharmacology"],
        "chemical_universes": ["preclinical_or_tool_compounds"],
        "development_status_hint": {
            "status": "unknown",
            "value": None,
            "reason": "Not reported at discovery depth.",
        },
        "identity_status": "unassessed",
        "uncertainty": [],
    }


def resolver_source(source_id: str = "RESOLVER-A") -> dict[str, str]:
    return {
        "resolver_source_id": source_id,
        "authority": f"Authority {source_id}",
        "authority_release": "2026-07-21",
        "snapshot_id": f"SNAPSHOT-{source_id}",
        "snapshot_sha256": f"FROZEN-SNAPSHOT-{source_id}",
        "method": "frozen_authority_record",
        "locator": f"frozen://{source_id}",
    }


def active(
    key: str,
    *,
    relationship_type: str = "self",
    entity_kind: str = "single_compound",
) -> dict[str, str]:
    return {
        "relationship_type": relationship_type,
        "moiety_namespace": "MOIETY-REGISTRY",
        "moiety_identifier": key,
        "moiety_entity_kind": entity_kind,
        "exact_form_scope": "The asserted exact intervention only; no evidence transfer.",
    }


def exact_identity(
    key: str,
    *,
    entity_kind: str = "single_compound",
    active_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    composition_kinds = {"fixed_combination", "standardized_preparation", "mixture"}
    if active_rows is None:
        active_rows = [active(key)]
    structure = None
    composition_status = "not_applicable"
    components: list[dict[str, str]] = []
    product = None
    if entity_kind in composition_kinds:
        composition_status = "exact"
        components = [
            {
                "component_namespace": "COMPONENT-REGISTRY",
                "component_identifier": f"{key}-A",
                "component_entity_kind": "single_compound",
                "role": "active_component",
                "amount_or_fraction": "1 unit",
            },
            {
                "component_namespace": "COMPONENT-REGISTRY",
                "component_identifier": f"{key}-B",
                "component_entity_kind": "single_compound",
                "role": "active_component",
                "amount_or_fraction": "2 units",
            },
        ]
    elif entity_kind == "formulation":
        composition_status = "exact"
        components = [
            {
                "component_namespace": "COMPONENT-REGISTRY",
                "component_identifier": key,
                "component_entity_kind": "single_compound",
                "role": "active_component",
                "amount_or_fraction": "10 mg",
            }
        ]
        product = {
            "product_namespace": "PRODUCT-REGISTRY",
            "product_identifier": f"PRODUCT-{key}",
            "dosage_form": "tablet",
            "release_characteristic": "extended_release",
            "administration_routes": ["oral"],
        }
    else:
        structure = {
            "canonical_smiles": f"[{key}]",
            "standard_inchi": f"InChI=1S/{key}",
            "full_inchikey": f"EXACT-{key}",
            "stereochemistry_status": (
                "fully_specified" if entity_kind == "stereoisomer" else "not_applicable"
            ),
            "stereochemistry_descriptor": (
                key if entity_kind == "stereoisomer" else "not_applicable"
            ),
            "canonicalization_method": "authority_reported",
            "canonicalization_version": "2026-07",
        }
    return {
        "entity_kind": entity_kind,
        "registry_identifiers": [
            {"namespace": "EXACT-REGISTRY", "identifier": f"{entity_kind}:{key}"}
        ],
        "canonical_structure": structure,
        "composition_status": composition_status,
        "components": components,
        "product": product,
        "active_moieties": active_rows,
    }


def assertion(
    seed_id: str,
    identity: dict[str, Any] | None,
    *,
    source_id: str = "RESOLVER-A",
    record_suffix: str = "1",
    reported_identity: str | None = None,
    unresolved_reason: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = identity is not None
    return {
        "seed_id": seed_id,
        "resolver_source_id": source_id,
        "authority_record_id": f"{source_id}:{seed_id}:{record_suffix}",
        "authority_locator": f"frozen://{source_id}/{seed_id}/{record_suffix}",
        "assertion_status": "resolved" if resolved else "unresolved",
        "reported_identity": reported_identity or f"Raw reported {seed_id}",
        "identity": identity,
        "unresolved_reason": None if resolved else (unresolved_reason or "One-to-many identity."),
        "candidate_identities": candidates or [],
    }


def result(
    seed_id: str,
    *,
    status: str = "resolved",
    role: str = "repurposing",
    source_ids: list[str] | None = None,
    reason_code: str = "authority_resolution_complete",
    reason: str = "Frozen authority assertions completed exact-identity resolution.",
) -> dict[str, Any]:
    return {
        "seed_id": seed_id,
        "result_status": status,
        "case_role": role,
        "reason_code": reason_code,
        "reason": reason,
        "resolver_source_ids": source_ids or ["RESOLVER-A"],
    }


def resolver_bundle(
    seed_results: list[dict[str, Any]],
    identity_assertions: list[dict[str, Any]],
    *,
    revision: str = "production-disposition-r1",
    sources: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "resolver_revision": revision,
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "resolver_sources": sources or [resolver_source()],
        "seed_results": seed_results,
        "identity_assertions": identity_assertions,
    }


class ProductionDispositionAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = ready_case()

    def run_adapter(
        self,
        seeds: list[dict[str, Any]],
        resolver: dict[str, Any],
    ) -> tuple[dict[str, Any], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        adapter = V7DispositionAdapter(temporary.name)
        aggregate = dict(adapter.normalize_and_dispose(self.case, seeds, resolver))
        path = adapter.aggregate_path(
            self.case.case_revision_id, aggregate["disposition_plan_id"]
        )
        self.assertTrue(path.is_file())
        validate_disposition_aggregate(self.case, aggregate)
        return aggregate, path

    def test_all_six_dispositions_reconcile_and_preserve_lineage(self) -> None:
        seeds = [seed(self.case, index) for index in range(7)]
        identities = {
            0: exact_identity("DUPLICATE"),
            1: exact_identity("DUPLICATE"),
            2: exact_identity("BASELINE"),
            3: exact_identity("BASELINE"),
            4: exact_identity("REJECTED"),
        }
        assertions = [
            assertion(seeds[index]["seed_id"], identity)
            for index, identity in identities.items()
        ]
        assertions.append(
            assertion(
                seeds[5]["seed_id"],
                None,
                candidates=[exact_identity("AMBIG-A"), exact_identity("AMBIG-B")],
            )
        )
        results = [
            result(seeds[0]["seed_id"]),
            result(seeds[1]["seed_id"]),
            result(seeds[2]["seed_id"], role="baseline"),
            result(seeds[3]["seed_id"], role="baseline"),
            result(
                seeds[4]["seed_id"],
                role="ineligible",
                reason_code="prohibited_intervention_type",
                reason="A frozen case-scope rule excludes this exact intervention type.",
            ),
            result(
                seeds[5]["seed_id"],
                status="unresolved",
                role="unknown",
                reason_code="one_to_many_identity",
                reason="The resolver retained two decision-changing exact identities.",
            ),
            result(
                seeds[6]["seed_id"],
                status="technical_failure",
                role="unknown",
                reason_code="resolver_parse_failure",
                reason="The frozen resolver record could not be parsed.",
            ),
        ]
        aggregate, _ = self.run_adapter(seeds, resolver_bundle(results, assertions))
        counts = aggregate["reconciliation"]
        self.assertEqual(
            {name: counts[name] for name in (
                "N_admit", "N_merge", "N_baseline", "N_reject", "N_quarantine", "N_failed"
            )},
            {
                "N_admit": 1,
                "N_merge": 2,
                "N_baseline": 1,
                "N_reject": 1,
                "N_quarantine": 1,
                "N_failed": 1,
            },
        )
        self.assertTrue(counts["seed_equation_balanced"])
        self.assertEqual(counts["seed_lineage_count"], len(seeds))
        self.assertFalse(aggregate["stage_gate_passed"])
        self.assertEqual(
            aggregate["identity_denominators"]["N_identity_all"], 3
        )
        self.assertEqual(
            aggregate["identity_denominators"]["N_identity_admitted"], 1
        )
        self.assertEqual(
            aggregate["identity_denominators"]["N_identity_baseline"], 1
        )
        dispositions = {
            row["seed_id"]: row for row in aggregate["seed_dispositions"]
        }
        for original in seeds:
            row = dispositions[original["seed_id"]]
            self.assertEqual(row["source_mapping_id"], original["source_mapping_id"])
            self.assertEqual(row["discovery_route_ids"], original["discovery_route_ids"])
        merge_targets = {
            row["representative_seed_id"] for row in aggregate["merge_links"]
        }
        self.assertEqual(merge_targets, {seeds[0]["seed_id"], seeds[2]["seed_id"]})

    def test_missing_duplicate_and_cyclic_dispositions_fail_validation(self) -> None:
        seeds = [seed(self.case, index) for index in range(3)]
        resolver = resolver_bundle(
            [result(row["seed_id"]) for row in seeds],
            [
                assertion(row["seed_id"], exact_identity(f"EXACT-{index}"))
                for index, row in enumerate(seeds)
            ],
        )
        aggregate, _ = self.run_adapter(seeds, resolver)
        missing = copy.deepcopy(aggregate)
        missing["seed_dispositions"].pop()
        with self.assertRaises(DispositionAggregateError):
            validate_disposition_aggregate(self.case, missing)
        duplicate = copy.deepcopy(aggregate)
        duplicate["seed_dispositions"].append(
            copy.deepcopy(duplicate["seed_dispositions"][0])
        )
        with self.assertRaises(DispositionAggregateError):
            validate_disposition_aggregate(self.case, duplicate)
        cyclic = copy.deepcopy(aggregate)
        first, second = cyclic["seed_dispositions"][:2]
        first["canonical_disposition"] = "merge"
        first["representative_seed_id"] = second["seed_id"]
        second["canonical_disposition"] = "merge"
        second["representative_seed_id"] = first["seed_id"]
        with self.assertRaises(DispositionAggregateError):
            validate_disposition_aggregate(self.case, cyclic)

    def test_conflict_defeats_majority_and_names_never_resolve_or_merge(self) -> None:
        seeds = [
            seed(self.case, 0, raw_name="SAME DISPLAY NAME"),
            seed(self.case, 1, raw_name="SAME DISPLAY NAME"),
            seed(self.case, 2, raw_name="UNRELATED RAW NAME A"),
            seed(self.case, 3, raw_name="UNRELATED RAW NAME B"),
        ]
        sources = [resolver_source("RESOLVER-A"), resolver_source("RESOLVER-B")]
        assertions = [
            assertion(seeds[0]["seed_id"], exact_identity("IDENTITY-A")),
            assertion(
                seeds[0]["seed_id"],
                exact_identity("IDENTITY-A"),
                source_id="RESOLVER-B",
                record_suffix="2",
            ),
            assertion(
                seeds[0]["seed_id"],
                exact_identity("IDENTITY-B"),
                source_id="RESOLVER-B",
                record_suffix="3",
            ),
            assertion(seeds[1]["seed_id"], exact_identity("IDENTITY-C")),
            assertion(
                seeds[2]["seed_id"],
                exact_identity("IDENTITY-D"),
                reported_identity="Alias alpha",
            ),
            assertion(
                seeds[3]["seed_id"],
                exact_identity("IDENTITY-D"),
                reported_identity="Alias beta",
            ),
        ]
        results = [
            result(row["seed_id"], source_ids=["RESOLVER-A", "RESOLVER-B"])
            if index == 0
            else result(row["seed_id"])
            for index, row in enumerate(seeds)
        ]
        aggregate, _ = self.run_adapter(
            seeds,
            resolver_bundle(results, assertions, sources=sources),
        )
        dispositions = {
            row["seed_id"]: row["canonical_disposition"]
            for row in aggregate["seed_dispositions"]
        }
        self.assertEqual(dispositions[seeds[0]["seed_id"]], "quarantine")
        self.assertEqual(dispositions[seeds[1]["seed_id"]], "admit")
        self.assertEqual(dispositions[seeds[2]["seed_id"]], "admit")
        self.assertEqual(dispositions[seeds[3]["seed_id"]], "merge")
        conflict = aggregate["conflicting_identity_records"][0]
        self.assertFalse(conflict["majority_vote_used"])
        self.assertEqual(len(conflict["identity_fingerprints"]), 2)
        # Identical display names did not merge seeds 0 and 1; different names did merge 2 and 3.
        self.assertNotEqual(
            aggregate["seed_dispositions"][0].get("normalized_intervention_id"),
            aggregate["seed_dispositions"][1].get("normalized_intervention_id"),
        )
        self.assertTrue(
            all(
                not row["raw_names_used_for_identity"]
                for row in aggregate["normalized_interventions"]
            )
        )

    def test_concordant_exact_structure_unions_distinct_authority_identifiers(self) -> None:
        one = seed(self.case, 0)
        sources = [resolver_source("RESOLVER-A"), resolver_source("RESOLVER-B")]
        first = exact_identity("SHARED-STRUCTURE")
        second = copy.deepcopy(first)
        second["registry_identifiers"] = [
            {"namespace": "SECOND-REGISTRY", "identifier": "SECOND-ID"}
        ]
        aggregate, _ = self.run_adapter(
            [one],
            resolver_bundle(
                [result(one["seed_id"], source_ids=["RESOLVER-A", "RESOLVER-B"])],
                [
                    assertion(one["seed_id"], first),
                    assertion(
                        one["seed_id"],
                        second,
                        source_id="RESOLVER-B",
                        record_suffix="2",
                    ),
                ],
                sources=sources,
            ),
        )
        self.assertEqual(aggregate["reconciliation"]["N_admit"], 1)
        self.assertEqual(aggregate["reconciliation"]["N_quarantine"], 0)
        identifiers = aggregate["normalized_interventions"][0]["registry_identifiers"]
        self.assertEqual(len(identifiers), 2)

    def test_exact_forms_remain_distinct_with_policy_breadth_rollups(self) -> None:
        kinds = [
            ("single_compound", "PARENT", [active("PARENT")]),
            ("salt", "PARENT-SALT", [active("PARENT", relationship_type="salt_of")]),
            (
                "formulation",
                "PARENT-FORM",
                [active("PARENT", relationship_type="formulation_of")],
            ),
            ("stereoisomer", "PARENT-R", [active("PARENT-R")]),
            ("stereoisomer", "PARENT-S", [active("PARENT-S")]),
            ("prodrug", "PARENT-PRO", [active("PARENT", relationship_type="prodrug_of")]),
            (
                "active_metabolite",
                "PARENT-MET",
                [active("PARENT", relationship_type="active_metabolite_of")],
            ),
            (
                "fixed_combination",
                "COMBINATION",
                [
                    active("COMB-A", relationship_type="delivers_active_moiety"),
                    active("COMB-B", relationship_type="delivers_active_moiety"),
                ],
            ),
            (
                "standardized_preparation",
                "PREPARATION",
                [
                    active("PREP-A", relationship_type="delivers_active_moiety"),
                    active("PREP-B", relationship_type="delivers_active_moiety"),
                ],
            ),
        ]
        seeds = [seed(self.case, index) for index in range(len(kinds))]
        assertions = [
            assertion(
                row["seed_id"],
                exact_identity(key, entity_kind=kind, active_rows=active_rows),
            )
            for row, (kind, key, active_rows) in zip(seeds, kinds)
        ]
        aggregate, _ = self.run_adapter(
            seeds,
            resolver_bundle([result(row["seed_id"]) for row in seeds], assertions),
        )
        self.assertTrue(aggregate["stage_gate_passed"])
        self.assertEqual(aggregate["reconciliation"]["N_admit"], len(kinds))
        self.assertEqual(aggregate["reconciliation"]["N_merge"], 0)
        self.assertEqual(len(aggregate["normalized_interventions"]), len(kinds))
        self.assertEqual(
            aggregate["identity_denominators"]["N_breadth_admitted"], 7
        )
        by_kind = {
            row["entity_kind"]: row for row in aggregate["normalized_interventions"]
            if row["entity_kind"] not in {"stereoisomer"}
        }
        self.assertEqual(
            by_kind["single_compound"]["breadth_group_id"],
            by_kind["salt"]["breadth_group_id"],
        )
        self.assertEqual(
            by_kind["single_compound"]["breadth_group_id"],
            by_kind["formulation"]["breadth_group_id"],
        )
        self.assertNotEqual(
            by_kind["single_compound"]["normalized_intervention_id"],
            by_kind["salt"]["normalized_intervention_id"],
        )
        self.assertTrue(
            all(
                row["automatic_evidence_transfer"] is False
                for row in aggregate["active_moiety_relationships"]
            )
        )

    def test_missing_resolver_result_becomes_failed_and_replay_drift_fails(self) -> None:
        seeds = [seed(self.case, 0), seed(self.case, 1)]
        resolver = resolver_bundle(
            [result(seeds[0]["seed_id"])],
            [assertion(seeds[0]["seed_id"], exact_identity("EXACT-0"))],
            revision="replay-r1",
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        adapter = V7DispositionAdapter(temporary.name)
        first = adapter.normalize_and_dispose(self.case, seeds, resolver)
        replay = adapter.normalize_and_dispose(self.case, list(reversed(seeds)), resolver)
        self.assertEqual(first, replay)
        self.assertEqual(first["reconciliation"]["N_failed"], 1)
        self.assertFalse(first["stage_gate_passed"])
        failed = next(
            row
            for row in first["seed_dispositions"]
            if row["seed_id"] == seeds[1]["seed_id"]
        )
        self.assertEqual(failed["reason_code"], "resolver_result_missing")
        changed = copy.deepcopy(resolver)
        changed["seed_results"].append(result(seeds[1]["seed_id"]))
        changed["identity_assertions"].append(
            assertion(seeds[1]["seed_id"], exact_identity("EXACT-1"))
        )
        with self.assertRaises(DispositionAggregateConflictError):
            adapter.normalize_and_dispose(self.case, seeds, changed)

    def test_duplicate_seed_and_duplicate_resolver_result_are_conflicts(self) -> None:
        one = seed(self.case, 0)
        resolver = resolver_bundle(
            [result(one["seed_id"])],
            [assertion(one["seed_id"], exact_identity("ONE"))],
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = V7DispositionAdapter(directory)
            with self.assertRaises(DispositionAggregateConflictError):
                adapter.normalize_and_dispose(self.case, [one, copy.deepcopy(one)], resolver)
            duplicate_result = copy.deepcopy(resolver)
            duplicate_result["seed_results"].append(result(one["seed_id"]))
            with self.assertRaises(DispositionAggregateConflictError):
                adapter.normalize_and_dispose(self.case, [one], duplicate_result)
            conflicting_record = resolver_bundle(
                [result(one["seed_id"])],
                [
                    assertion(one["seed_id"], exact_identity("ONE")),
                    assertion(one["seed_id"], exact_identity("DIFFERENT-CONTENT")),
                ],
                revision="same-authority-record-conflict-r1",
            )
            with self.assertRaises(DispositionAggregateConflictError):
                adapter.normalize_and_dispose(self.case, [one], conflicting_record)

    def test_one_thousand_seed_reconciliation_is_lossless_and_deterministic(self) -> None:
        seeds = [seed(self.case, index) for index in range(1000)]
        results = [result(row["seed_id"]) for row in seeds]
        assertions = [
            assertion(
                row["seed_id"],
                exact_identity(f"GROUP-{index // 10:03d}"),
                reported_identity=f"Source-specific raw form {index:04d}",
            )
            for index, row in enumerate(seeds)
        ]
        aggregate, _ = self.run_adapter(
            list(reversed(seeds)),
            resolver_bundle(results, list(reversed(assertions)), revision="scale-1000-r1"),
        )
        counts = aggregate["reconciliation"]
        self.assertEqual(counts["N_seed"], 1000)
        self.assertEqual(counts["N_admit"], 100)
        self.assertEqual(counts["N_merge"], 900)
        self.assertEqual(counts["N_failed"], 0)
        self.assertTrue(counts["seed_equation_balanced"])
        self.assertTrue(counts["all_seed_lineage_preserved"])
        self.assertTrue(aggregate["stage_gate_passed"])
        self.assertEqual(len(aggregate["seed_dispositions"]), 1000)
        self.assertEqual(len(aggregate["identity_resolutions"]), 1000)
        self.assertEqual(len(aggregate["merge_links"]), 900)
        self.assertEqual(
            aggregate["identity_denominators"],
            {
                "N_identity_all": 100,
                "N_identity_admitted": 100,
                "N_identity_baseline": 0,
                "N_breadth_admitted": 100,
                "N_active_moiety_all": 100,
                "N_active_moiety_admitted": 100,
            },
        )


if __name__ == "__main__":
    unittest.main()
