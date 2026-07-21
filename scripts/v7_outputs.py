#!/usr/bin/env python3
"""Build reconciled schema-v7 full-funnel machine and expert-readable outputs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_output_contract import (
    ARTIFACT_SPECS,
    OUTPUT_CONTRACT_VERSION,
    OUTPUT_DIRECTORY,
    OutputStatus,
    canonical_sha256,
    render_reference_contract,
)
from v7_validation import load_committed_snapshot, validate_snapshot
from v7_validation.common import enum_value, index, normalize_snapshot, rows, snapshot_sha256
from v7_validation.seeds_funnel import reconcile


class V7OutputError(ValueError):
    pass


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonl(values: Iterable[Mapping[str, Any]]) -> tuple[bytes, int]:
    rows_value = [dict(value) for value in values]
    text = "".join(_json_text(value) + "\n" for value in rows_value)
    return text.encode("utf-8"), len(rows_value)


def _csv(values: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> tuple[bytes, int]:
    rows_value = [dict(value) for value in values]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows_value)
    return output.getvalue().encode("utf-8"), len(rows_value)


def _id(row: Mapping[str, Any], *fields: str) -> str:
    return next((str(row.get(field)) for field in fields if row.get(field)), "")


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _rank_row(
    record: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contributions = _list(record.get("diversity_contributions"))
    candidate = candidate or {}
    return {
        "candidate_id": record.get("candidate_id", ""),
        "evidence_strength_rank": record.get("evidence_strength_rank", ""),
        "novelty_information_value_rank": record.get("novelty_information_value_rank", ""),
        "diversified_portfolio_rank": record.get("diversified_portfolio_rank", ""),
        "portfolio_disposition": enum_value(record.get("disposition")),
        "evidence_component": record.get("evidence_component", ""),
        "novelty_information_component": record.get("novelty_information_component", ""),
        "diversity_component": record.get("diversity_component", ""),
        "total_selection_utility": record.get("total_selection_utility", ""),
        "audit_status": enum_value(record.get("audit_status")),
        "audit_outcome": enum_value(record.get("audit_outcome")),
        "council_disposition": enum_value(record.get("council_disposition")),
        "development_statuses": " | ".join(map(str, _list(candidate.get("development_statuses")))),
        "preclinical_only": candidate.get("preclinical_only", False),
        "diversity_contributions": _json_text(contributions),
        "reason": _clean(record.get("reason")),
    }


def _coverage_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    universes = index(rows(snapshot, "source_universes"), "source_universe_id")
    proofs = rows(snapshot, "coverage_proofs")
    proof_by_plan: dict[str, dict[str, Any]] = {}
    for proof in proofs:
        plan = proof.get("query_plan", {})
        plan_id = _id(plan, "query_plan_id") if isinstance(plan, Mapping) else _id(proof, "query_plan_id")
        proof_by_plan[plan_id] = proof
    result: list[dict[str, Any]] = []
    for plan in sorted(rows(snapshot, "query_plans"), key=lambda row: _id(row, "query_plan_id")):
        embedded = plan.get("source_universe", {})
        universe_id = _id(embedded, "source_universe_id") if isinstance(embedded, Mapping) else _id(plan, "source_universe_id")
        universe = universes.get(universe_id, dict(embedded) if isinstance(embedded, Mapping) else {})
        proof = proof_by_plan.get(_id(plan, "query_plan_id"), {})
        reconciliation = proof.get("reconciliation", {}) if isinstance(proof.get("reconciliation"), Mapping) else {}
        result.append(
            {
                "source_universe_id": universe_id,
                "source_id": universe.get("source_id", ""),
                "source_release": universe.get("source_release", ""),
                "source_snapshot_at": universe.get("source_snapshot_at", ""),
                "query_plan_id": plan.get("query_plan_id", ""),
                "query_family_id": plan.get("query_family_id", ""),
                "required": plan.get("required", ""),
                "denominator_kind": enum_value(universe.get("denominator_kind")),
                "declared_total": universe.get("declared_total", ""),
                "coverage_state": enum_value(proof.get("coverage_state")),
                "returned_native_record_count": reconciliation.get("returned_native_record_count", ""),
                "normalized_record_count": reconciliation.get("normalized_record_count", ""),
                "emitted_seed_count": reconciliation.get("emitted_seed_count", ""),
                "unvisited_record_count": reconciliation.get("unvisited_record_count", ""),
                "continuation_exhausted": reconciliation.get("continuation_exhausted", ""),
                "source_specific_limitations": " | ".join(map(str, _list(proof.get("source_specific_limitations")))),
                "coverage_gaps": " | ".join(map(str, _list(proof.get("coverage_gaps")))),
            }
        )
    return result


def _joined_ledgers(snapshot: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "seeds": index(rows(snapshot, "candidate_seeds"), "seed_id"),
        "mappings": index(rows(snapshot, "source_mappings"), "mapping_id"),
        "routes": index(rows(snapshot, "discovery_routes"), "route_id"),
        "identities": index(rows(snapshot, "identity_resolutions"), "seed_id"),
        "normalized": index(rows(snapshot, "normalized_interventions"), "normalized_intervention_id"),
        "decisions": index(rows(snapshot, "screening_decisions"), "seed_id"),
        "links": index(rows(snapshot, "seed_candidate_mappings"), "seed_id"),
        "screened": index(rows(snapshot, "screened_candidates"), "screened_candidate_id"),
        "deep_selection": index(rows(snapshot, "deep_selection_records"), "screened_candidate_id"),
        "deep": index(rows(snapshot, "deep_candidates"), "candidate_id"),
        "packages": index(rows(snapshot, "deep_evidence_packages"), "package_id", "deep_evidence_package_id"),
        "profiles": index(rows(snapshot, "decision_profiles"), "candidate_id"),
        "preparations": index(rows(snapshot, "ranking_preparation_records"), "candidate_id"),
        "assignments": index(rows(snapshot, "audit_assignments"), "candidate_id"),
        "audits": index(rows(snapshot, "audit_records"), "candidate_id"),
        "councils": index(rows(snapshot, "council_records"), "candidate_id"),
        "portfolio": index(rows(snapshot, "portfolio_rank_records"), "candidate_id"),
    }


def _seed_rows(snapshot: Mapping[str, Any], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for seed_id, seed in sorted(joined["seeds"].items()):
        decision = joined["decisions"].get(seed_id, {})
        identity = joined["identities"].get(seed_id, {})
        mapping = joined["mappings"].get(str(seed.get("source_mapping_id", "")), {})
        route_ids = [str(value) for value in _list(seed.get("discovery_route_ids"))]
        result.append(
            {
                "seed_id": seed_id,
                "case_revision_id": seed.get("case_revision_id", ""),
                "raw_intervention_assertion": mapping.get("raw_intervention_assertion", ""),
                "compound_hint": seed.get("compound_hint", {}),
                "endpoint_ids": _list(seed.get("endpoint_ids")),
                "source_mapping": mapping,
                "discovery_routes": [joined["routes"][value] for value in route_ids if value in joined["routes"]],
                "structured_routes": _list(seed.get("structured_routes")),
                "evidence_modalities": _list(seed.get("evidence_modalities")),
                "chemical_universes": _list(seed.get("chemical_universes")),
                "development_status_hint": seed.get("development_status_hint", {}),
                "uncertainty": _list(seed.get("uncertainty")),
                "identity_resolution": identity,
                "screening_decision": decision,
                "canonical_disposition": enum_value(decision.get("canonical_disposition")),
                "representative_seed_id": decision.get("representative_seed_id", ""),
            }
        )
    return result


def _funnel_rows(seed_rows: Iterable[Mapping[str, Any]], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for seed in seed_rows:
        seed_id = str(seed["seed_id"])
        decision = seed.get("screening_decision", {})
        link = joined["links"].get(seed_id, {})
        screened_id = str(link.get("screened_candidate_id", ""))
        deep_selection = joined["deep_selection"].get(screened_id, {})
        deep_id = screened_id if screened_id in joined["deep"] else ""
        portfolio = joined["portfolio"].get(deep_id, {})
        result.append(
            {
                "seed_id": seed_id,
                "raw_intervention_assertion": seed.get("raw_intervention_assertion", ""),
                "canonical_disposition": seed.get("canonical_disposition", ""),
                "detailed_disposition": enum_value(decision.get("disposition")),
                "disposition_reason": _clean(decision.get("reason")),
                "representative_seed_id": decision.get("representative_seed_id", ""),
                "screening_outcome": enum_value(decision.get("screening_outcome")),
                "screened_candidate_id": screened_id,
                "deep_selection_disposition": enum_value(deep_selection.get("selection_disposition")),
                "deep_completion_disposition": enum_value(deep_selection.get("completion_disposition")),
                "deep_selection_reason": _clean(deep_selection.get("reason")),
                "deep_candidate_id": deep_id,
                "portfolio_disposition": enum_value(portfolio.get("disposition")),
                "portfolio_reason": _clean(portfolio.get("reason")),
            }
        )
    return result


def _identity_rows(seed_rows: Iterable[Mapping[str, Any]], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for seed in seed_rows:
        seed_id = str(seed["seed_id"])
        identity = dict(seed.get("identity_resolution", {}))
        normalized_id = str(identity.get("verified_normalized_intervention_id") or identity.get("normalized_intervention_id") or "")
        result.append(
            {
                "seed_id": seed_id,
                "raw_intervention_assertion": seed.get("raw_intervention_assertion", ""),
                "identity_status": enum_value(identity.get("status")),
                "identity_verified": identity.get("identity_verified", False),
                "normalized_intervention_id": normalized_id,
                "normalized_intervention": joined["normalized"].get(normalized_id, {}),
                "active_moiety_id": identity.get("active_moiety_id", ""),
                "canonical_disposition": seed.get("canonical_disposition", ""),
                "representative_seed_id": seed.get("representative_seed_id", ""),
                "conflict_values": _list(identity.get("conflict_values")),
                "source_mapping_ids": _list(identity.get("source_mapping_ids")),
            }
        )
    return result


def _deep_rows(snapshot: Mapping[str, Any], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate_id, candidate in sorted(joined["deep"].items()):
        package_id = str(candidate.get("deep_evidence_package_id") or candidate.get("package_id") or "")
        package = joined["packages"].get(package_id, {})
        statuses = {
            enum_value(assertion.get("status"))
            for identity in _list(package.get("identity_records"))
            if isinstance(identity, Mapping)
            for assertion in _list(identity.get("development_status_assertions"))
            if isinstance(assertion, Mapping) and assertion.get("status")
        }
        profile = joined["profiles"].get(candidate_id, {})
        statuses.update(str(value) for value in _list(profile.get("development_statuses")))
        statuses.discard("")
        preclinical_only = bool(statuses) and statuses.issubset({"preclinical", "no_documented_human_use"})
        result.append(
            {
                "candidate_id": candidate_id,
                "normalized_intervention_id": candidate.get("normalized_intervention_id", ""),
                "identity_record_id": candidate.get("identity_record_id", ""),
                "deep_evidence_package_id": package_id,
                "endpoint_ids": _list(candidate.get("endpoint_ids")),
                "claim_ids": _list(candidate.get("claim_ids")),
                "path_ids": _list(candidate.get("path_ids")),
                "development_statuses": sorted(statuses),
                "preclinical_only": preclinical_only,
                "deep_evidence_package": package,
                "decision_profile": profile,
                "ranking_preparation": joined["preparations"].get(candidate_id, {}),
                "audit_assignment": joined["assignments"].get(candidate_id, {}),
                "audit_record": joined["audits"].get(candidate_id, {}),
                "council_record": joined["councils"].get(candidate_id, {}),
                "portfolio_rank_record": joined["portfolio"].get(candidate_id, {}),
            }
        )
    return result


def _exclusion_rows(funnel: Iterable[Mapping[str, Any]], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in funnel:
        disposition = str(row.get("canonical_disposition", ""))
        screen = str(row.get("screening_outcome", ""))
        if disposition in {"baseline", "reject", "quarantine", "failed"}:
            result.append({"stage": "seed_disposition", "record_id": row.get("seed_id", ""), "disposition": disposition, "detailed_disposition": row.get("detailed_disposition", ""), "reason": row.get("disposition_reason", "")})
        if screen in {"screen_rejected", "screen_quarantined", "screen_failed"}:
            result.append({"stage": "screening", "record_id": row.get("seed_id", ""), "disposition": screen, "detailed_disposition": row.get("detailed_disposition", ""), "reason": row.get("disposition_reason", "")})
        deep_disposition = str(row.get("deep_selection_disposition", ""))
        if deep_disposition == "screen_only" or str(row.get("deep_completion_disposition", "")) in {"deep_quarantined", "deep_failed"}:
            result.append({"stage": "deep_selection", "record_id": row.get("screened_candidate_id", ""), "disposition": deep_disposition or row.get("deep_completion_disposition", ""), "detailed_disposition": row.get("deep_completion_disposition", ""), "reason": row.get("deep_selection_reason", "")})
    for candidate_id, record in sorted(joined["portfolio"].items()):
        disposition = enum_value(record.get("disposition"))
        if disposition not in {"finalist", "reserve"}:
            result.append({"stage": "portfolio", "record_id": candidate_id, "disposition": disposition, "detailed_disposition": "", "reason": _clean(record.get("reason"))})
    return result


def _gap_rows(snapshot: Mapping[str, Any], joined: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    gaps: dict[str, dict[str, Any]] = {}

    def add(category: str, subject_id: str, detail: str, severity: str = "material") -> None:
        if not detail:
            return
        body = {"category": category, "subject_id": subject_id, "severity": severity, "detail": _clean(detail)}
        gap_id = f"V7GAP-{canonical_sha256(body)[:24]}"
        gaps[gap_id] = {"gap_id": gap_id, **body}

    for proof in rows(snapshot, "coverage_proofs"):
        proof_id = _id(proof, "coverage_proof_id")
        state = enum_value(proof.get("coverage_state"))
        if state not in {"complete_for_declared_query_and_release", "no_relevant_hits_within_declared_query"}:
            add("source_coverage", proof_id, f"coverage state: {state}")
        for value in _list(proof.get("coverage_gaps")):
            add("source_coverage", proof_id, str(value))
        for value in _list(proof.get("source_specific_limitations")):
            add("source_limitation", proof_id, str(value), "contextual")
    for record in rows(snapshot, "unsupported_capabilities"):
        add("unsupported_capability", _id(record, "capability_record_id"), str(record.get("preserved_coverage_gap", "")))
    for row in rows(snapshot, "quarantined_seeds"):
        add("identity_or_screening", _id(row, "seed_id", "quarantine_id"), str(row.get("reason", "")))
    for candidate_id, package in sorted(joined["packages"].items()):
        for endpoint in _list(package.get("endpoint_assessments")):
            if isinstance(endpoint, Mapping) and enum_value(endpoint.get("status")) in {"insufficient", "not_assessed"}:
                add("endpoint_evidence", str(endpoint.get("endpoint_id", candidate_id)), str(endpoint.get("reason", "")))
    for candidate_id, assignment in sorted(joined["assignments"].items()):
        if enum_value(assignment.get("selection_status")) == "unaudited":
            add("scientific_audit", candidate_id, str(assignment.get("reason", "unaudited")))
    for candidate_id, profile in sorted(joined["profiles"].items()):
        uncertainty = profile.get("uncertainty", {})
        if isinstance(uncertainty, Mapping):
            band = str(uncertainty.get("band", ""))
            add("candidate_uncertainty", candidate_id, f"uncertainty band: {band}", band or "unknown")
    return [gaps[key] for key in sorted(gaps)]


def _audit_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    assignments = rows(snapshot, "audit_assignments")
    audits = index(rows(snapshot, "audit_records"), "assignment_id")
    corrections = rows(snapshot, "audit_corrections")
    selected = sum(enum_value(row.get("selection_status")) == "selected_for_audit" for row in assignments)
    result: list[dict[str, Any]] = [
        {
            "record_type": "coverage_summary",
            "population_denominator": len(assignments),
            "selected_for_audit": selected,
            "unaudited": len(assignments) - selected,
            "achieved_audits": len(audits),
            "correction_count": len(corrections),
        }
    ]
    for assignment in assignments:
        assignment_id = str(assignment.get("assignment_id", ""))
        result.append({"record_type": "assignment", **assignment, "audit_record": audits.get(assignment_id, {})})
    result.extend({"record_type": "correction", **row} for row in corrections)
    result.extend(
        {"record_type": "stratum_report", **row}
        for row in rows(snapshot, "audit_stratum_reports")
    )
    return result


def _candidate_markdown(cards: Iterable[Mapping[str, Any]]) -> str:
    cards_value = list(cards)
    lines = ["# Candidate evidence cards", "", "Hypothesis generation for expert review; not clinical advice.", ""]
    if not cards_value:
        lines.append("No completed deep candidates were present in the canonical snapshot.")
    for card in cards_value:
        profile = card.get("decision_profile", {})
        portfolio = card.get("portfolio_rank_record", {})
        package = card.get("deep_evidence_package", {})
        package_sources = [
            str(row.get("source_id"))
            for row in _list(package.get("sources") if isinstance(package, Mapping) else [])
            if isinstance(row, Mapping) and row.get("source_id")
        ]
        audit = card.get("audit_record", {})
        lines.extend(
            [
                f"## {_clean(card.get('candidate_id'))}",
                "",
                f"- Normalized intervention: `{_clean(card.get('normalized_intervention_id'))}`",
                f"- Development stratum: {', '.join(map(str, _list(card.get('development_statuses')))) or 'unknown'}; preclinical-only: `{str(card.get('preclinical_only', False)).lower()}`",
                f"- Endpoints: {', '.join(map(str, _list(card.get('endpoint_ids')))) or 'none'}",
                f"- Therapeutic support: `{_clean((profile.get('therapeutic_support') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Evidence quality: `{_clean((profile.get('evidence_quality') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Exposure feasibility: `{_clean((profile.get('exposure_feasibility') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Safety/tolerability: `{_clean((profile.get('safety_and_tolerability') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Novelty/underexploration: `{_clean((profile.get('novelty_underexploration') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Uncertainty: `{_clean((profile.get('uncertainty') or {}).get('band') if isinstance(profile, Mapping) else '')}`",
                f"- Portfolio disposition: `{enum_value(portfolio.get('disposition')) if isinstance(portfolio, Mapping) else ''}`",
                f"- Audit outcome: `{enum_value(audit.get('outcome')) if isinstance(audit, Mapping) else ''}`",
                f"- Grounded claims: {len(_list(card.get('claim_ids')))}; evidence records: {len(_list(package.get('evidence_records') if isinstance(package, Mapping) else []))}",
                f"- Source IDs: {', '.join(sorted(set(package_sources))) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _gap_markdown(gaps: Iterable[Mapping[str, Any]]) -> str:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in gaps:
        groups.setdefault(str(row.get("category", "other")), []).append(row)
    lines = ["# Uncertainty and evidence gaps", "", "All gaps are bounded to the declared sources and committed snapshot.", ""]
    if not groups:
        lines.append("No material unresolved gap was recorded in the supplied complete snapshot.")
    for category in sorted(groups):
        lines.extend([f"## {category.replace('_', ' ').title()}", ""])
        lines.extend(f"- `{row.get('subject_id', '')}`: {_clean(row.get('detail'))}" for row in groups[category])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summary_markdown(
    snapshot: Mapping[str, Any],
    counts: Mapping[str, Any],
    coverage: Iterable[Mapping[str, Any]],
    deep_candidates: Iterable[Mapping[str, Any]],
) -> str:
    coverage_value = list(coverage)
    deep_value = list(deep_candidates)
    states: dict[str, int] = {}
    for row in coverage_value:
        state = str(row.get("coverage_state", "unknown"))
        states[state] = states.get(state, 0) + 1
    return "\n".join(
        [
            "# Schema-v7 full-funnel summary",
            "",
            f"Status: **{snapshot.get('output_status')}**.",
            "",
            "This is bounded-scope hypothesis generation for expert review. It is not a clinical recommendation, proof of efficacy, or claim of universal exhaustiveness.",
            "",
            "## Funnel",
            "",
            f"- Seeds: {counts['seed_count']} (admit {counts['admit_count']}, merge {counts['merge_count']}, baseline {counts['baseline_count']}, reject {counts['reject_count']}, quarantine {counts['quarantine_count']}, failed {counts['failed_count']}).",
            f"- Screened candidates: {counts['screened_count']}; completed deep candidates: {counts['deep_count']}.",
            f"- Preclinical-only deep stratum: {sum(row.get('preclinical_only') is True for row in deep_value)}.",
            f"- Portfolio: finalists {counts['finalist_count']}, reserves {counts['reserve_count']}, not selected {counts['not_selected_count']}, audit rejected {counts['audit_rejected_count']}, audit quarantined {counts['audit_quarantined_count']}, interim {counts['interim_portfolio_count']}.",
            f"- Identity denominators: resolved-all {counts['identity_resolved_all_count']}, admitted {counts['identity_admitted_count']}, baseline {counts['identity_baseline_count']}, admitted breadth groups {counts['breadth_admitted_count']}, active moieties {counts['active_moiety_count']}.",
            "",
            "## Declared coverage",
            "",
            f"- Query plans: {len(coverage_value)}; states: {_clean(_json_text(states))}.",
            "",
            "See the machine-readable ledgers, uncertainty summary, audit report, and evidence cards for exact provenance and limitations.",
            "",
        ]
    )


def _ledger_count(name: str, snapshot: Mapping[str, Any], row_count: int) -> int:
    exact = {
        "source_universes_and_coverage.csv": len(rows(snapshot, "query_plans")),
        "candidate_seed_universe.jsonl": len(rows(snapshot, "candidate_seeds")),
        "screening_and_disposition_funnel.csv": len(rows(snapshot, "candidate_seeds")),
        "funnel_reconciliation.jsonl": 1,
        "identity_normalization_and_merges.jsonl": len(rows(snapshot, "candidate_seeds")),
        "unresolved_and_quarantined_seeds.csv": len(rows(snapshot, "quarantined_seeds")),
        "deeply_assessed_candidates.jsonl": len(rows(snapshot, "deep_candidates")),
        "evidence_strength_ranking.csv": len(rows(snapshot, "deep_candidates")),
        "novelty_information_value_ranking.csv": len(rows(snapshot, "deep_candidates")),
        "diversified_portfolio_ranking.csv": len(rows(snapshot, "deep_candidates")),
        "candidate_evidence_cards.jsonl": len(rows(snapshot, "deep_candidates")),
        "candidate_evidence_cards.md": len(rows(snapshot, "deep_candidates")),
        "full_funnel_summary.md": 1,
    }
    return exact.get(name, row_count)


def build_full_funnel(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_snapshot(snapshot)
    issues = validate_snapshot(normalized)
    if issues:
        raise V7OutputError("Committed snapshot validation failed:\n" + "\n".join(f"- {row.render()}" for row in issues))
    counts = asdict(reconcile(normalized))
    joined = _joined_ledgers(normalized)
    coverage = _coverage_rows(normalized)
    seed_universe = _seed_rows(normalized, joined)
    funnel = _funnel_rows(seed_universe, joined)
    identities = _identity_rows(seed_universe, joined)
    unresolved = rows(normalized, "quarantined_seeds")
    deep = _deep_rows(normalized, joined)
    deep_by_id = index(deep, "candidate_id")
    ranks = [
        _rank_row(row, deep_by_id.get(str(row.get("candidate_id")), {}))
        for row in rows(normalized, "portfolio_rank_records")
    ]
    evidence_rank = sorted(ranks, key=lambda row: int(row["evidence_strength_rank"]))
    novelty_rank = sorted(ranks, key=lambda row: int(row["novelty_information_value_rank"]))
    portfolio_rank = sorted(ranks, key=lambda row: (row["diversified_portfolio_rank"] in {"", None}, int(row["diversified_portfolio_rank"] or 10**9)))
    exclusions = _exclusion_rows(funnel, joined)
    gaps = _gap_rows(normalized, joined)
    audit = _audit_rows(normalized)

    payloads: dict[str, bytes] = {}
    row_counts: dict[str, int] = {}

    def put(name: str, value: tuple[bytes, int] | str, logical_count: int | None = None) -> None:
        if isinstance(value, str):
            payloads[name] = value.encode("utf-8")
            row_counts[name] = logical_count if logical_count is not None else 1
        else:
            payloads[name], row_counts[name] = value

    put("source_universes_and_coverage.csv", _csv(coverage, (
        "source_universe_id", "source_id", "source_release", "source_snapshot_at", "query_plan_id", "query_family_id", "required", "denominator_kind", "declared_total", "coverage_state", "returned_native_record_count", "normalized_record_count", "emitted_seed_count", "unvisited_record_count", "continuation_exhausted", "source_specific_limitations", "coverage_gaps",
    )))
    put("candidate_seed_universe.jsonl", _jsonl(seed_universe))
    put("screening_and_disposition_funnel.csv", _csv(funnel, (
        "seed_id", "raw_intervention_assertion", "canonical_disposition", "detailed_disposition", "disposition_reason", "representative_seed_id", "screening_outcome", "screened_candidate_id", "deep_selection_disposition", "deep_completion_disposition", "deep_selection_reason", "deep_candidate_id", "portfolio_disposition", "portfolio_reason",
    )))
    put("funnel_reconciliation.jsonl", _jsonl([{"record_type": "full_funnel_reconciliation", **counts}]))
    put("identity_normalization_and_merges.jsonl", _jsonl(identities))
    put("unresolved_and_quarantined_seeds.csv", _csv(unresolved, (
        "quarantine_id", "seed_id", "disposition", "identity_status", "reason", "unresolved_fields", "source_mapping_ids", "discovery_route_ids", "alias_ids", "can_advance",
    )))
    put("deeply_assessed_candidates.jsonl", _jsonl(deep))
    rank_fields = (
        "candidate_id", "evidence_strength_rank", "novelty_information_value_rank", "diversified_portfolio_rank", "portfolio_disposition", "evidence_component", "novelty_information_component", "diversity_component", "total_selection_utility", "audit_status", "audit_outcome", "council_disposition", "development_statuses", "preclinical_only", "diversity_contributions", "reason",
    )
    put("evidence_strength_ranking.csv", _csv(evidence_rank, rank_fields))
    put("novelty_information_value_ranking.csv", _csv(novelty_rank, rank_fields))
    put("diversified_portfolio_ranking.csv", _csv(portfolio_rank, rank_fields))
    put("exclusions_and_reasons.csv", _csv(exclusions, ("stage", "record_id", "disposition", "detailed_disposition", "reason")))
    put("candidate_evidence_cards.jsonl", _jsonl(deep))
    put("candidate_evidence_cards.md", _candidate_markdown(deep), len(deep))
    put("uncertainty_and_evidence_gaps.jsonl", _jsonl(gaps))
    put("uncertainty_and_evidence_gaps.md", _gap_markdown(gaps), len(gaps))
    put("audit_coverage_and_corrections.jsonl", _jsonl(audit))
    put("full_funnel_summary.md", _summary_markdown(normalized, counts, coverage, deep), 1)

    snapshot_hash = snapshot_sha256(normalized)
    provenance_rows: list[dict[str, Any]] = [
        {
            "record_type": "snapshot",
            "snapshot_id": normalized.get("snapshot_id", ""),
            "snapshot_sha256": snapshot_hash,
            "canonical_scientific_hash": normalized.get("provenance", {}).get("canonical_scientific_hash", ""),
            "execution_hash": normalized.get("provenance", {}).get("execution_hash", ""),
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
        }
    ]
    for collection in sorted(key for key in normalized if isinstance(normalized.get(key), (list, tuple))):
        provenance_rows.append({
            "record_type": "canonical_ledger",
            "collection": collection,
            "record_count": len(normalized[collection]),
            "projection_sha256": canonical_sha256(normalized[collection]),
        })
    recorded_commit_ids: set[str] = set()
    for commit in normalized.get("provenance", {}).get("commits", []):
        if isinstance(commit, Mapping):
            commit_id = str(commit.get("commit_id", ""))
            recorded_commit_ids.add(commit_id)
            provenance_rows.append({"record_type": "commit", **commit})
    for commit_id in normalized.get("provenance", {}).get("commit_ids", []):
        if str(commit_id) not in recorded_commit_ids:
            provenance_rows.append({"record_type": "commit", "commit_id": commit_id})
    for name, payload in sorted(payloads.items()):
        provenance_rows.append({
            "record_type": "emitted_artifact",
            "filename": name,
            "sha256": hashlib.sha256(payload).hexdigest().upper(),
            "bytes": len(payload),
            "row_count": row_counts[name],
        })
    put("machine_readable_provenance.jsonl", _jsonl(provenance_rows))

    entries: list[dict[str, Any]] = []
    for spec in ARTIFACT_SPECS:
        payload = payloads[spec.filename]
        entries.append(
            {
                "filename": spec.filename,
                "media_type": spec.media_type,
                "sha256": hashlib.sha256(payload).hexdigest().upper(),
                "bytes": len(payload),
                "row_count": row_counts[spec.filename],
                "ledger_count": _ledger_count(spec.filename, normalized, row_counts[spec.filename]),
                "cardinality_basis": spec.cardinality_basis,
            }
        )
    manifest_body = {
        "schema_version": 7,
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": normalized.get("snapshot_id", ""),
        "snapshot_sha256": snapshot_hash,
        "output_status": normalized.get("output_status", OutputStatus.DIAGNOSTIC_PARTIAL.value),
        "reconciliation": counts,
        "artifacts": entries,
        "post_run_benchmark_join_key": {
            "snapshot_id": normalized.get("snapshot_id", ""),
            "snapshot_sha256": snapshot_hash,
            "output_contract_version": OUTPUT_CONTRACT_VERSION,
        },
    }
    output_manifest_id = f"V7OUTPUT-{canonical_sha256(manifest_body)[:28]}"
    manifest = {**manifest_body, "output_manifest_id": output_manifest_id}
    return {
        "schema_version": 7,
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
        "output_manifest": manifest,
        "reconciliation": counts,
        "artifact_payloads": {name: payload.decode("utf-8") for name, payload in payloads.items()},
        "post_run_benchmark_join_key": manifest_body["post_run_benchmark_join_key"],
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_full_funnel_outputs(
    run_folder: str | Path,
    snapshot: Mapping[str, Any] | None = None,
) -> tuple[Path, Path]:
    run_root = Path(run_folder).expanduser().resolve()
    committed = normalize_snapshot(snapshot) if snapshot is not None else load_committed_snapshot(run_root)
    result = build_full_funnel(committed)
    output_root = run_root / OUTPUT_DIRECTORY
    for name, text in result["artifact_payloads"].items():
        _atomic_write(output_root / name, str(text).encode("utf-8"))
    manifest_path = output_root / "artifact_manifest.json"
    _atomic_write(manifest_path, json.dumps(result["output_manifest"], ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return manifest_path, output_root / "full_funnel_summary.md"


class V7OutputAdapter:
    """Production-facing implementation of the V7-PROD-OUTPUTS protocol."""

    def build_full_funnel(self, committed_snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        return build_full_funnel(committed_snapshot)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "--check-reference":
        path = Path(argv[2]).expanduser().resolve()
        expected = render_reference_contract()
        if not path.is_file() or path.read_text(encoding="utf-8-sig") != expected:
            print(f"Generated contract reference is stale: {path}", file=sys.stderr)
            return 1
        print("OUTPUT CONTRACT REFERENCE CURRENT")
        return 0
    if len(argv) != 2:
        print("Usage: v7_outputs.py <run_folder> | --check-reference <reference.md>", file=sys.stderr)
        return 2
    try:
        manifest, summary = write_full_funnel_outputs(argv[1])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(manifest)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


__all__ = [
    "V7OutputAdapter",
    "V7OutputError",
    "build_full_funnel",
    "write_full_funnel_outputs",
]
