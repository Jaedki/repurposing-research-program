#!/usr/bin/env python3
"""Frozen mock adapters for schema-v7 retrieval/coverage acceptance tests.

This module is deliberately synthetic.  It is not a chemical, target,
literature, clinical, or regulatory source adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from v7_discovery import (
    CausalRoute,
    ChemicalUniverse,
    DevelopmentStatus,
    EffectDirection,
    EvidenceModality,
    InterventionAction,
    UncertaintyKind,
    UncertaintyLevel,
    known_node,
    not_applicable_node,
)
from v7_retrieval_adapter import (
    AdapterDescriptor,
    AdapterPageResponse,
    AdapterTransportError,
    DenominatorKind,
    PaginationKind,
    QueryPlan,
    RateLimitMetadata,
    RecordDisposition,
    RetrievalContractError,
    RetrievalRequest,
    RetryPolicy,
    make_adapter_descriptor,
    make_normalized_seed_assertion,
    make_normalized_source_record,
    make_query_plan,
    make_seed_route_template,
    make_source_universe,
)
from v7_seed_funnel import CompoundHintKind, SeedUncertainty


DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "schema_v7"
    / "retrieval_adapter"
    / "frozen_mock_scenarios.json"
)


class FrozenFixtureCatalog:
    def __init__(self, path: str | Path = DEFAULT_FIXTURE_PATH) -> None:
        self.path = Path(path).expanduser().resolve()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RetrievalContractError(f"Cannot read frozen adapter fixture: {exc}") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "fixture_id",
            "case_input",
            "scenarios",
        }:
            raise RetrievalContractError("Frozen adapter fixture has schema drift")
        if value["schema_version"] != 7 or not isinstance(value["case_input"], dict):
            raise RetrievalContractError("Frozen adapter fixture version/case is invalid")
        scenarios = value["scenarios"]
        if not isinstance(scenarios, dict) or not scenarios:
            raise RetrievalContractError("Frozen adapter fixture has no scenarios")
        self.fixture = value

    @property
    def case_input(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.fixture["case_input"]))

    def scenario(self, name: str) -> dict[str, Any]:
        value = self.fixture["scenarios"].get(name)
        if not isinstance(value, dict):
            raise RetrievalContractError(f"Unknown frozen adapter scenario: {name}")
        return json.loads(json.dumps(value))

    def build_plan(self, name: str, *, query_family_id: str | None = None) -> QueryPlan:
        scenario = self.scenario(name)
        universe = make_source_universe(
            source_id=scenario["source_id"],
            source_release=scenario["source_release"],
            source_snapshot_at=scenario["source_snapshot_at"],
            native_scope=scenario["native_scope"],
            source_side_filters=scenario.get("source_side_filters", {}),
            local_filters=scenario.get("local_filters", {}),
            denominator_kind=DenominatorKind(scenario["denominator_kind"]),
            declared_total=scenario.get("declared_total"),
            pagination_kind=PaginationKind(scenario["pagination_kind"]),
            continuation_parameter=scenario["continuation_parameter"],
            source_record_cap=scenario.get("source_record_cap"),
            limitations=scenario["limitations"],
        )
        retry = scenario["retry"]
        return make_query_plan(
            universe,
            query_family_id=query_family_id or scenario["query_family_id"],
            required=True,
            exact_request_parameters=scenario["query_parameters"],
            initial_continuation_token=scenario["initial_token"],
            max_pages=scenario.get("max_pages"),
            max_records=scenario.get("max_records"),
            allowed_terminal_codes=scenario["allowed_terminal_codes"],
            retry_policy=RetryPolicy(
                max_attempts=retry["max_attempts"],
                backoff_seconds=tuple(retry["backoff_seconds"]),
            ),
        )


def _token_key(token: str | None) -> str:
    return "<START>" if token is None else token


def _record(index: int, *, disposition: str = "emitted_seeds") -> dict[str, Any]:
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        raise RetrievalContractError("Frozen generated record index must be positive")
    return {
        "native_record_id": f"NATIVE-{index:06d}",
        "native_record_locator": f"/records/NATIVE-{index:06d}",
        "assertion_locator": "/compound_assertion/0",
        "raw_intervention_assertion": f"Frozen mock compound {index:06d}",
        "compound_name": f"Frozen mock compound {index:06d}",
        "evidence_id": f"EVIDENCE-MOCK-{index:06d}",
        "mapping_disposition": disposition,
    }


def _page_records(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "records" in page:
        values = page["records"]
        if not isinstance(values, list):
            raise RetrievalContractError("Frozen page records must be a list")
        result: list[dict[str, Any]] = []
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool):
                result.append(_record(value))
            elif isinstance(value, dict) and isinstance(value.get("index"), int):
                result.append(
                    _record(
                        value["index"],
                        disposition=value.get("mapping_disposition", "emitted_seeds"),
                    )
                )
            else:
                raise RetrievalContractError("Frozen inline record specification is invalid")
        return result
    generated = page.get("generated_records")
    if not isinstance(generated, dict) or set(generated) != {"start", "count"}:
        raise RetrievalContractError("Frozen page lacks records or a generator specification")
    start = generated["start"]
    count = generated["count"]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (start, count)):
        raise RetrievalContractError("Frozen record generator bounds are invalid")
    return [_record(index) for index in range(start, start + count)]


class FrozenMockRetrievalAdapter:
    """Execute one named frozen scenario through the generic adapter protocol."""

    def __init__(
        self,
        scenario_name: str,
        *,
        catalog: FrozenFixtureCatalog | None = None,
        transport_enabled: bool = True,
        capabilities: tuple[str, ...] | None = None,
    ) -> None:
        self.catalog = catalog or FrozenFixtureCatalog()
        self.scenario_name = scenario_name
        self.scenario = self.catalog.scenario(scenario_name)
        pagination = self.scenario["pagination_kind"]
        self.descriptor: AdapterDescriptor = make_adapter_descriptor(
            adapter_id=self.scenario["adapter_id"],
            adapter_version=self.scenario["adapter_version"],
            source_id=self.scenario["source_id"],
            source_release=self.scenario["source_release"],
            capabilities=capabilities or (f"pagination:{pagination}", "normalized_seed_mapping"),
        )
        self.transport_enabled = transport_enabled
        self.transport_calls = 0
        self._calls_by_token: dict[str, int] = {}

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        capability = f"pagination:{query_plan.source_universe.pagination_kind.value}"
        supported = (
            query_plan.source_universe.source_id == self.descriptor.source_id
            and query_plan.source_universe.source_release == self.descriptor.source_release
            and capability in self.descriptor.capabilities
        )
        return (
            supported,
            "" if supported else "Frozen adapter lacks the declared source or pagination capability.",
        )

    def _page(self, token: str | None) -> dict[str, Any]:
        matches = [
            row
            for row in self.scenario["pages"]
            if row.get("input_token") == token
        ]
        if len(matches) != 1:
            raise AdapterTransportError(
                "MOCK_UNKNOWN_CONTINUATION",
                f"No frozen page exists for continuation {_token_key(token)!r}",
                retryable=False,
            )
        return matches[0]

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        if not self.transport_enabled:
            raise AssertionError("Frozen transport was called during replay-only execution")
        self.transport_calls += 1
        token_key = _token_key(request.input_continuation_token)
        call_number = self._calls_by_token.get(token_key, 0) + 1
        self._calls_by_token[token_key] = call_number
        transient_failures = int(self.scenario.get("transient_failures", {}).get(token_key, 0))
        if call_number <= transient_failures:
            raise AdapterTransportError(
                "MOCK_TRANSIENT",
                "Frozen transient transport failure",
                retryable=True,
            )
        rate_failures = int(self.scenario.get("rate_limit_failures", {}).get(token_key, 0))
        if call_number <= rate_failures:
            rate_limit = RateLimitMetadata(
                limit=100,
                remaining=0,
                reset_at="2026-07-20T01:00:00Z",
                retry_after_seconds=0.0,
            )
            raise AdapterTransportError(
                "MOCK_RATE_LIMIT",
                "Frozen rate limit",
                retryable=True,
                rate_limited=True,
                retry_after_seconds=0.0,
                rate_limit=rate_limit,
            )
        page = self._page(request.input_continuation_token)
        if "malformed_payload" in page:
            raw = page["malformed_payload"].encode("utf-8")
            returned_count = page["returned_count"]
        else:
            records = _page_records(page)
            raw = json.dumps(
                {"records": records}, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            returned_count = len(records)
        rate_value = page.get("rate_limit")
        rate_limit = RateLimitMetadata(**rate_value) if isinstance(rate_value, dict) else None
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=raw,
            returned_count=returned_count,
            provider_total=page.get("provider_total"),
            output_continuation_token=page.get("output_token"),
            continuation_exhausted=page["exhausted"],
            terminal_code=page["terminal_code"],
            source_limit_reached=page.get("source_limit_reached", False),
            rate_limit=rate_limit,
        )

    def normalize(
        self, request: RetrievalRequest, response: AdapterPageResponse
    ) -> tuple[Any, ...]:
        del request
        try:
            payload = json.loads(response.raw_response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RetrievalContractError(f"Frozen response is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"records"}:
            raise RetrievalContractError("Frozen response must contain exactly one records field")
        records = payload["records"]
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise RetrievalContractError("Frozen response records must be a list of objects")
        expected_fields = {
            "native_record_id",
            "native_record_locator",
            "assertion_locator",
            "raw_intervention_assertion",
            "compound_name",
            "evidence_id",
            "mapping_disposition",
        }
        normalized = []
        for row in records:
            if set(row) != expected_fields:
                raise RetrievalContractError("Frozen native record has schema drift")
            disposition = RecordDisposition(row["mapping_disposition"])
            assertions = ()
            if disposition is RecordDisposition.EMITTED_SEEDS:
                route = make_seed_route_template(
                    causal_route=CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
                    disease_state_node=known_node("MOCK:DISEASE-STATE", "Frozen disease state"),
                    intervention_target=known_node("MOCK:TARGET", "Frozen intervention target"),
                    action=InterventionAction.MODULATE,
                    direction=EffectDirection.NORMALIZE,
                    intermediate_state=not_applicable_node(
                        "The direct mock route has no separate intermediate state."
                    ),
                    endpoint_id="EP-MOCK-PRIMARY",
                    evidence_ids=(row["evidence_id"],),
                )
                assertions = (
                    make_normalized_seed_assertion(
                        assertion_locator=row["assertion_locator"],
                        raw_intervention_assertion=row["raw_intervention_assertion"],
                        compound_hint_kind=CompoundHintKind.NAME_HINT,
                        compound_hint_value=row["compound_name"],
                        compound_hint_namespace="",
                        endpoint_ids=("EP-MOCK-PRIMARY",),
                        route_templates=(route,),
                        evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
                        chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
                        development_status=DevelopmentStatus.UNKNOWN,
                        uncertainty=(
                            SeedUncertainty(
                                kind=UncertaintyKind.IDENTITY,
                                level=UncertaintyLevel.UNKNOWN,
                                note="The frozen name hint has not undergone identity resolution.",
                            ),
                            SeedUncertainty(
                                kind=UncertaintyKind.SOURCE_COVERAGE,
                                level=UncertaintyLevel.LOW,
                                note="Coverage is limited to the declared frozen mock release.",
                            ),
                        ),
                    ),
                )
            normalized.append(
                make_normalized_source_record(
                    source_id=self.descriptor.source_id,
                    source_release=self.descriptor.source_release,
                    native_record_id=row["native_record_id"],
                    native_record_locator=row["native_record_locator"],
                    source_record=row,
                    disposition=disposition,
                    disposition_reason=(
                        "The normalized mock record contains one in-scope intervention assertion."
                        if disposition is RecordDisposition.EMITTED_SEEDS
                        else "The normalized mock record contains no in-scope intervention assertion."
                    ),
                    screening_rule_id="frozen-mock-record-screen-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(normalized)


__all__ = [
    "DEFAULT_FIXTURE_PATH",
    "FrozenFixtureCatalog",
    "FrozenMockRetrievalAdapter",
]
