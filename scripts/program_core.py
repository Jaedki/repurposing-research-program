#!/usr/bin/env python3
"""Small, content-addressed controller for a linear repurposing programme."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from pathology_sources import (
    SourceError,
    fetch_pathology_sources,
    screen_pathology_sources,
)

from repurposing_program.audit import (
    _accepted_ids,
    _assessment_source_uses,
    _component_score,
    _validate_candidate_audit,
    _validate_source_integrity,
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
from repurposing_program.candidates import (
    _review_batches,
    _validate_cited_entries,
    _validate_review_item,
    _validate_seed_item,
    _validate_string_list,
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
from repurposing_program.identity import (
    _candidate_queries,
    _canonical_candidates,
    _empty_identity_result,
    _exact_identity_groups,
    _identity_candidate_options,
    _identity_queue,
    _merge_candidate_rows,
    _post_unichem,
    _query_key,
    _resolve_seed_identities,
    _unichem_request,
    _unichem_requests,
    _validate_candidate_identity,
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
    _validate_exact_object,
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
