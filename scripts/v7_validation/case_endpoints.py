"""Normalized case and endpoint-portfolio validation."""

from __future__ import annotations

from typing import Any, Mapping

from v7_output_contract import OutputStatus

from .common import ValidationIssue, issue, rows, string_ids


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    case = snapshot.get("case_revision", {})
    if not isinstance(case, Mapping):
        return [issue("case_endpoints", "CASE", "case_revision must be an object")]
    case_revision_id = str(case.get("case_revision_id", ""))
    if not case_revision_id:
        issues.append(issue("case_endpoints", "CASE_ID", "case_revision_id is required"))
    endpoints = case.get("endpoints", [])
    if not isinstance(endpoints, (list, tuple)) or not endpoints:
        issues.append(issue("case_endpoints", "ENDPOINTS", "at least one endpoint is required"))
        return issues
    endpoint_ids = [str(row.get("endpoint_id", "")) for row in endpoints if isinstance(row, Mapping)]
    if len(endpoint_ids) != len(endpoints) or any(not value for value in endpoint_ids):
        issues.append(issue("case_endpoints", "ENDPOINT_ID", "every endpoint requires endpoint_id"))
    if len(endpoint_ids) != len(set(endpoint_ids)):
        issues.append(issue("case_endpoints", "ENDPOINT_DUPLICATE", "endpoint IDs must be unique"))
    for row in endpoints:
        if not isinstance(row, Mapping):
            continue
        required = row.get("required", {})
        if isinstance(required, Mapping) and required.get("status") == "known" and required.get("value") is True:
            if not row.get("role") or not row.get("endpoint_type"):
                issues.append(issue("case_endpoints", "REQUIRED_ENDPOINT", f"required endpoint {row.get('endpoint_id')} lacks role/type"))
    if (
        snapshot.get("output_status") == OutputStatus.COMPLETE.value
        and str(case.get("case_status", "")) != "ready"
    ):
        issues.append(issue("case_endpoints", "CASE_NOT_READY", "complete outputs require case_status=ready"))
    for name in ("candidate_seeds", "screened_candidates", "deep_candidates"):
        for row in rows(snapshot, name):
            if not isinstance(row, Mapping):
                continue
            linked = string_ids(row.get("endpoint_ids"))
            if any(value not in endpoint_ids for value in linked):
                issues.append(issue("case_endpoints", "ENDPOINT_LINK", f"{name} record references an endpoint outside the case portfolio"))
    return issues


__all__ = ["validate"]
