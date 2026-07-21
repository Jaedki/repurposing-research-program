"""Deep evidence, endpoint, safety, exposure, and claim-link validation."""

from __future__ import annotations

from typing import Any, Mapping

from .common import ValidationIssue, index, issue, rows, string_ids


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    case = snapshot.get("case_revision", {})
    endpoints = {
        str(row.get("endpoint_id"))
        for row in case.get("endpoints", [])
        if isinstance(row, Mapping) and row.get("endpoint_id")
    } if isinstance(case, Mapping) else set()
    packages = index(rows(snapshot, "deep_evidence_packages"), "package_id", "deep_evidence_package_id")
    deep = rows(snapshot, "deep_candidates")
    for candidate in deep:
        candidate_id = str(candidate.get("candidate_id", ""))
        package_id = str(candidate.get("deep_evidence_package_id") or candidate.get("package_id") or "")
        package = packages.get(package_id)
        if package is None:
            issues.append(issue("evidence", "PACKAGE_LINK", f"deep candidate {candidate_id} lacks its evidence package"))
            continue
        claims = package.get("claims", [])
        evidence = package.get("evidence_records", [])
        sources = index(package.get("sources", []) if isinstance(package.get("sources"), (list, tuple)) else [], "source_record_id")
        spans = index(package.get("evidence_spans", []) if isinstance(package.get("evidence_spans"), (list, tuple)) else [], "evidence_span_id")
        claim_by_id = index(claims if isinstance(claims, (list, tuple)) else [], "claim_id")
        evidence_by_id = index(evidence if isinstance(evidence, (list, tuple)) else [], "deep_evidence_record_id", "evidence_record_id")
        if not claim_by_id or not evidence_by_id:
            issues.append(issue("evidence", "DEEP_EMPTY", f"deep package {package_id} requires claims and evidence records"))
        for record in evidence_by_id.values():
            if str(record.get("claim_id")) not in claim_by_id:
                issues.append(issue("evidence", "CLAIM_LINK", f"evidence record in {package_id} references an unknown claim"))
            if str(record.get("source_record_id")) not in sources or str(record.get("evidence_span_id")) not in spans:
                issues.append(issue("evidence", "GROUNDING_LINK", f"evidence record in {package_id} lacks source/span grounding"))
        assessed = {
            str(row.get("endpoint_id"))
            for row in package.get("endpoint_assessments", [])
            if isinstance(row, Mapping) and row.get("endpoint_id")
        }
        if assessed != endpoints:
            issues.append(issue("evidence", "ENDPOINT_COVERAGE", f"deep package {package_id} must assess every case endpoint"))
        if not string_ids(candidate.get("claim_ids")):
            issues.append(issue("evidence", "CANDIDATE_CLAIMS", f"deep candidate {candidate_id} lacks claim IDs"))
    return issues


__all__ = ["validate"]
