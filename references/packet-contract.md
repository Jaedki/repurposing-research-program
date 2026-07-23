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
3. `candidate_review_research`: `documents`, `reviews`.
4. `audit_and_rank`: `rankings`, `audit_notes`.

Python creates `pathology_sources`, the frozen `evidence_graph`, aggregated `candidate_seed_generation`, and aggregated `candidate_review` results.

Research `document_id` values use a canonical PMID, PMCID, DOI, authoritative database accession, or HTTPS URL. Invented `DOC-AUTHOR-YEAR` aliases are rejected.

## Pathology records

- Monarch and DisMech nodes are disease-specific claims with a typed biological level and retained sources.
- Each node returns one detailed profile covering normal and pathological state, causal role, granular mechanisms, cell types, anatomy, timing, upstream causes, downstream consequences, contradictions, uncertainty, and gaps.
- `records.documents` must retain at least one researched source; supplied source metadata alone is not deep node research.
- Assertions link existing source-derived node IDs and cite retained documents.
- Pathology nodes, edges, profiles, and assertions cannot contain treatment or candidate fields.

## Candidate records

Each candidate states a desired biological change, links to frozen graph nodes, and carries:

- exact or explicitly unresolved identity;
- frozen graph node IDs;
- `pathology_source_ids`;
- `mechanism_source_ids` supporting the drug's action.

No direct disease-drug citation is required. Workers use canonical authoritative candidate IDs where available; Python merges seeds with the same candidate ID before review and rejects identity conflicts.

Only `status=complete` results are accepted. Operational failure is not scientific content: fix or rerun the worker and submit again. Conflicting replacement of an accepted result is rejected.
