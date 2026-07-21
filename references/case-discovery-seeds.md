# Schema-v7 case, discovery, and seed funnel

Use this contract from case initialization through lightweight screening. Typed definitions in `v7_case_model.py`, `v7_discovery.py`, and `v7_seed_funnel.py` are authoritative.

## Case gate

Create one immutable case revision retaining original input, field provenance, gene disease-state and desired modulation separately, disease/subtype, population, tissue, stage, target-product profile, contraindications, exclusions, and a coded endpoint portfolio. A missing or ambiguous decision field remains typed `unknown` or unresolved. A blocking ambiguity or absent supplied endpoint keeps the case at `needs_resolution` and prevents seeds.

The case initializer owns only the root case artifacts. The runtime owns only `runtime_v7/`; neither may rehash or rewrite the other's artifacts.

## Factorized discovery

Keep these dimensions independent:

- causal route: the seven `CausalRoute` values;
- evidence modality: the ten `EvidenceModality` values;
- chemical universe: the seven `ChemicalUniverse` values;
- development status;
- endpoint; and
- typed uncertainty.

Do not create a mixed-axis v7 perspective. Natural origin is not a causal route; genetics is not clinical evidence; novelty is an output.

Build the broad case model as separate disease-mechanism, directional-target, phenotype/signature, tissue/cell, substrate/metabolite, compensatory-node, contraindicated-mechanism, endpoint-mapping, unresolved-direction-conflict, and pharmacology-seed collections. Each collection has one owner. Retain every pharmacology mapping with source release, native record, locator, query, content receipt, and evidence.

Represent causal routes structurally with case revision, intervention, controlled route/action/direction, disease-state node, intervention target, intermediate state, endpoint, and evidence IDs. Deduplicate normalized topology while unioning evidence. Never use prose markers or substring tests.

## Seed admission

Create one immutable seed for every eligible source assertion before therapeutic plausibility, evidence depth, novelty, safety, exposure, readiness, or publication-volume filtering. Query overlap adds route lineage to the same source assertion; it does not create another seed. Zero or small truthful universes are valid.

Keep raw source identity, aliases, structural routes, modality, universe, development hint, endpoint, uncertainty, mapping, query, and retrieval-content receipt. Preliminary identity is seed-scoped and visibly unverified. Do not require active-moiety resolution, human use, human evidence, a full safety profile, a score, or audit at seed depth.

## Identity, disposition, and screening

Give every seed exactly one canonical disposition: `admit`, `merge`, `baseline`, `reject`, `quarantine`, or `failed`. A merge preserves the seed and points acyclically to one admitted or baseline representative. Technical failure is not scientific rejection. Decision-changing one-to-many identity ambiguity is quarantined.

Use verified authority evidence for chemical equivalence. Do not collapse names, salts, solvates, stereoisomers, prodrugs, metabolites, formulations, mixtures, or preparations without the typed policy and evidence applicable to that relationship.

Call `V7DispositionAdapter.normalize_and_dispose(case_revision, seeds, frozen_resolver_assertions)` for the persisted whole-case Stage 4 reduction. Resolver sources, exact identity assertions, case-role results, raw reported identity, and input hashes remain in the aggregate. Identity fingerprints exclude raw names. Conflicting authority content defeats a majority and quarantines; missing resolver results become technical failures. Identical replay is a no-op, while changing seeds or resolver content under one resolver revision fails.

The aggregate emits normalized interventions, active moieties and their typed exact-form relationships, breadth groups, one identity resolution and disposition per seed, direct merge links, unresolved/conflicting identity records, complete lineage, and the resolved-all/admitted/baseline/admitted-breadth/active-moiety denominators. Salts, hydrates, solvates, and exact formulations remain distinct intervention identities even when evidence-backed active-moiety roll-up gives them one breadth group. Stereoisomers, prodrugs, active metabolites, combinations, preparations, isotopes, and conjugates retain distinct intervention identities and breadth groups. Active-moiety linkage never transfers evidence automatically.

Give every admitted representative one lightweight screen record. Assess every case endpoint explicitly; a required applicable endpoint cannot be `not_assessed`. Preserve preclinical-only hypotheses in a visible stratum. Keep baseline care outside repurposing ranks.

## Reconciliation

Require these equations before the relevant stage passes:

- `N_seed = N_admit + N_merge + N_baseline + N_reject + N_quarantine + N_failed`
- `N_admit = N_screened + N_screen_rejected + N_screen_quarantined + N_screen_failed`
- `N_screened = N_selected_deep + N_screen_only`
- `N_selected_deep = N_deep + N_deep_quarantined + N_deep_failed`

Report resolved-all, admitted, baseline, admitted breadth-group, and active-moiety identity denominators separately. `N_identity_admitted` equals `N_admit`. Never substitute physical line counts or pad the conditional breadth aspiration.
