#!/usr/bin/env python3
"""Persisted whole-case schema-v7 discovery and seed aggregation.

This module is production code.  It reduces existing source-specific retrieval
adapters and generic coverage proofs; it does not import benchmark fixtures,
generators, validators, or expected answers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from v7_case_model import (
    CaseRevision,
    build_case_bundle,
    canonical_bytes,
    validate_case_revision,
)
from v7_chemical_target_adapters import (
    BindingDbAdapter,
    ChemblAdapter,
    HttpResponse as ChemicalHttpResponse,
    OpenTargetsAdapter,
    PubChemAdapter,
)
from v7_extended_discovery_adapters import (
    ChebiOlsAdapter,
    ClinicalTrialsGovAdapter,
    HttpResponse as ExtendedHttpResponse,
    PreprintAdapter,
    PreprintServer,
)
from v7_retrieval_adapter import (
    AdapterDescriptor,
    AdapterTransportError,
    ContentAddressedRetrievalCache,
    CoverageProof,
    CoverageState,
    DeclaredSourceUniverse,
    DenominatorKind,
    PaginationKind,
    QueryPlan,
    RecordDisposition,
    RetrievalAdapter,
    RetrievalContractError,
    RetryPolicy,
    combine_coverage_proofs,
    execute_query_plan,
    make_adapter_descriptor,
    make_query_plan,
    make_source_universe,
    validate_query_plan,
    validate_source_universe,
)


SCHEMA_VERSION = 7
MODEL_VERSION = "schema-v7-production-discovery-v1"
SOURCE_PLAN_ID_RULE = "schema-v7-production-source-plan-id-v1"
AGGREGATE_ID_RULE = "schema-v7-production-discovery-aggregate-id-v1"

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")
_COMPLETE_STATES = {
    CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
    CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY,
}


class DiscoveryAggregateError(RetrievalContractError):
    """Raised when a whole-case source plan or aggregate is inconsistent."""


class DiscoveryAggregateConflictError(DiscoveryAggregateError):
    """Raised when persisted immutable input/content is changed in place."""


AdapterFactory = Callable[[str, str, QueryPlan, Any], RetrievalAdapter]


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise DiscoveryAggregateError(f"Value is not canonical JSON: {type(value).__name__}")


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(_plain(value))).hexdigest().upper()


def _stable_id(prefix: str, rule: str, value: Any) -> str:
    return f"{prefix}-{_sha256({'rule': rule, 'value': value})[:24]}"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiscoveryAggregateError(f"{label} must be a nonempty string")
    return value.strip()


def _safe_component(value: str) -> str:
    result = _SAFE_COMPONENT.sub("_", value).strip("._")
    if not result:
        raise DiscoveryAggregateError("Persistent identity cannot be converted to a safe path")
    return result


def _coerce_case(value: CaseRevision | Mapping[str, Any]) -> CaseRevision:
    if isinstance(value, CaseRevision):
        validate_case_revision(value)
        return value
    if not isinstance(value, Mapping):
        raise DiscoveryAggregateError("case_revision must be a CaseRevision or mapping")
    raw_input = value.get("original_input", value)
    if not isinstance(raw_input, Mapping):
        raise DiscoveryAggregateError("case_revision.original_input must be a mapping")
    case = build_case_bundle(raw_input).case_revision
    for field_name in ("case_id", "case_revision_id", "source_input_sha256"):
        supplied = value.get(field_name)
        if supplied is not None and supplied != getattr(case, field_name):
            raise DiscoveryAggregateConflictError(
                f"case_revision.{field_name} conflicts with the rebuilt canonical case"
            )
    validate_case_revision(case)
    return case


def _enum(enum_type: type[Enum], value: Any, label: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise DiscoveryAggregateError(f"{label} has an invalid controlled value") from exc


def _coerce_source_universe(value: Any) -> DeclaredSourceUniverse:
    if isinstance(value, DeclaredSourceUniverse):
        validate_source_universe(value)
        return value
    if not isinstance(value, Mapping):
        raise DiscoveryAggregateError("query plan source_universe must be a mapping")
    required = {
        "source_id",
        "source_release",
        "source_snapshot_at",
        "native_scope",
        "source_side_filters",
        "local_filters",
        "denominator_kind",
        "declared_total",
        "pagination_kind",
        "continuation_parameter",
        "source_record_cap",
        "limitations",
    }
    unknown = set(value) - required - {"source_universe_id"}
    missing = required - set(value)
    if unknown or missing:
        raise DiscoveryAggregateError(
            f"source universe fields differ from contract; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    universe = make_source_universe(
        source_id=value["source_id"],
        source_release=value["source_release"],
        source_snapshot_at=value["source_snapshot_at"],
        native_scope=value["native_scope"],
        source_side_filters=value["source_side_filters"],
        local_filters=value["local_filters"],
        denominator_kind=_enum(
            DenominatorKind, value["denominator_kind"], "denominator_kind"
        ),
        declared_total=value["declared_total"],
        pagination_kind=_enum(
            PaginationKind, value["pagination_kind"], "pagination_kind"
        ),
        continuation_parameter=value["continuation_parameter"],
        source_record_cap=value["source_record_cap"],
        limitations=value["limitations"],
    )
    supplied_id = value.get("source_universe_id")
    if supplied_id is not None and supplied_id != universe.source_universe_id:
        raise DiscoveryAggregateConflictError("source universe ID does not match its content")
    return universe


def _coerce_query_plan(value: Any) -> QueryPlan:
    if isinstance(value, QueryPlan):
        validate_query_plan(value)
        return value
    if not isinstance(value, Mapping):
        raise DiscoveryAggregateError("branch query_plan must be a mapping")
    required = {
        "source_universe",
        "query_family_id",
        "required",
        "exact_request_parameters",
        "initial_continuation_token",
        "max_pages",
        "max_records",
        "allowed_terminal_codes",
        "retry_policy",
    }
    unknown = set(value) - required - {"query_plan_id"}
    missing = required - set(value)
    if unknown or missing:
        raise DiscoveryAggregateError(
            f"query plan fields differ from contract; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    retry = value["retry_policy"]
    if not isinstance(retry, Mapping) or set(retry) != {"max_attempts", "backoff_seconds"}:
        raise DiscoveryAggregateError("query plan retry_policy has schema drift")
    plan = make_query_plan(
        _coerce_source_universe(value["source_universe"]),
        query_family_id=value["query_family_id"],
        required=value["required"],
        exact_request_parameters=value["exact_request_parameters"],
        initial_continuation_token=value["initial_continuation_token"],
        max_pages=value["max_pages"],
        max_records=value["max_records"],
        allowed_terminal_codes=value["allowed_terminal_codes"],
        retry_policy=RetryPolicy(
            max_attempts=retry["max_attempts"],
            backoff_seconds=tuple(retry["backoff_seconds"]),
        ),
    )
    supplied_id = value.get("query_plan_id")
    if supplied_id is not None and supplied_id != plan.query_plan_id:
        raise DiscoveryAggregateConflictError("query plan ID does not match its content")
    return plan


def _normalize_plan_gap(value: Any, ordinal: int) -> dict[str, Any]:
    if isinstance(value, str):
        body: dict[str, Any] = {"reason": _required_text(value, "explicit gap")}
    elif isinstance(value, Mapping):
        body = _plain(value)
        if not any(
            isinstance(body.get(key), str) and body[key].strip()
            for key in ("reason", "description", "gap")
        ):
            raise DiscoveryAggregateError(
                "each explicit source-plan gap needs reason, description, or gap text"
            )
    else:
        raise DiscoveryAggregateError("explicit source-plan gaps must be strings or mappings")
    body.pop("gap_id", None)
    return {
        "gap_id": _stable_id(
            "DISCOVERY-GAP", "schema-v7-declared-discovery-gap-v1", {"ordinal": ordinal, "body": body}
        ),
        "gap_kind": "declared_plan_gap",
        **body,
    }


def _normalize_source_plan(
    case: CaseRevision, source_plan: Mapping[str, Any]
) -> tuple[str, str, tuple[dict[str, Any], ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    if not isinstance(source_plan, Mapping):
        raise DiscoveryAggregateError("source_plan must be a mapping")
    allowed = {"source_plan_id", "source_plan_revision", "branches", "explicit_gaps"}
    unknown = set(source_plan) - allowed
    if unknown:
        raise DiscoveryAggregateError(f"source_plan has unknown fields: {sorted(unknown)}")
    revision = _required_text(source_plan.get("source_plan_revision"), "source_plan_revision")
    raw_branches = source_plan.get("branches")
    if not isinstance(raw_branches, (tuple, list)) or not raw_branches:
        raise DiscoveryAggregateError("source_plan.branches must be a nonempty list")
    branches: list[dict[str, Any]] = []
    branch_ids: set[str] = set()
    query_plan_ids: set[str] = set()
    for raw in raw_branches:
        if not isinstance(raw, Mapping) or set(raw) != {"branch_id", "adapter_id", "query_plan"}:
            raise DiscoveryAggregateError(
                "each source-plan branch must contain branch_id, adapter_id, and query_plan"
            )
        branch_id = _required_text(raw["branch_id"], "branch_id")
        adapter_id = _required_text(raw["adapter_id"], "adapter_id")
        plan = _coerce_query_plan(raw["query_plan"])
        if branch_id in branch_ids or plan.query_plan_id in query_plan_ids:
            raise DiscoveryAggregateError("source plan repeats a branch or query-plan identity")
        branch_ids.add(branch_id)
        query_plan_ids.add(plan.query_plan_id)
        branches.append(
            {"branch_id": branch_id, "adapter_id": adapter_id, "query_plan": plan}
        )
    branches.sort(key=lambda row: row["branch_id"])
    raw_gaps = source_plan.get("explicit_gaps", ())
    if not isinstance(raw_gaps, (tuple, list)):
        raise DiscoveryAggregateError("source_plan.explicit_gaps must be a list")
    plan_gaps = tuple(
        sorted(
            (_normalize_plan_gap(value, index) for index, value in enumerate(raw_gaps)),
            key=lambda row: row["gap_id"],
        )
    )
    identity_body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "case_revision_id": case.case_revision_id,
        "source_plan_revision": revision,
        "branches": [
            {
                "branch_id": row["branch_id"],
                "adapter_id": row["adapter_id"],
                "query_plan": _plain(row["query_plan"]),
            }
            for row in branches
        ],
        "explicit_gaps": list(plan_gaps),
    }
    plan_id = _stable_id("SOURCE-PLAN", SOURCE_PLAN_ID_RULE, identity_body)
    supplied_id = source_plan.get("source_plan_id")
    if supplied_id is not None and supplied_id != plan_id:
        raise DiscoveryAggregateConflictError("source_plan_id does not match its declarations")
    return plan_id, revision, tuple(branches), plan_gaps, identity_body


class _FrozenHttpTape:
    """Exact caller-supplied response tape for real source adapter normalization."""

    def __init__(self, specification: Any, response_type: type[Any]) -> None:
        if isinstance(specification, Mapping) and "responses" in specification:
            unknown = set(specification) - {"responses"}
            if unknown:
                raise DiscoveryAggregateError(
                    f"frozen page specification has unknown fields: {sorted(unknown)}"
                )
            specification = specification["responses"]
        if not isinstance(specification, (tuple, list)):
            raise DiscoveryAggregateError("frozen branch pages must be a list or responses mapping")
        self._responses = list(specification)
        self._response_type = response_type

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> Any:
        del headers, body
        if not self._responses:
            raise AdapterTransportError(
                "FROZEN_PAGES_EXHAUSTED",
                "No declared frozen response remains for this branch request.",
                retryable=False,
            )
        entry = self._responses.pop(0)
        wrapper = isinstance(entry, Mapping) and bool(
            set(entry) & {"body", "status", "headers", "error", "expect_method", "expect_url_contains"}
        )
        if wrapper:
            expected_method = entry.get("expect_method")
            expected_url = entry.get("expect_url_contains")
            if expected_method is not None and expected_method != method:
                raise DiscoveryAggregateError("frozen response request method mismatch")
            if expected_url is not None and str(expected_url) not in url:
                raise DiscoveryAggregateError("frozen response URL expectation mismatch")
            error = entry.get("error")
            if error is not None:
                if not isinstance(error, Mapping):
                    raise DiscoveryAggregateError("frozen response error must be a mapping")
                raise AdapterTransportError(
                    _required_text(error.get("code"), "frozen error code"),
                    _required_text(error.get("message"), "frozen error message"),
                    retryable=bool(error.get("retryable", False)),
                    rate_limited=bool(error.get("rate_limited", False)),
                    retry_after_seconds=error.get("retry_after_seconds"),
                )
            payload = entry.get("body", {})
            status = entry.get("status", 200)
            response_headers = entry.get("headers", {})
        else:
            payload = entry
            status = 200
            response_headers = {}
        if isinstance(payload, bytes):
            raw = payload
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = canonical_bytes(_plain(payload))
        if not isinstance(status, int) or isinstance(status, bool):
            raise DiscoveryAggregateError("frozen HTTP status must be an integer")
        if not isinstance(response_headers, Mapping):
            raise DiscoveryAggregateError("frozen HTTP headers must be a mapping")
        return self._response_type(
            status=status,
            headers={str(key): str(value) for key, value in response_headers.items()},
            body=raw,
        )


class _UnsupportedRetrievalAdapter:
    def __init__(self, adapter_id: str, plan: QueryPlan) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id=adapter_id,
            adapter_version="unavailable",
            source_id=plan.source_universe.source_id,
            source_release=plan.source_universe.source_release,
            capabilities=("unsupported_source_capability",),
        )

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        del query_plan
        return False, "No configured production adapter supports this declared source capability."

    def retrieve(self, request: Any) -> Any:
        del request
        raise AssertionError("unsupported adapters cannot retrieve")

    def normalize(self, request: Any, response: Any) -> Any:
        del request, response
        raise AssertionError("unsupported adapters cannot normalize")


def _default_adapter_factory(
    branch_id: str, adapter_id: str, plan: QueryPlan, frozen_specification: Any
) -> RetrievalAdapter:
    del branch_id
    release = plan.source_universe.source_release
    chemical_factories: dict[str, Callable[[Any], RetrievalAdapter]] = {
        "open-targets-graphql-v4": lambda transport: OpenTargetsAdapter(
            release, transport=transport
        ),
        "chembl-data-web-services": lambda transport: ChemblAdapter(
            release, transport=transport
        ),
        "bindingdb-rest-get-ligands-by-uniprot": lambda transport: BindingDbAdapter(
            release, transport=transport
        ),
        "pubchem-pug-rest": lambda transport: PubChemAdapter(
            release, transport=transport
        ),
    }
    extended_factories: dict[str, Callable[[Any], RetrievalAdapter]] = {
        "clinicaltrials-gov-api-v2": lambda transport: ClinicalTrialsGovAdapter(
            release, transport=transport
        ),
        "biorxiv-details-api": lambda transport: PreprintAdapter(
            release, PreprintServer.BIORXIV, transport=transport
        ),
        "medrxiv-details-api": lambda transport: PreprintAdapter(
            release, PreprintServer.MEDRXIV, transport=transport
        ),
        "ebi-ols4-chebi-search": lambda transport: ChebiOlsAdapter(
            release, transport=transport
        ),
    }
    if adapter_id in chemical_factories:
        if frozen_specification is None:
            raise DiscoveryAggregateError(
                f"declared supported branch {adapter_id} has no frozen page tape"
            )
        return chemical_factories[adapter_id](
            _FrozenHttpTape(frozen_specification, ChemicalHttpResponse)
        )
    if adapter_id in extended_factories:
        if frozen_specification is None:
            raise DiscoveryAggregateError(
                f"declared supported branch {adapter_id} has no frozen page tape"
            )
        return extended_factories[adapter_id](
            _FrozenHttpTape(frozen_specification, ExtendedHttpResponse)
        )
    return _UnsupportedRetrievalAdapter(adapter_id, plan)


def _closure_statement(proof: CoverageProof) -> str:
    if proof.coverage_state in _COMPLETE_STATES:
        reconciliation = proof.reconciliation
        if (
            proof.query_plan.source_universe.denominator_kind
            is DenominatorKind.EXACT_DECLARED
            and reconciliation.unvisited_record_count == 0
        ):
            return "inventory complete"
        return "query result complete"
    if proof.coverage_state is CoverageState.PARTIAL_DUE_TO_SOURCE_LIMIT:
        provider_truncated = any(
            receipt.source_limit_reached for receipt in proof.content_receipts
        )
        return "open" if provider_truncated else "bounded plan complete"
    if proof.coverage_state in {
        CoverageState.UNSUPPORTED_SOURCE_CAPABILITY,
        CoverageState.FAILED_RETRIEVAL,
    }:
        return "failed"
    return "open"


def _mapping_outcomes(branch_id: str, proof: CoverageProof) -> list[dict[str, Any]]:
    disposition_by_record = {
        row.normalized_record_id: row for row in proof.screening_dispositions
    }
    links_by_record: dict[str, dict[str, Any]] = {}
    for link in proof.seed_emissions:
        bucket = links_by_record.setdefault(link.normalized_record_id, {})
        if link.assertion_locator in bucket:
            raise DiscoveryAggregateError(
                "one eligible intervention assertion has multiple emission outcomes"
            )
        bucket[link.assertion_locator] = link
    outcomes: list[dict[str, Any]] = []
    for record in proof.normalized_records:
        disposition = disposition_by_record.get(record.normalized_record_id)
        if disposition is None:
            raise DiscoveryAggregateError("retrieved normalized item lacks a mapping disposition")
        links = links_by_record.get(record.normalized_record_id, {})
        assertion_outcomes: list[dict[str, Any]] = []
        for assertion in record.seed_assertions:
            link = links.get(assertion.assertion_locator)
            if link is None:
                raise DiscoveryAggregateError(
                    "eligible intervention assertion lacks a seed emission outcome"
                )
            assertion_outcomes.append(
                {
                    "assertion_locator": assertion.assertion_locator,
                    "raw_intervention_assertion": assertion.raw_intervention_assertion,
                    "emission_link_id": link.emission_link_id,
                    "source_mapping_id": link.source_mapping.mapping_id,
                    "discovery_route_id": link.discovery_route.route_id,
                    "seed_id": link.seed.seed_id,
                    "assertion": _plain(assertion),
                }
            )
        if set(links) != {row.assertion_locator for row in record.seed_assertions}:
            raise DiscoveryAggregateError("seed emission exists without its eligible assertion")
        body = {
            "branch_id": branch_id,
            "query_plan_id": proof.query_plan.query_plan_id,
            "normalized_record_id": record.normalized_record_id,
            "source_id": record.source_id,
            "source_release": record.source_release,
            "native_record_id": record.native_record_id,
            "native_record_locator": record.native_record_locator,
            "source_record_sha256": record.source_record_sha256,
            "retrieval_content_receipt_id": record.retrieval_content_receipt_id,
            "disposition": disposition.disposition.value,
            "reason": disposition.reason,
            "screening_rule_id": disposition.screening_rule_id,
            "eligible_assertion_count": len(record.seed_assertions),
            "assertion_outcomes": assertion_outcomes,
        }
        outcomes.append(
            {
                "mapping_outcome_id": _stable_id(
                    "MAPPING-OUTCOME", "schema-v7-production-mapping-outcome-v1", body
                ),
                **body,
            }
        )
    return sorted(outcomes, key=lambda row: row["mapping_outcome_id"])


def _proof_gaps(branch_id: str, proof: CoverageProof) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    base = {
        "branch_id": branch_id,
        "source_universe_id": proof.query_plan.source_universe.source_universe_id,
        "query_plan_id": proof.query_plan.query_plan_id,
        "coverage_state": proof.coverage_state.value,
        "required": proof.query_plan.required,
    }
    for kind, values, impact in (
        ("coverage_gap", proof.coverage_gaps, "prevents_or_bounds_closure"),
        ("source_limitation", proof.source_specific_limitations, "bounded_scope"),
    ):
        for text in values:
            body = {**base, "gap_kind": kind, "reason": text, "closure_impact": impact}
            result.append(
                {
                    "gap_id": _stable_id(
                        "DISCOVERY-GAP", "schema-v7-production-discovery-gap-v1", body
                    ),
                    **body,
                }
            )
    return result


def _branch_record(branch_id: str, adapter_id: str, proof: CoverageProof) -> dict[str, Any]:
    statement = _closure_statement(proof)
    return {
        "branch_id": branch_id,
        "adapter_id": adapter_id,
        "adapter_version": proof.adapter.adapter_version,
        "source_universe_id": proof.query_plan.source_universe.source_universe_id,
        "query_plan_id": proof.query_plan.query_plan_id,
        "query_family_id": proof.query_plan.query_family_id,
        "required": proof.query_plan.required,
        "query_plan": _plain(proof.query_plan),
        "coverage_proof_id": proof.coverage_proof_id,
        "coverage_state": proof.coverage_state.value,
        "closure_state": statement,
        "reconciliation": _plain(proof.reconciliation),
    }


def _aggregate_reconciliation(
    proofs: Iterable[CoverageProof],
    mapping_outcomes: list[dict[str, Any]],
    seed_count: int,
    content_receipt_count: int,
) -> dict[str, Any]:
    proof_rows = tuple(proofs)
    returned = sum(
        receipt.returned_count
        for proof in proof_rows
        for receipt in proof.content_receipts
    )
    failed = sum(
        receipt.failed_record_count
        for proof in proof_rows
        for receipt in proof.content_receipts
    )
    assertion_occurrences = sum(
        row["eligible_assertion_count"] for row in mapping_outcomes
    )
    emission_occurrences = sum(
        len(row["assertion_outcomes"]) for row in mapping_outcomes
    )
    disposition_counts = {value.value: 0 for value in RecordDisposition}
    for row in mapping_outcomes:
        disposition_counts[row["disposition"]] += 1
    return {
        "branch_count": len(proof_rows),
        "content_receipt_count": content_receipt_count,
        "returned_native_item_count": returned,
        "mapping_outcome_occurrence_count": len(mapping_outcomes),
        "failed_native_item_count": failed,
        "unreconciled_native_item_count": returned - len(mapping_outcomes) - failed,
        "eligible_intervention_assertion_occurrence_count": assertion_occurrences,
        "seed_emission_occurrence_count": emission_occurrences,
        "unreconciled_eligible_assertion_count": assertion_occurrences - emission_occurrences,
        "unique_seed_count": seed_count,
        "query_overlap_reduction_count": emission_occurrences - seed_count,
        "mapping_disposition_counts": disposition_counts,
    }


def _aggregate_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "aggregate_id"}


def validate_discovery_aggregate(
    case_revision: CaseRevision | Mapping[str, Any], aggregate: Mapping[str, Any]
) -> None:
    case = _coerce_case(case_revision)
    if not isinstance(aggregate, Mapping):
        raise DiscoveryAggregateError("discovery aggregate must be a mapping")
    required = {
        "schema_version",
        "model_version",
        "aggregate_id",
        "case_id",
        "case_revision_id",
        "source_plan_id",
        "source_plan_revision",
        "input_receipts",
        "source_universes",
        "branches",
        "retrieval_content_receipts",
        "mapping_outcomes",
        "source_mappings",
        "discovery_routes",
        "seeds",
        "closure_states",
        "explicit_gaps",
        "closure",
        "reconciliation",
    }
    if set(aggregate) != required:
        raise DiscoveryAggregateError("persisted discovery aggregate has schema drift")
    if aggregate["schema_version"] != SCHEMA_VERSION or aggregate["model_version"] != MODEL_VERSION:
        raise DiscoveryAggregateError("discovery aggregate schema/model mismatch")
    if aggregate["case_id"] != case.case_id or aggregate["case_revision_id"] != case.case_revision_id:
        raise DiscoveryAggregateConflictError("discovery aggregate belongs to another case revision")
    branches = aggregate["branches"]
    outcomes = aggregate["mapping_outcomes"]
    receipts = aggregate["retrieval_content_receipts"]
    seeds = aggregate["seeds"]
    if not all(isinstance(value, list) for value in (branches, outcomes, receipts, seeds)):
        raise DiscoveryAggregateError("discovery aggregate collections must be lists")
    if len({row["branch_id"] for row in branches}) != len(branches):
        raise DiscoveryAggregateError("discovery aggregate repeats a branch")
    if len({row["content_receipt_id"] for row in receipts}) != len(receipts):
        raise DiscoveryAggregateError("discovery aggregate repeats a content receipt")
    if len({row["mapping_outcome_id"] for row in outcomes}) != len(outcomes):
        raise DiscoveryAggregateError("discovery aggregate repeats a mapping outcome")
    if len({row["seed_id"] for row in seeds}) != len(seeds):
        raise DiscoveryAggregateError("discovery aggregate repeats a stable seed")
    reconciliation = aggregate["reconciliation"]
    returned = sum(row["returned_count"] for row in receipts)
    failed = sum(row["failed_record_count"] for row in receipts)
    assertion_occurrences = sum(row["eligible_assertion_count"] for row in outcomes)
    emission_occurrences = sum(len(row["assertion_outcomes"]) for row in outcomes)
    expected = {
        "branch_count": len(branches),
        "content_receipt_count": len(receipts),
        "returned_native_item_count": returned,
        "mapping_outcome_occurrence_count": len(outcomes),
        "failed_native_item_count": failed,
        "unreconciled_native_item_count": returned - len(outcomes) - failed,
        "eligible_intervention_assertion_occurrence_count": assertion_occurrences,
        "seed_emission_occurrence_count": emission_occurrences,
        "unreconciled_eligible_assertion_count": assertion_occurrences - emission_occurrences,
        "unique_seed_count": len(seeds),
        "query_overlap_reduction_count": emission_occurrences - len(seeds),
    }
    for key, expected_value in expected.items():
        if reconciliation.get(key) != expected_value:
            raise DiscoveryAggregateError(f"aggregate reconciliation mismatch: {key}")
    if expected["unreconciled_native_item_count"] != 0:
        raise DiscoveryAggregateError("a retrieved native item lacks a mapping/failure outcome")
    if expected["unreconciled_eligible_assertion_count"] != 0:
        raise DiscoveryAggregateError("an eligible intervention assertion lacks a seed outcome")
    if expected["query_overlap_reduction_count"] < 0:
        raise DiscoveryAggregateError("unique seed count exceeds seed emission occurrences")
    seed_ids = {row["seed_id"] for row in seeds}
    for outcome in outcomes:
        if outcome["eligible_assertion_count"] != len(outcome["assertion_outcomes"]):
            raise DiscoveryAggregateError("mapping assertion cardinality mismatch")
        for assertion in outcome["assertion_outcomes"]:
            if assertion["seed_id"] not in seed_ids:
                raise DiscoveryAggregateError("mapping outcome references an absent seed")
    branch_by_id = {row["branch_id"]: row for row in branches}
    gaps = aggregate["explicit_gaps"]
    for branch_id, branch in branch_by_id.items():
        if branch["closure_state"] not in {
            "inventory complete",
            "query result complete",
            "bounded plan complete",
        } and not any(gap.get("branch_id") == branch_id for gap in gaps):
            raise DiscoveryAggregateError("noncomplete branch lacks an explicit gap")
    expected_id = _stable_id(
        "DISCOVERY-AGGREGATE", AGGREGATE_ID_RULE, _aggregate_projection(aggregate)
    )
    if aggregate["aggregate_id"] != expected_id:
        raise DiscoveryAggregateConflictError("discovery aggregate ID/content mismatch")


class V7DiscoveryAdapter:
    """Production test-facing adapter for persisted whole-case discovery."""

    def __init__(
        self,
        persistence_root: str | Path,
        *,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.persistence_root = Path(persistence_root).expanduser().resolve()
        self.adapter_factory = adapter_factory or _default_adapter_factory

    def aggregate_path(self, case_revision_id: str, source_plan_id: str) -> Path:
        return (
            self.persistence_root
            / _safe_component(case_revision_id)
            / _safe_component(source_plan_id)
            / "aggregate.json"
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DiscoveryAggregateError(f"cannot read persisted discovery aggregate: {path}") from exc
        if not isinstance(value, dict):
            raise DiscoveryAggregateError("persisted discovery aggregate is not an object")
        return value

    @staticmethod
    def _write_once(path: Path, value: Mapping[str, Any]) -> None:
        payload = canonical_bytes(_plain(value)) + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise DiscoveryAggregateConflictError(
                    f"immutable discovery artifact already exists with different content: {path}"
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

    def retrieve_and_seed(
        self,
        case_revision: CaseRevision | Mapping[str, Any],
        source_plan: Mapping[str, Any],
        frozen_pages: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Execute, reduce, validate, and persist one complete declared source plan."""

        case = _coerce_case(case_revision)
        (
            source_plan_id,
            source_plan_revision,
            branches,
            declared_gaps,
            source_plan_projection,
        ) = _normalize_source_plan(case, source_plan)
        if not isinstance(frozen_pages, Mapping) or any(
            not isinstance(key, str) for key in frozen_pages
        ):
            raise DiscoveryAggregateError("frozen_pages must be a string-keyed mapping")
        branch_ids = {row["branch_id"] for row in branches}
        extra_pages = set(frozen_pages) - branch_ids
        if extra_pages:
            raise DiscoveryAggregateError(
                f"frozen pages exist for undeclared branches: {sorted(extra_pages)}"
            )
        frozen_pages_sha256 = _sha256(frozen_pages)
        source_plan_sha256 = _sha256(source_plan_projection)
        target = self.aggregate_path(case.case_revision_id, source_plan_id)
        if target.is_file():
            stored = self._read_json(target)
            receipts = stored.get("input_receipts", {})
            if (
                receipts.get("source_plan_sha256") != source_plan_sha256
                or receipts.get("frozen_pages_sha256") != frozen_pages_sha256
            ):
                raise DiscoveryAggregateConflictError(
                    "persisted source-plan identity was replayed with different declarations or frozen pages"
                )
            validate_discovery_aggregate(case, stored)
            return stored

        proofs: list[CoverageProof] = []
        branch_rows: list[dict[str, Any]] = []
        outcomes: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = [dict(value) for value in declared_gaps]
        execution_receipts: list[dict[str, Any]] = []
        for branch in branches:
            branch_id = branch["branch_id"]
            adapter_id = branch["adapter_id"]
            plan = branch["query_plan"]
            adapter = self.adapter_factory(
                branch_id, adapter_id, plan, frozen_pages.get(branch_id)
            )
            if not isinstance(adapter, RetrievalAdapter):
                raise DiscoveryAggregateError(
                    f"adapter factory returned an invalid adapter for branch {branch_id}"
                )
            if not isinstance(adapter.descriptor, AdapterDescriptor):
                raise DiscoveryAggregateError("adapter descriptor is malformed")
            if adapter.descriptor.adapter_id != adapter_id:
                raise DiscoveryAggregateConflictError(
                    f"branch {branch_id} requested {adapter_id} but factory returned {adapter.descriptor.adapter_id}"
                )
            cache = ContentAddressedRetrievalCache(
                target.parent / "response_cache" / _safe_component(branch_id)
            )
            proof = execute_query_plan(
                case,
                plan,
                adapter,
                cache=cache,
                sleeper=lambda _: None,
            )
            proofs.append(proof)
            branch_rows.append(_branch_record(branch_id, adapter_id, proof))
            outcomes.extend(_mapping_outcomes(branch_id, proof))
            gaps.extend(_proof_gaps(branch_id, proof))
            execution_receipts.extend(_plain(proof.execution_receipts))

        bundle = combine_coverage_proofs(case, proofs)
        universes: dict[str, dict[str, Any]] = {}
        content_receipts: dict[str, dict[str, Any]] = {}
        for proof in proofs:
            universe = _plain(proof.query_plan.source_universe)
            universe_id = universe["source_universe_id"]
            prior_universe = universes.get(universe_id)
            if prior_universe is not None and prior_universe != universe:
                raise DiscoveryAggregateConflictError("source universe identity conflict")
            universes[universe_id] = universe
            for receipt in proof.content_receipts:
                plain_receipt = _plain(receipt)
                prior = content_receipts.get(receipt.content_receipt_id)
                if prior is not None and prior != plain_receipt:
                    raise DiscoveryAggregateConflictError("content receipt identity conflict")
                content_receipts[receipt.content_receipt_id] = plain_receipt

        branch_rows.sort(key=lambda row: row["branch_id"])
        outcomes.sort(key=lambda row: row["mapping_outcome_id"])
        gaps = sorted({row["gap_id"]: row for row in gaps}.values(), key=lambda row: row["gap_id"])
        closure_states = [
            {
                "branch_id": row["branch_id"],
                "query_plan_id": row["query_plan_id"],
                "required": row["required"],
                "coverage_state": row["coverage_state"],
                "closure_state": row["closure_state"],
            }
            for row in branch_rows
        ]
        accepted_closures = {
            "inventory complete",
            "query result complete",
            "bounded plan complete",
        }
        required_complete = all(
            row["closure_state"] in accepted_closures
            for row in closure_states
            if row["required"]
        )
        all_complete = all(
            row["closure_state"] in accepted_closures for row in closure_states
        )
        if all_complete:
            run_statement = "complete within the declared source releases and query plan"
        elif required_complete:
            run_statement = "required-branch complete with optional gaps"
        else:
            run_statement = "diagnostic partial; required source/query gaps remain"

        receipts_list = [content_receipts[key] for key in sorted(content_receipts)]
        seeds = [_plain(value) for value in bundle.seeds]
        reconciliation = _aggregate_reconciliation(
            proofs, outcomes, len(seeds), len(receipts_list)
        )
        draft: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "aggregate_id": "",
            "case_id": case.case_id,
            "case_revision_id": case.case_revision_id,
            "source_plan_id": source_plan_id,
            "source_plan_revision": source_plan_revision,
            "input_receipts": {
                "case_source_input_sha256": case.source_input_sha256,
                "source_plan_sha256": source_plan_sha256,
                "frozen_pages_sha256": frozen_pages_sha256,
            },
            "source_universes": [universes[key] for key in sorted(universes)],
            "branches": branch_rows,
            "retrieval_content_receipts": receipts_list,
            "mapping_outcomes": outcomes,
            "source_mappings": [_plain(value) for value in bundle.source_mappings],
            "discovery_routes": [_plain(value) for value in bundle.discovery_routes],
            "seeds": seeds,
            "closure_states": closure_states,
            "explicit_gaps": gaps,
            "closure": {
                "required_branches_complete": required_complete,
                "all_declared_branches_complete": all_complete,
                "statement": run_statement,
                "global_coverage_claimed": False,
            },
            "reconciliation": reconciliation,
        }
        draft["aggregate_id"] = _stable_id(
            "DISCOVERY-AGGREGATE", AGGREGATE_ID_RULE, _aggregate_projection(draft)
        )
        validate_discovery_aggregate(case, draft)

        execution_projection = {
            "schema_version": SCHEMA_VERSION,
            "model_version": MODEL_VERSION,
            "case_revision_id": case.case_revision_id,
            "source_plan_id": source_plan_id,
            "aggregate_id": draft["aggregate_id"],
            "execution_receipts": sorted(
                execution_receipts,
                key=lambda row: (
                    row["query_plan_id"],
                    row["page_ordinal"],
                    row["attempt_number"],
                    row["execution_receipt_id"],
                ),
            ),
        }
        execution_id = _stable_id(
            "DISCOVERY-EXECUTION", "schema-v7-production-discovery-execution-v1", execution_projection
        )
        execution_path = target.parent / "executions" / f"{execution_id}.json"
        self._write_once(execution_path, execution_projection)
        self._write_once(target, draft)
        return self._read_json(target)


__all__ = [
    "DiscoveryAggregateConflictError",
    "DiscoveryAggregateError",
    "MODEL_VERSION",
    "V7DiscoveryAdapter",
    "validate_discovery_aggregate",
]
