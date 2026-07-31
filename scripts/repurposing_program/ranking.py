"""Deterministic candidate scoring and dense ranking."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import SCORE_COMPONENTS
from .evidence import _rows
from .identity import _canonical_candidates


def _final_score(row: Mapping[str, Any]) -> int:
    return sum(
        int(row["component_scores"][component]["value"])
        for component in SCORE_COMPONENTS
    )


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    return {
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
        "source_ids": ";".join(
            sorted(map(str, row["net_assessment"]["source_ids"]))
        ),
    }


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
