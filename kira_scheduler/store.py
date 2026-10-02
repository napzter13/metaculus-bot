"""Atomic JSON files, the persisted scheduler state, log pruning and log summary parsing."""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RETENTION_DAYS = 14
# The research archive (persisted research and raw provider payloads) is what the sync and analysis
# tools read; on Actions it was a 90-day artifact. It keeps 120 days here, bounded by size.
ARCHIVE_RETENTION_DAYS = 120
ARCHIVE_MAX_BYTES = 4 * 1024**3
# What the dashboard (uid 1101, group kira-earn-read) needs: group-readable, never world-readable.
# mkstemp creates 0600 whatever the umask, so this is set explicitly rather than left to it.
FILE_MODE = 0o640
# 2750: the setgid bit keeps new files in the data dir's group (kira-earn-read); a plain 0750 would clear it.
DIR_MODE = 0o2750
_TAIL_BYTES = 256 * 1024
_RAW_RESEARCH_PREFIX = "raw_research_"

_SUMMARY_RE = re.compile(r"CREDIT_RUN_SUMMARY:.*?n_questions=(\d+).*?charged_usd=([0-9.]+)")
_DEGRADED_RE = re.compile(r"Run completed with \d+ alertable degradation event")
_TRANSIENT_RE = re.compile(r"TRANSIENT_NETWORK_SKIP:\s*stage=(?P<stage>\S+)\s+error=(?P<error>\S+)")


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` next to ``path`` and rename over it, so a reader never sees a torn file.

    The file is mode ``FILE_MODE`` (0640), set on the descriptor before the rename so there is no
    moment at which the dashboard could see it unreadable: ``mkstemp`` alone would leave 0600.
    """
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), FILE_MODE)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def ensure_dir(path: Path) -> None:
    """Create ``path`` (and parents) as ``DIR_MODE``; parents that already exist are left alone."""
    missing = [p for p in (path, *path.parents) if not p.exists()]
    path.mkdir(parents=True, exist_ok=True)
    for created in missing:
        with contextlib.suppress(OSError):
            created.chmod(DIR_MODE)


def set_file_mode(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.chmod(FILE_MODE)


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
    whether the bot reached its completion line with degradation events (it publishes, then exits 1), and
    whether it skipped the run for a network blip (the ``TRANSIENT_NETWORK_SKIP`` marker)."""
    tail = log_tail(path)
    matches = _SUMMARY_RE.findall(tail)
    questions: int | None = None
    spend: float | None = None
    if matches:
        questions, spend = int(matches[-1][0]), float(matches[-1][1])
    last_line = next((ln.strip() for ln in reversed(tail.splitlines()) if ln.strip()), "")
    transient = _TRANSIENT_RE.search(tail)
    return {
        "questions": questions,
        "spend_usd": spend,
        "degraded": bool(_DEGRADED_RE.search(tail)),
        "last_line": last_line[:200],
        # The bot skipped the run (exit 0) because DNS, connect or a timeout failed: a network blip.
        "transient": transient.groupdict() if transient else None,
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


def _is_archive(work_dir: Path, path: Path) -> bool:
    """The research archive inside the bot's work dir: ``research_outputs/`` and raw provider logs."""
    relative = path.relative_to(work_dir)
    return relative.parts[0] == "research_outputs" or path.name.startswith(_RAW_RESEARCH_PREFIX)


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _expired(work_dir: Path, path: Path, mtime: float, current: float) -> bool:
    days = ARCHIVE_RETENTION_DAYS if _is_archive(work_dir, path) else RETENTION_DAYS
    return mtime < current - days * 86400


def prune_work(work_dir: Path, *, now: float | None = None, max_archive_bytes: int = ARCHIVE_MAX_BYTES) -> int:
    """Prune the bot's work dir; returns how many files went.

    The research archive (``research_outputs/`` and ``raw_research_*.jsonl``) keeps
    ``ARCHIVE_RETENTION_DAYS`` and, past ``max_archive_bytes``, loses its OLDEST files first. Every
    other file keeps ``RETENTION_DAYS``.
    """
    current = time.time() if now is None else now
    if not work_dir.is_dir():
        return 0
    removed = 0
    kept_archive: list[tuple[float, int, Path]] = []
    for path in work_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        if _expired(work_dir, path, stat.st_mtime, current):
            removed += _unlink(path)
        elif _is_archive(work_dir, path):
            kept_archive.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in kept_archive)
    for _, size, path in sorted(kept_archive):
        if total <= max_archive_bytes:
            break
        if _unlink(path):
            total -= size
            removed += 1
    return removed
