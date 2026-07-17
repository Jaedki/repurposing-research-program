---
name: repurposing-research-program
description: "Run exhaustive, living-evidence drug and natural-compound repurposing research for genetic C. elegans disease models. Use when the user invokes this skill or asks for repurposing research and provides a human gene, worm gene, and allele mode in ordinary language or key-value form, seeking a broad exact-compound screening panel derived from recursive literature research, independent mechanistic and phenotype perspectives, adversarial review, and fact checking."
---

# Repurposing Research Program

Run one long-lived research programme whose only user-facing scientific product is a broad panel of exact named compounds that may move the specified worm disease model toward wild type. Build understanding before candidate generation, but keep the evidence base living: later research may add or correct sources, claims, relations, and subtopics after independent audit.

## Input

Accept the case directly from the user's invocation in ordinary language or key-value form. Require only the human gene, worm gene, and allele mode. Do not require an input-block header.

If a required value is missing, ask one concise question naming only the missing value or values. Do not print an example, template, field list, or suggested optional inputs. If all three values are present, begin immediately. Preserve additional case information only when the user volunteers it; do not solicit it.

No seed drug is required or assumed. The downstream assay context is Andre Brown/Tierpsy-style high-throughput C. elegans behavioural phenomics. Do not invent wet-lab protocol parameters unless asked.

## Read Before Running

Read all of these authoritative contracts before creating the run folder:

- `references/runtime-orchestration.md`
- `references/program-workflow.md`
- `references/evidence-system.md`
- `references/perspective-research.md`
- `references/compound-discovery.md`
- `references/audit-and-council.md`
- `references/output-contract.md`

## Core Execution Rules

1. Initialize and advance every run through `scripts/orchestrate_program.py`. Never choose the next lane, council turn, phase transition, or retry manually.
2. Keep exactly one open subagent. For every controller action with `agent_action=spawn_new`, call `spawn_agent` with `fork_turns="none"`. The prompt must contain only the controller-emitted `spawn_prompt`: job ID, absolute packet-manifest path, and absolute expected-result path. Never include the user conversation, a summary, conclusions, or scientific context. Resume an assigned role only through that existing agent's task. Close it after each controller-acknowledged attempt and complete the `release` handshake before starting or resuming another. The main agent is only the bridge between the controller and that subagent; it must not simulate workers, auditors, or council roles.
3. Build a broad discovery corpus and living evidence graph before compound generation.
4. Recursively register every material relation to the gene, allele, model, phenotype, pathway, cell, circuit, downstream effect, modifier, vulnerability, symptom, and assay readout. The controller creates separate evidence and, when relevant, compound units.
5. Run all mandatory global perspectives independently: direct molecular rescue, phenotype-first reversal, vulnerability/inverse phenotype, compensatory network, maximal novelty, and natural compounds. Add behavioural-data-first or prior-screen context only when the corresponding data were volunteered; never expose prior hits in blinded benchmark mode.
6. Give each agent only the immutable context-packet manifest emitted for its job. Every packet contains the absolute `run_root`, run-root-relative path contract, controller-generated dependency artifact manifests, versioned role contract, schemas, mandatory query families, and all required context. Require every packet chunk; never substitute whole-run ledgers or conversation summaries.
7. Stage worker output. Before handoff, every worker or auditor must run `orchestrate_program.py validate-result <run_folder> <job_id>` and repair the staged file until it passes. The controller validates again before canonical integration, and commits a worker-plus-auditor result only after a different audit agent verifies it.
8. Merge all exact compounds before council review. Do not pre-prune novel or indirect compounds merely because direct exact-model rescue is absent.
9. Run the compact three-agent council for every compound: advocate case, combined sceptic review, advocate response, then independent source fact audit.
10. Generate final outputs only through `scripts/build_final_outputs.py` after the controller returns `finalize`. Final validation defects create a serial audited repair pair; unchanged defects block after three repair rounds instead of looping or pretending completion.

If agent creation fails, record `spawn_failure` and retry the same job serially. If required independence remains unavailable, record an unrecoverable block rather than merge roles.

Rate limits change retry timing only. Record `rate_limit`, retain the same packet hash, obey the controller's proactive `wait_for_pacing` or retry cooldown, and retry that job. Never advance, regenerate a narrower packet, or replace unfinished retrieval with inference.

## Candidate Boundary

An output candidate is one exact, discrete chemical entity with authoritative registry identifiers, a structure identity key, and at least one connected audited graph path from that chemical to `CASE_WILD_TYPE_PHENOTYPE`. Every edge must support rescue and every path claim must match the allele mode. Approved drugs, investigational compounds, defined bioactive compounds, and exact natural compounds are eligible.

Never place genetic manipulations, RNAi, transgenes, optogenetics, diets, starvation, vehicles, assay conditions, targets, pathways, cell types, compound classes, extracts, mixtures, or controls in the candidate universe or final CSV. An internal mechanistic idea may remain in the evidence graph until an exact compound is found.

Known prior-model hits may remain candidates, but label their origin and keep them separate from evidence of independent de novo discovery. They can never satisfy completion of the de novo perspective programme.

## Run Behaviour

Create `repurposing_program_runs/<human_gene>_<worm_gene>_<YYYYMMDD_HHMMSS>/`. Persist all state in structured JSON/JSONL artifacts. Treat conversation summaries and Markdown assertions as non-authoritative.

Create the run through the controller `init` command. Then obey each controller action exactly: `next -> start -> validate-result -> complete -> close_agent -> release`. Close the subagent only after the controller acknowledges completion or failure, then call `release` before requesting further work. When `agent_action=resume_assigned`, resume that recorded agent ID rather than spawning another. If a running task no longer exists, use `recover-active <run_folder> <new_agent_id>`. If a ready repair is assigned to a task unavailable after a chat boundary, spawn only from its existing three-line prompt and use `recover-ready <run_folder> <job_id> <new_agent_id>` before `start`. Never silently replace an assignment. After interruption or user `continue`, run `resume`; do not reconstruct or edit state by hand.

Run in bounded execution slices. End a slice only when the controller returns `checkpoint`, normally after one worker/auditor pair or 20-30 minutes. State that the checkpoint is persisted and that `continue` will resume deterministically. Do not treat a checkpoint as scientific completion. Within a slice, submit each result and immediately execute the controller's next action.

Do not send ordinary progress updates inside a slice. Reply only for missing required input, a genuine user-only decision, an unrecoverable block, a controller checkpoint, or final completion. A worker saying it has enough, elapsed time, file length, source count, or consensus is never a completion reason.

The strongest permitted coverage statement is: `no known decision-changing search branch remains within the documented scope`. Never claim universal exhaustiveness.
