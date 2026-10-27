"""Stage timing in the progress file and the elapsed / limit / ETA fields of the progress endpoint."""

import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from dara import task_progress
from dara.server import task_control_router as control


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("DARA_TASK_PROGRESS_DIR", str(tmp_path))
    monkeypatch.setenv("DARA_TASK_OWNER_PID", str(os.getpid()))
    return tmp_path


def _state(path):
    return json.loads((path / "progress.json").read_text(encoding="utf-8"))


def test_stage_started_at_is_kept_within_a_stage(owned):
    task_progress.report_progress("Screening reference phases", 0, 10)
    first = _state(owned)
    assert first["stage_started_at"] and first["stage"] == "Screening reference phases"
    task_progress.report_progress("Screening reference phases", 4, 10)
    second = _state(owned)
    assert second["stage_started_at"] == first["stage_started_at"]
    assert second["completed"] == 4 and second["updated_at"] >= first["updated_at"]


def test_stage_started_at_resets_on_a_new_stage(owned):
    task_progress.report_progress("Screening reference phases", 3, 10)
    old = _state(owned)["stage_started_at"]
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    state = _state(owned)
    state["stage_started_at"] = stale
    (owned / "progress.json").write_text(json.dumps(state), encoding="utf-8")
    task_progress.report_progress("Screening reference phases", 4, 10)
    assert _state(owned)["stage_started_at"] == stale  # same stage keeps the stored value
    task_progress.report_progress("Searching phase combinations")
    new = _state(owned)
    assert new["stage_started_at"] != stale and new["stage_started_at"] >= old


def test_unreadable_previous_file_is_ignored(owned):
    (owned / "progress.json").write_text("not json", encoding="utf-8")
    task_progress.report_progress("Final candidate refinement", 1, 5)
    assert _state(owned)["stage_started_at"]


def test_signature_is_unchanged():
    import inspect

    assert list(inspect.signature(task_progress.report_progress).parameters) == ["stage", "completed", "total"]


# --- endpoint --------------------------------------------------------------------------

@pytest.fixture
def records(monkeypatch, tmp_path):
    rows = [{"index": 1, "uuid": str(uuid4()), "status": "RUNNING"}]

    class Store:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def query_one(self, criteria):
            return next((deepcopy(r) for r in rows if all(r.get(k) == v for k, v in criteria.items())), None)

        def update(self, record):
            rows[0].update(record)

    monkeypatch.setattr(control, "get_worker_store", Store)
    monkeypatch.setattr(control, "get_dara_server_settings",
                        lambda: SimpleNamespace(montydb_path=str(tmp_path / "montydb")))
    return rows


def _write_progress(row, tmp_path, **state):
    directory = control.task_directory(row["uuid"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "progress.json").write_text(json.dumps(state), encoding="utf-8")


def test_progress_reports_elapsed_budget_and_eta(records, tmp_path):
    now = datetime.now(timezone.utc)
    records[0].update(
        start_time=now - timedelta(seconds=250),
        job={"function_kwargs": {"max_runtime_s": 1800.0}},
    )
    _write_progress(
        records[0], tmp_path, stage="Screening reference phases", completed=10, total=40,
        stage_started_at=(now - timedelta(seconds=100)).isoformat(),
    )
    result = asyncio.run(control.progress(1))
    assert result["stage"] == "Screening reference phases" and result["status"] == "RUNNING"
    assert abs(result["elapsed_s"] - 250) <= 2
    assert result["budget_s"] == 1800.0
    assert abs(result["stage_elapsed_s"] - 100) <= 2
    assert abs(result["eta_s"] - 300) <= 6  # 100 s / 10 done * 30 left
    assert result["completed_candidates"] == 0


def test_naive_start_time_is_treated_as_utc(records, tmp_path):
    records[0]["start_time"] = (datetime.now(timezone.utc) - timedelta(seconds=90)).replace(tzinfo=None)
    result = asyncio.run(control.progress(1))
    assert abs(result["elapsed_s"] - 90) <= 2


def test_record_without_job_or_start_time_gives_nulls(records):
    result = asyncio.run(control.progress(1))
    assert result["elapsed_s"] is None and result["budget_s"] is None
    assert result["stage_elapsed_s"] is None and result["eta_s"] is None
    assert result["stage"] == "RUNNING" and result["completed_candidates"] == 0


def test_eta_needs_progress_and_a_stage_start(records, tmp_path):
    now = datetime.now(timezone.utc)
    started = (now - timedelta(seconds=60)).isoformat()
    for completed, total, stage_started, expected in [
        (0, 10, started, None), (10, 10, started, None), (None, None, started, None),
        (5, 10, None, None), (5, 10, started, 60),
    ]:
        state = {"stage": "x", "completed": completed, "total": total}
        if stage_started:
            state["stage_started_at"] = stage_started
        _write_progress(records[0], tmp_path, **state)
        eta = asyncio.run(control.progress(1))["eta_s"]
        assert (eta is None) if expected is None else abs(eta - expected) <= 2


def test_finished_tasks_report_no_elapsed_time(records, tmp_path):
    records[0].update(status="COMPLETED", start_time=datetime.now(timezone.utc) - timedelta(seconds=30))
    result = asyncio.run(control.progress(1))
    assert result["elapsed_s"] is None and result["eta_s"] is None
