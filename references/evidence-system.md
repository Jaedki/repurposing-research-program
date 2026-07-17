# Living Evidence System

## Broad Retrieval

Use source tools directly: NCBI/PubMed and PMC for literature; WormBase/Alliance, Ensembl, UniProt and model resources for entities; Reactome, STRING and Open Targets for pathways/networks; ChEMBL, BindingDB, PubChem and ChEBI for exact compounds and pharmacology; other primary or authoritative sources when needed. Use individual life-science tools appropriate to the query, not a competing generic workflow.

Invoke the available individual `life-science-research` source skills as compact adapters where useful; the user does not need to tag that plugin separately. Fall back to primary-source web retrieval when a needed source has no adapter. Do not patch or depend on plugin-cache internals.

Retrieve in stages: identifiers and result counts; paginated compact title/abstract/MeSH screening; then targeted original-content verification for retained claims. Use the adapters' record limits as page sizes, never as scientific stopping rules. Continue pagination and query expansion until the unit's documented closure tests pass.

Save bulky raw JSON, XML, HTML, full text, and database responses under `raw_sources/` without returning their bodies to the agent. Prefer `scripts/fetch_source_payload.py` for supported PubMed and UniProt retrieval so fetching, raw persistence, normalization, and compaction happen before model context. Parse other markup with a source-specific adapter, preserving biological text and provenance. Pass every normalized discovery or verification record through `scripts/compact_source_payload.py --query-id <query_id>`; it emits a schema-2 receipt whose every record is bound to that query ID and has an immutable hash. Build search rows with `scripts/build_search_record.py` where possible. Link each retained source and search record to that receipt. The validator rejects missing receipts, cross-query receipt reuse, mismatched hashes, raw markup, nested abstract structures, mixed record types, unknown wrappers, and unknown source fields.

For each research unit, predeclare question branches, aliases, source families, inclusion/exclusion criteria, counterevidence searches, backward/forward citation trails, and stop tests. Discovery may retrieve hundreds or thousands of records. Report discovered, deduplicated, screened, acquired, original-verified, and claim-supporting counts separately. Every retrieval page has one compact receipt and one ordered `pagination_trace` entry. The trace starts without an input continuation, chains each output-token hash to the next input-token hash, and terminates without an output continuation. List `acquired_source_ids` and `original_verified_source_ids`; their exact cardinalities, rather than prose estimates, define the corresponding counts.

Every worker must execute the baseline families `primary_retrieval`, `counterevidence`, `backward_citation`, `forward_citation`, and `missing_branch`. Evidence units also execute `authoritative_database`. Compound-generating units additionally execute `exact_compound_literature`, `chemical_database`, `identity_verification`, and `negative_direction`. Every query records its executor and the controller `origin_job_id` whose staged result created it. Auditors run a separate missing-branch or counterevidence query under their own agent and audit-job IDs. A no-result family remains a completed, paginated, compact-receipt-linked search; it must not be padded with irrelevant sources.

## Source Corpus

Write one JSON object per canonical source in `source_corpus.jsonl`:

```text
source_id,canonical_identifier,identifier_type,title,year,source_kind,source_family,discovered_by_units,discovery_query_ids,metadata_verified,screen_decision,exclusion_reason,original_acquired,original_pointer,content_verified,verification_method,verification_scope,supported_claim_ids,compaction_receipt_path,compaction_record_hash
```

A search result, title, snippet, generated summary, or metadata match cannot support a claim. Discovery packets are for screening only. A claim-supporting source requires verified identity, acquired original content or authoritative record, and targeted content verification within an explicit scope. Abstract-only support must say so and cannot support details absent from the abstract.

## Claims And Graph

Write atomic claims to `claim_ledger.jsonl` with:

```text
claim_id,subtopic_id,claim,evidence_kind,source_ids,calibration,directionality,allele_relevance,scope_conditions,contrary_claim_ids,audit_status
```

Use only `established`, `supported_with_qualifier`, `plausible_inference`, `speculative`, `unresolved`, or `contradicted`. Direct findings and inference must be distinct.

Write graph edges to `evidence_graph.jsonl` with:

```text
edge_id,from_node,to_node,relation,direction,directionality_status,allele_mode_effect,claim_ids,audit_status
```

Use `directionality_status=supports_rescue`, `opposes_rescue`, or `ambiguous`. No unaudited claim or edge may drive a candidate, and only connected `supports_rescue` edges may enter a candidate causal path.

## Subtopic Registry

Write `subtopic_registry.jsonl` with:

```text
subtopic_id,parent_id,name,relation_to_case,depth,discovered_by,candidate_relevant,required_research_unit_ids,status,closure_reason
```

Register new subtopics whenever evidence reveals a material causal, compensatory, contradictory, phenotypic, pharmacological, or assay relationship. Do not hide complexity by folding a new relation into an existing summary. An audited later discovery may correct an existing subtopic or promote it from `candidate_relevant=false` to true; the controller reopens its evidence work, adds any newly required compound unit, and reruns closure.

## Completeness

Source counts are descriptive. Completion requires all planned query families, paginated screening, all discovered material subtopics, counterevidence, citation chaining, and an independently chosen missing-branch search to be resolved. Use `evidence_absent_complete` only when exhaustive searches found no usable evidence and the auditor confirms no searchable high-yield branch remains. It cannot produce candidates.
