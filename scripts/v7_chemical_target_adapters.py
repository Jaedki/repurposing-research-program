#!/usr/bin/env python3
"""High-recall schema-v7 chemical and target source adapters.

The adapters enumerate source-native target, disease, assay, mechanism, and
activity mappings before any candidate-name lookup.  They implement only the
Chat 8 retrieval/normalization boundary: no therapeutic screening, ranking,
audit, persistence, output construction, or runtime scheduling occurs here.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol

from v7_case_model import canonical_bytes, content_sha256
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
    unknown_node,
)
from v7_retrieval_adapter import (
    AdapterDescriptor,
    AdapterPageResponse,
    AdapterTransportError,
    ChemicalIdentityMatchLevel,
    ChemicalIdentityReference,
    CoverageProof,
    DenominatorKind,
    NormalizedSourceRecord,
    PaginationKind,
    QueryPlan,
    RateLimitMetadata,
    RecordDisposition,
    RetrievalContractError,
    RetrievalRequest,
    RetryPolicy,
    SourceActivityObservation,
    make_adapter_descriptor,
    make_chemical_identity_reference,
    make_normalized_seed_assertion,
    make_normalized_source_record,
    make_query_plan,
    make_seed_route_template,
    make_source_activity_observation,
    make_source_mapping_context,
    make_source_universe,
    validate_coverage_proof,
)
from v7_seed_funnel import CompoundHintKind, SeedUncertainty


ADAPTER_VERSION = "schema-v7-chemical-target-adapters-v1"
UNION_MODEL_VERSION = "schema-v7-cross-adapter-chemical-union-v1"
UNICHEM_RESOLVER_VERSION = "schema-v7-unichem-resolver-v1"

OPEN_TARGETS_DOCS = "https://platform-docs.opentargets.org/data-access/graphql-api"
CHEMBL_DOCS = "https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services"
BINDINGDB_DOCS = "https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp"
PUBCHEM_DOCS = "https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest"
UNICHEM_DOCS = "https://www.ebi.ac.uk/unichem/api/docs"

OPEN_TARGETS_GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"
CHEMBL_API_URL = "https://www.ebi.ac.uk/chembl/api/data"
BINDINGDB_API_URL = "https://bindingdb.org/rest/getLigandsByUniprot"
PUBCHEM_PUG_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
UNICHEM_API_URL = "https://www.ebi.ac.uk/unichem/rest/verbose_inchikey"

OPEN_TARGETS_TARGET_QUERY = """query TargetDrugCandidates($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    drugAndClinicalCandidates {
      count
      rows {
        id
        maxClinicalStage
        drug { id name drugType maximumClinicalStage }
        diseases { diseaseFromSource disease { id name } }
      }
    }
  }
}"""

OPEN_TARGETS_DISEASE_QUERY = """query DiseaseDrugCandidates($efoId: String!) {
  disease(efoId: $efoId) {
    id
    name
    drugAndClinicalCandidates {
      count
      rows {
        id
        maxClinicalStage
        drug { id name drugType maximumClinicalStage }
      }
    }
  }
}"""


class ChemicalTargetAdapterError(RetrievalContractError):
    """Raised when a source-specific response violates its declared schema."""


class OpenTargetsEntityKind(str, Enum):
    TARGET = "target"
    DISEASE = "disease"


class PubChemQueryKind(str, Enum):
    GENE_ASSAY_IDS = "gene_assay_ids"
    ASSAY_CONCISE = "assay_concise"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse: ...


class UrllibHttpTransport:
    """Credential-free HTTPS transport used only when live retrieval is requested."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as stream:
                return HttpResponse(
                    status=int(stream.status),
                    headers={key.casefold(): value for key, value in stream.headers.items()},
                    body=stream.read(),
                )
        except urllib.error.HTTPError as exc:
            headers_lower = {key.casefold(): value for key, value in exc.headers.items()}
            retry_after = _float_or_none(headers_lower.get("retry-after"))
            raise AdapterTransportError(
                f"HTTP_{exc.code}",
                f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}",
                retryable=exc.code in {408, 425, 429, 500, 502, 503, 504},
                rate_limited=exc.code == 429,
                retry_after_seconds=retry_after,
                rate_limit=_rate_limit_metadata(headers_lower, retry_after),
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterTransportError(
                "NETWORK_ERROR",
                f"Network error from {urllib.parse.urlsplit(url).netloc}: {exc.reason}",
                retryable=True,
            ) from exc


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rate_limit_metadata(
    headers: Mapping[str, str], retry_after: float | None = None
) -> RateLimitMetadata | None:
    limit = _int_or_none(headers.get("x-ratelimit-limit"))
    remaining = _int_or_none(headers.get("x-ratelimit-remaining"))
    reset = headers.get("x-ratelimit-reset")
    if limit is None and remaining is None and reset is None and retry_after is None:
        return None
    return RateLimitMetadata(
        limit=limit,
        remaining=remaining,
        reset_at=reset if reset and "T" in reset else None,
        retry_after_seconds=retry_after,
    )


def _json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ChemicalTargetAdapterError(f"{label}: response is not valid UTF-8 JSON") from exc


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChemicalTargetAdapterError(f"{label}: expected an object")
    return dict(value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ChemicalTargetAdapterError(f"{label}: expected a list")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not " ".join(value.split()):
        raise ChemicalTargetAdapterError(f"{label}: expected nonblank text")
    return value


def _source_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _transport_response(
    transport: HttpTransport,
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
) -> HttpResponse:
    result = transport.request(
        method,
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": content_type,
            "User-Agent": "repurposing-research-program/schema-v7",
        },
        body=body,
    )
    if not isinstance(result, HttpResponse) or not 200 <= result.status < 300:
        status = result.status if isinstance(result, HttpResponse) else "UNKNOWN"
        raise AdapterTransportError(
            f"HTTP_{status}",
            f"Source transport returned HTTP {status}",
            retryable=False,
        )
    return result


def _seed_context(
    *,
    endpoint_ids: Iterable[str],
    disease_state_id: str,
    target_id: str,
    target_organism: str,
) -> dict[str, Any]:
    endpoints = tuple(sorted({str(value).strip() for value in endpoint_ids if str(value).strip()}))
    if not endpoints:
        raise ChemicalTargetAdapterError("at least one case endpoint ID is required")
    return {
        "endpoint_ids": endpoints,
        "disease_state_id": disease_state_id.strip(),
        "target_id": target_id.strip(),
        "target_organism": target_organism.strip(),
    }


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(max_attempts=3, backoff_seconds=(1.0, 2.0))


def make_open_targets_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    entity_kind: OpenTargetsEntityKind,
    entity_id: str,
    endpoint_ids: Iterable[str],
    disease_state_id: str = "",
    target_organism: str = "Homo sapiens",
    required: bool = True,
) -> QueryPlan:
    if not isinstance(entity_kind, OpenTargetsEntityKind):
        raise ChemicalTargetAdapterError("Open Targets entity kind is invalid")
    entity = _required_text(entity_id, "Open Targets entity_id")
    query = (
        OPEN_TARGETS_TARGET_QUERY
        if entity_kind is OpenTargetsEntityKind.TARGET
        else OPEN_TARGETS_DISEASE_QUERY
    )
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=disease_state_id or entity,
        target_id=entity if entity_kind is OpenTargetsEntityKind.TARGET else "",
        target_organism=target_organism,
    )
    universe = make_source_universe(
        source_id="open-targets-platform",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"{entity_kind.value} drugAndClinicalCandidates for {entity}",
        source_side_filters={"entity_kind": entity_kind.value, "entity_id": entity},
        local_filters={"intervention_type": "small molecule"},
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.NONE,
        limitations=(
            "Coverage is bounded to one Open Targets entity and the declared Platform release.",
            "The GraphQL API is intended for single-entity queries; systematic release-wide coverage requires downloads.",
            "Non-small-molecule rows are individually ledgered as type exclusions.",
            f"Official API documentation: {OPEN_TARGETS_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"open_targets:{entity_kind.value}:drug_candidates:{entity}",
        required=required,
        exact_request_parameters={
            "endpoint_url": OPEN_TARGETS_GRAPHQL_URL,
            "operation_name": (
                "TargetDrugCandidates"
                if entity_kind is OpenTargetsEntityKind.TARGET
                else "DiseaseDrugCandidates"
            ),
            "graphql_query": query,
            "entity_kind": entity_kind.value,
            "entity_id": entity,
            "seed_context": context,
        },
        initial_continuation_token=None,
        max_pages=1,
        max_records=None,
        allowed_terminal_codes=("graphql_complete",),
        retry_policy=_retry_policy(),
    )


def make_chembl_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    resource: str,
    filters: Mapping[str, Any],
    endpoint_ids: Iterable[str],
    origin_kind: str,
    origin_ids: Iterable[str],
    target_id: str = "",
    target_organism: str = "",
    disease_state_id: str = "",
    page_size: int = 1000,
    required: bool = True,
) -> QueryPlan:
    resource_name = resource.strip().casefold()
    if resource_name not in {"target", "mechanism", "molecule", "activity"}:
        raise ChemicalTargetAdapterError("unsupported ChEMBL resource")
    frozen_filters = {str(key): value for key, value in sorted(filters.items())}
    prohibited = {key for key in frozen_filters if "pref_name" in key or key == "search"}
    if prohibited:
        raise ChemicalTargetAdapterError("candidate-name searches are prohibited at enumeration depth")
    origin = origin_kind.strip().casefold()
    if origin not in {"target", "pathway", "assay", "disease", "source_mapping"}:
        raise ChemicalTargetAdapterError("ChEMBL plans require a target/pathway/assay/disease mapping origin")
    origins = tuple(sorted({_required_text(value, "origin_id") for value in origin_ids}))
    if not origins:
        raise ChemicalTargetAdapterError("ChEMBL plans require at least one origin ID")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ChemicalTargetAdapterError("ChEMBL page_size must be positive")
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=disease_state_id,
        target_id=target_id,
        target_organism=target_organism,
    )
    universe = make_source_universe(
        source_id="chembl",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"ChEMBL {resource_name} resource for declared {origin} origins",
        source_side_filters={"resource": resource_name, "filters": frozen_filters},
        local_filters={"origin_kind": origin, "origin_ids": origins},
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.PAGE,
        continuation_parameter="offset",
        limitations=(
            "Coverage is exact only for the declared ChEMBL resource, release, filters, and completed offset traversal.",
            "Target, mechanism, molecule, and activity records have different native identifier and completeness semantics.",
            f"Official API documentation: {CHEMBL_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"chembl:{resource_name}:{origin}:{content_sha256(origins)[:12]}",
        required=required,
        exact_request_parameters={
            "base_url": CHEMBL_API_URL,
            "resource": resource_name,
            "format": "json",
            "limit": page_size,
            "filters": frozen_filters,
            "origin_kind": origin,
            "origin_ids": origins,
            "seed_context": context,
        },
        initial_continuation_token="0",
        max_pages=None,
        max_records=None,
        allowed_terminal_codes=("chembl_page", "chembl_complete"),
        retry_policy=_retry_policy(),
    )


def make_bindingdb_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    uniprot_id: str,
    endpoint_ids: Iterable[str],
    target_organism: str = "",
    affinity_cutoff: int | None = None,
    disease_state_id: str = "",
    required: bool = True,
) -> QueryPlan:
    accession = _required_text(uniprot_id, "BindingDB UniProt ID")
    if affinity_cutoff is not None and (
        isinstance(affinity_cutoff, bool)
        or not isinstance(affinity_cutoff, int)
        or affinity_cutoff < 1
    ):
        raise ChemicalTargetAdapterError("BindingDB affinity cutoff must be positive")
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=disease_state_id,
        target_id=accession,
        target_organism=target_organism,
    )
    universe = make_source_universe(
        source_id="bindingdb",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"BindingDB ligands returned for UniProt {accession}",
        source_side_filters={"uniprot": accession, "affinity_cutoff": affinity_cutoff},
        local_filters={},
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.NONE,
        limitations=(
            "The public getLigandsByUniprot service is non-paginated and source-reported hit counts bound only this request.",
            "The response does not provide assay identifiers, organism, confidence, or explicit affinity units for each row; absent fields remain blank.",
            f"Official API documentation: {BINDINGDB_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"bindingdb:uniprot:{accession}",
        required=required,
        exact_request_parameters={
            "endpoint_url": BINDINGDB_API_URL,
            "uniprot_id": accession,
            "affinity_cutoff": affinity_cutoff,
            "response": "application/json",
            "seed_context": context,
        },
        initial_continuation_token=None,
        max_pages=1,
        max_records=None,
        allowed_terminal_codes=("bindingdb_complete",),
        retry_policy=_retry_policy(),
    )


def make_pubchem_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    query_kind: PubChemQueryKind,
    identifier: str,
    endpoint_ids: Iterable[str],
    target_id: str = "",
    target_organism: str = "",
    disease_state_id: str = "",
    required: bool = True,
) -> QueryPlan:
    if not isinstance(query_kind, PubChemQueryKind):
        raise ChemicalTargetAdapterError("PubChem query kind is invalid")
    native_identifier = _required_text(identifier, "PubChem identifier")
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=disease_state_id,
        target_id=target_id,
        target_organism=target_organism,
    )
    scope = (
        f"PubChem assay identifiers for NCBI Gene {native_identifier}"
        if query_kind is PubChemQueryKind.GENE_ASSAY_IDS
        else f"PubChem concise bioactivity rows for AID {native_identifier}"
    )
    universe = make_source_universe(
        source_id="pubchem",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=scope,
        source_side_filters={"query_kind": query_kind.value, "identifier": native_identifier},
        local_filters={},
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.NONE,
        limitations=(
            "PUG REST gene and assay operations are non-paginated; large gene-level concise requests may time out.",
            "The recommended high-recall route enumerates AIDs from a target, then retrieves concise rows per AID.",
            "Concise rows do not contain complete assay protocols or universal organism/confidence fields.",
            f"Official API documentation: {PUBCHEM_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"pubchem:{query_kind.value}:{native_identifier}",
        required=required,
        exact_request_parameters={
            "base_url": PUBCHEM_PUG_URL,
            "query_kind": query_kind.value,
            "identifier": native_identifier,
            "seed_context": context,
        },
        initial_continuation_token=None,
        max_pages=1,
        max_records=None,
        allowed_terminal_codes=("pubchem_complete",),
        retry_policy=_retry_policy(),
    )


def _development_status(value: Any) -> DevelopmentStatus:
    token = _source_text(value).upper().replace(" ", "_")
    return {
        "APPROVED": DevelopmentStatus.APPROVED,
        "PHASE_4": DevelopmentStatus.APPROVED,
        "PHASE_3": DevelopmentStatus.PHASE_3,
        "PHASE_2": DevelopmentStatus.PHASE_2,
        "PHASE_1": DevelopmentStatus.PHASE_1,
        "PRECLINICAL": DevelopmentStatus.PRECLINICAL,
        "DISCONTINUED": DevelopmentStatus.DISCONTINUED,
        "WITHDRAWN": DevelopmentStatus.WITHDRAWN,
    }.get(token, DevelopmentStatus.UNKNOWN)


def _status_from_phase(value: Any) -> DevelopmentStatus:
    phase = _int_or_none(value)
    return {
        4: DevelopmentStatus.APPROVED,
        3: DevelopmentStatus.PHASE_3,
        2: DevelopmentStatus.PHASE_2,
        1: DevelopmentStatus.PHASE_1,
        0: DevelopmentStatus.PRECLINICAL,
    }.get(phase, DevelopmentStatus.UNKNOWN)


def _universe_from_status(status: DevelopmentStatus) -> ChemicalUniverse:
    if status is DevelopmentStatus.APPROVED:
        return ChemicalUniverse.APPROVED_HUMAN_USE_COMPOUNDS
    if status in {
        DevelopmentStatus.CLINICAL_STAGE,
        DevelopmentStatus.INVESTIGATIONAL,
        DevelopmentStatus.PHASE_1,
        DevelopmentStatus.PHASE_2,
        DevelopmentStatus.PHASE_3,
    }:
        return ChemicalUniverse.CLINICAL_STAGE_ASSETS
    if status in {
        DevelopmentStatus.SHELVED,
        DevelopmentStatus.FAILED,
        DevelopmentStatus.DISCONTINUED,
        DevelopmentStatus.WITHDRAWN,
    }:
        return ChemicalUniverse.SHELVED_OR_FAILED_ASSETS
    return ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS


def _uncertainty(source_note: str) -> tuple[SeedUncertainty, ...]:
    return (
        SeedUncertainty(
            kind=UncertaintyKind.IDENTITY,
            level=UncertaintyLevel.LOW,
            note="The source-native identifier is retained; downstream chemical equivalence remains unverified.",
        ),
        SeedUncertainty(
            kind=UncertaintyKind.SOURCE_COVERAGE,
            level=UncertaintyLevel.MEDIUM,
            note=source_note,
        ),
    )


def _route_templates(
    request: RetrievalRequest,
    *,
    evidence_id: str,
    target_id: str,
    action: InterventionAction,
    causal_route: CausalRoute = CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION,
) -> tuple[Any, ...]:
    parameters = request.exact_request_parameters
    context = _mapping(parameters["seed_context"], "seed_context")
    disease_id = _source_text(context.get("disease_state_id"))
    target = target_id or _source_text(context.get("target_id"))
    return tuple(
        make_seed_route_template(
            causal_route=causal_route,
            disease_state_node=(known_node(disease_id) if disease_id else unknown_node("No source disease-state identifier was available.")),
            intervention_target=(known_node(target) if target else unknown_node("No source target identifier was available.")),
            action=action,
            direction=EffectDirection.UNKNOWN,
            intermediate_state=not_applicable_node("No distinct intermediate state is asserted at source-enumeration depth."),
            endpoint_id=endpoint_id,
            evidence_ids=(evidence_id,),
        )
        for endpoint_id in context["endpoint_ids"]
    )


def _action(value: Any) -> InterventionAction:
    token = _source_text(value).upper()
    if "INHIB" in token or token == "BLOCKER":
        return InterventionAction.INHIBIT
    if "ANTAGON" in token:
        return InterventionAction.ANTAGONIZE
    if "AGON" in token:
        return InterventionAction.AGONIZE
    if "ACTIV" in token:
        return InterventionAction.ACTIVATE
    if "DEGRAD" in token:
        return InterventionAction.DEGRADE
    if "STABIL" in token:
        return InterventionAction.STABILIZE
    return InterventionAction.MODULATE


class OpenTargetsAdapter:
    def __init__(
        self,
        source_release: str,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="open-targets-graphql-v4",
            adapter_version=ADAPTER_VERSION,
            source_id="open-targets-platform",
            source_release=source_release,
            capabilities=("pagination:none", "target_disease_drug_mapping"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = (
            query_plan.source_universe.source_id == self.descriptor.source_id
            and query_plan.query_family_id.startswith("open_targets:")
            and query_plan.source_universe.pagination_kind is PaginationKind.NONE
        )
        return supported, "" if supported else "Plan is not an Open Targets single-entity drug-candidate query."

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        parameters = request.exact_request_parameters
        entity_kind = OpenTargetsEntityKind(parameters["entity_kind"])
        variable_name = "ensemblId" if entity_kind is OpenTargetsEntityKind.TARGET else "efoId"
        body = canonical_bytes(
            {
                "operationName": parameters["operation_name"],
                "query": parameters["graphql_query"],
                "variables": {variable_name: parameters["entity_id"]},
            }
        )
        result = _transport_response(
            self.transport,
            "POST",
            parameters["endpoint_url"],
            body=body,
        )
        payload = _mapping(_json(result.body, "Open Targets"), "Open Targets response")
        if payload.get("errors"):
            raise ChemicalTargetAdapterError("Open Targets GraphQL response contains errors")
        root = _mapping(_mapping(payload.get("data"), "Open Targets data").get(entity_kind.value), "Open Targets entity")
        candidates = _mapping(root.get("drugAndClinicalCandidates"), "Open Targets drugAndClinicalCandidates")
        rows = _list(candidates.get("rows"), "Open Targets rows")
        count = _int_or_none(candidates.get("count"))
        if count is None:
            raise ChemicalTargetAdapterError("Open Targets response omitted its candidate count")
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(rows),
            provider_total=count,
            output_continuation_token=None,
            continuation_exhausted=True,
            terminal_code="graphql_complete",
            rate_limit=_rate_limit_metadata(result.headers),
        )

    def normalize(self, request: RetrievalRequest, response: AdapterPageResponse) -> tuple[NormalizedSourceRecord, ...]:
        parameters = request.exact_request_parameters
        entity_kind = OpenTargetsEntityKind(parameters["entity_kind"])
        payload = _mapping(_json(response.raw_response, "Open Targets"), "Open Targets response")
        root = _mapping(_mapping(payload["data"], "Open Targets data")[entity_kind.value], "Open Targets entity")
        rows = _list(_mapping(root["drugAndClinicalCandidates"], "Open Targets candidates")["rows"], "Open Targets rows")
        records: list[NormalizedSourceRecord] = []
        for row_value in rows:
            row = _mapping(row_value, "Open Targets row")
            native_id = _required_text(row.get("id"), "Open Targets row id")
            drug = row.get("drug")
            disposition = RecordDisposition.EMITTED_SEEDS
            assertions: tuple[Any, ...] = ()
            reason = "The target/disease mapping identifies one in-scope small-molecule intervention."
            if not isinstance(drug, dict):
                disposition = RecordDisposition.FAILED_MAPPING
                reason = "The source row did not resolve its linked drug object."
            elif _source_text(drug.get("drugType")).casefold() != "small molecule":
                disposition = RecordDisposition.NON_INTERVENTION_TYPE_EXCLUDED
                reason = f"The source classified this intervention as {_source_text(drug.get('drugType')) or 'unknown type'}, outside the declared small-molecule scope."
            else:
                drug_id = _required_text(drug.get("id"), "Open Targets drug id")
                name = _required_text(drug.get("name"), "Open Targets drug name")
                status = _development_status(row.get("maxClinicalStage") or drug.get("maximumClinicalStage"))
                target_id = parameters["entity_id"] if entity_kind is OpenTargetsEntityKind.TARGET else ""
                activity = make_source_activity_observation(
                    activity_type="target_disease_drug_mapping",
                    value=_source_text(row.get("maxClinicalStage") or drug.get("maximumClinicalStage")),
                    units="clinical_stage",
                    assay_context=f"Open Targets {entity_kind.value} drugAndClinicalCandidates",
                    target_id=target_id,
                    target_organism=_mapping(parameters["seed_context"], "seed_context").get("target_organism", ""),
                )
                mapping_contexts = []
                if entity_kind is OpenTargetsEntityKind.TARGET:
                    disease_rows = row.get("diseases", [])
                    if not isinstance(disease_rows, list):
                        raise ChemicalTargetAdapterError("Open Targets diseases must be a list")
                    for disease_value in disease_rows:
                        disease_row = _mapping(disease_value, "Open Targets disease mapping")
                        disease = disease_row.get("disease")
                        disease_id = ""
                        disease_context = _source_text(disease_row.get("diseaseFromSource"))
                        if isinstance(disease, dict):
                            disease_id = _source_text(disease.get("id"))
                            disease_context = _source_text(disease.get("name")) or disease_context
                        if not disease_id and disease_context:
                            disease_id = f"SOURCE-LABEL:{disease_context}"
                        if disease_id:
                            mapping_contexts.append(
                                make_source_mapping_context(
                                    mapping_type="target_disease_drug",
                                    target_id=target_id,
                                    disease_id=disease_id,
                                    source_context=disease_context,
                                )
                            )
                    if not mapping_contexts:
                        mapping_contexts.append(
                            make_source_mapping_context(
                                mapping_type="target_drug",
                                target_id=target_id,
                                source_context="No disease list was supplied for this target-drug row.",
                            )
                        )
                else:
                    mapping_contexts.append(
                        make_source_mapping_context(
                            mapping_type="disease_drug",
                            disease_id=str(parameters["entity_id"]),
                            source_context=_source_text(root.get("name")),
                        )
                    )
                assertions = (
                    make_normalized_seed_assertion(
                        assertion_locator=f"/data/{entity_kind.value}/drugAndClinicalCandidates/rows/{native_id}/drug",
                        raw_intervention_assertion=name,
                        compound_hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
                        compound_hint_value=drug_id,
                        compound_hint_namespace="CHEMBL",
                        endpoint_ids=_mapping(parameters["seed_context"], "seed_context")["endpoint_ids"],
                        route_templates=_route_templates(
                            request,
                            evidence_id=f"OPEN-TARGETS:{native_id}",
                            target_id=target_id,
                            action=InterventionAction.MODULATE,
                        ),
                        evidence_modalities=(EvidenceModality.AUTHORITATIVE_PHARMACOLOGY,),
                        chemical_universes=(_universe_from_status(status),),
                        development_status=status,
                        uncertainty=_uncertainty("Coverage is bounded to the exact Open Targets entity query and release."),
                        activity_observations=(activity,),
                        identity_references=(
                            make_chemical_identity_reference(
                                namespace="CHEMBL",
                                identifier=drug_id,
                                match_level=ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
                                authority="Open Targets Platform",
                                authority_release=self.descriptor.source_release,
                            ),
                        ),
                        mapping_contexts=mapping_contexts,
                    ),
                )
            records.append(
                make_normalized_source_record(
                    source_id=self.descriptor.source_id,
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=f"graphql:{entity_kind.value}.drugAndClinicalCandidates.rows[id={native_id}]",
                    source_record=row,
                    disposition=disposition,
                    disposition_reason=reason,
                    screening_rule_id="open-targets-small-molecule-mapping-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(records)


class ChemblAdapter:
    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="chembl-data-web-services",
            adapter_version=ADAPTER_VERSION,
            source_id="chembl",
            source_release=source_release,
            capabilities=("pagination:page", "target", "mechanism", "molecule", "activity"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        resource = query_plan.exact_request_parameters.get("resource")
        supported = (
            query_plan.source_universe.source_id == "chembl"
            and query_plan.query_family_id.startswith("chembl:")
            and resource in {"target", "mechanism", "molecule", "activity"}
        )
        return supported, "" if supported else "Plan is not a declared ChEMBL resource traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        query = dict(_mapping(parameters["filters"], "ChEMBL filters"))
        query.update(
            {
                "format": "json",
                "limit": parameters["limit"],
                "offset": request.input_continuation_token,
            }
        )
        return f"{parameters['base_url']}/{parameters['resource']}.json?{urllib.parse.urlencode(query, doseq=True)}"

    @staticmethod
    def _collection_key(resource: str) -> str:
        return "activities" if resource == "activity" else f"{resource}s"

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        payload = _mapping(_json(result.body, "ChEMBL"), "ChEMBL response")
        resource = str(request.exact_request_parameters["resource"])
        collection_key = self._collection_key(resource)
        rows = _list(payload.get(collection_key), f"ChEMBL {collection_key}")
        meta = _mapping(payload.get("page_meta"), "ChEMBL page_meta")
        total = _int_or_none(meta.get("total_count"))
        if total is None:
            raise ChemicalTargetAdapterError("ChEMBL page_meta omitted total_count")
        next_url = meta.get("next")
        next_offset: str | None = None
        if next_url:
            parsed = urllib.parse.urlsplit(str(next_url))
            values = urllib.parse.parse_qs(parsed.query)
            offsets = values.get("offset")
            if not offsets or len(offsets) != 1:
                raise ChemicalTargetAdapterError("ChEMBL next link omitted one offset")
            next_offset = offsets[0]
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(rows),
            provider_total=total,
            output_continuation_token=next_offset,
            continuation_exhausted=next_offset is None,
            terminal_code="chembl_complete" if next_offset is None else "chembl_page",
            rate_limit=_rate_limit_metadata(result.headers),
        )

    def normalize(self, request: RetrievalRequest, response: AdapterPageResponse) -> tuple[NormalizedSourceRecord, ...]:
        resource = str(request.exact_request_parameters["resource"])
        payload = _mapping(_json(response.raw_response, "ChEMBL"), "ChEMBL response")
        collection_key = self._collection_key(resource)
        rows = _list(payload[collection_key], f"ChEMBL {collection_key}")
        records: list[NormalizedSourceRecord] = []
        seen_row_hashes: set[str] = set()
        for row_ordinal, row_value in enumerate(rows, 1):
            row = _mapping(row_value, f"ChEMBL {resource} row")
            native_id = self._native_id(resource, row)
            row_hash = content_sha256(row)
            repeated_source_row = row_hash in seen_row_hashes
            seen_row_hashes.add(row_hash)
            if resource == "mechanism":
                native_id = f"{native_id}:ASSERTION:{row_hash[:16]}"
            if repeated_source_row:
                native_id = f"{native_id}:DUPLICATE:{row_ordinal}"
            assertions: tuple[Any, ...] = ()
            disposition = RecordDisposition.NO_INTERVENTION_MAPPING
            reason = "The source-native target record was enumerated and contains no intervention assertion."
            if repeated_source_row:
                reason = "An exact repeated source row was ledgered without emitting a duplicate seed assertion."
            elif resource != "target":
                molecule_id = _source_text(row.get("molecule_chembl_id"))
                if not molecule_id:
                    disposition = RecordDisposition.FAILED_MAPPING
                    reason = "The source record omitted molecule_chembl_id."
                else:
                    disposition = RecordDisposition.EMITTED_SEEDS
                    reason = "The source-native record maps a ChEMBL molecule from a declared target/assay/mapping origin."
                    assertions = (self._assertion(request, resource, row, native_id, molecule_id),)
            records.append(
                make_normalized_source_record(
                    source_id="chembl",
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=f"/chembl/api/data/{resource}/{native_id}",
                    source_record=row,
                    disposition=disposition,
                    disposition_reason=reason,
                    screening_rule_id=f"chembl-{resource}-mapping-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(records)

    def _native_id(self, resource: str, row: Mapping[str, Any]) -> str:
        candidates = {
            "target": ("target_chembl_id",),
            "molecule": ("molecule_chembl_id",),
            "activity": ("activity_id",),
            "mechanism": ("mechanism_id", "record_id"),
        }[resource]
        for field in candidates:
            value = _source_text(row.get(field))
            if value:
                return value
        projection = {
            key: row.get(key)
            for key in (
                "molecule_chembl_id",
                "target_chembl_id",
                "action_type",
                "mechanism_of_action",
            )
        }
        return f"{resource.upper()}-{content_sha256(projection)[:24]}"

    def _assertion(
        self,
        request: RetrievalRequest,
        resource: str,
        row: Mapping[str, Any],
        native_id: str,
        molecule_id: str,
    ) -> Any:
        withdrawn_flag = row.get("withdrawn_flag")
        withdrawn = resource == "molecule" and (
            withdrawn_flag is True
            or _source_text(withdrawn_flag).casefold() in {"1", "true", "yes"}
        )
        status = (
            DevelopmentStatus.WITHDRAWN
            if withdrawn
            else _status_from_phase(row.get("max_phase"))
        )
        target_id = _source_text(row.get("target_chembl_id")) or _mapping(
            request.exact_request_parameters["seed_context"], "seed_context"
        ).get("target_id", "")
        organism = _source_text(row.get("target_organism")) or _mapping(
            request.exact_request_parameters["seed_context"], "seed_context"
        ).get("target_organism", "")
        name = _source_text(row.get("pref_name") or row.get("molecule_pref_name")) or molecule_id
        observations: tuple[SourceActivityObservation, ...] = ()
        if resource == "activity":
            confidence = _source_text(row.get("assay_confidence_score") or row.get("confidence_score"))
            observations = (
                make_source_activity_observation(
                    activity_type=_source_text(row.get("standard_type") or row.get("type")) or "bioactivity",
                    relation=_source_text(row.get("standard_relation") or row.get("relation")),
                    value=_source_text(row.get("standard_value") or row.get("value")),
                    units=_source_text(row.get("standard_units") or row.get("units")),
                    assay_id=_source_text(row.get("assay_chembl_id")),
                    assay_context=_source_text(row.get("assay_description") or row.get("assay_type")),
                    target_id=target_id,
                    target_organism=organism,
                    confidence=confidence,
                    confidence_scale="ChEMBL assay confidence score" if confidence else "",
                ),
            )
        elif resource == "mechanism":
            observations = (
                make_source_activity_observation(
                    activity_type="mechanism_of_action",
                    value=_source_text(row.get("action_type")),
                    assay_context=_source_text(row.get("mechanism_of_action")),
                    target_id=target_id,
                    target_organism=organism,
                ),
            )
        return make_normalized_seed_assertion(
            assertion_locator=f"/{resource}/{native_id}/molecule_chembl_id",
            raw_intervention_assertion=name,
            compound_hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
            compound_hint_value=molecule_id,
            compound_hint_namespace="CHEMBL",
            endpoint_ids=_mapping(request.exact_request_parameters["seed_context"], "seed_context")["endpoint_ids"],
            route_templates=_route_templates(
                request,
                evidence_id=f"CHEMBL:{resource}:{native_id}",
                target_id=target_id,
                action=_action(row.get("action_type")),
            ),
            evidence_modalities=(
                EvidenceModality.BIOACTIVITY
                if resource == "activity"
                else EvidenceModality.AUTHORITATIVE_PHARMACOLOGY
            ,),
            chemical_universes=(_universe_from_status(status),),
            development_status=status,
            uncertainty=_uncertainty("Coverage is bounded to the exact ChEMBL filters and completed offset traversal."),
            activity_observations=observations,
            identity_references=(
                make_chemical_identity_reference(
                    namespace="CHEMBL",
                    identifier=molecule_id,
                    match_level=ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
                    authority="ChEMBL",
                    authority_release=self.descriptor.source_release,
                ),
            ),
            mapping_contexts=(
                (
                    make_source_mapping_context(
                        mapping_type=f"chembl_{resource}_chemical",
                        target_id=target_id,
                        assay_id=_source_text(row.get("assay_chembl_id")),
                        source_context=_source_text(
                            row.get("assay_description")
                            or row.get("mechanism_of_action")
                        ),
                    ),
                )
                if target_id or _source_text(row.get("assay_chembl_id"))
                else ()
            ),
        )


class BindingDbAdapter:
    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="bindingdb-rest-get-ligands-by-uniprot",
            adapter_version=ADAPTER_VERSION,
            source_id="bindingdb",
            source_release=source_release,
            capabilities=("pagination:none", "target_ligand_affinity"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = query_plan.source_universe.source_id == "bindingdb" and query_plan.query_family_id.startswith("bindingdb:uniprot:")
        return supported, "" if supported else "Plan is not a BindingDB UniProt ligand traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        value = str(parameters["uniprot_id"])
        if parameters.get("affinity_cutoff") is not None:
            value += f";{parameters['affinity_cutoff']}"
        return f"{parameters['endpoint_url']}?{urllib.parse.urlencode({'uniprot': value, 'response': parameters['response']})}"

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        if not result.body.strip():
            count = 0
            rows: list[Any] = []
        else:
            payload = _mapping(_json(result.body, "BindingDB"), "BindingDB response")
            root = _mapping(payload.get("getLindsByUniprotResponse"), "BindingDB getLindsByUniprotResponse")
            rows = _list(root.get("bdb.affinities", []), "BindingDB affinities")
            count = _int_or_none(root.get("bdb.hit"))
            if count is None:
                count = len(rows)
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(rows),
            provider_total=count,
            output_continuation_token=None,
            continuation_exhausted=True,
            terminal_code="bindingdb_complete",
            rate_limit=_rate_limit_metadata(result.headers),
        )

    def normalize(self, request: RetrievalRequest, response: AdapterPageResponse) -> tuple[NormalizedSourceRecord, ...]:
        if not response.raw_response.strip():
            return ()
        payload = _mapping(_json(response.raw_response, "BindingDB"), "BindingDB response")
        root = _mapping(payload["getLindsByUniprotResponse"], "BindingDB result")
        rows = _list(root.get("bdb.affinities", []), "BindingDB affinities")
        seen: set[str] = set()
        records: list[NormalizedSourceRecord] = []
        target_id = _source_text(root.get("bdb.primary")) or str(request.exact_request_parameters["uniprot_id"])
        context = _mapping(request.exact_request_parameters["seed_context"], "seed_context")
        for row_value in rows:
            row = _mapping(row_value, "BindingDB affinity row")
            native_projection = {
                "target": target_id,
                "monomerid": row.get("bdb.monomerid"),
                "affinity_type": row.get("bdb.affinity_type"),
                "affinity": row.get("bdb.affinity"),
                "smile": row.get("bdb.smile"),
            }
            native_id = f"BDB-AFFINITY-{content_sha256(native_projection)[:24]}"
            if native_id in seen:
                raise ChemicalTargetAdapterError("BindingDB repeated an identical source affinity row")
            seen.add(native_id)
            monomer_id = _required_text(
                _source_text(row.get("bdb.monomerid")), "BindingDB monomer ID"
            )
            smiles = _required_text(row.get("bdb.smile"), "BindingDB SMILES")
            observation = make_source_activity_observation(
                activity_type=_required_text(row.get("bdb.affinity_type"), "BindingDB affinity type"),
                value=_required_text(row.get("bdb.affinity"), "BindingDB affinity value"),
                assay_context="BindingDB getLigandsByUniprot affinity mapping",
                target_id=target_id,
                target_organism=context.get("target_organism", ""),
            )
            assertion = make_normalized_seed_assertion(
                assertion_locator=f"/getLindsByUniprotResponse/bdb.affinities/{native_id}",
                raw_intervention_assertion=smiles,
                compound_hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
                compound_hint_value=monomer_id,
                compound_hint_namespace="BINDINGDB",
                endpoint_ids=context["endpoint_ids"],
                route_templates=_route_templates(
                    request,
                    evidence_id=f"BINDINGDB:{native_id}",
                    target_id=target_id,
                    action=InterventionAction.MODULATE,
                ),
                evidence_modalities=(EvidenceModality.BIOACTIVITY,),
                chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
                development_status=DevelopmentStatus.UNKNOWN,
                uncertainty=_uncertainty("BindingDB public target retrieval is non-paginated and omits several assay metadata fields."),
                activity_observations=(observation,),
                identity_references=(
                    make_chemical_identity_reference(
                        namespace="BINDINGDB",
                        identifier=monomer_id,
                        match_level=ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
                        authority="BindingDB",
                        authority_release=self.descriptor.source_release,
                    ),
                ),
                mapping_contexts=(
                    make_source_mapping_context(
                        mapping_type="target_ligand",
                        target_id=target_id,
                        source_context="BindingDB getLigandsByUniprot",
                    ),
                ),
            )
            records.append(
                make_normalized_source_record(
                    source_id="bindingdb",
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=f"getLigandsByUniprot:{target_id}:{native_id}",
                    source_record=row,
                    disposition=RecordDisposition.EMITTED_SEEDS,
                    disposition_reason="The source affinity row identifies one ligand mapped from the declared UniProt target.",
                    screening_rule_id="bindingdb-target-ligand-mapping-v1",
                    seed_assertions=(assertion,),
                )
            )
        return tuple(records)


class PubChemAdapter:
    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="pubchem-pug-rest",
            adapter_version=ADAPTER_VERSION,
            source_id="pubchem",
            source_release=source_release,
            capabilities=("pagination:none", "gene_assay_ids", "assay_concise"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = query_plan.source_universe.source_id == "pubchem" and query_plan.query_family_id.startswith("pubchem:")
        return supported, "" if supported else "Plan is not a PubChem target/assay traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        identifier = urllib.parse.quote(str(parameters["identifier"]), safe="")
        if parameters["query_kind"] == PubChemQueryKind.GENE_ASSAY_IDS.value:
            return f"{parameters['base_url']}/gene/geneid/{identifier}/aids/JSON"
        return f"{parameters['base_url']}/assay/aid/{identifier}/concise/JSON"

    @staticmethod
    def _gene_assay_ids(payload: Mapping[str, Any]) -> list[Any]:
        """Accept both documented PubChem gene-to-AID response envelopes."""

        identifier_list = payload.get("IdentifierList")
        if isinstance(identifier_list, Mapping):
            return _list(identifier_list.get("AID", []), "PubChem AIDs")
        information_list = _mapping(
            payload.get("InformationList"), "PubChem InformationList"
        )
        information = _list(
            information_list.get("Information", []), "PubChem Information"
        )
        aids: list[Any] = []
        for entry in information:
            aids.extend(
                _list(_mapping(entry, "PubChem Information entry").get("AID", []), "PubChem AIDs")
            )
        return aids

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        payload = _mapping(_json(result.body, "PubChem"), "PubChem response")
        if request.exact_request_parameters["query_kind"] == PubChemQueryKind.GENE_ASSAY_IDS.value:
            rows = self._gene_assay_ids(payload)
        else:
            rows = _list(_mapping(payload.get("Table"), "PubChem Table").get("Row", []), "PubChem concise rows")
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(rows),
            provider_total=len(rows),
            output_continuation_token=None,
            continuation_exhausted=True,
            terminal_code="pubchem_complete",
            rate_limit=_rate_limit_metadata(result.headers),
        )

    def normalize(self, request: RetrievalRequest, response: AdapterPageResponse) -> tuple[NormalizedSourceRecord, ...]:
        kind = PubChemQueryKind(request.exact_request_parameters["query_kind"])
        payload = _mapping(_json(response.raw_response, "PubChem"), "PubChem response")
        if kind is PubChemQueryKind.GENE_ASSAY_IDS:
            aids = self._gene_assay_ids(payload)
            return tuple(
                make_normalized_source_record(
                    source_id="pubchem",
                    source_release=self.descriptor.source_release,
                    native_record_id=f"AID:{aid}",
                    native_record_locator=f"/assay/aid/{aid}",
                    source_record={"AID": aid},
                    disposition=RecordDisposition.NO_INTERVENTION_MAPPING,
                    disposition_reason="The target-derived assay identifier is retained for the next assay-to-compound traversal.",
                    screening_rule_id="pubchem-target-assay-enumeration-v1",
                )
                for aid in aids
            )
        table = _mapping(payload["Table"], "PubChem Table")
        columns = _list(_mapping(table["Columns"], "PubChem Columns")["Column"], "PubChem column names")
        rows = _list(table.get("Row", []), "PubChem concise rows")
        records: list[NormalizedSourceRecord] = []
        context = _mapping(request.exact_request_parameters["seed_context"], "seed_context")
        for row_ordinal, row_value in enumerate(rows, 1):
            cells = _list(_mapping(row_value, "PubChem row").get("Cell"), "PubChem cells")
            if len(cells) != len(columns):
                raise ChemicalTargetAdapterError("PubChem concise row/column cardinality differs")
            row = {str(columns[index]): cells[index] for index in range(len(columns))}
            aid = _required_text(_source_text(row.get("AID")), "PubChem AID")
            sid = _required_text(_source_text(row.get("SID")), "PubChem SID")
            cid = _source_text(row.get("CID"))
            native_id = f"AID:{aid}:ROW:{row_ordinal}:SID:{sid}"
            native_locator = f"/assay/aid/{aid}/concise/row/{row_ordinal}/sid/{sid}"
            if not cid:
                records.append(
                    make_normalized_source_record(
                        source_id="pubchem",
                        source_release=self.descriptor.source_release,
                        native_record_id=native_id,
                        native_record_locator=native_locator,
                        source_record=row,
                        disposition=RecordDisposition.NO_INTERVENTION_MAPPING,
                        disposition_reason="The concise assay row has no normalized PubChem CID mapping.",
                        screening_rule_id="pubchem-concise-cid-mapping-v1",
                    )
                )
                continue
            target_id = _source_text(row.get("Target GeneID") or row.get("Target Accession")) or context.get("target_id", "")
            assay_type = _source_text(row.get("Assay Type"))
            modality = (
                EvidenceModality.PHENOTYPIC_SCREENING
                if "cell" in assay_type.casefold() or not target_id
                else EvidenceModality.BIOACTIVITY
            )
            observation = make_source_activity_observation(
                activity_type=_source_text(row.get("Activity Name")) or "activity_outcome",
                value=_source_text(row.get("Activity Value [uM]") or row.get("Activity Outcome")),
                units="uM" if _source_text(row.get("Activity Value [uM]")) else "",
                assay_id=f"AID:{aid}",
                assay_context=" | ".join(
                    value
                    for value in (_source_text(row.get("Assay Name")), assay_type, _source_text(row.get("Activity Outcome")))
                    if value
                ),
                target_id=target_id,
                target_organism=context.get("target_organism", ""),
            )
            assertion = make_normalized_seed_assertion(
                assertion_locator=f"{native_locator}/CID",
                raw_intervention_assertion=f"PubChem CID {cid}",
                compound_hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
                compound_hint_value=cid,
                compound_hint_namespace="PUBCHEM",
                endpoint_ids=context["endpoint_ids"],
                route_templates=_route_templates(
                    request,
                    evidence_id=f"PUBCHEM:{native_id}",
                    target_id=target_id,
                    action=InterventionAction.MODULATE,
                    causal_route=(
                        CausalRoute.PHENOTYPE_OR_STATE_REVERSAL
                        if modality is EvidenceModality.PHENOTYPIC_SCREENING
                        else CausalRoute.DIRECT_DISEASE_DRIVER_MODULATION
                    ),
                ),
                evidence_modalities=(modality,),
                chemical_universes=(ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS,),
                development_status=DevelopmentStatus.UNKNOWN,
                uncertainty=_uncertainty("Coverage is bounded to one PubChem AID concise result; assay protocol detail may require a separate description query."),
                activity_observations=(observation,),
                identity_references=(
                    make_chemical_identity_reference(
                        namespace="PUBCHEM",
                        identifier=cid,
                        match_level=ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
                        authority="PubChem",
                        authority_release=self.descriptor.source_release,
                    ),
                ),
                mapping_contexts=(
                    make_source_mapping_context(
                        mapping_type="target_assay_compound",
                        target_id=target_id,
                        assay_id=f"AID:{aid}",
                        source_context=_source_text(row.get("Assay Name")),
                    ),
                ),
            )
            records.append(
                make_normalized_source_record(
                    source_id="pubchem",
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=native_locator,
                    source_record=row,
                    disposition=RecordDisposition.EMITTED_SEEDS,
                    disposition_reason="The target-derived assay row identifies one PubChem compound mapping regardless of activity outcome.",
                    screening_rule_id="pubchem-concise-cid-mapping-v1",
                    seed_assertions=(assertion,),
                )
            )
        return tuple(records)


@dataclass(frozen=True)
class UniChemCrossReference:
    namespace: str
    identifier: str
    source_id: int
    source_url: str


@dataclass(frozen=True)
class UniChemIdentityResolution:
    resolution_id: str
    input_seed_id: str
    query_namespace: str
    query_identifier: str
    exact_query_url: str
    source_release: str
    response_sha256: str
    references: tuple[UniChemCrossReference, ...]
    limitations: tuple[str, ...]


def make_unichem_identity_resolution(
    *,
    input_seed_id: str,
    query_identifier: str,
    exact_query_url: str,
    source_release: str,
    response_sha256: str,
    references: Iterable[UniChemCrossReference],
    limitations: Iterable[str],
) -> UniChemIdentityResolution:
    digest = _required_text(response_sha256, "UniChem response SHA-256").upper()
    if len(digest) != 64:
        raise ChemicalTargetAdapterError("UniChem response SHA-256 is malformed")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ChemicalTargetAdapterError("UniChem response SHA-256 is not hexadecimal") from exc
    refs = tuple(
        sorted(
            set(references),
            key=lambda value: (value.namespace, value.identifier, value.source_id),
        )
    )
    for reference in refs:
        if not isinstance(reference, UniChemCrossReference):
            raise ChemicalTargetAdapterError("UniChem references contain an invalid record")
        _required_text(reference.namespace, "UniChem reference namespace")
        _required_text(reference.identifier, "UniChem reference identifier")
        if isinstance(reference.source_id, bool) or not isinstance(reference.source_id, int) or reference.source_id < 1:
            raise ChemicalTargetAdapterError("UniChem reference source_id must be positive")
    body = {
        "input_seed_id": _required_text(input_seed_id, "UniChem input seed ID"),
        "query_namespace": "INCHIKEY",
        "query_identifier": _required_text(query_identifier, "InChIKey").upper(),
        "exact_query_url": _required_text(exact_query_url, "UniChem exact query URL"),
        "source_release": _required_text(source_release, "UniChem source release"),
        "response_sha256": digest,
        "references": refs,
        "limitations": tuple(sorted({_required_text(value, "UniChem limitation") for value in limitations})),
    }
    return UniChemIdentityResolution(
        resolution_id=f"UNICHEM-RESOLUTION-{content_sha256({'rule': UNICHEM_RESOLVER_VERSION, 'body': body})[:24]}",
        **body,
    )


def validate_unichem_identity_resolution(value: UniChemIdentityResolution) -> None:
    if not isinstance(value, UniChemIdentityResolution):
        raise ChemicalTargetAdapterError("expected UniChemIdentityResolution")
    rebuilt = make_unichem_identity_resolution(
        input_seed_id=value.input_seed_id,
        query_identifier=value.query_identifier,
        exact_query_url=value.exact_query_url,
        source_release=value.source_release,
        response_sha256=value.response_sha256,
        references=value.references,
        limitations=value.limitations,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(value):
        raise ChemicalTargetAdapterError("UniChem resolution differs from its exact source facts")


class UniChemIdentityResolver:
    """Bounded exact-InChIKey cross-reference support for existing seeds."""

    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.source_release = _required_text(source_release, "UniChem source release")
        self.transport = transport or UrllibHttpTransport()

    def resolve_inchikey(self, *, input_seed_id: str, inchikey: str) -> UniChemIdentityResolution:
        seed_id = _required_text(input_seed_id, "UniChem input seed ID")
        key = _required_text(inchikey, "InChIKey").upper()
        url = f"{UNICHEM_API_URL}/{urllib.parse.quote(key, safe='-')}"
        result = _transport_response(self.transport, "GET", url)
        rows = _list(_json(result.body, "UniChem"), "UniChem verbose_inchikey response")
        references: list[UniChemCrossReference] = []
        for row_value in rows:
            row = _mapping(row_value, "UniChem source row")
            namespace = _required_text(row.get("name"), "UniChem source name").upper()
            source_id = _int_or_none(row.get("src_id"))
            if source_id is None:
                raise ChemicalTargetAdapterError("UniChem source row omitted src_id")
            identifiers = _list(row.get("src_compound_id"), "UniChem source compound IDs")
            for identifier in identifiers:
                references.append(
                    UniChemCrossReference(
                        namespace=namespace,
                        identifier=_required_text(identifier, "UniChem source compound ID"),
                        source_id=source_id,
                        source_url=_source_text(row.get("src_url")),
                    )
                )
        return make_unichem_identity_resolution(
            input_seed_id=seed_id,
            query_identifier=key,
            exact_query_url=url,
            source_release=self.source_release,
            response_sha256=_sha256(result.body),
            references=references,
            limitations=(
                "This resolver uses exact InChIKey lookup; connectivity-only equivalence is not promoted to exact identity.",
                "UniChem cross-references do not by themselves establish active-moiety, formulation, route, or evidence-transfer equivalence.",
                f"Official API documentation: {UNICHEM_DOCS}",
            ),
        )


@dataclass(frozen=True)
class CrossAdapterChemicalRecord:
    union_record_id: str
    identity_keys: tuple[str, ...]
    seed_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_mapping_ids: tuple[str, ...]
    query_plan_ids: tuple[str, ...]
    retrieval_content_receipt_ids: tuple[str, ...]
    emission_link_ids: tuple[str, ...]
    raw_intervention_assertions: tuple[str, ...]


@dataclass(frozen=True)
class CrossAdapterChemicalUnion:
    schema_version: int
    model_version: str
    union_id: str
    input_seed_count: int
    union_record_count: int
    provenance_edge_count: int
    records: tuple[CrossAdapterChemicalRecord, ...]


def _identity_key(reference: ChemicalIdentityReference) -> str | None:
    if reference.match_level not in {
        ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
        ChemicalIdentityMatchLevel.EXACT_STRUCTURE,
    }:
        return None
    return f"{reference.namespace}:{reference.identifier}"


def union_cross_adapter_chemicals(
    proofs: Iterable[CoverageProof],
    *,
    unichem_resolutions: Iterable[UniChemIdentityResolution] = (),
) -> CrossAdapterChemicalUnion:
    """Union exact identities while retaining every source seed and receipt edge."""

    proof_rows = tuple(sorted(proofs, key=lambda value: value.query_plan.query_plan_id))
    if not proof_rows:
        raise ChemicalTargetAdapterError("cross-adapter union requires at least one proof")
    for proof in proof_rows:
        validate_coverage_proof(None, proof)
    seed_payload: dict[str, dict[str, set[str]]] = {}
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def merge(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    identity_owner: dict[str, str] = {}
    for proof in proof_rows:
        records = {row.normalized_record_id: row for row in proof.normalized_records}
        for emission in proof.seed_emissions:
            seed_id = emission.seed.seed_id
            find(seed_id)
            record = records[emission.normalized_record_id]
            assertion = next(
                value
                for value in record.seed_assertions
                if value.assertion_locator == emission.assertion_locator
            )
            payload = seed_payload.setdefault(
                seed_id,
                {
                    "identity_keys": set(),
                    "source_ids": set(),
                    "source_mapping_ids": set(),
                    "query_plan_ids": set(),
                    "retrieval_content_receipt_ids": set(),
                    "emission_link_ids": set(),
                    "raw_intervention_assertions": set(),
                },
            )
            payload["source_ids"].add(record.source_id)
            payload["source_mapping_ids"].add(emission.source_mapping.mapping_id)
            payload["query_plan_ids"].add(proof.query_plan.query_plan_id)
            payload["retrieval_content_receipt_ids"].add(record.retrieval_content_receipt_id)
            payload["emission_link_ids"].add(emission.emission_link_id)
            payload["raw_intervention_assertions"].add(assertion.raw_intervention_assertion)
            for reference in assertion.identity_references:
                key = _identity_key(reference)
                if key is None:
                    continue
                payload["identity_keys"].add(key)
                owner = identity_owner.setdefault(key, seed_id)
                merge(seed_id, owner)

    for resolution in sorted(unichem_resolutions, key=lambda value: value.resolution_id):
        validate_unichem_identity_resolution(resolution)
        if resolution.input_seed_id not in seed_payload:
            raise ChemicalTargetAdapterError("UniChem resolution refers to a seed absent from the union")
        payload = seed_payload[resolution.input_seed_id]
        inchikey_key = f"INCHIKEY:{resolution.query_identifier}"
        payload["identity_keys"].add(inchikey_key)
        owner = identity_owner.setdefault(inchikey_key, resolution.input_seed_id)
        merge(resolution.input_seed_id, owner)
        for reference in resolution.references:
            key = f"{reference.namespace}:{reference.identifier}"
            payload["identity_keys"].add(key)
            owner = identity_owner.setdefault(key, resolution.input_seed_id)
            merge(resolution.input_seed_id, owner)

    groups: dict[str, list[str]] = {}
    for seed_id in sorted(seed_payload):
        groups.setdefault(find(seed_id), []).append(seed_id)
    records: list[CrossAdapterChemicalRecord] = []
    for seed_ids in groups.values():
        combined = {
            key: set().union(*(seed_payload[seed_id][key] for seed_id in seed_ids))
            for key in next(iter(seed_payload.values()))
        }
        body = {
            "identity_keys": tuple(sorted(combined["identity_keys"])),
            "seed_ids": tuple(sorted(seed_ids)),
            "source_ids": tuple(sorted(combined["source_ids"])),
            "source_mapping_ids": tuple(sorted(combined["source_mapping_ids"])),
            "query_plan_ids": tuple(sorted(combined["query_plan_ids"])),
            "retrieval_content_receipt_ids": tuple(sorted(combined["retrieval_content_receipt_ids"])),
            "emission_link_ids": tuple(sorted(combined["emission_link_ids"])),
            "raw_intervention_assertions": tuple(sorted(combined["raw_intervention_assertions"])),
        }
        records.append(
            CrossAdapterChemicalRecord(
                union_record_id=f"CHEMICAL-UNION-{content_sha256({'rule': UNION_MODEL_VERSION, 'body': body})[:24]}",
                **body,
            )
        )
    result_rows = tuple(sorted(records, key=lambda value: value.union_record_id))
    body = {
        "schema_version": 7,
        "model_version": UNION_MODEL_VERSION,
        "input_seed_count": len(seed_payload),
        "union_record_count": len(result_rows),
        "provenance_edge_count": sum(len(value.emission_link_ids) for value in result_rows),
        "records": result_rows,
    }
    result = CrossAdapterChemicalUnion(
        union_id=f"CROSS-ADAPTER-UNION-{content_sha256(body)[:24]}",
        **body,
    )
    validate_cross_adapter_union(result)
    return result


def validate_cross_adapter_union(value: CrossAdapterChemicalUnion) -> None:
    if not isinstance(value, CrossAdapterChemicalUnion):
        raise ChemicalTargetAdapterError("expected CrossAdapterChemicalUnion")
    if value.schema_version != 7 or value.model_version != UNION_MODEL_VERSION:
        raise ChemicalTargetAdapterError("cross-adapter union version mismatch")
    if tuple(sorted(value.records, key=lambda row: row.union_record_id)) != value.records:
        raise ChemicalTargetAdapterError("cross-adapter union records are not canonical")
    seed_ids = [seed_id for row in value.records for seed_id in row.seed_ids]
    if len(seed_ids) != len(set(seed_ids)) or len(seed_ids) != value.input_seed_count:
        raise ChemicalTargetAdapterError("cross-adapter union lost or duplicated a source seed")
    if value.union_record_count != len(value.records):
        raise ChemicalTargetAdapterError("cross-adapter union record count differs")
    if value.provenance_edge_count != sum(len(row.emission_link_ids) for row in value.records):
        raise ChemicalTargetAdapterError("cross-adapter provenance edge count differs")
    body = {
        "schema_version": value.schema_version,
        "model_version": value.model_version,
        "input_seed_count": value.input_seed_count,
        "union_record_count": value.union_record_count,
        "provenance_edge_count": value.provenance_edge_count,
        "records": value.records,
    }
    if value.union_id != f"CROSS-ADAPTER-UNION-{content_sha256(body)[:24]}":
        raise ChemicalTargetAdapterError("cross-adapter union ID mismatch")


__all__ = [
    "ADAPTER_VERSION",
    "BINDINGDB_DOCS",
    "BindingDbAdapter",
    "CHEMBL_DOCS",
    "ChemblAdapter",
    "ChemicalTargetAdapterError",
    "CrossAdapterChemicalRecord",
    "CrossAdapterChemicalUnion",
    "HttpResponse",
    "HttpTransport",
    "OPEN_TARGETS_DOCS",
    "OpenTargetsAdapter",
    "OpenTargetsEntityKind",
    "PUBCHEM_DOCS",
    "PubChemAdapter",
    "PubChemQueryKind",
    "UNICHEM_DOCS",
    "UniChemCrossReference",
    "UniChemIdentityResolution",
    "UniChemIdentityResolver",
    "UrllibHttpTransport",
    "make_bindingdb_plan",
    "make_chembl_plan",
    "make_open_targets_plan",
    "make_pubchem_plan",
    "make_unichem_identity_resolution",
    "union_cross_adapter_chemicals",
    "validate_cross_adapter_union",
    "validate_unichem_identity_resolution",
]
