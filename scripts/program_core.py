#!/usr/bin/env python3
"""Small, content-addressed controller for a linear repurposing programme."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sklearn import __version__ as SKLEARN_VERSION
from sklearn.cluster import BisectingKMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from pathology_sources import SourceError, fetch_pathology_sources


SCHEMA_VERSION = 2
OBJECTIVE = (
    "Identify existing drugs whose established mode of action could plausibly alter a "
    "specific evidence-backed element of the supplied disease pathology. A prior "
    "disease-drug literature association is not required."
)
EXPERIMENTAL_USE_POLICY = (
    "Hypothesis generation only. Outputs are not clinical advice or proof of efficacy."
)
CANONICAL_DOCUMENT_ID = re.compile(
    r"^(?:PMID:\d+|PMCID:PMC\d+|DOI:10\.\d{4,9}/\S+|"
    r"(?:MONARCH-ASSOC|DISMECH-FILE)-[A-F0-9]{24}|"
    r"(?:ORPHA|CGGV|CLINGEN|GENCC|CLINVAR|UNIPROT(?:KB)?|HPA|"
    r"NCBI(?:-BOOKSHELF|-GENE)?|CHEMBL|PUBCHEM|DRUGBANK|DAILYMED|FDA|EMA|"
    r"WHO|ISBN|NCT):\S+|NCT\d{8}|https://\S+)$",
    re.IGNORECASE,
)
STAGES = (
    "pathology_sources",
    "evidence_graph",
    "mechanism_clustering",
    "candidate_seed_generation",
    "candidate_review",
    "audit_and_rank",
)
CLUSTERING_VERSION = 1
CLUSTER_PROFILE_FIELDS = (
    "node_type", "summary", "normal_state", "pathological_state", "causal_role",
    "mechanisms", "cell_types", "anatomical_context", "temporal_context",
    "upstream_causes", "downstream_consequences",
)
CLUSTER_CITATION = re.compile(
    r"\b(?:PMID:\d+|PMCID:PMC\d+|DOI:10\.\d{4,9}/\S+|https://\S+)", re.IGNORECASE
)

STAGE_GUIDANCE: dict[str, dict[str, Any]] = {
    "pathology_node_research": {
        "role": "disease pathology researcher",
        "task": (
            "Research this one source-derived pathology node in exceptional disease-specific "
            "depth. Explain its normal state, pathological change, causal role, mechanisms, "
            "biological context, uncertainty, contradictions, and gaps. Do not research or "
            "propose drugs, treatments, or therapeutic strategies."
        ),
        "collections": ["documents", "profiles", "assertions"],
    },
    "candidate_seed_research": {
        "role": "mechanism-directed candidate seed researcher",
        "task": (
            "For this frozen pathology cluster, define biological changes that could move its "
            "pathological states toward normal, then generate up to 100 diverse existing-drug "
            "seeds whose established mode of action could cause those changes. Do not pad the "
            "list. A drug need not have any prior literature association with the disease; cite "
            "pathology evidence and mode-of-action evidence separately."
        ),
        "collections": ["documents", "candidates", "exclusions"],
    },
    "candidate_review_research": {
        "role": "candidate evidence reviewer",
        "task": (
            "Review every candidate against the graph and cited evidence. Record rescue rationale, "
            "counterevidence, limitations, evidence strength, rescue fit, and uncertainty."
        ),
        "collections": ["documents", "reviews"],
    },
    "audit_and_rank": {
        "role": "independent auditor and ranker",
        "task": (
            "Audit citation and graph support, correct decision-changing errors, and give every "
            "reviewed candidate an eligible or excluded ranking record. Keep evidence, fit, "
            "and uncertainty separate."
        ),
        "collections": ["rankings", "audit_notes"],
    },
}

ROW_FIELDS = {
    "documents": ["document_id", "title", "source"],
    "source_nodes": ["node_id", "label", "node_type", "source_ids"],
    "source_edges": [
        "edge_id", "subject_id", "relation", "object_id", "evidence_summary", "source_ids",
    ],
    "source_receipts": ["source", "version", "query", "record_count"],
    "profiles": [
        "node_id", "node_type", "summary", "normal_state", "pathological_state",
        "causal_role", "mechanisms", "cell_types", "anatomical_context",
        "temporal_context", "upstream_causes", "downstream_consequences",
        "contradictions", "gaps", "uncertainty", "source_ids",
    ],
    "assertions": [
        "assertion_id", "subject_id", "relation", "object_id",
        "evidence_summary", "source_ids",
    ],
    "clusters": ["cluster_id", "member_node_ids"],
    "candidates": [
        "candidate_id", "name", "identity", "desired_change", "mechanism_hypothesis",
        "graph_node_ids", "pathology_source_ids", "mechanism_source_ids",
    ],
    "exclusions": ["name", "reason"],
    "reviews": [
        "candidate_id", "rescue_rationale", "evidence_strength", "rescue_fit",
        "uncertainty", "counterevidence", "limitations", "source_ids",
    ],
    "rankings": [
        "candidate_id", "eligible", "evidence_strength", "rescue_fit",
        "uncertainty", "priority_tier", "rationale", "source_ids",
    ],
    "audit_notes": ["subject_id", "finding"],
}

PATHOLOGY_PROFILE_LIST_FIELDS = (
    "mechanisms", "cell_types", "anatomical_context", "temporal_context",
    "upstream_causes", "downstream_consequences", "contradictions", "gaps", "source_ids",
)

FIELD_RULES = {
    "pathology_node_research": [
        "return exactly one profile whose node_id and node_type match the supplied node",
        "retain at least one independently researched document",
        f"profile fields {', '.join(PATHOLOGY_PROFILE_LIST_FIELDS)} are JSON lists",
        "assertions link only supplied source-derived node IDs; all claims cite retained sources; "
        "no treatment content",
    ],
    "candidate_seed_research": [
        "identity has status, preferred_name, and identifiers; resolved identity needs an "
        "identifier",
        "graph_node_ids contains only supplied cluster members and is non-empty; "
        "pathology_source_ids support the disease mechanism",
        "mechanism_source_ids support the drug mode of action; disease-drug citations are optional",
    ],
    "candidate_review_research": [
        "one review per candidate; evidence_strength and rescue_fit are integers 0..4",
        "uncertainty is low, medium, high, or unknown",
    ],
    "audit_and_rank": [
        "one ranking per candidate; scores are integers 0..4 and uncertainty uses the "
        "review values",
        "eligible records use priority_tier 1..3; ineligible records need exclusion_reason",
    ],
}

_SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "refresh_token",
    "secret",
}
_COMPARATORS = {"placebo", "vehicle", "sham"}
_UNCERTAINTY_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}
_PATHOLOGY_FORBIDDEN_KEYS = {"candidate", "compound", "drug", "treatment", "therapeutic"}


class ProgramError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(_canonical_bytes(value))[:24]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ProgramError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProgramError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProgramError(f"Expected one JSON object: {path}")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProgramError(f"Immutable artifact conflicts with existing file: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> None:
    _write_once(path, _canonical_bytes(value))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = b"".join(_canonical_bytes(row) for row in rows)
    _write_once(path, payload)


def _rows(records: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = records.get(name)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ProgramError(f"records.{name} must be a list of objects")
    return [dict(row) for row in value]


def _contract_rows(
    records: Mapping[str, Any], name: str, id_field: str | None = None
) -> list[dict[str, Any]]:
    rows = _rows(records, name)
    for index, row in enumerate(rows):
        missing = [field for field in ROW_FIELDS[name] if field not in row]
        if missing:
            raise ProgramError(f"{name}[{index}] is missing fields: {', '.join(missing)}")
    if id_field:
        _ids(rows, id_field, name)
    return rows


def _ids(rows: list[dict[str, Any]], field: str, label: str) -> set[str]:
    values: list[str] = []
    for index, row in enumerate(rows):
        value = str(row.get(field, "")).strip()
        if not value:
            raise ProgramError(f"{label}[{index}].{field} is required")
        values.append(value)
    if len(values) != len(set(values)):
        raise ProgramError(f"{label}.{field} values must be unique")
    return set(values)


def _required(row: Mapping[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if row.get(field) in (None, "")]
    if missing:
        raise ProgramError(f"{label} is missing required fields: {', '.join(missing)}")


def _references(row: Mapping[str, Any], field: str, allowed: set[str], label: str) -> set[str]:
    values = row.get(field)
    if not isinstance(values, list) or not values:
        raise ProgramError(f"{label}.{field} must be a non-empty list")
    refs = {str(value) for value in values}
    unknown = refs - allowed
    if unknown:
        raise ProgramError(f"{label}.{field} contains unknown IDs: {sorted(unknown)}")
    return refs


def _secret_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).lower() in _SECRET_KEYS:
                found.append(child)
            found.extend(_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{path}[{index}]"))
    return found


def _forbidden_pathology_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]+", "_", str(key).casefold()).strip("_")
            if set(normalized.split("_")) & _PATHOLOGY_FORBIDDEN_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_forbidden_pathology_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_pathology_paths(item, f"{path}[{index}]"))
    return found


def _result_path(root: Path, stage: str) -> Path:
    return root / "results" / f"{stage}.json"


def _item_token(item_id: str) -> str:
    return _stable_id("ITEM", item_id)


def _item_result_path(root: Path, task: str, item_id: str) -> Path:
    return root / "results" / "items" / task / f"{_item_token(item_id)}.json"


def _packet_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "packets" / f"{task}.json"
    return root / "packets" / "items" / task / f"{_item_token(item_id)}.json"


def _submission_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "submissions" / f"{task}.json"
    return root / "submissions" / "items" / task / f"{_item_token(item_id)}.json"


def _case(root: Path) -> dict[str, Any]:
    case = _read_json(root / "case.json")
    if case.get("schema_version") != SCHEMA_VERSION or not str(case.get("disease", "")).strip():
        raise ProgramError("case.json is not a valid lean repurposing case")
    if case.get("objective") != OBJECTIVE:
        raise ProgramError("case.json does not contain the built-in repurposing objective")
    basis = {
        "disease": case["disease"],
        "gene": case.get("gene"),
        "mondo": case.get("mondo"),
        "objective": OBJECTIVE,
    }
    if case.get("case_id") != _stable_id("CASE", basis):
        raise ProgramError("case.json content no longer matches its case_id")
    return case


def initialize(
    root: str | Path,
    disease: str,
    gene: str | None = None,
    mondo: str | None = None,
) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    disease = disease.strip()
    gene = gene.strip() if gene else None
    mondo = mondo.strip().upper() if mondo else None
    if not disease:
        raise ProgramError("--disease is required")
    case_path = run_root / "case.json"
    if run_root.exists() and not case_path.exists() and any(run_root.iterdir()):
        raise ProgramError(
            "Run folder is not empty and does not contain this programme's case.json"
        )
    if case_path.exists():
        existing = _case(run_root)
        if (
            existing["disease"] != disease
            or existing.get("gene") != gene
            or existing.get("mondo") != mondo
        ):
            raise ProgramError("Existing run case conflicts with the supplied disease, gene, or MONDO ID")
        return _program_status(run_root, existing, _load_results(run_root))
    run_root.mkdir(parents=True, exist_ok=True)
    case_basis = {"disease": disease, "gene": gene, "mondo": mondo, "objective": OBJECTIVE}
    case = {
        "schema_version": SCHEMA_VERSION,
        "case_id": _stable_id("CASE", case_basis),
        "disease": disease,
        "gene": gene,
        "mondo": mondo,
        "objective": OBJECTIVE,
        "created_at": datetime.now(timezone.utc).isoformat(),

        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_json(case_path, case)
    for name in ("packets", "results", "outputs"):
        (run_root / name).mkdir(exist_ok=True)
    return _program_status(run_root, case, {})


def _load_results(root: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    missing_seen = False
    for stage in STAGES:
        path = _result_path(root, stage)
        if not path.exists():
            missing_seen = True
            continue
        if missing_seen:
            raise ProgramError(f"Result exists out of stage order: {path}")
        results[stage] = _read_json(path)
    return results


def _item_ids(stage: str, results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    field = "node_id"
    if stage == "evidence_graph":
        rows = _rows(results["pathology_sources"]["records"], "source_nodes")
    elif stage == "candidate_seed_generation":
        rows = _clusters(results)
        field = "cluster_id"
    elif stage == "candidate_review":
        rows = _rows(results["candidate_seed_generation"]["records"], "candidates")
        field = "candidate_id"
    else:
        return []
    return sorted(str(row[field]) for row in rows)


def _item_results(
    root: Path, task: str, item_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    return {
        item_id: _read_json(path)
        for item_id in item_ids
        if (path := _item_result_path(root, task, item_id)).exists()
    }


def _first_missing(root: Path, task: str, item_ids: list[str]) -> tuple[str | None, int]:
    accepted = _item_results(root, task, item_ids)
    return next((item_id for item_id in item_ids if item_id not in accepted), None), len(accepted)


def _stop_reason(results: Mapping[str, Mapping[str, Any]]) -> str | None:
    checks = (
        ("pathology_sources", "source_nodes", "Monarch and DisMech returned no pathology nodes"),
        ("evidence_graph", "profiles", "no source-backed pathology profiles were produced"),
        ("mechanism_clustering", "clusters", "no pathology mechanism clusters were produced"),
        ("candidate_seed_generation", "candidates", "no mechanism-linked drug seeds were produced"),
        ("candidate_review", "reviews", "no candidates received an evidence review"),
    )
    for stage, collection, reason in checks:
        result = results.get(stage)
        if result is not None and not result.get("records", {}).get(collection):
            return reason
    audit = results.get("audit_and_rank")
    if audit is not None and not any(
        row.get("eligible") is True for row in audit.get("records", {}).get("rankings", [])
    ):
        return "the audit left no eligible candidates"
    return None


def _verify_outputs(root: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "complete" or not manifest.get("candidate_count"):
        raise ProgramError("Output manifest is not complete")
    if manifest.get("case_sha256") != _sha256((root / "case.json").read_bytes()):
        raise ProgramError("case.json changed after outputs were built")
    stage_results = manifest.get("stage_results")
    if not isinstance(stage_results, dict) or set(stage_results) != set(STAGES):
        raise ProgramError("Output manifest does not cover every stage result")
    for stage, expected in stage_results.items():
        if _sha256(_result_path(root, stage).read_bytes()) != expected:
            raise ProgramError(f"Accepted result changed after outputs were built: {stage}")
    for artifact in manifest.get("artifacts", []):
        path = root / "outputs" / str(artifact.get("filename", ""))
        if not path.is_file() or _sha256(path.read_bytes()) != artifact.get("sha256"):
            raise ProgramError(f"Output artifact is missing or changed: {path}")


def _program_status(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    stop = _stop_reason(results)
    manifest_path = run_root / "outputs" / "manifest.json"
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        _verify_outputs(run_root, manifest)
        state = "complete"
        next_stage = None
    elif stop:
        state = "stopped"
        next_stage = None
    elif len(results) == len(STAGES):
        state = "ready_to_build"
        next_stage = None
        next_task = None
        next_item_id = None
        accepted_items = 0
    else:
        next_stage = STAGES[len(results)]
        next_item_id = None
        accepted_items = 0
        if next_stage in {"pathology_sources", "mechanism_clustering"}:
            state, next_task = "needs_controller", next_stage
        elif next_stage in {"evidence_graph", "candidate_seed_generation", "candidate_review"}:
            next_task = {
                "evidence_graph": "pathology_node_research",
                "candidate_seed_generation": "candidate_seed_research",
                "candidate_review": "candidate_review_research",
            }[next_stage]
            next_item_id, accepted_items = _first_missing(
                run_root, next_task, _item_ids(next_stage, results)
            )
            state = "needs_agent" if next_item_id is not None else "needs_controller"
        else:
            state, next_task = "needs_agent", next_stage
    if manifest_path.exists() or stop:
        next_task = None
        next_item_id = None
        accepted_items = 0
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "state": state,
        "next_stage": next_stage,
        "next_task": next_task,
        "next_item_id": next_item_id,
        "accepted_items": accepted_items,
        "accepted_stages": list(results),
        "stop_reason": stop,
    }


def status(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    return _program_status(run_root, _case(run_root), _load_results(run_root))


def _source_index(
    documents: list[dict[str, Any]], source_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    fields = ("document_id", "title", "source", "citation", "url", "raw_path", "abstract")
    return [
        {key: row[key] for key in fields if key in row}
        for row in documents
        if source_ids is None or str(row["document_id"]) in source_ids
    ]


def _merge_unique(
    rows: Iterable[dict[str, Any]], id_field: str, label: str
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(id_field, "")).strip()
        if not key:
            raise ProgramError(f"{label}.{id_field} is required")
        if key in merged and merged[key] != row:
            raise ProgramError(f"Conflicting {label} records share {id_field}={key}")
        merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _merge_text(*values: Any) -> str:
    parts = {
        part.strip()
        for value in values
        for part in str(value).split(" | ")
        if part.strip()
    }
    return " | ".join(sorted(parts))


def _merge_documents(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        document_id = str(row.get("document_id", "")).strip()
        if not document_id:
            raise ProgramError("documents.document_id is required")
        current = merged.setdefault(document_id, {"document_id": document_id})
        for field, value in row.items():
            if field == "document_id" or value in (None, "", []):
                continue
            if isinstance(value, list):
                prior = current.get(field, [])
                if not isinstance(prior, list):
                    raise ProgramError(f"documents.{field} changes type")
                values = {_canonical_bytes(item): item for item in [*prior, *value]}
                value = [values[key] for key in sorted(values)]
            current[field] = value
    return [merged[key] for key in sorted(merged)]


def _merge_assertions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        assertion_id = str(row.get("assertion_id", "")).strip()
        if not assertion_id:
            raise ProgramError("assertions.assertion_id is required")
        current = merged.get(assertion_id)
        if current is None:
            merged[assertion_id] = {**row, "assertion_id": assertion_id}
            continue
        if any(
            current[field] != row[field]
            for field in ("subject_id", "relation", "object_id")
        ):
            raise ProgramError(
                f"Conflicting assertion identities share assertion_id={assertion_id}"
            )
        current["source_ids"] = sorted({
            *map(str, current["source_ids"]),
            *map(str, row["source_ids"]),
        })
        current["evidence_summary"] = _merge_text(
            current["evidence_summary"], row["evidence_summary"]
        )
    return [merged[key] for key in sorted(merged)]


def _all_documents(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _merge_documents(
        (
            row
            for result in results.values()
            for row in result.get("records", {}).get("documents", [])
            if isinstance(row, dict)
        )
    )


def _find(rows: Iterable[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get(field)) == value]
    if len(matches) != 1:
        raise ProgramError(f"Expected exactly one {field}={value} record")
    return matches[0]


def _cited_ids(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    fields = ("source_ids", "pathology_source_ids", "mechanism_source_ids")
    return {
        str(value)
        for row in rows
        for field in fields
        for value in (row.get(field) if isinstance(row.get(field), list) else [])
    }


def _packet_context(
    task: str,
    item_id: str | None,
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    documents = _all_documents(results)
    if task == "pathology_node_research":
        source = results["pathology_sources"]["records"]
        node = _find(_rows(source, "source_nodes"), "node_id", str(item_id))
        edges = [
            row
            for row in _rows(source, "source_edges")
            if str(item_id) in {str(row["subject_id"]), str(row["object_id"])}
        ]
        return {
            "node": node,
            "adjacent_edges": edges,
            "source_index": _source_index(documents, _cited_ids([node, *edges])),
            "source_receipts": _rows(source, "source_receipts"),
            "upstream_gaps": results["pathology_sources"].get("gaps", []),
        }
    graph = results["evidence_graph"]["records"]
    if task == "candidate_seed_research":
        cluster = _find(_clusters(results), "cluster_id", str(item_id))
        member_ids = set(map(str, cluster["member_node_ids"]))
        nodes = [
            row for row in _rows(graph, "source_nodes") if str(row["node_id"]) in member_ids
        ]
        profiles = [
            row for row in _rows(graph, "profiles") if str(row["node_id"]) in member_ids
        ]
        edges = [
            row
            for row in [*_rows(graph, "source_edges"), *_rows(graph, "assertions")]
            if member_ids & {str(row["subject_id"]), str(row["object_id"])}
        ]
        return {
            "graph_sha256": _sha256(_canonical_bytes(results["evidence_graph"])),
            "cluster": cluster,
            "nodes": nodes,
            "profiles": profiles,
            "related_edges": edges,
            "source_index": _source_index(
                documents, _cited_ids([*nodes, *profiles, *edges])
            ),
        }
    seeds = results["candidate_seed_generation"]["records"]
    if task == "candidate_review_research":
        candidate = _find(_rows(seeds, "candidates"), "candidate_id", str(item_id))
        node_ids = set(map(str, candidate["graph_node_ids"]))
        return {
            "candidate": candidate,
            "pathology_profiles": [
                row for row in _rows(graph, "profiles") if str(row["node_id"]) in node_ids
            ],
            "source_index": _source_index(documents, _cited_ids([candidate])),
        }
    return {
        "candidates": _rows(seeds, "candidates"),
        "reviews": _rows(results["candidate_review"]["records"], "reviews"),
        "source_index": _source_index(documents),
    }


def _build_packet(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    task: str,
    item_id: str | None = None,
) -> dict[str, Any]:
    upstream = [
        {
            "stage": name,
            "path": str(_result_path(run_root, name)),
            "sha256": _sha256(_result_path(run_root, name).read_bytes()),
        }
        for name in results
    ]
    guidance = STAGE_GUIDANCE[task]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "stage": task,
        "item_id": item_id,
        "role": guidance["role"],
        "objective": OBJECTIVE,
        "task": guidance["task"],
        "case": case,
        "upstream": upstream,
        "context": _packet_context(task, item_id, results),
        "result_contract": {
            "stage": task,
            "item_id": item_id,
            "packet_id": "copy from this packet",
            "status": "complete",
            "records": {
                name: {"type": "list of objects", "required_fields": ROW_FIELDS[name]}
                for name in guidance["collections"]
            },
            "field_rules": FIELD_RULES[task],
            "gaps": "list of explicit limitations or unresolved questions",
            "notes": "optional list of concise notes",
        },
        "rules": [
            "Use only supplied or newly retrieved named sources; never invent citations.",
            "Use PMID:<digits>, PMCID:PMC<digits>, DOI:<doi>, recognized accession:<id>, or "
            "HTTPS URL document IDs; never invent DOC aliases.",
            "Preserve contradictions, negative results, unresolved identity, and source gaps.",
            "Return JSON only and do not include credentials or API keys.",
        ],
    }
    packet = {**unsigned, "packet_id": _stable_id("PACKET", unsigned)}
    _write_json(_packet_path(run_root, task, item_id), packet)
    return packet


def next_action(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case = _case(run_root)
    for _ in range(len(STAGES) + 1):
        results = _load_results(run_root)
        current = _program_status(run_root, case, results)
        if current["state"] != "needs_controller":
            break
        _advance_controller(run_root, case, results, str(current["next_stage"]))
    else:
        raise ProgramError("Controller could not reach an agent or terminal state")
    if current["state"] != "needs_agent":
        return current
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, results, task, item_id)
    packet_path = _packet_path(run_root, task, item_id)
    result_path = _submission_path(run_root, task, item_id)
    return {
        **current,
        "packet_id": packet["packet_id"],
        "packet_path": str(packet_path),
        "suggested_result_path": str(result_path),
        "worker_prompt": (
            f"Read only the content packet at {packet_path}. Complete the {task} task and write "
            f"one JSON object matching result_contract to {result_path}. Use this exact header: "
            f"stage={json.dumps(task)}, item_id={json.dumps(item_id)}, "
            f"packet_id={json.dumps(packet['packet_id'])}, status=\"complete\". "
            "Return the result path to the controller."
        ),
    }


def _item_collection(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
    collection: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _rows(accepted[item_id]["records"], collection)
    ]


def _item_gaps(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[Any]:
    accepted = _item_results(root, task, _item_ids(stage, results))
    return [gap for result in accepted.values() for gap in result.get("gaps", [])]


def _build_graph_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    source = results["pathology_sources"]["records"]
    records = {
        "documents": _merge_documents(
            [
                *_rows(source, "documents"),
                *_item_collection(
                    root, results, "evidence_graph", "pathology_node_research", "documents"
                ),
            ]
        ),
        "source_nodes": _rows(source, "source_nodes"),
        "source_edges": _rows(source, "source_edges"),
        "source_receipts": _rows(source, "source_receipts"),
        "profiles": _merge_unique(
            _item_collection(
                root, results, "evidence_graph", "pathology_node_research", "profiles"
            ),
            "node_id",
            "profiles",
        ),
        "assertions": _merge_assertions(
            _item_collection(
                root, results, "evidence_graph", "pathology_node_research", "assertions"
            )
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "evidence_graph",
        "status": "complete",
        "snapshot_id": _stable_id("GRAPH", records),
        "records": records,
        "gaps": _item_gaps(root, results, "evidence_graph", "pathology_node_research"),
        "notes": ["Frozen pathology-only graph; deterministic mechanism clustering may now begin."],
    }


def _build_cluster_result(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    graph_result = results["evidence_graph"]
    graph = graph_result["records"]
    nodes = {str(row["node_id"]): row for row in _rows(graph, "source_nodes")}
    profiles = sorted(
        (
            row
            for row in _rows(graph, "profiles")
            if row.get("node_type") != "disease_anchor"
        ),
        key=lambda row: str(row["node_id"]),
    )
    if not profiles:
        raise ProgramError("Cannot cluster a graph without non-anchor pathology profiles")
    texts = []
    for profile in profiles:
        node = nodes[str(profile["node_id"])]
        values: list[Any] = [node.get("label", "")]
        values.extend(profile.get(field, "") for field in CLUSTER_PROFILE_FIELDS)
        texts.append(_cluster_text(values))
    cluster_count = math.isqrt(len(profiles) - 1) + 1
    vectors = TfidfVectorizer(
        stop_words="english", ngram_range=(1, 2), strip_accents="unicode"
    ).fit_transform(texts)
    labels = (
        [0]
        if cluster_count == 1
        else BisectingKMeans(
            n_clusters=cluster_count, random_state=0, n_init=10
        ).fit_predict(vectors)
    )
    grouped: dict[int, list[str]] = {}
    for label, profile in zip(labels, profiles):
        grouped.setdefault(int(label), []).append(str(profile["node_id"]))
    clusters = []
    for members in grouped.values():
        members.sort()
        clusters.append(
            {
                "cluster_id": _stable_id("CLUSTER", members),
                "member_node_ids": members,
                "node_types": sorted({str(nodes[node_id]["node_type"]) for node_id in members}),
            }
        )
    clusters.sort(key=lambda row: str(row["cluster_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "mechanism_clustering",
        "status": "complete",
        "graph_snapshot_id": graph_result["snapshot_id"],
        "graph_sha256": _sha256(_canonical_bytes(graph_result)),
        "method": {
            "name": "tfidf_bisecting_kmeans",
            "version": CLUSTERING_VERSION,
            "cluster_count_rule": "ceil_sqrt_profile_count",
            "requested_clusters": cluster_count,
            "cluster_count": len(clusters),
            "random_state": 0,
            "n_init": 10,
            "scikit_learn": SKLEARN_VERSION,
        },
        "records": {"clusters": clusters},
        "gaps": [],
        "notes": [f"Clustered {len(profiles)} pathology profiles into {len(clusters)} groups."],
    }


def _cluster_text(value: Any) -> str:
    if isinstance(value, Mapping):
        value = [item for key, item in value.items() if key != "source_ids"]
    if isinstance(value, (list, tuple)):
        return " ".join(filter(None, map(_cluster_text, value)))
    return " ".join(CLUSTER_CITATION.sub("", str(value)).split())


def _clusters(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    graph_result = results["evidence_graph"]
    result = results["mechanism_clustering"]
    if (
        result.get("graph_snapshot_id") != graph_result.get("snapshot_id")
        or result.get("graph_sha256") != _sha256(_canonical_bytes(graph_result))
    ):
        raise ProgramError("Mechanism clusters do not match the frozen graph")
    records = result.get("records")
    if not isinstance(records, dict):
        raise ProgramError("Mechanism clustering result requires records")
    clusters = _contract_rows(records, "clusters", "cluster_id")
    expected = {
        str(row["node_id"])
        for row in _rows(graph_result["records"], "profiles")
        if row.get("node_type") != "disease_anchor"
    }
    if any(not isinstance(row["member_node_ids"], list) or not row["member_node_ids"] for row in clusters):
        raise ProgramError("Every mechanism cluster requires member_node_ids")
    members = [str(node_id) for row in clusters for node_id in row["member_node_ids"]]
    if len(members) != len(set(members)) or set(members) != expected:
        raise ProgramError("Mechanism clusters must partition every non-anchor profile exactly once")
    return clusters


def _merge_candidates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_id = str(row["candidate_id"])
        current = merged.get(candidate_id)
        if current is None:
            merged[candidate_id] = dict(row)
            continue
        if current["identity"] != row["identity"]:
            raise ProgramError(f"Conflicting identities share candidate_id={candidate_id}")
        for field in (
            "graph_node_ids", "pathology_source_ids", "mechanism_source_ids", "origin_cluster_ids"
        ):
            current[field] = sorted({*map(str, current.get(field, [])), *map(str, row.get(field, []))})
        for field in ("desired_change", "mechanism_hypothesis"):
            current[field] = _merge_text(current[field], row[field])
    return [merged[key] for key in sorted(merged)]


def _build_seed_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    item_ids = _item_ids("candidate_seed_generation", results)
    accepted = _item_results(root, "candidate_seed_research", item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError("Cannot aggregate seeds before every mechanism cluster is accepted")
    raw_candidates = [
        {**row, "origin_cluster_ids": [item_id]}
        for item_id in item_ids
        for row in _rows(accepted[item_id]["records"], "candidates")
    ]
    records = {
        "documents": _merge_documents(
            [
                *_rows(results["evidence_graph"]["records"], "documents"),
                *(row for item_id in item_ids for row in _rows(accepted[item_id]["records"], "documents")),
            ]
        ),
        "candidates": _merge_candidates(raw_candidates),
        "exclusions": [
            {**row, "origin_cluster_id": item_id}
            for item_id in item_ids
            for row in _rows(accepted[item_id]["records"], "exclusions")
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "candidate_seed_generation",
        "status": "complete",
        "graph_snapshot_id": results["evidence_graph"]["snapshot_id"],
        "records": records,
        "gaps": _item_gaps(
            root, results, "candidate_seed_generation", "candidate_seed_research"
        ),
        "notes": [f"Aggregated {len(raw_candidates)} raw seeds into {len(records['candidates'])} candidates."],
    }


def _build_review_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "candidate_review",
        "status": "complete",
        "records": {
            "documents": _merge_documents(
                [
                    *_all_documents(results),
                    *_item_collection(
                        root, results, "candidate_review", "candidate_review_research", "documents"
                    ),
                ]
            ),
            "reviews": _merge_unique(
                _item_collection(
                    root, results, "candidate_review", "candidate_review_research", "reviews"
                ),
                "candidate_id",
                "reviews",
            ),
        },
        "gaps": _item_gaps(root, results, "candidate_review", "candidate_review_research"),
        "notes": [],
    }


def _advance_controller(
    root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> None:
    if stage == "pathology_sources":
        try:
            result = fetch_pathology_sources(root, str(case["disease"]), case.get("mondo"))
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        result["schema_version"] = SCHEMA_VERSION
        _validate_source_result(result)
    elif stage == "evidence_graph":
        result = _build_graph_result(root, results)
    elif stage == "mechanism_clustering":
        result = _build_cluster_result(results)
    elif stage == "candidate_seed_generation":
        result = _build_seed_result(root, results)
    elif stage == "candidate_review":
        result = _build_review_result(root, results)
    else:
        raise ProgramError(f"No controller action exists for stage: {stage}")
    _write_json(_result_path(root, stage), result)


def _validate_documents(
    records: Mapping[str, Any], *, canonical_ids: bool = False
) -> list[dict[str, Any]]:
    documents = _contract_rows(records, "documents", "document_id")
    for index, row in enumerate(documents):
        _required(row, ("document_id", "title", "source"), f"documents[{index}]")
        if canonical_ids and not CANONICAL_DOCUMENT_ID.fullmatch(str(row["document_id"])):
            raise ProgramError(
                f"documents[{index}].document_id must be a canonical PMID, PMCID, DOI, "
                "authoritative accession, or HTTPS URL"
            )
    return documents


def _validate_source_result(result: Mapping[str, Any]) -> None:
    records = result.get("records")
    if not isinstance(records, dict):
        raise ProgramError("Pathology source adapter did not return records")
    documents = _validate_documents(records)
    nodes = _contract_rows(records, "source_nodes", "node_id")
    edges = _contract_rows(records, "source_edges", "edge_id")
    _contract_rows(records, "source_receipts")
    document_ids = {str(row["document_id"]) for row in documents}
    node_ids = {str(row["node_id"]) for row in nodes}
    for index, row in enumerate(nodes):
        _references(row, "source_ids", document_ids, f"source_nodes[{index}]")
    for index, row in enumerate(edges):
        label = f"source_edges[{index}]"
        if str(row["subject_id"]) not in node_ids or str(row["object_id"]) not in node_ids:
            raise ProgramError(f"{label} refers to an unknown source node")
        _references(row, "source_ids", document_ids, label)
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields reached the pathology source result: {forbidden}")


def _validate_pathology_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    source_document_ids = _ids(
        _rows(results["pathology_sources"]["records"], "documents"),
        "document_id",
        "documents",
    )
    if not {str(row["document_id"]) for row in documents} - source_document_ids:
        raise ProgramError("pathology node research must retain newly researched evidence")
    profiles = _contract_rows(records, "profiles", "node_id")
    assertions = _contract_rows(records, "assertions", "assertion_id")
    if len(profiles) != 1 or str(profiles[0]["node_id"]) != item_id:
        raise ProgramError("pathology node research must return exactly one profile for item_id")
    source_nodes = _rows(results["pathology_sources"]["records"], "source_nodes")
    node = _find(source_nodes, "node_id", item_id)
    if profiles[0]["node_type"] != node["node_type"]:
        raise ProgramError("profile.node_type must match the source-derived node")
    profile = profiles[0]
    _required(
        profile,
        ("summary", "normal_state", "pathological_state", "causal_role", "uncertainty"),
        "profiles[0]",
    )
    for field in PATHOLOGY_PROFILE_LIST_FIELDS:
        if not isinstance(profile[field], list):
            raise ProgramError(f"profiles[0].{field} must be a list")
    node_ids = {str(row["node_id"]) for row in source_nodes}
    source_ids = {
        *source_document_ids,
        *(str(row["document_id"]) for row in documents),
    }
    _references(profiles[0], "source_ids", source_ids, "profiles[0]")
    for index, row in enumerate(assertions):
        label = f"assertions[{index}]"
        _required(
            row,
            ("assertion_id", "subject_id", "relation", "object_id", "evidence_summary"),
            label,
        )
        if str(row["subject_id"]) not in node_ids or str(row["object_id"]) not in node_ids:
            raise ProgramError(f"{label} refers to an unknown source-derived node")
        _references(row, "source_ids", source_ids, label)
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields are forbidden in pathology research: {forbidden}")


def _validate_seed_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    candidates = _contract_rows(records, "candidates", "candidate_id")
    _contract_rows(records, "exclusions")
    graph = results["evidence_graph"]["records"]
    cluster = _find(_clusters(results), "cluster_id", item_id)
    cluster_node_ids = set(map(str, cluster["member_node_ids"]))
    node_ids = _ids(_rows(graph, "source_nodes"), "node_id", "source_nodes")
    pathology_source_ids = _ids(_rows(graph, "documents"), "document_id", "documents")
    new_mechanism_source_ids = {str(row["document_id"]) for row in documents}
    mechanism_source_ids = {
        *pathology_source_ids,
        *new_mechanism_source_ids,
    }
    for index, row in enumerate(candidates):
        label = f"candidates[{index}]"
        _required(
            row,
            (
                "candidate_id", "name", "identity", "desired_change", "mechanism_hypothesis",
            ),
            label,
        )
        if str(row["name"]).strip().casefold() in _COMPARATORS:
            raise ProgramError(f"{label} is a comparator, not a drug candidate")
        identity = row.get("identity")
        if not isinstance(identity, dict) or identity.get("status") not in {
            "resolved",
            "unresolved",
            "conflicting",
        }:
            raise ProgramError(
                f"{label}.identity.status must be resolved, unresolved, or conflicting"
            )
        _required(identity, ("preferred_name", "identifiers"), f"{label}.identity")
        if not isinstance(identity["identifiers"], dict):
            raise ProgramError(f"{label}.identity.identifiers must be an object")
        if identity["status"] == "resolved" and not identity["identifiers"]:
            raise ProgramError(
                f"{label}.identity requires an authoritative identifier when resolved"
            )
        graph_refs = _references(row, "graph_node_ids", node_ids, label)
        if not graph_refs <= cluster_node_ids:
            raise ProgramError(f"{label}.graph_node_ids contains nodes outside the item cluster")
        _references(row, "pathology_source_ids", pathology_source_ids, label)
        mechanism_refs = _references(row, "mechanism_source_ids", mechanism_source_ids, label)
        if not mechanism_refs & new_mechanism_source_ids:
            raise ProgramError(f"{label}.mechanism_source_ids needs a retained drug-MOA source")


def _score(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 4:
        raise ProgramError(f"{label} must be an integer from 0 to 4")
    return value


def _accepted_ids(
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    collection: str,
    field: str,
) -> set[str]:
    return _ids(_rows(results[stage]["records"], collection), field, collection)


def _validate_review_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    reviews = _contract_rows(records, "reviews", "candidate_id")
    if len(reviews) != 1 or str(reviews[0]["candidate_id"]) != item_id:
        raise ProgramError("candidate review must return exactly one review for item_id")
    candidate_ids = _accepted_ids(results, "candidate_seed_generation", "candidates", "candidate_id")
    if item_id not in candidate_ids:
        raise ProgramError("candidate review item_id is not an accepted seed")
    source_ids = {
        *(str(row["document_id"]) for row in _all_documents(results)),
        *(str(row["document_id"]) for row in documents),
    }
    for index, row in enumerate(reviews):
        label = f"reviews[{index}]"
        _required(
            row,
            (
                "candidate_id",
                "rescue_rationale",
                "uncertainty",
                "counterevidence",
                "limitations",
            ),
            label,
        )
        _score(row.get("evidence_strength"), f"{label}.evidence_strength")
        _score(row.get("rescue_fit"), f"{label}.rescue_fit")
        if row.get("uncertainty") not in _UNCERTAINTY_ORDER:
            raise ProgramError(f"{label}.uncertainty must be low, medium, high, or unknown")

        _references(row, "source_ids", source_ids, label)


def _validate_rankings(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    rankings = _contract_rows(records, "rankings", "candidate_id")
    _contract_rows(records, "audit_notes")
    candidate_ids = _accepted_ids(
        results, "candidate_review", "reviews", "candidate_id"
    )
    ranked_ids = {str(row["candidate_id"]) for row in rankings}
    if ranked_ids != candidate_ids:
        raise ProgramError("rankings must contain exactly one record for every reviewed candidate")
    source_ids = {str(row["document_id"]) for row in _all_documents(results)}
    for index, row in enumerate(rankings):
        label = f"rankings[{index}]"
        if type(row.get("eligible")) is not bool:
            raise ProgramError(f"{label}.eligible must be true or false")
        _required(row, ("candidate_id", "rationale"), label)
        _score(row.get("evidence_strength"), f"{label}.evidence_strength")
        _score(row.get("rescue_fit"), f"{label}.rescue_fit")
        if row.get("uncertainty") not in _UNCERTAINTY_ORDER:
            raise ProgramError(f"{label}.uncertainty must be low, medium, high, or unknown")
        if row["eligible"] is True and row.get("priority_tier") not in {1, 2, 3}:
            raise ProgramError(f"{label}.priority_tier must be 1, 2, or 3 when eligible")
        if row["eligible"] is False and not str(row.get("exclusion_reason", "")).strip():
            raise ProgramError(f"{label}.exclusion_reason is required when ineligible")
        _references(row, "source_ids", source_ids, label)


def _validate_result(
    task: str,
    item_id: str | None,
    result: Mapping[str, Any],
    packet: Mapping[str, Any],
    prior: Mapping[str, Mapping[str, Any]],
) -> None:
    if (
        result.get("stage") != task
        or result.get("item_id") != item_id
        or result.get("packet_id") != packet.get("packet_id")
    ):
        raise ProgramError("Result stage or packet_id does not match the ready packet")
    if result.get("status") != "complete":
        raise ProgramError(
            "Only status=complete results become canonical; revise failed work and resubmit"
        )
    if not isinstance(result.get("records"), dict) or not isinstance(result.get("gaps"), list):
        raise ProgramError("Result requires records object and gaps list")
    if "notes" in result and not isinstance(result["notes"], list):
        raise ProgramError("Result notes must be a list when supplied")
    secrets = _secret_paths(result)
    if secrets:
        raise ProgramError(f"Credentials must never be persisted in results: {secrets}")
    validators = {
        "pathology_node_research": lambda: _validate_pathology_item(
            result["records"], str(item_id), prior
        ),
        "candidate_seed_research": lambda: _validate_seed_item(
            result["records"], str(item_id), prior
        ),
        "candidate_review_research": lambda: _validate_review_item(
            result["records"], str(item_id), prior
        ),
        "audit_and_rank": lambda: _validate_rankings(result["records"], prior),
    }
    validators[task]()


def submit(root: str | Path, result_path: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, prior = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, prior)
    if current["state"] != "needs_agent":
        raise ProgramError(f"No agent result is ready for submission; state is {current['state']}")
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, prior, task, item_id)
    result = _read_json(Path(result_path).expanduser().resolve())
    _validate_result(task, item_id, result, packet, prior)
    destination = (
        _item_result_path(run_root, task, str(item_id))
        if item_id is not None
        else _result_path(run_root, task)
    )
    _write_json(destination, result)
    return _program_status(run_root, case, _load_results(run_root))


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"filename": path.name, "bytes": len(payload), "sha256": _sha256(payload)}


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    reviews: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    review = reviews[row["candidate_id"]]
    return {
        "rank": rank,
        "candidate_id": row["candidate_id"],
        "name": candidate["name"],
        "identity_status": candidate["identity"]["status"],
        "evidence_strength": row["evidence_strength"],
        "rescue_fit": row["rescue_fit"],
        "uncertainty": row["uncertainty"],
        "priority_tier": row["priority_tier"],
        "mechanism_hypothesis": candidate["mechanism_hypothesis"],
        "rescue_rationale": review["rescue_rationale"],
        "audit_rationale": row["rationale"],
        "source_ids": ";".join(sorted(map(str, row["source_ids"]))),
    }


def _ranked_rows(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    seeds = results["candidate_seed_generation"]["records"]
    candidates = {row["candidate_id"]: row for row in _rows(seeds, "candidates")}
    review_by_id = {
        row["candidate_id"]: row
        for row in _rows(results["candidate_review"]["records"], "reviews")
    }
    eligible = [
        row
        for row in _rows(results["audit_and_rank"]["records"], "rankings")
        if row["eligible"] is True
    ]
    eligible.sort(
        key=lambda row: (
            row["priority_tier"],
            -row["evidence_strength"],
            -row["rescue_fit"],
            _UNCERTAINTY_ORDER[row["uncertainty"]],
            str(candidates[row["candidate_id"]]["name"]).casefold(),
            row["candidate_id"],
        )
    )

    rows = [
        _project_ranked_row(rank, row, candidates, review_by_id)
        for rank, row in enumerate(eligible, 1)
    ]
    return rows, candidates


def _cards_bytes(rows: list[dict[str, Any]]) -> bytes:
    cards = ["# Repurposing candidate cards", "", EXPERIMENTAL_USE_POLICY, ""]
    for row in rows:
        cards += [
            f"## {row['rank']}. {row['name']}",
            "",
            f"Priority tier: {row['priority_tier']}; evidence: {row['evidence_strength']}/4; "
            f"rescue fit: {row['rescue_fit']}/4; uncertainty: {row['uncertainty']}.",
            "",
            f"Mechanism hypothesis: {row['mechanism_hypothesis']}",
            "",
            f"Review: {row['rescue_rationale']}",
            "",
            f"Audit: {row['audit_rationale']}",
            "",
            f"Sources: {row['source_ids']}",
            "",
        ]
    return ("\n".join(cards) + "\n").encode("utf-8")


def _provenance_rows(
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    assertions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        candidate = candidates[row["candidate_id"]]
        node_ids = set(map(str, candidate["graph_node_ids"]))
        output.append(
            {
                "candidate_id": row["candidate_id"],
                "graph_node_ids": sorted(node_ids),
                "assertion_ids": sorted(
                    assertion["assertion_id"]
                    for assertion in assertions
                    if str(assertion["subject_id"]) in node_ids
                    or str(assertion["object_id"]) in node_ids
                ),
                "pathology_source_ids": sorted(map(str, candidate["pathology_source_ids"])),
                "mechanism_source_ids": sorted(map(str, candidate["mechanism_source_ids"])),
                "origin_cluster_ids": sorted(
                    map(str, candidate.get("origin_cluster_ids", []))
                ),
            }
        )
    return output


def _write_output_files(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[Path]:
    outputs = run_root / "outputs"
    graph = results["evidence_graph"]["records"]
    _write_once(outputs / "candidates.csv", _csv_bytes(rows, list(rows[0])))
    _write_once(outputs / "candidate_cards.md", _cards_bytes(rows))
    documents = sorted(_all_documents(results), key=lambda row: row["document_id"])
    _write_jsonl(outputs / "citations.jsonl", documents)
    assertions = _rows(graph, "assertions")
    _write_json(
        outputs / "graph.json",
        {
            "case_id": case["case_id"],
            "snapshot_id": results["evidence_graph"]["snapshot_id"],
            "nodes": _rows(graph, "source_nodes"),
            "source_edges": _rows(graph, "source_edges"),
            "profiles": _rows(graph, "profiles"),
            "assertions": assertions,
        },
    )
    _write_jsonl(
        outputs / "candidate_provenance.jsonl",
        _provenance_rows(rows, candidates, assertions),
    )
    gap_count = sum(len(results[stage].get("gaps", [])) for stage in STAGES)
    summary = (
        "# Repurposing programme summary\n\n"
        f"Disease: {case['disease']}\n\n"
        f"Gene: {case.get('gene') or 'not supplied'}\n\n"
        f"Pathology graph snapshot: {results['evidence_graph']['snapshot_id']}\n\n"
        f"Status: complete with {len(rows)} ranked candidate(s).\n\n"
        f"Sources: {len(documents)}; pathology nodes: {len(graph['profiles'])}; "
        f"assertions: {len(graph['assertions'])}; candidate seeds: {len(candidates)}; "
        f"reported gaps: {gap_count}.\n\n"
        "Candidate eligibility did not require a prior disease-drug literature association.\n\n"
        f"{EXPERIMENTAL_USE_POLICY}\n"
    )
    _write_once(outputs / "summary.md", summary.encode("utf-8"))
    return [
        outputs / "candidates.csv",
        outputs / "candidate_cards.md",
        outputs / "citations.jsonl",
        outputs / "graph.json",
        outputs / "candidate_provenance.jsonl",
        outputs / "summary.md",
    ]


def build_outputs(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, results)
    if current["state"] == "complete":
        return _read_json(run_root / "outputs" / "manifest.json")
    if current["state"] != "ready_to_build":
        raise ProgramError(f"Outputs cannot be built while state is {current['state']}")
    rows, candidates = _ranked_rows(results)
    artifact_paths = _write_output_files(run_root, case, results, rows, candidates)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "case_sha256": _sha256((run_root / "case.json").read_bytes()),
        "status": "complete",
        "candidate_count": len(rows),
        "stage_results": {
            stage: _sha256(_result_path(run_root, stage).read_bytes()) for stage in STAGES
        },
        "artifacts": [_artifact(path) for path in artifact_paths],
        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_json(run_root / "outputs" / "manifest.json", manifest)
    return manifest


__all__ = [
    "EXPERIMENTAL_USE_POLICY",
    "OBJECTIVE",
    "ProgramError",
    "STAGES",
    "build_outputs",
    "initialize",
    "next_action",
    "status",
    "submit",
]
