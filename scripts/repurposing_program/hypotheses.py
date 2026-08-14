"""Global pathology-question research and hypothesis-connection validation."""

from __future__ import annotations

from typing import Any, Mapping

from .bibliography import _normalized_publication_id
from .errors import ProgramError
from .evidence import _find, _merge_documents, _normalized_title, _rows, _source_index
from .graph import _graph_index
from .validation import _contract_rows, _required, _validate_documents


_ANSWER_STATUSES = frozenset({"answered", "partially_answered", "unresolved"})
_RESEARCH_DISPOSITIONS = frozenset({
    "corpus_sufficient", "literature_delta_found", "still_unresolved",
})
_CLAIM_EPISTEMIC_STATUSES = frozenset({
    "direct_observation", "synthesis", "inference",
})
_CLAIM_DELTA_TYPES = frozenset({
    "baseline", "extends", "contradicts", "discriminates", "transfers",
    "defines_decisive_test",
})
_CLAIM_FIELDS = frozenset({
    "claim_id", "claim", "epistemic_status", "delta_type", "evidence_scope",
    "assumptions", "source_ids",
})


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProgramError(f"{label} must be non-empty text")
    return value.strip()


def _string_list(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        requirement = "a non-empty list" if not allow_empty else "a list"
        raise ProgramError(f"{label} must be {requirement} of non-empty strings")
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ProgramError(f"{label}[{index}] must be a non-empty string")
        normalized.append(item.strip())
    if len(normalized) != len(set(normalized)):
        raise ProgramError(f"{label} values must be unique")
    return normalized


def _id_list(value: Any, label: str, allowed: set[str], *, allow_empty: bool = False) -> set[str]:
    values = set(_string_list(value, label, allow_empty=allow_empty))
    unknown = values - allowed
    if unknown:
        raise ProgramError(f"{label} contains unknown IDs: {sorted(unknown)}")
    return values


def _graph_node_ids(results: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        str(row["node_id"])
        for row in _graph_index(results["evidence_graph"]["records"])
    }


def _graph_source_ids(results: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        str(row["document_id"])
        for row in _rows(results["evidence_graph"]["records"], "documents")
    }


def _graph_documents(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _rows(results["evidence_graph"]["records"], "documents")


def _publication_identity_tokens(document: Mapping[str, Any]) -> set[str]:
    values = [
        document.get("document_id"),
        document.get("canonical_publication_id"),
        *(
            document.get("identifier_aliases", [])
            if isinstance(document.get("identifier_aliases"), list)
            else []
        ),
    ]
    tokens: set[str] = set()
    for value in values:
        normalized = _normalized_publication_id(value)
        if normalized is not None:
            tokens.add(normalized)
            continue
        text = str(value or "").strip()
        if text.upper().startswith("S2:"):
            tokens.add(text.upper())
    return tokens


def _reused_graph_publications(
    documents: list[dict[str, Any]], graph_documents: list[dict[str, Any]]
) -> set[str]:
    graph_tokens = set().union(*(
        _publication_identity_tokens(document) for document in graph_documents
    )) if graph_documents else set()
    graph_titles = {
        _normalized_title(document.get("title"))
        for document in graph_documents
        if _publication_identity_tokens(document) and _normalized_title(document.get("title"))
    }
    reused: set[str] = set()
    for document in documents:
        tokens = _publication_identity_tokens(document)
        if not tokens:
            continue
        title = _normalized_title(document.get("title"))
        if tokens & graph_tokens or (title and title in graph_titles):
            reused.add(str(document["document_id"]))
    return reused


def _open_questions(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _rows(results["pathology_open_questions"]["records"], "open_questions")


def _question_answers(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _rows(results["pathology_question_research"]["records"], "question_answers")


def _validate_open_questions(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    questions = _contract_rows(records, "open_questions", "question_id")
    if not 1 <= len(questions) <= 10:
        raise ProgramError("open_questions must contain between one and ten questions")
    allowed_nodes = _graph_node_ids(results)
    seen_questions: set[str] = set()
    seen_unresolved_bases: set[str] = set()
    for index, row in enumerate(questions):
        label = f"open_questions[{index}]"
        _required(
            row,
            (
                "question_id", "question", "rationale", "unresolved_basis",
                "discriminating_evidence",
            ),
            label,
        )
        _text(row["question_id"], f"{label}.question_id")
        question = _text(row["question"], f"{label}.question")
        _text(row["rationale"], f"{label}.rationale")
        unresolved_basis = _text(row["unresolved_basis"], f"{label}.unresolved_basis")
        _text(row["discriminating_evidence"], f"{label}.discriminating_evidence")
        _id_list(row["node_ids"], f"{label}.node_ids", allowed_nodes)
        normalized = " ".join(question.casefold().split())
        if normalized in seen_questions:
            raise ProgramError("open_questions contains repeated question text")
        seen_questions.add(normalized)
        normalized_basis = " ".join(unresolved_basis.casefold().split())
        if normalized_basis in seen_unresolved_bases:
            raise ProgramError("open_questions contains repeated unresolved_basis text")
        seen_unresolved_bases.add(normalized_basis)


def _validate_question_research(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    answers = _contract_rows(records, "question_answers", "question_id")
    questions = {str(row["question_id"]): row for row in _open_questions(results)}
    if {str(row["question_id"]) for row in answers} != set(questions):
        raise ProgramError("question_answers must partition every supplied question_id exactly once")

    allowed_nodes = _graph_node_ids(results)
    graph_documents = _graph_documents(results)
    graph_sources = _graph_source_ids(results)
    returned_sources = {str(row["document_id"]) for row in documents}
    repeated_graph_ids = returned_sources & graph_sources
    if repeated_graph_ids:
        raise ProgramError(
            "question research returned frozen graph documents as new evidence: "
            f"{sorted(repeated_graph_ids)}"
        )
    reused_publications = _reused_graph_publications(documents, graph_documents)
    if reused_publications:
        raise ProgramError(
            "question research returned publications already present in the frozen corpus: "
            f"{sorted(reused_publications)}"
        )
    allowed_sources = {*graph_sources, *returned_sources}
    cited_returned: set[str] = set()
    claim_ids: set[str] = set()
    for index, row in enumerate(answers):
        label = f"question_answers[{index}]"
        question_id = str(row["question_id"])
        _text(row["question"], f"{label}.question")
        if row["question"] != questions[question_id]["question"]:
            raise ProgramError(f"{label}.question must exactly copy the supplied question")
        if row["status"] not in _ANSWER_STATUSES:
            raise ProgramError(
                f"{label}.status must be answered, partially_answered, or unresolved"
            )
        _text(row["answer"], f"{label}.answer")
        answer_nodes = _id_list(row["node_ids"], f"{label}.node_ids", allowed_nodes)
        required_nodes = set(map(str, questions[question_id]["node_ids"]))
        if not required_nodes <= answer_nodes:
            raise ProgramError(f"{label}.node_ids must include every node from its question")
        _string_list(row["limitations"], f"{label}.limitations")
        claims = row["claims"]
        if not isinstance(claims, list):
            raise ProgramError(f"{label}.claims must be a list of objects")
        if row["status"] != "unresolved" and not claims:
            raise ProgramError(f"{label}.claims must not be empty for {row['status']}")
        local_claims: dict[str, dict[str, Any]] = {}
        baseline_claim_ids: set[str] = set()
        delta_claim_ids: set[str] = set()
        for claim_index, claim in enumerate(claims):
            claim_label = f"{label}.claims[{claim_index}]"
            if not isinstance(claim, dict) or set(claim) != _CLAIM_FIELDS:
                raise ProgramError(
                    f"{claim_label} must contain exactly {', '.join(sorted(_CLAIM_FIELDS))}"
                )
            claim_id = _text(claim["claim_id"], f"{claim_label}.claim_id")
            if claim_id in claim_ids:
                raise ProgramError("question-research claim_id values must be unique")
            claim_ids.add(claim_id)
            local_claims[claim_id] = claim
            _text(claim["claim"], f"{claim_label}.claim")
            epistemic_status = str(claim["epistemic_status"])
            if epistemic_status not in _CLAIM_EPISTEMIC_STATUSES:
                raise ProgramError(
                    f"{claim_label}.epistemic_status must be direct_observation, synthesis, "
                    "or inference"
                )
            delta_type = str(claim["delta_type"])
            if delta_type not in _CLAIM_DELTA_TYPES:
                raise ProgramError(
                    f"{claim_label}.delta_type must be one of "
                    f"{sorted(_CLAIM_DELTA_TYPES)}"
                )
            _text(claim["evidence_scope"], f"{claim_label}.evidence_scope")
            assumptions = _string_list(
                claim["assumptions"], f"{claim_label}.assumptions"
            )
            if epistemic_status == "direct_observation" and assumptions:
                raise ProgramError(
                    f"{claim_label}.assumptions must be empty for direct_observation"
                )
            if (epistemic_status == "inference" or delta_type == "transfers") and not assumptions:
                raise ProgramError(
                    f"{claim_label}.assumptions must not be empty for inference or transfer"
                )
            cited = _id_list(
                claim["source_ids"], f"{claim_label}.source_ids", allowed_sources
            )
            if delta_type == "baseline":
                if not cited <= graph_sources:
                    raise ProgramError(
                        f"{claim_label} with delta_type baseline may cite only frozen graph sources"
                    )
                baseline_claim_ids.add(claim_id)
            else:
                newly_cited = cited & returned_sources
                if not newly_cited:
                    raise ProgramError(
                        f"{claim_label} with delta_type {delta_type} must cite newly returned evidence"
                    )
                delta_claim_ids.add(claim_id)
                cited_returned.update(newly_cited)

        declared_baseline = _id_list(
            row["frozen_baseline_claim_ids"],
            f"{label}.frozen_baseline_claim_ids",
            set(local_claims),
            allow_empty=True,
        )
        if declared_baseline != baseline_claim_ids:
            raise ProgramError(
                f"{label}.frozen_baseline_claim_ids must name exactly its baseline claims"
            )
        _id_list(
            row["counterevidence_claim_ids"],
            f"{label}.counterevidence_claim_ids",
            set(local_claims),
            allow_empty=True,
        )
        _id_list(
            row["alternative_explanation_claim_ids"],
            f"{label}.alternative_explanation_claim_ids",
            set(local_claims),
            allow_empty=True,
        )
        disposition = str(row["research_disposition"])
        if disposition not in _RESEARCH_DISPOSITIONS:
            raise ProgramError(
                f"{label}.research_disposition must be corpus_sufficient, "
                "literature_delta_found, or still_unresolved"
            )
        if disposition == "corpus_sufficient":
            if row["status"] != "answered" or delta_claim_ids:
                raise ProgramError(
                    f"{label} with corpus_sufficient must be answered using baseline claims only"
                )
            if row["material_answer_delta"] is not None:
                raise ProgramError(
                    f"{label}.material_answer_delta must be null for corpus_sufficient"
                )
            _text(row["saturation_reason"], f"{label}.saturation_reason")
        elif disposition == "literature_delta_found":
            if not delta_claim_ids:
                raise ProgramError(
                    f"{label} with literature_delta_found requires a non-baseline claim"
                )
            _text(row["material_answer_delta"], f"{label}.material_answer_delta")
            if row["saturation_reason"] is not None:
                raise ProgramError(
                    f"{label}.saturation_reason must be null for literature_delta_found"
                )
        else:
            if row["status"] not in {"partially_answered", "unresolved"} or delta_claim_ids:
                raise ProgramError(
                    f"{label} with still_unresolved must be partially_answered or unresolved "
                    "without a material delta claim"
                )
            if row["material_answer_delta"] is not None:
                raise ProgramError(
                    f"{label}.material_answer_delta must be null for still_unresolved"
                )
            _text(row["saturation_reason"], f"{label}.saturation_reason")
    unused = returned_sources - cited_returned
    if unused:
        raise ProgramError(
            "question research returned documents without a material delta claim: "
            f"{sorted(unused)}"
        )


def _claim_index(results: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for answer in _question_answers(results):
        for claim in answer["claims"]:
            claims[str(claim["claim_id"])] = {
                **claim,
                "question_id": str(answer["question_id"]),
                "node_ids": list(answer["node_ids"]),
                "research_disposition": str(answer["research_disposition"]),
            }
    return claims


def _validate_hypothesis_synthesis(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    connections = _contract_rows(records, "hypothesis_connections", "connection_id")
    allowed_nodes = _graph_node_ids(results)
    claims = _claim_index(results)
    returned_sources = {str(row["document_id"]) for row in documents}
    answer_sources = {
        str(row["document_id"])
        for row in _rows(results["pathology_question_research"]["records"], "documents")
    }
    allowed_sources = {*_graph_source_ids(results), *answer_sources, *returned_sources}
    cited_returned: set[str] = set()
    seen_titles: set[str] = set()
    for index, row in enumerate(connections):
        label = f"hypothesis_connections[{index}]"
        for field in (
            "connection_id", "title", "mechanistic_reasoning", "predicted_rescue_direction",
            "why_unexpected", "counterargument", "weakest_link", "falsifying_observation",
        ):
            _text(row[field], f"{label}.{field}")
        title = " ".join(str(row["title"]).casefold().split())
        if title in seen_titles:
            raise ProgramError("hypothesis_connections contains repeated titles")
        seen_titles.add(title)
        node_ids = _id_list(row["node_ids"], f"{label}.node_ids", allowed_nodes)
        claim_ids = _id_list(row["claim_ids"], f"{label}.claim_ids", set(claims))
        _string_list(row["limitations"], f"{label}.limitations", allow_empty=False)
        _string_list(row["assumptions"], f"{label}.assumptions", allow_empty=False)
        if not any(
            claims[claim_id]["research_disposition"] == "literature_delta_found"
            and claims[claim_id]["delta_type"] != "baseline"
            for claim_id in claim_ids
        ):
            raise ProgramError(
                f"{label}.claim_ids must include a non-baseline literature-delta claim"
            )
        source_ids = _id_list(
            row["source_ids"], f"{label}.source_ids", allowed_sources
        )
        required_sources = {
            str(source_id)
            for claim_id in claim_ids
            for source_id in claims[claim_id]["source_ids"]
        }
        if not required_sources <= source_ids:
            raise ProgramError(
                f"{label}.source_ids must include every source behind its selected claims"
            )
        claim_nodes = {
            str(node_id)
            for claim_id in claim_ids
            for node_id in claims[claim_id]["node_ids"]
        }
        if not node_ids & claim_nodes:
            raise ProgramError(f"{label}.node_ids must overlap its selected claim context")
        cited_returned.update(source_ids & returned_sources)
    unused = returned_sources - cited_returned
    if unused:
        raise ProgramError(f"hypothesis synthesis returned uncited documents: {sorted(unused)}")


def _connection_rows(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = results.get("pathology_hypothesis_synthesis")
    if result is None:
        return []
    return _rows(result["records"], "hypothesis_connections")


def _connection_index(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    fields = ("connection_id", "title", "node_ids", "predicted_rescue_direction")
    return [
        {field: row[field] for field in fields}
        for row in sorted(_connection_rows(results), key=lambda value: str(value["connection_id"]))
    ]


def _connection_context(
    results: Mapping[str, Mapping[str, Any]], connection_id: str
) -> dict[str, Any]:
    connection = _find(_connection_rows(results), "connection_id", connection_id)
    claim_ids = set(map(str, connection["claim_ids"]))
    claims = [
        row for claim_id, row in sorted(_claim_index(results).items()) if claim_id in claim_ids
    ]
    source_ids = set(map(str, connection["source_ids"]))
    documents = _merge_documents([
        *_rows(results["evidence_graph"]["records"], "documents"),
        *_rows(results["pathology_question_research"]["records"], "documents"),
        *_rows(results["pathology_hypothesis_synthesis"]["records"], "documents"),
    ])
    return {
        "connection": connection,
        "claims": claims,
        "source_index": _source_index(documents, source_ids),
    }
