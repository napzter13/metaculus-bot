"""The scheduler's workflow table against the workflow YAML, the child env, the gate and the manifest.

The table in ``kira_scheduler.spec`` is a hand-kept twin of each ``Run bot`` step, so the tests parse
the YAML and fail on any difference: a key added to a workflow but not to the table, a secret
mapped differently, a command or timeout that moved. The slots are the one thing the YAML no longer
holds (the ``schedule:`` triggers moved to Kira), and tests/test_kira_scheduler_slots.py pins them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from kira_scheduler.env import (
    build_child_env,
    effective_mode,
    missing_keys,
    program_missing,
    workflow_enabled,
    workflow_missing,
)
from kira_scheduler.spec import (
    MODE_DEFAULTS,
    PROGRAM_GATE,
    SECRET_ALIASES,
    WORKFLOWS,
    Workflow,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"
_SECRET_RE = re.compile(r"^\$\{\{ secrets\.(\w+) \}\}$")
_VAR_RE = re.compile(r"^\$\{\{ vars\.(\w+) \|\| '([^']*)' \}\}$")
_BY_NAME = {wf.name: wf for wf in WORKFLOWS}


def _bot_step(wf: Workflow) -> dict[str, Any]:
    workflow = yaml.safe_load((_WORKFLOW_DIR / wf.workflow_file).read_text())
    steps = [s for s in workflow["jobs"]["forecast_job"]["steps"] if s.get("name") == "Run bot"]
    assert len(steps) == 1
    return steps[0]


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda w: w.name)
class TestTableMatchesTheWorkflow:
    def test_same_env_keys(self, wf: Workflow) -> None:
        assert {e.target for e in wf.env} == set(_bot_step(wf)["env"])

    def test_same_env_mapping(self, wf: Workflow) -> None:
        by_target = {e.target: e for e in wf.env}
        for key, raw in _bot_step(wf)["env"].items():
            assert isinstance(raw, str), f"{key}: quote the value so YAML does not turn it into a bool"
            entry = by_target[key]
            if match := _SECRET_RE.match(raw):
                assert entry.kind == "secret", key
                assert entry.source == SECRET_ALIASES.get(match.group(1), match.group(1)), key
            elif match := _VAR_RE.match(raw):
                assert entry.kind == "var", key
                assert entry.source == match.group(1) == key, key
            else:
                assert entry.kind == "literal", key
                assert entry.value == raw, key

    def test_same_command_and_timeout(self, wf: Workflow) -> None:
        step = _bot_step(wf)
        command = re.search(r"uv run --frozen --no-dev python main\.py([^|]*?) 2>&1", step["run"])
        assert command is not None
        assert tuple(command.group(1).split()) == wf.args
        assert step["timeout-minutes"] * 60 == wf.run_timeout_s

    def test_actions_no_longer_schedules_it(self, wf: Workflow) -> None:
        workflow: Any = yaml.safe_load((_WORKFLOW_DIR / wf.workflow_file).read_text())
        triggers = workflow.get("on") or workflow.get(True)
        assert "schedule" not in triggers, "Actions would spend beside Kira; the schedule lives in kira_scheduler"
        assert "workflow_dispatch" in triggers, "a manual run stays available"


_KEYS = {
    "METACULUS_TOKEN": "tok-m",
    "OPENROUTER_API_KEY": "tok-or",
    "OAI_ANTH_OPENROUTER_KEY": "tok-donated",
    "MANTIC_TOKEN": "tok-mantic",
    "GEMINI_API_KEY": "tok-gemini",
    "EXA_API_KEY": "tok-exa",
    "ASKNEWS_CLIENT_ID": "an-id",
    "UNRELATED_SECRET": "must-not-leak",
    "PATH": "/usr/bin",
    "HOME": "/home/x",
    "UV_PROJECT_ENVIRONMENT": "/opt/venv",
    "PLAYWRIGHT_BROWSERS_PATH": "/data/pw",
    "KIRA_EARN_DATA": "/data",
}


class TestChildEnv:
    def test_secrets_copy_and_gemini_maps_to_google(self) -> None:
        env = build_child_env(_BY_NAME["tournament"], _KEYS, "kira-tournament-x")
        assert env["METACULUS_TOKEN"] == "tok-m"
        assert env["GOOGLE_API_KEY"] == "tok-gemini"
        assert "GEMINI_API_KEY" not in env
        assert env["EXA_API_KEY"] == "tok-exa"
        assert env["OAI_ANTH_OPENROUTER_KEY"] == "tok-donated"

    def test_an_unset_secret_is_passed_empty_like_actions(self) -> None:
        env = build_child_env(_BY_NAME["tournament"], _KEYS, "r")
        assert env["FRED_API_KEY"] == ""
        assert env["PERPLEXITY_API_KEY"] == ""

    def test_children_only_inherit_the_allowlist(self) -> None:
        env = build_child_env(_BY_NAME["tournament"], _KEYS, "r")
        assert "UNRELATED_SECRET" not in env
        assert "MANTIC_TOKEN" not in env, "a tournament run must not hold the Mantic token"
        for kept in ("PATH", "HOME", "UV_PROJECT_ENVIRONMENT", "PLAYWRIGHT_BROWSERS_PATH", "KIRA_EARN_DATA"):
            assert kept in env

    def test_mantic_is_personal_keys_only(self) -> None:
        env = build_child_env(_BY_NAME["mantic"], _KEYS, "r")
        assert env["MANTIC_TOKEN"] == "tok-mantic"
        assert "METACULUS_TOKEN" not in env
        assert "OAI_ANTH_OPENROUTER_KEY" not in env
        assert env["DONATED_OPENROUTER_KEY_ENABLED"] == "false"
        assert env["GEMINI_USE_DONATED_OPENROUTER_KEY"] == "false"

    def test_mode_flags_default_to_the_zero_credit_posture(self) -> None:
        env = build_child_env(_BY_NAME["tournament"], {}, "r")
        for name, default in MODE_DEFAULTS.items():
            assert env[name] == default, name

    def test_an_env_file_value_overrides_and_an_empty_one_does_not(self) -> None:
        env = build_child_env(_BY_NAME["minibench"], {"GAP_FILL_V2_ENABLED": "true", "SUPPORT_MODEL_ROUTE": "  "}, "r")
        assert env["GAP_FILL_V2_ENABLED"] == "true"
        assert env["SUPPORT_MODEL_ROUTE"] == "free"

    def test_mantic_ignores_the_mode_flags_like_its_workflow(self) -> None:
        env = build_child_env(_BY_NAME["mantic"], {"FORECASTER_FREE_TIER_ENABLED": "true"}, "r")
        assert "FORECASTER_FREE_TIER_ENABLED" not in env
        assert env["GAP_FILL_V2_ENABLED"] == "true"

    def test_the_child_is_pinned_to_utc_whatever_the_container_tz_is(self) -> None:
        env = build_child_env(_BY_NAME["tournament"], {**_KEYS, "TZ": "Europe/Stockholm"}, "r")
        assert env["TZ"] == "UTC", "Actions runners were UTC; the container's Stockholm TZ must not reach the bot"

    def test_each_run_gets_its_own_run_id(self) -> None:
        one = build_child_env(_BY_NAME["tournament"], {}, "kira-tournament-a")
        two = build_child_env(_BY_NAME["tournament"], {}, "kira-tournament-b")
        assert one["GITHUB_RUN_ID"] != two["GITHUB_RUN_ID"]

    def test_effective_mode_reports_the_flags_without_secrets(self) -> None:
        mode = effective_mode({"GAP_FILL_ENABLED": "true", "METACULUS_TOKEN": "tok"})
        assert mode["GAP_FILL_ENABLED"] == "true"
        assert set(mode) == set(MODE_DEFAULTS)
        assert "tok" not in json.dumps(mode)


class TestGate:
    def test_program_gate_is_the_manifest_gate(self) -> None:
        assert PROGRAM_GATE == ("METACULUS_TOKEN", "OPENROUTER_API_KEY")
        assert program_missing({}) == list(PROGRAM_GATE)
        assert program_missing({"METACULUS_TOKEN": "t"}) == ["OPENROUTER_API_KEY"]
        assert program_missing({"METACULUS_TOKEN": "t", "OPENROUTER_API_KEY": "k"}) == []

    def test_blank_and_whitespace_count_as_unset(self) -> None:
        assert missing_keys(("A", "B"), {"A": "  ", "B": ""}) == ["A", "B"]

    def test_mantic_needs_its_own_token_and_the_personal_key(self) -> None:
        mantic = _BY_NAME["mantic"]
        keys = {"METACULUS_TOKEN": "t", "OPENROUTER_API_KEY": "k"}
        assert workflow_missing(mantic, keys) == ["MANTIC_TOKEN"]
        assert workflow_missing(mantic, {**keys, "MANTIC_TOKEN": "m"}) == []
        assert workflow_missing(_BY_NAME["tournament"], keys) == []

    def test_switches_default_on_and_accept_off(self) -> None:
        minibench = _BY_NAME["minibench"]
        assert workflow_enabled(minibench, {})
        assert not workflow_enabled(minibench, {"WORKFLOW_MINIBENCH_ENABLED": "false"})
        assert workflow_enabled(minibench, {"WORKFLOW_MINIBENCH_ENABLED": "true"})


class TestManifest:
    manifest: dict[str, Any] = json.loads((_REPO_ROOT / "kira-earn.json").read_text())

    def test_identity_matches_the_contract(self) -> None:
        m = self.manifest
        assert (m["schema"], m["name"], m["uid"], m["runtime"]) == (1, "metaculus-bot", 1103, "python")
        assert (m["money"], m["armed_default"], m["litellm"], m["ports"]) == (False, True, False, [])
        assert m["status_file"] == "status.json"
        assert m["heartbeat"] == {"file": "heartbeat", "max_age_s": 900}
        assert m["stop_wait_s"] == 90

    def test_run_command_starts_the_scheduler_module(self) -> None:
        assert self.manifest["run"][-2:] == ["-m", "kira_scheduler"]
        assert (_REPO_ROOT / "kira_scheduler" / "__main__.py").is_file()

    def test_setup_syncs_then_installs_chromium_without_root(self) -> None:
        setup = self.manifest["setup"]
        assert setup[0] == ["uv", "sync", "--frozen", "--no-dev"]
        assert "playwright install chromium" in " ".join(setup[1])
        assert "--with-deps" not in " ".join(setup[1]), "the program uid cannot apt-get"

    def test_gate_keys_are_exactly_the_program_gate(self) -> None:
        gates = [e["name"] for e in self.manifest["env_required"] if e.get("gate")]
        assert tuple(gates) == PROGRAM_GATE

    def test_defaults_are_the_zero_credit_posture(self) -> None:
        assert self.manifest["env_defaults"] == MODE_DEFAULTS

    def test_every_secret_a_workflow_reads_is_listed_with_a_where(self) -> None:
        listed = {e["name"]: e for e in [*self.manifest["env_required"], *self.manifest["env_optional"]]}
        sources = {e.source for wf in WORKFLOWS for e in wf.env if e.kind == "secret"}
        assert sources <= set(listed), sorted(sources - set(listed))
        assert all(entry["where"] for entry in listed.values())
        switches = {wf.enable_var for wf in WORKFLOWS}
        assert switches <= set(listed)
        assert set(MODE_DEFAULTS) <= set(listed), "the mode flags must be editable through kira-secrets"
