#!/usr/bin/env python3
"""Build one deterministic, receipt-bound search_log record."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _read_receipt(root: Path, value: str, query_id: str) -> tuple[str, list[dict[str, Any]]]:
    path = (root / value).resolve()
    path.relative_to(root)
    receipt = json.loads(path.read_text(encoding="utf-8-sig"))
    if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py":
        raise ValueError(f"Invalid compact receipt: {value}")
    records = receipt.get("records")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"Receipt records must be objects: {value}")
    mismatches = [index for index, row in enumerate(records, 1) if str(row.get("query_id", "")) != query_id]
    if mismatches:
        raise ValueError(f"Receipt {value} has records not bound to {query_id}: {mismatches}")
    return str(path.relative_to(root)), records


def _identity(record: dict[str, Any]) -> str:
    canonical = str(record.get("canonical_identifier", "")).strip().casefold()
    identifier_type = str(record.get("identifier_type", "")).strip().casefold()
    return f"{identifier_type}:{canonical}" if canonical else f"hash:{record.get('compact_record_hash', '')}"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def build_search_record(
    root: Path,
    *,
    query_id: str,
    research_unit_id: str,
    subtopic_id: str,
    query_family: str,
    resource: str,
    query: str,
    receipt_paths: list[str],
    continuation_tokens: list[str],
    acquired_source_ids: list[str],
    original_verified_source_ids: list[str],
    retained_source_ids: list[str],
    executed_by_agent_id: str,
    executor_role: str,
    origin_job_id: str,
    closure_note: str,
) -> dict[str, Any]:
    if len(continuation_tokens) != max(0, len(receipt_paths) - 1):
        raise ValueError("Provide exactly one continuation token between adjacent receipt pages")
    normalized_paths: list[str] = []
    records: list[dict[str, Any]] = []
    for value in receipt_paths:
        path, page_records = _read_receipt(root, value, query_id)
        normalized_paths.append(path)
        records.extend(page_records)
    hashes = [_hash_token(token) for token in continuation_tokens]
    trace = []
    for index, path in enumerate(normalized_paths):
        trace.append(
            {
                "page_index": index + 1,
                "receipt_path": path,
                "input_token_hash": hashes[index - 1] if index else "",
                "output_token_hash": hashes[index] if index < len(hashes) else "",
            }
        )
    return {
        "query_id": query_id,
        "research_unit_id": research_unit_id,
        "subtopic_id": subtopic_id,
        "query_family": query_family,
        "resource": resource,
        "query": query,
        "result_count": len(records),
        "deduplicated_count": len({_identity(row) for row in records}),
        "screened_count": len({_identity(row) for row in records}),
        "acquired_count": len(set(acquired_source_ids)),
        "original_verified_count": len(set(original_verified_source_ids)),
        "page_count": len(normalized_paths),
        "pagination_complete": True,
        "continuation_exhausted": True,
        "compact_payload_paths": normalized_paths,
        "pagination_trace": trace,
        "acquired_source_ids": list(dict.fromkeys(acquired_source_ids)),
        "original_verified_source_ids": list(dict.fromkeys(original_verified_source_ids)),
        "executed_by_agent_id": executed_by_agent_id,
        "executor_role": executor_role,
        "origin_job_id": origin_job_id,
        "retained_source_ids": list(dict.fromkeys(retained_source_ids)),
        "new_subtopic_ids": [],
        "new_claim_ids": [],
        "new_candidate_ids": [],
        "outcome": "completed",
        "rate_limit_pending": False,
        "closure_note": closure_note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder")
    parser.add_argument("output_path")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--research-unit-id", required=True)
    parser.add_argument("--subtopic-id", default="")
    parser.add_argument("--query-family", required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--continuation-token", action="append", default=[])
    parser.add_argument("--acquired-source-id", action="append", default=[])
    parser.add_argument("--verified-source-id", action="append", default=[])
    parser.add_argument("--retained-source-id", action="append", default=[])
    parser.add_argument("--executed-by-agent-id", required=True)
    parser.add_argument("--executor-role", choices=("worker", "auditor"), required=True)
    parser.add_argument("--origin-job-id", required=True)
    parser.add_argument("--closure-note", required=True)
    args = parser.parse_args()
    root = Path(args.run_folder).expanduser().resolve()
    row = build_search_record(
        root,
        query_id=args.query_id,
        research_unit_id=args.research_unit_id,
        subtopic_id=args.subtopic_id,
        query_family=args.query_family,
        resource=args.resource,
        query=args.query,
        receipt_paths=args.receipt,
        continuation_tokens=args.continuation_token,
        acquired_source_ids=args.acquired_source_id,
        original_verified_source_ids=args.verified_source_id,
        retained_source_ids=args.retained_source_id,
        executed_by_agent_id=args.executed_by_agent_id,
        executor_role=args.executor_role,
        origin_job_id=args.origin_job_id,
        closure_note=args.closure_note,
    )
    output = Path(args.output_path)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(row, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "query_id": args.query_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
