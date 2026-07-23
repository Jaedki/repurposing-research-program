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
        self.assertEqual(action["next_item_id"], "NODE:1")
        self.submit(
            action,
            {
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
                    }
                ],
                "exclusions": [],
            },
        )
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_review_research")

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


if __name__ == "__main__":
    unittest.main()
