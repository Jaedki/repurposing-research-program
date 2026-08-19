"""Verbatim rendering of candidate hypothesis reports."""

from __future__ import annotations

from typing import Any


def _evidence_card_rows(ranked_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"name": str(row["name"]).strip(), "hypothesis_report": str(row["hypothesis_report"]).strip()}
        for row in ranked_rows
    ]


def _cards_bytes(cards: list[dict[str, str]]) -> bytes:
    lines = [
        line
        for card in cards
        for line in (f"## {card['name']}", "", card["hypothesis_report"], "")
    ]
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")
