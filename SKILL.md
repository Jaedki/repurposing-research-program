---
name: repurposing-research-program
description: "Run a deterministic, living-evidence drug and natural-compound repurposing programme whose primary endpoint is human therapeutic benefit. Use for broad repurposing research when the user supplies a human gene, human disease, and/or human phenotype and wants exact compounds discovered through independent mechanistic, phenotype, clinical, novelty, and natural-compound perspectives with source-linked claims, proportionate independent fact checking, and deterministic ranking."
---

# Repurposing Research Program

Build a complete, ranked list of exact compounds that could plausibly improve a human disease or phenotype. Preserve a living evidence graph so later evidence can add, qualify, correct, supersede, or contradict earlier relations.

## Input

Require at least one of a human gene, human disease, or human phenotype. Ask one concise question only when all three are absent. Accept ordinary language or structured input. Treat organism, cell, organoid, behavioural, and other experimental-model details as optional context; never require a model gene, allele, or assay platform.

## Read before running

Read these schema-v5 contracts before creating a run:

- `references/workflow.md`
- `references/evidence.md`
- `references/runtime.md`
- `references/ranking.md`

Treat `scripts/program_contract.py` as the authoritative definition of schemas, query families, ranking components, caps, and invariants.

## Execute

1. Create `repurposing_program_runs/<human-case-slug>_<YYYYMMDD_HHMMSS>/` and initialize it through `scripts/orchestrate_program.py init`. Never initialize over an existing run.
2. Ask the controller for work. For every `start_agent` action, start exactly one isolated subagent with `fork_turns="none"` and pass only the controller's three-line `spawn_prompt`. Do not simulate worker, auditor, integrator, or council roles in the main agent.
3. Have the subagent process every packet chunk, write only the expected staged result, and run `validate-result` until it passes. Controlled values are exact enums: prose nuance belongs in rationales and scope fields. Submit with `complete`. Do not edit canonical ledgers or controller state by hand.
4. Run broad human evidence units before independent compound perspectives. Never expose one perspective's candidate observations to another. Permit later units to add contrary or superseding claims and graph edges.
5. Preserve every exact identity-resolved observation through merge, normalizing salts and formulations by source-backed active moiety. Assign candidate class, compound origin, one explicit target endpoint, and a separate repurposing-readiness assessment. Require every candidate path to start at its chemical node and end at `CASE_HUMAN_THERAPEUTIC_OUTCOME`; model-only restoration is not a terminal outcome.
6. Independently retrieve and verify every decisive candidate-path claim in the batched audit; packet evidence is a lead, not an independent check. Reassess every score and cap after the audit. Keep explicit supported, qualified, contradicted, unresolved, and conflicted states.
7. Let deterministic code derive caps and partition candidates into primary repurposing, target-disease benchmarks, baseline care, and preclinical hypotheses. Council reviews therapeutic and repurposing leaders plus true material conflicts, corrects category or endpoint-type errors, triggers deterministic reranking, and records a disposition surfaced in the outputs. Keep experimental-model suitability separate from the human score.
8. Continue serially through checkpoints and retries until the controller returns `finalize`. Then run `scripts/build_final_outputs.py <run_folder>`. Do not hand-write or prune the final outputs.

## Non-negotiable boundaries

- Keep raw source payloads under `raw_sources/`; pass only compact, query-bound receipts and targeted metadata into packets.
- Link every scientific claim to verified source IDs. Never treat snippets, generated summaries, or unverified metadata as evidence.
- Resolve chemical identity by authoritative registry records and structure identity, not name resemblance.
- Reuse one source row per canonical work and aggregate discovery provenance. Article sections are claims or verification scopes, never new canonical source identifiers.
- Exclude mixtures, extracts, compound classes, diets, genetic manipulations, targets, pathways, assay conditions, and controls from the candidate list.
- Preserve standard care and target-disease assets as baseline or benchmark records; never present them as primary repurposing leads. Treat natural origin separately from disease-relationship class.
- Log every screened exact compound that is not emitted as a candidate, with a source-linked exclusion reason.
- Apply structural and provenance validation to every staged record. Use independent retrieval for decisive scientific claims; do not create a worker/auditor pair for each minor topic.
- Preserve uncertainty and counterevidence. Novelty, indirectness, or absence of prior testing alone is not a reason to discard an exact compound.
- Use bounded serial execution. After a checkpoint, state that progress is persisted and that `continue` resumes deterministically.
- Describe completion as: `no known decision-changing search branch remains within the documented scope`. Never claim universal exhaustiveness or clinical efficacy.
