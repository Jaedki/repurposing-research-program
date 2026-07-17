#!/usr/bin/env python3
"""Shared schema and workflow constants for repurposing research runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
MAX_ACTIVE_JOBS = 1

BROAD_DOMAINS = (
    "human_gene_disease",
    "worm_model_orthology",
    "allele_function",
    "structure_complex_localization",
    "interactions_pathways",
    "cell_circuit",
    "phenotype_phenomics",
    "modifiers_omics",
    "pharmacology_landscape",
    "assay_interpretation",
)

GLOBAL_PERSPECTIVES = (
    "direct_molecular",
    "phenotype_first",
    "vulnerability_inverse",
    "compensatory_network",
    "maximal_novelty",
    "natural_compounds",
)

BASE_QUERY_FAMILIES = {
    "primary_retrieval",
    "counterevidence",
    "backward_citation",
    "forward_citation",
    "missing_branch",
}
EVIDENCE_QUERY_FAMILIES = {"authoritative_database"}
COMPOUND_QUERY_FAMILIES = {
    "exact_compound_literature",
    "chemical_database",
    "identity_verification",
    "negative_direction",
}

COUNCIL_STAGES = (
    ("advocate_case", "advocate"),
    ("skeptic_review", "skeptic"),
    ("advocate_response", "advocate"),
    ("fact_audit", "fact_auditor"),
)

SKEPTIC_CRITIQUE_DOMAINS = {
    "mechanism_direction",
    "worm_target_orthology",
    "allele_relevance",
    "pharmacology_selectivity",
    "exposure_feasibility",
    "phenomics_confounding",
}

ALLOWED_EXCLUSION_REASONS = {
    "wrong_direction",
    "causal_path_refuted",
    "assay_incompatible_confounding",
    "chemical_identity_not_screenable",
}

CALIBRATIONS = {
    "established",
    "supported_with_qualifier",
    "plausible_inference",
    "speculative",
    "unresolved",
    "contradicted",
}

LEDGER_KEYS = {
    "source_corpus.jsonl": "source_id",
    "search_log.jsonl": "query_id",
    "claim_ledger.jsonl": "claim_id",
    "evidence_graph.jsonl": "edge_id",
    "subtopic_registry.jsonl": "subtopic_id",
    "research_units.jsonl": "unit_id",
    "unit_audits.jsonl": "audit_id",
    "candidate_records.jsonl": "candidate_id",
    "council_records.jsonl": "candidate_id",
    "council_exchanges.jsonl": "exchange_id",
}

LEDGER_SCHEMAS = {
    "source_corpus.jsonl": (
        "source_id", "canonical_identifier", "identifier_type", "title", "year",
        "source_kind", "source_family", "discovered_by_units", "discovery_query_ids",
        "metadata_verified", "screen_decision", "exclusion_reason", "original_acquired",
        "original_pointer", "content_verified", "verification_method", "verification_scope",
        "supported_claim_ids", "compaction_receipt_path", "compaction_record_hash",
    ),
    "search_log.jsonl": (
        "query_id", "research_unit_id", "subtopic_id", "query_family", "resource", "query",
        "result_count", "deduplicated_count", "screened_count", "acquired_count",
        "original_verified_count", "page_count", "pagination_complete", "continuation_exhausted",
        "compact_payload_paths", "pagination_trace", "acquired_source_ids",
        "original_verified_source_ids", "executed_by_agent_id", "executor_role", "origin_job_id", "retained_source_ids",
        "new_subtopic_ids", "new_claim_ids", "new_candidate_ids", "outcome",
        "rate_limit_pending", "closure_note",
    ),
    "claim_ledger.jsonl": (
        "claim_id", "subtopic_id", "claim", "evidence_kind", "source_ids", "calibration",
        "directionality", "allele_relevance", "scope_conditions", "contrary_claim_ids", "audit_status",
    ),
    "evidence_graph.jsonl": (
        "edge_id", "from_node", "to_node", "relation", "direction", "directionality_status",
        "allele_mode_effect", "claim_ids", "audit_status",
    ),
    "subtopic_registry.jsonl": (
        "subtopic_id", "parent_id", "name", "relation_to_case", "depth", "discovered_by",
        "candidate_relevant", "required_research_unit_ids", "status", "closure_reason",
    ),
    "research_units.jsonl": (
        "unit_id", "unit_type", "subtopic_id", "perspective", "worker_agent_id", "auditor_agent_id",
        "status", "audit_status", "planned_query_families", "completed_query_families",
        "independent_audit_query_ids", "rate_limit_pending", "known_high_yield_search_remaining",
        "unresolved_repair_count", "candidate_ids", "absence_reason",
    ),
    "unit_audits.jsonl": (
        "audit_id", "unit_id", "auditor_agent_id", "checked_source_ids", "independent_query_ids",
        "material_findings", "repairs_completed", "perspective_distinctness_verified",
        "source_overlap_assessment", "final_status", "closure_basis",
    ),
    "candidate_records.jsonl": (
        "candidate_id", "canonical_name", "canonical_identifier", "registry_identifiers",
        "structure_identity_key", "chemical_node_id", "identity_source_ids", "entity_type",
        "identity_verified", "human_gene", "worm_gene", "allele_mode", "worm_model", "origin",
        "source_research_unit_ids", "causal_paths", "rationale", "phenomic_interpretation",
        "decisive_uncertainty", "dossier_path", "council_disposition", "fact_audit_status",
    ),
    "council_records.jsonl": (
        "candidate_id", "advocate_agent_id", "skeptic_agent_id", "fact_auditor_agent_id",
        "direct_response_complete", "critique_checklist_complete", "novelty_challenge_resolved",
        "fact_audit_status", "material_claim_ids", "claim_verdicts", "independent_checks",
        "surviving_causal_path_ids", "disposition", "exclusion_reason", "unresolved_material_claims",
        "debate_path", "fact_audit_path",
    ),
    "council_exchanges.jsonl": (
        "exchange_id", "candidate_id", "role", "agent_id", "exchange_type", "responds_to_id",
        "content", "assertions", "claim_ids", "critique_domains", "challenge_items",
        "response_items", "fact_audit_status",
    ),
}

SOURCE_ALLOWED_FIELDS = set(LEDGER_SCHEMAS["source_corpus.jsonl"])


def required_query_families(unit_type: str) -> set[str]:
    families = set(BASE_QUERY_FAMILIES)
    if unit_type in {"broad_evidence", "subtopic_evidence"}:
        families.update(EVIDENCE_QUERY_FAMILIES)
    if unit_type in {"subtopic_compound", "global_perspective"}:
        families.update(COMPOUND_QUERY_FAMILIES)
    return families


def role_contract(job: dict[str, Any], unit: dict[str, Any] | None) -> dict[str, Any]:
    unit_type = str((unit or {}).get("unit_type", ""))
    required_families = sorted(required_query_families(unit_type)) if unit_type else []
    kind = str(job.get("kind", ""))
    role = str(job.get("role", ""))
    contract: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "job_kind": kind,
        "required_query_families": required_families,
        "ledger_schemas": LEDGER_SCHEMAS,
        "result_required_fields": (
            "job_id", "packet_hash", "all_chunks_processed", "outcome", "ledger_updates", "approved_subtopics"
        ),
        "tool_paths": {
            "source_compactor": str((Path(__file__).resolve().parent / "compact_source_payload.py")),
            "source_fetcher": str((Path(__file__).resolve().parent / "fetch_source_payload.py")),
            "search_record_builder": str((Path(__file__).resolve().parent / "build_search_record.py")),
            "result_validator": str((Path(__file__).resolve().parent / "orchestrate_program.py")),
        },
        "preferred_source_resources": {
            "literature": ["NCBI Entrez/PubMed", "PMC", "primary publisher record"],
            "genes_models": ["WormBase/Alliance", "Ensembl", "UniProt"],
            "pathways_networks": ["Reactome", "STRING", "Open Targets"],
            "chemistry_pharmacology": ["ChEMBL", "BindingDB", "PubChem", "ChEBI", "DrugCentral"],
        },
        "source_rule": (
            "Every retained source must originate from compact_source_payload.py, point to its compact receipt, "
            "have receipt_record.query_id equal to its search query_id, and be original-content or "
            "authoritative-record verified before supporting a claim."
        ),
        "path_rule": (
            "Resolve every relative artifact and dependency path against the packet run_root, never against the "
            "current directory, packet directory, result directory, or an inferred staging directory. Check the "
            "controller-generated dependency artifact_manifest before claiming that a worker artifact is missing."
        ),
        "source_enum_rule": "Use screen_decision=include or exclude only; retained is not a permitted value.",
        "search_rule": (
            "Log executor identity, a receipt-bound pagination trace, acquired and original-verified source IDs, "
            "citation-chain searches, and a non-rhetorical closure note. Counts must be exactly derivable from "
            "the compact receipts and source-ID lists; do not estimate or inflate them."
        ),
        "completion_rule": (
            "Do not complete because enough material exists. Complete only when every declared query family, "
            "continuation, counterevidence branch, citation trail, and decision-changing relation is resolved."
        ),
        "ambiguity_rule": "Preserve ambiguity and contradictory evidence; never manufacture confidence or sources.",
        "handoff_rule": (
            "Before handoff, run orchestrate_program.py validate-result for this run and job; repair until it passes."
        ),
    }
    if role in {"auditor", "closure_auditor"}:
        contract["independence_rule"] = (
            "Run at least one new missing-branch or counterevidence query under your own agent ID; "
            "do not reuse a worker-executed query as the independent audit."
        )
    if kind == "council_turn":
        contract["council_rule"] = (
            "Use structured assertions and the stage-specific challenge or response items. "
            "Every factual assertion must reference canonical claim IDs."
        )
    if kind == "council_fact_auditor":
        contract["council_rule"] = (
            "Verify every material debate claim, record your own agent ID on each independent check, "
            "and retain only connected candidate causal_path IDs whose claims survive fact audit."
        )
    if kind in {"final_repair_worker", "final_repair_auditor"}:
        contract["repair_rule"] = (
            "Address every supplied final-validation error through staged records; do not bypass or weaken validation."
        )
    return contract
