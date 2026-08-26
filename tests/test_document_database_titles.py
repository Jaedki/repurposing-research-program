import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from repurposing_program import bibliography, evidence, hypotheses  # noqa: E402
from repurposing_program.errors import ProgramError  # noqa: E402


class PublicationIdentityTests(unittest.TestCase):
    def test_exact_document_ids_merge_without_title_identity_logic(self):
        documents = evidence._merge_documents([
            {
                "document_id": "PMID:35445439",
                "title": "Authoritative article title",
                "evidence_passages": [{"text": "First", "locator": "p1"}],
            },
            {
                "document_id": "PMID:35445439",
                "title": "Treatment-blind projection",
                "evidence_passages": [{"text": "Second", "locator": "p2"}],
            },
        ])

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["title"], "Authoritative article title")
        self.assertEqual(len(documents[0]["evidence_passages"]), 2)

    def test_publication_ids_normalize_once_and_merge_all_evidence(self):
        records = {
            "documents": [
                {
                    "document_id": "PMID:123",
                    "pmid": "123",
                    "title": "Paper",
                    "evidence_passages": [{"text": "First", "locator": "p1"}],
                    "tags": ["source"],
                    "source_ids": ["PMID:123"],
                },
                {
                    "document_id": "DOI:10.1000/example",
                    "doi": "10.1000/example",
                    "pmcid": "PMC123",
                    "title": "Paper",
                    "evidence_passages": [{"text": "Second", "locator": "p2"}],
                    "tags": ["coverage"],
                },
            ],
            "claims": [{"source_ids": ["PMID:123"]}],
            "profiles": [{"pathology_source_ids": ["PMID:123"]}],
            "candidates": [{"mechanism_source_ids": ["PMID:123"]}],
        }
        metadata = {
            document_id: {
                "title": "Paper",
                "canonical_id": "DOI:10.1000/example",
                "identifiers": [document_id, "DOI:10.1000/example"],
            }
            for document_id in ("PMID:123", "DOI:10.1000/example")
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=True
            )
            repeated = bibliography._normalize_result_documents(
                Path(directory), normalized, verify_titles=True
            )

        self.assertEqual(normalized, repeated)
        self.assertEqual(records["documents"][0]["document_id"], "PMID:123")
        self.assertEqual(len(normalized["documents"]), 1)
        self.assertFalse(
            {"doi", "pmid", "pmcid"} & set(normalized["documents"][0])
        )
        self.assertEqual(
            normalized["documents"][0]["document_id"], "DOI:10.1000/example"
        )
        self.assertEqual(len(normalized["documents"][0]["evidence_passages"]), 2)
        self.assertEqual(normalized["documents"][0]["tags"], ["coverage", "source"])
        self.assertEqual(
            normalized["documents"][0]["source_ids"], ["DOI:10.1000/example"]
        )
        for collection, field in (
            ("claims", "source_ids"),
            ("profiles", "pathology_source_ids"),
            ("candidates", "mechanism_source_ids"),
        ):
            self.assertEqual(
                normalized[collection][0][field], ["DOI:10.1000/example"]
            )

    def test_disposition_crosswalk_uses_the_same_citation_rewrite(self):
        records = {
            "documents": [{"document_id": "PMCID:PMC123", "title": "Paper"}],
            "receipts": [{
                "paper_dispositions": [
                    {"source_ids": ["PMCID:PMC123"], "disposition": "retained"},
                    {"source_ids": [], "disposition": "not_retained"},
                ]
            }],
        }
        metadata = {"PMCID:PMC123": {
            "title": "Paper",
            "canonical_id": "DOI:10.1000/example",
            "identifiers": ["PMCID:PMC123", "DOI:10.1000/example"],
        }}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=True
            )

        dispositions = normalized["receipts"][0]["paper_dispositions"]
        self.assertEqual(dispositions[0]["source_ids"], ["DOI:10.1000/example"])
        self.assertEqual(dispositions[1]["source_ids"], [])

    def test_xald_and_unc80_publication_groups_each_collapse_to_one_document(self):
        groups = (
            (
                "DOI:10.1016/j.ebiom.2023.104781",
                "PMID:37683329",
                "PMCID:PMC10497986",
            ),
            (
                "DOI:10.26502/jbb.2642-91280091",
                "PMID:38077449",
                "PMCID:PMC10705002",
            ),
            (
                "DOI:10.1016/j.ajhg.2015.11.004",
                "PMID:26708751",
                "PMCID:PMC4716670",
            ),
            (
                "DOI:10.1038/s41467-020-17105-8",
                "PMID:32620897",
                "PMCID:PMC7335163",
            ),
        )
        documents = [
            {"document_id": document_id, "title": f"Paper {index}"}
            for index, group in enumerate(groups)
            for document_id in group
        ]
        metadata = {
            document_id: {
                "title": f"Paper {index}",
                "canonical_id": group[0],
                "identifiers": list(group),
            }
            for index, group in enumerate(groups)
            for document_id in group
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), {"documents": documents}, verify_titles=True
            )

        self.assertEqual(
            [row["document_id"] for row in normalized["documents"]],
            sorted(group[0] for group in groups),
        )

    def test_source_pmid_and_node_pmcid_become_existing_evidence(self):
        metadata = {
            document_id: {
                "title": "One paper",
                "canonical_id": "DOI:10.1000/existing",
                "identifiers": [
                    "PMID:123", "PMCID:PMC123", "DOI:10.1000/existing"
                ],
            }
            for document_id in ("PMID:123", "PMCID:PMC123")
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            source = bibliography._normalize_result_documents(
                Path(directory),
                {"documents": [{"document_id": "PMID:123", "title": "One paper"}]},
                verify_titles=True,
            )
            node = bibliography._normalize_result_documents(
                Path(directory),
                {"documents": [{"document_id": "PMCID:PMC123", "title": "One paper"}]},
                verify_titles=True,
            )

        self.assertEqual(
            hypotheses._reused_graph_publications(
                node["documents"], source["documents"]
            ),
            {"DOI:10.1000/existing"},
        )

    def test_citation_only_publication_id_is_normalized(self):
        records = {
            "documents": [{"document_id": "DOI:10.1000/existing", "title": "Paper"}],
            "profiles": [{"source_ids": ["PMCID:PMC123"]}],
        }
        metadata = {
            value: {
                "title": "Paper",
                "canonical_id": "DOI:10.1000/existing",
                "identifiers": ["PMCID:PMC123", "DOI:10.1000/existing"],
            }
            for value in ("PMCID:PMC123", "DOI:10.1000/existing")
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=True
            )

        self.assertEqual(
            normalized["profiles"][0]["source_ids"], ["DOI:10.1000/existing"]
        )

    def test_titles_are_verified_without_being_replaced(self):
        records = {"documents": [{
            "document_id": "PMID:123",
            "title": "Multi-center phase II study",
        }]}
        metadata = {"PMID:123": {
            "title": "Multicenter phase 2 study",
            "canonical_id": "PMID:123",
            "identifiers": ["PMID:123"],
        }}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=True
            )
        self.assertEqual(
            normalized["documents"][0]["title"], records["documents"][0]["title"]
        )

        metadata["PMID:123"]["title"] = "Different publication"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ), self.assertRaisesRegex(ProgramError, "metadata mismatch"):
            bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=True
            )

    def test_controller_titles_and_nonpublication_ids_remain_unchanged(self):
        records = {"documents": [
            {"document_id": "PMID:123", "title": "Treatment-blind pathology"},
            {"document_id": "S2:" + "A" * 40, "title": "Opaque paper"},
        ]}
        metadata = {"PMID:123": {
            "title": "Treatment-focused source title",
            "canonical_id": "DOI:10.1000/example",
            "identifiers": ["PMID:123", "DOI:10.1000/example"],
        }}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            normalized = bibliography._normalize_result_documents(
                Path(directory), records, verify_titles=False
            )

        self.assertEqual(
            {row["document_id"]: row["title"] for row in normalized["documents"]},
            {
                "DOI:10.1000/example": "Treatment-blind pathology",
                "S2:" + "A" * 40: "Opaque paper",
            },
        )


if __name__ == "__main__":
    unittest.main()
