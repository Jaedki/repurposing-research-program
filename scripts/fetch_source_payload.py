#!/usr/bin/env python3
"""Fetch supported source pages directly to disk and emit only compact receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from compact_source_payload import compact_payload


USER_AGENT = "repurposing-research-program/3.1 (local research workflow)"


def _fetch_json(url: str) -> tuple[dict[str, Any], dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
        headers = {key.lower(): value for key, value in response.headers.items()}
    if not isinstance(payload, dict):
        raise ValueError("Source response was not a JSON object")
    return payload, headers


def _write_raw(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def normalize_pubmed_summary(payload: dict[str, Any], query_id: str) -> list[dict[str, Any]]:
    result = payload.get("result", {})
    uids = result.get("uids", []) if isinstance(result, dict) else []
    rows = []
    for uid in uids:
        item = result.get(str(uid), {})
        if not isinstance(item, dict):
            continue
        pubdate = str(item.get("pubdate", ""))
        year_match = re.search(r"\b(19|20)\d{2}\b", pubdate)
        rows.append(
            {
                "canonical_identifier": f"PMID:{uid}",
                "identifier_type": "PMID",
                "title": str(item.get("title", "")).rstrip("."),
                "year": int(year_match.group(0)) if year_match else pubdate,
                "source_kind": "literature_metadata",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "query_id": query_id,
            }
        )
    return rows


def normalize_uniprot(payload: dict[str, Any], query_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        accession = str(item.get("primaryAccession", ""))
        description = item.get("proteinDescription", {})
        recommended = description.get("recommendedName", {}) if isinstance(description, dict) else {}
        full_name = recommended.get("fullName", {}) if isinstance(recommended, dict) else {}
        title = str(full_name.get("value", "")) if isinstance(full_name, dict) else ""
        if accession:
            rows.append(
                {
                    "canonical_identifier": accession,
                    "identifier_type": "UniProtKB",
                    "title": title or accession,
                    "source_kind": "authoritative_database",
                    "url": f"https://rest.uniprot.org/uniprotkb/{accession}",
                    "query_id": query_id,
                }
            )
    return rows


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("pubmed", "uniprot"))
    parser.add_argument("run_folder")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--cursor", default="")
    parser.add_argument("--mode", choices=("discovery", "verification"), default="discovery")
    args = parser.parse_args()
    root = Path(args.run_folder).expanduser().resolve()
    raw_dir = root / "raw_sources" / re.sub(r"[^A-Za-z0-9_.-]+", "_", args.query_id)
    raw_dir.mkdir(parents=True, exist_ok=True)

    continuation = ""
    total_count = 0
    if args.source == "pubmed":
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
            {"db": "pubmed", "term": args.query, "retmode": "json", "retmax": args.page_size, "retstart": args.offset}
        )
        search_payload, _ = _fetch_json(search_url)
        _write_raw(raw_dir / f"search_{args.offset:06d}.json", search_payload)
        search_result = search_payload.get("esearchresult", {})
        ids = [str(value) for value in search_result.get("idlist", [])]
        total_count = int(search_result.get("count", 0))
        summary_payload: dict[str, Any] = {"result": {"uids": []}}
        if ids:
            summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode(
                {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
            )
            summary_payload, _ = _fetch_json(summary_url)
        _write_raw(raw_dir / f"summary_{args.offset:06d}.json", summary_payload)
        records = normalize_pubmed_summary(summary_payload, args.query_id)
        next_offset = args.offset + len(ids)
        continuation = str(next_offset) if next_offset < total_count else ""
    else:
        params = {"query": args.query, "format": "json", "size": args.page_size}
        if args.cursor:
            params["cursor"] = args.cursor
        payload, headers = _fetch_json("https://rest.uniprot.org/uniprotkb/search?" + urllib.parse.urlencode(params))
        _write_raw(raw_dir / f"page_{_hash(args.cursor)[:12] or 'first'}.json", payload)
        records = normalize_uniprot(payload, args.query_id)
        total_count = int(headers.get("x-total-results", len(records)))
        link = headers.get("link", "")
        match = re.search(r"[?&]cursor=([^&>]+)", link)
        continuation = urllib.parse.unquote(match.group(1)) if match else ""

    receipt = compact_payload(records, args.mode, args.query_id)
    receipt_path = raw_dir / f"receipt_{args.offset:06d}_{_hash(args.cursor)[:8] or 'page'}.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "query_id": args.query_id,
                "receipt_path": str(receipt_path.relative_to(root)),
                "records": len(records),
                "total_count": total_count,
                "continuation": continuation,
                "continuation_hash": _hash(continuation),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
