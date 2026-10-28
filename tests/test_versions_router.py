"""Versions, the selection grammar, plots, raw fit curves and fit comparison."""

import asyncio
import json
from functools import wraps
from pathlib import Path

import numpy as np
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from dara import refine
from dara.refinement_controls import RefinementControls
from dara.server import api_router, quick_refine, review_router, versions, versions_router
from dara.server.quick_refine import QuickRefineRequest
from test_quick_refine import (  # noqa: F401
    TASK_UUID,
    _refined,
    _wait,
    env,
    references,
)


def _sequence_fake(rwps, grids=None):
    """Fake ``do_refinement`` returning the next Rwp (and optionally a shifted grid)."""
    remaining = list(rwps)
    shifts = list(grids or [0.0] * len(remaining))

    @wraps(refine.do_refinement)
    def fake(**kwargs):
        names = [Path(p).stem for p in kwargs["phases"]]
        result = _refined([type("C", (), {"filename": name}) for name in names])
        result.lst_data.rwp = remaining.pop(0)
        shift = shifts.pop(0)
        if shift:
            result.plot_data.x = [value + shift for value in result.plot_data.x]
        return result

    return fake


@pytest.fixture
def api(monkeypatch, env):
    for module in (versions_router, review_router, api_router):
        monkeypatch.setattr(module, "get_worker_store", env["workers"])
    for module in (review_router, api_router):
        monkeypatch.setattr(module, "get_result_store", env["results"])
    env["workers"].docs[0]["summary"] = {
        "schema": 3, "final_rwp": 5.0, "gof": 2.5, "final_phases": ["BiFeO3", "Bi2Fe4O9"],
        "phases": [
            {"name": "BiFeO3", "weight_percent": 60.0}, {"name": "Bi2Fe4O9", "weight_percent": 40.0},
        ],
    }
    api_router._version_plot_json.cache_clear()
    api_router._plot_json.cache_clear()
    versions_router._TASK_PAYLOADS.clear()
    return env


def _make_versions(monkeypatch, env, rwps=(3.0, 5.04), grids=None):
    """v2 removes Bi2Fe4O9; v3 re-refines both phases with other controls."""
    monkeypatch.setattr(quick_refine, "do_refinement", _sequence_fake(rwps, grids))
    other = env["references"][1].filename

    async def main():
        first = await quick_refine.submit_quick_refine(7, QuickRefineRequest(remove=[other], origin="remove"))
        await _wait(first["job_id"])
        second = await quick_refine.submit_quick_refine(
            7, QuickRefineRequest(controls=RefinementControls(orientation="isotropic"), label="Isotropic")
        )
        await _wait(second["job_id"])

    asyncio.run(main())
    return {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}


def _call(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- selection grammar
@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "best"), ("best", "best"), (0, 0), (4, 4), ("3", 3), ("version:2", ("version", 2)),
        ("version:1", "best"), ("version:999999", ("version", 999999)),
    ],
)
def test_parse_selection_accepts(value, expected):
    assert versions.parse_selection(value) == expected


@pytest.mark.parametrize(
    "value", ["version:0", "v:2", True, False, -1, "version:", "version:1000000", "Best", "version:02", [], 1.5]
)
def test_parse_selection_rejects(value):
    with pytest.raises(HTTPException) as exc:
        versions.parse_selection(value)
    assert exc.value.status_code == 422


def test_format_selection_round_trips():
    for value in ("best", 3, "version:5"):
        assert versions.format_selection(versions.parse_selection(value)) == value


# --------------------------------------------------------------------------- versions API
def test_listing_synthesizes_v1_and_lists_saved_versions(monkeypatch, api):
    stored = _make_versions(monkeypatch, api)
    listing = _call(versions_router.list_task_versions(7))
    assert listing["adopted"] is None
    first, second, third = listing["versions"]
    assert (first["version"], first["label"], first["deletable"]) == (1, "Original analysis", False)
    assert first["rwp"] == 5.0 and first["gof"] == 2.5
    assert first["weight_percent"] == {"BiFeO3": 60.0, "Bi2Fe4O9": 40.0}
    assert first["origin"] == {"kind": "automatic"}
    assert (second["version"], second["rwp"], second["deletable"]) == (2, 3.0, True)
    assert second["origin"]["kind"] == "remove" and second["phase_stems"] == ["BiFeO3"]
    assert third["label"] == "Isotropic" and third["controls"]["orientation"] == "isotropic"
    assert second["elapsed_s"] is not None
    assert set(stored) == {2, 3}


def test_get_version_and_delete_rules(monkeypatch, api):
    _make_versions(monkeypatch, api)
    original = _call(versions_router.get_task_version(7, 1))
    assert original["use_task_payload"] is True and original["final_result"] is None
    detail = _call(versions_router.get_task_version(7, 2))
    assert detail["final_result"]["rwp"] == 3.0
    assert detail["effective_phase_params"]["BiFeO3"]["gewicht"] == "SPHAR4"
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.get_task_version(7, 9))
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        _call(versions_router.delete_task_version(7, 1))
    assert exc.value.status_code == 400

    _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:2")))
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.delete_task_version(7, 2))
    assert exc.value.status_code == 409
    assert _call(versions_router.delete_task_version(7, 3))["deleted"] is True
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.get_task_version(7, 3))
    assert exc.value.status_code == 404
    assert [v["version"] for v in _call(versions_router.list_task_versions(7))["versions"]] == [1, 2]
    assert _call(versions_router.list_task_versions(7))["adopted"] == "version:2"
    assert versions.next_version_number(TASK_UUID) == 4


def test_unfinished_tasks_have_no_versions(api):
    api["workers"].docs[0]["status"] = "RUNNING"
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.list_task_versions(7))
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- plot / curves / compare
def test_plot_accepts_version_selection(monkeypatch, api):
    _make_versions(monkeypatch, api)
    response = _call(api_router.plot(7, None, "version:2"))
    figure = json.loads(response.body)
    assert figure["data"], "plotly figure expected"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert figure["layout"]["meta"]["wavelength_angstrom"] > 1.5
    assert json.loads(_call(api_router.plot(7, None, "best")).body)["data"]
    assert json.loads(_call(api_router.plot(7, 0, None)).body)["data"]
    with pytest.raises(HTTPException) as exc:
        _call(api_router.plot(7, None, "version:8"))
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        _call(api_router.plot(7, 0, "version:2"))
    assert exc.value.status_code == 422


def test_fit_curves_shapes(monkeypatch, api):
    _make_versions(monkeypatch, api)
    response = _call(versions_router.fit_curves(7, "version:2"))
    payload = json.loads(response.body)
    assert response.headers["cache-control"] == "private, max-age=3600"
    n = len(payload["x"])
    assert n == 3 and payload["sel"] == "version:2" and payload["name"] == "test.xy"
    for key in ("y_obs", "y_calc", "y_bkg"):
        assert len(payload[key]) == n
    assert set(payload["phases"]) == {"BiFeO3"} and len(payload["phases"]["BiFeO3"]) == n
    assert payload["reflections"] == {"BiFeO3": [20.0]}
    assert payload["rwp"] == 3.0
    assert payload["excluded_ranges"] == [[18.0, 19.0]]
    assert payload["wavelength_angstrom"] > 1.5
    best = json.loads(_call(versions_router.fit_curves(7, "best")).body)
    assert set(best["phases"]) == {"BiFeO3", "Bi2Fe4O9"} and best["sel"] == "best"
    assert json.loads(_call(versions_router.fit_curves(7, "version:1")).body)["sel"] == "best"
    with pytest.raises(HTTPException):
        _call(versions_router.fit_curves(7, "version:7"))
    with pytest.raises(HTTPException):
        _call(versions_router.fit_curves(7, "nonsense"))


def test_fit_curves_round_to_six_significant_digits():
    assert versions_router._sig([1234.56789012, 0.000123456789, float("nan")]) == [1234.57, 0.000123457, None]


def test_fit_compare_verdicts(monkeypatch, api):
    _make_versions(monkeypatch, api)
    better = _call(versions_router.fit_compare(7, "best", "version:2"))
    assert better["a"]["sel"] == "best" and better["b"]["sel"] == "version:2"
    assert better["a"]["rwp"] == 5.0 and better["b"]["rwp"] == 3.0
    assert better["verdict"] == {"rwp_delta": -2.0, "better": "b"}
    assert better["b_only"] == [] and better["a_only"] == ["Bi2Fe4O9"] and better["common"] == ["BiFeO3"]
    assert len(better["x"]) == len(better["a_residual"]) == len(better["b_calculated"]) == 3
    assert better["wavelength_angstrom"] > 1.5
    assert better["b"]["label"].startswith("Removed")
    for side in ("a", "b"):
        assert {"gof", "chi_squared", "weight_percent", "mass_balance", "n_unexplained", "cautions"} <= set(better[side])
    # |delta| < 0.1 percentage points counts as similar (5.04 versus 5.0)
    similar = _call(versions_router.fit_compare(7, "best", "version:3"))
    assert similar["verdict"]["better"] == "similar"
    worse = _call(versions_router.fit_compare(7, "version:2", "best"))
    assert worse["verdict"]["better"] == "a"
    # values outside the analysis window are masked (12-29 degrees with 18-19 excluded)
    assert better["observed"][0] is None and better["observed"][1] == 5.0 and better["observed"][2] is None
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.fit_compare(7, "best", "version:1"))
    assert exc.value.status_code == 400


def test_fit_compare_rejects_different_grids(monkeypatch, api):
    _make_versions(monkeypatch, api, rwps=(3.0, 4.0), grids=[0.5, 0.0])
    with pytest.raises(HTTPException) as exc:
        _call(versions_router.fit_compare(7, "best", "version:2"))
    assert exc.value.status_code == 409
    assert "angular grids" in exc.value.detail
    assert _call(versions_router.fit_compare(7, "best", "version:3"))["verdict"]["better"] == "b"


# --------------------------------------------------------------------------- review / report / figure / export
def test_review_accepts_versions_and_rejects_missing_or_deleted(monkeypatch, api):
    _make_versions(monkeypatch, api)
    saved = _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:2", notes="n")))
    assert saved["selection"] == "version:2"
    assert _call(review_router.get_review(7))["selection"] == "version:2"
    assert _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:1")))["selection"] == "best"
    assert _call(review_router.save_review(7, review_router.ReviewUpdate(selection=0)))["selection"] == 0
    with pytest.raises(HTTPException) as exc:
        _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:9")))
    assert exc.value.status_code == 404
    _call(versions_router.delete_task_version(7, 3))
    with pytest.raises(HTTPException) as exc:
        _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:3")))
    assert exc.value.status_code == 404
    for bad in ("version:0", "version:x", "latest", True, -1.5):
        with pytest.raises(ValidationError):
            review_router.ReviewUpdate(selection=bad)


def test_select_refinement_keeps_legacy_messages(api):
    document = api["document"]
    assert review_router._select_refinement(document, None) is document.final_result
    assert review_router._select_refinement(document, "best") is document.final_result
    assert review_router._select_refinement(document, 0) is document.results[0][1]
    with pytest.raises(HTTPException) as exc:
        review_router._select_refinement(document, 9)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        review_router._select_refinement(document, 1.5)
    assert exc.value.status_code == 422


def test_report_and_figure_accept_sel(monkeypatch, api):
    _make_versions(monkeypatch, api)
    response = _call(review_router.download_report(7, None, False, "version:2"))
    html = response.body.decode("utf-8")
    assert "Version 2" in html and "Removed" in html
    assert "Minimum-Rwp final result" not in html
    assert "version_2_report.html" in response.headers["content-disposition"]
    best = _call(review_router.download_report(7, None, False, None))
    assert "Minimum-Rwp final result" in best.body.decode("utf-8")
    with pytest.raises(HTTPException) as exc:
        _call(review_router.download_report(7, 0, True, None))
    assert exc.value.status_code == 422
    # idx wins over sel, best wins over sel
    by_idx = _call(review_router.download_report(7, 0, False, "version:2"))
    assert "candidate_1_report" in by_idx.headers["content-disposition"]
    by_best = _call(review_router.download_report(7, None, True, "version:2"))
    assert "best_report" in by_best.headers["content-disposition"]

    captured = {}
    import dara.server.report.figures as figures

    def fake_figure(refinement, **kwargs):
        captured.update(rwp=refinement.lst_data.rwp, **kwargs)
        return b"<svg/>"

    monkeypatch.setattr(figures, "fit_figure", fake_figure)
    figure = _call(review_router.download_figure(7, "svg", None, False, 600, 170, "version:2"))
    assert figure.body == b"<svg/>" and captured["rwp"] == 3.0
    assert captured["excluded_ranges"] == [[18.0, 19.0]]
    assert "version_2_figure.svg" in figure.headers["content-disposition"]


def test_report_defaults_to_the_adopted_version(monkeypatch, api):
    _make_versions(monkeypatch, api)
    _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:2")))
    response = _call(review_router.download_report(7, None, False, None))
    assert "Version 2" in response.body.decode("utf-8")
    assert "User-adopted result" in response.body.decode("utf-8")


def test_export_accepts_sel(monkeypatch, api):
    from dara.export import bundles
    from dara.server import export_router

    monkeypatch.setattr(export_router.review_router, "get_worker_store", api["workers"])
    _make_versions(monkeypatch, api)
    captured = {}

    def fake_bundle(fmt, refinement, document, out_dir, references):
        captured.update(fmt=fmt, rwp=refinement.lst_data.rwp, names=[cif.filename for cif in references])
        (Path(out_dir) / "README.txt").write_text("x")

    monkeypatch.setattr(bundles, "build_bundle", fake_bundle)
    response = _call(export_router.export_task(7, "gsas2", None, "version:2"))
    assert captured == {"fmt": "gsas2", "rwp": 3.0, "names": ["BiFeO3"]}
    assert "gsas2_version_2.zip" in response.headers["content-disposition"]
    _call(export_router.export_task(7, "gsas2", None, "best"))
    assert captured["rwp"] == 5.0
    _call(export_router.export_task(7, "gsas2", 0, None))
    assert captured["rwp"] == 5.0
    with pytest.raises(HTTPException) as exc:
        _call(export_router.export_task(7, "gsas2", 0, "version:2"))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        _call(export_router.export_task(7, "gsas2", None, "version:8"))
    assert exc.value.status_code == 404


def test_version_records_survive_a_json_round_trip(monkeypatch, api):
    stored = _make_versions(monkeypatch, api)
    record = json.loads(json.dumps(stored[2], default=str))
    assert record["result"]["@class"] == "RefinementResult"
    assert np.isclose(versions.version_refinement(stored[2]).lst_data.rwp, 3.0)
    assert record["summary"]["fit_quality"]["rwp"] == 3.0
