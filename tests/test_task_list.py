import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from maggma.stores import MontyStore

from dara.server import api_router
from dara.server.task_summary import SUMMARY_SCHEMA, compact_summary, runtime_seconds


def _store(path):
    return MontyStore(
        database_name="dara", collection_name="jobs", database_path=str(path),
        storage="sqlite", key=["uuid", "index"],
    )


@pytest.fixture
def populated(tmp_path, monkeypatch):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    records = [
        {"uuid": "u1", "index": 1, "status": "COMPLETED", "user": "amy", "submitted_time": base,
         "job": {"name": "BiFeO3_800C.xrdml"},
         "summary": {"schema": 1, "best_rwp": 6.3, "gof": 1.8, "final_phases": ["A"],
                     "final_phase_labels": ["Anatase"], "n_candidates": 2}},
        {"uuid": "u2", "index": 2, "status": "FIZZLED", "user": "bob",
         "submitted_time": base.replace(day=2), "job": {"name": "other (rutile).xy"},
         "error": "Traceback...\nRuntimeError: Error in BGMN refinement. The exit code is -1."},
        {"uuid": "u3", "index": 3, "status": "COMPLETED", "user": "amy",
         "submitted_time": base.replace(day=3), "job": {"name": "bifeo3_second.xy"},
         "review": {"selection": 2, "notes": ""}},
    ]
    with _store(tmp_path) as store:
        for record in records:
            store.update(record)
    # A fresh store object per call, as in production.
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _store(tmp_path))
    return tmp_path


def _list(**kwargs):
    kwargs.setdefault("page", 1)
    kwargs.setdefault("limit", 10)
    kwargs.setdefault("user", None)
    kwargs.setdefault("name", None)
    return asyncio.run(api_router.get_all_tasks(**kwargs))


def test_no_filter_returns_all_with_new_fields(populated):
    payload = _list()
    assert payload["total_tasks"] == 3
    by_id = {task["fw_id"]: task for task in payload["tasks"]}
    assert by_id[1]["summary"]["best_rwp"] == 6.3 and by_id[1]["adopted"] is None
    assert by_id[1]["error_summary"] is None
    assert by_id[2]["summary"] is None
    assert by_id[2]["error_summary"]["code"] == "bgmn_exit"
    assert "-1" in by_id[2]["error_summary"]["title"]
    assert by_id[3]["adopted"] == 2 and by_id[3]["error_summary"] is None
    assert {"fw_id", "name", "state", "created_on", "user"} <= set(by_id[1])


def test_name_filter_is_case_insensitive_substring(populated):
    payload = _list(name="BIFEO")
    assert sorted(task["fw_id"] for task in payload["tasks"]) == [1, 3]
    assert payload["total_tasks"] == 2
    assert _list(name="  bifeo3_second ")["total_tasks"] == 1
    assert _list(name="")["total_tasks"] == 3
    assert _list(name="nomatch")["tasks"] == []


def test_name_filter_treats_regex_metacharacters_literally(populated):
    payload = _list(name="(rutile)")
    assert [task["fw_id"] for task in payload["tasks"]] == [2]
    assert _list(name=".*")["total_tasks"] == 0
    assert _list(name="bifeo", user="amy")["total_tasks"] == 2
    assert _list(name="bifeo", user="bob")["total_tasks"] == 0


def _document(final_phases=("TiO2_141_(cod_7206075)-0",)):
    phases = {name: SimpleNamespace() for name in final_phases}
    return SimpleNamespace(
        best_rwp=6.26, results=[1, 2, 3],
        final_result=SimpleNamespace(lst_data=SimpleNamespace(rwp=6.31, rexp=3.0, phases_results=phases)),
    )


def test_compact_summary_shape():
    summary = compact_summary(_document(), 312.4)
    assert summary["schema"] == SUMMARY_SCHEMA
    assert summary["best_rwp"] == 6.26 and summary["final_rwp"] == 6.31
    assert summary["gof"] == pytest.approx(6.31 / 3.0)
    assert summary["final_phases"] == ["TiO2_141_(cod_7206075)-0"]
    assert summary["final_phase_labels"][0].startswith("Anatase")
    assert summary["n_candidates"] == 3 and summary["runtime_s"] == 312.4
    empty = compact_summary(SimpleNamespace(best_rwp=None, results=None, final_result=None), None)
    assert empty["gof"] is None and empty["final_phases"] == [] and empty["n_candidates"] == 0


def test_runtime_seconds_tolerates_naive_and_aware_datetimes():
    naive = datetime(2026, 1, 1, 0, 0)
    aware = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    assert runtime_seconds(naive, aware) == 300
    assert runtime_seconds(None, aware) is None
