#!/usr/bin/env python3
"""Strip source-tool payloads to compact discovery or verification records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


DISCOVERY_FIELDS = (
    "canonical_identifier",
    "identifier_type",
    "title",
    "abstract",
    "mesh_terms",
    "keywords",
    "year",
    "source_kind",
    "url",
    "query_id",
)
VERIFICATION_FIELDS = DISCOVERY_FIELDS + (
    "targeted_excerpt",
    "verification_pointer",
    "verification_scope",
    "support_direction",
)
IDENTIFIER_ALIASES = (
    "canonical_identifier",
    "pmid",
    "doi",
    "uid",
    "id",
    "chembl_id",
    "molecule_chembl_id",
    "chebi_id",
)
CONTAINER_FIELDS = ("records", "results", "items", "articles", "molecules", "activities")
TEXT_FIELDS = {
    "canonical_identifier",
    "identifier_type",
    "title",
    "abstract",
    "source_kind",
    "url",
    "query_id",
    "targeted_excerpt",
    "verification_pointer",
    "verification_scope",
    "support_direction",
}
TEXT_OR_LIST_FIELDS = {"mesh_terms", "keywords"}


class PayloadValidationError(ValueError):
    """Raised when an adapter payload has not been normalized safely."""


def _record_hash(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        invalid = [index for index, row in enumerate(payload) if not isinstance(row, dict)]
        if invalid:
            raise PayloadValidationError(f"Record list contains non-object entries at indexes {invalid}")
        return payload
    if not isinstance(payload, dict):
        raise PayloadValidationError(
            "Expected normalized JSON object or record list; raw HTML/XML/text must be parsed by its source adapter"
        )
    for key in CONTAINER_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if not isinstance(value, list):
            raise PayloadValidationError(f"Container field {key!r} must be a list of normalized records")
        invalid = [index for index, row in enumerate(value) if not isinstance(row, dict)]
        if invalid:
            raise PayloadValidationError(f"Container {key!r} has non-object entries at indexes {invalid}")
        return value
    return [payload]


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _validate_field(record_index: int, field: str, value: Any) -> None:
    if field in TEXT_FIELDS and not isinstance(value, (str, int, float)):
        raise PayloadValidationError(
            f"Record {record_index} field {field!r} is nested; parse and normalize it in the source-specific adapter"
        )
    if field in TEXT_OR_LIST_FIELDS:
        if isinstance(value, str):
            return
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise PayloadValidationError(
                f"Record {record_index} field {field!r} must be text or a list of text values"
            )
    if field == "year" and not isinstance(value, (str, int)):
        raise PayloadValidationError(f"Record {record_index} field 'year' must be text or an integer")


def compact_payload(payload: Any, mode: str, query_id: str | None = None) -> dict[str, Any]:
    if mode not in {"discovery", "verification"}:
        raise PayloadValidationError(f"Unsupported compaction mode: {mode!r}")
    allowed = VERIFICATION_FIELDS if mode == "verification" else DISCOVERY_FIELDS
    compact: list[dict[str, Any]] = []
    for record_index, row in enumerate(_records(payload)):
        normalized = dict(row)
        existing_query_id = str(normalized.get("query_id", "")).strip()
        if query_id and existing_query_id and existing_query_id != query_id:
            raise PayloadValidationError(
                f"Record {record_index} query_id {existing_query_id!r} does not match requested query {query_id!r}"
            )
        if query_id:
            normalized["query_id"] = query_id
        normalized["canonical_identifier"] = _first(row, *IDENTIFIER_ALIASES)
        normalized["title"] = _first(row, "title", "name", "pref_name")
        normalized["abstract"] = _first(row, "abstract", "summary", "description")
        normalized["mesh_terms"] = _first(row, "mesh_terms", "mesh", "mesh_headings")
        normalized["year"] = _first(row, "year", "publication_year", "pub_year")
        cleaned = {
            field: normalized[field]
            for field in allowed
            if normalized.get(field) not in (None, "", [], {})
        }
        if not cleaned:
            raise PayloadValidationError(
                f"Record {record_index} contains no recognized normalized source fields; raw or nested payload suspected"
            )
        for field, value in cleaned.items():
            _validate_field(record_index, field, value)
        cleaned["compact_record_hash"] = _record_hash(cleaned)
        compact.append(cleaned)
    return {
        "schema_version": 2,
        "compactor": "compact_source_payload.py",
        "mode": mode,
        "result_count": len(compact),
        "records": compact,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    parser.add_argument("--mode", choices=("discovery", "verification"), default="discovery")
    parser.add_argument("--query-id", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.input_path).read_text(encoding="utf-8-sig"))
        compact = compact_payload(payload, args.mode, args.query_id)
    except (OSError, json.JSONDecodeError, PayloadValidationError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "requirement": "Provide normalized JSON records from a source-specific adapter; raw HTML/XML is not accepted.",
                }
            ),
            file=sys.stderr,
        )
        return 1
    Path(args.output_path).write_text(
        json.dumps(compact, ensure_ascii=True, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps({"ok": True, "records": compact["result_count"], "output": args.output_path}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
