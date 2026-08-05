"""Pure evidence-record validation, projection, citation, and merge helpers."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .contracts import _CITATION_FIELDS
from .errors import ProgramError
from .storage import _canonical_bytes


def _normalized_title(value: Any) -> str:
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _year(value: Any) -> int | None:
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b", str(value))
    return int(match.group(1)) if match else None


def _rows(records: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = records.get(name)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ProgramError(f"records.{name} must be a list of objects")
    return [dict(row) for row in value]


def _find(rows: Iterable[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get(field)) == value]
    if len(matches) != 1:
        raise ProgramError(f"Expected exactly one {field}={value} record")
    return matches[0]


def _merge_text(*values: Any) -> str:
    parts = {
        part.strip()
        for value in values
        for part in str(value).split(" | ")
        if part.strip()
    }
    return " | ".join(sorted(parts))


def _validate_research_document_content(records: Mapping[str, Any]) -> None:
    for index, row in enumerate(_rows(records, "documents")):
        passages = row.get("evidence_passages")
        if not isinstance(passages, list) or not passages:
            raise ProgramError(
                f"documents[{index}].evidence_passages must contain inspectable source content"
            )
        for passage_index, passage in enumerate(passages):
            label = f"documents[{index}].evidence_passages[{passage_index}]"
            if not isinstance(passage, dict) or set(passage) != {"text", "locator"}:
                raise ProgramError(f"{label} must contain exactly text and locator")
            if not str(passage["text"]).strip() or not str(passage["locator"]).strip():
                raise ProgramError(f"{label}.text and locator must be non-empty")


def _document_has_inspectable_content(row: Mapping[str, Any]) -> bool:
    passages = row.get("evidence_passages")
    if isinstance(passages, list) and any(
        isinstance(passage, dict) and str(passage.get("text", "")).strip()
        for passage in passages
    ):
        return True
    if str(row.get("abstract", "")).strip() or str(row.get("raw_path", "")).strip():
        return True
    if str(row.get("supporting_text", "")).strip():
        return True
    snippets = row.get("snippets")
    if isinstance(snippets, list) and any(str(value).strip() for value in snippets):
        return True
    supports = row.get("supports")
    if isinstance(supports, list) and any(str(value).strip() for value in supports):
        return True
    structured = row.get("structured_content")
    if isinstance(structured, (Mapping, list)) and bool(structured):
        return True
    return False


def _source_index(
    documents: list[dict[str, Any]], source_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    fields = (
        "document_id", "canonical_publication_id", "identifier_aliases", "title",
        "submitted_title", "year", "journal", "authors", "source", "metadata_source",
        "citation", "url", "raw_path", "abstract", "evidence_passages", "supporting_text",
        "structured_content", "snippets", "supports",
    )
    return [
        {key: row[key] for key in fields if key in row}
        for row in documents
        if source_ids is None or str(row["document_id"]) in source_ids
    ]


def _merge_unique(
    rows: Iterable[dict[str, Any]], id_field: str, label: str
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(id_field, "")).strip()
        if not key:
            raise ProgramError(f"{label}.{id_field} is required")
        if key in merged and merged[key] != row:
            raise ProgramError(f"Conflicting {label} records share {id_field}={key}")
        merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _merge_documents(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    identity_fields = {"title", "year", "canonical_publication_id"}
    for row in rows:
        document_id = str(row.get("document_id", "")).strip()
        if not document_id:
            raise ProgramError("documents.document_id is required")
        current = merged.setdefault(document_id, {"document_id": document_id})
        for field, value in row.items():
            if field == "document_id" or value in (None, "", []):
                continue
            conflict = field in identity_fields and field in current and current[field] != value
            if field == "title" and conflict:
                conflict = _normalized_title(current[field]) != _normalized_title(value)
            if field == "year" and conflict:
                conflict = _year(current[field]) != _year(value)
            if conflict:
                raise ProgramError(
                    f"Conflicting document metadata for {document_id}: {field}"
                )
            if isinstance(value, list):
                prior = current.get(field, [])
                if not isinstance(prior, list):
                    raise ProgramError(f"documents.{field} changes type")
                values = {_canonical_bytes(item): item for item in [*prior, *value]}
                value = [values[key] for key in sorted(values)]
            elif field in current and current[field] != value:
                continue
            current[field] = value
    return [merged[key] for key in sorted(merged)]


def _cited_ids(value: Any) -> set[str]:
    """Collect citations recursively without treating document metadata as evidence."""
    cited: set[str] = set()

    def visit(current: Any) -> None:
        if isinstance(current, Mapping):
            for field, nested in current.items():
                if field == "documents":
                    continue
                if field in _CITATION_FIELDS:
                    if isinstance(nested, list):
                        cited.update(str(item) for item in nested)
                    continue
                visit(nested)
        elif isinstance(current, list):
            for item in current:
                visit(item)

    visit(value)
    return cited


def _select_cited_documents(
    documents: Iterable[dict[str, Any]], citations: Any
) -> list[dict[str, Any]]:
    cited_ids = _cited_ids(citations)
    return [
        dict(row)
        for row in documents
        if str(row.get("document_id", "")) in cited_ids
    ]


def _cited_documents(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents = records.get("documents", [])
    if not isinstance(documents, list) or any(not isinstance(row, dict) for row in documents):
        raise ProgramError("records.documents must be a list of objects")
    return _select_cited_documents(documents, records)


def _all_documents(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the retained downstream corpus, not every accepted discovery document.

    Landscape papers are projected selectively into the frozen graph after curation. Directly
    unioning the scan result here would reintroduce papers supporting excluded proposals.
    """
    return _merge_documents(
        (
            row
            for stage, result in results.items()
            if stage != "pathology_landscape_scan"
            if isinstance(result.get("records"), Mapping)
            for row in _cited_documents(result["records"])
        )
    )
