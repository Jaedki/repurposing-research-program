#!/usr/bin/env python3
"""Immutable schema-v7 candidate-seed and lightweight funnel records.

This module starts from already-retrieved source assertions.  It defines and
validates their seed, lineage, provisional identity, current disposition,
screened-candidate linkage, and quarantine records.  It does not retrieve
sources, run identity resolvers, build deep evidence, rank, audit, persist a
runtime, or create final outputs.
"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Iterable

from v7_case_model import (
    CaseRevision,
    CaseStatus,
    QualifiedValue,
    ValueStatus,
    canonical_bytes,
    content_sha256,
    validate_case_revision,
)
from v7_discovery import (
    ChemicalUniverse,
    DevelopmentStatus,
    EvidenceModality,
    StructuredCausalRoute,
    UncertaintyKind,
    UncertaintyLevel,
    make_structured_route,
    normalize_structured_routes,
    validate_structured_route,
)


SCHEMA_VERSION = 7
SEED_FUNNEL_MODEL_VERSION = "schema-v7-seed-funnel-v2"
SEED_ID_RULE = "schema-v7-seed-id-v1"
MAPPING_ID_RULE = "schema-v7-source-mapping-id-v1"
ROUTE_ID_RULE = "schema-v7-seed-discovery-route-id-v1"
ALIAS_ID_RULE = "schema-v7-seed-alias-id-v1"
IDENTITY_ASSERTION_ID_RULE = "schema-v7-identity-assertion-id-v1"
IDENTITY_RESOLUTION_ID_RULE = "schema-v7-identity-resolution-id-v1"
NORMALIZED_INTERVENTION_ID_RULE = "schema-v7-normalized-intervention-id-v1"
ACTIVE_MOIETY_ID_RULE = "schema-v7-active-moiety-id-v1"
SCREENING_DECISION_ID_RULE = "schema-v7-screening-decision-id-v1"
CANDIDATE_ID_RULE = "schema-v7-screened-candidate-id-v1"
SEED_CANDIDATE_LINK_ID_RULE = "schema-v7-seed-candidate-link-id-v1"
QUARANTINE_ID_RULE = "schema-v7-quarantined-seed-id-v1"
SNAPSHOT_ID_RULE = "schema-v7-seed-funnel-snapshot-id-v1"


class SeedFunnelError(ValueError):
    """Raised when lightweight seed/funnel records are inconsistent."""


class CompoundHintKind(str, Enum):
    REGISTRY_IDENTIFIER = "registry_identifier"
    DATABASE_IDENTIFIER = "database_identifier"
    STRUCTURE_IDENTIFIER = "structure_identifier"
    NAME_HINT = "name_hint"


class SeedIdentityStatus(str, Enum):
    UNASSESSED = "unassessed"
    NAME_HINT_ONLY = "name_hint_only"
    PROVISIONAL = "provisional"
    RESOLVED = "resolved"
    CONFLICTING = "conflicting"
    UNRESOLVED = "unresolved"
    QUARANTINED = "quarantined"


class IdentityAssertionStatus(str, Enum):
    UNVERIFIED = "unverified"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    CONFLICTING = "conflicting"


class AliasKind(str, Enum):
    SOURCE_NAME = "source_name"
    REGISTRY_IDENTIFIER = "registry_identifier"
    DATABASE_IDENTIFIER = "database_identifier"
    SYNONYM = "synonym"
    TRADE_NAME = "trade_name"
    SALT_OR_SOLVATE = "salt_or_solvate"
    FORMULATION = "formulation"


class AliasAssertionStatus(str, Enum):
    UNVERIFIED = "unverified"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    CONFLICTING = "conflicting"


class EndpointScreenStatus(str, Enum):
    SUPPORTIVE = "supportive"
    NEUTRAL = "neutral"
    CONTRADICTORY = "contradictory"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"
    NOT_ASSESSED = "not_assessed"


class EndpointApplicabilityReason(str, Enum):
    POPULATION_MISMATCH = "population_mismatch"
    DISEASE_STAGE_MISMATCH = "disease_stage_mismatch"
    ENDPOINT_ROLE_NOT_APPLICABLE = "endpoint_role_not_applicable"
    INTERVENTION_SCOPE_MISMATCH = "intervention_scope_mismatch"
    OTHER = "other"


class DetailedDisposition(str, Enum):
    RETAINED_FOR_IDENTITY_RESOLUTION = "retained_for_identity_resolution"
    RETAINED_FOR_DEEP_REVIEW = "retained_for_deep_review"
    DUPLICATE_ALIAS = "duplicate_alias"
    DUPLICATE_FORMULATION = "duplicate_formulation"
    WRONG_DIRECTION = "wrong_direction"
    UNRELATED_ENDPOINT = "unrelated_endpoint"
    PROHIBITED_INTERVENTION_TYPE = "prohibited_intervention_type"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    EVIDENCE_INSUFFICIENT_BUT_PRESERVED = "evidence_insufficient_but_preserved"
    EXPOSURE_INFEASIBLE = "exposure_infeasible"
    SAFETY_MISMATCH = "safety_mismatch"
    QUARANTINED_INVALID_SOURCE = "quarantined_invalid_source"
    SCREENING_TECHNICAL_FAILURE = "screening_technical_failure"
    BASELINE_CARE = "baseline_care"
    TECHNICAL_FAILURE = "technical_failure"


class CanonicalDisposition(str, Enum):
    ADMIT = "admit"
    MERGE = "merge"
    BASELINE = "baseline"
    REJECT = "reject"
    QUARANTINE = "quarantine"
    FAILED = "failed"


class ScreeningOutcome(str, Enum):
    SCREENED = "screened"
    SCREEN_REJECTED = "screen_rejected"
    SCREEN_QUARANTINED = "screen_quarantined"
    SCREEN_FAILED = "screen_failed"
    NOT_SCREENED = "not_screened"


class SeedCandidateRole(str, Enum):
    REPRESENTATIVE = "representative"
    DUPLICATE_ALIAS = "duplicate_alias"
    DUPLICATE_FORMULATION = "duplicate_formulation"


DETAILED_TO_CANONICAL = {
    DetailedDisposition.RETAINED_FOR_DEEP_REVIEW: CanonicalDisposition.ADMIT,
    DetailedDisposition.DUPLICATE_ALIAS: CanonicalDisposition.MERGE,
    DetailedDisposition.DUPLICATE_FORMULATION: CanonicalDisposition.MERGE,
    DetailedDisposition.BASELINE_CARE: CanonicalDisposition.BASELINE,
    DetailedDisposition.WRONG_DIRECTION: CanonicalDisposition.ADMIT,
    DetailedDisposition.UNRELATED_ENDPOINT: CanonicalDisposition.ADMIT,
    DetailedDisposition.PROHIBITED_INTERVENTION_TYPE: CanonicalDisposition.REJECT,
    DetailedDisposition.EXPOSURE_INFEASIBLE: CanonicalDisposition.ADMIT,
    DetailedDisposition.SAFETY_MISMATCH: CanonicalDisposition.ADMIT,
    DetailedDisposition.RETAINED_FOR_IDENTITY_RESOLUTION: CanonicalDisposition.QUARANTINE,
    DetailedDisposition.IDENTITY_UNRESOLVED: CanonicalDisposition.QUARANTINE,
    DetailedDisposition.EVIDENCE_INSUFFICIENT_BUT_PRESERVED: CanonicalDisposition.ADMIT,
    DetailedDisposition.SCREENING_TECHNICAL_FAILURE: CanonicalDisposition.ADMIT,
    DetailedDisposition.QUARANTINED_INVALID_SOURCE: CanonicalDisposition.QUARANTINE,
    DetailedDisposition.TECHNICAL_FAILURE: CanonicalDisposition.FAILED,
}

DETAILED_TO_SCREENING_OUTCOME = {
    DetailedDisposition.RETAINED_FOR_DEEP_REVIEW: ScreeningOutcome.SCREENED,
    DetailedDisposition.WRONG_DIRECTION: ScreeningOutcome.SCREEN_REJECTED,
    DetailedDisposition.UNRELATED_ENDPOINT: ScreeningOutcome.SCREEN_REJECTED,
    DetailedDisposition.EXPOSURE_INFEASIBLE: ScreeningOutcome.SCREEN_REJECTED,
    DetailedDisposition.SAFETY_MISMATCH: ScreeningOutcome.SCREEN_REJECTED,
    DetailedDisposition.EVIDENCE_INSUFFICIENT_BUT_PRESERVED: ScreeningOutcome.SCREEN_QUARANTINED,
    DetailedDisposition.SCREENING_TECHNICAL_FAILURE: ScreeningOutcome.SCREEN_FAILED,
    DetailedDisposition.RETAINED_FOR_IDENTITY_RESOLUTION: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.DUPLICATE_ALIAS: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.DUPLICATE_FORMULATION: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.PROHIBITED_INTERVENTION_TYPE: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.IDENTITY_UNRESOLVED: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.QUARANTINED_INVALID_SOURCE: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.BASELINE_CARE: ScreeningOutcome.NOT_SCREENED,
    DetailedDisposition.TECHNICAL_FAILURE: ScreeningOutcome.NOT_SCREENED,
}


@dataclass(frozen=True)
class CompoundHint:
    kind: CompoundHintKind
    value: str
    namespace: str


@dataclass(frozen=True)
class SeedUncertainty:
    kind: UncertaintyKind
    level: UncertaintyLevel
    note: str


@dataclass(frozen=True)
class SeedSourceMapping:
    mapping_id: str
    seed_id: str
    case_id: str
    case_revision_id: str
    source_id: str
    source_release: str
    native_record_id: str
    assertion_locator: str
    raw_intervention_assertion: str


@dataclass(frozen=True)
class SeedDiscoveryRoute:
    route_id: str
    seed_id: str
    source_mapping_id: str
    query_id: str
    query_record_locator: str
    retrieval_content_receipt_id: str


@dataclass(frozen=True)
class CandidateSeed:
    seed_id: str
    case_id: str
    case_revision_id: str
    endpoint_ids: tuple[str, ...]
    compound_hint: CompoundHint
    source_mapping_id: str
    discovery_route_ids: tuple[str, ...]
    structured_routes: tuple[StructuredCausalRoute, ...]
    evidence_modalities: tuple[EvidenceModality, ...]
    chemical_universes: tuple[ChemicalUniverse, ...]
    development_status_hint: QualifiedValue[DevelopmentStatus]
    identity_status: SeedIdentityStatus
    uncertainty: tuple[SeedUncertainty, ...]


@dataclass(frozen=True)
class SeedAlias:
    alias_id: str
    seed_id: str
    alias_kind: AliasKind
    raw_alias: str
    comparison_value: str
    source_mapping_id: str
    discovery_route_ids: tuple[str, ...]
    assertion_status: AliasAssertionStatus
    equivalent_seed_id: str | None
    authority: str
    authority_release: str


@dataclass(frozen=True)
class IdentityAssertion:
    assertion_id: str
    seed_id: str
    authority: str
    authority_release: str
    identifier_type: str
    identifier: str
    assertion_status: IdentityAssertionStatus
    source_mapping_ids: tuple[str, ...]


@dataclass(frozen=True)
class IdentityResolutionRecord:
    identity_resolution_id: str
    seed_id: str
    status: SeedIdentityStatus
    screening_intervention_id: str | None
    verified_normalized_intervention_id: str | None
    active_moiety_id: str | None
    identity_verified: bool
    decision_changing_ambiguity: bool
    conflict_values: tuple[str, ...]
    assertions: tuple[IdentityAssertion, ...]
    source_mapping_ids: tuple[str, ...]
    rule_version: str


@dataclass(frozen=True)
class EndpointScreeningAssessment:
    endpoint_id: str
    status: EndpointScreenStatus
    reason: str
    applicability_reason: EndpointApplicabilityReason | None
    evidence_pointer_ids: tuple[str, ...]
    uncertainty: tuple[SeedUncertainty, ...]


@dataclass(frozen=True)
class ScreeningDecision:
    decision_id: str
    seed_id: str
    case_revision_id: str
    disposition: DetailedDisposition
    canonical_disposition: CanonicalDisposition
    screening_outcome: ScreeningOutcome
    reason: str
    rule_version: str
    identity_resolution_id: str
    representative_seed_id: str | None
    endpoint_assessments: tuple[EndpointScreeningAssessment, ...]
    unresolved_fields: tuple[str, ...]
    provenance_mapping_ids: tuple[str, ...]
    provenance_route_ids: tuple[str, ...]


@dataclass(frozen=True)
class SeedToScreenedCandidateMapping:
    link_id: str
    seed_id: str
    screened_candidate_id: str
    role: SeedCandidateRole
    representative_seed_id: str
    source_mapping_ids: tuple[str, ...]
    discovery_route_ids: tuple[str, ...]
    alias_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScreenedCandidateRecord:
    screened_candidate_id: str
    case_id: str
    case_revision_id: str
    lane: str
    screening_intervention_id: str
    verified_normalized_intervention_id: str | None
    active_moiety_id: str | None
    identity_status: SeedIdentityStatus
    identity_verified: bool
    representative_seed_id: str
    endpoint_ids: tuple[str, ...]
    structured_routes: tuple[StructuredCausalRoute, ...]
    evidence_modalities: tuple[EvidenceModality, ...]
    chemical_universes: tuple[ChemicalUniverse, ...]
    source_seed_ids: tuple[str, ...]
    source_mapping_ids: tuple[str, ...]
    discovery_route_ids: tuple[str, ...]
    alias_ids: tuple[str, ...]


@dataclass(frozen=True)
class QuarantinedSeedRecord:
    quarantine_id: str
    seed_id: str
    disposition: DetailedDisposition
    identity_status: SeedIdentityStatus
    reason: str
    unresolved_fields: tuple[str, ...]
    source_mapping_ids: tuple[str, ...]
    discovery_route_ids: tuple[str, ...]
    alias_ids: tuple[str, ...]
    can_advance: bool


@dataclass(frozen=True)
class DispositionCount:
    disposition: DetailedDisposition
    count: int


@dataclass(frozen=True)
class FunnelReconciliation:
    retrieved_mapping_count: int
    seed_count: int
    current_disposition_count: int
    admit_count: int
    merge_count: int
    baseline_count: int
    reject_count: int
    quarantine_count: int
    failed_count: int
    screened_count: int
    screen_rejected_count: int
    screen_quarantined_count: int
    screen_failed_count: int
    not_screened_count: int
    screened_candidate_count: int
    seed_candidate_link_count: int
    unresolved_or_quarantined_seed_count: int
    detailed_disposition_counts: tuple[DispositionCount, ...]


@dataclass(frozen=True)
class SeedFunnelSnapshot:
    schema_version: int
    model_version: str
    snapshot_id: str
    case_id: str
    case_revision_id: str
    source_mappings: tuple[SeedSourceMapping, ...]
    discovery_routes: tuple[SeedDiscoveryRoute, ...]
    seeds: tuple[CandidateSeed, ...]
    aliases: tuple[SeedAlias, ...]
    identity_resolutions: tuple[IdentityResolutionRecord, ...]
    screening_decisions: tuple[ScreeningDecision, ...]
    seed_candidate_mappings: tuple[SeedToScreenedCandidateMapping, ...]
    screened_candidates: tuple[ScreenedCandidateRecord, ...]
    unresolved_or_quarantined_seeds: tuple[QuarantinedSeedRecord, ...]
    reconciliation: FunnelReconciliation


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SeedFunnelError(f"{label}: expected text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise SeedFunnelError(f"{label}: value cannot be blank")
    return normalized


def _raw_text(value: Any, label: str) -> str:
    """Validate nonblank source text while preserving its exact supplied bytes."""

    if not isinstance(value, str):
        raise SeedFunnelError(f"{label}: expected text")
    if not " ".join(unicodedata.normalize("NFKC", value).split()):
        raise SeedFunnelError(f"{label}: value cannot be blank")
    return value


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _string_tuple(values: Iterable[str], label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    normalized = tuple(sorted({_text(value, label) for value in values}))
    if not allow_empty and not normalized:
        raise SeedFunnelError(f"{label}: at least one value is required")
    return normalized


def _stable_id(prefix: str, rule_id: str, projection: Any) -> str:
    return f"{prefix}-{content_sha256({'rule_id': rule_id, 'projection': projection})[:24]}"


def alias_comparison_value(value: str) -> str:
    """Return a deterministic comparison token without asserting chemical identity."""

    return _text(value, "alias").casefold()


def _source_identity_projection(
    case_revision_id: str,
    source_id: str,
    source_release: str,
    native_record_id: str,
    assertion_locator: str,
    raw_intervention_assertion: str,
) -> dict[str, str]:
    return {
        "case_revision_id": _text(case_revision_id, "case_revision_id"),
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "native_record_id": _text(native_record_id, "native_record_id"),
        "assertion_locator": _text(assertion_locator, "assertion_locator"),
        "raw_intervention_assertion": _raw_text(
            raw_intervention_assertion, "raw_intervention_assertion"
        ),
    }


def make_source_mapping(
    case: CaseRevision,
    *,
    source_id: str,
    source_release: str,
    native_record_id: str,
    assertion_locator: str,
    raw_intervention_assertion: str,
) -> SeedSourceMapping:
    projection = _source_identity_projection(
        case.case_revision_id,
        source_id,
        source_release,
        native_record_id,
        assertion_locator,
        raw_intervention_assertion,
    )
    return SeedSourceMapping(
        mapping_id=_stable_id("MAP", MAPPING_ID_RULE, projection),
        seed_id=_stable_id("SEED", SEED_ID_RULE, projection),
        case_id=case.case_id,
        case_revision_id=case.case_revision_id,
        source_id=projection["source_id"],
        source_release=projection["source_release"],
        native_record_id=projection["native_record_id"],
        assertion_locator=projection["assertion_locator"],
        raw_intervention_assertion=projection["raw_intervention_assertion"],
    )


def make_discovery_route(
    mapping: SeedSourceMapping,
    *,
    query_id: str,
    query_record_locator: str,
    retrieval_content_receipt_id: str,
) -> SeedDiscoveryRoute:
    projection = {
        "seed_id": mapping.seed_id,
        "source_mapping_id": mapping.mapping_id,
        "query_id": _text(query_id, "query_id"),
        "query_record_locator": _text(query_record_locator, "query_record_locator"),
        "retrieval_content_receipt_id": _text(
            retrieval_content_receipt_id, "retrieval_content_receipt_id"
        ),
    }
    return SeedDiscoveryRoute(
        route_id=_stable_id("SEED-ROUTE", ROUTE_ID_RULE, projection),
        seed_id=mapping.seed_id,
        source_mapping_id=mapping.mapping_id,
        query_id=projection["query_id"],
        query_record_locator=projection["query_record_locator"],
        retrieval_content_receipt_id=projection["retrieval_content_receipt_id"],
    )


def make_compound_hint(
    kind: CompoundHintKind,
    value: str,
    *,
    namespace: str = "",
) -> CompoundHint:
    normalized_namespace = ""
    if namespace:
        normalized_namespace = _text(namespace, "compound_hint.namespace").upper()
    if kind is not CompoundHintKind.NAME_HINT and not normalized_namespace:
        raise SeedFunnelError("Identifier compound hints require a namespace")
    return CompoundHint(kind=kind, value=_text(value, "compound_hint.value"), namespace=normalized_namespace)


def unknown_development_status(reason: str) -> QualifiedValue[DevelopmentStatus]:
    return QualifiedValue(status=ValueStatus.UNKNOWN, value=None, reason=_text(reason, "reason"))


def known_development_status(value: DevelopmentStatus) -> QualifiedValue[DevelopmentStatus]:
    if value is DevelopmentStatus.UNKNOWN:
        raise SeedFunnelError("Use unknown_development_status for an unknown development status")
    return QualifiedValue(status=ValueStatus.KNOWN, value=value, reason="")


def make_candidate_seed(
    case: CaseRevision,
    mapping: SeedSourceMapping,
    *,
    endpoint_ids: Iterable[str],
    compound_hint: CompoundHint,
    discovery_route_ids: Iterable[str],
    structured_routes: Iterable[StructuredCausalRoute],
    evidence_modalities: Iterable[EvidenceModality],
    chemical_universes: Iterable[ChemicalUniverse],
    development_status_hint: QualifiedValue[DevelopmentStatus],
    identity_status: SeedIdentityStatus,
    uncertainty: Iterable[SeedUncertainty],
) -> CandidateSeed:
    return CandidateSeed(
        seed_id=mapping.seed_id,
        case_id=case.case_id,
        case_revision_id=case.case_revision_id,
        endpoint_ids=_string_tuple(endpoint_ids, "endpoint_ids", allow_empty=False),
        compound_hint=compound_hint,
        source_mapping_id=mapping.mapping_id,
        discovery_route_ids=_string_tuple(
            discovery_route_ids, "discovery_route_ids", allow_empty=False
        ),
        structured_routes=normalize_structured_routes(structured_routes),
        evidence_modalities=tuple(
            sorted(set(evidence_modalities), key=lambda value: value.value)
        ),
        chemical_universes=tuple(
            sorted(set(chemical_universes), key=lambda value: value.value)
        ),
        development_status_hint=development_status_hint,
        identity_status=identity_status,
        uncertainty=tuple(sorted(set(uncertainty), key=lambda row: row.kind.value)),
    )


def make_seed_alias(
    seed: CandidateSeed,
    *,
    alias_kind: AliasKind,
    raw_alias: str,
    assertion_status: AliasAssertionStatus,
    equivalent_seed_id: str | None = None,
    authority: str = "",
    authority_release: str = "",
) -> SeedAlias:
    raw = _raw_text(raw_alias, "raw_alias")
    equivalent = _optional_text(equivalent_seed_id, "equivalent_seed_id")
    normalized_authority = _text(authority, "authority") if authority else ""
    normalized_release = _text(authority_release, "authority_release") if authority_release else ""
    projection = {
        "seed_id": seed.seed_id,
        "alias_kind": alias_kind.value,
        "raw_alias": raw,
        "source_mapping_id": seed.source_mapping_id,
        "discovery_route_ids": seed.discovery_route_ids,
        "assertion_status": assertion_status.value,
        "equivalent_seed_id": equivalent,
        "authority": normalized_authority,
        "authority_release": normalized_release,
    }
    return SeedAlias(
        alias_id=_stable_id("SEED-ALIAS", ALIAS_ID_RULE, projection),
        seed_id=seed.seed_id,
        alias_kind=alias_kind,
        raw_alias=raw,
        comparison_value=alias_comparison_value(raw),
        source_mapping_id=seed.source_mapping_id,
        discovery_route_ids=seed.discovery_route_ids,
        assertion_status=assertion_status,
        equivalent_seed_id=equivalent,
        authority=normalized_authority,
        authority_release=normalized_release,
    )


def make_identity_assertion(
    seed: CandidateSeed,
    *,
    authority: str,
    authority_release: str,
    identifier_type: str,
    identifier: str,
    assertion_status: IdentityAssertionStatus,
    source_mapping_ids: Iterable[str] | None = None,
) -> IdentityAssertion:
    mapping_ids = _string_tuple(
        source_mapping_ids or (seed.source_mapping_id,),
        "identity_assertion.source_mapping_ids",
        allow_empty=False,
    )
    projection = {
        "seed_id": seed.seed_id,
        "authority": _text(authority, "authority"),
        "authority_release": _text(authority_release, "authority_release"),
        "identifier_type": _text(identifier_type, "identifier_type"),
        "identifier": _text(identifier, "identifier"),
        "assertion_status": assertion_status.value,
        "source_mapping_ids": mapping_ids,
    }
    return IdentityAssertion(
        assertion_id=_stable_id("IDENTITY-ASSERTION", IDENTITY_ASSERTION_ID_RULE, projection),
        seed_id=seed.seed_id,
        authority=projection["authority"],
        authority_release=projection["authority_release"],
        identifier_type=projection["identifier_type"],
        identifier=projection["identifier"],
        assertion_status=assertion_status,
        source_mapping_ids=mapping_ids,
    )


def provisional_screening_intervention_id(seed: CandidateSeed) -> str:
    """Create a seed-scoped provisional key without asserting verified identity."""

    return _stable_id(
        "PROVISIONAL-INTERVENTION",
        "schema-v7-provisional-screening-intervention-id-v1",
        {
            "seed_id": seed.seed_id,
            "hint_kind": seed.compound_hint.kind.value,
            "hint_namespace": seed.compound_hint.namespace,
            "hint_value": seed.compound_hint.value,
        },
    )


def _verified_identity_assertion_projection(
    assertions: Iterable[IdentityAssertion], *, active_moiety: bool
) -> tuple[dict[str, Any], ...]:
    selected: list[dict[str, Any]] = []
    for assertion in assertions:
        type_token = "_".join(assertion.identifier_type.casefold().replace("-", " ").split())
        is_active_moiety = type_token in {"active_moiety", "active_moiety_id"}
        if (
            assertion.assertion_status is IdentityAssertionStatus.VERIFIED
            and is_active_moiety is active_moiety
        ):
            selected.append(
                {
                    "authority": assertion.authority,
                    "authority_release": assertion.authority_release,
                    "identifier_type": assertion.identifier_type,
                    "identifier": assertion.identifier,
                }
            )
    return tuple(sorted(selected, key=canonical_bytes))


def verified_normalized_intervention_id(
    assertions: Iterable[IdentityAssertion],
) -> str:
    """Derive an internal normalized-intervention ID from verified assertions."""

    projection = _verified_identity_assertion_projection(assertions, active_moiety=False)
    if not projection:
        raise SeedFunnelError("A resolved intervention requires a verified identity assertion")
    return _stable_id(
        "NORMALIZED-INTERVENTION", NORMALIZED_INTERVENTION_ID_RULE, projection
    )


def verified_active_moiety_id(assertions: Iterable[IdentityAssertion]) -> str:
    """Derive an optional active-moiety ID only from explicit verified assertions."""

    projection = _verified_identity_assertion_projection(assertions, active_moiety=True)
    if not projection:
        raise SeedFunnelError("An active-moiety ID requires a verified active-moiety assertion")
    return _stable_id("ACTIVE-MOIETY", ACTIVE_MOIETY_ID_RULE, projection)


def make_identity_resolution(
    seed: CandidateSeed,
    *,
    status: SeedIdentityStatus,
    screening_intervention_id: str | None = None,
    verified_normalized_intervention_id: str | None = None,
    active_moiety_id: str | None = None,
    identity_verified: bool = False,
    decision_changing_ambiguity: bool = False,
    conflict_values: Iterable[str] = (),
    assertions: Iterable[IdentityAssertion] = (),
    source_mapping_ids: Iterable[str] | None = None,
    rule_version: str = "schema-v7-lightweight-identity-v1",
) -> IdentityResolutionRecord:
    normalized_assertions = tuple(sorted(set(assertions), key=lambda row: row.assertion_id))
    mapping_ids = _string_tuple(
        source_mapping_ids or (seed.source_mapping_id,),
        "identity_resolution.source_mapping_ids",
        allow_empty=False,
    )
    body = {
        "seed_id": seed.seed_id,
        "status": status,
        "screening_intervention_id": _optional_text(
            screening_intervention_id, "screening_intervention_id"
        ),
        "verified_normalized_intervention_id": _optional_text(
            verified_normalized_intervention_id, "verified_normalized_intervention_id"
        ),
        "active_moiety_id": _optional_text(active_moiety_id, "active_moiety_id"),
        "identity_verified": identity_verified,
        "decision_changing_ambiguity": decision_changing_ambiguity,
        "conflict_values": _string_tuple(conflict_values, "conflict_values"),
        "assertions": normalized_assertions,
        "source_mapping_ids": mapping_ids,
        "rule_version": _text(rule_version, "rule_version"),
    }
    return IdentityResolutionRecord(
        identity_resolution_id=_stable_id(
            "IDENTITY-RESOLUTION", IDENTITY_RESOLUTION_ID_RULE, body
        ),
        **body,
    )


def make_endpoint_assessment(
    endpoint_id: str,
    status: EndpointScreenStatus,
    *,
    reason: str,
    applicability_reason: EndpointApplicabilityReason | None = None,
    evidence_pointer_ids: Iterable[str] = (),
    uncertainty: Iterable[SeedUncertainty] = (),
) -> EndpointScreeningAssessment:
    return EndpointScreeningAssessment(
        endpoint_id=_text(endpoint_id, "endpoint_id"),
        status=status,
        reason=_text(reason, "endpoint assessment reason"),
        applicability_reason=applicability_reason,
        evidence_pointer_ids=_string_tuple(evidence_pointer_ids, "evidence_pointer_ids"),
        uncertainty=tuple(sorted(set(uncertainty), key=lambda row: row.kind.value)),
    )


def make_screening_decision(
    seed: CandidateSeed,
    identity: IdentityResolutionRecord,
    *,
    disposition: DetailedDisposition,
    reason: str,
    endpoint_assessments: Iterable[EndpointScreeningAssessment] = (),
    representative_seed_id: str | None = None,
    unresolved_fields: Iterable[str] = (),
    rule_version: str = "schema-v7-lightweight-screen-v1",
) -> ScreeningDecision:
    assessments = tuple(sorted(set(endpoint_assessments), key=lambda row: row.endpoint_id))
    body = {
        "seed_id": seed.seed_id,
        "case_revision_id": seed.case_revision_id,
        "disposition": disposition,
        "canonical_disposition": DETAILED_TO_CANONICAL[disposition],
        "screening_outcome": DETAILED_TO_SCREENING_OUTCOME[disposition],
        "reason": _text(reason, "screening reason"),
        "rule_version": _text(rule_version, "rule_version"),
        "identity_resolution_id": identity.identity_resolution_id,
        "representative_seed_id": _optional_text(
            representative_seed_id, "representative_seed_id"
        ),
        "endpoint_assessments": assessments,
        "unresolved_fields": _string_tuple(unresolved_fields, "unresolved_fields"),
        "provenance_mapping_ids": (seed.source_mapping_id,),
        "provenance_route_ids": seed.discovery_route_ids,
    }
    return ScreeningDecision(
        decision_id=_stable_id("SCREEN", SCREENING_DECISION_ID_RULE, body),
        **body,
    )


def _record_projection(record: Any, id_field: str) -> dict[str, Any]:
    return {
        field.name: getattr(record, field.name)
        for field in fields(record)
        if field.name != id_field
    }


def _reduce_records(
    records: Iterable[Any], expected_type: type[Any], id_field: str, label: str
) -> tuple[Any, ...]:
    reduced: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, expected_type):
            raise SeedFunnelError(f"{label}: expected {expected_type.__name__}")
        record_id = _text(getattr(record, id_field), f"{label}.{id_field}")
        prior = reduced.get(record_id)
        if prior is not None and canonical_bytes(prior) != canonical_bytes(record):
            raise SeedFunnelError(f"{label}: idempotency conflict for {record_id}")
        reduced[record_id] = record
    return tuple(reduced[key] for key in sorted(reduced))


def _expected_source_ids(mapping: SeedSourceMapping) -> tuple[str, str]:
    projection = _source_identity_projection(
        mapping.case_revision_id,
        mapping.source_id,
        mapping.source_release,
        mapping.native_record_id,
        mapping.assertion_locator,
        mapping.raw_intervention_assertion,
    )
    return (
        _stable_id("MAP", MAPPING_ID_RULE, projection),
        _stable_id("SEED", SEED_ID_RULE, projection),
    )


def _validate_development_hint(value: QualifiedValue[DevelopmentStatus], label: str) -> None:
    if not isinstance(value, QualifiedValue) or not isinstance(value.status, ValueStatus):
        raise SeedFunnelError(f"{label}: malformed development-status hint")
    if value.status is ValueStatus.KNOWN:
        if not isinstance(value.value, DevelopmentStatus) or value.value is DevelopmentStatus.UNKNOWN:
            raise SeedFunnelError(f"{label}: known development status is invalid")
        if value.reason:
            raise SeedFunnelError(f"{label}: known development status cannot include a reason")
    elif value.status is ValueStatus.UNKNOWN:
        if value.value is not None or not str(value.reason).strip():
            raise SeedFunnelError(f"{label}: unknown development status requires a reason")
    else:
        raise SeedFunnelError(f"{label}: development status cannot be not_applicable")


def _validate_uncertainty(values: tuple[SeedUncertainty, ...], label: str) -> None:
    if not values:
        raise SeedFunnelError(f"{label}: explicit uncertainty is required")
    if tuple(sorted(values, key=lambda row: row.kind.value)) != values:
        raise SeedFunnelError(f"{label}: uncertainty must be canonically ordered")
    kinds: set[UncertaintyKind] = set()
    for row in values:
        if not isinstance(row, SeedUncertainty):
            raise SeedFunnelError(f"{label}: invalid uncertainty record")
        if row.kind in kinds:
            raise SeedFunnelError(f"{label}: duplicate uncertainty kind {row.kind.value}")
        kinds.add(row.kind)
        if not isinstance(row.level, UncertaintyLevel):
            raise SeedFunnelError(f"{label}: invalid uncertainty level")
        _text(row.note, f"{label}.{row.kind.value}")


def _validate_identity_record(
    record: IdentityResolutionRecord,
    seed: CandidateSeed,
    mappings: dict[str, SeedSourceMapping],
) -> None:
    label = f"identity {record.identity_resolution_id}"
    if not isinstance(record.status, SeedIdentityStatus):
        raise SeedFunnelError(f"{label}: invalid identity status")
    if record.seed_id != seed.seed_id or record.status is not seed.identity_status:
        raise SeedFunnelError(f"{label}: seed identity status/link mismatch")
    if seed.source_mapping_id not in record.source_mapping_ids:
        raise SeedFunnelError(f"{label}: source mapping provenance was dropped")
    if tuple(sorted(set(record.source_mapping_ids))) != record.source_mapping_ids:
        raise SeedFunnelError(f"{label}: source mapping IDs must be unique and ordered")
    if any(mapping_id not in mappings for mapping_id in record.source_mapping_ids):
        raise SeedFunnelError(f"{label}: unknown source mapping")
    if tuple(sorted(record.assertions, key=lambda row: row.assertion_id)) != record.assertions:
        raise SeedFunnelError(f"{label}: assertions must be canonically ordered")
    if len({row.assertion_id for row in record.assertions}) != len(record.assertions):
        raise SeedFunnelError(f"{label}: duplicate identity assertions")
    for assertion in record.assertions:
        if not isinstance(assertion, IdentityAssertion) or not isinstance(
            assertion.assertion_status, IdentityAssertionStatus
        ):
            raise SeedFunnelError(f"{label}: invalid identity assertion")
        body = _record_projection(assertion, "assertion_id")
        expected_id = _stable_id("IDENTITY-ASSERTION", IDENTITY_ASSERTION_ID_RULE, body)
        if assertion.assertion_id != expected_id or assertion.seed_id != seed.seed_id:
            raise SeedFunnelError(f"{label}: malformed identity assertion")
        if any(mapping_id not in mappings for mapping_id in assertion.source_mapping_ids):
            raise SeedFunnelError(f"{label}: assertion references an unknown source mapping")
        for field_name in ("authority", "authority_release", "identifier_type", "identifier"):
            value = getattr(assertion, field_name)
            if _text(value, f"{label}.{field_name}") != value:
                raise SeedFunnelError(f"{label}: identity assertions must be canonically normalized")

    if any(
        assertion.assertion_status is IdentityAssertionStatus.CONFLICTING
        for assertion in record.assertions
    ) and record.status is not SeedIdentityStatus.CONFLICTING:
        raise SeedFunnelError(
            f"{label}: conflicting identity assertions require conflicting status"
        )

    screening_id = record.screening_intervention_id
    normalized_id = record.verified_normalized_intervention_id
    if record.status is SeedIdentityStatus.RESOLVED:
        try:
            expected_normalized_id = verified_normalized_intervention_id(record.assertions)
        except SeedFunnelError as exc:
            raise SeedFunnelError(f"{label}: {exc}") from exc
        if (
            not record.identity_verified
            or not screening_id
            or not normalized_id
            or screening_id != normalized_id
            or normalized_id != expected_normalized_id
            or record.decision_changing_ambiguity
            or record.conflict_values
            or not any(
                row.assertion_status is IdentityAssertionStatus.VERIFIED
                for row in record.assertions
            )
        ):
            raise SeedFunnelError(f"{label}: resolved identity lacks verified, unambiguous support")
        if record.active_moiety_id is not None:
            try:
                expected_active_moiety_id = verified_active_moiety_id(record.assertions)
            except SeedFunnelError as exc:
                raise SeedFunnelError(f"{label}: {exc}") from exc
            if record.active_moiety_id != expected_active_moiety_id:
                raise SeedFunnelError(f"{label}: active-moiety ID lacks matching verified support")
    elif record.status is SeedIdentityStatus.PROVISIONAL:
        if (
            record.identity_verified
            or not screening_id
            or screening_id != provisional_screening_intervention_id(seed)
            or seed.compound_hint.kind is CompoundHintKind.NAME_HINT
            or normalized_id is not None
            or record.active_moiety_id is not None
            or record.decision_changing_ambiguity
            or record.conflict_values
        ):
            raise SeedFunnelError(f"{label}: provisional identity is overstated")
    else:
        if screening_id is not None or normalized_id is not None or record.active_moiety_id is not None:
            raise SeedFunnelError(f"{label}: unresolved identity cannot carry resolved identity IDs")
        if record.identity_verified:
            raise SeedFunnelError(f"{label}: unresolved identity cannot be verified")
    if record.status is SeedIdentityStatus.CONFLICTING:
        if not record.decision_changing_ambiguity or not record.conflict_values:
            raise SeedFunnelError(f"{label}: conflicting identity requires explicit conflicts")
    if record.status is SeedIdentityStatus.UNRESOLVED and not record.decision_changing_ambiguity:
        raise SeedFunnelError(f"{label}: unresolved identity must remain decision-changing")
    expected_id = _stable_id(
        "IDENTITY-RESOLUTION",
        IDENTITY_RESOLUTION_ID_RULE,
        _record_projection(record, "identity_resolution_id"),
    )
    if record.identity_resolution_id != expected_id:
        raise SeedFunnelError(f"{label}: content-derived ID mismatch")


def _candidate_id(case_revision_id: str, screening_intervention_id: str) -> str:
    return _stable_id(
        "SCREENED-CANDIDATE",
        CANDIDATE_ID_RULE,
        {
            "case_revision_id": case_revision_id,
            "screening_intervention_id": screening_intervention_id,
            "lane": "repurposing",
        },
    )


def _candidate_link_id(seed_id: str, candidate_id: str, role: SeedCandidateRole) -> str:
    return _stable_id(
        "SEED-CANDIDATE-LINK",
        SEED_CANDIDATE_LINK_ID_RULE,
        {"seed_id": seed_id, "candidate_id": candidate_id, "role": role.value},
    )


def _validate_endpoint_assessments(
    case: CaseRevision,
    decision: ScreeningDecision,
    *,
    admitted: bool,
) -> None:
    valid = {endpoint.endpoint_id: endpoint for endpoint in case.endpoints}
    assessment_ids = [row.endpoint_id for row in decision.endpoint_assessments]
    if assessment_ids != sorted(set(assessment_ids)):
        raise SeedFunnelError(f"decision {decision.decision_id}: endpoint assessments are not canonical")
    if any(endpoint_id not in valid for endpoint_id in assessment_ids):
        raise SeedFunnelError(f"decision {decision.decision_id}: unknown endpoint assessment")
    if admitted and set(assessment_ids) != set(valid):
        raise SeedFunnelError(
            f"decision {decision.decision_id}: an admitted representative must assess every case endpoint"
        )
    for row in decision.endpoint_assessments:
        if not isinstance(row, EndpointScreeningAssessment) or not isinstance(
            row.status, EndpointScreenStatus
        ):
            raise SeedFunnelError(f"decision {decision.decision_id}: invalid endpoint assessment")
        _text(row.reason, f"decision {decision.decision_id} endpoint reason")
        if row.status is EndpointScreenStatus.NOT_APPLICABLE:
            if not isinstance(row.applicability_reason, EndpointApplicabilityReason):
                raise SeedFunnelError(
                    f"decision {decision.decision_id}: not_applicable needs a typed rationale"
                )
        elif row.applicability_reason is not None:
            raise SeedFunnelError(
                f"decision {decision.decision_id}: applicability rationale is only for not_applicable"
            )
        if tuple(sorted(set(row.evidence_pointer_ids))) != row.evidence_pointer_ids:
            raise SeedFunnelError(
                f"decision {decision.decision_id}: evidence pointers must be unique and ordered"
            )
        _validate_uncertainty(row.uncertainty, f"decision {decision.decision_id} endpoint")
        endpoint = valid[row.endpoint_id]
        if (
            admitted
            and endpoint.required.status is ValueStatus.KNOWN
            and endpoint.required.value is True
            and row.status is EndpointScreenStatus.NOT_ASSESSED
        ):
            raise SeedFunnelError(
                f"decision {decision.decision_id}: required endpoint cannot be not_assessed"
            )


def _validate_records(
    case: CaseRevision,
    source_mappings: tuple[SeedSourceMapping, ...],
    discovery_routes: tuple[SeedDiscoveryRoute, ...],
    seeds: tuple[CandidateSeed, ...],
    aliases: tuple[SeedAlias, ...],
    identity_resolutions: tuple[IdentityResolutionRecord, ...],
    screening_decisions: tuple[ScreeningDecision, ...],
) -> tuple[
    dict[str, SeedSourceMapping],
    dict[str, SeedDiscoveryRoute],
    dict[str, CandidateSeed],
    dict[str, list[SeedAlias]],
    dict[str, IdentityResolutionRecord],
    dict[str, ScreeningDecision],
]:
    mappings = {row.mapping_id: row for row in source_mappings}
    routes = {row.route_id: row for row in discovery_routes}
    seeds_by_id = {row.seed_id: row for row in seeds}
    aliases_by_seed: dict[str, list[SeedAlias]] = defaultdict(list)
    identity_counts = Counter(row.seed_id for row in identity_resolutions)
    decision_counts = Counter(row.seed_id for row in screening_decisions)
    identities = {row.seed_id: row for row in identity_resolutions}
    decisions = {row.seed_id: row for row in screening_decisions}
    valid_endpoint_ids = {endpoint.endpoint_id for endpoint in case.endpoints}

    for mapping in source_mappings:
        if mapping.case_id != case.case_id or mapping.case_revision_id != case.case_revision_id:
            raise SeedFunnelError(f"mapping {mapping.mapping_id}: case link mismatch")
        expected_mapping_id, expected_seed_id = _expected_source_ids(mapping)
        if mapping.mapping_id != expected_mapping_id or mapping.seed_id != expected_seed_id:
            raise SeedFunnelError(f"mapping {mapping.mapping_id}: content-derived identity mismatch")
        for field_name in (
            "source_id",
            "source_release",
            "native_record_id",
            "assertion_locator",
            "raw_intervention_assertion",
        ):
            _text(getattr(mapping, field_name), f"mapping {mapping.mapping_id}.{field_name}")

    for route in discovery_routes:
        mapping = mappings.get(route.source_mapping_id)
        if mapping is None or mapping.seed_id != route.seed_id:
            raise SeedFunnelError(f"route {route.route_id}: mapping/seed link mismatch")
        expected = _stable_id(
            "SEED-ROUTE", ROUTE_ID_RULE, _record_projection(route, "route_id")
        )
        if route.route_id != expected:
            raise SeedFunnelError(f"route {route.route_id}: content-derived ID mismatch")
        for field_name in ("query_id", "query_record_locator", "retrieval_content_receipt_id"):
            _text(getattr(route, field_name), f"route {route.route_id}.{field_name}")

    route_ids_by_seed: dict[str, set[str]] = defaultdict(set)
    for route in discovery_routes:
        route_ids_by_seed[route.seed_id].add(route.route_id)
    mapping_seed_ids = [row.seed_id for row in source_mappings]
    if len(mapping_seed_ids) != len(set(mapping_seed_ids)):
        raise SeedFunnelError("Each source assertion mapping must identify one distinct seed")
    if set(mapping_seed_ids) != set(seeds_by_id):
        raise SeedFunnelError("Every retrieved mapping must reconcile to exactly one seed")
    for seed in seeds:
        mapping = mappings.get(seed.source_mapping_id)
        if mapping is None or mapping.seed_id != seed.seed_id:
            raise SeedFunnelError(f"seed {seed.seed_id}: source mapping link mismatch")
        if seed.case_id != case.case_id or seed.case_revision_id != case.case_revision_id:
            raise SeedFunnelError(f"seed {seed.seed_id}: case link mismatch")
        if tuple(sorted(set(seed.endpoint_ids))) != seed.endpoint_ids or not seed.endpoint_ids:
            raise SeedFunnelError(f"seed {seed.seed_id}: endpoint IDs must be nonempty and canonical")
        if not set(seed.endpoint_ids).issubset(valid_endpoint_ids):
            raise SeedFunnelError(f"seed {seed.seed_id}: unknown endpoint link")
        if not seed.discovery_route_ids or set(seed.discovery_route_ids) != route_ids_by_seed.get(
            seed.seed_id, set()
        ):
            raise SeedFunnelError(f"seed {seed.seed_id}: discovery-route provenance is incomplete")
        if not isinstance(seed.compound_hint, CompoundHint) or not isinstance(
            seed.compound_hint.kind, CompoundHintKind
        ):
            raise SeedFunnelError(f"seed {seed.seed_id}: invalid compound hint")
        if seed.compound_hint.kind is not CompoundHintKind.NAME_HINT and not seed.compound_hint.namespace:
            raise SeedFunnelError(f"seed {seed.seed_id}: identifier hint lacks a namespace")
        _text(seed.compound_hint.value, f"seed {seed.seed_id}.compound_hint")
        if not seed.structured_routes:
            raise SeedFunnelError(f"seed {seed.seed_id}: at least one structural route is required")
        normalized_routes = normalize_structured_routes(seed.structured_routes)
        if normalized_routes != seed.structured_routes:
            raise SeedFunnelError(
                f"seed {seed.seed_id}: structural routes must be deduplicated and ordered"
            )
        for route in seed.structured_routes:
            validate_structured_route(route)
            if route.case_revision_id != seed.case_revision_id:
                raise SeedFunnelError(f"seed {seed.seed_id}: structural route case mismatch")
            if route.intervention_id != seed.seed_id:
                raise SeedFunnelError(
                    f"seed {seed.seed_id}: seed-stage structural route must use the seed ID"
                )
            if route.endpoint_id not in seed.endpoint_ids:
                raise SeedFunnelError(f"seed {seed.seed_id}: structural route endpoint mismatch")
        if (
            not seed.evidence_modalities
            or tuple(sorted(set(seed.evidence_modalities), key=lambda value: value.value))
            != seed.evidence_modalities
            or any(not isinstance(value, EvidenceModality) for value in seed.evidence_modalities)
        ):
            raise SeedFunnelError(
                f"seed {seed.seed_id}: evidence modalities must be nonempty, unique, and ordered"
            )
        if (
            not seed.chemical_universes
            or tuple(sorted(set(seed.chemical_universes), key=lambda value: value.value))
            != seed.chemical_universes
            or any(not isinstance(value, ChemicalUniverse) for value in seed.chemical_universes)
        ):
            raise SeedFunnelError(
                f"seed {seed.seed_id}: chemical universes must be nonempty, unique, and ordered"
            )
        if not isinstance(seed.identity_status, SeedIdentityStatus):
            raise SeedFunnelError(f"seed {seed.seed_id}: invalid identity status")
        _validate_development_hint(
            seed.development_status_hint, f"seed {seed.seed_id}.development_status_hint"
        )
        _validate_uncertainty(seed.uncertainty, f"seed {seed.seed_id}.uncertainty")

    for alias in aliases:
        if not isinstance(alias.alias_kind, AliasKind) or not isinstance(
            alias.assertion_status, AliasAssertionStatus
        ):
            raise SeedFunnelError(f"alias {alias.alias_id}: invalid controlled value")
        seed = seeds_by_id.get(alias.seed_id)
        if seed is None:
            raise SeedFunnelError(f"alias {alias.alias_id}: unknown seed")
        if alias.source_mapping_id != seed.source_mapping_id:
            raise SeedFunnelError(f"alias {alias.alias_id}: source mapping mismatch")
        if set(alias.discovery_route_ids) != set(seed.discovery_route_ids):
            raise SeedFunnelError(f"alias {alias.alias_id}: discovery provenance was dropped")
        if alias.comparison_value != alias_comparison_value(alias.raw_alias):
            raise SeedFunnelError(f"alias {alias.alias_id}: comparison value mismatch")
        if alias.equivalent_seed_id is not None:
            if alias.equivalent_seed_id == alias.seed_id or alias.equivalent_seed_id not in seeds_by_id:
                raise SeedFunnelError(f"alias {alias.alias_id}: invalid equivalent seed")
        if alias.assertion_status is AliasAssertionStatus.VERIFIED:
            if not alias.equivalent_seed_id or not alias.authority or not alias.authority_release:
                raise SeedFunnelError(
                    f"alias {alias.alias_id}: verified equivalence needs authority and release"
                )
            if (
                _text(alias.authority, f"alias {alias.alias_id}.authority") != alias.authority
                or _text(alias.authority_release, f"alias {alias.alias_id}.authority_release")
                != alias.authority_release
            ):
                raise SeedFunnelError(
                    f"alias {alias.alias_id}: authority and release must be canonically normalized"
                )
        expected = _stable_id("SEED-ALIAS", ALIAS_ID_RULE, {
            "seed_id": alias.seed_id,
            "alias_kind": alias.alias_kind.value,
            "raw_alias": alias.raw_alias,
            "source_mapping_id": alias.source_mapping_id,
            "discovery_route_ids": alias.discovery_route_ids,
            "assertion_status": alias.assertion_status.value,
            "equivalent_seed_id": alias.equivalent_seed_id,
            "authority": alias.authority,
            "authority_release": alias.authority_release,
        })
        if alias.alias_id != expected:
            raise SeedFunnelError(f"alias {alias.alias_id}: content-derived ID mismatch")
        aliases_by_seed[alias.seed_id].append(alias)

    if set(identities) != set(seeds_by_id) or any(
        count != 1 for count in identity_counts.values()
    ):
        raise SeedFunnelError("Every seed must have exactly one identity-resolution record")
    for seed_id, identity in identities.items():
        _validate_identity_record(identity, seeds_by_id[seed_id], mappings)

    if set(decisions) != set(seeds_by_id) or any(
        count != 1 for count in decision_counts.values()
    ):
        raise SeedFunnelError("Every seed must have exactly one current disposition")
    for seed_id, decision in decisions.items():
        seed = seeds_by_id[seed_id]
        identity = identities[seed_id]
        label = f"decision {decision.decision_id}"
        if decision.case_revision_id != case.case_revision_id:
            raise SeedFunnelError(f"{label}: case revision mismatch")
        if decision.identity_resolution_id != identity.identity_resolution_id:
            raise SeedFunnelError(f"{label}: identity-resolution link mismatch")
        if (
            not isinstance(decision.disposition, DetailedDisposition)
            or not isinstance(decision.canonical_disposition, CanonicalDisposition)
            or not isinstance(decision.screening_outcome, ScreeningOutcome)
        ):
            raise SeedFunnelError(f"{label}: invalid controlled disposition")
        if decision.canonical_disposition is not DETAILED_TO_CANONICAL[decision.disposition]:
            raise SeedFunnelError(f"{label}: canonical disposition mismatch")
        if decision.screening_outcome is not DETAILED_TO_SCREENING_OUTCOME[decision.disposition]:
            raise SeedFunnelError(f"{label}: screening outcome mismatch")
        if decision.provenance_mapping_ids != (seed.source_mapping_id,):
            raise SeedFunnelError(f"{label}: source mapping provenance was dropped")
        if set(decision.provenance_route_ids) != set(seed.discovery_route_ids):
            raise SeedFunnelError(f"{label}: discovery-route provenance was dropped")
        _text(decision.reason, f"{label}.reason")
        _text(decision.rule_version, f"{label}.rule_version")
        admitted = decision.canonical_disposition is CanonicalDisposition.ADMIT
        _validate_endpoint_assessments(
            case,
            decision,
            admitted=(
                admitted and decision.screening_outcome is not ScreeningOutcome.SCREEN_FAILED
            ),
        )
        if (
            identity.decision_changing_ambiguity
            and decision.canonical_disposition is not CanonicalDisposition.QUARANTINE
        ):
            raise SeedFunnelError(
                f"{label}: decision-changing identity ambiguity must remain quarantined"
            )
        if identity.status in {
            SeedIdentityStatus.CONFLICTING,
            SeedIdentityStatus.UNRESOLVED,
            SeedIdentityStatus.QUARANTINED,
        } and decision.canonical_disposition is not CanonicalDisposition.QUARANTINE:
            raise SeedFunnelError(
                f"{label}: decision-changing identity ambiguity must remain quarantined"
            )
        if admitted:
            if decision.representative_seed_id is not None:
                raise SeedFunnelError(f"{label}: admitted representative cannot point to another seed")
            if identity.status not in {SeedIdentityStatus.PROVISIONAL, SeedIdentityStatus.RESOLVED}:
                raise SeedFunnelError(f"{label}: admitted seed lacks one resolved use identity")
            if identity.decision_changing_ambiguity or not identity.screening_intervention_id:
                raise SeedFunnelError(f"{label}: decision-changing identity ambiguity cannot advance")
        elif decision.disposition in {
            DetailedDisposition.DUPLICATE_ALIAS,
            DetailedDisposition.DUPLICATE_FORMULATION,
        }:
            representative = decision.representative_seed_id
            if not representative or representative == seed_id or representative not in seeds_by_id:
                raise SeedFunnelError(f"{label}: merge requires a valid representative seed")
            if representative != min(seed_id, representative):
                raise SeedFunnelError(f"{label}: merge representative is not deterministic")
            expected_kind = (
                None
                if decision.disposition is DetailedDisposition.DUPLICATE_ALIAS
                else AliasKind.FORMULATION
            )
            verified_links = [
                row
                for row in aliases_by_seed.get(seed_id, [])
                if row.assertion_status is AliasAssertionStatus.VERIFIED
                and row.equivalent_seed_id == representative
                and (expected_kind is None or row.alias_kind is expected_kind)
            ]
            if not verified_links:
                raise SeedFunnelError(
                    f"{label}: duplicate disposition requires an explicit verified equivalence"
                )
        elif decision.representative_seed_id is not None:
            raise SeedFunnelError(f"{label}: non-merge disposition cannot name a representative")
        if decision.canonical_disposition is CanonicalDisposition.BASELINE and identity.status not in {
            SeedIdentityStatus.PROVISIONAL,
            SeedIdentityStatus.RESOLVED,
        }:
            raise SeedFunnelError(f"{label}: baseline representative lacks one resolved use identity")
        if decision.disposition is DetailedDisposition.IDENTITY_UNRESOLVED and identity.status not in {
            SeedIdentityStatus.NAME_HINT_ONLY,
            SeedIdentityStatus.CONFLICTING,
            SeedIdentityStatus.UNRESOLVED,
            SeedIdentityStatus.QUARANTINED,
            SeedIdentityStatus.UNASSESSED,
        }:
            raise SeedFunnelError(f"{label}: identity_unresolved conflicts with identity status")
        if decision.canonical_disposition is CanonicalDisposition.MERGE and identity.status not in {
            SeedIdentityStatus.PROVISIONAL,
            SeedIdentityStatus.RESOLVED,
        }:
            raise SeedFunnelError(f"{label}: a merged seed must have one resolved use identity")
        if (
            decision.disposition is DetailedDisposition.RETAINED_FOR_IDENTITY_RESOLUTION
            and identity.status is SeedIdentityStatus.RESOLVED
        ):
            raise SeedFunnelError(f"{label}: resolved identity cannot remain pending identity resolution")
        expected = _stable_id(
            "SCREEN", SCREENING_DECISION_ID_RULE, _record_projection(decision, "decision_id")
        )
        if decision.decision_id != expected:
            raise SeedFunnelError(f"{label}: content-derived ID mismatch")

    for decision in decisions.values():
        if decision.canonical_disposition is CanonicalDisposition.MERGE:
            representative = decisions[decision.representative_seed_id or ""]
            if representative.canonical_disposition not in {
                CanonicalDisposition.ADMIT,
                CanonicalDisposition.BASELINE,
            }:
                raise SeedFunnelError(
                    f"decision {decision.decision_id}: merge must terminate at an admitted or baseline representative"
                )

    for alias in aliases:
        if alias.assertion_status is not AliasAssertionStatus.VERIFIED:
            continue
        representative = alias.equivalent_seed_id or ""
        if representative != min(alias.seed_id, representative):
            raise SeedFunnelError(
                f"alias {alias.alias_id}: verified equivalence does not use the deterministic representative"
            )
        expected_disposition = (
            DetailedDisposition.DUPLICATE_FORMULATION
            if alias.alias_kind is AliasKind.FORMULATION
            else DetailedDisposition.DUPLICATE_ALIAS
        )
        alias_decision = decisions[alias.seed_id]
        if (
            alias_decision.disposition is not expected_disposition
            or alias_decision.representative_seed_id != representative
        ):
            raise SeedFunnelError(
                f"alias {alias.alias_id}: verified equivalence must use the matching merge disposition"
            )

    admitted_identity_owners: dict[str, str] = {}
    for seed_id, decision in sorted(decisions.items()):
        if decision.canonical_disposition is not CanonicalDisposition.ADMIT:
            continue
        intervention_id = identities[seed_id].screening_intervention_id or ""
        prior = admitted_identity_owners.get(intervention_id)
        if prior is not None and prior != seed_id:
            raise SeedFunnelError(
                "Multiple admitted representatives share one screening identity; use an explicit merge"
            )
        admitted_identity_owners[intervention_id] = seed_id

    return mappings, routes, seeds_by_id, aliases_by_seed, identities, decisions


def _derive_candidate_records(
    case: CaseRevision,
    mappings: dict[str, SeedSourceMapping],
    routes: dict[str, SeedDiscoveryRoute],
    seeds: dict[str, CandidateSeed],
    aliases_by_seed: dict[str, list[SeedAlias]],
    identities: dict[str, IdentityResolutionRecord],
    decisions: dict[str, ScreeningDecision],
) -> tuple[tuple[SeedToScreenedCandidateMapping, ...], tuple[ScreenedCandidateRecord, ...]]:
    contributors: dict[str, list[tuple[str, SeedCandidateRole]]] = defaultdict(list)
    for seed_id, decision in decisions.items():
        if decision.screening_outcome is ScreeningOutcome.SCREENED:
            contributors[seed_id].append((seed_id, SeedCandidateRole.REPRESENTATIVE))
        elif (
            decision.canonical_disposition is CanonicalDisposition.MERGE
            and decisions[decision.representative_seed_id or ""].screening_outcome
            is ScreeningOutcome.SCREENED
        ):
            role = (
                SeedCandidateRole.DUPLICATE_ALIAS
                if decision.disposition is DetailedDisposition.DUPLICATE_ALIAS
                else SeedCandidateRole.DUPLICATE_FORMULATION
            )
            contributors[decision.representative_seed_id or ""].append((seed_id, role))

    links: list[SeedToScreenedCandidateMapping] = []
    candidates: list[ScreenedCandidateRecord] = []
    candidate_owners: dict[str, str] = {}
    for representative_seed_id in sorted(contributors):
        representative_identity = identities[representative_seed_id]
        screening_intervention_id = representative_identity.screening_intervention_id
        if screening_intervention_id is None:
            raise SeedFunnelError("Admitted representative lacks a screening intervention ID")
        candidate_id = _candidate_id(case.case_revision_id, screening_intervention_id)
        prior_owner = candidate_owners.get(candidate_id)
        if prior_owner is not None and prior_owner != representative_seed_id:
            raise SeedFunnelError(
                "Multiple admitted representatives share one screening identity; use an explicit merge"
            )
        candidate_owners[candidate_id] = representative_seed_id
        member_rows = sorted(contributors[representative_seed_id], key=lambda row: row[0])
        seed_ids = tuple(row[0] for row in member_rows)
        mapping_ids = tuple(sorted(seeds[seed_id].source_mapping_id for seed_id in seed_ids))
        route_ids = tuple(
            sorted(
                route_id
                for seed_id in seed_ids
                for route_id in seeds[seed_id].discovery_route_ids
            )
        )
        alias_ids = tuple(
            sorted(alias.alias_id for seed_id in seed_ids for alias in aliases_by_seed.get(seed_id, []))
        )
        for seed_id, role in member_rows:
            seed_alias_ids = tuple(
                sorted(alias.alias_id for alias in aliases_by_seed.get(seed_id, []))
            )
            links.append(
                SeedToScreenedCandidateMapping(
                    link_id=_candidate_link_id(seed_id, candidate_id, role),
                    seed_id=seed_id,
                    screened_candidate_id=candidate_id,
                    role=role,
                    representative_seed_id=representative_seed_id,
                    source_mapping_ids=(seeds[seed_id].source_mapping_id,),
                    discovery_route_ids=seeds[seed_id].discovery_route_ids,
                    alias_ids=seed_alias_ids,
                )
            )
        candidates.append(
            ScreenedCandidateRecord(
                screened_candidate_id=candidate_id,
                case_id=case.case_id,
                case_revision_id=case.case_revision_id,
                lane="repurposing",
                screening_intervention_id=screening_intervention_id,
                verified_normalized_intervention_id=(
                    representative_identity.verified_normalized_intervention_id
                ),
                active_moiety_id=representative_identity.active_moiety_id,
                identity_status=representative_identity.status,
                identity_verified=representative_identity.identity_verified,
                representative_seed_id=representative_seed_id,
                endpoint_ids=tuple(sorted(endpoint.endpoint_id for endpoint in case.endpoints)),
                structured_routes=normalize_structured_routes(
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
                    for seed_id in seed_ids
                    for route in seeds[seed_id].structured_routes
                ),
                evidence_modalities=tuple(
                    sorted(
                        {
                            modality
                            for seed_id in seed_ids
                            for modality in seeds[seed_id].evidence_modalities
                        },
                        key=lambda value: value.value,
                    )
                ),
                chemical_universes=tuple(
                    sorted(
                        {
                            universe
                            for seed_id in seed_ids
                            for universe in seeds[seed_id].chemical_universes
                        },
                        key=lambda value: value.value,
                    )
                ),
                source_seed_ids=seed_ids,
                source_mapping_ids=mapping_ids,
                discovery_route_ids=route_ids,
                alias_ids=alias_ids,
            )
        )
    return (
        tuple(sorted(links, key=lambda row: row.link_id)),
        tuple(sorted(candidates, key=lambda row: row.screened_candidate_id)),
    )


def _derive_quarantine_records(
    seeds: dict[str, CandidateSeed],
    aliases_by_seed: dict[str, list[SeedAlias]],
    identities: dict[str, IdentityResolutionRecord],
    decisions: dict[str, ScreeningDecision],
) -> tuple[QuarantinedSeedRecord, ...]:
    rows: list[QuarantinedSeedRecord] = []
    for seed_id, decision in sorted(decisions.items()):
        if (
            decision.canonical_disposition is not CanonicalDisposition.QUARANTINE
            and decision.screening_outcome is not ScreeningOutcome.SCREEN_QUARANTINED
        ):
            continue
        seed = seeds[seed_id]
        body = {
            "seed_id": seed_id,
            "disposition": decision.disposition,
            "identity_status": identities[seed_id].status,
            "reason": decision.reason,
            "unresolved_fields": decision.unresolved_fields,
            "source_mapping_ids": (seed.source_mapping_id,),
            "discovery_route_ids": seed.discovery_route_ids,
            "alias_ids": tuple(
                sorted(alias.alias_id for alias in aliases_by_seed.get(seed_id, []))
            ),
            "can_advance": False,
        }
        rows.append(
            QuarantinedSeedRecord(
                quarantine_id=_stable_id("QUARANTINE", QUARANTINE_ID_RULE, body),
                **body,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.quarantine_id))


def _reconciliation(
    mappings: tuple[SeedSourceMapping, ...],
    seeds: tuple[CandidateSeed, ...],
    decisions: tuple[ScreeningDecision, ...],
    links: tuple[SeedToScreenedCandidateMapping, ...],
    candidates: tuple[ScreenedCandidateRecord, ...],
    quarantines: tuple[QuarantinedSeedRecord, ...],
) -> FunnelReconciliation:
    detailed = Counter(row.disposition for row in decisions)
    canonical = Counter(row.canonical_disposition for row in decisions)
    screening = Counter(row.screening_outcome for row in decisions)
    result = FunnelReconciliation(
        retrieved_mapping_count=len(mappings),
        seed_count=len(seeds),
        current_disposition_count=len(decisions),
        admit_count=canonical[CanonicalDisposition.ADMIT],
        merge_count=canonical[CanonicalDisposition.MERGE],
        baseline_count=canonical[CanonicalDisposition.BASELINE],
        reject_count=canonical[CanonicalDisposition.REJECT],
        quarantine_count=canonical[CanonicalDisposition.QUARANTINE],
        failed_count=canonical[CanonicalDisposition.FAILED],
        screened_count=screening[ScreeningOutcome.SCREENED],
        screen_rejected_count=screening[ScreeningOutcome.SCREEN_REJECTED],
        screen_quarantined_count=screening[ScreeningOutcome.SCREEN_QUARANTINED],
        screen_failed_count=screening[ScreeningOutcome.SCREEN_FAILED],
        not_screened_count=screening[ScreeningOutcome.NOT_SCREENED],
        screened_candidate_count=len(candidates),
        seed_candidate_link_count=len(links),
        unresolved_or_quarantined_seed_count=len(quarantines),
        detailed_disposition_counts=tuple(
            DispositionCount(disposition=disposition, count=detailed[disposition])
            for disposition in sorted(detailed, key=lambda value: value.value)
        ),
    )
    if result.retrieved_mapping_count != result.seed_count:
        raise SeedFunnelError("Retrieved mapping and seed counts do not reconcile")
    if result.seed_count != result.current_disposition_count:
        raise SeedFunnelError("Seed and current disposition counts do not reconcile")
    if result.seed_count != sum(
        (
            result.admit_count,
            result.merge_count,
            result.baseline_count,
            result.reject_count,
            result.quarantine_count,
            result.failed_count,
        )
    ):
        raise SeedFunnelError("Canonical seed-disposition equation does not balance")
    if result.admit_count != sum(
        (
            result.screened_count,
            result.screen_rejected_count,
            result.screen_quarantined_count,
            result.screen_failed_count,
        )
    ):
        raise SeedFunnelError("Admitted-seed screening equation does not balance")
    if result.not_screened_count != result.seed_count - result.admit_count:
        raise SeedFunnelError("Non-admitted seeds must remain explicitly not_screened")
    if result.screened_candidate_count != result.screened_count:
        raise SeedFunnelError("Screened-candidate count does not match screen-pass decisions")
    if (
        result.quarantine_count + result.screen_quarantined_count
        != result.unresolved_or_quarantined_seed_count
    ):
        raise SeedFunnelError("Quarantine records do not reconcile")
    return result


def build_seed_funnel(
    case: CaseRevision,
    *,
    source_mappings: Iterable[SeedSourceMapping],
    discovery_routes: Iterable[SeedDiscoveryRoute],
    seeds: Iterable[CandidateSeed],
    aliases: Iterable[SeedAlias],
    identity_resolutions: Iterable[IdentityResolutionRecord],
    screening_decisions: Iterable[ScreeningDecision],
) -> SeedFunnelSnapshot:
    """Validate and canonically reduce already-retrieved lightweight records.

    Identical replay is a no-op; the same stable ID with different content is a
    hard conflict.  No minimum candidate count or discovery behavior is applied.
    """

    if not isinstance(case, CaseRevision):
        raise SeedFunnelError("A typed CaseRevision is required")
    validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise SeedFunnelError("Seed creation requires a READY case with resolved endpoints")

    reduced_mappings = _reduce_records(
        source_mappings, SeedSourceMapping, "mapping_id", "source mappings"
    )
    reduced_routes = _reduce_records(
        discovery_routes, SeedDiscoveryRoute, "route_id", "discovery routes"
    )
    reduced_seeds = _reduce_records(seeds, CandidateSeed, "seed_id", "seeds")
    reduced_aliases = _reduce_records(aliases, SeedAlias, "alias_id", "aliases")
    reduced_identities = _reduce_records(
        identity_resolutions,
        IdentityResolutionRecord,
        "identity_resolution_id",
        "identity resolutions",
    )
    reduced_decisions = _reduce_records(
        screening_decisions, ScreeningDecision, "decision_id", "screening decisions"
    )
    (
        mappings_by_id,
        routes_by_id,
        seeds_by_id,
        aliases_by_seed,
        identities_by_seed,
        decisions_by_seed,
    ) = _validate_records(
        case,
        reduced_mappings,
        reduced_routes,
        reduced_seeds,
        reduced_aliases,
        reduced_identities,
        reduced_decisions,
    )
    links, candidates = _derive_candidate_records(
        case,
        mappings_by_id,
        routes_by_id,
        seeds_by_id,
        aliases_by_seed,
        identities_by_seed,
        decisions_by_seed,
    )
    quarantines = _derive_quarantine_records(
        seeds_by_id, aliases_by_seed, identities_by_seed, decisions_by_seed
    )
    reconciliation = _reconciliation(
        reduced_mappings,
        reduced_seeds,
        reduced_decisions,
        links,
        candidates,
        quarantines,
    )
    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": SEED_FUNNEL_MODEL_VERSION,
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "source_mappings": reduced_mappings,
        "discovery_routes": reduced_routes,
        "seeds": reduced_seeds,
        "aliases": reduced_aliases,
        "identity_resolutions": reduced_identities,
        "screening_decisions": reduced_decisions,
        "seed_candidate_mappings": links,
        "screened_candidates": candidates,
        "unresolved_or_quarantined_seeds": quarantines,
        "reconciliation": reconciliation,
    }
    return SeedFunnelSnapshot(
        snapshot_id=_stable_id("SEED-FUNNEL", SNAPSHOT_ID_RULE, body),
        **body,
    )


def validate_seed_funnel(case: CaseRevision, snapshot: SeedFunnelSnapshot) -> None:
    """Rebuild and compare a snapshot using only its immutable input ledgers."""

    if not isinstance(snapshot, SeedFunnelSnapshot):
        raise SeedFunnelError("Expected SeedFunnelSnapshot")
    rebuilt = build_seed_funnel(
        case,
        source_mappings=snapshot.source_mappings,
        discovery_routes=snapshot.discovery_routes,
        seeds=snapshot.seeds,
        aliases=snapshot.aliases,
        identity_resolutions=snapshot.identity_resolutions,
        screening_decisions=snapshot.screening_decisions,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(snapshot):
        raise SeedFunnelError("Seed funnel snapshot differs from its deterministic rebuild")


__all__ = [
    "ALIAS_ID_RULE",
    "AliasAssertionStatus",
    "AliasKind",
    "CandidateSeed",
    "CanonicalDisposition",
    "ChemicalUniverse",
    "CompoundHint",
    "CompoundHintKind",
    "DETAILED_TO_CANONICAL",
    "DETAILED_TO_SCREENING_OUTCOME",
    "DetailedDisposition",
    "DevelopmentStatus",
    "DispositionCount",
    "EndpointScreenStatus",
    "EndpointApplicabilityReason",
    "EndpointScreeningAssessment",
    "FunnelReconciliation",
    "IdentityAssertion",
    "IdentityAssertionStatus",
    "IdentityResolutionRecord",
    "EvidenceModality",
    "QuarantinedSeedRecord",
    "SCHEMA_VERSION",
    "SEED_FUNNEL_MODEL_VERSION",
    "ScreenedCandidateRecord",
    "ScreeningDecision",
    "ScreeningOutcome",
    "SeedAlias",
    "SeedCandidateRole",
    "SeedDiscoveryRoute",
    "SeedFunnelError",
    "SeedFunnelSnapshot",
    "SeedIdentityStatus",
    "SeedSourceMapping",
    "SeedToScreenedCandidateMapping",
    "SeedUncertainty",
    "StructuredCausalRoute",
    "UncertaintyKind",
    "UncertaintyLevel",
    "alias_comparison_value",
    "build_seed_funnel",
    "known_development_status",
    "make_candidate_seed",
    "make_compound_hint",
    "make_discovery_route",
    "make_endpoint_assessment",
    "make_identity_assertion",
    "make_identity_resolution",
    "make_screening_decision",
    "make_seed_alias",
    "make_source_mapping",
    "provisional_screening_intervention_id",
    "unknown_development_status",
    "validate_seed_funnel",
    "verified_active_moiety_id",
    "verified_normalized_intervention_id",
]
