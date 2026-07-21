"""Separate schema-v7 ranking validation."""

from __future__ import annotations

from typing import Any, Mapping

from .common import ValidationIssue, issue, rows


_DIMENSIONS = (
    "therapeutic_support",
    "evidence_quality",
    "mechanistic_coherence",
    "human_clinical_evidence",
    "human_derived_model_evidence",
    "endpoint_specificity",
    "clinical_translatability",
    "exposure_feasibility",
    "safety_and_tolerability",
    "repurposing_readiness",
    "novelty_underexploration",
    "uncertainty",
    "information_value",
)


def _validate_rank(values: list[dict[str, Any]], field: str, issues: list[ValidationIssue]) -> None:
    ranks = [row.get(field) for row in values]
    integers = sorted(value for value in ranks if isinstance(value, int) and not isinstance(value, bool))
    if len(integers) != len(values) or integers != list(range(1, len(values) + 1)):
        issues.append(issue("ranking", "RANK_SEQUENCE", f"{field} must be a complete deterministic 1..N order"))


def validate(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    profiles = rows(snapshot, "decision_profiles")
    for profile in profiles:
        missing = [field for field in _DIMENSIONS if not isinstance(profile.get(field), Mapping)]
        if missing:
            issues.append(issue("ranking", "DIMENSIONS", f"candidate {profile.get('candidate_id')} lacks typed dimensions {missing}"))
    records = rows(snapshot, "portfolio_rank_records")
    deep_ids = {str(row.get("candidate_id")) for row in rows(snapshot, "deep_candidates")}
    ranked_ids = {str(row.get("candidate_id")) for row in records}
    if ranked_ids != deep_ids:
        issues.append(issue("ranking", "RANK_COVERAGE", "the three post-audit ranks must cover every and only deep candidate"))
    if records:
        _validate_rank(records, "evidence_strength_rank", issues)
        _validate_rank(records, "novelty_information_value_rank", issues)
        diversified = [row for row in records if row.get("diversified_portfolio_rank") is not None]
        if diversified:
            _validate_rank(diversified, "diversified_portfolio_rank", issues)
    return issues


__all__ = ["validate"]
