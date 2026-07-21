"""Declared retrieval universe, receipt, and coverage validation."""

from __future__ import annotations

from typing import Any, Mapping

from v7_output_contract import OutputStatus

from .common import ValidationIssue, duplicate_ids, index, issue, rows


_COVERAGE_STATES = {
    "complete_for_declared_query_and_release",
    "no_relevant_hits_within_declared_query",
    "partial_due_to_source_limit",
    "partial_due_to_rate_limit",
    "unsupported_source_capability",
    "failed_retrieval",
    "not_yet_searched",
}
_COMPLETE_STATES = {
    "complete_for_declared_query_and_release",
    "no_relevant_hits_within_declared_query",
}


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    universes = rows(snapshot, "source_universes")
    plans = rows(snapshot, "query_plans")
    proofs = rows(snapshot, "coverage_proofs")
    if duplicate_ids(universes, "source_universe_id"):
        issues.append(issue("retrieval_coverage", "UNIVERSE_DUPLICATE", "source universe IDs must be unique"))
    if duplicate_ids(plans, "query_plan_id"):
        issues.append(issue("retrieval_coverage", "PLAN_DUPLICATE", "query plan IDs must be unique"))
    if duplicate_ids(proofs, "coverage_proof_id"):
        issues.append(issue("retrieval_coverage", "PROOF_DUPLICATE", "coverage proof IDs must be unique"))
    universe_by_id = index(universes, "source_universe_id")
    plan_by_id = index(plans, "query_plan_id")
    planned_universe_ids: set[str] = set()
    proof_by_plan: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        universe = plan.get("source_universe", {})
        universe_id = str(
            universe.get("source_universe_id", "") if isinstance(universe, Mapping)
            else plan.get("source_universe_id", "")
        )
        if universe_id not in universe_by_id:
            issues.append(issue("retrieval_coverage", "PLAN_UNIVERSE", f"query plan {plan.get('query_plan_id')} lacks a declared source universe"))
        else:
            planned_universe_ids.add(universe_id)
    if planned_universe_ids != set(universe_by_id):
        issues.append(issue("retrieval_coverage", "UNIVERSE_COVERAGE", "every declared source universe must have at least one query plan"))
    for proof in proofs:
        plan = proof.get("query_plan", {})
        plan_id = str(plan.get("query_plan_id", "") if isinstance(plan, Mapping) else proof.get("query_plan_id", ""))
        if plan_id not in plan_by_id:
            issues.append(issue("retrieval_coverage", "PROOF_PLAN", f"coverage proof {proof.get('coverage_proof_id')} lacks a declared query plan"))
        proof_by_plan.setdefault(plan_id, []).append(proof)
        state = str(proof.get("coverage_state", ""))
        if state not in _COVERAGE_STATES:
            issues.append(issue("retrieval_coverage", "COVERAGE_STATE", f"coverage proof {proof.get('coverage_proof_id')} has invalid state"))
        receipts = proof.get("content_receipts", [])
        if not isinstance(receipts, (list, tuple)):
            issues.append(issue("retrieval_coverage", "CONTENT_RECEIPTS", "content_receipts must be a list"))
            receipts = []
        ordinals = [row.get("page_ordinal") for row in receipts if isinstance(row, Mapping)]
        if ordinals and ordinals != list(range(1, len(ordinals) + 1)):
            issues.append(issue("retrieval_coverage", "PAGE_CHAIN", f"coverage proof {proof.get('coverage_proof_id')} has a non-gapless page order"))
        reconciliation = proof.get("reconciliation", {})
        if not isinstance(reconciliation, Mapping) or reconciliation.get("count_reconciliation_ok") is not True:
            issues.append(issue("retrieval_coverage", "COUNT_RECONCILIATION", f"coverage proof {proof.get('coverage_proof_id')} does not reconcile"))
        if state in _COMPLETE_STATES and reconciliation.get("continuation_exhausted") is not True:
            issues.append(issue("retrieval_coverage", "FALSE_CLOSURE", f"complete proof {proof.get('coverage_proof_id')} did not exhaust continuation"))
    for plan_id, plan in plan_by_id.items():
        matching = proof_by_plan.get(plan_id, [])
        if len(matching) != 1:
            issues.append(issue("retrieval_coverage", "PLAN_CARDINALITY", f"query plan {plan_id} must have exactly one coverage proof"))
            continue
        state = str(matching[0].get("coverage_state", ""))
        if snapshot.get("output_status") == OutputStatus.COMPLETE.value and state not in _COMPLETE_STATES:
            issues.append(issue("retrieval_coverage", "INCOMPLETE_PLAN", f"complete outputs cannot contain query plan {plan_id} in state {state}"))
    for record in rows(snapshot, "unsupported_capabilities"):
        if not str(record.get("preserved_coverage_gap", "")).strip():
            issues.append(issue("retrieval_coverage", "UNSUPPORTED_GAP", "unsupported capability must preserve its coverage gap"))
    return issues


__all__ = ["validate"]
