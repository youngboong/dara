"""Size / signal-to-noise aware classification of unexplained peaks (synthetic profiles)."""

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dara.server import peak_significance as ps
from dara.utils import angular_correction

FWHM = 0.15
SIGMA_NOISE = math.sqrt(1000.0)


def _gauss(x, centre, height, fwhm=FWHM):
    return height * np.exp(-0.5 * ((x - centre) / (fwhm / 2.3548)) ** 2)


def _refinement(injected=(), fitted=((30.0, 10000.0),), peak_data=None, eps=None, phases=()):
    """Flat 1000-count background, fitted Gaussians in obs and calc, injected extras in obs."""
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
        peak_data=peak_data,
        lst_data=lst,
    )


def _classify(refinement, missing, **kwargs):
    kwargs.setdefault("wavelength", ps.resolve_wavelength("Cu"))
    kwargs.setdefault("counts_like", True)
    return ps.classify_missing_peaks(refinement, missing, **kwargs)


def _by_angle(result):
    return {round(item["two_theta"]): item for item in result["missing_classified"]}


# --- 1. classes, dedupe, rounding -----------------------------------------------------------

def test_classes_dedupe_and_counts():
    refinement = _refinement(injected=[(40.0, 400.0), (45.0, 150.0), (50.0, 20.0)])
    result = _classify(refinement, [[40, 5], [45, 3], [50, 1], [45.02, 3]])
    entries = _by_angle(result)
    assert len(result["missing_classified"]) == 3  # 45.02 is a duplicate of 45
    assert entries[40]["class"] == "significant"
    assert entries[45]["class"] == "minor"
    assert entries[50]["class"] == "noise"
    assert entries[40]["rel_height"] == pytest.approx(0.04, rel=0.10)
    assert entries[45]["rel_height"] == pytest.approx(0.015, rel=0.10)
    assert entries[40]["snr"] > 8
    assert entries[40]["source"] == "peak"
    assert entries[40]["attribution"] is None and entries[40]["hint"] is None
    assert result["counts_by_class"] == {
        "noise": 1, "minor": 1, "significant": 1, "instrument": 0, "total": 3,
    }
    significance = result["missing_significance"]
    assert significance["schema"] == 1
    assert significance["smoothing_points"] == 7  # 0.01 degree step
    assert significance["anode"] == "cu" and significance["wavelength_a"] == pytest.approx(1.540598)
    assert significance["largest_significant_rel"] == entries[40]["rel_height"]
    assert significance["largest_minor_rel"] == entries[45]["rel_height"]
    assert significance["significant_rel_sum"] == entries[40]["rel_height"]
    assert significance["thresholds"] == {
        "noise_rel": 0.005, "noise_snr": 3.0, "significant_rel": 0.02, "significant_snr": 8.0,
    }
    assert result["trace_hints"] == []
    # rounding: 2theta 3 dp, heights 1 dp, rel 4 dp, snr 1 dp
    item = entries[40]
    assert item["two_theta"] == round(item["two_theta"], 3)
    assert item["height"] == round(item["height"], 1) and item["snr"] == round(item["snr"], 1)
    assert item["rel_height"] == round(item["rel_height"], 4)
    assert [e["two_theta"] for e in result["missing_classified"]] == sorted(
        e["two_theta"] for e in result["missing_classified"]
    )


def test_listed_peaks_sharing_one_residual_maximum_count_once():
    refinement = _refinement(injected=[(40.0, 400.0)])
    for offset in (0.06, 0.10, 0.15):
        result = _classify(refinement, [[40.0, 5], [40.0 + offset, 5]])
        assert len(result["missing_classified"]) == 1, offset
        assert result["counts_by_class"]["significant"] == 1
        assert result["missing_significance"]["significant_rel_sum"] == pytest.approx(0.04, rel=0.10)
    # two real features at distinct positions stay two entries
    two = _refinement(injected=[(40.0, 400.0), (40.6, 400.0)])
    assert len(_classify(two, [[40.0, 5], [40.6, 5]])["missing_classified"]) == 2


def test_instrument_line_phrase_names_the_actual_line():
    kb = {"attribution": {"type": "kbeta"}}
    wl = {"attribution": {"type": "w_lalpha"}}
    assert ps.instrument_line_phrase([kb], "cu") == "Cu K\u03b2"
    assert ps.instrument_line_phrase([wl, wl], "cu") == "W L\u03b11"
    assert ps.instrument_line_phrase([kb, wl], "cu") == "Cu K\u03b2 or W L\u03b11"
    assert ps.instrument_line_phrase([kb], None) == "K\u03b2"
    assert ps.instrument_line_phrase([{"attribution": None}], "cu") == "Cu K\u03b2 or W L\u03b11"


def test_counts_like_false_uses_only_the_mad_noise():
    refinement = _refinement(injected=[(40.0, 400.0)])
    counts = _by_angle(_classify(refinement, [[40, 5]], counts_like=True))[40]
    cps = _by_angle(_classify(refinement, [[40, 5]], counts_like=False))[40]
    assert cps["snr"] >= counts["snr"]


def test_analysis_range_excludes_entries():
    refinement = _refinement(injected=[(40.0, 400.0), (45.0, 400.0)])
    result = _classify(
        refinement, [[40, 5], [45, 5]], range_params={"excluded_ranges": [[39.5, 40.5]], "x": 1}
    )
    assert [round(e["two_theta"]) for e in result["missing_classified"]] == [45]


# --- 2. residual region ----------------------------------------------------------------------

def test_residual_region_entry_is_measured_from_the_profile():
    refinement = _refinement(injected=[(60.0, 300.0)])
    result = _classify(refinement, [[60.0, 0.0]])
    item = result["missing_classified"][0]
    assert item["source"] == "residual_region"
    assert item["class"] == "significant" and item["rel_height"] == pytest.approx(0.03, rel=0.1)


# --- 3. instrument lines -----------------------------------------------------------------------

def _peak_data(rows):
    return pd.DataFrame(
        rows,
        columns=["2theta", "intensity", "b1", "b2", "h", "k", "l", "phase", "phase_idx"],
    )


def _satellite_refinement(injected):
    data = _peak_data([
        [36.0, 100.0, 0.0, 0.0, 1, 0, 4, "P_1_(cod_1)-0", 0],
        [60.0, 5.0, 0.0, 0.0, 2, 1, 0, "P_1_(cod_1)-0", 0],  # weak reflection: not a parent
    ])
    return _refinement(injected=injected, fitted=((36.0, 10000.0),), peak_data=data)


def _kbeta_angle(parent=36.0):
    return 2 * math.degrees(math.asin(math.sin(math.radians(parent / 2)) * 1.392250 / 1.540598))


def test_kbeta_and_w_lalpha_satellites_are_instrument_lines():
    assert _kbeta_angle() == pytest.approx(32.43, abs=0.01)
    refinement = _satellite_refinement([(32.43, 80.0), (34.45, 80.0)])
    result = _classify(refinement, [[32.43, 2], [34.45, 2]])
    kb, wl = (item for item in result["missing_classified"])
    assert kb["class"] == "instrument" and wl["class"] == "instrument"
    assert kb["attribution"]["type"] == "kbeta"
    assert kb["attribution"]["parent_two_theta"] == pytest.approx(36.0, abs=0.01)
    assert kb["attribution"]["parent_hkl"] == [1, 0, 4]
    assert kb["attribution"]["parent_phase"] == "P_1_(cod_1)-0"
    assert kb["attribution"]["wavelength_a"] == pytest.approx(1.39225, abs=1e-4)
    assert wl["attribution"]["type"] == "w_lalpha"
    assert wl["attribution"]["wavelength_a"] == pytest.approx(1.47639, abs=1e-4)
    assert kb["snr"] > ps.NOISE_SNR and kb["rel_height"] < 0.02
    assert result["counts_by_class"]["instrument"] == 2
    assert result["missing_significance"]["largest_significant_rel"] is None


def test_strong_satellite_candidate_is_not_attributed():
    refinement = _satellite_refinement([(32.43, 600.0)])  # 6 % of its parent
    item = _classify(refinement, [[32.43, 2]])["missing_classified"][0]
    assert item["attribution"] is None and item["class"] == "significant"


def test_no_attribution_without_a_known_anode():
    refinement = _satellite_refinement([(32.43, 80.0)])
    numeric = _classify(refinement, [[32.43, 2]], wavelength=ps.resolve_wavelength(0.15406))
    assert numeric["missing_classified"][0]["class"] == "minor"
    assert numeric["missing_significance"]["anode"] is None
    unknown = _classify(refinement, [[32.43, 2]], wavelength=None)
    assert unknown["missing_classified"][0]["class"] == "minor"
    # the same peak without any peak_data cannot be attributed either
    bare = _refinement(injected=[(32.43, 80.0)], fitted=((36.0, 10000.0),))
    assert _classify(bare, [[32.43, 2]])["missing_classified"][0]["class"] == "minor"


# --- 4. wavelength ----------------------------------------------------------------------------

def test_resolve_wavelength():
    for spec in ("Cu", "cu", " CU "):
        info = ps.resolve_wavelength(spec)
        assert info["anode"] == "cu" and info["alpha1_a"] == pytest.approx(1.54060, abs=1e-5)
        assert info["kbeta_a"] == pytest.approx(1.39225, abs=1e-5)
    for spec in (0.15406, "0.15406"):
        info = ps.resolve_wavelength(spec)
        assert info == {"alpha1_a": pytest.approx(1.5406), "kbeta_a": None, "anode": None}
    for bad in ("xx", None, "", float("nan"), -1.0, object()):
        assert ps.resolve_wavelength(bad) is None


# --- 5. trace-phase hints ------------------------------------------------------------------------

def _pattern(name, formula, positions, intensities, *, sg=100, source="cod", popularity=1,
             symbol=None):
    return {
        "name": name, "formula": formula, "spacegroup_number": sg, "spacegroup_symbol": symbol,
        "source": source, "popularity": popularity,
        "two_theta": np.array(positions, float), "intensity": np.array(intensities, float),
    }


def _two_minor_peaks(**kwargs):
    refinement = _refinement(injected=[(40.0, 150.0), (45.0, 150.0)], **kwargs)
    classified = _classify(refinement, [[40, 3], [45, 3]])
    assert [e["class"] for e in classified["missing_classified"]] == ["minor", "minor"]
    return refinement, classified


def test_hint_requires_two_strong_lines_and_names_the_phase():
    refinement, classified = _two_minor_peaks()
    both = _pattern("Hem", "Fe2O3", [40.0, 45.05, 60.0], [100, 60, 10], sg=167, symbol="R-3c")
    one_of_two = _pattern("Half", "ZnO", [40.0, 55.0], [100, 100])
    out = ps.attach_trace_hints(classified, refinement, [one_of_two, both])
    assert out is classified
    for entry in out["missing_classified"]:
        assert entry["hint"]["phase"] == "Hem" and entry["hint"]["formula"] == "Fe2O3"
        assert entry["hint"]["spacegroup_number"] == 167
        assert entry["hint"]["spacegroup_symbol"] == "R-3c"
        assert entry["hint"]["source"] == "cod"
        assert entry["hint"]["matched_peaks"] == pytest.approx([40.0, 45.0], abs=0.01)
    assert [h["formula"] for h in out["trace_hints"]] == ["Fe2O3"]


def test_hint_without_enough_matches_and_single_line_pattern():
    refinement, classified = _two_minor_peaks()
    out = ps.attach_trace_hints(classified, refinement, [_pattern("Half", "ZnO", [40.0, 55.0], [100, 100])])
    assert all(entry["hint"] is None for entry in out["missing_classified"])
    assert out["trace_hints"] == []

    refinement, classified = _two_minor_peaks()
    single = _pattern("One", "NiO", [40.1, 70.0], [100, 10])  # only one strong stick
    out = ps.attach_trace_hints(classified, refinement, [single])
    assert out["missing_classified"][0]["hint"]["formula"] == "NiO"
    assert out["missing_classified"][1]["hint"] is None


def test_hint_needs_the_strongest_line_to_be_observed():
    refinement, classified = _two_minor_peaks()
    unseen = _pattern("Top", "MgO", [40.0, 45.0, 52.0], [50, 40, 100])
    assert ps.attach_trace_hints(classified, refinement, [unseen])["trace_hints"] == []
    # the strongest line may sit under a fitted phase (30 degrees carries a fitted peak)
    refinement, classified = _two_minor_peaks()
    covered = _pattern("Cov", "MgO", [30.0, 40.0, 45.0], [100, 50, 40])
    assert [h["formula"] for h in ps.attach_trace_hints(classified, refinement, [covered])["trace_hints"]] == ["MgO"]


def test_hint_applies_the_eps_correction_to_the_simulated_sticks():
    eps = {"EPS2": (-0.0027, 0.0001)}
    refinement, classified = _two_minor_peaks(eps=eps)
    shift = angular_correction(np.array([40.0, 45.0]), 0.0, -0.0027)
    assert shift[0] > 0.2  # larger than the matching tolerance
    shifted = _pattern("Hem", "Fe2O3", [40.0 - shift[0], 45.0 - shift[1]], [100, 70])
    assert ps.attach_trace_hints(classified, refinement, [shifted])["trace_hints"]
    # without the EPS2 refinement the same sticks are too far away
    refinement, classified = _two_minor_peaks()
    assert ps.attach_trace_hints(classified, refinement, [shifted])["trace_hints"] == []


def test_hint_excludes_refined_phases_and_ranks_candidates():
    refinement, classified = _two_minor_peaks(phases=("Fe2O3_167_(cod_1)-0",))
    sticks = ([40.0, 45.0], [100, 70])
    patterns = [
        _pattern("refined", "Fe2O3", *sticks, sg=167, popularity=99),
        _pattern("dirty", "Fe2.5O3", *sticks, sg=12, popularity=50),
        _pattern("rare", "AlN", *sticks, sg=186, popularity=1),
        _pattern("common", "CuO", *sticks, sg=15, popularity=3),
    ]
    out = ps.attach_trace_hints(classified, refinement, patterns)
    assert [h["formula"] for h in out["trace_hints"]] == ["CuO", "AlN", "Fe2.5O3"]
    assert out["missing_classified"][0]["hint"]["formula"] == "CuO"
    assert all(h["formula"] != "Fe2O3" for h in out["trace_hints"])
    assert len(out["trace_hints"]) <= ps.TRACE_HINTS_MAX


def test_hints_are_limited_to_minor_and_significant_peaks():
    refinement = _refinement(injected=[(40.0, 20.0), (45.0, 20.0)])
    classified = _classify(refinement, [[40, 3], [45, 3]])
    assert {e["class"] for e in classified["missing_classified"]} == {"noise"}
    out = ps.attach_trace_hints(classified, refinement, [_pattern("x", "CuO", [40.0, 45.0], [100, 70])])
    assert out["trace_hints"] == []


# --- 6. robustness ------------------------------------------------------------------------------

def test_classification_returns_none_instead_of_raising():
    full = _refinement(injected=[(40.0, 400.0)])
    only_x = SimpleNamespace(plot_data=SimpleNamespace(x=full.plot_data.x))
    assert _classify(only_x, [[40, 5]]) is None
    assert _classify(full, None) is None
    assert _classify(SimpleNamespace(), [[40, 5]]) is None
    assert _classify(None, [[40, 5]]) is None
    mismatched = SimpleNamespace(plot_data=SimpleNamespace(
        x=full.plot_data.x, y_obs=full.plot_data.y_obs[:-1],
        y_calc=full.plot_data.y_calc, y_bkg=full.plot_data.y_bkg,
    ))
    assert _classify(mismatched, [[40, 5]]) is None
    few = SimpleNamespace(plot_data=SimpleNamespace(
        x=np.arange(5.0), y_obs=np.ones(5), y_calc=np.ones(5), y_bkg=np.ones(5)
    ))
    assert _classify(few, [[1, 5]]) is None
    nan_profile = SimpleNamespace(plot_data=SimpleNamespace(
        x=np.full(50, np.nan), y_obs=np.full(50, np.nan), y_calc=np.full(50, np.nan),
        y_bkg=np.full(50, np.nan),
    ))
    assert _classify(nan_profile, [[40, 5]]) is None
    flat = SimpleNamespace(plot_data=SimpleNamespace(
        x=np.arange(100.0), y_obs=np.zeros(100), y_calc=np.zeros(100), y_bkg=np.zeros(100)
    ))
    assert _classify(flat, [[40, 5]]) is None  # no positive reference height
    assert _classify(full, [["bad"], [None, 1], [float("nan"), 1], []]) ["counts_by_class"]["total"] == 0
    assert _classify(full, [])["counts_by_class"] == {
        "noise": 0, "minor": 0, "significant": 0, "instrument": 0, "total": 0,
    }


def test_nan_samples_do_not_break_the_classification():
    refinement = _refinement(injected=[(40.0, 400.0)])
    refinement.plot_data.y_obs[100:110] = np.nan
    result = _classify(refinement, [[40, 5]])
    assert result["missing_classified"][0]["class"] == "significant"


def test_attach_trace_hints_never_raises():
    refinement, classified = _two_minor_peaks()
    before = [dict(entry) for entry in classified["missing_classified"]]
    garbage = [None, "x", {}, {"formula": 1}, {"formula": "A", "two_theta": [1, 2], "intensity": [1]},
               _pattern("nan", "B", [np.nan, np.nan], [1, 2])]
    out = ps.attach_trace_hints(classified, refinement, garbage)
    assert out is classified and out["missing_classified"] == before and out["trace_hints"] == []
    assert ps.attach_trace_hints(None, refinement, garbage) is None
    assert ps.attach_trace_hints({"missing_classified": "x"}, None, None) == {"missing_classified": "x"}
    assert ps.attach_trace_hints(classified, SimpleNamespace(), garbage) is classified


# --- 7. COD reference pool -------------------------------------------------------------------------

@pytest.fixture
def fake_cod(monkeypatch, tmp_path):
    from dara import structure_db
    from dara.cif import Cif
    from dara.search import prefilter

    for code in (111, 112, 113):
        (tmp_path / f"{code}.cif").write_text("data_x")
    rows = [
        ("Fe2O3", 111, 167, 0.0),
        ("Fe4O6", 112, 167, 0.0),  # same reduced formula and space group: popularity 2
        ("FeO", 113, 225, 0.0),
        ("TiO2", 114, 136, 0.0),  # not cached: never downloaded
    ]
    calls = []

    def fake_rows(elements, precursors, hull):
        calls.append((tuple(elements), tuple(precursors), hull))
        return list(rows)

    class FakeCif:
        @staticmethod
        def from_file(path):
            return SimpleNamespace(to_structure=lambda: SimpleNamespace(
                volume=100.0, path=str(path)))

    def fake_simulate(structure, wavelength_a, two_theta_range):
        return np.array([30.0, 40.0]), np.array([100.0, 50.0]), np.array([3.0, 2.0])

    monkeypatch.setattr(structure_db, "cod_candidate_rows", fake_rows)
    monkeypatch.setattr(structure_db, "cod_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(Cif, "from_file", FakeCif.from_file)
    monkeypatch.setattr(prefilter, "simulate_structure_reflections", fake_simulate)
    ps._CACHE.clear()
    ps._PENDING.clear()
    ps._FAILED.clear()
    yield calls
    ps.wait_idle(10)
    ps._STOP.clear()
    ps._CACHE.clear()
    ps._PENDING.clear()
    ps._FAILED.clear()


def _wait_for_background():
    assert ps.wait_idle(30)


def test_cod_reference_patterns_modes(fake_cod):
    args = (["Fe", "O"], ["Fe2O3"], 1.540598, (10.0, 80.0))
    assert ps.cod_reference_patterns(*args, mode="off") is None
    assert ps.cod_reference_patterns([], [], 1.54, (10.0, 80.0), mode="sync") is None
    assert ps.cod_reference_patterns(*args, mode="bogus") is None
    assert fake_cod == []

    assert ps.cod_reference_patterns(*args, mode="background") is None  # computed in the background
    _wait_for_background()
    patterns = ps.cod_reference_patterns(*args, mode="background")
    assert patterns is not None and len(fake_cod) == 1
    by_formula = {p["formula"]: p for p in patterns}
    assert set(by_formula) == {"Fe2O3", "FeO"}  # the uncached TiO2 row was skipped
    assert by_formula["Fe2O3"]["popularity"] == 2 and by_formula["FeO"]["popularity"] == 1
    assert by_formula["Fe2O3"]["spacegroup_number"] == 167
    assert by_formula["Fe2O3"]["spacegroup_symbol"] == "R-3c"
    assert by_formula["Fe2O3"]["source"] == "cod"
    assert by_formula["Fe2O3"]["two_theta"].tolist() == [30.0, 40.0]
    # served from the cache afterwards, also in sync mode
    assert ps.cod_reference_patterns(*args, mode="sync") is not None and len(fake_cod) == 1


def test_cod_reference_patterns_sync_and_failures(fake_cod, monkeypatch):
    patterns = ps.cod_reference_patterns(["Fe"], [], 1.54, (10.0, 80.0), mode="sync")
    assert len(patterns) == 2
    from dara import structure_db

    def broken(*args, **kwargs):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(structure_db, "cod_candidate_rows", broken)
    assert ps.cod_reference_patterns(["Ni"], [], 1.54, (10.0, 80.0), mode="sync") == []


def test_cod_cache_is_bounded(fake_cod):
    for step in range(ps._CACHE_MAX + 3):
        ps.cod_reference_patterns(["Fe"], [], 1.54, (10.0, 60.0 + step), mode="sync")
    assert len(ps._CACHE) == ps._CACHE_MAX


def test_worker_is_a_daemon_thread_so_shutdown_never_waits_for_it(fake_cod):
    ps.cod_reference_patterns(["Fe", "O"], [], 1.54, (10.0, 80.0), mode="background")
    _wait_for_background()
    assert ps._WORKER is not None and ps._WORKER.daemon
    assert not hasattr(ps, "_EXECUTOR")


def test_queue_is_bounded_deduped_and_drops_the_oldest(fake_cod):
    import threading

    gate = threading.Event()
    ran = []
    ps.submit_background("blocker", lambda: gate.wait(10))
    for _ in range(200):  # wait until the blocker is running
        if ps._ACTIVE == "blocker":
            break
        threading.Event().wait(0.01)
    assert ps._ACTIVE == "blocker"
    assert ps.submit_background("k0", lambda: ran.append("k0")) is True
    assert ps.submit_background("k0", lambda: ran.append("dup")) is False  # same key queued once
    for i in range(1, ps.PENDING_MAX + 3):
        ps.submit_background(f"k{i}", lambda i=i: ran.append(f"k{i}"))
    assert len(ps._QUEUE) == ps.PENDING_MAX
    assert "k0" not in ps._PENDING and "k1" not in ps._PENDING  # oldest dropped
    gate.set()
    assert ps.wait_idle(10)
    assert "k0" not in ran and "dup" not in ran
    assert ran[-1] == f"k{ps.PENDING_MAX + 2}" and len(ran) == ps.PENDING_MAX
    assert not ps._PENDING


def test_stop_event_aborts_the_cif_loop_and_nothing_is_cached(fake_cod, monkeypatch):
    from dara.cif import Cif

    seen = []

    def stopping_from_file(path):
        seen.append(path)
        ps._STOP.set()  # the shutdown request arrives while the first CIF is simulated
        return SimpleNamespace(to_structure=lambda: SimpleNamespace(volume=100.0))

    monkeypatch.setattr(Cif, "from_file", stopping_from_file)
    with pytest.raises(ps._Stopped):
        ps._simulate_cod_pool(["Fe"], [], 1.54, (10.0, 80.0))
    assert len(seen) == 1  # the remaining CIFs were not processed

    ps._STOP.clear()
    ps._background_job("stopped", (["Fe"], [], 1.54, (10.0, 80.0)))
    assert "stopped" not in ps._CACHE and "stopped" not in ps._FAILED  # not cached, not a failure
    ps._STOP.clear()


def test_shutdown_stops_a_busy_worker_quickly(fake_cod):
    import threading
    import time

    started = threading.Event()

    def job():
        started.set()
        while not ps.stop_requested():
            time.sleep(0.01)

    ps.submit_background("busy", job)
    assert started.wait(5)
    t0 = time.monotonic()
    ps.shutdown(timeout=5)
    assert time.monotonic() - t0 < 2
    assert not ps._WORKER.is_alive()
    assert ps.submit_background("late", lambda: None) is False  # refused after shutdown


def test_failed_and_empty_pools_are_retried_not_cached_forever(fake_cod, monkeypatch):
    from dara import structure_db

    args = (["Zn"], [], 1.54, (10.0, 80.0))
    good_rows = structure_db.cod_candidate_rows

    def broken(*a, **k):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(structure_db, "cod_candidate_rows", broken)
    assert ps.cod_reference_patterns(*args, mode="background") is None
    _wait_for_background()
    assert not ps._CACHE and len(ps._FAILED) == 1  # the failure is not cached as a pool
    assert ps.cod_reference_patterns(*args, mode="background") is None  # cooling down
    assert not ps._PENDING

    monkeypatch.setattr(structure_db, "cod_candidate_rows", good_rows)
    monkeypatch.setattr(ps, "FAILURE_COOLDOWN_S", 0.0)  # cooldown elapsed
    assert ps.cod_reference_patterns(*args, mode="background") is None
    _wait_for_background()
    assert len(ps.cod_reference_patterns(*args, mode="background")) == 2
    assert not ps._FAILED

    # an empty pool (nothing downloaded yet) is looked up again after the retry interval
    monkeypatch.setattr(structure_db, "cod_candidate_rows", lambda *a, **k: [])
    other = (["Ni"], [], 1.54, (10.0, 80.0))
    assert ps.cod_reference_patterns(*other, mode="sync") == []
    monkeypatch.setattr(structure_db, "cod_candidate_rows", good_rows)
    assert ps.cod_reference_patterns(*other, mode="sync") == []  # still within the interval
    monkeypatch.setattr(ps, "EMPTY_RETRY_S", 0.0)
    assert len(ps.cod_reference_patterns(*other, mode="sync")) == 2


def test_hint_mode_environment(monkeypatch):
    monkeypatch.delenv("DARA_TRACE_HINTS", raising=False)
    assert ps.hint_mode() == "off"  # pytest is loaded
    for value in ("sync", "background", "off", "SYNC"):
        monkeypatch.setenv("DARA_TRACE_HINTS", value)
        assert ps.hint_mode() == value.lower()
    monkeypatch.setenv("DARA_TRACE_HINTS", "bogus")
    assert ps.hint_mode() == "off"


# --- integration with the API payload --------------------------------------------------------------

def test_classified_peak_diagnostics_keeps_legacy_keys():
    from dara.server import api_router

    refinement = _refinement(injected=[(40.0, 400.0), (45.0, 150.0), (50.0, 20.0)])
    refinement.refinement_metrics = SimpleNamespace(
        missing_peaks=[[40, 5], [45, 3], [50, 1], [45.02, 3]], extra_peaks=[[20, 1]],
        intensity_mismatch_peaks=None,
    )
    document = SimpleNamespace(
        search_kwargs={"wavelength": "Cu", "refinement_params": {"excluded_ranges": [[70.0, 71.0]]}},
        xrd_data=SimpleNamespace(angles=refinement.plot_data.x,
                                 intensities=refinement.plot_data.y_obs, intensity_unit=None),
        results=[], input_cifs=[], final_result=refinement, elements=["Fe"], precursors=[],
    )
    legacy = api_router._peak_diagnostics(refinement)
    diag = api_router._classified_peak_diagnostics(document, refinement, hint_mode="off")
    for key in ("missing", "extra", "intensity_mismatch", "counts"):
        assert diag[key] == legacy[key]
    assert diag["counts"]["missing"] == 4  # the stored count still includes duplicates
    assert diag["counts_by_class"]["total"] == 3
    assert diag["counts_by_class"]["significant"] == 1 and diag["counts_by_class"]["minor"] == 1
    assert {"missing_classified", "missing_significance", "trace_hints"} <= set(diag)

    refinement.refinement_metrics = SimpleNamespace(
        missing_peaks=None, extra_peaks=None, intensity_mismatch_peaks=None
    )
    assert api_router._classified_peak_diagnostics(document, refinement) == \
        api_router._peak_diagnostics(refinement)
    bare = SimpleNamespace(plot_data=SimpleNamespace(x=refinement.plot_data.x))
    assert "counts_by_class" not in api_router._classified_peak_diagnostics(document, bare)


def _hint_document(refinement, cif):
    refinement.refinement_metrics = SimpleNamespace(
        missing_peaks=[[40, 5]], extra_peaks=None, intensity_mismatch_peaks=None
    )
    return SimpleNamespace(
        search_kwargs={"wavelength": "Cu"},
        xrd_data=SimpleNamespace(angles=refinement.plot_data.x,
                                 intensities=refinement.plot_data.y_obs, intensity_unit=None),
        results=[([[cif]], refinement)], input_cifs=[], final_result=refinement,
        elements=[], precursors=[],
    )


def test_candidate_references_are_simulated_by_the_background_worker(monkeypatch):
    import threading

    from dara.search import prefilter
    from dara.server import api_router

    threads = []
    gate = threading.Event()

    def fake_simulate(structure, wavelength_a, two_theta_range):
        if threading.current_thread().name == "dara-trace-hints":
            gate.wait(10)  # keep the worker busy until the first response was built
        threads.append(threading.current_thread().name)
        return np.array([40.0]), np.array([100.0]), np.array([3.0])

    from dara.cif import Cif

    cif = Cif({}, filename="Fe2O3_167_(cod_1)-0.cif")
    monkeypatch.setattr(Cif, "to_structure", lambda self: SimpleNamespace(volume=100.0))
    monkeypatch.setattr(prefilter, "simulate_structure_reflections", fake_simulate)
    api_router._REFERENCE_MEMO.clear()
    refinement = _refinement(injected=[(40.0, 400.0)])
    document = _hint_document(refinement, cif)
    context = api_router._peak_context(document, "background", job_key="job-d4")

    first = api_router._classified_peak_diagnostics(
        document, refinement, hint_mode="background", context=context
    )
    assert threads == []  # nothing was simulated on the request thread
    assert first["missing_classified"][0]["class"] == "significant"
    assert first["missing_classified"][0]["hint"] is None and first["trace_hints"] == []

    gate.set()
    assert ps.wait_idle(30)
    assert threads == ["dara-trace-hints"]
    later = api_router._classified_peak_diagnostics(
        document, refinement, hint_mode="background", context=context
    )
    assert later["trace_hints"][0]["formula"] == "Fe2O3"
    assert later["missing_classified"][0]["hint"]["formula"] == "Fe2O3"
    assert threads == ["dara-trace-hints"]  # memoized, not simulated again

    # sync mode (benchmark / offline) simulates on the calling thread
    api_router._REFERENCE_MEMO.clear()
    sync = api_router._classified_peak_diagnostics(document, refinement, hint_mode="sync")
    assert sync["trace_hints"][0]["formula"] == "Fe2O3"
    assert threads[-1] == threading.current_thread().name
    api_router._REFERENCE_MEMO.clear()
