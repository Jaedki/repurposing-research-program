#!/usr/bin/env python3
"""Schema-v7 multimodal discovery adapters and bounded query planners.

This module extends source discovery only.  It emits lightweight seeds or
explicit planned/unsupported coverage records.  It performs no identity
normalization, therapeutic screening, deep grounding, ranking, audit, output
construction, persistence, orchestration, or runtime scheduling.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol

from v7_case_model import (
    CaseRevision,
    CaseStatus,
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
    UncertaintyKind,
    UncertaintyLevel,
    known_node,
    not_applicable_node,
    unknown_node,
)
from v7_retrieval_adapter import (
    AdapterPageResponse,
    AdapterTransportError,
    ChemicalIdentityMatchLevel,
    DenominatorKind,
    NormalizedSourceRecord,
    PaginationKind,
    PublicationDensityMetadata,
    QueryPlan,
    RateLimitMetadata,
    RecordDisposition,
    RetrievalContractError,
    RetrievalRequest,
    RetryPolicy,
    SourceFindingPolarity,
    make_adapter_descriptor,
    make_chemical_identity_reference,
    make_normalized_seed_assertion,
    make_normalized_source_record,
    make_publication_density_metadata,
    make_query_plan,
    make_seed_route_template,
    make_source_activity_observation,
    make_source_evidence_annotation,
    make_source_mapping_context,
    make_source_universe,
    validate_publication_density_metadata,
)
from v7_seed_funnel import CandidateSeed, CompoundHintKind, SeedUncertainty


ADAPTER_VERSION = "schema-v7-extended-discovery-adapters-v1"
PLANNER_VERSION = "schema-v7-bounded-discovery-planner-v1"
ANTI_POPULARITY_POLICY_VERSION = "schema-v7-anti-popularity-discovery-v1"

CLINICAL_TRIALS_API = "https://clinicaltrials.gov/api/v2/studies"
CLINICAL_TRIALS_DOCS = "https://clinicaltrials.gov/data-about-studies/learn-about-api"
BIORXIV_API = "https://api.biorxiv.org/details"
BIORXIV_DOCS = "https://api.biorxiv.org/details/medrxiv/help"
OLS_SEARCH_API = "https://www.ebi.ac.uk/ols4/api/search"
CHEBI_DOCS = "https://www.ebi.ac.uk/chebi/beta/tools"
GWAS_CATALOG_DOCS = "https://www.ebi.ac.uk/gwas/rest/api/v2/docs"
STRING_DOCS = "https://string-db.org/help/api/"
NCBI_EUTILS_DOCS = "https://www.ncbi.nlm.nih.gov/books/NBK25497/"
CLUE_TERMS = "https://assets.clue.io/resources/CLUE_Software_License_T_and_C.pdf"


class ExtendedDiscoveryError(RetrievalContractError):
    """Raised when an extended source plan or response violates its contract."""


class PlannerDisposition(str, Enum):
    BOUNDED_QUERY_PLAN = "bounded_query_plan"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


class UnsupportedReason(str, Enum):
    LICENSE_REQUIRED = "license_required"
    CREDENTIAL_REQUIRED = "credential_required"
    REDISTRIBUTION_AMBIGUOUS = "redistribution_ambiguous"
    NO_STABLE_PUBLIC_API = "no_stable_public_api"
    LOCAL_DATA_REQUIRED = "local_data_required"


class DirectionalAlignment(str, Enum):
    ALIGNED = "aligned"
    OPPOSED = "opposed"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ClinicalTrialsBranch(str, Enum):
    INTERVENTION_ENUMERATION = "intervention_enumeration"
    FAILED_TERMINATED_OR_NEGATIVE = "failed_terminated_or_negative"
    ADJACENT_INDICATION = "adjacent_indication"
    OBSERVATIONAL_REAL_WORLD = "observational_real_world"


class PreprintServer(str, Enum):
    BIORXIV = "biorxiv"
    MEDRXIV = "medrxiv"


@dataclass(frozen=True)
class BoundedDiscoveryPlanner:
    planner_id: str
    planner_version: str
    disposition: PlannerDisposition
    case_revision_id: str
    endpoint_ids: tuple[str, ...]
    source_id: str
    source_release: str
    source_snapshot_at: str
    query_purpose: str
    evidence_modalities: tuple[EvidenceModality, ...]
    endpoint_url: str
    request_method: str
    exact_request_parameters_json: str
    local_filters_json: str
    maximum_requests: int
    continuation_grammar: str
    downstream_handoff: str
    allowed_coverage_statement: str
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class UnsupportedCapabilityRecord:
    capability_record_id: str
    planner_version: str
    disposition: PlannerDisposition
    case_revision_id: str
    endpoint_ids: tuple[str, ...]
    source_id: str
    source_release: str
    source_snapshot_at: str
    planned_capability: str
    reason: UnsupportedReason
    exact_planned_query_json: str
    access_requirement: str
    preserved_coverage_gap: str
    authoritative_reference: str
    alternatives: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryAdmissionMetadata:
    seed_id: str
    source_ids: tuple[str, ...]
    database_only: bool
    most_recent_record_date: str
    directional_alignment: DirectionalAlignment
    evidence_signals: tuple[SourceFindingPolarity, ...]
    publication_density: tuple[PublicationDensityMetadata, ...]
    chemical_universes: tuple[ChemicalUniverse, ...]
    causal_routes: tuple[CausalRoute, ...]


@dataclass(frozen=True)
class AntiPopularityDiscoveryFrame:
    frame_id: str
    policy_version: str
    seed_ids: tuple[str, ...]
    metadata: tuple[DiscoveryAdmissionMetadata, ...]
    reserved_database_only_seed_ids: tuple[str, ...]
    reserved_low_publication_seed_ids: tuple[str, ...]
    recent_or_uncited_seed_ids: tuple[str, ...]
    negative_or_null_seed_ids: tuple[str, ...]
    preclinical_seed_ids: tuple[str, ...]
    citation_used_for_admission: bool
    citation_chain_used_for_admission: bool
    admission_rule: str


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
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as stream:
                return HttpResponse(
                    status=int(stream.status),
                    headers={key.casefold(): value for key, value in stream.headers.items()},
                    body=stream.read(),
                )
        except urllib.error.HTTPError as exc:
            retry_after = _float_or_none(exc.headers.get("Retry-After"))
            raise AdapterTransportError(
                f"HTTP_{exc.code}",
                f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}",
                retryable=exc.code in {408, 425, 429, 500, 502, 503, 504},
                rate_limited=exc.code == 429,
                retry_after_seconds=retry_after,
                rate_limit=RateLimitMetadata(
                    limit=None,
                    remaining=0 if exc.code == 429 else None,
                    reset_at=None,
                    retry_after_seconds=retry_after,
                ) if exc.code == 429 else None,
            ) from exc
        except urllib.error.URLError as exc:
            raise AdapterTransportError(
                "NETWORK_ERROR",
                f"Network error from {urllib.parse.urlsplit(url).netloc}: {exc.reason}",
                retryable=True,
            ) from exc


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ExtendedDiscoveryError(f"{label}: expected text")
    result = " ".join(unicodedata.normalize("NFKC", value).split())
    if not result:
        raise ExtendedDiscoveryError(f"{label}: value cannot be blank")
    return result


def _optional_text(value: Any) -> str:
    return "" if value in {None, ""} else " ".join(str(value).split())


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, label) for value in values}))
    if required and not result:
        raise ExtendedDiscoveryError(f"{label}: at least one value is required")
    return result


def _json_text(value: Any, label: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ExtendedDiscoveryError(f"{label}: value is not canonical JSON") from exc


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ExtendedDiscoveryError(f"{label}: response is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExtendedDiscoveryError(f"{label}: response must be an object")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExtendedDiscoveryError(f"{label}: expected an object")
    return dict(value)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExtendedDiscoveryError(f"{label}: expected a list")
    return value


def _int_or_none(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stable_id(prefix: str, projection: Any) -> str:
    return f"{prefix}-{content_sha256({'rule': PLANNER_VERSION, 'projection': projection})[:24]}"


def _timestamp(value: str, label: str) -> str:
    result = _text(value, label)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExtendedDiscoveryError(f"{label}: invalid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ExtendedDiscoveryError(f"{label}: timezone is required")
    return result


def _date(value: str, label: str) -> str:
    result = _text(value, label)
    try:
        date.fromisoformat(result)
    except ValueError as exc:
        raise ExtendedDiscoveryError(f"{label}: invalid ISO date") from exc
    return result


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(max_attempts=3, backoff_seconds=(1.0, 2.0))


def _case_binding(
    case: CaseRevision, endpoint_ids: Iterable[str]
) -> tuple[str, tuple[str, ...]]:
    validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise ExtendedDiscoveryError("discovery planning requires a READY case")
    endpoints = _strings(endpoint_ids, "endpoint_ids", required=True)
    case_endpoints = {row.endpoint_id for row in case.endpoints}
    unknown = set(endpoints) - case_endpoints
    if unknown:
        raise ExtendedDiscoveryError(
            f"planner endpoints are outside the case portfolio: {sorted(unknown)}"
        )
    return case.case_revision_id, endpoints


def _make_bounded_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_id: str,
    source_release: str,
    source_snapshot_at: str,
    query_purpose: str,
    evidence_modalities: Iterable[EvidenceModality],
    endpoint_url: str,
    request_method: str,
    exact_request_parameters: Mapping[str, Any],
    local_filters: Mapping[str, Any],
    maximum_requests: int,
    continuation_grammar: str,
    downstream_handoff: str,
    allowed_coverage_statement: str,
    limitations: Iterable[str],
) -> BoundedDiscoveryPlanner:
    if isinstance(maximum_requests, bool) or not isinstance(maximum_requests, int) or maximum_requests < 1:
        raise ExtendedDiscoveryError("maximum_requests must be a positive integer")
    modalities = tuple(sorted(set(evidence_modalities), key=lambda item: item.value))
    if not modalities:
        raise ExtendedDiscoveryError("bounded planner requires an evidence modality")
    case_revision_id, covered_endpoints = _case_binding(case, endpoint_ids)
    body = {
        "planner_version": PLANNER_VERSION,
        "disposition": PlannerDisposition.BOUNDED_QUERY_PLAN,
        "case_revision_id": case_revision_id,
        "endpoint_ids": covered_endpoints,
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "source_snapshot_at": _timestamp(source_snapshot_at, "source_snapshot_at"),
        "query_purpose": _text(query_purpose, "query_purpose"),
        "evidence_modalities": modalities,
        "endpoint_url": _text(endpoint_url, "endpoint_url"),
        "request_method": _text(request_method, "request_method").upper(),
        "exact_request_parameters_json": _json_text(exact_request_parameters, "request parameters"),
        "local_filters_json": _json_text(local_filters, "local filters"),
        "maximum_requests": maximum_requests,
        "continuation_grammar": _text(continuation_grammar, "continuation_grammar"),
        "downstream_handoff": _text(downstream_handoff, "downstream_handoff"),
        "allowed_coverage_statement": _text(allowed_coverage_statement, "coverage statement"),
        "limitations": _strings(limitations, "limitations"),
    }
    result = BoundedDiscoveryPlanner(
        planner_id=_stable_id("BOUNDED-DISCOVERY-PLAN", body), **body
    )
    validate_bounded_discovery_planner(result, case=case)
    return result


def make_unsupported_capability_record(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_id: str,
    source_release: str,
    source_snapshot_at: str,
    planned_capability: str,
    reason: UnsupportedReason,
    exact_planned_query: Mapping[str, Any],
    access_requirement: str,
    preserved_coverage_gap: str,
    authoritative_reference: str,
    alternatives: Iterable[str] = (),
) -> UnsupportedCapabilityRecord:
    if not isinstance(reason, UnsupportedReason):
        raise ExtendedDiscoveryError("unsupported reason is invalid")
    case_revision_id, covered_endpoints = _case_binding(case, endpoint_ids)
    body = {
        "planner_version": PLANNER_VERSION,
        "disposition": PlannerDisposition.UNSUPPORTED_CAPABILITY,
        "case_revision_id": case_revision_id,
        "endpoint_ids": covered_endpoints,
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "source_snapshot_at": _timestamp(source_snapshot_at, "source_snapshot_at"),
        "planned_capability": _text(planned_capability, "planned_capability"),
        "reason": reason,
        "exact_planned_query_json": _json_text(exact_planned_query, "planned query"),
        "access_requirement": _text(access_requirement, "access_requirement"),
        "preserved_coverage_gap": _text(preserved_coverage_gap, "preserved_coverage_gap"),
        "authoritative_reference": _text(authoritative_reference, "authoritative_reference"),
        "alternatives": _strings(alternatives, "alternatives"),
    }
    result = UnsupportedCapabilityRecord(
        capability_record_id=_stable_id("UNSUPPORTED-CAPABILITY", body), **body
    )
    validate_unsupported_capability_record(result, case=case)
    return result


def _body_without(record: Any, id_field: str) -> dict[str, Any]:
    return {
        field.name: getattr(record, field.name)
        for field in fields(record)
        if field.name != id_field
    }


def validate_bounded_discovery_planner(
    value: BoundedDiscoveryPlanner, *, case: CaseRevision | None = None
) -> None:
    if not isinstance(value, BoundedDiscoveryPlanner):
        raise ExtendedDiscoveryError("expected BoundedDiscoveryPlanner")
    if (
        value.planner_version != PLANNER_VERSION
        or value.disposition is not PlannerDisposition.BOUNDED_QUERY_PLAN
    ):
        raise ExtendedDiscoveryError("bounded discovery planner version/disposition mismatch")
    _timestamp(value.source_snapshot_at, "source_snapshot_at")
    _text(value.case_revision_id, "case_revision_id")
    if not value.endpoint_ids:
        raise ExtendedDiscoveryError("bounded discovery planner requires endpoint IDs")
    if not value.evidence_modalities or any(
        not isinstance(item, EvidenceModality) for item in value.evidence_modalities
    ):
        raise ExtendedDiscoveryError("bounded discovery planner modalities are invalid")
    for payload in (value.exact_request_parameters_json, value.local_filters_json):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ExtendedDiscoveryError("bounded discovery planner JSON is invalid") from exc
        if not isinstance(parsed, dict):
            raise ExtendedDiscoveryError("bounded discovery planner JSON must be an object")
    if isinstance(value.maximum_requests, bool) or value.maximum_requests < 1:
        raise ExtendedDiscoveryError("bounded discovery planner maximum_requests is invalid")
    expected = _stable_id("BOUNDED-DISCOVERY-PLAN", _body_without(value, "planner_id"))
    if value.planner_id != expected:
        raise ExtendedDiscoveryError("bounded discovery planner ID mismatch")
    if case is not None:
        case_revision_id, endpoints = _case_binding(case, value.endpoint_ids)
        if value.case_revision_id != case_revision_id or value.endpoint_ids != endpoints:
            raise ExtendedDiscoveryError("bounded planner case/endpoint lineage mismatch")


def validate_unsupported_capability_record(
    value: UnsupportedCapabilityRecord, *, case: CaseRevision | None = None
) -> None:
    if not isinstance(value, UnsupportedCapabilityRecord):
        raise ExtendedDiscoveryError("expected UnsupportedCapabilityRecord")
    if (
        value.planner_version != PLANNER_VERSION
        or value.disposition is not PlannerDisposition.UNSUPPORTED_CAPABILITY
        or not isinstance(value.reason, UnsupportedReason)
    ):
        raise ExtendedDiscoveryError("unsupported capability version/disposition mismatch")
    _timestamp(value.source_snapshot_at, "source_snapshot_at")
    _text(value.case_revision_id, "case_revision_id")
    if not value.endpoint_ids:
        raise ExtendedDiscoveryError("unsupported capability requires endpoint IDs")
    try:
        parsed = json.loads(value.exact_planned_query_json)
    except json.JSONDecodeError as exc:
        raise ExtendedDiscoveryError("unsupported planned-query JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise ExtendedDiscoveryError("unsupported planned-query JSON must be an object")
    expected = _stable_id(
        "UNSUPPORTED-CAPABILITY", _body_without(value, "capability_record_id")
    )
    if value.capability_record_id != expected:
        raise ExtendedDiscoveryError("unsupported capability record ID mismatch")
    if case is not None:
        case_revision_id, endpoints = _case_binding(case, value.endpoint_ids)
        if value.case_revision_id != case_revision_id or value.endpoint_ids != endpoints:
            raise ExtendedDiscoveryError("unsupported record case/endpoint lineage mismatch")


def make_signature_reversal_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_release: str,
    source_snapshot_at: str,
    disease_signature_id: str,
    up_gene_ids: Iterable[str],
    down_gene_ids: Iterable[str],
    data_release_uri: str = "",
    redistribution_status: str = "unknown",
    maximum_perturbagens: int = 1000,
    cell_contexts: Iterable[str] = (),
    doses: Iterable[str] = (),
    timepoints: Iterable[str] = (),
) -> BoundedDiscoveryPlanner | UnsupportedCapabilityRecord:
    if (
        isinstance(maximum_perturbagens, bool)
        or not isinstance(maximum_perturbagens, int)
        or maximum_perturbagens < 1
    ):
        raise ExtendedDiscoveryError("maximum_perturbagens must be positive")
    up = _strings(up_gene_ids, "up gene IDs", required=True)
    down = _strings(down_gene_ids, "down gene IDs", required=True)
    query = {
        "disease_signature_id": _text(disease_signature_id, "disease_signature_id"),
        "up_gene_ids": up,
        "down_gene_ids": down,
        "reversal_metric": "signed_rank_correlation",
        "maximum_perturbagens": maximum_perturbagens,
        "cell_contexts": _strings(cell_contexts, "cell_contexts"),
        "doses": _strings(doses, "doses"),
        "timepoints": _strings(timepoints, "timepoints"),
    }
    if not data_release_uri or redistribution_status not in {"open", "redistributable"}:
        return make_unsupported_capability_record(
            case=case,
            endpoint_ids=endpoint_ids,
            source_id="clue-cmap-lincs",
            source_release=source_release,
            source_snapshot_at=source_snapshot_at,
            planned_capability="transcriptomic_signature_reversal",
            reason=(
                UnsupportedReason.LICENSE_REQUIRED
                if data_release_uri
                else UnsupportedReason.LOCAL_DATA_REQUIRED
            ),
            exact_planned_query=query,
            access_requirement="Provide a licensed or openly redistributable frozen signature matrix and its release URI.",
            preserved_coverage_gap="No transcriptomic-reversal candidates were enumerated from CLUE/CMap for this plan.",
            authoritative_reference=CLUE_TERMS,
            alternatives=("Use an explicitly open LINCS release with the same frozen query contract.",),
        )
    return _make_bounded_planner(
        case=case,
        endpoint_ids=endpoint_ids,
        source_id="lincs-signature-matrix",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        query_purpose="Enumerate perturbagens whose signatures reverse the declared disease signature.",
        evidence_modalities=(EvidenceModality.OMICS_SIGNATURE,),
        endpoint_url=data_release_uri,
        request_method="LOCAL_SNAPSHOT",
        exact_request_parameters=query,
        local_filters={"redistribution_status": redistribution_status},
        maximum_requests=1,
        continuation_grammar="not_applicable_frozen_matrix",
        downstream_handoff="Emit source-native perturbagen mappings as lightweight seeds; do not infer efficacy.",
        allowed_coverage_statement="complete only for the declared frozen signature matrix, metric, and bound",
        limitations=("Cell line, dose, time, and perturbagen coverage remain release-specific.",),
    )


def make_pubchem_phenotypic_screen_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_release: str,
    source_snapshot_at: str,
    phenotype_query: str,
    maximum_assays: int = 200,
) -> BoundedDiscoveryPlanner:
    if (
        isinstance(maximum_assays, bool)
        or not isinstance(maximum_assays, int)
        or maximum_assays < 1
    ):
        raise ExtendedDiscoveryError("maximum_assays must be positive")
    return _make_bounded_planner(
        case=case,
        endpoint_ids=endpoint_ids,
        source_id="pubchem-bioassay",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        query_purpose="Enumerate phenotype-matched PubChem BioAssay IDs before AID-to-compound retrieval.",
        evidence_modalities=(EvidenceModality.PHENOTYPIC_SCREENING,),
        endpoint_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        request_method="GET",
        exact_request_parameters={
            "db": "pcassay",
            "term": _text(phenotype_query, "phenotype_query"),
            "retmode": "json",
            "retmax": maximum_assays,
            "follow_up": "PubChem PUG REST assay/aid/{AID}/concise/JSON",
        },
        local_filters={"intervention_mapping": "retain active, inactive, inconclusive, and unspecified outcomes"},
        maximum_requests=maximum_assays + 1,
        continuation_grammar="ESearch retstart followed by one bounded PUG concise request per retained AID",
        downstream_handoff="Pass every AID to make_pubchem_plan(ASSAY_CONCISE); retain inactive rows.",
        allowed_coverage_statement="bounded plan complete for the exact ESearch query and AID bound",
        limitations=(
            "ESearch is text retrieval over deposited assay metadata, not semantic phenotype exhaustiveness.",
            f"Official E-utilities documentation: {NCBI_EUTILS_DOCS}",
        ),
    )


def make_recent_preprint_entity_discovery_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_release: str,
    source_snapshot_at: str,
    server: PreprintServer,
    from_date: str,
    to_date: str,
    case_terms: Iterable[str],
    chemical_dictionary_release_uri: str = "",
    chemical_dictionary_sha256: str = "",
    maximum_records: int = 600,
) -> BoundedDiscoveryPlanner | UnsupportedCapabilityRecord:
    """Plan recent-record chemical NER without predeclaring candidate names."""

    if not isinstance(server, PreprintServer):
        raise ExtendedDiscoveryError("preprint server is invalid")
    if (
        isinstance(maximum_records, bool)
        or not isinstance(maximum_records, int)
        or maximum_records < 1
    ):
        raise ExtendedDiscoveryError("maximum_records must be positive")
    from_value = _date(from_date, "from_date")
    to_value = _date(to_date, "to_date")
    if from_value > to_value:
        raise ExtendedDiscoveryError("preprint date interval is reversed")
    query = {
        "server": server.value,
        "from_date": from_value,
        "to_date": to_value,
        "case_terms": _strings(case_terms, "case_terms", required=True),
        "maximum_records": maximum_records,
        "entity_matching": "Unicode-normalized longest exact label/synonym match",
    }
    if not chemical_dictionary_release_uri:
        return make_unsupported_capability_record(
            case=case,
            endpoint_ids=endpoint_ids,
            source_id=f"{server.value}-chemical-entity-discovery",
            source_release=source_release,
            source_snapshot_at=source_snapshot_at,
            planned_capability="recent_sparse_literature_unknown_compound_discovery",
            reason=UnsupportedReason.LOCAL_DATA_REQUIRED,
            exact_planned_query=query,
            access_requirement="Provide an openly redistributable, checksum-bound ChEBI or equivalent chemical dictionary snapshot.",
            preserved_coverage_gap="Recent records were not scanned for previously unknown compound names.",
            authoritative_reference=CHEBI_DOCS,
            alternatives=("Use the exact predeclared-term preprint adapter as evidence expansion only.",),
        )
    digest = _text(chemical_dictionary_sha256, "chemical_dictionary_sha256").upper()
    if len(digest) != 64:
        raise ExtendedDiscoveryError("chemical_dictionary_sha256 must contain 64 hexadecimal characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ExtendedDiscoveryError("chemical_dictionary_sha256 is not hexadecimal") from exc
    query["chemical_dictionary_release_uri"] = chemical_dictionary_release_uri
    query["chemical_dictionary_sha256"] = digest
    return _make_bounded_planner(
        case=case,
        endpoint_ids=endpoint_ids,
        source_id=f"{server.value}-api-plus-chebi-dictionary",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        query_purpose="Scan bounded recent case-matched records for chemical entities not predeclared by name.",
        evidence_modalities=(EvidenceModality.MOLECULAR_FUNCTIONAL,),
        endpoint_url=f"{BIORXIV_API}/{server.value}/{from_value}/{to_value}/{{cursor}}",
        request_method="GET_PLUS_LOCAL_DICTIONARY",
        exact_request_parameters=query,
        local_filters={
            "dictionary_match": "longest exact label/synonym",
            "identity_status": "unverified name or database hint only",
        },
        maximum_requests=(maximum_records + 29) // 30,
        continuation_grammar="follow official 30-record cursor pages until exhaustion or the declared record bound",
        downstream_handoff="Emit each source-located chemical mention as a lightweight unverified seed; do not normalize identity or infer efficacy.",
        allowed_coverage_statement="bounded plan complete for the declared recent feed, record bound, and dictionary snapshot",
        limitations=(
            "Dictionary matching can miss novel codes, misspellings, and ambiguous abbreviations.",
            "This planner does not transfer evidence between matched names or normalize chemical identity.",
        ),
    )


def make_gwas_catalog_genetics_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_release: str,
    source_snapshot_at: str,
    efo_id: str,
    show_child_traits: bool = False,
    maximum_pages: int = 25,
) -> BoundedDiscoveryPlanner:
    if (
        isinstance(maximum_pages, bool)
        or not isinstance(maximum_pages, int)
        or maximum_pages < 1
    ):
        raise ExtendedDiscoveryError("maximum_pages must be positive")
    return _make_bounded_planner(
        case=case,
        endpoint_ids=endpoint_ids,
        source_id="gwas-catalog-v2",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        query_purpose="Enumerate curated trait associations for genetically proxied target hypotheses.",
        evidence_modalities=(EvidenceModality.GENETICS,),
        endpoint_url="https://www.ebi.ac.uk/gwas/rest/api/v2/associations",
        request_method="GET",
        exact_request_parameters={
            "efo_id": _text(efo_id, "efo_id"),
            "show_child_traits": show_child_traits,
            "extended_geneset": False,
            "page_size": 20,
        },
        local_filters={"gene_mapping": "retain Catalog mapped genes and mapping provenance; do not equate proximity with causality"},
        maximum_requests=maximum_pages,
        continuation_grammar="follow the response next link without synthesizing URLs",
        downstream_handoff="Map retained target hypotheses through a separately declared chemical adapter; keep genetics distinct from clinical evidence.",
        allowed_coverage_statement="bounded plan complete for the declared trait query and page bound",
        limitations=(
            "The REST API exposes literature-curated top associations; full summary statistics coverage is separate.",
            f"Official API documentation: {GWAS_CATALOG_DOCS}",
        ),
    )


def make_string_network_proximity_planner(
    *,
    case: CaseRevision,
    endpoint_ids: Iterable[str],
    source_release: str,
    source_snapshot_at: str,
    versioned_api_url: str,
    identifiers: Iterable[str],
    required_score: int = 700,
    additional_nodes: int = 10,
) -> BoundedDiscoveryPlanner:
    ids = _strings(identifiers, "STRING identifiers", required=True)
    if isinstance(required_score, bool) or not isinstance(required_score, int) or not 0 <= required_score <= 1000:
        raise ExtendedDiscoveryError("STRING required_score must be between 0 and 1000")
    if isinstance(additional_nodes, bool) or not isinstance(additional_nodes, int) or additional_nodes < 0:
        raise ExtendedDiscoveryError("STRING additional_nodes must be nonnegative")
    return _make_bounded_planner(
        case=case,
        endpoint_ids=endpoint_ids,
        source_id="string-db",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        query_purpose="Enumerate bounded human network-neighbour targets for proximity hypotheses.",
        evidence_modalities=(EvidenceModality.NETWORK_COMPUTATIONAL,),
        endpoint_url=f"{_text(versioned_api_url, 'versioned_api_url').rstrip('/')}/api/tsv/interaction_partners",
        request_method="POST",
        exact_request_parameters={
            "identifiers": ids,
            "species": 9606,
            "required_score": required_score,
            "limit": additional_nodes,
            "caller_identity": "repurposing-research-program-schema-v7",
        },
        local_filters={"network_distance": 1, "target_organism": "Homo sapiens"},
        maximum_requests=2,
        continuation_grammar="one identifier-mapping request followed by one bounded partner request",
        downstream_handoff="Pass source-grounded neighbour targets to target-first chemical enumeration; do not treat proximity as causal proof.",
        allowed_coverage_statement="bounded plan complete for the declared STRING release, score, and node limit",
        limitations=(
            "Single-protein queries may auto-expand unless the node limit is explicit.",
            f"Official API documentation: {STRING_DOCS}",
        ),
    )


def make_admission_metadata(
    seed: CandidateSeed,
    *,
    source_ids: Iterable[str],
    database_only: bool,
    most_recent_record_date: str,
    directional_alignment: DirectionalAlignment,
    evidence_signals: Iterable[SourceFindingPolarity] = (),
    publication_density: Iterable[PublicationDensityMetadata] = (),
) -> DiscoveryAdmissionMetadata:
    if not isinstance(seed, CandidateSeed):
        raise ExtendedDiscoveryError("admission metadata requires a CandidateSeed")
    if not isinstance(database_only, bool):
        raise ExtendedDiscoveryError("database_only must be boolean")
    if not isinstance(directional_alignment, DirectionalAlignment):
        raise ExtendedDiscoveryError("directional_alignment is invalid")
    density = tuple(sorted(set(publication_density), key=canonical_bytes))
    for row in density:
        validate_publication_density_metadata(row)
    signals = tuple(sorted(set(evidence_signals), key=lambda item: item.value))
    if any(not isinstance(item, SourceFindingPolarity) for item in signals):
        raise ExtendedDiscoveryError("evidence_signals contains an invalid value")
    routes = tuple(
        sorted(
            {row.causal_route for row in seed.structured_routes},
            key=lambda item: item.value,
        )
    )
    return DiscoveryAdmissionMetadata(
        seed_id=seed.seed_id,
        source_ids=_strings(source_ids, "source_ids", required=True),
        database_only=database_only,
        most_recent_record_date=_date(
            most_recent_record_date, "most_recent_record_date"
        ),
        directional_alignment=directional_alignment,
        evidence_signals=signals,
        publication_density=density,
        chemical_universes=seed.chemical_universes,
        causal_routes=routes,
    )


def build_anti_popularity_discovery_frame(
    seeds: Iterable[CandidateSeed],
    metadata: Iterable[DiscoveryAdmissionMetadata],
    *,
    recent_cutoff_date: str,
) -> AntiPopularityDiscoveryFrame:
    """Retain all mapped seeds and expose reserved discovery cohorts.

    Publication and citation counts are read only to label reserved coverage
    cohorts.  They cannot suppress or prioritize a seed, and this function
    deliberately emits no score or rank.
    """

    cutoff = date.fromisoformat(_date(recent_cutoff_date, "recent_cutoff_date"))
    seed_rows = tuple(sorted(seeds, key=lambda item: item.seed_id))
    if not seed_rows or len({row.seed_id for row in seed_rows}) != len(seed_rows):
        raise ExtendedDiscoveryError("anti-popularity frame requires unique seeds")
    metadata_rows = tuple(sorted(metadata, key=lambda item: item.seed_id))
    if len({row.seed_id for row in metadata_rows}) != len(metadata_rows):
        raise ExtendedDiscoveryError("anti-popularity metadata repeats a seed")
    seed_ids = tuple(row.seed_id for row in seed_rows)
    if {row.seed_id for row in metadata_rows} != set(seed_ids):
        raise ExtendedDiscoveryError("every seed requires exactly one admission-metadata record")

    database_only: list[str] = []
    low_publication: list[str] = []
    recent_or_uncited: list[str] = []
    negative_or_null: list[str] = []
    preclinical: list[str] = []
    for row in metadata_rows:
        if row.database_only:
            database_only.append(row.seed_id)
        counts = [
            value
            for density in row.publication_density
            for value in (density.publication_count, density.citation_count)
            if value is not None
        ]
        if not counts or min(counts) <= 1:
            low_publication.append(row.seed_id)
        is_recent = date.fromisoformat(row.most_recent_record_date) >= cutoff
        is_uncited = any(
            density.citation_count == 0 for density in row.publication_density
        )
        if is_recent or is_uncited:
            recent_or_uncited.append(row.seed_id)
        if any(
            signal
            in {
                SourceFindingPolarity.CONTRADICTORY,
                SourceFindingPolarity.NULL,
                SourceFindingPolarity.MIXED,
            }
            for signal in row.evidence_signals
        ):
            negative_or_null.append(row.seed_id)
        if ChemicalUniverse.PRECLINICAL_OR_TOOL_COMPOUNDS in row.chemical_universes:
            preclinical.append(row.seed_id)

    body = {
        "policy_version": ANTI_POPULARITY_POLICY_VERSION,
        "seed_ids": seed_ids,
        "metadata": metadata_rows,
        "reserved_database_only_seed_ids": tuple(sorted(database_only)),
        "reserved_low_publication_seed_ids": tuple(sorted(low_publication)),
        "recent_or_uncited_seed_ids": tuple(sorted(recent_or_uncited)),
        "negative_or_null_seed_ids": tuple(sorted(negative_or_null)),
        "preclinical_seed_ids": tuple(sorted(preclinical)),
        "citation_used_for_admission": False,
        "citation_chain_used_for_admission": False,
        "admission_rule": "Retain every eligible source mapping; publication density is descriptive metadata only.",
    }
    return AntiPopularityDiscoveryFrame(
        frame_id=f"ANTI-POPULARITY-FRAME-{content_sha256(body)[:24]}", **body
    )


def validate_anti_popularity_discovery_frame(
    seeds: Iterable[CandidateSeed],
    frame: AntiPopularityDiscoveryFrame,
    *,
    recent_cutoff_date: str,
) -> None:
    if not isinstance(frame, AntiPopularityDiscoveryFrame):
        raise ExtendedDiscoveryError("expected AntiPopularityDiscoveryFrame")
    rebuilt = build_anti_popularity_discovery_frame(
        seeds, frame.metadata, recent_cutoff_date=recent_cutoff_date
    )
    if canonical_bytes(rebuilt) != canonical_bytes(frame):
        raise ExtendedDiscoveryError("anti-popularity frame differs from its seed metadata")


def _transport_response(
    transport: HttpTransport, method: str, url: str
) -> HttpResponse:
    result = transport.request(
        method,
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "repurposing-research-program/schema-v7",
        },
        body=None,
    )
    if not isinstance(result, HttpResponse) or not 200 <= result.status < 300:
        status = result.status if isinstance(result, HttpResponse) else "UNKNOWN"
        raise AdapterTransportError(
            f"HTTP_{status}", f"Source returned HTTP {status}", retryable=False
        )
    return result


def _rate_limit(headers: Mapping[str, str]) -> RateLimitMetadata | None:
    limit = _int_or_none(headers.get("x-ratelimit-limit"))
    remaining = _int_or_none(headers.get("x-ratelimit-remaining"))
    retry_after = _float_or_none(headers.get("retry-after"))
    if limit is None and remaining is None and retry_after is None:
        return None
    return RateLimitMetadata(
        limit=limit,
        remaining=remaining,
        reset_at=None,
        retry_after_seconds=retry_after,
    )


def _seed_context(
    *,
    endpoint_ids: Iterable[str],
    disease_state_id: str,
    target_id: str,
    causal_route: CausalRoute,
) -> dict[str, Any]:
    endpoints = _strings(endpoint_ids, "endpoint_ids", required=True)
    if not isinstance(causal_route, CausalRoute):
        raise ExtendedDiscoveryError("causal_route is invalid")
    return {
        "endpoint_ids": endpoints,
        "disease_state_id": disease_state_id.strip(),
        "target_id": target_id.strip(),
        "causal_route": causal_route.value,
    }


def _route_templates(
    request: RetrievalRequest,
    *,
    evidence_id: str,
    target_id: str = "",
    action: InterventionAction = InterventionAction.MODULATE,
    direction: EffectDirection = EffectDirection.UNKNOWN,
) -> tuple[Any, ...]:
    context = _mapping(request.exact_request_parameters["seed_context"], "seed_context")
    disease_id = _optional_text(context.get("disease_state_id"))
    target = target_id or _optional_text(context.get("target_id"))
    route = CausalRoute(str(context["causal_route"]))
    return tuple(
        make_seed_route_template(
            causal_route=route,
            disease_state_node=(
                known_node(disease_id)
                if disease_id
                else unknown_node("No source disease-state identifier was available.")
            ),
            intervention_target=(
                known_node(target)
                if target
                else unknown_node("No source target identifier was available.")
            ),
            action=action,
            direction=direction,
            intermediate_state=not_applicable_node(
                "No separate intermediate state is asserted at source-enumeration depth."
            ),
            endpoint_id=endpoint_id,
            evidence_ids=(evidence_id,),
        )
        for endpoint_id in context["endpoint_ids"]
    )


def _uncertainty(note: str) -> tuple[SeedUncertainty, ...]:
    return (
        SeedUncertainty(
            kind=UncertaintyKind.IDENTITY,
            level=UncertaintyLevel.UNKNOWN,
            note="The source-reported intervention has not undergone identity normalization.",
        ),
        SeedUncertainty(
            kind=UncertaintyKind.SOURCE_COVERAGE,
            level=UncertaintyLevel.MEDIUM,
            note=note,
        ),
    )


FAILED_TRIAL_STATUSES = (
    "NO_LONGER_AVAILABLE",
    "SUSPENDED",
    "TERMINATED",
    "WITHDRAWN",
    "WITHHELD",
)


def make_clinical_trials_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    branch: ClinicalTrialsBranch,
    condition_query: str,
    endpoint_ids: Iterable[str],
    causal_route: CausalRoute,
    overall_statuses: Iterable[str] = (),
    adjacent_indication_id: str = "",
    target_id: str = "",
    page_size: int = 100,
    max_pages: int = 25,
    required: bool = True,
) -> QueryPlan:
    if not isinstance(branch, ClinicalTrialsBranch):
        raise ExtendedDiscoveryError("ClinicalTrials.gov branch is invalid")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 1000:
        raise ExtendedDiscoveryError("ClinicalTrials.gov page_size must be 1..1000")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ExtendedDiscoveryError("ClinicalTrials.gov max_pages must be positive")
    statuses = _strings(overall_statuses, "overall_statuses")
    if branch is ClinicalTrialsBranch.FAILED_TERMINATED_OR_NEGATIVE and not statuses:
        statuses = FAILED_TRIAL_STATUSES
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=adjacent_indication_id or condition_query,
        target_id=target_id,
        causal_route=causal_route,
    )
    universe = make_source_universe(
        source_id="clinicaltrials-gov",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"ClinicalTrials.gov {branch.value} studies matching the declared condition query",
        source_side_filters={
            "query.cond": _text(condition_query, "condition_query"),
            "filter.overallStatus": statuses,
        },
        local_filters={
            "branch": branch.value,
            "study_type": (
                "OBSERVATIONAL"
                if branch is ClinicalTrialsBranch.OBSERVATIONAL_REAL_WORLD
                else "INTERVENTIONAL_OR_EXPANDED_ACCESS"
            ),
            "intervention_types": ("DRUG", "DIETARY_SUPPLEMENT"),
        },
        denominator_kind=DenominatorKind.UNKNOWN,
        declared_total=None,
        pagination_kind=PaginationKind.CURSOR,
        continuation_parameter="pageToken",
        limitations=(
            "The API total is a study count while normalized native items are individual study interventions; no false intervention denominator is inferred.",
            "Terminated or withdrawn study status and why-stopped text are retained without inferring asset-wide failure or therapeutic efficacy.",
            "Posted results are retained as discovery annotations; semantic outcome interpretation remains deep-evidence work.",
            f"Official API documentation: {CLINICAL_TRIALS_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"clinicaltrials:{branch.value}:{content_sha256({'condition': condition_query, 'statuses': statuses})[:12]}",
        required=required,
        exact_request_parameters={
            "base_url": CLINICAL_TRIALS_API,
            "condition_query": condition_query,
            "overall_statuses": statuses,
            "branch": branch.value,
            "page_size": page_size,
            "adjacent_indication_id": adjacent_indication_id,
            "seed_context": context,
        },
        initial_continuation_token=None,
        max_pages=max_pages,
        max_records=None,
        allowed_terminal_codes=("clinicaltrials_page", "clinicaltrials_complete"),
        retry_policy=_retry_policy(),
    )


def _clinical_trial_intervention_items(studies: Iterable[Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for study_value in studies:
        study = _mapping(study_value, "ClinicalTrials.gov study")
        protocol = _mapping(study.get("protocolSection"), "ClinicalTrials.gov protocolSection")
        arms = protocol.get("armsInterventionsModule", {})
        arms_mapping = _mapping(arms, "ClinicalTrials.gov armsInterventionsModule")
        interventions = arms_mapping.get("interventions", [])
        intervention_rows = _list(interventions, "ClinicalTrials.gov interventions")
        if not intervention_rows:
            items.append({"study": study, "intervention": None, "intervention_index": None})
            continue
        for index, intervention_value in enumerate(intervention_rows):
            items.append(
                {
                    "study": study,
                    "intervention": _mapping(
                        intervention_value, "ClinicalTrials.gov intervention"
                    ),
                    "intervention_index": index,
                }
            )
    return items


def _clinical_development_status(phases: Iterable[Any]) -> DevelopmentStatus:
    values = {_optional_text(value).upper() for value in phases}
    if "PHASE4" in values:
        return DevelopmentStatus.APPROVED
    if "PHASE3" in values:
        return DevelopmentStatus.PHASE_3
    if "PHASE2" in values:
        return DevelopmentStatus.PHASE_2
    if "PHASE1" in values or "EARLY_PHASE1" in values:
        return DevelopmentStatus.PHASE_1
    return DevelopmentStatus.CLINICAL_STAGE


class ClinicalTrialsGovAdapter:
    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="clinicaltrials-gov-api-v2",
            adapter_version=ADAPTER_VERSION,
            source_id="clinicaltrials-gov",
            source_release=source_release,
            capabilities=("pagination:cursor", "study_intervention_enumeration"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = (
            query_plan.source_universe.source_id == self.descriptor.source_id
            and query_plan.query_family_id.startswith("clinicaltrials:")
            and query_plan.source_universe.pagination_kind is PaginationKind.CURSOR
        )
        return supported, "" if supported else "Plan is not a ClinicalTrials.gov v2 study traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        query: list[tuple[str, str]] = [
            ("format", "json"),
            ("pageSize", str(parameters["page_size"])),
            ("countTotal", "true"),
            ("query.cond", str(parameters["condition_query"])),
        ]
        statuses = tuple(parameters["overall_statuses"])
        if statuses:
            query.append(("filter.overallStatus", "|".join(statuses)))
        if request.input_continuation_token is not None:
            query.append(("pageToken", request.input_continuation_token))
        return f"{parameters['base_url']}?{urllib.parse.urlencode(query)}"

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        payload = _parse_json(result.body, "ClinicalTrials.gov")
        studies = _list(payload.get("studies", []), "ClinicalTrials.gov studies")
        item_count = len(_clinical_trial_intervention_items(studies))
        next_token = payload.get("nextPageToken")
        if next_token is not None:
            next_token = _text(next_token, "ClinicalTrials.gov nextPageToken")
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=item_count,
            provider_total=None,
            output_continuation_token=next_token,
            continuation_exhausted=next_token is None,
            terminal_code=(
                "clinicaltrials_complete" if next_token is None else "clinicaltrials_page"
            ),
            rate_limit=_rate_limit(result.headers),
        )

    def normalize(
        self, request: RetrievalRequest, response: AdapterPageResponse
    ) -> tuple[NormalizedSourceRecord, ...]:
        payload = _parse_json(response.raw_response, "ClinicalTrials.gov")
        studies = _list(payload.get("studies", []), "ClinicalTrials.gov studies")
        parameters = request.exact_request_parameters
        branch = ClinicalTrialsBranch(str(parameters["branch"]))
        context = _mapping(parameters["seed_context"], "seed_context")
        records: list[NormalizedSourceRecord] = []
        for item in _clinical_trial_intervention_items(studies):
            study = item["study"]
            protocol = _mapping(study["protocolSection"], "protocolSection")
            identification = _mapping(
                protocol.get("identificationModule"), "identificationModule"
            )
            nct_id = _text(identification.get("nctId"), "NCT ID")
            status_module = _mapping(protocol.get("statusModule", {}), "statusModule")
            design = _mapping(protocol.get("designModule", {}), "designModule")
            conditions_module = _mapping(
                protocol.get("conditionsModule", {}), "conditionsModule"
            )
            overall_status = _optional_text(status_module.get("overallStatus")).upper() or "UNKNOWN"
            study_type = _optional_text(design.get("studyType")).upper()
            phases = design.get("phases", [])
            if not isinstance(phases, list):
                raise ExtendedDiscoveryError("ClinicalTrials.gov phases must be a list")
            conditions = conditions_module.get("conditions", [])
            if not isinstance(conditions, list):
                raise ExtendedDiscoveryError("ClinicalTrials.gov conditions must be a list")
            intervention = item["intervention"]
            index = item["intervention_index"]
            native_id = f"{nct_id}:NO-INTERVENTION" if intervention is None else f"{nct_id}:INTERVENTION:{index}"
            locator = f"/studies/{nct_id}/protocolSection/armsInterventionsModule/interventions/{index}" if intervention is not None else f"/studies/{nct_id}/protocolSection"
            disposition = RecordDisposition.NO_INTERVENTION_MAPPING
            reason = "The study record contains no intervention mapping."
            assertions: tuple[Any, ...] = ()
            observational = branch is ClinicalTrialsBranch.OBSERVATIONAL_REAL_WORLD
            type_matches = study_type == ("OBSERVATIONAL" if observational else "INTERVENTIONAL") or (
                not observational and study_type == "EXPANDED_ACCESS"
            )
            if not type_matches:
                disposition = RecordDisposition.SOURCE_SCOPE_EXCLUDED
                reason = f"Study type {study_type or 'unknown'} is outside the declared branch."
            elif intervention is not None:
                intervention_type = _optional_text(intervention.get("type")).upper()
                if intervention_type not in {"DRUG", "DIETARY_SUPPLEMENT"}:
                    disposition = RecordDisposition.NON_INTERVENTION_TYPE_EXCLUDED
                    reason = f"Intervention type {intervention_type or 'unknown'} is outside the declared compound scope."
                else:
                    name = _text(intervention.get("name"), "ClinicalTrials.gov intervention name")
                    modality = (
                        EvidenceModality.OBSERVATIONAL_REAL_WORLD
                        if observational
                        else EvidenceModality.CLINICAL_INTERVENTION
                    )
                    status = _clinical_development_status(phases)
                    universes = {ChemicalUniverse.CLINICAL_STAGE_ASSETS}
                    why_stopped = _optional_text(status_module.get("whyStopped"))
                    annotations = [
                        make_source_evidence_annotation(
                            modality=modality,
                            annotation_type=(
                                "failed_or_terminated_trial_status"
                                if overall_status in FAILED_TRIAL_STATUSES
                                else "trial_status"
                            ),
                            source_item_id=nct_id,
                            source_locator=f"/studies/{nct_id}/protocolSection/statusModule",
                            finding_polarity=SourceFindingPolarity.NOT_EVALUATED,
                            status=overall_status,
                            endpoint_id=endpoint_id,
                            source_text=why_stopped,
                        )
                        for endpoint_id in context["endpoint_ids"]
                    ]
                    if study.get("hasResults") or study.get("resultsSection"):
                        annotations.extend(
                            make_source_evidence_annotation(
                                modality=modality,
                                annotation_type="posted_results_present",
                                source_item_id=nct_id,
                                source_locator=f"/studies/{nct_id}/resultsSection",
                                finding_polarity=SourceFindingPolarity.NOT_EVALUATED,
                                status=overall_status,
                                endpoint_id=endpoint_id,
                                source_text="Posted results retained; outcome polarity is not interpreted at discovery depth.",
                            )
                            for endpoint_id in context["endpoint_ids"]
                        )
                    results_section = study.get("resultsSection")
                    if isinstance(results_section, Mapping):
                        outcome_module = results_section.get("outcomeMeasuresModule", {})
                        if not isinstance(outcome_module, Mapping):
                            raise ExtendedDiscoveryError(
                                "ClinicalTrials.gov outcomeMeasuresModule must be an object"
                            )
                        outcome_rows = outcome_module.get("outcomeMeasures", [])
                        if not isinstance(outcome_rows, list):
                            raise ExtendedDiscoveryError(
                                "ClinicalTrials.gov outcomeMeasures must be a list"
                            )
                        for outcome_index, outcome_value in enumerate(outcome_rows):
                            outcome = _mapping(
                                outcome_value,
                                "ClinicalTrials.gov posted outcome",
                            )
                            outcome_text = " | ".join(
                                value
                                for value in (
                                    _optional_text(outcome.get("title")),
                                    _optional_text(outcome.get("description")),
                                )
                                if value
                            )
                            if not outcome_text:
                                continue
                            annotations.extend(
                                make_source_evidence_annotation(
                                    modality=modality,
                                    annotation_type="posted_outcome_result",
                                    source_item_id=nct_id,
                                    source_locator=(
                                        f"/studies/{nct_id}/resultsSection/"
                                        f"outcomeMeasuresModule/outcomeMeasures/{outcome_index}"
                                    ),
                                    finding_polarity=SourceFindingPolarity.NOT_EVALUATED,
                                    status=overall_status,
                                    endpoint_id=endpoint_id,
                                    source_text=outcome_text,
                                )
                                for endpoint_id in context["endpoint_ids"]
                            )
                    disease_id = _optional_text(parameters.get("adjacent_indication_id"))
                    if not disease_id and conditions:
                        disease_id = f"SOURCE-LABEL:{_text(conditions[0], 'trial condition')}"
                    mappings = ()
                    if disease_id:
                        mappings = (
                            make_source_mapping_context(
                                mapping_type=(
                                    "adjacent_indication_intervention"
                                    if branch is ClinicalTrialsBranch.ADJACENT_INDICATION
                                    else "clinical_study_intervention"
                                ),
                                disease_id=disease_id,
                                source_context=" | ".join(_optional_text(value) for value in conditions),
                            ),
                        )
                    assertion = make_normalized_seed_assertion(
                        assertion_locator=locator,
                        raw_intervention_assertion=name,
                        compound_hint_kind=CompoundHintKind.NAME_HINT,
                        compound_hint_value=name,
                        compound_hint_namespace="",
                        endpoint_ids=context["endpoint_ids"],
                        route_templates=_route_templates(
                            request,
                            evidence_id=f"CLINICALTRIALS:{nct_id}:{index}",
                        ),
                        evidence_modalities=(modality,),
                        chemical_universes=universes,
                        development_status=status,
                        uncertainty=_uncertainty(
                            "Coverage is bounded to the exact ClinicalTrials.gov query, snapshot, statuses, and page limit."
                        ),
                        activity_observations=(
                            make_source_activity_observation(
                                activity_type="clinical_study_status",
                                value=overall_status,
                                assay_id=nct_id,
                                assay_context=" | ".join(
                                    value
                                    for value in (
                                        branch.value,
                                        study_type,
                                        ",".join(_optional_text(item) for item in phases),
                                    )
                                    if value
                                ),
                            ),
                        ),
                        mapping_contexts=mappings,
                        evidence_annotations=annotations,
                    )
                    disposition = RecordDisposition.EMITTED_SEEDS
                    reason = "The source-native study intervention identifies one in-scope drug or dietary supplement."
                    assertions = (assertion,)
            source_projection = {
                "nct_id": nct_id,
                "overall_status": overall_status,
                "why_stopped": status_module.get("whyStopped"),
                "study_type": study_type,
                "phases": phases,
                "conditions": conditions,
                "intervention": intervention,
                "has_results": study.get("hasResults", False),
                "results_section": study.get("resultsSection"),
            }
            records.append(
                make_normalized_source_record(
                    source_id=self.descriptor.source_id,
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=locator,
                    source_record=source_projection,
                    disposition=disposition,
                    disposition_reason=reason,
                    screening_rule_id="clinicaltrials-study-intervention-mapping-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(records)


def make_preprint_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    server: PreprintServer,
    from_date: str,
    to_date: str,
    intervention_terms: Iterable[str],
    case_terms: Iterable[str],
    endpoint_ids: Iterable[str],
    causal_route: CausalRoute,
    evidence_modality: EvidenceModality,
    chemical_universes: Iterable[ChemicalUniverse],
    development_status: DevelopmentStatus = DevelopmentStatus.UNKNOWN,
    category: str = "",
    max_pages: int = 20,
    required: bool = False,
) -> QueryPlan:
    if not isinstance(server, PreprintServer):
        raise ExtendedDiscoveryError("preprint server is invalid")
    if not isinstance(evidence_modality, EvidenceModality):
        raise ExtendedDiscoveryError("preprint evidence_modality is invalid")
    if not isinstance(development_status, DevelopmentStatus):
        raise ExtendedDiscoveryError("preprint development_status is invalid")
    universes = tuple(sorted(set(chemical_universes), key=lambda item: item.value))
    if not universes:
        raise ExtendedDiscoveryError("preprint plan requires a chemical universe")
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id="",
        target_id="",
        causal_route=causal_route,
    )
    from_value = _date(from_date, "from_date")
    to_value = _date(to_date, "to_date")
    if from_value > to_value:
        raise ExtendedDiscoveryError("preprint date interval is reversed")
    interventions = _strings(
        intervention_terms, "intervention_terms", required=True
    )
    cases = _strings(case_terms, "case_terms")
    universe = make_source_universe(
        source_id=f"{server.value}-api",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"{server.value} records in the declared date/category interval",
        source_side_filters={
            "server": server.value,
            "source_snapshot_at": source_snapshot_at,
            "from_date": from_value,
            "to_date": to_value,
            "category": category,
        },
        local_filters={
            "case_terms": cases,
            "intervention_terms": interventions,
            "matching": "Unicode-normalized case-insensitive phrase match over title and abstract",
        },
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.CURSOR,
        continuation_parameter="cursor",
        limitations=(
            "The official API exposes date/category feeds, not compound entity annotations; exact predeclared term matches are retained as unverified name hints.",
            "Citation counts are not supplied by this API and remain unknown rather than becoming an admission filter.",
            "This branch deliberately searches recent records without following citation chains.",
            f"Official API documentation: {BIORXIV_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"preprint:{server.value}:{from_value}:{to_value}:{content_sha256(interventions)[:10]}",
        required=required,
        exact_request_parameters={
            "base_url": BIORXIV_API,
            "server": server.value,
            "source_snapshot_at": source_snapshot_at,
            "from_date": from_value,
            "to_date": to_value,
            "category": category,
            "case_terms": cases,
            "intervention_terms": interventions,
            "evidence_modality": evidence_modality.value,
            "chemical_universes": tuple(item.value for item in universes),
            "development_status": development_status.value,
            "seed_context": context,
        },
        initial_continuation_token="0",
        max_pages=max_pages,
        max_records=None,
        allowed_terminal_codes=("preprint_page", "preprint_complete"),
        retry_policy=_retry_policy(),
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    haystack = unicodedata.normalize("NFKC", text).casefold()
    needle = unicodedata.normalize("NFKC", phrase).casefold()
    pattern = rf"(?<!\w){re.escape(needle)}(?!\w)"
    return re.search(pattern, haystack) is not None


class PreprintAdapter:
    def __init__(
        self,
        source_release: str,
        *,
        server: PreprintServer,
        transport: HttpTransport | None = None,
    ) -> None:
        self.server = server
        self.descriptor = make_adapter_descriptor(
            adapter_id=f"{server.value}-details-api",
            adapter_version=ADAPTER_VERSION,
            source_id=f"{server.value}-api",
            source_release=source_release,
            capabilities=("pagination:cursor", "recent_sparse_literature"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = (
            query_plan.source_universe.source_id == self.descriptor.source_id
            and query_plan.query_family_id.startswith(f"preprint:{self.server.value}:")
        )
        return supported, "" if supported else "Plan is not a matching bioRxiv/medRxiv details traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        cursor = request.input_continuation_token or "0"
        path = "/".join(
            urllib.parse.quote(str(value), safe="-")
            for value in (
                parameters["server"],
                parameters["from_date"],
                parameters["to_date"],
                cursor,
            )
        )
        url = f"{parameters['base_url']}/{path}"
        if parameters.get("category"):
            url += "?" + urllib.parse.urlencode({"category": parameters["category"]})
        return url

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        payload = _parse_json(result.body, self.server.value)
        collection = _list(payload.get("collection", []), "preprint collection")
        messages = _list(payload.get("messages", []), "preprint messages")
        message = _mapping(messages[0], "preprint message") if messages else {}
        total = _int_or_none(message.get("total") or message.get("count"))
        cursor = int(request.input_continuation_token or "0")
        next_cursor = cursor + len(collection)
        exhausted = not collection or (total is not None and next_cursor >= total)
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(collection),
            provider_total=total,
            output_continuation_token=None if exhausted else str(next_cursor),
            continuation_exhausted=exhausted,
            terminal_code="preprint_complete" if exhausted else "preprint_page",
            rate_limit=_rate_limit(result.headers),
        )

    def normalize(
        self, request: RetrievalRequest, response: AdapterPageResponse
    ) -> tuple[NormalizedSourceRecord, ...]:
        payload = _parse_json(response.raw_response, self.server.value)
        collection = _list(payload.get("collection", []), "preprint collection")
        parameters = request.exact_request_parameters
        context = _mapping(parameters["seed_context"], "seed_context")
        case_terms = tuple(parameters["case_terms"])
        intervention_terms = tuple(parameters["intervention_terms"])
        modality = EvidenceModality(str(parameters["evidence_modality"]))
        universes = tuple(
            ChemicalUniverse(str(value)) for value in parameters["chemical_universes"]
        )
        status = DevelopmentStatus(str(parameters["development_status"]))
        records: list[NormalizedSourceRecord] = []
        for row_value in collection:
            row = _mapping(row_value, "preprint row")
            doi = _text(row.get("doi"), "preprint DOI")
            version = _optional_text(row.get("version")) or "1"
            native_id = f"{doi}:v{version}"
            title = _text(row.get("title"), "preprint title")
            abstract = _optional_text(row.get("abstract"))
            searchable = f"{title}\n{abstract}"
            disposition = RecordDisposition.NO_INTERVENTION_MAPPING
            reason = "No exact predeclared intervention term was found in the title or abstract."
            assertions: list[Any] = []
            if case_terms and not any(_contains_phrase(searchable, term) for term in case_terms):
                disposition = RecordDisposition.SOURCE_SCOPE_EXCLUDED
                reason = "The recent record did not match a predeclared case term."
            else:
                for term in intervention_terms:
                    if not _contains_phrase(searchable, term):
                        continue
                    assertion_locator = f"/collection/{native_id}/text-match/{urllib.parse.quote(term, safe='')}"
                    publication = (
                        make_publication_density_metadata(
                            source_id=self.descriptor.source_id,
                            as_of=str(request.exact_request_parameters["source_snapshot_at"]),
                            query_scope=f"Exact recent-record match for {term} in {doi}",
                            publication_count=1,
                            citation_count=None,
                            limitations=(
                                "The preprint API does not report citation counts; citation density remains unknown.",
                            ),
                        ),
                    )
                    assertions.append(
                        make_normalized_seed_assertion(
                            assertion_locator=assertion_locator,
                            raw_intervention_assertion=term,
                            compound_hint_kind=CompoundHintKind.NAME_HINT,
                            compound_hint_value=term,
                            compound_hint_namespace="",
                            endpoint_ids=context["endpoint_ids"],
                            route_templates=_route_templates(
                                request,
                                evidence_id=f"PREPRINT:{doi}:v{version}",
                            ),
                            evidence_modalities=(modality,),
                            chemical_universes=universes,
                            development_status=status,
                            uncertainty=_uncertainty(
                                "Coverage is bounded to the declared server, date/category interval, pages, case terms, and intervention lexicon."
                            ),
                            evidence_annotations=(
                                make_source_evidence_annotation(
                                    modality=modality,
                                    annotation_type="recent_preprint_mention",
                                    source_item_id=doi,
                                    source_locator=f"/collection/{native_id}",
                                    finding_polarity=SourceFindingPolarity.NOT_EVALUATED,
                                    status=_optional_text(row.get("published")) or "preprint",
                                    source_text=title,
                                ),
                            ),
                            publication_density=publication,
                        )
                    )
                if assertions:
                    disposition = RecordDisposition.EMITTED_SEEDS
                    reason = "The recent source record contains one or more exact predeclared intervention mentions."
            records.append(
                make_normalized_source_record(
                    source_id=self.descriptor.source_id,
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=f"https://doi.org/{doi}",
                    source_record=row,
                    disposition=disposition,
                    disposition_reason=reason,
                    screening_rule_id="recent-preprint-exact-intervention-mention-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(records)


def make_chebi_mapping_plan(
    *,
    source_release: str,
    source_snapshot_at: str,
    query_term: str,
    endpoint_ids: Iterable[str],
    causal_route: CausalRoute,
    chemical_universes: Iterable[ChemicalUniverse],
    context_id: str = "",
    intervention_action: InterventionAction = InterventionAction.UNKNOWN,
    effect_direction: EffectDirection = EffectDirection.UNKNOWN,
    rows: int = 100,
    max_pages: int = 10,
    required: bool = False,
) -> QueryPlan:
    universes = tuple(sorted(set(chemical_universes), key=lambda item: item.value))
    allowed = {
        ChemicalUniverse.NATURAL_PRODUCTS,
        ChemicalUniverse.ENDOGENOUS_COMPOUNDS_OR_NUTRIENTS,
        ChemicalUniverse.FORMULATION_COMPONENTS,
    }
    if not universes or any(item not in allowed for item in universes):
        raise ExtendedDiscoveryError(
            "ChEBI mapping plans require natural-product, endogenous/nutrient, or formulation-component memberships"
        )
    if not isinstance(intervention_action, InterventionAction) or not isinstance(
        effect_direction, EffectDirection
    ):
        raise ExtendedDiscoveryError("ChEBI action/direction controlled value is invalid")
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
        raise ExtendedDiscoveryError("ChEBI rows must be positive")
    context = _seed_context(
        endpoint_ids=endpoint_ids,
        disease_state_id=context_id,
        target_id=context_id,
        causal_route=causal_route,
    )
    universe = make_source_universe(
        source_id="chebi-ols",
        source_release=source_release,
        source_snapshot_at=source_snapshot_at,
        native_scope=f"ChEBI OLS search results for the exact query {query_term!r}",
        source_side_filters={"q": query_term, "ontology": "chebi", "type": "class"},
        local_filters={"chemical_universes": tuple(item.value for item in universes)},
        denominator_kind=DenominatorKind.PROVIDER_REPORTED,
        declared_total=None,
        pagination_kind=PaginationKind.PAGE,
        continuation_parameter="start",
        limitations=(
            "ChEBI search relevance does not itself prove natural origin, endogenous status, nutrient use, causal direction, or therapeutic applicability.",
            "Chemical-universe membership is supplied by the declared case/source-mapping branch and remains orthogonal to causal route.",
            f"Official API documentation: {CHEBI_DOCS}",
        ),
    )
    return make_query_plan(
        universe,
        query_family_id=f"chebi:ols:{content_sha256({'query': query_term, 'universes': universes})[:16]}",
        required=required,
        exact_request_parameters={
            "base_url": OLS_SEARCH_API,
            "q": _text(query_term, "query_term"),
            "ontology": "chebi",
            "type": "class",
            "rows": rows,
            "chemical_universes": tuple(item.value for item in universes),
            "intervention_action": intervention_action.value,
            "effect_direction": effect_direction.value,
            "seed_context": context,
        },
        initial_continuation_token="0",
        max_pages=max_pages,
        max_records=None,
        allowed_terminal_codes=("ols_page", "ols_complete"),
        retry_policy=_retry_policy(),
    )


class ChebiOlsAdapter:
    def __init__(self, source_release: str, *, transport: HttpTransport | None = None) -> None:
        self.descriptor = make_adapter_descriptor(
            adapter_id="ebi-ols4-chebi-search",
            adapter_version=ADAPTER_VERSION,
            source_id="chebi-ols",
            source_release=source_release,
            capabilities=("pagination:page", "chemical_ontology_mapping"),
        )
        self.transport = transport or UrllibHttpTransport()

    def supports(self, query_plan: QueryPlan) -> tuple[bool, str]:
        supported = (
            query_plan.source_universe.source_id == self.descriptor.source_id
            and query_plan.query_family_id.startswith("chebi:ols:")
        )
        return supported, "" if supported else "Plan is not a ChEBI OLS search traversal."

    def _url(self, request: RetrievalRequest) -> str:
        parameters = request.exact_request_parameters
        query = {
            "q": parameters["q"],
            "ontology": parameters["ontology"],
            "type": parameters["type"],
            "rows": parameters["rows"],
            "start": request.input_continuation_token,
        }
        return f"{parameters['base_url']}?{urllib.parse.urlencode(query)}"

    def retrieve(self, request: RetrievalRequest) -> AdapterPageResponse:
        result = _transport_response(self.transport, "GET", self._url(request))
        payload = _parse_json(result.body, "ChEBI OLS")
        response = _mapping(payload.get("response"), "OLS response")
        docs = _list(response.get("docs", []), "OLS docs")
        total = _int_or_none(response.get("numFound"))
        if total is None:
            raise ExtendedDiscoveryError("OLS response omitted numFound")
        start = _int_or_none(response.get("start"))
        if start is None:
            start = int(request.input_continuation_token or "0")
        next_start = start + len(docs)
        exhausted = not docs or next_start >= total
        return AdapterPageResponse(
            request_sha256=request.request_sha256,
            raw_response=result.body,
            returned_count=len(docs),
            provider_total=total,
            output_continuation_token=None if exhausted else str(next_start),
            continuation_exhausted=exhausted,
            terminal_code="ols_complete" if exhausted else "ols_page",
            rate_limit=_rate_limit(result.headers),
        )

    def normalize(
        self, request: RetrievalRequest, response: AdapterPageResponse
    ) -> tuple[NormalizedSourceRecord, ...]:
        payload = _parse_json(response.raw_response, "ChEBI OLS")
        docs = _list(_mapping(payload["response"], "OLS response").get("docs", []), "OLS docs")
        parameters = request.exact_request_parameters
        context = _mapping(parameters["seed_context"], "seed_context")
        universes = tuple(
            ChemicalUniverse(str(value)) for value in parameters["chemical_universes"]
        )
        records: list[NormalizedSourceRecord] = []
        for doc_value in docs:
            doc = _mapping(doc_value, "OLS document")
            obo_id = _optional_text(doc.get("obo_id"))
            label = _optional_text(doc.get("label"))
            iri = _optional_text(doc.get("iri"))
            native_id = obo_id or f"OLS-DOC-{content_sha256(doc)[:24]}"
            disposition = RecordDisposition.EMITTED_SEEDS
            reason = "The ChEBI ontology search result identifies one chemical entity."
            assertions: tuple[Any, ...] = ()
            if not obo_id.startswith("CHEBI:") or not label:
                disposition = RecordDisposition.FAILED_MAPPING
                reason = "The OLS result omitted a canonical ChEBI identifier or label."
            else:
                mappings = ()
                context_id = _optional_text(context.get("target_id"))
                if context_id:
                    mappings = (
                        make_source_mapping_context(
                            mapping_type="substrate_metabolite_or_nutrient_mapping",
                            pathway_id=context_id,
                            source_context=str(parameters["q"]),
                        ),
                    )
                assertions = (
                    make_normalized_seed_assertion(
                        assertion_locator=f"/response/docs/{urllib.parse.quote(obo_id, safe='')}",
                        raw_intervention_assertion=label,
                        compound_hint_kind=CompoundHintKind.DATABASE_IDENTIFIER,
                        compound_hint_value=obo_id,
                        compound_hint_namespace="CHEBI",
                        endpoint_ids=context["endpoint_ids"],
                        route_templates=_route_templates(
                            request,
                            evidence_id=f"CHEBI:{obo_id}",
                            target_id=context_id,
                            action=InterventionAction(
                                str(parameters["intervention_action"])
                            ),
                            direction=EffectDirection(
                                str(parameters["effect_direction"])
                            ),
                        ),
                        evidence_modalities=(EvidenceModality.MOLECULAR_FUNCTIONAL,),
                        chemical_universes=universes,
                        development_status=DevelopmentStatus.UNKNOWN,
                        uncertainty=_uncertainty(
                            "Coverage is bounded to the exact ChEBI OLS search, release, pages, and supplied universe mapping."
                        ),
                        activity_observations=(
                            make_source_activity_observation(
                                activity_type="chemical_ontology_mapping",
                                value=str(parameters["q"]),
                                assay_context="ChEBI OLS search result",
                            ),
                        ),
                        identity_references=(
                            make_chemical_identity_reference(
                                namespace="CHEBI",
                                identifier=obo_id,
                                match_level=ChemicalIdentityMatchLevel.EXACT_DATABASE_IDENTIFIER,
                                authority="ChEBI",
                                authority_release=self.descriptor.source_release,
                            ),
                        ),
                        mapping_contexts=mappings,
                    ),
                )
            records.append(
                make_normalized_source_record(
                    source_id=self.descriptor.source_id,
                    source_release=self.descriptor.source_release,
                    native_record_id=native_id,
                    native_record_locator=iri or f"{OLS_SEARCH_API}?q={urllib.parse.quote(label)}",
                    source_record=doc,
                    disposition=disposition,
                    disposition_reason=reason,
                    screening_rule_id="chebi-ols-chemical-entity-mapping-v1",
                    seed_assertions=assertions,
                )
            )
        return tuple(records)


__all__ = [
    "ADAPTER_VERSION",
    "ANTI_POPULARITY_POLICY_VERSION",
    "AntiPopularityDiscoveryFrame",
    "BIORXIV_DOCS",
    "BoundedDiscoveryPlanner",
    "CHEBI_DOCS",
    "CLINICAL_TRIALS_DOCS",
    "CLUE_TERMS",
    "ChebiOlsAdapter",
    "ClinicalTrialsBranch",
    "ClinicalTrialsGovAdapter",
    "DirectionalAlignment",
    "DiscoveryAdmissionMetadata",
    "ExtendedDiscoveryError",
    "GWAS_CATALOG_DOCS",
    "HttpResponse",
    "HttpTransport",
    "NCBI_EUTILS_DOCS",
    "PLANNER_VERSION",
    "PlannerDisposition",
    "PreprintAdapter",
    "PreprintServer",
    "STRING_DOCS",
    "UnsupportedCapabilityRecord",
    "UnsupportedReason",
    "UrllibHttpTransport",
    "build_anti_popularity_discovery_frame",
    "make_admission_metadata",
    "make_chebi_mapping_plan",
    "make_clinical_trials_plan",
    "make_gwas_catalog_genetics_planner",
    "make_preprint_plan",
    "make_pubchem_phenotypic_screen_planner",
    "make_recent_preprint_entity_discovery_planner",
    "make_signature_reversal_planner",
    "make_string_network_proximity_planner",
    "make_unsupported_capability_record",
    "validate_anti_popularity_discovery_frame",
    "validate_bounded_discovery_planner",
    "validate_unsupported_capability_record",
]
