# Content packet contract

`next` writes one packet. Item packets include a stable `item_id`; global packets use `null`. Every packet contains the case, one task, hashes of accepted canonical barriers, only the task context, exact result fields, and evidence rules.

The worker returns one JSON object:

```json
{
  "stage": "pathology_node_research",
  "item_id": "NODE-...",
  "packet_id": "PACKET-...",
  "status": "complete",
  "records": {},
  "gaps": [],
  "notes": []
}
```

## Agent task collections

1. `pathology_curation`: `concepts`; one packet partitions every supplied non-anchor source node exactly once.
2. `pathology_node_research`: `documents`, `profiles`, `assertions`.
3. `candidate_seed_research`: `documents`, `candidates`, `exclusions`.
4. `candidate_identity`: `documents`, `identity_groups`; one global packet partitions every UniChem-flagged or unresolved seed exactly once. It is skipped deterministically when the queue is empty.
5. `candidate_review_research`: `documents`, `reviews`; one packet contains the identity-resolved candidates assigned to one pathology concept and requires exactly one review per supplied candidate.
6. `audit_and_rank`: `rankings`, `audit_notes`.

Python creates `pathology_sources`, the frozen `evidence_graph`, UniChem-enriched `candidate_seed_generation`, and aggregated `candidate_review` results. The accepted curation partition replaces automatic text clustering.

Research `document_id` values use a canonical PMID, PMCID, DOI, authoritative database accession, or HTTPS URL. Invented `DOC-AUTHOR-YEAR` aliases are rejected.
Python keeps one document per canonical ID, enriches scalar metadata in controller order, and unions list metadata without duplicates.

## Pathology records

- Monarch and DisMech nodes are disease-specific claims with a typed biological level and retained sources.
- Non-node DisMech material is retained in `disease_context`. It informs curation and compact shared disease context without creating independent research tasks.
- A curated concept chooses one member source-node ID as its run-local `concept_id`, retains member IDs and aliases, and uses one of `driver`, `mechanism`, `phenotype`, or `context`. Nodes merge only when they express the same claim at the same causal level; shared identifiers or biological relationships are not equivalence. Same-label gene-level disease claims may merge across sources, while mutation-, variant-, repeat-, model-, and mechanism-specific claims remain separate. True duplicates remain as members of the retained concept, and Python rejects duplicate retained type-label pairs. Python requires an exact partition and does not perform fuzzy matching.
- Only `research` concepts receive deep work. Each `context_only` concept names at least one research concept in `related_concept_ids`; Python retains it in the frozen graph and creates explicit context edges without a separate research packet. `exclude` concepts remain visible in the curation artifact but do not enter the graph.
- Each researched concept returns one detailed profile covering normal and pathological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps.
- `records.documents` must retain at least one researched source; supplied source metadata alone is not deep node research.
- Assertions link existing source-derived node IDs and cite retained documents.
- Repeated assertion IDs must retain the same subject-relation-object identity; Python unions their sources and unique evidence summaries.
- Pathology nodes, edges, profiles, and assertions cannot contain treatment or candidate fields.

## Candidate records

Each seed packet contains one frozen researched pathology concept. Each candidate states a desired biological change, links to that concept, and carries:

- exact or explicitly unresolved identity;
- frozen graph node IDs;
- `pathology_source_ids`;
- `mechanism_source_ids` supporting the drug's action.

No direct disease-drug citation is required. Workers retain every authoritative candidate identifier found. Python submits every supported identifier to UniChem, automatically merges only exact UCI matches, and queues every connectivity-only match, conflicting or partial mapping, unsupported identifier, and no result. A no-result record is unresolved, never evidence of uniqueness. The identity reviewer receives the entire queue plus a compact exact-resolved index, so possible aliases are not selected by name heuristics. Same-name matching never merges candidates automatically.

UniChem identifiers use their native database values under `chembl`, `drugbank`, `gtopdb`, `chebi`, `unii`, `pubchem_cid`, `drugcentral`, `inchi`, or `inchikey`. Other identifier types remain visible but are not rewritten or submitted speculatively.

The identity reviewer may attach queued seeds to an exact supplied `UNICHEM:<uci>` candidate or partition them into new resolved, unresolved, or conflicting groups. Seeds sharing one exact UCI are an indivisible identity block even when that block enters review because of a connectivity relationship. Each group cites newly retained authoritative identity evidence. Python validates complete, non-overlapping queue coverage and constructs the final candidate records without rewriting pathology or mechanism evidence.

Review packets retain the assigned candidates and all of their linked frozen pathology concepts and profiles, including complete cross-concept provenance, but include document metadata only for the candidates' drug-mechanism sources. Pathology citations remain attached to the frozen graph and candidate records without duplicating other graph sections or the full pathology source library into every review packet. Workers verify drug facts with primary or authoritative sources, map them to the supplied pathology, and make every review cite at least one document retained in its result. Disease-specific drug literature is a bounded, secondary prior-art check reported only when decision-changing.

Only `status=complete` results are accepted. Operational failure is not scientific content: fix or rerun the worker and submit again. Conflicting replacement of an accepted result is rejected.
