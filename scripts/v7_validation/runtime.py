"""Runtime and committed-snapshot validation."""

from __future__ import annotations

from typing import Any, Mapping

from v7_output_contract import OutputStatus, SCHEMA_VERSION

from .common import ValidationIssue, issue


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        issues.append(issue("runtime", "SCHEMA_VERSION", "committed snapshot schema_version must be 7"))
    if not str(snapshot.get("snapshot_id", "")).strip():
        issues.append(issue("runtime", "SNAPSHOT_ID", "committed snapshot requires a stable snapshot_id"))
    if str(snapshot.get("output_status", "")) not in {row.value for row in OutputStatus}:
        issues.append(issue("runtime", "OUTPUT_STATUS", "output_status must be complete or diagnostic_partial"))
    provenance = snapshot.get("provenance", {})
    if not isinstance(provenance, Mapping):
        issues.append(issue("runtime", "PROVENANCE", "machine-readable provenance must be an object"))
    else:
        if not str(provenance.get("canonical_scientific_hash", "")).strip():
            issues.append(issue("runtime", "SCIENTIFIC_HASH", "canonical scientific hash is required"))
        if not str(provenance.get("execution_hash", "")).strip():
            issues.append(issue("runtime", "EXECUTION_HASH", "separate execution hash is required"))
        commit_ids = provenance.get("commit_ids", [])
        if not isinstance(commit_ids, (list, tuple)) or len(commit_ids) != len(set(map(str, commit_ids))):
            issues.append(issue("runtime", "COMMIT_IDS", "commit_ids must be a unique list"))
        elif not commit_ids:
            issues.append(issue("runtime", "COMMIT_IDS", "at least one canonical commit ID is required"))
    return issues


__all__ = ["validate"]
