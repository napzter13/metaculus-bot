"""Atomic JSON files, the persisted scheduler state, log pruning and log summary parsing."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RETENTION_DAYS = 14
_TAIL_BYTES = 256 * 1024

_SUMMARY_RE = re.compile(r"CREDIT_RUN_SUMMARY:.*?n_questions=(\d+).*?charged_usd=([0-9.]+)")
_DEGRADED_RE = re.compile(r"Run completed with \d+ alertable degradation event")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` next to ``path`` and rename over it, so a reader never sees a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any]:
    """The parsed file, or an empty dict when it is absent or unreadable (state is rebuilt, not fatal)."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def iso(moment: datetime | None) -> str | None:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def parse_iso(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def log_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def summarize_log(path: Path) -> dict[str, Any]:
    """From a finished run's log: questions forecast and dollars charged (last summary line), and
    whether the bot reached its completion line with degradation events (it publishes, then exits 1)."""
    tail = log_tail(path)
    matches = _SUMMARY_RE.findall(tail)
    questions: int | None = None
    spend: float | None = None
    if matches:
        questions, spend = int(matches[-1][0]), float(matches[-1][1])
    last_line = next((ln.strip() for ln in reversed(tail.splitlines()) if ln.strip()), "")
    return {
        "questions": questions,
        "spend_usd": spend,
        "degraded": bool(_DEGRADED_RE.search(tail)),
        "last_line": last_line[:200],
    }


def prune_old(root: Path, *, now: float | None = None, days: int = RETENTION_DAYS, suffix: str | None = None) -> int:
    """Delete files under ``root`` last modified more than ``days`` days ago; returns how many."""
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and (suffix is None or path.suffix == suffix) and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
