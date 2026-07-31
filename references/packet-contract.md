# Content packet contract

`next` writes one packet. Item packets include a stable `item_id`; global packets use `null`. Every packet contains the case, one task, hashes of accepted canonical barriers, only the task context, exact result fields, and evidence rules.

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
2. `pathology_curation`: `concepts`; one packet partitions every supplied non-anchor source node exactly once.
3. `pathology_node_research`: `documents`, `profiles`, `assertions`.
4. `candidate_seed_research`: `documents`, `candidates`, `exclusions`.
5. `candidate_identity`: `documents`, `identity_groups`; one global packet partitions every UniChem-flagged or unresolved seed exactly once. It is skipped deterministically when the queue is empty.
6. `candidate_evidence_review`: `documents`, `reviews`; one packet contains the identity-resolved candidates assigned to one pathology concept and requires exactly one evidence dossier per supplied candidate.
7. `candidate_audit`: `assessments`, `excluded_candidates`; one global closed-corpus packet requires an exact partition of every reviewed candidate and cannot return new documents.

Python creates `pathology_source_screening`, `pathology_sources`, the frozen `evidence_graph`,
UniChem-enriched `candidate_seed_generation`, and aggregated `candidate_review` results.

Research `document_id` values use a canonical PMID, PMCID, DOI, authoritative database accession, or HTTPS URL. Invented `DOC-AUTHOR-YEAR` aliases are rejected. Every returned document contains one or more `evidence_passages` objects with exactly non-empty `text` and `locator` fields; a citation record without inspectable retained content is invalid.
For PMID, PMCID, and DOI documents, Python uses cached authoritative metadata to verify that the supplied title belongs to the identifier and projects the canonical publication ID, known identifier aliases, title, year, journal, and authors downstream. One worker result cannot return the same publication through two identifier aliases. Accepted worker results remain unchanged. Python keeps one document per submitted ID, unions list evidence without duplicates, and stops on conflicting bibliographic identity metadata instead of choosing the last value.

Workers may search and read freely, but `records.documents` contains only underlying documents that directly support a submitted claim, counterclaim, identity decision, or limitation. Each returned document ID is cited through `source_ids`, `pathology_source_ids`, or `mechanism_source_ids` somewhere in that result, including nested observation citations. Upstream citations need not be returned again.

Python preserves accepted results unchanged and applies this rule as a soft propagation boundary: unused returned documents are not rejected, but do not enter aggregated stages or downstream packets. The graph, seeds, identity review, and candidate review each contribute only their own cited documents. `_all_documents()` assembles the deduplicated cross-stage union for the auditor and final outputs, including cited evidence for excluded candidates and negative findings.

## Pathology records

- Structured treatment sections and fields are excluded before sentence screening. The screening
  batch contains deduplicated sentence IDs, exact sentences, bounded signal classes, and source
  paths. The adjudicator returns decisions only; it cannot create nodes, return documents, or
  introduce replacement text. Python restores only exact sentences classified
  `retain_pathology`; mixed and ambiguous sentences fail closed and become explicit gaps.

- Monarch and DisMech nodes are disease-specific claims with a provisional source-adapter type and retained sources. The curator assigns the authoritative run-local concept type from the supplied claim, payload, and edges.
- Non-node DisMech material is retained in `disease_context`. The curator receives only compact disease-defining context and performs packet-only classification without searching or deep research. Receipts and full provenance remain controller-owned and do not create independent research tasks.
- A curated concept chooses one member source-node ID as its run-local `concept_id`, retains member IDs and aliases, and uses one of `driver`, `mechanism`, `phenotype`, or `context`. Nodes merge only when they express the same claim at the same causal level; shared identifiers or biological relationships are not equivalence. Same-label gene-level disease claims may merge across sources, while mutation-, variant-, repeat-, model-, and mechanism-specific claims remain separate. True duplicates remain as members of the retained concept, and Python rejects duplicate retained type-label pairs. Python requires an exact partition and does not perform fuzzy matching.
- Only `research` concepts receive deep work. Each `context_only` concept names at least one research concept in `related_concept_ids`; Python retains it in the frozen graph and creates explicit context edges without a separate research packet. `exclude` concepts remain visible in the curation artifact but do not enter the graph.
- Each researched concept returns one detailed profile covering normal and pathological state, desired biological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps. `desired_biological_state` is one concise state that would reverse the pathology or compensate for an irreversible driver; it is not a treatment, assay, control, candidate, or generic clinical improvement.
- `established_pathology_observations` is a list of `{observation, source_ids}` objects and may be empty. It retains only sourced pathology observations of movement toward the desired state; workers do not invent assays, thresholds, or biomarkers.
- `records.documents` must retain at least one researched source; supplied source metadata alone is not deep node research.
- Pathology discovery remains pathology-led and does not search for candidates, therapies, repurposing, or disease-drug associations. When an intervention appears in a source, profiles and assertions retain only directly supported causal biology and pathology; therapeutic interpretation, efficacy, candidate status, and trial history do not construct the pathology.
- Assertions link existing source-derived node IDs and cite retained documents.
- Repeated assertion IDs must retain the same subject-relation-object identity; Python unions their sources and unique evidence summaries.
- Pathology nodes, edges, profiles, and assertions cannot contain treatment or candidate fields.

## Candidate records

Each seed packet contains one frozen focal concept, a compact index of every retained non-anchor graph concept, and a read-only command that returns one bounded node context from the same snapshot. Focal and retrieved contexts keep source edges separate from researched assertions and omit pathology document metadata.

Each candidate links established drug action to the focal profile's desired biological state and carries:

- a preferred candidate name and every authoritative identifier found;
- frozen graph node IDs;
- `pathology_source_ids`;
- `mechanism_source_ids` supporting the drug's action.

`graph_node_ids` includes the focal concept and may include any other indexed concept materially used by the hypothesis. The seed worker may retrieve such concepts through the supplied command but cannot alter the frozen graph.

For every `graph_node_id`, `pathology_source_ids` includes at least one source attached to that node, its profile, or an incident graph relation. The cited evidence must support the stated use of the context; graph proximity or topical similarity is insufficient.

Seed workers derive candidates from the supplied biological change and retrieve evidence connecting its target or process to established drug action. They do not use disease-specific drug literature or queries combining the disease with drug, treatment, therapy, trial, or repurposing terms. Such literature is reserved for candidate review and reported only when it changes the decision.

Seed workers do not classify identity status. They retain every authoritative candidate identifier found. Python submits every supported identifier to UniChem, automatically merges only exact UCI matches, and queues every connectivity-only match, conflicting or partial mapping, unsupported identifier, and no result. A no-result record is unresolved, never evidence of uniqueness. The identity reviewer receives the entire queue plus a compact, controller-generated `canonical_candidate_options` list, so legal canonical IDs are explicit and possible aliases are not selected by name heuristics. Same-name matching never merges candidates automatically.

UniChem identifiers use their native database values under `chembl`, `drugbank`, `gtopdb`, `chebi`, `unii`, `pubchem_cid`, `drugcentral`, `inchi`, or `inchikey`. Other identifier types remain visible but are not rewritten or submitted speculatively.

The identity reviewer may attach a resolved queued group only to a candidate ID copied exactly from `canonical_candidate_options`, or partition queued seeds into new resolved, unresolved, or conflicting groups with a null `canonical_candidate_id`. Each option is either an existing resolved candidate or a queued exact-UCI block and names any queued seed IDs that the group must contain. UCI values appearing elsewhere in a partial or conflicting queue record are identity evidence, not canonical options. Seeds sharing one exact UCI are an indivisible identity block even when that block enters review because of a connectivity relationship. Each group cites newly retained authoritative identity evidence. Python validates complete, non-overlapping queue coverage and constructs the final candidate records without rewriting pathology or mechanism evidence.

Review packets retain the assigned candidates and all of their linked frozen pathology concepts and profiles, including complete cross-concept provenance, and include document metadata for the candidates' identity and drug-mechanism sources. Pathology citations remain attached to the frozen graph and candidate records without duplicating other graph sections or the full pathology source library into every review packet. Workers first verify drug facts with primary or authoritative sources and map them to the supplied pathology, then check exact-disease prior art. A review is an evidence dossier, not a decision: it contains a hypothesis, cited supporting findings, an explicit mechanistic bridge, assumptions, cited counterevidence, prior-art classification, supported aliases, and limitations. It does not score, rank, or exclude the candidate.

The audit packet contains the reviewed candidates, dossiers, complete frozen evidence graph, candidate-identity result, and a canonical index of the deduplicated retained corpus with inspectable passages or controller-retained source content. The auditor must use that closed corpus, independently weigh the argument and evidence, and partition every candidate exactly once. It must not search, add evidence, restate a dossier as analysis, request re-review, or defer a source judgment for later verification. Established exact-disease use and exact-disease human intervention are exclusionary; preclinical disease work is not. Long causal distance, weak evidence, unresolved identity, uncertain exposure, and material assumptions remain scored reservations rather than exclusions.

Each assessment contains `source_integrity`, exactly four `component_scores`, a cited `net_assessment`, and only the cited `aliases` and `why_not` findings the auditor accepts for final output. `source_integrity` contains only `checks`; each check identifies one `source_id`, its exact scope (a component name, `net_assessment`, `aliases[n]`, or `why_not[n]`), a verdict of `supports`, `partly_supports`, `does_not_support`, or `contradicts`, and a concrete finding based on retained content. Python requires every cited source-use pair exactly once and rejects two identifier aliases for the same publication within one scope. A flat status, generic declaration, or instruction to re-verify is invalid. The four components are `drug_action_confidence`, `disease_mechanism_relevance`, `mechanistic_bridge_plausibility`, and `translational_feasibility`. The packet carries distinct category-specific anchors: 5 is the weakest non-excluded, testable case; 10 is limited; 15 is strong with one material uncertainty; and 20 is direct, convergent support. Counterevidence never earns positive scoring credit: it lowers each component whose premise it directly challenges and otherwise remains an unscored `why_not` finding and part of the net assessment. Each score has a reason and retained `source_ids`; Python sums the values without weighting to a prioritisation score out of 80 and sorts descending by total, then candidate ID, with tied totals sharing a dense rank. The score is not a probability of efficacy.

An exclusion contains `candidate_id`, a cited `finding`, `source_integrity` checks with scope `exclusion`, and one `reason_code`: `exact_disease_use`, `human_intervention`, `unsupported_action`, `opposite_action`, `impossible_translational_feasibility`, or `invalid_candidate`. The packet carries the exact definition of every code. In particular, missing downstream evidence is not an unsupported drug action, uncertainty is not demonstrated translational impossibility, and unresolved identity is not an invalid candidate. A component-level failure corresponding to zero belongs in this exclusion record rather than in a scored assessment. Final cards use only audit-owned assessment fields; review fields never enter them directly.

Only `status=complete` results are accepted. On validation failure, stop and report the exact
error; do not retry automatically or repair the worker's research JSON. Conflicting replacement
of an accepted result is rejected.
