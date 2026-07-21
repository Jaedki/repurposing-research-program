#!/usr/bin/env python3
"""Schema-v7 role contracts, bounded sharding, and immutable packet construction."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 7
PACKET_MODEL_VERSION = "schema-v7-role-packets-v1"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def _safe(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.")
    if not cleaned:
        raise ValueError("identifier becomes empty after path normalization")
    return cleaned


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


COLLECTION_ID_FIELDS: Mapping[str, str] = {
    "broad_case_model_snapshots": "snapshot_id",
    "case_model_records": "record_id",
    "pharmacology_seed_emissions": "emission_id",
    "source_universes": "source_universe_id",
    "query_plans": "query_plan_id",
    "coverage_proofs": "coverage_proof_id",
    "source_mappings": "mapping_id",
    "discovery_routes": "route_id",
    "candidate_seeds": "seed_id",
    "seed_dispositions": "seed_disposition_id",
    "identity_resolutions": "identity_resolution_id",
    "screening_decisions": "decision_id",
    "normalized_interventions": "normalized_intervention_id",
    "quarantined_seeds": "quarantine_id",
    "screen_records": "screen_record_id",
    "screened_candidates": "screened_candidate_id",
    "seed_candidate_mappings": "link_id",
    "triage_dispositions": "disposition_id",
    "deep_selection_records": "selection_record_id",
    "deep_evidence_packages": "package_id",
    "deep_candidates": "candidate_id",
    "decision_profiles": "profile_id",
    "structured_safety": "safety_record_id",
    "structured_exposure": "exposure_record_id",
    "ranking_preparation_records": "preparation_id",
    "audit_assignments": "assignment_id",
    "audit_records": "audit_record_id",
    "audit_corrections": "correction_id",
    "portfolio_review_items": "review_item_id",
    "council_records": "council_record_id",
    "portfolio_review_records": "portfolio_review_id",
    "portfolio_rank_records": "candidate_id",
    "validation_reports": "validation_report_id",
    "output_manifests": "output_manifest_id",
}


def _role(
    role: str,
    *,
    instruction: str,
    inputs: Iterable[str],
    outputs: Iterable[str],
    required_outputs: Iterable[str] = (),
    case_fields: Iterable[str] = (),
) -> dict[str, Any]:
    output_names = tuple(outputs)
    return {
        "schema_version": SCHEMA_VERSION,
        "packet_model_version": PACKET_MODEL_VERSION,
        "role": role,
        "instruction": instruction,
        "allowed_input_collections": sorted(set(inputs)),
        "allowed_output_collections": sorted(set(output_names)),
        "required_output_collections": sorted(set(required_outputs)),
        "output_id_fields": {
            name: COLLECTION_ID_FIELDS[name] for name in sorted(set(output_names))
        },
        "allowed_case_fields": sorted(set(case_fields)),
        "result_top_level_fields": [
            "schema_version",
            "job_id",
            "attempt_id",
            "packet_hash",
            "dependency_commit_ids",
            "outcome",
            "shard_complete",
            "records",
            "progress",
            "budget_usage",
        ],
        "result_required_fields": [
            "schema_version",
            "job_id",
            "attempt_id",
            "packet_hash",
            "dependency_commit_ids",
            "outcome",
            "shard_complete",
            "records",
            "progress",
            "budget_usage",
        ],
        "prohibited_context": [
            "benchmark_labels",
            "benchmark_expected_outcomes",
            "unrelated_candidate_records",
        ],
    }


_CASE_CORE = (
    "case_id",
    "case_revision_id",
    "case_status",
    "gene",
    "disease",
    "phenotypes",
    "endpoints",
)
_CASE_CONSTRAINTS = (
    *_CASE_CORE,
    "disease_subtype",
    "population",
    "tissue",
    "disease_stage",
    "target_product_profile",
    "contraindications",
    "excluded_intervention_categories",
)


ROLE_CONTRACTS: Mapping[str, Mapping[str, Any]] = {
    "case_model_constructor": _role(
        "case_model_constructor",
        instruction=(
            "Construct only the typed broad case-model collections from the immutable case revision; "
            "retain grounded pharmacology mappings and unresolved direction conflicts."
        ),
        inputs=(),
        outputs=(
            "broad_case_model_snapshots",
            "case_model_records",
            "pharmacology_seed_emissions",
        ),
        required_outputs=("broad_case_model_snapshots",),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "source_universe_planner": _role(
        "source_universe_planner",
        instruction=(
            "Declare bounded source universes and query plans. Do not retrieve, screen, rank, "
            "audit, or make portfolio decisions."
        ),
        inputs=("broad_case_model_snapshots", "case_model_records"),
        outputs=("source_universes", "query_plans"),
        required_outputs=("source_universes", "query_plans"),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "discovery_source_worker": _role(
        "discovery_source_worker",
        instruction=(
            "Traverse only the assigned declared source/query shard and emit coverage, mappings, "
            "routes, and lightweight seeds. Stop after source-bounded seed emission."
        ),
        inputs=("source_universes", "query_plans"),
        outputs=(
            "coverage_proofs",
            "source_mappings",
            "discovery_routes",
            "candidate_seeds",
        ),
        required_outputs=("coverage_proofs",),
        case_fields=_CASE_CORE,
    ),
    "identity_worker": _role(
        "identity_worker",
        instruction=(
            "Normalize only the assigned seeds, preserving every seed and conflict. Emit explicit "
            "identity resolution and disposition records; do not perform therapeutic ranking."
        ),
        inputs=("candidate_seeds", "source_mappings", "discovery_routes"),
        outputs=(
            "identity_resolutions",
            "seed_dispositions",
            "normalized_interventions",
            "quarantined_seeds",
        ),
        required_outputs=("identity_resolutions", "seed_dispositions"),
        case_fields=("case_id", "case_revision_id", "endpoints"),
    ),
    "preliminary_triage_worker": _role(
        "preliminary_triage_worker",
        instruction=(
            "Apply only the declared lightweight triage rules to the assigned normalized interventions. "
            "Keep endpoint assessments explicit and do not build deep dossiers or ranks."
        ),
        inputs=("seed_dispositions", "normalized_interventions", "identity_resolutions"),
        outputs=(
            "screening_decisions",
            "screen_records",
            "screened_candidates",
            "seed_candidate_mappings",
            "triage_dispositions",
        ),
        required_outputs=("screening_decisions", "screen_records", "triage_dispositions"),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "deep_evidence_worker": _role(
        "deep_evidence_worker",
        instruction=(
            "Build original-content-grounded deep packages only for the assigned screened candidates, "
            "using exact identities and endpoint-specific evidence. Do not rank or select a portfolio."
        ),
        inputs=("screened_candidates", "screen_records"),
        outputs=(
            "deep_selection_records",
            "deep_evidence_packages",
            "deep_candidates",
            "structured_safety",
            "structured_exposure",
        ),
        required_outputs=("deep_selection_records", "deep_evidence_packages"),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "ranking_preparation_worker": _role(
        "ranking_preparation_worker",
        instruction=(
            "Derive typed evidence features and the separate pre-audit therapeutic-confidence and "
            "research-priority orders for the assigned deep candidates using the frozen schema-v7 "
            "decision tables. Do not apply audit, council, portfolio-membership, or output policy."
        ),
        inputs=("deep_candidates", "deep_evidence_packages"),
        outputs=("decision_profiles", "ranking_preparation_records"),
        required_outputs=("decision_profiles", "ranking_preparation_records"),
        case_fields=("case_id", "case_revision_id", "endpoints"),
    ),
    "audit_sampling_worker": _role(
        "audit_sampling_worker",
        instruction=(
            "Apply the supplied frozen audit plan to only the assigned preparation shard and emit one "
            "deterministic assignment per candidate, including explicit unaudited status. Do not invent "
            "or revise audit policy."
        ),
        inputs=("ranking_preparation_records",),
        outputs=("audit_assignments",),
        required_outputs=("audit_assignments",),
        case_fields=("case_id", "case_revision_id", "endpoints"),
    ),
    "candidate_auditor": _role(
        "candidate_auditor",
        instruction=(
            "Audit only the assigned frozen claims/candidates independently. Emit typed outcomes, append-only "
            "corrections, and bounded decision-changing portfolio-review items. Do not alter the frozen audit "
            "or ranking policy and do not silently replace a source record."
        ),
        inputs=("audit_assignments",),
        outputs=("audit_records", "audit_corrections", "portfolio_review_items"),
        required_outputs=("audit_records",),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "council_portfolio_reviewer": _role(
        "council_portfolio_reviewer",
        instruction=(
            "Review only typed decision-changing audited issues under the supplied frozen portfolio policy. "
            "Use evidence ancestry rather than agent counts for independence and emit decomposed council and "
            "three-rank portfolio records; do not redesign scores, audit rules, or user-facing outputs."
        ),
        inputs=("portfolio_review_items", "audit_records"),
        outputs=("council_records", "portfolio_review_records", "portfolio_rank_records"),
        required_outputs=("portfolio_review_records", "portfolio_rank_records"),
        case_fields=_CASE_CONSTRAINTS,
    ),
    "final_structural_validator": _role(
        "final_structural_validator",
        instruction=(
            "Validate committed hashes, reconciliation, required-stage completion, and declared gaps. "
            "Do not reinterpret scientific evidence or ranking policy."
        ),
        inputs=(),
        outputs=("validation_reports",),
        required_outputs=("validation_reports",),
        case_fields=("case_id", "case_revision_id", "case_status"),
    ),
    "final_output_builder": _role(
        "final_output_builder",
        instruction=(
            "Invoke the separately governed output contract from the validated committed snapshot and emit "
            "only output artifact manifests. Do not redesign user-facing content."
        ),
        inputs=("validation_reports",),
        outputs=("output_manifests",),
        required_outputs=("output_manifests",),
        case_fields=("case_id", "case_revision_id", "case_status"),
    ),
}


def role_contract(role: str) -> dict[str, Any]:
    try:
        return dict(ROLE_CONTRACTS[role])
    except KeyError as exc:
        raise ValueError(f"unknown schema-v7 role: {role}") from exc


def contract_hash(role: str) -> str:
    return canonical_sha256(role_contract(role))


def write_role_contracts(runtime_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for role in sorted(ROLE_CONTRACTS):
        body = role_contract(role)
        payload = canonical_bytes(body)
        path = runtime_root / "contracts" / f"{_safe(role)}.json"
        _atomic_write(path, payload)
        result[role] = {
            "path": path.relative_to(runtime_root.parent).as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest().upper(),
            "bytes": len(payload),
        }
    return result


def shard_records(
    records: Iterable[Mapping[str, Any]],
    *,
    id_field: str,
    max_records: int,
    max_bytes: int,
    envelope: Mapping[str, Any] | None = None,
) -> list[list[dict[str, Any]]]:
    """Deterministically shard full records under count and canonical-byte limits."""

    if max_records < 1 or max_bytes < 256:
        raise ValueError("shard limits must be positive and max_bytes must be at least 256")
    unique: dict[str, dict[str, Any]] = {}
    for value in records:
        record = dict(value)
        identity = str(record.get(id_field, "")).strip()
        if not identity:
            raise ValueError(f"record lacks {id_field}")
        if identity in unique and unique[identity] != record:
            raise ValueError(f"idempotency conflict for {identity}")
        unique[identity] = record
    base = dict(envelope or {})
    shards: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for identity in sorted(unique):
        record = unique[identity]
        proposed = [*current, record]
        if current and (
            len(proposed) > max_records
            or len(canonical_bytes({**base, "records": proposed})) > max_bytes
        ):
            shards.append(current)
            current = [record]
        else:
            current = proposed
        if len(canonical_bytes({**base, "records": current})) > max_bytes:
            raise ValueError(f"one record exceeds the configured shard byte limit: {identity}")
    if current:
        shards.append(current)
    return shards


def shard_record_refs(
    refs: Iterable[Mapping[str, Any]],
    *,
    max_records: int,
    max_source_bytes: int,
    max_packet_bytes: int,
) -> list[list[dict[str, Any]]]:
    """Shard record references by both referenced bytes and packet-envelope bytes."""

    if max_records < 1 or max_source_bytes < 1 or max_packet_bytes < 512:
        raise ValueError("invalid record-reference shard limits")
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in refs:
        ref = dict(raw)
        key = (str(ref.get("collection", "")), str(ref.get("record_id", "")))
        if not all(key):
            raise ValueError("record reference requires collection and record_id")
        if key in unique and unique[key] != ref:
            raise ValueError(f"conflicting record reference: {key[0]}:{key[1]}")
        unique[key] = ref
    ordered = [unique[key] for key in sorted(unique)]
    shards: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    source_bytes = 0
    for ref in ordered:
        record_bytes = int(ref.get("bytes", 0))
        if record_bytes < 0:
            raise ValueError("record reference bytes must be nonnegative")
        proposed = [*current, ref]
        if current and (
            len(proposed) > max_records
            or source_bytes + record_bytes > max_source_bytes
            or len(canonical_bytes({"record_refs": proposed})) > max_packet_bytes
        ):
            shards.append(current)
            current = [ref]
            source_bytes = record_bytes
        else:
            current = proposed
            source_bytes += record_bytes
        if (
            source_bytes > max_source_bytes
            or len(canonical_bytes({"record_refs": current})) > max_packet_bytes
        ):
            raise ValueError(
                f"one referenced record exceeds configured shard limits: {ref['record_id']}"
            )
    if current:
        shards.append(current)
    return shards


def deterministic_shard_key(
    *,
    stage: str,
    role: str,
    ordinal: int,
    record_refs: Iterable[Mapping[str, Any]],
) -> str:
    boundary = [
        {
            "collection": str(ref.get("collection", "")),
            "record_id": str(ref.get("record_id", "")),
            "sha256": str(ref.get("sha256", "")),
        }
        for ref in record_refs
    ]
    digest = canonical_sha256(
        {"stage": stage, "role": role, "ordinal": ordinal, "boundary": boundary}
    )[:16]
    return f"{_safe(stage).lower()}-{ordinal:05d}-{digest}"


def build_packet(
    run_root: Path,
    runtime_root: Path,
    job: Mapping[str, Any],
    case_revision: Mapping[str, Any],
    dependency_commits: Iterable[Mapping[str, Any]],
    *,
    max_packet_bytes: int,
) -> tuple[Path, str]:
    """Write one immutable role-specific packet and its manifest."""

    role = str(job["role"])
    contract = role_contract(role)
    case_subset = {
        field: case_revision[field]
        for field in contract["allowed_case_fields"]
        if field in case_revision
    }
    case_payload = canonical_bytes(case_subset)
    case_hash = hashlib.sha256(case_payload).hexdigest().upper()
    case_view_path = runtime_root / "case_views" / f"{_safe(role)}-{case_hash}.json"
    if case_view_path.is_file() and case_view_path.read_bytes() != case_payload:
        raise ValueError(f"role-specific case-view hash collision: {role}")
    if not case_view_path.is_file():
        _atomic_write(case_view_path, case_payload)
    dependency_rows = [
        {
            "job_id": str(row["job_id"]),
            "commit_id": str(row["commit_id"]),
            "scientific_hash": str(row["scientific_hash"]),
            "path": str(row["path"]),
        }
        for row in dependency_commits
    ]
    dependency_rows.sort(key=lambda row: row["job_id"])
    contract_path = runtime_root / "contracts" / f"{_safe(role)}.json"
    if not contract_path.is_file():
        raise ValueError(f"missing shared role contract: {role}")
    contract_payload = contract_path.read_bytes()
    expected_contract_hash = contract_hash(role)
    if hashlib.sha256(contract_payload).hexdigest().upper() != expected_contract_hash:
        raise ValueError(f"role contract integrity failure: {role}")
    record_refs: list[dict[str, Any]] = []
    allowed_inputs = set(contract["allowed_input_collections"])
    for raw_ref in job.get("input_refs", []):
        ref = dict(raw_ref)
        collection = str(ref.get("collection", ""))
        if collection not in allowed_inputs:
            raise ValueError(f"role {role} received an unapproved input collection: {collection}")
        record_path = (run_root / str(ref.get("path", ""))).resolve()
        record_path.relative_to(run_root.resolve())
        if not record_path.is_file():
            raise ValueError(f"packet record reference is missing: {ref.get('record_id')}")
        raw_record = record_path.read_bytes()
        if (
            hashlib.sha256(raw_record).hexdigest().upper() != str(ref.get("sha256", "")).upper()
            or len(raw_record) != int(ref.get("bytes", -1))
        ):
            raise ValueError(f"packet record reference integrity failure: {ref.get('record_id')}")
        record_refs.append(ref)
    record_refs.sort(key=lambda ref: (str(ref["collection"]), str(ref["record_id"])))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "packet_model_version": PACKET_MODEL_VERSION,
        "job_id": str(job["job_id"]),
        "stage": str(job["stage"]),
        "role": role,
        "shard_key": str(job["shard_key"]),
        "path_contract": {
            "relative_paths_resolve_against": "the run root containing runtime_v7",
            "workers_write_only": "expected staged result path",
        },
        "case_ref": {
            "path": case_view_path.relative_to(run_root).as_posix(),
            "sha256": case_hash,
            "bytes": len(case_payload),
            "fields": contract["allowed_case_fields"],
        },
        "dependency_commits": dependency_rows,
        "record_refs": record_refs,
        "budget_snapshot": dict(job.get("budget_snapshot", {})),
        "role_contract_ref": {
            "path": contract_path.relative_to(run_root).as_posix(),
            "sha256": expected_contract_hash,
            "bytes": len(contract_payload),
        },
        "expected_result": {
            "path_template": str(job["result_path_template"]),
            "allowed_output_collections": contract["allowed_output_collections"],
            "required_output_collections": contract["required_output_collections"],
            "output_id_fields": contract["output_id_fields"],
        },
    }
    packet_payload = canonical_bytes(payload)
    if len(packet_payload) > max_packet_bytes:
        raise ValueError(
            f"packet for {job['job_id']} is {len(packet_payload)} bytes; "
            f"configured maximum is {max_packet_bytes}"
        )
    packet_dir = runtime_root / "packets" / _safe(str(job["job_id"]))
    packet_path = packet_dir / "input.json"
    _atomic_write(packet_path, packet_payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "packet_model_version": PACKET_MODEL_VERSION,
        "job_id": str(job["job_id"]),
        "stage": str(job["stage"]),
        "role": role,
        "shard_key": str(job["shard_key"]),
        "payload_path": packet_path.relative_to(run_root).as_posix(),
        "payload_sha256": hashlib.sha256(packet_payload).hexdigest().upper(),
        "payload_bytes": len(packet_payload),
        "role_contract_path": contract_path.relative_to(run_root).as_posix(),
        "role_contract_sha256": expected_contract_hash,
    }
    manifest["packet_hash"] = canonical_sha256(manifest)
    manifest_path = packet_dir / "manifest.json"
    _atomic_write(manifest_path, canonical_bytes(manifest))
    return manifest_path, str(manifest["packet_hash"])


def verify_packet(run_root: Path, manifest_path: Path, expected_hash: str) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("packet manifest must be one object")
    stored = str(manifest.get("packet_hash", ""))
    body = {key: value for key, value in manifest.items() if key != "packet_hash"}
    if stored != canonical_sha256(body) or stored != expected_hash:
        raise ValueError("packet manifest hash mismatch")
    payload_path = (run_root / str(manifest["payload_path"])).resolve()
    payload_path.relative_to(run_root.resolve())
    payload = payload_path.read_bytes()
    if hashlib.sha256(payload).hexdigest().upper() != manifest["payload_sha256"]:
        raise ValueError("packet payload hash mismatch")
    packet = json.loads(payload.decode("utf-8"))
    if not isinstance(packet, dict):
        raise ValueError("packet payload must be one object")
    for label, ref in [
        ("case view", packet.get("case_ref")),
        *(("record", value) for value in packet.get("record_refs", [])),
    ]:
        if not isinstance(ref, dict):
            raise ValueError(f"packet {label} reference is malformed")
        referenced = (run_root / str(ref.get("path", ""))).resolve()
        referenced.relative_to(run_root.resolve())
        if not referenced.is_file():
            raise ValueError(f"packet {label} reference is missing")
        raw = referenced.read_bytes()
        if (
            hashlib.sha256(raw).hexdigest().upper() != str(ref.get("sha256", "")).upper()
            or len(raw) != int(ref.get("bytes", -1))
        ):
            raise ValueError(f"packet {label} reference integrity failure")
    contract_path = (run_root / str(manifest["role_contract_path"])).resolve()
    contract_path.relative_to(run_root.resolve())
    if hashlib.sha256(contract_path.read_bytes()).hexdigest().upper() != manifest["role_contract_sha256"]:
        raise ValueError("packet role-contract hash mismatch")
    return manifest


def build_task_packets(
    task_name: str,
    candidate_ids: Iterable[str],
    max_candidates: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    """Build deterministic in-memory minimal packets for the production packet protocol."""

    if max_candidates < 1 or max_bytes < 256:
        raise ValueError("packet limits must be positive")
    identities = sorted({str(value).strip() for value in candidate_ids})
    if any(not value for value in identities):
        raise ValueError("candidate IDs must be nonblank")
    role = (
        "candidate_auditor"
        if "audit" in task_name
        else "preliminary_triage_worker"
    )
    shared_contract_hash = contract_hash(role)

    def envelope(rows: list[str], ordinal: int) -> dict[str, Any]:
        shard_key = f"{_safe(task_name).lower()}-{ordinal:05d}-{canonical_sha256(rows)[:16]}"
        return {
            "schema_version": SCHEMA_VERSION,
            "packet_model_version": PACKET_MODEL_VERSION,
            "task_name": task_name,
            "role": role,
            "shard_key": shard_key,
            "role_contract_sha256": shared_contract_hash,
            "candidate_ids": rows,
        }

    shards: list[list[str]] = []
    current: list[str] = []
    for identity in identities:
        proposed = [*current, identity]
        ordinal = len(shards) + 1
        if current and (
            len(proposed) > max_candidates
            or len(canonical_bytes(envelope(proposed, ordinal))) > max_bytes
        ):
            shards.append(current)
            current = [identity]
        else:
            current = proposed
        if len(canonical_bytes(envelope(current, len(shards) + 1))) > max_bytes:
            raise ValueError(f"one candidate reference exceeds max_bytes: {identity}")
    if current:
        shards.append(current)
    return [envelope(rows, ordinal) for ordinal, rows in enumerate(shards, 1)]


__all__ = [
    "COLLECTION_ID_FIELDS",
    "PACKET_MODEL_VERSION",
    "ROLE_CONTRACTS",
    "SCHEMA_VERSION",
    "build_packet",
    "build_task_packets",
    "canonical_bytes",
    "canonical_sha256",
    "contract_hash",
    "deterministic_shard_key",
    "role_contract",
    "shard_record_refs",
    "shard_records",
    "verify_packet",
    "write_role_contracts",
]
