# Independent Research Programme

## Unit Contract

Each research unit is a separate subagent task with one scientific question, its own search plan, and an auditor who did not perform the work. The worker receives only its immutable packet: relevant audited graph neighbourhoods, linked source records, contradictions, and case context. It never receives another independent unit's candidate list. It must search original literature and authoritative databases itself; the knowledge base is context, not a substitute for retrieval.

Before searching, write the unit's question, directional rescue logic, aliases, planned query families, source families, expected contrary evidence, and explicit completion tests to `research_units.jsonl`. Search broadly, screen results, acquire relevant original evidence, follow backward and forward citations, and add newly discovered material subtopics to the registry.

A worker must process every packet chunk and reject completion based on elapsed time, document length, source count, consensus, or phrases such as `enough to write` or `enough to judge`. It may request closure only after it has enumerated the remaining uncertainties, tested plausible missing branches, and concluded that none is currently decision-changing or searchable at high yield. Preserve ambiguity; never convert absence, indirectness, species differences, or conflicting evidence into confidence.

## Evidence Units

After broad retrieval, assign one evidence worker per registered subtopic. A subtopic is not a paragraph assignment: it is an independent retrieval, screening, synthesis, counterevidence, and closure task. Material children receive new workers in the next wave.

## Compound Units Per Subtopic

Every `candidate_relevant=true` subtopic receives an independent compound-discovery unit. Its question is: which exact drugs or exact natural compounds could directionally improve this relation in the specified allele and worm model? It must use the search routes in `compound-discovery.md` and may return zero candidates only through `evidence_absent_complete`.

## Mandatory Global Perspectives

Run all of these even when subtopic units appear to cover similar biology:

- `direct_molecular`: restore, stabilize, potentiate, replace, bypass, or directionally compensate the primary molecular defect.
- `phenotype_first`: start from the complete worm and human phenotype/readout profile, then find compounds reported to reverse analogous signatures without assuming the primary mechanism.
- `vulnerability_inverse`: identify suppressor states, inverse phenotypes, synthetic rescue, antagonistic pathways, and liabilities unique to the disease state, then work back to exact compounds.
- `compensatory_network`: search paralogues, neighbours, complexes, feedback loops, circuit compensation, modifiers, and bypass pathways.
- `maximal_novelty`: deliberately cross domains, indications, species, phenotypic screens, perturbational signatures, and underused mechanisms while retaining traceable causal logic.
- `natural_compounds`: search exact, chemically defined natural products and metabolites with identity and purity-resolvable records; exclude extracts and mixtures.
- `behavioural_data_first`: required when WT and disease-model behavioural data are supplied; identify altered Tierpsy/Brown-style features and seek compounds or signatures predicted to shift the multivariate phenotype toward WT.

Add `prior_screen_context` only when prior-screen data were volunteered and the run is not blinded. Recover exact-model and closely analogous screen hits, label them separately, and never let them stand in for de novo work.

## Independence And Convergence

Overlap in sources or compounds is allowed when biologically justified. Distinctness is demonstrated by different predeclared questions and query families, not by forced non-overlap. The auditor challenges suspicious convergence: identical search strings, identical evidence paths, or a perspective that never used its defining logic requires repair.

Record convergence only after units close. Packet generation must not leak candidates between units. Independent convergence strengthens prioritization; it never erases dissent or the distinct supporting routes.
