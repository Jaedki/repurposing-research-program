import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from repurposing_program import bibliography, evidence  # noqa: E402
from repurposing_program.errors import ProgramError  # noqa: E402


class DatabaseDocumentTitleTests(unittest.TestCase):
    def test_pubchem_locator_suffix_does_not_create_a_title_conflict(self):
        documents = evidence._merge_documents(
            [
                {
                    "document_id": "PUBCHEM:163659",
                    "title": "Mithramycin",
                    "evidence_passages": [
                        {"text": "Seed evidence", "locator": "PubChem record"}
                    ],
                },
                {
                    "document_id": "PUBCHEM:163659",
                    "title": "Mithramycin, PubChem CID 163659",
                    "evidence_passages": [
                        {"text": "Identity evidence", "locator": "PubChem record"}
                    ],
                },
            ]
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["title"], "Mithramycin")
        self.assertEqual(len(documents[0]["evidence_passages"]), 2)

    def test_pubchem_locator_suffix_does_not_hide_a_different_name(self):
        with self.assertRaisesRegex(ProgramError, "Conflicting document metadata"):
            evidence._merge_documents(
                [
                    {
                        "document_id": "PUBCHEM:163659",
                        "title": "Mithramycin",
                    },
                    {
                        "document_id": "PUBCHEM:163659",
                        "title": "Different compound, PubChem CID 163659",
                    },
                ]
            )

    def test_publication_titles_remain_strict(self):
        with self.assertRaisesRegex(ProgramError, "Conflicting document metadata"):
            evidence._merge_documents(
                [
                    {"document_id": "PMID:123", "title": "Mithramycin"},
                    {
                        "document_id": "PMID:123",
                        "title": "Mithramycin, PubChem CID 163659",
                    },
                ]
            )

    def test_verified_publication_formatting_variants_merge(self):
        documents = evidence._merge_documents(
            [
                {
                    "document_id": "PMID:35445439",
                    "title": "5-HT2 receptor antagonism reduces motoneuron output",
                },
                {
                    "document_id": "PMID:35445439",
                    "title": "5-HT(2) receptor antagonism reduces motoneuron output.",
                },
            ]
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(
            documents[0]["title"],
            "5-HT2 receptor antagonism reduces motoneuron output",
        )

    def test_minor_publication_title_variants_merge_and_project_canonical_title(self):
        submitted = "Multi-center phase II study of a candidate treatment"
        canonical = "Multicenter phase 2 study of a candidate treatment"
        documents = evidence._merge_documents([
            {"document_id": "PMID:123", "title": submitted},
            {"document_id": "PMID:123", "title": canonical},
        ])
        metadata = {"PMID:123": {
            "title": canonical,
            "canonical_publication_id": "PMID:123",
            "identifier_aliases": ["PMID:123"],
            "metadata_source": "PubMed",
        }}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            bibliography, "_resolve_bibliographic_metadata", return_value=metadata
        ):
            projected = bibliography._canonicalize_documents(
                Path(directory), documents, verify_titles=True
            )

        self.assertEqual(projected[0]["title"], canonical)

    def test_publication_formatting_normalization_does_not_hide_word_changes(self):
        with self.assertRaisesRegex(ProgramError, "Conflicting document metadata"):
            evidence._merge_documents(
                [
                    {"document_id": "PMID:35445439", "title": "5-HT2 agonism"},
                    {"document_id": "PMID:35445439", "title": "5-HT2 antagonism"},
                ]
            )


if __name__ == "__main__":
    unittest.main()
