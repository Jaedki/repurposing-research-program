#!/usr/bin/env python3
"""Schema-v7 factorized discovery and broad case-model contracts.

This module is deliberately adapter-free and runtime-free.  It turns already
retrieved, source-grounded records into immutable typed snapshots, enumerates
the discovery matrix, normalizes structural causal routes, and provides
deterministic scoring, ranking, sampling, caching, and output construction.
It never writes canonical run state.
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from v7_case_model import (
    CaseRevision,
    CaseStatus,
    ValueStatus,
    canonical_bytes,
    content_sha256,
    validate_case_revision,
)


SCHEMA_VERSION = 7
DISCOVERY_MODEL_VERSION = "schema-v7-discovery-v1"
ROUTE_ID_RULE = "schema-v7-structural-causal-route-v1"
CASE_MODEL_RECORD_ID_RULE = "schema-v7-broad-case-model-record-v1"
CASE_MODEL_SNAPSHOT_ID_RULE = "schema-v7-broad-case-model-snapshot-v1"
DISCOVERY_JOB_ID_RULE = "schema-v7-discovery-job-v1"
DISCOVERY_HYPOTHESIS_ID_RULE = "schema-v7-discovery-hypothesis-v1"
DISCOVERY_SNAPSHOT_ID_RULE = "schema-v7-discovery-snapshot-v1"


class DiscoveryContractError(ValueError):
    """Raised when a schema-v7 discovery record violates the contract."""


class CausalRoute(str, Enum):
    DIRECT_DISEASE_DRIVER_MODULATION = "direct_disease_driver_modulation"
    DOWNSTREAM_OR_BYPASS_RESTORATION = "downstream_or_bypass_restoration"
    PHENOTYPE_OR_STATE_REVERSAL = "phenotype_or_state_reversal"
    DISEASE_CREATED_VULNERABILITY = "disease_created_vulnerability"
    SUBSTRATE_COFACTOR_OR_TRANSPORTER_CORRECTION = (
        "substrate_cofactor_or_transporter_correction"
    )
    HOST_ENVIRONMENT_OR_INFLAMMATORY_MODULATION = (
        "host_environment_or_inflammatory_modulation"
    )
    SYMPTOMATIC_OR_COMPLICATION_MANAGEMENT = (
        "symptomatic_or_complication_management"
    )


class EvidenceModality(str, Enum):
    GENETICS = "genetics"
    MOLECULAR_FUNCTIONAL = "molecular_functional"
    OMICS_SIGNATURE = "omics_signature"
    PHENOTYPIC_SCREENING = "phenotypic_screening"
    BIOACTIVITY = "bioactivity"
    NETWORK_COMPUTATIONAL = "network_computational"
    CLINICAL_INTERVENTION = "clinical_intervention"
    OBSERVATIONAL_REAL_WORLD = "observational_real_world"
    SAFETY_ADVERSE_EVENT = "safety_adverse_event"
    AUTHORITATIVE_PHARMACOLOGY = "authoritative_pharmacology"


class ChemicalUniverse(str, Enum):
    APPROVED_HUMAN_USE_COMPOUNDS = "approved_human_use_compounds"
    CLINICAL_STAGE_ASSETS = "clinical_stage_assets"
    SHELVED_OR_FAILED_ASSETS = "shelved_or_failed_assets"
    PRECLINICAL_OR_TOOL_COMPOUNDS = "preclinical_or_tool_compounds"
    NATURAL_PRODUCTS = "natural_products"
    ENDOGENOUS_COMPOUNDS_OR_NUTRIENTS = "endogenous_compounds_or_nutrients"
    FORMULATION_COMPONENTS = "formulation_components"


class DevelopmentStatus(str, Enum):
    PRECLINICAL = "preclinical"
    INVESTIGATIONAL = "investigational"
    CLINICAL_STAGE = "clinical_stage"
    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"
    APPROVED = "approved"
    SHELVED = "shelved"
    FAILED = "failed"
    DISCONTINUED = "discontinued"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class UncertaintyKind(str, Enum):
    IDENTITY = "identity"
    SOURCE_COVERAGE = "source_coverage"
    CAUSAL = "causal"
    EFFECT = "effect"
    TRANSLATION_APPLICABILITY = "translation_applicability"
    SAFETY_EXPOSURE = "safety_exposure"
    MISSINGNESS = "missingness"


class UncertaintyLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class NodeStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class InterventionAction(str, Enum):
    INHIBIT = "inhibit"
    ACTIVATE = "activate"
    ANTAGONIZE = "antagonize"
    AGONIZE = "agonize"
    REPLACE = "replace"
    SUPPLEMENT = "supplement"
    RESTORE = "restore"
    DEGRADE = "degrade"
    STABILIZE = "stabilize"
    MODULATE = "modulate"
    CORRECT = "correct"
    AVOID = "avoid"
    UNKNOWN = "unknown"


class EffectDirection(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    RESTORE = "restore"
    NORMALIZE = "normalize"
    STABILIZE = "stabilize"
    PREVENT = "prevent"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class CaseModelOutputKind(str, Enum):
    DISEASE_MECHANISM = "disease_mechanism"
    DIRECTIONAL_TARGET = "directional_target"
    PHENOTYPE_OR_SIGNATURE = "phenotype_or_signature"
    TISSUE_OR_CELL_TYPE = "tissue_or_cell_type"
    SUBSTRATE_OR_METABOLITE = "substrate_or_metabolite"
    COMPENSATORY_NODE = "compensatory_node"
    CONTRAINDICATED_MECHANISM = "contraindicated_mechanism"
    ENDPOINT_MAPPING = "endpoint_mapping"


class BroadDomain(str, Enum):
    DISEASE_MECHANISM_MODEL = "disease_mechanism_model"
    DIRECTIONAL_TARGET_MODEL = "directional_target_model"
    PHENOTYPE_SIGNATURE_MODEL = "phenotype_signature_model"
    TISSUE_CELL_CONTEXT = "tissue_cell_context"
    SUBSTRATE_METABOLITE_CONTEXT = "substrate_metabolite_context"
    COMPENSATORY_NODE_MODEL = "compensatory_node_model"
    CONTRAINDICATED_MECHANISM_MODEL = "contraindicated_mechanism_model"
    ENDPOINT_MAPPING_MODEL = "endpoint_mapping_model"
    PHARMACOLOGY_SEED_MAPPING = "pharmacology_seed_mapping"


class JudgmentStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    RECORDED = "recorded"


@dataclass(frozen=True)
class ScientificNode:
    status: NodeStatus
    node_id: str | None
    label: str | None
    reason: str


@dataclass(frozen=True)
class Uncertainty:
    kind: UncertaintyKind
    level: UncertaintyLevel
    note: str


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    modality: EvidenceModality
    source_id: str
    source_release: str
    native_record_id: str
    locator: str
    retrieval_content_receipt_id: str
    claim_id: str


@dataclass(frozen=True)
class StructuredCausalRoute:
    route_id: str
    case_revision_id: str
    intervention_id: str
    causal_route: CausalRoute
    disease_state_node: ScientificNode
    intervention_target: ScientificNode
    action: InterventionAction
    direction: EffectDirection
    intermediate_state: ScientificNode
    endpoint_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CaseModelRecord:
    record_id: str
    output_kind: CaseModelOutputKind
    primary_node: ScientificNode
    related_nodes: tuple[ScientificNode, ...]
    action: InterventionAction
    direction: EffectDirection
    endpoint_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_mapping_ids: tuple[str, ...]
    uncertainty: tuple[Uncertainty, ...]


@dataclass(frozen=True)
class DirectionConflict:
    conflict_id: str
    subject_node: ScientificNode
    asserted_directions: tuple[EffectDirection, ...]
    evidence_ids: tuple[str, ...]
    blocking: bool


@dataclass(frozen=True)
class PharmacologySeedEmission:
    emission_id: str
    source_id: str
    source_release: str
    native_record_id: str
    assertion_locator: str
    raw_intervention_assertion: str
    query_id: str
    query_record_locator: str
    retrieval_content_receipt_id: str
    compound_hint_kind: str
    compound_hint_value: str
    compound_hint_namespace: str
    endpoint_ids: tuple[str, ...]
    structured_routes: tuple[StructuredCausalRoute, ...]
    evidence_modalities: tuple[EvidenceModality, ...]
    chemical_universes: tuple[ChemicalUniverse, ...]
    development_status: DevelopmentStatus
    uncertainty: tuple[Uncertainty, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExpertJudgment:
    judgment_id: str
    field_path: str
    interpretation_question: str
    selected_value: str
    evidence_ids: tuple[str, ...]
    model_id: str
    prompt_template_version: str
    rationale: str
    status: JudgmentStatus


@dataclass(frozen=True)
class BroadDomainContract:
    domain: BroadDomain
    owned_output_fields: tuple[str, ...]
    prohibited_output_fields: tuple[str, ...]


@dataclass(frozen=True)
class BroadCaseModelSnapshot:
    schema_version: int
    model_version: str
    snapshot_id: str
    case_id: str
    case_revision_id: str
    disease_mechanisms: tuple[CaseModelRecord, ...]
    directional_targets: tuple[CaseModelRecord, ...]
    phenotypes_and_signatures: tuple[CaseModelRecord, ...]
    tissues_and_cell_types: tuple[CaseModelRecord, ...]
    substrates_and_metabolites: tuple[CaseModelRecord, ...]
    compensatory_nodes: tuple[CaseModelRecord, ...]
    contraindicated_mechanisms: tuple[CaseModelRecord, ...]
    endpoint_mappings: tuple[CaseModelRecord, ...]
    unresolved_direction_conflicts: tuple[DirectionConflict, ...]
    pharmacology_seed_emissions: tuple[PharmacologySeedEmission, ...]
    evidence_records: tuple[EvidenceRecord, ...]
    expert_judgments: tuple[ExpertJudgment, ...]


@dataclass(frozen=True)
class DiscoveryJob:
    job_id: str
    case_revision_id: str
    causal_route: CausalRoute
    evidence_modality: EvidenceModality
    chemical_universe: ChemicalUniverse
    development_statuses: tuple[DevelopmentStatus, ...]
    endpoint_id: str
    uncertainty_kinds: tuple[UncertaintyKind, ...]
    case_model_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryHypothesis:
    hypothesis_id: str
    case_revision_id: str
    intervention_id: str
    structured_route: StructuredCausalRoute
    evidence_modality: EvidenceModality
    chemical_universe: ChemicalUniverse
    development_status: DevelopmentStatus
    endpoint_id: str
    uncertainty: tuple[Uncertainty, ...]
    source_mapping_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryScore:
    hypothesis_id: str
    structural_completeness: int
    grounded_evidence_count: int
    independent_modality_count: int
    total: int


@dataclass(frozen=True)
class RankedDiscoveryHypothesis:
    rank: int
    hypothesis: DiscoveryHypothesis
    score: DiscoveryScore


@dataclass(frozen=True)
class DiscoverySnapshot:
    schema_version: int
    model_version: str
    snapshot_id: str
    case_revision_id: str
    hypotheses: tuple[DiscoveryHypothesis, ...]
    ranked_hypotheses: tuple[RankedDiscoveryHypothesis, ...]


_CASE_MODEL_FIELDS = (
    "disease_mechanisms",
    "directional_targets",
    "phenotypes_and_signatures",
    "tissues_and_cell_types",
    "substrates_and_metabolites",
    "compensatory_nodes",
    "contraindicated_mechanisms",
    "endpoint_mappings",
    "unresolved_direction_conflicts",
    "pharmacology_seed_emissions",
)


BROAD_DOMAIN_CONTRACTS = (
    BroadDomainContract(BroadDomain.DISEASE_MECHANISM_MODEL, ("disease_mechanisms",), ()),
    BroadDomainContract(
        BroadDomain.DIRECTIONAL_TARGET_MODEL,
        ("directional_targets", "unresolved_direction_conflicts"),
        (),
    ),
    BroadDomainContract(
        BroadDomain.PHENOTYPE_SIGNATURE_MODEL, ("phenotypes_and_signatures",), ()
    ),
    BroadDomainContract(BroadDomain.TISSUE_CELL_CONTEXT, ("tissues_and_cell_types",), ()),
    BroadDomainContract(
        BroadDomain.SUBSTRATE_METABOLITE_CONTEXT, ("substrates_and_metabolites",), ()
    ),
    BroadDomainContract(BroadDomain.COMPENSATORY_NODE_MODEL, ("compensatory_nodes",), ()),
    BroadDomainContract(
        BroadDomain.CONTRAINDICATED_MECHANISM_MODEL, ("contraindicated_mechanisms",), ()
    ),
    BroadDomainContract(BroadDomain.ENDPOINT_MAPPING_MODEL, ("endpoint_mappings",), ()),
    BroadDomainContract(
        BroadDomain.PHARMACOLOGY_SEED_MAPPING, ("pharmacology_seed_emissions",), ()
    ),
)


_FIELD_KIND = {
    "disease_mechanisms": CaseModelOutputKind.DISEASE_MECHANISM,
    "directional_targets": CaseModelOutputKind.DIRECTIONAL_TARGET,
    "phenotypes_and_signatures": CaseModelOutputKind.PHENOTYPE_OR_SIGNATURE,
    "tissues_and_cell_types": CaseModelOutputKind.TISSUE_OR_CELL_TYPE,
    "substrates_and_metabolites": CaseModelOutputKind.SUBSTRATE_OR_METABOLITE,
    "compensatory_nodes": CaseModelOutputKind.COMPENSATORY_NODE,
    "contraindicated_mechanisms": CaseModelOutputKind.CONTRAINDICATED_MECHANISM,
    "endpoint_mappings": CaseModelOutputKind.ENDPOINT_MAPPING,
}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise DiscoveryContractError(f"{label}: expected text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise DiscoveryContractError(f"{label}: value cannot be blank")
    return normalized


def _raw_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not " ".join(unicodedata.normalize("NFKC", value).split()):
        raise DiscoveryContractError(f"{label}: nonblank source text is required")
    return value


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, label) for value in values}))
    if required and not result:
        raise DiscoveryContractError(f"{label}: at least one value is required")
    return result


def _enums(values: Iterable[Enum], enum_type: type[Enum], label: str) -> tuple[Any, ...]:
    result = tuple(sorted(set(values), key=lambda value: value.value))
    if any(not isinstance(value, enum_type) for value in result):
        raise DiscoveryContractError(f"{label}: invalid controlled value")
    return result


def _stable_id(prefix: str, rule: str, projection: Any) -> str:
    return f"{prefix}-{content_sha256({'rule_id': rule, 'projection': projection})[:24]}"


def known_node(node_id: str, label: str = "") -> ScientificNode:
    return ScientificNode(
        status=NodeStatus.KNOWN,
        node_id=_text(node_id, "node_id"),
        label=_text(label, "node label") if label else None,
        reason="",
    )


def unknown_node(reason: str) -> ScientificNode:
    return ScientificNode(NodeStatus.UNKNOWN, None, None, _text(reason, "node reason"))


def not_applicable_node(reason: str) -> ScientificNode:
    return ScientificNode(NodeStatus.NOT_APPLICABLE, None, None, _text(reason, "node reason"))


def validate_node(node: ScientificNode, label: str) -> None:
    if not isinstance(node, ScientificNode) or not isinstance(node.status, NodeStatus):
        raise DiscoveryContractError(f"{label}: invalid node")
    if node.status is NodeStatus.KNOWN:
        if node.node_id is None or node.reason or _text(node.node_id, label) != node.node_id:
            raise DiscoveryContractError(f"{label}: known node requires a canonical ID and no reason")
        if node.label is not None and _text(node.label, label) != node.label:
            raise DiscoveryContractError(f"{label}: node label is not canonical")
    elif node.node_id is not None or node.label is not None or not node.reason:
        raise DiscoveryContractError(f"{label}: unknown/N/A node requires only a reason")


def _route_signature(route: StructuredCausalRoute) -> dict[str, Any]:
    def node_identity(node: ScientificNode) -> dict[str, Any]:
        # Display labels and explanatory reasons are presentation/provenance, not
        # causal topology.  Excluding them prevents paraphrases from creating
        # artificial route convergence.
        return {"status": node.status, "node_id": node.node_id}

    return {
        "case_revision_id": route.case_revision_id,
        "intervention_id": route.intervention_id,
        "causal_route": route.causal_route,
        "disease_state_node": node_identity(route.disease_state_node),
        "intervention_target": node_identity(route.intervention_target),
        "action": route.action,
        "direction": route.direction,
        "intermediate_state": node_identity(route.intermediate_state),
        "endpoint_id": route.endpoint_id,
    }


def make_structured_route(
    *,
    case_revision_id: str,
    intervention_id: str,
    causal_route: CausalRoute,
    disease_state_node: ScientificNode,
    intervention_target: ScientificNode,
    action: InterventionAction,
    direction: EffectDirection,
    intermediate_state: ScientificNode,
    endpoint_id: str,
    evidence_ids: Iterable[str],
) -> StructuredCausalRoute:
    draft = StructuredCausalRoute(
        route_id="",
        case_revision_id=_text(case_revision_id, "case_revision_id"),
        intervention_id=_text(intervention_id, "intervention_id"),
        causal_route=causal_route,
        disease_state_node=disease_state_node,
        intervention_target=intervention_target,
        action=action,
        direction=direction,
        intermediate_state=intermediate_state,
        endpoint_id=_text(endpoint_id, "endpoint_id"),
        evidence_ids=_strings(evidence_ids, "route evidence_ids", required=True),
    )
    route = replace(draft, route_id=_stable_id("ROUTE", ROUTE_ID_RULE, _route_signature(draft)))
    validate_structured_route(route)
    return route


def validate_structured_route(route: StructuredCausalRoute) -> None:
    if not isinstance(route.causal_route, CausalRoute):
        raise DiscoveryContractError("route: invalid causal route")
    if not isinstance(route.action, InterventionAction) or not isinstance(
        route.direction, EffectDirection
    ):
        raise DiscoveryContractError("route: invalid action or direction")
    for name in ("disease_state_node", "intervention_target", "intermediate_state"):
        validate_node(getattr(route, name), f"route.{name}")
    for name in ("case_revision_id", "intervention_id", "endpoint_id"):
        if _text(getattr(route, name), f"route.{name}") != getattr(route, name):
            raise DiscoveryContractError(f"route.{name}: not canonical")
    if tuple(sorted(set(route.evidence_ids))) != route.evidence_ids or not route.evidence_ids:
        raise DiscoveryContractError("route evidence IDs must be nonempty, unique, and ordered")
    expected = _stable_id("ROUTE", ROUTE_ID_RULE, _route_signature(route))
    if route.route_id != expected:
        raise DiscoveryContractError("route content-derived ID mismatch")


def normalize_structured_routes(
    routes: Iterable[StructuredCausalRoute],
) -> tuple[StructuredCausalRoute, ...]:
    """Deduplicate equivalent structures and union evidence without reading prose."""

    grouped: dict[bytes, StructuredCausalRoute] = {}
    for route in routes:
        validate_structured_route(route)
        key = canonical_bytes(_route_signature(route))
        prior = grouped.get(key)
        if prior is None:
            grouped[key] = route
        else:
            representative = min((prior, route), key=canonical_bytes)
            grouped[key] = replace(
                representative,
                evidence_ids=tuple(sorted(set(prior.evidence_ids) | set(route.evidence_ids))),
            )
    return tuple(sorted(grouped.values(), key=lambda row: row.route_id))


def make_case_model_record(
    *,
    output_kind: CaseModelOutputKind,
    primary_node: ScientificNode,
    related_nodes: Iterable[ScientificNode] = (),
    action: InterventionAction = InterventionAction.UNKNOWN,
    direction: EffectDirection = EffectDirection.UNKNOWN,
    endpoint_ids: Iterable[str] = (),
    evidence_ids: Iterable[str],
    source_mapping_ids: Iterable[str] = (),
    uncertainty: Iterable[Uncertainty] = (),
) -> CaseModelRecord:
    body = {
        "output_kind": output_kind,
        "primary_node": primary_node,
        "related_nodes": tuple(sorted(set(related_nodes), key=canonical_bytes)),
        "action": action,
        "direction": direction,
        "endpoint_ids": _strings(endpoint_ids, "case-model endpoint_ids"),
        "evidence_ids": _strings(evidence_ids, "case-model evidence_ids", required=True),
        "source_mapping_ids": _strings(source_mapping_ids, "case-model source_mapping_ids"),
        "uncertainty": tuple(sorted(set(uncertainty), key=lambda row: row.kind.value)),
    }
    return CaseModelRecord(
        record_id=_stable_id("CASE-MODEL", CASE_MODEL_RECORD_ID_RULE, body), **body
    )


def validate_broad_domain_contracts(
    contracts: Iterable[BroadDomainContract] = BROAD_DOMAIN_CONTRACTS,
) -> None:
    rows = tuple(contracts)
    if len({row.domain for row in rows}) != len(rows):
        raise DiscoveryContractError("broad domains must be unique")
    owners: dict[str, BroadDomain] = {}
    for row in rows:
        for field_name in row.owned_output_fields:
            if field_name in owners:
                raise DiscoveryContractError(
                    f"broad responsibility {field_name!r} is duplicated by "
                    f"{owners[field_name].value} and {row.domain.value}"
                )
            owners[field_name] = row.domain
        if set(row.owned_output_fields) & set(row.prohibited_output_fields):
            raise DiscoveryContractError(f"{row.domain.value}: owned and prohibited fields overlap")
    if set(owners) != set(_CASE_MODEL_FIELDS):
        raise DiscoveryContractError("broad-domain responsibilities are incomplete or uncontrolled")


def _validate_uncertainty(rows: tuple[Uncertainty, ...], label: str) -> None:
    for row in rows:
        if not isinstance(row, Uncertainty) or not isinstance(row.kind, UncertaintyKind) or not isinstance(
            row.level, UncertaintyLevel
        ):
            raise DiscoveryContractError(f"{label}: invalid uncertainty")
        _text(row.note, f"{label}.note")
    if len({row.kind for row in rows}) != len(rows):
        raise DiscoveryContractError(f"{label}: uncertainty kinds must be unique")
    expected = tuple(
        sorted(rows, key=lambda row: (row.kind.value, row.level.value, row.note))
    )
    if expected != rows:
        raise DiscoveryContractError(f"{label}: uncertainty must be unique and ordered")


def _record_body(record: Any, id_field: str) -> dict[str, Any]:
    return {field.name: getattr(record, field.name) for field in fields(record) if field.name != id_field}


def _reduce(records: Iterable[Any], id_field: str, expected_type: type[Any]) -> tuple[Any, ...]:
    reduced: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, expected_type):
            raise DiscoveryContractError(f"expected {expected_type.__name__}")
        record_id = _text(getattr(record, id_field), id_field)
        prior = reduced.get(record_id)
        if prior is not None and canonical_bytes(prior) != canonical_bytes(record):
            raise DiscoveryContractError(f"idempotency conflict for {record_id}")
        reduced[record_id] = record
    return tuple(reduced[key] for key in sorted(reduced))


def build_broad_case_model(
    case: CaseRevision,
    *,
    disease_mechanisms: Iterable[CaseModelRecord] = (),
    directional_targets: Iterable[CaseModelRecord] = (),
    phenotypes_and_signatures: Iterable[CaseModelRecord] = (),
    tissues_and_cell_types: Iterable[CaseModelRecord] = (),
    substrates_and_metabolites: Iterable[CaseModelRecord] = (),
    compensatory_nodes: Iterable[CaseModelRecord] = (),
    contraindicated_mechanisms: Iterable[CaseModelRecord] = (),
    endpoint_mappings: Iterable[CaseModelRecord] = (),
    unresolved_direction_conflicts: Iterable[DirectionConflict] = (),
    pharmacology_seed_emissions: Iterable[PharmacologySeedEmission] = (),
    evidence_records: Iterable[EvidenceRecord] = (),
    expert_judgments: Iterable[ExpertJudgment] = (),
) -> BroadCaseModelSnapshot:
    validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise DiscoveryContractError("broad case-model construction requires a READY case")
    validate_broad_domain_contracts()
    valid_endpoints = {row.endpoint_id for row in case.endpoints}
    evidence = _reduce(evidence_records, "evidence_id", EvidenceRecord)
    evidence_ids = {row.evidence_id for row in evidence}
    for row in evidence:
        for name in (
            "source_id", "source_release", "native_record_id", "locator",
            "retrieval_content_receipt_id", "claim_id",
        ):
            _text(getattr(row, name), f"evidence {row.evidence_id}.{name}")
        if not isinstance(row.modality, EvidenceModality):
            raise DiscoveryContractError(f"evidence {row.evidence_id}: invalid modality")

    supplied = {
        "disease_mechanisms": disease_mechanisms,
        "directional_targets": directional_targets,
        "phenotypes_and_signatures": phenotypes_and_signatures,
        "tissues_and_cell_types": tissues_and_cell_types,
        "substrates_and_metabolites": substrates_and_metabolites,
        "compensatory_nodes": compensatory_nodes,
        "contraindicated_mechanisms": contraindicated_mechanisms,
        "endpoint_mappings": endpoint_mappings,
    }
    normalized: dict[str, tuple[CaseModelRecord, ...]] = {}
    for field_name, records in supplied.items():
        rows = _reduce(records, "record_id", CaseModelRecord)
        expected_kind = _FIELD_KIND[field_name]
        for row in rows:
            if row.output_kind is not expected_kind:
                raise DiscoveryContractError(f"{field_name}: wrong output kind")
            validate_node(row.primary_node, f"{field_name}.primary_node")
            for node in row.related_nodes:
                validate_node(node, f"{field_name}.related_nodes")
            if not set(row.endpoint_ids).issubset(valid_endpoints):
                raise DiscoveryContractError(f"{field_name}: unknown endpoint")
            if not set(row.evidence_ids).issubset(evidence_ids):
                raise DiscoveryContractError(f"{field_name}: ungrounded evidence ID")
            if not isinstance(row.action, InterventionAction) or not isinstance(
                row.direction, EffectDirection
            ):
                raise DiscoveryContractError(f"{field_name}: invalid action or direction")
            _validate_uncertainty(row.uncertainty, field_name)
            expected_id = _stable_id(
                "CASE-MODEL", CASE_MODEL_RECORD_ID_RULE, _record_body(row, "record_id")
            )
            if row.record_id != expected_id:
                raise DiscoveryContractError(f"{field_name}: record ID mismatch")
        normalized[field_name] = rows

    conflicts = _reduce(unresolved_direction_conflicts, "conflict_id", DirectionConflict)
    for row in conflicts:
        validate_node(row.subject_node, "direction conflict subject")
        if len(row.asserted_directions) < 2 or len(set(row.asserted_directions)) < 2:
            raise DiscoveryContractError("direction conflict requires at least two distinct directions")
        if (
            tuple(sorted(set(row.asserted_directions), key=lambda value: value.value))
            != row.asserted_directions
            or any(not isinstance(value, EffectDirection) for value in row.asserted_directions)
        ):
            raise DiscoveryContractError("direction conflict values must be controlled and ordered")
        if not set(row.evidence_ids).issubset(evidence_ids):
            raise DiscoveryContractError("direction conflict has ungrounded evidence")
        expected = _stable_id(
            "DIRECTION-CONFLICT", "schema-v7-direction-conflict-v1", _record_body(row, "conflict_id")
        )
        if row.conflict_id != expected:
            raise DiscoveryContractError("direction conflict ID mismatch")

    emissions = _reduce(pharmacology_seed_emissions, "emission_id", PharmacologySeedEmission)
    for row in emissions:
        for name in (
            "source_id", "source_release", "native_record_id", "assertion_locator",
            "query_id", "query_record_locator", "retrieval_content_receipt_id",
            "compound_hint_kind", "compound_hint_value",
        ):
            _text(getattr(row, name), f"seed emission.{name}")
        _raw_text(row.raw_intervention_assertion, "seed emission.raw_intervention_assertion")
        if not set(row.endpoint_ids).issubset(valid_endpoints) or not row.endpoint_ids:
            raise DiscoveryContractError("seed emission endpoint links are invalid")
        if not set(row.evidence_ids).issubset(evidence_ids) or not row.evidence_ids:
            raise DiscoveryContractError("seed emission must preserve grounded evidence")
        if not row.evidence_modalities or not row.chemical_universes:
            raise DiscoveryContractError("seed emission requires factorized modality and universe")
        if (
            any(not isinstance(value, EvidenceModality) for value in row.evidence_modalities)
            or any(not isinstance(value, ChemicalUniverse) for value in row.chemical_universes)
            or not isinstance(row.development_status, DevelopmentStatus)
        ):
            raise DiscoveryContractError("seed emission has an invalid orthogonal dimension")
        _validate_uncertainty(row.uncertainty, "seed emission")
        if not row.structured_routes:
            raise DiscoveryContractError("seed emission requires at least one structural route")
        for route in row.structured_routes:
            validate_structured_route(route)
            if route.case_revision_id != case.case_revision_id:
                raise DiscoveryContractError("seed emission route case mismatch")
            if route.endpoint_id not in valid_endpoints:
                raise DiscoveryContractError("seed emission route endpoint mismatch")
            if not set(route.evidence_ids).issubset(evidence_ids):
                raise DiscoveryContractError("seed emission route evidence is ungrounded")
        expected = _stable_id(
            "SEED-EMISSION", "schema-v7-pharmacology-seed-emission-v1",
            _record_body(row, "emission_id"),
        )
        if row.emission_id != expected:
            raise DiscoveryContractError("seed emission ID mismatch")

    judgments = _reduce(expert_judgments, "judgment_id", ExpertJudgment)
    for row in judgments:
        if not isinstance(row.status, JudgmentStatus):
            raise DiscoveryContractError("expert judgment has an invalid status")
        if row.status is JudgmentStatus.RECORDED:
            for name in (
                "field_path", "interpretation_question", "selected_value", "model_id",
                "prompt_template_version", "rationale",
            ):
                _text(getattr(row, name), f"judgment.{name}")
            if not set(row.evidence_ids).issubset(evidence_ids):
                raise DiscoveryContractError("expert judgment has ungrounded evidence")
            expected = _stable_id(
                "EXPERT-JUDGMENT",
                "schema-v7-expert-judgment-v1",
                _record_body(row, "judgment_id"),
            )
            if row.judgment_id != expected:
                raise DiscoveryContractError("expert judgment ID mismatch")

    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": DISCOVERY_MODEL_VERSION,
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        **normalized,
        "unresolved_direction_conflicts": conflicts,
        "pharmacology_seed_emissions": emissions,
        "evidence_records": evidence,
        "expert_judgments": judgments,
    }
    return BroadCaseModelSnapshot(
        snapshot_id=_stable_id("BROAD-CASE", CASE_MODEL_SNAPSHOT_ID_RULE, body), **body
    )


def make_direction_conflict(
    *,
    subject_node: ScientificNode,
    asserted_directions: Iterable[EffectDirection],
    evidence_ids: Iterable[str],
    blocking: bool = True,
) -> DirectionConflict:
    body = {
        "subject_node": subject_node,
        "asserted_directions": _enums(
            asserted_directions, EffectDirection, "asserted_directions"
        ),
        "evidence_ids": _strings(evidence_ids, "conflict evidence_ids", required=True),
        "blocking": bool(blocking),
    }
    return DirectionConflict(
        conflict_id=_stable_id("DIRECTION-CONFLICT", "schema-v7-direction-conflict-v1", body),
        **body,
    )


def make_expert_judgment(
    *,
    field_path: str,
    interpretation_question: str,
    selected_value: str,
    evidence_ids: Iterable[str],
    model_id: str,
    prompt_template_version: str,
    rationale: str,
) -> ExpertJudgment:
    body = {
        "field_path": _text(field_path, "judgment field_path"),
        "interpretation_question": _text(
            interpretation_question, "judgment interpretation_question"
        ),
        "selected_value": _text(selected_value, "judgment selected_value"),
        "evidence_ids": _strings(evidence_ids, "judgment evidence_ids", required=True),
        "model_id": _text(model_id, "judgment model_id"),
        "prompt_template_version": _text(
            prompt_template_version, "judgment prompt_template_version"
        ),
        "rationale": _text(rationale, "judgment rationale"),
        "status": JudgmentStatus.RECORDED,
    }
    return ExpertJudgment(
        judgment_id=_stable_id(
            "EXPERT-JUDGMENT", "schema-v7-expert-judgment-v1", body
        ),
        **body,
    )


def extract_grounded_evidence(
    snapshot: BroadCaseModelSnapshot,
) -> tuple[EvidenceRecord, ...]:
    """Return exactly the evidence referenced by broad outputs and seed emissions."""

    referenced: set[str] = set()
    for field_name in _FIELD_KIND:
        for row in getattr(snapshot, field_name):
            referenced.update(row.evidence_ids)
    for row in snapshot.unresolved_direction_conflicts:
        referenced.update(row.evidence_ids)
    for row in snapshot.pharmacology_seed_emissions:
        referenced.update(row.evidence_ids)
        for route in row.structured_routes:
            referenced.update(route.evidence_ids)
    for row in snapshot.expert_judgments:
        referenced.update(row.evidence_ids)
    index = {row.evidence_id: row for row in snapshot.evidence_records}
    missing = referenced - set(index)
    if missing:
        raise DiscoveryContractError(f"broad case model has missing evidence IDs: {sorted(missing)}")
    return tuple(index[evidence_id] for evidence_id in sorted(referenced))


def make_seed_emission(**values: Any) -> PharmacologySeedEmission:
    values = dict(values)
    values["source_id"] = _text(values["source_id"], "source_id")
    values["source_release"] = _text(values["source_release"], "source_release")
    values["native_record_id"] = _text(values["native_record_id"], "native_record_id")
    values["assertion_locator"] = _text(values["assertion_locator"], "assertion_locator")
    values["raw_intervention_assertion"] = _raw_text(
        values["raw_intervention_assertion"], "raw_intervention_assertion"
    )
    for name in (
        "query_id", "query_record_locator", "retrieval_content_receipt_id",
        "compound_hint_kind", "compound_hint_value",
    ):
        values[name] = _text(values[name], name)
    values["compound_hint_namespace"] = (
        _text(values.get("compound_hint_namespace", ""), "compound_hint_namespace").upper()
        if values.get("compound_hint_namespace") else ""
    )
    values["endpoint_ids"] = _strings(values["endpoint_ids"], "endpoint_ids", required=True)
    values["structured_routes"] = normalize_structured_routes(values["structured_routes"])
    if not values["structured_routes"]:
        raise DiscoveryContractError("seed emission requires at least one structural route")
    values["evidence_modalities"] = _enums(
        values["evidence_modalities"], EvidenceModality, "evidence_modalities"
    )
    values["chemical_universes"] = _enums(
        values["chemical_universes"], ChemicalUniverse, "chemical_universes"
    )
    values["uncertainty"] = tuple(
        sorted(set(values.get("uncertainty", ())), key=lambda row: row.kind.value)
    )
    values["evidence_ids"] = _strings(values["evidence_ids"], "evidence_ids", required=True)
    body = {key: value for key, value in values.items() if key != "emission_id"}
    return PharmacologySeedEmission(
        emission_id=_stable_id(
            "SEED-EMISSION", "schema-v7-pharmacology-seed-emission-v1", body
        ),
        **body,
    )


def enumerate_discovery_jobs(
    case: CaseRevision,
    broad_case_model: BroadCaseModelSnapshot,
) -> tuple[DiscoveryJob, ...]:
    validate_case_revision(case)
    if broad_case_model.case_revision_id != case.case_revision_id:
        raise DiscoveryContractError("case-model snapshot does not belong to the case revision")
    record_ids = tuple(
        sorted(
            row.record_id
            for field_name in _FIELD_KIND
            for row in getattr(broad_case_model, field_name)
        )
    )
    development_statuses = tuple(sorted(DevelopmentStatus, key=lambda row: row.value))
    uncertainty_kinds = tuple(sorted(UncertaintyKind, key=lambda row: row.value))
    jobs: list[DiscoveryJob] = []
    for endpoint in sorted(case.endpoints, key=lambda row: row.endpoint_id):
        for causal_route in CausalRoute:
            for modality in EvidenceModality:
                for universe in ChemicalUniverse:
                    body = {
                        "case_revision_id": case.case_revision_id,
                        "causal_route": causal_route,
                        "evidence_modality": modality,
                        "chemical_universe": universe,
                        "development_statuses": development_statuses,
                        "endpoint_id": endpoint.endpoint_id,
                        "uncertainty_kinds": uncertainty_kinds,
                        "case_model_record_ids": record_ids,
                    }
                    jobs.append(
                        DiscoveryJob(
                            job_id=_stable_id("DISCOVERY-JOB", DISCOVERY_JOB_ID_RULE, body),
                            **body,
                        )
                    )
    return tuple(sorted(jobs, key=lambda row: row.job_id))


def materialize_seed_emission(
    case: CaseRevision,
    emission: PharmacologySeedEmission,
) -> tuple[Any, Any, Any]:
    """Convert one preserved pharmacology mapping into seed-funnel inputs.

    The import is intentionally local so the discovery contract remains the
    upstream owner of shared dimensions while the seed model may import it.
    """

    from v7_seed_funnel import (  # pylint: disable=import-outside-toplevel
        CompoundHintKind,
        SeedIdentityStatus,
        SeedUncertainty,
        known_development_status,
        make_candidate_seed,
        make_compound_hint,
        make_discovery_route,
        make_source_mapping,
        unknown_development_status,
    )

    validate_case_revision(case)
    if case.case_status is not CaseStatus.READY:
        raise DiscoveryContractError("seed emission materialization requires a READY case")
    if not emission.structured_routes:
        raise DiscoveryContractError("seed emission requires at least one structural route")
    if case.case_revision_id != emission.structured_routes[0].case_revision_id:
        raise DiscoveryContractError("seed emission belongs to a different case revision")
    mapping = make_source_mapping(
        case,
        source_id=emission.source_id,
        source_release=emission.source_release,
        native_record_id=emission.native_record_id,
        assertion_locator=emission.assertion_locator,
        raw_intervention_assertion=emission.raw_intervention_assertion,
    )
    discovery_route = make_discovery_route(
        mapping,
        query_id=emission.query_id,
        query_record_locator=emission.query_record_locator,
        retrieval_content_receipt_id=emission.retrieval_content_receipt_id,
    )
    structural_routes = tuple(
        make_structured_route(
            case_revision_id=route.case_revision_id,
            intervention_id=mapping.seed_id,
            causal_route=route.causal_route,
            disease_state_node=route.disease_state_node,
            intervention_target=route.intervention_target,
            action=route.action,
            direction=route.direction,
            intermediate_state=route.intermediate_state,
            endpoint_id=route.endpoint_id,
            evidence_ids=route.evidence_ids,
        )
        for route in emission.structured_routes
    )
    development_status = (
        unknown_development_status("The source reported no development status.")
        if emission.development_status is DevelopmentStatus.UNKNOWN
        else known_development_status(emission.development_status)
    )
    seed = make_candidate_seed(
        case,
        mapping,
        endpoint_ids=emission.endpoint_ids,
        compound_hint=make_compound_hint(
            CompoundHintKind(emission.compound_hint_kind),
            emission.compound_hint_value,
            namespace=emission.compound_hint_namespace,
        ),
        discovery_route_ids=(discovery_route.route_id,),
        structured_routes=structural_routes,
        evidence_modalities=emission.evidence_modalities,
        chemical_universes=emission.chemical_universes,
        development_status_hint=development_status,
        identity_status=SeedIdentityStatus.UNASSESSED,
        uncertainty=tuple(
            SeedUncertainty(kind=row.kind, level=row.level, note=row.note)
            for row in emission.uncertainty
        )
        or (
            SeedUncertainty(
                kind=UncertaintyKind.IDENTITY,
                level=UncertaintyLevel.UNKNOWN,
                note="Identity has not yet been resolved at case-model emission depth.",
            ),
        ),
    )
    return mapping, discovery_route, seed


def make_discovery_hypothesis(
    *,
    case_revision_id: str,
    intervention_id: str,
    structured_route: StructuredCausalRoute,
    evidence_modality: EvidenceModality,
    chemical_universe: ChemicalUniverse,
    development_status: DevelopmentStatus,
    endpoint_id: str,
    uncertainty: Iterable[Uncertainty],
    source_mapping_ids: Iterable[str],
    evidence_ids: Iterable[str],
) -> DiscoveryHypothesis:
    body = {
        "case_revision_id": _text(case_revision_id, "case_revision_id"),
        "intervention_id": _text(intervention_id, "intervention_id"),
        "structured_route": structured_route,
        "evidence_modality": evidence_modality,
        "chemical_universe": chemical_universe,
        "development_status": development_status,
        "endpoint_id": _text(endpoint_id, "endpoint_id"),
        "uncertainty": tuple(sorted(set(uncertainty), key=lambda row: row.kind.value)),
        "source_mapping_ids": _strings(source_mapping_ids, "source_mapping_ids", required=True),
        "evidence_ids": _strings(evidence_ids, "evidence_ids", required=True),
    }
    result = DiscoveryHypothesis(
        hypothesis_id=_stable_id("DISCOVERY", DISCOVERY_HYPOTHESIS_ID_RULE, body), **body
    )
    validate_discovery_hypothesis(result)
    return result


def validate_discovery_hypothesis(row: DiscoveryHypothesis) -> None:
    if not isinstance(row, DiscoveryHypothesis):
        raise DiscoveryContractError("expected DiscoveryHypothesis")
    validate_structured_route(row.structured_route)
    if (
        row.structured_route.case_revision_id != row.case_revision_id
        or row.structured_route.intervention_id != row.intervention_id
        or row.structured_route.endpoint_id != row.endpoint_id
    ):
        raise DiscoveryContractError("discovery hypothesis and structural route disagree")
    if not isinstance(row.evidence_modality, EvidenceModality) or not isinstance(
        row.chemical_universe, ChemicalUniverse
    ) or not isinstance(row.development_status, DevelopmentStatus):
        raise DiscoveryContractError("discovery hypothesis has an invalid orthogonal dimension")
    _validate_uncertainty(row.uncertainty, "discovery hypothesis")
    if tuple(sorted(set(row.source_mapping_ids))) != row.source_mapping_ids or not row.source_mapping_ids:
        raise DiscoveryContractError("source mapping IDs must be nonempty, unique, and ordered")
    if tuple(sorted(set(row.evidence_ids))) != row.evidence_ids or not row.evidence_ids:
        raise DiscoveryContractError("evidence IDs must be nonempty, unique, and ordered")
    expected = _stable_id(
        "DISCOVERY", DISCOVERY_HYPOTHESIS_ID_RULE, _record_body(row, "hypothesis_id")
    )
    if row.hypothesis_id != expected:
        raise DiscoveryContractError("discovery hypothesis content-derived ID mismatch")


def deduplicate_discovery_hypotheses(
    hypotheses: Iterable[DiscoveryHypothesis],
) -> tuple[DiscoveryHypothesis, ...]:
    """Deduplicate exact factorized hypotheses while preserving distinct routes."""

    reduced: dict[bytes, DiscoveryHypothesis] = {}
    for row in hypotheses:
        validate_discovery_hypothesis(row)
        key = canonical_bytes(
            {
                "case_revision_id": row.case_revision_id,
                "intervention_id": row.intervention_id,
                "route_id": row.structured_route.route_id,
                "evidence_modality": row.evidence_modality,
                "chemical_universe": row.chemical_universe,
                "development_status": row.development_status,
                "endpoint_id": row.endpoint_id,
            }
        )
        prior = reduced.get(key)
        if prior is None:
            reduced[key] = row
        else:
            representative = min((prior, row), key=canonical_bytes)
            merged_evidence = tuple(sorted(set(prior.evidence_ids) | set(row.evidence_ids)))
            merged_mappings = tuple(
                sorted(set(prior.source_mapping_ids) | set(row.source_mapping_ids))
            )
            merged_uncertainty = tuple(
                sorted(set(prior.uncertainty) | set(row.uncertainty), key=lambda item: item.kind.value)
            )
            reduced[key] = make_discovery_hypothesis(
                case_revision_id=representative.case_revision_id,
                intervention_id=representative.intervention_id,
                structured_route=replace(
                    representative.structured_route,
                    evidence_ids=tuple(
                        sorted(
                            set(prior.structured_route.evidence_ids)
                            | set(row.structured_route.evidence_ids)
                        )
                    ),
                ),
                evidence_modality=representative.evidence_modality,
                chemical_universe=representative.chemical_universe,
                development_status=representative.development_status,
                endpoint_id=representative.endpoint_id,
                uncertainty=merged_uncertainty,
                source_mapping_ids=merged_mappings,
                evidence_ids=merged_evidence,
            )
    return tuple(sorted(reduced.values(), key=lambda row: row.hypothesis_id))


def score_discovery_hypotheses(
    hypotheses: Iterable[DiscoveryHypothesis],
) -> tuple[DiscoveryScore, ...]:
    rows = tuple(hypotheses)
    modalities_by_intervention: dict[str, set[EvidenceModality]] = {}
    for row in rows:
        modalities_by_intervention.setdefault(row.intervention_id, set()).add(row.evidence_modality)
    scores: list[DiscoveryScore] = []
    for row in rows:
        route = row.structured_route
        completeness = sum(
            node.status is NodeStatus.KNOWN
            for node in (
                route.disease_state_node,
                route.intervention_target,
                route.intermediate_state,
            )
        ) + int(route.action is not InterventionAction.UNKNOWN) + int(
            route.direction is not EffectDirection.UNKNOWN
        )
        evidence_count = len(set(row.evidence_ids) | set(route.evidence_ids))
        modality_count = len(modalities_by_intervention[row.intervention_id])
        scores.append(
            DiscoveryScore(
                hypothesis_id=row.hypothesis_id,
                structural_completeness=completeness,
                grounded_evidence_count=evidence_count,
                independent_modality_count=modality_count,
                total=100 * completeness + 10 * modality_count + min(evidence_count, 9),
            )
        )
    return tuple(sorted(scores, key=lambda row: row.hypothesis_id))


def rank_discovery_hypotheses(
    hypotheses: Iterable[DiscoveryHypothesis],
) -> tuple[RankedDiscoveryHypothesis, ...]:
    rows = deduplicate_discovery_hypotheses(hypotheses)
    scores = {row.hypothesis_id: row for row in score_discovery_hypotheses(rows)}
    ordered = sorted(
        rows,
        key=lambda row: (-scores[row.hypothesis_id].total, row.hypothesis_id),
    )
    return tuple(
        RankedDiscoveryHypothesis(rank=index, hypothesis=row, score=scores[row.hypothesis_id])
        for index, row in enumerate(ordered, 1)
    )


def deterministic_sample(
    hypotheses: Iterable[DiscoveryHypothesis], sample_size: int, *, salt: str
) -> tuple[DiscoveryHypothesis, ...]:
    if sample_size < 0:
        raise DiscoveryContractError("sample_size cannot be negative")
    normalized_salt = _text(salt, "sampling salt")
    rows = deduplicate_discovery_hypotheses(hypotheses)
    return tuple(
        sorted(
            sorted(
                rows,
                key=lambda row: content_sha256(
                    {"salt": normalized_salt, "hypothesis_id": row.hypothesis_id}
                ),
            )[:sample_size],
            key=lambda row: row.hypothesis_id,
        )
    )


def build_discovery_snapshot(
    case_revision_id: str, hypotheses: Iterable[DiscoveryHypothesis]
) -> DiscoverySnapshot:
    normalized = deduplicate_discovery_hypotheses(hypotheses)
    if any(row.case_revision_id != case_revision_id for row in normalized):
        raise DiscoveryContractError("discovery hypothesis case mismatch")
    ranked = rank_discovery_hypotheses(normalized)
    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": DISCOVERY_MODEL_VERSION,
        "case_revision_id": case_revision_id,
        "hypotheses": normalized,
        "ranked_hypotheses": ranked,
    }
    return DiscoverySnapshot(
        snapshot_id=_stable_id("DISCOVERY-SNAPSHOT", DISCOVERY_SNAPSHOT_ID_RULE, body),
        **body,
    )


def load_frozen_source_payload(
    path: str | Path, *, source_id: str, source_release: str
) -> dict[str, Any]:
    """Retrieve and parse a local frozen JSON payload; this is not a live adapter."""

    target = Path(path).expanduser().resolve()
    payload = target.read_bytes()
    try:
        parsed = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DiscoveryContractError(f"invalid frozen JSON payload: {exc}") from exc
    if not isinstance(parsed, (dict, list)):
        raise DiscoveryContractError("frozen source payload must contain a JSON object or array")
    return {
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "payload_sha256": content_sha256(parsed),
        "record_count": len(parsed) if isinstance(parsed, list) else 1,
        "records": parsed,
    }


def cache_model_output(
    cache_root: str | Path,
    *,
    namespace: str,
    inputs: Any,
    source_releases: Iterable[str],
    output: Any,
) -> Path:
    """Write only a content-addressed cache entry; never mutate canonical state."""

    root = Path(cache_root).expanduser().resolve()
    key = content_sha256(
        {
            "namespace": _text(namespace, "cache namespace"),
            "inputs": inputs,
            "source_releases": _strings(source_releases, "source releases"),
            "model_version": DISCOVERY_MODEL_VERSION,
        }
    )
    payload = canonical_bytes(output) + b"\n"
    target = root / namespace / f"{key}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise DiscoveryContractError("content-addressed cache conflict")
        return target
    descriptor: int | None = None
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if target.read_bytes() != payload:
            raise DiscoveryContractError("content-addressed cache conflict")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return target


__all__ = [
    "BROAD_DOMAIN_CONTRACTS",
    "BroadCaseModelSnapshot",
    "BroadDomain",
    "BroadDomainContract",
    "CaseModelOutputKind",
    "CaseModelRecord",
    "CausalRoute",
    "ChemicalUniverse",
    "DevelopmentStatus",
    "DirectionConflict",
    "DiscoveryContractError",
    "DiscoveryHypothesis",
    "DiscoveryJob",
    "DiscoveryScore",
    "DiscoverySnapshot",
    "EffectDirection",
    "EvidenceModality",
    "EvidenceRecord",
    "ExpertJudgment",
    "InterventionAction",
    "JudgmentStatus",
    "NodeStatus",
    "PharmacologySeedEmission",
    "RankedDiscoveryHypothesis",
    "ScientificNode",
    "StructuredCausalRoute",
    "Uncertainty",
    "UncertaintyKind",
    "UncertaintyLevel",
    "build_broad_case_model",
    "build_discovery_snapshot",
    "cache_model_output",
    "deduplicate_discovery_hypotheses",
    "deterministic_sample",
    "enumerate_discovery_jobs",
    "extract_grounded_evidence",
    "known_node",
    "load_frozen_source_payload",
    "materialize_seed_emission",
    "make_case_model_record",
    "make_direction_conflict",
    "make_discovery_hypothesis",
    "make_expert_judgment",
    "make_seed_emission",
    "make_structured_route",
    "normalize_structured_routes",
    "not_applicable_node",
    "rank_discovery_hypotheses",
    "score_discovery_hypotheses",
    "unknown_node",
    "validate_broad_domain_contracts",
    "validate_discovery_hypothesis",
    "validate_node",
    "validate_structured_route",
]
