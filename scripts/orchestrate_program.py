#!/usr/bin/env python3
"""CLI for the lean repurposing research controller."""

from __future__ import annotations

import argparse
import json
import sys

from program_core import ProgramError, build_outputs, initialize, next_action, status, submit


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a disease run")
    init.add_argument("run_folder")
    init.add_argument("--disease", required=True)
    init.add_argument("--gene")
    init.add_argument("--mondo", help="optional exact MONDO disease identifier")
    for name in ("next", "status", "build"):
        command = commands.add_parser(name)
        command.add_argument("run_folder")
    submit_command = commands.add_parser("submit", help="validate and accept one agent result")
    submit_command.add_argument("run_folder")
    submit_command.add_argument("result_path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        match args.command:
            case "init":
                result = initialize(args.run_folder, args.disease, args.gene, args.mondo)
            case "next":
                result = next_action(args.run_folder)
            case "submit":
                result = submit(args.run_folder, args.result_path)
            case "status":
                result = status(args.run_folder)
            case "build":
                result = build_outputs(args.run_folder)
            case _:
                raise AssertionError(args.command)
    except (ProgramError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
