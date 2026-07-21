#!/usr/bin/env python3
"""Canonical schema-v7 case model, deterministic initialization, and legacy inspection.

This module intentionally stops at the normalized case and endpoint portfolio.  It
does not define seeds, candidates, discovery, ranking, audit, outputs, or a runtime
DAG.  It uses only the Python standard library supported by the repository CI.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from collections import abc as collections_abc
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType, UnionType
from typing import Any, Generic, Mapping, TypeVar, Union, get_args, get_origin, get_type_hints


SCHEMA_VERSION = 7
CASE_MODEL_VERSION = "schema-v7-case-v1"
PROVENANCE_VERSION = "schema-v7-case-provenance-v1"
ENDPOINT_ID_RULE = "schema-v7-endpoint-id-v1"


class CaseInputError(ValueError):
    """Raised when raw case input is malformed or contradictory."""


class ValueStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CaseStatus(str, Enum):
    READY = "ready"
    NEEDS_RESOLUTION = "needs_resolution"


class ProvenanceClassification(str, Enum):
    USER_SUPPLIED = "user_supplied"
    NORMALIZED = "normalized"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class UnresolvedKind(str, Enum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class ConceptKind(str, Enum):
    GENE = "gene"
    DISEASE = "disease"
    PHENOTYPE = "phenotype"
    ENDPOINT_CONSTRUCT = "endpoint_construct"


class GeneDiseaseState(str, Enum):
    INCREASED_ACTIVITY = "increased_activity"
    DECREASED_ACTIVITY = "decreased_activity"
    GAIN_OF_FUNCTION = "gain_of_function"
    LOSS_OF_FUNCTION = "loss_of_function"
    OVEREXPRESSION = "overexpression"
    UNDEREXPRESSION = "underexpression"
    DYSREGULATED = "dysregulated"
    MIXED = "mixed"


class TherapeuticModulation(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"
    ACTIVATE = "activate"
    INHIBIT = "inhibit"
    STABILIZE = "stabilize"
    REPLACE = "replace"
    RESTORE = "restore"
    MODULATE = "modulate"
    AVOID = "avoid"


class EndpointRole(str, Enum):
    BENEFIT = "benefit"
    SAFETY = "safety"
    BIOMARKER = "biomarker"


class EndpointType(str, Enum):
    CLINICAL_OUTCOME = "clinical_outcome"
    FUNCTIONAL_OUTCOME = "functional_outcome"
    SYMPTOM_OUTCOME = "symptom_outcome"
    SAFETY_OUTCOME = "safety_outcome"
    BIOMARKER = "biomarker"
    SURROGATE = "surrogate"
    COMPOSITE = "composite"
    OTHER = "other"


class EndpointDirection(str, Enum):
    INCREASE_IS_BENEFIT = "increase_is_benefit"
    DECREASE_IS_BENEFIT = "decrease_is_benefit"
    INCREASE_IS_HARM = "increase_is_harm"
    DECREASE_IS_HARM = "decrease_is_harm"
    TARGET_RANGE = "target_range"
    EVENT_AVOIDANCE = "event_avoidance"


class EndpointPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    EXPLORATORY = "exploratory"


class EndpointRelationshipType(str, Enum):
    SURROGATE_FOR = "surrogate_for"
    COMPONENT_OF = "component_of"
    SUPPORTS = "supports"
    COMPETES_WITH = "competes_with"
    SAFETY_CONSTRAINT_FOR = "safety_constraint_for"
    OTHER = "other"


T = TypeVar("T")


@dataclass(frozen=True)
class QualifiedValue(Generic[T]):
    status: ValueStatus
    value: T | None
    reason: str


@dataclass(frozen=True)
class CodedIdentifier:
    namespace: str
    identifier: str
    ontology_version: QualifiedValue[str]


@dataclass(frozen=True)
class MappedConcept:
    concept_kind: ConceptKind
    raw_input: str
    label: QualifiedValue[str]
    coding: QualifiedValue[CodedIdentifier]
    mapping_candidates: tuple[CodedIdentifier, ...]
    mapping_rule_id: str


@dataclass(frozen=True)
class GeneContext:
    concept: MappedConcept
    disease_associated_state: QualifiedValue[GeneDiseaseState]
    desired_therapeutic_modulation: QualifiedValue[TherapeuticModulation]


@dataclass(frozen=True)
class PopulationContext:
    description: QualifiedValue[str]
    inclusion: QualifiedValue[tuple[str, ...]]
    exclusion: QualifiedValue[tuple[str, ...]]
    genotypes: QualifiedValue[tuple[str, ...]]


@dataclass(frozen=True)
class TissueContext:
    target: QualifiedValue[str]
    relevance: QualifiedValue[str]


@dataclass(frozen=True)
class StageContext:
    stage: QualifiedValue[str]
    severity: QualifiedValue[str]


@dataclass(frozen=True)
class TargetProductProfile:
    intended_benefit: QualifiedValue[str]
    setting: QualifiedValue[str]
    allowed_routes: QualifiedValue[tuple[str, ...]]
    excluded_routes: QualifiedValue[tuple[str, ...]]
    regimen_constraints: QualifiedValue[tuple[str, ...]]
    exposure_constraints: QualifiedValue[tuple[str, ...]]
    time_horizon: QualifiedValue[str]
    acceptable_risk: QualifiedValue[str]


@dataclass(frozen=True)
class EndpointRelationship:
    relationship_type: EndpointRelationshipType
    related_endpoint_id: str
    rationale: QualifiedValue[str]


@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    stable_key: QualifiedValue[str]
    display_label: QualifiedValue[str]
    construct: QualifiedValue[MappedConcept]
    role: QualifiedValue[EndpointRole]
    endpoint_type: QualifiedValue[EndpointType]
    population: QualifiedValue[str]
    disease_stage: QualifiedValue[str]
    timeframe: QualifiedValue[str]
    measurement: QualifiedValue[str]
    disease_context: QualifiedValue[str]
    direction: QualifiedValue[EndpointDirection]
    priority: QualifiedValue[EndpointPriority]
    required: QualifiedValue[bool]
    relationships: QualifiedValue[tuple[EndpointRelationship, ...]]


@dataclass(frozen=True)
class UnresolvedInput:
    path: str
    kind: UnresolvedKind
    reason: str
    candidates: tuple[str, ...]
    blocking: bool


@dataclass(frozen=True)
class ProvenanceEntry:
    path: str
    classifications: tuple[ProvenanceClassification, ...]
    source_paths: tuple[str, ...]
    rule_id: str
    original_value: Any
    normalized_value: Any
    note: str


@dataclass(frozen=True)
class CaseRevision:
    schema_version: int
    model_version: str
    case_id: str
    case_revision_id: str
    case_status: CaseStatus
    source_input_sha256: str
    original_input: Mapping[str, Any]
    gene: QualifiedValue[GeneContext]
    disease: QualifiedValue[MappedConcept]
    phenotypes: QualifiedValue[tuple[MappedConcept, ...]]
    disease_subtype: QualifiedValue[str]
    population: PopulationContext
    tissue: TissueContext
    disease_stage: StageContext
    target_product_profile: TargetProductProfile
    contraindications: QualifiedValue[tuple[str, ...]]
    excluded_intervention_categories: QualifiedValue[tuple[str, ...]]
    endpoints: tuple[Endpoint, ...]
    unresolved_inputs: tuple[UnresolvedInput, ...]


@dataclass(frozen=True)
class CaseModelProvenance:
    schema_version: int
    provenance_version: str
    case_revision_id: str
    source_input_sha256: str
    entries: tuple[ProvenanceEntry, ...]


@dataclass(frozen=True)
class CaseBundle:
    case_revision: CaseRevision
    provenance: CaseModelProvenance
    validation_metadata: Mapping[str, Any]


_CLASSIFICATION_ORDER = {
    ProvenanceClassification.USER_SUPPLIED: 0,
    ProvenanceClassification.NORMALIZED: 1,
    ProvenanceClassification.INFERRED: 2,
    ProvenanceClassification.UNRESOLVED: 3,
}
_MISSING = object()
_ENDPOINT_ID_PATTERN = re.compile(r"^EP-[A-Z0-9][A-Z0-9_.-]{2,63}$")
_GENE_SYMBOL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{1,30}$")


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _plain(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def _canonical_text(value: Any) -> str:
    if not isinstance(value, str):
        raise CaseInputError(f"Expected text, found {type(value).__name__}")
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _canonical_token(value: Any) -> str:
    return _canonical_text(value).casefold().replace("-", "_").replace(" ", "_")


def _known(value: T) -> QualifiedValue[T]:
    return QualifiedValue(status=ValueStatus.KNOWN, value=value, reason="")


def _unknown(reason: str) -> QualifiedValue[Any]:
    return QualifiedValue(status=ValueStatus.UNKNOWN, value=None, reason=reason)


def _not_applicable(reason: str) -> QualifiedValue[Any]:
    return QualifiedValue(status=ValueStatus.NOT_APPLICABLE, value=None, reason=reason)


def _validate_qualified(value: QualifiedValue[Any], path: str) -> None:
    if value.status is ValueStatus.KNOWN:
        if value.value is None:
            raise CaseInputError(f"{path}: known value cannot be null")
        if value.reason:
            raise CaseInputError(f"{path}: known value must not carry an unresolved reason")
    elif value.value is not None or not value.reason.strip():
        raise CaseInputError(f"{path}: {value.status.value} requires a reason and no value")


def _is_missing(raw: Any, path: str, source_path: str) -> bool:
    if raw is None and source_path:
        raise CaseInputError(
            f"{path}: explicit null is not allowed; use a qualified unknown with a reason"
        )
    return raw is _MISSING or raw is None


def _type_expression(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation).replace("typing.", ""))
    if origin in {Union, UnionType}:
        return " | ".join(_type_expression(arg) for arg in get_args(annotation))
    name = getattr(origin, "__name__", str(origin).replace("typing.", ""))
    return f"{name}[{', '.join(_type_expression(arg) for arg in get_args(annotation))}]"


def validation_metadata() -> dict[str, Any]:
    """Derive field and enum metadata from the canonical typed definitions."""

    dataclasses: dict[str, Any] = {}
    enums: dict[str, list[str]] = {}
    visited: set[Any] = set()

    def visit(annotation: Any) -> None:
        origin = get_origin(annotation)
        if origin in {Union, UnionType}:
            for argument in get_args(annotation):
                visit(argument)
            return
        if origin is not None:
            if is_dataclass(origin):
                visit(origin)
            for argument in get_args(annotation):
                visit(argument)
            return
        if annotation in visited:
            return
        visited.add(annotation)
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            enums[annotation.__name__] = [member.value for member in annotation]
            return
        if isinstance(annotation, type) and is_dataclass(annotation):
            hints = get_type_hints(annotation)
            dataclasses[annotation.__name__] = {
                "additional_fields_allowed": False,
                "required_fields": [field.name for field in fields(annotation)],
                "fields": {
                    field.name: _type_expression(hints[field.name]) for field in fields(annotation)
                },
            }
            for field in fields(annotation):
                visit(hints[field.name])

    visit(CaseRevision)
    visit(CaseModelProvenance)
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": CASE_MODEL_VERSION,
        "root_type": "CaseRevision",
        "provenance_root_type": "CaseModelProvenance",
        "dataclasses": {key: dataclasses[key] for key in sorted(dataclasses)},
        "enums": {key: enums[key] for key in sorted(enums)},
        "cross_field_rules": [
            "at_least_one_gene_disease_or_phenotype_anchor",
            "population_inclusion_exclusion_disjoint",
            "target_product_profile_route_sets_disjoint",
            "endpoint_ids_unique_and_label_independent",
            "endpoint_relationships_resolve_and_are_not_self_referential",
            "required_endpoint_not_marked_not_applicable",
            "ambiguity_is_explicit_and_blocking",
        ],
        "endpoint_id_pattern": _ENDPOINT_ID_PATTERN.pattern,
    }


class _BuildContext:
    def __init__(self) -> None:
        self.provenance: list[ProvenanceEntry] = []
        self.unresolved: list[UnresolvedInput] = []

    def record(
        self,
        path: str,
        classifications: tuple[ProvenanceClassification, ...],
        source_paths: tuple[str, ...],
        rule_id: str,
        original: Any,
        normalized: Any,
        note: str = "",
    ) -> None:
        ordered = tuple(sorted(set(classifications), key=_CLASSIFICATION_ORDER.__getitem__))
        self.provenance.append(
            ProvenanceEntry(
                path=path,
                classifications=ordered,
                source_paths=source_paths,
                rule_id=rule_id,
                original_value=_freeze(_plain(original)),
                normalized_value=_freeze(_plain(normalized)),
                note=note,
            )
        )

    def unresolved_value(
        self,
        path: str,
        kind: UnresolvedKind,
        reason: str,
        *,
        candidates: tuple[str, ...] = (),
        blocking: bool = False,
    ) -> None:
        for index, row in enumerate(self.unresolved):
            if (
                row.path == path
                and row.kind is kind
                and row.reason == reason
                and row.candidates == tuple(candidates)
            ):
                if blocking and not row.blocking:
                    self.unresolved[index] = replace(row, blocking=True)
                return
        self.unresolved.append(
            UnresolvedInput(
                path=path,
                kind=kind,
                reason=reason,
                candidates=tuple(candidates),
                blocking=blocking,
            )
        )


def _get_alias(raw: Mapping[str, Any], names: tuple[str, ...], path: str) -> tuple[Any, str]:
    present = [(name, raw[name]) for name in names if name in raw]
    if not present:
        return _MISSING, ""
    first_name, first_value = present[0]
    for name, value in present[1:]:
        if canonical_bytes(value) != canonical_bytes(first_value):
            raise CaseInputError(f"{path}: contradictory aliases {first_name!r} and {name!r}")
    return first_value, first_name


def _qualified_text(
    raw: Any,
    path: str,
    source_path: str,
    context: _BuildContext,
    missing_reason: str,
    *,
    blocking_when_missing: bool = False,
) -> QualifiedValue[str]:
    if _is_missing(raw, path, source_path):
        value = _unknown(missing_reason)
        context.record(
            path,
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            missing_reason,
        )
        context.unresolved_value(path, UnresolvedKind.MISSING, missing_reason, blocking=blocking_when_missing)
        return value
    if isinstance(raw, Mapping) and "status" in raw:
        unknown = set(raw) - {"status", "value", "reason"}
        if unknown:
            raise CaseInputError(f"{path}: unknown qualified-value fields {sorted(unknown)}")
        try:
            status = ValueStatus(_canonical_token(raw.get("status")))
        except ValueError as exc:
            raise CaseInputError(f"{path}: invalid value status") from exc
        reason = _canonical_text(raw.get("reason", ""))
        supplied_value = raw.get("value")
        if status is ValueStatus.KNOWN:
            if reason:
                raise CaseInputError(f"{path}: known value must not include a reason")
            normalized = _canonical_text(supplied_value)
            if not normalized:
                raise CaseInputError(f"{path}: known text cannot be empty")
            value = _known(normalized)
            classifications = (ProvenanceClassification.USER_SUPPLIED,)
            if normalized != supplied_value:
                classifications += (ProvenanceClassification.NORMALIZED,)
        else:
            if supplied_value is not None:
                raise CaseInputError(f"{path}: {status.value} must not include a value")
            value = _unknown(reason) if status is ValueStatus.UNKNOWN else _not_applicable(reason)
            classifications = (ProvenanceClassification.USER_SUPPLIED,)
            if status is ValueStatus.UNKNOWN:
                classifications += (ProvenanceClassification.UNRESOLVED,)
                context.unresolved_value(path, UnresolvedKind.MISSING, reason, blocking=blocking_when_missing)
        _validate_qualified(value, path)
        context.record(path, classifications, (source_path,), "explicit_qualified_value_v1", raw, value)
        return value
    normalized = _canonical_text(raw)
    if not normalized:
        raise CaseInputError(f"{path}: text cannot be empty")
    value = _known(normalized)
    classifications = (ProvenanceClassification.USER_SUPPLIED,)
    if normalized != raw:
        classifications += (ProvenanceClassification.NORMALIZED,)
    context.record(path, classifications, (source_path,), "unicode_nfkc_whitespace_v1", raw, value)
    return value


def _qualified_string_list(
    raw: Any,
    path: str,
    source_path: str,
    context: _BuildContext,
    missing_reason: str,
) -> QualifiedValue[tuple[str, ...]]:
    if _is_missing(raw, path, source_path):
        value = _unknown(missing_reason)
        context.record(
            path,
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            missing_reason,
        )
        context.unresolved_value(path, UnresolvedKind.MISSING, missing_reason)
        return value
    if isinstance(raw, Mapping) and "status" in raw:
        unknown = set(raw) - {"status", "values", "value", "reason"}
        if unknown:
            raise CaseInputError(f"{path}: unknown qualified-list fields {sorted(unknown)}")
        status_token = _canonical_token(raw.get("status"))
        reason = _canonical_text(raw.get("reason", ""))
        if status_token == ValueStatus.UNKNOWN.value:
            if raw.get("values", raw.get("value")) is not None:
                raise CaseInputError(f"{path}: unknown must not include values")
            value = _unknown(reason)
            context.unresolved_value(path, UnresolvedKind.MISSING, reason)
            classifications = (
                ProvenanceClassification.USER_SUPPLIED,
                ProvenanceClassification.UNRESOLVED,
            )
        elif status_token == ValueStatus.NOT_APPLICABLE.value:
            if raw.get("values", raw.get("value")) is not None:
                raise CaseInputError(f"{path}: not_applicable must not include values")
            value = _not_applicable(reason)
            classifications = (ProvenanceClassification.USER_SUPPLIED,)
        elif status_token == ValueStatus.KNOWN.value:
            if reason:
                raise CaseInputError(f"{path}: known value must not include a reason")
            raw = raw.get("values", raw.get("value", _MISSING))
            if raw is _MISSING:
                raise CaseInputError(f"{path}: known list requires values")
            return _qualified_string_list(raw, path, source_path, context, missing_reason)
        else:
            raise CaseInputError(f"{path}: invalid value status")
        _validate_qualified(value, path)
        context.record(path, classifications, (source_path,), "explicit_qualified_value_v1", raw, value)
        return value
    if not isinstance(raw, (list, tuple)) or isinstance(raw, (str, bytes)):
        raise CaseInputError(f"{path}: expected a list of strings")
    original = list(raw)
    normalized_values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        normalized = _canonical_text(item)
        if not normalized:
            raise CaseInputError(f"{path}: list values cannot be empty")
        marker = normalized.casefold()
        if marker not in seen:
            seen.add(marker)
            normalized_values.append(normalized)
    normalized_values.sort(key=lambda item: (item.casefold(), item))
    value = _known(tuple(normalized_values))
    classifications = (ProvenanceClassification.USER_SUPPLIED,)
    if original != normalized_values:
        classifications += (ProvenanceClassification.NORMALIZED,)
    context.record(path, classifications, (source_path,), "normalized_unique_sorted_list_v1", original, value)
    return value


def _qualified_enum(
    raw: Any,
    enum_type: type[T],
    path: str,
    source_path: str,
    context: _BuildContext,
    missing_reason: str,
) -> QualifiedValue[T]:
    original_raw = raw
    if _is_missing(raw, path, source_path):
        value = _unknown(missing_reason)
        context.record(
            path,
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            missing_reason,
        )
        context.unresolved_value(path, UnresolvedKind.MISSING, missing_reason)
        return value
    if isinstance(raw, Mapping) and "status" in raw:
        unknown = set(raw) - {"status", "value", "reason"}
        if unknown:
            raise CaseInputError(f"{path}: unknown qualified-value fields {sorted(unknown)}")
        status = _canonical_token(raw.get("status"))
        if status != ValueStatus.KNOWN.value:
            return _qualified_text(raw, path, source_path, context, missing_reason)  # type: ignore[return-value]
        if _canonical_text(raw.get("reason", "")):
            raise CaseInputError(f"{path}: known value must not include a reason")
        raw = raw.get("value", _MISSING)
    if raw is _MISSING:
        raise CaseInputError(f"{path}: known enum requires a value")
    token = _canonical_token(raw)
    aliases = {
        "lof": "loss_of_function",
        "gof": "gain_of_function",
        "increase_benefit": "increase_is_benefit",
        "decrease_benefit": "decrease_is_benefit",
        "avoid_event": "event_avoidance",
    }
    token = aliases.get(token, token)
    try:
        parsed = enum_type(token)  # type: ignore[call-arg]
    except ValueError as exc:
        allowed = [member.value for member in enum_type]  # type: ignore[attr-defined]
        raise CaseInputError(f"{path}: expected one of {allowed}, found {raw!r}") from exc
    value = _known(parsed)
    classifications = (ProvenanceClassification.USER_SUPPLIED,)
    if token != raw:
        classifications += (ProvenanceClassification.NORMALIZED,)
    context.record(
        path,
        classifications,
        (source_path,),
        "controlled_value_normalization_v1",
        original_raw,
        value,
    )
    return value


def _qualified_bool(
    raw: Any,
    path: str,
    source_path: str,
    context: _BuildContext,
    missing_reason: str,
) -> QualifiedValue[bool]:
    original_raw = raw
    if _is_missing(raw, path, source_path):
        value = _unknown(missing_reason)
        context.record(
            path,
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            missing_reason,
        )
        context.unresolved_value(path, UnresolvedKind.MISSING, missing_reason)
        return value
    if isinstance(raw, Mapping) and "status" in raw:
        unknown = set(raw) - {"status", "value", "reason"}
        if unknown:
            raise CaseInputError(f"{path}: unknown qualified-value fields {sorted(unknown)}")
        status = _canonical_token(raw.get("status"))
        reason = _canonical_text(raw.get("reason", ""))
        if status == ValueStatus.UNKNOWN.value:
            if raw.get("value") is not None:
                raise CaseInputError(f"{path}: unknown must not include a value")
            value = _unknown(reason)
            context.unresolved_value(path, UnresolvedKind.MISSING, reason)
        elif status == ValueStatus.NOT_APPLICABLE.value:
            if raw.get("value") is not None:
                raise CaseInputError(f"{path}: not_applicable must not include a value")
            value = _not_applicable(reason)
        elif status == ValueStatus.KNOWN.value:
            if reason:
                raise CaseInputError(f"{path}: known value must not include a reason")
            raw = raw.get("value", _MISSING)
            if not isinstance(raw, bool):
                raise CaseInputError(f"{path}: known required status must be boolean")
            value = _known(raw)
        else:
            raise CaseInputError(f"{path}: invalid value status")
        _validate_qualified(value, path)
        classifications = (ProvenanceClassification.USER_SUPPLIED,)
        if value.status is ValueStatus.UNKNOWN:
            classifications += (ProvenanceClassification.UNRESOLVED,)
        context.record(
            path,
            classifications,
            (source_path,),
            "explicit_qualified_value_v1",
            original_raw,
            value,
        )
        return value
    if not isinstance(raw, bool):
        raise CaseInputError(f"{path}: required status must be boolean")
    value = _known(raw)
    context.record(
        path,
        (ProvenanceClassification.USER_SUPPLIED,),
        (source_path,),
        "boolean_identity_v1",
        original_raw,
        value,
    )
    return value


_NAMESPACE_ALIASES = {
    "hgnc": "HGNC",
    "hgnc_symbol": "HGNC_SYMBOL",
    "ensembl": "ENSEMBL",
    "ensembl_gene": "ENSEMBL",
    "ncbigene": "NCBIGENE",
    "entrez": "NCBIGENE",
    "entrezgene": "NCBIGENE",
    "mondo": "MONDO",
    "doid": "DOID",
    "orpha": "ORPHA",
    "orphanet": "ORPHA",
    "omim": "OMIM",
    "icd10": "ICD10",
    "icd_10": "ICD10",
    "icd10cm": "ICD10CM",
    "icd_10_cm": "ICD10CM",
    "hp": "HP",
    "hpo": "HP",
    "loinc": "LOINC",
    "snomed": "SNOMEDCT",
    "snomedct": "SNOMEDCT",
    "snomed_ct": "SNOMEDCT",
    "sctid": "SNOMEDCT",
    "ncit": "NCIT",
    "nci_thesaurus": "NCIT",
    "umls": "UMLS",
}


def _normalize_namespace(value: Any, path: str, *, allow_custom: bool = False) -> str:
    token = _canonical_token(value)
    namespace = _NAMESPACE_ALIASES.get(token)
    if namespace is None and allow_custom:
        candidate = _canonical_text(value).upper().replace(" ", "_")
        if re.fullmatch(r"[A-Z][A-Z0-9_.-]{1,31}", candidate):
            namespace = candidate
    if not namespace:
        raise CaseInputError(f"{path}: unsupported identifier namespace {value!r}")
    return namespace


def _split_prefixed_identifier(value: str, kind: ConceptKind) -> tuple[str, str] | None:
    token = _canonical_text(value)
    upper = token.upper()
    allowed = {
        ConceptKind.GENE: {"HGNC", "NCBIGENE", "ENTREZ", "ENSEMBL"},
        ConceptKind.DISEASE: {"MONDO", "DOID", "ORPHA", "ORPHANET", "OMIM", "ICD10", "ICD10CM"},
        ConceptKind.PHENOTYPE: {"HP", "HPO"},
        ConceptKind.ENDPOINT_CONSTRUCT: {
            "MONDO", "DOID", "ORPHA", "OMIM", "ICD10", "ICD10CM", "HP", "HPO",
            "LOINC", "SNOMED", "SNOMEDCT", "SCTID", "NCIT", "UMLS",
        },
    }[kind]
    if re.fullmatch(r"ENSG\d{11}", upper) and kind is ConceptKind.GENE:
        return "ENSEMBL", upper
    if ":" not in upper:
        return None
    prefix, identifier = upper.split(":", 1)
    if prefix not in allowed or not identifier:
        return None
    namespace = _normalize_namespace(prefix, "identifier")
    if namespace in {"HGNC", "NCBIGENE", "MONDO", "DOID", "ORPHA", "OMIM", "HP"}:
        if not identifier.isdigit():
            return None
        widths = {"MONDO": 7, "HP": 7}
        if namespace in widths:
            identifier = identifier.zfill(widths[namespace])
    elif namespace == "ENSEMBL" and not re.fullmatch(r"ENSG\d{11}", identifier):
        return None
    elif namespace in {"ICD10", "ICD10CM"} and not re.fullmatch(r"[A-Z][A-Z0-9.]{1,10}", identifier):
        return None
    elif namespace == "LOINC" and not re.fullmatch(r"\d{1,7}-\d", identifier):
        return None
    elif namespace == "SNOMEDCT" and not re.fullmatch(r"\d{6,18}", identifier):
        return None
    elif namespace == "NCIT" and not re.fullmatch(r"C\d+", identifier):
        return None
    elif namespace == "UMLS" and not re.fullmatch(r"C\d{7}", identifier):
        return None
    return namespace, identifier


def _coded_identifier(
    raw: Any,
    kind: ConceptKind,
    path: str,
    source_path: str,
    context: _BuildContext,
    *,
    namespace_hint: Any = _MISSING,
    version_raw: Any = _MISSING,
) -> CodedIdentifier:
    original = raw
    if isinstance(raw, Mapping):
        unknown = set(raw) - {"namespace", "identifier", "ontology_version", "version"}
        if unknown:
            raise CaseInputError(f"{path}: unknown identifier fields {sorted(unknown)}")
        namespace_hint = raw.get("namespace", namespace_hint)
        version_raw = raw.get("ontology_version", raw.get("version", version_raw))
        raw = raw.get("identifier", _MISSING)
    if raw is _MISSING:
        raise CaseInputError(f"{path}: identifier is missing")
    token = _canonical_text(raw)
    split = _split_prefixed_identifier(token, kind)
    if namespace_hint is not _MISSING:
        namespace = _normalize_namespace(
            namespace_hint,
            f"{path}/namespace",
            allow_custom=kind is ConceptKind.ENDPOINT_CONSTRUCT,
        )
        identifier = token.upper()
        if ":" in identifier:
            supplied_prefix, identifier = identifier.split(":", 1)
            if _normalize_namespace(
                supplied_prefix,
                path,
                allow_custom=kind is ConceptKind.ENDPOINT_CONSTRUCT,
            ) != namespace:
                raise CaseInputError(f"{path}: namespace conflicts with identifier prefix")
        normalized_split = _split_prefixed_identifier(f"{namespace}:{identifier}", kind)
        custom_endpoint_identifier = (
            kind is ConceptKind.ENDPOINT_CONSTRUCT
            and namespace not in {
                "MONDO", "DOID", "ORPHA", "OMIM", "ICD10", "ICD10CM", "HP",
                "LOINC", "SNOMEDCT", "NCIT", "UMLS",
            }
            and re.fullmatch(r"[A-Z0-9][A-Z0-9._:/-]{0,127}", identifier) is not None
        )
        if normalized_split is None and not custom_endpoint_identifier and not (
            kind is ConceptKind.GENE
            and namespace == "HGNC_SYMBOL"
            and _GENE_SYMBOL_PATTERN.fullmatch(identifier)
        ):
            raise CaseInputError(f"{path}: malformed {namespace} identifier {identifier!r}")
        if normalized_split:
            namespace, identifier = normalized_split
    elif split:
        namespace, identifier = split
    elif kind is ConceptKind.GENE and _GENE_SYMBOL_PATTERN.fullmatch(token):
        namespace, identifier = "HGNC_SYMBOL", token.upper()
    else:
        raise CaseInputError(f"{path}: identifier requires a supported namespace or prefix")
    ontology_version = _qualified_text(
        version_raw,
        f"{path}/ontology_version",
        f"{source_path}/ontology_version" if source_path else "",
        context,
        "Identifier-system or ontology version was not supplied.",
    )
    result = CodedIdentifier(namespace=namespace, identifier=identifier, ontology_version=ontology_version)
    classifications = (ProvenanceClassification.USER_SUPPLIED,)
    if _plain(original) != _plain(result):
        classifications += (ProvenanceClassification.NORMALIZED,)
    context.record(
        path,
        classifications,
        (source_path,),
        "lexical_identifier_normalization_v1",
        original,
        result,
        "Lexical normalization only; no external ontology lookup was performed.",
    )
    return result


def _concept_value(
    raw: Any,
    kind: ConceptKind,
    path: str,
    source_path: str,
    context: _BuildContext,
    *,
    blocking_ambiguity: bool,
) -> QualifiedValue[MappedConcept]:
    original_raw = raw
    if _is_missing(raw, path, source_path):
        reason = f"No {kind.value.replace('_', ' ')} input was supplied."
        value = _unknown(reason)
        context.record(
            path,
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            reason,
        )
        context.unresolved_value(path, UnresolvedKind.MISSING, reason)
        return value
    if isinstance(raw, Mapping) and "status" in raw:
        unknown = set(raw) - {"status", "value", "reason"}
        if unknown:
            raise CaseInputError(f"{path}: unknown qualified-value fields {sorted(unknown)}")
        status = _canonical_token(raw.get("status"))
        if status == ValueStatus.KNOWN.value:
            if _canonical_text(raw.get("reason", "")):
                raise CaseInputError(f"{path}: known concept must not include a reason")
            raw = raw.get("value", _MISSING)
            if raw is _MISSING:
                raise CaseInputError(f"{path}: known concept requires a value")
        else:
            if status not in {ValueStatus.UNKNOWN.value, ValueStatus.NOT_APPLICABLE.value}:
                raise CaseInputError(f"{path}: invalid value status")
            if raw.get("value") is not None:
                raise CaseInputError(f"{path}: {status} concept must not include a value")
            reason = _canonical_text(raw.get("reason", ""))
            value = _unknown(reason) if status == ValueStatus.UNKNOWN.value else _not_applicable(reason)
            _validate_qualified(value, path)
            classifications = (ProvenanceClassification.USER_SUPPLIED,)
            if value.status is ValueStatus.UNKNOWN:
                classifications += (ProvenanceClassification.UNRESOLVED,)
                context.unresolved_value(path, UnresolvedKind.MISSING, reason)
            context.record(path, classifications, (source_path,), "explicit_qualified_value_v1", raw, value)
            return value

    label_raw: Any = _MISSING
    identifier_raw: Any = _MISSING
    namespace_raw: Any = _MISSING
    version_raw: Any = _MISSING
    candidates_raw: Any = _MISSING
    raw_input = (
        original_raw
        if isinstance(original_raw, str)
        else json.dumps(_plain(original_raw), ensure_ascii=True, sort_keys=True)
    )
    if isinstance(raw, Mapping):
        allowed = {
            "label", "identifier", "namespace", "ontology_version", "version",
            "candidates", "candidate_identifiers",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise CaseInputError(f"{path}: unknown concept fields {sorted(unknown)}")
        label_raw = raw.get("label", _MISSING)
        identifier_raw = raw.get("identifier", _MISSING)
        namespace_raw = raw.get("namespace", _MISSING)
        version_raw = raw.get("ontology_version", raw.get("version", _MISSING))
        candidates_raw, _ = _get_alias(raw, ("candidates", "candidate_identifiers"), f"{path}/candidates")
    elif isinstance(raw, str):
        if _split_prefixed_identifier(raw, kind) or (
            kind is ConceptKind.GENE and _GENE_SYMBOL_PATTERN.fullmatch(_canonical_text(raw))
        ):
            identifier_raw = raw
            if kind is ConceptKind.GENE and not _split_prefixed_identifier(raw, kind):
                label_raw = _canonical_text(raw).upper()
        else:
            label_raw = raw
    else:
        raise CaseInputError(f"{path}: expected text or a concept object")

    label = _qualified_text(
        label_raw,
        f"{path}/label",
        f"{source_path}/label" if isinstance(raw, Mapping) else source_path,
        context,
        f"No display label was supplied for the {kind.value.replace('_', ' ')} identifier.",
    )
    candidates: list[CodedIdentifier] = []
    if candidates_raw is not _MISSING:
        if not isinstance(candidates_raw, (list, tuple)) or isinstance(candidates_raw, (str, bytes)):
            raise CaseInputError(f"{path}/mapping_candidates: expected a list")
        for index, candidate in enumerate(candidates_raw):
            candidates.append(
                _coded_identifier(
                    candidate,
                    kind,
                    f"{path}/mapping_candidates/{index}",
                    f"{source_path}/candidates/{index}",
                    context,
                )
            )
    candidate_keys = {(row.namespace, row.identifier) for row in candidates}
    if len(candidate_keys) != len(candidates):
        raise CaseInputError(f"{path}: duplicate mapping candidates")

    if identifier_raw is not _MISSING:
        selected = _coded_identifier(
            identifier_raw,
            kind,
            f"{path}/coding",
            f"{source_path}/identifier" if isinstance(raw, Mapping) else source_path,
            context,
            namespace_hint=namespace_raw,
            version_raw=version_raw,
        )
        if candidates and (selected.namespace, selected.identifier) not in candidate_keys:
            raise CaseInputError(f"{path}: selected identifier conflicts with mapping candidates")
        coding = _known(selected)
        rule_id = "user_identifier_lexical_normalization_v1"
    else:
        if namespace_raw is not _MISSING or version_raw is not _MISSING:
            raise CaseInputError(f"{path}: namespace/version supplied without an identifier")
        reason = (
            "Multiple candidate ontology mappings were supplied without a selected identifier."
            if len(candidates) > 1
            else "No authoritative ontology identifier was supplied; free text was not silently mapped."
        )
        coding = _unknown(reason)
        kind_value = UnresolvedKind.AMBIGUOUS if candidates else UnresolvedKind.MISSING
        candidate_labels = tuple(f"{row.namespace}:{row.identifier}" for row in candidates)
        context.unresolved_value(
            f"{path}/coding",
            kind_value,
            reason,
            candidates=candidate_labels,
            blocking=blocking_ambiguity and len(candidates) > 1,
        )
        context.record(
            f"{path}/coding",
            (
                ProvenanceClassification.USER_SUPPLIED,
                ProvenanceClassification.UNRESOLVED,
            ),
            (source_path,),
            "free_text_mapping_not_inferred_v1",
            raw,
            coding,
            reason,
        )
        rule_id = "ambiguous_mapping_unresolved_v1" if candidates else "label_only_unresolved_v1"
    concept = MappedConcept(
        concept_kind=kind,
        raw_input=str(raw_input),
        label=label,
        coding=coding,
        mapping_candidates=tuple(candidates),
        mapping_rule_id=rule_id,
    )
    context.record(
        path,
        (ProvenanceClassification.USER_SUPPLIED,),
        (source_path,),
        rule_id,
        original_raw,
        concept,
    )
    return _known(concept)


def _gene_context(
    raw: Any,
    source_path: str,
    context: _BuildContext,
) -> QualifiedValue[GeneContext]:
    if _is_missing(raw, "/gene", source_path):
        reason = "No human gene input was supplied."
        value = _unknown(reason)
        context.record(
            "/gene",
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            reason,
        )
        context.unresolved_value("/gene", UnresolvedKind.MISSING, reason)
        return value
    disease_direction_raw = _MISSING
    therapeutic_direction_raw = _MISSING
    concept_raw = raw
    if isinstance(raw, Mapping) and "status" not in raw:
        allowed = {
            "label", "identifier", "namespace", "ontology_version", "version",
            "candidates", "candidate_identifiers", "disease_associated_state",
            "disease_direction", "desired_therapeutic_modulation", "therapeutic_direction",
            "intervention_direction",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise CaseInputError(f"/gene: unknown fields {sorted(unknown)}")
        disease_direction_raw, disease_source = _get_alias(
            raw, ("disease_associated_state", "disease_direction"), "/gene/disease_associated_state"
        )
        therapeutic_direction_raw, therapeutic_source = _get_alias(
            raw,
            ("desired_therapeutic_modulation", "therapeutic_direction", "intervention_direction"),
            "/gene/desired_therapeutic_modulation",
        )
        concept_raw = {key: value for key, value in raw.items() if key in {
            "label", "identifier", "namespace", "ontology_version", "version",
            "candidates", "candidate_identifiers",
        }}
        disease_source_path = f"{source_path}/{disease_source}" if disease_source else ""
        therapeutic_source_path = f"{source_path}/{therapeutic_source}" if therapeutic_source else ""
    else:
        disease_source_path = ""
        therapeutic_source_path = ""
    concept_value = _concept_value(
        concept_raw, ConceptKind.GENE, "/gene/concept", source_path, context, blocking_ambiguity=True
    )
    if concept_value.status is not ValueStatus.KNOWN or concept_value.value is None:
        reason = concept_value.reason or "Gene concept is unresolved."
        value = QualifiedValue(status=concept_value.status, value=None, reason=reason)
        classifications = (ProvenanceClassification.USER_SUPPLIED,)
        if concept_value.status is ValueStatus.UNKNOWN:
            classifications += (ProvenanceClassification.UNRESOLVED,)
        context.record(
            "/gene",
            classifications,
            (source_path,),
            "unresolved_gene_concept_v1",
            raw,
            value,
        )
        return value
    disease_state = _qualified_enum(
        disease_direction_raw,
        GeneDiseaseState,
        "/gene/disease_associated_state",
        disease_source_path,
        context,
        "Disease-associated gene direction/state was not supplied; no inverse was assumed.",
    )
    therapeutic_modulation = _qualified_enum(
        therapeutic_direction_raw,
        TherapeuticModulation,
        "/gene/desired_therapeutic_modulation",
        therapeutic_source_path,
        context,
        "Desired therapeutic modulation was not supplied; it was not inferred from disease direction.",
    )
    value = _known(
        GeneContext(
            concept=concept_value.value,
            disease_associated_state=disease_state,
            desired_therapeutic_modulation=therapeutic_modulation,
        )
    )
    context.record(
        "/gene",
        (ProvenanceClassification.USER_SUPPLIED,),
        (source_path,),
        "gene_context_v1",
        raw,
        value,
    )
    return value


def _population_context(raw: Any, source_path: str, context: _BuildContext) -> PopulationContext:
    if _is_missing(raw, "/population", source_path):
        raw = {}
    if isinstance(raw, str):
        raw = {"description": raw}
    if not isinstance(raw, Mapping):
        raise CaseInputError("/population: expected text or an object")
    if "status" in raw:
        unknown = set(raw) - {"status", "reason"}
        if unknown:
            raise CaseInputError(f"/population: unknown fields {sorted(unknown)}")
        qualified = {"status": raw.get("status"), "reason": raw.get("reason", "")}
        raw = {
            "description": qualified,
            "inclusion": qualified,
            "exclusion": qualified,
            "genotypes": qualified,
        }
    allowed = {"description", "inclusion", "exclusion", "genotypes", "genotype"}
    unknown = set(raw) - allowed
    if unknown:
        raise CaseInputError(f"/population: unknown fields {sorted(unknown)}")
    genotype_raw, genotype_name = _get_alias(raw, ("genotypes", "genotype"), "/population/genotypes")
    description = _qualified_text(
        raw.get("description", _MISSING),
        "/population/description",
        f"{source_path}/description" if source_path else "",
        context,
        "Population description was not supplied.",
    )
    inclusion = _qualified_string_list(
        raw.get("inclusion", _MISSING),
        "/population/inclusion",
        f"{source_path}/inclusion" if source_path else "",
        context,
        "Population inclusion criteria were not supplied.",
    )
    exclusion = _qualified_string_list(
        raw.get("exclusion", _MISSING),
        "/population/exclusion",
        f"{source_path}/exclusion" if source_path else "",
        context,
        "Population exclusion criteria were not supplied.",
    )
    genotypes = _qualified_string_list(
        genotype_raw,
        "/population/genotypes",
        f"{source_path}/{genotype_name}" if source_path and genotype_name else "",
        context,
        "Relevant population genotype criteria were not supplied.",
    )
    if inclusion.status is ValueStatus.KNOWN and exclusion.status is ValueStatus.KNOWN:
        overlap = {
            item.casefold(): item for item in inclusion.value or ()
        }.keys() & {item.casefold(): item for item in exclusion.value or ()}.keys()
        if overlap:
            raise CaseInputError(
                f"/population: criteria cannot be both included and excluded: {sorted(overlap)}"
            )
    return PopulationContext(
        description=description,
        inclusion=inclusion,
        exclusion=exclusion,
        genotypes=genotypes,
    )


def _tissue_context(raw: Any, source_path: str, context: _BuildContext) -> TissueContext:
    if _is_missing(raw, "/tissue", source_path):
        raw = {}
    if isinstance(raw, str):
        raw = {"target": raw}
    if not isinstance(raw, Mapping):
        raise CaseInputError("/tissue: expected text or an object")
    allowed = {"target", "relevance"}
    unknown = set(raw) - allowed
    if unknown:
        raise CaseInputError(f"/tissue: unknown fields {sorted(unknown)}")
    return TissueContext(
        target=_qualified_text(
            raw.get("target", _MISSING),
            "/tissue/target",
            f"{source_path}/target" if source_path else "",
            context,
            "Target tissue or system was not supplied.",
        ),
        relevance=_qualified_text(
            raw.get("relevance", _MISSING),
            "/tissue/relevance",
            f"{source_path}/relevance" if source_path else "",
            context,
            "Target tissue or system relevance was not supplied.",
        ),
    )


def _stage_context(raw: Any, source_path: str, context: _BuildContext) -> StageContext:
    if _is_missing(raw, "/disease_stage", source_path):
        raw = {}
    if isinstance(raw, str):
        raw = {"stage": raw}
    if not isinstance(raw, Mapping):
        raise CaseInputError("/disease_stage: expected text or an object")
    allowed = {"stage", "severity"}
    unknown = set(raw) - allowed
    if unknown:
        raise CaseInputError(f"/disease_stage: unknown fields {sorted(unknown)}")
    return StageContext(
        stage=_qualified_text(
            raw.get("stage", _MISSING),
            "/disease_stage/stage",
            f"{source_path}/stage" if source_path else "",
            context,
            "Disease stage was not supplied.",
        ),
        severity=_qualified_text(
            raw.get("severity", _MISSING),
            "/disease_stage/severity",
            f"{source_path}/severity" if source_path else "",
            context,
            "Disease severity was not supplied.",
        ),
    )


def _target_product_profile(
    raw: Any,
    source_path: str,
    context: _BuildContext,
    top_level_time_horizon: Any,
    top_level_time_source: str,
) -> TargetProductProfile:
    if _is_missing(raw, "/target_product_profile", source_path):
        raw = {}
    if not isinstance(raw, Mapping):
        raise CaseInputError("/target_product_profile: expected an object")
    allowed = {
        "intended_benefit", "setting", "allowed_routes", "excluded_routes",
        "route_constraints", "regimen_constraints", "exposure_constraints",
        "time_horizon", "acceptable_risk", "acceptable_risk_constraints",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise CaseInputError(f"/target_product_profile: unknown fields {sorted(unknown)}")
    allowed_routes_raw, allowed_routes_name = _get_alias(
        raw, ("allowed_routes", "route_constraints"), "/target_product_profile/allowed_routes"
    )
    risk_raw, risk_name = _get_alias(
        raw,
        ("acceptable_risk", "acceptable_risk_constraints"),
        "/target_product_profile/acceptable_risk",
    )
    profile_time = raw.get("time_horizon", _MISSING)
    if profile_time is not _MISSING and top_level_time_horizon is not _MISSING:
        if canonical_bytes(profile_time) != canonical_bytes(top_level_time_horizon):
            raise CaseInputError(
                "/target_product_profile/time_horizon: conflicts with top-level time_horizon"
            )
    time_raw = profile_time if profile_time is not _MISSING else top_level_time_horizon
    time_source = (
        f"{source_path}/time_horizon" if profile_time is not _MISSING else top_level_time_source
    )
    allowed_routes = _qualified_string_list(
        allowed_routes_raw,
        "/target_product_profile/allowed_routes",
        f"{source_path}/{allowed_routes_name}" if source_path and allowed_routes_name else "",
        context,
        "Allowed administration routes were not supplied.",
    )
    excluded_routes = _qualified_string_list(
        raw.get("excluded_routes", _MISSING),
        "/target_product_profile/excluded_routes",
        f"{source_path}/excluded_routes" if source_path else "",
        context,
        "Excluded administration routes were not supplied.",
    )
    if allowed_routes.status is ValueStatus.KNOWN and excluded_routes.status is ValueStatus.KNOWN:
        allowed_markers = {item.casefold() for item in allowed_routes.value or ()}
        excluded_markers = {item.casefold() for item in excluded_routes.value or ()}
        overlap = sorted(allowed_markers & excluded_markers)
        if overlap:
            raise CaseInputError(
                "/target_product_profile: routes cannot be both allowed and excluded: " + ", ".join(overlap)
            )
    return TargetProductProfile(
        intended_benefit=_qualified_text(
            raw.get("intended_benefit", _MISSING),
            "/target_product_profile/intended_benefit",
            f"{source_path}/intended_benefit" if source_path else "",
            context,
            "Intended target-product benefit was not supplied.",
        ),
        setting=_qualified_text(
            raw.get("setting", _MISSING),
            "/target_product_profile/setting",
            f"{source_path}/setting" if source_path else "",
            context,
            "Intended treatment setting was not supplied.",
        ),
        allowed_routes=allowed_routes,
        excluded_routes=excluded_routes,
        regimen_constraints=_qualified_string_list(
            raw.get("regimen_constraints", _MISSING),
            "/target_product_profile/regimen_constraints",
            f"{source_path}/regimen_constraints" if source_path else "",
            context,
            "Regimen constraints were not supplied.",
        ),
        exposure_constraints=_qualified_string_list(
            raw.get("exposure_constraints", _MISSING),
            "/target_product_profile/exposure_constraints",
            f"{source_path}/exposure_constraints" if source_path else "",
            context,
            "Exposure constraints were not supplied.",
        ),
        time_horizon=_qualified_text(
            time_raw,
            "/target_product_profile/time_horizon",
            time_source,
            context,
            "Target-product time horizon was not supplied.",
        ),
        acceptable_risk=_qualified_text(
            risk_raw,
            "/target_product_profile/acceptable_risk",
            f"{source_path}/{risk_name}" if source_path and risk_name else "",
            context,
            "Acceptable-risk constraints were not supplied.",
        ),
    )


def _concept_reference(value: QualifiedValue[MappedConcept]) -> str | None:
    if value.status is not ValueStatus.KNOWN or value.value is None:
        return None
    concept = value.value
    if concept.coding.status is ValueStatus.KNOWN and concept.coding.value is not None:
        return f"{concept.coding.value.namespace}:{concept.coding.value.identifier}"
    if concept.label.status is ValueStatus.KNOWN:
        return concept.label.value
    return None


def _inherited_or_unknown_text(
    raw: Any,
    path: str,
    source_path: str,
    context: _BuildContext,
    missing_reason: str,
    inherited: QualifiedValue[str] | str | None,
    inherited_path: str,
    rule_id: str,
) -> QualifiedValue[str]:
    if raw is not _MISSING:
        return _qualified_text(raw, path, source_path, context, missing_reason)
    inherited_value = inherited.value if isinstance(inherited, QualifiedValue) else inherited
    inherited_known = (
        inherited.status is ValueStatus.KNOWN if isinstance(inherited, QualifiedValue) else inherited is not None
    )
    if inherited_known and inherited_value:
        value = _known(str(inherited_value))
        context.record(
            path,
            (ProvenanceClassification.INFERRED,),
            (inherited_path,),
            rule_id,
            None,
            value,
            f"Inherited explicitly from {inherited_path}.",
        )
        return value
    return _qualified_text(_MISSING, path, source_path, context, missing_reason)


def _endpoint_id(stable_identity: str) -> str:
    canonical_identity = _canonical_text(stable_identity).casefold()
    digest = hashlib.sha256(
        f"{ENDPOINT_ID_RULE}|{canonical_identity}".encode("utf-8")
    ).hexdigest().upper()
    return f"EP-{digest[:20]}"


def _explicit_endpoint_id(raw: Any, path: str, context: _BuildContext) -> str:
    normalized = _canonical_text(raw).upper()
    if not _ENDPOINT_ID_PATTERN.fullmatch(normalized):
        raise CaseInputError(f"{path}: endpoint_id must match {_ENDPOINT_ID_PATTERN.pattern}")
    classifications = (ProvenanceClassification.USER_SUPPLIED,)
    if normalized != raw:
        classifications += (ProvenanceClassification.NORMALIZED,)
    context.record(path, classifications, (path,), "explicit_endpoint_id_v1", raw, normalized)
    return normalized


def _endpoint_stable_key(
    raw: Any,
    construct: QualifiedValue[MappedConcept],
    index: int,
    source_path: str,
    context: _BuildContext,
) -> QualifiedValue[str]:
    path = f"/endpoints/{index}/stable_key"
    if raw is not _MISSING:
        return _qualified_text(raw, path, f"{source_path}/stable_key", context, "Endpoint stable key is missing.")
    reference: str | None = None
    if (
        construct.status is ValueStatus.KNOWN
        and construct.value is not None
        and construct.value.coding.status is ValueStatus.KNOWN
        and construct.value.coding.value is not None
    ):
        code = construct.value.coding.value
        reference = f"{code.namespace}:{code.identifier}"
    if reference:
        value = _known(f"construct:{reference}")
        rule = "endpoint_stable_key_from_construct_v1"
        source = f"/endpoints/{index}/construct/coding"
        classifications = (ProvenanceClassification.INFERRED,)
    else:
        value = _known(f"unresolved-input-slot:{index + 1}")
        rule = "unresolved_endpoint_identity_slot_v1"
        source = source_path
        classifications = (
            ProvenanceClassification.INFERRED,
            ProvenanceClassification.UNRESOLVED,
        )
        reason = "Endpoint stable identity requires an explicit stable_key or coded construct."
        context.unresolved_value(path, UnresolvedKind.MISSING, reason, blocking=True)
    context.record(
        path,
        classifications,
        (source,),
        rule,
        None,
        value,
        "Stable machine identity excludes the display label.",
    )
    return value


@dataclass(frozen=True)
class _EndpointDraft:
    endpoint: Endpoint
    raw_relationships: Any
    source_path: str


def _endpoint_draft(
    raw: Mapping[str, Any],
    index: int,
    source_path: str,
    context: _BuildContext,
    disease: QualifiedValue[MappedConcept],
    population: PopulationContext,
    stage: StageContext,
    profile: TargetProductProfile,
) -> _EndpointDraft:
    allowed = {
        "endpoint_id", "stable_key", "display_label", "label", "construct", "role",
        "endpoint_type", "type", "population", "disease_stage", "stage", "timeframe",
        "time_horizon", "measurement", "measure", "disease_context", "direction",
        "priority", "required", "relationships",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise CaseInputError(f"/endpoints/{index}: unknown fields {sorted(unknown)}")
    display_raw, display_name = _get_alias(raw, ("display_label", "label"), f"/endpoints/{index}/display_label")
    type_raw, type_name = _get_alias(raw, ("endpoint_type", "type"), f"/endpoints/{index}/endpoint_type")
    stage_raw, stage_name = _get_alias(raw, ("disease_stage", "stage"), f"/endpoints/{index}/disease_stage")
    timeframe_raw, timeframe_name = _get_alias(raw, ("timeframe", "time_horizon"), f"/endpoints/{index}/timeframe")
    measurement_raw, measurement_name = _get_alias(raw, ("measurement", "measure"), f"/endpoints/{index}/measurement")
    construct = _concept_value(
        raw.get("construct", _MISSING),
        ConceptKind.ENDPOINT_CONSTRUCT,
        f"/endpoints/{index}/construct",
        f"{source_path}/construct",
        context,
        blocking_ambiguity=True,
    )
    if not (
        construct.status is ValueStatus.KNOWN
        and construct.value is not None
        and construct.value.coding.status is ValueStatus.KNOWN
        and construct.value.coding.value is not None
    ):
        reason = "Endpoint construct requires one selected coded identifier before Stage 1 can pass."
        context.unresolved_value(
            f"/endpoints/{index}/construct/coding",
            UnresolvedKind.MISSING,
            reason,
            blocking=True,
        )
    stable_key = _endpoint_stable_key(
        raw.get("stable_key", _MISSING), construct, index, source_path, context
    )
    if stable_key.value is None:
        raise AssertionError("Endpoint stable keys are always known after normalization")
    explicit_id = raw.get("endpoint_id", _MISSING)
    if explicit_id is _MISSING:
        endpoint_id = _endpoint_id(stable_key.value)
        context.record(
            f"/endpoints/{index}/endpoint_id",
            (ProvenanceClassification.INFERRED,),
            (f"/endpoints/{index}/stable_key",),
            ENDPOINT_ID_RULE,
            None,
            endpoint_id,
            "Content-derived machine ID; display label excluded.",
        )
    else:
        endpoint_id = _explicit_endpoint_id(explicit_id, f"/endpoints/{index}/endpoint_id", context)
    display_label = _qualified_text(
        display_raw,
        f"/endpoints/{index}/display_label",
        f"{source_path}/{display_name}" if display_name else "",
        context,
        "Endpoint display label was not supplied.",
    )
    if display_label.status is ValueStatus.KNOWN and str(display_label.value).casefold() == endpoint_id.casefold():
        raise CaseInputError(
            f"/endpoints/{index}: endpoint_id must be distinct from the display label"
        )
    inherited_population: str | None = None
    if population.description.status is ValueStatus.KNOWN:
        inherited_population = population.description.value
    elif population.inclusion.status is ValueStatus.KNOWN and population.inclusion.value:
        inherited_population = "; ".join(population.inclusion.value)
    disease_reference = _concept_reference(disease)
    endpoint = Endpoint(
        endpoint_id=endpoint_id,
        stable_key=stable_key,
        display_label=display_label,
        construct=construct,
        role=_qualified_enum(
            raw.get("role", _MISSING),
            EndpointRole,
            f"/endpoints/{index}/role",
            f"{source_path}/role",
            context,
            "Endpoint benefit/safety/biomarker role was not supplied.",
        ),
        endpoint_type=_qualified_enum(
            type_raw,
            EndpointType,
            f"/endpoints/{index}/endpoint_type",
            f"{source_path}/{type_name}" if type_name else "",
            context,
            "Endpoint type was not supplied.",
        ),
        population=_inherited_or_unknown_text(
            raw.get("population", _MISSING),
            f"/endpoints/{index}/population",
            f"{source_path}/population",
            context,
            "Endpoint population was not supplied and could not be inherited.",
            inherited_population,
            "/population",
            "inherit_case_population_v1",
        ),
        disease_stage=_inherited_or_unknown_text(
            stage_raw,
            f"/endpoints/{index}/disease_stage",
            f"{source_path}/{stage_name}" if stage_name else "",
            context,
            "Endpoint disease stage was not supplied and could not be inherited.",
            stage.stage,
            "/disease_stage/stage",
            "inherit_case_stage_v1",
        ),
        timeframe=_inherited_or_unknown_text(
            timeframe_raw,
            f"/endpoints/{index}/timeframe",
            f"{source_path}/{timeframe_name}" if timeframe_name else "",
            context,
            "Endpoint timeframe was not supplied and could not be inherited.",
            profile.time_horizon,
            "/target_product_profile/time_horizon",
            "inherit_case_time_horizon_v1",
        ),
        measurement=_qualified_text(
            measurement_raw,
            f"/endpoints/{index}/measurement",
            f"{source_path}/{measurement_name}" if measurement_name else "",
            context,
            "Endpoint measurement or instrument was not supplied.",
        ),
        disease_context=_inherited_or_unknown_text(
            raw.get("disease_context", _MISSING),
            f"/endpoints/{index}/disease_context",
            f"{source_path}/disease_context",
            context,
            "Endpoint disease context was not supplied and could not be inherited.",
            disease_reference,
            "/disease",
            "inherit_case_disease_context_v1",
        ),
        direction=_qualified_enum(
            raw.get("direction", _MISSING),
            EndpointDirection,
            f"/endpoints/{index}/direction",
            f"{source_path}/direction",
            context,
            "Endpoint direction of benefit or harm was not supplied.",
        ),
        priority=_qualified_enum(
            raw.get("priority", _MISSING),
            EndpointPriority,
            f"/endpoints/{index}/priority",
            f"{source_path}/priority",
            context,
            "Endpoint priority was not supplied.",
        ),
        required=_qualified_bool(
            raw.get("required", _MISSING),
            f"/endpoints/{index}/required",
            f"{source_path}/required",
            context,
            "Endpoint required status was not supplied.",
        ),
        relationships=_unknown("Endpoint relationships have not yet been resolved."),
    )
    if endpoint.required.status is ValueStatus.KNOWN and endpoint.required.value is True:
        for name in ("role", "endpoint_type", "direction", "priority"):
            value = getattr(endpoint, name)
            if value.status is ValueStatus.NOT_APPLICABLE:
                raise CaseInputError(
                    f"/endpoints/{index}/{name}: required endpoint cannot be not_applicable"
                )
    return _EndpointDraft(
        endpoint=endpoint,
        raw_relationships=raw.get("relationships", _MISSING),
        source_path=source_path,
    )


def _resolve_endpoint_relationships(
    drafts: list[_EndpointDraft], context: _BuildContext
) -> tuple[Endpoint, ...]:
    by_id = {draft.endpoint.endpoint_id: draft.endpoint.endpoint_id for draft in drafts}
    by_key = {
        str(draft.endpoint.stable_key.value).casefold(): draft.endpoint.endpoint_id for draft in drafts
    }
    if len(by_id) != len(drafts):
        raise CaseInputError("/endpoints: duplicate endpoint_id values")
    if len(by_key) != len(drafts):
        raise CaseInputError("/endpoints: duplicate endpoint stable keys")
    resolved: list[Endpoint] = []
    for index, draft in enumerate(drafts):
        raw = draft.raw_relationships
        path = f"/endpoints/{index}/relationships"
        source_path = f"{draft.source_path}/relationships"
        if _is_missing(raw, path, source_path if raw is not _MISSING else ""):
            relationships = _unknown("Endpoint relationships were not supplied.")
            context.record(
                path,
                (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
                (),
                "missing_to_explicit_unknown_v1",
                None,
                relationships,
                relationships.reason,
            )
            context.unresolved_value(path, UnresolvedKind.MISSING, relationships.reason)
        else:
            if not isinstance(raw, (list, tuple)) or isinstance(raw, (str, bytes)):
                raise CaseInputError(f"{path}: expected a list")
            rows: list[EndpointRelationship] = []
            seen: set[tuple[str, str]] = set()
            for relationship_index, item in enumerate(raw):
                item_path = f"{path}/{relationship_index}"
                if not isinstance(item, Mapping):
                    raise CaseInputError(f"{item_path}: expected an object")
                allowed = {
                    "relationship_type", "type", "related_endpoint_id", "target_endpoint_id",
                    "related_endpoint_key", "target_endpoint_key", "rationale",
                }
                unknown = set(item) - allowed
                if unknown:
                    raise CaseInputError(f"{item_path}: unknown fields {sorted(unknown)}")
                type_raw, type_name = _get_alias(
                    item, ("relationship_type", "type"), f"{item_path}/relationship_type"
                )
                if type_raw is _MISSING:
                    raise CaseInputError(f"{item_path}: relationship_type is required")
                relationship_type = _qualified_enum(
                    type_raw,
                    EndpointRelationshipType,
                    f"{item_path}/relationship_type",
                    f"{source_path}/{relationship_index}/{type_name}",
                    context,
                    "Relationship type is required.",
                )
                if relationship_type.status is not ValueStatus.KNOWN or relationship_type.value is None:
                    raise CaseInputError(f"{item_path}: relationship_type must be known")
                target_id_raw, target_id_name = _get_alias(
                    item,
                    ("related_endpoint_id", "target_endpoint_id"),
                    f"{item_path}/related_endpoint_id",
                )
                target_key_raw, target_key_name = _get_alias(
                    item,
                    ("related_endpoint_key", "target_endpoint_key"),
                    f"{item_path}/related_endpoint_key",
                )
                if target_id_raw is not _MISSING and target_key_raw is not _MISSING:
                    target_id = _explicit_endpoint_id(target_id_raw, f"{item_path}/related_endpoint_id", context)
                    key_target = by_key.get(_canonical_text(target_key_raw).casefold())
                    if key_target != target_id:
                        raise CaseInputError(f"{item_path}: target ID and stable key conflict")
                elif target_id_raw is not _MISSING:
                    target_id = _explicit_endpoint_id(target_id_raw, f"{item_path}/related_endpoint_id", context)
                elif target_key_raw is not _MISSING:
                    target_key = _canonical_text(target_key_raw).casefold()
                    target_id = by_key.get(target_key, "")
                    context.record(
                        f"{item_path}/related_endpoint_id",
                        (ProvenanceClassification.INFERRED,),
                        (f"{source_path}/{relationship_index}/{target_key_name}",),
                        "resolve_endpoint_relationship_key_v1",
                        target_key_raw,
                        target_id,
                    )
                else:
                    raise CaseInputError(f"{item_path}: related endpoint ID or stable key is required")
                if target_id not in by_id:
                    raise CaseInputError(f"{item_path}: relationship target {target_id!r} does not exist")
                if target_id == draft.endpoint.endpoint_id:
                    raise CaseInputError(f"{item_path}: endpoint relationship cannot reference itself")
                identity = (relationship_type.value.value, target_id)
                if identity in seen:
                    raise CaseInputError(f"{item_path}: duplicate endpoint relationship")
                seen.add(identity)
                rows.append(
                    EndpointRelationship(
                        relationship_type=relationship_type.value,
                        related_endpoint_id=target_id,
                        rationale=_qualified_text(
                            item.get("rationale", _MISSING),
                            f"{item_path}/rationale",
                            f"{source_path}/{relationship_index}/rationale",
                            context,
                            "Endpoint relationship rationale was not supplied.",
                        ),
                    )
                )
            relationships = _known(tuple(rows))
            context.record(
                path,
                (ProvenanceClassification.USER_SUPPLIED, ProvenanceClassification.NORMALIZED),
                (source_path,),
                "typed_endpoint_relationships_v1",
                raw,
                relationships,
            )
        resolved.append(replace(draft.endpoint, relationships=relationships))
    return tuple(resolved)


_TOP_LEVEL_INPUT_FIELDS = {
    "schema_version",
    "gene",
    "human_gene",
    "disease",
    "human_disease",
    "phenotype",
    "phenotypes",
    "human_phenotype",
    "disease_subtype",
    "population",
    "tissue",
    "target_tissue",
    "disease_stage",
    "stage",
    "target_product_profile",
    "time_horizon",
    "contraindications",
    "excluded_intervention_categories",
    "endpoints",
}


def _phenotype_portfolio(
    raw: Any, source_path: str, context: _BuildContext
) -> QualifiedValue[tuple[MappedConcept, ...]]:
    if _is_missing(raw, "/phenotypes", source_path):
        reason = "No human phenotype input was supplied."
        value = _unknown(reason)
        context.record(
            "/phenotypes",
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (source_path,) if source_path else (),
            "missing_to_explicit_unknown_v1",
            None,
            value,
            reason,
        )
        context.unresolved_value("/phenotypes", UnresolvedKind.MISSING, reason)
        return value
    is_portfolio = isinstance(raw, (list, tuple)) and not isinstance(raw, (str, bytes))
    items = raw if is_portfolio else [raw]
    concepts: list[MappedConcept] = []
    for index, item in enumerate(items):
        parsed = _concept_value(
            item,
            ConceptKind.PHENOTYPE,
            f"/phenotypes/{index}",
            f"{source_path}/{index}" if isinstance(raw, (list, tuple)) else source_path,
            context,
            blocking_ambiguity=True,
        )
        if parsed.status is ValueStatus.KNOWN and parsed.value is not None:
            concepts.append(parsed.value)
        elif is_portfolio:
            raise CaseInputError(
                f"/phenotypes/{index}: an unresolved portfolio member cannot be silently omitted"
            )
        else:
            value = QualifiedValue(
                status=parsed.status,
                value=None,
                reason=parsed.reason,
            )
            context.record(
                "/phenotypes",
                (ProvenanceClassification.USER_SUPPLIED, ProvenanceClassification.UNRESOLVED),
                (source_path,),
                "explicit_unresolved_phenotype_portfolio_v1",
                raw,
                value,
            )
            return value
    value = _known(tuple(concepts))
    context.record(
        "/phenotypes",
        (ProvenanceClassification.USER_SUPPLIED, ProvenanceClassification.NORMALIZED),
        (source_path,),
        "normalized_phenotype_portfolio_v1",
        raw,
        value,
    )
    return value


def _concept_has_anchor(value: QualifiedValue[MappedConcept]) -> bool:
    if value.status is not ValueStatus.KNOWN or value.value is None:
        return False
    return (
        value.value.coding.status is ValueStatus.KNOWN
        or value.value.label.status is ValueStatus.KNOWN
    )


def _gene_has_anchor(value: QualifiedValue[GeneContext]) -> bool:
    if value.status is not ValueStatus.KNOWN or value.value is None:
        return False
    concept = value.value.concept
    return concept.coding.status is ValueStatus.KNOWN or concept.label.status is ValueStatus.KNOWN


def _anchor_projection(
    gene: QualifiedValue[GeneContext],
    disease: QualifiedValue[MappedConcept],
    phenotypes: QualifiedValue[tuple[MappedConcept, ...]],
) -> dict[str, Any]:
    return {
        "gene": (
            _concept_reference(_known(gene.value.concept))
            if gene.status is ValueStatus.KNOWN and gene.value is not None
            else None
        ),
        "disease": _concept_reference(disease),
        "phenotypes": sorted(
            filter(
                None,
                (
                    _concept_reference(_known(concept))
                    for concept in (phenotypes.value or ())
                ),
            )
        ),
    }


def _dedupe_unresolved(values: list[UnresolvedInput]) -> tuple[UnresolvedInput, ...]:
    unique: dict[bytes, UnresolvedInput] = {}
    for value in values:
        unique[canonical_bytes(value)] = value
    return tuple(
        sorted(
            unique.values(),
            key=lambda row: (row.path, row.kind.value, row.reason, row.candidates, row.blocking),
        )
    )


def _validate_against_annotation(value: Any, annotation: Any, path: str) -> None:
    if annotation is Any or isinstance(annotation, type(T)):
        return
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {Union, UnionType}:
        failures: list[str] = []
        for argument in arguments:
            try:
                _validate_against_annotation(value, argument, path)
                return
            except CaseInputError as exc:
                failures.append(str(exc))
        raise CaseInputError(f"{path}: value does not match {_type_expression(annotation)}")
    if origin is QualifiedValue:
        if not isinstance(value, QualifiedValue):
            raise CaseInputError(f"{path}: expected QualifiedValue")
        _validate_qualified(value, path)
        if value.status is ValueStatus.KNOWN and arguments:
            _validate_against_annotation(value.value, arguments[0], f"{path}/value")
        return
    if origin is tuple:
        if not isinstance(value, tuple):
            raise CaseInputError(f"{path}: expected immutable tuple")
        item_type = arguments[0] if arguments else Any
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            for index, item in enumerate(value):
                _validate_against_annotation(item, item_type, f"{path}/{index}")
        elif arguments:
            if len(value) != len(arguments):
                raise CaseInputError(f"{path}: tuple length mismatch")
            for index, (item, item_annotation) in enumerate(zip(value, arguments)):
                _validate_against_annotation(item, item_annotation, f"{path}/{index}")
        return
    if origin in {dict, Mapping, collections_abc.Mapping}:
        if not isinstance(value, Mapping):
            raise CaseInputError(f"{path}: expected mapping")
        key_type, value_type = arguments if len(arguments) == 2 else (Any, Any)
        for key, item in value.items():
            _validate_against_annotation(key, key_type, f"{path}/<key>")
            _validate_against_annotation(item, value_type, f"{path}/{key}")
        return
    if origin is not None:
        try:
            matches = isinstance(value, origin)
        except TypeError:
            matches = True
        if not matches:
            raise CaseInputError(f"{path}: expected {_type_expression(annotation)}")
        return
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        if not isinstance(value, annotation):
            raise CaseInputError(f"{path}: expected {annotation.__name__}")
        return
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, annotation):
            raise CaseInputError(f"{path}: expected {annotation.__name__}")
        hints = get_type_hints(annotation)
        for field in fields(annotation):
            _validate_against_annotation(
                getattr(value, field.name), hints[field.name], f"{path}/{field.name}"
            )
        return
    if annotation is type(None):
        if value is not None:
            raise CaseInputError(f"{path}: expected null")
        return
    if annotation in {str, int, bool, float}:
        if type(value) is not annotation:
            raise CaseInputError(f"{path}: expected {annotation.__name__}")
        return
    if isinstance(annotation, type) and not isinstance(value, annotation):
        raise CaseInputError(f"{path}: expected {annotation.__name__}")


def _decode_typed(value: Any, annotation: Any, path: str) -> Any:
    if annotation is Any or isinstance(annotation, type(T)):
        return _freeze(value)
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {Union, UnionType}:
        if value is None and type(None) in arguments:
            return None
        errors: list[str] = []
        for argument in arguments:
            if argument is type(None):
                continue
            try:
                return _decode_typed(value, argument, path)
            except CaseInputError as exc:
                errors.append(str(exc))
        raise CaseInputError(f"{path}: value does not match {_type_expression(annotation)}")
    if origin is QualifiedValue:
        if not isinstance(value, Mapping) or set(value) != {"status", "value", "reason"}:
            raise CaseInputError(f"{path}: malformed QualifiedValue")
        try:
            status = ValueStatus(value["status"])
        except (TypeError, ValueError) as exc:
            raise CaseInputError(f"{path}: invalid qualified status") from exc
        if type(value["reason"]) is not str:
            raise CaseInputError(f"{path}/reason: expected str")
        decoded_value = (
            _decode_typed(value["value"], arguments[0], f"{path}/value")
            if status is ValueStatus.KNOWN and arguments
            else value["value"]
        )
        result = QualifiedValue(status=status, value=decoded_value, reason=value["reason"])
        _validate_qualified(result, path)
        return result
    if origin is tuple:
        if not isinstance(value, list):
            raise CaseInputError(f"{path}: persisted tuple must be a JSON list")
        item_type = arguments[0] if arguments else Any
        if len(arguments) == 2 and arguments[1] is Ellipsis:
            return tuple(
                _decode_typed(item, item_type, f"{path}/{index}")
                for index, item in enumerate(value)
            )
        if arguments and len(value) != len(arguments):
            raise CaseInputError(f"{path}: tuple length mismatch")
        return tuple(
            _decode_typed(item, arguments[index], f"{path}/{index}")
            for index, item in enumerate(value)
        )
    if origin in {dict, Mapping, collections_abc.Mapping}:
        if not isinstance(value, Mapping):
            raise CaseInputError(f"{path}: expected mapping")
        key_type, value_type = arguments if len(arguments) == 2 else (Any, Any)
        decoded: dict[Any, Any] = {}
        for key, item in value.items():
            decoded_key = _decode_typed(key, key_type, f"{path}/<key>")
            decoded[decoded_key] = _decode_typed(item, value_type, f"{path}/{key}")
        return MappingProxyType(decoded)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        try:
            return annotation(value)
        except (TypeError, ValueError) as exc:
            raise CaseInputError(f"{path}: invalid {annotation.__name__}") from exc
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, Mapping):
            raise CaseInputError(f"{path}: expected {annotation.__name__} object")
        expected = {field.name for field in fields(annotation)}
        if set(value) != expected:
            raise CaseInputError(f"{path}: fields differ from {annotation.__name__}")
        hints = get_type_hints(annotation)
        return annotation(
            **{
                field.name: _decode_typed(
                    value[field.name], hints[field.name], f"{path}/{field.name}"
                )
                for field in fields(annotation)
            }
        )
    if annotation is type(None):
        if value is not None:
            raise CaseInputError(f"{path}: expected null")
        return None
    if annotation in {str, int, bool, float}:
        if type(value) is not annotation:
            raise CaseInputError(f"{path}: expected {annotation.__name__}")
        return value
    if origin is not None:
        return value
    if isinstance(annotation, type) and not isinstance(value, annotation):
        raise CaseInputError(f"{path}: expected {annotation.__name__}")
    return value


def validate_case_revision(case: CaseRevision) -> None:
    """Validate a constructed revision without consulting duplicated field lists."""

    _validate_against_annotation(case, CaseRevision, "")
    if case.schema_version != SCHEMA_VERSION or case.model_version != CASE_MODEL_VERSION:
        raise CaseInputError("Case revision schema/model version mismatch")
    if not isinstance(case.original_input, MappingProxyType):
        raise CaseInputError("original_input must be recursively immutable")
    if case.source_input_sha256 != content_sha256(case.original_input):
        raise CaseInputError("source_input_sha256 does not match original_input")
    expected_case_id = f"CASE-{content_sha256(_anchor_projection(case.gene, case.disease, case.phenotypes))[:20]}"
    if case.case_id != expected_case_id:
        raise CaseInputError("case_id does not match normalized case anchors")
    expected_revision_id = f"CASE-REV-{content_sha256(replace(case, case_revision_id=''))[:24]}"
    if case.case_revision_id != expected_revision_id:
        raise CaseInputError("case_revision_id does not match canonical case content")
    anchors = [
        _gene_has_anchor(case.gene),
        _concept_has_anchor(case.disease),
        bool(case.phenotypes.value) if case.phenotypes.status is ValueStatus.KNOWN else False,
    ]
    if not any(anchors):
        raise CaseInputError("Provide at least one human gene, disease, or phenotype anchor")
    if not case.endpoints:
        raise CaseInputError("Case endpoint portfolio cannot be empty")
    endpoint_ids = [endpoint.endpoint_id for endpoint in case.endpoints]
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise CaseInputError("Endpoint IDs must be unique")
    valid_ids = set(endpoint_ids)
    stable_keys: set[str] = set()
    blocking_paths = {row.path for row in case.unresolved_inputs if row.blocking}
    for index, endpoint in enumerate(case.endpoints):
        if not _ENDPOINT_ID_PATTERN.fullmatch(endpoint.endpoint_id):
            raise CaseInputError(f"Malformed endpoint ID: {endpoint.endpoint_id}")
        if endpoint.stable_key.status is not ValueStatus.KNOWN or not endpoint.stable_key.value:
            raise CaseInputError("Every endpoint requires a known machine stable key")
        stable_marker = _canonical_text(endpoint.stable_key.value).casefold()
        if stable_marker in stable_keys:
            raise CaseInputError("Endpoint stable keys must be unique case-insensitively")
        stable_keys.add(stable_marker)
        if (
            endpoint.display_label.status is ValueStatus.KNOWN
            and str(endpoint.display_label.value).casefold() == endpoint.endpoint_id.casefold()
        ):
            raise CaseInputError("Endpoint ID must be distinct from display label")
        construct_is_coded = (
            endpoint.construct.status is ValueStatus.KNOWN
            and endpoint.construct.value is not None
            and endpoint.construct.value.coding.status is ValueStatus.KNOWN
            and endpoint.construct.value.coding.value is not None
        )
        if not construct_is_coded and f"/endpoints/{index}/construct/coding" not in blocking_paths:
            raise CaseInputError("Uncoded endpoint construct must be a blocking unresolved input")
        if endpoint.required.status is ValueStatus.KNOWN and endpoint.required.value is True:
            for name in ("role", "endpoint_type", "direction", "priority"):
                if getattr(endpoint, name).status is ValueStatus.NOT_APPLICABLE:
                    raise CaseInputError("Required endpoint fields cannot be not_applicable")
        if endpoint.relationships.status is ValueStatus.KNOWN:
            relationship_keys: set[tuple[str, str]] = set()
            for relationship in endpoint.relationships.value or ():
                if relationship.related_endpoint_id not in valid_ids:
                    raise CaseInputError("Endpoint relationship target is absent")
                if relationship.related_endpoint_id == endpoint.endpoint_id:
                    raise CaseInputError("Endpoint relationship cannot be self-referential")
                relationship_key = (
                    relationship.relationship_type.value,
                    relationship.related_endpoint_id,
                )
                if relationship_key in relationship_keys:
                    raise CaseInputError("Endpoint relationships must be unique")
                relationship_keys.add(relationship_key)
    unresolved_bytes = [canonical_bytes(row) for row in case.unresolved_inputs]
    if len(unresolved_bytes) != len(set(unresolved_bytes)):
        raise CaseInputError("unresolved_inputs contains duplicate records")
    if tuple(sorted(case.unresolved_inputs, key=lambda row: (
        row.path, row.kind.value, row.reason, row.candidates, row.blocking
    ))) != case.unresolved_inputs:
        raise CaseInputError("unresolved_inputs must be in canonical order")
    expected_status = (
        CaseStatus.NEEDS_RESOLUTION
        if any(row.blocking for row in case.unresolved_inputs)
        else CaseStatus.READY
    )
    if case.case_status is not expected_status:
        raise CaseInputError("case_status does not match blocking unresolved inputs")


def build_case_bundle(raw_input: Mapping[str, Any]) -> CaseBundle:
    """Normalize raw schema-v7 input into one canonical typed case revision."""

    if not isinstance(raw_input, Mapping):
        raise CaseInputError("Case input must be one JSON object")
    if any(not isinstance(key, str) for key in raw_input):
        raise CaseInputError("Case input keys must be strings")
    unknown_fields = set(raw_input) - _TOP_LEVEL_INPUT_FIELDS
    if unknown_fields:
        raise CaseInputError(f"Unknown case input fields: {sorted(unknown_fields)}")
    if "schema_version" in raw_input and (
        type(raw_input["schema_version"]) is not int
        or raw_input["schema_version"] != SCHEMA_VERSION
    ):
        raise CaseInputError(f"schema_version must be integer {SCHEMA_VERSION} for v7 initialization")
    try:
        source_input_sha256 = content_sha256(raw_input)
    except (TypeError, ValueError) as exc:
        raise CaseInputError(f"Case input is not canonical-JSON serializable: {exc}") from exc
    original_input = _freeze(json.loads(canonical_bytes(raw_input).decode("utf-8")))
    context = _BuildContext()

    gene_raw, gene_name = _get_alias(raw_input, ("gene", "human_gene"), "/gene")
    disease_raw, disease_name = _get_alias(raw_input, ("disease", "human_disease"), "/disease")
    phenotype_raw, phenotype_name = _get_alias(
        raw_input, ("phenotypes", "phenotype", "human_phenotype"), "/phenotypes"
    )
    tissue_raw, tissue_name = _get_alias(raw_input, ("tissue", "target_tissue"), "/tissue")
    stage_raw, stage_name = _get_alias(raw_input, ("disease_stage", "stage"), "/disease_stage")

    gene = _gene_context(gene_raw, f"/{gene_name}" if gene_name else "", context)
    disease = _concept_value(
        disease_raw,
        ConceptKind.DISEASE,
        "/disease",
        f"/{disease_name}" if disease_name else "",
        context,
        blocking_ambiguity=True,
    )
    phenotypes = _phenotype_portfolio(
        phenotype_raw, f"/{phenotype_name}" if phenotype_name else "", context
    )
    disease_subtype = _qualified_text(
        raw_input.get("disease_subtype", _MISSING),
        "/disease_subtype",
        "/disease_subtype" if "disease_subtype" in raw_input else "",
        context,
        "Disease subtype was not supplied.",
    )
    population = _population_context(
        raw_input.get("population", _MISSING),
        "/population" if "population" in raw_input else "",
        context,
    )
    tissue = _tissue_context(
        tissue_raw, f"/{tissue_name}" if tissue_name else "", context
    )
    disease_stage = _stage_context(
        stage_raw, f"/{stage_name}" if stage_name else "", context
    )
    profile = _target_product_profile(
        raw_input.get("target_product_profile", _MISSING),
        "/target_product_profile" if "target_product_profile" in raw_input else "",
        context,
        raw_input.get("time_horizon", _MISSING),
        "/time_horizon" if "time_horizon" in raw_input else "",
    )
    contraindications = _qualified_string_list(
        raw_input.get("contraindications", _MISSING),
        "/contraindications",
        "/contraindications" if "contraindications" in raw_input else "",
        context,
        "Known contraindications were not supplied; this does not mean none exist.",
    )
    excluded_categories = _qualified_string_list(
        raw_input.get("excluded_intervention_categories", _MISSING),
        "/excluded_intervention_categories",
        "/excluded_intervention_categories" if "excluded_intervention_categories" in raw_input else "",
        context,
        "Excluded intervention categories were not supplied.",
    )

    endpoints_raw = raw_input.get("endpoints", _MISSING)
    missing_endpoint_portfolio = _is_missing(
        endpoints_raw,
        "/endpoints",
        "/endpoints" if "endpoints" in raw_input else "",
    )
    if missing_endpoint_portfolio:
        endpoint_items: list[Mapping[str, Any]] = [{}]
        endpoint_source = ""
        reason = (
            "No endpoint portfolio was supplied. A typed unresolved slot was created; it is not a generic "
            "therapeutic endpoint and Stage 1 cannot pass until resolved."
        )
        context.unresolved_value("/endpoints", UnresolvedKind.MISSING, reason, blocking=True)
        context.record(
            "/endpoints",
            (ProvenanceClassification.INFERRED, ProvenanceClassification.UNRESOLVED),
            (),
            "missing_endpoint_to_unresolved_slot_v1",
            None,
            [{"stable_key": "unresolved-input-slot:1"}],
            reason,
        )
    else:
        if not isinstance(endpoints_raw, (list, tuple)) or isinstance(endpoints_raw, (str, bytes)):
            raise CaseInputError("/endpoints: expected a list of endpoint objects")
        if not endpoints_raw:
            raise CaseInputError("/endpoints: explicitly empty endpoint portfolio is not allowed")
        if any(not isinstance(item, Mapping) for item in endpoints_raw):
            raise CaseInputError("/endpoints: every endpoint must be an object")
        if any(not item for item in endpoints_raw):
            raise CaseInputError("/endpoints: an explicitly supplied endpoint object cannot be empty")
        endpoint_items = list(endpoints_raw)
        endpoint_source = "/endpoints"
    drafts = [
        _endpoint_draft(
            item,
            index,
            f"{endpoint_source}/{index}" if endpoint_source else "",
            context,
            disease,
            population,
            disease_stage,
            profile,
        )
        for index, item in enumerate(endpoint_items)
    ]
    endpoints = _resolve_endpoint_relationships(drafts, context)

    if not (_gene_has_anchor(gene) or _concept_has_anchor(disease) or (
        phenotypes.status is ValueStatus.KNOWN and bool(phenotypes.value)
    )):
        raise CaseInputError("Provide at least one human gene, disease, or phenotype anchor")

    unresolved = _dedupe_unresolved(context.unresolved)
    case_status = (
        CaseStatus.NEEDS_RESOLUTION if any(row.blocking for row in unresolved) else CaseStatus.READY
    )
    anchor_projection = _anchor_projection(gene, disease, phenotypes)
    case_id = f"CASE-{content_sha256(anchor_projection)[:20]}"
    draft = CaseRevision(
        schema_version=SCHEMA_VERSION,
        model_version=CASE_MODEL_VERSION,
        case_id=case_id,
        case_revision_id="",
        case_status=case_status,
        source_input_sha256=source_input_sha256,
        original_input=original_input,
        gene=gene,
        disease=disease,
        phenotypes=phenotypes,
        disease_subtype=disease_subtype,
        population=population,
        tissue=tissue,
        disease_stage=disease_stage,
        target_product_profile=profile,
        contraindications=contraindications,
        excluded_intervention_categories=excluded_categories,
        endpoints=endpoints,
        unresolved_inputs=unresolved,
    )
    revision_id = f"CASE-REV-{content_sha256(draft)[:24]}"
    case_revision = replace(draft, case_revision_id=revision_id)
    context.record(
        "/schema_version",
        (ProvenanceClassification.INFERRED,),
        (),
        "schema_version_constant_v1",
        raw_input.get("schema_version"),
        SCHEMA_VERSION,
    )
    context.record(
        "/model_version",
        (ProvenanceClassification.INFERRED,),
        (),
        "case_model_version_constant_v1",
        None,
        CASE_MODEL_VERSION,
    )
    context.record(
        "/source_input_sha256",
        (ProvenanceClassification.INFERRED, ProvenanceClassification.NORMALIZED),
        ("/",),
        "canonical_source_input_hash_v1",
        raw_input,
        source_input_sha256,
    )
    context.record(
        "/original_input",
        (ProvenanceClassification.USER_SUPPLIED,),
        ("/",),
        "retain_original_input_v1",
        raw_input,
        original_input,
    )
    context.record(
        "/case_status",
        (ProvenanceClassification.INFERRED,),
        ("/unresolved_inputs",),
        "blocking_unresolved_status_v1",
        None,
        case_status,
    )
    context.record(
        "/unresolved_inputs",
        (ProvenanceClassification.INFERRED,),
        tuple(row.path for row in unresolved),
        "explicit_unresolved_inventory_v1",
        None,
        unresolved,
    )
    context.record(
        "/case_id",
        (ProvenanceClassification.INFERRED,),
        ("/gene", "/disease", "/phenotypes"),
        "case_anchor_identity_v1",
        None,
        case_id,
    )
    context.record(
        "/case_revision_id",
        (ProvenanceClassification.INFERRED,),
        ("/",),
        "case_revision_content_hash_v1",
        None,
        revision_id,
    )
    entries = tuple(
        sorted(
            context.provenance,
            key=lambda row: (row.path, row.rule_id, canonical_bytes(row.original_value)),
        )
    )
    provenance = CaseModelProvenance(
        schema_version=SCHEMA_VERSION,
        provenance_version=PROVENANCE_VERSION,
        case_revision_id=revision_id,
        source_input_sha256=source_input_sha256,
        entries=entries,
    )
    validate_case_revision(case_revision)
    return CaseBundle(
        case_revision=case_revision,
        provenance=provenance,
        validation_metadata=_freeze(validation_metadata()),
    )


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(_plain(value), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def initialize_case(root: str | Path, raw_input: Mapping[str, Any]) -> dict[str, Any]:
    """Write one deterministic native-v7 case container and no workflow DAG."""

    root_path = Path(root).expanduser().resolve()
    if root_path.exists() and (not root_path.is_dir() or any(root_path.iterdir())):
        raise CaseInputError(f"Run folder is not empty: {root_path}")
    bundle = build_case_bundle(raw_input)
    artifacts: dict[str, bytes] = {
        "case_input.json": _pretty_json_bytes(bundle.case_revision.original_input),
        "case_revision.json": _pretty_json_bytes(bundle.case_revision),
        "case_model_provenance.json": _pretty_json_bytes(bundle.provenance),
        "case_model_schema.json": _pretty_json_bytes(bundle.validation_metadata),
    }
    artifact_hashes = {
        name: hashlib.sha256(payload).hexdigest().upper() for name, payload in sorted(artifacts.items())
    }
    manifest = {
        "artifact_type": "schema_v7_native_case_container",
        "schema_version": SCHEMA_VERSION,
        "model_version": CASE_MODEL_VERSION,
        "case_id": bundle.case_revision.case_id,
        "case_revision_id": bundle.case_revision.case_revision_id,
        "case_status": bundle.case_revision.case_status.value,
        "initialization_state": "case_model_complete",
        "runtime_state": "not_implemented_in_foundational_case_slice",
        "artifacts": artifact_hashes,
    }
    root_existed = root_path.is_dir()
    root_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{root_path.name}.v7-init-", dir=str(root_path.parent))
    )
    try:
        for name, payload in sorted(artifacts.items()):
            _atomic_write(staging / name, payload)
        _atomic_write(staging / "schema_manifest.json", _pretty_json_bytes(manifest))
        if root_existed:
            root_path.rmdir()
        staging.replace(root_path)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if root_existed and not root_path.exists():
            root_path.mkdir()
        raise
    return manifest


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaseInputError(f"Cannot inspect JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CaseInputError(f"Inspectable artifact must contain one JSON object: {path}")
    return value


def detect_schema_version(path: str | Path) -> int:
    target = Path(path).expanduser().resolve()
    if target.is_file():
        version = _read_json_object(target).get("schema_version")
    elif target.is_dir():
        manifest = target / "schema_manifest.json"
        if manifest.is_file():
            version = _read_json_object(manifest).get("schema_version")
        else:
            versions: list[Any] = []
            for name in ("program_state.json", "execution_plan.json"):
                artifact = target / name
                if artifact.is_file():
                    versions.append(_read_json_object(artifact).get("schema_version"))
            if not versions:
                for artifact in sorted(target.glob("*.json")):
                    try:
                        candidate = _read_json_object(artifact).get("schema_version")
                    except CaseInputError:
                        continue
                    if candidate is not None:
                        versions.append(candidate)
            if not versions:
                raise CaseInputError(f"No schema-bearing artifact found in {target}")
            if len(set(versions)) != 1:
                raise CaseInputError(f"Conflicting schema versions in {target}: {versions}")
            version = versions[0]
    else:
        raise CaseInputError(f"Artifact does not exist: {target}")
    if isinstance(version, bool) or not isinstance(version, int):
        raise CaseInputError(f"Artifact has no integer schema_version: {target}")
    return version


def _artifact_hashes(path: Path) -> tuple[dict[str, str], int]:
    if path.is_file():
        payload = path.read_bytes()
        return {path.name: hashlib.sha256(payload).hexdigest().upper()}, len(payload)
    hashes: dict[str, str] = {}
    total_bytes = 0
    for artifact in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = artifact.relative_to(path).as_posix()
        payload = artifact.read_bytes()
        hashes[relative] = hashlib.sha256(payload).hexdigest().upper()
        total_bytes += len(payload)
    return hashes, total_bytes


_NATIVE_CASE_ARTIFACTS = {
    "case_input.json",
    "case_revision.json",
    "case_model_provenance.json",
    "case_model_schema.json",
}


def _mapping_concept_reference(value: Any) -> str | None:
    if not isinstance(value, Mapping) or value.get("status") != ValueStatus.KNOWN.value:
        return None
    concept = value.get("value")
    if not isinstance(concept, Mapping):
        return None
    coding = concept.get("coding")
    if isinstance(coding, Mapping) and coding.get("status") == ValueStatus.KNOWN.value:
        code = coding.get("value")
        if isinstance(code, Mapping):
            return f"{code.get('namespace')}:{code.get('identifier')}"
    label = concept.get("label")
    if isinstance(label, Mapping) and label.get("status") == ValueStatus.KNOWN.value:
        return str(label.get("value"))
    return None


def _verify_native_case_container(target: Path, manifest: Mapping[str, Any]) -> None:
    if not target.is_dir():
        raise CaseInputError("Native schema-v7 case inspection requires a container directory")
    if manifest.get("artifact_type") != "schema_v7_native_case_container":
        raise CaseInputError("Unsupported schema-v7 artifact type")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("model_version") != CASE_MODEL_VERSION:
        raise CaseInputError("Native schema-v7 manifest version mismatch")
    declared_hashes = manifest.get("artifacts")
    if not isinstance(declared_hashes, Mapping) or set(declared_hashes) != _NATIVE_CASE_ARTIFACTS:
        raise CaseInputError("Native schema-v7 manifest artifact set is invalid")
    actual_files = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    }
    core_files = _NATIVE_CASE_ARTIFACTS | {"schema_manifest.json"}
    missing_core = core_files - actual_files
    unexpected = {
        name
        for name in actual_files - core_files
        if not name.startswith("runtime_v7/")
    }
    if missing_core or unexpected:
        raise CaseInputError("Native schema-v7 container contains missing or unexpected files")
    if any(name.startswith("runtime_v7/") for name in actual_files):
        runtime_plan = target / "runtime_v7" / "execution_plan.json"
        runtime_config = target / "runtime_v7" / "runtime_config.json"
        if not runtime_plan.is_file() or not runtime_config.is_file():
            raise CaseInputError("Native schema-v7 runtime subtree is incomplete")
    for name in sorted(_NATIVE_CASE_ARTIFACTS):
        actual = hashlib.sha256((target / name).read_bytes()).hexdigest().upper()
        if actual != str(declared_hashes[name]).upper():
            raise CaseInputError(f"Native schema-v7 artifact hash mismatch: {name}")

    case_input = _read_json_object(target / "case_input.json")
    case = _read_json_object(target / "case_revision.json")
    provenance = _read_json_object(target / "case_model_provenance.json")
    stored_metadata = _read_json_object(target / "case_model_schema.json")
    if stored_metadata != _plain(validation_metadata()):
        raise CaseInputError("Derived case-model validation metadata does not match production types")
    typed_case = _decode_typed(case, CaseRevision, "")
    typed_provenance = _decode_typed(provenance, CaseModelProvenance, "/provenance")
    validate_case_revision(typed_case)
    _validate_against_annotation(typed_provenance, CaseModelProvenance, "/provenance")
    if set(case) != {field.name for field in fields(CaseRevision)}:
        raise CaseInputError("Native case revision fields differ from the canonical typed definition")
    if set(provenance) != {field.name for field in fields(CaseModelProvenance)}:
        raise CaseInputError("Native provenance fields differ from the canonical typed definition")
    if case.get("schema_version") != SCHEMA_VERSION or case.get("model_version") != CASE_MODEL_VERSION:
        raise CaseInputError("Native case revision version mismatch")
    source_hash = content_sha256(case_input)
    if case.get("source_input_sha256") != source_hash or case.get("original_input") != case_input:
        raise CaseInputError("Native case revision does not retain its hashed original input")
    if provenance.get("schema_version") != SCHEMA_VERSION:
        raise CaseInputError("Native case provenance schema mismatch")
    if (
        provenance.get("case_revision_id") != case.get("case_revision_id")
        or provenance.get("source_input_sha256") != source_hash
    ):
        raise CaseInputError("Native case provenance does not link to the case revision/input")
    revision_body = dict(case)
    revision_body["case_revision_id"] = ""
    expected_revision = f"CASE-REV-{content_sha256(revision_body)[:24]}"
    if case.get("case_revision_id") != expected_revision:
        raise CaseInputError("Native case revision content hash mismatch")

    gene = case.get("gene")
    gene_reference = None
    if isinstance(gene, Mapping) and gene.get("status") == ValueStatus.KNOWN.value:
        gene_value = gene.get("value")
        if isinstance(gene_value, Mapping):
            gene_reference = _mapping_concept_reference(
                {"status": ValueStatus.KNOWN.value, "value": gene_value.get("concept"), "reason": ""}
            )
    disease_reference = _mapping_concept_reference(case.get("disease"))
    phenotype_references: list[str] = []
    phenotype_value = case.get("phenotypes")
    if isinstance(phenotype_value, Mapping) and phenotype_value.get("status") == ValueStatus.KNOWN.value:
        for concept in phenotype_value.get("value") or []:
            reference = _mapping_concept_reference(
                {"status": ValueStatus.KNOWN.value, "value": concept, "reason": ""}
            )
            if reference:
                phenotype_references.append(reference)
    anchor_projection = {
        "gene": gene_reference,
        "disease": disease_reference,
        "phenotypes": sorted(phenotype_references),
    }
    expected_case_id = f"CASE-{content_sha256(anchor_projection)[:20]}"
    if case.get("case_id") != expected_case_id:
        raise CaseInputError("Native case identity does not match normalized anchors")
    unresolved = case.get("unresolved_inputs")
    if not isinstance(unresolved, list):
        raise CaseInputError("Native unresolved_inputs must be a list")
    expected_status = (
        CaseStatus.NEEDS_RESOLUTION.value
        if any(isinstance(row, Mapping) and row.get("blocking") is True for row in unresolved)
        else CaseStatus.READY.value
    )
    if case.get("case_status") != expected_status:
        raise CaseInputError("Native case status does not match blocking unresolved inputs")
    if (
        manifest.get("case_id") != case.get("case_id")
        or manifest.get("case_revision_id") != case.get("case_revision_id")
        or manifest.get("case_status") != case.get("case_status")
    ):
        raise CaseInputError("Native schema-v7 manifest identity/status mismatch")


def inspect_artifact(path: str | Path) -> dict[str, Any]:
    """Inspect a schema-bearing file or folder without opening any write handle."""

    target = Path(path).expanduser().resolve()
    version = detect_schema_version(target)
    native_verified = False
    if target.is_dir() and (target / "schema_manifest.json").is_file():
        manifest = _read_json_object(target / "schema_manifest.json")
        if manifest.get("artifact_type") == "schema_v7_native_case_container":
            _verify_native_case_container(target, manifest)
            native_verified = True
        elif version == SCHEMA_VERSION:
            raise CaseInputError("Unsupported schema-v7 container type")
    elif version == SCHEMA_VERSION and target.is_dir():
        raise CaseInputError("Schema-v7 container lacks schema_manifest.json")
    hashes, total_bytes = _artifact_hashes(target)
    legacy = version in {3, 4, 5, 6}
    mode = (
        "read_only"
        if legacy
        else "native_read_only_inspection"
        if native_verified
        else "unsupported_read_only"
    )
    return {
        "schema_version": version,
        "artifact_kind": "file" if target.is_file() else "folder",
        "mode": mode,
        "legacy": legacy,
        "integrity": "verified" if native_verified else "not_applicable",
        "file_count": len(hashes),
        "total_bytes": total_bytes,
        "content_sha256": content_sha256(hashes),
        "file_sha256": hashes,
        "supported_operations": ["inspect"],
    }


class V7CompatibilityAdapter:
    """Production compatibility boundary used by v7 tests and CLI routing."""

    def inspect_legacy(self, path: Path) -> Mapping[str, Any]:
        result = inspect_artifact(path)
        if result["schema_version"] not in {3, 4, 5, 6}:
            raise CaseInputError("inspect_legacy accepts only schema-v3 through schema-v6 artifacts")
        return result

    def request_legacy_operation(self, path: Path, operation: str) -> Mapping[str, Any]:
        normalized_operation = _canonical_token(operation)
        version = detect_schema_version(path)
        if version not in {3, 4, 5, 6}:
            return {
                "schema_version": version,
                "operation": normalized_operation,
                "allowed": False,
                "mode": "unsupported",
                "reason": "artifact is not schema-v3 through schema-v6",
            }
        if normalized_operation == "inspect":
            return {
                "schema_version": version,
                "operation": normalized_operation,
                "allowed": True,
                "mode": "read_only",
                "reason": "legacy inspection is read-only",
                "inspection": self.inspect_legacy(path),
            }
        if normalized_operation in {"resume", "write", "append", "finalize", "initialize"}:
            return {
                "schema_version": version,
                "operation": normalized_operation,
                "allowed": False,
                "mode": "read_only",
                "reason": "schema-v3 through schema-v6 mutation is prohibited at the v7 boundary",
            }
        return {
            "schema_version": version,
            "operation": normalized_operation,
            "allowed": False,
            "mode": "unsupported",
            "reason": "unsupported legacy operation",
        }


def is_v7_case_container(path: str | Path) -> bool:
    target = Path(path).expanduser().resolve()
    manifest = target / "schema_manifest.json" if target.is_dir() else target
    if not manifest.is_file():
        return False
    try:
        value = _read_json_object(manifest)
    except CaseInputError:
        return False
    return (
        value.get("schema_version") == SCHEMA_VERSION
        and value.get("artifact_type") == "schema_v7_native_case_container"
    )


__all__ = [
    "CASE_MODEL_VERSION",
    "SCHEMA_VERSION",
    "CaseBundle",
    "CaseInputError",
    "CaseModelProvenance",
    "CaseRevision",
    "CaseStatus",
    "Endpoint",
    "EndpointDirection",
    "EndpointPriority",
    "EndpointRelationship",
    "EndpointRelationshipType",
    "EndpointRole",
    "EndpointType",
    "GeneDiseaseState",
    "ProvenanceClassification",
    "QualifiedValue",
    "TherapeuticModulation",
    "UnresolvedInput",
    "V7CompatibilityAdapter",
    "ValueStatus",
    "build_case_bundle",
    "canonical_bytes",
    "content_sha256",
    "detect_schema_version",
    "initialize_case",
    "inspect_artifact",
    "is_v7_case_container",
    "validate_case_revision",
    "validation_metadata",
]
