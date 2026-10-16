"""Selected-phase workflows without a database, worker or BGMN executable."""

import asyncio
import json
import zipfile
from copy import deepcopy
from datetime import datetime
from functools import wraps
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from monty.json import MontyDecoder, MontyEncoder
from pydantic import ValidationError

from dara import selected_refinement
from dara.cif import Cif
from dara.result import DiaResult, LstResult, PhaseResult, RefinementMetrics, RefinementResult
from dara.schema import PhaseSearchDocument
from dara.server import api_router, selected_refinement_router
from dara.server.selected_refinement_router import SelectedRefinementRequest
from dara.xrd import XRDData


@pytest.fixture
def references():
    directory = Path(__file__).parent / "test_data"
    return [Cif.from_file(directory / name) for name in ("BiFeO3.cif", "Bi2Fe4O9.cif")]


def _refined(cifs, metrics=True):
    names = [cif.filename for cif in cifs]
    phase = PhaseResult(
        spacegroup_no=161, hermann_mauguin="R3c", xray_density=8.0,
        rphase=3.0, unit="NM", gewicht=1.0, gewicht_name=None,
        a=(0.55, 0.001), c=(1.35, 0.001),
    )
    return RefinementResult(
        lst_data=LstResult(
            raw_lst="Synthetic test result", pattern_name="test", num_steps=2,
            rp=2.0, rpb=3.0, r=4.0, rwp=5.0, rexp=2.0, d=1.5,
            rho=2.0, phases_results={name: phase.model_copy(deep=True) for name in names},
        ),
        plot_data=DiaResult(
            x=[10, 20, 30], y_obs=[1, 5, 1], y_calc=[1, 4, 1], y_bkg=[0.5] * 3,
            structs={name: [0.5, 3.5 / len(names), 0.5] for name in names},
        ),
        peak_data=pd.DataFrame({"phase": names, "2theta": [20.0] * len(names), "intensity": [4.0] * len(names)}),
        refinement_metrics=RefinementMetrics(
            rwp=5.0, missing_peaks=np.array([[25.0, 1.0]]),
            extra_peaks=None, intensity_mismatch_peaks=np.empty((0, 2)),
        ),
    ) if metrics else _refined(cifs).model_copy(update={"refinement_metrics": RefinementMetrics(rwp=5.0)})


def _document(references):
    refined = _refined(references)
    return PhaseSearchDocument(
        task_label="test.xy", xrd_data=XRDData([10, 20, 30], [1, 5, 1], errors=[1, 2, 1]),
        input_cifs=references,
        results=[([[cif] for cif in references], refined)], final_result=refined,
        best_rwp=5.0, precursors=["Bi2O3", "Fe2O3"], elements=["Bi", "Fe", "O"],
        candidate_sources=["COD"],
        search_kwargs={"instrument_profile": "test-device", "wavelength": "Cu", "refinement_params": {
            "wmin": 12.0, "wmax": 29.0, "excluded_ranges": [[18.0, 19.0]],
        }, "max_phases": 5, "max_num_results": 5},
        final_refinement_params={"gewicht": "SPHAR4", "lattice_range": 0.03},
        run_final_refinement=True, cifs_folder_name="dara_cifs",
    )


def _record():
    timestamp = datetime(2026, 9, 8, 1, 0, 0)
    return {"index": 7, "uuid": "test-parent", "status": "COMPLETED", "job": {"name": "test.xy"},
            "user": "tester", "submitted_time": timestamp, "start_time": timestamp, "end_time": timestamp}


def _patch_load(monkeypatch, document):
    monkeypatch.setattr(selected_refinement_router, "_load", lambda task_id: (_record(), document))


def test_queue_only_exact_selected_references_and_keep_parent_unchanged(monkeypatch, references):
    document = _document(references)
    before = json.dumps(document, cls=MontyEncoder, sort_keys=True)
    _patch_load(monkeypatch, document)
    captured = {}

    def queue(job, user):
        captured.update(job=job, user=user)
        return 21

    monkeypatch.setattr(selected_refinement_router, "add_job_to_queue", queue)
    response = asyncio.run(selected_refinement_router.refine_selected(7, SelectedRefinementRequest(phases=[references[1].filename], selection=0)))
    assert response == {"message": "submitted", "wf_id": 21, "parent_task_id": 7}
    assert captured["user"] == "tester"
    kwargs = captured["job"].function_kwargs
    assert [cif.filename for cif in kwargs["cifs"]] == [references[1].filename]
    assert np.array_equal(kwargs["xrd_data"].angles, document.xrd_data.angles)
    assert np.array_equal(kwargs["xrd_data"].errors, document.xrd_data.errors)
    assert kwargs["search_kwargs"] == document.search_kwargs
    assert kwargs["final_refinement_params"] == document.final_refinement_params
    assert kwargs["context"]["parent_task_id"] == 7
    assert kwargs["context"]["parent_selection"] == 0
    assert "cif_dbs" not in kwargs
    assert json.dumps(document, cls=MontyEncoder, sort_keys=True) == before


@pytest.mark.parametrize("names, selection", [(["unknown"], "best"), (["BiFeO3", "BiFeO3"], "best"), (["BiFeO3"], -1), (["BiFeO3"], 1)])
def test_reject_unknown_duplicate_and_invalid_candidate(monkeypatch, references, names, selection):
    _patch_load(monkeypatch, _document(references))
    monkeypatch.setattr(selected_refinement_router, "add_job_to_queue", lambda *args: pytest.fail("Invalid requests must not queue a job"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(selected_refinement_router.refine_selected(7, SelectedRefinementRequest(phases=names, selection=selection)))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("name", ["../escape", "folder/phase", "folder\\phase", "C:\\phase", "bad\x00phase", "bad\nphase", "NUL", "CON", "COM1", "LPT9", "phase.", "phase "])
def test_reject_even_stored_path_identities(monkeypatch, references, name):
    references[0].filename = name
    _patch_load(monkeypatch, _document(references))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(selected_refinement_router.refine_selected(7, SelectedRefinementRequest(phases=[name])))
    assert exc.value.status_code == 400


def test_request_rejects_empty_excessive_and_unexpected_fields():
    for fields in [{"phases": []}, {"phases": [str(i) for i in range(21)]}, {"phases": ["BiFeO3"], "path": "elsewhere"}, {"phases": ["BiFeO3"], "selection": True}, {"phases": ["BiFeO3"], "selection": "0"}]:
        with pytest.raises(ValidationError):
            SelectedRefinementRequest(**fields)


@pytest.mark.parametrize("has_metrics", [True, False])
def test_maker_retains_settings_and_builds_serializable_candidate(monkeypatch, tmp_path, references, has_metrics):
    document = _document(references)
    selected = [references[1]]
    refined = _refined(selected, metrics=has_metrics)
    captured = {}

    @wraps(selected_refinement.do_refinement)
    def fake_refine(**kwargs):
        captured.update(kwargs)
        return refined

    monkeypatch.setattr(selected_refinement, "do_refinement", fake_refine)
    monkeypatch.chdir(tmp_path)
    maker = selected_refinement.SelectedPhaseRefinementMaker(name="Selected test")
    result = maker.make.original(
        maker, document.xrd_data, selected, document.search_kwargs,
        document.final_refinement_params, {"parent_task_id": 7, "parent_selection": 0, "elements": document.elements},
    )
    assert [path.name for path in captured["phases"]] == ["Bi2Fe4O9.cif"]
    assert captured["instrument_profile"] == "test-device"
    assert captured["wavelength"] == "Cu"
    assert captured["refinement_params"] == document.search_kwargs["refinement_params"]
    assert captured["phase_params"] == document.final_refinement_params
    assert "max_phases" not in captured
    assert "max_num_results" not in captured
    assert Path("Bi2Fe4O9.cif").is_file()
    assert np.allclose(np.loadtxt("xrd_data.xy")[:, :2], [[10, 1], [20, 5], [30, 1]])
    assert isinstance(result, PhaseSearchDocument)
    assert [[cif.filename for cif in group] for group in result.results[0][0]] == [["Bi2Fe4O9"]]
    assert result.search_summary["parent_task_id"] == 7
    assert result.analysis_mode == "selected_phases"
    assert result.extra_peaks == [None]
    assert result.missing_peaks == ([[[25.0, 1.0]]] if has_metrics else [None])
    encoded = json.dumps(result, cls=MontyEncoder)
    restored = json.loads(encoded, cls=MontyDecoder)
    assert isinstance(restored, PhaseSearchDocument)
    assert restored.results[0][0][0][0].filename == "Bi2Fe4O9"
    assert restored.search_kwargs == result.search_kwargs


def test_selected_document_works_in_existing_result_api_and_archive(monkeypatch, references):
    document = _document(references)
    document.analysis_mode = "selected_phases"
    document.screening_rwps = [5.0]
    encoded = json.loads(json.dumps(document, cls=MontyEncoder))

    class Store:
        def __init__(self, record):
            self.record = record
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def query_one(self, criteria):
            return deepcopy(self.record)

    monkeypatch.setattr(api_router, "get_worker_store", lambda: Store(_record()))
    monkeypatch.setattr(api_router, "get_result_store", lambda: Store({"output": encoded}))
    summary = asyncio.run(api_router.result(7))
    assert summary["analysis_mode"] == "selected_phases"
    assert len(summary["all_results"]) == 1
    assert summary["all_results"][0]["phases"] == [["BiFeO3"], ["Bi2Fe4O9"]]
    assert summary["all_results"][0]["is_final_refined"] is True
    for index in (None, 0):
        archive = api_router._make_result_archive(7, document, summary, result_index=index)
        with zipfile.ZipFile(archive) as contents:
            assert "pattern.xy" in contents.namelist()
            assert "summary.json" in contents.namelist()
            assert any(name.endswith("refinement.csv") for name in contents.namelist())
            assert any(name.endswith("plot.html") for name in contents.namelist())


def test_maker_rejects_empty_and_duplicate_references(monkeypatch, tmp_path, references):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(selected_refinement, "do_refinement", lambda **kwargs: pytest.fail("Must reject before refinement"))
    maker = selected_refinement.SelectedPhaseRefinementMaker()
    for cifs in ([], [references[0], references[0]]):
        with pytest.raises(ValueError):
            maker.make.original(maker, XRDData([10, 20], [1, 2]), cifs, {})


@pytest.mark.parametrize("state, missing_result, code", [(None, False, 404), ("RUNNING", False, 409), ("FIZZLED", False, 409), ("COMPLETED", True, 404)])
def test_load_rejects_missing_and_incomplete_tasks(monkeypatch, state, missing_result, code):
    class Store:
        def __init__(self, value):
            self.value = value
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def query_one(self, criteria):
            return self.value

    record = None if state is None else {**_record(), "status": state}
    monkeypatch.setattr(selected_refinement_router, "get_worker_store", lambda: Store(record))
    monkeypatch.setattr(selected_refinement_router, "get_result_store", lambda: Store(None if missing_result else {}))
    with pytest.raises(HTTPException) as exc:
        selected_refinement_router._load(7)
    assert exc.value.status_code == code


def test_phase_options_preserve_exact_alternative_ids_without_duplicates(monkeypatch, references):
    document = _document(references)
    alternative = deepcopy(references[0])
    alternative.filename = "BiFeO3_161_(cod_1234567)-2"
    document.results[0][0][0].append(alternative)
    document.input_cifs.append(alternative)
    _patch_load(monkeypatch, document)
    response = asyncio.run(selected_refinement_router.phase_options(7))
    assert response["phases"] == ["BiFeO3", "BiFeO3_161_(cod_1234567)-2", "Bi2Fe4O9"]
    assert response["max_phases"] == 20
