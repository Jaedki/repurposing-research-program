import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import orchestrate_program as cli  # noqa: E402
import program_core as core  # noqa: E402


class PublicContractTest(unittest.TestCase):
    def test_program_core_exports_remain_explicit(self):
        self.assertEqual(
            set(core.__all__),
            {
                "EXPERIMENTAL_USE_POLICY",
                "OBJECTIVE",
                "ProgramError",
                "STAGES",
                "build_outputs",
                "connection_context",
                "graph_context",
                "initialize",
                "next_action",
                "status",
                "submit",
                "validate_submission",
            },
        )

    def test_cli_success_envelope_and_dispatch(self):
        expected = {"state": "needs_agent", "next_task": "pathology_curation"}
        stdout = io.StringIO()
        with (
            patch.object(cli, "status", return_value=expected) as status,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.main(["status", "RUN"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True, "result": expected})
        status.assert_called_once_with("RUN")

    def test_cli_error_envelope_and_exit_code(self):
        stderr = io.StringIO()
        with (
            patch.object(cli, "status", side_effect=core.ProgramError("baseline failure")),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = cli.main(["status", "RUN"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"ok": False, "error": "baseline failure"},
        )

    def test_cli_validate_dispatches_without_submission(self):
        expected = {"valid": True, "stage": "candidate_audit", "item_id": None}
        stdout = io.StringIO()
        with (
            patch.object(cli, "validate_submission", return_value=expected) as validate,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.main(["validate", "RUN", "result.json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True, "result": expected})
        validate.assert_called_once_with("RUN", "result.json")

    def test_cli_connection_context_dispatches_bounded_lookup(self):
        expected = {"context": {"connection": {"connection_id": "CONNECTION:1"}}}
        stdout = io.StringIO()
        with (
            patch.object(cli, "connection_context", return_value=expected) as lookup,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.main(["connection-context", "RUN", "CONNECTION:1"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": True, "result": expected})
        lookup.assert_called_once_with("RUN", "CONNECTION:1")


if __name__ == "__main__":
    unittest.main()
