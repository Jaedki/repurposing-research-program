#!/usr/bin/env python3
"""Authoritative schema-v5 contracts for human therapeutic repurposing runs."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 5
MAX_ACTIVE_JOBS = 1
HUMAN_OUTCOME_NODE = "CASE_HUMAN_THERAPEUTIC_OUTCOME"
RANKING_VERSION = "human-therapeutic-v2"
COUNCIL_TOP_N = 5
REPURPOSING_READINESS_MAX = 10

BROAD_DOMAINS = (
    "human_disease_biology",
    "molecular_function_network",
    "phenotype_pathophysiology",
    "pharmacology_landscape",
    "clinical_safety_exposure",
)

GLOBAL_PERSPECTIVES = (
    "direct_mechanism",
    "phenotype_reversal",
    "vulnerability_inverse",
    "compensatory_network",
    "human_genetics_clinical",
    "hidden_in_plain_sight",
    "natural_compounds",
)

BASE_QUERY_FAMILIES = {
    "primary_literature",
    "authoritative_databases",
    "counterevidence",
    "citation_chaining",
}
COMPOUND_QUERY_FAMILIES = {
    "exact_compound",
    "identity_exposure",
    "human_translation",
}
AUDIT_QUERY_FAMILIES = {"independent_verification", "counterevidence"}

CALIBRATIONS = {
    "established",
    "supported_with_qualifier",
    "plausible_inference",
    "speculative",
    "unresolved",
    "contradicted",
}
SCIENTIFIC_AUDIT_STATUSES = {
    "unreviewed",
    "independently_verified",
    "qualified",
    "conflicted",
}
AUDIT_VERDICTS = {"supported", "qualified", "unsupported", "contradicted", "unresolved"}

HUMAN_RELEVANCE_LEVELS = {
    "human_therapeutic_outcome",
    "human_interventional_biomarker",
    "human_observational_or_genetic",
    "human_patient_cell",
    "animal_model",
    "nonhuman_in_vitro",
    "mechanistic_inference",
}
HUMAN_EVIDENCE_LEVELS = {
    "human_therapeutic_outcome",
    "human_interventional_biomarker",
    "human_observational_or_genetic",
    "human_patient_cell",
}
CLAIM_DIRECTIONS = {
    "supports_benefit",
    "opposes_benefit",
    "qualifies_benefit",
    "neutral_context",
    "unclear",
}
CANDIDATE_CLASSES = {
    "repurposing_candidate",
    "target_disease_investigational",
    "approved_for_target_disease",
    "supportive_standard_care",
    "preclinical_hypothesis",
}
COMPOUND_ORIGINS = {
    "synthetic_or_semisynthetic",
    "natural_product",
    "endogenous_or_nutrient",
    "formulation_component",
}
TARGET_ENDPOINT_TYPES = {
    "disease_modifying_clinical",
    "prevention_clinical",
    "symptom_or_function",
    "complication_management",
    "surrogate_biomarker",
}
SUPPORTIVE_ENDPOINT_TYPES = {"symptom_or_function", "complication_management"}
COUNCIL_DISPOSITIONS = {
    "retain",
    "deprioritize",
    "conflict_unresolved",
    "baseline_only",
    "benchmark_only",
}
RANK_SECTION_BY_CLASS = {
    "repurposing_candidate": "primary_repurposing",
    "target_disease_investigational": "target_disease_benchmark",
    "approved_for_target_disease": "target_disease_benchmark",
    "supportive_standard_care": "baseline_care",
    "preclinical_hypothesis": "preclinical_hypothesis",
}
RANK_SECTION_ORDER = (
    "primary_repurposing",
    "target_disease_benchmark",
    "baseline_care",
    "preclinical_hypothesis",
)

RANKING_COMPONENTS = {
    "human_evidence": 25,
    "mechanistic_fit": 20,
    "clinical_translatability": 15,
    "safety_tolerability": 15,
    "exposure_feasibility": 10,
    "evidence_independence": 5,
    "endpoint_specificity": 10,
}
RANKING_CAPS = {
    "unresolved_direction": 25,
    "absent_human_evidence": 40,
    "serious_safety_mismatch": 30,
    "infeasible_exposure": 20,
}

NESTED_SCHEMAS = {
    "pagination_trace": ("page_index", "receipt_path", "input_token_hash", "output_token_hash"),
    "causal_path": (
        "path_id", "edge_ids", "claim_ids", "start_node", "end_node", "expected_direction",
        "target_endpoint",
    ),
    "score_component": ("score", "rationale", "source_ids"),
    "cap_assessment": ("applies", "rationale", "source_ids"),
    "repurposing_readiness": ("score", "rationale", "source_ids"),
    "target_endpoint": ("endpoint_type", "label", "claim_ids", "source_ids"),
    "candidate_exclusion": ("name", "reason", "source_ids"),
    "experimental_model_suitability": ("assessed", "score", "rationale", "source_ids"),
    "applied_cap": ("maximum", "reasons"),
}


def _schema(key: str, fields: Iterable[str], nonempty: Iterable[str]) -> dict[str, Any]:
    return {"key": key, "fields": tuple(fields), "nonempty": tuple(nonempty)}


SCHEMAS = {
    "source_corpus.jsonl": _schema(
        "source_id",
        (
            "source_id", "canonical_identifier", "identifier_type", "title", "year",
            "source_kind", "source_family", "discovered_by_units", "discovery_query_ids",
            "metadata_verified", "screen_decision", "exclusion_reason", "original_acquired",
            "original_pointer", "content_verified", "verification_method", "verification_scope",
            "supported_claim_ids", "compaction_receipt_path", "compaction_record_hash",
        ),
        (
            "source_id", "canonical_identifier", "identifier_type", "title", "source_kind",
            "source_family", "screen_decision", "original_pointer", "verification_method",
            "verification_scope", "compaction_receipt_path", "compaction_record_hash",
        ),
    ),
    "search_log.jsonl": _schema(
        "query_id",
        (
            "query_id", "research_unit_id", "query_family", "resource", "query",
            "result_count", "screened_count", "pagination_complete", "compact_payload_paths",
            "pagination_trace", "acquired_source_ids", "verified_source_ids", "retained_source_ids",
            "executed_by_agent_id", "origin_job_id", "produced_claim_ids",
            "produced_observation_ids", "outcome", "closure_note",
        ),
        (
            "query_id", "research_unit_id", "query_family", "resource", "query",
            "compact_payload_paths", "pagination_trace", "executed_by_agent_id",
            "origin_job_id", "outcome", "closure_note",
        ),
    ),
    "claim_ledger.jsonl": _schema(
        "claim_id",
        (
            "claim_id", "topic", "statement", "claim_type", "source_ids", "calibration",
            "human_relevance", "direction", "scope", "contrary_claim_ids",
            "supersedes_claim_ids", "audit_status", "audit_note",
        ),
        (
            "claim_id", "topic", "statement", "claim_type", "source_ids", "calibration",
            "human_relevance", "direction", "scope", "audit_status",
        ),
    ),
    "evidence_graph.jsonl": _schema(
        "edge_id",
        (
            "edge_id", "from_node", "to_node", "relation", "effect", "directionality",
            "claim_ids", "contrary_edge_ids", "supersedes_edge_ids", "audit_status", "uncertainty",
        ),
        (
            "edge_id", "from_node", "to_node", "relation", "effect", "directionality",
            "claim_ids", "audit_status", "uncertainty",
        ),
    ),
    "research_units.jsonl": _schema(
        "unit_id",
        (
            "unit_id", "unit_type", "perspective", "question", "worker_agent_id", "status",
            "planned_query_families", "completed_query_families", "search_ids",
            "observation_ids", "candidate_exclusions", "closure_basis",
        ),
        ("unit_id", "unit_type", "perspective", "question", "status", "planned_query_families"),
    ),
    "candidate_observations.jsonl": _schema(
        "observation_id",
        (
            "observation_id", "research_unit_id", "canonical_name", "canonical_identifier",
            "registry_identifiers", "structure_identity_key", "active_moiety_key",
            "active_moiety_source_ids", "active_moiety_rationale", "chemical_node_id",
            "identity_source_ids", "mode_of_action", "claim_ids", "edge_ids", "rationale",
            "rationale_source_ids", "uncertainty",
        ),
        (
            "observation_id", "research_unit_id", "canonical_name", "canonical_identifier",
            "registry_identifiers", "structure_identity_key", "active_moiety_key",
            "active_moiety_source_ids", "active_moiety_rationale", "chemical_node_id",
            "identity_source_ids", "mode_of_action", "claim_ids", "edge_ids", "rationale",
            "rationale_source_ids", "uncertainty",
        ),
    ),
    "candidate_records.jsonl": _schema(
        "candidate_id",
        (
            "candidate_id", "canonical_name", "canonical_identifier", "registry_identifiers",
            "structure_identity_key", "active_moiety_key", "active_moiety_source_ids",
            "active_moiety_rationale", "formulation_structure_keys",
            "chemical_node_id", "identity_source_ids", "identity_verified",
            "observation_ids", "source_research_unit_ids", "causal_paths", "mode_of_action",
            "human_outcome", "candidate_class", "candidate_class_source_ids", "compound_origin",
            "target_endpoint", "repurposing_readiness", "rationale", "rationale_source_ids", "uncertainty",
            "decisive_claim_ids", "audit_status", "score_components", "cap_assessments",
            "experimental_model_suitability", "material_conflicts", "raw_score", "total_score",
            "applied_cap", "rank_section", "rank", "endpoint_rank", "ranking_version", "council_status",
        ),
        (
            "candidate_id", "canonical_name", "canonical_identifier", "registry_identifiers",
            "structure_identity_key", "active_moiety_key", "active_moiety_source_ids",
            "active_moiety_rationale", "formulation_structure_keys",
            "chemical_node_id", "identity_source_ids", "observation_ids",
            "source_research_unit_ids", "causal_paths", "mode_of_action", "human_outcome",
            "candidate_class", "candidate_class_source_ids", "compound_origin", "target_endpoint",
            "repurposing_readiness", "rationale", "rationale_source_ids", "uncertainty", "decisive_claim_ids",
            "audit_status", "score_components", "cap_assessments",
            "experimental_model_suitability", "ranking_version", "council_status",
        ),
    ),
    "audit_records.jsonl": _schema(
        "audit_id",
        (
            "audit_id", "subject_type", "subject_id", "auditor_agent_id", "checked_source_ids",
            "independent_search_ids", "verdict", "rationale", "completed_at",
        ),
        (
            "audit_id", "subject_type", "subject_id", "auditor_agent_id", "checked_source_ids",
            "independent_search_ids", "verdict", "rationale", "completed_at",
        ),
    ),
    "council_records.jsonl": _schema(
        "candidate_id",
        (
            "candidate_id", "review_reason", "reviewer_agent_id", "reviewed_claim_ids",
            "checked_source_ids", "candidate_class", "target_endpoint_type",
            "candidate_class_assessment", "endpoint_assessment", "disposition", "rationale",
            "unresolved_conflicts", "audit_status",
        ),
        (
            "candidate_id", "review_reason", "reviewer_agent_id", "reviewed_claim_ids",
            "checked_source_ids", "candidate_class", "target_endpoint_type",
            "candidate_class_assessment", "endpoint_assessment", "disposition", "rationale", "audit_status",
        ),
    ),
}

LEDGER_KEYS = {name: spec["key"] for name, spec in SCHEMAS.items()}
SOURCE_ALLOWED_FIELDS = set(SCHEMAS["source_corpus.jsonl"]["fields"])
SOURCE_AGGREGATE_FIELDS = {"discovered_by_units", "discovery_query_ids", "supported_claim_ids"}
RESULT_REQUIRED_FIELDS = (
    "job_id", "packet_hash", "all_chunks_processed", "outcome", "ledger_updates"
)


def required_case_present(case: dict[str, Any]) -> bool:
    return any(str(case.get(field, "")).strip() for field in ("human_gene", "human_disease", "human_phenotype"))


def required_query_families(unit_type: str) -> set[str]:
    if unit_type == "decisive_audit":
        return set(AUDIT_QUERY_FAMILIES)
    families = set(BASE_QUERY_FAMILIES)
    if unit_type == "compound_perspective":
        families.update(COMPOUND_QUERY_FAMILIES)
    return families


def allowed_ledgers(job_kind: str) -> set[str]:
    mapping = {
        "research": {
            "source_corpus.jsonl", "search_log.jsonl", "claim_ledger.jsonl",
            "evidence_graph.jsonl", "candidate_observations.jsonl",
        },
        "merge": {"candidate_observations.jsonl", "candidate_records.jsonl"},
        "decisive_audit": {
            "source_corpus.jsonl", "search_log.jsonl", "claim_ledger.jsonl",
            "evidence_graph.jsonl", "candidate_records.jsonl", "audit_records.jsonl",
        },
        "council": {"candidate_records.jsonl", "council_records.jsonl"},
    }
    return mapping.get(job_kind, set())


def role_contract(job: dict[str, Any], unit: dict[str, Any] | None) -> dict[str, Any]:
    unit_type = str((unit or {}).get("unit_type", ""))
    return {
        "schema_version": SCHEMA_VERSION,
        "job_kind": job.get("kind"),
        "role": job.get("role"),
        "required_query_families": sorted(required_query_families(unit_type)) if unit_type else [],
        "allowed_ledgers": sorted(allowed_ledgers(str(job.get("kind", "")))),
        "schemas": SCHEMAS,
        "nested_schemas": NESTED_SCHEMAS,
        "result_required_fields": RESULT_REQUIRED_FIELDS,
        "conditional_result_fields": {
            "job_with_research_unit": ["closure_basis"],
            "compound_perspective": ["candidate_exclusions"],
        },
        "human_endpoint": HUMAN_OUTCOME_NODE,
        "ranking_components": RANKING_COMPONENTS,
        "ranking_caps": RANKING_CAPS,
        "controlled_values": {
            "human_relevance": sorted(HUMAN_RELEVANCE_LEVELS),
            "claim_direction": sorted(CLAIM_DIRECTIONS),
            "candidate_class": sorted(CANDIDATE_CLASSES),
            "compound_origin": sorted(COMPOUND_ORIGINS),
            "target_endpoint_type": sorted(TARGET_ENDPOINT_TYPES),
            "council_disposition": sorted(COUNCIL_DISPOSITIONS),
            "rank_section": list(RANK_SECTION_ORDER),
        },
        "source_compactor": str(Path(__file__).resolve().parent / "compact_source_payload.py"),
        "search_record_builder": str(Path(__file__).resolve().parent / "build_search_record.py"),
        "result_validator": str(Path(__file__).resolve().parent / "orchestrate_program.py"),
        "rules": (
            "Resolve relative paths against run_root. Keep raw source bodies under raw_sources. "
            "Every claim must cite verified source records and use the controlled semantic values exactly. "
            "Reuse canonical source IDs already present in context; if validation reports a canonical collision, "
            "repair it by reusing that source ID and aggregating discovery provenance. Article sections are claims, "
            "not new sources. "
            "Preserve contrary and superseded relations. Classify standard care and target-disease development as "
            "baselines or benchmarks, not primary repurposing leads. Score one source-backed target endpoint. "
            "A decisive audit must independently retrieve evidence outside the packet and reassess caps. "
            "Material conflicts must be candidate-specific and decision-changing, not generic translation caveats. "
            "Do not infer completion from source count or elapsed time. Validate the staged result before handoff."
        ),
    }
