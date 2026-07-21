"""Artifact inventory, hash, and ledger-cardinality validation."""

from __future__ import annotations

import hashlib
import csv
import io
import json
from pathlib import Path
from typing import Any, Mapping

from v7_output_contract import ARTIFACT_SPECS, OUTPUT_CONTRACT_VERSION

from .common import ValidationIssue, issue, rows, snapshot_sha256


def _ledger_counts(snapshot: Mapping[str, Any]) -> dict[str, int]:
    seed_count = len(rows(snapshot, "candidate_seeds"))
    deep_count = len(rows(snapshot, "deep_candidates"))
    unresolved_count = len(rows(snapshot, "quarantined_seeds"))
    return {
        "source_universes_and_coverage.csv": len(rows(snapshot, "query_plans")),
        "candidate_seed_universe.jsonl": seed_count,
        "screening_and_disposition_funnel.csv": seed_count,
        "funnel_reconciliation.jsonl": 1,
        "identity_normalization_and_merges.jsonl": seed_count,
        "unresolved_and_quarantined_seeds.csv": unresolved_count,
        "deeply_assessed_candidates.jsonl": deep_count,
        "evidence_strength_ranking.csv": deep_count,
        "novelty_information_value_ranking.csv": deep_count,
        "diversified_portfolio_ranking.csv": deep_count,
        "candidate_evidence_cards.jsonl": deep_count,
        "candidate_evidence_cards.md": deep_count,
        "full_funnel_summary.md": 1,
    }


def _serialized_row_count(payload: bytes, media_type: str) -> int | None:
    text = payload.decode("utf-8-sig")
    if media_type == "text/csv":
        return sum(1 for _ in csv.DictReader(io.StringIO(text)))
    if media_type == "application/x-ndjson":
        count = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise ValueError("JSONL row must be an object")
            count += 1
        return count
    return None


def validate(
    snapshot: Mapping[str, Any],
    output_root: Path,
    manifest: Mapping[str, Any] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    manifest_value: Mapping[str, Any]
    if manifest is None:
        path = output_root / "artifact_manifest.json"
        if not path.is_file():
            return [issue("outputs", "MANIFEST_MISSING", "artifact_manifest.json is missing")]
        try:
            parsed = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            return [issue("outputs", "MANIFEST_JSON", str(exc))]
        manifest_value = parsed if isinstance(parsed, Mapping) else {}
    else:
        manifest_value = manifest
    if manifest_value.get("output_contract_version") != OUTPUT_CONTRACT_VERSION:
        issues.append(issue("outputs", "CONTRACT_VERSION", "artifact manifest output contract version mismatch"))
    if manifest_value.get("snapshot_sha256") != snapshot_sha256(snapshot):
        issues.append(issue("outputs", "SNAPSHOT_HASH", "artifact manifest is not bound to the committed snapshot"))
    entries = manifest_value.get("artifacts", [])
    if not isinstance(entries, list):
        return [*issues, issue("outputs", "ARTIFACT_LIST", "manifest artifacts must be a list")]
    by_name = {str(row.get("filename")): row for row in entries if isinstance(row, Mapping)}
    expected_names = {row.filename for row in ARTIFACT_SPECS}
    specs = {row.filename: row for row in ARTIFACT_SPECS}
    if set(by_name) != expected_names:
        issues.append(issue("outputs", "ARTIFACT_INVENTORY", "manifest must contain every and only canonical output artifact"))
    ledger_counts = _ledger_counts(snapshot)
    for name, entry in by_name.items():
        path = (output_root / name).resolve()
        if output_root.resolve() not in path.parents or not path.is_file():
            issues.append(issue("outputs", "ARTIFACT_MISSING", f"artifact is missing or outside output root: {name}"))
            continue
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest().upper() != str(entry.get("sha256", "")).upper():
            issues.append(issue("outputs", "ARTIFACT_HASH", f"artifact hash mismatch: {name}"))
        if len(payload) != entry.get("bytes"):
            issues.append(issue("outputs", "ARTIFACT_BYTES", f"artifact byte count mismatch: {name}"))
        try:
            serialized_count = _serialized_row_count(payload, specs[name].media_type)
        except Exception as exc:
            issues.append(issue("outputs", "ARTIFACT_PARSE", f"artifact {name} is malformed: {exc}"))
        else:
            if serialized_count is not None and serialized_count != entry.get("row_count"):
                issues.append(issue("outputs", "ROW_COUNT", f"artifact {name} row count does not match its serialized content"))
        if name in ledger_counts and entry.get("ledger_count") != ledger_counts[name]:
            issues.append(issue("outputs", "LEDGER_COUNT", f"artifact {name} does not reconcile to its canonical ledger"))
        if not isinstance(entry.get("row_count"), int) or entry.get("row_count") < 0:
            issues.append(issue("outputs", "ROW_COUNT", f"artifact {name} has an invalid logical row count"))
    reconciliation = manifest_value.get("reconciliation", {})
    if not isinstance(reconciliation, Mapping) or not all(
        reconciliation.get(field) is True
        for field in (
            "seed_equation_balanced",
            "screening_equation_balanced",
            "deep_selection_equation_balanced",
            "deep_completion_equation_balanced",
            "portfolio_equation_balanced",
        )
    ):
        issues.append(issue("outputs", "RECONCILIATION", "artifact manifest funnel reconciliation is incomplete"))
    return issues


__all__ = ["validate"]
