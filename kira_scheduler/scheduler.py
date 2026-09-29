"""The scheduler: fires the three bot workflows on their UTC slots and reports through status.json.

One foreground process, single-threaded. Each tick reaps finished children, enforces the 70 minute
run timeout, fires due slots and refreshes the heartbeat and status files. Behaviour it owns:

- Never two runs of one workflow at once. A slot that arrives mid-run is queued once and starts the
  moment the run ends, which is what the workflows' ``cancel-in-progress: false`` group did.
- Catch-up: at start (and whenever keys appear) a workflow whose latest slot was never handled runs
  at once. A run cut short by SIGTERM leaves its slot unhandled, so the next start repeats it.
- Gate: a workflow without its gate keys logs "not configured" once per slot and does nothing.
- SIGTERM: the children get SIGTERM as a group, then SIGKILL after ``STOP_GRACE_S``; the outcome is
  recorded and the process exits 0 inside kira-earn's ``stop_wait_s`` (90 s).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from kira_scheduler import env as envmod
from kira_scheduler import store
from kira_scheduler.slots import latest_slot, next_slot
from kira_scheduler.spec import PROGRAM_NAME, WORKFLOWS, Workflow

logger = logging.getLogger("kira_scheduler")

HEARTBEAT_INTERVAL_S = 30
STATUS_INTERVAL_S = 60
TICK_S = 1.0
KILL_GRACE_S = 30
STOP_GRACE_S = 60
SUMMARY_MAX_CHARS = 200

CommandFor = Callable[[Workflow], list[str]]


@dataclass
class _Run:
    proc: subprocess.Popen[bytes]
    log_path: Path
    log_handle: IO[bytes]
    started: datetime
    slot: datetime
    prev_handled: datetime | None
    term_sent: datetime | None = None
    killed: bool = False
    reason: str | None = None  # "timeout" | "shutdown"


@dataclass
class _Runtime:
    run: _Run | None = None
    pending: bool = False
    notified: datetime | None = None


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _epoch(moment: datetime | None) -> int | None:
    return int(moment.timestamp()) if moment else None


class Scheduler:
    def __init__(
        self,
        *,
        environ: Mapping[str, str],
        data_dir: Path,
        app_dir: Path,
        workflows: tuple[Workflow, ...] = WORKFLOWS,
        command_for: CommandFor | None = None,
        stop_grace_s: float = STOP_GRACE_S,
        kill_grace_s: float = KILL_GRACE_S,
        started_at: datetime | None = None,
    ) -> None:
        self.environ = dict(environ)
        self.data_dir = data_dir
        self.app_dir = app_dir
        self.workflows = workflows
        self.command_for: CommandFor = command_for or self._default_command
        self.stop_grace_s = stop_grace_s
        self.kill_grace_s = kill_grace_s
        self.started_at = started_at or datetime.now(UTC)
        self._rt = {wf.name: _Runtime() for wf in workflows}
        self._state: dict[str, Any] = {"schema": 1, "workflows": {}}
        self._stop = threading.Event()
        self._stopping = False
        self._last_beat: datetime | None = None
        self._last_status: datetime | None = None
        self._dirty = True

    # ---- paths -------------------------------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def status_path(self) -> Path:
        return self.data_dir / "status.json"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    def run_dir(self, wf: Workflow) -> Path:
        return self.data_dir / "runs" / wf.name

    def _default_command(self, wf: Workflow) -> list[str]:
        # The workflow's own line, `uv run --frozen --no-dev python main.py [--mode X]`, with the code
        # addressed by path because the working directory is the data dir (the app dir is read-only).
        return [
            "uv",
            "run",
            "--frozen",
            "--no-dev",
            "--project",
            str(self.app_dir),
            "python",
            str(self.app_dir / "main.py"),
            *wf.args,
        ]

    # ---- persisted per-workflow state ---------------------------------------------------------
    def _wstate(self, wf: Workflow) -> dict[str, Any]:
        return self._state["workflows"].setdefault(wf.name, {})

    def _handled(self, wf: Workflow) -> datetime | None:
        return store.parse_iso(self._wstate(wf).get("handled_slot"))

    def _save_state(self) -> None:
        store.write_json_atomic(self.state_path, self._state)
        self._dirty = True

    # ---- lifecycle ----------------------------------------------------------------------------
    def startup(self, now: datetime) -> None:
        for sub in ("runs", "work"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        loaded = store.read_json(self.state_path)
        if isinstance(loaded.get("workflows"), dict):
            self._state = {"schema": 1, "workflows": loaded["workflows"]}
        self._kill_orphans()
        self._prune()
        self._beat(now)
        missing = envmod.program_missing(self.environ)
        logger.info(
            "started (%s); program %s",
            PROGRAM_NAME,
            "configured" if not missing else f"NOT configured, missing {', '.join(missing)}",
        )
        self._write_status(now)

    def request_stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.startup(datetime.now(UTC))
        while not self._stop.is_set():
            self.tick(datetime.now(UTC))
            self._stop.wait(TICK_S)
        self.shutdown(datetime.now(UTC))

    def tick(self, now: datetime) -> None:
        self._reap(now)
        for wf in self.workflows:
            self._consider(wf, now)
        if self._last_beat is None or (now - self._last_beat).total_seconds() >= HEARTBEAT_INTERVAL_S:
            self._beat(now)
        if self._dirty or self._last_status is None or (now - self._last_status).total_seconds() >= STATUS_INTERVAL_S:
            self._write_status(now)

    def shutdown(self, now: datetime) -> None:
        """SIGTERM every child group, wait ``stop_grace_s``, SIGKILL what is left, record all of it."""
        self._stopping = True
        live = [(wf, rt.run) for wf in self.workflows if (rt := self._rt[wf.name]).run is not None]
        for _, run in live:
            if run is not None:
                run.reason = "shutdown"
                self._signal_group(run.proc, signal.SIGTERM)
        deadline = time.monotonic() + self.stop_grace_s
        while time.monotonic() < deadline and any(run is not None and run.proc.poll() is None for _, run in live):
            time.sleep(0.05)
        for _, run in live:
            if run is not None and run.proc.poll() is None:
                self._signal_group(run.proc, signal.SIGKILL)
                run.proc.wait()
        for wf, run in live:
            if run is not None:
                self._finish(wf, run, run.proc.returncode, now)
        self._beat(now)
        self._write_status(now, stopped=True)
        logger.info("stopped")

    # ---- firing -------------------------------------------------------------------------------
    def _consider(self, wf: Workflow, now: datetime) -> None:
        if self._stopping or not envmod.workflow_enabled(wf, self.environ):
            return
        slot = latest_slot(wf.slots, now)
        rt = self._rt[wf.name]
        missing = envmod.workflow_missing(wf, self.environ)
        if missing:
            if rt.notified != slot:
                rt.notified = slot
                logger.info(
                    "%s: not configured (missing %s); nothing to do for slot %s",
                    wf.name,
                    ", ".join(missing),
                    store.iso(slot),
                )
                self._dirty = True
            return
        handled = self._handled(wf)
        if handled is not None and handled >= slot:
            return
        if rt.run is not None:
            rt.pending = True
            self._wstate(wf)["handled_slot"] = store.iso(slot)
            self._save_state()
            logger.info("%s: slot %s arrived while a run is in progress; queued behind it", wf.name, store.iso(slot))
            return
        self._start(wf, now, slot)

    def _start(self, wf: Workflow, now: datetime, slot: datetime) -> None:
        stamp = _stamp(now)
        log_dir = self.run_dir(wf)
        log_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{stamp}.log"
        suffix = 1
        while log_path.exists():
            log_path = log_dir / f"{stamp}-{suffix}.log"
            suffix += 1
        run_id = f"kira-{wf.name}-{stamp}"
        child_env = envmod.build_child_env(wf, self.environ, run_id)
        state = self._wstate(wf)
        prev_handled = self._handled(wf)
        handle = log_path.open("ab")
        handle.write(f"# kira-earn {PROGRAM_NAME} {wf.name} slot={store.iso(slot)} started={store.iso(now)}\n".encode())
        handle.flush()
        state["handled_slot"] = store.iso(slot)
        state["last_started"] = store.iso(now)
        try:
            proc = subprocess.Popen(  # noqa: S603  # argv list built here, no shell; env is the allowlisted child env
                self.command_for(wf),
                cwd=self.work_dir,
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            handle.write(f"# failed to start: {exc}\n".encode())
            handle.close()
            state.update(
                last_finished=store.iso(now),
                last_rc=127,
                last_error=f"failed to start: {exc}"[:SUMMARY_MAX_CHARS],
                last_interrupted=False,
                pid=None,
            )
            self._save_state()
            logger.error("%s: failed to start: %s", wf.name, exc)
            return
        state["pid"] = proc.pid
        state["last_interrupted"] = False
        self._save_state()
        self._rt[wf.name].run = _Run(proc, log_path, handle, now, slot, prev_handled)
        logger.info("%s: run started for slot %s (pid %d, log %s)", wf.name, store.iso(slot), proc.pid, log_path.name)

    # ---- children -----------------------------------------------------------------------------
    @staticmethod
    def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)

    def _reap(self, now: datetime) -> None:
        for wf in self.workflows:
            run = self._rt[wf.name].run
            if run is None:
                continue
            rc = run.proc.poll()
            if rc is not None:
                self._finish(wf, run, rc, now)
                continue
            if run.term_sent is None and (now - run.started).total_seconds() >= wf.run_timeout_s:
                run.reason = "timeout"
                run.term_sent = now
                self._signal_group(run.proc, signal.SIGTERM)
                logger.warning("%s: run exceeded %d min; sent SIGTERM", wf.name, wf.run_timeout_s // 60)
            elif (
                run.term_sent is not None
                and not run.killed
                and (now - run.term_sent).total_seconds() >= self.kill_grace_s
            ):
                run.killed = True
                self._signal_group(run.proc, signal.SIGKILL)

    def _finish(self, wf: Workflow, run: _Run, rc: int | None, now: datetime) -> None:
        run.log_handle.close()
        summary = store.summarize_log(run.log_path)
        state = self._wstate(wf)
        rc_value = rc if rc is not None else -1
        interrupted = run.reason == "shutdown"
        if interrupted:
            error: str | None = "stopped by SIGTERM (scheduler shutdown)"
            state["handled_slot"] = store.iso(run.prev_handled)  # the next start repeats this slot
        elif run.reason == "timeout":
            error = f"timeout after {wf.run_timeout_s // 60}m"
        elif rc_value != 0:
            note = " (published with degradation events)" if summary["degraded"] else ""
            error = f"exit {rc_value}{note}: {summary['last_line']}"[:SUMMARY_MAX_CHARS]
        else:
            error = None
        state.update(
            last_finished=store.iso(now),
            last_rc=rc_value,
            last_error=error,
            last_interrupted=interrupted,
            last_degraded=bool(summary["degraded"]) and rc_value != 0 and not interrupted,
            pid=None,
        )
        if not interrupted and summary["questions"] is not None:
            state["last_questions"] = summary["questions"]
            state["last_spend_usd"] = summary["spend_usd"]
            state["questions_total"] = int(state.get("questions_total", 0)) + summary["questions"]
            state["spend_total"] = round(float(state.get("spend_total", 0.0)) + (summary["spend_usd"] or 0.0), 4)
        if rc_value == 0 and not interrupted and run.reason is None:
            state["last_ok_finished"] = store.iso(now)
        self._save_state()
        rt = self._rt[wf.name]
        rt.run = None
        logger.info("%s: run finished rc=%d%s", wf.name, rc_value, f" ({error})" if error else "")
        self._prune()
        if rt.pending and not self._stopping:
            rt.pending = False
            self._start(wf, now, latest_slot(wf.slots, now))

    def _kill_orphans(self) -> None:
        """A run left behind by a scheduler that died hard is killed, so two never overlap."""
        for wf in self.workflows:
            state = self._wstate(wf)
            pid = state.get("pid")
            if not isinstance(pid, int) or pid <= 1:
                continue
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            except OSError:
                cmdline = b""
            if b"main.py" in cmdline:
                logger.warning("%s: killing orphaned run pid %d from a previous scheduler", wf.name, pid)
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(pid, sig)
                    except (ProcessLookupError, PermissionError):
                        break
                    time.sleep(2 if sig == signal.SIGTERM else 0)
            state["pid"] = None
            state["handled_slot"] = state.get("handled_slot") if state.get("last_finished") else None
        self._save_state()

    def _prune(self) -> None:
        for wf in self.workflows:
            store.prune_old(self.run_dir(wf), suffix=".log")
        store.prune_old(self.work_dir)

    # ---- heartbeat and status -----------------------------------------------------------------
    def _beat(self, now: datetime) -> None:
        self._last_beat = now
        self._dirty = True  # status.json carries this heartbeat, so it is rewritten with it
        self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path.write_text(f"{_epoch(now)}\n", encoding="utf-8")

    def _workflow_view(self, wf: Workflow, now: datetime) -> dict[str, Any]:
        state = self._wstate(wf)
        enabled = envmod.workflow_enabled(wf, self.environ)
        return {
            "enabled": enabled,
            "configured": not envmod.workflow_missing(wf, self.environ),
            "next_slot": store.iso(next_slot(wf.slots, now)) if enabled else None,
            "last_started": state.get("last_started"),
            "last_finished": state.get("last_finished"),
            "last_rc": state.get("last_rc"),
            "questions_forecast": int(state.get("questions_total", 0)),
            "spend_usd": round(float(state.get("spend_total", 0.0)), 4),
            "error": state.get("last_error"),
            "running": self._rt[wf.name].run is not None,
            "last_questions": state.get("last_questions"),
            "last_spend_usd": state.get("last_spend_usd"),
            "degraded": bool(state.get("last_degraded", False)),
        }

    def _last_run(self) -> dict[str, Any] | None:
        best: tuple[datetime, Workflow] | None = None
        for wf in self.workflows:
            state = self._wstate(wf)
            finished = store.parse_iso(state.get("last_finished"))
            if finished is None or state.get("last_interrupted"):
                continue
            if best is None or finished > best[0]:
                best = (finished, wf)
        if best is None:
            return None
        finished, wf = best
        state = self._wstate(wf)
        return {
            "kind": wf.name,
            "ok": state.get("last_rc") == 0,
            "finished": _epoch(finished),
            "error": state.get("last_error"),
            "rc": state.get("last_rc"),
            "degraded": bool(state.get("last_degraded", False)),
        }

    def _last_ok(self) -> int | None:
        stamps = [store.parse_iso(self._wstate(wf).get("last_ok_finished")) for wf in self.workflows]
        latest = max((s for s in stamps if s is not None), default=None)
        return _epoch(latest)

    def _summary(self, now: datetime, *, stopped: bool) -> str:
        if stopped:
            return "stopped"
        missing = envmod.program_missing(self.environ)
        if missing:
            return f"not configured: missing {', '.join(missing)}; idling, checked again each slot"
        parts = []
        for wf in self.workflows:
            view = self._workflow_view(wf, now)
            if not view["enabled"]:
                parts.append(f"{wf.name} off")
            elif not view["configured"]:
                parts.append(f"{wf.name} not configured")
            elif view["running"]:
                parts.append(f"{wf.name} running")
            elif view["last_rc"] is None:
                parts.append(f"{wf.name} waiting for its slot")
            else:
                parts.append(f"{wf.name} rc={view['last_rc']} {view['questions_forecast']} q")
        return "; ".join(parts)[:SUMMARY_MAX_CHARS]

    def status(self, now: datetime, *, stopped: bool = False) -> dict[str, Any]:
        """The status.json payload: the launcher's schema plus the fields kira-earnings reads."""
        missing = envmod.program_missing(self.environ)
        return {
            "schema": 1,
            "updated_at": store.iso(now),
            "summary": self._summary(now, stopped=stopped),
            "configured": not missing,
            "missing": missing,
            "mode": envmod.effective_mode(self.environ),
            "workflows": {wf.name: self._workflow_view(wf, now) for wf in self.workflows},
            "heartbeat": _epoch(self._last_beat),
            "started": _epoch(self.started_at),
            "last_run": self._last_run(),
            "last_ok": self._last_ok(),
        }

    def _write_status(self, now: datetime, *, stopped: bool = False) -> None:
        store.write_json_atomic(self.status_path, self.status(now, stopped=stopped))
        self._last_status = now
        self._dirty = False


def main() -> int:
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s kira_scheduler: %(message)s"
    )
    data = os.environ.get("KIRA_EARN_DATA")
    if not data:
        logger.error("KIRA_EARN_DATA is not set; kira-earn provides it")
        return 2
    app = Path(os.environ.get("KIRA_EARN_APP") or Path(__file__).resolve().parent.parent)
    scheduler = Scheduler(environ=os.environ, data_dir=Path(data), app_dir=app)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: scheduler.request_stop())
    scheduler.run()
    return 0
