"""Peak-candidate search: structure ranking for observed peak positions and its endpoint."""

import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from dara.cif import Cif
from dara.server import api_router, peak_candidates_router, peak_significance as ps
from dara.server import reference_library_router
from dara.utils import angular_correction

FWHM = 0.15
SIGMA_NOISE = math.sqrt(1000.0)
DATA = Path(__file__).parent / "test_data"


def _gauss(x, centre, height, fwhm=FWHM):
    return height * np.exp(-0.5 * ((x - centre) / (fwhm / 2.3548)) ** 2)


def _refinement(injected=(), fitted=((30.0, 10000.0),), eps=None, phases=()):
    rng = np.random.default_rng(0)
    x = np.arange(10.0, 80.0 + 1e-9, 0.01)
    bkg = np.full_like(x, 1000.0)
    calc = bkg + sum(_gauss(x, c, h) for c, h in fitted)
    obs = calc + rng.normal(0.0, SIGMA_NOISE, x.size) + sum(
        (_gauss(x, c, h) for c, h in injected), np.zeros_like(x)
    )
    lst = SimpleNamespace(phases_results={name: object() for name in phases}, **(eps or {}))
    return SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=calc, y_bkg=bkg),
        peak_data=None,
        lst_data=lst,
    )


def _pattern(name, sticks, formula, number=None, source="candidate", popularity=1):
    return {
        "name": name, "formula": formula, "spacegroup_number": number, "source": source,
        "popularity": popularity,
        "two_theta": np.array([s[0] for s in sticks], dtype=float),
        "intensity": np.array([s[1] for s in sticks], dtype=float),
    }


TARGETS = [35.4, 43.1]


def _rank(refinement, patterns, **kwargs):
    sigma, points = ps.noise_for(refinement, ps.resolve_wavelength("Cu"), True, None)
    return ps.rank_structures_for_peaks(
        kwargs.pop("two_theta", TARGETS), refinement, patterns,
        noise_sigma=sigma, smoothing_points=points, **kwargs,
    )


@pytest.fixture(scope="module")
def refinement():
    return _refinement(injected=[(35.4, 500.0), (43.1, 400.0)])


def test_noise_for_matches_the_classification(refinement):
    sigma, points = ps.noise_for(refinement, ps.resolve_wavelength("Cu"), True, None)
    classified = ps.classify_missing_peaks(
        refinement, [[35.4, 5]], wavelength=ps.resolve_wavelength("Cu"), counts_like=True
    )
    significance = classified["missing_significance"]
    assert (sigma, points) == (significance["noise_sigma"], significance["smoothing_points"])
    assert points == 7 and sigma == pytest.approx(SIGMA_NOISE, rel=0.1)
    assert ps.noise_for(SimpleNamespace(plot_data=None), None, None) == (0.0, 1)


def test_ranking_prefers_full_match_without_unobserved_strong_sticks(refinement):
    full = _pattern("Full_1_(a)-0", [(35.4, 100), (43.1, 80), (62.5, 5)], "Fe3O4", 227)
    extra = _pattern("Extra_2_(b)-0", [(35.4, 100), (43.1, 80), (50.0, 60), (60.0, 50)], "Fe2O3", 167)
    half = _pattern("Half_3_(c)-0", [(35.4, 100), (70.0, 100)], "FeO", 225)
    none = _pattern("None_4_(d)-0", [(20.0, 100), (60.0, 90)], "NiO", 225)
    ranked = _rank(refinement, [none, half, extra, full])
    assert [entry["name"] for entry in ranked] == ["Full_1_(a)-0", "Extra_2_(b)-0", "Half_3_(c)-0"]
    top, second, third = ranked
    assert top["score"] == 1.0 and top["strong_unobserved"] == 0 and top["n_strong"] == 2
    assert top["matched"] == TARGETS and top["pattern_source"] == "candidate"
    assert top["spacegroup_symbol"]  # looked up from the number
    assert second["strong_unobserved"] == 2 and second["n_strong"] == 4
    assert second["score"] == pytest.approx(0.75)
    assert third["matched"] == [35.4] and third["strong_unobserved"] == 1
    assert third["score"] == pytest.approx(0.25)
    # the preview ticks hold strong and medium sticks (>= 5 %), rounded
    assert [35.4, 100.0] in top["sticks"] and [62.5, 5.0] in top["sticks"]
    assert all(len(tick) == 2 for tick in top["sticks"])


def test_displacement_correction_is_honoured():
    eps = {"EPS1": 0.0, "EPS2": 0.003}
    shifted = _refinement(injected=[(35.4, 500.0), (43.1, 400.0)], eps=eps)
    shift = angular_correction(np.array(TARGETS), 0.0, 0.003)
    assert np.all(np.abs(shift) > 0.25)
    raw = [(35.4 - shift[0], 100), (43.1 - shift[1], 80)]
    pattern = _pattern("Shift_1_(a)-0", raw, "Fe3O4", 227)
    assert _rank(shifted, [pattern])[0]["score"] == 1.0
    # the same pattern misses both peaks when the profile has no displacement
    assert _rank(_refinement(injected=[(35.4, 500.0), (43.1, 400.0)]), [pattern]) == []


def test_refined_identity_excluded_unless_requested():
    refined = _refinement(injected=[(35.4, 500.0), (43.1, 400.0)], phases=["Fe3O4_227_(cod_1)-0"])
    pattern = _pattern("Fe3O4_227_(cod_9)-0", [(35.4, 100), (43.1, 80)], "Fe3O4", 227)
    assert _rank(refined, [pattern]) == []
    assert [e["formula"] for e in _rank(refined, [pattern], include_refined=True)] == ["Fe3O4"]


def test_dedup_by_formula_and_spacegroup_and_limit(refinement):
    a = _pattern("A_227_(1)-0", [(35.4, 100), (43.1, 80)], "Fe3O4", 227, popularity=1)
    b = _pattern("B_227_(2)-0", [(35.4, 100), (43.1, 80)], "Fe3O4", 227, popularity=9)
    c = _pattern("C_225_(3)-0", [(35.4, 100), (43.1, 80)], "FeO", 225, popularity=2)
    d = _pattern("D_62_(4)-0", [(35.4, 100), (43.1, 80)], "Fe2O3", 62, popularity=3)
    ranked = _rank(refinement, [a, b, c, d])
    assert [e["name"] for e in ranked] == ["B_227_(2)-0", "D_62_(4)-0", "C_225_(3)-0"]  # popularity order
    assert len(_rank(refinement, [a, b, c, d], limit=2)) == 2


def test_excluded_ranges_do_not_count_as_strong_sticks(refinement):
    pattern = _pattern("E_1_(a)-0", [(35.4, 100), (43.1, 80), (55.0, 70)], "Fe3O4", 227)
    plain = _rank(refinement, [pattern])[0]
    assert plain["n_strong"] == 3 and plain["strong_unobserved"] == 1
    excluded = _rank(refinement, [pattern], range_params={"excluded_ranges": [[54.0, 56.0]]})[0]
    assert excluded["n_strong"] == 2 and excluded["strong_unobserved"] == 0 and excluded["score"] == 1.0


# --- router ---------------------------------------------------------------------------------

def _record(status="COMPLETED"):
    return {"index": 7, "uuid": "u-7", "status": status, "job": {"name": "t.xy"}, "user": "tester"}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    refinement = _refinement(injected=[(35.4, 500.0), (43.1, 400.0)])
    cifs = [Cif.from_file(DATA / "BiFeO3.cif"), Cif.from_file(DATA / "Bi2Fe4O9.cif")]
    document = SimpleNamespace(
        results=[([[cifs[0]], [cifs[1]]], refinement)],
        input_cifs=[],
        final_result=refinement,
        elements=["Fe", "O"],
        precursors=["Fe2O3"],
        xrd_data=SimpleNamespace(angles=refinement.plot_data.x, intensities=refinement.plot_data.y_obs),
        search_kwargs={"wavelength": "Cu"},
    )
    sticks = {
        "BiFeO3": ([35.4, 43.1, 62.0], [100.0, 80.0, 25.0]),
        "Bi2Fe4O9": ([35.4, 50.0], [100.0, 70.0]),
    }
    calls = []

    def simulated(cif, stem, ctx, window, *, compute=True):
        calls.append(stem)
        two_theta, intensity = sticks[stem]
        return np.array(two_theta), np.array(intensity)

    monkeypatch.setattr(api_router, "_simulated_reference", simulated)
    monkeypatch.setattr(peak_candidates_router, "_load", lambda task_id: (_record(), document))
    monkeypatch.setattr(ps, "cod_reference_patterns", lambda *a, **k: None)
    monkeypatch.setattr("dara.structure_db.cod_cache_dir", lambda: tmp_path)
    return SimpleNamespace(document=document, cifs=cifs, calls=calls, tmp=tmp_path)


def _call(**body):
    body.setdefault("two_theta", TARGETS)
    request = peak_candidates_router.PeakCandidatesRequest(**body)
    return asyncio.run(peak_candidates_router.peak_candidates(7, request))


def test_endpoint_shape_and_cod_pending(setup):
    response = _call()
    assert set(response) == {
        "task_id", "sel", "two_theta", "tolerance_deg", "pools_used", "pools_pending",
        "candidates", "elapsed_s",
    }
    assert response["task_id"] == 7 and response["sel"] == "best"
    assert response["two_theta"] == TARGETS and response["tolerance_deg"] == 0.2
    assert response["pools_pending"] == ["cod"]
    assert response["pools_used"] == ["candidate", "input", "library"]
    first = response["candidates"][0]
    assert set(first) == {
        "ref", "source", "name", "formula", "spacegroup_number", "spacegroup_symbol",
        "pattern_source", "matched", "strong_unobserved", "n_strong", "score", "cif_available",
        "sticks",
    }
    assert first["ref"] == "BiFeO3" and first["source"] == "task"
    assert first["matched"] == TARGETS and first["score"] == pytest.approx(1.0 - 0.5 / 3, abs=1e-4)
    assert first["cif_available"] is True
    assert first["strong_unobserved"] == 1 and first["n_strong"] == 3
    assert response["candidates"][1]["ref"] == "Bi2Fe4O9"
    assert response["candidates"][1]["score"] == pytest.approx(0.25)
    assert response["elapsed_s"] < 2
    json.dumps(response)  # plain JSON types only


def test_warm_call_reuses_memo_and_is_fast(setup):
    first = _call()
    second = _call()
    assert second["candidates"] == first["candidates"]
    assert second["elapsed_s"] < 2


def test_cod_pool_when_ready(setup, monkeypatch):
    cod = [
        _pattern("Fe2O3_167_(cod_9015964)-0", [(35.4, 100), (43.1, 90)], "Fe2O3", 167,
                 source="cod", popularity=5) | {"code": "9015964"},
        _pattern("Fe2O3_62_(cod_7777)-0", [(35.4, 100), (43.1, 85)], "Fe2O3", 62,
                 source="cod", popularity=1) | {"code": "7777"},
    ]
    (setup.tmp / "9015964.cif").write_text("data_x", encoding="utf-8")
    monkeypatch.setattr(ps, "cod_reference_patterns", lambda *a, **k: cod)
    response = _call(pools=["cod"])
    assert response["pools_pending"] == [] and response["pools_used"] == ["cod"]
    by_ref = {c["ref"]: c for c in response["candidates"]}
    assert by_ref["Fe2O3_167_(cod_9015964)-0"]["source"] == "cod:9015964"
    assert by_ref["Fe2O3_167_(cod_9015964)-0"]["cif_available"] is True
    assert by_ref["Fe2O3_62_(cod_7777)-0"]["cif_available"] is False
    assert by_ref["Fe2O3_62_(cod_7777)-0"]["pattern_source"] == "cod"
    assert setup.calls == []  # task pools not requested


def test_library_pool(setup, monkeypatch, tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    item_id = "a" * 32
    (library / f"{item_id}.cif").write_text((DATA / "BiFeO3.cif").read_text(), encoding="utf-8")
    (library / "registry.json").write_text(json.dumps({"items": [{"id": item_id}, {"id": "bad"}]}))
    monkeypatch.setattr(reference_library_router, "library_directory", lambda: library)
    monkeypatch.setattr(
        "dara.search.prefilter.simulate_structure_reflections",
        lambda structure, wavelength, window: (np.array([35.4, 43.1]), np.array([100.0, 60.0]), None),
    )
    response = _call(pools=["library"])
    assert response["pools_used"] == ["library"] and response["pools_pending"] == []
    (entry,) = response["candidates"]
    assert entry["source"] == f"library:{item_id}" and entry["cif_available"] is True
    assert entry["ref"] == Cif.from_file(DATA / "BiFeO3.cif").name
    assert entry["matched"] == TARGETS and entry["pattern_source"] == "library"
    peak_candidates_router._LIBRARY_MEMO.clear()


@pytest.mark.parametrize(
    "body",
    [
        {"two_theta": []},
        {"two_theta": [30.0 + i for i in range(11)]},
        {"two_theta": TARGETS, "pools": ["nope"]},
        {"two_theta": TARGETS, "pools": []},
        {"two_theta": TARGETS, "tolerance_deg": 0.6},
        {"two_theta": TARGETS, "tolerance_deg": 0.01},
        {"two_theta": TARGETS, "limit": 0},
        {"two_theta": TARGETS, "limit": 51},
        {"two_theta": TARGETS, "extra": 1},
        {"two_theta": [float("nan")]},
    ],
)
def test_request_validation(body):
    with pytest.raises(ValidationError):
        peak_candidates_router.PeakCandidatesRequest(**body)


def test_out_of_range_and_versions_without_ws_a(setup, monkeypatch):
    with pytest.raises(HTTPException) as error:
        _call(two_theta=[5.0])
    assert error.value.status_code == 422
    monkeypatch.setattr(peak_candidates_router, "parse_selection", None)
    monkeypatch.setattr(peak_candidates_router, "resolve_refinement", None)
    with pytest.raises(HTTPException) as error:
        _call(sel="version:2")
    assert error.value.status_code == 422 and "not available" in error.value.detail
    assert _call(sel=0)["sel"] == 0
    assert _call(sel="best")["candidates"]


def test_version_selection_uses_resolve_refinement(setup, monkeypatch):
    other = _refinement(injected=[(35.4, 500.0)])
    seen = {}

    def parse(value):
        seen["parsed"] = value
        return ("version", 2)

    def resolve(document, record, sel):
        seen["resolved"] = sel
        return other, {"kind": "version"}

    monkeypatch.setattr(peak_candidates_router, "parse_selection", parse)
    monkeypatch.setattr(peak_candidates_router, "resolve_refinement", resolve)
    response = _call(sel="version:2")
    assert seen == {"parsed": "version:2", "resolved": ("version", 2)}
    assert response["sel"] == "version:2" and response["candidates"]
