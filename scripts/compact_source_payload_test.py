#!/usr/bin/env python3
"""Biological-content preservation tests for compact_source_payload.py."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from compact_source_payload import PayloadValidationError, compact_payload, main


class CompactSourcePayloadTests(unittest.TestCase):
    def test_inline_markup_preserves_gene_allele_and_species_exactly(self) -> None:
        text = (
            "<p>The <b>UNC80</b> complex is regulated by "
            "<italic>unc-80(e1069)</italic> in <i>C. elegans</i>.</p>"
        )
        result = compact_payload(
            {"records": [{"pmid": "123", "title": "Tagged biology", "abstract": text}]},
            "discovery",
        )
        self.assertEqual(result["records"][0]["abstract"], text)
        for term in ("UNC80", "unc-80(e1069)", "C. elegans"):
            self.assertIn(term, result["records"][0]["abstract"])

    def test_normalized_structured_abstract_and_symbols_survive_exactly(self) -> None:
        text = (
            '<AbstractText Label="RESULTS"><italic>daf-2(e1370)</italic> altered '
            "Ca2+, α-synuclein, β-oxidation, and &alpha;-synuclein.</AbstractText>"
        )
        result = compact_payload(
            {"articles": [{"doi": "10.1/example", "summary": text, "mesh": ["Genes", "Phenotype"]}]},
            "discovery",
        )
        record = result["records"][0]
        self.assertEqual(record["abstract"], text)
        self.assertEqual(record["mesh_terms"], ["Genes", "Phenotype"])
        for term in ("daf-2(e1370)", "Ca2+", "α-synuclein", "β-oxidation", "&alpha;"):
            self.assertIn(term, record["abstract"])

    def test_unicode_round_trip_through_cli_output(self) -> None:
        text = "unc-80(e1069); α-synuclein; Ca²⁺; ΔFosB; 10 µM"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.json"
            output = root / "output.json"
            source.write_text(
                json.dumps({"records": [{"pmid": "1", "abstract": text}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(
                sys, "argv", ["compact_source_payload.py", str(source), str(output), "--query-id", "Q_TEST"]
            ):
                self.assertEqual(main(), 0)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["records"][0]["abstract"], text)

    def test_verification_excerpt_is_preserved_exactly(self) -> None:
        excerpt = "Figure 3: unc-80(e1069) animals showed partial rescue at 10 micromolar."
        result = compact_payload(
            {
                "records": [
                    {
                        "pmid": "2",
                        "targeted_excerpt": excerpt,
                        "verification_pointer": "Figure 3",
                        "verification_scope": "dose and rescue direction",
                        "support_direction": "supports",
                    }
                ]
            },
            "verification",
        )
        self.assertEqual(result["records"][0]["targeted_excerpt"], excerpt)

    def test_bulky_metadata_is_removed_without_touching_text(self) -> None:
        text = "The UNC80-NALCN complex includes UNC79."
        result = compact_payload(
            {
                "records": [
                    {
                        "pmid": "3",
                        "abstract": text,
                        "author_affiliations": ["large metadata"],
                        "complete_reference_list": ["large references"],
                        "raw_xml": "<article>large payload</article>",
                    }
                ]
            },
            "discovery",
        )
        record = result["records"][0]
        self.assertEqual(record["abstract"], text)
        self.assertNotIn("author_affiliations", record)
        self.assertNotIn("complete_reference_list", record)
        self.assertNotIn("raw_xml", record)

    def test_nested_abstract_requires_source_specific_parser(self) -> None:
        with self.assertRaisesRegex(PayloadValidationError, "parse and normalize"):
            compact_payload(
                {
                    "records": [
                        {
                            "pmid": "4",
                            "abstract": {"section": [{"italic": "unc-80(e1069)"}]},
                        }
                    ]
                },
                "discovery",
            )

    def test_raw_xml_string_fails_loudly(self) -> None:
        with self.assertRaisesRegex(PayloadValidationError, "raw HTML/XML/text"):
            compact_payload("<article><italic>unc-80(e1069)</italic></article>", "discovery")

    def test_cli_rejects_raw_xml_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.xml"
            output = root / "output.json"
            source.write_text("<article><italic>unc-80(e1069)</italic></article>", encoding="utf-8")
            with patch.object(
                sys, "argv", ["compact_source_payload.py", str(source), str(output), "--query-id", "Q_TEST"]
            ):
                self.assertEqual(main(), 1)
            self.assertFalse(output.exists())

    def test_unknown_nested_wrapper_fails_loudly(self) -> None:
        with self.assertRaisesRegex(PayloadValidationError, "no recognized normalized source fields"):
            compact_payload({"PubmedArticleSet": {"PubmedArticle": []}}, "discovery")

    def test_mixed_record_list_does_not_silently_drop_entries(self) -> None:
        with self.assertRaisesRegex(PayloadValidationError, "non-object entries"):
            compact_payload([{"pmid": "5", "title": "Valid"}, "raw text"], "discovery")

    def test_empty_normalized_result_set_is_valid(self) -> None:
        self.assertEqual(compact_payload({"records": []}, "discovery")["result_count"], 0)

    def test_record_hash_is_stable_and_content_bound(self) -> None:
        payload = {"records": [{"pmid": "6", "title": "Stable biological record"}]}
        first = compact_payload(payload, "discovery")
        second = compact_payload(payload, "discovery")
        changed = compact_payload(
            {"records": [{"pmid": "6", "title": "Changed biological record"}]},
            "discovery",
        )
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["compactor"], "compact_source_payload.py")
        self.assertEqual(
            first["records"][0]["compact_record_hash"],
            second["records"][0]["compact_record_hash"],
        )
        self.assertNotEqual(
            first["records"][0]["compact_record_hash"],
            changed["records"][0]["compact_record_hash"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
