"""Import this bot's Kira run data into the artifact store the sync tools already read.

On GitHub Actions every run uploaded a 90-day ``research-<run_id>`` artifact (its log, the persisted
research and the raw provider payloads) and ``make sync_telemetry`` / ``sync_research`` /
``sync_raw_research`` / ``sync_all`` pulled them. On Kira the same data sits under the program's data
dir (``runs/<workflow>/<stamp>.log`` and ``work/``). This script copies each run into
``backtests/gha_artifact_store/research-<run_id>/`` in the layout those tools expect, so they then
run OFFLINE from the store::

    docker cp kira-earn:/var/lib/kira-earn/metaculus-bot ./kira-data
    uv run python scripts/import_kira_runs.py --data-dir ./kira-data
    make sync_telemetry ARGS="--from-store"       # then: make cost_report
    make sync_all ARGS="--from-store"

Free and read-only against the data dir. Re-running is safe: a run already in the store is skipped.
Import at least every 14 days, since Kira prunes run logs at 14 days (the research archive keeps 120).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from kira_scheduler.runid import kira_run_id
from kira_scheduler.spec import WORKFLOWS
from scripts.gha_artifacts import DEFAULT_STORE_DIR, STORE_META_FILENAME

logger = logging.getLogger(__name__)

_WORKFLOW_NAMES = frozenset(wf.name for wf in WORKFLOWS)


def _created_at(stamp: str) -> str:
    return datetime.strptime(stamp[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _research_files_for(work_dir: Path, bot_run_id: str) -> list[Path]:
    """The persisted-research files whose records carry this run's ``GITHUB_RUN_ID``."""
    matches: list[Path] = []
    for path in sorted((work_dir / "research_outputs").glob("*.jsonl")):
        try:
            with path.open(encoding="utf-8") as handle:
                first = json.loads(handle.readline() or "{}")
        except (OSError, ValueError):
            continue
        if isinstance(first, dict) and first.get("run_id") == bot_run_id:
            matches.append(path)
    return matches


def import_one(log: Path, workflow: str, work_dir: Path, store_dir: Path) -> bool:
    """Persist one run; returns False when it is already in the store."""
    stamp = log.stem[:16]
    run_id = kira_run_id(workflow, stamp)
    name = f"research-{run_id}"
    dest = store_dir / name
    if (dest / STORE_META_FILENAME).is_file():
        return False
    bot_run_id = f"kira-{workflow}-{stamp}"
    staging = store_dir / f".{name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "run_logs").mkdir(parents=True)
    shutil.copy2(log, staging / "run_logs" / f"run_{run_id}_{stamp}.log")
    raw = work_dir / "run_logs" / f"raw_research_{bot_run_id}.jsonl"
    if raw.is_file():
        shutil.copy2(raw, staging / "run_logs" / raw.name)
    research = _research_files_for(work_dir, bot_run_id)
    if research:
        (staging / "research_outputs").mkdir()
        for path in research:
            shutil.copy2(path, staging / "research_outputs" / path.name)
    meta = {"artifact_id": str(run_id), "name": name, "created_at": _created_at(stamp), "run_id": str(run_id)}
    # The meta file is what marks a store dir complete, so it goes in last, before the rename.
    (staging / STORE_META_FILENAME).write_text(json.dumps(meta))
    shutil.rmtree(dest, ignore_errors=True)
    staging.rename(dest)
    return True


def import_kira_runs(data_dir: Path, store_dir: Path) -> tuple[int, int]:
    """Import every run log under ``data_dir``; returns ``(imported, already_present)``."""
    imported = skipped = 0
    store_dir.mkdir(parents=True, exist_ok=True)
    runs = data_dir / "runs"
    for workflow_dir in sorted(runs.iterdir()) if runs.is_dir() else []:
        if workflow_dir.name not in _WORKFLOW_NAMES:
            continue
        for log in sorted(workflow_dir.glob("*.log")):
            if import_one(log, workflow_dir.name, data_dir / "work", store_dir):
                imported += 1
            else:
                skipped += 1
    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Kira run data into the GHA artifact store.")
    parser.add_argument("--data-dir", required=True, help="a copy of /var/lib/kira-earn/metaculus-bot")
    parser.add_argument("--store-dir", default=DEFAULT_STORE_DIR, help=f"default: {DEFAULT_STORE_DIR}")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    data_dir = Path(args.data_dir)
    if not (data_dir / "runs").is_dir():
        logger.error("%s has no runs/ directory; is it a copy of the metaculus-bot data dir?", data_dir)
        return 2
    imported, skipped = import_kira_runs(data_dir, Path(args.store_dir))
    logger.info("Imported %d run(s), %d already in the store (%s)", imported, skipped, args.store_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
