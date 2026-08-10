# Content packet contract

`next` writes one packet. While that task remains ready, repeated `next` calls and `submit` read the exact persisted packet; an issued packet is never regenerated in place. Item packets include a stable `item_id`; global packets use `null`. Every packet contains the case, one task, hashes of accepted canonical barriers, only the task context, exact result fields, and evidence rules.

The worker returns one JSON object:

```json
{
  "stage": "pathology_node_research",
  "item_id": "NODE-...",
  "packet_id": "PACKET-...",
  "status": "complete",
  "records": {},
  "gaps": [],
  "notes": []
}
```

## Agent task collections

1. `pathology_source_adjudication`: `sentence_decisions`; one compact packet partitions every flagged free-text sentence exactly once as `retain_pathology`, `exclude_treatment`, `exclude_mixed`, or `exclude_ambiguous`. It performs no search or rewriting and is skipped deterministically when the batch is empty.
2. `pathology_landscape_scan`: `documents`, `landscape_proposals`, `asta_call_receipts`; one global, shallow Asta scan checks the compact initial index through one stable broad search and one to three short searches for broad or under-covered concepts, completing each search cycle before starting the next. Zero scientific results are valid after a receipt-verified scan; all attempted searches failing their minimal retries returns empty scientific collections and an explicit gap.
3. `pathology_coverage_expansion`: `documents`, `coverage_proposals`; one global Undermind deep search challenges the complete post-Asta index, inspects its full ranked result, and reads up to twenty decision-relevant PDFs in one native parallel batch before returning supported missing or materially refined atomic proposals. Service failure returns empty scientific collections and an explicit gap.
4. `pathology_curation`: `concepts`; one packet partitions every supplied non-anchor Monarch, DisMech, and projected Asta or Undermind node exactly once and is the only packet that decides splits, merges, final concept identity, and research eligibility.
5. `pathology_node_research`: `documents`, `profiles`, `assertions`.
6. `candidate_seed_research`: `documents`, `candidates`, `exclusions`.
7. `candidate_identity`: `documents`, `identity_groups`; one global packet partitions every UniChem-flagged or unresolved seed exactly once. It is skipped deterministically when the queue is empty.
8. `candidate_evidence_review`: `documents`, `reviews`; one packet contains the identity-resolved candidates assigned to one pathology concept and requires exactly one evidence dossier per supplied candidate.
9. `candidate_audit`: `assessments`, `excluded_candidates`; one global closed-corpus packet requires an exact partition of every reviewed candidate and cannot return new documents.

Python creates `pathology_source_screening`, `pathology_sources`, the frozen `evidence_graph`,
UniChem-enriched `candidate_seed_generation`, and aggregated `candidate_review` results.

Research `document_id` values use a canonical PMID, PMCID, DOI, namespaced Semantic Scholar paper ID (`S2:` followed by 40 hexadecimal characters), supported authoritative database accession, or HTTPS URL; the generated packet contract lists the exact accepted formats. Invented `DOC-AUTHOR-YEAR` aliases are rejected. Every returned document contains one or more `evidence_passages` objects with exactly non-empty string values for `text` and `locator`; a citation record without inspectable retained content is invalid.
For PMID, PMCID, and DOI documents, Python uses cached authoritative metadata to verify that the supplied title belongs to the identifier and projects the canonical publication ID, known identifier aliases, title, year, journal, and authors downstream. One worker result cannot return the same publication through two identifier aliases. Accepted worker results remain unchanged. Python keeps one document per submitted ID, unions list evidence without duplicates, and stops on conflicting bibliographic identity metadata instead of choosing the last value.

Workers may search and read freely, but `records.documents` contains only underlying documents that directly support a submitted claim, counterclaim, identity decision, or limitation. Each returned document ID is cited through `source_ids`, `pathology_source_ids`, or `mechanism_source_ids` somewhere in that result, including nested observation citations. Upstream citations need not be returned again.

Python preserves accepted results unchanged and applies this rule as a soft propagation boundary: unused returned documents are normally not rejected, but do not enter aggregated stages or downstream packets. Both pathology-discovery stages are stricter and reject papers not cited by a proposal. The graph, seeds, identity review, and candidate review each contribute only their own cited documents. `_all_documents()` assembles the deduplicated retained cross-stage union for the auditor and final outputs, including cited evidence for excluded candidates and negative findings but excluding papers attached only to rejected Asta or Undermind proposals.

## Pathology records

- Structured treatment sections and fields are excluded before sentence screening. The screening
  batch contains deduplicated sentence IDs, exact sentences, bounded signal classes, and source
  paths. The adjudicator returns decisions only; it cannot create nodes, return documents, or
  introduce replacement text. Python restores only exact sentences classified
  `retain_pathology`; mixed and ambiguous sentences fail closed and become explicit gaps.

- The landscape packet contains the compact initial source-node index, source edges, compact
  disease context, and a coverage checklist. Following
  `https://allenai.org/asta/resources/mcp`, the worker runs one stable broad relevance search and
  one to three short searches for broad or under-covered indexed concepts. It screens compact
  metadata, evaluates at most thirty unique originals, and applies the single coverage-saturation
  rule defined in [SKILL.md](../SKILL.md#hard-boundaries). It finishes each selected original and its citations within the current search cycle,
  discards unprocessed results when that cycle closes, and keeps no cross-search pending-paper queue.
  Completed receipts prevent repeated operations. A global working index classifies findings as
  duplicate, refinement, or new and is updated immediately. Each retained original
  receives a paper-specific snippet search; up to three citing papers are read from
  `structuredContent.result[].citingPaper` and each distinct paper receives one snippet search.
  Exactly one Asta call is made and inspected per orchestration step; calls are not batched or
  buffered, and a pending call is not terminated before 180 seconds. A retryable error
  or 180-second no-response receives exactly one minimal same-operation retry and another full
  wait. A terminal citation failure is endpoint-specific and does not prevent snippet evaluation
  of the original relevance paper; one search failure records a gap and only all attempted relevance
  searches failing their retries makes Asta unavailable. `asta_call_receipts` contains one non-secret row per actual call with only logical
  operation, tool, paper ID when applicable, attempt/profile, outcome category, elapsed time,
  result count, and bounded error type. Query text, raw payloads, error messages, headers, and
  credentials remain transient. Each proposal
  contains exactly `label`,
  `provisional_type`, `claim`, `index_comparison`, and `source_ids`; it cites a returned canonical
  paper with inspectable content. Python rejects duplicates, unknown sources, treatment framing,
  worker-supplied IDs, and unused papers, then assigns `ASTA-NODE-<hash>` from normalized type,
  label, and claim. Zero proposals are valid.

- The coverage-expansion packet contains the complete post-Asta node index, source edges, compact
  disease context, coverage checklist, and upstream gaps. The worker runs one treatment-blind deep
  search, inspects its full ranked result, and reads up to twenty decision-relevant papers in one
  native parallel batch. `coverage_proposals` use the same five fields as Asta proposals and cite
  only canonical underlying papers with inspectable full-text passages. Python rejects treatment
  framing, duplicates, unknown sources, and unused papers, then assigns stable
  `UNDERMIND-NODE-<hash>` identities. The final curator alone decides splits, merges, identity,
  eligibility, and desired state. Service failure produces empty scientific collections and an
  explicit gap; raw search and account data are never returned.

- Monarch, DisMech, and projected Asta or Undermind nodes are disease-specific claims with a provisional type and retained sources. Each literature proposal contains one pathological state or process at one causal level; one paper may support several separate proposals. The curator assigns the authoritative run-local concept type from the supplied claim, payload, and edges; literature-service proposals have no privileged status.
- Non-node DisMech material is retained in `disease_context`. The curator receives only compact disease-defining context and performs packet-only classification without searching or deep research. Receipts and full provenance remain controller-owned and do not create independent research tasks.
- A curated concept chooses one member source-node ID as its run-local `concept_id`, retains member IDs and aliases, and uses one of `driver`, `mechanism`, `phenotype`, or `context`. A mechanism concept contains one pathological state or process at one causal level. Nodes merge only when the same pathological state, biological context, profile, and desired biological state fit every member; source-supported claims at different causal levels remain separate even when causally linked. Shared identifiers or biological relationships are not equivalence and are represented by source edges or researched assertions. Same-label gene-level disease claims may merge across sources, while mutation-, variant-, repeat-, model-, and mechanism-specific claims remain separate. This identity rule does not determine disposition. True duplicates remain as members of the retained concept, and Python rejects duplicate retained type-label pairs. Python requires an exact partition and does not perform fuzzy matching.
- Each curation concept contains `atomicity`. It is `null` for `context_only` and `exclude`. For `research`, it contains exactly `focal_abnormal_state`, `causal_level`, `biological_direction`, `compartment`, `primary_desired_biological_state`, and `atomicity_rationale`, all as non-empty single strings. `focal_abnormal_state` names one source-supported changed biological variable or process. `causal_level` names the level actually supported by the claim. `biological_direction` states the observed change rather than merely “abnormal” or “dysregulated.” `compartment` gives the supported subcellular, cell-type, tissue, anatomical, or systemic context. `primary_desired_biological_state` reverses or specifically compensates the same focal state. `atomicity_rationale` explains why it is one intervention variable and distinguishes linked causes, consequences, assays, biomarkers, and outcomes. Unsupported specificity makes the concept `context_only`; do not invent state metadata to obtain `research` disposition. Keep separately supplied claims distinct when they can occur independently, require different normalisation, occupy different causal levels or compartments, or have materially different evidence. If a lone source node bundles such states without separately supported proposals, retain it as `context_only` and report the missing atomic subclaims as a gap; do not fabricate them. Do not split inseparable causal steps, assay/model/population differences, biomarkers, or alternate wording that leave the focal state and desired state unchanged. Do not make a bundled claim appear atomic by replacing it with an umbrella label: the focal state must identify one supplied biological control variable or process. Independently variable cell types, compartments, causal levels, cargo classes, or molecular species remain separate when their biological normalisation differs. The curator does not research or invent missing causal decompositions; without separately supported supplied nodes or proposals, the broad claim is `context_only` plus a decomposition gap. The concepts partition and `member_node_ids` are the sole split/merge record; `proposed_splits`, `merge_targets`, and parallel identity metadata are invalid.
- Concept type and disposition are separate judgments. Concept distinctness does not create a research job, and researchability may not be deferred to deep research. A concept is `research` only when the supplied packet already establishes a specific abnormal biological state or process, a specific well-supported causal lesion defining its own pathology route and non-generic compensatory direction, or a phenotype that passes every phenotype-research criterion below. A bare gene or gene-disease association, risk factor, model genotype, broad pathway, terminal outcome, or mutation label without supplied functional pathology is normally `context_only`. Generic gene and lesion-specific claims do not both create research routes unless each supplies a distinct intervention variable. Measurement-only biomarkers are `context_only`, while a biomarker-labelled causal process is classified by that mechanistic role, normally as a mechanism. A phenotype is `research` only when the supplied packet establishes all five conditions: it is disease-attributed rather than incidental, treatment-induced, age-related, or comorbid (disease-attributed does not mean disease-unique); it has a direct, source-supported causal or mechanistic link to a retained driver or mechanism rather than co-occurrence or graph proximity; it expresses one physiological state, direction, and supported compartment; it is an independent objective rather than a subordinate sign, measurement, severity descriptor, or manifestation substantially subsumed by normalising an upstream retained concept; and it is material enough to preserve a major function or independently important disease burden and justify a separate candidate-discovery route. If any condition is absent or uncertain, the phenotype is `context_only` and links to relevant research concepts. Curation does not assess drug availability, therapeutic actionability, or rescue plausibility for this gate.
- Only `research` concepts receive deep work. Each `context_only` concept names at least one research concept in `related_concept_ids`; Python retains it in the frozen graph and creates explicit context edges without a separate research packet. `exclude` concepts remain visible in the curation artifact but do not enter the graph.
- Each researched concept returns one detailed profile covering normal and pathological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps. `desired_biological_state` must preserve the curator-fixed `atomicity.primary_desired_biological_state`; research cannot split, merge, or redefine the concept. An irreversible driver therefore uses the curator-fixed specific compensatory state rather than generic improvement. `secondary_desired_states` may contain distinct atomic biological states and may be empty. `phenotype_objective` states the separate disease-phenotype change sought. State fields do not contain phenotype outcomes, assays, stages, populations, treatments, candidates, or generic clinical improvement. Apparent nested mechanisms are checked semantically against the complete `allowed_assertion_nodes` index and its atomicity metadata. Indexed mechanisms are not duplicated; unindexed siblings are researched distinctly in the profile, with independently atomic ones also flagged in `gaps`. None changes focal identity or desired state or creates additional graph nodes.
- `established_pathology_observations` is a list of `{observation, source_ids}` objects and may be empty. It retains only sourced pathology observations of movement toward the desired state; workers do not invent assays, thresholds, or biomarkers.
- `records.documents` must retain at least one researched source; supplied source metadata alone is not deep node research.
- Pathology discovery remains pathology-led and does not search for candidates, therapies, repurposing, or disease-drug associations. When an intervention appears in a source, profiles and assertions retain only directly supported causal biology and pathology; therapeutic interpretation, efficacy, candidate status, and trial history do not construct the pathology.
- Worker assertions are optional and contain only `subject_id`, `relation`, `object_id`, and a non-empty `evidence_context` list. Both endpoints must copy `node_id` values exactly from the packet's complete `allowed_assertion_nodes` index; aliases, cross-references, and newly researched entities are not endpoints. A mechanism without two allowed endpoints remains in the profile rather than creating a graph node. Each context records `source_ids`, `evidence_type` (`human`, `animal`, `cell`, `biochemical`, or `inferred`), `model`, `stage`, `polarity` (`supports` or `contradicts`), and one assertion-specific `summary`. Assertion-level source and summary fields are not duplicated. Assertions cite retained documents.
- Python merges assertions by `subject_id + relation + object_id`, unions sources only within otherwise identical evidence contexts, and assigns the stable assertion ID from that biological triple.
- Pathology nodes, edges, profiles, and assertions cannot contain treatment or candidate fields.

## Candidate records

Each seed packet contains one frozen focal concept, a compact index of every retained non-anchor graph concept, and a read-only command that returns one bounded node context from the same snapshot. Focal and retrieved contexts keep source edges separate from researched assertions and omit pathology document metadata. Before searching, the seed worker reviews every immediate focal neighbour, source edge, and researched assertion; it then includes only materially useful graph context. A focal-only hypothesis remains valid after this bounded review.

Candidate generation keeps established drug action linked to the focal profile's primary `desired_biological_state` as its main anchor. Each candidate carries:

- a preferred candidate name and every authoritative identifier found;
- frozen graph node IDs;
- graph assertion IDs actually used and one concise graph rationale;
- `pathology_source_ids`;
- `mechanism_source_ids` supporting the drug's action.

`graph_node_ids` includes the focal concept and may include any other indexed concept materially used by the hypothesis. The seed worker may retrieve such concepts through the supplied command but cannot alter the frozen graph.

`assertion_ids` lists only assertions materially used by the hypothesis. It may be empty when the focal profile is sufficient, in which case `graph_rationale` explains that choice. Every non-anchor endpoint of a selected assertion appears in `graph_node_ids`. `graph_rationale` explains the chosen graph support without repeating `mechanism_hypothesis`.

For every `graph_node_id`, `pathology_source_ids` includes at least one source attached to that node, its profile, or an incident graph relation. The cited evidence must support the stated use of the context; graph proximity or topical similarity is insufficient.

Seed workers derive candidates from the supplied biological change and retrieve evidence connecting its target or process to established drug action. They do not use disease-specific drug literature or queries combining the disease with drug, treatment, therapy, trial, or repurposing terms. Such literature is reserved for candidate review and reported only when it changes the decision.

`secondary_desired_states` and `phenotype_objective` remain context and do not create additional candidate-discovery routes by themselves. A supplied linked graph node may support a symptomatic or compensatory candidate only when its relationship to the focal concept and candidate hypothesis is mechanistically justified.

Seed workers do not classify identity status. They retain every authoritative candidate identifier found. Python submits every supported identifier to UniChem, automatically merges only exact UCI matches, and queues every connectivity-only match, conflicting or partial mapping, unsupported identifier, and no result. A no-result record is unresolved, never evidence of uniqueness. The identity reviewer receives the entire queue plus a compact, controller-generated `canonical_candidate_options` list, so legal canonical IDs are explicit and possible aliases are not selected by name heuristics. Same-name matching never merges candidates automatically.

UniChem identifiers use their native database values under `chembl`, `drugbank`, `gtopdb`, `chebi`, `unii`, `pubchem_cid`, `drugcentral`, `inchi`, or `inchikey`. Other identifier types remain visible but are not rewritten or submitted speculatively.

The identity reviewer may attach a resolved queued group only to a candidate ID copied exactly from `canonical_candidate_options`, or partition queued seeds into new resolved, unresolved, or conflicting groups with a null `canonical_candidate_id`. Each option is either an existing resolved candidate or a queued exact-UCI block and names any queued seed IDs that the group must contain. UCI values appearing elsewhere in a partial or conflicting queue record are identity evidence, not canonical options. Seeds sharing one exact UCI are an indivisible identity block even when that block enters review because of a connectivity relationship. Each group cites newly retained authoritative identity evidence. Python validates complete, non-overlapping queue coverage and constructs the final candidate records without rewriting pathology or mechanism evidence.

Review packets retain the assigned candidates and all of their linked frozen pathology concepts and profiles, including complete cross-concept provenance, and include document metadata for the candidates' identity and drug-mechanism sources. For each candidate they also contain `selected_graph_evidence`: researched assertions matching `assertion_ids` exactly and source edges bounded to selected nodes and the candidate's cited pathology sources. Pathology citations remain attached to the frozen graph and candidate records without duplicating the full graph or pathology source library into every review packet. Workers first verify drug facts with primary or authoritative sources and map them to the supplied pathology, then check exact-disease prior art. A review is an evidence dossier, not a decision: it contains a hypothesis, cited supporting findings, an explicit mechanistic bridge, assumptions, cited counterevidence, prior-art classification, supported aliases, and limitations. It does not score, rank, or exclude the candidate.

Final candidate provenance exports the selected assertion IDs directly; it never infers assertion use from endpoint incidence. The summary reports compact graph coverage: candidate counts per non-anchor node, uncovered nodes, candidates using multiple nodes, and candidates using context-only nodes.

The audit packet contains the reviewed candidates, dossiers, complete frozen evidence graph, candidate-identity result, and a canonical index of the deduplicated retained corpus with inspectable passages or controller-retained source content. The auditor must use that closed corpus, independently weigh the argument and evidence, and partition every candidate exactly once. It must not search, add evidence, restate a dossier as analysis, request re-review, or defer a source judgment for later verification. Established exact-disease use and qualifying exact-disease experiments are exclusionary. A qualifying experiment may be human or preclinical and favorable or unfavorable, but the retained corpus must establish relevant exposure, a credible counterfactual, and a disease-relevant outcome. Mere registration, uncontrolled anecdote, unsuitable model, inadequate control, and otherwise uninterpretable experimentation remain scored with a concise cited reservation. Long causal distance, weak evidence, unresolved identity, uncertain exposure, and material assumptions remain scored reservations rather than exclusions.

Each assessment contains `source_integrity`, exactly four `component_scores`, a cited `net_assessment`, and only the cited `aliases` and `why_not` findings the auditor accepts for final output. `source_integrity` contains only `checks`; each check identifies one `source_id`, its exact scope (a bare component name, `net_assessment`, `aliases[n]`, or `why_not[n]`), a verdict of `supports`, `partly_supports`, `does_not_support`, or `contradicts`, and a concrete finding based on retained content. `component_scores.<name>` remains an accepted compatibility alias, but packets request the bare name. Python requires every cited source-use pair exactly once and rejects two identifier aliases for the same publication within one scope. A flat status, generic declaration, or instruction to re-verify is invalid. The four components are `drug_action_confidence`, `disease_mechanism_relevance`, `mechanistic_bridge_plausibility`, and `translational_feasibility`. The packet carries distinct category-specific anchors: 5 is the weakest non-excluded, testable case; 10 is limited; 15 is strong with one material uncertainty; and 20 is direct, convergent support. Counterevidence never earns positive scoring credit: it lowers each component whose premise it directly challenges and otherwise remains only an unscored `why_not` finding. The cited `net_assessment` concisely states why the candidate remains worth ranking without repeating component reasons or `why_not` findings. Python rejects exact repetition of either in `net_assessment`. Each score has a reason and retained `source_ids`; Python sums the values without weighting to a prioritisation score out of 80 and sorts descending by total, then candidate ID, with tied totals sharing a dense rank. The score is not a probability of efficacy.

An exclusion contains `candidate_id`, a cited `finding`, `source_integrity` checks with scope `exclusion`, and one `reason_code`: `exact_disease_use`, `qualifying_exact_disease_experiment`, `unsupported_action`, `opposite_action`, `impossible_translational_feasibility`, or `invalid_candidate`. The packet carries the exact definition of every code. In particular, missing downstream evidence is not an unsupported drug action, uncertainty is not demonstrated translational impossibility, and unresolved identity is not an invalid candidate. A component-level failure corresponding to zero belongs in this exclusion record rather than in a scored assessment. Final cards use only audit-owned assessment fields; review fields never enter them directly.

Only `status=complete` results are accepted. On validation failure, stop and report the exact
error; do not retry automatically or repair the worker's research JSON. Conflicting replacement
of an accepted result is rejected.
