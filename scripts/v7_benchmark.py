#!/usr/bin/env python3
"""Offline schema-v7 benchmark fixtures, validators, metrics, and test oracles.

This module is benchmark infrastructure only.  It deliberately does not import or
implement the schema-v7 production runtime, ranking, schemas, or output builders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPOSITORY_ROOT / "benchmarks" / "schema_v7"
FIXTURE_MANIFEST_PATH = BENCHMARK_ROOT / "fixture_manifest.json"
GOLDEN_CONTROLS_PATH = BENCHMARK_ROOT / "golden_controls.json"
FIXTURE_CHECKSUMS_PATH = BENCHMARK_ROOT / "fixture_checksums.json"
LEGACY_ROOT = BENCHMARK_ROOT / "legacy"

EVIDENCE_MODALITIES = (
    "human_interventional",
    "observational",
    "genetic",
    "in_vivo",
    "cellular",
    "computational",
)
SAFETY_FIELDS = {
    "exact_intervention_id",
    "dose",
    "route",
    "duration",
    "population",
    "tissue",
    "interactions",
    "contraindications",
    "severity",
    "reversibility",
    "source_ids",
    "uncertainty",
}
EXPOSURE_FIELDS = {
    "exact_intervention_id",
    "dose",
    "route",
    "duration",
    "population",
    "tissue",
    "achieved_exposure",
    "decision",
    "source_ids",
    "uncertainty",
}
DECISION_OUTPUTS = {
    "therapeutic_support",
    "evidence_quality",
    "readiness",
    "novelty",
    "uncertainty",
    "information_value",
    "portfolio_diversity",
}


EXPECTED_PRODUCTION_INTERFACES: tuple[dict[str, str], ...] = (
    {
        "test_id": "V7-PROD-DISCOVERY",
        "call": "adapter.retrieve_and_seed(case_revision, source_plan, frozen_pages)",
        "required_result": "source_universes, branches, retrieval_content_receipts, mapping_outcomes, seeds",
        "reason": "IMPLEMENTED: v7_production_discovery.V7DiscoveryAdapter provides the persisted whole-case aggregate without benchmark-oracle imports.",
    },
    {
        "test_id": "V7-PROD-DISPOSITION",
        "call": "adapter.normalize_and_dispose(case_revision, seeds, frozen_resolver_assertions)",
        "required_result": "normalized_interventions, seed_dispositions, identity_denominators",
        "reason": "IMPLEMENTED: v7_production_disposition.V7DispositionAdapter provides the persisted all-seed aggregate without benchmark-oracle imports.",
    },
    {
        "test_id": "V7-PROD-SCREEN-DEEP",
        "call": "adapter.screen_and_deepen(case_revision, admitted_frame, frozen_evidence)",
        "required_result": "screen_records, deep_selection, deep_packages, structured safety/exposure",
        "reason": "PENDING: no all-admitted schema-v7 screen/deep aggregate implements screen_and_deepen.",
    },
    {
        "test_id": "V7-PROD-PORTFOLIO",
        "call": "adapter.audit_and_select(case_revision, deep_frame, frozen_audit_plan)",
        "required_result": "audit_report, seven decision outputs, portfolio_dispositions, canonical order",
        "reason": "PENDING: no retrieval-backed persisted schema-v7 audit/portfolio aggregate implements audit_and_select.",
    },
    {
        "test_id": "V7-PROD-RUNTIME",
        "call": "adapter.execute_packets(task_packets, interruption_schedule, replay_schedule)",
        "required_result": "staged attempts, atomic commits, recovered commits, scientific hash, execution hash",
        "reason": "IMPLEMENTED: v7_runtime.V7RuntimeAdapter and the persisted runtime provide staged atomic execution.",
    },
    {
        "test_id": "V7-PROD-PACKETS",
        "call": "adapter.build_task_packets(task_name, candidate_ids, max_candidates, max_bytes)",
        "required_result": "minimal allowlisted packets with deterministic shard and dependency hashes",
        "reason": "IMPLEMENTED: v7_runtime.V7RuntimeAdapter builds bounded role-specific task packets.",
    },
    {
        "test_id": "V7-PROD-LEGACY",
        "call": "adapter.inspect_legacy(path); adapter.request_legacy_operation(path, operation)",
        "required_result": "read-only inspection plus refusal of resume/write/append/finalize for schema-v3..v6",
        "reason": "IMPLEMENTED: v7_case_model.V7CompatibilityAdapter provides real read-only inspection/refusal.",
    },
    {
        "test_id": "V7-PROD-OUTPUTS",
        "call": "adapter.build_full_funnel(committed_snapshot)",
        "required_result": "reconciled machine-readable full funnel and separate post-run benchmark join key",
        "reason": "IMPLEMENTED: v7_outputs.V7OutputAdapter builds reconciled full-funnel projections without benchmark-oracle imports.",
    },
)


@runtime_checkable
class V7ProductionAdapter(Protocol):
    """Exact test-facing protocol expected from a later production implementation."""

    def retrieve_and_seed(
        self,
        case_revision: Mapping[str, Any],
        source_plan: Mapping[str, Any],
        frozen_pages: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def normalize_and_dispose(
        self,
        case_revision: Mapping[str, Any],
        seeds: Iterable[Mapping[str, Any]],
        frozen_resolver_assertions: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def screen_and_deepen(
        self,
        case_revision: Mapping[str, Any],
        admitted_frame: Mapping[str, Any],
        frozen_evidence: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def audit_and_select(
        self,
        case_revision: Mapping[str, Any],
        deep_frame: Mapping[str, Any],
        frozen_audit_plan: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def execute_packets(
        self,
        task_packets: Iterable[Mapping[str, Any]],
        interruption_schedule: Mapping[str, Any],
        replay_schedule: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def build_task_packets(
        self,
        task_name: str,
        candidate_ids: Iterable[str],
        max_candidates: int,
        max_bytes: int,
    ) -> Iterable[Mapping[str, Any]]: ...

    def inspect_legacy(self, path: Path) -> Mapping[str, Any]: ...

    def request_legacy_operation(self, path: Path, operation: str) -> Mapping[str, Any]: ...

    def build_full_funnel(self, committed_snapshot: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BenchmarkIssue:
    code: str
    message: str


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_fixture_manifest() -> dict[str, Any]:
    return load_json(FIXTURE_MANIFEST_PATH)


def load_golden_controls() -> dict[str, Any]:
    return load_json(GOLDEN_CONTROLS_PATH)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def candidate_id(index: int) -> str:
    return f"CASE-SYNTHETIC-R1::NI-{index:06d}::repurposing"


def _letters(value: int, width: int) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    output = ["A"] * width
    current = value
    for position in range(width - 1, -1, -1):
        output[position] = alphabet[current % len(alphabet)]
        current //= len(alphabet)
    return "".join(output)


def structure_key(index: int, variant: int = 0) -> str:
    value = index + (variant * 100_000)
    return f"INCHIKEY:{_letters(value, 14)}-{_letters(value * 17 + 11, 10)}-N"


def _universe_ranges(manifest: Mapping[str, Any]) -> list[tuple[int, int, Mapping[str, Any]]]:
    ranges: list[tuple[int, int, Mapping[str, Any]]] = []
    start = 0
    for universe in manifest["source_universes"]:
        end = start + int(universe["mapping_count"])
        ranges.append((start, end, universe))
        start = end
    return ranges


def _relation_map(manifest: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["mapping_index"]): dict(row)
        for row in manifest["special_indices"]["merge_relations"]
    }


def _source_page_receipts(
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[int, str], list[dict[str, Any]], list[dict[str, Any]]]:
    receipts: list[dict[str, Any]] = []
    receipt_by_index: dict[int, str] = {}
    branches: list[dict[str, Any]] = []
    coverage_claims: list[dict[str, Any]] = []
    for start, end, universe in _universe_ranges(manifest):
        page_size = int(universe["page_size"])
        receipt_ids: list[str] = []
        page_count = math.ceil((end - start) / page_size)
        for page_index in range(page_count):
            page_start = start + page_index * page_size
            page_end = min(end, page_start + page_size)
            receipt_id = f"RCP-{universe['branch_id']}-{page_index + 1:03d}"
            input_cursor = None if page_index == 0 else f"CURSOR-{universe['branch_id']}-{page_index:03d}"
            output_cursor = (
                None
                if page_index == page_count - 1
                else f"CURSOR-{universe['branch_id']}-{page_index + 1:03d}"
            )
            mapping_ids = [f"MAP-{index:06d}" for index in range(page_start, page_end)]
            receipts.append(
                {
                    "receipt_id": receipt_id,
                    "source_universe_id": universe["source_universe_id"],
                    "branch_id": universe["branch_id"],
                    "coverage_family": universe["coverage_family"],
                    "release": universe["release"],
                    "page_ordinal": page_index + 1,
                    "input_cursor": input_cursor,
                    "output_cursor": output_cursor,
                    "returned_count": len(mapping_ids),
                    "unique_count": len(mapping_ids),
                    "provider_total": int(universe["mapping_count"]),
                    "mapping_ids": mapping_ids,
                    "response_hash": canonical_sha256(mapping_ids),
                    "cap": None,
                    "truncated": False,
                    "terminal_code": "end_of_results" if output_cursor is None else "page_complete",
                }
            )
            receipt_ids.append(receipt_id)
            for index in range(page_start, page_end):
                receipt_by_index[index] = receipt_id
        branches.append(
            {
                "branch_id": universe["branch_id"],
                "source_universe_id": universe["source_universe_id"],
                "required": bool(universe["required"]),
                "state": "complete",
                "remaining_items": 0,
                "receipt_ids": receipt_ids,
            }
        )
        coverage_claims.append(
            {
                "coverage_family": universe["coverage_family"],
                "source_universe_id": universe["source_universe_id"],
                "branch_id": universe["branch_id"],
                "receipt_ids": receipt_ids,
            }
        )
    return receipts, receipt_by_index, branches, coverage_claims


def _mapping_name(index: int, relations: Mapping[int, Mapping[str, Any]]) -> str:
    relation = relations.get(index)
    if not relation:
        return f"SyntheticCompound-{index:06d}"
    representative = int(relation["representative_index"])
    suffix = {"alias": "Alias", "salt": "Hydrochloride", "formulation": "ExtendedRelease"}[
        str(relation["relation"])
    ]
    return f"SyntheticCompound-{representative:06d}-{suffix}"


def generate_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Generate the frozen synthetic projection from the compact manifest."""

    mapping_count = int(manifest["generator"]["mapping_count"])
    relations = _relation_map(manifest)
    special = manifest["special_indices"]
    baseline_index = int(special["baseline_index"])
    reject_index = int(special["reject_index"])
    unresolved_index = int(special["unresolved_identity_index"])
    database_only_index = int(special["database_only_index"])
    sparse_index = int(special["sparse_literature_index"])
    weak_popular_index = int(special["weak_popular_index"])
    strong_obscure_index = int(special["strong_obscure_index"])
    multi_endpoint_index = int(special["multi_endpoint_index"])
    safety_index = int(special["structured_safety_index"])

    receipts, receipt_by_index, branches, coverage_claims = _source_page_receipts(manifest)
    ranges = _universe_ranges(manifest)
    mappings: list[dict[str, Any]] = []
    seeds: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    universe_for_index: dict[int, Mapping[str, Any]] = {}
    for start, end, universe in ranges:
        for index in range(start, end):
            universe_for_index[index] = universe

    for index in range(mapping_count):
        universe = universe_for_index[index]
        relation = relations.get(index)
        representative_index = int(relation["representative_index"]) if relation else index
        name = _mapping_name(index, relations)
        publication_count = index % 23
        support_strength = round(0.10 + ((index * 7) % 80) / 100, 2)
        long_tail = False
        if index == database_only_index:
            publication_count = 0
            support_strength = 0.84
            long_tail = True
        elif index == sparse_index:
            publication_count = 1
            support_strength = 0.78
            long_tail = True
        elif index == weak_popular_index:
            publication_count = 10_000
            support_strength = 0.35
        elif index == strong_obscure_index:
            publication_count = 1
            support_strength = 0.93
            long_tail = True
        elif index == multi_endpoint_index:
            support_strength = 0.88

        mapping_id = f"MAP-{index:06d}"
        seed_id = f"SEED-{index:06d}"
        mappings.append(
            {
                "mapping_id": mapping_id,
                "mapping_index": index,
                "source_universe_id": universe["source_universe_id"],
                "branch_id": universe["branch_id"],
                "source_family": universe["coverage_family"],
                "native_item_id": f"ITEM-{index:06d}",
                "assertion_locator": f"ITEM-{index:06d}#intervention-1",
                "raw_intervention_assertion": name,
                "mapping_outcome": "seed_emitted",
                "seed_id": seed_id,
            }
        )
        assertions = [
            {
                "authority": "synthetic-resolver-a",
                "release": "2026-07-19",
                "structure_identity_key": structure_key(representative_index),
            }
        ]
        identity_status = "resolved"
        if index == unresolved_index:
            identity_status = "conflicting"
            assertions.append(
                {
                    "authority": "synthetic-resolver-b",
                    "release": "2026-07-19",
                    "structure_identity_key": structure_key(index, variant=1),
                }
            )
        seeds.append(
            {
                "seed_id": seed_id,
                "mapping_id": mapping_id,
                "mapping_index": index,
                "raw_intervention_assertion": name,
                "source_item_id": f"ITEM-{index:06d}",
                "assertion_locator": f"ITEM-{index:06d}#intervention-1",
                "source_universe_id": universe["source_universe_id"],
                "branch_id": universe["branch_id"],
                "retrieval_content_receipt_id": receipt_by_index[index],
                "query_lineage": [universe["branch_id"]],
                "source_family": universe["coverage_family"],
                "evidence_modality": EVIDENCE_MODALITIES[index % len(EVIDENCE_MODALITIES)],
                "publication_count": publication_count,
                "support_strength": support_strength,
                "long_tail": long_tail,
                "identity_assertions": assertions,
                "identity_resolution_status": identity_status,
                "database_only": index == database_only_index,
                "sparse_literature": index == sparse_index,
            }
        )

        disposition = "admit"
        reason_code = "eligible_repurposing_representative"
        normalized_id: str | None = f"NI-{representative_index:06d}"
        representative_seed_id: str | None = None
        if relation:
            disposition = "merge"
            reason_code = f"confirmed_{relation['relation']}_same_breadth_group"
            representative_seed_id = f"SEED-{representative_index:06d}"
        elif index == baseline_index:
            disposition = "baseline"
            reason_code = "fixture_baseline_care_role"
        elif index == reject_index:
            disposition = "reject"
            reason_code = "fixture_non_pharmacologic_scope_exclusion"
        elif index == unresolved_index:
            disposition = "quarantine"
            reason_code = "decision_changing_identity_conflict"
            normalized_id = None
        disposition_row: dict[str, Any] = {
            "seed_id": seed_id,
            "mapping_index": index,
            "disposition": disposition,
            "reason_code": reason_code,
            "normalized_intervention_id": normalized_id,
            "breadth_group_id": f"BG-{representative_index:06d}" if normalized_id else None,
            "active_moiety_id": f"AM-{representative_index:06d}" if normalized_id else None,
            "representative_seed_id": representative_seed_id,
            "rule_version": "synthetic-disposition-v1",
            "provenance_seed_ids": [seed_id],
        }
        dispositions.append(disposition_row)

    disposition_by_seed = {row["seed_id"]: row for row in dispositions}
    normalized_interventions: list[dict[str, Any]] = []
    seen_normalized: set[str] = set()
    for seed in seeds:
        disposition = disposition_by_seed[seed["seed_id"]]
        normalized_id = disposition["normalized_intervention_id"]
        if not normalized_id or normalized_id in seen_normalized:
            continue
        seen_normalized.add(normalized_id)
        normalized_interventions.append(
            {
                "normalized_intervention_id": normalized_id,
                "structure_identity_key": seed["identity_assertions"][0]["structure_identity_key"],
                "breadth_group_id": disposition["breadth_group_id"],
                "active_moiety_id": disposition["active_moiety_id"],
                "resolver_authority": "synthetic-resolver-a",
                "resolver_release": "2026-07-19",
                "source_seed_ids": [seed["seed_id"]],
            }
        )

    endpoint_ids = list(manifest["case"]["endpoint_ids"])
    admitted_indices = [
        int(row["mapping_index"])
        for row in dispositions
        if row["disposition"] == "admit"
    ]
    screens: list[dict[str, Any]] = []
    for index in admitted_indices:
        seed = seeds[index]
        benefit_status = "supportive" if float(seed["support_strength"]) >= 0.60 else "insufficient"
        safety_status = "contradictory" if index == safety_index else "neutral"
        screens.append(
            {
                "candidate_id": candidate_id(index),
                "candidate_key": [
                    manifest["case"]["case_revision_id"],
                    f"NI-{index:06d}",
                    "repurposing",
                ],
                "normalized_intervention_id": f"NI-{index:06d}",
                "source_seed_ids": [f"SEED-{index:06d}"],
                "outcome": "pass",
                "endpoint_assessments": [
                    {
                        "endpoint_id": endpoint_ids[0],
                        "status": benefit_status,
                        "reason": "Synthetic fixture support assessment.",
                        "uncertainty": "fixture_bounded",
                    },
                    {
                        "endpoint_id": endpoint_ids[1],
                        "status": safety_status,
                        "reason": "Synthetic fixture safety assessment.",
                        "uncertainty": "fixture_bounded",
                    },
                ],
                "source_family": seed["source_family"],
                "evidence_modality": seed["evidence_modality"],
                "publication_count": seed["publication_count"],
                "support_strength": seed["support_strength"],
                "long_tail": seed["long_tail"],
            }
        )

    deep_packages: list[dict[str, Any]] = []
    for index_value in manifest["deep_candidate_indices"]:
        index = int(index_value)
        exact_intervention_id = f"NI-{index:06d}"
        serious = index == safety_index
        deep_packages.append(
            {
                "candidate_id": candidate_id(index),
                "origin_seed_ids": [f"SEED-{index:06d}"],
                "identity_resolution": {
                    "status": "resolved",
                    "normalized_intervention_id": exact_intervention_id,
                    "structure_identity_key": structure_key(index),
                    "resolver_authority": "synthetic-resolver-a",
                    "resolver_release": "2026-07-19",
                    "decision_relevant_conflicts": [],
                },
                "endpoint_assessments": [
                    {
                        "endpoint_id": endpoint_ids[0],
                        "status": "supportive" if index != weak_popular_index else "insufficient",
                        "reason": "Frozen synthetic deep-evidence control.",
                    },
                    {
                        "endpoint_id": endpoint_ids[1],
                        "status": "contradictory" if serious else "neutral",
                        "reason": "Frozen synthetic safety control.",
                    },
                ],
                "safety": {
                    "exact_intervention_id": exact_intervention_id,
                    "dose": "synthetic-dose-1",
                    "route": "oral",
                    "duration": "28 days",
                    "population": "synthetic adult population",
                    "tissue": "systemic",
                    "interactions": ["synthetic-interaction"] if serious else [],
                    "contraindications": ["synthetic-risk-population"] if serious else [],
                    "severity": "serious" if serious else "non_serious",
                    "reversibility": "unknown" if serious else "reversible",
                    "source_ids": [f"SRC-{index:06d}-SAFETY"],
                    "uncertainty": "material_conflict" if serious else "fixture_bounded",
                },
                "exposure": {
                    "exact_intervention_id": exact_intervention_id,
                    "dose": "synthetic-dose-1",
                    "route": "oral",
                    "duration": "28 days",
                    "population": "synthetic adult population",
                    "tissue": "systemic",
                    "achieved_exposure": "unresolved" if serious else "adequate",
                    "decision": "quarantine" if serious else "proceed",
                    "source_ids": [f"SRC-{index:06d}-EXPOSURE"],
                    "uncertainty": "decision_changing" if serious else "fixture_bounded",
                },
                "decision_outputs": {
                    "therapeutic_support": seed_support(seeds[index]),
                    "evidence_quality": "synthetic_moderate",
                    "readiness": "synthetic_development",
                    "novelty": "long_tail" if seeds[index]["long_tail"] else "common",
                    "uncertainty": "material" if serious else "bounded",
                    "information_value": "high" if serious else "moderate",
                    "portfolio_diversity": "computed_at_portfolio_stage",
                },
            }
        )

    portfolio_dispositions = {
        int(index): disposition
        for index, disposition in manifest["portfolio_dispositions"].items()
    }
    rank_by_index = {
        int(index): rank
        for rank, index in enumerate(manifest["portfolio_order_indices"], 1)
    }
    portfolio = [
        {
            "candidate_id": candidate_id(index),
            "disposition": portfolio_dispositions[index],
            "rank": rank_by_index.get(index),
            "decision_rule_version": "synthetic-portfolio-v1",
        }
        for index in [int(value) for value in manifest["deep_candidate_indices"]]
    ]

    sample_order = sorted(
        (row["candidate_id"] for row in screens),
        key=lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest(),
    )
    planned_count = int(manifest["audit"]["planned_sample_count"])
    achieved_count = int(manifest["audit"]["achieved_sample_count"])
    audit = {
        "population_count": len(screens),
        "sampling_rule": manifest["audit"]["sampling_rule"],
        "planned_sample_ids": sample_order[:planned_count],
        "achieved_sample_ids": sample_order[:achieved_count],
    }

    return {
        "benchmark_schema_version": manifest["benchmark_schema_version"],
        "fixture_id": manifest["fixture_id"],
        "case": dict(manifest["case"]),
        "inventory": {
            "source_universes": [dict(row) for row in manifest["source_universes"]],
            "branches": branches,
            "frontier": [],
            "coverage_claims": coverage_claims,
            "retrieval_content_receipts": receipts,
        },
        "mappings": mappings,
        "seeds": seeds,
        "seed_dispositions": dispositions,
        "normalized_interventions": normalized_interventions,
        "screen_records": screens,
        "deep_packages": deep_packages,
        "audit": audit,
        "portfolio": portfolio,
        "full_funnel": dict(manifest["expected_counts"]),
    }


def seed_support(seed: Mapping[str, Any]) -> str:
    value = float(seed["support_strength"])
    if value >= 0.85:
        return "strong"
    if value >= 0.60:
        return "moderate"
    return "weak"


def _issue(issues: list[BenchmarkIssue], code: str, message: str) -> None:
    issues.append(BenchmarkIssue(code, message))


def _duplicates(values: Iterable[str]) -> set[str]:
    counts = Counter(values)
    return {value for value, count in counts.items() if count > 1}


def validate_projection(
    manifest: Mapping[str, Any],
    projection: Mapping[str, Any],
    golden: Mapping[str, Any],
) -> list[BenchmarkIssue]:
    """Validate a fixture-shaped v7 projection and return every detected issue."""

    issues: list[BenchmarkIssue] = []
    mappings = list(projection.get("mappings", []))
    seeds = list(projection.get("seeds", []))
    dispositions = list(projection.get("seed_dispositions", []))
    screens = list(projection.get("screen_records", []))
    deep_packages = list(projection.get("deep_packages", []))
    portfolio = list(projection.get("portfolio", []))
    inventory = projection.get("inventory", {})

    if max(int(row["mapping_count"]) for row in manifest["source_universes"]) < 1000:
        _issue(issues, "SOURCE_UNIVERSE_TOO_SMALL", "No declared synthetic source universe has at least 1,000 mappings.")
    expected_mapping_count = int(manifest["generator"]["mapping_count"])
    if len(mappings) != expected_mapping_count:
        _issue(issues, "MAPPING_TOTAL_MISMATCH", f"Expected {expected_mapping_count} mappings, found {len(mappings)}.")

    mapping_ids = [str(row.get("mapping_id", "")) for row in mappings]
    seed_ids = [str(row.get("seed_id", "")) for row in seeds]
    if "" in mapping_ids or _duplicates(mapping_ids):
        _issue(issues, "MAPPING_ID_INVALID", "Mapping IDs must be nonempty and unique.")
    if "" in seed_ids or _duplicates(seed_ids):
        _issue(issues, "SEED_ID_INVALID", "Seed IDs must be nonempty and unique.")

    seeds_by_mapping: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for seed in seeds:
        seeds_by_mapping[str(seed.get("mapping_id", ""))].append(seed)
    for mapping in mappings:
        mapping_id = str(mapping.get("mapping_id", ""))
        linked = seeds_by_mapping.get(mapping_id, [])
        if len(linked) != 1 or mapping.get("mapping_outcome") != "seed_emitted":
            _issue(
                issues,
                "DROPPED_OR_SILENT_MAPPING",
                f"Mapping {mapping_id} does not have exactly one explicit seed outcome.",
            )
        elif str(mapping.get("seed_id", "")) != str(linked[0].get("seed_id", "")):
            _issue(issues, "MAPPING_SEED_LINK_MISMATCH", f"Mapping {mapping_id} has inconsistent seed linkage.")
    unknown_seed_mappings = set(seeds_by_mapping) - set(mapping_ids)
    if unknown_seed_mappings:
        _issue(issues, "SEED_WITHOUT_MAPPING", f"Seeds reference unknown mappings: {sorted(unknown_seed_mappings)[:3]}.")

    disposition_by_seed: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for disposition in dispositions:
        disposition_by_seed[str(disposition.get("seed_id", ""))].append(disposition)
    for seed in seeds:
        rows = disposition_by_seed.get(str(seed.get("seed_id", "")), [])
        if len(rows) != 1:
            _issue(
                issues,
                "SEED_DISPOSITION_INCOMPLETE",
                f"Seed {seed.get('seed_id')} has {len(rows)} canonical dispositions.",
            )
    if set(disposition_by_seed) - set(seed_ids):
        _issue(issues, "DISPOSITION_WITHOUT_SEED", "At least one disposition references an unknown seed.")

    allowed_dispositions = {"admit", "merge", "baseline", "reject", "quarantine", "failed"}
    actual_disposition_counts = Counter(str(row.get("disposition")) for row in dispositions)
    invalid_dispositions = set(actual_disposition_counts) - allowed_dispositions
    if invalid_dispositions:
        _issue(issues, "INVALID_DISPOSITION", f"Invalid dispositions: {sorted(invalid_dispositions)}.")

    receipts = list(inventory.get("retrieval_content_receipts", [])) if isinstance(inventory, Mapping) else []
    receipts_by_universe: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        receipts_by_universe[str(receipt.get("source_universe_id", ""))].append(receipt)
    for universe in manifest["source_universes"]:
        universe_id = str(universe["source_universe_id"])
        rows = sorted(receipts_by_universe.get(universe_id, []), key=lambda row: int(row.get("page_ordinal", 0)))
        expected_total = int(universe["mapping_count"])
        if not rows:
            _issue(issues, "PAGINATION_MISSING", f"Universe {universe_id} has no content receipts.")
            continue
        prior_output: Any = None
        seen_cursors: set[str] = set()
        returned_total = 0
        for ordinal, row in enumerate(rows, 1):
            if row.get("page_ordinal") != ordinal:
                _issue(issues, "PAGINATION_ORDINAL_GAP", f"Universe {universe_id} has a page ordinal gap.")
            if row.get("input_cursor") != prior_output:
                _issue(issues, "PAGINATION_CURSOR_DISCONNECTED", f"Universe {universe_id} page {ordinal} is disconnected.")
            output_cursor = row.get("output_cursor")
            if output_cursor is not None:
                if str(output_cursor) in seen_cursors:
                    _issue(issues, "PAGINATION_CURSOR_LOOP", f"Universe {universe_id} repeats cursor {output_cursor}.")
                seen_cursors.add(str(output_cursor))
            if row.get("provider_total") != expected_total:
                _issue(issues, "PAGINATION_PROVIDER_TOTAL_MISMATCH", f"Universe {universe_id} provider total changed.")
            mapping_ids_on_page = list(row.get("mapping_ids", []))
            if row.get("returned_count") != len(mapping_ids_on_page) or row.get("unique_count") != len(set(mapping_ids_on_page)):
                _issue(issues, "PAGINATION_PAGE_COUNT_MISMATCH", f"Universe {universe_id} page {ordinal} count is unproven.")
            if row.get("truncated") is not False or row.get("cap") is not None:
                _issue(issues, "PAGINATION_HIDDEN_TRUNCATION", f"Universe {universe_id} is capped or truncated.")
            returned_total += int(row.get("returned_count", 0))
            prior_output = output_cursor
        if prior_output is not None or rows[-1].get("terminal_code") != "end_of_results":
            _issue(issues, "PAGINATION_INVALID_TERMINAL", f"Universe {universe_id} lacks a valid exhausted terminal receipt.")
        if returned_total != expected_total:
            _issue(issues, "PAGINATION_TOTAL_MISMATCH", f"Universe {universe_id} returned {returned_total} of {expected_total} mappings.")

    branches = list(inventory.get("branches", [])) if isinstance(inventory, Mapping) else []
    frontier = list(inventory.get("frontier", [])) if isinstance(inventory, Mapping) else []
    unprocessed = [row for row in branches if row.get("state") in {"unprocessed", "open"}]
    if unprocessed and not frontier:
        _issue(
            issues,
            "EMPTY_FRONTIER_WITH_UNPROCESSED_BRANCHES",
            "The frontier is empty while declared inventory branches remain unprocessed.",
        )
    for branch in branches:
        if branch.get("required") is True and branch.get("state") != "complete":
            _issue(issues, "REQUIRED_BRANCH_INCOMPLETE", f"Required branch {branch.get('branch_id')} is not complete.")

    coverage_claims = list(inventory.get("coverage_claims", [])) if isinstance(inventory, Mapping) else []
    family_sources: dict[str, str] = {}
    source_families: dict[str, str] = {}
    receipt_owners: dict[str, str] = {}
    for claim in coverage_claims:
        family = str(claim.get("coverage_family", ""))
        source_id = str(claim.get("source_universe_id", ""))
        if family in family_sources and family_sources[family] != source_id:
            _issue(issues, "COVERAGE_FAMILY_AMBIGUOUS", f"Coverage family {family} uses conflicting universes.")
        family_sources[family] = source_id
        if source_id in source_families and source_families[source_id] != family:
            _issue(
                issues,
                "REUSED_SOURCE_FALSE_COVERAGE",
                f"Universe {source_id} is reused to satisfy unrelated families {source_families[source_id]} and {family}.",
            )
        source_families[source_id] = family
        for receipt_id in claim.get("receipt_ids", []):
            receipt_id = str(receipt_id)
            if receipt_id in receipt_owners and receipt_owners[receipt_id] != family:
                _issue(issues, "REUSED_RECEIPT_FALSE_COVERAGE", f"Receipt {receipt_id} is reused across unrelated families.")
            receipt_owners[receipt_id] = family

    relation_rows = manifest["special_indices"]["merge_relations"]
    dispositions_by_index = {int(row.get("mapping_index", -1)): row for row in dispositions}
    for relation in relation_rows:
        index = int(relation["mapping_index"])
        representative = int(relation["representative_index"])
        row = dispositions_by_index.get(index, {})
        representative_row = dispositions_by_index.get(representative, {})
        if (
            row.get("disposition") != "merge"
            or row.get("representative_seed_id") != f"SEED-{representative:06d}"
            or row.get("breadth_group_id") != representative_row.get("breadth_group_id")
            or representative_row.get("disposition") != "admit"
        ):
            _issue(issues, "CHEMICAL_DEDUPLICATION_FAILED", f"{relation['relation']} mapping {index} is not merged correctly.")
    admitted_breadth_groups = [
        str(row.get("breadth_group_id", ""))
        for row in dispositions
        if row.get("disposition") == "admit"
    ]
    duplicate_breadth_groups = _duplicates(admitted_breadth_groups)
    if duplicate_breadth_groups:
        _issue(issues, "DUPLICATE_ADMITTED_BREADTH_GROUP", f"Admitted breadth groups repeat: {sorted(duplicate_breadth_groups)[:3]}.")

    screen_by_candidate = {str(row.get("candidate_id", "")): row for row in screens}
    deep_by_candidate = {str(row.get("candidate_id", "")): row for row in deep_packages}
    unresolved_index = int(manifest["special_indices"]["unresolved_identity_index"])
    unresolved_candidate_id = candidate_id(unresolved_index)
    for package in deep_packages:
        identity = package.get("identity_resolution", {})
        origin_seed_ids = set(str(value) for value in package.get("origin_seed_ids", []))
        origin_dispositions = [
            rows[0]
            for seed_id in origin_seed_ids
            if (rows := disposition_by_seed.get(seed_id))
        ]
        if (
            package.get("candidate_id") not in screen_by_candidate
            or not isinstance(identity, Mapping)
            or identity.get("status") != "resolved"
            or not identity.get("normalized_intervention_id")
            or identity.get("decision_relevant_conflicts")
            or any(row.get("disposition") != "admit" for row in origin_dispositions)
        ):
            _issue(
                issues,
                "UNRESOLVED_IDENTITY_AT_DEPTH",
                f"Deep package {package.get('candidate_id')} lacks resolved, admitted decision-relevant identity.",
            )
    if unresolved_candidate_id in deep_by_candidate:
        _issue(issues, "UNRESOLVED_IDENTITY_AT_DEPTH", "The unresolved-identity control entered the deep tier.")

    endpoint_ids = set(str(value) for value in manifest["case"]["endpoint_ids"])
    required_endpoint_ids = set(str(value) for value in manifest["case"]["required_endpoint_ids"])
    for tier_name, rows in (("screen", screens), ("deep", deep_packages)):
        for row in rows:
            assessments = list(row.get("endpoint_assessments", []))
            by_endpoint = {str(item.get("endpoint_id", "")): item for item in assessments if isinstance(item, Mapping)}
            if set(by_endpoint) != endpoint_ids:
                _issue(
                    issues,
                    "MULTI_ENDPOINT_INCOMPLETE",
                    f"{tier_name} record {row.get('candidate_id')} does not cover the exact endpoint portfolio.",
                )
            for endpoint_id in required_endpoint_ids:
                if by_endpoint.get(endpoint_id, {}).get("status") == "not_assessed":
                    _issue(issues, "REQUIRED_ENDPOINT_NOT_ASSESSED", f"Required endpoint {endpoint_id} is not assessed.")

    for package in deep_packages:
        safety = package.get("safety")
        exposure = package.get("exposure")
        if not isinstance(safety, Mapping) or not SAFETY_FIELDS.issubset(safety):
            _issue(issues, "SAFETY_NOT_STRUCTURED", f"Deep package {package.get('candidate_id')} lacks structured safety.")
        if not isinstance(exposure, Mapping) or not EXPOSURE_FIELDS.issubset(exposure):
            _issue(issues, "EXPOSURE_NOT_STRUCTURED", f"Deep package {package.get('candidate_id')} lacks structured exposure.")
        outputs = package.get("decision_outputs")
        if not isinstance(outputs, Mapping) or set(outputs) != DECISION_OUTPUTS:
            _issue(issues, "DECISION_OUTPUTS_INCOMPLETE", f"Deep package {package.get('candidate_id')} lacks seven separate outputs.")

    portfolio_by_candidate = {str(row.get("candidate_id", "")): row for row in portfolio}
    for control in list(golden["positive_controls"]) + list(golden["negative_controls"]):
        index = int(control["mapping_index"])
        disposition = dispositions_by_index.get(index, {}).get("disposition")
        if disposition != control["expected_seed_disposition"]:
            _issue(issues, "GOLDEN_DISPOSITION_MISMATCH", f"Control {control['control_id']} has disposition {disposition}.")
        expected_portfolio = control.get("expected_portfolio_disposition")
        if expected_portfolio and portfolio_by_candidate.get(candidate_id(index), {}).get("disposition") != expected_portfolio:
            _issue(issues, "GOLDEN_PORTFOLIO_MISMATCH", f"Control {control['control_id']} has the wrong portfolio disposition.")
        if control.get("forbidden_stage") == "deep" and candidate_id(index) in deep_by_candidate:
            _issue(issues, "GOLDEN_FORBIDDEN_PROMOTION", f"Control {control['control_id']} entered the forbidden deep stage.")

    database_only_index = int(manifest["special_indices"]["database_only_index"])
    sparse_index = int(manifest["special_indices"]["sparse_literature_index"])
    for index, code in (
        (database_only_index, "DATABASE_ONLY_SEED_LOST"),
        (sparse_index, "SPARSE_LITERATURE_SEED_LOST"),
    ):
        if dispositions_by_index.get(index, {}).get("disposition") != "admit" or candidate_id(index) not in screen_by_candidate:
            _issue(issues, code, f"Long-tail control at mapping {index} did not survive seed admission and screening.")

    weak_index = int(manifest["special_indices"]["weak_popular_index"])
    strong_index = int(manifest["special_indices"]["strong_obscure_index"])
    ranked = {
        str(row.get("candidate_id")): int(row["rank"])
        for row in portfolio
        if isinstance(row.get("rank"), int)
    }
    if candidate_id(strong_index) not in ranked or candidate_id(weak_index) not in ranked:
        _issue(issues, "POPULARITY_CONTROL_ERASED", "The strong-obscure or weak-popular control is absent from ranked review.")
    elif ranked[candidate_id(strong_index)] >= ranked[candidate_id(weak_index)]:
        _issue(issues, "POPULARITY_BIAS", "The weak highly published control outranks the stronger obscure control.")

    actual_counts = {
        "mappings": len(mappings),
        "seeds": len(seeds),
        **{name: actual_disposition_counts.get(name, 0) for name in allowed_dispositions},
        "identity_all": len({row.get("normalized_intervention_id") for row in dispositions if row.get("normalized_intervention_id")}),
        "identity_admitted": len({row.get("normalized_intervention_id") for row in dispositions if row.get("disposition") == "admit"}),
        "identity_baseline": len({row.get("normalized_intervention_id") for row in dispositions if row.get("disposition") == "baseline"}),
        "breadth_admitted": len({row.get("breadth_group_id") for row in dispositions if row.get("disposition") == "admit"}),
        "screened": sum(1 for row in screens if row.get("outcome") == "pass"),
        "selected_deep": len(deep_packages),
        "deep": len(deep_packages),
        "screen_only": sum(1 for row in screens if row.get("outcome") == "pass") - len(deep_packages),
        "finalist": sum(1 for row in portfolio if row.get("disposition") == "finalist"),
        "reserve": sum(1 for row in portfolio if row.get("disposition") == "reserve"),
        "not_selected": sum(1 for row in portfolio if row.get("disposition") == "not_selected"),
        "audit_rejected": sum(1 for row in portfolio if row.get("disposition") == "audit_rejected"),
        "audit_quarantined": sum(1 for row in portfolio if row.get("disposition") == "audit_quarantined"),
    }
    full_funnel = dict(projection.get("full_funnel", {}))
    for field, expected in manifest["expected_counts"].items():
        actual = actual_counts.get(field)
        if actual != int(expected) or full_funnel.get(field) != int(expected):
            _issue(
                issues,
                "FULL_FUNNEL_RECONCILIATION_FAILED",
                f"Funnel count {field}: actual={actual}, emitted={full_funnel.get(field)}, expected={expected}.",
            )
    if actual_counts["seeds"] != sum(actual_disposition_counts.get(name, 0) for name in allowed_dispositions):
        _issue(issues, "SEED_EQUATION_FAILED", "Seed disposition equation does not balance.")
    if actual_counts["screened"] != actual_counts["selected_deep"] + actual_counts["screen_only"]:
        _issue(issues, "DEEP_SELECTION_EQUATION_FAILED", "Deep-selection equation does not balance.")
    if actual_counts["deep"] != sum(
        actual_counts[name]
        for name in ("finalist", "reserve", "not_selected", "audit_rejected", "audit_quarantined")
    ):
        _issue(issues, "PORTFOLIO_EQUATION_FAILED", "Portfolio equation does not balance.")

    audit = projection.get("audit", {})
    planned = list(audit.get("planned_sample_ids", [])) if isinstance(audit, Mapping) else []
    achieved = list(audit.get("achieved_sample_ids", [])) if isinstance(audit, Mapping) else []
    if len(planned) != int(manifest["audit"]["planned_sample_count"]) or not set(achieved).issubset(planned):
        _issue(issues, "AUDIT_SAMPLE_RECONCILIATION_FAILED", "Audit planned and achieved samples do not reconcile.")
    if len(achieved) != int(manifest["audit"]["achieved_sample_count"]):
        _issue(issues, "AUDIT_SAMPLE_INCOMPLETE", "Audit achieved sample count differs from the frozen plan.")

    return issues


def _normalized_shannon(values: Iterable[str]) -> dict[str, Any]:
    counts = Counter(values)
    total = sum(counts.values())
    if total == 0:
        return {"distinct_count": 0, "normalized_shannon": 0.0}
    entropy = -sum((count / total) * math.log(count / total) for count in counts.values())
    normalized = 1.0 if len(counts) == 1 else entropy / math.log(len(counts))
    return {"distinct_count": len(counts), "normalized_shannon": round(normalized, 6)}


def build_task_packets(candidate_count: int, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build benchmark-only minimal packets under supplied, non-global limits."""

    max_candidates = int(contract["max_candidates_per_shard"])
    max_bytes = int(contract["max_packet_bytes"])
    records = [
        {
            "candidate_id": candidate_id(index),
            "normalized_intervention_id": f"NI-{index:06d}",
            "endpoint_ids": ["EP-BENEFIT", "EP-SAFETY"],
        }
        for index in range(candidate_count)
    ]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def envelope(rows: list[dict[str, Any]], ordinal: int) -> dict[str, Any]:
        candidate_ids = [row["candidate_id"] for row in rows]
        return {
            "schema_version": 7,
            "task_name": "candidate_screen",
            "shard_key": f"candidate-screen-{ordinal:04d}-{canonical_sha256(candidate_ids)[:12]}",
            "dependency_commit_id": "SHA256:" + ("A" * 64),
            "candidates": rows,
        }

    for record in records:
        proposed = [*current, record]
        ordinal = len(chunks) + 1
        if current and (
            len(proposed) > max_candidates
            or len(canonical_bytes(envelope(proposed, ordinal))) > max_bytes
        ):
            chunks.append(current)
            current = [record]
        else:
            current = proposed
        if len(canonical_bytes(envelope(current, len(chunks) + 1))) > max_bytes:
            raise ValueError("One benchmark candidate exceeds the configured packet byte limit")
    if current:
        chunks.append(current)
    packets = [envelope(rows, ordinal) for ordinal, rows in enumerate(chunks, 1)]
    if any(len(packet["candidates"]) > max_candidates for packet in packets):
        raise AssertionError("benchmark packet candidate limit exceeded")
    if any(len(canonical_bytes(packet)) > max_bytes for packet in packets):
        raise AssertionError("benchmark packet byte limit exceeded")
    return packets


def packet_measurements(manifest: Mapping[str, Any]) -> dict[str, Any]:
    contract = manifest["packet_contract"]
    results: dict[str, Any] = {
        "configured_max_candidates_per_shard": int(contract["max_candidates_per_shard"]),
        "configured_max_packet_bytes": int(contract["max_packet_bytes"]),
        "sizes": {},
    }
    for requested in contract["test_sizes"]:
        count = int(requested)
        packets = build_task_packets(count, contract)
        sizes = [len(canonical_bytes(packet)) for packet in packets]
        flattened = [row["candidate_id"] for packet in packets for row in packet["candidates"]]
        results["sizes"][str(count)] = {
            "candidate_count": count,
            "shard_count": len(packets),
            "max_candidates_in_shard": max(len(packet["candidates"]) for packet in packets),
            "max_packet_bytes": max(sizes),
            "mean_packet_bytes": round(statistics.fmean(sizes), 3),
            "total_packet_bytes": sum(sizes),
            "candidate_coverage": len(flattened),
            "duplicate_candidate_ids": len(flattened) - len(set(flattened)),
        }
    return results


def canonical_reduce(records: Iterable[Mapping[str, Any]], id_field: str) -> list[dict[str, Any]]:
    """Benchmark oracle for idempotent canonical reduction; conflicts fail loudly."""

    canonical: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record[id_field])
        value = dict(record)
        if key in canonical and canonical[key] != value:
            raise ValueError(f"idempotency conflict for {key}")
        canonical[key] = value
    return [canonical[key] for key in sorted(canonical)]


def replay_recovery_measurement(projection: Mapping[str, Any]) -> dict[str, Any]:
    scientific_rows = [
        {
            "seed_id": row["seed_id"],
            "disposition": row["disposition"],
            "normalized_intervention_id": row["normalized_intervention_id"],
            "breadth_group_id": row["breadth_group_id"],
        }
        for row in projection["seed_dispositions"]
    ]
    initial_commit = canonical_reduce(scientific_rows, "seed_id")
    replay_commit = canonical_reduce([*scientific_rows, *reversed(scientific_rows)], "seed_id")
    staged_before_interruption = list(reversed(scientific_rows))
    recovered_commit = canonical_reduce(staged_before_interruption, "seed_id")
    hashes = {
        "initial_scientific_hash": canonical_sha256(initial_commit),
        "replay_scientific_hash": canonical_sha256(replay_commit),
        "recovered_scientific_hash": canonical_sha256(recovered_commit),
    }
    return {
        **hashes,
        "hashes_identical": len(set(hashes.values())) == 1,
        "canonical_record_count": len(initial_commit),
        "replay_duplicate_effects_prevented": len(scientific_rows),
        "interrupted_commit_recovered": recovered_commit == initial_commit,
        "execution_receipts_differ_allowed": True,
    }


def legacy_operation_decision(schema_version: int, operation: str) -> dict[str, Any]:
    if schema_version in {3, 4, 5, 6} and operation == "inspect":
        return {"allowed": True, "mode": "read_only", "reason": "legacy inspection"}
    if schema_version in {3, 4, 5, 6} and operation in {"resume", "write", "append", "finalize"}:
        return {"allowed": False, "mode": "read_only", "reason": "legacy mutation prohibited"}
    return {"allowed": False, "mode": "unsupported", "reason": "unsupported schema/operation"}


def validate_legacy_fixtures() -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    for version in (3, 4, 5, 6):
        path = LEGACY_ROOT / f"schema-v{version}.json"
        before = path.read_bytes()
        payload = json.loads(before.decode("utf-8"))
        if payload.get("schema_version") != version:
            _issue(issues, "LEGACY_VERSION_MISMATCH", f"Legacy fixture v{version} reports another version.")
        if legacy_operation_decision(version, "inspect")["allowed"] is not True:
            _issue(issues, "LEGACY_INSPECTION_REFUSED", f"Legacy fixture v{version} cannot be inspected.")
        for operation in ("resume", "write", "append", "finalize"):
            if legacy_operation_decision(version, operation)["allowed"] is not False:
                _issue(issues, "LEGACY_MUTATION_ALLOWED", f"Legacy fixture v{version} permits {operation}.")
        if path.read_bytes() != before:
            _issue(issues, "LEGACY_FIXTURE_MUTATED", f"Legacy fixture v{version} changed during inspection.")
    return issues


def validate_frozen_assets() -> list[BenchmarkIssue]:
    issues: list[BenchmarkIssue] = []
    if not FIXTURE_CHECKSUMS_PATH.is_file():
        return [BenchmarkIssue("FROZEN_CHECKSUMS_MISSING", "fixture_checksums.json is missing.")]
    checksums = load_json(FIXTURE_CHECKSUMS_PATH)
    for relative_path, expected_hash in checksums.get("sha256", {}).items():
        path = BENCHMARK_ROOT / str(relative_path)
        if not path.is_file():
            _issue(issues, "FROZEN_ASSET_MISSING", f"Frozen asset is missing: {relative_path}.")
        elif file_sha256(path) != str(expected_hash).upper():
            _issue(issues, "FROZEN_ASSET_HASH_MISMATCH", f"Frozen asset hash changed: {relative_path}.")
    return issues


def benchmark_metrics(
    manifest: Mapping[str, Any],
    projection: Mapping[str, Any],
    golden: Mapping[str, Any],
    runtime_seconds: float,
) -> dict[str, Any]:
    mappings = list(projection["mappings"])
    seeds = list(projection["seeds"])
    dispositions = list(projection["seed_dispositions"])
    seed_mapping_ids = {str(row["mapping_id"]) for row in seeds}
    disposition_seed_ids = {str(row["seed_id"]) for row in dispositions}
    relevant = {candidate_id(int(index)) for index in golden["ranking_relevant_candidate_indices"]}
    ranked = sorted(
        (row for row in projection["portfolio"] if isinstance(row.get("rank"), int)),
        key=lambda row: int(row["rank"]),
    )
    k = int(golden["top_k"])
    top_k = {str(row["candidate_id"]) for row in ranked[:k]}
    long_tail_expected = {candidate_id(int(index)) for index in golden["long_tail_candidate_indices"]}
    screened_ids = {str(row["candidate_id"]) for row in projection["screen_records"]}
    disposition_counts = Counter(str(row["disposition"]) for row in dispositions)
    unresolved = sum(1 for row in seeds if row.get("identity_resolution_status") != "resolved")
    audit = projection["audit"]
    planned = len(audit["planned_sample_ids"])
    achieved = len(audit["achieved_sample_ids"])
    return {
        "candidate_universe_recall": {
            "value": round(len(seed_mapping_ids) / len(mappings), 6) if mappings else 0.0,
            "numerator": len(seed_mapping_ids),
            "denominator": len(mappings),
            "scope": "declared frozen synthetic source mappings; not a global scientific universe",
        },
        "seed_disposition_completeness": {
            "value": round(len(disposition_seed_ids) / len(seeds), 6) if seeds else 0.0,
            "numerator": len(disposition_seed_ids),
            "denominator": len(seeds),
        },
        "recall_at_k": {
            "k": k,
            "value": round(len(top_k & relevant) / len(relevant), 6) if relevant else 0.0,
            "relevant_count": len(relevant),
        },
        "precision_at_k": {
            "k": k,
            "value": round(len(top_k & relevant) / k, 6) if k else 0.0,
            "true_positive_count": len(top_k & relevant),
        },
        "long_tail_recall": {
            "value": round(len(long_tail_expected & screened_ids) / len(long_tail_expected), 6),
            "numerator": len(long_tail_expected & screened_ids),
            "denominator": len(long_tail_expected),
        },
        "source_diversity": _normalized_shannon(str(row["source_family"]) for row in seeds),
        "evidence_modality_diversity": _normalized_shannon(str(row["evidence_modality"]) for row in seeds),
        "duplicate_rate": {
            "value": round(disposition_counts["merge"] / len(seeds), 6) if seeds else 0.0,
            "merged_occurrences": disposition_counts["merge"],
            "seed_denominator": len(seeds),
            "post_dedup_admitted_duplicate_rate": 0.0,
        },
        "unresolved_identity_rate": {
            "value": round(unresolved / len(seeds), 6) if seeds else 0.0,
            "unresolved_count": unresolved,
            "seed_denominator": len(seeds),
        },
        "audit_sampling_coverage": {
            "value": round(achieved / planned, 6) if planned else 0.0,
            "achieved": achieved,
            "planned": planned,
            "population": int(audit["population_count"]),
        },
        "runtime": {
            "baseline_seconds": round(runtime_seconds, 6),
            "network_calls": 0,
        },
        "packet_size": packet_measurements(manifest),
    }


PASSING_CHECK_IDS = (
    "synthetic_source_universe_1000",
    "mapping_seed_disposition_reconciliation",
    "dropped_mapping_detection",
    "pagination_total_and_cursor_chain",
    "empty_frontier_rejection",
    "unrelated_coverage_source_reuse_rejection",
    "alias_salt_formulation_deduplication",
    "unresolved_identity_deep_rejection",
    "database_only_and_sparse_seed_preservation",
    "popularity_bias_control",
    "multi_endpoint_completeness",
    "structured_safety_and_exposure",
    "deterministic_replay_and_interrupted_recovery_oracle",
    "packet_limits_500_and_1000",
    "legacy_v3_to_v6_read_only_oracle",
    "full_funnel_reconciliation",
    "offline_golden_positive_and_negative_controls",
)


def run_baseline() -> dict[str, Any]:
    started = time.perf_counter()
    manifest = load_fixture_manifest()
    golden = load_golden_controls()
    projection = generate_projection(manifest)
    issues = [
        *validate_frozen_assets(),
        *validate_projection(manifest, projection, golden),
        *validate_legacy_fixtures(),
    ]
    projection_hash = canonical_sha256(projection)
    if projection_hash != str(manifest["expected_generated_projection_sha256"]).upper():
        issues.append(
            BenchmarkIssue(
                "GENERATED_PROJECTION_HASH_MISMATCH",
                f"Generated projection hash {projection_hash} differs from the frozen expectation.",
            )
        )
    replay = replay_recovery_measurement(projection)
    if not replay["hashes_identical"] or not replay["interrupted_commit_recovered"]:
        issues.append(BenchmarkIssue("REPLAY_RECOVERY_ORACLE_FAILED", "Replay/recovery changed the scientific projection."))
    packets = packet_measurements(manifest)
    for size, measurement in packets["sizes"].items():
        if measurement["candidate_coverage"] != measurement["candidate_count"] or measurement["duplicate_candidate_ids"]:
            issues.append(BenchmarkIssue("PACKET_SHARD_RECONCILIATION_FAILED", f"Packet test size {size} lost or duplicated candidates."))
    runtime_seconds = time.perf_counter() - started
    metrics = benchmark_metrics(manifest, projection, golden, runtime_seconds)
    return {
        "status": "pass" if not issues else "fail",
        "benchmark_schema_version": manifest["benchmark_schema_version"],
        "fixture_id": manifest["fixture_id"],
        "fixture_partition": manifest["partition"],
        "certification_eligible": manifest["certification_eligible"],
        "network_required": False,
        "generated_projection_sha256": projection_hash,
        "passing_checks": list(PASSING_CHECK_IDS) if not issues else [],
        "issues": [asdict(issue) for issue in issues],
        "metrics": metrics,
        "replay_and_recovery": replay,
        "implemented_production_tests": [
            dict(row) for row in EXPECTED_PRODUCTION_INTERFACES
            if str(row["reason"]).startswith("IMPLEMENTED:")
        ],
        "pending_production_tests": [
            dict(row) for row in EXPECTED_PRODUCTION_INTERFACES
            if str(row["reason"]).startswith("PENDING:")
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "list-pending"), default="baseline")
    args = parser.parse_args(argv)
    if args.mode == "list-pending":
        print(json.dumps(list(EXPECTED_PRODUCTION_INTERFACES), indent=2))
        return 0
    report = run_baseline()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
