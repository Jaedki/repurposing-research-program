#!/usr/bin/env python3
"""Persisted schema-v7 all-seed identity and disposition aggregation.

This production module starts from immutable discovery seeds and frozen resolver
assertions.  It does not retrieve chemistry, infer identity from names, import
benchmark fixtures/oracles, screen candidates, or perform deep evidence work.
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

from v7_case_model import CaseRevision, build_case_bundle, validate_case_revision


SCHEMA_VERSION = 7
MODEL_VERSION = "schema-v7-production-disposition-v1"
NORMALIZATION_POLICY_VERSION = "schema-v7-production-identity-policy-v1"
DISPOSITION_RULE_VERSION = "schema-v7-production-seed-disposition-v1"
PLAN_ID_RULE = "schema-v7-production-disposition-plan-id-v1"
AGGREGATE_ID_RULE = "schema-v7-production-disposition-aggregate-id-v1"

CANONICAL_DISPOSITIONS = {
    "admit",
    "merge",
    "baseline",
    "reject",
    "quarantine",
    "failed",
}
ENTITY_KINDS = {
    "single_compound",
    "salt",
    "hydrate",
    "solvate",
    "stereoisomer",
    "prodrug",
    "active_metabolite",
    "isotope",
    "conjugate",
    "fixed_combination",
    "standardized_preparation",
    "formulation",
    "mixture",
}
COMPOSITION_STATUSES = {"not_applicable", "exact", "partial", "undefined"}
STEREOCHEMISTRY_STATUSES = {
    "fully_specified",
    "partially_specified",
    "unspecified",
    "racemate",
    "not_applicable",
    "unresolved",
}
RELATIONSHIP_TYPES = {
    "self",
    "salt_of",
    "hydrate_of",
    "solvate_of",
    "stereoisomer_of",
    "prodrug_of",
    "active_metabolite_of",
    "isotope_of",
    "conjugate_of",
    "formulation_of",
    "delivers_active_moiety",
    "component_of",
}
RESULT_STATUSES = {"resolved", "unresolved", "technical_failure"}
CASE_ROLES = {"repurposing", "baseline", "ineligible", "unknown"}
ASSERTION_STATUSES = {"resolved", "unresolved"}

_ROLLUP_ENTITY_KINDS = {
    "single_compound",
    "salt",
    "hydrate",
    "solvate",
    "formulation",
}
_SINGLE_ACTIVE_MOIETY_KINDS = {
    "single_compound",
    "salt",
    "hydrate",
    "solvate",
    "stereoisomer",
    "prodrug",
    "active_metabolite",
    "isotope",
    "conjugate",
    "formulation",
}


class DispositionAggregateError(ValueError):
    """Raised when disposition inputs or aggregate content violate schema v7."""


class DispositionAggregateConflictError(DispositionAggregateError):
    """Raised when immutable logical content is replayed with different bytes."""


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
    raise DispositionAggregateError(
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


def _stable_id(prefix: str, rule: str, value: Any) -> str:
    return f"{prefix}-{_sha256({'rule': rule, 'value': value})[:24]}"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispositionAggregateError(f"{label} must be a nonempty string")
    return value.strip()


def _raw_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispositionAggregateError(f"{label} must be nonblank text")
    return value


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _controlled(value: Any, allowed: set[str], label: str) -> str:
    token = _required_text(value, label)
    if token not in allowed:
        raise DispositionAggregateError(f"{label} has an invalid controlled value")
    return token


def _text_list(
    value: Any,
    label: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise DispositionAggregateError(f"{label} must be a list")
    rows = sorted({_required_text(item, label) for item in value})
    if not allow_empty and not rows:
        raise DispositionAggregateError(f"{label} cannot be empty")
    return rows


def _safe_component(value: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in "_.-" else "_"
        for character in value
    ).strip("._")
    if not safe:
        raise DispositionAggregateError(
            "Persistent identity cannot be converted to a safe path"
        )
    return safe


def _coerce_case(value: CaseRevision | Mapping[str, Any]) -> CaseRevision:
    if isinstance(value, CaseRevision):
        validate_case_revision(value)
        return value
    if not isinstance(value, Mapping):
        raise DispositionAggregateError(
            "case_revision must be a CaseRevision or mapping"
        )
    raw_input = value.get("original_input", value)
    if not isinstance(raw_input, Mapping):
        raise DispositionAggregateError(
            "case_revision.original_input must be a mapping"
        )
    case = build_case_bundle(raw_input).case_revision
    for field_name in ("case_id", "case_revision_id", "source_input_sha256"):
        supplied = value.get(field_name)
        if supplied is not None and supplied != getattr(case, field_name):
            raise DispositionAggregateConflictError(
                f"case_revision.{field_name} conflicts with the rebuilt canonical case"
            )
    validate_case_revision(case)
    return case


def _normalize_seeds(
    case: CaseRevision, seeds: Iterable[Mapping[str, Any] | Any]
) -> list[dict[str, Any]]:
    if isinstance(seeds, (str, bytes, Mapping)):
        raise DispositionAggregateError("seeds must be an iterable of seed records")
    reduced: dict[str, dict[str, Any]] = {}
    for supplied in seeds:
        row = _plain(supplied)
        if not isinstance(row, dict):
            raise DispositionAggregateError("every seed must be a mapping or dataclass")
        required = {
            "seed_id",
            "case_id",
            "case_revision_id",
            "endpoint_ids",
            "compound_hint",
            "source_mapping_id",
            "discovery_route_ids",
        }
        missing = required - set(row)
        if missing:
            raise DispositionAggregateError(
                f"seed lacks required fields: {sorted(missing)}"
            )
        seed_id = _required_text(row["seed_id"], "seed.seed_id")
        if seed_id in reduced:
            raise DispositionAggregateConflictError(
                f"seeds repeat canonical seed identity {seed_id}"
            )
        if row["case_id"] != case.case_id or row["case_revision_id"] != case.case_revision_id:
            raise DispositionAggregateConflictError(
                f"seed {seed_id} does not belong to the supplied case revision"
            )
        endpoint_ids = _text_list(
            row["endpoint_ids"], f"seed {seed_id}.endpoint_ids", allow_empty=False
        )
        case_endpoint_ids = {endpoint.endpoint_id for endpoint in case.endpoints}
        if not set(endpoint_ids).issubset(case_endpoint_ids):
            raise DispositionAggregateError(
                f"seed {seed_id} references an endpoint outside the case portfolio"
            )
        route_ids = _text_list(
            row["discovery_route_ids"],
            f"seed {seed_id}.discovery_route_ids",
            allow_empty=False,
        )
        hint = row["compound_hint"]
        if not isinstance(hint, dict):
            raise DispositionAggregateError(f"seed {seed_id}.compound_hint must be an object")
        for name in ("kind", "value", "namespace"):
            if name not in hint or not isinstance(hint[name], str):
                raise DispositionAggregateError(
                    f"seed {seed_id}.compound_hint.{name} is missing"
                )
        _required_text(row["source_mapping_id"], f"seed {seed_id}.source_mapping_id")
        row["endpoint_ids"] = endpoint_ids
        row["discovery_route_ids"] = route_ids
        reduced[seed_id] = row
    return [reduced[key] for key in sorted(reduced)]


def _normalize_registry_identifiers(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise DispositionAggregateError(f"{label} must be a list")
    reduced: dict[tuple[str, str], dict[str, str]] = {}
    for supplied in value:
        if not isinstance(supplied, Mapping) or set(supplied) != {
            "namespace",
            "identifier",
        }:
            raise DispositionAggregateError(
                f"{label} entries require namespace and identifier only"
            )
        row = {
            "namespace": _required_text(
                supplied["namespace"], f"{label}.namespace"
            ).upper(),
            "identifier": _required_text(
                supplied["identifier"], f"{label}.identifier"
            ),
        }
        reduced[(row["namespace"], row["identifier"])] = row
    return [reduced[key] for key in sorted(reduced)]


def _normalize_structure(value: Any, label: str) -> dict[str, str] | None:
    if value is None:
        return None
    required = {
        "canonical_smiles",
        "standard_inchi",
        "full_inchikey",
        "stereochemistry_status",
        "stereochemistry_descriptor",
        "canonicalization_method",
        "canonicalization_version",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise DispositionAggregateError(
            f"{label} requires exactly {sorted(required)}"
        )
    return {
        "canonical_smiles": _required_text(
            value["canonical_smiles"], f"{label}.canonical_smiles"
        ),
        "standard_inchi": _required_text(
            value["standard_inchi"], f"{label}.standard_inchi"
        ),
        "full_inchikey": _required_text(
            value["full_inchikey"], f"{label}.full_inchikey"
        ),
        "stereochemistry_status": _controlled(
            value["stereochemistry_status"],
            STEREOCHEMISTRY_STATUSES,
            f"{label}.stereochemistry_status",
        ),
        "stereochemistry_descriptor": _required_text(
            value["stereochemistry_descriptor"],
            f"{label}.stereochemistry_descriptor",
        ),
        "canonicalization_method": _required_text(
            value["canonicalization_method"],
            f"{label}.canonicalization_method",
        ),
        "canonicalization_version": _required_text(
            value["canonicalization_version"],
            f"{label}.canonicalization_version",
        ),
    }


def _normalize_components(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise DispositionAggregateError(f"{label} must be a list")
    required = {
        "component_namespace",
        "component_identifier",
        "component_entity_kind",
        "role",
        "amount_or_fraction",
    }
    rows: dict[tuple[str, ...], dict[str, str]] = {}
    for supplied in value:
        if not isinstance(supplied, Mapping) or set(supplied) != required:
            raise DispositionAggregateError(
                f"{label} entries require exactly {sorted(required)}"
            )
        row = {
            "component_namespace": _required_text(
                supplied["component_namespace"],
                f"{label}.component_namespace",
            ).upper(),
            "component_identifier": _required_text(
                supplied["component_identifier"],
                f"{label}.component_identifier",
            ),
            "component_entity_kind": _controlled(
                supplied["component_entity_kind"],
                ENTITY_KINDS,
                f"{label}.component_entity_kind",
            ),
            "role": _required_text(supplied["role"], f"{label}.role"),
            "amount_or_fraction": _required_text(
                supplied["amount_or_fraction"], f"{label}.amount_or_fraction"
            ),
        }
        key = tuple(row[name] for name in sorted(row))
        rows[key] = row
    return [rows[key] for key in sorted(rows)]


def _normalize_product(value: Any, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    required = {
        "product_namespace",
        "product_identifier",
        "dosage_form",
        "release_characteristic",
        "administration_routes",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise DispositionAggregateError(
            f"{label} requires exactly {sorted(required)}"
        )
    return {
        "product_namespace": _required_text(
            value["product_namespace"], f"{label}.product_namespace"
        ).upper(),
        "product_identifier": _required_text(
            value["product_identifier"], f"{label}.product_identifier"
        ),
        "dosage_form": _required_text(
            value["dosage_form"], f"{label}.dosage_form"
        ),
        "release_characteristic": _required_text(
            value["release_characteristic"],
            f"{label}.release_characteristic",
        ),
        "administration_routes": _text_list(
            value["administration_routes"],
            f"{label}.administration_routes",
            allow_empty=False,
        ),
    }


def _normalize_active_moieties(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise DispositionAggregateError(f"{label} must be a list")
    required = {
        "relationship_type",
        "moiety_namespace",
        "moiety_identifier",
        "moiety_entity_kind",
        "exact_form_scope",
    }
    rows: dict[tuple[str, ...], dict[str, str]] = {}
    for supplied in value:
        if not isinstance(supplied, Mapping) or set(supplied) != required:
            raise DispositionAggregateError(
                f"{label} entries require exactly {sorted(required)}"
            )
        row = {
            "relationship_type": _controlled(
                supplied["relationship_type"],
                RELATIONSHIP_TYPES,
                f"{label}.relationship_type",
            ),
            "moiety_namespace": _required_text(
                supplied["moiety_namespace"], f"{label}.moiety_namespace"
            ).upper(),
            "moiety_identifier": _required_text(
                supplied["moiety_identifier"], f"{label}.moiety_identifier"
            ),
            "moiety_entity_kind": _controlled(
                supplied["moiety_entity_kind"],
                ENTITY_KINDS,
                f"{label}.moiety_entity_kind",
            ),
            "exact_form_scope": _required_text(
                supplied["exact_form_scope"], f"{label}.exact_form_scope"
            ),
        }
        key = tuple(row[name] for name in sorted(row))
        rows[key] = row
    return [rows[key] for key in sorted(rows)]


def _normalize_identity(value: Any, label: str) -> dict[str, Any]:
    required = {
        "entity_kind",
        "registry_identifiers",
        "canonical_structure",
        "composition_status",
        "components",
        "product",
        "active_moieties",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise DispositionAggregateError(
            f"{label} requires exactly {sorted(required)}"
        )
    entity_kind = _controlled(value["entity_kind"], ENTITY_KINDS, f"{label}.entity_kind")
    registry = _normalize_registry_identifiers(
        value["registry_identifiers"], f"{label}.registry_identifiers"
    )
    structure = _normalize_structure(
        value["canonical_structure"], f"{label}.canonical_structure"
    )
    composition_status = _controlled(
        value["composition_status"],
        COMPOSITION_STATUSES,
        f"{label}.composition_status",
    )
    components = _normalize_components(value["components"], f"{label}.components")
    product = _normalize_product(value["product"], f"{label}.product")
    active_moieties = _normalize_active_moieties(
        value["active_moieties"], f"{label}.active_moieties"
    )
    if not registry and structure is None and not components and product is None:
        raise DispositionAggregateError(
            f"{label} lacks an authoritative exact identifier, structure, composition, or product"
        )
    composition_kinds = {"fixed_combination", "standardized_preparation", "mixture"}
    if entity_kind in composition_kinds and (
        composition_status != "exact" or not components
    ):
        raise DispositionAggregateError(
            f"{label} cannot resolve an undefined or non-exact composition"
        )
    if entity_kind not in composition_kinds | {"formulation"} and (
        composition_status != "not_applicable" or components
    ):
        raise DispositionAggregateError(
            f"{label} gives a single exact entity an incompatible mixture composition"
        )
    if entity_kind == "formulation" and (
        product is None or composition_status != "exact" or not components
    ):
        raise DispositionAggregateError(
            f"{label} formulation lacks an exact product and component composition"
        )
    if entity_kind != "formulation" and product is not None:
        raise DispositionAggregateError(
            f"{label} product descriptor is permitted only for an exact formulation"
        )
    if entity_kind == "stereoisomer" and (
        structure is None
        or structure["stereochemistry_status"]
        not in {"fully_specified", "racemate"}
    ):
        raise DispositionAggregateError(
            f"{label} stereoisomer lacks decision-relevant exact stereochemistry"
        )
    if not active_moieties:
        raise DispositionAggregateError(
            f"{label} lacks an explicit active-moiety relationship"
        )
    if entity_kind in _SINGLE_ACTIVE_MOIETY_KINDS and len(active_moieties) != 1:
        raise DispositionAggregateError(
            f"{label} exact form must name exactly one active-moiety relationship"
        )
    relationship_policy = {
        "single_compound": {"self"},
        "salt": {"salt_of", "delivers_active_moiety"},
        "hydrate": {"hydrate_of", "delivers_active_moiety"},
        "solvate": {"solvate_of", "delivers_active_moiety"},
        "stereoisomer": {"self", "stereoisomer_of"},
        "prodrug": {"prodrug_of", "delivers_active_moiety"},
        "active_metabolite": {"active_metabolite_of"},
        "isotope": {"isotope_of"},
        "conjugate": {"conjugate_of", "delivers_active_moiety"},
        "fixed_combination": {"delivers_active_moiety"},
        "standardized_preparation": {"delivers_active_moiety"},
        "formulation": {"formulation_of", "delivers_active_moiety"},
        "mixture": {"delivers_active_moiety"},
    }
    invalid_relationships = {
        row["relationship_type"]
        for row in active_moieties
        if row["relationship_type"] not in relationship_policy[entity_kind]
    }
    if invalid_relationships:
        raise DispositionAggregateError(
            f"{label} has active-moiety relationships incompatible with {entity_kind}: "
            f"{sorted(invalid_relationships)}"
        )
    return {
        "entity_kind": entity_kind,
        "registry_identifiers": registry,
        "canonical_structure": structure,
        "composition_status": composition_status,
        "components": components,
        "product": product,
        "active_moieties": active_moieties,
    }


def _identity_projection(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact identity fingerprint; raw names and active links are excluded."""

    if identity["canonical_structure"] is not None:
        basis = {
            "basis": "canonical_structure",
            "canonical_structure": identity["canonical_structure"],
        }
    elif identity["product"] is not None:
        basis = {
            "basis": "exact_product",
            "product": identity["product"],
            "components": identity["components"],
        }
    elif identity["composition_status"] == "exact" and identity["components"]:
        basis = {
            "basis": "exact_composition",
            "components": identity["components"],
        }
    else:
        basis = {
            "basis": "stable_registry_identifiers",
            "registry_identifiers": identity["registry_identifiers"],
        }
    return {"entity_kind": identity["entity_kind"], **basis}


def _normalize_resolver_bundle(
    seed_ids: set[str], supplied: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(supplied, Mapping):
        raise DispositionAggregateError("frozen_resolver_assertions must be a mapping")
    required = {
        "resolver_revision",
        "normalization_policy_version",
        "resolver_sources",
        "seed_results",
        "identity_assertions",
    }
    if set(supplied) != required:
        raise DispositionAggregateError(
            "frozen resolver fields differ from the production contract; "
            f"missing={sorted(required - set(supplied))}, "
            f"unknown={sorted(set(supplied) - required)}"
        )
    policy_version = _required_text(
        supplied["normalization_policy_version"], "normalization_policy_version"
    )
    if policy_version != NORMALIZATION_POLICY_VERSION:
        raise DispositionAggregateError(
            "normalization policy version does not match the production master policy"
        )
    if not isinstance(supplied["resolver_sources"], (list, tuple)):
        raise DispositionAggregateError("resolver_sources must be a list")
    source_required = {
        "resolver_source_id",
        "authority",
        "authority_release",
        "snapshot_id",
        "snapshot_sha256",
        "method",
        "locator",
    }
    sources: dict[str, dict[str, str]] = {}
    for value in supplied["resolver_sources"]:
        if not isinstance(value, Mapping) or set(value) != source_required:
            raise DispositionAggregateError(
                f"resolver source fields must be exactly {sorted(source_required)}"
            )
        row = {
            name: _required_text(value[name], f"resolver_source.{name}")
            for name in sorted(source_required)
        }
        source_id = row["resolver_source_id"]
        prior = sources.get(source_id)
        if prior is not None and prior != row:
            raise DispositionAggregateConflictError(
                f"resolver source identity conflict for {source_id}"
            )
        sources[source_id] = row
    if not sources:
        raise DispositionAggregateError("at least one frozen resolver source is required")

    if not isinstance(supplied["identity_assertions"], (list, tuple)):
        raise DispositionAggregateError("identity_assertions must be a list")
    assertion_required = {
        "seed_id",
        "resolver_source_id",
        "authority_record_id",
        "authority_locator",
        "assertion_status",
        "reported_identity",
        "identity",
        "unresolved_reason",
        "candidate_identities",
    }
    assertions: dict[str, dict[str, Any]] = {}
    logical_assertions: dict[tuple[str, str, str], dict[str, Any]] = {}
    for value in supplied["identity_assertions"]:
        if not isinstance(value, Mapping):
            raise DispositionAggregateError("identity assertion must be an object")
        unknown = set(value) - assertion_required - {"assertion_id"}
        missing = assertion_required - set(value)
        if unknown or missing:
            raise DispositionAggregateError(
                "identity assertion fields differ from contract; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        seed_id = _required_text(value["seed_id"], "identity_assertion.seed_id")
        if seed_id not in seed_ids:
            raise DispositionAggregateError(
                f"identity assertion references unknown seed {seed_id}"
            )
        source_id = _required_text(
            value["resolver_source_id"], "identity_assertion.resolver_source_id"
        )
        if source_id not in sources:
            raise DispositionAggregateError(
                f"identity assertion references unknown resolver source {source_id}"
            )
        status = _controlled(
            value["assertion_status"],
            ASSERTION_STATUSES,
            "identity_assertion.assertion_status",
        )
        identity = None
        if status == "resolved":
            if value["identity"] is None or value["unresolved_reason"] is not None:
                raise DispositionAggregateError(
                    "resolved identity assertion requires identity and no unresolved reason"
                )
            identity = _normalize_identity(value["identity"], "identity_assertion.identity")
        else:
            if value["identity"] is not None:
                raise DispositionAggregateError(
                    "unresolved identity assertion cannot claim a resolved identity"
                )
            _required_text(
                value["unresolved_reason"],
                "identity_assertion.unresolved_reason",
            )
        if not isinstance(value["candidate_identities"], (list, tuple)):
            raise DispositionAggregateError(
                "identity_assertion.candidate_identities must be a list"
            )
        candidates = [
            _normalize_identity(candidate, "identity_assertion.candidate_identity")
            for candidate in value["candidate_identities"]
        ]
        candidates.sort(key=_canonical_bytes)
        body: dict[str, Any] = {
            "seed_id": seed_id,
            "resolver_source_id": source_id,
            "authority_record_id": _required_text(
                value["authority_record_id"],
                "identity_assertion.authority_record_id",
            ),
            "authority_locator": _required_text(
                value["authority_locator"],
                "identity_assertion.authority_locator",
            ),
            "assertion_status": status,
            "reported_identity": _raw_text(
                value["reported_identity"],
                "identity_assertion.reported_identity",
            ),
            "identity": identity,
            "unresolved_reason": _optional_text(
                value["unresolved_reason"],
                "identity_assertion.unresolved_reason",
            ),
            "candidate_identities": candidates,
        }
        assertion_id = _stable_id(
            "RESOLVER-ASSERTION",
            "schema-v7-production-resolver-assertion-v1",
            body,
        )
        if value.get("assertion_id") not in {None, assertion_id}:
            raise DispositionAggregateConflictError(
                f"identity assertion content conflicts with supplied ID {value.get('assertion_id')}"
            )
        row = {"assertion_id": assertion_id, **body}
        logical_key = (source_id, body["authority_record_id"], seed_id)
        logical_prior = logical_assertions.get(logical_key)
        if logical_prior is not None and logical_prior != row:
            raise DispositionAggregateConflictError(
                "one frozen authority-record identity has conflicting assertion content"
            )
        logical_assertions[logical_key] = row
        prior = assertions.get(assertion_id)
        if prior is not None and prior != row:
            raise DispositionAggregateConflictError(
                f"identity assertion conflict for {assertion_id}"
            )
        assertions[assertion_id] = row

    if not isinstance(supplied["seed_results"], (list, tuple)):
        raise DispositionAggregateError("seed_results must be a list")
    result_required = {
        "seed_id",
        "result_status",
        "case_role",
        "reason_code",
        "reason",
        "resolver_source_ids",
        "synthetic",
    }
    results: dict[str, dict[str, Any]] = {}
    for value in supplied["seed_results"]:
        if not isinstance(value, Mapping):
            raise DispositionAggregateError("seed result must be an object")
        unknown = set(value) - result_required - {"result_id"}
        missing = (result_required - {"synthetic"}) - set(value)
        if unknown or missing:
            raise DispositionAggregateError(
                "seed result fields differ from contract; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        seed_id = _required_text(value["seed_id"], "seed_result.seed_id")
        if seed_id not in seed_ids:
            raise DispositionAggregateError(
                f"seed result references unknown seed {seed_id}"
            )
        if seed_id in results:
            raise DispositionAggregateConflictError(
                f"more than one seed result exists for {seed_id}"
            )
        status = _controlled(
            value["result_status"], RESULT_STATUSES, "seed_result.result_status"
        )
        role = _controlled(value["case_role"], CASE_ROLES, "seed_result.case_role")
        source_ids = _text_list(
            value["resolver_source_ids"], "seed_result.resolver_source_ids"
        )
        if not set(source_ids).issubset(sources):
            raise DispositionAggregateError(
                f"seed result {seed_id} references an unknown resolver source"
            )
        synthetic = bool(value.get("synthetic", False))
        if synthetic and (
            status != "technical_failure"
            or role != "unknown"
            or value["reason_code"] != "resolver_result_missing"
            or source_ids
        ):
            raise DispositionAggregateError(
                "synthetic seed results are reserved for a missing resolver result"
            )
        if not synthetic and not source_ids:
            raise DispositionAggregateError(
                f"seed result {seed_id} lacks resolver provenance"
            )
        body = {
            "seed_id": seed_id,
            "result_status": status,
            "case_role": role,
            "reason_code": _required_text(
                value["reason_code"], "seed_result.reason_code"
            ),
            "reason": _raw_text(value["reason"], "seed_result.reason"),
            "resolver_source_ids": source_ids,
            "synthetic": synthetic,
        }
        result_id = _stable_id(
            "RESOLVER-RESULT", "schema-v7-production-resolver-result-v1", body
        )
        if value.get("result_id") not in {None, result_id}:
            raise DispositionAggregateConflictError(
                f"seed result content conflicts with supplied ID {value.get('result_id')}"
            )
        results[seed_id] = {"result_id": result_id, **body}

    for seed_id in sorted(seed_ids - set(results)):
        body = {
            "seed_id": seed_id,
            "result_status": "technical_failure",
            "case_role": "unknown",
            "reason_code": "resolver_result_missing",
            "reason": "No frozen resolver result was supplied for this canonical seed.",
            "resolver_source_ids": [],
            "synthetic": True,
        }
        results[seed_id] = {
            "result_id": _stable_id(
                "RESOLVER-RESULT", "schema-v7-production-resolver-result-v1", body
            ),
            **body,
        }

    return {
        "resolver_revision": _required_text(
            supplied["resolver_revision"], "resolver_revision"
        ),
        "normalization_policy_version": policy_version,
        "resolver_sources": [sources[key] for key in sorted(sources)],
        "seed_results": [results[key] for key in sorted(results)],
        "identity_assertions": [assertions[key] for key in sorted(assertions)],
    }


def _active_moiety_id(row: Mapping[str, str]) -> str:
    return _stable_id(
        "ACTIVE-MOIETY",
        "schema-v7-production-active-moiety-id-v1",
        {
            "namespace": row["moiety_namespace"],
            "identifier": row["moiety_identifier"],
            "entity_kind": row["moiety_entity_kind"],
        },
    )


def _normalized_intervention_id(identity: Mapping[str, Any]) -> str:
    return _stable_id(
        "NORMALIZED-INTERVENTION",
        "schema-v7-production-normalized-intervention-id-v1",
        _identity_projection(identity),
    )


def _breadth_group_id(
    identity: Mapping[str, Any],
    normalized_intervention_id: str,
    active_rows: list[Mapping[str, str]],
) -> str:
    active_ids = sorted({_active_moiety_id(row) for row in active_rows})
    if identity["entity_kind"] in _ROLLUP_ENTITY_KINDS and len(active_ids) == 1:
        projection: Mapping[str, Any] = {
            "rollup_kind": "confirmed_active_moiety",
            "active_moiety_id": active_ids[0],
        }
    else:
        projection = {
            "rollup_kind": "exact_intervention",
            "normalized_intervention_id": normalized_intervention_id,
        }
    return _stable_id(
        "BREADTH-GROUP", "schema-v7-production-breadth-group-id-v1", projection
    )


def _seed_lineage(seed: Mapping[str, Any]) -> dict[str, Any]:
    structured_route_ids = sorted(
        {
            str(route.get("route_id"))
            for route in seed.get("structured_routes", [])
            if isinstance(route, Mapping) and route.get("route_id")
        }
    )
    body = {
        "seed_id": seed["seed_id"],
        "source_mapping_id": seed["source_mapping_id"],
        "discovery_route_ids": list(seed["discovery_route_ids"]),
        "structured_route_ids": structured_route_ids,
        "endpoint_ids": list(seed["endpoint_ids"]),
        "compound_hint": _plain(seed["compound_hint"]),
        "seed_payload_sha256": _sha256(seed),
    }
    return {
        "lineage_id": _stable_id(
            "SEED-LINEAGE", "schema-v7-production-seed-lineage-v1", body
        ),
        **body,
    }


def _construct_aggregate(
    case: CaseRevision,
    seeds: Iterable[Mapping[str, Any] | Any],
    frozen_resolver_assertions: Mapping[str, Any],
) -> dict[str, Any]:
    seed_rows = _normalize_seeds(case, seeds)
    seed_by_id = {row["seed_id"]: row for row in seed_rows}
    resolver = _normalize_resolver_bundle(set(seed_by_id), frozen_resolver_assertions)
    source_by_id = {
        row["resolver_source_id"]: row for row in resolver["resolver_sources"]
    }
    assertions_by_seed: dict[str, list[dict[str, Any]]] = {
        seed_id: [] for seed_id in seed_by_id
    }
    for assertion in resolver["identity_assertions"]:
        assertions_by_seed[assertion["seed_id"]].append(assertion)
    result_by_seed = {row["seed_id"]: row for row in resolver["seed_results"]}
    lineage_rows = [_seed_lineage(row) for row in seed_rows]
    lineage_by_seed = {row["seed_id"]: row for row in lineage_rows}

    seed_states: dict[str, dict[str, Any]] = {}
    unresolved_records: list[dict[str, Any]] = []
    conflict_records: list[dict[str, Any]] = []
    exact_groups: dict[str, dict[str, Any]] = {}

    for seed_id in sorted(seed_by_id):
        result = result_by_seed[seed_id]
        assertions = assertions_by_seed[seed_id]
        resolved_assertions = [
            row for row in assertions if row["assertion_status"] == "resolved"
        ]
        unresolved_assertions = [
            row for row in assertions if row["assertion_status"] == "unresolved"
        ]
        state: dict[str, Any] = {
            "seed_id": seed_id,
            "result": result,
            "assertions": assertions,
            "identity_status": "failed",
            "relationship_status": "not_applicable",
            "normalized_intervention_id": None,
            "breadth_group_id": None,
            "active_moiety_ids": [],
            "conflict_record_ids": [],
            "unresolved_record_ids": [],
        }
        if result["result_status"] == "technical_failure":
            seed_states[seed_id] = state
            continue

        unresolved_reasons: list[str] = []
        candidate_identities: list[dict[str, Any]] = []
        if result["result_status"] == "unresolved":
            unresolved_reasons.append(result["reason"])
        for assertion in unresolved_assertions:
            unresolved_reasons.append(assertion["unresolved_reason"])
            candidate_identities.extend(assertion["candidate_identities"])
        if not resolved_assertions:
            unresolved_reasons.append(
                "No frozen authoritative exact-identity assertion resolved this seed."
            )
        if unresolved_reasons:
            body = {
                "seed_id": seed_id,
                "raw_source_identity": _plain(seed_by_id[seed_id]["compound_hint"]),
                "reason_codes": sorted(set(unresolved_reasons)),
                "candidate_identity_fingerprints": sorted(
                    {_sha256(_identity_projection(row)) for row in candidate_identities}
                ),
                "resolver_assertion_ids": sorted(
                    row["assertion_id"] for row in assertions
                ),
                "resolver_source_ids": sorted(
                    {row["resolver_source_id"] for row in assertions}
                ),
                "can_advance": False,
            }
            record = {
                "unresolved_identity_id": _stable_id(
                    "UNRESOLVED-IDENTITY",
                    "schema-v7-production-unresolved-identity-v1",
                    body,
                ),
                **body,
            }
            unresolved_records.append(record)
            state["identity_status"] = "unresolved"
            state["relationship_status"] = "unresolved"
            state["unresolved_record_ids"] = [record["unresolved_identity_id"]]
            seed_states[seed_id] = state
            continue

        fingerprints: dict[str, dict[str, Any]] = {}
        for assertion in resolved_assertions:
            identity = assertion["identity"]
            assert identity is not None
            fingerprint = _sha256(_identity_projection(identity))
            fingerprints[fingerprint] = identity
        if len(fingerprints) != 1:
            body = {
                "seed_ids": [seed_id],
                "conflict_kind": "exact_identity",
                "identity_fingerprints": sorted(fingerprints),
                "resolver_assertion_ids": sorted(
                    row["assertion_id"] for row in resolved_assertions
                ),
                "resolver_source_ids": sorted(
                    {row["resolver_source_id"] for row in resolved_assertions}
                ),
                "majority_vote_used": False,
                "can_advance": False,
            }
            record = {
                "conflicting_identity_id": _stable_id(
                    "CONFLICTING-IDENTITY",
                    "schema-v7-production-conflicting-identity-v1",
                    body,
                ),
                **body,
            }
            conflict_records.append(record)
            state["identity_status"] = "conflicting"
            state["relationship_status"] = "not_applicable"
            state["conflict_record_ids"] = [record["conflicting_identity_id"]]
            seed_states[seed_id] = state
            continue

        fingerprint = next(iter(fingerprints))
        identity = fingerprints[fingerprint]
        normalized_id = _normalized_intervention_id(identity)
        state["identity_status"] = "resolved"
        state["normalized_intervention_id"] = normalized_id
        state["exact_fingerprint"] = fingerprint
        state["identity"] = identity
        state["resolved_assertions"] = resolved_assertions
        group = exact_groups.setdefault(
            normalized_id,
            {
                "identity": identity,
                "identity_projection": _identity_projection(identity),
                "fingerprint": fingerprint,
                "seed_ids": [],
                "assertions": [],
            },
        )
        if (
            group["fingerprint"] != fingerprint
            or group["identity_projection"] != _identity_projection(identity)
        ):
            raise DispositionAggregateConflictError(
                "normalized intervention identity collision"
            )
        group["seed_ids"].append(seed_id)
        group["assertions"].extend(resolved_assertions)
        seed_states[seed_id] = state

    normalized_interventions: list[dict[str, Any]] = []
    active_moieties: dict[str, dict[str, Any]] = {}
    relationship_rows: dict[str, dict[str, Any]] = {}
    breadth_groups: dict[str, dict[str, Any]] = {}

    for normalized_id in sorted(exact_groups):
        group = exact_groups[normalized_id]
        identity = group["identity"]
        assertions = sorted(
            {row["assertion_id"]: row for row in group["assertions"]}.values(),
            key=lambda row: row["assertion_id"],
        )
        relationship_sets = {
            _sha256(row["identity"]["active_moieties"]): row["identity"]["active_moieties"]
            for row in assertions
        }
        relationship_status = "resolved" if len(relationship_sets) == 1 else "conflicting"
        active_rows: list[dict[str, str]] = []
        for rows in relationship_sets.values():
            for row in rows:
                if row not in active_rows:
                    active_rows.append(row)
        active_rows.sort(key=_canonical_bytes)
        active_ids = sorted({_active_moiety_id(row) for row in active_rows})
        breadth_id = None
        if relationship_status == "resolved":
            breadth_id = _breadth_group_id(identity, normalized_id, active_rows)
        resolver_assertion_ids = [row["assertion_id"] for row in assertions]
        resolver_source_ids = sorted(
            {row["resolver_source_id"] for row in assertions}
        )
        intervention_body = {
            "normalized_intervention_id": normalized_id,
            "entity_kind": identity["entity_kind"],
            "identity_fingerprint_sha256": group["fingerprint"],
            "registry_identifiers": sorted(
                {
                    (row["namespace"], row["identifier"]): row
                    for assertion in assertions
                    for row in assertion["identity"]["registry_identifiers"]
                }.values(),
                key=lambda row: (row["namespace"], row["identifier"]),
            ),
            "canonical_structure": identity["canonical_structure"],
            "composition_status": identity["composition_status"],
            "components": identity["components"],
            "product": identity["product"],
            "breadth_group_id": breadth_id,
            "active_moiety_ids": active_ids,
            "active_moiety_relationship_status": relationship_status,
            "source_seed_ids": sorted(set(group["seed_ids"])),
            "resolver_assertion_ids": resolver_assertion_ids,
            "resolver_source_ids": resolver_source_ids,
            "source_reported_identities": sorted(
                {row["reported_identity"] for row in assertions}
            ),
            "raw_names_used_for_identity": False,
            "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        }
        normalized_interventions.append(intervention_body)

        for row in active_rows:
            active_id = _active_moiety_id(row)
            source_assertion_ids = sorted(
                assertion["assertion_id"]
                for assertion in assertions
                if row in assertion["identity"]["active_moieties"]
            )
            source_ids = sorted(
                {
                    assertion["resolver_source_id"]
                    for assertion in assertions
                    if row in assertion["identity"]["active_moieties"]
                }
            )
            active = {
                "active_moiety_id": active_id,
                "namespace": row["moiety_namespace"],
                "identifier": row["moiety_identifier"],
                "entity_kind": row["moiety_entity_kind"],
                "resolver_assertion_ids": source_assertion_ids,
                "resolver_source_ids": source_ids,
            }
            prior_active = active_moieties.get(active_id)
            if prior_active is not None:
                active["resolver_assertion_ids"] = sorted(
                    set(prior_active["resolver_assertion_ids"])
                    | set(source_assertion_ids)
                )
                active["resolver_source_ids"] = sorted(
                    set(prior_active["resolver_source_ids"]) | set(source_ids)
                )
            active_moieties[active_id] = active
            relationship_body = {
                "normalized_intervention_id": normalized_id,
                "active_moiety_id": active_id,
                "relationship_type": row["relationship_type"],
                "relationship_status": relationship_status,
                "exact_form_scope": row["exact_form_scope"],
                "resolver_assertion_ids": source_assertion_ids,
                "resolver_source_ids": source_ids,
                "automatic_evidence_transfer": False,
            }
            relationship_id = _stable_id(
                "ACTIVE-MOIETY-RELATIONSHIP",
                "schema-v7-production-active-moiety-relationship-v1",
                relationship_body,
            )
            relationship_rows[relationship_id] = {
                "active_moiety_relationship_id": relationship_id,
                **relationship_body,
            }

        if relationship_status == "conflicting":
            conflict_body = {
                "seed_ids": sorted(set(group["seed_ids"])),
                "conflict_kind": "active_moiety_relationship",
                "identity_fingerprints": sorted(relationship_sets),
                "resolver_assertion_ids": resolver_assertion_ids,
                "resolver_source_ids": resolver_source_ids,
                "majority_vote_used": False,
                "can_advance": False,
            }
            conflict = {
                "conflicting_identity_id": _stable_id(
                    "CONFLICTING-IDENTITY",
                    "schema-v7-production-conflicting-identity-v1",
                    conflict_body,
                ),
                **conflict_body,
            }
            conflict_records.append(conflict)
            for seed_id in group["seed_ids"]:
                seed_states[seed_id]["relationship_status"] = "conflicting"
                seed_states[seed_id]["active_moiety_ids"] = active_ids
                seed_states[seed_id]["conflict_record_ids"].append(
                    conflict["conflicting_identity_id"]
                )
            continue

        for seed_id in group["seed_ids"]:
            seed_states[seed_id]["relationship_status"] = "resolved"
            seed_states[seed_id]["breadth_group_id"] = breadth_id
            seed_states[seed_id]["active_moiety_ids"] = active_ids

        assert breadth_id is not None
        breadth_projection = (
            {
                "rollup_kind": "confirmed_active_moiety",
                "active_moiety_id": active_ids[0],
            }
            if identity["entity_kind"] in _ROLLUP_ENTITY_KINDS
            and len(active_ids) == 1
            else {
                "rollup_kind": "exact_intervention",
                "normalized_intervention_id": normalized_id,
            }
        )
        breadth = breadth_groups.setdefault(
            breadth_id,
            {
                "breadth_group_id": breadth_id,
                **breadth_projection,
                "normalized_intervention_ids": [],
                "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
            },
        )
        breadth["normalized_intervention_ids"].append(normalized_id)

    for breadth in breadth_groups.values():
        breadth["normalized_intervention_ids"] = sorted(
            set(breadth["normalized_intervention_ids"])
        )

    eligible_groups: dict[tuple[str, str], list[str]] = {}
    for seed_id, state in seed_states.items():
        result = state["result"]
        if (
            state["identity_status"] == "resolved"
            and state["relationship_status"] == "resolved"
            and result["result_status"] == "resolved"
            and result["case_role"] in {"repurposing", "baseline"}
        ):
            eligible_groups.setdefault(
                (result["case_role"], state["normalized_intervention_id"]), []
            ).append(seed_id)
    representatives = {
        key: min(values) for key, values in eligible_groups.items()
    }

    identity_resolutions: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    merge_links: list[dict[str, Any]] = []
    for seed_id in sorted(seed_states):
        state = seed_states[seed_id]
        result = state["result"]
        assertion_ids = sorted(row["assertion_id"] for row in state["assertions"])
        source_ids = sorted(
            set(result["resolver_source_ids"])
            | {row["resolver_source_id"] for row in state["assertions"]}
        )
        resolution_body = {
            "seed_id": seed_id,
            "status": state["identity_status"],
            "active_moiety_relationship_status": state["relationship_status"],
            "normalized_intervention_id": state["normalized_intervention_id"],
            "verified_normalized_intervention_id": state[
                "normalized_intervention_id"
            ],
            "breadth_group_id": state["breadth_group_id"],
            "active_moiety_ids": state["active_moiety_ids"],
            "identity_verified": state["identity_status"] == "resolved",
            "decision_changing_ambiguity": state["identity_status"]
            in {"unresolved", "conflicting"}
            or state["relationship_status"] in {"unresolved", "conflicting"}
            or result["case_role"] == "unknown",
            "resolver_assertion_ids": assertion_ids,
            "resolver_result_id": result["result_id"],
            "resolver_source_ids": source_ids,
            "unresolved_identity_ids": sorted(state["unresolved_record_ids"]),
            "conflicting_identity_ids": sorted(state["conflict_record_ids"]),
            "raw_source_identity": _plain(seed_by_id[seed_id]["compound_hint"]),
            "rule_version": NORMALIZATION_POLICY_VERSION,
        }
        resolution = {
            "identity_resolution_id": _stable_id(
                "IDENTITY-RESOLUTION",
                "schema-v7-production-identity-resolution-v1",
                resolution_body,
            ),
            **resolution_body,
        }
        identity_resolutions.append(resolution)

        representative_seed_id = None
        if result["result_status"] == "technical_failure":
            canonical = "failed"
            reason_code = result["reason_code"]
            reason = result["reason"]
        elif state["identity_status"] in {"unresolved", "conflicting"}:
            canonical = "quarantine"
            reason_code = "decision_changing_identity_ambiguity"
            reason = "Authoritative exact identity is unresolved or conflicting."
        elif state["relationship_status"] != "resolved":
            canonical = "quarantine"
            reason_code = "active_moiety_relationship_ambiguity"
            reason = "Active-moiety or exact-form relationship assertions conflict."
        elif result["result_status"] == "unresolved" or result["case_role"] == "unknown":
            canonical = "quarantine"
            reason_code = result["reason_code"]
            reason = result["reason"]
        elif result["case_role"] == "ineligible":
            canonical = "reject"
            reason_code = result["reason_code"]
            reason = result["reason"]
        else:
            key = (result["case_role"], state["normalized_intervention_id"])
            representative_seed_id = representatives[key]
            if representative_seed_id == seed_id:
                canonical = (
                    "admit" if result["case_role"] == "repurposing" else "baseline"
                )
                representative_seed_id = None
                reason_code = (
                    "resolved_repurposing_representative"
                    if canonical == "admit"
                    else "resolved_baseline_representative"
                )
                reason = (
                    "Deterministic representative for one resolved repurposing identity."
                    if canonical == "admit"
                    else "Deterministic representative for one resolved baseline-care identity."
                )
            else:
                canonical = "merge"
                reason_code = "verified_exact_identity_duplicate"
                reason = (
                    "A frozen authoritative exact-identity match links this seed to "
                    "the deterministic representative without deleting lineage."
                )
        disposition_body = {
            "seed_id": seed_id,
            "canonical_disposition": canonical,
            "reason_code": reason_code,
            "reason": reason,
            "case_role": result["case_role"],
            "normalized_intervention_id": state["normalized_intervention_id"],
            "breadth_group_id": state["breadth_group_id"],
            "active_moiety_ids": state["active_moiety_ids"],
            "identity_resolution_id": resolution["identity_resolution_id"],
            "representative_seed_id": representative_seed_id,
            "seed_lineage_id": lineage_by_seed[seed_id]["lineage_id"],
            "source_mapping_id": seed_by_id[seed_id]["source_mapping_id"],
            "discovery_route_ids": list(seed_by_id[seed_id]["discovery_route_ids"]),
            "resolver_assertion_ids": assertion_ids,
            "resolver_result_id": result["result_id"],
            "resolver_source_ids": source_ids,
            "rule_version": DISPOSITION_RULE_VERSION,
        }
        disposition = {
            "seed_disposition_id": _stable_id(
                "SEED-DISPOSITION",
                "schema-v7-production-seed-disposition-id-v1",
                disposition_body,
            ),
            **disposition_body,
        }
        dispositions.append(disposition)
        if canonical == "merge":
            assert representative_seed_id is not None
            link_body = {
                "seed_id": seed_id,
                "representative_seed_id": representative_seed_id,
                "normalized_intervention_id": state["normalized_intervention_id"],
                "case_role": result["case_role"],
                "resolver_assertion_ids": assertion_ids,
                "seed_lineage_id": lineage_by_seed[seed_id]["lineage_id"],
                "merge_basis": "verified_exact_identity",
            }
            merge_links.append(
                {
                    "merge_link_id": _stable_id(
                        "MERGE-LINK",
                        "schema-v7-production-merge-link-v1",
                        link_body,
                    ),
                    **link_body,
                }
            )

    disposition_counts = {
        value: sum(
            row["canonical_disposition"] == value for row in dispositions
        )
        for value in sorted(CANONICAL_DISPOSITIONS)
    }
    resolution_by_seed = {row["seed_id"]: row for row in identity_resolutions}
    disposition_by_seed = {row["seed_id"]: row for row in dispositions}
    identity_all = {
        row["normalized_intervention_id"]
        for row in identity_resolutions
        if row["normalized_intervention_id"]
        and disposition_by_seed[row["seed_id"]]["canonical_disposition"] != "failed"
    }
    identity_admitted = {
        row["normalized_intervention_id"]
        for row in dispositions
        if row["canonical_disposition"] == "admit"
    }
    identity_baseline = {
        row["normalized_intervention_id"]
        for row in dispositions
        if row["canonical_disposition"] == "baseline"
    }
    breadth_admitted = {
        row["breadth_group_id"]
        for row in dispositions
        if row["canonical_disposition"] == "admit"
    }
    active_all = {
        active_id
        for row in identity_resolutions
        if disposition_by_seed[row["seed_id"]]["canonical_disposition"] != "failed"
        and row["active_moiety_relationship_status"] == "resolved"
        for active_id in row["active_moiety_ids"]
    }
    active_admitted = {
        active_id
        for row in dispositions
        if row["canonical_disposition"] == "admit"
        for active_id in row["active_moiety_ids"]
    }
    identity_denominators = {
        "N_identity_all": len(identity_all),
        "N_identity_admitted": len(identity_admitted),
        "N_identity_baseline": len(identity_baseline),
        "N_breadth_admitted": len(breadth_admitted),
        "N_active_moiety_all": len(active_all),
        "N_active_moiety_admitted": len(active_admitted),
    }
    reconciliation = {
        "N_seed": len(seed_rows),
        "N_admit": disposition_counts["admit"],
        "N_merge": disposition_counts["merge"],
        "N_baseline": disposition_counts["baseline"],
        "N_reject": disposition_counts["reject"],
        "N_quarantine": disposition_counts["quarantine"],
        "N_failed": disposition_counts["failed"],
        "seed_lineage_count": len(lineage_rows),
        "identity_resolution_count": len(identity_resolutions),
        "seed_disposition_count": len(dispositions),
        "merge_link_count": len(merge_links),
        "seed_equation": (
            "N_seed = N_admit + N_merge + N_baseline + N_reject + "
            "N_quarantine + N_failed"
        ),
        "seed_equation_balanced": len(seed_rows) == sum(disposition_counts.values()),
        "all_seed_lineage_preserved": len(lineage_rows) == len(seed_rows),
        "all_seed_identity_resolutions_present": len(resolution_by_seed)
        == len(seed_rows),
        "all_seed_dispositions_present": len(disposition_by_seed) == len(seed_rows),
    }
    stage_gate_passed = (
        reconciliation["seed_equation_balanced"]
        and reconciliation["all_seed_lineage_preserved"]
        and reconciliation["all_seed_identity_resolutions_present"]
        and reconciliation["all_seed_dispositions_present"]
        and reconciliation["N_failed"] == 0
    )
    plan_projection = {
        "case_revision_id": case.case_revision_id,
        "resolver_revision": resolver["resolver_revision"],
        "normalization_policy_version": resolver["normalization_policy_version"],
    }
    plan_id = _stable_id("DISPOSITION-PLAN", PLAN_ID_RULE, plan_projection)
    draft: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "aggregate_id": "",
        "disposition_plan_id": plan_id,
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "aggregate_status": "complete" if stage_gate_passed else "diagnostic_partial",
        "stage_gate_passed": stage_gate_passed,
        "input_receipts": {
            "case_source_input_sha256": case.source_input_sha256,
            "seeds_sha256": _sha256(seed_rows),
            "frozen_resolver_assertions_sha256": _sha256(resolver),
        },
        "seeds": seed_rows,
        "seed_lineage": lineage_rows,
        "resolver_provenance": resolver,
        "normalized_interventions": sorted(
            normalized_interventions,
            key=lambda row: row["normalized_intervention_id"],
        ),
        "active_moieties": [active_moieties[key] for key in sorted(active_moieties)],
        "active_moiety_relationships": [
            relationship_rows[key] for key in sorted(relationship_rows)
        ],
        "breadth_groups": [breadth_groups[key] for key in sorted(breadth_groups)],
        "identity_resolutions": identity_resolutions,
        "seed_dispositions": dispositions,
        "merge_links": sorted(merge_links, key=lambda row: row["merge_link_id"]),
        "unresolved_identity_records": sorted(
            unresolved_records, key=lambda row: row["unresolved_identity_id"]
        ),
        "conflicting_identity_records": sorted(
            conflict_records, key=lambda row: row["conflicting_identity_id"]
        ),
        "identity_denominators": identity_denominators,
        "reconciliation": reconciliation,
    }
    draft["aggregate_id"] = _stable_id(
        "DISPOSITION-AGGREGATE",
        AGGREGATE_ID_RULE,
        {key: value for key, value in draft.items() if key != "aggregate_id"},
    )
    return draft


def validate_disposition_aggregate(
    case_revision: CaseRevision | Mapping[str, Any], aggregate: Mapping[str, Any]
) -> None:
    """Rebuild the aggregate from retained inputs and require byte-exact equality."""

    case = _coerce_case(case_revision)
    if not isinstance(aggregate, Mapping):
        raise DispositionAggregateError("disposition aggregate must be an object")
    seeds = aggregate.get("seeds")
    resolver = aggregate.get("resolver_provenance")
    if not isinstance(seeds, list) or not isinstance(resolver, Mapping):
        raise DispositionAggregateError(
            "disposition aggregate lacks retained seeds or resolver provenance"
        )
    seed_ids = [row.get("seed_id") for row in seeds if isinstance(row, Mapping)]
    if len(seed_ids) != len(set(seed_ids)):
        raise DispositionAggregateError("aggregate seeds contain duplicate identities")
    dispositions = aggregate.get("seed_dispositions")
    if not isinstance(dispositions, list):
        raise DispositionAggregateError("aggregate seed_dispositions must be a list")
    disposition_seed_ids = [
        row.get("seed_id") for row in dispositions if isinstance(row, Mapping)
    ]
    if len(disposition_seed_ids) != len(set(disposition_seed_ids)):
        raise DispositionAggregateError(
            "every seed must receive exactly one disposition; duplicates exist"
        )
    if set(disposition_seed_ids) != set(seed_ids):
        raise DispositionAggregateError(
            "every and only canonical seed must receive one disposition"
        )
    disposition_by_seed = {
        row["seed_id"]: row for row in dispositions if isinstance(row, Mapping)
    }
    for row in dispositions:
        if not isinstance(row, Mapping):
            raise DispositionAggregateError("seed disposition must be an object")
        canonical = row.get("canonical_disposition")
        if canonical not in CANONICAL_DISPOSITIONS:
            raise DispositionAggregateError("seed disposition has invalid controlled value")
        if canonical == "merge":
            representative = row.get("representative_seed_id")
            target = disposition_by_seed.get(representative)
            if representative == row.get("seed_id") or target is None:
                raise DispositionAggregateError("merge link is missing, self-linked, or cyclic")
            if target.get("canonical_disposition") not in {"admit", "baseline"}:
                raise DispositionAggregateError(
                    "merge link must terminate directly at admit or baseline"
                )
    expected = _construct_aggregate(case, seeds, resolver)
    if _canonical_bytes(expected) != _canonical_bytes(aggregate):
        raise DispositionAggregateConflictError(
            "disposition aggregate differs from deterministic retained-input reconstruction"
        )


class V7DispositionAdapter:
    """Production test-facing adapter for persisted whole-case disposition."""

    def __init__(self, persistence_root: str | Path) -> None:
        self.persistence_root = Path(persistence_root).expanduser().resolve()

    def aggregate_path(self, case_revision_id: str, disposition_plan_id: str) -> Path:
        return (
            self.persistence_root
            / _safe_component(case_revision_id)
            / _safe_component(disposition_plan_id)
            / "aggregate.json"
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DispositionAggregateError(
                f"cannot read persisted disposition aggregate: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise DispositionAggregateError(
                "persisted disposition aggregate is not an object"
            )
        return value

    @staticmethod
    def _write_once(path: Path, value: Mapping[str, Any]) -> None:
        payload = _canonical_bytes(value) + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != payload:
                raise DispositionAggregateConflictError(
                    f"immutable disposition artifact already exists with different content: {path}"
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

    def normalize_and_dispose(
        self,
        case_revision: CaseRevision | Mapping[str, Any],
        seeds: Iterable[Mapping[str, Any] | Any],
        frozen_resolver_assertions: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Normalize, reconcile, validate, and persist one complete seed frame."""

        case = _coerce_case(case_revision)
        seed_rows = _normalize_seeds(case, seeds)
        resolver = _normalize_resolver_bundle(
            {row["seed_id"] for row in seed_rows}, frozen_resolver_assertions
        )
        plan_id = _stable_id(
            "DISPOSITION-PLAN",
            PLAN_ID_RULE,
            {
                "case_revision_id": case.case_revision_id,
                "resolver_revision": resolver["resolver_revision"],
                "normalization_policy_version": resolver[
                    "normalization_policy_version"
                ],
            },
        )
        target = self.aggregate_path(case.case_revision_id, plan_id)
        input_hashes = {
            "seeds_sha256": _sha256(seed_rows),
            "frozen_resolver_assertions_sha256": _sha256(resolver),
        }
        if target.is_file():
            stored = self._read_json(target)
            receipts = stored.get("input_receipts", {})
            if any(receipts.get(key) != value for key, value in input_hashes.items()):
                raise DispositionAggregateConflictError(
                    "persisted disposition-plan identity was replayed with different seeds or resolver content"
                )
            validate_disposition_aggregate(case, stored)
            return stored

        aggregate = _construct_aggregate(case, seed_rows, resolver)
        validate_disposition_aggregate(case, aggregate)
        self._write_once(target, aggregate)
        return self._read_json(target)


__all__ = [
    "AGGREGATE_ID_RULE",
    "CANONICAL_DISPOSITIONS",
    "DISPOSITION_RULE_VERSION",
    "DispositionAggregateConflictError",
    "DispositionAggregateError",
    "MODEL_VERSION",
    "NORMALIZATION_POLICY_VERSION",
    "V7DispositionAdapter",
    "validate_disposition_aggregate",
]
