"""Deterministic candidate provenance and exclusion projections."""

from __future__ import annotations

from typing import Any, Mapping

from .evidence import _rows


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
                "pathology_source_ids": sorted(
                    map(str, candidate["pathology_source_ids"])
                ),
                "mechanism_source_ids": sorted(
                    map(str, candidate["mechanism_source_ids"])
                ),
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
                "graph_node_ids": sorted(
                    set(map(str, candidate["graph_node_ids"]))
                ),
                "pathology_source_ids": sorted(
                    set(map(str, candidate["pathology_source_ids"]))
                ),
                "mechanism_source_ids": sorted(
                    set(map(str, candidate["mechanism_source_ids"]))
                ),
                "source_integrity": exclusion["source_integrity"],
            }
        )
    return rows
