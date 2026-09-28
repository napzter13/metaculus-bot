"""The ``FORECASTER_FREE_TIER_ENABLED`` roster swap in ``metaculus_bot.llm_configs``.

The module-level ``FORECASTER_LLMS`` / ``PARSER_LLM`` singletons are built once at import by
``_build_forecaster_llms`` / ``_build_parser_llm``, so these tests call the builders under a
patched environment rather than reloading the module: a reload would replace singletons that other
tests compare by identity. Both OpenRouter keys are set and distinct, which is the shape under
which a donated-key provider gets the ``FallbackOpenRouterLlm`` wrapper, so the free-tier
assertions prove the ``:free`` slugs stay off the donated key even when it is available.
"""

from __future__ import annotations

import pytest
from forecasting_tools import GeneralLlm

from metaculus_bot import llm_configs
from metaculus_bot.constants import (
    FORECASTER_FREE_TIER_ENABLED_ENV,
    OAI_ANTH_OPENROUTER_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
)
from metaculus_bot.fallback_openrouter import FallbackOpenRouterLlm

_PAID_SLUGS = [
    "openrouter/openai/o3",
    "openrouter/anthropic/claude-sonnet-4.5",
    "openrouter/openai/gpt-5.6-sol",
]


@pytest.fixture(autouse=True)
def _both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENROUTER_API_KEY_ENV, "personal-test-key")
    monkeypatch.setenv(OAI_ANTH_OPENROUTER_KEY_ENV, "donated-test-key")


def test_import_time_roster_is_the_paid_median_of_three() -> None:
    assert [llm.model for llm in llm_configs.FORECASTER_LLMS] == _PAID_SLUGS
    assert llm_configs.PARSER_LLM.model == "openrouter/openai/gpt-6-luna"


@pytest.mark.parametrize("flag", [None, "false"])
def test_flag_unset_or_false_builds_the_paid_roster(monkeypatch: pytest.MonkeyPatch, flag: str | None) -> None:
    if flag is None:
        monkeypatch.delenv(FORECASTER_FREE_TIER_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(FORECASTER_FREE_TIER_ENABLED_ENV, flag)
    assert [llm.model for llm in llm_configs._build_forecaster_llms()] == _PAID_SLUGS
    assert llm_configs._build_parser_llm().model == "openrouter/openai/gpt-6-luna"


def test_free_tier_swaps_three_free_forecasters_off_the_donated_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FORECASTER_FREE_TIER_ENABLED_ENV, "true")
    forecasters = llm_configs._build_forecaster_llms()
    assert len(forecasters) == 3, "the median-of-three must survive the swap"
    for llm in forecasters:
        assert llm.model.endswith(":free"), llm.model
        assert not isinstance(llm, FallbackOpenRouterLlm), f"{llm.model} would 404 on the donated key"


def test_free_tier_parser_is_a_plain_llm_even_though_google_is_a_donated_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FORECASTER_FREE_TIER_ENABLED_ENV, "true")
    parser = llm_configs._build_parser_llm()
    assert parser.model == "openrouter/google/gemma-4-31b-it:free"
    assert type(parser) is GeneralLlm
