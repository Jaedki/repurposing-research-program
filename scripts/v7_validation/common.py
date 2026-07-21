"""Shared helpers for focused schema-v7 structural validators."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_output_contract import OutputStatus, SCHEMA_VERSION, canonical_sha256


COLLECTION_ALIASES: Mapping[str, tuple[str, ...]] = {
    "candidate_seeds": ("candidate_seeds", "seeds"),
    "seed_aliases": ("seed_aliases", "aliases"),
    "seed_candidate_mappings": ("seed_candidate_mappings", "candidate_links"),
    "quarantined_seeds": ("quarantined_seeds", "unresolved_or_quarantined_seeds"),
    "decision_profiles": ("decision_profiles", "candidate_decision_profiles"),
    "portfolio_rank_records": ("portfolio_rank_records", "portfolio_records"),
    "portfolio_selections": ("portfolio_selections", "portfolio_selection"),
}


@dataclass(frozen=True)
class ValidationIssue:
    domain: str
    code: str
    message: str

    def render(self) -> str:
        return f"[{self.domain}:{self.code}] {self.message}"


def issue(domain: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(domain, code, message)


def plain(value: Any) -> Any:
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(item) for item in value]
    return value


def normalize_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = plain(value)
    if not isinstance(snapshot, dict):
        raise ValueError("schema-v7 committed snapshot must be an object")
    collections = snapshot.get("collections", {})
    if isinstance(collections, Mapping):
        for name, rows_value in collections.items():
            snapshot.setdefault(str(name), rows_value)
    snapshot.setdefault("schema_version", SCHEMA_VERSION)
    snapshot.setdefault("output_status", OutputStatus.DIAGNOSTIC_PARTIAL.value)
    return snapshot


def rows(snapshot: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    aliases = COLLECTION_ALIASES.get(name, (name,))
    value: Any = None
    for alias in aliases:
        if alias in snapshot:
            value = snapshot[alias]
            break
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "records" in value and isinstance(value["records"], (list, tuple)):
            value = value["records"]
        elif name == "portfolio_selections" and "selection_id" in value:
            value = [value]
        else:
            value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def first(snapshot: Mapping[str, Any], name: str) -> dict[str, Any]:
    values = rows(snapshot, name)
    return values[0] if values else {}


def enum_value(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value or "")


def string_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if str(item))


def index(values: Iterable[Mapping[str, Any]], *fields: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in values:
        row = dict(raw)
        identity = next((str(row.get(field, "")) for field in fields if row.get(field)), "")
        if identity:
            result[identity] = row
    return result


def duplicate_ids(values: Iterable[Mapping[str, Any]], *fields: str) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in values:
        identity = next((str(row.get(field, "")) for field in fields if row.get(field)), "")
        if identity in seen:
            duplicates.add(identity)
        elif identity:
            seen.add(identity)
    return duplicates


def snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    normalized = normalize_snapshot(snapshot)
    raw_collections = normalized.get("collections")
    if isinstance(raw_collections, Mapping):
        collections = {
            str(key): value
            for key, value in raw_collections.items()
            if key != "output_manifests"
        }
    else:
        collections = {
            key: value
            for key, value in normalized.items()
            if isinstance(value, (list, tuple)) and key != "output_manifests"
        }
    projection = {
        "schema_version": normalized.get("schema_version"),
        "output_status": normalized.get("output_status"),
        "case_revision": normalized.get("case_revision", {}),
        "collections": collections,
    }
    return canonical_sha256(projection)


def load_committed_snapshot(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case_path = run_root / "case_revision.json"
    index_path = run_root / "runtime_v7" / "canonical_index.json"
    if not case_path.is_file() or not index_path.is_file():
        raise ValueError("native schema-v7 case_revision.json and runtime_v7/canonical_index.json are required")
    case_revision = json.loads(case_path.read_text(encoding="utf-8-sig"))
    canonical_index = json.loads(index_path.read_text(encoding="utf-8-sig"))
    collections: dict[str, list[dict[str, Any]]] = {}
    for collection, refs in sorted(canonical_index.get("collections", {}).items()):
        if not isinstance(refs, Mapping):
            raise ValueError(f"canonical collection {collection!r} is malformed")
        collection_rows: list[dict[str, Any]] = []
        for record_id, ref in sorted(refs.items()):
            if not isinstance(ref, Mapping):
                raise ValueError(f"canonical record reference is malformed: {collection}:{record_id}")
            path = (run_root / str(ref.get("path", ""))).resolve()
            if run_root not in path.parents or not path.is_file():
                raise ValueError(f"canonical record path is invalid: {collection}:{record_id}")
            payload = path.read_bytes()
            record = json.loads(payload.decode("utf-8-sig"))
            if hashlib.sha256(payload).hexdigest().upper() != str(ref.get("sha256", "")).upper():
                raise ValueError(f"canonical record hash mismatch: {collection}:{record_id}")
            collection_rows.append(record)
        collections[str(collection)] = collection_rows
    state_path = run_root / "runtime_v7" / "runtime_state.json"
    plan_path = run_root / "runtime_v7" / "execution_plan.json"
    attempts_path = run_root / "runtime_v7" / "attempts.json"
    runtime_state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.is_file() else {}
    execution_plan = json.loads(plan_path.read_text(encoding="utf-8-sig")) if plan_path.is_file() else {}
    jobs = execution_plan.get("jobs", []) if isinstance(execution_plan, Mapping) else []
    upstream_jobs = [
        row for row in jobs
        if isinstance(row, Mapping) and row.get("stage") != "final_outputs" and row.get("required") is True
    ]
    derived_complete = bool(upstream_jobs) and all(row.get("status") == "committed" for row in upstream_jobs)
    derived_complete = derived_complete and not runtime_state.get("acceptance_blockers")
    event_rows = []
    events_root = run_root / "runtime_v7" / "events"
    if events_root.is_dir():
        event_rows = [json.loads(path.read_text(encoding="utf-8-sig")) for path in sorted(events_root.glob("*.json"))]
    attempts = json.loads(attempts_path.read_text(encoding="utf-8-sig")) if attempts_path.is_file() else []
    commit_rows = []
    commits_root = run_root / "runtime_v7" / "commits"
    if commits_root.is_dir():
        for path in sorted(commits_root.glob("*.json")):
            commit = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(commit, Mapping):
                commit_rows.append(
                    {
                        "commit_id": str(commit.get("commit_id", "")),
                        "scientific_hash": str(commit.get("scientific_hash", "")),
                        "job_id": str(commit.get("job_id", "")),
                        "stage": str(commit.get("stage", "")),
                        "dependency_commit_ids": sorted(
                            str(row.get("commit_id"))
                            for row in commit.get("dependency_commits", [])
                            if isinstance(row, Mapping) and row.get("commit_id")
                        ),
                    }
                )
    indexed_commit_ids = {
        str(ref.get("origin_commit_id"))
        for refs in canonical_index.get("collections", {}).values()
        if isinstance(refs, Mapping)
        for ref in refs.values()
        if isinstance(ref, Mapping) and ref.get("origin_commit_id")
    }
    provenance = {
        "canonical_scientific_hash": str(canonical_index.get("scientific_hash", "")),
        "execution_hash": canonical_sha256({"attempts": attempts, "events": event_rows}),
        "commit_ids": sorted(indexed_commit_ids | {row["commit_id"] for row in commit_rows if row["commit_id"]}),
        "commits": commit_rows,
        "canonical_index_sha256": canonical_sha256(canonical_index),
    }
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": str(canonical_index.get("scientific_hash", "")),
        "output_status": OutputStatus.COMPLETE.value if derived_complete else OutputStatus.DIAGNOSTIC_PARTIAL.value,
        "case_revision": case_revision,
        "collections": collections,
        "provenance": provenance,
    }
    return normalize_snapshot(snapshot)


def count_by(values: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in values:
        value = enum_value(row.get(field))
        result[value] = result.get(value, 0) + 1
    return result


__all__ = [
    "ValidationIssue",
    "count_by",
    "duplicate_ids",
    "enum_value",
    "first",
    "index",
    "issue",
    "load_committed_snapshot",
    "normalize_snapshot",
    "plain",
    "rows",
    "snapshot_sha256",
    "string_ids",
]
