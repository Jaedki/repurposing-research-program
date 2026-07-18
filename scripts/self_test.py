#!/usr/bin/env python3
"""Focused contract, saturation, resilience, ranking, and isolation tests for schema v6."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from build_context_packet import build_packet
from program_contract import (
    AUDIT_QUERY_FAMILIES,
    BASE_QUERY_FAMILIES,
    BRANCH_BUDGET_PER_UNIT,
    COMPOUND_QUERY_FAMILIES,
    GLOBAL_PERSPECTIVES,
    HUMAN_OUTCOME_NODE,
    HUMAN_RELEVANCE_LEVELS,
    MAX_ACTIVE_JOBS,
    PERSPECTIVE_CONTRACTS,
    RANKING_CAPS,
    RANKING_COMPONENTS,
    RETRY_BASE_SECONDS,
    RETRY_DELAY_CAP_SECONDS,
    RETRY_LIMIT,
    SCHEMA_VERSION,
    required_query_families,
)
from program_io import read_json, read_jsonl, write_json, write_jsonl
from program_runtime import _merge_updates, complete_job, fail_job, initialize, next_action, start_job
from ranking import council_selection, rank_candidates, rank_rows
from validate_program import (
    _validate_distinct_perspective_rationales,
    _validate_perspective_rationale,
    _validate_unit_frontier,
    validate_run,
    validate_staged_result,
)


VALID_PERSPECTIVE_RATIONALES = {
    "direct_mechanism": (
        "Lens route [direct_target_or_process_correction]: Syntheticmol directly inhibits the "
        "disease-driving target process to correct pathological signalling. Human-outcome bridge: "
        "That directional correction is linked to improved human disease progression."
    ),
    "phenotype_reversal": (
        "Lens route [phenotype_or_signature_reversal]: Syntheticmol reverses the disease molecular "
        "signature and normalises the pathological phenotype biomarker pattern. Human-outcome bridge: "
        "Normalisation of that pattern is linked to improved clinical function in human disease."
    ),
    "vulnerability_inverse": (
        "Lens route [disease_created_vulnerability_inverse]: Disease creates a stress response "
        "vulnerability and dependency that Syntheticmol protects against and corrects. Human-outcome bridge: "
        "Protection from that dependency is linked to preserved human clinical function."
    ),
    "compensatory_network": (
        "Lens route [parallel_or_compensatory_restoration]: Direct correction is incomplete, while "
        "Syntheticmol activates a parallel bypass network that restores function. Human-outcome bridge: "
        "The compensatory restoration is linked to improved human disease function."
    ),
    "human_genetics_clinical": (
        "Lens route [human_causal_or_intervention_anchor]: A human genetic variant supplies causal target "
        "validation and the Syntheticmol intervention follows that direction. Human-outcome bridge: "
        "The validated intervention direction is linked to a better human therapeutic outcome."
    ),
    "hidden_in_plain_sight": (
        "Lens route [adjacent_or_observed_clinical_signal]: A real-world comorbidity clinical observation "
        "shows reduced disease burden and improved response with Syntheticmol. Human-outcome bridge: "
        "That observed benefit maps to the target human clinical outcome."
    ),
    "natural_compounds": (
        "Lens route [exact_natural_compound_with_independent_route]: The exact plant-derived natural product "
        "Syntheticmol has a source-supported causal mechanism independent of its origin. Human-outcome bridge: "
        "That independent mechanism is linked to improved human disease function."
    ),
}


def _component_scores(source_id: str, human: int, total_target: int) -> dict[str, dict[str, object]]:
    values = {name: 0 for name in RANKING_COMPONENTS}
    values["human_evidence"] = human
    remaining = total_target - human
    order = [name for name in RANKING_COMPONENTS if name not in {"human_evidence", "evidence_independence"}]
    order.append("evidence_independence")
    for name in order:
        maximum = 1 if name == "evidence_independence" else RANKING_COMPONENTS[name]
        values[name] = min(maximum, remaining)
        remaining -= values[name]
    assert remaining == 0
    return {
        name: {
            "score": values[name],
            "rationale": f"Source-linked rationale for {name}.",
            "source_ids": [source_id],
        }
        for name in RANKING_COMPONENTS
    }


def _caps(source_id: str, **applies: bool) -> dict[str, dict[str, object]]:
    return {
        name: {
            "applies": bool(applies.get(name, False)),
            "rationale": f"Evidence-backed assessment of {name}.",
            "source_ids": [source_id],
        }
        for name in RANKING_CAPS
    }


def _candidate_metadata(source_id: str, claim_id: str) -> dict[str, object]:
    return {
        "candidate_class": "repurposing_candidate",
        "candidate_class_source_ids": [source_id],
        "compound_origin": "synthetic_or_semisynthetic",
        "target_endpoint": {
            "endpoint_type": "disease_modifying_clinical",
            "label": "synthetic disease progression",
            "claim_ids": [claim_id],
            "source_ids": [source_id],
        },
        "repurposing_readiness": {
            "score": 50,
            "rationale": "Established human use elsewhere supports a tractable repurposing test.",
            "source_ids": [source_id],
        },
    }


def exercise_human_inputs() -> None:
    for field in ("human_gene", "human_disease", "human_phenotype"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            state = initialize(root, {field: "human case"})
            assert state["schema_version"] == SCHEMA_VERSION
            case = read_json(root / "case.json", {})
            assert case[field] == "human case"
            assert "worm_gene" not in case and "allele_mode" not in case
    with tempfile.TemporaryDirectory() as temporary:
        try:
            initialize(Path(temporary) / "run", {})
        except ValueError as exc:
            assert "human_gene" in str(exc) and "human_disease" in str(exc)
        else:
            raise AssertionError("empty human case was accepted")
    assert HUMAN_OUTCOME_NODE == "CASE_HUMAN_THERAPEUTIC_OUTCOME"


def exercise_perspective_contracts_and_packet_delivery() -> None:
    expected_perspectives = (
        "direct_mechanism",
        "phenotype_reversal",
        "vulnerability_inverse",
        "compensatory_network",
        "human_genetics_clinical",
        "hidden_in_plain_sight",
        "natural_compounds",
    )
    required_contract_fields = {
        "perspective_id",
        "discovery_objective",
        "required_causal_route",
        "required_coverage_areas",
        "prohibited_primary_rationales",
        "required_lens_specific_rationale",
        "distinguishing_boundary",
    }
    assert GLOBAL_PERSPECTIVES == expected_perspectives
    assert tuple(PERSPECTIVE_CONTRACTS) == expected_perspectives
    assert MAX_ACTIVE_JOBS == 1
    assert (RETRY_BASE_SECONDS, RETRY_DELAY_CAP_SECONDS, RETRY_LIMIT) == (30, 900, 6)
    assert required_query_families("broad_evidence", "human_disease_biology") == BASE_QUERY_FAMILIES
    assert required_query_families("decisive_audit", "decisive_claims") == AUDIT_QUERY_FAMILIES

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_disease": "synthetic disease"})
        units = {row["perspective"]: row for row in read_jsonl(root / "research_units.jsonl")}
        plan = read_json(root / "execution_plan.json", {})
        compound_jobs = [row for row in plan["jobs"] if str(row["job_id"]).startswith("CP")]
        assert [row["job_id"] for row in compound_jobs] == [f"CP{i:02d}.research" for i in range(1, 8)]
        assert [row["sequence"] for row in compound_jobs] == list(range(201, 208))
        for index, perspective in enumerate(expected_perspectives, 1):
            contract = PERSPECTIVE_CONTRACTS[perspective]
            assert set(contract) == required_contract_fields
            assert contract["perspective_id"] == perspective
            assert len(contract["required_coverage_areas"]) == 3
            required = required_query_families("compound_perspective", perspective)
            assert required == (
                BASE_QUERY_FAMILIES
                | COMPOUND_QUERY_FAMILIES
                | set(contract["required_coverage_areas"])
            )
            assert set(units[perspective]["planned_query_families"]) == required
            assert set(units[perspective]["coverage_statuses"]) == required
            assert set(units[perspective]["coverage_statuses"].values()) == {"NOT_YET_SEARCHED"}
            manifest_path, _ = build_packet(root, f"CP{index:02d}.research")
            manifest = read_json(manifest_path, {})
            packet = read_json(root / manifest["required_chunks"][0]["path"], {})
            machine = packet["machine_contract"]
            assert machine["compound_perspective_contract"] == json.loads(json.dumps(contract))
            assert machine["required_query_families"] == sorted(required)
            packet_text = (root / manifest["required_chunks"][0]["path"]).read_text(encoding="utf-8")
            for other in set(expected_perspectives) - {perspective}:
                marker = PERSPECTIVE_CONTRACTS[other]["required_lens_specific_rationale"]["route_marker"]
                assert marker not in packet_text


def exercise_perspective_rationale_validation_and_convergence() -> None:
    for perspective, rationale in VALID_PERSPECTIVE_RATIONALES.items():
        errors: list[str] = []
        _validate_perspective_rationale("fixture", perspective, rationale, errors)
        assert not errors, (perspective, errors)

    errors = []
    _validate_perspective_rationale(
        "generic fixture",
        "direct_mechanism",
        "A promising exact compound has relevant evidence and should be repurposed.",
        errors,
    )
    assert any("route marker" in error for error in errors), errors

    wrong_lens = (
        "Lens route [phenotype_or_signature_reversal]: Syntheticmol directly inhibits a disease-driving "
        "target process to correct signalling. Human-outcome bridge: This may improve human disease progression."
    )
    errors = []
    _validate_perspective_rationale("wrong lens fixture", "phenotype_reversal", wrong_lens, errors)
    assert any("phenotype_reversal route" in error for error in errors), errors

    origin_only = (
        "Lens route [exact_natural_compound_with_independent_route]: Syntheticmol is a familiar plant-derived "
        "natural product that is widely available. Human-outcome bridge: It may help human disease outcomes."
    )
    errors = []
    _validate_perspective_rationale("origin-only fixture", "natural_compounds", origin_only, errors)
    assert any("natural_compounds route" in error for error in errors), errors

    units = {
        "CP01": {"perspective": "direct_mechanism"},
        "CP02": {"perspective": "phenotype_reversal"},
    }
    convergent = {
        "OBS_DIRECT": {
            "research_unit_id": "CP01",
            "active_moiety_key": "INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
            "rationale": VALID_PERSPECTIVE_RATIONALES["direct_mechanism"],
        },
        "OBS_PHENOTYPE": {
            "research_unit_id": "CP02",
            "active_moiety_key": "INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
            "rationale": VALID_PERSPECTIVE_RATIONALES["phenotype_reversal"],
        },
    }
    errors = []
    _validate_distinct_perspective_rationales(convergent, units, errors)
    assert not errors, errors

    shared_narrative = (
        " Syntheticmol directly modulates the target process and reverses the disease phenotype signature "
        "to normalise its biomarker pattern. Human-outcome bridge: This is linked to improved human clinical function."
    )
    duplicated = {
        "OBS_DIRECT": {**convergent["OBS_DIRECT"], "rationale": (
            "Lens route [direct_target_or_process_correction]:" + shared_narrative
        )},
        "OBS_PHENOTYPE": {**convergent["OBS_PHENOTYPE"], "rationale": (
            "Lens route [phenotype_or_signature_reversal]:" + shared_narrative
        )},
    }
    errors = []
    _validate_distinct_perspective_rationales(duplicated, units, errors)
    assert any("semantically duplicated" in error for error in errors), errors


def exercise_ranking_caps() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_jsonl(root / "source_corpus.jsonl", [{"source_id": "SRC1"}])
        write_jsonl(
            root / "claim_ledger.jsonl",
            [
                {"claim_id": "CL_HUMAN", "human_relevance": "human_therapeutic_outcome", "direction": "supports_benefit", "calibration": "established", "source_ids": ["SRC1"]},
                {"claim_id": "CL_MODEL", "human_relevance": "animal_model", "direction": "supports_benefit", "calibration": "supported_with_qualifier", "source_ids": ["SRC1"]},
                {"claim_id": "CL_UNRESOLVED", "human_relevance": "human_therapeutic_outcome", "direction": "supports_benefit", "calibration": "supported_with_qualifier", "source_ids": ["SRC1"]},
            ],
        )
        write_jsonl(
            root / "audit_records.jsonl",
            [
                {"subject_type": "claim", "subject_id": "CL_HUMAN", "verdict": "supported"},
                {"subject_type": "claim", "subject_id": "CL_MODEL", "verdict": "qualified"},
                {"subject_type": "claim", "subject_id": "CL_UNRESOLVED", "verdict": "unresolved"},
            ],
        )
        base_model = {
            "assessed": False,
            "score": None,
            "rationale": "No experimental model was supplied; excluded from the human score.",
            "source_ids": [],
        }
        write_jsonl(
            root / "candidate_records.jsonl",
            [
                {
                    **_candidate_metadata("SRC1", "CL_HUMAN"),
                    "candidate_id": "A",
                    "canonical_name": "Human-backed",
                    "decisive_claim_ids": ["CL_HUMAN"],
                    "score_components": _component_scores("SRC1", 22, 80),
                    "cap_assessments": _caps("SRC1"),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": [],
                    "audit_status": "independently_verified",
                },
                {
                    **_candidate_metadata("SRC1", "CL_MODEL"),
                    "candidate_id": "B",
                    "canonical_name": "Model-only",
                    "decisive_claim_ids": ["CL_MODEL"],
                    "score_components": _component_scores("SRC1", 15, 86),
                    "cap_assessments": _caps("SRC1", absent_human_evidence=True),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": [],
                    "audit_status": "qualified",
                },
                {
                    **_candidate_metadata("SRC1", "CL_UNRESOLVED"),
                    "candidate_id": "C",
                    "canonical_name": "Direction-unclear",
                    "decisive_claim_ids": ["CL_UNRESOLVED"],
                    "score_components": _component_scores("SRC1", 22, 80),
                    "cap_assessments": _caps("SRC1", unresolved_direction=True, absent_human_evidence=True),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": ["CL_UNRESOLVED"],
                    "audit_status": "conflicted",
                },
                {
                    **_candidate_metadata("SRC1", "CL_HUMAN"),
                    "candidate_id": "D",
                    "canonical_name": "Safety-mismatch",
                    "decisive_claim_ids": ["CL_HUMAN"],
                    "score_components": _component_scores("SRC1", 22, 80),
                    "cap_assessments": _caps("SRC1", serious_safety_mismatch=True),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": [],
                    "audit_status": "independently_verified",
                },
                {
                    **_candidate_metadata("SRC1", "CL_HUMAN"),
                    "candidate_id": "E",
                    "canonical_name": "Exposure-infeasible",
                    "decisive_claim_ids": ["CL_HUMAN"],
                    "score_components": _component_scores("SRC1", 22, 80),
                    "cap_assessments": _caps("SRC1", infeasible_exposure=True),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": [],
                    "audit_status": "independently_verified",
                },
                {
                    **_candidate_metadata("SRC1", "CL_UNRESOLVED"),
                    "candidate_id": "F",
                    "canonical_name": "Low-conflict",
                    "decisive_claim_ids": ["CL_UNRESOLVED"],
                    "score_components": _component_scores("SRC1", 5, 10),
                    "cap_assessments": _caps("SRC1", unresolved_direction=True, absent_human_evidence=True),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": ["CL_UNRESOLVED"],
                    "audit_status": "conflicted",
                },
                {
                    **_candidate_metadata("SRC1", "CL_HUMAN"),
                    "candidate_id": "G",
                    "canonical_name": "Low-no-conflict",
                    "decisive_claim_ids": ["CL_HUMAN"],
                    "score_components": _component_scores("SRC1", 5, 5),
                    "cap_assessments": _caps("SRC1"),
                    "experimental_model_suitability": base_model,
                    "material_conflicts": [],
                    "audit_status": "independently_verified",
                },
            ],
        )
        ranked = rank_candidates(root)
        assert [row["candidate_id"] for row in ranked] == ["A", "B", "D", "C", "E", "F", "G"]
        assert ranked[0]["total_score"] == 80 and ranked[0]["rank"] == 1
        assert ranked[1]["raw_score"] == 86 and ranked[1]["total_score"] == 40
        assert ranked[1]["applied_cap"] == {"maximum": 40, "reasons": ["absent_human_evidence"]}
        assert ranked[1]["experimental_model_suitability"]["score"] is None
        assert {row["candidate_id"]: row["total_score"] for row in ranked} == {
            "A": 80,
            "B": 40,
            "C": 25,
            "D": 30,
            "E": 20,
            "F": 10,
            "G": 5,
        }
        assert council_selection(ranked) == ["A", "B", "D", "C", "E", "F"]

        candidates = read_jsonl(root / "candidate_records.jsonl")
        candidates[0]["cap_assessments"]["absent_human_evidence"]["applies"] = True
        try:
            rank_rows(
                candidates,
                read_jsonl(root / "source_corpus.jsonl"),
                read_jsonl(root / "claim_ledger.jsonl"),
                read_jsonl(root / "audit_records.jsonl"),
            )
        except ValueError as exc:
            assert "absent human evidence cap must not apply" in str(exc)
        else:
            raise AssertionError("false-positive absent-human-evidence cap was accepted")

        candidates = read_jsonl(root / "candidate_records.jsonl")
        candidates[0]["cap_assessments"]["unresolved_direction"]["applies"] = True
        try:
            rank_rows(
                candidates,
                read_jsonl(root / "source_corpus.jsonl"),
                read_jsonl(root / "claim_ledger.jsonl"),
                read_jsonl(root / "audit_records.jsonl"),
            )
        except ValueError as exc:
            assert "unresolved direction cap must not apply" in str(exc)
        else:
            raise AssertionError("false-positive unresolved-direction cap was accepted")

        candidates = read_jsonl(root / "candidate_records.jsonl")
        candidates[0]["causal_paths"] = [{"edge_ids": ["EDGE_AMBIG"]}]
        try:
            rank_rows(
                candidates,
                read_jsonl(root / "source_corpus.jsonl"),
                read_jsonl(root / "claim_ledger.jsonl"),
                read_jsonl(root / "audit_records.jsonl"),
                [{"edge_id": "EDGE_AMBIG", "directionality": "ambiguous"}],
            )
        except ValueError as exc:
            assert "unresolved direction cap must apply" in str(exc)
        else:
            raise AssertionError("ambiguous edge avoided the unresolved-direction cap")


def exercise_class_partition() -> None:
    source_rows = [{"source_id": "SRC1"}]
    claim_rows = [{
        "claim_id": "CL1",
        "human_relevance": "human_therapeutic_outcome",
        "direction": "supports_benefit",
        "calibration": "established",
        "source_ids": ["SRC1"],
    }]
    audit_rows = [{"subject_type": "claim", "subject_id": "CL1", "verdict": "supported"}]
    model = {
        "assessed": False,
        "score": None,
        "rationale": "No model supplied.",
        "source_ids": [],
    }
    specifications = (
        ("REP", "Repurposed", "repurposing_candidate", 60, 70),
        ("REP_LOW", "Repurposed lower readiness", "repurposing_candidate", 60, 50),
        ("BENCH", "Target asset", "target_disease_investigational", 80, 0),
        ("BASE", "Replacement", "supportive_standard_care", 85, 0),
        ("PRE", "Tool compound", "preclinical_hypothesis", 75, 0),
    )
    rows = []
    for candidate_id, name, candidate_class, raw_score, readiness in specifications:
        metadata = _candidate_metadata("SRC1", "CL1")
        metadata["candidate_class"] = candidate_class
        metadata["repurposing_readiness"]["score"] = readiness if candidate_class == "repurposing_candidate" else None
        if candidate_class == "supportive_standard_care":
            metadata["target_endpoint"]["endpoint_type"] = "complication_management"
            metadata["target_endpoint"]["label"] = "synthetic complication"
        rows.append({
            **metadata,
            "candidate_id": candidate_id,
            "canonical_name": name,
            "decisive_claim_ids": ["CL1"],
            "score_components": _component_scores("SRC1", 20, raw_score),
            "cap_assessments": _caps("SRC1"),
            "experimental_model_suitability": model,
            "material_conflicts": [],
            "audit_status": "independently_verified",
        })
    ranked = rank_rows(rows, source_rows, claim_rows, audit_rows)
    assert [row["candidate_id"] for row in ranked] == ["REP", "REP_LOW", "BENCH", "BASE", "PRE"]
    assert [row["rank_section"] for row in ranked] == [
        "primary_repurposing",
        "primary_repurposing",
        "target_disease_benchmark",
        "baseline_care",
        "preclinical_hypothesis",
    ]
    assert [row["rank"] for row in ranked] == [1, 2, 1, 1, 1]
    assert [row["endpoint_rank"] for row in ranked] == [1, 2, 1, 1, 1]


def exercise_candidate_isolation() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        write_jsonl(
            root / "candidate_observations.jsonl",
            [{"observation_id": "MUST_NOT_LEAK", "canonical_name": "Hiddenmol"}],
        )
        write_jsonl(
            root / "claim_ledger.jsonl",
            [
                {"claim_id": "CP01_SECRET_CLAIM", "statement": "Hiddenmol is a prior perspective candidate.", "source_ids": []},
                {"claim_id": "BROAD_SHARED_CLAIM", "statement": "Broad disease biology.", "source_ids": ["SRC_SHARED"]},
            ],
        )
        write_jsonl(
            root / "evidence_graph.jsonl",
            [
                {"edge_id": "CP01_SECRET_EDGE", "claim_ids": ["CP01_SECRET_CLAIM"], "effect": "Hiddenmol benefit"},
                {"edge_id": "CHEMICAL_BROAD_EDGE", "from_node": "CHEM:SECRET", "to_node": "PATHWAY", "claim_ids": ["BROAD_SHARED_CLAIM"], "effect": "Hiddenmol chemical edge"},
                {"edge_id": "BROAD_EDGE", "from_node": "DISEASE", "to_node": "PATHWAY", "claim_ids": ["BROAD_SHARED_CLAIM"], "effect": "broad context"},
            ],
        )
        write_jsonl(root / "search_log.jsonl", [{
            "query_id": "Q_BE01",
            "research_unit_id": "BE01",
            "produced_claim_ids": ["BROAD_SHARED_CLAIM"],
        }])
        write_jsonl(root / "source_corpus.jsonl", [{
            "source_id": "SRC_SHARED",
            "canonical_identifier": "PMID:123",
            "title": "Broad shared source",
            "verification_scope": "broad biology",
            "supported_claim_ids": ["BROAD_SHARED_CLAIM", "CP01_SECRET_CLAIM"],
            "discovered_by_units": ["BE01", "CP01"],
            "discovery_query_ids": ["Q_BE01", "Q_CP01_SECRET"],
        }])
        perspective_manifest, _ = build_packet(root, "CP02.research")
        perspective = read_json(perspective_manifest, {})
        perspective_text = "".join(
            (root / chunk["path"]).read_text(encoding="utf-8")
            for chunk in perspective["required_chunks"]
        )
        assert "MUST_NOT_LEAK" not in perspective_text
        assert "Hiddenmol" not in perspective_text
        assert "CP01_SECRET_CLAIM" not in perspective_text
        merge_manifest, _ = build_packet(root, "MERGE01")
        merge = read_json(merge_manifest, {})
        merge_text = "".join(
            (root / chunk["path"]).read_text(encoding="utf-8")
            for chunk in merge["required_chunks"]
        )
        assert "MUST_NOT_LEAK" in merge_text
        assert "Hiddenmol" in merge_text
        assert "raw_payload" not in perspective_text
        assert HUMAN_RELEVANCE_LEVELS.issubset(set(read_json(
            root / perspective["required_chunks"][0]["path"], {}
        )["machine_contract"]["controlled_values"]["human_relevance"]))


def exercise_semantic_enum_rejection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_disease": "synthetic disease"})
        write_jsonl(
            root / "claim_ledger.jsonl",
            [{
                "claim_id": "CL_BAD_ENUM",
                "topic": "bad enum fixture",
                "statement": "A structurally complete claim still needs a controlled relevance tier.",
                "claim_type": "fixture",
                "source_ids": ["MISSING_SOURCE"],
                "calibration": "plausible_inference",
                "human_relevance": "direct human evidence in prose",
                "direction": "supports_benefit",
                "scope": "fixture",
                "contrary_claim_ids": [],
                "supersedes_claim_ids": [],
                "audit_status": "unreviewed",
                "audit_note": "",
            }],
        )
        write_jsonl(
            root / "source_corpus.jsonl",
            [
                {"source_id": "SRC_DUP_1", "canonical_identifier": "PMID:12345"},
                {"source_id": "SRC_DUP_2", "canonical_identifier": "PMID:12345#section"},
            ],
        )
        errors = validate_run(root)
        assert any("CL_BAD_ENUM: invalid human_relevance" in error for error in errors), errors
        assert any("duplicate canonical source identity" in error for error in errors), errors
        assert any("article section" in error for error in errors), errors


def exercise_source_provenance_union() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_jsonl(root / "source_corpus.jsonl", [{
            "source_id": "SRC1",
            "canonical_identifier": "PMID:1",
            "discovered_by_units": ["BE01"],
            "discovery_query_ids": ["Q1"],
            "supported_claim_ids": ["CL1"],
        }])
        _merge_updates(root, {"source_corpus.jsonl": [{
            "source_id": "SRC1",
            "canonical_identifier": "PMID:1",
            "discovered_by_units": ["CP01"],
            "discovery_query_ids": ["Q2"],
            "supported_claim_ids": ["CL2"],
        }]})
        source = read_jsonl(root / "source_corpus.jsonl")[0]
        assert source["discovered_by_units"] == ["BE01", "CP01"]
        assert source["discovery_query_ids"] == ["Q1", "Q2"]
        assert source["supported_claim_ids"] == ["CL1", "CL2"]


def exercise_frontier_budget_and_materiality() -> None:
    searches = {
        f"Q{index}": {"research_unit_id": "U1"}
        for index in range(1, BRANCH_BUDGET_PER_UNIT + 1)
    }
    frontier = [
        {
            "branch_id": f"B{index}",
            "branch_order": index,
            "causal_route": f"Distinct causal route {index}",
            "distinct_causal_route": True,
            "human_or_candidate_relevance": True,
            "already_covered": False,
            "materiality_score": 51,
            "decision": "expanded",
            "query_ids": [f"Q{index}"],
            "source_ids": [],
            "rationale": "The route is distinct, relevant, material, and within budget.",
        }
        for index in range(1, BRANCH_BUDGET_PER_UNIT + 1)
    ]
    frontier.append({
        "branch_id": "B_BUDGET",
        "branch_order": BRANCH_BUDGET_PER_UNIT + 1,
        "causal_route": "Additional material route",
        "distinct_causal_route": True,
        "human_or_candidate_relevance": True,
        "already_covered": False,
        "materiality_score": 100,
        "decision": "closed_budget_exhausted",
        "query_ids": [],
        "source_ids": [],
        "rationale": "The deterministic branch budget is exhausted.",
    })
    frontier.append({
        "branch_id": "B_THRESHOLD",
        "branch_order": BRANCH_BUDGET_PER_UNIT + 2,
        "causal_route": "Threshold-boundary route",
        "distinct_causal_route": True,
        "human_or_candidate_relevance": True,
        "already_covered": False,
        "materiality_score": 50,
        "decision": "closed_immaterial",
        "query_ids": [],
        "source_ids": [],
        "rationale": "Materiality must exceed, not equal, the threshold.",
    })
    unit = {
        "branch_budget": BRANCH_BUDGET_PER_UNIT,
        "evidence_frontier": frontier,
        "frontier_exhausted": True,
    }
    errors: list[str] = []
    _validate_unit_frontier("U1", unit, searches, {}, errors)
    assert not errors, errors
    unit["evidence_frontier"][-2]["decision"] = "expanded"
    errors = []
    _validate_unit_frontier("U1", unit, searches, {}, errors)
    assert any("closed_budget_exhausted" in error for error in errors), errors


def exercise_not_yet_searched_gate() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_disease": "synthetic disease"})
        units = read_jsonl(root / "research_units.jsonl")
        unit = next(row for row in units if row["perspective"] == "phenotype_reversal")
        assert "lens_phenotype_reversal_evidence" in unit["planned_query_families"]
        unit["status"] = "complete"
        unit["worker_agent_id"] = "fixture-agent"
        unit["closure_basis"] = "Fixture closure that must not override machine coverage."
        unit["completed_query_families"] = list(unit["planned_query_families"])
        unit["frontier_exhausted"] = True
        write_jsonl(root / "research_units.jsonl", units)
        errors = validate_run(root)
        assert any("NOT_YET_SEARCHED" in error for error in errors), errors


def exercise_bounded_retry_and_stale_detection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        delays = []
        for retry_number in range(1, 8):
            action = next_action(root)
            if action["action"] == "wait_for_retry":
                plan = read_json(root / "execution_plan.json", {})
                job = next(row for row in plan["jobs"] if row["job_id"] == action["job_id"])
                job["retry_not_before"] = "2000-01-01T00:00:00+00:00"
                write_json(root / "execution_plan.json", plan)
                action = next_action(root)
            attempt = start_job(root, action["job_id"], "retry-agent")
            if retry_number == 1:
                attempts = read_jsonl(root / "job_attempts.jsonl")
                attempts[-1]["last_progress_at"] = "2000-01-01T00:00:00+00:00"
                write_jsonl(root / "job_attempts.jsonl", attempts)
                stale = next_action(root)
                assert stale["stale_run_detected"] is True
            failed = fail_job(root, action["job_id"], "tpm_exhaustion", None, "synthetic TPM limit")
            if retry_number <= 6:
                delays.append(failed["retry_delay_seconds"])
            else:
                assert failed["action"] == "blocked"
            assert read_jsonl(root / "job_attempts.jsonl")[-1]["attempt_id"] == attempt["attempt_id"]
        assert delays == [30, 60, 120, 240, 480, 900]


def exercise_packet_and_legacy_guards() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        action = next_action(root)
        manifest = read_json(root / action["packet_manifest_path"], {})
        chunk_path = root / manifest["required_chunks"][0]["path"]
        chunk_path.write_bytes(chunk_path.read_bytes() + b" ")
        try:
            next_action(root)
        except ValueError as exc:
            assert "packet chunk integrity" in str(exc)
        else:
            raise AssertionError("tampered packet was dispatched")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        state = read_json(root / "program_state.json", {})
        plan = read_json(root / "execution_plan.json", {})
        state["schema_version"] = SCHEMA_VERSION - 1
        plan["schema_version"] = SCHEMA_VERSION - 1
        write_json(root / "program_state.json", state)
        write_json(root / "execution_plan.json", plan)
        before = (root / "program_state.json").read_bytes(), (root / "execution_plan.json").read_bytes()
        try:
            next_action(root)
        except ValueError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("legacy run was mutated instead of rejected")
        after = (root / "program_state.json").read_bytes(), (root / "execution_plan.json").read_bytes()
        assert before == after

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        action = next_action(root)
        attempt_id = "BE01.research.attempt001"
        write_jsonl(root / "job_attempts.jsonl", [{
            "attempt_id": attempt_id,
            "job_id": action["job_id"],
            "agent_id": "interrupted-start-agent",
            "packet_hash": action["packet_hash"],
            "packet_manifest_path": action["packet_manifest_path"],
            "expected_result_path": f"staging/{attempt_id}/result.json",
            "status": "running",
            "started_at": "2026-07-18T12:00:00+00:00",
            "last_progress_at": "2026-07-18T12:00:00+00:00",
            "finished_at": "",
            "failure_kind": "",
            "retry_reason": "",
        }])
        resumed = next_action(root)
        assert resumed["action"] == "resume_active_job" and resumed["attempt_id"] == attempt_id
        duplicate = start_job(root, action["job_id"], "interrupted-start-agent")
        assert duplicate["duplicate_start_prevented"] is True


def exercise_research_overwrite_guard() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_gene": "GENE1"})
        claim = {
            "claim_id": "CL_EXISTING",
            "topic": "fixture",
            "statement": "Existing claim.",
            "claim_type": "fixture",
            "source_ids": ["SRC_MISSING"],
            "calibration": "plausible_inference",
            "human_relevance": "mechanistic_inference",
            "direction": "neutral_context",
            "scope": "fixture",
            "contrary_claim_ids": [],
            "supersedes_claim_ids": [],
            "audit_status": "unreviewed",
            "audit_note": "",
        }
        write_jsonl(root / "claim_ledger.jsonl", [claim])
        plan = read_json(root / "execution_plan.json", {})
        job = next(row for row in plan["jobs"] if row["job_id"] == "BE01.research")
        errors = validate_staged_result(
            root,
            job,
            {"ledger_updates": {"claim_ledger.jsonl": [{**claim, "statement": "Overwritten."}]}},
            "agent-1",
        )
        assert any("research jobs may not overwrite" in error for error in errors), errors


def exercise_invalid_stage_is_not_merged() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_phenotype": "progressive weakness"})
        action = next_action(root)
        attempt = start_job(root, action["job_id"], "invalid-stage-agent")
        write_json(
            root / attempt["expected_result_path"],
            {
                "job_id": action["job_id"],
                "packet_hash": action["packet_hash"],
                "all_chunks_processed": True,
                "outcome": "completed",
                "closure_basis": "Synthetic invalid fixture.",
                "ledger_updates": {"source_corpus.jsonl": [{"source_id": "BROKEN"}]},
            },
        )
        try:
            complete_job(root, action["job_id"])
        except ValueError as exc:
            assert "failed validation" in str(exc)
        else:
            raise AssertionError("invalid staged data was accepted")
        assert read_jsonl(root / "source_corpus.jsonl") == []
        assert read_json(root / "program_state.json", {})["active_job_id"] == action["job_id"]


def main() -> int:
    exercise_human_inputs()
    exercise_perspective_contracts_and_packet_delivery()
    exercise_perspective_rationale_validation_and_convergence()
    exercise_ranking_caps()
    exercise_class_partition()
    exercise_candidate_isolation()
    exercise_semantic_enum_rejection()
    exercise_source_provenance_union()
    exercise_frontier_budget_and_materiality()
    exercise_not_yet_searched_gate()
    exercise_bounded_retry_and_stale_detection()
    exercise_packet_and_legacy_guards()
    exercise_research_overwrite_guard()
    exercise_invalid_stage_is_not_merged()
    print("SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
