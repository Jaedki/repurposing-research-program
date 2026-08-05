import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import (  # noqa: E402
    bibliography,
    contracts,
    graph as graph_rules,
    orchestration,
    packets,
    pathology,
    run_state,
    storage,
)


def source_screening_result(*_args):
    return {
        "stage": "pathology_source_screening",
        "status": "complete",
        "resolved_disease": {"mondo_id": "MONDO:1", "name": "Disease"},
        "records": {"flagged_sentences": []},
        "gaps": [],
        "notes": [],
    }


def source_result(*_args):
    return {
        "stage": "pathology_sources",
        "status": "complete",
        "resolved_disease": {"mondo_id": "MONDO:1", "name": "Disease"},
        "records": {
            "documents": [
                {"document_id": "SRC:1", "title": "Pathology", "source": "test"}
            ],
            "source_nodes": [
                {
                    "node_id": "MONDO:1",
                    "label": "Disease",
                    "node_type": "disease_anchor",
                    "source_ids": ["SRC:1"],
                },
                {
                    "node_id": "NODE:1",
                    "label": "Broad process",
                    "node_type": "mechanism",
                    "description": "A broad disease mechanism",
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


def bibliographic_metadata(_root, documents):
    return {
        row["document_id"]: {
            "title": row["title"],
            "year": 2026,
            "journal": "Test journal",
            "authors": ["Test Author"],
            "canonical_publication_id": row["document_id"],
            "identifier_aliases": [row["document_id"]],
            "metadata_source": "test",
        }
        for row in documents
        if bibliography._normalized_publication_id(str(row["document_id"])) is not None
    }


def document(document_id, title):
    return {
        "document_id": document_id,
        "title": title,
        "source": "Asta-selected underlying paper",
        "evidence_passages": [
            {"text": f"Inspectable pathology evidence from {title}.", "locator": "abstract"}
        ],
    }


def proposal(label, claim, source_id, provisional_type="mechanism"):
    return {
        "label": label,
        "provisional_type": provisional_type,
        "claim": claim,
        "index_comparison": "Adds a more specific causal level than the initial index.",
        "source_ids": [source_id],
    }


ASTA_TEST_PAPER_ID = "3fabad2e28b0d9b09b98194d68f8c63862ede98a"


def asta_receipt(
    operation_id,
    tool,
    *,
    paper_id=None,
    attempt=1,
    request_profile="standard",
    outcome="completed",
    elapsed_seconds=1.0,
    result_count=1,
    error_type=None,
):
    return {
        "operation_id": operation_id,
        "tool": tool,
        "paper_id": paper_id,
        "attempt": attempt,
        "request_profile": request_profile,
        "outcome": outcome,
        "elapsed_seconds": elapsed_seconds,
        "result_count": result_count,
        "error_type": error_type,
    }


def completed_asta_receipts(result_count=1):
    receipts = [asta_receipt(
        "ASTA-OP-SEARCH-1",
        "search_papers_by_relevance",
        result_count=result_count,
    )]
    if result_count:
        receipts.extend([
            asta_receipt(
                "ASTA-OP-CITATIONS-1",
                "get_citations",
                paper_id=ASTA_TEST_PAPER_ID,
            ),
            asta_receipt(
                "ASTA-OP-SNIPPET-1",
                "snippet_search",
                paper_id=ASTA_TEST_PAPER_ID,
            ),
        ])
    return receipts


def unavailable_asta_receipts():
    return [
        asta_receipt(
            "ASTA-OP-SEARCH-1",
            "search_papers_by_relevance",
            outcome="no_response",
            elapsed_seconds=180,
            result_count=None,
            error_type="timeout",
        ),
        asta_receipt(
            "ASTA-OP-SEARCH-1",
            "search_papers_by_relevance",
            attempt=2,
            request_profile="minimal",
            outcome="no_response",
            elapsed_seconds=180,
            result_count=None,
            error_type="timeout",
        ),
    ]


class LandscapeWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = [
            patch.object(orchestration, "screen_pathology_sources", source_screening_result),
            patch.object(orchestration, "fetch_pathology_sources", source_result),
            patch.object(
                bibliography, "_resolve_bibliographic_metadata", bibliographic_metadata
            ),
            patch.dict(os.environ, {"ASTA_AI2_API_KEY": "TEST-SECRET-MUST-NOT-LEAK"}),
        ]
        for patcher in self.patchers:
            patcher.start()
        core.initialize(self.root, "Disease", gene="GENE1", mondo="MONDO:1")
        self.action = core.next_action(self.root)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def submit(self, records, gaps=None, extra_result=None):
        records = json.loads(json.dumps(records))
        records.setdefault(
            "asta_call_receipts",
            completed_asta_receipts(
                1 if records.get("documents") or records.get("landscape_proposals") else 0
            ),
        )
        result_gaps = [] if gaps is None else gaps
        result = {
            "stage": "pathology_landscape_scan",
            "item_id": None,
            "packet_id": self.action["packet_id"],
            "status": "complete",
            "records": records,
            "gaps": result_gaps,
        }
        if extra_result:
            result.update(extra_result)
        path = self.root / f"landscape-{len(list(self.root.glob('landscape-*')))}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return core.submit(self.root, path)

    def accepted_results(self):
        return run_state._load_results(self.root)

    def test_stage_appears_once_before_curation_and_packet_has_no_secret(self):
        self.assertEqual(contracts.STAGES.count("pathology_landscape_scan"), 1)
        self.assertEqual(
            contracts.STAGES.index("pathology_landscape_scan") + 1,
            contracts.STAGES.index("pathology_curation"),
        )
        self.assertEqual(self.action["next_task"], "pathology_landscape_scan")
        packet = json.loads(Path(self.action["packet_path"]).read_text(encoding="utf-8"))
        serialized = json.dumps(packet)
        self.assertNotIn("TEST-SECRET-MUST-NOT-LEAK", serialized)
        self.assertEqual(packets._secret_paths(packet), [])
        self.assertEqual(
            [row["node_id"] for row in packet["context"]["source_node_index"]],
            ["NODE:1"],
        )
        self.assertEqual(
            set(packet["result_contract"]["records"]),
            {"documents", "landscape_proposals", "asta_call_receipts"},
        )
        self.assertEqual(
            packet["result_contract"]["allowed_top_level_fields"],
            ["stage", "item_id", "packet_id", "status", "records", "gaps", "notes"],
        )
        receipt_contract = packet["result_contract"]["records"]["asta_call_receipts"]
        receipt_fields = receipt_contract["field_contracts"]
        self.assertEqual(
            receipt_fields["tool"]["allowed_values"],
            sorted(contracts.ASTA_CALL_TOOLS),
        )
        self.assertIn("bare logical", receipt_fields["tool"]["value_rule"])
        self.assertEqual(
            receipt_fields["outcome"]["allowed_values"],
            sorted(contracts.ASTA_CALL_OUTCOMES),
        )
        self.assertIn("not success", receipt_fields["outcome"]["value_rule"])
        self.assertEqual(
            receipt_fields["operation_id"]["pattern"],
            contracts.ASTA_OPERATION_ID_PATTERN,
        )
        self.assertTrue(any(
            "same tool and paper_id" in rule
            for rule in packet["result_contract"]["field_rules"]
        ))
        document_contract = packet["result_contract"]["records"]["documents"]
        self.assertEqual(
            document_contract,
            {"type": "list of objects", **contracts.RESEARCH_DOCUMENT_CONTRACT},
        )
        self.assertEqual(
            document_contract["required_fields"],
            ["document_id", "title", "source", "evidence_passages"],
        )
        self.assertEqual(
            document_contract["field_contracts"]["evidence_passages"],
            {
                "type": "non-empty list of objects",
                "required_fields": ["text", "locator"],
                "additional_fields": False,
                "value_rule": "text and locator must both be non-empty strings",
            },
        )
        self.assertIn("coverage_checklist", packet["context"])
        self.assertTrue(any("embedded" in rule for rule in packet["rules"]))
        self.assertTrue(any(
            "Literal research-document example" in rule for rule in packet["rules"]
        ))

    def test_packet_describes_the_simple_asta_discovery_and_curation_flow(self):
        packet = json.loads(Path(self.action["packet_path"]).read_text(encoding="utf-8"))
        task = packet["task"]
        field_rules = " ".join(packet["result_contract"]["field_rules"])
        runtime_rules = " ".join(packet["rules"])

        self.assertIn("search_papers_by_relevance", task)
        self.assertIn("get_citations", task)
        self.assertIn("snippet_search", task)
        self.assertIn("every related citation retained for evaluation", task)
        self.assertIn("following curation agent", task)
        self.assertNotIn("180 seconds", task)
        self.assertNotIn("request_profile", task)
        self.assertNotIn("asta_call_receipts", task)
        self.assertNotIn("provisional_type", task)

        self.assertIn("https://allenai.org/asta/resources/mcp", runtime_rules)
        self.assertIn("calls sequentially", runtime_rules)
        self.assertIn("180 seconds", runtime_rules)
        self.assertIn("smallest useful limit", runtime_rules)
        self.assertIn("untrusted evidence", runtime_rules)
        self.assertIn("block submission", runtime_rules)
        self.assertIn("raw MCP responses are transient", runtime_rules)
        self.assertNotIn("source_node_index", runtime_rules)

        self.assertIn("exactly one row per actual Asta call", field_rules)
        self.assertIn("same tool and paper_id", field_rules)
        self.assertIn("no_response", field_rules)
        self.assertIn("every paper passed to get_citations", field_rules)
        self.assertIn("positive completed relevance search", field_rules)
        self.assertIn("if no relevance search completes", field_rules)
        self.assertIn("terminal call failure", field_rules)
        self.assertNotIn("calls sequentially", field_rules)
        self.assertNotIn("smallest useful limit", field_rules)
        self.assertNotIn("untrusted evidence", field_rules)
        self.assertFalse(any("asta_operations" in rule for rule in packet["rules"]))
        self.assertNotIn("asta_operations", packet["result_contract"]["records"])
        self.assertIn("asta_call_receipts", packet["result_contract"]["records"])

    def test_valid_proposal_gets_a_normalized_deterministic_node_id(self):
        records = {
            "documents": [document("PMID:101", "Specific mechanism")],
            "landscape_proposals": [
                proposal(
                    "  Specific   Process ",
                    "Abnormal signalling causes cellular dysfunction.",
                    "PMID:101",
                )
            ],
        }
        status = self.submit(records)
        self.assertEqual(status["next_task"], "pathology_curation")
        first = pathology._landscape_source_nodes(self.accepted_results())[0]

        variant = self.accepted_results()
        variant["pathology_landscape_scan"] = json.loads(
            json.dumps(variant["pathology_landscape_scan"])
        )
        variant_proposal = variant["pathology_landscape_scan"]["records"][
            "landscape_proposals"
        ][0]
        variant_proposal["label"] = "specific process"
        variant_proposal["claim"] = "  abnormal SIGNALLING causes cellular dysfunction.  "
        second = pathology._landscape_source_nodes(variant)[0]

        self.assertRegex(first["node_id"], r"^ASTA-NODE-[A-F0-9]{24}$")
        self.assertEqual(first["node_id"], second["node_id"])

    def test_document_evidence_shape_and_supported_asta_identifiers_validate(self):
        semantic_scholar_id = "S2:" + "a" * 40
        status = self.submit({
            "documents": [
                document(semantic_scholar_id, "Semantic Scholar paper"),
                document("https://example.org/pathology-paper", "HTTPS paper"),
            ],
            "landscape_proposals": [
                proposal(
                    "Specific molecular process",
                    "A specific molecular process is abnormal in disease.",
                    semantic_scholar_id,
                ),
                proposal(
                    "Specific cellular process",
                    "A specific cellular process is abnormal in disease.",
                    "https://example.org/pathology-paper",
                ),
            ],
        })
        self.assertEqual(status["next_task"], "pathology_curation")

    def test_passage_alias_has_a_clear_validation_error(self):
        invalid = document("PMID:101", "Specific mechanism")
        invalid["evidence_passages"] = [
            {"passage": "Inspectable evidence.", "locator": "abstract"}
        ]
        with self.assertRaisesRegex(
            core.ProgramError, "must contain exactly text and locator"
        ):
            self.submit({
                "documents": [invalid],
                "landscape_proposals": [
                    proposal(
                        "Specific process",
                        "A disease-linked process is abnormal.",
                        "PMID:101",
                    )
                ],
            })

    def test_null_passage_values_are_not_stringified_into_evidence(self):
        invalid = document("PMID:101", "Specific mechanism")
        invalid["evidence_passages"] = [{"text": None, "locator": "abstract"}]
        with self.assertRaisesRegex(core.ProgramError, "must be non-empty strings"):
            self.submit({
                "documents": [invalid],
                "landscape_proposals": [
                    proposal(
                        "Specific process",
                        "A disease-linked process is abnormal.",
                        "PMID:101",
                    )
                ],
            })

    def test_unaccepted_landscape_packet_regenerates_without_touching_results(self):
        packet_path = Path(self.action["packet_path"])
        stale = json.loads(packet_path.read_text(encoding="utf-8"))
        stale["result_contract"]["records"]["documents"] = {
            "type": "list of objects",
            **contracts.ROW_SCHEMAS["documents"],
        }
        stale_unsigned = {
            key: value for key, value in stale.items() if key != "packet_id"
        }
        stale["packet_id"] = storage._stable_id("PACKET", stale_unsigned)
        packet_path.write_text(json.dumps(stale), encoding="utf-8")
        accepted_before = {
            path.relative_to(self.root): path.read_bytes()
            for path in (self.root / "results").rglob("*.json")
        }

        regenerated_action = core.next_action(self.root)
        regenerated = json.loads(packet_path.read_text(encoding="utf-8"))
        accepted_after = {
            path.relative_to(self.root): path.read_bytes()
            for path in (self.root / "results").rglob("*.json")
        }

        self.assertNotEqual(regenerated_action["packet_id"], stale["packet_id"])
        self.assertEqual(
            regenerated["result_contract"]["records"]["documents"],
            {"type": "list of objects", **contracts.RESEARCH_DOCUMENT_CONTRACT},
        )
        self.assertEqual(accepted_after, accepted_before)

    def test_duplicate_and_uncited_proposals_are_rejected(self):
        duplicate = proposal(
            "Specific Process",
            "Abnormal signalling causes cellular dysfunction.",
            "PMID:101",
        )
        with self.assertRaisesRegex(core.ProgramError, "duplicate normalized proposals"):
            self.submit({
                "documents": [document("PMID:101", "Specific mechanism")],
                "landscape_proposals": [
                    duplicate,
                    {
                        **duplicate,
                        "label": " specific   process ",
                        "claim": "ABNORMAL signalling causes cellular dysfunction.",
                    },
                ],
            })

        with self.assertRaisesRegex(core.ProgramError, "uncited"):
            self.submit({
                "documents": [
                    document("PMID:101", "Specific mechanism"),
                    document("PMID:102", "Unused paper"),
                ],
                "landscape_proposals": [duplicate],
            })

    def test_unknown_evidence_and_treatment_framing_are_rejected(self):
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit({
                "documents": [document("PMID:101", "Specific mechanism")],
                "landscape_proposals": [
                    proposal("Specific process", "Disease-linked abnormality.", "PMID:999")
                ],
            })
        with self.assertRaisesRegex(core.ProgramError, "treatment-framed"):
            self.submit({
                "documents": [document("PMID:101", "Specific mechanism")],
                "landscape_proposals": [
                    proposal(
                        "Treatment response",
                        "A drug treatment reverses the measured phenotype.",
                        "PMID:101",
                    )
                ],
            })

    def test_empty_scan_continues_existing_source_workflow(self):
        status = self.submit(
            {
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": unavailable_asta_receipts(),
            },
            gaps=["Asta was unavailable; Monarch and DisMech remain available."],
        )
        self.assertEqual(status["state"], "needs_agent")
        self.assertEqual(status["next_task"], "pathology_curation")

    def test_positive_search_cannot_abandon_citation_and_snippet_work(self):
        with self.assertRaisesRegex(
            core.ProgramError, "requires citation and snippet operations"
        ):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": completed_asta_receipts(1)[:1],
            })

    def test_no_response_requires_full_wait_and_one_minimal_retry(self):
        too_early = unavailable_asta_receipts()
        too_early[0]["elapsed_seconds"] = 45
        with self.assertRaisesRegex(core.ProgramError, "at least 180 elapsed seconds"):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": too_early,
            }, gaps=["Search did not respond."])

        no_retry = unavailable_asta_receipts()[:1]
        with self.assertRaisesRegex(core.ProgramError, "retry a failed call exactly once"):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": no_retry,
            }, gaps=["Search did not respond."])

    def test_terminal_citation_failure_still_requires_snippet_and_gap(self):
        receipts = completed_asta_receipts(1)[:1]
        receipts.extend([
            asta_receipt(
                "ASTA-OP-CITATIONS-1",
                "get_citations",
                paper_id=ASTA_TEST_PAPER_ID,
                outcome="no_response",
                elapsed_seconds=180,
                result_count=None,
                error_type="timeout",
            ),
            asta_receipt(
                "ASTA-OP-CITATIONS-1",
                "get_citations",
                paper_id=ASTA_TEST_PAPER_ID,
                attempt=2,
                request_profile="minimal",
                outcome="no_response",
                elapsed_seconds=180,
                result_count=None,
                error_type="timeout",
            ),
        ])
        with self.assertRaisesRegex(core.ProgramError, "followed by snippet_search"):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": receipts,
            }, gaps=["Citation expansion failed."])

        receipts.append(asta_receipt(
            "ASTA-OP-SNIPPET-1",
            "snippet_search",
            paper_id=ASTA_TEST_PAPER_ID,
        ))
        with self.assertRaisesRegex(core.ProgramError, "require an explicit gap"):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": receipts,
            })

        status = self.submit({
            "documents": [],
            "landscape_proposals": [],
            "asta_call_receipts": receipts,
        }, gaps=["Citation expansion failed after its minimal retry; snippet evaluation continued."])
        self.assertEqual(status["next_task"], "pathology_curation")

    def test_authentication_and_invalid_request_are_not_outages(self):
        for error_type in ("authentication", "invalid_request"):
            receipt = asta_receipt(
                "ASTA-OP-SEARCH-1",
                "search_papers_by_relevance",
                outcome="tool_error",
                result_count=None,
                error_type=error_type,
            )
            with self.subTest(error_type=error_type), self.assertRaisesRegex(
                core.ProgramError, "blocking .* defect"
            ):
                self.submit({
                    "documents": [],
                    "landscape_proposals": [],
                    "asta_call_receipts": [receipt],
                }, gaps=["Asta failed."])

    def test_receipts_reject_malformed_paper_ids(self):
        receipts = completed_asta_receipts(1)
        receipts[1]["paper_id"] = "not-an-asta-paper-id"
        with self.assertRaisesRegex(core.ProgramError, "documented Asta paper identifier"):
            self.submit({
                "documents": [],
                "landscape_proposals": [],
                "asta_call_receipts": receipts,
            })

    def test_result_rejects_secret_header_names_and_extra_top_level_payloads(self):
        secret_document = document("PMID:101", "Specific mechanism")
        secret_document["headers"] = {"x-api-key": "TEST-SECRET"}
        with self.assertRaisesRegex(core.ProgramError, "Credentials must never be persisted"):
            self.submit({
                "documents": [secret_document],
                "landscape_proposals": [
                    proposal(
                        "Specific process",
                        "A disease-linked process is abnormal.",
                        "PMID:101",
                    )
                ],
            })

        with self.assertRaisesRegex(core.ProgramError, "unexpected top-level fields"):
            self.submit(
                {
                    "documents": [],
                    "landscape_proposals": [],
                    "asta_call_receipts": completed_asta_receipts(0),
                },
                extra_result={"raw_mcp_response": {"results": []}},
            )

    def test_curation_partitions_original_and_asta_nodes_and_can_merge_equivalents(self):
        self.submit({
            "documents": [document("PMID:101", "Specific mechanism")],
            "landscape_proposals": [
                proposal(
                    "Broad process",
                    "A broad disease mechanism.",
                    "PMID:101",
                )
            ],
        })
        results = self.accepted_results()
        asta_id = pathology._landscape_source_nodes(results)[0]["node_id"]
        curation = core.next_action(self.root)
        incomplete = {
            "concepts": [{
                "concept_id": "NODE:1",
                "preferred_label": "Broad process",
                "concept_type": "mechanism",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable disease mechanism.",
                "related_concept_ids": [],
            }]
        }
        result = {
            "stage": "pathology_curation",
            "item_id": None,
            "packet_id": curation["packet_id"],
            "status": "complete",
            "records": incomplete,
            "gaps": [],
        }
        path = self.root / "curation-incomplete.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(core.ProgramError, "partition every supplied"):
            core.submit(self.root, path)

        result["records"]["concepts"][0]["member_node_ids"].append(asta_id)
        path = self.root / "curation-merged.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        status = core.submit(self.root, path)
        self.assertEqual(status["next_task"], "pathology_node_research")
        self.assertEqual(status["next_item_id"], "NODE:1")

    def test_distinct_mechanisms_remain_distinct_research_items(self):
        self.submit({
            "documents": [document("PMID:101", "Specific mechanism")],
            "landscape_proposals": [
                proposal(
                    "Specific downstream process",
                    "A distinct downstream abnormality causes cellular dysfunction.",
                    "PMID:101",
                )
            ],
        })
        results = self.accepted_results()
        asta_id = pathology._landscape_source_nodes(results)[0]["node_id"]
        action = core.next_action(self.root)
        concepts = [
            {
                "concept_id": node_id,
                "preferred_label": label,
                "concept_type": "mechanism",
                "member_node_ids": [node_id],
                "aliases": [],
                "disposition": "research",
                "reason": "A distinct abnormal process with its own desired biological state.",
                "related_concept_ids": [],
            }
            for node_id, label in (
                ("NODE:1", "Broad process"),
                (asta_id, "Specific downstream process"),
            )
        ]
        self._submit_curation(action, concepts)
        self.assertEqual(
            run_state._item_ids("evidence_graph", self.accepted_results()),
            sorted(["NODE:1", asta_id]),
        )

    def test_biomarker_context_does_not_create_a_research_item(self):
        self.submit({
            "documents": [document("PMID:101", "Biomarker paper")],
            "landscape_proposals": [
                proposal(
                    "Informative biomarker",
                    "The biomarker is elevated in affected tissue.",
                    "PMID:101",
                    provisional_type="biomarker",
                )
            ],
        })
        results = self.accepted_results()
        asta_id = pathology._landscape_source_nodes(results)[0]["node_id"]
        action = core.next_action(self.root)
        self._submit_curation(action, [
            {
                "concept_id": "NODE:1",
                "preferred_label": "Broad process",
                "concept_type": "mechanism",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable mechanism.",
                "related_concept_ids": [],
            },
            {
                "concept_id": asta_id,
                "preferred_label": "Informative biomarker",
                "concept_type": "context",
                "member_node_ids": [asta_id],
                "aliases": [],
                "disposition": "context_only",
                "reason": "Observational readout contextualizes the mechanism.",
                "related_concept_ids": ["NODE:1"],
            },
        ])
        self.assertEqual(
            run_state._item_ids("evidence_graph", self.accepted_results()), ["NODE:1"]
        )

    def test_researchable_phenotype_and_measurement_biomarker_remain_distinct(self):
        self.submit({
            "documents": [
                document("PMID:101", "Distinct phenotype"),
                document("PMID:102", "Measurement biomarker"),
            ],
            "landscape_proposals": [
                proposal(
                    "Distinct functional phenotype",
                    "A modifiable functional state defines a separate intervention objective.",
                    "PMID:101",
                    provisional_type="phenotype",
                ),
                proposal(
                    "Damage marker",
                    "The measured marker tracks the functional pathology.",
                    "PMID:102",
                    provisional_type="biomarker",
                ),
            ],
        })
        nodes = {
            row["label"]: row["node_id"]
            for row in pathology._landscape_source_nodes(self.accepted_results())
        }
        phenotype_id = nodes["Distinct functional phenotype"]
        biomarker_id = nodes["Damage marker"]
        action = core.next_action(self.root)
        self._submit_curation(action, [
            {
                "concept_id": "NODE:1",
                "preferred_label": "Broad process",
                "concept_type": "mechanism",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable mechanism.",
                "related_concept_ids": [],
            },
            {
                "concept_id": phenotype_id,
                "preferred_label": "Distinct functional phenotype",
                "concept_type": "phenotype",
                "member_node_ids": [phenotype_id],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable pathology and intervention objective.",
                "related_concept_ids": [],
            },
            {
                "concept_id": biomarker_id,
                "preferred_label": "Damage marker",
                "concept_type": "context",
                "member_node_ids": [biomarker_id],
                "aliases": [],
                "disposition": "context_only",
                "reason": "Measurement-only readout of the functional pathology.",
                "related_concept_ids": [phenotype_id],
            },
        ])
        results = self.accepted_results()
        self.assertEqual(
            run_state._item_ids("evidence_graph", results),
            sorted(["NODE:1", phenotype_id]),
        )
        _, edges = pathology._canonical_source_records(results)
        self.assertTrue(any(
            row["subject_id"] == biomarker_id
            and row["relation"] == "contextualizes"
            and row["object_id"] == phenotype_id
            for row in edges
        ))

    def test_retained_asta_concept_and_source_reach_its_research_packet(self):
        self.submit({
            "documents": [document("PMID:101", "Specific mechanism")],
            "landscape_proposals": [
                proposal(
                    "Specific downstream process",
                    "A distinct downstream abnormality causes cellular dysfunction.",
                    "PMID:101",
                )
            ],
        })
        results = self.accepted_results()
        asta_id = pathology._landscape_source_nodes(results)[0]["node_id"]
        action = core.next_action(self.root)
        self._submit_curation(action, [
            {
                "concept_id": asta_id,
                "preferred_label": "Specific downstream process",
                "concept_type": "mechanism",
                "member_node_ids": [asta_id],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable mechanism.",
                "related_concept_ids": [],
            },
            {
                "concept_id": "NODE:1",
                "preferred_label": "Broad process",
                "concept_type": "context",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "context_only",
                "reason": "The broad claim contextualizes the specific mechanism.",
                "related_concept_ids": [asta_id],
            },
        ])
        research = core.next_action(self.root)
        packet = json.loads(Path(research["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(research["next_item_id"], asta_id)
        self.assertEqual(
            [row["node_id"] for row in packet["context"]["member_source_nodes"]],
            [asta_id],
        )
        self.assertIn(
            "PMID:101",
            [row["document_id"] for row in packet["context"]["source_index"]],
        )

        shallow_only_result = {
            "stage": "pathology_node_research",
            "item_id": asta_id,
            "packet_id": research["packet_id"],
            "status": "complete",
            "records": {
                "documents": [document("PMID:101", "Specific mechanism")],
                "profiles": [{
                    "node_id": asta_id,
                    "node_type": "mechanism",
                    "summary": "Specific downstream pathology.",
                    "normal_state": "Regulated signalling.",
                    "pathological_state": "Abnormal signalling.",
                    "desired_biological_state": "Decrease abnormal signalling.",
                    "secondary_desired_states": [],
                    "phenotype_objective": "Reduce disease-linked cellular dysfunction.",
                    "established_pathology_observations": [],
                    "causal_role": "Contributes to dysfunction.",
                    "mechanisms": [],
                    "cell_types": [],
                    "anatomical_context": [],
                    "temporal_context": [],
                    "upstream_causes": [],
                    "downstream_consequences": [],
                    "contradictions": [],
                    "gaps": [],
                    "uncertainty": "Model generalization remains uncertain.",
                    "source_ids": ["PMID:101"],
                }],
                "assertions": [],
            },
            "gaps": [],
        }
        shallow_path = self.root / "shallow-only-research.json"
        shallow_path.write_text(json.dumps(shallow_only_result), encoding="utf-8")
        with self.assertRaisesRegex(core.ProgramError, "newly researched evidence"):
            core.submit(self.root, shallow_path)

        deep_result = json.loads(json.dumps(shallow_only_result))
        deep_result["records"]["documents"] = [
            document("PMID:103", "Deep mechanism research")
        ]
        deep_result["records"]["profiles"][0]["source_ids"] = [
            "PMID:101", "PMID:103"
        ]
        deep_path = self.root / "deep-research.json"
        deep_path.write_text(json.dumps(deep_result), encoding="utf-8")
        core.submit(self.root, deep_path)
        seed_action = core.next_action(self.root)
        self.assertEqual(seed_action["next_task"], "candidate_seed_research")
        self.assertEqual(seed_action["next_item_id"], asta_id)
        graph_result = json.loads(
            (self.root / "results" / "evidence_graph.json").read_text(encoding="utf-8")
        )
        graph_document_ids = {
            row["document_id"] for row in graph_result["records"]["documents"]
        }
        self.assertTrue({"PMID:101", "PMID:103"} <= graph_document_ids)

    def test_excluded_asta_document_does_not_enter_frozen_graph(self):
        self.submit({
            "documents": [
                document("PMID:101", "Retained context"),
                document("PMID:102", "Excluded proposal"),
            ],
            "landscape_proposals": [
                proposal(
                    "Retained context",
                    "A defining phenotype accompanies the broad process.",
                    "PMID:101",
                    provisional_type="phenotype",
                ),
                proposal(
                    "Irrelevant observation",
                    "An observation is not relevant to the disease mechanism.",
                    "PMID:102",
                    provisional_type="context",
                ),
            ],
        })
        results = self.accepted_results()
        nodes = {
            row["label"]: row["node_id"]
            for row in pathology._landscape_source_nodes(results)
        }
        retained_id = nodes["Retained context"]
        excluded_id = nodes["Irrelevant observation"]
        action = core.next_action(self.root)
        self._submit_curation(action, [
            {
                "concept_id": "NODE:1",
                "preferred_label": "Broad process",
                "concept_type": "mechanism",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct modifiable mechanism.",
                "related_concept_ids": [],
            },
            {
                "concept_id": retained_id,
                "preferred_label": "Retained context",
                "concept_type": "phenotype",
                "member_node_ids": [retained_id],
                "aliases": [],
                "disposition": "context_only",
                "reason": "Phenotype contextualizes the broad mechanism.",
                "related_concept_ids": ["NODE:1"],
            },
            {
                "concept_id": excluded_id,
                "preferred_label": "Irrelevant observation",
                "concept_type": "context",
                "member_node_ids": [excluded_id],
                "aliases": [],
                "disposition": "exclude",
                "reason": "Not relevant to the disease mechanism.",
                "related_concept_ids": [],
            },
        ])
        results = self.accepted_results()
        graph = graph_rules._assemble_graph_result(
            results,
            [{"node_id": "NODE:1", "source_ids": ["PMID:999"]}],
            [],
            [document("PMID:999", "Deep research")],
            [],
        )
        document_ids = {
            row["document_id"] for row in graph["records"]["documents"]
        }
        self.assertIn("PMID:101", document_ids)
        self.assertNotIn("PMID:102", document_ids)

    def _submit_curation(self, action, concepts):
        result = {
            "stage": "pathology_curation",
            "item_id": None,
            "packet_id": action["packet_id"],
            "status": "complete",
            "records": {"concepts": concepts},
            "gaps": [],
        }
        path = self.root / f"curation-{len(list(self.root.glob('curation-*')))}.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return core.submit(self.root, path)


class CurrentWorkflowContractTest(unittest.TestCase):
    def test_incompatible_objective_or_stage_sequence_requires_a_fresh_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core.initialize(root, "Disease", mondo="MONDO:1")
            case = json.loads((root / "case.json").read_text(encoding="utf-8"))
            case["objective"] = "A different objective"
            case["case_id"] = storage._stable_id("CASE", {
                "disease": case["disease"],
                "gene": case["gene"],
                "mondo": case["mondo"],
                "objective": case["objective"],
            })
            (root / "case.json").write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaisesRegex(core.ProgramError, "start a fresh run"):
                core.status(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core.initialize(root, "Disease", mondo="MONDO:1")
            storage._result_path(root, core.STAGES[1]).write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(core.ProgramError, "start a fresh run"):
                core.status(root)


class DocumentationContractTest(unittest.TestCase):
    def test_skill_and_references_describe_one_consistent_landscape_barrier(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / "SKILL.md").read_text(encoding="utf-8")
        architecture = (root / "references" / "architecture.md").read_text(
            encoding="utf-8"
        )
        packet_contract = (root / "references" / "packet-contract.md").read_text(
            encoding="utf-8"
        )
        adapters = (root / "references" / "source-adapters.md").read_text(
            encoding="utf-8"
        )

        self.assertLess(
            skill.index("pathology-only source normalization"),
            skill.index("`pathology_landscape_scan`"),
        )
        self.assertLess(
            skill.index("`pathology_landscape_scan`"),
            skill.index("one constrained curation packet"),
        )
        self.assertLess(
            architecture.index("`pathology_sources`"),
            architecture.index("`pathology_landscape_scan`"),
        )
        self.assertLess(
            architecture.index("`pathology_landscape_scan`"),
            architecture.index("`pathology_curation`"),
        )
        self.assertIn("Zero proposals are valid", packet_contract)
        self.assertIn("S2:` followed by 40 hexadecimal characters", packet_contract)
        self.assertIn("Asta is not a Python source adapter", adapters)
        self.assertIn("ASTA_AI2_API_KEY", skill)
        self.assertIn("ASTA_AI2_API_KEY", adapters)


class SparseSourceWorkflowTest(unittest.TestCase):
    def test_landscape_scan_can_rescue_an_empty_initial_concept_index(self):
        sparse = source_result()
        sparse["records"]["source_nodes"] = sparse["records"]["source_nodes"][:1]
        sparse["records"]["source_edges"] = []
        sparse["records"]["source_receipts"][0]["record_count"] = 1
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(orchestration, "screen_pathology_sources", source_screening_result),
            patch.object(orchestration, "fetch_pathology_sources", return_value=sparse),
            patch.object(
                bibliography, "_resolve_bibliographic_metadata", bibliographic_metadata
            ),
        ):
            root = Path(directory)
            core.initialize(root, "Disease", mondo="MONDO:1")
            action = core.next_action(root)
            self.assertEqual(action["next_task"], "pathology_landscape_scan")
            packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
            self.assertEqual(packet["context"]["source_node_index"], [])
            result = {
                "stage": "pathology_landscape_scan",
                "item_id": None,
                "packet_id": action["packet_id"],
                "status": "complete",
                "records": {
                    "documents": [document("PMID:101", "Rescue mechanism")],
                    "landscape_proposals": [
                        proposal(
                            "Rescue mechanism",
                            "A disease-linked molecular defect causes cellular dysfunction.",
                            "PMID:101",
                        )
                    ],
                    "asta_call_receipts": completed_asta_receipts(1),
                },
                "gaps": [],
            }
            path = root / "landscape.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            status = core.submit(root, path)
            self.assertEqual(status["next_task"], "pathology_curation")


if __name__ == "__main__":
    unittest.main()
