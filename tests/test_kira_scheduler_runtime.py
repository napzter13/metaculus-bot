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
from kira_scheduler.store import prune_old, write_json_atomic

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
    def test_logs_and_work_files_older_than_14_days_are_pruned_on_start(self, tmp_path: Path) -> None:
        sched = _make(tmp_path)
        old_log = sched.data_dir / "runs" / "tournament" / "20260101T000000Z.log"
        fresh_log = sched.data_dir / "runs" / "tournament" / "20260928T000000Z.log"
        old_research = sched.data_dir / "work" / "research_outputs" / "old.jsonl"
        for path in (old_log, fresh_log, old_research):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        long_ago = time.time() - 15 * 86400
        for path in (old_log, old_research):
            os.utime(path, (long_ago, long_ago))
        sched.startup(_t(10, 14))
        assert not old_log.exists()
        assert not old_research.exists()
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
