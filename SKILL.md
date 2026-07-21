---
name: repurposing-research-program
description: "Run or extend a deterministic, provenance-complete human therapeutic repurposing programme for a supplied gene, disease, or phenotype. Use for schema-v7 case modeling, source-bounded exact-compound discovery, seed screening, identity normalization, deep evidence, audit, separate evidence/novelty/diversity rankings, full-funnel outputs, or read-only inspection of historical schema-v3 through schema-v6 artifacts."
---

# Repurposing Research Program

Produce source-bounded exact-compound hypotheses and a reconciled evidence portfolio for experimental investigation. Treat every result as hypothesis generation.

Schema v7 is an experimental research tool; outputs are not independently scientifically certified and must not be treated as clinical advice or proof of efficacy.

## Choose the schema path

- Use schema v7 for new ordinary work when no schema is specified and whenever v7 is explicitly requested. It is released for normal experimental use under V7-D006. Require at least one human gene, disease, or phenotype and at least one supplied endpoint; preserve typed unknowns and blocking ambiguity, and do not infer a generic endpoint.
- Use schema v6 only when explicitly requested, declared by the case, or continuing a native schema-v6 run. Keep its perspectives, route markers, integer rubric, validator, and final files historical.
- Inspect schema-v3 through schema-v6 artifacts read-only. Never resume, append, finalize, repair, or rewrite them through v7. If an immutable derived container is required, use `scripts/orchestrate_program.py copy-migrate-legacy <source> <new-folder>`; this copies and hashes the original bytes, fills v7-only fields with typed unknowns, and remains ineligible for native v7 execution.

## Run schema v7

Run commands from this skill folder, or replace each relative script path with its absolute path.

1. Read `references/case-discovery-seeds.md`, then initialize with `scripts/orchestrate_program.py init <run-folder> --schema-version 7 --case-file <case.json>`. Resolve a `needs_resolution` case before seed work.
2. Read `references/runtime.md`. For a complete source-frozen run, use `V7ProgramAdapter.execute` in `scripts/v7_production_program.py`; it composes the real discovery, disposition, screen/deep, portfolio, runtime, validation, and output boundaries across all eight stages. For direct runtime recovery or diagnostic operation, drive only controller actions: `next -> start -> progress -> validate-result -> complete`. Run every ready shard up to configured concurrency; give each worker only its emitted three-line prompt. Use `fail` for controlled retries and `recover-active` after interruption.
3. For retrieval, read `references/retrieval-adapters.md` plus the relevant source-family reference. Declare source universes and query plans before traversal. Preserve every eligible mapping, negative/null context, unsupported capability, continuation, and source-specific gap.
4. Normalize and dispose every canonical seed through `scripts/v7_production_disposition.py` using frozen authority assertions; preserve unresolved/conflicting identity and every exact form. Read `references/deep-evidence-identity.md`, then call `V7ScreenDeepAdapter.screen_and_deepen` with the persisted admitted frame and frozen evidence. Require the immutable all-admitted screen and deep-selection frame to persist before original-content deep validation.
5. For triage, audit, council, and portfolio work, read `references/triage-ranking.md` and `references/audit-council-portfolio.md`. Call `V7PortfolioAdapter.audit_and_select` only with the passing persisted deep frame and a fully frozen audit plan. Keep evidence strength, novelty/information value, readiness, uncertainty, and portfolio diversity separate.
6. Read `references/outputs-validation.md`. `V7ProgramAdapter` writes canonical outputs after the committed ledgers validate. To rebuild from an already complete canonical snapshot, use `scripts/build_final_outputs.py <run-folder>`. Always validate through `scripts/validate_program.py <run-folder>` and treat any interim audit, failure, budget deferral, or unresolved reconciliation as diagnostic partial.
Workers write only staged results. The controller validates and atomically commits content-addressed canonical records. Identical replay is a no-op; conflicting content for one identity fails. Keep schedule-specific attempts and retries out of the scientific projection.

## Reference routing

- `references/case-discovery-seeds.md`: v7 case, factorized discovery, structural routes, seeds, disposition, screening, and reconciliation
- `references/runtime.md`: v7 concurrent lifecycle and historical v6 runtime
- `references/retrieval-adapters.md`: generic v7 declarations, receipts, bounded coverage, cache, and replay
- `references/chemical-target-adapters.md`: Open Targets, ChEMBL, BindingDB, PubChem, and UniChem boundaries
- `references/extended-discovery-adapters.md`: clinical-trial, preprint, ChEBI, multimodal planner, unsupported-source, and anti-popularity boundaries
- `references/deep-evidence-identity.md`: original-content grounding, authoritative identity, exact forms, and corrections
- `references/triage-ranking.md`: typed decision features, safety/exposure, triage, and separate pre-audit orders
- `references/audit-council-portfolio.md`: stratified audit, correction authority, council, and diversified portfolio policy
- `references/outputs-validation.md`: generated v7 artifact inventory, reconciliation, and focused validation domains
- `references/workflow.md`, `references/evidence.md`, `references/ranking.md`: historical schema-v6 workflow, evidence, ranking, and outputs

Treat typed Python records and controller artifacts as authoritative. References explain when to use them; they do not redefine field schemas or controlled values.

## Closure and handoff

Never manufacture a candidate quota, use citation density as admission, collapse identity by name, hide source gaps, substitute structural validation for scientific audit, or expose benchmark answers to a live run. The strongest ordinary-run closure claim is `complete within the declared source releases and query plan`; otherwise name the bounded or failed branches. Apply the experimental-use policy above to every completion handoff.

At a checkpoint, report that canonical progress is persisted and `resume` is deterministic. At completion, report the output status, reconciled counts, material gaps, and experimental-use policy.
