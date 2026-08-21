"""Deterministic candidate scoring and dense ranking."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import SCORE_COMPONENTS, SCORE_SCALE
from .evidence import _rows
from .identity import _canonical_candidates


def _final_score(row: Mapping[str, Any]) -> int:
    return SCORE_SCALE * sum(
        int(row["component_scores"][component]["value"])
        for component in SCORE_COMPONENTS
    )


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    return {
        "rank": rank,
        "candidate_id": row["candidate_id"],
        "name": candidate["name"],
        "identity_status": candidate["identity"]["status"],
        "viability_status": (
            "invalidated" if row["invalidating_finding"] is not None else "viable"
        ),
        **{
            component: row["component_scores"][component]["value"]
            for component in SCORE_COMPONENTS
        },
        **{
            f"{component}_rationale": (
                f"{row['component_scores'][component]['reason']} Sources: "
                f"{'; '.join(map(str, row['component_scores'][component]['source_ids']))}"
            )
            for component in SCORE_COMPONENTS
        },
        "final_score": _final_score(row),
        "hypothesis_report": reports[str(row["candidate_id"])]["hypothesis_report"],
    }


def _ranked_rows(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = {row["candidate_id"]: row for row in _canonical_candidates(results)}
    reports = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_review"]["records"], "reviews")
    }
    assessments = _rows(results["candidate_audit"]["records"], "assessments")
    assessments.sort(key=lambda row: (
        row["invalidating_finding"] is not None,
        -_final_score(row),
        str(row["candidate_id"]),
    ))
    rows: list[dict[str, Any]] = []
    rank = 0
    prior_key: tuple[bool, int] | None = None
    for assessment in assessments:
        score = _final_score(assessment)
        key = (assessment["invalidating_finding"] is not None, score)
        if key != prior_key:
            rank += 1
            prior_key = key
        rows.append(_project_ranked_row(rank, assessment, candidates, reports))
    return rows, candidates
