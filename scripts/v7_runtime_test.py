#!/usr/bin/env python3
"""Integration tests for the schema-v7 concurrent DAG, packets, commit, and replay."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from v7_case_model import initialize_case
from v7_case_model_test import endpoint_input
from v7_packets import ROLE_CONTRACTS, build_task_packets, canonical_bytes
from v7_runtime import (
    SimulatedInterruption,
    V7RuntimeAdapter,
    V7RuntimeError,
    complete_job,
    fail_job,
    initialize_runtime,
    next_action,
    record_progress,
    start_job,
    status,
    validate_runtime,
)


ZERO_USAGE = {
    "source_records": 0,
    "seeds": 0,
    "deep_reviews": 0,
    "audits": 0,
    "elapsed_seconds": 0,
    "cost_units": 0,
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _new_root(base: Path, name: str, **config: int | str) -> Path:
    root = base / name
    initialize_case(
        root,
        {
            "schema_version": 7,
            "human_gene": "TP53",
            "endpoints": [endpoint_input()],
        },
    )
    initialize_runtime(root, config)
    return root


def _job(root: Path, job_id: str) -> dict[str, Any]:
    return next(
        row
        for row in _read(root / "runtime_v7" / "execution_plan.json")["jobs"]
        if row["job_id"] == job_id
    )


def _record_ids(root: Path, action: dict[str, Any]) -> list[str]:
    return [str(ref["record_id"]) for ref in _job(root, action["job_id"])["input_refs"]]


def _result(
    root: Path,
    action: dict[str, Any],
    agent_id: str,
    records: dict[str, list[dict[str, Any]]],
    *,
    usage: dict[str, int] | None = None,
) -> tuple[dict[str, Any], Path]:
    attempt = start_job(root, action["job_id"], agent_id)
    job = _job(root, action["job_id"])
    plan = _read(root / "runtime_v7" / "execution_plan.json")
    jobs = {row["job_id"]: row for row in plan["jobs"]}
    result = {
        "schema_version": 7,
        "job_id": job["job_id"],
        "attempt_id": attempt["attempt_id"],
        "packet_hash": job["packet_hash"],
        "dependency_commit_ids": sorted(
            jobs[value]["commit_id"] for value in job["dependency_job_ids"]
        ),
        "outcome": "completed",
        "shard_complete": True,
        "records": records,
        "progress": {
            "processed_records": job["input_record_count"],
            "total_records": job["input_record_count"],
            "cursor": "",
            "checkpoint_ref": "",
        },
        "budget_usage": {**ZERO_USAGE, **(usage or {})},
    }
    path = root / attempt["expected_result_path"]
    _write(path, result)
    return attempt, path


def _source_records(count: int) -> dict[str, list[dict[str, Any]]]:
    universes = [
        {"source_universe_id": f"UNIVERSE-{index:04d}", "source_id": f"SOURCE-{index:04d}"}
        for index in range(count)
    ]
    plans = [
        {
            "query_plan_id": f"QUERY-{index:04d}",
            "source_universe": universes[index],
            "required": True,
        }
        for index in range(count)
    ]
    return {"source_universes": universes, "query_plans": plans}


def _records_for_action(root: Path, action: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    role = action["role"]
    identities = _record_ids(root, action)
    if role == "case_model_constructor":
        return {"broad_case_model_snapshots": [{"snapshot_id": "BROAD-SNAPSHOT-001"}]}, {}
    if role == "discovery_source_worker":
        seeds = [{"seed_id": f"SEED-{value}"} for value in identities]
        proofs = [{"coverage_proof_id": f"PROOF-{value}"} for value in identities]
        return {
            "coverage_proofs": proofs,
            "candidate_seeds": seeds,
            "source_mappings": [],
            "discovery_routes": [],
        }, {"source_records": len(identities), "seeds": len(seeds)}
    if role == "identity_worker":
        suffixes = [value.removeprefix("SEED-") for value in identities]
        return {
            "identity_resolutions": [
                {"identity_resolution_id": f"IDENTITY-{value}"} for value in suffixes
            ],
            "screening_decisions": [
                {"decision_id": f"IDENTITY-DISPOSITION-{value}"} for value in suffixes
            ],
            "normalized_interventions": [
                {"normalized_intervention_id": f"NI-{value}"} for value in suffixes
            ],
        }, {}
    if role == "preliminary_triage_worker":
        suffixes = [value.removeprefix("NI-") for value in identities]
        return {
            "screen_records": [{"screen_record_id": f"SCREEN-{value}"} for value in suffixes],
            "screened_candidates": [
                {"screened_candidate_id": f"CANDIDATE-{value}"} for value in suffixes
            ],
            "triage_dispositions": [
                {"disposition_id": f"TRIAGE-{value}"} for value in suffixes
            ],
        }, {}
    if role == "deep_evidence_worker":
        suffixes = [value.removeprefix("CANDIDATE-") for value in identities]
        return {
            "deep_evidence_packages": [
                {"package_id": f"PACKAGE-{value}"} for value in suffixes
            ],
            "deep_candidates": [{"candidate_id": f"DEEP-{value}"} for value in suffixes],
        }, {"deep_reviews": len(suffixes)}
    if role == "ranking_preparation_worker":
        suffixes = [value.removeprefix("DEEP-") for value in identities]
        return {
            "ranking_preparation_records": [
                {"preparation_id": f"PREP-{value}"} for value in suffixes
            ]
        }, {}
    if role == "audit_sampling_worker":
        suffixes = [value.removeprefix("PREP-") for value in identities]
        return {
            "audit_assignments": [
                {"assignment_id": f"ASSIGN-{value}"} for value in suffixes
            ]
        }, {}
    if role == "candidate_auditor":
        suffixes = [value.removeprefix("ASSIGN-") for value in identities]
        return {
            "audit_records": [
                {"audit_record_id": f"AUDIT-{value}"} for value in suffixes
            ],
            "portfolio_review_items": [
                {"review_item_id": f"REVIEW-{value}"} for value in suffixes
            ],
        }, {"audits": len(suffixes)}
    if role == "council_portfolio_reviewer":
        suffixes = [value.removeprefix("REVIEW-") for value in identities]
        return {
            "council_records": [
                {"council_record_id": f"COUNCIL-{value}"} for value in suffixes
            ],
            "portfolio_review_records": [
                {"portfolio_review_id": f"PORTFOLIO-{value}"} for value in suffixes
            ],
        }, {}
    if role == "final_structural_validator":
        return {"validation_reports": [{"validation_report_id": "VALIDATION-001"}]}, {}
    if role == "final_output_builder":
        return {"output_manifests": [{"output_manifest_id": "OUTPUTS-001"}]}, {}
    raise AssertionError(role)


def _complete_action(root: Path, action: dict[str, Any], agent_id: str) -> dict[str, Any]:
    records, usage = _records_for_action(root, action)
    _result(root, action, agent_id, records, usage=usage)
    return complete_job(root, action["job_id"])


def _prepare_discovery(root: Path, query_count: int) -> list[dict[str, Any]]:
    action = next_action(root)
    _complete_action(root, action["jobs"][0], "case-model-agent")
    action = next_action(root)
    planner = action["jobs"][0]
    _result(root, planner, "planner-agent", _source_records(query_count))
    complete_job(root, planner["job_id"])
    return next_action(root)["jobs"]


def _run_to_completion(root: Path, *, reverse_batches: bool) -> dict[str, Any]:
    supplied_source_plan = False
    agent_number = 0
    while True:
        action = next_action(root)
        if action["action"] == "complete":
            return status(root)
        if action["action"] == "wait_for_retries":
            raise AssertionError(action)
        if action["action"] == "await_active_jobs":
            raise AssertionError(action)
        if action["action"] == "blocked":
            raise AssertionError(action)
        if action["action"] == "blocked_by_dependencies":
            raise AssertionError(action)
        jobs = list(action["jobs"])
        staged: list[tuple[dict[str, Any], str]] = []
        for job_action in jobs:
            agent_number += 1
            agent_id = f"agent-{agent_number:04d}"
            if job_action["role"] == "source_universe_planner":
                self_records = _source_records(12)
                supplied_source_plan = True
            else:
                self_records, usage = _records_for_action(root, job_action)
            if job_action["role"] == "source_universe_planner":
                usage = {}
            _result(root, job_action, agent_id, self_records, usage=usage)
            staged.append((job_action, agent_id))
        if reverse_batches:
            staged.reverse()
        for job_action, _ in staged:
            complete_job(root, job_action["job_id"])
    assert supplied_source_plan


class V7PacketContractTests(unittest.TestCase):
    def test_role_contracts_are_minimal_and_discovery_receives_no_global_contracts(self) -> None:
        discovery = ROLE_CONTRACTS["discovery_source_worker"]
        serialized = canonical_bytes(discovery)
        self.assertLess(len(serialized), 4096)
        self.assertNotIn(b"ranking", serialized)
        self.assertNotIn(b"council", serialized)
        self.assertNotIn(b"runtime_contract", serialized)
        self.assertEqual(
            set(discovery["allowed_input_collections"]),
            {"query_plans", "source_universes"},
        )

    def test_500_and_1000_candidate_packets_obey_count_and_byte_limits(self) -> None:
        for count in (500, 1000):
            packets = build_task_packets(
                "candidate_audit",
                [f"CANDIDATE-{index:05d}" for index in range(count)],
                125,
                16384,
            )
            flattened = [value for packet in packets for value in packet["candidate_ids"]]
            self.assertEqual(len(flattened), count)
            self.assertEqual(len(set(flattened)), count)
            self.assertTrue(all(len(packet["candidate_ids"]) <= 125 for packet in packets))
            self.assertTrue(all(len(canonical_bytes(packet)) <= 16384 for packet in packets))
            self.assertTrue(all(len(packet["candidate_ids"]) < 500 for packet in packets))


class V7RuntimeIntegrationTests(unittest.TestCase):
    def test_configurable_concurrency_yields_parallel_ready_source_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _new_root(
                Path(temporary),
                "run",
                max_active_jobs=4,
                max_source_records_per_shard=1,
            )
            ready = _prepare_discovery(root, 8)
            self.assertEqual(len(ready), 4)
            self.assertEqual({row["stage"] for row in ready}, {"discovery_source_shards"})
            self.assertEqual({row["input_record_count"] for row in ready}, {1})
            self.assertEqual(len({row["job_id"] for row in ready}), 4)

    def test_out_of_order_and_duplicate_completion_have_one_canonical_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _new_root(
                Path(temporary),
                "run",
                max_active_jobs=8,
                max_source_records_per_shard=1,
            )
            ready = _prepare_discovery(root, 6)
            staged: list[dict[str, Any]] = []
            for index, action in enumerate(ready):
                records, usage = _records_for_action(root, action)
                _result(root, action, f"agent-{index}", records, usage=usage)
                staged.append(action)
            commits = [complete_job(root, row["job_id"]) for row in reversed(staged)]
            duplicate = complete_job(root, staged[0]["job_id"])
            self.assertTrue(duplicate["duplicate_completion_prevented"])
            self.assertEqual(len({row["commit_id"] for row in commits}), 6)
            index = _read(root / "runtime_v7" / "canonical_index.json")
            self.assertEqual(len(index["collections"]["candidate_seeds"]), 6)
            self.assertEqual(validate_runtime(root), [])

    def test_progress_retry_and_failed_required_shard_remain_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _new_root(
                Path(temporary),
                "run",
                max_active_jobs=2,
                max_source_records_per_shard=1,
            )
            ready = _prepare_discovery(root, 2)
            action = ready[0]
            attempt = start_job(root, action["job_id"], "worker-a")
            progress = record_progress(
                root,
                action["job_id"],
                "worker-a",
                processed_records=1,
                total_records=2,
                cursor="cursor-1",
                checkpoint_ref="checkpoint-1",
            )
            self.assertEqual(progress["processed_records"], 1)
            retry = fail_job(root, action["job_id"], "rate_limit", retryable=True)
            self.assertEqual(retry["status"], "retry_wait")
            self.assertEqual(retry["packet_hash"], action["packet_hash"])

            second = ready[1]
            start_job(root, second["job_id"], "worker-b")
            failed = fail_job(root, second["job_id"], "unrecoverable")
            self.assertEqual(failed["status"], "failed")
            snapshot = status(root)
            failed_job = next(row for row in snapshot["jobs"] if row["job_id"] == second["job_id"])
            self.assertEqual(failed_job["status"], "failed")
            self.assertIn("required_shard_failed", " ".join(snapshot["state"]["acceptance_blockers"]))
            self.assertEqual(attempt["packet_hash"], retry["packet_hash"])

    def test_seed_budget_stops_deeper_work_without_erasing_discovered_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _new_root(
                Path(temporary),
                "run",
                max_active_jobs=8,
                max_source_records_per_shard=1,
                seed_budget=2,
            )
            ready = _prepare_discovery(root, 4)
            for index, action in enumerate(ready):
                _complete_action(root, action, f"discovery-{index}")
            snapshot = status(root)
            index = _read(root / "runtime_v7" / "canonical_index.json")
            self.assertEqual(len(index["collections"]["candidate_seeds"]), 4)
            self.assertTrue(snapshot["state"]["budget_exhausted"]["seed"])
            next_value = next_action(root)
            self.assertEqual(next_value["action"], "start_agents")
            self.assertEqual(
                {row["stage"] for row in next_value["jobs"]},
                {"identity_shards"},
            )

    def test_interrupted_published_commit_is_recovered_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _new_root(Path(temporary), "run", max_active_jobs=2)
            action = next_action(root)["jobs"][0]
            records, usage = _records_for_action(root, action)
            _result(root, action, "worker", records, usage=usage)
            with self.assertRaises(SimulatedInterruption):
                complete_job(
                    root,
                    action["job_id"],
                    simulate_interruption_after_commit=True,
                )
            resumed = status(root)
            self.assertIn(action["job_id"], resumed["recovered_commit_jobs"])
            recovered_job = next(row for row in resumed["jobs"] if row["job_id"] == action["job_id"])
            self.assertEqual(recovered_job["status"], "committed")
            self.assertTrue(recovered_job["commit_id"].startswith("V7COMMIT-"))
            self.assertEqual(validate_runtime(root), [])

    def test_schedule_independent_fan_in_and_scientific_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config = {
                "max_active_jobs": 12,
                "max_source_records_per_shard": 2,
                "max_candidate_records_per_shard": 2,
                "max_fan_in_dependencies": 3,
                "deep_review_budget": 100,
                "audit_budget": 100,
            }
            first = _new_root(base, "first", **config)
            second = _new_root(base, "second", **config)
            first_status = _run_to_completion(first, reverse_batches=False)
            second_status = _run_to_completion(second, reverse_batches=True)
            self.assertEqual(first_status["state"]["status"], "complete")
            self.assertEqual(second_status["state"]["status"], "complete")
            self.assertEqual(
                first_status["canonical_scientific_hash"],
                second_status["canonical_scientific_hash"],
            )
            first_plan = _read(first / "runtime_v7" / "execution_plan.json")
            second_plan = _read(second / "runtime_v7" / "execution_plan.json")
            first_barriers = sorted(
                (row["stage"], row["job_id"], row["commit_id"])
                for row in first_plan["jobs"]
                if row["internal"] and "fanin" in row["stage"]
            )
            second_barriers = sorted(
                (row["stage"], row["job_id"], row["commit_id"])
                for row in second_plan["jobs"]
                if row["internal"] and "fanin" in row["stage"]
            )
            self.assertEqual(first_barriers, second_barriers)
            self.assertTrue(
                all(len(row["dependency_job_ids"]) <= 3 for row in first_plan["jobs"] if row["internal"])
            )
            self.assertEqual(validate_runtime(first, final=True), [])
            self.assertEqual(validate_runtime(second, final=True), [])

    def test_runtime_adapter_replay_is_scientifically_stable(self) -> None:
        adapter = V7RuntimeAdapter()
        packets = adapter.build_task_packets(
            "candidate_audit",
            [f"CANDIDATE-{index:03d}" for index in range(20)],
            5,
            4096,
        )
        first = adapter.execute_packets(
            packets,
            {"after_stage": [packets[1]["shard_key"]]},
            {"order": [packet["shard_key"] for packet in packets]},
        )
        second = adapter.execute_packets(
            packets,
            {"after_stage": []},
            {"order": [packet["shard_key"] for packet in reversed(packets)]},
        )
        self.assertEqual(first["scientific_hash"], second["scientific_hash"])
        self.assertNotEqual(first["execution_hash"], second["execution_hash"])
        self.assertEqual(first["canonical_record_count"], 20)
        self.assertTrue(first["recovered_commits"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
