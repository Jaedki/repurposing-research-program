---
name: repurposing-research-program
description: "Run a source-backed, agent-assisted programme to identify existing drugs for a supplied genetic disease and optional gene."
---

# Repurposing Research Program

Identify existing drugs whose established mode of action could plausibly reverse, compensate for, slow, or ameliorate a specific evidence-backed element of the supplied disease pathology. A prior literature association between the drug and the disease is not required.

Treat every result as an experimental hypothesis, never clinical advice or proof of efficacy.

## Run the programme

Run commands from this skill folder, or use absolute script paths.

Install the pinned runtime dependencies once with `python -m pip install -r requirements.txt`.

1. Initialize a run. Supply the exact MONDO ID if available; if not, indicate that the ID is unknown and proceed without it:

   ```powershell
   python scripts/orchestrate_program.py init <run-folder> --disease "<disease>" [--gene <gene>] [--mondo MONDO:...]
   ```

2. Call `next`. Python performs any ready deterministic controller work, then writes exactly one content packet and returns its path and worker prompt.

3. Start one new research agent per packet with only this `SKILL.md`, the returned worker prompt, and the current packet—never prior packet content or a previous worker thread. It researches to evidence saturation with primary or authoritative sources, retains those sources in `records.documents`, and writes one JSON result matching `result_contract`. Submit it unchanged:

   ```powershell
   python scripts/orchestrate_program.py submit <run-folder> <result.json>
   ```

   If validation rejects the result, discard it and give the same packet to another new agent; do not repair research JSON in the controller.

4. Repeat `next -> new agent -> submit` in the visible controller chat. Do not replace this loop with a persistent or background supervisor. The controller progresses through:

   - pathology-only Monarch and DisMech ingestion;
   - one constrained curation packet that partitions every non-anchor source node into run-local research, context-only, or excluded concepts;
   - one deep pathology-research packet per curated research concept;
   - a frozen, content-addressed living evidence graph;
   - one mechanism-directed candidate-seed packet per researched pathology concept;
   - deterministic UniChem lookup for every raw candidate seed, followed by one identity-review
     packet covering every exact conflict, connectivity-only match, unsupported identifier, and
     no-result seed;
   - one evidence-review packet per pathology concept after candidate identity resolution;
   - independent audit and deterministic ranking.

5. When status is `ready_to_build`, run:

   ```powershell
   python scripts/orchestrate_program.py build <run-folder>
   ```

Use `status` at any time. Resume means calling `next`; accepted results are immutable and content-addressed. Lean orchestration is not a limit on research depth: workers should investigate each packet as deeply as the evidence permits.

## Hard boundaries

- Pathology construction is treatment-blind. Pathology packets must not contain drug, compound, treatment, therapeutic, or candidate fields.
- Candidate generation starts only after curation and every required pathology-concept result are accepted and Python freezes the graph snapshot.
- Candidate eligibility follows `pathology element -> desired biological change -> established drug mode of action`. Direct disease-drug literature is optional.
- Monarch associations are pathology-category allowlisted. DisMech treatment-oriented sections and fields are excluded and remaining free text is treatment-redacted before packet construction.
- Workers create research content only. Python controls source receipts, task order, item cursors, packet lineage, validation, persistence, candidate aggregation, ranking order, and outputs.

## Evidence safeguards

- Preserve source IDs, exact identity, contradictions, negative results, unresolved identity, exclusions, and explicit gaps.
- Every graph assertion cites retained pathology sources.
- Candidate reviews use the frozen graph as disease context, retrieve primary or authoritative drug facts, and map verified pharmacology to that context. Disease-specific drug literature is secondary and reported only when decision-changing.
- Never persist API keys, access tokens, authorization headers, or secrets.
- Placebo, vehicle, and sham are comparators, not candidates.
- Unresolved identity stays visible and may be reviewed; it must not silently erase the programme.
- Exact UniChem UCI matches merge automatically. Connectivity-only matches, conflicting
  identifiers, unsupported candidates, and no results are interpretive identity work and never
  imply either equivalence or uniqueness.
- `complete` requires at least one audited eligible candidate and verified hashes for all accepted results and outputs.

Read [architecture.md](references/architecture.md) for ownership, [packet-contract.md](references/packet-contract.md) for worker results, and [source-adapters.md](references/source-adapters.md) before changing source ingestion.

At handoff report `source_edges` as source edges and researched `assertions` as assertions; never use either term for the other. Before pathology-node research, the assertion count is zero. Also report the status, retained source count, pathology profile count, raw and deduplicated candidate counts, material gaps, and the experimental-use policy.
