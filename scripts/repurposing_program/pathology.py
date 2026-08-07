"""Treatment-blind pathology curation, projection, and validation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from pathology_sources import SENTENCE_DECISIONS

from .contracts import (
    ASSERTION_EVIDENCE_TYPES,
    ASSERTION_POLARITIES,
    ASTA_CALL_ERROR_TYPES,
    ASTA_CALL_OUTCOMES,
    ASTA_CALL_PROFILES,
    ASTA_CALL_TOOLS,
    ASTA_NO_RESPONSE_SECONDS,
    ASTA_OPERATION_ID_PATTERN,
    ASTA_PAPER_ID_PATTERN,
    LANDSCAPE_PROPOSAL_TYPES,
    PATHOLOGY_PROFILE_LIST_FIELDS,
    _PATHOLOGY_FORBIDDEN_KEYS,
    _RESEARCH_CONTEXT_SECTIONS,
)
from .errors import ProgramError
from .evidence import _find, _merge_text, _rows
from .storage import _stable_id
from .validation import (
    _contract_rows,
    _ids,
    _references,
    _required,
    _validate_documents,
    _validate_exact_object,
)

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


def _compact_disease_context(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in _rows(records, "disease_context")
        if row["section"] in _RESEARCH_CONTEXT_SECTIONS
    ]


def _normalized_landscape_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split()).casefold()


def _landscape_source_nodes(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = results.get("pathology_landscape_scan")
    if not isinstance(result, Mapping) or not isinstance(result.get("records"), Mapping):
        return []
    nodes = []
    for proposal in _rows(result["records"], "landscape_proposals"):
        basis = {
            "provisional_type": _normalized_landscape_text(proposal["provisional_type"]),
            "label": _normalized_landscape_text(proposal["label"]),
            "claim": _normalized_landscape_text(proposal["claim"]),
        }
        nodes.append({
            "node_id": _stable_id("ASTA-NODE", basis),
            "label": str(proposal["label"]).strip(),
            "node_type": str(proposal["provisional_type"]),
            "description": str(proposal["claim"]).strip(),
            "index_comparison": str(proposal["index_comparison"]).strip(),
            "source_ids": sorted(set(map(str, proposal["source_ids"]))),
            "source_adapter": "Asta",
            "source_section": "pathology_landscape_scan",
        })
    return sorted(nodes, key=lambda row: str(row["node_id"]))


def _combined_source_records(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = results["pathology_sources"]["records"]
    return (
        [*_rows(source, "source_nodes"), *_landscape_source_nodes(results)],
        _rows(source, "source_edges"),
    )


def _curation_concepts(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = results.get("pathology_curation")
    if not isinstance(result, Mapping) or not isinstance(result.get("records"), Mapping):
        raise ProgramError("Pathology curation result is missing")
    return _contract_rows(result["records"], "concepts", "concept_id")


def _research_concepts(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        (
            row
            for row in _curation_concepts(results)
            if row.get("disposition") == "research"
        ),
        key=lambda row: str(row["concept_id"]),
    )


def _canonical_source_records(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project raw source records through the accepted run-local concept partition."""
    raw_nodes, raw_edges = _combined_source_records(results)
    raw_by_id = {str(row["node_id"]): row for row in raw_nodes}
    concepts = _curation_concepts(results)

    node_map: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if raw.get("node_type") != "disease_anchor":
            continue
        node_id = str(raw["node_id"])
        node_map[node_id] = node_id
        nodes.append(
            {
                "node_id": node_id,
                "label": str(raw["label"]),
                "node_type": "disease_anchor",
                "source_ids": sorted(set(map(str, raw["source_ids"]))),
                "aliases": [],
                "member_node_ids": [node_id],
                "disposition": "context_only",
            }
        )

    for concept in concepts:
        if concept["disposition"] == "exclude":
            continue
        concept_id = str(concept["concept_id"])
        members = sorted(set(map(str, concept["member_node_ids"])))
        for member in members:
            node_map[member] = concept_id
        source_ids = sorted(
            {
                str(source_id)
                for member in members
                for source_id in raw_by_id[member]["source_ids"]
            }
        )
        aliases = sorted(
            {
                str(value).strip()
                for value in [
                    *concept["aliases"],
                    *(raw_by_id[member].get("label", "") for member in members),
                ]
                if str(value).strip()
                and str(value).strip().casefold()
                != str(concept["preferred_label"]).strip().casefold()
            },
            key=lambda value: (value.casefold(), value),
        )
        nodes.append(
            {
                "node_id": concept_id,
                "label": str(concept["preferred_label"]),
                "node_type": str(concept["concept_type"]),
                "source_ids": source_ids,
                "aliases": aliases,
                "member_node_ids": members,
                "disposition": str(concept["disposition"]),
            }
        )

    grouped_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in raw_edges:
        subject = node_map.get(str(edge["subject_id"]))
        object_id = node_map.get(str(edge["object_id"]))
        if not subject or not object_id or subject == object_id:
            continue
        relation = str(edge["relation"])
        key = (subject, relation, object_id)
        current = grouped_edges.setdefault(
            key,
            {
                "edge_id": _stable_id(
                    "CURATED-EDGE",
                    {"subject_id": subject, "relation": relation, "object_id": object_id},
                ),
                "subject_id": subject,
                "relation": relation,
                "object_id": object_id,
                "evidence_summary": "",
                "source_ids": [],
                "original_edge_ids": [],
            },
        )
        current["evidence_summary"] = _merge_text(
            current["evidence_summary"], edge["evidence_summary"]
        )
        current["source_ids"] = sorted(
            {*map(str, current["source_ids"]), *map(str, edge["source_ids"])}
        )
        current["original_edge_ids"] = sorted(
            {*map(str, current["original_edge_ids"]), str(edge["edge_id"])}
        )

    node_by_id = {str(row["node_id"]): row for row in nodes}
    for concept in concepts:
        if concept["disposition"] != "context_only":
            continue
        subject = str(concept["concept_id"])
        for object_id in map(str, concept["related_concept_ids"]):
            relation = "contextualizes"
            key = (subject, relation, object_id)
            current = grouped_edges.setdefault(
                key,
                {
                    "edge_id": _stable_id(
                        "CURATED-EDGE",
                        {
                            "subject_id": subject,
                            "relation": relation,
                            "object_id": object_id,
                        },
                    ),
                    "subject_id": subject,
                    "relation": relation,
                    "object_id": object_id,
                    "evidence_summary": "",
                    "source_ids": [],
                    "original_edge_ids": [],
                },
            )
            current["evidence_summary"] = _merge_text(
                current["evidence_summary"], concept["reason"]
            )
            current["source_ids"] = sorted(
                {
                    *map(str, current["source_ids"]),
                    *map(str, node_by_id[subject]["source_ids"]),
                }
            )

    return (
        sorted(nodes, key=lambda row: str(row["node_id"])),
        sorted(grouped_edges.values(), key=lambda row: str(row["edge_id"])),
    )


def _validate_source_screening(result: Mapping[str, Any]) -> None:
    if (
        result.get("stage") != "pathology_source_screening"
        or result.get("status") != "complete"
    ):
        raise ProgramError("Pathology source screening returned an invalid stage or status")
    records = result.get("records")
    if not isinstance(records, dict) or set(records) != {"flagged_sentences"}:
        raise ProgramError("Pathology source screening requires only flagged_sentences")
    rows = _contract_rows(records, "flagged_sentences", "sentence_id")
    allowed_signals = {"named_intervention", "treatment_event", "treatment_language"}
    for index, row in enumerate(rows):
        label = f"flagged_sentences[{index}]"
        sentence = str(row["sentence"]).strip()
        if not sentence or row["sentence_id"] != _stable_id(
            "DISMECH-SENTENCE", sentence
        ):
            raise ProgramError(f"{label} does not have a stable sentence identity")
        signals = row["signals"]
        if (
            not isinstance(signals, list)
            or not signals
            or any(signal not in allowed_signals for signal in signals)
            or len(signals) != len(set(signals))
        ):
            raise ProgramError(f"{label}.signals contains invalid or duplicate values")
        paths = row["paths"]
        if (
            not isinstance(paths, list)
            or not paths
            or any(
                not isinstance(path, str) or not path.startswith("$")
                for path in paths
            )
            or len(paths) != len(set(paths))
        ):
            raise ProgramError(f"{label}.paths must contain unique source paths")


def _validate_source_adjudication(
    records: Mapping[str, Any], prior: Mapping[str, Mapping[str, Any]]
) -> None:
    decisions = _contract_rows(records, "sentence_decisions", "sentence_id")
    expected = {
        str(row["sentence_id"])
        for row in _rows(
            prior["pathology_source_screening"]["records"],
            "flagged_sentences",
        )
    }
    actual = {str(row["sentence_id"]) for row in decisions}
    if actual != expected:
        raise ProgramError(
            "Sentence adjudication must partition every flagged sentence exactly once; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )
    for index, row in enumerate(decisions):
        label = f"sentence_decisions[{index}]"
        if row["decision"] not in SENTENCE_DECISIONS:
            raise ProgramError(
                f"{label}.decision must be one of {sorted(SENTENCE_DECISIONS)}"
            )
        if not str(row["reason"]).strip():
            raise ProgramError(f"{label}.reason must be non-empty")


def _validate_source_result(result: Mapping[str, Any]) -> None:
    records = result.get("records")
    if not isinstance(records, dict):
        raise ProgramError("Pathology source adapter did not return records")
    documents = _validate_documents(records)
    nodes = _contract_rows(records, "source_nodes", "node_id")
    edges = _contract_rows(records, "source_edges", "edge_id")
    contexts = _contract_rows(records, "disease_context", "context_id")
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
    for index, row in enumerate(contexts):
        _references(row, "source_ids", document_ids, f"disease_context[{index}]")
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields reached the pathology source result: {forbidden}")


_LANDSCAPE_TREATMENT_PATTERN = re.compile(
    r"\b(?:candidate|compound|dos(?:e|ed|ing)|drug|medication|repurpos\w*|"
    r"therap(?:y|ies|eutic\w*)|treat(?:ment|ed|ing|s)?)\b",
    re.IGNORECASE,
)
def _validate_asta_call_receipts(
    records: Mapping[str, Any],
    gaps: list[Any],
    *,
    has_documents: bool,
    has_proposals: bool,
) -> None:
    receipts = _contract_rows(records, "asta_call_receipts")
    if not receipts:
        raise ProgramError(
            "pathology_landscape_scan requires non-secret asta_call_receipts"
        )

    operations: dict[str, list[dict[str, Any]]] = {}
    for index, receipt in enumerate(receipts):
        label = f"asta_call_receipts[{index}]"
        operation_id = receipt["operation_id"]
        if not isinstance(operation_id, str) or not re.fullmatch(
            ASTA_OPERATION_ID_PATTERN, operation_id
        ):
            raise ProgramError(
                f"{label}.operation_id must match ASTA-OP-<letters, digits, _ or ->"
            )
        tool = receipt["tool"]
        if tool not in ASTA_CALL_TOOLS:
            raise ProgramError(f"{label}.tool must be one of {sorted(ASTA_CALL_TOOLS)}")

        paper_id = receipt["paper_id"]
        if tool == "search_papers_by_relevance":
            if paper_id is not None:
                raise ProgramError(f"{label}.paper_id must be null for relevance search")
        elif not isinstance(paper_id, str) or not re.fullmatch(
            ASTA_PAPER_ID_PATTERN, paper_id
        ):
            raise ProgramError(
                f"{label}.paper_id must use a documented Asta paper identifier"
            )

        attempt = receipt["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt not in {1, 2}:
            raise ProgramError(f"{label}.attempt must be 1 or 2")
        request_profile = receipt["request_profile"]
        if request_profile not in ASTA_CALL_PROFILES:
            raise ProgramError(
                f"{label}.request_profile must be one of {sorted(ASTA_CALL_PROFILES)}"
            )
        expected_profile = "standard" if attempt == 1 else "minimal"
        if request_profile != expected_profile:
            raise ProgramError(
                f"{label}.request_profile must be {expected_profile} for attempt {attempt}"
            )

        outcome = receipt["outcome"]
        if outcome not in ASTA_CALL_OUTCOMES:
            raise ProgramError(
                f"{label}.outcome must be one of {sorted(ASTA_CALL_OUTCOMES)}"
            )
        elapsed = receipt["elapsed_seconds"]
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or elapsed < 0
        ):
            raise ProgramError(f"{label}.elapsed_seconds must be a non-negative number")
        result_count = receipt["result_count"]
        error_type = receipt["error_type"]
        if outcome == "completed":
            if (
                isinstance(result_count, bool)
                or not isinstance(result_count, int)
                or result_count < 0
            ):
                raise ProgramError(
                    f"{label}.result_count must be a non-negative integer when completed"
                )
            if error_type is not None:
                raise ProgramError(f"{label}.error_type must be null when completed")
        else:
            if result_count is not None:
                raise ProgramError(f"{label}.result_count must be null after a failed call")
            if error_type not in ASTA_CALL_ERROR_TYPES:
                raise ProgramError(
                    f"{label}.error_type must be one of {sorted(ASTA_CALL_ERROR_TYPES)}"
                )
            if error_type in {"authentication", "invalid_request"}:
                raise ProgramError(
                    f"{label} records a blocking {error_type} defect, not a scientific outage"
                )
            if outcome == "no_response" and (
                error_type != "timeout" or elapsed < ASTA_NO_RESPONSE_SECONDS
            ):
                raise ProgramError(
                    f"{label}.no_response requires error_type=timeout and at least "
                    f"{ASTA_NO_RESPONSE_SECONDS} elapsed seconds"
                )
        operations.setdefault(operation_id, []).append(receipt)

    terminal_failures: list[dict[str, Any]] = []
    operation_summaries: list[tuple[str, str | None, dict[str, Any]]] = []
    for operation_id, attempts in operations.items():
        ordered = sorted(attempts, key=lambda row: row["attempt"])
        attempt_numbers = [row["attempt"] for row in ordered]
        if len(attempt_numbers) != len(set(attempt_numbers)):
            raise ProgramError(f"{operation_id} contains duplicate attempt receipts")
        if attempt_numbers not in ([1], [1, 2]):
            raise ProgramError(f"{operation_id} attempts must be [1] or [1, 2]")
        tools = {str(row["tool"]) for row in ordered}
        paper_ids = {row["paper_id"] for row in ordered}
        if len(tools) != 1 or len(paper_ids) != 1:
            raise ProgramError(
                f"{operation_id} must retain one tool and paper_id across its retry"
            )
        first = ordered[0]
        if first["outcome"] == "completed" and len(ordered) != 1:
            raise ProgramError(f"{operation_id} must not retry a completed call")
        if first["outcome"] != "completed" and attempt_numbers != [1, 2]:
            raise ProgramError(
                f"{operation_id} must retry a failed call exactly once with a minimal payload"
            )
        terminal = ordered[-1]
        if terminal["outcome"] != "completed":
            terminal_failures.append(terminal)
        operation_summaries.append((first["tool"], first["paper_id"], terminal))

    search_operations = [
        terminal
        for tool, _paper_id, terminal in operation_summaries
        if tool == "search_papers_by_relevance"
    ]
    if not search_operations:
        raise ProgramError("Asta receipts require at least one relevance-search operation")
    completed_searches = [
        row for row in search_operations if row["outcome"] == "completed"
    ]
    snippet_operations = [
        (paper_id, terminal)
        for tool, paper_id, terminal in operation_summaries
        if tool == "snippet_search"
    ]
    snippet_targets = {paper_id for paper_id, _terminal in snippet_operations}
    failed_citation_targets = {
        paper_id
        for tool, paper_id, terminal in operation_summaries
        if tool == "get_citations" and terminal["outcome"] != "completed"
    }
    missing_snippets = failed_citation_targets - snippet_targets
    if missing_snippets:
        raise ProgramError(
            "A terminal get_citations failure must still be followed by snippet_search on the "
            f"original paper; missing={sorted(missing_snippets)}"
        )
    citation_targets = {
        paper_id
        for tool, paper_id, _terminal in operation_summaries
        if tool == "get_citations"
    }
    unevaluated_citation_targets = citation_targets - snippet_targets
    if unevaluated_citation_targets:
        raise ProgramError(
            "Every paper passed to get_citations must also receive paper-restricted "
            f"snippet_search; missing={sorted(unevaluated_citation_targets)}"
        )
    if not completed_searches:
        if has_documents or has_proposals:
            raise ProgramError(
                "An unavailable relevance search cannot produce landscape documents or proposals"
            )
    elif any(row["result_count"] > 0 for row in completed_searches):
        tools_attempted = {tool for tool, _paper_id, _terminal in operation_summaries}
        missing = {"get_citations", "snippet_search"} - tools_attempted
        if missing:
            raise ProgramError(
                "A positive Asta relevance search requires citation and snippet operations; "
                f"missing={sorted(missing)}"
            )

    completed_snippets = [
        row
        for _paper_id, row in snippet_operations
        if row["outcome"] == "completed" and row["result_count"] > 0
    ]
    if (has_documents or has_proposals) and not completed_snippets:
        raise ProgramError(
            "Landscape documents and proposals require a completed paper-restricted snippet call"
        )

    if terminal_failures and not any(
        isinstance(gap, str) and gap.strip() for gap in gaps
    ):
        raise ProgramError("Terminal Asta call failures require an explicit gap")


def _validate_landscape_scan(records: Mapping[str, Any], gaps: list[Any]) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    proposals = _contract_rows(records, "landscape_proposals")
    _validate_asta_call_receipts(
        records,
        gaps,
        has_documents=bool(documents),
        has_proposals=bool(proposals),
    )
    document_ids = {str(row["document_id"]) for row in documents}
    cited_ids: set[str] = set()
    normalized_proposals: set[tuple[str, str, str]] = set()
    for index, proposal in enumerate(proposals):
        label = f"landscape_proposals[{index}]"
        _required(
            proposal,
            ("label", "provisional_type", "claim", "index_comparison"),
            label,
        )
        for field in ("label", "provisional_type", "claim", "index_comparison"):
            if not isinstance(proposal[field], str) or not proposal[field].strip():
                raise ProgramError(f"{label}.{field} must be non-empty text")
        if proposal["provisional_type"] not in LANDSCAPE_PROPOSAL_TYPES:
            raise ProgramError(
                f"{label}.provisional_type must be one of "
                f"{sorted(LANDSCAPE_PROPOSAL_TYPES)}"
            )
        normalized = tuple(
            _normalized_landscape_text(proposal[field])
            for field in ("provisional_type", "label", "claim")
        )
        if normalized in normalized_proposals:
            raise ProgramError(
                "landscape_proposals must not contain duplicate normalized proposals"
            )
        normalized_proposals.add(normalized)
        proposal_sources = _references(proposal, "source_ids", document_ids, label)
        if len(proposal_sources) != len(proposal["source_ids"]):
            raise ProgramError(f"{label}.source_ids must be unique")
        cited_ids.update(proposal_sources)
        proposal_text = " ".join(
            str(proposal[field]) for field in ("label", "claim", "index_comparison")
        )
        if _LANDSCAPE_TREATMENT_PATTERN.search(proposal_text):
            raise ProgramError(
                f"{label} is treatment-framed rather than a treatment-blind pathology claim"
            )
    if cited_ids != document_ids:
        raise ProgramError(
            "Landscape documents must be cited by an actual proposal; "
            f"uncited={sorted(document_ids - cited_ids)}"
        )
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(
            f"Treatment fields are forbidden in the pathology landscape scan: {forbidden}"
        )


def _validate_curation(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    concepts = _contract_rows(records, "concepts", "concept_id")
    source_nodes, _ = _combined_source_records(results)
    expected = {
        str(row["node_id"])
        for row in source_nodes
        if row.get("node_type") != "disease_anchor"
    }
    assigned: list[str] = []
    retained_labels: dict[tuple[str, str], str] = {}
    for index, concept in enumerate(concepts):
        label = f"concepts[{index}]"
        _required(concept, ("preferred_label", "concept_type", "disposition", "reason"), label)
        members = concept.get("member_node_ids")
        aliases = concept.get("aliases")
        related = concept.get("related_concept_ids")
        if not isinstance(members, list) or not members:
            raise ProgramError(f"{label}.member_node_ids must be a non-empty list")
        if not isinstance(aliases, list) or any(
            not isinstance(value, str) or not value.strip() for value in aliases
        ):
            raise ProgramError(f"{label}.aliases must be a list of non-empty strings")
        if not isinstance(related, list) or any(
            not isinstance(value, str) or not value.strip() for value in related
        ):
            raise ProgramError(
                f"{label}.related_concept_ids must be a list of non-empty strings"
            )
        if len(related) != len(set(related)):
            raise ProgramError(f"{label}.related_concept_ids contains duplicates")
        member_ids = list(map(str, members))
        if len(member_ids) != len(set(member_ids)):
            raise ProgramError(f"{label}.member_node_ids contains duplicates")
        if str(concept["concept_id"]) not in member_ids:
            raise ProgramError(f"{label}.concept_id must be one of its member_node_ids")
        if concept["concept_type"] not in {"driver", "mechanism", "phenotype", "context"}:
            raise ProgramError(
                f"{label}.concept_type must be driver, mechanism, phenotype, or context"
            )
        if concept["disposition"] not in {"research", "context_only", "exclude"}:
            raise ProgramError(
                f"{label}.disposition must be research, context_only, or exclude"
            )
        if concept["disposition"] == "research" and concept["concept_type"] == "context":
            raise ProgramError(f"{label} cannot research a context-only concept type")
        if concept["disposition"] != "exclude":
            key = (
                str(concept["concept_type"]),
                str(concept["preferred_label"]).strip().casefold(),
            )
            previous = retained_labels.get(key)
            if previous:
                raise ProgramError(
                    f"{label} duplicates the retained type and label in {previous}; "
                    "merge equivalent source claims or give distinct claims distinct labels"
                )
            retained_labels[key] = label
        assigned.extend(member_ids)
    if len(assigned) != len(set(assigned)) or set(assigned) != expected:
        missing = sorted(expected - set(assigned))
        unknown = sorted(set(assigned) - expected)
        raise ProgramError(
            "Pathology concepts must partition every supplied non-anchor node exactly once; "
            f"missing={missing}, unknown={unknown}"
        )
    research_ids = {
        str(concept["concept_id"])
        for concept in concepts
        if concept["disposition"] == "research"
    }
    for index, concept in enumerate(concepts):
        related = set(map(str, concept["related_concept_ids"]))
        if concept["disposition"] == "context_only":
            if not related or not related <= research_ids:
                raise ProgramError(
                    f"concepts[{index}].related_concept_ids must contain only retained research concepts"
                )
        elif related:
            raise ProgramError(
                f"concepts[{index}].related_concept_ids must be empty unless context_only"
            )
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields are forbidden in pathology curation: {forbidden}")


def _validate_pathology_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    upstream_documents = [
        *_rows(results["pathology_sources"]["records"], "documents"),
        *(
            _rows(results["pathology_landscape_scan"]["records"], "documents")
            if "pathology_landscape_scan" in results
            else []
        ),
    ]
    upstream_document_ids = {
        str(row.get("document_id", "")).strip() for row in upstream_documents
    }
    if "" in upstream_document_ids:
        raise ProgramError("upstream documents.document_id is required")
    if not {str(row["document_id"]) for row in documents} - upstream_document_ids:
        raise ProgramError("pathology node research must retain newly researched evidence")
    profiles = _contract_rows(records, "profiles", "node_id")
    assertions = _contract_rows(records, "assertions")
    if len(profiles) != 1 or str(profiles[0]["node_id"]) != item_id:
        raise ProgramError("pathology node research must return exactly one profile for item_id")
    source_nodes, _ = _canonical_source_records(results)
    node = _find(source_nodes, "node_id", item_id)
    if node.get("disposition") != "research":
        raise ProgramError("pathology node research item must be a curated research concept")
    if profiles[0]["node_type"] != node["node_type"]:
        raise ProgramError("profile.node_type must match the source-derived node")
    profile = profiles[0]
    _required(
        profile,
        (
            "summary", "normal_state", "pathological_state", "desired_biological_state",
            "phenotype_objective", "causal_role", "uncertainty",
        ),
        "profiles[0]",
    )
    for field in ("desired_biological_state", "phenotype_objective"):
        if not isinstance(profile[field], str) or not profile[field].strip():
            raise ProgramError(f"profiles[0].{field} must be non-empty text")
    for field in PATHOLOGY_PROFILE_LIST_FIELDS:
        if not isinstance(profile[field], list):
            raise ProgramError(f"profiles[0].{field} must be a list")
    secondary_states = profile["secondary_desired_states"]
    if any(not isinstance(value, str) or not value.strip() for value in secondary_states):
        raise ProgramError(
            "profiles[0].secondary_desired_states must contain only non-empty strings"
        )
    normalized_secondary_states = [
        " ".join(value.split()).casefold() for value in secondary_states
    ]
    if len(normalized_secondary_states) != len(set(normalized_secondary_states)):
        raise ProgramError("profiles[0].secondary_desired_states must be unique")
    normalized_primary_state = " ".join(
        profile["desired_biological_state"].split()
    ).casefold()
    if normalized_primary_state in normalized_secondary_states:
        raise ProgramError(
            "profiles[0].secondary_desired_states must not repeat desired_biological_state"
        )
    node_ids = {str(row["node_id"]) for row in source_nodes}
    source_ids = {
        *upstream_document_ids,
        *(str(row["document_id"]) for row in documents),
    }
    _references(profiles[0], "source_ids", source_ids, "profiles[0]")
    observations = profile["established_pathology_observations"]
    if not isinstance(observations, list) or any(
        not isinstance(row, dict) for row in observations
    ):
        raise ProgramError(
            "profiles[0].established_pathology_observations must be a list of objects"
        )
    for index, observation in enumerate(observations):
        label = f"profiles[0].established_pathology_observations[{index}]"
        if set(observation) != {"observation", "source_ids"}:
            raise ProgramError(f"{label} must contain only observation and source_ids")
        _required(observation, ("observation",), label)
        _references(observation, "source_ids", source_ids, label)
    for index, row in enumerate(assertions):
        label = f"assertions[{index}]"
        _required(
            row,
            ("subject_id", "relation", "object_id"),
            label,
        )
        unknown_endpoints = sorted(
            {str(row["subject_id"]), str(row["object_id"])} - node_ids
        )
        if unknown_endpoints:
            raise ProgramError(
                f"{label} contains endpoint IDs not listed in "
                f"context.allowed_assertion_nodes: {unknown_endpoints}"
            )
        contexts = row["evidence_context"]
        if not isinstance(contexts, list) or not contexts:
            raise ProgramError(f"{label}.evidence_context must be a non-empty list")
        seen_contexts: set[tuple[str, str, str, str, str]] = set()
        for context_index, context in enumerate(contexts):
            context_label = f"{label}.evidence_context[{context_index}]"
            context = _validate_exact_object(
                context,
                {"source_ids", "evidence_type", "model", "stage", "polarity", "summary"},
                context_label,
            )
            _required(
                context,
                ("evidence_type", "model", "stage", "polarity", "summary"),
                context_label,
            )
            for field in ("evidence_type", "model", "stage", "polarity", "summary"):
                if not isinstance(context[field], str) or not context[field].strip():
                    raise ProgramError(f"{context_label}.{field} must be non-empty text")
            if context["evidence_type"] not in ASSERTION_EVIDENCE_TYPES:
                raise ProgramError(
                    f"{context_label}.evidence_type must be one of "
                    f"{sorted(ASSERTION_EVIDENCE_TYPES)}"
                )
            if context["polarity"] not in ASSERTION_POLARITIES:
                raise ProgramError(
                    f"{context_label}.polarity must be one of {sorted(ASSERTION_POLARITIES)}"
                )
            context_key = tuple(
                " ".join(str(context[field]).split()).casefold()
                for field in ("evidence_type", "model", "stage", "polarity", "summary")
            )
            if context_key in seen_contexts:
                raise ProgramError(f"{label}.evidence_context entries must be unique")
            seen_contexts.add(context_key)
            context_sources = _references(context, "source_ids", source_ids, context_label)
            if len(context_sources) != len(context["source_ids"]):
                raise ProgramError(f"{context_label}.source_ids must be unique")
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields are forbidden in pathology research: {forbidden}")
