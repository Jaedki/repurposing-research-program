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
from typing import Any, Iterable, Mapping
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
_TREATMENT_EVENT = re.compile(
    r"\b(?:patients?|participants?|subjects?)\s+"
    r"(?:(?:was|were)\s+)?(?:on|receiv(?:e|ed|ing)|taking|took|given|prescribed)\b|"
    r"\b(?:administ(?:er(?:ed|ing)?|ration)|prescri(?:be|bed|bing|ption)|"
    r"infus(?:e|ed|ing|ion)|inject(?:ed|ing|ion))\b",
    re.IGNORECASE,
)
SENTENCE_DECISIONS = frozenset(
    {
        "retain_pathology",
        "exclude_treatment",
        "exclude_mixed",
        "exclude_ambiguous",
    }
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


def _split_sentences(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", value)
        if part.strip()
    ]


def _treatment_signals(value: str, treatment_terms: set[str]) -> tuple[str, ...]:
    signals: list[str] = []
    if any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", value, re.IGNORECASE)
        for term in treatment_terms
    ):
        signals.append("named_intervention")
    if _TREATMENT_EVENT.search(value):
        signals.append("treatment_event")
    if _TREATMENT_TEXT.search(value):
        signals.append("treatment_language")
    return tuple(signals)


def _treatment_text(value: str, treatment_terms: set[str]) -> bool:
    return bool(_treatment_signals(value, treatment_terms))


def _sentence_id(value: str) -> str:
    return _stable_id("DISMECH-SENTENCE", value)


def _flagged_sentences(
    value: Any,
    treatment_terms: set[str],
    path: str = "$",
) -> list[dict[str, Any]]:
    flagged: dict[str, dict[str, Any]] = {}

    def visit(current: Any, current_path: str) -> None:
        if isinstance(current, dict):
            for key, item in current.items():
                if not _blocked_key(key):
                    visit(item, f"{current_path}.{key}")
        elif isinstance(current, list):
            for index, item in enumerate(current):
                visit(item, f"{current_path}[{index}]")
        elif isinstance(current, str):
            for sentence in _split_sentences(current):
                signals = _treatment_signals(sentence, treatment_terms)
                if not signals:
                    continue
                sentence_id = _sentence_id(sentence)
                row = flagged.setdefault(
                    sentence_id,
                    {
                        "sentence_id": sentence_id,
                        "sentence": sentence,
                        "signals": set(),
                        "paths": set(),
                    },
                )
                row["signals"].update(signals)
                row["paths"].add(current_path)

    visit(value, path)
    return [
        {
            **row,
            "signals": sorted(row["signals"]),
            "paths": sorted(row["paths"]),
        }
        for _, row in sorted(flagged.items())
    ]


def _sanitize_text(
    value: str,
    treatment_terms: set[str],
    sentence_decisions: Mapping[str, str],
) -> str:
    retained: list[str] = []
    for sentence in _split_sentences(value):
        if not _treatment_text(sentence, treatment_terms):
            retained.append(sentence)
            continue
        sentence_id = _sentence_id(sentence)
        decision = sentence_decisions.get(sentence_id)
        if decision not in SENTENCE_DECISIONS:
            raise SourceError(f"Missing valid adjudication for flagged sentence {sentence_id}")
        if decision == "retain_pathology":
            retained.append(sentence)
    return " ".join(retained)


def _pathology_only(
    value: Any,
    treatment_terms: set[str],
    sentence_decisions: Mapping[str, str],
) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if _blocked_key(key):
                continue
            sanitized = _pathology_only(item, treatment_terms, sentence_decisions)
            if sanitized not in (None, "", [], {}):
                output[str(key)] = sanitized
        return output
    if isinstance(value, list):
        output = [
            _pathology_only(item, treatment_terms, sentence_decisions)
            for item in value
        ]
        return [item for item in output if item not in (None, "", [], {})]
    if isinstance(value, str):
        return _sanitize_text(value, treatment_terms, sentence_decisions)
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


def _unapproved_flagged_paths(
    value: Any,
    treatment_terms: set[str],
    sentence_decisions: Mapping[str, str],
    path: str = "$",
) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(
                _unapproved_flagged_paths(
                    item, treatment_terms, sentence_decisions, f"{path}.{key}"
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(
                _unapproved_flagged_paths(
                    item, treatment_terms, sentence_decisions, f"{path}[{index}]"
                )
            )
    elif isinstance(value, str):
        for sentence in _split_sentences(value):
            if (
                _treatment_text(sentence, treatment_terms)
                and sentence_decisions.get(_sentence_id(sentence)) != "retain_pathology"
            ):
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


def _load_dismech(
    cache: Path, mondo_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
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
        return None, None, gaps

    page = str(match.get("dismech_url", ""))
    slug = Path(urlparse(page).path).stem
    if not slug:
        gaps.append(f"DisMech mapping for {mondo_id} has no usable disorder path")
        return None, None, gaps
    yaml_url = f"{DISMECH_RAW}/{commit_sha}/kb/disorders/{quote(slug)}.yaml"
    yaml_path = cache / f"dismech_{slug}.yaml"
    yaml_payload = _fetch(yaml_url, yaml_path)
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
    return raw, {
        "commit_sha": commit_sha,
        "page": page,
        "slug": slug,
        "yaml_path": yaml_path,
    }, gaps


def _dismech(
    cache: Path,
    mondo_id: str,
    sentence_decisions: Mapping[str, str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    list[str],
]:
    raw, metadata, gaps = _load_dismech(cache, mondo_id)
    if raw is None or metadata is None:
        if sentence_decisions:
            raise SourceError("Sentence adjudication was supplied without a DisMech record")
        return [], [], [], [], None, gaps

    treatment_terms = _treatment_terms(raw)
    flagged = _flagged_sentences(raw, treatment_terms)
    expected_ids = {str(row["sentence_id"]) for row in flagged}
    supplied_ids = set(map(str, sentence_decisions))
    if supplied_ids != expected_ids:
        raise SourceError(
            "Sentence adjudication must cover every flagged DisMech sentence exactly; "
            f"missing={sorted(expected_ids - supplied_ids)}, "
            f"unknown={sorted(supplied_ids - expected_ids)}"
        )
    invalid = {
        str(sentence_id): decision
        for sentence_id, decision in sentence_decisions.items()
        if decision not in SENTENCE_DECISIONS
    }
    if invalid:
        raise SourceError(f"Sentence adjudication contains invalid decisions: {invalid}")
    sanitized = _pathology_only(raw, treatment_terms, sentence_decisions)

    documents: dict[str, dict[str, Any]] = {}
    commit_sha = str(metadata["commit_sha"])
    slug = str(metadata["slug"])
    page = str(metadata["page"])
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
    leaked = _unapproved_flagged_paths(
        {
            "documents": list(documents.values()),
            "source_nodes": nodes,
            "source_edges": edges,
            "disease_context": contexts,
        },
        treatment_terms,
        sentence_decisions,
    )
    if leaked:
        raise SourceError(
            f"Unapproved flagged content survived DisMech normalization: {leaked[:10]}"
        )

    decision_counts = {
        decision: sum(value == decision for value in sentence_decisions.values())
        for decision in sorted(SENTENCE_DECISIONS)
    }
    unresolved_count = (
        decision_counts["exclude_mixed"] + decision_counts["exclude_ambiguous"]
    )
    if unresolved_count:
        gaps.append(
            "DisMech sanitation excluded "
            f"{unresolved_count} mixed or ambiguous flagged sentence(s); "
            "see the accepted pathology-source adjudication result."
        )

    raw_files = [
        cache / "dismech_commit.json",
        cache / "dismech_mondo_emc.tsv",
        Path(metadata["yaml_path"]),
    ]
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
        "node_sections": sorted(
            section for section in DISMECH_NODE_TYPES if section in sanitized
        ),
        "context_sections": sorted(
            section for section in sanitized if section not in DISMECH_NODE_TYPES
        ),
        "excluded_sections": sorted(str(key) for key in raw if _blocked_key(key)),
        "context_count": len(contexts),
    }
    return list(documents.values()), nodes, edges, contexts, receipt, gaps


def screen_pathology_sources(
    run_root: Path,
    disease: str,
    mondo_hint: str | None = None,
) -> dict[str, Any]:
    """Collect one compact, deduplicated sentence batch for bounded adjudication."""
    cache = run_root / "sources" / "raw"
    entity, _, _, _, _ = _monarch(cache, disease, mondo_hint)
    mondo_id = str(entity["id"])
    raw, _, _ = _load_dismech(cache, mondo_id)
    flagged = _flagged_sentences(raw, _treatment_terms(raw)) if raw is not None else []
    return {
        "stage": "pathology_source_screening",
        "status": "complete",
        "resolved_disease": {
            "mondo_id": mondo_id,
            "name": entity.get("name") or disease,
        },
        "records": {"flagged_sentences": flagged},
        "gaps": [],
        "notes": [
            "Only free-text sentences flagged by deterministic treatment signals enter "
            "the bounded adjudication packet."
        ],
    }


def fetch_pathology_sources(
    run_root: Path,
    disease: str,
    mondo_hint: str | None,
    sentence_decisions: Mapping[str, str],
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
    ) = _dismech(cache, mondo_id, sentence_decisions)

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
            "Treatment-oriented sections and fields were excluded. Flagged free text was "
            "retained only after bounded pathology-only adjudication."
        ],
    }


__all__ = [
    "SENTENCE_DECISIONS",
    "SourceError",
    "fetch_pathology_sources",
    "screen_pathology_sources",
]
