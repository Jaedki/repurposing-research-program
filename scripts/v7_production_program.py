#!/usr/bin/env python3
"""Persist and execute one complete schema-v7 production programme end to end."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from v7_case_model import CaseRevision, is_v7_case_container, validate_case_revision
from v7_output_contract import EXPERIMENTAL_USE_POLICY
from v7_outputs import write_full_funnel_outputs
from v7_packets import canonical_bytes, canonical_sha256
from v7_production_discovery import V7DiscoveryAdapter, validate_discovery_aggregate
from v7_production_disposition import V7DispositionAdapter, validate_disposition_aggregate
from v7_production_portfolio import V7PortfolioAdapter, validate_portfolio_aggregate
from v7_production_screen_deep import V7ScreenDeepAdapter, validate_screen_deep_aggregate
from v7_runtime import (
    RUNTIME_DIRECTORY,
    complete_job,
    initialize_runtime,
    is_v7_runtime,
    next_action,
    start_job,
    status as runtime_status,
    validate_runtime,
)
from v7_validation import load_committed_snapshot, validate_run, validate_snapshot
from v7_validation.common import plain


PROGRAM_MODEL_VERSION = "schema-v7-production-program-v2"
PROGRAM_DIRECTORY = "production_program"


class ProgramAggregateError(ValueError):
    pass


class ProgramAggregateConflictError(ProgramAggregateError):
    pass


InputFactory = Callable[[CaseRevision, Mapping[str, Any]], Mapping[str, Any]]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ProgramAggregateError(f"Expected one JSON object: {path}")
    return value


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        if path.read_bytes() != payload:
            raise ProgramAggregateConflictError(f"Persisted programme artifact conflicts: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{canonical_sha256(value)[:24]}"


def _factory_value(
    label: str,
    supplied: Mapping[str, Any] | InputFactory,
    case: CaseRevision,
    upstream: Mapping[str, Any],
) -> Mapping[str, Any]:
    value = supplied(case, upstream) if callable(supplied) else supplied
    if not isinstance(value, Mapping):
        raise ProgramAggregateError(f"{label} must resolve to one mapping")
    return value


def _screening_projection(
    disposition: Mapping[str, Any],
    deep: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    screens_by_seed = {
        str(row["representative_seed_id"]): dict(row)
        for row in deep.get("screen_records", [])
    }
    screened_by_seed = {
        str(row["representative_seed_id"]): dict(row)
        for row in deep.get("screened_candidates", [])
    }
    identity_by_seed = {
        str(row["seed_id"]): dict(row)
        for row in disposition.get("identity_resolutions", [])
    }
    decisions: list[dict[str, Any]] = []
    quarantines: list[dict[str, Any]] = []
    triage: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for row in disposition.get("seed_dispositions", []):
        seed_id = str(row["seed_id"])
        canonical_disposition = str(row["canonical_disposition"])
        screen = screens_by_seed.get(seed_id)
        screening_outcome = (
            str(screen["screening_outcome"])
            if canonical_disposition == "admit" and screen is not None
            else "not_screened"
        )
        decision = {
            "decision_id": _stable_id(
                "SCREENING-DECISION",
                {
                    "seed_id": seed_id,
                    "seed_disposition_id": row["seed_disposition_id"],
                    "screening_outcome": screening_outcome,
                },
            ),
            "seed_id": seed_id,
            "seed_disposition_id": row["seed_disposition_id"],
            "canonical_disposition": canonical_disposition,
            "disposition": row.get("reason_code", canonical_disposition),
            "screening_outcome": screening_outcome,
            "representative_seed_id": row.get("representative_seed_id"),
            "reason": row["reason"],
            "endpoint_assessments": [],
        }
        decisions.append(decision)
        triage.append(
            {
                "disposition_id": _stable_id("TRIAGE-DISPOSITION", decision),
                "seed_id": seed_id,
                "normalized_intervention_id": row.get("normalized_intervention_id"),
                "screening_outcome": screening_outcome,
                "reason": screen.get("reason", row["reason"]) if screen else row["reason"],
            }
        )
        if canonical_disposition == "quarantine":
            identity = identity_by_seed.get(seed_id, {})
            quarantines.append(
                {
                    "quarantine_id": _stable_id("SEED-QUARANTINE", decision),
                    "seed_id": seed_id,
                    "disposition": row.get("reason_code", "identity_unresolved"),
                    "identity_status": identity.get("status", "unresolved"),
                    "reason": row["reason"],
                    "unresolved_fields": ["normalized_intervention_id"],
                    "source_mapping_ids": [row["source_mapping_id"]],
                    "discovery_route_ids": list(row.get("discovery_route_ids", [])),
                    "alias_ids": [],
                    "can_advance": False,
                }
            )
        screened = screened_by_seed.get(seed_id)
        if screened is not None and screening_outcome == "screened":
            links.append(
                {
                    "link_id": _stable_id(
                        "SEED-CANDIDATE-LINK",
                        {
                            "seed_id": seed_id,
                            "screened_candidate_id": screened["screened_candidate_id"],
                        },
                    ),
                    "seed_id": seed_id,
                    "screened_candidate_id": screened["screened_candidate_id"],
                    "representative_seed_id": seed_id,
                }
            )
    return {
        "screening_decisions": sorted(decisions, key=lambda row: row["seed_id"]),
        "quarantined_seeds": sorted(quarantines, key=lambda row: row["seed_id"]),
        "triage_dispositions": sorted(triage, key=lambda row: row["seed_id"]),
        "seed_candidate_mappings": sorted(links, key=lambda row: row["seed_id"]),
    }


def _deep_selection_records(deep: Mapping[str, Any]) -> list[dict[str, Any]]:
    frame = deep.get("deep_selection", {})
    selected = set(map(str, frame.get("selected_candidate_ids", [])))
    results = {
        str(row["candidate_id"]): dict(row)
        for row in deep.get("deep_results", [])
    }
    screen_only = {
        str(row["candidate_id"]): dict(row)
        for row in frame.get("screen_only", [])
    }
    rows: list[dict[str, Any]] = []
    for candidate in deep.get("screened_candidates", []):
        candidate_id = str(candidate["screened_candidate_id"])
        if candidate_id in selected:
            result = results.get(candidate_id, {})
            completion = str(result.get("status", "deep_failed"))
            if completion not in {"deep", "deep_quarantined", "deep_failed"}:
                completion = "deep_failed"
            selection = "selected_deep"
            reason = str(result.get("reason", "Selected deep result is missing."))
        else:
            row = screen_only.get(candidate_id, {})
            selection = "screen_only"
            completion = "not_selected"
            reason = str(row.get("reason", "Retained outside the frozen deep capacity."))
        projection = {
            "screened_candidate_id": candidate_id,
            "selection_disposition": selection,
            "completion_disposition": completion,
            "reason": reason,
            "rule_version": frame.get("selection_policy_version", ""),
        }
        rows.append(
            {
                "selection_record_id": _stable_id("DEEP-SELECTION-RECORD", projection),
                **projection,
            }
        )
    return sorted(rows, key=lambda row: row["screened_candidate_id"])


def compose_canonical_collections(
    case: CaseRevision,
    discovery: Mapping[str, Any],
    disposition: Mapping[str, Any],
    deep: Mapping[str, Any],
    portfolio: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Project the four persisted production aggregates into canonical runtime ledgers."""

    query_plans = [dict(row["query_plan"]) for row in discovery.get("branches", [])]
    receipts_by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in discovery.get("retrieval_content_receipts", []):
        receipts_by_plan[str(receipt.get("query_plan_id", ""))].append(dict(receipt))
    proofs: list[dict[str, Any]] = []
    universe_by_id = {
        str(row["source_universe_id"]): dict(row)
        for row in discovery.get("source_universes", [])
    }
    for branch in discovery.get("branches", []):
        plan_id = str(branch["query_plan_id"])
        universe = universe_by_id.get(str(branch["source_universe_id"]), {})
        proofs.append(
            {
                **dict(branch),
                "content_receipts": sorted(
                    receipts_by_plan.get(plan_id, []),
                    key=lambda row: int(row.get("page_ordinal", 0)),
                ),
                "source_specific_limitations": list(universe.get("limitations", [])),
                "coverage_gaps": [
                    row for row in discovery.get("explicit_gaps", [])
                    if str(row.get("branch_id", "")) in {"", str(branch["branch_id"])}
                ],
            }
        )
    screen_projection = _screening_projection(disposition, deep)
    deep_selection = _deep_selection_records(deep)
    deep_wrappers = [dict(row) for row in deep.get("deep_packages", [])]
    review_items: list[dict[str, Any]] = []
    portfolio_reviews: list[dict[str, Any]] = []
    disposition_by_candidate = {
        str(row["candidate_id"]): dict(row)
        for row in portfolio.get("portfolio_dispositions", [])
    }
    rank_by_candidate = {
        str(row["candidate_id"]): dict(row)
        for row in portfolio.get("diversified_portfolio_ranking", [])
    }
    for candidate_id in sorted(disposition_by_candidate):
        disposition_row = disposition_by_candidate[candidate_id]
        review = {
            "review_item_id": _stable_id(
                "PORTFOLIO-REVIEW-ITEM",
                {"candidate_id": candidate_id, "audit_revision": portfolio.get("audit_revision")},
            ),
            "candidate_id": candidate_id,
            "audit_revision": portfolio.get("audit_revision"),
            "disposition": disposition_row["disposition"],
            "reason": disposition_row["reason"],
        }
        review_items.append(review)
        portfolio_reviews.append(
            {
                "portfolio_review_id": _stable_id("PORTFOLIO-REVIEW", review),
                **review,
                "rank_record": rank_by_candidate.get(candidate_id, {}),
            }
        )
    broad = {
        "snapshot_id": _stable_id(
            "BROAD-CASE-SNAPSHOT",
            {"case_revision_id": case.case_revision_id, "endpoints": [row.endpoint_id for row in case.endpoints]},
        ),
        "case_id": case.case_id,
        "case_revision_id": case.case_revision_id,
        "case_status": case.case_status.value,
        "endpoint_ids": sorted(row.endpoint_id for row in case.endpoints),
        "unresolved_direction_conflicts": [],
        "pharmacology_seed_emission_ids": [],
    }
    result: dict[str, list[dict[str, Any]]] = {
        "broad_case_model_snapshots": [broad],
        "source_universes": [dict(row) for row in discovery.get("source_universes", [])],
        "query_plans": query_plans,
        "coverage_proofs": proofs,
        "source_mappings": [dict(row) for row in discovery.get("source_mappings", [])],
        "discovery_routes": [dict(row) for row in discovery.get("discovery_routes", [])],
        "candidate_seeds": [dict(row) for row in discovery.get("seeds", [])],
        "seed_dispositions": [dict(row) for row in disposition.get("seed_dispositions", [])],
        "identity_resolutions": [dict(row) for row in disposition.get("identity_resolutions", [])],
        "normalized_interventions": [dict(row) for row in disposition.get("normalized_interventions", [])],
        "quarantined_seeds": screen_projection["quarantined_seeds"],
        "screening_decisions": screen_projection["screening_decisions"],
        "screen_records": [dict(row) for row in deep.get("screen_records", [])],
        "screened_candidates": [dict(row) for row in deep.get("screened_candidates", [])],
        "seed_candidate_mappings": screen_projection["seed_candidate_mappings"],
        "triage_dispositions": screen_projection["triage_dispositions"],
        "deep_selection_records": deep_selection,
        "deep_evidence_packages": [dict(row["package"]) for row in deep_wrappers],
        "deep_candidates": [dict(row["deep_candidate"]) for row in deep_wrappers],
        "structured_safety": [dict(row) for wrapper in deep_wrappers for row in wrapper.get("structured_safety", [])],
        "structured_exposure": [dict(row) for wrapper in deep_wrappers for row in wrapper.get("structured_exposure", [])],
        "decision_profiles": [dict(row) for row in deep.get("candidate_decision_profiles", [])],
        "ranking_preparation_records": [dict(row) for row in deep.get("ranking_preparation", [])],
        "audit_assignments": [dict(row) for row in portfolio.get("audit_assignments", [])],
        "audit_records": [dict(row) for row in portfolio.get("audit_records", [])],
        "audit_corrections": [dict(row) for row in portfolio.get("audit_corrections", [])],
        "portfolio_review_items": review_items,
        "council_records": [dict(row) for row in portfolio.get("council_records", [])],
        "portfolio_review_records": portfolio_reviews,
        "portfolio_rank_records": [dict(row) for row in portfolio.get("diversified_portfolio_ranking", [])],
    }
    return {
        name: sorted(rows, key=lambda row: canonical_sha256(row))
        for name, rows in result.items()
    }


def _records_by_role(
    role: str,
    input_refs: list[dict[str, Any]],
    collections: Mapping[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    input_ids = {str(row["record_id"]) for row in input_refs}
    if role == "case_model_constructor":
        return {"broad_case_model_snapshots": collections["broad_case_model_snapshots"]}
    if role == "source_universe_planner":
        return {
            "source_universes": collections["source_universes"],
            "query_plans": collections["query_plans"],
        }
    if role == "discovery_source_worker":
        proofs = [row for row in collections["coverage_proofs"] if str(row["query_plan_id"]) in input_ids]
        plan_ids = {str(row["query_plan_id"]) for row in proofs}
        routes = [row for row in collections["discovery_routes"] if str(row.get("query_id")) in plan_ids]
        mapping_ids = {str(row["source_mapping_id"]) for row in routes}
        mappings = [row for row in collections["source_mappings"] if str(row["mapping_id"]) in mapping_ids]
        seed_ids = {str(row["seed_id"]) for row in mappings}
        seeds = [row for row in collections["candidate_seeds"] if str(row["seed_id"]) in seed_ids]
        return {
            "coverage_proofs": proofs,
            "source_mappings": mappings,
            "discovery_routes": routes,
            "candidate_seeds": seeds,
        }
    if role == "identity_worker":
        resolutions = [row for row in collections["identity_resolutions"] if str(row["seed_id"]) in input_ids]
        dispositions = [row for row in collections["seed_dispositions"] if str(row["seed_id"]) in input_ids]
        normalized_ids = {str(row.get("normalized_intervention_id") or "") for row in dispositions}
        return {
            "identity_resolutions": resolutions,
            "seed_dispositions": dispositions,
            "normalized_interventions": [row for row in collections["normalized_interventions"] if str(row["normalized_intervention_id"]) in normalized_ids],
            "quarantined_seeds": [row for row in collections["quarantined_seeds"] if str(row["seed_id"]) in input_ids],
        }
    if role == "preliminary_triage_worker":
        dispositions = [row for row in collections["seed_dispositions"] if str(row["seed_disposition_id"]) in input_ids]
        seed_ids = {str(row["seed_id"]) for row in dispositions}
        candidate_ids = {
            str(row["screened_candidate_id"])
            for row in collections["seed_candidate_mappings"]
            if str(row["seed_id"]) in seed_ids
        }
        return {
            "screening_decisions": [row for row in collections["screening_decisions"] if str(row["seed_id"]) in seed_ids],
            "screen_records": [row for row in collections["screen_records"] if str(row["representative_seed_id"]) in seed_ids],
            "screened_candidates": [row for row in collections["screened_candidates"] if str(row["screened_candidate_id"]) in candidate_ids],
            "seed_candidate_mappings": [row for row in collections["seed_candidate_mappings"] if str(row["seed_id"]) in seed_ids],
            "triage_dispositions": [row for row in collections["triage_dispositions"] if str(row["seed_id"]) in seed_ids],
        }
    if role == "deep_evidence_worker":
        selected = [row for row in collections["deep_selection_records"] if str(row["screened_candidate_id"]) in input_ids]
        deep_ids = {
            str(row["screened_candidate_id"])
            for row in selected
            if row["completion_disposition"] == "deep"
        }
        deep_candidates = [row for row in collections["deep_candidates"] if str(row["candidate_id"]) in deep_ids]
        package_ids = {str(row["deep_evidence_package_id"]) for row in deep_candidates}
        safety_ids = {
            str(row.get("safety_record_id"))
            for wrapper in collections.get("_deep_wrappers", [])
            if str(wrapper.get("candidate_id")) in deep_ids
            for row in wrapper.get("structured_safety", [])
        }
        exposure_ids = {
            str(row.get("exposure_record_id"))
            for wrapper in collections.get("_deep_wrappers", [])
            if str(wrapper.get("candidate_id")) in deep_ids
            for row in wrapper.get("structured_exposure", [])
        }
        return {
            "deep_selection_records": selected,
            "deep_evidence_packages": [row for row in collections["deep_evidence_packages"] if str(row["package_id"]) in package_ids],
            "deep_candidates": deep_candidates,
            "structured_safety": [row for row in collections["structured_safety"] if str(row["safety_record_id"]) in safety_ids],
            "structured_exposure": [row for row in collections["structured_exposure"] if str(row["exposure_record_id"]) in exposure_ids],
        }
    if role == "ranking_preparation_worker":
        profiles = [row for row in collections["decision_profiles"] if str(row["candidate_id"]) in input_ids]
        return {
            "decision_profiles": profiles,
            "ranking_preparation_records": [row for row in collections["ranking_preparation_records"] if str(row["candidate_id"]) in input_ids],
        }
    if role == "audit_sampling_worker":
        candidate_ids = {
            str(row["candidate_id"])
            for row in collections["ranking_preparation_records"]
            if str(row["preparation_id"]) in input_ids
        }
        all_preparations = sorted(str(row["preparation_id"]) for row in collections["ranking_preparation_records"])
        include_uncoupled = bool(all_preparations and all_preparations[0] in input_ids)
        assignments = [
            row for row in collections["audit_assignments"]
            if str(row.get("candidate_id") or "") in candidate_ids
            or (include_uncoupled and not row.get("candidate_id"))
        ]
        return {"audit_assignments": assignments}
    if role == "candidate_auditor":
        audits = [row for row in collections["audit_records"] if str(row["assignment_id"]) in input_ids]
        corrections = [row for row in collections["audit_corrections"] if str(row["assignment_id"]) in input_ids]
        candidate_ids = {str(row.get("candidate_id") or "") for row in audits} - {""}
        review_items = [row for row in collections["portfolio_review_items"] if str(row["candidate_id"]) in candidate_ids]
        return {
            "audit_records": audits,
            "audit_corrections": corrections,
            "portfolio_review_items": review_items,
        }
    if role == "council_portfolio_reviewer":
        candidates = {
            str(row["candidate_id"])
            for row in collections["portfolio_review_items"]
            if str(row["review_item_id"]) in input_ids
        }
        return {
            "council_records": [row for row in collections["council_records"] if str(row.get("candidate_id")) in candidates],
            "portfolio_review_records": [row for row in collections["portfolio_review_records"] if str(row["candidate_id"]) in candidates],
            "portfolio_rank_records": [row for row in collections["portfolio_rank_records"] if str(row["candidate_id"]) in candidates],
        }
    raise ProgramAggregateError(f"No production dispatcher exists for role {role}")


def _drive_runtime(
    root: Path,
    collections: dict[str, list[dict[str, Any]]],
    deep: Mapping[str, Any],
) -> None:
    collections = dict(collections)
    collections["_deep_wrappers"] = [dict(row) for row in deep.get("deep_packages", [])]
    agent_number = 0
    while True:
        action = next_action(root)
        if action["action"] == "complete":
            return
        if action["action"] != "start_agents":
            raise ProgramAggregateError(f"Production programme runtime stalled: {action}")
        for job_action in action["jobs"]:
            agent_number += 1
            role = str(job_action["role"])
            attempt = start_job(root, job_action["job_id"], f"program-{agent_number:04d}")
            plan = _read_json(root / RUNTIME_DIRECTORY / "execution_plan.json")
            jobs = {str(row["job_id"]): row for row in plan["jobs"]}
            job = jobs[str(job_action["job_id"])]
            input_refs = [dict(row) for row in job.get("input_refs", [])]
            if role == "final_structural_validator":
                snapshot = load_committed_snapshot(root)
                snapshot["output_status"] = "complete"
                issues = validate_snapshot(snapshot)
                if issues:
                    raise ProgramAggregateError(
                        "Final canonical-ledger validation failed: "
                        + "; ".join(row.render() for row in issues)
                    )
                records = {
                    "validation_reports": [
                        {
                            "validation_report_id": _stable_id(
                                "VALIDATION-REPORT",
                                {"snapshot_id": snapshot["snapshot_id"], "issues": []},
                            ),
                            "snapshot_id": snapshot["snapshot_id"],
                            "status": "pass",
                            "issue_count": 0,
                            "domains": [
                                "runtime",
                                "retrieval_coverage",
                                "case_endpoints",
                                "seeds_funnel",
                                "evidence",
                                "identity",
                                "ranking",
                                "audit_council",
                            ],
                        }
                    ]
                }
            elif role == "final_output_builder":
                snapshot = load_committed_snapshot(root)
                if snapshot.get("output_status") != "complete":
                    raise ProgramAggregateError("Final output stage requires a complete committed snapshot")
                manifest_path, _ = write_full_funnel_outputs(root, snapshot)
                records = {"output_manifests": [_read_json(manifest_path)]}
            else:
                records = _records_by_role(role, input_refs, collections)
            dependency_ids = sorted(
                str(jobs[dependency_id]["commit_id"])
                for dependency_id in job["dependency_job_ids"]
            )
            total = int(job.get("input_record_count", 0))
            usage = {
                "source_records": len(records.get("source_mappings", [])),
                "seeds": len(records.get("candidate_seeds", [])),
                "deep_reviews": len(records.get("deep_evidence_packages", [])),
                "audits": len(records.get("audit_records", [])),
                "elapsed_seconds": 0,
                "cost_units": 0,
            }
            result = {
                "schema_version": 7,
                "job_id": job["job_id"],
                "attempt_id": attempt["attempt_id"],
                "packet_hash": job["packet_hash"],
                "dependency_commit_ids": dependency_ids,
                "outcome": "completed",
                "shard_complete": True,
                "records": records,
                "progress": {
                    "processed_records": total,
                    "total_records": total,
                    "cursor": "",
                    "checkpoint_ref": "",
                },
                "budget_usage": usage,
            }
            result_path = root / str(attempt["expected_result_path"])
            _write_once(result_path, result)
            complete_job(root, job["job_id"])


class V7ProgramAdapter:
    """Controller-owned composition of all eight persisted schema-v7 stages."""

    def __init__(self, run_root: str | Path, runtime_config: Mapping[str, Any] | None = None) -> None:
        self.run_root = Path(run_root).expanduser().resolve()
        self.runtime_config = dict(runtime_config or {})

    @property
    def program_root(self) -> Path:
        return self.run_root / RUNTIME_DIRECTORY / PROGRAM_DIRECTORY

    @property
    def manifest_path(self) -> Path:
        return self.program_root / "program_manifest.json"

    def execute(
        self,
        case_revision: CaseRevision,
        source_plan: Mapping[str, Any],
        frozen_pages: Mapping[str, Any],
        resolver_assertions: Mapping[str, Any] | InputFactory,
        frozen_evidence: Mapping[str, Any] | InputFactory,
        frozen_audit_plan: Mapping[str, Any] | InputFactory,
    ) -> Mapping[str, Any]:
        if not is_v7_case_container(self.run_root):
            raise ProgramAggregateError("Production programme requires a native schema-v7 case container")
        validate_case_revision(case_revision)
        stored_case = _read_json(self.run_root / "case_revision.json")
        if stored_case.get("case_revision_id") != case_revision.case_revision_id:
            raise ProgramAggregateConflictError("Supplied case revision does not match the run container")
        if not is_v7_runtime(self.run_root):
            initialize_runtime(self.run_root, self.runtime_config)

        production = self.program_root
        discovery = dict(
            V7DiscoveryAdapter(production / "discovery").retrieve_and_seed(
                case_revision,
                source_plan,
                frozen_pages,
            )
        )
        validate_discovery_aggregate(case_revision, discovery)
        if not discovery.get("closure", {}).get("all_declared_branches_complete"):
            raise ProgramAggregateError("A complete programme requires every declared source branch to close")

        resolver = _factory_value("resolver_assertions", resolver_assertions, case_revision, discovery)
        disposition = dict(
            V7DispositionAdapter(production / "disposition").normalize_and_dispose(
                case_revision,
                discovery["seeds"],
                resolver,
            )
        )
        validate_disposition_aggregate(case_revision, disposition)
        if not disposition.get("stage_gate_passed"):
            raise ProgramAggregateError("Stage 4 disposition/identity gate did not pass")

        evidence = _factory_value("frozen_evidence", frozen_evidence, case_revision, disposition)
        deep = dict(
            V7ScreenDeepAdapter(production / "screen_deep").screen_and_deepen(
                case_revision,
                disposition,
                evidence,
            )
        )
        validate_screen_deep_aggregate(case_revision, deep)
        if not deep.get("stage_gate_passed"):
            raise ProgramAggregateError("Stage 5-6 screen/deep gate did not pass")

        audit = _factory_value("frozen_audit_plan", frozen_audit_plan, case_revision, deep)
        portfolio = dict(
            V7PortfolioAdapter(production / "portfolio").audit_and_select(
                case_revision,
                deep,
                audit,
            )
        )
        validate_portfolio_aggregate(case_revision, portfolio)
        if not portfolio.get("stage_gate_passed"):
            raise ProgramAggregateError("Stage 7 audit/portfolio gate did not pass")

        collections = compose_canonical_collections(
            case_revision,
            discovery,
            disposition,
            deep,
            portfolio,
        )
        _drive_runtime(self.run_root, collections, deep)
        runtime_errors = validate_runtime(self.run_root, final=True)
        if runtime_errors:
            raise ProgramAggregateError("Final runtime validation failed: " + "; ".join(runtime_errors))
        public_errors = validate_run(self.run_root, final=True)
        if public_errors:
            raise ProgramAggregateError("Final public validation failed: " + "; ".join(public_errors))
        snapshot = load_committed_snapshot(self.run_root)
        manifest = {
            "schema_version": 7,
            "model_version": PROGRAM_MODEL_VERSION,
            "program_id": _stable_id(
                "PRODUCTION-PROGRAM",
                {
                    "case_revision_id": case_revision.case_revision_id,
                    "discovery_aggregate_id": discovery["aggregate_id"],
                    "disposition_aggregate_id": disposition["aggregate_id"],
                    "screen_deep_aggregate_id": deep["aggregate_id"],
                    "portfolio_aggregate_id": portfolio["aggregate_id"],
                    "snapshot_id": snapshot["snapshot_id"],
                },
            ),
            "case_id": case_revision.case_id,
            "case_revision_id": case_revision.case_revision_id,
            "stage_status": {str(index): "pass" for index in range(1, 9)},
            "stage_aggregate_ids": {
                "discovery": discovery["aggregate_id"],
                "disposition": disposition["aggregate_id"],
                "screen_deep": deep["aggregate_id"],
                "audit_portfolio": portfolio["aggregate_id"],
            },
            "canonical_snapshot_id": snapshot["snapshot_id"],
            "output_manifest_id": _read_json(
                self.run_root / "outputs_v7" / "artifact_manifest.json"
            )["output_manifest_id"],
            "reconciliation": {
                "discovery": discovery["reconciliation"],
                "disposition": disposition["reconciliation"],
                "screen_deep": deep["reconciliation"],
                "audit_portfolio": portfolio["reconciliation"],
            },
            "closure_statement": discovery["closure"]["statement"],
            "hypothesis_generation_only": True,
            "experimental_use": True,
            "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
            "runtime_status": runtime_status(self.run_root)["state"]["status"],
        }
        _write_once(self.manifest_path, manifest)
        return manifest


__all__ = [
    "PROGRAM_DIRECTORY",
    "EXPERIMENTAL_USE_POLICY",
    "PROGRAM_MODEL_VERSION",
    "ProgramAggregateConflictError",
    "ProgramAggregateError",
    "V7ProgramAdapter",
    "compose_canonical_collections",
]
