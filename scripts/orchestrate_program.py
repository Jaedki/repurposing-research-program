#!/usr/bin/env python3
"""Thin CLI over the deterministic schema-v5 programme runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from program_io import read_json
from program_runtime import (
    complete_job,
    fail_job,
    initialize,
    next_action,
    recover_active,
    resume_action,
    start_job,
    status,
    validate_result,
)


def _case(args: argparse.Namespace) -> dict[str, Any]:
    value = read_json(Path(args.case_file), {}) if args.case_file else {}
    if not isinstance(value, dict):
        raise ValueError("Case file must contain one JSON object")
    for field in ("human_gene", "human_disease", "human_phenotype"):
        supplied = getattr(args, field, None)
        if supplied:
            value[field] = supplied
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("run_folder")
    init.add_argument("--case-file")
    init.add_argument("--human-gene")
    init.add_argument("--human-disease")
    init.add_argument("--human-phenotype")
    for name in ("next", "resume", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("run_folder")
    start = commands.add_parser("start")
    start.add_argument("run_folder")
    start.add_argument("job_id")
    start.add_argument("agent_id")
    validate = commands.add_parser("validate-result")
    validate.add_argument("run_folder")
    validate.add_argument("job_id")
    validate.add_argument("--result-path")
    complete = commands.add_parser("complete")
    complete.add_argument("run_folder")
    complete.add_argument("job_id")
    complete.add_argument("--result-path")
    recover = commands.add_parser("recover-active")
    recover.add_argument("run_folder")
    recover.add_argument("new_agent_id")
    recover.add_argument("--reason", default="assigned task unavailable")
    fail = commands.add_parser("fail")
    fail.add_argument("run_folder")
    fail.add_argument("job_id")
    fail.add_argument("failure_kind", choices=("rate_limit", "spawn_failure", "transient", "unrecoverable"))
    fail.add_argument("--retry-after-seconds", type=int, default=60)
    fail.add_argument("--detail", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.run_folder).expanduser().resolve()
    try:
        if args.command == "init":
            result = initialize(root, _case(args))
        elif args.command == "next":
            result = next_action(root)
        elif args.command == "resume":
            result = resume_action(root)
        elif args.command == "status":
            result = status(root)
        elif args.command == "start":
            result = start_job(root, args.job_id, args.agent_id)
        elif args.command == "validate-result":
            result = validate_result(root, args.job_id, args.result_path)
        elif args.command == "complete":
            result = complete_job(root, args.job_id, args.result_path)
        elif args.command == "recover-active":
            result = recover_active(root, args.new_agent_id, args.reason)
        elif args.command == "fail":
            result = fail_job(root, args.job_id, args.failure_kind, args.retry_after_seconds, args.detail)
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    if args.command == "validate-result" and result.get("status") != "valid":
        print(json.dumps({"ok": False, "result": result}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
