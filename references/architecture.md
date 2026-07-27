# Architecture

## Fixed barriers

| Barrier | Owner | Completion condition |
| --- | --- | --- |
| `pathology_sources` | Python | Treatment-blind Monarch and DisMech nodes, shared disease context, receipts, versions, and raw hashes are retained |
| `pathology_curation` | One agent | Every non-anchor source node is assigned exactly once to a run-local concept marked `research`, `context_only`, or `exclude`; context is attached to retained research concepts and uncertain equivalence remains separate |
| `evidence_graph` | Python after item work | Every curated research concept has one accepted deep-research profile; Python projects retained concepts and source edges through the partition and freezes the graph snapshot |
| `candidate_seed_generation` | Python after item work | Every researched pathology concept has one accepted seed result; Python merges canonical candidate IDs |
| `candidate_review` | Python after item work | Every nonempty concept review batch has one accepted result covering each assigned canonical candidate exactly once |
| `audit_and_rank` | One independent agent, then Python | Every reviewed candidate has one audit record; Python applies the final order |

Within the three item barriers, `next` selects the first missing item from a stable sorted manifest. Candidate review reuses curated concept IDs as batch IDs. After deduplication, Python assigns each candidate once to a linked origin concept, breaking ties by concept ID. This is a deterministic cursor, not a general DAG, queue, scheduler, or agent-controlled handoff.

## Separation of evidence

The pathology phase may contain disease, gene, variant, molecular, biochemical, cellular, tissue, organ, anatomy, and phenotype records. It structurally rejects candidate, compound, drug, treatment, and therapeutic fields.

The graph becomes candidate input only after its immutable `snapshot_id` is written. Candidate evidence has two explicit parts:

1. `pathology_source_ids`: why this mechanism element belongs to this disease;
2. `mechanism_source_ids`: why the drug has the required biological action.

The chain is sufficient without a paper directly joining the drug to the disease.

## Ownership

Python owns order, source receipts, hashing, item cursors, immutable acceptance, curation coverage checks, cross-reference checks, treatment exclusion during pathology work, secret rejection, graph freezing, candidate aggregation, ranking order, and exports. The curation agent owns semantic equivalence and research-value judgment; research agents own research content. Sources own evidence. No worker may select the next task or declare the programme complete.

A run is derived from `case.json`, canonical `results/*.json`, item results under `results/items/`, cached source receipts, and the final output manifest. The manifest hashes every accepted result file.

## Status

- `needs_controller`: `next` can perform a deterministic source, merge, freeze, or aggregation action.
- `needs_agent`: `next` emits exactly one agent packet.
- `stopped`: a scientifically necessary collection is empty, with an explicit reason.
- `ready_to_build`: all barriers passed and at least one candidate survived audit.
- `complete`: outputs exist and match the manifest hashes.
