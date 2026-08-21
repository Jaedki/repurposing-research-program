# UNC80 deficiency example run

This is the final, unmodified output bundle from a completed run of the repurposing research programme on 21 August 2026.

## Input

- Disease: UNC80 deficiency
- Gene: `UNC80`
- MONDO: `MONDO:0014777`
- Objective: identify existing drugs whose established mode of action could plausibly alter a specific evidence-backed element of the disease pathology

The machine-readable input is in [case.json](case.json).

## Result at a glance

The run retained 270 sources, built 15 pathology profiles and 7 researched assertions, generated 32 raw seeds, deduplicated them to 28 candidates, ranked 27 candidates, and recorded 1 audited exclusion. Ganaxolone ranked first with a score of 68; capsaicin, prucalopride, and perampanel shared the next rank with scores of 64.

Start with [outputs/summary.md](outputs/summary.md), then use:

- [outputs/candidates.csv](outputs/candidates.csv) for the complete ranking and scoring rationales
- [outputs/candidate_cards.md](outputs/candidate_cards.md) for readable evidence reports on every ranked candidate
- [outputs/citations.jsonl](outputs/citations.jsonl) for the canonical source records
- [outputs/graph.json](outputs/graph.json) for the frozen pathology and evidence graph
- [outputs/candidate_provenance.jsonl](outputs/candidate_provenance.jsonl) and [outputs/rescue_strategies.jsonl](outputs/rescue_strategies.jsonl) for the path from pathology to candidate nomination
- [outputs/candidate_exclusions.csv](outputs/candidate_exclusions.csv) and [outputs/seed_exclusions.jsonl](outputs/seed_exclusions.jsonl) for exclusions
- [outputs/manifest.json](outputs/manifest.json) for artifact hashes and run counts

The outputs are research hypotheses for prioritization. They are not clinical advice or evidence of efficacy.
