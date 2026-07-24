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

    def test_graph_barrier_and_mechanism_evidence_chain(self):
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_node_research")
        contract = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))[
            "result_contract"
        ]
        self.assertEqual(contract["records"]["profiles"]["type"], "list of objects")
        self.assertIsInstance(contract["records"]["profiles"]["required_fields"], list)
        self.assertTrue(any("temporal_context" in rule for rule in contract["field_rules"]))
        self.assertIn(action["packet_id"], action["worker_prompt"])
        self.assertIn(action["suggested_result_path"], action["worker_prompt"])
        first_item = action["next_item_id"]
        node_type = json.loads(Path(action["packet_path"]).read_text())["context"]["node"][
            "node_type"
        ]
        self.submit(action, self.profile(first_item, node_type))
        self.assertEqual(core.status(self.root)["next_task"], "pathology_node_research")
        self.assertFalse((self.root / "results" / "evidence_graph.json").exists())

        action = core.next_action(self.root)
        second_item = action["next_item_id"]
        node_type = json.loads(Path(action["packet_path"]).read_text())["context"]["node"][
            "node_type"
        ]
        self.submit(action, self.profile(second_item, node_type))

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_seed_research")
        self.assertTrue((self.root / "results" / "evidence_graph.json").exists())
        self.assertTrue((self.root / "results" / "mechanism_clustering.json").exists())
        self.assertTrue(action["next_item_id"].startswith("CLUSTER-"))
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(packet["context"]["cluster"]["member_node_ids"], ["NODE:1"])
        self.assertEqual([row["node_id"] for row in packet["context"]["profiles"]], ["NODE:1"])
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
        with self.assertRaisesRegex(core.ProgramError, "outside the item cluster"):
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
        self.assertEqual(review_packet["context"]["cluster"]["cluster_id"], packet["item_id"])
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
            seeds["records"]["candidates"][0]["origin_cluster_ids"],
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

    def test_mechanism_clustering_is_a_deterministic_partition(self):
        nodes = [
            {"node_id": "NODE:1", "label": "mitochondrial energy", "node_type": "process"},
            {"node_id": "NODE:2", "label": "oxidative metabolism", "node_type": "process"},
            {"node_id": "NODE:3", "label": "RNA splicing", "node_type": "process"},
            {"node_id": "NODE:4", "label": "RNA processing", "node_type": "process"},
        ]
        profiles = [
            {"node_id": row["node_id"], "node_type": row["node_type"], "summary": row["label"]}
            for row in nodes
        ]
        graph = {
            "snapshot_id": "GRAPH:1",
            "records": {"source_nodes": nodes, "profiles": profiles},
        }
        reversed_graph = {
            "snapshot_id": "GRAPH:1",
            "records": {
                "source_nodes": list(reversed(nodes)),
                "profiles": list(reversed(profiles)),
            },
        }
        first = core._build_cluster_result({"evidence_graph": graph})
        second = core._build_cluster_result({"evidence_graph": reversed_graph})

        self.assertEqual(first["records"], second["records"])
        clusters = core._clusters(
            {"evidence_graph": graph, "mechanism_clustering": first}
        )
        self.assertEqual(len(clusters), 2)
        self.assertEqual(
            {node_id for cluster in clusters for node_id in cluster["member_node_ids"]},
            {row["node_id"] for row in nodes},
        )

    def test_cluster_text_omits_citations_but_keeps_structured_detail(self):
        self.assertEqual(
            core._cluster_text(
                {
                    "mechanism": "mitochondrial rescue",
                    "detail": "supported by PMID:123 and DOI:10.1000/example",
                    "source_ids": ["PMID:123"],
                }
            ),
            "mitochondrial rescue supported by and",
        )

    def test_pathology_research_requires_retained_evidence(self):
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
            "CLUSTER-A",
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
            "CLUSTER-B",
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
        self.assertEqual(merged[0]["origin_cluster_ids"], ["CLUSTER-A", "CLUSTER-B"])
        self.assertEqual(merged[0]["graph_node_ids"], ["NODE:A", "NODE:B"])

        wrong = self.candidate(
            "PUBCHEM:11125",
            "lithium carbonate",
            {
                "status": "resolved",
                "preferred_name": "lithium carbonate",
                "identifiers": {"pubchem_cid": "999"},
            },
            "CLUSTER-C",
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
            "CLUSTER-C",
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
            "CLUSTER-A",
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
            "CLUSTER-B",
            "NODE:B",
        )

        merged = core._merge_candidates([first, second])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["candidate_id"], "PUBCHEM:9848818")
        self.assertEqual(merged[0]["identity"]["identifiers"]["pubchem_cid"], "9848818")

    def test_review_batches_assign_each_candidate_once_to_strongest_origin(self):
        clusters = [
            {"cluster_id": "CLUSTER-A", "member_node_ids": ["NODE:A1", "NODE:A2"]},
            {"cluster_id": "CLUSTER-B", "member_node_ids": ["NODE:B1", "NODE:B2"]},
        ]
        candidates = [
            {
                "candidate_id": "DRUG-A",
                "origin_cluster_ids": ["CLUSTER-A"],
                "graph_node_ids": ["NODE:A1"],
            },
            {
                "candidate_id": "DRUG-B",
                "origin_cluster_ids": ["CLUSTER-A", "CLUSTER-B"],
                "graph_node_ids": ["NODE:A1", "NODE:B1", "NODE:B2"],
            },
            {
                "candidate_id": "DRUG-TIE",
                "origin_cluster_ids": ["CLUSTER-B", "CLUSTER-A"],
                "graph_node_ids": ["NODE:A2", "NODE:B2"],
            },
        ]
        results = {"candidate_seed_generation": {"records": {"candidates": candidates}}}

        with patch.object(core, "_clusters", return_value=clusters):
            first = core._review_batches(results)
            results["candidate_seed_generation"]["records"]["candidates"] = list(
                reversed(candidates)
            )
            second = core._review_batches(results)

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            [
                {"cluster_id": "CLUSTER-A", "candidate_ids": ["DRUG-A", "DRUG-TIE"]},
                {"cluster_id": "CLUSTER-B", "candidate_ids": ["DRUG-B"]},
            ],
        )

    def test_review_validation_requires_exact_batch_coverage(self):
        records = {
            "documents": [
                {"document_id": "PMID:1", "title": "Drug evidence", "source": "test"}
            ],
            "reviews": [self.review("DRUG-A"), self.review("DRUG-B")],
        }
        batch = [{"cluster_id": "CLUSTER-A", "candidate_ids": ["DRUG-A", "DRUG-B"]}]
        with patch.object(core, "_review_batches", return_value=batch):
            core._validate_review_item(records, "CLUSTER-A", {})
            records["documents"] = []
            with self.assertRaisesRegex(core.ProgramError, "retained by this review"):
                core._validate_review_item(
                    records,
                    "CLUSTER-A",
                    {"prior": {"records": {"documents": [
                        {"document_id": "PMID:1", "title": "Prior evidence", "source": "test"}
                    ]}}},
                )
            records["documents"] = [
                {"document_id": "PMID:1", "title": "Drug evidence", "source": "test"}
            ]
            records["reviews"] = [self.review("DRUG-A")]
            with self.assertRaisesRegex(core.ProgramError, "exactly the supplied batch"):
                core._validate_review_item(records, "CLUSTER-A", {})

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
    def candidate(candidate_id, name, identity, cluster_id, node_id):
        return {
            "candidate_id": candidate_id,
            "name": name,
            "identity": identity,
            "desired_change": f"change {node_id}",
            "mechanism_hypothesis": f"mechanism {node_id}",
            "graph_node_ids": [node_id],
            "pathology_source_ids": [f"PATH:{node_id}"],
            "mechanism_source_ids": [f"MOA:{node_id}"],
            "origin_cluster_ids": [cluster_id],
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
