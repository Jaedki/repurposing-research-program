"""Pure evidence-record validation, projection, citation, and merge helpers."""

from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from .contracts import (
    EVIDENCE_PASSAGE_FIELDS,
    RESEARCH_DOCUMENT_BASE_FIELDS,
    RESEARCH_DOCUMENT_PASSAGES_FIELD,
    RESEARCH_DOCUMENT_REQUIRED_FIELDS,
    _CITATION_FIELDS,
)
from .errors import ProgramError
from .storage import _canonical_bytes

_DATABASE_RECORD_ID = re.compile(
    r"^(?:(?:HPA|UNIPROT(?:KB)?|DAILYMED|PUBCHEM):\S+|https://reactome\.org/content/detail/R-[A-Z]{3}-\d+|https://www\.ebi\.ac\.uk/QuickGO/term/GO:\d+|https://www\.bgee\.org/gene/ENSG\d+)$",
    re.IGNORECASE,
)
_DATABASE_TITLE_STOPWORDS = {
    "human", "protein", "atlas", "gene", "entry", "tissue", "expression", "summary",
    "single", "cell", "uniprotkb", "reactome", "plasma", "membrane", "bgee",
    "healthy", "wild", "type", "conditions", "homo", "sapiens",
    "tablet", "tablets", "film", "coated", "injection", "full", "prescribing",
    "information", "compound", "record",
}


def _normalized_title(value: Any) -> str:
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _normalized_document_title(document_id: str, value: Any) -> str:
    """Normalize a title while ignoring a redundant database-record locator."""
    title = _normalized_title(value)
    if re.fullmatch(
        r"(?:PMID:\d+|PMCID:PMC\d+|DOI:10\.\d{4,9}/\S+)",
        document_id,
        flags=re.IGNORECASE,
    ):
        # Submission-time bibliographic verification uses this compact form so
        # formatting variants such as ``5-HT2`` and ``5-HT(2)`` can both match
        # the same authoritative title.  Aggregation must apply the identical
        # equivalence rule to already-verified immutable results.
        return title.replace(" ", "")
    match = re.fullmatch(r"PUBCHEM:(\d+)", document_id, flags=re.IGNORECASE)
    if match:
        suffix = f" pubchem cid {match.group(1)}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def _equivalent_document_titles(document_id: str, left: Any, right: Any) -> bool:
    """Accept minor title variants while retaining a wrong-title sanity check."""
    normalized = [
        _normalized_document_title(document_id, value) for value in (left, right)
    ]
    if normalized[0] == normalized[1]:
        return True
    if _DATABASE_RECORD_ID.fullmatch(document_id):
        identifier_tokens = set(_normalized_title(document_id).split())
        tokens = [
            {token for token in title.split()
             if (len(token) >= 4 or any(char.isdigit() for char in token))
             and token not in _DATABASE_TITLE_STOPWORDS | identifier_tokens}
            for title in normalized
        ]
        return bool(tokens[0] and (tokens[0] <= tokens[1] or tokens[1] <= tokens[0]))
    return min(map(len, normalized)) >= 12 and SequenceMatcher(
        None, *normalized, autojunk=False
    ).ratio() >= 0.9


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
        label = f"documents[{index}]"
        missing = [
            field for field in RESEARCH_DOCUMENT_REQUIRED_FIELDS if field not in row
        ]
        if missing:
            raise ProgramError(f"{label} is missing fields: {', '.join(missing)}")
        empty = [
            field
            for field in RESEARCH_DOCUMENT_BASE_FIELDS
            if not str(row[field]).strip()
        ]
        if empty:
            raise ProgramError(f"{label} has empty required fields: {', '.join(empty)}")
        passages = row[RESEARCH_DOCUMENT_PASSAGES_FIELD]
        if not isinstance(passages, list) or not passages:
            raise ProgramError(
                f"{label}.{RESEARCH_DOCUMENT_PASSAGES_FIELD} must contain inspectable source content"
            )
        for passage_index, passage in enumerate(passages):
            passage_label = (
                f"{label}.{RESEARCH_DOCUMENT_PASSAGES_FIELD}[{passage_index}]"
            )
            if not isinstance(passage, dict) or set(passage) != set(
                EVIDENCE_PASSAGE_FIELDS
            ):
                raise ProgramError(
                    f"{passage_label} must contain exactly "
                    f"{' and '.join(EVIDENCE_PASSAGE_FIELDS)}"
                )
            if any(
                not isinstance(passage[field], str) or not passage[field].strip()
                for field in EVIDENCE_PASSAGE_FIELDS
            ):
                raise ProgramError(
                    f"{passage_label}.{' and '.join(EVIDENCE_PASSAGE_FIELDS)} "
                    "must be non-empty strings"
                )


def _document_has_inspectable_content(row: Mapping[str, Any]) -> bool:
    passages = row.get(RESEARCH_DOCUMENT_PASSAGES_FIELD)
    if isinstance(passages, list) and any(
        isinstance(passage, dict)
        and all(
            isinstance(passage.get(field), str) and passage[field].strip()
            for field in EVIDENCE_PASSAGE_FIELDS
        )
        for passage in passages
    ):
        return True
    if any(
        isinstance(row.get(field), str) and row[field].strip()
        for field in ("abstract", "raw_path", "supporting_text")
    ):
        return True
    snippets = row.get("snippets")
    if isinstance(snippets, list) and any(
        isinstance(value, str) and value.strip() for value in snippets
    ):
        return True
    supports = row.get("supports")
    if isinstance(supports, list) and any(
        isinstance(value, str) and value.strip() for value in supports
    ):
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
        if source_ids is None or source_ids & {str(row["document_id"]), *map(str, row.get("identifier_aliases", []))}
    ]


def _document_alias_index(
    documents: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for row in documents:
        document_id = str(row["document_id"])
        aliases = {document_id, *map(str, row.get("identifier_aliases", []))}
        for alias in aliases:
            prior = index.setdefault(alias, row)
            if str(prior["document_id"]) != document_id:
                raise ProgramError(f"Publication alias {alias} maps to multiple retained documents")
    return index


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


def _merge_documents(rows: Iterable[dict[str, Any]], *, canonical_publications: bool = False) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    identity_fields = {"title", "canonical_publication_id"}
    for row in rows:
        document_id = str(row.get("document_id", "")).strip()
        if not document_id:
            raise ProgramError("documents.document_id is required")
        key = str(row.get("canonical_publication_id") or document_id) if canonical_publications else document_id
        current = merged.setdefault(key, {"document_id": key})
        if canonical_publications:
            row = {**row, "identifier_aliases": sorted({document_id, *row.get("identifier_aliases", [])})}
        for field, value in row.items():
            if field == "document_id" or value in (None, "", []):
                continue
            conflict = field in identity_fields and field in current and current[field] != value
            if field == "title" and conflict:
                conflict = not canonical_publications and not _equivalent_document_titles(
                    document_id, current[field], value
                )
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

    Asta and Undermind papers are projected selectively into the frozen graph after curation.
    Global question and connection research is not unioned directly; candidate review aggregation
    carries forward only sources cited by a completed candidate hypothesis packet.
    """
    return _merge_documents(
        (
            row
            for stage, result in results.items()
            if stage not in {
                "pathology_landscape_scan", "pathology_coverage_expansion",
                "pathology_question_research", "pathology_hypothesis_synthesis",
            }
            if isinstance(result.get("records"), Mapping)
            for row in _cited_documents(result["records"])
        ), canonical_publications=True
    )
