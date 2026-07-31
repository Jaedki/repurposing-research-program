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
                "graph_context",
                "initialize",
                "next_action",
                "status",
                "submit",
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


if __name__ == "__main__":
    unittest.main()
