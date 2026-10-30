import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from dara.server import api_router
from dara.server import fit_cautions as fc
from dara.xrd import load_pattern

DATA = Path(__file__).parent / "test_data"


@pytest.fixture(scope="module")
def pattern():
    return load_pattern(DATA / "BiFeO3.xy")


def _build(pattern, **kwargs):
    defaults = dict(analysis_mode="elemental", elements=["Bi", "Fe", "O"], use_mp=False)
    defaults.update(kwargs)
    return api_router.build_submission_job(pattern, "BiFeO3.xy", **defaults)


def test_build_submission_job_returns_unqueued_job(pattern):
    job = _build(pattern)
    assert job.function_kwargs["elements"] == ["Bi", "Fe", "O"]
    assert job.metadata["submission_settings"]["filename"] == "BiFeO3.xy"
    assert job.metadata["submission_settings"]["library_cif_ids"] == []


def test_list_and_json_string_inputs_are_equivalent(pattern):
    from_list = _build(pattern, elements=["Bi", "Fe", "O"], excluded_ranges=[[30.0, 31.0]])
    from_text = _build(pattern, elements='["Bi", "Fe", "O"]', excluded_ranges="[[30.0, 31.0]]",
                       refinement_controls="{}")
    for key in ("elements", "precursors", "analysis_mode"):
        assert from_list.function_kwargs[key] == from_text.function_kwargs[key]
    assert from_list.function_kwargs["search_kwargs"] == from_text.function_kwargs["search_kwargs"]
    dict_controls = _build(pattern, refinement_controls={})
    assert (
        dict_controls.function_kwargs["final_refinement_params"]
        == _build(pattern).function_kwargs["final_refinement_params"]
    )


def test_invalid_inputs_raise_400(pattern):
    with pytest.raises(HTTPException) as info:
        _build(pattern, analysis_mode="nonsense")
    assert info.value.status_code == 400 and info.value.detail == "Invalid analysis mode"
    with pytest.raises(HTTPException) as info:
        _build(pattern, excluded_ranges=[[0, 180]])
    assert info.value.status_code == 400 and "fewer than three" in str(info.value.detail)


def test_library_cifs_are_appended_and_unknown_ids_rejected(pattern, monkeypatch):
    from dara.cif import Cif

    marker = Cif.from_file(DATA / "BiFeO3.cif")
    seen = []

    def fake(ids):
        seen.append(list(ids))
        return [marker]

    monkeypatch.setitem(sys.modules, "dara.server.reference_library_router",
                        SimpleNamespace(load_library_cifs=fake))
    job = _build(pattern, library_cif_ids=["abc"], additional_cifs=[])
    assert seen == [["abc"]]
    assert len(job.function_kwargs["additional_cifs"]) == 1
    assert job.metadata["submission_settings"]["library_cif_ids"] == ["abc"]

    def unknown(ids):
        raise KeyError("abc")

    monkeypatch.setitem(sys.modules, "dara.server.reference_library_router",
                        SimpleNamespace(load_library_cifs=unknown))
    with pytest.raises(HTTPException) as info:
        _build(pattern, library_cif_ids='["abc"]')
    assert info.value.status_code == 400
    assert info.value.detail == "Unknown reference library CIF: abc"


def test_library_unavailable_gives_clear_error(pattern, monkeypatch):
    monkeypatch.setitem(sys.modules, "dara.server.reference_library_router", None)
    with pytest.raises(HTTPException) as info:
        _build(pattern, library_cif_ids=["abc"])
    assert info.value.detail == "The reference CIF library is not available on this server."


def test_submit_has_library_field_and_mp_default():
    import inspect

    signature = inspect.signature(api_router.submit)
    assert signature.parameters["library_cif_ids"].default == "[]"
    assert signature.parameters["max_mp_structures"].default == 100


# --- summarize_document ------------------------------------------------------

def _refinement(rwp):
    phase = SimpleNamespace(
        spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=(0.5, 0.4),
        a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
        xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
    )
    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
            phases_results={"CaF2_225_(cod_2300449)-0": phase},
        ),
        plot_data=SimpleNamespace(x=np.array([20.0, 21.0, 22.0, 30.0])),
        get_phase_weights=lambda: {"CaF2_225_(cod_2300449)-0": 1.0},
    )


def _job_and_document():
    job = {
        "index": 4, "uuid": "u-sum", "status": "COMPLETED",
        "job": {"name": "x.xy", "function_kwargs": {}, "metadata": {}},
        "submitted_time": datetime(2026, 1, 1, 0, 0), "start_time": datetime(2026, 1, 1, 0, 1),
        "end_time": datetime(2026, 1, 1, 0, 3),
    }
    document = SimpleNamespace(
        results=[([[SimpleNamespace(filename="CaF2_225_(cod_2300449)-0")]], _refinement(5.0))],
        grouped_phases=None, missing_peaks=None, extra_peaks=None, screening_rwps=None,
        final_result=_refinement(4.0), best_rwp=4.0, precursors=[], phase_predictor=None,
        predict_kwargs=None, search_kwargs={}, final_refinement_params=None, input_cifs=[],
        xrd_data=SimpleNamespace(intensities=np.array([1.5, 2.5, 3.5]), intensity_unit="cps"),
    )
    return job, document


class _Store:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        for record in self.records:
            if all(record.get(key) == value for key, value in criteria.items()):
                return dict(record)
        return None

    def update(self, value):  # pragma: no cover - must not be called
        raise AssertionError("worker store written")


def test_summarize_document_matches_result_and_never_touches_store(monkeypatch):
    job, document = _job_and_document()
    fc.clear_lattice_flag_cache()

    def fail():
        raise AssertionError("worker store used")

    monkeypatch.setattr(api_router, "get_worker_store", fail)
    payload = api_router.summarize_document(job, document, persist_summary=False)
    assert payload["status"] == "COMPLETED"
    assert payload["final_result"]["confidence"]["overall"]["level"] in {"high", "check", "expert"}
    assert payload["all_results"][0]["confidence"]["schema"] == 1

    worker, results = _Store([job]), _Store([{"uuid": "u-sum", "output": document}])
    worker.update = lambda value: None
    monkeypatch.setattr(api_router, "get_worker_store", lambda: worker)
    monkeypatch.setattr(api_router, "get_result_store", lambda: results)
    monkeypatch.setattr(
        api_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda value: value)
    )
    fc.clear_lattice_flag_cache()
    via_result = asyncio.run(api_router.result(4))
    assert set(via_result) == set(payload)
    assert set(via_result["final_result"]) == set(payload["final_result"])
    fc.clear_lattice_flag_cache()


def test_summarize_document_without_final_result_is_404():
    job, document = _job_and_document()
    document.final_result = None
    with pytest.raises(HTTPException) as info:
        api_router.summarize_document(job, document, persist_summary=False)
    assert info.value.status_code == 404

# --- run-time controls (pre-filter, screening limit, analysis time limit) -----------------

RUNTIME_KEYS = ("candidate_prefilter", "screening_timeout_s", "max_runtime_s")


def test_default_submission_omits_the_runtime_controls(pattern):
    job = _build(pattern)
    for key in RUNTIME_KEYS:
        assert key not in job.function_kwargs
    settings = job.metadata["submission_settings"]
    assert settings["candidate_prefilter"] is None
    assert settings["screening_timeout_s"] is None and settings["max_analysis_minutes"] is None


def test_explicit_runtime_controls_are_mapped(pattern):
    job = _build(pattern, candidate_prefilter=True, screening_timeout_s=300, max_analysis_minutes=10)
    assert job.function_kwargs["candidate_prefilter"] is True
    assert job.function_kwargs["screening_timeout_s"] == 300.0
    assert job.function_kwargs["max_runtime_s"] == 600.0
    settings = job.metadata["submission_settings"]
    assert (settings["candidate_prefilter"], settings["screening_timeout_s"],
            settings["max_analysis_minutes"]) == (True, 300, 10)
    off = _build(pattern, candidate_prefilter=False)
    assert off.function_kwargs["candidate_prefilter"] is False  # explicit False is kept
    assert "screening_timeout_s" not in off.function_kwargs


def test_runtime_controls_are_not_inherited_from_the_source_task(pattern):
    job = _build(pattern, original_kwargs={"candidate_prefilter": True, "screening_timeout_s": 120.0,
                                           "max_runtime_s": 900.0})
    for key in RUNTIME_KEYS:
        assert key not in job.function_kwargs


@pytest.mark.parametrize(
    "field, value, detail",
    [
        ("max_analysis_minutes", 4, "Maximum analysis time must be between 5 and 720 minutes"),
        ("max_analysis_minutes", 721, "Maximum analysis time must be between 5 and 720 minutes"),
        ("screening_timeout_s", 59, "Screening time limit must be between 60 and 1200 seconds"),
        ("screening_timeout_s", 1201, "Screening time limit must be between 60 and 1200 seconds"),
    ],
)
def test_runtime_control_bounds(pattern, field, value, detail):
    with pytest.raises(HTTPException) as info:
        _build(pattern, **{field: value})
    assert info.value.status_code == 400 and info.value.detail == detail
    for edge in (5, 720) if field == "max_analysis_minutes" else (60, 1200):
        assert _build(pattern, **{field: edge}) is not None


def test_submit_endpoints_accept_the_runtime_controls():
    import inspect

    from dara.server import series_router

    for function in (api_router.submit, series_router.submit_series):
        parameters = inspect.signature(function).parameters
        for name in ("candidate_prefilter", "screening_timeout_s", "max_analysis_minutes"):
            assert parameters[name].default is None


def _saved_record(kwargs, metadata=None):
    return {"index": 9, "user": "u", "job": {"name": "x.xy", "metadata": metadata or {}}}, kwargs


def test_rerun_settings_for_legacy_and_new_tasks():
    pattern = SimpleNamespace(angles=np.linspace(10, 80, 50))
    record, kwargs = _saved_record({"elements": ["Bi", "O"], "search_kwargs": {}})
    legacy = api_router._rerun_settings(record, kwargs, pattern)["settings"]
    # settings an older task did not record take today's submit defaults
    assert legacy["candidate_prefilter"] is True
    assert legacy["screening_timeout_s"] == 300.0 and legacy["max_analysis_minutes"] is None

    record, kwargs = _saved_record({
        "elements": ["Bi", "O"], "search_kwargs": {}, "candidate_prefilter": False,
    })
    assert api_router._rerun_settings(record, kwargs, pattern)["settings"]["candidate_prefilter"] is False

    record, kwargs = _saved_record({
        "elements": ["Bi", "O"], "search_kwargs": {},
        "candidate_prefilter": True, "screening_timeout_s": 240.0, "max_runtime_s": 5400.0,
    })
    stored = api_router._rerun_settings(record, kwargs, pattern)["settings"]
    assert stored["candidate_prefilter"] is True
    assert stored["screening_timeout_s"] == 240.0 and stored["max_analysis_minutes"] == 90.0


def test_summarize_document_adds_search_cautions_to_the_final_result_only(monkeypatch):
    job, document = _job_and_document()
    document.search_summary = {"stopped_early": True, "stopped_at_stage": "search", "runtime_budget_s": 600}
    document.candidate_summary = {"prefilter": {"applied": True, "excluded": 3, "before": 20}}
    fc.clear_lattice_flag_cache()
    payload = api_router.summarize_document(job, document, persist_summary=False)
    codes = [c["code"] for c in payload["final_result"]["cautions"]]
    assert "search_incomplete" in codes and "prefilter_applied" in codes
    assert all(c["code"] not in {"search_incomplete", "prefilter_applied"}
               for entry in payload["all_results"] for c in entry["cautions"])
    fc.clear_lattice_flag_cache()
    clean_job, clean_document = _job_and_document()
    clean = api_router.summarize_document(clean_job, clean_document, persist_summary=False)
    assert not {"search_incomplete", "prefilter_applied", "screening_timed_out"} & {
        c["code"] for c in clean["final_result"]["cautions"]}
    fc.clear_lattice_flag_cache()
