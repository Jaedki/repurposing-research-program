"""Global pathology-question research and hypothesis-connection validation."""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ProgramError
from .evidence import _find, _merge_documents, _rows, _source_index
from .graph import _graph_index
from .validation import _contract_rows, _required, _validate_documents


_ANSWER_STATUSES = frozenset({"answered", "partially_answered", "unresolved"})


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
    for index, row in enumerate(questions):
        label = f"open_questions[{index}]"
        _required(row, ("question_id", "question", "rationale"), label)
        _text(row["question_id"], f"{label}.question_id")
        question = _text(row["question"], f"{label}.question")
        _text(row["rationale"], f"{label}.rationale")
        _id_list(row["node_ids"], f"{label}.node_ids", allowed_nodes)
        normalized = " ".join(question.casefold().split())
        if normalized in seen_questions:
            raise ProgramError("open_questions contains repeated question text")
        seen_questions.add(normalized)


def _validate_question_research(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    answers = _contract_rows(records, "question_answers", "question_id")
    questions = {str(row["question_id"]): row for row in _open_questions(results)}
    if {str(row["question_id"]) for row in answers} != set(questions):
        raise ProgramError("question_answers must partition every supplied question_id exactly once")

    allowed_nodes = _graph_node_ids(results)
    returned_sources = {str(row["document_id"]) for row in documents}
    allowed_sources = {*_graph_source_ids(results), *returned_sources}
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
        for claim_index, claim in enumerate(claims):
            claim_label = f"{label}.claims[{claim_index}]"
            if not isinstance(claim, dict) or set(claim) != {"claim_id", "claim", "source_ids"}:
                raise ProgramError(
                    f"{claim_label} must contain exactly claim_id, claim, and source_ids"
                )
            claim_id = _text(claim["claim_id"], f"{claim_label}.claim_id")
            if claim_id in claim_ids:
                raise ProgramError("question-research claim_id values must be unique")
            claim_ids.add(claim_id)
            _text(claim["claim"], f"{claim_label}.claim")
            cited = _id_list(
                claim["source_ids"], f"{claim_label}.source_ids", allowed_sources
            )
            cited_returned.update(cited & returned_sources)
    unused = returned_sources - cited_returned
    if unused:
        raise ProgramError(f"question research returned uncited documents: {sorted(unused)}")


def _claim_index(results: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for answer in _question_answers(results):
        for claim in answer["claims"]:
            claims[str(claim["claim_id"])] = {
                **claim,
                "question_id": str(answer["question_id"]),
                "node_ids": list(answer["node_ids"]),
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
            "why_unexpected", "counterargument",
        ):
            _text(row[field], f"{label}.{field}")
        title = " ".join(str(row["title"]).casefold().split())
        if title in seen_titles:
            raise ProgramError("hypothesis_connections contains repeated titles")
        seen_titles.add(title)
        node_ids = _id_list(row["node_ids"], f"{label}.node_ids", allowed_nodes)
        claim_ids = _id_list(row["claim_ids"], f"{label}.claim_ids", set(claims))
        _string_list(row["limitations"], f"{label}.limitations")
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
