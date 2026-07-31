"""Linear workflow advancement, item aggregation, and result acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pathology_sources import SourceError, fetch_pathology_sources, screen_pathology_sources

from .audit import _validate_candidate_audit
from .bibliography import _canonicalize_documents, _validate_bibliographic_documents
from .candidates import _validate_review_item, _validate_seed_item
from .contracts import STAGE_GUIDANCE, STAGES
from .errors import ProgramError
from .evidence import (
    _all_documents,
    _cited_documents,
    _merge_documents,
    _merge_unique,
    _rows,
    _select_cited_documents,
    _validate_research_document_content,
)
from .graph import _assemble_graph_result
from .identity import (
    _empty_identity_result,
    _exact_identity_groups,
    _resolve_seed_identities,
    _validate_candidate_identity,
)
from .packets import _build_packet
from .pathology import (
    _validate_curation,
    _validate_pathology_item,
    _validate_source_adjudication,
    _validate_source_result,
    _validate_source_screening,
)
from .run_state import (
    _case,
    _item_ids,
    _item_results,
    _load_results,
    _program_status,
)
from .storage import (
    _item_result_path,
    _item_token,
    _packet_path,
    _read_json,
    _result_path,
    _stable_id,
    _submission_path,
    _write_json,
)
from .validation import _secret_paths


def next_action(root: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case = _case(run_root)
    for _ in range(len(STAGES) + 1):
        results = _load_results(run_root)
        current = _program_status(run_root, case, results)
        if current["state"] != "needs_controller":
            break
        _advance_controller(run_root, case, results, str(current["next_stage"]))
    else:
        raise ProgramError("Controller could not reach an agent or terminal state")
    if current["state"] != "needs_agent":
        return current
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, results, task, item_id)
    packet_path = _packet_path(run_root, task, item_id)
    result_path = _submission_path(run_root, task, item_id)
    display_item_id = (
        f"{task}/{item_id}/{_item_token(str(item_id))}"
        if item_id is not None
        else task
    )
    return {
        **current,
        "display_item_id": display_item_id,
        "packet_id": packet["packet_id"],
        "packet_path": str(packet_path),
        "suggested_result_path": str(result_path),
        "worker_prompt": (
            f"Complete {display_item_id}. Read only the content packet at {packet_path} and any "
            "controller-returned graph "
            f"context explicitly authorized by that packet. Complete the {task} task and write "
            f"one JSON object matching result_contract to {result_path}. Use this exact header: "
            f"stage={json.dumps(task)}, item_id={json.dumps(item_id)}, "
            f"packet_id={json.dumps(packet['packet_id'])}, status=\"complete\". "
            "Return the result path to the controller."
        ),
    }


def _item_collection(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
    collection: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _rows(accepted[item_id]["records"], collection)
    ]


def _item_cited_documents(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[dict[str, Any]]:
    item_ids = _item_ids(stage, results)
    accepted = _item_results(root, task, item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError(f"Cannot aggregate {stage} before every item is accepted")
    return [
        row
        for item_id in item_ids
        for row in _cited_documents(accepted[item_id]["records"])
    ]


def _item_gaps(
    root: Path,
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
    task: str,
) -> list[Any]:
    accepted = _item_results(root, task, _item_ids(stage, results))
    return [gap for result in accepted.values() for gap in result.get("gaps", [])]


def _build_graph_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    return _assemble_graph_result(
        results,
        _item_collection(
            root, results, "evidence_graph", "pathology_node_research", "profiles"
        ),
        _item_collection(
            root, results, "evidence_graph", "pathology_node_research", "assertions"
        ),
        _item_cited_documents(
            root, results, "evidence_graph", "pathology_node_research"
        ),
        _item_gaps(root, results, "evidence_graph", "pathology_node_research"),
    )


def _build_seed_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    item_ids = _item_ids("candidate_seed_generation", results)
    accepted = _item_results(root, "candidate_seed_research", item_ids)
    if len(accepted) != len(item_ids):
        raise ProgramError("Cannot aggregate seeds before every researched concept is accepted")
    raw_candidates = []
    for item_id in item_ids:
        for row in _rows(accepted[item_id]["records"], "candidates"):
            seed_id = _stable_id(
                "SEED",
                {"origin_concept_id": item_id, "candidate_id": row["candidate_id"]},
            )
            raw_candidates.append(
                {
                    **row,
                    "seed_id": seed_id,
                    "origin_concept_ids": [item_id],
                }
            )
    candidates, receipts = _resolve_seed_identities(root, raw_candidates)
    queued_count = sum(
        row["identity_resolution"]["status"] != "exact" for row in candidates
    )
    upstream_document_ids = {str(row["document_id"]) for row in _all_documents(results)}
    records = {
        "candidates": candidates,
        "identity_receipts": receipts,
        "exclusions": [
            {**row, "origin_concept_id": item_id}
            for item_id in item_ids
            for row in _rows(accepted[item_id]["records"], "exclusions")
        ],
    }
    records["documents"] = _select_cited_documents(
        _merge_documents(
            row
            for item_id in item_ids
            for row in _cited_documents(accepted[item_id]["records"])
            if str(row["document_id"]) not in upstream_document_ids
        ),
        records,
    )
    return {
        "stage": "candidate_seed_generation",
        "status": "complete",
        "graph_snapshot_id": results["evidence_graph"]["snapshot_id"],
        "records": records,
        "gaps": _item_gaps(
            root, results, "candidate_seed_generation", "candidate_seed_research"
        ),
        "notes": [
            f"Submitted {len(raw_candidates)} raw seeds to UniChem; "
            f"resolved {len(_exact_identity_groups(records))} exact identity group(s) and queued "
            f"{queued_count} seed(s) for identity review."
        ],
    }


def _build_review_result(
    root: Path, results: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    reviews = _merge_unique(
        _item_collection(
            root,
            results,
            "candidate_review",
            "candidate_evidence_review",
            "reviews",
        ),
        "candidate_id",
        "reviews",
    )
    upstream_document_ids = {str(row["document_id"]) for row in _all_documents(results)}
    return {
        "stage": "candidate_review",
        "status": "complete",
        "records": {
            "documents": _select_cited_documents(
                (
                    row
                    for row in _item_cited_documents(
                        root,
                        results,
                        "candidate_review",
                        "candidate_evidence_review",
                    )
                    if str(row["document_id"]) not in upstream_document_ids
                ),
                reviews,
            ),
            "reviews": reviews,
        },
        "gaps": _item_gaps(
            root, results, "candidate_review", "candidate_evidence_review"
        ),
        "notes": [],
    }


def _advance_controller(
    root: Path,
    case: Mapping[str, Any],
    results: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> None:
    if stage == "pathology_source_screening":
        try:
            result = screen_pathology_sources(
                root, str(case["disease"]), case.get("mondo")
            )
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        _validate_source_screening(result)
    elif stage == "pathology_source_adjudication":
        flagged = _rows(
            results["pathology_source_screening"]["records"],
            "flagged_sentences",
        )
        if flagged:
            raise ProgramError("Flagged source sentences require agent adjudication")
        result = {
            "stage": "pathology_source_adjudication",
            "status": "complete",
            "records": {"sentence_decisions": []},
            "gaps": [],
            "notes": ["No DisMech free-text sentences required adjudication."],
        }
    elif stage == "pathology_sources":
        decisions = {
            str(row["sentence_id"]): str(row["decision"])
            for row in _rows(
                results["pathology_source_adjudication"]["records"],
                "sentence_decisions",
            )
        }
        try:
            result = fetch_pathology_sources(
                root,
                str(case["disease"]),
                case.get("mondo"),
                decisions,
            )
        except SourceError as exc:
            raise ProgramError(str(exc)) from exc
        result["records"]["documents"] = _canonicalize_documents(
            root,
            _rows(result["records"], "documents"),
            verify_titles=False,
        )
        _validate_source_result(result)
    elif stage == "evidence_graph":
        result = _build_graph_result(root, results)
    elif stage == "candidate_seed_generation":
        result = _build_seed_result(root, results)
    elif stage == "candidate_identity":
        result = _empty_identity_result(results)
    elif stage == "candidate_review":
        result = _build_review_result(root, results)
    else:
        raise ProgramError(f"No controller action exists for stage: {stage}")
    _write_json(_result_path(root, stage), result)


def _validate_result(
    task: str,
    item_id: str | None,
    result: Mapping[str, Any],
    packet: Mapping[str, Any],
    prior: Mapping[str, Mapping[str, Any]],
) -> None:
    if (
        result.get("stage") != task
        or result.get("item_id") != item_id
        or result.get("packet_id") != packet.get("packet_id")
    ):
        raise ProgramError("Result stage or packet_id does not match the ready packet")
    if result.get("status") != "complete":
        raise ProgramError(
            "Only status=complete results become canonical; revise failed work and resubmit"
        )
    if not isinstance(result.get("records"), dict) or not isinstance(
        result.get("gaps"), list
    ):
        raise ProgramError("Result requires records object and gaps list")
    expected_collections = set(STAGE_GUIDANCE[task]["collections"])
    actual_collections = set(result["records"])
    if actual_collections != expected_collections:
        raise ProgramError(
            "Result records must contain exactly these collections: "
            f"{sorted(expected_collections)}"
        )
    if "notes" in result and not isinstance(result["notes"], list):
        raise ProgramError("Result notes must be a list when supplied")
    if "documents" in expected_collections:
        _validate_research_document_content(result["records"])
    secrets = _secret_paths(result)
    if secrets:
        raise ProgramError(f"Credentials must never be persisted in results: {secrets}")
    validators = {
        "pathology_source_adjudication": lambda: _validate_source_adjudication(
            result["records"], prior
        ),
        "pathology_curation": lambda: _validate_curation(result["records"], prior),
        "pathology_node_research": lambda: _validate_pathology_item(
            result["records"], str(item_id), prior
        ),
        "candidate_seed_research": lambda: _validate_seed_item(
            result["records"], str(item_id), prior
        ),
        "candidate_identity": lambda: _validate_candidate_identity(
            result["records"], prior
        ),
        "candidate_evidence_review": lambda: _validate_review_item(
            result["records"], str(item_id), prior
        ),
        "candidate_audit": lambda: _validate_candidate_audit(
            result["records"], prior, packet["context"]["source_index"]
        ),
    }
    validators[task]()


def submit(root: str | Path, result_path: str | Path) -> dict[str, Any]:
    run_root = Path(root).expanduser().resolve()
    case, prior = _case(run_root), _load_results(run_root)
    current = _program_status(run_root, case, prior)
    if current["state"] != "needs_agent":
        raise ProgramError(
            f"No agent result is ready for submission; state is {current['state']}"
        )
    task = str(current["next_task"])
    item_id = current.get("next_item_id")
    packet = _build_packet(run_root, case, prior, task, item_id)
    result = _read_json(Path(result_path).expanduser().resolve())
    _validate_result(task, item_id, result, packet, prior)
    if "documents" in STAGE_GUIDANCE[task]["collections"]:
        _validate_bibliographic_documents(run_root, result["records"])
    destination = (
        _item_result_path(run_root, task, str(item_id))
        if item_id is not None
        else _result_path(run_root, task)
    )
    _write_json(destination, result)
    return _program_status(run_root, case, _load_results(run_root))
