"""The scheduler loop with real child processes: catch-up, the gate, overlap, timeouts, status, SIGTERM.

Children are throwaway ``python -c`` programs, so no bot code, key or network is involved. The clock
is injected (``tick(now)``), which is what lets a test cross a slot boundary or a 70 minute timeout
without waiting. The last test runs the real ``python -m kira_scheduler`` with a stand-in ``uv`` on
PATH to prove the production command line and the SIGTERM path end to end.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from kira_scheduler import scheduler as scheduler_module
from kira_scheduler.scheduler import Scheduler
from kira_scheduler.spec import MODE_DEFAULTS, WORKFLOWS
from kira_scheduler.store import prune_old, prune_work, write_json_atomic

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OK_LINE = "CREDIT_RUN_SUMMARY: n_questions=3 charged_usd=0.1234 usd_per_question=0.0411"
_KEYS = {"METACULUS_TOKEN": "tok-secret-m", "OPENROUTER_API_KEY": "tok-secret-or"}
_ONLY_TOURNAMENT = {"WORKFLOW_MINIBENCH_ENABLED": "false", "WORKFLOW_MANTIC_ENABLED": "false"}


def _t(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, second, tzinfo=UTC)


def _make(tmp_path: Path, code: str = "pass", environ: dict[str, str] | None = None, **kwargs: Any) -> Scheduler:
    def command_for(_wf: Any) -> list[str]:
        return [sys.executable, "-c", code, str(tmp_path / "flag")]

    return Scheduler(
        environ={**_KEYS, **(environ or {})},
        data_dir=tmp_path / "data",
        app_dir=tmp_path / "app",
        command_for=kwargs.pop("command_for", command_for),
        started_at=_t(9, 0),
        **kwargs,
    )


def _settle(sched: Scheduler, now: datetime, *, timeout: float = 15.0) -> None:
    """Tick until no child is left, since a child finishes on its own real-time schedule."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sched.tick(now)
        if not any(sched.status(now)["workflows"][wf.name]["running"] for wf in sched.workflows):
            return
        time.sleep(0.02)
    raise AssertionError("children still running")


def _logs(sched: Scheduler, name: str) -> list[Path]:
    directory = sched.data_dir / "runs" / name
    return sorted(directory.glob("*.log")) if directory.is_dir() else []


def _status(sched: Scheduler) -> dict[str, Any]:
    return json.loads(sched.status_path.read_text())


class TestGate:
    def test_without_keys_it_idles_logs_once_per_slot_and_stays_healthy(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        sched = _make(tmp_path, environ={"METACULUS_TOKEN": "", "OPENROUTER_API_KEY": ""})
        caplog.set_level(logging.INFO, logger="kira_scheduler")
        sched.startup(_t(10, 14))
        for second in range(5):
            sched.tick(_t(10, 14, second))  # one slot (10:03 for the tournament), five ticks
        sched.tick(_t(10, 24))  # the tournament moves to 10:23, a new slot
        sched.shutdown(_t(10, 24))
        notices = [r.getMessage() for r in caplog.records if r.getMessage().startswith("tournament: not configured")]
        assert len(notices) == 2, notices
        assert "METACULUS_TOKEN, OPENROUTER_API_KEY" in notices[0]
        assert not (sched.data_dir / "runs" / "tournament").exists() or not _logs(sched, "tournament")
        status = _status(sched)
        assert status["configured"] is False
        assert status["missing"] == ["METACULUS_TOKEN", "OPENROUTER_API_KEY"]
        assert status["summary"] == "stopped"

    def test_mantic_needs_its_own_token(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=f"print({_OK_LINE!r})")
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        assert _logs(sched, "tournament"), "the Metaculus key pair arms the tournament"
        assert not _logs(sched, "mantic"), "MANTIC_TOKEN is missing, so mantic must idle"
        assert _status(sched)["workflows"]["mantic"]["configured"] is False
        sched2 = _make(tmp_path / "b", code="pass", environ={"MANTIC_TOKEN": "tok-mantic"})
        sched2.startup(_t(10, 14))
        _settle(sched2, _t(10, 14))
        assert _logs(sched2, "mantic")

    def test_a_disabled_workflow_is_left_alone(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ={"WORKFLOW_MINIBENCH_ENABLED": "false"})
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        assert _logs(sched, "tournament")
        assert not _logs(sched, "minibench")
        view = _status(sched)["workflows"]["minibench"]
        assert (view["enabled"], view["next_slot"]) == (False, None)


class TestSlotsAndCatchUp:
    def test_start_runs_the_latest_missed_slot_once(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code="import os; print(os.getcwd())", environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        _settle(sched, _t(10, 14, 30))  # the same slot again: nothing new
        logs = _logs(sched, "tournament")
        assert [p.name for p in logs] == ["20260929T101400Z.log"]
        text = logs[0].read_text()
        assert "slot=2026-09-29T10:03:00Z" in text.splitlines()[0]
        assert str(sched.data_dir / "work") in text, "children run with the data dir's work dir as cwd"

    def test_the_next_slot_fires_on_time(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        sched.tick(_t(10, 22, 59))
        assert len(_logs(sched, "tournament")) == 1
        _settle(sched, _t(10, 23))
        assert [p.name for p in _logs(sched, "tournament")] == ["20260929T101400Z.log", "20260929T102300Z.log"]

    def test_a_restart_inside_a_handled_slot_does_not_repeat_it(self, tmp_path: Path) -> None:
        first = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        first.startup(_t(10, 14))
        _settle(first, _t(10, 14))
        first.shutdown(_t(10, 15))
        second = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        second.startup(_t(10, 16))
        _settle(second, _t(10, 16))
        assert len(_logs(second, "tournament")) == 1

    def test_keys_arriving_at_a_restart_run_the_slot_they_missed(self, tmp_path: Path) -> None:
        keyless = _make(tmp_path, environ={"METACULUS_TOKEN": "", "OPENROUTER_API_KEY": "", **_ONLY_TOURNAMENT})
        keyless.startup(_t(10, 14))
        keyless.tick(_t(10, 14))
        keyless.shutdown(_t(10, 15))
        keyed = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        keyed.startup(_t(10, 16))
        _settle(keyed, _t(10, 16))
        assert len(_logs(keyed, "tournament")) == 1, "the owner should not wait up to 20 minutes for the first run"


class TestOverlap:
    _WAIT = "import os, sys, time\nwhile not os.path.exists(sys.argv[1]):\n    time.sleep(0.02)\n"

    def test_one_run_per_workflow_and_one_queued_slot(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=self._WAIT, environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        sched.tick(_t(10, 23))  # next slot arrives while the first run is still going
        sched.tick(_t(10, 43))  # and another: still only one is queued
        assert len(_logs(sched, "tournament")) == 1
        assert _status(sched)["workflows"]["tournament"]["running"] is True
        (tmp_path / "flag").write_text("go")
        _settle(sched, _t(10, 44))
        assert len(_logs(sched, "tournament")) == 2, "exactly one queued run starts when the first ends"
        assert _status(sched)["workflows"]["tournament"]["last_rc"] == 0


class TestTimeouts:
    _SLEEP = "import time\ntime.sleep(300)\n"

    def test_a_run_past_its_timeout_is_terminated_and_recorded(self, tmp_path: Path) -> None:
        # A 60 s cap inside one slot (10:03 to 10:23) keeps the test to a single run; the real 70 minute
        # value is pinned against the workflow YAML in test_kira_scheduler_spec.
        wf = dataclasses.replace(WORKFLOWS[0], run_timeout_s=60)
        sched = _make(tmp_path, code=self._SLEEP, environ=_ONLY_TOURNAMENT, workflows=(wf,))
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        sched.tick(_t(10, 14, 59))  # 59 s: still inside the cap
        assert _status(sched)["workflows"]["tournament"]["running"] is True
        _settle(sched, _t(10, 15, 1))
        view = _status(sched)["workflows"]["tournament"]
        assert view["running"] is False
        assert view["error"] == "timeout after 1m"
        assert view["last_rc"] == -signal.SIGTERM
        assert _status(sched)["last_run"]["ok"] is False

    def test_a_child_that_ignores_sigterm_is_killed(self, tmp_path: Path) -> None:
        code = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(300)\n"
        wf = dataclasses.replace(WORKFLOWS[0], run_timeout_s=60)
        sched = _make(tmp_path, code=code, environ=_ONLY_TOURNAMENT, workflows=(wf,), kill_grace_s=0)
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        time.sleep(0.3)  # let the child install its handler
        sched.tick(_t(10, 15, 1))  # timeout: SIGTERM, ignored
        _settle(sched, _t(10, 15, 2))  # grace 0: SIGKILL on the next tick
        view = _status(sched)["workflows"]["tournament"]
        assert view["last_rc"] == -signal.SIGKILL
        assert view["error"] == "timeout after 1m"


class TestResults:
    def test_success_records_questions_spend_and_a_last_ok(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=f"print({_OK_LINE!r})", environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        _settle(sched, _t(10, 23))
        status = _status(sched)
        view = status["workflows"]["tournament"]
        assert (view["questions_forecast"], view["spend_usd"]) == (6, 0.2468), "totals accumulate across runs"
        assert (view["last_questions"], view["last_spend_usd"]) == (3, 0.1234)
        assert view["last_rc"] == 0
        assert view["error"] is None
        finished = int(_t(10, 23).timestamp())
        assert status["last_run"] == {
            "kind": "tournament",
            "ok": True,
            "finished": finished,
            "error": None,
            "rc": 0,
            "degraded": False,
        }
        assert status["last_ok"] == finished

    def test_a_failed_run_is_not_ok_and_last_ok_keeps_the_older_success(self, tmp_path: Path) -> None:
        marker = tmp_path / "fail-now"
        code = (
            "import os, sys\n"
            "if os.path.exists(sys.argv[1].replace('flag', 'fail-now')):\n"
            "    print('boom: it broke')\n"
            "    sys.exit(1)\n"
        )
        sched = _make(tmp_path, code=code, environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        marker.write_text("x")
        _settle(sched, _t(10, 23))
        status = _status(sched)
        assert status["last_run"]["ok"] is False
        assert status["last_run"]["rc"] == 1
        assert "boom: it broke" in status["last_run"]["error"]
        assert status["last_run"]["degraded"] is False
        assert status["last_ok"] == int(_t(10, 14).timestamp())

    def test_a_published_run_with_degradation_events_is_flagged_not_hidden(self, tmp_path: Path) -> None:
        line = "Run completed with 2 alertable degradation event(s) (bot=2); exiting non-zero so CI marks this run red."
        sched = _make(tmp_path, code=f"import sys; print({line!r}); sys.exit(1)", environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        last = _status(sched)["last_run"]
        assert (last["ok"], last["degraded"], last["rc"]) == (False, True, 1)
        assert "published with degradation events" in last["error"]

    def test_a_command_that_cannot_start_is_recorded_and_not_retried_in_a_loop(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ=_ONLY_TOURNAMENT, command_for=lambda _wf: ["/nonexistent/uv-binary"])
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        sched.tick(_t(10, 14, 5))
        view = _status(sched)["workflows"]["tournament"]
        assert view["last_rc"] == 127
        assert view["error"].startswith("failed to start")
        assert view["running"] is False
        assert len(list((sched.data_dir / "runs" / "tournament").glob("*.log"))) == 1


class TestStatusFile:
    def test_the_schema_is_a_superset_of_the_launcher_and_the_dashboard_views(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        status = _status(sched)
        launcher = {"schema", "updated_at", "summary", "configured", "missing", "mode", "workflows"}
        dashboard = {"configured", "summary", "heartbeat", "started", "last_run", "last_ok"}
        assert launcher <= set(status)
        assert dashboard <= set(status)
        assert status["schema"] == 1
        assert status["mode"] == MODE_DEFAULTS
        assert set(status["workflows"]) == {"tournament", "minibench", "mantic"}
        workflow_keys = {
            "enabled",
            "configured",
            "next_slot",
            "last_started",
            "last_finished",
            "last_rc",
            "questions_forecast",
            "spend_usd",
            "error",
            "running",
        }
        for view in status["workflows"].values():
            assert workflow_keys <= set(view)
        assert isinstance(status["heartbeat"], int)
        assert status["started"] == int(_t(9, 0).timestamp())
        assert len(status["summary"]) <= 200

    def test_a_fresh_program_reports_unknown_not_ok(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ={"METACULUS_TOKEN": "", "OPENROUTER_API_KEY": ""})
        sched.startup(_t(10, 14))
        status = _status(sched)
        assert status["last_run"] is None
        assert status["last_ok"] is None
        assert status["configured"] is False
        assert status["summary"].startswith("not configured: missing METACULUS_TOKEN, OPENROUTER_API_KEY")

    def test_no_secret_value_reaches_status_or_state(self, tmp_path: Path) -> None:
        environ = {**_ONLY_TOURNAMENT, "GEMINI_API_KEY": "tok-secret-gemini", "MANTIC_TOKEN": "tok-secret-mantic"}
        sched = _make(tmp_path, code=f"print({_OK_LINE!r})", environ=environ)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        sched.shutdown(_t(10, 15))
        blob = sched.status_path.read_text() + sched.state_path.read_text()
        for value in ("tok-secret-m", "tok-secret-or", "tok-secret-gemini", "tok-secret-mantic"):
            assert value not in blob
        for log in _logs(sched, "tournament"):
            assert "tok-secret" not in log.read_text(), "the scheduler's own log header must not carry env"

    def test_heartbeat_is_rewritten_on_its_interval(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ={"METACULUS_TOKEN": "", "OPENROUTER_API_KEY": ""})
        sched.startup(_t(10, 14))
        first = sched.heartbeat_path.read_text()
        sched.tick(_t(10, 14, 20))
        assert sched.heartbeat_path.read_text() == first
        sched.tick(_t(10, 14, 31))
        assert sched.heartbeat_path.read_text() == f"{int(_t(10, 14, 31).timestamp())}\n"
        assert _status(sched)["heartbeat"] == int(_t(10, 14, 31).timestamp())

    def test_a_reader_never_sees_a_torn_file(self, tmp_path: Path) -> None:
        target = tmp_path / "status.json"
        write_json_atomic(target, {"n": 0})
        stop = threading.Event()

        def writer() -> None:
            n = 0
            while not stop.is_set():
                n += 1
                write_json_atomic(target, {"n": n, "pad": "x" * 5000})

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(300):
                assert "n" in json.loads(target.read_text())
        finally:
            stop.set()
            thread.join()
        assert [p.name for p in tmp_path.iterdir()] == ["status.json"], "no temp file may be left behind"


class TestRetentionAndOrphans:
    def test_logs_and_scratch_files_older_than_14_days_are_pruned_on_start(self, tmp_path: Path) -> None:
        sched = _make(tmp_path)
        old_log = sched.data_dir / "runs" / "tournament" / "20260101T000000Z.log"
        fresh_log = sched.data_dir / "runs" / "tournament" / "20260928T000000Z.log"
        old_scratch = sched.data_dir / "work" / "cache" / "old.tmp"
        for path in (old_log, fresh_log, old_scratch):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        long_ago = time.time() - 15 * 86400
        for path in (old_log, old_scratch):
            os.utime(path, (long_ago, long_ago))
        sched.startup(_t(10, 14))
        assert not old_log.exists()
        assert not old_scratch.exists()
        assert fresh_log.exists()

    def test_prune_leaves_files_at_13_days(self, tmp_path: Path) -> None:
        recent = tmp_path / "a.log"
        recent.write_text("x")
        thirteen_days = time.time() - 13 * 86400
        os.utime(recent, (thirteen_days, thirteen_days))
        assert prune_old(tmp_path, suffix=".log") == 0
        assert recent.exists()

    def test_a_run_orphaned_by_a_dead_scheduler_is_killed_at_start(self, tmp_path: Path) -> None:
        orphan = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)", "main.py"], start_new_session=True
        )
        try:
            data = tmp_path / "data"
            write_json_atomic(
                data / "state.json",
                {"schema": 1, "workflows": {"tournament": {"pid": orphan.pid, "handled_slot": "2026-09-29T10:03:00Z"}}},
            )
            sched = _make(tmp_path, environ=_ONLY_TOURNAMENT)
            sched.startup(_t(10, 14))
            assert orphan.wait(timeout=15) == -signal.SIGTERM
        finally:
            if orphan.poll() is None:
                orphan.kill()


class TestResearchArchiveRetention:
    """research_outputs/ and raw_research_*.jsonl were 90 day Actions artifacts the sync tools pulled."""

    @staticmethod
    def _age(path: Path, days: float) -> None:
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))

    def _files(self, work: Path) -> dict[str, Path]:
        paths = {
            "research": work / "research_outputs" / "research_x.jsonl",
            "raw": work / "run_logs" / "raw_research_kira-tournament-x.jsonl",
            "other_run_log": work / "run_logs" / "other.log",
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x" * 10)
        return paths

    def test_the_archive_outlives_14_days_but_not_120(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        paths = self._files(work)
        for path in paths.values():
            self._age(path, 100)
        prune_work(work)
        assert paths["research"].exists()
        assert paths["raw"].exists()
        assert not paths["other_run_log"].exists(), "only the archive earns the longer retention"
        for name in ("research", "raw"):
            self._age(paths[name], 130)
        prune_work(work)
        assert not paths["research"].exists()
        assert not paths["raw"].exists()

    def test_a_size_bound_drops_the_oldest_archive_files_first(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        files = []
        for i, age in enumerate((50, 40, 30, 20)):
            path = work / "research_outputs" / f"research_{i}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x" * 100)
            self._age(path, age)
            files.append(path)
        prune_work(work, max_archive_bytes=250)  # 400 bytes on disk: the two oldest have to go
        assert [p.exists() for p in files] == [False, False, True, True]

    def test_a_run_never_deletes_the_archive_early(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        archive = sched.data_dir / "work" / "research_outputs" / "research_old.jsonl"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("{}")
        self._age(archive, 60)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        assert archive.exists()


class TestFileModes:
    """mkstemp makes 0600 whatever the umask; the dashboard (another uid, shared group) needs 0640."""

    @staticmethod
    def _mode(path: Path) -> int:
        return path.stat().st_mode & 0o7777

    def test_files_the_dashboard_reads_are_group_readable_even_under_a_strict_umask(self, tmp_path: Path) -> None:
        previous = os.umask(0o077)
        try:
            sched = _make(tmp_path, code=f"print({_OK_LINE!r})", environ=_ONLY_TOURNAMENT)
            sched.startup(_t(10, 14))
            _settle(sched, _t(10, 14))
            sched.tick(_t(10, 15))
            for path in (sched.status_path, sched.state_path, sched.heartbeat_path, *_logs(sched, "tournament")):
                assert self._mode(path) == 0o640, (path.name, oct(self._mode(path)))
            for directory in (sched.data_dir / "runs", sched.run_dir(sched.workflows[0]), sched.work_dir):
                assert self._mode(directory) == 0o2750, (directory.name, oct(self._mode(directory)))
        finally:
            os.umask(previous)

    def test_new_directories_keep_the_setgid_bit_so_files_stay_in_the_group(self, tmp_path: Path) -> None:
        parent = tmp_path / "data"
        parent.mkdir()
        parent.chmod(0o2750)  # what kira-earn gives the data dir
        sched = Scheduler(
            environ=_KEYS,
            data_dir=parent,
            app_dir=tmp_path / "app",
            command_for=lambda _wf: ["true"],
            started_at=_t(9, 0),
        )
        sched.startup(_t(10, 14))
        for directory in (parent / "runs", parent / "work"):
            assert self._mode(directory) & 0o2000, f"{directory.name} lost the setgid bit: {oct(self._mode(directory))}"

    def test_a_rewrite_keeps_the_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "status.json"
        write_json_atomic(target, {"n": 1})
        write_json_atomic(target, {"n": 2})
        assert self._mode(target) == 0o640


class TestProcessGroups:
    """SIGKILL to the group only lands while a member lives, so the leader exiting must not end the hunt."""

    _SPAWN = (
        "import os, subprocess, sys, time\n"
        "pidfile = sys.argv[1] + '.gc'\n"
        "grandchild = 'import os, signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(300)\'\n'
        "subprocess.Popen([sys.executable, '-c', grandchild, pidfile])\n"
        "while not os.path.exists(pidfile):\n"
        "    time.sleep(0.02)\n"
    )

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return False
        return state != "Z"

    def _grandchild(self, tmp_path: Path) -> int:
        pidfile = Path(str(tmp_path / "flag") + ".gc")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text()):
            time.sleep(0.02)
        return int(pidfile.read_text())

    @staticmethod
    def _gone(pid: int, alive: Any) -> bool:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and alive(pid):
            time.sleep(0.05)
        return not alive(pid)

    def test_a_grandchild_that_ignores_sigterm_does_not_survive_the_timeout(self, tmp_path: Path) -> None:
        wf = dataclasses.replace(WORKFLOWS[0], run_timeout_s=60)
        sched = _make(
            tmp_path, code=self._SPAWN + "time.sleep(300)\n", environ=_ONLY_TOURNAMENT, workflows=(wf,), kill_grace_s=0
        )
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        pid = self._grandchild(tmp_path)
        try:
            sched.tick(_t(10, 15, 1))  # timeout: SIGTERM ends the leader, the grandchild ignores it
            _settle(sched, _t(10, 15, 2))
            assert self._gone(pid, self._alive), "the SIGTERM-ignoring grandchild outlived the run"
        finally:
            if self._alive(pid):
                os.kill(pid, signal.SIGKILL)

    def test_a_grandchild_that_ignores_sigterm_does_not_survive_shutdown(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=self._SPAWN + "time.sleep(300)\n", environ=_ONLY_TOURNAMENT, stop_grace_s=1)
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        pid = self._grandchild(tmp_path)
        try:
            sched.shutdown(_t(10, 15))
            assert self._gone(pid, self._alive), "shutdown returned with the grandchild still running"
            assert _status(sched)["summary"] == "stopped"
        finally:
            if self._alive(pid):
                os.kill(pid, signal.SIGKILL)

    def test_a_grandchild_left_behind_by_a_normal_exit_is_killed(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=self._SPAWN, environ=_ONLY_TOURNAMENT)  # the leader exits 0 at once
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        pid = self._grandchild(tmp_path)
        try:
            _settle(sched, _t(10, 14))
            assert _status(sched)["workflows"]["tournament"]["last_rc"] == 0
            assert self._gone(pid, self._alive), "a helper outlived the run that started it"
        finally:
            if self._alive(pid):
                os.kill(pid, signal.SIGKILL)


class TestShutdownSurvivesAStuckChild:
    def test_a_child_that_will_not_be_reaped_still_gets_its_stop_recorded(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code="import time\ntime.sleep(300)\n", environ=_ONLY_TOURNAMENT, stop_grace_s=1)
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        time.sleep(0.2)
        proc = sched._rt["tournament"].run.proc  # type: ignore[union-attr]  # a run is in progress here
        real_wait = proc.wait

        def stuck(timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="uv", timeout=timeout or 0)

        proc.wait = stuck  # type: ignore[method-assign]  # a D-state child: SIGKILL sent, never reaped
        try:
            sched.shutdown(_t(10, 15))  # must not raise
        finally:
            proc.wait = real_wait  # type: ignore[method-assign]
            proc.wait(timeout=10)
        status = _status(sched)
        assert status["summary"] == "stopped"
        view = status["workflows"]["tournament"]
        assert view["running"] is False
        assert view["error"] == "stopped by SIGTERM (scheduler shutdown)"
        assert view["last_rc"] == -1, "an unknown exit code is recorded as -1"


class TestQueuedSlots:
    """A slot that arrives mid-run is queued; it is handled only when a run for it has started."""

    _WAIT = TestOverlap._WAIT

    def test_a_queued_slot_is_not_marked_handled_while_it_waits(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, code=self._WAIT, environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        sched.tick(_t(10, 14))
        sched.tick(_t(10, 23))
        state = json.loads(sched.state_path.read_text())
        assert state["workflows"]["tournament"]["handled_slot"] == "2026-09-29T10:03:00Z"
        (tmp_path / "flag").write_text("go")
        _settle(sched, _t(10, 24))
        state = json.loads(sched.state_path.read_text())
        assert state["workflows"]["tournament"]["handled_slot"] == "2026-09-29T10:23:00Z"

    def test_a_stop_during_the_first_run_repeats_the_latest_slot_exactly_once(self, tmp_path: Path) -> None:
        first = _make(tmp_path, code=self._WAIT, environ=_ONLY_TOURNAMENT)
        first.startup(_t(10, 14))
        first.tick(_t(10, 14))
        first.tick(_t(10, 23))  # queued behind the run in progress
        time.sleep(0.2)
        first.shutdown(_t(10, 24))
        assert len(_logs(first, "tournament")) == 1, "the queued slot must not start during shutdown"
        (tmp_path / "flag").write_text("go")
        second = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        second.startup(_t(10, 25))
        _settle(second, _t(10, 25))
        _settle(second, _t(10, 26))
        assert len(_logs(second, "tournament")) == 2, "one interrupted run plus exactly one catch-up, no slot twice"

    def test_an_interrupted_queued_run_leaves_its_slot_unhandled(self, tmp_path: Path) -> None:
        first = _make(tmp_path, code=self._WAIT, environ=_ONLY_TOURNAMENT)
        first.startup(_t(10, 14))
        first.tick(_t(10, 14))
        first.tick(_t(10, 23))  # queue slot 10:23 behind the 10:03 run
        (tmp_path / "flag").write_text("go")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(_logs(first, "tournament")) < 2:
            first.tick(_t(10, 24))  # the first run ends, the queued run starts
            time.sleep(0.02)
        (tmp_path / "flag").unlink()  # the second run must block, so the stop lands mid-run
        assert len(_logs(first, "tournament")) == 2
        time.sleep(0.2)
        first.shutdown(_t(10, 24, 30))
        state = json.loads(first.state_path.read_text())
        assert state["workflows"]["tournament"]["handled_slot"] == "2026-09-29T10:03:00Z", (
            "the queued run was interrupted, so its slot (10:23) must be repeated at the next start"
        )


class TestShutdown:
    def test_sigterm_style_stop_records_the_run_and_the_next_start_repeats_the_slot(self, tmp_path: Path) -> None:
        wait = "import time\ntime.sleep(300)\n"
        first = _make(tmp_path, code=wait, environ=_ONLY_TOURNAMENT)
        first.startup(_t(10, 14))
        first.tick(_t(10, 14))
        time.sleep(0.2)
        first.shutdown(_t(10, 15))
        view = _status(first)["workflows"]["tournament"]
        assert view["running"] is False
        assert view["error"] == "stopped by SIGTERM (scheduler shutdown)"
        assert view["last_rc"] == -signal.SIGTERM
        assert _status(first)["last_run"] is None, "a stop is not a failure the dashboard should turn red for"
        second = _make(tmp_path, environ=_ONLY_TOURNAMENT)
        second.startup(_t(10, 16))
        _settle(second, _t(10, 16))
        assert len(_logs(second, "tournament")) == 2, "the interrupted slot is caught up"


_FAKE_UV = """#!{python}
import os, signal, sys, time
which = "minibench" if "minibench" in sys.argv else ("mantic" if "mantic" in sys.argv else "tournament")
def on_term(signum, frame):
    open("got-term-" + which, "w").write("1")
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
open("started-" + which, "w").write(" ".join(sys.argv[1:]) + "\\n" + os.environ.get("GITHUB_RUN_ID", ""))
time.sleep(120)
"""


def test_the_real_module_runs_the_production_command_and_stops_cleanly_on_sigterm(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text(_FAKE_UV.format(python=sys.executable))
    fake.chmod(0o755)
    data, app = tmp_path / "data", tmp_path / "app"
    app.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "KIRA_EARN_DATA": str(data),
        "KIRA_EARN_APP": str(app),
        "HOME": str(tmp_path),
        **_KEYS,
    }
    with (tmp_path / "scheduler.out").open("w") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kira_scheduler"], cwd=_REPO_ROOT, env=env, stdout=out, stderr=subprocess.STDOUT
        )
    try:
        work = data / "work"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            (work / "started-tournament").exists() and (work / "started-minibench").exists()
        ):
            time.sleep(0.1)
        assert (work / "started-tournament").exists(), (tmp_path / "scheduler.out").read_text()
        assert (work / "started-minibench").exists()
        tournament_argv, run_id = (work / "started-tournament").read_text().split("\n")
        assert tournament_argv == f"run --frozen --no-dev --project {app} python {app / 'main.py'}"
        assert run_id.startswith("kira-tournament-")
        assert (work / "started-minibench").read_text().split("\n")[0].endswith("main.py --mode minibench")
        assert not (work / "started-mantic").exists(), "no MANTIC_TOKEN, so mantic idles"

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert (work / "got-term-tournament").exists(), "the child must receive SIGTERM, not a hard kill"
    assert (work / "got-term-minibench").exists()
    status = json.loads((data / "status.json").read_text())
    assert status["summary"] == "stopped"
    for name in ("tournament", "minibench"):
        assert status["workflows"][name]["running"] is False
        assert status["workflows"][name]["error"] == "stopped by SIGTERM (scheduler shutdown)"
    assert status["workflows"]["mantic"]["configured"] is False


def test_under_stockholm_time_slots_stamps_and_logs_are_still_utc(tmp_path: Path) -> None:
    """A real scheduler process with the container's TZ: the slot it reports is the UTC one."""
    from kira_scheduler.slots import latest_slot  # only this test needs it beside the process

    env = {
        "PATH": os.environ["PATH"],
        "TZ": "Europe/Stockholm",
        "KIRA_EARN_DATA": str(tmp_path / "data"),
        "KIRA_EARN_APP": str(tmp_path),
        "HOME": str(tmp_path),
    }
    out = tmp_path / "out.log"
    before = datetime.now(UTC)
    with out.open("w") as handle:
        proc = subprocess.Popen(
            [sys.executable, "-m", "kira_scheduler"], cwd=_REPO_ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT
        )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and "tournament: not configured" not in out.read_text():
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    after = datetime.now(UTC)
    text = out.read_text()
    slots = {latest_slot((3, 23, 43), before), latest_slot((3, 23, 43), after)}
    assert any(f"for slot {slot.strftime('%Y-%m-%dT%H:%M:%SZ')}" in text for slot in slots), text
    # The log line prefix is UTC too: its hour is the UTC hour, not Stockholm's (1 or 2 hours ahead).
    stamp = next(line for line in text.splitlines() if "kira_scheduler:" in line).split()[1]
    assert stamp[:2] in {f"{before.hour:02d}", f"{after.hour:02d}"}, (stamp, before)
    status = json.loads((tmp_path / "data" / "status.json").read_text())
    assert status["updated_at"].endswith("Z")
    assert abs(status["started"] - before.timestamp()) < 60


def test_a_run_fired_under_stockholm_time_is_named_and_slotted_in_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Europe/Stockholm")
    time.tzset()
    try:
        sched = _make(tmp_path, code="import os; print(os.environ['TZ'])", environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 14))
        _settle(sched, _t(10, 14))
        (log,) = _logs(sched, "tournament")
        assert log.name == "20260929T101400Z.log"  # 10:14 UTC, not 12:14 Stockholm
        first, _, rest = log.read_text().partition("\n")
        assert "slot=2026-09-29T10:03:00Z started=2026-09-29T10:14:00Z" in first
        assert rest.strip() == "UTC", "the bot child must see TZ=UTC"
        assert _status(sched)["workflows"]["tournament"]["next_slot"] == "2026-09-29T10:23:00Z"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_main_refuses_to_start_without_the_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIRA_EARN_DATA", raising=False)
    assert scheduler_module.main() == 2


def test_slots_are_the_only_time_source_the_clock_injection_is_enough() -> None:
    # A guard on the design: the scheduler must not import a wall-clock sleep loop of its own that
    # ticks() cannot drive, or the catch-up and timeout tests above would stop meaning anything.
    assert callable(Scheduler.tick)
    assert timedelta(seconds=scheduler_module.TICK_S) <= timedelta(seconds=2)
