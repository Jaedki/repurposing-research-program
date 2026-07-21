#!/usr/bin/env python3
"""Schema-v7 authoritative identity and deep-evidence records.

This module starts from immutable screened candidates and already-retained
source payloads.  It performs no retrieval, therapeutic ranking, scientific
audit, portfolio selection, persistence, orchestration, or runtime work.

Deep promotion is deliberately stricter than lightweight seed screening:
content verification is derived from retained bytes and claim-bound support
spans, while exact chemical identity is derived from concordant authoritative
assertions rather than identifier syntax or a boolean attestation.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Iterable, Mapping

from v7_case_model import canonical_bytes, content_sha256
from v7_discovery import DevelopmentStatus, EvidenceModality, StructuredCausalRoute
from v7_seed_funnel import ScreenedCandidateRecord


SCHEMA_VERSION = 7
DEEP_EVIDENCE_MODEL_VERSION = "schema-v7-deep-evidence-identity-v1"
SOURCE_RECORD_ID_RULE = "schema-v7-deep-source-record-v1"
EVIDENCE_SPAN_ID_RULE = "schema-v7-claim-evidence-span-v1"
IDENTITY_ASSERTION_ID_RULE = "schema-v7-authoritative-identity-assertion-v1"
IDENTITY_RECORD_ID_RULE = "schema-v7-authoritative-identity-record-v1"
NORMALIZED_INTERVENTION_ID_RULE = "schema-v7-deep-normalized-intervention-v1"
BREADTH_GROUP_ID_RULE = "schema-v7-deep-breadth-group-v1"
DEEP_EVIDENCE_RECORD_ID_RULE = "schema-v7-deep-evidence-record-v1"
CLAIM_ID_RULE = "schema-v7-atomic-deep-claim-v1"
PATH_ID_RULE = "schema-v7-deep-evidence-path-v1"
CORRECTION_ID_RULE = "schema-v7-record-correction-v1"
PACKAGE_ID_RULE = "schema-v7-deep-evidence-package-v1"

_SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


class DeepEvidenceError(ValueError):
    """Raised when a deep identity/evidence record violates schema v7."""


class VerificationMode(str, Enum):
    STRUCTURAL = "structural"
    ORIGINAL_CONTENT_REQUIRED = "original_content_required"


class SourceContentScope(str, Enum):
    METADATA_ONLY = "metadata_only"
    ABSTRACT_ONLY = "abstract_only"
    ORIGINAL_FULL_TEXT = "original_full_text"
    ORIGINAL_SUPPLEMENT = "original_supplement"
    ORIGINAL_TABLE = "original_table"
    ORIGINAL_FIGURE = "original_figure"
    ORIGINAL_DATABASE_RECORD = "original_database_record"
    ORIGINAL_REGULATORY_RECORD = "original_regulatory_record"


ORIGINAL_CONTENT_SCOPES = {
    SourceContentScope.ORIGINAL_FULL_TEXT,
    SourceContentScope.ORIGINAL_SUPPLEMENT,
    SourceContentScope.ORIGINAL_TABLE,
    SourceContentScope.ORIGINAL_FIGURE,
    SourceContentScope.ORIGINAL_DATABASE_RECORD,
    SourceContentScope.ORIGINAL_REGULATORY_RECORD,
}


class RetrievalMethod(str, Enum):
    SOURCE_API = "source_api"
    HTTPS_DOWNLOAD = "https_download"
    LOCAL_FROZEN_FIXTURE = "local_frozen_fixture"
    MANUAL_ARCHIVAL_IMPORT = "manual_archival_import"


class ContentVerificationMethod(str, Enum):
    RETAINED_PAYLOAD_SHA256 = "retained_payload_sha256"
    EXACT_EXCERPT_MATCH = "exact_excerpt_match"
    STRUCTURED_POINTER_MATCH = "structured_pointer_match"
    METADATA_RECEIPT_ONLY = "metadata_receipt_only"


class EvidenceSupportKind(str, Enum):
    EXACT_EXCERPT = "exact_excerpt"
    STRUCTURED_TABLE_POINTER = "structured_table_pointer"
    STRUCTURED_FIGURE_POINTER = "structured_figure_pointer"


class ChemicalEntityKind(str, Enum):
    SINGLE_COMPOUND = "single_compound"
    SALT = "salt"
    SOLVATE = "solvate"
    FIXED_COMBINATION = "fixed_combination"
    STANDARDIZED_PREPARATION = "standardized_preparation"
    MIXTURE = "mixture"
    PRODRUG = "prodrug"
    ACTIVE_METABOLITE = "active_metabolite"
    FORMULATION = "formulation"


class IdentityResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"
    QUARANTINED = "quarantined"


class CompositionStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    EXACT = "exact"
    PARTIAL = "partial"
    UNDEFINED = "undefined"


class StereochemistryStatus(str, Enum):
    FULLY_SPECIFIED = "fully_specified"
    PARTIALLY_SPECIFIED = "partially_specified"
    UNSPECIFIED = "unspecified"
    RACEMATE = "racemate"
    NOT_APPLICABLE = "not_applicable"
    UNRESOLVED = "unresolved"


class IdentityRelationshipType(str, Enum):
    SALT_OF = "salt_of"
    SOLVATE_OF = "solvate_of"
    TAUTOMER_OF = "tautomer_of"
    STEREOISOMER_OF = "stereoisomer_of"
    PRODRUG_OF = "prodrug_of"
    ACTIVE_METABOLITE_OF = "active_metabolite_of"
    DELIVERS_ACTIVE_MOIETY = "delivers_active_moiety"
    COMPONENT_OF = "component_of"
    FORMULATION_OF = "formulation_of"


class CompoundOrigin(str, Enum):
    SYNTHETIC = "synthetic"
    SEMISYNTHETIC = "semisynthetic"
    NATURAL_PRODUCT = "natural_product"
    ENDOGENOUS = "endogenous"
    NUTRIENT = "nutrient"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class HumanUseStatus(str, Enum):
    MARKETED_HUMAN_PRODUCT = "marketed_human_product"
    ADMINISTERED_IN_HUMANS = "administered_in_humans"
    HUMAN_INVESTIGATION_PLANNED = "human_investigation_planned"
    NO_DOCUMENTED_HUMAN_USE = "no_documented_human_use"
    UNKNOWN = "unknown"


class StudyDesign(str, Enum):
    RANDOMIZED_CONTROLLED_TRIAL = "randomized_controlled_trial"
    NONRANDOMIZED_INTERVENTIONAL = "nonrandomized_interventional"
    COHORT = "cohort"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES = "case_series"
    CASE_REPORT = "case_report"
    ANIMAL_INTERVENTION = "animal_intervention"
    EX_VIVO = "ex_vivo"
    ORGANOID = "organoid"
    CELLULAR = "cellular"
    BIOACTIVITY_ASSAY = "bioactivity_assay"
    COMPUTATIONAL = "computational"
    AUTHORITATIVE_DATABASE = "authoritative_database"
    OTHER = "other"


class ExperimentalModelKind(str, Enum):
    HUMAN = "human"
    HUMAN_PATIENT_CELL = "human_patient_cell"
    ANIMAL = "animal"
    ORGANOID = "organoid"
    EX_VIVO = "ex_vivo"
    CELL_LINE = "cell_line"
    CELL_FREE = "cell_free"
    COMPUTATIONAL = "computational"
    DATABASE = "database"


class ReportedValueStatus(str, Enum):
    REPORTED = "reported"
    NOT_REPORTED = "not_reported"
    NOT_APPLICABLE = "not_applicable"


class ObservedEffectDirection(str, Enum):
    BENEFIT = "benefit"
    HARM = "harm"
    INCREASE = "increase"
    DECREASE = "decrease"
    NORMALIZE = "normalize"
    NO_EFFECT = "no_effect"
    MIXED = "mixed"
    UNCLEAR = "unclear"


class RiskOfBiasLevel(str, Enum):
    LOW = "low"
    SOME_CONCERNS = "some_concerns"
    HIGH = "high"
    UNCLEAR = "unclear"
    NOT_ASSESSED = "not_assessed"


class ClaimPolarity(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    NULL = "null"
    MIXED = "mixed"
    CONTEXT_ONLY = "context_only"


class ClaimReportingStatus(str, Enum):
    REPORTED = "reported"
    INFERRED = "inferred"


class ClaimCalibration(str, Enum):
    ESTABLISHED = "established"
    SUPPORTED_WITH_QUALIFIER = "supported_with_qualifier"
    PLAUSIBLE_INFERENCE = "plausible_inference"
    SPECULATIVE = "speculative"
    UNRESOLVED = "unresolved"
    CONTRADICTED = "contradicted"


class EndpointDeepStatus(str, Enum):
    ASSESSED = "assessed"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"
    NOT_ASSESSED = "not_assessed"


class CorrectionTargetKind(str, Enum):
    SOURCE = "source"
    EVIDENCE_SPAN = "evidence_span"
    IDENTITY = "identity"
    CLAIM = "claim"
    PATH = "path"


class CorrectionAction(str, Enum):
    SUPERSEDE = "supersede"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class DeepSourceRecord:
    source_record_id: str
    source_id: str
    source_release: str
    native_record_id: str
    retrieval_content_receipt_id: str
    retained_payload_locator: str
    raw_content_sha256: str
    content_scope: SourceContentScope
    retrieval_method: RetrievalMethod
    verification_method: ContentVerificationMethod


@dataclass(frozen=True)
class StructuredEvidencePointer:
    artifact_label: str
    page_or_section: str
    coordinates: str
    cell_or_region: str
    extracted_value: str
    extraction_method: str


@dataclass(frozen=True)
class EvidenceSpan:
    evidence_span_id: str
    source_record_id: str
    source_id: str
    claim_id: str
    raw_content_sha256: str
    support_kind: EvidenceSupportKind
    stable_locator: str
    exact_excerpt: str | None
    structured_pointer: StructuredEvidencePointer | None


def _plain_record(record: Any, id_field: str) -> dict[str, Any]:
    return {field.name: getattr(record, field.name) for field in fields(record) if field.name != id_field}


def _text(value: Any, label: str) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        raise DeepEvidenceError(f"{label} is required")
    return text


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _verbatim_text(value: Any, label: str) -> str:
    """Retain source coordinates exactly while rejecting empty excerpts."""

    if not isinstance(value, str) or not value.strip():
        raise DeepEvidenceError(f"{label} is required")
    return value


def _strings(values: Iterable[str], label: str, *, required: bool = False) -> tuple[str, ...]:
    rows = tuple(sorted({_text(value, label) for value in values}))
    if required and not rows:
        raise DeepEvidenceError(f"{label} must be nonempty")
    return rows


def _stable_id(prefix: str, rule: str, projection: Any) -> str:
    return f"{prefix}-{content_sha256({'rule': rule, 'projection': projection})[:24]}"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _validate_sha256(value: str, label: str) -> str:
    normalized = _text(value, label).upper()
    if not _SHA256_RE.fullmatch(normalized):
        raise DeepEvidenceError(f"{label} must be a SHA-256 hex digest")
    return normalized


def make_deep_source_record(
    *,
    source_id: str,
    source_release: str,
    native_record_id: str,
    retrieval_content_receipt_id: str,
    retained_payload_locator: str,
    raw_content: bytes,
    content_scope: SourceContentScope,
    retrieval_method: RetrievalMethod,
    verification_method: ContentVerificationMethod = ContentVerificationMethod.RETAINED_PAYLOAD_SHA256,
) -> DeepSourceRecord:
    if not isinstance(raw_content, bytes) or not raw_content:
        raise DeepEvidenceError("raw_content must be nonempty bytes")
    body = {
        "source_id": _text(source_id, "source_id"),
        "source_release": _text(source_release, "source_release"),
        "native_record_id": _text(native_record_id, "native_record_id"),
        "retrieval_content_receipt_id": _text(
            retrieval_content_receipt_id, "retrieval_content_receipt_id"
        ),
        "retained_payload_locator": _text(retained_payload_locator, "retained_payload_locator"),
        "raw_content_sha256": _sha256_bytes(raw_content),
        "content_scope": content_scope,
        "retrieval_method": retrieval_method,
        "verification_method": verification_method,
    }
    return DeepSourceRecord(
        source_record_id=_stable_id("DEEP-SOURCE", SOURCE_RECORD_ID_RULE, body), **body
    )


def validate_deep_source_record(
    record: DeepSourceRecord,
    *,
    verification_mode: VerificationMode,
    retained_payloads: Mapping[str, bytes] | None = None,
) -> bytes | None:
    if not isinstance(record, DeepSourceRecord):
        raise DeepEvidenceError("expected DeepSourceRecord")
    for name in (
        "source_id",
        "source_release",
        "native_record_id",
        "retrieval_content_receipt_id",
        "retained_payload_locator",
    ):
        if getattr(record, name) != _text(getattr(record, name), f"source.{name}"):
            raise DeepEvidenceError(f"source.{name} is not canonical")
    _validate_sha256(record.raw_content_sha256, "source.raw_content_sha256")
    if not isinstance(record.content_scope, SourceContentScope):
        raise DeepEvidenceError("source.content_scope is invalid")
    if not isinstance(record.retrieval_method, RetrievalMethod):
        raise DeepEvidenceError("source.retrieval_method is invalid")
    if not isinstance(record.verification_method, ContentVerificationMethod):
        raise DeepEvidenceError("source.verification_method is invalid")
    expected_id = _stable_id("DEEP-SOURCE", SOURCE_RECORD_ID_RULE, _plain_record(record, "source_record_id"))
    if record.source_record_id != expected_id:
        raise DeepEvidenceError("source record content-derived ID mismatch")
    payload: bytes | None = None
    if retained_payloads is not None:
        payload = retained_payloads.get(record.retained_payload_locator)
    if verification_mode is VerificationMode.ORIGINAL_CONTENT_REQUIRED:
        if record.content_scope not in ORIGINAL_CONTENT_SCOPES:
            raise DeepEvidenceError(
                f"source {record.source_record_id}: metadata/abstract-only receipt cannot verify a deep claim"
            )
        if record.verification_method is ContentVerificationMethod.METADATA_RECEIPT_ONLY:
            raise DeepEvidenceError(
                f"source {record.source_record_id}: metadata-only verification method is insufficient"
            )
        if payload is None:
            raise DeepEvidenceError(
                f"source {record.source_record_id}: original retained payload is required in verification mode"
            )
    if payload is not None:
        if not isinstance(payload, bytes) or _sha256_bytes(payload) != record.raw_content_sha256:
            raise DeepEvidenceError(f"source {record.source_record_id}: retained payload hash mismatch")
    return payload


def make_evidence_span(
    source: DeepSourceRecord,
    *,
    claim_id: str,
    support_kind: EvidenceSupportKind,
    stable_locator: str,
    exact_excerpt: str | None = None,
    structured_pointer: StructuredEvidencePointer | None = None,
) -> EvidenceSpan:
    excerpt = None if exact_excerpt is None else _verbatim_text(exact_excerpt, "exact_excerpt")
    if support_kind is EvidenceSupportKind.EXACT_EXCERPT:
        if excerpt is None or structured_pointer is not None:
            raise DeepEvidenceError("exact-excerpt spans require only exact_excerpt")
    else:
        if excerpt is not None or not isinstance(structured_pointer, StructuredEvidencePointer):
            raise DeepEvidenceError("structured spans require only a structured pointer")
    body = {
        "source_record_id": source.source_record_id,
        "source_id": source.source_id,
        "claim_id": _text(claim_id, "claim_id"),
        "raw_content_sha256": source.raw_content_sha256,
        "support_kind": support_kind,
        "stable_locator": _text(stable_locator, "stable_locator"),
        "exact_excerpt": excerpt,
        "structured_pointer": structured_pointer,
    }
    return EvidenceSpan(
        evidence_span_id=_stable_id("EVIDENCE-SPAN", EVIDENCE_SPAN_ID_RULE, body), **body
    )


def _payload_text(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeepEvidenceError(
            f"{label}: retained binary content needs a source-specific extracted-text snapshot"
        ) from exc


def validate_evidence_span(
    span: EvidenceSpan,
    source: DeepSourceRecord,
    *,
    verification_mode: VerificationMode,
    retained_payloads: Mapping[str, bytes] | None = None,
) -> None:
    if not isinstance(span, EvidenceSpan) or span.source_record_id != source.source_record_id:
        raise DeepEvidenceError("evidence span/source link mismatch")
    if span.source_id != source.source_id or span.raw_content_sha256 != source.raw_content_sha256:
        raise DeepEvidenceError("evidence span source/hash binding mismatch")
    _text(span.claim_id, "span.claim_id")
    _text(span.stable_locator, "span.stable_locator")
    if span.support_kind is EvidenceSupportKind.EXACT_EXCERPT:
        if span.exact_excerpt is None or span.structured_pointer is not None:
            raise DeepEvidenceError("malformed exact-excerpt span")
    elif span.support_kind in {
        EvidenceSupportKind.STRUCTURED_TABLE_POINTER,
        EvidenceSupportKind.STRUCTURED_FIGURE_POINTER,
    }:
        pointer = span.structured_pointer
        if span.exact_excerpt is not None or not isinstance(pointer, StructuredEvidencePointer):
            raise DeepEvidenceError("malformed structured evidence pointer")
        for name in (
            "artifact_label",
            "page_or_section",
            "coordinates",
            "cell_or_region",
            "extracted_value",
            "extraction_method",
        ):
            _text(getattr(pointer, name), f"structured_pointer.{name}")
    else:
        raise DeepEvidenceError("unsupported evidence span kind")
    expected_id = _stable_id("EVIDENCE-SPAN", EVIDENCE_SPAN_ID_RULE, _plain_record(span, "evidence_span_id"))
    if span.evidence_span_id != expected_id:
        raise DeepEvidenceError("evidence span content-derived ID mismatch")
    if verification_mode is VerificationMode.ORIGINAL_CONTENT_REQUIRED:
        payload = validate_deep_source_record(
            source,
            verification_mode=verification_mode,
            retained_payloads=retained_payloads,
        )
        assert payload is not None
        text = _payload_text(payload, f"span {span.evidence_span_id}")
        if span.support_kind is EvidenceSupportKind.EXACT_EXCERPT:
            if span.exact_excerpt not in text:
                raise DeepEvidenceError(
                    f"span {span.evidence_span_id}: exact excerpt is absent from retained original content"
                )
        else:
            pointer = span.structured_pointer
            assert pointer is not None
            if pointer.extracted_value not in text and pointer.artifact_label not in text:
                raise DeepEvidenceError(
                    f"span {span.evidence_span_id}: table/figure pointer is not verifiable in retained content"
                )


@dataclass(frozen=True)
class CanonicalStructure:
    canonical_smiles: str
    canonical_smiles_sha256: str
    standard_inchi: str
    inchikey: str
    stereochemistry_status: StereochemistryStatus
    stereochemistry_descriptor: str
    canonicalization_method: str
    canonicalization_version: str


@dataclass(frozen=True)
class CompositionComponent:
    normalized_intervention_id: str
    role: str
    amount_or_fraction: str
    source_record_id: str
    evidence_span_id: str


@dataclass(frozen=True)
class IdentityRelationship:
    relationship_type: IdentityRelationshipType
    related_normalized_intervention_id: str
    source_record_id: str
    evidence_span_id: str
    applicability_scope: str


@dataclass(frozen=True)
class RegistryIdentityAssertion:
    assertion_id: str
    authority: str
    authority_release: str
    source_record_id: str
    evidence_span_id: str
    entity_kind: ChemicalEntityKind
    registry_identifiers: tuple[tuple[str, str], ...]
    canonical_smiles: str | None
    standard_inchi: str | None
    inchikey: str | None
    stereochemistry_status: StereochemistryStatus
    stereochemistry_descriptor: str


@dataclass(frozen=True)
class CompoundOriginAssertion:
    origin: CompoundOrigin
    source_record_id: str
    evidence_span_id: str
    rationale: str


@dataclass(frozen=True)
class HumanUseStatusAssertion:
    status: HumanUseStatus
    jurisdiction: str
    indication: str
    as_of: str
    source_record_id: str
    evidence_span_id: str


@dataclass(frozen=True)
class DevelopmentStatusAssertion:
    status: DevelopmentStatus
    jurisdiction: str
    indication: str
    as_of: str
    source_record_id: str
    evidence_span_id: str


@dataclass(frozen=True)
class ActiveMoietyMapping:
    active_moiety_id: str
    relationship_type: IdentityRelationshipType
    source_record_id: str
    evidence_span_id: str
    exact_form_scope: str


@dataclass(frozen=True)
class FormulationDescriptor:
    product_name: str
    dosage_form: str
    release_characteristic: str
    administration_routes: tuple[str, ...]
    component_ids: tuple[str, ...]
    source_record_id: str
    evidence_span_id: str


@dataclass(frozen=True)
class AuthoritativeIdentityRecord:
    identity_record_id: str
    screened_candidate_id: str
    retained_seed_ids: tuple[str, ...]
    raw_reported_identity: str
    entity_kind: ChemicalEntityKind
    resolution_status: IdentityResolutionStatus
    normalized_intervention_id: str | None
    breadth_group_id: str | None
    canonical_structure: CanonicalStructure | None
    composition_status: CompositionStatus
    components: tuple[CompositionComponent, ...]
    registry_assertions: tuple[RegistryIdentityAssertion, ...]
    relationships: tuple[IdentityRelationship, ...]
    formulation: FormulationDescriptor | None
    compound_origin_assertions: tuple[CompoundOriginAssertion, ...]
    human_use_status_assertions: tuple[HumanUseStatusAssertion, ...]
    development_status_assertions: tuple[DevelopmentStatusAssertion, ...]
    active_moiety_mappings: tuple[ActiveMoietyMapping, ...]
    identity_conflicts: tuple[str, ...]
    unresolved_reasons: tuple[str, ...]
    deep_identity_eligible: bool
    normalization_policy_version: str


def make_registry_identity_assertion(
    *,
    authority: str,
    authority_release: str,
    source_record_id: str,
    evidence_span_id: str,
    entity_kind: ChemicalEntityKind,
    registry_identifiers: Mapping[str, str] | Iterable[tuple[str, str]],
    canonical_smiles: str | None,
    standard_inchi: str | None,
    inchikey: str | None,
    stereochemistry_status: StereochemistryStatus,
    stereochemistry_descriptor: str = "not_reported",
) -> RegistryIdentityAssertion:
    pairs = registry_identifiers.items() if isinstance(registry_identifiers, Mapping) else registry_identifiers
    identifiers = tuple(
        sorted(
            {
                (_text(namespace, "registry namespace").upper(), _text(identifier, "registry identifier"))
                for namespace, identifier in pairs
            }
        )
    )
    if not identifiers:
        raise DeepEvidenceError("registry identity assertion needs at least one identifier")
    smiles = _optional_text(canonical_smiles, "canonical_smiles")
    inchi = _optional_text(standard_inchi, "standard_inchi")
    key = _optional_text(inchikey, "inchikey")
    if key is not None:
        key = key.upper()
        if not _INCHIKEY_RE.fullmatch(key):
            raise DeepEvidenceError("InChIKey syntax is invalid")
    body = {
        "authority": _text(authority, "authority"),
        "authority_release": _text(authority_release, "authority_release"),
        "source_record_id": _text(source_record_id, "source_record_id"),
        "evidence_span_id": _text(evidence_span_id, "evidence_span_id"),
        "entity_kind": entity_kind,
        "registry_identifiers": identifiers,
        "canonical_smiles": smiles,
        "standard_inchi": inchi,
        "inchikey": key,
        "stereochemistry_status": stereochemistry_status,
        "stereochemistry_descriptor": _text(
            stereochemistry_descriptor, "stereochemistry_descriptor"
        ),
    }
    return RegistryIdentityAssertion(
        assertion_id=_stable_id("IDENTITY-ASSERTION", IDENTITY_ASSERTION_ID_RULE, body), **body
    )


def _canonical_structure_from_assertions(
    assertions: tuple[RegistryIdentityAssertion, ...],
    *,
    canonicalization_method: str,
    canonicalization_version: str,
) -> tuple[CanonicalStructure | None, tuple[str, ...], tuple[str, ...]]:
    structural = tuple(
        row
        for row in assertions
        if row.canonical_smiles is not None and row.standard_inchi is not None and row.inchikey is not None
    )
    fingerprints: dict[tuple[Any, ...], list[RegistryIdentityAssertion]] = defaultdict(list)
    for row in structural:
        fingerprints[
            (
                row.canonical_smiles,
                row.standard_inchi,
                row.inchikey,
                row.stereochemistry_status,
                row.stereochemistry_descriptor,
            )
        ].append(row)
    conflicts: list[str] = []
    if len(fingerprints) != 1:
        for fingerprint, rows in sorted(fingerprints.items(), key=lambda item: canonical_bytes(item[0])):
            conflicts.append(
                "Authorities "
                + ", ".join(sorted({row.authority for row in rows}))
                + f" assert structure fingerprint {fingerprint!r}."
            )
        return None, tuple(conflicts), ()
    if (
        len({row.authority.casefold() for row in structural}) < 2
        or len({row.source_record_id for row in structural}) < 2
    ):
        return None, (), ("Fewer than two independent authoritative structure assertions agree.",)
    fingerprint = next(iter(fingerprints))
    smiles, inchi, key, stereo_status, stereo_descriptor = fingerprint
    structure = CanonicalStructure(
        canonical_smiles=smiles,
        canonical_smiles_sha256=_sha256_bytes(smiles.encode("utf-8")),
        standard_inchi=inchi,
        inchikey=key,
        stereochemistry_status=stereo_status,
        stereochemistry_descriptor=stereo_descriptor,
        canonicalization_method=_text(canonicalization_method, "canonicalization_method"),
        canonicalization_version=_text(canonicalization_version, "canonicalization_version"),
    )
    if structure.stereochemistry_status in {
        StereochemistryStatus.PARTIALLY_SPECIFIED,
        StereochemistryStatus.UNSPECIFIED,
        StereochemistryStatus.UNRESOLVED,
    }:
        return None, (), ("Decision-relevant stereochemistry is not fully resolved.",)
    return structure, (), ()


def _component_projection(components: tuple[CompositionComponent, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "normalized_intervention_id": row.normalized_intervention_id,
            "role": row.role,
            "amount_or_fraction": row.amount_or_fraction,
        }
        for row in components
    )


def _active_moiety_breadth_projection(
    entity_kind: ChemicalEntityKind,
    normalized_intervention_id: str,
    mappings: tuple[ActiveMoietyMapping, ...],
) -> Any:
    active_ids = {row.active_moiety_id for row in mappings}
    collapsible = entity_kind in {
        ChemicalEntityKind.SALT,
        ChemicalEntityKind.SOLVATE,
        ChemicalEntityKind.FORMULATION,
    }
    if collapsible and len(active_ids) == 1:
        return {"active_moiety_id": next(iter(active_ids))}
    return {"active_moiety_id": normalized_intervention_id}


def normalize_authoritative_identity(
    screened_candidate: ScreenedCandidateRecord,
    *,
    raw_reported_identity: str,
    entity_kind: ChemicalEntityKind,
    registry_assertions: Iterable[RegistryIdentityAssertion],
    composition_status: CompositionStatus = CompositionStatus.NOT_APPLICABLE,
    components: Iterable[CompositionComponent] = (),
    relationships: Iterable[IdentityRelationship] = (),
    formulation: FormulationDescriptor | None = None,
    compound_origin_assertions: Iterable[CompoundOriginAssertion] = (),
    human_use_status_assertions: Iterable[HumanUseStatusAssertion] = (),
    development_status_assertions: Iterable[DevelopmentStatusAssertion] = (),
    active_moiety_mappings: Iterable[ActiveMoietyMapping] = (),
    canonicalization_method: str = "authority-reported-standard-structure",
    canonicalization_version: str = "v1",
    normalization_policy_version: str = "schema-v7-authoritative-identity-policy-v1",
) -> AuthoritativeIdentityRecord:
    if not isinstance(screened_candidate, ScreenedCandidateRecord):
        raise DeepEvidenceError("authoritative normalization requires a ScreenedCandidateRecord")
    assertions = tuple(sorted(set(registry_assertions), key=lambda row: row.assertion_id))
    component_rows = tuple(
        sorted(set(components), key=lambda row: (row.normalized_intervention_id, row.role))
    )
    relationship_rows = tuple(
        sorted(
            set(relationships),
            key=lambda row: (row.relationship_type.value, row.related_normalized_intervention_id),
        )
    )
    active_rows = tuple(
        sorted(set(active_moiety_mappings), key=lambda row: (row.active_moiety_id, row.source_record_id))
    )
    conflicts: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    structure: CanonicalStructure | None = None
    single_structure_kinds = {
        ChemicalEntityKind.SINGLE_COMPOUND,
        ChemicalEntityKind.SALT,
        ChemicalEntityKind.SOLVATE,
        ChemicalEntityKind.PRODRUG,
        ChemicalEntityKind.ACTIVE_METABOLITE,
    }
    if entity_kind in single_structure_kinds:
        structure, conflicts, unresolved = _canonical_structure_from_assertions(
            assertions,
            canonicalization_method=canonicalization_method,
            canonicalization_version=canonicalization_version,
        )
        if composition_status is not CompositionStatus.NOT_APPLICABLE or component_rows:
            conflicts = (*conflicts, "A single-structure entity cannot also claim mixture composition.")
    else:
        if composition_status is not CompositionStatus.EXACT or not component_rows:
            unresolved = (
                "Combination, preparation, mixture, or formulation lacks exact component composition.",
            )
        if entity_kind is ChemicalEntityKind.FORMULATION and formulation is None:
            unresolved = (*unresolved, "Formulation identity lacks an exact product descriptor.")
        if entity_kind is ChemicalEntityKind.MIXTURE and composition_status is not CompositionStatus.EXACT:
            unresolved = (*unresolved, "Undefined mixtures cannot advance to deep-candidate identity.")
        if (
            len({row.authority.casefold() for row in assertions}) < 2
            or len({row.source_record_id for row in assertions}) < 2
        ):
            unresolved = (*unresolved, "Fewer than two independent authorities cross-check the exact entity.")

    if any(row.entity_kind is not entity_kind for row in assertions):
        conflicts = (*conflicts, "Authoritative registries disagree on the intervention entity kind.")
    if conflicts:
        status = IdentityResolutionStatus.CONFLICTING
    elif unresolved:
        status = IdentityResolutionStatus.UNRESOLVED
    else:
        status = IdentityResolutionStatus.RESOLVED

    normalized_intervention_id: str | None = None
    breadth_group_id: str | None = None
    eligible = status is IdentityResolutionStatus.RESOLVED
    if eligible:
        identity_projection = {
            "entity_kind": entity_kind,
            "canonical_structure": structure,
            "composition": _component_projection(component_rows),
            "formulation": formulation,
        }
        normalized_intervention_id = _stable_id(
            "NORMALIZED-INTERVENTION", NORMALIZED_INTERVENTION_ID_RULE, identity_projection
        )
        breadth_group_id = _stable_id(
            "BREADTH-GROUP",
            BREADTH_GROUP_ID_RULE,
            _active_moiety_breadth_projection(entity_kind, normalized_intervention_id, active_rows),
        )
    body = {
        "screened_candidate_id": screened_candidate.screened_candidate_id,
        "retained_seed_ids": tuple(sorted(set(screened_candidate.source_seed_ids))),
        "raw_reported_identity": _text(raw_reported_identity, "raw_reported_identity"),
        "entity_kind": entity_kind,
        "resolution_status": status,
        "normalized_intervention_id": normalized_intervention_id,
        "breadth_group_id": breadth_group_id,
        "canonical_structure": structure,
        "composition_status": composition_status,
        "components": component_rows,
        "registry_assertions": assertions,
        "relationships": relationship_rows,
        "formulation": formulation,
        "compound_origin_assertions": tuple(sorted(set(compound_origin_assertions), key=canonical_bytes)),
        "human_use_status_assertions": tuple(sorted(set(human_use_status_assertions), key=canonical_bytes)),
        "development_status_assertions": tuple(
            sorted(set(development_status_assertions), key=canonical_bytes)
        ),
        "active_moiety_mappings": active_rows,
        "identity_conflicts": _strings(conflicts, "identity_conflicts"),
        "unresolved_reasons": _strings(unresolved, "unresolved_reasons"),
        "deep_identity_eligible": eligible,
        "normalization_policy_version": _text(
            normalization_policy_version, "normalization_policy_version"
        ),
    }
    return AuthoritativeIdentityRecord(
        identity_record_id=_stable_id("IDENTITY", IDENTITY_RECORD_ID_RULE, body), **body
    )


def _validate_source_span_reference(
    source_record_id: str,
    evidence_span_id: str,
    sources: Mapping[str, DeepSourceRecord],
    spans: Mapping[str, EvidenceSpan],
) -> None:
    source = sources.get(source_record_id)
    span = spans.get(evidence_span_id)
    if source is None or span is None or span.source_record_id != source_record_id:
        raise DeepEvidenceError("provenance source/evidence-span link does not resolve")


def validate_authoritative_identity(
    record: AuthoritativeIdentityRecord,
    screened_candidate: ScreenedCandidateRecord,
    *,
    sources: Mapping[str, DeepSourceRecord] | None = None,
    spans: Mapping[str, EvidenceSpan] | None = None,
) -> None:
    if not isinstance(record, AuthoritativeIdentityRecord):
        raise DeepEvidenceError("expected AuthoritativeIdentityRecord")
    if record.screened_candidate_id != screened_candidate.screened_candidate_id:
        raise DeepEvidenceError("identity/screened-candidate link mismatch")
    if record.retained_seed_ids != tuple(sorted(set(screened_candidate.source_seed_ids))):
        raise DeepEvidenceError("authoritative identity did not preserve every source seed")
    if not isinstance(record.entity_kind, ChemicalEntityKind) or not isinstance(
        record.resolution_status, IdentityResolutionStatus
    ):
        raise DeepEvidenceError("identity controlled values are invalid")
    if not isinstance(record.composition_status, CompositionStatus):
        raise DeepEvidenceError("identity composition status is invalid")
    if tuple(sorted(record.registry_assertions, key=lambda row: row.assertion_id)) != record.registry_assertions:
        raise DeepEvidenceError("identity assertions are not canonical")
    for assertion in record.registry_assertions:
        rebuilt = make_registry_identity_assertion(
            authority=assertion.authority,
            authority_release=assertion.authority_release,
            source_record_id=assertion.source_record_id,
            evidence_span_id=assertion.evidence_span_id,
            entity_kind=assertion.entity_kind,
            registry_identifiers=assertion.registry_identifiers,
            canonical_smiles=assertion.canonical_smiles,
            standard_inchi=assertion.standard_inchi,
            inchikey=assertion.inchikey,
            stereochemistry_status=assertion.stereochemistry_status,
            stereochemistry_descriptor=assertion.stereochemistry_descriptor,
        )
        if canonical_bytes(rebuilt) != canonical_bytes(assertion):
            raise DeepEvidenceError("identity assertion differs from its source facts")
        if sources is not None and spans is not None:
            _validate_source_span_reference(
                assertion.source_record_id, assertion.evidence_span_id, sources, spans
            )
            support_span = spans[assertion.evidence_span_id]
            support_text = (
                support_span.exact_excerpt
                if support_span.exact_excerpt is not None
                else support_span.structured_pointer.extracted_value
                if support_span.structured_pointer is not None
                else ""
            )
            structure_facts = tuple(
                value
                for value in (
                    assertion.canonical_smiles,
                    assertion.standard_inchi,
                    assertion.inchikey,
                )
                if value is not None
            )
            if structure_facts and any(value not in support_text for value in structure_facts):
                raise DeepEvidenceError(
                    "authoritative structure assertion is not present in its exact support span"
                )
            if not structure_facts and not any(
                identifier in support_text
                for _, identifier in assertion.registry_identifiers
            ):
                raise DeepEvidenceError(
                    "structureless product/preparation assertion lacks an exact identifier in its support span"
                )
    for component in record.components:
        for name in ("normalized_intervention_id", "role", "amount_or_fraction"):
            _text(getattr(component, name), f"composition component {name}")
    for relationship in record.relationships:
        if not isinstance(relationship.relationship_type, IdentityRelationshipType):
            raise DeepEvidenceError("identity relationship type is invalid")
        _text(
            relationship.related_normalized_intervention_id,
            "relationship related_normalized_intervention_id",
        )
        _text(relationship.applicability_scope, "relationship applicability_scope")
    for assertion in record.compound_origin_assertions:
        if not isinstance(assertion.origin, CompoundOrigin):
            raise DeepEvidenceError("compound-origin assertion is invalid")
        _text(assertion.rationale, "compound-origin rationale")
    for assertion in record.human_use_status_assertions:
        if not isinstance(assertion.status, HumanUseStatus):
            raise DeepEvidenceError("human-use-status assertion is invalid")
        for name in ("jurisdiction", "indication", "as_of"):
            _text(getattr(assertion, name), f"human-use-status {name}")
    for assertion in record.development_status_assertions:
        if not isinstance(assertion.status, DevelopmentStatus):
            raise DeepEvidenceError("development-status assertion is invalid")
        for name in ("jurisdiction", "indication", "as_of"):
            _text(getattr(assertion, name), f"development-status {name}")
    for mapping in record.active_moiety_mappings:
        if not isinstance(mapping.relationship_type, IdentityRelationshipType):
            raise DeepEvidenceError("active-moiety relationship type is invalid")
        _text(mapping.active_moiety_id, "active_moiety_id")
        _text(mapping.exact_form_scope, "active-moiety exact_form_scope")
    if record.formulation is not None:
        for name in ("product_name", "dosage_form", "release_characteristic"):
            _text(getattr(record.formulation, name), f"formulation {name}")
        if record.formulation.administration_routes != _strings(
            record.formulation.administration_routes,
            "formulation administration_routes",
            required=True,
        ):
            raise DeepEvidenceError("formulation routes are not canonical")
        if record.formulation.component_ids != _strings(
            record.formulation.component_ids, "formulation component_ids", required=True
        ):
            raise DeepEvidenceError("formulation component IDs are not canonical")
    provenance_rows = (
        *record.compound_origin_assertions,
        *record.human_use_status_assertions,
        *record.development_status_assertions,
        *record.active_moiety_mappings,
        *record.components,
        *record.relationships,
    )
    if record.formulation is not None:
        provenance_rows = (*provenance_rows, record.formulation)
    if sources is not None and spans is not None:
        for row in provenance_rows:
            _validate_source_span_reference(row.source_record_id, row.evidence_span_id, sources, spans)
    rebuilt = normalize_authoritative_identity(
        screened_candidate,
        raw_reported_identity=record.raw_reported_identity,
        entity_kind=record.entity_kind,
        registry_assertions=record.registry_assertions,
        composition_status=record.composition_status,
        components=record.components,
        relationships=record.relationships,
        formulation=record.formulation,
        compound_origin_assertions=record.compound_origin_assertions,
        human_use_status_assertions=record.human_use_status_assertions,
        development_status_assertions=record.development_status_assertions,
        active_moiety_mappings=record.active_moiety_mappings,
        canonicalization_method=(
            record.canonical_structure.canonicalization_method
            if record.canonical_structure is not None
            else "authority-reported-standard-structure"
        ),
        canonicalization_version=(
            record.canonical_structure.canonicalization_version
            if record.canonical_structure is not None
            else "v1"
        ),
        normalization_policy_version=record.normalization_policy_version,
    )
    if canonical_bytes(rebuilt) != canonical_bytes(record):
        raise DeepEvidenceError("identity record is not the deterministic authoritative normalization")
    if record.resolution_status is IdentityResolutionStatus.RESOLVED:
        if not record.deep_identity_eligible or not record.normalized_intervention_id:
            raise DeepEvidenceError("resolved identity is not deep-eligible")
        if record.canonical_structure is not None:
            if record.canonical_structure.canonical_smiles_sha256 != _sha256_bytes(
                record.canonical_structure.canonical_smiles.encode("utf-8")
            ):
                raise DeepEvidenceError("canonical-SMILES hash mismatch")
            if not _INCHIKEY_RE.fullmatch(record.canonical_structure.inchikey):
                raise DeepEvidenceError("resolved InChIKey syntax is invalid")
    elif record.deep_identity_eligible or record.normalized_intervention_id is not None:
        raise DeepEvidenceError("unresolved/conflicting identity cannot be marked deep-eligible")


@dataclass(frozen=True)
class ReportedQuantity:
    status: ReportedValueStatus
    value: str | None
    unit: str | None
    note: str


@dataclass(frozen=True)
class ReportedText:
    status: ReportedValueStatus
    value: str | None
    note: str


@dataclass(frozen=True)
class PopulationOrExperimentalModel:
    model_kind: ExperimentalModelKind
    description: str
    species: str
    inclusion_or_genotype: str
    disease_stage: str


@dataclass(frozen=True)
class Comparator:
    status: ReportedValueStatus
    description: str | None
    matched_conditions: str


@dataclass(frozen=True)
class EffectMagnitude:
    status: ReportedValueStatus
    measure: str | None
    estimate: str | None
    unit: str | None
    timepoint_or_subgroup: str
    adjusted: str


@dataclass(frozen=True)
class StatisticalUncertainty:
    status: ReportedValueStatus
    interval: str | None
    standard_error: str | None
    p_value: str | None
    multiplicity_or_model: str


@dataclass(frozen=True)
class RiskOfBiasAssessment:
    level: RiskOfBiasLevel
    tool_or_framework: str
    domains: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class ClaimScope:
    case_revision_id: str
    population: str
    disease_stage: str
    tissue_or_cell_type: str
    dose_or_concentration: str
    administration_route: str
    duration_or_timepoint: str
    endpoint_id: str


@dataclass(frozen=True)
class AtomicClaimCore:
    claim_id: str
    candidate_id: str
    proposition: str
    polarity: ClaimPolarity
    reporting_status: ClaimReportingStatus
    evidence_modality: EvidenceModality
    scope: ClaimScope
    calibration: ClaimCalibration
    uncertainty: tuple[str, ...]


@dataclass(frozen=True)
class DeepClaimRecord:
    claim_id: str
    candidate_id: str
    proposition: str
    polarity: ClaimPolarity
    reporting_status: ClaimReportingStatus
    evidence_modality: EvidenceModality
    scope: ClaimScope
    calibration: ClaimCalibration
    uncertainty: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeepEvidenceRecord:
    deep_evidence_record_id: str
    claim_id: str
    source_id: str
    source_record_id: str
    evidence_span_id: str
    exact_excerpt: str | None
    structured_pointer: StructuredEvidencePointer | None
    raw_content_sha256: str
    retrieval_method: RetrievalMethod
    verification_method: ContentVerificationMethod
    study_design: StudyDesign
    population_or_experimental_model: PopulationOrExperimentalModel
    sample_size: ReportedQuantity
    comparator: Comparator
    dose: ReportedQuantity
    administration_route: ReportedText
    duration: ReportedText
    tissue_or_cell_type: ReportedText
    exposure_or_concentration: ReportedQuantity
    endpoint_id: str
    endpoint_measure: str
    effect_direction: ObservedEffectDirection
    effect_magnitude: EffectMagnitude
    statistical_uncertainty: StatisticalUncertainty
    study_limitations: tuple[str, ...]
    risk_of_bias_assessment: RiskOfBiasAssessment
    claim_calibration: ClaimCalibration


@dataclass(frozen=True)
class DeepEvidencePath:
    path_id: str
    candidate_id: str
    structured_route_id: str
    endpoint_id: str
    claim_ids: tuple[str, ...]
    evidence_record_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeepEndpointAssessment:
    endpoint_id: str
    status: EndpointDeepStatus
    reason: str
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class RecordCorrection:
    correction_id: str
    target_kind: CorrectionTargetKind
    target_id: str
    action: CorrectionAction
    replacement_id: str | None
    reason: str
    provenance_source_ids: tuple[str, ...]
    provenance_evidence_span_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeepEvidencePackage:
    schema_version: int
    model_version: str
    package_id: str
    screened_candidate: ScreenedCandidateRecord
    identity_records: tuple[AuthoritativeIdentityRecord, ...]
    current_identity_record_id: str | None
    sources: tuple[DeepSourceRecord, ...]
    evidence_spans: tuple[EvidenceSpan, ...]
    evidence_records: tuple[DeepEvidenceRecord, ...]
    claims: tuple[DeepClaimRecord, ...]
    paths: tuple[DeepEvidencePath, ...]
    endpoint_assessments: tuple[DeepEndpointAssessment, ...]
    corrections: tuple[RecordCorrection, ...]


@dataclass(frozen=True)
class DeepCandidateRecord:
    candidate_id: str
    deep_evidence_package_id: str
    identity_record_id: str
    normalized_intervention_id: str
    endpoint_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    path_ids: tuple[str, ...]


def reported_quantity(value: str, unit: str, *, note: str = "reported") -> ReportedQuantity:
    return ReportedQuantity(
        status=ReportedValueStatus.REPORTED,
        value=_text(value, "reported quantity value"),
        unit=_text(unit, "reported quantity unit"),
        note=_text(note, "reported quantity note"),
    )


def missing_quantity(*, not_applicable: bool = False, reason: str) -> ReportedQuantity:
    return ReportedQuantity(
        status=(
            ReportedValueStatus.NOT_APPLICABLE
            if not_applicable
            else ReportedValueStatus.NOT_REPORTED
        ),
        value=None,
        unit=None,
        note=_text(reason, "missing quantity reason"),
    )


def reported_text(value: str, *, note: str = "reported") -> ReportedText:
    return ReportedText(
        status=ReportedValueStatus.REPORTED,
        value=_text(value, "reported text value"),
        note=_text(note, "reported text note"),
    )


def missing_text(*, not_applicable: bool = False, reason: str) -> ReportedText:
    return ReportedText(
        status=(
            ReportedValueStatus.NOT_APPLICABLE
            if not_applicable
            else ReportedValueStatus.NOT_REPORTED
        ),
        value=None,
        note=_text(reason, "missing text reason"),
    )


def _validate_reported_quantity(value: ReportedQuantity, label: str) -> None:
    if not isinstance(value, ReportedQuantity) or not isinstance(value.status, ReportedValueStatus):
        raise DeepEvidenceError(f"{label} is malformed")
    _text(value.note, f"{label}.note")
    if value.status is ReportedValueStatus.REPORTED:
        _text(value.value, f"{label}.value")
        _text(value.unit, f"{label}.unit")
    elif value.value is not None or value.unit is not None:
        raise DeepEvidenceError(f"{label}: unreported values cannot carry an estimate or unit")


def _validate_reported_text(value: ReportedText, label: str) -> None:
    if not isinstance(value, ReportedText) or not isinstance(value.status, ReportedValueStatus):
        raise DeepEvidenceError(f"{label} is malformed")
    _text(value.note, f"{label}.note")
    if value.status is ReportedValueStatus.REPORTED:
        _text(value.value, f"{label}.value")
    elif value.value is not None:
        raise DeepEvidenceError(f"{label}: unreported text cannot carry a value")


def make_atomic_claim_core(
    *,
    candidate_id: str,
    proposition: str,
    polarity: ClaimPolarity,
    reporting_status: ClaimReportingStatus,
    evidence_modality: EvidenceModality,
    scope: ClaimScope,
    calibration: ClaimCalibration,
    uncertainty: Iterable[str],
) -> AtomicClaimCore:
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "proposition": _text(proposition, "claim proposition"),
        "polarity": polarity,
        "reporting_status": reporting_status,
        "evidence_modality": evidence_modality,
        "scope": scope,
        "calibration": calibration,
        "uncertainty": _strings(uncertainty, "claim uncertainty", required=True),
    }
    return AtomicClaimCore(claim_id=_stable_id("DEEP-CLAIM", CLAIM_ID_RULE, body), **body)


def bind_atomic_claim(
    core: AtomicClaimCore, *, evidence_record_ids: Iterable[str]
) -> DeepClaimRecord:
    if not isinstance(core, AtomicClaimCore):
        raise DeepEvidenceError("expected AtomicClaimCore")
    return DeepClaimRecord(
        **{field.name: getattr(core, field.name) for field in fields(core)},
        evidence_record_ids=_strings(
            evidence_record_ids, "claim evidence_record_ids", required=True
        ),
    )


def make_deep_evidence_record(
    claim: AtomicClaimCore,
    source: DeepSourceRecord,
    span: EvidenceSpan,
    *,
    study_design: StudyDesign,
    population_or_experimental_model: PopulationOrExperimentalModel,
    sample_size: ReportedQuantity,
    comparator: Comparator,
    dose: ReportedQuantity,
    administration_route: ReportedText,
    duration: ReportedText,
    tissue_or_cell_type: ReportedText,
    exposure_or_concentration: ReportedQuantity,
    endpoint_measure: str,
    effect_direction: ObservedEffectDirection,
    effect_magnitude: EffectMagnitude,
    statistical_uncertainty: StatisticalUncertainty,
    study_limitations: Iterable[str],
    risk_of_bias_assessment: RiskOfBiasAssessment,
) -> DeepEvidenceRecord:
    if span.claim_id != claim.claim_id:
        raise DeepEvidenceError("evidence span is bound to a different atomic claim")
    if span.source_record_id != source.source_record_id:
        raise DeepEvidenceError("evidence span is bound to a different source")
    body = {
        "claim_id": claim.claim_id,
        "source_id": source.source_id,
        "source_record_id": source.source_record_id,
        "evidence_span_id": span.evidence_span_id,
        "exact_excerpt": span.exact_excerpt,
        "structured_pointer": span.structured_pointer,
        "raw_content_sha256": source.raw_content_sha256,
        "retrieval_method": source.retrieval_method,
        "verification_method": (
            ContentVerificationMethod.EXACT_EXCERPT_MATCH
            if span.support_kind is EvidenceSupportKind.EXACT_EXCERPT
            else ContentVerificationMethod.STRUCTURED_POINTER_MATCH
        ),
        "study_design": study_design,
        "population_or_experimental_model": population_or_experimental_model,
        "sample_size": sample_size,
        "comparator": comparator,
        "dose": dose,
        "administration_route": administration_route,
        "duration": duration,
        "tissue_or_cell_type": tissue_or_cell_type,
        "exposure_or_concentration": exposure_or_concentration,
        "endpoint_id": claim.scope.endpoint_id,
        "endpoint_measure": _text(endpoint_measure, "endpoint_measure"),
        "effect_direction": effect_direction,
        "effect_magnitude": effect_magnitude,
        "statistical_uncertainty": statistical_uncertainty,
        "study_limitations": _strings(
            study_limitations, "study_limitations", required=True
        ),
        "risk_of_bias_assessment": risk_of_bias_assessment,
        "claim_calibration": claim.calibration,
    }
    return DeepEvidenceRecord(
        deep_evidence_record_id=_stable_id(
            "DEEP-EVIDENCE", DEEP_EVIDENCE_RECORD_ID_RULE, body
        ),
        **body,
    )


def make_deep_evidence_path(
    *,
    candidate_id: str,
    structured_route_id: str,
    endpoint_id: str,
    claim_ids: Iterable[str],
    evidence_record_ids: Iterable[str],
) -> DeepEvidencePath:
    body = {
        "candidate_id": _text(candidate_id, "candidate_id"),
        "structured_route_id": _text(structured_route_id, "structured_route_id"),
        "endpoint_id": _text(endpoint_id, "endpoint_id"),
        "claim_ids": _strings(claim_ids, "path claim_ids", required=True),
        "evidence_record_ids": _strings(
            evidence_record_ids, "path evidence_record_ids", required=True
        ),
    }
    return DeepEvidencePath(path_id=_stable_id("DEEP-PATH", PATH_ID_RULE, body), **body)


def make_record_correction(
    *,
    target_kind: CorrectionTargetKind,
    target_id: str,
    action: CorrectionAction,
    replacement_id: str | None,
    reason: str,
    provenance_source_ids: Iterable[str],
    provenance_evidence_span_ids: Iterable[str],
) -> RecordCorrection:
    replacement = _optional_text(replacement_id, "replacement_id")
    if action is CorrectionAction.SUPERSEDE and replacement is None:
        raise DeepEvidenceError("supersession requires a replacement record")
    if action is CorrectionAction.QUARANTINE and replacement is not None:
        raise DeepEvidenceError("quarantine cannot silently replace its target")
    body = {
        "target_kind": target_kind,
        "target_id": _text(target_id, "target_id"),
        "action": action,
        "replacement_id": replacement,
        "reason": _text(reason, "correction reason"),
        "provenance_source_ids": _strings(
            provenance_source_ids, "correction provenance_source_ids", required=True
        ),
        "provenance_evidence_span_ids": _strings(
            provenance_evidence_span_ids,
            "correction provenance_evidence_span_ids",
            required=True,
        ),
    }
    return RecordCorrection(
        correction_id=_stable_id("CORRECTION", CORRECTION_ID_RULE, body), **body
    )


def _validate_effect_magnitude(value: EffectMagnitude) -> None:
    if not isinstance(value, EffectMagnitude) or not isinstance(value.status, ReportedValueStatus):
        raise DeepEvidenceError("effect magnitude is malformed")
    _text(value.timepoint_or_subgroup, "effect timepoint_or_subgroup")
    _text(value.adjusted, "effect adjusted status")
    numeric_fields = (value.measure, value.estimate, value.unit)
    if value.status is ReportedValueStatus.REPORTED:
        for field_value, label in zip(numeric_fields, ("measure", "estimate", "unit")):
            _text(field_value, f"effect {label}")
    elif any(field_value is not None for field_value in numeric_fields):
        raise DeepEvidenceError("unreported effect magnitude cannot carry invented numeric fields")


def _validate_statistical_uncertainty(value: StatisticalUncertainty) -> None:
    if not isinstance(value, StatisticalUncertainty) or not isinstance(
        value.status, ReportedValueStatus
    ):
        raise DeepEvidenceError("statistical uncertainty is malformed")
    _text(value.multiplicity_or_model, "statistical uncertainty model")
    reported = (value.interval, value.standard_error, value.p_value)
    if value.status is ReportedValueStatus.REPORTED:
        if not any(item is not None and str(item).strip() for item in reported):
            raise DeepEvidenceError("reported statistical uncertainty needs an interval, error, or p-value")
        for item in reported:
            if item is not None:
                _text(item, "statistical uncertainty value")
    elif any(item is not None for item in reported):
        raise DeepEvidenceError("unreported statistical uncertainty cannot carry invented values")


def _validate_deep_evidence_record(
    record: DeepEvidenceRecord,
    claim: DeepClaimRecord,
    source: DeepSourceRecord,
    span: EvidenceSpan,
) -> None:
    if record.claim_id != claim.claim_id or span.claim_id != claim.claim_id:
        raise DeepEvidenceError("deep evidence is not claim-specific")
    if (
        record.source_record_id != source.source_record_id
        or record.source_id != source.source_id
        or record.evidence_span_id != span.evidence_span_id
    ):
        raise DeepEvidenceError("deep evidence source/span linkage mismatch")
    if record.raw_content_sha256 != source.raw_content_sha256:
        raise DeepEvidenceError("deep evidence raw-content hash mismatch")
    if record.exact_excerpt != span.exact_excerpt or record.structured_pointer != span.structured_pointer:
        raise DeepEvidenceError("deep evidence support differs from its evidence span")
    if record.retrieval_method is not source.retrieval_method:
        raise DeepEvidenceError("deep evidence retrieval method differs from source receipt")
    expected_verification = (
        ContentVerificationMethod.EXACT_EXCERPT_MATCH
        if span.support_kind is EvidenceSupportKind.EXACT_EXCERPT
        else ContentVerificationMethod.STRUCTURED_POINTER_MATCH
    )
    if record.verification_method is not expected_verification:
        raise DeepEvidenceError("deep evidence verification method does not match its span")
    if record.endpoint_id != claim.scope.endpoint_id or record.claim_calibration is not claim.calibration:
        raise DeepEvidenceError("deep evidence endpoint/calibration differs from its claim")
    if not isinstance(record.study_design, StudyDesign):
        raise DeepEvidenceError("study design is invalid")
    model = record.population_or_experimental_model
    if not isinstance(model, PopulationOrExperimentalModel) or not isinstance(
        model.model_kind, ExperimentalModelKind
    ):
        raise DeepEvidenceError("population/experimental model is malformed")
    for name in ("description", "species", "inclusion_or_genotype", "disease_stage"):
        _text(getattr(model, name), f"population_or_model.{name}")
    _validate_reported_quantity(record.sample_size, "sample_size")
    comparator = record.comparator
    if not isinstance(comparator, Comparator) or not isinstance(
        comparator.status, ReportedValueStatus
    ):
        raise DeepEvidenceError("comparator is malformed")
    _text(comparator.matched_conditions, "comparator.matched_conditions")
    if comparator.status is ReportedValueStatus.REPORTED:
        _text(comparator.description, "comparator.description")
    elif comparator.description is not None:
        raise DeepEvidenceError("unreported comparator cannot carry a description")
    _validate_reported_quantity(record.dose, "dose")
    _validate_reported_text(record.administration_route, "administration_route")
    _validate_reported_text(record.duration, "duration")
    _validate_reported_text(record.tissue_or_cell_type, "tissue_or_cell_type")
    _validate_reported_quantity(record.exposure_or_concentration, "exposure_or_concentration")
    _text(record.endpoint_measure, "endpoint_measure")
    if not isinstance(record.effect_direction, ObservedEffectDirection):
        raise DeepEvidenceError("effect direction is invalid")
    _validate_effect_magnitude(record.effect_magnitude)
    _validate_statistical_uncertainty(record.statistical_uncertainty)
    if record.study_limitations != _strings(
        record.study_limitations, "study_limitations", required=True
    ):
        raise DeepEvidenceError("study limitations are not canonical")
    bias = record.risk_of_bias_assessment
    if not isinstance(bias, RiskOfBiasAssessment) or not isinstance(bias.level, RiskOfBiasLevel):
        raise DeepEvidenceError("risk-of-bias assessment is malformed")
    _text(bias.tool_or_framework, "risk_of_bias.tool_or_framework")
    _text(bias.rationale, "risk_of_bias.rationale")
    if bias.domains != _strings(bias.domains, "risk_of_bias.domains", required=True):
        raise DeepEvidenceError("risk-of-bias domains are not canonical")
    expected = _stable_id(
        "DEEP-EVIDENCE",
        DEEP_EVIDENCE_RECORD_ID_RULE,
        _plain_record(record, "deep_evidence_record_id"),
    )
    if record.deep_evidence_record_id != expected:
        raise DeepEvidenceError("deep evidence record content-derived ID mismatch")


def _record_universes(package: DeepEvidencePackage) -> dict[CorrectionTargetKind, set[str]]:
    return {
        CorrectionTargetKind.SOURCE: {row.source_record_id for row in package.sources},
        CorrectionTargetKind.EVIDENCE_SPAN: {
            row.evidence_span_id for row in package.evidence_spans
        },
        CorrectionTargetKind.IDENTITY: {
            row.identity_record_id for row in package.identity_records
        },
        CorrectionTargetKind.CLAIM: {row.claim_id for row in package.claims},
        CorrectionTargetKind.PATH: {row.path_id for row in package.paths},
    }


def _validate_corrections(package: DeepEvidencePackage) -> dict[CorrectionTargetKind, set[str]]:
    universes = _record_universes(package)
    superseded_or_quarantined: dict[CorrectionTargetKind, set[str]] = defaultdict(set)
    replacement_edges: dict[CorrectionTargetKind, dict[str, str]] = defaultdict(dict)
    seen_targets: set[tuple[CorrectionTargetKind, str]] = set()
    source_ids = universes[CorrectionTargetKind.SOURCE]
    span_ids = universes[CorrectionTargetKind.EVIDENCE_SPAN]
    for correction in package.corrections:
        if not isinstance(correction, RecordCorrection):
            raise DeepEvidenceError("corrections contain an invalid record")
        target_key = (correction.target_kind, correction.target_id)
        if target_key in seen_targets:
            raise DeepEvidenceError("one record has multiple current correction directives")
        seen_targets.add(target_key)
        if correction.target_id not in universes[correction.target_kind]:
            raise DeepEvidenceError("correction target does not resolve")
        if not set(correction.provenance_source_ids).issubset(source_ids):
            raise DeepEvidenceError("correction provenance source does not resolve")
        if not set(correction.provenance_evidence_span_ids).issubset(span_ids):
            raise DeepEvidenceError("correction provenance span does not resolve")
        if correction.action is CorrectionAction.SUPERSEDE:
            replacement = correction.replacement_id
            if replacement is None or replacement == correction.target_id:
                raise DeepEvidenceError("supersession needs a distinct replacement")
            if replacement not in universes[correction.target_kind]:
                raise DeepEvidenceError("supersession replacement does not resolve")
            replacement_edges[correction.target_kind][correction.target_id] = replacement
        elif correction.action is CorrectionAction.QUARANTINE:
            if correction.replacement_id is not None:
                raise DeepEvidenceError("quarantine cannot carry a replacement")
        else:
            raise DeepEvidenceError("correction action is invalid")
        superseded_or_quarantined[correction.target_kind].add(correction.target_id)
        expected = _stable_id(
            "CORRECTION", CORRECTION_ID_RULE, _plain_record(correction, "correction_id")
        )
        if correction.correction_id != expected:
            raise DeepEvidenceError("correction content-derived ID mismatch")
    for kind, edges in replacement_edges.items():
        for start in edges:
            seen: set[str] = set()
            cursor = start
            while cursor in edges:
                if cursor in seen:
                    raise DeepEvidenceError(f"{kind.value} correction chain is cyclic")
                seen.add(cursor)
                cursor = edges[cursor]
    return superseded_or_quarantined


def make_deep_evidence_package(
    screened_candidate: ScreenedCandidateRecord,
    *,
    identity_records: Iterable[AuthoritativeIdentityRecord],
    current_identity_record_id: str | None,
    sources: Iterable[DeepSourceRecord],
    evidence_spans: Iterable[EvidenceSpan],
    evidence_records: Iterable[DeepEvidenceRecord],
    claims: Iterable[DeepClaimRecord],
    paths: Iterable[DeepEvidencePath],
    endpoint_assessments: Iterable[DeepEndpointAssessment],
    corrections: Iterable[RecordCorrection] = (),
) -> DeepEvidencePackage:
    body = {
        "schema_version": SCHEMA_VERSION,
        "model_version": DEEP_EVIDENCE_MODEL_VERSION,
        "screened_candidate": screened_candidate,
        "identity_records": tuple(
            sorted(set(identity_records), key=lambda row: row.identity_record_id)
        ),
        "current_identity_record_id": _optional_text(
            current_identity_record_id, "current_identity_record_id"
        ),
        "sources": tuple(sorted(set(sources), key=lambda row: row.source_record_id)),
        "evidence_spans": tuple(
            sorted(set(evidence_spans), key=lambda row: row.evidence_span_id)
        ),
        "evidence_records": tuple(
            sorted(set(evidence_records), key=lambda row: row.deep_evidence_record_id)
        ),
        "claims": tuple(sorted(set(claims), key=lambda row: row.claim_id)),
        "paths": tuple(sorted(set(paths), key=lambda row: row.path_id)),
        "endpoint_assessments": tuple(
            sorted(set(endpoint_assessments), key=lambda row: row.endpoint_id)
        ),
        "corrections": tuple(sorted(set(corrections), key=lambda row: row.correction_id)),
    }
    return DeepEvidencePackage(
        package_id=_stable_id("DEEP-PACKAGE", PACKAGE_ID_RULE, body), **body
    )


def _unique_index(rows: Iterable[Any], id_field: str, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        row_id = getattr(row, id_field)
        if row_id in result:
            raise DeepEvidenceError(f"duplicate {label} ID {row_id}")
        result[row_id] = row
    return result


def validate_deep_evidence_package(
    package: DeepEvidencePackage,
    *,
    verification_mode: VerificationMode = VerificationMode.STRUCTURAL,
    retained_payloads: Mapping[str, bytes] | None = None,
) -> None:
    if not isinstance(package, DeepEvidencePackage):
        raise DeepEvidenceError("expected DeepEvidencePackage")
    if package.schema_version != SCHEMA_VERSION or package.model_version != DEEP_EVIDENCE_MODEL_VERSION:
        raise DeepEvidenceError("deep evidence package version mismatch")
    candidate = package.screened_candidate
    if not isinstance(candidate, ScreenedCandidateRecord):
        raise DeepEvidenceError("deep package requires a screened candidate")
    sources = _unique_index(package.sources, "source_record_id", "source")
    spans = _unique_index(package.evidence_spans, "evidence_span_id", "evidence span")
    evidence = _unique_index(
        package.evidence_records, "deep_evidence_record_id", "deep evidence"
    )
    claims = _unique_index(package.claims, "claim_id", "claim")
    paths = _unique_index(package.paths, "path_id", "path")
    identities = _unique_index(package.identity_records, "identity_record_id", "identity")
    if (
        package.current_identity_record_id is not None
        and package.current_identity_record_id not in identities
    ):
        raise DeepEvidenceError("current identity record does not resolve")
    for source in package.sources:
        validate_deep_source_record(
            source,
            verification_mode=(
                verification_mode
                if any(span.source_record_id == source.source_record_id for span in package.evidence_spans)
                else VerificationMode.STRUCTURAL
            ),
            retained_payloads=retained_payloads,
        )
    for span in package.evidence_spans:
        source = sources.get(span.source_record_id)
        if source is None:
            raise DeepEvidenceError("evidence span refers to an unknown source")
        validate_evidence_span(
            span,
            source,
            verification_mode=verification_mode,
            retained_payloads=retained_payloads,
        )
    for identity in package.identity_records:
        validate_authoritative_identity(
            identity, candidate, sources=sources, spans=spans
        )
    for claim in package.claims:
        core = make_atomic_claim_core(
            candidate_id=claim.candidate_id,
            proposition=claim.proposition,
            polarity=claim.polarity,
            reporting_status=claim.reporting_status,
            evidence_modality=claim.evidence_modality,
            scope=claim.scope,
            calibration=claim.calibration,
            uncertainty=claim.uncertainty,
        )
        if claim.claim_id != core.claim_id:
            raise DeepEvidenceError("claim content-derived ID mismatch")
        if claim.candidate_id != candidate.screened_candidate_id:
            raise DeepEvidenceError("claim candidate link mismatch")
        if claim.evidence_record_ids != _strings(
            claim.evidence_record_ids, "claim evidence_record_ids", required=True
        ):
            raise DeepEvidenceError("claim evidence record IDs are not canonical")
        if any(record_id not in evidence for record_id in claim.evidence_record_ids):
            raise DeepEvidenceError("claim refers to unknown deep evidence")
        for record_id in claim.evidence_record_ids:
            if evidence[record_id].claim_id != claim.claim_id:
                raise DeepEvidenceError("claim reused evidence bound to another claim")
    claimed_evidence_ids = {
        record_id for claim in package.claims for record_id in claim.evidence_record_ids
    }
    if claimed_evidence_ids != set(evidence):
        raise DeepEvidenceError("every deep evidence record must belong to exactly one atomic claim")
    for record in package.evidence_records:
        claim = claims.get(record.claim_id)
        source = sources.get(record.source_record_id)
        span = spans.get(record.evidence_span_id)
        if claim is None or source is None or span is None:
            raise DeepEvidenceError("deep evidence has broken claim/source/span linkage")
        _validate_deep_evidence_record(record, claim, source, span)
    route_ids = {route.route_id for route in candidate.structured_routes}
    for path in package.paths:
        if path.candidate_id != candidate.screened_candidate_id:
            raise DeepEvidenceError("deep path candidate link mismatch")
        if path.structured_route_id not in route_ids:
            raise DeepEvidenceError("deep path route does not resolve to the screened candidate")
        if path.claim_ids != _strings(path.claim_ids, "path claim_ids", required=True):
            raise DeepEvidenceError("path claim IDs are not canonical")
        if any(claim_id not in claims for claim_id in path.claim_ids):
            raise DeepEvidenceError("deep path claim does not resolve")
        if any(claims[claim_id].scope.endpoint_id != path.endpoint_id for claim_id in path.claim_ids):
            raise DeepEvidenceError("deep path silently crosses endpoint scope")
        expected_evidence = {
            record_id for claim_id in path.claim_ids for record_id in claims[claim_id].evidence_record_ids
        }
        if set(path.evidence_record_ids) != expected_evidence:
            raise DeepEvidenceError("deep path evidence does not exactly cover its claims")
        expected_id = _stable_id("DEEP-PATH", PATH_ID_RULE, _plain_record(path, "path_id"))
        if path.path_id != expected_id:
            raise DeepEvidenceError("deep path content-derived ID mismatch")
    endpoint_ids = set(candidate.endpoint_ids)
    assessment_ids = [row.endpoint_id for row in package.endpoint_assessments]
    if assessment_ids != sorted(endpoint_ids) or set(assessment_ids) != endpoint_ids:
        raise DeepEvidenceError("deep package must explicitly assess every candidate endpoint")
    for assessment in package.endpoint_assessments:
        if not isinstance(assessment.status, EndpointDeepStatus):
            raise DeepEvidenceError("deep endpoint status is invalid")
        _text(assessment.reason, "deep endpoint assessment reason")
        if tuple(sorted(set(assessment.claim_ids))) != assessment.claim_ids:
            raise DeepEvidenceError("deep endpoint claim IDs are not canonical")
        if any(claim_id not in claims for claim_id in assessment.claim_ids):
            raise DeepEvidenceError("deep endpoint assessment claim does not resolve")
        if any(claims[claim_id].scope.endpoint_id != assessment.endpoint_id for claim_id in assessment.claim_ids):
            raise DeepEvidenceError("deep endpoint assessment crosses endpoint scope")
        if assessment.status is EndpointDeepStatus.ASSESSED and not assessment.claim_ids:
            raise DeepEvidenceError("assessed deep endpoint needs at least one atomic claim")
    inactive = _validate_corrections(package)
    active_identity_ids = set(identities) - inactive[CorrectionTargetKind.IDENTITY]
    if package.current_identity_record_id is None:
        if active_identity_ids:
            raise DeepEvidenceError("an active identity exists but is not designated current")
    elif package.current_identity_record_id in inactive[CorrectionTargetKind.IDENTITY]:
        raise DeepEvidenceError("current identity record has been superseded or quarantined")
    elif active_identity_ids != {package.current_identity_record_id}:
        raise DeepEvidenceError("identity history must designate exactly one active current identity")
    active_claim_ids = set(claims) - inactive[CorrectionTargetKind.CLAIM]
    active_path_ids = set(paths) - inactive[CorrectionTargetKind.PATH]
    if package.current_identity_record_id is not None:
        current_identity = identities[package.current_identity_record_id]
        current_identity_provenance = (
            *current_identity.registry_assertions,
            *current_identity.compound_origin_assertions,
            *current_identity.human_use_status_assertions,
            *current_identity.development_status_assertions,
            *current_identity.active_moiety_mappings,
            *current_identity.components,
            *current_identity.relationships,
        )
        if current_identity.formulation is not None:
            current_identity_provenance = (*current_identity_provenance, current_identity.formulation)
        for row in current_identity_provenance:
            if row.source_record_id in inactive[CorrectionTargetKind.SOURCE]:
                raise DeepEvidenceError("current identity depends on a superseded/quarantined source")
            if row.evidence_span_id in inactive[CorrectionTargetKind.EVIDENCE_SPAN]:
                raise DeepEvidenceError("current identity depends on a superseded/quarantined span")
    for path_id in active_path_ids:
        if not set(paths[path_id].claim_ids).issubset(active_claim_ids):
            raise DeepEvidenceError("active path depends on a superseded/quarantined claim")
    for claim_id in active_claim_ids:
        for record_id in claims[claim_id].evidence_record_ids:
            record = evidence[record_id]
            if record.source_record_id in inactive[CorrectionTargetKind.SOURCE]:
                raise DeepEvidenceError("active claim depends on a corrected/quarantined source")
            if record.evidence_span_id in inactive[CorrectionTargetKind.EVIDENCE_SPAN]:
                raise DeepEvidenceError("active claim depends on a corrected/quarantined evidence span")
    expected_package_id = _stable_id(
        "DEEP-PACKAGE", PACKAGE_ID_RULE, _plain_record(package, "package_id")
    )
    if package.package_id != expected_package_id:
        raise DeepEvidenceError("deep evidence package content-derived ID mismatch")


def promote_deep_candidate(
    package: DeepEvidencePackage,
    *,
    retained_payloads: Mapping[str, bytes],
) -> DeepCandidateRecord:
    """Validate original content and exact identity before deep promotion."""

    validate_deep_evidence_package(
        package,
        verification_mode=VerificationMode.ORIGINAL_CONTENT_REQUIRED,
        retained_payloads=retained_payloads,
    )
    identities = {row.identity_record_id: row for row in package.identity_records}
    if package.current_identity_record_id is None:
        raise DeepEvidenceError("deep candidate has no active authoritative identity")
    identity = identities[package.current_identity_record_id]
    if (
        identity.resolution_status is not IdentityResolutionStatus.RESOLVED
        or not identity.deep_identity_eligible
        or identity.normalized_intervention_id is None
    ):
        raise DeepEvidenceError(
            "deep candidate requires exact, authoritative, decision-relevant identity"
        )
    if not identity.compound_origin_assertions:
        raise DeepEvidenceError("deep identity requires explicit compound-origin provenance")
    if not identity.human_use_status_assertions:
        raise DeepEvidenceError("deep identity requires explicit human-use-status provenance")
    if not identity.development_status_assertions:
        raise DeepEvidenceError("deep identity requires approval/development-status provenance")
    if identity.entity_kind in {
        ChemicalEntityKind.SALT,
        ChemicalEntityKind.SOLVATE,
        ChemicalEntityKind.PRODRUG,
        ChemicalEntityKind.ACTIVE_METABOLITE,
        ChemicalEntityKind.FORMULATION,
    } and not identity.active_moiety_mappings:
        raise DeepEvidenceError("this exact form requires explicit active-moiety mapping provenance")
    active_claim_ids = {row.claim_id for row in package.claims}
    active_path_ids = {row.path_id for row in package.paths}
    for correction in package.corrections:
        if correction.target_kind is CorrectionTargetKind.CLAIM:
            active_claim_ids.discard(correction.target_id)
        if correction.target_kind is CorrectionTargetKind.PATH:
            active_path_ids.discard(correction.target_id)
    if not active_claim_ids or not active_path_ids:
        raise DeepEvidenceError("deep candidate requires active grounded claims and paths")
    if any(
        assessment.status is EndpointDeepStatus.NOT_ASSESSED
        for assessment in package.endpoint_assessments
    ):
        raise DeepEvidenceError("deep promotion cannot omit an endpoint without an upstream selection policy")
    return DeepCandidateRecord(
        candidate_id=package.screened_candidate.screened_candidate_id,
        deep_evidence_package_id=package.package_id,
        identity_record_id=identity.identity_record_id,
        normalized_intervention_id=identity.normalized_intervention_id,
        endpoint_ids=tuple(sorted(package.screened_candidate.endpoint_ids)),
        claim_ids=tuple(sorted(active_claim_ids)),
        path_ids=tuple(sorted(active_path_ids)),
    )


__all__ = [
    "ActiveMoietyMapping",
    "AtomicClaimCore",
    "AuthoritativeIdentityRecord",
    "CanonicalStructure",
    "ChemicalEntityKind",
    "ClaimCalibration",
    "ClaimPolarity",
    "ClaimReportingStatus",
    "ClaimScope",
    "Comparator",
    "CompositionComponent",
    "CompositionStatus",
    "CompoundOrigin",
    "CompoundOriginAssertion",
    "ContentVerificationMethod",
    "CorrectionAction",
    "CorrectionTargetKind",
    "DeepCandidateRecord",
    "DeepClaimRecord",
    "DeepEndpointAssessment",
    "DeepEvidenceError",
    "DeepEvidencePackage",
    "DeepEvidencePath",
    "DeepEvidenceRecord",
    "DeepSourceRecord",
    "DevelopmentStatusAssertion",
    "EffectMagnitude",
    "EndpointDeepStatus",
    "EvidenceSpan",
    "EvidenceSupportKind",
    "ExperimentalModelKind",
    "FormulationDescriptor",
    "HumanUseStatus",
    "HumanUseStatusAssertion",
    "IdentityRelationship",
    "IdentityRelationshipType",
    "IdentityResolutionStatus",
    "ObservedEffectDirection",
    "PopulationOrExperimentalModel",
    "RecordCorrection",
    "RegistryIdentityAssertion",
    "ReportedQuantity",
    "ReportedText",
    "ReportedValueStatus",
    "RetrievalMethod",
    "RiskOfBiasAssessment",
    "RiskOfBiasLevel",
    "SourceContentScope",
    "StatisticalUncertainty",
    "StereochemistryStatus",
    "StructuredEvidencePointer",
    "StudyDesign",
    "VerificationMode",
    "bind_atomic_claim",
    "make_atomic_claim_core",
    "make_deep_evidence_package",
    "make_deep_evidence_path",
    "make_deep_evidence_record",
    "make_deep_source_record",
    "make_evidence_span",
    "make_record_correction",
    "make_registry_identity_assertion",
    "missing_quantity",
    "missing_text",
    "normalize_authoritative_identity",
    "promote_deep_candidate",
    "reported_quantity",
    "reported_text",
    "validate_authoritative_identity",
    "validate_deep_evidence_package",
    "validate_deep_source_record",
    "validate_evidence_span",
]
