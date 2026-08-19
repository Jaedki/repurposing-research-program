"""Run identity, accepted-result loading, and derived programme status."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .candidates import _review_batches
from .contracts import EXPERIMENTAL_USE_POLICY, OBJECTIVE, STAGES
from .errors import ProgramError
from .evidence import _rows
from .graph import _graph_node_context
from .hypotheses import _connection_context
from .identity import _identity_queue
from .pathology import _research_concepts
from .storage import (
    _accepted_result_files,
    _item_result_path,
    _read_json,
    _result_path,
    _sha256,
    _stable_id,
    _write_json,
)


def _case(root: Path) -> dict[str, Any]:
    case = _read_json(root / "case.json")
    if not str(case.get("disease", "")).strip():
        raise ProgramError("case.json is not a valid lean repurposing case")
    if case.get("objective") != OBJECTIVE:
        raise ProgramError("Run objective differs from the current contract; start a fresh run")
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
            raise ProgramError(
                "Existing run case conflicts with the supplied disease, gene, or MONDO ID"
            )
        return _program_status(run_root, existing, _load_results(run_root))
    run_root.mkdir(parents=True, exist_ok=True)
    case_basis = {
        "disease": disease,
        "gene": gene,
        "mondo": mondo,
        "objective": OBJECTIVE,
    }
    case = {
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
            raise ProgramError(
                f"Result does not match the current stage sequence; start a fresh run: {path}"
            )
        results[stage] = _read_json(path)
    return results


def _item_ids(stage: str, results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    if stage in {"evidence_graph", "candidate_seed_generation"}:
        return sorted(str(row["concept_id"]) for row in _research_concepts(results))
    elif stage == "candidate_review":
        return sorted(str(row["candidate_id"]) for row in _review_batches(results))
    return []


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
    return next((item_id for item_id in item_ids if item_id not in accepted), None), len(
        accepted
    )


def _stop_reason(results: Mapping[str, Mapping[str, Any]]) -> str | None:
    curation = results.get("pathology_curation")
    if curation is not None and not any(
        row.get("disposition") == "research"
        for row in curation.get("records", {}).get("concepts", [])
        if isinstance(row, dict)
    ):
        return "pathology curation retained no concepts requiring deep research"
    coverage = results.get("pathology_coverage_expansion")
    if coverage is not None:
        source_nodes = results.get("pathology_sources", {}).get("records", {}).get(
            "source_nodes", []
        )
        landscape_proposals = results.get("pathology_landscape_scan", {}).get(
            "records", {}
        ).get("landscape_proposals", [])
        coverage_proposals = coverage.get("records", {}).get("coverage_proposals", [])
        has_source_concept = isinstance(source_nodes, list) and any(
            isinstance(row, dict) and row.get("node_type") != "disease_anchor"
            for row in source_nodes
        )
        if not has_source_concept and not landscape_proposals and not coverage_proposals:
            return "Monarch, DisMech, Asta, and Undermind returned no pathology concepts"
    checks = (
        ("evidence_graph", "profiles", "no source-backed pathology profiles were produced"),
        (
            "candidate_seed_generation",
            "candidates",
            "no mechanism-linked drug seeds were produced",
        ),
        ("candidate_review", "reviews", "no candidates received an evidence review"),
    )
    for stage, collection, reason in checks:
        result = results.get(stage)
        if result is not None and not result.get("records", {}).get(collection):
            return reason
    audit = results.get("candidate_audit")
    if audit is not None and not audit.get("records", {}).get("assessments"):
        return "the audit excluded every reviewed candidate"
    return None


def _verify_outputs(root: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "complete" or not manifest.get("candidate_count"):
        raise ProgramError("Output manifest is not complete")
    if manifest.get("case_sha256") != _sha256((root / "case.json").read_bytes()):
        raise ProgramError("case.json changed after outputs were built")
    accepted_results = manifest.get("accepted_results")
    result_files = _accepted_result_files(root)
    if not isinstance(accepted_results, dict) or set(accepted_results) != set(result_files):
        raise ProgramError("Output manifest does not cover every accepted result")
    for name, path in result_files.items():
        if _sha256(path.read_bytes()) != accepted_results[name]:
            raise ProgramError(f"Accepted result changed after outputs were built: {name}")
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
    next_task = next_item_id = None
    accepted_items = 0
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
    else:
        next_stage = STAGES[len(results)]
        if next_stage in {"pathology_source_screening", "pathology_sources"}:
            state, next_task = "needs_controller", next_stage
        elif next_stage == "pathology_source_adjudication":
            next_task = next_stage
            flagged = _rows(
                results["pathology_source_screening"]["records"],
                "flagged_sentences",
            )
            state = "needs_agent" if flagged else "needs_controller"
        elif next_stage in {
            "evidence_graph",
            "candidate_seed_generation",
            "candidate_review",
        }:
            next_task = {
                "evidence_graph": "pathology_node_research",
                "candidate_seed_generation": "candidate_seed_research",
                "candidate_review": "candidate_evidence_review",
            }[next_stage]
            next_item_id, accepted_items = _first_missing(
                run_root, next_task, _item_ids(next_stage, results)
            )
            state = "needs_agent" if next_item_id is not None else "needs_controller"
        elif next_stage == "candidate_identity":
            next_task = "candidate_identity"
            queue = _identity_queue(results["candidate_seed_generation"]["records"])
            state = "needs_agent" if queue else "needs_controller"
        else:
            state, next_task = "needs_agent", next_stage
    return {
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


def graph_context(root: str | Path, node_id: str) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    graph = results.get("evidence_graph")
    if graph is None:
        raise ProgramError("Graph context is unavailable before the evidence graph is frozen")
    records = graph.get("records")
    if not isinstance(records, dict) or graph.get("snapshot_id") != _stable_id(
        "GRAPH", records
    ):
        raise ProgramError("Evidence graph snapshot verification failed")
    node_id = node_id.strip()
    if not node_id:
        raise ProgramError("node_id is required")
    return {
        "case_id": case["case_id"],
        "graph_snapshot_id": graph["snapshot_id"],
        "context": _graph_node_context(records, node_id),
    }


def connection_context(root: str | Path, connection_id: str) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    graph = results.get("evidence_graph")
    if graph is None:
        raise ProgramError("Connection context is unavailable before the evidence graph is frozen")
    records = graph.get("records")
    if not isinstance(records, dict) or graph.get("snapshot_id") != _stable_id(
        "GRAPH", records
    ):
        raise ProgramError("Evidence graph snapshot verification failed")
    if "pathology_hypothesis_synthesis" not in results:
        raise ProgramError("Connection context is unavailable before hypothesis synthesis")
    connection_id = connection_id.strip()
    if not connection_id:
        raise ProgramError("connection_id is required")
    return {
        "case_id": case["case_id"],
        "graph_snapshot_id": graph["snapshot_id"],
        "context": _connection_context(results, connection_id),
    }
