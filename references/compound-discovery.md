# Exact-Compound Discovery

## Candidate Question

For each audited rescue relation, search for every exact drug or exact natural compound with a plausible directional path toward the wild-type worm phenotype. This is a screening-discovery standard, not a requirement for proven therapeutic efficacy. Lack of exact-model rescue evidence lowers confidence but does not disqualify a novel candidate supported by upstream pharmacology and a coherent causal path.

## Search Routes

Use several independent routes where applicable:

- literature searches combining target, pathway, phenotype, cell, circuit, metabolite, modifier, or inverse-state terms with drug, compound, agonist, antagonist, activator, inhibitor, stabilizer, chaperone, suppressor, rescue, phenotypic screen, and natural-product terms;
- ChEMBL, BindingDB, PubChem BioAssay, ChEBI, Open Targets, DrugCentral or other authoritative drug/chemical resources;
- target-to-ligand, ligand-to-target, pathway-to-compound, phenotype-to-compound, disease-to-drug, and perturbational-signature routes;
- citation chaining from relevant screens, pharmacology studies, reviews used only as maps, and primary reports;
- close-species and model-organism evidence with explicit transfer limitations;
- exact natural-product and endogenous-metabolite searches, including chemical identity and activity verification.

Do not stop at the first familiar compounds. Search aliases, orthologues, protein-complex members, pathway nodes, opposing phenotypes, and downstream effects. Record searches that yield no candidates so absence is distinguishable from omission.

## Exact Identity Gate

A candidate must be one resolvable chemical entity with:

- a canonical name;
- an authoritative stable identifier such as ChEMBL ID, PubChem CID, ChEBI ID, DrugBank ID, or another auditable equivalent;
- a standardized `structure_identity_key`: InChIKey, or a canonical-SMILES SHA-256 fallback when no InChIKey exists;
- identity verification against an authoritative chemical record;
- at least one connected audited graph path from its chemical node to `CASE_WILD_TYPE_PHENOTYPE`, using only rescue-supporting edges and allele-compatible claims;
- a concise uncertainty statement.

Salts may be normalized to the active moiety when scientifically appropriate, with formulation differences retained internally. A compound class is never expanded into guessed members. A named extract, diet, mixture, genetic manipulation, environmental condition, assay reagent, tool without a plausible rescue purpose, or intervention lacking a discrete chemical identity is not a candidate.

## Candidate Record

Write one object per identity to `candidate_records.jsonl`:

```text
candidate_id,canonical_name,canonical_identifier,registry_identifiers,structure_identity_key,chemical_node_id,identity_source_ids,entity_type,identity_verified,human_gene,worm_gene,allele_mode,worm_model,origin,source_research_unit_ids,causal_paths,rationale,phenomic_interpretation,decisive_uncertainty,dossier_path,council_disposition,fact_audit_status
```

Use `entity_type=discrete_chemical`. Use origin `de_novo`, `prior_exact_model_screen`, or `mixed`. Each causal path contains `path_id`, ordered `edge_ids`, `claim_ids`, `start_node`, `end_node`, and `expected_rescue_direction=toward_wild_type`. It starts at `CHEM:<structure_identity_key>`, ends at `CASE_WILD_TYPE_PHENOTYPE`, and is graph-connected.

## Merge Without Premature Pruning

Merge synonyms and cross-database identifiers by `structure_identity_key`, not by text resemblance or one registry ID. Preserve every registry identifier and independent discovery route in the merged dossier. Send all identity-valid, traceable candidates to council; do not keep only familiar drugs, positive-control-like compounds, or candidates already tested in the exact model.

Exclude before council only when the entity is not an exact chemical, identity cannot be resolved, or no audited directional path survives repair. Record that exclusion outside the candidate universe so it cannot leak into final output.
