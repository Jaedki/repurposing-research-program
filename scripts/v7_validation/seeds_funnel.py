"""Seed, screening, deep-selection, and funnel reconciliation validation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from v7_output_contract import FullFunnelReconciliation, OutputStatus

from .common import ValidationIssue, count_by, duplicate_ids, enum_value, index, issue, rows


_SEED_DISPOSITIONS = {"admit", "merge", "baseline", "reject", "quarantine", "failed"}
_SCREEN_OUTCOMES = {"screened", "screen_rejected", "screen_quarantined", "screen_failed"}
_INTERIM_PORTFOLIO = {"unaudited", "council_blocked", "selection_pending_additional_audit"}


def reconcile(snapshot: Mapping[str, Any]) -> FullFunnelReconciliation:
    seeds = rows(snapshot, "candidate_seeds")
    decisions = rows(snapshot, "screening_decisions")
    deep_selections = rows(snapshot, "deep_selection_records")
    deep_candidates = rows(snapshot, "deep_candidates")
    portfolio = rows(snapshot, "portfolio_rank_records")
    decision_counts = count_by(decisions, "canonical_disposition")
    admit_decisions = [row for row in decisions if enum_value(row.get("canonical_disposition")) == "admit"]
    screening_counts = count_by(admit_decisions, "screening_outcome")
    selection_counts = count_by(deep_selections, "selection_disposition")
    completion_counts = count_by(deep_selections, "completion_disposition")
    portfolio_counts = count_by(portfolio, "disposition")

    identity_rows = rows(snapshot, "identity_resolutions")
    identity_by_seed = index(identity_rows, "seed_id")
    decision_by_seed = index(decisions, "seed_id")
    normalized = index(rows(snapshot, "normalized_interventions"), "normalized_intervention_id")

    def normalized_id(row: Mapping[str, Any]) -> str:
        return str(
            row.get("verified_normalized_intervention_id")
            or row.get("normalized_intervention_id")
            or ""
        )

    resolved_all = {
        normalized_id(row)
        for row in identity_rows
        if normalized_id(row) and enum_value(decision_by_seed.get(str(row.get("seed_id")), {}).get("canonical_disposition")) != "failed"
    }
    admitted = {
        normalized_id(identity_by_seed.get(str(row.get("seed_id")), {}))
        for row in decisions
        if enum_value(row.get("canonical_disposition")) == "admit"
    } - {""}
    baseline = {
        normalized_id(identity_by_seed.get(str(row.get("seed_id")), {}))
        for row in decisions
        if enum_value(row.get("canonical_disposition")) == "baseline"
    } - {""}
    breadth = {
        str(normalized.get(value, {}).get("breadth_group_id") or identity_by_seed.get(
            next((seed_id for seed_id, record in identity_by_seed.items() if normalized_id(record) == value), ""), {}
        ).get("breadth_group_id") or value)
        for value in admitted
    }
    active_moieties = {
        str(row.get("active_moiety_id"))
        for row in identity_rows
        if row.get("active_moiety_id")
    }

    seed_count = len({str(row.get("seed_id")) for row in seeds})
    admit_count = decision_counts.get("admit", 0)
    selected_deep = selection_counts.get("selected_deep", 0)
    deep_count = completion_counts.get("deep", 0)
    interim_count = sum(portfolio_counts.get(value, 0) for value in _INTERIM_PORTFOLIO)
    return FullFunnelReconciliation(
        seed_count=seed_count,
        admit_count=admit_count,
        merge_count=decision_counts.get("merge", 0),
        baseline_count=decision_counts.get("baseline", 0),
        reject_count=decision_counts.get("reject", 0),
        quarantine_count=decision_counts.get("quarantine", 0),
        failed_count=decision_counts.get("failed", 0),
        screened_count=screening_counts.get("screened", 0),
        screen_rejected_count=screening_counts.get("screen_rejected", 0),
        screen_quarantined_count=screening_counts.get("screen_quarantined", 0),
        screen_failed_count=screening_counts.get("screen_failed", 0),
        selected_deep_count=selected_deep,
        screen_only_count=selection_counts.get("screen_only", 0),
        deep_count=deep_count,
        deep_quarantined_count=completion_counts.get("deep_quarantined", 0),
        deep_failed_count=completion_counts.get("deep_failed", 0),
        finalist_count=portfolio_counts.get("finalist", 0),
        reserve_count=portfolio_counts.get("reserve", 0),
        not_selected_count=portfolio_counts.get("not_selected", 0),
        audit_rejected_count=portfolio_counts.get("audit_rejected", 0),
        audit_quarantined_count=portfolio_counts.get("audit_quarantined", 0),
        interim_portfolio_count=interim_count,
        identity_resolved_all_count=len(resolved_all),
        identity_admitted_count=len(admitted),
        identity_baseline_count=len(baseline),
        breadth_admitted_count=len(breadth),
        active_moiety_count=len(active_moieties),
        seed_equation_balanced=seed_count == sum(decision_counts.get(value, 0) for value in _SEED_DISPOSITIONS),
        screening_equation_balanced=admit_count == sum(screening_counts.get(value, 0) for value in _SCREEN_OUTCOMES),
        deep_selection_equation_balanced=screening_counts.get("screened", 0) == selected_deep + selection_counts.get("screen_only", 0),
        deep_completion_equation_balanced=selected_deep == deep_count + completion_counts.get("deep_quarantined", 0) + completion_counts.get("deep_failed", 0),
        portfolio_equation_balanced=deep_count == sum(portfolio_counts.values()),
    )


def reconciliation_dict(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return asdict(reconcile(snapshot))


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seeds = rows(snapshot, "candidate_seeds")
    decisions = rows(snapshot, "screening_decisions")
    seed_ids = {str(row.get("seed_id")) for row in seeds if row.get("seed_id")}
    if duplicate_ids(seeds, "seed_id"):
        issues.append(issue("seeds_funnel", "SEED_DUPLICATE", "candidate seed IDs must be unique"))
    if duplicate_ids(decisions, "seed_id"):
        issues.append(issue("seeds_funnel", "DISPOSITION_DUPLICATE", "each seed must have exactly one current disposition"))
    decision_ids = {str(row.get("seed_id")) for row in decisions if row.get("seed_id")}
    if decision_ids != seed_ids:
        issues.append(issue("seeds_funnel", "DISPOSITION_COVERAGE", "screening decisions must cover every and only canonical seed"))
    decision_by_seed = index(decisions, "seed_id")
    for seed_id, decision in decision_by_seed.items():
        disposition = enum_value(decision.get("canonical_disposition"))
        if disposition not in _SEED_DISPOSITIONS:
            issues.append(issue("seeds_funnel", "DISPOSITION_VALUE", f"seed {seed_id} has invalid canonical disposition"))
        if not str(decision.get("reason", "")).strip():
            issues.append(issue("seeds_funnel", "DISPOSITION_REASON", f"seed {seed_id} lacks a disposition reason"))
        if disposition == "merge":
            representative = str(decision.get("representative_seed_id", ""))
            target = decision_by_seed.get(representative, {})
            if representative == seed_id or enum_value(target.get("canonical_disposition")) not in {"admit", "baseline"}:
                issues.append(issue("seeds_funnel", "MERGE_TARGET", f"merged seed {seed_id} must point to an admitted or baseline representative"))
    screened = rows(snapshot, "screened_candidates")
    links = rows(snapshot, "seed_candidate_mappings")
    deep_selection = rows(snapshot, "deep_selection_records")
    screened_ids = {str(row.get("screened_candidate_id")) for row in screened if row.get("screened_candidate_id")}
    linked_screened_ids = {
        str(row.get("screened_candidate_id"))
        for row in links
        if row.get("screened_candidate_id")
    }
    if not linked_screened_ids.issubset(screened_ids):
        issues.append(issue("seeds_funnel", "SCREEN_LINK_TARGET", "seed-to-screened links must resolve to canonical screened candidates"))
    representative_links = {
        str(row.get("screened_candidate_id"))
        for row in links
        if enum_value(decision_by_seed.get(str(row.get("seed_id")), {}).get("screening_outcome")) == "screened"
    }
    if representative_links != screened_ids:
        issues.append(issue("seeds_funnel", "SCREEN_LINK_COVERAGE", "every screened candidate requires one admitted screened representative link"))
    selection_ids = {str(row.get("screened_candidate_id")) for row in deep_selection if row.get("screened_candidate_id")}
    if selection_ids != screened_ids:
        issues.append(issue("seeds_funnel", "DEEP_SELECTION_COVERAGE", "deep selection must cover every and only screened candidate"))
    for row in deep_selection:
        selected = enum_value(row.get("selection_disposition"))
        completed = enum_value(row.get("completion_disposition"))
        if selected == "screen_only" and completed != "not_selected":
            issues.append(issue("seeds_funnel", "SCREEN_ONLY_COMPLETION", "screen-only records must use completion_disposition=not_selected"))
        if selected == "selected_deep" and completed not in {"deep", "deep_quarantined", "deep_failed"}:
            issues.append(issue("seeds_funnel", "DEEP_COMPLETION", "selected-deep records require a deep completion disposition"))
        if not str(row.get("reason", "")).strip():
            issues.append(issue("seeds_funnel", "DEEP_SELECTION_REASON", "deep selection records require reasons"))
    counts = reconcile(snapshot)
    if len(screened_ids) != counts.screened_count:
        issues.append(issue("seeds_funnel", "SCREENED_COUNT", "screened-candidate ledger count must equal N_screened"))
    if len(rows(snapshot, "deep_candidates")) != counts.deep_count:
        issues.append(issue("seeds_funnel", "DEEP_COUNT", "deep-candidate ledger count must equal N_deep"))
    for field in (
        "seed_equation_balanced",
        "screening_equation_balanced",
        "deep_selection_equation_balanced",
        "deep_completion_equation_balanced",
        "portfolio_equation_balanced",
    ):
        if not getattr(counts, field):
            issues.append(issue("seeds_funnel", "RECONCILIATION", f"{field} is false"))
    if counts.identity_admitted_count != counts.admit_count:
        issues.append(issue("seeds_funnel", "IDENTITY_ADMITTED", "N_identity_admitted must equal N_admit"))
    if snapshot.get("output_status") == OutputStatus.COMPLETE.value and counts.interim_portfolio_count:
        issues.append(issue("seeds_funnel", "INTERIM_PORTFOLIO", "complete outputs cannot contain interim portfolio dispositions"))
    return issues


__all__ = ["reconcile", "reconciliation_dict", "validate"]
