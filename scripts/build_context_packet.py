#!/usr/bin/env python3
"""Build immutable compact context packets without raw-source bodies or candidate leakage."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from program_contract import SCHEMA_VERSION, role_contract
from program_io import canonical_bytes, content_hash, index_rows, read_json, read_jsonl


SOURCE_PACKET_FIELDS = (
    "source_id", "canonical_identifier", "identifier_type", "title", "year", "source_kind",
    "source_family", "original_pointer", "verification_method", "verification_scope",
    "supported_claim_ids", "discovered_by_units", "discovery_query_ids",
)


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.")
    if not cleaned:
        raise ValueError("job_id cannot be normalized")
    return cleaned


def _compact_source(row: dict[str, Any], *, include_provenance: bool = True) -> dict[str, Any]:
    provenance = {"supported_claim_ids", "discovered_by_units", "discovery_query_ids"}
    return {
        field: row[field]
        for field in SOURCE_PACKET_FIELDS
        if field in row and (include_provenance or field not in provenance)
    }


def _candidate_source_ids(candidate: dict[str, Any]) -> set[str]:
    values = {
        str(value)
        for field in (
            "identity_source_ids", "rationale_source_ids", "candidate_class_source_ids",
            "active_moiety_source_ids",
        )
        for value in candidate.get(field, [])
    }
    for field in ("target_endpoint", "repurposing_readiness", "experimental_model_suitability"):
        nested = candidate.get(field, {})
        if isinstance(nested, dict):
            values.update(str(value) for value in nested.get("source_ids", []))
    for field in ("score_components", "cap_assessments"):
        collection = candidate.get(field, {})
        if isinstance(collection, dict):
            for nested in collection.values():
                if isinstance(nested, dict):
                    values.update(str(value) for value in nested.get("source_ids", []))
    return values


def _selected_context(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    sources = index_rows(read_jsonl(root / "source_corpus.jsonl"), "source_id")
    searches = read_jsonl(root / "search_log.jsonl")
    claims = index_rows(read_jsonl(root / "claim_ledger.jsonl"), "claim_id")
    edges = index_rows(read_jsonl(root / "evidence_graph.jsonl"), "edge_id")
    units = index_rows(read_jsonl(root / "research_units.jsonl"), "unit_id")
    observations = read_jsonl(root / "candidate_observations.jsonl")
    candidates = index_rows(read_jsonl(root / "candidate_records.jsonl"), "candidate_id")
    audits = read_jsonl(root / "audit_records.jsonl")
    councils = read_jsonl(root / "council_records.jsonl")
    kind = str(job.get("kind", ""))
    unit = units.get(str(job.get("unit_id", "")), {})

    selected_claims: list[dict[str, Any]] = []
    selected_edges: list[dict[str, Any]] = []
    selected_observations: list[dict[str, Any]] = []
    selected_candidates: list[dict[str, Any]] = []
    selected_audits: list[dict[str, Any]] = []
    selected_councils: list[dict[str, Any]] = []

    if kind == "research" and unit.get("unit_type") == "compound_perspective":
        broad_unit_ids = {
            unit_id for unit_id, row in units.items() if row.get("unit_type") == "broad_evidence"
        }
        broad_claim_ids = {
            str(claim_id)
            for search in searches
            if str(search.get("research_unit_id")) in broad_unit_ids
            for claim_id in search.get("produced_claim_ids", [])
        }
        selected_claims = [claims[value] for value in sorted(broad_claim_ids) if value in claims]
        selected_edges = [
            edge for edge in edges.values()
            if edge.get("claim_ids")
            and {str(value) for value in edge.get("claim_ids", [])}.issubset(broad_claim_ids)
            and not str(edge.get("from_node", "")).startswith("CHEM:")
            and not str(edge.get("to_node", "")).startswith("CHEM:")
        ]
    elif kind == "merge":
        selected_claims = list(claims.values())
        selected_edges = list(edges.values())
        selected_observations = observations
    elif kind == "decisive_audit":
        selected_candidates = list(candidates.values())
        decisive = {
            str(value)
            for candidate in selected_candidates
            for value in candidate.get("decisive_claim_ids", [])
        }
        selected_claims = [claims[value] for value in sorted(decisive) if value in claims]
        edge_ids = {
            str(value)
            for candidate in selected_candidates
            for path in candidate.get("causal_paths", [])
            if isinstance(path, dict)
            for value in path.get("edge_ids", [])
        }
        selected_edges = [edges[value] for value in sorted(edge_ids) if value in edges]
    elif kind == "council":
        candidate_ids = {str(value) for value in job.get("candidate_ids", [])}
        selected_candidates = [candidates[value] for value in sorted(candidate_ids) if value in candidates]
        decisive = {
            str(value)
            for candidate in selected_candidates
            for value in candidate.get("decisive_claim_ids", [])
        }
        selected_claims = [claims[value] for value in sorted(decisive) if value in claims]
        edge_ids = {
            str(value)
            for candidate in selected_candidates
            for path in candidate.get("causal_paths", [])
            if isinstance(path, dict)
            for value in path.get("edge_ids", [])
        }
        selected_edges = [edges[value] for value in sorted(edge_ids) if value in edges]
        selected_audits = [row for row in audits if str(row.get("subject_id")) in decisive]
        selected_councils = [row for row in councils if str(row.get("candidate_id")) in candidate_ids]

    source_ids = {
        str(value)
        for claim in selected_claims
        for value in claim.get("source_ids", [])
    }
    for candidate in selected_candidates:
        source_ids.update(_candidate_source_ids(candidate))
    source_ids.update(
        str(value)
        for observation in selected_observations
        for field in ("identity_source_ids", "rationale_source_ids", "active_moiety_source_ids")
        for value in observation.get(field, [])
    )
    source_ids.update(
        str(value)
        for audit in selected_audits
        for value in audit.get("checked_source_ids", [])
    )
    isolated_compound_research = kind == "research" and unit.get("unit_type") == "compound_perspective"
    return {
        "research_unit": unit,
        "claims": selected_claims,
        "edges": selected_edges,
        "sources": [
            _compact_source(sources[value], include_provenance=not isolated_compound_research)
            for value in sorted(source_ids)
            if value in sources
        ],
        "candidate_observations": selected_observations,
        "candidates": selected_candidates,
        "audit_records": selected_audits,
        "prior_council_records": selected_councils,
        "selection_stats": {
            "all_claims": len(claims),
            "selected_claims": len(selected_claims),
            "all_sources": len(sources),
            "selected_sources": len(source_ids),
            "observations": len(selected_observations),
            "candidates": len(selected_candidates),
        },
    }


def _chunks(context: dict[str, Any], max_bytes: int) -> list[dict[str, Any]]:
    static = {key: value for key, value in context.items() if not isinstance(value, list)}
    collections = {key: value for key, value in context.items() if isinstance(value, list)}
    empty = {key: [] for key in collections}
    first = {**static, **empty, "context_part": 1}
    if len(canonical_bytes(first)) > max_bytes:
        raise ValueError("Static context exceeds the packet limit")
    atoms = [(name, item) for name, rows in collections.items() for item in rows]
    if not atoms:
        return [first]
    result: list[dict[str, Any]] = []
    current = first
    for name, item in atoms:
        proposed = {**current, name: [*current[name], item]}
        if len(canonical_bytes(proposed)) <= max_bytes:
            current = proposed
            continue
        if current == first:
            raise ValueError(f"One {name} record exceeds the packet limit")
        result.append(current)
        current = {**empty, "context_part": len(result) + 1}
        current[name] = [item]
        if len(canonical_bytes(current)) > max_bytes:
            raise ValueError(f"One {name} record exceeds the packet limit")
    result.append(current)
    return result


def build_packet(run_folder: str | Path, job_id: str, max_bytes: int = 60000) -> tuple[Path, str]:
    root = Path(run_folder).expanduser().resolve()
    case = read_json(root / "case.json", {})
    plan = read_json(root / "execution_plan.json", {})
    jobs = index_rows(plan.get("jobs", []), "job_id")
    job = jobs.get(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")
    units = index_rows(read_jsonl(root / "research_units.jsonl"), "unit_id")
    unit = units.get(str(job.get("unit_id", "")))
    base = {
        "schema_version": SCHEMA_VERSION,
        "run_root": str(root),
        "path_contract": {
            "relative_paths_resolve_against": "run_root",
            "raw_source_bodies": "raw_sources",
            "result_updates_are_staged": True,
        },
        "job": {
            key: job.get(key)
            for key in ("job_id", "kind", "role", "unit_id", "question", "candidate_ids")
        },
        "machine_contract": role_contract(job, unit),
        "case": case,
    }
    room = max_bytes - len(canonical_bytes(base)) - 1024
    if room <= 0:
        raise ValueError("Machine contract leaves no room for context")
    contexts = _chunks(_selected_context(root, job), room)
    packet_dir = root / "packets" / _safe(job_id)
    packet_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, context in enumerate(contexts, 1):
        packet = {**base, "chunk_index": index, "chunk_count": len(contexts), "context": context}
        payload = canonical_bytes(packet)
        if len(payload) > max_bytes:
            raise ValueError(f"Packet chunk {index} exceeds {max_bytes} bytes")
        path = packet_dir / f"input_{index:03d}.json"
        path.write_bytes(payload)
        entries.append(
            {
                "chunk_index": index,
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "required_chunks": entries,
        "all_chunks_required": True,
        "max_chunk_bytes": max_bytes,
        "total_packet_bytes": sum(row["bytes"] for row in entries),
    }
    manifest["packet_hash"] = content_hash(manifest)
    manifest_path = packet_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    return manifest_path, str(manifest["packet_hash"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder")
    parser.add_argument("job_id")
    parser.add_argument("--max-bytes", type=int, default=60000)
    args = parser.parse_args()
    path, packet_hash = build_packet(args.run_folder, args.job_id, args.max_bytes)
    print(json.dumps({"manifest_path": str(path), "packet_hash": packet_hash}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
