# Pathology source adapters

Source ingestion runs as the first deterministic controller barrier. Responses are cached immutably under `<run>/sources/raw/`; packets receive normalized pathology records and receipts, not credentials.

## Monarch Initiative

The adapter resolves the case to an exact MONDO entity, preferably from `--mondo`. It exhausts the v3 association endpoint with an explicit pathology allowlist:

- disease to phenotype;
- causal and correlated gene to disease;
- variant and genotype to disease;
- disease or phenotype to location.

It rejects any chemical, drug, treatment, or therapeutic category even if the upstream API changes. Exact non-secret parameters, API version, primary knowledge source, native association ID, pagination, and raw responses are retained.

## DisMech

DisMech's documented source of truth is its repository YAML. The adapter pins the default branch commit, resolves the MONDO ID through `exports/mondo_emc.tsv`, fetches the matching disorder YAML, and parses it with PyYAML.

Only pathology sections are exposed: disease description and mappings, inheritance, progression, mechanistic hypotheses, pathophysiology, phenotypes, biochemical findings, genetics, environmental factors, and evidence references. Treatment and clinical-trial sections and nested drug/compound/therapeutic fields are excluded before packet construction.

If no MONDO-mapped DisMech entry exists, the adapter records an explicit gap and continues with Monarch rather than treating source coverage as programme failure.

## Invariants

- Never place credentials or authorization headers in packets, results, URLs, logs, or manifests.
- Preserve source version, exact query, native record ID, retrieval time, raw path, and response hash.
- Exhaust declared pagination or record a bounded gap.
- Source traversal never means scientific completion.
- Add source types by normalizing into the existing pathology source records; do not add workflow branches.
