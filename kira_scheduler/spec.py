"""What each workflow runs: slots, gate keys, command and the env the workflow hands the bot.

This table is the Kira twin of the ``Run bot`` step in ``.github/workflows/run_bot_on_*.yaml``.
``tests/test_kira_scheduler_spec.py`` parses those files and fails when the two differ, so change
both together. Only the mapping lives here; no secret value is ever written into this repo.
"""

from __future__ import annotations

from dataclasses import dataclass

PROGRAM_NAME = "metaculus-bot"

# The zero-credit posture (mode M4, docs/KIRA-SETUP.md). Also the manifest's ``env_defaults``, and the
# value used when the env file leaves a flag unset, so a missing default fails CHEAP, never paid.
MODE_DEFAULTS: dict[str, str] = {
    "FORECASTER_FREE_TIER_ENABLED": "true",
    "SUPPORT_MODEL_ROUTE": "free",
    "GAP_FILL_ENABLED": "false",
    "GAP_FILL_V2_ENABLED": "false",
    "NATIVE_SEARCH_ENABLED": "false",
    "GEMINI_SEARCH_ENABLED": "false",
    "OPENROUTER_CREDIT_FLOOR_USD": "15",
}

# Every slot and timestamp is UTC: kira_scheduler reads the clock as ``datetime.now(UTC)``, formats with
# explicit UTC, and pins the child's TZ to UTC, so the container's TZ=Europe/Stockholm never reaches it.

# The gate of the program as a whole, the manifest's ``gate: true`` entries.
PROGRAM_GATE: tuple[str, ...] = ("METACULUS_TOKEN", "OPENROUTER_API_KEY")

# The workflow's step timeout ("Run bot", timeout-minutes: 70). The checkout, setup-uv, install and
# playwright steps have no runtime twin: kira-earn does them once, at install time.
RUN_TIMEOUT_S = 70 * 60

# Actions secrets whose Kira env name differs. The workflows read ``secrets.exa_key`` (lowercase).
SECRET_ALIASES: dict[str, str] = {"exa_key": "EXA_API_KEY"}


@dataclass(frozen=True)
class EnvEntry:
    """One variable in the bot step's env.

    kind ``literal``: ``value`` verbatim. ``secret``: copied from the Kira env name ``source`` (empty
    string when unset, exactly what Actions passes). ``var``: copied from ``source`` when set and
    non-empty, else ``value`` (the Actions ``vars.X || 'default'`` idiom).
    """

    target: str
    kind: str
    source: str = ""
    value: str = ""


def lit(target: str, value: str) -> EnvEntry:
    return EnvEntry(target, "literal", value=value)


def sec(target: str, source: str | None = None) -> EnvEntry:
    return EnvEntry(target, "secret", source=source or target)


def var(name: str) -> EnvEntry:
    return EnvEntry(name, "var", source=name, value=MODE_DEFAULTS[name])


@dataclass(frozen=True)
class Workflow:
    name: str
    slots: tuple[int, ...]  # minutes past the hour, UTC
    args: tuple[str, ...]  # after ``main.py``
    gate: tuple[str, ...]
    enable_var: str
    env: tuple[EnvEntry, ...]
    run_timeout_s: int = RUN_TIMEOUT_S
    default_enabled: bool = True

    @property
    def workflow_file(self) -> str:
        return f"run_bot_on_{self.name}.yaml"


# Shared by all three: the research and stacking switches that never vary between workflows.
_COMMON_LITERALS: tuple[EnvEntry, ...] = (
    lit("PYTHONUNBUFFERED", "1"),
    lit("ASKNEWS_MAX_CONCURRENCY", "1"),
    lit("ASKNEWS_MAX_RPS", "0.2"),
    lit("DISABLE_AIOHTTP_TRANSPORT", "true"),
    lit("FINANCIAL_DATA_ENABLED", "true"),
    lit("PROBABILISTIC_TOOLS_ENABLED", "false"),
    lit("PROBABILISTIC_TOOLS_TYPES", "binary,multiple_choice"),
    lit("PREDICTION_MARKETS_ENABLED", "true"),
    lit("RESOLUTION_SOURCE_ENABLED", "true"),
    lit("RESOLUTION_SOURCE_URL_CONTEXT_ENABLED", "true"),
    lit("TS_ANCHOR_ENABLED", "true"),
    lit("TS_ANCHOR_CHART_ENABLED", "false"),
    lit("NUMERIC_STACKING_ENABLED", "false"),
    lit("BINARY_STACKING_ENABLED", "false"),
    lit("MC_STACKING_ENABLED", "false"),
    lit("OPENAI_AGENTS_DISABLE_TRACING", "true"),
    lit("PERSIST_RESEARCH_ENABLED", "true"),
    lit("RAW_RESEARCH_LOG_ENABLED", "true"),
)

# Secrets both families read (research providers and the optional direct-provider keys).
_COMMON_SECRETS: tuple[EnvEntry, ...] = (
    sec("PERPLEXITY_API_KEY"),
    sec("EXA_API_KEY"),
    sec("OPENAI_API_KEY"),
    sec("OPENROUTER_API_KEY"),
    sec("ANTHROPIC_API_KEY"),
    sec("ASKNEWS_CLIENT_ID"),
    sec("ASKNEWS_SECRET"),
    sec("FRED_API_KEY"),
    sec("SEC_EDGAR_CONTACT_EMAIL"),
    sec("GOOGLE_API_KEY", "GEMINI_API_KEY"),
)

# Tournament and MiniBench: donated-key routing on, every cost switch read from the env file.
_METACULUS_ENV: tuple[EnvEntry, ...] = (
    *_COMMON_LITERALS,
    *_COMMON_SECRETS,
    sec("METACULUS_TOKEN"),
    sec("OAI_ANTH_OPENROUTER_KEY"),
    lit("GEMINI_USE_DONATED_OPENROUTER_KEY", "true"),
    var("FORECASTER_FREE_TIER_ENABLED"),
    var("SUPPORT_MODEL_ROUTE"),
    var("OPENROUTER_CREDIT_FLOOR_USD"),
    var("NATIVE_SEARCH_ENABLED"),
    var("GEMINI_SEARCH_ENABLED"),
    var("GAP_FILL_ENABLED"),
    var("GAP_FILL_V2_ENABLED"),
)

# Mantic: personal keys only, donated routing forced off, and the research switches pinned ON in the
# workflow (it never read the cost variables). The M4 mode flags therefore do NOT reach a Mantic run.
_MANTIC_ENV: tuple[EnvEntry, ...] = (
    *_COMMON_LITERALS,
    *_COMMON_SECRETS,
    sec("MANTIC_TOKEN"),
    lit("DONATED_OPENROUTER_KEY_ENABLED", "false"),
    lit("GEMINI_USE_DONATED_OPENROUTER_KEY", "false"),
    lit("NATIVE_SEARCH_ENABLED", "true"),
    lit("GEMINI_SEARCH_ENABLED", "true"),
    lit("GAP_FILL_ENABLED", "true"),
    lit("GAP_FILL_V2_ENABLED", "true"),
)

WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        name="tournament",
        slots=(3, 23, 43),
        args=(),
        gate=("METACULUS_TOKEN", "OPENROUTER_API_KEY"),
        enable_var="WORKFLOW_TOURNAMENT_ENABLED",
        env=_METACULUS_ENV,
    ),
    Workflow(
        name="minibench",
        slots=(8, 38),
        args=("--mode", "minibench"),
        gate=("METACULUS_TOKEN", "OPENROUTER_API_KEY"),
        enable_var="WORKFLOW_MINIBENCH_ENABLED",
        env=_METACULUS_ENV,
    ),
    Workflow(
        name="mantic",
        slots=(5, 15, 25),
        args=("--mode", "mantic"),
        gate=("MANTIC_TOKEN", "OPENROUTER_API_KEY"),
        enable_var="WORKFLOW_MANTIC_ENABLED",
        env=_MANTIC_ENV,
    ),
)
