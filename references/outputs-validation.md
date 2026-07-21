# Schema-v7 outputs and validation

This reference is generated from `scripts/v7_output_contract.py` and describes only the user-facing projection and structural validation boundary. The typed definitions and reducers remain authoritative.

## Output procedure

Build outputs only from a canonical committed snapshot. Run the focused validators before writing, write into `outputs_v7/` atomically, then validate artifact hashes and row counts through the same public `scripts/validate_program.py <run-folder>` entry point. A diagnostic partial may expose preserved records and gaps, but it must not claim complete portfolio status.

Every human-readable file must state bounded scope, material gaps, hypothesis-generation status, and expert-review intent. Benchmark results are a separate post-run join and never enter these artifacts.

## Artifact contract

| Artifact | Media type | Cardinality | Content |
|---|---|---|---|
| `source_universes_and_coverage.csv` | `text/csv` | one row per declared query plan | Declared source universes, query plans, bounded coverage states, denominators, and gaps. |
| `candidate_seed_universe.jsonl` | `application/x-ndjson` | one row per canonical seed ID | Every immutable candidate seed with source, route, identity, and disposition lineage. |
| `screening_and_disposition_funnel.csv` | `text/csv` | one row per canonical seed ID | Seed-to-screen-to-deep-to-portfolio transition projection. |
| `funnel_reconciliation.jsonl` | `application/x-ndjson` | one reconciliation record | All canonical funnel denominators and balanced equations. |
| `identity_normalization_and_merges.jsonl` | `application/x-ndjson` | one row per canonical seed ID | Seed identity decisions, exact interventions, breadth groups, active moieties, and merge representatives. |
| `unresolved_and_quarantined_seeds.csv` | `text/csv` | one row per unresolved or quarantined seed ID | Every unresolved or quarantined seed and its non-advancement reason. |
| `deeply_assessed_candidates.jsonl` | `application/x-ndjson` | one row per canonical deep candidate ID | Every completed deep candidate and its endpoint, evidence, safety, exposure, and identity package links. |
| `evidence_strength_ranking.csv` | `text/csv` | one row per canonical deep candidate ID | Post-audit evidence-strength order without novelty or diversity substitution. |
| `novelty_information_value_ranking.csv` | `text/csv` | one row per canonical deep candidate ID | Separate novelty and information-value order. |
| `diversified_portfolio_ranking.csv` | `text/csv` | one row per canonical deep candidate ID | Portfolio membership, diversified order, and decomposed marginal diversity. |
| `exclusions_and_reasons.csv` | `text/csv` | one row per explicit non-advancement decision | Scientific exclusions, baseline routing, screen-only decisions, failures, and non-selection reasons. |
| `candidate_evidence_cards.jsonl` | `application/x-ndjson` | one row per canonical deep candidate ID | Joined candidate evidence cards with separate decision dimensions and provenance. |
| `candidate_evidence_cards.md` | `text/markdown` | one section per canonical deep candidate ID | Concise expert-readable deep-candidate evidence cards. |
| `uncertainty_and_evidence_gaps.jsonl` | `application/x-ndjson` | one row per unique typed gap | Typed coverage, identity, evidence, endpoint, safety, exposure, and audit gaps. |
| `uncertainty_and_evidence_gaps.md` | `text/markdown` | one section per gap category | Concise expert-readable uncertainty and evidence-gap summary. |
| `audit_coverage_and_corrections.jsonl` | `application/x-ndjson` | one summary plus one row per audit assignment or correction | Audit denominators, assignments, outcomes, unaudited state, and append-only corrections. |
| `machine_readable_provenance.jsonl` | `application/x-ndjson` | one row per provenance subject | Snapshot, commit, ledger, contract, and emitted-artifact hashes. |
| `full_funnel_summary.md` | `text/markdown` | one run summary | Bounded-scope full-funnel summary with reconciliation and expert-review framing. |

`artifact_manifest.json` binds the output contract version, canonical snapshot hash, output status, reconciliation record, artifact SHA-256 values, byte sizes, and logical row counts.

## Reconciliation

- `N_seed = N_admit + N_merge + N_baseline + N_reject + N_quarantine + N_failed`
- `N_admit = N_screened + N_screen_rejected + N_screen_quarantined + N_screen_failed`
- `N_screened = N_selected_deep + N_screen_only`
- `N_selected_deep = N_deep + N_deep_quarantined + N_deep_failed`
- A complete portfolio requires `N_deep = N_finalist + N_reserve + N_not_selected + N_audit_rejected + N_audit_quarantined`.

Interim `unaudited`, `council_blocked`, or `selection_pending_additional_audit` records remain visible in a diagnostic partial and prevent complete output status. Report resolved-all, admitted, baseline, admitted breadth-group, and active-moiety identity denominators separately.

## Validation domains

| Module | Responsibility |
|---|---|
| `runtime` | Native case/runtime integrity, commit hashes, snapshot identity, and execution/scientific hash separation. |
| `retrieval_coverage` | Declared source/query coverage, receipt lineage, bounded closure, and source reconciliation. |
| `case_endpoints` | Normalized case readiness and complete endpoint-portfolio references. |
| `seeds_funnel` | Seed preservation, exclusive dispositions, merge links, screen/deep transitions, and equations. |
| `evidence` | Deep package, claim, source/span, endpoint, safety, exposure, and counterevidence links. |
| `identity` | Resolved identities, exact forms, breadth groups, active moieties, merges, and quarantine. |
| `ranking` | Separate evidence, novelty/information-value, and diversified portfolio orders. |
| `audit_council` | Audit denominators, unaudited state, corrections, council decisions, and portfolio dispositions. |
| `outputs` | Artifact inventory, row counts, hashes, summaries, and ledger reconciliation. |

Keep `scripts/validate_program.py` as the public CLI. It routes native schema-v7 runs to the focused `scripts/v7_validation/` modules and retains the historical schema-v6 implementation for compatible commands.
