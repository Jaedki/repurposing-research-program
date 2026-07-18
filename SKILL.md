---
name: repurposing-research-program
description: "Run the deterministic living-evidence repurposing programme for a supplied human gene, disease, or phenotype. Use when the user wants exact drugs or natural compounds discovered through independent evidence perspectives, independently audited, and deterministically ranked for human therapeutic benefit."
---

# Repurposing Research Program

Produce a ranked set of exact compounds and a reusable evidence graph for a human therapeutic outcome.

## Scope

At least one human gene, disease, or phenotype identifies the case. Input may be ordinary language or structured. If all three are absent, ask one concise question. Organism, cell, organoid, behavioural, and other experimental-model details are optional context, not required inputs or terminal endpoints.

## Authority

Read the human-readable contracts before initialization:

- `references/workflow.md`: stages, isolation, endpoint, and closure
- `references/evidence.md`: evidence, search, sources, and exact compounds
- `references/runtime.md`: controller lifecycle, packets, retries, and resume
- `references/ranking.md`: audit, ranking, council, and outputs

Runtime code and emitted artifacts are authoritative:

- `scripts/program_contract.py` owns schemas, controlled values, perspective and query contracts, retry parameters, ranking components and caps, and invariants.
- `scripts/orchestrate_program.py` and `scripts/program_runtime.py` own state transitions, immutable packets, job isolation, checkpoints, retries, commits, and finalization.
- `scripts/validate_program.py`, `scripts/ranking.py`, and `scripts/build_final_outputs.py` own validation, deterministic ranking, and final output construction.

The references describe these contracts. If prose differs from the controller packet, runtime artifacts, or validation result, the runtime governs.

## Execution and outputs

Initialize `repurposing_program_runs/<human-case-slug>_<YYYYMMDD_HHMMSS>/` with `scripts/orchestrate_program.py init` and the supplied human fields. Subsequent work follows controller actions until `finalize`.

Each `start_agent` action maps to one isolated subagent with `fork_turns="none"`; its exact three-line `spawn_prompt` is the complete handoff. The subagent processes every packet chunk and writes only the expected staged result. A result passes `validate-result` before `complete`; canonical state changes only through controller commit. Controlled failures are recorded with `fail`, and retry timing remains controller-owned.

At a checkpoint, report that progress is persisted and that `continue` resumes deterministically. At `finalize`, run `scripts/build_final_outputs.py <run_folder>`. It validates the run and creates `ranked_compound_candidates.csv` and `candidate_justifications.md`; final outputs are runtime-built rather than hand-edited.

The programme is hypothesis generation for expert review. Report closure as `no known decision-changing search branch remains within the documented scope`; this does not claim universal exhaustiveness or clinical efficacy.
