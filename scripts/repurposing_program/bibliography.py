"""Canonical publication identity, metadata transport, and validation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .contracts import _PUBLICATION_ID
from .errors import ProgramError
from .evidence import _equivalent_document_titles, _rows, _year
from .storage import _canonical_bytes, _read_json, _sha256, _write_json


def _normalized_publication_id(value: Any) -> str | None:
    document_id = str(value).strip()
    match = _PUBLICATION_ID.fullmatch(document_id)
    if not match:
        return None
    prefix, identifier = document_id.split(":", 1)
    prefix = prefix.upper()
    if prefix == "DOI":
        identifier = identifier.lower()
    elif prefix == "PMCID":
        identifier = identifier.upper()
    return f"{prefix}:{identifier}"


def _bibliographic_get(url: str, *, accept: str = "application/json") -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "repurposing-research-program/5",
        },
        method="GET",
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise ProgramError(f"Bibliographic metadata request failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ProgramError(f"Bibliographic metadata request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramError(f"Bibliographic metadata returned invalid JSON: {exc}") from exc
        else:
            if not isinstance(result, dict):
                raise ProgramError("Bibliographic metadata returned a non-object response")
            return result
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _bibliographic_request(
    root: Path,
    kind: str,
    url: str,
    *,
    accept: str = "application/json",
) -> dict[str, Any]:
    token = _sha256(_canonical_bytes({"url": url, "accept": accept}))[:24]
    path = root / "sources" / "raw" / "bibliography" / f"{kind}-{token}.json"
    if path.exists():
        return _read_json(path)
    result = _bibliographic_get(url, accept=accept)
    _write_json(path, result)
    return result


def _batches(values: list[str], size: int = 200) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _ncbi_summaries(
    root: Path, database: str, identifiers: Iterable[str]
) -> dict[str, dict[str, Any]]:
    values = sorted({str(value) for value in identifiers if str(value)})
    output: dict[str, dict[str, Any]] = {}
    for batch in _batches(values):
        query = urlencode({
            "db": database,
            "id": ",".join(batch),
            "retmode": "json",
            "tool": "repurposing-research-program",
        })
        response = _bibliographic_request(
            root,
            f"ncbi-{database}-summary",
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{query}",
        )
        payload = response.get("result")
        if not isinstance(payload, dict):
            raise ProgramError(f"NCBI {database} metadata response has no result object")
        for identifier in batch:
            row = payload.get(identifier)
            if isinstance(row, dict):
                output[identifier] = row
    return output


def _summary_metadata(
    row: Mapping[str, Any],
    *,
    source: str,
    aliases: Iterable[str],
) -> dict[str, Any]:
    article_ids = row.get("articleids", [])
    resolved_aliases = set(aliases)
    if isinstance(article_ids, list):
        for item in article_ids:
            if not isinstance(item, dict) or not item.get("value"):
                continue
            id_type = str(item.get("idtype", "")).casefold()
            value = str(item["value"])
            if id_type == "pubmed":
                resolved_aliases.add(f"PMID:{value}")
            elif id_type == "pmc":
                resolved_aliases.add(f"PMCID:{value.upper()}")
            elif id_type == "doi":
                resolved_aliases.add(f"DOI:{value.lower()}")
    authors = row.get("authors", [])
    author_names = [
        str(author.get("name"))
        for author in authors
        if isinstance(author, dict) and author.get("name")
    ] if isinstance(authors, list) else []
    return {
        "title": str(row.get("title") or "").strip(),
        "year": _year(row.get("pubdate")),
        "journal": str(row.get("fulljournalname") or row.get("source") or "").strip(),
        "authors": author_names,
        "identifier_aliases": sorted(resolved_aliases),
        "metadata_source": source,
    }


def _doi_metadata(root: Path, doi: str) -> dict[str, Any]:
    response = _bibliographic_request(
        root,
        "doi-csl",
        f"https://doi.org/{quote(doi, safe='/')}",
        accept="application/vnd.citationstyles.csl+json",
    )
    authors = response.get("author", [])
    author_names = []
    if isinstance(authors, list):
        for author in authors:
            if not isinstance(author, dict):
                continue
            name = " ".join(
                value for value in (str(author.get("given") or "").strip(),
                                    str(author.get("family") or "").strip()) if value
            )
            if name:
                author_names.append(name)
    issued = response.get("issued", {})
    date_parts = issued.get("date-parts", []) if isinstance(issued, dict) else []
    year = None
    if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
        try:
            year = int(date_parts[0][0])
        except (TypeError, ValueError):
            year = None
    title = response.get("title")
    if isinstance(title, list):
        title = title[0] if title else ""
    journal = response.get("container-title")
    if isinstance(journal, list):
        journal = journal[0] if journal else ""
    return {
        "title": str(title or "").strip(),
        "year": year,
        "journal": str(journal or "").strip(),
        "authors": author_names,
        "identifier_aliases": [f"DOI:{doi.lower()}"],
        "metadata_source": "DOI",
    }


def _resolve_bibliographic_metadata(
    root: Path, documents: Iterable[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    identities = {
        str(row["document_id"]): normalized
        for row in documents
        if (normalized := _normalized_publication_id(row.get("document_id"))) is not None
    }
    pubmed = _ncbi_summaries(root, "pubmed", (
        normalized.removeprefix("PMID:")
        for normalized in identities.values()
        if normalized.startswith("PMID:")
    ))
    pmc = _ncbi_summaries(root, "pmc", (
        normalized.removeprefix("PMCID:PMC")
        for normalized in identities.values()
        if normalized.startswith("PMCID:PMC")
    ))

    resolved: dict[str, dict[str, Any]] = {}
    for document_id, normalized in identities.items():
        prefix, value = normalized.split(":", 1)
        if prefix == "PMID" and value in pubmed:
            metadata = _summary_metadata(
                pubmed[value], source="PubMed", aliases=[normalized]
            )
        elif prefix == "PMCID":
            key = value.removeprefix("PMC")
            if key not in pmc:
                raise ProgramError(f"Canonical metadata was not found for {document_id}")
            metadata = _summary_metadata(
                pmc[key], source="PubMed Central", aliases=[normalized]
            )
        elif prefix == "DOI":
            metadata = _doi_metadata(root, value)
        else:
            raise ProgramError(f"Canonical metadata was not found for {document_id}")
        if not metadata["title"]:
            raise ProgramError(f"Canonical metadata has no title for {document_id}")
        alias_set = set(map(str, metadata["identifier_aliases"]))
        canonical_id = next(
            (alias for alias_prefix in ("DOI:", "PMID:", "PMCID:")
             for alias in sorted(alias_set) if alias.startswith(alias_prefix)),
            normalized,
        )
        resolved[document_id] = {
            **metadata,
            "canonical_publication_id": canonical_id,
            "identifier_aliases": sorted(alias_set),
        }
    return resolved


def _canonicalize_documents(
    root: Path,
    documents: Iterable[dict[str, Any]],
    *,
    verify_titles: bool,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in documents]
    metadata = _resolve_bibliographic_metadata(root, rows)
    output: list[dict[str, Any]] = []
    for row in rows:
        document_id = str(row["document_id"])
        canonical = metadata.get(document_id)
        if canonical is None:
            output.append(row)
            continue
        if not _equivalent_document_titles(
            document_id, row.get("title"), canonical["title"]
        ):
            if verify_titles:
                raise ProgramError(
                    f"Document metadata mismatch for {document_id}: submitted title "
                    f"{row.get('title')!r}; canonical title {canonical['title']!r}"
                )
            row["submitted_title"] = row.get("title")
        row.update({key: value for key, value in canonical.items() if value not in (None, "", [])})
        output.append(row)
    return output


def _validate_bibliographic_documents(root: Path, records: Mapping[str, Any]) -> None:
    documents = _rows(records, "documents")
    canonicalized = _canonicalize_documents(root, documents, verify_titles=True)
    seen: dict[str, str] = {}
    for row in canonicalized:
        canonical_id = row.get("canonical_publication_id")
        if canonical_id is None:
            continue
        document_id = str(row["document_id"])
        prior = seen.setdefault(str(canonical_id), document_id)
        if prior != document_id:
            raise ProgramError(
                f"Documents {prior} and {document_id} identify the same publication "
                f"{canonical_id}; return one canonical citation"
            )
