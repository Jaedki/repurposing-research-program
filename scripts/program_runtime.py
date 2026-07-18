#!/usr/bin/env python3
"""Deterministic serial runtime for schema-v6 repurposing programmes."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_context_packet import build_packet
from program_contract import (
    BRANCH_BUDGET_PER_UNIT,
    BROAD_DOMAINS,
    FAILURE_KINDS,
    GLOBAL_PERSPECTIVES,
    LEDGER_KEYS,
    MAX_ACTIVE_JOBS,
    RETRY_BASE_SECONDS,
    RETRY_DELAY_CAP_SECONDS,
    RETRY_LIMIT,
    RETRYABLE_FAILURE_KINDS,
    SCHEMAS,
    SCHEMA_VERSION,
    SOURCE_AGGREGATE_FIELDS,
    STALE_RUN_SECONDS,
    TERMINAL_SEARCH_COVERAGE_STATUSES,
    required_case_present,
    required_query_families,
)
from program_io import (
    append_jsonl,
    content_hash,
    file_hash,
    index_rows,
    read_json,
    read_jsonl,
    upsert_jsonl,
    write_json,
    write_jsonl,
)
from ranking import council_selection, rank_candidates
from validate_program import validate_run, validate_staged_result


DEFAULT_SLICE_JOBS = 4
DEFAULT_SLICE_MINUTES = 25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.")
    if not cleaned:
        raise ValueError("Identifier becomes empty after normalization")
    return cleaned


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _job(
    job_id: str,
    *,
    phase: int,
    sequence: int,
    kind: str,
    role: str,
    question: str,
    unit_id: str = "",
    depends_on: list[str] | None = None,
    candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "phase": phase,
        "sequence": sequence,
        "kind": kind,
        "role": role,
        "unit_id": unit_id,
        "question": question,
        "depends_on": depends_on or [],
        "candidate_ids": candidate_ids or [],
        "status": "planned",
        "assigned_agent_id": "",
        "attempt_count": 0,
        "packet_manifest_path": "",
        "packet_hash": "",
        "result_path": "",
        "result_hash": "",
        "retry_not_before": "",
        "retry_reason": "",
        "retry_detail": "",
        "retry_count": 0,
        "retry_limit": RETRY_LIMIT,
        "retry_delay_seconds": 0,
        "completion_state": "not_validated",
        "validated_result_path": "",
        "validated_result_hash": "",
        "commit_started_at": "",
        "completed_at": "",
        "selection_snapshot": [],
        "selection_snapshot_hash": "",
    }


def _unit(unit_id: str, unit_type: str, perspective: str, question: str) -> dict[str, Any]:
    planned_query_families = sorted(required_query_families(unit_type, perspective))
    return {
        "unit_id": unit_id,
        "unit_type": unit_type,
        "perspective": perspective,
        "question": question,
        "worker_agent_id": "",
        "status": "planned",
        "planned_query_families": planned_query_families,
        "completed_query_families": [],
        "search_ids": [],
        "coverage_statuses": {
            family: "NOT_YET_SEARCHED" for family in planned_query_families
        },
        "evidence_frontier": [],
        "frontier_exhausted": False,
        "branch_budget": BRANCH_BUDGET_PER_UNIT,
        "observation_ids": [],
        "candidate_exclusions": [],
        "closure_basis": "",
    }


def _initial_plan() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    jobs: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    prior = ""
    for index, perspective in enumerate(BROAD_DOMAINS, 1):
        unit_id = f"BE{index:02d}"
        question = f"Build source-linked human therapeutic evidence for {perspective}; do not generate compounds."
        units.append(_unit(unit_id, "broad_evidence", perspective, question))
        jobs.append(
            _job(
                f"{unit_id}.research",
                phase=1,
                sequence=100 + index,
                kind="research",
                role="evidence_researcher",
                unit_id=unit_id,
                question=question,
                depends_on=[prior] if prior else [],
            )
        )
        prior = f"{unit_id}.research"
    for index, perspective in enumerate(GLOBAL_PERSPECTIVES, 1):
        unit_id = f"CP{index:02d}"
        question = (
            f"Independently discover exact compounds from the {perspective} perspective and connect each "
            "to a specific human endpoint; resolve its exact structure and active moiety."
        )
        units.append(_unit(unit_id, "compound_perspective", perspective, question))
        jobs.append(
            _job(
                f"{unit_id}.research",
                phase=2,
                sequence=200 + index,
                kind="research",
                role="compound_researcher",
                unit_id=unit_id,
                question=question,
                depends_on=[prior],
            )
        )
        prior = f"{unit_id}.research"
    jobs.append(
        _job(
            "MERGE01",
            phase=3,
            sequence=300,
            kind="merge",
            role="evidence_integrator",
            question=(
                "Merge every exact-compound observation by structure identity, preserve independent routes and "
                "uncertainties, normalize formulations by active moiety, and assign source-backed candidate class, "
                "compound origin, primary endpoint, repurposing readiness, and every ranking component."
            ),
            depends_on=[prior],
        )
    )
    audit_question = (
        "Independently retrieve and verify every decisive candidate-path claim against primary or authoritative "
        "sources outside the packet, run a distinct counterevidence search, record claim-specific rationales, "
        "verify candidate class, endpoint, and readiness evidence, and reassess every candidate score and cap."
    )
    units.append(_unit("AUDIT01", "decisive_audit", "decisive_claims", audit_question))
    jobs.append(
        _job(
            "AUDIT01",
            phase=4,
            sequence=400,
            kind="decisive_audit",
            role="scientific_auditor",
            unit_id="AUDIT01",
            question=audit_question,
            depends_on=["MERGE01"],
        )
    )
    return (
        {
            "schema_version": SCHEMA_VERSION,
            "max_active_jobs": MAX_ACTIVE_JOBS,
            "jobs": jobs,
            "created_at": _now(),
        },
        units,
    )


def _event(root: Path, event: str, **details: Any) -> None:
    rows = read_jsonl(root / "orchestration.jsonl")
    dedupe_key = str(details.pop("dedupe_key", ""))
    if dedupe_key and any(str(row.get("dedupe_key", "")) == dedupe_key for row in rows):
        return
    append_jsonl(
        root / "orchestration.jsonl",
        {
            "event_id": f"EV{len(rows) + 1:06d}",
            "event": event,
            "at": _now(),
            **({"dedupe_key": dedupe_key} if dedupe_key else {}),
            **details,
        },
    )


def _job_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return index_rows(plan.get("jobs", []), "job_id")


def _retry_elapsed(job: dict[str, Any]) -> bool:
    value = _parse_time(job.get("retry_not_before"))
    return value is None or value <= datetime.now(timezone.utc)


def _retry_delay_seconds(retry_count: int, retry_after_seconds: int | None = None) -> int:
    exponential = min(RETRY_DELAY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, retry_count - 1)))
    requested = max(0, int(retry_after_seconds or 0))
    return min(RETRY_DELAY_CAP_SECONDS, max(exponential, requested))


def _refresh(plan: dict[str, Any], state: dict[str, Any]) -> None:
    jobs = _job_map(plan)
    for job in plan.get("jobs", []):
        if job.get("status") == "retry_wait" and _retry_elapsed(job):
            job["status"] = "ready"
        if job.get("status") != "planned":
            continue
        if all(jobs.get(str(dep), {}).get("status") == "complete" for dep in job.get("depends_on", [])):
            job["status"] = "ready"
    incomplete = sorted(
        [row for row in plan.get("jobs", []) if row.get("status") != "complete"],
        key=lambda row: (int(row.get("phase", 99)), int(row.get("sequence", 9999)), str(row.get("job_id"))),
    )
    state["current_phase"] = f"phase_{incomplete[0]['phase']}" if incomplete else "ready_for_finalization"
    state["updated_at"] = _now()


def _detect_run_health(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    job_id = str(state.get("active_job_id", ""))
    attempt_id = str(state.get("active_attempt_id", ""))
    jobs = _job_map(plan)
    attempts = index_rows(read_jsonl(root / "job_attempts.jsonl"), "attempt_id")
    job = jobs.get(job_id)
    attempt = attempts.get(attempt_id)
    dangling_running = [
        row for row in attempts.values()
        if row.get("status") == "running" and str(row.get("attempt_id")) != attempt_id
    ]
    inconsistent = bool(job_id) != bool(attempt_id)
    if job_id and (
        not job
        or job.get("status") != "running"
        or not attempt
        or attempt.get("status") != "running"
        or str(attempt.get("job_id")) != job_id
    ):
        inconsistent = True
    if dangling_running:
        inconsistent = True
    committing = bool(job and job.get("completion_state") in {"validated", "committing"})
    state["interrupted_run_detected"] = inconsistent or committing
    progress = _parse_time((attempt or {}).get("last_progress_at") or (attempt or {}).get("started_at"))
    state["stale_run_detected"] = bool(
        job_id and progress and (datetime.now(timezone.utc) - progress).total_seconds() >= STALE_RUN_SECONDS
    )


def _reconcile_interrupted_start(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    if state.get("active_job_id") or state.get("active_attempt_id"):
        return
    running = [row for row in read_jsonl(root / "job_attempts.jsonl") if row.get("status") == "running"]
    if len(running) != 1:
        return
    attempt = running[0]
    job = _job_map(plan).get(str(attempt.get("job_id", "")))
    if (
        not job
        or job.get("status") not in {"ready", "running"}
        or str(job.get("packet_hash", "")) != str(attempt.get("packet_hash", ""))
    ):
        return
    job["status"] = "running"
    job["assigned_agent_id"] = str(attempt.get("agent_id", ""))
    match = re.search(r"\.attempt(\d+)$", str(attempt.get("attempt_id", "")))
    if match:
        job["attempt_count"] = max(int(job.get("attempt_count", 0)), int(match.group(1)))
    state["active_job_id"] = str(job.get("job_id", ""))
    state["active_attempt_id"] = str(attempt.get("attempt_id", ""))
    _event(
        root,
        "interrupted_start_reconciled",
        job_id=job.get("job_id"),
        attempt_id=attempt.get("attempt_id"),
        dedupe_key=f"start-reconcile:{attempt.get('attempt_id')}",
    )


def _persist(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    _reconcile_interrupted_start(root, plan, state)
    _refresh(plan, state)
    _detect_run_health(root, plan, state)
    write_json(root / "execution_plan.json", plan)
    write_json(root / "program_state.json", state)


def _assert_current_schema(root: Path) -> None:
    state = read_json(root / "program_state.json", {})
    plan = read_json(root / "execution_plan.json", {})
    versions = {state.get("schema_version"), plan.get("schema_version")}
    if versions != {SCHEMA_VERSION}:
        raise ValueError(
            f"Legacy or incompatible run is read-only: expected schema {SCHEMA_VERSION}, "
            f"found state={state.get('schema_version')!r}, plan={plan.get('schema_version')!r}"
        )


def _verify_packet(root: Path, job: dict[str, Any]) -> None:
    manifest_path = (root / str(job.get("packet_manifest_path", ""))).resolve()
    manifest_path.relative_to(root.resolve())
    manifest = read_json(manifest_path, {})
    body = {key: value for key, value in manifest.items() if key != "packet_hash"}
    if (
        manifest.get("job_id") != job.get("job_id")
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("packet_hash") != content_hash(body)
        or manifest.get("packet_hash") != job.get("packet_hash")
    ):
        raise ValueError(f"Immutable packet integrity failure for {job.get('job_id')}")
    for chunk in manifest.get("required_chunks", []):
        path = (root / str(chunk.get("path", ""))).resolve()
        path.relative_to(root.resolve())
        if not path.is_file() or str(chunk.get("sha256")) != file_hash(path):
            raise ValueError(f"Immutable packet chunk integrity failure for {job.get('job_id')}")


def initialize(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Run folder is not empty: {root}")
    if not required_case_present(case):
        raise ValueError("Provide at least one of human_gene, human_disease, or human_phenotype")
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("packets", "staging", "raw_sources"):
        (root / directory).mkdir()
    plan, units = _initial_plan()
    state = {
        "schema_version": SCHEMA_VERSION,
        "max_active_jobs": MAX_ACTIVE_JOBS,
        "current_phase": "phase_1",
        "active_job_id": "",
        "active_attempt_id": "",
        "checkpoint_pending": False,
        "slice_started_at": _now(),
        "slice_jobs_completed": 0,
        "slice_max_jobs": DEFAULT_SLICE_JOBS,
        "slice_max_minutes": DEFAULT_SLICE_MINUTES,
        "blocked_reason": "",
        "interrupted_run_detected": False,
        "stale_run_detected": False,
        "created_at": _now(),
        "updated_at": _now(),
    }
    write_json(root / "case.json", case)
    for filename in SCHEMAS:
        write_jsonl(root / filename, units if filename == "research_units.jsonl" else [])
    write_jsonl(root / "job_attempts.jsonl", [])
    write_jsonl(root / "orchestration.jsonl", [])
    _event(root, "initialized", schema_version=SCHEMA_VERSION)
    _persist(root, plan, state)
    return state


def _checkpoint_due(state: dict[str, Any]) -> bool:
    if state.get("checkpoint_pending") is True:
        return True
    started = _parse_time(state.get("slice_started_at"))
    elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60 if started else 0
    return (
        int(state.get("slice_jobs_completed", 0)) >= int(state.get("slice_max_jobs", DEFAULT_SLICE_JOBS))
        or elapsed >= int(state.get("slice_max_minutes", DEFAULT_SLICE_MINUTES))
    )


def next_action(root: Path) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    _persist(root, plan, state)
    if state.get("active_job_id"):
        active_job = _job_map(plan).get(str(state["active_job_id"]), {})
        if active_job.get("completion_state") in {"validated", "committing"}:
            completed = _commit_validated_job(root, plan, state, active_job)
            return {
                **completed["next"],
                "resumed_interrupted_completion": str(active_job.get("job_id")),
            }
        return {
            "action": "resume_active_job",
            "job_id": state["active_job_id"],
            "attempt_id": state.get("active_attempt_id"),
            "stale_run_detected": state.get("stale_run_detected") is True,
            "interrupted_run_detected": state.get("interrupted_run_detected") is True,
        }
    if state.get("blocked_reason"):
        return {"action": "blocked", "reason": state["blocked_reason"]}
    incomplete = sorted(
        [row for row in plan.get("jobs", []) if row.get("status") != "complete"],
        key=lambda row: (int(row.get("phase", 99)), int(row.get("sequence", 9999)), str(row.get("job_id"))),
    )
    if not incomplete:
        errors = validate_run(root)
        if errors:
            state["blocked_reason"] = "Final structural or scientific validation failed"
            _persist(root, plan, state)
            return {"action": "blocked", "reason": state["blocked_reason"], "validation_errors": errors}
        return {"action": "finalize"}
    if _checkpoint_due(state):
        state["checkpoint_pending"] = True
        _persist(root, plan, state)
        return {"action": "checkpoint", "reason": "bounded_execution_slice_complete", "resume_command": "resume"}
    job = incomplete[0]
    if job.get("status") == "retry_wait":
        return {
            "action": "wait_for_retry",
            "job_id": job["job_id"],
            "retry_not_before": job["retry_not_before"],
            "retry_reason": job.get("retry_reason", ""),
            "retry_detail": job.get("retry_detail", ""),
            "retry_count": job.get("retry_count", 0),
            "retry_limit": job.get("retry_limit", RETRY_LIMIT),
            "retry_delay_seconds": job.get("retry_delay_seconds", 0),
        }
    if job.get("status") != "ready":
        return {"action": "blocked_by_dependencies", "earliest_job": job["job_id"]}
    if job.get("packet_manifest_path"):
        _verify_packet(root, job)
    else:
        manifest, packet_hash = build_packet(root, str(job["job_id"]))
        job["packet_manifest_path"] = str(manifest.relative_to(root))
        job["packet_hash"] = packet_hash
        _persist(root, plan, state)
    attempt_number = int(job.get("attempt_count", 0)) + 1
    result = root / "staging" / f"{_safe(str(job['job_id']))}.attempt{attempt_number:03d}" / "result.json"
    prompt = (
        f"Job ID: {job['job_id']}\n"
        f"Packet manifest: {(root / job['packet_manifest_path']).resolve()}\n"
        f"Expected result: {result.resolve()}"
    )
    return {
        "action": "start_agent",
        "job_id": job["job_id"],
        "role": job["role"],
        "packet_manifest_path": job["packet_manifest_path"],
        "packet_hash": job["packet_hash"],
        "expected_result_path": str(result.relative_to(root)),
        "spawn_prompt": prompt,
        "spawn_contract": {"fork_turns": "none", "prompt_lines": 3},
        "agent_action": "resume_assigned" if job.get("assigned_agent_id") else "spawn_new",
        "assigned_agent_id": job.get("assigned_agent_id", ""),
        "concurrency": 1,
    }


def start_job(root: Path, job_id: str, agent_id: str) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job = _job_map(plan).get(job_id)
    if not job:
        raise ValueError(f"Unknown job: {job_id}")
    if state.get("active_job_id"):
        if state.get("active_job_id") == job_id:
            active = _active_attempt(root, state)
            if str(active.get("agent_id")) == agent_id and active.get("status") == "running":
                return {**active, "duplicate_start_prevented": True}
        raise ValueError(f"Another job is active: {state['active_job_id']}")
    recoverable = next(
        (
            row for row in reversed(read_jsonl(root / "job_attempts.jsonl"))
            if row.get("job_id") == job_id and row.get("status") == "running"
        ),
        None,
    )
    if recoverable:
        if str(recoverable.get("agent_id")) != agent_id:
            raise ValueError(f"Interrupted start is reserved to {recoverable.get('agent_id')}")
        if str(recoverable.get("packet_hash")) != str(job.get("packet_hash")):
            raise ValueError("Interrupted start packet hash disagrees with the current job")
        job["status"] = "running"
        job["assigned_agent_id"] = agent_id
        state["active_job_id"] = job_id
        state["active_attempt_id"] = str(recoverable["attempt_id"])
        _event(root, "job_start_recovered", job_id=job_id, attempt_id=recoverable["attempt_id"], agent_id=agent_id)
        _persist(root, plan, state)
        return {**recoverable, "recovered_interrupted_start": True}
    if job.get("status") != "ready" or not job.get("packet_hash"):
        raise ValueError("Job is not ready with an immutable packet")
    _verify_packet(root, job)
    assigned = str(job.get("assigned_agent_id", ""))
    if assigned and assigned != agent_id:
        raise ValueError(f"Job is assigned to {assigned}")
    conflict = next(
        (row["job_id"] for row in plan.get("jobs", []) if row.get("assigned_agent_id") == agent_id and row["job_id"] != job_id),
        "",
    )
    if conflict:
        raise ValueError(f"Agent {agent_id} already owns independent job {conflict}")
    job["assigned_agent_id"] = agent_id
    job["attempt_count"] = int(job.get("attempt_count", 0)) + 1
    attempt_id = f"{_safe(job_id)}.attempt{job['attempt_count']:03d}"
    result_path = root / "staging" / attempt_id / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    attempt = {
        "attempt_id": attempt_id,
        "job_id": job_id,
        "agent_id": agent_id,
        "packet_hash": job["packet_hash"],
        "packet_manifest_path": job["packet_manifest_path"],
        "expected_result_path": str(result_path.relative_to(root)),
        "status": "running",
        "started_at": _now(),
        "last_progress_at": _now(),
        "finished_at": "",
        "failure_kind": "",
        "retry_reason": "",
    }
    append_jsonl(root / "job_attempts.jsonl", attempt)
    job["status"] = "running"
    state["active_job_id"] = job_id
    state["active_attempt_id"] = attempt_id
    _event(root, "job_started", job_id=job_id, attempt_id=attempt_id, agent_id=agent_id)
    _persist(root, plan, state)
    return attempt


def _active_attempt(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    attempt = next(
        (row for row in read_jsonl(root / "job_attempts.jsonl") if row.get("attempt_id") == state.get("active_attempt_id")),
        None,
    )
    if not attempt:
        raise ValueError("Active attempt record is missing")
    return attempt


def validate_result(root: Path, job_id: str, result_path: str | None = None) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job = _job_map(plan).get(job_id)
    if not job:
        raise ValueError(f"Unknown job: {job_id}")
    cached_relative = str(job.get("validated_result_path") or job.get("result_path") or "")
    supplied_relative = result_path or cached_relative
    if supplied_relative:
        supplied_path = (root / supplied_relative).resolve()
        supplied_path.relative_to(root.resolve())
        if (
            supplied_path.is_file()
            and job.get("completion_state") in {"validated", "committing", "committed"}
            and str(job.get("validated_result_hash")) == file_hash(supplied_path)
        ):
            return {
                "status": "valid",
                "job_id": job_id,
                "result_path": str(supplied_path.relative_to(root)),
                "cached_validation": True,
            }
    if job.get("status") != "running" or state.get("active_job_id") != job_id:
        raise ValueError(f"Job is not active: {job_id}")
    attempt = _active_attempt(root, state)
    relative = result_path or str(attempt["expected_result_path"])
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    result = read_json(path, None)
    if not isinstance(result, dict):
        return {"status": "invalid", "errors": ["Result must be one JSON object"], "result_path": relative}
    errors: list[str] = []
    for field in ("job_id", "packet_hash", "all_chunks_processed", "outcome", "ledger_updates"):
        if field not in result:
            errors.append(f"Result is missing {field}")
    if str(result.get("job_id")) != job_id:
        errors.append("Result job_id does not match the active job")
    if str(result.get("packet_hash")) != str(job.get("packet_hash")):
        errors.append("Result packet_hash does not match the immutable packet")
    if result.get("all_chunks_processed") is not True:
        errors.append("Result must confirm every packet chunk was processed")
    if result.get("outcome") != "completed":
        errors.append("Result outcome must be completed; scientific uncertainty belongs in structured records")
    if job.get("unit_id") and not str(result.get("closure_basis", "")).strip():
        errors.append("Research and audit jobs require a non-rhetorical closure_basis")
    if job.get("unit_id"):
        if not isinstance(result.get("evidence_frontier"), list):
            errors.append("Research and audit jobs require an evidence_frontier list")
        if result.get("frontier_exhausted") is not True:
            errors.append("Research and audit jobs must confirm frontier_exhausted=true")
    if job.get("kind") == "research":
        unit = next(
            (
                row for row in read_jsonl(root / "research_units.jsonl")
                if str(row.get("unit_id")) == str(job.get("unit_id"))
            ),
            {},
        )
        if unit.get("unit_type") == "compound_perspective" and not isinstance(
            result.get("candidate_exclusions"), list
        ):
            errors.append("Compound-perspective results require a candidate_exclusions list")
    if not errors:
        errors.extend(validate_staged_result(root, job, result, str(attempt.get("agent_id", ""))))
    if errors:
        return {"status": "invalid", "errors": errors, "result_path": relative}
    write_json(path, result, compact=True)
    job["completion_state"] = "validated"
    job["validated_result_path"] = str(path.relative_to(root))
    job["validated_result_hash"] = file_hash(path)
    attempts = read_jsonl(root / "job_attempts.jsonl")
    for row in attempts:
        if row.get("attempt_id") == attempt.get("attempt_id"):
            row["last_progress_at"] = _now()
    write_jsonl(root / "job_attempts.jsonl", attempts)
    _event(
        root,
        "result_validated",
        job_id=job_id,
        attempt_id=attempt.get("attempt_id"),
        dedupe_key=f"result-valid:{job_id}:{job.get('validated_result_hash')}",
    )
    _persist(root, plan, state)
    return {
        "status": "valid",
        "job_id": job_id,
        "result_path": str(path.relative_to(root)),
        "cached_validation": False,
    }


def _finish_attempt(
    root: Path,
    attempt_id: str,
    status: str,
    failure_kind: str = "",
    retry_reason: str = "",
) -> None:
    attempts = read_jsonl(root / "job_attempts.jsonl")
    target = next((row for row in attempts if row.get("attempt_id") == attempt_id), None)
    if not target:
        raise ValueError(f"Unknown attempt: {attempt_id}")
    if target.get("status") == status and target.get("finished_at"):
        return
    target["status"] = status
    target["finished_at"] = _now()
    target["last_progress_at"] = target["finished_at"]
    target["failure_kind"] = failure_kind
    target["retry_reason"] = retry_reason
    write_jsonl(root / "job_attempts.jsonl", attempts)


def _merge_updates(root: Path, updates: dict[str, Any]) -> None:
    for filename, rows in updates.items():
        normalized = rows
        if filename == "source_corpus.jsonl":
            current = index_rows(read_jsonl(root / filename), "source_id")
            normalized = []
            for row in rows:
                combined = dict(row)
                prior = current.get(str(row.get("source_id")), {})
                for field in SOURCE_AGGREGATE_FIELDS:
                    combined[field] = sorted({
                        *(str(value) for value in prior.get(field, [])),
                        *(str(value) for value in row.get(field, [])),
                    })
                normalized.append(combined)
        upsert_jsonl(root / filename, normalized, LEDGER_KEYS[filename])


def _complete_unit(root: Path, job: dict[str, Any], agent_id: str, result: dict[str, Any]) -> None:
    unit_id = str(job.get("unit_id", ""))
    if not unit_id:
        return
    units = read_jsonl(root / "research_units.jsonl")
    unit = next((row for row in units if str(row.get("unit_id")) == unit_id), None)
    if not unit:
        raise ValueError(f"Unknown research unit: {unit_id}")
    searches = [row for row in read_jsonl(root / "search_log.jsonl") if str(row.get("research_unit_id")) == unit_id]
    families = {str(row.get("query_family")) for row in searches}
    required = set(unit.get("planned_query_families", []))
    if families != required:
        raise ValueError(f"Unit {unit_id} did not complete its exact query-family contract")
    coverage_statuses: dict[str, str] = {}
    for family in sorted(required):
        outcomes = {
            str(row.get("outcome")) for row in searches if str(row.get("query_family")) == family
        }
        coverage_statuses[family] = (
            "FOUND" if "FOUND" in outcomes
            else "NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH"
            if outcomes == {"NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH"}
            else "NOT_YET_SEARCHED"
        )
    if any(value not in TERMINAL_SEARCH_COVERAGE_STATUSES for value in coverage_statuses.values()):
        raise ValueError(f"Unit {unit_id} retains a NOT_YET_SEARCHED coverage area")
    observations = [
        str(row.get("observation_id"))
        for row in read_jsonl(root / "candidate_observations.jsonl")
        if str(row.get("research_unit_id")) == unit_id
    ]
    unit.update(
        worker_agent_id=agent_id,
        status="complete",
        completed_query_families=sorted(families),
        search_ids=sorted(str(row.get("query_id")) for row in searches),
        coverage_statuses=coverage_statuses,
        evidence_frontier=result.get("evidence_frontier", []),
        frontier_exhausted=result.get("frontier_exhausted") is True,
        branch_budget=BRANCH_BUDGET_PER_UNIT,
        observation_ids=sorted(observations),
        candidate_exclusions=result.get("candidate_exclusions", []),
        closure_basis=str(result.get("closure_basis", "")),
    )
    write_jsonl(root / "research_units.jsonl", units)


def _register_council(root: Path, plan: dict[str, Any], selected: list[str]) -> None:
    candidates = read_jsonl(root / "candidate_records.jsonl")
    selected_set = set(selected)
    for candidate in candidates:
        candidate["council_status"] = "pending" if str(candidate.get("candidate_id")) in selected_set else "not_selected"
    write_jsonl(root / "candidate_records.jsonl", candidates)
    if not selected:
        return
    if any(job.get("kind") == "council" for job in plan.get("jobs", [])):
        return
    snapshot = [
        {
            "candidate_id": candidate["candidate_id"],
            "canonical_name": candidate["canonical_name"],
            "rank_section": candidate["rank_section"],
            "raw_score": candidate["raw_score"],
            "total_score": candidate["total_score"],
            "repurposing_readiness": {
                "score": candidate["repurposing_readiness"]["score"],
            },
            "score_components": {
                name: {"score": candidate["score_components"][name]["score"]}
                for name in ("human_evidence", "safety_tolerability")
            },
            "material_conflicts": candidate.get("material_conflicts", []),
            "audit_status": candidate.get("audit_status"),
        }
        for candidate in candidates
    ]
    council_job = _job(
            "COUNCIL01",
            phase=5,
            sequence=500,
            kind="council",
            role="candidate_council_reviewer",
            question=(
                "Review only the controller-selected therapeutic and repurposing leaders plus material conflicts. "
                "Explicitly challenge candidate class and endpoint category errors, require baseline_only for supportive "
                "care and benchmark_only for target-disease assets, and check mechanism, human relevance, safety, "
                "exposure, and unresolved contradictions. Return every selected candidate record with any class or "
                "endpoint-type correction before disposition, without rerunning a full debate."
            ),
            depends_on=["AUDIT01"],
            candidate_ids=selected,
        )
    council_job["selection_snapshot"] = snapshot
    council_job["selection_snapshot_hash"] = content_hash(snapshot)
    plan["jobs"].append(council_job)


def _commit_validated_job(
    root: Path,
    plan: dict[str, Any],
    state: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(job["job_id"])
    relative = str(job.get("validated_result_path", ""))
    path = root / relative
    if not path.is_file() or file_hash(path) != str(job.get("validated_result_hash", "")):
        raise ValueError(f"Validated result checkpoint is missing or changed for {job_id}")
    if job.get("completion_state") == "validated":
        job["completion_state"] = "committing"
        job["commit_started_at"] = _now()
        _event(
            root,
            "job_commit_started",
            job_id=job_id,
            attempt_id=state.get("active_attempt_id"),
            dedupe_key=f"commit-start:{job_id}:{job.get('validated_result_hash')}",
        )
        _persist(root, plan, state)
    if job.get("completion_state") != "committing":
        raise ValueError(f"Job {job_id} has invalid completion state {job.get('completion_state')!r}")
    attempt = _active_attempt(root, state)
    result = read_json(path, {})
    _merge_updates(root, result.get("ledger_updates", {}))
    _complete_unit(root, job, str(attempt.get("agent_id", "")), result)
    if job.get("kind") == "merge":
        observations = read_jsonl(root / "candidate_observations.jsonl")
        candidates = read_jsonl(root / "candidate_records.jsonl")
        covered = {
            str(value)
            for candidate in candidates
            for value in candidate.get("observation_ids", [])
        }
        if covered != {str(row.get("observation_id")) for row in observations}:
            raise ValueError("Merge must retain every independent candidate observation")
    if job.get("kind") == "decisive_audit":
        ranked = rank_candidates(root)
        _register_council(root, plan, council_selection(ranked))
    if job.get("kind") == "council":
        candidates = read_jsonl(root / "candidate_records.jsonl")
        selected = {str(value) for value in job.get("candidate_ids", [])}
        for candidate in candidates:
            if str(candidate.get("candidate_id")) in selected:
                candidate["council_status"] = "reviewed"
        write_jsonl(root / "candidate_records.jsonl", candidates)
        rank_candidates(root)

    job["status"] = "complete"
    job["completion_state"] = "committed"
    job["result_path"] = relative
    job["result_hash"] = file_hash(path)
    job["completed_at"] = _now()
    attempt_id = str(state.get("active_attempt_id"))
    _finish_attempt(root, attempt_id, "complete")
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    state["slice_jobs_completed"] = int(state.get("slice_jobs_completed", 0)) + 1
    if state["slice_jobs_completed"] >= int(state.get("slice_max_jobs", DEFAULT_SLICE_JOBS)):
        state["checkpoint_pending"] = True
    _event(
        root,
        "job_completed",
        job_id=job_id,
        attempt_id=attempt_id,
        dedupe_key=f"job-complete:{job_id}:{job.get('result_hash')}",
    )
    _persist(root, plan, state)
    return {"status": "complete", "next": next_action(root)}


def complete_job(root: Path, job_id: str, result_path: str | None = None) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job = _job_map(plan).get(job_id)
    if not job:
        raise ValueError(f"Unknown job: {job_id}")
    if job.get("status") == "complete" and job.get("completion_state") == "committed":
        path = root / str(job.get("result_path", ""))
        if not path.is_file() or file_hash(path) != str(job.get("result_hash", "")):
            raise ValueError(f"Completed result checkpoint is missing or changed for {job_id}")
        return {"status": "complete", "duplicate_completion_prevented": True, "next": next_action(root)}
    if job.get("status") != "running" or state.get("active_job_id") != job_id:
        raise ValueError(f"Job is not active: {job_id}")
    validation = validate_result(root, job_id, result_path)
    if validation.get("status") != "valid":
        raise ValueError("Staged result failed validation:\n" + "\n".join(f"- {e}" for e in validation["errors"]))
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job = _job_map(plan)[job_id]
    return _commit_validated_job(root, plan, state, job)


def fail_job(
    root: Path,
    job_id: str,
    failure_kind: str,
    retry_after_seconds: int | None,
    detail: str,
) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job = _job_map(plan).get(job_id)
    if not job or job.get("status") != "running" or state.get("active_job_id") != job_id:
        raise ValueError(f"Job is not active: {job_id}")
    attempt_id = str(state.get("active_attempt_id"))
    if failure_kind not in FAILURE_KINDS:
        raise ValueError(f"Unknown failure kind: {failure_kind}")
    if job.get("completion_state") != "not_validated":
        raise ValueError("A validated result must be completed or resumed, not failed")
    retry_reason = detail.strip() or failure_kind
    if failure_kind == "unrecoverable":
        job["status"] = "blocked"
        state["blocked_reason"] = detail or f"Unrecoverable failure in {job_id}"
    else:
        if failure_kind not in RETRYABLE_FAILURE_KINDS:
            raise ValueError(f"Failure kind is not retryable: {failure_kind}")
        job["retry_count"] = int(job.get("retry_count", 0)) + 1
        job["retry_reason"] = failure_kind
        job["retry_detail"] = retry_reason
        if int(job["retry_count"]) > int(job.get("retry_limit", RETRY_LIMIT)):
            job["status"] = "blocked"
            state["blocked_reason"] = f"Retry limit exceeded for {job_id}: {retry_reason}"
        else:
            delay = _retry_delay_seconds(int(job["retry_count"]), retry_after_seconds)
            job["retry_delay_seconds"] = delay
            job["retry_not_before"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            job["status"] = "retry_wait"
    _finish_attempt(root, attempt_id, "failed", failure_kind, retry_reason)
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    _event(
        root,
        "job_failed",
        job_id=job_id,
        attempt_id=attempt_id,
        failure_kind=failure_kind,
        detail=detail,
        retry_count=job.get("retry_count", 0),
        retry_not_before=job.get("retry_not_before", ""),
        dedupe_key=f"job-failed:{attempt_id}",
    )
    _persist(root, plan, state)
    return next_action(root)


def recover_active(root: Path, new_agent_id: str, reason: str = "assigned task unavailable") -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    job_id = str(state.get("active_job_id", ""))
    attempt_id = str(state.get("active_attempt_id", ""))
    job = _job_map(plan).get(job_id)
    if not job or not attempt_id:
        raise ValueError("No active job is available for recovery")
    _finish_attempt(root, attempt_id, "orphaned", "worker_interruption", reason)
    old_agent = str(job.get("assigned_agent_id", ""))
    job["assigned_agent_id"] = ""
    job["status"] = "ready"
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    _event(root, "active_job_recovered", job_id=job_id, old_agent_id=old_agent, new_agent_id=new_agent_id, reason=reason)
    _persist(root, plan, state)
    replacement = start_job(root, job_id, new_agent_id)
    return {**replacement, "recovered_from_attempt_id": attempt_id, "orphaned_agent_id": old_agent}


def resume_action(root: Path) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    if not state.get("active_job_id"):
        state["checkpoint_pending"] = False
        state["slice_started_at"] = _now()
        state["slice_jobs_completed"] = 0
        _event(root, "execution_slice_resumed")
        _persist(root, plan, state)
    return next_action(root)


def status(root: Path) -> dict[str, Any]:
    _assert_current_schema(root)
    plan = read_json(root / "execution_plan.json", {})
    state = read_json(root / "program_state.json", {})
    _persist(root, plan, state)
    counts: dict[str, int] = defaultdict(int)
    for job in plan.get("jobs", []):
        counts[str(job.get("status"))] += 1
    next_value = next_action(root)
    return {
        "state": read_json(root / "program_state.json", state),
        "job_counts": dict(counts),
        "next": next_value,
    }
