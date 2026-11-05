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
    for module in (review_router, api_router):
        monkeypatch.setattr(module, "get_worker_store", env["workers"])
    monkeypatch.setattr(api_router, "get_result_store", env["results"])
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


def test_baseline_stale_flag(monkeypatch, api):
    from datetime import datetime

    from dara.server.versions_router import BASELINE_LABEL

    # The fixture task finished on 2026-09-08, before REFINEMENT_CODE_DATE.
    assert _call(versions_router.list_task_versions(7))["baseline_stale"] is True
    api["workers"].docs[0]["end_time"] = datetime(2026, 11, 5, 0, 0, 0)
    assert _call(versions_router.list_task_versions(7))["baseline_stale"] is False
    api["workers"].docs[0]["end_time"] = datetime(2026, 9, 8, 1, 0, 0)
    monkeypatch.setattr(quick_refine, "do_refinement", _sequence_fake([4.0]))

    async def main():
        job = await quick_refine.submit_quick_refine(7, QuickRefineRequest(controls=RefinementControls(), label=BASELINE_LABEL))
        await _wait(job["job_id"])

    asyncio.run(main())
    assert _call(versions_router.list_task_versions(7))["baseline_stale"] is False


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
    assert "Result shown: version 2" in html and "Removed" in html
    assert "version_2_report.html" in response.headers["content-disposition"]
    best = _call(review_router.download_report(7, None, False, None))
    assert "Result shown" not in best.body.decode("utf-8")
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
    assert "Result shown: version 2" in response.body.decode("utf-8")
    assert "adopted by the user" in response.body.decode("utf-8")


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


# --------------------------------------------------------------------------- quick checks / run settings
def _with_quick_check(monkeypatch, env):
    """v2 removes Bi2Fe4O9, v3 re-refines both phases, v4 adds Bi2Fe4O9 back as a quick check."""
    from dara.server.quick_refine import QuickPhase

    stored = _make_versions(monkeypatch, env, rwps=(3.0, 5.04))
    monkeypatch.setattr(quick_refine, "do_refinement", _sequence_fake([2.9]))
    other = env["references"][1].filename

    async def main():
        job = await quick_refine.submit_quick_refine(
            7, QuickRefineRequest(base="version:2", phases=[QuickPhase(ref=other)], origin="add_hint")
        )
        return await _wait(job["job_id"])

    assert asyncio.run(main())["version"] == 4
    stored = {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}
    assert stored[4]["refine_mode"] == "fast"
    return stored


def test_listing_and_detail_carry_the_run_settings(monkeypatch, api):
    _with_quick_check(monkeypatch, api)
    listing = {v["version"]: v for v in _call(versions_router.list_task_versions(7))["versions"]}
    assert (listing[2]["refine_mode"], listing[2]["iteration_cap"]) == ("full", quick_refine.QUICK_REFINE_ITMAX)
    assert (listing[4]["refine_mode"], listing[4]["iteration_cap"]) == ("fast", quick_refine.FAST_ADD_ITMAX)
    assert listing[1]["iteration_cap"] is None and listing[1]["fixed_phases"] == []
    for number in (2, 4):
        detail = _call(versions_router.get_task_version(7, number))
        for key in ("refine_mode", "fixed_phases", "iteration_cap"):
            assert detail[key] == listing[number][key], (number, key)
    # v1 detail uses the cached document like the listing does (n_unexplained filled in)
    assert quick_refine._DOCUMENTS.peek(TASK_UUID) is not None
    assert _call(versions_router.get_task_version(7, 1))["n_unexplained"] == listing[1]["n_unexplained"]
    assert listing[1]["n_unexplained"] is not None


def test_fit_compare_marks_quick_checks_as_not_comparable(monkeypatch, api):
    _with_quick_check(monkeypatch, api)
    compared = _call(versions_router.fit_compare(7, "version:3", "version:4"))
    assert (compared["a"]["refine_mode"], compared["b"]["refine_mode"]) == ("full", "fast")
    assert compared["verdict"]["better"] == "similar"  # 2.9 vs 5.04 would otherwise be "b"
    assert compared["verdict"]["note"] == review_router.QUICK_CHECK_MESSAGE
    full = _call(versions_router.fit_compare(7, "best", "version:2"))
    assert full["verdict"] == {"rwp_delta": -2.0, "better": "b"} and full["a"]["refine_mode"] == "full"


def test_quick_check_named_like_the_baseline_does_not_count_as_one(api):
    from dara.server.versions_router import BASELINE_LABEL

    task = api["workers"].docs[0]
    quick = {"label": BASELINE_LABEL, "refine_mode": "fast"}
    assert versions_router._baseline_stale(task, [quick]) is True
    assert versions_router._baseline_stale(task, [{**quick, "refine_mode": "full"}]) is False


def test_export_refuses_quick_checks_and_uses_the_version_settings(monkeypatch, api):
    from dara.export import bundles
    from dara.server import export_router

    stored = _with_quick_check(monkeypatch, api)
    captured = {}

    def fake_bundle(fmt, refinement, document, out_dir, references):
        captured.update(rwp=refinement.lst_data.rwp, document=document)
        (Path(out_dir) / "README.txt").write_text("x")

    monkeypatch.setattr(bundles, "build_bundle", fake_bundle)
    with pytest.raises(HTTPException) as exc:
        _call(export_router.export_task(7, "profex", None, "version:4"))
    assert exc.value.status_code == 409 and exc.value.detail == review_router.QUICK_CHECK_MESSAGE
    _call(export_router.export_task(7, "profex", None, "version:2"))
    document = captured["document"]
    assert captured["rwp"] == 3.0 and document.final_result.lst_data.rwp == 3.0
    assert document.search_kwargs["refinement_params"] == stored[2]["refinement_params"]
    assert document.final_refinement_params == stored[2]["phase_params"]


def test_export_defaults_to_the_adopted_selection(monkeypatch, api):
    from dara.export import bundles
    from dara.server import export_router

    monkeypatch.setattr(export_router.review_router, "get_worker_store", api["workers"])
    _with_quick_check(monkeypatch, api)
    captured = {}

    def fake_bundle(fmt, refinement, document, out_dir, references):
        captured.update(rwp=refinement.lst_data.rwp)
        (Path(out_dir) / "README.txt").write_text("x")

    monkeypatch.setattr(bundles, "build_bundle", fake_bundle)
    response = _call(export_router.export_task(7, "profex", None, None))
    assert captured["rwp"] == 5.0 and "profex.zip" in response.headers["content-disposition"]
    _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:2")))
    response = _call(export_router.export_task(7, "profex", None, None))
    assert captured["rwp"] == 3.0 and "profex_version_2.zip" in response.headers["content-disposition"]
    _call(export_router.export_task(7, "profex", None, "best"))  # an explicit choice still wins
    assert captured["rwp"] == 5.0
    _call(review_router.save_review(7, review_router.ReviewUpdate(selection=0)))
    response = _call(export_router.export_task(7, "profex", None, None))
    assert "profex_candidate_1.zip" in response.headers["content-disposition"]
    # A legacy adopted quick check is refused, like the report.
    api["workers"].docs[0]["review"] = {"selection": "version:4"}
    with pytest.raises(HTTPException) as exc:
        _call(export_router.export_task(7, "profex", None, None))
    assert exc.value.status_code == 409


def test_version_confidence_is_judged_against_the_original_candidates(monkeypatch, api):
    stored = _make_versions(monkeypatch, api)
    document = api["document"]
    view = versions.document_view(document, api["record"], stored[2])
    assert len(view.results) == len(document.results) + 1
    assert view.results[0][1].lst_data.rwp == 3.0 and view.final_result is view.results[0][1]
    assert view.results[1:] == document.results  # the original comparison set follows
    confidence = stored[2]["summary"].get("confidence")
    assert confidence and confidence["phases"]
    for verdict in confidence["phases"].values():
        assert verdict["evidence"]["n_candidates"] == 2  # was 1: a pool of the version alone


def test_table_export_lists_the_adopted_version(monkeypatch, api):
    import csv
    import io

    _with_quick_check(monkeypatch, api)
    _call(review_router.save_review(7, review_router.ReviewUpdate(selection="version:2")))
    response = _call(api_router.download_task_table(7))
    rows = list(csv.DictReader(io.StringIO(response.body.decode("utf-8-sig"))))
    adopted = [row for row in rows if row["candidate"] == "version 2"]
    assert adopted and all(row["adopted"] == "yes" for row in adopted)
    assert all(row["adopted"] == "" for row in rows if row["candidate"] == "final")
    assert not any(row["candidate"] == "version 4" for row in rows)  # quick checks never appear


# --------------------------------------------------------------------------- dangling bases
def test_a_base_pointing_at_a_deleted_quick_check_is_resolved(monkeypatch, api):
    """Records stored before the fix name the replaced quick check as their base."""
    _make_versions(monkeypatch, api)
    store = api["versions"]
    v2 = store.query_one({"uuid": TASK_UUID, "index": 2})
    v3 = store.query_one({"uuid": TASK_UUID, "index": 3})
    v2["origin"] = {"kind": "add_hint", "base": "best", "added": ["Bi2Fe4O9"], "removed": []}
    v2["refine_mode"] = "fast"
    v3["origin"] = {"kind": "refine_all", "base": "version:2", "added": [], "removed": []}
    v3["replaces"] = 2
    store.update([v2, v3])
    versions.tombstone_version(TASK_UUID, 2)
    listing = _call(versions_router.list_task_versions(7))
    entry = next(v for v in listing["versions"] if v["version"] == 3)
    assert entry["origin"]["base"] == "best"
    assert entry["origin"]["added"] == ["Bi2Fe4O9"] and entry["replaces"] == 2
    detail = _call(versions_router.get_task_version(7, 3))
    assert detail["origin"]["base"] == "best" and detail["origin"]["added"] == ["Bi2Fe4O9"]
    # The stored record is not rewritten.
    assert store.query_one({"uuid": TASK_UUID, "index": 3})["origin"]["base"] == "version:2"


def test_bases_follow_chains_and_unknown_numbers_fall_back_to_best():
    by_number = {
        2: {"index": 2, "deleted": True, "origin": {"base": "version:3"}},
        3: {"index": 3, "deleted": True, "origin": {"base": 0}},
        4: {"index": 4, "origin": {"base": "best"}},
    }
    resolve = versions_router._resolve_origin
    assert resolve({"origin": {"base": "version:2"}}, by_number)["base"] == 0
    assert resolve({"origin": {"base": "version:4"}}, by_number)["base"] == "version:4"
    assert resolve({"origin": {"base": "version:9"}}, by_number)["base"] == "best"
    assert resolve({"origin": {"base": 1}}, by_number)["base"] == 1
    assert resolve({"origin": None}, by_number) is None
    loop = {5: {"deleted": True, "origin": {"base": "version:6"}}, 6: {"deleted": True, "origin": {"base": "version:5"}}}
    assert resolve({"origin": {"base": "version:5"}}, loop)["base"] == "best"


def test_v1_peak_counts_are_filled_on_a_cold_server(monkeypatch, api):
    quick_refine._DOCUMENTS.clear()
    first = _call(versions_router.list_task_versions(7))["versions"][0]
    assert first["n_unexplained"] == 1  # the fixture fit has one missing peak
    assert _call(versions_router.get_task_version(7, 1))["n_unexplained"] == 1


def test_unexplained_count_matches_the_task_card():
    from dara.server.api_router import unexplained_count

    classified = {
        "counts": {"missing": 5},
        "counts_by_class": {"significant": 2, "minor": 2, "instrument": 1, "noise": 0},
    }
    # significant + minor, as the task card and the delta strip show (instrument/noise excluded)
    assert unexplained_count(classified) == 4
    assert unexplained_count({"counts": {"missing": 3}}) == 3  # unclassified payloads
    assert unexplained_count(None) is None
    entry = versions_router._version_entry(
        {"version": 2, "summary": {"peak_diagnostics": classified}}
    )
    assert entry["n_unexplained"] == 4
    lite = versions_router._lite(1, {"label": "x"}, {"peak_diagnostics": classified})
    assert lite["n_unexplained"] == 4


def test_merged_member_gets_the_same_cautions_as_a_displayed_entry(monkeypatch):
    # source index 4 was merged into another entry, so it has no all_results row of its own
    monkeypatch.setattr(versions_router, "_task_payload", lambda task, document: {"all_results": []})
    quality = {"rwp": 25.0, "gof": 6.0}
    details = {"A": {"weight_percent": 0.5, "weight_esd_percent": 0.1}}
    monkeypatch.setattr(api_router, "_fit_quality", lambda refinement: quality)
    monkeypatch.setattr(api_router, "_phase_refinement_details", lambda refinement, *_: details)
    monkeypatch.setattr(api_router, "_classified_peak_diagnostics", lambda *args, **kwargs: {})
    monkeypatch.setattr(api_router, "_weight_percent", lambda refinement: {"A": 0.5})
    monkeypatch.setattr(api_router, "_counts_like", lambda document: False)
    refinement = type("R", (), {"lst_data": type("L", (), {"rwp": 25.0})()})()
    summary = versions_router._side_summary({"uuid": "u"}, object(), 4, {}, refinement)
    expected = api_router.fit_cautions(quality, details, {}, counts_like=False)
    assert summary["cautions"] == expected
    assert {c["code"] for c in summary["cautions"]} >= {"poor_fit", "insignificant_phase", "rexp_unreliable"}
    lite = versions_router._lite(4, {}, summary)
    assert lite["cautions"] == expected
    assert lite["phase_identity"] == {"A": "name:A"}


def test_fit_compare_sides_carry_phase_identities():
    weights = {"Bi2O3_14_(cod_9012546)-0": 50.0, "Bi2O3_14_(cod_1010004)-0": 50.0}
    lite = versions_router._lite(0, {}, {"weight_percent": weights})
    assert lite["phase_identity"] == dict.fromkeys(weights, "Bi2O3_14")


def test_versions_and_compare_sides_carry_space_group_symbols():
    # two Fe2O3 polymorphs must be labelled R-3c / R-3m in the UI, not "SG 167" / "SG 166"
    entry = versions_router._version_entry({
        "version": 3, "phases": ["TiO2_136_(cod_1)-0", "Fe2O3_166_(cod_2)-0"],
        "origin": {"added": ["Fe2O3_166_(cod_2)-0"]},
        "summary": {"phases": ["TiO2_136_(cod_1)-0", "Fe2O3_166_(cod_2)-0"]},
    })
    assert entry["phase_symbols"] == {"TiO2_136_(cod_1)-0": "P4_2/mnm", "Fe2O3_166_(cod_2)-0": "R-3m"}
    lite = versions_router._lite(0, {}, {"weight_percent": {"Fe2O3_167_(cod_3)-0": 10.0, "odd name": 5.0}})
    assert lite["phase_symbols"] == {"Fe2O3_167_(cod_3)-0": "R-3c"}
    assert versions_router.phase_symbols(None, ["X_999_(cod_1)-0"]) == {}
