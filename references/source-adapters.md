# Pathology source adapters

Source ingestion runs as the first deterministic controller barrier. Responses are cached immutably under `<run>/sources/raw/`; packets receive normalized pathology records and receipts, not credentials.

Normalized non-anchor nodes remain source records until the curation barrier assigns each one exactly once to a run-local research, context-only, or excluded concept. Source adapters do not perform fuzzy semantic merging or research-value pruning.

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

All remaining pathology-safe sections are retained once in `disease_context` rather than expanded into low-value nodes. This includes disease description, classifications, mappings, inheritance, progression, stages, subtypes, prevalence, epidemiology, datasets, models, diagnostic context, discussions, and other source metadata. Curation receives the complete context; repeated research packets receive only compact disease-defining context.

Treatment, clinical-trial, intervention, regimen, surrogate-endpoint, and related sections or nested fields are excluded. Explicit intervention names and their bounded acronyms are used only as an internal redaction lexicon and are never emitted. Remaining free text is split into sentences and any sentence containing treatment language or a named intervention is removed before source records or packets are written. Raw YAML remains cached unchanged for provenance.

If no MONDO-mapped DisMech entry exists, the adapter records an explicit gap and continues with Monarch rather than treating source coverage as programme failure.

## Invariants

- Never place credentials or authorization headers in packets, results, URLs, logs, or manifests.
- Preserve source version, exact query, native record ID, retrieval time, raw path, and response hash.
- Exhaust declared pagination or record a bounded gap.
- Source traversal never means scientific completion.
- Add source types by normalizing into the existing pathology source records; do not add workflow branches.
