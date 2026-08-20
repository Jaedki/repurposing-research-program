"""Structural validation for candidate scoring and bounded exclusions."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import AUDIT_EXCLUSION_REASONS, SCORE_COMPONENTS, SCORE_MAX, SCORE_MIN
from .errors import ProgramError
from .evidence import _document_alias_index, _rows
from .validation import _contract_rows, _ids, _references, _validate_exact_object


def _component_score(value: Any, label: str) -> int:
    if type(value) is not int or not SCORE_MIN <= value <= SCORE_MAX:
        raise ProgramError(f"{label} must be an integer from {SCORE_MIN} through {SCORE_MAX}")
    return value


def _validate_candidate_audit(
    records: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    hypothesis_packets: Iterable[Mapping[str, Any]],
) -> None:
    assessments = _contract_rows(records, "assessments", "candidate_id")
    exclusions = _contract_rows(records, "excluded_candidates", "candidate_id")
    candidate_ids = _ids(
        _rows(results["candidate_review"]["records"], "reviews"),
        "candidate_id",
        "reviews",
    )
    assessment_ids = {str(row["candidate_id"]) for row in assessments}
    exclusion_ids = {str(row["candidate_id"]) for row in exclusions}
    if assessment_ids & exclusion_ids or assessment_ids | exclusion_ids != candidate_ids:
        raise ProgramError(
            "assessments and excluded_candidates must partition every reviewed candidate exactly once"
        )
    hypothesis_packets = list(hypothesis_packets)
    source_ids_by_candidate = {
        str(packet["hypothesis"]["candidate"]["candidate_id"]): set(
            _document_alias_index(packet["source_index"])
        )
        for packet in hypothesis_packets
    }
    if (
        len(source_ids_by_candidate) != len(hypothesis_packets)
        or set(source_ids_by_candidate) != candidate_ids
    ):
        raise ProgramError("hypothesis_packets must contain every reviewed candidate exactly once")
    for index, row in enumerate(assessments):
        label = f"assessments[{index}]"
        candidate_source_ids = source_ids_by_candidate[str(row["candidate_id"])]
        components = _validate_exact_object(
            row["component_scores"], set(SCORE_COMPONENTS), f"{label}.component_scores"
        )
        for component in SCORE_COMPONENTS:
            score = _validate_exact_object(
                components[component], {"value", "reason", "source_ids"},
                f"{label}.component_scores.{component}",
            )
            _component_score(score["value"], f"{label}.component_scores.{component}.value")
            if not str(score["reason"]).strip():
                raise ProgramError(f"{label}.component_scores.{component}.reason must be non-empty")
            if not _references(
                score, "source_ids", candidate_source_ids,
                f"{label}.component_scores.{component}",
            ):
                raise ProgramError(f"{label}.component_scores.{component}.source_ids must cite retained evidence")
        invalidating = row["invalidating_finding"]
        if invalidating is not None:
            invalidating = _validate_exact_object(
                invalidating, {"finding", "source_ids"}, f"{label}.invalidating_finding"
            )
            if not str(invalidating["finding"]).strip():
                raise ProgramError(f"{label}.invalidating_finding.finding must be non-empty")
            invalidating_refs = _references(
                invalidating, "source_ids", candidate_source_ids,
                f"{label}.invalidating_finding",
            )
            zero_sources = {
                str(source_id) for component in SCORE_COMPONENTS
                if components[component]["value"] == 0
                for source_id in components[component]["source_ids"]
            }
            if not invalidating_refs & zero_sources:
                raise ProgramError(
                    f"{label}.invalidating_finding must share evidence with a zero-scored category"
                )
    for index, row in enumerate(exclusions):
        label = f"excluded_candidates[{index}]"
        if row["reason_code"] not in AUDIT_EXCLUSION_REASONS:
            raise ProgramError(f"{label}.reason_code must be one of {sorted(AUDIT_EXCLUSION_REASONS)}")
        if not str(row["finding"]).strip():
            raise ProgramError(f"{label}.finding must be non-empty")
        if not _references(
            row, "source_ids", source_ids_by_candidate[str(row["candidate_id"])], label
        ):
            raise ProgramError(f"{label}.source_ids must cite retained evidence")
