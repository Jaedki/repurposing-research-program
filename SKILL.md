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

1. Start a fresh run unless the user explicitly asks to resume one created under the current
   objective and stage sequence. Choose its run-folder path before `init` and reuse that exact path
   in every controller command. Supply the exact MONDO ID if available; if not, indicate that the
   ID is unknown and proceed without it:

   ```powershell
   python scripts/orchestrate_program.py init <run-folder> --disease "<disease>" [--gene <gene>] [--mondo MONDO:...]
   ```

2. Call `next`. Python performs any ready deterministic controller work, then writes exactly one content packet and returns its path and worker prompt.

3. Start one new agent per packet with only this `SKILL.md`, the returned worker prompt, the current packet, and any read-only graph context explicitly retrieved through that packet—never prior packet content or a previous worker thread. Research workers investigate to evidence saturation with primary or authoritative sources and return in `records.documents` only the canonical documents directly cited by submitted claims, counterclaims, identity decisions, or limitations. Every returned document includes at least one concise `evidence_passages` entry with exact inspectable text and a locator. For PMID, PMCID, and DOI records, the controller retrieves and caches authoritative bibliographic metadata, rejects an ID/title mismatch, and supplies canonical publication identity in downstream source projections. The final auditor instead uses only the retained corpus supplied in its packet and returns no new documents. Every agent writes one JSON result matching `result_contract`; submit it unchanged:

   ```powershell
   python scripts/orchestrate_program.py submit <run-folder> <result.json>
   ```

   If validation rejects the result, stop and report the exact validation error. A rejected result
   is noncanonical and does not invalidate the run. Do not retry automatically or repair research
   JSON in the controller. If the user explicitly asks to continue, call `status` and start a new
   isolated worker for the same ready packet; do not start a fresh run unless the case or source
   inputs changed or the user requests one.

4. Repeat `next -> new agent -> submit` in the visible controller chat. Do not replace this loop with a persistent or background supervisor. The controller progresses through:

   - deterministic Monarch and DisMech source screening, followed by one compact
     pathology-source sentence adjudication packet when free text is flagged;
   - pathology-only source normalization after Python applies the complete adjudication;
   - one shallow, global `pathology_landscape_scan` that uses the host-configured Asta MCP tools
     to identify supported missing or overly broad pathology claims without deep research;
   - one constrained curation packet that partitions every non-anchor Monarch, DisMech, and
     projected Asta node into run-local research, context-only, or excluded concepts;
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

Use `status` at any time. To resume a run created under the current objective and stage sequence,
call `next` on its run folder. Start a fresh run after either contract changes. Accepted results are
immutable and content-addressed. Lean orchestration is not a limit on research depth: workers
should investigate each packet as deeply as the evidence permits.

## Hard boundaries

- Pathology construction is treatment-blind. The isolated source-adjudication packet is the
  only packet allowed to contain flagged source sentences; it cannot create nodes or propagate
  into pathology context. All subsequent pathology packets must not contain drug, compound,
  treatment, therapeutic, or candidate fields or interpretations.
- Candidate generation starts only after curation and every required pathology-concept result are accepted and Python freezes the graph snapshot.
- The Asta landscape scan is a single treatment-blind discovery packet between normalized sources
  and curation. It searches papers by relevance, inspects related citing papers, and runs
  paper-restricted snippet searches on every paper retained for evaluation. It follows
  `https://allenai.org/asta/resources/mcp` and cannot create final concept IDs, perform deep node
  research, or replace curation. A pending call is not terminated before 180 seconds; a retryable
  failure receives one minimal same-operation retry. Citation-endpoint failure is partial and does
  not suppress snippet evaluation of the original relevance paper.
- At curation, projected Asta additions join the same `source_nodes` collection and shared disease
  context as all other pathology claims; source origin is provenance, not a separate evidence tier.
- Candidate generation keeps `pathology element -> focal primary desired biological state -> established drug mode of action` as its main anchor. Secondary desired states and the phenotype objective remain context and do not create additional discovery routes by themselves. A supplied linked graph node may support a symptomatic or compensatory candidate only when its relationship to the focal concept and candidate hypothesis is mechanistically justified.
- Monarch associations are pathology-category allowlisted. DisMech treatment-oriented sections
  and fields are excluded unconditionally. Flagged free text is batched once, classified without
  search or rewriting, and retained only when the complete sentence is adjudicated pathology-only.
- Workers create research content only. Python controls source receipts, task order, item cursors, packet lineage, validation, persistence, candidate aggregation, ranking order, and outputs.

## Evidence safeguards

- Preserve source IDs, exact identity, contradictions, negative results, unresolved identity, exclusions, and explicit gaps.
- Search results, citation results, snippets, and raw MCP responses are transient. In the landscape
  scan, search by relevance, inspect related citing papers, and run paper-restricted snippet search
  on each paper retained for evaluation. Return only canonical papers cited by an actual proposal, each with an
  inspectable evidence passage. Return non-secret call receipts containing only operation metadata,
  outcomes, elapsed time, and result counts. Asta is unavailable only when relevance search fails
  both the standard and minimal attempts; endpoint-specific terminal failures remain explicit gaps
  and do not block valid Monarch/DisMech curation.
- Configure Asta in the MCP host with `ASTA_AI2_API_KEY`. The controller never reads that variable
  or persists keys, headers, authentication data, or raw MCP exchanges.
- Pathology research assertions are optional and may use only exact `node_id` values in the packet's `allowed_assertion_nodes`; keep newly researched mechanisms in the profile when they do not connect two allowed nodes.
- Curation keeps mechanisms atomic at one causal level and separates concept identity from research
  eligibility: concept distinctness does not create a research job, and researchability may not be
  deferred to deep research. A bare gene or gene-disease association, risk factor, model genotype,
  broad pathway, terminal outcome, or mutation label without supplied functional pathology is
  normally context-only. Generic gene and lesion-specific claims do not both create research routes
  unless each supplies a distinct intervention variable. Measurement-only biomarkers are context;
  a distinct modifiable phenotype may be research, and a biomarker-labelled causal process is
  classified by that mechanistic role rather than forced into context.
- Every graph assertion is keyed by its biological triple and retains cited evidence contexts for evidence type, model, stage, polarity, and summary; Python assigns the stable assertion ID.
- Candidate graph provenance contains only graph nodes and assertion IDs explicitly selected by the seed worker, with one non-duplicative graph rationale. Focal-profile-only hypotheses may select no assertion.
- Seed workers review every immediate focal neighbour, source edge, and researched assertion before
  searching, then retain only materially useful graph context. Candidate reviewers receive the exact
  selected assertions plus a bounded source-edge projection for the selected nodes and cited
  pathology sources; the full graph remains available to the audit.
- Candidate evidence reviews use the frozen graph as disease context, retrieve primary or authoritative drug facts, and build cited dossiers without scoring, ranking, or excluding candidates.
- The independent audit receives the frozen graph, candidate-identity result, dossiers, and retained source content. It reads and weighs that evidence rather than restating a review and may not search, add evidence, or send a candidate for re-review.
- Audit source integrity is decided at the exact point of use. Every source cited by a score component, net assessment, indexed alias, indexed reservation, or exclusion receives one concrete `supports`, `partly_supports`, `does_not_support`, or `contradicts` finding based on retained content. A generic integrity status or a direction to re-verify later is invalid; Python checks complete non-overlapping coverage, prevents publication aliases from being counted as independent support in one scope, and summarizes the results.
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
