# Architecture

## Fixed barriers

| Barrier | Owner | Completion condition |
| --- | --- | --- |
| `pathology_source_screening` | Python | One deduplicated flagged-sentence batch or an empty batch |
| `pathology_source_adjudication` | One bounded agent, or Python for an empty batch | Every flagged sentence classified once |
| `pathology_sources` | Python | Normalized treatment-blind source records and receipts |
| `pathology_landscape_scan` | One global agent | Terminal Asta coverage register and retained cited proposals |
| `pathology_coverage_expansion` | One global agent | One completed Undermind coverage challenge and retained cited proposals |
| `pathology_curation` | One agent | Exact partition into final run-local concepts and dispositions |
| `evidence_graph` | Python after item work | One accepted profile per research concept and one frozen snapshot |
| `candidate_seed_generation` | Python after item work | One accepted seed result per research concept and deterministic aggregation |
| `candidate_identity` | Python plus one bounded agent when needed | Complete resolution or explicit retention of every identity residue |
| `candidate_review` | Python after item work | One accepted dossier per assigned canonical candidate |
| `candidate_audit` | One independent agent, then Python | Exact candidate partition, at least one assessment, and deterministic ordering |

Within the three item barriers, `next` selects the first missing item from a stable sorted manifest.
`graph-context` returns a deterministic read-only node projection without changing controller state.
After identity resolution, Python assigns each candidate once to an origin concept, breaking ties by
concept ID. This is a deterministic cursor, not a general DAG, queue, scheduler, or agent-controlled
handoff. Packet contents and worker obligations are defined in [packet-contract.md](packet-contract.md).

## Separation of evidence

The isolated source-adjudication packet may contain only the compact flagged-sentence batch and
cannot propagate into the pathology graph. The landscape packet receives only the compact initial
index and pathology context and retrieves literature through Asta. The coverage-expansion packet
then receives the complete post-Asta index and retrieves literature through Undermind. Neither can
return node IDs or treatment-framed proposals. Search results, citation results, snippets,
deep-search reports, rankings, goals, queries, and raw MCP content remain transient. The
subsequent pathology phase may contain disease,
gene, variant, molecular, biochemical, cellular, tissue, organ, anatomy, and phenotype records.
It structurally rejects candidate, compound, drug, treatment, and therapeutic fields.

The graph becomes candidate input only after its immutable `snapshot_id` is written. Candidate evidence has two explicit parts:

1. `pathology_source_ids`: why this mechanism element belongs to this disease;
2. `mechanism_source_ids`: why the drug has the required biological action.

The chain is sufficient without a paper directly joining the drug to the disease.

Graph edges originate only from retained source edges or accepted researched assertions. Curation's
`related_concept_ids` remain a separate administrative `context_nodes` projection so a seed worker
can consider them while constructing rescue routes without mistaking curation proximity for sourced
biological support.

## Ownership

Python owns workflow state: order, receipts, hashing, item cursors, immutable acceptance, packet
construction, validation, deterministic source processing, graph freezing, identity aggregation,
scoring, ranking, and exports. Agents own only the scientific judgment assigned by their current
packet. In particular, curation owns pathology identity and research eligibility; review owns
evidence dossiers without scoring; audit owns closed-corpus assessment and bounded exclusions.
Sources own evidence. No worker selects the next task or declares the programme complete. The exact
division for every worker collection is documented in [packet-contract.md](packet-contract.md), and
source-service transport is documented in [source-adapters.md](source-adapters.md).

A run is derived from `case.json`, canonical `results/*.json`, item results under `results/items/`, cached source receipts, and the final output manifest. The manifest hashes every accepted result file.

## Evidence propagation

Accepted worker results remain unchanged. At each aggregation boundary, Python propagates only cited
documents owned by that stage; unused returned documents remain in the accepted result but do not
enter downstream context. Publication aliases are canonicalized without rewriting the submitted
document ID. `_all_documents()` constructs the deduplicated closed corpus only for audit and output
generation. The audit cannot add evidence. Final cards project only the preferred drug name, the
audit-owned cited mechanistic account, and audit-owned cited reasons against prioritisation; the
remaining audit and ranking fields stay in structured artifacts. See
[packet-contract.md](packet-contract.md) for citation propagation and audit fields, and
[source-adapters.md](source-adapters.md) for publication identity.
The compact exclusion list is exported as CSV; detailed source-integrity records remain in the
accepted audit result.

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
- `repurposing_program.pathology` owns treatment-field rejection, Asta and Undermind proposal and
  receipt validation, deterministic discovery-node projection, curation projection, pathology source/adjudication validation,
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
re-exports only the explicit public API: programme contracts, controller lifecycle, and the output
build operation.
