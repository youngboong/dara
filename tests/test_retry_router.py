"""Retry endpoints: unchanged retry, retry with the suggested fix, selected-phase tasks."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from monty.json import MontyEncoder

from dara.cif import Cif
from dara.server import api_router, retry_router
from dara.xrd import XRDData

DATA = Path(__file__).parent / "test_data"
TIMEOUT = 'Traceback (most recent call last):\n  File "x.py", line 1\nsubprocess.TimeoutExpired: Command bgmn timed out after 300 seconds\n'
KEY = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: MP_API_KEY is not set\n'


def _encode(value):
    return json.loads(json.dumps(value, cls=MontyEncoder))


def _search_record(index=5, status="FIZZLED", error=TIMEOUT, **overrides):
    kwargs = {
        "xrd_data": _encode(XRDData([10, 20, 30], [1, 5, 1])),
        "elements": ["Fe", "O"],
        "precursors": [],
        "cif_dbs": [{"@class": "CODDatabase"}],
        "analysis_mode": "elemental",
        "search_kwargs": {"instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "wavelength": "Cu"},
        "screening_timeout_s": 300.0,
        "max_runtime_s": 3600.0,
    }
    kwargs.update(overrides)
    return {
        "index": index, "uuid": f"u-{index}", "status": status, "user": "amy", "error": error,
        "job": {"name": "pattern.xy", "function_kwargs": kwargs,
                "metadata": {"submission_settings": {"filename": "pattern.xy"}}},
    }


def _selected_record(index=6, status="FIZZLED"):
    reference = Cif.from_file(DATA / "BiFeO3.cif")
    kwargs = {
        "xrd_data": _encode(XRDData([10, 20, 30], [1, 5, 1])),
        "cifs": _encode([reference]),
        "search_kwargs": {"instrument_profile": "test", "wavelength": "Cu"},
        "final_refinement_params": {"gewicht": "SPHAR4", "lattice_range": 0.03},
        "context": {"parent_task_id": 5, "parent_selection": "best", "precursors": [], "elements": ["Fe", "O"],
                    "candidate_sources": []},
    }
    return {
        "index": index, "uuid": f"u-{index}", "status": status, "user": "amy", "error": TIMEOUT,
        "job": {"name": "pattern.xy [selected phases]", "function_kwargs": kwargs,
                "metadata": {"instrument_profile_details": {"name": "x"}}},
    }


class _Store:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria):
        for record in self.records:
            if record["index"] == criteria.get("index"):
                return deepcopy(record)
        return None


@pytest.fixture
def queue(monkeypatch):
    queued = []

    def add(job, user):
        queued.append((job, user))
        return 100 + len(queued)

    monkeypatch.setattr(retry_router, "add_job_to_queue", add)
    return queued


def _install(monkeypatch, *records):
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _Store(list(records)))


def _retry(task_id, **body):
    return asyncio.run(retry_router.retry_task(task_id, retry_router.RetryRequest(**body)))


def _plan(task_id):
    return asyncio.run(retry_router.retry_plan(task_id))


class _Capture:
    """Stands in for ``build_submission_job``: records the settings it was called with."""

    def __init__(self):
        self.calls = []

    def __call__(self, pattern, name, **settings):
        self.calls.append((name, settings))
        return SimpleNamespace(metadata={"submission_settings": {"filename": name}})


def test_unchanged_retry_queues_one_job_with_the_original_settings(monkeypatch, queue):
    _install(monkeypatch, _search_record())
    response = _retry(5)
    assert response == {"wf_id": 101, "applied": None, "retry_of": 5}
    assert len(queue) == 1
    job, user = queue[0]
    assert user == "amy"
    assert job.name == "pattern.xy"
    kwargs = job.function_kwargs
    assert kwargs["elements"] == ["Fe", "O"]
    assert kwargs["screening_timeout_s"] == 300.0 and kwargs["max_runtime_s"] == 3600.0
    assert [type(db).__name__ for db in kwargs["cif_dbs"]] == ["CODDatabase"]
    submission = job.metadata["submission_settings"]
    assert submission["retry_of"] == 5 and submission["retry_remedy"] is None
    assert submission["source_task_id"] == 5


def test_remedy_retry_applies_the_changes(monkeypatch, queue):
    _install(monkeypatch, _search_record())
    capture = _Capture()
    monkeypatch.setattr(retry_router, "build_submission_job", capture)
    response = _retry(5, apply_remedy=True)
    assert response["wf_id"] == 101 and response["retry_of"] == 5
    assert response["applied"] == {
        "code": "timeout", "label": "Retry with longer time limits",
        "changes": {"screening_timeout_s": 600.0, "max_analysis_minutes": 90.0},
    }
    (name, settings), = capture.calls
    assert name == "pattern.xy"
    assert settings["screening_timeout_s"] == 600.0 and settings["max_analysis_minutes"] == 90.0
    assert settings["library_cif_ids"] == "[]" and settings["source_task_id"] == 5
    assert "user" not in settings and "_additional_cif_count" not in settings
    assert settings["original_kwargs"]["screening_timeout_s"] == 300.0
    assert settings["use_cod"] is True and settings["elements"] == ["Fe", "O"]
    job, user = queue[0]
    assert user == "amy"
    assert job.metadata["submission_settings"]["retry_of"] == 5
    assert job.metadata["submission_settings"]["retry_remedy"] == "timeout"


def test_apply_remedy_false_keeps_every_setting(monkeypatch, queue):
    _install(monkeypatch, _search_record())
    capture = _Capture()
    monkeypatch.setattr(retry_router, "build_submission_job", capture)
    assert _retry(5)["applied"] is None
    settings = capture.calls[0][1]
    assert settings["screening_timeout_s"] == 300.0 and settings["max_analysis_minutes"] == 60.0
    assert queue[0][0].metadata["submission_settings"]["retry_remedy"] is None


def test_mp_key_remedy_switches_mp_off_and_cod_on(monkeypatch, queue):
    record = _search_record(error=KEY, cif_dbs=[
        {"@class": "MaterialsProjectDatabase", "max_structures": 80, "e_hull_filter": 0.05},
    ])
    _install(monkeypatch, record)
    plan = _plan(5)
    assert plan == {
        "code": "mp_key", "label": "Retry without Materials Project",
        "changes": {"use_mp": False, "use_cod": True},
        "retry_unchanged": True, "supported": True, "reason": None,
    }
    capture = _Capture()
    monkeypatch.setattr(retry_router, "build_submission_job", capture)
    _retry(5, apply_remedy=True)
    settings = capture.calls[0][1]
    assert settings["use_mp"] is False and settings["use_cod"] is True


def test_remedy_values_never_exceed_what_a_submission_accepts(monkeypatch):
    _install(monkeypatch, _search_record(screening_timeout_s=1000.0, max_runtime_s=600 * 60.0))
    changes = _plan(5)["changes"]
    assert changes == {"screening_timeout_s": 1200.0, "max_analysis_minutes": 720.0}


def test_refinement_controls_remedy_is_passed_as_a_dict(monkeypatch, queue):
    error = 'Traceback...\nRuntimeError: Final refinement failed for every candidate.\n'
    _install(monkeypatch, _search_record(error=error))
    capture = _Capture()
    monkeypatch.setattr(retry_router, "build_submission_job", capture)
    response = _retry(5, apply_remedy=True)
    assert response["applied"]["changes"] == {"refinement_controls": {"lattice": "refine", "lattice_percent": 3}}
    assert capture.calls[0][1]["refinement_controls"] == {"lattice": "refine", "lattice_percent": 3}


def test_unknown_failure_has_no_remedy_but_can_retry_unchanged(monkeypatch, queue):
    _install(monkeypatch, _search_record(error="Traceback...\nKeyError: 'x'\n"))
    plan = _plan(5)
    assert plan["code"] == "unknown" and plan["changes"] is None and plan["label"] is None
    assert plan["supported"] is True and plan["retry_unchanged"] is True
    response = _retry(5, apply_remedy=True)  # nothing to apply: runs unchanged
    assert response["applied"] is None and len(queue) == 1


def test_only_failed_tasks_can_be_retried(monkeypatch, queue):
    _install(monkeypatch, _search_record(status="COMPLETED"))
    with pytest.raises(HTTPException) as error:
        _retry(5)
    assert error.value.status_code == 409
    assert queue == []
    plan = _plan(5)
    assert plan["supported"] is False and plan["reason"]


def test_unknown_task_and_missing_pattern(monkeypatch, queue):
    broken = _search_record(index=8)
    broken["job"]["function_kwargs"].pop("xrd_data")
    _install(monkeypatch, broken)
    with pytest.raises(HTTPException) as error:
        _retry(99)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        _retry(8)
    assert error.value.status_code == 409
    assert queue == []


def test_selected_phase_task_is_requeued_unchanged(monkeypatch, queue):
    _install(monkeypatch, _selected_record())
    plan = _plan(6)
    assert plan["changes"] is None and plan["supported"] is True and plan["reason"]
    response = _retry(6)
    assert response == {"wf_id": 101, "applied": None, "retry_of": 6}
    job, user = queue[0]
    assert user == "amy" and job.name == "pattern.xy [selected phases]"
    assert job.metadata["retry_of"] == 6
    assert job.metadata["instrument_profile_details"] == {"name": "x"}
    assert [cif.filename for cif in job.function_kwargs["cifs"]] == [
        Cif.from_file(DATA / "BiFeO3.cif").filename
    ]


def test_remedy_on_selected_phase_task_is_rejected(monkeypatch, queue):
    _install(monkeypatch, _selected_record())
    with pytest.raises(HTTPException) as error:
        _retry(6, apply_remedy=True)
    assert error.value.status_code == 409
    assert queue == []


def test_default_screening_timeout_remedy_is_suppressed_when_it_changes_nothing(monkeypatch, queue):
    # screening_timeout_s=None means the legacy 1200 s, which is already the maximum a
    # submission accepts; without a analysis-time limit there is nothing left to change.
    _install(monkeypatch, _search_record(screening_timeout_s=None, max_runtime_s=None))
    plan = _plan(5)
    assert plan["changes"] is None and plan["label"] is None
    assert plan["code"] == "timeout"
    assert plan["retry_unchanged"] is True and plan["supported"] is True
    capture = _Capture()
    monkeypatch.setattr(retry_router, "build_submission_job", capture)
    assert _retry(5, apply_remedy=True)["applied"] is None  # nothing to apply


def test_remedy_keeps_only_effective_changes(monkeypatch):
    # Timeout at its default (1200 s) is dropped; the analysis-time limit can still grow.
    _install(monkeypatch, _search_record(screening_timeout_s=None, max_runtime_s=3600.0))
    assert _plan(5)["changes"] == {"max_analysis_minutes": 90.0}
    # A lower saved timeout still gets raised.
    _install(monkeypatch, _search_record(screening_timeout_s=300.0, max_runtime_s=None))
    assert _plan(5)["changes"] == {"screening_timeout_s": 600.0}


def test_effective_values_normalise_none():
    assert retry_router._effective("screening_timeout_s", None) == 1200.0
    assert retry_router._effective("screening_timeout_s", 300) == 300.0
    assert retry_router._effective("max_analysis_minutes", None) is None
    assert retry_router._effective("max_analysis_minutes", 60) == 60.0
    assert retry_router._effective("use_mp", True) is True


def test_eflech_timeout_gets_its_own_label_and_no_timeout_remedy(monkeypatch):
    error = ('Traceback (most recent call last):\n  File "x.py", line 1\n'
             "subprocess.TimeoutExpired: Command '['C:/bgmn/eflech.exe', 'a.xy']' timed out after 1800 seconds\n")
    _install(monkeypatch, _search_record(error=error, screening_timeout_s=300.0))
    plan = _plan(5)
    assert plan["code"] == "eflech_timeout"
    assert plan["changes"] is None and plan["label"] is None
    assert plan["retry_unchanged"] is True


def test_bgmn_timeout_whose_traceback_mentions_eflech_files_is_still_a_timeout():
    from dara.server.failure_summary import summarize_failure

    error = ('Traceback (most recent call last):\n  File "dara/eflech_worker.py", line 97, in run\n'
             "subprocess.TimeoutExpired: Command 'bgmn' timed out after 300 seconds\n")
    assert summarize_failure(error)["code"] == "timeout"
