#!/usr/bin/env python3
"""Source compaction, query binding, and fetch-normalizer regression tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_search_record import build_search_record
from compact_source_payload import PayloadValidationError, compact_payload, main
from fetch_source_payload import normalize_pubmed_summary, normalize_uniprot


class SourcePipelineTests(unittest.TestCase):
    def test_biological_text_and_unicode_are_preserved(self) -> None:
        text = "<p><b>SCN2A</b> p.Arg1882Gln in <i>Homo sapiens</i>; alpha-synuclein; 10 micromolar.</p>"
        result = compact_payload(
            {"records": [{"pmid": "123", "title": "Human variant", "abstract": text}]},
            "discovery",
        )
        self.assertEqual(result["records"][0]["abstract"], text)
        for term in ("SCN2A", "p.Arg1882Gln", "Homo sapiens"):
            self.assertIn(term, result["records"][0]["abstract"])

    def test_verification_excerpt_is_preserved(self) -> None:
        excerpt = "Patient-derived neurons showed partial rescue at 10 micromolar."
        result = compact_payload(
            {"records": [{"pmid": "2", "targeted_excerpt": excerpt, "verification_pointer": "Figure 3",
                           "verification_scope": "dose and direction", "support_direction": "supports"}]},
            "verification",
        )
        self.assertEqual(result["records"][0]["targeted_excerpt"], excerpt)

    def test_bulky_metadata_is_removed(self) -> None:
        result = compact_payload(
            {"records": [{"pmid": "3", "abstract": "Human NALCN complex biology.",
                           "author_affiliations": ["large"], "raw_xml": "<article>large</article>"}]},
            "discovery",
        )
        self.assertNotIn("author_affiliations", result["records"][0])
        self.assertNotIn("raw_xml", result["records"][0])

    def test_nested_or_raw_payload_fails_loudly(self) -> None:
        with self.assertRaisesRegex(PayloadValidationError, "parse and normalize"):
            compact_payload({"records": [{"pmid": "4", "abstract": {"section": "nested"}}]}, "discovery")
        with self.assertRaisesRegex(PayloadValidationError, "raw HTML/XML/text"):
            compact_payload("<article>raw</article>", "discovery")
        with self.assertRaisesRegex(PayloadValidationError, "non-object entries"):
            compact_payload([{"pmid": "5"}, "raw"], "discovery")

    def test_cli_rejects_raw_xml_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.xml"
            output = root / "output.json"
            source.write_text("<article>raw</article>", encoding="utf-8")
            with patch.object(sys, "argv", ["compact_source_payload.py", str(source), str(output), "--query-id", "Q"]):
                self.assertEqual(main(), 1)
            self.assertFalse(output.exists())

    def test_hash_is_stable_and_query_bound(self) -> None:
        payload = [{"pmid": "6", "title": "Stable human record"}]
        first = compact_payload(payload, "discovery", "Q1")
        second = compact_payload(payload, "discovery", "Q1")
        changed = compact_payload([{"pmid": "6", "title": "Changed record"}], "discovery", "Q1")
        self.assertEqual(first["records"][0]["compact_record_hash"], second["records"][0]["compact_record_hash"])
        self.assertNotEqual(first["records"][0]["compact_record_hash"], changed["records"][0]["compact_record_hash"])
        self.assertEqual(first["records"][0]["query_id"], "Q1")

    def test_search_builder_binds_pages_and_query(self) -> None:
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
                query_family="primary_literature",
                resource="PubMed",
                query="human disease therapy",
                receipt_paths=["raw_sources/page1.json", "raw_sources/page2.json"],
                continuation_tokens=["next"],
                acquired_source_ids=[],
                verified_source_ids=[],
                retained_source_ids=[],
                executed_by_agent_id="agent-1",
                origin_job_id="U1.research",
                closure_note="All pages were screened.",
            )
            self.assertEqual((row["result_count"], row["screened_count"]), (2, 2))
            self.assertEqual(row["pagination_trace"][0]["output_token_hash"], row["pagination_trace"][1]["input_token_hash"])

    def test_search_builder_rejects_cross_query_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw_sources").mkdir()
            receipt = compact_payload([{"pmid": "1", "title": "Paper"}], "discovery", "OTHER")
            (root / "raw_sources" / "page.json").write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not bound to Q1"):
                build_search_record(
                    root,
                    query_id="Q1",
                    research_unit_id="U1",
                    query_family="primary_literature",
                    resource="PubMed",
                    query="human gene",
                    receipt_paths=["raw_sources/page.json"],
                    continuation_tokens=[],
                    acquired_source_ids=[],
                    verified_source_ids=[],
                    retained_source_ids=[],
                    executed_by_agent_id="agent-1",
                    origin_job_id="U1.research",
                    closure_note="All pages were screened.",
                )

    def test_source_normalizers_are_compactor_ready(self) -> None:
        pubmed = normalize_pubmed_summary(
            {"result": {"uids": ["123"], "123": {"title": "A report.", "pubdate": "2026 Jul"}}}, "QP"
        )
        uniprot = normalize_uniprot(
            {"results": [{"primaryAccession": "P12345", "proteinDescription": {
                "recommendedName": {"fullName": {"value": "Human protein"}}
            }}]},
            "QU",
        )
        self.assertEqual(pubmed[0]["year"], 2026)
        self.assertEqual(uniprot[0]["query_id"], "QU")


if __name__ == "__main__":
    unittest.main(verbosity=2)
