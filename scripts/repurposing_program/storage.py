"""Canonical serialization and immutable run-artifact paths."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .errors import ProgramError


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_sha256(_canonical_bytes(value))[:24]}"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ProgramError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProgramError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProgramError(f"Expected one JSON object: {path}")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProgramError(f"Immutable artifact conflicts with existing file: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> None:
    _write_once(path, _canonical_bytes(value))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = b"".join(_canonical_bytes(row) for row in rows)
    _write_once(path, payload)


def _result_path(root: Path, stage: str) -> Path:
    return root / "results" / f"{stage}.json"


def _accepted_result_files(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted((root / "results").rglob("*.json"))
        if path.is_file()
    }


def _item_token(item_id: str) -> str:
    return _stable_id("ITEM", item_id)


def _item_result_path(root: Path, task: str, item_id: str) -> Path:
    return root / "results" / "items" / task / f"{_item_token(item_id)}.json"


def _packet_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "packets" / f"{task}.json"
    return root / "packets" / "items" / task / f"{_item_token(item_id)}.json"


def _submission_path(root: Path, task: str, item_id: str | None = None) -> Path:
    if item_id is None:
        return root / "submissions" / f"{task}.json"
    return root / "submissions" / "items" / task / f"{_item_token(item_id)}.json"
