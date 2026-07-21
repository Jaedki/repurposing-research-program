#!/usr/bin/env python3
"""CLI for schema-v6 historical execution and the native schema-v7 runtime DAG."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from program_contract import FAILURE_KINDS
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
from v7_case_model import initialize_case as initialize_v7_case
from v7_case_model import inspect_artifact, is_v7_case_container
from v7_runtime import (
    complete_job as complete_v7_job,
    fail_job as fail_v7_job,
    initialize_runtime as initialize_v7_runtime,
    is_v7_runtime,
    next_action as next_v7_action,
    record_progress as record_v7_progress,
    recover_job as recover_v7_job,
    resume_action as resume_v7_action,
    start_job as start_v7_job,
    status as v7_status,
    validate_result as validate_v7_result,
)


def _case(args: argparse.Namespace, *, strict_v7: bool = False) -> dict[str, Any]:
    if args.case_file:
        path = Path(args.case_file).expanduser().resolve()
        if strict_v7 and not path.is_file():
            raise ValueError(f"Case file does not exist: {path}")
        value = read_json(path, {})
    else:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("Case file must contain one JSON object")
    for field in ("human_gene", "human_disease", "human_phenotype"):
        supplied = getattr(args, field, None)
        if supplied:
            if strict_v7 and field in value and value[field] != supplied:
                raise ValueError(
                    f"Case-file field {field!r} conflicts with the command-line value"
                )
            value[field] = supplied
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("run_folder")
    init.add_argument("--schema-version", type=int, choices=(6, 7))
    init.add_argument("--case-file")
    init.add_argument("--human-gene")
    init.add_argument("--human-disease")
    init.add_argument("--human-phenotype")
    init.add_argument("--runtime-config")
    init.add_argument(
        "--breadth-mode",
        choices=("broad_discovery", "balanced", "clinical_shortlist"),
    )
    init.add_argument("--max-active-jobs", type=int)
    init.add_argument("--source-budget", type=int)
    init.add_argument("--seed-budget", type=int)
    init.add_argument("--deep-review-budget", type=int)
    init.add_argument("--audit-budget", type=int)
    init.add_argument("--time-budget-seconds", type=int)
    init.add_argument("--cost-budget-units", type=int)
    init.add_argument("--max-source-records-per-shard", type=int)
    init.add_argument("--max-candidate-records-per-shard", type=int)
    init.add_argument("--max-shard-source-bytes", type=int)
    init.add_argument("--max-packet-bytes", type=int)
    for name in ("next", "resume", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("run_folder")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("run_folder")
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
    recover.add_argument("--job-id")
    recover.add_argument("--reason", default="assigned task unavailable")
    fail = commands.add_parser("fail")
    fail.add_argument("run_folder")
    fail.add_argument("job_id")
    fail.add_argument("failure_kind", choices=sorted(FAILURE_KINDS))
    fail.add_argument("--retry-after-seconds", type=int)
    fail.add_argument("--detail", default="")
    progress = commands.add_parser("progress")
    progress.add_argument("run_folder")
    progress.add_argument("job_id")
    progress.add_argument("agent_id")
    progress.add_argument("processed_records", type=int)
    progress.add_argument("total_records", type=int)
    progress.add_argument("--cursor", default="")
    progress.add_argument("--checkpoint-ref", default="")
    return parser


def _v7_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if getattr(args, "runtime_config", None):
        value = read_json(Path(args.runtime_config).expanduser().resolve(), None)
        if not isinstance(value, dict):
            raise ValueError("--runtime-config must contain one JSON object")
        config.update(value)
    for field in (
        "breadth_mode",
        "max_active_jobs",
        "source_budget",
        "seed_budget",
        "deep_review_budget",
        "audit_budget",
        "time_budget_seconds",
        "cost_budget_units",
        "max_source_records_per_shard",
        "max_candidate_records_per_shard",
        "max_shard_source_bytes",
        "max_packet_bytes",
    ):
        value = getattr(args, field, None)
        if value is not None:
            config[field] = value
    return config


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.run_folder).expanduser().resolve()
    try:
        if args.command == "init":
            preliminary_case = _case(args)
            declared_version = preliminary_case.get("schema_version")
            if declared_version is not None and (
                type(declared_version) is not int or declared_version not in {6, 7}
            ):
                raise ValueError("Case-file schema_version must be 6 or 7")
            selected_version = args.schema_version or declared_version or 6
            if declared_version is not None and declared_version != selected_version:
                raise ValueError(
                    f"Case-file schema_version {declared_version} conflicts with "
                    f"--schema-version {selected_version}"
                )
            case = _case(args, strict_v7=True) if selected_version == 7 else preliminary_case
            if selected_version == 7:
                case_manifest = initialize_v7_case(root, case)
                runtime_manifest = initialize_v7_runtime(root, _v7_runtime_config(args))
                result = {**case_manifest, **runtime_manifest}
            else:
                result = initialize(root, case)
        elif args.command == "inspect":
            result = inspect_artifact(root)
        elif is_v7_case_container(root):
            if not is_v7_runtime(root):
                initialize_v7_runtime(root, {})
            if args.command == "next":
                result = next_v7_action(root)
            elif args.command == "resume":
                result = resume_v7_action(root)
            elif args.command == "status":
                result = v7_status(root)
            elif args.command == "start":
                result = start_v7_job(root, args.job_id, args.agent_id)
            elif args.command == "validate-result":
                result = validate_v7_result(root, args.job_id, args.result_path)
            elif args.command == "complete":
                result = complete_v7_job(root, args.job_id, args.result_path)
            elif args.command == "recover-active":
                job_id = args.job_id
                if not job_id:
                    active = v7_status(root)["state"]["active_job_ids"]
                    if len(active) != 1:
                        raise ValueError("--job-id is required unless exactly one schema-v7 job is active")
                    job_id = active[0]
                result = recover_v7_job(root, job_id, args.new_agent_id, args.reason)
            elif args.command == "fail":
                result = fail_v7_job(
                    root,
                    args.job_id,
                    args.failure_kind,
                    args.retry_after_seconds,
                    args.detail,
                )
            elif args.command == "progress":
                result = record_v7_progress(
                    root,
                    args.job_id,
                    args.agent_id,
                    processed_records=args.processed_records,
                    total_records=args.total_records,
                    cursor=args.cursor,
                    checkpoint_ref=args.checkpoint_ref,
                )
            else:
                raise AssertionError(args.command)
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
        elif args.command == "progress":
            raise ValueError("progress checkpoints are available only for schema-v7 runs")
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
