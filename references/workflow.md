# Human Therapeutic Workflow

## Inputs and endpoint

Require at least one of `human_gene`, `human_disease`, or `human_phenotype`. Treat organism, cell, organoid, and other experimental-model details as optional context. Never make a model-organism gene, allele, assay platform, or wild-type model phenotype a required input or terminal endpoint.

Every candidate causal path starts at its exact chemical node and terminates at `CASE_HUMAN_THERAPEUTIC_OUTCOME`. A path may contain model evidence, but it must explain the transfer to a human disease or beneficial human phenotype and preserve the uncertainty of that transfer.

## Stages

1. Run five isolated broad-evidence units: human disease biology, molecular function/network, phenotype/pathophysiology, pharmacology landscape, and clinical safety/exposure. These units build the living evidence graph but do not generate compounds. Each existing unit closes only after terminal search coverage, required saturation families, and deterministic frontier exhaustion.
2. Run seven isolated compound perspectives: direct mechanism, phenotype reversal, vulnerability/inverse state, compensatory network, human genetics/clinical, hidden in plain sight, and exact natural compounds. `PERSPECTIVE_CONTRACTS` in `scripts/program_contract.py` defines each lens's discovery objective, causal route, required coverage areas, prohibited primary rationales, exact rationale contract, and boundary from the other lenses. Give each worker only its own contract and the broad-evidence graph, never another perspective's contract, candidate claims, edges, sources, or observations. The same exact compound may converge independently when each observation supplies a genuinely distinct source-supported route. Log every screened exact compound not emitted and why. Branch expansion remains inside the existing unit and is permitted only by the machine contract; it is not a new stage.
3. Merge observations by source-backed active moiety while retaining exact formulation structure keys. Preserve every observation, source unit, rationale, and disagreement. Assign `candidate_class`, orthogonal `compound_origin`, a source-backed primary target endpoint, and repurposing readiness. Standard care and target-disease development remain baselines or benchmarks, not primary repurposing leads.
4. Independently retrieve and verify every claim used by a candidate path in one batched decisive-claim audit. Packet sources cannot satisfy independent retrieval. Record a verdict, independently executed verification and counterevidence searches, checked source IDs, and a claim-specific rationale; then reassess every candidate score and cap.
5. Apply the deterministic rubric and bidirectionally derived caps. Rank within primary repurposing, target-disease benchmark, baseline-care, and preclinical sections and also within the declared endpoint. Select the top primary repurposing candidates, top therapeutic scores, and genuine material conflicts for one focused council review. Council must correct category or endpoint-type errors, return the selected candidate records for deterministic reranking, and assign `baseline_only` or `benchmark_only` where applicable. Do not run an advocate/sceptic/response exchange.
6. Validate the complete run and build the two user-facing outputs with `scripts/build_final_outputs.py`.

All research remains hypothesis generation for expert review, not clinical advice. Do not claim universal exhaustiveness; the strongest closure claim is that no known decision-changing branch remains within the documented scope.
