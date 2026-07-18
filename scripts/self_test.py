#!/usr/bin/env python3
"""Focused contract, ranking-cap, and context-isolation tests for schema v5."""

from __future__ import annotations

import tempfile
from pathlib import Path

from build_context_packet import build_packet
from program_contract import (
    HUMAN_OUTCOME_NODE,
    HUMAN_RELEVANCE_LEVELS,
    RANKING_CAPS,
    RANKING_COMPONENTS,
    SCHEMA_VERSION,
)
from program_io import read_json, read_jsonl, write_json, write_jsonl
from program_runtime import _merge_updates, complete_job, initialize, next_action, start_job
from ranking import council_selection, rank_candidates, rank_rows
from validate_program import validate_run, validate_staged_result


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
            "score": 5,
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
        ("REP", "Repurposed", "repurposing_candidate", 60, 7),
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
    assert [row["candidate_id"] for row in ranked] == ["REP", "BENCH", "BASE", "PRE"]
    assert [row["rank_section"] for row in ranked] == [
        "primary_repurposing",
        "target_disease_benchmark",
        "baseline_care",
        "preclinical_hypothesis",
    ]
    assert all(row["rank"] == 1 and row["endpoint_rank"] == 1 for row in ranked)


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
        state["schema_version"] = 4
        plan["schema_version"] = 4
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
    exercise_ranking_caps()
    exercise_class_partition()
    exercise_candidate_isolation()
    exercise_semantic_enum_rejection()
    exercise_source_provenance_union()
    exercise_packet_and_legacy_guards()
    exercise_research_overwrite_guard()
    exercise_invalid_stage_is_not_merged()
    print("SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
