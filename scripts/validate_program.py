#!/usr/bin/env python3
"""Validate a repurposing research run from its structured evidence records."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from program_contract import (
    BROAD_DOMAINS,
    CALIBRATIONS,
    COUNCIL_STAGES,
    GLOBAL_PERSPECTIVES,
    LEDGER_SCHEMAS,
    MAX_ACTIVE_JOBS,
    SCHEMA_VERSION,
    SKEPTIC_CRITIQUE_DOMAINS,
    SOURCE_ALLOWED_FIELDS,
    required_query_families,
)

REQUIRED_FILES = (
    "case.json",
    "program_state.json",
    "execution_plan.json",
    "orchestration.jsonl",
    "job_attempts.jsonl",
    "source_corpus.jsonl",
    "search_log.jsonl",
    "claim_ledger.jsonl",
    "evidence_graph.jsonl",
    "subtopic_registry.jsonl",
    "research_units.jsonl",
    "unit_audits.jsonl",
    "candidate_records.jsonl",
    "council_records.jsonl",
    "council_exchanges.jsonl",
)

VERIFIED = {"verified", "audited_complete"}
FINAL_UNIT_STATUSES = {"audited_complete", "evidence_absent_complete"}
COUNCIL_STAGE_ROLES = COUNCIL_STAGES
PROHIBITED_SOURCE_PAYLOAD_FIELDS = {
    "affiliations",
    "author_affiliations",
    "raw_payload",
    "raw_xml",
    "raw_html",
    "full_text",
    "complete_reference_list",
    "nested_metadata",
}
BAD_COMPLETION_PHRASES = (
    "enough to write",
    "enough to judge",
    "good enough",
    "clean enough",
    "sufficient to proceed",
)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _empty_collection(value: Any) -> bool:
    return value in (None, "", [], {})


def _present(obj: dict[str, Any], fields: Iterable[str], label: str, errors: list[str]) -> None:
    missing = [field for field in fields if field not in obj]
    if missing:
        errors.append(f"{label}: missing fields {missing}")


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"{path.name}: invalid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name}: expected one JSON object")
        return {}
    return value


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception as exc:
        errors.append(f"{path.name}: could not read: {exc}")
        return rows
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception as exc:
            errors.append(f"{path.name}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number}: expected a JSON object")
            continue
        rows.append(value)
    return rows


def _index(
    rows: Iterable[dict[str, Any]], key: str, label: str, errors: list[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(rows, 1):
        value = str(row.get(key, "")).strip()
        if not value:
            errors.append(f"{label} row {row_number}: missing {key}")
        elif value in result:
            errors.append(f"{label}: duplicate {key} {value!r}")
        else:
            result[value] = row
    return result


def _required(row: dict[str, Any], fields: Iterable[str], label: str, errors: list[str]) -> None:
    for field in fields:
        if _blank(row.get(field)):
            errors.append(f"{label}: missing {field}")


def _inside_file(root: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    if _blank(value):
        errors.append(f"{label}: missing path")
        return None
    supplied = Path(str(value))
    target = supplied if supplied.is_absolute() else root / supplied
    try:
        resolved = target.resolve()
        resolved.relative_to(root.resolve())
    except Exception:
        errors.append(f"{label}: path must stay inside the run folder: {value}")
        return None
    if not resolved.is_file():
        errors.append(f"{label}: file does not exist: {value}")
        return None
    return resolved


def _integer(value: Any, label: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{label}: expected a non-negative integer")
        return None
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _receipt_identity(record: dict[str, Any]) -> str:
    canonical = str(record.get("canonical_identifier", "")).strip().casefold()
    identifier_type = str(record.get("identifier_type", "")).strip().casefold()
    if canonical:
        return f"{identifier_type}:{canonical}"
    return f"hash:{str(record.get('compact_record_hash', '')).casefold()}"


def _validate_query_depth(
    root: Path,
    query_id: str,
    query: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    errors: list[str],
    *,
    label_prefix: str = "query",
) -> None:
    """Bind search depth claims to compact receipts, source IDs, and a continuation chain."""
    label = f"{label_prefix} {query_id}"
    compact_paths = [str(value) for value in _list(query.get("compact_payload_paths"))]
    if not compact_paths:
        errors.append(f"{label}: no compact payload receipt")
        return
    if len(compact_paths) != len(set(compact_paths)):
        errors.append(f"{label}: compact payload receipts must be unique per retrieval page")

    records: list[dict[str, Any]] = []
    for compact_path in compact_paths:
        compact_file = _inside_file(root, compact_path, f"{label} compact payload", errors)
        if not compact_file:
            continue
        receipt = _read_json(compact_file, errors)
        receipt_records = _list(receipt.get("records"))
        if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py":
            errors.append(f"{label}: compact payload was not produced by the required compactor")
        receipt_count = _integer(receipt.get("result_count"), f"{label} compact receipt result_count", errors)
        if receipt_count is not None and receipt_count != len(receipt_records):
            errors.append(f"{label}: compact receipt result_count does not match its records")
        for record_number, record in enumerate(receipt_records, 1):
            if not isinstance(record, dict):
                errors.append(f"{label}: compact receipt record {record_number} is not an object")
                continue
            stored_hash = str(record.get("compact_record_hash", ""))
            hash_body = {key: value for key, value in record.items() if key != "compact_record_hash"}
            if not stored_hash or stored_hash != _content_hash(hash_body):
                errors.append(f"{label}: compact receipt record {record_number} hash mismatch")
            if str(record.get("query_id", "")) != query_id:
                errors.append(
                    f"{label}: compact receipt record {record_number} query_id does not match the search record"
                )
            records.append(record)

    result_count = query.get("result_count")
    if isinstance(result_count, int) and not isinstance(result_count, bool) and result_count != len(records):
        errors.append(f"{label}: result_count is not proven by compact receipt records")
    identities = {_receipt_identity(record) for record in records}
    deduplicated_count = query.get("deduplicated_count")
    if (
        isinstance(deduplicated_count, int)
        and not isinstance(deduplicated_count, bool)
        and deduplicated_count != len(identities)
    ):
        errors.append(f"{label}: deduplicated_count is not proven by compact receipt identities")
    page_count = query.get("page_count")
    if isinstance(page_count, int) and not isinstance(page_count, bool) and page_count != len(compact_paths):
        errors.append(f"{label}: page_count is not proven by compact receipt paths")

    trace = _list(query.get("pagination_trace"))
    if len(trace) != len(compact_paths):
        errors.append(f"{label}: pagination_trace must contain exactly one entry per compact receipt")
    previous_output = ""
    for index, item in enumerate(trace, 1):
        if not isinstance(item, dict):
            errors.append(f"{label}: pagination trace entry {index} is not an object")
            continue
        _present(
            item,
            ("page_index", "receipt_path", "input_token_hash", "output_token_hash"),
            f"{label} pagination trace entry {index}",
            errors,
        )
        if item.get("page_index") != index:
            errors.append(f"{label}: pagination trace indexes are not contiguous")
        if index <= len(compact_paths) and str(item.get("receipt_path", "")) != compact_paths[index - 1]:
            errors.append(f"{label}: pagination trace is not bound to compact receipt order")
        input_hash = str(item.get("input_token_hash", ""))
        output_hash = str(item.get("output_token_hash", ""))
        for token_hash in (input_hash, output_hash):
            if token_hash and not re.fullmatch(r"[0-9a-fA-F]{64}", token_hash):
                errors.append(f"{label}: pagination continuation hashes must be SHA-256 or empty")
        if input_hash != previous_output:
            errors.append(f"{label}: pagination continuation chain is disconnected")
        if index < len(trace) and not output_hash:
            errors.append(f"{label}: pagination trace ends before the final page")
        previous_output = output_hash
    if trace and previous_output:
        errors.append(f"{label}: final pagination continuation is not exhausted")

    acquired_ids = [str(value) for value in _list(query.get("acquired_source_ids"))]
    verified_ids = [str(value) for value in _list(query.get("original_verified_source_ids"))]
    retained_ids = [str(value) for value in _list(query.get("retained_source_ids"))]
    if len(acquired_ids) != len(set(acquired_ids)) or len(verified_ids) != len(set(verified_ids)):
        errors.append(f"{label}: acquisition and verification source IDs must be unique")
    if query.get("acquired_count") != len(acquired_ids):
        errors.append(f"{label}: acquired_count is not proven by acquired_source_ids")
    if query.get("original_verified_count") != len(verified_ids):
        errors.append(f"{label}: original_verified_count is not proven by original_verified_source_ids")
    if not set(verified_ids).issubset(set(acquired_ids)):
        errors.append(f"{label}: original-verified sources must be a subset of acquired sources")
    if not set(retained_ids).issubset(set(verified_ids)):
        errors.append(f"{label}: retained sources must be a subset of original-verified sources")
    receipt_canonical_ids = {
        str(record.get("canonical_identifier", "")).strip().casefold()
        for record in records
        if str(record.get("canonical_identifier", "")).strip()
    }
    for source_id in acquired_ids:
        source = sources.get(source_id)
        if source is None:
            errors.append(f"{label}: acquired source {source_id} is unknown")
            continue
        if source.get("original_acquired") is not True:
            errors.append(f"{label}: acquired source {source_id} lacks original acquisition")
        if str(source.get("canonical_identifier", "")).strip().casefold() not in receipt_canonical_ids:
            errors.append(f"{label}: acquired source {source_id} is absent from compact receipt records")
    for source_id in verified_ids:
        source = sources.get(source_id)
        if source is not None and not all(
            source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")
        ):
            errors.append(f"{label}: original-verified source {source_id} lacks verified original content")


def _validate_runtime(
    root: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    units: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    councils: list[dict[str, Any]],
    errors: list[str],
) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"program_state.json: schema_version must be {SCHEMA_VERSION}")
    if state.get("max_active_jobs") != MAX_ACTIVE_JOBS:
        errors.append(f"program_state.json: max_active_jobs must be {MAX_ACTIVE_JOBS}")
    if not _blank(state.get("active_job_id")) or not _blank(state.get("active_attempt_id")):
        errors.append("program_state.json: no job or attempt may remain active at finalization")
    if not _blank(state.get("pending_agent_release_id")) or not _blank(state.get("pending_agent_release_attempt_id")):
        errors.append("program_state.json: no agent release may remain pending at finalization")
    if not _blank(state.get("blocked_reason")):
        errors.append("program_state.json: blocked_reason must be empty at finalization")
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"execution_plan.json: schema_version must be {SCHEMA_VERSION}")
    if plan.get("max_active_jobs") != MAX_ACTIVE_JOBS:
        errors.append(f"execution_plan.json: max_active_jobs must be {MAX_ACTIVE_JOBS}")
    if plan.get("fixed_seed_topology") is not True:
        errors.append("execution_plan.json: fixed_seed_topology must be true")

    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        errors.append("execution_plan.json: jobs must be a nonempty list")
        return
    job_by_id = _index(jobs, "job_id", "execution_plan jobs", errors)
    role_agents = plan.get("role_agents")
    if not isinstance(role_agents, dict) or not role_agents:
        errors.append("execution_plan.json: role_agents must record every assigned independent role")
        role_agents = {}
    assigned_values = [str(value) for value in role_agents.values() if str(value).strip()]
    if len(assigned_values) != len(set(assigned_values)):
        errors.append("execution_plan.json: one agent is assigned to multiple independent roles")
    attempts_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    attempt_by_id = _index(attempts, "attempt_id", "job_attempts", errors)
    for attempt_id, attempt in attempt_by_id.items():
        _required(
            attempt,
            (
                "job_id", "agent_id", "packet_hash", "packet_manifest_path", "expected_result_path",
                "status", "started_at", "release_acknowledged", "released_at",
            ),
            f"attempt {attempt_id}",
            errors,
        )
        job_id = str(attempt.get("job_id", ""))
        attempts_by_job[job_id].append(attempt)
        if job_id not in job_by_id:
            errors.append(f"attempt {attempt_id}: unknown job {job_id}")
        if attempt.get("status") not in {"complete", "failed"}:
            errors.append(f"attempt {attempt_id}: unresolved status {attempt.get('status')}")
        if _blank(attempt.get("finished_at")):
            errors.append(f"attempt {attempt_id}: missing finished_at")
        if attempt.get("release_acknowledged") is not True or _blank(attempt.get("released_at")):
            errors.append(f"attempt {attempt_id}: agent release was not acknowledged")

    unit_by_id = {str(row.get("unit_id")): row for row in units}
    council_by_id = {str(row.get("candidate_id")): row for row in councils}
    jobs_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job_id, job in job_by_id.items():
        _required(
            job,
            ("phase", "sequence", "kind", "role", "question", "completion_contract", "status", "packet_manifest_path", "packet_hash", "result_path", "result_hash"),
            f"job {job_id}",
            errors,
        )
        if job.get("status") != "complete":
            errors.append(f"job {job_id}: final status must be complete")
        for dependency_id in _list(job.get("depends_on")):
            dependency = job_by_id.get(str(dependency_id))
            if dependency is None:
                errors.append(f"job {job_id}: unknown dependency {dependency_id}")
            elif dependency.get("status") != "complete":
                errors.append(f"job {job_id}: dependency {dependency_id} is incomplete")
        unit_id = str(job.get("unit_id", ""))
        if unit_id:
            jobs_by_unit[unit_id].append(job)
            if unit_id not in unit_by_id:
                errors.append(f"job {job_id}: unknown unit {unit_id}")

        role_key = (
            f"council:{job.get('candidate_id')}:{job.get('role')}"
            if job.get("candidate_id")
            else f"unit:{job.get('unit_id')}:{job.get('role')}"
            if job.get("unit_id")
            else f"job:{job_id}:{job.get('role')}"
        )
        assigned_agent = str(role_agents.get(role_key, ""))
        if not assigned_agent:
            errors.append(f"job {job_id}: no controller role-agent assignment for {role_key}")
        if unit_id and unit_id in unit_by_id and assigned_agent:
            unit_agent_field = "worker_agent_id" if job.get("role") == "worker" else "auditor_agent_id"
            if str(unit_by_id[unit_id].get(unit_agent_field, "")) != assigned_agent:
                errors.append(f"job {job_id}: controller role agent disagrees with research unit")
        candidate_id = str(job.get("candidate_id", ""))
        council_role_fields = {
            "advocate": "advocate_agent_id",
            "skeptic": "skeptic_agent_id",
            "fact_auditor": "fact_auditor_agent_id",
        }
        if candidate_id in council_by_id and assigned_agent:
            council_field = council_role_fields.get(str(job.get("role")))
            if council_field and str(council_by_id[candidate_id].get(council_field, "")) != assigned_agent:
                errors.append(f"job {job_id}: controller role agent disagrees with council record")

        manifest_file = _inside_file(root, job.get("packet_manifest_path"), f"job {job_id} packet manifest", errors)
        result_file = _inside_file(root, job.get("result_path"), f"job {job_id} result", errors)
        if result_file and str(job.get("result_hash")) != _sha256(result_file):
            errors.append(f"job {job_id}: result hash mismatch")
        if manifest_file:
            manifest = _read_json(manifest_file, errors)
            if manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"job {job_id}: packet manifest schema version mismatch")
            if manifest.get("job_id") != job_id:
                errors.append(f"job {job_id}: packet manifest job mismatch")
            if manifest.get("packet_hash") != job.get("packet_hash"):
                errors.append(f"job {job_id}: packet hash mismatch")
            manifest_body = {key: value for key, value in manifest.items() if key != "packet_hash"}
            if manifest.get("packet_hash") != _content_hash(manifest_body):
                errors.append(f"job {job_id}: packet manifest content hash mismatch")
            if manifest.get("all_chunks_required") is not True or manifest.get("silent_truncation_permitted") is not False:
                errors.append(f"job {job_id}: packet does not forbid silent truncation")
            chunks = _list(manifest.get("required_chunks"))
            if not chunks:
                errors.append(f"job {job_id}: packet has no required chunks")
            for chunk in chunks:
                chunk_file = _inside_file(root, chunk.get("path"), f"job {job_id} packet chunk", errors)
                if chunk_file and str(chunk.get("sha256")) != _sha256(chunk_file):
                    errors.append(f"job {job_id}: packet chunk hash mismatch")
                if chunk_file:
                    packet = _read_json(chunk_file, errors)
                    contract = packet.get("machine_contract")
                    path_contract = packet.get("path_contract")
                    if packet.get("schema_version") != SCHEMA_VERSION or not isinstance(contract, dict):
                        errors.append(f"job {job_id}: packet chunk lacks the versioned machine contract")
                    elif not all(
                        key in contract
                        for key in (
                            "ledger_schemas", "result_required_fields", "completion_rule",
                            "tool_paths", "preferred_source_resources", "path_rule", "source_enum_rule",
                        )
                    ):
                        errors.append(f"job {job_id}: packet machine contract is incomplete")
                    if str(packet.get("run_root", "")) != str(root):
                        errors.append(f"job {job_id}: packet run_root does not match the authoritative run folder")
                    if (
                        not isinstance(path_contract, dict)
                        or path_contract.get("relative_paths_resolve_against") != "run_root"
                        or _blank(path_contract.get("missing_artifact_rule"))
                    ):
                        errors.append(f"job {job_id}: packet path-resolution contract is incomplete")
                    for dependency in _list(packet.get("context", {}).get("dependency_results")):
                        if not isinstance(dependency, dict):
                            errors.append(f"job {job_id}: dependency result entry is not an object")
                            continue
                        dependency_file = _inside_file(
                            root,
                            dependency.get("result_path"),
                            f"job {job_id} dependency result",
                            errors,
                        )
                        if dependency_file and str(dependency.get("result_hash", "")) != _sha256(dependency_file):
                            errors.append(f"job {job_id}: dependency result hash mismatch")
                        if dependency.get("path_base") != "run_root":
                            errors.append(f"job {job_id}: dependency result lacks run-root path semantics")
                        for artifact in _list(dependency.get("artifact_manifest")):
                            if not isinstance(artifact, dict):
                                errors.append(f"job {job_id}: dependency artifact entry is not an object")
                                continue
                            artifact_file = _inside_file(
                                root,
                                artifact.get("path"),
                                f"job {job_id} dependency artifact",
                                errors,
                            )
                            if artifact.get("exists") is not True:
                                errors.append(f"job {job_id}: dependency artifact manifest records a missing file")
                            if (
                                artifact_file
                                and artifact.get("immutable") is True
                                and str(artifact.get("sha256", "")) != _sha256(artifact_file)
                            ):
                                errors.append(f"job {job_id}: dependency artifact hash mismatch")

        completed_attempts = [
            row for row in attempts_by_job.get(job_id, [])
            if row.get("status") == "complete" and row.get("packet_hash") == job.get("packet_hash")
        ]
        if not completed_attempts:
            errors.append(f"job {job_id}: no completed attempt matches the final packet hash")
        elif assigned_agent and any(str(row.get("agent_id")) != assigned_agent for row in completed_attempts):
            errors.append(f"job {job_id}: completed attempt does not use assigned role agent")

    for unit_id, unit in unit_by_id.items():
        unit_jobs = jobs_by_unit.get(unit_id, [])
        if unit.get("unit_type") == "closure_audit":
            kinds = {str(job.get("kind")) for job in unit_jobs}
            if not {"closure_worker", "closure_auditor"}.issubset(kinds):
                errors.append(f"unit {unit_id}: missing controller closure worker or independent auditor")
        elif unit.get("unit_type") in {"broad_evidence", "subtopic_evidence", "subtopic_compound", "global_perspective"}:
            roles = {str(job.get("role")) for job in unit_jobs}
            if not {"worker", "auditor"}.issubset(roles):
                errors.append(f"unit {unit_id}: controller plan lacks worker and auditor jobs")

    candidate_ids = {str(row.get("candidate_id")) for row in candidates}
    for candidate_id in candidate_ids:
        council_jobs = sorted(
            [job for job in jobs if str(job.get("candidate_id", "")) == candidate_id],
            key=lambda row: int(row.get("sequence", 0)),
        )
        actual = [(str(job.get("stage")), str(job.get("role"))) for job in council_jobs]
        if actual != list(COUNCIL_STAGE_ROLES):
            errors.append(f"candidate {candidate_id}: controller council turn order is incomplete or incorrect")
        for previous, current in zip(council_jobs, council_jobs[1:]):
            if str(previous.get("job_id")) not in {str(value) for value in _list(current.get("depends_on"))}:
                errors.append(f"candidate {candidate_id}: council stage {current.get('stage')} does not depend on prior turn")


def validate_run(run_folder: str | Path) -> list[str]:
    root = Path(run_folder).expanduser().resolve()
    errors: list[str] = []
    if not root.is_dir():
        return [f"Run folder does not exist: {root}"]

    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            errors.append(f"Missing required file: {name}")
    for directory in ("dossiers", "packets", "staging", "raw_sources"):
        if not (root / directory).is_dir():
            errors.append(f"Missing required directory: {directory}")
    if errors:
        return errors

    case = _read_json(root / "case.json", errors)
    state = _read_json(root / "program_state.json", errors)
    plan = _read_json(root / "execution_plan.json", errors)
    orchestration = _read_jsonl(root / "orchestration.jsonl", errors)
    attempts = _read_jsonl(root / "job_attempts.jsonl", errors)
    sources = _read_jsonl(root / "source_corpus.jsonl", errors)
    searches = _read_jsonl(root / "search_log.jsonl", errors)
    claims = _read_jsonl(root / "claim_ledger.jsonl", errors)
    edges = _read_jsonl(root / "evidence_graph.jsonl", errors)
    subtopics = _read_jsonl(root / "subtopic_registry.jsonl", errors)
    units = _read_jsonl(root / "research_units.jsonl", errors)
    audits = _read_jsonl(root / "unit_audits.jsonl", errors)
    candidates = _read_jsonl(root / "candidate_records.jsonl", errors)
    councils = _read_jsonl(root / "council_records.jsonl", errors)
    exchanges = _read_jsonl(root / "council_exchanges.jsonl", errors)

    _validate_runtime(root, state, plan, attempts, units, candidates, councils, errors)

    _required(case, ("human_gene", "worm_gene", "allele_mode"), "case.json", errors)
    for gate in (
        "broad_evidence_complete",
        "subtopic_closure_complete",
        "de_novo_perspectives_complete",
        "candidate_universe_complete",
        "council_complete",
    ):
        if state.get(gate) is not True:
            errors.append(f"program_state.json: {gate} must be true")
    if state.get("current_phase") != "ready_for_finalization":
        errors.append("program_state.json: current_phase must be ready_for_finalization")
    for event_number, event in enumerate(orchestration, 1):
        _required(event, ("event_id", "status"), f"orchestration event {event_number}", errors)

    source_by_id = _index(sources, "source_id", "source_corpus", errors)
    search_by_id = _index(searches, "query_id", "search_log", errors)
    claim_by_id = _index(claims, "claim_id", "claim_ledger", errors)
    edge_by_id = _index(edges, "edge_id", "evidence_graph", errors)
    subtopic_by_id = _index(subtopics, "subtopic_id", "subtopic_registry", errors)
    unit_by_id = _index(units, "unit_id", "research_units", errors)
    audit_by_id = _index(audits, "audit_id", "unit_audits", errors)
    candidate_by_id = _index(candidates, "candidate_id", "candidate_records", errors)
    council_by_id = _index(councils, "candidate_id", "council_records", errors)
    exchange_by_id = _index(exchanges, "exchange_id", "council_exchanges", errors)
    plan_job_by_id = _index(plan.get("jobs", []), "job_id", "execution_plan jobs", errors)

    for required_name, mapping in (
        ("source_corpus", source_by_id),
        ("search_log", search_by_id),
        ("claim_ledger", claim_by_id),
        ("evidence_graph", edge_by_id),
        ("subtopic_registry", subtopic_by_id),
        ("research_units", unit_by_id),
        ("unit_audits", audit_by_id),
    ):
        if not mapping:
            errors.append(f"{required_name}: must not be empty")

    for source_id, source in source_by_id.items():
        _present(source, LEDGER_SCHEMAS["source_corpus.jsonl"], f"source {source_id}", errors)
        _required(
            source,
            (
                "canonical_identifier", "identifier_type", "title", "source_kind", "source_family",
                "screen_decision", "original_pointer", "verification_method", "verification_scope",
                "compaction_receipt_path", "compaction_record_hash",
            ),
            f"source {source_id}",
            errors,
        )
        unexpected = set(source) - SOURCE_ALLOWED_FIELDS
        if unexpected:
            errors.append(f"source {source_id}: unrecognized or uncompact source fields {sorted(unexpected)}")
        if source.get("screen_decision") not in {"include", "exclude"}:
            errors.append(f"source {source_id}: screen_decision must be include or exclude")
        prohibited = PROHIBITED_SOURCE_PAYLOAD_FIELDS.intersection(source)
        if prohibited:
            errors.append(f"source {source_id}: prohibited bulky payload fields {sorted(prohibited)}")
        receipt_file = _inside_file(
            root, source.get("compaction_receipt_path"), f"source {source_id} compaction receipt", errors
        )
        if receipt_file:
            receipt = _read_json(receipt_file, errors)
            records = _list(receipt.get("records"))
            matching = [
                record for record in records
                if isinstance(record, dict)
                and str(record.get("compact_record_hash")) == str(source.get("compaction_record_hash"))
            ]
            if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py":
                errors.append(f"source {source_id}: invalid compaction receipt schema")
            if len(matching) != 1:
                errors.append(f"source {source_id}: compaction record hash does not resolve exactly once")
            elif (
                str(matching[0].get("canonical_identifier", "")) != str(source.get("canonical_identifier", ""))
                or str(matching[0].get("title", "")) != str(source.get("title", ""))
            ):
                errors.append(f"source {source_id}: canonical identity disagrees with compact receipt")
            elif str(matching[0].get("query_id", "")) not in {
                str(value) for value in _list(source.get("discovery_query_ids"))
            }:
                errors.append(f"source {source_id}: compact receipt query lacks reverse source linkage")
        for unit_id in _list(source.get("discovered_by_units")):
            if str(unit_id) not in unit_by_id:
                errors.append(f"source {source_id}: unknown discovered_by unit {unit_id}")
        for query_id in _list(source.get("discovery_query_ids")):
            if str(query_id) not in search_by_id:
                errors.append(f"source {source_id}: unknown discovery query {query_id}")
        for claim_id in _list(source.get("supported_claim_ids")):
            if str(claim_id) not in claim_by_id:
                errors.append(f"source {source_id}: unknown supported claim {claim_id}")

    searches_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query_id, query in search_by_id.items():
        _present(query, LEDGER_SCHEMAS["search_log.jsonl"], f"query {query_id}", errors)
        _required(
            query,
            (
                "research_unit_id", "query_family", "resource", "query", "compact_payload_paths",
                "pagination_trace", "acquired_source_ids", "original_verified_source_ids",
                "executed_by_agent_id", "executor_role", "origin_job_id", "outcome", "closure_note",
            ),
            f"query {query_id}",
            errors,
        )
        unit_id = str(query.get("research_unit_id", ""))
        searches_by_unit[unit_id].append(query)
        if unit_id not in unit_by_id:
            errors.append(f"query {query_id}: unknown research unit {unit_id}")
        subtopic_id = str(query.get("subtopic_id", ""))
        if subtopic_id and subtopic_id not in subtopic_by_id:
            errors.append(f"query {query_id}: unknown subtopic {subtopic_id}")
        result_count = _integer(query.get("result_count"), f"query {query_id} result_count", errors)
        dedup_count = _integer(query.get("deduplicated_count"), f"query {query_id} deduplicated_count", errors)
        screened_count = _integer(query.get("screened_count"), f"query {query_id} screened_count", errors)
        acquired_count = _integer(query.get("acquired_count"), f"query {query_id} acquired_count", errors)
        verified_count = _integer(
            query.get("original_verified_count"), f"query {query_id} original_verified_count", errors
        )
        page_count = _integer(query.get("page_count"), f"query {query_id} page_count", errors)
        if None not in (result_count, dedup_count, screened_count, acquired_count, verified_count):
            if dedup_count > result_count:
                errors.append(f"query {query_id}: deduplicated_count exceeds result_count")
            if screened_count != dedup_count:
                errors.append(f"query {query_id}: every deduplicated record must be screened")
            if not (verified_count <= acquired_count <= screened_count):
                errors.append(f"query {query_id}: verification/acquisition/screening counts are inconsistent")
            if len(_list(query.get("retained_source_ids"))) > verified_count:
                errors.append(f"query {query_id}: retained sources exceed original-verified records")
        if page_count is not None and page_count < 1:
            errors.append(f"query {query_id}: page_count must show at least one retrieved response page")
        if query.get("pagination_complete") is not True or query.get("continuation_exhausted") is not True:
            errors.append(f"query {query_id}: pagination or continuation is not exhausted")
        _validate_query_depth(root, query_id, query, source_by_id, errors)
        executor_role = str(query.get("executor_role", ""))
        executor_id = str(query.get("executed_by_agent_id", ""))
        unit = unit_by_id.get(unit_id, {})
        expected_executor = str(
            unit.get("auditor_agent_id" if executor_role == "auditor" else "worker_agent_id", "")
        )
        if executor_role not in {"worker", "auditor"} or not executor_id or executor_id != expected_executor:
            errors.append(f"query {query_id}: executor provenance does not match its controller-assigned role")
        origin_job = plan_job_by_id.get(str(query.get("origin_job_id", "")))
        expected_job_role = "worker" if executor_role == "worker" else (
            "closure_auditor" if unit.get("unit_type") == "closure_audit" else "auditor"
        )
        if (
            origin_job is None
            or str(origin_job.get("unit_id", "")) != unit_id
            or str(origin_job.get("role", "")) != expected_job_role
        ):
            errors.append(f"query {query_id}: origin_job_id does not prove the recorded executor role")
        closure_note = str(query.get("closure_note", "")).casefold()
        if not closure_note or any(phrase in closure_note for phrase in BAD_COMPLETION_PHRASES):
            errors.append(f"query {query_id}: invalid or rhetorical closure note")
        if query.get("rate_limit_pending") is not False:
            errors.append(f"query {query_id}: rate_limit_pending must be false")
        if str(query.get("outcome", "")).lower() in {"pending", "failed", "rate_limited"}:
            errors.append(f"query {query_id}: unresolved outcome {query.get('outcome')}")
        for source_id in _list(query.get("retained_source_ids")):
            source = source_by_id.get(str(source_id))
            if source is None:
                errors.append(f"query {query_id}: unknown retained source {source_id}")
            else:
                if query_id not in {str(value) for value in _list(source.get("discovery_query_ids"))}:
                    errors.append(f"query {query_id}: source {source_id} lacks reverse query linkage")
                if unit_id not in {str(value) for value in _list(source.get("discovered_by_units"))}:
                    errors.append(f"query {query_id}: source {source_id} lacks reverse unit linkage")
        for child_id in _list(query.get("new_subtopic_ids")):
            if str(child_id) not in subtopic_by_id:
                errors.append(f"query {query_id}: unknown new subtopic {child_id}")
        for claim_id in _list(query.get("new_claim_ids")):
            if str(claim_id) not in claim_by_id:
                errors.append(f"query {query_id}: unknown new claim {claim_id}")
        for candidate_id in _list(query.get("new_candidate_ids")):
            if str(candidate_id) not in candidate_by_id:
                errors.append(f"query {query_id}: unknown new candidate {candidate_id}")
        unit = unit_by_id.get(unit_id, {})
        if _list(query.get("new_candidate_ids")) and unit.get("unit_type") not in {"subtopic_compound", "global_perspective"}:
            errors.append(f"query {query_id}: evidence-only unit emitted candidates")

    for claim_id, claim in claim_by_id.items():
        _required(
            claim,
            ("subtopic_id", "claim", "evidence_kind", "calibration", "directionality", "allele_relevance", "audit_status"),
            f"claim {claim_id}",
            errors,
        )
        if str(claim.get("subtopic_id", "")) not in subtopic_by_id:
            errors.append(f"claim {claim_id}: unknown subtopic {claim.get('subtopic_id')}")
        if claim.get("calibration") not in CALIBRATIONS:
            errors.append(f"claim {claim_id}: invalid calibration {claim.get('calibration')}")
        if claim.get("audit_status") not in VERIFIED:
            errors.append(f"claim {claim_id}: audit_status is not verified")
        source_ids = [str(value) for value in _list(claim.get("source_ids"))]
        if not source_ids:
            errors.append(f"claim {claim_id}: no source_ids")
        for source_id in source_ids:
            source = source_by_id.get(source_id)
            if source is None:
                errors.append(f"claim {claim_id}: unknown source {source_id}")
                continue
            if not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                errors.append(f"claim {claim_id}: source {source_id} lacks original-content verification")
            if source.get("screen_decision") != "include":
                errors.append(f"claim {claim_id}: source {source_id} is not included")
            if _blank(source.get("original_pointer")) or _blank(source.get("verification_method")) or _blank(source.get("verification_scope")):
                errors.append(f"claim {claim_id}: source {source_id} lacks verification traceability")
            if claim_id not in {str(value) for value in _list(source.get("supported_claim_ids"))}:
                errors.append(f"claim {claim_id}: source {source_id} lacks reverse claim linkage")
        for contrary_id in _list(claim.get("contrary_claim_ids")):
            if str(contrary_id) not in claim_by_id:
                errors.append(f"claim {claim_id}: unknown contrary claim {contrary_id}")

    for edge_id, edge in edge_by_id.items():
        _present(edge, LEDGER_SCHEMAS["evidence_graph.jsonl"], f"edge {edge_id}", errors)
        _required(
            edge,
            ("from_node", "to_node", "relation", "direction", "directionality_status", "allele_mode_effect", "claim_ids", "audit_status"),
            f"edge {edge_id}",
            errors,
        )
        if edge.get("audit_status") not in VERIFIED:
            errors.append(f"edge {edge_id}: audit_status is not verified")
        if edge.get("directionality_status") not in {"supports_rescue", "opposes_rescue", "ambiguous"}:
            errors.append(f"edge {edge_id}: invalid directionality_status")
        edge_claims = [str(value) for value in _list(edge.get("claim_ids"))]
        if not edge_claims:
            errors.append(f"edge {edge_id}: no claim_ids")
        for claim_id in edge_claims:
            if claim_id not in claim_by_id:
                errors.append(f"edge {edge_id}: unknown claim {claim_id}")

    workers: dict[str, str] = {}
    auditors: dict[str, str] = {}
    units_by_type_and_perspective: set[tuple[str, str]] = set()
    for unit_id, unit in unit_by_id.items():
        _required(unit, ("unit_type", "worker_agent_id", "auditor_agent_id", "status", "audit_status"), f"unit {unit_id}", errors)
        worker = str(unit.get("worker_agent_id", ""))
        auditor = str(unit.get("auditor_agent_id", ""))
        if worker == auditor and worker:
            errors.append(f"unit {unit_id}: worker and auditor must differ")
        if worker in workers:
            errors.append(f"units {workers[worker]} and {unit_id}: worker agent reused")
        else:
            workers[worker] = unit_id
        if auditor in auditors:
            errors.append(f"units {auditors[auditor]} and {unit_id}: auditor agent reused")
        else:
            auditors[auditor] = unit_id
        if unit.get("status") not in FINAL_UNIT_STATUSES:
            errors.append(f"unit {unit_id}: invalid final status {unit.get('status')}")
        if unit.get("audit_status") not in VERIFIED:
            errors.append(f"unit {unit_id}: audit_status is not verified")
        planned = {str(value) for value in _list(unit.get("planned_query_families"))}
        completed = {str(value) for value in _list(unit.get("completed_query_families"))}
        if not planned or planned != completed:
            errors.append(f"unit {unit_id}: planned and completed query families must be equal and nonempty")
        unit_type = str(unit.get("unit_type", ""))
        required_families = required_query_families(unit_type)
        missing_families = required_families - planned
        if missing_families:
            errors.append(f"unit {unit_id}: missing required query families {sorted(missing_families)}")
        logged_families = {str(query.get("query_family", "")) for query in searches_by_unit.get(unit_id, [])}
        worker_logged_families = {
            str(query.get("query_family", ""))
            for query in searches_by_unit.get(unit_id, [])
            if query.get("executor_role") == "worker"
        }
        if planned - logged_families:
            errors.append(f"unit {unit_id}: declared families lack searches {sorted(planned - logged_families)}")
        if logged_families - planned:
            errors.append(f"unit {unit_id}: search families were not predeclared {sorted(logged_families - planned)}")
        if planned - worker_logged_families:
            errors.append(f"unit {unit_id}: worker did not execute declared families {sorted(planned - worker_logged_families)}")
        independent_ids = [str(value) for value in _list(unit.get("independent_audit_query_ids"))]
        if not independent_ids:
            errors.append(f"unit {unit_id}: no independent audit query")
        for query_id in independent_ids:
            query = search_by_id.get(query_id)
            if query is None:
                errors.append(f"unit {unit_id}: unknown independent audit query {query_id}")
            elif str(query.get("research_unit_id")) != unit_id:
                errors.append(f"unit {unit_id}: audit query {query_id} belongs to another unit")
            elif query.get("query_family") not in {"missing_branch", "counterevidence"}:
                errors.append(f"unit {unit_id}: audit query {query_id} is not a missing-branch or counterevidence search")
            elif query.get("executor_role") != "auditor" or str(query.get("executed_by_agent_id")) != auditor:
                errors.append(f"unit {unit_id}: audit query {query_id} was not independently executed by its auditor")
        if not searches_by_unit.get(unit_id):
            errors.append(f"unit {unit_id}: no search records")
        if unit.get("rate_limit_pending") is not False:
            errors.append(f"unit {unit_id}: rate_limit_pending must be false")
        if not _empty_collection(unit.get("known_high_yield_search_remaining")):
            errors.append(f"unit {unit_id}: known high-yield searches remain")
        if unit.get("unresolved_repair_count") != 0:
            errors.append(f"unit {unit_id}: unresolved_repair_count must be 0")
        if unit.get("status") == "evidence_absent_complete":
            if _list(unit.get("candidate_ids")):
                errors.append(f"unit {unit_id}: evidence-absent unit cannot have candidates")
            if _blank(unit.get("absence_reason")):
                errors.append(f"unit {unit_id}: evidence-absent unit needs an absence_reason")
        units_by_type_and_perspective.add((str(unit.get("unit_type", "")), str(unit.get("perspective", ""))))

    if set(workers).intersection(auditors):
        errors.append("research_units: an agent cannot serve as any worker and any auditor in the same run")
    present_broad = {perspective for unit_type, perspective in units_by_type_and_perspective if unit_type == "broad_evidence"}
    for missing in sorted(set(BROAD_DOMAINS) - present_broad):
        errors.append(f"research_units: missing broad evidence domain {missing}")
    present_global = {perspective for unit_type, perspective in units_by_type_and_perspective if unit_type == "global_perspective"}
    required_global = set(GLOBAL_PERSPECTIVES)
    has_prior_screen = bool(case.get("prior_screen_path") or case.get("prior_screen_rows"))
    if has_prior_screen and str(case.get("benchmark_mode", "")).casefold() != "blinded":
        required_global.add("prior_screen_context")
    if str(case.get("benchmark_mode", "")).casefold() == "blinded" and "prior_screen_context" in present_global:
        errors.append("research_units: blinded benchmark may not expose prior_screen_context")
    if case.get("wt_behavioural_parameters") or case.get("disease_model_behavioural_parameters"):
        required_global.add("behavioural_data_first")
    for missing in sorted(required_global - present_global):
        errors.append(f"research_units: missing global perspective {missing}")
    if not any(unit.get("unit_type") == "closure_audit" for unit in units):
        errors.append("research_units: missing closure_audit unit")

    audits_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for audit_id, audit in audit_by_id.items():
        _required(
            audit,
            ("unit_id", "auditor_agent_id", "perspective_distinctness_verified", "source_overlap_assessment", "final_status", "closure_basis"),
            f"audit {audit_id}",
            errors,
        )
        unit_id = str(audit.get("unit_id", ""))
        audits_by_unit[unit_id].append(audit)
        unit = unit_by_id.get(unit_id)
        if unit is None:
            errors.append(f"audit {audit_id}: unknown unit {unit_id}")
            continue
        if audit.get("auditor_agent_id") != unit.get("auditor_agent_id"):
            errors.append(f"audit {audit_id}: auditor does not match unit {unit_id}")
        if audit.get("final_status") != "verified":
            errors.append(f"audit {audit_id}: final_status must be verified")
        if audit.get("perspective_distinctness_verified") is not True:
            errors.append(f"audit {audit_id}: perspective distinctness is not verified")
        checked = [str(value) for value in _list(audit.get("checked_source_ids"))]
        if unit.get("status") == "audited_complete" and not checked:
            errors.append(f"audit {audit_id}: no decisive source checks")
        for source_id in checked:
            if source_id not in source_by_id:
                errors.append(f"audit {audit_id}: unknown checked source {source_id}")
        audit_queries = {str(value) for value in _list(audit.get("independent_query_ids"))}
        unit_queries = {str(value) for value in _list(unit.get("independent_audit_query_ids"))}
        if not audit_queries or audit_queries != unit_queries:
            errors.append(f"audit {audit_id}: independent queries do not match unit {unit_id}")
        findings = _list(audit.get("material_findings"))
        repairs = _list(audit.get("repairs_completed"))
        if findings and len(repairs) < len(findings):
            errors.append(f"audit {audit_id}: not every material finding has a completed repair")
        closure = str(audit.get("closure_basis", "")).lower()
        if any(phrase in closure for phrase in BAD_COMPLETION_PHRASES):
            errors.append(f"audit {audit_id}: invalid completion rationale")
    for unit_id in unit_by_id:
        if len(audits_by_unit.get(unit_id, [])) != 1:
            errors.append(f"unit {unit_id}: expected exactly one final unit audit")

    claims_by_subtopic: dict[str, list[str]] = defaultdict(list)
    for claim_id, claim in claim_by_id.items():
        claims_by_subtopic[str(claim.get("subtopic_id", ""))].append(claim_id)
    for subtopic_id, subtopic in subtopic_by_id.items():
        _required(subtopic, ("name", "relation_to_case", "status", "closure_reason"), f"subtopic {subtopic_id}", errors)
        parent_id = str(subtopic.get("parent_id", ""))
        if parent_id and parent_id not in subtopic_by_id:
            errors.append(f"subtopic {subtopic_id}: unknown parent {parent_id}")
        if subtopic.get("status") not in FINAL_UNIT_STATUSES:
            errors.append(f"subtopic {subtopic_id}: invalid final status")
        required_unit_ids = [str(value) for value in _list(subtopic.get("required_research_unit_ids"))]
        for unit_id in required_unit_ids:
            if unit_id not in unit_by_id:
                errors.append(f"subtopic {subtopic_id}: unknown required unit {unit_id}")
        own_units = [unit for unit in units if str(unit.get("subtopic_id", "")) == subtopic_id]
        if not any(unit.get("unit_type") == "subtopic_evidence" for unit in own_units):
            errors.append(f"subtopic {subtopic_id}: missing subtopic_evidence unit")
        if subtopic.get("candidate_relevant") is True and not any(unit.get("unit_type") == "subtopic_compound" for unit in own_units):
            errors.append(f"subtopic {subtopic_id}: candidate-relevant subtopic lacks compound unit")
        if subtopic.get("status") == "audited_complete" and not claims_by_subtopic.get(subtopic_id):
            errors.append(f"subtopic {subtopic_id}: audited completion has no claims")
        seen: set[str] = set()
        cursor = subtopic_id
        while cursor:
            if cursor in seen:
                errors.append(f"subtopic {subtopic_id}: parent cycle detected")
                break
            seen.add(cursor)
            cursor = str(subtopic_by_id.get(cursor, {}).get("parent_id", ""))

    structure_key_pattern = re.compile(
        r"^(INCHIKEY:[A-Z]{14}-[A-Z]{10}-[A-Z]|SMILES-SHA256:[0-9A-F]{64})$",
        re.IGNORECASE,
    )
    normalized_structure_keys: dict[str, str] = {}
    candidate_paths_by_candidate: dict[str, dict[str, dict[str, Any]]] = {}
    for candidate_id, candidate in candidate_by_id.items():
        _present(candidate, LEDGER_SCHEMAS["candidate_records.jsonl"], f"candidate {candidate_id}", errors)
        _required(
            candidate,
            (
                "canonical_name", "canonical_identifier", "registry_identifiers", "structure_identity_key",
                "chemical_node_id", "identity_source_ids", "entity_type", "human_gene", "worm_gene",
                "allele_mode", "worm_model", "origin", "source_research_unit_ids", "causal_paths",
                "rationale", "phenomic_interpretation", "decisive_uncertainty", "dossier_path",
                "council_disposition", "fact_audit_status",
            ),
            f"candidate {candidate_id}",
            errors,
        )
        if candidate.get("entity_type") != "discrete_chemical":
            errors.append(f"candidate {candidate_id}: entity_type must be discrete_chemical")
        if candidate.get("identity_verified") is not True:
            errors.append(f"candidate {candidate_id}: identity_verified must be true")
        identity_source_ids = [str(value) for value in _list(candidate.get("identity_source_ids"))]
        for source_id in identity_source_ids:
            source = source_by_id.get(source_id)
            if source is None:
                errors.append(f"candidate {candidate_id}: unknown identity source {source_id}")
            elif not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                errors.append(f"candidate {candidate_id}: identity source {source_id} is not verified")
            elif source.get("screen_decision") != "include" or any(
                _blank(source.get(field)) for field in ("original_pointer", "verification_method", "verification_scope")
            ):
                errors.append(f"candidate {candidate_id}: identity source {source_id} lacks verification traceability")
        chemical_id = str(candidate.get("canonical_identifier", "")).strip()
        registry_identifiers = candidate.get("registry_identifiers")
        if not isinstance(registry_identifiers, dict) or not registry_identifiers:
            errors.append(f"candidate {candidate_id}: registry_identifiers must be a nonempty object")
        elif chemical_id not in {str(value) for value in registry_identifiers.values()}:
            errors.append(f"candidate {candidate_id}: canonical identifier is absent from registry_identifiers")
        structure_key = str(candidate.get("structure_identity_key", "")).strip().upper()
        if not structure_key_pattern.match(structure_key):
            errors.append(f"candidate {candidate_id}: invalid structure_identity_key")
        if structure_key in normalized_structure_keys:
            errors.append(
                f"candidates {normalized_structure_keys[structure_key]} and {candidate_id}: duplicate cross-registry chemical identity"
            )
        else:
            normalized_structure_keys[structure_key] = candidate_id
        chemical_node_id = str(candidate.get("chemical_node_id", ""))
        if chemical_node_id != f"CHEM:{structure_key}":
            errors.append(f"candidate {candidate_id}: chemical_node_id must be derived from structure_identity_key")
        for field in ("human_gene", "worm_gene", "allele_mode"):
            if str(candidate.get(field, "")).casefold() != str(case.get(field, "")).casefold():
                errors.append(f"candidate {candidate_id}: {field} does not match case")
        if candidate.get("origin") not in {"de_novo", "prior_exact_model_screen", "mixed"}:
            errors.append(f"candidate {candidate_id}: invalid origin")
        source_units = [str(value) for value in _list(candidate.get("source_research_unit_ids"))]
        if not source_units:
            errors.append(f"candidate {candidate_id}: no source research units")
        for unit_id in source_units:
            unit = unit_by_id.get(unit_id)
            if unit is None:
                errors.append(f"candidate {candidate_id}: unknown source unit {unit_id}")
            elif unit.get("unit_type") not in {"subtopic_compound", "global_perspective"}:
                errors.append(f"candidate {candidate_id}: source unit {unit_id} is not a compound-generating unit")
        non_prior = [unit_id for unit_id in source_units if unit_by_id.get(unit_id, {}).get("perspective") != "prior_screen_context"]
        prior = [unit_id for unit_id in source_units if unit_by_id.get(unit_id, {}).get("perspective") == "prior_screen_context"]
        if candidate.get("origin") == "de_novo" and not non_prior:
            errors.append(f"candidate {candidate_id}: de_novo origin has only prior-screen units")
        if candidate.get("origin") == "prior_exact_model_screen" and not prior:
            errors.append(f"candidate {candidate_id}: prior-screen origin lacks a prior-screen unit")
        if candidate.get("origin") == "mixed" and (not prior or not non_prior):
            errors.append(f"candidate {candidate_id}: mixed origin needs prior and de novo units")
        causal_paths = _list(candidate.get("causal_paths"))
        path_by_id: dict[str, dict[str, Any]] = {}
        all_path_claims: set[str] = set()
        if not causal_paths:
            errors.append(f"candidate {candidate_id}: no connected causal path")
        for path_number, path in enumerate(causal_paths, 1):
            if not isinstance(path, dict):
                errors.append(f"candidate {candidate_id}: causal path {path_number} is not an object")
                continue
            _required(
                path,
                ("path_id", "edge_ids", "claim_ids", "start_node", "end_node", "expected_rescue_direction"),
                f"candidate {candidate_id} causal path {path_number}",
                errors,
            )
            path_id = str(path.get("path_id", ""))
            if path_id in path_by_id:
                errors.append(f"candidate {candidate_id}: duplicate causal path ID {path_id}")
            path_by_id[path_id] = path
            edge_ids = [str(value) for value in _list(path.get("edge_ids"))]
            path_claims = {str(value) for value in _list(path.get("claim_ids"))}
            all_path_claims.update(path_claims)
            if not edge_ids or not path_claims:
                errors.append(f"candidate {candidate_id} path {path_id}: edge_ids and claim_ids must be nonempty")
                continue
            path_edges = [edge_by_id.get(edge_id) for edge_id in edge_ids]
            if any(edge is None for edge in path_edges):
                errors.append(f"candidate {candidate_id} path {path_id}: unknown graph edge")
                continue
            concrete_edges = [edge for edge in path_edges if edge is not None]
            if str(path.get("start_node")) != chemical_node_id or concrete_edges[0].get("from_node") != chemical_node_id:
                errors.append(f"candidate {candidate_id} path {path_id}: path does not start at the candidate chemical node")
            if str(path.get("end_node")) != "CASE_WILD_TYPE_PHENOTYPE" or concrete_edges[-1].get("to_node") != "CASE_WILD_TYPE_PHENOTYPE":
                errors.append(f"candidate {candidate_id} path {path_id}: path does not terminate at wild-type phenotype restoration")
            if path.get("expected_rescue_direction") != "toward_wild_type":
                errors.append(f"candidate {candidate_id} path {path_id}: rescue direction is not toward_wild_type")
            for left, right in zip(concrete_edges, concrete_edges[1:]):
                if left.get("to_node") != right.get("from_node"):
                    errors.append(f"candidate {candidate_id} path {path_id}: graph edges are disconnected")
            for edge in concrete_edges:
                edge_claims = {str(value) for value in _list(edge.get("claim_ids"))}
                if not edge_claims.intersection(path_claims):
                    errors.append(f"candidate {candidate_id} path {path_id}: edge {edge.get('edge_id')} has no path claim")
                if edge.get("audit_status") not in VERIFIED or edge.get("directionality_status") != "supports_rescue":
                    errors.append(f"candidate {candidate_id} path {path_id}: edge {edge.get('edge_id')} is not audited rescue-supporting evidence")
            for claim_id in path_claims:
                claim = claim_by_id.get(claim_id)
                if claim is None:
                    errors.append(f"candidate {candidate_id}: unknown causal path claim {claim_id}")
                elif claim.get("calibration") in {"unresolved", "contradicted"}:
                    errors.append(f"candidate {candidate_id}: causal path uses {claim.get('calibration')} claim {claim_id}")
                elif str(claim.get("allele_relevance", "")).casefold() != str(case.get("allele_mode", "")).casefold():
                    errors.append(f"candidate {candidate_id}: causal path claim {claim_id} is not allele-mode compatible")
        candidate_paths_by_candidate[candidate_id] = path_by_id
        candidate_queries = [query for unit_id in source_units for query in searches_by_unit.get(unit_id, [])]
        if not any(candidate_id in {str(value) for value in _list(query.get("new_candidate_ids"))} for query in candidate_queries):
            errors.append(f"candidate {candidate_id}: no source-unit search emitted the candidate")
        if not any(all_path_claims.intersection({str(value) for value in _list(query.get("new_claim_ids"))}) for query in candidate_queries):
            errors.append(f"candidate {candidate_id}: causal path was not established by a source-unit search")
        if not any(
            query.get("query_family") == "identity_verification"
            and set(identity_source_ids).intersection({str(value) for value in _list(query.get("retained_source_ids"))})
            for query in candidate_queries
        ):
            errors.append(f"candidate {candidate_id}: no identity-verification query retained an identity source")
        _inside_file(root, candidate.get("dossier_path"), f"candidate {candidate_id} dossier", errors)
        if candidate.get("council_disposition") not in {"screen", "exclude"}:
            errors.append(f"candidate {candidate_id}: invalid council disposition")
        if candidate.get("fact_audit_status") != "verified":
            errors.append(f"candidate {candidate_id}: fact audit is not verified")
        if not any(candidate_id in {str(value) for value in _list(unit.get("candidate_ids"))} for unit in units):
            errors.append(f"candidate {candidate_id}: not emitted by any research unit")

    exchanges_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for exchange_id, exchange in exchange_by_id.items():
        _required(
            exchange,
            ("candidate_id", "role", "agent_id", "exchange_type", "content", "assertions", "claim_ids", "fact_audit_status"),
            f"exchange {exchange_id}",
            errors,
        )
        candidate_id = str(exchange.get("candidate_id", ""))
        exchanges_by_candidate[candidate_id].append(exchange)
        if candidate_id not in candidate_by_id:
            errors.append(f"exchange {exchange_id}: unknown candidate {candidate_id}")
        if exchange.get("fact_audit_status") != "verified":
            errors.append(f"exchange {exchange_id}: fact audit is not verified")
        claim_ids = [str(value) for value in _list(exchange.get("claim_ids"))]
        if not claim_ids:
            errors.append(f"exchange {exchange_id}: no audited claim references")
        assertions = _list(exchange.get("assertions"))
        assertion_claim_ids: set[str] = set()
        if not assertions:
            errors.append(f"exchange {exchange_id}: no structured material assertions")
        for assertion_number, assertion in enumerate(assertions, 1):
            if not isinstance(assertion, dict):
                errors.append(f"exchange {exchange_id}: assertion {assertion_number} is not an object")
                continue
            _required(
                assertion,
                ("claim_id", "stance", "text"),
                f"exchange {exchange_id} assertion {assertion_number}",
                errors,
            )
            assertion_claim_ids.add(str(assertion.get("claim_id", "")))
        if assertion_claim_ids != set(claim_ids):
            errors.append(f"exchange {exchange_id}: claim_ids do not exactly match structured assertions")
        for claim_id in claim_ids:
            if claim_id not in claim_by_id:
                errors.append(f"exchange {exchange_id}: unknown claim {claim_id}")
        exchange_type = str(exchange.get("exchange_type", ""))
        parent = str(exchange.get("responds_to_id", ""))
        if exchange_type not in {"case", "challenge", "response"}:
            errors.append(f"exchange {exchange_id}: invalid compact-council exchange_type {exchange_type}")
        if exchange_type == "response" and not parent:
            errors.append(f"exchange {exchange_id}: response lacks responds_to_id")
        if exchange_type in {"case", "challenge"} and parent:
            errors.append(f"exchange {exchange_id}: opening case or challenge cannot respond to another exchange")
        if exchange_type == "challenge":
            domains = {str(value) for value in _list(exchange.get("critique_domains"))}
            missing_domains = SKEPTIC_CRITIQUE_DOMAINS - domains
            if missing_domains:
                errors.append(f"exchange {exchange_id}: sceptic checklist lacks {sorted(missing_domains)}")
            challenge_items = _list(exchange.get("challenge_items"))
            item_domains = {
                str(item.get("domain")) for item in challenge_items if isinstance(item, dict)
            }
            if item_domains != SKEPTIC_CRITIQUE_DOMAINS or len(challenge_items) != len(SKEPTIC_CRITIQUE_DOMAINS):
                errors.append(f"exchange {exchange_id}: sceptic challenge_items must cover each critique domain exactly once")
            for item_number, item in enumerate(challenge_items, 1):
                if isinstance(item, dict):
                    _required(
                        item,
                        ("domain", "challenge", "claim_ids", "resolution_required"),
                        f"exchange {exchange_id} challenge item {item_number}",
                        errors,
                    )
                    if item.get("resolution_required") is not True or not _list(item.get("claim_ids")):
                        errors.append(f"exchange {exchange_id}: challenge item {item_number} lacks a resolvable claim challenge")
        if exchange_type == "response":
            response_items = _list(exchange.get("response_items"))
            response_domains = {
                str(item.get("domain")) for item in response_items if isinstance(item, dict)
            }
            if response_domains != SKEPTIC_CRITIQUE_DOMAINS or len(response_items) != len(SKEPTIC_CRITIQUE_DOMAINS):
                errors.append(f"exchange {exchange_id}: advocate response must answer each critique domain exactly once")
            for item_number, item in enumerate(response_items, 1):
                if isinstance(item, dict):
                    _required(
                        item,
                        ("domain", "response", "claim_ids", "disposition"),
                        f"exchange {exchange_id} response item {item_number}",
                        errors,
                    )
                    if item.get("disposition") not in {"accepted", "rebutted", "qualified"} or not _list(item.get("claim_ids")):
                        errors.append(f"exchange {exchange_id}: response item {item_number} is not substantive")
        if parent:
            parent_row = exchange_by_id.get(parent)
            if parent_row is None:
                errors.append(f"exchange {exchange_id}: unknown parent exchange {parent}")
            elif parent_row.get("candidate_id") != candidate_id:
                errors.append(f"exchange {exchange_id}: responds across candidates")

    for candidate_id, candidate in candidate_by_id.items():
        council = council_by_id.get(candidate_id)
        if council is None:
            errors.append(f"candidate {candidate_id}: missing council record")
            continue
        role_fields = (
            "advocate_agent_id",
            "skeptic_agent_id",
            "fact_auditor_agent_id",
        )
        _required(council, role_fields, f"council {candidate_id}", errors)
        candidate_exchanges = exchanges_by_candidate.get(candidate_id, [])
        exchange_material_claims = {
            str(value) for exchange in candidate_exchanges for value in _list(exchange.get("claim_ids"))
        }
        declared_material_claims = {str(value) for value in _list(council.get("material_claim_ids"))}
        if not declared_material_claims or declared_material_claims != exchange_material_claims:
            errors.append(f"council {candidate_id}: material_claim_ids do not exactly cover the debate")
        role_agents = [str(council.get(field, "")) for field in role_fields]
        if len(set(role_agents)) != len(role_agents):
            errors.append(f"council {candidate_id}: advocate, sceptic, and fact auditor must use distinct agents")
        if set(role_agents).intersection(set(workers) | set(auditors)):
            errors.append(f"council {candidate_id}: council agents must be independent of research workers and auditors")
        if council.get("direct_response_complete") is not True:
            errors.append(f"council {candidate_id}: advocate response is incomplete")
        if council.get("critique_checklist_complete") is not True:
            errors.append(f"council {candidate_id}: combined sceptic checklist is incomplete")
        if council.get("novelty_challenge_resolved") is not True:
            errors.append(f"council {candidate_id}: therapeutic-conservatism challenge is unresolved")
        if council.get("fact_audit_status") != "verified":
            errors.append(f"council {candidate_id}: fact audit is not verified")
        if council.get("disposition") != candidate.get("council_disposition"):
            errors.append(f"council {candidate_id}: disposition disagrees with candidate record")
        if not _empty_collection(council.get("unresolved_material_claims")):
            errors.append(f"council {candidate_id}: unresolved material claims remain")
        claim_verdicts = _list(council.get("claim_verdicts"))
        if not claim_verdicts:
            errors.append(f"council {candidate_id}: fact audit has no claim verdicts")
        verdict_by_claim: dict[str, str] = {}
        for verdict in claim_verdicts:
            if not isinstance(verdict, dict):
                errors.append(f"council {candidate_id}: claim verdict is not an object")
                continue
            claim_id = str(verdict.get("claim_id", ""))
            status = str(verdict.get("verdict", ""))
            if claim_id not in claim_by_id:
                errors.append(f"council {candidate_id}: verdict references unknown claim {claim_id}")
            if status not in {"supported", "qualified", "unsupported", "contradicted"}:
                errors.append(f"council {candidate_id}: invalid claim verdict {status!r}")
            checked_sources = [str(value) for value in _list(verdict.get("checked_source_ids"))]
            if not checked_sources:
                errors.append(f"council {candidate_id}: claim verdict {claim_id} has no checked sources")
            for source_id in checked_sources:
                source = source_by_id.get(source_id)
                if source is None:
                    errors.append(f"council {candidate_id}: claim verdict {claim_id} uses unknown source {source_id}")
                elif not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                    errors.append(f"council {candidate_id}: claim verdict {claim_id} uses an unverified source {source_id}")
            claim_sources = {
                str(value) for value in claim_by_id.get(claim_id, {}).get("source_ids", [])
            }
            if status in {"supported", "qualified"} and not set(checked_sources).intersection(claim_sources):
                errors.append(
                    f"council {candidate_id}: supporting verdict for {claim_id} checked no source attached to the claim"
                )
            verdict_by_claim[claim_id] = status
        if set(verdict_by_claim) != declared_material_claims:
            errors.append(f"council {candidate_id}: fact auditor did not verdict every and only material debate claim")
        independent_checks = _list(council.get("independent_checks"))
        if not independent_checks:
            errors.append(f"council {candidate_id}: fact auditor ran no independent source check")
        for check_number, check in enumerate(independent_checks, 1):
            if not isinstance(check, dict):
                errors.append(f"council {candidate_id}: independent check {check_number} is not an object")
                continue
            if (
                _blank(check.get("resource"))
                or _blank(check.get("query"))
                or str(check.get("executed_by_agent_id", "")) != str(council.get("fact_auditor_agent_id", ""))
            ):
                errors.append(f"council {candidate_id}: independent check {check_number} lacks valid fact-auditor provenance")
            checked_sources = [str(value) for value in _list(check.get("checked_source_ids"))]
            if not checked_sources:
                errors.append(f"council {candidate_id}: independent check {check_number} has no checked sources")
            for source_id in checked_sources:
                source = source_by_id.get(source_id)
                if source is None:
                    errors.append(f"council {candidate_id}: independent check {check_number} uses unknown source {source_id}")
                elif not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                    errors.append(f"council {candidate_id}: independent check {check_number} uses unverified source {source_id}")
        surviving_paths = [str(value) for value in _list(council.get("surviving_causal_path_ids"))]
        candidate_paths = candidate_paths_by_candidate.get(candidate_id, {})
        for path_id in surviving_paths:
            path = candidate_paths.get(path_id)
            if path is None:
                errors.append(f"council {candidate_id}: surviving path uses unknown candidate path {path_id}")
                continue
            for claim_id in _list(path.get("claim_ids")):
                if verdict_by_claim.get(str(claim_id)) not in {"supported", "qualified"}:
                    errors.append(f"council {candidate_id}: surviving path {path_id} has a claim without a supporting fact-audit verdict")
        if council.get("disposition") == "screen" and not surviving_paths:
            errors.append(f"council {candidate_id}: screened candidate has no surviving fact-audited causal path")
        debate_file = _inside_file(root, council.get("debate_path"), f"council {candidate_id} debate", errors)
        fact_audit_file = _inside_file(root, council.get("fact_audit_path"), f"council {candidate_id} fact audit", errors)
        exclusion_reason = str(council.get("exclusion_reason", ""))
        if council.get("disposition") == "exclude" and exclusion_reason not in {
            "wrong_direction",
            "causal_path_refuted",
            "assay_incompatible_confounding",
            "chemical_identity_not_screenable",
        }:
            errors.append(f"council {candidate_id}: exclusion lacks an allowed material reason")
        if council.get("disposition") == "screen" and exclusion_reason:
            errors.append(f"council {candidate_id}: screened candidate has an exclusion reason")
        role_to_agent = {
            "advocate": str(council.get("advocate_agent_id", "")),
            "skeptic": str(council.get("skeptic_agent_id", "")),
        }
        for exchange in candidate_exchanges:
            expected_agent = role_to_agent.get(str(exchange.get("role", "")))
            if expected_agent is None:
                errors.append(f"council {candidate_id}: unknown exchange role {exchange.get('role')}")
            elif str(exchange.get("agent_id", "")) != expected_agent:
                errors.append(f"council {candidate_id}: exchange {exchange.get('exchange_id')} agent does not match role assignment")
        advocate_cases = [item for item in candidate_exchanges if item.get("role") == "advocate" and item.get("exchange_type") == "case"]
        challenges = [item for item in candidate_exchanges if item.get("role") == "skeptic" and item.get("exchange_type") == "challenge"]
        responses = [item for item in candidate_exchanges if item.get("role") == "advocate" and item.get("exchange_type") == "response"]
        if len(advocate_cases) != 1 or len(challenges) != 1 or len(responses) != 1 or len(candidate_exchanges) != 3:
            errors.append(f"council {candidate_id}: compact council must contain exactly one case, challenge, and response")
        challenge_ids = {str(item.get("exchange_id")) for item in challenges}
        if not any(str(item.get("responds_to_id", "")) in challenge_ids for item in responses):
            errors.append(f"council {candidate_id}: sceptic challenge lacks an advocate response")
        if debate_file:
            debate_text = debate_file.read_text(encoding="utf-8-sig")
            for exchange in candidate_exchanges:
                if str(exchange.get("exchange_id")) not in debate_text:
                    errors.append(f"council {candidate_id}: debate file omits exchange {exchange.get('exchange_id')}")
        if fact_audit_file:
            fact_audit_text = fact_audit_file.read_text(encoding="utf-8-sig")
            for exchange in candidate_exchanges:
                if str(exchange.get("exchange_id")) not in fact_audit_text:
                    errors.append(f"council {candidate_id}: fact audit omits exchange {exchange.get('exchange_id')}")

    for candidate_id in council_by_id:
        if candidate_id not in candidate_by_id:
            errors.append(f"council record references unknown candidate {candidate_id}")

    return errors


def validate_staged_commit(
    run_folder: str | Path,
    job: dict[str, Any],
    result_paths: list[str],
    active_agent_id: str,
) -> list[str]:
    """Validate a proposed audited merge before canonical ledgers are changed."""
    root = Path(run_folder).expanduser().resolve()
    errors: list[str] = []
    case = _read_json(root / "case.json", errors)
    snapshots: dict[str, list[dict[str, Any]]] = {
        filename: _read_jsonl(root / filename, errors) for filename in LEDGER_SCHEMAS
    }

    for relative_path in result_paths:
        result_file = _inside_file(root, relative_path, "staged result", errors)
        if not result_file:
            continue
        result = _read_json(result_file, errors)
        result_job_id = str(result.get("job_id", ""))
        for field in ("job_id", "packet_hash", "all_chunks_processed", "outcome", "ledger_updates", "approved_subtopics"):
            if field not in result:
                errors.append(f"staged result {relative_path}: missing {field}")
        if result.get("all_chunks_processed") is not True:
            errors.append(f"staged result {relative_path}: all_chunks_processed must be true")
        updates = result.get("ledger_updates")
        if not isinstance(updates, dict):
            errors.append(f"staged result {relative_path}: ledger_updates must be an object")
            continue
        for filename, rows in updates.items():
            if filename not in LEDGER_SCHEMAS:
                errors.append(f"staged result {relative_path}: unapproved ledger {filename}")
                continue
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                errors.append(f"staged result {relative_path}: {filename} must contain objects")
                continue
            key = LEDGER_SCHEMAS[filename][0]
            current = {str(row.get(key)): row for row in snapshots[filename]}
            for row in rows:
                _present(row, LEDGER_SCHEMAS[filename], f"staged {filename} row", errors)
                identity = str(row.get(key, ""))
                if filename == "search_log.jsonl" and str(row.get("origin_job_id", "")) != result_job_id:
                    errors.append(
                        f"staged search {identity}: origin_job_id must match the result that created the search"
                    )
                if identity:
                    if filename == "research_units.jsonl" and identity in current:
                        protected = {
                            field: current[identity].get(field)
                            for field in (
                                "worker_agent_id", "auditor_agent_id", "status", "audit_status",
                                "rate_limit_pending", "unresolved_repair_count",
                            )
                        }
                        current[identity] = {**current[identity], **row, **protected}
                    else:
                        current[identity] = row
            snapshots[filename] = list(current.values())

    sources = _index(snapshots["source_corpus.jsonl"], "source_id", "staged sources", errors)
    searches = _index(snapshots["search_log.jsonl"], "query_id", "staged searches", errors)
    claims = _index(snapshots["claim_ledger.jsonl"], "claim_id", "staged claims", errors)
    edges = _index(snapshots["evidence_graph.jsonl"], "edge_id", "staged edges", errors)
    units = _index(snapshots["research_units.jsonl"], "unit_id", "staged units", errors)
    candidates = _index(snapshots["candidate_records.jsonl"], "candidate_id", "staged candidates", errors)
    plan = _read_json(root / "execution_plan.json", errors)
    plan_jobs = _index(plan.get("jobs", []), "job_id", "staged execution jobs", errors)

    for source_id, source in sources.items():
        unexpected = set(source) - SOURCE_ALLOWED_FIELDS
        if unexpected:
            errors.append(f"staged source {source_id}: unrecognized fields {sorted(unexpected)}")
        _required(
            source,
            (
                "canonical_identifier", "identifier_type", "title", "source_kind", "source_family",
                "screen_decision", "original_pointer", "verification_method", "verification_scope",
                "compaction_receipt_path", "compaction_record_hash",
            ),
            f"staged source {source_id}",
            errors,
        )
        if source.get("screen_decision") not in {"include", "exclude"}:
            errors.append(f"staged source {source_id}: screen_decision must be include or exclude")
        receipt_file = _inside_file(
            root, source.get("compaction_receipt_path"), f"staged source {source_id} compaction receipt", errors
        )
        if receipt_file:
            receipt = _read_json(receipt_file, errors)
            matches = [
                row for row in _list(receipt.get("records"))
                if isinstance(row, dict)
                and str(row.get("compact_record_hash")) == str(source.get("compaction_record_hash"))
            ]
            if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py" or len(matches) != 1:
                errors.append(f"staged source {source_id}: invalid compaction provenance")
            elif (
                str(matches[0].get("canonical_identifier", "")) != str(source.get("canonical_identifier", ""))
                or str(matches[0].get("title", "")) != str(source.get("title", ""))
            ):
                errors.append(f"staged source {source_id}: compact identity mismatch")
            elif str(matches[0].get("query_id", "")) not in {
                str(value) for value in _list(source.get("discovery_query_ids"))
            }:
                errors.append(f"staged source {source_id}: compact receipt query lacks reverse source linkage")

    for query_id, query in searches.items():
        unit = units.get(str(query.get("research_unit_id", "")), {})
        executor_role = str(query.get("executor_role", ""))
        expected_agent = str(
            unit.get("auditor_agent_id" if executor_role == "auditor" else "worker_agent_id", "")
        )
        if executor_role not in {"worker", "auditor"} or str(query.get("executed_by_agent_id", "")) != expected_agent:
            errors.append(f"staged query {query_id}: executor provenance mismatch")
        origin_job = plan_jobs.get(str(query.get("origin_job_id", "")))
        expected_job_role = "worker" if executor_role == "worker" else (
            "closure_auditor" if unit.get("unit_type") == "closure_audit" else "auditor"
        )
        if (
            origin_job is None
            or str(origin_job.get("unit_id", "")) != str(query.get("research_unit_id", ""))
            or str(origin_job.get("role", "")) != expected_job_role
        ):
            errors.append(f"staged query {query_id}: origin job does not match executor role")
        counts = []
        for field in ("result_count", "deduplicated_count", "screened_count", "acquired_count", "original_verified_count"):
            counts.append(_integer(query.get(field), f"staged query {query_id} {field}", errors))
        page_count = _integer(query.get("page_count"), f"staged query {query_id} page_count", errors)
        if None not in counts:
            result_count, dedup_count, screened_count, acquired_count, verified_count = counts
            if dedup_count > result_count or screened_count != dedup_count or not (
                verified_count <= acquired_count <= screened_count
            ):
                errors.append(f"staged query {query_id}: inconsistent depth counts")
            if len(_list(query.get("retained_source_ids"))) > verified_count:
                errors.append(f"staged query {query_id}: retained sources exceed verified records")
        if page_count is not None and page_count < 1:
            errors.append(f"staged query {query_id}: page_count must be positive")
        if query.get("pagination_complete") is not True or query.get("continuation_exhausted") is not True:
            errors.append(f"staged query {query_id}: retrieval continuation remains open")
        _validate_query_depth(root, query_id, query, sources, errors, label_prefix="staged query")
        closure_note = str(query.get("closure_note", "")).casefold()
        if not closure_note or any(phrase in closure_note for phrase in BAD_COMPLETION_PHRASES):
            errors.append(f"staged query {query_id}: invalid completion rationale")

    for claim_id, claim in claims.items():
        if claim.get("calibration") not in CALIBRATIONS:
            errors.append(f"staged claim {claim_id}: invalid calibration {claim.get('calibration')}")
        if job.get("kind") in {
            "unit_auditor", "closure_auditor", "merge_auditor", "council_fact_auditor", "final_repair_auditor"
        } and claim.get("audit_status") not in VERIFIED:
            errors.append(f"staged claim {claim_id}: audited commit requires verified audit_status")
        for source_id in _list(claim.get("source_ids")):
            source = sources.get(str(source_id))
            if source is None:
                errors.append(f"staged claim {claim_id}: unknown source {source_id}")
            elif not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                errors.append(f"staged claim {claim_id}: source {source_id} is not original-content verified")
            elif source.get("screen_decision") != "include":
                errors.append(f"staged claim {claim_id}: source {source_id} is not included")
            elif claim_id not in {str(value) for value in _list(source.get("supported_claim_ids"))}:
                errors.append(f"staged claim {claim_id}: source {source_id} lacks reverse claim linkage")

    for edge_id, edge in edges.items():
        if edge.get("directionality_status") not in {"supports_rescue", "opposes_rescue", "ambiguous"}:
            errors.append(f"staged edge {edge_id}: invalid directionality_status")
        if job.get("kind") in {
            "unit_auditor", "closure_auditor", "merge_auditor", "council_fact_auditor", "final_repair_auditor"
        } and edge.get("audit_status") not in VERIFIED:
            errors.append(f"staged edge {edge_id}: audited commit requires verified audit_status")
        for claim_id in _list(edge.get("claim_ids")):
            if str(claim_id) not in claims:
                errors.append(f"staged edge {edge_id}: unknown claim {claim_id}")

    structure_keys: dict[str, str] = {}
    structure_key_pattern = re.compile(
        r"^(INCHIKEY:[A-Z]{14}-[A-Z]{10}-[A-Z]|SMILES-SHA256:[0-9A-F]{64})$", re.IGNORECASE
    )
    for candidate_id, candidate in candidates.items():
        if candidate.get("entity_type") != "discrete_chemical" or candidate.get("identity_verified") is not True:
            errors.append(f"staged candidate {candidate_id}: candidate is not an identity-verified discrete chemical")
        registry_identifiers = candidate.get("registry_identifiers")
        if (
            not isinstance(registry_identifiers, dict)
            or not registry_identifiers
            or str(candidate.get("canonical_identifier", "")) not in {str(value) for value in registry_identifiers.values()}
        ):
            errors.append(f"staged candidate {candidate_id}: registry identity mapping is invalid")
        for source_id in _list(candidate.get("identity_source_ids")):
            source = sources.get(str(source_id))
            if source is None or not all(
                source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")
            ):
                errors.append(f"staged candidate {candidate_id}: identity source {source_id} is not verified")
        structure_key = str(candidate.get("structure_identity_key", "")).upper()
        if not structure_key_pattern.match(structure_key):
            errors.append(f"staged candidate {candidate_id}: invalid structure identity key")
        if structure_key in structure_keys and structure_keys[structure_key] != candidate_id:
            errors.append(f"staged candidates {structure_keys[structure_key]} and {candidate_id}: duplicate structure identity")
        structure_keys[structure_key] = candidate_id
        chemical_node = str(candidate.get("chemical_node_id", ""))
        if chemical_node != f"CHEM:{structure_key}":
            errors.append(f"staged candidate {candidate_id}: chemical node mismatch")
        staged_paths = _list(candidate.get("causal_paths"))
        if not staged_paths:
            errors.append(f"staged candidate {candidate_id}: no connected causal path")
        for path in staged_paths:
            if not isinstance(path, dict):
                errors.append(f"staged candidate {candidate_id}: causal path is not an object")
                continue
            edge_ids = [str(value) for value in _list(path.get("edge_ids"))]
            path_edges = [edges.get(edge_id) for edge_id in edge_ids]
            if not edge_ids or any(edge is None for edge in path_edges):
                errors.append(f"staged candidate {candidate_id}: causal path has unknown edges")
                continue
            concrete = [edge for edge in path_edges if edge is not None]
            if concrete[0].get("from_node") != chemical_node or concrete[-1].get("to_node") != "CASE_WILD_TYPE_PHENOTYPE":
                errors.append(f"staged candidate {candidate_id}: causal path endpoints are invalid")
            if any(left.get("to_node") != right.get("from_node") for left, right in zip(concrete, concrete[1:])):
                errors.append(f"staged candidate {candidate_id}: causal path is disconnected")
            if path.get("expected_rescue_direction") != "toward_wild_type":
                errors.append(f"staged candidate {candidate_id}: causal path direction is invalid")
            path_claims = {str(value) for value in _list(path.get("claim_ids"))}
            if not path_claims or any(claim_id not in claims for claim_id in path_claims):
                errors.append(f"staged candidate {candidate_id}: causal path has unknown claims")
            if any(edge.get("directionality_status") != "supports_rescue" for edge in concrete):
                errors.append(f"staged candidate {candidate_id}: causal path contains a non-rescue edge")
            for claim_id in path_claims:
                claim = claims.get(claim_id, {})
                if claim.get("calibration") in {"unresolved", "contradicted"}:
                    errors.append(f"staged candidate {candidate_id}: causal path uses a non-supporting claim")
                if str(claim.get("allele_relevance", "")).casefold() != str(case.get("allele_mode", "")).casefold():
                    errors.append(f"staged candidate {candidate_id}: causal path claim is not allele-mode compatible")

    unit_id = str(job.get("unit_id", ""))
    unit = units.get(unit_id)
    if unit and job.get("kind") in {"unit_auditor", "closure_auditor"}:
        planned = {str(value) for value in _list(unit.get("planned_query_families"))}
        completed = {str(value) for value in _list(unit.get("completed_query_families"))}
        required = required_query_families(str(unit.get("unit_type", "")))
        unit_queries = [row for row in searches.values() if str(row.get("research_unit_id")) == unit_id]
        worker_families = {
            str(row.get("query_family")) for row in unit_queries if row.get("executor_role") == "worker"
        }
        if not planned or planned != completed or required - planned or planned - worker_families:
            errors.append(f"staged unit {unit_id}: required worker query families are incomplete")
        independent_ids = {str(value) for value in _list(unit.get("independent_audit_query_ids"))}
        if not independent_ids:
            errors.append(f"staged unit {unit_id}: independent audit query is missing")
        for query_id in independent_ids:
            query = searches.get(query_id, {})
            if (
                query.get("executor_role") != "auditor"
                or str(query.get("executed_by_agent_id", "")) != active_agent_id
                or query.get("query_family") not in {"missing_branch", "counterevidence"}
            ):
                errors.append(f"staged unit {unit_id}: independent audit query provenance is invalid")
        matching_audits = [
            row for row in snapshots["unit_audits.jsonl"] if str(row.get("unit_id")) == unit_id
        ]
        if len(matching_audits) != 1 or str(matching_audits[0].get("auditor_agent_id", "")) != active_agent_id:
            errors.append(f"staged unit {unit_id}: independent unit audit record is missing or misattributed")

    candidate_id = str(job.get("candidate_id", ""))
    if candidate_id:
        candidate_exchanges = [
            row for row in snapshots["council_exchanges.jsonl"]
            if str(row.get("candidate_id", "")) == candidate_id
        ]
        for exchange in candidate_exchanges:
            exchange_id = str(exchange.get("exchange_id", ""))
            assertions = _list(exchange.get("assertions"))
            claim_ids = {str(value) for value in _list(exchange.get("claim_ids"))}
            assertion_claims = {
                str(item.get("claim_id")) for item in assertions if isinstance(item, dict)
            }
            if not assertions or assertion_claims != claim_ids:
                errors.append(f"staged council exchange {exchange_id}: assertions do not cover claim_ids")
            exchange_type = str(exchange.get("exchange_type", ""))
            if exchange_type == "challenge":
                items = _list(exchange.get("challenge_items"))
                domains = {str(item.get("domain")) for item in items if isinstance(item, dict)}
                if domains != SKEPTIC_CRITIQUE_DOMAINS or len(items) != len(SKEPTIC_CRITIQUE_DOMAINS):
                    errors.append(f"staged council exchange {exchange_id}: incomplete sceptic domain challenges")
            if exchange_type == "response":
                items = _list(exchange.get("response_items"))
                domains = {str(item.get("domain")) for item in items if isinstance(item, dict)}
                if domains != SKEPTIC_CRITIQUE_DOMAINS or len(items) != len(SKEPTIC_CRITIQUE_DOMAINS):
                    errors.append(f"staged council exchange {exchange_id}: incomplete advocate responses")
        if job.get("kind") == "council_fact_auditor":
            cases = [row for row in candidate_exchanges if row.get("exchange_type") == "case"]
            challenges = [row for row in candidate_exchanges if row.get("exchange_type") == "challenge"]
            responses = [row for row in candidate_exchanges if row.get("exchange_type") == "response"]
            if len(cases) != 1 or len(challenges) != 1 or len(responses) != 1 or len(candidate_exchanges) != 3:
                errors.append(f"staged council {candidate_id}: debate does not contain exactly three substantive exchanges")

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_program.py <run_folder>", file=sys.stderr)
        return 2
    errors = validate_run(argv[1])
    if errors:
        print(f"VALIDATION FAILED ({len(errors)} issue(s))")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
