#!/usr/bin/env python3
"""Persisted retrieval-backed schema-v7 audit and portfolio aggregate.

The adapter consumes the accepted Stage 5-6 aggregate, freezes the complete
deep-candidate decision set and audit plan, verifies retrieval-backed audit
outcomes, applies append-only package revisions, reduces typed council work,
and emits a reconciled deterministic portfolio.  It never imports benchmark
fixtures or evaluation labels.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_audit_portfolio import (
    AuditAssignment,
    AuditDecisionEffect,
    AuditOutcome,
    AuditRecord,
    AuditSelectionStatus,
    AuditStratum,
    CouncilDisposition,
    CouncilRecord,
    DiversityDimension,
    PortfolioCandidateFrame,
    PortfolioDisposition,
    PortfolioSelectionStatus,
    make_diversity_features,
    make_portfolio_policy,
    make_scaffold_descriptor,
    select_diversified_portfolio,
)
from v7_case_model import (
    CaseRevision,
    CaseStatus,
    build_case_bundle,
    validate_case_revision,
)
from v7_production_screen_deep import validate_screen_deep_aggregate
from v7_triage_ranking import (
    RankingPreparationRecord,
    ResearchPriorityTier,
    TherapeuticConfidenceTier,
    TriageCategory,
)


SCHEMA_VERSION = 7
MODEL_VERSION = "schema-v7-production-audit-portfolio-v1"
AUDIT_PLAN_VERSION = "schema-v7-risk-size-retrieval-audit-v1"
PORTFOLIO_PLAN_ID_RULE = "case-revision+audit-revision+plan-version"
PORTFOLIO_AGGREGATE_ID_RULE = "canonical-retained-input-portfolio-aggregate-v1"

AUDIT_CATEGORIES = (
    "candidate_tier",
    "disposition",
    "source",
    "modality",
    "endpoint",
    "identity_uncertainty",
    "safety_risk",
    "novelty",
    "claim_impact",
)
RISK_LEVELS = {"low", "moderate", "high", "critical"}
ESCALATION_MODES = {"census_affected_stratum", "quarantine_unaudited"}
DECISION_OUTPUT_NAMES = (
    "therapeutic_support",
    "evidence_quality",
    "readiness",
    "novelty",
    "uncertainty",
    "information_value",
    "portfolio_diversity",
)
CORRECTION_FIELDS = {
    "chemical_identity",
    "active_moiety_mapping",
    "claim_statement",
    "direction",
    "human_relevance",
    "causal_path",
    "endpoint",
    "candidate_class",
    "exposure",
    "safety",
    "ranking_feature",
}
CORRECTION_ACTIONS = {"correct", "supersede", "quarantine", "reject"}

_PRIMARY_ID_FIELDS = (
    "identity_record_id",
    "deep_evidence_record_id",
    "evidence_span_id",
    "safety_record_id",
    "exposure_record_id",
    "study_record_id",
    "effect_record_id",
    "path_id",
    "claim_id",
    "preparation_id",
    "profile_id",
    "screen_record_id",
    "seed_id",
    "source_record_id",
)
_CORRECTION_ID_FIELDS = {
    "chemical_identity": {"identity_record_id"},
    "active_moiety_mapping": {"identity_record_id"},
    "claim_statement": {"claim_id"},
    "direction": {"claim_id"},
    "human_relevance": {"claim_id"},
    "causal_path": {"path_id"},
    "endpoint": {"claim_id"},
    "candidate_class": {"screen_record_id", "preparation_id"},
    "exposure": {"exposure_record_id"},
    "safety": {"safety_record_id"},
    "ranking_feature": {"profile_id", "preparation_id"},
}
_FORBIDDEN_RUNTIME_KEY_PARTS = (
    "benchmark",
    "golden",
    "expected_outcome",
    "expected_disposition",
    "holdout",
    "partition_label",
)
_FORBIDDEN_RUNTIME_VALUE_LABELS = frozenset(
    {
        "benchmark",
        "benchmark_only",
        "certification_holdout",
        "expected_disposition",
        "expected_outcome",
        "golden",
        "golden_control",
        "holdout",
        "partition_label",
    }
)


class PortfolioAggregateError(ValueError):
    """Raised when the frozen audit or portfolio input is invalid."""


class PortfolioAggregateConflictError(PortfolioAggregateError):
    """Raised when one immutable audit revision is replayed with drift."""


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _plain(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def content_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{content_sha256(value)[:24]}"


def _text(value: Any, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise PortfolioAggregateError(f"{label} must be nonempty")
    return text


def _strings(values: Iterable[Any], label: str, *, required: bool = False) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise PortfolioAggregateError(f"{label} must be a list")
    rows = sorted({_text(value, label) for value in values})
    if required and not rows:
        raise PortfolioAggregateError(f"{label} must be nonempty")
    return rows


def _safe_component(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", _text(value, "persistent identity"))
    safe = safe.strip("._")
    if not safe:
        raise PortfolioAggregateError("persistent identity cannot be converted to a safe path")
    return safe


def _coerce_case(value: CaseRevision | Mapping[str, Any]) -> CaseRevision:
    if isinstance(value, CaseRevision):
        case = value
        validate_case_revision(case)
    else:
        if not isinstance(value, Mapping):
            raise PortfolioAggregateError("case_revision must be a CaseRevision or mapping")
        raw = value.get("original_input", value)
        if not isinstance(raw, Mapping):
            raise PortfolioAggregateError("case_revision.original_input must be a mapping")
        case = build_case_bundle(raw).case_revision
        for field_name in ("case_id", "case_revision_id", "source_input_sha256"):
            supplied = value.get(field_name)
            if supplied is not None and supplied != getattr(case, field_name):
                raise PortfolioAggregateConflictError(
                    f"case_revision.{field_name} conflicts with the rebuilt canonical case"
                )
        validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise PortfolioAggregateError("audit/portfolio work requires a ready immutable case")
    return case


def _reject_benchmark_labels(value: Any, path: str = "runtime") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_RUNTIME_KEY_PARTS):
                raise PortfolioAggregateError(
                    f"benchmark/evaluation label is prohibited from runtime packets or ledgers: {path}.{key}"
                )
            _reject_benchmark_labels(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_benchmark_labels(item, f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        identifier_like = re.fullmatch(r"[A-Za-z0-9_.:/-]+", value) is not None
        embedded_label = normalized in _FORBIDDEN_RUNTIME_VALUE_LABELS or (
            identifier_like
            and any(
                normalized.startswith(f"{label}_")
                or normalized.endswith(f"_{label}")
                or f"_{label}_" in normalized
                for label in _FORBIDDEN_RUNTIME_VALUE_LABELS
            )
        )
        if embedded_label:
            raise PortfolioAggregateError(
                f"benchmark/evaluation label is prohibited from runtime packets or ledgers: {path}"
            )


def _values_for_keys(value: Any, keys: set[str]) -> list[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in keys and item not in (None, ""):
                if isinstance(item, (list, tuple)):
                    result.update(str(row) for row in item if row not in (None, ""))
                else:
                    result.add(str(item))
            result.update(_values_for_keys(item, keys))
    elif isinstance(value, (list, tuple)):
        for item in value:
            result.update(_values_for_keys(item, keys))
    return sorted(result)


def _primary_id(record: Mapping[str, Any]) -> tuple[str, str] | None:
    for field_name in _PRIMARY_ID_FIELDS:
        value = record.get(field_name)
        if value not in (None, ""):
            return field_name, str(value)
    return None


def _record_index(value: Any) -> dict[str, tuple[str, dict[str, Any]]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("package"), Mapping):
        raise PortfolioAggregateError("deep record is missing its canonical package")
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    package = value["package"]
    containers: list[Any] = []
    for name in (
        "identity_records",
        "claims",
        "paths",
        "sources",
        "evidence_spans",
        "evidence_records",
    ):
        containers.extend(package.get(name, []))
    for name in (
        "structured_safety",
        "structured_exposure",
    ):
        containers.extend(value.get(name, []))
    containers.extend(
        [value.get("candidate_decision_profile"), value.get("ranking_preparation")]
    )
    for item in containers:
        if not isinstance(item, Mapping):
            continue
        primary = _primary_id(item)
        if primary is None:
            continue
        field_name, record_id = primary
        row = _plain(item)
        if record_id in result and result[record_id] != (field_name, row):
            raise PortfolioAggregateError(f"canonical record identity is not unique: {record_id}")
        result[record_id] = (field_name, row)
    return result


def _replace_record(
    value: Any,
    target_id: str,
    target_value: Mapping[str, Any],
    replacement: Mapping[str, Any],
) -> tuple[Any, int]:
    count = 0

    def visit(item: Any) -> Any:
        nonlocal count
        if isinstance(item, Mapping):
            primary = _primary_id(item)
            if (
                primary is not None
                and primary[1] == target_id
                and _plain(item) == _plain(target_value)
            ):
                count += 1
                return copy.deepcopy(dict(replacement))
            return {str(key): visit(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [visit(nested) for nested in item]
        if isinstance(item, tuple):
            return [visit(nested) for nested in item]
        return copy.deepcopy(item)

    return visit(value), count


def _normalize_sampling_rules(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise PortfolioAggregateError("plan.sampling_rules must be a list")
    result: dict[str, dict[str, Any]] = {}
    expected = {
        "category",
        "risk_level",
        "minimum",
        "rate_basis_points",
        "maximum",
        "acceptance_threshold",
        "escalation_mode",
    }
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise PortfolioAggregateError("each sampling rule has an invalid field set")
        category = _text(raw["category"], "sampling category")
        if category not in AUDIT_CATEGORIES or category in result:
            raise PortfolioAggregateError("sampling categories must be unique and complete")
        risk = _text(raw["risk_level"], "risk level")
        if risk not in RISK_LEVELS:
            raise PortfolioAggregateError("sampling risk level is invalid")
        minimum = raw["minimum"]
        rate = raw["rate_basis_points"]
        maximum = raw["maximum"]
        threshold = raw["acceptance_threshold"]
        if any(isinstance(row, bool) or not isinstance(row, int) for row in (minimum, rate, maximum, threshold)):
            raise PortfolioAggregateError("sampling counts, rate, and threshold must be integers")
        if minimum < 1 or maximum < minimum or not 0 <= rate <= 10_000 or threshold < 0:
            raise PortfolioAggregateError("sampling rule bounds are invalid")
        escalation = _text(raw["escalation_mode"], "escalation mode")
        if escalation not in ESCALATION_MODES:
            raise PortfolioAggregateError("sampling escalation mode is invalid")
        result[category] = {
            "category": category,
            "risk_level": risk,
            "minimum": minimum,
            "rate_basis_points": rate,
            "maximum": maximum,
            "acceptance_threshold": threshold,
            "escalation_mode": escalation,
        }
    if set(result) != set(AUDIT_CATEGORIES):
        raise PortfolioAggregateError("sampling rules must cover every required audit category")
    return result


def _normalize_portfolio_policy(value: Any) -> dict[str, Any]:
    input_expected = {
        "finalist_capacity",
        "reserve_capacity",
        "evidence_weight",
        "information_weight",
        "diversity_weight",
        "diversity_dimension_weights",
        "allowed_therapeutic_tiers",
    }
    normalized_expected = input_expected | {"policy_id", "selection_rule_version"}
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(input_expected),
        frozenset(normalized_expected),
    }:
        raise PortfolioAggregateError("plan.portfolio_policy has an invalid field set")
    weights = value["diversity_dimension_weights"]
    if isinstance(weights, Mapping):
        weight_map = dict(weights)
    elif isinstance(weights, list):
        weight_map = {str(row[0]): row[1] for row in weights if isinstance(row, list) and len(row) == 2}
    else:
        weight_map = {}
    if set(weight_map) != {row.value for row in DiversityDimension}:
        raise PortfolioAggregateError("portfolio policy must weight all diversity dimensions")
    try:
        policy = make_portfolio_policy(
            finalist_capacity=value["finalist_capacity"],
            reserve_capacity=value["reserve_capacity"],
            evidence_weight=value["evidence_weight"],
            information_weight=value["information_weight"],
            diversity_weight=value["diversity_weight"],
            diversity_dimension_weights={
                DiversityDimension(key): number for key, number in weight_map.items()
            },
            allowed_therapeutic_tiers=(
                TherapeuticConfidenceTier(row)
                for row in value["allowed_therapeutic_tiers"]
            ),
        )
    except (TypeError, ValueError) as exc:
        raise PortfolioAggregateError(f"portfolio policy is invalid: {exc}") from exc
    return _plain(policy)


def _normalize_scaffolds(value: Any, candidate_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PortfolioAggregateError("plan.scaffolds must be a list")
    expected = {"candidate_id", "scaffold_key", "method", "version", "identity_record_ids"}
    rows: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise PortfolioAggregateError("scaffold descriptor has an invalid field set")
        candidate_id = _text(raw["candidate_id"], "scaffold candidate_id")
        key = raw["scaffold_key"]
        if key is not None:
            key = _text(key, "scaffold key")
        rows.append(
            {
                "candidate_id": candidate_id,
                "scaffold_key": key,
                "method": _text(raw["method"], "scaffold method"),
                "version": _text(raw["version"], "scaffold version"),
                "identity_record_ids": _strings(
                    raw["identity_record_ids"], "scaffold identity record ID", required=True
                ),
            }
        )
    if {row["candidate_id"] for row in rows} != candidate_ids or len(rows) != len(candidate_ids):
        raise PortfolioAggregateError("plan.scaffolds must cover every deep candidate exactly once")
    return sorted(rows, key=lambda row: row["candidate_id"])


def _normalize_plan(plan: Any, deep_frame: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "plan_version",
        "audit_revision",
        "sampling_seed",
        "sampling_rules",
        "subject_author_ids",
        "portfolio_policy",
        "scaffolds",
        "supersedes_portfolio_aggregate_id",
    }
    if not isinstance(plan, Mapping) or set(plan) != expected:
        raise PortfolioAggregateError("frozen audit plan.plan has an invalid field set")
    if plan["plan_version"] != AUDIT_PLAN_VERSION:
        raise PortfolioAggregateError("audit plan version mismatch")
    candidates = {str(row["candidate_id"]) for row in deep_frame["deep_packages"]}
    authors = plan["subject_author_ids"]
    if not isinstance(authors, Mapping):
        raise PortfolioAggregateError("plan.subject_author_ids must be an object")
    normalized_authors = {
        _text(subject_id, "subject author key"): _strings(
            author_ids, "subject author ID", required=True
        )
        for subject_id, author_ids in authors.items()
    }
    supersedes = plan["supersedes_portfolio_aggregate_id"]
    if supersedes is not None:
        supersedes = _text(supersedes, "superseded portfolio aggregate ID")
    return {
        "plan_version": AUDIT_PLAN_VERSION,
        "audit_revision": _text(plan["audit_revision"], "audit revision"),
        "sampling_seed": _text(plan["sampling_seed"], "sampling seed"),
        "sampling_rules": [
            row for _, row in sorted(_normalize_sampling_rules(plan["sampling_rules"]).items())
        ],
        "subject_author_ids": normalized_authors,
        "portfolio_policy": _normalize_portfolio_policy(plan["portfolio_policy"]),
        "scaffolds": _normalize_scaffolds(plan["scaffolds"], candidates),
        "supersedes_portfolio_aggregate_id": supersedes,
    }


def _decision_output_band(row: Mapping[str, Any], name: str) -> str:
    value = row.get("separate_decision_outputs", {}).get(name, {})
    if not isinstance(value, Mapping):
        return "unknown"
    return str(value.get("band", value.get("status", "unknown")))


def _source_stratum(value: Any) -> str:
    source_id = _text(value, "audit source ID")
    for separator in (":", "/", "|"):
        if separator in source_id:
            return source_id.split(separator, 1)[0]
    parts = source_id.split("-")
    return parts[0] if len(parts) > 1 else source_id


def _audit_units(deep_frame: Mapping[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    deep_ids = {str(row["candidate_id"]) for row in deep_frame["deep_packages"]}
    deep_by_id = {str(row["candidate_id"]): row for row in deep_frame["deep_packages"]}

    def add(
        *,
        subject_id: str,
        candidate_id: str | None,
        unit_kind: str,
        mandatory: bool,
        mandatory_reason: str,
        candidate_tier: str,
        disposition: str,
        source_ids: Iterable[str],
        modalities: Iterable[str],
        endpoints: Iterable[str],
        identity_uncertainty: str,
        safety_risk: str,
        novelty: str,
        claim_impact: str,
    ) -> None:
        values = {
            "candidate_tier": [candidate_tier],
            "disposition": [disposition],
            "source": sorted({_source_stratum(row) for row in source_ids}) or ["unknown"],
            "modality": _strings(modalities, "audit modality") or ["unknown"],
            "endpoint": _strings(endpoints, "audit endpoint") or ["not_applicable"],
            "identity_uncertainty": [identity_uncertainty],
            "safety_risk": [safety_risk],
            "novelty": [novelty],
            "claim_impact": [claim_impact],
        }
        body = {
            "subject_id": subject_id,
            "candidate_id": candidate_id,
            "unit_kind": unit_kind,
            "mandatory_census": mandatory,
            "mandatory_reason": mandatory_reason,
            "stratum_values": values,
        }
        units.append({"audit_unit_id": _stable_id("AUDIT-UNIT", body), **body})

    for candidate_id, deep in sorted(deep_by_id.items()):
        package = deep["package"]
        novelty = _decision_output_band(deep, "novelty")
        uncertainty = _decision_output_band(deep, "uncertainty")
        identity = next(
            row
            for row in package["identity_records"]
            if row["identity_record_id"] == package["current_identity_record_id"]
        )
        identity_state = str(identity["resolution_status"])
        add(
            subject_id=str(identity["identity_record_id"]),
            candidate_id=candidate_id,
            unit_kind="deep_identity",
            mandatory=True,
            mandatory_reason="exact identity can promote or block portfolio membership",
            candidate_tier="deep",
            disposition="deep",
            source_ids=_values_for_keys(identity, {"source_record_id"}),
            modalities=("authoritative_pharmacology",),
            endpoints=package["screened_candidate"]["endpoint_ids"],
            identity_uncertainty=identity_state,
            safety_risk="not_applicable",
            novelty=novelty,
            claim_impact="decision_critical",
        )
        evidence = {row["deep_evidence_record_id"]: row for row in package["evidence_records"]}
        for claim in package["claims"]:
            linked = [evidence[row] for row in claim["evidence_record_ids"]]
            add(
                subject_id=str(claim["claim_id"]),
                candidate_id=candidate_id,
                unit_kind="deep_claim",
                mandatory=True,
                mandatory_reason="support/counterevidence claim can change eligibility, order, or cutoff",
                candidate_tier="deep",
                disposition="deep",
                source_ids=(row["source_id"] for row in linked),
                modalities=(claim["evidence_modality"],),
                endpoints=(claim["scope"]["endpoint_id"],),
                identity_uncertainty=identity_state,
                safety_risk="not_applicable",
                novelty=novelty,
                claim_impact="decision_critical",
            )
        for safety in deep["structured_safety"]:
            risk = str(deep["candidate_decision_profile"]["safety_and_tolerability"]["band"])
            add(
                subject_id=str(safety["safety_record_id"]),
                candidate_id=candidate_id,
                unit_kind="safety",
                mandatory=True,
                mandatory_reason="exact-form safety can promote or block a finalist",
                candidate_tier="deep",
                disposition="deep",
                source_ids=safety["source_record_ids"],
                modalities=("safety_adverse_event",),
                endpoints=package["screened_candidate"]["endpoint_ids"],
                identity_uncertainty=identity_state,
                safety_risk=risk,
                novelty=novelty,
                claim_impact="decision_critical",
            )
        for exposure in deep["structured_exposure"]:
            risk = str(deep["candidate_decision_profile"]["exposure_feasibility"]["band"])
            add(
                subject_id=str(exposure["exposure_record_id"]),
                candidate_id=candidate_id,
                unit_kind="exposure",
                mandatory=True,
                mandatory_reason="exact-form exposure can promote or block a finalist",
                candidate_tier="deep",
                disposition="deep",
                source_ids=exposure["source_record_ids"],
                modalities=("authoritative_pharmacology",),
                endpoints=package["screened_candidate"]["endpoint_ids"],
                identity_uncertainty=identity_state,
                safety_risk=risk,
                novelty=novelty,
                claim_impact="decision_critical",
            )
        for source in package["sources"]:
            add(
                subject_id=str(source["source_record_id"]),
                candidate_id=candidate_id,
                unit_kind="deep_source",
                mandatory=False,
                mandatory_reason="residual source record receives deterministic sampling",
                candidate_tier="deep",
                disposition="deep",
                source_ids=(source["source_id"],),
                modalities=("source_record",),
                endpoints=package["screened_candidate"]["endpoint_ids"],
                identity_uncertainty=identity_state,
                safety_risk="not_applicable",
                novelty=novelty,
                claim_impact="residual",
            )

    screen_only = {
        str(row["candidate_id"])
        for row in deep_frame["deep_selection"].get("screen_only", [])
    }
    for screen in deep_frame["screen_records"]:
        candidate_id = str(screen["candidate_id"])
        outcome = str(screen["screening_outcome"])
        disposition = "screen_only" if candidate_id in screen_only else outcome
        add(
            subject_id=str(screen["screen_record_id"]),
            candidate_id=candidate_id if candidate_id in deep_ids else None,
            unit_kind="screen_record",
            mandatory=False,
            mandatory_reason="earlier-funnel screen loss receives deterministic sampling",
            candidate_tier="screen",
            disposition=disposition,
            source_ids=(),
            modalities=(),
            endpoints=_values_for_keys(screen, {"endpoint_id"}),
            identity_uncertainty="resolved" if outcome != "quarantined" else "unresolved",
            safety_risk="screen_flag",
            novelty=_decision_output_band(deep_by_id[candidate_id], "novelty") if candidate_id in deep_by_id else "unknown",
            claim_impact="earlier_funnel",
        )

    admitted = deep_frame["retained_inputs"]["admitted_frame"]
    seeds = {str(row["seed_id"]): row for row in admitted["seeds"]}
    for disposition in admitted["seed_dispositions"]:
        seed_id = str(disposition["seed_id"])
        seed = seeds[seed_id]
        add(
            subject_id=seed_id,
            candidate_id=None,
            unit_kind="seed_disposition",
            mandatory=False,
            mandatory_reason="seed/merge/baseline/rejection/quarantine loss receives deterministic sampling",
            candidate_tier="seed",
            disposition=str(disposition["canonical_disposition"]),
            source_ids=_values_for_keys(seed, {"source_universe_id"}),
            modalities=_values_for_keys(seed, {"evidence_modality", "evidence_modalities"}),
            endpoints=_values_for_keys(seed, {"endpoint_id", "endpoint_ids"}),
            identity_uncertainty=str(disposition.get("identity_status", "unknown")),
            safety_risk="not_assessed",
            novelty="not_assessed",
            claim_impact="earlier_funnel",
        )

    unit_ids = [row["audit_unit_id"] for row in units]
    subject_ids = [row["subject_id"] for row in units]
    if len(unit_ids) != len(set(unit_ids)) or len(subject_ids) != len(set(subject_ids)):
        raise PortfolioAggregateError("audit population contains duplicate unit or subject identities")
    return sorted(units, key=lambda row: row["audit_unit_id"])


def _diversity_features(deep_frame: Mapping[str, Any], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    scaffold_by_candidate = {row["candidate_id"]: row for row in plan["scaffolds"]}
    result: list[dict[str, Any]] = []
    for deep in sorted(deep_frame["deep_packages"], key=lambda row: row["candidate_id"]):
        candidate_id = str(deep["candidate_id"])
        package = deep["package"]
        identity = next(
            row for row in package["identity_records"]
            if row["identity_record_id"] == package["current_identity_record_id"]
        )
        scaffold = scaffold_by_candidate[candidate_id]
        if not set(scaffold["identity_record_ids"]).issubset(
            {str(row["identity_record_id"]) for row in package["identity_records"]}
        ):
            raise PortfolioAggregateError("scaffold descriptor cites identity outside its package")
        routes = package["screened_candidate"]["structured_routes"]
        targets = sorted(
            {
                str(row["intervention_target"]["node_id"])
                for row in routes
                if row["intervention_target"].get("node_id")
            }
        )
        mechanisms = sorted(
            {
                f"{row['causal_route']}|{row['action']}|{row['direction']}"
                for row in routes
            }
        )
        route_ids = sorted({str(row["causal_route"]) for row in routes})
        modalities = sorted({str(row["evidence_modality"]) for row in package["claims"]})
        endpoints = sorted({str(row["endpoint_id"]) for row in package["endpoint_assessments"]})
        development = sorted(
            {
                str(row["status"])
                for record in package["identity_records"]
                for row in record["development_status_assertions"]
            }
        ) or ["unknown"]
        uncertainty = [_decision_output_band(deep, "uncertainty")]
        result.append(
            {
                "candidate_id": candidate_id,
                "target_ids": targets,
                "mechanism_ids": mechanisms,
                "causal_route_ids": route_ids,
                "scaffold": scaffold,
                "evidence_modalities": modalities,
                "endpoint_ids": endpoints,
                "development_statuses": development,
                "uncertainty_bands": uncertainty,
            }
        )
    return result


def _build_freeze(case: CaseRevision, deep_frame: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    units = _audit_units(deep_frame)
    authors = plan["subject_author_ids"]
    missing_authors = sorted(
        {row["subject_id"] for row in units if row["mandatory_census"]} - set(authors)
    )
    if missing_authors:
        raise PortfolioAggregateError(
            f"mandatory audit subjects lack frozen author identities: {missing_authors[:3]}"
        )
    rules = {row["category"]: row for row in plan["sampling_rules"]}
    strata: dict[str, dict[str, Any]] = {}
    for unit in units:
        for category in AUDIT_CATEGORIES:
            for value in unit["stratum_values"][category]:
                stratum_id = f"{category}:{value}"
                row = strata.setdefault(
                    stratum_id,
                    {"stratum_id": stratum_id, "category": category, "value": value, "unit_ids": []},
                )
                row["unit_ids"].append(unit["audit_unit_id"])

    units_by_id = {row["audit_unit_id"]: row for row in units}
    selected: dict[str, set[str]] = {row["audit_unit_id"]: set() for row in units}
    reports: list[dict[str, Any]] = []
    for stratum_id, stratum in sorted(strata.items()):
        population = sorted(set(stratum["unit_ids"]))
        mandatory = sorted(row for row in population if units_by_id[row]["mandatory_census"])
        available = [row for row in population if row not in set(mandatory)]
        rule = rules[stratum["category"]]
        requested = max(
            rule["minimum"],
            math.ceil(len(population) * rule["rate_basis_points"] / 10_000),
        )
        requested = min(rule["maximum"], requested)
        sample_count = min(len(available), requested)
        sample = sorted(
            available,
            key=lambda unit_id: (
                content_sha256(
                    {"seed": plan["sampling_seed"], "stratum_id": stratum_id, "unit_id": unit_id}
                ),
                unit_id,
            ),
        )[:sample_count]
        for unit_id in mandatory:
            selected[unit_id].add(f"census:{stratum_id}")
        for unit_id in sample:
            selected[unit_id].add(f"sample:{stratum_id}")
        reports.append(
            {
                "stratum_id": stratum_id,
                "category": stratum["category"],
                "value": stratum["value"],
                "risk_level": rule["risk_level"],
                "population_unit_ids": population,
                "population_denominator": len(population),
                "mandatory_census_unit_ids": mandatory,
                "mandatory_census_count": len(mandatory),
                "planned_sample_unit_ids": sample,
                "planned_sample_count": len(sample),
                "planned_audit_count": len(set(mandatory) | set(sample)),
                "acceptance_threshold": rule["acceptance_threshold"],
                "escalation_mode": rule["escalation_mode"],
                "deterministic_sampling_rule": (
                    f"risk={rule['risk_level']}; max(minimum={rule['minimum']}, "
                    f"ceil(N*{rule['rate_basis_points']}/10000)); maximum={rule['maximum']}; "
                    "SHA-256(seed,stratum,unit) order"
                ),
            }
        )

    assignments: list[dict[str, Any]] = []
    for unit in units:
        selections = sorted(selected[unit["audit_unit_id"]])
        status = "selected_for_audit" if selections else "unaudited"
        body = {
            "audit_unit_id": unit["audit_unit_id"],
            "subject_id": unit["subject_id"],
            "candidate_id": unit["candidate_id"],
            "selection_status": status,
            "selection_strata": selections,
            "mandatory_census": unit["mandatory_census"],
            "reason": (
                "mandatory census or deterministic risk/size sample"
                if selections
                else "explicitly unaudited under the frozen risk/size plan"
            ),
        }
        assignments.append({"assignment_id": _stable_id("AUDIT-ASSIGN", body), **body})

    deep_candidates = sorted(str(row["candidate_id"]) for row in deep_frame["deep_packages"])
    decision_outputs = [
        {
            "candidate_id": str(row["candidate_id"]),
            **{name: copy.deepcopy(row["separate_decision_outputs"][name]) for name in DECISION_OUTPUT_NAMES},
        }
        for row in sorted(deep_frame["deep_packages"], key=lambda item: item["candidate_id"])
    ]
    projection = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "case_revision_id": case.case_revision_id,
        "audit_revision": plan["audit_revision"],
        "deep_frame_aggregate_id": deep_frame["aggregate_id"],
        "deep_candidate_ids": deep_candidates,
        "deep_candidate_decision_set": decision_outputs,
        "ranking_preparation": copy.deepcopy(deep_frame["ranking_preparation"]),
        "portfolio_policy": plan["portfolio_policy"],
        "diversity_features": _diversity_features(deep_frame, plan),
        "audit_units": units,
        "audit_assignments": sorted(assignments, key=lambda row: row["assignment_id"]),
        "audit_stratum_reports": reports,
        "sampling_rules": plan["sampling_rules"],
        "subject_author_ids": plan["subject_author_ids"],
        "supersedes_portfolio_aggregate_id": plan["supersedes_portfolio_aggregate_id"],
        "input_receipts": {
            "case_source_input_sha256": case.source_input_sha256,
            "deep_frame_sha256": content_sha256(deep_frame),
            "plan_projection_sha256": content_sha256(plan),
        },
        "frozen_before_audit_outcomes": True,
    }
    plan_id = _stable_id(
        "PORTFOLIO-PLAN",
        {
            "rule": PORTFOLIO_PLAN_ID_RULE,
            "case_revision_id": case.case_revision_id,
            "audit_revision": plan["audit_revision"],
            "plan_version": plan["plan_version"],
        },
    )
    return {"portfolio_plan_id": plan_id, **projection}


def preview_audit_freeze(
    case_revision: CaseRevision | Mapping[str, Any],
    deep_frame: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the deterministic outcome-free artifact used before audit execution."""

    case = _coerce_case(case_revision)
    validate_screen_deep_aggregate(case, deep_frame)
    normalized_deep = _plain(deep_frame)
    if not normalized_deep.get("stage_gate_passed"):
        raise PortfolioAggregateError("portfolio input requires a passing Stage 5-6 aggregate")
    normalized_plan = _normalize_plan(plan, normalized_deep)
    _reject_benchmark_labels(normalized_plan, "audit_plan")
    return _build_freeze(case, normalized_deep, normalized_plan)


def make_frozen_audit_search(
    *,
    source_id: str,
    source_release: str,
    query: str,
    native_record_id: str,
    locator: str,
    payload: str,
    support_text: str,
) -> dict[str, Any]:
    """Build a retained retrieval object whose support span can be reverified."""

    body = _text(payload, "audit search payload")
    support = _text(support_text, "audit search support_text")
    start = body.find(support)
    if start < 0:
        raise PortfolioAggregateError("audit support_text is absent from retained payload")
    row = {
        "source_id": _text(source_id, "audit search source_id"),
        "source_release": _text(source_release, "audit search source_release"),
        "query": _text(query, "audit search query"),
        "native_record_id": _text(native_record_id, "audit search native_record_id"),
        "locator": _text(locator, "audit search locator"),
        "payload": body,
        "payload_sha256": content_sha256(body),
        "support_start": start,
        "support_end": start + len(support),
        "support_text": support,
    }
    row["search_id"] = _stable_id("AUDIT-SEARCH", row)
    return row


def _normalize_search(value: Any) -> dict[str, Any]:
    expected = {
        "search_id",
        "source_id",
        "source_release",
        "query",
        "native_record_id",
        "locator",
        "payload",
        "payload_sha256",
        "support_start",
        "support_end",
        "support_text",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PortfolioAggregateError("independent audit search has an invalid field set")
    payload = _text(value["payload"], "audit search payload")
    start, end = value["support_start"], value["support_end"]
    if any(isinstance(row, bool) or not isinstance(row, int) for row in (start, end)):
        raise PortfolioAggregateError("audit search support coordinates must be integers")
    support = _text(value["support_text"], "audit search support_text")
    if start < 0 or end <= start or payload[start:end] != support:
        raise PortfolioAggregateError("audit search support span does not match retained payload")
    if value["payload_sha256"] != content_sha256(payload):
        raise PortfolioAggregateError("audit search retained payload hash mismatch")
    body = {
        "source_id": _text(value["source_id"], "audit search source_id"),
        "source_release": _text(value["source_release"], "audit search source_release"),
        "query": _text(value["query"], "audit search query"),
        "native_record_id": _text(value["native_record_id"], "audit search native_record_id"),
        "locator": _text(value["locator"], "audit search locator"),
        "payload": payload,
        "payload_sha256": value["payload_sha256"],
        "support_start": start,
        "support_end": end,
        "support_text": support,
    }
    expected_id = _stable_id("AUDIT-SEARCH", body)
    if value["search_id"] != expected_id:
        raise PortfolioAggregateError("audit search content-derived ID mismatch")
    return {"search_id": expected_id, **body}


_ALLOWED_EFFECTS = {
    "support": {"no_change"},
    "qualify": {"qualified", "reranked"},
    "contradict": {"reranked", "blocked_unresolved", "quarantined", "rejected"},
    "unresolved": {"blocked_unresolved", "quarantined"},
    "correct": {"reranked"},
    "supersede": {"reranked"},
    "quarantine": {"quarantined"},
    "reject": {"rejected"},
}


def _normalize_outcomes(value: Any, freeze: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise PortfolioAggregateError("audit_outcomes must be a list")
    assignments = {row["assignment_id"]: row for row in freeze["audit_assignments"]}
    units = {row["audit_unit_id"]: row for row in freeze["audit_units"]}
    authors = freeze["subject_author_ids"]
    expected = {
        "assignment_id",
        "outcome",
        "decision_effect",
        "auditor_id",
        "independent_searches",
        "rationale",
        "ranking_revision_id",
    }
    result: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise PortfolioAggregateError("audit outcome has an invalid field set")
        assignment_id = _text(raw["assignment_id"], "audit assignment_id")
        if assignment_id not in assignments or assignment_id in result:
            raise PortfolioAggregateError("audit outcome assignment is unknown or duplicated")
        assignment = assignments[assignment_id]
        unit = units[assignment["audit_unit_id"]]
        outcome = _text(raw["outcome"], "audit outcome")
        effect = _text(raw["decision_effect"], "audit decision effect")
        if outcome not in _ALLOWED_EFFECTS or effect not in _ALLOWED_EFFECTS[outcome]:
            raise PortfolioAggregateError("audit outcome and decision effect are inconsistent")
        auditor = _text(raw["auditor_id"], "auditor_id")
        subject_authors = set(authors.get(unit["subject_id"], []))
        if not subject_authors:
            raise PortfolioAggregateError("audited subject lacks frozen author identity")
        if auditor in subject_authors:
            raise PortfolioAggregateError("the author of a subject cannot self-approve its audit")
        searches = sorted(
            (_normalize_search(row) for row in raw["independent_searches"]),
            key=lambda row: row["search_id"],
        )
        if not searches or len({row["search_id"] for row in searches}) != len(searches):
            raise PortfolioAggregateError("every audit outcome needs unique retrieval-backed independent search")
        revision = raw["ranking_revision_id"]
        revision = _text(revision, "ranking revision ID") if revision is not None else None
        if (effect == "reranked") != bool(revision):
            raise PortfolioAggregateError("reranked audit effects require exactly one ranking revision ID")
        body = {
            "assignment_id": assignment_id,
            "audit_unit_id": assignment["audit_unit_id"],
            "subject_id": unit["subject_id"],
            "candidate_id": unit["candidate_id"],
            "outcome": outcome,
            "decision_effect": effect,
            "auditor_id": auditor,
            "subject_author_ids": sorted(subject_authors),
            "independent_searches": searches,
            "independent_search_receipt_ids": [row["search_id"] for row in searches],
            "checked_source_ids": sorted({row["source_id"] for row in searches}),
            "checked_evidence_span_ids": [
                _stable_id(
                    "AUDIT-SPAN",
                    {
                        "search_id": row["search_id"],
                        "start": row["support_start"],
                        "end": row["support_end"],
                        "text": row["support_text"],
                    },
                )
                for row in searches
            ],
            "rationale": _text(raw["rationale"], "audit rationale"),
            "ranking_revision_id": revision,
        }
        result[assignment_id] = {
            "audit_record_id": _stable_id("RETRIEVAL-AUDIT", body), **body
        }
    return result


def _failure_outcome(row: Mapping[str, Any]) -> bool:
    return row["outcome"] in {"contradict", "unresolved", "quarantine", "reject"} or row[
        "decision_effect"
    ] in {"blocked_unresolved", "quarantined", "rejected"}


def _escalation(
    freeze: Mapping[str, Any], outcomes: Mapping[str, Mapping[str, Any]]
) -> tuple[set[str], list[dict[str, Any]], set[str]]:
    assignments = {row["assignment_id"]: row for row in freeze["audit_assignments"]}
    assignment_by_unit = {row["audit_unit_id"]: row for row in freeze["audit_assignments"]}
    failed_units = {
        assignments[assignment_id]["audit_unit_id"]
        for assignment_id, row in outcomes.items()
        if assignments[assignment_id]["selection_status"] == "selected_for_audit"
        and _failure_outcome(row)
    }
    allowed_escalated: set[str] = set()
    quarantined_units: set[str] = set()
    reports: list[dict[str, Any]] = []
    for report in freeze["audit_stratum_reports"]:
        failed = sorted(set(report["population_unit_ids"]) & failed_units)
        triggered = len(failed) > report["acceptance_threshold"]
        unaudited = sorted(
            unit_id
            for unit_id in report["population_unit_ids"]
            if assignment_by_unit[unit_id]["assignment_id"] not in outcomes
        )
        escalated_ids: list[str] = []
        quarantined_ids: list[str] = []
        if triggered:
            if report["escalation_mode"] == "census_affected_stratum":
                escalated_ids = [assignment_by_unit[row]["assignment_id"] for row in unaudited]
                allowed_escalated.update(escalated_ids)
            else:
                quarantined_ids = unaudited
                quarantined_units.update(unaudited)
        reports.append(
            {
                **copy.deepcopy(report),
                "decision_changing_failure_unit_ids": failed,
                "escalation_triggered": triggered,
                "escalated_assignment_ids": sorted(escalated_ids),
                "quarantined_unaudited_unit_ids": sorted(quarantined_ids),
            }
        )
    return allowed_escalated, reports, quarantined_units


def _normalize_corrections(
    value: Any,
    deep_frame: Mapping[str, Any],
    freeze: Mapping[str, Any],
    outcomes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not isinstance(value, list):
        raise PortfolioAggregateError("corrections must be a list")
    expected = {
        "assignment_id",
        "candidate_id",
        "authority_field",
        "target_record_id",
        "action",
        "prior_value_sha256",
        "replacement_record_id",
        "replacement_value",
        "parent_correction_id",
        "rationale",
    }
    deep_by_candidate = {str(row["candidate_id"]): copy.deepcopy(row) for row in deep_frame["deep_packages"]}
    indexes = {candidate_id: _record_index(row) for candidate_id, row in deep_by_candidate.items()}
    current = copy.deepcopy(deep_by_candidate)
    current_indexes = copy.deepcopy(indexes)
    rows: list[dict[str, Any]] = []
    by_input_ref: dict[str, str] = {}
    pending = list(value)
    while pending:
        progressed = False
        for raw in list(pending):
            if not isinstance(raw, Mapping) or set(raw) != expected:
                raise PortfolioAggregateError("audit correction has an invalid field set")
            parent_ref = raw["parent_correction_id"]
            if parent_ref is not None and parent_ref not in by_input_ref:
                continue
            assignment_id = _text(raw["assignment_id"], "correction assignment_id")
            outcome = outcomes.get(assignment_id)
            if outcome is None:
                raise PortfolioAggregateError("correction lacks a completed audit outcome")
            candidate_id = _text(raw["candidate_id"], "correction candidate_id")
            if candidate_id not in current or outcome["candidate_id"] != candidate_id:
                raise PortfolioAggregateError("correction candidate differs from its audit assignment")
            authority = _text(raw["authority_field"], "correction authority field")
            action = _text(raw["action"], "correction action")
            if authority not in CORRECTION_FIELDS or action not in CORRECTION_ACTIONS:
                raise PortfolioAggregateError("correction authority or action is invalid")
            target_id = _text(raw["target_record_id"], "correction target_record_id")
            target = current_indexes[candidate_id].get(target_id)
            if target is None or target[0] not in _CORRECTION_ID_FIELDS[authority]:
                raise PortfolioAggregateError("correction target is absent or outside its authority field")
            if raw["prior_value_sha256"] != content_sha256(target[1]):
                raise PortfolioAggregateError("correction prior value hash does not match retained history")
            replacement_id = raw["replacement_record_id"]
            replacement_value = raw["replacement_value"]
            if action in {"correct", "supersede"}:
                replacement_id = _text(replacement_id, "replacement_record_id")
                if replacement_id == target_id or not isinstance(replacement_value, Mapping):
                    raise PortfolioAggregateError("correction requires a distinct replacement record")
                replacement_primary = _primary_id(replacement_value)
                if replacement_primary is None or replacement_primary != (target[0], replacement_id):
                    raise PortfolioAggregateError("replacement identity field does not match its target kind")
            elif replacement_id is not None or replacement_value is not None:
                raise PortfolioAggregateError("quarantine/reject cannot carry a hidden replacement")
            parent_id = by_input_ref.get(parent_ref) if parent_ref is not None else None
            body = {
                "assignment_id": assignment_id,
                "candidate_id": candidate_id,
                "authority_field": authority,
                "target_record_id": target_id,
                "action": action,
                "prior_value": target[1],
                "prior_value_sha256": content_sha256(target[1]),
                "replacement_record_id": replacement_id,
                "replacement_value": _plain(replacement_value) if replacement_value is not None else None,
                "replacement_value_sha256": content_sha256(replacement_value) if replacement_value is not None else None,
                "parent_correction_id": parent_id,
                "provenance_search_receipt_ids": outcome["independent_search_receipt_ids"],
                "provenance_evidence_span_ids": outcome["checked_evidence_span_ids"],
                "rationale": _text(raw["rationale"], "correction rationale"),
            }
            correction_id = _stable_id("AUDIT-CORRECTION", body)
            if action in {"correct", "supersede"}:
                revised, count = _replace_record(
                    current[candidate_id], target_id, target[1], replacement_value
                )
                if count != 1:
                    raise PortfolioAggregateError("correction did not replace exactly one current record")
                current[candidate_id] = revised
                current_indexes[candidate_id] = _record_index(revised)
            reference = str(raw.get("replacement_record_id") or target_id) + "|" + assignment_id
            by_input_ref[reference] = correction_id
            by_input_ref[correction_id] = correction_id
            rows.append({"correction_id": correction_id, **body})
            pending.remove(raw)
            progressed = True
        if not progressed:
            raise PortfolioAggregateError("correction chain is cyclic or references an unknown parent")

    correction_by_assignment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        correction_by_assignment.setdefault(row["assignment_id"], []).append(row)
    for assignment_id, outcome in outcomes.items():
        actions = {row["action"] for row in correction_by_assignment.get(assignment_id, [])}
        required = {
            "correct": "correct",
            "supersede": "supersede",
            "quarantine": "quarantine",
            "reject": "reject",
        }.get(outcome["outcome"])
        if required is not None and required not in actions:
            raise PortfolioAggregateError("correction-bearing audit outcome lacks its append-only action")
        if required is None and actions:
            raise PortfolioAggregateError("non-correction audit outcome cannot carry a correction")
    return sorted(rows, key=lambda row: row["correction_id"]), current


def _normalize_council(
    value: Any,
    freeze: Mapping[str, Any],
    outcomes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, CouncilRecord]]:
    if not isinstance(value, list):
        raise PortfolioAggregateError("council_reviews must be a list")
    expected = {"candidate_id", "disposition", "rationale", "issues"}
    issue_expected = {
        "issue_kind",
        "decision_impact",
        "subject_ids",
        "finding",
        "reviewer_id",
        "evidence_ancestry_cluster_ids",
        "rationale",
    }
    units = {row["subject_id"]: row for row in freeze["audit_units"]}
    authors = freeze["subject_author_ids"]
    auditors_by_candidate: dict[str, set[str]] = {}
    for row in outcomes.values():
        if row["candidate_id"]:
            auditors_by_candidate.setdefault(row["candidate_id"], set()).add(row["auditor_id"])
    records: list[dict[str, Any]] = []
    typed: dict[str, CouncilRecord] = {}
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise PortfolioAggregateError("council review has an invalid field set")
        candidate_id = _text(raw["candidate_id"], "council candidate_id")
        if candidate_id in typed:
            raise PortfolioAggregateError("candidate has duplicate council records")
        try:
            disposition = CouncilDisposition(raw["disposition"])
        except ValueError as exc:
            raise PortfolioAggregateError("council disposition is invalid") from exc
        if disposition is CouncilDisposition.BENCHMARK_ONLY:
            raise PortfolioAggregateError("benchmark-only lanes cannot enter a live portfolio")
        issues: list[dict[str, Any]] = []
        findings: list[tuple[Any, Any]] = []
        ancestry: set[str] = set()
        issue_ids: list[str] = []
        assessment_ids: list[str] = []
        for issue in raw["issues"]:
            if not isinstance(issue, Mapping) or set(issue) != issue_expected:
                raise PortfolioAggregateError("council issue has an invalid field set")
            subjects = _strings(issue["subject_ids"], "council subject ID", required=True)
            if any(subject not in units or units[subject]["candidate_id"] != candidate_id for subject in subjects):
                raise PortfolioAggregateError("council issue subject is outside its candidate")
            reviewer = _text(issue["reviewer_id"], "council reviewer_id")
            excluded = set(auditors_by_candidate.get(candidate_id, set()))
            for subject in subjects:
                excluded.update(authors.get(subject, []))
            if reviewer in excluded:
                raise PortfolioAggregateError("council reviewer is not independent of authors/auditors")
            finding = _text(issue["finding"], "council finding")
            if finding not in {
                "confirmed", "qualified", "correction_required", "contradicted",
                "unresolved", "quarantine", "reject",
            }:
                raise PortfolioAggregateError("council finding is invalid")
            clusters = _strings(
                issue["evidence_ancestry_cluster_ids"],
                "council evidence ancestry cluster ID",
                required=True,
            )
            body = {
                "candidate_id": candidate_id,
                "issue_kind": _text(issue["issue_kind"], "council issue kind"),
                "decision_impact": _text(issue["decision_impact"], "council decision impact"),
                "subject_ids": subjects,
                "finding": finding,
                "reviewer_id": reviewer,
                "evidence_ancestry_cluster_ids": clusters,
                "rationale": _text(issue["rationale"], "council issue rationale"),
            }
            issue_id = _stable_id("COUNCIL-ISSUE", {key: body[key] for key in ("candidate_id", "issue_kind", "decision_impact", "subject_ids", "rationale")})
            assessment_id = _stable_id("COUNCIL-ASSESSMENT", {"issue_id": issue_id, **body})
            issue_ids.append(issue_id)
            assessment_ids.append(assessment_id)
            ancestry.update(clusters)
            findings.append((body["issue_kind"], finding))
            issues.append({"issue_id": issue_id, "assessment_id": assessment_id, **body})
        if not issues:
            raise PortfolioAggregateError("council record requires at least one typed decision issue")
        finding_set = {row[1] for row in findings}
        if "reject" in finding_set and disposition is not CouncilDisposition.REJECTED:
            raise PortfolioAggregateError("council reject finding requires rejected disposition")
        if "reject" not in finding_set and "quarantine" in finding_set and disposition is not CouncilDisposition.QUARANTINED:
            raise PortfolioAggregateError("council quarantine finding requires quarantined disposition")
        if finding_set & {"unresolved", "contradicted"} and disposition not in {
            CouncilDisposition.CONFLICT_UNRESOLVED,
            CouncilDisposition.QUARANTINED,
            CouncilDisposition.REJECTED,
        }:
            raise PortfolioAggregateError("unresolved council conflict cannot be silently retained")
        body = {
            "candidate_id": candidate_id,
            "issue_ids": sorted(issue_ids),
            "assessment_ids": sorted(assessment_ids),
            "typed_findings": sorted([list(row) for row in findings]),
            "independent_evidence_cluster_ids": sorted(ancestry),
            "correction_ids": [],
            "disposition": disposition.value,
            "rationale": _text(raw["rationale"], "council rationale"),
        }
        record_id = _stable_id("COUNCIL", body)
        records.append({"council_record_id": record_id, "issues": issues, **body})
        typed[candidate_id] = CouncilRecord(
            council_record_id=record_id,
            candidate_id=candidate_id,
            issue_ids=tuple(body["issue_ids"]),
            assessment_ids=tuple(body["assessment_ids"]),
            typed_findings=tuple(),
            independent_evidence_cluster_ids=tuple(body["independent_evidence_cluster_ids"]),
            correction_ids=tuple(),
            disposition=disposition,
            rationale=body["rationale"],
        )
    return sorted(records, key=lambda row: row["council_record_id"]), typed


def _decode_preparation(value: Mapping[str, Any]) -> RankingPreparationRecord:
    try:
        return RankingPreparationRecord(
            preparation_id=str(value["preparation_id"]),
            candidate_id=str(value["candidate_id"]),
            profile_id=str(value["profile_id"]),
            primary_endpoint_id=str(value["primary_endpoint_id"]),
            triage_category=TriageCategory(value["triage_category"]),
            therapeutic_confidence_tier=TherapeuticConfidenceTier(value["therapeutic_confidence_tier"]),
            therapeutic_rank_within_tier=int(value["therapeutic_rank_within_tier"]),
            research_priority_tier=ResearchPriorityTier(value["research_priority_tier"]),
            research_rank_within_tier=int(value["research_rank_within_tier"]),
            therapeutic_ordering_bands=tuple(value["therapeutic_ordering_bands"]),
            research_ordering_bands=tuple(value["research_ordering_bands"]),
            deterministic_tie_breaker=str(value["deterministic_tie_breaker"]),
            ordering_rule_version=str(value["ordering_rule_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PortfolioAggregateError(f"revised ranking preparation is invalid: {exc}") from exc


def _candidate_audit_state(
    candidate_id: str,
    freeze: Mapping[str, Any],
    outcomes: Mapping[str, Mapping[str, Any]],
    corrections: list[dict[str, Any]],
) -> tuple[AuditAssignment, AuditRecord | None]:
    units = {row["audit_unit_id"]: row for row in freeze["audit_units"]}
    assignments = [
        row for row in freeze["audit_assignments"]
        if units[row["audit_unit_id"]]["candidate_id"] == candidate_id
        and row["selection_status"] == "selected_for_audit"
    ]
    missing = [row for row in assignments if row["assignment_id"] not in outcomes]
    body = {
        "candidate_id": candidate_id,
        "selected_assignment_ids": sorted(row["assignment_id"] for row in assignments),
        "missing_assignment_ids": sorted(row["assignment_id"] for row in missing),
    }
    assignment = AuditAssignment(
        assignment_id=_stable_id("CANDIDATE-AUDIT-ASSIGN", body),
        policy_id=freeze["portfolio_plan_id"],
        candidate_id=candidate_id,
        selection_status=AuditSelectionStatus.SELECTED,
        strata=(AuditStratum.FINALIST_CENSUS,),
        sample_key=content_sha256(body),
        reason="all decision-capable deep subjects are assigned by frozen census",
    )
    if missing:
        return assignment, None
    candidate_outcomes = [outcomes[row["assignment_id"]] for row in assignments]
    effects = {row["decision_effect"] for row in candidate_outcomes}
    if "rejected" in effects:
        outcome, effect = AuditOutcome.REJECT, AuditDecisionEffect.REJECTED
    elif effects & {"quarantined", "blocked_unresolved"}:
        outcome = AuditOutcome.QUARANTINE if "quarantined" in effects else AuditOutcome.UNRESOLVED
        effect = AuditDecisionEffect.QUARANTINED if "quarantined" in effects else AuditDecisionEffect.BLOCKED_UNRESOLVED
    elif "reranked" in effects:
        outcome, effect = AuditOutcome.CORRECT, AuditDecisionEffect.RERANKED
    elif "qualified" in effects:
        outcome, effect = AuditOutcome.QUALIFY, AuditDecisionEffect.QUALIFIED
    else:
        outcome, effect = AuditOutcome.SUPPORT, AuditDecisionEffect.NO_CHANGE
    ranking_revisions = sorted(
        {row["ranking_revision_id"] for row in candidate_outcomes if row["ranking_revision_id"]}
    )
    revision = _stable_id("RANKING-REVISION", ranking_revisions) if effect is AuditDecisionEffect.RERANKED else None
    candidate_corrections = [row for row in corrections if row["candidate_id"] == candidate_id]
    record_body = {
        "assignment_id": assignment.assignment_id,
        "candidate_id": candidate_id,
        "audited_subject_ids": sorted(row["subject_id"] for row in candidate_outcomes),
        "outcome": outcome.value,
        "decision_effect": effect.value,
        "correction_ids": sorted(row["correction_id"] for row in candidate_corrections),
        "checked_source_ids": sorted({value for row in candidate_outcomes for value in row["checked_source_ids"]}),
        "checked_evidence_span_ids": sorted({value for row in candidate_outcomes for value in row["checked_evidence_span_ids"]}),
        "independent_search_receipt_ids": sorted({value for row in candidate_outcomes for value in row["independent_search_receipt_ids"]}),
        "claim_author_ids": sorted({value for row in candidate_outcomes for value in row["subject_author_ids"]}),
        "auditor_id": "deterministic-candidate-audit-reducer",
        "rationale": "Reduced all frozen candidate audit obligations without counting reviewers as evidence.",
        "ranking_revision_id": revision,
    }
    return assignment, AuditRecord(
        audit_record_id=_stable_id("CANDIDATE-AUDIT", record_body),
        assignment_id=assignment.assignment_id,
        candidate_id=candidate_id,
        audited_subject_ids=tuple(record_body["audited_subject_ids"]),
        outcome=outcome,
        decision_effect=effect,
        correction_ids=tuple(record_body["correction_ids"]),
        checked_source_ids=tuple(record_body["checked_source_ids"]),
        checked_evidence_span_ids=tuple(record_body["checked_evidence_span_ids"]),
        independent_search_receipt_ids=tuple(record_body["independent_search_receipt_ids"]),
        claim_author_ids=tuple(record_body["claim_author_ids"]),
        auditor_id=record_body["auditor_id"],
        rationale=record_body["rationale"],
        ranking_revision_id=revision,
    )


def _construct_aggregate(
    case: CaseRevision,
    deep_frame: Mapping[str, Any],
    frozen_bundle: Mapping[str, Any],
    freeze: Mapping[str, Any],
) -> dict[str, Any]:
    outcomes = _normalize_outcomes(frozen_bundle["audit_outcomes"], freeze)
    escalated, stratum_reports, quarantined_units = _escalation(freeze, outcomes)
    assignments = {row["assignment_id"]: row for row in freeze["audit_assignments"]}
    allowed = {
        assignment_id
        for assignment_id, row in assignments.items()
        if row["selection_status"] == "selected_for_audit"
    } | escalated
    unexpected = set(outcomes) - allowed
    if unexpected:
        raise PortfolioAggregateError("audit outcome was supplied for an explicitly unaudited unit")
    quarantine_resolved_assignments = {
        assignments_by_unit["assignment_id"]
        for unit_id in quarantined_units
        for assignments_by_unit in (next(
            row for row in freeze["audit_assignments"] if row["audit_unit_id"] == unit_id
        ),)
    }
    required = ({
        assignment_id
        for assignment_id, row in assignments.items()
        if row["selection_status"] == "selected_for_audit"
    } | escalated) - quarantine_resolved_assignments
    missing = sorted(required - set(outcomes))

    corrections, revised = _normalize_corrections(
        frozen_bundle["corrections"], deep_frame, freeze, outcomes
    )
    council_records, council_typed = _normalize_council(
        frozen_bundle["council_reviews"], freeze, outcomes
    )
    package_revisions: list[dict[str, Any]] = []
    for candidate_id, current in sorted(revised.items()):
        original = next(row for row in deep_frame["deep_packages"] if row["candidate_id"] == candidate_id)
        candidate_corrections = [row for row in corrections if row["candidate_id"] == candidate_id]
        body = {
            "candidate_id": candidate_id,
            "base_package_id": original["package"]["package_id"],
            "base_deep_record_sha256": content_sha256(original),
            "correction_ids": [row["correction_id"] for row in candidate_corrections],
            "current_deep_record": current,
            "current_deep_record_sha256": content_sha256(current),
        }
        package_revisions.append({"package_revision_id": _stable_id("PACKAGE-REVISION", body), **body})

    plan_features = {row["candidate_id"]: row for row in freeze["diversity_features"]}
    portfolio_frames: list[PortfolioCandidateFrame] = []
    candidate_audit_records: list[dict[str, Any]] = []
    for revision in package_revisions:
        candidate_id = revision["candidate_id"]
        deep = revision["current_deep_record"]
        preparation = _decode_preparation(deep["ranking_preparation"])
        feature = plan_features[candidate_id]
        scaffold_row = feature["scaffold"]
        scaffold = make_scaffold_descriptor(
            scaffold_key=scaffold_row["scaffold_key"],
            method=scaffold_row["method"],
            version=scaffold_row["version"],
            identity_record_ids=scaffold_row["identity_record_ids"],
        )
        diversity = make_diversity_features(
            candidate_id=candidate_id,
            target_ids=feature["target_ids"],
            mechanism_ids=feature["mechanism_ids"],
            causal_route_ids=feature["causal_route_ids"],
            scaffold=scaffold,
            evidence_modalities=feature["evidence_modalities"],
            endpoint_ids=feature["endpoint_ids"],
            development_statuses=feature["development_statuses"],
            uncertainty_bands=feature["uncertainty_bands"],
        )
        audit_assignment, audit_record = _candidate_audit_state(
            candidate_id, freeze, outcomes, corrections
        )
        if audit_record is not None:
            candidate_audit_records.append(_plain(audit_record))
        portfolio_frames.append(
            PortfolioCandidateFrame(
                candidate_id=candidate_id,
                preparation=preparation,
                diversity=diversity,
                audit_assignment=audit_assignment,
                audit_record=audit_record,
                council_record=council_typed.get(candidate_id),
                ranking_revision_id=audit_record.ranking_revision_id if audit_record else None,
            )
        )

    policy_row = freeze["portfolio_policy"]
    policy = make_portfolio_policy(
        finalist_capacity=policy_row["finalist_capacity"],
        reserve_capacity=policy_row["reserve_capacity"],
        evidence_weight=policy_row["evidence_weight"],
        information_weight=policy_row["information_weight"],
        diversity_weight=policy_row["diversity_weight"],
        diversity_dimension_weights={
            DiversityDimension(row[0]): row[1] for row in policy_row["diversity_dimension_weights"]
        },
        allowed_therapeutic_tiers=(TherapeuticConfidenceTier(row) for row in policy_row["allowed_therapeutic_tiers"]),
    )
    selection = select_diversified_portfolio(portfolio_frames, policy)

    rank_rows = [_plain(row) for row in selection.records]
    if missing:
        selection_status = "needs_additional_audit"
        finalist_ids: list[str] = []
        reserve_ids: list[str] = []
    else:
        selection_status = selection.status.value
        finalist_ids = list(selection.finalist_ids)
        reserve_ids = list(selection.reserve_ids)

    quarantined_candidates = {
        row["candidate_id"]
        for row in freeze["audit_units"]
        if row["audit_unit_id"] in quarantined_units and row["candidate_id"]
    }
    if quarantined_candidates:
        finalist_ids = [row for row in finalist_ids if row not in quarantined_candidates]
        reserve_ids = [row for row in reserve_ids if row not in quarantined_candidates]

    portfolio_dispositions: list[dict[str, Any]] = []
    for row in rank_rows:
        candidate_id = row["candidate_id"]
        if candidate_id in quarantined_candidates:
            row["disposition"] = "audit_quarantined"
            row["reason"] = "audit escalation quarantined an unaudited affected-stratum record"
        if missing:
            row["disposition"] = (
                "unaudited"
                if candidate_id in selection.additional_audit_required_ids
                else "selection_pending_additional_audit"
            )
            row["reason"] = "portfolio selection is pending complete frozen audit coverage"
        portfolio_dispositions.append(
            {
                "candidate_id": candidate_id,
                "disposition": row["disposition"],
                "reason": row["reason"],
            }
        )

    by_candidate_deep = {row["candidate_id"]: row["current_deep_record"] for row in package_revisions}
    seven_outputs: list[dict[str, Any]] = []
    rank_by_candidate = {row["candidate_id"]: row for row in rank_rows}
    for candidate_id in sorted(by_candidate_deep):
        base = by_candidate_deep[candidate_id]["separate_decision_outputs"]
        rank = rank_by_candidate[candidate_id]
        outputs = {name: copy.deepcopy(base[name]) for name in DECISION_OUTPUT_NAMES}
        outputs["portfolio_diversity"] = {
            "status": "derived_portfolio_property",
            "marginal_contributions": rank["diversity_contributions"],
            "diversity_component": rank["diversity_component"],
            "policy_id": policy.policy_id,
        }
        seven_outputs.append(
            {
                "candidate_id": candidate_id,
                **outputs,
                "therapeutic_support_sha256_before_portfolio": content_sha256(base["therapeutic_support"]),
                "therapeutic_support_sha256_after_portfolio": content_sha256(outputs["therapeutic_support"]),
                "evidence_quality_sha256_before_portfolio": content_sha256(base["evidence_quality"]),
                "evidence_quality_sha256_after_portfolio": content_sha256(outputs["evidence_quality"]),
                "novelty_or_diversity_modified_therapeutic_support": False,
            }
        )

    evidence_ranking = sorted(rank_rows, key=lambda row: (row["evidence_strength_rank"], row["candidate_id"]))
    novelty_ranking = sorted(rank_rows, key=lambda row: (row["novelty_information_value_rank"], row["candidate_id"]))
    diversified_ranking = sorted(
        rank_rows,
        key=lambda row: (
            row["diversified_portfolio_rank"] is None,
            row["diversified_portfolio_rank"] or 10**9,
            row["candidate_id"],
        ),
    )
    canonical_order = [row["candidate_id"] for row in diversified_ranking]

    audit_records = sorted(outcomes.values(), key=lambda row: row["audit_record_id"])
    achieved_ids = set(outcomes)
    audit_report_rows: list[dict[str, Any]] = []
    for report in stratum_reports:
        population_assignments = {
            next(row["assignment_id"] for row in freeze["audit_assignments"] if row["audit_unit_id"] == unit_id)
            for unit_id in report["population_unit_ids"]
        }
        achieved = sorted(population_assignments & achieved_ids)
        outcomes_count: dict[str, int] = {}
        for assignment_id in achieved:
            outcome = outcomes[assignment_id]["outcome"]
            outcomes_count[outcome] = outcomes_count.get(outcome, 0) + 1
        audit_report_rows.append(
            {
                **report,
                "achieved_assignment_ids": achieved,
                "achieved_audit_count": len(achieved),
                "audited_outcomes": outcomes_count,
                "unresolved_gap_assignment_ids": sorted(
                    set(report["escalated_assignment_ids"]) - achieved_ids
                ),
            }
        )

    dispositions = {row["disposition"] for row in portfolio_dispositions}
    n_deep = len(portfolio_dispositions)
    counts = {
        "N_deep": n_deep,
        "N_finalist": sum(row["disposition"] == "finalist" for row in portfolio_dispositions),
        "N_reserve": sum(row["disposition"] == "reserve" for row in portfolio_dispositions),
        "N_not_selected": sum(row["disposition"] == "not_selected" for row in portfolio_dispositions),
        "N_audit_rejected": sum(row["disposition"] == "audit_rejected" for row in portfolio_dispositions),
        "N_audit_quarantined": sum(row["disposition"] == "audit_quarantined" for row in portfolio_dispositions),
        "N_interim": sum(
            row["disposition"] in {
                "unaudited", "selection_pending_additional_audit", "council_blocked"
            }
            for row in portfolio_dispositions
        ),
    }
    counts["portfolio_equation_balanced"] = counts["N_deep"] == sum(
        counts[key]
        for key in (
            "N_finalist", "N_reserve", "N_not_selected", "N_audit_rejected", "N_audit_quarantined"
        )
    )
    coverage_complete = not missing and all(
        not row["unresolved_gap_assignment_ids"] for row in audit_report_rows
    )
    stage_gate = (
        coverage_complete
        and selection_status == PortfolioSelectionStatus.COMPLETE.value
        and counts["portfolio_equation_balanced"]
        and counts["N_interim"] == 0
        and not (dispositions & {"council_blocked"})
    )
    reconciliation = {
        **counts,
        "audit_population_denominator": len(freeze["audit_units"]),
        "mandatory_census_count": sum(row["mandatory_census"] for row in freeze["audit_units"]),
        "planned_audit_count": sum(row["selection_status"] == "selected_for_audit" for row in freeze["audit_assignments"]),
        "achieved_audit_count": len(outcomes),
        "explicit_unaudited_count": sum(row["selection_status"] == "unaudited" for row in freeze["audit_assignments"]),
        "missing_audit_count": len(missing),
        "escalated_assignment_count": len(escalated),
        "escalation_quarantine_count": len(quarantined_units),
        "audit_coverage_reconciled": coverage_complete,
        "one_portfolio_disposition_per_deep_candidate": len({row["candidate_id"] for row in portfolio_dispositions}) == n_deep,
        "seven_outputs_per_deep_candidate": len(seven_outputs) == n_deep and all(
            set(DECISION_OUTPUT_NAMES).issubset(row) for row in seven_outputs
        ),
        "three_rankings_cover_every_deep_candidate": all(
            len(rows) == n_deep and len({row["candidate_id"] for row in rows}) == n_deep
            for rows in (evidence_ranking, novelty_ranking, diversified_ranking)
        ),
    }

    draft: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "aggregate_id": "",
        "portfolio_plan_id": freeze["portfolio_plan_id"],
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "audit_revision": freeze["audit_revision"],
        "aggregate_status": "complete" if stage_gate else "diagnostic_partial",
        "stage_gate_passed": stage_gate,
        "selection_status": selection_status,
        "input_receipts": {
            "case_source_input_sha256": case.source_input_sha256,
            "deep_frame_sha256": content_sha256(deep_frame),
            "frozen_plan_sha256": content_sha256(freeze),
            "frozen_audit_bundle_sha256": content_sha256(frozen_bundle),
        },
        "retained_inputs": {
            "deep_frame": copy.deepcopy(deep_frame),
            "frozen_audit_plan": copy.deepcopy(frozen_bundle),
        },
        "frozen_decision_and_audit_plan": copy.deepcopy(freeze),
        "audit_assignments": copy.deepcopy(freeze["audit_assignments"]),
        "audit_stratum_reports": audit_report_rows,
        "audit_records": audit_records,
        "audit_corrections": corrections,
        "audit_report": {
            "population_denominator": len(freeze["audit_units"]),
            "planned_audit_count": reconciliation["planned_audit_count"],
            "achieved_audit_count": len(outcomes),
            "explicit_unaudited_count": reconciliation["explicit_unaudited_count"],
            "missing_assignment_ids": missing,
            "escalated_assignment_ids": sorted(escalated),
            "quarantined_unaudited_unit_ids": sorted(quarantined_units),
            "coverage_reconciled": coverage_complete,
        },
        "package_revisions": package_revisions,
        "seven_decision_outputs": seven_outputs,
        "candidate_audit_records": sorted(candidate_audit_records, key=lambda row: row["candidate_id"]),
        "council_records": council_records,
        "mechanism_clusters": _plain(selection.mechanism_clusters),
        "scaffold_clusters": _plain(selection.scaffold_clusters),
        "evidence_strength_ranking": evidence_ranking,
        "novelty_information_value_ranking": novelty_ranking,
        "diversified_portfolio_ranking": diversified_ranking,
        "portfolio_dispositions": sorted(portfolio_dispositions, key=lambda row: row["candidate_id"]),
        "finalists": finalist_ids,
        "reserves": reserve_ids,
        "canonical_order": canonical_order,
        "reconciliation": reconciliation,
    }
    draft["aggregate_id"] = _stable_id(
        "PORTFOLIO-AGGREGATE",
        {
            "rule": PORTFOLIO_AGGREGATE_ID_RULE,
            "projection": {key: value for key, value in draft.items() if key != "aggregate_id"},
        },
    )
    _reject_benchmark_labels(draft, "portfolio_aggregate")
    return draft


def _normalize_bundle(
    case: CaseRevision, deep_frame: Mapping[str, Any], frozen_audit_plan: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = {"plan", "audit_outcomes", "corrections", "council_reviews"}
    if not isinstance(frozen_audit_plan, Mapping) or set(frozen_audit_plan) != expected:
        raise PortfolioAggregateError("frozen_audit_plan has an invalid field set")
    normalized_plan = _normalize_plan(frozen_audit_plan["plan"], deep_frame)
    audit_outcomes = _plain(frozen_audit_plan["audit_outcomes"])
    corrections = _plain(frozen_audit_plan["corrections"])
    council_reviews = _plain(frozen_audit_plan["council_reviews"])
    if not all(isinstance(row, Mapping) for row in audit_outcomes):
        raise PortfolioAggregateError("audit_outcomes must contain objects")
    if not all(isinstance(row, Mapping) for row in corrections):
        raise PortfolioAggregateError("corrections must contain objects")
    if not all(isinstance(row, Mapping) for row in council_reviews):
        raise PortfolioAggregateError("council_reviews must contain objects")
    bundle = {
        "plan": normalized_plan,
        "audit_outcomes": sorted(audit_outcomes, key=lambda row: str(row.get("assignment_id", ""))),
        "corrections": sorted(
            corrections,
            key=lambda row: (
                str(row.get("candidate_id", "")),
                str(row.get("assignment_id", "")),
                str(row.get("target_record_id", "")),
                str(row.get("replacement_record_id", "")),
            ),
        ),
        "council_reviews": sorted(council_reviews, key=lambda row: str(row.get("candidate_id", ""))),
    }
    _reject_benchmark_labels(bundle, "frozen_audit_plan")
    freeze = _build_freeze(case, deep_frame, normalized_plan)
    return bundle, freeze


def validate_portfolio_aggregate(
    case_revision: CaseRevision | Mapping[str, Any], aggregate: Mapping[str, Any]
) -> None:
    """Rebuild the persisted aggregate from retained inputs and compare bytes."""

    case = _coerce_case(case_revision)
    if not isinstance(aggregate, Mapping):
        raise PortfolioAggregateError("portfolio aggregate must be an object")
    retained = aggregate.get("retained_inputs")
    if not isinstance(retained, Mapping) or set(retained) != {"deep_frame", "frozen_audit_plan"}:
        raise PortfolioAggregateError("portfolio aggregate lacks retained inputs")
    deep_frame = retained["deep_frame"]
    validate_screen_deep_aggregate(case, deep_frame)
    bundle, freeze = _normalize_bundle(case, deep_frame, retained["frozen_audit_plan"])
    expected = _construct_aggregate(case, deep_frame, bundle, freeze)
    if _canonical_bytes(expected) != _canonical_bytes(aggregate):
        raise PortfolioAggregateConflictError(
            "portfolio aggregate differs from deterministic retained-input reconstruction"
        )


class V7PortfolioAdapter:
    """Production adapter for persisted Stage 7 audit and portfolio review."""

    def __init__(self, persistence_root: str | Path) -> None:
        self.persistence_root = Path(persistence_root).expanduser().resolve()

    def plan_root(self, case_revision_id: str, portfolio_plan_id: str) -> Path:
        return self.persistence_root / _safe_component(case_revision_id) / _safe_component(portfolio_plan_id)

    def freeze_path(self, case_revision_id: str, portfolio_plan_id: str) -> Path:
        return self.plan_root(case_revision_id, portfolio_plan_id) / "frozen_decision_and_audit_plan.json"

    def aggregate_path(self, case_revision_id: str, portfolio_plan_id: str) -> Path:
        return self.plan_root(case_revision_id, portfolio_plan_id) / "aggregate.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PortfolioAggregateError(f"cannot read persisted portfolio artifact: {path}") from exc
        if not isinstance(value, dict):
            raise PortfolioAggregateError("persisted portfolio artifact is not an object")
        return value

    @staticmethod
    def _write_once(path: Path, value: Mapping[str, Any]) -> None:
        payload = _canonical_bytes(value) + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise PortfolioAggregateConflictError(
                    f"immutable portfolio artifact already exists with different content: {path}"
                )
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def audit_and_select(
        self,
        case_revision: CaseRevision | Mapping[str, Any],
        deep_frame: Mapping[str, Any],
        frozen_audit_plan: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Freeze, audit, correct, review, select, reconcile, and persist."""

        case = _coerce_case(case_revision)
        validate_screen_deep_aggregate(case, deep_frame)
        normalized_deep = _plain(deep_frame)
        if not normalized_deep.get("stage_gate_passed"):
            raise PortfolioAggregateError("portfolio input requires a passing Stage 5-6 aggregate")
        bundle, freeze = _normalize_bundle(case, normalized_deep, frozen_audit_plan)
        plan_id = freeze["portfolio_plan_id"]
        # The complete decision set and outcome-free audit plan are persisted first.
        self._write_once(self.freeze_path(case.case_revision_id, plan_id), freeze)

        target = self.aggregate_path(case.case_revision_id, plan_id)
        if target.is_file():
            stored = self._read_json(target)
            receipts = stored.get("input_receipts", {})
            supplied = {
                "deep_frame_sha256": content_sha256(normalized_deep),
                "frozen_audit_bundle_sha256": content_sha256(bundle),
            }
            if any(receipts.get(key) != value for key, value in supplied.items()):
                raise PortfolioAggregateConflictError(
                    "persisted portfolio plan was replayed with different deep/audit content"
                )
            validate_portfolio_aggregate(case, stored)
            return stored

        aggregate = _construct_aggregate(case, normalized_deep, bundle, freeze)
        validate_portfolio_aggregate(case, aggregate)
        self._write_once(target, aggregate)
        return self._read_json(target)


__all__ = [
    "AUDIT_CATEGORIES",
    "AUDIT_PLAN_VERSION",
    "DECISION_OUTPUT_NAMES",
    "MODEL_VERSION",
    "PortfolioAggregateConflictError",
    "PortfolioAggregateError",
    "V7PortfolioAdapter",
    "content_sha256",
    "make_frozen_audit_search",
    "preview_audit_freeze",
    "validate_portfolio_aggregate",
]
