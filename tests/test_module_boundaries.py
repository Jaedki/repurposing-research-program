import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import program_core as core  # noqa: E402
from repurposing_program import contracts, errors, storage  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
