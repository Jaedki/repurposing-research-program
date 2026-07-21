#!/usr/bin/env python3
"""Build native schema-v7 full-funnel outputs or historical schema-v6 rankings."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

from program_contract import RANK_SECTION_ORDER
from program_io import index_rows, read_jsonl
from validate_program import validate_run


CSV_COLUMNS = (
    "rank_section",
    "rank",
    "endpoint_rank",
    "drug",
    "chemical_identifier",
    "candidate_class",
    "compound_origin",
    "target_endpoint_type",
    "target_endpoint",
    "mode_of_action",
    "repurposing_readiness",
    "raw_score",
    "total_score",
    "applied_cap",
    "cap_reason",
    "audit_status",
    "council_disposition",
)

SECTION_TITLES = {
    "primary_repurposing": "Primary repurposing candidates",
    "target_disease_benchmark": "Target-disease development benchmarks",
    "baseline_care": "Baseline and supportive care",
    "preclinical_hypothesis": "Preclinical hypotheses",
}


def _clean(value: Any) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _reference(source: dict[str, Any]) -> str:
    source_id = _clean(source.get("source_id", "source"))
    identifier = _clean(source.get("canonical_identifier", ""))
    label = f"{source_id}: {identifier}" if identifier else source_id
    pointer = str(source.get("original_pointer", ""))
    return f"[{label}]({pointer})" if pointer.startswith(("https://", "http://")) else label


def _candidate_source_ids(
    candidate: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    council: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    for claim_id in candidate.get("decisive_claim_ids", []):
        values.extend(str(value) for value in claims.get(str(claim_id), {}).get("source_ids", []))
    endpoint = candidate.get("target_endpoint", {})
    if isinstance(endpoint, dict):
        values.extend(str(value) for value in endpoint.get("source_ids", []))
    values.extend(str(value) for value in candidate.get("candidate_class_source_ids", []))
    readiness = candidate.get("repurposing_readiness", {})
    if isinstance(readiness, dict):
        values.extend(str(value) for value in readiness.get("source_ids", []))
    caps = candidate.get("cap_assessments", {})
    for reason in candidate.get("applied_cap", {}).get("reasons", []):
        assessment = caps.get(str(reason), {}) if isinstance(caps, dict) else {}
        if isinstance(assessment, dict):
            values.extend(str(value) for value in assessment.get("source_ids", []))
    values.extend(str(value) for value in council.get("checked_source_ids", []))
    return list(dict.fromkeys(values))


def _candidate_order(candidate: dict[str, Any]) -> tuple[int, int, str]:
    return (
        RANK_SECTION_ORDER.index(str(candidate["rank_section"])),
        int(candidate["rank"]),
        str(candidate["candidate_id"]),
    )


def build_outputs(run_folder: str | Path) -> tuple[Path, Path]:
    root = Path(run_folder).expanduser().resolve()
    case_revision_path = root / "case_revision.json"
    if case_revision_path.is_file():
        try:
            import json

            case_revision = json.loads(case_revision_path.read_text(encoding="utf-8-sig"))
        except Exception:
            case_revision = {}
        if isinstance(case_revision, dict) and case_revision.get("schema_version") == 7:
            from v7_outputs import write_full_funnel_outputs

            return write_full_funnel_outputs(root)
    errors = validate_run(root)
    if errors:
        raise ValueError("Run validation failed; outputs were not written:\n" + "\n".join(f"- {e}" for e in errors))
    candidates = sorted(read_jsonl(root / "candidate_records.jsonl"), key=_candidate_order)
    sources = index_rows(read_jsonl(root / "source_corpus.jsonl"), "source_id")
    claims = index_rows(read_jsonl(root / "claim_ledger.jsonl"), "claim_id")
    councils = index_rows(read_jsonl(root / "council_records.jsonl"), "candidate_id")
    csv_path = root / "ranked_compound_candidates.csv"
    markdown_path = root / "candidate_justifications.md"
    csv_temp = csv_path.with_suffix(".csv.tmp")
    markdown_temp = markdown_path.with_suffix(".md.tmp")
    with csv_temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in candidates:
            endpoint = row["target_endpoint"]
            applied_cap = row["applied_cap"]
            council = councils.get(str(row["candidate_id"]), {})
            writer.writerow(
                {
                    "rank_section": row["rank_section"],
                    "rank": row["rank"],
                    "endpoint_rank": row["endpoint_rank"],
                    "drug": row["canonical_name"],
                    "chemical_identifier": row["canonical_identifier"],
                    "candidate_class": row["candidate_class"],
                    "compound_origin": row["compound_origin"],
                    "target_endpoint_type": endpoint["endpoint_type"],
                    "target_endpoint": endpoint["label"],
                    "mode_of_action": row["mode_of_action"],
                    "repurposing_readiness": (
                        row["repurposing_readiness"]["score"]
                        if row["repurposing_readiness"]["score"] is not None else ""
                    ),
                    "raw_score": row["raw_score"],
                    "total_score": row["total_score"],
                    "applied_cap": applied_cap["maximum"],
                    "cap_reason": ";".join(applied_cap["reasons"]),
                    "audit_status": row["audit_status"],
                    "council_disposition": council.get("disposition", "not_selected"),
                }
            )
    lines = ["# Candidate justifications", ""]
    if not candidates:
        lines.append("No identity-resolved compound candidates were found within the documented search scope.")
    current_section = ""
    for row in candidates:
        if row["rank_section"] != current_section:
            current_section = str(row["rank_section"])
            lines.extend([f"## {SECTION_TITLES[current_section]}", ""])
        endpoint = row["target_endpoint"]
        applied_cap = row["applied_cap"]
        cap_note = ", ".join(applied_cap["reasons"]) or "none"
        council = councils.get(str(row["candidate_id"]), {})
        disposition = council.get("disposition", "not selected")
        reference_ids = _candidate_source_ids(row, claims, council)
        references = "; ".join(_reference(sources[value]) for value in reference_ids if value in sources)
        readiness_score = row["repurposing_readiness"]["score"]
        readiness_note = str(readiness_score) if readiness_score is not None else "not applicable"
        council_note = _clean(" ".join(
            str(council.get(field, ""))
            for field in ("candidate_class_assessment", "endpoint_assessment", "rationale")
        )) or "not reviewed"
        lines.append(
            f"{row['rank']}. **{_clean(row['canonical_name'])}** — "
            f"{_clean(row['candidate_class'])}; endpoint: {_clean(endpoint['label'])} "
            f"({_clean(endpoint['endpoint_type'])}); score {row['raw_score']}→{row['total_score']}; "
            f"cap: {cap_note}; readiness: {readiness_note}; audit: {_clean(row['audit_status'])}; "
            f"council: {_clean(disposition)} ({council_note}). {_clean(row['rationale'])} ({references})."
        )
    markdown_temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    csv_temp.replace(csv_path)
    markdown_temp.replace(markdown_path)
    return csv_path, markdown_path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: build_final_outputs.py <run_folder>", file=sys.stderr)
        return 2
    try:
        csv_path, markdown_path = build_outputs(argv[1])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(csv_path)
    print(markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
