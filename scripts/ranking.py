#!/usr/bin/env python3
"""Deterministic human-therapeutic scoring, caps, ranking, and council selection."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from program_contract import (
    AUDIT_VERDICTS,
    CANDIDATE_CLASSES,
    COUNCIL_TOP_N,
    COMPOUND_ORIGINS,
    HUMAN_EVIDENCE_LEVELS,
    NESTED_SCHEMAS,
    RANK_SECTION_BY_CLASS,
    RANK_SECTION_ORDER,
    RANKING_CAPS,
    RANKING_COMPONENTS,
    RANKING_VERSION,
    REPURPOSING_READINESS_MAX,
    SUPPORTIVE_ENDPOINT_TYPES,
    TARGET_ENDPOINT_TYPES,
)
from program_io import index_rows, read_jsonl, write_jsonl


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source_ids_exist(values: Any, sources: set[str]) -> bool:
    return (
        isinstance(values, list)
        and bool(values)
        and len(values) == len(set(str(value) for value in values))
        and {str(value) for value in values}.issubset(sources)
    )


def _score_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Order evidence strength without letting a cap hide the raw-score tie break."""
    return (
        -int(row["total_score"]),
        -int(row["raw_score"]),
        -(row["repurposing_readiness"]["score"] if isinstance(row["repurposing_readiness"]["score"], int) else -1),
        -int(row["score_components"]["human_evidence"]["score"]),
        -int(row["score_components"]["safety_tolerability"]["score"]),
        str(row["canonical_name"]).casefold(),
        str(row["candidate_id"]),
    )


def _assign_section_ranks(candidates: list[dict[str, Any]]) -> None:
    section_counts: dict[str, int] = {}
    endpoint_counts: dict[tuple[str, str, str], int] = {}
    for candidate in candidates:
        section = str(candidate["rank_section"])
        section_counts[section] = section_counts.get(section, 0) + 1
        candidate["rank"] = section_counts[section]
        endpoint = candidate["target_endpoint"]
        endpoint_key = (
            section,
            str(endpoint["endpoint_type"]),
            " ".join(str(endpoint["label"]).casefold().split()),
        )
        endpoint_counts[endpoint_key] = endpoint_counts.get(endpoint_key, 0) + 1
        candidate["endpoint_rank"] = endpoint_counts[endpoint_key]


def rank_rows(
    candidate_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate and rank detached rows without writing runtime state."""
    candidates = deepcopy(candidate_rows)
    sources = set(index_rows(source_rows, "source_id"))
    claims = index_rows(claim_rows, "claim_id")
    edges = index_rows(edge_rows or [], "edge_id")
    audits: dict[str, dict[str, Any]] = {}
    duplicate_audit_claims: set[str] = set()
    for row in audit_rows:
        if row.get("subject_type") != "claim":
            continue
        claim_id = str(row.get("subject_id"))
        if claim_id in audits:
            duplicate_audit_claims.add(claim_id)
        audits[claim_id] = row
    errors: list[str] = []
    if duplicate_audit_claims:
        errors.append(f"claims have multiple audit records: {sorted(duplicate_audit_claims)}")
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id", "<missing>"))
        decisive_ids = [str(value) for value in _items(candidate.get("decisive_claim_ids"))]
        candidate_class = str(candidate.get("candidate_class", ""))
        if candidate_class not in CANDIDATE_CLASSES:
            errors.append(f"candidate {candidate_id}: invalid candidate_class")
        if candidate.get("compound_origin") not in COMPOUND_ORIGINS:
            errors.append(f"candidate {candidate_id}: invalid compound_origin")
        if not _source_ids_exist(candidate.get("candidate_class_source_ids"), sources):
            errors.append(f"candidate {candidate_id}: candidate_class_source_ids must be nonempty, unique, and resolve")

        endpoint = candidate.get("target_endpoint")
        endpoint_claim_ids: list[str] = []
        if not isinstance(endpoint, dict) or set(endpoint) != set(NESTED_SCHEMAS["target_endpoint"]):
            errors.append(f"candidate {candidate_id}: target_endpoint fields do not match the schema")
        else:
            if endpoint.get("endpoint_type") not in TARGET_ENDPOINT_TYPES:
                errors.append(f"candidate {candidate_id}: invalid target endpoint type")
            if (
                candidate_class == "supportive_standard_care"
                and endpoint.get("endpoint_type") not in SUPPORTIVE_ENDPOINT_TYPES
            ):
                errors.append(f"candidate {candidate_id}: supportive standard care needs a supportive endpoint")
            if not str(endpoint.get("label", "")).strip():
                errors.append(f"candidate {candidate_id}: target endpoint label is required")
            endpoint_claim_ids = [str(value) for value in _items(endpoint.get("claim_ids"))]
            if (
                not endpoint_claim_ids
                or len(endpoint_claim_ids) != len(set(endpoint_claim_ids))
                or not set(endpoint_claim_ids).issubset(decisive_ids)
            ):
                errors.append(f"candidate {candidate_id}: target endpoint claim_ids must be unique decisive claims")
            if not _source_ids_exist(endpoint.get("source_ids"), sources):
                errors.append(f"candidate {candidate_id}: target endpoint source_ids must be nonempty, unique, and resolve")
            else:
                endpoint_claim_sources = {
                    str(source_id)
                    for claim_id in endpoint_claim_ids
                    for source_id in _items(claims.get(claim_id, {}).get("source_ids"))
                }
                if not {str(value) for value in endpoint.get("source_ids", [])}.issubset(endpoint_claim_sources):
                    errors.append(f"candidate {candidate_id}: target endpoint sources must support endpoint claims")

        readiness = candidate.get("repurposing_readiness")
        if not isinstance(readiness, dict) or set(readiness) != set(NESTED_SCHEMAS["repurposing_readiness"]):
            errors.append(f"candidate {candidate_id}: repurposing_readiness fields do not match the schema")
        else:
            readiness_score = readiness.get("score")
            if candidate_class == "repurposing_candidate":
                if (
                    isinstance(readiness_score, bool)
                    or not isinstance(readiness_score, int)
                    or not 0 <= readiness_score <= REPURPOSING_READINESS_MAX
                ):
                    errors.append(
                        f"candidate {candidate_id}: repurposing_readiness score must be 0-{REPURPOSING_READINESS_MAX}"
                    )
            elif readiness_score is not None:
                errors.append(f"candidate {candidate_id}: non-repurposing readiness score must be null")
            if not str(readiness.get("rationale", "")).strip():
                errors.append(f"candidate {candidate_id}: repurposing_readiness rationale is required")
            if not _source_ids_exist(readiness.get("source_ids"), sources):
                errors.append(f"candidate {candidate_id}: repurposing_readiness source_ids must be nonempty, unique, and resolve")

        candidate_evidence_sources = {
            str(source_id)
            for claim_id in decisive_ids
            for source_id in _items(claims.get(claim_id, {}).get("source_ids"))
        }
        candidate_evidence_sources.update(
            str(value) for value in _items(candidate.get("candidate_class_source_ids"))
        )
        if isinstance(endpoint, dict):
            candidate_evidence_sources.update(str(value) for value in _items(endpoint.get("source_ids")))

        components = candidate.get("score_components")
        if not isinstance(components, dict) or set(components) != set(RANKING_COMPONENTS):
            errors.append(f"candidate {candidate_id}: score_components must contain the exact rubric")
            continue
        raw_score = 0
        for name, maximum in RANKING_COMPONENTS.items():
            component = components.get(name)
            label = f"candidate {candidate_id} component {name}"
            if not isinstance(component, dict):
                errors.append(f"{label}: expected an object")
                continue
            if set(component) != set(NESTED_SCHEMAS["score_component"]):
                errors.append(f"{label}: fields must exactly match the score-component schema")
            score = component.get("score")
            if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= maximum:
                errors.append(f"{label}: score must be an integer from 0 to {maximum}")
            else:
                raw_score += score
            if not str(component.get("rationale", "")).strip():
                errors.append(f"{label}: rationale is required")
            if not _source_ids_exist(component.get("source_ids"), sources):
                errors.append(f"{label}: source_ids must be nonempty, unique, and resolve")
            elif not {str(value) for value in component.get("source_ids", [])}.issubset(candidate_evidence_sources):
                errors.append(f"{label}: source_ids must be candidate-evidence linked")

        distinct_evidence_sources = {
            str(source_id)
            for claim_id in decisive_ids
            for source_id in _items(claims.get(claim_id, {}).get("source_ids"))
        }
        class_sources = {str(value) for value in _items(candidate.get("candidate_class_source_ids"))}
        readiness_sources = {
            str(value) for value in _items(readiness.get("source_ids"))
        } if isinstance(readiness, dict) else set()
        if not class_sources.issubset(distinct_evidence_sources):
            errors.append(f"candidate {candidate_id}: candidate-class sources must be decisively claimed and audited")
        if not readiness_sources.issubset(distinct_evidence_sources):
            errors.append(f"candidate {candidate_id}: readiness sources must be decisively claimed and audited")
        independence = components.get("evidence_independence", {}) if isinstance(components, dict) else {}
        independence_score = independence.get("score") if isinstance(independence, dict) else None
        if isinstance(independence_score, int) and not isinstance(independence_score, bool):
            maximum_independence = min(RANKING_COMPONENTS["evidence_independence"], len(distinct_evidence_sources))
            if independence_score > maximum_independence:
                errors.append(
                    f"candidate {candidate_id}: evidence_independence exceeds {maximum_independence} "
                    "distinct decisive-claim sources"
                )

        caps = candidate.get("cap_assessments")
        if not isinstance(caps, dict) or set(caps) != set(RANKING_CAPS):
            errors.append(f"candidate {candidate_id}: cap_assessments must contain the exact cap rubric")
            continue
        applied: list[tuple[str, int]] = []
        for name, maximum in RANKING_CAPS.items():
            assessment = caps.get(name)
            label = f"candidate {candidate_id} cap {name}"
            if not isinstance(assessment, dict) or not isinstance(assessment.get("applies"), bool):
                errors.append(f"{label}: applies must be boolean")
                continue
            if set(assessment) != set(NESTED_SCHEMAS["cap_assessment"]):
                errors.append(f"{label}: fields must exactly match the cap-assessment schema")
            if not str(assessment.get("rationale", "")).strip():
                errors.append(f"{label}: rationale is required")
            if not _source_ids_exist(assessment.get("source_ids"), sources):
                errors.append(f"{label}: source_ids must be nonempty, unique, and resolve")
            elif not {str(value) for value in assessment.get("source_ids", [])}.issubset(candidate_evidence_sources):
                errors.append(f"{label}: source_ids must be candidate-evidence linked")
            if assessment.get("applies") is True:
                applied.append((name, maximum))

        decisive_audits = [audits.get(claim_id) for claim_id in decisive_ids]
        if not decisive_ids or any(audit is None for audit in decisive_audits):
            errors.append(f"candidate {candidate_id}: every decisive claim requires an independent audit")
        else:
            verdicts = {str(audit.get("verdict")) for audit in decisive_audits if audit}
            if not verdicts.issubset(AUDIT_VERDICTS):
                errors.append(f"candidate {candidate_id}: invalid decisive-claim audit verdict")
            has_human_endpoint_evidence = any(
                claims.get(claim_id, {}).get("human_relevance") in HUMAN_EVIDENCE_LEVELS
                and claims.get(claim_id, {}).get("direction") in {"supports_benefit", "qualifies_benefit"}
                and claims.get(claim_id, {}).get("calibration") not in {"unresolved", "contradicted"}
                and audits.get(claim_id, {}).get("verdict") in {"supported", "qualified"}
                for claim_id in endpoint_claim_ids
            )
            absent_cap = caps.get("absent_human_evidence", {}).get("applies") is True
            if absent_cap != (not has_human_endpoint_evidence):
                requirement = "apply" if not has_human_endpoint_evidence else "not apply"
                errors.append(f"candidate {candidate_id}: absent human evidence cap must {requirement}")
            unresolved = any(
                audits.get(claim_id, {}).get("verdict") in {"unsupported", "contradicted", "unresolved"}
                or claims.get(claim_id, {}).get("direction") in {"opposes_benefit", "unclear"}
                or claims.get(claim_id, {}).get("calibration") in {"unresolved", "contradicted"}
                for claim_id in decisive_ids
            )
            unresolved = unresolved or any(
                edges.get(str(edge_id), {}).get("directionality") == "ambiguous"
                for path in _items(candidate.get("causal_paths"))
                if isinstance(path, dict)
                for edge_id in _items(path.get("edge_ids"))
            )
            unresolved_cap = caps.get("unresolved_direction", {}).get("applies") is True
            if unresolved_cap != unresolved:
                requirement = "apply" if unresolved else "not apply"
                errors.append(f"candidate {candidate_id}: unresolved direction cap must {requirement}")

        model = candidate.get("experimental_model_suitability")
        if not isinstance(model, dict) or not isinstance(model.get("assessed"), bool):
            errors.append(f"candidate {candidate_id}: experimental_model_suitability is malformed")
        else:
            if set(model) != set(NESTED_SCHEMAS["experimental_model_suitability"]):
                errors.append(f"candidate {candidate_id}: model-suitability fields do not match the schema")
            if not str(model.get("rationale", "")).strip():
                errors.append(f"candidate {candidate_id}: model-suitability rationale is required")
            if model.get("assessed") is True:
                score = model.get("score")
                if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
                    errors.append(f"candidate {candidate_id}: model-suitability score must be 0-100")
                if not _source_ids_exist(model.get("source_ids"), sources):
                    errors.append(f"candidate {candidate_id}: assessed model suitability needs source IDs")
            elif model.get("score") is not None:
                errors.append(f"candidate {candidate_id}: unassessed model suitability must use score=null")

        cap_value = min((value for _, value in applied), default=100)
        candidate["raw_score"] = raw_score
        candidate["applied_cap"] = {
            "maximum": cap_value,
            "reasons": sorted(name for name, value in applied if value == cap_value),
        }
        candidate["total_score"] = min(raw_score, cap_value)
        candidate["ranking_version"] = RANKING_VERSION
        candidate["rank_section"] = RANK_SECTION_BY_CLASS.get(candidate_class, "")
    if errors:
        raise ValueError("Ranking validation failed:\n" + "\n".join(f"- {error}" for error in errors))
    candidates.sort(
        key=lambda row: (RANK_SECTION_ORDER.index(str(row["rank_section"])), *_score_key(row))
    )
    _assign_section_ranks(candidates)
    return candidates


def rank_candidates(root: str | Path, *, persist: bool = True) -> list[dict[str, Any]]:
    run_root = Path(root).expanduser().resolve()
    candidates = rank_rows(
        read_jsonl(run_root / "candidate_records.jsonl"),
        read_jsonl(run_root / "source_corpus.jsonl"),
        read_jsonl(run_root / "claim_ledger.jsonl"),
        read_jsonl(run_root / "audit_records.jsonl"),
        read_jsonl(run_root / "evidence_graph.jsonl"),
    )
    if persist:
        write_jsonl(run_root / "candidate_records.jsonl", candidates)
    return candidates


def council_selection(candidates: list[dict[str, Any]]) -> list[str]:
    primary = [row for row in candidates if row.get("rank_section") == "primary_repurposing"]
    primary_leaders = {str(row["candidate_id"]) for row in primary[:COUNCIL_TOP_N]}
    therapeutic_leaders = {
        str(row["candidate_id"])
        for row in sorted(candidates, key=_score_key)[:COUNCIL_TOP_N]
    }
    conflicts = {
        str(row["candidate_id"])
        for row in candidates
        if _items(row.get("material_conflicts")) or row.get("audit_status") == "conflicted"
    }
    selected = primary_leaders | therapeutic_leaders | conflicts
    return [str(row["candidate_id"]) for row in candidates if str(row["candidate_id"]) in selected]
