# Deep evidence and authoritative identity

Use `scripts/v7_deep_evidence.py` only after a schema-v7 `ScreenedCandidateRecord` exists and original source payloads have been retained. This module is an immutable, in-memory model. It does not retrieve sources, screen candidates, rank hypotheses, define scientific-audit policy, build final outputs, persist ledgers, or schedule work.

## Deep grounding

Create one `DeepSourceRecord` for each retained payload. Record the stable retrieval-content receipt, payload locator, raw-byte SHA-256, content scope, retrieval method, and verification method. A metadata or abstract receipt remains useful provenance but cannot verify a decision-relevant deep claim.

Create claim-specific `EvidenceSpan` records. Use either:

- an exact excerpt with a stable locator; or
- a table/figure pointer containing the artifact label, page or section, coordinates, cell or region, extracted value, and extraction method.

In `ORIGINAL_CONTENT_REQUIRED` mode, supply the retained payload bytes. Validation recomputes the raw hash and proves the excerpt or structured pointer against those bytes. There is no trusted `content_verified` boolean. A true flag, a receipt ID, or a standalone hash never substitutes for original content plus a verifiable support span.

Build an atomic claim before its evidence. Its ID binds the proposition, polarity, reported-versus-inferred state, modality, exact case/population/stage/tissue/dose/route/time/endpoint scope, calibration, and uncertainty. Every deep evidence record is bound to exactly one claim ID and one span carrying that same claim ID; a span for another proposition cannot be reused silently.

`DeepEvidenceRecord` stores source/span IDs, exact support, raw-content hash, retrieval and verification methods, study design, population or model, sample size, comparator, dose, route, duration, tissue/cell type, exposure/concentration, endpoint, effect direction and magnitude, statistical uncertainty, limitations, risk of bias, and claim calibration. Use typed `not_reported` or `not_applicable` values. Never infer a numeric effect, interval, p-value, dose, or sample size from prose.

## Authoritative identity normalization

Treat normalization as deterministic and provenance-backed, not infallible. Preserve every source seed and every registry assertion. A regex-valid InChIKey is only syntax; it cannot establish deep identity.

For a single chemical entity, require at least two independent authoritative source payloads whose exact spans agree on:

- entity kind;
- authority-reported canonical SMILES;
- standard InChI and full InChIKey;
- stereochemistry status and descriptor.

Hash the exact canonical-SMILES serialization with SHA-256 and retain the canonicalization method/version. Conflicting authority facts produce `conflicting`; a lone assertion, missing structure, or decision-relevant unspecified stereochemistry produces `unresolved`. Neither can be promoted. Never generate a structure to fill an unresolved seed.

Apply these form policies:

- **Salts and solvates:** retain distinct normalized-intervention identities and exact structures. They may share a breadth group with a parent only through an explicit grounded active-moiety mapping. Form-, route-, exposure-, dose-, and safety-specific evidence stays with the tested form.
- **Stereoisomers/enantiomers:** retain distinct identities and breadth groups. Unspecified or partially specified decision-relevant stereochemistry blocks deep promotion.
- **Tautomers:** retain the asserted structure and a grounded `tautomer_of` relationship. Group only under an explicitly versioned canonicalization policy with agreeing authorities; never transfer evidence automatically.
- **Prodrugs and active metabolites:** retain distinct identities and breadth groups with grounded typed relationships. Active-moiety linkage does not transfer efficacy, safety, route, dose, or exposure evidence.
- **Fixed combinations, standardized preparations, and formulations:** retain exact product/preparation identity, component IDs, amounts/fractions, dosage form, release characteristics, and routes. Component evidence is not product evidence without a separate grounded applicability claim.
- **Mixtures:** only exact, composition-defined mixtures can resolve. Undefined extracts or mixtures retain their seeds and raw names with `unresolved`; they receive no invented single structure or active moiety.

Record source-and-span provenance separately for compound origin, human-use status, indication/jurisdiction/as-of approval or development status, and active-moiety mappings. Human use is not approval; approval is indication-, jurisdiction-, product-, and time-specific; chemical-universe membership is neither.

## Deep promotion and later correction

Call `promote_deep_candidate` only with retained payload bytes. Promotion requires a resolved authoritative identity, original-content verification, active claim-specific evidence and paths, explicit endpoint assessments, and provenance for origin, human-use status, and approval/development status. Exact salts, solvates, prodrugs, metabolites, and formulations also require active-moiety provenance.

Use append-only `RecordCorrection` records to let a later deep audit supersede or quarantine an identity, claim, path, source, or evidence span. Retain the old record. A supersession names a distinct replacement and cannot form a cycle; a quarantine has no replacement. Current identity, claims, and paths cannot depend on corrected or quarantined source support. This is a correction mechanism only, not scientific-audit sampling or decision policy.

## Persisted production aggregation

Use `V7ScreenDeepAdapter` in `scripts/v7_production_screen_deep.py` after Stage 4. A completed deep result must use the exact frozen screened-candidate record and bridge the admitted exact intervention to the independently normalized deep identity by exact structure/stereochemistry or exact product/formulation evidence. Structure comparison includes the stereochemistry descriptor. Structureless composition uses a content-derived component bridge ID over entity kind plus qualified `NAMESPACE:identifier`, then requires exact role and amount/fraction equality; a formulation additionally requires the same authoritative product identifier, retained product name, dosage form, release characteristic, routes, and component IDs. Deep component and formulation source/span provenance is verified against retained original content. The bridge forbids automatic evidence transfer.

Retain original payload bytes, claims and spans, structured study/effect projections, endpoint-specific counterevidence search status, one applicability assessment per evidence record across every transfer axis, explicit missingness, nonempty structured safety/exposure, all derived decision dimensions, and the separate pre-audit orders. Broken retained content, hidden counterevidence, ungrounded transfer, exact-form drift, missing safety/exposure, or an invented effect fails completed-package validation.
