"""Centralised model configuration for TemplateForecaster.

Keeping these objects in a single module avoids merge-conflicts and makes it
possible to tweak/benchmark models without touching application code.
"""

from typing import Any

from forecasting_tools import GeneralLlm

from metaculus_bot.constants import forecaster_free_tier_enabled
from metaculus_bot.credit_telemetry import llm_call_metadata, plain_llm_key_alias
from metaculus_bot.fallback_openrouter import build_llm_with_openrouter_fallback

__all__ = [
    "DISAGREEMENT_ANALYZER_LLM",
    "FORECASTER_LLMS",
    "FORECASTER_MODEL_NAMES",
    "MARKET_QUERY_AUTHOR_LLM_CONFIG",
    "MARKET_RANKER_LLM_CONFIG",
    "PARSER_LLM",
    "RESEARCHER_LLM",
    "STACKER_FALLBACK_LLM",
    "STACKER_LLM",
    "SUMMARIZER_LLM",
]
# Reasoning models ignore (or degrade under) explicit sampling params, so we
# defer to provider defaults. temperature=None is explicit but redundant on
# ft 0.2.92, whose GeneralLlm ctor already defaults temperature to None (0.2.54
# injected 0 when the arg was omitted); top_p flows via **kwargs and is never set.
REASONING_MODEL_CONFIG: dict[str, Any] = {
    "temperature": None,
    "max_tokens": 64_000,  # Prevent truncation; all current forecasters/stackers support 64k output
    "stream": False,
    "timeout": 480,
    "allowed_tries": 3,
}
# Low-effort utility slots (parser, summarizer, analyzer). Same sampling-param
# rationale as REASONING_MODEL_CONFIG: temperature=None defers to provider
# defaults (redundant on ft 0.2.92, whose ctor default is already None); top_p
# left unset.
UTILITY_MODEL_CONFIG: dict[str, Any] = {
    "temperature": None,
    "max_tokens": 32_000,
    "stream": False,
    "timeout": 300,
    "allowed_tries": 3,
}
ACCEPTABLE_QUANTS = [
    "fp8",
    "fp16",
    "bf16",
    "fp32",
    "unknown",
]

# Per-instance allowed_tries=1 override (Round-2): forecaster .invoke is wrapped
# in the broad retry gated on TRANSIENT_RETRY_MAX_ELAPSED_S (forecaster_runners.py)
# so we can impose the universal "never retry a slow failure" deadline-safety rule
# that forecasting-tools' un-gated tenacity cannot. Spread per-instance (NOT by mutating
# REASONING_MODEL_CONFIG) so PARSER_LLM / STACKER configs are untouched.
_FORECASTER_CONFIG = {**REASONING_MODEL_CONFIG, "allowed_tries": 1}


def forecaster_role(model: str) -> str:
    """``forecaster:<vendor>`` for an ``openrouter/<vendor>/<model>`` roster slug.

    The CREDIT_ROLE_SPEND spend line every roster slot books under. The roster is
    latest-per-vendor, one slot each, so the VENDOR is the stable identity of a slot
    across model rotations — a per-model role would start a new time series at every swap
    and defeat the era-over-era cost comparison this exists for.
    """
    parts = model.split("/")
    if len(parts) < 3 or parts[0] != "openrouter":
        raise ValueError(f"forecaster_role expects an openrouter/<vendor>/<model> slug, got {model!r}")
    return f"forecaster:{parts[1]}"


def _forecaster_slot(model: str, **kwargs: Any) -> GeneralLlm:
    """One roster member, booked in the CREDIT_ROLE_SPEND ledger under ``forecaster:<vendor>``.

    The role is derived from the slug rather than written beside it so a roster swap cannot
    leave a slot mislabeled.
    """
    return build_llm_with_openrouter_fallback(model=model, role=forecaster_role(model), **_FORECASTER_CONFIG, **kwargs)


# SEASON-START RITUAL (operator, not an implementing session): resolve "latest per vendor"
# with a LIVE OpenRouter model-list read, never from memory — nothing in this repo can say
# what the newest OpenAI/Anthropic/Google model currently is, and the 2026-08-31 gemini-slot
# review found that a roster decision needs that one read before anything else:
#   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[] | [.id, .created] | @tsv' | sort
# filtered per vendor prefix (openai/, anthropic/, google/, x-ai/); what to check on the
# result is in docs/operations.md "Season-start checklist". Any change here is a config-era
# boundary for residual analysis, so make it once, before the first question.
# --- The roster: paid track record, not latest-per-vendor ---
#
# 2026-09-26 (fall 2026 season start, napzter13 fork). The standing design rule above was
# LATEST-PER-VENDOR. This roster departs from it deliberately, on the operator's instruction, and
# selects on FutureEval PAID TRACK RECORD instead: o3, Sonnet-4.5-high and GPT-5.x-high were paid
# in both finalized seasons, while Opus 4.6 and Gemini 3 Pro lost money. So the two loss-making
# vendor lines are out and the three paid ones are in.
#
# Read this as a deliberate trade, because it is one. Against it: these are older models than the
# ones they replace (the fork inherited gpt-6-sol / claude-opus-5.5 / gemini-3.1-pro-preview), the
# payout record is a fact about past SEASONS rather than about these model ids in fall 2026, and
# two of the three slots are now OpenAI, so an OpenAI outage or a shared reasoning failure takes
# two thirds of the ensemble instead of one third. For it: the payout record is the only
# out-of-sample evidence anyone has about this tournament's scoring, and it is the operator's call.
# If the season's residuals disagree, that is the signal to revisit, and the revisit is a
# config-era boundary like this one.
#
# CONSEQUENCE, by design and worth knowing before reading a cost report: forecaster_role() keys the
# CREDIT_ROLE_SPEND ledger on the VENDOR, because the roster used to be one slot per vendor. With
# two OpenAI slots, o3 and gpt-5.6-sol both book under "forecaster:openai" and their spend lines
# merge. Nothing breaks; per-slot cost comparison just is not available for those two this season.
#
# MEDIAN-OF-THREE IS PRESERVED: three members, and aggregation is unchanged (MEDIAN in prod, since
# the stacking flags are off). The support models, the stacker and its fallback are untouched.
#
# Live OpenRouter model-list read, 2026-09-26, per the season-start ritual above: all three slugs
# are served, all three accept `reasoning`, and all three cap completions at or above the 64k in
# REASONING_MODEL_CONFIG (o3 100k, claude-sonnet-4.5 64k exactly, gpt-5.6-sol 128k).
_PAID_FORECASTER_SLOTS: list[tuple[str, dict[str, Any]]] = [
    # OpenAI, legacy o-series. No reasoning kwarg: the track record names this slot plain "o3",
    # not "o3-high", so it runs at the provider default (medium) rather than at an effort the
    # payout data never measured. o3's enum stops at high; it has no xhigh or max tier.
    ("openrouter/openai/o3", {}),
    # Anthropic. "Sonnet-4.5-high" -> effort high, declared the only way that works on an
    # Anthropic slot: reasoning.effort ALONE. Never add extra_body={"verbosity": ...} beside it.
    # OpenRouter maps both onto the single output_config.effort and verbosity wins, which is how
    # the retired opus slots silently ran at high for months while declaring xhigh (roster_history
    # 2026-09-22). A test pins the prohibition.
    ("openrouter/anthropic/claude-sonnet-4.5", {"reasoning": {"effort": "high"}}),
    # OpenAI, 5.x line. "GPT-5.x-high" -> the top of the 5.x family at effort high, which is the
    # exact configuration this repo ran in prod until the 2026-09-22 GPT-6 migration bumped it to
    # gpt-6-sol at xhigh. Staying at high is the point: high is what was measured and paid.
    ("openrouter/openai/gpt-5.6-sol", {"reasoning": {"effort": "high"}}),
]

# --- Free-tier stopgap: forecasting before the donated credits land ---
#
# A new bot account has no Metaculus-donated OpenRouter grant and may have no personal balance, so
# every slot above would fail on credit and the bot would publish nothing at all. Under
# FORECASTER_FREE_TIER_ENABLED the roster swaps to OpenRouter ``:free`` slugs, which cost nothing
# and need only a (possibly unfunded) OPENROUTER_API_KEY.
#
# Why a roster swap and not a third rung inside FallbackOpenRouterLlm: that wrapper is fallback
# code on the publish critical path, and AGENTS.md allows it strictly-safer changes only. A
# module-level swap adds no branch to any call that runs today, and with the flag off this file
# behaves exactly as it did.
#
# These are PLAIN GeneralLlm instances, which _forecaster_slot already produces for them:
# should_route_via_donated_key matches only openai/anthropic/google, so a nvidia or
# thinkingmachines slug never touches the donated key. That is required, not incidental. Most
# ``:free`` variants are served by providers outside the donated key's allowed list, so routing
# them through the wrapper earns a 404 "no allowed providers", a wasted fallback attempt and a
# bumped alert counter (ablation/forecaster_lineup.py carries the same finding).
#
# Chosen off the same live 2026-09-26 model-list read, which also caught that the ablation
# harness's free lineup has rotted: minimax-m2.5:free and qwen3-next-80b-a3b-instruct:free are
# both DELISTED from OpenRouter now, so that list could not simply be copied. All three below are
# served, accept `reasoning`, and cap completions at or above REASONING_MODEL_CONFIG's 64k
# (nemotron-ultra 65,536, inkling 262,144, nemotron-super 235,929).
#
# Honest limits, because this is a stopgap and not a roster: only nemotron-super has ever been
# bake-off validated as a forecaster in this repo; free slugs are rate-limited at the upstream
# provider and capped per day by OpenRouter; and two of the three are NVIDIA, so ensemble
# diversity is thinner than the paid roster's. No effort kwarg on any of them, because none of
# these efforts has been measured here. Turn the flag off the day credits land.
_FREE_TIER_FORECASTER_SLOTS: list[tuple[str, dict[str, Any]]] = [
    ("openrouter/nvidia/nemotron-3-ultra-550b-a55b:free", {}),
    ("openrouter/thinkingmachines/inkling:free", {}),
    # The one free model this repo has actually bake-off validated as a forecaster.
    ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", {}),
]


def _build_forecaster_llms() -> list[GeneralLlm]:
    """The roster in effect: the free-tier stopgap when its flag is set, else the paid roster.

    Only the selected lineup is constructed, so the unused one costs no import-time work.
    """
    slots = _FREE_TIER_FORECASTER_SLOTS if forecaster_free_tier_enabled() else _PAID_FORECASTER_SLOTS
    return [_forecaster_slot(model, **kwargs) for model, kwargs in slots]


FORECASTER_LLMS: list[GeneralLlm] = _build_forecaster_llms()


def _forecaster_display_name(llm: GeneralLlm) -> str:
    """Short label for a forecaster (e.g. 'claude-opus-5.5') — strips the 'openrouter/<provider>/' prefix.

    Used by performance_analysis.parsing to map 'Forecaster N' labels in bot comments
    back to a model name without having to hand-maintain a parallel list.
    """
    return llm.model.rsplit("/", 1)[-1]


FORECASTER_MODEL_NAMES: list[str] = [_forecaster_display_name(llm) for llm in FORECASTER_LLMS]

# Summarizer: compresses raw AskNews article markdown into an analyst briefing
# (AskNews-only; all other providers already emit LLM prose). sol → terra
# 2026-07-18 operator decision: AskNews is an auxiliary/augmenting source
# (content audit: 16% unique content vs native-search 54% / gap-fill 59%), so
# the absolute-frontier tier isn't warranted. The role audit
# (scratch/research_role_audit_2026-07-17/) had sol 1st but verdict "MARGINAL
# EDGE" with terra 2nd (one attribution blur, no fabrications), and 4/5 briefing
# failures in the AskNews quality audit (scratch/asknews_quality_audit_2026-07-18/)
# were prompt-era (mini summarizer + missing no-forecast rule), not model-tier.
# Terra: -43% cost, ~50s vs ~118s wall. Effort stays low (latency).
# allowed_tries=1 (Round-2): the summarizer invoke is wrapped in the broad,
# elapsed-gated retry (orchestrator._summarize_asknews) to impose the universal
# "never retry a slow failure" deadline rule. Per-instance override so PARSER_LLM (which
# also uses UTILITY_MODEL_CONFIG) keeps its allowed_tries=3.
# 2026-09-22: terra -> gpt-6-sol. Terra has no GPT-6 successor, so every Terra role
# moves to Sol 6 at the same (low) effort it ran at.
SUMMARIZER_LLM: GeneralLlm = build_llm_with_openrouter_fallback(
    "openrouter/openai/gpt-6-sol",
    role="summarizer",
    reasoning={"effort": "low"},
    **{**UTILITY_MODEL_CONFIG, "allowed_tries": 1},
)
# Parser: deterministic extraction of percentiles/JSON from rationales — a
# capability-saturated task, so it rides the cheapest tier that saturates it and
# keeps allowed_tries=3 for robustness. mini → luna 2026-08-03: the per-token
# comparison that used to favor mini inverted. Luna was $0.20/$1.20 vs mini's
# $0.75/$4.50 per 1M, so the newer model was also the ~3.75x cheaper one. (The
# models API showed $0.10/$0.60 behind a "50% off" badge on 2026-08-03; a live
# call on 2026-08-04 billed at double that, so the promo does not apply on this
# route — see the ranker cost comment below. The swap still won, by less.)
# 2026-09-22: gpt-5.6-luna -> gpt-6-luna (GPT-6 release), now $0.10/$0.50 per 1M.
# Effort unchanged at low.
# 2026-09-26: the free-tier stopgap swaps this slot too, and it has to. A forecast whose
# percentiles cannot be extracted is not a forecast, so a free roster with a paid parser still
# publishes nothing on an unfunded key. gemma-4-31b-it:free is the repo's own bake-off winner for
# exactly this job (8 free models tried on a real failing rationale; 3/3 pass, ~10s, deterministic,
# all 11 percentiles interpolated in bounds - see ablation/forecaster_lineup.py FREE_PARSER_MODEL
# for the losers and why). Its 32,768-token completion cap clears UTILITY_MODEL_CONFIG's 32k, and
# being Google-served it would match DONATED_KEY_PROVIDERS, so unlike the free forecasters it is
# built as a plain GeneralLlm explicitly rather than by falling through the wrapper's provider
# check. Verified served on the live 2026-09-26 model-list read.
_FREE_TIER_PARSER_MODEL: str = "openrouter/google/gemma-4-31b-it:free"


def _build_parser_llm() -> GeneralLlm:
    """The parser in effect: the free-tier model when its flag is set, else the paid Luna slot."""
    if forecaster_free_tier_enabled():
        return GeneralLlm(
            model=_FREE_TIER_PARSER_MODEL,
            metadata=llm_call_metadata("parser", plain_llm_key_alias(_FREE_TIER_PARSER_MODEL)),
            **UTILITY_MODEL_CONFIG,
        )
    return build_llm_with_openrouter_fallback(
        "openrouter/openai/gpt-6-luna",
        role="parser",
        reasoning={"effort": "low"},
        **UTILITY_MODEL_CONFIG,
    )


PARSER_LLM: GeneralLlm = _build_parser_llm()
# Researcher slot in the forecasting-tools LLM config dict. Effectively dead
# code in our pipeline — we use research providers (AskNews/Gemini/native_search)
# rather than the framework's researcher path — but the slot must be populated
# to avoid silent framework defaults. Aliasing to SUMMARIZER_LLM rather than
# constructing a duplicate config: same model, same effort, same job tier, no
# reason to maintain two parallel definitions.
RESEARCHER_LLM = SUMMARIZER_LLM

# Stacker meta-model for conditional stacking (invoked only on high-disagreement questions).
#
# allowed_tries=1: a single attempt at REASONING_MODEL_CONFIG's timeout, no
# retries. The outer STACKER_SOFT_DEADLINE catches wholly stuck calls; on failure
# we fall back to STACKER_FALLBACK_LLM rather than burning two more full-timeout
# attempts against the same Anthropic API that just stalled. Retrying against the same
# provider after a stall rarely succeeds (we're almost certainly re-rolling a
# dice with the same distribution), and the budget is better spent on a
# different-provider fallback.
STACKER_LLM: GeneralLlm = build_llm_with_openrouter_fallback(
    # 2026-07-20: fable-5 → opus-4.8 (fable-5 pulled from BOTH roles after
    # content=None failures in the 2026-07-19 test_bot run — see the forecaster-slot
    # comment above + FUTURE.md). Stacking is prod-disabled, so this is
    # backtest/ablation-only exposure today. 2026-09-22: opus-4.8 -> opus-5.5
    # (Anthropic release); verbosity removed, since it overrode reasoning.effort (see the
    # forecaster slot above). Anthropic uses
    # effort-based adaptive thinking, not a max_tokens budget. Live-verified
    # OpenRouter effort enum: none/minimal/low/medium/high/xhigh/max.
    # effort=xhigh matches the forecaster slot; "max" (one tier above xhigh) is
    # deliberately held back for latency — the stacker runs under STACKER_SOFT_DEADLINE.
    "openrouter/anthropic/claude-opus-5.5",
    role="stacker",
    reasoning={"effort": "xhigh"},
    **{**REASONING_MODEL_CONFIG, "allowed_tries": 1},
)

# Fallback stacker used when the primary stacker times out or errors.
# Reasoning slot → strongest OpenAI tier (gpt-6-sol, gpt-5.6-sol -> gpt-6-sol on
# the 2026-09-22 GPT-6 migration) at xhigh (high -> xhigh 2026-09-22, operator:
# both stackers at xhigh; gpt-6-sol@xhigh took 72.5 s on a prod numeric forecaster
# prompt that day); deliberately cross-provider from the Anthropic primary so an
# Anthropic stall doesn't take both attempts down. Tighter timeout and single try
# since we're already running late on the critical path by the time this fires.
STACKER_FALLBACK_LLM: GeneralLlm = build_llm_with_openrouter_fallback(
    "openrouter/openai/gpt-6-sol",
    role="stacker_fallback",
    reasoning={"effort": "xhigh"},
    **{**REASONING_MODEL_CONFIG, "allowed_tries": 1, "timeout": 300},
)

# --- The prediction-market provider's two LLM stages ---
#
# Both are RAW DICTS rather than built GeneralLlm singletons, unlike PARSER_LLM and friends:
# the provider is gated OFF by default, so paying construction cost at import would be waste,
# and the tests patch `build_llm_with_openrouter_fallback` at the provider's one invocation
# helper. Both route `openrouter/openai/...` through that wrapper, which tries the donated
# Metaculus key first and falls back to the personal key on credential / credit / route errors
# — so prod spend lands on OAI_ANTH_OPENROUTER_KEY.
#
# `allowed_tries=1` is required, not decorative: the repo's elapsed-gated `llm_retry` wrapper
# (prediction_market._invoke_market_llm) is the SOLE retry layer, and leaving this unpinned
# inherits forecasting-tools' default of 2 with an UN-GATED `random.uniform(5, 10)` tenacity
# sleep — a large slice of PREDICTION_MARKET_TIMEOUT spent sleeping blind, which is exactly
# what llm_retry exists to eliminate. `temperature=None` defers reasoning models to provider
# defaults (redundant on ft 0.2.92, whose ctor default is already None); top_p left unset. Each
# litellm `timeout` sits ABOVE its elapsed-gated wall cap in constants.py, so the wall is the
# binding bound.
#
# Luna is the cheapest tier that saturates both tasks. The measured rate on this route was
# $0.20/M in and $1.20/M out — TWICE the $0.10/$0.60 the bake-off read off the models API on
# 2026-08-03, where a "50% off" badge was displayed that has since lapsed or never applied here. A
# live ranking call reconciled the true rates to 7 significant figures against OpenRouter's own
# `upstream_inference_cost` (26,250 in / 685 out / a 25% cache-WRITE surcharge on the input,
# `scratch/market_port_2026-08-04/QA_DRY_RUN.md`), so this is measured rather than quoted.
# 2026-09-22: gpt-5.6-luna -> gpt-6-luna (GPT-6 release), now $0.10/$0.50 per 1M; the cost figures
# below predate that swap and are receipts, not current pricing.
#
# MEASURED cost per question: ranker $0.0074 (26k in at the median post-enrichment,
# full-PredictIt shape + ~685 out, cache write included); author ~1.4k in + ~300 out ≈ $0.0005.
# The two keyword calls they replace measured ~170 tok in / ~50 out ≈ $0.0001, so net new is
# ≈ +$0.008 per question — under a cent per run at the prod shape of 1-2 questions, and ~$0.24
# of ranker spend across a 30-question tournament run. The earlier ~$0.003-0.004 arithmetic in
# the port plan understated by 2.4x purely because of the promo price; the token shapes were
# right. This traffic is ~97% input, so the input rate is the whole cost.

# Prediction-market RANKER: one call per question over the whole ~380-440-candidate pool,
# emitting up to 8 ranked rows with a relation tier and a one-phrase label. Measured completion
# averages 589 tokens including reasoning, max 1,042 (scratch/bakeoff_run_2026-08-03/results/
# RANKED_ARM_RESULTS.md). No max_tokens since 2026-09-22 (operator): a TRUNCATED ranking is a
# fail-open that loses the whole ranking, and MARKET_RANKER_WALL_TIMEOUT already bounds a runaway.
MARKET_RANKER_LLM_CONFIG: dict = {
    "model": "openrouter/openai/gpt-6-luna",
    "role": "market_ranker",
    "temperature": None,
    "reasoning_effort": "low",
    "timeout": 90,
    "allowed_tries": 1,
}

# Prediction-market QUERY AUTHOR: one call per question emitting the domain vocabulary the
# question's own tokens cannot reach (up to 8 synonyms + 3 framings). Its output is ADDITIVE to
# a deterministic query set, so its failure costs recall nothing. Measured completion max 588
# tokens including reasoning. No max_tokens since 2026-09-22: MARKET_QUERY_AUTHOR_WALL_TIMEOUT bounds it.
MARKET_QUERY_AUTHOR_LLM_CONFIG: dict = {
    "model": "openrouter/openai/gpt-6-luna",
    "role": "market_query_author",
    "temperature": None,
    "reasoning_effort": "low",
    "timeout": 45,
    "allowed_tries": 1,
}


# Tier-B auxiliary: read-and-synthesize work that needs taste but not deep
# reasoning. Identifies the crux of forecaster disagreement; output text seeds
# the targeted-search query downstream. Runs under CRUX_SOFT_DEADLINE;
# effort deliberately low since 2026-05-20 for latency — the tier was upgraded
# instead (smarter-model-at-lower-effort beats more effort on a smaller model).
# 2026-07-17: sol→terra per the role audit; terra 2nd (sol 3rd) at -49% cost;
# the role fires rarely (stacking disabled in prod).
# allowed_tries=1 (Round-2): the crux-analyzer invoke is wrapped in the broad,
# elapsed-gated retry (targeted.extract_disagreement_crux) to impose the universal
# "never retry a slow failure" deadline rule on the conditional-stacking critical path.
# Per-instance override so PARSER_LLM keeps its allowed_tries=3.
# 2026-09-22: terra -> gpt-6-sol. Terra has no GPT-6 successor, so every Terra role
# moves to Sol 6 at the same (low) effort it ran at.
DISAGREEMENT_ANALYZER_LLM: GeneralLlm = build_llm_with_openrouter_fallback(
    "openrouter/openai/gpt-6-sol",
    role="crux_analyzer",
    reasoning={"effort": "low"},
    **{**UTILITY_MODEL_CONFIG, "allowed_tries": 1},
)
