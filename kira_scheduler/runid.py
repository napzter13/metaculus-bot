"""Numeric run ids for runs made on Kira, so the sync tools can file them like Actions runs.

The tools key everything on an integer run id and read the workflow from a run-id map GitHub
supplies. A Kira run has no GitHub run id, so it gets one that is a pure function of its workflow and
its UTC start time: ``<YYYYmmddHHMMSS><code>``. That is 15 digits, well above a GitHub run id (about
11), so ``kira_workflow`` can tell the two apart and the workflow needs no side table.
"""

from __future__ import annotations

import re

_CODES: dict[str, int] = {"tournament": 1, "minibench": 2, "mantic": 3}
_BY_CODE = {code: name for name, code in _CODES.items()}
_FLOOR = 10**13
_STAMP_RE = re.compile(r"^(\d{8})T(\d{6})Z")


def kira_run_id(workflow: str, stamp: str) -> int:
    """The id for the run of ``workflow`` started at ``stamp`` (``20260929T101400Z``, a suffix is ignored)."""
    match = _STAMP_RE.match(stamp)
    if match is None or workflow not in _CODES:
        raise ValueError(f"not a Kira run: workflow={workflow!r} stamp={stamp!r}")
    return int(match.group(1) + match.group(2)) * 10 + _CODES[workflow]


def kira_workflow(run_id: int | str) -> str | None:
    """The workflow a Kira run id encodes, or None for a GitHub run id or anything that is not a number.

    Callers in the sync tools hold the id as an int in one place and a string in another, so both work.
    """
    try:
        value = int(run_id)
    except (TypeError, ValueError):
        return None
    if value < _FLOOR:
        return None
    return _BY_CODE.get(value % 10)
