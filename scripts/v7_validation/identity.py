"""Identity normalization, merge, and quarantine validation."""

from __future__ import annotations

from typing import Any, Mapping

from .common import ValidationIssue, enum_value, index, issue, rows


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seeds = rows(snapshot, "candidate_seeds")
    resolutions = rows(snapshot, "identity_resolutions")
    decisions = index(rows(snapshot, "screening_decisions"), "seed_id")
    resolution_by_seed = index(resolutions, "seed_id")
    seed_ids = {str(row.get("seed_id")) for row in seeds if row.get("seed_id")}
    if set(resolution_by_seed) != seed_ids:
        issues.append(issue("identity", "RESOLUTION_COVERAGE", "identity resolution must cover every and only canonical seed"))
    normalized = index(rows(snapshot, "normalized_interventions"), "normalized_intervention_id")
    expected_quarantine_ids = {
        seed_id
        for seed_id, decision in decisions.items()
        if enum_value(decision.get("canonical_disposition")) == "quarantine"
    }
    recorded_quarantine_ids = {
        str(row.get("seed_id"))
        for row in rows(snapshot, "quarantined_seeds")
        if row.get("seed_id")
    }
    if recorded_quarantine_ids != expected_quarantine_ids:
        issues.append(issue("identity", "QUARANTINE_COVERAGE", "quarantine records must cover every and only quarantined seed"))
    for seed_id, resolution in resolution_by_seed.items():
        status = enum_value(resolution.get("status"))
        normalized_id = str(
            resolution.get("verified_normalized_intervention_id")
            or resolution.get("normalized_intervention_id")
            or ""
        )
        disposition = enum_value(decisions.get(seed_id, {}).get("canonical_disposition"))
        if status in {"unresolved", "conflicting", "quarantined"} and normalized_id:
            issues.append(issue("identity", "UNRESOLVED_ID", f"unresolved seed {seed_id} cannot contribute a normalized identity"))
        if status in {"unresolved", "conflicting", "quarantined"} and disposition != "quarantine":
            issues.append(issue("identity", "UNRESOLVED_DISPOSITION", f"unresolved seed {seed_id} must be quarantined"))
        if disposition == "admit":
            if not normalized_id or normalized_id not in normalized:
                issues.append(issue("identity", "ADMITTED_ID", f"admitted seed {seed_id} lacks a canonical normalized intervention"))
            if resolution.get("identity_verified") is not True:
                issues.append(issue("identity", "ADMITTED_VERIFICATION", f"admitted seed {seed_id} must have verified identity"))
    deep_ids = {
        str(row.get("normalized_intervention_id"))
        for row in rows(snapshot, "deep_candidates")
        if row.get("normalized_intervention_id")
    }
    if not deep_ids.issubset(normalized):
        issues.append(issue("identity", "DEEP_ID", "every deep candidate normalized identity must exist in the canonical identity ledger"))
    for row in rows(snapshot, "quarantined_seeds"):
        if row.get("can_advance") is not False:
            issues.append(issue("identity", "QUARANTINE_ADVANCE", f"quarantined seed {row.get('seed_id')} must have can_advance=false"))
    return issues


__all__ = ["validate"]
