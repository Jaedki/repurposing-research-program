# Program Workflow

## Phase 0: Case And Search Architecture

Run the controller `init` command. It writes the case, immutable seed topology, serial runtime state, empty ledgers, packet and staging directories, and append-only orchestration history. Normalize gene aliases, worm model, allele direction, optional behavioural data, benchmark mode, and prior-screen handling in `case.json`.

## Phase 1: Broad Evidence Collection

Run source-focused workers across human gene/disease biology, worm orthology/model validity, allele consequences, structure/complex/localization, interactions/pathways, cellular/circuit effects, phenotypes, modifiers, omics, pharmacology context, and Tierpsy/Brown assay interpretation. Retrieve broadly before detailed synthesis. Build `source_corpus.jsonl`, `search_log.jsonl`, `claim_ledger.jsonl`, and `evidence_graph.jsonl`.

Create one independent `broad_evidence` unit for each domain: `human_gene_disease`, `worm_model_orthology`, `allele_function`, `structure_complex_localization`, `interactions_pathways`, `cell_circuit`, `phenotype_phenomics`, `modifiers_omics`, `pharmacology_landscape`, and `assay_interpretation`. These are discovery workers, not candidate generators.

When prior exact-model screen data are volunteered and the run is not blinded, add a distinct Phase-3 `prior_screen_context` unit so those hits cannot stand in for de novo discovery. In blinded benchmark mode, do not create that unit or expose hit-revealing sources.

## Phase 2: Recursive Subtopic Closure

Derive `subtopic_registry.jsonl` from the evidence graph. Every material node or edge that could alter rescue logic becomes a subtopic. Spawn one evidence unit per subtopic. Workers may add sources, claims, graph edges, and child subtopics.

After each audit, let the controller integrate only verified additions and register approved children. It schedules each child before the closure auditor. Repeat until every subtopic is `audited_complete` or `evidence_absent_complete`, no child is pending, and the closure auditor's independent search finds no unregistered decision-changing relation.

Use a dedicated `closure_audit` research unit. Its worker must not be any evidence worker or the orchestrator.

## Phase 3: Independent Repurposing Research

Run one compound-research unit for every candidate-relevant subtopic before the mandatory global perspectives in `perspective-research.md`. Each unit uses its audited context packet and conducts a fresh comprehensive literature/database search. It may stage new evidence and subtopics before proposing compounds.

If a compound unit discovers a new decision-changing relation, its audit must return `repair_required`. The controller pauses that unit, returns the relation to Phase 2 for an independent evidence worker and audit, reruns closure, then resumes the affected compound unit against the expanded graph. A phase-3 audit cannot close while registering a material subtopic.

Do not expose another unit's candidate list. Convergence is measured only after independent units finish.

## Phase 4: Compound Universe

Resolve exact identities, merge synonyms and registry identifiers by structure identity key, and write `candidate_records.jsonl`. Preserve every supporting route and dissent. Do not remove a candidate before council unless it is not an exact compound, has unresolved identity, or has no connected audited directional path.

## Phase 5: Council And Fact Audit

Review every candidate using the three-agent, four-turn compact council in `audit-and-council.md`. Required repairs reopen the combined sceptic review and advocate response, or return to the originating research unit. Commit `council_records.jsonl` only after the independent source fact audit verifies the debate's decisive claims.

## Phase 6: Finalization

Continue asking the controller for work within bounded slices. When all scientific jobs close, it runs final validation itself. Validation defects create one serial repair worker and an independent repair auditor with the exact error list plus only targeted ledger slices in their packets. If the same defects survive three repair rounds, the controller blocks rather than looping. Only a clean run returns `finalize`; then run `scripts/build_final_outputs.py <run_folder>`. Agents must not hand-write final outputs.

## Scheduling

Always execute the single action returned by the controller. Concurrency is fixed at one. After every attempt, close the specified agent and acknowledge `release` before requesting the next action. Do not launch waves in parallel, retain completed agents, select a later ready job, compress lanes, or change the predeclared seed topology.

On rate limits, retain and retry the pending job with the same packet hash and obey proactive pacing. Stop only when the controller returns `checkpoint`, normally after one worker/auditor pair or the configured slice duration. On resume, ask the controller for the authoritative active or earliest incomplete job.
