# Audit And Compact Council

## Per-Unit Fact Audit

Audit every evidence and compound unit before dependent work begins. Use a different subagent that independently retrieves decisive sources and runs at least one missing-branch or counterevidence query under its own recorded agent ID. A worker-executed query cannot be relabelled as independent.

Resolve worker artifacts through the packet `run_root` and dependency `artifact_manifest`. Do not call a receipt missing merely because it is not copied beside the worker's staged `result.json`; canonical `raw_sources/` paths are run-root-relative.

Record the audit in `unit_audits.jsonl`; a status inside the worker's record is not an audit. Check source identity and content, gene and allele scope, species and tissue, dose and direction, phenotype relevance, inference calibration, omissions, contradictions, exact chemical identity, and the unit's defining perspective.

`repair_required` is not a pass. Return every material issue and the audit result to the worker, audit the revision, and repeat until `audited_complete` or defensible `evidence_absent_complete`. Never substitute `clean enough`, consensus, elapsed time, or document length for closure.

## Evidence Closure Audit

Use a closure worker to map unresolved branches, followed by a different closure auditor that does not see an orchestrator-authored closure rationale. Search for missing relations, contradictions, uncaptured phenotypes, omitted model evidence, and unsearched source families. Any decision-changing discovery reopens the graph.

## Three-Agent Council

Use three isolated agents and four serial turns for every exact compound:

1. `advocate_case`: combine optimist and novelty-defender duties. Present the strongest screening case, protect coherent indirect or unfamiliar mechanisms, enumerate material claims, and cite source IDs.
2. `skeptic_review`: combine mechanistic, pharmacology, worm-biology, exposure, and assay criticism. Complete every checklist domain: `mechanism_direction`, `worm_target_orthology`, `allele_relevance`, `pharmacology_selectivity`, `exposure_feasibility`, and `phenomics_confounding`.
3. `advocate_response`: answer or accept each challenge, remove indefensible claims, and submit a revised causal path. Resume the original advocate agent.
4. `fact_audit`: use a third agent that does not debate. Independently retrieve and check every decisive claim and citation against primary or authoritative evidence.

The advocate and sceptic must not perform the fact auditor's role. The fact auditor must not accept agreement, rhetorical strength, or another model's source summary as evidence. Intrinsic LLM self-correction is not verification.

## Exchange And Audit Records

Write exactly three substantive exchanges: advocate `case`, sceptic `challenge`, and advocate `response` linked to that challenge. Each exchange has structured `assertions`, one per listed claim ID. The challenge has one item for every critique domain; the response answers every item with `accepted`, `rebutted`, or `qualified`. Do not add summary exchanges. Preserve the exchange text and structured records.

The fact auditor declares `material_claim_ids` exactly equal to the union of all exchange claim IDs and returns one verdict per material claim: `supported`, `qualified`, `unsupported`, or `contradicted`, with checked source IDs. Independent checks record the assigned fact-auditor agent ID. It returns surviving candidate `path_id` values, not an unconnected bag of claim IDs.

An unsupported citation removes or qualifies that claim; it does not automatically exclude the compound. Exclude only when no plausible audited causal path survives or an allowed material exclusion reason is verified.

If a claim, checklist domain, or citation remains unresolved, return `repair_required` with `reopen_stage=skeptic_review` and `required_specialist_checks`. The controller supplies this audit as mandatory feedback and reruns the sceptic, advocate response, and fact audit. Do not add routine specialist agents to every candidate.

## Disposition

Use only `screen` or `exclude`. The controller defaults to `screen` when at least one fact-audited causal path survives and no allowed exclusion reason is verified.

Allowed exclusion reasons are `wrong_direction`, `causal_path_refuted`, `assay_incompatible_confounding`, or `chemical_identity_not_screenable`. Novelty, indirectness, unfamiliarity, or lack of exact-model testing alone are not exclusion reasons.
