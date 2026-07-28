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
USER_AGENT = "repurposing-research-program/3"

MONARCH_PATHOLOGY_CATEGORIES = (
    "biolink:DiseaseToPhenotypicFeatureAssociation",
    "biolink:CausalGeneToDiseaseAssociation",
    "biolink:CorrelatedGeneToDiseaseAssociation",
    "biolink:VariantToDiseaseAssociation",
    "biolink:GenotypeToDiseaseAssociation",
    "biolink:DiseaseOrPhenotypicFeatureToLocationAssociation",
)

DISMECH_NODE_TYPES = {
    "mechanistic_hypotheses": "mechanism",
    "pathophysiology": "mechanism",
    "phenotypes": "phenotype",
    "histopathology": "phenotype",
    "imaging_findings": "phenotype",
    "biochemical": "mechanism",
    "genetic": "driver",
    "variants": "driver",
    "environmental": "driver",
    "infectious_agent": "driver",
    "agent_life_cycle": "mechanism",
    "transmission": "mechanism",
}

_BLOCKED_KEY_PARTS = (
    "treatment",
    "therapeutic",
    "drug",
    "compound",
    "clinical_benefit",
    "clinical_trial",
    "dose",
    "efficacy",
    "intervention",
    "medication",
    "pharmacotherapy",
    "regimen",
    "approval",
    "surrogate_endpoint",
    "medical_action",
)
_TREATMENT_TEXT = re.compile(
    r"\b(?:treat(?:ment|ed|ing)?|therap(?:y|ies|eutic(?:s|ally)?)|drugs?|"
    r"medicat(?:ion|ions)|pharmacolog(?:y|ic|ical|ically)|clinical\s+trials?|"
    r"trials?|interventions?|repurpos(?:e|ed|ing)|antisense\s+oligonucleotides?|"
    r"transplant(?:s|ed|ing|ations?)?|"
    r"asos?|efficacy|clinical\s+benefit|approv(?:e|ed|al)|discontinued|placebo|"
    r"randomi[sz]ed|dosing|phase\s*(?:i{1,3}|iv|[1-4]))\b",
    re.IGNORECASE,
)
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


def _raw_cache_path(path: Path) -> str:
    return (Path("sources") / "raw" / path.name).as_posix()


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
            {"path": _raw_cache_path(path), "sha256": _sha256(path.read_bytes())}
            for path in raw_files
        ],
    }
    return entity, list(documents.values()), list(nodes.values()), edges, receipt


def _blocked_key(key: Any) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in _BLOCKED_KEY_PARTS)


def _treatment_names(value: Any) -> set[str]:
    names: set[str] = set()
    records = value if isinstance(value, list) else [value]
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in ("name", "generic_name", "intervention_name"):
            name = record.get(key)
            if isinstance(name, str) and len(name.strip()) >= 4:
                names.add(name.strip())
        treatment_term = record.get("treatment_term")
        if not isinstance(treatment_term, dict):
            continue
        agents = treatment_term.get("therapeutic_agent", [])
        agents = agents if isinstance(agents, list) else [agents]
        for agent in agents:
            if isinstance(agent, str):
                name = agent
            elif isinstance(agent, dict):
                term = agent.get("term")
                name = agent.get("preferred_term") or (
                    term.get("label") if isinstance(term, dict) else None
                )
            else:
                continue
            if isinstance(name, str) and len(name.strip()) >= 4:
                names.add(name.strip())
    return names


def _treatment_terms(value: Any) -> set[str]:
    terms: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if _blocked_key(key):
                names = _treatment_names(item)
                terms.update(names)
                for name in names:
                    words = re.findall(r"[A-Za-z]+", name)
                    acronym = "".join(
                        word[0]
                        for word in words
                        if word.casefold() not in {"a", "an", "and", "for", "of", "or", "the", "to", "with"}
                    ).upper()
                    if 3 <= len(acronym) <= 10:
                        terms.add(acronym)
            terms.update(_treatment_terms(item))
    elif isinstance(value, list):
        for item in value:
            terms.update(_treatment_terms(item))
    return terms


def _treatment_text(value: str, treatment_terms: set[str]) -> bool:
    return bool(_TREATMENT_TEXT.search(value)) or any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", value, re.IGNORECASE)
        for term in treatment_terms
    )


def _sanitize_text(value: str, treatment_terms: set[str]) -> str:
    parts = re.split(r"(?<=[.!?])\s+|[\r\n]+", value)
    return " ".join(
        part.strip()
        for part in parts
        if part.strip() and not _treatment_text(part, treatment_terms)
    )


def _pathology_only(value: Any, treatment_terms: set[str]) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if _blocked_key(key):
                continue
            sanitized = _pathology_only(item, treatment_terms)
            if sanitized not in (None, "", [], {}):
                output[str(key)] = sanitized
        return output
    if isinstance(value, list):
        output = [_pathology_only(item, treatment_terms) for item in value]
        return [item for item in output if item not in (None, "", [], {})]
    if isinstance(value, str):
        return _sanitize_text(value, treatment_terms)
    return value


def _evidence_items(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and reference.strip():
            found.append(
                {
                    "reference": reference.strip(),
                    "reference_title": value.get("reference_title"),
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


def _section_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"value": item} for item in value]
    if isinstance(value, dict):
        return [value]
    return [{"value": value}]


def _preferred_label(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("preferred_term", "label", "name"):
            label = _preferred_label(value.get(key))
            if label:
                return label
        return _preferred_label(value.get("term"))
    return ""


def _node_label(section: str, item: dict[str, Any], index: int) -> str:
    for key in (
        "hypothesis_label",
        "name",
        "label",
        "gene",
        "variant",
        "phenotype_term",
        "finding_term",
        "infectious_agent_term",
    ):
        label = _preferred_label(item.get(key))
        if label:
            return label
    return f"{section.replace('_', ' ').title()} {index + 1}"


def _evidence_documents(
    documents: dict[str, dict[str, Any]], value: Any
) -> list[str]:
    source_ids: list[str] = []
    for evidence_item in _evidence_items(value):
        reference = str(evidence_item["reference"])
        document_id = reference if ":" in reference else _stable_id("DISMECH-REF", reference)
        source_ids.append(document_id)
        _add_document(
            documents,
            {
                "document_id": document_id,
                "title": str(evidence_item.get("reference_title") or reference),
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
    return sorted(set(source_ids))


def _treatment_text_paths(
    value: Any, treatment_terms: set[str], path: str = "$"
) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_treatment_text_paths(item, treatment_terms, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_treatment_text_paths(item, treatment_terms, f"{path}[{index}]"))
    elif isinstance(value, str) and _treatment_text(value, treatment_terms):
        found.append(path)
    return found


def _normalize_dismech_sections(
    sanitized: dict[str, Any], mondo_id: str, file_document_id: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
]:
    documents: dict[str, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    contexts: list[dict[str, Any]] = []
    name_index: dict[str, set[str]] = {}
    node_payloads: list[tuple[str, str, dict[str, Any]]] = []
    gaps: list[str] = []

    for section, node_type in DISMECH_NODE_TYPES.items():
        if section not in sanitized:
            continue
        for index, item in enumerate(_section_items(sanitized[section])):
            label = _node_label(section, item, index)
            node_id = _stable_id(
                "DISMECH-NODE",
                {"mondo_id": mondo_id, "section": section, "label": label},
            )
            source_ids = _evidence_documents(documents, item) or [file_document_id]
            _add_node(
                nodes,
                {
                    "node_id": node_id,
                    "label": label,
                    "node_type": node_type,
                    "description": str(item.get("description") or ""),
                    "source_ids": source_ids,
                    "source_payloads": [item],
                    "source_section": section,
                },
            )
            node_payloads.append((node_id, label, item))
            name_index.setdefault(label.casefold(), set()).add(node_id)

    for section, value in sanitized.items():
        if section in DISMECH_NODE_TYPES:
            continue
        source_ids = _evidence_documents(documents, value) or [file_document_id]
        contexts.append(
            {
                "context_id": _stable_id(
                    "DISMECH-CONTEXT", {"mondo_id": mondo_id, "section": section}
                ),
                "section": section,
                "value": value,
                "source_ids": source_ids,
            }
        )

    edges: list[dict[str, Any]] = []
    for node_id, label, payload in node_payloads:
        for edge_field, relation in (
            ("downstream", "causes_or_contributes_to"),
            ("sequelae", "leads_to"),
        ):
            values = payload.get(edge_field, [])
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                edge = value if isinstance(value, dict) else {"target": value}
                target = str(edge.get("target") or "").strip()
                targets = sorted(name_index.get(target.casefold(), set()))
                if len(targets) != 1:
                    gaps.append(
                        f"DisMech edge target could not be resolved uniquely: {label} -> {target}"
                    )
                    continue
                source_ids = _evidence_documents(documents, edge) or list(
                    nodes[node_id]["source_ids"]
                )
                edges.append(
                    {
                        "edge_id": _stable_id(
                            "DISMECH-EDGE",
                            {
                                "subject": node_id,
                                "relation": relation,
                                "target": targets[0],
                                "index": index,
                            },
                        ),
                        "subject_id": node_id,
                        "relation": relation,
                        "object_id": targets[0],
                        "evidence_summary": str(edge.get("description") or relation),
                        "source_ids": source_ids,
                        "confidence": str(edge.get("causal_link_type") or "curated"),
                    }
                )

    return (
        list(documents.values()),
        list(nodes.values()),
        edges,
        contexts,
        gaps,
    )


def _dismech(
    cache: Path, mondo_id: str
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    list[str],
]:
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
        return [], [], [], [], None, gaps

    page = str(match.get("dismech_url", ""))
    slug = Path(urlparse(page).path).stem
    if not slug:
        gaps.append(f"DisMech mapping for {mondo_id} has no usable disorder path")
        return [], [], [], [], None, gaps
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
    treatment_terms = _treatment_terms(raw)
    sanitized = _pathology_only(raw, treatment_terms)

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
            "raw_path": _raw_cache_path(cache / f"dismech_{slug}.yaml"),
        },
    )
    section_documents, nodes, edges, contexts, section_gaps = _normalize_dismech_sections(
        sanitized, mondo_id, file_document_id
    )
    for row in section_documents:
        _add_document(documents, row)
    gaps.extend(section_gaps)
    leaked = _treatment_text_paths(
        {
            "documents": list(documents.values()),
            "source_nodes": nodes,
            "source_edges": edges,
            "disease_context": contexts,
        },
        treatment_terms,
    )
    if leaked:
        raise SourceError(f"Treatment content survived DisMech normalization: {leaked[:10]}")

    raw_files = [cache / "dismech_mondo_emc.tsv", cache / f"dismech_{slug}.yaml"]
    receipt = {
        "source": "dismech",
        "version": commit_sha,
        "query": {"mondo_id": mondo_id, "slug": slug},
        "record_count": len(nodes),
        "url": page,
        "raw_files": [
            {"path": _raw_cache_path(path), "sha256": _sha256(path.read_bytes())}
            for path in raw_files
        ],
        "pathology_sections": sorted(sanitized),
        "node_sections": sorted(section for section in DISMECH_NODE_TYPES if section in sanitized),
        "context_sections": sorted(section for section in sanitized if section not in DISMECH_NODE_TYPES),
        "excluded_sections": sorted(str(key) for key in raw if _blocked_key(key)),
        "context_count": len(contexts),
    }
    return list(documents.values()), nodes, edges, contexts, receipt, gaps


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
    (
        dismech_docs,
        dismech_nodes,
        dismech_edges,
        dismech_context,
        dismech_receipt,
        gaps,
    ) = _dismech(cache, mondo_id)

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
        "disease_context": sorted(
            dismech_context, key=lambda row: str(row["context_id"])
        ),
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
            "Treatment-oriented sections and fields were excluded, and remaining DisMech text was treatment-redacted before packet construction."
        ],
    }


__all__ = ["SourceError", "fetch_pathology_sources"]
