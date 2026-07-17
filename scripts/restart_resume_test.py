#!/usr/bin/env python3
"""Process-boundary stop/restart/resume test for the controller CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("orchestrate_program.py")


def _run(*args: str, expect: int = 0) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8"
    )
    assert completed.returncode == expect, completed.stderr or completed.stdout
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    return json.loads(stream)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        _run(
            "init", str(root), "--human-gene", "GENE1", "--worm-gene", "gene-1", "--allele-mode", "loss_of_function"
        )
        action = _run("next", str(root))["result"]
        assert action["action"] == "start_agent"
        assert action["spawn_contract"]["fork_turns"] == "none"
        started = _run("start", str(root), action["job_id"], "agent-before-restart")["result"]

        resumed = _run("resume", str(root))["result"]
        assert resumed["action"] == "resume_active_job"
        assert resumed["attempt_id"] == started["attempt_id"]

        recovered = _run("recover-active", str(root), "agent-after-restart")["result"]
        assert recovered["packet_hash"] == action["packet_hash"]
        assert recovered["recovered_from_attempt_id"] == started["attempt_id"]
        result_path = root / recovered["expected_result_path"]
        result_path.write_text(
            json.dumps(
                {
                    "job_id": action["job_id"],
                    "packet_hash": action["packet_hash"],
                    "all_chunks_processed": True,
                    "outcome": "completed",
                    "ledger_updates": {},
                    "approved_subtopics": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        validated = _run("validate-result", str(root), action["job_id"])["result"]
        assert validated["status"] == "valid"
        assert "\n" not in result_path.read_text(encoding="utf-8")
        completed = _run("complete", str(root), action["job_id"])["result"]
        assert completed["next"]["action"] == "close_agent"

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "run"
        _run(
            "init", str(root), "--human-gene", "GENE1", "--worm-gene", "gene-1", "--allele-mode", "loss_of_function"
        )
        worker_action = _run("next", str(root))["result"]
        worker_attempt = _run("start", str(root), worker_action["job_id"], "worker-before-chat")["result"]
        worker_result = root / worker_attempt["expected_result_path"]
        worker_result.write_text(
            json.dumps(
                {
                    "job_id": worker_action["job_id"],
                    "packet_hash": worker_action["packet_hash"],
                    "all_chunks_processed": True,
                    "outcome": "completed",
                    "ledger_updates": {},
                    "approved_subtopics": [],
                }
            ),
            encoding="utf-8",
        )
        _run("validate-result", str(root), worker_action["job_id"])
        worker_complete = _run("complete", str(root), worker_action["job_id"])["result"]
        _run(
            "release",
            str(root),
            worker_complete["next"]["attempt_id"],
            worker_complete["next"]["agent_id"],
        )

        audit_action = _run("next", str(root))["result"]
        audit_attempt = _run("start", str(root), audit_action["job_id"], "independent-auditor")["result"]
        audit_result = root / audit_attempt["expected_result_path"]
        audit_result.write_text(
            json.dumps(
                {
                    "job_id": audit_action["job_id"],
                    "packet_hash": audit_action["packet_hash"],
                    "all_chunks_processed": True,
                    "outcome": "repair_required",
                    "ledger_updates": {},
                    "approved_subtopics": [],
                    "material_findings": ["repair this deterministic fixture"],
                }
            ),
            encoding="utf-8",
        )
        _run("validate-result", str(root), audit_action["job_id"])
        audit_complete = _run("complete", str(root), audit_action["job_id"])["result"]
        _run(
            "release",
            str(root),
            audit_complete["next"]["attempt_id"],
            audit_complete["next"]["agent_id"],
        )
        repair_action = _run("resume", str(root))["result"]
        assert repair_action["job_id"] == worker_action["job_id"]
        assert repair_action["agent_action"] == "resume_assigned"
        assert repair_action["assigned_agent_id"] == "worker-before-chat"
        packet_hash = repair_action["packet_hash"]
        packet_manifest_path = repair_action["packet_manifest_path"]

        refused = _run(
            "recover-ready",
            str(root),
            repair_action["job_id"],
            "independent-auditor",
            expect=1,
        )
        assert "independent role" in refused["error"]

        recovered = _run(
            "recover-ready",
            str(root),
            repair_action["job_id"],
            "worker-after-chat",
            "--reason",
            "previous chat task unavailable",
        )["result"]
        assert recovered["prior_agent_id"] == "worker-before-chat"
        assert recovered["new_agent_id"] == "worker-after-chat"
        assert recovered["packet_hash"] == packet_hash
        assert recovered["packet_manifest_path"] == packet_manifest_path
        assert recovered["repair_round"] == 1
        assert len(recovered["repair_context_paths"]) == 1
        assert recovered["next"]["assigned_agent_id"] == "worker-after-chat"
        assert recovered["next"]["spawn_prompt"] == repair_action["spawn_prompt"]
        started_repair = _run(
            "start", str(root), repair_action["job_id"], "worker-after-chat"
        )["result"]
        assert started_repair["packet_hash"] == packet_hash
        attempts = [json.loads(line) for line in (root / "job_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
        assert attempts[-1]["agent_id"] == "worker-after-chat"
        assert attempts[-1]["packet_hash"] == packet_hash
        events = [json.loads(line) for line in (root / "orchestration.jsonl").read_text(encoding="utf-8").splitlines()]
        reassigned = [row for row in events if row.get("event") == "ready_repair_reassigned"]
        assert len(reassigned) == 1
        assert reassigned[0]["prior_agent_id"] == "worker-before-chat"
        assert reassigned[0]["new_agent_id"] == "worker-after-chat"

        repaired_result = root / started_repair["expected_result_path"]
        repaired_result.write_text(
            json.dumps(
                {
                    "job_id": repair_action["job_id"],
                    "packet_hash": packet_hash,
                    "all_chunks_processed": True,
                    "outcome": "completed",
                    "ledger_updates": {},
                    "approved_subtopics": [],
                }
            ),
            encoding="utf-8",
        )
        _run("validate-result", str(root), repair_action["job_id"])
        repaired_complete = _run("complete", str(root), repair_action["job_id"])["result"]
        re_audit_action = _run(
            "release",
            str(root),
            repaired_complete["next"]["attempt_id"],
            repaired_complete["next"]["agent_id"],
        )["result"]["next"]
        assert re_audit_action["job_id"] == audit_action["job_id"]
        assert re_audit_action["agent_action"] == "resume_assigned"
        assert re_audit_action["assigned_agent_id"] == "independent-auditor"
        refused_auditor = _run(
            "recover-ready",
            str(root),
            re_audit_action["job_id"],
            "worker-after-chat",
            expect=1,
        )
        assert "independent role" in refused_auditor["error"]
        recovered_auditor = _run(
            "recover-ready",
            str(root),
            re_audit_action["job_id"],
            "auditor-after-chat",
        )["result"]
        assert recovered_auditor["prior_agent_id"] == "independent-auditor"
        assert recovered_auditor["new_agent_id"] == "auditor-after-chat"
        assert recovered_auditor["packet_hash"] == re_audit_action["packet_hash"]
        assert recovered_auditor["repair_round"] == 1
        assert len(recovered_auditor["repair_context_paths"]) == 1
        assert recovered_auditor["next"]["spawn_prompt"] == re_audit_action["spawn_prompt"]
        started_auditor = _run(
            "start", str(root), re_audit_action["job_id"], "auditor-after-chat"
        )["result"]
        assert started_auditor["packet_hash"] == re_audit_action["packet_hash"]
    print("RESTART/RESUME TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
