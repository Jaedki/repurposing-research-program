# Pathology source adapters

This file owns source acquisition, service-call behavior, retry and recovery rules, receipts, and
external identity transport. Worker result fields and scientific decision gates live in
[packet-contract.md](packet-contract.md); controller-stage ownership lives in
[architecture.md](architecture.md).

Source ingestion begins with deterministic collection and screening. Responses are cached
immutably under `<run>/sources/raw/`. One isolated, compact adjudication packet is emitted only
when free-text sentences are flagged; subsequent packets receive normalized pathology records and
receipts, not credentials or excluded sentences.

Normalized non-anchor nodes remain source records until the curation barrier assigns each one exactly once to a run-local research, context-only, or excluded concept. Source adapters do not perform fuzzy semantic merging or research-value pruning.

## Asta pathology landscape scan

Asta is not a Python source adapter and has no controller transport. After normalized Monarch and
DisMech acquisition, one global worker uses the host-configured Asta MCP tools for coverage-driven
literature discovery, following the official signatures at
`https://allenai.org/asta/resources/mcp`. The packet carries a compact initial index, source edges, compact disease
context, and coverage checklist. The worker runs one stable broad relevance search and focused
searches for every open coverage gap. It preserves pending originals across cycles until every gap
is classified as resolved, searched-unresolved, or merged, with no fixed search, paper, or proposal
count. Completed receipts provide exact-ID deduplication while a live index tracks
duplicate/refinement/new mechanisms. For every retained original it retrieves
at most three citing papers and runs one paper-specific snippet search per distinct paper. It does not recurse
through citations, pathways, or authors.

A targeted structured scientific lookup may resolve an entity, pathway, interaction, expression, or
ontology ambiguity when that materially sharpens an Asta query. It remains transient and does not
replace the required Asta search or the source-backed proposal returned from that search.

The worker makes exactly one Asta call per orchestration step and inspects its complete response
before constructing the next; calls are never batched, looped, or buffered. `get_citations`
papers are read from `structuredContent.result[].citingPaper`; a different shape blocks rather
than silently discarding citations. Slow responses are not outages. A pending
call is not terminated before 180 seconds. A completed retryable error or a 180-second no-response
receives exactly one retry of the same logical operation using a minimal payload, followed by
another full wait. Minimal search and citation retries request only title, year, and URL with the
smallest useful limit; a minimal snippet retry uses a concise query and limit 1. Authentication and
invalid-request errors are blocking defects, not outages. A terminal `get_citations` failure is
endpoint-specific: the worker still performs paper-restricted `snippet_search` on the original
relevance paper and records a partial gap. One failed relevance-search operation also records a
gap. At least one relevance search must complete before the scan can be accepted.

The worker returns only canonical papers cited by an actual missing or more-specific pathology
proposal, with inspectable evidence passages from the underlying paper. Search results, citation
results, snippets, raw MCP responses, query text, error messages, and authentication data are
transient. The worker returns one non-secret receipt per call containing only its logical operation,
tool, paper ID when applicable, attempt and request profile, outcome category, elapsed time, result
count, and bounded error type. Python validates the
papers and proposals and assigns stable `ASTA-NODE` identities; the existing curator then treats
the projected proposals on equal terms with Monarch and DisMech claims and decides whether each is
truly distinct. Only documents attached to proposals surviving curation can enter the frozen graph.
Receipt validation rejects early no-response claims, missing retries, citation abandonment without
snippet evaluation, blocking request/authentication defects, and a scan with no completed relevance
search. The coverage register must classify every gap as resolved, searched-unresolved, or merged,
link tested gaps to completed search operations, cite retained proposal sources for resolutions,
and use a focused search for unresolved gaps.

Configure `ASTA_AI2_API_KEY` in the MCP host. The controller never reads it, constructs Asta
requests, writes authentication headers, or caches raw MCP exchanges.

## Undermind pathology coverage expansion

Undermind is also an agent-used MCP service rather than a Python source adapter. After the Asta
result is accepted, one global worker receives the complete projected post-Asta node index, source
edges, disease context, coverage checklist, upstream gaps, and a stable logical search name. It
calls `get_orientation`, lists and reuses or creates a workspace, inspects named searches, launches only when absent,
polls the asynchronous search without interrupting it, inspects every ranked-result page, and reads
selected papers in one native batch. A lost launch response is recovered by inspecting first and
relaunching the same logical name only if no search exists. Only
canonical underlying papers cited by an actual proposal are returned, with inspectable full-text
passages; search and account data remain transient.

`coverage_proposals` use the same scientific shape as Asta proposals. One non-secret completion
receipt records workspace, search name and path, ranked-result IDs and count, PDF count, and a compact
disposition for every paper actually read. Each disposition preserves the ranked cite key and
rationale; retained papers additionally crosswalk to their returned canonical document ID. Python validates their
evidence and assigns `UNDERMIND-NODE-<hash>` IDs; the final curator treats them on the same terms as
all other source nodes and alone decides concept identity. The packet remains active until its
search receipt records a completed outcome.

## Monarch Initiative

The adapter resolves the case to an exact MONDO entity, preferably from `--mondo`. It exhausts the v3 association endpoint with an explicit pathology allowlist:

- disease to phenotype;
- causal and correlated gene to disease;
- variant and genotype to disease;
- disease or phenotype to location.

It rejects any chemical, drug, treatment, or therapeutic category even if the upstream API changes. Exact non-secret parameters, API version, primary knowledge source, native association ID, pagination, and raw responses are retained.

## DisMech

DisMech's documented source of truth is its repository YAML. The adapter pins the default branch commit, resolves the MONDO ID through `exports/mondo_emc.tsv`, fetches the matching disorder YAML, and parses it with PyYAML.

Every top-level DisMech section is accounted for. Sections that can express distinct pathology concepts are normalized as source nodes:

- `mechanistic_hypotheses`, `pathophysiology`, `biochemical`, and infectious-agent life-cycle or transmission mechanisms become `mechanism` nodes;
- `phenotypes`, `histopathology`, and `imaging_findings` become `phenotype` nodes;
- `genetic`, `variants`, `environmental`, and `infectious_agent` become `driver` nodes.

DisMech `downstream` and `sequelae` relationships retain edge-local citations when supplied. When
the record reports a relationship without one, the edge cites the immutable DisMech source record
and carries `evidence_scope=source_record`; endpoint-node citations are never borrowed. This keeps
the relationship available as an exploratory lead without overstating primary evidential support.

All remaining pathology-safe sections are retained once in `disease_context` rather than expanded into low-value nodes. This includes disease description, classifications, mappings, inheritance, progression, stages, subtypes, prevalence, epidemiology, datasets, models, diagnostic context, discussions, and other source metadata. The controller retains the complete context and provenance. Curation receives source nodes, edges, and only compact disease-defining context; repeated research packets receive the same bounded context.

Treatment, clinical-trial, intervention, regimen, surrogate-endpoint, and related sections or
nested fields are excluded unconditionally. Explicit intervention names and their bounded
acronyms remain an internal screening lexicon. Remaining free text is split into sentences and
screened by named-intervention, treatment-language, and treatment-event signals. Flagged sentences
are deduplicated into one bounded packet containing stable IDs, exact text, signal classes, and
source paths. The adjudicator does not search, rewrite, cite, or create nodes; it classifies every
sentence exactly once. Python restores only exact sentences classified `retain_pathology`.
Treatment sentences are excluded, while mixed or ambiguous sentences fail closed and create an
explicit gap. Raw YAML remains cached unchanged for provenance.

If no MONDO-mapped DisMech entry exists, the adapter records an explicit gap and continues with
Monarch. When both disease wording and a gene are supplied, up to three related spectrum entries
matching both are added as clearly labelled discovery leads for Asta or Undermind; they are never
normalized as focal nodes, context, or evidence.

## Invariants

- Never place credentials or authorization headers in packets, results, URLs, logs, or manifests.
- Preserve source version, exact query, native record ID, retrieval time, raw path, and response hash.
- Exhaust declared pagination or record a bounded gap.
- Source traversal never means scientific completion.
- Add source types by normalizing into the existing pathology source records; do not add workflow branches.

## Publication identity

PMID, PMCID, and DOI references from source adapters and research workers use the same small controller-owned validation path. Responses from the NCBI identifier converter, PubMed or PubMed Central summary service, and DOI resolver are cached immutably under `<run>/sources/raw/bibliography/`. The controller uses them only to validate and project bibliographic identity; it does not infer scientific support from metadata. A submitted publication title that materially disagrees with the identifier stops acceptance; formatting and minor wording variants share one generic near-match check. Known aliases are unioned monotonically under one canonical publication identity in downstream source projections, while the originally cited document ID remains stable for provenance; repeated canonicalization is idempotent. Treatment-blind pathology projections retain their already screened submitted title when canonical metadata is added; canonical titles are restored only after the pathology barriers.

## UniChem candidate identity

Candidate identity resolution is separate from treatment-blind pathology ingestion. After every
seed packet is accepted, the controller queries the EMBL-EBI UniChem `compounds` endpoint for
every supported source identifier and caches each response immutably under
`<run>/sources/raw/unichem/`. Uncached requests run sequentially in bounded batches; a
`needs_controller` progress response resumes from that cache on the next `next` call. Exact UCI
equality is the only automatic candidate merge.

The controller also checks connectivity for every exact UCI. Connectivity-only relationships,
partial or conflicting mappings, unsupported identifiers, and explicit no-result responses enter
the complete identity-review queue. Operational API failure stops controller advancement and is
never recorded as a scientific no result. Requests are deduplicated and sequential; transient
rate-limit, server, network, and timeout failures receive two bounded retries.
