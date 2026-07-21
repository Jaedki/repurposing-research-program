#!/usr/bin/env python3
"""Persisted schema-v7 all-admitted screening and selected deep evidence.

This production adapter consumes an immutable Stage 4 disposition aggregate and
frozen, caller-supplied screen/deep evidence.  It freezes and persists the
complete lightweight screen frame plus the deep-selection rule before it
validates any deep package.  It never retrieves evidence, infers an unreported
effect, transfers evidence between identities/scopes, uses publication
popularity for admission, or collapses the separate decision dimensions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_case_model import (
    CaseInputError,
    CaseRevision,
    CaseStatus,
    ValueStatus,
    _decode_typed,
    build_case_bundle,
    content_sha256,
    validate_case_revision,
)
from v7_deep_evidence import (
    AuthoritativeIdentityRecord,
    ClaimPolarity,
    DeepCandidateRecord,
    DeepEvidencePackage,
    IdentityResolutionStatus,
    ReportedValueStatus,
    VerificationMode,
    promote_deep_candidate,
    validate_deep_evidence_package,
)
from v7_discovery import (
    ChemicalUniverse,
    EvidenceModality,
    UncertaintyKind,
    UncertaintyLevel,
    make_structured_route,
    normalize_structured_routes,
)
from v7_production_disposition import validate_disposition_aggregate
from v7_seed_funnel import (
    CANDIDATE_ID_RULE,
    CandidateSeed,
    DetailedDisposition,
    EndpointApplicabilityReason,
    EndpointScreeningAssessment,
    EndpointScreenStatus,
    IdentityResolutionRecord,
    ScreenedCandidateRecord,
    ScreeningOutcome,
    SeedIdentityStatus,
    SeedUncertainty,
    make_endpoint_assessment,
    make_screening_decision,
)
from v7_triage_ranking import (
    CandidateDecisionProfile,
    EvidenceAncestry,
    ExposureEvidence,
    ExpertAssessment,
    LiteratureLandscape,
    RankingPreparationRecord,
    SafetyEvidence,
    ScopeEligibility,
    build_candidate_evidence_input,
    derive_candidate_profile,
    rank_candidate_profiles,
)


SCHEMA_VERSION = 7
MODEL_VERSION = "schema-v7-production-screen-deep-v1"
SCREEN_RULE_VERSION = "schema-v7-production-lightweight-screen-v1"
DEEP_SELECTION_POLICY_VERSION = "schema-v7-production-deep-selection-v1"
PLAN_ID_RULE = "schema-v7-production-screen-deep-plan-id-v1"
SELECTION_ID_RULE = "schema-v7-production-deep-selection-id-v1"
AGGREGATE_ID_RULE = "schema-v7-production-screen-deep-aggregate-id-v1"
COMPONENT_BRIDGE_ID_RULE = "schema-v7-stage4-component-bridge-id-v1"

SELECTION_STRATA = (
    "supportive_or_mixed_evidence",
    "sparse_or_unknown_evidence",
    "preclinical_only",
)
MANDATORY_SCREEN_RULES: Mapping[str, frozenset[str]] = {
    "eligibility": frozenset({"eligible", "ineligible", "conflicting", "unknown"}),
    "contraindication": frozenset({"clear", "blocked", "conflicting", "unknown"}),
    "preliminary_safety": frozenset(
        {"acceptable", "unacceptable", "conflicting", "unknown"}
    ),
    "preliminary_exposure": frozenset(
        {"feasible", "infeasible", "conflicting", "unknown"}
    ),
    "development_readiness": frozenset(
        {"ready", "preclinical_only", "not_ready", "conflicting", "unknown"}
    ),
    "case_fit": frozenset({"plausible", "not_plausible", "conflicting", "unknown"}),
}
COUNTEREVIDENCE_STATUSES = {
    "present",
    "searched_none_identified",
    "not_applicable",
}
APPLICABILITY_AXES = {
    "identity",
    "species",
    "population",
    "disease_stage",
    "tissue",
    "dose_route",
    "duration_timepoint",
    "endpoint",
}
APPLICABILITY_STATUSES = {
    "direct",
    "transfer_asserted",
    "not_applicable",
    "unknown",
    "mismatch",
}


class ScreenDeepAggregateError(ValueError):
    """Raised when screen/deep inputs or aggregate content violate schema v7."""


class ScreenDeepAggregateConflictError(ScreenDeepAggregateError):
    """Raised when immutable logical content is replayed with different bytes."""


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(key): _plain(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ScreenDeepAggregateError(
        f"Value is not canonical JSON: {type(value).__name__}"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _stable_id(prefix: str, rule: str, value: Any) -> str:
    return f"{prefix}-{_sha256({'rule': rule, 'value': value})[:24]}"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScreenDeepAggregateError(f"{label} must be a nonempty string")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ScreenDeepAggregateError(
            f"{label} must be an integer greater than or equal to {minimum}"
        )
    return value


def _text_list(
    value: Any, label: str, *, required: bool = False
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError(f"{label} must be a list")
    rows = sorted({_required_text(item, label) for item in value})
    if required and not rows:
        raise ScreenDeepAggregateError(f"{label} cannot be empty")
    return rows


def _safe_component(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in value
    ).strip("._")
    if not safe:
        raise ScreenDeepAggregateError(
            "Persistent identity cannot be converted to a safe path"
        )
    return safe


def _coerce_case(value: CaseRevision | Mapping[str, Any]) -> CaseRevision:
    if isinstance(value, CaseRevision):
        validate_case_revision(value)
        case = value
    else:
        if not isinstance(value, Mapping):
            raise ScreenDeepAggregateError(
                "case_revision must be a CaseRevision or mapping"
            )
        raw_input = value.get("original_input", value)
        if not isinstance(raw_input, Mapping):
            raise ScreenDeepAggregateError(
                "case_revision.original_input must be a mapping"
            )
        case = build_case_bundle(raw_input).case_revision
        for field_name in ("case_id", "case_revision_id", "source_input_sha256"):
            supplied = value.get(field_name)
            if supplied is not None and supplied != getattr(case, field_name):
                raise ScreenDeepAggregateConflictError(
                    f"case_revision.{field_name} conflicts with the rebuilt canonical case"
                )
        validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise ScreenDeepAggregateError(
            "screen/deep work requires a ready immutable case revision"
        )
    return case


def _decode(value: Any, annotation: Any, label: str) -> Any:
    if annotation is not Any and isinstance(annotation, type) and isinstance(value, annotation):
        return value
    try:
        return _decode_typed(_plain(value), annotation, label)
    except (CaseInputError, TypeError, ValueError) as exc:
        raise ScreenDeepAggregateError(f"{label} is malformed: {exc}") from exc


def _candidate_id(case_revision_id: str, normalized_intervention_id: str) -> str:
    return (
        "SCREENED-CANDIDATE-"
        + content_sha256(
            {
                "rule": CANDIDATE_ID_RULE,
                "projection": {
                    "case_revision_id": case_revision_id,
                    "screening_intervention_id": normalized_intervention_id,
                    "lane": "repurposing",
                },
            }
        )[:24]
    )


def _stage4_component_bridge_id(component: Mapping[str, Any]) -> str:
    projection = {
        "component_namespace": component["component_namespace"].upper(),
        "component_identifier": component["component_identifier"],
        "component_entity_kind": component["component_entity_kind"],
    }
    return _stable_id("NORMALIZED-COMPONENT", COMPONENT_BRIDGE_ID_RULE, projection)


def _normalize_admitted_frame(
    case: CaseRevision, admitted_frame: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(admitted_frame, Mapping):
        raise ScreenDeepAggregateError("admitted_frame must be an object")
    try:
        validate_disposition_aggregate(case, admitted_frame)
    except Exception as exc:
        raise ScreenDeepAggregateError(
            f"admitted_frame is not a valid persisted Stage 4 aggregate: {exc}"
        ) from exc
    result = _plain(admitted_frame)
    admitted = [
        row
        for row in result["seed_dispositions"]
        if row["canonical_disposition"] == "admit"
    ]
    normalized_ids = [row.get("normalized_intervention_id") for row in admitted]
    if any(not value for value in normalized_ids) or len(normalized_ids) != len(
        set(normalized_ids)
    ):
        raise ScreenDeepAggregateError(
            "every admitted representative needs one unique normalized intervention"
        )
    if result["identity_denominators"]["N_identity_admitted"] != len(admitted):
        raise ScreenDeepAggregateError(
            "admitted-frame identity denominator differs from admitted representatives"
        )
    return result


def _admitted_indexes(admitted: Mapping[str, Any]) -> dict[str, Any]:
    dispositions = {
        row["seed_id"]: row for row in admitted["seed_dispositions"]
    }
    seeds = {
        row["seed_id"]: _decode(row, CandidateSeed, f"seed {row.get('seed_id')}")
        for row in admitted["seeds"]
    }
    interventions = {
        row["normalized_intervention_id"]: row
        for row in admitted["normalized_interventions"]
    }
    representatives = {
        row["normalized_intervention_id"]: row
        for row in dispositions.values()
        if row["canonical_disposition"] == "admit"
    }
    return {
        "dispositions": dispositions,
        "seeds": seeds,
        "interventions": interventions,
        "representatives": representatives,
    }


def build_screened_candidate(
    case_revision: CaseRevision | Mapping[str, Any],
    admitted_frame: Mapping[str, Any],
    normalized_intervention_id: str,
) -> ScreenedCandidateRecord:
    """Build the canonical candidate identity from one admitted Stage 4 representative."""

    case = _coerce_case(case_revision)
    admitted = _normalize_admitted_frame(case, admitted_frame)
    indexes = _admitted_indexes(admitted)
    normalized_id = _required_text(
        normalized_intervention_id, "normalized_intervention_id"
    )
    representative = indexes["representatives"].get(normalized_id)
    intervention = indexes["interventions"].get(normalized_id)
    if representative is None or intervention is None:
        raise ScreenDeepAggregateError(
            "normalized intervention is not an admitted representative"
        )
    representative_seed_id = representative["seed_id"]
    member_seed_ids = sorted(
        {
            representative_seed_id,
            *(
                row["seed_id"]
                for row in indexes["dispositions"].values()
                if row["canonical_disposition"] == "merge"
                and row["representative_seed_id"] == representative_seed_id
            ),
        }
    )
    member_seeds = [indexes["seeds"][seed_id] for seed_id in member_seed_ids]
    candidate_id = _candidate_id(case.case_revision_id, normalized_id)
    routes = normalize_structured_routes(
        make_structured_route(
            case_revision_id=route.case_revision_id,
            intervention_id=candidate_id,
            causal_route=route.causal_route,
            disease_state_node=route.disease_state_node,
            intervention_target=route.intervention_target,
            action=route.action,
            direction=route.direction,
            intermediate_state=route.intermediate_state,
            endpoint_id=route.endpoint_id,
            evidence_ids=route.evidence_ids,
        )
        for seed in member_seeds
        for route in seed.structured_routes
    )
    active_ids = tuple(intervention.get("active_moiety_ids", ()))
    return ScreenedCandidateRecord(
        screened_candidate_id=candidate_id,
        case_id=case.case_id,
        case_revision_id=case.case_revision_id,
        lane="repurposing",
        screening_intervention_id=normalized_id,
        verified_normalized_intervention_id=normalized_id,
        active_moiety_id=active_ids[0] if len(active_ids) == 1 else None,
        identity_status=SeedIdentityStatus.RESOLVED,
        identity_verified=True,
        representative_seed_id=representative_seed_id,
        endpoint_ids=tuple(sorted(endpoint.endpoint_id for endpoint in case.endpoints)),
        structured_routes=routes,
        evidence_modalities=tuple(
            sorted(
                {modality for seed in member_seeds for modality in seed.evidence_modalities},
                key=lambda row: row.value,
            )
        ),
        chemical_universes=tuple(
            sorted(
                {universe for seed in member_seeds for universe in seed.chemical_universes},
                key=lambda row: row.value,
            )
        ),
        source_seed_ids=tuple(member_seed_ids),
        source_mapping_ids=tuple(
            sorted({seed.source_mapping_id for seed in member_seeds})
        ),
        discovery_route_ids=tuple(
            sorted(
                {
                    route_id
                    for seed in member_seeds
                    for route_id in seed.discovery_route_ids
                }
            )
        ),
        alias_ids=(),
    )


def _normalize_uncertainties(value: Any, label: str) -> tuple[SeedUncertainty, ...]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError(f"{label} must be a list")
    rows: list[SeedUncertainty] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {"kind", "level", "note"}:
            raise ScreenDeepAggregateError(
                f"{label}[{index}] must contain kind, level, and note"
            )
        try:
            rows.append(
                SeedUncertainty(
                    kind=UncertaintyKind(item["kind"]),
                    level=UncertaintyLevel(item["level"]),
                    note=_required_text(item["note"], f"{label}[{index}].note"),
                )
            )
        except ValueError as exc:
            raise ScreenDeepAggregateError(
                f"{label}[{index}] has an invalid controlled value"
            ) from exc
    by_kind = {row.kind: row for row in rows}
    if len(by_kind) != len(rows):
        raise ScreenDeepAggregateError(f"{label} repeats an uncertainty kind")
    return tuple(sorted(rows, key=lambda row: row.kind.value))


def _normalize_rule_assessments(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != set(MANDATORY_SCREEN_RULES):
        raise ScreenDeepAggregateError(
            f"{label} must contain exactly every mandatory lightweight screen rule"
        )
    result: dict[str, dict[str, Any]] = {}
    for rule_id, allowed in MANDATORY_SCREEN_RULES.items():
        row = value[rule_id]
        if not isinstance(row, Mapping) or set(row) not in ({
            "status",
            "reason",
            "evidence_pointer_ids",
            "uncertainty",
        }, {
            "rule_id",
            "status",
            "reason",
            "evidence_pointer_ids",
            "uncertainty",
        }):
            raise ScreenDeepAggregateError(
                f"{label}.{rule_id} has an invalid field set"
            )
        if row.get("rule_id", rule_id) != rule_id:
            raise ScreenDeepAggregateError(
                f"{label}.{rule_id}.rule_id conflicts with its mapping key"
            )
        status = _required_text(row["status"], f"{label}.{rule_id}.status")
        if status not in allowed:
            raise ScreenDeepAggregateError(
                f"{label}.{rule_id}.status has an invalid controlled value"
            )
        result[rule_id] = {
            "rule_id": rule_id,
            "status": status,
            "reason": _required_text(row["reason"], f"{label}.{rule_id}.reason"),
            "evidence_pointer_ids": _text_list(
                row["evidence_pointer_ids"],
                f"{label}.{rule_id}.evidence_pointer_ids",
            ),
            "uncertainty": _plain(
                _normalize_uncertainties(
                    row["uncertainty"], f"{label}.{rule_id}.uncertainty"
                )
            ),
        }
    return result


def _normalize_endpoint_assessments(
    case: CaseRevision, value: Any, label: str
) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError(f"{label} must be a list")
    case_endpoints = {row.endpoint_id: row for row in case.endpoints}
    rows = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "endpoint_id",
            "status",
            "reason",
            "applicability_reason",
            "evidence_pointer_ids",
            "uncertainty",
        }:
            raise ScreenDeepAggregateError(f"{label}[{index}] has an invalid field set")
        endpoint_id = _required_text(item["endpoint_id"], f"{label}[{index}].endpoint_id")
        if endpoint_id not in case_endpoints:
            raise ScreenDeepAggregateError(f"{label}[{index}] refers to an unknown endpoint")
        try:
            status = EndpointScreenStatus(item["status"])
            applicability = (
                None
                if item["applicability_reason"] is None
                else EndpointApplicabilityReason(item["applicability_reason"])
            )
        except (TypeError, ValueError) as exc:
            raise ScreenDeepAggregateError(
                f"{label}[{index}] has an invalid controlled value"
            ) from exc
        endpoint = case_endpoints[endpoint_id]
        required = (
            endpoint.required.status is ValueStatus.KNOWN
            and endpoint.required.value is True
        )
        if required and status is EndpointScreenStatus.NOT_ASSESSED:
            raise ScreenDeepAggregateError(
                f"required endpoint {endpoint_id} cannot be not_assessed"
            )
        rows.append(
            make_endpoint_assessment(
                endpoint_id,
                status,
                reason=_required_text(item["reason"], f"{label}[{index}].reason"),
                applicability_reason=applicability,
                evidence_pointer_ids=_text_list(
                    item["evidence_pointer_ids"],
                    f"{label}[{index}].evidence_pointer_ids",
                ),
                uncertainty=_normalize_uncertainties(
                    item["uncertainty"], f"{label}[{index}].uncertainty"
                ),
            )
        )
    rows.sort(key=lambda row: row.endpoint_id)
    if [row.endpoint_id for row in rows] != sorted(case_endpoints):
        raise ScreenDeepAggregateError(
            "every admitted representative must assess every case endpoint exactly once"
        )
    return tuple(rows)


def _normalize_screen_inputs(
    case: CaseRevision,
    admitted: Mapping[str, Any],
    value: Any,
) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError("candidate_screens must be a list")
    representatives = _admitted_indexes(admitted)["representatives"]
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "normalized_intervention_id",
            "representative_seed_id",
            "processing_status",
            "processing_reason",
            "rules",
            "endpoint_assessments",
            "unresolved_fields",
        }:
            raise ScreenDeepAggregateError(
                f"candidate_screens[{index}] has an invalid field set"
            )
        normalized_id = _required_text(
            item["normalized_intervention_id"],
            f"candidate_screens[{index}].normalized_intervention_id",
        )
        representative = representatives.get(normalized_id)
        if representative is None:
            raise ScreenDeepAggregateError(
                "candidate screen does not resolve to an admitted representative"
            )
        if item["representative_seed_id"] != representative["seed_id"]:
            raise ScreenDeepAggregateError(
                "candidate screen representative seed differs from Stage 4"
            )
        processing_status = _required_text(
            item["processing_status"], f"candidate_screens[{index}].processing_status"
        )
        if processing_status not in {"complete", "technical_failure"}:
            raise ScreenDeepAggregateError("screen processing status is invalid")
        rows.append(
            {
                "normalized_intervention_id": normalized_id,
                "representative_seed_id": representative["seed_id"],
                "processing_status": processing_status,
                "processing_reason": _required_text(
                    item["processing_reason"],
                    f"candidate_screens[{index}].processing_reason",
                ),
                "rules": _normalize_rule_assessments(
                    item["rules"], f"candidate_screens[{index}].rules"
                ),
                "endpoint_assessments": _plain(
                    _normalize_endpoint_assessments(
                        case,
                        item["endpoint_assessments"],
                        f"candidate_screens[{index}].endpoint_assessments",
                    )
                ),
                "unresolved_fields": _text_list(
                    item["unresolved_fields"],
                    f"candidate_screens[{index}].unresolved_fields",
                ),
            }
        )
    rows.sort(key=lambda row: row["normalized_intervention_id"])
    ids = [row["normalized_intervention_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(representatives):
        raise ScreenDeepAggregateError(
            "candidate_screens must contain exactly one record per admitted representative"
        )
    return rows


def _normalize_selection_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "policy_version",
        "capacity",
        "allocation_rule",
        "tie_rule",
        "strata",
    }:
        raise ScreenDeepAggregateError("deep_selection_policy has an invalid field set")
    if value["policy_version"] != DEEP_SELECTION_POLICY_VERSION:
        raise ScreenDeepAggregateError("deep-selection policy version mismatch")
    if value["allocation_rule"] != "round_robin_declared_strata":
        raise ScreenDeepAggregateError("unsupported deep-selection allocation rule")
    if value["tie_rule"] != "candidate_id_ascending":
        raise ScreenDeepAggregateError("unsupported deep-selection tie rule")
    raw_strata = value["strata"]
    if not isinstance(raw_strata, (list, tuple)):
        raise ScreenDeepAggregateError("deep-selection strata must be a list")
    strata: list[dict[str, Any]] = []
    for index, item in enumerate(raw_strata):
        if not isinstance(item, Mapping) or set(item) != {"stratum_id", "capacity"}:
            raise ScreenDeepAggregateError(
                f"deep-selection stratum {index} has an invalid field set"
            )
        stratum_id = _required_text(item["stratum_id"], "stratum_id")
        stratum_capacity = item["capacity"]
        if stratum_capacity is not None:
            stratum_capacity = _integer(
                stratum_capacity, f"stratum {stratum_id} capacity"
            )
        strata.append({"stratum_id": stratum_id, "capacity": stratum_capacity})
    ids = [row["stratum_id"] for row in strata]
    if len(ids) != len(set(ids)) or set(ids) != set(SELECTION_STRATA):
        raise ScreenDeepAggregateError(
            "deep-selection policy must declare every controlled stratum exactly once"
        )
    return {
        "policy_version": DEEP_SELECTION_POLICY_VERSION,
        "capacity": _integer(value["capacity"], "deep-selection capacity"),
        "allocation_rule": "round_robin_declared_strata",
        "tie_rule": "candidate_id_ascending",
        "strata": strata,
    }


def _screen_disposition(
    case: CaseRevision, screen: Mapping[str, Any]
) -> tuple[DetailedDisposition, str, str]:
    if screen["processing_status"] == "technical_failure":
        return (
            DetailedDisposition.SCREENING_TECHNICAL_FAILURE,
            "screen_processing_failure",
            screen["processing_reason"],
        )
    statuses = {key: row["status"] for key, row in screen["rules"].items()}
    if any(status == "conflicting" for status in statuses.values()):
        return (
            DetailedDisposition.EVIDENCE_INSUFFICIENT_BUT_PRESERVED,
            "screen_rule_conflict",
            "A decision-changing lightweight screen rule is conflicting.",
        )
    if statuses["eligibility"] == "ineligible":
        return (
            DetailedDisposition.PROHIBITED_INTERVENTION_TYPE,
            "screen_scope_ineligible",
            screen["rules"]["eligibility"]["reason"],
        )
    if statuses["contraindication"] == "blocked" or statuses["preliminary_safety"] == "unacceptable":
        return (
            DetailedDisposition.SAFETY_MISMATCH,
            "known_preliminary_safety_block",
            "A typed preliminary contraindication or safety rule blocks screening.",
        )
    if statuses["preliminary_exposure"] == "infeasible":
        return (
            DetailedDisposition.EXPOSURE_INFEASIBLE,
            "known_preliminary_exposure_block",
            screen["rules"]["preliminary_exposure"]["reason"],
        )
    if statuses["development_readiness"] == "not_ready":
        return (
            DetailedDisposition.UNRELATED_ENDPOINT,
            "development_not_ready_for_declared_case",
            screen["rules"]["development_readiness"]["reason"],
        )
    if statuses["case_fit"] == "not_plausible":
        return (
            DetailedDisposition.UNRELATED_ENDPOINT,
            "case_fit_not_plausible",
            screen["rules"]["case_fit"]["reason"],
        )
    case_endpoint = {row.endpoint_id: row for row in case.endpoints}
    endpoint_rows = screen["endpoint_assessments"]
    blocking = []
    for row in endpoint_rows:
        endpoint = case_endpoint[row["endpoint_id"]]
        required = (
            endpoint.required.status is ValueStatus.KNOWN
            and endpoint.required.value is True
        )
        if required and row["status"] in {
            EndpointScreenStatus.CONTRADICTORY.value,
            EndpointScreenStatus.NOT_APPLICABLE.value,
        }:
            blocking.append(row["endpoint_id"])
    if blocking:
        return (
            DetailedDisposition.WRONG_DIRECTION,
            "required_endpoint_contradictory_or_not_applicable",
            "Required endpoint screen is contradictory or not applicable: "
            + ", ".join(sorted(blocking)),
        )
    return (
        DetailedDisposition.RETAINED_FOR_DEEP_REVIEW,
        "lightweight_screen_pass",
        "No declared lightweight rule establishes a rejection, quarantine, or failure.",
    )


def _selection_stratum(
    candidate: ScreenedCandidateRecord, screen: Mapping[str, Any]
) -> str:
    if (
        screen["rules"]["development_readiness"]["status"] == "preclinical_only"
        or (
            ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS
            in candidate.chemical_universes
            and not {
                ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS,
                ChemicalUniverse.CLINICAL_STAGE_ASSETS,
            }.intersection(candidate.chemical_universes)
        )
    ):
        return "preclinical_only"
    statuses = {row["status"] for row in screen["endpoint_assessments"]}
    if statuses.intersection(
        {EndpointScreenStatus.SUPPORTIVE.value, EndpointScreenStatus.CONTRADICTORY.value}
    ):
        return "supportive_or_mixed_evidence"
    return "sparse_or_unknown_evidence"


def _screen_and_selection(
    case: CaseRevision,
    admitted: Mapping[str, Any],
    candidate_screens: Any,
    policy_input: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    screens = _normalize_screen_inputs(case, admitted, candidate_screens)
    policy = _normalize_selection_policy(policy_input)
    indexes = _admitted_indexes(admitted)
    screen_records: list[dict[str, Any]] = []
    screened_candidates: list[dict[str, Any]] = []
    candidate_objects: dict[str, ScreenedCandidateRecord] = {}
    for screen in screens:
        normalized_id = screen["normalized_intervention_id"]
        candidate = build_screened_candidate(case, admitted, normalized_id)
        candidate_objects[candidate.screened_candidate_id] = candidate
        seed = indexes["seeds"][screen["representative_seed_id"]]
        stage4_identity = next(
            row
            for row in admitted["identity_resolutions"]
            if row["seed_id"] == seed.seed_id
        )
        identity = IdentityResolutionRecord(
            identity_resolution_id=stage4_identity["identity_resolution_id"],
            seed_id=seed.seed_id,
            status=SeedIdentityStatus.RESOLVED,
            screening_intervention_id=normalized_id,
            verified_normalized_intervention_id=normalized_id,
            active_moiety_id=candidate.active_moiety_id,
            identity_verified=True,
            decision_changing_ambiguity=False,
            conflict_values=(),
            assertions=(),
            source_mapping_ids=(seed.source_mapping_id,),
            rule_version=stage4_identity["rule_version"],
        )
        endpoint_assessments = tuple(
            _decode(row, EndpointScreeningAssessment, "endpoint assessment")
            for row in screen["endpoint_assessments"]
        )
        disposition, reason_code, reason = _screen_disposition(case, screen)
        decision = make_screening_decision(
            seed,
            identity,
            disposition=disposition,
            reason=reason,
            endpoint_assessments=endpoint_assessments,
            unresolved_fields=screen["unresolved_fields"],
            rule_version=SCREEN_RULE_VERSION,
        )
        stratum = _selection_stratum(candidate, screen)
        record = {
            "screen_record_id": decision.decision_id,
            "candidate_id": candidate.screened_candidate_id,
            "normalized_intervention_id": normalized_id,
            "representative_seed_id": seed.seed_id,
            "screening_outcome": decision.screening_outcome.value,
            "reason_code": reason_code,
            "reason": reason,
            "selection_stratum": stratum,
            "preclinical_only": stratum == "preclinical_only",
            "rule_assessments": [screen["rules"][key] for key in sorted(screen["rules"])],
            "screening_decision": _plain(decision),
            "screen_rule_version": SCREEN_RULE_VERSION,
        }
        screen_records.append(record)
        if decision.screening_outcome is ScreeningOutcome.SCREENED:
            screened_candidates.append(_plain(candidate))
    screen_records.sort(key=lambda row: row["candidate_id"])
    screened_candidates.sort(key=lambda row: row["screened_candidate_id"])

    by_stratum: dict[str, list[str]] = {stratum: [] for stratum in SELECTION_STRATA}
    for row in screen_records:
        if row["screening_outcome"] == ScreeningOutcome.SCREENED.value:
            by_stratum[row["selection_stratum"]].append(row["candidate_id"])
    for values in by_stratum.values():
        values.sort()
    selected: list[str] = []
    selected_by_stratum = {stratum: 0 for stratum in SELECTION_STRATA}
    positions = {stratum: 0 for stratum in SELECTION_STRATA}
    stratum_limits = {
        row["stratum_id"]: row["capacity"] for row in policy["strata"]
    }
    while len(selected) < policy["capacity"]:
        progress = False
        for row in policy["strata"]:
            stratum = row["stratum_id"]
            if len(selected) >= policy["capacity"]:
                break
            limit = stratum_limits[stratum]
            if limit is not None and selected_by_stratum[stratum] >= limit:
                continue
            position = positions[stratum]
            if position >= len(by_stratum[stratum]):
                continue
            selected.append(by_stratum[stratum][position])
            positions[stratum] += 1
            selected_by_stratum[stratum] += 1
            progress = True
        if not progress:
            break
    selected_set = set(selected)
    screen_only: list[dict[str, Any]] = []
    candidate_to_stratum = {
        row["candidate_id"]: row["selection_stratum"] for row in screen_records
    }
    for candidate_id in sorted(
        candidate.screened_candidate_id
        for candidate in candidate_objects.values()
        if any(
            row["candidate_id"] == candidate.screened_candidate_id
            and row["screening_outcome"] == ScreeningOutcome.SCREENED.value
            for row in screen_records
        )
        and candidate.screened_candidate_id not in selected_set
    ):
        stratum = candidate_to_stratum[candidate_id]
        limit = stratum_limits[stratum]
        reason_code = (
            "stratum_capacity_exhausted"
            if limit is not None and selected_by_stratum[stratum] >= limit
            else "deep_capacity_exhausted"
        )
        body = {
            "candidate_id": candidate_id,
            "selection_stratum": stratum,
            "reason_code": reason_code,
            "reason": (
                "The immutable screened candidate remains screen-only because the "
                "frozen deep-review capacity or stratum capacity was exhausted."
            ),
            "scientific_rejection": False,
        }
        screen_only.append(
            {
                "screen_only_id": _stable_id(
                    "SCREEN-ONLY", "schema-v7-production-screen-only-v1", body
                ),
                **body,
            }
        )
    ties = [
        {
            "stratum_id": stratum,
            "candidate_ids": values,
            "tie_basis": "equal_within_stratum_without_composite_or_popularity_score",
            "tie_rule": policy["tie_rule"],
        }
        for stratum, values in by_stratum.items()
        if len(values) > 1
    ]
    strata = [
        {
            "stratum_id": row["stratum_id"],
            "capacity": row["capacity"],
            "eligible_candidate_ids": by_stratum[row["stratum_id"]],
            "eligible_count": len(by_stratum[row["stratum_id"]]),
            "selected_candidate_ids": [
                candidate_id
                for candidate_id in selected
                if candidate_to_stratum[candidate_id] == row["stratum_id"]
            ],
            "selected_count": selected_by_stratum[row["stratum_id"]],
        }
        for row in policy["strata"]
    ]
    selection_body = {
        "case_revision_id": case.case_revision_id,
        "screened_frame_sha256": _sha256(screen_records),
        "selection_rule": policy["allocation_rule"],
        "selection_policy_version": policy["policy_version"],
        "capacity": policy["capacity"],
        "strata": strata,
        "tie_rule": policy["tie_rule"],
        "ties": ties,
        "selected_candidate_ids": selected,
        "screen_only": screen_only,
        "popularity_or_publication_count_used": False,
        "composite_decision_score_used": False,
        "frozen_before_deep_work": True,
    }
    selection = {
        "deep_selection_id": _stable_id(
            "DEEP-SELECTION", SELECTION_ID_RULE, selection_body
        ),
        **selection_body,
    }
    return screen_records, screened_candidates, selection


def _payload_rows(value: Any, label: str) -> tuple[list[dict[str, str]], dict[str, bytes]]:
    if not isinstance(value, Mapping):
        raise ScreenDeepAggregateError(f"{label} must be a locator-to-content object")
    rows: list[dict[str, str]] = []
    payloads: dict[str, bytes] = {}
    for raw_locator, raw_content in value.items():
        locator = _required_text(raw_locator, f"{label} locator")
        if isinstance(raw_content, bytes):
            content = raw_content
        elif (
            isinstance(raw_content, Mapping)
            and set(raw_content) == {"base64"}
            and isinstance(raw_content["base64"], str)
        ):
            try:
                content = base64.b64decode(raw_content["base64"], validate=True)
            except ValueError as exc:
                raise ScreenDeepAggregateError(f"{label}.{locator} has invalid base64") from exc
        else:
            raise ScreenDeepAggregateError(
                f"{label}.{locator} must be bytes or a canonical base64 object"
            )
        if not content:
            raise ScreenDeepAggregateError(f"{label}.{locator} cannot be empty")
        if locator in payloads:
            raise ScreenDeepAggregateError(f"{label} repeats locator {locator}")
        payloads[locator] = content
        rows.append(
            {
                "retained_payload_locator": locator,
                "raw_content_sha256": _sha256_bytes(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    rows.sort(key=lambda row: row["retained_payload_locator"])
    return rows, payloads


def _decode_payload_rows(value: Any, label: str) -> dict[str, bytes]:
    if isinstance(value, Mapping):
        return _payload_rows(value, label)[1]
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError(f"{label} must be a list")
    payloads: dict[str, bytes] = {}
    for index, row in enumerate(value):
        if not isinstance(row, Mapping) or set(row) != {
            "retained_payload_locator",
            "raw_content_sha256",
            "content_base64",
        }:
            raise ScreenDeepAggregateError(f"{label}[{index}] has an invalid field set")
        locator = _required_text(row["retained_payload_locator"], "payload locator")
        try:
            content = base64.b64decode(row["content_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise ScreenDeepAggregateError(f"{label}[{index}] has invalid base64") from exc
        if _sha256_bytes(content) != row["raw_content_sha256"]:
            raise ScreenDeepAggregateError(f"{label}[{index}] hash mismatch")
        payloads[locator] = content
    return payloads


def _exact_identity_bridge(
    admitted_intervention: Mapping[str, Any], identity: AuthoritativeIdentityRecord
) -> dict[str, Any]:
    if identity.resolution_status is not IdentityResolutionStatus.RESOLVED:
        raise ScreenDeepAggregateError("completed deep package identity is not resolved")
    stage_kind = admitted_intervention["entity_kind"]
    deep_kind = identity.entity_kind.value
    if stage_kind != deep_kind:
        raise ScreenDeepAggregateError(
            "deep exact entity kind differs from the admitted exact intervention"
        )
    stage_structure = admitted_intervention.get("canonical_structure")
    deep_structure = identity.canonical_structure
    stage_component_rows = tuple(admitted_intervention.get("components", ()))
    stage_components = tuple(
        sorted(_stage4_component_bridge_id(row) for row in stage_component_rows)
    )
    deep_components = tuple(
        sorted(row.normalized_intervention_id for row in identity.components)
    )
    if admitted_intervention["composition_status"] != identity.composition_status.value:
        raise ScreenDeepAggregateError(
            "deep composition status differs from the admitted exact intervention"
        )
    if stage_structure is not None:
        if deep_structure is None:
            raise ScreenDeepAggregateError(
                "deep identity lost the admitted exact canonical structure"
            )
        comparisons = {
            "canonical_smiles": (
                stage_structure["canonical_smiles"], deep_structure.canonical_smiles
            ),
            "standard_inchi": (
                stage_structure["standard_inchi"], deep_structure.standard_inchi
            ),
            "full_inchikey": (
                stage_structure["full_inchikey"], deep_structure.inchikey
            ),
            "stereochemistry_status": (
                stage_structure["stereochemistry_status"],
                deep_structure.stereochemistry_status.value,
            ),
            "stereochemistry_descriptor": (
                stage_structure["stereochemistry_descriptor"],
                deep_structure.stereochemistry_descriptor,
            ),
        }
        if any(left != right for left, right in comparisons.values()):
            raise ScreenDeepAggregateError(
                "deep exact structure/stereochemistry differs from Stage 4"
            )
        match_basis = "exact_canonical_structure_and_stereochemistry"
    else:
        stage_product = admitted_intervention.get("product")
        stage_registry_ids = {
            (row["namespace"].upper(), row["identifier"])
            for row in admitted_intervention.get("registry_identifiers", ())
        }
        deep_registry_ids = {
            pair
            for assertion in identity.registry_assertions
            for pair in assertion.registry_identifiers
        }
        if not stage_registry_ids.issubset(deep_registry_ids):
            raise ScreenDeepAggregateError(
                "deep authorities do not retain every Stage 4 exact registry identifier"
            )
        if stage_components != deep_components:
            raise ScreenDeepAggregateError(
                "deep exact component identities differ from Stage 4 component bridge IDs"
            )
        stage_component_by_id = {
            _stage4_component_bridge_id(row): row for row in stage_component_rows
        }
        if len(stage_component_by_id) != len(stage_component_rows):
            raise ScreenDeepAggregateError(
                "Stage 4 repeats one exact component identity with conflicting details"
            )
        for component in identity.components:
            stage_component = stage_component_by_id[component.normalized_intervention_id]
            if (
                component.role != stage_component["role"]
                or component.amount_or_fraction
                != stage_component["amount_or_fraction"]
            ):
                raise ScreenDeepAggregateError(
                    "deep component role or amount/fraction differs from Stage 4"
                )
        if stage_product is None:
            if identity.formulation is not None:
                raise ScreenDeepAggregateError(
                    "deep formulation descriptor was added to a non-formulation intervention"
                )
            match_basis = "exact_qualified_component_composition_with_grounded_deep_details"
        elif identity.formulation is None:
            raise ScreenDeepAggregateError(
                "structureless exact identities require matching product/formulation provenance"
            )
        else:
            stage_product_id = (
                stage_product["product_namespace"].upper(),
                stage_product["product_identifier"],
            )
            if stage_product_id not in deep_registry_ids:
                raise ScreenDeepAggregateError(
                    "deep authorities do not retain the admitted exact product identifier"
                )
            reported_names = set(admitted_intervention["source_reported_identities"])
            if identity.formulation.product_name not in reported_names:
                raise ScreenDeepAggregateError(
                    "deep exact product name differs from every retained Stage 4 identity"
                )
            product_comparisons = {
                "dosage_form": (
                    stage_product["dosage_form"], identity.formulation.dosage_form
                ),
                "release_characteristic": (
                    stage_product["release_characteristic"],
                    identity.formulation.release_characteristic,
                ),
                "administration_routes": (
                    tuple(stage_product["administration_routes"]),
                    tuple(sorted(identity.formulation.administration_routes)),
                ),
                "component_ids": (
                    stage_components, tuple(sorted(identity.formulation.component_ids))
                ),
            }
            if any(left != right for left, right in product_comparisons.values()):
                raise ScreenDeepAggregateError(
                    "deep exact product/formulation attributes differ from Stage 4"
                )
            match_basis = (
                "exact_product_identifier_and_qualified_components_with_grounded_deep_details"
            )
    body = {
        "stage4_normalized_intervention_id": admitted_intervention[
            "normalized_intervention_id"
        ],
        "deep_normalized_intervention_id": identity.normalized_intervention_id,
        "deep_identity_record_id": identity.identity_record_id,
        "match_basis": match_basis,
        "stage4_resolver_assertion_ids": admitted_intervention[
            "resolver_assertion_ids"
        ],
        "deep_registry_assertion_ids": [
            row.assertion_id for row in identity.registry_assertions
        ],
        "exact_component_identity_tokens": list(stage_components),
        "stage4_component_projection": [
            {
                "component_bridge_id": _stage4_component_bridge_id(row),
                "qualified_component_id": (
                    f"{row['component_namespace'].upper()}:{row['component_identifier']}"
                ),
                "component_entity_kind": row["component_entity_kind"],
                "role": row["role"],
                "amount_or_fraction": row["amount_or_fraction"],
            }
            for row in stage_component_rows
        ],
        "deep_component_details": [
            {
                "qualified_component_id": row.normalized_intervention_id,
                "role": row.role,
                "amount_or_fraction": row.amount_or_fraction,
                "source_record_id": row.source_record_id,
                "evidence_span_id": row.evidence_span_id,
            }
            for row in identity.components
        ],
        "component_detail_basis": (
            "The content-derived bridge ID binds Stage 4 entity kind plus qualified "
            "component identifier; role and amount/fraction match exactly, and deep "
            "source/span references are verified with the package."
        ),
        "automatic_evidence_transfer_permitted": False,
    }
    return {
        "identity_bridge_id": _stable_id(
            "IDENTITY-BRIDGE", "schema-v7-production-deep-identity-bridge-v1", body
        ),
        **body,
    }


def _counterevidence(
    package: DeepEvidencePackage, value: Any
) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError("counterevidence_assessments must be a list")
    claims = {row.claim_id: row for row in package.claims}
    endpoint_ids = set(package.screened_candidate.endpoint_ids)
    counter_by_endpoint = {
        endpoint_id: sorted(
            row.claim_id
            for row in package.claims
            if row.scope.endpoint_id == endpoint_id
            and row.polarity in {ClaimPolarity.REFUTES, ClaimPolarity.NULL, ClaimPolarity.MIXED}
        )
        for endpoint_id in endpoint_ids
    }
    source_ids = {row.source_record_id for row in package.sources}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "endpoint_id",
            "status",
            "claim_ids",
            "source_record_ids",
            "search_scope",
            "reason",
        }:
            raise ScreenDeepAggregateError(
                f"counterevidence_assessments[{index}] has an invalid field set"
            )
        endpoint_id = _required_text(item["endpoint_id"], "counterevidence endpoint")
        status = _required_text(item["status"], "counterevidence status")
        if endpoint_id not in endpoint_ids or status not in COUNTEREVIDENCE_STATUSES:
            raise ScreenDeepAggregateError("counterevidence endpoint/status is invalid")
        claim_ids = _text_list(item["claim_ids"], "counterevidence claim_ids")
        cited_sources = _text_list(
            item["source_record_ids"], "counterevidence source_record_ids"
        )
        if not set(cited_sources).issubset(source_ids):
            raise ScreenDeepAggregateError(
                "counterevidence assessment cites an unknown retained source"
            )
        if status == "present":
            if claim_ids != counter_by_endpoint[endpoint_id] or not claim_ids:
                raise ScreenDeepAggregateError(
                    "present counterevidence must list every counter/null/mixed endpoint claim"
                )
            if any(claim_id not in claims for claim_id in claim_ids):
                raise ScreenDeepAggregateError("counterevidence claim does not resolve")
        elif claim_ids or counter_by_endpoint[endpoint_id]:
            raise ScreenDeepAggregateError(
                "counterevidence cannot be hidden behind a no-evidence status"
            )
        if status == "searched_none_identified" and not cited_sources:
            raise ScreenDeepAggregateError(
                "a no-counterevidence search needs at least one retained source scope"
            )
        rows.append(
            {
                "endpoint_id": endpoint_id,
                "status": status,
                "claim_ids": claim_ids,
                "source_record_ids": cited_sources,
                "search_scope": _required_text(item["search_scope"], "search_scope"),
                "reason": _required_text(item["reason"], "counterevidence reason"),
            }
        )
    rows.sort(key=lambda row: row["endpoint_id"])
    if [row["endpoint_id"] for row in rows] != sorted(endpoint_ids):
        raise ScreenDeepAggregateError(
            "counterevidence status must be explicit for every case endpoint"
        )
    return rows


def _applicability(
    package: DeepEvidencePackage, value: Any
) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError("applicability_assessments must be a list")
    evidence = {row.deep_evidence_record_id: row for row in package.evidence_records}
    claims = {row.claim_id: row for row in package.claims}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping) or set(item) != {
            "evidence_record_id",
            "axes",
            "reason",
            "uncertainty",
        }:
            raise ScreenDeepAggregateError(
                f"applicability_assessments[{index}] has an invalid field set"
            )
        record_id = _required_text(item["evidence_record_id"], "applicability evidence")
        if record_id not in evidence:
            raise ScreenDeepAggregateError("applicability evidence record does not resolve")
        raw_axes = item["axes"]
        if not isinstance(raw_axes, Mapping) or set(raw_axes) != APPLICABILITY_AXES:
            raise ScreenDeepAggregateError(
                "applicability assessment must cover every transfer axis"
            )
        axes: dict[str, Any] = {}
        for axis in sorted(APPLICABILITY_AXES):
            axis_row = raw_axes[axis]
            if not isinstance(axis_row, Mapping) or set(axis_row) != {
                "status",
                "applicability_claim_id",
                "reason",
            }:
                raise ScreenDeepAggregateError(
                    f"applicability axis {axis} has an invalid field set"
                )
            status = _required_text(axis_row["status"], f"applicability {axis} status")
            if status not in APPLICABILITY_STATUSES:
                raise ScreenDeepAggregateError(
                    f"applicability axis {axis} has an invalid status"
                )
            claim_id = _optional_text(
                axis_row["applicability_claim_id"],
                f"applicability {axis} claim",
            )
            if status == "transfer_asserted":
                if claim_id not in claims:
                    raise ScreenDeepAggregateError(
                        "every evidence transfer needs a grounded package claim"
                    )
            elif claim_id is not None:
                raise ScreenDeepAggregateError(
                    "only an explicit transfer assertion may cite an applicability claim"
                )
            axes[axis] = {
                "status": status,
                "applicability_claim_id": claim_id,
                "reason": _required_text(axis_row["reason"], f"applicability {axis} reason"),
            }
        rows.append(
            {
                "evidence_record_id": record_id,
                "claim_id": evidence[record_id].claim_id,
                "axes": axes,
                "reason": _required_text(item["reason"], "applicability reason"),
                "uncertainty": _text_list(
                    item["uncertainty"], "applicability uncertainty", required=True
                ),
            }
        )
    rows.sort(key=lambda row: row["evidence_record_id"])
    if [row["evidence_record_id"] for row in rows] != sorted(evidence):
        raise ScreenDeepAggregateError(
            "every structured study/effect record needs one applicability assessment"
        )
    return rows


def _missingness(package: DeepEvidencePackage) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(record_id: str, field_path: str, value: Any, reason: str) -> None:
        status = getattr(value, "status", None)
        if status in {ReportedValueStatus.NOT_REPORTED, ReportedValueStatus.NOT_APPLICABLE}:
            rows.append(
                {
                    "record_id": record_id,
                    "field_path": field_path,
                    "status": status.value,
                    "reason": _required_text(reason, f"missingness {field_path} reason"),
                    "inferred_value_used": False,
                }
            )

    for record in package.evidence_records:
        record_id = record.deep_evidence_record_id
        for field_name in (
            "sample_size",
            "dose",
            "administration_route",
            "duration",
            "tissue_or_cell_type",
            "exposure_or_concentration",
        ):
            value = getattr(record, field_name)
            add(record_id, field_name, value, getattr(value, "note", "Explicitly unreported."))
        add(
            record_id,
            "effect_magnitude",
            record.effect_magnitude,
            "Effect magnitude was explicitly not reported or not applicable.",
        )
        add(
            record_id,
            "statistical_uncertainty",
            record.statistical_uncertainty,
            "Statistical uncertainty was explicitly not reported or not applicable.",
        )
    rows.sort(key=lambda row: (row["record_id"], row["field_path"]))
    return rows


def _study_and_effect_records(package: DeepEvidencePackage) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    studies: list[dict[str, Any]] = []
    effects: list[dict[str, Any]] = []
    for record in package.evidence_records:
        studies.append(
            {
                "study_record_id": record.deep_evidence_record_id,
                "claim_id": record.claim_id,
                "source_record_id": record.source_record_id,
                "study_design": record.study_design.value,
                "population_or_experimental_model": _plain(
                    record.population_or_experimental_model
                ),
                "sample_size": _plain(record.sample_size),
                "comparator": _plain(record.comparator),
                "dose": _plain(record.dose),
                "administration_route": _plain(record.administration_route),
                "duration": _plain(record.duration),
                "follow_up": _plain(record.duration),
                "tissue_or_cell_type": _plain(record.tissue_or_cell_type),
                "study_limitations": list(record.study_limitations),
                "risk_of_bias_assessment": _plain(record.risk_of_bias_assessment),
            }
        )
        effect_body = {
            "study_record_id": record.deep_evidence_record_id,
            "claim_id": record.claim_id,
            "endpoint_id": record.endpoint_id,
            "measure": record.endpoint_measure,
            "effect_direction": record.effect_direction.value,
            "effect_magnitude": _plain(record.effect_magnitude),
            "statistical_uncertainty": _plain(record.statistical_uncertainty),
            "reported_only": record.effect_magnitude.status.value,
        }
        effects.append(
            {
                "effect_record_id": _stable_id(
                    "EFFECT", "schema-v7-production-effect-projection-v1", effect_body
                ),
                **effect_body,
            }
        )
    return studies, effects


def _decision_outputs(profile: CandidateDecisionProfile) -> dict[str, Any]:
    return {
        "therapeutic_support": _plain(profile.therapeutic_support),
        "evidence_quality": _plain(profile.evidence_quality),
        "readiness": _plain(profile.repurposing_readiness),
        "novelty": _plain(profile.novelty_underexploration),
        "uncertainty": _plain(profile.uncertainty),
        "information_value": _plain(profile.information_value),
        "portfolio_diversity": {
            "status": "pending_portfolio",
            "reason": (
                "Portfolio diversity is a downstream portfolio property and is not "
                "allowed to alter Stage 6 therapeutic evidence values."
            ),
        },
        "additional_separate_dimensions": {
            "mechanistic_coherence": _plain(profile.mechanistic_coherence),
            "human_clinical_evidence": _plain(profile.human_clinical_evidence),
            "human_derived_model_evidence": _plain(
                profile.human_derived_model_evidence
            ),
            "endpoint_specificity": _plain(profile.endpoint_specificity),
            "clinical_translatability": _plain(profile.clinical_translatability),
            "exposure_feasibility": _plain(profile.exposure_feasibility),
            "safety_and_tolerability": _plain(profile.safety_and_tolerability),
        },
        "endpoint_decision_dimensions": _plain(profile.endpoint_assessments),
        "universal_composite_rank_emitted": False,
    }


def _normalize_deep_results(
    case: CaseRevision,
    admitted: Mapping[str, Any],
    screened_candidates: Iterable[Mapping[str, Any]],
    selection: Mapping[str, Any],
    value: Any,
) -> tuple[list[dict[str, Any]], list[CandidateDecisionProfile]]:
    if not isinstance(value, (list, tuple)):
        raise ScreenDeepAggregateError("deep_results must be a list")
    candidate_objects = {
        row["screened_candidate_id"]: _decode(
            row, ScreenedCandidateRecord, f"screened candidate {row.get('screened_candidate_id')}"
        )
        for row in screened_candidates
    }
    selected_ids = set(selection["selected_candidate_ids"])
    admitted_interventions = _admitted_indexes(admitted)["interventions"]
    input_rows: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ScreenDeepAggregateError(f"deep_results[{index}] must be an object")
        candidate_id = _required_text(item.get("candidate_id"), f"deep_results[{index}].candidate_id")
        if candidate_id in input_rows:
            raise ScreenDeepAggregateError("deep_results repeats a selected candidate")
        input_rows[candidate_id] = item
    if set(input_rows) != selected_ids:
        raise ScreenDeepAggregateError(
            "deep_results must contain exactly one result per frozen selected candidate"
        )

    rows: list[dict[str, Any]] = []
    profiles: list[CandidateDecisionProfile] = []
    complete_fields = {
        "candidate_id",
        "status",
        "reason",
        "package",
        "retained_payloads",
        "primary_endpoint_id",
        "ancestry",
        "exposure",
        "safety",
        "literature_landscape",
        "scope_eligibility",
        "scope_reason",
        "explicit_uncertainties",
        "expert_assessments",
        "counterevidence_assessments",
        "applicability_assessments",
        "missing_fields",
    }
    incomplete_fields = {"candidate_id", "status", "reason", "missing_fields"}
    for candidate_id in sorted(input_rows):
        item = input_rows[candidate_id]
        status = _required_text(item.get("status"), f"deep result {candidate_id}.status")
        if status not in {"completed", "quarantined", "technical_failure"}:
            raise ScreenDeepAggregateError("deep result status is invalid")
        expected_fields = complete_fields if status == "completed" else incomplete_fields
        if set(item) != expected_fields:
            raise ScreenDeepAggregateError(
                f"deep result {candidate_id} has an invalid field set for {status}"
            )
        reason = _required_text(item["reason"], f"deep result {candidate_id}.reason")
        missing_fields = _text_list(
            item["missing_fields"], f"deep result {candidate_id}.missing_fields"
        )
        if status != "completed":
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "status": "deep_quarantined" if status == "quarantined" else "deep_failed",
                    "reason": reason,
                    "missing_fields": missing_fields,
                    "deep_selection_id": selection["deep_selection_id"],
                }
            )
            continue
        candidate = candidate_objects[candidate_id]
        package = _decode(item["package"], DeepEvidencePackage, f"deep package {candidate_id}")
        if package.screened_candidate != candidate:
            raise ScreenDeepAggregateError(
                "deep package candidate differs from the frozen screened candidate"
            )
        payload_rows, payloads = _payload_rows(
            item["retained_payloads"], f"deep result {candidate_id}.retained_payloads"
        )
        validate_deep_evidence_package(
            package,
            verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
            retained_payloads=payloads,
        )
        promoted: DeepCandidateRecord = promote_deep_candidate(
            package, retained_payloads=payloads
        )
        identity = next(
            row
            for row in package.identity_records
            if row.identity_record_id == package.current_identity_record_id
        )
        bridge = _exact_identity_bridge(
            admitted_interventions[candidate.screening_intervention_id], identity
        )
        promoted_projection = _plain(promoted)
        # Deep identity normalization verifies the retained authority package, but it must
        # promote the same canonical Stage 4 intervention identity rather than minting a
        # second normalized-intervention denominator from the deep identity record.
        promoted_projection["normalized_intervention_id"] = candidate.screening_intervention_id
        ancestry = tuple(
            sorted(
                (
                    _decode(row, EvidenceAncestry, f"deep result {candidate_id}.ancestry")
                    for row in item["ancestry"]
                ),
                key=lambda row: row.evidence_record_id,
            )
        )
        exposure = tuple(
            sorted(
                (
                    _decode(row, ExposureEvidence, f"deep result {candidate_id}.exposure")
                    for row in item["exposure"]
                ),
                key=lambda row: row.exposure_record_id,
            )
        )
        safety = tuple(
            sorted(
                (
                    _decode(row, SafetyEvidence, f"deep result {candidate_id}.safety")
                    for row in item["safety"]
                ),
                key=lambda row: row.safety_record_id,
            )
        )
        if not exposure or not safety:
            raise ScreenDeepAggregateError(
                "completed deep package needs structured exposure and safety records"
            )
        landscape = _decode(
            item["literature_landscape"],
            LiteratureLandscape,
            f"deep result {candidate_id}.literature_landscape",
        )
        expert = tuple(
            sorted(
                (
                    _decode(row, ExpertAssessment, f"deep result {candidate_id}.expert")
                    for row in item["expert_assessments"]
                ),
                key=lambda row: row.assessment_id,
            )
        )
        try:
            scope_eligibility = ScopeEligibility(item["scope_eligibility"])
        except (TypeError, ValueError) as exc:
            raise ScreenDeepAggregateError("deep scope eligibility is invalid") from exc
        candidate_input = build_candidate_evidence_input(
            case,
            package,
            primary_endpoint_id=_required_text(
                item["primary_endpoint_id"], "primary_endpoint_id"
            ),
            ancestry=ancestry,
            exposure=exposure,
            safety=safety,
            literature_landscape=landscape,
            scope_eligibility=scope_eligibility,
            scope_reason=_required_text(item["scope_reason"], "scope_reason"),
            explicit_uncertainties=_text_list(
                item["explicit_uncertainties"], "explicit_uncertainties"
            ),
            expert_assessments=expert,
        )
        profile = derive_candidate_profile(candidate_input)
        profiles.append(profile)
        counter = _counterevidence(package, item["counterevidence_assessments"])
        applicability = _applicability(package, item["applicability_assessments"])
        missingness = _missingness(package)
        studies, effects = _study_and_effect_records(package)
        rows.append(
            {
                "candidate_id": candidate_id,
                "status": "deep",
                "reason": reason,
                "missing_fields": missing_fields,
                "deep_selection_id": selection["deep_selection_id"],
                "deep_candidate": promoted_projection,
                "identity_bridge": bridge,
                "package": _plain(package),
                "retained_payloads": payload_rows,
                "study_records": studies,
                "effect_records": effects,
                "counterevidence_assessments": counter,
                "structured_safety": _plain(safety),
                "structured_exposure": _plain(exposure),
                "applicability_assessments": applicability,
                "missingness_records": missingness,
                "candidate_decision_profile": _plain(profile),
                "separate_decision_outputs": _decision_outputs(profile),
            }
        )
    rows.sort(key=lambda row: row["candidate_id"])
    profiles.sort(key=lambda row: row.candidate_id)
    return rows, profiles


def _normalized_frozen_input(
    case: CaseRevision,
    admitted: Mapping[str, Any],
    frozen_evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(frozen_evidence, Mapping) or set(frozen_evidence) != {
        "evidence_revision",
        "screen_rule_version",
        "candidate_screens",
        "deep_selection_policy",
        "deep_results",
    }:
        raise ScreenDeepAggregateError("frozen_evidence has an invalid field set")
    if frozen_evidence["screen_rule_version"] != SCREEN_RULE_VERSION:
        raise ScreenDeepAggregateError("screen rule version mismatch")
    screens, candidates, selection = _screen_and_selection(
        case,
        admitted,
        frozen_evidence["candidate_screens"],
        frozen_evidence["deep_selection_policy"],
    )
    normalized_deep_results = _plain(frozen_evidence["deep_results"])
    if not isinstance(normalized_deep_results, list) or any(
        not isinstance(row, Mapping) or not isinstance(row.get("candidate_id"), str)
        for row in normalized_deep_results
    ):
        raise ScreenDeepAggregateError(
            "deep_results must contain candidate-keyed objects"
        )
    normalized_deep_results.sort(key=lambda row: row["candidate_id"])
    normalized = {
        "evidence_revision": _required_text(
            frozen_evidence["evidence_revision"], "evidence_revision"
        ),
        "screen_rule_version": SCREEN_RULE_VERSION,
        "candidate_screens": _normalize_screen_inputs(
            case, admitted, frozen_evidence["candidate_screens"]
        ),
        "deep_selection_policy": _normalize_selection_policy(
            frozen_evidence["deep_selection_policy"]
        ),
        "deep_results": normalized_deep_results,
    }
    return normalized, screens, candidates, selection


def _construct_aggregate(
    case: CaseRevision,
    admitted_frame: Mapping[str, Any],
    frozen_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    admitted = _normalize_admitted_frame(case, admitted_frame)
    normalized, screen_records, screened_candidates, selection = _normalized_frozen_input(
        case, admitted, frozen_evidence
    )
    deep_results, profiles = _normalize_deep_results(
        case,
        admitted,
        screened_candidates,
        selection,
        normalized["deep_results"],
    )
    rankings: tuple[RankingPreparationRecord, ...] = rank_candidate_profiles(profiles)
    ranking_by_candidate = {row.candidate_id: row for row in rankings}
    for row in deep_results:
        if row["status"] == "deep":
            row["ranking_preparation"] = _plain(ranking_by_candidate[row["candidate_id"]])

    outcome_counts = {
        value: sum(row["screening_outcome"] == value for row in screen_records)
        for value in (
            "screened",
            "screen_rejected",
            "screen_quarantined",
            "screen_failed",
        )
    }
    deep_counts = {
        value: sum(row["status"] == value for row in deep_results)
        for value in ("deep", "deep_quarantined", "deep_failed")
    }
    n_admit = sum(
        row["canonical_disposition"] == "admit"
        for row in admitted["seed_dispositions"]
    )
    reconciliation = {
        "N_admit": n_admit,
        "N_screened": outcome_counts["screened"],
        "N_screen_rejected": outcome_counts["screen_rejected"],
        "N_screen_quarantined": outcome_counts["screen_quarantined"],
        "N_screen_failed": outcome_counts["screen_failed"],
        "N_selected_deep": len(selection["selected_candidate_ids"]),
        "N_screen_only": len(selection["screen_only"]),
        "N_deep": deep_counts["deep"],
        "N_deep_quarantined": deep_counts["deep_quarantined"],
        "N_deep_failed": deep_counts["deep_failed"],
        "screen_equation": (
            "N_admit = N_screened + N_screen_rejected + "
            "N_screen_quarantined + N_screen_failed"
        ),
        "screen_equation_balanced": n_admit == sum(outcome_counts.values()),
        "selection_equation": "N_screened = N_selected_deep + N_screen_only",
        "selection_equation_balanced": outcome_counts["screened"]
        == len(selection["selected_candidate_ids"]) + len(selection["screen_only"]),
        "deep_equation": (
            "N_selected_deep = N_deep + N_deep_quarantined + N_deep_failed"
        ),
        "deep_equation_balanced": len(selection["selected_candidate_ids"])
        == sum(deep_counts.values()),
        "screen_record_count": len(screen_records),
        "screened_candidate_count": len(screened_candidates),
        "deep_result_count": len(deep_results),
        "all_admitted_representatives_screened_once": len(screen_records) == n_admit,
    }
    screen_gate = (
        reconciliation["screen_equation_balanced"]
        and reconciliation["all_admitted_representatives_screened_once"]
        and reconciliation["N_screen_failed"] == 0
        and bool(admitted["stage_gate_passed"])
    )
    deep_gate = (
        reconciliation["selection_equation_balanced"]
        and reconciliation["deep_equation_balanced"]
        and reconciliation["N_deep_failed"] == 0
    )
    stage_gate = screen_gate and deep_gate
    plan_projection = {
        "case_revision_id": case.case_revision_id,
        "evidence_revision": normalized["evidence_revision"],
        "screen_rule_version": SCREEN_RULE_VERSION,
        "selection_policy_version": DEEP_SELECTION_POLICY_VERSION,
    }
    plan_id = _stable_id("SCREEN-DEEP-PLAN", PLAN_ID_RULE, plan_projection)
    draft: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "aggregate_id": "",
        "screen_deep_plan_id": plan_id,
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "aggregate_status": "complete" if stage_gate else "diagnostic_partial",
        "screen_stage_gate_passed": screen_gate,
        "deep_stage_gate_passed": deep_gate,
        "stage_gate_passed": stage_gate,
        "input_receipts": {
            "case_source_input_sha256": case.source_input_sha256,
            "admitted_frame_sha256": _sha256(admitted),
            "frozen_evidence_sha256": _sha256(normalized),
            "screened_frame_sha256": selection["screened_frame_sha256"],
            "deep_selection_sha256": _sha256(selection),
        },
        "retained_inputs": {
            "admitted_frame": admitted,
            "frozen_evidence": normalized,
        },
        "screen_records": screen_records,
        "screened_candidates": screened_candidates,
        "deep_selection": selection,
        "deep_results": deep_results,
        "deep_packages": [row for row in deep_results if row["status"] == "deep"],
        "structured_safety": [
            record
            for row in deep_results
            if row["status"] == "deep"
            for record in row["structured_safety"]
        ],
        "structured_exposure": [
            record
            for row in deep_results
            if row["status"] == "deep"
            for record in row["structured_exposure"]
        ],
        "candidate_decision_profiles": [
            row["candidate_decision_profile"]
            for row in deep_results
            if row["status"] == "deep"
        ],
        "ranking_preparation": _plain(rankings),
        "reconciliation": reconciliation,
    }
    draft["aggregate_id"] = _stable_id(
        "SCREEN-DEEP-AGGREGATE",
        AGGREGATE_ID_RULE,
        {key: value for key, value in draft.items() if key != "aggregate_id"},
    )
    return draft


def validate_screen_deep_aggregate(
    case_revision: CaseRevision | Mapping[str, Any], aggregate: Mapping[str, Any]
) -> None:
    """Rebuild the aggregate from retained inputs and require byte-exact equality."""

    case = _coerce_case(case_revision)
    if not isinstance(aggregate, Mapping):
        raise ScreenDeepAggregateError("screen/deep aggregate must be an object")
    retained = aggregate.get("retained_inputs")
    if not isinstance(retained, Mapping) or set(retained) != {
        "admitted_frame",
        "frozen_evidence",
    }:
        raise ScreenDeepAggregateError("screen/deep aggregate lacks retained inputs")
    screen_records = aggregate.get("screen_records")
    if not isinstance(screen_records, list):
        raise ScreenDeepAggregateError("screen_records must be a list")
    screen_ids = [row.get("screen_record_id") for row in screen_records if isinstance(row, Mapping)]
    candidate_ids = [row.get("candidate_id") for row in screen_records if isinstance(row, Mapping)]
    if len(screen_ids) != len(set(screen_ids)) or len(candidate_ids) != len(set(candidate_ids)):
        raise ScreenDeepAggregateError("screen records contain duplicate identities")
    expected = _construct_aggregate(
        case, retained["admitted_frame"], retained["frozen_evidence"]
    )
    if _canonical_bytes(expected) != _canonical_bytes(aggregate):
        raise ScreenDeepAggregateConflictError(
            "screen/deep aggregate differs from deterministic retained-input reconstruction"
        )


class V7ScreenDeepAdapter:
    """Production test-facing adapter for persisted Stage 5-6 aggregation."""

    def __init__(self, persistence_root: str | Path) -> None:
        self.persistence_root = Path(persistence_root).expanduser().resolve()

    def plan_root(self, case_revision_id: str, screen_deep_plan_id: str) -> Path:
        return (
            self.persistence_root
            / _safe_component(case_revision_id)
            / _safe_component(screen_deep_plan_id)
        )

    def selection_path(self, case_revision_id: str, screen_deep_plan_id: str) -> Path:
        return self.plan_root(case_revision_id, screen_deep_plan_id) / "deep_selection.json"

    def aggregate_path(self, case_revision_id: str, screen_deep_plan_id: str) -> Path:
        return self.plan_root(case_revision_id, screen_deep_plan_id) / "aggregate.json"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ScreenDeepAggregateError(
                f"cannot read persisted screen/deep artifact: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise ScreenDeepAggregateError("persisted screen/deep artifact is not an object")
        return value

    @staticmethod
    def _write_once(path: Path, value: Mapping[str, Any]) -> None:
        payload = _canonical_bytes(value) + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise ScreenDeepAggregateConflictError(
                    f"immutable screen/deep artifact already exists with different content: {path}"
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

    def screen_and_deepen(
        self,
        case_revision: CaseRevision | Mapping[str, Any],
        admitted_frame: Mapping[str, Any],
        frozen_evidence: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Screen every admit, freeze selection, validate deep work, and persist."""

        case = _coerce_case(case_revision)
        admitted = _normalize_admitted_frame(case, admitted_frame)
        normalized, screen_records, screened_candidates, selection = _normalized_frozen_input(
            case, admitted, frozen_evidence
        )
        plan_id = _stable_id(
            "SCREEN-DEEP-PLAN",
            PLAN_ID_RULE,
            {
                "case_revision_id": case.case_revision_id,
                "evidence_revision": normalized["evidence_revision"],
                "screen_rule_version": SCREEN_RULE_VERSION,
                "selection_policy_version": DEEP_SELECTION_POLICY_VERSION,
            },
        )
        selection_artifact = {
            "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "screen_deep_plan_id": plan_id,
            "case_revision_id": case.case_revision_id,
            "evidence_revision": normalized["evidence_revision"],
            "selection_input_receipts": {
                "admitted_frame_sha256": _sha256(admitted),
                "candidate_screens_sha256": _sha256(normalized["candidate_screens"]),
                "deep_selection_policy_sha256": _sha256(
                    normalized["deep_selection_policy"]
                ),
            },
            "screen_records": screen_records,
            "screened_candidates": screened_candidates,
            "deep_selection": selection,
            "frozen_before_deep_work": True,
        }
        # This immutable write intentionally precedes deep-package validation.
        self._write_once(self.selection_path(case.case_revision_id, plan_id), selection_artifact)

        target = self.aggregate_path(case.case_revision_id, plan_id)
        if target.is_file():
            stored = self._read_json(target)
            supplied_hashes = {
                "admitted_frame_sha256": _sha256(admitted),
                "frozen_evidence_sha256": _sha256(normalized),
            }
            receipts = stored.get("input_receipts", {})
            if any(receipts.get(key) != value for key, value in supplied_hashes.items()):
                raise ScreenDeepAggregateConflictError(
                    "persisted screen/deep plan was replayed with different admitted or evidence content"
                )
            validate_screen_deep_aggregate(case, stored)
            return stored

        aggregate = _construct_aggregate(case, admitted, normalized)
        validate_screen_deep_aggregate(case, aggregate)
        self._write_once(target, aggregate)
        return self._read_json(target)


__all__ = [
    "AGGREGATE_ID_RULE",
    "DEEP_SELECTION_POLICY_VERSION",
    "MODEL_VERSION",
    "SCREEN_RULE_VERSION",
    "ScreenDeepAggregateConflictError",
    "ScreenDeepAggregateError",
    "V7ScreenDeepAdapter",
    "build_screened_candidate",
    "validate_screen_deep_aggregate",
]
