#!/usr/bin/env python3
"""Build compound-only user outputs after full structured validation."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any

from validate_program import validate_run


CSV_COLUMNS = (
    "candidate_id",
    "drug_name",
    "human_gene",
    "worm_gene",
    "allele_mode",
    "worm_disease_model",
    "dossier_path",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _clean_markdown(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def build_outputs(run_folder: str | Path) -> tuple[Path, Path]:
    root = Path(run_folder).expanduser().resolve()
    errors = validate_run(root)
    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"Run validation failed; outputs were not written:\n{joined}")

    candidates = [
        row
        for row in _read_jsonl(root / "candidate_records.jsonl")
        if row["council_disposition"] == "screen"
    ]
    candidates.sort(key=lambda row: (row["canonical_name"].casefold(), row["candidate_id"]))

    csv_path = root / "17_screening_candidates.csv"
    markdown_path = root / "18_candidate_rationales.md"
    csv_temp = csv_path.with_suffix(".csv.tmp")
    markdown_temp = markdown_path.with_suffix(".md.tmp")

    with csv_temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in candidates:
            writer.writerow(
                {
                    "candidate_id": row["candidate_id"],
                    "drug_name": row["canonical_name"],
                    "human_gene": row["human_gene"],
                    "worm_gene": row["worm_gene"],
                    "allele_mode": row["allele_mode"],
                    "worm_disease_model": row["worm_model"],
                    "dossier_path": row["dossier_path"],
                }
            )

    lines = ["# Compound Screening Rationales", ""]
    if not candidates:
        lines.extend(
            [
                "No exact compound met the audited screening-inclusion standard.",
                "",
            ]
        )
    for row in candidates:
        lines.extend(
            [
                f"## {_clean_markdown(row['canonical_name'])}",
                "",
                f"- Candidate ID: `{_clean_markdown(row['candidate_id'])}`",
                f"- Chemical ID: `{_clean_markdown(row['canonical_identifier'])}`",
                f"- Model: {_clean_markdown(row['worm_model'])} ({_clean_markdown(row['allele_mode'])})",
                f"- Evidence origin: {_clean_markdown(row['origin'])}",
                f"- Rationale: {_clean_markdown(row['rationale'])}",
                f"- Expected phenomic interpretation: {_clean_markdown(row['phenomic_interpretation'])}",
                f"- Decisive uncertainty: {_clean_markdown(row['decisive_uncertainty'])}",
                f"- Evidence dossier: `{_clean_markdown(row['dossier_path'])}`",
                "",
            ]
        )
    markdown_temp.write_text("\n".join(lines), encoding="utf-8")

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
