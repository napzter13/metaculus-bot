"""``SUPPORT_MODEL_ROUTE``: text-only support roles onto a ``:free`` slug at the shared builder.

The route is read inside ``build_llm_with_openrouter_fallback``, so these tests call the builder
directly under a patched environment. Both OpenRouter keys are set and distinct, the shape under
which an OpenAI or Google slug gets the donated-key wrapper, so a free-routed role being a plain
``GeneralLlm`` proves it stays off the donated key even when that key is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forecasting_tools import GeneralLlm

from metaculus_bot.constants import (
    DONATED_OPENROUTER_KEY_ENABLED_ENV,
    FREE_ROUTABLE_SUPPORT_ROLES,
    FREE_TIER_SUPPORT_MODEL,
    OAI_ANTH_OPENROUTER_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    SUPPORT_MODEL_ROUTE_ENV,
    support_model_route,
)
from metaculus_bot.credit_telemetry import ROLE_METADATA_KEY
from metaculus_bot.fallback_openrouter import FallbackOpenRouterLlm, build_llm_with_openrouter_fallback
from metaculus_bot.llm_configs import (
    DISAGREEMENT_ANALYZER_LLM,
    MARKET_QUERY_AUTHOR_LLM_CONFIG,
    MARKET_RANKER_LLM_CONFIG,
    PARSER_LLM,
    SUMMARIZER_LLM,
)
from metaculus_bot.research.page_digest import PAGE_DIGEST_ROLE

_PAID_SUPPORT_SLUG = "openrouter/openai/gpt-6-sol"


@pytest.fixture(autouse=True)
def _both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENROUTER_API_KEY_ENV, "personal-test-key")
    monkeypatch.setenv(OAI_ANTH_OPENROUTER_KEY_ENV, "donated-test-key")
    # Pinned: a few suites write this switch straight into os.environ and it outlives them.
    monkeypatch.setenv(DONATED_OPENROUTER_KEY_ENABLED_ENV, "true")


def _role(llm: GeneralLlm) -> str:
    return llm.litellm_kwargs["metadata"][ROLE_METADATA_KEY]


@pytest.mark.parametrize("value", [None, "", "paid", "PAID"])
def test_default_route_is_paid(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(SUPPORT_MODEL_ROUTE_ENV, raising=False)
    else:
        monkeypatch.setenv(SUPPORT_MODEL_ROUTE_ENV, value)
    assert support_model_route() == "paid"


def test_an_unknown_route_raises_instead_of_silently_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SUPPORT_MODEL_ROUTE_ENV, "fre")
    with pytest.raises(ValueError, match="SUPPORT_MODEL_ROUTE"):
        build_llm_with_openrouter_fallback(_PAID_SUPPORT_SLUG, role="summarizer")


@pytest.mark.parametrize("role", sorted(FREE_ROUTABLE_SUPPORT_ROLES))
def test_paid_route_builds_the_configured_model_unchanged(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.delenv(SUPPORT_MODEL_ROUTE_ENV, raising=False)
    llm = build_llm_with_openrouter_fallback(_PAID_SUPPORT_SLUG, role=role, reasoning={"effort": "low"})
    assert isinstance(llm, FallbackOpenRouterLlm)
    assert llm.model == _PAID_SUPPORT_SLUG
    assert llm.litellm_kwargs["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize("role", sorted(FREE_ROUTABLE_SUPPORT_ROLES))
def test_free_route_moves_every_text_role_onto_a_plain_free_llm(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.setenv(SUPPORT_MODEL_ROUTE_ENV, "free")
    llm = build_llm_with_openrouter_fallback(
        _PAID_SUPPORT_SLUG,
        role=role,
        reasoning={"effort": "low"},
        reasoning_effort="low",
        timeout=30,
        allowed_tries=1,
        response_format={"type": "json_object"},
    )
    assert type(llm) is GeneralLlm, "a :free Google slug on the donated key answers 404"
    assert llm.model == FREE_TIER_SUPPORT_MODEL
    assert _role(llm) == role, "the spend ledger must still book the call under its role"
    kwargs = llm.litellm_kwargs
    assert "reasoning" not in kwargs
    assert "reasoning_effort" not in kwargs
    assert kwargs["timeout"] == 30, "the role's own wall must survive the swap"
    assert kwargs["response_format"] == {"type": "json_object"}, "structured-output roles keep their schema"


@pytest.mark.parametrize(
    "role", ["forecaster:openai", "forecaster:anthropic", "native_search", "gap_fill_resolver", "stacker", None]
)
def test_free_route_never_moves_forecasters_or_web_search_roles(
    monkeypatch: pytest.MonkeyPatch, role: str | None
) -> None:
    monkeypatch.setenv(SUPPORT_MODEL_ROUTE_ENV, "free")
    llm = build_llm_with_openrouter_fallback(_PAID_SUPPORT_SLUG, role=role)
    assert llm.model == _PAID_SUPPORT_SLUG


def test_every_routable_role_name_is_one_a_builder_really_uses() -> None:
    """A renamed role would silently drop off the route; pin the set to the live spellings."""
    live = {
        _role(SUMMARIZER_LLM),
        _role(PARSER_LLM),
        _role(DISAGREEMENT_ANALYZER_LLM),
        MARKET_RANKER_LLM_CONFIG["role"],
        MARKET_QUERY_AUTHOR_LLM_CONFIG["role"],
        PAGE_DIGEST_ROLE,
    }
    assert live <= FREE_ROUTABLE_SUPPORT_ROLES
    # The two call-time builders stamp their roles as literals at the call site.
    src = Path(__file__).resolve().parent.parent / "metaculus_bot" / "research"
    assert 'role="gap_fill_analyzer"' in (src / "targeted.py").read_text()
    assert 'role="financial_classifier"' in (src / "financial_data.py").read_text()
