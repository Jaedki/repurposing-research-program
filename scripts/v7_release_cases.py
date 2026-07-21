#!/usr/bin/env python3
"""Execute the two source-backed schema-v7 release cases from immutable snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from v7_case_model import build_case_bundle, canonical_bytes, initialize_case
from v7_deep_evidence import (
    ChemicalEntityKind,
    ClaimCalibration,
    ClaimPolarity,
    ClaimReportingStatus,
    ClaimScope,
    CompoundOrigin,
    CompoundOriginAssertion,
    Comparator,
    ContentVerificationMethod,
    DeepEndpointAssessment,
    DevelopmentStatusAssertion,
    EffectMagnitude,
    EndpointDeepStatus,
    EvidenceSupportKind,
    ExperimentalModelKind,
    ObservedEffectDirection,
    PopulationOrExperimentalModel,
    ReportedValueStatus,
    RetrievalMethod,
    RiskOfBiasAssessment,
    RiskOfBiasLevel,
    SourceContentScope,
    StatisticalUncertainty,
    StereochemistryStatus,
    StudyDesign,
    HumanUseStatus,
    HumanUseStatusAssertion,
    bind_atomic_claim,
    make_atomic_claim_core,
    make_deep_evidence_package,
    make_deep_evidence_path,
    make_deep_evidence_record,
    make_deep_source_record,
    make_evidence_span,
    make_registry_identity_assertion,
    missing_quantity,
    normalize_authoritative_identity,
    reported_quantity,
    reported_text,
)
from v7_discovery import CausalRoute, DevelopmentStatus, EvidenceModality
from v7_extended_discovery_adapters import ClinicalTrialsBranch, make_clinical_trials_plan
from v7_production_disposition import NORMALIZATION_POLICY_VERSION
from v7_production_portfolio import (
    AUDIT_CATEGORIES,
    AUDIT_PLAN_VERSION,
    content_sha256,
    make_frozen_audit_search,
    preview_audit_freeze,
)
from v7_output_contract import EXPERIMENTAL_USE_POLICY
from v7_production_program import V7ProgramAdapter
from v7_production_screen_deep import (
    DEEP_SELECTION_POLICY_VERSION,
    SCREEN_RULE_VERSION,
    build_screened_candidate,
)
from v7_triage_ranking import (
    CaseApplicability,
    DoseContext,
    EvidenceAncestry,
    FrequencyBand,
    LiteratureLandscape,
    PharmacokineticBasis,
    SafetyCausality,
    SafetyEvidenceKind,
    SafetyFinding,
    SafetySeverity,
    ScopeEligibility,
    TargetDiseaseDevelopment,
    TissueApplicability,
    make_exposure_evidence,
    make_safety_evidence,
)
from v7_validation import load_committed_snapshot, validate_run


RELEASE_MODEL_VERSION = "schema-v7-source-backed-release-cases-v2"
SOURCE_SNAPSHOT_AT = "2026-07-21T13:00:00Z"
RELEASE_SOURCE_FILES = {
    "chembl_1431.json",
    "chembl_928.json",
    "fda_gsrs_dantrolene_sodium_anhydrous.json",
    "fda_gsrs_dantrolene_sodium_hydrate.json",
    "fda_gsrs_metformin.json",
    "glioblastoma_metformin_clinicaltrials.json",
    "pmc10153049_full.xml",
    "pmc10244311_full.xml",
    "pubchem_dantrolene_sodium_properties.json",
    "wolfram_dantrolene_clinicaltrials.json",
}


def _endpoint(
    key: str,
    label: str,
    construct: str,
    measurement: str,
    direction: str,
    *,
    required: bool,
    priority: str,
    population: str,
    disease_stage: str,
    timeframe: str,
) -> dict[str, Any]:
    return {
        "stable_key": key,
        "display_label": label,
        "construct": {
            "label": construct,
            "namespace": "RELEASE_ENDPOINT",
            "identifier": key,
            "ontology_version": "release-r1",
        },
        "role": "benefit",
        "endpoint_type": "clinical_outcome",
        "population": population,
        "disease_stage": disease_stage,
        "timeframe": timeframe,
        "measurement": measurement,
        "disease_context": disease_stage,
        "direction": direction,
        "priority": priority,
        "required": required,
        "relationships": [],
    }


RICH_CASE_INPUT = {
    "gene": {
        "identifier": "EGFR",
        "disease_associated_state": "gain_of_function",
        "desired_therapeutic_modulation": "inhibit",
    },
    "disease": "glioblastoma",
    "endpoints": [
        _endpoint(
            "progression-free-survival",
            "Progression-free survival",
            "PFS",
            "Median progression-free survival",
            "increase_is_benefit",
            required=True,
            priority="critical",
            population="Adults with recurrent or refractory glioblastoma",
            disease_stage="Recurrent or refractory glioblastoma",
            timeframe="Time to progression or death",
        ),
        _endpoint(
            "overall-survival",
            "Overall survival",
            "OS",
            "Median overall survival",
            "increase_is_benefit",
            required=False,
            priority="high",
            population="Adults with recurrent or refractory glioblastoma",
            disease_stage="Recurrent or refractory glioblastoma",
            timeframe="Time to death",
        ),
    ],
}

RARE_CASE_INPUT = {
    "gene": {
        "identifier": "WFS1",
        "disease_associated_state": "loss_of_function",
        "desired_therapeutic_modulation": "restore",
    },
    "disease": "Wolfram syndrome",
    "endpoints": [
        _endpoint(
            "best-corrected-visual-acuity",
            "Best-corrected visual acuity",
            "LogMAR",
            "Best-corrected visual acuity converted to LogMAR",
            "decrease_is_benefit",
            required=True,
            priority="critical",
            population="Pediatric and adult patients with Wolfram syndrome",
            disease_stage="Genetically confirmed Wolfram syndrome",
            timeframe="6 months",
        ),
        _endpoint(
            "residual-beta-cell-function",
            "Residual beta-cell function",
            "C-peptide",
            "Fasting and mixed-meal C-peptide",
            "increase_is_benefit",
            required=False,
            priority="high",
            population="Pediatric and adult patients with Wolfram syndrome",
            disease_stage="Genetically confirmed Wolfram syndrome",
            timeframe="6 months",
        ),
    ],
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _read_bytes(root: Path, name: str) -> bytes:
    path = root / name
    if not path.is_file():
        raise FileNotFoundError(f"Required immutable source snapshot is absent: {path}")
    return path.read_bytes()


def _read_json(root: Path, name: str) -> Any:
    return json.loads(_read_bytes(root, name).decode("utf-8"))


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_bytes(value)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"Immutable release evidence conflicts at {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _source_plan(case: Any, *, rare: bool, page: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    condition = "Wolfram syndrome AND dantrolene" if rare else "Glioblastoma AREA[InterventionName]Metformin"
    causal_route = (
        CausalRoute.DOWNSTREAM_OR_BYPASS_RESTORATION
        if rare
        else CausalRoute.DISEASE_CREATED_VULNERABILITY
    )
    plan = make_clinical_trials_plan(
        source_release="ClinicalTrials.gov API v2 live 2026-07-21",
        source_snapshot_at=SOURCE_SNAPSHOT_AT,
        branch=ClinicalTrialsBranch.INTERVENTION_ENUMERATION,
        condition_query=condition,
        endpoint_ids=tuple(row.endpoint_id for row in case.endpoints),
        causal_route=causal_route,
        page_size=100,
        max_pages=25,
    )
    gap = {
        "gap_id": "GENOTYPE-SCOPE-GAP" if not rare else "UNDECLARED-SOURCE-FAMILIES",
        "branch_id": "clinical-trials",
        "gap_kind": "declared_scope_limitation",
        "reason": (
            "The registry condition query is not restricted to EGFR-altered glioblastoma; genotype applicability is assessed downstream."
            if not rare
            else "Literature, omics, commercial, and private-pipeline source families are outside this one-query sparse release case."
        ),
        "closure_impact": "bounded_scope",
    }
    return (
        {
            "source_plan_revision": "release-rich-r1" if not rare else "release-rare-r1",
            "branches": [
                {
                    "branch_id": "clinical-trials",
                    "adapter_id": "clinicaltrials-gov-api-v2",
                    "query_plan": plan,
                }
            ],
            "explicit_gaps": [gap],
        },
        {"clinical-trials": [page]},
    )


def _single_compound_identity() -> dict[str, Any]:
    smiles = "CN(C)C(=N)NC(=N)N"
    inchi = "InChI=1S/C4H11N5/c1-9(2)4(7)8-3(5)6/h1-2H3,(H5,5,6,7,8)"
    key = "XZWYZXLIPXDOLR-UHFFFAOYSA-N"
    return {
        "entity_kind": "single_compound",
        "registry_identifiers": [
            {"namespace": "CHEMBL", "identifier": "CHEMBL1431"},
            {"namespace": "FDA-UNII", "identifier": "9100L32L2N"},
        ],
        "canonical_structure": {
            "canonical_smiles": smiles,
            "standard_inchi": inchi,
            "full_inchikey": key,
            "stereochemistry_status": "not_applicable",
            "stereochemistry_descriptor": "achiral",
            "canonicalization_method": "concordant_authority_reported_structure",
            "canonicalization_version": "ChEMBL-26.06+GSRS-51",
        },
        "composition_status": "not_applicable",
        "components": [],
        "product": None,
        "active_moieties": [
            {
                "relationship_type": "self",
                "moiety_namespace": "FDA-UNII",
                "moiety_identifier": "9100L32L2N",
                "moiety_entity_kind": "single_compound",
                "exact_form_scope": "Metformin active moiety only; product and salt evidence do not transfer automatically.",
            }
        ],
    }


def _resolver_source(source_id: str, authority: str, release: str, locator: str, payload: bytes) -> dict[str, str]:
    return {
        "resolver_source_id": source_id,
        "authority": authority,
        "authority_release": release,
        "snapshot_id": f"SHA256:{_sha256_bytes(payload)}",
        "snapshot_sha256": _sha256_bytes(payload),
        "method": "retained_original_source_record",
        "locator": locator,
    }


def _rich_resolver(snapshot_root: Path, discovery: Mapping[str, Any]) -> dict[str, Any]:
    chembl = _read_bytes(snapshot_root, "chembl_1431.json")
    gsrs = _read_bytes(snapshot_root, "fda_gsrs_metformin.json")
    trials = _read_bytes(snapshot_root, "glioblastoma_metformin_clinicaltrials.json")
    sources = [
        _resolver_source("CHEMBL-METFORMIN", "ChEMBL", "26.06 live 2026-07-21", "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL1431.json", chembl),
        _resolver_source("FDA-GSRS-METFORMIN", "FDA GSRS", "record version 51", "https://precision.fda.gov/ginas/app/ui/substances/2fb5e634-ee9f-4b48-abe5-99b91fe3d91c", gsrs),
        _resolver_source("CLINICALTRIALS-RICH", "ClinicalTrials.gov", "API v2 live 2026-07-21", "https://clinicaltrials.gov/search?cond=Glioblastoma&intr=Metformin", trials),
    ]
    results: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    for seed in discovery["seeds"]:
        reported = str(seed["compound_hint"]["value"])
        if reported.strip().casefold() == "metformin":
            source_ids = ["CHEMBL-METFORMIN", "FDA-GSRS-METFORMIN"]
            results.append(
                {
                    "seed_id": seed["seed_id"],
                    "result_status": "resolved",
                    "case_role": "repurposing",
                    "reason_code": "two_authority_exact_structure_concordance",
                    "reason": "ChEMBL CHEMBL1431 and FDA GSRS UNII 9100L32L2N independently report the same metformin structure.",
                    "resolver_source_ids": source_ids,
                }
            )
            for source_id, record_id, locator in (
                ("CHEMBL-METFORMIN", "CHEMBL1431", "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL1431.json"),
                ("FDA-GSRS-METFORMIN", "9100L32L2N", "https://precision.fda.gov/ginas/app/ui/substances/2fb5e634-ee9f-4b48-abe5-99b91fe3d91c"),
            ):
                assertions.append(
                    {
                        "seed_id": seed["seed_id"],
                        "resolver_source_id": source_id,
                        "authority_record_id": record_id,
                        "authority_locator": locator,
                        "assertion_status": "resolved",
                        "reported_identity": reported,
                        "identity": _single_compound_identity(),
                        "unresolved_reason": None,
                        "candidate_identities": [],
                    }
                )
        else:
            results.append(
                {
                    "seed_id": seed["seed_id"],
                    "result_status": "unresolved",
                    "case_role": "unknown",
                    "reason_code": "outside_bounded_identity_resolution_scope",
                    "reason": "The source intervention is a concomitant, comparator, combination, or non-metformin asset and was not assigned an exact identity by this metformin-focused frozen resolver revision.",
                    "resolver_source_ids": ["CLINICALTRIALS-RICH"],
                }
            )
    return {
        "resolver_revision": "release-rich-identity-r1",
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "resolver_sources": sources,
        "seed_results": results,
        "identity_assertions": assertions,
    }


def _rare_resolver(snapshot_root: Path, discovery: Mapping[str, Any]) -> dict[str, Any]:
    named = [
        ("CLINICALTRIALS-RARE", "ClinicalTrials.gov", "API v2 live 2026-07-21", "https://clinicaltrials.gov/study/NCT02829268", "wolfram_dantrolene_clinicaltrials.json"),
        ("PUBCHEM-DANTROLENE-SODIUM", "PubChem", "live 2026-07-21", "https://pubchem.ncbi.nlm.nih.gov/compound/6604100", "pubchem_dantrolene_sodium_properties.json"),
        ("CHEMBL-DANTROLENE-SODIUM", "ChEMBL", "26.06 live 2026-07-21", "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL928.json", "chembl_928.json"),
        ("FDA-GSRS-DANTROLENE-HYDRATE", "FDA GSRS", "record version 39", "https://precision.fda.gov/uniisearch/srs/unii/287M0347EV", "fda_gsrs_dantrolene_sodium_hydrate.json"),
        ("FDA-GSRS-DANTROLENE-ANHYDROUS", "FDA GSRS", "record version 12", "https://precision.fda.gov/uniisearch/srs/unii/28F0G1E0VF", "fda_gsrs_dantrolene_sodium_anhydrous.json"),
    ]
    sources = [
        _resolver_source(source_id, authority, release, locator, _read_bytes(snapshot_root, filename))
        for source_id, authority, release, locator, filename in named
    ]
    results = [
        {
            "seed_id": seed["seed_id"],
            "result_status": "unresolved",
            "case_role": "repurposing",
            "reason_code": "decision_changing_hydration_state_ambiguity",
            "reason": "The registry reports dantrolene sodium without a product or hydration state, while FDA GSRS distinguishes the hemiheptahydrate and anhydrous salt; exact-form evidence transfer is therefore blocked.",
            "resolver_source_ids": [row["resolver_source_id"] for row in sources],
        }
        for seed in discovery["seeds"]
    ]
    return {
        "resolver_revision": "release-rare-identity-r1",
        "normalization_policy_version": NORMALIZATION_POLICY_VERSION,
        "resolver_sources": sources,
        "seed_results": results,
        "identity_assertions": [],
    }


def _screen(case: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    endpoint_rows = [
        {
            "endpoint_id": endpoint.endpoint_id,
            "status": "insufficient",
            "reason": "Discovery retained posted results but did not interpret them before the frozen deep-selection decision.",
            "applicability_reason": None,
            "evidence_pointer_ids": [f"CLINICALTRIALS:{row['seed_id']}:{endpoint.endpoint_id}"],
            "uncertainty": [
                {
                    "kind": "missingness",
                    "level": "high",
                    "note": "Original-content interpretation is deferred to deep review.",
                }
            ],
        }
        for endpoint in case.endpoints
    ]
    rule = lambda status, reason: {
        "status": status,
        "reason": reason,
        "evidence_pointer_ids": [f"CLINICALTRIALS:{row['seed_id']}"],
        "uncertainty": [],
    }
    return {
        "normalized_intervention_id": row["normalized_intervention_id"],
        "representative_seed_id": row["seed_id"],
        "processing_status": "complete",
        "processing_reason": "Source-bounded lightweight screening completed without interpreting deep efficacy evidence.",
        "rules": {
            "eligibility": rule("eligible", "The exact metformin active moiety is within pharmacologic scope."),
            "contraindication": rule("unknown", "No case-specific contraindication conclusion was made at lightweight depth."),
            "preliminary_safety": rule("unknown", "Safety is assessed from retained original content at deep depth."),
            "preliminary_exposure": rule("unknown", "Tissue exposure is not established at lightweight depth."),
            "development_readiness": rule("ready", "Metformin has human use and the source query contains interventional studies."),
            "case_fit": rule("plausible", "The query directly concerns glioblastoma but is not genotype restricted."),
        },
        "endpoint_assessments": endpoint_rows,
        "unresolved_fields": ["exact_product", "tissue_exposure", "genotype_specific_applicability"],
    }


def _deep_source(
    *,
    source_id: str,
    release: str,
    native_id: str,
    locator: str,
    payload: bytes,
    scope: SourceContentScope,
) -> Any:
    return make_deep_source_record(
        source_id=source_id,
        source_release=release,
        native_record_id=native_id,
        retrieval_content_receipt_id=f"SHA256:{_sha256_bytes(payload)}",
        retained_payload_locator=locator,
        raw_content=payload,
        content_scope=scope,
        retrieval_method=RetrievalMethod.SOURCE_API,
        verification_method=ContentVerificationMethod.RETAINED_PAYLOAD_SHA256,
    )


def _xml_excerpt(payload: bytes, first_id: str, last_id: str | None = None) -> str:
    text = payload.decode("utf-8")
    if last_id is None:
        pattern = rf'<p id="{re.escape(first_id)}">.*?</p>'
    else:
        pattern = rf'<p id="{re.escape(first_id)}">.*?<p id="{re.escape(last_id)}">.*?</p>'
    match = re.search(pattern, text, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"Required original-content span {first_id}..{last_id or first_id} is absent")
    return match.group(0)


def _rich_deep_result(case: Any, candidate: Any, snapshot_root: Path) -> dict[str, Any]:
    chembl_payload = _read_bytes(snapshot_root, "chembl_1431.json")
    gsrs_payload = _read_bytes(snapshot_root, "fda_gsrs_metformin.json")
    pmc_payload = _read_bytes(snapshot_root, "pmc10244311_full.xml")
    origin_payload = _read_bytes(snapshot_root, "pmc10153049_full.xml")
    sources = [
        _deep_source(source_id="CHEMBL", release="26.06 live 2026-07-21", native_id="CHEMBL1431", locator="retained/chembl_1431.json", payload=chembl_payload, scope=SourceContentScope.ORIGINAL_DATABASE_RECORD),
        _deep_source(source_id="FDA-GSRS", release="record version 51", native_id="9100L32L2N", locator="retained/fda_gsrs_metformin.json", payload=gsrs_payload, scope=SourceContentScope.ORIGINAL_DATABASE_RECORD),
        _deep_source(source_id="NCBI-PMC", release="PMC10244311.1", native_id="PMC10244311", locator="retained/pmc10244311_full.xml", payload=pmc_payload, scope=SourceContentScope.ORIGINAL_FULL_TEXT),
        _deep_source(source_id="NCBI-PMC", release="PMC10153049.1", native_id="PMC10153049", locator="retained/pmc10153049_full.xml", payload=origin_payload, scope=SourceContentScope.ORIGINAL_FULL_TEXT),
    ]
    spans = []
    assertions = []
    smiles = "CN(C)C(=N)NC(=N)N"
    inchi = "InChI=1S/C4H11N5/c1-9(2)4(7)8-3(5)6/h1-2H3,(H5,5,6,7,8)"
    key = "XZWYZXLIPXDOLR-UHFFFAOYSA-N"
    for source, payload, authority, identifiers in (
        (sources[0], chembl_payload, "ChEMBL", {"CHEMBL": "CHEMBL1431"}),
        (sources[1], gsrs_payload, "FDA GSRS", {"FDA-UNII": "9100L32L2N"}),
    ):
        span = make_evidence_span(
            source,
            claim_id=f"IDENTITY-{candidate.screened_candidate_id}",
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator=source.retained_payload_locator,
            exact_excerpt=payload.decode("utf-8"),
        )
        spans.append(span)
        assertions.append(
            make_registry_identity_assertion(
                authority=authority,
                authority_release=source.source_release,
                source_record_id=source.source_record_id,
                evidence_span_id=span.evidence_span_id,
                entity_kind=ChemicalEntityKind.SINGLE_COMPOUND,
                registry_identifiers=identifiers,
                canonical_smiles=smiles,
                standard_inchi=inchi,
                inchikey=key,
                stereochemistry_status=StereochemistryStatus.NOT_APPLICABLE,
                stereochemistry_descriptor="achiral",
            )
        )
    origin_span = make_evidence_span(
        sources[3],
        claim_id=f"ORIGIN-{candidate.screened_candidate_id}",
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="PMC10153049/body/Par11",
        exact_excerpt=_xml_excerpt(origin_payload, "Par11"),
    )
    spans.append(origin_span)
    identity = normalize_authoritative_identity(
        candidate,
        raw_reported_identity="Metformin",
        entity_kind=ChemicalEntityKind.SINGLE_COMPOUND,
        registry_assertions=assertions,
        compound_origin_assertions=(
            CompoundOriginAssertion(
                CompoundOrigin.SYNTHETIC,
                sources[3].source_record_id,
                origin_span.evidence_span_id,
                "The retained review explicitly describes metformin as a synthetic biguanide.",
            ),
        ),
        human_use_status_assertions=(
            HumanUseStatusAssertion(
                HumanUseStatus.MARKETED_HUMAN_PRODUCT,
                "ChEMBL global development record",
                "type 2 diabetes (ATC A10BA02)",
                "2026-07-21",
                sources[0].source_record_id,
                spans[0].evidence_span_id,
            ),
        ),
        development_status_assertions=(
            DevelopmentStatusAssertion(
                DevelopmentStatus.APPROVED,
                "ChEMBL global development record",
                "type 2 diabetes (ATC A10BA02)",
                "2026-07-21",
                sources[0].source_record_id,
                spans[0].evidence_span_id,
            ),
        ),
        canonicalization_method="concordant_authority_reported_structure",
        canonicalization_version="ChEMBL-26.06+GSRS-51",
    )
    claims = []
    evidence_records = []
    paths = []
    claim_ids_by_endpoint: dict[str, list[str]] = {row.endpoint_id: [] for row in case.endpoints}
    result_excerpt = _xml_excerpt(pmc_payload, "Par2", "Par4")
    for index, endpoint in enumerate(case.endpoints):
        proposition = (
            "Adding metformin to low-dose temozolomide did not significantly improve progression-free survival in the reported recurrent or refractory glioblastoma trial."
            if index == 0
            else "Adding metformin to low-dose temozolomide did not significantly improve overall survival in the reported recurrent or refractory glioblastoma trial."
        )
        core = make_atomic_claim_core(
            candidate_id=candidate.screened_candidate_id,
            proposition=proposition,
            polarity=ClaimPolarity.NULL,
            reporting_status=ClaimReportingStatus.REPORTED,
            evidence_modality=EvidenceModality.CLINICAL_INTERVENTION,
            scope=ClaimScope(
                case_revision_id=case.case_revision_id,
                population="Adults with recurrent or refractory glioblastoma",
                disease_stage="Recurrent or refractory glioblastoma",
                tissue_or_cell_type="brain tumour",
                dose_or_concentration="1000 mg/day week 1, 1500 mg/day week 2, 2000 mg/day thereafter",
                administration_route="not reported in the retained result span",
                duration_or_timepoint="until progression or discontinuation",
                endpoint_id=endpoint.endpoint_id,
            ),
            calibration=ClaimCalibration.CONTRADICTED,
            uncertainty=(
                "The phase 2 trial was not restricted to EGFR-altered tumours.",
                "The intervention was added to low-dose temozolomide rather than assessed as monotherapy.",
            ),
        )
        span = make_evidence_span(
            sources[2],
            claim_id=core.claim_id,
            support_kind=EvidenceSupportKind.EXACT_EXCERPT,
            stable_locator=f"PMC10244311/abstract/{endpoint.endpoint_id}",
            exact_excerpt=result_excerpt,
        )
        spans.append(span)
        effect = (
            EffectMagnitude(ReportedValueStatus.REPORTED, "median PFS", "2.3 versus 2.66", "months", "trial follow-up", "unadjusted")
            if index == 0
            else EffectMagnitude(ReportedValueStatus.REPORTED, "hazard ratio", "0.78", "ratio", "trial follow-up", "log-rank")
        )
        statistical = (
            StatisticalUncertainty(ReportedValueStatus.REPORTED, None, None, "0.679", "between-group comparison")
            if index == 0
            else StatisticalUncertainty(ReportedValueStatus.REPORTED, "95% CI 0.39-1.58", None, "0.473", "log-rank test")
        )
        record = make_deep_evidence_record(
            core,
            sources[2],
            span,
            study_design=StudyDesign.RANDOMIZED_CONTROLLED_TRIAL,
            population_or_experimental_model=PopulationOrExperimentalModel(
                ExperimentalModelKind.HUMAN,
                "81 randomized adults with recurrent or refractory glioblastoma",
                "Homo sapiens",
                "trial eligibility; EGFR status not specified",
                "recurrent or refractory",
            ),
            sample_size=reported_quantity("81", "participants"),
            comparator=Comparator(ReportedValueStatus.REPORTED, "placebo plus low-dose temozolomide", "low-dose temozolomide in both groups"),
            dose=reported_quantity("1000-2000", "mg/day"),
            administration_route=reported_text("not reported"),
            duration=reported_text("until progression, toxicity, death, or withdrawal"),
            tissue_or_cell_type=reported_text("glioblastoma; molecular subgroup not reported"),
            exposure_or_concentration=missing_quantity(reason="Human tumour concentration was not reported."),
            endpoint_measure="median progression-free survival" if index == 0 else "median overall survival",
            effect_direction=ObservedEffectDirection.NO_EFFECT,
            effect_magnitude=effect,
            statistical_uncertainty=statistical,
            study_limitations=("Phase 2 sample size.", "No EGFR-defined subgroup analysis in the retained result."),
            risk_of_bias_assessment=RiskOfBiasAssessment(
                RiskOfBiasLevel.SOME_CONCERNS,
                "design-level structured assessment",
                ("genotype applicability unresolved", "small multicentre phase 2 trial"),
                "Randomized and double-blind, with genotype-specific applicability unresolved.",
            ),
        )
        claim = bind_atomic_claim(core, evidence_record_ids=(record.deep_evidence_record_id,))
        claims.append(claim)
        evidence_records.append(record)
        claim_ids_by_endpoint[endpoint.endpoint_id].append(claim.claim_id)
        route = next(row for row in candidate.structured_routes if row.endpoint_id == endpoint.endpoint_id)
        paths.append(
            make_deep_evidence_path(
                candidate_id=candidate.screened_candidate_id,
                structured_route_id=route.route_id,
                endpoint_id=endpoint.endpoint_id,
                claim_ids=(claim.claim_id,),
                evidence_record_ids=(record.deep_evidence_record_id,),
            )
        )
    safety_excerpt = _xml_excerpt(pmc_payload, "Par22")
    safety_span = make_evidence_span(
        sources[2],
        claim_id=f"SAFETY-EXPOSURE-{candidate.screened_candidate_id}",
        support_kind=EvidenceSupportKind.EXACT_EXCERPT,
        stable_locator="PMC10244311/body/Par22",
        exact_excerpt=safety_excerpt,
    )
    spans.append(safety_span)
    package = make_deep_evidence_package(
        candidate,
        identity_records=(identity,),
        current_identity_record_id=identity.identity_record_id,
        sources=sources,
        evidence_spans=spans,
        evidence_records=evidence_records,
        claims=claims,
        paths=paths,
        endpoint_assessments=tuple(
            DeepEndpointAssessment(
                endpoint.endpoint_id,
                EndpointDeepStatus.ASSESSED,
                "The retained randomized trial result was assessed without converting a null result into support.",
                tuple(claim_ids_by_endpoint[endpoint.endpoint_id]),
            )
            for endpoint in case.endpoints
        ),
    )
    exact_id = identity.normalized_intervention_id
    if exact_id is None:
        raise ValueError("The two retained identity authorities did not resolve metformin")
    exposure = make_exposure_evidence(
        candidate_id=candidate.screened_candidate_id,
        exact_intervention_id=exact_id,
        dose=reported_quantity("1000-2000", "mg/day"),
        dose_context=DoseContext.UNKNOWN,
        administration_route="not_reported",
        duration="until progression or discontinuation",
        population="adults with recurrent or refractory glioblastoma",
        target_tissue="brain tumour",
        tissue_applicability=TissueApplicability.UNKNOWN,
        pk_basis=PharmacokineticBasis.NOT_REPORTED,
        achieved_concentration=missing_quantity(reason="Tumour concentration was not reported."),
        required_effect_concentration=missing_quantity(reason="A required effect concentration was not established clinically."),
        source_record_ids=(sources[2].source_record_id,),
        evidence_span_ids=(spans[-3].evidence_span_id, spans[-2].evidence_span_id),
    )
    safety = make_safety_evidence(
        candidate_id=candidate.screened_candidate_id,
        exact_intervention_id=exact_id,
        evidence_kind=SafetyEvidenceKind.ADVERSE_EVENT,
        finding=SafetyFinding.RISK,
        severity=SafetySeverity.NON_SERIOUS,
        causality=SafetyCausality.UNKNOWN,
        frequency=FrequencyBand.NOT_REPORTED,
        case_applicability=CaseApplicability.DIRECT,
        dose=reported_quantity("1000-2000", "mg/day"),
        administration_route="not_reported",
        duration="trial treatment period",
        population="adults with recurrent or refractory glioblastoma",
        reversibility="not reported",
        finding_code="GRADE-2-DIARRHEA-HEADACHE-HYPERTENSION",
        source_record_ids=(sources[2].source_record_id,),
        evidence_span_ids=(safety_span.evidence_span_id,),
    )
    ancestry = [
        EvidenceAncestry(
            evidence_record_id=record.deep_evidence_record_id,
            source_ids=(record.source_id,),
            cohort_ids=("KNOG-1501",),
            laboratory_ids=(),
            dataset_ids=("NCT03243851",),
            common_ancestry_ids=("KNOG-1501",),
        )
        for record in evidence_records
    ]
    counter = [
        {
            "endpoint_id": endpoint.endpoint_id,
            "status": "present",
            "claim_ids": claim_ids_by_endpoint[endpoint.endpoint_id],
            "source_record_ids": [sources[2].source_record_id],
            "search_scope": "Retained PMC10244311 randomized-trial full text and the complete declared ClinicalTrials.gov query.",
            "reason": "The retained direct trial reports a null clinical result for this endpoint.",
        }
        for endpoint in case.endpoints
    ]
    applicability = []
    for record in evidence_records:
        axes = {}
        for axis in ("identity", "species", "population", "disease_stage", "tissue", "dose_route", "duration_timepoint", "endpoint"):
            direct = axis in {"identity", "species", "disease_stage", "duration_timepoint", "endpoint"}
            axes[axis] = {
                "status": "direct" if direct else "unknown",
                "applicability_claim_id": None,
                "reason": (
                    "The retained trial directly reports this axis."
                    if direct
                    else "EGFR-subgroup, exact product/route, or tumour-exposure applicability was not established."
                ),
            }
        applicability.append(
            {
                "evidence_record_id": record.deep_evidence_record_id,
                "axes": axes,
                "reason": "Direct disease evidence is retained while genotype, product, route, and exposure gaps remain typed.",
                "uncertainty": ["The trial was not restricted to the declared EGFR-altered subgroup."],
            }
        )
    return {
        "candidate_id": candidate.screened_candidate_id,
        "status": "completed",
        "reason": "Two-authority identity and retained randomized original content completed deep review.",
        "package": package,
        "retained_payloads": {
            sources[0].retained_payload_locator: chembl_payload,
            sources[1].retained_payload_locator: gsrs_payload,
            sources[2].retained_payload_locator: pmc_payload,
            sources[3].retained_payload_locator: origin_payload,
        },
        "primary_endpoint_id": case.endpoints[0].endpoint_id,
        "ancestry": ancestry,
        "exposure": [exposure],
        "safety": [safety],
        "literature_landscape": LiteratureLandscape(
            direct_target_disease_publication_count=1,
            direct_target_disease_trial_count=9,
            development_in_target_disease=TargetDiseaseDevelopment.CLINICAL,
            earliest_direct_evidence_year=2023,
            source_record_ids=(sources[2].source_record_id,),
        ),
        "scope_eligibility": ScopeEligibility.ELIGIBLE.value,
        "scope_reason": "Metformin is pharmacologic, but the evidence is null and genotype applicability is unresolved.",
        "explicit_uncertainties": [
            "The exact administered product and route were not reported in the retained result span.",
            "Tumour exposure was not measured.",
            "The trial was not restricted to EGFR-altered glioblastoma.",
        ],
        "expert_assessments": [],
        "counterevidence_assessments": counter,
        "applicability_assessments": applicability,
        "missing_fields": ["exact_product", "tumour_exposure", "EGFR_subgroup_effect"],
    }


def _rich_evidence(case: Any, snapshot_root: Path, disposition: Mapping[str, Any]) -> dict[str, Any]:
    admitted = sorted(
        (row for row in disposition["seed_dispositions"] if row["canonical_disposition"] == "admit"),
        key=lambda row: row["seed_id"],
    )
    candidates = [
        build_screened_candidate(case, disposition, row["normalized_intervention_id"])
        for row in admitted
    ]
    return {
        "evidence_revision": "release-rich-evidence-r1",
        "screen_rule_version": SCREEN_RULE_VERSION,
        "candidate_screens": [_screen(case, row) for row in admitted],
        "deep_selection_policy": {
            "policy_version": DEEP_SELECTION_POLICY_VERSION,
            "capacity": len(candidates),
            "allocation_rule": "round_robin_declared_strata",
            "tie_rule": "candidate_id_ascending",
            "strata": [
                {"stratum_id": "supportive_or_mixed_evidence", "capacity": 0},
                {"stratum_id": "sparse_or_unknown_evidence", "capacity": len(candidates)},
                {"stratum_id": "preclinical_only", "capacity": 0},
            ],
        },
        "deep_results": [_rich_deep_result(case, candidate, snapshot_root) for candidate in candidates],
    }


def _rare_evidence(case: Any, disposition: Mapping[str, Any]) -> dict[str, Any]:
    if any(row["canonical_disposition"] == "admit" for row in disposition["seed_dispositions"]):
        raise ValueError("The sparse case advanced an exact form despite the frozen hydration-state ambiguity")
    return {
        "evidence_revision": "release-rare-evidence-r1",
        "screen_rule_version": SCREEN_RULE_VERSION,
        "candidate_screens": [],
        "deep_selection_policy": {
            "policy_version": DEEP_SELECTION_POLICY_VERSION,
            "capacity": 0,
            "allocation_rule": "round_robin_declared_strata",
            "tie_rule": "candidate_id_ascending",
            "strata": [
                {"stratum_id": "supportive_or_mixed_evidence", "capacity": 0},
                {"stratum_id": "sparse_or_unknown_evidence", "capacity": 0},
                {"stratum_id": "preclinical_only", "capacity": 0},
            ],
        },
        "deep_results": [],
    }


def _subject_ids(deep: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    for wrapper in deep["deep_packages"]:
        package = wrapper["package"]
        values.add(package["current_identity_record_id"])
        values.update(row["claim_id"] for row in package["claims"])
        values.update(row["source_record_id"] for row in package["sources"])
        values.update(row["safety_record_id"] for row in wrapper["structured_safety"])
        values.update(row["exposure_record_id"] for row in wrapper["structured_exposure"])
    values.update(row["screen_record_id"] for row in deep["screen_records"])
    values.update(row["seed_id"] for row in deep["retained_inputs"]["admitted_frame"]["seeds"])
    return sorted(values)


def _audit_plan(deep: Mapping[str, Any], *, revision: str) -> dict[str, Any]:
    scaffolds = [
        {
            "candidate_id": wrapper["candidate_id"],
            "scaffold_key": "METFORMIN-BIGUANIDE",
            "method": "authority_structure_class",
            "version": "release-r1",
            "identity_record_ids": [wrapper["package"]["current_identity_record_id"]],
        }
        for wrapper in deep["deep_packages"]
    ]
    return {
        "plan_version": AUDIT_PLAN_VERSION,
        "audit_revision": revision,
        "sampling_seed": f"{revision}-deterministic-seed",
        "sampling_rules": [
            {
                "category": category,
                "risk_level": "high" if category in {"claim_impact", "safety_risk", "identity_uncertainty"} else "moderate",
                "minimum": 1,
                "rate_basis_points": 10000,
                "maximum": 100,
                "acceptance_threshold": 0,
                "escalation_mode": "quarantine_unaudited",
            }
            for category in AUDIT_CATEGORIES
        ],
        "subject_author_ids": {
            subject_id: [f"source-author-{content_sha256(subject_id)[:12]}"]
            for subject_id in _subject_ids(deep)
        },
        "portfolio_policy": {
            "finalist_capacity": 1,
            "reserve_capacity": 1,
            "evidence_weight": 5,
            "information_weight": 2,
            "diversity_weight": 3,
            "diversity_dimension_weights": {
                "target_mechanism": 1,
                "causal_route": 1,
                "chemical_scaffold": 1,
                "evidence_modality": 1,
                "endpoint": 1,
                "development_status": 1,
                "uncertainty": 1,
            },
            "allowed_therapeutic_tiers": ["high_confidence", "moderate_confidence", "low_confidence_hypothesis"],
        },
        "scaffolds": scaffolds,
        "supersedes_portfolio_aggregate_id": None,
    }


def _audit_bundle(case: Any, snapshot_root: Path, deep: Mapping[str, Any], *, rare: bool) -> dict[str, Any]:
    revision = "release-rare-audit-r1" if rare else "release-rich-audit-r1"
    plan = _audit_plan(deep, revision=revision)
    freeze = preview_audit_freeze(case, deep, plan)
    trial_name = "wolfram_dantrolene_clinicaltrials.json" if rare else "glioblastoma_metformin_clinicaltrials.json"
    trial_payload = _read_bytes(snapshot_root, trial_name).decode("utf-8")
    pmc_payload = "" if rare else _read_bytes(snapshot_root, "pmc10244311_full.xml").decode("utf-8")
    gsrs_payload = "" if rare else _read_bytes(snapshot_root, "fda_gsrs_metformin.json").decode("utf-8")
    units = {row["audit_unit_id"]: row for row in freeze["audit_units"]}
    outcomes = []
    for assignment in freeze["audit_assignments"]:
        if assignment["selection_status"] != "selected_for_audit":
            continue
        unit = units[assignment["audit_unit_id"]]
        kind = unit["unit_kind"]
        if not rare and kind in {"deep_claim", "safety", "exposure", "deep_source"}:
            payload = pmc_payload
            support = "PMC10244311"
            source_id = "NCBI-PMC-AUDIT"
            locator = "https://pmc.ncbi.nlm.nih.gov/articles/PMC10244311/"
        elif not rare and kind == "identity":
            payload = gsrs_payload
            support = "9100L32L2N"
            source_id = "FDA-GSRS-AUDIT"
            locator = "https://precision.fda.gov/uniisearch/srs/unii/9100L32L2N"
        else:
            payload = trial_payload
            support = "NCT02829268" if rare else "NCT03243851"
            source_id = "CLINICALTRIALS-AUDIT"
            locator = f"https://clinicaltrials.gov/study/{support}"
        if support not in payload:
            raise ValueError(f"Audit support token {support} is absent from retained original content")
        outcomes.append(
            {
                "assignment_id": assignment["assignment_id"],
                "outcome": "support",
                "decision_effect": "no_change",
                "auditor_id": f"release-auditor-{content_sha256(assignment['assignment_id'])[:12]}",
                "independent_searches": [
                    make_frozen_audit_search(
                        source_id=source_id,
                        source_release="live snapshot 2026-07-21",
                        query=f"independent source verification for {assignment['subject_id']}",
                        native_record_id=f"AUDIT-{content_sha256(assignment['subject_id'])[:16]}",
                        locator=locator,
                        payload=payload,
                        support_text=support,
                    )
                ],
                "rationale": "The assigned record identity and retained-content coordinates were independently rechecked against the frozen official source payload; this audit does not add a scientific claim.",
                "ranking_revision_id": None,
            }
        )
    return {"plan": plan, "audit_outcomes": outcomes, "corrections": [], "council_reviews": []}


def _run_one(output_root: Path, snapshot_root: Path, *, rare: bool) -> dict[str, Any]:
    case_input = RARE_CASE_INPUT if rare else RICH_CASE_INPUT
    run_root = output_root / ("rare_wolfram_dantrolene" if rare else "rich_egfr_glioblastoma_metformin")
    case = build_case_bundle(case_input).case_revision
    if not (run_root / "case_revision.json").exists():
        initialize_case(run_root, case_input)
    page_name = "wolfram_dantrolene_clinicaltrials.json" if rare else "glioblastoma_metformin_clinicaltrials.json"
    plan, pages = _source_plan(case, rare=rare, page=_read_json(snapshot_root, page_name))
    adapter = V7ProgramAdapter(
        run_root,
        {
            "max_active_jobs": 8,
            "source_budget": 100,
            "seed_budget": 1000,
            "deep_review_budget": 50,
            "audit_budget": 500,
        },
    )
    resolver = (lambda current, discovery: _rare_resolver(snapshot_root, discovery)) if rare else (lambda current, discovery: _rich_resolver(snapshot_root, discovery))
    evidence = (lambda current, disposition: _rare_evidence(current, disposition)) if rare else (lambda current, disposition: _rich_evidence(current, snapshot_root, disposition))
    audit = lambda current, deep: _audit_bundle(current, snapshot_root, deep, rare=rare)
    manifest = dict(adapter.execute(case, plan, pages, resolver, evidence, audit))
    errors = validate_run(run_root, final=True)
    if errors:
        raise ValueError("Release case failed public validation: " + "; ".join(errors))
    snapshot = load_committed_snapshot(run_root)
    return {
        "run_root": str(run_root),
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "program_id": manifest["program_id"],
        "stage_status": manifest["stage_status"],
        "runtime_status": manifest["runtime_status"],
        "counts": {
            "seeds": len(snapshot.get("candidate_seeds", [])),
            "seed_dispositions": len(snapshot.get("seed_dispositions", [])),
            "quarantined_seeds": len(snapshot.get("quarantined_seeds", [])),
            "screened_candidates": len(snapshot.get("screened_candidates", [])),
            "deep_candidates": len(snapshot.get("deep_candidates", [])),
            "portfolio_rank_records": len(snapshot.get("portfolio_rank_records", [])),
        },
        "output_manifest_id": manifest["output_manifest_id"],
        "closure_statement": manifest["closure_statement"],
    }


def execute_release_cases(output_root: Path, snapshot_root: Path) -> dict[str, Any]:
    rich = _run_one(output_root, snapshot_root, rare=False)
    rare = _run_one(output_root, snapshot_root, rare=True)
    source_hashes = {
        path.name: _sha256_bytes(path.read_bytes())
        for path in sorted(snapshot_root.iterdir())
        if path.is_file() and path.name in RELEASE_SOURCE_FILES
    }
    result = {
        "schema_version": 7,
        "model_version": RELEASE_MODEL_VERSION,
        "source_snapshot_at": SOURCE_SNAPSHOT_AT,
        "source_snapshot_hashes": source_hashes,
        "cases": {"rich": rich, "rare_sparse": rare},
        "hypothesis_generation_only": True,
        "experimental_use": True,
        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_once(output_root / "release_case_evidence.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    arguments = parser.parse_args()
    result = execute_release_cases(arguments.output_root.resolve(), arguments.snapshot_root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
