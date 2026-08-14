"""Minimal projection and Markdown rendering of final evidence cards."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .evidence import _rows


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
        assessment = assessments[str(ranked_row["candidate_id"])]
        cards.append(
            {
                "name": str(ranked_row["name"]).strip(),
                "how_it_could_work": {
                    "text": str(assessment["net_assessment"]["text"]).strip(),
                    "source_ids": sorted(
                        set(map(str, assessment["net_assessment"]["source_ids"]))
                    ),
                },
                "reasons_why_not": [
                    {
                        "text": str(finding["finding"]).strip(),
                        "source_ids": sorted(set(map(str, finding["source_ids"]))),
                    }
                    for finding in assessment["why_not"]
                ],
            }
        )
    return cards


def _prose(value: Any) -> str:
    return " ".join(str(value).split())


def _with_citations(text: Any, source_ids: Iterable[Any]) -> str:
    citations = "; ".join(sorted(set(map(str, source_ids))))
    return f"{_prose(text)} [{citations}]" if citations else _prose(text)


def _cards_bytes(cards: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for card in cards:
        mechanism = card["how_it_could_work"]
        lines.extend(
            [
                f"## {_prose(card['name'])}",
                "",
                "### How it could work",
                "",
                _with_citations(mechanism["text"], mechanism["source_ids"]),
                "",
            ]
        )
        if card["reasons_why_not"]:
            lines.extend(["### Reasons why not", ""])
            for reason in card["reasons_why_not"]:
                lines.extend(
                    [_with_citations(reason["text"], reason["source_ids"]), ""]
                )
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")
