# Schema-v6 historical living evidence and search contract

Use this file only for native schema-v6 ledgers and workers. Schema v7 uses the typed discovery, retrieval, deep-evidence, and output references.

## Evidence records

Treat `scripts/program_contract.py` as the only authoritative field/schema definition. Store atomic claims in `claim_ledger.jsonl` and relations in `evidence_graph.jsonl`. Each claim cites verified source IDs. Each edge cites claim IDs. Preserve uncertainty and use `contrary_*` and `supersedes_*` links so later evidence can add, correct, qualify, or contradict earlier relations without erasing provenance.

Scientific audit status is distinct from structural validation. The controller structurally and provenance-validates every staged record before canonical integration. Claims and edges remain `unreviewed` unless independently checked. Every claim used by a final candidate path must have an `audit_records.jsonl` verdict and a corresponding explicit scientific audit status.

Use the exact `human_relevance` and claim-direction values supplied in each packet's `controlled_values`. Do not put prose in an enum field; cohort, endpoint, and transfer nuance belongs in `scope`, `statement`, uncertainty, and audit rationale. Malformed semantic values fail staged validation and are never converted into evidence caps.

## Source acquisition

Search primary literature and authoritative gene, disease, pathway, chemistry, pharmacology, safety, and clinical resources. Use the available individual life-science source skills where useful. Retrieve in stages: identifiers/counts, compact screening records, then targeted original-content verification.

Keep raw JSON, XML, HTML, full text, and bulky database payloads under `raw_sources/`. Normalize source-specific payloads, then pass each page through `compact_source_payload.py --query-id <id>`. Use `build_search_record.py` to bind query identity, receipt pages, continuation hashes, executor, source IDs, and produced claims or observations. Search records must cover every predeclared family and use exactly one terminal coverage state: `FOUND` or `NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH`. `NOT_YET_SEARCHED` is retained for planned coverage and prohibits completion. Source count or elapsed time never proves completion.

The authoritative contract maps synonym expansion, citation expansion, contradiction search, and adjacent-domain search to required query families. For compound units it also maps the current perspective's declared coverage areas to distinct required query-family IDs. A research unit reaches saturation only when all generic and lens-specific required families have terminal coverage, every considered frontier branch has a deterministic decision, the branch budget is respected, and `frontier_exhausted` is true. Sparse literature reaches saturation through exhaustive `NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH` records; never manufacture publications or enforce a publication count.

Frontier records state whether a causal route is distinct, human- or candidate-relevant, already covered, and material. Code derives the only permitted decision from those fields and the remaining branch budget. Thresholds, budgets, statuses, and decision enums live only in `scripts/program_contract.py`.

Store one source row per normalized canonical identifier. When another unit independently rediscovers the work, reuse its source ID and aggregate `discovered_by_units`, `discovery_query_ids`, and supported claims; keep each query's receipt provenance. A fragment, article section, or purpose-specific excerpt belongs in claim or verification scope, not after `#` in a canonical identifier. Decisive audit queries must retrieve from external primary or authoritative resources, use distinct verification and counterevidence queries, and give each claim a specific rationale.

## Exact compounds

Candidate observations and merged records must represent a discrete chemical with an authoritative identifier, verified identity source, and either an InChIKey or canonical-SMILES SHA-256 identity key. Record an active-moiety key with an explicit rationale and verified mapping sources, separately from exact formulation structure keys. Merge scientifically equivalent salts by active moiety when justified, retaining every formulation structure, observation, and formulation-starting evidence path.

Exclude classes, extracts, mixtures, diets, genetic interventions, assay conditions, targets, pathways, and controls. Keep independent discoveries separate in `candidate_observations.jsonl`; merge them only by exact structure into `candidate_records.jsonl`.

Every candidate observation uses the existing `rationale` field for its lens-specific route. It must begin with the exact route marker supplied in the current packet, demonstrate the required causal pattern, and include an explicit `Human-outcome bridge:` supported through the observation's emitted claims. Generic or wrong-lens narratives fail validation. Natural-compound observations require verified natural-product, endogenous, or nutrient origin after merge plus an independent causal route; origin alone never qualifies. Identical compounds may be observed by multiple perspectives, but route-marker-normalized duplicate narratives fail because convergence must represent distinct causal routes.
