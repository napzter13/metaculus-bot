"""Kira run data reaches the sync tools through the artifact store, attributed to the right workflow.

On Actions the sync tools pulled 90-day ``research-<run_id>`` artifacts. On Kira the same data is in
the program's data dir, and ``scripts/import_kira_runs.py`` turns it into store dirs. The last test
runs the documented path for real: import, then the OFFLINE telemetry harvest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kira_scheduler.runid import kira_run_id, kira_workflow
from scripts.download_research import research_jsonl_files
from scripts.download_run_logs import download_and_harvest, infer_workflow
from scripts.gha_artifacts import STORE_META_FILENAME, store_artifacts
from scripts.import_kira_runs import import_kira_runs

_STAMP = "20260929T101400Z"
_BOT_RUN_ID = f"kira-tournament-{_STAMP}"
_LOG = "\n".join(
    [
        f"# kira-earn metaculus-bot tournament slot=2026-09-29T10:03:00Z started=2026-09-29T{_STAMP[9:11]}:14:00Z",
        "CREDIT_ROLE_SPEND: role=forecaster:openai key=personal usd=0.2000 calls=1 costed_calls=1 byok_usd=0.1000 "
        "prompt_tokens=1000 completion_tokens=500 cached_tokens=0 reasoning_tokens=300 charged_usd=0.1000 "
        "byok_calls=0 max_prompt_tokens=1000",
        "CREDIT_RUN_SUMMARY: n_questions=1 charged_usd=0.1000 usd_per_question=0.1000 donated_usd=0.0000 "
        "personal_usd=0.1000 prompt_tokens=1000 cached_tokens=0 cached_share=0.0000 max_prompt_tokens=1000 "
        "max_prompt_role=forecaster:openai",
    ]
)


def _data_dir(root: Path) -> Path:
    data = root / "data"
    (data / "runs" / "tournament").mkdir(parents=True)
    (data / "runs" / "tournament" / f"{_STAMP}.log").write_text(_LOG + "\n")
    (data / "work" / "run_logs").mkdir(parents=True)
    (data / "work" / "run_logs" / f"raw_research_{_BOT_RUN_ID}.jsonl").write_text('{"qid": 1, "provider": "x"}\n')
    (data / "work" / "research_outputs").mkdir(parents=True)
    (data / "work" / "research_outputs" / "research_a.jsonl").write_text(
        json.dumps({"qid": 1, "run_id": _BOT_RUN_ID}) + "\n"
    )
    (data / "work" / "research_outputs" / "research_other_run.jsonl").write_text(
        json.dumps({"qid": 2, "run_id": "kira-tournament-20260101T000000Z"}) + "\n"
    )
    return data


class TestRunIds:
    def test_a_kira_id_encodes_its_time_and_workflow(self) -> None:
        run_id = kira_run_id("minibench", _STAMP)
        assert run_id == 202609291014002
        assert kira_workflow(run_id) == "minibench"
        assert kira_workflow(kira_run_id("mantic", "20260929T101400Z-1")) == "mantic", "a collision suffix is ignored"

    def test_a_github_run_id_is_not_mistaken_for_a_kira_one(self) -> None:
        assert kira_workflow(36407985903) is None
        assert infer_workflow("research-36407985903", 36407985903, {}) == "unknown"

    def test_the_id_may_arrive_as_a_string_or_junk(self) -> None:
        assert kira_workflow(str(kira_run_id("mantic", _STAMP))) == "mantic"
        assert kira_workflow("100") is None
        assert kira_workflow("not-a-number") is None
        assert infer_workflow("research-100", "100", {}) == "unknown"  # type: ignore[arg-type]  # the sync_all path passes str

    def test_ids_are_unique_per_workflow_and_second(self) -> None:
        ids = {
            kira_run_id(wf, stamp)
            for wf in ("tournament", "minibench", "mantic")
            for stamp in (_STAMP, "20260929T101401Z")
        }
        assert len(ids) == 6

    @pytest.mark.parametrize(("workflow", "stamp"), [("cup", _STAMP), ("tournament", "not-a-stamp")])
    def test_a_bad_run_is_refused(self, workflow: str, stamp: str) -> None:
        with pytest.raises(ValueError, match="not a Kira run"):
            kira_run_id(workflow, stamp)


class TestImport:
    def test_a_run_lands_in_the_layout_the_sync_tools_read(self, tmp_path: Path) -> None:
        store = tmp_path / "store"
        assert import_kira_runs(_data_dir(tmp_path), store) == (1, 0)
        run_id = kira_run_id("tournament", _STAMP)
        run_dir = store / f"research-{run_id}"
        meta = json.loads((run_dir / STORE_META_FILENAME).read_text())
        assert meta == {
            "artifact_id": str(run_id),
            "name": f"research-{run_id}",
            "created_at": "2026-09-29T10:14:00Z",
            "run_id": str(run_id),
        }
        assert (run_dir / "run_logs" / f"run_{run_id}_{_STAMP}.log").read_text().startswith("# kira-earn")
        assert (run_dir / "run_logs" / f"raw_research_{_BOT_RUN_ID}.jsonl").is_file()
        research = research_jsonl_files(run_dir)
        assert [p.name for p in research] == ["research_a.jsonl"], "only this run's records, never another run's"
        assert [a["run_id"] for a in store_artifacts(store)] == [run_id]
        assert not [p for p in store.iterdir() if p.name.startswith(".")], "no staging dir left behind"

    def test_importing_twice_skips_what_is_already_there(self, tmp_path: Path) -> None:
        data, store = _data_dir(tmp_path), tmp_path / "store"
        import_kira_runs(data, store)
        assert import_kira_runs(data, store) == (0, 1)

    def test_a_workflow_dir_that_is_not_ours_is_ignored(self, tmp_path: Path) -> None:
        data = _data_dir(tmp_path)
        (data / "runs" / "cup").mkdir()
        (data / "runs" / "cup" / f"{_STAMP}.log").write_text("x")
        assert import_kira_runs(data, tmp_path / "store") == (1, 0)

    def test_an_empty_data_dir_imports_nothing(self, tmp_path: Path) -> None:
        assert import_kira_runs(tmp_path / "nothing", tmp_path / "store") == (0, 0)

    def test_the_documented_offline_harvest_attributes_the_run_and_reads_its_cost(self, tmp_path: Path) -> None:
        store, archive = tmp_path / "store", tmp_path / "archive"
        import_kira_runs(_data_dir(tmp_path), store)
        totals, runs, expired = download_and_harvest(
            "napzter13/metaculus-bot", 0, archive, store_dir=store, from_store=True
        )
        assert expired == 0
        assert [r.workflow for r in runs] == ["tournament"]
        assert totals["credit_role_spend"] == 1
        assert totals["credit_run_summary"] == 1
