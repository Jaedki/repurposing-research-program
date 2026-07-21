# Schema-v7 extended discovery adapters

Use `scripts/v7_extended_discovery_adapters.py` only for source-bounded discovery before identity normalization or screening. Validate every executed `CoverageProof`; retain every planner or unsupported-capability record in the coverage frame.

## Implemented source slices

| Modality or branch | Source/contract | Boundary |
|---|---|---|
| Clinical intervention enumeration | ClinicalTrials.gov API v2 `/studies` | Traverse `nextPageToken`; normalize each study intervention separately; emit only drug/dietary-supplement mappings; ledger other intervention types. |
| Failed, terminated, or negative-result branch | ClinicalTrials.gov status filters, `whyStopped`, `hasResults`, retained `resultsSection` | Preserve status and exact why-stopped/outcome text as lightweight annotations. Do not infer efficacy, asset-wide development status, shelved membership, or result polarity at discovery depth. |
| Adjacent indication | Separate ClinicalTrials.gov condition plan | Keep the adjacent disease mapping explicit; do not transfer endpoint evidence. |
| Observational/real-world signal | Separate ClinicalTrials.gov observational plan | Use `observational_real_world`; never relabel it as genetics or interventional evidence. |
| Recent sparse literature | bioRxiv/medRxiv date/category cursor API | Traverse all declared recent pages. The executable adapter matches predeclared exact case/intervention terms; a separate checksum-bound dictionary planner covers previously unknown chemical names. Citation count is unknown unless a separate authoritative snapshot supplies it. |
| Natural product, metabolite, or nutrient mapping | ChEBI through OLS4 | Retain exact ChEBI IDs. Supply chemical-universe membership from the declared branch; keep it orthogonal to causal route, action, and direction. Default action/direction remain `unknown`. |
| Public withdrawn assets | ChEMBL molecule traversal with source-native `withdrawn_flag` | Emit withdrawn development status and `shelved_or_failed_assets` membership only when ChEMBL supplies the flag; a terminated trial alone cannot confer either. |

## Bounded planners

- Every bounded or unsupported planner is constructed from a validated `ready` case, carries its case revision ID, and refuses endpoint IDs outside that case portfolio.
- **Molecular-signature reversal:** Freeze the disease signature, up/down genes, matrix release URI, reversal metric, perturbagen bound, explicit cell/dose/time filter tuples, and redistribution status. Empty filter tuples mean the entire declared matrix dimension, not an unspecified choice. Without an accessible frozen matrix, emit `UnsupportedCapabilityRecord`.
- **Phenotypic screening:** Search PubChem BioAssay through NCBI ESearch, retain the bounded AID list, then execute one declared PubChem concise plan per AID. Preserve active, inactive, inconclusive, and unspecified outcomes.
- **Genetics:** Query a frozen GWAS Catalog v2 trait branch, retain association and gene-mapping provenance, then hand mapped targets to a separately declared chemical adapter. Proximity is not causality; genetics never becomes clinical evidence.
- **Pathway/network proximity:** Map identifiers and query a version-specific STRING endpoint with explicit species, score, distance, and added-node bounds; hand retained targets to target-first chemical enumeration.
- **Previously unknown recent names:** Traverse the same bounded preprint feed and scan case-matched records against an openly redistributable, checksum-bound ChEBI/equivalent label-synonym dictionary. Emit unverified source-located name/database hints only; without the dictionary snapshot, emit an unsupported gap.

Official contracts:

- ClinicalTrials.gov: `https://clinicaltrials.gov/data-about-studies/learn-about-api`
- bioRxiv/medRxiv: `https://api.biorxiv.org/details/medrxiv/help`
- ChEBI/OLS: `https://www.ebi.ac.uk/chebi/beta/tools`
- GWAS Catalog v2: `https://www.ebi.ac.uk/gwas/rest/api/v2/docs`
- STRING: `https://string-db.org/help/api/`
- NCBI E-utilities: `https://www.ncbi.nlm.nih.gov/books/NBK25497/`

## Unsupported sources

For a licence, credential, private enclave, inaccessible API, absent frozen release, or ambiguous redistribution right, emit a content-derived `UnsupportedCapabilityRecord` containing the exact planned query, reason, access requirement, authoritative reference, alternatives, and preserved coverage gap. Do not omit the branch or convert it to complete/no-hits coverage. This applies, for example, to an unfurnished CLUE/CMap matrix and commercial claims or pipeline-asset inventories.

## Anti-popularity discovery

Build `AntiPopularityDiscoveryFrame` from all eligible source-derived seeds. The frame must:

- retain every mapped seed without publication/citation filtering;
- expose reserved database-only and low-publication cohorts;
- expose recent or uncited and negative/null cohorts;
- retain a distinct preclinical cohort;
- set both citation-admission flags to false; and
- emit no therapeutic score or rank.

Treat `PublicationDensityMetadata` as descriptive only. A highly cited directionally opposed record remains visible as counterevidence but receives no discovery privilege. Do not follow citation chains as the only literature frontier.

Use `benchmarks/schema_v7/extended_discovery_adapters/frozen_responses.json` for offline tests. It is a synthetic official-shape development fixture, not a source oracle or scientific benchmark.
