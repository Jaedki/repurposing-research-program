#!/usr/bin/env python3
"""Schema-v5 structural, provenance, scientific-audit, and runtime validation."""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from program_contract import (
    AUDIT_QUERY_FAMILIES,
    AUDIT_VERDICTS,
    BROAD_DOMAINS,
    CALIBRATIONS,
    CANDIDATE_CLASSES,
    CLAIM_DIRECTIONS,
    COMPOUND_ORIGINS,
    COUNCIL_DISPOSITIONS,
    GLOBAL_PERSPECTIVES,
    HUMAN_RELEVANCE_LEVELS,
    HUMAN_OUTCOME_NODE,
    MAX_ACTIVE_JOBS,
    NESTED_SCHEMAS,
    RANKING_VERSION,
    SCHEMAS,
    SCHEMA_VERSION,
    SCIENTIFIC_AUDIT_STATUSES,
    SOURCE_ALLOWED_FIELDS,
    SOURCE_AGGREGATE_FIELDS,
    TARGET_ENDPOINT_TYPES,
    allowed_ledgers,
    required_case_present,
    required_query_families,
)
from program_io import content_hash, file_hash, index_rows, inside, read_json, read_jsonl


REQUIRED_FILES = (
    "case.json",
    "program_state.json",
    "execution_plan.json",
    "orchestration.jsonl",
    "job_attempts.jsonl",
    *SCHEMAS,
)
STRUCTURE_KEY = re.compile(
    r"^(INCHIKEY:[A-Z]{14}-[A-Z]{10}-[A-Z]|SMILES-SHA256:[0-9A-F]{64})$",
    re.IGNORECASE,
)
PROHIBITED_SOURCE_FIELDS = {
    "raw_payload", "raw_xml", "raw_html", "full_text", "nested_metadata",
    "complete_reference_list", "author_affiliations",
}
AUDIT_MUTABLE_FIELDS = {
    "claim_ledger.jsonl": {
        "source_ids", "calibration", "contrary_claim_ids", "supersedes_claim_ids", "audit_status", "audit_note",
    },
    "evidence_graph.jsonl": {
        "claim_ids", "contrary_edge_ids", "supersedes_edge_ids", "audit_status", "uncertainty",
    },
    "candidate_records.jsonl": {
        "candidate_class", "candidate_class_source_ids", "compound_origin", "target_endpoint",
        "repurposing_readiness", "rationale", "rationale_source_ids", "uncertainty", "audit_status",
        "score_components", "cap_assessments", "experimental_model_suitability", "material_conflicts",
        "raw_score", "total_score", "applied_cap", "rank_section", "rank", "endpoint_rank",
        "ranking_version", "council_status",
    },
}
COUNCIL_MUTABLE_CANDIDATE_FIELDS = {
    "candidate_class", "candidate_class_source_ids", "target_endpoint", "repurposing_readiness",
    "score_components", "cap_assessments", "raw_score", "total_score", "applied_cap",
    "rank_section", "rank", "endpoint_rank", "ranking_version",
}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _unique(values: Iterable[Any]) -> bool:
    rows = [str(value) for value in values]
    return len(rows) == len(set(rows))


def _resolve_file(root: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    if _blank(value):
        errors.append(f"{label}: missing path")
        return None
    try:
        path = inside(root, str(value))
    except Exception:
        errors.append(f"{label}: path must stay inside the run folder")
        return None
    if not path.is_file():
        errors.append(f"{label}: file does not exist: {value}")
        return None
    return path


def _read_receipt(root: Path, value: Any, label: str, errors: list[str]) -> dict[str, Any]:
    path = _resolve_file(root, value, label, errors)
    if not path:
        return {}
    try:
        receipt = read_json(path, {})
    except Exception as exc:
        errors.append(f"{label}: invalid JSON: {exc}")
        return {}
    if not isinstance(receipt, dict):
        errors.append(f"{label}: expected one JSON object")
        return {}
    if receipt.get("schema_version") != 2 or receipt.get("compactor") != "compact_source_payload.py":
        errors.append(f"{label}: invalid compact-source receipt")
    records = receipt.get("records")
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        errors.append(f"{label}: records must be a list of objects")
    else:
        for position, record in enumerate(records, 1):
            stored = str(record.get("compact_record_hash", ""))
            body = {key: value for key, value in record.items() if key != "compact_record_hash"}
            if not stored or stored != content_hash(body):
                errors.append(f"{label}: record {position} hash mismatch")
    return receipt


def _schema_rows(ledgers: dict[str, list[dict[str, Any]]], errors: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for filename, spec in SCHEMAS.items():
        rows = ledgers.get(filename, [])
        seen: dict[str, dict[str, Any]] = {}
        allowed = set(spec["fields"])
        for position, row in enumerate(rows, 1):
            label = f"{filename} row {position}"
            missing = [field for field in spec["fields"] if field not in row]
            if missing:
                errors.append(f"{label}: missing fields {missing}")
            empty = [field for field in spec["nonempty"] if _blank(row.get(field))]
            if empty:
                errors.append(f"{label}: empty required fields {empty}")
            unknown = set(row) - allowed
            if unknown:
                errors.append(f"{label}: unknown fields {sorted(unknown)}")
            identity = str(row.get(spec["key"], "")).strip()
            if identity in seen:
                errors.append(f"{filename}: duplicate {spec['key']} {identity!r}")
            elif identity:
                seen[identity] = row
        indexes[filename] = seen
    return indexes


def _validate_sources_and_searches(
    root: Path,
    indexes: dict[str, dict[str, dict[str, Any]]],
    plan_jobs: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    sources = indexes["source_corpus.jsonl"]
    searches = indexes["search_log.jsonl"]
    units = indexes["research_units.jsonl"]
    claims = indexes["claim_ledger.jsonl"]
    observations = indexes["candidate_observations.jsonl"]

    canonical_sources: dict[str, str] = {}
    for source_id, source in sources.items():
        label = f"source {source_id}"
        if set(source) - SOURCE_ALLOWED_FIELDS:
            errors.append(f"{label}: source schema drift")
        if PROHIBITED_SOURCE_FIELDS.intersection(source):
            errors.append(f"{label}: bulky raw-source fields are prohibited")
        if source.get("screen_decision") not in {"include", "exclude"}:
            errors.append(f"{label}: screen_decision must be include or exclude")
        canonical_identifier = str(source.get("canonical_identifier", "")).strip()
        canonical_key = canonical_identifier.split("#", 1)[0].casefold()
        if "#" in canonical_identifier:
            errors.append(f"{label}: canonical_identifier must identify the source, not an article section")
        prior_source = canonical_sources.get(canonical_key)
        if canonical_key and prior_source and prior_source != source_id:
            errors.append(f"{label}: duplicate canonical source identity with {prior_source}")
        elif canonical_key:
            canonical_sources[canonical_key] = source_id
        receipt = _read_receipt(root, source.get("compaction_receipt_path"), f"{label} receipt", errors)
        matching = [
            row for row in _items(receipt.get("records"))
            if str(row.get("compact_record_hash")) == str(source.get("compaction_record_hash"))
        ]
        if len(matching) != 1:
            errors.append(f"{label}: compact record hash must resolve exactly once")
        elif (
            str(matching[0].get("canonical_identifier")) != str(source.get("canonical_identifier"))
            or str(matching[0].get("title")) != str(source.get("title"))
        ):
            errors.append(f"{label}: compact identity mismatch")
        discovered_units = [str(value) for value in _items(source.get("discovered_by_units"))]
        discovery_queries = [str(value) for value in _items(source.get("discovery_query_ids"))]
        supported_claims = [str(value) for value in _items(source.get("supported_claim_ids"))]
        if not all(_unique(values) for values in (discovered_units, discovery_queries, supported_claims)):
            errors.append(f"{label}: discovery and supported-claim lists must be unique")
        for unit_id in discovered_units:
            if str(unit_id) not in units:
                errors.append(f"{label}: unknown discovery unit {unit_id}")
        for query_id in discovery_queries:
            query = searches.get(str(query_id))
            if not query:
                errors.append(f"{label}: unknown discovery query {query_id}")
            elif source_id not in {str(value) for value in _items(query.get("acquired_source_ids"))}:
                errors.append(f"{label}: discovery query {query_id} lacks reverse acquired-source linkage")
            elif str(query.get("research_unit_id")) not in discovered_units:
                errors.append(f"{label}: discovery query {query_id} has an unlisted discovery unit")
        for claim_id in supported_claims:
            if str(claim_id) not in claims:
                errors.append(f"{label}: unknown supported claim {claim_id}")
            elif source_id not in {str(value) for value in _items(claims[claim_id].get("source_ids"))}:
                errors.append(f"{label}: supported claim {claim_id} lacks reverse source linkage")

    searches_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for query_id, query in searches.items():
        label = f"query {query_id}"
        unit_id = str(query.get("research_unit_id", ""))
        unit = units.get(unit_id)
        if not unit:
            errors.append(f"{label}: unknown research unit {unit_id}")
            continue
        searches_by_unit[unit_id].append(query)
        if query.get("query_family") not in set(unit.get("planned_query_families", [])):
            errors.append(f"{label}: query family was not predeclared")
        origin = plan_jobs.get(str(query.get("origin_job_id", "")))
        if not origin or str(origin.get("unit_id", "")) != unit_id:
            errors.append(f"{label}: origin_job_id does not match the unit")
        if str(query.get("executed_by_agent_id", "")) != str(origin.get("assigned_agent_id", "")):
            errors.append(f"{label}: executor does not match the controller assignment")
        if query.get("outcome") != "completed" or query.get("pagination_complete") is not True:
            errors.append(f"{label}: search or pagination is incomplete")
        if not isinstance(query.get("result_count"), int) or not isinstance(query.get("screened_count"), int):
            errors.append(f"{label}: result and screened counts must be integers")

        paths = [str(value) for value in _items(query.get("compact_payload_paths"))]
        trace = _items(query.get("pagination_trace"))
        if not paths or len(paths) != len(trace) or not _unique(paths):
            errors.append(f"{label}: receipts and pagination trace must be unique and one-to-one")
        records: list[dict[str, Any]] = []
        prior_output = ""
        for index, (receipt_path, page) in enumerate(zip(paths, trace), 1):
            receipt = _read_receipt(root, receipt_path, f"{label} receipt {index}", errors)
            page_records = _items(receipt.get("records"))
            for record in page_records:
                if str(record.get("query_id", "")) != query_id:
                    errors.append(f"{label}: compact record is bound to another query")
            records.extend(page_records)
            if (
                not isinstance(page, dict)
                or set(page) != set(NESTED_SCHEMAS["pagination_trace"])
                or page.get("page_index") != index
                or str(page.get("receipt_path")) != receipt_path
            ):
                errors.append(f"{label}: pagination trace entry {index} is malformed")
                continue
            input_hash = str(page.get("input_token_hash", ""))
            output_hash = str(page.get("output_token_hash", ""))
            if input_hash != prior_output or (index < len(trace) and not output_hash):
                errors.append(f"{label}: pagination continuation chain is disconnected")
            prior_output = output_hash
        if prior_output:
            errors.append(f"{label}: final continuation was not exhausted")
        if query.get("result_count") != len(records) or query.get("screened_count") != len(records):
            errors.append(f"{label}: receipt records do not prove the search counts")

        acquired = [str(value) for value in _items(query.get("acquired_source_ids"))]
        verified = [str(value) for value in _items(query.get("verified_source_ids"))]
        retained = [str(value) for value in _items(query.get("retained_source_ids"))]
        if not all(_unique(values) for values in (acquired, verified, retained)):
            errors.append(f"{label}: source ID lists must be unique")
        if not set(retained).issubset(verified) or not set(verified).issubset(acquired):
            errors.append(f"{label}: retained must be verified and verified must be acquired")
        receipt_ids = {str(row.get("canonical_identifier", "")).casefold() for row in records}
        for source_id in acquired:
            source = sources.get(source_id)
            if not source:
                errors.append(f"{label}: unknown acquired source {source_id}")
            elif str(source.get("canonical_identifier", "")).casefold() not in receipt_ids:
                errors.append(f"{label}: acquired source {source_id} is absent from receipts")
            else:
                if unit_id not in {str(value) for value in _items(source.get("discovered_by_units"))}:
                    errors.append(f"{label}: acquired source {source_id} lacks reverse unit provenance")
                if query_id not in {str(value) for value in _items(source.get("discovery_query_ids"))}:
                    errors.append(f"{label}: acquired source {source_id} lacks reverse query provenance")
        for source_id in verified:
            source = sources.get(source_id, {})
            if not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                errors.append(f"{label}: source {source_id} lacks verified original content")
        for claim_id in _items(query.get("produced_claim_ids")):
            if str(claim_id) not in claims:
                errors.append(f"{label}: unknown produced claim {claim_id}")
        for observation_id in _items(query.get("produced_observation_ids")):
            if str(observation_id) not in observations:
                errors.append(f"{label}: unknown produced observation {observation_id}")
        if unit.get("unit_type") == "decisive_audit" and str(query.get("resource", "")).strip().casefold().startswith(
            ("packet", "local_packet", "context_packet")
        ):
            errors.append(f"{label}: decisive audit must retrieve evidence independently of its packet")

    for unit_id, unit in units.items():
        planned = set(str(value) for value in _items(unit.get("planned_query_families")))
        required = required_query_families(str(unit.get("unit_type", "")))
        if planned != required:
            errors.append(f"unit {unit_id}: planned query families differ from the authoritative contract")
        logged = {str(row.get("query_family")) for row in searches_by_unit.get(unit_id, [])}
        if unit.get("status") == "complete":
            if set(_items(unit.get("completed_query_families"))) != planned or logged != planned:
                errors.append(f"unit {unit_id}: completed query coverage is incomplete")
            if set(_items(unit.get("search_ids"))) != {str(row.get("query_id")) for row in searches_by_unit.get(unit_id, [])}:
                errors.append(f"unit {unit_id}: search_ids do not match canonical searches")
            if _blank(unit.get("worker_agent_id")) or _blank(unit.get("closure_basis")):
                errors.append(f"unit {unit_id}: completed unit lacks agent or closure basis")
            unit_searches = searches_by_unit.get(unit_id, [])
            if unit.get("unit_type") == "decisive_audit":
                normalized_queries = [str(row.get("query", "")).strip().casefold() for row in unit_searches]
                if len(normalized_queries) != len(set(normalized_queries)):
                    errors.append(f"unit {unit_id}: independent-verification and counterevidence queries must differ")
            exclusions = unit.get("candidate_exclusions")
            if not isinstance(exclusions, list):
                errors.append(f"unit {unit_id}: candidate_exclusions must be a list")
            else:
                for position, exclusion in enumerate(exclusions, 1):
                    exclusion_label = f"unit {unit_id} candidate exclusion {position}"
                    if not isinstance(exclusion, dict) or set(exclusion) != set(NESTED_SCHEMAS["candidate_exclusion"]):
                        errors.append(f"{exclusion_label}: fields do not match the schema")
                        continue
                    if not str(exclusion.get("name", "")).strip() or not str(exclusion.get("reason", "")).strip():
                        errors.append(f"{exclusion_label}: name and reason are required")
                    source_ids = [str(value) for value in _items(exclusion.get("source_ids"))]
                    if not source_ids or not _unique(source_ids) or any(value not in sources for value in source_ids):
                        errors.append(f"{exclusion_label}: source_ids must be nonempty, unique, and resolve")
        elif unit.get("status") != "planned":
            errors.append(f"unit {unit_id}: invalid status {unit.get('status')!r}")


def _validate_evidence(indexes: dict[str, dict[str, dict[str, Any]]], errors: list[str]) -> None:
    sources = indexes["source_corpus.jsonl"]
    claims = indexes["claim_ledger.jsonl"]
    edges = indexes["evidence_graph.jsonl"]
    for claim_id, claim in claims.items():
        if claim.get("calibration") not in CALIBRATIONS:
            errors.append(f"claim {claim_id}: invalid calibration")
        if claim.get("audit_status") not in SCIENTIFIC_AUDIT_STATUSES:
            errors.append(f"claim {claim_id}: invalid audit_status")
        if claim.get("human_relevance") not in HUMAN_RELEVANCE_LEVELS:
            errors.append(
                f"claim {claim_id}: invalid human_relevance; allowed values are {sorted(HUMAN_RELEVANCE_LEVELS)}"
            )
        if claim.get("direction") not in CLAIM_DIRECTIONS:
            errors.append(f"claim {claim_id}: invalid direction; allowed values are {sorted(CLAIM_DIRECTIONS)}")
        for source_id in _items(claim.get("source_ids")):
            source = sources.get(str(source_id))
            if not source:
                errors.append(f"claim {claim_id}: unknown source {source_id}")
            elif not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified")):
                errors.append(f"claim {claim_id}: source {source_id} is not verified")
            elif source.get("screen_decision") != "include":
                errors.append(f"claim {claim_id}: source {source_id} is excluded")
            elif claim_id not in {str(value) for value in _items(source.get("supported_claim_ids"))}:
                errors.append(f"claim {claim_id}: source {source_id} lacks reverse claim linkage")
        for field in ("contrary_claim_ids", "supersedes_claim_ids"):
            for related in _items(claim.get(field)):
                if str(related) not in claims or str(related) == claim_id:
                    errors.append(f"claim {claim_id}: invalid {field} reference {related}")
    for edge_id, edge in edges.items():
        if edge.get("directionality") not in {"supports_benefit", "opposes_benefit", "ambiguous"}:
            errors.append(f"edge {edge_id}: invalid directionality")
        if edge.get("audit_status") not in SCIENTIFIC_AUDIT_STATUSES:
            errors.append(f"edge {edge_id}: invalid audit_status")
        for claim_id in _items(edge.get("claim_ids")):
            if str(claim_id) not in claims:
                errors.append(f"edge {edge_id}: unknown claim {claim_id}")
        for field in ("contrary_edge_ids", "supersedes_edge_ids"):
            for related in _items(edge.get(field)):
                if str(related) not in edges or str(related) == edge_id:
                    errors.append(f"edge {edge_id}: invalid {field} reference {related}")


def _validate_compounds(
    indexes: dict[str, dict[str, dict[str, Any]]],
    *,
    final: bool,
    errors: list[str],
) -> None:
    sources = indexes["source_corpus.jsonl"]
    searches = indexes["search_log.jsonl"]
    claims = indexes["claim_ledger.jsonl"]
    edges = indexes["evidence_graph.jsonl"]
    units = indexes["research_units.jsonl"]
    observations = indexes["candidate_observations.jsonl"]
    candidates = indexes["candidate_records.jsonl"]
    audits = indexes["audit_records.jsonl"]

    def validate_identity(label: str, row: dict[str, Any]) -> str:
        key = str(row.get("structure_identity_key", "")).upper()
        if not STRUCTURE_KEY.fullmatch(key):
            errors.append(f"{label}: invalid structure_identity_key")
        if str(row.get("chemical_node_id", "")) != f"CHEM:{key}":
            errors.append(f"{label}: chemical_node_id does not match the structure key")
        registry = row.get("registry_identifiers")
        if not isinstance(registry, dict) or str(row.get("canonical_identifier")) not in {str(value) for value in registry.values()}:
            errors.append(f"{label}: canonical identifier is absent from registry_identifiers")
        for source_id in _items(row.get("identity_source_ids")):
            source = sources.get(str(source_id))
            if not source:
                errors.append(f"{label}: unknown identity source {source_id}")
            elif (
                not all(source.get(field) is True for field in ("metadata_verified", "original_acquired", "content_verified"))
                or source.get("screen_decision") != "include"
            ):
                errors.append(f"{label}: identity source {source_id} is not verified")
        return key

    def validate_evidence_sources(label: str, values: Any) -> list[str]:
        source_ids = [str(value) for value in _items(values)]
        if not source_ids or not _unique(source_ids):
            errors.append(f"{label}: source IDs must be nonempty and unique")
        for source_id in source_ids:
            source = sources.get(source_id)
            if not source:
                errors.append(f"{label}: unknown source {source_id}")
            elif source.get("content_verified") is not True or source.get("screen_decision") != "include":
                errors.append(f"{label}: source {source_id} must be content-verified and included")
        return source_ids

    for observation_id, observation in observations.items():
        label = f"observation {observation_id}"
        key = validate_identity(label, observation)
        active_moiety_key = str(observation.get("active_moiety_key", "")).upper()
        if not STRUCTURE_KEY.fullmatch(active_moiety_key):
            errors.append(f"{label}: invalid active_moiety_key")
        validate_evidence_sources(f"{label} active moiety", observation.get("active_moiety_source_ids"))
        if not str(observation.get("active_moiety_rationale", "")).strip():
            errors.append(f"{label}: active_moiety_rationale is required")
        unit = units.get(str(observation.get("research_unit_id", "")))
        if not unit or unit.get("unit_type") != "compound_perspective":
            errors.append(f"{label}: observation does not belong to a compound perspective")
        for field, mapping in (("claim_ids", claims), ("edge_ids", edges), ("rationale_source_ids", sources)):
            for value in _items(observation.get(field)):
                if str(value) not in mapping:
                    errors.append(f"{label}: unknown {field} value {value}")
        emitted = any(
            observation_id in {str(value) for value in _items(query.get("produced_observation_ids"))}
            for query in searches.values()
            if str(query.get("research_unit_id")) == str(observation.get("research_unit_id"))
        )
        if not emitted:
            errors.append(f"{label}: no source-unit search emitted the observation")
    candidate_moieties: dict[str, str] = {}
    audits_by_claim: dict[str, dict[str, Any]] = {}
    for row in audits.values():
        if row.get("subject_type") != "claim":
            continue
        claim_id = str(row.get("subject_id"))
        if claim_id in audits_by_claim:
            errors.append(f"claim {claim_id}: multiple audit records are not allowed")
        audits_by_claim[claim_id] = row
    for candidate_id, candidate in candidates.items():
        label = f"candidate {candidate_id}"
        key = validate_identity(label, candidate)
        active_moiety_key = str(candidate.get("active_moiety_key", "")).upper()
        if not STRUCTURE_KEY.fullmatch(active_moiety_key):
            errors.append(f"{label}: invalid active_moiety_key")
        if active_moiety_key in candidate_moieties:
            errors.append(f"{label}: duplicate active moiety with {candidate_moieties[active_moiety_key]}")
        candidate_moieties[active_moiety_key] = candidate_id
        candidate_moiety_sources = validate_evidence_sources(
            f"{label} active moiety", candidate.get("active_moiety_source_ids")
        )
        if not str(candidate.get("active_moiety_rationale", "")).strip():
            errors.append(f"{label}: active_moiety_rationale is required")
        if candidate.get("identity_verified") is not True:
            errors.append(f"{label}: identity_verified must be true")
        applied_cap = candidate.get("applied_cap")
        if not isinstance(applied_cap, dict) or set(applied_cap) != set(NESTED_SCHEMAS["applied_cap"]):
            errors.append(f"{label}: applied_cap fields do not match the schema")
        for field in ("raw_score", "total_score", "rank"):
            if isinstance(candidate.get(field), bool) or not isinstance(candidate.get(field), int) or candidate.get(field) < 0:
                errors.append(f"{label}: {field} must be a non-negative integer")
        observation_ids = [str(value) for value in _items(candidate.get("observation_ids"))]
        formulation_structures = {
            str(value).upper() for value in _items(candidate.get("formulation_structure_keys"))
        }
        if not observation_ids or any(value not in observations for value in observation_ids):
            errors.append(f"{label}: observation_ids must resolve")
        else:
            observation_moieties = {
                str(observations[value].get("active_moiety_key", "")).upper()
                for value in observation_ids
            }
            if observation_moieties != {active_moiety_key}:
                errors.append(f"{label}: merged observations do not share the active moiety")
            observation_moiety_sources = {
                str(source_id)
                for value in observation_ids
                for source_id in _items(observations[value].get("active_moiety_source_ids"))
            }
            if set(candidate_moiety_sources) != observation_moiety_sources:
                errors.append(f"{label}: active-moiety sources must aggregate all merged observations")
            observed_structures = {
                str(observations[value].get("structure_identity_key", "")).upper()
                for value in observation_ids
            }
            if not formulation_structures or formulation_structures != observed_structures or key not in formulation_structures:
                errors.append(f"{label}: formulation_structure_keys must exactly cover merged observation structures")
        source_units = {str(value) for value in _items(candidate.get("source_research_unit_ids"))}
        observed_units = {str(observations[value].get("research_unit_id")) for value in observation_ids if value in observations}
        if source_units != observed_units:
            errors.append(f"{label}: source_research_unit_ids do not match merged observations")
        validate_evidence_sources(f"{label} rationale", candidate.get("rationale_source_ids"))
        if str(candidate.get("human_outcome")) != HUMAN_OUTCOME_NODE:
            errors.append(f"{label}: human_outcome must be {HUMAN_OUTCOME_NODE}")
        if candidate.get("candidate_class") not in CANDIDATE_CLASSES:
            errors.append(f"{label}: invalid candidate_class")
        if candidate.get("compound_origin") not in COMPOUND_ORIGINS:
            errors.append(f"{label}: invalid compound_origin")
        validate_evidence_sources(f"{label} candidate class", candidate.get("candidate_class_source_ids"))
        target_endpoint = candidate.get("target_endpoint")
        endpoint_label = ""
        endpoint_claim_ids: set[str] = set()
        if not isinstance(target_endpoint, dict) or set(target_endpoint) != set(NESTED_SCHEMAS["target_endpoint"]):
            errors.append(f"{label}: target_endpoint fields do not match the schema")
        else:
            endpoint_label = str(target_endpoint.get("label", "")).strip()
            if target_endpoint.get("endpoint_type") not in TARGET_ENDPOINT_TYPES or not endpoint_label:
                errors.append(f"{label}: target endpoint type and label are invalid")
            endpoint_claim_values = [str(value) for value in _items(target_endpoint.get("claim_ids"))]
            endpoint_claim_ids = set(endpoint_claim_values)
            if (
                not endpoint_claim_values
                or len(endpoint_claim_values) != len(endpoint_claim_ids)
                or any(value not in claims for value in endpoint_claim_ids)
            ):
                errors.append(f"{label}: target endpoint claim_ids must be nonempty, unique, and resolve")
            validate_evidence_sources(f"{label} target endpoint", target_endpoint.get("source_ids"))
        readiness = candidate.get("repurposing_readiness")
        if isinstance(readiness, dict):
            validate_evidence_sources(f"{label} repurposing readiness", readiness.get("source_ids"))
        for collection_name in ("score_components", "cap_assessments"):
            collection = candidate.get(collection_name)
            if isinstance(collection, dict):
                for assessment_name, assessment in collection.items():
                    if isinstance(assessment, dict):
                        validate_evidence_sources(
                            f"{label} {collection_name} {assessment_name}", assessment.get("source_ids")
                        )
        model_suitability = candidate.get("experimental_model_suitability")
        if isinstance(model_suitability, dict) and model_suitability.get("assessed") is True:
            validate_evidence_sources(f"{label} model suitability", model_suitability.get("source_ids"))

        path_claims: set[str] = set()
        ambiguous_path = False
        paths = _items(candidate.get("causal_paths"))
        if not paths:
            errors.append(f"{label}: at least one human therapeutic path is required")
        for path_number, path in enumerate(paths, 1):
            if not isinstance(path, dict):
                errors.append(f"{label} path {path_number}: expected an object")
                continue
            required = set(NESTED_SCHEMAS["causal_path"])
            if set(path) != required:
                errors.append(f"{label} path {path_number}: fields must exactly match the causal-path schema")
                continue
            edge_ids = [str(value) for value in _items(path.get("edge_ids"))]
            claim_ids = {str(value) for value in _items(path.get("claim_ids"))}
            concrete = [edges.get(value) for value in edge_ids]
            if not edge_ids or any(edge is None for edge in concrete):
                errors.append(f"{label} path {path_number}: graph edges must resolve")
                continue
            graph = [edge for edge in concrete if edge]
            ambiguous_path = ambiguous_path or any(edge.get("directionality") == "ambiguous" for edge in graph)
            allowed_start_nodes = {f"CHEM:{value}" for value in formulation_structures}
            path_start = str(path.get("start_node"))
            if path_start not in allowed_start_nodes or str(graph[0].get("from_node")) != path_start:
                errors.append(f"{label} path {path_number}: path must start at one retained formulation structure")
            if str(path.get("end_node")) != HUMAN_OUTCOME_NODE or graph[-1].get("to_node") != HUMAN_OUTCOME_NODE:
                errors.append(f"{label} path {path_number}: path must end at the human therapeutic outcome")
            if path.get("expected_direction") != "therapeutic_benefit":
                errors.append(f"{label} path {path_number}: expected_direction must be therapeutic_benefit")
            if str(path.get("target_endpoint", "")).strip() != endpoint_label:
                errors.append(f"{label} path {path_number}: target_endpoint must match the candidate endpoint")
            if any(left.get("to_node") != right.get("from_node") for left, right in zip(graph, graph[1:])):
                errors.append(f"{label} path {path_number}: graph edges are disconnected")
            if any(edge.get("directionality") == "opposes_benefit" for edge in graph):
                errors.append(f"{label} path {path_number}: path contains an opposing edge")
            if final and any(edge.get("audit_status") == "unreviewed" for edge in graph):
                errors.append(f"{label} path {path_number}: decisive graph edge remains unreviewed")
            if not claim_ids or any(value not in claims for value in claim_ids):
                errors.append(f"{label} path {path_number}: claim_ids must resolve")
            if any(not set(str(value) for value in _items(edge.get("claim_ids"))).intersection(claim_ids) for edge in graph):
                errors.append(f"{label} path {path_number}: every edge needs a path claim")
            graph_claim_ids = {
                str(value) for edge in graph for value in _items(edge.get("claim_ids"))
            }
            if not claim_ids.issubset(graph_claim_ids):
                errors.append(f"{label} path {path_number}: every path claim must belong to a path edge")
            path_claims.update(claim_ids)
        decisive = {str(value) for value in _items(candidate.get("decisive_claim_ids"))}
        if decisive != path_claims:
            errors.append(f"{label}: decisive_claim_ids must exactly cover all path claims")
        if not endpoint_claim_ids.issubset(decisive):
            errors.append(f"{label}: target endpoint claims must be decisive path claims")
        endpoint_claim_sources = {
            str(source_id)
            for claim_id in endpoint_claim_ids
            for source_id in _items(claims.get(claim_id, {}).get("source_ids"))
        }
        endpoint_sources_declared = {
            str(value) for value in _items(target_endpoint.get("source_ids"))
        } if isinstance(target_endpoint, dict) else set()
        if not endpoint_sources_declared.issubset(endpoint_claim_sources):
            errors.append(f"{label}: target endpoint sources must support its decisive claims")
        unresolved_from_semantics = ambiguous_path or any(
            claims.get(claim_id, {}).get("direction") in {"opposes_benefit", "unclear"}
            or claims.get(claim_id, {}).get("calibration") in {"unresolved", "contradicted"}
            for claim_id in decisive
        )
        unresolved_cap = candidate.get("cap_assessments", {}).get("unresolved_direction", {}).get("applies")
        if unresolved_from_semantics and unresolved_cap is not True:
            errors.append(f"{label}: ambiguous or adverse path semantics require unresolved_direction cap")
        rationale_sources = {str(value) for value in _items(candidate.get("rationale_source_ids"))}
        claim_linked_sources = {
            str(source_id)
            for claim_id in decisive
            for source_id in _items(claims.get(claim_id, {}).get("source_ids"))
        }
        endpoint_sources = {
            str(value) for value in _items((target_endpoint or {}).get("source_ids"))
        } if isinstance(target_endpoint, dict) else set()
        identity_sources = {str(value) for value in _items(candidate.get("identity_source_ids"))}
        if not rationale_sources.issubset(claim_linked_sources | endpoint_sources | identity_sources):
            errors.append(f"{label}: rationale_source_ids must be candidate-claim, endpoint, or identity linked")

        if final:
            audit_rows = [audits_by_claim.get(value) for value in decisive]
            if any(row is None for row in audit_rows):
                errors.append(f"{label}: every decisive claim requires an audit record")
            else:
                verdicts = {str(row.get("verdict")) for row in audit_rows if row}
                expected_status = (
                    "conflicted" if verdicts.intersection({"unsupported", "contradicted", "unresolved"})
                    else "qualified" if "qualified" in verdicts
                    else "independently_verified"
                )
                if candidate.get("audit_status") != expected_status:
                    errors.append(f"{label}: audit_status disagrees with decisive-claim verdicts")
            if candidate.get("ranking_version") != RANKING_VERSION or not isinstance(candidate.get("rank"), int):
                errors.append(f"{label}: deterministic ranking was not applied")
            if candidate.get("council_status") not in {"reviewed", "not_selected"}:
                errors.append(f"{label}: council_status is not final")
        material_conflicts = {str(value) for value in _items(candidate.get("material_conflicts"))}
        if not material_conflicts.issubset(decisive):
            errors.append(f"{label}: material_conflicts must be decisive candidate claim IDs")
        for conflict_id in material_conflicts.intersection(claims):
            conflict = claims[conflict_id]
            related = {str(value) for value in _items(conflict.get("contrary_claim_ids"))}
            reciprocal = any(
                conflict_id in {str(value) for value in _items(claims.get(other, {}).get("contrary_claim_ids"))}
                for other in decisive - {conflict_id}
            )
            if (
                conflict.get("direction") not in {"opposes_benefit", "unclear"}
                and not related.intersection(decisive)
                and not reciprocal
            ):
                errors.append(f"{label}: material conflict {conflict_id} lacks adverse or contrary evidence")

    covered_observations = {
        str(value)
        for candidate in candidates.values()
        for value in _items(candidate.get("observation_ids"))
    }
    if final and covered_observations != set(observations):
        errors.append("candidate merge must retain every and only independent observations")

    _validate_audit_records(indexes, final=final, errors=errors)
    _validate_council_records(indexes, errors)


def _validate_audit_records(
    indexes: dict[str, dict[str, dict[str, Any]]],
    *,
    final: bool,
    errors: list[str],
) -> None:
    audits = indexes["audit_records.jsonl"]
    candidates = indexes["candidate_records.jsonl"]
    claims = indexes["claim_ledger.jsonl"]
    searches = indexes["search_log.jsonl"]
    sources = indexes["source_corpus.jsonl"]
    units = indexes["research_units.jsonl"]
    rationales: dict[str, str] = {}
    for audit_id, audit in audits.items():
        subject_id = str(audit.get("subject_id"))
        if audit.get("subject_type") != "claim" or subject_id not in claims:
            errors.append(f"audit {audit_id}: subject must resolve to a claim")
        if audit.get("verdict") not in AUDIT_VERDICTS:
            errors.append(f"audit {audit_id}: invalid verdict")
        checked = {str(value) for value in _items(audit.get("checked_source_ids"))}
        subject_sources = {str(value) for value in _items(claims.get(subject_id, {}).get("source_ids"))}
        if not checked.issubset(subject_sources):
            errors.append(f"audit {audit_id}: checked sources must be linked from the subject claim")
        for source_id in checked:
            source = sources.get(source_id)
            if not source or source.get("content_verified") is not True or source.get("screen_decision") != "include":
                errors.append(f"audit {audit_id}: checked source {source_id} is not verified")
            elif subject_id not in {str(value) for value in _items(source.get("supported_claim_ids"))}:
                errors.append(f"audit {audit_id}: checked source {source_id} is not linked to its subject claim")
        retrieved: set[str] = set()
        families: set[str] = set()
        for query_id in _items(audit.get("independent_search_ids")):
            query = searches.get(str(query_id))
            if not query or str(query.get("executed_by_agent_id")) != str(audit.get("auditor_agent_id")):
                errors.append(f"audit {audit_id}: independent search provenance is invalid")
            elif units.get(str(query.get("research_unit_id")), {}).get("unit_type") != "decisive_audit":
                errors.append(f"audit {audit_id}: independent search is not from the decisive-audit unit")
            else:
                retrieved.update(str(value) for value in _items(query.get("verified_source_ids")))
                families.add(str(query.get("query_family")))
                if subject_id not in {str(value) for value in _items(query.get("produced_claim_ids"))}:
                    errors.append(f"audit {audit_id}: search {query_id} is not bound to the subject claim")
        if families != set(AUDIT_QUERY_FAMILIES):
            errors.append(f"audit {audit_id}: each claim requires verification and counterevidence searches")
        if not checked.issubset(retrieved):
            errors.append(f"audit {audit_id}: checked sources were not independently retrieved")
        rationale = " ".join(str(audit.get("rationale", "")).casefold().split())
        if rationale in rationales and rationales[rationale] != audit_id:
            errors.append(f"audit {audit_id}: rationale duplicates {rationales[rationale]}; use claim-specific reasoning")
        elif rationale:
            rationales[rationale] = audit_id
        expected_status = (
            "independently_verified" if audit.get("verdict") == "supported"
            else "qualified" if audit.get("verdict") == "qualified"
            else "conflicted"
        )
        if subject_id in claims and claims[subject_id].get("audit_status") != expected_status:
            errors.append(f"audit {audit_id}: claim audit_status disagrees with the verdict")
    if final:
        decisive = {
            str(value) for candidate in candidates.values() for value in _items(candidate.get("decisive_claim_ids"))
        }
        audited = {
            str(row.get("subject_id")) for row in audits.values() if row.get("subject_type") == "claim"
        }
        if audited != decisive:
            errors.append("audit records must cover every and only decisive candidate claims")


def _validate_council_records(
    indexes: dict[str, dict[str, dict[str, Any]]], errors: list[str]
) -> None:
    candidates = indexes["candidate_records.jsonl"]
    claims = indexes["claim_ledger.jsonl"]
    councils = indexes["council_records.jsonl"]
    sources = indexes["source_corpus.jsonl"]
    for candidate_id, council in councils.items():
        candidate = candidates.get(candidate_id, {})
        if not candidate:
            errors.append(f"council {candidate_id}: unknown candidate")
        if council.get("disposition") not in COUNCIL_DISPOSITIONS:
            errors.append(f"council {candidate_id}: invalid disposition")
        if council.get("review_reason") not in {"leader", "material_conflict", "leader_and_conflict"}:
            errors.append(f"council {candidate_id}: invalid review_reason")
        if council.get("audit_status") != "reviewed":
            errors.append(f"council {candidate_id}: audit_status must be reviewed")
        reviewed = {str(value) for value in _items(council.get("reviewed_claim_ids"))}
        checked = {str(value) for value in _items(council.get("checked_source_ids"))}
        if not reviewed.issubset(claims):
            errors.append(f"council {candidate_id}: reviewed_claim_ids must resolve")
        if not checked.issubset(sources):
            errors.append(f"council {candidate_id}: checked_source_ids must resolve")
        if not reviewed.issubset({str(value) for value in _items(candidate.get("decisive_claim_ids"))}):
            errors.append(f"council {candidate_id}: reviewed claims are not decisive candidate claims")
        material = {str(value) for value in _items(candidate.get("material_conflicts"))}
        unresolved = {str(value) for value in _items(council.get("unresolved_conflicts"))}
        if not unresolved.issubset(material):
            errors.append(f"council {candidate_id}: unresolved_conflicts must be material candidate conflicts")
        if (council.get("disposition") == "conflict_unresolved") != bool(unresolved):
            errors.append(f"council {candidate_id}: unresolved conflicts and disposition must agree")
        endpoint = candidate.get("target_endpoint") if isinstance(candidate.get("target_endpoint"), dict) else {}
        decision_sources = {str(value) for value in _items(candidate.get("candidate_class_source_ids"))}
        decision_sources.update(str(value) for value in _items(endpoint.get("source_ids")))
        readiness = candidate.get("repurposing_readiness")
        if isinstance(readiness, dict):
            decision_sources.update(str(value) for value in _items(readiness.get("source_ids")))
        if not decision_sources.issubset(checked):
            errors.append(f"council {candidate_id}: checked sources must cover class, endpoint, and readiness")
        if council.get("candidate_class") != candidate.get("candidate_class"):
            errors.append(f"council {candidate_id}: candidate_class assessment does not match the candidate")
        if council.get("target_endpoint_type") != endpoint.get("endpoint_type"):
            errors.append(f"council {candidate_id}: target endpoint assessment does not match the candidate")
        if not str(council.get("candidate_class_assessment", "")).strip() or not str(
            council.get("endpoint_assessment", "")
        ).strip():
            errors.append(f"council {candidate_id}: class and endpoint assessments are required")
        expected = (
            "baseline_only" if candidate.get("candidate_class") == "supportive_standard_care"
            else "benchmark_only" if candidate.get("candidate_class") in {
                "target_disease_investigational", "approved_for_target_disease"
            }
            else ""
        )
        if expected and council.get("disposition") != expected:
            errors.append(f"council {candidate_id}: {candidate.get('candidate_class')} requires {expected}")
        if not expected and council.get("disposition") in {"baseline_only", "benchmark_only"}:
            errors.append(f"council {candidate_id}: disposition conflicts with candidate_class")


def _validate_final_runtime(
    root: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    attempts: list[dict[str, Any]],
    indexes: dict[str, dict[str, dict[str, Any]]],
    errors: list[str],
) -> None:
    if state.get("schema_version") != SCHEMA_VERSION or plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"runtime schema_version must be {SCHEMA_VERSION}")
    if state.get("max_active_jobs") != MAX_ACTIVE_JOBS or plan.get("max_active_jobs") != MAX_ACTIVE_JOBS:
        errors.append("runtime max_active_jobs must be 1")
    if state.get("active_job_id") or state.get("active_attempt_id"):
        errors.append("no job may remain active at finalization")
    if state.get("blocked_reason"):
        errors.append("blocked_reason must be empty at finalization")
    if state.get("current_phase") != "ready_for_finalization":
        errors.append("current_phase must be ready_for_finalization")
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        errors.append("execution plan must contain jobs")
        return
    job_by_id = index_rows(jobs, "job_id")
    assigned = [str(job.get("assigned_agent_id")) for job in jobs if job.get("assigned_agent_id")]
    if len(assigned) != len(set(assigned)):
        errors.append("independent jobs must not share assigned agents")
    attempts_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        attempts_by_job[str(attempt.get("job_id"))].append(attempt)
        if attempt.get("status") not in {"complete", "failed", "orphaned"}:
            errors.append(f"attempt {attempt.get('attempt_id')}: unresolved status")
        if not attempt.get("finished_at"):
            errors.append(f"attempt {attempt.get('attempt_id')}: missing finished_at")
    for job_id, job in job_by_id.items():
        if job.get("status") != "complete":
            errors.append(f"job {job_id}: final status must be complete")
        if any(job_by_id.get(str(dep), {}).get("status") != "complete" for dep in _items(job.get("depends_on"))):
            errors.append(f"job {job_id}: dependency is incomplete")
        manifest_path = _resolve_file(root, job.get("packet_manifest_path"), f"job {job_id} packet", errors)
        result_path = _resolve_file(root, job.get("result_path"), f"job {job_id} result", errors)
        if result_path and str(job.get("result_hash")) != file_hash(result_path):
            errors.append(f"job {job_id}: result hash mismatch")
        if manifest_path:
            manifest = read_json(manifest_path, {})
            body = {key: value for key, value in manifest.items() if key != "packet_hash"}
            if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("job_id") != job_id:
                errors.append(f"job {job_id}: packet identity mismatch")
            if manifest.get("packet_hash") != content_hash(body) or manifest.get("packet_hash") != job.get("packet_hash"):
                errors.append(f"job {job_id}: packet hash mismatch")
            for chunk in _items(manifest.get("required_chunks")):
                chunk_path = _resolve_file(root, chunk.get("path"), f"job {job_id} packet chunk", errors)
                if chunk_path and str(chunk.get("sha256")) != file_hash(chunk_path):
                    errors.append(f"job {job_id}: packet chunk hash mismatch")
        completed = [
            row for row in attempts_by_job.get(job_id, [])
            if row.get("status") == "complete" and row.get("packet_hash") == job.get("packet_hash")
        ]
        if len(completed) != 1 or str(completed[0].get("agent_id")) != str(job.get("assigned_agent_id")):
            errors.append(f"job {job_id}: completed attempt does not match the assigned agent and packet")

    units = indexes["research_units.jsonl"]
    present_broad = {str(row.get("perspective")) for row in units.values() if row.get("unit_type") == "broad_evidence"}
    present_global = {str(row.get("perspective")) for row in units.values() if row.get("unit_type") == "compound_perspective"}
    if present_broad != set(BROAD_DOMAINS):
        errors.append("research units do not contain the exact broad-evidence perspectives")
    if present_global != set(GLOBAL_PERSPECTIVES):
        errors.append("research units do not contain the exact independent compound perspectives")
    if sum(row.get("unit_type") == "decisive_audit" for row in units.values()) != 1:
        errors.append("exactly one decisive-audit unit is required")

    candidates = list(indexes["candidate_records.jsonl"].values())
    council_job = next((job for job in jobs if job.get("kind") == "council"), None)
    try:
        from ranking import council_selection, rank_candidates

        computed = rank_candidates(root, persist=False)
        stored = {str(row.get("candidate_id")): row for row in candidates}
        for row in computed:
            original = stored[str(row["candidate_id"])]
            for field in (
                "raw_score", "total_score", "applied_cap", "rank_section", "rank", "endpoint_rank",
                "ranking_version",
            ):
                if original.get(field) != row.get(field):
                    errors.append(f"candidate {row['candidate_id']}: stored {field} differs from deterministic ranking")
        if council_job:
            snapshot = council_job.get("selection_snapshot")
            if not isinstance(snapshot, list) or council_job.get("selection_snapshot_hash") != content_hash(snapshot):
                errors.append("council selection snapshot is missing or has an invalid hash")
                selected = set()
            else:
                selected = set(council_selection(snapshot))
        else:
            selected = set(council_selection(computed))
    except Exception as exc:
        errors.append(str(exc))
        selected = set()
    recorded = set(indexes["council_records.jsonl"])
    if selected:
        if not council_job or set(str(value) for value in _items(council_job.get("candidate_ids"))) != selected:
            errors.append("council job does not match deterministic leader/conflict selection")
        if recorded != selected:
            errors.append("council records do not cover exactly the selected candidates")
        if council_job:
            reviewer = str(council_job.get("assigned_agent_id", ""))
            if any(str(row.get("reviewer_agent_id")) != reviewer for row in indexes["council_records.jsonl"].values()):
                errors.append("council reviewer provenance does not match its job")
    elif council_job or recorded:
        errors.append("council work exists without a selected candidate")


def _load_ledgers(root: Path, errors: list[str]) -> dict[str, list[dict[str, Any]]]:
    ledgers: dict[str, list[dict[str, Any]]] = {}
    for filename in SCHEMAS:
        try:
            ledgers[filename] = read_jsonl(root / filename)
        except Exception as exc:
            errors.append(f"{filename}: invalid JSONL: {exc}")
            ledgers[filename] = []
    return ledgers


def validate_ledgers(
    root: Path,
    ledgers: dict[str, list[dict[str, Any]]],
    plan: dict[str, Any],
    *,
    final: bool,
) -> list[str]:
    errors: list[str] = []
    indexes = _schema_rows(ledgers, errors)
    jobs = index_rows(plan.get("jobs", []), "job_id") if isinstance(plan.get("jobs"), list) else {}
    _validate_sources_and_searches(root, indexes, jobs, errors)
    _validate_evidence(indexes, errors)
    _validate_compounds(indexes, final=final, errors=errors)
    return errors


def validate_staged_result(
    run_folder: str | Path,
    job: dict[str, Any],
    result: dict[str, Any],
    active_agent_id: str,
) -> list[str]:
    root = Path(run_folder).expanduser().resolve()
    errors: list[str] = []
    updates = result.get("ledger_updates")
    if not isinstance(updates, dict):
        return ["ledger_updates must be an object"]
    permitted = allowed_ledgers(str(job.get("kind", "")))
    unknown = set(updates) - permitted
    if unknown:
        errors.append(f"job {job.get('job_id')}: unapproved ledgers {sorted(unknown)}")
    ledgers = _load_ledgers(root, errors)
    original_indexes = {
        filename: index_rows(rows, SCHEMAS[filename]["key"])
        for filename, rows in ledgers.items()
    }
    if job.get("kind") == "research":
        for filename in (
            "search_log.jsonl", "claim_ledger.jsonl", "evidence_graph.jsonl",
            "candidate_observations.jsonl",
        ):
            key = SCHEMAS[filename]["key"]
            for row in updates.get(filename, []):
                if isinstance(row, dict) and str(row.get(key, "")) in original_indexes[filename]:
                    errors.append(f"{filename} {row.get(key)}: research jobs may not overwrite prior records")
    for source in updates.get("source_corpus.jsonl", []):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id", ""))
        original = original_indexes["source_corpus.jsonl"].get(source_id)
        if not original:
            continue
        changed = {
            field for field in set(original) | set(source)
            if field not in SOURCE_AGGREGATE_FIELDS and original.get(field) != source.get(field)
        }
        if changed:
            errors.append(f"source {source_id}: rediscovery may not change fields {sorted(changed)}")
    if job.get("kind") == "decisive_audit":
        for filename, mutable_fields in AUDIT_MUTABLE_FIELDS.items():
            key = SCHEMAS[filename]["key"]
            for row in updates.get(filename, []):
                if not isinstance(row, dict):
                    continue
                identity = str(row.get(key, ""))
                original = original_indexes[filename].get(identity)
                if not original:
                    continue
                changed = {
                    field for field in set(original) | set(row)
                    if field not in mutable_fields and original.get(field) != row.get(field)
                }
                if changed:
                    errors.append(f"{filename} {identity}: audit may not change fields {sorted(changed)}")
                monotonic_fields = (
                    {"source_ids", "contrary_claim_ids", "supersedes_claim_ids"}
                    if filename == "claim_ledger.jsonl"
                    else {"claim_ids", "contrary_edge_ids", "supersedes_edge_ids"}
                    if filename == "evidence_graph.jsonl"
                    else set()
                )
                for field in monotonic_fields:
                    before = {str(value) for value in _items(original.get(field))}
                    after = {str(value) for value in _items(row.get(field))}
                    if not before.issubset(after):
                        errors.append(f"{filename} {identity}: audit may not discard {field}")
    if job.get("kind") == "council":
        selected = {str(value) for value in _items(job.get("candidate_ids"))}
        candidate_updates = {
            str(row.get("candidate_id")): row
            for row in updates.get("candidate_records.jsonl", [])
            if isinstance(row, dict)
        }
        if set(candidate_updates) != selected:
            errors.append("council must return every and only selected candidate record")
        for candidate_id, row in candidate_updates.items():
            original = original_indexes["candidate_records.jsonl"].get(candidate_id, {})
            changed = {
                field for field in set(original) | set(row)
                if field not in COUNCIL_MUTABLE_CANDIDATE_FIELDS and original.get(field) != row.get(field)
            }
            if changed:
                errors.append(f"candidate {candidate_id}: council may not change fields {sorted(changed)}")
            old_endpoint = original.get("target_endpoint", {})
            new_endpoint = row.get("target_endpoint", {})
            if (
                isinstance(old_endpoint, dict)
                and isinstance(new_endpoint, dict)
                and old_endpoint.get("label") != new_endpoint.get("label")
            ):
                errors.append(f"candidate {candidate_id}: council may not change the endpoint label")
    if job.get("kind") == "merge":
        mutable_observation_fields = {
            "active_moiety_key", "active_moiety_source_ids", "active_moiety_rationale",
        }
        for row in updates.get("candidate_observations.jsonl", []):
            if not isinstance(row, dict):
                continue
            observation_id = str(row.get("observation_id", ""))
            original = original_indexes["candidate_observations.jsonl"].get(observation_id)
            if not original:
                errors.append(f"observation {observation_id}: merge may normalize only existing observations")
                continue
            changed = {
                field for field in set(original) | set(row)
                if field not in mutable_observation_fields and original.get(field) != row.get(field)
            }
            if changed:
                errors.append(f"observation {observation_id}: merge may not change fields {sorted(changed)}")
    for filename, rows in updates.items():
        if filename not in SCHEMAS:
            continue
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            errors.append(f"{filename}: staged updates must be a list of objects")
            continue
        key = SCHEMAS[filename]["key"]
        merged = index_rows(ledgers[filename], key)
        for row in rows:
            identity = str(row.get(key, ""))
            if identity:
                if filename == "source_corpus.jsonl" and identity in merged:
                    combined = dict(row)
                    for field in SOURCE_AGGREGATE_FIELDS:
                        combined[field] = sorted({
                            *(str(value) for value in _items(merged[identity].get(field))),
                            *(str(value) for value in _items(row.get(field))),
                        })
                    merged[identity] = combined
                else:
                    merged[identity] = row
        ledgers[filename] = list(merged.values())
    errors.extend(validate_ledgers(root, ledgers, read_json(root / "execution_plan.json", {}), final=False))

    for query in updates.get("search_log.jsonl", []):
        if str(query.get("executed_by_agent_id", "")) != active_agent_id:
            errors.append(f"staged query {query.get('query_id')}: executor must be the active agent")
        if str(query.get("origin_job_id", "")) != str(job.get("job_id", "")):
            errors.append(f"staged query {query.get('query_id')}: origin_job_id must match the active job")
    if job.get("kind") == "research":
        if any(row.get("research_unit_id") != job.get("unit_id") for row in updates.get("search_log.jsonl", [])):
            errors.append("research job emitted a search for another unit")
        if any(row.get("research_unit_id") != job.get("unit_id") for row in updates.get("candidate_observations.jsonl", [])):
            errors.append("research job emitted an observation for another unit")
        unit = next(
            (row for row in ledgers["research_units.jsonl"] if str(row.get("unit_id")) == str(job.get("unit_id"))),
            {},
        )
        if unit.get("unit_type") == "compound_perspective":
            exclusions = result.get("candidate_exclusions")
            if not isinstance(exclusions, list):
                errors.append("compound-perspective result requires candidate_exclusions")
            else:
                source_ids = {str(row.get("source_id")) for row in ledgers["source_corpus.jsonl"]}
                for position, exclusion in enumerate(exclusions, 1):
                    label = f"candidate exclusion {position}"
                    if not isinstance(exclusion, dict) or set(exclusion) != set(NESTED_SCHEMAS["candidate_exclusion"]):
                        errors.append(f"{label}: fields do not match the schema")
                        continue
                    if not str(exclusion.get("name", "")).strip() or not str(exclusion.get("reason", "")).strip():
                        errors.append(f"{label}: name and reason are required")
                    cited = [str(value) for value in _items(exclusion.get("source_ids"))]
                    if not cited or not _unique(cited) or not set(cited).issubset(source_ids):
                        errors.append(f"{label}: source_ids must be nonempty, unique, and resolve")
    if job.get("kind") in {"research", "decisive_audit"}:
        unit = next(
            (row for row in ledgers["research_units.jsonl"] if str(row.get("unit_id")) == str(job.get("unit_id"))),
            {},
        )
        staged_families = {
            str(row.get("query_family"))
            for row in updates.get("search_log.jsonl", [])
            if str(row.get("research_unit_id")) == str(job.get("unit_id"))
        }
        if staged_families != set(unit.get("planned_query_families", [])):
            errors.append("research result must complete every and only predeclared query family")
    if job.get("kind") == "merge":
        observations = {
            str(row.get("observation_id")) for row in ledgers["candidate_observations.jsonl"]
        }
        covered = {
            str(value)
            for candidate in ledgers["candidate_records.jsonl"]
            for value in _items(candidate.get("observation_ids"))
        }
        if covered != observations:
            errors.append("merge result must retain every and only independent candidate observation")
        try:
            from ranking import rank_rows

            synthetic_audits = [
                {"subject_type": "claim", "subject_id": claim_id, "verdict": "supported"}
                for claim_id in {
                    str(value)
                    for candidate in ledgers["candidate_records.jsonl"]
                    for value in _items(candidate.get("decisive_claim_ids"))
                }
            ]
            rank_rows(
                ledgers["candidate_records.jsonl"],
                ledgers["source_corpus.jsonl"],
                ledgers["claim_ledger.jsonl"],
                synthetic_audits,
                ledgers["evidence_graph.jsonl"],
            )
        except Exception as exc:
            errors.append(str(exc))
    if job.get("kind") == "decisive_audit":
        decisive = {
            str(value)
            for candidate in ledgers["candidate_records.jsonl"]
            for value in _items(candidate.get("decisive_claim_ids"))
        }
        audited = {
            str(row.get("subject_id"))
            for row in ledgers["audit_records.jsonl"]
            if row.get("subject_type") == "claim"
        }
        if audited != decisive:
            errors.append("decisive audit must cover every and only decisive candidate claims")
        all_candidates = {
            str(row.get("candidate_id")) for row in ledgers["candidate_records.jsonl"]
        }
        updated_candidates = {
            str(row.get("candidate_id")) for row in updates.get("candidate_records.jsonl", [])
        }
        if updated_candidates != all_candidates:
            errors.append("decisive audit must reassess every candidate record, including scores and caps")
        if any(str(row.get("auditor_agent_id")) != active_agent_id for row in updates.get("audit_records.jsonl", [])):
            errors.append("audit record provenance must match the active independent auditor")
        try:
            from ranking import rank_rows

            rank_rows(
                ledgers["candidate_records.jsonl"],
                ledgers["source_corpus.jsonl"],
                ledgers["claim_ledger.jsonl"],
                ledgers["audit_records.jsonl"],
                ledgers["evidence_graph.jsonl"],
            )
        except Exception as exc:
            errors.append(str(exc))
    if job.get("kind") == "council":
        selected = {str(value) for value in _items(job.get("candidate_ids"))}
        recorded = {str(row.get("candidate_id")) for row in updates.get("council_records.jsonl", [])}
        if recorded != selected:
            errors.append("council result must cover exactly the controller-selected candidates")
        if any(str(row.get("reviewer_agent_id")) != active_agent_id for row in updates.get("council_records.jsonl", [])):
            errors.append("council record provenance must match the active reviewer")
        try:
            from ranking import rank_rows

            rank_rows(
                ledgers["candidate_records.jsonl"],
                ledgers["source_corpus.jsonl"],
                ledgers["claim_ledger.jsonl"],
                ledgers["audit_records.jsonl"],
                ledgers["evidence_graph.jsonl"],
            )
        except Exception as exc:
            errors.append(str(exc))
    return list(dict.fromkeys(errors))


def validate_run(run_folder: str | Path) -> list[str]:
    root = Path(run_folder).expanduser().resolve()
    if not root.is_dir():
        return [f"Run folder does not exist: {root}"]
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    missing_dirs = [name for name in ("packets", "staging", "raw_sources") if not (root / name).is_dir()]
    if missing or missing_dirs:
        return [*(f"Missing required file: {name}" for name in missing), *(f"Missing required directory: {name}" for name in missing_dirs)]
    errors: list[str] = []
    try:
        case = read_json(root / "case.json", {})
        state = read_json(root / "program_state.json", {})
        plan = read_json(root / "execution_plan.json", {})
        attempts = read_jsonl(root / "job_attempts.jsonl")
        orchestration = read_jsonl(root / "orchestration.jsonl")
    except Exception as exc:
        return [f"Runtime artifact is invalid: {exc}"]
    if not required_case_present(case):
        errors.append("case.json requires a human gene, human disease, and/or human phenotype")
    if any(_blank(row.get("event_id")) or _blank(row.get("event")) for row in orchestration):
        errors.append("orchestration events require event_id and event")
    ledgers = _load_ledgers(root, errors)
    errors.extend(validate_ledgers(root, ledgers, plan, final=True))
    indexes = _schema_rows(ledgers, [])
    _validate_final_runtime(root, state, plan, attempts, indexes, errors)
    return list(dict.fromkeys(errors))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_program.py <run_folder>", file=sys.stderr)
        return 2
    errors = validate_run(argv[1])
    if errors:
        print(f"VALIDATION FAILED ({len(errors)} issue(s))")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
