import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import (  # noqa: E402
    audit,
    bibliography,
    candidates as candidate_rules,
    contracts,
    evidence,
    evidence_cards,
    graph as graph_rules,
    hypotheses,
    identity,
    orchestration,
    packets,
    pathology,
    ranking,
    run_state,
    storage,
)


PROGRAM_BASELINE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "program_baseline.json").read_text(
        encoding="utf-8"
    )
)


def curation_atomicity(disposition="research"):
    if disposition != "research":
        return None
    return {
        "focal_abnormal_state": "increased kinase signalling",
        "causal_level": "molecular signalling",
        "biological_direction": "increased kinase activity",
        "compartment": "spinal motor neurons",
        "atomicity_rationale": "Kinase activity is one distinct normalisable variable.",
    }


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
        normalized = bibliography._normalized_publication_id(document_id)
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


def asta_receipt(
    operation_id,
    tool,
    *,
    paper_id=None,
    attempt=1,
    request_profile="standard",
    outcome="completed",
    elapsed_seconds=1.0,
    result_count=0,
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


def completed_asta_receipts(result_count=0):
    receipts = [
        asta_receipt(
            "ASTA-OP-SEARCH-1",
            "search_papers_by_relevance",
            result_count=result_count,
        ),
        asta_receipt(
            "ASTA-OP-SEARCH-2",
            "search_papers_by_relevance",
            result_count=0,
        ),
    ]
    if result_count:
        paper_id = "3fabad2e28b0d9b09b98194d68f8c63862ede98a"
        receipts.extend([
            asta_receipt(
                "ASTA-OP-CITATIONS-1", "get_citations", paper_id=paper_id, result_count=1
            ),
            asta_receipt(
                "ASTA-OP-SNIPPET-1", "snippet_search", paper_id=paper_id, result_count=1
            ),
        ])
    return receipts


class UniChemTransportTest(unittest.TestCase):
    def test_request_batches_cache_progress_and_resume(self):
        bodies = [{"compound": str(value), "type": "uci"} for value in range(3)]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(identity, "_UNICHEM_BATCH_SIZE", 2),
            patch.object(
                identity,
                "_post_unichem",
                return_value={"response": "Success", "compounds": []},
            ) as request,
        ):
            root = Path(directory)
            with self.assertRaisesRegex(identity._UniChemBatchPending, "Call next again"):
                identity._unichem_requests(root, "compounds", bodies)
            self.assertEqual(request.call_count, 2)

            resolved = identity._unichem_requests(root, "compounds", bodies)

        self.assertEqual(len(resolved), 3)
        self.assertEqual(request.call_count, 3)

    def test_controller_reports_unichem_batch_progress_as_normal_state(self):
        current = {
            "case_id": "CASE:1",
            "state": "needs_controller",
            "next_stage": "candidate_seed_generation",
            "next_task": "candidate_seed_research",
            "next_item_id": None,
            "accepted_items": 1,
            "accepted_stages": [],
            "stop_reason": None,
        }
        with (
            patch.object(orchestration, "_case", return_value={"case_id": "CASE:1"}),
            patch.object(orchestration, "_load_results", return_value={}),
            patch.object(orchestration, "_program_status", return_value=current),
            patch.object(
                orchestration,
                "_advance_controller",
                side_effect=identity._UniChemBatchPending("two remain"),
            ),
        ):
            action = orchestration.next_action(".")

        self.assertEqual(action["state"], "needs_controller")
        self.assertEqual(action["controller_progress"], "two remain")

    def test_accepts_explicit_compound_not_found_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "compounds": [],
            "response": "Not found",
        }).encode()
        with patch.object(identity, "urlopen", return_value=response) as request:
            result = identity._post_unichem(
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
        with patch.object(identity, "urlopen", return_value=response) as request:
            with self.assertRaisesRegex(
                core.ProgramError, "UniChem compounds returned an invalid response"
            ):
                identity._post_unichem(
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
            patch.object(identity, "urlopen", side_effect=[error, response]) as request,
            patch.object(identity.time, "sleep") as pause,
        ):
            result = identity._post_unichem(
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
            result = bibliography._bibliographic_get("https://example.org/record")

        self.assertEqual(result, {"record": "canonical"})
        self.assertEqual(request.call_count, 2)
        pause.assert_called_once_with(1)
        error.close()

    def test_cached_request_fetches_once_and_reuses_immutable_response(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_get", return_value={"record": "canonical"}
        ) as fetch:
            root = Path(directory)
            first = bibliography._bibliographic_request(root, "test", "https://example.org/record")
            second = bibliography._bibliographic_request(root, "test", "https://example.org/record")

        self.assertEqual(first, {"record": "canonical"})
        self.assertEqual(second, first)
        fetch.assert_called_once_with("https://example.org/record", accept="application/json")

    def test_large_native_summary_sets_are_split_into_bounded_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_request", return_value={"result": {}}
        ) as request:
            bibliography._ncbi_summaries(
                Path(directory), "pubmed", (str(identifier) for identifier in range(1, 202))
            )

        self.assertEqual(request.call_count, 2)

    def test_resolver_covers_pmid_pmcid_doi_and_ignores_other_ids(self):
        documents = [
            {"document_id": "PMID:11", "title": "Eleven"},
            {"document_id": "PMCID:PMC22", "title": "Twenty two"},
            {"document_id": "DOI:10.1000/example", "title": "DOI article"},
            {"document_id": "https://example.org/database", "title": "Database"},
        ]
        def summaries(_root, database, identifiers):
            self.assertEqual(set(identifiers), {"11"} if database == "pubmed" else {"22"})
            if database == "pubmed":
                return {"11": {
                    "title": "Eleven",
                    "pubdate": "2020 Jan",
                    "fulljournalname": "Journal A",
                    "authors": [{"name": "A Author"}],
                    "articleids": [
                        {"idtype": "pmc", "value": "PMC11"},
                        {"idtype": "doi", "value": "10.1000/eleven"},
                    ],
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
            patch.object(bibliography, "_ncbi_summaries", side_effect=summaries),
            patch.object(bibliography, "_doi_metadata", return_value=doi_metadata) as doi,
        ):
            resolved = bibliography._resolve_bibliographic_metadata(Path(directory), documents)

        self.assertEqual(set(resolved), {"PMID:11", "PMCID:PMC22", "DOI:10.1000/example"})
        self.assertEqual(
            resolved["PMID:11"]["canonical_publication_id"], "DOI:10.1000/eleven"
        )
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

    def test_mixed_doi_and_pmcid_inputs_use_only_native_metadata_routes(self):
        documents = [
            {"document_id": "DOI:10.1093/brain/awae038", "title": "Mitochondria"},
            {"document_id": "DOI:10.1002/glia.24042", "title": "Axon and myelin"},
            {"document_id": "PMCID:PMC10497986", "title": "Biomarkers"},
        ]

        def request(_root, kind, url, *, accept="application/json"):
            self.assertNotIn("idconv", url)
            if kind == "ncbi-pmc-summary":
                return {"result": {"10497986": {
                    "title": "Biomarkers",
                    "pubdate": "2023 Oct",
                    "source": "EBioMedicine",
                    "authors": [],
                    "articleids": [
                        {"idtype": "pmc", "value": "PMC10497986"},
                        {"idtype": "doi", "value": "10.1016/j.ebiom.2023.104781"},
                    ],
                }}}
            title = "Mitochondria" if url.endswith("awae038") else "Axon and myelin"
            return {
                "title": title,
                "issued": {"date-parts": [[2024]]},
                "container-title": "Journal",
                "author": [],
            }

        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_bibliographic_request", side_effect=request
        ) as fetch:
            resolved = bibliography._resolve_bibliographic_metadata(
                Path(directory), documents
            )

        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(
            resolved["PMCID:PMC10497986"]["canonical_publication_id"],
            "DOI:10.1016/j.ebiom.2023.104781",
        )

    def test_missing_canonical_publication_metadata_stops_validation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bibliography, "_ncbi_summaries", return_value={}),
        ):
            with self.assertRaisesRegex(core.ProgramError, "Canonical metadata was not found"):
                bibliography._resolve_bibliographic_metadata(
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
                bibliography._validate_bibliographic_documents(
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
            metadata = bibliography._doi_metadata(Path(directory), "10.1000/example")

        self.assertEqual(metadata["title"], "A DOI article")
        self.assertEqual(metadata["journal"], "A Journal")
        self.assertEqual(metadata["authors"], ["Ada Lovelace"])
        self.assertEqual(metadata["year"], 2024)


class ArtifactPersistenceTest(unittest.TestCase):
    def test_rows_returns_defensive_record_copies(self):
        records = {"items": [{"item_id": "ITEM:1", "values": ["original"]}]}

        rows = evidence._rows(records, "items")
        rows[0]["item_id"] = "ITEM:CHANGED"

        self.assertIsNot(rows, records["items"])
        self.assertIsNot(rows[0], records["items"][0])
        self.assertEqual(records["items"][0]["item_id"], "ITEM:1")

    def test_write_once_rejects_conflicting_accepted_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results" / "items" / "accepted.json"
            accepted = b'{"status":"complete"}\n'
            replacement = b'{"status":"complete","different":true}\n'
            storage._write_once(path, accepted)

            with self.assertRaisesRegex(
                core.ProgramError, "Immutable artifact conflicts with existing file"
            ):
                storage._write_once(path, replacement)

            self.assertEqual(path.read_bytes(), accepted)


class InstructionContractTest(unittest.TestCase):
    def test_routine_execution_is_silent_by_default(self):
        root = Path(__file__).resolve().parents[1]
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        skill = (root / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Default to no optional commentary during routine execution", agents)
        self.assertEqual(agents.casefold().count("commentary"), 1)
        self.assertNotIn("visible controller chat", skill)

    def test_validation_failure_uses_targeted_same_packet_repair(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(skill.split())
        self.assertIn("amend only the reported invalid field and direct dependants", normalized)
        self.assertIn("Validation never accepts or mutates a result", normalized)

    def test_each_packet_gets_one_fresh_authorized_worker_with_bounded_rate_limit_recovery(self):
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
            encoding="utf-8"
        )
        normalized = " ".join(skill.split())

        self.assertIn("fresh `fork_turns=none` agent", normalized)
        self.assertIn("its sibling `AGENTS.md`", normalized)
        self.assertIn("Keep only this one packet worker active", normalized)
        self.assertIn("without polling or repeated nudges", normalized)
        self.assertIn("packet research belongs to the fresh worker", normalized)
        self.assertIn("honour `Retry-After`", normalized)
        self.assertIn("continue from `status` in a fresh task", normalized)


class SourceAdjudicationWorkflowTest(unittest.TestCase):
    def test_source_validation_still_rejects_treatment_fields_in_pathology_content(self):
        result = source_result()
        pathology._validate_source_result(result)
        result["records"]["source_nodes"][1]["treatment"] = "not pathology"

        with self.assertRaisesRegex(core.ProgramError, "Treatment fields reached"):
            pathology._validate_source_result(result)

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
                        "sentence_id": storage._stable_id("DISMECH-SENTENCE", sentence),
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
            patch.object(
                orchestration, "screen_pathology_sources", return_value=screening
            ),
            patch.object(
                orchestration, "fetch_pathology_sources", side_effect=source_result
            ) as fetch,
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
            self.assertEqual(action["next_task"], "pathology_landscape_scan")
            landscape_result = {
                "stage": "pathology_landscape_scan",
                "item_id": None,
                "packet_id": action["packet_id"],
                "status": "complete",
                "records": {
                    "documents": [],
                    "landscape_proposals": [],
                    "asta_call_receipts": completed_asta_receipts(),
                    "coverage_checks": [
                        {"gap": gap, "status": "searched_unresolved",
                         "reason": "The completed search found no additional mechanism."}
                        for gap in json.loads(Path(action["packet_path"]).read_text())
                        ["context"]["coverage_checklist"]
                    ],
                },
                "gaps": [],
            }
            landscape_submission = root / "landscape.json"
            landscape_submission.write_text(
                json.dumps(landscape_result), encoding="utf-8"
            )
            core.submit(root, landscape_submission)

            action = core.next_action(root)
            self.assertEqual(action["next_task"], "pathology_coverage_expansion")
            coverage_result = {
                "stage": "pathology_coverage_expansion",
                "item_id": None,
                "packet_id": action["packet_id"],
                "status": "complete",
                "records": {
                    "documents": [],
                    "coverage_proposals": [],
                    "undermind_search_receipts": [{
                        "workspace_id": "workspace-1",
                        "search_name": json.loads(Path(action["packet_path"]).read_text())[
                            "context"
                        ]["undermind_search_name"],
                        "search_path": "/workspaces/workspace-1/deep-searches/test",
                        "outcome": "completed",
                        "ranked_result_count": 1,
                        "pdf_count": 0,
                    }],
                },
                "gaps": [],
            }
            coverage_submission = root / "coverage.json"
            coverage_submission.write_text(
                json.dumps(coverage_result), encoding="utf-8"
            )
            core.submit(root, coverage_submission)

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
            orchestration, "screen_pathology_sources", source_screening_result
        )
        self.screening_patch.start()
        self.patch = patch.object(orchestration, "fetch_pathology_sources", source_result)
        self.patch.start()
        self.unichem_patch = patch.object(identity, "_post_unichem", unichem_result)
        self.unichem_patch.start()
        self.bibliographic_patch = patch.object(
            bibliography, "_resolve_bibliographic_metadata", bibliographic_metadata
        )
        self.bibliographic_patch.start()
        core.initialize(self.root, "Disease", mondo="MONDO:1")
        landscape = core.next_action(self.root)
        self.assertEqual(landscape["next_task"], "pathology_landscape_scan")
        self.submit(
            landscape,
            {"documents": [], "landscape_proposals": []},
        )
        coverage = core.next_action(self.root)
        self.assertEqual(coverage["next_task"], "pathology_coverage_expansion")
        self.submit(
            coverage,
            {"documents": [], "coverage_proposals": []},
        )

    def tearDown(self):
        self.screening_patch.stop()
        self.patch.stop()
        self.unichem_patch.stop()
        self.bibliographic_patch.stop()
        self.temp.cleanup()

    def submit(self, action, records, *, add_evidence_passages=True):
        records = json.loads(json.dumps(records))
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        if action["next_task"] == "pathology_curation":
            for concept in records.get("concepts", []):
                concept.setdefault(
                    "atomicity", curation_atomicity(concept["disposition"])
                )
        if action["next_task"] == "pathology_landscape_scan":
            records.setdefault(
                "asta_call_receipts",
                completed_asta_receipts(
                    1 if records.get("documents") or records.get("landscape_proposals") else 0
                ),
            )
            records.setdefault("coverage_checks", [
                {"gap": gap, "status": "searched_unresolved",
                 "reason": "The completed searches resolved or bounded this coverage area."}
                for gap in packet["context"]["coverage_checklist"]
            ])
        if action["next_task"] == "pathology_coverage_expansion":
            records.setdefault("undermind_search_receipts", [{
                "workspace_id": "workspace-1",
                "search_name": packet["context"]["undermind_search_name"],
                "search_path": "/workspaces/workspace-1/deep-searches/test",
                "outcome": "completed",
                "ranked_result_count": max(1, len(records.get("documents", []))),
                "pdf_count": len(records.get("documents", [])),
            }])
        if action["next_task"] == "candidate_seed_research":
            strategy_outcome = "seeded" if records.get("candidates") else "no_supported_seed"
            records.setdefault("rescue_strategies", [{
                "strategy_key": "strategy-1",
                "primary_node_id": action["next_item_id"],
                "linked_node_ids": [],
                "connection_ids": [],
                "pathological_state": "increased kinase signalling",
                "rescuable_state": "kinase signalling within the normal physiological range",
                "desired_direction": "decrease excessive kinase activity",
                "mechanistic_basis": "The focal profile establishes increased kinase activity.",
                "ownership_rationale": "The rescued state is the focal node's control variable.",
                "assertion_ids": [],
                "source_ids": ["SRC:1"],
                "search_outcome": strategy_outcome,
                "search_summary": (
                    "The route produced at least one supported seed."
                    if strategy_outcome == "seeded"
                    else "No supported drug-action seed was identified for this route."
                ),
            }])
            default_strategy_key = records["rescue_strategies"][0].get(
                "strategy_key", "strategy-1"
            )
            for candidate in records.get("candidates", []):
                candidate.setdefault("strategy_keys", [default_strategy_key])
        if add_evidence_passages:
            for document in records.get("documents", []):
                document.setdefault("evidence_passages", [{
                    "text": f"Inspectable evidence from {document['title']}",
                    "locator": "test fixture",
                }])
        if action["next_task"] == "candidate_audit":
            excluded = {str(row["candidate_id"]): row for row in records.get(
                "excluded_candidates", [])}
            dispositions = []
            for entry in packet["context"]["candidate_evidence_index"]:
                candidate_id = str(entry["candidate_id"])
                exclusion = excluded.get(candidate_id, {})
                for source_id in entry["source_ids"]:
                    disposition = "irrelevant"
                    if source_id in exclusion.get("source_ids", []):
                        disposition = {
                            "exact_disease_prior_use_or_testing": (
                                "exact_disease_prior_use_or_testing"
                            ),
                        }.get(exclusion.get("reason_code"), disposition)
                    dispositions.append({
                        "candidate_id": candidate_id, "source_id": source_id,
                        "disposition": disposition,
                        "reason": "The retained source was classified against the exact-disease gate.",
                    })
            records.setdefault("evidence_dispositions", dispositions)
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

    def test_unindexed_distinct_mechanism_stays_in_the_frozen_profile(self):
        self.curate_single_process()
        action = core.next_action(self.root)
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["distinct_mechanisms"] = [{
            "label": "Cell-specific inflammatory transcription",
            "normal_state": "Inflammatory transcription is transiently regulated.",
            "pathological_state": "Inflammatory transcription remains elevated.",
            "biological_direction": "increased transcriptional activation",
            "causal_level": "cellular signalling",
            "compartment": "disease-relevant glial cells",
            "relationship_to_focal": "A distinct downstream component of the focal process.",
            "index_status": "unindexed_distinct",
            "indexed_node_id": None,
            "limitations": ["Cell-state generalisation is unresolved."],
            "source_ids": ["PMID:1"],
        }]
        self.submit(action, records)

        seed = core.next_action(self.root)
        self.assertEqual(seed["next_task"], "pathology_open_questions")
        self.assertNotIn("pathology_reconciliation", core.STAGES)
        graph = json.loads((self.root / "results" / "evidence_graph.json").read_text())
        self.assertEqual(
            graph["records"]["profiles"][0]["distinct_mechanisms"],
            records["profiles"][0]["distinct_mechanisms"],
        )

    def test_distinct_mechanism_index_status_cannot_point_to_the_disease_anchor(self):
        self.curate_single_process()
        action = core.next_action(self.root)
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["distinct_mechanisms"] = [{
            "label": "Indexed component", "normal_state": "Normal component state.",
            "pathological_state": "Abnormal component state.",
            "biological_direction": "increased component activity",
            "causal_level": "cellular signalling", "compartment": "affected cells",
            "relationship_to_focal": "A component of the focal process.",
            "index_status": "indexed_node", "indexed_node_id": "MONDO:1",
            "limitations": [], "source_ids": ["PMID:1"],
        }]
        with self.assertRaisesRegex(core.ProgramError, "another indexed node"):
            self.submit(action, records)

    def test_pathology_research_source_index_has_canonical_publication_metadata(self):
        source = source_result()
        document = {"document_id": "PMID:11", "title": "Pathology", "source": "test"}
        source["records"]["documents"] = [document]
        for collection in ("source_nodes", "source_edges", "disease_context"):
            for row in source["records"][collection]:
                row["source_ids"] = ["PMID:11"]
        results = {
            "pathology_sources": source,
            "pathology_curation": {"records": {"concepts": [{
                "concept_id": "NODE:1",
                "preferred_label": "Process",
                "concept_type": "mechanism",
                "member_node_ids": ["NODE:1"],
                "aliases": [],
                "disposition": "research",
                "reason": "Distinct process",
                "related_concept_ids": [],
                "atomicity": curation_atomicity(),
            }]}},
        }

        context = packets._packet_context(
            self.root, "pathology_node_research", "NODE:1", results
        )

        self.assertEqual(context["source_index"][0]["canonical_publication_id"], "PMID:11")
        self.assertEqual(context["source_index"][0]["year"], 2026)
        self.assertNotIn("canonical_publication_id", document)

    def test_candidate_review_source_index_has_canonical_publication_metadata(self):
        document = {"document_id": "PMID:12", "title": "Drug action", "source": "test"}
        candidate = {
            "candidate_id": "DRUG:1",
            "strategy_ids": ["STRATEGY:1"],
            "graph_node_ids": [],
            "assertion_ids": [],
            "pathology_source_ids": [],
            "mechanism_source_ids": ["PMID:12"],
            "identity": {"source_ids": []},
        }
        results = {
            "evidence_graph": {"records": {
                "source_nodes": [], "source_edges": [], "profiles": [], "assertions": [],
            }},
            "candidate_seed_generation": {"records": {
                "documents": [document], "candidates": [candidate],
                "rescue_strategies": [{"strategy_id": "STRATEGY:1"}],
            }},
        }
        with (
            patch.object(
                packets, "_review_batches",
                return_value=[{"concept_id": "NODE:1", "candidate_ids": ["DRUG:1"]}],
            ),
            patch.object(packets, "_canonical_candidates", return_value=[candidate]),
        ):
            context = packets._packet_context(
                self.root, "candidate_evidence_review", "NODE:1", results
            )

        self.assertEqual(context["source_index"][0]["canonical_publication_id"], "PMID:12")
        self.assertEqual(context["source_index"][0]["year"], 2026)
        self.assertNotIn("canonical_publication_id", document)

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
            packets._validate_packet(invalid_packet, "pathology_curation", None)
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
        profile_fields = contract["records"]["profiles"]["required_fields"]
        self.assertIn("distinct_mechanisms", profile_fields)
        self.assertNotIn("desired_biological_state", profile_fields)
        self.assertNotIn("phenotype_objective", profile_fields)
        for excluded_field in (
            "applicable_stage_population", "measurable_readouts", "causal_prerequisites",
            "invalidating_conditions",
        ):
            self.assertNotIn(excluded_field, profile_fields)
        self.assertTrue(any("temporal_context" in rule for rule in contract["field_rules"]))
        self.assertIn(action["packet_id"], action["worker_prompt"])
        self.assertIn(action["suggested_result_path"], action["worker_prompt"])
        self.assertNotIn("authorized", action["worker_prompt"])
        self.assertIn("read-only graph context returned through that packet", action["worker_prompt"])
        first_item = action["next_item_id"]
        self.assertEqual(first_item, "NODE:1")
        self.assertEqual(
            action["display_item_id"],
            f"pathology_node_research/NODE:1/{storage._item_token('NODE:1')}",
        )
        self.assertIn(action["display_item_id"], action["worker_prompt"])
        node_type = research_packet["context"]["node"]["node_type"]
        self.assertEqual(
            research_packet["context"]["disease_context"][0]["section"],
            "description",
        )
        self.assertEqual(
            [row["node_id"] for row in research_packet["context"]["allowed_assertion_nodes"]],
            ["MONDO:1", "NODE:1"],
        )
        indexed_node = next(
            row
            for row in research_packet["context"]["allowed_assertion_nodes"]
            if row["node_id"] == "NODE:1"
        )
        self.assertEqual(indexed_node["atomicity"], curation_atomicity())
        research_rules = " ".join(contract["field_rules"])
        research_instructions = " ".join([
            research_packet["task"], *research_packet["rules"]
        ])
        self.assertNotIn("Life Science Research", research_instructions)
        self.assertNotIn("structured lookup", research_instructions.casefold())
        endpoint_rule = contracts.PATHOLOGY_ASSERTION_ENDPOINT_RULE
        nested_rule = contracts.NESTED_MECHANISM_RESEARCH_RULE
        self.assertIn(endpoint_rule, research_packet["task"])
        self.assertIn(endpoint_rule, contract["field_rules"])
        self.assertEqual(research_packet["task"].count(endpoint_rule), 1)
        self.assertEqual(contract["field_rules"].count(endpoint_rule), 1)
        self.assertIn(nested_rule, research_packet["task"])
        self.assertIn(nested_rule, contract["field_rules"])
        self.assertEqual(research_packet["task"].count(nested_rule), 1)
        self.assertEqual(contract["field_rules"].count(nested_rule), 1)
        self.assertIn("context.allowed_assertion_nodes", research_rules)
        pathology_records = self.profile(first_item, node_type)
        pathology_records["assertions"] = [
            self.assertion(first_item, "MONDO:1", relation="contributes_to"),
            self.assertion(first_item, "MONDO:1", relation="precedes"),
        ]
        pathology_records["documents"].append(
            {"document_id": "PMID:90", "title": "Unused pathology search hit", "source": "test"}
        )
        self.submit(action, pathology_records)
        accepted_pathology = json.loads(
            storage._item_result_path(self.root, "pathology_node_research", first_item).read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in accepted_pathology["records"]["documents"]],
            ["PMID:1", "PMID:90"],
        )

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_open_questions")
        self.assertTrue((self.root / "results" / "evidence_graph.json").exists())
        graph_result = json.loads(
            (self.root / "results" / "evidence_graph.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in graph_result["records"]["documents"]],
            ["PMID:1", "SRC:1"],
        )
        open_packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            [row["node_id"] for row in open_packet["context"]["graph_index"]],
            ["NODE:1"],
        )
        self.assertNotIn("source_index", open_packet["context"])
        open_contract = open_packet["result_contract"]["records"]["open_questions"]
        self.assertIn("unresolved_basis", open_contract["required_fields"])
        self.assertIn("discriminating_evidence", open_contract["required_fields"])
        self.assertNotIn("gap_type", open_contract["required_fields"])
        invalid_questions = [{
            "question_id": "Q1",
            "question": "Could a missing compensatory route alter the pathological state?",
            "rationale": "Resolving this could change rescue-pathway selection.",
            "node_ids": ["NODE:unknown"],
            "unresolved_basis": (
                "The graph does not establish whether feedback capacity changes the state."
            ),
            "discriminating_evidence": (
                "A timed perturbation separating feedback from correlated pathway activity."
            ),
        }]
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit(action, {"open_questions": invalid_questions})
        questions = [{
            **invalid_questions[0],
            "node_ids": ["NODE:1"],
        }]
        repeated_gap = json.loads(json.dumps(questions[0]))
        repeated_gap.update({
            "question_id": "Q2",
            "question": "Does feedback alter the duration of the pathological state?",
        })
        with self.assertRaisesRegex(core.ProgramError, "repeated unresolved_basis"):
            self.submit(action, {"open_questions": [questions[0], repeated_gap]})
        self.submit(action, {"open_questions": questions})

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_question_research")
        question_packet = json.loads(
            Path(action["packet_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(question_packet["context"]["open_questions"], questions)
        self.assertTrue(question_packet["context"]["source_index"])
        self.assertFalse(any(
            "evidence_passages" in row
            for row in question_packet["context"]["source_index"]
        ))
        question_instructions = " ".join([
            question_packet["task"], *question_packet["rules"]
        ])
        self.assertIn("short discovery pass", question_instructions)
        self.assertIn("primary-source research", question_instructions)
        self.assertNotIn("structured scientific lookup", question_instructions.casefold())
        answer_contract = question_packet["result_contract"]["records"]["question_answers"]
        self.assertEqual(
            answer_contract["field_contracts"]["research_disposition"]["allowed_values"],
            ["corpus_sufficient", "literature_delta_found", "still_unresolved"],
        )
        self.assertEqual(
            answer_contract["field_contracts"]["claims"]["field_contracts"]
            ["epistemic_status"]["allowed_values"],
            ["direct_observation", "synthesis", "inference"],
        )
        with self.assertRaisesRegex(core.ProgramError, "partition every supplied question_id"):
            self.submit(action, {"documents": [], "question_answers": []})
        answer = {
            "question_id": "Q1",
            "question": questions[0]["question"],
            "status": "answered",
            "answer": (
                "A compensatory feedback route can oppose sustained kinase signalling in the "
                "disease-relevant cellular state."
            ),
            "claims": [
                {
                    "claim_id": "CLAIM:BASE",
                    "claim": "The disease graph establishes excessive kinase signalling.",
                    "epistemic_status": "direct_observation",
                    "delta_type": "baseline",
                    "evidence_scope": "Disease-relevant experimental model in the frozen graph.",
                    "assumptions": [],
                    "source_ids": ["PMID:1"],
                },
                {
                    "claim_id": "CLAIM:1",
                    "claim": (
                        "Feedback phosphatase activity can oppose sustained kinase signalling."
                    ),
                    "epistemic_status": "direct_observation",
                    "delta_type": "extends",
                    "evidence_scope": "Perturbed disease-relevant cells during sustained signalling.",
                    "assumptions": [],
                    "source_ids": ["PMID:4"],
                },
            ],
            "node_ids": ["NODE:1"],
            "limitations": ["The magnitude of compensation in human tissue remains uncertain."],
            "research_disposition": "literature_delta_found",
            "frozen_baseline_claim_ids": ["CLAIM:BASE"],
            "counterevidence_claim_ids": [],
            "alternative_explanation_claim_ids": [],
            "material_answer_delta": (
                "New perturbational evidence identifies feedback capacity as a causal constraint."
            ),
            "saturation_reason": None,
        }
        prior = run_state._load_results(self.root)
        corpus_sufficient = json.loads(json.dumps(answer))
        corpus_sufficient.update({
            "status": "answered",
            "claims": [answer["claims"][0]],
            "research_disposition": "corpus_sufficient",
            "frozen_baseline_claim_ids": ["CLAIM:BASE"],
            "material_answer_delta": None,
            "saturation_reason": (
                "The frozen claim directly answers every part of the question; discovery found no "
                "distinct evidential requirement."
            ),
        })
        hypotheses._validate_question_research(
            {"documents": [], "question_answers": [corpus_sufficient]}, prior
        )
        still_unresolved = json.loads(json.dumps(answer))
        still_unresolved.update({
            "status": "unresolved",
            "answer": "The available literature does not discriminate the proposed feedback route.",
            "claims": [],
            "research_disposition": "still_unresolved",
            "frozen_baseline_claim_ids": [],
            "material_answer_delta": None,
            "saturation_reason": (
                "No temporally resolved perturbation study tested the required feedback relationship."
            ),
        })
        hypotheses._validate_question_research(
            {"documents": [], "question_answers": [still_unresolved]}, prior
        )
        aliased_graph_answer = json.loads(json.dumps(answer))
        aliased_graph_answer["claims"][1]["source_ids"] = ["DOI:10.1000/graph-paper"]
        with self.assertRaisesRegex(core.ProgramError, "already present in the frozen corpus"):
            hypotheses._validate_question_research(
                {
                    "documents": [{
                        "document_id": "DOI:10.1000/graph-paper",
                        "title": "Research evidence",
                        "source": "test",
                    }],
                    "question_answers": [aliased_graph_answer],
                },
                prior,
            )
        redundant_citation = json.loads(json.dumps(answer))
        redundant_citation["claims"][1]["delta_type"] = "baseline"
        redundant_citation["frozen_baseline_claim_ids"].append("CLAIM:1")
        with self.assertRaisesRegex(core.ProgramError, "may cite only frozen graph sources"):
            hypotheses._validate_question_research(
                {
                    "documents": [{
                        "document_id": "PMID:4", "title": "Compensatory feedback",
                        "source": "test",
                    }],
                    "question_answers": [redundant_citation],
                },
                prior,
            )
        reused_graph_answer = json.loads(json.dumps(answer))
        reused_graph_answer["claims"][1]["source_ids"] = ["PMID:1"]
        with self.assertRaisesRegex(core.ProgramError, "frozen graph documents"):
            self.submit(action, {
                "documents": [{
                    "document_id": "PMID:1", "title": "Research evidence", "source": "test",
                }],
                "question_answers": [reused_graph_answer],
            })
        unsupported_inference = json.loads(json.dumps(answer))
        unsupported_inference["claims"][1].update({
            "epistemic_status": "inference",
            "assumptions": [],
        })
        with self.assertRaisesRegex(core.ProgramError, "assumptions must not be empty"):
            self.submit(action, {
                "documents": [{
                    "document_id": "PMID:4", "title": "Compensatory feedback", "source": "test",
                }],
                "question_answers": [unsupported_inference],
            })
        self.submit(action, {
            "documents": [{
                "document_id": "PMID:4", "title": "Compensatory feedback", "source": "test",
            }],
            "question_answers": [answer],
        })

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "pathology_hypothesis_synthesis")
        synthesis_packet = json.loads(
            Path(action["packet_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(synthesis_packet["context"]["question_answers"], [answer])
        self.assertIn(
            "PMID:4",
            {row["document_id"] for row in synthesis_packet["context"]["source_index"]},
        )
        question_tags = [{"question_id": "Q1", "node_ids": ["NODE:1"]}]
        with self.assertRaisesRegex(core.ProgramError, "partition every supplied question"):
            hypotheses._validate_hypothesis_synthesis(
                {"documents": [], "question_node_tags": [], "hypothesis_connections": []},
                run_state._load_results(self.root),
            )
        hypotheses._validate_hypothesis_synthesis(
            {"documents": [], "question_node_tags": question_tags, "hypothesis_connections": []},
            run_state._load_results(self.root),
        )
        connection = {
            "connection_id": "CONNECTION:1",
            "title": "Feedback capacity may define a bypass rescue route",
            "node_ids": ["NODE:1"],
            "claim_ids": ["CLAIM:1"],
            "mechanistic_reasoning": (
                "Restoring feedback phosphatase capacity could constrain the sustained kinase "
                "state without directly blocking its initiating lesion."
            ),
            "predicted_rescue_direction": "Increase negative feedback on excessive kinase activity.",
            "why_unexpected": "The route targets endogenous control capacity rather than the driver.",
            "counterargument": "Feedback induction may be too weak or occur in the wrong cell state.",
            "limitations": ["Human disease-tissue feedback capacity is not quantified."],
            "assumptions": [
                "The perturbational feedback relationship is conserved in the affected human cells."
            ],
            "weakest_link": (
                "Feedback magnitude has not been measured in the affected human cell state."
            ),
            "falsifying_observation": (
                "Restoring feedback capacity fails to reduce sustained kinase signalling."
            ),
            "source_ids": ["PMID:4", "PMID:5"],
        }
        baseline_only_connection = json.loads(json.dumps(connection))
        baseline_only_connection.update({
            "claim_ids": ["CLAIM:BASE"],
            "source_ids": ["PMID:1"],
        })
        with self.assertRaisesRegex(core.ProgramError, "literature-delta claim"):
            hypotheses._validate_hypothesis_synthesis(
                {
                    "documents": [], "question_node_tags": question_tags,
                    "hypothesis_connections": [baseline_only_connection],
                },
                run_state._load_results(self.root),
            )
        alternative_connection = json.loads(json.dumps(connection))
        alternative_connection.update({
            "connection_id": "CONNECTION:2",
            "title": "Feedback capacity may also alter recovery kinetics",
            "mechanistic_reasoning": (
                "The same feedback evidence could support faster recovery after signalling peaks "
                "without changing the initiating lesion."
            ),
            "predicted_rescue_direction": "Accelerate recovery from excessive kinase activity.",
            "why_unexpected": (
                "This route changes state duration rather than steady-state pathway amplitude."
            ),
        })
        hypotheses._validate_hypothesis_synthesis(
            {
                "documents": [{
                    "document_id": "PMID:5", "title": "Feedback verification", "source": "test",
                }],
                "question_node_tags": question_tags,
                "hypothesis_connections": [connection, alternative_connection],
            },
            run_state._load_results(self.root),
        )
        invalid_connection = json.loads(json.dumps(connection))
        invalid_connection["source_ids"] = ["PMID:5"]
        with self.assertRaisesRegex(core.ProgramError, "every source behind its selected claims"):
            self.submit(action, {
                "documents": [{
                    "document_id": "PMID:5", "title": "Feedback verification", "source": "test",
                }],
                "question_node_tags": question_tags,
                "hypothesis_connections": [invalid_connection],
            })
        invalid_connection = json.loads(json.dumps(connection))
        invalid_connection["assumptions"] = []
        with self.assertRaisesRegex(core.ProgramError, "assumptions must be a non-empty list"):
            self.submit(action, {
                "documents": [{
                    "document_id": "PMID:5", "title": "Feedback verification", "source": "test",
                }],
                "question_node_tags": question_tags,
                "hypothesis_connections": [invalid_connection],
            })
        self.submit(action, {
            "documents": [{
                "document_id": "PMID:5", "title": "Feedback verification", "source": "test",
            }],
            "question_node_tags": question_tags,
            "hypothesis_connections": [connection],
        })

        action = core.next_action(self.root)
        self.assertEqual(action["next_task"], "candidate_seed_research")
        self.assertEqual(action["next_item_id"], "NODE:1")
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        seed_instructions = " ".join([packet["task"], *packet["rules"]])
        self.assertNotIn("Life Science Research", seed_instructions)
        self.assertNotIn("structured lookup", seed_instructions.casefold())
        candidate_contract = packet["result_contract"]["records"]["candidates"]
        strategy_contract = packet["result_contract"]["records"]["rescue_strategies"]
        document_contract = packet["result_contract"]["records"]["documents"]
        self.assertEqual(strategy_contract["template"]["primary_node_id"], "NODE:1")
        self.assertIn("strategy_key", strategy_contract["required_fields"])
        self.assertIn("linked_node_ids", strategy_contract["required_fields"])
        self.assertIn("connection_ids", strategy_contract["required_fields"])
        self.assertIn("desired_direction", strategy_contract["required_fields"])
        self.assertEqual(
            strategy_contract["field_contracts"]["search_outcome"]["allowed_values"],
            ["seeded", "no_supported_seed"],
        )
        self.assertIn(
            "focal node ID supplied",
            strategy_contract["field_contracts"]["primary_node_id"]["value_rule"],
        )
        template_strategy = packet["result_contract"]["result_template"]["records"][
            "rescue_strategies"
        ]
        self.assertEqual(len(template_strategy), 1)
        self.assertEqual(template_strategy[0]["primary_node_id"], "NODE:1")
        self.assertEqual(
            document_contract["template"]["evidence_passages"],
            [{"text": None, "locator": None}],
        )
        self.assertEqual(
            {key: candidate_contract[key] for key in contracts.ROW_SCHEMAS["candidates"]},
            contracts.ROW_SCHEMAS["candidates"],
        )
        self.assertIn("template", candidate_contract)
        self.assertIn("identifiers", candidate_contract["required_fields"])
        self.assertIn("assertion_ids", candidate_contract["required_fields"])
        self.assertIn("strategy_keys", candidate_contract["required_fields"])
        self.assertIn("graph_rationale", candidate_contract["required_fields"])
        self.assertNotIn("identity", candidate_contract["required_fields"])
        for field in (
            "strategy_keys", "graph_node_ids", "assertion_ids", "pathology_source_ids",
            "mechanism_source_ids",
        ):
            self.assertEqual(candidate_contract["template"][field], [])
        self.assertEqual(candidate_contract["template"]["identifiers"], {})
        self.assertEqual(
            candidate_contract["field_contracts"]["identifiers"]["type"], "object"
        )
        self.assertIn(
            "non-empty list of non-empty strings",
            candidate_contract["field_contracts"]["identifiers"]["value_rule"],
        )
        self.assertEqual(packet["context"]["focal_context"]["node"]["node_id"], "NODE:1")
        self.assertEqual(packet["context"]["focal_context"]["profile"]["node_id"], "NODE:1")
        self.assertEqual(packet["context"]["routed_question_answers"], [answer])
        self.assertEqual(packet["context"]["routed_connections"], [connection])
        self.assertNotIn("connection_index", packet["context"])
        self.assertNotIn("optional discovery leads", seed_instructions)
        self.assertEqual(
            packet["context"]["connection_lookup"]["argv"][-1], "<connection_id>"
        )
        bounded_connection = core.connection_context(self.root, "CONNECTION:1")
        self.assertEqual(
            bounded_connection["context"]["connection"]["connection_id"],
            "CONNECTION:1",
        )
        self.assertEqual(
            [row["claim_id"] for row in bounded_connection["context"]["claims"]],
            ["CLAIM:1"],
        )
        self.assertEqual(
            {
                row["document_id"]
                for row in bounded_connection["context"]["source_index"]
            },
            {"PMID:4", "PMID:5"},
        )
        self.assertIn("context_nodes", packet["context"]["focal_context"])
        self.assertIn("context-node association", seed_instructions)
        self.assertIn("rescue-pathway check", seed_instructions)
        self.assertIn("not biological evidence", seed_instructions)
        assertions_by_relation = {
            row["relation"]: row["assertion_id"]
            for row in packet["context"]["focal_context"]["assertions"]
        }
        selected_assertion_id = assertions_by_relation["contributes_to"]
        self.assertEqual(
            [row["node_id"] for row in packet["context"]["graph_index"]], ["NODE:1"]
        )
        self.assertEqual(packet["context"]["graph_snapshot_id"], core.graph_context(
            self.root, "NODE:1"
        )["graph_snapshot_id"])
        self.assertEqual(packet["context"]["context_lookup"]["argv"][-1], "<node_id>")
        self.assertIn("row-shaped placeholder", " ".join(packet["rules"]))
        with self.assertRaisesRegex(core.ProgramError, "disease anchor"):
            core.graph_context(self.root, "MONDO:1")
        seed_records = {
            "documents": [
                {
                    "document_id": "PMID:1",
                    "title": "Research evidence",
                    "source": "test",
                    "evidence_passages": [{
                        "text": "Seed-specific evidence from the same paper.",
                        "locator": "seed results",
                    }],
                },
                {"document_id": "PMID:2", "title": "Drug MOA", "source": "test"},
                {"document_id": "PMID:80", "title": "Unused seed search hit", "source": "test"},
            ],
            "rescue_strategies": [{
                "strategy_key": "strategy-1",
                "primary_node_id": "NODE:1",
                "linked_node_ids": [],
                "connection_ids": ["CONNECTION:1"],
                "pathological_state": "increased kinase signalling",
                "rescuable_state": "kinase signalling within the normal physiological range",
                "desired_direction": "decrease excessive kinase activity",
                "mechanistic_basis": (
                    "The focal pathology and retained feedback connection support a bypass route."
                ),
                "ownership_rationale": "The rescued signalling state belongs to NODE:1.",
                "assertion_ids": [],
                "source_ids": ["SRC:1"],
                "search_outcome": "seeded",
                "search_summary": "The feedback route produced two supported candidate seeds.",
            }],
            "candidates": [
                {
                    "candidate_id": "CHEMBL:1",
                    "name": "Drug",
                    "identifiers": {"chembl": "CHEMBL1"},
                    "mechanism_hypothesis": "inhibits the process",
                    "graph_node_ids": ["NODE:1"],
                    "assertion_ids": [selected_assertion_id],
                    "graph_rationale": (
                        "The selected contributes_to assertion establishes the focal disease link."
                    ),
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:1", "PMID:2"],
                },
                {
                    "candidate_id": "CHEMBL:2",
                    "name": "Second drug",
                    "identifiers": {"chembl": "CHEMBL2"},
                    "mechanism_hypothesis": "modulates the process",
                    "graph_node_ids": ["NODE:1"],
                    "assertion_ids": [],
                    "graph_rationale": (
                        "The focal pathology profile alone establishes the disease context."
                    ),
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:2"],
                },
            ],
            "exclusions": [],
        }
        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["rescue_strategies"][0]["connection_ids"] = [
            "CONNECTION:missing"
        ]
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit(action, invalid_records)
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
        for malformed_identifiers in (
            {"chembl": {"id": "CHEMBL25"}},
            {"chembl": ""},
            {"chembl": []},
            {"chembl": ["CHEMBL25", ""]},
            {"chembl": [["CHEMBL25"]]},
            {"chembl": 25},
            {"chembl": None},
        ):
            invalid_records = json.loads(json.dumps(seed_records))
            invalid_records["candidates"][0]["identifiers"] = malformed_identifiers
            with self.subTest(identifiers=malformed_identifiers), self.assertRaisesRegex(
                core.ProgramError,
                "must be a non-empty string or a non-empty list of non-empty strings",
            ):
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
        self.assertIn("controller alone owns canonical PMID", review_packet["task"])
        review_instructions = " ".join([
            review_packet["task"], *review_packet["rules"]
        ])
        self.assertNotIn("Life Science Research", review_instructions)
        self.assertNotIn("structured lookup", review_instructions.casefold())
        self.assertNotIn("research service", review_instructions.casefold())
        self.assertIn(
            "Controller validation owns canonical publication identity",
            " ".join(review_packet["rules"]),
        )
        self.assertIn("strategy_ids", review_packet["task"])
        self.assertIn("strategy_ids", " ".join(review_packet["rules"]))
        self.assertTrue(
            any(
                "document retained" in rule
                for rule in review_packet["result_contract"]["field_rules"]
            )
        )
        self.assertNotIn("score_rubric", review_packet["result_contract"])
        self.assertEqual(review_packet["context"]["primary_concept_id"], packet["item_id"])
        self.assertEqual(
            review_packet["context"]["rescue_strategies"][0]["primary_node_id"],
            "NODE:1",
        )
        self.assertEqual(
            review_packet["context"]["rescue_strategies"][0]["connection_ids"],
            ["CONNECTION:1"],
        )
        linked_strategy_id = review_packet["context"]["rescue_strategies"][0][
            "strategy_id"
        ]
        self.assertTrue(linked_strategy_id.startswith("STRATEGY-"))
        self.assertEqual(
            [row["candidate_id"] for row in review_packet["context"]["candidates"]],
            ["UNICHEM:1", "UNICHEM:2"],
        )
        self.assertEqual(
            [row["document_id"] for row in review_packet["context"]["source_index"]],
            ["PMID:1", "PMID:2"],
        )
        selected_graph_evidence = {
            row["candidate_id"]: row
            for row in review_packet["context"]["selected_graph_evidence"]
        }
        self.assertNotIn("graph_node_ids", selected_graph_evidence["UNICHEM:1"])
        selected_source_edges = selected_graph_evidence["UNICHEM:1"]["source_edges"]
        self.assertEqual(len(selected_source_edges), 1)
        self.assertEqual(selected_source_edges[0]["subject_id"], "NODE:1")
        self.assertEqual(selected_source_edges[0]["relation"], "contributes_to")
        self.assertEqual(selected_source_edges[0]["object_id"], "MONDO:1")
        self.assertEqual(
            [
                row["assertion_id"]
                for row in selected_graph_evidence["UNICHEM:1"]["assertions"]
            ],
            [selected_assertion_id],
        )
        self.assertEqual(selected_graph_evidence["UNICHEM:2"]["assertions"], [])
        seeds = json.loads(
            (self.root / "results" / "candidate_seed_generation.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in seeds["records"]["documents"]],
            ["PMID:1", "PMID:2"],
        )
        self.assertEqual(
            seeds["records"]["candidates"][0]["origin_concept_ids"],
            [packet["item_id"]],
        )
        self.assertEqual(
            seeds["records"]["candidates"][0]["strategy_ids"],
            [linked_strategy_id],
        )
        self.submit(
            action,
            {
                "documents": [
                    {
                        "document_id": "PMID:2",
                        "title": "Drug MOA",
                        "source": "test",
                        "evidence_passages": [{
                            "text": "Review-specific evidence from the same paper.",
                            "locator": "review results",
                        }],
                    },
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
                            "finding": "Relevant exposure remains uncertain.",
                            "source_ids": ["PMID:3"],
                        }],
                    },
                    {
                        **self.review("UNICHEM:2", "PMID:3"),
                        "prior_art": {
                            "status": "human_intervention",
                            "summary": "An interpretable exact-disease controlled intervention was published.",
                            "findings": [{
                                "finding": "The candidate achieved relevant exposure against a placebo counterfactual and a disease-relevant outcome.",
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
        self.assertIn(
            "do not search for or add evidence",
            audit_packet["task"].lower(),
        )
        self.assertFalse(
            any("newly retrieved" in rule.lower() for rule in audit_packet["rules"])
        )
        self.assertEqual(len(audit_packet["context"]["candidates"]), 2)
        self.assertEqual(
            audit_packet["context"]["rescue_strategies"][0]["desired_direction"],
            "decrease excessive kinase activity",
        )
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
        source_by_id = {
            row["document_id"]: row
            for row in audit_packet["context"]["source_index"]
        }
        self.assertEqual(
            {row["text"] for row in source_by_id["PMID:1"]["evidence_passages"]},
            {
                "Inspectable evidence from Research evidence",
                "Seed-specific evidence from the same paper.",
            },
        )
        self.assertEqual(
            {row["text"] for row in source_by_id["PMID:2"]["evidence_passages"]},
            {
                "Inspectable evidence from Drug MOA",
                "Review-specific evidence from the same paper.",
            },
        )
        rubric = audit_packet["result_contract"]["score_rubric"]
        self.assertIn("final-card prose defined in their field rules", audit_packet["task"])
        audit_field_rules = " ".join(audit_packet["result_contract"]["field_rules"])
        self.assertIn("predicted corrective or compensatory effect", audit_field_rules)
        self.assertIn("ellipses standing in for omitted text", audit_field_rules)
        self.assertNotIn("remains worth ranking without repeating", audit_packet["task"])
        self.assertEqual(
            audit_packet["result_contract"]["records"]["assessments"]["template"]
            ["source_integrity"],
            {"checks": []},
        )
        self.assertIn("without weighting", rubric["method"])
        self.assertIn("not a probability", rubric["method"])
        self.assertIn("Counterevidence earns no points", rubric["method"])
        self.assertEqual(set(rubric["components"]), set(contracts.SCORE_COMPONENTS))
        self.assertEqual(contracts.MAX_SCORE, 80)
        self.assertIn("any integer from 1", rubric["method"])
        self.assertNotIn("anchors", rubric["components"]["mechanistic_bridge_plausibility"])
        exclusion_policy = audit_packet["result_contract"]["exclusion_policy"]
        self.assertEqual(set(exclusion_policy), set(contracts.AUDIT_EXCLUSION_REASONS))
        novelty_policy = exclusion_policy["exact_disease_prior_use_or_testing"]
        self.assertIn("registered exact-disease therapeutic study", novelty_policy)
        self.assertIn("regardless of outcome, controls, study quality", novelty_policy)
        self.assertIn("computational prediction", novelty_policy)
        self.assertNotIn("human_intervention", exclusion_policy)
        self.assertIn("missing data", exclusion_policy["impossible_translational_feasibility"])
        self.assertIn("unresolved identity alone", exclusion_policy["invalid_candidate"])
        review_result = json.loads(
            (self.root / "results" / "candidate_review.json").read_text()
        )
        self.assertEqual(
            [row["document_id"] for row in review_result["records"]["documents"]],
            ["PMID:2", "PMID:3"],
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
                "finding": "Relevant exposure remains uncertain.",
                "source_ids": ["PMID:3"],
            }],
            "net_assessment": {
                "text": (
                    "The drug's established action is expected to reduce the excessive "
                    "pathological process and move the affected tissue toward its normal state."
                ),
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
                    "reason_code": "exact_disease_prior_use_or_testing",
                    "finding": "The candidate has already been tested in the exact disease.",
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
        accepted_result_paths = {
            path.relative_to(self.root).as_posix()
            for path in (self.root / "results").rglob("*.json")
        }
        self.assertEqual(set(manifest["accepted_results"]), accepted_result_paths)
        self.assertTrue(any(
            path.startswith("results/items/") for path in accepted_result_paths
        ))
        self.assertNotIn("stage_results", manifest)
        summary = (self.root / "outputs" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("raw candidate seeds: 2; deduplicated candidates: 2", summary)
        self.assertIn("rescue strategies: 1 (0 without a supported seed)", summary)
        self.assertIn("4 20-point components out of 80", summary)
        self.assertIn("## Graph coverage", summary)
        self.assertIn("Candidates per graph node: NODE:1 (Process): 2", summary)
        self.assertIn("Nodes with no candidate: none", summary)
        self.assertIn("Candidates using more than one node: none", summary)
        self.assertIn("Candidates using context-only nodes: none", summary)
        provenance = [
            json.loads(line)
            for line in (self.root / "outputs" / "candidate_provenance.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(provenance), 1)
        self.assertEqual(provenance[0]["assertion_ids"], [selected_assertion_id])
        self.assertEqual(provenance[0]["strategy_ids"], [linked_strategy_id])
        self.assertNotIn(assertions_by_relation["precedes"], provenance[0]["assertion_ids"])
        self.assertIn("contributes_to assertion", provenance[0]["graph_rationale"])
        exported_strategies = [
            json.loads(line)
            for line in (self.root / "outputs" / "rescue_strategies.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [row["strategy_id"] for row in exported_strategies],
            [linked_strategy_id],
        )
        csv_lines = (self.root / "outputs" / "candidates.csv").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertNotIn("candidate_id", csv_lines[0].split(","))
        self.assertEqual(csv_lines[0].split(",")[1], "name")
        self.assertEqual(csv_lines[1].split(",")[1], "Drug")
        cards = (self.root / "outputs" / "candidate_cards.md").read_text(encoding="utf-8")
        self.assertIn("## Drug", cards)
        self.assertNotIn("UNICHEM:1", cards)
        self.assertIn(
            "### How it could work\n\n"
            "The drug's established action is expected to reduce the excessive pathological "
            "process and move the affected tissue toward its normal state. [PMID:3]",
            cards,
        )
        self.assertIn(
            "### Reasons why not\n\n"
            "Relevant exposure remains uncertain. [PMID:3]",
            cards,
        )
        for hidden in (
            "Aliases:", "Score:", "Source verification:", "Citation-audit exceptions:",
            "Drug-action confidence:", "Mechanistic-bridge plausibility:", "Priority tier:",
            "Mechanism hypothesis:", "Review:", "Audit:", "References:",
        ):
            self.assertNotIn(hidden, cards)
        exclusions = (self.root / "outputs" / "candidate_exclusions.csv").read_text()
        self.assertIn("candidate_id,name,reason_code,finding,source_ids", exclusions)
        self.assertIn(
            "UNICHEM:2,Second drug,exact_disease_prior_use_or_testing,"
            "The candidate has already been tested in the exact disease.,PMID:3",
            exclusions,
        )
        self.assertFalse((self.root / "outputs" / "candidate_exclusions.jsonl").exists())
        self.assertEqual(core.status(self.root)["state"], "complete")
        self.assertEqual(core.build_outputs(self.root), manifest)

        normalized_stage_hashes = {}
        for stage in core.STAGES:
            result = json.loads(storage._result_path(self.root, stage).read_text(encoding="utf-8"))
            result.pop("packet_id", None)
            normalized_stage_hashes[stage] = storage._sha256(storage._canonical_bytes(result))
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

        item_result = next((self.root / "results" / "items").rglob("*.json"))
        accepted_bytes = item_result.read_bytes()
        item_result.write_bytes(accepted_bytes + b"\n")
        with self.assertRaisesRegex(core.ProgramError, "Accepted result changed"):
            core.status(self.root)
        item_result.write_bytes(accepted_bytes)
        self.assertEqual(core.status(self.root)["state"], "complete")

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

        context = packets._packet_context(
            self.root, "pathology_curation", None, {"pathology_sources": source}
        )
        guidance = contracts.STAGE_GUIDANCE["pathology_curation"]["task"]

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
        self.assertIn("do not establish equivalence", guidance)
        self.assertIn("same-label gene-level", guidance)
        self.assertIn("Merge true duplicate records", guidance)
        self.assertIn("context_only even when measurable", guidance)
        self.assertIn("different causal levels", guidance)
        pathology_guidance = contracts.STAGE_GUIDANCE["pathology_node_research"]["task"]
        self.assertIn("Keep discovery pathology-led", pathology_guidance)
        self.assertIn("directional evidence", pathology_guidance)
        self.assertIn("do not split, merge, redefine, or formulate a rescue objective", pathology_guidance)
        self.assertIn("distinct_mechanisms", pathology_guidance)
        self.assertIn("Do not duplicate an indexed profile", pathology_guidance)
        self.assertIn("evidence_context", pathology_guidance)
        self.assertIn("Python assigns the final assertion ID", pathology_guidance)
        self.assertNotIn("Life Science Research", pathology_guidance)
        self.assertNotIn("structured lookup", pathology_guidance.casefold())
        landscape_guidance = contracts.STAGE_GUIDANCE["pathology_landscape_scan"]["task"]
        self.assertIn("coverage-gap register from the Monarch and DisMech index", landscape_guidance)
        self.assertIn("structured scientific lookup may be used transiently", landscape_guidance)
        self.assertIn("sharpen an Asta query", landscape_guidance)
        self.assertIn("does not replace the required Asta search", landscape_guidance)
        self.assertIn("Turn the resulting questions into the", landscape_guidance)
        self.assertIn("prescribed broad and focused Asta searches", landscape_guidance)
        seed_guidance = contracts.STAGE_GUIDANCE["candidate_seed_research"]["task"]
        self.assertIn("assertion_ids", seed_guidance)
        self.assertIn("graph_rationale", seed_guidance)
        self.assertIn("every immediate source edge", seed_guidance)
        self.assertIn("neighbouring node", seed_guidance)
        self.assertNotIn("cross-node use is never mandatory", seed_guidance)
        self.assertIn("do not use disease-specific drug literature", seed_guidance)
        self.assertIn("before searching for any drug", seed_guidance)
        self.assertIn("rescue_strategies", seed_guidance)
        self.assertNotIn("Life Science Research", seed_guidance)
        self.assertNotIn("structured lookup", seed_guidance.casefold())
        self.assertIn(
            "evidence dossier", contracts.STAGE_GUIDANCE["candidate_evidence_review"]["task"]
        )
        self.assertIn(
            "exact-disease prior art",
            contracts.STAGE_GUIDANCE["candidate_evidence_review"]["task"],
        )
        review_guidance = contracts.STAGE_GUIDANCE["candidate_evidence_review"]["task"]
        self.assertIn("ordinary literature discovery to evidence saturation", review_guidance)
        self.assertNotIn("Life Science Research", review_guidance)
        self.assertNotIn("structured lookup", review_guidance.casefold())
        self.assertNotIn("research service", review_guidance.casefold())
        self.assertIn("controller alone owns canonical PMID", review_guidance)
        self.assertIn("strategy_ids", review_guidance)
        for guidance_text in (
            pathology_guidance, landscape_guidance, seed_guidance, review_guidance,
        ):
            self.assertNotIn("may be skipped without penalty", guidance_text)
            self.assertNotIn("optional when directly relevant", guidance_text)
        audit_guidance = contracts.STAGE_GUIDANCE["candidate_audit"]["task"]
        self.assertIn("strategy_ids", audit_guidance)
        for task in (
            "pathology_node_research", "candidate_seed_research",
            "candidate_evidence_review",
        ):
            with self.subTest(task=task):
                self.assertNotIn("Asta", contracts.STAGE_GUIDANCE[task]["task"])
                self.assertNotIn("Undermind", contracts.STAGE_GUIDANCE[task]["task"])
        self.assertIn(
            "unresolved identity", " ".join(contracts.FIELD_RULES["candidate_audit"])
        )

    def test_curation_policy_has_one_markdown_owner(self):
        root = Path(__file__).resolve().parents[1]
        authoritative_sources = {
            "guidance": contracts.STAGE_GUIDANCE["pathology_curation"]["task"],
            "packet contract": (
                root / "references" / "packet-contract.md"
            ).read_text(encoding="utf-8"),
        }
        summary_sources = {
            "skill": (root / "SKILL.md").read_text(encoding="utf-8"),
            "architecture": (
                root / "references" / "architecture.md"
            ).read_text(encoding="utf-8"),
        }
        combined_policy = (
            r"concept distinctness does not create a research job.*"
            r"researchability may not be deferred to deep research.*"
            r"bare gene or gene-disease association.*"
            r"generic gene and lesion-specific claims do not both create research routes.*"
            r"distinct intervention variable"
        )
        for label, text in authoritative_sources.items():
            with self.subTest(source=label):
                self.assertRegex(" ".join(text.casefold().split()), combined_policy)
        for label, text in summary_sources.items():
            with self.subTest(summary=label):
                self.assertNotRegex(" ".join(text.casefold().split()), combined_policy)

    def test_candidate_seed_guidance_combines_focal_anchor_with_linked_context(self):
        guidance = contracts.STAGE_GUIDANCE["candidate_seed_research"]["task"]
        self.assertRegex(
            guidance,
            r"before searching for any drug.*rescue_strategies.*"
            r"supplied linked graph node may support a symptomatic or compensatory candidate.*"
            r"mechanistically justified",
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
                    "atomicity": curation_atomicity(),
                }
            ]
        }
        prior = {"pathology_sources": source_result()}
        pathology._validate_curation(records, prior)
        records["concepts"][0]["member_node_ids"] = ["UNKNOWN"]
        records["concepts"][0]["concept_id"] = "UNKNOWN"
        with self.assertRaisesRegex(core.ProgramError, "partition every supplied"):
            pathology._validate_curation(records, prior)

    def test_curation_structurally_owns_atomicity(self):
        concept = {
            "concept_id": "NODE:1",
            "preferred_label": "Process",
            "concept_type": "mechanism",
            "member_node_ids": ["NODE:1"],
            "aliases": [],
            "disposition": "research",
            "reason": "Distinct mechanism",
            "related_concept_ids": [],
            "atomicity": curation_atomicity(),
        }
        prior = {"pathology_sources": source_result()}
        pathology._validate_curation({"concepts": [concept]}, prior)
        self.assertIn("atomicity", contracts.ROW_SCHEMAS["concepts"]["required_fields"])

        missing = json.loads(json.dumps(concept))
        del missing["atomicity"]
        with self.assertRaisesRegex(core.ProgramError, "missing fields: atomicity"):
            pathology._validate_curation({"concepts": [missing]}, prior)

        conflicting = json.loads(json.dumps(concept))
        conflicting["proposed_splits"] = []
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            pathology._validate_curation({"concepts": [conflicting]}, prior)

        context = json.loads(json.dumps(concept))
        context.update({
            "concept_type": "context",
            "disposition": "context_only",
            "related_concept_ids": ["NODE:1"],
        })
        with self.assertRaisesRegex(core.ProgramError, "must be null unless disposition is research"):
            pathology._validate_curation({"concepts": [context]}, prior)
        self.assertIsNone(curation_atomicity("context_only"))

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
                            "atomicity": curation_atomicity(),
                        }
                    ]
                }
            },
        }
        nodes, edges = pathology._canonical_source_records(results)
        self.assertEqual([row["node_id"] for row in nodes], ["MONDO:1", "NODE:1"])
        self.assertEqual(nodes[1]["member_node_ids"], ["NODE:1", "NODE:2"])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["subject_id"], "NODE:1")
        self.assertEqual(edges[0]["object_id"], "MONDO:1")

    def test_canonical_alias_order_is_stable_across_hash_seeds(self):
        results = {
            "pathology_sources": {"records": {
                "source_nodes": [
                    {
                        "node_id": "MONDO:1",
                        "label": "Disease",
                        "node_type": "disease_anchor",
                        "source_ids": ["SRC:1"],
                    },
                    {
                        "node_id": "NODE:1",
                        "label": "Motor Neuron Atrophy",
                        "node_type": "phenotype",
                        "source_ids": ["SRC:1"],
                    },
                    {
                        "node_id": "NODE:2",
                        "label": "Motor neuron atrophy",
                        "node_type": "phenotype",
                        "source_ids": ["SRC:1"],
                    },
                ],
                "source_edges": [],
            }},
            "pathology_curation": {"records": {"concepts": [{
                "concept_id": "NODE:1",
                "preferred_label": "Motor neuron loss",
                "concept_type": "phenotype",
                "member_node_ids": ["NODE:1", "NODE:2"],
                "aliases": ["Motor neuron atrophy", "Motor Neuron Atrophy"],
                "disposition": "research",
                "reason": "Equivalent source concepts",
                "related_concept_ids": [],
                "atomicity": curation_atomicity(),
            }]}},
        }
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(SCRIPTS)!r});"
            "from repurposing_program.pathology import _canonical_source_records;"
            f"nodes,_=_canonical_source_records(json.loads({json.dumps(json.dumps(results))}));"
            "print(json.dumps(nodes,sort_keys=True,separators=(',',':')))"
        )
        outputs = []
        for seed in ("1", "3"):
            environment = dict(os.environ, PYTHONHASHSEED=seed)
            outputs.append(subprocess.check_output(
                [sys.executable, "-c", script], env=environment, text=True
            ))

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(
            json.loads(outputs[0])[1]["aliases"],
            ["Motor Neuron Atrophy", "Motor neuron atrophy"],
        )

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
                "atomicity": curation_atomicity(),
            }
            for node_id in ("NODE:1", "NODE:2")
        ]

        with self.assertRaisesRegex(core.ProgramError, "duplicates the retained type and label"):
            pathology._validate_curation(
                {"concepts": concepts}, {"pathology_sources": source}
            )

    def test_graph_support_ids_exclude_contradicting_assertion_evidence(self):
        graph = {
            "source_nodes": [{
                "node_id": "NODE:1",
                "node_type": "mechanism",
                "source_ids": ["SRC:NODE"],
            }],
            "profiles": [],
            "source_edges": [],
            "assertions": [{
                "subject_id": "NODE:1",
                "relation": "contributes_to",
                "object_id": "NODE:2",
                "evidence_context": [
                    {"polarity": "supports", "source_ids": ["SRC:SUPPORTS"]},
                    {"polarity": "contradicts", "source_ids": ["SRC:CONTRADICTS"]},
                ],
            }],
        }

        self.assertEqual(
            graph_rules._graph_support_ids(graph)["NODE:1"],
            {"SRC:NODE", "SRC:SUPPORTS"},
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
                "atomicity": curation_atomicity(disposition),
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
        pathology._validate_curation(results["pathology_curation"]["records"], results)
        self.assertEqual(run_state._item_ids("evidence_graph", results), ["NODE:1"])
        nodes, edges = pathology._canonical_source_records(results)
        self.assertEqual(
            [row["node_id"] for row in nodes], ["MONDO:1", "NODE:1", "NODE:2"]
        )
        context_node = next(row for row in nodes if row["node_id"] == "NODE:2")
        self.assertEqual(context_node["related_concept_ids"], ["NODE:1"])
        self.assertFalse(
            any(row.get("relation") == "contextualizes" for row in edges)
        )
        self.assertTrue(all(row["original_edge_ids"] for row in edges))
        graph = {
            "source_nodes": nodes,
            "source_edges": edges,
            "profiles": [self.profile("NODE:1", "mechanism")["profiles"][0]],
            "assertions": [],
        }
        self.assertEqual(
            [row["node_id"] for row in graph_rules._graph_index(graph)],
            ["NODE:1", "NODE:2"],
        )
        context = graph_rules._graph_node_context(graph, "NODE:2")
        self.assertIsNone(context["profile"])
        self.assertEqual(context["related_nodes"], [])
        self.assertEqual([row["node_id"] for row in context["context_nodes"]], ["NODE:1"])
        focal_context = graph_rules._graph_node_context(graph, "NODE:1")
        self.assertEqual([row["node_id"] for row in focal_context["context_nodes"]], ["NODE:2"])
        self.assertEqual(graph_rules._graph_support_ids(graph)["NODE:2"], {"SRC:2"})

        graph["documents"] = [
            *source["records"]["documents"],
            *self.profile("NODE:1", "mechanism")["documents"],
        ]
        selected_assertion = graph_rules._merge_assertions([
            self.assertion("NODE:1", "NODE:2", source_id="SRC:2")
        ])[0]
        graph["assertions"] = [selected_assertion]
        results["evidence_graph"] = {"records": graph}
        seed_records = {
            "documents": [
                {"document_id": "PMID:2", "title": "Drug action", "source": "test"}
            ],
            "rescue_strategies": [{
                "strategy_key": "strategy-1",
                "primary_node_id": "NODE:1",
                "linked_node_ids": ["NODE:2"],
                "connection_ids": [],
                "pathological_state": "increased kinase signalling",
                "rescuable_state": "kinase signalling within its physiological range",
                "desired_direction": "decrease excessive kinase activity",
                "mechanistic_basis": "The focal pathology profile supports this direction.",
                "ownership_rationale": "The rescued kinase state belongs to NODE:1.",
                "assertion_ids": [],
                "source_ids": ["SRC:1", "SRC:2"],
                "search_outcome": "seeded",
                "search_summary": "The route produced a supported candidate seed.",
            }],
            "candidates": [
                {
                    "candidate_id": "CHEMBL:1",
                    "name": "Drug",
                    "identifiers": {"chembl": "CHEMBL1"},
                    "mechanism_hypothesis": "Mechanism using both contexts",
                    "strategy_keys": ["strategy-1"],
                    "graph_node_ids": ["NODE:1", "NODE:2"],
                    "assertion_ids": [],
                    "graph_rationale": (
                        "The focal profile and linked context node support the hypothesis."
                    ),
                    "pathology_source_ids": ["SRC:1"],
                    "mechanism_source_ids": ["PMID:2"],
                }
            ],
            "exclusions": [],
        }
        missing_strategy = json.loads(json.dumps(seed_records))
        del missing_strategy["rescue_strategies"]
        with self.assertRaisesRegex(core.ProgramError, "records.rescue_strategies"):
            candidate_rules._validate_seed_item(missing_strategy, "NODE:1", results)
        legacy_strategy = json.loads(json.dumps(seed_records))
        legacy_strategy["rescue_strategies"][0]["node_id"] = legacy_strategy[
            "rescue_strategies"
        ][0].pop("primary_node_id")
        with self.assertRaisesRegex(core.ProgramError, "unsupported field node_id"):
            candidate_rules._validate_seed_item(legacy_strategy, "NODE:1", results)
        controller_strategy_id = json.loads(json.dumps(seed_records))
        controller_strategy_id["rescue_strategies"][0]["strategy_id"] = (
            "STRATEGY:controller-owned"
        )
        with self.assertRaisesRegex(core.ProgramError, "strategy_id is controller-owned"):
            candidate_rules._validate_seed_item(controller_strategy_id, "NODE:1", results)
        missing_candidate_link = json.loads(json.dumps(seed_records))
        del missing_candidate_link["candidates"][0]["strategy_keys"]
        with self.assertRaisesRegex(core.ProgramError, "copy one or more keys"):
            candidate_rules._validate_seed_item(missing_candidate_link, "NODE:1", results)
        controller_candidate_ids = json.loads(json.dumps(seed_records))
        controller_candidate_ids["candidates"][0]["strategy_ids"] = (
            controller_candidate_ids["candidates"][0].pop("strategy_keys")
        )
        with self.assertRaisesRegex(core.ProgramError, "strategy_ids is controller-owned"):
            candidate_rules._validate_seed_item(controller_candidate_ids, "NODE:1", results)
        invalid_strategy = json.loads(json.dumps(seed_records))
        invalid_strategy["rescue_strategies"][0]["desired_direction"] = ""
        with self.assertRaisesRegex(core.ProgramError, "missing required fields: desired_direction"):
            candidate_rules._validate_seed_item(invalid_strategy, "NODE:1", results)
        seed_records["rescue_strategies"][0]["source_ids"] = ["SRC:1"]
        with self.assertRaisesRegex(core.ProgramError, "do not support graph nodes"):
            candidate_rules._validate_seed_item(seed_records, "NODE:1", results)
        seed_records["rescue_strategies"][0]["source_ids"].append("SRC:2")
        seed_records["candidates"][0]["pathology_source_ids"].append("SRC:2")
        candidate_rules._validate_seed_item(seed_records, "NODE:1", results)

        multiple = json.loads(json.dumps(seed_records))
        second_strategy = {
            "strategy_key": "strategy-2",
            "primary_node_id": "NODE:1",
            "linked_node_ids": [],
            "connection_ids": [],
            "pathological_state": "reduced cellular resilience",
            "rescuable_state": "cellular resilience sufficient to preserve function",
            "desired_direction": "increase cellular resilience",
            "mechanistic_basis": "The focal profile supports an independent compensatory route.",
            "ownership_rationale": "The preserved function belongs to the focal NODE:1 state.",
            "assertion_ids": [],
            "source_ids": ["SRC:1"],
            "search_outcome": "seeded",
            "search_summary": "The compensatory route produced a supported candidate seed.",
        }
        multiple["rescue_strategies"].append(second_strategy)
        second_candidate = json.loads(json.dumps(multiple["candidates"][0]))
        second_candidate.update({
            "candidate_id": "CHEMBL:2",
            "name": "Second drug",
            "identifiers": {"chembl": "CHEMBL2"},
            "strategy_keys": ["strategy-2"],
            "graph_node_ids": ["NODE:1"],
            "pathology_source_ids": ["SRC:1"],
        })
        multiple["candidates"].append(second_candidate)
        candidate_rules._validate_seed_item(multiple, "NODE:1", results)

        duplicate = json.loads(json.dumps(seed_records))
        repeated = json.loads(json.dumps(duplicate["rescue_strategies"][0]))
        repeated["strategy_key"] = "strategy-duplicate"
        duplicate["rescue_strategies"].append(repeated)
        with self.assertRaisesRegex(core.ProgramError, "repeated biological strategy"):
            candidate_rules._validate_seed_item(duplicate, "NODE:1", results)

        unseeded = json.loads(json.dumps(seed_records))
        no_seed = json.loads(json.dumps(second_strategy))
        no_seed["search_outcome"] = "no_supported_seed"
        no_seed["search_summary"] = "No established action supported a candidate seed."
        unseeded["rescue_strategies"].append(no_seed)
        candidate_rules._validate_seed_item(unseeded, "NODE:1", results)
        unseeded["candidates"][0]["strategy_keys"].append("strategy-2")
        with self.assertRaisesRegex(core.ProgramError, "no_supported_seed strategies"):
            candidate_rules._validate_seed_item(unseeded, "NODE:1", results)

        only_unseeded = json.loads(json.dumps(seed_records))
        only_unseeded["rescue_strategies"] = [no_seed]
        only_unseeded["candidates"] = []
        candidate_rules._validate_seed_item(only_unseeded, "NODE:1", results)

        wrong_owner = json.loads(json.dumps(seed_records))
        wrong_owner["rescue_strategies"][0]["primary_node_id"] = "NODE:2"
        with self.assertRaisesRegex(core.ProgramError, "must equal the focal item concept"):
            candidate_rules._validate_seed_item(wrong_owner, "NODE:1", results)

        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["candidates"][0]["assertion_ids"] = ["ASSERTION:UNKNOWN"]
        with self.assertRaisesRegex(core.ProgramError, "assertion_ids contains unknown IDs"):
            candidate_rules._validate_seed_item(invalid_records, "NODE:1", results)

        invalid_records = json.loads(json.dumps(seed_records))
        invalid_records["candidates"][0]["assertion_ids"] = [
            selected_assertion["assertion_id"]
        ]
        invalid_records["candidates"][0]["graph_node_ids"] = ["NODE:1"]
        with self.assertRaisesRegex(core.ProgramError, "include selected assertion nodes"):
            candidate_rules._validate_seed_item(invalid_records, "NODE:1", results)

        seed_records["candidates"][0]["assertion_ids"] = [
            selected_assertion["assertion_id"]
        ]
        candidate_rules._validate_seed_item(seed_records, "NODE:1", results)

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
        records["profiles"][0]["rescuable_state"] = "normalize the process"
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["distinct_mechanisms"] = [{
            "label": "Distinct inflammatory component", "normal_state": "Regulated activity.",
            "pathological_state": "Sustained activity.",
            "biological_direction": "increased activity", "causal_level": "cellular",
            "compartment": "affected cells", "relationship_to_focal": "Downstream component.",
            "index_status": "unindexed_distinct", "indexed_node_id": None,
            "limitations": [], "source_ids": [],
        }]
        with self.assertRaisesRegex(core.ProgramError, "source_ids must be a non-empty list"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["profiles"][0]["established_pathology_observations"] = [
            {"observation": "unsupported", "source_ids": ["PMID:999"]}
        ]
        with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["assertions"] = [self.assertion(action["next_item_id"], "MONDO:1")]
        records["assertions"][0]["assertion_id"] = "ASSERTION:WORKER"
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["assertions"] = [self.assertion(action["next_item_id"], "MONDO:1")]
        records["assertions"][0]["evidence_context"][0]["evidence_type"] = "generic"
        with self.assertRaisesRegex(core.ProgramError, "evidence_type must be one of"):
            self.submit(action, records)

        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["assertions"] = [
            self.assertion(action["next_item_id"], "NCBIGene:785")
        ]
        with self.assertRaisesRegex(
            core.ProgramError,
            "context.allowed_assertion_nodes.*NCBIGene:785",
        ):
            self.submit(action, records)
        rejected_status = core.status(self.root)
        self.assertEqual(rejected_status["state"], "needs_agent")
        self.assertEqual(rejected_status["next_task"], "pathology_node_research")
        self.assertEqual(rejected_status["next_item_id"], action["next_item_id"])

        self.submit(
            action,
            self.profile(action["next_item_id"], packet["context"]["node"]["node_type"]),
        )
        resumed_action = core.next_action(self.root)
        self.assertEqual(resumed_action["next_task"], "pathology_open_questions")

    def test_preflight_reuses_the_ready_packet_and_does_not_accept(self):
        self.curate_single_process()
        action = core.next_action(self.root)
        packet = json.loads(Path(action["packet_path"]).read_text(encoding="utf-8"))
        records = self.profile(action["next_item_id"], packet["context"]["node"]["node_type"])
        records["documents"][0]["evidence_passages"] = [{
            "text": "Inspectable research evidence.", "locator": "results"
        }]
        records["profiles"][0]["distinct_mechanisms"] = "not a list"
        result = {
            "stage": action["next_task"], "item_id": action["next_item_id"],
            "packet_id": action["packet_id"], "status": "complete",
            "records": records, "gaps": [], "notes": [],
        }
        path = self.root / "preflight.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(core.ProgramError, "distinct_mechanisms must be a list"):
            core.validate_submission(self.root, path)
        self.assertFalse(storage._item_result_path(
            self.root, "pathology_node_research", action["next_item_id"]
        ).exists())

        result["records"]["profiles"][0]["distinct_mechanisms"] = []
        path.write_text(json.dumps(result), encoding="utf-8")
        self.assertEqual(core.validate_submission(self.root, path)["valid"], True)
        self.assertEqual(core.next_action(self.root)["packet_id"], action["packet_id"])
        core.submit(self.root, path)

    def test_canonical_document_identifier_families(self):
        for document_id in (
            "PMID:10195180", "PMCID:PMC10338806", "DOI:10.1002/ana.21147",
            "MONARCH-ASSOC-" + "A" * 24, "CLINGEN:CCID004621",
            "UNIPROT:P09651", "NCBI:NBK551641", "S2:" + "a" * 40,
            "https://example.org/report",
        ):
            self.assertIsNotNone(contracts.CANONICAL_DOCUMENT_ID.fullmatch(document_id))
        self.assertIsNone(contracts.CANONICAL_DOCUMENT_ID.fullmatch("DOC-AUTHOR-2026-TOPIC"))

    def test_document_metadata_enriches_only_when_identity_fields_agree(self):
        documents = evidence._merge_documents([
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
            evidence._merge_documents([
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
                bibliography._canonicalize_documents(root, [document], verify_titles=True)
            projected = bibliography._canonicalize_documents(root, [document], verify_titles=False)[0]

        self.assertEqual(projected["title"], "Canonical article title")
        self.assertEqual(projected["submitted_title"], "Incorrect title")
        self.assertEqual(projected["canonical_publication_id"], "PMID:12024045")

    def test_bibliographic_title_verification_normalizes_formatting(self):
        canonical = {
            "PMID:35584812": {
                "title": "Disease progression in a SOD1(G93A) mouse model.",
                "canonical_publication_id": "PMID:35584812",
                "identifier_aliases": ["PMID:35584812"],
                "metadata_source": "PubMed",
            },
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                bibliography,
                "_resolve_bibliographic_metadata",
                return_value=canonical,
            ),
        ):
            accepted = bibliography._canonicalize_documents(
                Path(directory),
                [{
                    "document_id": "PMID:35584812",
                    "title": "Disease progression in a SOD1G93A mouse model",
                }],
                verify_titles=True,
            )

        self.assertEqual(
            accepted[0]["title"], "Disease progression in a SOD1(G93A) mouse model."
        )

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
            evidence._cited_ids(records),
            {"UPSTREAM:1", "PMID:10", "PMID:11"},
        )
        self.assertEqual(
            [row["document_id"] for row in evidence._cited_documents(records)],
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
            [row["document_id"] for row in evidence._all_documents(identity_results)],
            ["PMID:13"],
        )

    def test_non_document_identity_conflicts_remain_strict(self):
        with self.assertRaisesRegex(core.ProgramError, "Conflicting profiles records"):
            evidence._merge_unique(
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

        with patch.object(identity, "_post_unichem", response):
            enriched, receipts = identity._resolve_seed_identities(self.root, rows)
        records = {"candidates": enriched}
        groups = identity._exact_identity_groups(records)
        self.assertEqual(groups["UNICHEM:42"], ["SEED-A", "SEED-B"])
        self.assertEqual(groups["UNICHEM:10"], ["SEED-10"])
        self.assertEqual(groups["UNICHEM:20"], ["SEED-20"])
        self.assertEqual(len(receipts), 8)
        self.assertEqual(
            {
                row["seed_id"]: row["identity_resolution"]["status"]
                for row in identity._identity_queue(records)
            },
            {
                "SEED-10": "connectivity_match",
                "SEED-20": "connectivity_match",
                "SEED-NONE": "not_queryable",
                "SEED-NO-RESULT": "no_result",
            },
        )

    def test_malformed_identifier_does_not_reach_unichem(self):
        row = self.seed(
            "CHEMBL:25",
            "SEED-MALFORMED",
            identifiers={"chembl": {"id": "CHEMBL25"}},
        )

        with patch.object(identity, "_post_unichem") as request, self.assertRaisesRegex(
            core.ProgramError, "candidate.identifiers.chembl"
        ):
            identity._resolve_seed_identities(self.root, [row])

        request.assert_not_called()

    def test_automatic_exact_resolution_retains_all_identifiers(self):
        resolution = {"status": "exact", "uci": "42"}
        seeds = [
            self.seed(
                "PUBCHEM:42", "SEED-B", name="zeta drug",
                identifiers={"pubchem_cid": "42", "alias": ["zeta", "shared"]},
                resolution=resolution,
            ),
            self.seed(
                "DRUGBANK:DB42", "SEED-A", name="Alpha drug",
                identifiers={"drugbank": "DB42", "alias": ["shared", "alpha"]},
                resolution=resolution,
            ),
        ]

        [candidate] = identity._canonical_candidates({
            "candidate_seed_generation": {"records": {"candidates": seeds}}
        }, reviewed=False)

        self.assertEqual(candidate["name"], "Alpha drug")
        self.assertEqual(candidate["identity"]["identifiers"], {
            "alias": ["alpha", "shared", "zeta"],
            "drugbank": "DB42",
            "pubchem_cid": "42",
            "unichem_uci": "42",
        })

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

        context = packets._packet_context(
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
        rules = " ".join(contracts.FIELD_RULES["candidate_identity"])
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
            identity._validate_candidate_identity(records, prior)

        records["identity_groups"][0]["canonical_candidate_id"] = None
        identity._validate_candidate_identity(records, prior)

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
        seeds[0]["assertion_ids"] = ["ASSERTION:A"]
        seeds[0]["graph_rationale"] = "Assertion A supports the first origin concept."
        seeds[1]["assertion_ids"] = ["ASSERTION:B"]
        seeds[1]["graph_rationale"] = "Assertion B supports the second origin concept."
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

        identity._validate_candidate_identity(records, prior)
        prior["candidate_identity"] = {"records": records}
        candidates = identity._canonical_candidates(prior)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_id"], "UNICHEM:121892")
        self.assertEqual(candidates[0]["member_seed_ids"], ["SEED-A", "SEED-B"])
        self.assertEqual(candidates[0]["assertion_ids"], ["ASSERTION:A", "ASSERTION:B"])
        self.assertIn("first origin concept", candidates[0]["graph_rationale"])
        self.assertIn("second origin concept", candidates[0]["graph_rationale"])
        records["identity_groups"][0]["member_seed_ids"] = ["SEED-A"]
        with self.assertRaisesRegex(core.ProgramError, "cannot split an exact UniChem"):
            identity._validate_candidate_identity(records, prior)

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
            identifiers={"inn": ["retigabine", "retigabine"]},
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
                "identifiers": {"inn": ["ezogabine", "retigabine"]},
                "reason": "Authoritative synonym",
                "source_ids": ["https://example.org/synonym"],
            }],
        }

        identity._validate_candidate_identity(records, prior)
        prior["candidate_identity"] = {"records": records}
        candidates = identity._canonical_candidates(prior)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["member_seed_ids"], ["SEED-ALIAS", "SEED-EXACT"])
        self.assertEqual(candidates[0]["name"], "retigabine")
        self.assertEqual(candidates[0]["identity"]["identifiers"], {
            "inn": ["ezogabine", "retigabine"],
            "pubchem_cid": "121892",
            "unichem_uci": "121892",
        })

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
                "atomicity": curation_atomicity(),
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
        with patch.object(candidate_rules, "_canonical_candidates", return_value=candidates):
            first = candidate_rules._review_batches(results)
        with patch.object(
            candidate_rules, "_canonical_candidates", return_value=list(reversed(candidates))
        ):
            second = candidate_rules._review_batches(results)

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
        with patch.object(candidate_rules, "_review_batches", return_value=batch):
            candidate_rules._validate_review_item(records, "NODE:A", {})
            records["reviews"][0]["prior_art"]["search_status"] = "completed"
            with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
                candidate_rules._validate_review_item(records, "NODE:A", {})
            del records["reviews"][0]["prior_art"]["search_status"]
            records["reviews"][0]["aliases"] = [{"name": "Drug salt", "source_ids": []}]
            with self.assertRaisesRegex(core.ProgramError, "must be a non-empty list"):
                candidate_rules._validate_review_item(records, "NODE:A", {})
            records["reviews"][0]["aliases"] = []
            records["reviews"][0]["why_not"] = [{
                "finding": "Unsupported concern",
                "source_ids": ["PMID:404"],
            }]
            with self.assertRaisesRegex(core.ProgramError, "unknown IDs"):
                candidate_rules._validate_review_item(records, "NODE:A", {})
            records["reviews"][0]["why_not"] = []
            records["reviews"][0]["counterevidence"] = []
            with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
                candidate_rules._validate_review_item(records, "NODE:A", {})
            del records["reviews"][0]["counterevidence"]
            records["documents"] = []
            with self.assertRaisesRegex(core.ProgramError, "retained by this review"):
                candidate_rules._validate_review_item(
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
                candidate_rules._validate_review_item(records, "NODE:A", {})

    def test_audit_validation_is_review_independent_and_preserves_longshots(self):
        second_review = self.review("DRUG-B")
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
            values={component: 5 for component in contracts.SCORE_COMPONENTS},
        )
        records = {
            "assessments": [longshot],
            "evidence_dispositions": [],
            "excluded_candidates": [{
                "candidate_id": "DRUG-B",
                "reason_code": "unsupported_action",
                "finding": "The audit found no credible support for the proposed drug action.",
                "source_ids": ["PMID:1"],
                "source_integrity": self.exclusion_integrity(["PMID:1"]),
            }],
        }

        audit._validate_candidate_audit(records, results)
        self.assertEqual(ranking._final_score(longshot), 20)

        legacy_reason = json.loads(json.dumps(records))
        legacy_reason["excluded_candidates"][0]["reason_code"] = "human_intervention"
        with self.assertRaisesRegex(core.ProgramError, "reason_code must be one of"):
            audit._validate_candidate_audit(legacy_reason, results)

        all_excluded = {
            "assessments": [],
            "evidence_dispositions": [],
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
        audit._validate_candidate_audit(all_excluded, results)
        self.assertEqual(
            run_state._stop_reason({"candidate_audit": {"records": all_excluded}}),
            "the audit excluded every reviewed candidate",
        )

        arbitrary = json.loads(json.dumps(records))
        arbitrary["assessments"][0]["component_scores"]["drug_action_confidence"]["value"] = 12
        audit._validate_candidate_audit(arbitrary, results)
        arbitrary["assessments"][0]["component_scores"]["drug_action_confidence"]["value"] = 21
        with self.assertRaisesRegex(core.ProgramError, "integer from 1 through 20"):
            audit._validate_candidate_audit(arbitrary, results)

        invalid = json.loads(json.dumps(records))
        invalid["excluded_candidates"] = []
        with self.assertRaisesRegex(core.ProgramError, "partition every reviewed candidate"):
            audit._validate_candidate_audit(invalid, results)

        independently_assessed = json.loads(json.dumps(records))
        independently_assessed["assessments"].append(self.assessment("DRUG-B"))
        independently_assessed["excluded_candidates"] = []
        audit._validate_candidate_audit(independently_assessed, results)

    def test_exact_disease_novelty_exclusion_dry_run(self):
        documents = [
            {"document_id": "PMID:1", "title": "Candidate action", "source": "test",
             "evidence_passages": [{"text": "Action evidence.", "locator": "abstract"}]},
            {"document_id": "PMID:2", "title": "Ketamine trial in FHM1", "source": "test",
             "evidence_passages": [{"text": "Ketamine entered a registered therapeutic study in genetically confirmed FHM1.", "locator": "trial record"}]},
            {"document_id": "PMID:3", "title": "Novel agent in a related disease", "source": "test",
             "evidence_passages": [{"text": "Novel agent was studied only in a related disease.", "locator": "abstract"}]},
        ]
        ketamine_review = self.review("DRUG-KET", "PMID:1")
        ketamine_review["prior_art"] = {
            "status": "human_intervention",
            "summary": "A registered exact-disease therapeutic study was identified.",
            "findings": [{
                "finding": "Ketamine entered a registered therapeutic study in FHM1.",
                "source_ids": ["PMID:2"],
            }],
        }
        results = {
            "evidence_graph": {"records": {}},
            "candidate_identity": {"records": {}},
            "candidate_seed_generation": {"records": {"rescue_strategies": []}},
            "candidate_review": {"records": {
                "documents": documents,
                "reviews": [ketamine_review,
                            self.review("DRUG-OTHER", "PMID:3")],
            }},
        }
        candidates = [
            {"candidate_id": "DRUG-KET", "name": "Ketamine"},
            {"candidate_id": "DRUG-OTHER", "name": "Novel agent"},
        ]
        with patch.object(packets, "_canonical_candidates", return_value=candidates):
            context = packets._packet_context(self.root, "candidate_audit", None, results)
        ketamine_sources = next(
            row["source_ids"] for row in context["candidate_evidence_index"]
            if row["candidate_id"] == "DRUG-KET"
        )
        self.assertIn("PMID:2", ketamine_sources)
        dispositions = [
            {"candidate_id": row["candidate_id"], "source_id": source_id,
             "disposition": (
                 "exact_disease_prior_use_or_testing"
                 if row["candidate_id"] == "DRUG-KET" and source_id == "PMID:2"
                 else "relevant_not_tested"
                 if row["candidate_id"] == "DRUG-OTHER" and source_id == "PMID:3"
                 else "irrelevant"
             ), "reason": "Classified against the exact-disease novelty gate."}
            for row in context["candidate_evidence_index"] for source_id in row["source_ids"]
        ]
        records = {
            "assessments": [self.assessment("DRUG-OTHER", "PMID:3")],
            "evidence_dispositions": dispositions,
            "excluded_candidates": [
                {"candidate_id": "DRUG-KET", "reason_code": "unsupported_action",
                 "finding": "Wrong bounded disposition.", "source_ids": ["PMID:1"],
                 "source_integrity": self.exclusion_integrity(["PMID:1"])},
            ],
        }
        with self.assertRaisesRegex(core.ProgramError, "must be excluded"):
            audit._validate_candidate_audit(
                records, results, context["source_index"], context["candidate_evidence_index"]
            )
        ketamine = records["excluded_candidates"][0]
        ketamine.update({
            "reason_code": "exact_disease_prior_use_or_testing",
            "finding": "Ketamine has already entered a registered FHM1 therapeutic study.",
            "source_ids": ["PMID:2"],
            "source_integrity": self.exclusion_integrity(["PMID:2"]),
        })
        audit._validate_candidate_audit(
            records, results, context["source_index"], context["candidate_evidence_index"]
        )

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
            "DRUG-A", values={component: 5 for component in contracts.SCORE_COMPONENTS}
        )
        baseline_score = ranking._final_score(assessment)
        assessment["why_not"] = [{
            "finding": "Independent disease models found no efficacy.",
            "source_ids": ["PMID:1"],
        }]
        assessment["source_integrity"] = self.source_integrity(assessment)
        audit._validate_candidate_audit(
            {"assessments": [assessment], "excluded_candidates": [],
             "evidence_dispositions": []}, results
        )
        self.assertEqual(ranking._final_score(assessment), baseline_score)

        invalid = json.loads(json.dumps(assessment))
        invalid["component_scores"]["evidence_robustness"] = {
            "value": 20,
            "reason": "Consistent negative findings form a strong evidence base.",
            "source_ids": ["PMID:1"],
        }
        with self.assertRaisesRegex(core.ProgramError, "unexpected fields"):
            audit._validate_candidate_audit(
                {"assessments": [invalid], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

    def test_component_scores_require_minimal_verdict_coherence(self):
        review = self.review("DRUG-A")
        review["supporting_findings"].append({
            "finding": "A second retained source informs the drug-action component.",
            "source_ids": ["PMID:2"],
        })
        results = {
            "candidate_review": {"records": {
                "documents": [
                    {
                        "document_id": source_id,
                        "title": "Retained evidence",
                        "source": "test",
                        "evidence_passages": [{"text": "Evidence", "locator": "results"}],
                    }
                    for source_id in ("PMID:1", "PMID:2")
                ],
                "reviews": [review],
            }}
        }
        component = "drug_action_confidence"
        assessment = self.assessment("DRUG-A")
        assessment["component_scores"][component]["source_ids"].append("PMID:2")
        assessment["source_integrity"] = self.source_integrity(assessment)
        next(
            check for check in assessment["source_integrity"]["checks"]
            if check["scope"] == component and check["source_id"] == "PMID:2"
        )["verdict"] = "contradicts"

        audit._validate_candidate_audit(
            {"assessments": [assessment], "excluded_candidates": [],
             "evidence_dispositions": []}, results
        )
        assessment["component_scores"][component]["value"] = 20
        with self.assertRaisesRegex(core.ProgramError, "20-point component"):
            audit._validate_candidate_audit(
                {"assessments": [assessment], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        unsupported = self.assessment("DRUG-A")
        next(
            check for check in unsupported["source_integrity"]["checks"]
            if check["scope"] == component
        )["verdict"] = "does_not_support"
        with self.assertRaisesRegex(core.ProgramError, "supports or partly_supports"):
            audit._validate_candidate_audit(
                {"assessments": [unsupported], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
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
        audit._validate_candidate_audit(
            {"assessments": [assessment], "excluded_candidates": [],
             "evidence_dispositions": []}, results
        )
        scopes = {
            check["scope"] for check in assessment["source_integrity"]["checks"]
        }
        self.assertIn("aliases[0]", scopes)
        self.assertIn("aliases[1]", scopes)

        not_object = json.loads(json.dumps(assessment))
        not_object["source_integrity"] = []
        with self.assertRaisesRegex(core.ProgramError, "source_integrity must be an object"):
            audit._validate_candidate_audit(
                {"assessments": [not_object], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        prefixed = json.loads(json.dumps(assessment))
        for check in prefixed["source_integrity"]["checks"]:
            if check["scope"] in contracts.SCORE_COMPONENTS:
                check["scope"] = f"component_scores.{check['scope']}"
        with self.assertRaisesRegex(core.ProgramError, "cover every cited source use"):
            audit._validate_candidate_audit(
                {"assessments": [prefixed], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        generic = json.loads(json.dumps(assessment))
        generic["source_integrity"] = {
            "status": "supported",
            "finding": "Looks sound.",
            "source_ids": ["PMID:1"],
        }
        with self.assertRaisesRegex(core.ProgramError, "missing fields: checks"):
            audit._validate_candidate_audit(
                {"assessments": [generic], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        missing = json.loads(json.dumps(assessment))
        missing["source_integrity"]["checks"].pop()
        with self.assertRaisesRegex(core.ProgramError, "cover every cited source use"):
            audit._validate_candidate_audit(
                {"assessments": [missing], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        deferred = json.loads(json.dumps(assessment))
        deferred["source_integrity"]["checks"][0]["finding"] = (
            "This source needs independent verification."
        )
        with self.assertRaisesRegex(core.ProgramError, "not defer verification"):
            audit._validate_candidate_audit(
                {"assessments": [deferred], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        deferred["source_integrity"]["checks"][0]["finding"] = (
            "This citation is unverifiable from the packet."
        )
        with self.assertRaisesRegex(core.ProgramError, "not defer verification"):
            audit._validate_candidate_audit(
                {"assessments": [deferred], "excluded_candidates": [],
                 "evidence_dispositions": []}, results
            )

        no_content = json.loads(json.dumps(results))
        del no_content["candidate_review"]["records"]["documents"][0]["evidence_passages"]
        with self.assertRaisesRegex(core.ProgramError, "no inspectable content"):
            audit._validate_candidate_audit(
                {"assessments": [assessment], "excluded_candidates": [],
                 "evidence_dispositions": []}, no_content
            )

        null_content = json.loads(json.dumps(results))
        null_content["candidate_review"]["records"]["documents"][0][
            "evidence_passages"
        ] = [{"text": None, "locator": None}]
        with self.assertRaisesRegex(core.ProgramError, "no inspectable content"):
            audit._validate_candidate_audit(
                {"assessments": [assessment], "excluded_candidates": [],
                 "evidence_dispositions": []}, null_content
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
            audit._validate_candidate_audit(
                {"assessments": [duplicate_publication], "excluded_candidates": [],
                 "evidence_dispositions": []},
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
        high = {component: 20 for component in contracts.SCORE_COMPONENTS}
        medium = dict(zip(contracts.SCORE_COMPONENTS, (12, 17, 9, 14)))
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
        with patch.object(ranking, "_canonical_candidates", return_value=candidates):
            rows, _ = ranking._ranked_rows(results)

        self.assertEqual(
            [(row["candidate_id"], row["rank"], row["final_score"]) for row in rows],
            [("DRUG-A", 1, 80), ("DRUG-B", 1, 80), ("DRUG-C", 2, 52)],
        )

    def test_card_renderer_contains_only_name_mechanism_and_reasons_why_not(self):
        payload = evidence_cards._cards_bytes([{
            "name": "Candidate drug",
            "how_it_could_work": {
                "text": "Target inhibition could reduce the disease-associated signalling excess.",
                "source_ids": ["PMID:2", "PMID:1"],
            },
            "reasons_why_not": [{
                "text": "Relevant tissue exposure has not been established.",
                "source_ids": ["PMID:3"],
            }],
        }]).decode("utf-8")

        self.assertEqual(
            payload,
            "## Candidate drug\n\n"
            "### How it could work\n\n"
            "Target inhibition could reduce the disease-associated signalling excess. "
            "[PMID:1; PMID:2]\n\n"
            "### Reasons why not\n\n"
            "Relevant tissue exposure has not been established. [PMID:3]\n",
        )

    def test_card_prose_rejects_fragments_metadata_labels_and_ellipses(self):
        self.assertEqual(
            audit._validate_card_prose(
                "Target inhibition could reduce pathological signalling.", "card"
            ),
            "Target inhibition could reduce pathological signalling.",
        )
        invalid = {
            "Target inhibition": "complete sentence prose",
            "Mechanism: target inhibition.": "metadata-labelled fragment",
            "Target inhibition could reduce...": "ellipses",
        }
        for prose, error in invalid.items():
            with self.subTest(prose=prose):
                with self.assertRaisesRegex(core.ProgramError, error):
                    audit._validate_card_prose(prose, "card")

    def test_assertions_merge_by_triple_with_context_and_controller_id(self):
        assertion = {
            "subject_id": "NODE:1",
            "relation": "contributes_to",
            "object_id": "NODE:2",
            "evidence_context": [{
                "source_ids": ["PMID:1"],
                "evidence_type": "human",
                "model": "affected individuals",
                "stage": "established disease",
                "polarity": "supports",
                "summary": "first finding",
            }],
        }
        merged = graph_rules._merge_assertions([
            assertion,
            {
                **assertion,
                "evidence_context": [{
                    **assertion["evidence_context"][0],
                    "source_ids": ["PMID:2"],
                }],
            },
            {
                **assertion,
                "evidence_context": [{
                    "source_ids": ["PMID:3"],
                    "evidence_type": "animal",
                    "model": "disease model",
                    "stage": "presymptomatic",
                    "polarity": "contradicts",
                    "summary": "opposite model finding",
                }],
            },
        ])

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0]["assertion_id"],
            storage._stable_id("ASSERTION", {
                "subject_id": "NODE:1",
                "relation": "contributes_to",
                "object_id": "NODE:2",
            }),
        )
        self.assertNotIn("source_ids", merged[0])
        self.assertNotIn("evidence_summary", merged[0])
        contexts = merged[0]["evidence_context"]
        self.assertEqual(len(contexts), 2)
        self.assertEqual(
            next(row for row in contexts if row["polarity"] == "supports")["source_ids"],
            ["PMID:1", "PMID:2"],
        )
        self.assertEqual(
            {row["polarity"] for row in contexts}, {"supports", "contradicts"}
        )
        distinct = graph_rules._merge_assertions(
            [assertion, {**assertion, "object_id": "NODE:3"}]
        )
        self.assertEqual(len(distinct), 2)

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
                    "established_pathology_observations": [],
                    "causal_role": "causal",
                    "mechanisms": ["mechanism"],
                    "distinct_mechanisms": [],
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
            "strategy_ids": [f"STRATEGY:{concept_id}"],
            "graph_node_ids": [node_id],
            "assertion_ids": [],
            "graph_rationale": f"The focal profile for {node_id} is sufficient.",
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
            "strategy_ids": [f"STRATEGY:{concept_id}"],
            "graph_node_ids": [concept_id],
            "assertion_ids": [],
            "graph_rationale": f"The focal profile for {concept_id} is sufficient.",
            "pathology_source_ids": [f"PATH:{concept_id}"],
            "mechanism_source_ids": [f"MOA:{concept_id}"],
            "origin_concept_ids": [concept_id],
            "seed_id": seed_id,
        }
        if resolution is not None:
            row["identity_resolution"] = resolution
        return row

    @staticmethod
    def assertion(
        subject_id, object_id, *, relation="contributes_to", source_id="PMID:1"
    ):
        return {
            "subject_id": subject_id,
            "relation": relation,
            "object_id": object_id,
            "evidence_context": [{
                "source_ids": [source_id],
                "evidence_type": "human",
                "model": "affected individuals",
                "stage": "established disease",
                "polarity": "supports",
                "summary": "The source supports this disease-pathology relationship.",
            }],
        }

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
                "summary": "No exact-disease experimentation was found in the bounded search.",
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
                for component in contracts.SCORE_COMPONENTS
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
                for source_id, scope in sorted(audit._assessment_source_uses(assessment))
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
