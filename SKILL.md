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

2. Call `next`. If a bounded deterministic source batch returns `needs_controller` with
   `controller_progress`, call `next` again. Otherwise Python writes exactly one content packet
   and returns its path and worker prompt.

3. Start one new agent per packet with only this `SKILL.md`, the returned worker prompt, the current packet, and any read-only graph context explicitly retrieved through that packet—never prior packet content or a previous worker thread. Research workers investigate to evidence saturation with primary or authoritative sources and return in `records.documents` only the canonical documents directly cited by submitted claims, counterclaims, identity decisions, or limitations. Every returned document includes at least one concise `evidence_passages` entry with exact inspectable text and a locator. For PMID, PMCID, and DOI records, the controller retrieves and caches authoritative bibliographic metadata, rejects an ID/title mismatch, and supplies canonical publication identity in downstream source projections. The final auditor instead uses only the retained corpus supplied in its packet and returns no new documents. Every agent writes one JSON result matching `result_contract`; submit it unchanged:

   ```powershell
   python scripts/orchestrate_program.py submit <run-folder> <result.json>
   ```

   If validation rejects the result, stop and report the exact validation error. A rejected result
   is noncanonical and does not invalidate the run. Do not retry automatically or repair research
   JSON in the controller. If the user explicitly asks to continue, call `status` and start a new
   isolated worker for the same ready packet; do not start a fresh run unless the case or source
   inputs changed or the user requests one.

4. Repeat `next -> new agent -> submit` as foreground actions. Do not replace this loop with a persistent or background supervisor. The controller progresses through:

   - deterministic Monarch and DisMech source screening, followed by one compact
     pathology-source sentence adjudication packet when free text is flagged;
   - pathology-only source normalization after Python applies the complete adjudication;
   - one shallow, global `pathology_landscape_scan` that uses the host-configured Asta MCP tools
     to identify supported missing or overly broad pathology claims without deep research;
   - one global `pathology_coverage_expansion` that gives the complete post-Asta index to the
     host-configured Undermind MCP service for one treatment-blind deep-search coverage challenge;
   - one constrained curation packet that partitions every non-anchor Monarch, DisMech, and
     projected Asta or Undermind node into run-local research, context-only, or excluded concepts;
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
  and curation. It runs one stable broad relevance search plus one to three short searches for
  broad or under-covered indexed concepts, screens compact search metadata, and evaluates no more
  than thirty unique originals. It completes each selected original and its citations within the
  current search cycle, keeps no cross-search pending-paper queue, uses completed receipts to avoid
  duplicates, and maintains a live mechanism index. Before searching, it builds an ordered register
  of distinct high-priority coverage gaps from the source-node index and coverage checklist; a gap
  is either a missing disease process or an indexed concept too broad to express one abnormal state.
  The broad cycle may resolve several gaps, and each focused cycle targets the highest-priority
  unchecked gap. After every complete cycle it updates the index, merges equivalent gaps, and marks
  the targeted gap as filled or refined, already covered, or searched but unresolved. Coverage is
  saturated only when every registered gap has been searched or merged into a searched equivalent
  and the updated index exposes no new distinct in-scope gap. A fixed paper count, an empty result,
  or a streak of duplicates alone never establishes saturation. If the search or thirty-original cap is
  reached with a gap left, it stops at the cap and reports that gap rather than claiming saturation.
  A material refinement must change the abnormal state, causal step or level, biological direction,
  or disease-relevant context enough to change the atomic pathology concept or its later desired
  biological state; a new model, assay, population, biomarker, or wording alone is not material.
  Packet wording about repeatedly non-novel papers means this coverage rule, not a numerical streak.
  It makes and inspects one Asta call at a time. For each retained original it retrieves at most
  three citing papers and runs one paper-specific snippet search per distinct paper. It follows
  `https://allenai.org/asta/resources/mcp` and cannot create final concept IDs, perform deep node
  research, or replace curation. A pending call is not terminated before 180 seconds; a retryable
  failure receives one minimal same-operation retry. Citation-endpoint failure is partial and does
  not suppress snippet evaluation of the original relevance paper; Asta is unavailable only when
  all attempted relevance searches fail their retries.
- At curation, projected Asta additions join the same `source_nodes` collection and shared disease
  context as all other pathology claims; projected Undermind additions join identically, and source
  origin is provenance rather than a separate evidence tier.
- The Undermind coverage expansion is one treatment-blind discovery packet after Asta and before
  curation. It challenges the complete post-Asta index with one comprehensive deep search, inspects
  the full ranked result, and reads decision-relevant papers in one native parallel batch of up to
  twenty. It returns only full-text-supported missing or materially refined atomic pathology
  proposals; reports, rankings, abstracts, goals, queries, and raw responses remain transient. The
  final curator alone decides splits, merges, identity, eligibility, and desired state. Service
  failure returns empty scientific collections and an explicit non-blocking gap.
- Candidate generation keeps `pathology element -> focal primary desired biological state -> established drug mode of action` as its main anchor. Secondary desired states and the phenotype objective remain context and do not create additional discovery routes by themselves. A supplied linked graph node may support a symptomatic or compensatory candidate only when its relationship to the focal concept and candidate hypothesis is mechanistically justified.
- Monarch associations are pathology-category allowlisted. DisMech treatment-oriented sections
  and fields are excluded unconditionally. Flagged free text is batched once, classified without
  search or rewriting, and retained only when the complete sentence is adjudicated pathology-only.
- Workers create research content only. Python controls source receipts, task order, item cursors, packet lineage, validation, persistence, candidate aggregation, ranking order, and outputs.

## Evidence safeguards

- Preserve source IDs, exact identity, contradictions, negative results, unresolved identity, exclusions, and explicit gaps.
- Search results, citation results, snippets, deep-search reports, rankings, and raw MCP responses are transient. In the landscape
  scan, search by relevance, inspect related citing papers, and run paper-restricted snippet search
  on each paper retained for evaluation. Return only canonical papers cited by an actual proposal, each with an
  inspectable evidence passage. Return non-secret call receipts containing only operation metadata,
  outcomes, elapsed time, and result counts. Asta is unavailable only when all attempted relevance searches
  fail their standard and minimal attempts; endpoint-specific terminal failures remain explicit gaps
  and do not block valid Monarch/DisMech curation.
- Configure Asta in the MCP host with `ASTA_AI2_API_KEY`. The controller never reads that variable
  or persists keys, headers, authentication data, or raw MCP exchanges.
- Configure and authenticate Undermind in the MCP host. The controller does not persist account or
  authentication data, search goals, queries, reports, or raw MCP exchanges.
- Pathology research assertions are optional and may use only exact `node_id` values in the packet's `allowed_assertion_nodes`; keep newly researched mechanisms in the profile when they do not connect two allowed nodes.
- If a focal research concept appears to contain nested mechanisms, compare each semantically with
  that complete index and its atomicity metadata. Do not duplicate indexed mechanisms; research
  unindexed mechanisms distinctly in the focal profile, and flag one in `gaps` as a possible
  missing atomic concept when it has an independent focal or desired state, causal level, or
  compartment. This does not change focal identity or desired state or create additional graph
  nodes.
- Curation alone owns pathology splits, merges, concept identity, and research eligibility. Asta
  and Undermind may expose a broad claim by returning separate evidence-backed atomic proposals,
  but those are decomposition candidates rather than ontology decisions. Every research concept records one
  focal abnormal state, causal level, biological direction, compartment, atomicity rationale, and
  primary desired biological state. Context-only and excluded concepts use `atomicity: null`; do
  not invent state metadata for claims that do not support a research route. Keep separately supplied
  states separate when they can occur independently, require different biological normalisation,
  occupy different causal levels or compartments, or have materially different evidence. If a
  lone source node bundles such states without separately supported proposals, retain it as
  `context_only` and report the missing atomic subclaims as a gap; do not fabricate them. Keep
  inseparable steps, assays, models,
  biomarkers, populations, and alternate wording within one concept when they do not change that
  state. Do not make a bundled claim appear atomic by replacing it with an umbrella label: the
  focal abnormal state must identify one supplied biological control variable or process. Claims
  spanning independently variable cell types, compartments, causal levels, cargo classes, or
  molecular species remain separate when their biological normalisation differs. The curator does
  not research or invent why a broad pathology exists; it uses only separately supported supplied
  nodes and proposals, and otherwise assigns the broad claim `context_only` plus a decomposition
  gap. The final concepts partition and `member_node_ids` are the sole split/merge record; do not
  add parallel `proposed_splits`, `merge_targets`, or identity metadata. Later research preserves
  this identity boundary and primary desired state and records contrary findings as gaps rather
  than silently splitting, merging, or redefining the concept.
- Curation separates concept identity from research eligibility: concept distinctness does not
  create a research job, and researchability may not be deferred to deep research. A bare gene or
  gene-disease association, risk factor, model genotype,
  broad pathway, terminal outcome, or mutation label without supplied functional pathology is
  normally context-only. Generic gene and lesion-specific claims do not both create research routes
  unless each supplies a distinct intervention variable. Measurement-only biomarkers are context;
  a biomarker-labelled causal process is classified by that mechanistic role rather than forced
  into context. A phenotype receives `research` disposition only when every following criterion is
  established by the supplied packet: (1) it is disease-attributed, meaning supported as part of
  the focal disease rather than merely incidental, treatment-induced, age-related, or comorbid;
  disease-attributed does not mean unique to that disease; (2) it has at least one direct,
  source-supported causal or mechanistic link to a retained driver or mechanism, not merely
  co-occurrence, graph proximity, or label similarity; (3) it expresses one physiological state
  with one biological direction and supported compartment; (4) it is an independent objective,
  not a subordinate sign, measurement, severity descriptor, or manifestation that would be
  substantially subsumed by normalising an upstream retained concept; and (5) it is material
  enough that changing it would preserve a major function or independently important disease
  burden and justify its own candidate-discovery route. If any criterion is absent or uncertain,
  retain the phenotype `context_only` and attach it to the relevant research concept. Curation
  does not assess drug availability, therapeutic actionability, or rescue plausibility when making
  this decision.
- Every graph assertion is keyed by its biological triple and retains cited evidence contexts for evidence type, model, stage, polarity, and summary; Python assigns the stable assertion ID.
- Candidate graph provenance contains only graph nodes and assertion IDs explicitly selected by the seed worker, with one non-duplicative graph rationale. Focal-profile-only hypotheses may select no assertion.
- Seed workers review every immediate focal neighbour, source edge, and researched assertion before
  searching, then retain only materially useful graph context. Candidate reviewers receive the exact
  selected assertions plus a bounded source-edge projection for the selected nodes and cited
  pathology sources; the full graph remains available to the audit.
- Candidate evidence reviews use the frozen graph as disease context, retrieve primary or authoritative drug facts, and build cited dossiers without scoring, ranking, or excluding candidates.
- The independent audit receives the frozen graph, candidate-identity result, dossiers, and retained source content. It reads and weighs that evidence rather than restating a review and may not search, add evidence, or send a candidate for re-review.
- Audit source integrity is decided at the exact point of use. Every source cited by a score component, net assessment, indexed alias, indexed reservation, or exclusion receives one concrete `supports`, `partly_supports`, `does_not_support`, or `contradicts` finding based on retained content. A generic integrity status or a direction to re-verify later is invalid; Python checks complete non-overlapping coverage, prevents publication aliases from being counted as independent support in one scope, and summarizes the results.
- Long or uncertain hypotheses remain rankable with explicit reservations. Audit exclusion is limited to established exact-disease use, a qualifying exact-disease experiment, unsupported or opposite proposed action, demonstrated impossibility of relevant action or exposure, or an entity established not to be a repurposable drug or administered intervention. A qualifying experiment may be human or preclinical and favorable or unfavorable, but the retained corpus must establish relevant exposure, a credible counterfactual, and a disease-relevant outcome; mere registration, uncontrolled anecdote, or an unsuitable or inadequately controlled experiment remains ranked with a concise cited reservation. The audit packet supplies exact definitions; uncertainty or missing data never establishes an exclusion.
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
