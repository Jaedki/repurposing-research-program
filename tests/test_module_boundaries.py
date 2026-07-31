import ast
import inspect
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import (  # noqa: E402
    bibliography,
    contracts,
    errors,
    evidence,
    storage,
)


class StageOneBoundaryTest(unittest.TestCase):
    def test_contracts_have_one_owner(self):
        for name in (
            "AUDIT_EXCLUSION_POLICY",
            "CANONICAL_DOCUMENT_ID",
            "FIELD_RULES",
            "OBJECTIVE",
            "ROW_SCHEMAS",
            "SCORE_RUBRIC",
            "STAGES",
            "STAGE_GUIDANCE",
        ):
            self.assertIs(getattr(core, name), getattr(contracts, name))

    def test_error_and_storage_helpers_have_one_owner(self):
        self.assertIs(core.ProgramError, errors.ProgramError)
        for name in (
            "_canonical_bytes",
            "_item_result_path",
            "_item_token",
            "_packet_path",
            "_read_json",
            "_result_path",
            "_sha256",
            "_stable_id",
            "_submission_path",
            "_write_json",
            "_write_jsonl",
            "_write_once",
        ):
            helper = getattr(storage, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.storage")


class StageTwoBoundaryTest(unittest.TestCase):
    def test_bibliography_has_one_owner(self):
        for name in (
            "_batches",
            "_bibliographic_get",
            "_bibliographic_request",
            "_canonicalize_documents",
            "_doi_metadata",
            "_id_converter_records",
            "_ncbi_summaries",
            "_normalized_publication_id",
            "_resolve_bibliographic_metadata",
            "_summary_metadata",
            "_validate_bibliographic_documents",
        ):
            helper = getattr(bibliography, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.bibliography")

    def test_evidence_has_one_owner(self):
        for name in (
            "_all_documents",
            "_cited_documents",
            "_cited_ids",
            "_document_has_inspectable_content",
            "_merge_documents",
            "_merge_unique",
            "_normalized_title",
            "_rows",
            "_select_cited_documents",
            "_source_index",
            "_validate_research_document_content",
            "_year",
        ):
            helper = getattr(evidence, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.evidence")

    def test_extracted_modules_do_not_import_program_core(self):
        for module in (bibliography, contracts, errors, evidence, storage):
            imported = {
                alias.name
                for node in ast.walk(ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported.update(
                node.module or ""
                for node in ast.walk(ast.parse(inspect.getsource(module)))
                if isinstance(node, ast.ImportFrom)
            )
            self.assertNotIn("program_core", imported)


if __name__ == "__main__":
    unittest.main()
