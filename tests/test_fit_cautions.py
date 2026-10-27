import asyncio
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from dara.server import api_router
from dara.server import fit_cautions as fc
from dara.server.fit_cautions import fit_cautions


def _quality(rwp=10.0, gof=1.5, dw=1.8):
    return {"rwp": rwp, "gof": gof, "durbin_watson": dw}


def _codes(cautions):
    return [item["code"] for item in cautions]


def test_empty_and_none_inputs_are_safe():
    assert fit_cautions({}, {}, None) == []
    assert fit_cautions(
        {"gof": None, "rwp": None, "durbin_watson": None},
        {"A": {"weight_percent": None}},
        {"counts": {"missing": None}},
    ) == []


@pytest.mark.parametrize("gof,expected", [(0.9, True), (1.0, False), (1.2, False)])
def test_rexp_unreliable_gof_threshold(gof, expected):
    cautions = fit_cautions(_quality(gof=gof), {}, None)
    assert ("rexp_unreliable" in _codes(cautions)) is expected
    if expected:
        assert cautions[0]["level"] == "info"
        assert "GOF 0.90 < 1" in cautions[0]["message"]


def test_rexp_unreliable_for_non_counts_and_combined():
    assert "rexp_unreliable" not in _codes(fit_cautions(_quality(), {}, None, counts_like=True))
    assert "rexp_unreliable" not in _codes(fit_cautions(_quality(), {}, None, counts_like=None))
    only = fit_cautions(_quality(), {}, None, counts_like=False)
    assert "raw counts" in only[0]["message"]
    both = fit_cautions(_quality(gof=0.8), {}, None, counts_like=False)
    assert _codes(both).count("rexp_unreliable") == 1
    assert "GOF 0.80" in both[0]["message"] and "raw counts" in both[0]["message"]


@pytest.mark.parametrize(
    "rwp,gof,expected",
    [(25.0, 3.0, False), (25.1, 1.0, True), (10.0, 3.01, True), (10.0, 3.0, False)],
)
def test_poor_fit_thresholds(rwp, gof, expected):
    cautions = fit_cautions(_quality(rwp=rwp, gof=gof), {}, None)
    assert ("poor_fit" in _codes(cautions)) is expected
    if expected:
        item = next(c for c in cautions if c["code"] == "poor_fit")
        assert item["level"] == "warning"


def test_insignificant_phase_rules():
    details = {
        "TiO2_141_(cod_7206075)-0": {"weight_percent": 0.9, "weight_esd_percent": None},
        "Al2O3_167_(cod_1)-0": {"weight_percent": 3.0, "weight_esd_percent": 2.0},
        "CaF2_225_(cod_2)-0": {"weight_percent": 3.0, "weight_esd_percent": 1.5},
        "Fe_229_(cod_3)-0": {"weight_percent": 50.0, "weight_esd_percent": 0.2},
    }
    cautions = [c for c in fit_cautions(_quality(), details, None) if c["code"] == "insignificant_phase"]
    assert [c["phase"] for c in cautions] == ["TiO2_141_(cod_7206075)-0", "Al2O3_167_(cod_1)-0"]
    assert "0.90 wt%" in cautions[0]["message"] and "±" not in cautions[0]["message"]
    assert "(± 2.00)" in cautions[1]["message"]
    assert "<sub>" not in cautions[0]["message"]


def test_insignificant_phase_messages_are_split_by_reason():
    details = {
        "A_1_(cod_1)-0": {"weight_percent": 0.4, "weight_esd_percent": 0.3},  # wt < 1 and >= 2 esd? no: 0.4 < 0.6
        "B_1_(cod_2)-0": {"weight_percent": 0.8, "weight_esd_percent": 0.1},  # < 1 wt%, well above esd
        "C_1_(cod_3)-0": {"weight_percent": 5.0, "weight_esd_percent": 4.0},  # >= 1 wt%, below 2 esd
        "D_1_(cod_4)-0": {"weight_percent": 1.0, "weight_esd_percent": 0.2},  # no caution
    }
    cautions = {
        c["phase"]: c["message"]
        for c in fit_cautions(_quality(), details, None)
        if c["code"] == "insignificant_phase"
    }
    assert set(cautions) == {"A_1_(cod_1)-0", "B_1_(cod_2)-0", "C_1_(cod_3)-0"}
    assert "not clearly above its uncertainty" in cautions["A_1_(cod_1)-0"]
    assert "not clearly above its uncertainty" in cautions["C_1_(cod_3)-0"]
    low = cautions["B_1_(cod_2)-0"]
    assert "below 1 wt%" in low and "detection limit" in low
    assert "uncertainty" not in low


def test_correlated_residuals_and_missing_peaks():
    assert "correlated_residuals" in _codes(fit_cautions(_quality(dw=0.99), {}, None))
    assert "correlated_residuals" not in _codes(fit_cautions(_quality(dw=1.0), {}, None))
    one = fit_cautions(_quality(), {}, {"counts": {"missing": 1}})[0]
    assert (one["code"], one["level"]) == ("missing_peaks_remain", "info")
    three = fit_cautions(_quality(), {}, {"counts": {"missing": 3}})[0]
    assert three["level"] == "warning" and "3 observed peak(s)" in three["message"]
    assert fit_cautions(_quality(), {}, {"counts": {"missing": 0}}) == []


def test_lattice_flags_group_per_phase_and_order_is_stable():
    flags = [
        {"phase": "A_225_(cod_1)-0", "parameter": "a"},
        {"phase": "A_225_(cod_1)-0", "parameter": "c"},
    ]
    cautions = fit_cautions(
        _quality(rwp=30.0, dw=0.5), {}, {"counts": {"missing": 2}}, lattice_flags=flags
    )
    assert _codes(cautions) == [
        "poor_fit", "lattice_at_bound", "correlated_residuals", "missing_peaks_remain",
    ]
    assert "a, c" in cautions[1]["message"]


def test_lattice_flag_cache_is_bounded_and_memoizes():
    fc.clear_lattice_flag_cache()
    calls = []

    def compute():
        calls.append(1)
        return [{"phase": "p", "parameter": "a"}]

    assert fc.cached_lattice_flags("u1", compute) == fc.cached_lattice_flags("u1", compute)
    assert len(calls) == 1
    for index in range(60):
        fc.cached_lattice_flags(f"k{index}", lambda: [])
    assert len(fc._LATTICE_FLAG_CACHE) <= 32
    assert fc.cached_lattice_flags("bad", lambda: 1 / 0) == []
    fc.clear_lattice_flag_cache()


def test_lattice_bound_flags_never_raises():
    refinement = SimpleNamespace(lst_data=SimpleNamespace(phases_results={"X": SimpleNamespace(a=0.4)}))
    assert fc.lattice_bound_flags(refinement, [], 0.02) == []
    assert fc.lattice_bound_flags(refinement, [SimpleNamespace(filename="X", orig_string="junk")], 0.02) == []
    assert fc.lattice_bound_flags(refinement, None, "fixed") == []
    assert fc.lattice_bound_flags(None, None, 0.02) == []


def test_lattice_bound_flags_detects_values_near_bound(monkeypatch):
    def fake_cif2str(path, working_dir=None, lattice_range=None, **kwargs):
        out = working_dir / "X.str"
        out.write_text(
            "PARAM=A=0.400000_0.392000^0.408000 //\nPARAM=C=0.600000_0.588000^0.612000 //\n"
        )
        return out

    monkeypatch.setattr("dara.cif2str.cif2str", fake_cif2str)
    phase = SimpleNamespace(a=(0.4078, 0.0001), c=0.6005, unit="NM")
    refinement = SimpleNamespace(lst_data=SimpleNamespace(phases_results={"X": phase}))
    cif = SimpleNamespace(filename="X", orig_string="data_x")
    assert fc.lattice_bound_flags(refinement, [cif], 0.02) == [{"phase": "X", "parameter": "a"}]


# ---------------------------------------------------------------------------
# result() payload
# ---------------------------------------------------------------------------

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


class _Store:
    def __init__(self, records):
        self.records = records
        self.updated = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        for record in self.records:
            if all(record.get(key) == value for key, value in criteria.items()):
                return dict(record)
        return None

    def update(self, value):
        self.updated.append(value)


def test_result_payload_contains_cautions_and_backfills_summary(monkeypatch):
    task = {
        "index": 4, "uuid": "u-4", "status": "COMPLETED", "job": {"name": "x.xy"},
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
    worker, results = _Store([task]), _Store([{"uuid": "u-4", "output": document}])
    monkeypatch.setattr(api_router, "get_worker_store", lambda: worker)
    monkeypatch.setattr(api_router, "get_result_store", lambda: results)
    monkeypatch.setattr(api_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda value: value))
    fc.clear_lattice_flag_cache()

    payload = asyncio.run(api_router.result(4))
    final_codes = _codes(payload["final_result"]["cautions"])
    assert "rexp_unreliable" in final_codes  # intensity_unit == "cps"
    candidate = payload["all_results"][0]
    assert isinstance(candidate["cautions"], list)
    assert all({"code", "level", "message", "phase"} <= set(item) for item in candidate["cautions"])
    # Summary was persisted for old records without one.
    assert worker.updated and worker.updated[0]["summary"]["n_candidates"] == 1
    assert worker.updated[0]["summary"]["final_phases"] == ["CaF2_225_(cod_2300449)-0"]


# ---------------------------------------------------------------------------
# per-phase lattice ranges
# ---------------------------------------------------------------------------

def _fake_cif2str_factory(monkeypatch, start=0.4):
    seen = []

    def fake_cif2str(path, working_dir=None, lattice_range=None, **kwargs):
        seen.append(lattice_range)
        low, high = start * (1 - lattice_range), start * (1 + lattice_range)
        out = working_dir / "X.str"
        out.write_text(f"PARAM=A={start:.6f}_{low:.6f}^{high:.6f} //\n")
        return out

    monkeypatch.setattr("dara.cif2str.cif2str", fake_cif2str)
    return seen


def _lattice_refinement(a):
    phase = SimpleNamespace(a=(a, 0.0001), unit="NM")
    return SimpleNamespace(lst_data=SimpleNamespace(phases_results={"X": phase}))


def test_lattice_range_resolver_precedence():
    user = SimpleNamespace(filename="X", name="X", orig_string="")
    other = SimpleNamespace(filename="Y", name="Y", orig_string="")
    resolve = fc.lattice_range_resolver(0.02, [user], {"lattice_range": 0.05}, None)
    assert resolve(user) == 0.05 and resolve(other) == 0.02
    resolve = fc.lattice_range_resolver(0.02, [user], {"lattice_range": 0.05}, {"lattice_range": 0.03})
    assert resolve(user) == 0.03 and resolve(other) == 0.03
    resolve = fc.lattice_range_resolver(0.02, [user], {"k1": "0_0^1"}, None)
    assert resolve(user) == 0.02
    assert fc.lattice_range_resolver(0.02, None, None, None)(user) == 0.02


def test_lattice_flags_use_each_phases_effective_range(monkeypatch):
    seen = _fake_cif2str_factory(monkeypatch)
    cif = SimpleNamespace(filename="X", name="X", orig_string="data_x")
    # 3% move: inside a 5% user range, but would be at the bound of a 2% range.
    moved_3pct = _lattice_refinement(0.4 * 1.03)
    assert fc.lattice_bound_flags(moved_3pct, [cif], 0.02) == [{"phase": "X", "parameter": "a"}]
    resolve = fc.lattice_range_resolver(0.02, [cif], {"lattice_range": 0.05}, None)
    assert fc.lattice_bound_flags(moved_3pct, [cif], resolve) == []
    assert seen[-1] == 0.05
    # 1.9% move with the default 2% range is flagged.
    assert fc.lattice_bound_flags(_lattice_refinement(0.4 * 1.019), [cif], 0.02) == [
        {"phase": "X", "parameter": "a"}
    ]
    # A fixed lattice is never flagged.
    fixed = fc.lattice_range_resolver("fixed", None, None, None)
    assert fc.lattice_bound_flags(moved_3pct, [cif], fixed) == []


def test_final_lattice_flags_honour_additional_cif_params(monkeypatch):
    _fake_cif2str_factory(monkeypatch)
    monkeypatch.setattr(api_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda value: value))
    cif = SimpleNamespace(filename="X", name="X", orig_string="data_x")

    def flags(a, kwargs, uuid):
        document = SimpleNamespace(
            final_result=_lattice_refinement(a), input_cifs=[],
            final_refinement_params={"lattice_range": 0.02},
        )
        job = {"uuid": uuid, "job": {"function_kwargs": kwargs}}
        return api_router._final_lattice_flags(job, document)

    fc.clear_lattice_flag_cache()
    user_kwargs = {"additional_cifs": [cif], "additional_cif_params": {"lattice_range": 0.05}}
    assert flags(0.4 * 1.03, user_kwargs, "u-a") == []
    assert flags(0.4 * 1.049, user_kwargs, "u-b") == [{"phase": "X", "parameter": "a"}]
    # Without a per-phase override the global 2% range applies.
    assert flags(0.4 * 1.019, {"additional_cifs": [cif]}, "u-c") == [{"phase": "X", "parameter": "a"}]
    fc.clear_lattice_flag_cache()


# --- classified unexplained peaks ----------------------------------------------------------

def _classified(significant=0, minor=0, instrument=0, noise=0, rel=0.05, hints=None, anode="cu"):
    entries = (
        [{"class": "significant", "rel_height": rel}] * significant
        + [{"class": "minor", "rel_height": rel / 2}] * minor
        + [{"class": "instrument", "rel_height": 0.008}] * instrument
        + [{"class": "noise", "rel_height": 0.001}] * noise
    )
    return {
        "counts": {"missing": len(entries), "extra": 0, "intensity_mismatch": 0},
        "counts_by_class": {"significant": significant, "minor": minor, "instrument": instrument,
                            "noise": noise, "total": len(entries)},
        "missing_classified": entries, "trace_hints": hints or [],
        "missing_significance": {"anode": anode},
    }


def _caution_map(diagnostics):
    return {c["code"]: c for c in fit_cautions(_quality(), {}, diagnostics)}


def test_classified_peaks_produce_class_specific_cautions():
    result = _caution_map(_classified(significant=2, minor=3, instrument=1, noise=4, rel=0.062))
    assert result["missing_peaks_remain"]["level"] == "info"
    assert result["missing_peaks_remain"]["message"] == (
        "2 significant unexplained peak(s), up to 6.2% of the strongest peak, are not explained "
        "by the fitted phases."
    )
    assert result["minor_unexplained_peaks"]["level"] == "info"
    assert result["minor_unexplained_peaks"]["message"] == (
        "3 minor unexplained peak(s), up to 3.1% of the strongest peak, remain; they do not "
        "affect the overall assessment."
    )
    assert result["instrument_lines"]["level"] == "info"
    assert result["instrument_lines"]["message"] == (
        "1 weak peak(s) match Cu Kβ or W Lα1 lines of strong reflections and are not counted "
        "as unexplained."
    )
    assert _caution_map(_classified(significant=3))["missing_peaks_remain"]["level"] == "warning"


def test_classified_noise_only_adds_no_caution_and_hint_is_named():
    assert fit_cautions(_quality(), {}, _classified(noise=5)) == []
    hinted = _caution_map(_classified(minor=1, hints=[
        {"formula": "Fe2O3", "spacegroup_symbol": "R-3c"}]))
    assert hinted["minor_unexplained_peaks"]["message"].endswith(
        " Positions are consistent with trace Fe2O3 (R-3c).")
    assert "missing_peaks_remain" not in hinted
    unknown_anode = _caution_map(_classified(instrument=2, anode=None))
    assert "match Kβ or W Lα1 lines" in unknown_anode["instrument_lines"]["message"]
    named = _classified(instrument=2)
    named["missing_classified"] = [
        {"class": "instrument", "rel_height": 0.008, "attribution": {"type": "kbeta"}},
        {"class": "instrument", "rel_height": 0.008, "attribution": {"type": "kbeta"}},
    ]
    assert "match Cu Kβ lines" in _caution_map(named)["instrument_lines"]["message"]
    named["missing_classified"][1]["attribution"] = {"type": "w_lalpha"}
    assert "match Cu Kβ or W Lα1 lines" in _caution_map(named)["instrument_lines"]["message"]
    named["missing_classified"] = [
        {"class": "instrument", "rel_height": 0.008, "attribution": {"type": "w_lalpha"}}
    ] * 2
    assert "match W Lα1 lines" in _caution_map(named)["instrument_lines"]["message"]


def test_legacy_diagnostics_keep_the_count_based_caution():
    legacy = _caution_map({"counts": {"missing": 4}})
    assert legacy["missing_peaks_remain"]["level"] == "warning"
    assert legacy["missing_peaks_remain"]["message"] == "4 observed peak(s) are not explained by the fitted phases."
    assert _caution_map({"counts": {"missing": 1}, "counts_by_class": "bad"})["missing_peaks_remain"]["level"] == "info"
