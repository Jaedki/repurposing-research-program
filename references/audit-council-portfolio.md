# Schema-v7 audit, council, and portfolio policy

Use `scripts/v7_audit_portfolio.py` after deterministic pre-audit ranking preparation. It remains the runtime-free policy core. Use `V7PortfolioAdapter.audit_and_select(case_revision, deep_frame, frozen_audit_plan)` in `scripts/v7_production_portfolio.py` for the persisted retrieval-backed Stage 7 aggregate.

## Stratified audit

Freeze the candidate frame, provisional finalist set, and `AuditSamplingPolicy` before outcomes. Call `build_audit_candidate_frames` and `build_audit_plan` to:

- audit every provisional finalist;
- audit every material conflict derived from frozen support/uncertainty bands;
- take configurable deterministic novel/underexplored and uncertainty samples;
- take a SHA-256-keyed seeded tail sample; and
- emit one assignment for every frozen candidate, including explicit `unaudited` assignments.

Keep the per-stratum population denominator, mandatory census count, planned sample count, selected IDs, unaudited IDs, and rule. A candidate that would enter finalist or reserve capacity while unaudited triggers `needs_additional_audit`; do not silently exclude or promote it.

For production aggregation, freeze all deep candidate IDs, seven decision outputs, pre-audit preparation records, finalist/reserve policy, scaffold descriptors, every audit unit and assignment, author identities, risk/size sample rules, thresholds, and escalation modes before reading outcomes. Cover candidate tier/disposition plus source, modality, endpoint, identity uncertainty, safety risk, novelty, and claim impact without building a full Cartesian product. Census every decision-capable deep claim and exact identity/safety/exposure record. Give every remaining seed, screen, source, and residual deep record an explicit sampled or unaudited assignment.

Require each achieved audit to retain an independent-search payload, source release/query/native locator, exact support span, recomputed payload hash, auditor identity, and frozen subject authors. Reject self-approval. A decision-changing failure must trigger the frozen affected-stratum census or explicit quarantine of its unaudited members. Missing capacity-relevant audit keeps the portfolio diagnostic and emits no finalists or reserves.

Record one of `support`, `qualify`, `contradict`, `unresolved`, `correct`, `supersede`, `quarantine`, or `reject`. Keep the decision effect separate. A reranking effect requires a matching ranking revision before portfolio use. Require independent search receipts and refuse claim-author self-approval.

## Correction authority

Use append-only `AuditCorrection` records for chemical identity, active-moiety mapping, claim statement, direction, human relevance, causal path, endpoint, candidate class, exposure, safety, and ranking features. A correction or supersession names a distinct replacement and retains the prior canonical value and hash. Quarantine and rejection carry no hidden replacement.

Call `apply_audit_corrections` to build a current overlay. It retains the original snapshot and the complete parent-linked chain, refuses parallel replacement, validates every prior-value hash, and never rewrites the source record. Bridge identity/claim/path corrections to the narrower `v7_deep_evidence.RecordCorrection` package hook when constructing a revised deep package; do not delete either history.

The production adapter persists the original deep frame, append-only correction ledger, original record/value hashes, distinct replacements, and one revised deep record per candidate. A correction-bearing audit must use its typed action and ranking revision. Replay with reordered outcomes is a no-op; changed plan or outcome content under one audit revision is a conflict.

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

The persisted aggregate returns reconciled audit coverage, seven decision outputs, council records, evidence-strength ranking, novelty/information-value ranking, diversified portfolio ranking, one disposition per deep candidate, finalists, reserves, and canonical order. It records before/after therapeutic-support and evidence-quality hashes so novelty or diversity cannot rewrite either. Reject benchmark labels, expected outcomes, holdout state, or partition labels anywhere in the live plan, retained retrievals, corrections, council records, or aggregate.
