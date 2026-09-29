"""The child process env, the gate and the effective mode flags. Names only ever reach a log."""

from __future__ import annotations

from collections.abc import Mapping

from kira_scheduler.spec import MODE_DEFAULTS, PROGRAM_GATE, EnvEntry, Workflow

# What a child inherits from the scheduler besides the workflow env. An allowlist, so one workflow
# never sees another's token (tournament must not receive MANTIC_TOKEN, mantic never the donated key).
_BASE_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "TZ",
        "LANG",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_BASE_PREFIXES = ("UV_", "PLAYWRIGHT_", "KIRA_EARN_", "LC_", "XDG_")


def is_set(environ: Mapping[str, str], name: str) -> bool:
    return bool(environ.get(name, "").strip())


def missing_keys(names: tuple[str, ...], environ: Mapping[str, str]) -> list[str]:
    return [name for name in names if not is_set(environ, name)]


def workflow_missing(workflow: Workflow, environ: Mapping[str, str]) -> list[str]:
    return missing_keys(workflow.gate, environ)


def program_missing(environ: Mapping[str, str]) -> list[str]:
    return missing_keys(PROGRAM_GATE, environ)


def workflow_enabled(workflow: Workflow, environ: Mapping[str, str]) -> bool:
    raw = environ.get(workflow.enable_var, "").strip().lower()
    if raw in ("false", "0", "no"):
        return False
    if raw in ("true", "1", "yes"):
        return True
    return workflow.default_enabled


def _resolve(entry: EnvEntry, environ: Mapping[str, str]) -> str:
    if entry.kind == "literal":
        return entry.value
    raw = environ.get(entry.source, "").strip()
    if entry.kind == "secret":
        return raw
    return raw or entry.value


def build_child_env(workflow: Workflow, environ: Mapping[str, str], run_id: str) -> dict[str, str]:
    """Base allowlist, then the workflow env, then a unique ``GITHUB_RUN_ID`` naming this run's files."""
    child = {k: v for k, v in environ.items() if k in _BASE_KEYS or k.startswith(_BASE_PREFIXES)}
    for entry in workflow.env:
        child[entry.target] = _resolve(entry, environ)
    # The bot names raw-research and persisted-research files after this id; "local" would collide.
    child["GITHUB_RUN_ID"] = run_id
    # Actions runners are UTC and the bot was tuned there. kira-earn sets TZ=Europe/Stockholm for the
    # container, so pin the child to UTC rather than let its local-time reads shift by 1 or 2 hours.
    child["TZ"] = "UTC"
    return child


def effective_mode(environ: Mapping[str, str]) -> dict[str, str]:
    """The seven cost flags as the tournament and MiniBench children see them."""
    return {name: (environ.get(name, "").strip() or default) for name, default in MODE_DEFAULTS.items()}
