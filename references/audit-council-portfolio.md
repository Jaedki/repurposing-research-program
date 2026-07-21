# Schema-v7 audit, council, and portfolio policy

Use `scripts/v7_audit_portfolio.py` after deterministic pre-audit ranking preparation. It is a runtime-free policy core. It does not retrieve evidence, mutate deep packages, persist ledgers, redesign final files, or certify a release.

## Stratified audit

Freeze the candidate frame, provisional finalist set, and `AuditSamplingPolicy` before outcomes. Call `build_audit_candidate_frames` and `build_audit_plan` to:

- audit every provisional finalist;
- audit every material conflict derived from frozen support/uncertainty bands;
- take configurable deterministic novel/underexplored and uncertainty samples;
- take a SHA-256-keyed seeded tail sample; and
- emit one assignment for every frozen candidate, including explicit `unaudited` assignments.

Keep the per-stratum population denominator, mandatory census count, planned sample count, selected IDs, unaudited IDs, and rule. A candidate that would enter finalist or reserve capacity while unaudited triggers `needs_additional_audit`; do not silently exclude or promote it.

Record one of `support`, `qualify`, `contradict`, `unresolved`, `correct`, `supersede`, `quarantine`, or `reject`. Keep the decision effect separate. A reranking effect requires a matching ranking revision before portfolio use. Require independent search receipts and refuse claim-author self-approval.

## Correction authority

Use append-only `AuditCorrection` records for chemical identity, active-moiety mapping, claim statement, direction, human relevance, causal path, endpoint, candidate class, exposure, safety, and ranking features. A correction or supersession names a distinct replacement and retains the prior canonical value and hash. Quarantine and rejection carry no hidden replacement.

Call `apply_audit_corrections` to build a current overlay. It retains the original snapshot and the complete parent-linked chain, refuses parallel replacement, validates every prior-value hash, and never rewrites the source record. Bridge identity/claim/path corrections to the narrower `v7_deep_evidence.RecordCorrection` package hook when constructing a revised deep package; do not delete either history.

## Council review

Create council work only for a typed `CouncilIssue` with a decision impact on eligibility, ordering, lane, cutoff, tie outcome, or a safety/exposure block. Emit one typed assessment per issue. Use evidence-ancestry cluster IDs as the independence basis; reviewer or agent count is provenance, not independent evidence.

Unresolved, contradicted, quarantine, and reject findings cannot be silently retained. A class-based baseline-only or benchmark-only disposition requires a typed candidate-class issue. Council does not replace source audit and does not run an advocate/sceptic vote simulation.

## Diversified portfolio

Supply normalized target and mechanism IDs, structural causal routes, a versioned scaffold descriptor, evidence modalities, endpoint IDs, development statuses, and uncertainty bands. Mechanism clustering uses shared normalized target/mechanism tokens with deterministic transitive closure. Scaffold clustering uses the supplied versioned scaffold key; all missing scaffold descriptors share one unknown cluster and cannot create false diversity.

Call `select_diversified_portfolio` with a frozen `PortfolioPolicy`. It emits separately:

- evidence-strength rank;
- novelty/information-value rank; and
- diversified portfolio rank.

The greedy policy publishes evidence, information, and marginal diversity components plus per-dimension new values and weights. Diversity can change membership but never changes therapeutic-support or evidence-quality bands. Every finalist and reserve must have a compatible completed audit; blocking audit/council outcomes remain explicit dispositions.
