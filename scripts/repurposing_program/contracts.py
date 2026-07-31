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
    r"(?:MONARCH-ASSOC|DISMECH-FILE)-[A-F0-9]{24}|"
    r"(?:ORPHA|CGGV|CLINGEN|GENCC|CLINVAR|UNIPROT(?:KB)?|HPA|"
    r"NCBI(?:-BOOKSHELF|-GENE)?|CHEMBL|PUBCHEM|DRUGBANK|DAILYMED|FDA|EMA|"
    r"WHO|ISBN|NCT):\S+|NCT\d{8}|https://\S+)$",
    re.IGNORECASE,
)
STAGES = (
    "pathology_source_screening",
    "pathology_source_adjudication",
    "pathology_sources",
    "pathology_curation",
    "evidence_graph",
    "candidate_seed_generation",
    "candidate_identity",
    "candidate_review",
    "candidate_audit",
)
GRAPH_INDEX_FIELDS = ("node_id", "label", "node_type", "disposition", "aliases")
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
    "pathology_curation": {
        "role": "disease pathology concept curator",
        "task": (
            "Use only the supplied packet; do not search or perform deep research. Convert the "
            "supplied source-derived pathology nodes into coherent run-local concepts "
            "before research; do not minimize concept count. Merge only when one disease-specific "
            "biological profile and one desired biological state accurately describe every "
            "member at the same causal level. Shared genes, ontology IDs, pathways, anatomy, or "
            "causal relationships do not establish equivalence; keep bare entities, disease "
            "drivers, mechanisms, and phenotypes separate unless they express the same claim. "
            "Input node_type values are provisional source-adapter categories, not curated "
            "classifications; assign concept_type independently from the supplied claim, "
            "source_payloads, and edges. "
            "The supplied nodes are disease-specific source claims, so same-label gene-level "
            "claims from different sources may merge when neither specifies a mutation, variant, "
            "repeat, model genotype, or downstream mechanism; keep those more specific claims "
            "separate from the broader gene association. "
            "Merge true duplicate records into the retained concept so all evidence survives. "
            "Retain original labels as aliases and assign every non-anchor source node exactly "
            "once. Use supplied disease_context to interpret nodes, but do not create concepts "
            "from administrative metadata alone. After resolving identity, assign disposition "
            "independently. A valid, unique claim is research only when supplied evidence "
            "establishes distinct causal or modifiable pathology, or a major phenotype defining a "
            "distinct intervention objective. Subordinate symptoms, clinical signs, severity "
            "descriptors, and measurement endpoints are context_only even when measurable; attach "
            "them to the relevant research concept. A bare entity or observational readout is also "
            "supporting context unless its abnormal state satisfies this research test. Otherwise "
            "retain relevant supporting claims "
            "context_only and attach them to relevant research concepts; uncertainty never "
            "upgrades a claim to research. Exclude only malformed or "
            "irrelevant records, generic ontology noise, and self-referential disease concepts. "
            "When uncertain, keep concepts separate. Do not introduce or discuss drugs or treatments."
        ),
        "collections": ["concepts"],
    },
    "pathology_node_research": {
        "role": "disease pathology researcher",
        "task": (
            "Research this one curated pathology concept in exceptional disease-specific "
            "depth. Explain its normal state, pathological change, causal role, mechanisms, "
            "biological context, uncertainty, contradictions, and gaps. Define one concise, "
            "evidence-grounded desired biological state that would reverse the focal pathology "
            "or compensate for an irreversible driver. Label synthesis as inference and cite the "
            "directional evidence it follows from. Retain only established pathology "
            "observations of movement toward that state; an empty list is valid. Keep discovery "
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
            "focused set of diverse existing-drug seeds whose established mode of action could "
            "produce its desired biological state. Do not pad the "
            "list. Consider both disease-modifying changes to the assigned concept and symptomatic "
            "or compensatory benefit for linked context nodes where mechanistically plausible. "
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
            "Treat the supplied frozen pathology profiles as authoritative disease context. For "
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
        "required_fields": ["document_id", "title", "source"],
        "additional_fields": True,
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
            "desired_biological_state", "established_pathology_observations", "causal_role",
            "mechanisms", "cell_types", "anatomical_context", "temporal_context",
            "upstream_causes", "downstream_consequences", "contradictions", "gaps",
            "uncertainty", "source_ids",
        ],
        "additional_fields": False,
    },
    "assertions": {
        "required_fields": [
            "assertion_id", "subject_id", "relation", "object_id",
            "evidence_summary", "source_ids",
        ],
        "additional_fields": False,
    },
    "candidates": {
        "required_fields": [
            "candidate_id", "name", "identifiers", "mechanism_hypothesis",
            "graph_node_ids", "pathology_source_ids", "mechanism_source_ids",
        ],
        "additional_fields": False,
        "field_types": {"identifiers": "object"},
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
    "mechanisms", "cell_types", "anatomical_context", "temporal_context",
    "upstream_causes", "downstream_consequences", "contradictions", "gaps", "source_ids",
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
    "pathology_curation": [
        "partition every supplied non-anchor source node exactly once across concepts",
        "concept_id is one member_node_id; choose an authoritative member ID only after same-level "
        "equivalence is established and the ID denotes the curated concept",
        "shared identifiers, genes, pathways, anatomy, or causal adjacency are not equivalence; "
        "one biological profile and desired biological state must fit every merged member",
        "same-label gene-level source claims may merge across sources; mutation-, variant-, "
        "repeat-, model-, and mechanism-specific claims remain separate",
        "merge true duplicate records into a retained concept; do not exclude their evidence",
        "concept_type is driver, mechanism, phenotype, or context",
        "disposition is research, context_only, or exclude; every decision has a concise reason",
        "each context_only concept links to at least one research concept through "
        "related_concept_ids; other dispositions use an empty list",
        "aliases and member_node_ids are JSON lists; uncertain equivalence remains separate",
    ],
    "pathology_node_research": [
        "return exactly one profile whose node_id and node_type match the supplied curated concept",
        "retain at least one independently researched document",
        f"profile fields {', '.join(PATHOLOGY_PROFILE_LIST_FIELDS)} are JSON lists",
        "desired_biological_state is one concise biological state, not a treatment, assay, "
        "control, candidate, or generic clinical improvement",
        "established_pathology_observations is a list of observation and source_ids objects; "
        "use an empty list rather than inventing an assay, threshold, or biomarker",
        "assertions link only supplied source-derived node IDs; all claims cite retained sources; "
        "no treatment content",
    ],
    "candidate_seed_research": [
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
        "non-anchor concepts used by the hypothesis; pathology_source_ids support those concepts",
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
