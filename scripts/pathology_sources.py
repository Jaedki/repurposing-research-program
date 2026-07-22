#!/usr/bin/env python3
"""Pathology-only source ingestion for the repurposing controller."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


MONARCH_API = "https://api.monarchinitiative.org/v3/api"
DISMECH_REPO_API = "https://api.github.com/repos/monarch-initiative/dismech"
DISMECH_RAW = "https://raw.githubusercontent.com/monarch-initiative/dismech"
USER_AGENT = "repurposing-research-program/2"

MONARCH_PATHOLOGY_CATEGORIES = (
    "biolink:DiseaseToPhenotypicFeatureAssociation",
    "biolink:CausalGeneToDiseaseAssociation",
    "biolink:CorrelatedGeneToDiseaseAssociation",
    "biolink:VariantToDiseaseAssociation",
    "biolink:GenotypeToDiseaseAssociation",
    "biolink:DiseaseOrPhenotypicFeatureToLocationAssociation",
)

DISMECH_ALLOWED_SECTIONS = (
    "name",
    "description",
    "category",
    "parents",
    "mappings",
    "definitions",
    "inheritance",
    "progression",
    "mechanistic_hypotheses",
    "pathophysiology",
    "phenotypes",
    "biochemical",
    "genetic",
    "environmental",
    "disease_term",
    "references",
)

DISMECH_NODE_TYPES = {
    "pathophysiology": "molecular_process",
    "phenotypes": "phenotype",
    "biochemical": "biochemical_state",
    "genetic": "genetic_driver",
    "environmental": "environmental_driver",
}

_BLOCKED_KEY_PARTS = ("treatment", "therapeutic", "drug", "compound", "clinical_trial")
_MONDO = re.compile(r"^MONDO:\d+$", re.IGNORECASE)


class SourceError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(_canonical_bytes(value))[:24]}"


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise SourceError(f"Cached source conflicts with existing file: {path}")
        return
    path.write_bytes(payload)


def _fetch(url: str, path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    request = Request(
        url,
        headers={"Accept": "application/json, text/plain, */*", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=90) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SourceError(f"Source request failed for {url}: {exc}") from exc
    _write_once(path, payload)
    return payload


def _fetch_json(url: str, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_fetch(url, path).decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(f"Source did not return valid JSON: {url}") from exc
    if not isinstance(value, dict):
        raise SourceError(f"Source did not return one JSON object: {url}")
    return value


def _url(base: str, pairs: Iterable[tuple[str, Any]]) -> str:
    return f"{base}?{urlencode(list(pairs), doseq=True)}"


def _compact_entity(item: dict[str, Any]) -> dict[str, Any]:
    fields = ("id", "name", "category", "description", "synonym", "xref", "namespace")
    return {field: item[field] for field in fields if item.get(field) not in (None, [], "")}


def _exact_disease(items: list[dict[str, Any]], disease: str) -> dict[str, Any]:
    wanted = disease.casefold().strip()
    exact = []
    for item in items:
        names = [item.get("name", ""), *(item.get("synonym") or [])]
        if any(str(name).casefold().strip() == wanted for name in names):
            exact.append(item)
    exact = [item for item in exact if str(item.get("id", "")).startswith("MONDO:")]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(items) == 1 and str(items[0].get("id", "")).startswith("MONDO:"):
        return items[0]
    choices = sorted({f"{item.get('id')}: {item.get('name')}" for item in exact or items[:10]})
    raise SourceError(
        "Monarch disease resolution is ambiguous. Re-run init with --mondo MONDO:... "
        f"Candidates: {choices}"
    )


def _monarch_node_type(category: str) -> str:
    return {
        "biolink:Disease": "disease_anchor",
        "biolink:Gene": "genetic_driver",
        "biolink:Protein": "genetic_driver",
        "biolink:SequenceVariant": "genetic_driver",
        "biolink:Genotype": "genetic_driver",
        "biolink:PhenotypicFeature": "phenotype",
        "biolink:AnatomicalEntity": "anatomy",
        "biolink:BiologicalProcess": "molecular_process",
        "biolink:Pathway": "molecular_process",
        "biolink:MolecularActivity": "molecular_process",
        "biolink:Cell": "cell_state",
    }.get(category, "pathology_context")


def _add_document(index: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    key = str(row["document_id"])
    current = index.get(key)
    if current is None:
        index[key] = row
        return
    for field, value in row.items():
        if field not in current or current[field] in (None, "", []):
            current[field] = value
        elif isinstance(value, list) and isinstance(current[field], list):
            current[field] = sorted({*map(str, current[field]), *map(str, value)})


def _add_node(index: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    key = str(row["node_id"])
    current = index.get(key)
    if current is None:
        index[key] = row
        return
    current["source_ids"] = sorted(
        {*map(str, current.get("source_ids", [])), *map(str, row.get("source_ids", []))}
    )
    payloads = current.setdefault("source_payloads", [])
    for payload in row.get("source_payloads", []):
        if payload not in payloads:
            payloads.append(payload)


def _monarch(
    cache: Path, disease: str, mondo_hint: str | None
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    version_url = f"{MONARCH_API}/version"
    version = _fetch_json(version_url, cache / "monarch_version.json")

    supplied_id = mondo_hint or (disease.upper() if _MONDO.match(disease) else None)
    if supplied_id:
        supplied_id = supplied_id.upper()
        entity_url = f"{MONARCH_API}/entity/{quote(supplied_id, safe='')}"
        entity = _fetch_json(entity_url, cache / "monarch_entity.json")
    else:
        search_url = _url(
            f"{MONARCH_API}/search",
            (("q", disease), ("category", "biolink:Disease"), ("limit", 100)),
        )
        search = _fetch_json(search_url, cache / "monarch_search.json")
        items = [item for item in search.get("items", []) if isinstance(item, dict)]
        entity = _exact_disease(items, disease)

    mondo_id = str(entity.get("id", "")).upper()
    if not _MONDO.match(mondo_id):
        raise SourceError("Monarch did not resolve the case to a MONDO disease identifier")

    pairs: list[tuple[str, Any]] = [("entity", mondo_id), ("direct", "true")]
    pairs.extend(("category", category) for category in MONARCH_PATHOLOGY_CATEGORIES)
    pairs.extend((("limit", 500), ("offset", 0)))

    associations: list[dict[str, Any]] = []
    offset = 0
    page = 0
    while True:
        page_pairs = [(name, offset if name == "offset" else value) for name, value in pairs]
        association_url = _url(f"{MONARCH_API}/association", page_pairs)
        response = _fetch_json(association_url, cache / f"monarch_associations_{page:04d}.json")
        items = response.get("items", response.get("associations", []))
        if not isinstance(items, list):
            raise SourceError("Monarch association response does not contain an items list")
        associations.extend(item for item in items if isinstance(item, dict))
        offset += len(items)
        page += 1
        total = int(response.get("total", len(associations)))
        if not items or offset >= total:
            break

    documents: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    entity_doc_id = _stable_id("MONARCH-ENTITY", mondo_id)
    _add_document(
        documents,
        {
            "document_id": entity_doc_id,
            "title": f"Monarch entity: {entity.get('name', mondo_id)}",
            "source": "Monarch Initiative",
            "citation": mondo_id,
            "url": f"https://monarchinitiative.org/{mondo_id}",
        },
    )
    _add_node(
        nodes,
        {
            "node_id": mondo_id,
            "label": str(entity.get("name") or mondo_id),
            "node_type": "disease_anchor",
            "description": str(entity.get("description") or ""),
            "source_ids": [entity_doc_id],
            "source_payloads": [_compact_entity(entity)],
        },
    )

    blocked = ("chemical", "drug", "treatment")
    for association in associations:
        category = str(association.get("category", ""))
        predicate = str(association.get("predicate", ""))
        subject = str(association.get("subject", ""))
        object_id = str(association.get("object", ""))
        subject_category = str(association.get("subject_category", ""))
        object_category = str(association.get("object_category", ""))
        signature = " ".join((category, predicate, subject_category, object_category)).casefold()
        if category not in MONARCH_PATHOLOGY_CATEGORIES or any(term in signature for term in blocked):
            continue
        if mondo_id not in {subject, object_id}:
            continue
        native_id = str(association.get("id") or _stable_id("ASSOC", association))
        document_id = _stable_id("MONARCH-ASSOC", native_id)
        _add_document(
            documents,
            {
                "document_id": document_id,
                "title": (
                    f"Monarch association: {association.get('subject_label', subject)} "
                    f"{predicate} {association.get('object_label', object_id)}"
                ),
                "source": "Monarch Initiative",
                "citation": str(association.get("primary_knowledge_source") or native_id),
                "native_id": native_id,
                "publications": association.get("publications") or [],
                "supporting_text": association.get("supporting_text"),
            },
        )
        for side, node_id, node_category in (
            ("subject", subject, subject_category),
            ("object", object_id, object_category),
        ):
            if not node_id or node_id == mondo_id:
                continue
            _add_node(
                nodes,
                {
                    "node_id": node_id,
                    "label": str(association.get(f"{side}_label") or node_id),
                    "node_type": _monarch_node_type(node_category),
                    "description": "",
                    "source_ids": [document_id],
                    "source_payloads": [
                        {
                            "association_id": native_id,
                            "category": category,
                            "predicate": predicate,
                            "primary_knowledge_source": association.get(
                                "primary_knowledge_source"
                            ),
                            "frequency": association.get("frequency_qualifier_label"),
                            "evidence": association.get("has_evidence") or [],
                            "publications": association.get("publications") or [],
                        }
                    ],
                },
            )
        edges.append(
            {
                "edge_id": _stable_id("MONARCH-EDGE", native_id),
                "subject_id": subject,
                "relation": predicate,
                "object_id": object_id,
                "evidence_summary": str(
                    association.get("supporting_text")
                    or association.get("primary_knowledge_source")
                    or "Monarch knowledge-graph association"
                ),
                "source_ids": [document_id],
                "confidence": "source_assertion",
            }
        )

    raw_files = sorted(cache.glob("monarch_*.json"))
    receipt = {
        "source": "monarch",
        "version": version,
        "resolved_entity": _compact_entity(entity),
        "query": {
            "mondo_id": mondo_id,
            "categories": list(MONARCH_PATHOLOGY_CATEGORIES),
            "direct": True,
        },
        "record_count": len(edges),
        "raw_files": [
            {"path": str(path), "sha256": _sha256(path.read_bytes())} for path in raw_files
        ],
    }
    return entity, list(documents.values()), list(nodes.values()), edges, receipt


def _blocked_key(key: Any) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _BLOCKED_KEY_PARTS)


def _pathology_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _pathology_only(item)
            for key, item in value.items()
            if not _blocked_key(key)
        }
    if isinstance(value, list):
        return [_pathology_only(item) for item in value]
    return value


def _evidence_items(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and reference.strip():
            found.append(
                {
                    "reference": reference.strip(),
                    "snippet": value.get("snippet"),
                    "supports": value.get("supports"),
                    "explanation": value.get("explanation"),
                    "evidence_source": value.get("evidence_source"),
                }
            )
        for item in value.values():
            found.extend(_evidence_items(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_evidence_items(item))
    return found


def _dismech(
    cache: Path, mondo_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    gaps: list[str] = []
    commit = _fetch_json(
        f"{DISMECH_REPO_API}/commits/main", cache / "dismech_commit.json"
    )
    commit_sha = str(commit.get("sha", ""))
    if not commit_sha:
        raise SourceError("DisMech did not return a commit SHA")
    export_url = f"{DISMECH_RAW}/{commit_sha}/exports/mondo_emc.tsv"
    export_payload = _fetch(export_url, cache / "dismech_mondo_emc.tsv")
    reader = csv.DictReader(io.StringIO(export_payload.decode("utf-8-sig")), delimiter="\t")
    match = next((row for row in reader if row.get("mondo_id") == mondo_id), None)
    if match is None:
        gaps.append(f"DisMech has no MONDO-mapped disorder entry for {mondo_id}")
        return [], [], [], None, gaps

    page = str(match.get("dismech_url", ""))
    slug = Path(urlparse(page).path).stem
    if not slug:
        gaps.append(f"DisMech mapping for {mondo_id} has no usable disorder path")
        return [], [], [], None, gaps
    yaml_url = f"{DISMECH_RAW}/{commit_sha}/kb/disorders/{quote(slug)}.yaml"
    yaml_payload = _fetch(yaml_url, cache / f"dismech_{slug}.yaml")
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SourceError("PyYAML is required to parse the pathology-only DisMech record") from exc
    try:
        raw = yaml.safe_load(yaml_payload.decode("utf-8-sig"))
    except Exception as exc:
        raise SourceError(f"DisMech YAML could not be parsed for {mondo_id}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SourceError(f"DisMech disorder record is not an object for {mondo_id}")
    sanitized = _pathology_only(
        {key: raw[key] for key in DISMECH_ALLOWED_SECTIONS if key in raw}
    )

    documents: dict[str, dict[str, Any]] = {}
    file_document_id = _stable_id("DISMECH-FILE", {"commit": commit_sha, "slug": slug})
    _add_document(
        documents,
        {
            "document_id": file_document_id,
            "title": f"DisMech pathology record: {sanitized.get('name', slug)}",
            "source": "DisMech",
            "citation": f"monarch-initiative/dismech@{commit_sha}:{slug}",
            "url": page,
            "raw_path": str(cache / f"dismech_{slug}.yaml"),
        },
    )

    nodes: list[dict[str, Any]] = []
    name_index: dict[str, list[str]] = {}
    node_payloads: dict[str, dict[str, Any]] = {}
    for section, node_type in DISMECH_NODE_TYPES.items():
        values = sanitized.get(section, [])
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            label = str(item.get("name") or item.get("label") or f"{section} {index + 1}")
            node_id = _stable_id(
                "DISMECH-NODE", {"mondo_id": mondo_id, "section": section, "label": label}
            )
            evidence = _evidence_items(item)
            source_ids: list[str] = []
            for evidence_item in evidence:
                reference = str(evidence_item["reference"])
                document_id = reference if ":" in reference else _stable_id("DISMECH-REF", reference)
                source_ids.append(document_id)
                _add_document(
                    documents,
                    {
                        "document_id": document_id,
                        "title": reference,
                        "source": "DisMech evidence",
                        "citation": reference,
                        "snippets": [evidence_item["snippet"]]
                        if evidence_item.get("snippet")
                        else [],
                        "supports": [evidence_item["supports"]]
                        if evidence_item.get("supports")
                        else [],
                    },
                )
            if not source_ids:
                source_ids = [file_document_id]
            payload = dict(item)
            node_payloads[node_id] = payload
            nodes.append(
                {
                    "node_id": node_id,
                    "label": label,
                    "node_type": node_type,
                    "description": str(item.get("description") or ""),
                    "source_ids": sorted(set(source_ids)),
                    "source_payloads": [payload],
                    "source_section": section,
                }
            )
            name_index.setdefault(label.casefold(), []).append(node_id)

    edges: list[dict[str, Any]] = []
    for node in nodes:
        payload = node_payloads[node["node_id"]]
        for edge_field, relation in (("downstream", "causes_or_contributes_to"), ("sequelae", "leads_to")):
            values = payload.get(edge_field, [])
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                edge = value if isinstance(value, dict) else {"target": value}
                target = str(edge.get("target") or "").strip()
                targets = name_index.get(target.casefold(), [])
                if len(targets) != 1:
                    gaps.append(
                        f"DisMech edge target could not be resolved uniquely: {node['label']} -> {target}"
                    )
                    continue
                edge_evidence = _evidence_items(edge)
                source_ids = [
                    str(item["reference"]) if ":" in str(item["reference"]) else _stable_id("DISMECH-REF", item["reference"])
                    for item in edge_evidence
                ] or list(node["source_ids"])
                edges.append(
                    {
                        "edge_id": _stable_id(
                            "DISMECH-EDGE",
                            {"subject": node["node_id"], "relation": relation, "target": targets[0], "index": index},
                        ),
                        "subject_id": node["node_id"],
                        "relation": relation,
                        "object_id": targets[0],
                        "evidence_summary": str(edge.get("description") or relation),
                        "source_ids": sorted(set(source_ids)),
                        "confidence": str(edge.get("causal_link_type") or "curated"),
                    }
                )

    raw_files = [cache / "dismech_mondo_emc.tsv", cache / f"dismech_{slug}.yaml"]
    receipt = {
        "source": "dismech",
        "version": commit_sha,
        "query": {"mondo_id": mondo_id, "slug": slug},
        "record_count": len(nodes),
        "url": page,
        "raw_files": [
            {"path": str(path), "sha256": _sha256(path.read_bytes())} for path in raw_files
        ],
        "pathology_sections": list(DISMECH_ALLOWED_SECTIONS),
        "excluded_sections": ["treatments"],
    }
    return list(documents.values()), nodes, edges, receipt, gaps


def fetch_pathology_sources(
    run_root: Path,
    disease: str,
    mondo_hint: str | None = None,
) -> dict[str, Any]:
    """Fetch and normalize pathology-only source records for one immutable run."""
    cache = run_root / "sources" / "raw"
    entity, monarch_docs, monarch_nodes, monarch_edges, monarch_receipt = _monarch(
        cache, disease, mondo_hint
    )
    mondo_id = str(entity["id"])
    dismech_docs, dismech_nodes, dismech_edges, dismech_receipt, gaps = _dismech(
        cache, mondo_id
    )

    documents: dict[str, dict[str, Any]] = {}
    for row in [*monarch_docs, *dismech_docs]:
        _add_document(documents, row)
    nodes: dict[str, dict[str, Any]] = {}
    for row in [*monarch_nodes, *dismech_nodes]:
        _add_node(nodes, row)
    receipts = [monarch_receipt]
    if dismech_receipt:
        receipts.append(dismech_receipt)

    records = {
        "documents": sorted(documents.values(), key=lambda row: str(row["document_id"])),
        "source_nodes": sorted(nodes.values(), key=lambda row: str(row["node_id"])),
        "source_edges": sorted(
            [*monarch_edges, *dismech_edges], key=lambda row: str(row["edge_id"])
        ),
        "source_receipts": receipts,
    }
    return {
        "stage": "pathology_sources",
        "status": "complete",
        "resolved_disease": {
            "mondo_id": mondo_id,
            "name": entity.get("name") or disease,
            "description": entity.get("description") or "",
        },
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
        "gaps": gaps,
        "notes": [
            "Treatment, therapeutic, drug, compound, and clinical-trial fields were excluded before packet construction."
        ],
    }


__all__ = ["SourceError", "fetch_pathology_sources"]
