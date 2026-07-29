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

3. Start one new agent per packet with only this `SKILL.md`, the returned worker prompt, the current packet, and any read-only graph context explicitly retrieved through that packet—never prior packet content or a previous worker thread. Research workers investigate to evidence saturation with primary or authoritative sources and return in `records.documents` only the canonical documents directly cited by submitted claims, counterclaims, identity decisions, or limitations. The final auditor instead uses only the retained corpus supplied in its packet and returns no new documents. Every agent writes one JSON result matching `result_contract`; submit it unchanged:

   ```powershell
   python scripts/orchestrate_program.py submit <run-folder> <result.json>
   ```

   If validation rejects the result, discard it and give the same packet to another new agent; do not repair research JSON in the controller.

4. Repeat `next -> new agent -> submit` in the visible controller chat. Do not replace this loop with a persistent or background supervisor. The controller progresses through:

   - deterministic Monarch and DisMech source screening, followed by one compact
     pathology-source sentence adjudication packet when free text is flagged;
   - pathology-only source normalization after Python applies the complete adjudication;
   - one constrained curation packet that partitions every non-anchor source node into run-local research, context-only, or excluded concepts;
   - one deep pathology-research packet per curated research concept;
   - a frozen, content-addressed living evidence graph;
   - one mechanism-directed candidate-seed packet per researched pathology concept, with a compact frozen-graph index and read-only context lookup;
   - deterministic UniChem lookup for every raw candidate seed, followed by one identity-review
     packet covering every exact conflict, connectivity-only match, unsupported identifier, and
     no-result seed;
   - one evidence-dossier packet per pathology concept after candidate identity resolution;
   - one closed-corpus independent audit that partitions every reviewed candidate into a scored
     assessment or a bounded, cited exclusion;
   - deterministic raw-score calculation and ranking by Python.

5. When status is `ready_to_build`, run:

   ```powershell
   python scripts/orchestrate_program.py build <run-folder>
   ```

Use `status` at any time. Resume means calling `next`; accepted results are immutable and content-addressed. Lean orchestration is not a limit on research depth: workers should investigate each packet as deeply as the evidence permits.

## Hard boundaries

- Pathology construction is treatment-blind. The isolated source-adjudication packet is the
  only packet allowed to contain flagged source sentences; it cannot create nodes or propagate
  into pathology context. All subsequent pathology packets must not contain drug, compound,
  treatment, therapeutic, or candidate fields or interpretations.
- Candidate generation starts only after curation and every required pathology-concept result are accepted and Python freezes the graph snapshot.
- Candidate eligibility follows `pathology element -> desired biological state -> established drug mode of action`.
- Monarch associations are pathology-category allowlisted. DisMech treatment-oriented sections
  and fields are excluded unconditionally. Flagged free text is batched once, classified without
  search or rewriting, and retained only when the complete sentence is adjudicated pathology-only.
- Workers create research content only. Python controls source receipts, task order, item cursors, packet lineage, validation, persistence, candidate aggregation, ranking order, and outputs.

## Evidence safeguards

- Preserve source IDs, exact identity, contradictions, negative results, unresolved identity, exclusions, and explicit gaps.
- Search results and snippets are transient. When using [Asta](https://allenai.org/asta/resources/mcp), search narrowly, inspect snippets, select and verify the underlying paper, cite its canonical ID, and return only that paper; do not propagate search output.
- Every graph assertion cites retained pathology sources.
- Candidate evidence reviews use the frozen graph as disease context, retrieve primary or authoritative drug facts, and build cited dossiers without scoring, ranking, or excluding candidates.
- The independent audit reads and weighs the retained evidence rather than restating a review. It may not search, add evidence, or send a candidate for re-review.
- Long or uncertain hypotheses remain rankable with explicit reservations. Audit exclusion is limited to established exact-disease use, exact-disease human intervention, unsupported or opposite proposed action, demonstrated impossibility of relevant action or exposure, or an entity established not to be a repurposable drug or administered intervention. The audit packet supplies exact definitions; uncertainty or missing data never establishes an exclusion.
- Each assessed candidate receives four cited component scores of 5, 10, 15, or 20: drug-action confidence, disease-mechanism relevance, mechanistic-bridge plausibility, and translational feasibility. The audit packet supplies category-specific anchors for every level. Counterevidence never earns points: it lowers each component whose premise it directly challenges and otherwise remains an unscored reservation. Python sums the four components without weighting to a transparent score out of 80 and applies deterministic dense ranking.
- Never persist API keys, access tokens, authorization headers, or secrets.
- Placebo, vehicle, and sham are comparators, not candidates.
- Unresolved identity stays visible and may be reviewed; it must not silently erase the programme.
- Exact UniChem UCI matches merge automatically. Connectivity-only matches, conflicting
  identifiers, unsupported candidates, and no results are interpretive identity work and never
  imply either equivalence or uniqueness.
- `complete` requires at least one audited, scored candidate and verified hashes for all accepted results and outputs.

Read [architecture.md](references/architecture.md) for ownership, [packet-contract.md](references/packet-contract.md) for worker results, and [source-adapters.md](references/source-adapters.md) before changing source ingestion.

At handoff report `source_edges` as source edges and researched `assertions` as assertions; never use either term for the other. Before pathology-node research, the assertion count is zero. Also report the status, retained source count, pathology profile count, raw and deduplicated candidate counts, material gaps, and the experimental-use policy.
