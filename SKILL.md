---
name: repurposing-research-program
description: "Run a source-backed, agent-assisted programme to identify existing drugs for a supplied genetic disease and optional gene."
---

# Repurposing Research Program

Identify existing drugs whose established mode of action could plausibly reverse, compensate for, slow, or ameliorate a specific evidence-backed element of the supplied disease pathology. A prior literature association between the drug and the disease is not required.

Treat every result as an experimental hypothesis, never clinical advice or proof of efficacy.

## Run the programme

Run commands from this skill folder, or use absolute script paths.

1. Initialize a run. Supply the exact MONDO ID when known:

   ```powershell
   python scripts/orchestrate_program.py init <run-folder> --disease "<disease>" [--gene <gene>] [--mondo MONDO:...]
   ```

2. Call `next`. Python performs any ready deterministic controller work, then writes exactly one content packet and returns its path and worker prompt.

3. Give one research agent only that packet. The agent writes one JSON result matching `result_contract`. Submit it:

   ```powershell
   python scripts/orchestrate_program.py submit <run-folder> <result.json>
   ```

4. Repeat `next -> agent -> submit`. The controller progresses through:

   - pathology-only Monarch and DisMech ingestion;
   - one deep pathology-research packet per selected node;
   - a frozen, content-addressed living evidence graph;
   - one mechanism-directed candidate-seed packet per eligible pathology node;
   - one evidence-review packet per canonical candidate ID;
   - independent audit and deterministic ranking.

5. When status is `ready_to_build`, run:

   ```powershell
   python scripts/orchestrate_program.py build <run-folder>
   ```

Use `status` at any time. Resume means calling `next`; accepted results are immutable and content-addressed. Lean orchestration is not a limit on research depth: workers should investigate each packet as deeply as the evidence permits.

## Hard boundaries

- Pathology construction is treatment-blind. Pathology packets must not contain drug, compound, treatment, therapeutic, or candidate fields.
- Candidate generation starts only after every pathology-node result is accepted and Python freezes the graph snapshot.
- Candidate eligibility follows `pathology element -> desired biological change -> established drug mode of action`. Direct disease-drug literature is optional.
- Monarch associations are pathology-category allowlisted. DisMech treatment content is excluded before packet construction.
- Workers create research content only. Python controls source receipts, task order, item cursors, packet lineage, validation, persistence, candidate aggregation, ranking order, and outputs.

## Evidence safeguards

- Preserve source IDs, exact identity, contradictions, negative results, unresolved identity, exclusions, and explicit gaps.
- Every graph assertion cites retained pathology sources.
- Every candidate separately cites pathology evidence and drug mode-of-action evidence.
- Never persist API keys, access tokens, authorization headers, or secrets.
- Placebo, vehicle, and sham are comparators, not candidates.
- Unresolved identity stays visible and may be reviewed; it must not silently erase the programme.
- `complete` requires at least one audited eligible candidate and verified hashes for all accepted results and outputs.

Read [architecture.md](references/architecture.md) for ownership, [packet-contract.md](references/packet-contract.md) for worker results, and [source-adapters.md](references/source-adapters.md) before changing source ingestion.

At handoff report the status, retained source count, pathology profile and assertion counts, raw and deduplicated candidate counts, material gaps, and the experimental-use policy.
