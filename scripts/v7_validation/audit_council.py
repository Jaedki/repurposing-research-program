"""Audit, correction, council, and portfolio validation."""

from __future__ import annotations

from typing import Any, Mapping

from v7_output_contract import OutputStatus

from .common import ValidationIssue, enum_value, index, issue, rows


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    deep_ids = {str(row.get("candidate_id")) for row in rows(snapshot, "deep_candidates")}
    assignments = rows(snapshot, "audit_assignments")
    assignments_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for assignment in assignments:
        candidate_id = str(assignment.get("candidate_id") or "")
        if candidate_id:
            assignments_by_candidate.setdefault(candidate_id, []).append(assignment)
    if set(assignments_by_candidate) != deep_ids:
        issues.append(issue("audit_council", "ASSIGNMENT_COVERAGE", "audit assignments must cover every and only deep candidate"))
    assignment_by_id = index(assignments, "assignment_id")
    audits = rows(snapshot, "audit_records")
    audit_by_assignment = index(audits, "assignment_id")
    for assignment_id, assignment in assignment_by_id.items():
        selected = enum_value(assignment.get("selection_status")) == "selected_for_audit"
        if selected != (assignment_id in audit_by_assignment):
            issues.append(issue("audit_council", "AUDIT_CARDINALITY", f"audit assignment {assignment_id} selected/audited state disagrees"))
    corrections = rows(snapshot, "audit_corrections")
    correction_by_id = index(corrections, "correction_id")
    for audit in audits:
        for correction_id in audit.get("correction_ids", []) if isinstance(audit.get("correction_ids"), (list, tuple)) else []:
            if str(correction_id) not in correction_by_id:
                issues.append(issue("audit_council", "CORRECTION_LINK", f"audit record {audit.get('audit_record_id')} references unknown correction"))
    councils = index(rows(snapshot, "council_records"), "candidate_id")
    portfolio = rows(snapshot, "portfolio_rank_records")
    portfolio_ids = {str(row.get("candidate_id")) for row in portfolio}
    if portfolio_ids != deep_ids:
        issues.append(issue("audit_council", "PORTFOLIO_COVERAGE", "portfolio dispositions must cover every and only deep candidate"))
    for row in portfolio:
        candidate_id = str(row.get("candidate_id", ""))
        disposition = enum_value(row.get("disposition"))
        candidate_assignments = assignments_by_candidate.get(candidate_id, [])
        if disposition in {"finalist", "reserve"} and not any(
            enum_value(assignment.get("selection_status")) == "selected_for_audit"
            for assignment in candidate_assignments
        ):
            issues.append(issue("audit_council", "UNAUDITED_CAPACITY", f"{candidate_id} cannot enter finalist/reserve capacity while unaudited"))
        if disposition == "council_blocked" and candidate_id not in councils:
            issues.append(issue("audit_council", "COUNCIL_LINK", f"council-blocked candidate {candidate_id} lacks a council record"))
    if snapshot.get("output_status") == OutputStatus.COMPLETE.value:
        unaudited = [row for row in assignments if enum_value(row.get("selection_status")) == "unaudited"]
        if any(str(row.get("candidate_id")) in {r.get("candidate_id") for r in portfolio if enum_value(r.get("disposition")) in {"finalist", "reserve"}} for row in unaudited):
            issues.append(issue("audit_council", "COMPLETE_UNAUDITED", "complete outputs cannot promote unaudited candidates"))
    return issues


__all__ = ["validate"]
