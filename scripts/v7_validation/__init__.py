"""Public orchestrator for focused schema-v7 validation domains."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import audit_council, case_endpoints, evidence, identity, outputs, ranking, retrieval_coverage, runtime, seeds_funnel
from .common import ValidationIssue, load_committed_snapshot, normalize_snapshot


VALIDATORS = (
    runtime.validate,
    retrieval_coverage.validate,
    case_endpoints.validate,
    seeds_funnel.validate,
    evidence.validate,
    identity.validate,
    ranking.validate,
    audit_council.validate,
)


def validate_snapshot(snapshot: Mapping[str, Any]) -> list[ValidationIssue]:
    normalized = normalize_snapshot(snapshot)
    issues: list[ValidationIssue] = []
    for validator in VALIDATORS:
        issues.extend(validator(normalized))
    return list(dict.fromkeys(issues))


def validate_output_artifacts(
    root: str | Path,
    snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
) -> list[ValidationIssue]:
    return outputs.validate(normalize_snapshot(snapshot), Path(root), manifest)


def validate_run(root: str | Path, *, final: bool = True) -> list[str]:
    run_root = Path(root).expanduser().resolve()
    try:
        snapshot = load_committed_snapshot(run_root)
    except Exception as exc:
        return [f"[runtime:SNAPSHOT_LOAD] {exc}"]
    issues = validate_snapshot(snapshot)
    output_root = run_root / "outputs_v7"
    manifest_path = output_root / "artifact_manifest.json"
    if manifest_path.is_file():
        import json

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            issues.append(ValidationIssue("outputs", "MANIFEST_JSON", str(exc)))
        else:
            issues.extend(validate_output_artifacts(output_root, snapshot, manifest))
    elif final:
        issues.append(ValidationIssue("outputs", "MISSING_OUTPUTS", "outputs_v7/artifact_manifest.json is required"))
    try:
        from v7_runtime import validate_runtime

        issues.extend(
            ValidationIssue("runtime", "RUNTIME", message)
            for message in validate_runtime(run_root, final=final and not issues)
        )
    except Exception as exc:
        issues.append(ValidationIssue("runtime", "RUNTIME_IMPORT", str(exc)))
    return list(dict.fromkeys(row.render() for row in issues))


__all__ = [
    "ValidationIssue",
    "load_committed_snapshot",
    "validate_output_artifacts",
    "validate_run",
    "validate_snapshot",
]
