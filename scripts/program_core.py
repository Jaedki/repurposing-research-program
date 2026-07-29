#!/usr/bin/env python3
"""Small, content-addressed controller for a linear repurposing programme."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pathology_sources import SourceError, fetch_pathology_sources


SCHEMA_VERSION = 7
OBJECTIVE = (
    "Identify existing drugs whose established mode of action could plausibly alter a "
    "specific evidence-backed element of the supplied disease pathology. A prior "
    "disease-drug literature association is not required."
)
EXPERIMENTAL_USE_POLICY = (
    "Hypothesis generation only. Outputs are not clinical advice or proof of efficacy."
)
CANONICAL_DOCUMENT_ID = re.compile(
    r"^(?:PMID:\d+|PMCID:PMC\d+|DOI:10\.\d{4,9}/\S+|"
    r"(?:MONARCH-ASSOC|DISMECH-FILE)-[A-F0-9]{24}|"
    r"(?:ORPHA|CGGV|CLINGEN|GENCC|CLINVAR|UNIPROT(?:KB)?|HPA|"
    r"NCBI(?:-BOOKSHELF|-GENE)?|CHEMBL|PUBCHEM|DRUGBANK|DAILYMED|FDA|EMA|"
    r"WHO|ISBN|NCT):\S+|NCT\d{8}|https://\S+)$",
    re.IGNORECASE,
)
STAGES = (
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
SCORE_RUBRIC = {
    "method": (
        "Score each distinct component from its anchors, then sum the five values without "
        "weighting. The total is a prioritisation score out of 100, not a probability of efficacy."
    ),
    "zero_policy": (
        "Zero is not a scored level. Use a bounded exclusion only when the evidence establishes "
        "an exclusion condition; otherwise retain the candidate at 5 with the reservation stated."
    ),
    "components": {
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
        "evidence_robustness": {
            "label": "Evidence robustness",
            "question": "How independent, reproducible, direct, and internally consistent is the overall evidence base?",
            "anchors": {
                5: "Sparse or single-source evidence is sufficient only to keep the hypothesis testable.",
                10: "Limited or model-dominated evidence has meaningful gaps or conflicts.",
                15: "Multiple credible or orthogonal sources converge with minor limitations.",
                20: "Independent, high-quality, directly relevant evidence converges without material conflict.",
            },
        },
    },
}
SCORE_COMPONENTS = tuple(SCORE_RUBRIC["components"])
SCORE_LABELS = {
    component: definition["label"]
    for component, definition in SCORE_RUBRIC["components"].items()
}
SCORE_VALUES = frozenset({5, 10, 15, 20})
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
    "pathology_curation": {
        "role": "disease pathology concept curator",
        "task": (
            "Convert the supplied source-derived pathology nodes into coherent run-local concepts "
            "before research; do not minimize concept count. Merge only when one disease-specific "
            "biological profile and one desired biological state accurately describe every "
            "member at the same causal level. Shared genes, ontology IDs, pathways, anatomy, or "
            "causal relationships do not establish equivalence; keep bare entities, disease "
            "drivers, mechanisms, and phenotypes separate unless they express the same claim. "
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
            "sources to decide whether queued seeds are the same intervention, attach to an "
            "existing exact-UniChem candidate, remain separate, or stay unresolved/conflicting. "
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
            "read and weigh each dossier rather than restating it. Partition every candidate exactly "
            "once into a scored assessment or a cited exclusion. Exclude only established exact-"
            "disease use, exact-disease human intervention, unsupported proposed drug action, action "
            "clearly opposite to the desired state without compensation, demonstrated impossibility "
            "of relevant action or exposure, or an invalid candidate class. Unresolved identity, "
            "weak evidence, long causal distance, uncertain exposure, preclinical-only evidence, and "
            "material assumptions remain scored with explicit why-not findings. For every assessment, "
            "audit source integrity; assign one cited 5, 10, 15, or 20 score for each of drug-action "
            "confidence, disease-mechanism relevance, mechanistic-bridge plausibility, translational "
            "feasibility, and evidence robustness; and give a cited net assessment that weighs the "
            "strongest support against the strongest reservation. Python computes the raw total and "
            "ranking. Return only audited aliases and why-not findings that may enter final output."
        ),
        "collections": ["assessments", "excluded_candidates"],
    },
}

ROW_FIELDS = {
    "documents": ["document_id", "title", "source"],
    "source_nodes": ["node_id", "label", "node_type", "source_ids"],
    "source_edges": [
        "edge_id", "subject_id", "relation", "object_id", "evidence_summary", "source_ids",
    ],
    "source_receipts": ["source", "version", "query", "record_count"],
    "disease_context": ["context_id", "section", "value", "source_ids"],
    "concepts": [
        "concept_id", "preferred_label", "concept_type", "member_node_ids",
        "aliases", "disposition", "reason", "related_concept_ids",
    ],
    "profiles": [
        "node_id", "node_type", "summary", "normal_state", "pathological_state",
        "desired_biological_state", "established_pathology_observations", "causal_role",
        "mechanisms", "cell_types", "anatomical_context",
        "temporal_context", "upstream_causes", "downstream_consequences",
        "contradictions", "gaps", "uncertainty", "source_ids",
    ],
    "assertions": [
        "assertion_id", "subject_id", "relation", "object_id",
        "evidence_summary", "source_ids",
    ],
    "candidates": [
        "candidate_id", "name", "identity", "mechanism_hypothesis",
        "graph_node_ids", "pathology_source_ids", "mechanism_source_ids",
    ],
    "exclusions": ["name", "reason"],
    "identity_groups": [
        "member_seed_ids", "canonical_candidate_id", "status", "preferred_name",
        "identifiers", "reason", "source_ids",
    ],
    "reviews": [
        "candidate_id", "hypothesis", "supporting_findings", "mechanistic_bridge",
        "assumptions", "why_not", "prior_art", "aliases", "limitations",
    ],
    "assessments": [
        "candidate_id", "source_integrity", "component_scores", "net_assessment",
        "aliases", "why_not",
    ],
    "excluded_candidates": ["candidate_id", "reason_code", "finding", "source_ids"],
}

PATHOLOGY_PROFILE_LIST_FIELDS = (
    "mechanisms", "cell_types", "anatomical_context", "temporal_context",
    "upstream_causes", "downstream_consequences", "contradictions", "gaps", "source_ids",
)

FIELD_RULES = {
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
        "identity has status, preferred_name, and identifiers; include every authoritative "
        "identifier found because Python submits all supported identifiers to UniChem",
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
        "canonical_candidate_id is null for a new residual identity or an exact supplied "
        "UNICHEM:<uci> candidate_id when authoritative evidence establishes attachment",
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
        "source_integrity has exactly status, finding, and source_ids; status is supported or "
        "partly_supported",
        "component_scores has exactly drug_action_confidence, disease_mechanism_relevance, "
        "mechanistic_bridge_plausibility, translational_feasibility, and evidence_robustness",
        "each component has exactly value, reason, and source_ids; value is 5, 10, 15, or 20; "
        "Python sums the five values without weighting",
        "net_assessment has exactly text and source_ids and explicitly weighs decisive support "
        "against the strongest reservation",
        "audited aliases and why_not use cited name or finding objects and are the only review-like "
        "fields that may enter final cards",
        "excluded_candidates use a source-backed reason_code from the bounded exclusion policy",
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


class ProgramError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(_canonical_bytes(value))[:24]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ProgramError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProgramError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProgramError(f"Expected one JSON object: {path}")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProgramError(f"Immutable artifact conflicts with existing file: {path}")
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


def _write_json(path: Path, value: Any) -> None:
    _write_once(path, _canonical_bytes(value))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = b"".join(_canonical_bytes(row) for row in rows)
    _write_once(path, payload)


def _rows(records: Mapping[str, Any], name: str) -> list[dict[str, Any]]:
    value = records.get(name)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ProgramError(f"records.{name} must be a list of objects")
    return [dict(row) for row in value]


def _contract_rows(
    records: Mapping[str, Any], name: str, id_field: str | None = None
) -> list[dict[str, Any]]:
    rows = _rows(records, name)
    for index, row in enumerate(rows):
        missing = [field for field in ROW_FIELDS[name] if field not in row]
        if missing:
            raise ProgramError(f"{name}[{index}] is missing fields: {', '.join(missing)}")
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


def _forbidden_pathology_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]+", "_", str(key).casefold()).strip("_")
            if set(normalized.split("_")) & _PATHOLOGY_FORBIDDEN_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_forbidden_pathology_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_pathology_paths(item, f"{path}[{index}]"))
    return found


def _result_path(root: Path, stage: str) -> Path:
    return root / "results" / f"{stage}.json"


def _item_token(item_id: str) -> str:
    return _stable_id("ITEM", item_id)


def _item_result_path(root: Path, task: str, item_id: str) -> Path:
    return root / "results" / "items" / task / f"{_item_token(item_id)}.json"


def _packet_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "packets" / f"{task}.json"
    return root / "packets" / "items" / task / f"{_item_token(item_id)}.json"


def _submission_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "submissions" / f"{task}.json"
    return root / "submissions" / "items" / task / f"{_item_token(item_id)}.json"


def _case(root: Path) -> dict[str, Any]:
    case = _read_json(root / "case.json")
    if case.get("schema_version") != SCHEMA_VERSION or not str(case.get("disease", "")).strip():
        raise ProgramError("case.json is not a valid lean repurposing case")
    if case.get("objective") != OBJECTIVE:
        raise ProgramError("case.json does not contain the built-in repurposing objective")
    basis = {
        "disease": case["disease"],
        "gene": case.get("gene"),
        "mondo": case.get("mondo"),
        "objective": OBJECTIVE,
    }
    if case.get("case_id") != _stable_id("CASE", basis):
        raise ProgramError("case.json content no longer matches its case_id")
    return case


def initialize(
    root: str | Path,
    disease: str,
    gene: str | None = None,
    mondo: str | None = None,
) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    disease = disease.strip()
    gene = gene.strip() if gene else None
    mondo = mondo.strip().upper() if mondo else None
    if not disease:
        raise ProgramError("--disease is required")
    case_path = run_root / "case.json"
    if run_root.exists() and not case_path.exists() and any(run_root.iterdir()):
        raise ProgramError(
            "Run folder is not empty and does not contain this programme's case.json"
        )
    if case_path.exists():
        existing = _case(run_root)
        if (
            existing["disease"] != disease
            or existing.get("gene") != gene
            or existing.get("mondo") != mondo
        ):
            raise ProgramError("Existing run case conflicts with the supplied disease, gene, or MONDO ID")
        return _program_status(run_root, existing, _load_results(run_root))
    run_root.mkdir(parents=True, exist_ok=True)
    case_basis = {"disease": disease, "gene": gene, "mondo": mondo, "objective": OBJECTIVE}
    case = {
        "schema_version": SCHEMA_VERSION,
        "case_id": _stable_id("CASE", case_basis),
        "disease": disease,
        "gene": gene,
        "mondo": mondo,
        "objective": OBJECTIVE,
        "created_at": datetime.now(timezone.utc).isoformat(),

        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_json(case_path, case)
    for name in ("packets", "results", "outputs"):
        (run_root / name).mkdir(exist_ok=True)
    return _program_status(run_root, case, {})


def _load_results(root: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    missing_seen = False
    for stage in STAGES:
        path = _result_path(root, stage)
        if not path.exists():
            missing_seen = True
            continue
        if missing_seen:
            raise ProgramError(f"Result exists out of stage order: {path}")
        results[stage] = _read_json(path)
    return results


def _item_ids(stage: str, results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    field = "concept_id"
    if stage in {"evidence_graph", "candidate_seed_generation"}:
        rows = _research_concepts(results)
    elif stage == "candidate_review":
        rows = _review_batches(results)
    else:
        return []
    return sorted(str(row[field]) for row in rows)


def _item_results(
    root: Path, task: str, item_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    return {
        item_id: _read_json(path)
        for item_id in item_ids
        if (path := _item_result_path(root, task, item_id)).exists()
    }


def _first_missing(root: Path, task: str, item_ids: list[str]) -> tuple[str | None, int]:
    accepted = _item_results(root, task, item_ids)
    return next((item_id for item_id in item_ids if item_id not in accepted), None), len(accepted)


def _stop_reason(results: Mapping[str, Mapping[str, Any]]) -> str | None:
    curation = results.get("pathology_curation")
    if curation is not None and not any(
        row.get("disposition") == "research"
        for row in curation.get("records", {}).get("concepts", [])
        if isinstance(row, dict)
    ):
        return "pathology curation retained no concepts requiring deep research"
    checks = (
        ("pathology_sources", "source_nodes", "Monarch and DisMech returned no pathology nodes"),
        ("evidence_graph", "profiles", "no source-backed pathology profiles were produced"),
        ("candidate_seed_generation", "candidates", "no mechanism-linked drug seeds were produced"),
        ("candidate_review", "reviews", "no candidates received an evidence review"),
    )
    for stage, collection, reason in checks:
        result = results.get(stage)
        if result is not None and not result.get("records", {}).get(collection):
            return reason
    audit = results.get("candidate_audit")
    if audit is not None and not audit.get("records", {}).get("assessments"):
        return "the audit excluded every reviewed candidate"
    return None


def _verify_outputs(root: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "complete" or not manifest.get("candidate_count"):
        raise ProgramError("Output manifest is not complete")
    if manifest.get("case_sha256") != _sha256((root / "case.json").read_bytes()):
        raise ProgramError("case.json changed after outputs were built")
    stage_results = manifest.get("stage_results")
    if not isinstance(stage_results, dict) or set(stage_results) != set(STAGES):
        raise ProgramError("Output manifest does not cover every stage result")
    for stage, expected in stage_results.items():
        if _sha256(_result_path(root, stage).read_bytes()) != expected:
            raise ProgramError(f"Accepted result changed after outputs were built: {stage}")
    for artifact in manifest.get("artifacts", []):
        path = root / "outputs" / str(artifact.get("filename", ""))
        if not path.is_file() or _sha256(path.read_bytes()) != artifact.get("sha256"):
            raise ProgramError(f"Output artifact is missing or changed: {path}")


def _program_status(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    stop = _stop_reason(results)
    manifest_path = run_root / "outputs" / "manifest.json"
    next_task = next_item_id = None
    accepted_items = 0
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        _verify_outputs(run_root, manifest)
        state = "complete"
        next_stage = None
    elif stop:
        state = "stopped"
        next_stage = None
    elif len(results) == len(STAGES):
        state = "ready_to_build"
        next_stage = None
    else:
        next_stage = STAGES[len(results)]
        if next_stage == "pathology_sources":
            state, next_task = "needs_controller", next_stage
        elif next_stage in {"evidence_graph", "candidate_seed_generation", "candidate_review"}:
            next_task = {
                "evidence_graph": "pathology_node_research",
                "candidate_seed_generation": "candidate_seed_research",
                "candidate_review": "candidate_evidence_review",
            }[next_stage]
            next_item_id, accepted_items = _first_missing(
                run_root, next_task, _item_ids(next_stage, results)
            )
            state = "needs_agent" if next_item_id is not None else "needs_controller"
        elif next_stage == "candidate_identity":
            next_task = "candidate_identity"
            queue = _identity_queue(results["candidate_seed_generation"]["records"])
            state = "needs_agent" if queue else "needs_controller"
        else:
            state, next_task = "needs_agent", next_stage
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "state": state,
        "next_stage": next_stage,
        "next_task": next_task,
        "next_item_id": next_item_id,
        "accepted_items": accepted_items,
        "accepted_stages": list(results),
        "stop_reason": stop,
    }


def status(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    return _program_status(run_root, _case(run_root), _load_results(run_root))


def _source_index(
    documents: list[dict[str, Any]], source_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    fields = ("document_id", "title", "source", "citation", "url", "raw_path", "abstract")
    return [
        {key: row[key] for key in fields if key in row}
        for row in documents
        if source_ids is None or str(row["document_id"]) in source_ids
    ]


def _merge_unique(
    rows: Iterable[dict[str, Any]], id_field: str, label: str
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(id_field, "")).strip()
        if not key:
            raise ProgramError(f"{label}.{id_field} is required")
        if key in merged and merged[key] != row:
            raise ProgramError(f"Conflicting {label} records share {id_field}={key}")
        merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _merge_text(*values: Any) -> str:
    parts = {
        part.strip()
        for value in values
        for part in str(value).split(" | ")
        if part.strip()
    }
    return " | ".join(sorted(parts))


def _merge_documents(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        document_id = str(row.get("document_id", "")).strip()
        if not document_id:
            raise ProgramError("documents.document_id is required")
        current = merged.setdefault(document_id, {"document_id": document_id})
        for field, value in row.items():
            if field == "document_id" or value in (None, "", []):
                continue
            if isinstance(value, list):
                prior = current.get(field, [])
                if not isinstance(prior, list):
                    raise ProgramError(f"documents.{field} changes type")
                values = {_canonical_bytes(item): item for item in [*prior, *value]}
                value = [values[key] for key in sorted(values)]
            current[field] = value
    return [merged[key] for key in sorted(merged)]


def _merge_assertions(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        assertion_id = str(row.get("assertion_id", "")).strip()
        if not assertion_id:
            raise ProgramError("assertions.assertion_id is required")
        current = merged.get(assertion_id)
        if current is None:
            merged[assertion_id] = {**row, "assertion_id": assertion_id}
            continue
        if any(
            current[field] != row[field]
            for field in ("subject_id", "relation", "object_id")
        ):
            raise ProgramError(
                f"Conflicting assertion identities share assertion_id={assertion_id}"
            )
        current["source_ids"] = sorted({
            *map(str, current["source_ids"]),
            *map(str, row["source_ids"]),
        })
        current["evidence_summary"] = _merge_text(
            current["evidence_summary"], row["evidence_summary"]
        )
    return [merged[key] for key in sorted(merged)]


def _all_documents(results: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _merge_documents(
        (
            row
            for result in results.values()
            if isinstance(result.get("records"), Mapping)
            for row in _cited_documents(result["records"])
        )
    )


def _find(rows: Iterable[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get(field)) == value]
    if len(matches) != 1:
        raise ProgramError(f"Expected exactly one {field}={value} record")
    return matches[0]


def _cited_ids(value: Any) -> set[str]:
    """Collect citations recursively without treating document metadata as evidence."""
    cited: set[str] = set()

    def visit(current: Any) -> None:
        if isinstance(current, Mapping):
            for field, nested in current.items():
                if field == "documents":
                    continue
                if field in _CITATION_FIELDS:
                    if isinstance(nested, list):
                        cited.update(str(item) for item in nested)
                    continue
                visit(nested)
        elif isinstance(current, list):
            for item in current:
                visit(item)

    visit(value)
    return cited


def _select_cited_documents(
    documents: Iterable[dict[str, Any]], citations: Any
) -> list[dict[str, Any]]:
    cited_ids = _cited_ids(citations)
    return [
        dict(row)
        for row in documents
        if str(row.get("document_id", "")) in cited_ids
    ]


def _cited_documents(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents = records.get("documents", [])
    if not isinstance(documents, list) or any(not isinstance(row, dict) for row in documents):
        raise ProgramError("records.documents must be a list of objects")
    return _select_cited_documents(documents, records)


def _curation_concepts(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = results.get("pathology_curation")
    if not isinstance(result, Mapping) or not isinstance(result.get("records"), Mapping):
        raise ProgramError("Pathology curation result is missing")
    return _contract_rows(result["records"], "concepts", "concept_id")


def _research_concepts(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        (
            row
            for row in _curation_concepts(results)
            if row.get("disposition") == "research"
        ),
        key=lambda row: str(row["concept_id"]),
    )


def _canonical_source_records(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project raw source records through the accepted run-local concept partition."""
    source = results["pathology_sources"]["records"]
    raw_nodes = _rows(source, "source_nodes")
    raw_by_id = {str(row["node_id"]): row for row in raw_nodes}
    raw_edges = _rows(source, "source_edges")
    concepts = _curation_concepts(results)

    node_map: dict[str, str] = {}
    nodes: list[dict[str, Any]] = []
    for raw in raw_nodes:
        if raw.get("node_type") != "disease_anchor":
            continue
        node_id = str(raw["node_id"])
        node_map[node_id] = node_id
        nodes.append(
            {
                "node_id": node_id,
                "label": str(raw["label"]),
                "node_type": "disease_anchor",
                "source_ids": sorted(set(map(str, raw["source_ids"]))),
                "aliases": [],
                "member_node_ids": [node_id],
                "disposition": "context_only",
            }
        )

    for concept in concepts:
        if concept["disposition"] == "exclude":
            continue
        concept_id = str(concept["concept_id"])
        members = sorted(set(map(str, concept["member_node_ids"])))
        for member in members:
            node_map[member] = concept_id
        source_ids = sorted(
            {
                str(source_id)
                for member in members
                for source_id in raw_by_id[member]["source_ids"]
            }
        )
        aliases = sorted(
            {
                str(value).strip()
                for value in [
                    *concept["aliases"],
                    *(raw_by_id[member].get("label", "") for member in members),
                ]
                if str(value).strip()
                and str(value).strip().casefold()
                != str(concept["preferred_label"]).strip().casefold()
            },
            key=str.casefold,
        )
        nodes.append(
            {
                "node_id": concept_id,
                "label": str(concept["preferred_label"]),
                "node_type": str(concept["concept_type"]),
                "source_ids": source_ids,
                "aliases": aliases,
                "member_node_ids": members,
                "disposition": str(concept["disposition"]),
            }
        )

    grouped_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in raw_edges:
        subject = node_map.get(str(edge["subject_id"]))
        object_id = node_map.get(str(edge["object_id"]))
        if not subject or not object_id or subject == object_id:
            continue
        relation = str(edge["relation"])
        key = (subject, relation, object_id)
        current = grouped_edges.setdefault(
            key,
            {
                "edge_id": _stable_id(
                    "CURATED-EDGE",
                    {"subject_id": subject, "relation": relation, "object_id": object_id},
                ),
                "subject_id": subject,
                "relation": relation,
                "object_id": object_id,
                "evidence_summary": "",
                "source_ids": [],
                "original_edge_ids": [],
            },
        )
        current["evidence_summary"] = _merge_text(
            current["evidence_summary"], edge["evidence_summary"]
        )
        current["source_ids"] = sorted(
            {*map(str, current["source_ids"]), *map(str, edge["source_ids"])}
        )
        current["original_edge_ids"] = sorted(
            {*map(str, current["original_edge_ids"]), str(edge["edge_id"])}
        )

    node_by_id = {str(row["node_id"]): row for row in nodes}
    for concept in concepts:
        if concept["disposition"] != "context_only":
            continue
        subject = str(concept["concept_id"])
        for object_id in map(str, concept["related_concept_ids"]):
            relation = "contextualizes"
            key = (subject, relation, object_id)
            current = grouped_edges.setdefault(
                key,
                {
                    "edge_id": _stable_id(
                        "CURATED-EDGE",
                        {
                            "subject_id": subject,
                            "relation": relation,
                            "object_id": object_id,
                        },
                    ),
                    "subject_id": subject,
                    "relation": relation,
                    "object_id": object_id,
                    "evidence_summary": "",
                    "source_ids": [],
                    "original_edge_ids": [],
                },
            )
            current["evidence_summary"] = _merge_text(
                current["evidence_summary"], concept["reason"]
            )
            current["source_ids"] = sorted(
                {
                    *map(str, current["source_ids"]),
                    *map(str, node_by_id[subject]["source_ids"]),
                }
            )

    return (
        sorted(nodes, key=lambda row: str(row["node_id"])),
        sorted(grouped_edges.values(), key=lambda row: str(row["edge_id"])),
    )


def _graph_index(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in GRAPH_INDEX_FIELDS}
        for row in sorted(
            _rows(records, "source_nodes"), key=lambda value: str(value["node_id"])
        )
        if row.get("node_type") != "disease_anchor"
    ]


def _graph_support_ids(records: Mapping[str, Any]) -> dict[str, set[str]]:
    support = {
        str(row["node_id"]): set(map(str, row["source_ids"]))
        for row in _rows(records, "source_nodes")
        if row.get("node_type") != "disease_anchor"
    }
    for row in _rows(records, "profiles"):
        node_id = str(row["node_id"])
        if node_id in support:
            support[node_id].update(map(str, row["source_ids"]))
    for row in [*_rows(records, "source_edges"), *_rows(records, "assertions")]:
        source_ids = set(map(str, row["source_ids"]))
        for node_id in map(str, (row["subject_id"], row["object_id"])):
            if node_id in support:
                support[node_id].update(source_ids)
    return support


def _graph_node_context(records: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    nodes = _rows(records, "source_nodes")
    node_by_id = {str(row["node_id"]): row for row in nodes}
    node = _find(nodes, "node_id", node_id)
    if node.get("node_type") == "disease_anchor":
        raise ProgramError("The disease anchor is not a retrievable pathology concept")
    profiles = [
        row for row in _rows(records, "profiles") if str(row["node_id"]) == node_id
    ]
    if len(profiles) > 1:
        raise ProgramError(f"Multiple pathology profiles exist for node_id={node_id}")
    source_edges = [
        row
        for row in _rows(records, "source_edges")
        if node_id in {str(row["subject_id"]), str(row["object_id"])}
    ]
    assertions = [
        row
        for row in _rows(records, "assertions")
        if node_id in {str(row["subject_id"]), str(row["object_id"])}
    ]
    related_ids = {
        str(value)
        for edge in [*source_edges, *assertions]
        for value in (edge["subject_id"], edge["object_id"])
        if str(value) != node_id
    }
    return {
        "node": node,
        "profile": profiles[0] if profiles else None,
        "source_edges": sorted(source_edges, key=lambda row: str(row["edge_id"])),
        "assertions": sorted(assertions, key=lambda row: str(row["assertion_id"])),
        "related_nodes": [
            {key: node_by_id[value][key] for key in GRAPH_INDEX_FIELDS}
            for value in sorted(related_ids)
            if value in node_by_id
            and node_by_id[value].get("node_type") != "disease_anchor"
        ],
    }


def graph_context(root: str | Path, node_id: str) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    graph = results.get("evidence_graph")
    if graph is None:
        raise ProgramError("Graph context is unavailable before the evidence graph is frozen")
    records = graph.get("records")
    if not isinstance(records, dict) or graph.get("snapshot_id") != _stable_id("GRAPH", records):
        raise ProgramError("Evidence graph snapshot verification failed")
    node_id = node_id.strip()
    if not node_id:
        raise ProgramError("node_id is required")
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "graph_snapshot_id": graph["snapshot_id"],
        "context": _graph_node_context(records, node_id),
    }


def _packet_context(
    run_root: Path,
    task: str,
    item_id: str | None,
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if task == "pathology_curation":
        documents = _all_documents(results)
        source_result = results["pathology_sources"]
        source = source_result["records"]
        nodes = sorted(
            (
                row
                for row in _rows(source, "source_nodes")
                if row.get("node_type") != "disease_anchor"
            ),
            key=lambda row: (
                str(row.get("node_type", "")).casefold(),
                str(row.get("label", "")).casefold(),
                str(row.get("node_id", "")),
            ),
        )
        edges = _rows(source, "source_edges")
        disease_context = _rows(source, "disease_context")
        return {
            "resolved_disease": source_result.get("resolved_disease"),
            "source_nodes": nodes,
            "source_edges": edges,
            "disease_context": disease_context,
            "source_index": _source_index(
                documents, _cited_ids([*nodes, *edges, *disease_context])
            ),
            "source_receipts": _rows(source, "source_receipts"),
            "upstream_gaps": source_result.get("gaps", []),
        }
    if task == "pathology_node_research":
        documents = _all_documents(results)
        source = results["pathology_sources"]["records"]
        concept = _find(_research_concepts(results), "concept_id", str(item_id))
        canonical_nodes, canonical_edges = _canonical_source_records(results)
        node = _find(canonical_nodes, "node_id", str(item_id))
        member_ids = set(map(str, concept["member_node_ids"]))
        member_nodes = [
            {
                key: row[key]
                for key in ("node_id", "label", "node_type", "description", "source_ids", "source_section")
                if key in row
            }
            for row in _rows(source, "source_nodes")
            if str(row["node_id"]) in member_ids
        ]
        edges = [
            row
            for row in canonical_edges
            if str(item_id) in {str(row["subject_id"]), str(row["object_id"])}
        ]
        related_ids = {
            str(value)
            for edge in edges
            for value in (edge["subject_id"], edge["object_id"])
            if str(value) != str(item_id)
        }
        related_nodes = [
            row for row in canonical_nodes if str(row["node_id"]) in related_ids
        ]
        disease_context = [
            row
            for row in _rows(source, "disease_context")
            if row["section"] in _RESEARCH_CONTEXT_SECTIONS
        ]
        return {
            "concept": concept,
            "node": node,
            "member_source_nodes": member_nodes,
            "related_nodes": related_nodes,
            "adjacent_edges": edges,
            "disease_context": disease_context,
            "source_index": _source_index(
                documents,
                _cited_ids(
                    [node, *member_nodes, *related_nodes, *edges, *disease_context]
                ),
            ),
            "source_receipts": _rows(source, "source_receipts"),
            "upstream_gaps": results["pathology_sources"].get("gaps", []),
        }
    graph_result = results["evidence_graph"]
    graph = graph_result["records"]
    if task == "candidate_seed_research":
        return {
            "graph_snapshot_id": graph_result["snapshot_id"],
            "focal_context": _graph_node_context(graph, str(item_id)),
            "graph_index": _graph_index(graph),
            "context_lookup": {
                "argv": [
                    "python",
                    str(Path(__file__).with_name("orchestrate_program.py").resolve()),
                    "graph-context",
                    str(run_root),
                    "<node_id>",
                ]
            },
        }
    seeds = results["candidate_seed_generation"]["records"]
    if task == "candidate_identity":
        queued = _identity_queue(seeds)
        return {
            "identity_queue": queued,
            "resolved_candidates": _canonical_candidates(results, reviewed=False),
        }
    if task == "candidate_evidence_review":
        documents = _all_documents(results)
        batch = _find(_review_batches(results), "concept_id", str(item_id))
        candidate_ids = set(map(str, batch["candidate_ids"]))
        candidates = [
            row
            for row in _canonical_candidates(results)
            if str(row["candidate_id"]) in candidate_ids
        ]
        node_ids = {
            str(node_id)
            for candidate in candidates
            for node_id in candidate["graph_node_ids"]
        }
        concepts = [
            row
            for row in _rows(graph, "source_nodes")
            if str(row["node_id"]) in node_ids
        ]
        profiles = [
            row for row in _rows(graph, "profiles") if str(row["node_id"]) in node_ids
        ]
        review_source_ids = {
            str(source_id)
            for candidate in candidates
            for source_id in (
                *candidate["mechanism_source_ids"],
                *candidate.get("identity", {}).get("source_ids", []),
            )
        }
        return {
            "primary_concept_id": str(item_id),
            "candidates": candidates,
            "pathology_concepts": concepts,
            "pathology_profiles": profiles,
            "source_index": _source_index(documents, review_source_ids),
        }
    documents = _all_documents(results)
    return {
        "candidates": _canonical_candidates(results),
        "reviews": _rows(results["candidate_review"]["records"], "reviews"),
        "source_index": _source_index(documents),
    }


def _build_packet(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    task: str,
    item_id: str | None = None,
) -> dict[str, Any]:
    upstream = [
        {
            "stage": name,
            "path": str(_result_path(run_root, name)),
            "sha256": _sha256(_result_path(run_root, name).read_bytes()),
        }
        for name in results
    ]
    guidance = STAGE_GUIDANCE[task]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "stage": task,
        "item_id": item_id,
        "role": guidance["role"],
        "objective": OBJECTIVE,
        "task": guidance["task"],
        "case": case,
        "upstream": upstream,
        "context": _packet_context(run_root, task, item_id, results),
        "result_contract": {
            "stage": task,
            "item_id": item_id,
            "packet_id": "copy from this packet",
            "status": "complete",
            "records": {
                name: {"type": "list of objects", "required_fields": ROW_FIELDS[name]}
                for name in guidance["collections"]
            },
            "field_rules": FIELD_RULES[task],
            **(
                {
                    "score_rubric": SCORE_RUBRIC,
                    "exclusion_policy": AUDIT_EXCLUSION_POLICY,
                }
                if task == "candidate_audit"
                else {}
            ),
            "gaps": "list of explicit limitations or unresolved questions",
            "notes": "optional list of concise notes",
        },
        "rules": [
            "Use only supplied or newly retrieved named sources; never invent citations.",
            "Use PMID:<digits>, PMCID:PMC<digits>, DOI:<doi>, recognized accession:<id>, or "
            "HTTPS URL document IDs; never invent DOC aliases.",
            *(
                [
                    "Search and read freely, but return only documents that directly support a "
                    "submitted claim, counterclaim, identity decision, or limitation.",
                    "Every returned document_id must be cited in this result through source_ids, "
                    "pathology_source_ids, or mechanism_source_ids; cited upstream documents do "
                    "not need to be returned again.",
                ]
                if "documents" in guidance["collections"]
                else []
            ),
            "Preserve contradictions, negative results, unresolved identity, and source gaps.",
            "Return JSON only and do not include credentials or API keys.",
        ],
    }
    packet = {**unsigned, "packet_id": _stable_id("PACKET", unsigned)}
    _write_json(_packet_path(run_root, task, item_id), packet)
    return packet


def next_action(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case = _case(run_root)
    for _ in range(len(STAGES) + 1):
        results = _load_results(run_root)
        current = _program_status(run_root, case, results)
        if current["state"] != "needs_controller":
            break
        _advance_controller(run_root, case, results, str(current["next_stage"]))
    else:
        raise ProgramError("Controller could not reach an agent or terminal state")
    if current["state"] != "needs_agent":
        return current
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, results, task, item_id)
    packet_path = _packet_path(run_root, task, item_id)
    result_path = _submission_path(run_root, task, item_id)
    return {
        **current,
        "packet_id": packet["packet_id"],
        "packet_path": str(packet_path),
        "suggested_result_path": str(result_path),
        "worker_prompt": (
            f"Read only the content packet at {packet_path} and any controller-returned graph "
            f"context explicitly authorized by that packet. Complete the {task} task and write "
            f"one JSON object matching result_contract to {result_path}. Use this exact header: "
            f"stage={json.dumps(task)}, item_id={json.dumps(item_id)}, "
            f"packet_id={json.dumps(packet['packet_id'])}, status=\"complete\". "
            "Return the result path to the controller."
        ),
    }


def _item_collection(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
    collection: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _rows(accepted[item_id]["records"], collection)
    ]


def _item_cited_documents(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _cited_documents(accepted[item_id]["records"])
    ]


def _item_gaps(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[Any]:
    accepted = _item_results(root, task, _item_ids(stage, results))
    return [gap for result in accepted.values() for gap in result.get("gaps", [])]


def _build_graph_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    source = results["pathology_sources"]["records"]
    canonical_nodes, canonical_edges = _canonical_source_records(results)
    records = {
        "source_nodes": canonical_nodes,
        "source_edges": canonical_edges,
        "source_receipts": _rows(source, "source_receipts"),
        "disease_context": _rows(source, "disease_context"),
        "profiles": _merge_unique(
            _item_collection(
                root, results, "evidence_graph", "pathology_node_research", "profiles"
            ),
            "node_id",
            "profiles",
        ),
        "assertions": _merge_assertions(
            _item_collection(
                root, results, "evidence_graph", "pathology_node_research", "assertions"
            )
        ),
    }
    records["documents"] = _select_cited_documents(
        _merge_documents(
            [
                *_cited_documents(source),
                *_item_cited_documents(
                    root, results, "evidence_graph", "pathology_node_research"
                ),
            ]
        ),
        records,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "evidence_graph",
        "status": "complete",
        "snapshot_id": _stable_id("GRAPH", records),
        "records": records,
        "gaps": _item_gaps(root, results, "evidence_graph", "pathology_node_research"),
        "notes": [
            "Frozen pathology-only graph built from the accepted run-local concept partition."
        ],
    }


def _review_batches(
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    concept_ids = {str(row["concept_id"]) for row in _research_concepts(results)}
    grouped: dict[str, list[str]] = {concept_id: [] for concept_id in concept_ids}
    candidates = _canonical_candidates(results)
    candidate_ids = _ids(candidates, "candidate_id", "candidates")
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        origin_ids = sorted(set(map(str, candidate.get("origin_concept_ids", []))))
        unknown = set(origin_ids) - concept_ids
        if not origin_ids or unknown:
            raise ProgramError(
                f"Candidate {candidate_id} has invalid origin_concept_ids: {sorted(unknown)}"
            )
        node_ids = set(map(str, candidate.get("graph_node_ids", [])))
        primary = min(origin_ids, key=lambda value: (value not in node_ids, value))
        if primary not in node_ids:
            raise ProgramError(f"Candidate {candidate_id} has no node in an origin concept")
        grouped[primary].append(candidate_id)

    batches = [
        {"concept_id": concept_id, "candidate_ids": sorted(ids)}
        for concept_id, ids in sorted(grouped.items())
        if ids
    ]
    assigned = [candidate_id for batch in batches for candidate_id in batch["candidate_ids"]]
    if len(assigned) != len(set(assigned)) or set(assigned) != candidate_ids:
        raise ProgramError("Review batches must partition every candidate exactly once")
    return batches


def _candidate_queries(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    queries: set[tuple[str, int | None, str]] = set()
    identity = row.get("identity") if isinstance(row.get("identity"), Mapping) else {}
    identifiers = identity.get("identifiers", {})
    if isinstance(identifiers, Mapping):
        for key, raw_value in identifiers.items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                compound = str(value).strip()
                if key in {"inchi", "inchikey"} and compound:
                    queries.add((key, None, compound))
                elif key in _UNICHEM_SOURCE_IDS:
                    if compound:
                        queries.add(("sourceID", _UNICHEM_SOURCE_IDS[key], compound))
    return [
        {
            "compound": compound,
            "type": query_type,
            **({"sourceID": source_id} if source_id is not None else {}),
        }
        for query_type, source_id, compound in sorted(queries, key=str)
    ]


def _post_unichem(endpoint: str, body: Mapping[str, Any]) -> dict[str, Any]:
    payload = _canonical_bytes(body)
    request = Request(
        f"{_UNICHEM_API}/{endpoint}",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "repurposing-research-program/4",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramError(f"UniChem {endpoint} returned invalid JSON: {exc}") from exc
        else:
            if not isinstance(result, dict) or result.get("response") != "Success":
                raise ProgramError(f"UniChem {endpoint} returned an invalid response")
            return result
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _unichem_request(
    root: Path, endpoint: str, body: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    token = _sha256(_canonical_bytes(body))[:24]
    path = root / "sources" / "raw" / "unichem" / f"{endpoint}-{token}.json"
    if path.exists():
        response = _read_json(path)
    else:
        response = _post_unichem(endpoint, body)
        _write_json(path, response)
    return response, {
        "source": "UniChem",
        "api": _UNICHEM_API,
        "endpoint": endpoint,
        "query": dict(body),
        "raw_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path.read_bytes()),
    }


def _unichem_requests(
    root: Path, endpoint: str, bodies: Iterable[Mapping[str, Any]]
) -> dict[bytes, tuple[dict[str, Any], dict[str, Any]]]:
    unique = {_canonical_bytes(body): dict(body) for body in bodies}
    return {
        key: _unichem_request(root, endpoint, body) for key, body in unique.items()
    }


def _query_key(query: Mapping[str, Any]) -> tuple[int, str] | None:
    if query.get("type") != "sourceID":
        return None
    return int(query["sourceID"]), str(query["compound"]).casefold()


def _resolve_seed_identities(
    root: Path, candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queries_by_seed = {
        str(row["seed_id"]): _candidate_queries(row) for row in candidates
    }
    exact = _unichem_requests(
        root, "compounds", (query for queries in queries_by_seed.values() for query in queries)
    )
    receipts = [value[1] for _, value in sorted(exact.items())]
    preliminary: dict[str, dict[str, Any]] = {}
    query_seeds: dict[tuple[int, str], set[str]] = {}
    for seed_id, queries in queries_by_seed.items():
        found: list[dict[str, Any]] = []
        missed = False
        for query in queries:
            response = exact[_canonical_bytes(query)][0]
            compounds = [row for row in response.get("compounds", []) if isinstance(row, dict)]
            found.extend(compounds)
            missed = missed or not compounds
            key = _query_key(query)
            if key:
                query_seeds.setdefault(key, set()).add(seed_id)
        ucis = {str(row.get("uci")) for row in found if row.get("uci") is not None}
        if not queries:
            preliminary[seed_id] = {"status": "not_queryable", "queries": []}
        elif not ucis:
            preliminary[seed_id] = {"status": "no_result", "queries": queries}
        elif len(ucis) != 1 or missed:
            preliminary[seed_id] = {
                "status": "conflicting_or_partial_result",
                "queries": queries,
                "ucis": sorted(ucis),
            }
        else:
            uci = next(iter(ucis))
            compound = next(row for row in found if str(row.get("uci")) == uci)
            preliminary[seed_id] = {
                "status": "exact",
                "queries": queries,
                "uci": uci,
                "standard_inchikey": compound.get("standardInchiKey"),
            }

    exact_seeds = {
        seed_id: row for seed_id, row in preliminary.items() if row["status"] == "exact"
    }
    connectivity_bodies = [
        {"compound": uci, "type": "uci", "searchComponents": True}
        for uci in sorted({row["uci"] for row in exact_seeds.values()})
    ]
    connectivity = _unichem_requests(root, "connectivity", connectivity_bodies)
    receipts.extend(value[1] for _, value in sorted(connectivity.items()))
    related: dict[str, set[str]] = {seed_id: set() for seed_id in exact_seeds}
    for body in connectivity_bodies:
        uci = str(body["compound"])
        response = connectivity[_canonical_bytes(body)][0]
        own = {seed_id for seed_id, row in exact_seeds.items() if row["uci"] == uci}
        for source in response.get("sources", []):
            key = (int(source.get("id", -1)), str(source.get("compoundId", "")).casefold())
            for other in query_seeds.get(key, set()) - own:
                if other in exact_seeds and exact_seeds[other]["uci"] != uci:
                    for seed_id in own:
                        related[seed_id].add(other)
                        related[other].add(seed_id)
    by_connectivity: dict[str, set[str]] = {}
    for seed_id, row in exact_seeds.items():
        inchikey = str(row.get("standard_inchikey") or "")
        if len(inchikey) >= 14:
            by_connectivity.setdefault(inchikey[:14], set()).add(seed_id)
    for seed_ids in by_connectivity.values():
        ucis = {exact_seeds[seed_id]["uci"] for seed_id in seed_ids}
        if len(ucis) > 1:
            for seed_id in seed_ids:
                related[seed_id].update(seed_ids - {seed_id})

    for seed_id, seed_related in related.items():
        if seed_related:
            preliminary[seed_id]["status"] = "connectivity_match"
            preliminary[seed_id]["related_seed_ids"] = sorted(seed_related)
    enriched = [
        {**row, "identity_resolution": preliminary[str(row["seed_id"])]}
        for row in candidates
    ]
    return enriched, sorted(receipts, key=lambda row: row["raw_path"])


def _identity_queue(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(records, "candidates")
        if row.get("identity_resolution", {}).get("status") != "exact"
    ]


def _exact_identity_groups(records: Mapping[str, Any]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for seed in _rows(records, "candidates"):
        resolution = seed.get("identity_resolution", {})
        if resolution.get("uci") is None:
            continue
        candidate_id = f"UNICHEM:{resolution['uci']}"
        groups.setdefault(candidate_id, []).append(str(seed["seed_id"]))
    return {candidate_id: sorted(groups[candidate_id]) for candidate_id in sorted(groups)}


def _merge_candidate_rows(
    rows: list[dict[str, Any]], candidate_id: str, identity: Mapping[str, Any]
) -> dict[str, Any]:
    rows = sorted(
        {str(row["seed_id"]): row for row in rows}.values(),
        key=lambda row: str(row["seed_id"]),
    )
    return {
        "candidate_id": candidate_id,
        "name": str(identity["preferred_name"]),
        "identity": dict(identity),
        "mechanism_hypothesis": _merge_text(*(row["mechanism_hypothesis"] for row in rows)),
        "graph_node_ids": sorted({str(value) for row in rows for value in row["graph_node_ids"]}),
        "pathology_source_ids": sorted({
            str(value) for row in rows for value in row["pathology_source_ids"]
        }),
        "mechanism_source_ids": sorted({
            str(value) for row in rows for value in row["mechanism_source_ids"]
        }),
        "origin_concept_ids": sorted({
            str(value) for row in rows for value in row["origin_concept_ids"]
        }),
        "member_seed_ids": [str(row["seed_id"]) for row in rows],
        "asserted_candidate_ids": sorted({str(row["candidate_id"]) for row in rows}),
    }


def _canonical_candidates(
    results: Mapping[str, Mapping[str, Any]],
    *,
    reviewed: bool = True,
) -> list[dict[str, Any]]:
    seed_records = results["candidate_seed_generation"]["records"]
    seeds = {str(row["seed_id"]): row for row in _rows(seed_records, "candidates")}
    queued = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    exact_groups = _exact_identity_groups(seed_records)
    candidates: dict[str, dict[str, Any]] = {}
    for candidate_id, member_ids in exact_groups.items():
        member_ids = set(member_ids)
        if member_ids & queued:
            continue
        rows = [seeds[seed_id] for seed_id in sorted(member_ids)]
        preferred_name = min(
            (str(row["identity"]["preferred_name"]) for row in rows),
            key=lambda value: (value.casefold(), value),
        )
        identity = {
            "status": "resolved",
            "preferred_name": preferred_name,
            "identifiers": {"unichem_uci": candidate_id.split(":", 1)[1]},
        }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    if not reviewed:
        return [candidates[key] for key in sorted(candidates)]

    identity_result = results.get("candidate_identity", {"records": {"identity_groups": []}})
    for group in _rows(identity_result["records"], "identity_groups"):
        rows = [seeds[str(seed_id)] for seed_id in group["member_seed_ids"]]
        target = group.get("canonical_candidate_id")
        if target:
            exact = exact_groups[str(target)]
            rows.extend(seeds[seed_id] for seed_id in exact)
            preferred_name = min(
                (str(row["identity"]["preferred_name"]) for row in rows),
                key=lambda value: (value.casefold(), value),
            )
            identity = {
                "status": "resolved",
                "preferred_name": preferred_name,
                "identifiers": {"unichem_uci": str(target).split(":", 1)[1]},
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
            candidate_id = str(target)
        else:
            candidate_id = _stable_id("CANDIDATE", sorted(map(str, group["member_seed_ids"])))
            identity = {
                "status": group["status"],
                "preferred_name": group["preferred_name"],
                "identifiers": group["identifiers"],
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    return [candidates[key] for key in sorted(candidates)]


def _build_seed_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    item_ids = _item_ids("candidate_seed_generation", results)
    accepted = _item_results(root, "candidate_seed_research", item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError("Cannot aggregate seeds before every researched concept is accepted")
    raw_candidates = []
    for item_id in item_ids:
        for row in _rows(accepted[item_id]["records"], "candidates"):
            seed_id = _stable_id(
                "SEED", {"origin_concept_id": item_id, "candidate_id": row["candidate_id"]}
            )
            raw_candidates.append({
                **row,
                "seed_id": seed_id,
                "origin_concept_ids": [item_id],
            })
    candidates, receipts = _resolve_seed_identities(root, raw_candidates)
    queued_count = sum(
        row["identity_resolution"]["status"] != "exact" for row in candidates
    )
    upstream_document_ids = {
        str(row["document_id"]) for row in _all_documents(results)
    }
    records = {
        "candidates": candidates,
        "identity_receipts": receipts,
        "exclusions": [
            {**row, "origin_concept_id": item_id}
            for item_id in item_ids
            for row in _rows(accepted[item_id]["records"], "exclusions")
        ],
    }
    records["documents"] = _select_cited_documents(
        _merge_documents(
            row
            for item_id in item_ids
            for row in _cited_documents(accepted[item_id]["records"])
            if str(row["document_id"]) not in upstream_document_ids
        ),
        records,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "candidate_seed_generation",
        "status": "complete",
        "graph_snapshot_id": results["evidence_graph"]["snapshot_id"],
        "records": records,
        "gaps": _item_gaps(
            root, results, "candidate_seed_generation", "candidate_seed_research"
        ),
        "notes": [
            f"Submitted {len(raw_candidates)} raw seeds to UniChem; "
            f"resolved {len(_exact_identity_groups(records))} exact identity group(s) and queued "
            f"{queued_count} seed(s) for identity review."
        ],
    }


def _empty_identity_result(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if _identity_queue(results["candidate_seed_generation"]["records"]):
        raise ProgramError("Candidate identity review is required before controller advancement")
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "candidate_identity",
        "status": "complete",
        "records": {"documents": [], "identity_groups": []},
        "gaps": [],
        "notes": ["Every candidate was resolved by exact UniChem identity."],
    }


def _build_review_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    reviews = _merge_unique(
        _item_collection(
            root, results, "candidate_review", "candidate_evidence_review", "reviews"
        ),
        "candidate_id",
        "reviews",
    )
    upstream_document_ids = {
        str(row["document_id"]) for row in _all_documents(results)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": "candidate_review",
        "status": "complete",
        "records": {
            "documents": _select_cited_documents(
                (
                    row
                    for row in _item_cited_documents(
                        root, results, "candidate_review", "candidate_evidence_review"
                    )
                    if str(row["document_id"]) not in upstream_document_ids
                ),
                reviews,
            ),
            "reviews": reviews,
        },
        "gaps": _item_gaps(root, results, "candidate_review", "candidate_evidence_review"),
        "notes": [],
    }


def _advance_controller(
    root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> None:
    if stage == "pathology_sources":
        try:
            result = fetch_pathology_sources(root, str(case["disease"]), case.get("mondo"))
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        result["schema_version"] = SCHEMA_VERSION
        _validate_source_result(result)
    elif stage == "evidence_graph":
        result = _build_graph_result(root, results)
    elif stage == "candidate_seed_generation":
        result = _build_seed_result(root, results)
    elif stage == "candidate_identity":
        result = _empty_identity_result(results)
    elif stage == "candidate_review":
        result = _build_review_result(root, results)
    else:
        raise ProgramError(f"No controller action exists for stage: {stage}")
    _write_json(_result_path(root, stage), result)


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


def _validate_source_result(result: Mapping[str, Any]) -> None:
    records = result.get("records")
    if not isinstance(records, dict):
        raise ProgramError("Pathology source adapter did not return records")
    documents = _validate_documents(records)
    nodes = _contract_rows(records, "source_nodes", "node_id")
    edges = _contract_rows(records, "source_edges", "edge_id")
    contexts = _contract_rows(records, "disease_context", "context_id")
    _contract_rows(records, "source_receipts")
    document_ids = {str(row["document_id"]) for row in documents}
    node_ids = {str(row["node_id"]) for row in nodes}
    for index, row in enumerate(nodes):
        _references(row, "source_ids", document_ids, f"source_nodes[{index}]")
    for index, row in enumerate(edges):
        label = f"source_edges[{index}]"
        if str(row["subject_id"]) not in node_ids or str(row["object_id"]) not in node_ids:
            raise ProgramError(f"{label} refers to an unknown source node")
        _references(row, "source_ids", document_ids, label)
    for index, row in enumerate(contexts):
        _references(row, "source_ids", document_ids, f"disease_context[{index}]")
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields reached the pathology source result: {forbidden}")


def _validate_curation(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    concepts = _contract_rows(records, "concepts", "concept_id")
    source_nodes = _rows(results["pathology_sources"]["records"], "source_nodes")
    expected = {
        str(row["node_id"])
        for row in source_nodes
        if row.get("node_type") != "disease_anchor"
    }
    assigned: list[str] = []
    retained_labels: dict[tuple[str, str], str] = {}
    for index, concept in enumerate(concepts):
        label = f"concepts[{index}]"
        _required(concept, ("preferred_label", "concept_type", "disposition", "reason"), label)
        members = concept.get("member_node_ids")
        aliases = concept.get("aliases")
        related = concept.get("related_concept_ids")
        if not isinstance(members, list) or not members:
            raise ProgramError(f"{label}.member_node_ids must be a non-empty list")
        if not isinstance(aliases, list) or any(
            not isinstance(value, str) or not value.strip() for value in aliases
        ):
            raise ProgramError(f"{label}.aliases must be a list of non-empty strings")
        if not isinstance(related, list) or any(
            not isinstance(value, str) or not value.strip() for value in related
        ):
            raise ProgramError(
                f"{label}.related_concept_ids must be a list of non-empty strings"
            )
        if len(related) != len(set(related)):
            raise ProgramError(f"{label}.related_concept_ids contains duplicates")
        member_ids = list(map(str, members))
        if len(member_ids) != len(set(member_ids)):
            raise ProgramError(f"{label}.member_node_ids contains duplicates")
        if str(concept["concept_id"]) not in member_ids:
            raise ProgramError(f"{label}.concept_id must be one of its member_node_ids")
        if concept["concept_type"] not in {"driver", "mechanism", "phenotype", "context"}:
            raise ProgramError(
                f"{label}.concept_type must be driver, mechanism, phenotype, or context"
            )
        if concept["disposition"] not in {"research", "context_only", "exclude"}:
            raise ProgramError(
                f"{label}.disposition must be research, context_only, or exclude"
            )
        if concept["disposition"] == "research" and concept["concept_type"] == "context":
            raise ProgramError(f"{label} cannot research a context-only concept type")
        if concept["disposition"] != "exclude":
            key = (
                str(concept["concept_type"]),
                str(concept["preferred_label"]).strip().casefold(),
            )
            previous = retained_labels.get(key)
            if previous:
                raise ProgramError(
                    f"{label} duplicates the retained type and label in {previous}; "
                    "merge equivalent source claims or give distinct claims distinct labels"
                )
            retained_labels[key] = label
        assigned.extend(member_ids)
    if len(assigned) != len(set(assigned)) or set(assigned) != expected:
        missing = sorted(expected - set(assigned))
        unknown = sorted(set(assigned) - expected)
        raise ProgramError(
            "Pathology concepts must partition every supplied non-anchor node exactly once; "
            f"missing={missing}, unknown={unknown}"
        )
    research_ids = {
        str(concept["concept_id"])
        for concept in concepts
        if concept["disposition"] == "research"
    }
    for index, concept in enumerate(concepts):
        related = set(map(str, concept["related_concept_ids"]))
        if concept["disposition"] == "context_only":
            if not related or not related <= research_ids:
                raise ProgramError(
                    f"concepts[{index}].related_concept_ids must contain only retained research concepts"
                )
        elif related:
            raise ProgramError(
                f"concepts[{index}].related_concept_ids must be empty unless context_only"
            )
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields are forbidden in pathology curation: {forbidden}")


def _validate_pathology_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    source_document_ids = _ids(
        _rows(results["pathology_sources"]["records"], "documents"),
        "document_id",
        "documents",
    )
    if not {str(row["document_id"]) for row in documents} - source_document_ids:
        raise ProgramError("pathology node research must retain newly researched evidence")
    profiles = _contract_rows(records, "profiles", "node_id")
    assertions = _contract_rows(records, "assertions", "assertion_id")
    if len(profiles) != 1 or str(profiles[0]["node_id"]) != item_id:
        raise ProgramError("pathology node research must return exactly one profile for item_id")
    source_nodes, _ = _canonical_source_records(results)
    node = _find(source_nodes, "node_id", item_id)
    if node.get("disposition") != "research":
        raise ProgramError("pathology node research item must be a curated research concept")
    if profiles[0]["node_type"] != node["node_type"]:
        raise ProgramError("profile.node_type must match the source-derived node")
    profile = profiles[0]
    _required(
        profile,
        (
            "summary", "normal_state", "pathological_state", "desired_biological_state",
            "causal_role", "uncertainty",
        ),
        "profiles[0]",
    )
    for field in PATHOLOGY_PROFILE_LIST_FIELDS:
        if not isinstance(profile[field], list):
            raise ProgramError(f"profiles[0].{field} must be a list")
    node_ids = {str(row["node_id"]) for row in source_nodes}
    source_ids = {
        *source_document_ids,
        *(str(row["document_id"]) for row in documents),
    }
    _references(profiles[0], "source_ids", source_ids, "profiles[0]")
    observations = profile["established_pathology_observations"]
    if not isinstance(observations, list) or any(
        not isinstance(row, dict) for row in observations
    ):
        raise ProgramError(
            "profiles[0].established_pathology_observations must be a list of objects"
        )
    for index, observation in enumerate(observations):
        label = f"profiles[0].established_pathology_observations[{index}]"
        if set(observation) != {"observation", "source_ids"}:
            raise ProgramError(f"{label} must contain only observation and source_ids")
        _required(observation, ("observation",), label)
        _references(observation, "source_ids", source_ids, label)
    for index, row in enumerate(assertions):
        label = f"assertions[{index}]"
        _required(
            row,
            ("assertion_id", "subject_id", "relation", "object_id", "evidence_summary"),
            label,
        )
        if str(row["subject_id"]) not in node_ids or str(row["object_id"]) not in node_ids:
            raise ProgramError(f"{label} refers to an unknown curated pathology concept")
        _references(row, "source_ids", source_ids, label)
    forbidden = _forbidden_pathology_paths(records)
    if forbidden:
        raise ProgramError(f"Treatment fields are forbidden in pathology research: {forbidden}")


def _validate_seed_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    candidates = _contract_rows(records, "candidates", "candidate_id")
    _contract_rows(records, "exclusions")
    graph = results["evidence_graph"]["records"]
    concept = _find(_research_concepts(results), "concept_id", item_id)
    concept_id = str(concept["concept_id"])
    support_by_node = _graph_support_ids(graph)
    allowed_node_ids = set(support_by_node)
    pathology_source_ids = _ids(_rows(graph, "documents"), "document_id", "documents")
    new_mechanism_source_ids = {str(row["document_id"]) for row in documents}
    mechanism_source_ids = {
        *pathology_source_ids,
        *new_mechanism_source_ids,
    }
    for index, row in enumerate(candidates):
        label = f"candidates[{index}]"
        unexpected = sorted(set(row) - set(ROW_FIELDS["candidates"]))
        if unexpected:
            raise ProgramError(f"{label} has unexpected fields: {unexpected}")
        _required(
            row,
            ("candidate_id", "name", "identity", "mechanism_hypothesis"),
            label,
        )
        if str(row["name"]).strip().casefold() in _COMPARATORS:
            raise ProgramError(f"{label} is a comparator, not a drug candidate")
        identity = row.get("identity")
        if not isinstance(identity, dict) or identity.get("status") not in {
            "resolved",
            "unresolved",
            "conflicting",
        }:
            raise ProgramError(
                f"{label}.identity.status must be resolved, unresolved, or conflicting"
            )
        _required(identity, ("preferred_name", "identifiers"), f"{label}.identity")
        if not isinstance(identity["identifiers"], dict):
            raise ProgramError(f"{label}.identity.identifiers must be an object")
        if identity["status"] == "resolved" and not identity["identifiers"]:
            raise ProgramError(
                f"{label}.identity requires an authoritative identifier when resolved"
            )
        graph_refs = _references(row, "graph_node_ids", allowed_node_ids, label)
        if concept_id not in graph_refs:
            raise ProgramError(f"{label}.graph_node_ids must include the focal item concept")
        pathology_refs = _references(row, "pathology_source_ids", pathology_source_ids, label)
        unsupported = sorted(
            node_id
            for node_id in graph_refs
            if not pathology_refs & support_by_node[node_id]
        )
        if unsupported:
            raise ProgramError(
                f"{label}.pathology_source_ids do not support graph nodes: {unsupported}"
            )
        mechanism_refs = _references(row, "mechanism_source_ids", mechanism_source_ids, label)
        if not mechanism_refs & new_mechanism_source_ids:
            raise ProgramError(f"{label}.mechanism_source_ids needs a retained drug-MOA source")


def _validate_candidate_identity(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    groups = _contract_rows(records, "identity_groups")
    seed_records = results["candidate_seed_generation"]["records"]
    queue_ids = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    covered: list[str] = []
    targets: list[str] = []
    exact_blocks = {
        candidate_id: set(member_ids)
        for candidate_id, member_ids in _exact_identity_groups(seed_records).items()
    }
    document_ids = {str(row["document_id"]) for row in documents}
    for index, group in enumerate(groups):
        label = f"identity_groups[{index}]"
        member_ids = group.get("member_seed_ids")
        if not isinstance(member_ids, list) or not member_ids:
            raise ProgramError(f"{label}.member_seed_ids must be a non-empty list")
        members = [str(value) for value in member_ids]
        if len(members) != len(set(members)) or not set(members) <= queue_ids:
            raise ProgramError(f"{label}.member_seed_ids must be unique queued seed IDs")
        member_set = set(members)
        member_exact_ids = {
            candidate_id
            for candidate_id, block in exact_blocks.items()
            if member_set.intersection(block)
        }
        if any(member_set & block and not block <= member_set for block in exact_blocks.values()):
            raise ProgramError(f"{label} cannot split an exact UniChem identity group")
        covered.extend(members)
        if group.get("status") not in {"resolved", "unresolved", "conflicting"}:
            raise ProgramError(f"{label}.status must be resolved, unresolved, or conflicting")
        _required(group, ("preferred_name", "reason"), label)
        if not isinstance(group.get("identifiers"), dict):
            raise ProgramError(f"{label}.identifiers must be an object")
        target = group.get("canonical_candidate_id")
        if target is not None:
            target = str(target)
            valid = (
                target in exact_blocks
                and group["status"] == "resolved"
                and member_exact_ids <= {target}
            )
            if valid and exact_blocks[target] & queue_ids:
                valid = exact_blocks[target] <= member_set
            if not valid:
                raise ProgramError(
                    f"{label}.canonical_candidate_id must be an exact supplied UniChem "
                    "candidate, contain no different UCI, and the group must be resolved"
                )
            targets.append(target)
        elif group["status"] == "resolved" and len(member_exact_ids) == 1:
            raise ProgramError(
                f"{label}.canonical_candidate_id is required when a resolved group contains "
                "one exact UniChem identity"
            )
        _references(group, "source_ids", document_ids, label)
    if sorted(covered) != sorted(queue_ids) or len(covered) != len(set(covered)):
        raise ProgramError("identity_groups must partition every queued seed exactly once")
    if len(targets) != len(set(targets)):
        raise ProgramError("Each exact UniChem candidate may be attached at most once")


def _component_score(value: Any, label: str) -> int:
    if type(value) is not int or value not in SCORE_VALUES:
        raise ProgramError(f"{label} must be one of {sorted(SCORE_VALUES)}")
    return value


def _accepted_ids(
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    collection: str,
    field: str,
) -> set[str]:
    return _ids(_rows(results[stage]["records"], collection), field, collection)


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


def _validate_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not str(item).strip() for item in value):
        raise ProgramError(f"{label} must be a list of non-empty strings")


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


def _validate_review_item(
    records: Mapping[str, Any], item_id: str, results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    reviews = _contract_rows(records, "reviews", "candidate_id")
    batch = _find(_review_batches(results), "concept_id", item_id)
    expected_ids = set(map(str, batch["candidate_ids"]))
    review_ids = {str(row["candidate_id"]) for row in reviews}
    if review_ids != expected_ids:
        raise ProgramError("candidate review must cover exactly the supplied batch candidates")
    retained_ids = {str(row["document_id"]) for row in documents}
    source_ids = {
        *(str(row["document_id"]) for row in _all_documents(results)),
        *retained_ids,
    }
    for index, row in enumerate(reviews):
        label = f"reviews[{index}]"
        unexpected = sorted(set(row) - set(ROW_FIELDS["reviews"]))
        if unexpected:
            raise ProgramError(f"{label} has unexpected fields: {unexpected}")
        _required(row, ("candidate_id", "hypothesis", "mechanistic_bridge"), label)
        _validate_cited_entries(
            row["supporting_findings"],
            label=f"{label}.supporting_findings",
            text_field="finding",
            source_ids=source_ids,
        )
        if not row["supporting_findings"]:
            raise ProgramError(f"{label}.supporting_findings must not be empty")
        _validate_string_list(row["assumptions"], f"{label}.assumptions")
        _validate_string_list(row["limitations"], f"{label}.limitations")
        _validate_cited_entries(
            row["aliases"],
            label=f"{label}.aliases",
            text_field="name",
            source_ids=source_ids,
        )
        _validate_cited_entries(
            row["why_not"],
            label=f"{label}.why_not",
            text_field="finding",
            source_ids=source_ids,
        )
        prior_art = _validate_exact_object(
            row["prior_art"], {"status", "summary", "findings"}, f"{label}.prior_art"
        )
        if prior_art["status"] not in PRIOR_ART_STATUSES:
            raise ProgramError(
                f"{label}.prior_art.status must be one of {sorted(PRIOR_ART_STATUSES)}"
            )
        if not str(prior_art["summary"]).strip():
            raise ProgramError(f"{label}.prior_art.summary must be non-empty")
        _validate_cited_entries(
            prior_art["findings"],
            label=f"{label}.prior_art.findings",
            text_field="finding",
            source_ids=source_ids,
        )
        if prior_art["status"] in {
            "preclinical_only", "human_intervention", "established_use"
        } and not prior_art["findings"]:
            raise ProgramError(
                f"{label}.prior_art.findings must not be empty for status {prior_art['status']}"
            )
        cited_ids = _cited_ids(row)
        unknown = cited_ids - source_ids
        if unknown:
            raise ProgramError(f"{label} contains unknown source IDs: {sorted(unknown)}")
        if not cited_ids & retained_ids:
            raise ProgramError(f"{label} needs a cited document retained by this review")


def _validate_candidate_audit(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    assessments = _contract_rows(records, "assessments", "candidate_id")
    exclusions = _contract_rows(records, "excluded_candidates", "candidate_id")
    candidate_ids = _accepted_ids(
        results, "candidate_review", "reviews", "candidate_id"
    )
    assessment_ids = {str(row["candidate_id"]) for row in assessments}
    exclusion_ids = {str(row["candidate_id"]) for row in exclusions}
    if assessment_ids & exclusion_ids or assessment_ids | exclusion_ids != candidate_ids:
        raise ProgramError(
            "assessments and excluded_candidates must partition every reviewed candidate exactly once"
        )
    source_ids = {str(row["document_id"]) for row in _all_documents(results)}
    reviews = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_review"]["records"], "reviews")
    }
    for index, row in enumerate(assessments):
        label = f"assessments[{index}]"
        unexpected = sorted(set(row) - set(ROW_FIELDS["assessments"]))
        if unexpected:
            raise ProgramError(f"{label} has unexpected fields: {unexpected}")
        prior_status = reviews[str(row["candidate_id"])]["prior_art"]["status"]
        if prior_status in {"human_intervention", "established_use"}:
            raise ProgramError(
                f"{label} cannot assess a candidate with disqualifying prior-art status {prior_status}"
            )
        integrity = _validate_exact_object(
            row["source_integrity"],
            {"status", "finding", "source_ids"},
            f"{label}.source_integrity",
        )
        if integrity["status"] not in {"supported", "partly_supported"}:
            raise ProgramError(
                f"{label}.source_integrity.status must be supported or partly_supported"
            )
        if not str(integrity["finding"]).strip():
            raise ProgramError(f"{label}.source_integrity.finding must be non-empty")
        _references(integrity, "source_ids", source_ids, f"{label}.source_integrity")

        components = _validate_exact_object(
            row["component_scores"], set(SCORE_COMPONENTS), f"{label}.component_scores"
        )
        for component in SCORE_COMPONENTS:
            score = _validate_exact_object(
                components[component],
                {"value", "reason", "source_ids"},
                f"{label}.component_scores.{component}",
            )
            _component_score(score["value"], f"{label}.component_scores.{component}.value")
            if not str(score["reason"]).strip():
                raise ProgramError(f"{label}.component_scores.{component}.reason must be non-empty")
            _references(
                score,
                "source_ids",
                source_ids,
                f"{label}.component_scores.{component}",
            )

        net = _validate_exact_object(
            row["net_assessment"], {"text", "source_ids"}, f"{label}.net_assessment"
        )
        if not str(net["text"]).strip():
            raise ProgramError(f"{label}.net_assessment.text must be non-empty")
        _references(net, "source_ids", source_ids, f"{label}.net_assessment")
        _validate_cited_entries(
            row["aliases"],
            label=f"{label}.aliases",
            text_field="name",
            source_ids=source_ids,
        )
        _validate_cited_entries(
            row["why_not"],
            label=f"{label}.why_not",
            text_field="finding",
            source_ids=source_ids,
        )

    expected_prior_reasons = {
        "established_use": "exact_disease_use",
        "human_intervention": "human_intervention",
    }
    for index, row in enumerate(exclusions):
        label = f"excluded_candidates[{index}]"
        unexpected = sorted(set(row) - set(ROW_FIELDS["excluded_candidates"]))
        if unexpected:
            raise ProgramError(f"{label} has unexpected fields: {unexpected}")
        if row["reason_code"] not in AUDIT_EXCLUSION_REASONS:
            raise ProgramError(
                f"{label}.reason_code must be one of {sorted(AUDIT_EXCLUSION_REASONS)}"
            )
        if not str(row["finding"]).strip():
            raise ProgramError(f"{label}.finding must be non-empty")
        _references(row, "source_ids", source_ids, label)
        prior_status = reviews[str(row["candidate_id"])]["prior_art"]["status"]
        expected_reason = expected_prior_reasons.get(prior_status)
        if expected_reason and row["reason_code"] != expected_reason:
            raise ProgramError(
                f"{label}.reason_code must be {expected_reason} for prior-art status {prior_status}"
            )


def _validate_result(
    task: str,
    item_id: str | None,
    result: Mapping[str, Any],
    packet: Mapping[str, Any],
    prior: Mapping[str, Mapping[str, Any]],
) -> None:
    if (
        result.get("stage") != task
        or result.get("item_id") != item_id
        or result.get("packet_id") != packet.get("packet_id")
    ):
        raise ProgramError("Result stage or packet_id does not match the ready packet")
    if result.get("status") != "complete":
        raise ProgramError(
            "Only status=complete results become canonical; revise failed work and resubmit"
        )
    if not isinstance(result.get("records"), dict) or not isinstance(result.get("gaps"), list):
        raise ProgramError("Result requires records object and gaps list")
    expected_collections = set(STAGE_GUIDANCE[task]["collections"])
    actual_collections = set(result["records"])
    if actual_collections != expected_collections:
        raise ProgramError(
            "Result records must contain exactly these collections: "
            f"{sorted(expected_collections)}"
        )
    if "notes" in result and not isinstance(result["notes"], list):
        raise ProgramError("Result notes must be a list when supplied")
    secrets = _secret_paths(result)
    if secrets:
        raise ProgramError(f"Credentials must never be persisted in results: {secrets}")
    validators = {
        "pathology_curation": lambda: _validate_curation(result["records"], prior),
        "pathology_node_research": lambda: _validate_pathology_item(
            result["records"], str(item_id), prior
        ),
        "candidate_seed_research": lambda: _validate_seed_item(
            result["records"], str(item_id), prior
        ),
        "candidate_identity": lambda: _validate_candidate_identity(
            result["records"], prior
        ),
        "candidate_evidence_review": lambda: _validate_review_item(
            result["records"], str(item_id), prior
        ),
        "candidate_audit": lambda: _validate_candidate_audit(result["records"], prior),
    }
    validators[task]()


def submit(root: str | Path, result_path: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, prior = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, prior)
    if current["state"] != "needs_agent":
        raise ProgramError(f"No agent result is ready for submission; state is {current['state']}")
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, prior, task, item_id)
    result = _read_json(Path(result_path).expanduser().resolve())
    _validate_result(task, item_id, result, packet, prior)
    destination = (
        _item_result_path(run_root, task, str(item_id))
        if item_id is not None
        else _result_path(run_root, task)
    )
    _write_json(destination, result)
    return _program_status(run_root, case, _load_results(run_root))


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"filename": path.name, "bytes": len(payload), "sha256": _sha256(payload)}


def _final_score(row: Mapping[str, Any]) -> int:
    return sum(int(row["component_scores"][component]["value"]) for component in SCORE_COMPONENTS)


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    projected = {
        "rank": rank,
        "candidate_id": row["candidate_id"],
        "name": candidate["name"],
        "identity_status": candidate["identity"]["status"],
        **{
            component: row["component_scores"][component]["value"]
            for component in SCORE_COMPONENTS
        },
        "final_score": _final_score(row),
        "net_assessment": row["net_assessment"]["text"],
        "source_ids": ";".join(sorted(map(str, row["net_assessment"]["source_ids"]))),
    }
    return projected


def _ranked_rows(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = {row["candidate_id"]: row for row in _canonical_candidates(results)}
    assessments = _rows(results["candidate_audit"]["records"], "assessments")
    assessments.sort(key=lambda row: (-_final_score(row), str(row["candidate_id"])))
    rows: list[dict[str, Any]] = []
    rank = 0
    prior_score: int | None = None
    for assessment in assessments:
        score = _final_score(assessment)
        if score != prior_score:
            rank += 1
            prior_score = score
        rows.append(_project_ranked_row(rank, assessment, candidates))
    return rows, candidates


def _evidence_card_rows(
    ranked_rows: list[dict[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    assessments = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_audit"]["records"], "assessments")
    }
    cards: list[dict[str, Any]] = []
    for ranked_row in ranked_rows:
        candidate_id = str(ranked_row["candidate_id"])
        assessment = assessments[candidate_id]
        aliases = sorted(
            (
                {
                    "name": str(alias["name"]).strip(),
                    "source_ids": sorted(set(map(str, alias["source_ids"]))),
                }
                for alias in assessment["aliases"]
            ),
            key=lambda alias: (
                alias["name"].casefold(),
                alias["name"],
                alias["source_ids"],
            ),
        )
        why_not = sorted(
            (
                {
                    "finding": str(finding["finding"]).strip(),
                    "source_ids": sorted(set(map(str, finding["source_ids"]))),
                }
                for finding in assessment["why_not"]
            ),
            key=lambda finding: (
                finding["finding"].casefold(),
                finding["finding"],
                finding["source_ids"],
            ),
        )
        cards.append(
            {
                "drug_id": candidate_id,
                "aliases": aliases,
                "score": _final_score(assessment),
                "components": {
                    component: {
                        "value": assessment["component_scores"][component]["value"],
                        "reason": str(
                            assessment["component_scores"][component]["reason"]
                        ).strip(),
                        "source_ids": sorted(set(map(
                            str, assessment["component_scores"][component]["source_ids"]
                        ))),
                    }
                    for component in SCORE_COMPONENTS
                },
                "why": {
                    "text": str(assessment["net_assessment"]["text"]).strip(),
                    "source_ids": sorted(set(map(
                        str, assessment["net_assessment"]["source_ids"]
                    ))),
                },
                "why_not": why_not,
            }
        )
    return cards


def _single_line(value: Any) -> str:
    return " ".join(str(value).split())


def _reference_line(source_ids: Iterable[Any]) -> str:
    return "References: " + ", ".join(sorted(set(map(str, source_ids))))


def _cards_bytes(cards: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for card in cards:
        lines.extend([f"## {_single_line(card['drug_id'])}", ""])
        if card["aliases"]:
            lines.append("Aliases:")
            lines.extend(
                f"- {_single_line(alias['name'])} "
                f"({_reference_line(alias['source_ids'])})"
                for alias in card["aliases"]
            )
            lines.append("")
        lines.extend([f"Score: {card['score']}/100", ""])
        for component in SCORE_COMPONENTS:
            score = card["components"][component]
            lines.extend(
                [
                    f"- {SCORE_LABELS[component]}: {score['value']}/20 — "
                    f"{_single_line(score['reason'])}",
                    f"  {_reference_line(score['source_ids'])}",
                ]
            )
        lines.append("")
        lines.extend(
            [
                "### Why",
                "",
                _single_line(card["why"]["text"]),
                "",
                _reference_line(card["why"]["source_ids"]),
                "",
            ]
        )
        if card["why_not"]:
            lines.extend(["### Why not", ""])
            for finding in card["why_not"]:
                lines.extend(
                    [
                        f"- {_single_line(finding['finding'])}",
                        f"  {_reference_line(finding['source_ids'])}",
                    ]
                )
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _provenance_rows(
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    assertions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        candidate = candidates[row["candidate_id"]]
        node_ids = set(map(str, candidate["graph_node_ids"]))
        output.append(
            {
                "candidate_id": row["candidate_id"],
                "graph_node_ids": sorted(node_ids),
                "assertion_ids": sorted(
                    assertion["assertion_id"]
                    for assertion in assertions
                    if str(assertion["subject_id"]) in node_ids
                    or str(assertion["object_id"]) in node_ids
                ),
                "pathology_source_ids": sorted(map(str, candidate["pathology_source_ids"])),
                "mechanism_source_ids": sorted(map(str, candidate["mechanism_source_ids"])),
                "origin_concept_ids": sorted(
                    map(str, candidate.get("origin_concept_ids", []))
                ),
            }
        )
    return output


def _excluded_candidate_rows(
    results: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exclusion in sorted(
        _rows(results["candidate_audit"]["records"], "excluded_candidates"),
        key=lambda row: str(row["candidate_id"]),
    ):
        candidate = candidates[str(exclusion["candidate_id"])]
        rows.append(
            {
                "candidate_id": exclusion["candidate_id"],
                "name": candidate["name"],
                "reason_code": exclusion["reason_code"],
                "finding": exclusion["finding"],
                "source_ids": sorted(set(map(str, exclusion["source_ids"]))),
                "graph_node_ids": sorted(set(map(str, candidate["graph_node_ids"]))),
                "pathology_source_ids": sorted(set(map(str, candidate["pathology_source_ids"]))),
                "mechanism_source_ids": sorted(set(map(str, candidate["mechanism_source_ids"]))),
            }
        )
    return rows


def _write_output_files(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[Path]:
    outputs = run_root / "outputs"
    graph = results["evidence_graph"]["records"]
    _write_once(outputs / "candidates.csv", _csv_bytes(rows, list(rows[0])))
    card_rows = _evidence_card_rows(rows, results)
    _write_once(outputs / "candidate_cards.md", _cards_bytes(card_rows))
    excluded_rows = _excluded_candidate_rows(results, candidates)
    _write_jsonl(outputs / "candidate_exclusions.jsonl", excluded_rows)
    documents = sorted(_all_documents(results), key=lambda row: row["document_id"])
    _write_jsonl(outputs / "citations.jsonl", documents)
    assertions = _rows(graph, "assertions")
    _write_json(
        outputs / "graph.json",
        {
            "case_id": case["case_id"],
            "snapshot_id": results["evidence_graph"]["snapshot_id"],
            "nodes": _rows(graph, "source_nodes"),
            "source_edges": _rows(graph, "source_edges"),
            "disease_context": _rows(graph, "disease_context"),
            "profiles": _rows(graph, "profiles"),
            "assertions": assertions,
        },
    )
    _write_jsonl(
        outputs / "candidate_provenance.jsonl",
        _provenance_rows(rows, candidates, assertions),
    )
    gap_count = sum(len(results[stage].get("gaps", [])) for stage in STAGES)
    raw_candidate_count = len(
        _rows(results["candidate_seed_generation"]["records"], "candidates")
    )
    summary = (
        "# Repurposing programme summary\n\n"
        f"Disease: {case['disease']}\n\n"
        f"Gene: {case.get('gene') or 'not supplied'}\n\n"
        f"Pathology graph snapshot: {results['evidence_graph']['snapshot_id']}\n\n"
        f"Status: complete with {len(rows)} ranked candidate(s) and "
        f"{len(excluded_rows)} audited exclusion(s).\n\n"
        f"Sources: {len(documents)}; pathology nodes: {len(graph['profiles'])}; "
        f"assertions: {len(graph['assertions'])}; raw candidate seeds: "
        f"{raw_candidate_count}; deduplicated candidates: {len(candidates)}; "
        f"reported gaps: {gap_count}.\n\n"
        "Candidate nomination did not require a prior disease-drug literature association. "
        "Audited candidates were ranked by an unweighted sum of five 20-point components; "
        "exact-disease established use or human trials and other bounded decisive failures were "
        "exclusionary.\n\n"
        f"{EXPERIMENTAL_USE_POLICY}\n"
    )
    _write_once(outputs / "summary.md", summary.encode("utf-8"))
    return [
        outputs / "candidates.csv",
        outputs / "candidate_cards.md",
        outputs / "candidate_exclusions.jsonl",
        outputs / "citations.jsonl",
        outputs / "graph.json",
        outputs / "candidate_provenance.jsonl",
        outputs / "summary.md",
    ]


def build_outputs(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, results)
    if current["state"] == "complete":
        return _read_json(run_root / "outputs" / "manifest.json")
    if current["state"] != "ready_to_build":
        raise ProgramError(f"Outputs cannot be built while state is {current['state']}")
    rows, candidates = _ranked_rows(results)
    artifact_paths = _write_output_files(run_root, case, results, rows, candidates)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "case_sha256": _sha256((run_root / "case.json").read_bytes()),
        "status": "complete",
        "candidate_count": len(rows),
        "excluded_candidate_count": len(
            _rows(results["candidate_audit"]["records"], "excluded_candidates")
        ),
        "raw_candidate_count": len(
            _rows(results["candidate_seed_generation"]["records"], "candidates")
        ),
        "deduplicated_candidate_count": len(candidates),
        "stage_results": {
            stage: _sha256(_result_path(run_root, stage).read_bytes()) for stage in STAGES
        },
        "artifacts": [_artifact(path) for path in artifact_paths],
        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_json(run_root / "outputs" / "manifest.json", manifest)
    return manifest


__all__ = [
    "EXPERIMENTAL_USE_POLICY",
    "OBJECTIVE",
    "ProgramError",
    "STAGES",
    "build_outputs",
    "graph_context",
    "initialize",
    "next_action",
    "status",
    "submit",
]
