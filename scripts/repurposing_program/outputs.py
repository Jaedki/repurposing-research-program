"""Final deterministic artifact export and build orchestration."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Mapping

from .bibliography import _canonicalize_documents
from .candidate_exports import _excluded_candidate_rows, _provenance_rows
from .contracts import EXPERIMENTAL_USE_POLICY, MAX_SCORE, SCORE_COMPONENTS, STAGES
from .errors import ProgramError
from .evidence import _all_documents, _rows
from .evidence_cards import _cards_bytes, _evidence_card_rows
from .manifests import _build_manifest
from .ranking import _ranked_rows
from .run_state import _case, _load_results, _program_status
from .storage import _read_json, _write_json, _write_jsonl, _write_once


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _write_output_files(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[Path]:
    outputs = run_root / "outputs"
    graph = results["evidence_graph"]["records"]
    csv_rows = [
        {key: value for key, value in row.items() if key != "candidate_id"}
        for row in rows
    ]
    _write_once(outputs / "candidates.csv", _csv_bytes(csv_rows, list(csv_rows[0])))
    card_rows = _evidence_card_rows(rows, results)
    _write_once(outputs / "candidate_cards.md", _cards_bytes(card_rows))
    excluded_rows = _excluded_candidate_rows(results, candidates)
    _write_jsonl(outputs / "candidate_exclusions.jsonl", excluded_rows)
    documents = sorted(
        _canonicalize_documents(run_root, _all_documents(results), verify_titles=False),
        key=lambda row: row["document_id"],
    )
    _write_jsonl(outputs / "citations.jsonl", documents)
    assertions = _rows(graph, "assertions")
    _write_json(
        outputs / "graph.json",
        {
            "case_id": case["case_id"],
            "snapshot_id": results["evidence_graph"]["snapshot_id"],
            "nodes": _rows(graph, "source_nodes"),
            "source_edges": _rows(graph, "source_edges"),
            "disease_context": _rows(graph, "disease_context"),
            "profiles": _rows(graph, "profiles"),
            "assertions": assertions,
        },
    )
    _write_jsonl(
        outputs / "candidate_provenance.jsonl",
        _provenance_rows(rows, candidates),
    )
    gap_count = sum(len(results[stage].get("gaps", [])) for stage in STAGES)
    raw_candidate_count = len(
        _rows(results["candidate_seed_generation"]["records"], "candidates")
    )
    coverage_nodes = [
        node for node in _rows(graph, "source_nodes")
        if node.get("node_type") != "disease_anchor"
    ]
    candidate_node_ids = {
        str(candidate_id): set(map(str, candidate["graph_node_ids"]))
        for candidate_id, candidate in candidates.items()
    }
    candidate_ids_by_node = {
        str(node["node_id"]): sorted(
            candidate_id
            for candidate_id, node_ids in candidate_node_ids.items()
            if str(node["node_id"]) in node_ids
        )
        for node in coverage_nodes
    }
    candidates_using_multiple_nodes = sorted(
        candidate_id
        for candidate_id, node_ids in candidate_node_ids.items()
        if len(node_ids) > 1
    )
    context_only_ids = {
        str(node["node_id"])
        for node in coverage_nodes
        if node.get("disposition") == "context_only"
    }
    candidates_using_context_only_nodes = sorted(
        candidate_id
        for candidate_id, node_ids in candidate_node_ids.items()
        if node_ids & context_only_ids
    )
    node_coverage = "; ".join(
        f"{node['node_id']} ({node['label']}): "
        f"{len(candidate_ids_by_node[str(node['node_id'])])}"
        for node in coverage_nodes
    ) or "none"
    uncovered_nodes = ", ".join(
        f"{node['node_id']} ({node['label']})"
        for node in coverage_nodes
        if not candidate_ids_by_node[str(node["node_id"])]
    ) or "none"
    multiple_node_candidates = ", ".join(candidates_using_multiple_nodes) or "none"
    context_only_candidates = ", ".join(candidates_using_context_only_nodes) or "none"
    summary = (
        "# Repurposing programme summary\n\n"
        f"Disease: {case['disease']}\n\n"
        f"Gene: {case.get('gene') or 'not supplied'}\n\n"
        f"Pathology graph snapshot: {results['evidence_graph']['snapshot_id']}\n\n"
        f"Status: complete with {len(rows)} ranked candidate(s) and "
        f"{len(excluded_rows)} audited exclusion(s).\n\n"
        f"Sources: {len(documents)}; pathology nodes: {len(graph['profiles'])}; "
        f"assertions: {len(graph['assertions'])}; raw candidate seeds: "
        f"{raw_candidate_count}; deduplicated candidates: {len(candidates)}; "
        f"reported gaps: {gap_count}.\n\n"
        "## Graph coverage\n\n"
        f"Candidates per graph node: {node_coverage}.\n\n"
        f"Nodes with no candidate: {uncovered_nodes}.\n\n"
        f"Candidates using more than one node: {multiple_node_candidates}.\n\n"
        f"Candidates using context-only nodes: {context_only_candidates}.\n\n"
        "Candidate nomination did not require a prior disease-drug literature association. "
        f"Audited candidates were ranked by an unweighted sum of "
        f"{len(SCORE_COMPONENTS)} 20-point components out of {MAX_SCORE}; "
        "exact-disease established use or qualifying interpretable experiments and other bounded decisive failures were "
        "exclusionary.\n\n"
        f"{EXPERIMENTAL_USE_POLICY}\n"
    )
    _write_once(outputs / "summary.md", summary.encode("utf-8"))
    return [
        outputs / "candidates.csv",
        outputs / "candidate_cards.md",
        outputs / "candidate_exclusions.jsonl",
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
    manifest = _build_manifest(
        run_root, case, results, rows, candidates, artifact_paths
    )
    _write_json(run_root / "outputs" / "manifest.json", manifest)
    return manifest
