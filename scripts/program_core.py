#!/usr/bin/env python3
"""Small, content-addressed controller for a linear repurposing programme."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable, Mapping

from repurposing_program.audit import (
    _accepted_ids,
    _assessment_source_uses,
    _component_score,
    _validate_candidate_audit,
    _validate_source_integrity,
)
from repurposing_program.bibliography import (
    _batches,
    _bibliographic_get,
    _bibliographic_request,
    _canonicalize_documents,
    _doi_metadata,
    _id_converter_records,
    _ncbi_summaries,
    _normalized_publication_id,
    _resolve_bibliographic_metadata,
    _summary_metadata,
    _validate_bibliographic_documents,
)
from repurposing_program.candidates import (
    _review_batches,
    _validate_cited_entries,
    _validate_review_item,
    _validate_seed_item,
    _validate_string_list,
)
from repurposing_program.contracts import (
    AUDIT_EXCLUSION_POLICY,
    AUDIT_EXCLUSION_REASONS,
    CANONICAL_DOCUMENT_ID,
    EXPERIMENTAL_USE_POLICY,
    FIELD_RULES,
    GRAPH_INDEX_FIELDS,
    MAX_SCORE,
    OBJECTIVE,
    PATHOLOGY_PROFILE_LIST_FIELDS,
    PRIOR_ART_STATUSES,
    ROW_SCHEMAS,
    SCORE_COMPONENT_RUBRIC,
    SCORE_COMPONENTS,
    SCORE_LABELS,
    SCORE_RUBRIC,
    SCORE_VALUES,
    STAGE_GUIDANCE,
    STAGES,
    _CITATION_FIELDS,
    _COMPARATORS,
    _PATHOLOGY_FORBIDDEN_KEYS,
    _PUBLICATION_ID,
    _PUBLICATION_ID_PATTERN,
    _RESEARCH_CONTEXT_SECTIONS,
    _SECRET_KEYS,
    _SOURCE_CHECK_VERDICTS,
    _UNICHEM_API,
    _UNICHEM_SOURCE_IDS,
)
from repurposing_program.errors import ProgramError
from repurposing_program.evidence import (
    _all_documents,
    _cited_documents,
    _cited_ids,
    _document_has_inspectable_content,
    _find,
    _merge_documents,
    _merge_text,
    _merge_unique,
    _normalized_title,
    _rows,
    _select_cited_documents,
    _source_index,
    _validate_research_document_content,
    _year,
)
from repurposing_program.graph import (
    _assemble_graph_result,
    _graph_index,
    _graph_node_context,
    _graph_support_ids,
    _merge_assertions,
)
from repurposing_program.identity import (
    _candidate_queries,
    _canonical_candidates,
    _empty_identity_result,
    _exact_identity_groups,
    _identity_candidate_options,
    _identity_queue,
    _merge_candidate_rows,
    _post_unichem,
    _query_key,
    _resolve_seed_identities,
    _unichem_request,
    _unichem_requests,
    _validate_candidate_identity,
)
from repurposing_program.orchestration import (
    _advance_controller,
    _build_graph_result,
    _build_review_result,
    _build_seed_result,
    _item_cited_documents,
    _item_collection,
    _item_gaps,
    _validate_result,
    next_action,
    submit,
)
from repurposing_program.packets import (
    _build_packet,
    _packet_context,
    _record_contract,
    _validate_packet,
)
from repurposing_program.pathology import (
    _canonical_source_records,
    _compact_disease_context,
    _curation_concepts,
    _forbidden_pathology_paths,
    _research_concepts,
    _validate_curation,
    _validate_pathology_item,
    _validate_source_adjudication,
    _validate_source_result,
    _validate_source_screening,
)
from repurposing_program.run_state import (
    _case,
    _first_missing,
    _item_ids,
    _item_results,
    _load_results,
    _program_status,
    _stop_reason,
    _verify_outputs,
    graph_context,
    initialize,
    status,
)
from repurposing_program.storage import (
    _canonical_bytes,
    _item_result_path,
    _item_token,
    _packet_path,
    _read_json,
    _result_path,
    _sha256,
    _stable_id,
    _submission_path,
    _write_json,
    _write_jsonl,
    _write_once,
)
from repurposing_program.validation import (
    _contract_rows,
    _ids,
    _references,
    _required,
    _secret_paths,
    _validate_documents,
    _validate_exact_object,
)


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _artifact(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {"filename": path.name, "bytes": len(payload), "sha256": _sha256(payload)}


def _final_score(row: Mapping[str, Any]) -> int:
    return sum(int(row["component_scores"][component]["value"]) for component in SCORE_COMPONENTS)


def _project_ranked_row(
    rank: int,
    row: Mapping[str, Any],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    candidate = candidates[row["candidate_id"]]
    projected = {
        "rank": rank,
        "candidate_id": row["candidate_id"],
        "name": candidate["name"],
        "identity_status": candidate["identity"]["status"],
        **{
            component: row["component_scores"][component]["value"]
            for component in SCORE_COMPONENTS
        },
        "final_score": _final_score(row),
        "net_assessment": row["net_assessment"]["text"],
        "source_ids": ";".join(sorted(map(str, row["net_assessment"]["source_ids"]))),
    }
    return projected


def _ranked_rows(
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    candidates = {row["candidate_id"]: row for row in _canonical_candidates(results)}
    assessments = _rows(results["candidate_audit"]["records"], "assessments")
    assessments.sort(key=lambda row: (-_final_score(row), str(row["candidate_id"])))
    rows: list[dict[str, Any]] = []
    rank = 0
    prior_score: int | None = None
    for assessment in assessments:
        score = _final_score(assessment)
        if score != prior_score:
            rank += 1
            prior_score = score
        rows.append(_project_ranked_row(rank, assessment, candidates))
    return rows, candidates


def _evidence_card_rows(
    ranked_rows: list[dict[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    assessments = {
        str(row["candidate_id"]): row
        for row in _rows(results["candidate_audit"]["records"], "assessments")
    }
    cards: list[dict[str, Any]] = []
    for ranked_row in ranked_rows:
        candidate_id = str(ranked_row["candidate_id"])
        assessment = assessments[candidate_id]
        aliases = [
            {
                "name": str(alias["name"]).strip(),
                "source_ids": sorted(set(map(str, alias["source_ids"]))),
            }
            for alias in assessment["aliases"]
        ]
        why_not = [
            {
                "finding": str(finding["finding"]).strip(),
                "source_ids": sorted(set(map(str, finding["source_ids"]))),
            }
            for finding in assessment["why_not"]
        ]
        cards.append(
            {
                "drug_id": candidate_id,
                "aliases": aliases,
                "score": _final_score(assessment),
                "components": {
                    component: {
                        "value": assessment["component_scores"][component]["value"],
                        "reason": str(
                            assessment["component_scores"][component]["reason"]
                        ).strip(),
                        "source_ids": sorted(set(map(
                            str, assessment["component_scores"][component]["source_ids"]
                        ))),
                    }
                    for component in SCORE_COMPONENTS
                },
                "why": {
                    "text": str(assessment["net_assessment"]["text"]).strip(),
                    "source_ids": sorted(set(map(
                        str, assessment["net_assessment"]["source_ids"]
                    ))),
                },
                "why_not": why_not,
                "source_integrity": assessment["source_integrity"],
            }
        )
    return cards


def _single_line(value: Any) -> str:
    return " ".join(str(value).split())


def _reference_line(source_ids: Iterable[Any]) -> str:
    return "References: " + ", ".join(sorted(set(map(str, source_ids))))


def _source_verification_summary(checks: Iterable[Mapping[str, Any]]) -> str:
    counts = {verdict: 0 for verdict in _SOURCE_CHECK_VERDICTS}
    total = 0
    for check in checks:
        verdict = str(check["verdict"])
        counts[verdict] += 1
        total += 1
    details = ", ".join(
        f"{counts[verdict]} {verdict.replace('_', ' ')}"
        for verdict in ("supports", "partly_supports", "does_not_support", "contradicts")
        if counts[verdict]
    )
    return f"{total} cited use{'s' if total != 1 else ''} checked ({details})"


def _cards_bytes(cards: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for card in cards:
        lines.extend([f"## {_single_line(card['drug_id'])}", ""])
        if card["aliases"]:
            lines.append("Aliases:")
            lines.extend(
                f"- {_single_line(alias['name'])} "
                f"({_reference_line(alias['source_ids'])})"
                for alias in card["aliases"]
            )
            lines.append("")
        lines.extend([f"Score: {card['score']}/{MAX_SCORE}", ""])
        lines.extend(
            [
                "Source verification: "
                f"{_source_verification_summary(card['source_integrity']['checks'])}",
                "",
            ]
        )
        exceptions = [
            check
            for check in card["source_integrity"]["checks"]
            if check["verdict"] != "supports"
        ]
        if exceptions:
            lines.append("Citation-audit exceptions:")
            lines.extend(
                f"- {_single_line(check['source_id'])} in {_single_line(check['scope'])}: "
                f"{str(check['verdict']).replace('_', ' ')} — "
                f"{_single_line(check['finding'])}"
                for check in exceptions
            )
            lines.append("")
        for component in SCORE_COMPONENTS:
            score = card["components"][component]
            lines.extend(
                [
                    f"- {SCORE_LABELS[component]}: {score['value']}/20 — "
                    f"{_single_line(score['reason'])}",
                    f"  {_reference_line(score['source_ids'])}",
                ]
            )
        lines.append("")
        lines.extend(
            [
                "### Why",
                "",
                _single_line(card["why"]["text"]),
                "",
                _reference_line(card["why"]["source_ids"]),
                "",
            ]
        )
        if card["why_not"]:
            lines.extend(["### Why not", ""])
            for finding in card["why_not"]:
                lines.extend(
                    [
                        f"- {_single_line(finding['finding'])}",
                        f"  {_reference_line(finding['source_ids'])}",
                    ]
                )
            lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _provenance_rows(
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    assertions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        candidate = candidates[row["candidate_id"]]
        node_ids = set(map(str, candidate["graph_node_ids"]))
        output.append(
            {
                "candidate_id": row["candidate_id"],
                "graph_node_ids": sorted(node_ids),
                "assertion_ids": sorted(
                    assertion["assertion_id"]
                    for assertion in assertions
                    if str(assertion["subject_id"]) in node_ids
                    or str(assertion["object_id"]) in node_ids
                ),
                "pathology_source_ids": sorted(map(str, candidate["pathology_source_ids"])),
                "mechanism_source_ids": sorted(map(str, candidate["mechanism_source_ids"])),
                "origin_concept_ids": sorted(
                    map(str, candidate.get("origin_concept_ids", []))
                ),
            }
        )
    return output


def _excluded_candidate_rows(
    results: Mapping[str, Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for exclusion in sorted(
        _rows(results["candidate_audit"]["records"], "excluded_candidates"),
        key=lambda row: str(row["candidate_id"]),
    ):
        candidate = candidates[str(exclusion["candidate_id"])]
        rows.append(
            {
                "candidate_id": exclusion["candidate_id"],
                "name": candidate["name"],
                "reason_code": exclusion["reason_code"],
                "finding": exclusion["finding"],
                "source_ids": sorted(set(map(str, exclusion["source_ids"]))),
                "graph_node_ids": sorted(set(map(str, candidate["graph_node_ids"]))),
                "pathology_source_ids": sorted(set(map(str, candidate["pathology_source_ids"]))),
                "mechanism_source_ids": sorted(set(map(str, candidate["mechanism_source_ids"]))),
                "source_integrity": exclusion["source_integrity"],
            }
        )
    return rows


def _write_output_files(
    run_root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    rows: list[dict[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
) -> list[Path]:
    outputs = run_root / "outputs"
    graph = results["evidence_graph"]["records"]
    _write_once(outputs / "candidates.csv", _csv_bytes(rows, list(rows[0])))
    card_rows = _evidence_card_rows(rows, results)
    _write_once(outputs / "candidate_cards.md", _cards_bytes(card_rows))
    excluded_rows = _excluded_candidate_rows(results, candidates)
    _write_jsonl(outputs / "candidate_exclusions.jsonl", excluded_rows)
    documents = sorted(
        _canonicalize_documents(
            run_root, _all_documents(results), verify_titles=False
        ),
        key=lambda row: row["document_id"],
    )
    _write_jsonl(outputs / "citations.jsonl", documents)
    assertions = _rows(graph, "assertions")
    _write_json(
        outputs / "graph.json",
        {
            "case_id": case["case_id"],
            "snapshot_id": results["evidence_graph"]["snapshot_id"],
            "nodes": _rows(graph, "source_nodes"),
            "source_edges": _rows(graph, "source_edges"),
            "disease_context": _rows(graph, "disease_context"),
            "profiles": _rows(graph, "profiles"),
            "assertions": assertions,
        },
    )
    _write_jsonl(
        outputs / "candidate_provenance.jsonl",
        _provenance_rows(rows, candidates, assertions),
    )
    gap_count = sum(len(results[stage].get("gaps", [])) for stage in STAGES)
    raw_candidate_count = len(
        _rows(results["candidate_seed_generation"]["records"], "candidates")
    )
    summary = (
        "# Repurposing programme summary\n\n"
        f"Disease: {case['disease']}\n\n"
        f"Gene: {case.get('gene') or 'not supplied'}\n\n"
        f"Pathology graph snapshot: {results['evidence_graph']['snapshot_id']}\n\n"
        f"Status: complete with {len(rows)} ranked candidate(s) and "
        f"{len(excluded_rows)} audited exclusion(s).\n\n"
        f"Sources: {len(documents)}; pathology nodes: {len(graph['profiles'])}; "
        f"assertions: {len(graph['assertions'])}; raw candidate seeds: "
        f"{raw_candidate_count}; deduplicated candidates: {len(candidates)}; "
        f"reported gaps: {gap_count}.\n\n"
        "Candidate nomination did not require a prior disease-drug literature association. "
        f"Audited candidates were ranked by an unweighted sum of "
        f"{len(SCORE_COMPONENTS)} 20-point components out of {MAX_SCORE}; "
        "exact-disease established use or human trials and other bounded decisive failures were "
        "exclusionary.\n\n"
        f"{EXPERIMENTAL_USE_POLICY}\n"
    )
    _write_once(outputs / "summary.md", summary.encode("utf-8"))
    return [
        outputs / "candidates.csv",
        outputs / "candidate_cards.md",
        outputs / "candidate_exclusions.jsonl",
        outputs / "citations.jsonl",
        outputs / "graph.json",
        outputs / "candidate_provenance.jsonl",
        outputs / "summary.md",
    ]


def build_outputs(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, results = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, results)
    if current["state"] == "complete":
        return _read_json(run_root / "outputs" / "manifest.json")
    if current["state"] != "ready_to_build":
        raise ProgramError(f"Outputs cannot be built while state is {current['state']}")
    rows, candidates = _ranked_rows(results)
    artifact_paths = _write_output_files(run_root, case, results, rows, candidates)
    manifest = {
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
        "stage_results": {
            stage: _sha256(_result_path(run_root, stage).read_bytes()) for stage in STAGES
        },
        "artifacts": [_artifact(path) for path in artifact_paths],
        "experimental_use_policy": EXPERIMENTAL_USE_POLICY,
    }
    _write_json(run_root / "outputs" / "manifest.json", manifest)
    return manifest


__all__ = [
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
]
