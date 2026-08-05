"""Final output artifact metadata and manifest construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracts import EXPERIMENTAL_USE_POLICY
from .evidence import _rows
from .storage import _accepted_result_files, _sha256


def _artifact(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"filename": path.name, "bytes": len(payload), "sha256": _sha256(payload)}


def _build_manifest(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    artifact_paths: list[Path],
) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "case_sha256": _sha256((run_root / "case.json").read_bytes()),
        "status": "complete",
        "candidate_count": len(rows),
        "excluded_candidate_count": len(
            _rows(results["candidate_audit"]["records"], "excluded_candidates")
        ),
        "raw_candidate_count": len(
            _rows(results["candidate_seed_generation"]["records"], "candidates")
        ),
        "deduplicated_candidate_count": len(candidates),
        "accepted_results": {
            name: _sha256(path.read_bytes())
            for name, path in _accepted_result_files(run_root).items()
        },
        "artifacts": [_artifact(path) for path in artifact_paths],
        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
