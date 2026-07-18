#!/usr/bin/env python3
"""Build one schema-v5 search record from query-bound compact receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from program_io import inside


def _receipt(root: Path, value: str, query_id: str) -> tuple[str, list[dict[str, Any]]]:
    path = inside(root, value)
    receipt = json.loads(path.read_text(encoding="utf-8-sig"))
    records = receipt.get("records")
    if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py":
        raise ValueError(f"Invalid compact receipt: {value}")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"Receipt records must be objects: {value}")
    if any(str(row.get("query_id", "")) != query_id for row in records):
        raise ValueError(f"Receipt {value} contains records not bound to {query_id}")
    return str(path.relative_to(root)), records


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def build_search_record(
    root: Path,
    *,
    query_id: str,
    research_unit_id: str,
    query_family: str,
    resource: str,
    query: str,
    receipt_paths: list[str],
    continuation_tokens: list[str],
    acquired_source_ids: list[str],
    verified_source_ids: list[str],
    retained_source_ids: list[str],
    executed_by_agent_id: str,
    origin_job_id: str,
    closure_note: str,
    produced_claim_ids: list[str] | None = None,
    produced_observation_ids: list[str] | None = None,
) -> dict[str, Any]:
    if len(continuation_tokens) != max(0, len(receipt_paths) - 1):
        raise ValueError("Provide exactly one continuation token between adjacent receipt pages")
    normalized_paths: list[str] = []
    records: list[dict[str, Any]] = []
    for value in receipt_paths:
        relative, page = _receipt(root, value, query_id)
        normalized_paths.append(relative)
        records.extend(page)
    token_hashes = [_token_hash(value) for value in continuation_tokens]
    trace = [
        {
            "page_index": index + 1,
            "receipt_path": path,
            "input_token_hash": token_hashes[index - 1] if index else "",
            "output_token_hash": token_hashes[index] if index < len(token_hashes) else "",
        }
        for index, path in enumerate(normalized_paths)
    ]
    acquired = list(dict.fromkeys(acquired_source_ids))
    verified = list(dict.fromkeys(verified_source_ids))
    retained = list(dict.fromkeys(retained_source_ids))
    if not set(verified).issubset(acquired) or not set(retained).issubset(verified):
        raise ValueError("Retained sources must be verified, and verified sources must be acquired")
    return {
        "query_id": query_id,
        "research_unit_id": research_unit_id,
        "query_family": query_family,
        "resource": resource,
        "query": query,
        "result_count": len(records),
        "screened_count": len(records),
        "pagination_complete": True,
        "compact_payload_paths": normalized_paths,
        "pagination_trace": trace,
        "acquired_source_ids": acquired,
        "verified_source_ids": verified,
        "retained_source_ids": retained,
        "executed_by_agent_id": executed_by_agent_id,
        "origin_job_id": origin_job_id,
        "produced_claim_ids": list(dict.fromkeys(produced_claim_ids or [])),
        "produced_observation_ids": list(dict.fromkeys(produced_observation_ids or [])),
        "outcome": "completed",
        "closure_note": closure_note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_folder")
    parser.add_argument("output_path")
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--research-unit-id", required=True)
    parser.add_argument("--query-family", required=True)
    parser.add_argument("--resource", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--receipt", action="append", required=True)
    parser.add_argument("--continuation-token", action="append", default=[])
    parser.add_argument("--acquired-source-id", action="append", default=[])
    parser.add_argument("--verified-source-id", action="append", default=[])
    parser.add_argument("--retained-source-id", action="append", default=[])
    parser.add_argument("--produced-claim-id", action="append", default=[])
    parser.add_argument("--produced-observation-id", action="append", default=[])
    parser.add_argument("--executed-by-agent-id", required=True)
    parser.add_argument("--origin-job-id", required=True)
    parser.add_argument("--closure-note", required=True)
    args = parser.parse_args()
    root = Path(args.run_folder).expanduser().resolve()
    row = build_search_record(
        root,
        query_id=args.query_id,
        research_unit_id=args.research_unit_id,
        query_family=args.query_family,
        resource=args.resource,
        query=args.query,
        receipt_paths=args.receipt,
        continuation_tokens=args.continuation_token,
        acquired_source_ids=args.acquired_source_id,
        verified_source_ids=args.verified_source_id,
        retained_source_ids=args.retained_source_id,
        produced_claim_ids=args.produced_claim_id,
        produced_observation_ids=args.produced_observation_id,
        executed_by_agent_id=args.executed_by_agent_id,
        origin_job_id=args.origin_job_id,
        closure_note=args.closure_note,
    )
    output = inside(root, args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(row, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "query_id": args.query_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
