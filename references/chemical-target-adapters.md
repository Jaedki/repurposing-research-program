# Schema-v7 chemical and target adapters

Use `scripts/v7_chemical_target_adapters.py` after declaring a ready case and before scientific screening. Keep retrieval source-bounded; do not translate adapter completion into universal chemical or scientific exhaustiveness.

## Enumeration order

1. Enumerate Open Targets drug candidates from an exact target or disease entity.
2. Enumerate ChEMBL targets, then target/assay-derived mechanisms, molecules, and activities. Reject candidate-name filters at this stage.
3. Enumerate BindingDB ligands from an exact UniProt target.
4. Enumerate PubChem AIDs from an NCBI Gene target, then retrieve concise compound mappings per AID.
5. Resolve exact InChIKey cross-references with UniChem only for already-emitted seeds.

Every accepted source record receives one normalized disposition. Emit seeds for all in-scope chemical mappings regardless of activity outcome, literature volume, human evidence, readiness, or therapeutic plausibility. Retain approved, clinical-stage, shelved/failed, and tool/preclinical memberships independently.

## Source-specific boundaries

- **Open Targets:** use the current GraphQL `drugAndClinicalCandidates` collections for one declared target or disease and release. Individually ledger non-small-molecule exclusions. For systematic release-wide work, use the official downloads rather than repeated one-entity calls.
- **ChEMBL:** follow `page_meta.next` offsets through the declared resource and filters. Preserve `total_count`, native IDs, molecule/target/assay IDs, activity type/relation/value/units, organism, and assay confidence where supplied.
- **ChEMBL withdrawn assets:** use a separately declared molecule plan with the source-native `withdrawn_flag`; only that flag promotes withdrawn development status and `shelved_or_failed_assets` membership. A failed trial in another source does not.
- **BindingDB:** treat `getLigandsByUniprot` as a bounded non-paginated query. Preserve monomer ID, SMILES, affinity type/value, and target. Leave units, assay ID, organism, and confidence blank when the response omits them.
- **PubChem:** preserve AID/SID/CID identity and concise activity outcome, value, units, assay name/type, and target identifiers. Inactive rows still identify chemical mappings and therefore seed.
- **UniChem:** use exact InChIKey cross-references as identity support. Do not infer active-moiety, formulation, route, connectivity-only, or evidence-transfer equivalence.

Consult the official APIs before changing request or response contracts:

- Open Targets: `https://platform-docs.opentargets.org/data-access/graphql-api`
- ChEMBL: `https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services`
- BindingDB: `https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp`
- PubChem: `https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest`
- UniChem: `https://www.ebi.ac.uk/unichem/api/docs`

Use the checksum-bound `benchmarks/schema_v7/chemical_target_adapters/frozen_responses.json` fixture for offline tests and cache/replay validation. Use `union_cross_adapter_chemicals` only as an exact-identity provenance union; it does not perform seed disposition, active-moiety normalization, screening, ranking, audit, persistence, or output construction.
