"""Candidate seed and evidence-dossier partition and validation rules."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import PRIOR_ART_STATUSES, _COMPARATORS
from .errors import ProgramError
from .evidence import _all_documents, _cited_ids, _find, _rows
from .graph import _graph_support_ids
from .identity import _canonical_candidates
from .pathology import _research_concepts
from .validation import (
    _contract_rows,
    _ids,
    _references,
    _required,
    _validate_cited_entries,
    _validate_documents,
    _validate_exact_object,
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
    assertions_by_id = {
        str(row["assertion_id"]): row for row in _rows(graph, "assertions")
    }
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
            ("candidate_id", "name", "mechanism_hypothesis", "graph_rationale"),
            label,
        )
        if not isinstance(row["graph_rationale"], str) or not row["graph_rationale"].strip():
            raise ProgramError(f"{label}.graph_rationale must be non-empty text")
        if str(row["name"]).strip().casefold() in _COMPARATORS:
            raise ProgramError(f"{label} is a comparator, not a drug candidate")
        graph_refs = _references(row, "graph_node_ids", allowed_node_ids, label)
        if len(graph_refs) != len(row["graph_node_ids"]):
            raise ProgramError(f"{label}.graph_node_ids must be unique")
        if concept_id not in graph_refs:
            raise ProgramError(f"{label}.graph_node_ids must include the focal item concept")
        assertion_values = row["assertion_ids"]
        if not isinstance(assertion_values, list) or any(
            not isinstance(value, str) or not value.strip() for value in assertion_values
        ):
            raise ProgramError(f"{label}.assertion_ids must be a list of non-empty IDs")
        assertion_refs = set(map(str, assertion_values))
        if len(assertion_refs) != len(assertion_values):
            raise ProgramError(f"{label}.assertion_ids must be unique")
        unknown_assertions = assertion_refs - set(assertions_by_id)
        if unknown_assertions:
            raise ProgramError(
                f"{label}.assertion_ids contains unknown IDs: {sorted(unknown_assertions)}"
            )
        missing_assertion_nodes = sorted({
            node_id
            for assertion_id in assertion_refs
            for node_id in map(
                str,
                (
                    assertions_by_id[assertion_id]["subject_id"],
                    assertions_by_id[assertion_id]["object_id"],
                ),
            )
            if node_id in allowed_node_ids and node_id not in graph_refs
        })
        if missing_assertion_nodes:
            raise ProgramError(
                f"{label}.graph_node_ids must include selected assertion nodes: "
                f"{missing_assertion_nodes}"
            )
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


def _validate_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not str(item).strip() for item in value):
        raise ProgramError(f"{label} must be a list of non-empty strings")


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
