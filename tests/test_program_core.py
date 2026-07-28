import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402


def source_result(*_args):
    return {
        "stage": "pathology_sources",
        "status": "complete",
        "records": {
            "documents": [{"document_id": "SRC:1", "title": "Pathology", "source": "test"}],
            "source_nodes": [
                {
                    "node_id": "MONDO:1",
                    "label": "Disease",
                    "node_type": "disease_anchor",
                    "source_ids": ["SRC:1"],
                },
                {
                    "node_id": "NODE:1",
                    "label": "Process",
                    "node_type": "molecular_process",
                    "source_ids": ["SRC:1"],
                },
            ],
            "source_edges": [
                {
                    "edge_id": "EDGE:1",
                    "subject_id": "NODE:1",
                    "relation": "contributes_to",
                    "object_id": "MONDO:1",
                    "evidence_summary": "test",
                    "source_ids": ["SRC:1"],
                }
            ],
            "source_receipts": [
                {"source": "test", "version": "1", "query": {}, "record_count": 2}
            ],
            "disease_context": [
                {
                    "context_id": "CONTEXT:1",
                    "section": "description",
                    "value": "Shared disease context",
                    "source_ids": ["SRC:1"],
                }
            ],
        },
        "gaps": [],
        "notes": [],
    }


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(core, "fetch_pathology_sources", source_result)
        self.patch.start()
        core.initialize(self.root, "Disease", mondo="MONDO:1")

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def submit(self, action, records):
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        result = {
            "stage": action["next_task"],
            "item_id": packet["item_id"],
            "packet_id": packet["packet_id"],
            "status": "complete",
            "records": records,
            "gaps": [],
        }
        path = self.root / f"submission-{len(list(self.root.glob('submission-*')))}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return core.submit(self.root, path)

    def curate_single_process(self):
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_curation")
        self.submit(
            action,
            {
                "concepts": [
                    {
                        "concept_id": "NODE:1",
                        "preferred_label": "Process",
                        "concept_type": "mechanism",
                        "member_node_ids": ["NODE:1"],
                        "aliases": [],
                        "disposition": "research",
                        "reason": "Distinct modifiable disease process",
                        "related_concept_ids": [],
                    }
                ]
            },
        )

    def test_graph_barrier_and_mechanism_evidence_chain(self):
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_curation")
        curation_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [row["node_id"] for row in curation_packet["context"]["source_nodes"]],
            ["NODE:1"],
        )
        self.assertEqual(
            [row["section"] for row in curation_packet["context"]["disease_context"]],
            ["description"],
        )
        rules = " ".join(curation_packet["result_contract"]["field_rules"])
        self.assertIn("same-level equivalence", rules)
        self.assertNotIn("prefer an authoritative", rules)
        self.submit(
            action,
            {
                "concepts": [
                    {
                        "concept_id": "NODE:1",
                        "preferred_label": "Process",
                        "concept_type": "mechanism",
                        "member_node_ids": ["NODE:1"],
                        "aliases": [],
                        "disposition": "research",
                        "reason": "Distinct modifiable disease process",
                        "related_concept_ids": [],
                    }
                ]
            },
        )

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_node_research")
        research_packet = json.loads(
            Path(action["packet_path"]).read_text(encoding="utf-8")
        )
        contract = research_packet["result_contract"]
        self.assertEqual(contract["records"]["profiles"]["type"], "list of objects")
        self.assertIsInstance(contract["records"]["profiles"]["required_fields"], list)
        self.assertTrue(any("temporal_context" in rule for rule in contract["field_rules"]))
        self.assertIn(action["packet_id"], action["worker_prompt"])
        self.assertIn(action["suggested_result_path"], action["worker_prompt"])
        first_item = action["next_item_id"]
        self.assertEqual(first_item, "NODE:1")
        node_type = research_packet["context"]["node"]["node_type"]
        self.assertEqual(
            research_packet["context"]["disease_context"][0]["section"],
            "description",
        )
        self.submit(action, self.profile(first_item, node_type))

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_seed_research")
        self.assertTrue((self.root / "results" / "evidence_graph.json").exists())
        self.assertEqual(action["next_item_id"], "NODE:1")
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(packet["context"]["concept"]["node_id"], "NODE:1")
        self.assertEqual(packet["context"]["profile"]["node_id"], "NODE:1")
        seed_records = {
            "documents": [
                {"document_id": "PMID:2", "title": "Drug MOA", "source": "test"}
            ],
            "candidates": [
                {
                    "candidate_id": "CHEMBL:1",
                    "name": "Drug",
                    "identity": {
                        "status": "resolved",
                        "preferred_name": "Drug",
                        "identifiers": {"chembl": "CHEMBL:1"},
                    },
                    "desired_change": "normalize the process",
                    "mechanism_hypothesis": "inhibits the process",
                    "graph_node_ids": ["NODE:1"],
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:2"],
                },
                {
                    "candidate_id": "CHEMBL:2",
                    "name": "Second drug",
                    "identity": {
                        "status": "resolved",
                        "preferred_name": "Second drug",
                        "identifiers": {"chembl": "CHEMBL:2"},
                    },
                    "desired_change": "normalize the process differently",
                    "mechanism_hypothesis": "modulates the process",
                    "graph_node_ids": ["NODE:1"],
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:2"],
                },
            ],
            "exclusions": [],
        }
        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["candidates"][0]["graph_node_ids"] = ["MONDO:1"]
        with self.assertRaisesRegex(core.ProgramError, "outside the item concept"):
            self.submit(action, invalid_records)
        self.submit(
            action,
            seed_records,
        )
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_review_research")
        self.assertEqual(action["next_item_id"], packet["item_id"])
        review_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn("authoritative disease context", review_packet["task"])
        self.assertIn("decision-changing prior art", review_packet["task"])
        self.assertTrue(
            any(
                "document retained" in rule
                for rule in review_packet["result_contract"]["field_rules"]
            )
        )
        self.assertEqual(review_packet["context"]["primary_concept_id"], packet["item_id"])
        self.assertEqual(
            [row["candidate_id"] for row in review_packet["context"]["candidates"]],
            ["CHEMBL:1", "CHEMBL:2"],
        )
        self.assertEqual(
            [row["document_id"] for row in review_packet["context"]["source_index"]],
            ["PMID:2"],
        )
        seeds = json.loads(
            (self.root / "results" / "candidate_seed_generation.json").read_text()
        )
        self.assertEqual(
            seeds["records"]["candidates"][0]["origin_concept_ids"],
            [packet["item_id"]],
        )
        self.submit(
            action,
            {
                "documents": [
                    {"document_id": "PMID:3", "title": "Drug review", "source": "test"}
                ],
                "reviews": [
                    self.review("CHEMBL:1", "PMID:3"),
                    self.review("CHEMBL:2", "PMID:3"),
                ],
            },
        )
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "audit_and_rank")
        audit_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(audit_packet["context"]["candidates"]), 2)
        self.assertEqual(len(audit_packet["context"]["reviews"]), 2)

    def test_curation_guidance_and_input_order_preserve_semantic_granularity(self):
        source = source_result()
        source["records"]["source_nodes"].extend(
            [
                {
                    "node_id": "NODE:Z",
                    "label": "Zeta driver",
                    "node_type": "driver",
                    "source_ids": ["SRC:1"],
                },
                {
                    "node_id": "NODE:A",
                    "label": "Alpha driver",
                    "node_type": "driver",
                    "source_ids": ["SRC:1"],
                },
                {
                    "node_id": "NODE:P",
                    "label": "Beta phenotype",
                    "node_type": "phenotype",
                    "source_ids": ["SRC:1"],
                },
            ]
        )

        context = core._packet_context(
            "pathology_curation", None, {"pathology_sources": source}
        )
        guidance = core.STAGE_GUIDANCE["pathology_curation"]["task"]

        self.assertEqual(
            [row["node_id"] for row in context["source_nodes"]],
            ["NODE:A", "NODE:Z", "NODE:1", "NODE:P"],
        )
        self.assertIn("do not minimize concept count", guidance)
        self.assertIn("do not establish equivalence", guidance)
        self.assertIn("same-label gene-level", guidance)
        self.assertIn("Merge true duplicate records", guidance)
        self.assertIn("assign disposition independently", guidance)
        self.assertIn("major phenotype defining a distinct intervention objective", guidance)
        self.assertIn("context_only even when measurable", guidance)
        self.assertIn("bare entity or observational readout", guidance)
        self.assertIn("uncertainty never upgrades", guidance)
        seed_guidance = core.STAGE_GUIDANCE["candidate_seed_research"]["task"]
        self.assertIn("symptomatic or compensatory benefit", seed_guidance)
        self.assertIn("linked context nodes", seed_guidance)

    def test_curation_requires_an_exact_partition(self):
        records = {
            "concepts": [
                {
                    "concept_id": "NODE:1",
                    "preferred_label": "Process",
                    "concept_type": "mechanism",
                    "member_node_ids": ["NODE:1"],
                    "aliases": [],
                    "disposition": "research",
                    "reason": "Distinct mechanism",
                    "related_concept_ids": [],
                }
            ]
        }
        prior = {"pathology_sources": source_result()}
        core._validate_curation(records, prior)
        records["concepts"][0]["member_node_ids"] = ["UNKNOWN"]
        records["concepts"][0]["concept_id"] = "UNKNOWN"
        with self.assertRaisesRegex(core.ProgramError, "partition every supplied"):
            core._validate_curation(records, prior)

    def test_curation_merges_nodes_and_collapses_self_edges(self):
        source = source_result()
        source["records"]["source_nodes"].append(
            {
                "node_id": "NODE:2",
                "label": "Same process",
                "node_type": "molecular_process",
                "source_ids": ["SRC:1"],
            }
        )
        source["records"]["source_edges"].append(
            {
                "edge_id": "EDGE:2",
                "subject_id": "NODE:2",
                "relation": "equivalent_to",
                "object_id": "NODE:1",
                "evidence_summary": "duplicate",
                "source_ids": ["SRC:1"],
            }
        )
        results = {
            "pathology_sources": source,
            "pathology_curation": {
                "records": {
                    "concepts": [
                        {
                            "concept_id": "NODE:1",
                            "preferred_label": "Process",
                            "concept_type": "mechanism",
                            "member_node_ids": ["NODE:1", "NODE:2"],
                            "aliases": ["Same process"],
                            "disposition": "research",
                            "reason": "Equivalent source concepts",
                            "related_concept_ids": [],
                        }
                    ]
                }
            },
        }
        nodes, edges = core._canonical_source_records(results)
        self.assertEqual([row["node_id"] for row in nodes], ["MONDO:1", "NODE:1"])
        self.assertEqual(nodes[1]["member_node_ids"], ["NODE:1", "NODE:2"])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["subject_id"], "NODE:1")
        self.assertEqual(edges[0]["object_id"], "MONDO:1")

    def test_curation_rejects_duplicate_retained_type_and_label(self):
        source = source_result()
        source["records"]["source_nodes"].append(
            {
                "node_id": "NODE:2",
                "label": "Process",
                "node_type": "molecular_process",
                "source_ids": ["SRC:1"],
            }
        )
        concepts = [
            {
                "concept_id": node_id,
                "preferred_label": "Process",
                "concept_type": "mechanism",
                "member_node_ids": [node_id],
                "aliases": [],
                "disposition": "research",
                "reason": "Separate source claim",
                "related_concept_ids": [],
            }
            for node_id in ("NODE:1", "NODE:2")
        ]

        with self.assertRaisesRegex(core.ProgramError, "duplicates the retained type and label"):
            core._validate_curation(
                {"concepts": concepts}, {"pathology_sources": source}
            )

    def test_only_research_concepts_create_work_items(self):
        source = source_result()
        source["records"]["source_nodes"].extend(
            [
                {
                    "node_id": "NODE:2",
                    "label": "Anatomical context",
                    "node_type": "anatomy",
                    "source_ids": ["SRC:1"],
                },
                {
                    "node_id": "NODE:3",
                    "label": "Generic noise",
                    "node_type": "pathology_context",
                    "source_ids": ["SRC:1"],
                },
            ]
        )
        concepts = [
            {
                "concept_id": node_id,
                "preferred_label": label,
                "concept_type": concept_type,
                "member_node_ids": [node_id],
                "aliases": [],
                "disposition": disposition,
                "reason": reason,
                "related_concept_ids": related,
            }
            for node_id, label, concept_type, disposition, reason, related in (
                ("NODE:1", "Process", "mechanism", "research", "Distinct mechanism", []),
                ("NODE:2", "Anatomical context", "context", "context_only", "Context only", ["NODE:1"]),
                ("NODE:3", "Generic noise", "context", "exclude", "Not informative", []),
            )
        ]
        results = {
            "pathology_sources": source,
            "pathology_curation": {"records": {"concepts": concepts}},
        }
        core._validate_curation(results["pathology_curation"]["records"], results)
        self.assertEqual(core._item_ids("evidence_graph", results), ["NODE:1"])
        nodes, edges = core._canonical_source_records(results)
        self.assertEqual(
            [row["node_id"] for row in nodes], ["MONDO:1", "NODE:1", "NODE:2"]
        )
        self.assertTrue(
            any(
                row["subject_id"] == "NODE:2"
                and row["relation"] == "contextualizes"
                and row["object_id"] == "NODE:1"
                for row in edges
            )
        )

    def test_pathology_research_requires_retained_evidence(self):
        self.curate_single_process()
        action = core.next_action(self.root)
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["temporal_context"] = "disease progression"
        with self.assertRaisesRegex(core.ProgramError, "temporal_context must be a list"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["documents"] = []
        with self.assertRaisesRegex(core.ProgramError, "retain newly researched evidence"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["documents"][0]["document_id"] = "DOC-AUTHOR-2026-TOPIC"
        records["profiles"][0]["source_ids"][-1] = "DOC-AUTHOR-2026-TOPIC"
        with self.assertRaisesRegex(core.ProgramError, "canonical PMID"):
            self.submit(action, records)

    def test_canonical_document_identifier_families(self):
        for document_id in (
            "PMID:10195180", "PMCID:PMC10338806", "DOI:10.1002/ana.21147",
            "MONARCH-ASSOC-" + "A" * 24, "CLINGEN:CCID004621",
            "UNIPROT:P09651", "NCBI:NBK551641", "https://example.org/report",
        ):
            self.assertIsNotNone(core.CANONICAL_DOCUMENT_ID.fullmatch(document_id))
        self.assertIsNone(core.CANONICAL_DOCUMENT_ID.fullmatch("DOC-AUTHOR-2026-TOPIC"))

    def test_document_metadata_enriches_without_identity_conflict(self):
        documents = core._merge_documents([
            {
                "document_id": "PMID:22312314",
                "title": "PMID:22312314",
                "source": "DisMech evidence",
                "citation": "PMID:22312314",
                "snippets": ["source snippet"],
                "supports": ["PARTIAL"],
            },
            {
                "document_id": "PMID:22312314",
                "title": "Disruption of Axonal Transport in Motor Neuron Diseases",
                "source": "PubMed Central",
                "snippets": ["research snippet"],
                "supports": ["SUPPORT"],
            },
        ])

        self.assertEqual(len(documents), 1)
        self.assertEqual(
            documents[0]["title"],
            "Disruption of Axonal Transport in Motor Neuron Diseases",
        )
        self.assertEqual(documents[0]["source"], "PubMed Central")
        self.assertEqual(documents[0]["citation"], "PMID:22312314")
        self.assertEqual(documents[0]["snippets"], ["research snippet", "source snippet"])
        self.assertEqual(documents[0]["supports"], ["PARTIAL", "SUPPORT"])

    def test_non_document_identity_conflicts_remain_strict(self):
        with self.assertRaisesRegex(core.ProgramError, "Conflicting profiles records"):
            core._merge_unique(
                [
                    {"node_id": "NODE:1", "label": "one"},
                    {"node_id": "NODE:1", "label": "two"},
                ],
                "node_id",
                "profiles",
            )

    def test_candidate_identity_metadata_normalizes_without_losing_integrity(self):
        first = self.candidate(
            "PUBCHEM:11125",
            "lithium carbonate",
            {
                "status": "resolved",
                "preferred_name": "lithium carbonate",
                "identifiers": {"PubChem CID": "11125", "ChEMBL": "CHEMBL1200826"},
            },
            "CONCEPT-A",
            "NODE:A",
        )
        second = self.candidate(
            "PUBCHEM:11125",
            "Lithium carbonate",
            {
                "status": "resolved",
                "preferred_name": "Lithium carbonate",
                "identifiers": {"pubchem_cid": "11125", "inchikey": "KEY"},
            },
            "CONCEPT-B",
            "NODE:B",
        )

        merged = core._merge_candidates([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0]["identity"]["identifiers"],
            {
                "chembl": "CHEMBL1200826",
                "inchikey": "KEY",
                "pubchem_cid": "11125",
            },
        )
        self.assertEqual(merged[0]["origin_concept_ids"], ["CONCEPT-A", "CONCEPT-B"])
        self.assertEqual(merged[0]["graph_node_ids"], ["NODE:A", "NODE:B"])

        wrong = self.candidate(
            "PUBCHEM:11125",
            "lithium carbonate",
            {
                "status": "resolved",
                "preferred_name": "lithium carbonate",
                "identifiers": {"pubchem_cid": "999"},
            },
            "CONCEPT-C",
            "NODE:C",
        )
        with self.assertRaisesRegex(core.ProgramError, "conflicts with candidate_id"):
            core._merge_candidates([first, wrong])

        wrong_name = self.candidate(
            "PUBCHEM:11125",
            "aspirin",
            {
                "status": "resolved",
                "preferred_name": "aspirin",
                "identifiers": {"pubchem_cid": "11125"},
            },
            "CONCEPT-C",
            "NODE:C",
        )
        with self.assertRaisesRegex(core.ProgramError, "Conflicting candidate names"):
            core._merge_candidates([first, wrong_name])

    def test_authoritative_candidate_id_allows_verified_synonym_names(self):
        first = self.candidate(
            "PUBCHEM:9848818",
            "tauroursodeoxycholic acid",
            {
                "status": "resolved",
                "preferred_name": "tauroursodeoxycholic acid",
                "identifiers": {"pubchem_cid": "9848818"},
            },
            "CONCEPT-A",
            "NODE:A",
        )
        second = self.candidate(
            "PUBCHEM:9848818",
            "taurursodiol",
            {
                "status": "resolved",
                "preferred_name": "taurursodiol",
                "identifiers": {
                    "PubChem CID": "9848818",
                    "synonym": "tauroursodeoxycholic acid (TUDCA)",
                },
            },
            "CONCEPT-B",
            "NODE:B",
        )

        merged = core._merge_candidates([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["candidate_id"], "PUBCHEM:9848818")
        self.assertEqual(merged[0]["identity"]["identifiers"]["pubchem_cid"], "9848818")

    def test_review_batches_assign_each_candidate_once_to_a_linked_origin(self):
        concepts = [
            {
                "concept_id": concept_id,
                "preferred_label": concept_id,
                "concept_type": "mechanism",
                "member_node_ids": [concept_id],
                "aliases": [],
                "disposition": "research",
                "reason": "test",
                "related_concept_ids": [],
            }
            for concept_id in ("NODE:A", "NODE:B")
        ]
        candidates = [
            {
                "candidate_id": "DRUG-A",
                "origin_concept_ids": ["NODE:A"],
                "graph_node_ids": ["NODE:A"],
            },
            {
                "candidate_id": "DRUG-B",
                "origin_concept_ids": ["NODE:B"],
                "graph_node_ids": ["NODE:B"],
            },
            {
                "candidate_id": "DRUG-TIE",
                "origin_concept_ids": ["NODE:B", "NODE:A"],
                "graph_node_ids": ["NODE:A", "NODE:B"],
            },
        ]
        results = {
            "pathology_curation": {"records": {"concepts": concepts}},
            "candidate_seed_generation": {"records": {"candidates": candidates}},
        }

        first = core._review_batches(results)
        results["candidate_seed_generation"]["records"]["candidates"] = list(
            reversed(candidates)
        )
        second = core._review_batches(results)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            [
                {"concept_id": "NODE:A", "candidate_ids": ["DRUG-A", "DRUG-TIE"]},
                {"concept_id": "NODE:B", "candidate_ids": ["DRUG-B"]},
            ],
        )

    def test_review_validation_requires_exact_batch_coverage(self):
        records = {
            "documents": [
                {"document_id": "PMID:1", "title": "Drug evidence", "source": "test"}
            ],
            "reviews": [self.review("DRUG-A"), self.review("DRUG-B")],
        }
        batch = [{"concept_id": "NODE:A", "candidate_ids": ["DRUG-A", "DRUG-B"]}]
        with patch.object(core, "_review_batches", return_value=batch):
            core._validate_review_item(records, "NODE:A", {})
            records["documents"] = []
            with self.assertRaisesRegex(core.ProgramError, "retained by this review"):
                core._validate_review_item(
                    records,
                    "NODE:A",
                    {"prior": {"records": {"documents": [
                        {"document_id": "PMID:1", "title": "Prior evidence", "source": "test"}
                    ]}}},
                )
            records["documents"] = [
                {"document_id": "PMID:1", "title": "Drug evidence", "source": "test"}
            ]
            records["reviews"] = [self.review("DRUG-A")]
            with self.assertRaisesRegex(core.ProgramError, "exactly the supplied batch"):
                core._validate_review_item(records, "NODE:A", {})

    def test_assertion_evidence_merges_but_identity_collision_fails(self):
        assertion = {
            "assertion_id": "ASSERTION:1",
            "subject_id": "NODE:1",
            "relation": "contributes_to",
            "object_id": "NODE:2",
            "evidence_summary": "first finding",
            "source_ids": ["PMID:1"],
        }
        merged = core._merge_assertions([
            assertion,
            {
                **assertion,
                "evidence_summary": "second finding",
                "source_ids": ["PMID:2"],
            },
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_ids"], ["PMID:1", "PMID:2"])
        self.assertEqual(
            merged[0]["evidence_summary"], "first finding | second finding"
        )
        with self.assertRaisesRegex(core.ProgramError, "Conflicting assertion identities"):
            core._merge_assertions([assertion, {**assertion, "object_id": "NODE:3"}])

    @staticmethod
    def profile(item_id, node_type):
        return {
            "documents": [
                {"document_id": "PMID:1", "title": "Research evidence", "source": "test"}
            ],
            "profiles": [
                {
                    "node_id": item_id,
                    "node_type": node_type,
                    "summary": "deep profile",
                    "normal_state": "normal",
                    "pathological_state": "abnormal",
                    "causal_role": "causal",
                    "mechanisms": ["mechanism"],
                    "cell_types": ["relevant cell"],
                    "anatomical_context": ["relevant tissue"],
                    "temporal_context": ["disease progression"],
                    "upstream_causes": ["driver"],
                    "downstream_consequences": ["damage"],
                    "contradictions": [],
                    "gaps": [],
                    "uncertainty": "low",
                    "source_ids": ["SRC:1", "PMID:1"],
                }
            ],
            "assertions": [],
        }

    @staticmethod
    def candidate(candidate_id, name, identity, concept_id, node_id):
        return {
            "candidate_id": candidate_id,
            "name": name,
            "identity": identity,
            "desired_change": f"change {node_id}",
            "mechanism_hypothesis": f"mechanism {node_id}",
            "graph_node_ids": [node_id],
            "pathology_source_ids": [f"PATH:{node_id}"],
            "mechanism_source_ids": [f"MOA:{node_id}"],
            "origin_concept_ids": [concept_id],
        }

    @staticmethod
    def review(candidate_id, source_id="PMID:1"):
        return {
            "candidate_id": candidate_id,
            "rescue_rationale": "plausible rescue",
            "evidence_strength": 2,
            "rescue_fit": 2,
            "uncertainty": "medium",
            "counterevidence": [],
            "limitations": [],
            "source_ids": [source_id],
        }


if __name__ == "__main__":
    unittest.main()
