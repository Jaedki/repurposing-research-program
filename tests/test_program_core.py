import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import bibliography  # noqa: E402


PROGRAM_BASELINE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "program_baseline.json").read_text(
        encoding="utf-8"
    )
)


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


def source_screening_result(*_args):
    return {
        "stage": "pathology_source_screening",
        "status": "complete",
        "resolved_disease": {"mondo_id": "MONDO:1", "name": "Disease"},
        "records": {"flagged_sentences": []},
        "gaps": [],
        "notes": [],
    }


def unichem_result(endpoint, body):
    compound = str(body["compound"])
    uci = "1" if compound.endswith("1") else "2"
    source_id = int(body.get("sourceID", 1))
    if endpoint == "compounds":
        return {
            "response": "Success",
            "compounds": [{
                "uci": int(uci),
                "standardInchiKey": f"CONNECTIVITY{uci.zfill(2)}-STEREO-{uci}",
                "sources": [{
                    "id": source_id,
                    "shortName": "chembl",
                    "compoundId": compound,
                }],
            }],
            "notFound": [],
        }
    return {
        "response": "Success",
        "searchedCompound": {"uci": int(uci)},
        "sources": [{"id": source_id, "compoundId": compound}],
    }


def bibliographic_metadata(_root, documents):
    resolved = {}
    for row in documents:
        document_id = str(row["document_id"])
        normalized = core._normalized_publication_id(document_id)
        if normalized is None:
            continue
        resolved[document_id] = {
            "title": row["title"],
            "year": 2026,
            "journal": "Test journal",
            "authors": ["Test Author"],
            "canonical_publication_id": normalized,
            "identifier_aliases": [normalized],
            "metadata_source": "test",
        }
    return resolved


class UniChemTransportTest(unittest.TestCase):
    def test_accepts_explicit_compound_not_found_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "compounds": [],
            "response": "Not found",
        }).encode()
        with patch.object(core, "urlopen", return_value=response) as request:
            result = core._post_unichem(
                "compounds",
                {"compound": "CHEMBL1201607", "type": "sourceID", "sourceID": 1},
            )

        self.assertEqual(result["response"], "Not found")
        self.assertEqual(result["compounds"], [])
        self.assertEqual(request.call_count, 1)

    def test_rejects_non_success_response_with_http_200(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "response": "Error",
            "message": "The query could not be processed",
        }).encode()
        with patch.object(core, "urlopen", return_value=response) as request:
            with self.assertRaisesRegex(
                core.ProgramError, "UniChem compounds returned an invalid response"
            ):
                core._post_unichem(
                    "compounds",
                    {"compound": "4021", "type": "sourceID", "sourceID": 22},
                )

        self.assertEqual(request.call_count, 1)

    def test_retries_transient_server_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "response": "Success",
            "compounds": [],
        }).encode()
        error = HTTPError("https://example.org", 500, "server error", {}, None)
        with (
            patch.object(core, "urlopen", side_effect=[error, response]) as request,
            patch.object(core.time, "sleep") as pause,
        ):
            result = core._post_unichem(
                "compounds", {"compound": "4021", "type": "sourceID", "sourceID": 22}
            )

        self.assertEqual(result["response"], "Success")
        self.assertEqual(request.call_count, 2)
        pause.assert_called_once_with(1)
        error.close()


class BibliographicMetadataTest(unittest.TestCase):
    def test_metadata_transport_retries_transient_server_failure(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"record":"canonical"}'
        error = HTTPError("https://example.org", 503, "unavailable", {}, None)
        with (
            patch.object(bibliography, "urlopen", side_effect=[error, response]) as request,
            patch.object(bibliography.time, "sleep") as pause,
        ):
            result = core._bibliographic_get("https://example.org/record")

        self.assertEqual(result, {"record": "canonical"})
        self.assertEqual(request.call_count, 2)
        pause.assert_called_once_with(1)
        error.close()

    def test_cached_request_fetches_once_and_reuses_immutable_response(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_get", return_value={"record": "canonical"}
        ) as fetch:
            root = Path(directory)
            first = core._bibliographic_request(root, "test", "https://example.org/record")
            second = core._bibliographic_request(root, "test", "https://example.org/record")

        self.assertEqual(first, {"record": "canonical"})
        self.assertEqual(second, first)
        fetch.assert_called_once_with("https://example.org/record", accept="application/json")

    def test_identifier_converter_indexes_every_returned_publication_alias(self):
        response = {
            "records": [{
                "pmid": "11",
                "pmcid": "PMC11",
                "doi": "10.1000/eleven",
            }]
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_request", return_value=response
        ):
            records = core._id_converter_records(
                Path(directory), ["PMID:11", "PMCID:PMC11", "DOI:10.1000/ELEVEN"]
            )

        self.assertEqual(
            set(records),
            {"PMID:11", "PMCID:PMC11", "DOI:10.1000/eleven"},
        )

    def test_large_identifier_sets_are_split_into_bounded_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_request", return_value={"records": []}
        ) as request:
            core._id_converter_records(
                Path(directory), [f"PMID:{identifier}" for identifier in range(1, 202)]
            )

        self.assertEqual(request.call_count, 2)

    def test_resolver_covers_pmid_pmcid_doi_and_ignores_other_ids(self):
        documents = [
            {"document_id": "PMID:11", "title": "Eleven"},
            {"document_id": "PMCID:PMC22", "title": "Twenty two"},
            {"document_id": "DOI:10.1000/example", "title": "DOI article"},
            {"document_id": "https://example.org/database", "title": "Database"},
        ]
        converted = {
            "PMID:11": {"pmid": "11", "pmcid": "PMC11", "doi": "10.1000/eleven"},
            "PMCID:PMC22": {"pmcid": "PMC22"},
        }

        def summaries(_root, database, identifiers):
            self.assertEqual(set(identifiers), {"11"} if database == "pubmed" else {"22"})
            if database == "pubmed":
                return {"11": {
                    "title": "Eleven",
                    "pubdate": "2020 Jan",
                    "fulljournalname": "Journal A",
                    "authors": [{"name": "A Author"}],
                    "articleids": [],
                }}
            return {"22": {
                "title": "Twenty two",
                "pubdate": "2021",
                "source": "Journal B",
                "authors": [],
                "articleids": [{"idtype": "pmc", "value": "PMC22"}],
            }}

        doi_metadata = {
            "title": "DOI article",
            "year": 2022,
            "journal": "Journal C",
            "authors": ["C Author"],
            "identifier_aliases": ["DOI:10.1000/example"],
            "metadata_source": "DOI",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bibliography, "_id_converter_records", return_value=converted),
            patch.object(bibliography, "_ncbi_summaries", side_effect=summaries),
            patch.object(bibliography, "_doi_metadata", return_value=doi_metadata) as doi,
        ):
            resolved = core._resolve_bibliographic_metadata(Path(directory), documents)

        self.assertEqual(set(resolved), {"PMID:11", "PMCID:PMC22", "DOI:10.1000/example"})
        self.assertEqual(resolved["PMID:11"]["canonical_publication_id"], "PMID:11")
        self.assertEqual(
            resolved["PMID:11"]["identifier_aliases"],
            ["DOI:10.1000/eleven", "PMCID:PMC11", "PMID:11"],
        )
        self.assertEqual(resolved["PMCID:PMC22"]["canonical_publication_id"], "PMCID:PMC22")
        self.assertEqual(
            resolved["DOI:10.1000/example"]["canonical_publication_id"],
            "DOI:10.1000/example",
        )
        doi.assert_called_once()

    def test_missing_canonical_publication_metadata_stops_validation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bibliography, "_id_converter_records", return_value={}),
            patch.object(bibliography, "_ncbi_summaries", return_value={}),
        ):
            with self.assertRaisesRegex(core.ProgramError, "Canonical metadata was not found"):
                core._resolve_bibliographic_metadata(
                    Path(directory), [{"document_id": "PMID:999", "title": "Invented"}]
                )

    def test_one_worker_result_cannot_return_two_aliases_for_one_publication(self):
        documents = [
            {"document_id": "PMID:11", "title": "One article"},
            {"document_id": "DOI:10.1000/one", "title": "One article"},
        ]
        metadata = {
            document["document_id"]: {
                "title": "One article",
                "canonical_publication_id": "PMID:11",
                "identifier_aliases": ["PMID:11", "DOI:10.1000/one"],
            }
            for document in documents
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            with self.assertRaisesRegex(core.ProgramError, "identify the same publication"):
                core._validate_bibliographic_documents(
                    Path(directory), {"documents": documents}
                )

    def test_doi_metadata_projection_is_bounded_and_canonical(self):
        response = {
            "title": "A DOI article",
            "container-title": "A Journal",
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "issued": {"date-parts": [[2024, 2, 1]]},
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_request", return_value=response
        ):
            metadata = core._doi_metadata(Path(directory), "10.1000/example")

        self.assertEqual(metadata["title"], "A DOI article")
        self.assertEqual(metadata["journal"], "A Journal")
        self.assertEqual(metadata["authors"], ["Ada Lovelace"])
        self.assertEqual(metadata["year"], 2024)


class ArtifactPersistenceTest(unittest.TestCase):
    def test_write_once_rejects_conflicting_accepted_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results" / "items" / "accepted.json"
            accepted = b'{"status":"complete"}\n'
            replacement = b'{"status":"complete","different":true}\n'
            core._write_once(path, accepted)

            with self.assertRaisesRegex(
                core.ProgramError, "Immutable artifact conflicts with existing file"
            ):
                core._write_once(path, replacement)

            self.assertEqual(path.read_bytes(), accepted)


class InstructionContractTest(unittest.TestCase):
    def test_validation_failure_stops_instead_of_retrying_automatically(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(skill.split())
        self.assertIn("stop and report the exact validation error", normalized)
        self.assertIn("Do not retry automatically", normalized)
        self.assertNotIn("give the same packet to another new agent", normalized)


class SourceAdjudicationWorkflowTest(unittest.TestCase):
    def test_source_validation_still_rejects_treatment_fields_in_pathology_content(self):
        result = source_result()
        core._validate_source_result(result)
        result["records"]["source_nodes"][1]["treatment"] = "not pathology"

        with self.assertRaisesRegex(core.ProgramError, "Treatment fields reached"):
            core._validate_source_result(result)

    def test_batched_adjudication_is_complete_bounded_and_applied_once(self):
        sentences = (
            "The approved HGNC symbol is SOD1.",
            "Patients on riluzole showed improved survival.",
        )
        screening = {
            "stage": "pathology_source_screening",
            "status": "complete",
            "resolved_disease": {"mondo_id": "MONDO:1", "name": "Disease"},
            "records": {
                "flagged_sentences": [
                    {
                        "sentence_id": core._stable_id("DISMECH-SENTENCE", sentence),
                        "sentence": sentence,
                        "signals": ["treatment_language"] if index == 0 else ["treatment_event"],
                        "paths": [f"$.pathophysiology[{index}].description"],
                    }
                    for index, sentence in enumerate(sentences)
                ]
            },
            "gaps": [],
            "notes": [],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(core, "screen_pathology_sources", return_value=screening),
            patch.object(core, "fetch_pathology_sources", side_effect=source_result) as fetch,
        ):
            root = Path(directory)
            core.initialize(root, "Disease", mondo="MONDO:1")
            action = core.next_action(root)

            self.assertEqual(action["next_task"], "pathology_source_adjudication")
            packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
            self.assertEqual(
                packet["context"]["flagged_sentences"],
                screening["records"]["flagged_sentences"],
            )
            self.assertNotIn("documents", packet["result_contract"]["records"])
            self.assertTrue(any("do not search" in rule.lower() for rule in packet["rules"]))

            incomplete = {
                "stage": action["next_task"],
                "item_id": None,
                "packet_id": action["packet_id"],
                "status": "complete",
                "records": {
                    "sentence_decisions": [
                        {
                            "sentence_id": screening["records"]["flagged_sentences"][0][
                                "sentence_id"
                            ],
                            "decision": "retain_pathology",
                            "reason": "Approved describes the gene symbol, not an intervention.",
                        }
                    ]
                },
                "gaps": [],
            }
            submission = root / "incomplete.json"
            submission.write_text(json.dumps(incomplete), encoding="utf-8")
            with self.assertRaisesRegex(core.ProgramError, "partition every flagged sentence"):
                core.submit(root, submission)

            decisions = [
                {
                    "sentence_id": screening["records"]["flagged_sentences"][0]["sentence_id"],
                    "decision": "retain_pathology",
                    "reason": "Approved describes nomenclature rather than treatment.",
                },
                {
                    "sentence_id": screening["records"]["flagged_sentences"][1]["sentence_id"],
                    "decision": "exclude_treatment",
                    "reason": "The sentence reports patient exposure and a clinical outcome.",
                },
            ]
            complete = {**incomplete, "records": {"sentence_decisions": decisions}}
            submission = root / "complete.json"
            submission.write_text(json.dumps(complete), encoding="utf-8")
            core.submit(root, submission)

            action = core.next_action(root)
            self.assertEqual(action["next_task"], "pathology_curation")
            curation_packet = Path(action["packet_path"]).read_text(encoding="utf-8")
            self.assertNotIn(sentences[0], curation_packet)
            self.assertNotIn(sentences[1], curation_packet)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(
                fetch.call_args.args[3],
                {row["sentence_id"]: row["decision"] for row in decisions},
            )


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.screening_patch = patch.object(
            core, "screen_pathology_sources", source_screening_result
        )
        self.screening_patch.start()
        self.patch = patch.object(core, "fetch_pathology_sources", source_result)
        self.patch.start()
        self.unichem_patch = patch.object(core, "_post_unichem", unichem_result)
        self.unichem_patch.start()
        self.bibliographic_patch = patch.object(
            bibliography, "_resolve_bibliographic_metadata", bibliographic_metadata
        )
        self.bibliographic_patch.start()
        core.initialize(self.root, "Disease", mondo="MONDO:1")

    def tearDown(self):
        self.screening_patch.stop()
        self.patch.stop()
        self.unichem_patch.stop()
        self.bibliographic_patch.stop()
        self.temp.cleanup()

    def submit(self, action, records, *, add_evidence_passages=True):
        records = json.loads(json.dumps(records))
        if add_evidence_passages:
            for document in records.get("documents", []):
                document.setdefault("evidence_passages", [{
                    "text": f"Inspectable evidence from {document['title']}",
                    "locator": "test fixture",
                }])
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

    def test_empty_source_screening_skips_the_adjudication_agent(self):
        action = core.next_action(self.root)

        self.assertEqual(action["next_task"], "pathology_curation")
        adjudication = json.loads(
            (self.root / "results" / "pathology_source_adjudication.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(adjudication["records"]["sentence_decisions"], [])
        self.assertFalse(
            (self.root / "packets" / "pathology_source_adjudication.json").exists()
        )

    def test_graph_barrier_and_mechanism_evidence_chain(self):
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_curation")
        curation_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertNotIn("objective", curation_packet)
        self.assertEqual(
            set(curation_packet["case"]),
            {"case_id", "disease", "gene", "mondo"},
        )
        self.assertEqual(action["display_item_id"], "pathology_curation")
        invalid_packet = {
            key: value for key, value in curation_packet.items() if key != "packet_id"
        }
        invalid_packet["objective"] = core.OBJECTIVE
        with self.assertRaisesRegex(core.ProgramError, "global objective"):
            core._validate_packet(invalid_packet, "pathology_curation", None)
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
        self.assertEqual(
            action["display_item_id"],
            f"pathology_node_research/NODE:1/{core._item_token('NODE:1')}",
        )
        self.assertIn(action["display_item_id"], action["worker_prompt"])
        node_type = research_packet["context"]["node"]["node_type"]
        self.assertEqual(
            research_packet["context"]["disease_context"][0]["section"],
            "description",
        )
        pathology_records = self.profile(first_item, node_type)
        pathology_records["documents"].append(
            {"document_id": "PMID:90", "title": "Unused pathology search hit", "source": "test"}
        )
        self.submit(action, pathology_records)
        accepted_pathology = json.loads(
            core._item_result_path(self.root, "pathology_node_research", first_item).read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in accepted_pathology["records"]["documents"]],
            ["PMID:1", "PMID:90"],
        )

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_seed_research")
        self.assertTrue((self.root / "results" / "evidence_graph.json").exists())
        graph_result = json.loads(
            (self.root / "results" / "evidence_graph.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in graph_result["records"]["documents"]],
            ["PMID:1", "SRC:1"],
        )
        self.assertEqual(action["next_item_id"], "NODE:1")
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        candidate_contract = packet["result_contract"]["records"]["candidates"]
        self.assertEqual(
            candidate_contract,
            {"type": "list of objects", **core.ROW_SCHEMAS["candidates"]},
        )
        self.assertIn("identifiers", candidate_contract["required_fields"])
        self.assertNotIn("identity", candidate_contract["required_fields"])
        self.assertEqual(packet["context"]["focal_context"]["node"]["node_id"], "NODE:1")
        self.assertEqual(packet["context"]["focal_context"]["profile"]["node_id"], "NODE:1")
        self.assertEqual(
            [row["node_id"] for row in packet["context"]["graph_index"]], ["NODE:1"]
        )
        self.assertEqual(packet["context"]["graph_snapshot_id"], core.graph_context(
            self.root, "NODE:1"
        )["graph_snapshot_id"])
        self.assertEqual(packet["context"]["context_lookup"]["argv"][-1], "<node_id>")
        with self.assertRaisesRegex(core.ProgramError, "disease anchor"):
            core.graph_context(self.root, "MONDO:1")
        seed_records = {
            "documents": [
                {"document_id": "PMID:1", "title": "Re-emitted pathology evidence", "source": "test"},
                {"document_id": "PMID:2", "title": "Drug MOA", "source": "test"},
                {"document_id": "PMID:80", "title": "Unused seed search hit", "source": "test"},
            ],
            "candidates": [
                {
                    "candidate_id": "CHEMBL:1",
                    "name": "Drug",
                    "identifiers": {"chembl": "CHEMBL1"},
                    "mechanism_hypothesis": "inhibits the process",
                    "graph_node_ids": ["NODE:1"],
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:1", "PMID:2"],
                },
                {
                    "candidate_id": "CHEMBL:2",
                    "name": "Second drug",
                    "identifiers": {"chembl": "CHEMBL2"},
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
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit(action, invalid_records)
        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["candidates"][0]["unexpected_field"] = "not in the contract"
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            self.submit(action, invalid_records)
        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["candidates"][0]["identifiers"] = "CHEMBL1"
        with self.assertRaisesRegex(core.ProgramError, "identifiers must be an object"):
            self.submit(action, invalid_records)
        self.submit(
            action,
            seed_records,
        )
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_evidence_review")
        self.assertTrue((self.root / "results" / "candidate_identity.json").exists())
        self.assertEqual(action["next_item_id"], packet["item_id"])
        review_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn("authoritative disease context", review_packet["task"])
        self.assertIn("exact-disease prior art", review_packet["task"])
        self.assertTrue(
            any(
                "document retained" in rule
                for rule in review_packet["result_contract"]["field_rules"]
            )
        )
        self.assertNotIn("score_rubric", review_packet["result_contract"])
        self.assertEqual(review_packet["context"]["primary_concept_id"], packet["item_id"])
        self.assertEqual(
            [row["candidate_id"] for row in review_packet["context"]["candidates"]],
            ["UNICHEM:1", "UNICHEM:2"],
        )
        self.assertEqual(
            [row["document_id"] for row in review_packet["context"]["source_index"]],
            ["PMID:1", "PMID:2"],
        )
        seeds = json.loads(
            (self.root / "results" / "candidate_seed_generation.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in seeds["records"]["documents"]],
            ["PMID:2"],
        )
        self.assertEqual(
            seeds["records"]["candidates"][0]["origin_concept_ids"],
            [packet["item_id"]],
        )
        self.submit(
            action,
            {
                "documents": [
                    {"document_id": "PMID:2", "title": "Re-emitted seed evidence", "source": "test"},
                    {"document_id": "PMID:3", "title": "Drug review", "source": "test"},
                    {"document_id": "PMID:70", "title": "Unused review search hit", "source": "test"},
                ],
                "reviews": [
                    {
                        **self.review("UNICHEM:1", "PMID:3"),
                        "aliases": [
                            {"name": "Drug hydrochloride", "source_ids": ["PMID:3"]},
                            {"name": "Drug", "source_ids": ["PMID:2"]},
                        ],
                        "why_not": [{
                            "finding": "Relevant exposure remains uncertain",
                            "source_ids": ["PMID:3"],
                        }],
                    },
                    {
                        **self.review("UNICHEM:2", "PMID:3"),
                        "prior_art": {
                            "status": "human_intervention",
                            "summary": "An exact-disease human intervention was registered.",
                            "findings": [{
                                "finding": "The candidate entered an exact-disease human study.",
                                "source_ids": ["PMID:3"],
                            }],
                        },
                    },
                ],
            },
        )
        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_audit")
        audit_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(audit_packet["context"]["candidates"]), 2)
        self.assertEqual(len(audit_packet["context"]["reviews"]), 2)
        self.assertEqual(
            [row["document_id"] for row in audit_packet["context"]["source_index"]],
            ["PMID:1", "PMID:2", "PMID:3", "SRC:1"],
        )
        self.assertEqual(
            audit_packet["context"]["evidence_graph"]["profiles"][0]["node_id"],
            "NODE:1",
        )
        self.assertEqual(
            audit_packet["context"]["candidate_identity"]["identity_groups"], []
        )
        self.assertTrue(
            audit_packet["context"]["source_index"][0]["evidence_passages"]
        )
        self.assertEqual(
            audit_packet["context"]["source_index"][0]["canonical_publication_id"],
            "PMID:1",
        )
        self.assertEqual(
            audit_packet["context"]["source_index"][0]["year"], 2026
        )
        rubric = audit_packet["result_contract"]["score_rubric"]
        self.assertIn("without weighting", rubric["method"])
        self.assertIn("not a probability", rubric["method"])
        self.assertIn("Counterevidence earns no points", rubric["method"])
        self.assertEqual(set(rubric["components"]), set(core.SCORE_COMPONENTS))
        self.assertEqual(core.MAX_SCORE, 80)
        self.assertEqual(
            set(rubric["components"]["mechanistic_bridge_plausibility"]["anchors"]),
            {"5", "10", "15", "20"},
        )
        self.assertIn(
            "long or speculative",
            rubric["components"]["mechanistic_bridge_plausibility"]["anchors"]["5"],
        )
        exclusion_policy = audit_packet["result_contract"]["exclusion_policy"]
        self.assertEqual(set(exclusion_policy), set(core.AUDIT_EXCLUSION_REASONS))
        self.assertIn("missing data", exclusion_policy["impossible_translational_feasibility"])
        self.assertIn("unresolved identity alone", exclusion_policy["invalid_candidate"])
        review_result = json.loads(
            (self.root / "results" / "candidate_review.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in review_result["records"]["documents"]],
            ["PMID:3"],
        )
        first_assessment = {
            **self.assessment(
                "UNICHEM:1",
                "PMID:3",
                {
                    "drug_action_confidence": 15,
                    "disease_mechanism_relevance": 20,
                    "mechanistic_bridge_plausibility": 5,
                    "translational_feasibility": 15,
                },
            ),
            "aliases": [
                {"name": "Drug hydrochloride", "source_ids": ["PMID:3"]},
                {"name": "Drug", "source_ids": ["PMID:2"]},
            ],
            "why_not": [{
                "finding": "Relevant exposure remains uncertain",
                "source_ids": ["PMID:3"],
            }],
            "net_assessment": {
                "text": "Supported action and pathology fit outweigh a speculative bridge.",
                "source_ids": ["PMID:3"],
            },
        }
        first_assessment["source_integrity"] = self.source_integrity(first_assessment)
        self.submit(
            action,
            {
                "assessments": [first_assessment],
                "excluded_candidates": [{
                    "candidate_id": "UNICHEM:2",
                    "reason_code": "human_intervention",
                    "finding": "An exact-disease human intervention disqualifies repurposing.",
                    "source_ids": ["PMID:3"],
                    "source_integrity": self.exclusion_integrity(["PMID:3"]),
                }],
            },
        )
        self.assertEqual(core.status(self.root)["state"], "ready_to_build")
        manifest = core.build_outputs(self.root)
        self.assertEqual(manifest["raw_candidate_count"], 2)
        self.assertEqual(manifest["deduplicated_candidate_count"], 2)
        self.assertEqual(manifest["excluded_candidate_count"], 1)
        summary = (self.root / "outputs" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("raw candidate seeds: 2; deduplicated candidates: 2", summary)
        self.assertIn("4 20-point components out of 80", summary)
        cards = (self.root / "outputs" / "candidate_cards.md").read_text(encoding="utf-8")
        self.assertIn("## UNICHEM:1", cards)
        self.assertIn(
            "Aliases:\n- Drug hydrochloride (References: PMID:3)\n"
            "- Drug (References: PMID:2)",
            cards,
        )
        self.assertEqual(cards.count("Aliases:"), 1)
        self.assertIn("Score: 55/80", cards)
        self.assertIn("Source verification:", cards)
        self.assertIn("Mechanistic-bridge plausibility: 5/20", cards)
        self.assertIn(
            "### Why\n\nSupported action and pathology fit outweigh a speculative bridge."
            "\n\nReferences: PMID:3",
            cards,
        )
        self.assertIn(
            "### Why not\n\n- Relevant exposure remains uncertain\n"
            "  References: PMID:3",
            cards,
        )
        self.assertEqual(cards.count("### Why not"), 1)
        self.assertNotIn("Priority tier:", cards)
        self.assertNotIn("Mechanism hypothesis:", cards)
        self.assertNotIn("Review:", cards)
        self.assertNotIn("Audit:", cards)
        exclusions = (self.root / "outputs" / "candidate_exclusions.jsonl").read_text()
        self.assertIn('"candidate_id":"UNICHEM:2"', exclusions)
        self.assertIn('"reason_code":"human_intervention"', exclusions)
        self.assertEqual(core.status(self.root)["state"], "complete")
        self.assertEqual(core.build_outputs(self.root), manifest)

        normalized_stage_hashes = {}
        for stage in core.STAGES:
            result = json.loads(core._result_path(self.root, stage).read_text(encoding="utf-8"))
            result.pop("packet_id", None)
            normalized_stage_hashes[stage] = core._sha256(core._canonical_bytes(result))
        baseline = {
            "case_id": manifest["case_id"],
            "counts": {
                field: manifest[field]
                for field in (
                    "candidate_count",
                    "deduplicated_candidate_count",
                    "excluded_candidate_count",
                    "raw_candidate_count",
                )
            },
            "stage_results_without_packet_id": normalized_stage_hashes,
            "artifacts": {
                artifact["filename"]: {
                    "bytes": artifact["bytes"],
                    "sha256": artifact["sha256"],
                }
                for artifact in manifest["artifacts"]
            },
        }
        self.assertEqual(baseline, PROGRAM_BASELINE)

    def test_curation_guidance_and_input_order_preserve_semantic_granularity(self):
        source = source_result()
        source["records"]["disease_context"].extend(
            [
                {
                    "context_id": "CONTEXT:ADMIN",
                    "section": "creation_date",
                    "value": "2026-01-01",
                    "source_ids": ["SRC:1"],
                },
                {
                    "context_id": "CONTEXT:SUBTYPES",
                    "section": "has_subtypes",
                    "value": ["Subtype"],
                    "source_ids": ["SRC:1"],
                },
            ]
        )
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
            self.root, "pathology_curation", None, {"pathology_sources": source}
        )
        guidance = core.STAGE_GUIDANCE["pathology_curation"]["task"]

        self.assertEqual(
            [row["node_id"] for row in context["source_nodes"]],
            ["NODE:A", "NODE:Z", "NODE:1", "NODE:P"],
        )
        self.assertEqual(
            [row["section"] for row in context["disease_context"]],
            ["description", "has_subtypes"],
        )
        self.assertNotIn("source_index", context)
        self.assertNotIn("source_receipts", context)
        self.assertIn("Use only the supplied packet", guidance)
        self.assertIn("do not search or perform deep research", guidance)
        self.assertIn("node_type values are provisional", guidance)
        self.assertIn("assign concept_type independently", guidance)
        self.assertIn("do not minimize concept count", guidance)
        self.assertIn("do not establish equivalence", guidance)
        self.assertIn("same-label gene-level", guidance)
        self.assertIn("Merge true duplicate records", guidance)
        self.assertIn("assign disposition independently", guidance)
        self.assertIn("major phenotype defining a distinct intervention objective", guidance)
        self.assertIn("context_only even when measurable", guidance)
        self.assertIn("bare entity or observational readout", guidance)
        self.assertIn("uncertainty never upgrades", guidance)
        pathology_guidance = core.STAGE_GUIDANCE["pathology_node_research"]["task"]
        self.assertIn("Keep discovery pathology-led", pathology_guidance)
        self.assertIn("directional evidence", pathology_guidance)
        seed_guidance = core.STAGE_GUIDANCE["candidate_seed_research"]["task"]
        self.assertIn("symptomatic or compensatory benefit", seed_guidance)
        self.assertIn("linked context nodes", seed_guidance)
        self.assertIn("do not use disease-specific drug literature", seed_guidance)
        self.assertIn(
            "evidence dossier", core.STAGE_GUIDANCE["candidate_evidence_review"]["task"]
        )
        self.assertIn(
            "exact-disease prior art",
            core.STAGE_GUIDANCE["candidate_evidence_review"]["task"],
        )
        self.assertIn(
            "unresolved identity", " ".join(core.FIELD_RULES["candidate_audit"])
        )

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
        source["records"]["documents"].append(
            {"document_id": "SRC:2", "title": "Context evidence", "source": "test"}
        )
        source["records"]["source_nodes"].extend(
            [
                {
                    "node_id": "NODE:2",
                    "label": "Anatomical context",
                    "node_type": "anatomy",
                    "source_ids": ["SRC:2"],
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
        graph = {
            "source_nodes": nodes,
            "source_edges": edges,
            "profiles": [self.profile("NODE:1", "mechanism")["profiles"][0]],
            "assertions": [],
        }
        self.assertEqual(
            [row["node_id"] for row in core._graph_index(graph)], ["NODE:1", "NODE:2"]
        )
        context = core._graph_node_context(graph, "NODE:2")
        self.assertIsNone(context["profile"])
        self.assertEqual([row["node_id"] for row in context["related_nodes"]], ["NODE:1"])
        self.assertEqual(core._graph_support_ids(graph)["NODE:2"], {"SRC:2"})

        graph["documents"] = [
            *source["records"]["documents"],
            *self.profile("NODE:1", "mechanism")["documents"],
        ]
        results["evidence_graph"] = {"records": graph}
        seed_records = {
            "documents": [
                {"document_id": "PMID:2", "title": "Drug action", "source": "test"}
            ],
            "candidates": [
                {
                    "candidate_id": "CHEMBL:1",
                    "name": "Drug",
                    "identifiers": {"chembl": "CHEMBL1"},
                    "mechanism_hypothesis": "Mechanism using both contexts",
                    "graph_node_ids": ["NODE:1", "NODE:2"],
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:2"],
                }
            ],
            "exclusions": [],
        }
        with self.assertRaisesRegex(core.ProgramError, "do not support graph nodes"):
            core._validate_seed_item(seed_records, "NODE:1", results)
        seed_records["candidates"][0]["pathology_source_ids"].append("SRC:2")
        core._validate_seed_item(seed_records, "NODE:1", results)

    def test_pathology_research_requires_retained_evidence(self):
        self.curate_single_process()
        action = core.next_action(self.root)
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        with self.assertRaisesRegex(core.ProgramError, "evidence_passages"):
            self.submit(action, records, add_evidence_passages=False)

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

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["desired_biological_state"] = ""
        with self.assertRaisesRegex(core.ProgramError, "desired_biological_state"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["established_pathology_observations"] = [
            {"observation": "unsupported", "source_ids": ["PMID:999"]}
        ]
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit(action, records)

    def test_canonical_document_identifier_families(self):
        for document_id in (
            "PMID:10195180", "PMCID:PMC10338806", "DOI:10.1002/ana.21147",
            "MONARCH-ASSOC-" + "A" * 24, "CLINGEN:CCID004621",
            "UNIPROT:P09651", "NCBI:NBK551641", "https://example.org/report",
        ):
            self.assertIsNotNone(core.CANONICAL_DOCUMENT_ID.fullmatch(document_id))
        self.assertIsNone(core.CANONICAL_DOCUMENT_ID.fullmatch("DOC-AUTHOR-2026-TOPIC"))

    def test_document_metadata_enriches_only_when_identity_fields_agree(self):
        documents = core._merge_documents([
            {
                "document_id": "PMID:22312314",
                "title": "Disruption of Axonal Transport in Motor Neuron Diseases",
                "source": "DisMech evidence",
                "citation": "PMID:22312314",
                "snippets": ["source snippet"],
                "supports": ["PARTIAL"],
            },
            {
                "document_id": "PMID:22312314",
                "title": "Disruption of axonal transport in motor neuron diseases.",
                "source": "PubMed Central",
                "citation": "PMID:22312314",
                "snippets": ["research snippet"],
                "supports": ["SUPPORT"],
            },
        ])

        self.assertEqual(len(documents), 1)
        self.assertEqual(
            documents[0]["title"],
            "Disruption of Axonal Transport in Motor Neuron Diseases",
        )
        self.assertEqual(documents[0]["source"], "DisMech evidence")
        self.assertEqual(documents[0]["citation"], "PMID:22312314")
        self.assertEqual(documents[0]["snippets"], ["research snippet", "source snippet"])
        self.assertEqual(documents[0]["supports"], ["PARTIAL", "SUPPORT"])

        with self.assertRaisesRegex(core.ProgramError, "Conflicting document metadata"):
            core._merge_documents([
                documents[0],
                {**documents[0], "title": "A different article"},
            ])

    def test_bibliographic_title_mismatch_is_rejected_and_projection_is_authoritative(self):
        document = {
            "document_id": "PMID:12024045",
            "title": "Incorrect title",
            "source": "test",
            "evidence_passages": [{"text": "Evidence", "locator": "abstract"}],
        }
        canonical = {
            "PMID:12024045": {
                "title": "Canonical article title",
                "year": 2002,
                "journal": "Canonical journal",
                "authors": ["A. Author"],
                "canonical_publication_id": "PMID:12024045",
                "identifier_aliases": ["PMID:12024045", "DOI:10.1/example"],
                "metadata_source": "PubMed",
            }
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bibliography, "_resolve_bibliographic_metadata", return_value=canonical),
        ):
            root = Path(directory)
            with self.assertRaisesRegex(core.ProgramError, "metadata mismatch"):
                core._canonicalize_documents(root, [document], verify_titles=True)
            projected = core._canonicalize_documents(root, [document], verify_titles=False)[0]

        self.assertEqual(projected["title"], "Canonical article title")
        self.assertEqual(projected["submitted_title"], "Incorrect title")
        self.assertEqual(projected["canonical_publication_id"], "PMID:12024045")

    def test_document_propagation_recurses_and_ignores_document_metadata(self):
        records = {
            "documents": [
                {"document_id": "PMID:10", "title": "Nested evidence", "source": "test"},
                {"document_id": "PMID:11", "title": "Limitation evidence", "source": "test"},
                {
                    "document_id": "PMID:12",
                    "title": "Unused search hit",
                    "source": "test",
                    "source_ids": ["PMID:12"],
                },
            ],
            "profiles": [{
                "source_ids": ["UPSTREAM:1"],
                "established_pathology_observations": [{
                    "observation": "Observed directional change",
                    "source_ids": ["PMID:10"],
                }],
            }],
            "interpretation": {
                "limitations": [{"finding": "Bounded evidence", "source_ids": ["PMID:11"]}]
            },
        }

        self.assertEqual(
            core._cited_ids(records),
            {"UPSTREAM:1", "PMID:10", "PMID:11"},
        )
        self.assertEqual(
            [row["document_id"] for row in core._cited_documents(records)],
            ["PMID:10", "PMID:11"],
        )
        identity_results = {
            "candidate_identity": {"records": {
                "documents": [
                    {"document_id": "PMID:13", "title": "Identity evidence", "source": "test"},
                    {"document_id": "PMID:14", "title": "Unused identity hit", "source": "test"},
                ],
                "identity_groups": [{
                    "source_ids": ["PMID:13", "UPSTREAM:2"],
                }],
            }}
        }
        self.assertEqual(
            [row["document_id"] for row in core._all_documents(identity_results)],
            ["PMID:13"],
        )

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

    def test_unichem_merges_exact_ids_and_queues_every_ambiguous_seed(self):
        rows = [
            self.seed("PUBCHEM:11125", "SEED-A", identifiers={"pubchem_cid": "11125"}),
            self.seed("DRUGBANK:DBX", "SEED-B", identifiers={"drugbank": "DBX"}),
            self.seed("PUBCHEM:10", "SEED-10", identifiers={"pubchem_cid": "10"}),
            self.seed("PUBCHEM:20", "SEED-20", identifiers={"pubchem_cid": "20"}),
            self.seed("CODE:ALPHA", "SEED-NONE"),
            self.seed("PUBCHEM:30", "SEED-NO-RESULT", identifiers={"pubchem_cid": "30"}),
        ]

        def response(endpoint, body):
            compound = str(body["compound"])
            exact_sources = [
                {"id": 22, "compoundId": "11125"},
                {"id": 2, "compoundId": "DBX"},
            ]
            related_sources = [
                {"id": 22, "compoundId": "10"},
                {"id": 22, "compoundId": "20"},
            ]
            if endpoint == "connectivity":
                return {
                    "response": "Success",
                    "sources": related_sources if compound in {"10", "20"} else exact_sources,
                }
            if compound == "30":
                return {"response": "Success", "compounds": [], "notFound": ["30"]}
            uci = 42 if compound in {"11125", "DBX"} else int(compound)
            source_id = 2 if compound == "DBX" else 22
            return {
                "response": "Success",
                "compounds": [{
                    "uci": uci,
                    "standardInchiKey": f"CONNECTIVITY-{uci}",
                    "sources": [{"id": source_id, "compoundId": compound}],
                }],
                "notFound": [],
            }

        with patch.object(core, "_post_unichem", response):
            enriched, receipts = core._resolve_seed_identities(self.root, rows)
        records = {"candidates": enriched}
        groups = core._exact_identity_groups(records)
        self.assertEqual(groups["UNICHEM:42"], ["SEED-A", "SEED-B"])
        self.assertEqual(groups["UNICHEM:10"], ["SEED-10"])
        self.assertEqual(groups["UNICHEM:20"], ["SEED-20"])
        self.assertEqual(len(receipts), 8)
        self.assertEqual(
            {
                row["seed_id"]: row["identity_resolution"]["status"]
                for row in core._identity_queue(records)
            },
            {
                "SEED-10": "connectivity_match",
                "SEED-20": "connectivity_match",
                "SEED-NONE": "not_queryable",
                "SEED-NO-RESULT": "no_result",
            },
        )

    def test_identity_packet_exposes_one_explicit_set_of_canonical_options(self):
        records = {"candidates": [
            self.seed(
                "PUBCHEM:42", "SEED-EXACT", name="existing candidate",
                resolution={"status": "exact", "uci": "42"},
            ),
            self.seed(
                "PUBCHEM:10", "SEED-CONNECTED", name="queued exact block",
                resolution={"status": "connectivity_match", "uci": "10"},
            ),
            self.seed(
                "CHEMBL:PARTIAL", "SEED-PARTIAL", name="partial result",
                resolution={
                    "status": "conflicting_or_partial_result",
                    "ucis": ["1657"],
                },
            ),
        ]}
        results = {
            "evidence_graph": {"records": {}},
            "candidate_seed_generation": {"records": records},
        }

        context = core._packet_context(
            self.root, "candidate_identity", None, results
        )
        options = {
            row["candidate_id"]: row
            for row in context["canonical_candidate_options"]
        }

        self.assertNotIn("resolved_candidates", context)
        self.assertEqual(set(options), {"UNICHEM:10", "UNICHEM:42"})
        self.assertEqual(
            options["UNICHEM:10"]["required_member_seed_ids"],
            ["SEED-CONNECTED"],
        )
        self.assertEqual(
            options["UNICHEM:42"]["required_member_seed_ids"], []
        )
        self.assertNotIn("UNICHEM:1657", options)
        self.assertTrue(all(
            "mechanism_hypothesis" not in row for row in options.values()
        ))
        rules = " ".join(core.FIELD_RULES["candidate_identity"])
        self.assertIn("context.canonical_candidate_options", rules)
        self.assertIn("identity_resolution.ucis", rules)

    def test_partial_unichem_observation_cannot_be_used_as_canonical_id(self):
        seed_records = {"candidates": [self.seed(
            "CHEMBL:PARTIAL", "SEED-PARTIAL", name="partial result",
            resolution={
                "status": "conflicting_or_partial_result",
                "ucis": ["1657"],
            },
        )]}
        prior = {
            "candidate_seed_generation": {"records": seed_records},
        }
        records = {
            "documents": [{
                "document_id": "https://example.org/identity",
                "title": "Authoritative identity",
                "source": "test",
            }],
            "identity_groups": [{
                "member_seed_ids": ["SEED-PARTIAL"],
                "canonical_candidate_id": "UNICHEM:1657",
                "status": "resolved",
                "preferred_name": "partial result",
                "identifiers": {"unichem_uci": "1657"},
                "reason": "Identity evidence supports one residual identity",
                "source_ids": ["https://example.org/identity"],
            }],
        }

        with self.assertRaisesRegex(
            core.ProgramError, "context.canonical_candidate_options"
        ):
            core._validate_candidate_identity(records, prior)

        records["identity_groups"][0]["canonical_candidate_id"] = None
        core._validate_candidate_identity(records, prior)

    def test_identity_review_partitions_all_flagged_seeds_before_merge(self):
        resolution = {
            "status": "connectivity_match",
            "uci": "121892",
            "standard_inchikey": "KEY",
        }
        seeds = [
            self.seed(
                candidate_id, seed_id, name="retigabine/ezogabine",
                resolution=resolution, concept_id=concept,
            )
            for seed_id, candidate_id, concept in (
                ("SEED-A", "INN:RETIGABINE", "NODE:A"),
                ("SEED-B", "PUBCHEM:121892", "NODE:B"),
            )
        ]
        prior = {
            "candidate_seed_generation": {
                "records": {
                    "candidates": seeds,
                }
            }
        }
        records = {
            "documents": [{
                "document_id": "https://example.org/identity",
                "title": "Authoritative identity",
                "source": "test",
            }],
            "identity_groups": [{
                "member_seed_ids": ["SEED-A", "SEED-B"],
                "canonical_candidate_id": "UNICHEM:121892",
                "status": "resolved",
                "preferred_name": "retigabine",
                "identifiers": {"pubchem_cid": "121892"},
                "reason": "Authoritative synonym identity",
                "source_ids": ["https://example.org/identity"],
            }],
        }

        core._validate_candidate_identity(records, prior)
        prior["candidate_identity"] = {"records": records}
        candidates = core._canonical_candidates(prior)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_id"], "UNICHEM:121892")
        self.assertEqual(candidates[0]["member_seed_ids"], ["SEED-A", "SEED-B"])
        records["identity_groups"][0]["member_seed_ids"] = ["SEED-A"]
        with self.assertRaisesRegex(core.ProgramError, "cannot split an exact UniChem"):
            core._validate_candidate_identity(records, prior)

    def test_identity_review_can_attach_no_result_to_exact_candidate(self):
        exact = self.seed(
            "PUBCHEM:121892", "SEED-EXACT", name="ezogabine",
            identifiers={"pubchem_cid": "121892"},
            resolution={
                "status": "exact",
                "uci": "121892",
                "standard_inchikey": "KEY",
            },
        )
        alias = self.seed(
            "INN:RETIGABINE", "SEED-ALIAS", name="retigabine",
            resolution={"status": "not_queryable", "queries": []}, concept_id="NODE:B",
        )
        prior = {
            "candidate_seed_generation": {"records": {
                "candidates": [exact, alias],
            }}
        }
        records = {
            "documents": [{
                "document_id": "https://example.org/synonym",
                "title": "Synonym identity",
                "source": "test",
            }],
            "identity_groups": [{
                "member_seed_ids": ["SEED-ALIAS"],
                "canonical_candidate_id": "UNICHEM:121892",
                "status": "resolved",
                "preferred_name": "retigabine",
                "identifiers": {"inn": "retigabine"},
                "reason": "Authoritative synonym",
                "source_ids": ["https://example.org/synonym"],
            }],
        }

        core._validate_candidate_identity(records, prior)
        prior["candidate_identity"] = {"records": records}
        candidates = core._canonical_candidates(prior)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["member_seed_ids"], ["SEED-ALIAS", "SEED-EXACT"])

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
        }
        with patch.object(core, "_canonical_candidates", return_value=candidates):
            first = core._review_batches(results)
        with patch.object(
            core, "_canonical_candidates", return_value=list(reversed(candidates))
        ):
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
            records["reviews"][0]["aliases"] = [{"name": "Drug salt", "source_ids": []}]
            with self.assertRaisesRegex(core.ProgramError, "must be a non-empty list"):
                core._validate_review_item(records, "NODE:A", {})
            records["reviews"][0]["aliases"] = []
            records["reviews"][0]["why_not"] = [{
                "finding": "Unsupported concern",
                "source_ids": ["PMID:404"],
            }]
            with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
                core._validate_review_item(records, "NODE:A", {})
            records["reviews"][0]["why_not"] = []
            records["reviews"][0]["counterevidence"] = []
            with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
                core._validate_review_item(records, "NODE:A", {})
            del records["reviews"][0]["counterevidence"]
            records["documents"] = []
            with self.assertRaisesRegex(core.ProgramError, "retained by this review"):
                core._validate_review_item(
                    records,
                    "NODE:A",
                    {"prior": {"records": {
                        "documents": [
                            {"document_id": "PMID:1", "title": "Prior evidence", "source": "test"}
                        ],
                        "profiles": [{"source_ids": ["PMID:1"]}],
                    }}},
                )
            records["documents"] = [
                {"document_id": "PMID:1", "title": "Drug evidence", "source": "test"}
            ]
            records["reviews"] = [self.review("DRUG-A")]
            with self.assertRaisesRegex(core.ProgramError, "exactly the supplied batch"):
                core._validate_review_item(records, "NODE:A", {})

    def test_audit_validation_partitions_candidates_and_preserves_longshots(self):
        second_review = self.review("DRUG-B")
        second_review["prior_art"] = {
            "status": "human_intervention",
            "summary": "An exact-disease human study was identified.",
            "findings": [{
                "finding": "The candidate was tested in an exact-disease human study.",
                "source_ids": ["PMID:1"],
            }],
        }
        results = {
            "candidate_review": {"records": {
                "documents": [
                    {
                        "document_id": "PMID:1",
                        "title": "Retained evidence",
                        "source": "test",
                        "evidence_passages": [{"text": "Evidence", "locator": "abstract"}],
                    }
                ],
                "reviews": [self.review("DRUG-A"), second_review],
            }}
        }
        longshot = self.assessment(
            "DRUG-A",
            values={component: 5 for component in core.SCORE_COMPONENTS},
        )
        records = {
            "assessments": [longshot],
            "excluded_candidates": [{
                "candidate_id": "DRUG-B",
                "reason_code": "human_intervention",
                "finding": "Exact-disease human intervention is disqualifying.",
                "source_ids": ["PMID:1"],
                "source_integrity": self.exclusion_integrity(["PMID:1"]),
            }],
        }

        core._validate_candidate_audit(records, results)
        self.assertEqual(core._final_score(longshot), 20)

        all_excluded = {
            "assessments": [],
            "excluded_candidates": [
                {
                    "candidate_id": "DRUG-A",
                    "reason_code": "unsupported_action",
                    "finding": "The retained corpus does not support the proposed drug action.",
                    "source_ids": ["PMID:1"],
                    "source_integrity": self.exclusion_integrity(["PMID:1"]),
                },
                records["excluded_candidates"][0],
            ],
        }
        core._validate_candidate_audit(all_excluded, results)
        self.assertEqual(
            core._stop_reason({"candidate_audit": {"records": all_excluded}}),
            "the audit excluded every reviewed candidate",
        )

        invalid = json.loads(json.dumps(records))
        invalid["assessments"][0]["component_scores"]["drug_action_confidence"]["value"] = 12
        with self.assertRaisesRegex(core.ProgramError, "must be one of"):
            core._validate_candidate_audit(invalid, results)

        invalid = json.loads(json.dumps(records))
        invalid["excluded_candidates"] = []
        with self.assertRaisesRegex(core.ProgramError, "partition every reviewed candidate"):
            core._validate_candidate_audit(invalid, results)

        invalid = json.loads(json.dumps(records))
        invalid["assessments"].append(self.assessment("DRUG-B"))
        invalid["excluded_candidates"] = []
        with self.assertRaisesRegex(core.ProgramError, "disqualifying prior-art status"):
            core._validate_candidate_audit(invalid, results)

    def test_counterevidence_is_unscored_and_cannot_restore_robustness_points(self):
        results = {
            "candidate_review": {"records": {
                "documents": [
                    {
                        "document_id": "PMID:1",
                        "title": "Retained evidence",
                        "source": "test",
                        "evidence_passages": [{"text": "Evidence", "locator": "abstract"}],
                    }
                ],
                "reviews": [self.review("DRUG-A")],
            }}
        }
        assessment = self.assessment(
            "DRUG-A", values={component: 5 for component in core.SCORE_COMPONENTS}
        )
        baseline_score = core._final_score(assessment)
        assessment["why_not"] = [{
            "finding": "Independent disease models found no efficacy.",
            "source_ids": ["PMID:1"],
        }]
        assessment["source_integrity"] = self.source_integrity(assessment)
        core._validate_candidate_audit(
            {"assessments": [assessment], "excluded_candidates": []}, results
        )
        self.assertEqual(core._final_score(assessment), baseline_score)

        invalid = json.loads(json.dumps(assessment))
        invalid["component_scores"]["evidence_robustness"] = {
            "value": 20,
            "reason": "Consistent negative findings form a strong evidence base.",
            "source_ids": ["PMID:1"],
        }
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            core._validate_candidate_audit(
                {"assessments": [invalid], "excluded_candidates": []}, results
            )

    def test_source_integrity_checks_every_cited_use_and_cannot_defer_judgment(self):
        results = {
            "candidate_review": {"records": {
                "documents": [{
                    "document_id": "PMID:1",
                    "title": "Retained evidence",
                    "source": "test",
                    "evidence_passages": [{"text": "Evidence", "locator": "results"}],
                }],
                "reviews": [self.review("DRUG-A")],
            }}
        }
        assessment = self.assessment("DRUG-A")
        assessment["aliases"] = [
            {"name": "Alias one", "source_ids": ["PMID:1"]},
            {"name": "Alias two", "source_ids": ["PMID:1"]},
        ]
        assessment["source_integrity"] = self.source_integrity(assessment)
        core._validate_candidate_audit(
            {"assessments": [assessment], "excluded_candidates": []}, results
        )
        scopes = {
            check["scope"] for check in assessment["source_integrity"]["checks"]
        }
        self.assertIn("aliases[0]", scopes)
        self.assertIn("aliases[1]", scopes)

        generic = json.loads(json.dumps(assessment))
        generic["source_integrity"] = {
            "status": "supported",
            "finding": "Looks sound.",
            "source_ids": ["PMID:1"],
        }
        with self.assertRaisesRegex(core.ProgramError, "missing fields: checks"):
            core._validate_candidate_audit(
                {"assessments": [generic], "excluded_candidates": []}, results
            )

        missing = json.loads(json.dumps(assessment))
        missing["source_integrity"]["checks"].pop()
        with self.assertRaisesRegex(core.ProgramError, "cover every cited source use"):
            core._validate_candidate_audit(
                {"assessments": [missing], "excluded_candidates": []}, results
            )

        deferred = json.loads(json.dumps(assessment))
        deferred["source_integrity"]["checks"][0]["finding"] = (
            "This source needs independent verification."
        )
        with self.assertRaisesRegex(core.ProgramError, "not defer verification"):
            core._validate_candidate_audit(
                {"assessments": [deferred], "excluded_candidates": []}, results
            )

        deferred["source_integrity"]["checks"][0]["finding"] = (
            "This citation is unverifiable from the packet."
        )
        with self.assertRaisesRegex(core.ProgramError, "not defer verification"):
            core._validate_candidate_audit(
                {"assessments": [deferred], "excluded_candidates": []}, results
            )

        no_content = json.loads(json.dumps(results))
        del no_content["candidate_review"]["records"]["documents"][0]["evidence_passages"]
        with self.assertRaisesRegex(core.ProgramError, "no inspectable content"):
            core._validate_candidate_audit(
                {"assessments": [assessment], "excluded_candidates": []}, no_content
            )

        duplicate_publication = json.loads(json.dumps(assessment))
        duplicate_publication["component_scores"]["drug_action_confidence"][
            "source_ids"
        ].append("DOI:10.1000/same")
        duplicate_publication["source_integrity"] = self.source_integrity(
            duplicate_publication
        )
        source_index = [
            {
                **results["candidate_review"]["records"]["documents"][0],
                "canonical_publication_id": "PMID:1",
            },
            {
                "document_id": "DOI:10.1000/same",
                "title": "Retained evidence",
                "canonical_publication_id": "PMID:1",
                "evidence_passages": [{"text": "Evidence", "locator": "results"}],
            },
        ]
        with self.assertRaisesRegex(core.ProgramError, "more than once"):
            core._validate_candidate_audit(
                {"assessments": [duplicate_publication], "excluded_candidates": []},
                results,
                source_index,
            )

    def test_raw_scores_sort_deterministically_and_ties_share_rank(self):
        candidates = [
            {
                "candidate_id": candidate_id,
                "name": candidate_id,
                "identity": {"status": "resolved"},
                "graph_node_ids": ["NODE:1"],
                "pathology_source_ids": ["PMID:1"],
                "mechanism_source_ids": ["PMID:1"],
            }
            for candidate_id in ("DRUG-A", "DRUG-B", "DRUG-C")
        ]
        high = {component: 20 for component in core.SCORE_COMPONENTS}
        medium = {component: 10 for component in core.SCORE_COMPONENTS}
        results = {
            "candidate_audit": {"records": {
                "assessments": [
                    self.assessment("DRUG-B", values=high),
                    self.assessment("DRUG-C", values=medium),
                    self.assessment("DRUG-A", values=high),
                ],
                "excluded_candidates": [],
            }}
        }
        with patch.object(core, "_canonical_candidates", return_value=candidates):
            rows, _ = core._ranked_rows(results)

        self.assertEqual(
            [(row["candidate_id"], row["rank"], row["final_score"]) for row in rows],
            [("DRUG-A", 1, 80), ("DRUG-B", 1, 80), ("DRUG-C", 2, 40)],
        )

    def test_card_renderer_uses_actual_id_and_omits_empty_optional_sections(self):
        components = {
            component: {
                "value": 10,
                "reason": "Bounded support.",
                "source_ids": ["PMID:1"],
            }
            for component in core.SCORE_COMPONENTS
        }
        payload = core._cards_bytes([{
            "drug_id": "CANDIDATE-UNRESOLVED",
            "aliases": [],
            "score": 40,
            "components": components,
            "why": {"text": "Evidence supports nomination.", "source_ids": ["PMID:1"]},
            "why_not": [],
            "source_integrity": {
                "checks": [
                    {
                        "source_id": "PMID:1",
                        "scope": scope,
                        "verdict": "partly_supports" if scope == "translational_feasibility" else "supports",
                        "finding": "Direct support." if scope != "translational_feasibility" else "Only model-level support.",
                    }
                    for scope in (*core.SCORE_COMPONENTS, "net_assessment")
                ],
            },
        }]).decode("utf-8")

        self.assertIn("## CANDIDATE-UNRESOLVED", payload)
        self.assertIn("Score: 40/80", payload)
        self.assertIn("Source verification: 5 cited uses checked (4 supports, 1 partly supports)", payload)
        self.assertIn(
            "PMID:1 in translational_feasibility: partly supports — Only model-level support.",
            payload,
        )
        self.assertIn("Drug-action confidence: 10/20", payload)
        self.assertIn(
            "### Why\n\nEvidence supports nomination.\n\nReferences: PMID:1",
            payload,
        )
        self.assertNotIn("Aliases:", payload)
        self.assertNotIn("### Why not", payload)

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
                    "desired_biological_state": "restore the normal process",
                    "established_pathology_observations": [],
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
            "mechanism_hypothesis": f"mechanism {node_id}",
            "graph_node_ids": [node_id],
            "pathology_source_ids": [f"PATH:{node_id}"],
            "mechanism_source_ids": [f"MOA:{node_id}"],
            "origin_concept_ids": [concept_id],
        }

    @classmethod
    def seed(
        cls, candidate_id, seed_id, *, name="candidate", identifiers=None,
        resolution=None, concept_id="NODE:A",
    ):
        row = {
            "candidate_id": candidate_id,
            "name": name,
            "identifiers": identifiers or {},
            "mechanism_hypothesis": f"mechanism {concept_id}",
            "graph_node_ids": [concept_id],
            "pathology_source_ids": [f"PATH:{concept_id}"],
            "mechanism_source_ids": [f"MOA:{concept_id}"],
            "origin_concept_ids": [concept_id],
            "seed_id": seed_id,
        }
        if resolution is not None:
            row["identity_resolution"] = resolution
        return row

    @staticmethod
    def review(candidate_id, source_id="PMID:1"):
        return {
            "candidate_id": candidate_id,
            "hypothesis": "The candidate could move the process toward the desired state.",
            "supporting_findings": [{
                "finding": "The candidate has the required pharmacological action.",
                "source_ids": [source_id],
            }],
            "mechanistic_bridge": "The established action is inferred to normalize the process.",
            "assumptions": ["The pharmacological context transfers to the affected tissue."],
            "aliases": [],
            "why_not": [],
            "prior_art": {
                "status": "none_found",
                "summary": "No exact-disease human intervention was found in the bounded search.",
                "findings": [],
            },
            "limitations": [],
        }

    @staticmethod
    def assessment(candidate_id, source_id="PMID:1", values=None):
        values = values or {
            "drug_action_confidence": 15,
            "disease_mechanism_relevance": 15,
            "mechanistic_bridge_plausibility": 15,
            "translational_feasibility": 15,
        }
        assessment = {
            "candidate_id": candidate_id,
            "component_scores": {
                component: {
                    "value": values[component],
                    "reason": f"The retained evidence supports the {component} rating.",
                    "source_ids": [source_id],
                }
                for component in core.SCORE_COMPONENTS
            },
            "net_assessment": {
                "text": "The mechanistic support outweighs the retained uncertainty.",
                "source_ids": [source_id],
            },
            "aliases": [],
            "why_not": [],
        }
        assessment["source_integrity"] = WorkflowTest.source_integrity(assessment)
        return assessment

    @staticmethod
    def source_integrity(assessment):
        return {
            "checks": [
                {
                    "source_id": source_id,
                    "scope": scope,
                    "verdict": "supports",
                    "finding": "The retained passage supports this exact use.",
                }
                for source_id, scope in sorted(core._assessment_source_uses(assessment))
            ],
        }

    @staticmethod
    def exclusion_integrity(source_ids):
        return {
            "checks": [
                {
                    "source_id": source_id,
                    "scope": "exclusion",
                    "verdict": "supports",
                    "finding": "The retained passage directly supports the exclusion finding.",
                }
                for source_id in sorted(source_ids)
            ],
        }


if __name__ == "__main__":
    unittest.main()
