# Pathology source adapters

Source ingestion begins with deterministic collection and screening. Responses are cached
immutably under `<run>/sources/raw/`. One isolated, compact adjudication packet is emitted only
when free-text sentences are flagged; subsequent packets receive normalized pathology records and
receipts, not credentials or excluded sentences.

Normalized non-anchor nodes remain source records until the curation barrier assigns each one exactly once to a run-local research, context-only, or excluded concept. Source adapters do not perform fuzzy semantic merging or research-value pruning.

## Asta pathology landscape scan

Asta is not a Python source adapter and has no controller transport. After normalized Monarch and
DisMech acquisition, one global worker uses the host-configured Asta MCP tools for bounded
literature discovery, following the official signatures at
`https://allenai.org/asta/resources/mcp`. The packet carries a compact initial index, source edges, compact disease
context, and coverage checklist. The worker searches papers by relevance, inspects related citing
papers, and runs paper-restricted snippet search on every paper retained for evaluation. It does not recurse
through citations, pathways, or authors.

The worker makes Asta calls sequentially, awaiting every `get_citations` and paper-restricted
`snippet_search` response before making the next call. A response taking about 30 seconds is
normal and is not an outage. An outage is recorded only after a completed tool error or after
waiting at least 45 seconds for a response.

The worker returns only canonical papers cited by an actual missing or more-specific pathology
proposal, with inspectable evidence passages from the underlying paper. Search results, citation
results, snippets, raw MCP responses, and authentication data are transient. Python validates the
papers and proposals and assigns stable `ASTA-NODE` identities; the existing curator then treats
the projected proposals on equal terms with Monarch and DisMech claims and decides whether each is
truly distinct. Only documents attached to proposals surviving curation can enter the frozen graph.
An Asta outage produces empty collections and an explicit gap, leaving source-derived curation
available.

Configure `ASTA_AI2_API_KEY` in the MCP host. The controller never reads it, constructs Asta
requests, writes authentication headers, or caches raw MCP exchanges.

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

If no MONDO-mapped DisMech entry exists, the adapter records an explicit gap and continues with Monarch rather than treating source coverage as programme failure.

## Invariants

- Never place credentials or authorization headers in packets, results, URLs, logs, or manifests.
- Preserve source version, exact query, native record ID, retrieval time, raw path, and response hash.
- Exhaust declared pagination or record a bounded gap.
- Source traversal never means scientific completion.
- Add source types by normalizing into the existing pathology source records; do not add workflow branches.

## Publication identity

PMID, PMCID, and DOI references from source adapters and research workers use the same small controller-owned validation path. Responses from the NCBI identifier converter, PubMed or PubMed Central summary service, and DOI resolver are cached immutably under `<run>/sources/raw/bibliography/`. The controller uses them only to validate and project bibliographic identity; it does not infer scientific support from metadata. A submitted publication title that does not match the identifier stops acceptance. Known aliases are retained under one canonical publication identity in downstream source projections, while the originally cited document ID remains stable for provenance.

## UniChem candidate identity

Candidate identity resolution is separate from treatment-blind pathology ingestion. After every
seed packet is accepted, the controller queries the EMBL-EBI UniChem `compounds` endpoint for
every supported source identifier and caches each response immutably under
`<run>/sources/raw/unichem/`. Exact UCI equality is the only automatic candidate merge.

The controller also checks connectivity for every exact UCI. Connectivity-only relationships,
partial or conflicting mappings, unsupported identifiers, and explicit no-result responses enter
the complete identity-review queue. Operational API failure stops controller advancement and is
never recorded as a scientific no result. Requests are deduplicated and sequential; transient
rate-limit, server, network, and timeout failures receive two bounded retries.
