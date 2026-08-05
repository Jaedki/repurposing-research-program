# Architecture

## Fixed barriers

| Barrier | Owner | Completion condition |
| --- | --- | --- |
| `pathology_source_screening` | Python | Structured treatment fields are excluded and every remaining DisMech sentence matching a named-intervention, treatment-language, or treatment-event signal is deduplicated into one bounded batch |
| `pathology_source_adjudication` | One bounded agent, or Python when the batch is empty | Every flagged sentence is classified exactly once as pathology-only, treatment, mixed, or ambiguous without searching, rewriting, or creating nodes |
| `pathology_sources` | Python | Treatment-blind Monarch and DisMech nodes, shared disease context, receipts, versions, and raw hashes are retained |
| `pathology_landscape_scan` | One global agent | One shallow Asta scan searches by relevance, inspects related citing papers and paper-restricted snippets, returns non-secret call receipts, and returns only cited missing or more-specific pathology proposals; relevance-search unavailability after one minimal retry returns an explicit gap and empty scientific collections |
| `pathology_curation` | One agent | Every non-anchor Monarch, DisMech, and projected Asta node is assigned exactly once to an atomic run-local concept marked `research`, `context_only`, or `exclude`; researchability follows the biological claim rather than its provisional label, context is attached to retained research concepts, and uncertain equivalence remains separate |
| `evidence_graph` | Python after item work | Every curated research concept has one accepted deep-research profile and desired biological state; Python projects retained concepts and source edges through the partition and freezes the graph snapshot |
| `candidate_seed_generation` | Python after item work | Every researched pathology concept has one accepted seed result; Python assigns immutable seed IDs and submits every supported identifier to UniChem |
| `candidate_identity` | Python plus one bounded agent review when needed | Exact UniChem UCI groups merge automatically; every connectivity match, identifier conflict, unsupported candidate, and no-result seed is partitioned exactly once by the identity reviewer |
| `candidate_review` | Python after item work | Every nonempty concept review batch has one accepted evidence dossier covering each assigned canonical candidate exactly once |
| `candidate_audit` | One independent agent, then Python | Every reviewed candidate is partitioned exactly once into a scored assessment or a bounded, cited exclusion; at least one assessment remains and Python computes raw totals and the final order |

Within the three item barriers, `next` selects the first missing item from a stable sorted manifest. Seed packets carry a compact index of every retained non-anchor graph concept; the read-only `graph-context` command returns one deterministic node projection from the frozen snapshot without changing controller state. Seed workers must review the complete immediate focal projection before searching but retain only materially useful context. Candidate identity is global and receives only the deterministic UniChem residue plus a compact, controller-generated list of every legal canonical candidate option. Candidate review reuses curated concept IDs as batch IDs and receives, per candidate, the exact selected assertions plus source edges bounded by selected nodes and cited pathology sources. After identity resolution, Python assigns each candidate once to a linked origin concept, breaking ties by concept ID. This is a deterministic cursor, not a general DAG, queue, scheduler, or agent-controlled handoff.

## Separation of evidence

The isolated source-adjudication packet may contain only the compact flagged-sentence batch and
cannot propagate into the pathology graph. The landscape packet receives only the compact initial
index and pathology context, retrieves literature through the host-configured Asta MCP service, and
cannot return node IDs or treatment-framed proposals. Search results, citation results, snippets,
and raw MCP content remain transient; bounded receipts retain only tool, logical operation, paper
ID when applicable, attempt/profile, outcome category, elapsed time, and result count. The
subsequent pathology phase may contain disease,
gene, variant, molecular, biochemical, cellular, tissue, organ, anatomy, and phenotype records.
It structurally rejects candidate, compound, drug, treatment, and therapeutic fields.

The graph becomes candidate input only after its immutable `snapshot_id` is written. Candidate evidence has two explicit parts:

1. `pathology_source_ids`: why this mechanism element belongs to this disease;
2. `mechanism_source_ids`: why the drug has the required biological action.

The chain is sufficient without a paper directly joining the drug to the disease.

## Ownership

Python owns order, source receipts, hashing, item cursors, immutable acceptance, structured
treatment exclusion, sentence screening, application of adjudication decisions, curation coverage
checks, deterministic Asta-node identity,
cross-reference checks, secret rejection, graph freezing
and context projection, exact
UniChem merging, cached publication-identity validation, candidate aggregation, raw score calculation, ranking order, and exports. The
source adjudicator owns only the interpretation of flagged sentences and may neither search nor
rewrite them. The landscape worker owns one bounded literature-discovery scan and proposal set; it
does not curate or perform deep node research. The candidate identity reviewer owns only the
interpretation of UniChem-flagged or
unresolved seeds. Candidate evidence reviewers own source-backed dossiers but do not score or
decide eligibility. The independent auditor owns the closed-corpus assessment, bounded exclusions,
component judgments, net assessment, and final aliases and reservations. The curation agent owns
pathology semantic equivalence and research-value judgment; research agents own research content.
Sources own evidence. No worker may select the next task or declare the programme complete.

A run is derived from `case.json`, canonical `results/*.json`, item results under `results/items/`, cached source receipts, and the final output manifest. The manifest hashes every accepted result file.

## Evidence propagation

Accepted worker results remain unchanged for provenance. Research documents carry the exact retained passage or passages used by the worker. At submission, Python resolves PMID, PMCID, and DOI metadata through cached authoritative endpoints and rejects an ID/title mismatch. At each deterministic aggregation boundary, Python recursively collects `source_ids`, `pathology_source_ids`, and `mechanism_source_ids` from all non-document records, including nested observations, and propagates only returned documents whose IDs were cited by that result. Unused documents remain in the accepted worker result but do not enter downstream context. Landscape papers receive an additional semantic filter: only papers attached to Asta nodes surviving curation may enter the frozen graph or later retained corpus. Repeated document IDs union list evidence only when bibliographic identity fields agree; conflicting identity metadata stops aggregation instead of being overwritten.

Aggregated stages own only their evidence: the graph retains cited pathology documents, seed generation retains cited seed documents, identity contributes cited identity documents, and candidate review retains cited review documents. They do not copy prior stage libraries. Researched assertions are merged by biological triple and keep sources and summaries only inside model-, stage-, type-, and polarity-specific evidence contexts. `_all_documents()` constructs the deduplicated union when audit or output generation genuinely needs cross-stage evidence. The audit receives this closed union as a canonical source index, including retained passages and controller-resolved publication aliases, together with the complete frozen graph and candidate-identity result; it cannot add documents. A result may cite an upstream document without returning it again, so propagation requires returned document IDs to be a subset of the result's citations, not equality.

Final evidence cards contain the canonical candidate ID, the raw score out of 80, all four cited component judgments, only aliases retained by the auditor, one cited net assessment, and only cited why-not findings retained by the auditor. The auditor checks each exact source-use pair against retained content as `supports`, `partly_supports`, `does_not_support`, or `contradicts`; it cannot replace these checks with a generic status or defer them for later verification. Python validates exhaustive, non-duplicated coverage, summarizes the verdicts, and exposes non-supporting checks as citation-audit exceptions. Source integrity and counterevidence remain unscored: counterevidence lowers a component only when it directly challenges that component's premise and never earns positive scoring credit. Review fields cannot flow directly into final cards. Python omits empty optional sections and does not infer card content.

## Status

- `needs_controller`: `next` can perform a deterministic source, merge, freeze, or aggregation action.
- `needs_agent`: `next` emits exactly one agent packet.
- `stopped`: a scientifically necessary collection is empty, with an explicit reason.
- `ready_to_build`: all barriers passed and at least one candidate received a scored audit assessment.
- `complete`: outputs exist and match the manifest hashes.

## Module boundaries

The programme remains a linear modular monolith. `program_core.py` is the stable public entry
point while extracted responsibilities have one owner:

- `repurposing_program.contracts` owns static workflow schemas, scientific rules, rubrics, and
  policies;
- `repurposing_program.errors` owns `ProgramError`;
- `repurposing_program.storage` owns canonical serialization, hashing, immutable writes, and run
  artifact paths;
- `repurposing_program.evidence` owns evidence-record access, document-content checks, citation
  traversal, source projection, and deterministic evidence merging;
- `repurposing_program.bibliography` owns publication-ID normalization, cached metadata transport,
  canonical publication projection, and bibliographic validation;
- `repurposing_program.validation` owns shared record-schema, reference, secret, and document-ID
  validation primitives;
- `repurposing_program.pathology` owns treatment-field rejection, landscape-proposal validation and
  deterministic node projection, curation projection, pathology source/adjudication validation,
  and researched-profile validation;
- `repurposing_program.graph` owns deterministic assertion merging, frozen-graph assembly, graph
  indexing, support lookup, and bounded node-context projection;
- `repurposing_program.identity` owns UniChem transport/caching, exact-identity grouping, identity
  review options, canonical candidate assembly, and identity-result validation;
- `repurposing_program.candidates` owns review-batch partitioning and candidate seed/dossier
  validation, including pathology-versus-mechanism citation separation;
- `repurposing_program.audit` owns closed-corpus audit partition, component-score, exact source-use,
  publication-alias, and bounded-exclusion validation;
- `repurposing_program.ranking` owns raw-score calculation, deterministic assessment ordering,
  dense ranks, and ranked-row projection;
- `repurposing_program.evidence_cards` owns final evidence-card projection and deterministic
  Markdown rendering;
- `repurposing_program.candidate_exports` owns candidate provenance and audited-exclusion output
  projections;
- `repurposing_program.manifests` owns artifact metadata and final manifest construction;
- `repurposing_program.run_state` owns case identity and initialization, accepted stage and item
  loading, derived stop/status state, output-manifest verification, and read-only graph context;
- `repurposing_program.packets` owns stage-specific context projection, worker result-contract
  construction, packet validation, content addressing, and packet persistence;
- `repurposing_program.orchestration` owns deterministic controller advancement, item-result
  aggregation, controller-built stage results, worker-result validation, and immutable submission;
- `repurposing_program.outputs` owns final CSV, JSON, JSONL, Markdown, graph, citation, and summary
  export plus the public output-build operation.

These extracted modules never import `program_core`. Higher domain modules may depend on evidence,
validation, and foundation modules; dependency must not point back from an extracted module to
orchestration. Orchestration depends on run state and packets, which remain independent of it.
The output modules form a terminal dependency branch: domain, validation, packet, orchestration,
and run-state modules do not import them; focused output projections do not import run state,
packets, orchestration, or the output coordinator. `outputs` alone coordinates read-only final
state with those projections and persistence. `program_core` contains no implementation logic and
re-exports only the unchanged explicit public API: programme contracts, controller lifecycle, and
the output build operation.
