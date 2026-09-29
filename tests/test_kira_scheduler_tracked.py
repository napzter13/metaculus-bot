"""The files a Kira install needs must be tracked, not swallowed by a broad ``.gitignore`` rule.

``*.json`` is ignored repo-wide, which silently dropped ``kira-earn.json``: it existed on the author's
disk and would have been absent from a fresh checkout, so kira-earn could not have installed the bot.
"""

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REQUIRED = [
    "kira-earn.json",
    *(f"kira_scheduler/{name}.py" for name in ("__main__", "env", "runid", "scheduler", "slots", "spec", "store")),
    "scripts/import_kira_runs.py",
]


@pytest.mark.parametrize("rel_path", _REQUIRED)
def test_install_files_are_not_gitignored(rel_path: str) -> None:
    if not (_REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", rel_path],
        cwd=_REPO_ROOT,
        check=False,
    )
    assert result.returncode == 1, f"{rel_path} is matched by a .gitignore rule, so it would not be committed"
