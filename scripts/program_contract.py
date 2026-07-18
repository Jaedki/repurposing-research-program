#!/usr/bin/env python3
"""Authoritative schema-v6 contracts for human therapeutic repurposing runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 6
MAX_ACTIVE_JOBS = 1
HUMAN_OUTCOME_NODE = "CASE_HUMAN_THERAPEUTIC_OUTCOME"
RANKING_VERSION = "human-therapeutic-v2"
COUNCIL_TOP_N = 5
REPURPOSING_READINESS_MAX = 100
REPURPOSING_READINESS_STEP = 10

SEARCH_COVERAGE_STATUSES = {
    "FOUND",
    "NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH",
    "NOT_YET_SEARCHED",
}
TERMINAL_SEARCH_COVERAGE_STATUSES = SEARCH_COVERAGE_STATUSES - {"NOT_YET_SEARCHED"}

BRANCH_BUDGET_PER_UNIT = 12
BRANCH_MATERIALITY_THRESHOLD = 50
FRONTIER_DECISIONS = {
    "expanded",
    "closed_duplicate",
    "closed_irrelevant",
    "closed_immaterial",
    "closed_budget_exhausted",
}

RETRYABLE_FAILURE_KINDS = {
    "tpm_exhaustion",
    "rpm_exhaustion",
    "api_rate_limit",
    "network_interruption",
    "worker_interruption",
    "process_termination",
    "spawn_failure",
    "transient",
    "rate_limit",  # schema-v5 CLI compatibility
}
FAILURE_KINDS = RETRYABLE_FAILURE_KINDS | {"unrecoverable"}
RETRY_BASE_SECONDS = 30
RETRY_DELAY_CAP_SECONDS = 900
RETRY_LIMIT = 6
STALE_RUN_SECONDS = 3600

JOB_STATUSES = {"planned", "ready", "running", "retry_wait", "blocked", "complete"}
ATTEMPT_STATUSES = {"running", "complete", "failed", "orphaned"}
COMPLETION_STATES = {"not_validated", "validated", "committing", "committed"}

BROAD_DOMAINS = (
    "human_disease_biology",
    "molecular_function_network",
    "phenotype_pathophysiology",
    "pharmacology_landscape",
    "clinical_safety_exposure",
)

PERSPECTIVE_CONTRACTS = {
    "direct_mechanism": {
        "perspective_id": "direct_mechanism",
        "discovery_objective": (
            "Find exact compounds that directly correct, modulate, or counter a source-supported "
            "disease-driving target or process."
        ),
        "required_causal_route": (
            "The compound acts directly on the disease-driving target or process in the direction "
            "expected to improve the declared human outcome."
        ),
        "required_coverage_areas": {
            "lens_direct_disease_driver": "Establish the source-supported disease-driving target or process.",
            "lens_direct_directional_modulation": (
                "Search exact compounds whose direct action corrects, modulates, or counters that driver."
            ),
            "lens_direct_human_outcome_bridge": (
                "Search the explicit bridge from directional target or process correction to a human outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "phenotype reversal without a direct target or process action",
            "natural origin, availability, novelty, or generic pathway relevance alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [direct_target_or_process_correction]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("direct", "directly"),
                ("target", "process", "driver"),
                ("correct", "modulat", "counter", "inhibit", "activat"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike phenotype reversal or compensation, this lane requires direct directional action on "
            "the disease driver itself."
        ),
    },
    "phenotype_reversal": {
        "perspective_id": "phenotype_reversal",
        "discovery_objective": (
            "Find exact compounds that reverse or normalise a disease-relevant phenotype, biomarker pattern, "
            "or molecular signature with an explicit human-outcome bridge."
        ),
        "required_causal_route": (
            "The compound reverses or normalises a defined disease phenotype, biomarker pattern, or molecular "
            "signature, and that reversal is connected to the declared human outcome."
        ),
        "required_coverage_areas": {
            "lens_phenotype_disease_pattern": (
                "Define the disease-relevant phenotype, biomarker pattern, or molecular signature."
            ),
            "lens_phenotype_reversal_evidence": (
                "Search exact-compound evidence for reversal or normalisation of that defined pattern."
            ),
            "lens_phenotype_human_outcome_bridge": (
                "Search the explicit bridge from pattern reversal to a human therapeutic outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "direct target modulation without phenotype, biomarker, or signature reversal",
            "generic mechanism, natural origin, novelty, or assay activity alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [phenotype_or_signature_reversal]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("phenotype", "biomarker", "signature", "molecular pattern"),
                ("revers", "normalis", "normaliz", "restor"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike direct mechanism, this lane requires demonstrated reversal of a disease-state readout; "
            "unlike vulnerability inverse, it does not depend on a disease-created dependency."
        ),
    },
    "vulnerability_inverse": {
        "perspective_id": "vulnerability_inverse",
        "discovery_objective": (
            "Find exact compounds that exploit, oppose, protect against, or correct a disease-created "
            "dependency, reciprocal state, stress response, or induced vulnerability."
        ),
        "required_causal_route": (
            "The disease creates a specific dependency, reciprocal state, stress response, or vulnerability, "
            "and the compound therapeutically exploits, opposes, protects against, or corrects it."
        ),
        "required_coverage_areas": {
            "lens_vulnerability_disease_created_state": (
                "Establish the disease-created dependency, reciprocal state, stress response, or vulnerability."
            ),
            "lens_vulnerability_inverse_intervention": (
                "Search exact compounds that exploit, oppose, protect against, or correct that created state."
            ),
            "lens_vulnerability_human_outcome_bridge": (
                "Search the explicit bridge from the vulnerability intervention to a human outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "generic phenotype reversal without a disease-created dependency or reciprocal state",
            "direct disease-driver modulation, natural origin, novelty, or stress-assay activity alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [disease_created_vulnerability_inverse]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("dependency", "vulnerability", "reciprocal state", "stress response"),
                ("exploit", "oppose", "protect", "correct", "buffer"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike phenotype reversal, this lane must name a disease-created dependency or reciprocal state "
            "and the inverse intervention that acts on it."
        ),
    },
    "compensatory_network": {
        "perspective_id": "compensatory_network",
        "discovery_objective": (
            "Find exact compounds that restore function through a parallel, downstream, bypass, or compensatory "
            "network route when direct correction is unavailable, unsafe, or incomplete."
        ),
        "required_causal_route": (
            "A source-supported parallel, downstream, bypass, or compensatory route restores function despite "
            "an unavailable, unsafe, or incomplete direct correction."
        ),
        "required_coverage_areas": {
            "lens_compensation_direct_route_limit": (
                "Establish why direct correction is unavailable, unsafe, or incomplete for the relevant outcome."
            ),
            "lens_compensation_bypass_route": (
                "Search exact compounds acting through a parallel, downstream, bypass, or compensatory network."
            ),
            "lens_compensation_human_outcome_bridge": (
                "Search the explicit bridge from compensatory functional restoration to a human outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "direct correction presented without a distinct compensatory or bypass route",
            "generic network proximity, phenotype reversal, natural origin, or novelty alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [parallel_or_compensatory_restoration]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("parallel", "downstream", "bypass", "compensat"),
                ("restore", "rescue", "preserve"),
                ("unavailable", "unsafe", "incomplete", "insufficient", "not feasible"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike direct mechanism, this lane acts through a distinct restoration route and must state the "
            "limitation of direct correction."
        ),
    },
    "human_genetics_clinical": {
        "perspective_id": "human_genetics_clinical",
        "discovery_objective": (
            "Find exact compounds supported by human genetic causality, natural experiments, human target "
            "validation, or human intervention evidence relevant to the proposed route."
        ),
        "required_causal_route": (
            "Human genetic, natural-experiment, target-validation, or intervention evidence anchors the "
            "direction of the compound route and its relevance to the declared human outcome."
        ),
        "required_coverage_areas": {
            "lens_human_causal_anchor": (
                "Search human genetic causality, natural experiments, or human target-validation evidence."
            ),
            "lens_human_intervention_route": (
                "Search exact compounds whose directional route matches the human causal or intervention anchor."
            ),
            "lens_human_genetics_clinical_outcome_bridge": (
                "Search the explicit bridge from the human causal anchor or intervention to the target outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "model-only genetics or mechanistic plausibility without a human causal anchor",
            "generic clinical availability, natural origin, phenotype reversal, or novelty alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [human_causal_or_intervention_anchor]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("genetic", "variant", "allele", "natural experiment", "target validation", "human intervention"),
                ("causal", "validat", "intervention"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike the other mechanistic lanes, this lane requires a human causal, natural-experiment, "
            "target-validation, or intervention anchor for the proposed direction."
        ),
    },
    "hidden_in_plain_sight": {
        "perspective_id": "hidden_in_plain_sight",
        "discovery_objective": (
            "Find exact compounds with adjacent-indication, real-world, comorbidity, or clinically observed "
            "therapeutic signals that bridge to the declared human outcome."
        ),
        "required_causal_route": (
            "A source-supported adjacent-indication, real-world, comorbidity, or other clinical observation "
            "provides a directional therapeutic signal and a defensible bridge to the target outcome."
        ),
        "required_coverage_areas": {
            "lens_hidden_adjacent_indication_signal": (
                "Search adjacent indications and clinically observed signals relevant to the target outcome."
            ),
            "lens_hidden_real_world_comorbidity_signal": (
                "Search real-world and comorbidity-associated therapeutic observations."
            ),
            "lens_hidden_human_outcome_bridge": (
                "Search the explicit bridge from the observed clinical signal to the target human outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "being an obvious, available, or familiar compound without a human-outcome signal",
            "mechanistic plausibility, natural origin, novelty, or anecdote alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [adjacent_or_observed_clinical_signal]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("adjacent indication", "real-world", "real world", "comorbidity", "clinically observed", "clinical observation"),
                ("outcome", "benefit", "improv", "response", "reduc"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike human genetics/clinical, this lane is anchored in adjacent-use or observed clinical signals "
            "rather than requiring genetic causality or formal target validation."
        ),
    },
    "natural_compounds": {
        "perspective_id": "natural_compounds",
        "discovery_objective": (
            "Find identity-resolved exact natural compounds whose source-supported causal route and explicit "
            "human-outcome bridge justify consideration independently of natural origin."
        ),
        "required_causal_route": (
            "The exact compound has verified natural origin plus a distinct source-supported causal route and "
            "human-outcome bridge; origin itself contributes no therapeutic rationale."
        ),
        "required_coverage_areas": {
            "lens_natural_exact_identity_origin": (
                "Verify exact chemical identity and natural, endogenous, or nutrient origin."
            ),
            "lens_natural_independent_causal_route": (
                "Search a causal therapeutic route independent of the compound's origin."
            ),
            "lens_natural_human_outcome_bridge": (
                "Search the explicit bridge from that independent route to a human outcome."
            ),
        },
        "prohibited_primary_rationales": (
            "natural, traditional, dietary, familiar, or widely available origin alone",
            "extract, mixture, compound class, generic antioxidant activity, or novelty alone",
        ),
        "required_lens_specific_rationale": {
            "field": "rationale",
            "route_marker": "Lens route [exact_natural_compound_with_independent_route]:",
            "human_outcome_marker": "Human-outcome bridge:",
            "route_term_groups": (
                ("natural product", "natural origin", "plant", "microbial", "marine", "endogenous", "nutrient"),
                ("causal", "mechanism", "phenotype", "genetic", "clinical"),
            ),
        },
        "distinguishing_boundary": (
            "Unlike every route-defined lane, this lane is additionally origin-constrained; unlike origin-only "
            "screening, it still requires an independent causal route and human-outcome bridge."
        ),
    },
}

GLOBAL_PERSPECTIVES = tuple(PERSPECTIVE_CONTRACTS)

BASE_QUERY_FAMILIES = {
    "primary_literature",
    "authoritative_databases",
    "counterevidence",
    "citation_chaining",
    "synonym_expansion",
    "adjacent_domain_search",
}
COMPOUND_QUERY_FAMILIES = {
    "exact_compound",
    "identity_exposure",
    "human_translation",
}
AUDIT_QUERY_FAMILIES = {
    "independent_verification",
    "counterevidence",
    "citation_chaining",
    "synonym_expansion",
    "adjacent_domain_search",
}
SATURATION_QUERY_FAMILIES = {
    "synonym_expansion": "synonym_expansion",
    "citation_expansion": "citation_chaining",
    "contradiction_search": "counterevidence",
    "adjacent_domain_search": "adjacent_domain_search",
}

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
    "frontier_branch": (
        "branch_id", "branch_order", "causal_route", "distinct_causal_route",
        "human_or_candidate_relevance", "already_covered", "materiality_score",
        "decision", "query_ids", "source_ids", "rationale",
    ),
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
            "produced_observation_ids", "idempotency_key", "outcome", "closure_note",
        ),
        (
            "query_id", "research_unit_id", "query_family", "resource", "query",
            "compact_payload_paths", "pagination_trace", "executed_by_agent_id",
            "origin_job_id", "idempotency_key", "outcome", "closure_note",
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
            "coverage_statuses", "evidence_frontier", "frontier_exhausted",
            "branch_budget", "observation_ids", "candidate_exclusions", "closure_basis",
        ),
        (
            "unit_id", "unit_type", "perspective", "question", "status",
            "planned_query_families", "coverage_statuses", "branch_budget",
        ),
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

JOB_REQUIRED_FIELDS = (
    "job_id", "phase", "sequence", "kind", "role", "unit_id", "question", "depends_on",
    "candidate_ids", "status", "assigned_agent_id", "attempt_count", "packet_manifest_path",
    "packet_hash", "result_path", "result_hash", "retry_not_before", "retry_reason",
    "retry_detail", "retry_count", "retry_limit", "retry_delay_seconds", "completion_state",
    "validated_result_path", "validated_result_hash", "commit_started_at", "completed_at",
    "selection_snapshot", "selection_snapshot_hash",
)
ATTEMPT_REQUIRED_FIELDS = (
    "attempt_id", "job_id", "agent_id", "packet_hash", "packet_manifest_path",
    "expected_result_path", "status", "started_at", "last_progress_at", "finished_at",
    "failure_kind", "retry_reason",
)
PROGRAM_STATE_REQUIRED_FIELDS = (
    "schema_version", "max_active_jobs", "current_phase", "active_job_id",
    "active_attempt_id", "checkpoint_pending", "slice_started_at", "slice_jobs_completed",
    "slice_max_jobs", "slice_max_minutes", "blocked_reason", "interrupted_run_detected",
    "stale_run_detected", "created_at", "updated_at",
)


def required_case_present(case: dict[str, Any]) -> bool:
    return any(str(case.get(field, "")).strip() for field in ("human_gene", "human_disease", "human_phenotype"))


def search_idempotency_key(
    research_unit_id: str,
    query_family: str,
    resource: str,
    query: str,
) -> str:
    normalized = {
        "research_unit_id": research_unit_id.strip(),
        "query_family": query_family.strip(),
        "resource": " ".join(resource.casefold().split()),
        "query": " ".join(query.casefold().split()),
    }
    payload = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"SEARCH:{hashlib.sha256(payload).hexdigest()}"


def required_query_families(unit_type: str, perspective: str = "") -> set[str]:
    if unit_type == "decisive_audit":
        return set(AUDIT_QUERY_FAMILIES)
    families = set(BASE_QUERY_FAMILIES)
    if unit_type == "compound_perspective":
        families.update(COMPOUND_QUERY_FAMILIES)
        if perspective:
            contract = PERSPECTIVE_CONTRACTS.get(perspective)
            if contract is None:
                raise ValueError(f"Unknown compound perspective: {perspective}")
            families.update(contract["required_coverage_areas"])
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
    perspective = str((unit or {}).get("perspective", ""))
    perspective_contract = (
        PERSPECTIVE_CONTRACTS.get(perspective, {}) if unit_type == "compound_perspective" else {}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "job_kind": job.get("kind"),
        "role": job.get("role"),
        "required_query_families": (
            sorted(required_query_families(unit_type, perspective)) if unit_type else []
        ),
        "compound_perspective_contract": perspective_contract,
        "allowed_ledgers": sorted(allowed_ledgers(str(job.get("kind", "")))),
        "schemas": SCHEMAS,
        "nested_schemas": NESTED_SCHEMAS,
        "result_required_fields": RESULT_REQUIRED_FIELDS,
        "conditional_result_fields": {
            "job_with_research_unit": ["closure_basis", "evidence_frontier", "frontier_exhausted"],
            "compound_perspective": ["candidate_exclusions"],
        },
        "human_endpoint": HUMAN_OUTCOME_NODE,
        "ranking_components": RANKING_COMPONENTS,
        "ranking_caps": RANKING_CAPS,
        "search_coverage": {
            "statuses": sorted(SEARCH_COVERAGE_STATUSES),
            "terminal_statuses": sorted(TERMINAL_SEARCH_COVERAGE_STATUSES),
            "saturation_query_families": SATURATION_QUERY_FAMILIES,
            "completion_requires_no_not_yet_searched": True,
        },
        "frontier_contract": {
            "branch_budget": BRANCH_BUDGET_PER_UNIT,
            "materiality_threshold_exclusive": BRANCH_MATERIALITY_THRESHOLD,
            "decisions": sorted(FRONTIER_DECISIONS),
            "expansion_requires": (
                "distinct_causal_route", "human_or_candidate_relevance", "not_already_covered",
                "materiality_above_threshold", "branch_budget_remaining",
            ),
        },
        "retry_contract": {
            "failure_kinds": sorted(FAILURE_KINDS),
            "retry_limit": RETRY_LIMIT,
            "base_delay_seconds": RETRY_BASE_SECONDS,
            "delay_cap_seconds": RETRY_DELAY_CAP_SECONDS,
            "jitter": False,
            "stale_after_seconds": STALE_RUN_SECONDS,
        },
        "idempotency_contract": {
            "search_key_fields": ("research_unit_id", "query_family", "normalized_resource", "normalized_query"),
            "check_existing_operation_before_external_execution": True,
            "reuse_query_bound_receipts": True,
            "duplicate_search_operations_prohibited": True,
            "validated_result_hash_is_completion_checkpoint": True,
            "duplicate_validation_prohibited": True,
            "duplicate_completion_prohibited": True,
        },
        "runtime_contracts": {
            "job_required_fields": JOB_REQUIRED_FIELDS,
            "attempt_required_fields": ATTEMPT_REQUIRED_FIELDS,
            "program_state_required_fields": PROGRAM_STATE_REQUIRED_FIELDS,
            "job_statuses": sorted(JOB_STATUSES),
            "attempt_statuses": sorted(ATTEMPT_STATUSES),
            "completion_states": sorted(COMPLETION_STATES),
        },
        "score_presentation": {
            "repurposing_readiness_max": REPURPOSING_READINESS_MAX,
            "repurposing_readiness_step": REPURPOSING_READINESS_STEP,
        },
        "controlled_values": {
            "human_relevance": sorted(HUMAN_RELEVANCE_LEVELS),
            "claim_direction": sorted(CLAIM_DIRECTIONS),
            "candidate_class": sorted(CANDIDATE_CLASSES),
            "compound_origin": sorted(COMPOUND_ORIGINS),
            "target_endpoint_type": sorted(TARGET_ENDPOINT_TYPES),
            "council_disposition": sorted(COUNCIL_DISPOSITIONS),
            "rank_section": list(RANK_SECTION_ORDER),
            "search_coverage_status": sorted(SEARCH_COVERAGE_STATUSES),
            "frontier_decision": sorted(FRONTIER_DECISIONS),
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
