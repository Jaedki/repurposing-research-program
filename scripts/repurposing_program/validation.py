"""Shared structural validation primitives for programme records."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .contracts import (
    CANONICAL_DOCUMENT_ID,
    RESEARCH_DOCUMENT_BASE_FIELDS,
    ROW_SCHEMAS,
    _SECRET_KEYS,
)
from .errors import ProgramError
from .evidence import _rows
def _validate_contract_object(value: Any, schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(value, Mapping): raise ProgramError(f"{label} must be an object")
    if missing := sorted(set(schema.get("required_fields", [])) - set(value)): raise ProgramError(f"{label} is missing fields: {', '.join(missing)}")
    if schema.get("additional_fields") is False and (unexpected := sorted(set(value) - set(schema.get("required_fields", [])))): raise ProgramError(f"{label} has unexpected fields: {unexpected}")
    for field, contract in schema.get("field_contracts", {}).items():
        if field not in value: continue
        item, item_label = value[field], f"{label}.{field}"
        if (allowed := contract.get("allowed_values")) is not None and item not in allowed: raise ProgramError(f"{item_label} must be one of {allowed}")
        if (value_type := contract.get("type")) == "object": _validate_contract_object(item, contract, item_label)
        elif value_type in {"list of objects", "non-empty list of objects"}:
            if not isinstance(item, list) or (value_type.startswith("non-empty") and not item): raise ProgramError(f"{item_label} must be a {value_type}")
            for index, nested in enumerate(item): _validate_contract_object(nested, contract, f"{item_label}[{index}]")
        elif value_type == "non-empty string" and (not isinstance(item, str) or not item.strip()): raise ProgramError(f"{item_label} must be a non-empty string")
        elif value_type in {"list of non-empty strings", "non-empty list of non-empty strings", "list of unique non-empty strings", "non-empty list of unique non-empty strings"}:
            if not isinstance(item, list) or (value_type.startswith("non-empty") and not item) or any(not isinstance(nested, str) or not nested.strip() for nested in item): raise ProgramError(f"{item_label} must be a {value_type}")
            if "unique" in value_type and len(item) != len(set(item)): raise ProgramError(f"{item_label} values must be unique")
def _contract_rows(
    records: Mapping[str, Any], name: str, id_field: str | None = None
) -> list[dict[str, Any]]:
    rows = _rows(records, name)
    schema = ROW_SCHEMAS[name]
    for index, row in enumerate(rows):
        _validate_contract_object(row, schema, f"{name}[{index}]")
        for field, field_type in schema.get("field_types", {}).items():
            if field_type == "object" and not isinstance(row[field], dict):
                raise ProgramError(f"{name}[{index}].{field} must be an object")
    if id_field:
        _ids(rows, id_field, name)
    return rows


def _ids(rows: list[dict[str, Any]], field: str, label: str) -> set[str]:
    values: list[str] = []
    for index, row in enumerate(rows):
        value = str(row.get(field, "")).strip()
        if not value:
            raise ProgramError(f"{label}[{index}].{field} is required")
        values.append(value)
    if len(values) != len(set(values)):
        raise ProgramError(f"{label}.{field} values must be unique")
    return set(values)


def _required(row: Mapping[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if row.get(field) in (None, "")]
    if missing:
        raise ProgramError(f"{label} is missing required fields: {', '.join(missing)}")


def _references(row: Mapping[str, Any], field: str, allowed: set[str], label: str) -> set[str]:
    values = row.get(field)
    if not isinstance(values, list) or not values:
        raise ProgramError(f"{label}.{field} must be a non-empty list")
    refs = {str(value) for value in values}
    unknown = refs - allowed
    if unknown:
        raise ProgramError(f"{label}.{field} contains unknown IDs: {sorted(unknown)}")
    return refs


def _validate_cited_entries(
    value: Any,
    *,
    label: str,
    text_field: str,
    source_ids: set[str],
) -> None:
    if not isinstance(value, list):
        raise ProgramError(f"{label} must be a list of objects")
    seen: set[str] = set()
    required_fields = {text_field, "source_ids"}
    for index, entry in enumerate(value):
        entry_label = f"{label}[{index}]"
        if not isinstance(entry, dict):
            raise ProgramError(f"{entry_label} must be an object")
        missing = sorted(required_fields - set(entry))
        if missing:
            raise ProgramError(f"{entry_label} is missing fields: {', '.join(missing)}")
        unexpected = sorted(set(entry) - required_fields)
        if unexpected:
            raise ProgramError(f"{entry_label} has unexpected fields: {unexpected}")
        text = str(entry[text_field]).strip()
        if not text:
            raise ProgramError(f"{entry_label}.{text_field} must be non-empty")
        key = text.casefold()
        if key in seen:
            raise ProgramError(f"{label}.{text_field} values must be unique")
        seen.add(key)
        _references(entry, "source_ids", source_ids, entry_label)


def _secret_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_", str(key).casefold()
            ).strip("_")
            if normalized_key in _SECRET_KEYS:
                found.append(child)
            found.extend(_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{path}[{index}]"))
    return found


def _validate_exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProgramError(f"{label} must be an object")
    missing = sorted(fields - set(value))
    unexpected = sorted(set(value) - fields)
    if missing:
        raise ProgramError(f"{label} is missing fields: {', '.join(missing)}")
    if unexpected:
        raise ProgramError(f"{label} has unexpected fields: {unexpected}")
    return value


def _validate_documents(
    records: Mapping[str, Any], *, canonical_ids: bool = False
) -> list[dict[str, Any]]:
    documents = _contract_rows(records, "documents", "document_id")
    for index, row in enumerate(documents):
        _required(row, RESEARCH_DOCUMENT_BASE_FIELDS, f"documents[{index}]")
        if canonical_ids and not CANONICAL_DOCUMENT_ID.fullmatch(str(row["document_id"])):
            raise ProgramError(
                f"documents[{index}].document_id must be a canonical PMID, PMCID, DOI, "
                "S2 Semantic Scholar ID, authoritative accession, or HTTPS URL"
            )
    return documents
