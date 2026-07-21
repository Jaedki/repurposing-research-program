#!/usr/bin/env python3
"""Active harness checks and schema-v7 production acceptance tests."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from v7_benchmark import (
    candidate_id,
    canonical_reduce,
    canonical_sha256,
    generate_projection,
    legacy_operation_decision,
    load_fixture_manifest,
    load_golden_controls,
    packet_measurements,
    replay_recovery_measurement,
    run_baseline,
    structure_key,
    validate_frozen_assets,
    validate_legacy_fixtures,
    validate_projection,
)
from v7_case_model import V7CompatibilityAdapter, build_case_bundle, initialize_case
from v7_chemical_target_adapters import OpenTargetsEntityKind, make_open_targets_plan
from v7_packets import canonical_bytes
from v7_production_discovery import V7DiscoveryAdapter, validate_discovery_aggregate
from v7_production_disposition import (
    NORMALIZATION_POLICY_VERSION,
    V7DispositionAdapter,
    validate_disposition_aggregate,
)
from v7_production_screen_deep import (
    V7ScreenDeepAdapter,
    validate_screen_deep_aggregate,
)
from v7_production_screen_deep_test import make_production_fixture
from v7_production_portfolio import V7PortfolioAdapter, validate_portfolio_aggregate
from v7_production_portfolio_test import make_portfolio_fixture
from v7_production_program import V7ProgramAdapter
from v7_production_program_test import _audit, _evidence, _resolver, _source_plan
from v7_runtime import V7RuntimeAdapter
from v7_outputs import V7OutputAdapter
from v7_outputs_test import make_complete_snapshot


def issue_codes(manifest: dict[str, Any], projection: dict[str, Any], golden: dict[str, Any]) -> set[str]:
    return {issue.code for issue in validate_projection(manifest, projection, golden)}


class BenchmarkFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_fixture_manifest()
        cls.golden = load_golden_controls()
        cls.projection = generate_projection(cls.manifest)

    def test_frozen_assets_and_generated_projection_are_stable(self) -> None:
        self.assertEqual(validate_frozen_assets(), [])
        self.assertEqual(
            canonical_sha256(self.projection),
            self.manifest["expected_generated_projection_sha256"],
        )

    def test_source_universe_has_at_least_one_thousand_mappings(self) -> None:
        self.assertGreaterEqual(
            max(int(row["mapping_count"]) for row in self.manifest["source_universes"]),
            1000,
        )
        self.assertEqual(len(self.projection["mappings"]), 1024)

    def test_baseline_projection_passes_every_active_validator(self) -> None:
        self.assertEqual(validate_projection(self.manifest, self.projection, self.golden), [])

    def test_dropped_mapping_is_detected(self) -> None:
        projection = copy.deepcopy(self.projection)
        missing = projection["seeds"].pop(200)
        projection["seed_dispositions"] = [
            row for row in projection["seed_dispositions"] if row["seed_id"] != missing["seed_id"]
        ]
        self.assertIn("DROPPED_OR_SILENT_MAPPING", issue_codes(self.manifest, projection, self.golden))

    def test_missing_or_duplicate_seed_disposition_is_detected(self) -> None:
        projection = copy.deepcopy(self.projection)
        projection["seed_dispositions"].pop(300)
        self.assertIn("SEED_DISPOSITION_INCOMPLETE", issue_codes(self.manifest, projection, self.golden))
        projection = copy.deepcopy(self.projection)
        projection["seed_dispositions"].append(copy.deepcopy(projection["seed_dispositions"][300]))
        self.assertIn("SEED_DISPOSITION_INCOMPLETE", issue_codes(self.manifest, projection, self.golden))

    def test_pagination_totals_and_cursor_chain_are_validated(self) -> None:
        projection = copy.deepcopy(self.projection)
        receipts = projection["inventory"]["retrieval_content_receipts"]
        receipts[0]["provider_total"] += 1
        self.assertIn("PAGINATION_PROVIDER_TOTAL_MISMATCH", issue_codes(self.manifest, projection, self.golden))
        projection = copy.deepcopy(self.projection)
        receipts = projection["inventory"]["retrieval_content_receipts"]
        receipts[1]["input_cursor"] = "CURSOR-DISCONNECTED"
        self.assertIn("PAGINATION_CURSOR_DISCONNECTED", issue_codes(self.manifest, projection, self.golden))

    def test_empty_frontier_with_unprocessed_inventory_is_rejected(self) -> None:
        projection = copy.deepcopy(self.projection)
        projection["inventory"]["branches"][0]["state"] = "unprocessed"
        self.assertEqual(projection["inventory"]["frontier"], [])
        self.assertIn(
            "EMPTY_FRONTIER_WITH_UNPROCESSED_BRANCHES",
            issue_codes(self.manifest, projection, self.golden),
        )

    def test_reused_source_cannot_satisfy_unrelated_coverage_families(self) -> None:
        projection = copy.deepcopy(self.projection)
        claims = projection["inventory"]["coverage_claims"]
        claims[1]["source_universe_id"] = claims[0]["source_universe_id"]
        claims[1]["receipt_ids"] = list(claims[0]["receipt_ids"])
        codes = issue_codes(self.manifest, projection, self.golden)
        self.assertIn("REUSED_SOURCE_FALSE_COVERAGE", codes)
        self.assertIn("REUSED_RECEIPT_FALSE_COVERAGE", codes)

    def test_alias_salt_and_formulation_deduplication_is_enforced(self) -> None:
        projection = copy.deepcopy(self.projection)
        alias = next(row for row in projection["seed_dispositions"] if row["mapping_index"] == 1)
        alias["disposition"] = "admit"
        alias["representative_seed_id"] = None
        self.assertIn("CHEMICAL_DEDUPLICATION_FAILED", issue_codes(self.manifest, projection, self.golden))

    def test_syntax_valid_but_unresolved_identity_cannot_enter_deep_tier(self) -> None:
        projection = copy.deepcopy(self.projection)
        package = copy.deepcopy(projection["deep_packages"][0])
        package["candidate_id"] = candidate_id(10)
        package["origin_seed_ids"] = ["SEED-000010"]
        package["identity_resolution"] = {
            "status": "unresolved",
            "normalized_intervention_id": "NI-000010",
            "structure_identity_key": structure_key(10),
            "resolver_authority": "synthetic-resolver-a",
            "resolver_release": "2026-07-19",
            "decision_relevant_conflicts": [structure_key(10, variant=1)],
        }
        projection["deep_packages"].append(package)
        self.assertIn("UNRESOLVED_IDENTITY_AT_DEPTH", issue_codes(self.manifest, projection, self.golden))

    def test_database_only_and_sparse_literature_seeds_are_preserved(self) -> None:
        projection = copy.deepcopy(self.projection)
        database_id = candidate_id(self.manifest["special_indices"]["database_only_index"])
        sparse_id = candidate_id(self.manifest["special_indices"]["sparse_literature_index"])
        screened = {row["candidate_id"] for row in projection["screen_records"]}
        self.assertIn(database_id, screened)
        self.assertIn(sparse_id, screened)
        projection["screen_records"] = [row for row in projection["screen_records"] if row["candidate_id"] != database_id]
        self.assertIn("DATABASE_ONLY_SEED_LOST", issue_codes(self.manifest, projection, self.golden))

    def test_popularity_bias_control_preserves_stronger_obscure_candidate(self) -> None:
        projection = copy.deepcopy(self.projection)
        strong_id = candidate_id(self.manifest["special_indices"]["strong_obscure_index"])
        weak_id = candidate_id(self.manifest["special_indices"]["weak_popular_index"])
        ranks = {row["candidate_id"]: row["rank"] for row in projection["portfolio"]}
        self.assertLess(ranks[strong_id], ranks[weak_id])
        for row in projection["portfolio"]:
            if row["candidate_id"] == strong_id:
                row["rank"] = 7
            elif row["candidate_id"] == weak_id:
                row["rank"] = 1
        self.assertIn("POPULARITY_BIAS", issue_codes(self.manifest, projection, self.golden))

    def test_multi_endpoint_omission_is_detected(self) -> None:
        projection = copy.deepcopy(self.projection)
        multi_id = candidate_id(self.manifest["special_indices"]["multi_endpoint_index"])
        screen = next(row for row in projection["screen_records"] if row["candidate_id"] == multi_id)
        screen["endpoint_assessments"].pop()
        self.assertIn("MULTI_ENDPOINT_INCOMPLETE", issue_codes(self.manifest, projection, self.golden))

    def test_safety_and_exposure_must_be_structured(self) -> None:
        projection = copy.deepcopy(self.projection)
        package = projection["deep_packages"][0]
        package["safety"] = "safe in prose"
        package["exposure"].pop("population")
        codes = issue_codes(self.manifest, projection, self.golden)
        self.assertIn("SAFETY_NOT_STRUCTURED", codes)
        self.assertIn("EXPOSURE_NOT_STRUCTURED", codes)

    def test_full_funnel_mismatch_is_detected(self) -> None:
        projection = copy.deepcopy(self.projection)
        projection["full_funnel"]["seeds"] -= 1
        self.assertIn("FULL_FUNNEL_RECONCILIATION_FAILED", issue_codes(self.manifest, projection, self.golden))

    def test_golden_controls_are_offline_development_assets(self) -> None:
        self.assertFalse(self.golden["network_required"])
        self.assertEqual(self.golden["partition"], "development")
        self.assertFalse(self.golden["certification_eligible"])
        self.assertIn("not universal scientific negatives", self.golden["expectation_scope"])


class RuntimeAndScaleOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_fixture_manifest()
        cls.projection = generate_projection(cls.manifest)

    def test_deterministic_replay_and_interrupted_commit_recovery(self) -> None:
        measurement = replay_recovery_measurement(self.projection)
        self.assertTrue(measurement["hashes_identical"])
        self.assertTrue(measurement["interrupted_commit_recovered"])
        self.assertEqual(measurement["canonical_record_count"], 1024)

    def test_idempotency_conflict_is_not_last_writer_wins(self) -> None:
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            canonical_reduce(
                [
                    {"record_id": "R1", "value": 1},
                    {"record_id": "R1", "value": 2},
                ],
                "record_id",
            )

    def test_packet_limits_cover_500_and_1000_candidates(self) -> None:
        metrics = packet_measurements(self.manifest)
        self.assertEqual(set(metrics["sizes"]), {"500", "1000"})
        for requested, result in metrics["sizes"].items():
            self.assertEqual(result["candidate_coverage"], int(requested))
            self.assertEqual(result["duplicate_candidate_ids"], 0)
            self.assertLessEqual(result["max_candidates_in_shard"], metrics["configured_max_candidates_per_shard"])
            self.assertLessEqual(result["max_packet_bytes"], metrics["configured_max_packet_bytes"])


class LegacyReadOnlyOracleTests(unittest.TestCase):
    def test_schema_v3_through_v6_fixtures_remain_read_only(self) -> None:
        self.assertEqual(validate_legacy_fixtures(), [])
        for version in (3, 4, 5, 6):
            self.assertTrue(legacy_operation_decision(version, "inspect")["allowed"])
            for operation in ("resume", "write", "append", "finalize"):
                self.assertFalse(legacy_operation_decision(version, operation)["allowed"])


class BaselineMetricTests(unittest.TestCase):
    def test_all_required_metrics_are_reported(self) -> None:
        report = run_baseline()
        self.assertEqual(report["status"], "pass", report["issues"])
        metrics = report["metrics"]
        expected = {
            "candidate_universe_recall",
            "seed_disposition_completeness",
            "recall_at_k",
            "precision_at_k",
            "long_tail_recall",
            "source_diversity",
            "evidence_modality_diversity",
            "duplicate_rate",
            "unresolved_identity_rate",
            "audit_sampling_coverage",
            "runtime",
            "packet_size",
        }
        self.assertEqual(set(metrics), expected)
        self.assertEqual(metrics["candidate_universe_recall"]["value"], 1.0)
        self.assertEqual(metrics["seed_disposition_completeness"]["value"], 1.0)
        self.assertEqual(metrics["long_tail_recall"]["value"], 1.0)
        self.assertEqual(metrics["audit_sampling_coverage"]["value"], 1.0)
        self.assertEqual(metrics["runtime"]["network_calls"], 0)


class ProductionProtocolAcceptanceTests(unittest.TestCase):
    def test_production_program_executes_all_eight_stages(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "benchmarks"
                / "schema_v7"
                / "chemical_target_adapters"
                / "frozen_responses.json"
            ).read_text(encoding="utf-8")
        )
        case = build_case_bundle(fixture["case_input"]).case_revision
        source_plan, pages = _source_plan(case)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "program"
            initialize_case(root, fixture["case_input"])
            result = V7ProgramAdapter(root).execute(
                case, source_plan, pages, _resolver, _evidence, _audit
            )
            self.assertTrue((root / "outputs_v7" / "artifact_manifest.json").is_file())
        self.assertEqual(result["stage_status"], {str(index): "pass" for index in range(1, 9)})
        self.assertEqual(result["runtime_status"], "complete")
        self.assertTrue(result["output_manifest_id"].startswith("V7OUTPUT-"))

    def test_production_discovery_against_frozen_transport(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "schema_v7"
            / "chemical_target_adapters"
            / "frozen_responses.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        case = build_case_bundle(fixture["case_input"]).case_revision
        plan = make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-21T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(case.endpoints[0].endpoint_id,),
            disease_state_id="MONDO_0004979",
        )
        source_plan = {
            "source_plan_revision": "benchmark-production-discovery-r1",
            "branches": [
                {
                    "branch_id": "open-targets-target",
                    "adapter_id": "open-targets-graphql-v4",
                    "query_plan": plan,
                }
            ],
        }
        pages = {
            "open-targets-target": [fixture["responses"]["open_targets_target"]]
        }
        with tempfile.TemporaryDirectory() as directory:
            adapter = V7DiscoveryAdapter(directory)
            result = adapter.retrieve_and_seed(
                {
                    "original_input": fixture["case_input"],
                    "case_revision_id": case.case_revision_id,
                },
                source_plan,
                pages,
            )
            replay = adapter.retrieve_and_seed(case, source_plan, pages)
            self.assertEqual(result, replay)
            self.assertTrue(
                adapter.aggregate_path(case.case_revision_id, result["source_plan_id"]).is_file()
            )
        validate_discovery_aggregate(case, result)
        self.assertEqual(
            {
                "source_universes",
                "branches",
                "retrieval_content_receipts",
                "mapping_outcomes",
                "seeds",
            }
            - set(result),
            set(),
        )
        self.assertEqual(result["reconciliation"]["returned_native_item_count"], 3)
        self.assertEqual(result["reconciliation"]["mapping_outcome_occurrence_count"], 3)
        self.assertEqual(result["reconciliation"]["unique_seed_count"], 2)
        self.assertEqual(result["reconciliation"]["unreconciled_native_item_count"], 0)
        self.assertTrue(result["closure"]["all_declared_branches_complete"])
        self.assertFalse(result["closure"]["global_coverage_claimed"])

    def test_production_disposition_and_identity_against_fixture(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "schema_v7"
            / "chemical_target_adapters"
            / "frozen_responses.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        case = build_case_bundle(fixture["case_input"]).case_revision
        plan = make_open_targets_plan(
            source_release="26.06",
            source_snapshot_at="2026-07-21T00:00:00Z",
            entity_kind=OpenTargetsEntityKind.TARGET,
            entity_id="ENSG00000141510",
            endpoint_ids=(case.endpoints[0].endpoint_id,),
            disease_state_id="MONDO_0004979",
        )
        source_plan = {
            "source_plan_revision": "benchmark-production-disposition-discovery-r1",
            "branches": [
                {
                    "branch_id": "open-targets-target",
                    "adapter_id": "open-targets-graphql-v4",
                    "query_plan": plan,
                }
            ],
        }
        pages = {
            "open-targets-target": [fixture["responses"]["open_targets_target"]]
        }
        with tempfile.TemporaryDirectory() as directory:
            discovery = V7DiscoveryAdapter(Path(directory) / "discovery")
            discovery_result = discovery.retrieve_and_seed(
                case, source_plan, pages
            )
            seeds = discovery_result["seeds"]
            resolver_source = {
                "resolver_source_id": "FROZEN-AUTHORITY",
                "authority": "Frozen exact-identity authority",
                "authority_release": "2026-07-21",
                "snapshot_id": "FROZEN-AUTHORITY-SNAPSHOT-1",
                "snapshot_sha256": "FROZEN-AUTHORITY-CONTENT-HASH",
                "method": "frozen_authority_record",
                "locator": "frozen://production-disposition-acceptance",
            }
            seed_results = []
            identity_assertions = []
            for index, seed_row in enumerate(sorted(seeds, key=lambda row: row["seed_id"])):
                seed_id = seed_row["seed_id"]
                exact_key = f"EXACT-PRODUCTION-{index}"
                seed_results.append(
                    {
                        "seed_id": seed_id,
                        "result_status": "resolved",
                        "case_role": "repurposing",
                        "reason_code": "authority_resolution_complete",
                        "reason": "The frozen authority record resolves one exact intervention.",
                        "resolver_source_ids": ["FROZEN-AUTHORITY"],
                    }
                )
                identity_assertions.append(
                    {
                        "seed_id": seed_id,
                        "resolver_source_id": "FROZEN-AUTHORITY",
                        "authority_record_id": f"AUTHORITY:{exact_key}",
                        "authority_locator": f"frozen://authority/{exact_key}",
                        "assertion_status": "resolved",
                        "reported_identity": seed_row["compound_hint"]["value"],
                        "identity": {
                            "entity_kind": "single_compound",
                            "registry_identifiers": [
                                {
                                    "namespace": "FROZEN-EXACT-ID",
                                    "identifier": exact_key,
                                }
                            ],
                            "canonical_structure": {
                                "canonical_smiles": f"[{exact_key}]",
                                "standard_inchi": f"InChI=1S/{exact_key}",
                                "full_inchikey": f"AUTHORITY-{exact_key}",
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
                                    "relationship_type": "self",
                                    "moiety_namespace": "FROZEN-EXACT-ID",
                                    "moiety_identifier": exact_key,
                                    "moiety_entity_kind": "single_compound",
                                    "exact_form_scope": "The asserted exact intervention only.",
                                }
                            ],
                        },
                        "unresolved_reason": None,
                        "candidate_identities": [],
                    }
                )
            frozen_resolver_assertions = {
                "resolver_revision": "benchmark-production-disposition-r1",
                "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
                "resolver_sources": [resolver_source],
                "seed_results": seed_results,
                "identity_assertions": identity_assertions,
            }
            adapter = V7DispositionAdapter(Path(directory) / "disposition")
            result = adapter.normalize_and_dispose(
                case, seeds, frozen_resolver_assertions
            )
            replay = adapter.normalize_and_dispose(
                case, list(reversed(seeds)), frozen_resolver_assertions
            )
            self.assertEqual(result, replay)
            self.assertTrue(
                adapter.aggregate_path(
                    case.case_revision_id, result["disposition_plan_id"]
                ).is_file()
            )
        validate_discovery_aggregate(case, discovery_result)
        validate_disposition_aggregate(case, result)
        self.assertEqual(
            {
                "normalized_interventions",
                "seed_dispositions",
                "identity_denominators",
            }
            - set(result),
            set(),
        )
        self.assertEqual(result["reconciliation"]["N_seed"], 2)
        self.assertEqual(result["reconciliation"]["N_admit"], 2)
        self.assertEqual(result["reconciliation"]["N_failed"], 0)
        self.assertTrue(result["reconciliation"]["seed_equation_balanced"])
        self.assertTrue(result["stage_gate_passed"])
        self.assertEqual(result["identity_denominators"]["N_identity_all"], 2)
        self.assertEqual(result["identity_denominators"]["N_identity_admitted"], 2)

    def test_production_screen_and_deep_packages_against_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, admitted_frame, frozen_evidence = make_production_fixture(root)
            adapter = V7ScreenDeepAdapter(root / "screen-deep")
            result = adapter.screen_and_deepen(
                case, admitted_frame, frozen_evidence
            )
            replay = adapter.screen_and_deepen(
                case,
                admitted_frame,
                {
                    **frozen_evidence,
                    "candidate_screens": list(
                        reversed(frozen_evidence["candidate_screens"])
                    ),
                    "deep_results": list(reversed(frozen_evidence["deep_results"])),
                },
            )
            self.assertEqual(result, replay)
            self.assertTrue(
                adapter.aggregate_path(
                    case.case_revision_id, result["screen_deep_plan_id"]
                ).is_file()
            )
            self.assertTrue(
                adapter.selection_path(
                    case.case_revision_id, result["screen_deep_plan_id"]
                ).is_file()
            )
        validate_screen_deep_aggregate(case, result)
        self.assertEqual(
            {
                "screen_records",
                "deep_selection",
                "deep_packages",
                "structured_safety",
                "structured_exposure",
            }
            - set(result),
            set(),
        )
        self.assertEqual(result["reconciliation"]["N_admit"], 5)
        self.assertEqual(result["reconciliation"]["N_screened"], 3)
        self.assertEqual(result["reconciliation"]["N_selected_deep"], 2)
        self.assertEqual(result["reconciliation"]["N_screen_only"], 1)
        self.assertEqual(result["reconciliation"]["N_deep"], 2)
        self.assertTrue(result["reconciliation"]["screen_equation_balanced"])
        self.assertTrue(result["reconciliation"]["selection_equation_balanced"])
        self.assertTrue(result["reconciliation"]["deep_equation_balanced"])
        self.assertTrue(result["stage_gate_passed"])

    def test_production_audit_and_portfolio_against_frozen_stage6(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, deep_frame, frozen_audit_plan = make_portfolio_fixture(
            root, revision="portfolio-protocol-production-r1"
            )
            adapter = V7PortfolioAdapter(root / "portfolio")
            result = adapter.audit_and_select(case, deep_frame, frozen_audit_plan)
            replay_input = copy.deepcopy(frozen_audit_plan)
            replay_input["audit_outcomes"].reverse()
            replay = adapter.audit_and_select(case, deep_frame, replay_input)
            self.assertEqual(result, replay)
            self.assertTrue(
                adapter.freeze_path(
                    case.case_revision_id, result["portfolio_plan_id"]
                ).is_file()
            )
            self.assertTrue(
                adapter.aggregate_path(
                    case.case_revision_id, result["portfolio_plan_id"]
                ).is_file()
            )
        validate_portfolio_aggregate(case, result)
        self.assertEqual(
            {
                "audit_report",
                "seven_decision_outputs",
                "portfolio_dispositions",
                "canonical_order",
            }
            - set(result),
            set(),
        )
        self.assertTrue(result["stage_gate_passed"])
        self.assertTrue(result["audit_report"]["coverage_reconciled"])
        self.assertEqual(result["reconciliation"]["N_deep"], 2)
        self.assertEqual(result["reconciliation"]["N_finalist"], 1)
        self.assertEqual(result["reconciliation"]["N_reserve"], 1)
        self.assertTrue(result["reconciliation"]["portfolio_equation_balanced"])
        self.assertTrue(result["reconciliation"]["three_rankings_cover_every_deep_candidate"])
        self.assertTrue(
            all(
                not row["novelty_or_diversity_modified_therapeutic_support"]
                for row in result["seven_decision_outputs"]
            )
        )

    def test_production_replay_and_interrupted_commit_recovery(self) -> None:
        adapter = V7RuntimeAdapter()
        packets = adapter.build_task_packets(
            "candidate_audit",
            [f"CANDIDATE-{index:04d}" for index in range(40)],
            10,
            4096,
        )
        forward = adapter.execute_packets(
            packets,
            {"after_stage": [packets[1]["shard_key"]]},
            {"order": [packet["shard_key"] for packet in packets]},
        )
        reverse = adapter.execute_packets(
            packets,
            {"after_stage": []},
            {"order": [packet["shard_key"] for packet in reversed(packets)]},
        )
        self.assertEqual(forward["scientific_hash"], reverse["scientific_hash"])
        self.assertNotEqual(forward["execution_hash"], reverse["execution_hash"])
        self.assertEqual(forward["canonical_record_count"], 40)
        self.assertTrue(forward["recovered_commits"])

    def test_production_packet_limits_for_500_and_1000_candidates(self) -> None:
        adapter = V7RuntimeAdapter()
        for count in (500, 1000):
            packets = adapter.build_task_packets(
                "candidate_audit",
                [f"CANDIDATE-{index:05d}" for index in range(count)],
                125,
                16384,
            )
            flattened = [value for packet in packets for value in packet["candidate_ids"]]
            self.assertEqual(len(flattened), count)
            self.assertEqual(len(set(flattened)), count)
            self.assertTrue(all(len(packet["candidate_ids"]) <= 125 for packet in packets))
            self.assertTrue(all(len(canonical_bytes(packet)) <= 16384 for packet in packets))

    def test_production_legacy_v3_through_v6_read_only_handling(self) -> None:
        adapter = V7CompatibilityAdapter()
        legacy_root = Path(__file__).resolve().parents[1] / "benchmarks" / "schema_v7" / "legacy"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in (3, 4, 5, 6):
                path = legacy_root / f"schema-v{version}.json"
                before = path.read_bytes()
                inspection = adapter.inspect_legacy(path)
                self.assertEqual(inspection["schema_version"], version)
                self.assertEqual(inspection["mode"], "read_only")
                for operation in ("resume", "write", "append", "finalize"):
                    decision = adapter.request_legacy_operation(path, operation)
                    self.assertFalse(decision["allowed"], (version, operation, decision))
                    self.assertEqual(decision["mode"], "read_only")
                migrated = adapter.copy_migrate(path, root / f"schema-v{version}-derived")
                self.assertEqual(migrated["source_schema_version"], version)
                self.assertFalse(migrated["native_v7"])
                copied = root / f"schema-v{version}-derived" / "legacy_original" / path.name
                self.assertEqual(copied.read_bytes(), before)
                self.assertEqual(path.read_bytes(), before)

    def test_production_full_funnel_output_reconciliation(self) -> None:
        result = V7OutputAdapter().build_full_funnel(make_complete_snapshot())
        reconciliation = result["reconciliation"]
        self.assertTrue(all(value is True for key, value in reconciliation.items() if key.endswith("_balanced")))
        self.assertEqual(reconciliation["seed_count"], 5)
        self.assertEqual(reconciliation["deep_count"], 1)
        self.assertEqual(reconciliation["finalist_count"], 1)
        self.assertIn("machine_readable_provenance.jsonl", result["artifact_payloads"])
        self.assertEqual(
            result["post_run_benchmark_join_key"]["snapshot_sha256"],
            result["output_manifest"]["snapshot_sha256"],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", action="store_true")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    baseline_ok = True
    if args.baseline_report:
        report = run_baseline()
        print("V7 BENCHMARK BASELINE REPORT")
        print(json.dumps(report, indent=2, sort_keys=True))
        baseline_ok = report["status"] == "pass"
    return 0 if result.wasSuccessful() and baseline_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
