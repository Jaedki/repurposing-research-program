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

1. `pathology_node_research`: `documents`, `profiles`, `assertions`.
2. `candidate_seed_research`: `documents`, `candidates`, `exclusions`.
3. `candidate_review_research`: `documents`, `reviews`; one packet contains the deduplicated candidates assigned to one pathology cluster and requires exactly one review per supplied candidate.
4. `audit_and_rank`: `rankings`, `audit_notes`.

Python creates `pathology_sources`, the frozen `evidence_graph`, its deterministic `mechanism_clustering` partition, aggregated `candidate_seed_generation`, and aggregated `candidate_review` results.

Research `document_id` values use a canonical PMID, PMCID, DOI, authoritative database accession, or HTTPS URL. Invented `DOC-AUTHOR-YEAR` aliases are rejected.
Python keeps one document per canonical ID, enriches scalar metadata in controller order, and unions list metadata without duplicates.

## Pathology records

- Monarch and DisMech nodes are disease-specific claims with a typed biological level and retained sources.
- Each node returns one detailed profile covering normal and pathological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps.
- `records.documents` must retain at least one researched source; supplied source metadata alone is not deep node research.
- Assertions link existing source-derived node IDs and cite retained documents.
- Repeated assertion IDs must retain the same subject-relation-object identity; Python unions their sources and unique evidence summaries.
- Pathology nodes, edges, profiles, and assertions cannot contain treatment or candidate fields.

## Candidate records

Each seed packet contains one frozen pathology cluster. Each candidate states a desired biological change, links only to relevant members of that cluster, and carries:

- exact or explicitly unresolved identity;
- frozen graph node IDs;
- `pathology_source_ids`;
- `mechanism_source_ids` supporting the drug's action.

No direct disease-drug citation is required. Workers use canonical authoritative candidate IDs where available; Python merges seeds with the same candidate ID before review and rejects identity conflicts.

Review packets retain the batch cluster's frozen pathology profiles and the assigned candidates, including their complete cross-cluster provenance, but include document metadata only for the candidates' drug-mechanism sources. Pathology citations remain attached to the frozen graph and candidate records without duplicating other graph sections or the full pathology source library into every review packet. Workers verify drug facts with primary or authoritative sources, map them to the supplied pathology, and make every review cite at least one document retained in its result. Disease-specific drug literature is a bounded, secondary prior-art check reported only when decision-changing.

Only `status=complete` results are accepted. Operational failure is not scientific content: fix or rerun the worker and submit again. Conflicting replacement of an accepted result is rejected.
