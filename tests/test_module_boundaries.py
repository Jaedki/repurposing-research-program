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
    candidate_exports,
    candidates,
    contracts,
    errors,
    evidence,
    evidence_cards,
    graph,
    identity,
    manifests,
    orchestration,
    outputs,
    packets,
    pathology,
    ranking,
    run_state,
    storage,
    validation,
)


MODULES = (
    audit,
    bibliography,
    candidate_exports,
    candidates,
    contracts,
    errors,
    evidence,
    evidence_cards,
    graph,
    identity,
    manifests,
    orchestration,
    outputs,
    packets,
    pathology,
    ranking,
    run_state,
    storage,
    validation,
)

FUNCTION_OWNERS = {
    errors: ("ProgramError",),
    storage: (
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
    ),
    bibliography: (
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
    ),
    evidence: (
        "_all_documents",
        "_cited_documents",
        "_cited_ids",
        "_document_has_inspectable_content",
        "_find",
        "_merge_documents",
        "_merge_text",
        "_merge_unique",
        "_normalized_title",
        "_rows",
        "_select_cited_documents",
        "_source_index",
        "_validate_research_document_content",
        "_year",
    ),
    validation: (
        "_contract_rows",
        "_ids",
        "_references",
        "_required",
        "_secret_paths",
        "_validate_documents",
        "_validate_exact_object",
    ),
    pathology: (
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
    ),
    graph: (
        "_assemble_graph_result",
        "_graph_index",
        "_graph_node_context",
        "_graph_support_ids",
        "_merge_assertions",
    ),
    identity: (
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
    ),
    candidates: (
        "_review_batches",
        "_validate_cited_entries",
        "_validate_review_item",
        "_validate_seed_item",
        "_validate_string_list",
    ),
    audit: (
        "_accepted_ids",
        "_assessment_source_uses",
        "_component_score",
        "_validate_candidate_audit",
        "_validate_source_integrity",
    ),
    run_state: (
        "_case",
        "_first_missing",
        "_item_ids",
        "_item_results",
        "_load_results",
        "_program_status",
        "_stop_reason",
        "_verify_outputs",
        "graph_context",
        "initialize",
        "status",
    ),
    packets: (
        "_build_packet",
        "_packet_context",
        "_record_contract",
        "_validate_packet",
    ),
    orchestration: (
        "_advance_controller",
        "_build_graph_result",
        "_build_review_result",
        "_build_seed_result",
        "_item_cited_documents",
        "_item_collection",
        "_item_gaps",
        "_validate_result",
        "next_action",
        "submit",
    ),
    ranking: ("_final_score", "_project_ranked_row", "_ranked_rows"),
    evidence_cards: (
        "_cards_bytes",
        "_evidence_card_rows",
        "_reference_line",
        "_single_line",
        "_source_verification_summary",
    ),
    candidate_exports: ("_excluded_candidate_rows", "_provenance_rows"),
    manifests: ("_artifact", "_build_manifest"),
    outputs: ("_csv_bytes", "_write_output_files", "build_outputs"),
}


def _imports(module):
    tree = ast.parse(inspect.getsource(module))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    return names


class FinishedArchitectureBoundaryTest(unittest.TestCase):
    def test_every_implementation_has_one_module_owner(self):
        seen = {}
        for module, names in FUNCTION_OWNERS.items():
            for name in names:
                implementation = getattr(module, name)
                self.assertEqual(implementation.__module__, module.__name__)
                self.assertNotIn(name, seen)
                seen[name] = module.__name__

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
            self.assertIn(name, vars(contracts))

    def test_program_core_is_only_the_explicit_public_facade(self):
        expected = {
            "EXPERIMENTAL_USE_POLICY",
            "OBJECTIVE",
            "ProgramError",
            "STAGES",
            "build_outputs",
            "graph_context",
            "initialize",
            "next_action",
            "status",
            "submit",
        }
        self.assertEqual(set(core.__all__), expected)
        self.assertEqual(
            {name for name in vars(core) if not name.startswith("_")}, expected
        )
        tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
        self.assertFalse(
            any(
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                for node in tree.body
            )
        )

    def test_extracted_modules_never_import_program_core(self):
        for module in MODULES:
            self.assertNotIn("program_core", _imports(module), module.__name__)

    def test_output_modules_are_a_terminal_dependency_branch(self):
        output_names = {
            "candidate_exports",
            "evidence_cards",
            "manifests",
            "outputs",
            "ranking",
        }
        domain_modules = set(MODULES) - {
            candidate_exports,
            evidence_cards,
            manifests,
            outputs,
            ranking,
        }
        for module in domain_modules:
            self.assertFalse(_imports(module) & output_names, module.__name__)

        higher_level_names = {"orchestration", "outputs", "packets", "run_state"}
        for module in (candidate_exports, evidence_cards, manifests, ranking):
            self.assertFalse(_imports(module) & higher_level_names, module.__name__)

    def test_package_dependency_graph_is_acyclic(self):
        modules = {module.__name__.rsplit(".", 1)[-1]: module for module in MODULES}
        dependencies = {
            name: _imports(module) & modules.keys() for name, module in modules.items()
        }
        visiting = set()
        visited = set()

        def visit(name):
            if name in visiting:
                self.fail(f"Dependency cycle reaches {name}")
            if name in visited:
                return
            visiting.add(name)
            for dependency in dependencies[name]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in modules:
            visit(name)


if __name__ == "__main__":
    unittest.main()
