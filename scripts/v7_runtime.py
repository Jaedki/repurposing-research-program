#!/usr/bin/env python3
"""Deterministic concurrent schema-v7 DAG runtime and canonical commit store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_packets import (
    COLLECTION_ID_FIELDS,
    ROLE_CONTRACTS,
    build_packet,
    build_task_packets,
    canonical_bytes,
    canonical_sha256,
    deterministic_shard_key,
    role_contract,
    shard_record_refs,
    verify_packet,
    write_role_contracts,
)


SCHEMA_VERSION = 7
RUNTIME_MODEL_VERSION = "schema-v7-runtime-dag-v1"
RUNTIME_DIRECTORY = "runtime_v7"

JOB_STATUSES = {
    "planned",
    "ready",
    "running",
    "retry_wait",
    "committed",
    "failed",
    "budget_deferred",
    "blocked",
}
TERMINAL_JOB_STATUSES = {"committed", "failed", "budget_deferred", "blocked"}
ATTEMPT_STATUSES = {"running", "validated", "committed", "failed", "orphaned"}

USAGE_FIELDS = (
    "source_records",
    "seeds",
    "deep_reviews",
    "audits",
    "elapsed_seconds",
    "cost_units",
)
BUDGET_FIELDS = (
    "source_budget",
    "seed_budget",
    "deep_review_budget",
    "audit_budget",
    "time_budget_seconds",
    "cost_budget_units",
)

BREADTH_PROFILES: Mapping[str, Mapping[str, int]] = {
    "broad_discovery": {
        "max_active_jobs": 12,
        "source_budget": 5000,
        "seed_budget": 10000,
        "deep_review_budget": 250,
        "audit_budget": 150,
        "time_budget_seconds": 86400,
        "cost_budget_units": 2000,
    },
    "balanced": {
        "max_active_jobs": 8,
        "source_budget": 2500,
        "seed_budget": 5000,
        "deep_review_budget": 125,
        "audit_budget": 75,
        "time_budget_seconds": 43200,
        "cost_budget_units": 1000,
    },
    "clinical_shortlist": {
        "max_active_jobs": 4,
        "source_budget": 1000,
        "seed_budget": 1500,
        "deep_review_budget": 40,
        "audit_budget": 30,
        "time_budget_seconds": 21600,
        "cost_budget_units": 400,
    },
}

_CONFIG_DEFAULTS: Mapping[str, Any] = {
    "breadth_mode": "balanced",
    "max_source_records_per_shard": 100,
    "max_candidate_records_per_shard": 100,
    "max_shard_source_bytes": 262144,
    "max_packet_bytes": 65536,
    "max_fan_in_dependencies": 16,
    "retry_limit": 4,
    "retry_base_seconds": 30,
    "retry_delay_cap_seconds": 900,
}

_STAGE_ROLE = {
    "case_model": "case_model_constructor",
    "source_universe_planning": "source_universe_planner",
    "discovery_source_shards": "discovery_source_worker",
    "identity_shards": "identity_worker",
    "preliminary_triage": "preliminary_triage_worker",
    "deep_evidence_packages": "deep_evidence_worker",
    "ranking_preparation": "ranking_preparation_worker",
    "audit_sampling": "audit_sampling_worker",
    "candidate_audit_shards": "candidate_auditor",
    "council_portfolio_review": "council_portfolio_reviewer",
    "final_validation": "final_structural_validator",
    "final_outputs": "final_output_builder",
}


class V7RuntimeError(ValueError):
    pass


class SimulatedInterruption(RuntimeError):
    """Raised only by an explicit test hook after an immutable commit is published."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.")
    if not cleaned:
        raise V7RuntimeError("identifier becomes empty after path normalization")
    return cleaned


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


class _RunLock(AbstractContextManager["_RunLock"]):
    """Cross-platform advisory file lock for every mutable runtime transition."""

    def __init__(self, path: Path, timeout_seconds: float = 20.0) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.handle: Any = None

    def __enter__(self) -> "_RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    if self.handle.tell() == 0:
                        self.handle.write(b"0")
                        self.handle.flush()
                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise V7RuntimeError("timed out acquiring the schema-v7 runtime lock")
                time.sleep(0.02)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _runtime_root(root: Path) -> Path:
    return root / RUNTIME_DIRECTORY


def _lock(root: Path) -> _RunLock:
    return _RunLock(_runtime_root(root) / "locks" / "run.lock")


def normalize_runtime_config(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    supplied = dict(value or {})
    allowed = {*_CONFIG_DEFAULTS, *BUDGET_FIELDS, "max_active_jobs"}
    unknown = set(supplied) - allowed
    if unknown:
        raise V7RuntimeError(f"unknown schema-v7 runtime config fields: {sorted(unknown)}")
    mode = str(supplied.get("breadth_mode", _CONFIG_DEFAULTS["breadth_mode"]))
    if mode not in BREADTH_PROFILES:
        raise V7RuntimeError(f"unknown breadth_mode: {mode}")
    result = {**_CONFIG_DEFAULTS, **BREADTH_PROFILES[mode], **supplied}
    result["breadth_mode"] = mode
    integer_fields = set(result) - {"breadth_mode"}
    for field in integer_fields:
        raw = result[field]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise V7RuntimeError(f"runtime config {field} must be a positive integer")
    if result["max_active_jobs"] > 128:
        raise V7RuntimeError("max_active_jobs cannot exceed 128")
    if result["max_packet_bytes"] < 4096:
        raise V7RuntimeError("max_packet_bytes must be at least 4096")
    if result["max_fan_in_dependencies"] < 2:
        raise V7RuntimeError("max_fan_in_dependencies must be at least 2")
    return result


def _job_id(
    case_revision_id: str,
    stage: str,
    role: str,
    shard_key: str,
    dependencies: Iterable[str],
) -> str:
    projection = {
        "case_revision_id": case_revision_id,
        "stage": stage,
        "role": role,
        "shard_key": shard_key,
        "dependencies": sorted(str(value) for value in dependencies),
        "runtime_model_version": RUNTIME_MODEL_VERSION,
    }
    return f"V7JOB-{_safe(stage).upper()}-{canonical_sha256(projection)[:20]}"


def _new_job(
    case_revision_id: str,
    *,
    stage: str,
    role: str,
    shard_key: str,
    dependencies: Iterable[str],
    input_refs: Iterable[Mapping[str, Any]] = (),
    required: bool = True,
    internal: bool = False,
    budget_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dependency_ids = sorted(set(str(value) for value in dependencies))
    refs = [dict(value) for value in input_refs]
    job_id = _job_id(case_revision_id, stage, role, shard_key, dependency_ids)
    return {
        "job_id": job_id,
        "stage": stage,
        "role": role,
        "shard_key": shard_key,
        "dependency_job_ids": dependency_ids,
        "input_refs": refs,
        "input_record_count": len(refs),
        "input_source_bytes": sum(int(ref.get("bytes", 0)) for ref in refs),
        "required": required,
        "internal": internal,
        "status": "planned",
        "packet_manifest_path": "",
        "packet_hash": "",
        "attempt_count": 0,
        "active_attempt_id": "",
        "retry_count": 0,
        "retry_not_before": "",
        "retry_delay_seconds": 0,
        "failure_kind": "",
        "failure_detail": "",
        "completion_state": "not_validated",
        "validated_result_path": "",
        "validated_result_sha256": "",
        "commit_id": "",
        "commit_path": "",
        "scientific_hash": "",
        "progress": {
            "processed_records": 0,
            "total_records": len(refs),
            "cursor": "",
            "checkpoint_ref": "",
        },
        "budget_snapshot": dict(budget_snapshot or {}),
        "result_path_template": (
            f"{RUNTIME_DIRECTORY}/staging/{_safe(job_id)}.attempt{{attempt_number:03d}}/result.json"
        ),
    }


def _load_runtime(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    runtime = _runtime_root(root)
    config = _read_json(runtime / "runtime_config.json", {})
    plan = _read_json(runtime / "execution_plan.json", {})
    state = _read_json(runtime / "runtime_state.json", {})
    attempts = _read_json(runtime / "attempts.json", [])
    if not all(isinstance(value, dict) for value in (config, plan, state)) or not isinstance(attempts, list):
        raise V7RuntimeError("schema-v7 runtime metadata is malformed")
    return config, plan, state, attempts


def _persist_runtime(
    root: Path,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    attempts: Iterable[Mapping[str, Any]],
) -> None:
    runtime = _runtime_root(root)
    _atomic_json(runtime / "execution_plan.json", plan)
    _atomic_json(runtime / "runtime_state.json", state)
    _atomic_json(runtime / "attempts.json", list(attempts))


def _job_map(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["job_id"]): row for row in plan.get("jobs", [])}


def _attempt_map(attempts: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["attempt_id"]): dict(row) for row in attempts}


def _event(root: Path, event: str, *, event_key: str, **details: Any) -> None:
    runtime = _runtime_root(root)
    event_id = f"V7EV-{canonical_sha256({'event': event, 'event_key': event_key})[:24]}"
    path = runtime / "events" / f"{event_id}.json"
    value = {
        "event_id": event_id,
        "event": event,
        "event_key": event_key,
        "at": _now(),
        **details,
    }
    if path.is_file():
        prior = _read_json(path, {})
        stable_prior = {key: item for key, item in prior.items() if key != "at"}
        stable_value = {key: item for key, item in value.items() if key != "at"}
        if stable_prior != stable_value:
            raise V7RuntimeError(f"execution-event conflict for {event_id}")
        return
    _atomic_json(path, value)


def _canonical_index(runtime: Path) -> dict[str, Any]:
    value = _read_json(
        runtime / "canonical_index.json",
        {"schema_version": SCHEMA_VERSION, "collections": {}, "scientific_hash": canonical_sha256({})},
    )
    if not isinstance(value, dict) or not isinstance(value.get("collections"), dict):
        raise V7RuntimeError("canonical index is malformed")
    return value


def _scientific_index_projection(index: Mapping[str, Any]) -> dict[str, Any]:
    return {
        collection: {
            record_id: str(ref["sha256"])
            for record_id, ref in sorted(records.items())
        }
        for collection, records in sorted(index.get("collections", {}).items())
    }


def _refresh_scientific_hash(index: dict[str, Any]) -> None:
    index["scientific_hash"] = canonical_sha256(_scientific_index_projection(index))


def _record_ref(index: Mapping[str, Any], collection: str, record_id: str) -> dict[str, Any] | None:
    raw = index.get("collections", {}).get(collection, {}).get(record_id)
    return dict(raw) if isinstance(raw, Mapping) else None


def _collection_refs(index: Mapping[str, Any], collections: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for collection in collections:
        rows = index.get("collections", {}).get(collection, {})
        if not isinstance(rows, Mapping):
            continue
        for record_id in sorted(rows):
            ref = dict(rows[record_id])
            ref["collection"] = collection
            ref["record_id"] = record_id
            result.append(ref)
    return result


def _case_revision(root: Path) -> dict[str, Any]:
    value = _read_json(root / "case_revision.json", {})
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise V7RuntimeError("case_revision.json is not a schema-v7 case revision")
    return value


def _case_manifest(root: Path) -> dict[str, Any]:
    value = _read_json(root / "schema_manifest.json", {})
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "schema_v7_native_case_container"
    ):
        raise V7RuntimeError("schema-v7 runtime requires a native schema-v7 case container")
    artifacts = value.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise V7RuntimeError("schema-v7 case manifest artifacts are malformed")
    for name, expected in artifacts.items():
        path = root / str(name)
        if not path.is_file() or _sha256_file(path) != str(expected).upper():
            raise V7RuntimeError(f"immutable case artifact changed: {name}")
    return value


def _commit_body(
    job: Mapping[str, Any],
    *,
    dependency_commits: Iterable[Mapping[str, Any]],
    records: Mapping[str, Iterable[Mapping[str, Any]]],
    progress: Mapping[str, Any],
    barrier_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    deps = [
        {
            "job_id": str(row["job_id"]),
            "commit_id": str(row["commit_id"]),
            "scientific_hash": str(row["scientific_hash"]),
        }
        for row in dependency_commits
    ]
    deps.sort(key=lambda row: row["job_id"])
    canonical_records = {
        name: sorted(
            (dict(value) for value in values),
            key=lambda row: str(row[COLLECTION_ID_FIELDS[name]]),
        )
        for name, values in sorted(records.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_model_version": RUNTIME_MODEL_VERSION,
        "job_id": str(job["job_id"]),
        "stage": str(job["stage"]),
        "shard_key": str(job["shard_key"]),
        "packet_hash": str(job.get("packet_hash", "")),
        "dependency_commits": deps,
        "records": canonical_records,
        "progress": dict(progress),
        "barrier_summary": dict(barrier_summary or {}),
    }


def _publish_commit_locked(
    root: Path,
    job: dict[str, Any],
    *,
    dependency_commits: Iterable[Mapping[str, Any]],
    records: Mapping[str, Iterable[Mapping[str, Any]]],
    progress: Mapping[str, Any],
    barrier_summary: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    runtime = _runtime_root(root)
    body = _commit_body(
        job,
        dependency_commits=dependency_commits,
        records=records,
        progress=progress,
        barrier_summary=barrier_summary,
    )
    scientific_hash = canonical_sha256(body)
    commit_id = f"V7COMMIT-{scientific_hash[:28]}"
    commit = {**body, "commit_id": commit_id, "scientific_hash": scientific_hash}
    path = runtime / "commits" / f"{commit_id}.json"
    created = not path.is_file()
    if path.is_file():
        if _read_json(path, {}) != commit:
            raise V7RuntimeError(f"commit ID collision: {commit_id}")
    else:
        _atomic_json(path, commit)

    index = _canonical_index(runtime)
    for collection, rows in body["records"].items():
        id_field = COLLECTION_ID_FIELDS[collection]
        target = index["collections"].setdefault(collection, {})
        for record in rows:
            record_id = str(record[id_field])
            payload = canonical_bytes(record)
            record_hash = hashlib.sha256(payload).hexdigest().upper()
            record_path = runtime / "records" / _safe(collection) / f"{record_hash}.json"
            if record_path.is_file() and record_path.read_bytes() != payload:
                raise V7RuntimeError(f"record content-address collision: {collection}:{record_id}")
            if not record_path.is_file():
                _atomic_json(record_path, record)
            ref = {
                "id_field": id_field,
                "sha256": record_hash,
                "bytes": len(payload),
                "path": record_path.relative_to(root).as_posix(),
                "origin_commit_id": commit_id,
            }
            prior = target.get(record_id)
            if prior and str(prior.get("sha256")) != record_hash:
                raise V7RuntimeError(
                    f"idempotency conflict for {collection}:{record_id}; last-writer-wins is prohibited"
                )
            target[record_id] = prior or ref
    _refresh_scientific_hash(index)
    _atomic_json(runtime / "canonical_index.json", index)
    job.update(
        status="committed",
        completion_state="committed",
        commit_id=commit_id,
        commit_path=path.relative_to(root).as_posix(),
        scientific_hash=scientific_hash,
        active_attempt_id="",
        progress=dict(progress),
    )
    return commit, created


def _reconcile_commit_records_locked(root: Path, commit: Mapping[str, Any]) -> None:
    """Finish an interrupted canonical-index publication from an immutable commit."""

    runtime = _runtime_root(root)
    index = _canonical_index(runtime)
    records_by_collection = commit.get("records", {})
    if not isinstance(records_by_collection, Mapping):
        raise V7RuntimeError("published commit records are malformed")
    for collection, raw_rows in records_by_collection.items():
        if collection not in COLLECTION_ID_FIELDS or not isinstance(raw_rows, list):
            raise V7RuntimeError("published commit collection is malformed")
        id_field = COLLECTION_ID_FIELDS[collection]
        target = index["collections"].setdefault(collection, {})
        for raw_record in raw_rows:
            if not isinstance(raw_record, Mapping):
                raise V7RuntimeError("published commit record is malformed")
            record = dict(raw_record)
            record_id = str(record.get(id_field, ""))
            if not record_id:
                raise V7RuntimeError("published commit record lacks its identity")
            payload = canonical_bytes(record)
            record_hash = hashlib.sha256(payload).hexdigest().upper()
            record_path = runtime / "records" / _safe(collection) / f"{record_hash}.json"
            if record_path.is_file() and record_path.read_bytes() != payload:
                raise V7RuntimeError(f"record content-address collision: {collection}:{record_id}")
            if not record_path.is_file():
                _atomic_json(record_path, record)
            prior = target.get(record_id)
            if prior and str(prior.get("sha256")) != record_hash:
                raise V7RuntimeError(
                    f"idempotency conflict for {collection}:{record_id}; last-writer-wins is prohibited"
                )
            target[record_id] = prior or {
                "id_field": id_field,
                "sha256": record_hash,
                "bytes": len(payload),
                "path": record_path.relative_to(root).as_posix(),
                "origin_commit_id": str(commit["commit_id"]),
            }
    _refresh_scientific_hash(index)
    _atomic_json(runtime / "canonical_index.json", index)


def _write_commit_execution_receipt(
    root: Path,
    *,
    commit_id: str,
    job_id: str,
    attempt_id: str,
    result_sha256: str,
    budget_usage: Mapping[str, Any],
) -> None:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "commit_id": commit_id,
        "job_id": job_id,
        "attempt_id": attempt_id,
        "result_sha256": result_sha256,
        "budget_usage": {field: int(budget_usage[field]) for field in USAGE_FIELDS},
    }
    path = _runtime_root(root) / "commit_receipts" / f"{commit_id}.json"
    if path.is_file() and _read_json(path, {}) != receipt:
        raise V7RuntimeError(f"commit execution-receipt conflict: {commit_id}")
    if not path.is_file():
        _atomic_json(path, receipt)


def _dependency_commits(root: Path, job: Mapping[str, Any], jobs: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for dependency_id in job.get("dependency_job_ids", []):
        dependency = jobs.get(str(dependency_id))
        if not dependency or dependency.get("status") != "committed":
            raise V7RuntimeError(f"job dependency is not committed: {dependency_id}")
        result.append(
            {
                "job_id": str(dependency_id),
                "commit_id": str(dependency["commit_id"]),
                "scientific_hash": str(dependency["scientific_hash"]),
                "path": str(dependency["commit_path"]),
            }
        )
    return result


def _append_job(plan: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    jobs = _job_map(plan)
    existing = jobs.get(job["job_id"])
    if existing:
        stable_fields = (
            "job_id",
            "stage",
            "role",
            "shard_key",
            "dependency_job_ids",
            "input_refs",
            "required",
            "internal",
        )
        if any(existing.get(field) != job.get(field) for field in stable_fields):
            raise V7RuntimeError(f"deterministic job conflict: {job['job_id']}")
        return existing
    plan.setdefault("jobs", []).append(job)
    plan["jobs"].sort(key=lambda row: (str(row["stage"]), str(row["job_id"])))
    return job


def _retry_elapsed(job: Mapping[str, Any]) -> bool:
    value = _parse_time(job.get("retry_not_before"))
    return value is None or value <= datetime.now(timezone.utc)


def _budget_exhaustion(config: Mapping[str, Any], usage: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "source": int(usage["source_records"]) >= int(config["source_budget"]),
        "seed": int(usage["seeds"]) >= int(config["seed_budget"]),
        "deep_review": int(usage["deep_reviews"]) >= int(config["deep_review_budget"]),
        "audit": int(usage["audits"]) >= int(config["audit_budget"]),
        "time": int(usage["elapsed_seconds"]) >= int(config["time_budget_seconds"]),
        "cost": int(usage["cost_units"]) >= int(config["cost_budget_units"]),
    }


def _stage_budget_reason(job: Mapping[str, Any], exhausted: Mapping[str, bool]) -> str:
    stage = str(job.get("stage", ""))
    if exhausted.get("time") or exhausted.get("cost"):
        if stage not in {"case_model", "source_universe_planning", "final_validation", "final_outputs"}:
            return "time_or_cost_budget_exhausted"
    if stage == "discovery_source_shards" and exhausted.get("source"):
        return "source_budget_exhausted"
    if stage == "deep_evidence_packages" and (
        exhausted.get("seed") or exhausted.get("deep_review")
    ):
        return "seed_or_deep_review_budget_exhausted"
    if stage in {"audit_sampling", "candidate_audit_shards"} and exhausted.get("audit"):
        return "audit_budget_exhausted"
    return ""


def _refresh_jobs(config: Mapping[str, Any], plan: dict[str, Any], state: dict[str, Any]) -> None:
    jobs = _job_map(plan)
    exhausted = _budget_exhaustion(config, state["budget_usage"])
    state["budget_exhausted"] = exhausted
    for job in plan.get("jobs", []):
        if job.get("status") == "retry_wait" and _retry_elapsed(job):
            job["status"] = "ready"
        if job.get("status") not in {"planned", "ready"}:
            continue
        dependencies = [jobs.get(str(value)) for value in job.get("dependency_job_ids", [])]
        if any(value is None for value in dependencies):
            job["status"] = "blocked"
            job["failure_kind"] = "missing_dependency"
            continue
        if any(value.get("status") in {"failed", "blocked"} and value.get("required") for value in dependencies):
            job["status"] = "blocked"
            job["failure_kind"] = "required_dependency_failed"
            continue
        if all(value.get("status") == "committed" for value in dependencies):
            reason = _stage_budget_reason(job, exhausted)
            if reason and not job.get("internal"):
                job["status"] = "budget_deferred"
                job["failure_kind"] = reason
                state.setdefault("budget_deferrals", []).append(
                    {
                        "job_id": job["job_id"],
                        "stage": job["stage"],
                        "reason": reason,
                        "input_record_ids": [str(ref["record_id"]) for ref in job.get("input_refs", [])],
                    }
                )
                blocker = f"{job['job_id']}:{reason}"
                if blocker not in state["acceptance_blockers"]:
                    state["acceptance_blockers"].append(blocker)
            else:
                job["status"] = "ready"
    state["budget_deferrals"] = sorted(
        {canonical_sha256(row): row for row in state.get("budget_deferrals", [])}.values(),
        key=lambda row: str(row["job_id"]),
    )


def _stage_jobs(plan: Mapping[str, Any], stage: str) -> list[dict[str, Any]]:
    return [row for row in plan.get("jobs", []) if row.get("stage") == stage]


def _stage_terminal(plan: Mapping[str, Any], stage: str) -> bool:
    rows = _stage_jobs(plan, stage)
    return bool(rows) and all(row.get("status") in TERMINAL_JOB_STATUSES for row in rows)


def _required_stage_failure(plan: Mapping[str, Any], stage: str) -> list[dict[str, Any]]:
    return [
        row for row in _stage_jobs(plan, stage)
        if row.get("required") and row.get("status") in {"failed", "blocked"}
    ]


def _commit_internal_job(root: Path, plan: dict[str, Any], job: dict[str, Any], summary: Mapping[str, Any]) -> None:
    jobs = _job_map(plan)
    dependencies = _dependency_commits(root, job, jobs)
    _publish_commit_locked(
        root,
        job,
        dependency_commits=dependencies,
        records={},
        progress={
            "processed_records": int(summary.get("input_record_count", 0)),
            "total_records": int(summary.get("input_record_count", 0)),
            "cursor": "",
            "checkpoint_ref": "",
        },
        barrier_summary=summary,
    )


def _barrier_tree(
    root: Path,
    config: Mapping[str, Any],
    plan: dict[str, Any],
    *,
    case_revision_id: str,
    barrier_stage: str,
    source_jobs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    source_rows = list(source_jobs)
    rows = sorted(
        (row for row in source_rows if row.get("status") == "committed"),
        key=lambda row: str(row["job_id"]),
    )
    failed_projection = [
        {
            "job_id": str(row["job_id"]),
            "status": str(row["status"]),
            "required": bool(row.get("required")),
            "failure_kind": str(row.get("failure_kind", "")),
        }
        for row in sorted(source_rows, key=lambda row: str(row["job_id"]))
        if row.get("status") != "committed"
    ]
    if not rows:
        upstream_ids = sorted({
            str(dependency_id)
            for row in source_rows
            for dependency_id in row.get("dependency_job_ids", [])
            if _job_map(plan).get(str(dependency_id), {}).get("status") == "committed"
        })
        if not upstream_ids:
            raise V7RuntimeError(f"cannot construct {barrier_stage} without a committed dependency")
        empty_leaf = _new_job(
            case_revision_id,
            stage=f"{barrier_stage}_empty_input",
            role="controller_empty_stage",
            shard_key=f"{barrier_stage}-empty-{canonical_sha256(upstream_ids)[:16]}",
            dependencies=upstream_ids,
            internal=True,
        )
        empty_leaf = _append_job(plan, empty_leaf)
        if empty_leaf["status"] != "committed":
            empty_leaf["status"] = "ready"
            _commit_internal_job(
                root,
                plan,
                empty_leaf,
                {
                    "barrier_stage": barrier_stage,
                    "reason": "all_stage_shards_failed_or_budget_deferred",
                    "failed_or_deferred_shards_sha256": canonical_sha256(failed_projection),
                    "input_record_count": 0,
                },
            )
        rows = [empty_leaf]
    max_fan_in = int(config["max_fan_in_dependencies"])
    level = 0
    while len(rows) > 1:
        level += 1
        next_rows: list[dict[str, Any]] = []
        for ordinal, offset in enumerate(range(0, len(rows), max_fan_in), 1):
            group = rows[offset : offset + max_fan_in]
            stage = f"{barrier_stage}_fanin_l{level}"
            shard_key = (
                f"{stage}-{ordinal:05d}-"
                f"{canonical_sha256([row['job_id'] for row in group])[:16]}"
            )
            job = _new_job(
                case_revision_id,
                stage=stage,
                role="controller_barrier",
                shard_key=shard_key,
                dependencies=[str(row["job_id"]) for row in group],
                internal=True,
            )
            job = _append_job(plan, job)
            if job["status"] != "committed":
                job["status"] = "ready"
                _commit_internal_job(
                    root,
                    plan,
                    job,
                    {
                        "barrier_stage": barrier_stage,
                        "level": level,
                        "input_commit_ids": [str(row["commit_id"]) for row in group],
                        "input_record_count": sum(int(row.get("input_record_count", 0)) for row in group),
                    },
                )
            next_rows.append(job)
        rows = next_rows
    final_dependency = rows[0]
    final_job = _new_job(
        case_revision_id,
        stage=barrier_stage,
        role="controller_barrier",
        shard_key=f"{barrier_stage}-root-{canonical_sha256(final_dependency['job_id'])[:16]}",
        dependencies=[str(final_dependency["job_id"])],
        internal=True,
    )
    final_job = _append_job(plan, final_job)
    if final_job["status"] != "committed":
        final_job["status"] = "ready"
        _commit_internal_job(
            root,
            plan,
            final_job,
            {
                "barrier_stage": barrier_stage,
                "input_commit_ids": [str(final_dependency["commit_id"])],
                "failed_or_deferred_shards_sha256": canonical_sha256(failed_projection),
                "failed_or_deferred_shard_count": len(failed_projection),
                "input_record_count": sum(int(row.get("input_record_count", 0)) for row in source_rows),
            },
        )
    return final_job


def _shard_refs_for_stage(
    config: Mapping[str, Any],
    refs: Iterable[Mapping[str, Any]],
    *,
    candidate_bound: bool,
) -> list[list[dict[str, Any]]]:
    return shard_record_refs(
        refs,
        max_records=int(
            config["max_candidate_records_per_shard"]
            if candidate_bound
            else config["max_source_records_per_shard"]
        ),
        max_source_bytes=int(config["max_shard_source_bytes"]),
        max_packet_bytes=max(1024, int(config["max_packet_bytes"]) // 2),
    )


def _create_sharded_jobs(
    config: Mapping[str, Any],
    plan: dict[str, Any],
    *,
    case_revision_id: str,
    stage: str,
    dependency_job_id: str,
    refs: Iterable[Mapping[str, Any]],
    candidate_bound: bool,
    required: bool = True,
) -> list[dict[str, Any]]:
    role = _STAGE_ROLE[stage]
    shards = _shard_refs_for_stage(config, refs, candidate_bound=candidate_bound)
    jobs: list[dict[str, Any]] = []
    for ordinal, shard in enumerate(shards, 1):
        key = deterministic_shard_key(stage=stage, role=role, ordinal=ordinal, record_refs=shard)
        job = _new_job(
            case_revision_id,
            stage=stage,
            role=role,
            shard_key=key,
            dependencies=[dependency_job_id],
            input_refs=shard,
            required=required,
        )
        jobs.append(_append_job(plan, job))
    return jobs


def _source_plan_refs(runtime: Path, index: Mapping[str, Any]) -> list[dict[str, Any]]:
    query_refs = _collection_refs(index, ("query_plans",))
    universe_ids = {
        ref["record_id"] for ref in _collection_refs(index, ("source_universes",))
    }
    for query_ref in query_refs:
        query_path = runtime.parent / str(query_ref["path"])
        query = _read_json(query_path, {})
        universe = query.get("source_universe", {}) if isinstance(query, dict) else {}
        universe_id = str(universe.get("source_universe_id", ""))
        if not universe_id or universe_id not in universe_ids:
            raise V7RuntimeError(
                f"query plan {query_ref['record_id']} does not link a committed source universe"
            )
    # QueryPlan embeds its immutable DeclaredSourceUniverse. Referencing the plan therefore keeps
    # one source/query branch atomic while the separately committed universe remains reconstructable.
    return query_refs


def _cap_refs(
    refs: list[dict[str, Any]],
    limit: int,
    state: dict[str, Any],
    *,
    stage: str,
    reason: str,
) -> list[dict[str, Any]]:
    if len(refs) <= limit:
        return refs
    retained = refs[:limit]
    deferred = refs[limit:]
    state.setdefault("budget_deferrals", []).append(
        {
            "job_id": f"stage-cap:{stage}",
            "stage": stage,
            "reason": reason,
            "input_record_ids": [str(ref["record_id"]) for ref in deferred],
        }
    )
    blocker = f"{stage}:{reason}:{len(deferred)}"
    if blocker not in state["acceptance_blockers"]:
        state["acceptance_blockers"].append(blocker)
    return retained


def _expand_after_stage(
    root: Path,
    config: Mapping[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
    *,
    source_stage: str,
    barrier_stage: str,
    target_stage: str | None,
    input_collections: Iterable[str],
    candidate_bound: bool,
) -> bool:
    expanded = plan.setdefault("expanded_stages", {})
    if barrier_stage in expanded or not _stage_terminal(plan, source_stage):
        return False
    failures = _required_stage_failure(plan, source_stage)
    if failures:
        state["status"] = "blocked"
        state["blocked_reason"] = f"required_shard_failure:{source_stage}"
        for row in failures:
            blocker = f"required_shard_failed:{row['job_id']}"
            if blocker not in state["acceptance_blockers"]:
                state["acceptance_blockers"].append(blocker)
        return False
    source_jobs = _stage_jobs(plan, source_stage)
    barrier = _barrier_tree(
        root,
        config,
        plan,
        case_revision_id=str(plan["case_revision_id"]),
        barrier_stage=barrier_stage,
        source_jobs=source_jobs,
    )
    expanded[barrier_stage] = barrier["job_id"]
    if target_stage:
        index = _canonical_index(_runtime_root(root))
        refs = _collection_refs(index, input_collections)
        if target_stage == "deep_evidence_packages":
            refs = _cap_refs(
                refs,
                int(config["deep_review_budget"]),
                state,
                stage=target_stage,
                reason="deep_review_budget_capacity",
            )
        if target_stage == "candidate_audit_shards":
            refs = _cap_refs(
                refs,
                int(config["audit_budget"]),
                state,
                stage=target_stage,
                reason="audit_budget_capacity",
            )
        jobs = _create_sharded_jobs(
            config,
            plan,
            case_revision_id=str(plan["case_revision_id"]),
            stage=target_stage,
            dependency_job_id=str(barrier["job_id"]),
            refs=refs,
            candidate_bound=candidate_bound,
        )
        if not jobs:
            if target_stage == "final_validation":
                validation_job = _new_job(
                    str(plan["case_revision_id"]),
                    stage=target_stage,
                    role=_STAGE_ROLE[target_stage],
                    shard_key="final-validation-committed-snapshot",
                    dependencies=[str(barrier["job_id"])],
                    input_refs=(),
                    required=True,
                )
                _append_job(plan, validation_job)
                return True
            empty = _new_job(
                str(plan["case_revision_id"]),
                stage=target_stage,
                role="controller_empty_stage",
                shard_key=f"{target_stage}-empty",
                dependencies=[str(barrier["job_id"])],
                input_refs=(),
                required=False,
                internal=True,
            )
            empty = _append_job(plan, empty)
            if empty["status"] != "committed":
                empty["status"] = "ready"
                _commit_internal_job(
                    root,
                    plan,
                    empty,
                    {
                        "empty_stage": target_stage,
                        "reason": "no_committed_input_records_or_budget_capacity",
                        "input_record_count": 0,
                    },
                )
    return True


def _advance_dag_locked(
    root: Path,
    config: Mapping[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
) -> None:
    """Expand deterministic stage shards and short fan-in barriers to a fixed point."""

    changed = True
    while changed:
        changed = False
        _refresh_jobs(config, plan, state)
        expanded = plan.setdefault("expanded_stages", {})
        if (
            "discovery_source_shards" not in expanded
            and _stage_terminal(plan, "source_universe_planning")
            and not _required_stage_failure(plan, "source_universe_planning")
        ):
            planner = _stage_jobs(plan, "source_universe_planning")[0]
            index = _canonical_index(_runtime_root(root))
            refs = _source_plan_refs(_runtime_root(root), index)
            jobs = _create_sharded_jobs(
                config,
                plan,
                case_revision_id=str(plan["case_revision_id"]),
                stage="discovery_source_shards",
                dependency_job_id=str(planner["job_id"]),
                refs=refs,
                candidate_bound=False,
            )
            if not jobs:
                empty = _new_job(
                    str(plan["case_revision_id"]),
                    stage="discovery_source_shards",
                    role="controller_empty_stage",
                    shard_key="discovery-source-shards-empty",
                    dependencies=[str(planner["job_id"])],
                    required=False,
                    internal=True,
                )
                empty = _append_job(plan, empty)
                if empty["status"] != "committed":
                    empty["status"] = "ready"
                    _commit_internal_job(
                        root,
                        plan,
                        empty,
                        {
                            "empty_stage": "discovery_source_shards",
                            "reason": "no_declared_query_plans",
                            "input_record_count": 0,
                        },
                    )
            expanded["discovery_source_shards"] = [row["job_id"] for row in jobs]
            changed = True
            continue

        transitions = (
            (
                "discovery_source_shards",
                "seed_union_reconciliation",
                "identity_shards",
                ("candidate_seeds",),
                True,
            ),
            (
                "identity_shards",
                "identity_fan_in",
                "preliminary_triage",
                ("normalized_interventions",),
                True,
            ),
            (
                "preliminary_triage",
                "preliminary_triage_fan_in",
                "deep_evidence_packages",
                ("screened_candidates",),
                True,
            ),
            (
                "deep_evidence_packages",
                "deep_evidence_fan_in",
                "ranking_preparation",
                ("deep_candidates",),
                True,
            ),
            (
                "ranking_preparation",
                "ranking_preparation_fan_in",
                "audit_sampling",
                ("ranking_preparation_records",),
                True,
            ),
            (
                "audit_sampling",
                "audit_sampling_fan_in",
                "candidate_audit_shards",
                ("audit_assignments",),
                True,
            ),
            (
                "candidate_audit_shards",
                "candidate_audit_fan_in",
                "council_portfolio_review",
                ("portfolio_review_items",),
                True,
            ),
            (
                "council_portfolio_review",
                "council_portfolio_fan_in",
                "final_validation",
                (),
                False,
            ),
        )
        for source_stage, barrier_stage, target_stage, collections, candidate_bound in transitions:
            if _expand_after_stage(
                root,
                config,
                plan,
                state,
                source_stage=source_stage,
                barrier_stage=barrier_stage,
                target_stage=target_stage,
                input_collections=collections,
                candidate_bound=candidate_bound,
            ):
                changed = True
                break
        if changed:
            continue
        if (
            "final_outputs" not in expanded
            and _stage_terminal(plan, "final_validation")
            and not _required_stage_failure(plan, "final_validation")
        ):
            validation_job = _stage_jobs(plan, "final_validation")[0]
            refs = _collection_refs(_canonical_index(_runtime_root(root)), ("validation_reports",))
            jobs = _create_sharded_jobs(
                config,
                plan,
                case_revision_id=str(plan["case_revision_id"]),
                stage="final_outputs",
                dependency_job_id=str(validation_job["job_id"]),
                refs=refs,
                candidate_bound=False,
            )
            expanded["final_outputs"] = [row["job_id"] for row in jobs]
            changed = True
            continue
    _refresh_jobs(config, plan, state)
    active = [row for row in plan.get("jobs", []) if row.get("status") == "running"]
    state["active_job_ids"] = sorted(str(row["job_id"]) for row in active)
    if _stage_terminal(plan, "final_outputs") and not _required_stage_failure(plan, "final_outputs"):
        state["status"] = "complete" if not state["acceptance_blockers"] else "diagnostic_partial"
    elif state.get("status") not in {"blocked", "complete", "diagnostic_partial"}:
        state["status"] = "running" if active else "ready"
    state["updated_at"] = _now()


def initialize_runtime(
    root: str | Path,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Initialize a native v7 runtime subtree without modifying case artifacts."""

    run_root = Path(root).expanduser().resolve()
    manifest = _case_manifest(run_root)
    case = _case_revision(run_root)
    runtime = _runtime_root(run_root)
    if runtime.exists():
        raise V7RuntimeError(f"schema-v7 runtime already exists: {runtime}")
    normalized_config = normalize_runtime_config(config)
    for directory in (
        "contracts",
        "case_views",
        "packets",
        "staging",
        "commits",
        "commit_receipts",
        "records",
        "events",
        "locks",
    ):
        (runtime / directory).mkdir(parents=True, exist_ok=True)
    _atomic_json(runtime / "runtime_config.json", normalized_config)
    contract_manifest = write_role_contracts(runtime)
    _atomic_json(runtime / "contract_manifest.json", contract_manifest)
    index = {
        "schema_version": SCHEMA_VERSION,
        "collections": {},
        "scientific_hash": canonical_sha256({}),
    }
    _atomic_json(runtime / "canonical_index.json", index)
    case_revision_id = str(case["case_revision_id"])
    normalization = _new_job(
        case_revision_id,
        stage="case_normalization",
        role="controller_case_normalization",
        shard_key=f"case-normalization-{case_revision_id}",
        dependencies=(),
        internal=True,
    )
    case_model = _new_job(
        case_revision_id,
        stage="case_model",
        role=_STAGE_ROLE["case_model"],
        shard_key=f"case-model-{case_revision_id}",
        dependencies=[normalization["job_id"]],
        budget_snapshot=normalized_config,
    )
    source_plan = _new_job(
        case_revision_id,
        stage="source_universe_planning",
        role=_STAGE_ROLE["source_universe_planning"],
        shard_key=f"source-plan-{case_revision_id}",
        dependencies=[case_model["job_id"]],
        budget_snapshot=normalized_config,
    )
    plan = {
        "schema_version": SCHEMA_VERSION,
        "runtime_model_version": RUNTIME_MODEL_VERSION,
        "case_revision_id": case_revision_id,
        "config_sha256": canonical_sha256(normalized_config),
        "jobs": [normalization, case_model, source_plan],
        "expanded_stages": {
            "case_normalization": normalization["job_id"],
            "case_model": [case_model["job_id"]],
            "source_universe_planning": [source_plan["job_id"]],
        },
    }
    state = {
        "schema_version": SCHEMA_VERSION,
        "runtime_model_version": RUNTIME_MODEL_VERSION,
        "case_revision_id": case_revision_id,
        "breadth_mode": normalized_config["breadth_mode"],
        "max_active_jobs": normalized_config["max_active_jobs"],
        "status": "blocked" if case.get("case_status") != "ready" else "ready",
        "blocked_reason": (
            "case_revision_needs_resolution" if case.get("case_status") != "ready" else ""
        ),
        "active_job_ids": [],
        "budget_usage": {field: 0 for field in USAGE_FIELDS},
        "budget_exhausted": {
            "source": False,
            "seed": False,
            "deep_review": False,
            "audit": False,
            "time": False,
            "cost": False,
        },
        "budget_deferrals": [],
        "applied_commit_usage_ids": [],
        "acceptance_blockers": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _atomic_json(runtime / "attempts.json", [])
    dependencies: list[dict[str, Any]] = []
    case_summary = {
        "case_manifest_sha256": _sha256_file(run_root / "schema_manifest.json"),
        "case_revision_sha256": _sha256_file(run_root / "case_revision.json"),
        "case_status": case.get("case_status"),
        "input_record_count": 1,
    }
    _publish_commit_locked(
        run_root,
        normalization,
        dependency_commits=dependencies,
        records={},
        progress={
            "processed_records": 1,
            "total_records": 1,
            "cursor": "",
            "checkpoint_ref": "case_revision.json",
        },
        barrier_summary=case_summary,
    )
    _refresh_jobs(normalized_config, plan, state)
    _persist_runtime(run_root, plan, state, [])
    _event(
        run_root,
        "runtime_initialized",
        event_key=case_revision_id,
        case_revision_id=case_revision_id,
        case_manifest_sha256=_sha256_file(run_root / "schema_manifest.json"),
        runtime_config_sha256=canonical_sha256(normalized_config),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "runtime_model_version": RUNTIME_MODEL_VERSION,
        "case_revision_id": case_revision_id,
        "case_status": manifest["case_status"],
        "runtime_status": state["status"],
        "breadth_mode": normalized_config["breadth_mode"],
        "max_active_jobs": normalized_config["max_active_jobs"],
        "runtime_path": RUNTIME_DIRECTORY,
    }


def is_v7_runtime(root: str | Path) -> bool:
    run_root = Path(root).expanduser().resolve()
    value = _read_json(_runtime_root(run_root) / "execution_plan.json", {})
    return (
        isinstance(value, dict)
        and value.get("schema_version") == SCHEMA_VERSION
        and value.get("runtime_model_version") == RUNTIME_MODEL_VERSION
    )


def _build_ready_packets_locked(
    root: Path,
    config: Mapping[str, Any],
    plan: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    if state.get("blocked_reason"):
        return []
    jobs = _job_map(plan)
    running = sum(row.get("status") == "running" for row in jobs.values())
    available = max(0, int(config["max_active_jobs"]) - running)
    ready = sorted(
        (row for row in jobs.values() if row.get("status") == "ready" and not row.get("internal")),
        key=lambda row: (str(row["stage"]), str(row["job_id"])),
    )[:available]
    case = _case_revision(root)
    actions: list[dict[str, Any]] = []
    for job in ready:
        if job.get("packet_manifest_path"):
            manifest_path = root / str(job["packet_manifest_path"])
            verify_packet(root, manifest_path, str(job["packet_hash"]))
        else:
            dependencies = _dependency_commits(root, job, jobs)
            manifest_path, packet_hash = build_packet(
                root,
                _runtime_root(root),
                job,
                case,
                dependencies,
                max_packet_bytes=int(config["max_packet_bytes"]),
            )
            job["packet_manifest_path"] = manifest_path.relative_to(root).as_posix()
            job["packet_hash"] = packet_hash
        attempt_number = int(job["attempt_count"]) + 1
        attempt_id = f"{_safe(str(job['job_id']))}.attempt{attempt_number:03d}"
        result_path = str(job["result_path_template"]).format(attempt_number=attempt_number)
        actions.append(
            {
                "job_id": job["job_id"],
                "stage": job["stage"],
                "role": job["role"],
                "shard_key": job["shard_key"],
                "attempt_id": attempt_id,
                "packet_manifest_path": job["packet_manifest_path"],
                "packet_hash": job["packet_hash"],
                "expected_result_path": result_path,
                "input_record_count": job["input_record_count"],
                "input_source_bytes": job["input_source_bytes"],
                "resume_from": dict(job.get("progress", {})),
                "spawn_prompt": (
                    f"Job ID: {job['job_id']}\n"
                    f"Packet manifest: {(root / job['packet_manifest_path']).resolve()}\n"
                    f"Expected result: {(root / result_path).resolve()}"
                ),
                "spawn_contract": {"fork_turns": "none", "prompt_lines": 3},
            }
        )
    return actions


def next_action(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        _case_manifest(run_root)
        config, plan, state, attempts = _load_runtime(run_root)
        _recover_published_commits_locked(run_root, plan, state, attempts)
        _advance_dag_locked(run_root, config, plan, state)
        actions = _build_ready_packets_locked(run_root, config, plan, state)
        _persist_runtime(run_root, plan, state, attempts)
        if state.get("blocked_reason"):
            return {
                "action": "blocked",
                "reason": state["blocked_reason"],
                "acceptance_blockers": state["acceptance_blockers"],
            }
        if actions:
            return {
                "action": "start_agents",
                "jobs": actions,
                "available_slots": len(actions),
                "max_active_jobs": config["max_active_jobs"],
            }
        running = [row for row in plan["jobs"] if row.get("status") == "running"]
        if running:
            return {
                "action": "await_active_jobs",
                "active_jobs": [
                    {
                        "job_id": row["job_id"],
                        "attempt_id": row["active_attempt_id"],
                        "progress": row["progress"],
                    }
                    for row in sorted(running, key=lambda value: str(value["job_id"]))
                ],
            }
        retrying = [row for row in plan["jobs"] if row.get("status") == "retry_wait"]
        if retrying:
            return {
                "action": "wait_for_retries",
                "jobs": [
                    {
                        "job_id": row["job_id"],
                        "retry_not_before": row["retry_not_before"],
                        "retry_count": row["retry_count"],
                    }
                    for row in sorted(retrying, key=lambda value: str(value["job_id"]))
                ],
            }
        if state["status"] in {"complete", "diagnostic_partial"}:
            return {
                "action": "complete",
                "status": state["status"],
                "acceptance_blockers": state["acceptance_blockers"],
            }
        return {"action": "blocked_by_dependencies", "status": state["status"]}


def start_job(root: str | Path, job_id: str, agent_id: str) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        _advance_dag_locked(run_root, config, plan, state)
        job = _job_map(plan).get(job_id)
        if not job:
            raise V7RuntimeError(f"unknown schema-v7 job: {job_id}")
        if job.get("status") == "running":
            attempt = next(
                (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
                None,
            )
            if attempt and attempt.get("agent_id") == agent_id:
                return {**attempt, "duplicate_start_prevented": True}
            raise V7RuntimeError(f"job is already running: {job_id}")
        if job.get("status") != "ready" or not job.get("packet_hash"):
            raise V7RuntimeError(f"job is not ready with an immutable packet: {job_id}")
        verify_packet(run_root, run_root / str(job["packet_manifest_path"]), str(job["packet_hash"]))
        job["attempt_count"] = int(job["attempt_count"]) + 1
        attempt_id = f"{_safe(job_id)}.attempt{job['attempt_count']:03d}"
        result_path = str(job["result_path_template"]).format(attempt_number=job["attempt_count"])
        (run_root / result_path).parent.mkdir(parents=True, exist_ok=True)
        attempt = {
            "attempt_id": attempt_id,
            "job_id": job_id,
            "agent_id": str(agent_id),
            "packet_hash": job["packet_hash"],
            "status": "running",
            "expected_result_path": result_path,
            "started_at": _now(),
            "finished_at": "",
            "failure_kind": "",
            "failure_detail": "",
            "progress": dict(job["progress"]),
            "progress_history": [],
            "result_sha256": "",
            "commit_id": "",
        }
        attempts.append(attempt)
        job["status"] = "running"
        job["active_attempt_id"] = attempt_id
        state["active_job_ids"] = sorted({*state["active_job_ids"], job_id})
        state["status"] = "running"
        _event(
            run_root,
            "job_started",
            event_key=attempt_id,
            job_id=job_id,
            attempt_id=attempt_id,
            agent_id=agent_id,
            packet_hash=job["packet_hash"],
        )
        _persist_runtime(run_root, plan, state, attempts)
        return attempt


def record_progress(
    root: str | Path,
    job_id: str,
    agent_id: str,
    *,
    processed_records: int,
    total_records: int,
    cursor: str = "",
    checkpoint_ref: str = "",
) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        job = _job_map(plan).get(job_id)
        if not job or job.get("status") != "running":
            raise V7RuntimeError(f"job is not running: {job_id}")
        attempt = next(
            (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
            None,
        )
        if not attempt or attempt.get("agent_id") != agent_id:
            raise V7RuntimeError("progress reporter does not own the active attempt")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (processed_records, total_records)):
            raise V7RuntimeError("progress counts must be nonnegative integers")
        if processed_records > total_records:
            raise V7RuntimeError("processed_records cannot exceed total_records")
        prior = attempt["progress"]
        if processed_records < int(prior.get("processed_records", 0)):
            raise V7RuntimeError("progress cannot move backwards within an attempt")
        progress = {
            "processed_records": processed_records,
            "total_records": total_records,
            "cursor": str(cursor),
            "checkpoint_ref": str(checkpoint_ref),
        }
        sequence = len(attempt["progress_history"]) + 1
        progress_id = f"V7PROGRESS-{canonical_sha256({'attempt_id': attempt['attempt_id'], 'sequence': sequence, 'progress': progress})[:24]}"
        history = {"progress_id": progress_id, "sequence": sequence, "at": _now(), **progress}
        attempt["progress"] = progress
        attempt["progress_history"].append(history)
        job["progress"] = progress
        _event(
            run_root,
            "job_progress",
            event_key=progress_id,
            job_id=job_id,
            attempt_id=attempt["attempt_id"],
            progress_id=progress_id,
            progress=progress,
        )
        _persist_runtime(run_root, plan, state, attempts)
        return history


def _validate_result_object(
    root: Path,
    job: Mapping[str, Any],
    attempt: Mapping[str, Any],
    result: Any,
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["result must be one JSON object"], {}
    contract = role_contract(str(job["role"]))
    allowed_fields = set(contract["result_top_level_fields"])
    if set(result) != allowed_fields:
        errors.append(
            "result top-level fields differ from the role contract: "
            f"missing={sorted(allowed_fields - set(result))}, unknown={sorted(set(result) - allowed_fields)}"
        )
    if result.get("schema_version") != SCHEMA_VERSION:
        errors.append("result schema_version must be 7")
    if str(result.get("job_id")) != str(job["job_id"]):
        errors.append("result job_id does not match")
    if str(result.get("attempt_id")) != str(attempt["attempt_id"]):
        errors.append("result attempt_id does not match")
    if str(result.get("packet_hash")) != str(job["packet_hash"]):
        errors.append("result packet_hash does not match")
    jobs = _job_map(_read_json(_runtime_root(root) / "execution_plan.json", {}))
    expected_dependencies = sorted(
        str(jobs[dependency_id]["commit_id"]) for dependency_id in job["dependency_job_ids"]
    )
    actual_dependencies = result.get("dependency_commit_ids")
    if not isinstance(actual_dependencies, list) or sorted(str(value) for value in actual_dependencies) != expected_dependencies:
        errors.append("result dependency_commit_ids do not match the packet dependencies")
    if result.get("outcome") != "completed" or result.get("shard_complete") is not True:
        errors.append("result must declare outcome=completed and shard_complete=true")
    records_value = result.get("records")
    records: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(records_value, dict):
        errors.append("records must be an object")
    else:
        unknown_collections = set(records_value) - set(contract["allowed_output_collections"])
        if unknown_collections:
            errors.append(f"role emitted unapproved collections: {sorted(unknown_collections)}")
        for required in contract["required_output_collections"]:
            if required not in records_value:
                errors.append(f"role result is missing required collection: {required}")
        for collection, raw_rows in records_value.items():
            if collection not in COLLECTION_ID_FIELDS:
                continue
            if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
                errors.append(f"{collection} must be a list of objects")
                continue
            id_field = COLLECTION_ID_FIELDS[collection]
            unique: dict[str, dict[str, Any]] = {}
            for position, row in enumerate(raw_rows, 1):
                record = dict(row)
                identity = str(record.get(id_field, "")).strip()
                if not identity:
                    errors.append(f"{collection} record {position} lacks {id_field}")
                    continue
                if identity in unique and unique[identity] != record:
                    errors.append(f"{collection} contains an idempotency conflict for {identity}")
                unique[identity] = record
            records[collection] = [unique[key] for key in sorted(unique)]
    progress = result.get("progress")
    if not isinstance(progress, dict) or set(progress) != {
        "processed_records",
        "total_records",
        "cursor",
        "checkpoint_ref",
    }:
        errors.append("progress fields differ from the runtime contract")
    else:
        processed = progress.get("processed_records")
        total = progress.get("total_records")
        if (
            isinstance(processed, bool)
            or not isinstance(processed, int)
            or isinstance(total, bool)
            or not isinstance(total, int)
            or processed < 0
            or total < 0
            or processed > total
        ):
            errors.append("progress counts are invalid")
    usage = result.get("budget_usage")
    if not isinstance(usage, dict) or set(usage) != set(USAGE_FIELDS):
        errors.append("budget_usage fields differ from the runtime contract")
    elif any(isinstance(usage[field], bool) or not isinstance(usage[field], int) or usage[field] < 0 for field in USAGE_FIELDS):
        errors.append("budget_usage values must be nonnegative integers")
    return errors, records


def validate_result(root: str | Path, job_id: str, result_path: str | None = None) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        job = _job_map(plan).get(job_id)
        if not job:
            raise V7RuntimeError(f"unknown schema-v7 job: {job_id}")
        if job.get("status") == "committed":
            return {"status": "valid", "job_id": job_id, "cached_validation": True}
        if job.get("status") != "running":
            raise V7RuntimeError(f"job is not running: {job_id}")
        attempt = next(
            (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
            None,
        )
        if not attempt:
            raise V7RuntimeError("active attempt is missing")
        relative = result_path or str(attempt["expected_result_path"])
        path = (run_root / relative).resolve()
        path.relative_to(run_root)
        result = _read_json(path, None)
        errors, _ = _validate_result_object(run_root, job, attempt, result)
        if errors:
            return {"status": "invalid", "job_id": job_id, "result_path": relative, "errors": errors}
        result_sha256 = _sha256_file(path)
        if job.get("completion_state") == "validated":
            if str(job.get("validated_result_sha256")) != result_sha256:
                raise V7RuntimeError("validated result changed after validation")
            return {"status": "valid", "job_id": job_id, "result_path": relative, "cached_validation": True}
        job["completion_state"] = "validated"
        job["validated_result_path"] = path.relative_to(run_root).as_posix()
        job["validated_result_sha256"] = result_sha256
        attempt["status"] = "validated"
        attempt["result_sha256"] = result_sha256
        _event(
            run_root,
            "result_validated",
            event_key=f"{attempt['attempt_id']}:{result_sha256}",
            job_id=job_id,
            attempt_id=attempt["attempt_id"],
            result_sha256=result_sha256,
        )
        _persist_runtime(run_root, plan, state, attempts)
        return {"status": "valid", "job_id": job_id, "result_path": relative, "cached_validation": False}


def _recover_published_commits_locked(
    root: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> list[str]:
    runtime = _runtime_root(root)
    recovered: list[str] = []
    jobs = _job_map(plan)
    for path in sorted((runtime / "commits").glob("V7COMMIT-*.json")):
        commit = _read_json(path, {})
        job = jobs.get(str(commit.get("job_id", "")))
        if not job or job.get("status") == "committed":
            continue
        if str(commit.get("packet_hash", "")) != str(job.get("packet_hash", "")):
            continue
        expected = canonical_sha256({key: value for key, value in commit.items() if key not in {"commit_id", "scientific_hash"}})
        if commit.get("scientific_hash") != expected or commit.get("commit_id") != f"V7COMMIT-{expected[:28]}":
            raise V7RuntimeError(f"published commit integrity failure: {path.name}")
        _reconcile_commit_records_locked(root, commit)
        recovering_attempt_id = str(job.get("active_attempt_id", ""))
        job.update(
            status="committed",
            completion_state="committed",
            commit_id=commit["commit_id"],
            commit_path=path.relative_to(root).as_posix(),
            scientific_hash=commit["scientific_hash"],
            active_attempt_id="",
            progress=commit["progress"],
        )
        for attempt in attempts:
            if attempt.get("job_id") == job["job_id"] and attempt.get("result_sha256") == job.get("validated_result_sha256"):
                attempt["status"] = "committed"
                attempt["commit_id"] = commit["commit_id"]
                attempt["finished_at"] = attempt.get("finished_at") or _now()
        recovered.append(str(job["job_id"]))
        receipt_path = runtime / "commit_receipts" / f"{commit['commit_id']}.json"
        if not receipt_path.is_file() and job.get("validated_result_path"):
            result = _read_json(root / str(job["validated_result_path"]), {})
            usage = result.get("budget_usage") if isinstance(result, Mapping) else None
            if isinstance(usage, Mapping) and set(usage) == set(USAGE_FIELDS):
                _write_commit_execution_receipt(
                    root,
                    commit_id=str(commit["commit_id"]),
                    job_id=str(job["job_id"]),
                    attempt_id=recovering_attempt_id,
                    result_sha256=str(job.get("validated_result_sha256", "")),
                    budget_usage=usage,
                )
        receipt = _read_json(receipt_path, {})
        applied = state.setdefault("applied_commit_usage_ids", [])
        if receipt and commit["commit_id"] not in applied:
            usage = receipt.get("budget_usage", {})
            if not isinstance(usage, Mapping) or set(usage) != set(USAGE_FIELDS):
                raise V7RuntimeError(f"commit execution receipt is malformed: {commit['commit_id']}")
            for field in USAGE_FIELDS:
                state["budget_usage"][field] = int(state["budget_usage"][field]) + int(usage[field])
            applied.append(commit["commit_id"])
            applied.sort()
        _event(
            root,
            "published_commit_recovered",
            event_key=str(commit["commit_id"]),
            job_id=job["job_id"],
            commit_id=commit["commit_id"],
        )
    if recovered:
        state["active_job_ids"] = sorted(
            row["job_id"] for row in plan["jobs"] if row.get("status") == "running"
        )
    return recovered


def complete_job(
    root: str | Path,
    job_id: str,
    result_path: str | None = None,
    *,
    simulate_interruption_after_commit: bool = False,
) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    validation = validate_result(run_root, job_id, result_path)
    if validation.get("status") != "valid":
        raise V7RuntimeError("staged result failed validation: " + "; ".join(validation.get("errors", [])))
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        jobs = _job_map(plan)
        job = jobs.get(job_id)
        if not job:
            raise V7RuntimeError(f"unknown schema-v7 job: {job_id}")
        if job.get("status") == "committed":
            supplied = result_path or str(job.get("validated_result_path", ""))
            path = run_root / supplied
            if path.is_file() and _sha256_file(path) != str(job.get("validated_result_sha256")):
                raise V7RuntimeError("duplicate completion supplied conflicting result content")
            return {
                "status": "committed",
                "job_id": job_id,
                "commit_id": job["commit_id"],
                "duplicate_completion_prevented": True,
            }
        attempt = next(
            (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
            None,
        )
        if not attempt or job.get("completion_state") != "validated":
            raise V7RuntimeError("job does not have a validated active attempt")
        path = run_root / str(job["validated_result_path"])
        if not path.is_file() or _sha256_file(path) != str(job["validated_result_sha256"]):
            raise V7RuntimeError("validated result checkpoint is missing or changed")
        result = _read_json(path, {})
        errors, records = _validate_result_object(run_root, job, attempt, result)
        if errors:
            raise V7RuntimeError("validated result no longer validates: " + "; ".join(errors))
        dependencies = _dependency_commits(run_root, job, jobs)
        commit, _ = _publish_commit_locked(
            run_root,
            job,
            dependency_commits=dependencies,
            records=records,
            progress=result["progress"],
        )
        _write_commit_execution_receipt(
            run_root,
            commit_id=commit["commit_id"],
            job_id=job_id,
            attempt_id=str(attempt["attempt_id"]),
            result_sha256=str(job["validated_result_sha256"]),
            budget_usage=result["budget_usage"],
        )
        applied = state.setdefault("applied_commit_usage_ids", [])
        if commit["commit_id"] not in applied:
            for field in USAGE_FIELDS:
                state["budget_usage"][field] = int(state["budget_usage"][field]) + int(result["budget_usage"][field])
            applied.append(commit["commit_id"])
            applied.sort()
        attempt["status"] = "committed"
        attempt["finished_at"] = _now()
        attempt["commit_id"] = commit["commit_id"]
        attempt["progress"] = dict(result["progress"])
        state["active_job_ids"] = sorted(
            value for value in state["active_job_ids"] if value != job_id
        )
        _event(
            run_root,
            "job_committed",
            event_key=commit["commit_id"],
            job_id=job_id,
            attempt_id=attempt["attempt_id"],
            commit_id=commit["commit_id"],
            result_sha256=job["validated_result_sha256"],
        )
        if simulate_interruption_after_commit:
            # Publish attempts/index/commit, but deliberately leave the job plan at its pre-commit state.
            published_job = dict(job)
            prior_plan = _read_json(_runtime_root(run_root) / "execution_plan.json", {})
            _atomic_json(_runtime_root(run_root) / "attempts.json", attempts)
            _atomic_json(_runtime_root(run_root) / "runtime_state.json", state)
            _atomic_json(_runtime_root(run_root) / "execution_plan.json", prior_plan)
            raise SimulatedInterruption(
                f"simulated interruption after publishing {published_job['commit_id']}"
            )
        _advance_dag_locked(run_root, config, plan, state)
        _persist_runtime(run_root, plan, state, attempts)
        return {
            "status": "committed",
            "job_id": job_id,
            "commit_id": commit["commit_id"],
            "scientific_hash": commit["scientific_hash"],
            "canonical_scientific_hash": _canonical_index(_runtime_root(run_root))["scientific_hash"],
            "next": _next_summary(plan, state, config),
        }


def _next_summary(plan: Mapping[str, Any], state: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ready_jobs": sorted(
            row["job_id"] for row in plan.get("jobs", []) if row.get("status") == "ready"
        ),
        "running_jobs": sorted(
            row["job_id"] for row in plan.get("jobs", []) if row.get("status") == "running"
        ),
        "status": state["status"],
        "max_active_jobs": config["max_active_jobs"],
    }


def fail_job(
    root: str | Path,
    job_id: str,
    failure_kind: str,
    retry_after_seconds: int | None = None,
    detail: str = "",
    *,
    retryable: bool = True,
) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    if failure_kind == "unrecoverable":
        retryable = False
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        job = _job_map(plan).get(job_id)
        if not job or job.get("status") != "running":
            raise V7RuntimeError(f"job is not running: {job_id}")
        if job.get("completion_state") == "validated":
            raise V7RuntimeError("a validated result must be committed or recovered, not failed")
        attempt = next(
            (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
            None,
        )
        if not attempt:
            raise V7RuntimeError("active attempt is missing")
        job["retry_count"] = int(job["retry_count"]) + 1
        job["failure_kind"] = str(failure_kind)
        job["failure_detail"] = str(detail)
        if retryable and int(job["retry_count"]) <= int(config["retry_limit"]):
            exponential = int(config["retry_base_seconds"]) * (2 ** (job["retry_count"] - 1))
            delay = min(
                int(config["retry_delay_cap_seconds"]),
                max(exponential, int(retry_after_seconds or 0)),
            )
            job["retry_delay_seconds"] = delay
            job["retry_not_before"] = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).isoformat()
            job["status"] = "retry_wait"
        else:
            job["status"] = "failed"
            job["retry_not_before"] = ""
            if job.get("required"):
                blocker = f"required_shard_failed:{job_id}:{failure_kind}"
                if blocker not in state["acceptance_blockers"]:
                    state["acceptance_blockers"].append(blocker)
        job["active_attempt_id"] = ""
        attempt["status"] = "failed"
        attempt["finished_at"] = _now()
        attempt["failure_kind"] = str(failure_kind)
        attempt["failure_detail"] = str(detail)
        state["active_job_ids"] = sorted(value for value in state["active_job_ids"] if value != job_id)
        _event(
            run_root,
            "job_failed",
            event_key=str(attempt["attempt_id"]),
            job_id=job_id,
            attempt_id=attempt["attempt_id"],
            failure_kind=failure_kind,
            retryable=retryable,
            retry_count=job["retry_count"],
        )
        _advance_dag_locked(run_root, config, plan, state)
        _persist_runtime(run_root, plan, state, attempts)
        return {
            "job_id": job_id,
            "status": job["status"],
            "retry_count": job["retry_count"],
            "retry_not_before": job["retry_not_before"],
            "retry_delay_seconds": job["retry_delay_seconds"],
            "packet_hash": job["packet_hash"],
        }


def recover_job(root: str | Path, job_id: str, new_agent_id: str, reason: str) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        job = _job_map(plan).get(job_id)
        if not job or job.get("status") != "running":
            raise V7RuntimeError(f"job is not running: {job_id}")
        attempt = next(
            (row for row in attempts if row.get("attempt_id") == job.get("active_attempt_id")),
            None,
        )
        if not attempt:
            raise V7RuntimeError("active attempt is missing")
        attempt["status"] = "orphaned"
        attempt["finished_at"] = _now()
        attempt["failure_kind"] = "worker_interruption"
        attempt["failure_detail"] = reason
        job["status"] = "ready"
        job["active_attempt_id"] = ""
        state["active_job_ids"] = sorted(value for value in state["active_job_ids"] if value != job_id)
        _event(
            run_root,
            "job_recovered",
            event_key=str(attempt["attempt_id"]),
            job_id=job_id,
            orphaned_attempt_id=attempt["attempt_id"],
            new_agent_id=new_agent_id,
            reason=reason,
        )
        _persist_runtime(run_root, plan, state, attempts)
    return start_job(run_root, job_id, new_agent_id)


def resume_action(root: str | Path) -> dict[str, Any]:
    return next_action(root)


def _execution_hash(root: Path, attempts: Iterable[Mapping[str, Any]]) -> str:
    events = [_read_json(path, {}) for path in sorted((_runtime_root(root) / "events").glob("*.json"))]
    return canonical_sha256({"attempts": list(attempts), "events": events})


def status(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    with _lock(run_root):
        config, plan, state, attempts = _load_runtime(run_root)
        recovered = _recover_published_commits_locked(run_root, plan, state, attempts)
        _advance_dag_locked(run_root, config, plan, state)
        _persist_runtime(run_root, plan, state, attempts)
        counts: dict[str, int] = {}
        for job in plan["jobs"]:
            counts[str(job["status"])] = counts.get(str(job["status"]), 0) + 1
        index = _canonical_index(_runtime_root(run_root))
        return {
            "state": state,
            "job_counts": dict(sorted(counts.items())),
            "jobs": [
                {
                    "job_id": row["job_id"],
                    "stage": row["stage"],
                    "role": row["role"],
                    "shard_key": row["shard_key"],
                    "status": row["status"],
                    "required": row["required"],
                    "attempt_count": row["attempt_count"],
                    "retry_count": row["retry_count"],
                    "progress": row["progress"],
                    "commit_id": row["commit_id"],
                }
                for row in sorted(plan["jobs"], key=lambda value: (str(value["stage"]), str(value["job_id"])))
            ],
            "canonical_scientific_hash": index["scientific_hash"],
            "execution_hash": _execution_hash(run_root, attempts),
            "recovered_commit_jobs": recovered,
        }


def validate_runtime(root: str | Path, *, final: bool = False) -> list[str]:
    run_root = Path(root).expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = _case_manifest(run_root)
        config, plan, state, attempts = _load_runtime(run_root)
    except Exception as exc:
        return [str(exc)]
    if plan.get("schema_version") != SCHEMA_VERSION or state.get("schema_version") != SCHEMA_VERSION:
        errors.append("runtime schema_version must be 7")
    if plan.get("runtime_model_version") != RUNTIME_MODEL_VERSION or state.get("runtime_model_version") != RUNTIME_MODEL_VERSION:
        errors.append("runtime model version mismatch")
    if plan.get("case_revision_id") != manifest.get("case_revision_id"):
        errors.append("runtime case revision does not match the immutable case manifest")
    if plan.get("config_sha256") != canonical_sha256(config):
        errors.append("runtime config hash mismatch")
    jobs = _job_map(plan)
    if len(jobs) != len(plan.get("jobs", [])):
        errors.append("execution plan contains duplicate job IDs")
    for job_id, job in jobs.items():
        if job.get("status") not in JOB_STATUSES:
            errors.append(f"job {job_id}: invalid status")
        if len(job.get("dependency_job_ids", [])) != len(set(job.get("dependency_job_ids", []))):
            errors.append(f"job {job_id}: duplicate dependency")
        if job_id in job.get("dependency_job_ids", []):
            errors.append(f"job {job_id}: self dependency")
        if any(value not in jobs for value in job.get("dependency_job_ids", [])):
            errors.append(f"job {job_id}: missing dependency")
        if job.get("input_record_count") > (
            config["max_candidate_records_per_shard"]
            if job.get("stage") not in {"discovery_source_shards"}
            else config["max_source_records_per_shard"]
        ) and not job.get("internal"):
            errors.append(f"job {job_id}: record shard limit exceeded")
        if int(job.get("input_source_bytes", 0)) > int(config["max_shard_source_bytes"]) and not job.get("internal"):
            errors.append(f"job {job_id}: byte shard limit exceeded")
        if job.get("packet_manifest_path"):
            try:
                verify_packet(run_root, run_root / str(job["packet_manifest_path"]), str(job["packet_hash"]))
            except Exception as exc:
                errors.append(f"job {job_id}: {exc}")
        if job.get("status") == "committed":
            path = run_root / str(job.get("commit_path", ""))
            commit = _read_json(path, {})
            body = {key: value for key, value in commit.items() if key not in {"commit_id", "scientific_hash"}}
            expected = canonical_sha256(body)
            if (
                not path.is_file()
                or commit.get("scientific_hash") != expected
                or commit.get("commit_id") != f"V7COMMIT-{expected[:28]}"
                or commit.get("commit_id") != job.get("commit_id")
            ):
                errors.append(f"job {job_id}: committed artifact integrity failure")
    # Deterministic cycle check.
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in visited:
            return
        if job_id in visiting:
            errors.append(f"execution plan dependency cycle at {job_id}")
            return
        visiting.add(job_id)
        for dependency in jobs[job_id].get("dependency_job_ids", []):
            visit(str(dependency))
        visiting.remove(job_id)
        visited.add(job_id)

    for job_id in sorted(jobs):
        visit(job_id)
    attempt_ids: set[str] = set()
    for attempt in attempts:
        attempt_id = str(attempt.get("attempt_id", ""))
        if not attempt_id or attempt_id in attempt_ids:
            errors.append(f"duplicate or missing attempt ID: {attempt_id}")
        attempt_ids.add(attempt_id)
        if attempt.get("status") not in ATTEMPT_STATUSES:
            errors.append(f"attempt {attempt_id}: invalid status")
        if attempt.get("job_id") not in jobs:
            errors.append(f"attempt {attempt_id}: unknown job")
    for commit_id in state.get("applied_commit_usage_ids", []):
        receipt = _read_json(
            _runtime_root(run_root) / "commit_receipts" / f"{commit_id}.json",
            {},
        )
        if (
            not isinstance(receipt, dict)
            or receipt.get("commit_id") != commit_id
            or not isinstance(receipt.get("budget_usage"), dict)
            or set(receipt.get("budget_usage", {})) != set(USAGE_FIELDS)
        ):
            errors.append(f"commit execution receipt integrity failure: {commit_id}")
    try:
        index = _canonical_index(_runtime_root(run_root))
        if index.get("scientific_hash") != canonical_sha256(_scientific_index_projection(index)):
            errors.append("canonical scientific hash mismatch")
        for collection, records in index["collections"].items():
            for record_id, ref in records.items():
                path = run_root / str(ref.get("path", ""))
                if not path.is_file() or _sha256_file(path) != str(ref.get("sha256", "")):
                    errors.append(f"canonical record integrity failure: {collection}:{record_id}")
    except Exception as exc:
        errors.append(str(exc))
    if final:
        nonterminal = [row["job_id"] for row in jobs.values() if row.get("status") not in TERMINAL_JOB_STATUSES]
        if nonterminal:
            errors.append(f"final runtime has nonterminal jobs: {sorted(nonterminal)}")
        if state.get("status") != "complete":
            errors.append(
                "final acceptance requires status=complete; diagnostic partials and blockers cannot finalize"
            )
        if state.get("acceptance_blockers"):
            errors.append("final acceptance blockers remain")
    return list(dict.fromkeys(errors))


class V7RuntimeAdapter:
    """Production-facing runtime/packet methods used by the schema-v7 acceptance protocol."""

    def build_task_packets(
        self,
        task_name: str,
        candidate_ids: Iterable[str],
        max_candidates: int,
        max_bytes: int,
    ) -> list[dict[str, Any]]:
        return build_task_packets(task_name, candidate_ids, max_candidates, max_bytes)

    def execute_packets(
        self,
        task_packets: Iterable[Mapping[str, Any]],
        interruption_schedule: Mapping[str, Any],
        replay_schedule: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Deterministically reduce supplied packet records across schedule/replay variants."""

        packets = [dict(value) for value in task_packets]
        by_key: dict[str, dict[str, Any]] = {}
        for packet in packets:
            key = str(packet.get("shard_key", ""))
            if not key:
                raise V7RuntimeError("task packet lacks shard_key")
            if key in by_key and by_key[key] != packet:
                raise V7RuntimeError(f"idempotency conflict for packet {key}")
            by_key[key] = packet
        default_order = sorted(by_key)
        requested_order = [str(value) for value in replay_schedule.get("order", default_order)]
        if set(requested_order) != set(default_order):
            raise V7RuntimeError("replay schedule must cover every and only supplied shard")
        interrupted = {str(value) for value in interruption_schedule.get("after_stage", [])}
        canonical_records: dict[str, dict[str, Any]] = {}
        attempts: list[dict[str, Any]] = []
        recovered: list[str] = []
        commit_ids: list[str] = []
        processed_record_count = 0
        for key in requested_order:
            packet = by_key[key]
            rows = packet.get("records")
            if rows is None:
                rows = [{"record_id": value} for value in packet.get("candidate_ids", [])]
            if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                raise V7RuntimeError(f"packet {key} records are invalid")
            processed_record_count += len(rows)
            commit_projection = []
            for raw in rows:
                row = dict(raw)
                record_id = str(row.get("record_id") or row.get("candidate_id") or "")
                if not record_id:
                    raise V7RuntimeError(f"packet {key} contains an unidentifiable record")
                prior = canonical_records.get(record_id)
                if prior is not None and prior != row:
                    raise V7RuntimeError(f"idempotency conflict for {record_id}")
                canonical_records[record_id] = prior or row
                commit_projection.append(row)
            commit_id = f"V7COMMIT-{canonical_sha256({'shard_key': key, 'records': commit_projection})[:28]}"
            commit_ids.append(commit_id)
            attempts.append({"shard_key": key, "commit_id": commit_id, "outcome": "committed"})
            if key in interrupted:
                recovered.append(commit_id)
        scientific_projection = [canonical_records[key] for key in sorted(canonical_records)]
        scientific_hash = canonical_sha256(scientific_projection)
        execution_hash = canonical_sha256(
            {"attempts": attempts, "interrupted": sorted(interrupted), "replay_order": requested_order}
        )
        return {
            "staged_attempts": attempts,
            "atomic_commits": sorted(set(commit_ids)),
            "recovered_commits": sorted(recovered),
            "scientific_hash": scientific_hash,
            "execution_hash": execution_hash,
            "canonical_record_count": len(scientific_projection),
            "replay_duplicate_effects_prevented": processed_record_count - len(scientific_projection),
        }


__all__ = [
    "BREADTH_PROFILES",
    "RUNTIME_DIRECTORY",
    "RUNTIME_MODEL_VERSION",
    "SCHEMA_VERSION",
    "SimulatedInterruption",
    "V7RuntimeAdapter",
    "V7RuntimeError",
    "complete_job",
    "fail_job",
    "initialize_runtime",
    "is_v7_runtime",
    "next_action",
    "normalize_runtime_config",
    "record_progress",
    "recover_job",
    "resume_action",
    "start_job",
    "status",
    "validate_result",
    "validate_runtime",
]
