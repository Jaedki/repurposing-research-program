#!/usr/bin/env python3
"""Synthetic end-to-end test for schema v6, saturation, retry, checkpoint, and resume."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Any

from build_final_outputs import build_outputs
from build_search_record import build_search_record
from compact_source_payload import compact_payload
from program_contract import HUMAN_OUTCOME_NODE, RANKING_CAPS, RANKING_COMPONENTS
from program_io import read_json, read_jsonl, write_json
from program_runtime import (
    complete_job,
    fail_job,
    initialize,
    next_action,
    recover_active,
    resume_action,
    start_job,
    validate_result,
)
from validate_program import validate_run


STRUCTURE_KEY = "INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C"
CHEMICAL_NODE = f"CHEM:{STRUCTURE_KEY}"


def _write_receipt(root: Path, query_id: str, canonical_id: str, title: str) -> tuple[str, dict[str, Any]]:
    receipt = compact_payload(
        [{
            "canonical_identifier": canonical_id,
            "identifier_type": "PMID",
            "title": title,
            "year": 2026,
            "source_kind": "primary_research",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{canonical_id.split(':')[-1]}/",
        }],
        "verification",
        query_id,
    )
    path = root / "raw_sources" / f"{query_id}.json"
    write_json(path, receipt, compact=True)
    return str(path.relative_to(root)), receipt


def _research_updates(
    root: Path,
    job: dict[str, Any],
    agent_id: str,
    *,
    candidate: bool = False,
    audit: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    unit_id = str(job["unit_id"])
    unit = next(row for row in read_jsonl(root / "research_units.jsonl") if row["unit_id"] == unit_id)
    families = list(unit["planned_query_families"])
    source_id = f"SRC_{unit_id}"
    unit_number = int("".join(ch for ch in unit_id if ch.isdigit()) or 1)
    unit_family = 11 if unit_id.startswith("BE") else 12 if unit_id.startswith("CP") else 13
    canonical = f"PMID:{unit_family * 1000 + unit_number}"
    title = f"Verified human therapeutic evidence for {unit_id}"
    claim_id = "CL_CANDIDATE" if candidate or audit else f"CL_{unit_id}"
    observation_id = "OBS_CANDIDATE"
    emits_claim = candidate or audit or unit_id.startswith("BE")
    searches: list[dict[str, Any]] = []
    query_ids: list[str] = []
    first_receipt = ""
    first_hash = ""
    for family in families:
        query_id = f"Q_{unit_id}_{family}"
        receipt_path, receipt = _write_receipt(root, query_id, canonical, title)
        query_ids.append(query_id)
        first_receipt = first_receipt or receipt_path
        first_hash = first_hash or str(receipt["records"][0]["compact_record_hash"])
        produced_claims = [claim_id] if emits_claim and (audit or family == families[0]) else []
        produced_observations = [observation_id] if candidate and family == "exact_compound" else []
        searches.append(
            build_search_record(
                root,
                query_id=query_id,
                research_unit_id=unit_id,
                query_family=family,
                resource="PubMed",
                query=f"human disease therapeutic {family} {unit_id}",
                receipt_paths=[receipt_path],
                continuation_tokens=[],
                acquired_source_ids=[source_id],
                verified_source_ids=[source_id],
                retained_source_ids=[source_id],
                executed_by_agent_id=agent_id,
                origin_job_id=str(job["job_id"]),
                coverage_status="FOUND",
                closure_note="Every retrieved page and the predeclared branch were screened; no open continuation remains.",
                produced_claim_ids=produced_claims,
                produced_observation_ids=produced_observations,
            )
        )
    source = {
        "source_id": source_id,
        "canonical_identifier": canonical,
        "identifier_type": "PMID",
        "title": title,
        "year": 2026,
        "source_kind": "primary_research",
        "source_family": "literature",
        "discovered_by_units": [unit_id],
        "discovery_query_ids": query_ids,
        "metadata_verified": True,
        "screen_decision": "include",
        "exclusion_reason": "",
        "original_acquired": True,
        "original_pointer": f"https://pubmed.ncbi.nlm.nih.gov/{canonical.split(':')[-1]}/",
        "content_verified": True,
        "verification_method": "synthetic full-record verification",
        "verification_scope": f"decisive content for {unit_id}",
        "supported_claim_ids": [claim_id] if emits_claim else [],
        "compaction_receipt_path": first_receipt,
        "compaction_record_hash": first_hash,
    }
    updates: dict[str, list[dict[str, Any]]] = {
        "source_corpus.jsonl": [source],
        "search_log.jsonl": searches,
    }
    if audit:
        existing_claim = next(row for row in read_jsonl(root / "claim_ledger.jsonl") if row["claim_id"] == claim_id)
        existing_edge = next(row for row in read_jsonl(root / "evidence_graph.jsonl") if row["edge_id"] == "EDGE_CANDIDATE")
        candidate_row = read_jsonl(root / "candidate_records.jsonl")[0]
        updated_claim = {
            **existing_claim,
            "source_ids": list(dict.fromkeys(existing_claim["source_ids"] + [source_id])),
            "audit_status": "independently_verified",
            "audit_note": "Independently checked against primary human evidence and counterevidence searches.",
        }
        updated_edge = {**existing_edge, "audit_status": "independently_verified"}
        updated_candidate = {**candidate_row, "audit_status": "independently_verified"}
        updates.update(
            {
                "claim_ledger.jsonl": [updated_claim],
                "evidence_graph.jsonl": [updated_edge],
                "candidate_records.jsonl": [updated_candidate],
                "audit_records.jsonl": [
                    {
                        "audit_id": "AUDIT_CL_CANDIDATE",
                        "subject_type": "claim",
                        "subject_id": claim_id,
                        "auditor_agent_id": agent_id,
                        "checked_source_ids": [source_id],
                        "independent_search_ids": query_ids,
                        "verdict": "supported",
                        "rationale": "The primary human record supports the claimed direction and the countersearch found no refuting evidence.",
                        "completed_at": "2026-07-17T12:00:00+00:00",
                    }
                ],
            }
        )
        return updates
    claim = {
        "claim_id": claim_id,
        "topic": "human therapeutic mechanism",
        "statement": (
            "Syntheticmol is directionally linked to human therapeutic benefit."
            if candidate
            else f"Human evidence defines the {unit['perspective']} branch."
        ),
        "claim_type": "primary_evidence",
        "source_ids": [source_id],
        "calibration": "supported_with_qualifier",
        "human_relevance": "human_therapeutic_outcome" if candidate else "human_observational_or_genetic",
        "direction": "supports_benefit" if candidate else "neutral_context",
        "scope": "human disease context",
        "contrary_claim_ids": [],
        "supersedes_claim_ids": [],
        "audit_status": "unreviewed",
        "audit_note": "",
    }
    if unit_id.startswith("BE") or candidate:
        updates["claim_ledger.jsonl"] = [claim]
    if candidate:
        edge = {
            "edge_id": "EDGE_CANDIDATE",
            "from_node": CHEMICAL_NODE,
            "to_node": HUMAN_OUTCOME_NODE,
            "relation": "may_improve",
            "effect": "therapeutic benefit",
            "directionality": "supports_benefit",
            "claim_ids": [claim_id],
            "contrary_edge_ids": [],
            "supersedes_edge_ids": [],
            "audit_status": "unreviewed",
            "uncertainty": "Clinical magnitude is unknown.",
        }
        observation = {
            "observation_id": observation_id,
            "research_unit_id": unit_id,
            "canonical_name": "Syntheticmol",
            "canonical_identifier": "CHEMBL123",
            "registry_identifiers": {"ChEMBL": "CHEMBL123", "PubChem": "123"},
            "structure_identity_key": STRUCTURE_KEY,
            "active_moiety_key": STRUCTURE_KEY,
            "active_moiety_source_ids": [source_id],
            "active_moiety_rationale": "The exact registered structure is the active moiety.",
            "chemical_node_id": CHEMICAL_NODE,
            "identity_source_ids": [source_id],
            "mode_of_action": "Selective pathway modulator",
            "claim_ids": [claim_id],
            "edge_ids": ["EDGE_CANDIDATE"],
            "rationale": (
                "Lens route [direct_target_or_process_correction]: Syntheticmol directly inhibits the "
                "disease-driving target process to correct pathological signalling. Human-outcome bridge: "
                "That directional correction is linked to improved human disease progression."
            ),
            "rationale_source_ids": [source_id],
            "uncertainty": "Prospective therapeutic validation is still required.",
        }
        updates["evidence_graph.jsonl"] = [edge]
        updates["candidate_observations.jsonl"] = [observation]
    return updates


def _candidate(root: Path) -> dict[str, Any]:
    observation = read_jsonl(root / "candidate_observations.jsonl")[0]
    source_id = observation["identity_source_ids"][0]
    components = {
        name: {
            "score": min(maximum, {"human_evidence": 22, "mechanistic_fit": 17, "clinical_translatability": 11,
                                    "safety_tolerability": 12, "exposure_feasibility": 8,
                                    "evidence_independence": 1, "endpoint_specificity": 9}[name]),
            "rationale": f"Evidence-backed assessment for {name.replace('_', ' ')}.",
            "source_ids": [source_id],
        }
        for name, maximum in RANKING_COMPONENTS.items()
    }
    caps = {
        name: {
            "applies": False,
            "rationale": f"The available source does not establish the {name.replace('_', ' ')} cap condition.",
            "source_ids": [source_id],
        }
        for name in RANKING_CAPS
    }
    return {
        "candidate_id": "CANDIDATE_001",
        "canonical_name": observation["canonical_name"],
        "canonical_identifier": observation["canonical_identifier"],
        "registry_identifiers": observation["registry_identifiers"],
        "structure_identity_key": STRUCTURE_KEY,
        "active_moiety_key": STRUCTURE_KEY,
        "active_moiety_source_ids": [source_id],
        "active_moiety_rationale": "The sole formulation structure is the active moiety.",
        "formulation_structure_keys": [STRUCTURE_KEY],
        "chemical_node_id": CHEMICAL_NODE,
        "identity_source_ids": [source_id],
        "identity_verified": True,
        "observation_ids": [observation["observation_id"]],
        "source_research_unit_ids": [observation["research_unit_id"]],
        "causal_paths": [
            {
                "path_id": "PATH_001",
                "edge_ids": ["EDGE_CANDIDATE"],
                "claim_ids": ["CL_CANDIDATE"],
                "start_node": CHEMICAL_NODE,
                "end_node": HUMAN_OUTCOME_NODE,
                "expected_direction": "therapeutic_benefit",
                "target_endpoint": "synthetic disease progression",
            }
        ],
        "mode_of_action": observation["mode_of_action"],
        "human_outcome": HUMAN_OUTCOME_NODE,
        "candidate_class": "target_disease_investigational",
        "candidate_class_source_ids": [source_id],
        "compound_origin": "synthetic_or_semisynthetic",
        "target_endpoint": {
            "endpoint_type": "disease_modifying_clinical",
            "label": "synthetic disease progression",
            "claim_ids": ["CL_CANDIDATE"],
            "source_ids": [source_id],
        },
        "repurposing_readiness": {
            "score": None,
            "rationale": "Merge classified the asset as target-disease-specific; council must challenge this.",
            "source_ids": [source_id],
        },
        "rationale": observation["rationale"],
        "rationale_source_ids": [source_id],
        "uncertainty": observation["uncertainty"],
        "decisive_claim_ids": ["CL_CANDIDATE"],
        "audit_status": "unreviewed",
        "score_components": components,
        "cap_assessments": caps,
        "experimental_model_suitability": {
            "assessed": False,
            "score": None,
            "rationale": "No experimental model was supplied; this does not affect the human therapeutic score.",
            "source_ids": [],
        },
        "material_conflicts": [],
        "raw_score": 0,
        "total_score": 0,
        "applied_cap": {"maximum": 100, "reasons": []},
        "rank_section": "primary_repurposing",
        "rank": 0,
        "endpoint_rank": 0,
        "ranking_version": "human-therapeutic-v2",
        "council_status": "pending",
    }


def run_integration() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        initialize(root, {"human_disease": "synthetic neurodevelopmental disorder"})
        agents = 0
        retried = False
        recovered = False
        checkpoints = 0
        while True:
            action = next_action(root)
            if action["action"] == "finalize":
                break
            if action["action"] == "checkpoint":
                checkpoints += 1
                resume_action(root)
                continue
            if action["action"] == "wait_for_retry":
                plan = read_json(root / "execution_plan.json", {})
                next(row for row in plan["jobs"] if row["job_id"] == action["job_id"])["retry_not_before"] = (
                    "2000-01-01T00:00:00+00:00"
                )
                write_json(root / "execution_plan.json", plan)
                continue
            assert action["action"] == "start_agent", action
            agents += 1
            agent_id = action.get("assigned_agent_id") or f"agent-{agents:03d}"
            attempt = start_job(root, action["job_id"], agent_id)
            if action["job_id"] == "BE01.research" and not retried:
                retry = fail_job(root, action["job_id"], "rate_limit", 1, "synthetic throttle")
                assert retry["action"] == "wait_for_retry"
                assert retry["retry_count"] == 1 and retry["retry_delay_seconds"] == 30
                assert retry["retry_reason"] == "rate_limit"
                retried = True
                continue
            if action["job_id"] == "BE02.research" and not recovered:
                attempt = recover_active(root, "agent-recovered", "synthetic task loss")
                agent_id = "agent-recovered"
                recovered = True
            plan = read_json(root / "execution_plan.json", {})
            job = next(row for row in plan["jobs"] if row["job_id"] == action["job_id"])
            result: dict[str, Any] = {
                "job_id": job["job_id"],
                "packet_hash": job["packet_hash"],
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {},
            }
            if job.get("unit_id"):
                result["evidence_frontier"] = []
                result["frontier_exhausted"] = True
            if job["kind"] == "research":
                result["closure_basis"] = "All predeclared branches, counterevidence, citations, and continuations were resolved."
                if job["unit_id"].startswith("CP"):
                    result["candidate_exclusions"] = []
                result["ledger_updates"] = _research_updates(
                    root,
                    job,
                    agent_id,
                    candidate=job["unit_id"] == "CP01",
                )
            elif job["kind"] == "merge":
                result["ledger_updates"] = {"candidate_records.jsonl": [_candidate(root)]}
            elif job["kind"] == "decisive_audit":
                result["closure_basis"] = "Every decisive claim received independent source and counterevidence checks."
                result["ledger_updates"] = _research_updates(root, job, agent_id, audit=True)
            elif job["kind"] == "council":
                candidate = read_jsonl(root / "candidate_records.jsonl")[0]
                source_id = candidate["rationale_source_ids"][0]
                candidate["candidate_class"] = "repurposing_candidate"
                candidate["repurposing_readiness"] = {
                    "score": 80,
                    "rationale": "Existing human use outside the target disease supports near-term repurposing.",
                    "source_ids": [source_id],
                }
                result["ledger_updates"] = {
                    "candidate_records.jsonl": [candidate],
                    "council_records.jsonl": [
                        {
                            "candidate_id": candidate["candidate_id"],
                            "review_reason": "leader",
                            "reviewer_agent_id": agent_id,
                            "reviewed_claim_ids": candidate["decisive_claim_ids"],
                            "checked_source_ids": [source_id],
                            "candidate_class": candidate["candidate_class"],
                            "target_endpoint_type": candidate["target_endpoint"]["endpoint_type"],
                            "candidate_class_assessment": "Corrected to repurposing_candidate because established human use predates target-disease testing.",
                            "endpoint_assessment": "The scored endpoint is a disease-modifying clinical outcome.",
                            "disposition": "retain",
                            "rationale": "The leading candidate remains coherent after a focused mechanism, safety, and exposure review.",
                            "unresolved_conflicts": [],
                            "audit_status": "reviewed",
                        }
                    ]
                }
            else:
                raise AssertionError(job["kind"])
            result_path = root / str(attempt["expected_result_path"])
            write_json(result_path, result)
            if job["job_id"] == "BE03.research":
                validated = validate_result(root, job["job_id"])
                assert validated["status"] == "valid" and validated["cached_validation"] is False
                cached = validate_result(root, job["job_id"])
                assert cached["cached_validation"] is True
                resumed = next_action(root)
                assert resumed["resumed_interrupted_completion"] == job["job_id"]
                continue
            completed = complete_job(root, job["job_id"])
            if job["job_id"] == "BE04.research":
                replayed = complete_job(root, job["job_id"])
                assert replayed["duplicate_completion_prevented"] is True
                assert replayed["next"] == completed["next"]

        assert retried and recovered and checkpoints >= 1
        errors = validate_run(root)
        assert not errors, errors
        csv_path, markdown_path = build_outputs(root)
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows == [
            {
                "rank_section": "primary_repurposing",
                "rank": "1",
                "endpoint_rank": "1",
                "drug": "Syntheticmol",
                "chemical_identifier": "CHEMBL123",
                "candidate_class": "repurposing_candidate",
                "compound_origin": "synthetic_or_semisynthetic",
                "target_endpoint_type": "disease_modifying_clinical",
                "target_endpoint": "synthetic disease progression",
                "mode_of_action": "Selective pathway modulator",
                "repurposing_readiness": "80",
                "raw_score": "80",
                "total_score": "80",
                "applied_cap": "100",
                "cap_reason": "",
                "audit_status": "independently_verified",
                "council_disposition": "retain",
            }
        ]
        markdown = markdown_path.read_text(encoding="utf-8")
        assert "Syntheticmol" in markdown and "SRC_CP01" in markdown
        assert "Primary repurposing candidates" in markdown
        assert "Corrected to repurposing_candidate" in markdown
        candidate = read_jsonl(root / "candidate_records.jsonl")[0]
        assert candidate["council_status"] == "reviewed"
        assert candidate["candidate_class"] == "repurposing_candidate"
        assert candidate["rank_section"] == "primary_repurposing"
        assert candidate["experimental_model_suitability"]["assessed"] is False
        council_job = next(
            row for row in read_json(root / "execution_plan.json", {})["jobs"]
            if row["kind"] == "council"
        )
        assert council_job["selection_snapshot"][0]["rank_section"] == "target_disease_benchmark"


def main() -> int:
    run_integration()
    print("INTEGRATION TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
