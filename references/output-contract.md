# Runtime And Output Contract

## Authoritative Runtime Files

Use these structured artifacts in the run root:

- `case.json`
- `program_state.json`
- `execution_plan.json`
- `orchestration.jsonl`
- `job_attempts.jsonl`
- `source_corpus.jsonl`
- `search_log.jsonl`
- `claim_ledger.jsonl`
- `evidence_graph.jsonl`
- `subtopic_registry.jsonl`
- `research_units.jsonl`
- `unit_audits.jsonl`
- `candidate_records.jsonl`
- `council_records.jsonl`
- `council_exchanges.jsonl`
- `dossiers/` containing candidate rationale and full debate files.
- `packets/` containing immutable context manifests and mandatory chunks.
- `staging/` containing one result per job attempt until audit acceptance.
- `raw_sources/` containing bulky source payloads that must not enter agent packets.

JSONL files are append-compatible but must contain one current canonical record per ID at finalization. During repair, append events to `orchestration.jsonl` and replace the canonical structured record deliberately; do not leave conflicting duplicate IDs.

`program_state.json` and `execution_plan.json` use `schema_version=3`. The plan sets `max_active_jobs=1`, preserves the fixed seed topology, records every dynamic subtopic and final-repair job, and stores packet and result hashes. `program_state.json` contains at most one active job and attempt and at most one mandatory pending agent release.

The state also persists bounded-slice counters, checkpoint status, recent estimated token launches, the proactive per-minute token budget, and rate-limit strikes. These fields control timing only and never reduce scientific scope.

Each `job_attempts.jsonl` record contains:

```text
attempt_id,job_id,agent_id,packet_hash,packet_manifest_path,expected_result_path,status,started_at,finished_at,failure_kind,release_acknowledged,released_at
```

Retries may add attempts but may not alter the packet hash unless an audited repair changes the scientific context. No attempt may remain running or unreleased at finalization.

## Research Unit Record

Each `research_units.jsonl` object contains:

```text
unit_id,unit_type,subtopic_id,perspective,worker_agent_id,auditor_agent_id,status,audit_status,planned_query_families,completed_query_families,independent_audit_query_ids,rate_limit_pending,known_high_yield_search_remaining,unresolved_repair_count,candidate_ids,absence_reason
```

Final unit status is `audited_complete` or `evidence_absent_complete`. The worker and auditor differ. Planned and completed query families match. Independent audit query IDs resolve to `search_log.jsonl`. Rate-limit pending is false, known high-yield work is empty, and unresolved repairs equal zero. Evidence-absent units have no candidates and state the searched absence.

Each `unit_audits.jsonl` object contains:

```text
audit_id,unit_id,auditor_agent_id,checked_source_ids,independent_query_ids,material_findings,repairs_completed,perspective_distinctness_verified,source_overlap_assessment,final_status,closure_basis
```

The audit agent matches the unit's auditor and differs from its worker. Checked sources and independent searches resolve to the ledgers. The auditor verifies that the unit followed its distinct question and assesses biologically expected versus suspicious source overlap. `final_status=verified`; material findings have completed repairs or remain explicitly unresolved, in which case the unit cannot close.

## Search Record

Every `source_corpus.jsonl` record is flat and contains the fields defined in `program_contract.py`, including `compaction_receipt_path` and `compaction_record_hash`. The receipt must be a schema-2 output from `compact_source_payload.py`; its record hash, canonical identifier, and title must match. Unknown, renamed, nested, or bulky source fields are rejected.

Use only `screen_decision=include` or `exclude`. A claim-supporting source must use `include`; synonyms such as `retained`, `keep`, or `accepted` are rejected during staged validation.

Each `search_log.jsonl` object contains:

```text
query_id,research_unit_id,subtopic_id,query_family,resource,query,result_count,deduplicated_count,screened_count,acquired_count,original_verified_count,page_count,pagination_complete,continuation_exhausted,compact_payload_paths,pagination_trace,acquired_source_ids,original_verified_source_ids,executed_by_agent_id,executor_role,origin_job_id,retained_source_ids,new_subtopic_ids,new_claim_ids,new_candidate_ids,outcome,rate_limit_pending,closure_note
```

Each declared family must have a worker-executed search record. `origin_job_id` must be the controller job whose staged result created that record, preventing an auditor from manufacturing or relabelling worker searches. Auditors add a separately identified missing-branch or counterevidence query under their own agent ID and audit-job ID. Every compact receipt record must have `query_id` exactly equal to the owning search record; a combined or reused receipt cannot prove another query. Every deduplicated record is screened. `result_count` is the sum of compact-receipt records; `deduplicated_count` is recomputed from their canonical identities; and `page_count` equals the number of unique receipts. `acquired_count` and `original_verified_count` equal the unique IDs in `acquired_source_ids` and `original_verified_source_ids`, which resolve to appropriately verified source records. `pagination_trace` orders every receipt, chains hashed continuation tokens, and ends at an exhausted continuation. No final search may remain rate-limited, pending, or rhetorically closed.

## Candidate Record

Each `candidate_records.jsonl` object includes authoritative registry identifiers plus `structure_identity_key`, `chemical_node_id`, and `causal_paths`. `structure_identity_key` is a standardized InChIKey or a canonical-SMILES SHA-256 fallback and is unique across the run, preventing cross-database duplicate molecules.

Every causal path lists `path_id`, ordered `edge_ids`, `claim_ids`, `start_node`, `end_node`, and `expected_rescue_direction`. The edges must form one connected chain from `CHEM:<structure_identity_key>` to `CASE_WILD_TYPE_PHENOTYPE`; each edge is audited with `directionality_status=supports_rescue`, and each claim is compatible with the case allele mode.

## Council Record

Each `council_records.jsonl` object contains:

```text
candidate_id,advocate_agent_id,skeptic_agent_id,fact_auditor_agent_id,direct_response_complete,critique_checklist_complete,novelty_challenge_resolved,fact_audit_status,material_claim_ids,claim_verdicts,independent_checks,surviving_causal_path_ids,disposition,exclusion_reason,unresolved_material_claims,debate_path,fact_audit_path
```

The three agents are distinct from one another and from research workers/auditors. The advocate is resumed for its response. `material_claim_ids` exactly equals all debate claim IDs, and every one receives a verdict. Independent checks carry the fact auditor's assigned agent ID and use original-verified sources. A screened compound has at least one surviving connected candidate path whose claims all receive `supported` or `qualified` verdicts.

Each `council_exchanges.jsonl` object contains:

```text
exchange_id,candidate_id,role,agent_id,exchange_type,responds_to_id,content,assertions,claim_ids,critique_domains,challenge_items,response_items,fact_audit_status
```

For every candidate, require exactly one advocate `case`, one combined sceptic `challenge`, and one advocate `response` linked to that challenge. `assertions` provides one structured entry per claim ID. The challenge contains one substantive item for every mandatory critique domain, and the response answers each item. Extra summaries cannot satisfy or supplement the three exchanges. Agent IDs must match assigned roles. The fact auditor independently checks every material exchange claim rather than joining the exchange.

## Program Gates

`program_state.json` must state true for `broad_evidence_complete`, `subtopic_closure_complete`, `de_novo_perspectives_complete`, `candidate_universe_complete`, and `council_complete`, with `current_phase=ready_for_finalization`. The validator independently checks the underlying records; these booleans cannot override defects.

At finalization, every execution-plan job is complete, no active job exists, concurrency is one, all dependencies are complete, all packet manifests resolve, and the council stage sequence exists for every candidate.

## User-Facing Outputs

`scripts/build_final_outputs.py` creates exactly:

- `17_screening_candidates.csv`
- `18_candidate_rationales.md`

The CSV contains only council-included exact compounds and these columns:

```text
candidate_id,drug_name,human_gene,worm_gene,allele_mode,worm_disease_model,dossier_path
```

The Markdown contains one concise section per CSV compound: model, evidence origin, rationale, expected phenomic interpretation, and decisive uncertainty. Internal evidence and debate files exist for reasoning and audit, not as required reading for the user.

Never call an internal universe of hypotheses or interventions a screening panel. Never add controls, genetic experiments, compound classes, or rejected entities to either final file.
