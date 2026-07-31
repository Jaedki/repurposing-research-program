#!/usr/bin/env python3
"""Small, content-addressed controller for a linear repurposing programme."""

from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pathology_sources import (
    SourceError,
    fetch_pathology_sources,
    screen_pathology_sources,
)

from repurposing_program.bibliography import (
    _batches,
    _bibliographic_get,
    _bibliographic_request,
    _canonicalize_documents,
    _doi_metadata,
    _id_converter_records,
    _ncbi_summaries,
    _normalized_publication_id,
    _resolve_bibliographic_metadata,
    _summary_metadata,
    _validate_bibliographic_documents,
)
from repurposing_program.contracts import (
    AUDIT_EXCLUSION_POLICY,
    AUDIT_EXCLUSION_REASONS,
    CANONICAL_DOCUMENT_ID,
    EXPERIMENTAL_USE_POLICY,
    FIELD_RULES,
    GRAPH_INDEX_FIELDS,
    MAX_SCORE,
    OBJECTIVE,
    PATHOLOGY_PROFILE_LIST_FIELDS,
    PRIOR_ART_STATUSES,
    ROW_SCHEMAS,
    SCORE_COMPONENT_RUBRIC,
    SCORE_COMPONENTS,
    SCORE_LABELS,
    SCORE_RUBRIC,
    SCORE_VALUES,
    STAGE_GUIDANCE,
    STAGES,
    _CITATION_FIELDS,
    _COMPARATORS,
    _PATHOLOGY_FORBIDDEN_KEYS,
    _PUBLICATION_ID,
    _PUBLICATION_ID_PATTERN,
    _RESEARCH_CONTEXT_SECTIONS,
    _SECRET_KEYS,
    _SOURCE_CHECK_VERDICTS,
    _UNICHEM_API,
    _UNICHEM_SOURCE_IDS,
)
from repurposing_program.errors import ProgramError
from repurposing_program.evidence import (
    _all_documents,
    _cited_documents,
    _cited_ids,
    _document_has_inspectable_content,
    _find,
    _merge_documents,
    _merge_text,
    _merge_unique,
    _normalized_title,
    _rows,
    _select_cited_documents,
    _source_index,
    _validate_research_document_content,
    _year,
)
from repurposing_program.graph import (
    _assemble_graph_result,
    _graph_index,
    _graph_node_context,
    _graph_support_ids,
    _merge_assertions,
)
from repurposing_program.pathology import (
    _canonical_source_records,
    _compact_disease_context,
    _curation_concepts,
    _forbidden_pathology_paths,
    _research_concepts,
    _validate_curation,
    _validate_pathology_item,
    _validate_source_adjudication,
    _validate_source_result,
    _validate_source_screening,
)
from repurposing_program.storage import (
    _canonical_bytes,
    _item_result_path,
    _item_token,
    _packet_path,
    _read_json,
    _result_path,
    _sha256,
    _stable_id,
    _submission_path,
    _write_json,
    _write_jsonl,
    _write_once,
)
from repurposing_program.validation import (
    _contract_rows,
    _ids,
    _references,
    _required,
    _secret_paths,
    _validate_documents,
)


def _case(root: Path) -> dict[str, Any]:
    case = _read_json(root / "case.json")
    if not str(case.get("disease", "")).strip():
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
    field = "concept_id"
    if stage in {"evidence_graph", "candidate_seed_generation"}:
        rows = _research_concepts(results)
    elif stage == "candidate_review":
        rows = _review_batches(results)
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
    curation = results.get("pathology_curation")
    if curation is not None and not any(
        row.get("disposition") == "research"
        for row in curation.get("records", {}).get("concepts", [])
        if isinstance(row, dict)
    ):
        return "pathology curation retained no concepts requiring deep research"
    checks = (
        ("pathology_sources", "source_nodes", "Monarch and DisMech returned no pathology nodes"),
        ("evidence_graph", "profiles", "no source-backed pathology profiles were produced"),
        ("candidate_seed_generation", "candidates", "no mechanism-linked drug seeds were produced"),
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
        elif next_stage in {"evidence_graph", "candidate_seed_generation", "candidate_review"}:
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
    if not isinstance(records, dict) or graph.get("snapshot_id") != _stable_id("GRAPH", records):
        raise ProgramError("Evidence graph snapshot verification failed")
    node_id = node_id.strip()
    if not node_id:
        raise ProgramError("node_id is required")
    return {
        "case_id": case["case_id"],
        "graph_snapshot_id": graph["snapshot_id"],
        "context": _graph_node_context(records, node_id),
    }


def _packet_context(
    run_root: Path,
    task: str,
    item_id: str | None,
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if task == "pathology_source_adjudication":
        screening = results["pathology_source_screening"]
        return {
            "resolved_disease": screening.get("resolved_disease"),
            "flagged_sentences": _rows(
                screening["records"], "flagged_sentences"
            ),
        }
    if task == "pathology_curation":
        source_result = results["pathology_sources"]
        source = source_result["records"]
        nodes = sorted(
            (
                row
                for row in _rows(source, "source_nodes")
                if row.get("node_type") != "disease_anchor"
            ),
            key=lambda row: (
                str(row.get("node_type", "")).casefold(),
                str(row.get("label", "")).casefold(),
                str(row.get("node_id", "")),
            ),
        )
        edges = _rows(source, "source_edges")
        disease_context = _compact_disease_context(source)
        return {
            "resolved_disease": source_result.get("resolved_disease"),
            "source_nodes": nodes,
            "source_edges": edges,
            "disease_context": disease_context,
            "upstream_gaps": source_result.get("gaps", []),
        }
    if task == "pathology_node_research":
        documents = _all_documents(results)
        source = results["pathology_sources"]["records"]
        concept = _find(_research_concepts(results), "concept_id", str(item_id))
        canonical_nodes, canonical_edges = _canonical_source_records(results)
        node = _find(canonical_nodes, "node_id", str(item_id))
        member_ids = set(map(str, concept["member_node_ids"]))
        member_nodes = [
            {
                key: row[key]
                for key in ("node_id", "label", "node_type", "description", "source_ids", "source_section")
                if key in row
            }
            for row in _rows(source, "source_nodes")
            if str(row["node_id"]) in member_ids
        ]
        edges = [
            row
            for row in canonical_edges
            if str(item_id) in {str(row["subject_id"]), str(row["object_id"])}
        ]
        related_ids = {
            str(value)
            for edge in edges
            for value in (edge["subject_id"], edge["object_id"])
            if str(value) != str(item_id)
        }
        related_nodes = [
            row for row in canonical_nodes if str(row["node_id"]) in related_ids
        ]
        disease_context = _compact_disease_context(source)
        return {
            "concept": concept,
            "node": node,
            "member_source_nodes": member_nodes,
            "related_nodes": related_nodes,
            "adjacent_edges": edges,
            "disease_context": disease_context,
            "source_index": _source_index(
                documents,
                _cited_ids(
                    [node, *member_nodes, *related_nodes, *edges, *disease_context]
                ),
            ),
            "source_receipts": _rows(source, "source_receipts"),
            "upstream_gaps": results["pathology_sources"].get("gaps", []),
        }
    graph_result = results["evidence_graph"]
    graph = graph_result["records"]
    if task == "candidate_seed_research":
        return {
            "graph_snapshot_id": graph_result["snapshot_id"],
            "focal_context": _graph_node_context(graph, str(item_id)),
            "graph_index": _graph_index(graph),
            "context_lookup": {
                "argv": [
                    "python",
                    str(Path(__file__).with_name("orchestrate_program.py").resolve()),
                    "graph-context",
                    str(run_root),
                    "<node_id>",
                ]
            },
        }
    seeds = results["candidate_seed_generation"]["records"]
    if task == "candidate_identity":
        queued = _identity_queue(seeds)
        return {
            "identity_queue": queued,
            "canonical_candidate_options": _identity_candidate_options(seeds),
        }
    if task == "candidate_evidence_review":
        documents = _all_documents(results)
        batch = _find(_review_batches(results), "concept_id", str(item_id))
        candidate_ids = set(map(str, batch["candidate_ids"]))
        candidates = [
            row
            for row in _canonical_candidates(results)
            if str(row["candidate_id"]) in candidate_ids
        ]
        node_ids = {
            str(node_id)
            for candidate in candidates
            for node_id in candidate["graph_node_ids"]
        }
        concepts = [
            row
            for row in _rows(graph, "source_nodes")
            if str(row["node_id"]) in node_ids
        ]
        profiles = [
            row for row in _rows(graph, "profiles") if str(row["node_id"]) in node_ids
        ]
        review_source_ids = {
            str(source_id)
            for candidate in candidates
            for source_id in (
                *candidate["mechanism_source_ids"],
                *candidate.get("identity", {}).get("source_ids", []),
            )
        }
        return {
            "primary_concept_id": str(item_id),
            "candidates": candidates,
            "pathology_concepts": concepts,
            "pathology_profiles": profiles,
            "source_index": _source_index(documents, review_source_ids),
        }
    documents = _canonicalize_documents(
        run_root, _all_documents(results), verify_titles=False
    )
    return {
        "candidates": _canonical_candidates(results),
        "reviews": _rows(results["candidate_review"]["records"], "reviews"),
        "evidence_graph": graph,
        "candidate_identity": results["candidate_identity"]["records"],
        "source_index": _source_index(documents),
    }


_PACKET_CASE_FIELDS = ("case_id", "disease", "gene", "mondo")


def _record_contract(task: str) -> dict[str, dict[str, Any]]:
    return {
        name: {"type": "list of objects", **ROW_SCHEMAS[name]}
        for name in STAGE_GUIDANCE[task]["collections"]
    }


def _validate_packet(unsigned: Mapping[str, Any], task: str, item_id: str | None) -> None:
    if unsigned.get("stage") != task or unsigned.get("item_id") != item_id:
        raise ProgramError("Packet stage or item_id does not match the ready task")
    if "objective" in unsigned:
        raise ProgramError("Worker packets must use their stage task, not the global objective")
    case = unsigned.get("case")
    if not isinstance(case, dict) or set(case) != set(_PACKET_CASE_FIELDS):
        raise ProgramError("Worker packet case must contain only case_id, disease, gene, and mondo")
    contract = unsigned.get("result_contract")
    if not isinstance(contract, dict) or contract.get("records") != _record_contract(task):
        raise ProgramError("Worker packet result contract does not match the task schema")
    secrets = _secret_paths(unsigned)
    if secrets:
        raise ProgramError(f"Credentials must never be persisted in packets: {secrets}")


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
    packet_rules = (
        [
            "Use only the supplied sentences; do not search or retrieve sources.",
            "Return one compact decision for every supplied sentence_id and no others.",
            "Never rewrite, quote, summarize, or create a pathology node from a sentence.",
            "Return JSON only and do not include credentials or API keys.",
        ]
        if task == "pathology_source_adjudication"
        else [
            "Use only supplied or newly retrieved named sources; never invent citations.",
            "Use PMID:<digits>, PMCID:PMC<digits>, DOI:<doi>, recognized accession:<id>, or "
            "HTTPS URL document IDs; never invent DOC aliases.",
            *(
                [
                    "Search and read freely, but return only documents that directly support a "
                    "submitted claim, counterclaim, identity decision, or limitation.",
                    "Every returned document must include evidence_passages with at least one "
                    "non-empty text and locator copied from inspectable source content.",
                    "Every returned document_id must be cited in this result through source_ids, "
                    "pathology_source_ids, or mechanism_source_ids; cited upstream documents do "
                    "not need to be returned again.",
                ]
                if "documents" in guidance["collections"]
                else []
            ),
            "Preserve contradictions, negative results, unresolved identity, and source gaps.",
            "Return JSON only and do not include credentials or API keys.",
        ]
    )
    unsigned = {
        "stage": task,
        "item_id": item_id,
        "role": guidance["role"],
        "task": guidance["task"],
        "case": {
            key: case.get(key)
            for key in _PACKET_CASE_FIELDS
        },
        "upstream": upstream,
        "context": _packet_context(run_root, task, item_id, results),
        "result_contract": {
            "stage": task,
            "item_id": item_id,
            "packet_id": "copy from this packet",
            "status": "complete",
            "records": _record_contract(task),
            "field_rules": FIELD_RULES[task],
            **(
                {
                    "score_rubric": SCORE_RUBRIC,
                    "exclusion_policy": AUDIT_EXCLUSION_POLICY,
                }
                if task == "candidate_audit"
                else {}
            ),
            "gaps": "list of explicit limitations or unresolved questions",
            "notes": "optional list of concise notes",
        },
        "rules": packet_rules,
    }
    _validate_packet(unsigned, task, item_id)
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
    display_item_id = (
        f"{task}/{item_id}/{_item_token(str(item_id))}"
        if item_id is not None
        else task
    )
    return {
        **current,
        "display_item_id": display_item_id,
        "packet_id": packet["packet_id"],
        "packet_path": str(packet_path),
        "suggested_result_path": str(result_path),
        "worker_prompt": (
            f"Complete {display_item_id}. Read only the content packet at {packet_path} and any "
            "controller-returned graph "
            f"context explicitly authorized by that packet. Complete the {task} task and write "
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


def _item_cited_documents(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _cited_documents(accepted[item_id]["records"])
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
    return _assemble_graph_result(
        results,
        _item_collection(
            root, results, "evidence_graph", "pathology_node_research", "profiles"
        ),
        _item_collection(
            root, results, "evidence_graph", "pathology_node_research", "assertions"
        ),
        _item_cited_documents(
            root, results, "evidence_graph", "pathology_node_research"
        ),
        _item_gaps(root, results, "evidence_graph", "pathology_node_research"),
    )


def _review_batches(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    concept_ids = {str(row["concept_id"]) for row in _research_concepts(results)}
    grouped: dict[str, list[str]] = {concept_id: [] for concept_id in concept_ids}
    candidates = _canonical_candidates(results)
    candidate_ids = _ids(candidates, "candidate_id", "candidates")
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        origin_ids = sorted(set(map(str, candidate.get("origin_concept_ids", []))))
        unknown = set(origin_ids) - concept_ids
        if not origin_ids or unknown:
            raise ProgramError(
                f"Candidate {candidate_id} has invalid origin_concept_ids: {sorted(unknown)}"
            )
        node_ids = set(map(str, candidate.get("graph_node_ids", [])))
        primary = min(origin_ids, key=lambda value: (value not in node_ids, value))
        if primary not in node_ids:
            raise ProgramError(f"Candidate {candidate_id} has no node in an origin concept")
        grouped[primary].append(candidate_id)

    batches = [
        {"concept_id": concept_id, "candidate_ids": sorted(ids)}
        for concept_id, ids in sorted(grouped.items())
        if ids
    ]
    assigned = [candidate_id for batch in batches for candidate_id in batch["candidate_ids"]]
    if len(assigned) != len(set(assigned)) or set(assigned) != candidate_ids:
        raise ProgramError("Review batches must partition every candidate exactly once")
    return batches


def _candidate_queries(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    queries: set[tuple[str, int | None, str]] = set()
    identifiers = row.get("identifiers", {})
    if isinstance(identifiers, Mapping):
        for key, raw_value in identifiers.items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                compound = str(value).strip()
                if key in {"inchi", "inchikey"} and compound:
                    queries.add((key, None, compound))
                elif key in _UNICHEM_SOURCE_IDS:
                    if compound:
                        queries.add(("sourceID", _UNICHEM_SOURCE_IDS[key], compound))
    return [
        {
            "compound": compound,
            "type": query_type,
            **({"sourceID": source_id} if source_id is not None else {}),
        }
        for query_type, source_id, compound in sorted(queries, key=str)
    ]


def _post_unichem(endpoint: str, body: Mapping[str, Any]) -> dict[str, Any]:
    payload = _canonical_bytes(body)
    request = Request(
        f"{_UNICHEM_API}/{endpoint}",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "repurposing-research-program/4",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramError(f"UniChem {endpoint} returned invalid JSON: {exc}") from exc
        else:
            explicit_no_result = (
                endpoint == "compounds"
                and isinstance(result, dict)
                and result.get("response") == "Not found"
                and result.get("compounds") == []
            )
            if (
                not isinstance(result, dict)
                or result.get("response") != "Success"
            ) and not explicit_no_result:
                raise ProgramError(f"UniChem {endpoint} returned an invalid response")
            return result
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _unichem_request(
    root: Path, endpoint: str, body: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    token = _sha256(_canonical_bytes(body))[:24]
    path = root / "sources" / "raw" / "unichem" / f"{endpoint}-{token}.json"
    if path.exists():
        response = _read_json(path)
    else:
        response = _post_unichem(endpoint, body)
        _write_json(path, response)
    return response, {
        "source": "UniChem",
        "api": _UNICHEM_API,
        "endpoint": endpoint,
        "query": dict(body),
        "raw_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path.read_bytes()),
    }


def _unichem_requests(
    root: Path, endpoint: str, bodies: Iterable[Mapping[str, Any]]
) -> dict[bytes, tuple[dict[str, Any], dict[str, Any]]]:
    unique = {_canonical_bytes(body): dict(body) for body in bodies}
    return {
        key: _unichem_request(root, endpoint, body) for key, body in unique.items()
    }


def _query_key(query: Mapping[str, Any]) -> tuple[int, str] | None:
    if query.get("type") != "sourceID":
        return None
    return int(query["sourceID"]), str(query["compound"]).casefold()


def _resolve_seed_identities(
    root: Path, candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queries_by_seed = {
        str(row["seed_id"]): _candidate_queries(row) for row in candidates
    }
    exact = _unichem_requests(
        root, "compounds", (query for queries in queries_by_seed.values() for query in queries)
    )
    receipts = [value[1] for _, value in sorted(exact.items())]
    preliminary: dict[str, dict[str, Any]] = {}
    query_seeds: dict[tuple[int, str], set[str]] = {}
    for seed_id, queries in queries_by_seed.items():
        found: list[dict[str, Any]] = []
        missed = False
        for query in queries:
            response = exact[_canonical_bytes(query)][0]
            compounds = [row for row in response.get("compounds", []) if isinstance(row, dict)]
            found.extend(compounds)
            missed = missed or not compounds
            key = _query_key(query)
            if key:
                query_seeds.setdefault(key, set()).add(seed_id)
        ucis = {str(row.get("uci")) for row in found if row.get("uci") is not None}
        if not queries:
            preliminary[seed_id] = {"status": "not_queryable", "queries": []}
        elif not ucis:
            preliminary[seed_id] = {"status": "no_result", "queries": queries}
        elif len(ucis) != 1 or missed:
            preliminary[seed_id] = {
                "status": "conflicting_or_partial_result",
                "queries": queries,
                "ucis": sorted(ucis),
            }
        else:
            uci = next(iter(ucis))
            compound = next(row for row in found if str(row.get("uci")) == uci)
            preliminary[seed_id] = {
                "status": "exact",
                "queries": queries,
                "uci": uci,
                "standard_inchikey": compound.get("standardInchiKey"),
            }

    exact_seeds = {
        seed_id: row for seed_id, row in preliminary.items() if row["status"] == "exact"
    }
    connectivity_bodies = [
        {"compound": uci, "type": "uci", "searchComponents": True}
        for uci in sorted({row["uci"] for row in exact_seeds.values()})
    ]
    connectivity = _unichem_requests(root, "connectivity", connectivity_bodies)
    receipts.extend(value[1] for _, value in sorted(connectivity.items()))
    related: dict[str, set[str]] = {seed_id: set() for seed_id in exact_seeds}
    for body in connectivity_bodies:
        uci = str(body["compound"])
        response = connectivity[_canonical_bytes(body)][0]
        own = {seed_id for seed_id, row in exact_seeds.items() if row["uci"] == uci}
        for source in response.get("sources", []):
            key = (int(source.get("id", -1)), str(source.get("compoundId", "")).casefold())
            for other in query_seeds.get(key, set()) - own:
                if other in exact_seeds and exact_seeds[other]["uci"] != uci:
                    for seed_id in own:
                        related[seed_id].add(other)
                        related[other].add(seed_id)
    by_connectivity: dict[str, set[str]] = {}
    for seed_id, row in exact_seeds.items():
        inchikey = str(row.get("standard_inchikey") or "")
        if len(inchikey) >= 14:
            by_connectivity.setdefault(inchikey[:14], set()).add(seed_id)
    for seed_ids in by_connectivity.values():
        ucis = {exact_seeds[seed_id]["uci"] for seed_id in seed_ids}
        if len(ucis) > 1:
            for seed_id in seed_ids:
                related[seed_id].update(seed_ids - {seed_id})

    for seed_id, seed_related in related.items():
        if seed_related:
            preliminary[seed_id]["status"] = "connectivity_match"
            preliminary[seed_id]["related_seed_ids"] = sorted(seed_related)
    enriched = [
        {**row, "identity_resolution": preliminary[str(row["seed_id"])]}
        for row in candidates
    ]
    return enriched, sorted(receipts, key=lambda row: row["raw_path"])


def _identity_queue(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(records, "candidates")
        if row.get("identity_resolution", {}).get("status") != "exact"
    ]


def _exact_identity_groups(records: Mapping[str, Any]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for seed in _rows(records, "candidates"):
        resolution = seed.get("identity_resolution", {})
        if resolution.get("uci") is None:
            continue
        candidate_id = f"UNICHEM:{resolution['uci']}"
        groups.setdefault(candidate_id, []).append(str(seed["seed_id"]))
    return {candidate_id: sorted(groups[candidate_id]) for candidate_id in sorted(groups)}


def _identity_candidate_options(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    seeds = {str(row["seed_id"]): row for row in _rows(records, "candidates")}
    queued_ids = {str(row["seed_id"]) for row in _identity_queue(records)}
    options: list[dict[str, Any]] = []
    for candidate_id, member_ids in _exact_identity_groups(records).items():
        rows = [seeds[seed_id] for seed_id in member_ids]
        queued_block = bool(set(member_ids) & queued_ids)
        options.append({
            "candidate_id": candidate_id,
            "option_type": (
                "queued_exact_block" if queued_block else "existing_resolved_candidate"
            ),
            "candidate_names": sorted(
                {str(row["name"]) for row in rows},
                key=lambda value: (value.casefold(), value),
            ),
            "asserted_candidate_ids": sorted({
                str(row["candidate_id"]) for row in rows
            }),
            "required_member_seed_ids": member_ids if queued_block else [],
        })
    return options


def _merge_candidate_rows(
    rows: list[dict[str, Any]], candidate_id: str, identity: Mapping[str, Any]
) -> dict[str, Any]:
    rows = sorted(
        {str(row["seed_id"]): row for row in rows}.values(),
        key=lambda row: str(row["seed_id"]),
    )
    return {
        "candidate_id": candidate_id,
        "name": str(identity["preferred_name"]),
        "identity": dict(identity),
        "mechanism_hypothesis": _merge_text(*(row["mechanism_hypothesis"] for row in rows)),
        "graph_node_ids": sorted({str(value) for row in rows for value in row["graph_node_ids"]}),
        "pathology_source_ids": sorted({
            str(value) for row in rows for value in row["pathology_source_ids"]
        }),
        "mechanism_source_ids": sorted({
            str(value) for row in rows for value in row["mechanism_source_ids"]
        }),
        "origin_concept_ids": sorted({
            str(value) for row in rows for value in row["origin_concept_ids"]
        }),
        "member_seed_ids": [str(row["seed_id"]) for row in rows],
        "asserted_candidate_ids": sorted({str(row["candidate_id"]) for row in rows}),
    }


def _canonical_candidates(
    results: Mapping[str, Mapping[str, Any]],
    *,
    reviewed: bool = True,
) -> list[dict[str, Any]]:
    seed_records = results["candidate_seed_generation"]["records"]
    seeds = {str(row["seed_id"]): row for row in _rows(seed_records, "candidates")}
    queued = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    exact_groups = _exact_identity_groups(seed_records)
    candidates: dict[str, dict[str, Any]] = {}
    for candidate_id, member_ids in exact_groups.items():
        member_ids = set(member_ids)
        if member_ids & queued:
            continue
        rows = [seeds[seed_id] for seed_id in sorted(member_ids)]
        preferred_name = min(
            (str(row["name"]) for row in rows),
            key=lambda value: (value.casefold(), value),
        )
        identity = {
            "status": "resolved",
            "preferred_name": preferred_name,
            "identifiers": {"unichem_uci": candidate_id.split(":", 1)[1]},
        }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    if not reviewed:
        return [candidates[key] for key in sorted(candidates)]

    identity_result = results.get("candidate_identity", {"records": {"identity_groups": []}})
    for group in _rows(identity_result["records"], "identity_groups"):
        rows = [seeds[str(seed_id)] for seed_id in group["member_seed_ids"]]
        target = group.get("canonical_candidate_id")
        if target:
            exact = exact_groups[str(target)]
            rows.extend(seeds[seed_id] for seed_id in exact)
            preferred_name = min(
                (str(row["name"]) for row in rows),
                key=lambda value: (value.casefold(), value),
            )
            identity = {
                "status": "resolved",
                "preferred_name": preferred_name,
                "identifiers": {"unichem_uci": str(target).split(":", 1)[1]},
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
            candidate_id = str(target)
        else:
            candidate_id = _stable_id("CANDIDATE", sorted(map(str, group["member_seed_ids"])))
            identity = {
                "status": group["status"],
                "preferred_name": group["preferred_name"],
                "identifiers": group["identifiers"],
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    return [candidates[key] for key in sorted(candidates)]


def _build_seed_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    item_ids = _item_ids("candidate_seed_generation", results)
    accepted = _item_results(root, "candidate_seed_research", item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError("Cannot aggregate seeds before every researched concept is accepted")
    raw_candidates = []
    for item_id in item_ids:
        for row in _rows(accepted[item_id]["records"], "candidates"):
            seed_id = _stable_id(
                "SEED", {"origin_concept_id": item_id, "candidate_id": row["candidate_id"]}
            )
            raw_candidates.append({
                **row,
                "seed_id": seed_id,
                "origin_concept_ids": [item_id],
            })
    candidates, receipts = _resolve_seed_identities(root, raw_candidates)
    queued_count = sum(
        row["identity_resolution"]["status"] != "exact" for row in candidates
    )
    upstream_document_ids = {
        str(row["document_id"]) for row in _all_documents(results)
    }
    records = {
        "candidates": candidates,
        "identity_receipts": receipts,
        "exclusions": [
            {**row, "origin_concept_id": item_id}
            for item_id in item_ids
            for row in _rows(accepted[item_id]["records"], "exclusions")
        ],
    }
    records["documents"] = _select_cited_documents(
        _merge_documents(
            row
            for item_id in item_ids
            for row in _cited_documents(accepted[item_id]["records"])
            if str(row["document_id"]) not in upstream_document_ids
        ),
        records,
    )
    return {
        "stage": "candidate_seed_generation",
        "status": "complete",
        "graph_snapshot_id": results["evidence_graph"]["snapshot_id"],
        "records": records,
        "gaps": _item_gaps(
            root, results, "candidate_seed_generation", "candidate_seed_research"
        ),
        "notes": [
            f"Submitted {len(raw_candidates)} raw seeds to UniChem; "
            f"resolved {len(_exact_identity_groups(records))} exact identity group(s) and queued "
            f"{queued_count} seed(s) for identity review."
        ],
    }


def _empty_identity_result(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if _identity_queue(results["candidate_seed_generation"]["records"]):
        raise ProgramError("Candidate identity review is required before controller advancement")
    return {
        "stage": "candidate_identity",
        "status": "complete",
        "records": {"documents": [], "identity_groups": []},
        "gaps": [],
        "notes": ["Every candidate was resolved by exact UniChem identity."],
    }


def _build_review_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    reviews = _merge_unique(
        _item_collection(
            root, results, "candidate_review", "candidate_evidence_review", "reviews"
        ),
        "candidate_id",
        "reviews",
    )
    upstream_document_ids = {
        str(row["document_id"]) for row in _all_documents(results)
    }
    return {
        "stage": "candidate_review",
        "status": "complete",
        "records": {
            "documents": _select_cited_documents(
                (
                    row
                    for row in _item_cited_documents(
                        root, results, "candidate_review", "candidate_evidence_review"
                    )
                    if str(row["document_id"]) not in upstream_document_ids
                ),
                reviews,
            ),
            "reviews": reviews,
        },
        "gaps": _item_gaps(root, results, "candidate_review", "candidate_evidence_review"),
        "notes": [],
    }


def _advance_controller(
    root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> None:
    if stage == "pathology_source_screening":
        try:
            result = screen_pathology_sources(
                root, str(case["disease"]), case.get("mondo")
            )
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        _validate_source_screening(result)
    elif stage == "pathology_source_adjudication":
        flagged = _rows(
            results["pathology_source_screening"]["records"],
            "flagged_sentences",
        )
        if flagged:
            raise ProgramError("Flagged source sentences require agent adjudication")
        result = {
            "stage": "pathology_source_adjudication",
            "status": "complete",
            "records": {"sentence_decisions": []},
            "gaps": [],
            "notes": ["No DisMech free-text sentences required adjudication."],
        }
    elif stage == "pathology_sources":
        decisions = {
            str(row["sentence_id"]): str(row["decision"])
            for row in _rows(
                results["pathology_source_adjudication"]["records"],
                "sentence_decisions",
            )
        }
        try:
            result = fetch_pathology_sources(
                root,
                str(case["disease"]),
                case.get("mondo"),
                decisions,
            )
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        result["records"]["documents"] = _canonicalize_documents(
            root,
            _rows(result["records"], "documents"),
            verify_titles=False,
        )
        _validate_source_result(result)
    elif stage == "evidence_graph":
        result = _build_graph_result(root, results)
    elif stage == "candidate_seed_generation":
        result = _build_seed_result(root, results)
    elif stage == "candidate_identity":
        result = _empty_identity_result(results)
    elif stage == "candidate_review":
        result = _build_review_result(root, results)
    else:
        raise ProgramError(f"No controller action exists for stage: {stage}")
    _write_json(_result_path(root, stage), result)


def _validate_seed_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    candidates = _contract_rows(records, "candidates", "candidate_id")
    _contract_rows(records, "exclusions")
    graph = results["evidence_graph"]["records"]
    concept = _find(_research_concepts(results), "concept_id", item_id)
    concept_id = str(concept["concept_id"])
    support_by_node = _graph_support_ids(graph)
    allowed_node_ids = set(support_by_node)
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
            ("candidate_id", "name", "mechanism_hypothesis"),
            label,
        )
        if str(row["name"]).strip().casefold() in _COMPARATORS:
            raise ProgramError(f"{label} is a comparator, not a drug candidate")
        graph_refs = _references(row, "graph_node_ids", allowed_node_ids, label)
        if concept_id not in graph_refs:
            raise ProgramError(f"{label}.graph_node_ids must include the focal item concept")
        pathology_refs = _references(row, "pathology_source_ids", pathology_source_ids, label)
        unsupported = sorted(
            node_id
            for node_id in graph_refs
            if not pathology_refs & support_by_node[node_id]
        )
        if unsupported:
            raise ProgramError(
                f"{label}.pathology_source_ids do not support graph nodes: {unsupported}"
            )
        mechanism_refs = _references(row, "mechanism_source_ids", mechanism_source_ids, label)
        if not mechanism_refs & new_mechanism_source_ids:
            raise ProgramError(f"{label}.mechanism_source_ids needs a retained drug-MOA source")


def _validate_candidate_identity(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    groups = _contract_rows(records, "identity_groups")
    seed_records = results["candidate_seed_generation"]["records"]
    queue_ids = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    covered: list[str] = []
    targets: list[str] = []
    exact_blocks = {
        candidate_id: set(member_ids)
        for candidate_id, member_ids in _exact_identity_groups(seed_records).items()
    }
    candidate_options = {
        str(row["candidate_id"]): row
        for row in _identity_candidate_options(seed_records)
    }
    document_ids = {str(row["document_id"]) for row in documents}
    for index, group in enumerate(groups):
        label = f"identity_groups[{index}]"
        member_ids = group.get("member_seed_ids")
        if not isinstance(member_ids, list) or not member_ids:
            raise ProgramError(f"{label}.member_seed_ids must be a non-empty list")
        members = [str(value) for value in member_ids]
        if len(members) != len(set(members)) or not set(members) <= queue_ids:
            raise ProgramError(f"{label}.member_seed_ids must be unique queued seed IDs")
        member_set = set(members)
        member_exact_ids = {
            candidate_id
            for candidate_id, block in exact_blocks.items()
            if member_set.intersection(block)
        }
        if any(member_set & block and not block <= member_set for block in exact_blocks.values()):
            raise ProgramError(f"{label} cannot split an exact UniChem identity group")
        covered.extend(members)
        if group.get("status") not in {"resolved", "unresolved", "conflicting"}:
            raise ProgramError(f"{label}.status must be resolved, unresolved, or conflicting")
        _required(group, ("preferred_name", "reason"), label)
        if not isinstance(group.get("identifiers"), dict):
            raise ProgramError(f"{label}.identifiers must be an object")
        target = group.get("canonical_candidate_id")
        if target is not None:
            target = str(target)
            option = candidate_options.get(target)
            valid = (
                option is not None
                and group["status"] == "resolved"
                and member_exact_ids <= {target}
            )
            if valid and option["required_member_seed_ids"]:
                valid = set(option["required_member_seed_ids"]) <= member_set
            if not valid:
                raise ProgramError(
                    f"{label}.canonical_candidate_id must be null or copied exactly from "
                    "context.canonical_candidate_options for a resolved group containing any "
                    "required queued block and no different exact UCI"
                )
            targets.append(target)
        elif group["status"] == "resolved" and len(member_exact_ids) == 1:
            raise ProgramError(
                f"{label}.canonical_candidate_id is required when a resolved group contains "
                "one exact UniChem identity"
            )
        _references(group, "source_ids", document_ids, label)
    if sorted(covered) != sorted(queue_ids) or len(covered) != len(set(covered)):
        raise ProgramError("identity_groups must partition every queued seed exactly once")
    if len(targets) != len(set(targets)):
        raise ProgramError("Each exact UniChem candidate may be attached at most once")


def _component_score(value: Any, label: str) -> int:
    if type(value) is not int or value not in SCORE_VALUES:
        raise ProgramError(f"{label} must be one of {sorted(SCORE_VALUES)}")
    return value


def _accepted_ids(
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    collection: str,
    field: str,
) -> set[str]:
    return _ids(_rows(results[stage]["records"], collection), field, collection)


def _validate_cited_entries(
    value: Any,
    *,
    label: str,
    text_field: str,
    source_ids: set[str],
) -> None:
    if not isinstance(value, list):
        raise ProgramError(f"{label} must be a list of objects")
    seen: set[str] = set()
    required_fields = {text_field, "source_ids"}
    for index, entry in enumerate(value):
        entry_label = f"{label}[{index}]"
        if not isinstance(entry, dict):
            raise ProgramError(f"{entry_label} must be an object")
        missing = sorted(required_fields - set(entry))
        if missing:
            raise ProgramError(f"{entry_label} is missing fields: {', '.join(missing)}")
        unexpected = sorted(set(entry) - required_fields)
        if unexpected:
            raise ProgramError(f"{entry_label} has unexpected fields: {unexpected}")
        text = str(entry[text_field]).strip()
        if not text:
            raise ProgramError(f"{entry_label}.{text_field} must be non-empty")
        key = text.casefold()
        if key in seen:
            raise ProgramError(f"{label}.{text_field} values must be unique")
        seen.add(key)
        _references(entry, "source_ids", source_ids, entry_label)


def _validate_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not str(item).strip() for item in value):
        raise ProgramError(f"{label} must be a list of non-empty strings")


def _validate_exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProgramError(f"{label} must be an object")
    missing = sorted(fields - set(value))
    unexpected = sorted(set(value) - fields)
    if missing:
        raise ProgramError(f"{label} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ProgramError(f"{label} has unexpected fields: {unexpected}")
    return value


def _validate_review_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    reviews = _contract_rows(records, "reviews", "candidate_id")
    batch = _find(_review_batches(results), "concept_id", item_id)
    expected_ids = set(map(str, batch["candidate_ids"]))
    review_ids = {str(row["candidate_id"]) for row in reviews}
    if review_ids != expected_ids:
        raise ProgramError("candidate review must cover exactly the supplied batch candidates")
    retained_ids = {str(row["document_id"]) for row in documents}
    source_ids = {
        *(str(row["document_id"]) for row in _all_documents(results)),
        *retained_ids,
    }
    for index, row in enumerate(reviews):
        label = f"reviews[{index}]"
        _required(row, ("candidate_id", "hypothesis", "mechanistic_bridge"), label)
        _validate_cited_entries(
            row["supporting_findings"],
            label=f"{label}.supporting_findings",
            text_field="finding",
            source_ids=source_ids,
        )
        if not row["supporting_findings"]:
            raise ProgramError(f"{label}.supporting_findings must not be empty")
        _validate_string_list(row["assumptions"], f"{label}.assumptions")
        _validate_string_list(row["limitations"], f"{label}.limitations")
        _validate_cited_entries(
            row["aliases"],
            label=f"{label}.aliases",
            text_field="name",
            source_ids=source_ids,
        )
        _validate_cited_entries(
            row["why_not"],
            label=f"{label}.why_not",
            text_field="finding",
            source_ids=source_ids,
        )
        prior_art = _validate_exact_object(
            row["prior_art"], {"status", "summary", "findings"}, f"{label}.prior_art"
        )
        if prior_art["status"] not in PRIOR_ART_STATUSES:
            raise ProgramError(
                f"{label}.prior_art.status must be one of {sorted(PRIOR_ART_STATUSES)}"
            )
        if not str(prior_art["summary"]).strip():
            raise ProgramError(f"{label}.prior_art.summary must be non-empty")
        _validate_cited_entries(
            prior_art["findings"],
            label=f"{label}.prior_art.findings",
            text_field="finding",
            source_ids=source_ids,
        )
        if prior_art["status"] in {
            "preclinical_only", "human_intervention", "established_use"
        } and not prior_art["findings"]:
            raise ProgramError(
                f"{label}.prior_art.findings must not be empty for status {prior_art['status']}"
            )
        cited_ids = _cited_ids(row)
        unknown = cited_ids - source_ids
        if unknown:
            raise ProgramError(f"{label} contains unknown source IDs: {sorted(unknown)}")
        if not cited_ids & retained_ids:
            raise ProgramError(f"{label} needs a cited document retained by this review")


def _assessment_source_uses(row: Mapping[str, Any]) -> set[tuple[str, str]]:
    uses = {
        (str(source_id), component)
        for component in SCORE_COMPONENTS
        for source_id in row["component_scores"][component]["source_ids"]
    }
    uses.update(
        (str(source_id), "net_assessment")
        for source_id in row["net_assessment"]["source_ids"]
    )
    for collection in ("aliases", "why_not"):
        uses.update(
            (str(source_id), f"{collection}[{index}]")
            for index, entry in enumerate(row[collection])
            for source_id in entry["source_ids"]
        )
    return uses


def _validate_source_integrity(
    value: Any,
    *,
    expected_uses: set[tuple[str, str]],
    documents: Mapping[str, Mapping[str, Any]],
    label: str,
) -> None:
    integrity = _validate_exact_object(value, {"checks"}, label)
    checks = integrity["checks"]
    if not isinstance(checks, list):
        raise ProgramError(f"{label}.checks must be a list")
    actual_uses: list[tuple[str, str]] = []
    for index, check in enumerate(checks):
        check_label = f"{label}.checks[{index}]"
        check = _validate_exact_object(
            check, {"source_id", "scope", "verdict", "finding"}, check_label
        )
        source_id = str(check["source_id"])
        scope = str(check["scope"])
        verdict = str(check["verdict"])
        finding = str(check["finding"]).strip()
        if verdict not in _SOURCE_CHECK_VERDICTS:
            raise ProgramError(
                f"{check_label}.verdict must be one of {sorted(_SOURCE_CHECK_VERDICTS)}"
            )
        if not finding:
            raise ProgramError(f"{check_label}.finding must be non-empty")
        if re.search(
            r"\b(?:re-?verify|unverif(?:ied|iable)|needs? (?:independent )?verification|"
            r"verify later|requires? verification|(?:cannot|could not|unable to) verify|"
            r"verification (?:unavailable|pending))\b",
            finding,
            flags=re.IGNORECASE,
        ):
            raise ProgramError(
                f"{check_label}.finding must decide the supplied source use now, not defer verification"
            )
        document = documents.get(source_id)
        if document is None:
            raise ProgramError(f"{check_label}.source_id is not in the retained corpus")
        if not _document_has_inspectable_content(document):
            raise ProgramError(
                f"{check_label}.source_id has no inspectable content in the retained corpus"
            )
        actual_uses.append((source_id, scope))
    if len(actual_uses) != len(set(actual_uses)):
        raise ProgramError(f"{label}.checks contains duplicate source-use checks")
    actual = set(actual_uses)
    if actual != expected_uses:
        raise ProgramError(
            f"{label}.checks must cover every cited source use exactly once; "
            f"missing={sorted(expected_uses - actual)}, unknown={sorted(actual - expected_uses)}"
        )
    publication_uses: dict[tuple[str, str], str] = {}
    for source_id, scope in expected_uses:
        canonical_id = str(
            documents[source_id].get("canonical_publication_id") or source_id
        )
        key = (scope, canonical_id)
        prior = publication_uses.setdefault(key, source_id)
        if prior != source_id:
            raise ProgramError(
                f"{label}.checks cites publication {canonical_id} more than once in {scope} "
                f"through identifier aliases {sorted({prior, source_id})}"
            )


def _validate_candidate_audit(
    records: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    source_index: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    assessments = _contract_rows(records, "assessments", "candidate_id")
    exclusions = _contract_rows(records, "excluded_candidates", "candidate_id")
    candidate_ids = _accepted_ids(
        results, "candidate_review", "reviews", "candidate_id"
    )
    assessment_ids = {str(row["candidate_id"]) for row in assessments}
    exclusion_ids = {str(row["candidate_id"]) for row in exclusions}
    if assessment_ids & exclusion_ids or assessment_ids | exclusion_ids != candidate_ids:
        raise ProgramError(
            "assessments and excluded_candidates must partition every reviewed candidate exactly once"
        )
    corpus = source_index if source_index is not None else _all_documents(results)
    documents = {str(row["document_id"]): row for row in corpus}
    source_ids = set(documents)
    reviews = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_review"]["records"], "reviews")
    }
    for index, row in enumerate(assessments):
        label = f"assessments[{index}]"
        prior_status = reviews[str(row["candidate_id"])]["prior_art"]["status"]
        if prior_status in {"human_intervention", "established_use"}:
            raise ProgramError(
                f"{label} cannot assess a candidate with disqualifying prior-art status {prior_status}"
            )
        components = _validate_exact_object(
            row["component_scores"], set(SCORE_COMPONENTS), f"{label}.component_scores"
        )
        for component in SCORE_COMPONENTS:
            score = _validate_exact_object(
                components[component],
                {"value", "reason", "source_ids"},
                f"{label}.component_scores.{component}",
            )
            _component_score(score["value"], f"{label}.component_scores.{component}.value")
            if not str(score["reason"]).strip():
                raise ProgramError(f"{label}.component_scores.{component}.reason must be non-empty")
            _references(
                score,
                "source_ids",
                source_ids,
                f"{label}.component_scores.{component}",
            )

        net = _validate_exact_object(
            row["net_assessment"], {"text", "source_ids"}, f"{label}.net_assessment"
        )
        if not str(net["text"]).strip():
            raise ProgramError(f"{label}.net_assessment.text must be non-empty")
        _references(net, "source_ids", source_ids, f"{label}.net_assessment")
        _validate_cited_entries(
            row["aliases"],
            label=f"{label}.aliases",
            text_field="name",
            source_ids=source_ids,
        )
        _validate_cited_entries(
            row["why_not"],
            label=f"{label}.why_not",
            text_field="finding",
            source_ids=source_ids,
        )
        _validate_source_integrity(
            row["source_integrity"],
            expected_uses=_assessment_source_uses(row),
            documents=documents,
            label=f"{label}.source_integrity",
        )

    expected_prior_reasons = {
        "established_use": "exact_disease_use",
        "human_intervention": "human_intervention",
    }
    for index, row in enumerate(exclusions):
        label = f"excluded_candidates[{index}]"
        if row["reason_code"] not in AUDIT_EXCLUSION_REASONS:
            raise ProgramError(
                f"{label}.reason_code must be one of {sorted(AUDIT_EXCLUSION_REASONS)}"
            )
        if not str(row["finding"]).strip():
            raise ProgramError(f"{label}.finding must be non-empty")
        exclusion_sources = _references(row, "source_ids", source_ids, label)
        _validate_source_integrity(
            row["source_integrity"],
            expected_uses={(source_id, "exclusion") for source_id in exclusion_sources},
            documents=documents,
            label=f"{label}.source_integrity",
        )
        prior_status = reviews[str(row["candidate_id"])]["prior_art"]["status"]
        expected_reason = expected_prior_reasons.get(prior_status)
        if expected_reason and row["reason_code"] != expected_reason:
            raise ProgramError(
                f"{label}.reason_code must be {expected_reason} for prior-art status {prior_status}"
            )


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
    expected_collections = set(STAGE_GUIDANCE[task]["collections"])
    actual_collections = set(result["records"])
    if actual_collections != expected_collections:
        raise ProgramError(
            "Result records must contain exactly these collections: "
            f"{sorted(expected_collections)}"
        )
    if "notes" in result and not isinstance(result["notes"], list):
        raise ProgramError("Result notes must be a list when supplied")
    if "documents" in expected_collections:
        _validate_research_document_content(result["records"])
    secrets = _secret_paths(result)
    if secrets:
        raise ProgramError(f"Credentials must never be persisted in results: {secrets}")
    validators = {
        "pathology_source_adjudication": lambda: _validate_source_adjudication(
            result["records"], prior
        ),
        "pathology_curation": lambda: _validate_curation(result["records"], prior),
        "pathology_node_research": lambda: _validate_pathology_item(
            result["records"], str(item_id), prior
        ),
        "candidate_seed_research": lambda: _validate_seed_item(
            result["records"], str(item_id), prior
        ),
        "candidate_identity": lambda: _validate_candidate_identity(
            result["records"], prior
        ),
        "candidate_evidence_review": lambda: _validate_review_item(
            result["records"], str(item_id), prior
        ),
        "candidate_audit": lambda: _validate_candidate_audit(
            result["records"], prior, packet["context"]["source_index"]
        ),
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
    if "documents" in STAGE_GUIDANCE[task]["collections"]:
        _validate_bibliographic_documents(run_root, result["records"])
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


def _final_score(row: Mapping[str, Any]) -> int:
    return sum(int(row["component_scores"][component]["value"]) for component in SCORE_COMPONENTS)


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    projected = {
        "rank": rank,
        "candidate_id": row["candidate_id"],
        "name": candidate["name"],
        "identity_status": candidate["identity"]["status"],
        **{
            component: row["component_scores"][component]["value"]
            for component in SCORE_COMPONENTS
        },
        "final_score": _final_score(row),
        "net_assessment": row["net_assessment"]["text"],
        "source_ids": ";".join(sorted(map(str, row["net_assessment"]["source_ids"]))),
    }
    return projected


def _ranked_rows(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = {row["candidate_id"]: row for row in _canonical_candidates(results)}
    assessments = _rows(results["candidate_audit"]["records"], "assessments")
    assessments.sort(key=lambda row: (-_final_score(row), str(row["candidate_id"])))
    rows: list[dict[str, Any]] = []
    rank = 0
    prior_score: int | None = None
    for assessment in assessments:
        score = _final_score(assessment)
        if score != prior_score:
            rank += 1
            prior_score = score
        rows.append(_project_ranked_row(rank, assessment, candidates))
    return rows, candidates


def _evidence_card_rows(
    ranked_rows: list[dict[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    assessments = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_audit"]["records"], "assessments")
    }
    cards: list[dict[str, Any]] = []
    for ranked_row in ranked_rows:
        candidate_id = str(ranked_row["candidate_id"])
        assessment = assessments[candidate_id]
        aliases = [
            {
                "name": str(alias["name"]).strip(),
                "source_ids": sorted(set(map(str, alias["source_ids"]))),
            }
            for alias in assessment["aliases"]
        ]
        why_not = [
            {
                "finding": str(finding["finding"]).strip(),
                "source_ids": sorted(set(map(str, finding["source_ids"]))),
            }
            for finding in assessment["why_not"]
        ]
        cards.append(
            {
                "drug_id": candidate_id,
                "aliases": aliases,
                "score": _final_score(assessment),
                "components": {
                    component: {
                        "value": assessment["component_scores"][component]["value"],
                        "reason": str(
                            assessment["component_scores"][component]["reason"]
                        ).strip(),
                        "source_ids": sorted(set(map(
                            str, assessment["component_scores"][component]["source_ids"]
                        ))),
                    }
                    for component in SCORE_COMPONENTS
                },
                "why": {
                    "text": str(assessment["net_assessment"]["text"]).strip(),
                    "source_ids": sorted(set(map(
                        str, assessment["net_assessment"]["source_ids"]
                    ))),
                },
                "why_not": why_not,
                "source_integrity": assessment["source_integrity"],
            }
        )
    return cards


def _single_line(value: Any) -> str:
    return " ".join(str(value).split())


def _reference_line(source_ids: Iterable[Any]) -> str:
    return "References: " + ", ".join(sorted(set(map(str, source_ids))))


def _source_verification_summary(checks: Iterable[Mapping[str, Any]]) -> str:
    counts = {verdict: 0 for verdict in _SOURCE_CHECK_VERDICTS}
    total = 0
    for check in checks:
        verdict = str(check["verdict"])
        counts[verdict] += 1
        total += 1
    details = ", ".join(
        f"{counts[verdict]} {verdict.replace('_', ' ')}"
        for verdict in ("supports", "partly_supports", "does_not_support", "contradicts")
        if counts[verdict]
    )
    return f"{total} cited use{'s' if total != 1 else ''} checked ({details})"


def _cards_bytes(cards: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for card in cards:
        lines.extend([f"## {_single_line(card['drug_id'])}", ""])
        if card["aliases"]:
            lines.append("Aliases:")
            lines.extend(
                f"- {_single_line(alias['name'])} "
                f"({_reference_line(alias['source_ids'])})"
                for alias in card["aliases"]
            )
            lines.append("")
        lines.extend([f"Score: {card['score']}/{MAX_SCORE}", ""])
        lines.extend(
            [
                "Source verification: "
                f"{_source_verification_summary(card['source_integrity']['checks'])}",
                "",
            ]
        )
        exceptions = [
            check
            for check in card["source_integrity"]["checks"]
            if check["verdict"] != "supports"
        ]
        if exceptions:
            lines.append("Citation-audit exceptions:")
            lines.extend(
                f"- {_single_line(check['source_id'])} in {_single_line(check['scope'])}: "
                f"{str(check['verdict']).replace('_', ' ')} — "
                f"{_single_line(check['finding'])}"
                for check in exceptions
            )
            lines.append("")
        for component in SCORE_COMPONENTS:
            score = card["components"][component]
            lines.extend(
                [
                    f"- {SCORE_LABELS[component]}: {score['value']}/20 — "
                    f"{_single_line(score['reason'])}",
                    f"  {_reference_line(score['source_ids'])}",
                ]
            )
        lines.append("")
        lines.extend(
            [
                "### Why",
                "",
                _single_line(card["why"]["text"]),
                "",
                _reference_line(card["why"]["source_ids"]),
                "",
            ]
        )
        if card["why_not"]:
            lines.extend(["### Why not", ""])
            for finding in card["why_not"]:
                lines.extend(
                    [
                        f"- {_single_line(finding['finding'])}",
                        f"  {_reference_line(finding['source_ids'])}",
                    ]
                )
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


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
                "origin_concept_ids": sorted(
                    map(str, candidate.get("origin_concept_ids", []))
                ),
            }
        )
    return output


def _excluded_candidate_rows(
    results: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exclusion in sorted(
        _rows(results["candidate_audit"]["records"], "excluded_candidates"),
        key=lambda row: str(row["candidate_id"]),
    ):
        candidate = candidates[str(exclusion["candidate_id"])]
        rows.append(
            {
                "candidate_id": exclusion["candidate_id"],
                "name": candidate["name"],
                "reason_code": exclusion["reason_code"],
                "finding": exclusion["finding"],
                "source_ids": sorted(set(map(str, exclusion["source_ids"]))),
                "graph_node_ids": sorted(set(map(str, candidate["graph_node_ids"]))),
                "pathology_source_ids": sorted(set(map(str, candidate["pathology_source_ids"]))),
                "mechanism_source_ids": sorted(set(map(str, candidate["mechanism_source_ids"]))),
                "source_integrity": exclusion["source_integrity"],
            }
        )
    return rows


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
    card_rows = _evidence_card_rows(rows, results)
    _write_once(outputs / "candidate_cards.md", _cards_bytes(card_rows))
    excluded_rows = _excluded_candidate_rows(results, candidates)
    _write_jsonl(outputs / "candidate_exclusions.jsonl", excluded_rows)
    documents = sorted(
        _canonicalize_documents(
            run_root, _all_documents(results), verify_titles=False
        ),
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
        _provenance_rows(rows, candidates, assertions),
    )
    gap_count = sum(len(results[stage].get("gaps", [])) for stage in STAGES)
    raw_candidate_count = len(
        _rows(results["candidate_seed_generation"]["records"], "candidates")
    )
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
        "Candidate nomination did not require a prior disease-drug literature association. "
        f"Audited candidates were ranked by an unweighted sum of "
        f"{len(SCORE_COMPONENTS)} 20-point components out of {MAX_SCORE}; "
        "exact-disease established use or human trials and other bounded decisive failures were "
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
    manifest = {
        "case_id": case["case_id"],
        "case_sha256": _sha256((run_root / "case.json").read_bytes()),
        "status": "complete",
        "candidate_count": len(rows),
        "excluded_candidate_count": len(
            _rows(results["candidate_audit"]["records"], "excluded_candidates")
        ),
        "raw_candidate_count": len(
            _rows(results["candidate_seed_generation"]["records"], "candidates")
        ),
        "deduplicated_candidate_count": len(candidates),
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
    "graph_context",
    "initialize",
    "next_action",
    "status",
    "submit",
]
