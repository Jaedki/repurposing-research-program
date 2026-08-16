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
        source_ids = _cited_ids([
            context for context in _rows(row, "evidence_context")
            if context.get("polarity") == "supports"
        ])
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
    context_ids = set(map(str, node.get("related_concept_ids", [])))
    context_ids.update(
        str(row["node_id"])
        for row in nodes
        if node_id in set(map(str, row.get("related_concept_ids", [])))
    )
    return {
        "node": node,
        "profile": profiles[0] if profiles else None,
        "scope_evidence": ({"uncertainty": profiles[0].get("uncertainty", ""), "contradictions": profiles[0].get("contradictions", []), "gaps": profiles[0].get("gaps", [])} if profiles else None),
        "source_edges": sorted(source_edges, key=lambda row: str(row["edge_id"])),
        "assertions": sorted(assertions, key=lambda row: str(row["assertion_id"])),
        "gaps": [row for row in records.get("gap_index", []) if str(row["node_id"]) == node_id],
        "related_nodes": [
            {key: node_by_id[value][key] for key in GRAPH_INDEX_FIELDS}
            for value in sorted(related_ids)
            if value in node_by_id
            and node_by_id[value].get("node_type") != "disease_anchor"
        ],
        "context_nodes": [
            {key: node_by_id[value][key] for key in GRAPH_INDEX_FIELDS}
            for value in sorted(context_ids)
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
    profile_rows = _merge_unique(profiles, "node_id", "profiles")
    indexed_gaps = {
        (str(profile["node_id"]), "profile.gaps", " ".join(str(gap).split()))
        for profile in profile_rows for gap in profile.get("gaps", []) if str(gap).strip()
    } | {
        (str(profile["node_id"]), "profile.uncertainty", " ".join(str(profile["uncertainty"]).split()))
        for profile in profile_rows if str(profile.get("uncertainty", "")).strip()
    } | {
        (str(gap["node_id"]), "result.gaps", " ".join(str(gap["statement"]).split()))
        for gap in gaps if isinstance(gap, Mapping) and str(gap.get("statement", "")).strip()
    }
    records = {
        "source_nodes": canonical_nodes,
        "source_edges": canonical_edges,
        "source_receipts": _rows(source, "source_receipts"),
        "disease_context": _rows(source, "disease_context"),
        "profiles": profile_rows,
        "assertions": _merge_assertions(assertions),
        "gap_index": [{"gap_id": _stable_id("GAP", {"node_id": node_id, "bound_object": bound_object, "statement": statement}), "node_id": node_id, "bound_object": bound_object, "statement": statement} for node_id, bound_object, statement in sorted(indexed_gaps)],
    }
    landscape = results.get("pathology_landscape_scan", {}).get("records")
    landscape_documents = (
        _cited_documents(landscape) if isinstance(landscape, Mapping) else []
    )
    coverage = results.get("pathology_coverage_expansion", {}).get("records")
    coverage_documents = (
        _cited_documents(coverage) if isinstance(coverage, Mapping) else []
    )
    records["documents"] = _select_cited_documents(
        _merge_documents([
            *_cited_documents(source),
            *landscape_documents,
            *coverage_documents,
            *item_documents,
        ]),
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
