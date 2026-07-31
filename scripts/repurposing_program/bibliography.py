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
from .evidence import _normalized_title, _rows, _year
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


def _id_converter_records(root: Path, document_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    normalized = sorted({
        value
        for document_id in document_ids
        if (value := _normalized_publication_id(document_id)) is not None
    })
    by_alias: dict[str, dict[str, Any]] = {}
    for batch in _batches(normalized):
        query = urlencode({
            "ids": ",".join(value.split(":", 1)[1] for value in batch),
            "format": "json",
            "versions": "no",
            "tool": "repurposing-research-program",
        })
        response = _bibliographic_request(
            root,
            "ncbi-idconv",
            f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?{query}",
        )
        records = response.get("records")
        if not isinstance(records, list):
            raise ProgramError("NCBI identifier conversion response has no records list")
        for record in records:
            if not isinstance(record, dict) or record.get("status") == "error":
                continue
            aliases = []
            if record.get("pmid"):
                aliases.append(f"PMID:{record['pmid']}")
            if record.get("pmcid"):
                aliases.append(f"PMCID:{str(record['pmcid']).upper()}")
            if record.get("doi"):
                aliases.append(f"DOI:{str(record['doi']).lower()}")
            for alias in aliases:
                by_alias[alias] = record
    return by_alias


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
    publication_ids = [
        str(row["document_id"])
        for row in documents
        if _normalized_publication_id(row.get("document_id")) is not None
    ]
    converted = _id_converter_records(root, publication_ids)
    identities: dict[str, dict[str, str]] = {}
    for document_id in publication_ids:
        normalized = _normalized_publication_id(document_id)
        assert normalized is not None
        record = converted.get(normalized, {})
        identity: dict[str, str] = {}
        if record.get("pmid"):
            identity["pmid"] = str(record["pmid"])
        if record.get("pmcid"):
            identity["pmcid"] = str(record["pmcid"]).upper()
        if record.get("doi"):
            identity["doi"] = str(record["doi"]).lower()
        prefix, value = normalized.split(":", 1)
        identity.setdefault(prefix.casefold(), value)
        identities[document_id] = identity

    pubmed = _ncbi_summaries(root, "pubmed", (
        identity["pmid"] for identity in identities.values() if identity.get("pmid")
    ))
    pmc = _ncbi_summaries(root, "pmc", (
        identity["pmcid"].removeprefix("PMC")
        for identity in identities.values()
        if identity.get("pmcid") and not identity.get("pmid") and not identity.get("doi")
    ))

    resolved: dict[str, dict[str, Any]] = {}
    for document_id, identity in identities.items():
        aliases = []
        if identity.get("pmid"):
            aliases.append(f"PMID:{identity['pmid']}")
        if identity.get("pmcid"):
            aliases.append(f"PMCID:{identity['pmcid']}")
        if identity.get("doi"):
            aliases.append(f"DOI:{identity['doi']}")
        if identity.get("pmid") and identity["pmid"] in pubmed:
            metadata = _summary_metadata(
                pubmed[identity["pmid"]], source="PubMed", aliases=aliases
            )
        elif identity.get("doi"):
            metadata = _doi_metadata(root, identity["doi"])
            metadata["identifier_aliases"] = sorted({
                *metadata["identifier_aliases"], *aliases,
            })
        elif identity.get("pmcid"):
            key = identity["pmcid"].removeprefix("PMC")
            if key not in pmc:
                raise ProgramError(f"Canonical metadata was not found for {document_id}")
            metadata = _summary_metadata(pmc[key], source="PubMed Central", aliases=aliases)
        else:
            raise ProgramError(f"Canonical metadata was not found for {document_id}")
        if not metadata["title"]:
            raise ProgramError(f"Canonical metadata has no title for {document_id}")
        alias_set = set(map(str, metadata["identifier_aliases"]))
        canonical_id = next(
            (value for prefix in ("PMID:", "DOI:", "PMCID:")
             for value in sorted(alias_set) if value.startswith(prefix)),
            _normalized_publication_id(document_id),
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
        if _normalized_title(row.get("title")) != _normalized_title(canonical["title"]):
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
