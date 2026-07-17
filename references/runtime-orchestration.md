# Deterministic Runtime

The Python controller owns scheduling, state transitions, retries, packet identity, audit repair loops, and council order. The main agent only executes the controller's requested action.

## Start And Continue

Initialize a new, empty run folder:

```text
py -3 scripts/orchestrate_program.py init <run_folder> --human-gene <gene> --worm-gene <gene> --allele-mode <mode>
```

Use a case JSON file instead when optional input fields are present. Never initialize over an earlier run. On non-Windows systems use a confirmed working Python 3 interpreter; do not rely on a broken Windows Store `python` alias.

Request work with `next`. It returns one of:

- `start_agent`: start exactly one subagent with the named role and packet manifest;
- `wait_for_pacing`: wait until the proactive token-window cooldown expires;
- `close_agent`: close the recorded subagent, then acknowledge it with the named `release` command;
- `resume_active_job`: resume only that recorded job;
- `checkpoint`: end the current bounded execution slice with all state persisted;
- `wait_for_retry`: wait until the recorded retry time without advancing;
- `blocked`: report the unrecoverable reason;
- `finalize`: validate and build final outputs.

Before agent work, call `start <run_folder> <job_id> <agent_id>`. For `agent_action=spawn_new`, create a new agent with `fork_turns="none"` and pass exactly the controller-emitted `spawn_prompt`; it contains only the job ID, absolute packet manifest, and absolute expected result path. For `agent_action=resume_assigned`, resume the recorded closed agent's existing task. Require every chunk and one `result.json` at the expected path.

Every packet states an absolute `run_root`. Resolve every relative result, receipt, source, dossier, and dependency path against that root—never against the current directory, packet directory, result directory, or an inferred staging directory. Dependency entries include controller-generated artifact manifests with resolved paths, existence, sizes, and hashes. An auditor may report an artifact missing only after resolving it against `run_root` and confirming the manifest or resolved file is absent; the lack of a copy inside the worker attempt directory is not evidence of absence.

First run `validate-result <run_folder> <job_id>`. Submit with `complete <run_folder> <job_id>` only after validation passes. The result must repeat the job ID and packet hash, set `all_chunks_processed=true`, state a permitted outcome, and contain only staged `ledger_updates`. Workers use `completed`; auditors use `verified`, `evidence_absent_complete`, or `repair_required`. The controller rejects invalid staged records while the job remains active. Repair the same result and resubmit; invalid data never enters canonical ledgers.

After acknowledged completion or failure, the controller returns `close_agent`. Close that exact subagent, then run `release <run_folder> <attempt_id> <agent_id>`. No later job can start before release is acknowledged. This applies to successful jobs, retries, and rate limits. Closed assigned agents can be resumed for retries or the advocate response without occupying an open-agent slot.

## Staged Result

Write this core shape to the expected result path:

```text
job_id,packet_hash,all_chunks_processed,outcome,ledger_updates,approved_subtopics
```

`ledger_updates` maps only canonical ledger filenames to arrays of complete records. Never edit canonical ledgers directly. `approved_subtopics` contains auditor-approved subtopic records with `subtopic_id`, `name`, `relation_to_case`, `parent_id`, `depth`, and `candidate_relevant`.

An accepting unit audit also supplies `unit_status=audited_complete` or `evidence_absent_complete`; the latter requires `absence_reason`. The closure worker maps unresolved branches, then a different closure auditor supplies `closure_confirmed`. A merge audit supplies the resolved `candidate_ids`.

A council fact audit supplies `novelty_challenge_resolved`, `critique_checklist_complete`, `unresolved_material_claims`, `material_claim_ids`, `claim_verdicts`, `independent_checks`, `surviving_causal_path_ids`, and `verified_exclusion_reasons`. Each claim verdict includes `claim_id`, `verdict`, and `checked_source_ids`; each independent check includes `resource`, `query`, `executed_by_agent_id`, and `checked_source_ids`. On council repair, use `reopen_stage=skeptic_review` and list any `required_specialist_checks`.

After interruption, call `resume`. Do not infer work from chat history. If the assigned task cannot be resumed, use `recover-active <run_folder> <new_agent_id>` to mark the orphaned attempt, preserve packet identity, and create a replacement attempt for the same role.

If `resume` returns a ready repair with `agent_action=resume_assigned` but that prior agent task is unavailable (for example after crossing into a fresh chat), spawn one replacement agent with `fork_turns="none"` and exactly the returned three-line `spawn_prompt`, then run `recover-ready <run_folder> <job_id> <new_agent_id> --reason <reason>` before `start`. This command applies only to ready repairs with mandatory feedback, preserves and verifies the existing packet and feedback hashes, rejects an agent assigned to any independent role, and appends a `ready_repair_reassigned` audit event. Never use it to rotate an available role or to replace a running attempt.

## Failures

Use `fail` with `rate_limit`, `spawn_failure`, or `transient` for recoverable failures. The controller preserves the job, scope, packet manifest, packet hash, dependencies, and ordering. It cannot select later work while the earliest job is waiting. Obey proactive `wait_for_pacing` actions and the controller's escalating rate-limit cooldown.

Even after failure, close and release the assigned agent before the retry. The same role-agent assignment and packet hash remain authoritative.

Use `unrecoverable` only when retries cannot restore required role independence or a genuine user-only decision is missing. Never use failure handling to reduce scope.

## Staging And Audit

Worker ledger changes remain in its staged result. Its auditor receives the worker result path and independently checks decisive sources plus a missing branch. The controller merges worker and audit changes only after `verified` or `evidence_absent_complete`.

The controller's dependency artifact manifest proves file presence and content identity at packet-build time. Auditors still inspect scientific content and provenance, but must not replace the controller's path resolution with an ad hoc staging-folder search.

On `repair_required`, the controller returns the same scientific unit to its worker with a new repair round and includes both the auditor's result and the complete immutable lineage of earlier worker results in the new packet. A repaired worker may submit a delta; the controller carries forward and validates the earlier worker-owned searches and provenance at the next audit/commit. Auditors must never recreate or relabel those worker-owned records. On newly approved subtopics, it reopens evidence closure and schedules the new units before later phases.

## Invariants

- Keep `schema_version=3` and `max_active_jobs=1`.
- Never edit `execution_plan.json`, `program_state.json`, `job_attempts.jsonl`, packet manifests, or orchestration events by hand.
- Never start a second agent while `active_job_id` or `pending_agent_release_id` is set.
- Reuse the assigned council-role agent for that candidate's later replies; never reuse an agent across independent roles.
- Treat packet files, canonical ledgers, and controller state as authoritative; treat prose updates as non-authoritative.
