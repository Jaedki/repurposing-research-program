# Schema-v7 deterministic triage and ranking

Use `scripts/v7_triage_ranking.py` for schema-v7 scientific triage and pre-audit ordering. Do not import the schema-v6 integer rubric or caps from `scripts/ranking.py` or `scripts/program_contract.py`.

## Inputs and independence

Supply typed endpoint, claim/effect, study/model, risk-of-bias, identity, route, safety, exposure, development, and literature-landscape facts. Every evidence record needs ancestry metadata covering its source and any known cohort, laboratory, dataset, or common evidence ancestry. The reducer creates connected evidence clusters across those identifiers. Publication count is used only for novelty/underexploration; it never creates evidence independence, therapeutic support, or admission.

Treat `human_patient_cell` and human organoid/ex-vivo records as human-derived model evidence. Count clinical evidence only when the model is `human` and the study design is interventional or observational. Do not promote patient-cell evidence into the clinical dimension.

## Derived dimensions

Derive and retain these dimensions separately: therapeutic support, evidence quality, mechanistic coherence, human clinical evidence, human-derived model evidence, endpoint specificity, clinical translatability, exposure feasibility, safety/tolerability, repurposing readiness, novelty/underexploration, uncertainty, and information value. `DECISION_TABLES` and `DIMENSION_BANDS` are authoritative. A dimension ordinal is a deterministic ordering projection of its controlled band, never a worker-supplied score.

Assess every case endpoint. Declare one `primary_endpoint_id` for candidate-level therapeutic ordering and retain every secondary endpoint assessment. Required endpoints cannot be `not_assessed`.

Derive exposure from the exact intervention, reported dose and dose context, route constraints, target-tissue applicability, human PK basis, achieved concentration, required effect concentration, and computed concentration margin. Missing fields produce `unknown`; they never imply feasibility. Derive safety from structured adverse-event, contraindication, interaction, and population-risk records with severity, causality, frequency, case applicability, dose/route/duration/population, reversibility, and grounded source/span IDs. Missing safety evidence never means acceptable.

## Triage

Apply the ordered `TR-*` table. Emit exactly one of:

- `identity_follow_up`;
- `evidence_follow_up`;
- `deep_review`;
- `deferred_but_preserved`;
- `rejected_or_quarantined`, with `rejected` or `quarantined` and a controlled reason.

Do not admit or reject from a summed score. Preserve sparse or patient-cell-only hypotheses when information value warrants follow-up. Confirmed infeasible exposure, directly applicable serious safety mismatch, or an explicit prohibited scope may reject; conflicting safety/exposure quarantines. Identity ambiguity receives identity follow-up unless it is already quarantined.

## Separate pre-audit orders

Call `derive_and_rank_candidate_inputs` so decision profiles are rebuilt from typed inputs before ordering. Therapeutic-confidence ranking uses only therapeutic support, evidence quality, mechanism, human clinical evidence, human-derived model evidence, endpoint specificity, and uncertainty. It excludes readiness, novelty, and information value. Research-priority ranking uses information value and underexploration without changing therapeutic confidence. Both ranks are within explicit tiers and use candidate ID as the final deterministic tie breaker. They are pre-audit preparation; hand the frozen result to `v7_audit_portfolio.py` rather than treating it as a portfolio, finalist, reserve, council, or clinical recommendation.

## Expert assessments

Use `make_expert_assessment` only when a deterministic eligible dimension remains `unknown` or `insufficient`. The record must be source-grounded, schema-valid, content-addressed, cache-bound, and labeled `assessment_not_deterministic_fact`. Expert assessment cannot override therapeutic support, evidence quality, human clinical evidence, human-derived model evidence, exposure, safety, readiness, or uncertainty, and cannot bypass a triage blocker. A worker never mutates canonical state directly.
