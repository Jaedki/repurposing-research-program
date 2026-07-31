import ast
import inspect
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import (  # noqa: E402
    audit,
    bibliography,
    candidates,
    contracts,
    errors,
    evidence,
    graph,
    identity,
    pathology,
    storage,
    validation,
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
        for module in (
            bibliography,
            candidates,
            contracts,
            errors,
            evidence,
            graph,
            identity,
            pathology,
            storage,
            validation,
            audit,
        ):
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


class StageThreeBoundaryTest(unittest.TestCase):
    def test_shared_validation_has_one_owner(self):
        for name in (
            "_contract_rows",
            "_ids",
            "_references",
            "_required",
            "_secret_paths",
            "_validate_documents",
            "_validate_exact_object",
        ):
            helper = getattr(validation, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.validation")
        for name in ("_find", "_merge_text"):
            helper = getattr(evidence, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.evidence")

    def test_pathology_has_one_owner(self):
        for name in (
            "_canonical_source_records",
            "_compact_disease_context",
            "_curation_concepts",
            "_forbidden_pathology_paths",
            "_research_concepts",
            "_validate_curation",
            "_validate_pathology_item",
            "_validate_source_adjudication",
            "_validate_source_result",
            "_validate_source_screening",
        ):
            helper = getattr(pathology, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.pathology")

    def test_graph_has_one_owner(self):
        for name in (
            "_assemble_graph_result",
            "_graph_index",
            "_graph_node_context",
            "_graph_support_ids",
            "_merge_assertions",
        ):
            helper = getattr(graph, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.graph")


class StageFourBoundaryTest(unittest.TestCase):
    def test_identity_has_one_owner(self):
        for name in (
            "_candidate_queries",
            "_canonical_candidates",
            "_empty_identity_result",
            "_exact_identity_groups",
            "_identity_candidate_options",
            "_identity_queue",
            "_merge_candidate_rows",
            "_post_unichem",
            "_query_key",
            "_resolve_seed_identities",
            "_unichem_request",
            "_unichem_requests",
            "_validate_candidate_identity",
        ):
            helper = getattr(identity, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.identity")

    def test_candidate_rules_have_one_owner(self):
        for name in (
            "_review_batches",
            "_validate_cited_entries",
            "_validate_review_item",
            "_validate_seed_item",
            "_validate_string_list",
        ):
            helper = getattr(candidates, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.candidates")

    def test_audit_rules_have_one_owner(self):
        for name in (
            "_accepted_ids",
            "_assessment_source_uses",
            "_component_score",
            "_validate_candidate_audit",
            "_validate_source_integrity",
        ):
            helper = getattr(audit, name)
            self.assertIs(getattr(core, name), helper)
            self.assertEqual(helper.__module__, "repurposing_program.audit")


if __name__ == "__main__":
    unittest.main()
