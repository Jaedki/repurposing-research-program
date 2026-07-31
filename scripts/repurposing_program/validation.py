"""Shared structural validation primitives for programme records."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import CANONICAL_DOCUMENT_ID, ROW_SCHEMAS, _SECRET_KEYS
from .errors import ProgramError
from .evidence import _rows


def _contract_rows(
    records: Mapping[str, Any], name: str, id_field: str | None = None
) -> list[dict[str, Any]]:
    rows = _rows(records, name)
    schema = ROW_SCHEMAS[name]
    required_fields = schema["required_fields"]
    for index, row in enumerate(rows):
        missing = [field for field in required_fields if field not in row]
        if missing:
            raise ProgramError(f"{name}[{index}] is missing fields: {', '.join(missing)}")
        if not schema["additional_fields"]:
            unexpected = sorted(set(row) - set(required_fields))
            if unexpected:
                raise ProgramError(f"{name}[{index}] has unexpected fields: {unexpected}")
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


def _secret_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key).lower() in _SECRET_KEYS:
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
        _required(row, ("document_id", "title", "source"), f"documents[{index}]")
        if canonical_ids and not CANONICAL_DOCUMENT_ID.fullmatch(str(row["document_id"])):
            raise ProgramError(
                f"documents[{index}].document_id must be a canonical PMID, PMCID, DOI, "
                "authoritative accession, or HTTPS URL"
            )
    return documents
