# Schema-v6 historical ranking, audit, council, and outputs

Use this file only for native schema-v6 runs. Schema v7 uses `triage-ranking.md`, `audit-council-portfolio.md`, and `outputs-validation.md`.

## Independent audit

Automatic staged validation checks schema, paths, hashes, receipt/query binding, source verification, cross-record references, exact identity, graph connectivity, and provenance for every record. It is not a scientific audit.

After identity merge, one independent auditor verifies every decisive path claim. Each audit record must include the claim ID, verdict, checked source IDs, independently executed search IDs, agent ID, and rationale. Allowed verdicts are `supported`, `qualified`, `unsupported`, `contradicted`, and `unresolved`. Candidate and graph audit statuses must preserve qualification and conflict; disagreement is not silently resolved.

The auditor must retrieve evidence independently of the packet, run distinct verification and counterevidence queries, provide one claim-specific rationale per audit, and reassess every candidate record. Exactly one audit record is allowed per decisive claim. The `absent_human_evidence` and `unresolved_direction` caps are derived bidirectionally: a missing required cap and a stale false-positive cap both fail.

The controller sends the top five primary repurposing candidates, the top five therapeutic scores across sections, and candidates with genuine adverse or contrary material claims to one focused council job. Council records review candidate class, endpoint, mechanism direction, human relevance, safety, exposure, and unresolved contradictions. It may correct class or endpoint type and must return every selected candidate for deterministic reranking. Supportive care receives `baseline_only`; target-disease approved or investigational assets receive `benchmark_only`; true hypotheses receive `retain`, `deprioritize`, or `conflict_unresolved`. Council does not replace independent source audit or trigger a full multi-agent debate.

## Deterministic 100-point rubric

`scripts/program_contract.py` is authoritative. Every component requires an integer score, a rationale, and one or more resolving source IDs.

| Component | Maximum | High-score meaning |
|---|---:|---|
| Human evidence | 25 | Direct human genetic, biomarker, clinical, or disease-relevant evidence |
| Mechanistic fit | 20 | Directionally coherent action through the audited human outcome path |
| Clinical translatability | 15 | Practical human route, formulation, dosing, and development feasibility for the declared endpoint |
| Safety and tolerability | 15 | Therapeutic-window and population fit |
| Exposure feasibility | 10 | Plausible concentration at the relevant human tissue |
| Evidence independence | 5 | Convergence across genuinely independent sources or perspectives |
| Endpoint specificity | 10 | Evidence and mechanism address the declared human endpoint rather than only a generic pathway or surrogate |

The script sums components and then applies the strictest applicable cap: unresolved direction 25, absent human evidence 40, serious safety mismatch 30, or infeasible exposure 20. Every cap assessment also requires a rationale and source IDs.

`candidate_class` is mutually exclusive and source-backed:

- `repurposing_candidate`: an existing human-use compound from another indication; target-disease investigation that arose from that prior use remains repurposing;
- `target_disease_investigational`: an asset developed specifically for the target disease;
- `approved_for_target_disease`: already approved for the requested target disease;
- `supportive_standard_care`: established replacement, symptomatic, or complication management;
- `preclinical_hypothesis`: a tool or development compound without established human use.

`compound_origin` is orthogonal (`synthetic_or_semisynthetic`, `natural_product`, `endogenous_or_nutrient`, or `formulation_component`). The target endpoint contains a controlled type—`disease_modifying_clinical`, `prevention_clinical`, `symptom_or_function`, `complication_management`, or `surrogate_biomarker`—plus a disease-specific label, decisive audited claim IDs, and their source IDs. Score one primary endpoint, retain the generic human-outcome graph terminus, and report an endpoint-specific rank.

Therapeutic evidence and repurposing readiness are separate. The 100-point total estimates endpoint-specific therapeutic support; novelty is not a therapeutic component. A source-backed integer readiness assessment out of 100 is available only to `repurposing_candidate` records (null otherwise) and does not enter the therapeutic total. It uses ten-point increments so the prior assessment resolution and every tie order are preserved. Candidates are partitioned into `primary_repurposing`, `target_disease_benchmark`, `baseline_care`, and `preclinical_hypothesis`; rank is within section, with an additional endpoint rank. Ties resolve by capped total, raw score, repurposing readiness, human evidence, safety, canonical name, then candidate ID.

Record experimental-model suitability separately as a 0-100 assessment when a model was supplied. It never enters or modifies the human therapeutic score.

## Outputs

`build_final_outputs.py` validates the run and writes:

- `ranked_compound_candidates.csv`: every identity-resolved candidate grouped by rank section, with section and endpoint ranks, class, origin, endpoint, readiness, raw and capped score, applied cap/reason, audit status, and council disposition;
- `candidate_justifications.md`: the same four sections, one transparent referenced rationale per candidate using decisive-claim and endpoint sources.

Do not omit low-ranked, capped, baseline, benchmark, or preclinical candidates. Do not present them as one undifferentiated repurposing list.
