# Schema-v7 retrieval adapters and coverage proof

Use `scripts/v7_retrieval_adapter.py` as the generic boundary between declared source queries and already-existing v7 discovery/seed models.

## Contract

1. Build one content-derived `DeclaredSourceUniverse` for a named source release or snapshot. Declare native scope, exact source/local filters, denominator semantics, traversal grammar, caps, and limitations.
2. Build one content-derived `QueryPlan` per query family. Preserve exact request parameters, retry policy, allowed terminal codes, and all local bounds.
3. Implement `RetrievalAdapter` with only `supports`, `retrieve`, and `normalize`. Return exact response bytes and typed pagination metadata. Normalize each native record once; do not assign coverage status or receipt IDs inside the adapter.
4. Call `execute_query_plan`. The controller follows every returned continuation until an adapter terminal, declared bound, rate-limit terminal, or failure. It assigns receipts and maps eligible normalized assertions to `SeedSourceMapping`, `SeedDiscoveryRoute`, and `CandidateSeed` records.
5. Call `validate_coverage_proof` before consuming results. Use `combine_coverage_proofs` for declared query overlap; it unions discovery routes while retaining one stable seed per source assertion.

## Receipt separation

- `RetrievalContentReceipt` binds adapter/source versions, source universe, query plan and family, exact parameters, request/response hashes, page/cursor lineage, returned/normalized counts, provider total, terminal code, and truncation state. Scientific records may reference this stable receipt.
- `RetrievalExecutionReceipt` binds attempt number, timestamps, retry delay, transient error, rate-limit observation, cache hit, and the linked content receipt. Do not place this schedule-specific receipt in a scientific content hash.

Use `ContentAddressedRetrievalCache` for exact-response replay. A replay must reproduce content receipt IDs, normalized records, seeds, and coverage proof ID. Its execution trace may differ because timestamps and cache-hit metadata are execution facts.

`NormalizedSeedAssertion` may retain lightweight `SourceEvidenceAnnotation` and `PublicationDensityMetadata` records. These preserve source-native study status, negative/null/result context, and descriptive publication/citation counts without becoming deep grounded claims, therapeutic verdicts, scores, or seed-admission gates.

## Whole-case production aggregate

Use `V7DiscoveryAdapter.retrieve_and_seed(case_revision, source_plan, frozen_pages)` from `scripts/v7_production_discovery.py` after every branch has been declared. Supply one immutable source-plan revision, unique branch and query-plan identities, and exact frozen response pages for configured public adapters. Represent inaccessible sources as declared branches so they reduce to `unsupported_source_capability`; do not delete them from the plan.

The adapter executes the existing source adapters, validates each coverage proof, reduces declared overlap, and atomically persists one case/source-plan aggregate. It returns source universes, branches, content receipts, one mapping outcome per normalized native-item occurrence, one emission outcome per eligible assertion, stable deduplicated seeds and lineage, branch closure states, explicit gaps, and whole-case reconciliation. Schedule-specific execution receipts are persisted separately and do not enter the scientific aggregate hash. Identical replay is a no-op; changing frozen pages under the same source-plan identity fails and requires a new source-plan revision.

## Bounded states

Use only mechanically derived states:

- `complete_for_declared_query_and_release`
- `no_relevant_hits_within_declared_query`
- `partial_due_to_source_limit`
- `partial_due_to_rate_limit`
- `unsupported_source_capability`
- `failed_retrieval`
- `not_yet_searched`

Never translate these to universal source, literature, chemical, or scientific exhaustiveness.

## Required reconciliation

Require all of the following before accepting a complete proof:

- gapless page ordinals and input/output continuation links;
- one successful execution receipt per content receipt;
- consistent source-reported totals where supplied;
- returned count equals normalized record count for every accepted page;
- exactly one screening disposition per normalized record;
- emitted seed count equals unique linked seed records;
- exact source assertion, record, receipt, and query-plan lineage for every seed;
- zero unvisited records for a known finite denominator;
- an adapter-specific terminal receipt with final continuation exhaustion.

A nonterminal response without a continuation is a contract failure. A source record carries no worker-supplied query-family label: its family derives from the content-bound query plan. Reusing a receipt under another family fails validation. Legitimate overlap requires a separately declared and executed plan and adds lineage without seed inflation.

## Frozen acceptance fixtures

Use `scripts/v7_retrieval_adapter_mock.py` and `benchmarks/schema_v7/retrieval_adapter/frozen_mock_scenarios.json` only for offline tests. They exercise page and cursor traversal, empty results, bounded partial coverage, malformed responses, retries, rate-limit partials, deterministic replay, omitted continuations, declared query overlap, and 1,000-record seed emission. They are synthetic and are not real source adapters.
