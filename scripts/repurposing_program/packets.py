"""Task-context projection, worker contracts, and content-packet persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .bibliography import _canonicalize_documents
from .candidates import _review_batches
from .contracts import (
    AUDIT_EXCLUSION_POLICY,
    FIELD_RULES,
    ROW_SCHEMAS,
    SCORE_RUBRIC,
    STAGE_GUIDANCE,
)
from .errors import ProgramError
from .evidence import (
    _all_documents,
    _cited_ids,
    _find,
    _rows,
    _source_index,
)
from .graph import _graph_index, _graph_node_context
from .identity import (
    _canonical_candidates,
    _identity_candidate_options,
    _identity_queue,
)
from .pathology import (
    _canonical_source_records,
    _compact_disease_context,
    _research_concepts,
)
from .storage import _packet_path, _result_path, _sha256, _stable_id, _write_json
from .validation import _secret_paths


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
                for key in (
                    "node_id",
                    "label",
                    "node_type",
                    "description",
                    "source_ids",
                    "source_section",
                )
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
        orchestrator = Path(__file__).resolve().parents[1] / "orchestrate_program.py"
        return {
            "graph_snapshot_id": graph_result["snapshot_id"],
            "focal_context": _graph_node_context(graph, str(item_id)),
            "graph_index": _graph_index(graph),
            "context_lookup": {
                "argv": [
                    "python",
                    str(orchestrator.resolve()),
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
        raise ProgramError(
            "Worker packet case must contain only case_id, disease, gene, and mondo"
        )
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
        "case": {key: case.get(key) for key in _PACKET_CASE_FIELDS},
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
