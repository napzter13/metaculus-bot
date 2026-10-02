"""Network blips: one is invisible, two in a row is amber, four is red, and nothing else counts.

2026-10-02 07:43Z: the home WAN reconnected and DNS failed for one slot. The bot now skips such a run
(exit 0 and a ``TRANSIENT_NETWORK_SKIP`` line) and the scheduler counts consecutive skips per workflow
across slots. These tests use throwaway children that print that line, so the counting, the status
fields and the escalation are pinned without any network or bot code.
"""

from __future__ import annotations

import dataclasses
import signal
import sys
import time
from pathlib import Path
from typing import Any

from test_kira_scheduler_runtime import _KEYS, _OK_LINE, _ONLY_TOURNAMENT, _logs, _make, _settle, _status, _t

from kira_scheduler.scheduler import (
    TRANSIENT_AMBER_STREAK,
    TRANSIENT_RED_STREAK,
    Scheduler,
)
from kira_scheduler.spec import WORKFLOWS, Workflow

_BLIP = (
    "TRANSIENT_NETWORK_SKIP: stage=preflight error=TransientNetworkError "
    "detail='API identity preflight for www.metaculus.com could not reach it'; the run is skipped and the next slot retries"
)
_BLIP_CHILD = f"print({_BLIP!r})"
# Slots of the tournament are :03 :23 :43; a tick a minute after each starts exactly one run.
_SLOT_TICKS = [_t(10, 4), _t(10, 24), _t(10, 44), _t(11, 4), _t(11, 24), _t(11, 44)]


def _run_slots(sched: Scheduler, count: int) -> None:
    for moment in _SLOT_TICKS[:count]:
        _settle(sched, moment)


def _blipping(tmp_path: Path, **kwargs: Any) -> Scheduler:
    sched = _make(tmp_path, code=_BLIP_CHILD, environ=_ONLY_TOURNAMENT, **kwargs)
    sched.startup(_t(10, 3, 30))
    return sched


class TestThresholds:
    def test_the_thresholds_are_two_and_four(self) -> None:
        assert (TRANSIENT_AMBER_STREAK, TRANSIENT_RED_STREAK) == (2, 4)

    def test_one_blip_is_not_a_failed_run(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 1)
        status = _status(sched)
        view = status["workflows"]["tournament"]
        assert view["last_rc"] == 0, "the bot exited 0: the run was skipped, not failed"
        assert view["transient"] is True
        assert view["transient_streak"] == 1
        assert view["error"].startswith("transient network failure at preflight (TransientNetworkError), 1 in a row")
        assert view["health"] == "green"
        assert status["health"] == "green"
        assert status["last_run"]["ok"] is True
        assert status["last_run"]["transient"] is True
        assert not status["summary"].startswith(("AMBER", "RED"))
        assert "network blip x1" in status["summary"]

    def test_a_blip_is_not_a_successful_forecast_run(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 1)
        assert _status(sched)["last_ok"] is None, "last_ok only advances for a run that actually forecast"

    def test_two_in_a_row_across_slots_is_amber(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 2)
        status = _status(sched)
        assert status["workflows"]["tournament"]["transient_streak"] == 2
        assert status["workflows"]["tournament"]["health"] == "amber"
        assert status["health"] == "amber"
        assert status["transient_streak"] == 2
        assert status["summary"].startswith("AMBER: ")
        assert status["last_run"]["ok"] is True, "amber is advisory; the run itself is still not a failure"

    def test_three_is_still_amber(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 3)
        assert _status(sched)["health"] == "amber"

    def test_four_in_a_row_is_red_and_fails_last_run(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 4)
        status = _status(sched)
        assert status["health"] == "red"
        assert status["workflows"]["tournament"]["health"] == "red"
        assert status["summary"].startswith("RED: ")
        assert status["last_run"]["ok"] is False, "the dashboard's existing red rule keys on last_run.ok"
        assert status["last_run"]["transient"] is True
        assert "4 in a row" in status["last_run"]["error"]
        assert status["last_run"]["rc"] == 0


class TestWhatEndsAStreak:
    @staticmethod
    def _switching_child(tmp_path: Path) -> Any:
        """A child that blips until ``mode`` holds ``ok`` / ``fail``, so one scheduler walks both."""
        return (
            "import os, sys\n"
            "mode = open(sys.argv[1].replace('flag', 'mode')).read().strip() if os.path.exists(sys.argv[1].replace('flag', 'mode')) else 'blip'\n"
            f"line = {_BLIP!r}\n"
            "if mode == 'blip':\n    print(line)\n"
            f"elif mode == 'ok':\n    print({_OK_LINE!r})\n"
            "elif mode == 'fail':\n    print(line)\n    sys.exit(1)\n"
        )

    def _walk(self, tmp_path: Path, modes: list[str]) -> Scheduler:
        sched = _make(tmp_path, code=self._switching_child(tmp_path), environ=_ONLY_TOURNAMENT)
        sched.startup(_t(10, 3, 30))
        for moment, mode in zip(_SLOT_TICKS, modes, strict=False):
            (tmp_path / "mode").write_text(mode)
            _settle(sched, moment)
        return sched

    def test_a_successful_run_resets_the_streak(self, tmp_path: Path) -> None:
        sched = self._walk(tmp_path, ["blip", "blip", "blip", "ok"])
        status = _status(sched)
        assert status["workflows"]["tournament"]["transient_streak"] == 0
        assert status["health"] == "green"
        assert status["last_run"]["ok"] is True
        assert status["last_run"]["transient"] is False
        assert status["last_ok"] == int(_t(11, 4).timestamp())

    def test_a_blip_after_a_success_keeps_the_older_last_ok(self, tmp_path: Path) -> None:
        sched = self._walk(tmp_path, ["ok", "blip"])
        status = _status(sched)
        assert status["last_ok"] == int(_t(10, 4).timestamp())
        assert status["workflows"]["tournament"]["transient_streak"] == 1

    def test_a_real_failure_ends_the_streak_and_is_red_not_a_blip(self, tmp_path: Path) -> None:
        sched = self._walk(tmp_path, ["blip", "blip", "fail"])
        status = _status(sched)
        view = status["workflows"]["tournament"]
        assert view["transient_streak"] == 0, "exit 1 is a real failure even if the log also shows the marker"
        assert view["transient"] is False
        assert view["health"] == "red"
        assert status["last_run"]["ok"] is False
        assert status["last_run"]["rc"] == 1

    def test_a_timeout_is_never_a_blip(self, tmp_path: Path) -> None:
        wf = dataclasses.replace(WORKFLOWS[0], run_timeout_s=60)
        code = f"print({_BLIP!r}, flush=True)\nimport time\ntime.sleep(300)\n"
        sched = _make(tmp_path, code=code, environ=_ONLY_TOURNAMENT, workflows=(wf,))
        sched.startup(_t(10, 3, 30))
        sched.tick(_t(10, 4))
        time.sleep(0.3)
        _settle(sched, _t(10, 5, 5))
        view = _status(sched)["workflows"]["tournament"]
        assert view["error"] == "timeout after 1m"
        assert (view["transient"], view["transient_streak"]) == (False, 0)

    def test_a_stop_leaves_the_streak_alone(self, tmp_path: Path) -> None:
        sched = _make(
            tmp_path, code=f"print({_BLIP!r}, flush=True)\nimport time\ntime.sleep(300)\n", environ=_ONLY_TOURNAMENT
        )
        sched.startup(_t(10, 3, 30))
        sched._wstate(WORKFLOWS[0])["transient_streak"] = 3  # three blips already behind us
        sched.tick(_t(10, 4))
        time.sleep(0.3)
        sched.shutdown(_t(10, 5))
        view = _status(sched)["workflows"]["tournament"]
        assert view["transient_streak"] == 3, "a stop says nothing about the network"
        assert view["transient"] is False
        assert view["error"] == "stopped by SIGTERM (scheduler shutdown)"
        assert view["last_rc"] == -signal.SIGTERM


class TestAcrossRestartsAndWorkflows:
    def test_the_streak_survives_a_restart(self, tmp_path: Path) -> None:
        first = _blipping(tmp_path)
        _run_slots(first, 2)
        first.shutdown(_t(10, 30))
        second = _make(tmp_path, code=_BLIP_CHILD, environ=_ONLY_TOURNAMENT)
        second.startup(_t(10, 31))
        assert _status(second)["health"] == "amber", "two blips before the restart are still two"
        _settle(second, _t(10, 44))
        assert _status(second)["workflows"]["tournament"]["transient_streak"] == 3

    def test_a_stuck_workflow_is_not_hidden_by_another_workflows_clean_run(self, tmp_path: Path) -> None:
        def command_for(wf: Workflow) -> list[str]:
            body = _BLIP_CHILD if wf.name == "tournament" else f"print({_OK_LINE!r})"
            return [sys.executable, "-c", body]

        sched = Scheduler(
            environ={**_KEYS, "WORKFLOW_MANTIC_ENABLED": "false"},
            data_dir=tmp_path / "data",
            app_dir=tmp_path / "app",
            command_for=command_for,
            started_at=_t(9, 0),
        )
        sched.startup(_t(10, 3, 30))
        for moment in (_t(10, 14), _t(10, 24), _t(10, 44), _t(11, 4)):
            _settle(sched, moment)  # tournament blips four times; minibench succeeds on its own slots
        _settle(sched, _t(11, 9))  # minibench (:08) finishes LAST with a clean run
        status = _status(sched)
        assert status["workflows"]["minibench"]["health"] == "green"
        assert status["workflows"]["tournament"]["health"] == "red"
        assert status["health"] == "red"
        assert status["last_run"]["kind"] == "tournament"
        assert status["last_run"]["ok"] is False, "the red workflow must be what the single last_run field shows"

    def test_a_program_without_keys_is_waiting_not_green(self, tmp_path: Path) -> None:
        sched = _make(tmp_path, environ={"METACULUS_TOKEN": "", "OPENROUTER_API_KEY": ""})
        sched.startup(_t(10, 3, 30))
        assert _status(sched)["health"] == "waiting"
        assert _status(sched)["transient_streak"] == 0

    def test_the_log_of_a_blip_run_is_kept(self, tmp_path: Path) -> None:
        sched = _blipping(tmp_path)
        _run_slots(sched, 1)
        (log,) = _logs(sched, "tournament")
        assert "TRANSIENT_NETWORK_SKIP" in log.read_text()
