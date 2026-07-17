#!/usr/bin/env python3
"""Adversarial self-test for the repurposing run validator and output builder."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from pathlib import Path

from build_context_packet import build_packet
from build_final_outputs import build_outputs
from compact_source_payload import compact_payload
from orchestrate_program import (
    _register_subtopics,
    complete_job,
    fail_job,
    initialize,
    next_action,
    recover_active,
    release_agent,
    start_job,
    validate_result,
)
from program_contract import (
    BASE_QUERY_FAMILIES,
    BROAD_DOMAINS,
    COMPOUND_QUERY_FAMILIES,
    COUNCIL_STAGES,
    EVIDENCE_QUERY_FAMILIES,
    GLOBAL_PERSPECTIVES,
    SCHEMA_VERSION,
)
from validate_program import validate_run, validate_staged_commit


COUNCIL_STAGE_ROLES = COUNCIL_STAGES


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    text = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_runtime_fixture(root: Path, units: list[dict[str, object]], candidate_ids: list[str]) -> None:
    packets = root / "packets"
    staging = root / "staging"
    raw_sources = root / "raw_sources"
    packets.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    raw_sources.mkdir(exist_ok=True)
    jobs: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    role_agents: dict[str, str] = {}
    unit_by_id = {str(row["unit_id"]): row for row in units}
    council_role_agents = {
        "advocate": "council-advocate",
        "skeptic": "council-skeptic",
        "fact_auditor": "council-fact-audit",
    }
    sequence = 0

    def add_job(
        job_id: str,
        *,
        phase: int,
        role: str,
        kind: str,
        unit_id: str = "",
        candidate_id: str = "",
        stage: str = "",
        depends_on: list[str] | None = None,
    ) -> None:
        nonlocal sequence
        sequence += 1
        safe = job_id.replace(":", "_").replace(".", "_")
        packet_dir = packets / safe
        packet_dir.mkdir()
        chunk = packet_dir / "input_001.json"
        write_json(
            chunk,
            {
                "schema_version": SCHEMA_VERSION,
                "run_root": str(root.resolve()),
                "path_contract": {
                    "relative_paths_resolve_against": "run_root",
                    "never_resolve_against": ["staging_directory"],
                    "missing_artifact_rule": "Resolve against run_root before reporting a missing artifact.",
                },
                "job_id": job_id,
                "chunk_index": 1,
                "chunk_count": 1,
                "context": {"dependency_results": []},
                "machine_contract": {
                    "ledger_schemas": {},
                    "result_required_fields": [],
                    "completion_rule": "No unresolved branch remains.",
                    "tool_paths": {"source_compactor": "scripts/compact_source_payload.py"},
                    "preferred_source_resources": {"literature": ["PubMed"]},
                    "path_rule": "Resolve all relative paths against packet run_root.",
                    "source_enum_rule": "Use screen_decision=include or exclude only.",
                },
            },
        )
        manifest = packet_dir / "manifest.json"
        manifest_value = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "required_chunks": [
                {
                    "chunk_index": 1,
                    "path": str(chunk.relative_to(root)),
                    "sha256": sha256(chunk),
                    "bytes": chunk.stat().st_size,
                }
            ],
            "all_chunks_required": True,
            "silent_truncation_permitted": False,
        }
        packet_hash = hashlib.sha256(
            json.dumps(manifest_value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest_value["packet_hash"] = packet_hash
        write_json(manifest, manifest_value)
        result_dir = staging / safe
        result_dir.mkdir()
        result = result_dir / "result.json"
        write_json(
            result,
            {
                "job_id": job_id,
                "packet_hash": packet_hash,
                "all_chunks_processed": True,
                "outcome": "verified" if role in {"auditor", "closure_auditor", "fact_auditor"} else "completed",
                "ledger_updates": {},
                "approved_subtopics": [],
            },
        )
        jobs.append(
            {
                "job_id": job_id,
                "phase": phase,
                "sequence": sequence,
                "kind": kind,
                "role": role,
                "stage": stage,
                "unit_id": unit_id,
                "candidate_id": candidate_id,
                "question": f"Question for {job_id}",
                "completion_contract": "All required packet chunks and decision-changing branches are resolved.",
                "depends_on": depends_on or [],
                "gate": "",
                "context_scope": "case_only",
                "paired_worker_job_id": "",
                "status": "complete",
                "attempt_count": 1,
                "repair_round": 0,
                "packet_manifest_path": str(manifest.relative_to(root)),
                "packet_hash": packet_hash,
                "result_path": str(result.relative_to(root)),
                "result_hash": sha256(result),
                "retry_not_before": "",
            }
        )
        role_key = f"council:{candidate_id}:{role}" if candidate_id else f"unit:{unit_id}:{role}"
        if candidate_id:
            assigned_agent = council_role_agents[role]
        elif unit_id in unit_by_id:
            assigned_agent = str(
                unit_by_id[unit_id]["worker_agent_id" if role == "worker" else "auditor_agent_id"]
            )
        else:
            assigned_agent = f"agent-{len(role_agents) + 1:03d}"
        role_agents.setdefault(role_key, assigned_agent)
        attempts.append(
            {
                "attempt_id": f"{safe}.attempt001",
                "job_id": job_id,
                "agent_id": assigned_agent,
                "packet_hash": packet_hash,
                "packet_manifest_path": str(manifest.relative_to(root)),
                "expected_result_path": str(result.relative_to(root)),
                "status": "complete",
                "started_at": "2026-01-01T00:00:00+00:00",
                "finished_at": "2026-01-01T00:01:00+00:00",
                "failure_kind": "",
                "release_acknowledged": True,
                "released_at": "2026-01-01T00:01:01+00:00",
            }
        )

    for unit in units:
        unit_id = str(unit["unit_id"])
        unit_type = str(unit["unit_type"])
        if unit_type == "closure_audit":
            worker_id = f"{unit_id}.worker"
            add_job(worker_id, phase=2, role="worker", kind="closure_worker", unit_id=unit_id)
            add_job(
                f"{unit_id}.audit",
                phase=2,
                role="closure_auditor",
                kind="closure_auditor",
                unit_id=unit_id,
                depends_on=[worker_id],
            )
            continue
        phase = 1 if unit_type == "broad_evidence" else 3 if unit_type in {"global_perspective", "subtopic_compound"} else 2 if unit_type == "subtopic_evidence" else 4
        worker_id = f"{unit_id}.worker"
        add_job(worker_id, phase=phase, role="worker", kind="research_worker", unit_id=unit_id)
        add_job(f"{unit_id}.audit", phase=phase, role="auditor", kind="unit_auditor", unit_id=unit_id, depends_on=[worker_id])

    for candidate_id in candidate_ids:
        prior = ""
        for stage, role in COUNCIL_STAGE_ROLES:
            job_id = f"COUNCIL.{candidate_id}.{stage}"
            add_job(
                job_id,
                phase=5,
                role=role,
                kind="council_fact_auditor" if stage == "fact_audit" else "council_turn",
                candidate_id=candidate_id,
                stage=stage,
                depends_on=[prior] if prior else [],
            )
            prior = job_id

    write_json(
        root / "execution_plan.json",
        {
            "schema_version": SCHEMA_VERSION,
            "max_active_jobs": 1,
            "fixed_seed_topology": True,
            "jobs": jobs,
            "role_agents": role_agents,
            "next_dynamic_sequence": 2000,
        },
    )
    write_jsonl(root / "job_attempts.jsonl", attempts)


def make_valid_run(root: Path) -> None:
    (root / "dossiers").mkdir(parents=True)
    (root / "raw_sources").mkdir(parents=True)
    compact_receipt_path = root / "raw_sources" / "source1_compact.json"
    compact_receipt = compact_payload(
        [
            {
                "canonical_identifier": "PMID:1",
                "identifier_type": "PMID",
                "title": "Verified primary report",
                "year": 2024,
                "source_kind": "primary_research",
            }
        ],
        "verification",
    )
    write_json(compact_receipt_path, compact_receipt)
    compact_hash = str(compact_receipt["records"][0]["compact_record_hash"])
    write_json(
        root / "case.json",
        {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"},
    )
    write_json(
        root / "program_state.json",
        {
            "schema_version": SCHEMA_VERSION,
            "current_phase": "ready_for_finalization",
            "max_active_jobs": 1,
            "active_job_id": "",
            "active_attempt_id": "",
            "pending_agent_release_id": "",
            "pending_agent_release_attempt_id": "",
            "broad_evidence_complete": True,
            "subtopic_closure_complete": True,
            "de_novo_perspectives_complete": True,
            "candidate_universe_complete": True,
            "council_complete": True,
            "blocked_reason": "",
        },
    )
    write_jsonl(root / "orchestration.jsonl", [{"event_id": "O1", "status": "complete", "rate_limit_pending": False}])

    units: list[dict[str, object]] = []
    unit_specs: list[tuple[str, str, str]] = []
    for index, perspective in enumerate(sorted(BROAD_DOMAINS), 1):
        unit_specs.append((f"B{index:02d}", "broad_evidence", perspective))
    for index, perspective in enumerate(sorted(GLOBAL_PERSPECTIVES), 1):
        unit_specs.append((f"G{index:02d}", "global_perspective", perspective))
    unit_specs.extend(
        [
            ("C01", "closure_audit", "subtopic_closure"),
            ("S01", "subtopic_evidence", "relation_evidence"),
            ("S02", "subtopic_compound", "relation_compounds"),
        ]
    )

    searches: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    all_query_ids: list[str] = []
    all_unit_ids = [unit_id for unit_id, _, _ in unit_specs]
    for index, (unit_id, unit_type, perspective) in enumerate(unit_specs, 1):
        query_families = set(BASE_QUERY_FAMILIES)
        if unit_type in {"broad_evidence", "subtopic_evidence"}:
            query_families.update(EVIDENCE_QUERY_FAMILIES)
        if unit_type in {"global_perspective", "subtopic_compound"}:
            query_families.update(COMPOUND_QUERY_FAMILIES)
        query_families = sorted(query_families)
        audit_query = f"Q{index:02d}_audit_missing_branch"
        candidate_ids = ["CAND1"] if perspective in {"direct_molecular", "relation_compounds"} else []
        units.append(
            {
                "unit_id": unit_id,
                "unit_type": unit_type,
                "subtopic_id": "ST1" if unit_type.startswith("subtopic_") else "",
                "perspective": perspective,
                "worker_agent_id": f"worker-{index:02d}",
                "auditor_agent_id": f"auditor-{index:02d}",
                "status": "audited_complete",
                "audit_status": "verified",
                "planned_query_families": query_families,
                "completed_query_families": query_families,
                "independent_audit_query_ids": [audit_query],
                "rate_limit_pending": False,
                "known_high_yield_search_remaining": [],
                "unresolved_repair_count": 0,
                "candidate_ids": candidate_ids,
                "absence_reason": "",
            }
        )
        for family in query_families:
            query_id = f"Q{index:02d}_{family}"
            all_query_ids.append(query_id)
            searches.append(
                {
                    "query_id": query_id,
                    "research_unit_id": unit_id,
                    "subtopic_id": "ST1" if unit_type.startswith("subtopic_") else "",
                    "query_family": family,
                    "resource": "PubMed",
                    "query": f"{family} query for {perspective}",
                    "result_count": 1,
                    "deduplicated_count": 1,
                    "screened_count": 1,
                    "acquired_count": 1,
                    "original_verified_count": 1,
                    "page_count": 1,
                    "pagination_complete": True,
                    "continuation_exhausted": True,
                    "compact_payload_paths": ["raw_sources/source1_compact.json"],
                    "pagination_trace": [
                        {
                            "page_index": 1,
                            "receipt_path": "raw_sources/source1_compact.json",
                            "input_token_hash": "",
                            "output_token_hash": "",
                        }
                    ],
                    "acquired_source_ids": ["SRC1"],
                    "original_verified_source_ids": ["SRC1"],
                    "executed_by_agent_id": f"worker-{index:02d}",
                    "executor_role": "worker",
                    "origin_job_id": f"{unit_id}.worker",
                    "retained_source_ids": ["SRC1"],
                    "new_subtopic_ids": [],
                    "new_claim_ids": ["CL1"],
                    "new_candidate_ids": candidate_ids,
                    "outcome": "completed",
                    "rate_limit_pending": False,
                    "closure_note": "All result pages, continuations, citation trails, and counterevidence branches were resolved.",
                }
            )
        all_query_ids.append(audit_query)
        searches.append(
            {
                "query_id": audit_query,
                "research_unit_id": unit_id,
                "subtopic_id": "ST1" if unit_type.startswith("subtopic_") else "",
                "query_family": "missing_branch",
                "resource": "PubMed",
                "query": f"independent missing branch for {perspective}",
                "result_count": 1,
                "deduplicated_count": 1,
                "screened_count": 1,
                "acquired_count": 1,
                "original_verified_count": 1,
                "page_count": 1,
                "pagination_complete": True,
                "continuation_exhausted": True,
                "compact_payload_paths": ["raw_sources/source1_compact.json"],
                "pagination_trace": [
                    {
                        "page_index": 1,
                        "receipt_path": "raw_sources/source1_compact.json",
                        "input_token_hash": "",
                        "output_token_hash": "",
                    }
                ],
                "acquired_source_ids": ["SRC1"],
                "original_verified_source_ids": ["SRC1"],
                "executed_by_agent_id": f"auditor-{index:02d}",
                "executor_role": "auditor",
                "origin_job_id": f"{unit_id}.audit",
                "retained_source_ids": ["SRC1"],
                "new_subtopic_ids": [],
                "new_claim_ids": [],
                "new_candidate_ids": [],
                "outcome": "completed",
                "rate_limit_pending": False,
                "closure_note": "The independently selected missing branch and all continuations were resolved.",
            }
        )
        audits.append(
            {
                "audit_id": f"A{index:02d}",
                "unit_id": unit_id,
                "auditor_agent_id": f"auditor-{index:02d}",
                "checked_source_ids": ["SRC1"],
                "independent_query_ids": [audit_query],
                "material_findings": [],
                "repairs_completed": [],
                "perspective_distinctness_verified": True,
                "source_overlap_assessment": "Overlap is expected for the shared decisive source; this unit used its distinct predeclared question.",
                "final_status": "verified",
                "closure_basis": "All planned branches and the independent missing-branch query are resolved; no decision-changing branch remains.",
            }
        )

    for query_index, search in enumerate(searches):
        query_receipt_path = (
            compact_receipt_path
            if query_index == 0
            else root / "raw_sources" / f"{search['query_id']}_compact.json"
        )
        query_receipt = compact_payload(
            [
                {
                    "canonical_identifier": "PMID:1",
                    "identifier_type": "PMID",
                    "title": "Verified primary report",
                    "year": 2024,
                    "source_kind": "primary_research",
                }
            ],
            "verification",
            str(search["query_id"]),
        )
        write_json(query_receipt_path, query_receipt)
        relative_receipt = str(query_receipt_path.relative_to(root)).replace("\\", "/")
        search["compact_payload_paths"] = [relative_receipt]
        search["pagination_trace"] = [
            {
                "page_index": 1,
                "receipt_path": relative_receipt,
                "input_token_hash": "",
                "output_token_hash": "",
            }
        ]
        if query_index == 0:
            compact_hash = str(query_receipt["records"][0]["compact_record_hash"])

    write_jsonl(root / "research_units.jsonl", units)
    write_jsonl(root / "search_log.jsonl", searches)
    write_jsonl(root / "unit_audits.jsonl", audits)
    write_jsonl(
        root / "source_corpus.jsonl",
        [
            {
                "source_id": "SRC1",
                "canonical_identifier": "PMID:1",
                "identifier_type": "PMID",
                "title": "Verified primary report",
                "year": 2024,
                "source_kind": "primary_research",
                "source_family": "literature",
                "discovered_by_units": all_unit_ids,
                "discovery_query_ids": all_query_ids,
                "metadata_verified": True,
                "screen_decision": "include",
                "exclusion_reason": "",
                "original_acquired": True,
                "original_pointer": "local/source1.txt",
                "content_verified": True,
                "verification_method": "full_text",
                "verification_scope": "claim CL1",
                "supported_claim_ids": ["CL1"],
                "compaction_receipt_path": "raw_sources/source1_compact.json",
                "compaction_record_hash": compact_hash,
            }
        ],
    )
    write_jsonl(
        root / "claim_ledger.jsonl",
        [
            {
                "claim_id": "CL1",
                "subtopic_id": "ST1",
                "claim": "The verified chemical action has a directionally plausible path to phenotype improvement.",
                "evidence_kind": "primary",
                "source_ids": ["SRC1"],
                "calibration": "supported_with_qualifier",
                "directionality": "toward_wild_type",
                "allele_relevance": "loss_of_function",
                "scope_conditions": "worm model",
                "contrary_claim_ids": [],
                "audit_status": "verified",
            }
        ],
    )
    write_jsonl(
        root / "evidence_graph.jsonl",
        [
            {
                "edge_id": "E1",
                "from_node": "CHEM:INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
                "to_node": "CASE_WILD_TYPE_PHENOTYPE",
                "relation": "may_rescue",
                "direction": "positive",
                "directionality_status": "supports_rescue",
                "allele_mode_effect": "compensates_loss_of_function",
                "claim_ids": ["CL1"],
                "audit_status": "verified",
            }
        ],
    )
    write_jsonl(
        root / "subtopic_registry.jsonl",
        [
            {
                "subtopic_id": "ST1",
                "parent_id": "",
                "name": "Primary disease relation",
                "relation_to_case": "causal",
                "depth": 0,
                "discovered_by": "B01",
                "candidate_relevant": True,
                "required_research_unit_ids": ["S01", "S02"],
                "status": "audited_complete",
                "closure_reason": "Evidence and compound branches audited; no child relation remains.",
            }
        ],
    )

    candidate = {
        "candidate_id": "CAND1",
        "canonical_name": "Examplemol",
        "canonical_identifier": "CHEMBL123",
        "registry_identifiers": {"ChEMBL": "CHEMBL123", "PubChem": "PUBCHEM CID:123"},
        "structure_identity_key": "INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
        "chemical_node_id": "CHEM:INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
        "identity_source_ids": ["SRC1"],
        "entity_type": "discrete_chemical",
        "identity_verified": True,
        "human_gene": "GENE1",
        "worm_gene": "gene-1",
        "allele_mode": "loss_of_function",
        "worm_model": "gene-1 loss-of-function worm model",
        "origin": "de_novo",
        "source_research_unit_ids": ["S02"],
        "causal_paths": [
            {
                "path_id": "PATH1",
                "edge_ids": ["E1"],
                "claim_ids": ["CL1"],
                "start_node": "CHEM:INCHIKEY:AAAAAAAAAAAAAA-BBBBBBBBBB-C",
                "end_node": "CASE_WILD_TYPE_PHENOTYPE",
                "expected_rescue_direction": "toward_wild_type",
            }
        ],
        "rationale": "Audited evidence supports screening this exact compound for directional rescue.",
        "phenomic_interpretation": "Look for a multivariate shift toward the wild-type Tierpsy profile.",
        "decisive_uncertainty": "Worm exposure and effect magnitude remain unknown.",
        "dossier_path": "dossiers/CAND1.md",
        "council_disposition": "screen",
        "fact_audit_status": "verified",
    }
    write_jsonl(root / "candidate_records.jsonl", [candidate])

    role_agents = {
        "advocate": "council-advocate",
        "skeptic": "council-skeptic",
    }
    exchange_specs = [
        ("X1", "advocate", "case", ""),
        ("X2", "skeptic", "challenge", ""),
        ("X3", "advocate", "response", "X2"),
    ]
    exchanges = [
        {
            "exchange_id": exchange_id,
            "candidate_id": "CAND1",
            "role": role,
            "agent_id": role_agents[role],
            "exchange_type": exchange_type,
            "responds_to_id": responds_to,
            "content": f"Evidence-based {exchange_type} from {role}, with ambiguity retained.",
            "assertions": [
                {"claim_id": "CL1", "stance": role, "text": f"{role} assessment of CL1"}
            ],
            "claim_ids": ["CL1"],
            "critique_domains": [
                "mechanism_direction",
                "worm_target_orthology",
                "allele_relevance",
                "pharmacology_selectivity",
                "exposure_feasibility",
                "phenomics_confounding",
            ] if role == "skeptic" else [],
            "challenge_items": [
                {
                    "domain": domain,
                    "challenge": f"Challenge CL1 on {domain}",
                    "claim_ids": ["CL1"],
                    "resolution_required": True,
                }
                for domain in [
                    "mechanism_direction",
                    "worm_target_orthology",
                    "allele_relevance",
                    "pharmacology_selectivity",
                    "exposure_feasibility",
                    "phenomics_confounding",
                ]
            ] if exchange_type == "challenge" else [],
            "response_items": [
                {
                    "domain": domain,
                    "response": f"Qualified response for {domain}",
                    "claim_ids": ["CL1"],
                    "disposition": "qualified",
                }
                for domain in [
                    "mechanism_direction",
                    "worm_target_orthology",
                    "allele_relevance",
                    "pharmacology_selectivity",
                    "exposure_feasibility",
                    "phenomics_confounding",
                ]
            ] if exchange_type == "response" else [],
            "fact_audit_status": "verified",
        }
        for exchange_id, role, exchange_type, responds_to in exchange_specs
    ]
    debate_text = "# Candidate debate\n\n" + "\n".join(
        f"## {row['exchange_id']}\n{row['content']}" for row in exchanges
    )
    (root / "dossiers" / "CAND1.md").write_text(debate_text, encoding="utf-8")
    (root / "dossiers" / "CAND1_fact_audit.md").write_text(
        "# Council fact audit\n\n" + "\n".join(f"- {row['exchange_id']}: verified against CL1" for row in exchanges),
        encoding="utf-8",
    )
    write_jsonl(root / "council_exchanges.jsonl", exchanges)
    write_jsonl(
        root / "council_records.jsonl",
        [
            {
                "candidate_id": "CAND1",
                "advocate_agent_id": "council-advocate",
                "skeptic_agent_id": "council-skeptic",
                "fact_auditor_agent_id": "council-fact-audit",
                "direct_response_complete": True,
                "critique_checklist_complete": True,
                "novelty_challenge_resolved": True,
                "fact_audit_status": "verified",
                "material_claim_ids": ["CL1"],
                "claim_verdicts": [
                    {"claim_id": "CL1", "verdict": "qualified", "checked_source_ids": ["SRC1"]}
                ],
                "independent_checks": [
                    {
                        "resource": "PubMed",
                        "query": "independent fact check",
                        "executed_by_agent_id": "council-fact-audit",
                        "checked_source_ids": ["SRC1"],
                    }
                ],
                "surviving_causal_path_ids": ["PATH1"],
                "disposition": "screen",
                "exclusion_reason": "",
                "unresolved_material_claims": [],
                "debate_path": "dossiers/CAND1.md",
                "fact_audit_path": "dossiers/CAND1_fact_audit.md",
            }
        ],
    )
    write_runtime_fixture(root, units, ["CAND1"])


def expect_failure(label: str, mutation, expected_text: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        mutation(root)
        errors = validate_run(root)
        assert errors, f"{label}: validator unexpectedly passed"
        assert any(expected_text in error for error in errors), f"{label}: expected {expected_text!r}, got {errors}"


def mutate_jsonl(path: Path, mutator) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    mutator(rows)
    write_jsonl(path, rows)


def mutate_json(path: Path, mutator) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutator(value)
    write_json(path, value)


def remove_perspective(rows: list[dict[str, object]], perspective: str) -> None:
    rows[:] = [row for row in rows if row.get("perspective") != perspective]


def append_duplicate_candidate(rows: list[dict[str, object]]) -> None:
    duplicate = json.loads(json.dumps(rows[0]))
    duplicate["candidate_id"] = "CAND2"
    rows.append(duplicate)


def append_extra_exchange(rows: list[dict[str, object]]) -> None:
    duplicate = json.loads(json.dumps(rows[0]))
    duplicate["exchange_id"] = "X4"
    rows.append(duplicate)


def exercise_runtime_controller() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"},
        )
        first = next_action(root)
        assert first["action"] == "start_agent" and first["job_id"] == "BE01.worker"
        packet_hash = first["packet_hash"]
        attempt = start_job(root, "BE01.worker", "worker-agent-1")
        mutate_json(
            root / "program_state.json",
            lambda value: value.update(active_job_id="", active_attempt_id=""),
        )
        mutate_json(
            root / "execution_plan.json",
            lambda value: (
                next(job for job in value["jobs"] if job["job_id"] == "BE01.worker").update(
                    status="ready", attempt_count=0
                ),
                value.update(role_agents={}),
            ),
        )
        recovered = start_job(root, "BE01.worker", "worker-agent-1")
        assert recovered["attempt_id"] == attempt["attempt_id"]
        assert recovered["recovered_interrupted_start"] is True
        try:
            start_job(root, "BE02.worker", "worker-agent-2")
        except ValueError as exc:
            assert "Another job is active" in str(exc)
        else:
            raise AssertionError("controller allowed two active jobs")
        retry = fail_job(root, "BE01.worker", "rate_limit", 0, "TPM test")
        assert retry["action"] == "close_agent"
        released = release_agent(root, retry["attempt_id"], retry["agent_id"])
        retry = released["next"]
        assert retry["action"] == "wait_for_retry"
        mutate_json(
            root / "execution_plan.json",
            lambda value: next(job for job in value["jobs"] if job["job_id"] == "BE01.worker").update(
                retry_not_before="2000-01-01T00:00:00+00:00"
            ),
        )
        retry = next_action(root)
        assert retry["job_id"] == "BE01.worker" and retry["packet_hash"] == packet_hash
        assert retry["agent_action"] == "resume_assigned"
        attempt = start_job(root, "BE01.worker", "worker-agent-1")
        write_json(
            root / str(attempt["expected_result_path"]),
            {
                "job_id": "BE01.worker",
                "packet_hash": packet_hash,
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {},
                "approved_subtopics": [],
            },
        )
        validation = validate_result(root, "BE01.worker")
        assert validation["status"] == "valid"
        assert "\n" not in (root / str(attempt["expected_result_path"])).read_text(encoding="utf-8")
        completed = complete_job(root, "BE01.worker")
        assert completed["next"]["action"] == "close_agent"
        released = release_agent(
            root, completed["next"]["attempt_id"], completed["next"]["agent_id"]
        )
        assert released["next"]["job_id"] == "BE01.audit"

    compact = compact_payload(
        {
            "records": [
                {
                    "pmid": "123",
                    "title": "Relevant paper",
                    "abstract": "Relevant abstract",
                    "author_affiliations": ["large payload"],
                    "raw_xml": "large payload",
                }
            ]
        },
        "discovery",
    )
    assert compact["records"][0]["canonical_identifier"] == "123"
    assert "author_affiliations" not in compact["records"][0]
    assert "raw_xml" not in compact["records"][0]


def exercise_context_packets() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"},
        )
        source_rows = []
        claim_rows = []
        edge_rows = []
        for index in range(1, 6):
            source_rows.append(
                {
                    "source_id": f"SRC{index}",
                    "canonical_identifier": f"PMID:{index}",
                    "identifier_type": "PMID",
                    "title": f"Source {index}",
                    "year": 2025,
                    "source_kind": "primary_research",
                    "source_family": "literature",
                    "screen_decision": "include",
                    "original_pointer": f"raw_sources/source_{index}.txt",
                    "verification_method": "full_text",
                    "verification_scope": f"CL{index}",
                    "supported_claim_ids": [f"CL{index}"],
                    "raw_payload": "must never enter a packet",
                }
            )
            claim_rows.append(
                {
                    "claim_id": f"CL{index}",
                    "subtopic_id": "ST1",
                    "claim": "Audited evidence " + (str(index) * 800),
                    "source_ids": [f"SRC{index}"],
                    "audit_status": "verified",
                }
            )
            edge_rows.append(
                {
                    "edge_id": f"E{index}",
                    "from_node": "A",
                    "to_node": "B",
                    "claim_ids": [f"CL{index}"],
                    "audit_status": "verified",
                }
            )
        write_jsonl(root / "source_corpus.jsonl", source_rows)
        write_jsonl(root / "claim_ledger.jsonl", claim_rows)
        write_jsonl(root / "evidence_graph.jsonl", edge_rows)
        write_jsonl(
            root / "subtopic_registry.jsonl",
            [
                {
                    "subtopic_id": "ST1",
                    "parent_id": "",
                    "name": "Synthetic relation",
                    "relation_to_case": "causal",
                }
            ],
        )
        write_jsonl(
            root / "candidate_records.jsonl",
            [{"candidate_id": "LEAK_TEST", "causal_paths": [{"claim_ids": ["CL1"]}]}],
        )

        manifest_path, _ = build_packet(root, "GP02.worker", max_chars=12000)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["required_chunks"]) > 1
        seen_claims: set[str] = set()
        for chunk in manifest["required_chunks"]:
            assert chunk["bytes"] <= manifest["max_chunk_bytes"]
            packet = json.loads((root / chunk["path"]).read_text(encoding="utf-8"))
            context = packet["context"]
            assert packet["machine_contract"]["ledger_schemas"]
            assert Path(packet["run_root"]) == root.resolve()
            assert packet["path_contract"]["relative_paths_resolve_against"] == "run_root"
            assert "artifact_manifest" in json.dumps(context.get("dependency_results", [])) or not context.get("dependency_results")
            seen_claims.update(str(row["claim_id"]) for row in context["claims"])
            assert not context["candidate"]
            assert not context["dependency_results"]
            assert all("raw_payload" not in row for row in context["sources"])
        assert seen_claims == {f"CL{index}" for index in range(1, 6)}


def exercise_dependency_artifact_path_contract() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"},
        )
        receipt_path = root / "raw_sources" / "worker_receipt.json"
        receipt_path.parent.mkdir(exist_ok=True)
        receipt = compact_payload(
            [{"canonical_identifier": "PMID:1", "identifier_type": "PMID", "title": "Worker source"}],
            "verification",
        )
        write_json(receipt_path, receipt)
        result_path = root / "staging" / "BE01.worker.attempt001" / "result.json"
        result_path.parent.mkdir(parents=True)
        write_json(
            result_path,
            {
                "job_id": "BE01.worker",
                "packet_hash": "fixture",
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {
                    "source_corpus.jsonl": [
                        {
                            "source_id": "SRC1",
                            "compaction_receipt_path": "raw_sources/worker_receipt.json",
                        }
                    ]
                },
                "approved_subtopics": [],
            },
        )
        plan = json.loads((root / "execution_plan.json").read_text(encoding="utf-8"))
        worker = next(job for job in plan["jobs"] if job["job_id"] == "BE01.worker")
        worker.update(
            status="complete",
            result_path=str(result_path.relative_to(root)),
            result_hash=sha256(result_path),
        )
        write_json(root / "execution_plan.json", plan)
        manifest_path, _ = build_packet(root, "BE01.audit")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        packet = json.loads((root / manifest["required_chunks"][0]["path"]).read_text(encoding="utf-8"))
        dependency = packet["context"]["dependency_results"][0]
        artifact = dependency["artifact_manifest"][0]
        assert packet["run_root"] == str(root.resolve())
        assert dependency["path_base"] == "run_root"
        assert artifact["path"] == "raw_sources\\worker_receipt.json"
        assert artifact["immutable"] is True
        assert "source_compaction_receipt" in artifact["kinds"]
        assert artifact["exists"] is True
        assert Path(artifact["resolved_path"]) == receipt_path.resolve()
        assert artifact["sha256"] == sha256(receipt_path)


def exercise_immediate_rejection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(root, {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"})
        action = next_action(root)
        attempt = start_job(root, action["job_id"], "invalid-worker")
        write_json(
            root / str(attempt["expected_result_path"]),
            {
                "job_id": action["job_id"],
                "packet_hash": action["packet_hash"],
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {"source_corpus.jsonl": [{"source_id": "BROKEN"}]},
                "approved_subtopics": [],
            },
        )
        try:
            complete_job(root, action["job_id"])
        except ValueError as exc:
            assert "failed immediate validation" in str(exc)
        else:
            raise AssertionError("invalid staged evidence entered the canonical run")
        assert not (root / "source_corpus.jsonl").read_text(encoding="utf-8").strip()
        assert _read_json_for_test(root / "program_state.json")["active_job_id"] == action["job_id"]


def _read_json_for_test(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def exercise_final_repair_transition() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        mutate_jsonl(root / "source_corpus.jsonl", lambda rows: rows[0].update(content_verified=False))
        action = next_action(root)
        assert action["action"] == "start_agent"
        assert action["job_id"].startswith("FINAL_REPAIR001.worker")
        assert (root / "final_validation_errors_round001.json").is_file()
        manifest = _read_json_for_test(root / str(action["packet_manifest_path"]))
        packets = [
            _read_json_for_test(root / str(chunk["path"])) for chunk in manifest["required_chunks"]
        ]
        assert all(chunk["bytes"] <= manifest["max_chunk_bytes"] for chunk in manifest["required_chunks"])
        assert any(
            source.get("source_id") == "SRC1" and source.get("content_verified") is False
            for packet in packets
            for source in packet["context"]["sources"]
        )


def exercise_global_repair_packet_is_targeted() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        mutate_json(root / "program_state.json", lambda value: value.update(max_active_jobs=2))
        action = next_action(root)
        assert action["job_id"].startswith("FINAL_REPAIR001.worker")
        manifest = _read_json_for_test(root / str(action["packet_manifest_path"]))
        packets = [_read_json_for_test(root / str(item["path"])) for item in manifest["required_chunks"]]
        assert all(item["bytes"] <= manifest["max_chunk_bytes"] for item in manifest["required_chunks"])
        assert not any(packet["context"].get("claims") for packet in packets)
        assert not any(packet["context"].get("edges") for packet in packets)
        assert not any(packet["context"].get("sources") for packet in packets)
        ledger_records = [
            item
            for packet in packets
            for item in packet["context"].get("final_validation_snapshot", [])
            if item.get("kind") == "ledger_record"
        ]
        assert not ledger_records


def exercise_invalid_calibration_preflight() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        staged = root / "staging" / "invalid_calibration.json"
        claim = next(
            json.loads(line) for line in (root / "claim_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        )
        claim["calibration"] = "NOT_A_SCHEMA_VALUE"
        write_json(
            staged,
            {
                "job_id": "S01.audit",
                "packet_hash": "fixture",
                "all_chunks_processed": True,
                "outcome": "verified",
                "ledger_updates": {"claim_ledger.jsonl": [claim]},
                "approved_subtopics": [],
            },
        )
        errors = validate_staged_commit(
            root,
            {"job_id": "S01.audit", "kind": "unit_auditor", "unit_id": "S01"},
            [str(staged.relative_to(root))],
            "auditor-19",
        )
        assert any("invalid calibration" in error for error in errors), errors


def exercise_invalid_source_decision_preflight() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        staged = root / "staging" / "invalid_source_decision.json"
        source = next(
            json.loads(line) for line in (root / "source_corpus.jsonl").read_text(encoding="utf-8").splitlines()
        )
        source["screen_decision"] = "retained"
        write_json(
            staged,
            {
                "job_id": "S01.worker",
                "packet_hash": "fixture",
                "all_chunks_processed": True,
                "outcome": "completed",
                "ledger_updates": {"source_corpus.jsonl": [source]},
                "approved_subtopics": [],
            },
        )
        errors = validate_staged_commit(
            root,
            {"job_id": "S01.worker", "kind": "research_worker", "unit_id": "S01"},
            [str(staged.relative_to(root))],
            "worker-19",
        )
        assert any("screen_decision must be include or exclude" in error for error in errors), errors


def exercise_receipt_bound_multi_page_depth() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        searches = [
            json.loads(line)
            for line in (root / "search_log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        second_path = root / "raw_sources" / "second_page_compact.json"
        write_json(
            second_path,
            compact_payload(
                [
                    {
                        "canonical_identifier": "PMID:2",
                        "identifier_type": "PMID",
                        "title": "Screened but not acquired report",
                        "year": 2025,
                        "source_kind": "primary_research",
                    }
                ],
                "discovery",
                str(searches[0]["query_id"]),
            ),
        )
        token_hash = "a" * 64
        searches[0].update(
            result_count=2,
            deduplicated_count=2,
            screened_count=2,
            page_count=2,
            compact_payload_paths=[
                "raw_sources/source1_compact.json",
                "raw_sources/second_page_compact.json",
            ],
            pagination_trace=[
                {
                    "page_index": 1,
                    "receipt_path": "raw_sources/source1_compact.json",
                    "input_token_hash": "",
                    "output_token_hash": token_hash,
                },
                {
                    "page_index": 2,
                    "receipt_path": "raw_sources/second_page_compact.json",
                    "input_token_hash": token_hash,
                    "output_token_hash": "",
                },
            ],
        )
        write_jsonl(root / "search_log.jsonl", searches)
        assert not validate_run(root), "valid receipt-bound multi-page trace was rejected"


def exercise_cross_query_receipt_regression() -> None:
    """Regression for the UNC80 BE02 shared-receipt acceptance defect."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        searches = _read_jsonl_for_test(root / "search_log.jsonl")
        searches[1]["compact_payload_paths"] = list(searches[0]["compact_payload_paths"])
        searches[1]["pagination_trace"] = [
            {**searches[1]["pagination_trace"][0], "receipt_path": searches[0]["compact_payload_paths"][0]}
        ]
        write_jsonl(root / "search_log.jsonl", searches)
        errors = validate_run(root)
        assert any("query_id does not match the search record" in error for error in errors), errors


def _read_jsonl_for_test(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def exercise_recover_active_and_spawn_contract() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(root, {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"})
        action = next_action(root)
        assert action["spawn_contract"]["fork_turns"] == "none"
        assert len(str(action["spawn_prompt"]).splitlines()) == 3
        first = start_job(root, str(action["job_id"]), "orphaned-agent")
        recovered = recover_active(root, "replacement-agent", "synthetic lost task")
        assert recovered["packet_hash"] == first["packet_hash"]
        assert recovered["attempt_id"] != first["attempt_id"]
        attempts = _read_jsonl_for_test(root / "job_attempts.jsonl")
        assert attempts[0]["status"] == "orphaned" and attempts[0]["release_acknowledged"] is True
        assert attempts[1]["agent_id"] == "replacement-agent" and attempts[1]["status"] == "running"


def exercise_proactive_token_pacing() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(root, {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"})
        state = _read_json_for_test(root / "program_state.json")
        state["token_launches"] = [
            {"at": state["created_at"], "job_id": f"prior-{index}", "estimated_tokens": 60_000}
            for index in range(5)
        ]
        write_json(root / "program_state.json", state)
        action = next_action(root)
        assert action["action"] == "wait_for_pacing"
        assert action["rolling_estimated_tokens"] == 300_000


def exercise_subtopic_promotion_and_prior_screen_gating() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(root, {"human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function"})
        plan = _read_json_for_test(root / "execution_plan.json")
        _register_subtopics(
            root,
            plan,
            [{
                "subtopic_id": "PROMOTE_ME",
                "parent_id": "",
                "name": "Initial relation",
                "relation_to_case": "initial",
                "depth": 0,
                "candidate_relevant": False,
            }],
            "BE01.audit",
        )
        _register_subtopics(
            root,
            plan,
            [{
                "subtopic_id": "PROMOTE_ME",
                "parent_id": "",
                "name": "Initial relation",
                "relation_to_case": "initial",
                "depth": 0,
                "candidate_relevant": True,
            }],
            "BE02.audit",
        )
        evidence_worker = next(job for job in plan["jobs"] if job["job_id"] == "SE.PROMOTE_ME.worker")
        evidence_audit = next(job for job in plan["jobs"] if job["job_id"] == "SE.PROMOTE_ME.audit")
        assert evidence_worker["status"] == "ready" and evidence_audit["status"] == "planned"
        _register_subtopics(
            root,
            plan,
            [{
                "subtopic_id": "PROMOTE_ME",
                "parent_id": "",
                "name": "Corrected relation",
                "relation_to_case": "corrected and candidate relevant",
                "depth": 0,
                "candidate_relevant": True,
            }],
            "BE03.audit",
        )
        subtopic = next(
            row for row in [json.loads(line) for line in (root / "subtopic_registry.jsonl").read_text(encoding="utf-8").splitlines()]
            if row["subtopic_id"] == "PROMOTE_ME"
        )
        assert subtopic["candidate_relevant"] is True and subtopic["name"] == "Corrected relation"
        assert any(job["job_id"] == "SC.PROMOTE_ME.worker" for job in plan["jobs"])

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {
                "human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function",
                "prior_screen_path": "hidden.csv", "benchmark_mode": "blinded",
            },
        )
        units = [json.loads(line) for line in (root / "research_units.jsonl").read_text(encoding="utf-8").splitlines()]
        assert all(unit["perspective"] != "prior_screen_context" for unit in units)

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        initialize(
            root,
            {
                "human_gene": "GENE1", "worm_gene": "gene-1", "allele_mode": "loss_of_function",
                "prior_screen_path": "visible.csv",
            },
        )
        units = [json.loads(line) for line in (root / "research_units.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(unit["perspective"] == "prior_screen_context" for unit in units)


def main() -> int:
    exercise_runtime_controller()
    exercise_context_packets()
    exercise_dependency_artifact_path_contract()
    exercise_immediate_rejection()
    exercise_final_repair_transition()
    exercise_global_repair_packet_is_targeted()
    exercise_invalid_calibration_preflight()
    exercise_invalid_source_decision_preflight()
    exercise_receipt_bound_multi_page_depth()
    exercise_cross_query_receipt_regression()
    exercise_recover_active_and_spawn_contract()
    exercise_proactive_token_pacing()
    exercise_subtopic_promotion_and_prior_screen_gating()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        make_valid_run(root)
        errors = validate_run(root)
        assert not errors, f"valid fixture failed: {errors}"
        csv_path, markdown_path = build_outputs(root)
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1 and rows[0]["drug_name"] == "Examplemol"
        assert "Examplemol" in markdown_path.read_text(encoding="utf-8")

    expect_failure(
        "rate-limit bypass",
        lambda root: mutate_jsonl(root / "research_units.jsonl", lambda rows: rows[0].update(rate_limit_pending=True)),
        "rate_limit_pending must be false",
    )
    expect_failure(
        "nonchemical candidate",
        lambda root: mutate_jsonl(root / "candidate_records.jsonl", lambda rows: rows[0].update(entity_type="genetic_manipulation")),
        "entity_type must be discrete_chemical",
    )
    expect_failure(
        "unverified source",
        lambda root: mutate_jsonl(root / "source_corpus.jsonl", lambda rows: rows[0].update(content_verified=False)),
        "lacks original-content verification",
    )
    expect_failure(
        "self-reported audit pass",
        lambda root: mutate_jsonl(root / "unit_audits.jsonl", lambda rows: rows[0].update(final_status="repair_required")),
        "final_status must be verified",
    )
    expect_failure(
        "superficial council",
        lambda root: write_jsonl(root / "council_exchanges.jsonl", []),
        "compact council must contain exactly",
    )
    expect_failure(
        "incomplete combined sceptic",
        lambda root: mutate_jsonl(
            root / "council_exchanges.jsonl",
            lambda rows: next(row for row in rows if row.get("role") == "skeptic").update(
                critique_domains=["mechanism_direction"]
            ),
        ),
        "sceptic checklist lacks",
    )
    expect_failure(
        "fact auditor skipped independent retrieval",
        lambda root: mutate_jsonl(root / "council_records.jsonl", lambda rows: rows[0].update(independent_checks=[])),
        "fact auditor ran no independent source check",
    )
    expect_failure(
        "unsupported surviving path",
        lambda root: mutate_jsonl(
            root / "council_records.jsonl",
            lambda rows: rows[0]["claim_verdicts"][0].update(verdict="unsupported"),
        ),
        "surviving path PATH1 has a claim without a supporting fact-audit verdict",
    )
    expect_failure(
        "unscreened retrieval",
        lambda root: mutate_jsonl(root / "search_log.jsonl", lambda rows: rows[0].update(screened_count=0)),
        "every deduplicated record must be screened",
    )
    expect_failure(
        "inflated search depth",
        lambda root: mutate_jsonl(
            root / "search_log.jsonl",
            lambda rows: rows[0].update(
                result_count=1000,
                deduplicated_count=1000,
                screened_count=1000,
                page_count=100,
            ),
        ),
        "result_count is not proven by compact receipt records",
    )
    expect_failure(
        "unbound acquisition count",
        lambda root: mutate_jsonl(
            root / "search_log.jsonl", lambda rows: rows[0].update(acquired_source_ids=[])
        ),
        "acquired_count is not proven by acquired_source_ids",
    )
    expect_failure(
        "disconnected pagination trace",
        lambda root: mutate_jsonl(
            root / "search_log.jsonl",
            lambda rows: rows[0]["pagination_trace"][0].update(input_token_hash="a" * 64),
        ),
        "pagination continuation chain is disconnected",
    )
    expect_failure(
        "non-compound source laundering",
        lambda root: mutate_jsonl(
            root / "candidate_records.jsonl",
            lambda rows: rows[0].update(source_research_unit_ids=["B01"]),
        ),
        "is not a compound-generating unit",
    )
    expect_failure(
        "skipped perspective",
        lambda root: mutate_jsonl(root / "research_units.jsonl", lambda rows: remove_perspective(rows, "phenotype_first")),
        "missing global perspective phenotype_first",
    )
    expect_failure(
        "compressed agents",
        lambda root: mutate_jsonl(root / "research_units.jsonl", lambda rows: rows[1].update(worker_agent_id=rows[0]["worker_agent_id"])),
        "worker agent reused",
    )
    expect_failure(
        "unresolved novelty challenge",
        lambda root: mutate_jsonl(root / "council_records.jsonl", lambda rows: rows[0].update(novelty_challenge_resolved=False)),
        "therapeutic-conservatism challenge is unresolved",
    )
    expect_failure(
        "enough-to-write closure",
        lambda root: mutate_jsonl(root / "unit_audits.jsonl", lambda rows: rows[0].update(closure_basis="Enough to write the report.")),
        "invalid completion rationale",
    )
    expect_failure(
        "parallel runtime",
        lambda root: mutate_json(root / "program_state.json", lambda value: value.update(max_active_jobs=2)),
        "max_active_jobs must be 1",
    )
    expect_failure(
        "incomplete runtime job",
        lambda root: mutate_json(root / "execution_plan.json", lambda value: value["jobs"][0].update(status="ready")),
        "final status must be complete",
    )
    expect_failure(
        "council order drift",
        lambda root: mutate_json(
            root / "execution_plan.json",
            lambda value: next(job for job in value["jobs"] if job.get("stage") == "skeptic_review").update(stage="skeptic_summary"),
        ),
        "controller council turn order is incomplete or incorrect",
    )
    expect_failure(
        "council role collapse",
        lambda root: mutate_json(
            root / "execution_plan.json",
            lambda value: value["role_agents"].update(
                {"council:CAND1:skeptic": value["role_agents"]["council:CAND1:advocate"]}
            ),
        ),
        "one agent is assigned to multiple independent roles",
    )
    expect_failure(
        "bulky source payload",
        lambda root: mutate_jsonl(root / "source_corpus.jsonl", lambda rows: rows[0].update(raw_payload={"large": "object"})),
        "prohibited bulky payload fields",
    )
    expect_failure(
        "retry packet substitution",
        lambda root: mutate_jsonl(root / "job_attempts.jsonl", lambda rows: rows[0].update(packet_hash="different")),
        "no completed attempt matches the final packet hash",
    )
    expect_failure(
        "agent not released",
        lambda root: mutate_jsonl(
            root / "job_attempts.jsonl",
            lambda rows: rows[0].update(release_acknowledged=False, released_at=""),
        ),
        "agent release was not acknowledged",
    )
    expect_failure(
        "worker query reused as audit",
        lambda root: mutate_jsonl(
            root / "search_log.jsonl",
            lambda rows: next(row for row in rows if "audit_missing_branch" in row["query_id"]).update(
                executor_role="worker", executed_by_agent_id="worker-01"
            ),
        ),
        "was not independently executed by its auditor",
    )
    expect_failure(
        "pagination left open",
        lambda root: mutate_jsonl(
            root / "search_log.jsonl", lambda rows: rows[0].update(pagination_complete=False)
        ),
        "pagination or continuation is not exhausted",
    )
    expect_failure(
        "source compaction forgery",
        lambda root: mutate_jsonl(
            root / "source_corpus.jsonl", lambda rows: rows[0].update(compaction_record_hash="bad")
        ),
        "compaction record hash does not resolve exactly once",
    )
    expect_failure(
        "edited compact receipt",
        lambda root: mutate_json(
            root / "raw_sources" / "source1_compact.json",
            lambda value: value["records"][0].update(title="Edited after compaction"),
        ),
        "compact receipt record 1 hash mismatch",
    )
    expect_failure(
        "disconnected candidate path",
        lambda root: mutate_jsonl(
            root / "evidence_graph.jsonl", lambda rows: rows[0].update(from_node="UNRELATED_NODE")
        ),
        "path does not start at the candidate chemical node",
    )
    expect_failure(
        "cross-registry duplicate compound",
        lambda root: mutate_jsonl(root / "candidate_records.jsonl", append_duplicate_candidate),
        "duplicate cross-registry chemical identity",
    )
    expect_failure(
        "extra council summary",
        lambda root: mutate_jsonl(root / "council_exchanges.jsonl", append_extra_exchange),
        "compact council must contain exactly",
    )
    expect_failure(
        "fact auditor omitted material debate claim",
        lambda root: mutate_jsonl(
            root / "council_records.jsonl", lambda rows: rows[0].update(material_claim_ids=[])
        ),
        "material_claim_ids do not exactly cover the debate",
    )
    print("SELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
