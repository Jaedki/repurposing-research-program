#!/usr/bin/env python3
"""Generic schema-v7 retrieval adapters and mechanically proved coverage.

The framework owns source/query declarations, traversal, content and execution
receipts, deterministic cache/replay, normalized-record screening, and mapping
to the existing schema-v7 seed records.  Source-specific adapters only fetch
and normalize one response page.  They do not declare their own coverage state
or decide whether an unconsumed continuation may be ignored.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import time
import unicodedata
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from v7_case_model import (
    CaseRevision,
    CaseStatus,
    ValueStatus,
    canonical_bytes,
    content_sha256,
    validate_case_revision,
)
from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
    ScientificNode,
    UncertaintyKind,
    UncertaintyLevel,
    make_structured_route,
    validate_node,
)
from v7_seed_funnel import (
    CandidateSeed,
    CompoundHint,
    CompoundHintKind,
    SeedDiscoveryRoute,
    SeedIdentityStatus,
    SeedSourceMapping,
    SeedUncertainty,
    known_development_status,
    make_candidate_seed,
    make_compound_hint,
    make_discovery_route,
    make_source_mapping,
    unknown_development_status,
)


SCHEMA_VERSION = 7
RETRIEVAL_ADAPTER_MODEL_VERSION = "schema-v7-retrieval-adapter-v3"
SOURCE_UNIVERSE_ID_RULE = "schema-v7-source-universe-id-v1"
QUERY_PLAN_ID_RULE = "schema-v7-query-plan-id-v1"
NORMALIZED_RECORD_ID_RULE = "schema-v7-normalized-source-record-id-v1"
CONTENT_RECEIPT_ID_RULE = "schema-v7-retrieval-content-receipt-id-v1"
EXECUTION_RECEIPT_ID_RULE = "schema-v7-retrieval-execution-receipt-id-v1"
SEED_EMISSION_LINK_ID_RULE = "schema-v7-normalized-record-seed-link-id-v1"
COVERAGE_PROOF_ID_RULE = "schema-v7-coverage-proof-id-v1"
COVERAGE_BUNDLE_ID_RULE = "schema-v7-coverage-bundle-id-v1"


class RetrievalContractError(ValueError):
    """Raised when a plan, adapter response, receipt, or proof is inconsistent."""


class CacheMissError(RetrievalContractError):
    """Raised when replay-only execution cannot find a frozen response."""


class CoverageState(str, Enum):
    COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE = "complete_for_declared_query_and_release"
    NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY = "no_relevant_hits_within_declared_query"
    PARTIAL_DUE_TO_SOURCE_LIMIT = "partial_due_to_source_limit"
    PARTIAL_DUE_TO_RATE_LIMIT = "partial_due_to_rate_limit"
    UNSUPPORTED_SOURCE_CAPABILITY = "unsupported_source_capability"
    FAILED_RETRIEVAL = "failed_retrieval"
    NOT_YET_SEARCHED = "not_yet_searched"


class PaginationKind(str, Enum):
    NONE = "none"
    PAGE = "page"
    CURSOR = "cursor"


class DenominatorKind(str, Enum):
    EXACT_DECLARED = "exact_declared"
    PROVIDER_REPORTED = "provider_reported"
    UNKNOWN = "unknown"


class RecordDisposition(str, Enum):
    EMITTED_SEEDS = "emitted_seeds"
    NO_INTERVENTION_MAPPING = "no_intervention_mapping"
    SOURCE_SCOPE_EXCLUDED = "source_scope_excluded"
    NON_INTERVENTION_TYPE_EXCLUDED = "non_intervention_type_excluded"
    FAILED_MAPPING = "failed_mapping"


class ChemicalIdentityMatchLevel(str, Enum):
    """Strength of one source-native chemical identity assertion."""

    EXACT_DATABASE_IDENTIFIER = "exact_database_identifier"
    EXACT_STRUCTURE = "exact_structure"
    CONNECTIVITY_ONLY = "connectivity_only"
    UNVERIFIED = "unverified"


class SourceFindingPolarity(str, Enum):
    """Source-native discovery annotation, never a therapeutic verdict."""

    SUPPORTIVE = "supportive"
    CONTRADICTORY = "contradictory"
    NULL = "null"
    MIXED = "mixed"
    NOT_EVALUATED = "not_evaluated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    adapter_version: str
    source_id: str
    source_release: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: tuple[float, ...]


@dataclass(frozen=True)
class DeclaredSourceUniverse:
    source_universe_id: str
    source_id: str
    source_release: str
    source_snapshot_at: str
    native_scope: str
    source_side_filters: Mapping[str, Any]
    local_filters: Mapping[str, Any]
    denominator_kind: DenominatorKind
    declared_total: int | None
    pagination_kind: PaginationKind
    continuation_parameter: str
    source_record_cap: int | None
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class QueryPlan:
    query_plan_id: str
    source_universe: DeclaredSourceUniverse
    query_family_id: str
    required: bool
    exact_request_parameters: Mapping[str, Any]
    initial_continuation_token: str | None
    max_pages: int | None
    max_records: int | None
    allowed_terminal_codes: tuple[str, ...]
    retry_policy: RetryPolicy


@dataclass(frozen=True)
class RetrievalRequest:
    adapter_id: str
    adapter_version: str
    source_id: str
    source_release: str
    query_plan_id: str
    query_family_id: str
    page_ordinal: int
    input_continuation_token: str | None
    exact_request_parameters: Mapping[str, Any]
    request_sha256: str


@dataclass(frozen=True)
class RateLimitMetadata:
    limit: int | None
    remaining: int | None
    reset_at: str | None
    retry_after_seconds: float | None


@dataclass(frozen=True)
class AdapterPageResponse:
    request_sha256: str
    raw_response: bytes
    returned_count: int
    provider_total: int | None
    output_continuation_token: str | None
    continuation_exhausted: bool
    terminal_code: str
    source_limit_reached: bool = False
    rate_limit: RateLimitMetadata | None = None


@dataclass(frozen=True)
class SeedRouteTemplate:
    causal_route: CausalRoute
    disease_state_node: ScientificNode
    intervention_target: ScientificNode
    action: InterventionAction
    direction: EffectDirection
    intermediate_state: ScientificNode
    endpoint_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class SourceActivityObservation:
    """Source-native bioactivity facts retained before any evidence scoring."""

    activity_type: str
    relation: str
    value: str
    units: str
    assay_id: str
    assay_context: str
    target_id: str
    target_organism: str
    confidence: str
    confidence_scale: str


@dataclass(frozen=True)
class SourceMappingContext:
    """Typed target/disease/pathway/assay coordinates for one source mapping."""

    mapping_type: str
    target_id: str
    disease_id: str
    pathway_id: str
    assay_id: str
    source_context: str


@dataclass(frozen=True)
class ChemicalIdentityReference:
    """One exact or explicitly bounded chemical cross-reference assertion."""

    namespace: str
    identifier: str
    match_level: ChemicalIdentityMatchLevel
    authority: str
    authority_release: str


@dataclass(frozen=True)
class SourceEvidenceAnnotation:
    """Lightweight source-native status/result context retained with a seed.

    This is deliberately not a Stage-6 grounded claim.  In particular, a
    failed or terminated study status can be retained without being promoted
    to a finding about efficacy.
    """

    modality: EvidenceModality
    annotation_type: str
    source_item_id: str
    source_locator: str
    finding_polarity: SourceFindingPolarity
    status: str
    endpoint_id: str
    source_text: str


@dataclass(frozen=True)
class PublicationDensityMetadata:
    """Descriptive literature density; prohibited as a seed-admission gate."""

    source_id: str
    as_of: str
    query_scope: str
    publication_count: int | None
    citation_count: int | None
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedSeedAssertion:
    assertion_locator: str
    raw_intervention_assertion: str
    compound_hint: CompoundHint
    endpoint_ids: tuple[str, ...]
    route_templates: tuple[SeedRouteTemplate, ...]
    evidence_modalities: tuple[EvidenceModality, ...]
    chemical_universes: tuple[ChemicalUniverse, ...]
    development_status: DevelopmentStatus
    uncertainty: tuple[SeedUncertainty, ...]
    activity_observations: tuple[SourceActivityObservation, ...]
    identity_references: tuple[ChemicalIdentityReference, ...]
    mapping_contexts: tuple[SourceMappingContext, ...]
    evidence_annotations: tuple[SourceEvidenceAnnotation, ...]
    publication_density: tuple[PublicationDensityMetadata, ...]


@dataclass(frozen=True)
class NormalizedSourceRecord:
    normalized_record_id: str
    source_id: str
    source_release: str
    native_record_id: str
    native_record_locator: str
    source_record_sha256: str
    retrieval_content_receipt_id: str
    disposition: RecordDisposition
    disposition_reason: str
    screening_rule_id: str
    seed_assertions: tuple[NormalizedSeedAssertion, ...]


@dataclass(frozen=True)
class RetrievalContentReceipt:
    content_receipt_id: str
    adapter_id: str
    adapter_version: str
    source_id: str
    source_release: str
    source_universe_id: str
    query_plan_id: str
    query_family_id: str
    page_ordinal: int
    input_continuation_token: str | None
    output_continuation_token: str | None
    exact_request_parameters: Mapping[str, Any]
    request_sha256: str
    response_sha256: str
    returned_count: int
    normalized_record_count: int
    failed_record_count: int
    provider_total: int | None
    continuation_exhausted: bool
    terminal_code: str
    source_limit_reached: bool
    receipt_status: str
    failure_code: str


@dataclass(frozen=True)
class RetrievalExecutionReceipt:
    execution_receipt_id: str
    query_plan_id: str
    page_ordinal: int
    input_continuation_token: str | None
    request_sha256: str
    attempt_number: int
    max_attempts: int
    started_at: str
    completed_at: str
    outcome: str
    retryable: bool
    retry_delay_seconds: float
    error_code: str
    error_message: str
    rate_limit: RateLimitMetadata | None
    cache_hit: bool
    content_receipt_id: str | None


@dataclass(frozen=True)
class SeedEmissionLink:
    emission_link_id: str
    normalized_record_id: str
    assertion_locator: str
    source_mapping: SeedSourceMapping
    discovery_route: SeedDiscoveryRoute
    seed: CandidateSeed


@dataclass(frozen=True)
class RecordScreeningDisposition:
    normalized_record_id: str
    disposition: RecordDisposition
    reason: str
    screening_rule_id: str
    seed_ids: tuple[str, ...]
    emission_link_ids: tuple[str, ...]


@dataclass(frozen=True)
class DispositionCount:
    disposition: RecordDisposition
    count: int


@dataclass(frozen=True)
class CoverageReconciliation:
    source_reported_total: int | None
    retrieved_page_count: int
    returned_native_record_count: int
    normalized_record_count: int
    failed_record_count: int
    screened_record_count: int
    emitted_seed_count: int
    unvisited_record_count: int | None
    disposition_counts: tuple[DispositionCount, ...]
    continuation_exhausted: bool
    next_continuation_token: str | None
    count_reconciliation_ok: bool


@dataclass(frozen=True)
class CoverageProof:
    schema_version: int
    model_version: str
    coverage_proof_id: str
    execution_trace_id: str
    adapter: AdapterDescriptor
    query_plan: QueryPlan
    coverage_state: CoverageState
    content_receipts: tuple[RetrievalContentReceipt, ...]
    execution_receipts: tuple[RetrievalExecutionReceipt, ...]
    normalized_records: tuple[NormalizedSourceRecord, ...]
    screening_dispositions: tuple[RecordScreeningDisposition, ...]
    seed_emissions: tuple[SeedEmissionLink, ...]
    reconciliation: CoverageReconciliation
    source_specific_limitations: tuple[str, ...]
    coverage_gaps: tuple[str, ...]


@dataclass(frozen=True)
class CoverageBundle:
    schema_version: int
    model_version: str
    coverage_bundle_id: str
    proofs: tuple[CoverageProof, ...]
    source_mappings: tuple[SeedSourceMapping, ...]
    discovery_routes: tuple[SeedDiscoveryRoute, ...]
    seeds: tuple[CandidateSeed, ...]


class AdapterTransportError(RuntimeError):
    """Typed transport failure retained in execution provenance."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        rate_limited: bool = False,
        retry_after_seconds: float | None = None,
        rate_limit: RateLimitMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit = rate_limit


@runtime_checkable
class RetrievalAdapter(Protocol):
    """Source adapters fetch and normalize pages; the framework proves coverage."""

    descriptor: AdapterDescriptor

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]: ...

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse: ...

    def normalize(
        self, request: RetrievalRequest, response: AdapterPageResponse
    ) -> tuple[NormalizedSourceRecord, ...]: ...


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if hasattr(value, "__dataclass_fields__"):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze_json(value: Any, label: str = "value") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RetrievalContractError(f"{label}: non-finite numbers are prohibited")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise RetrievalContractError(f"{label}: mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze_json(value[key], f"{label}.{key}") for key in sorted(value)}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{label}[]") for item in value)
    raise RetrievalContractError(f"{label}: value is not canonical JSON")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise RetrievalContractError(f"{label}: expected text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise RetrievalContractError(f"{label}: value cannot be blank")
    return normalized


def _raw_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not " ".join(unicodedata.normalize("NFKC", value).split()):
        raise RetrievalContractError(f"{label}: nonblank source text is required")
    return value


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, label) for value in values}))
    if required and not result:
        raise RetrievalContractError(f"{label}: at least one value is required")
    return result


def _stable_id(prefix: str, rule: str, projection: Any) -> str:
    return f"{prefix}-{content_sha256({'rule_id': rule, 'projection': projection})[:24]}"


def _record_body(record: Any, id_field: str) -> dict[str, Any]:
    return {
        field.name: getattr(record, field.name)
        for field in fields(record)
        if field.name != id_field
    }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_timestamp(value: str, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RetrievalContractError(f"{label}: invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise RetrievalContractError(f"{label}: timestamp must include a UTC offset")


def make_adapter_descriptor(
    *,
    adapter_id: str,
    adapter_version: str,
    source_id: str,
    source_release: str,
    capabilities: Iterable[str],
) -> AdapterDescriptor:
    result = AdapterDescriptor(
        adapter_id=_text(adapter_id, "adapter_id"),
        adapter_version=_text(adapter_version, "adapter_version"),
        source_id=_text(source_id, "source_id"),
        source_release=_text(source_release, "source_release"),
        capabilities=_strings(capabilities, "capabilities", required=True),
    )
    return result


def make_source_universe(
    *,
    source_id: str,
    source_release: str,
    source_snapshot_at: str,
    native_scope: str,
    source_side_filters: Mapping[str, Any],
    local_filters: Mapping[str, Any],
    denominator_kind: DenominatorKind,
    declared_total: int | None,
    pagination_kind: PaginationKind,
    continuation_parameter: str = "",
    source_record_cap: int | None = None,
    limitations: Iterable[str] = (),
) -> DeclaredSourceUniverse:
    frozen_source_filters = _freeze_json(source_side_filters, "source_side_filters")
    frozen_local_filters = _freeze_json(local_filters, "local_filters")
    if not isinstance(denominator_kind, DenominatorKind):
        raise RetrievalContractError("denominator_kind: invalid controlled value")
    if declared_total is not None and (
        isinstance(declared_total, bool) or not isinstance(declared_total, int) or declared_total < 0
    ):
        raise RetrievalContractError("declared_total must be a nonnegative integer or null")
    if denominator_kind is DenominatorKind.EXACT_DECLARED and declared_total is None:
        raise RetrievalContractError("exact declared universes require declared_total")
    if denominator_kind is not DenominatorKind.EXACT_DECLARED and declared_total is not None:
        raise RetrievalContractError("only exact declared universes may predeclare a total")
    if source_record_cap is not None and (
        isinstance(source_record_cap, bool)
        or not isinstance(source_record_cap, int)
        or source_record_cap <= 0
    ):
        raise RetrievalContractError("source_record_cap must be a positive integer or null")
    continuation = ""
    if pagination_kind is not PaginationKind.NONE:
        continuation = _text(continuation_parameter, "continuation_parameter")
    elif continuation_parameter:
        raise RetrievalContractError("non-paginated universes cannot name a continuation parameter")
    body = {
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "source_snapshot_at": _text(source_snapshot_at, "source_snapshot_at"),
        "native_scope": _text(native_scope, "native_scope"),
        "source_side_filters": frozen_source_filters,
        "local_filters": frozen_local_filters,
        "denominator_kind": denominator_kind,
        "declared_total": declared_total,
        "pagination_kind": pagination_kind,
        "continuation_parameter": continuation,
        "source_record_cap": source_record_cap,
        "limitations": _strings(limitations, "limitations"),
    }
    _validate_timestamp(body["source_snapshot_at"], "source_snapshot_at")
    return DeclaredSourceUniverse(
        source_universe_id=_stable_id("SOURCE-UNIVERSE", SOURCE_UNIVERSE_ID_RULE, body),
        **body,
    )


def make_query_plan(
    source_universe: DeclaredSourceUniverse,
    *,
    query_family_id: str,
    required: bool,
    exact_request_parameters: Mapping[str, Any],
    initial_continuation_token: str | None,
    max_pages: int | None,
    max_records: int | None,
    allowed_terminal_codes: Iterable[str],
    retry_policy: RetryPolicy,
) -> QueryPlan:
    validate_source_universe(source_universe)
    if not isinstance(required, bool):
        raise RetrievalContractError("required must be boolean")
    frozen_parameters = _freeze_json(exact_request_parameters, "exact_request_parameters")
    if source_universe.pagination_kind is PaginationKind.NONE:
        if initial_continuation_token is not None or max_pages not in {None, 1}:
            raise RetrievalContractError("non-paginated plans cannot declare continuation traversal")
    elif source_universe.pagination_kind is PaginationKind.PAGE:
        if initial_continuation_token is None:
            raise RetrievalContractError("page traversal requires an initial page token")
        _text(initial_continuation_token, "initial_continuation_token")
    elif initial_continuation_token is not None:
        _text(initial_continuation_token, "initial_continuation_token")
    for value, label in ((max_pages, "max_pages"), (max_records, "max_records")):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise RetrievalContractError(f"{label} must be a positive integer or null")
    validate_retry_policy(retry_policy)
    body = {
        "source_universe": source_universe,
        "query_family_id": _text(query_family_id, "query_family_id"),
        "required": required,
        "exact_request_parameters": frozen_parameters,
        "initial_continuation_token": initial_continuation_token,
        "max_pages": max_pages,
        "max_records": max_records,
        "allowed_terminal_codes": _strings(
            allowed_terminal_codes, "allowed_terminal_codes", required=True
        ),
        "retry_policy": retry_policy,
    }
    return QueryPlan(
        query_plan_id=_stable_id("QUERY-PLAN", QUERY_PLAN_ID_RULE, body), **body
    )


def validate_retry_policy(policy: RetryPolicy) -> None:
    if not isinstance(policy, RetryPolicy):
        raise RetrievalContractError("retry_policy: expected RetryPolicy")
    if isinstance(policy.max_attempts, bool) or not isinstance(policy.max_attempts, int) or policy.max_attempts < 1:
        raise RetrievalContractError("retry_policy.max_attempts must be positive")
    if len(policy.backoff_seconds) != max(0, policy.max_attempts - 1):
        raise RetrievalContractError("retry_policy requires one backoff between adjacent attempts")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        for value in policy.backoff_seconds
    ):
        raise RetrievalContractError("retry backoffs must be finite nonnegative numbers")


def validate_source_universe(universe: DeclaredSourceUniverse) -> None:
    if not isinstance(universe, DeclaredSourceUniverse):
        raise RetrievalContractError("expected DeclaredSourceUniverse")
    rebuilt = make_source_universe(
        source_id=universe.source_id,
        source_release=universe.source_release,
        source_snapshot_at=universe.source_snapshot_at,
        native_scope=universe.native_scope,
        source_side_filters=universe.source_side_filters,
        local_filters=universe.local_filters,
        denominator_kind=universe.denominator_kind,
        declared_total=universe.declared_total,
        pagination_kind=universe.pagination_kind,
        continuation_parameter=universe.continuation_parameter,
        source_record_cap=universe.source_record_cap,
        limitations=universe.limitations,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(universe):
        raise RetrievalContractError("source universe differs from its content-derived declaration")


def validate_query_plan(plan: QueryPlan) -> None:
    if not isinstance(plan, QueryPlan):
        raise RetrievalContractError("expected QueryPlan")
    rebuilt = make_query_plan(
        plan.source_universe,
        query_family_id=plan.query_family_id,
        required=plan.required,
        exact_request_parameters=plan.exact_request_parameters,
        initial_continuation_token=plan.initial_continuation_token,
        max_pages=plan.max_pages,
        max_records=plan.max_records,
        allowed_terminal_codes=plan.allowed_terminal_codes,
        retry_policy=plan.retry_policy,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(plan):
        raise RetrievalContractError("query plan differs from its content-derived declaration")


def _request_projection(
    *,
    adapter_id: str,
    adapter_version: str,
    source_id: str,
    source_release: str,
    query_plan_id: str,
    query_family_id: str,
    page_ordinal: int,
    input_continuation_token: str | None,
    exact_request_parameters: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "source_id": source_id,
        "source_release": source_release,
        "query_plan_id": query_plan_id,
        "query_family_id": query_family_id,
        "page_ordinal": page_ordinal,
        "input_continuation_token": input_continuation_token,
        "exact_request_parameters": exact_request_parameters,
    }


def make_retrieval_request(
    descriptor: AdapterDescriptor,
    plan: QueryPlan,
    *,
    page_ordinal: int,
    input_continuation_token: str | None,
) -> RetrievalRequest:
    validate_query_plan(plan)
    if isinstance(page_ordinal, bool) or not isinstance(page_ordinal, int) or page_ordinal < 1:
        raise RetrievalContractError("page_ordinal must be a positive integer")
    parameters = dict(_plain(plan.exact_request_parameters))
    universe = plan.source_universe
    if universe.pagination_kind is PaginationKind.PAGE:
        if input_continuation_token is None:
            raise RetrievalContractError("page requests require a page token")
        parameters[universe.continuation_parameter] = input_continuation_token
    elif universe.pagination_kind is PaginationKind.CURSOR and input_continuation_token is not None:
        parameters[universe.continuation_parameter] = input_continuation_token
    frozen_parameters = _freeze_json(parameters, "request_parameters")
    projection = _request_projection(
        adapter_id=descriptor.adapter_id,
        adapter_version=descriptor.adapter_version,
        source_id=descriptor.source_id,
        source_release=descriptor.source_release,
        query_plan_id=plan.query_plan_id,
        query_family_id=plan.query_family_id,
        page_ordinal=page_ordinal,
        input_continuation_token=input_continuation_token,
        exact_request_parameters=frozen_parameters,
    )
    return RetrievalRequest(
        **projection,
        request_sha256=content_sha256(projection),
    )


def make_seed_route_template(
    *,
    causal_route: CausalRoute,
    disease_state_node: ScientificNode,
    intervention_target: ScientificNode,
    action: InterventionAction,
    direction: EffectDirection,
    intermediate_state: ScientificNode,
    endpoint_id: str,
    evidence_ids: Iterable[str],
) -> SeedRouteTemplate:
    result = SeedRouteTemplate(
        causal_route=causal_route,
        disease_state_node=disease_state_node,
        intervention_target=intervention_target,
        action=action,
        direction=direction,
        intermediate_state=intermediate_state,
        endpoint_id=_text(endpoint_id, "route endpoint_id"),
        evidence_ids=_strings(evidence_ids, "route evidence_ids", required=True),
    )
    validate_seed_route_template(result)
    return result


def validate_seed_route_template(template: SeedRouteTemplate) -> None:
    if not isinstance(template, SeedRouteTemplate):
        raise RetrievalContractError("expected SeedRouteTemplate")
    if not isinstance(template.causal_route, CausalRoute):
        raise RetrievalContractError("route template has an invalid causal route")
    if not isinstance(template.action, InterventionAction) or not isinstance(
        template.direction, EffectDirection
    ):
        raise RetrievalContractError("route template has an invalid action or direction")
    for name in ("disease_state_node", "intervention_target", "intermediate_state"):
        try:
            validate_node(getattr(template, name), f"route template.{name}")
        except ValueError as exc:
            raise RetrievalContractError(str(exc)) from exc
    _text(template.endpoint_id, "route template.endpoint_id")
    if tuple(sorted(set(template.evidence_ids))) != template.evidence_ids or not template.evidence_ids:
        raise RetrievalContractError("route template evidence IDs must be nonempty and canonical")


def _optional_source_text(value: Any, label: str) -> str:
    if value is None or value == "":
        return ""
    return _text(value, label)


def make_source_activity_observation(
    *,
    activity_type: str,
    relation: str = "",
    value: str = "",
    units: str = "",
    assay_id: str = "",
    assay_context: str = "",
    target_id: str = "",
    target_organism: str = "",
    confidence: str = "",
    confidence_scale: str = "",
) -> SourceActivityObservation:
    result = SourceActivityObservation(
        activity_type=_text(activity_type, "activity_type"),
        relation=_optional_source_text(relation, "activity relation"),
        value=_optional_source_text(value, "activity value"),
        units=_optional_source_text(units, "activity units"),
        assay_id=_optional_source_text(assay_id, "activity assay_id"),
        assay_context=_optional_source_text(assay_context, "activity assay_context"),
        target_id=_optional_source_text(target_id, "activity target_id"),
        target_organism=_optional_source_text(
            target_organism, "activity target_organism"
        ),
        confidence=_optional_source_text(confidence, "activity confidence"),
        confidence_scale=_optional_source_text(
            confidence_scale, "activity confidence_scale"
        ),
    )
    validate_source_activity_observation(result)
    return result


def validate_source_activity_observation(value: SourceActivityObservation) -> None:
    if not isinstance(value, SourceActivityObservation):
        raise RetrievalContractError("expected SourceActivityObservation")
    _text(value.activity_type, "activity_type")
    for field_name in (
        "relation",
        "value",
        "units",
        "assay_id",
        "assay_context",
        "target_id",
        "target_organism",
        "confidence",
        "confidence_scale",
    ):
        field_value = getattr(value, field_name)
        if field_value:
            _text(field_value, f"activity {field_name}")
    if bool(value.confidence) is not bool(value.confidence_scale):
        raise RetrievalContractError(
            "activity confidence and confidence scale must be supplied together"
        )


def make_source_mapping_context(
    *,
    mapping_type: str,
    target_id: str = "",
    disease_id: str = "",
    pathway_id: str = "",
    assay_id: str = "",
    source_context: str = "",
) -> SourceMappingContext:
    result = SourceMappingContext(
        mapping_type=_text(mapping_type, "mapping_type"),
        target_id=_optional_source_text(target_id, "mapping target_id"),
        disease_id=_optional_source_text(disease_id, "mapping disease_id"),
        pathway_id=_optional_source_text(pathway_id, "mapping pathway_id"),
        assay_id=_optional_source_text(assay_id, "mapping assay_id"),
        source_context=_optional_source_text(source_context, "mapping source_context"),
    )
    validate_source_mapping_context(result)
    return result


def validate_source_mapping_context(value: SourceMappingContext) -> None:
    if not isinstance(value, SourceMappingContext):
        raise RetrievalContractError("expected SourceMappingContext")
    _text(value.mapping_type, "mapping_type")
    for field_name in (
        "target_id",
        "disease_id",
        "pathway_id",
        "assay_id",
        "source_context",
    ):
        field_value = getattr(value, field_name)
        if field_value:
            _text(field_value, f"mapping {field_name}")
    if not any((value.target_id, value.disease_id, value.pathway_id, value.assay_id)):
        raise RetrievalContractError("mapping context requires a target, disease, pathway, or assay ID")


def make_chemical_identity_reference(
    *,
    namespace: str,
    identifier: str,
    match_level: ChemicalIdentityMatchLevel,
    authority: str,
    authority_release: str,
) -> ChemicalIdentityReference:
    if not isinstance(match_level, ChemicalIdentityMatchLevel):
        raise RetrievalContractError("identity match_level: invalid controlled value")
    result = ChemicalIdentityReference(
        namespace=_text(namespace, "identity namespace").upper(),
        identifier=_text(identifier, "identity identifier"),
        match_level=match_level,
        authority=_text(authority, "identity authority"),
        authority_release=_text(authority_release, "identity authority_release"),
    )
    validate_chemical_identity_reference(result)
    return result


def validate_chemical_identity_reference(value: ChemicalIdentityReference) -> None:
    if not isinstance(value, ChemicalIdentityReference):
        raise RetrievalContractError("expected ChemicalIdentityReference")
    if not isinstance(value.match_level, ChemicalIdentityMatchLevel):
        raise RetrievalContractError("identity reference has an invalid match level")
    if value.namespace != _text(value.namespace, "identity namespace").upper():
        raise RetrievalContractError("identity reference namespace is not canonical")
    _text(value.identifier, "identity identifier")
    _text(value.authority, "identity authority")
    _text(value.authority_release, "identity authority_release")


def make_source_evidence_annotation(
    *,
    modality: EvidenceModality,
    annotation_type: str,
    source_item_id: str,
    source_locator: str,
    finding_polarity: SourceFindingPolarity,
    status: str = "",
    endpoint_id: str = "",
    source_text: str = "",
) -> SourceEvidenceAnnotation:
    if not isinstance(modality, EvidenceModality):
        raise RetrievalContractError("evidence annotation modality is invalid")
    if not isinstance(finding_polarity, SourceFindingPolarity):
        raise RetrievalContractError("evidence annotation polarity is invalid")
    result = SourceEvidenceAnnotation(
        modality=modality,
        annotation_type=_text(annotation_type, "evidence annotation_type"),
        source_item_id=_text(source_item_id, "evidence source_item_id"),
        source_locator=_text(source_locator, "evidence source_locator"),
        finding_polarity=finding_polarity,
        status=_optional_source_text(status, "evidence status"),
        endpoint_id=_optional_source_text(endpoint_id, "evidence endpoint_id"),
        source_text=_optional_source_text(source_text, "evidence source_text"),
    )
    validate_source_evidence_annotation(result)
    return result


def validate_source_evidence_annotation(value: SourceEvidenceAnnotation) -> None:
    if not isinstance(value, SourceEvidenceAnnotation):
        raise RetrievalContractError("expected SourceEvidenceAnnotation")
    if not isinstance(value.modality, EvidenceModality) or not isinstance(
        value.finding_polarity, SourceFindingPolarity
    ):
        raise RetrievalContractError("evidence annotation controlled value is invalid")
    for field_name in ("annotation_type", "source_item_id", "source_locator"):
        _text(getattr(value, field_name), f"evidence {field_name}")
    for field_name in ("status", "endpoint_id", "source_text"):
        field_value = getattr(value, field_name)
        if field_value:
            _text(field_value, f"evidence {field_name}")


def make_publication_density_metadata(
    *,
    source_id: str,
    as_of: str,
    query_scope: str,
    publication_count: int | None,
    citation_count: int | None,
    limitations: Iterable[str] = (),
) -> PublicationDensityMetadata:
    for value, label in (
        (publication_count, "publication_count"),
        (citation_count, "citation_count"),
    ):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise RetrievalContractError(f"{label} must be a nonnegative integer or null")
    result = PublicationDensityMetadata(
        source_id=_text(source_id, "publication density source_id"),
        as_of=_text(as_of, "publication density as_of"),
        query_scope=_text(query_scope, "publication density query_scope"),
        publication_count=publication_count,
        citation_count=citation_count,
        limitations=_strings(limitations, "publication density limitations"),
    )
    validate_publication_density_metadata(result)
    return result


def validate_publication_density_metadata(value: PublicationDensityMetadata) -> None:
    if not isinstance(value, PublicationDensityMetadata):
        raise RetrievalContractError("expected PublicationDensityMetadata")
    _text(value.source_id, "publication density source_id")
    _validate_timestamp(value.as_of, "publication density as_of")
    _text(value.query_scope, "publication density query_scope")
    for field_name in ("publication_count", "citation_count"):
        item = getattr(value, field_name)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise RetrievalContractError(
                f"publication density {field_name} must be nonnegative or null"
            )
    if tuple(sorted(set(value.limitations))) != value.limitations:
        raise RetrievalContractError("publication density limitations are not canonical")


def make_normalized_seed_assertion(
    *,
    assertion_locator: str,
    raw_intervention_assertion: str,
    compound_hint_kind: CompoundHintKind,
    compound_hint_value: str,
    compound_hint_namespace: str,
    endpoint_ids: Iterable[str],
    route_templates: Iterable[SeedRouteTemplate],
    evidence_modalities: Iterable[EvidenceModality],
    chemical_universes: Iterable[ChemicalUniverse],
    development_status: DevelopmentStatus,
    uncertainty: Iterable[SeedUncertainty],
    activity_observations: Iterable[SourceActivityObservation] = (),
    identity_references: Iterable[ChemicalIdentityReference] = (),
    mapping_contexts: Iterable[SourceMappingContext] = (),
    evidence_annotations: Iterable[SourceEvidenceAnnotation] = (),
    publication_density: Iterable[PublicationDensityMetadata] = (),
) -> NormalizedSeedAssertion:
    result = NormalizedSeedAssertion(
        assertion_locator=_text(assertion_locator, "assertion_locator"),
        raw_intervention_assertion=_raw_text(
            raw_intervention_assertion, "raw_intervention_assertion"
        ),
        compound_hint=make_compound_hint(
            compound_hint_kind,
            compound_hint_value,
            namespace=compound_hint_namespace,
        ),
        endpoint_ids=_strings(endpoint_ids, "endpoint_ids", required=True),
        route_templates=tuple(
            sorted(set(route_templates), key=canonical_bytes)
        ),
        evidence_modalities=tuple(
            sorted(set(evidence_modalities), key=lambda value: value.value)
        ),
        chemical_universes=tuple(
            sorted(set(chemical_universes), key=lambda value: value.value)
        ),
        development_status=development_status,
        uncertainty=tuple(
            sorted(set(uncertainty), key=lambda row: (row.kind.value, row.level.value, row.note))
        ),
        activity_observations=tuple(
            sorted(set(activity_observations), key=canonical_bytes)
        ),
        identity_references=tuple(
            sorted(set(identity_references), key=canonical_bytes)
        ),
        mapping_contexts=tuple(sorted(set(mapping_contexts), key=canonical_bytes)),
        evidence_annotations=tuple(
            sorted(set(evidence_annotations), key=canonical_bytes)
        ),
        publication_density=tuple(
            sorted(set(publication_density), key=canonical_bytes)
        ),
    )
    validate_normalized_seed_assertion(result)
    return result


def validate_normalized_seed_assertion(assertion: NormalizedSeedAssertion) -> None:
    if not isinstance(assertion, NormalizedSeedAssertion):
        raise RetrievalContractError("expected NormalizedSeedAssertion")
    _text(assertion.assertion_locator, "assertion_locator")
    _raw_text(assertion.raw_intervention_assertion, "raw_intervention_assertion")
    if not isinstance(assertion.compound_hint, CompoundHint) or not isinstance(
        assertion.compound_hint.kind, CompoundHintKind
    ):
        raise RetrievalContractError("seed assertion has an invalid compound hint")
    if not assertion.endpoint_ids or tuple(sorted(set(assertion.endpoint_ids))) != assertion.endpoint_ids:
        raise RetrievalContractError("seed assertion endpoint IDs must be nonempty and canonical")
    if not assertion.route_templates or tuple(
        sorted(set(assertion.route_templates), key=canonical_bytes)
    ) != assertion.route_templates:
        raise RetrievalContractError("seed assertion route templates must be nonempty and canonical")
    for template in assertion.route_templates:
        validate_seed_route_template(template)
        if template.endpoint_id not in assertion.endpoint_ids:
            raise RetrievalContractError("route-template endpoint is absent from the seed assertion")
    if not assertion.evidence_modalities or tuple(
        sorted(set(assertion.evidence_modalities), key=lambda value: value.value)
    ) != assertion.evidence_modalities:
        raise RetrievalContractError("evidence modalities must be nonempty and canonical")
    if any(not isinstance(value, EvidenceModality) for value in assertion.evidence_modalities):
        raise RetrievalContractError("seed assertion contains an invalid evidence modality")
    if not assertion.chemical_universes or tuple(
        sorted(set(assertion.chemical_universes), key=lambda value: value.value)
    ) != assertion.chemical_universes:
        raise RetrievalContractError("chemical universes must be nonempty and canonical")
    if any(not isinstance(value, ChemicalUniverse) for value in assertion.chemical_universes):
        raise RetrievalContractError("seed assertion contains an invalid chemical universe")
    if not isinstance(assertion.development_status, DevelopmentStatus):
        raise RetrievalContractError("seed assertion has an invalid development status")
    if not assertion.uncertainty or tuple(
        sorted(
            set(assertion.uncertainty),
            key=lambda row: (row.kind.value, row.level.value, row.note),
        )
    ) != assertion.uncertainty:
        raise RetrievalContractError("seed assertion uncertainty must be nonempty and canonical")
    if len({row.kind for row in assertion.uncertainty}) != len(assertion.uncertainty):
        raise RetrievalContractError("seed assertion uncertainty kinds must be unique")
    for row in assertion.uncertainty:
        if not isinstance(row, SeedUncertainty) or not isinstance(
            row.kind, UncertaintyKind
        ) or not isinstance(row.level, UncertaintyLevel):
            raise RetrievalContractError("seed assertion has invalid uncertainty")
        _text(row.note, "seed assertion uncertainty note")
    if tuple(sorted(set(assertion.activity_observations), key=canonical_bytes)) != assertion.activity_observations:
        raise RetrievalContractError("activity observations must be unique and canonical")
    for row in assertion.activity_observations:
        validate_source_activity_observation(row)
    if tuple(sorted(set(assertion.identity_references), key=canonical_bytes)) != assertion.identity_references:
        raise RetrievalContractError("identity references must be unique and canonical")
    for row in assertion.identity_references:
        validate_chemical_identity_reference(row)
    if tuple(sorted(set(assertion.mapping_contexts), key=canonical_bytes)) != assertion.mapping_contexts:
        raise RetrievalContractError("mapping contexts must be unique and canonical")
    for row in assertion.mapping_contexts:
        validate_source_mapping_context(row)
    if tuple(
        sorted(set(assertion.evidence_annotations), key=canonical_bytes)
    ) != assertion.evidence_annotations:
        raise RetrievalContractError("evidence annotations must be unique and canonical")
    for row in assertion.evidence_annotations:
        validate_source_evidence_annotation(row)
        if row.endpoint_id and row.endpoint_id not in assertion.endpoint_ids:
            raise RetrievalContractError(
                "evidence-annotation endpoint is absent from the seed assertion"
            )
    if tuple(
        sorted(set(assertion.publication_density), key=canonical_bytes)
    ) != assertion.publication_density:
        raise RetrievalContractError("publication density metadata must be unique and canonical")
    for row in assertion.publication_density:
        validate_publication_density_metadata(row)


def make_normalized_source_record(
    *,
    source_id: str,
    source_release: str,
    native_record_id: str,
    native_record_locator: str,
    source_record: Any,
    disposition: RecordDisposition,
    disposition_reason: str,
    screening_rule_id: str,
    seed_assertions: Iterable[NormalizedSeedAssertion] = (),
) -> NormalizedSourceRecord:
    source = _text(source_id, "source_id")
    release = _text(source_release, "source_release")
    native_id = _text(native_record_id, "native_record_id")
    assertions = tuple(sorted(set(seed_assertions), key=canonical_bytes))
    result = NormalizedSourceRecord(
        normalized_record_id=_stable_id(
            "NORMALIZED-RECORD",
            NORMALIZED_RECORD_ID_RULE,
            {
                "source_id": source,
                "source_release": release,
                "native_record_id": native_id,
            },
        ),
        source_id=source,
        source_release=release,
        native_record_id=native_id,
        native_record_locator=_text(native_record_locator, "native_record_locator"),
        source_record_sha256=content_sha256(_freeze_json(source_record, "source_record")),
        retrieval_content_receipt_id="",
        disposition=disposition,
        disposition_reason=_text(disposition_reason, "disposition_reason"),
        screening_rule_id=_text(screening_rule_id, "screening_rule_id"),
        seed_assertions=assertions,
    )
    validate_normalized_source_record(result, allow_unbound_receipt=True)
    return result


def validate_normalized_source_record(
    record: NormalizedSourceRecord, *, allow_unbound_receipt: bool = False
) -> None:
    if not isinstance(record, NormalizedSourceRecord):
        raise RetrievalContractError("expected NormalizedSourceRecord")
    for name in (
        "source_id",
        "source_release",
        "native_record_id",
        "native_record_locator",
        "disposition_reason",
        "screening_rule_id",
    ):
        _text(getattr(record, name), f"normalized record.{name}")
    expected_id = _stable_id(
        "NORMALIZED-RECORD",
        NORMALIZED_RECORD_ID_RULE,
        {
            "source_id": record.source_id,
            "source_release": record.source_release,
            "native_record_id": record.native_record_id,
        },
    )
    if record.normalized_record_id != expected_id:
        raise RetrievalContractError("normalized record ID does not match source identity")
    if not isinstance(record.source_record_sha256, str) or len(record.source_record_sha256) != 64:
        raise RetrievalContractError("normalized record lacks a SHA-256 source-record receipt")
    try:
        int(record.source_record_sha256, 16)
    except ValueError as exc:
        raise RetrievalContractError("source-record receipt is not hexadecimal") from exc
    if allow_unbound_receipt:
        if record.retrieval_content_receipt_id:
            raise RetrievalContractError("adapter-normalized records must not self-assign a receipt")
    else:
        _text(record.retrieval_content_receipt_id, "retrieval_content_receipt_id")
    if not isinstance(record.disposition, RecordDisposition):
        raise RetrievalContractError("normalized record has an invalid disposition")
    if tuple(sorted(set(record.seed_assertions), key=canonical_bytes)) != record.seed_assertions:
        raise RetrievalContractError("seed assertions must be unique and canonical")
    for assertion in record.seed_assertions:
        validate_normalized_seed_assertion(assertion)
    if record.disposition is RecordDisposition.EMITTED_SEEDS and not record.seed_assertions:
        raise RetrievalContractError("emitted_seeds requires at least one normalized assertion")
    if record.disposition is not RecordDisposition.EMITTED_SEEDS and record.seed_assertions:
        raise RetrievalContractError("non-emission dispositions cannot carry seed assertions")


def _validate_rate_limit(value: RateLimitMetadata | None, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, RateLimitMetadata):
        raise RetrievalContractError(f"{label}: expected RateLimitMetadata")
    for field_name in ("limit", "remaining"):
        item = getattr(value, field_name)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise RetrievalContractError(f"{label}.{field_name}: invalid count")
    if value.limit is not None and value.remaining is not None and value.remaining > value.limit:
        raise RetrievalContractError(f"{label}: remaining exceeds limit")
    if value.reset_at is not None:
        _validate_timestamp(value.reset_at, f"{label}.reset_at")
    if value.retry_after_seconds is not None and (
        isinstance(value.retry_after_seconds, bool)
        or not isinstance(value.retry_after_seconds, (int, float))
        or not math.isfinite(float(value.retry_after_seconds))
        or value.retry_after_seconds < 0
    ):
        raise RetrievalContractError(f"{label}.retry_after_seconds: invalid delay")


def _validate_page_response(
    request: RetrievalRequest,
    response: AdapterPageResponse,
    plan: QueryPlan,
    *,
    allow_continuation_failure: bool = False,
) -> None:
    if not isinstance(response, AdapterPageResponse):
        raise RetrievalContractError("adapter returned a non-AdapterPageResponse value")
    if response.request_sha256 != request.request_sha256:
        raise RetrievalContractError("adapter response is bound to a different request")
    if not isinstance(response.raw_response, bytes):
        raise RetrievalContractError("adapter response must retain exact response bytes")
    if isinstance(response.returned_count, bool) or not isinstance(response.returned_count, int) or response.returned_count < 0:
        raise RetrievalContractError("adapter returned_count must be a nonnegative integer")
    if response.provider_total is not None and (
        isinstance(response.provider_total, bool)
        or not isinstance(response.provider_total, int)
        or response.provider_total < 0
    ):
        raise RetrievalContractError("provider_total must be a nonnegative integer or null")
    if not isinstance(response.continuation_exhausted, bool) or not isinstance(
        response.source_limit_reached, bool
    ):
        raise RetrievalContractError("adapter continuation flags must be boolean")
    terminal_code = _text(response.terminal_code, "terminal_code")
    if terminal_code not in plan.allowed_terminal_codes:
        raise RetrievalContractError("adapter returned an undeclared terminal code")
    if (
        not allow_continuation_failure
        and response.continuation_exhausted
        and response.output_continuation_token is not None
    ):
        raise RetrievalContractError("an exhausted response cannot expose another continuation")
    if (
        not allow_continuation_failure
        and
        not response.continuation_exhausted
        and plan.source_universe.pagination_kind is not PaginationKind.NONE
        and response.output_continuation_token is None
    ):
        raise RetrievalContractError("nonterminal adapter response omitted its continuation token")
    if response.output_continuation_token is not None:
        _text(response.output_continuation_token, "output_continuation_token")
    if not allow_continuation_failure and plan.source_universe.pagination_kind is PaginationKind.NONE and (
        response.output_continuation_token is not None or not response.continuation_exhausted
    ):
        raise RetrievalContractError("non-paginated adapters must terminate in one response")
    _validate_rate_limit(response.rate_limit, "response.rate_limit")


class ContentAddressedRetrievalCache:
    """Persist exact response bytes by deterministic request hash for offline replay."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, request_sha256: str) -> Path:
        return self.root / request_sha256[:2] / f"{request_sha256}.json"

    def load(self, request: RetrievalRequest) -> AdapterPageResponse | None:
        path = self._path(request.request_sha256)
        if not path.is_file():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RetrievalContractError(f"invalid retrieval cache entry: {path}") from exc
        expected_fields = {
            "cache_schema",
            "request_sha256",
            "response_sha256",
            "raw_response_base64",
            "returned_count",
            "provider_total",
            "output_continuation_token",
            "continuation_exhausted",
            "terminal_code",
            "source_limit_reached",
            "rate_limit",
        }
        if not isinstance(stored, dict) or set(stored) != expected_fields:
            raise RetrievalContractError("retrieval cache entry has schema drift")
        if stored["cache_schema"] != "schema-v7-retrieval-cache-v1" or stored["request_sha256"] != request.request_sha256:
            raise RetrievalContractError("retrieval cache request identity mismatch")
        try:
            raw = base64.b64decode(stored["raw_response_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise RetrievalContractError("retrieval cache response is not valid base64") from exc
        if _sha256_bytes(raw) != stored["response_sha256"]:
            raise RetrievalContractError("retrieval cache response hash mismatch")
        rate_value = stored["rate_limit"]
        rate_limit = RateLimitMetadata(**rate_value) if isinstance(rate_value, dict) else None
        response = AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=raw,
            returned_count=stored["returned_count"],
            provider_total=stored["provider_total"],
            output_continuation_token=stored["output_continuation_token"],
            continuation_exhausted=stored["continuation_exhausted"],
            terminal_code=stored["terminal_code"],
            source_limit_reached=stored["source_limit_reached"],
            rate_limit=rate_limit,
        )
        return response

    def store(self, request: RetrievalRequest, response: AdapterPageResponse) -> Path:
        body = {
            "cache_schema": "schema-v7-retrieval-cache-v1",
            "request_sha256": request.request_sha256,
            "response_sha256": _sha256_bytes(response.raw_response),
            "raw_response_base64": base64.b64encode(response.raw_response).decode("ascii"),
            "returned_count": response.returned_count,
            "provider_total": response.provider_total,
            "output_continuation_token": response.output_continuation_token,
            "continuation_exhausted": response.continuation_exhausted,
            "terminal_code": response.terminal_code,
            "source_limit_reached": response.source_limit_reached,
            "rate_limit": _plain(response.rate_limit) if response.rate_limit is not None else None,
        }
        payload = canonical_bytes(body) + b"\n"
        path = self._path(request.request_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise RetrievalContractError("content-addressed retrieval cache conflict")
            return path
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != payload:
                raise RetrievalContractError("content-addressed retrieval cache conflict")
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return path


def _content_receipt(
    descriptor: AdapterDescriptor,
    plan: QueryPlan,
    request: RetrievalRequest,
    response: AdapterPageResponse,
    normalized_count: int,
    *,
    failure_code: str = "",
) -> RetrievalContentReceipt:
    failed_count = response.returned_count - normalized_count if failure_code else 0
    if failed_count < 0:
        raise RetrievalContractError("normalized count exceeds the adapter returned count")
    body = {
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.adapter_version,
        "source_id": descriptor.source_id,
        "source_release": descriptor.source_release,
        "source_universe_id": plan.source_universe.source_universe_id,
        "query_plan_id": plan.query_plan_id,
        "query_family_id": plan.query_family_id,
        "page_ordinal": request.page_ordinal,
        "input_continuation_token": request.input_continuation_token,
        "output_continuation_token": response.output_continuation_token,
        "exact_request_parameters": request.exact_request_parameters,
        "request_sha256": request.request_sha256,
        "response_sha256": _sha256_bytes(response.raw_response),
        "returned_count": response.returned_count,
        "normalized_record_count": normalized_count,
        "failed_record_count": failed_count,
        "provider_total": response.provider_total,
        "continuation_exhausted": response.continuation_exhausted,
        "terminal_code": response.terminal_code,
        "source_limit_reached": response.source_limit_reached,
        "receipt_status": "failed" if failure_code else "accepted",
        "failure_code": failure_code,
    }
    return RetrievalContentReceipt(
        content_receipt_id=_stable_id(
            "RETRIEVAL-CONTENT", CONTENT_RECEIPT_ID_RULE, body
        ),
        **body,
    )


def _execution_receipt(**body: Any) -> RetrievalExecutionReceipt:
    return RetrievalExecutionReceipt(
        execution_receipt_id=_stable_id(
            "RETRIEVAL-EXECUTION", EXECUTION_RECEIPT_ID_RULE, body
        ),
        **body,
    )


def _retry_delay(policy: RetryPolicy, attempt_number: int, error: AdapterTransportError) -> float:
    if attempt_number >= policy.max_attempts:
        return 0.0
    planned = float(policy.backoff_seconds[attempt_number - 1])
    if error.retry_after_seconds is None:
        return planned
    return max(planned, float(error.retry_after_seconds))


def _map_record_to_seed_emissions(
    case: CaseRevision,
    plan: QueryPlan,
    record: NormalizedSourceRecord,
) -> tuple[SeedEmissionLink, ...]:
    if record.disposition is not RecordDisposition.EMITTED_SEEDS:
        return ()
    links: list[SeedEmissionLink] = []
    seen_locators: set[str] = set()
    for assertion in record.seed_assertions:
        if assertion.assertion_locator in seen_locators:
            raise RetrievalContractError(
                f"normalized record {record.normalized_record_id} repeats an assertion locator"
            )
        seen_locators.add(assertion.assertion_locator)
        mapping = make_source_mapping(
            case,
            source_id=record.source_id,
            source_release=record.source_release,
            native_record_id=record.native_record_id,
            assertion_locator=assertion.assertion_locator,
            raw_intervention_assertion=assertion.raw_intervention_assertion,
        )
        discovery_route = make_discovery_route(
            mapping,
            query_id=plan.query_plan_id,
            query_record_locator=record.native_record_locator,
            retrieval_content_receipt_id=record.retrieval_content_receipt_id,
        )
        structural_routes = tuple(
            make_structured_route(
                case_revision_id=case.case_revision_id,
                intervention_id=mapping.seed_id,
                causal_route=template.causal_route,
                disease_state_node=template.disease_state_node,
                intervention_target=template.intervention_target,
                action=template.action,
                direction=template.direction,
                intermediate_state=template.intermediate_state,
                endpoint_id=template.endpoint_id,
                evidence_ids=template.evidence_ids,
            )
            for template in assertion.route_templates
        )
        development_status = (
            unknown_development_status("The normalized source record reported no development status.")
            if assertion.development_status is DevelopmentStatus.UNKNOWN
            else known_development_status(assertion.development_status)
        )
        seed = make_candidate_seed(
            case,
            mapping,
            endpoint_ids=assertion.endpoint_ids,
            compound_hint=assertion.compound_hint,
            discovery_route_ids=(discovery_route.route_id,),
            structured_routes=structural_routes,
            evidence_modalities=assertion.evidence_modalities,
            chemical_universes=assertion.chemical_universes,
            development_status_hint=development_status,
            identity_status=SeedIdentityStatus.UNASSESSED,
            uncertainty=assertion.uncertainty,
        )
        body = {
            "normalized_record_id": record.normalized_record_id,
            "assertion_locator": assertion.assertion_locator,
            "source_mapping_id": mapping.mapping_id,
            "discovery_route_id": discovery_route.route_id,
            "seed_id": seed.seed_id,
        }
        links.append(
            SeedEmissionLink(
                emission_link_id=_stable_id(
                    "SEED-EMISSION-LINK", SEED_EMISSION_LINK_ID_RULE, body
                ),
                normalized_record_id=record.normalized_record_id,
                assertion_locator=assertion.assertion_locator,
                source_mapping=mapping,
                discovery_route=discovery_route,
                seed=seed,
            )
        )
    return tuple(sorted(links, key=lambda row: row.emission_link_id))


def _screen_records(
    case: CaseRevision,
    plan: QueryPlan,
    records: tuple[NormalizedSourceRecord, ...],
) -> tuple[tuple[RecordScreeningDisposition, ...], tuple[SeedEmissionLink, ...]]:
    dispositions: list[RecordScreeningDisposition] = []
    emissions: list[SeedEmissionLink] = []
    for record in records:
        links = _map_record_to_seed_emissions(case, plan, record)
        emissions.extend(links)
        dispositions.append(
            RecordScreeningDisposition(
                normalized_record_id=record.normalized_record_id,
                disposition=record.disposition,
                reason=record.disposition_reason,
                screening_rule_id=record.screening_rule_id,
                seed_ids=tuple(sorted(link.seed.seed_id for link in links)),
                emission_link_ids=tuple(sorted(link.emission_link_id for link in links)),
            )
        )
    return (
        tuple(sorted(dispositions, key=lambda row: row.normalized_record_id)),
        tuple(sorted(emissions, key=lambda row: row.emission_link_id)),
    )


def _provider_total(
    plan: QueryPlan,
    receipts: tuple[RetrievalContentReceipt, ...],
) -> tuple[int | None, bool, tuple[str, ...]]:
    reported = {row.provider_total for row in receipts if row.provider_total is not None}
    gaps: list[str] = []
    consistent = len(reported) <= 1
    if not consistent:
        gaps.append("The source reported inconsistent total-result counts across pages.")
    provider_total = next(iter(reported)) if len(reported) == 1 else None
    universe = plan.source_universe
    if universe.denominator_kind is DenominatorKind.EXACT_DECLARED:
        source_total = universe.declared_total
        if provider_total is not None and provider_total != source_total:
            consistent = False
            gaps.append("The provider total conflicts with the exact declared source denominator.")
    else:
        source_total = provider_total
    return source_total, consistent, tuple(gaps)


def _coverage_reconciliation(
    plan: QueryPlan,
    receipts: tuple[RetrievalContentReceipt, ...],
    records: tuple[NormalizedSourceRecord, ...],
    dispositions: tuple[RecordScreeningDisposition, ...],
    emissions: tuple[SeedEmissionLink, ...],
    *,
    continuation_exhausted: bool,
    next_continuation_token: str | None,
) -> tuple[CoverageReconciliation, tuple[str, ...]]:
    source_total, total_consistent, total_gaps = _provider_total(plan, receipts)
    returned_count = sum(row.returned_count for row in receipts)
    failed_count = sum(row.failed_record_count for row in receipts)
    internal_ok = (
        returned_count == len(records) + failed_count
        and len(records) == len(dispositions)
        and len({row.normalized_record_id for row in records}) == len(records)
        and len({row.seed.seed_id for row in emissions}) == len(emissions)
    )
    if source_total is None:
        unvisited = None
        total_possible = True
    elif source_total >= len(records) + failed_count:
        unvisited = source_total - len(records) - failed_count
        total_possible = True
    else:
        unvisited = None
        total_possible = False
    counts: dict[RecordDisposition, int] = {value: 0 for value in RecordDisposition}
    for row in dispositions:
        counts[row.disposition] += 1
    result = CoverageReconciliation(
        source_reported_total=source_total,
        retrieved_page_count=len(receipts),
        returned_native_record_count=returned_count,
        normalized_record_count=len(records),
        failed_record_count=failed_count,
        screened_record_count=len(dispositions),
        emitted_seed_count=len(emissions),
        unvisited_record_count=unvisited,
        disposition_counts=tuple(
            DispositionCount(disposition=value, count=counts[value])
            for value in sorted(RecordDisposition, key=lambda item: item.value)
        ),
        continuation_exhausted=continuation_exhausted,
        next_continuation_token=next_continuation_token,
        count_reconciliation_ok=internal_ok and total_consistent and total_possible,
    )
    gaps = list(total_gaps)
    if not internal_ok:
        gaps.append("Retrieved, normalized, screened, or emitted counts do not reconcile.")
    if not total_possible:
        gaps.append("The normalized-record count exceeds the source-reported denominator.")
    return result, tuple(gaps)


def _proof_projection(proof: CoverageProof) -> dict[str, Any]:
    return {
        "schema_version": proof.schema_version,
        "model_version": proof.model_version,
        "adapter": proof.adapter,
        "query_plan": proof.query_plan,
        "coverage_state": proof.coverage_state,
        "content_receipts": proof.content_receipts,
        "normalized_records": proof.normalized_records,
        "screening_dispositions": proof.screening_dispositions,
        "seed_emissions": proof.seed_emissions,
        "reconciliation": proof.reconciliation,
        "source_specific_limitations": proof.source_specific_limitations,
        "coverage_gaps": proof.coverage_gaps,
    }


def _build_proof(
    descriptor: AdapterDescriptor,
    plan: QueryPlan,
    state: CoverageState,
    *,
    content_receipts: Iterable[RetrievalContentReceipt] = (),
    execution_receipts: Iterable[RetrievalExecutionReceipt] = (),
    normalized_records: Iterable[NormalizedSourceRecord] = (),
    screening_dispositions: Iterable[RecordScreeningDisposition] = (),
    seed_emissions: Iterable[SeedEmissionLink] = (),
    continuation_exhausted: bool,
    next_continuation_token: str | None,
    coverage_gaps: Iterable[str] = (),
) -> CoverageProof:
    contents = tuple(sorted(content_receipts, key=lambda row: row.page_ordinal))
    executions = tuple(
        sorted(
            execution_receipts,
            key=lambda row: (row.page_ordinal, row.attempt_number, row.execution_receipt_id),
        )
    )
    records = tuple(sorted(normalized_records, key=lambda row: row.normalized_record_id))
    dispositions = tuple(
        sorted(screening_dispositions, key=lambda row: row.normalized_record_id)
    )
    emissions = tuple(sorted(seed_emissions, key=lambda row: row.emission_link_id))
    reconciliation, count_gaps = _coverage_reconciliation(
        plan,
        contents,
        records,
        dispositions,
        emissions,
        continuation_exhausted=continuation_exhausted,
        next_continuation_token=next_continuation_token,
    )
    gaps = _strings(tuple(coverage_gaps) + count_gaps, "coverage_gaps")
    if not reconciliation.count_reconciliation_ok and state in {
        CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
        CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY,
    }:
        state = CoverageState.FAILED_RETRIEVAL
        gaps = _strings(
            gaps + ("Complete coverage was refused because count reconciliation failed.",),
            "coverage_gaps",
        )
    draft = CoverageProof(
        schema_version=SCHEMA_VERSION,
        model_version=RETRIEVAL_ADAPTER_MODEL_VERSION,
        coverage_proof_id="",
        execution_trace_id=content_sha256(executions),
        adapter=descriptor,
        query_plan=plan,
        coverage_state=state,
        content_receipts=contents,
        execution_receipts=executions,
        normalized_records=records,
        screening_dispositions=dispositions,
        seed_emissions=emissions,
        reconciliation=reconciliation,
        source_specific_limitations=plan.source_universe.limitations,
        coverage_gaps=gaps,
    )
    proof = replace(
        draft,
        coverage_proof_id=_stable_id(
            "COVERAGE-PROOF", COVERAGE_PROOF_ID_RULE, _proof_projection(draft)
        ),
    )
    validate_coverage_proof(case=None, proof=proof)
    return proof


def not_yet_searched_proof(
    descriptor: AdapterDescriptor, plan: QueryPlan
) -> CoverageProof:
    """Materialize an explicit pre-retrieval state without implying closure."""

    return _build_proof(
        descriptor,
        plan,
        CoverageState.NOT_YET_SEARCHED,
        continuation_exhausted=False,
        next_continuation_token=plan.initial_continuation_token,
        coverage_gaps=("The declared query plan has not yet been executed.",),
    )


def execute_query_plan(
    case: CaseRevision,
    plan: QueryPlan,
    adapter: RetrievalAdapter,
    *,
    cache: ContentAddressedRetrievalCache | None = None,
    replay_only: bool = False,
    clock: Callable[[], str] = _utc_now,
    sleeper: Callable[[float], None] = time.sleep,
) -> CoverageProof:
    """Execute one declared plan and derive, never accept, its bounded state."""

    validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise RetrievalContractError("retrieval and seed emission require a READY case")
    validate_query_plan(plan)
    if not isinstance(adapter, RetrievalAdapter):
        raise RetrievalContractError("adapter does not implement the retrieval protocol")
    descriptor = adapter.descriptor
    if not isinstance(descriptor, AdapterDescriptor):
        raise RetrievalContractError("adapter descriptor is malformed")
    required_capability = f"pagination:{plan.source_universe.pagination_kind.value}"
    supported, support_reason = adapter.supports(plan)
    descriptor_matches = (
        descriptor.source_id == plan.source_universe.source_id
        and descriptor.source_release == plan.source_universe.source_release
        and required_capability in descriptor.capabilities
    )
    if not supported or not descriptor_matches:
        reason = support_reason or "The adapter does not support the declared source/query capability."
        return _build_proof(
            descriptor,
            plan,
            CoverageState.UNSUPPORTED_SOURCE_CAPABILITY,
            continuation_exhausted=False,
            next_continuation_token=plan.initial_continuation_token,
            coverage_gaps=(reason,),
        )

    contents: list[RetrievalContentReceipt] = []
    executions: list[RetrievalExecutionReceipt] = []
    normalized: list[NormalizedSourceRecord] = []
    record_ids: set[str] = set()
    native_identities: set[tuple[str, str, str]] = set()
    seen_continuations: set[str] = set()
    current_token = plan.initial_continuation_token
    page_ordinal = 1
    traversal_exhausted = False
    stopped_for_source_limit = False
    stopped_for_rate_limit = False
    failed = False
    gaps: list[str] = []

    while True:
        request = make_retrieval_request(
            descriptor,
            plan,
            page_ordinal=page_ordinal,
            input_continuation_token=current_token,
        )
        page_succeeded = False
        for attempt_number in range(1, plan.retry_policy.max_attempts + 1):
            started_at = clock()
            _validate_timestamp(started_at, "retrieval started_at")
            cache_hit = False
            response: AdapterPageResponse | None = None
            response_capturable = False
            try:
                response = cache.load(request) if cache is not None else None
                if response is not None:
                    cache_hit = True
                elif replay_only:
                    raise CacheMissError(
                        f"No cached response exists for request {request.request_sha256}"
                    )
                else:
                    response = adapter.retrieve(request)
                _validate_page_response(
                    request,
                    response,
                    plan,
                    allow_continuation_failure=True,
                )
                response_capturable = True
                _validate_page_response(request, response, plan)
                page_records = adapter.normalize(request, response)
                if not isinstance(page_records, tuple):
                    raise RetrievalContractError("adapter.normalize must return an immutable tuple")
                if len(page_records) != response.returned_count:
                    raise RetrievalContractError(
                        "adapter returned_count does not equal normalized page records"
                    )
                if len({row.normalized_record_id for row in page_records}) != len(page_records):
                    raise RetrievalContractError("adapter page repeats a normalized record")
                for row in page_records:
                    validate_normalized_source_record(row, allow_unbound_receipt=True)
                    if (
                        row.source_id != descriptor.source_id
                        or row.source_release != descriptor.source_release
                    ):
                        raise RetrievalContractError(
                            "adapter normalized a record outside its declared source release"
                        )
                receipt = _content_receipt(
                    descriptor, plan, request, response, len(page_records)
                )
                bound_records = tuple(
                    replace(row, retrieval_content_receipt_id=receipt.content_receipt_id)
                    for row in page_records
                )
                for row in bound_records:
                    validate_normalized_source_record(row)
                    native_identity = (row.source_id, row.source_release, row.native_record_id)
                    if row.normalized_record_id in record_ids or native_identity in native_identities:
                        raise RetrievalContractError(
                            "pagination repeated a source record instead of reconciling one traversal"
                        )
                if cache is not None and not cache_hit:
                    cache.store(request, response)
                completed_at = clock()
                _validate_timestamp(completed_at, "retrieval completed_at")
                executions.append(
                    _execution_receipt(
                        query_plan_id=plan.query_plan_id,
                        page_ordinal=page_ordinal,
                        input_continuation_token=current_token,
                        request_sha256=request.request_sha256,
                        attempt_number=attempt_number,
                        max_attempts=plan.retry_policy.max_attempts,
                        started_at=started_at,
                        completed_at=completed_at,
                        outcome="success",
                        retryable=False,
                        retry_delay_seconds=0.0,
                        error_code="",
                        error_message="",
                        rate_limit=response.rate_limit,
                        cache_hit=cache_hit,
                        content_receipt_id=receipt.content_receipt_id,
                    )
                )
                contents.append(receipt)
                for row in bound_records:
                    record_ids.add(row.normalized_record_id)
                    native_identities.add((row.source_id, row.source_release, row.native_record_id))
                normalized.extend(bound_records)
                page_succeeded = True
                traversal_exhausted = response.continuation_exhausted
                current_token = response.output_continuation_token
                if response.source_limit_reached:
                    stopped_for_source_limit = True
                    traversal_exhausted = False
                    gaps.append("The source reported a cap or truncation before query exhaustion.")
                break
            except AdapterTransportError as exc:
                completed_at = clock()
                delay = _retry_delay(plan.retry_policy, attempt_number, exc)
                executions.append(
                    _execution_receipt(
                        query_plan_id=plan.query_plan_id,
                        page_ordinal=page_ordinal,
                        input_continuation_token=current_token,
                        request_sha256=request.request_sha256,
                        attempt_number=attempt_number,
                        max_attempts=plan.retry_policy.max_attempts,
                        started_at=started_at,
                        completed_at=completed_at,
                        outcome="rate_limited" if exc.rate_limited else "transport_error",
                        retryable=exc.retryable,
                        retry_delay_seconds=delay,
                        error_code=_text(exc.code, "transport error code"),
                        error_message=_text(str(exc), "transport error message"),
                        rate_limit=exc.rate_limit,
                        cache_hit=False,
                        content_receipt_id=None,
                    )
                )
                if exc.retryable and attempt_number < plan.retry_policy.max_attempts:
                    sleeper(delay)
                    continue
                if exc.rate_limited:
                    stopped_for_rate_limit = True
                    gaps.append("Rate limiting remained after the declared retry policy was exhausted.")
                else:
                    failed = True
                    gaps.append(f"Retrieval failed with {exc.code}: {exc}")
                break
            except (CacheMissError, RetrievalContractError, ValueError, TypeError, json.JSONDecodeError) as exc:
                completed_at = clock()
                failed_content_receipt_id: str | None = None
                failed_rate_limit: RateLimitMetadata | None = None
                if response_capturable and response is not None:
                    failed_receipt = _content_receipt(
                        descriptor,
                        plan,
                        request,
                        response,
                        0,
                        failure_code="NORMALIZATION_OR_PAGE_CONTRACT_FAILED",
                    )
                    if cache is not None and not cache_hit:
                        cache.store(request, response)
                    contents.append(failed_receipt)
                    failed_content_receipt_id = failed_receipt.content_receipt_id
                    failed_rate_limit = response.rate_limit
                executions.append(
                    _execution_receipt(
                        query_plan_id=plan.query_plan_id,
                        page_ordinal=page_ordinal,
                        input_continuation_token=current_token,
                        request_sha256=request.request_sha256,
                        attempt_number=attempt_number,
                        max_attempts=plan.retry_policy.max_attempts,
                        started_at=started_at,
                        completed_at=completed_at,
                        outcome="contract_error",
                        retryable=False,
                        retry_delay_seconds=0.0,
                        error_code="MALFORMED_OR_UNREPLAYABLE_RESPONSE",
                        error_message=_text(str(exc), "adapter contract error"),
                        rate_limit=failed_rate_limit,
                        cache_hit=cache_hit,
                        content_receipt_id=failed_content_receipt_id,
                    )
                )
                failed = True
                gaps.append(str(exc))
                break

        if not page_succeeded:
            break
        if stopped_for_source_limit or traversal_exhausted:
            break
        if current_token is None:
            failed = True
            gaps.append("The adapter omitted a required continuation token.")
            break
        if current_token in seen_continuations:
            failed = True
            gaps.append("The adapter produced a continuation loop.")
            break
        seen_continuations.add(current_token)
        reached_page_bound = plan.max_pages is not None and len(contents) >= plan.max_pages
        reached_record_bound = plan.max_records is not None and len(normalized) >= plan.max_records
        reached_source_cap = (
            plan.source_universe.source_record_cap is not None
            and len(normalized) >= plan.source_universe.source_record_cap
        )
        if reached_page_bound or reached_record_bound or reached_source_cap:
            stopped_for_source_limit = True
            gaps.append("The declared local/source traversal bound stopped before cursor exhaustion.")
            break
        page_ordinal += 1

    normalized_rows = tuple(sorted(normalized, key=lambda row: row.normalized_record_id))
    dispositions, emissions = _screen_records(case, plan, normalized_rows)
    if any(row.disposition is RecordDisposition.FAILED_MAPPING for row in normalized_rows):
        failed = True
        gaps.append("At least one normalized source record failed mapping evaluation.")
    if stopped_for_rate_limit:
        state = CoverageState.PARTIAL_DUE_TO_RATE_LIMIT
    elif stopped_for_source_limit:
        state = CoverageState.PARTIAL_DUE_TO_SOURCE_LIMIT
    elif failed:
        state = CoverageState.FAILED_RETRIEVAL
    elif traversal_exhausted:
        state = (
            CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE
            if emissions
            else CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY
        )
    else:
        state = CoverageState.FAILED_RETRIEVAL
        gaps.append("Retrieval ended without an adapter-specific terminal condition.")

    proof = _build_proof(
        descriptor,
        plan,
        state,
        content_receipts=contents,
        execution_receipts=executions,
        normalized_records=normalized_rows,
        screening_dispositions=dispositions,
        seed_emissions=emissions,
        continuation_exhausted=traversal_exhausted and not stopped_for_source_limit,
        next_continuation_token=None if traversal_exhausted else current_token,
        coverage_gaps=gaps,
    )
    validate_coverage_proof(case, proof)
    return proof


def _validate_descriptor(descriptor: AdapterDescriptor) -> None:
    rebuilt = make_adapter_descriptor(
        adapter_id=descriptor.adapter_id,
        adapter_version=descriptor.adapter_version,
        source_id=descriptor.source_id,
        source_release=descriptor.source_release,
        capabilities=descriptor.capabilities,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(descriptor):
        raise RetrievalContractError("adapter descriptor is not canonical")


def _validate_content_receipt(
    receipt: RetrievalContentReceipt,
    descriptor: AdapterDescriptor,
    plan: QueryPlan,
) -> None:
    if not isinstance(receipt, RetrievalContentReceipt):
        raise RetrievalContractError("coverage proof has an invalid content receipt")
    expected_links = {
        "adapter_id": descriptor.adapter_id,
        "adapter_version": descriptor.adapter_version,
        "source_id": descriptor.source_id,
        "source_release": descriptor.source_release,
        "source_universe_id": plan.source_universe.source_universe_id,
        "query_plan_id": plan.query_plan_id,
        "query_family_id": plan.query_family_id,
    }
    if any(getattr(receipt, key) != value for key, value in expected_links.items()):
        raise RetrievalContractError("content receipt is relabeled to another plan/source/family")
    request = make_retrieval_request(
        descriptor,
        plan,
        page_ordinal=receipt.page_ordinal,
        input_continuation_token=receipt.input_continuation_token,
    )
    if (
        receipt.request_sha256 != request.request_sha256
        or canonical_bytes(receipt.exact_request_parameters)
        != canonical_bytes(request.exact_request_parameters)
    ):
        raise RetrievalContractError("content receipt request parameters/hash do not match the plan")
    for value, label in (
        (receipt.returned_count, "returned_count"),
        (receipt.normalized_record_count, "normalized_record_count"),
        (receipt.failed_record_count, "failed_record_count"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RetrievalContractError(f"content receipt {label} is invalid")
    if receipt.returned_count != receipt.normalized_record_count + receipt.failed_record_count:
        raise RetrievalContractError("content receipt returned/normalized/failed counts differ")
    if receipt.receipt_status not in {"accepted", "failed"}:
        raise RetrievalContractError("content receipt has an invalid status")
    if receipt.receipt_status == "accepted":
        if receipt.failure_code or receipt.failed_record_count:
            raise RetrievalContractError("accepted content receipt carries a failure")
    elif not receipt.failure_code or not receipt.failed_record_count:
        raise RetrievalContractError("failed content receipt lacks failure accounting")
    if receipt.provider_total is not None and (
        isinstance(receipt.provider_total, bool)
        or not isinstance(receipt.provider_total, int)
        or receipt.provider_total < 0
    ):
        raise RetrievalContractError("content receipt provider total is invalid")
    if len(receipt.response_sha256) != 64:
        raise RetrievalContractError("content receipt response hash is malformed")
    try:
        int(receipt.response_sha256, 16)
    except ValueError as exc:
        raise RetrievalContractError("content receipt response hash is not hexadecimal") from exc
    if receipt.terminal_code not in plan.allowed_terminal_codes:
        raise RetrievalContractError("content receipt terminal code was not predeclared")
    if (
        receipt.receipt_status == "accepted"
        and receipt.continuation_exhausted
        and receipt.output_continuation_token is not None
    ):
        raise RetrievalContractError("exhausted content receipt retains a continuation")
    if (
        receipt.receipt_status == "accepted"
        and
        not receipt.continuation_exhausted
        and plan.source_universe.pagination_kind is not PaginationKind.NONE
        and receipt.output_continuation_token is None
    ):
        raise RetrievalContractError("nonterminal content receipt omitted its continuation")
    expected_id = _stable_id(
        "RETRIEVAL-CONTENT",
        CONTENT_RECEIPT_ID_RULE,
        _record_body(receipt, "content_receipt_id"),
    )
    if receipt.content_receipt_id != expected_id:
        raise RetrievalContractError("content receipt ID does not match exact request/response facts")


def _validate_execution_receipt(
    receipt: RetrievalExecutionReceipt,
    descriptor: AdapterDescriptor,
    plan: QueryPlan,
    content_by_id: Mapping[str, RetrievalContentReceipt],
) -> None:
    if not isinstance(receipt, RetrievalExecutionReceipt):
        raise RetrievalContractError("coverage proof has an invalid execution receipt")
    request = make_retrieval_request(
        descriptor,
        plan,
        page_ordinal=receipt.page_ordinal,
        input_continuation_token=receipt.input_continuation_token,
    )
    if receipt.query_plan_id != plan.query_plan_id or receipt.request_sha256 != request.request_sha256:
        raise RetrievalContractError("execution receipt is bound to another request/plan")
    if (
        receipt.max_attempts != plan.retry_policy.max_attempts
        or isinstance(receipt.attempt_number, bool)
        or not isinstance(receipt.attempt_number, int)
        or not 1 <= receipt.attempt_number <= receipt.max_attempts
    ):
        raise RetrievalContractError("execution receipt retry metadata is invalid")
    _validate_timestamp(receipt.started_at, "execution receipt started_at")
    _validate_timestamp(receipt.completed_at, "execution receipt completed_at")
    if (
        isinstance(receipt.retry_delay_seconds, bool)
        or not isinstance(receipt.retry_delay_seconds, (int, float))
        or not math.isfinite(float(receipt.retry_delay_seconds))
        or receipt.retry_delay_seconds < 0
    ):
        raise RetrievalContractError("execution receipt retry delay is invalid")
    _validate_rate_limit(receipt.rate_limit, "execution receipt rate_limit")
    allowed_outcomes = {"success", "transport_error", "rate_limited", "contract_error"}
    if receipt.outcome not in allowed_outcomes:
        raise RetrievalContractError("execution receipt has an invalid outcome")
    if receipt.outcome == "success":
        if (
            receipt.content_receipt_id not in content_by_id
            or receipt.error_code
            or receipt.error_message
            or receipt.retryable
            or receipt.retry_delay_seconds != 0
        ):
            raise RetrievalContractError("successful execution receipt is internally inconsistent")
        content = content_by_id[receipt.content_receipt_id or ""]
        if content.request_sha256 != receipt.request_sha256 or content.receipt_status != "accepted":
            raise RetrievalContractError("execution/content receipts disagree on request hash")
    else:
        if not receipt.error_code or not receipt.error_message:
            raise RetrievalContractError("failed execution receipt lacks exact failure metadata")
        if receipt.content_receipt_id is not None:
            content = content_by_id.get(receipt.content_receipt_id)
            if (
                content is None
                or content.request_sha256 != receipt.request_sha256
                or content.receipt_status != "failed"
            ):
                raise RetrievalContractError("failed execution links an invalid content-failure receipt")
        if receipt.cache_hit and receipt.content_receipt_id is None:
            raise RetrievalContractError("a failed cache replay lacks a retained response receipt")
        if receipt.outcome == "rate_limited" and receipt.rate_limit is None:
            raise RetrievalContractError("rate-limited execution lacks rate-limit metadata")
    expected_id = _stable_id(
        "RETRIEVAL-EXECUTION",
        EXECUTION_RECEIPT_ID_RULE,
        _record_body(receipt, "execution_receipt_id"),
    )
    if receipt.execution_receipt_id != expected_id:
        raise RetrievalContractError("execution receipt ID does not match attempt metadata")


def _validate_seed_emission(
    case: CaseRevision | None,
    plan: QueryPlan,
    record: NormalizedSourceRecord,
    link: SeedEmissionLink,
) -> None:
    if not isinstance(link, SeedEmissionLink):
        raise RetrievalContractError("coverage proof has an invalid seed-emission link")
    if link.normalized_record_id != record.normalized_record_id:
        raise RetrievalContractError("seed emission is linked to another normalized record")
    assertions = {
        row.assertion_locator: row for row in record.seed_assertions
    }
    assertion = assertions.get(link.assertion_locator)
    if assertion is None:
        raise RetrievalContractError("seed emission has no matching normalized assertion")
    mapping = link.source_mapping
    route = link.discovery_route
    seed = link.seed
    if (
        mapping.source_id != record.source_id
        or mapping.source_release != record.source_release
        or mapping.native_record_id != record.native_record_id
        or mapping.assertion_locator != assertion.assertion_locator
        or mapping.raw_intervention_assertion != assertion.raw_intervention_assertion
    ):
        raise RetrievalContractError("seed mapping differs from its normalized source assertion")
    if (
        route.seed_id != mapping.seed_id
        or route.source_mapping_id != mapping.mapping_id
        or route.query_id != plan.query_plan_id
        or route.query_record_locator != record.native_record_locator
        or route.retrieval_content_receipt_id != record.retrieval_content_receipt_id
    ):
        raise RetrievalContractError("seed discovery route was relabeled or lost receipt lineage")
    if (
        seed.seed_id != mapping.seed_id
        or seed.source_mapping_id != mapping.mapping_id
        or seed.discovery_route_ids != (route.route_id,)
        or seed.compound_hint != assertion.compound_hint
        or seed.endpoint_ids != assertion.endpoint_ids
        or seed.evidence_modalities != assertion.evidence_modalities
        or seed.chemical_universes != assertion.chemical_universes
        or seed.identity_status is not SeedIdentityStatus.UNASSESSED
    ):
        raise RetrievalContractError("emitted seed differs from the normalized assertion")
    if case is not None and (
        mapping.case_id != case.case_id
        or mapping.case_revision_id != case.case_revision_id
        or seed.case_id != case.case_id
        or seed.case_revision_id != case.case_revision_id
    ):
        raise RetrievalContractError("emitted seed belongs to another case revision")
    expected_link_id = _stable_id(
        "SEED-EMISSION-LINK",
        SEED_EMISSION_LINK_ID_RULE,
        {
            "normalized_record_id": record.normalized_record_id,
            "assertion_locator": link.assertion_locator,
            "source_mapping_id": mapping.mapping_id,
            "discovery_route_id": route.route_id,
            "seed_id": seed.seed_id,
        },
    )
    if link.emission_link_id != expected_link_id:
        raise RetrievalContractError("seed-emission link ID mismatch")


def validate_coverage_proof(
    case: CaseRevision | None, proof: CoverageProof
) -> None:
    """Reconcile traversal, records, dispositions, seeds, and terminal state."""

    if not isinstance(proof, CoverageProof):
        raise RetrievalContractError("expected CoverageProof")
    if proof.schema_version != SCHEMA_VERSION or proof.model_version != RETRIEVAL_ADAPTER_MODEL_VERSION:
        raise RetrievalContractError("coverage proof schema/model version mismatch")
    if case is not None:
        validate_case_revision(case)
    _validate_descriptor(proof.adapter)
    validate_query_plan(proof.query_plan)
    if proof.source_specific_limitations != proof.query_plan.source_universe.limitations:
        raise RetrievalContractError("coverage proof dropped or rewrote source limitations")
    if tuple(sorted(set(proof.coverage_gaps))) != proof.coverage_gaps:
        raise RetrievalContractError("coverage gaps must be unique and canonical")

    contents = proof.content_receipts
    if tuple(sorted(contents, key=lambda row: row.page_ordinal)) != contents:
        raise RetrievalContractError("content receipts are not in page order")
    if len({row.content_receipt_id for row in contents}) != len(contents):
        raise RetrievalContractError("content receipts are duplicated")
    prior_output = proof.query_plan.initial_continuation_token
    for expected_ordinal, receipt in enumerate(contents, 1):
        _validate_content_receipt(receipt, proof.adapter, proof.query_plan)
        if receipt.page_ordinal != expected_ordinal:
            raise RetrievalContractError("content receipt page ordinals contain a gap")
        if receipt.input_continuation_token != prior_output:
            raise RetrievalContractError("content receipt continuation chain is disconnected")
        prior_output = receipt.output_continuation_token
    content_by_id = {row.content_receipt_id: row for row in contents}

    executions = proof.execution_receipts
    if tuple(
        sorted(
            executions,
            key=lambda row: (row.page_ordinal, row.attempt_number, row.execution_receipt_id),
        )
    ) != executions:
        raise RetrievalContractError("execution receipts are not canonically ordered")
    if len({row.execution_receipt_id for row in executions}) != len(executions):
        raise RetrievalContractError("execution receipts are duplicated")
    attempts_by_page: dict[int, list[RetrievalExecutionReceipt]] = {}
    for receipt in executions:
        _validate_execution_receipt(receipt, proof.adapter, proof.query_plan, content_by_id)
        attempts_by_page.setdefault(receipt.page_ordinal, []).append(receipt)
    for page, attempts in attempts_by_page.items():
        if [row.attempt_number for row in attempts] != list(range(1, len(attempts) + 1)):
            raise RetrievalContractError(f"page {page} retry attempts are not contiguous")
        successes = [row for row in attempts if row.outcome == "success"]
        if len(successes) > 1 or (successes and successes[-1] is not attempts[-1]):
            raise RetrievalContractError(f"page {page} has an invalid retry/success sequence")
    for receipt in contents:
        matching = [
            row
            for row in executions
            if row.content_receipt_id == receipt.content_receipt_id
            and (
                (receipt.receipt_status == "accepted" and row.outcome == "success")
                or (receipt.receipt_status == "failed" and row.outcome == "contract_error")
            )
        ]
        if len(matching) != 1:
            raise RetrievalContractError("each content receipt requires one matching execution outcome")

    records = proof.normalized_records
    if tuple(sorted(records, key=lambda row: row.normalized_record_id)) != records:
        raise RetrievalContractError("normalized records are not canonically ordered")
    if len({row.normalized_record_id for row in records}) != len(records):
        raise RetrievalContractError("normalized records are duplicated")
    if len({(row.source_id, row.source_release, row.native_record_id) for row in records}) != len(records):
        raise RetrievalContractError("one native source record was emitted more than once")
    records_by_receipt: dict[str, int] = {key: 0 for key in content_by_id}
    for record in records:
        validate_normalized_source_record(record)
        if (
            record.source_id != proof.adapter.source_id
            or record.source_release != proof.adapter.source_release
            or record.retrieval_content_receipt_id not in content_by_id
        ):
            raise RetrievalContractError("normalized record source/receipt lineage is invalid")
        records_by_receipt[record.retrieval_content_receipt_id] += 1
    for receipt in contents:
        if records_by_receipt[receipt.content_receipt_id] != receipt.normalized_record_count:
            raise RetrievalContractError("receipt normalized count does not match linked records")

    dispositions = proof.screening_dispositions
    if tuple(sorted(dispositions, key=lambda row: row.normalized_record_id)) != dispositions:
        raise RetrievalContractError("screening dispositions are not canonically ordered")
    disposition_by_record: dict[str, RecordScreeningDisposition] = {}
    for row in dispositions:
        if row.normalized_record_id in disposition_by_record:
            raise RetrievalContractError("a normalized record has multiple screening dispositions")
        disposition_by_record[row.normalized_record_id] = row
    record_by_id = {row.normalized_record_id: row for row in records}
    if set(disposition_by_record) != set(record_by_id):
        raise RetrievalContractError("every normalized record requires exactly one screening disposition")

    emissions = proof.seed_emissions
    if tuple(sorted(emissions, key=lambda row: row.emission_link_id)) != emissions:
        raise RetrievalContractError("seed emissions are not canonically ordered")
    if len({row.emission_link_id for row in emissions}) != len(emissions):
        raise RetrievalContractError("seed-emission links are duplicated")
    if len({row.seed.seed_id for row in emissions}) != len(emissions):
        raise RetrievalContractError("one seed was emitted more than once in a query proof")
    emissions_by_record: dict[str, list[SeedEmissionLink]] = {
        key: [] for key in record_by_id
    }
    for link in emissions:
        record = record_by_id.get(link.normalized_record_id)
        if record is None:
            raise RetrievalContractError("seed emission references an unknown normalized record")
        _validate_seed_emission(case, proof.query_plan, record, link)
        emissions_by_record[record.normalized_record_id].append(link)
    for record_id, disposition in disposition_by_record.items():
        record = record_by_id[record_id]
        linked = emissions_by_record[record_id]
        expected_seed_ids = tuple(sorted(row.seed.seed_id for row in linked))
        expected_link_ids = tuple(sorted(row.emission_link_id for row in linked))
        if (
            disposition.disposition is not record.disposition
            or disposition.reason != record.disposition_reason
            or disposition.screening_rule_id != record.screening_rule_id
            or disposition.seed_ids != expected_seed_ids
            or disposition.emission_link_ids != expected_link_ids
        ):
            raise RetrievalContractError("screening disposition does not reconcile to its record/seeds")
        if record.disposition is RecordDisposition.EMITTED_SEEDS and not linked:
            raise RetrievalContractError("emission disposition has no emitted seed")
        if record.disposition is not RecordDisposition.EMITTED_SEEDS and linked:
            raise RetrievalContractError("non-emission disposition produced a seed")

    if contents:
        final_receipt = contents[-1]
        if final_receipt.receipt_status == "failed":
            derived_exhaustion = False
            derived_next = final_receipt.input_continuation_token
        else:
            derived_exhaustion = (
                final_receipt.continuation_exhausted
                and not final_receipt.source_limit_reached
            )
            derived_next = None if derived_exhaustion else final_receipt.output_continuation_token
            later_failed_page = max(attempts_by_page, default=0) > final_receipt.page_ordinal
            if later_failed_page:
                latest_page = max(attempts_by_page)
                failed_input = attempts_by_page[latest_page][0].input_continuation_token
                if failed_input != final_receipt.output_continuation_token:
                    raise RetrievalContractError("failed continuation request is disconnected from the last page")
                derived_exhaustion = False
                derived_next = failed_input
    else:
        derived_exhaustion = False
        derived_next = proof.query_plan.initial_continuation_token
    if (
        proof.reconciliation.continuation_exhausted != derived_exhaustion
        or proof.reconciliation.next_continuation_token != derived_next
    ):
        raise RetrievalContractError("final cursor exhaustion/continuation is not mechanically proved")

    rebuilt_reconciliation, rebuilt_gaps = _coverage_reconciliation(
        proof.query_plan,
        contents,
        records,
        dispositions,
        emissions,
        continuation_exhausted=derived_exhaustion,
        next_continuation_token=derived_next,
    )
    if canonical_bytes(rebuilt_reconciliation) != canonical_bytes(proof.reconciliation):
        raise RetrievalContractError("coverage reconciliation differs from the underlying ledgers")
    if any(gap not in proof.coverage_gaps for gap in rebuilt_gaps):
        raise RetrievalContractError("coverage proof hides a mechanical count gap")

    state = proof.coverage_state
    if not isinstance(state, CoverageState):
        raise RetrievalContractError("coverage proof has an invalid bounded state")
    has_rate_limit_failure = any(row.outcome == "rate_limited" for row in executions)
    has_terminal_failure = any(
        row.outcome in {"transport_error", "contract_error"}
        and row is attempts_by_page.get(row.page_ordinal, [None])[-1]
        for row in executions
    )
    has_mapping_failure = any(
        row.disposition is RecordDisposition.FAILED_MAPPING for row in records
    )
    required_capability = f"pagination:{proof.query_plan.source_universe.pagination_kind.value}"
    descriptor_matches = (
        proof.adapter.source_id == proof.query_plan.source_universe.source_id
        and proof.adapter.source_release == proof.query_plan.source_universe.source_release
        and required_capability in proof.adapter.capabilities
    )
    if state is CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE:
        if (
            not derived_exhaustion
            or not emissions
            or not proof.reconciliation.count_reconciliation_ok
            or proof.reconciliation.unvisited_record_count not in {None, 0}
            or proof.reconciliation.failed_record_count
            or has_mapping_failure
        ):
            raise RetrievalContractError("complete state is not proved by receipts and counts")
    elif state is CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY:
        if (
            not derived_exhaustion
            or emissions
            or not proof.reconciliation.count_reconciliation_ok
            or proof.reconciliation.unvisited_record_count not in {None, 0}
            or proof.reconciliation.failed_record_count
            or has_mapping_failure
        ):
            raise RetrievalContractError("no-relevant-hits state is not proved")
    elif state is CoverageState.PARTIAL_DUE_TO_SOURCE_LIMIT:
        bound_reached = (
            any(row.source_limit_reached for row in contents)
            or (
                proof.query_plan.max_pages is not None
                and len(contents) >= proof.query_plan.max_pages
            )
            or (
                proof.query_plan.max_records is not None
                and len(records) >= proof.query_plan.max_records
            )
            or (
                proof.query_plan.source_universe.source_record_cap is not None
                and len(records) >= proof.query_plan.source_universe.source_record_cap
            )
        )
        if derived_exhaustion or not bound_reached:
            raise RetrievalContractError("source-limit partial state lacks a retained frontier/bound")
    elif state is CoverageState.PARTIAL_DUE_TO_RATE_LIMIT:
        if derived_exhaustion or not has_rate_limit_failure:
            raise RetrievalContractError("rate-limit partial state lacks a terminal rate-limit receipt")
    elif state is CoverageState.UNSUPPORTED_SOURCE_CAPABILITY:
        if descriptor_matches or contents or executions or records or dispositions or emissions:
            raise RetrievalContractError("unsupported state contains retrieval work or a supported descriptor")
    elif state is CoverageState.FAILED_RETRIEVAL:
        if not (
            has_terminal_failure
            or has_mapping_failure
            or not proof.reconciliation.count_reconciliation_ok
            or proof.coverage_gaps
        ):
            raise RetrievalContractError("failed retrieval lacks a mechanical failure/gap")
    elif state is CoverageState.NOT_YET_SEARCHED:
        if contents or executions or records or dispositions or emissions or derived_exhaustion:
            raise RetrievalContractError("not-yet-searched state contains retrieval work")

    if proof.execution_trace_id != content_sha256(executions):
        raise RetrievalContractError("execution trace hash mismatch")
    expected_proof_id = _stable_id(
        "COVERAGE-PROOF", COVERAGE_PROOF_ID_RULE, _proof_projection(proof)
    )
    if proof.coverage_proof_id != expected_proof_id:
        raise RetrievalContractError("coverage proof ID includes altered scientific/content facts")


def _reduce_by_id(
    records: Iterable[Any], id_field: str, label: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for record in records:
        record_id = _text(getattr(record, id_field), f"{label}.{id_field}")
        prior = result.get(record_id)
        if prior is not None and canonical_bytes(prior) != canonical_bytes(record):
            raise RetrievalContractError(f"{label}: idempotency conflict for {record_id}")
        result[record_id] = record
    return result


def _combine_coverage_proofs(
    case: CaseRevision,
    proofs: Iterable[CoverageProof],
    *,
    validate_inputs: bool,
) -> CoverageBundle:
    rows = tuple(sorted(proofs, key=lambda row: row.query_plan.query_plan_id))
    if not rows:
        raise RetrievalContractError("coverage bundle requires at least one proof")
    if len({row.query_plan.query_plan_id for row in rows}) != len(rows):
        raise RetrievalContractError("coverage bundle repeats a query plan")
    receipt_owners: dict[str, tuple[str, str]] = {}
    native_records: dict[tuple[str, str, str], str] = {}
    for proof in rows:
        if validate_inputs:
            validate_coverage_proof(case, proof)
        for receipt in proof.content_receipts:
            owner = (proof.query_plan.query_plan_id, proof.query_plan.query_family_id)
            prior = receipt_owners.get(receipt.content_receipt_id)
            if prior is not None and prior != owner:
                raise RetrievalContractError(
                    "one retrieval receipt cannot be relabeled as unrelated query coverage"
                )
            receipt_owners[receipt.content_receipt_id] = owner
        for record in proof.normalized_records:
            native_key = (record.source_id, record.source_release, record.native_record_id)
            prior_hash = native_records.get(native_key)
            if prior_hash is not None and prior_hash != record.source_record_sha256:
                raise RetrievalContractError("query overlap changed one native source record")
            native_records[native_key] = record.source_record_sha256

    emissions = [link for proof in rows for link in proof.seed_emissions]
    mappings = _reduce_by_id(
        (link.source_mapping for link in emissions), "mapping_id", "source mappings"
    )
    routes = _reduce_by_id(
        (link.discovery_route for link in emissions), "route_id", "discovery routes"
    )
    seeds_by_id: dict[str, CandidateSeed] = {}
    for link in emissions:
        seed = link.seed
        prior = seeds_by_id.get(seed.seed_id)
        if prior is None:
            seeds_by_id[seed.seed_id] = seed
            continue
        fixed_fields = (
            "case_id",
            "case_revision_id",
            "compound_hint",
            "source_mapping_id",
            "development_status_hint",
            "identity_status",
        )
        if any(getattr(prior, name) != getattr(seed, name) for name in fixed_fields):
            raise RetrievalContractError("query overlap changed one seed assertion")
        seeds_by_id[seed.seed_id] = replace(
            prior,
            endpoint_ids=tuple(sorted(set(prior.endpoint_ids) | set(seed.endpoint_ids))),
            discovery_route_ids=tuple(
                sorted(set(prior.discovery_route_ids) | set(seed.discovery_route_ids))
            ),
            structured_routes=tuple(
                sorted(set(prior.structured_routes) | set(seed.structured_routes), key=lambda row: row.route_id)
            ),
            evidence_modalities=tuple(
                sorted(
                    set(prior.evidence_modalities) | set(seed.evidence_modalities),
                    key=lambda value: value.value,
                )
            ),
            chemical_universes=tuple(
                sorted(
                    set(prior.chemical_universes) | set(seed.chemical_universes),
                    key=lambda value: value.value,
                )
            ),
            uncertainty=tuple(
                sorted(
                    set(prior.uncertainty) | set(seed.uncertainty),
                    key=lambda value: (value.kind.value, value.level.value, value.note),
                )
            ),
        )
    source_mappings = tuple(mappings[key] for key in sorted(mappings))
    discovery_routes = tuple(routes[key] for key in sorted(routes))
    seeds = tuple(seeds_by_id[key] for key in sorted(seeds_by_id))
    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": RETRIEVAL_ADAPTER_MODEL_VERSION,
        "coverage_proof_ids": tuple(row.coverage_proof_id for row in rows),
        "source_mappings": source_mappings,
        "discovery_routes": discovery_routes,
        "seeds": seeds,
    }
    return CoverageBundle(
        schema_version=SCHEMA_VERSION,
        model_version=RETRIEVAL_ADAPTER_MODEL_VERSION,
        coverage_bundle_id=_stable_id(
            "COVERAGE-BUNDLE", COVERAGE_BUNDLE_ID_RULE, body
        ),
        proofs=rows,
        source_mappings=source_mappings,
        discovery_routes=discovery_routes,
        seeds=seeds,
    )


def combine_coverage_proofs(
    case: CaseRevision, proofs: Iterable[CoverageProof]
) -> CoverageBundle:
    """Combine query overlap as lineage without inflating stable seed identity."""

    validate_case_revision(case)
    bundle = _combine_coverage_proofs(case, proofs, validate_inputs=True)
    validate_coverage_bundle(case, bundle)
    return bundle


def validate_coverage_bundle(case: CaseRevision, bundle: CoverageBundle) -> None:
    if not isinstance(bundle, CoverageBundle):
        raise RetrievalContractError("expected CoverageBundle")
    if bundle.schema_version != SCHEMA_VERSION or bundle.model_version != RETRIEVAL_ADAPTER_MODEL_VERSION:
        raise RetrievalContractError("coverage bundle schema/model mismatch")
    rebuilt = _combine_coverage_proofs(case, bundle.proofs, validate_inputs=True)
    if canonical_bytes(rebuilt) != canonical_bytes(bundle):
        raise RetrievalContractError("coverage bundle differs from deterministic query reduction")


__all__ = [
    "AdapterDescriptor",
    "AdapterPageResponse",
    "AdapterTransportError",
    "CacheMissError",
    "ChemicalIdentityMatchLevel",
    "ChemicalIdentityReference",
    "ContentAddressedRetrievalCache",
    "CoverageBundle",
    "CoverageProof",
    "CoverageReconciliation",
    "CoverageState",
    "DeclaredSourceUniverse",
    "DenominatorKind",
    "DispositionCount",
    "NormalizedSeedAssertion",
    "NormalizedSourceRecord",
    "PaginationKind",
    "PublicationDensityMetadata",
    "QueryPlan",
    "RateLimitMetadata",
    "RecordDisposition",
    "RecordScreeningDisposition",
    "RETRIEVAL_ADAPTER_MODEL_VERSION",
    "RetrievalAdapter",
    "RetrievalContentReceipt",
    "RetrievalContractError",
    "RetrievalExecutionReceipt",
    "RetrievalRequest",
    "RetryPolicy",
    "SeedEmissionLink",
    "SeedRouteTemplate",
    "SourceActivityObservation",
    "SourceEvidenceAnnotation",
    "SourceFindingPolarity",
    "SourceMappingContext",
    "combine_coverage_proofs",
    "execute_query_plan",
    "make_adapter_descriptor",
    "make_chemical_identity_reference",
    "make_normalized_seed_assertion",
    "make_normalized_source_record",
    "make_publication_density_metadata",
    "make_query_plan",
    "make_retrieval_request",
    "make_seed_route_template",
    "make_source_activity_observation",
    "make_source_evidence_annotation",
    "make_source_mapping_context",
    "make_source_universe",
    "not_yet_searched_proof",
    "validate_coverage_bundle",
    "validate_coverage_proof",
    "validate_chemical_identity_reference",
    "validate_normalized_seed_assertion",
    "validate_normalized_source_record",
    "validate_query_plan",
    "validate_publication_density_metadata",
    "validate_source_activity_observation",
    "validate_source_evidence_annotation",
    "validate_source_mapping_context",
    "validate_source_universe",
]
