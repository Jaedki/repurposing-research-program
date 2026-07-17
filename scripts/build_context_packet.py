#!/usr/bin/env python3
"""Build immutable, job-specific context packets from audited run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from program_contract import SCHEMA_VERSION, role_contract


SOURCE_PACKET_FIELDS = (
    "source_id",
    "canonical_identifier",
    "identifier_type",
    "title",
    "year",
    "source_kind",
    "source_family",
    "screen_decision",
    "original_pointer",
    "verification_method",
    "verification_scope",
    "supported_claim_ids",
)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _artifact_paths(result: dict[str, Any]) -> list[dict[str, Any]]:
    updates = result.get("ledger_updates", {})
    if not isinstance(updates, dict):
        return []
    artifacts: dict[str, dict[str, Any]] = {}

    def add(value: Any, kind: str, immutable: bool) -> None:
        path = str(value or "")
        if not path:
            return
        current = artifacts.setdefault(path, {"path": path, "kinds": [], "immutable": True})
        if kind not in current["kinds"]:
            current["kinds"].append(kind)
        current["immutable"] = bool(current["immutable"] and immutable)

    for source in updates.get("source_corpus.jsonl", []):
        if isinstance(source, dict) and source.get("compaction_receipt_path"):
            add(source["compaction_receipt_path"], "source_compaction_receipt", True)
    for search in updates.get("search_log.jsonl", []):
        if not isinstance(search, dict):
            continue
        for value in search.get("compact_payload_paths", []):
            add(value, "search_compaction_receipt", True)
        for item in search.get("pagination_trace", []):
            if isinstance(item, dict):
                add(item.get("receipt_path"), "pagination_receipt", True)
    for candidate in updates.get("candidate_records.jsonl", []):
        if isinstance(candidate, dict) and candidate.get("dossier_path"):
            add(candidate["dossier_path"], "candidate_dossier", False)
    for council in updates.get("council_records.jsonl", []):
        if not isinstance(council, dict):
            continue
        for field in ("debate_path", "fact_audit_path"):
            if council.get(field):
                add(council[field], field, False)
    return list(artifacts.values())


def _artifact_manifest(root: Path, result_path: str) -> list[dict[str, Any]]:
    result_file = (root / result_path).resolve()
    try:
        result_file.relative_to(root)
    except ValueError:
        return [{"path": result_path, "exists": False, "error": "path_outside_run_root"}]
    result = _read_json(result_file, {})
    entries: list[dict[str, Any]] = []
    for artifact in _artifact_paths(result):
        value = str(artifact["path"])
        supplied = Path(value)
        resolved = (supplied if supplied.is_absolute() else root / supplied).resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            entries.append({"path": value, "exists": False, "error": "path_outside_run_root"})
            continue
        exists = resolved.is_file()
        entries.append(
            {
                "path": str(relative),
                "resolved_path": str(resolved),
                "kinds": artifact["kinds"],
                "immutable": artifact["immutable"],
                "exists": exists,
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest() if exists else "",
                "bytes": resolved.stat().st_size if exists else 0,
            }
        )
    return entries


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "_" for char in value)


def _index(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in rows if str(row.get(key, "")).strip()}


def _compact_source(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in SOURCE_PACKET_FIELDS if field in row}


def _parent_subtopics(
    selected: set[str], subtopics: dict[str, dict[str, Any]]
) -> set[str]:
    result = set(selected)
    for subtopic_id in list(selected):
        cursor = str(subtopics.get(subtopic_id, {}).get("parent_id", ""))
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            result.add(cursor)
            cursor = str(subtopics.get(cursor, {}).get("parent_id", ""))
    return result


def _repair_ids(errors: list[str]) -> dict[str, set[str]]:
    labels = {
        "query": "queries",
        "source": "sources",
        "claim": "claims",
        "edge": "edges",
        "unit": "units",
        "candidate": "candidates",
        "council": "candidates",
        "subtopic": "subtopics",
    }
    found = {value: set() for value in labels.values()}
    for error in errors:
        for label, bucket in labels.items():
            for match in re.finditer(rf"\b{label}\s+([A-Za-z0-9_.:-]+)", error, re.IGNORECASE):
                found[bucket].add(match.group(1).rstrip(":.,;"))
    return found


def _select_context(root: Path, job: dict[str, Any]) -> dict[str, Any]:
    units = _index(_read_jsonl(root / "research_units.jsonl"), "unit_id")
    subtopics = _index(_read_jsonl(root / "subtopic_registry.jsonl"), "subtopic_id")
    claims = _read_jsonl(root / "claim_ledger.jsonl")
    edges = _read_jsonl(root / "evidence_graph.jsonl")
    sources = _index(_read_jsonl(root / "source_corpus.jsonl"), "source_id")
    candidates = _index(_read_jsonl(root / "candidate_records.jsonl"), "candidate_id")
    exchanges = _read_jsonl(root / "council_exchanges.jsonl")
    searches = _read_jsonl(root / "search_log.jsonl")
    audits = _read_jsonl(root / "unit_audits.jsonl")
    councils = _read_jsonl(root / "council_records.jsonl")

    validation_errors_path = str(job.get("validation_errors_path", ""))
    validation_errors = (
        _read_json(root / validation_errors_path, {}).get("errors", [])
        if validation_errors_path
        else []
    )
    repair_ids = _repair_ids(validation_errors)
    if str(job.get("context_scope", "")) == "final_validation":
        repair_ids["queries"].update(
            str(row.get("query_id"))
            for row in searches
            if str(row.get("research_unit_id")) in repair_ids["units"]
        )
        repair_ids["units"].update(
            str(row.get("research_unit_id"))
            for row in searches
            if str(row.get("query_id")) in repair_ids["queries"]
        )
        repair_ids["claims"].update(
            str(value)
            for row in candidates.values()
            if str(row.get("candidate_id")) in repair_ids["candidates"]
            for path in row.get("causal_paths", [])
            if isinstance(path, dict)
            for value in path.get("claim_ids", [])
        )
        repair_ids["edges"].update(
            str(value)
            for row in candidates.values()
            if str(row.get("candidate_id")) in repair_ids["candidates"]
            for path in row.get("causal_paths", [])
            if isinstance(path, dict)
            for value in path.get("edge_ids", [])
        )
        repair_ids["sources"].update(
            str(value)
            for row in claims
            if str(row.get("claim_id")) in repair_ids["claims"]
            for value in row.get("source_ids", [])
        )
        for row in searches:
            if str(row.get("query_id")) in repair_ids["queries"]:
                repair_ids["sources"].update(
                    str(value)
                    for field in ("acquired_source_ids", "original_verified_source_ids", "retained_source_ids")
                    for value in row.get(field, [])
                )

    unit = units.get(str(job.get("unit_id", "")), {})
    scope = str(job.get("context_scope", "case_only"))
    selected_subtopics = {
        str(value)
        for value in job.get("context_subtopic_ids", [])
        if str(value).strip()
    }
    unit_subtopic = str(unit.get("subtopic_id", ""))
    if unit_subtopic:
        selected_subtopics.add(unit_subtopic)
    selected_subtopics = _parent_subtopics(selected_subtopics, subtopics)

    candidate_id = str(job.get("candidate_id", ""))
    candidate = candidates.get(candidate_id)
    selected_claim_ids = {
        str(value)
        for value in job.get("context_claim_ids", [])
        if str(value).strip()
    }
    if candidate:
        selected_claim_ids.update(
            str(value)
            for path in candidate.get("causal_paths", [])
            if isinstance(path, dict)
            for value in path.get("claim_ids", [])
        )

    if scope == "final_validation":
        selected_claims = [
            row for row in claims if str(row.get("claim_id")) in repair_ids["claims"]
        ]
    elif scope in {"case_evidence", "closure"}:
        selected_claims = [row for row in claims if row.get("audit_status") in {"verified", "audited_complete"}]
    else:
        selected_claims = [
            row
            for row in claims
            if str(row.get("claim_id")) in selected_claim_ids
            or str(row.get("subtopic_id")) in selected_subtopics
        ]
    selected_claim_ids.update(str(row.get("claim_id")) for row in selected_claims)

    selected_edges = [row for row in edges if str(row.get("edge_id")) in repair_ids["edges"]] if scope == "final_validation" else [
        row
        for row in edges
        if row.get("audit_status") in {"verified", "audited_complete"}
        and selected_claim_ids.intersection(str(value) for value in row.get("claim_ids", []))
    ]
    selected_source_ids = {
        str(value)
        for claim in selected_claims
        for value in claim.get("source_ids", [])
    }
    selected_sources = [
        (sources[source_id] if scope == "final_validation" else _compact_source(sources[source_id]))
        for source_id in sorted(repair_ids["sources"] if scope == "final_validation" else selected_source_ids)
        if source_id in sources
    ]

    dependency_results: list[dict[str, str]] = []
    plan = _read_json(root / "execution_plan.json", {})
    jobs = _index(plan.get("jobs", []), "job_id")
    dependency_ids = (
        [str(value) for value in job.get("depends_on", [])]
        if job.get("include_dependency_results") is True
        else []
    )
    if candidate_id:
        dependency_ids.extend(
            str(row.get("job_id"))
            for row in plan.get("jobs", [])
            if row.get("candidate_id") == candidate_id
            and int(row.get("sequence", 0)) < int(job.get("sequence", 0))
            and row.get("status") == "complete"
        )
    def add_dependency_result(
        dependency_id: str, result_path: str, result_hash: str, purpose: str = ""
    ) -> None:
        if not result_path or any(entry["result_path"] == result_path for entry in dependency_results):
            return
        entry: dict[str, Any] = {
            "job_id": dependency_id,
            "result_path": result_path,
            "resolved_result_path": str((root / result_path).resolve()),
            "result_hash": result_hash,
            "path_base": "run_root",
            "artifact_manifest": _artifact_manifest(root, result_path),
        }
        if purpose:
            entry["purpose"] = purpose
        dependency_results.append(entry)

    for dependency_id in dict.fromkeys(dependency_ids):
        dependency = jobs.get(dependency_id, {})
        result_path = str(dependency.get("result_path", ""))
        if result_path:
            for repair in dependency.get("repair_context_paths", []):
                if isinstance(repair, dict):
                    add_dependency_result(
                        str(repair.get("job_id", dependency_id)),
                        str(repair.get("result_path", "")),
                        str(repair.get("result_hash", "")),
                        str(repair.get("purpose", "prior_repair_context")),
                    )
            add_dependency_result(str(dependency_id), result_path, str(dependency.get("result_hash", "")))

    for repair in job.get("repair_context_paths", []):
        if not isinstance(repair, dict) or not repair.get("result_path"):
            continue
        add_dependency_result(
            str(repair.get("job_id", "repair_feedback")),
            str(repair["result_path"]),
            str(repair.get("result_hash", "")),
            str(repair.get("purpose", "mandatory_repair_feedback")),
        )

    prior_exchanges = []
    if candidate_id:
        prior_exchanges = [
            row for row in exchanges if str(row.get("candidate_id")) == candidate_id
        ]

    final_validation_snapshot: list[dict[str, Any]] = []
    if scope == "final_validation":
        final_validation_snapshot.append(
            {"kind": "target_ids", "value": {key: sorted(value) for key, value in repair_ids.items()}}
        )
        slices = {
            "search_log.jsonl": [row for row in searches if str(row.get("query_id")) in repair_ids["queries"]],
            "subtopic_registry.jsonl": [row for key, row in subtopics.items() if key in repair_ids["subtopics"]],
            "research_units.jsonl": [row for key, row in units.items() if key in repair_ids["units"]],
            "unit_audits.jsonl": [row for row in audits if str(row.get("unit_id")) in repair_ids["units"]],
            "candidate_records.jsonl": [row for key, row in candidates.items() if key in repair_ids["candidates"]],
            "council_records.jsonl": [row for row in councils if str(row.get("candidate_id")) in repair_ids["candidates"]],
            "council_exchanges.jsonl": [row for row in exchanges if str(row.get("candidate_id")) in repair_ids["candidates"]],
        }
        final_validation_snapshot.extend(
            {"kind": "ledger_record", "ledger": ledger, "record": row}
            for ledger, rows in slices.items()
            for row in rows
        )

    return {
        "unit": unit,
        "subtopics": [subtopics[value] for value in sorted(selected_subtopics) if value in subtopics],
        "claims": selected_claims,
        "edges": selected_edges,
        "sources": selected_sources,
        "candidate": candidate or {},
        "prior_council_exchanges": prior_exchanges,
        "dependency_results": dependency_results,
        "final_validation_errors": validation_errors,
        "final_validation_snapshot": final_validation_snapshot,
        "selection_stats": {
            "all_subtopics": len(subtopics),
            "selected_subtopics": len(selected_subtopics),
            "all_claims": len(claims),
            "selected_claims": len(selected_claims),
            "all_sources": len(sources),
            "selected_sources": len(selected_sources),
        },
    }


def _chunk_context(context: dict[str, Any], max_chars: int) -> list[dict[str, Any]]:
    """Split every top-level collection without repeating the full snapshot."""
    static = {key: value for key, value in context.items() if not isinstance(value, list)}
    lists = {key: list(value) for key, value in context.items() if isinstance(value, list)}
    empty_lists = {key: [] for key in lists}
    first_base = {**static, **empty_lists, "context_part": 1}
    if len(_canonical_bytes(first_base)) > max_chars:
        raise ValueError("Packet static context exceeds the hard per-chunk limit; narrow the controller selection")
    atoms = [(key, item) for key, values in lists.items() for item in values]
    if not atoms:
        return [first_base]
    chunks: list[dict[str, Any]] = []
    current = first_base
    for key, item in atoms:
        proposed = {**current, key: [*current.get(key, []), item]}
        if len(_canonical_bytes(proposed)) > max_chars:
            if current == first_base:
                raise ValueError(f"One {key} record exceeds the hard per-chunk packet limit")
            chunks.append(current)
            current = {**{name: ({} if isinstance(value, dict) else value) for name, value in static.items()}, **empty_lists}
            current["context_part"] = len(chunks) + 1
            current[key] = [item]
            if len(_canonical_bytes(current)) > max_chars:
                raise ValueError(f"One {key} record exceeds the hard per-chunk packet limit")
        else:
            current = proposed
    chunks.append(current)
    return chunks


def build_packet(run_folder: str | Path, job_id: str, max_chars: int = 60000) -> tuple[Path, str]:
    root = Path(run_folder).expanduser().resolve()
    case = _read_json(root / "case.json", {})
    plan = _read_json(root / "execution_plan.json", {})
    jobs = _index(plan.get("jobs", []), "job_id")
    job = jobs.get(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")

    context = _select_context(root, job)
    units = _index(_read_jsonl(root / "research_units.jsonl"), "unit_id")
    unit = units.get(str(job.get("unit_id", "")))
    base = {
        "schema_version": SCHEMA_VERSION,
        "run_root": str(root),
        "path_contract": {
            "relative_paths_resolve_against": "run_root",
            "never_resolve_against": ["current_working_directory", "packet_directory", "result_directory", "staging_directory"],
            "missing_artifact_rule": (
                "Resolve the path against run_root and check the dependency artifact_manifest before reporting it missing. "
                "A staging directory is not the path base unless its run-root-relative prefix is explicitly present."
            ),
        },
        "job_id": job_id,
        "job_kind": job.get("kind"),
        "role": job.get("role"),
        "stage": job.get("stage"),
        "unit_id": job.get("unit_id"),
        "candidate_id": job.get("candidate_id"),
        "perspective": job.get("perspective"),
        "question": job.get("question"),
        "completion_contract": job.get("completion_contract"),
        "machine_contract": role_contract(job, unit),
        "case": case,
    }
    base_bytes = len(_canonical_bytes(base))
    if base_bytes + 1024 >= max_chars:
        raise ValueError("Role contract leaves no room inside the hard packet limit")
    chunks = _chunk_context(context, max_chars=max_chars - base_bytes - 1024)
    packet_dir = root / "packets" / _safe_name(job_id)
    packet_dir.mkdir(parents=True, exist_ok=True)
    chunk_entries: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, 1):
        packet = {**base, "chunk_index": index, "chunk_count": len(chunks), "context": chunk}
        path = packet_dir / f"input_{index:03d}.json"
        payload = _canonical_bytes(packet)
        if len(payload) > max_chars:
            raise ValueError(f"Packet chunk {index} exceeds hard limit: {len(payload)} > {max_chars}")
        path.write_bytes(payload)
        chunk_entries.append(
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
        "required_chunks": chunk_entries,
        "all_chunks_required": True,
        "silent_truncation_permitted": False,
        "max_chunk_bytes": max_chars,
        "total_packet_bytes": sum(entry["bytes"] for entry in chunk_entries),
    }
    manifest_hash = _hash(manifest)
    manifest["packet_hash"] = manifest_hash
    manifest_path = packet_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    return manifest_path, manifest_hash


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder")
    parser.add_argument("job_id")
    parser.add_argument("--max-chars", type=int, default=60000)
    args = parser.parse_args()
    path, packet_hash = build_packet(args.run_folder, args.job_id, args.max_chars)
    print(json.dumps({"manifest_path": str(path), "packet_hash": packet_hash}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
