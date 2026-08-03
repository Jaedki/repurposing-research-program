"""Frozen pathology-graph construction, indexing, and context projection."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import GRAPH_INDEX_FIELDS
from .errors import ProgramError
from .evidence import (
    _cited_ids,
    _cited_documents,
    _find,
    _merge_documents,
    _merge_unique,
    _rows,
    _select_cited_documents,
)
from .pathology import _canonical_source_records
from .storage import _stable_id

def _merge_assertions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[
        tuple[str, str, str],
        dict[tuple[str, str, str, str, str], dict[str, Any]],
    ] = {}
    for row in rows:
        triple = tuple(
            str(row.get(field, "")).strip()
            for field in ("subject_id", "relation", "object_id")
        )
        if not all(triple):
            raise ProgramError("assertions require subject_id, relation, and object_id")
        contexts = row.get("evidence_context")
        if not isinstance(contexts, list) or not contexts:
            raise ProgramError("assertions.evidence_context must be a non-empty list")
        grouped_contexts = merged.setdefault(triple, {})
        for context in contexts:
            context_key = tuple(
                str(context[field])
                for field in ("evidence_type", "model", "stage", "polarity", "summary")
            )
            current = grouped_contexts.setdefault(
                context_key,
                {
                    "evidence_type": context_key[0],
                    "model": context_key[1],
                    "stage": context_key[2],
                    "polarity": context_key[3],
                    "summary": context_key[4],
                    "source_ids": [],
                },
            )
            current["source_ids"] = sorted({
                *map(str, current["source_ids"]),
                *map(str, context["source_ids"]),
            })
    assertions: list[dict[str, Any]] = []
    for triple in sorted(merged):
        triple_record = dict(zip(("subject_id", "relation", "object_id"), triple))
        assertions.append({
            "assertion_id": _stable_id("ASSERTION", triple_record),
            **triple_record,
            "evidence_context": [
                merged[triple][context_key]
                for context_key in sorted(merged[triple])
            ],
        })
    return assertions


def _graph_index(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in GRAPH_INDEX_FIELDS}
        for row in sorted(
            _rows(records, "source_nodes"), key=lambda value: str(value["node_id"])
        )
        if row.get("node_type") != "disease_anchor"
    ]


def _graph_support_ids(records: Mapping[str, Any]) -> dict[str, set[str]]:
    support = {
        str(row["node_id"]): set(map(str, row["source_ids"]))
        for row in _rows(records, "source_nodes")
        if row.get("node_type") != "disease_anchor"
    }
    for row in _rows(records, "profiles"):
        node_id = str(row["node_id"])
        if node_id in support:
            support[node_id].update(map(str, row["source_ids"]))
    for row in _rows(records, "source_edges"):
        source_ids = set(map(str, row["source_ids"]))
        for node_id in map(str, (row["subject_id"], row["object_id"])):
            if node_id in support:
                support[node_id].update(source_ids)
    for row in _rows(records, "assertions"):
        source_ids = _cited_ids(row)
        for node_id in map(str, (row["subject_id"], row["object_id"])):
            if node_id in support:
                support[node_id].update(source_ids)
    return support


def _graph_node_context(records: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    nodes = _rows(records, "source_nodes")
    node_by_id = {str(row["node_id"]): row for row in nodes}
    node = _find(nodes, "node_id", node_id)
    if node.get("node_type") == "disease_anchor":
        raise ProgramError("The disease anchor is not a retrievable pathology concept")
    profiles = [
        row for row in _rows(records, "profiles") if str(row["node_id"]) == node_id
    ]
    if len(profiles) > 1:
        raise ProgramError(f"Multiple pathology profiles exist for node_id={node_id}")
    source_edges = [
        row
        for row in _rows(records, "source_edges")
        if node_id in {str(row["subject_id"]), str(row["object_id"])}
    ]
    assertions = [
        row
        for row in _rows(records, "assertions")
        if node_id in {str(row["subject_id"]), str(row["object_id"])}
    ]
    related_ids = {
        str(value)
        for edge in [*source_edges, *assertions]
        for value in (edge["subject_id"], edge["object_id"])
        if str(value) != node_id
    }
    return {
        "node": node,
        "profile": profiles[0] if profiles else None,
        "source_edges": sorted(source_edges, key=lambda row: str(row["edge_id"])),
        "assertions": sorted(assertions, key=lambda row: str(row["assertion_id"])),
        "related_nodes": [
            {key: node_by_id[value][key] for key in GRAPH_INDEX_FIELDS}
            for value in sorted(related_ids)
            if value in node_by_id
            and node_by_id[value].get("node_type") != "disease_anchor"
        ],
    }


def _assemble_graph_result(
    results: Mapping[str, Mapping[str, Any]],
    profiles: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    item_documents: list[dict[str, Any]],
    gaps: list[Any],
) -> dict[str, Any]:
    source = results["pathology_sources"]["records"]
    canonical_nodes, canonical_edges = _canonical_source_records(results)
    records = {
        "source_nodes": canonical_nodes,
        "source_edges": canonical_edges,
        "source_receipts": _rows(source, "source_receipts"),
        "disease_context": _rows(source, "disease_context"),
        "profiles": _merge_unique(profiles, "node_id", "profiles"),
        "assertions": _merge_assertions(assertions),
    }
    records["documents"] = _select_cited_documents(
        _merge_documents([*_cited_documents(source), *item_documents]),
        records,
    )
    return {
        "stage": "evidence_graph",
        "status": "complete",
        "snapshot_id": _stable_id("GRAPH", records),
        "records": records,
        "gaps": gaps,
        "notes": [
            "Frozen pathology-only graph built from the accepted run-local concept partition."
        ],
    }
