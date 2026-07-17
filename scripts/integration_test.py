#!/usr/bin/env python3
"""Synthetic end-to-end integration test for the deterministic runtime."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from compact_source_payload import compact_payload
from orchestrate_program import (
    COUNCIL_STAGES,
    complete_job,
    fail_job,
    initialize,
    next_action,
    release_agent,
    resume_action,
    start_job,
)
from program_contract import SKEPTIC_CRITIQUE_DOMAINS, required_query_families
from validate_program import validate_run


STRUCTURE_KEY = "INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C"
CHEMICAL_NODE = f"CHEM:{STRUCTURE_KEY}"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def _agent_for(job: dict[str, Any], assignments: dict[str, str]) -> str:
    key = (
        f"council:{job.get('candidate_id')}:{job.get('role')}"
        if job.get("candidate_id")
        else f"unit:{job.get('unit_id')}:{job.get('role')}"
        if job.get("unit_id")
        else f"job:{job.get('job_id')}:{job.get('role')}"
    )
    assignments.setdefault(key, f"synthetic-agent-{len(assignments) + 1:03d}")
    return assignments[key]


def _query_receipt(root: Path, unit_id: str, query_id: str) -> tuple[str, dict[str, Any]]:
    source_id = f"SRC_{unit_id.replace('.', '_')}"
    receipt_path = root / "raw_sources" / f"{query_id}_compact.json"
    receipt = compact_payload(
        [
            {
                "canonical_identifier": f"PMID:{1000 + len(unit_id)}",
                "identifier_type": "PMID",
                "title": f"Synthetic verified source for {unit_id}",
                "year": 2026,
                "source_kind": "primary_research",
            }
        ],
        "verification",
        query_id,
    )
    _write_json(receipt_path, receipt)
    return str(receipt_path.relative_to(root)).replace("\\", "/"), receipt


def _unit_source(root: Path, unit_id: str, primary_query_id: str) -> tuple[dict[str, Any], str]:
    source_id = f"SRC_{unit_id.replace('.', '_')}"
    receipt_path, receipt = _query_receipt(root, unit_id, primary_query_id)
    source = {
        "source_id": source_id,
        "canonical_identifier": receipt["records"][0]["canonical_identifier"],
        "identifier_type": "PMID",
        "title": receipt["records"][0]["title"],
        "year": 2026,
        "source_kind": "primary_research",
        "source_family": "literature",
        "discovered_by_units": [unit_id],
        "discovery_query_ids": [],
        "metadata_verified": True,
        "screen_decision": "include",
        "exclusion_reason": "",
        "original_acquired": True,
        "original_pointer": receipt_path,
        "content_verified": True,
        "verification_method": "authoritative synthetic fixture",
        "verification_scope": f"unit {unit_id}",
        "supported_claim_ids": ["CLAIM_SYNTHETIC"] if unit_id == "SC.ST_SYNTHETIC" else [],
        "compaction_receipt_path": receipt_path,
        "compaction_record_hash": receipt["records"][0]["compact_record_hash"],
    }
    return source, source_id


def _query(
    *,
    root: Path,
    query_id: str,
    unit_id: str,
    subtopic_id: str,
    family: str,
    source_id: str,
    agent_id: str,
    role: str,
    origin_job_id: str,
    candidate: bool = False,
) -> dict[str, Any]:
    compact_path, _ = _query_receipt(root, unit_id, query_id)
    return {
        "query_id": query_id,
        "research_unit_id": unit_id,
        "subtopic_id": subtopic_id,
        "query_family": family,
        "resource": "PubMed",
        "query": f"synthetic {family} query for {unit_id}",
        "result_count": 1,
        "deduplicated_count": 1,
        "screened_count": 1,
        "acquired_count": 1,
        "original_verified_count": 1,
        "page_count": 1,
        "pagination_complete": True,
        "continuation_exhausted": True,
        "compact_payload_paths": [compact_path],
        "pagination_trace": [
            {
                "page_index": 1,
                "receipt_path": compact_path,
                "input_token_hash": "",
                "output_token_hash": "",
            }
        ],
        "acquired_source_ids": [source_id],
        "original_verified_source_ids": [source_id],
        "executed_by_agent_id": agent_id,
        "executor_role": role,
        "origin_job_id": origin_job_id,
        "retained_source_ids": [source_id],
        "new_subtopic_ids": [],
        "new_claim_ids": ["CLAIM_SYNTHETIC"] if candidate and family == "exact_compound_literature" else [],
        "new_candidate_ids": ["CAND_SYNTHETIC"] if candidate and family == "exact_compound_literature" else [],
        "outcome": "completed",
        "rate_limit_pending": False,
        "closure_note": "All pages, continuations, citation trails, and counterevidence branches were resolved.",
    }


def _worker_updates(root: Path, job: dict[str, Any], agent_id: str) -> dict[str, list[dict[str, Any]]]:
    unit_id = str(job.get("unit_id", ""))
    units = {str(row["unit_id"]): row for row in _read_jsonl(root / "research_units.jsonl")}
    unit = units[unit_id]
    families = sorted(required_query_families(str(unit["unit_type"])))
    query_ids = [f"Q_{unit_id.replace('.', '_')}_{family}" for family in families]
    source, source_id = _unit_source(root, unit_id, query_ids[0])
    source["discovery_query_ids"] = query_ids
    candidate_unit = unit_id == "SC.ST_SYNTHETIC"
    searches = [
        _query(
            root=root,
            query_id=query_id,
            unit_id=unit_id,
            subtopic_id=str(unit.get("subtopic_id", "")),
            family=family,
            source_id=source_id,
            agent_id=agent_id,
            role="worker",
            origin_job_id=str(job["job_id"]),
            candidate=candidate_unit,
        )
        for query_id, family in zip(query_ids, families)
    ]
    unit_update = {
        **unit,
        "planned_query_families": families,
        "completed_query_families": families,
        "candidate_ids": ["CAND_SYNTHETIC"] if candidate_unit else [],
    }
    updates: dict[str, list[dict[str, Any]]] = {
        "source_corpus.jsonl": [source],
        "search_log.jsonl": searches,
        "research_units.jsonl": [unit_update],
    }
    if candidate_unit:
        (root / "dossiers" / "CAND_SYNTHETIC.md").write_text(
            "# Synthetic candidate dossier\n", encoding="utf-8"
        )
        updates.update(
            {
                "claim_ledger.jsonl": [
                    {
                        "claim_id": "CLAIM_SYNTHETIC",
                        "subtopic_id": "ST_SYNTHETIC",
                        "claim": "Syntheticmol has an audited directionally plausible route toward wild type.",
                        "evidence_kind": "primary",
                        "source_ids": [source_id],
                        "calibration": "supported_with_qualifier",
                        "directionality": "toward_wild_type",
                        "allele_relevance": "loss_of_function",
                        "scope_conditions": "gene-1 loss-of-function worm model",
                        "contrary_claim_ids": [],
                        "audit_status": "verified",
                    }
                ],
                "evidence_graph.jsonl": [
                    {
                        "edge_id": "EDGE_SYNTHETIC",
                        "from_node": CHEMICAL_NODE,
                        "to_node": "CASE_WILD_TYPE_PHENOTYPE",
                        "relation": "may_restore",
                        "direction": "positive",
                        "directionality_status": "supports_rescue",
                        "allele_mode_effect": "compensates_loss_of_function",
                        "claim_ids": ["CLAIM_SYNTHETIC"],
                        "audit_status": "verified",
                    }
                ],
                "candidate_records.jsonl": [
                    {
                        "candidate_id": "CAND_SYNTHETIC",
                        "canonical_name": "Syntheticmol",
                        "canonical_identifier": "CHEMBL123",
                        "registry_identifiers": {"ChEMBL": "CHEMBL123", "PubChem": "PUBCHEM CID:123"},
                        "structure_identity_key": STRUCTURE_KEY,
                        "chemical_node_id": CHEMICAL_NODE,
                        "identity_source_ids": [source_id],
                        "entity_type": "discrete_chemical",
                        "identity_verified": True,
                        "human_gene": "GENE1",
                        "worm_gene": "gene-1",
                        "allele_mode": "loss_of_function",
                        "worm_model": "gene-1 loss-of-function worm model",
                        "origin": "de_novo",
                        "source_research_unit_ids": [unit_id],
                        "causal_paths": [
                            {
                                "path_id": "PATH_SYNTHETIC",
                                "edge_ids": ["EDGE_SYNTHETIC"],
                                "claim_ids": ["CLAIM_SYNTHETIC"],
                                "start_node": CHEMICAL_NODE,
                                "end_node": "CASE_WILD_TYPE_PHENOTYPE",
                                "expected_rescue_direction": "toward_wild_type",
                            }
                        ],
                        "rationale": "Synthetic audited rationale.",
                        "phenomic_interpretation": "Shift toward the wild-type Tierpsy profile.",
                        "decisive_uncertainty": "Exposure and effect size remain uncertain.",
                        "dossier_path": "dossiers/CAND_SYNTHETIC.md",
                        "council_disposition": "pending",
                        "fact_audit_status": "pending",
                    }
                ],
            }
        )
    return updates


def _auditor_updates(root: Path, job: dict[str, Any], agent_id: str) -> dict[str, list[dict[str, Any]]]:
    unit_id = str(job.get("unit_id", ""))
    plan = _read_json(root / "execution_plan.json")
    worker_job = next(row for row in plan["jobs"] if row["job_id"] == job["paired_worker_job_id"])
    worker_result = _read_json(root / worker_job["result_path"])
    worker_updates = worker_result["ledger_updates"]
    unit = next(row for row in worker_updates["research_units.jsonl"] if row["unit_id"] == unit_id)
    source_id = f"SRC_{unit_id.replace('.', '_')}"
    source = dict(next(row for row in worker_updates["source_corpus.jsonl"] if row["source_id"] == source_id))
    audit_query_id = f"Q_{unit_id.replace('.', '_')}_AUDIT"
    source["discovery_query_ids"] = list(dict.fromkeys(source["discovery_query_ids"] + [audit_query_id]))
    query = _query(
        root=root,
        query_id=audit_query_id,
        unit_id=unit_id,
        subtopic_id=str(unit.get("subtopic_id", "")),
        family="missing_branch",
        source_id=source_id,
        agent_id=agent_id,
        role="auditor",
        origin_job_id=str(job["job_id"]),
    )
    unit_update = {**unit, "independent_audit_query_ids": [audit_query_id]}
    updates: dict[str, list[dict[str, Any]]] = {
        "source_corpus.jsonl": [source],
        "search_log.jsonl": [query],
        "research_units.jsonl": [unit_update],
        "unit_audits.jsonl": [
            {
                "audit_id": f"AUDIT_{unit_id.replace('.', '_')}",
                "unit_id": unit_id,
                "auditor_agent_id": agent_id,
                "checked_source_ids": [source_id],
                "independent_query_ids": [audit_query_id],
                "material_findings": [],
                "repairs_completed": [],
                "perspective_distinctness_verified": True,
                "source_overlap_assessment": "Shared source retained, with an independently executed missing-branch query.",
                "final_status": "verified",
                "closure_basis": "All planned families, continuations, citation trails, and the independent missing branch are resolved.",
            }
        ],
    }
    if unit_id == "SC.ST_SYNTHETIC":
        subtopics = {str(row["subtopic_id"]): row for row in _read_jsonl(root / "subtopic_registry.jsonl")}
        updates["subtopic_registry.jsonl"] = [
            {
                **subtopics["ST_SYNTHETIC"],
                "status": "audited_complete",
                "closure_reason": "Evidence and exact-compound discovery were independently audited.",
            }
        ]
    return updates


def _council_exchange(stage: str, agent_id: str) -> dict[str, Any]:
    exchange_type = {
        "advocate_case": "case",
        "skeptic_review": "challenge",
        "advocate_response": "response",
    }[stage]
    role = "skeptic" if stage == "skeptic_review" else "advocate"
    exchange_id = {
        "advocate_case": "X_CASE",
        "skeptic_review": "X_CHALLENGE",
        "advocate_response": "X_RESPONSE",
    }[stage]
    return {
        "exchange_id": exchange_id,
        "candidate_id": "CAND_SYNTHETIC",
        "role": role,
        "agent_id": agent_id,
        "exchange_type": exchange_type,
        "responds_to_id": "X_CHALLENGE" if exchange_type == "response" else "",
        "content": f"Substantive synthetic {exchange_type}.",
        "assertions": [
            {"claim_id": "CLAIM_SYNTHETIC", "stance": role, "text": "Structured assessment of the causal claim."}
        ],
        "claim_ids": ["CLAIM_SYNTHETIC"],
        "critique_domains": sorted(SKEPTIC_CRITIQUE_DOMAINS) if exchange_type == "challenge" else [],
        "challenge_items": [
            {
                "domain": domain,
                "challenge": f"Challenge on {domain}.",
                "claim_ids": ["CLAIM_SYNTHETIC"],
                "resolution_required": True,
            }
            for domain in sorted(SKEPTIC_CRITIQUE_DOMAINS)
        ] if exchange_type == "challenge" else [],
        "response_items": [
            {
                "domain": domain,
                "response": f"Qualified response on {domain}.",
                "claim_ids": ["CLAIM_SYNTHETIC"],
                "disposition": "qualified",
            }
            for domain in sorted(SKEPTIC_CRITIQUE_DOMAINS)
        ] if exchange_type == "response" else [],
        "fact_audit_status": "pending",
    }


def exercise_worker_repair_lineage() -> None:
    """A repair delta must retain prior worker searches without auditor laundering."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(root, {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"})
        state = _read_json(root / "program_state.json")
        state["token_budget_per_minute"] = 100_000_000
        state["token_reserve_per_agent"] = 0
        _write_json(root / "program_state.json", state)

        action = next_action(root)
        assert action["job_id"] == "BE01.worker"
        plan = _read_json(root / "execution_plan.json")
        worker = next(row for row in plan["jobs"] if row["job_id"] == "BE01.worker")
        start = start_job(root, "BE01.worker", "repair-lineage-worker")
        original = {
            "job_id": "BE01.worker", "packet_hash": action["packet_hash"], "all_chunks_processed": True,
            "outcome": "completed", "ledger_updates": _worker_updates(root, worker, "repair-lineage-worker"),
            "approved_subtopics": [],
        }
        _write_json(root / start["expected_result_path"], original)
        complete_job(root, "BE01.worker")
        close = next_action(root)
        release_agent(root, close["attempt_id"], close["agent_id"])

        action = next_action(root)
        assert action["job_id"] == "BE01.audit"
        audit_start = start_job(root, "BE01.audit", "repair-lineage-auditor")
        repair = {
            "job_id": "BE01.audit", "packet_hash": action["packet_hash"], "all_chunks_processed": True,
            "outcome": "repair_required", "ledger_updates": {}, "approved_subtopics": [],
        }
        _write_json(root / audit_start["expected_result_path"], repair)
        complete_job(root, "BE01.audit")
        close = next_action(root)
        release_agent(root, close["attempt_id"], close["agent_id"])
        checkpoint = next_action(root)
        assert checkpoint["action"] == "checkpoint"
        resume_action(root)

        action = next_action(root)
        assert action["job_id"] == "BE01.worker"
        manifest = _read_json(root / action["packet_manifest_path"])
        packet = _read_json(root / manifest["required_chunks"][0]["path"])
        repair_context = packet["context"]["dependency_results"]
        assert any(row.get("purpose") == "prior_worker_result" for row in repair_context)
        assert any(row.get("purpose") == "auditor_repair_feedback" for row in repair_context)
        original_path = next(row["result_path"] for row in repair_context if row.get("purpose") == "prior_worker_result")
        original_searches = _read_json(root / original_path)["ledger_updates"]["search_log.jsonl"]
        assert original_searches and all(row["origin_job_id"] == "BE01.worker" for row in original_searches)

        repair_start = start_job(root, "BE01.worker", "repair-lineage-worker")
        delta = {
            "job_id": "BE01.worker", "packet_hash": action["packet_hash"], "all_chunks_processed": True,
            "outcome": "completed", "ledger_updates": {}, "approved_subtopics": [],
        }
        _write_json(root / repair_start["expected_result_path"], delta)
        complete_job(root, "BE01.worker")
        close = next_action(root)
        release_agent(root, close["attempt_id"], close["agent_id"])

        action = next_action(root)
        assert action["job_id"] == "BE01.audit"
        manifest = _read_json(root / action["packet_manifest_path"])
        packet = _read_json(root / manifest["required_chunks"][0]["path"])
        audit_context = packet["context"]["dependency_results"]
        assert original_path in {row["result_path"] for row in audit_context}
        assert any(row.get("purpose") == "auditor_repair_feedback" for row in audit_context)


def run_integration() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {
                "human_gene": "GENE1",
                "worm_gene": "gene-1",
                "allele_mode": "loss_of_function",
                "worm_model": "gene-1 loss-of-function worm model",
            },
        )
        state = _read_json(root / "program_state.json")
        state["token_budget_per_minute"] = 100_000_000
        state["token_reserve_per_agent"] = 0
        _write_json(root / "program_state.json", state)
        assignments: dict[str, str] = {}
        rate_limit_tested = False
        subtopic_registered = False
        council_reopen_tested = False
        iterations = 0

        while True:
            iterations += 1
            assert iterations < 400, "controller did not converge"
            action = next_action(root)
            if action["action"] == "finalize":
                break
            if action["action"] == "close_agent":
                released = release_agent(root, action["attempt_id"], action["agent_id"])
                assert released["status"] == "released"
                continue
            if action["action"] == "wait_for_retry":
                plan = _read_json(root / "execution_plan.json")
                next(row for row in plan["jobs"] if row["job_id"] == action["job_id"])["retry_not_before"] = (
                    "2000-01-01T00:00:00+00:00"
                )
                _write_json(root / "execution_plan.json", plan)
                continue
            if action["action"] == "checkpoint":
                resume_action(root)
                continue
            assert action["action"] == "start_agent", action
            plan = _read_json(root / "execution_plan.json")
            job = next(row for row in plan["jobs"] if row["job_id"] == action["job_id"])
            manifest = _read_json(root / str(action["packet_manifest_path"]))
            packet = _read_json(root / str(manifest["required_chunks"][0]["path"]))
            assert packet["run_root"] == str(root.resolve())
            assert packet["path_contract"]["relative_paths_resolve_against"] == "run_root"
            for dependency in packet["context"].get("dependency_results", []):
                assert dependency["path_base"] == "run_root"
                assert Path(dependency["resolved_result_path"]).is_file()
                assert all(artifact["exists"] is True for artifact in dependency["artifact_manifest"])
                assert all(Path(artifact["resolved_path"]).is_file() for artifact in dependency["artifact_manifest"])
            agent_id = action.get("assigned_agent_id") or _agent_for(job, assignments)
            attempt = start_job(root, action["job_id"], agent_id)

            if action["job_id"] == "BE01.worker" and not rate_limit_tested:
                failed = fail_job(root, action["job_id"], "rate_limit", 0, "synthetic TPM limit")
                assert failed["action"] == "close_agent"
                rate_limit_tested = True
                continue

            result: dict[str, Any] = {
                "job_id": action["job_id"],
                "packet_hash": action["packet_hash"],
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {},
                "approved_subtopics": [],
            }
            kind = str(job.get("kind"))
            if action["job_id"] == "MERGE01.worker":
                pass
            elif kind in {"research_worker", "closure_worker"}:
                result["ledger_updates"] = _worker_updates(root, job, agent_id)
            elif kind in {"unit_auditor", "closure_auditor"}:
                result.update(outcome="verified", unit_status="audited_complete")
                result["ledger_updates"] = _auditor_updates(root, job, agent_id)
                if action["job_id"] == "BE02.audit" and not subtopic_registered:
                    result["approved_subtopics"] = [
                        {
                            "subtopic_id": "ST_SYNTHETIC",
                            "parent_id": "",
                            "name": "Synthetic downstream relation",
                            "relation_to_case": "synthetic causal relation",
                            "depth": 0,
                            "candidate_relevant": True,
                        }
                    ]
                    subtopic_registered = True
                if kind == "closure_auditor":
                    result["closure_confirmed"] = True
            elif kind == "merge_auditor":
                result.update(outcome="verified", unit_status="audited_complete", candidate_ids=["CAND_SYNTHETIC"])
            elif kind == "council_turn":
                exchange = _council_exchange(str(job["stage"]), agent_id)
                result["ledger_updates"] = {"council_exchanges.jsonl": [exchange]}
            elif kind == "council_fact_auditor" and not council_reopen_tested:
                result.update(outcome="repair_required", reopen_stage="skeptic_review")
                council_reopen_tested = True
            elif kind == "council_fact_auditor":
                role_agents = _read_json(root / "execution_plan.json")["role_agents"]
                exchanges = []
                for prior_job in _read_json(root / "execution_plan.json")["jobs"]:
                    if (
                        prior_job.get("candidate_id") == "CAND_SYNTHETIC"
                        and prior_job.get("kind") == "council_turn"
                        and prior_job.get("result_path")
                    ):
                        prior_result = _read_json(root / prior_job["result_path"])
                        exchanges.extend(
                            {**row, "fact_audit_status": "verified"}
                            for row in prior_result.get("ledger_updates", {}).get("council_exchanges.jsonl", [])
                        )
                source_id = "SRC_SC_ST_SYNTHETIC"
                (root / "dossiers" / "CAND_SYNTHETIC.md").write_text(
                    "# Debate\n\n" + "\n".join(row["exchange_id"] for row in exchanges), encoding="utf-8"
                )
                (root / "dossiers" / "CAND_SYNTHETIC_fact_audit.md").write_text(
                    "# Fact audit\n\n" + "\n".join(row["exchange_id"] for row in exchanges), encoding="utf-8"
                )
                independent_checks = [
                    {
                        "resource": "PubMed",
                        "query": "independent synthetic fact check",
                        "executed_by_agent_id": agent_id,
                        "checked_source_ids": [source_id],
                    }
                ]
                claim_verdicts = [
                    {"claim_id": "CLAIM_SYNTHETIC", "verdict": "qualified", "checked_source_ids": [source_id]}
                ]
                result.update(
                    outcome="verified",
                    novelty_challenge_resolved=True,
                    critique_checklist_complete=True,
                    unresolved_material_claims=[],
                    material_claim_ids=["CLAIM_SYNTHETIC"],
                    claim_verdicts=claim_verdicts,
                    independent_checks=independent_checks,
                    surviving_causal_path_ids=["PATH_SYNTHETIC"],
                    verified_exclusion_reasons=[],
                    ledger_updates={
                        "council_exchanges.jsonl": exchanges,
                        "council_records.jsonl": [
                            {
                                "candidate_id": "CAND_SYNTHETIC",
                                "advocate_agent_id": role_agents["council:CAND_SYNTHETIC:advocate"],
                                "skeptic_agent_id": role_agents["council:CAND_SYNTHETIC:skeptic"],
                                "fact_auditor_agent_id": role_agents["council:CAND_SYNTHETIC:fact_auditor"],
                                "direct_response_complete": True,
                                "critique_checklist_complete": True,
                                "novelty_challenge_resolved": True,
                                "fact_audit_status": "verified",
                                "material_claim_ids": ["CLAIM_SYNTHETIC"],
                                "claim_verdicts": claim_verdicts,
                                "independent_checks": independent_checks,
                                "surviving_causal_path_ids": ["PATH_SYNTHETIC"],
                                "disposition": "screen",
                                "exclusion_reason": "",
                                "unresolved_material_claims": [],
                                "debate_path": "dossiers/CAND_SYNTHETIC.md",
                                "fact_audit_path": "dossiers/CAND_SYNTHETIC_fact_audit.md",
                            }
                        ],
                    },
                )
            elif kind in {"final_repair_worker", "final_repair_auditor"}:
                error_files = sorted(root.glob("final_validation_errors_round*.json"))
                raise AssertionError(_read_json(error_files[-1]) if error_files else "missing final validation errors")
            else:
                raise AssertionError(f"Unhandled integration job kind: {kind}")

            _write_json(root / str(attempt["expected_result_path"]), result)
            complete_job(root, action["job_id"])

        errors = validate_run(root)
        assert not errors, errors
        state = _read_json(root / "program_state.json")
        plan = _read_json(root / "execution_plan.json")
        candidates = _read_jsonl(root / "candidate_records.jsonl")
        attempts = _read_jsonl(root / "job_attempts.jsonl")
        assert rate_limit_tested and subtopic_registered and council_reopen_tested
        assert state["current_phase"] == "ready_for_finalization"
        assert not state["pending_agent_release_id"]
        assert all(attempt["release_acknowledged"] is True for attempt in attempts)
        assert all(job["status"] == "complete" for job in plan["jobs"])
        assert next(row for row in candidates if row["candidate_id"] == "CAND_SYNTHETIC")["council_disposition"] == "screen"
        council_jobs = sorted(
            [job for job in plan["jobs"] if job.get("candidate_id") == "CAND_SYNTHETIC"],
            key=lambda row: row["sequence"],
        )
        assert [(job["stage"], job["role"]) for job in council_jobs] == list(COUNCIL_STAGES)


def main() -> int:
    exercise_worker_repair_lineage()
    run_integration()
    print("INTEGRATION TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
