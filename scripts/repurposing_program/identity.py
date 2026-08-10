"""UniChem transport and deterministic candidate-identity resolution."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import _UNICHEM_API, _UNICHEM_SOURCE_IDS
from .errors import ProgramError
from .evidence import _merge_text, _rows
from .storage import _canonical_bytes, _read_json, _sha256, _stable_id, _write_json
from .validation import _contract_rows, _references, _required, _validate_documents

_UNICHEM_BATCH_SIZE = 10


class _UniChemBatchPending(RuntimeError):
    """Signal durable controller progress without reporting a source failure."""


def _candidate_queries(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    queries: set[tuple[str, int | None, str]] = set()
    identifiers = row.get("identifiers")
    if not isinstance(identifiers, Mapping):
        raise ProgramError("candidate.identifiers must be an object")
    for key, raw_value in identifiers.items():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        if not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ProgramError(
                f"candidate.identifiers.{key} must be a non-empty string or a non-empty "
                "list of non-empty strings"
            )
        for value in values:
            compound = value.strip()
            if key in {"inchi", "inchikey"}:
                queries.add((key, None, compound))
            elif key in _UNICHEM_SOURCE_IDS:
                queries.add(("sourceID", _UNICHEM_SOURCE_IDS[key], compound))
    return [
        {
            "compound": compound,
            "type": query_type,
            **({"sourceID": source_id} if source_id is not None else {}),
        }
        for query_type, source_id, compound in sorted(queries, key=str)
    ]


def _post_unichem(endpoint: str, body: Mapping[str, Any]) -> dict[str, Any]:
    payload = _canonical_bytes(body)
    request = Request(
        f"{_UNICHEM_API}/{endpoint}",
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "repurposing-research-program/4",
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8-sig"))
        except HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ProgramError(f"UniChem {endpoint} request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProgramError(f"UniChem {endpoint} returned invalid JSON: {exc}") from exc
        else:
            explicit_no_result = (
                endpoint == "compounds"
                and isinstance(result, dict)
                and result.get("response") == "Not found"
                and result.get("compounds") == []
            )
            if (
                not isinstance(result, dict)
                or result.get("response") != "Success"
            ) and not explicit_no_result:
                raise ProgramError(f"UniChem {endpoint} returned an invalid response")
            return result
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _unichem_path(root: Path, endpoint: str, body: Mapping[str, Any]) -> Path:
    token = _sha256(_canonical_bytes(body))[:24]
    return root / "sources" / "raw" / "unichem" / f"{endpoint}-{token}.json"


def _unichem_request(
    root: Path, endpoint: str, body: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _unichem_path(root, endpoint, body)
    if path.exists():
        response = _read_json(path)
    else:
        response = _post_unichem(endpoint, body)
        _write_json(path, response)
    return response, {
        "source": "UniChem",
        "api": _UNICHEM_API,
        "endpoint": endpoint,
        "query": dict(body),
        "raw_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path.read_bytes()),
    }


def _unichem_requests(
    root: Path, endpoint: str, bodies: Iterable[Mapping[str, Any]]
) -> dict[bytes, tuple[dict[str, Any], dict[str, Any]]]:
    unique = {_canonical_bytes(body): dict(body) for body in bodies}
    pending = [
        body for _, body in sorted(unique.items())
        if not _unichem_path(root, endpoint, body).exists()
    ]
    for body in pending[:_UNICHEM_BATCH_SIZE]:
        _unichem_request(root, endpoint, body)
    if len(pending) > _UNICHEM_BATCH_SIZE:
        raise _UniChemBatchPending(
            f"UniChem {endpoint}: cached {_UNICHEM_BATCH_SIZE} request(s); "
            f"{len(pending) - _UNICHEM_BATCH_SIZE} remain. Call next again."
        )
    return {
        key: _unichem_request(root, endpoint, body) for key, body in unique.items()
    }


def _query_key(query: Mapping[str, Any]) -> tuple[int, str] | None:
    if query.get("type") != "sourceID":
        return None
    return int(query["sourceID"]), str(query["compound"]).casefold()


def _resolve_seed_identities(
    root: Path, candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queries_by_seed = {
        str(row["seed_id"]): _candidate_queries(row) for row in candidates
    }
    exact = _unichem_requests(
        root, "compounds", (query for queries in queries_by_seed.values() for query in queries)
    )
    receipts = [value[1] for _, value in sorted(exact.items())]
    preliminary: dict[str, dict[str, Any]] = {}
    query_seeds: dict[tuple[int, str], set[str]] = {}
    for seed_id, queries in queries_by_seed.items():
        found: list[dict[str, Any]] = []
        missed = False
        for query in queries:
            response = exact[_canonical_bytes(query)][0]
            compounds = [row for row in response.get("compounds", []) if isinstance(row, dict)]
            found.extend(compounds)
            missed = missed or not compounds
            key = _query_key(query)
            if key:
                query_seeds.setdefault(key, set()).add(seed_id)
        ucis = {str(row.get("uci")) for row in found if row.get("uci") is not None}
        if not queries:
            preliminary[seed_id] = {"status": "not_queryable", "queries": []}
        elif not ucis:
            preliminary[seed_id] = {"status": "no_result", "queries": queries}
        elif len(ucis) != 1 or missed:
            preliminary[seed_id] = {
                "status": "conflicting_or_partial_result",
                "queries": queries,
                "ucis": sorted(ucis),
            }
        else:
            uci = next(iter(ucis))
            compound = next(row for row in found if str(row.get("uci")) == uci)
            preliminary[seed_id] = {
                "status": "exact",
                "queries": queries,
                "uci": uci,
                "standard_inchikey": compound.get("standardInchiKey"),
            }

    exact_seeds = {
        seed_id: row for seed_id, row in preliminary.items() if row["status"] == "exact"
    }
    connectivity_bodies = [
        {"compound": uci, "type": "uci", "searchComponents": True}
        for uci in sorted({row["uci"] for row in exact_seeds.values()})
    ]
    connectivity = _unichem_requests(root, "connectivity", connectivity_bodies)
    receipts.extend(value[1] for _, value in sorted(connectivity.items()))
    related: dict[str, set[str]] = {seed_id: set() for seed_id in exact_seeds}
    for body in connectivity_bodies:
        uci = str(body["compound"])
        response = connectivity[_canonical_bytes(body)][0]
        own = {seed_id for seed_id, row in exact_seeds.items() if row["uci"] == uci}
        for source in response.get("sources", []):
            key = (int(source.get("id", -1)), str(source.get("compoundId", "")).casefold())
            for other in query_seeds.get(key, set()) - own:
                if other in exact_seeds and exact_seeds[other]["uci"] != uci:
                    for seed_id in own:
                        related[seed_id].add(other)
                        related[other].add(seed_id)
    by_connectivity: dict[str, set[str]] = {}
    for seed_id, row in exact_seeds.items():
        inchikey = str(row.get("standard_inchikey") or "")
        if len(inchikey) >= 14:
            by_connectivity.setdefault(inchikey[:14], set()).add(seed_id)
    for seed_ids in by_connectivity.values():
        ucis = {exact_seeds[seed_id]["uci"] for seed_id in seed_ids}
        if len(ucis) > 1:
            for seed_id in seed_ids:
                related[seed_id].update(seed_ids - {seed_id})

    for seed_id, seed_related in related.items():
        if seed_related:
            preliminary[seed_id]["status"] = "connectivity_match"
            preliminary[seed_id]["related_seed_ids"] = sorted(seed_related)
    enriched = [
        {**row, "identity_resolution": preliminary[str(row["seed_id"])]}
        for row in candidates
    ]
    return enriched, sorted(receipts, key=lambda row: row["raw_path"])


def _identity_queue(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(records, "candidates")
        if row.get("identity_resolution", {}).get("status") != "exact"
    ]


def _exact_identity_groups(records: Mapping[str, Any]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for seed in _rows(records, "candidates"):
        resolution = seed.get("identity_resolution", {})
        if resolution.get("uci") is None:
            continue
        candidate_id = f"UNICHEM:{resolution['uci']}"
        groups.setdefault(candidate_id, []).append(str(seed["seed_id"]))
    return {candidate_id: sorted(groups[candidate_id]) for candidate_id in sorted(groups)}


def _identity_candidate_options(records: Mapping[str, Any]) -> list[dict[str, Any]]:
    seeds = {str(row["seed_id"]): row for row in _rows(records, "candidates")}
    queued_ids = {str(row["seed_id"]) for row in _identity_queue(records)}
    options: list[dict[str, Any]] = []
    for candidate_id, member_ids in _exact_identity_groups(records).items():
        rows = [seeds[seed_id] for seed_id in member_ids]
        queued_block = bool(set(member_ids) & queued_ids)
        options.append({
            "candidate_id": candidate_id,
            "option_type": (
                "queued_exact_block" if queued_block else "existing_resolved_candidate"
            ),
            "candidate_names": sorted(
                {str(row["name"]) for row in rows},
                key=lambda value: (value.casefold(), value),
            ),
            "asserted_candidate_ids": sorted({
                str(row["candidate_id"]) for row in rows
            }),
            "required_member_seed_ids": member_ids if queued_block else [],
        })
    return options


def _merge_identifiers(*identifier_sets: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, set[str]] = {}
    for identifiers in identifier_sets:
        for key, raw_value in identifiers.items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            merged.setdefault(str(key), set()).update(map(str, values))
    return {
        key: values[0] if len(values) == 1 else values
        for key in sorted(merged)
        if (values := sorted(merged[key]))
    }


def _merge_candidate_rows(
    rows: list[dict[str, Any]], candidate_id: str, identity: Mapping[str, Any]
) -> dict[str, Any]:
    rows = sorted(
        {str(row["seed_id"]): row for row in rows}.values(),
        key=lambda row: str(row["seed_id"]),
    )
    identity = {
        **identity,
        "identifiers": _merge_identifiers(
            *(row["identifiers"] for row in rows), identity["identifiers"]
        ),
    }
    return {
        "candidate_id": candidate_id,
        "name": str(identity["preferred_name"]),
        "identity": dict(identity),
        "mechanism_hypothesis": _merge_text(*(row["mechanism_hypothesis"] for row in rows)),
        "graph_node_ids": sorted({str(value) for row in rows for value in row["graph_node_ids"]}),
        "assertion_ids": sorted({str(value) for row in rows for value in row["assertion_ids"]}),
        "graph_rationale": _merge_text(*(row["graph_rationale"] for row in rows)),
        "pathology_source_ids": sorted({
            str(value) for row in rows for value in row["pathology_source_ids"]
        }),
        "mechanism_source_ids": sorted({
            str(value) for row in rows for value in row["mechanism_source_ids"]
        }),
        "origin_concept_ids": sorted({
            str(value) for row in rows for value in row["origin_concept_ids"]
        }),
        "member_seed_ids": [str(row["seed_id"]) for row in rows],
        "asserted_candidate_ids": sorted({str(row["candidate_id"]) for row in rows}),
    }


def _canonical_candidates(
    results: Mapping[str, Mapping[str, Any]],
    *,
    reviewed: bool = True,
) -> list[dict[str, Any]]:
    seed_records = results["candidate_seed_generation"]["records"]
    seeds = {str(row["seed_id"]): row for row in _rows(seed_records, "candidates")}
    queued = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    exact_groups = _exact_identity_groups(seed_records)
    candidates: dict[str, dict[str, Any]] = {}
    for candidate_id, member_ids in exact_groups.items():
        member_ids = set(member_ids)
        if member_ids & queued:
            continue
        rows = [seeds[seed_id] for seed_id in sorted(member_ids)]
        preferred_name = min(
            (str(row["name"]) for row in rows),
            key=lambda value: (value.casefold(), value),
        )
        identity = {
            "status": "resolved",
            "preferred_name": preferred_name,
            "identifiers": {"unichem_uci": candidate_id.split(":", 1)[1]},
        }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    if not reviewed:
        return [candidates[key] for key in sorted(candidates)]

    identity_result = results.get("candidate_identity", {"records": {"identity_groups": []}})
    for group in _rows(identity_result["records"], "identity_groups"):
        rows = [seeds[str(seed_id)] for seed_id in group["member_seed_ids"]]
        target = group.get("canonical_candidate_id")
        if target:
            exact = exact_groups[str(target)]
            rows.extend(seeds[seed_id] for seed_id in exact)
            identity = {
                "status": "resolved",
                "preferred_name": group["preferred_name"],
                "identifiers": _merge_identifiers(
                    group["identifiers"],
                    {"unichem_uci": str(target).split(":", 1)[1]},
                ),
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
            candidate_id = str(target)
        else:
            candidate_id = _stable_id("CANDIDATE", sorted(map(str, group["member_seed_ids"])))
            identity = {
                "status": group["status"],
                "preferred_name": group["preferred_name"],
                "identifiers": group["identifiers"],
                "source_ids": sorted(set(map(str, group["source_ids"]))),
            }
        candidates[candidate_id] = _merge_candidate_rows(rows, candidate_id, identity)
    return [candidates[key] for key in sorted(candidates)]


def _empty_identity_result(results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if _identity_queue(results["candidate_seed_generation"]["records"]):
        raise ProgramError("Candidate identity review is required before controller advancement")
    return {
        "stage": "candidate_identity",
        "status": "complete",
        "records": {"documents": [], "identity_groups": []},
        "gaps": [],
        "notes": ["Every candidate was resolved by exact UniChem identity."],
    }


def _validate_candidate_identity(
    records: Mapping[str, Any], results: Mapping[str, Mapping[str, Any]]
) -> None:
    documents = _validate_documents(records, canonical_ids=True)
    groups = _contract_rows(records, "identity_groups")
    seed_records = results["candidate_seed_generation"]["records"]
    queue_ids = {str(row["seed_id"]) for row in _identity_queue(seed_records)}
    covered: list[str] = []
    targets: list[str] = []
    exact_blocks = {
        candidate_id: set(member_ids)
        for candidate_id, member_ids in _exact_identity_groups(seed_records).items()
    }
    candidate_options = {
        str(row["candidate_id"]): row
        for row in _identity_candidate_options(seed_records)
    }
    document_ids = {str(row["document_id"]) for row in documents}
    for index, group in enumerate(groups):
        label = f"identity_groups[{index}]"
        member_ids = group.get("member_seed_ids")
        if not isinstance(member_ids, list) or not member_ids:
            raise ProgramError(f"{label}.member_seed_ids must be a non-empty list")
        members = [str(value) for value in member_ids]
        if len(members) != len(set(members)) or not set(members) <= queue_ids:
            raise ProgramError(f"{label}.member_seed_ids must be unique queued seed IDs")
        member_set = set(members)
        member_exact_ids = {
            candidate_id
            for candidate_id, block in exact_blocks.items()
            if member_set.intersection(block)
        }
        if any(member_set & block and not block <= member_set for block in exact_blocks.values()):
            raise ProgramError(f"{label} cannot split an exact UniChem identity group")
        covered.extend(members)
        if group.get("status") not in {"resolved", "unresolved", "conflicting"}:
            raise ProgramError(f"{label}.status must be resolved, unresolved, or conflicting")
        _required(group, ("preferred_name", "reason"), label)
        if not isinstance(group.get("identifiers"), dict):
            raise ProgramError(f"{label}.identifiers must be an object")
        target = group.get("canonical_candidate_id")
        if target is not None:
            target = str(target)
            option = candidate_options.get(target)
            valid = (
                option is not None
                and group["status"] == "resolved"
                and member_exact_ids <= {target}
            )
            if valid and option["required_member_seed_ids"]:
                valid = set(option["required_member_seed_ids"]) <= member_set
            if not valid:
                raise ProgramError(
                    f"{label}.canonical_candidate_id must be null or copied exactly from "
                    "context.canonical_candidate_options for a resolved group containing any "
                    "required queued block and no different exact UCI"
                )
            targets.append(target)
        elif group["status"] == "resolved" and len(member_exact_ids) == 1:
            raise ProgramError(
                f"{label}.canonical_candidate_id is required when a resolved group contains "
                "one exact UniChem identity"
            )
        _references(group, "source_ids", document_ids, label)
    if sorted(covered) != sorted(queue_ids) or len(covered) != len(set(covered)):
        raise ProgramError("identity_groups must partition every queued seed exactly once")
    if len(targets) != len(set(targets)):
        raise ProgramError("Each exact UniChem candidate may be attached at most once")
