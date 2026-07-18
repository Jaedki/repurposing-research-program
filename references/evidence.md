# Living Evidence and Search Contract

## Evidence records

Treat `scripts/program_contract.py` as the only authoritative field/schema definition. Store atomic claims in `claim_ledger.jsonl` and relations in `evidence_graph.jsonl`. Each claim cites verified source IDs. Each edge cites claim IDs. Preserve uncertainty and use `contrary_*` and `supersedes_*` links so later evidence can add, correct, qualify, or contradict earlier relations without erasing provenance.

Scientific audit status is distinct from structural validation. The controller structurally and provenance-validates every staged record before canonical integration. Claims and edges remain `unreviewed` unless independently checked. Every claim used by a final candidate path must have an `audit_records.jsonl` verdict and a corresponding explicit scientific audit status.

Use the exact `human_relevance` and claim-direction values supplied in each packet's `controlled_values`. Do not put prose in an enum field; cohort, endpoint, and transfer nuance belongs in `scope`, `statement`, uncertainty, and audit rationale. Malformed semantic values fail staged validation and are never converted into evidence caps.

## Source acquisition

Search primary literature and authoritative gene, disease, pathway, chemistry, pharmacology, safety, and clinical resources. Use the available individual life-science source skills where useful. Retrieve in stages: identifiers/counts, compact screening records, then targeted original-content verification.

Keep raw JSON, XML, HTML, full text, and bulky database payloads under `raw_sources/`. Normalize source-specific payloads, then pass each page through `compact_source_payload.py --query-id <id>`. Use `build_search_record.py` to bind query identity, receipt pages, continuation hashes, executor, source IDs, and produced claims or observations. Search records must cover every predeclared family and state a substantive closure basis; source count or elapsed time never proves completion.

Store one source row per normalized canonical identifier. When another unit independently rediscovers the work, reuse its source ID and aggregate `discovered_by_units`, `discovery_query_ids`, and supported claims; keep each query's receipt provenance. A fragment, article section, or purpose-specific excerpt belongs in claim or verification scope, not after `#` in a canonical identifier. Decisive audit queries must retrieve from external primary or authoritative resources, use distinct verification and counterevidence queries, and give each claim a specific rationale.

## Exact compounds

Candidate observations and merged records must represent a discrete chemical with an authoritative identifier, verified identity source, and either an InChIKey or canonical-SMILES SHA-256 identity key. Record an active-moiety key with an explicit rationale and verified mapping sources, separately from exact formulation structure keys. Merge scientifically equivalent salts by active moiety when justified, retaining every formulation structure, observation, and formulation-starting evidence path.

Exclude classes, extracts, mixtures, diets, genetic interventions, assay conditions, targets, pathways, and controls. Keep independent discoveries separate in `candidate_observations.jsonl`; merge them only by exact structure into `candidate_records.jsonl`.
