"""Projection and deterministic Markdown rendering of final evidence cards."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import (
    MAX_SCORE,
    SCORE_COMPONENTS,
    SCORE_LABELS,
    _SOURCE_CHECK_VERDICTS,
)
from .evidence import _rows
from .ranking import _final_score


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
                        "source_ids": sorted(
                            set(
                                map(
                                    str,
                                    assessment["component_scores"][component][
                                        "source_ids"
                                    ],
                                )
                            )
                        ),
                    }
                    for component in SCORE_COMPONENTS
                },
                "why": {
                    "text": str(assessment["net_assessment"]["text"]).strip(),
                    "source_ids": sorted(
                        set(map(str, assessment["net_assessment"]["source_ids"]))
                    ),
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
        for verdict in (
            "supports",
            "partly_supports",
            "does_not_support",
            "contradicts",
        )
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
                f"{str(check['verdict']).replace('_', ' ')} \u2014 "
                f"{_single_line(check['finding'])}"
                for check in exceptions
            )
            lines.append("")
        for component in SCORE_COMPONENTS:
            score = card["components"][component]
            lines.extend(
                [
                    f"- {SCORE_LABELS[component]}: {score['value']}/20 \u2014 "
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
