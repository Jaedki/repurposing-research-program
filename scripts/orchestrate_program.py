#!/usr/bin/env python3
"""Deterministic serial controller for repurposing research programmes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from build_context_packet import build_packet
from program_contract import (
    ALLOWED_EXCLUSION_REASONS,
    BROAD_DOMAINS,
    COUNCIL_STAGES,
    GLOBAL_PERSPECTIVES,
    LEDGER_KEYS,
    MAX_ACTIVE_JOBS,
    SCHEMA_VERSION,
)
from validate_program import validate_run, validate_staged_commit
COUNCIL_QUESTIONS = {
    "advocate_case": (
        "Build the strongest evidence-based case for screening, explicitly protect coherent novelty and indirect rescue, "
        "and enumerate every material claim with source IDs."
    ),
    "skeptic_review": (
        "Red-team the case using the complete mandatory checklist: mechanism direction, worm target and orthology, "
        "allele relevance, pharmacology and selectivity, exposure feasibility, and Tierpsy/behavioural confounding."
    ),
    "advocate_response": (
        "Answer or accept every sceptic challenge, remove claims that cannot be defended, and submit a revised causal case."
    ),
    "fact_audit": (
        "Do not debate. Independently retrieve and verify every decisive claim and citation against primary or "
        "authoritative sources, then report claim verdicts and surviving causal paths."
    ),
}
CONTROLLER_OWNED_UNIT_FIELDS = {
    "worker_agent_id",
    "auditor_agent_id",
    "status",
    "audit_status",
    "rate_limit_pending",
    "unresolved_repair_count",
}
FINAL_UNIT_STATUSES = {"audited_complete", "evidence_absent_complete"}
DEFAULT_SLICE_MINUTES = 25
DEFAULT_SLICE_PAIRS = 1
DEFAULT_TOKEN_BUDGET_PER_MINUTE = 300_000
DEFAULT_TOKEN_RESERVE_PER_AGENT = 50_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def _write_compact_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
    )
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_id(value: str) -> str:
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


def _estimated_packet_tokens(root: Path, job: dict[str, Any]) -> int:
    manifest = _read_json(root / str(job.get("packet_manifest_path", "")), {})
    packet_bytes = int(manifest.get("total_packet_bytes", 0))
    return max(1, (packet_bytes + 3) // 4)


def _pacing_action(root: Path, state: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc)
    events = [
        row for row in state.get("token_launches", [])
        if _parse_time(row.get("at")) and (now - _parse_time(row.get("at"))).total_seconds() < 60
    ]
    state["token_launches"] = events
    estimate = _estimated_packet_tokens(root, job) + int(
        state.get("token_reserve_per_agent", DEFAULT_TOKEN_RESERVE_PER_AGENT)
    )
    budget = int(state.get("token_budget_per_minute", DEFAULT_TOKEN_BUDGET_PER_MINUTE))
    if sum(int(row.get("estimated_tokens", 0)) for row in events) + estimate <= budget:
        return None
    if not events:
        return {
            "action": "blocked",
            "reason": "One job's estimated token envelope exceeds the configured per-minute safety budget.",
            "job_id": job.get("job_id"),
            "estimated_tokens": estimate,
            "token_budget_per_minute": budget,
        }
    first = min(_parse_time(row.get("at")) for row in events if _parse_time(row.get("at")))
    retry = first + timedelta(seconds=60)
    return {
        "action": "wait_for_pacing",
        "job_id": job.get("job_id"),
        "retry_not_before": retry.isoformat(),
        "estimated_packet_tokens": estimate,
        "rolling_estimated_tokens": sum(int(row.get("estimated_tokens", 0)) for row in events),
        "token_budget_per_minute": budget,
    }


def _slice_checkpoint_due(state: dict[str, Any]) -> bool:
    if state.get("checkpoint_pending") is True:
        return True
    if int(state.get("slice_jobs_started", 0)) < 1:
        return False
    started = _parse_time(state.get("slice_started_at"))
    elapsed_minutes = (
        (datetime.now(timezone.utc) - started).total_seconds() / 60 if started else 0
    )
    return (
        int(state.get("slice_completed_pairs", 0)) >= int(state.get("slice_max_pairs", DEFAULT_SLICE_PAIRS))
        or elapsed_minutes >= int(state.get("slice_max_minutes", DEFAULT_SLICE_MINUTES))
    )


def _job(
    job_id: str,
    *,
    phase: int,
    sequence: int,
    kind: str,
    role: str,
    question: str,
    depends_on: Iterable[str] = (),
    gate: str = "",
    unit_id: str = "",
    perspective: str = "",
    candidate_id: str = "",
    stage: str = "",
    context_scope: str = "case_only",
    paired_worker_job_id: str = "",
    include_dependency_results: bool = False,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "phase": phase,
        "sequence": sequence,
        "kind": kind,
        "role": role,
        "stage": stage,
        "unit_id": unit_id,
        "perspective": perspective,
        "candidate_id": candidate_id,
        "question": question,
        "completion_contract": (
            "Process every required packet chunk; preserve ambiguity; write only staged ledger updates; "
            "do not close while a documented decision-changing or searchable high-yield branch remains."
        ),
        "depends_on": list(depends_on),
        "gate": gate,
        "context_scope": context_scope,
        "include_dependency_results": include_dependency_results,
        "paired_worker_job_id": paired_worker_job_id,
        "status": "planned",
        "attempt_count": 0,
        "repair_round": 0,
        "packet_manifest_path": "",
        "packet_hash": "",
        "result_path": "",
        "result_hash": "",
        "retry_not_before": "",
        "repair_context_paths": [],
    }


def _unit(unit_id: str, unit_type: str, perspective: str, subtopic_id: str = "") -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "unit_type": unit_type,
        "subtopic_id": subtopic_id,
        "perspective": perspective,
        "worker_agent_id": "",
        "auditor_agent_id": "",
        "status": "planned",
        "audit_status": "pending",
        "planned_query_families": [],
        "completed_query_families": [],
        "independent_audit_query_ids": [],
        "rate_limit_pending": False,
        "known_high_yield_search_remaining": [],
        "unresolved_repair_count": 0,
        "candidate_ids": [],
        "absence_reason": "",
    }


def _add_pair(
    jobs: list[dict[str, Any]],
    *,
    prefix: str,
    phase: int,
    sequence: int,
    unit_id: str,
    perspective: str,
    question: str,
    depends_on: Iterable[str] = (),
    gate: str = "",
    context_scope: str = "case_only",
) -> tuple[str, str]:
    worker_id = f"{prefix}.worker"
    audit_id = f"{prefix}.audit"
    jobs.append(
        _job(
            worker_id,
            phase=phase,
            sequence=sequence,
            kind="research_worker",
            role="worker",
            unit_id=unit_id,
            perspective=perspective,
            question=question,
            depends_on=depends_on,
            gate=gate,
            context_scope=context_scope,
        )
    )
    jobs.append(
        _job(
            audit_id,
            phase=phase,
            sequence=sequence + 1,
            kind="unit_auditor",
            role="auditor",
            unit_id=unit_id,
            perspective=perspective,
            question=f"Independently fact-check, challenge and audit {unit_id}; repair every material defect.",
            depends_on=(worker_id,),
            gate=gate,
            context_scope=context_scope,
            paired_worker_job_id=worker_id,
            include_dependency_results=True,
        )
    )
    return worker_id, audit_id


def _initial_plan(case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    jobs: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    prior_audit = ""
    for index, perspective in enumerate(BROAD_DOMAINS, 1):
        unit_id = f"BE{index:02d}"
        units.append(_unit(unit_id, "broad_evidence", perspective))
        _, prior_audit = _add_pair(
            jobs,
            prefix=unit_id,
            phase=1,
            sequence=100 + index * 10,
            unit_id=unit_id,
            perspective=perspective,
            question=f"Build exhaustive broad evidence for the {perspective} domain without generating candidates.",
            depends_on=(prior_audit,) if prior_audit else (),
            context_scope="case_only",
        )

    closure_unit = "CLOSURE01"
    units.append(_unit(closure_unit, "closure_audit", "subtopic_closure"))
    jobs.append(
        _job(
            "CLOSURE01.worker",
            phase=2,
            sequence=2890,
            kind="closure_worker",
            role="worker",
            unit_id=closure_unit,
            perspective="subtopic_closure",
            question="Map unresolved branches and search for omitted material relations before proposing evidence closure.",
            depends_on=(prior_audit,),
            gate="broad_evidence_complete",
            context_scope="closure",
        )
    )
    jobs.append(
        _job(
            "CLOSURE01.audit",
            phase=2,
            sequence=2891,
            kind="closure_auditor",
            role="closure_auditor",
            unit_id=closure_unit,
            perspective="subtopic_closure",
            question="Independently search for omitted material relations and approve closure only when none remains.",
            depends_on=("CLOSURE01.worker",),
            gate="broad_evidence_complete",
            context_scope="closure",
            paired_worker_job_id="CLOSURE01.worker",
            include_dependency_results=True,
        )
    )

    perspectives = list(GLOBAL_PERSPECTIVES)
    has_prior_screen = bool(case.get("prior_screen_path") or case.get("prior_screen_rows"))
    if has_prior_screen and str(case.get("benchmark_mode", "")).casefold() != "blinded":
        perspectives.append("prior_screen_context")
    if case.get("wt_behavioural_parameters") or case.get("disease_model_behavioural_parameters"):
        perspectives.append("behavioural_data_first")
    prior_audit = ""
    for index, perspective in enumerate(perspectives, 1):
        unit_id = f"GP{index:02d}"
        units.append(_unit(unit_id, "global_perspective", perspective))
        _, prior_audit = _add_pair(
            jobs,
            prefix=unit_id,
            phase=3,
            sequence=4000 + index * 10,
            unit_id=unit_id,
            perspective=perspective,
            question=f"Conduct independent exact-compound discovery from the {perspective} perspective.",
            depends_on=(prior_audit,) if prior_audit else (),
            gate="subtopic_closure_complete",
            context_scope="case_evidence",
        )

    merge_worker, _ = _add_pair(
        jobs,
        prefix="MERGE01",
        phase=4,
        sequence=5000,
        unit_id="",
        perspective="identity_merge",
        question="Resolve exact chemical identities and merge candidate synonyms without scientific pre-pruning.",
        depends_on=(prior_audit,) if prior_audit else (),
        gate="de_novo_perspectives_complete",
        context_scope="case_evidence",
    )
    jobs[-1]["kind"] = "merge_auditor"
    jobs[-1]["question"] = "Audit exact identity resolution and the completeness of the merged candidate universe."
    jobs[-1]["paired_worker_job_id"] = merge_worker

    return (
        {
            "schema_version": SCHEMA_VERSION,
            "max_active_jobs": MAX_ACTIVE_JOBS,
            "fixed_seed_topology": True,
            "jobs": jobs,
            "role_agents": {},
            "next_dynamic_sequence": 2000,
            "created_at": _now(),
        },
        units,
    )


def _job_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(job["job_id"]): job for job in plan.get("jobs", [])}


def _role_key(job: dict[str, Any]) -> str:
    if job.get("candidate_id"):
        return f"council:{job.get('candidate_id')}:{job.get('role')}"
    if job.get("unit_id"):
        return f"unit:{job.get('unit_id')}:{job.get('role')}"
    return f"job:{job.get('job_id')}:{job.get('role')}"


def _gate_open(state: dict[str, Any], gate: str) -> bool:
    return not gate or state.get(gate) is True


def _dependencies_complete(job: dict[str, Any], jobs: dict[str, dict[str, Any]]) -> bool:
    return all(jobs.get(str(value), {}).get("status") == "complete" for value in job.get("depends_on", []))


def _retry_elapsed(job: dict[str, Any]) -> bool:
    value = str(job.get("retry_not_before", ""))
    if not value:
        return True
    return datetime.fromisoformat(value) <= datetime.now(timezone.utc)


def _phase_complete(plan: dict[str, Any], phase: int, *, exclude_kinds: set[str] | None = None) -> bool:
    exclude_kinds = exclude_kinds or set()
    rows = [
        job for job in plan.get("jobs", [])
        if job.get("phase") == phase and job.get("kind") not in exclude_kinds
    ]
    return bool(rows) and all(job.get("status") == "complete" for job in rows)


def _refresh_state(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    jobs = _job_map(plan)
    state["broad_evidence_complete"] = _phase_complete(plan, 1)
    phase_two_open = [
        job for job in plan.get("jobs", [])
        if job.get("phase") == 2 and job.get("kind") != "closure_auditor" and job.get("status") != "complete"
    ]
    if phase_two_open:
        state["subtopic_closure_complete"] = False
    state["de_novo_perspectives_complete"] = (
        state.get("subtopic_closure_complete") is True and _phase_complete(plan, 3)
    )
    state["candidate_universe_complete"] = _phase_complete(plan, 4)
    council_jobs = [job for job in plan.get("jobs", []) if job.get("phase") == 5]
    state["council_complete"] = (
        state.get("candidate_universe_complete") is True
        and all(job.get("status") == "complete" for job in council_jobs)
    )

    for job in plan.get("jobs", []):
        if job.get("status") == "retry_wait" and _retry_elapsed(job):
            job["status"] = "ready"
            job["retry_not_before"] = ""
        if job.get("status") != "planned":
            continue
        if not _gate_open(state, str(job.get("gate", ""))):
            continue
        if not _dependencies_complete(job, jobs):
            continue
        if job.get("kind") == "closure_auditor" and any(
            other.get("phase") == 2
            and other.get("kind") != "closure_auditor"
            and other.get("status") != "complete"
            for other in plan.get("jobs", [])
        ):
            continue
        job["status"] = "ready"

    incomplete = sorted(
        [job for job in plan.get("jobs", []) if job.get("status") != "complete"],
        key=lambda row: (int(row.get("phase", 99)), int(row.get("sequence", 999999)), str(row.get("job_id"))),
    )
    if state.get("blocked_reason"):
        state["current_phase"] = "blocked"
    elif not incomplete and state.get("council_complete") is True:
        state["current_phase"] = "ready_for_finalization"
    elif incomplete:
        state["current_phase"] = f"phase_{incomplete[0].get('phase')}"
    else:
        state["current_phase"] = "phase_0"
    state["earliest_ready_job_id"] = next(
        (str(job.get("job_id")) for job in incomplete if job.get("status") == "ready"),
        "",
    )
    state["updated_at"] = _now()


def _persist(root: Path, plan: dict[str, Any], state: dict[str, Any]) -> None:
    _refresh_state(root, plan, state)
    _write_json(root / "execution_plan.json", plan)
    _write_json(root / "program_state.json", state)


def initialize(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Run folder is not empty: {root}")
    for required in ("human_gene", "worm_gene", "allele_mode"):
        if not str(case.get(required, "")).strip():
            raise ValueError(f"Missing required case field: {required}")
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("dossiers", "packets", "staging", "raw_sources"):
        (root / directory).mkdir()
    plan, units = _initial_plan(case)
    state = {
        "schema_version": SCHEMA_VERSION,
        "current_phase": "phase_1",
        "max_active_jobs": MAX_ACTIVE_JOBS,
            "active_job_id": "",
            "active_attempt_id": "",
            "pending_agent_release_id": "",
            "pending_agent_release_attempt_id": "",
        "broad_evidence_complete": False,
        "subtopic_closure_complete": False,
        "de_novo_perspectives_complete": False,
        "candidate_universe_complete": False,
        "council_complete": False,
        "blocked_reason": "",
        "created_at": _now(),
        "slice_started_at": _now(),
        "slice_jobs_started": 0,
        "slice_completed_pairs": 0,
        "slice_max_minutes": DEFAULT_SLICE_MINUTES,
        "slice_max_pairs": DEFAULT_SLICE_PAIRS,
        "checkpoint_pending": False,
        "token_budget_per_minute": DEFAULT_TOKEN_BUDGET_PER_MINUTE,
        "token_reserve_per_agent": DEFAULT_TOKEN_RESERVE_PER_AGENT,
        "token_launches": [],
        "rate_limit_strikes": 0,
    }
    _write_json(root / "case.json", case)
    _write_jsonl(root / "research_units.jsonl", units)
    for filename in LEDGER_KEYS:
        if filename != "research_units.jsonl":
            _write_jsonl(root / filename, [])
    _write_jsonl(root / "job_attempts.jsonl", [])
    _write_jsonl(
        root / "orchestration.jsonl",
        [{"event_id": "EV000001", "event": "initialized", "status": "complete", "at": _now(), "rate_limit_pending": False}],
    )
    _persist(root, plan, state)
    return state


def _event(root: Path, event: str, **details: Any) -> None:
    existing = _read_jsonl(root / "orchestration.jsonl")
    _append_jsonl(
        root / "orchestration.jsonl",
        {
            "event_id": f"EV{len(existing) + 1:06d}",
            "event": event,
            "at": _now(),
            "status": details.pop("status", "complete"),
            "rate_limit_pending": details.pop("rate_limit_pending", False),
            **details,
        },
    )


def _upsert_rows(path: Path, rows: list[dict[str, Any]], key: str) -> None:
    current = _read_jsonl(path)
    index = {str(row.get(key)): position for position, row in enumerate(current)}
    for row in rows:
        identity = str(row.get(key, "")).strip()
        if not identity:
            raise ValueError(f"{path.name} update lacks {key}")
        if identity in index:
            position = index[identity]
            if path.name == "research_units.jsonl":
                protected = {
                    field: current[position].get(field)
                    for field in CONTROLLER_OWNED_UNIT_FIELDS
                }
                current[position] = {**current[position], **row, **protected}
            else:
                current[position] = row
        else:
            index[identity] = len(current)
            current.append(row)
    _write_jsonl(path, current)


def _merge_result(root: Path, result: dict[str, Any]) -> None:
    updates = result.get("ledger_updates", {})
    if not isinstance(updates, dict):
        raise ValueError("ledger_updates must be an object")
    for filename, rows in updates.items():
        if filename not in LEDGER_KEYS:
            raise ValueError(f"Unapproved ledger update: {filename}")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"{filename} updates must be a list of objects")
        _upsert_rows(root / filename, rows, LEDGER_KEYS[filename])


def _load_result(root: Path, relative_path: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    path.relative_to(root.resolve())
    value = _read_json(path, None)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid result object: {relative_path}")
    return value


def _reset_job(job: dict[str, Any], status: str = "planned") -> None:
    job.update(
        status=status,
        packet_manifest_path="",
        packet_hash="",
        result_path="",
        result_hash="",
        retry_not_before="",
    )


def _register_subtopics(
    root: Path,
    plan: dict[str, Any],
    specs: list[dict[str, Any]],
    discovered_by_job_id: str,
) -> None:
    if not specs:
        return
    jobs = _job_map(plan)
    units = _read_jsonl(root / "research_units.jsonl")
    unit_ids = {str(row.get("unit_id")) for row in units}
    subtopics = _read_jsonl(root / "subtopic_registry.jsonl")
    subtopic_by_id = {str(row.get("subtopic_id")): row for row in subtopics}
    broad_audit = "BE10.audit"
    for spec in specs:
        subtopic_id = _safe_id(str(spec.get("subtopic_id", "")))
        for field in ("name", "relation_to_case"):
            if not str(spec.get(field, "")).strip():
                raise ValueError(f"Subtopic {subtopic_id} lacks {field}")
        evidence_unit = f"SE.{subtopic_id}"
        compound_unit = f"SC.{subtopic_id}"
        existing_subtopic = subtopic_by_id.get(subtopic_id)
        requested_candidate_relevant = spec.get("candidate_relevant") is True
        promoted = bool(
            existing_subtopic
            and requested_candidate_relevant
            and existing_subtopic.get("candidate_relevant") is not True
        )
        changed = False
        if existing_subtopic:
            replacements = {
                "parent_id": str(spec.get("parent_id", existing_subtopic.get("parent_id", ""))),
                "name": str(spec["name"]),
                "relation_to_case": str(spec["relation_to_case"]),
                "depth": int(spec.get("depth", existing_subtopic.get("depth", 0))),
            }
            changed = any(existing_subtopic.get(field) != value for field, value in replacements.items())
            if not changed and not promoted:
                continue
            existing_subtopic.update(replacements)
            existing_subtopic["candidate_relevant"] = (
                existing_subtopic.get("candidate_relevant") is True or requested_candidate_relevant
            )
            existing_subtopic["status"] = "planned"
            existing_subtopic["closure_reason"] = ""
            required = [evidence_unit]
            if existing_subtopic["candidate_relevant"]:
                required.append(compound_unit)
            existing_subtopic["required_research_unit_ids"] = required
        else:
            required = [evidence_unit]
            if requested_candidate_relevant:
                required.append(compound_unit)
            existing_subtopic = {
                "subtopic_id": subtopic_id,
                "parent_id": str(spec.get("parent_id", "")),
                "name": str(spec["name"]),
                "relation_to_case": str(spec["relation_to_case"]),
                "depth": int(spec.get("depth", 0)),
                "discovered_by": discovered_by_job_id,
                "candidate_relevant": requested_candidate_relevant,
                "required_research_unit_ids": required,
                "status": "planned",
                "closure_reason": "",
            }
            subtopics.append(existing_subtopic)
            subtopic_by_id[subtopic_id] = existing_subtopic
        sequence = int(plan.get("next_dynamic_sequence", 2000))
        plan["next_dynamic_sequence"] = sequence + 10
        if evidence_unit not in unit_ids:
            units.append(_unit(evidence_unit, "subtopic_evidence", "relation_evidence", subtopic_id))
            unit_ids.add(evidence_unit)
        if f"{evidence_unit}.worker" not in jobs:
            _add_pair(
                plan["jobs"],
                prefix=evidence_unit,
                phase=2,
                sequence=sequence,
                unit_id=evidence_unit,
                perspective="relation_evidence",
                question=f"Resolve the complete evidence base for subtopic {subtopic_id}: {spec['name']}.",
                depends_on=(broad_audit,),
                gate="broad_evidence_complete",
                context_scope="subtopic",
            )
        elif changed or promoted:
            _reset_job(jobs[f"{evidence_unit}.worker"], "ready")
            _reset_job(jobs[f"{evidence_unit}.audit"], "planned")
            for unit in units:
                if str(unit.get("unit_id")) == evidence_unit:
                    unit["status"] = "planned"
                    unit["audit_status"] = "pending"
                    unit["independent_audit_query_ids"] = []
        if existing_subtopic.get("candidate_relevant") is True:
            if compound_unit not in unit_ids:
                units.append(_unit(compound_unit, "subtopic_compound", "relation_compounds", subtopic_id))
                unit_ids.add(compound_unit)
            if f"{compound_unit}.worker" not in jobs:
                _add_pair(
                    plan["jobs"],
                    prefix=compound_unit,
                    phase=3,
                    sequence=3000 + (sequence - 2000),
                    unit_id=compound_unit,
                    perspective="relation_compounds",
                    question=f"Find every exact compound with a directional rescue path through subtopic {subtopic_id}.",
                    gate="subtopic_closure_complete",
                    context_scope="subtopic",
                )
        jobs = _job_map(plan)
    _write_jsonl(root / "subtopic_registry.jsonl", subtopics)
    _write_jsonl(root / "research_units.jsonl", units)
    refreshed_jobs = _job_map(plan)
    for closure_id in ("CLOSURE01.worker", "CLOSURE01.audit"):
        closure = refreshed_jobs.get(closure_id)
        if closure:
            _reset_job(closure)


def _register_council(plan: dict[str, Any], candidate_ids: list[str]) -> None:
    existing = _job_map(plan)
    for candidate_index, candidate_id in enumerate(sorted(set(candidate_ids)), 1):
        safe_candidate = _safe_id(candidate_id)
        prior = ""
        for stage_index, (stage, role) in enumerate(COUNCIL_STAGES, 1):
            job_id = f"COUNCIL.{safe_candidate}.{stage}"
            if job_id in existing:
                prior = job_id
                continue
            job = _job(
                job_id,
                phase=5,
                sequence=6000 + candidate_index * 100 + stage_index,
                kind="council_fact_auditor" if stage == "fact_audit" else "council_turn",
                role=role,
                stage=stage,
                candidate_id=candidate_id,
                question=f"For exact compound {candidate_id}: {COUNCIL_QUESTIONS[stage]}",
                depends_on=(prior,) if prior else ("MERGE01.audit",),
                gate="candidate_universe_complete",
                context_scope="candidate",
                include_dependency_results=True,
            )
            plan["jobs"].append(job)
            existing[job_id] = job
            prior = job_id


def _register_final_repair(
    root: Path, plan: dict[str, Any], state: dict[str, Any], errors: list[str]
) -> bool:
    error_hash = hashlib.sha256("\n".join(sorted(errors)).encode("utf-8")).hexdigest()
    history = plan.setdefault("final_validation_history", [])
    unchanged_count = 1
    if history and history[-1].get("error_hash") == error_hash:
        unchanged_count = int(history[-1].get("unchanged_count", 1)) + 1
    history.append(
        {
            "round": len(history) + 1,
            "error_hash": error_hash,
            "unchanged_count": unchanged_count,
            "error_count": len(errors),
            "at": _now(),
        }
    )
    if unchanged_count >= 3:
        state["blocked_reason"] = (
            "Final validation produced the same unresolved defects after three audited repair rounds."
        )
        _event(root, "final_validation_blocked", error_hash=error_hash, error_count=len(errors))
        return False

    round_number = len(history)
    errors_path = root / f"final_validation_errors_round{round_number:03d}.json"
    _write_compact_json(
        errors_path, {"schema_version": SCHEMA_VERSION, "error_hash": error_hash, "errors": errors}
    )
    prefix = f"FINAL_REPAIR{round_number:03d}"
    worker_id, audit_id = _add_pair(
        plan["jobs"],
        prefix=prefix,
        phase=6,
        sequence=9000 + round_number * 10,
        unit_id="",
        perspective="final_validation_repair",
        question="Repair every deterministic final-validation defect without weakening scientific requirements.",
        context_scope="final_validation",
    )
    jobs = _job_map(plan)
    jobs[worker_id]["kind"] = "final_repair_worker"
    jobs[audit_id]["kind"] = "final_repair_auditor"
    for job_id in (worker_id, audit_id):
        jobs[job_id]["validation_errors_path"] = str(errors_path.relative_to(root))
    jobs[audit_id]["question"] = (
        "Independently verify every final-validation repair and reject any bypass, deletion, or weakened requirement."
    )
    _event(root, "final_validation_repair_registered", round=round_number, error_hash=error_hash)
    return True


def next_action(root: Path) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    _persist(root, plan, state)
    if state.get("pending_agent_release_id"):
        return {
            "action": "close_agent",
            "agent_id": state["pending_agent_release_id"],
            "attempt_id": state.get("pending_agent_release_attempt_id"),
            "then_command": "release",
        }
    if state.get("active_job_id"):
        return {
            "action": "resume_active_job",
            "job_id": state["active_job_id"],
            "attempt_id": state.get("active_attempt_id"),
        }
    if state.get("blocked_reason"):
        return {"action": "blocked", "reason": state["blocked_reason"]}
    if _slice_checkpoint_due(state):
        state["checkpoint_pending"] = True
        _persist(root, plan, state)
        return {
            "action": "checkpoint",
            "reason": "bounded_execution_slice_complete",
            "completed_pairs": int(state.get("slice_completed_pairs", 0)),
            "slice_started_at": state.get("slice_started_at"),
            "resume_command": "resume",
        }
    jobs = sorted(
        [job for job in plan.get("jobs", []) if job.get("status") != "complete"],
        key=lambda row: (int(row.get("phase", 99)), int(row.get("sequence", 999999)), str(row.get("job_id"))),
    )
    if not jobs:
        final_errors = validate_run(root)
        if final_errors:
            _register_final_repair(root, plan, state, final_errors)
            _persist(root, plan, state)
            if state.get("blocked_reason"):
                return {"action": "blocked", "reason": state["blocked_reason"], "validation_errors": final_errors}
            return next_action(root)
        return {"action": "finalize", "current_phase": state.get("current_phase")}
    earliest = jobs[0]
    if earliest.get("status") == "retry_wait":
        return {
            "action": "wait_for_retry",
            "job_id": earliest["job_id"],
            "retry_not_before": earliest.get("retry_not_before"),
        }
    ready = earliest if earliest.get("status") == "ready" else None
    if ready is None:
        return {"action": "blocked_by_dependencies", "earliest_job": earliest["job_id"]}
    if ready.get("packet_manifest_path"):
        manifest_path = root / str(ready["packet_manifest_path"])
        if not manifest_path.is_file() or _read_json(manifest_path, {}).get("packet_hash") != ready.get("packet_hash"):
            raise ValueError(f"Packet integrity failure for {ready['job_id']}")
    else:
        manifest, packet_hash = build_packet(root, str(ready["job_id"]))
        ready["packet_manifest_path"] = str(manifest.relative_to(root))
        ready["packet_hash"] = packet_hash
        _persist(root, plan, state)
    pacing = _pacing_action(root, state, ready)
    if pacing:
        _persist(root, plan, state)
        return pacing
    assigned_agent_id = str(plan.get("role_agents", {}).get(_role_key(ready), ""))
    next_attempt = int(ready.get("attempt_count", 0)) + 1
    expected_result = root / "staging" / f"{_safe_id(str(ready['job_id']))}.attempt{next_attempt:03d}" / "result.json"
    absolute_manifest = (root / str(ready["packet_manifest_path"])).resolve()
    spawn_prompt = (
        f"Job ID: {ready['job_id']}\n"
        f"Packet manifest: {absolute_manifest}\n"
        f"Expected result: {expected_result.resolve()}"
    )
    return {
        "action": "start_agent",
        "job_id": ready["job_id"],
        "role": ready["role"],
        "stage": ready.get("stage"),
        "unit_id": ready.get("unit_id"),
        "candidate_id": ready.get("candidate_id"),
        "packet_manifest_path": ready["packet_manifest_path"],
        "packet_hash": ready["packet_hash"],
        "expected_result_path": str(expected_result.relative_to(root)),
        "spawn_prompt": spawn_prompt,
        "spawn_contract": {"fork_turns": "none", "prompt_fields": ["job_id", "packet_manifest", "expected_result"]},
        "concurrency": 1,
        "agent_action": "resume_assigned" if assigned_agent_id else "spawn_new",
        "assigned_agent_id": assigned_agent_id,
        "close_after_controller_acknowledgement": True,
    }


def start_job(root: Path, job_id: str, agent_id: str) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    jobs = _job_map(plan)
    job = jobs.get(job_id)
    if not job:
        raise ValueError(f"Unknown job: {job_id}")
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    recoverable = next(
        (
            row for row in reversed(attempts)
            if str(row.get("job_id")) == job_id
            and row.get("status") == "running"
            and row.get("release_acknowledged") is not True
        ),
        None,
    )
    if recoverable:
        if str(recoverable.get("agent_id")) != agent_id:
            raise ValueError(f"Interrupted start is reserved to agent {recoverable.get('agent_id')}")
        if str(recoverable.get("packet_hash")) != str(job.get("packet_hash")):
            raise ValueError("Interrupted start packet hash disagrees with the current job")
        role_key = _role_key(job)
        plan.setdefault("role_agents", {})[role_key] = agent_id
        attempt_id = str(recoverable["attempt_id"])
        attempt_number = int(attempt_id.rsplit("attempt", 1)[-1])
        job["attempt_count"] = max(int(job.get("attempt_count", 0)), attempt_number)
        job["status"] = "running"
        state["active_job_id"] = job_id
        state["active_attempt_id"] = attempt_id
        (root / str(recoverable["expected_result_path"])).parent.mkdir(parents=True, exist_ok=True)
        units = _read_jsonl(root / "research_units.jsonl")
        for unit in units:
            if str(unit.get("unit_id")) == str(job.get("unit_id")):
                field = "auditor_agent_id" if job.get("role") in {"auditor", "closure_auditor"} else "worker_agent_id"
                unit[field] = agent_id
        _write_jsonl(root / "research_units.jsonl", units)
        _event(root, "job_start_recovered", job_id=job_id, attempt_id=attempt_id, agent_id=agent_id)
        _persist(root, plan, state)
        return {**recoverable, "all_packet_chunks_required": True, "recovered_interrupted_start": True}
    if state.get("active_job_id"):
        raise ValueError(f"Another job is active: {state['active_job_id']}")
    if state.get("pending_agent_release_id"):
        raise ValueError(f"Close and release agent {state['pending_agent_release_id']} before starting another job")
    if job.get("status") != "ready":
        raise ValueError(f"Job is not ready: {job_id} ({job.get('status')})")
    if not str(job.get("packet_hash", "")):
        raise ValueError("Run next before start so the immutable packet exists")

    role_key = _role_key(job)
    role_agents = plan.setdefault("role_agents", {})
    assigned = str(role_agents.get(role_key, ""))
    if assigned and assigned != agent_id:
        raise ValueError(f"Role {role_key} is already assigned to agent {assigned}")
    other_role = next((key for key, value in role_agents.items() if value == agent_id and key != role_key), "")
    if other_role:
        raise ValueError(f"Agent {agent_id} is already assigned to independent role {other_role}")
    role_agents[role_key] = agent_id

    job["attempt_count"] = int(job.get("attempt_count", 0)) + 1
    attempt_id = f"{_safe_id(job_id)}.attempt{job['attempt_count']:03d}"
    staging_dir = root / "staging" / attempt_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    expected_result = staging_dir / "result.json"
    attempt = {
        "attempt_id": attempt_id,
        "job_id": job_id,
        "agent_id": agent_id,
        "packet_hash": job["packet_hash"],
        "packet_manifest_path": job["packet_manifest_path"],
        "expected_result_path": str(expected_result.relative_to(root)),
        "status": "running",
        "started_at": _now(),
        "finished_at": "",
        "failure_kind": "",
        "release_acknowledged": False,
        "released_at": "",
    }
    _append_jsonl(root / "job_attempts.jsonl", attempt)
    now = datetime.now(timezone.utc)
    state["token_launches"] = [
        row for row in state.get("token_launches", [])
        if _parse_time(row.get("at")) and (now - _parse_time(row.get("at"))).total_seconds() < 60
    ]
    state["token_launches"].append(
        {
            "at": _now(),
            "job_id": job_id,
            "estimated_tokens": _estimated_packet_tokens(root, job)
            + int(state.get("token_reserve_per_agent", DEFAULT_TOKEN_RESERVE_PER_AGENT)),
        }
    )
    state["slice_jobs_started"] = int(state.get("slice_jobs_started", 0)) + 1
    job["status"] = "running"
    state["active_job_id"] = job_id
    state["active_attempt_id"] = attempt_id

    units = _read_jsonl(root / "research_units.jsonl")
    for unit in units:
        if str(unit.get("unit_id")) != str(job.get("unit_id")):
            continue
        field = "auditor_agent_id" if job.get("role") in {"auditor", "closure_auditor"} else "worker_agent_id"
        unit[field] = agent_id
    _write_jsonl(root / "research_units.jsonl", units)
    _event(root, "job_started", job_id=job_id, attempt_id=attempt_id, agent_id=agent_id)
    _persist(root, plan, state)
    return {**attempt, "all_packet_chunks_required": True}


def validate_result(root: Path, job_id: str, result_path: str | None = None) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    jobs = _job_map(plan)
    job = jobs.get(job_id)
    if not job or job.get("status") != "running" or state.get("active_job_id") != job_id:
        raise ValueError(f"Job is not the active running job: {job_id}")
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    attempt = next(
        (row for row in attempts if str(row.get("attempt_id")) == str(state.get("active_attempt_id"))),
        None,
    )
    if not attempt:
        raise ValueError("Active attempt record is missing")
    relative_result = result_path or str(attempt["expected_result_path"])
    result = _load_result(root, relative_result)
    errors: list[str] = []
    if str(result.get("job_id")) != job_id:
        errors.append("Result job_id does not match active job")
    if str(result.get("packet_hash")) != str(job.get("packet_hash")):
        errors.append("Result packet_hash does not match immutable job packet")
    if result.get("all_chunks_processed") is not True:
        errors.append("Result must confirm all required packet chunks were processed")
    outcome = str(result.get("outcome", ""))
    allowed = {"completed"} if job.get("kind") in {
        "research_worker", "closure_worker", "council_turn", "final_repair_worker"
    } else {"verified", "repair_required", "evidence_absent_complete"}
    if outcome not in allowed:
        errors.append(f"Invalid outcome {outcome!r} for {job.get('kind')}")
    if not errors and outcome != "repair_required":
        staged_paths = _result_paths_for_commit(root, plan, job, relative_result)
        errors.extend(validate_staged_commit(root, job, staged_paths, str(attempt.get("agent_id", ""))))
    if errors:
        return {"status": "invalid", "error_count": len(errors), "errors": errors, "result_path": relative_result}
    result_file = (root / relative_result).resolve()
    result_file.relative_to(root.resolve())
    _write_compact_json(result_file, result)
    return {
        "status": "valid",
        "job_id": job_id,
        "result_path": str(result_file.relative_to(root)),
        "minified_bytes": result_file.stat().st_size,
    }


def recover_ready(
    root: Path,
    job_id: str,
    new_agent_id: str,
    reason: str = "assigned repair task unavailable",
) -> dict[str, Any]:
    """Reassign one ready repair job without changing its immutable scientific context."""
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    if state.get("active_job_id"):
        raise ValueError(f"Another job is active: {state['active_job_id']}")
    if state.get("pending_agent_release_id"):
        raise ValueError(
            f"Close and release agent {state['pending_agent_release_id']} before recovering a ready repair"
        )
    jobs = _job_map(plan)
    job = jobs.get(job_id)
    if not job:
        raise ValueError(f"Unknown job: {job_id}")
    if job.get("status") != "ready":
        raise ValueError(f"Job is not a ready repair: {job_id} ({job.get('status')})")
    repair_context = job.get("repair_context_paths", [])
    paired_worker = jobs.get(str(job.get("paired_worker_job_id", "")))
    effective_repair_round = max(
        int(job.get("repair_round", 0)),
        int(paired_worker.get("repair_round", 0)) if paired_worker else 0,
    )
    effective_repair_context = repair_context
    if not effective_repair_context and paired_worker:
        effective_repair_context = paired_worker.get("repair_context_paths", [])
    if (
        effective_repair_round < 1
        or not isinstance(effective_repair_context, list)
        or not effective_repair_context
        or (paired_worker is not None and paired_worker.get("status") != "complete")
    ):
        raise ValueError(f"Job is not a ready repair with mandatory feedback: {job_id}")
    new_agent_id = new_agent_id.strip()
    if not new_agent_id:
        raise ValueError("New agent ID is required")
    role_key = _role_key(job)
    role_agents = plan.setdefault("role_agents", {})
    old_agent_id = str(role_agents.get(role_key, ""))
    if not old_agent_id:
        raise ValueError(f"Ready repair role has no prior agent assignment: {role_key}")
    if old_agent_id == new_agent_id:
        raise ValueError("Replacement agent must differ from the unavailable prior agent")
    conflicting_role = next(
        (key for key, value in role_agents.items() if key != role_key and str(value) == new_agent_id),
        "",
    )
    if conflicting_role:
        raise ValueError(f"Agent {new_agent_id} is already assigned to independent role {conflicting_role}")
    for unit in _read_jsonl(root / "research_units.jsonl"):
        if str(unit.get("unit_id")) != str(job.get("unit_id")):
            continue
        independent_field = (
            "worker_agent_id"
            if job.get("role") in {"auditor", "closure_auditor"}
            else "auditor_agent_id"
        )
        if str(unit.get(independent_field, "")) == new_agent_id:
            raise ValueError(
                f"Agent {new_agent_id} is recorded as the independent {independent_field} for {job.get('unit_id')}"
            )
    packet_relative = str(job.get("packet_manifest_path", ""))
    packet_hash = str(job.get("packet_hash", ""))
    packet_path = (root / packet_relative).resolve()
    packet_path.relative_to(root.resolve())
    if not packet_relative or not packet_hash or not packet_path.is_file():
        raise ValueError("Ready repair does not have an immutable packet to preserve")
    if str(_read_json(packet_path, {}).get("packet_hash", "")) != packet_hash:
        raise ValueError(f"Packet integrity failure for {job_id}")
    for feedback in effective_repair_context:
        if not isinstance(feedback, dict):
            raise ValueError("Repair feedback entry is malformed")
        feedback_relative = str(feedback.get("result_path", ""))
        feedback_hash = str(feedback.get("result_hash", ""))
        feedback_path = (root / feedback_relative).resolve()
        feedback_path.relative_to(root.resolve())
        if not feedback_relative or not feedback_hash or not feedback_path.is_file():
            raise ValueError("Mandatory repair feedback is missing")
        if _hash_file(feedback_path) != feedback_hash:
            raise ValueError("Mandatory repair feedback hash mismatch")

    role_agents[role_key] = new_agent_id
    _event(
        root,
        "ready_repair_reassigned",
        job_id=job_id,
        prior_agent_id=old_agent_id,
        new_agent_id=new_agent_id,
        reason=reason,
        packet_hash=packet_hash,
        repair_round=effective_repair_round,
        repair_feedback_count=len(effective_repair_context),
    )
    _persist(root, plan, state)
    return {
        "status": "reassigned",
        "job_id": job_id,
        "prior_agent_id": old_agent_id,
        "new_agent_id": new_agent_id,
        "packet_manifest_path": packet_relative,
        "packet_hash": packet_hash,
        "repair_round": effective_repair_round,
        "repair_context_paths": effective_repair_context,
        "next": next_action(root),
    }


def recover_active(root: Path, new_agent_id: str, reason: str = "assigned task unavailable") -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    job_id = str(state.get("active_job_id", ""))
    attempt_id = str(state.get("active_attempt_id", ""))
    if not job_id or not attempt_id:
        raise ValueError("No active job is available for recovery")
    job = _job_map(plan).get(job_id)
    if not job or job.get("status") != "running":
        raise ValueError("Active job state is inconsistent")
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    orphan = next((row for row in attempts if str(row.get("attempt_id")) == attempt_id), None)
    if not orphan or orphan.get("status") != "running":
        raise ValueError("Active attempt is not recoverable")
    old_agent_id = str(orphan.get("agent_id", ""))
    orphan["status"] = "orphaned"
    orphan["finished_at"] = _now()
    orphan["failure_kind"] = "orphaned_agent"
    orphan["release_acknowledged"] = True
    orphan["released_at"] = _now()
    orphan["recovery_reason"] = reason
    _write_jsonl(root / "job_attempts.jsonl", attempts)
    role_key = _role_key(job)
    if str(plan.setdefault("role_agents", {}).get(role_key, "")) == old_agent_id:
        plan["role_agents"].pop(role_key, None)
    job["status"] = "ready"
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    _event(root, "active_job_recovered", job_id=job_id, orphaned_agent_id=old_agent_id, new_agent_id=new_agent_id)
    _persist(root, plan, state)
    replacement = start_job(root, job_id, new_agent_id)
    return {**replacement, "recovered_from_attempt_id": attempt_id, "orphaned_agent_id": old_agent_id}


def _finish_attempt(root: Path, attempt_id: str, status: str, failure_kind: str = "") -> None:
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    found = False
    for attempt in attempts:
        if str(attempt.get("attempt_id")) == attempt_id:
            attempt["status"] = status
            attempt["finished_at"] = _now()
            attempt["failure_kind"] = failure_kind
            found = True
    if not found:
        raise ValueError(f"Unknown attempt: {attempt_id}")
    _write_jsonl(root / "job_attempts.jsonl", attempts)


def release_agent(root: Path, attempt_id: str, agent_id: str) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    if str(state.get("pending_agent_release_attempt_id", "")) != attempt_id:
        raise ValueError(f"Attempt is not awaiting agent release: {attempt_id}")
    if str(state.get("pending_agent_release_id", "")) != agent_id:
        raise ValueError(f"Agent release mismatch: expected {state.get('pending_agent_release_id')}")
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    found = False
    for attempt in attempts:
        if str(attempt.get("attempt_id")) == attempt_id:
            attempt["release_acknowledged"] = True
            attempt["released_at"] = _now()
            found = True
    if not found:
        raise ValueError(f"Unknown attempt: {attempt_id}")
    _write_jsonl(root / "job_attempts.jsonl", attempts)
    state["pending_agent_release_id"] = ""
    state["pending_agent_release_attempt_id"] = ""
    _event(root, "agent_released", attempt_id=attempt_id, agent_id=agent_id)
    _persist(root, plan, state)
    return {"status": "released", "next": next_action(root)}


def _result_paths_for_commit(root: Path, plan: dict[str, Any], job: dict[str, Any], result_path: str) -> list[str]:
    """Return the complete, ordered staged-result lineage for a commit.

    A worker repair is a delta over its earlier worker output.  Its audit must
    therefore validate and merge the earlier result(s) as well as the repair,
    rather than asking an auditor to recreate worker-owned search records.
    """
    paths: list[str] = []
    jobs = _job_map(plan)

    def add_repair_lineage(candidate: dict[str, Any]) -> None:
        for context in candidate.get("repair_context_paths", []):
            if not isinstance(context, dict):
                continue
            path = str(context.get("result_path", ""))
            if path and path not in paths:
                paths.append(path)

    paired = str(job.get("paired_worker_job_id", ""))
    if paired and jobs.get(paired):
        add_repair_lineage(jobs[paired])
        if str(jobs[paired].get("result_path", "")):
            paths.append(str(jobs[paired]["result_path"]))
    if job.get("kind") == "council_fact_auditor":
        paths.extend(
            str(row.get("result_path"))
            for row in sorted(plan.get("jobs", []), key=lambda value: int(value.get("sequence", 0)))
            if row.get("candidate_id") == job.get("candidate_id")
            and row.get("status") == "complete"
            and row.get("result_path")
        )
    add_repair_lineage(job)
    paths.append(result_path)
    return list(dict.fromkeys(paths))


def _set_unit_complete(root: Path, unit_id: str, result: dict[str, Any]) -> None:
    if not unit_id:
        return
    status = str(result.get("unit_status", "audited_complete"))
    if status not in FINAL_UNIT_STATUSES:
        raise ValueError(f"Invalid unit_status: {status}")
    units = _read_jsonl(root / "research_units.jsonl")
    for unit in units:
        if str(unit.get("unit_id")) == unit_id:
            unit["status"] = status
            unit["audit_status"] = "verified"
            unit["rate_limit_pending"] = False
            unit["known_high_yield_search_remaining"] = []
            unit["unresolved_repair_count"] = 0
            if status == "evidence_absent_complete":
                unit["absence_reason"] = str(result.get("absence_reason", ""))
    _write_jsonl(root / "research_units.jsonl", units)


def _apply_council_disposition(
    root: Path, candidate_id: str, result: dict[str, Any], fact_auditor_agent_id: str
) -> str:
    claims = {str(row.get("claim_id")): row for row in _read_jsonl(root / "claim_ledger.jsonl")}
    sources = {str(row.get("source_id")): row for row in _read_jsonl(root / "source_corpus.jsonl")}
    candidates = _read_jsonl(root / "candidate_records.jsonl")
    candidate = next((row for row in candidates if str(row.get("candidate_id")) == candidate_id), None)
    if candidate is None:
        raise ValueError(f"Council candidate not found: {candidate_id}")
    exchanges = [
        row
        for row in _read_jsonl(root / "council_exchanges.jsonl")
        if str(row.get("candidate_id")) == candidate_id
    ]
    cases = [row for row in exchanges if row.get("role") == "advocate" and row.get("exchange_type") == "case"]
    challenges = [row for row in exchanges if row.get("role") == "skeptic" and row.get("exchange_type") == "challenge"]
    responses = [row for row in exchanges if row.get("role") == "advocate" and row.get("exchange_type") == "response"]
    if len(cases) != 1 or len(challenges) != 1 or len(responses) != 1 or len(exchanges) != 3:
        raise ValueError("Compact council requires exactly one advocate case, sceptic challenge, and advocate response")
    challenge_ids = {str(row.get("exchange_id")) for row in challenges}
    if not any(str(row.get("responds_to_id", "")) in challenge_ids for row in responses):
        raise ValueError("Advocate response must answer the combined sceptic challenge")
    required_domains = {
        "mechanism_direction",
        "worm_target_orthology",
        "allele_relevance",
        "pharmacology_selectivity",
        "exposure_feasibility",
        "phenomics_confounding",
    }
    covered_domains = {
        str(value) for row in challenges for value in row.get("critique_domains", [])
    }
    if required_domains - covered_domains:
        raise ValueError("Combined sceptic challenge did not cover every mandatory critique domain")
    unresolved = result.get("unresolved_material_claims", [])
    if unresolved:
        raise ValueError("Council fact audit cannot close with unresolved material claims")
    if result.get("novelty_challenge_resolved") is not True:
        raise ValueError("Council fact audit did not resolve therapeutic-conservatism challenge")
    if result.get("critique_checklist_complete") is not True:
        raise ValueError("Council fact audit did not verify the combined sceptic checklist")
    claim_verdicts = result.get("claim_verdicts", [])
    if not isinstance(claim_verdicts, list) or not claim_verdicts:
        raise ValueError("Council fact audit requires claim-by-claim verdicts")
    allowed_verdicts = {"supported", "qualified", "unsupported", "contradicted"}
    verdict_by_claim: dict[str, str] = {}
    for verdict in claim_verdicts:
        if not isinstance(verdict, dict):
            raise ValueError("Each claim verdict must be an object")
        claim_id = str(verdict.get("claim_id", "")).strip()
        status = str(verdict.get("verdict", "")).strip()
        checked_sources = verdict.get("checked_source_ids", [])
        if not claim_id or status not in allowed_verdicts or not isinstance(checked_sources, list) or not checked_sources:
            raise ValueError("Each claim verdict requires claim_id, a permitted verdict, and checked_source_ids")
        if claim_id not in claims:
            raise ValueError(f"Fact auditor referenced unknown claim ID: {claim_id}")
        unknown_sources = {str(value) for value in checked_sources} - set(sources)
        if unknown_sources:
            raise ValueError(f"Fact auditor referenced unknown source IDs: {sorted(unknown_sources)}")
        if any(
            not all(sources[str(source_id)].get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified"))
            for source_id in checked_sources
        ):
            raise ValueError(f"Fact auditor used an unverified source for claim {claim_id}")
        if status in {"supported", "qualified"} and not (
            {str(value) for value in checked_sources}
            & {str(value) for value in claims[claim_id].get("source_ids", [])}
        ):
            raise ValueError(f"Supporting verdict for {claim_id} did not check a source attached to that claim")
        verdict_by_claim[claim_id] = status
    exchange_claim_ids = {
        str(value) for exchange in exchanges for value in exchange.get("claim_ids", [])
    }
    material_claim_ids = {str(value) for value in result.get("material_claim_ids", [])}
    if material_claim_ids != exchange_claim_ids or set(verdict_by_claim) != material_claim_ids:
        raise ValueError("Fact auditor must verdict every and only material debate claim")
    independent_checks = result.get("independent_checks", [])
    if not isinstance(independent_checks, list) or not independent_checks:
        raise ValueError("Council fact audit requires at least one independent source check")
    for check in independent_checks:
        if (
            not isinstance(check, dict)
            or not str(check.get("resource", "")).strip()
            or not str(check.get("query", "")).strip()
            or str(check.get("executed_by_agent_id", "")) != fact_auditor_agent_id
            or not isinstance(check.get("checked_source_ids"), list)
            or not check.get("checked_source_ids")
        ):
            raise ValueError("Each independent check requires fact-auditor provenance, resource, query, and checked_source_ids")
        unknown_sources = {str(value) for value in check.get("checked_source_ids", [])} - set(sources)
        if unknown_sources:
            raise ValueError(f"Independent check referenced unknown source IDs: {sorted(unknown_sources)}")
        if any(
            not all(sources[str(source_id)].get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified"))
            for source_id in check.get("checked_source_ids", [])
        ):
            raise ValueError("Independent check referenced a source lacking original-content verification")
    reasons = {str(value) for value in result.get("verified_exclusion_reasons", [])}
    invalid = reasons - ALLOWED_EXCLUSION_REASONS
    if invalid:
        raise ValueError(f"Invalid exclusion reasons: {sorted(invalid)}")
    surviving_paths = [str(value) for value in result.get("surviving_causal_path_ids", [])]
    candidate_paths = {
        str(path.get("path_id")): path
        for path in candidate.get("causal_paths", [])
        if isinstance(path, dict)
    }
    if set(surviving_paths) - set(candidate_paths):
        raise ValueError("Surviving causal path must be part of the candidate's canonical causal dossier")
    if any(
        verdict_by_claim.get(str(claim_id)) not in {"supported", "qualified"}
        for path_id in surviving_paths
        for claim_id in candidate_paths[path_id].get("claim_ids", [])
    ):
        raise ValueError("A surviving causal path cannot rely on an unsupported or contradicted claim")
    if not reasons and not surviving_paths:
        raise ValueError("Screening requires at least one fact-audited surviving causal path")
    disposition = "exclude" if reasons else "screen"
    found = False
    for candidate in candidates:
        if str(candidate.get("candidate_id")) == candidate_id:
            candidate["council_disposition"] = disposition
            candidate["fact_audit_status"] = "verified"
            found = True
    if not found:
        raise ValueError(f"Council candidate not found: {candidate_id}")
    _write_jsonl(root / "candidate_records.jsonl", candidates)
    councils = _read_jsonl(root / "council_records.jsonl")
    for council in councils:
        if str(council.get("candidate_id")) == candidate_id:
            council["disposition"] = disposition
            council["exclusion_reason"] = sorted(reasons)[0] if reasons else ""
            council["fact_audit_status"] = "verified"
            council["unresolved_material_claims"] = []
            council["novelty_challenge_resolved"] = True
            council["direct_response_complete"] = True
            council["critique_checklist_complete"] = True
            council["material_claim_ids"] = sorted(material_claim_ids)
            council["claim_verdicts"] = claim_verdicts
            council["independent_checks"] = independent_checks
            council["surviving_causal_path_ids"] = surviving_paths
    _write_jsonl(root / "council_records.jsonl", councils)
    return disposition


def complete_job(root: Path, job_id: str, result_path: str | None = None) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    jobs = _job_map(plan)
    job = jobs.get(job_id)
    if not job or job.get("status") != "running" or state.get("active_job_id") != job_id:
        raise ValueError(f"Job is not the active running job: {job_id}")
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    attempt = next(
        (row for row in attempts if str(row.get("attempt_id")) == str(state.get("active_attempt_id"))),
        None,
    )
    if not attempt:
        raise ValueError("Active attempt record is missing")
    validation = validate_result(root, job_id, result_path)
    if validation.get("status") != "valid":
        joined = "\n".join(f"- {error}" for error in validation.get("errors", []))
        raise ValueError(f"Staged result failed immediate validation; repair the active job result:\n{joined}")
    relative_result = str(validation["result_path"])
    result = _load_result(root, relative_result)
    if str(result.get("job_id")) != job_id:
        raise ValueError("Result job_id does not match active job")
    if str(result.get("packet_hash")) != str(job.get("packet_hash")):
        raise ValueError("Result packet_hash does not match immutable job packet")
    if result.get("all_chunks_processed") is not True:
        raise ValueError("Result must confirm all required packet chunks were processed")
    outcome = str(result.get("outcome", ""))
    allowed = {"completed"} if job.get("kind") in {
        "research_worker", "closure_worker", "council_turn", "final_repair_worker"
    } else {
        "verified", "repair_required", "evidence_absent_complete"
    }
    if outcome not in allowed:
        raise ValueError(f"Invalid outcome {outcome!r} for {job.get('kind')}")
    result_file = (root / relative_result).resolve()
    result_file.relative_to(root.resolve())
    job["result_path"] = str(result_file.relative_to(root))
    job["result_hash"] = _hash_file(result_file)
    if outcome != "repair_required":
        staged_paths = _result_paths_for_commit(root, plan, job, job["result_path"])
        staged_errors = validate_staged_commit(root, job, staged_paths, str(attempt.get("agent_id", "")))
        if staged_errors:
            joined = "\n".join(f"- {error}" for error in staged_errors)
            raise ValueError(f"Staged result failed immediate validation; repair the active job result:\n{joined}")
    attempt_id = str(state["active_attempt_id"])
    _finish_attempt(root, attempt_id, "complete")
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    state["pending_agent_release_id"] = str(attempt.get("agent_id", ""))
    state["pending_agent_release_attempt_id"] = attempt_id

    approved_subtopics = result.get("approved_subtopics", [])
    if not isinstance(approved_subtopics, list):
        raise ValueError("approved_subtopics must be a list")
    if (
        approved_subtopics
        and int(job.get("phase", 0)) == 3
        and job.get("kind") == "unit_auditor"
        and outcome != "repair_required"
    ):
        raise ValueError(
            "A phase-3 unit that discovers a material subtopic must use repair_required, "
            "reopen evidence closure, and rerun against the expanded graph"
        )

    if outcome == "repair_required":
        repair_feedback = {
            "job_id": job_id,
            "result_path": job["result_path"],
            "result_hash": job["result_hash"],
            "purpose": "auditor_repair_feedback",
        }
        _register_subtopics(root, plan, approved_subtopics, job_id)
        paired_id = str(job.get("paired_worker_job_id", ""))
        if paired_id:
            paired = jobs[paired_id]
            prior_worker_result = {
                "job_id": paired_id,
                "result_path": str(paired.get("result_path", "")),
                "result_hash": str(paired.get("result_hash", "")),
                "purpose": "prior_worker_result",
            }
            if not prior_worker_result["result_path"] or not prior_worker_result["result_hash"]:
                raise ValueError("A repair-required audit must retain its paired worker result lineage")
            paired["repair_round"] = int(paired.get("repair_round", 0)) + 1
            _reset_job(paired, "ready")
            lineage = paired.setdefault("repair_context_paths", [])
            for entry in (prior_worker_result, repair_feedback):
                if entry not in lineage:
                    lineage.append(entry)
            _reset_job(job, "planned")
        elif job.get("kind") == "council_fact_auditor":
            reopen_stage = str(result.get("reopen_stage", "skeptic_review"))
            candidate_jobs = sorted(
                [row for row in plan.get("jobs", []) if row.get("candidate_id") == job.get("candidate_id")],
                key=lambda row: int(row.get("sequence", 0)),
            )
            target = next((row for row in candidate_jobs if row.get("stage") == reopen_stage), None)
            if target is None:
                raise ValueError(f"Unknown council reopen_stage: {reopen_stage}")
            for row in candidate_jobs:
                if int(row.get("sequence", 0)) >= int(target.get("sequence", 0)):
                    _reset_job(row, "planned")
            target.setdefault("repair_context_paths", []).append(repair_feedback)
        else:
            _reset_job(job, "planned")
        state["subtopic_closure_complete"] = False if approved_subtopics else state.get("subtopic_closure_complete", False)
        if job.get("kind") in {
            "unit_auditor", "closure_auditor", "merge_auditor", "council_fact_auditor", "final_repair_auditor"
        }:
            state["slice_completed_pairs"] = int(state.get("slice_completed_pairs", 0)) + 1
            state["checkpoint_pending"] = True
        _event(root, "repair_required", job_id=job_id, attempt_id=attempt_id, approved_subtopics=len(approved_subtopics))
        _persist(root, plan, state)
        return {"status": "repair_required", "next": next_action(root)}

    job["status"] = "complete"
    if job.get("kind") in {
        "unit_auditor", "merge_auditor", "council_fact_auditor", "final_repair_auditor"
    }:
        for path in _result_paths_for_commit(root, plan, job, job["result_path"]):
            _merge_result(root, _load_result(root, path))
        _set_unit_complete(root, str(job.get("unit_id", "")), result)
        _register_subtopics(root, plan, approved_subtopics, job_id)
    elif job.get("kind") == "closure_auditor":
        for path in _result_paths_for_commit(root, plan, job, job["result_path"]):
            _merge_result(root, _load_result(root, path))
        _set_unit_complete(root, str(job.get("unit_id", "")), result)
        _register_subtopics(root, plan, approved_subtopics, job_id)
        if result.get("closure_confirmed") is True and not approved_subtopics:
            state["subtopic_closure_complete"] = True
        else:
            _reset_job(job, "planned")
            state["subtopic_closure_complete"] = False

    if job.get("kind") == "merge_auditor" and job.get("status") == "complete":
        candidate_ids = [str(value) for value in result.get("candidate_ids", [])]
        if not candidate_ids:
            candidate_ids = [
                str(row.get("candidate_id"))
                for row in _read_jsonl(root / "candidate_records.jsonl")
            ]
        _register_council(plan, candidate_ids)

    disposition = ""
    if job.get("kind") == "council_fact_auditor" and job.get("status") == "complete":
        disposition = _apply_council_disposition(
            root, str(job.get("candidate_id")), result, str(attempt.get("agent_id", ""))
        )

    _event(root, "job_completed", job_id=job_id, attempt_id=attempt_id, outcome=outcome, disposition=disposition)
    if job.get("kind") in {
        "unit_auditor", "closure_auditor", "merge_auditor", "council_fact_auditor", "final_repair_auditor"
    } and job.get("status") == "complete":
        state["slice_completed_pairs"] = int(state.get("slice_completed_pairs", 0)) + 1
        if int(state["slice_completed_pairs"]) >= int(state.get("slice_max_pairs", DEFAULT_SLICE_PAIRS)):
            state["checkpoint_pending"] = True
    state["rate_limit_strikes"] = 0
    _persist(root, plan, state)
    return {"status": "complete", "disposition": disposition, "next": next_action(root)}


def fail_job(root: Path, job_id: str, failure_kind: str, retry_after_seconds: int, detail: str) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    job = _job_map(plan).get(job_id)
    if not job or state.get("active_job_id") != job_id or job.get("status") != "running":
        raise ValueError(f"Job is not active: {job_id}")
    attempt_id = str(state.get("active_attempt_id"))
    attempts = _read_jsonl(root / "job_attempts.jsonl")
    attempt = next((row for row in attempts if str(row.get("attempt_id")) == attempt_id), None)
    if not attempt:
        raise ValueError("Active attempt record is missing")
    if failure_kind == "unrecoverable":
        job["status"] = "blocked"
        state["blocked_reason"] = detail or f"Unrecoverable failure in {job_id}"
    elif failure_kind in {"rate_limit", "spawn_failure", "transient"}:
        if failure_kind == "rate_limit":
            state["rate_limit_strikes"] = int(state.get("rate_limit_strikes", 0)) + 1
            proactive = min(900, 30 * (2 ** (int(state["rate_limit_strikes"]) - 1)))
            retry_after_seconds = max(retry_after_seconds, proactive)
        job["status"] = "retry_wait" if retry_after_seconds > 0 else "ready"
        job["retry_not_before"] = (
            datetime.now(timezone.utc) + timedelta(seconds=max(0, retry_after_seconds))
        ).isoformat() if retry_after_seconds > 0 else ""
    else:
        raise ValueError(f"Unknown failure kind: {failure_kind}")
    _finish_attempt(root, attempt_id, "failed", failure_kind)
    state["active_job_id"] = ""
    state["active_attempt_id"] = ""
    state["pending_agent_release_id"] = str(attempt.get("agent_id", ""))
    state["pending_agent_release_attempt_id"] = attempt_id
    _event(
        root,
        "job_failed",
        job_id=job_id,
        attempt_id=attempt_id,
        failure_kind=failure_kind,
        detail=detail,
        status="rate_limited" if failure_kind == "rate_limit" else "failed",
        rate_limit_pending=failure_kind == "rate_limit",
    )
    _persist(root, plan, state)
    return next_action(root)


def status(root: Path) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    _persist(root, plan, state)
    counts: dict[str, int] = {}
    for job in plan.get("jobs", []):
        key = str(job.get("status", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return {"state": state, "job_counts": counts, "next": next_action(root)}


def resume_action(root: Path) -> dict[str, Any]:
    plan = _read_json(root / "execution_plan.json", {})
    state = _read_json(root / "program_state.json", {})
    if not state.get("active_job_id") and not state.get("pending_agent_release_id"):
        state["checkpoint_pending"] = False
        state["slice_started_at"] = _now()
        state["slice_jobs_started"] = 0
        state["slice_completed_pairs"] = 0
        _event(root, "execution_slice_resumed")
        _persist(root, plan, state)
    return next_action(root)


def _load_case(args: argparse.Namespace) -> dict[str, Any]:
    case = _read_json(Path(args.case_file), {}) if args.case_file else {}
    for field in ("human_gene", "worm_gene", "allele_mode"):
        supplied = getattr(args, field, None)
        if supplied:
            case[field] = supplied
    return case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("run_folder")
    init_parser.add_argument("--case-file")
    init_parser.add_argument("--human-gene")
    init_parser.add_argument("--worm-gene")
    init_parser.add_argument("--allele-mode")

    for name in ("next", "resume", "status"):
        command = subparsers.add_parser(name)
        command.add_argument("run_folder")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("run_folder")
    start_parser.add_argument("job_id")
    start_parser.add_argument("agent_id")

    complete_parser = subparsers.add_parser("complete")
    complete_parser.add_argument("run_folder")
    complete_parser.add_argument("job_id")
    complete_parser.add_argument("--result-path")

    validate_parser = subparsers.add_parser("validate-result")
    validate_parser.add_argument("run_folder")
    validate_parser.add_argument("job_id")
    validate_parser.add_argument("--result-path")

    recover_parser = subparsers.add_parser("recover-active")
    recover_parser.add_argument("run_folder")
    recover_parser.add_argument("new_agent_id")
    recover_parser.add_argument("--reason", default="assigned task unavailable")

    recover_ready_parser = subparsers.add_parser("recover-ready")
    recover_ready_parser.add_argument("run_folder")
    recover_ready_parser.add_argument("job_id")
    recover_ready_parser.add_argument("new_agent_id")
    recover_ready_parser.add_argument("--reason", default="assigned repair task unavailable")

    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("run_folder")
    release_parser.add_argument("attempt_id")
    release_parser.add_argument("agent_id")

    fail_parser = subparsers.add_parser("fail")
    fail_parser.add_argument("run_folder")
    fail_parser.add_argument("job_id")
    fail_parser.add_argument("failure_kind", choices=("rate_limit", "spawn_failure", "transient", "unrecoverable"))
    fail_parser.add_argument("--retry-after-seconds", type=int, default=60)
    fail_parser.add_argument("--detail", default="")

    args = parser.parse_args(argv)
    root = Path(args.run_folder).expanduser().resolve()
    try:
        if args.command == "init":
            result = initialize(root, _load_case(args))
        elif args.command == "next":
            result = next_action(root)
        elif args.command == "resume":
            result = resume_action(root)
        elif args.command == "status":
            result = status(root)
        elif args.command == "start":
            result = start_job(root, args.job_id, args.agent_id)
        elif args.command == "complete":
            result = complete_job(root, args.job_id, args.result_path)
        elif args.command == "validate-result":
            result = validate_result(root, args.job_id, args.result_path)
        elif args.command == "recover-active":
            result = recover_active(root, args.new_agent_id, args.reason)
        elif args.command == "recover-ready":
            result = recover_ready(root, args.job_id, args.new_agent_id, args.reason)
        elif args.command == "release":
            result = release_agent(root, args.attempt_id, args.agent_id)
        elif args.command == "fail":
            result = fail_job(root, args.job_id, args.failure_kind, args.retry_after_seconds, args.detail)
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    if args.command == "validate-result" and result.get("status") != "valid":
        print(json.dumps({"ok": False, "result": result}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
