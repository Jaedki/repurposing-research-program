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
2. `pathology_landscape_scan`: `documents`, `landscape_proposals`, `asta_call_receipts`; one global, shallow Asta scan checks the compact initial index. Zero scientific results are valid after a receipt-verified scan; relevance-search unavailability after one minimal retry returns empty scientific collections and an explicit gap.
3. `pathology_curation`: `concepts`; one packet partitions every supplied non-anchor Monarch, DisMech, and projected Asta node exactly once.
4. `pathology_node_research`: `documents`, `profiles`, `assertions`.
5. `candidate_seed_research`: `documents`, `candidates`, `exclusions`.
6. `candidate_identity`: `documents`, `identity_groups`; one global packet partitions every UniChem-flagged or unresolved seed exactly once. It is skipped deterministically when the queue is empty.
7. `candidate_evidence_review`: `documents`, `reviews`; one packet contains the identity-resolved candidates assigned to one pathology concept and requires exactly one evidence dossier per supplied candidate.
8. `candidate_audit`: `assessments`, `excluded_candidates`; one global closed-corpus packet requires an exact partition of every reviewed candidate and cannot return new documents.

Python creates `pathology_source_screening`, `pathology_sources`, the frozen `evidence_graph`,
UniChem-enriched `candidate_seed_generation`, and aggregated `candidate_review` results.

Research `document_id` values use a canonical PMID, PMCID, DOI, namespaced Semantic Scholar paper ID (`S2:` followed by 40 hexadecimal characters), supported authoritative database accession, or HTTPS URL; the generated packet contract lists the exact accepted formats. Invented `DOC-AUTHOR-YEAR` aliases are rejected. Every returned document contains one or more `evidence_passages` objects with exactly non-empty string values for `text` and `locator`; a citation record without inspectable retained content is invalid.
For PMID, PMCID, and DOI documents, Python uses cached authoritative metadata to verify that the supplied title belongs to the identifier and projects the canonical publication ID, known identifier aliases, title, year, journal, and authors downstream. One worker result cannot return the same publication through two identifier aliases. Accepted worker results remain unchanged. Python keeps one document per submitted ID, unions list evidence without duplicates, and stops on conflicting bibliographic identity metadata instead of choosing the last value.

Workers may search and read freely, but `records.documents` contains only underlying documents that directly support a submitted claim, counterclaim, identity decision, or limitation. Each returned document ID is cited through `source_ids`, `pathology_source_ids`, or `mechanism_source_ids` somewhere in that result, including nested observation citations. Upstream citations need not be returned again.

Python preserves accepted results unchanged and applies this rule as a soft propagation boundary: unused returned documents are normally not rejected, but do not enter aggregated stages or downstream packets. The landscape scan is stricter and rejects papers not cited by a proposal. The graph, seeds, identity review, and candidate review each contribute only their own cited documents. `_all_documents()` assembles the deduplicated retained cross-stage union for the auditor and final outputs, including cited evidence for excluded candidates and negative findings but excluding papers attached only to rejected landscape proposals.

## Pathology records

- Structured treatment sections and fields are excluded before sentence screening. The screening
  batch contains deduplicated sentence IDs, exact sentences, bounded signal classes, and source
  paths. The adjudicator returns decisions only; it cannot create nodes, return documents, or
  introduce replacement text. Python restores only exact sentences classified
  `retain_pathology`; mixed and ambiguous sentences fail closed and become explicit gaps.

- The landscape packet contains the compact initial source-node index, source edges, compact
  disease context, and a coverage checklist. Following
  `https://allenai.org/asta/resources/mcp`, the worker searches papers by relevance, inspects
  related citing papers, and runs paper-restricted snippet search on every paper retained for evaluation.
  Calls are sequential and a pending call is not terminated before 180 seconds. A retryable error
  or 180-second no-response receives exactly one minimal same-operation retry and another full
  wait. A terminal citation failure is endpoint-specific and does not prevent snippet evaluation
  of the original relevance paper; only relevance search failing both attempts makes Asta
  unavailable. `asta_call_receipts` contains one non-secret row per actual call with only logical
  operation, tool, paper ID when applicable, attempt/profile, outcome category, elapsed time,
  result count, and bounded error type. Query text, raw payloads, error messages, headers, and
  credentials remain transient. Each proposal
  contains exactly `label`,
  `provisional_type`, `claim`, `index_comparison`, and `source_ids`; it cites a returned canonical
  paper with inspectable content. Python rejects duplicates, unknown sources, treatment framing,
  worker-supplied IDs, and unused papers, then assigns `ASTA-NODE-<hash>` from normalized type,
  label, and claim. Zero proposals are valid.

- Monarch, DisMech, and projected Asta nodes are disease-specific claims with a provisional type and retained sources. Each Asta proposal contains one pathological state or process at one causal level; one paper may support several separate proposals. The curator assigns the authoritative run-local concept type from the supplied claim, payload, and edges; Asta proposals have no privileged status.
- Non-node DisMech material is retained in `disease_context`. The curator receives only compact disease-defining context and performs packet-only classification without searching or deep research. Receipts and full provenance remain controller-owned and do not create independent research tasks.
- A curated concept chooses one member source-node ID as its run-local `concept_id`, retains member IDs and aliases, and uses one of `driver`, `mechanism`, `phenotype`, or `context`. A mechanism concept contains one pathological state or process at one causal level. Nodes merge only when the same pathological state, biological context, profile, and desired biological state fit every member; source-supported claims at different causal levels remain separate even when causally linked. Shared identifiers or biological relationships are not equivalence and are represented by source edges or researched assertions. Same-label gene-level disease claims may merge across sources, while mutation-, variant-, repeat-, model-, and mechanism-specific claims remain separate. This identity rule does not determine disposition. True duplicates remain as members of the retained concept, and Python rejects duplicate retained type-label pairs. Python requires an exact partition and does not perform fuzzy matching.
- Concept type and disposition are separate judgments. Concept distinctness does not create a research job, and researchability may not be deferred to deep research. A concept is `research` only when the supplied packet already establishes a specific abnormal biological state or process, a specific well-supported causal lesion defining its own pathology route and non-generic compensatory direction, or a major phenotype defining a separate intervention objective. A bare gene or gene-disease association, risk factor, model genotype, broad pathway, terminal outcome, or mutation label without supplied functional pathology is normally `context_only`. Generic gene and lesion-specific claims do not both create research routes unless each supplies a distinct intervention variable. Subordinate phenotypes and measurement-only biomarkers are normally `context_only`; a distinct modifiable phenotype or separate intervention objective may be `research`, while a biomarker-labelled causal process is classified by that mechanistic role, normally as a mechanism.
- Only `research` concepts receive deep work. Each `context_only` concept names at least one research concept in `related_concept_ids`; Python retains it in the frozen graph and creates explicit context edges without a separate research packet. `exclude` concepts remain visible in the curation artifact but do not enter the graph.
- Each researched concept returns one detailed profile covering normal and pathological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps. `desired_biological_state` contains one primary biological variable and one desired direction; an irreversible driver uses a specific compensatory state rather than generic improvement. `secondary_desired_states` may contain distinct atomic biological states and may be empty. `phenotype_objective` states the separate disease-phenotype change sought. State fields do not contain phenotype outcomes, assays, stages, populations, treatments, candidates, or generic clinical improvement. Important bundled or missing submechanisms remain explicit gaps and do not create new graph nodes.
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

The audit packet contains the reviewed candidates, dossiers, complete frozen evidence graph, candidate-identity result, and a canonical index of the deduplicated retained corpus with inspectable passages or controller-retained source content. The auditor must use that closed corpus, independently weigh the argument and evidence, and partition every candidate exactly once. It must not search, add evidence, restate a dossier as analysis, request re-review, or defer a source judgment for later verification. Established exact-disease use and exact-disease human intervention are exclusionary; preclinical disease work is not. Long causal distance, weak evidence, unresolved identity, uncertain exposure, and material assumptions remain scored reservations rather than exclusions.

Each assessment contains `source_integrity`, exactly four `component_scores`, a cited `net_assessment`, and only the cited `aliases` and `why_not` findings the auditor accepts for final output. `source_integrity` contains only `checks`; each check identifies one `source_id`, its exact scope (a component name, `net_assessment`, `aliases[n]`, or `why_not[n]`), a verdict of `supports`, `partly_supports`, `does_not_support`, or `contradicts`, and a concrete finding based on retained content. Python requires every cited source-use pair exactly once and rejects two identifier aliases for the same publication within one scope. A flat status, generic declaration, or instruction to re-verify is invalid. The four components are `drug_action_confidence`, `disease_mechanism_relevance`, `mechanistic_bridge_plausibility`, and `translational_feasibility`. The packet carries distinct category-specific anchors: 5 is the weakest non-excluded, testable case; 10 is limited; 15 is strong with one material uncertainty; and 20 is direct, convergent support. Counterevidence never earns positive scoring credit: it lowers each component whose premise it directly challenges and otherwise remains an unscored `why_not` finding and part of the net assessment. Each score has a reason and retained `source_ids`; Python sums the values without weighting to a prioritisation score out of 80 and sorts descending by total, then candidate ID, with tied totals sharing a dense rank. The score is not a probability of efficacy.

An exclusion contains `candidate_id`, a cited `finding`, `source_integrity` checks with scope `exclusion`, and one `reason_code`: `exact_disease_use`, `human_intervention`, `unsupported_action`, `opposite_action`, `impossible_translational_feasibility`, or `invalid_candidate`. The packet carries the exact definition of every code. In particular, missing downstream evidence is not an unsupported drug action, uncertainty is not demonstrated translational impossibility, and unresolved identity is not an invalid candidate. A component-level failure corresponding to zero belongs in this exclusion record rather than in a scored assessment. Final cards use only audit-owned assessment fields; review fields never enter them directly.

Only `status=complete` results are accepted. On validation failure, stop and report the exact
error; do not retry automatically or repair the worker's research JSON. Conflicting replacement
of an accepted result is rejected.
