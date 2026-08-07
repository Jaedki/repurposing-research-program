"""Static workflow and scientific contracts.

This module contains data only. Runtime orchestration is owned by the orchestration module.
"""

from __future__ import annotations

import re
from typing import Any


OBJECTIVE = (
    "Identify existing drugs whose established mode of action could plausibly alter a "
    "specific evidence-backed element of the supplied disease pathology. A prior "
    "disease-drug literature association is not required."
)
EXPERIMENTAL_USE_POLICY = (
    "Hypothesis generation only. Outputs are not clinical advice or proof of efficacy."
)
_PUBLICATION_ID_PATTERN = r"(?:PMID:\d+|PMCID:PMC\d+|DOI:10\.\d{4,9}/\S+)"
_PUBLICATION_ID = re.compile(rf"^{_PUBLICATION_ID_PATTERN}$", re.IGNORECASE)
CANONICAL_DOCUMENT_ID = re.compile(
    rf"^(?:{_PUBLICATION_ID_PATTERN}|"
    r"S2:[A-F0-9]{40}|"
    r"(?:MONARCH-ASSOC|DISMECH-FILE)-[A-F0-9]{24}|"
    r"(?:ORPHA|CGGV|CLINGEN|GENCC|CLINVAR|UNIPROT(?:KB)?|HPA|"
    r"NCBI(?:-BOOKSHELF|-GENE)?|CHEMBL|PUBCHEM|DRUGBANK|DAILYMED|FDA|EMA|"
    r"WHO|ISBN|NCT):\S+|NCT\d{8}|https://\S+)$",
    re.IGNORECASE,
)
RESEARCH_DOCUMENT_BASE_FIELDS = ("document_id", "title", "source")
RESEARCH_DOCUMENT_PASSAGES_FIELD = "evidence_passages"
RESEARCH_DOCUMENT_REQUIRED_FIELDS = (
    *RESEARCH_DOCUMENT_BASE_FIELDS,
    RESEARCH_DOCUMENT_PASSAGES_FIELD,
)
EVIDENCE_PASSAGE_FIELDS = ("text", "locator")
RESEARCH_DOCUMENT_ID_FORMATS = (
    "PMID:<digits>",
    "PMCID:PMC<digits>",
    "DOI:10.<4-9 digits>/<non-whitespace suffix>",
    "S2:<40 hexadecimal characters> (namespaced Semantic Scholar paper ID)",
    "MONARCH-ASSOC-<24 hexadecimal characters>",
    "DISMECH-FILE-<24 hexadecimal characters>",
    "<namespace>:<non-whitespace identifier>, where namespace is ORPHA, CGGV, CLINGEN, "
    "GENCC, CLINVAR, UNIPROT, UNIPROTKB, HPA, NCBI, NCBI-BOOKSHELF, NCBI-GENE, "
    "CHEMBL, PUBCHEM, DRUGBANK, DAILYMED, FDA, EMA, WHO, ISBN, or NCT",
    "NCT<8 digits>",
    "https://<non-whitespace URL>",
)
RESEARCH_DOCUMENT_CONTRACT = {
    "required_fields": list(RESEARCH_DOCUMENT_REQUIRED_FIELDS),
    "additional_fields": True,
    "document_id_formats": list(RESEARCH_DOCUMENT_ID_FORMATS),
    "field_contracts": {
        RESEARCH_DOCUMENT_PASSAGES_FIELD: {
            "type": "non-empty list of objects",
            "required_fields": list(EVIDENCE_PASSAGE_FIELDS),
            "additional_fields": False,
            "value_rule": "text and locator must both be non-empty strings",
        }
    },
}
RESEARCH_DOCUMENT_EXAMPLE = (
    '{"document_id":"PMID:12345678","title":"Example paper",'
    '"source":"PubMed","evidence_passages":'
    '[{"text":"Exact inspectable evidence.","locator":"Results, paragraph 2"}]}'
)
STAGES = (
    "pathology_source_screening",
    "pathology_source_adjudication",
    "pathology_sources",
    "pathology_landscape_scan",
    "pathology_curation",
    "evidence_graph",
    "candidate_seed_generation",
    "candidate_identity",
    "candidate_review",
    "candidate_audit",
)
GRAPH_INDEX_FIELDS = ("node_id", "label", "node_type", "disposition", "aliases")
ASSERTION_EVIDENCE_TYPES = frozenset({
    "human", "animal", "cell", "biochemical", "inferred",
})
ASSERTION_POLARITIES = frozenset({"supports", "contradicts"})
PATHOLOGY_ASSERTION_ENDPOINT_RULE = (
    "Assertions are optional and may use only node_id values listed in "
    "context.allowed_assertion_nodes; never use aliases, cross-references, or newly researched "
    "entities as endpoints, and keep mechanisms without two allowed endpoints in the profile."
)
_CITATION_FIELDS = frozenset({"source_ids", "pathology_source_ids", "mechanism_source_ids"})
_SOURCE_CHECK_VERDICTS = frozenset({
    "supports", "partly_supports", "does_not_support", "contradicts",
})
SCORE_VALUES = frozenset({5, 10, 15, 20})
SCORE_COMPONENT_RUBRIC = {
    "drug_action_confidence": {
        "label": "Drug-action confidence",
        "question": "How securely is the proposed action established for this exact drug?",
        "anchors": {
            5: "Weak or indirect support for the action, but it is not unsupported or opposite.",
            10: "Credible action with limited, model-specific, or concentration-relevance evidence.",
            15: "Direct, good-quality pharmacology with one material target, direction, or dose uncertainty.",
            20: "Direct, convergent evidence establishes the exact target, direction, and relevant action.",
        },
    },
    "disease_mechanism_relevance": {
        "label": "Disease-mechanism relevance",
        "question": "Independent of the drug, how securely does the focal mechanism belong to disease pathology?",
        "anchors": {
            5: "An indirect but coherent disease link supports testing the mechanism.",
            10: "Disease association is credible but causal role or desired direction is limited.",
            15: "Strong disease evidence supports the mechanism and direction with one material uncertainty.",
            20: "Convergent disease evidence establishes a causal role and the desired biological direction.",
        },
    },
    "mechanistic_bridge_plausibility": {
        "label": "Mechanistic-bridge plausibility",
        "question": "How well does the drug action connect directionally to the desired disease state?",
        "anchors": {
            5: "A long or speculative bridge remains coherent and testable despite major stated assumptions.",
            10: "A multi-step bridge has partial support and several material assumptions.",
            15: "A short, directionally coherent bridge has direct support and one material assumption.",
            20: "Direct evidence links the drug action to the desired state with no material unsupported step.",
        },
    },
    "translational_feasibility": {
        "label": "Translational feasibility",
        "question": "Can relevant action plausibly be achieved in the needed tissue, dose, route, and timing?",
        "anchors": {
            5: "Delivery or exposure is highly uncertain or difficult, but not demonstrated impossible.",
            10: "Major exposure, dosing, tissue, route, or timing limitations may be surmountable.",
            15: "Relevant exposure and use are plausible with one material translational uncertainty.",
            20: "Established human use supports relevant exposure, formulation, route, and timing.",
        },
    },
}
SCORE_COMPONENTS = tuple(SCORE_COMPONENT_RUBRIC)
MAX_SCORE = max(SCORE_VALUES) * len(SCORE_COMPONENTS)
SCORE_RUBRIC = {
    "method": (
        f"Score each of the {len(SCORE_COMPONENTS)} distinct components from its anchors, then "
        f"sum the values without weighting. The total is a prioritisation score out of "
        f"{MAX_SCORE}, not a probability of efficacy. Counterevidence earns no points: lower "
        "any component whose premise it directly challenges, and otherwise retain it as an "
        "unscored reservation."
    ),
    "zero_policy": (
        "Zero is not a scored level. Use a bounded exclusion only when the evidence establishes "
        "an exclusion condition; otherwise retain the candidate at 5 with the reservation stated."
    ),
    "components": SCORE_COMPONENT_RUBRIC,
}
SCORE_LABELS = {
    component: definition["label"]
    for component, definition in SCORE_RUBRIC["components"].items()
}
PRIOR_ART_STATUSES = frozenset({
    "none_found",
    "preclinical_only",
    "human_intervention",
    "established_use",
    "unclear",
})
AUDIT_EXCLUSION_POLICY = {
    "exact_disease_use": (
        "The retained corpus establishes that the exact candidate is already an established "
        "therapeutic use for the exact disease."
    ),
    "human_intervention": (
        "The retained corpus establishes a registered or published human interventional study "
        "of the exact candidate in the exact disease; observational or preclinical work does not qualify."
    ),
    "unsupported_action": (
        "The retained corpus contains no credible direct support that the exact candidate has the "
        "proposed drug action; missing downstream disease evidence alone does not qualify."
    ),
    "opposite_action": (
        "The established drug action is directionally opposite to the desired biological state and "
        "the retained corpus supplies no coherent compensatory rationale."
    ),
    "impossible_translational_feasibility": (
        "The retained corpus demonstrates that relevant action or exposure cannot be achieved; "
        "difficulty, missing data, or uncertainty does not qualify."
    ),
    "invalid_candidate": (
        "The retained corpus establishes that the entity is not an existing drug or administered "
        "intervention suitable for repurposing, such as a placebo, vehicle, or sham; unresolved "
        "identity alone does not qualify."
    ),
}
AUDIT_EXCLUSION_REASONS = frozenset(AUDIT_EXCLUSION_POLICY)
LANDSCAPE_PROPOSAL_TYPES = frozenset({
    "driver", "mechanism", "phenotype", "biomarker", "context",
})
ASTA_CALL_TOOLS = frozenset({
    "search_papers_by_relevance", "get_citations", "snippet_search",
})
ASTA_OPERATION_ID_PATTERN = r"(?i)^ASTA-OP-[A-Z0-9][A-Z0-9_-]*$"
ASTA_PAPER_ID_PATTERN = (
    r"(?i)^(?:[A-F0-9]{40}|CorpusId:\d+|DOI:\S+|ARXIV:\S+|MAG:\d+|ACL:\S+|"
    r"PMID:\d+|PMCID:(?:PMC)?\d+|URL:https://\S+)$"
)
ASTA_CALL_OUTCOMES = frozenset({"completed", "tool_error", "no_response"})
ASTA_CALL_PROFILES = frozenset({"standard", "minimal"})
ASTA_CALL_ERROR_TYPES = frozenset({
    "authentication", "invalid_request", "network", "rate_limit", "server",
    "timeout", "unknown",
})
ASTA_NO_RESPONSE_SECONDS = 180

STAGE_GUIDANCE: dict[str, dict[str, Any]] = {
    "pathology_source_adjudication": {
        "role": "pathology-source sentence adjudicator",
        "task": (
            "Classify only the supplied flagged DisMech sentences. Do not search, add facts, "
            "rewrite text, infer a candidate, or perform pathology curation. Return "
            "retain_pathology only when the complete sentence is exclusively causal biology, "
            "pathology, phenotype, diagnosis, or source metadata and contains no therapeutic "
            "interpretation, efficacy claim, clinical intervention, administered intervention, "
            "or candidate framing. Return exclude_treatment for wholly treatment-oriented text, "
            "exclude_mixed when treatment and useful pathology coexist, and exclude_ambiguous "
            "when the distinction is uncertain. Mixed and ambiguous sentences fail closed."
        ),
        "collections": ["sentence_decisions"],
    },
    "pathology_landscape_scan": {
        "role": "treatment-blind disease pathology landscape researcher",
        "task": (
            "Perform one shallow, treatment-blind Asta landscape scan. Search for disease-"
            "pathology papers with search_papers_by_relevance. For each relevance result retained "
            "for evaluation, inspect related citing papers with get_citations and run paper-"
            "restricted snippet_search on that result and every related citation retained for "
            "evaluation. Use directly supported claims to identify coverage gaps in the supplied "
            "source_node_index for the following curation agent; that agent decides whether a "
            "proposal is distinct, and Python assigns node IDs. Do not perform deep node research, "
            "candidate research, or recursive citation traversal."
        ),
        "collections": ["documents", "landscape_proposals", "asta_call_receipts"],
    },
    "pathology_curation": {
        "role": "disease pathology concept curator",
        "task": (
            "Use only the supplied packet; do not search or perform deep research. Convert the "
            "supplied Monarch, DisMech, and Asta pathology nodes into coherent run-local concepts "
            "before research; Asta proposals have no privileged status over source-adapter claims; "
            "do not merge distinct claims merely to reduce concept count, but concept distinctness "
            "does not create a research job. Treat concept type and disposition as separate judgments: "
            "researchability follows the supplied disease claim, not its provisional label. Keep each "
            "mechanism concept atomic: one pathological state or process at one causal level. Merge "
            "only when the same pathological state, biological context, disease-specific profile, and "
            "desired biological state accurately describe every member. Keep source-supported claims at different causal "
            "levels separate even when one causes another; distinct abnormal processes or desired "
            "biological states remain separate. A broad mechanism and a supported molecular "
            "submechanism remain separate when each implies a distinct desired biological state. "
            "Shared genes, ontology IDs, pathways, anatomy, or "
            "causal relationships do not establish equivalence; represent overlap or causality with "
            "the supplied source edges or later researched assertions, not by merging concepts. Keep bare entities, disease "
            "drivers, mechanisms, and phenotypes separate unless they express the same claim. "
            "Input node_type values are provisional source-adapter categories, not curated "
            "classifications; assign concept_type independently from the supplied claim, "
            "source_payloads, and edges. "
            "The supplied nodes are disease-specific source claims, so same-label gene-level "
            "claims from different sources may merge when neither specifies a mutation, variant, "
            "repeat, model genotype, or downstream mechanism; keep those more specific claims "
            "separate from the broader gene association. This identity rule does not determine "
            "either concept's disposition. "
            "Merge true duplicate records into the retained concept so all evidence survives. "
            "Retain original labels as aliases and assign every non-anchor source node exactly "
            "once. Use supplied disease_context to interpret nodes, but do not create concepts "
            "from administrative metadata alone. After resolving identity, assign disposition "
            "independently. Researchability may not be deferred to deep research. A distinct "
            "concept is research only when the supplied packet already establishes a specific "
            "abnormal biological state or process, a specific well-supported causal lesion that "
            "defines its own pathology route and non-generic compensatory direction, or a major "
            "phenotype defining a distinct intervention objective. A bare gene or gene-disease "
            "association, risk factor, model genotype, broad pathway, terminal outcome, or mutation "
            "label without supplied functional pathology is normally context_only. Generic gene "
            "and lesion-specific claims do not both create research routes unless each supplies a "
            "distinct intervention variable; keep the non-qualifying concept as linked context for "
            "provenance. Subordinate symptoms, clinical signs, severity "
            "descriptors, measurement-only biomarkers, and measurement endpoints are normally context_only even when "
            "measurable; attach them to the relevant research concept through related_concept_ids. "
            "This attachment is related research context, not semantic merger. A phenotype receives "
            "its own research packet only when it is distinct modifiable pathology or a genuinely "
            "separate intervention objective. A claim described as a biomarker is not forced into "
            "context_only when the supplied evidence instead establishes that the measured entity or "
            "process is causal or modifiable pathology; classify that biological claim independently, "
            "normally as a mechanism. A bare entity or observational readout is also "
            "supporting context unless its abnormal state satisfies this research test. Otherwise "
            "retain relevant supporting claims "
            "context_only and attach them to relevant research concepts; uncertainty never "
            "upgrades a claim to research. Exclude only malformed or "
            "irrelevant records, generic ontology noise, and self-referential disease concepts. "
            "When uncertain, keep concepts separate for identity but do not upgrade uncertain "
            "research eligibility. Do not introduce or discuss drugs or treatments."
        ),
        "collections": ["concepts"],
    },
    "pathology_node_research": {
        "role": "disease pathology researcher",
        "task": (
            "Research this one curated pathology concept in exceptional disease-specific "
            "depth. Explain its normal state, pathological change, causal role, mechanisms, "
            "biological context, uncertainty, contradictions, and gaps. Define one concise, "
            "evidence-grounded primary desired biological state containing one biological "
            "variable and one desired direction. For an irreversible driver, define the specific "
            "compensatory state rather than generic improvement. Put other atomic biological "
            "states in secondary_desired_states and keep phenotype outcomes out of both state "
            "fields. Define phenotype_objective separately as the disease-relevant phenotype "
            "change sought, not an assay, stage, population, or treatment. Label synthesis as "
            "inference and cite the directional evidence it follows from. Record important "
            "bundled or missing submechanisms as explicit gaps; do not create additional graph "
            "nodes. "
            f"{PATHOLOGY_ASSERTION_ENDPOINT_RULE} Express each "
            "researched graph assertion as a biological triple with one or "
            "more evidence_context entries that preserve evidence type, model, stage, polarity, "
            "summary, and citations; Python assigns the final assertion ID. Retain only "
            "established pathology observations of movement toward that state; an empty list is "
            "valid. Keep discovery "
            "pathology-led: do not search for candidates, therapies, repurposing, or disease-drug "
            "associations. When an intervention appears in a source, retain only directly supported "
            "causal biology and pathology; do not use its therapeutic interpretation, efficacy, "
            "candidate status, or trial history to construct profiles or assertions."
        ),
        "collections": ["documents", "profiles", "assertions"],
    },
    "candidate_seed_research": {
        "role": "mechanism-directed candidate seed researcher",
        "task": (
            "For this frozen researched pathology concept and its linked context, generate a "
            "focused set of diverse existing-drug seeds. Keep the focal primary "
            "desired_biological_state as the main candidate anchor. Secondary desired states and "
            "the phenotype objective are context only and do not create additional discovery "
            "routes by themselves. "
            "List only graph assertion IDs actually used and explain the selected graph support "
            "once in graph_rationale without repeating the drug mechanism hypothesis. An empty "
            "assertion_ids list is valid when the focal profile alone supports the hypothesis, "
            "but graph_rationale must say so. Include only materially used graph nodes. "
            "Before searching, review the focal profile and every immediate source edge, researched "
            "assertion, and neighbouring node supplied in focal_context. Include only context that "
            "materially contributes after that bounded review; a focal-only hypothesis remains valid. "
            "After that review, cross-node use is never mandatory. "
            "Do not pad the "
            "list. A supplied linked graph node may support a symptomatic or compensatory candidate "
            "only when its relationship to the focal concept and candidate hypothesis is "
            "mechanistically justified. "
            "Retain a less-plausible seed only when it offers a discriminating, mechanism-relevant "
            "readout, and state what that readout would resolve. "
            "Use the compact graph index to identify context and retrieve only concepts materially "
            "relevant to the focal rescue; do not traverse the graph for completeness. "
            "Search from the supplied target or process to established drug action; do not use "
            "disease-specific drug literature or queries combining the disease with drug, "
            "treatment, therapy, trial, or repurposing terms. Cite pathology and mode-of-action "
            "evidence separately."
        ),
        "collections": ["documents", "candidates", "exclusions"],
    },
    "candidate_identity": {
        "role": "candidate identity adjudicator",
        "task": (
            "Resolve only the supplied UniChem-flagged candidate identities before evidence "
            "review. Every queued seed must appear exactly once. Use authoritative identity "
            "sources to decide whether queued seeds are the same intervention, attach a resolved "
            "group to one controller-listed canonical candidate option, remain separate, or stay "
            "unresolved/conflicting. "
            "Do not alter mechanism or pathology evidence or split seeds sharing an exact UCI. "
            "Exact UniChem groups not present in the queue are controller-owned and must not be "
            "reconsidered."
        ),
        "collections": ["documents", "identity_groups"],
    },
    "candidate_evidence_review": {
        "role": "pathology-concept candidate evidence reviewer",
        "task": (
            "Treat the supplied frozen pathology profiles and candidate-specific selected_graph_evidence "
            "as authoritative disease context. Its assertions are exactly those selected by assertion_id; "
            "its source edges are limited to selected-node edges supported by the candidate's cited "
            "pathology sources. Do not infer graph support from evidence outside that projection. For "
            "every candidate, first retrieve primary or authoritative sources that verify identity, "
            "target and action, pharmacology, relevant exposure, and measurable readouts, then map "
            "those facts to the supplied pathology. Build an evidence dossier rather than a score "
            "or eligibility decision: state the hypothesis, cited supporting findings, a concise "
            "mechanistic bridge, its explicit assumptions, cited why-not findings, and limitations. "
            "A long or unconventional bridge remains a valid hypothesis when its assumptions are "
            "clear. Only after constructing the mechanism, check exact-disease prior art and classify "
            "it as none found, preclinical only, human intervention, established use, or unclear. "
            "Preserve decision-changing negative evidence. Report safety only when it opposes the "
            "desired phenotype, prevents relevant exposure, confounds the readout, or changes "
            "prioritisation. Record only drug names or salt forms explicitly used in cited evidence."
        ),
        "collections": ["documents", "reviews"],
    },
    "candidate_audit": {
        "role": "independent candidate auditor",
        "task": (
            "Use only the supplied retained corpus; do not search for or add evidence. Independently "
            "inspect the supplied evidence passages, abstracts, structured source content, raw-source "
            "references, and frozen graph rather than restating each dossier. Partition every candidate exactly "
            "once into a scored assessment or a cited exclusion. Exclude only established exact-"
            "disease use, exact-disease human intervention, unsupported proposed drug action, action "
            "clearly opposite to the desired state without compensation, demonstrated impossibility "
            "of relevant action or exposure, or an invalid candidate class. Unresolved identity, "
            "weak evidence, long causal distance, uncertain exposure, preclinical-only evidence, and "
            "material assumptions remain scored with explicit why-not findings. For every assessment and "
            "exclusion, decide whether each cited source supports, partly supports, does not support, or "
            "contradicts the exact place it is used. Do not defer this judgment or request re-verification. "
            "Counterevidence earns no points: lower any component whose premise "
            "it directly challenges; if it does not challenge a scored premise, retain it only as an "
            "unscored why-not finding and in the net assessment. Assign one cited 5, 10, 15, or 20 score "
            "for each of drug-action confidence, disease-mechanism relevance, mechanistic-bridge "
            "plausibility, and translational feasibility; and give a cited net assessment that weighs "
            "the strongest support against the strongest reservation. Python computes the raw total "
            "and ranking. Return only audited aliases and why-not findings that may enter final output."
        ),
        "collections": ["assessments", "excluded_candidates"],
    },
}

ROW_SCHEMAS = {
    "flagged_sentences": {
        "required_fields": ["sentence_id", "sentence", "signals", "paths"],
        "additional_fields": False,
    },
    "sentence_decisions": {
        "required_fields": ["sentence_id", "decision", "reason"],
        "additional_fields": False,
    },
    "documents": {
        "required_fields": list(RESEARCH_DOCUMENT_BASE_FIELDS),
        "additional_fields": True,
    },
    "landscape_proposals": {
        "required_fields": [
            "label", "provisional_type", "claim", "index_comparison", "source_ids",
        ],
        "additional_fields": False,
    },
    "asta_call_receipts": {
        "required_fields": [
            "operation_id", "tool", "paper_id", "attempt", "request_profile",
            "outcome", "elapsed_seconds", "result_count", "error_type",
        ],
        "additional_fields": False,
        "field_contracts": {
            "operation_id": {
                "type": "string",
                "pattern": ASTA_OPERATION_ID_PATTERN,
            },
            "tool": {
                "type": "string",
                "allowed_values": sorted(ASTA_CALL_TOOLS),
                "value_rule": (
                    "use the bare logical operation name, not an MCP-qualified tool name"
                ),
            },
            "paper_id": {
                "type": "string or null",
                "pattern": ASTA_PAPER_ID_PATTERN,
                "value_rule": (
                    "must be null for search_papers_by_relevance and a matching string for "
                    "get_citations or snippet_search"
                ),
            },
            "attempt": {
                "type": "integer (not boolean)",
                "allowed_values": [1, 2],
            },
            "request_profile": {
                "type": "string",
                "allowed_values": sorted(ASTA_CALL_PROFILES),
                "value_rule": "standard for attempt 1; minimal for attempt 2",
            },
            "outcome": {
                "type": "string",
                "allowed_values": sorted(ASTA_CALL_OUTCOMES),
                "value_rule": "use completed, not success, for a successful call",
            },
            "elapsed_seconds": {
                "type": "non-negative number (not boolean)",
            },
            "result_count": {
                "type": "non-negative integer (not boolean) or null",
                "value_rule": "required for completed; null for tool_error or no_response",
            },
            "error_type": {
                "type": "string or null",
                "allowed_values": [None, *sorted(ASTA_CALL_ERROR_TYPES)],
                "value_rule": (
                    "null for completed; required for tool_error or no_response; "
                    "authentication and invalid_request are blocking defects and cannot be "
                    "submitted as outages"
                ),
            },
        },
    },
    "source_nodes": {
        "required_fields": ["node_id", "label", "node_type", "source_ids"],
        "additional_fields": True,
    },
    "source_edges": {
        "required_fields": [
            "edge_id", "subject_id", "relation", "object_id", "evidence_summary", "source_ids",
        ],
        "additional_fields": True,
    },
    "source_receipts": {
        "required_fields": ["source", "version", "query", "record_count"],
        "additional_fields": True,
    },
    "disease_context": {
        "required_fields": ["context_id", "section", "value", "source_ids"],
        "additional_fields": True,
    },
    "concepts": {
        "required_fields": [
            "concept_id", "preferred_label", "concept_type", "member_node_ids",
            "aliases", "disposition", "reason", "related_concept_ids",
        ],
        "additional_fields": False,
    },
    "profiles": {
        "required_fields": [
            "node_id", "node_type", "summary", "normal_state", "pathological_state",
            "desired_biological_state", "secondary_desired_states", "phenotype_objective",
            "established_pathology_observations", "causal_role", "mechanisms", "cell_types",
            "anatomical_context", "temporal_context", "upstream_causes",
            "downstream_consequences", "contradictions", "gaps", "uncertainty", "source_ids",
        ],
        "additional_fields": False,
    },
    "assertions": {
        "required_fields": [
            "subject_id", "relation", "object_id", "evidence_context",
        ],
        "additional_fields": False,
    },
    "candidates": {
        "required_fields": [
            "candidate_id", "name", "identifiers", "mechanism_hypothesis",
            "graph_node_ids", "assertion_ids", "graph_rationale", "pathology_source_ids",
            "mechanism_source_ids",
        ],
        "additional_fields": False,
        "field_contracts": {
            "identifiers": {
                "type": "object",
                "value_rule": (
                    "may be empty; each value is a non-empty string or non-empty list of "
                    "non-empty strings"
                ),
            },
        },
    },
    "exclusions": {
        "required_fields": ["name", "reason"],
        "additional_fields": False,
    },
    "identity_groups": {
        "required_fields": [
            "member_seed_ids", "canonical_candidate_id", "status", "preferred_name",
            "identifiers", "reason", "source_ids",
        ],
        "additional_fields": False,
        "field_types": {"identifiers": "object"},
    },
    "reviews": {
        "required_fields": [
            "candidate_id", "hypothesis", "supporting_findings", "mechanistic_bridge",
            "assumptions", "why_not", "prior_art", "aliases", "limitations",
        ],
        "additional_fields": False,
    },
    "assessments": {
        "required_fields": [
            "candidate_id", "source_integrity", "component_scores", "net_assessment",
            "aliases", "why_not",
        ],
        "additional_fields": False,
    },
    "excluded_candidates": {
        "required_fields": [
            "candidate_id", "reason_code", "finding", "source_ids", "source_integrity",
        ],
        "additional_fields": False,
    },
}

PATHOLOGY_PROFILE_LIST_FIELDS = (
    "secondary_desired_states", "mechanisms", "cell_types", "anatomical_context",
    "temporal_context", "upstream_causes", "downstream_consequences", "contradictions",
    "gaps", "source_ids",
)

FIELD_RULES = {
    "pathology_source_adjudication": [
        "partition every supplied sentence_id exactly once",
        "decision is retain_pathology, exclude_treatment, exclude_mixed, or exclude_ambiguous",
        "retain_pathology requires the entire sentence to be pathology-safe without rewriting",
        "exclude mixed or ambiguous sentences; uncertainty never permits restoration",
        "reason is one concise classification rationale and does not repeat the sentence",
        "do not search, cite sources, create nodes, or introduce new text",
    ],
    "pathology_landscape_scan": [
        "asta_call_receipts contains exactly one row per actual Asta call and at least one "
        "search_papers_by_relevance operation",
        "rows sharing operation_id keep the same tool and paper_id; a completed attempt 1 has no "
        "retry, while a failed attempt 1 requires exactly one attempt 2 with request_profile=minimal",
        "no_response uses error_type=timeout, result_count=null, and elapsed_seconds of at least 180",
        "every paper passed to get_citations also receives paper-restricted snippet_search, "
        "including after a terminal citation failure",
        "a positive completed relevance search includes get_citations and snippet_search operations; "
        "documents or proposals require a completed snippet_search with a positive result_count",
        "if no relevance search completes, documents and landscape_proposals are empty; every "
        "terminal call failure has a non-empty gap",
        "provisional_type is driver, mechanism, phenotype, biomarker, or context",
        "each proposal is a specific disease-linked abnormal biological claim that is missing from "
        "or more specific than the supplied index",
        "each proposal contains one pathological state or process at one causal level; one paper may "
        "support multiple separate proposals",
        "each source_id cites a document returned in this result, and every returned document is "
        "cited by at least one proposal",
        "retain only directly supported causal pathology from experimental perturbations; do not "
        "frame a proposal as a drug, treatment, therapeutic, or candidate response",
        "zero proposals are valid after a receipt-verified scan",
    ],
    "pathology_curation": [
        "partition every supplied non-anchor Monarch, DisMech, and Asta node exactly once across concepts",
        "concept_id is one member_node_id; choose an authoritative member ID only after same-level "
        "equivalence is established and the ID denotes the curated concept",
        "keep each mechanism atomic at one causal level and keep source-supported claims at different "
        "causal levels separate even when causally linked",
        "keep distinct abnormal processes and desired biological states separate; retain both a "
        "broad mechanism and a supported molecular submechanism when each could support a distinct "
        "desired biological state",
        "shared identifiers, genes, pathways, anatomy, or causal adjacency are not equivalence; "
        "the same pathological state, biological context, profile, and desired biological state must "
        "fit every merged member; use edges or assertions for relationships rather than merging",
        "same-label gene-level source claims may merge across sources; mutation-, variant-, "
        "repeat-, model-, and mechanism-specific claims remain separate, but this identity rule "
        "does not determine disposition",
        "merge true duplicate records into a retained concept; do not exclude their evidence",
        "concept_type is driver, mechanism, phenotype, or context",
        "disposition is research, context_only, or exclude; every decision has a concise reason",
        "concept distinctness does not create a research job, and researchability may not be "
        "deferred to deep research; a bare gene or gene-disease association, risk factor, model "
        "genotype, broad pathway, terminal outcome, or mutation label without supplied functional "
        "pathology is normally context_only; generic gene and lesion-specific claims do not both "
        "create research routes unless each supplies a distinct intervention variable",
        "disposition follows the biological claim rather than its provisional type: subordinate "
        "phenotypes and measurement-only biomarkers are normally context_only and attach through "
        "related_concept_ids, while a distinct modifiable phenotype may be research and a biomarker-"
        "labelled causal process is classified independently, normally as a mechanism",
        "each context_only concept links to at least one research concept through "
        "related_concept_ids; other dispositions use an empty list",
        "aliases and member_node_ids are JSON lists; uncertain equivalence remains separate",
    ],
    "pathology_node_research": [
        "return exactly one profile whose node_id and node_type match the supplied curated concept",
        "retain at least one independently researched document",
        f"profile fields {', '.join(PATHOLOGY_PROFILE_LIST_FIELDS)} are JSON lists",
        "desired_biological_state contains one biological variable and one desired direction; "
        "it is not a treatment, assay, control, candidate, phenotype outcome, or generic clinical "
        "improvement, and an irreversible driver uses a specific compensatory state",
        "secondary_desired_states contains only distinct atomic biological states and may be "
        "empty; phenotype_objective is a separate disease-phenotype change, not a biological "
        "state, assay, stage, population, or treatment",
        "record important bundled or missing submechanisms in gaps; do not invent graph nodes",
        PATHOLOGY_ASSERTION_ENDPOINT_RULE,
        "established_pathology_observations is a list of observation and source_ids objects; "
        "use an empty list rather than inventing an assay, threshold, or biomarker",
        "assertions contain subject_id, relation, object_id, and evidence_context only; Python "
        "assigns assertion_id from the biological triple",
        "each evidence_context cites retained sources and records evidence_type as human, animal, "
        "cell, biochemical, or inferred; model; stage; polarity as supports or contradicts; and "
        "one context-specific summary; no treatment content",
    ],
    "candidate_seed_research": [
        "candidate discovery keeps the focal primary desired_biological_state as its main anchor; "
        "secondary desired states and phenotype_objective do not create discovery routes by "
        "themselves, while a supplied linked graph node may support a symptomatic or compensatory "
        "candidate when its relationship to the focal concept and hypothesis is mechanistically justified",
        "include every authoritative candidate identifier found because Python submits all "
        "supported identifiers to UniChem; identity resolution belongs to Python and the later "
        "identity-review stage",
        "use native database values under exact UniChem keys: chembl, drugbank, gtopdb, chebi, "
        "unii, pubchem_cid, drugcentral, inchi, or inchikey; retain other identifiers under "
        "their own keys for identity review",
        "each identifier must denote the proposed candidate itself, not one ingredient of a "
        "combination, mixture, formulation, or biologic product",
        "candidates are repurposing hypotheses, not controls or comparators; do not pad the set",
        "graph_node_ids includes the supplied researched concept and may include other indexed "
        "non-anchor concepts materially used by the hypothesis; pathology_source_ids support "
        "those concepts",
        "assertion_ids lists only graph assertions materially used; it may be empty when the focal "
        "profile is sufficient, and graph_rationale explains the selected graph support once "
        "without repeating mechanism_hypothesis",
        "before searching, review every immediate source edge, researched assertion, and neighbouring "
        "node in focal_context; after that bounded review cross-node use is optional and must never be "
        "added for coverage",
        "each graph_node_id has at least one attached source in pathology_source_ids; state the "
        "supported relationship because graph proximity or label similarity is not evidence",
        "mechanism_source_ids support the drug mode of action and exclude disease-specific drug evidence",
    ],
    "candidate_identity": [
        "partition every queued seed_id exactly once across identity_groups",
        "all queued seeds sharing one exact UniChem UCI remain together in one identity_group",
        "canonical_candidate_id is null unless a resolved group attaches to one entry in "
        "context.canonical_candidate_options; when set, copy that entry's candidate_id exactly",
        "for a queued_exact_block option, include every required_member_seed_id in the group; "
        "UCI values elsewhere in identity_queue, including identity_resolution.ucis, are "
        "identity evidence only and are not canonical candidate options",
        "status is resolved, unresolved, or conflicting; uncertainty must remain explicit",
        "member_seed_ids, identifiers, and source_ids are JSON collections",
        "each identity group cites at least one newly retained authoritative identity source",
        "same name alone is not identity evidence; preserve material salt, stereochemical, "
        "mixture, biologic, product, and combination distinctions",
        "treat suspected aliases, salts, and conflicting identifiers as one unresolved identity "
        "until authoritative evidence resolves them",
    ],
    "candidate_evidence_review": [
        "return exactly one review for every candidate in the supplied batch and no others",
        "use only the candidate-specific selected_graph_evidence supplied for graph support: assertions "
        "match selected assertion_ids exactly and source edges are bounded by selected nodes and cited "
        "pathology sources",
        "each review cites at least one document retained in this result through supporting_findings, "
        "why_not, prior_art findings, or aliases",
        "hypothesis and mechanistic_bridge are concise non-empty text; mechanistic_bridge is an "
        "explicit inference and never an exclusion gate",
        "supporting_findings is a non-empty list of finding and source_ids objects",
        "assumptions and limitations are lists of non-empty strings",
        "aliases is a list of name and source_ids objects for drug names or salt forms explicitly "
        "used in cited evidence; use an empty list when none are supported",
        "why_not is a list of finding and source_ids objects containing only counterevidence "
        "encountered during the existing review; use an empty list when absent and do not search "
        "merely to populate it",
        "prior_art has exactly status, summary, and findings; status is none_found, preclinical_only, "
        "human_intervention, established_use, or unclear; positive statuses cite at least one finding",
        "the reviewer does not score, rank, or exclude candidates",
    ],
    "candidate_audit": [
        "assessments and excluded_candidates form a complete non-overlapping partition of every "
        "reviewed candidate",
        "source_integrity has exactly checks; do not return a summary status or generic declaration",
        "each source-integrity check has source_id, scope, verdict, and finding and covers exactly "
        "one source use in a component score, net assessment, indexed alias, indexed why-not "
        "finding, or exclusion",
        "verdict is supports, partly_supports, does_not_support, or contradicts; inspect the supplied "
        "passage, abstract, structured source content, or raw source record and make the decision now; "
        "never defer the decision as needing re-verification",
        "PMID, PMCID, and DOI aliases with the same canonical_publication_id are one source and "
        "must not be cited as independent support within the same scope",
        "component_scores has exactly drug_action_confidence, disease_mechanism_relevance, "
        "mechanistic_bridge_plausibility, and translational_feasibility",
        "each component has exactly value, reason, and source_ids; value is 5, 10, 15, or 20; "
        "Python sums the four values without weighting",
        "each component has at least one supports or partly_supports source-integrity check; "
        "a 20-point component has no does_not_support or contradicts checks",
        "counterevidence never earns positive scoring credit; lower every component whose premise "
        "it directly challenges and otherwise retain it only in why_not and net_assessment",
        "net_assessment has exactly text and source_ids and explicitly weighs decisive support "
        "against the strongest reservation",
        "audited aliases and why_not use cited name or finding objects and are the only review-like "
        "fields that may enter final cards",
        "excluded_candidates use a source-backed reason_code from the bounded exclusion policy and "
        "verify every cited source under the exclusion scope",
        "do not exclude a candidate merely for unresolved identity, weak evidence, long causal "
        "distance, uncertain exposure, preclinical-only evidence, or material assumptions",
    ],
}

_SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "refresh_token",
    "secret",
    "x_api_key",
}
_COMPARATORS = {"placebo", "vehicle", "sham"}
_PATHOLOGY_FORBIDDEN_KEYS = {"candidate", "compound", "drug", "treatment", "therapeutic"}
_RESEARCH_CONTEXT_SECTIONS = {
    "categories",
    "category",
    "classifications",
    "description",
    "disease_term",
    "has_subtypes",
    "inheritance",
    "parents",
    "progression",
    "stages",
    "synonyms",
}
_UNICHEM_API = "https://www.ebi.ac.uk/unichem/api/v1"
_UNICHEM_SOURCE_IDS = {
    "chembl": 1,
    "drugbank": 2,
    "gtopdb": 4,
    "chebi": 7,
    "unii": 14,
    "pubchem_cid": 22,
    "drugcentral": 34,
}
