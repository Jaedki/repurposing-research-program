"""Closed-corpus candidate audit and source-use integrity validation."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .contracts import (
    AUDIT_EXCLUSION_REASONS,
    SCORE_COMPONENTS,
    SCORE_VALUES,
    _SOURCE_CHECK_VERDICTS,
)
from .errors import ProgramError
from .evidence import _all_documents, _document_has_inspectable_content, _rows
from .validation import (
    _contract_rows,
    _ids,
    _references,
    _validate_cited_entries,
    _validate_exact_object,
)

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


def _source_scope(value: Any) -> str:
    scope = str(value)
    prefix = "component_scores."
    component = scope[len(prefix):]
    return (
        component
        if scope.startswith(prefix) and component in SCORE_COMPONENTS
        else scope
    )


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
        scope = _source_scope(check["scope"])
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
    for index, row in enumerate(assessments):
        label = f"assessments[{index}]"
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
        for component in SCORE_COMPONENTS:
            verdicts = {
                str(check["verdict"])
                for check in row["source_integrity"]["checks"]
                if _source_scope(check["scope"]) == component
            }
            if not verdicts & {"supports", "partly_supports"}:
                raise ProgramError(
                    f"{label}.component_scores.{component} must have at least one "
                    "supports or partly_supports source-integrity check"
                )
            if components[component]["value"] == 20 and verdicts & {
                "does_not_support", "contradicts"
            }:
                raise ProgramError(
                    f"{label}.component_scores.{component} is a 20-point component and "
                    "cannot contain does_not_support or contradicts checks"
                )

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
