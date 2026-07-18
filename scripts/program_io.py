#!/usr/bin/env python3
"""Small shared helpers for canonical JSON artifacts and safe in-run paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    text = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=True, indent=2)
    )
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n")


def inside(root: Path, value: str | Path) -> Path:
    supplied = Path(value)
    resolved = (supplied if supplied.is_absolute() else root / supplied).resolve()
    resolved.relative_to(root.resolve())
    return resolved


def index_rows(rows: Iterable[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {str(row.get(key)): row for row in rows if str(row.get(key, "")).strip()}


def upsert_jsonl(path: Path, rows: list[dict[str, Any]], key: str) -> None:
    current = read_jsonl(path)
    positions = {str(row.get(key)): index for index, row in enumerate(current)}
    for row in rows:
        identity = str(row.get(key, "")).strip()
        if not identity:
            raise ValueError(f"{path.name} update lacks {key}")
        if identity in positions:
            current[positions[identity]] = row
        else:
            positions[identity] = len(current)
            current.append(row)
    write_jsonl(path, current)
