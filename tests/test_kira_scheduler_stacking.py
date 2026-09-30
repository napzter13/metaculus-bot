"""On Kira the published forecast is the MEDIAN: the paid stacker cannot be reached, funded or not.

The bot's strategy is hard-coded ``CONDITIONAL_STACKING`` (cli.py), so every run log says
``Aggregation: conditional_stacking`` and names an Opus stacker. That line is the configured strategy,
not what runs: the stacker fires only when the spread is high AND the per-type
``<TYPE>_STACKING_ENABLED`` flag is ``true``. The scheduler pins all three to ``false`` for every
workflow, whatever the Kira env file says, so stacking cannot be turned on by credits, by a mode
flag or by a stray variable. These tests pin that chain end to end.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from forecasting_tools import (
    BinaryQuestion,
    DateQuestion,
    DiscreteQuestion,
    MultipleChoiceQuestion,
    NumericQuestion,
)
from test_conditional_stacking import (  # sibling test module: its helpers build the same bot the route tests use
    _HIGH_SPREAD_BINARY,
    _make_binary_question,
    _make_bot,
    mock_stacking_pipeline,
)

from kira_scheduler.env import build_child_env
from kira_scheduler.spec import WORKFLOWS, Workflow
from metaculus_bot.constants import (
    BINARY_STACKING_ENABLED_ENV,
    MC_STACKING_ENABLED_ENV,
    NUMERIC_STACKING_ENABLED_ENV,
)
from metaculus_bot.stacking_route import _type_gate_enabled

_FLAGS = (BINARY_STACKING_ENABLED_ENV, MC_STACKING_ENABLED_ENV, NUMERIC_STACKING_ENABLED_ENV)
_TURNED_ON = dict.fromkeys(_FLAGS, "true")


def _apply_child_env(monkeypatch: pytest.MonkeyPatch, wf: Workflow, kira_env: dict[str, str]) -> None:
    """Put the stacking flags the scheduler would hand the bot into the process env (over conftest's)."""
    child = build_child_env(wf, kira_env, "r")
    for flag in _FLAGS:
        monkeypatch.setenv(flag, child[flag])


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda w: w.name)
class TestTheSchedulerPinsStackingOff:
    def test_off_by_default(self, wf: Workflow) -> None:
        child = build_child_env(wf, {}, "r")
        assert [child[flag] for flag in _FLAGS] == ["false"] * 3

    def test_off_even_when_the_env_file_turns_it_on(self, wf: Workflow) -> None:
        child = build_child_env(wf, _TURNED_ON, "r")
        assert [child[flag] for flag in _FLAGS] == ["false"] * 3, "stacking must not be switchable from the env"

    def test_off_with_funded_keys_and_the_paid_mode(self, wf: Workflow) -> None:
        funded = {
            **_TURNED_ON,
            "OPENROUTER_API_KEY": "k",
            "OAI_ANTH_OPENROUTER_KEY": "donated",
            "FORECASTER_FREE_TIER_ENABLED": "false",
            "SUPPORT_MODEL_ROUTE": "paid",
            "NATIVE_SEARCH_ENABLED": "true",
            "GAP_FILL_ENABLED": "true",
            "GAP_FILL_V2_ENABLED": "true",
        }
        child = build_child_env(wf, funded, "r")
        assert [child[flag] for flag in _FLAGS] == ["false"] * 3


class TestTheBotHonoursThePinnedFlags:
    @pytest.mark.parametrize(
        "spec",
        [BinaryQuestion, MultipleChoiceQuestion, NumericQuestion, DiscreteQuestion, DateQuestion],
        ids=lambda s: s.__name__,
    )
    @pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda w: w.name)
    def test_every_question_type_is_gated_off_under_the_kira_env(
        self, monkeypatch: pytest.MonkeyPatch, wf: Workflow, spec: type
    ) -> None:
        _apply_child_env(monkeypatch, wf, _TURNED_ON)
        assert _type_gate_enabled(Mock(spec=spec)) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda w: w.name)
    async def test_a_high_spread_question_is_not_stacked_and_no_stacker_call_is_made(
        self, monkeypatch: pytest.MonkeyPatch, wf: Workflow
    ) -> None:
        """The configured strategy is conditional stacking and the forecasters disagree enough to trigger it,
        yet the crux analysis, the targeted search and the stacker are never called, so a funded key cannot
        be billed for them."""
        _apply_child_env(monkeypatch, wf, _TURNED_ON)
        bot = _make_bot()
        question = _make_binary_question()
        with mock_stacking_pipeline(bot, predictions=_HIGH_SPREAD_BINARY, aggregate_return=0.72) as mocks:
            result = await bot._research_and_make_predictions(question)
            mocks["crux"].assert_not_called()
            mocks["targeted"].assert_not_called()
            mocks["aggregate"].assert_not_called()  # the stacker
        assert len(result.predictions) == len(_HIGH_SPREAD_BINARY), "the base forecasts go on to the median"
        assert bot._pipeline.outcomes[question.id_of_question] == "skipped_config_off"
        assert bot._pipeline.skip_reasons[question.id_of_question] == "config_off"
