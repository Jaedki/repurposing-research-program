#!/usr/bin/env python3
"""Focused tests for receipt construction and source-to-disk adapters."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_search_record import build_search_record
from compact_source_payload import compact_payload
from fetch_source_payload import normalize_pubmed_summary, normalize_uniprot


class SafetyRegressionTests(unittest.TestCase):
    def test_search_builder_binds_counts_pages_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_sources"
            raw.mkdir()
            for index in (1, 2):
                receipt = compact_payload(
                    [{"canonical_identifier": f"PMID:{index}", "identifier_type": "PMID", "title": f"Paper {index}"}],
                    "discovery",
                    "Q1",
                )
                (raw / f"page{index}.json").write_text(json.dumps(receipt), encoding="utf-8")
            row = build_search_record(
                root,
                query_id="Q1",
                research_unit_id="U1",
                subtopic_id="",
                query_family="primary_retrieval",
                resource="PubMed",
                query="gene phenotype",
                receipt_paths=["raw_sources/page1.json", "raw_sources/page2.json"],
                continuation_tokens=["next-page-token"],
                acquired_source_ids=[],
                original_verified_source_ids=[],
                retained_source_ids=[],
                executed_by_agent_id="agent-1",
                executor_role="worker",
                origin_job_id="U1.worker",
                closure_note="All pages and continuations were screened.",
            )
            self.assertEqual((row["result_count"], row["deduplicated_count"], row["page_count"]), (2, 2, 2))
            self.assertEqual(row["pagination_trace"][0]["output_token_hash"], row["pagination_trace"][1]["input_token_hash"])

    def test_search_builder_rejects_cross_query_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw_sources"
            raw.mkdir()
            receipt = compact_payload([{"pmid": "1", "title": "Paper"}], "discovery", "Q_OTHER")
            (raw / "page.json").write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not bound to Q1"):
                build_search_record(
                    root,
                    query_id="Q1",
                    research_unit_id="U1",
                    subtopic_id="",
                    query_family="primary_retrieval",
                    resource="PubMed",
                    query="gene",
                    receipt_paths=["raw_sources/page.json"],
                    continuation_tokens=[],
                    acquired_source_ids=[],
                    original_verified_source_ids=[],
                    retained_source_ids=[],
                    executed_by_agent_id="agent-1",
                    executor_role="worker",
                    origin_job_id="U1.worker",
                    closure_note="All pages were screened.",
                )

    def test_source_normalizers_emit_compactor_ready_records(self) -> None:
        pubmed = normalize_pubmed_summary(
            {"result": {"uids": ["123"], "123": {"title": "A report.", "pubdate": "2026 Jul"}}}, "QP"
        )
        uniprot = normalize_uniprot(
            {
                "results": [
                    {
                        "primaryAccession": "P12345",
                        "proteinDescription": {"recommendedName": {"fullName": {"value": "Example protein"}}},
                    }
                ]
            },
            "QU",
        )
        self.assertEqual(pubmed[0]["query_id"], "QP")
        self.assertEqual(pubmed[0]["year"], 2026)
        self.assertEqual(uniprot[0]["canonical_identifier"], "P12345")
        self.assertEqual(uniprot[0]["query_id"], "QU")


if __name__ == "__main__":
    unittest.main(verbosity=2)
