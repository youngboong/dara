import math
from types import SimpleNamespace

import numpy as np
import pytest

from dara.server import confidence as cf
from dara.server.confidence import CandidateEvidence
from dara.server.fit_cautions import broad_background_caution, displacement_caution


# --- helpers -----------------------------------------------------------------

def test_instrument_radius_from_bundled_profiles():
    assert cf.instrument_radius_mm("Aeris-fds-Pixcel1d-Medipix3") == 145.0
    assert cf.instrument_radius_mm("LBL-d8-LynxEyeXE") == 280.0  # upper-case .SAV
    assert cf.instrument_radius_mm("user-0a1b2c3d-0000-4000-8000-000000000000") is None
    assert cf.instrument_radius_mm("nope") is None
    assert cf.instrument_radius_mm(None) is None
    assert cf.instrument_radius_mm("../Devices/Aeris-fds-Pixcel1d-Medipix3") is None


def test_raw_lst_counts_parses_docstring_example():
    raw = (
        "Rietveld refinement to file(s) Mg3MnNi3O8.xy\n"
        "BGMN version 4.2.23, 8301 measured points, 78 peaks, 20 parameters\n"
        "Start: Mon Dec 18 11:43:20 2023; End: Mon Dec 18 11:43:21 2023\n"
    )
    assert cf.raw_lst_counts(raw) == {
        "bgmn_version": "4.2.23", "points": 8301, "peaks": 78, "parameters": 20,
    }
    assert cf.raw_lst_counts("nothing") == {
        "bgmn_version": None, "points": None, "peaks": None, "parameters": None,
    }
    assert cf.raw_lst_counts(None)["points"] is None


def test_phase_identity():
    assert cf.phase_identity("Fe2O3_167_(mp_mp-19770)-0") == ("Fe2O3", 167)
    assert cf.phase_identity("Fe4O6_167_(cod_1)-0")[0] == "Fe2O3"  # reduced formula
    from pymatgen.core import Composition

    # the database space group of the name wins over the refined BGMN setting ...
    assert cf.phase_identity("BiFeO3_161", {"spacegroup_number": 160}) == (
        Composition("BiFeO3").reduced_formula, 161)
    assert cf.phase_identity("Fe2O3_167_(cod_2108027)-0", {"spacegroup_number": 15}) == (
        "Fe2O3", 167)
    # ... which is only a fallback for names without a space-group token
    assert cf.phase_identity("BiFeO3", {"spacegroup_number": 160}) == (
        Composition("BiFeO3").reduced_formula, 160)
    assert cf.phase_identity("not a formula!!") == (None, None)


def test_hamilton_test():
    significant = cf.hamilton_test(10.0, 11.0, 4000, 20, 10)
    assert significant["significant"] is True and significant["b"] == 10 and significant["dof"] == 3980
    assert cf.hamilton_test(10.0, 10.005, 4000, 20, 10)["significant"] is False
    assert cf.hamilton_test(None, 11.0, 4000, 20, 10) is None
    assert cf.hamilton_test(10.0, 11.0, 4000, 10, 20) is None  # b < 1
    assert cf.hamilton_test(10.0, 11.0, 15, 20, 10) is None  # dof < 1
    expected = math.sqrt(10 / 3980 * 2.3 + 1)
    assert significant["critical"] == pytest.approx(expected, rel=0.05)


# --- sample displacement ---------------------------------------------------------

AERIS = "Aeris-fds-Pixcel1d-Medipix3"


def _lst(eps2):
    return SimpleNamespace(EPS2=eps2)


def test_displacement_levels_for_known_radius():
    ok = cf.sample_displacement(_lst((-0.001657, 3.3e-05)), AERIS)
    assert ok["displacement_mm"] == pytest.approx(-0.240, abs=0.001)
    assert ok["displacement_mm_esd"] == pytest.approx(0.0048, abs=0.0001)
    assert ok["two_theta_shift_deg_at_30"] == pytest.approx(0.183, abs=0.002)
    assert ok["level"] == "ok" and not ok["at_bound"]
    assert ok["message"] == "Refined specimen displacement: -0.24 mm (EPS2 = -0.00166)."
    assert cf.sample_displacement(_lst(0.0055), AERIS)["level"] == "info"
    assert cf.sample_displacement(_lst(0.0055), AERIS)["displacement_mm"] == pytest.approx(0.7975)
    warning = cf.sample_displacement(_lst(0.0100), AERIS)
    assert warning["level"] == "warning" and "specimen height" in warning["message"]
    assert cf.sample_displacement(_lst(0.0150), AERIS)["level"] == "severe"
    bound = cf.sample_displacement(_lst((0.046, 0.001)), AERIS)
    assert bound["level"] == "severe" and bound["at_bound"] is True
    assert "refinement limit" in bound["message"]
    assert "convention" in bound and bound["convention"].startswith("s = EPS2 x R")


def test_displacement_without_radius_or_eps2():
    user = cf.sample_displacement(_lst(0.0060), "user-abc")
    assert user["radius_mm"] is None and user["displacement_mm"] is None
    assert user["level"] == "warning"  # 0.0060 -> about 0.66 deg at 30 deg
    assert cf.sample_displacement(_lst(0.0100), "user-abc")["level"] == "severe"
    assert "goniometer radius" in user["message"]
    assert cf.sample_displacement(_lst(0.0005), "user-abc")["level"] == "ok"
    missing = cf.sample_displacement(SimpleNamespace(), AERIS)
    assert missing["level"] == "unknown" and missing["eps2"] is None
    assert cf.sample_displacement(None, AERIS)["level"] == "unknown"
    # a fixed (plain float) EPS2 is accepted and has no esd
    fixed = cf.sample_displacement(_lst(-0.001), AERIS)
    assert fixed["eps2_esd"] is None and fixed["displacement_mm_esd"] is None
    # pydantic-style extras
    extra = cf.sample_displacement(SimpleNamespace(model_extra={"EPS2": (0.001, 0.0001)}), AERIS)
    assert extra["eps2"] == 0.001


def test_displacement_cautions():
    assert displacement_caution(cf.sample_displacement(_lst(0.0055), AERIS)) is None
    caution = displacement_caution(cf.sample_displacement(_lst(0.0100), AERIS))
    assert caution["code"] == "sample_displacement" and caution["level"] == "warning"
    assert displacement_caution(None) is None
    assert broad_background_caution({"detected": False}) is None
    info = broad_background_caution({"detected": True, "width_deg": 12.3, "relative_height": 0.3})
    assert info["code"] == "broad_background" and info["level"] == "info" and "12°" in info["message"]


# --- broad background ----------------------------------------------------------

def test_broad_background_detection():
    x = np.linspace(10, 80, 1400)
    peaks = 1000 * np.exp(-0.5 * ((x - 40) / 0.1) ** 2)
    flat = cf.broad_background(x, peaks + 20, np.full_like(x, 20.0))
    assert flat["detected"] is False and flat["width_deg"] is None
    hump = 300 * np.exp(-0.5 * ((x - 40) / 5) ** 2)
    found = cf.broad_background(x, peaks + 20 + hump, 20 + hump)
    assert found["detected"] is True and found["width_deg"] > 8
    reference = float(np.percentile(peaks, 99.5))
    assert found["relative_height"] == pytest.approx(300 / reference, rel=0.05)
    assert cf.broad_background([], [], []) ["detected"] is False
    narrow = 300 * np.exp(-0.5 * ((x - 40) / 1.0) ** 2)
    assert cf.broad_background(x, peaks + 20 + narrow, 20 + narrow)["detected"] is False


# --- phase / overall levels ------------------------------------------------------

def _evidence(key, phases, rwp, *, number=None, source_index=None, gof=1.5, refinement=None,
              flags=None, reference=None):
    """phases: {name: (weight, esd)}"""
    return CandidateEvidence(
        key=key, number=number, source_index=source_index, rwp=rwp, gof=gof,
        identities={name: cf.phase_identity(name) for name in phases},
        phase_details={
            name: {"weight_percent": w, "weight_esd_percent": e, "lattice_parameters": {}}
            for name, (w, e) in phases.items()
        },
        refinement=refinement, lattice_flags=flags or [], reference_lattices=reference or {},
    )


A = "Fe2O3_167_(cod_1)-0"
B = "Al2O3_167_(cod_2)-0"
C = "TiO2_141_(cod_3)-0"
D = "ZnO_186_(cod_4)-0"


def _refinement(*, eps2=-0.001, raw=None, hump=False):
    x = np.linspace(10, 80, 300)
    bkg = np.full_like(x, 10.0)
    obs = bkg + 50 * np.exp(-0.5 * ((x - 40) / 0.2) ** 2)
    return SimpleNamespace(
        lst_data=SimpleNamespace(EPS2=eps2, raw_lst=raw),
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs, y_bkg=bkg),
    )


def _evaluate(target, displayed, **kwargs):
    defaults = dict(counts_like=True, missing_peaks=0, search_summary=None, instrument_profile=AERIS)
    defaults.update(kwargs)
    target.refinement = target.refinement or _refinement()
    return cf.evaluate_confidence(target, displayed, **defaults)


def test_single_candidate_ignores_presence_and_is_high():
    target = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.0)
    only = _evidence("candidate:0", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.0, number=1, source_index=0)
    block = _evaluate(target, [only])
    assert block["schema"] == 1
    phase = block["phases"][A]
    assert phase["level"] == "confident" and phase["label"] == "Confident"
    assert phase["evidence"]["presence_fraction"] is None  # a single displayed model
    block = _evaluate(target, [])
    assert block["phases"][A]["evidence"]["presence_fraction"] is None
    assert block["phases"][A]["level"] == "confident"
    assert block["overall"]["level"] == "high" and block["overall"]["label"] == "High confidence"
    assert block["overall"]["reasons"] == ["All 2 phases are supported by every criterion."]
    assert "Weight fraction 60.0 ± 0.5 wt%" in phase["reasons"][0]
    assert set(block) == {"schema", "overall", "phases", "sample_displacement", "broad_background"}


def test_presence_fraction_and_ambiguity():
    target = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.0)
    displayed = [
        _evidence("candidate:0", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.0, number=1, source_index=0),
        _evidence("candidate:1", {A: (50.0, 1.0), C: (50.0, 1.0)}, 12.0, number=2, source_index=1),
        _evidence("candidate:2", {B: (50.0, 1.0), C: (50.0, 1.0)}, 12.5, number=3, source_index=2),
        _evidence("candidate:3", {C: (50.0, 1.0), D: (50.0, 1.0)}, 13.0, number=4, source_index=3),
        _evidence("candidate:4", {D: (50.0, 1.0), C: (50.0, 1.0)}, 13.5, number=5, source_index=4),
    ]
    block = _evaluate(target, displayed)
    evidence = block["phases"][A]["evidence"]
    assert evidence["presence_fraction"] == pytest.approx(0.4) and evidence["n_candidates"] == 5
    assert block["phases"][A]["level"] == "ambiguous"
    assert block["phases"][A]["reasons"][0] == "Present in 2 of 5 candidate models."
    assert block["overall"]["level"] == "check"
    assert block["overall"]["reasons"][0] == f"Ambiguous evidence for {cf._label(A)}."


def test_alternative_within_rwp_tolerance_makes_phase_ambiguous():
    target = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.6)
    near = _evidence("candidate:1", {A: (50.0, 1.0), C: (50.0, 1.0)}, 7.9, number=2, source_index=1)
    same = _evidence("candidate:0", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.6, number=1, source_index=0)
    block = _evaluate(target, [same, near])
    phase_b = block["phases"][B]
    assert phase_b["level"] == "ambiguous"
    alternative = phase_b["evidence"]["alternative"]
    assert alternative["candidate_number"] == 2 and alternative["delta_rwp"] == pytest.approx(0.3)
    assert alternative["replaced_by"] == [cf._label(C)]
    assert "Candidate 2 without this phase fits almost as well (Rwp 7.9% vs 7.6%)." in phase_b["reasons"]
    # A is also in the near candidate -> no alternative for A
    assert block["phases"][A]["evidence"]["alternative"] is None
    # Beyond max(0.5, 5%) -> no longer an alternative
    far = _evidence("candidate:1", {A: (50.0, 1.0), C: (50.0, 1.0)}, 8.5, number=2, source_index=1)
    assert _evaluate(target, [same, far])["phases"][B]["evidence"]["alternative"] is None


def test_weak_phase_rules_and_expert_overall():
    target = _evidence("final", {A: (92.0, 0.5), B: (8.0, 5.0)}, 7.0)  # sigma 1.6 < 2, wt >= 5
    block = _evaluate(target, [])
    weak = block["phases"][B]
    assert weak["level"] == "weak" and weak["label"] == "Weak evidence"
    assert "Weight fraction is 1.6 times its estimated standard deviation." in weak["reasons"]
    assert block["overall"]["level"] == "expert"
    assert block["overall"]["reasons"][0] == f"Weak evidence for {cf._label(B)} (8.0 wt%)."

    small = _evidence("final", {A: (99.2, 0.3), B: (0.8, 0.1)}, 7.0)
    block = _evaluate(small, [])
    assert block["phases"][B]["level"] == "weak"
    assert "Weight fraction 0.8 wt% is below 1 wt%." in block["phases"][B]["reasons"]
    assert block["overall"]["level"] == "check"  # weak but below 5 wt%

    bound = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 7.0,
                      flags=[{"phase": A, "parameter": "a"}])
    block = _evaluate(bound, [])
    assert block["phases"][A]["level"] == "weak"
    assert block["phases"][A]["evidence"]["lattice_at_bound"] is True
    assert block["phases"][A]["reasons"][0] == "A lattice parameter ended at its refinement limit."


def test_overall_expert_criteria():
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    assert _evaluate(_evidence("final", base, 27.3), [])["overall"]["level"] == "expert"
    assert "Rwp is 27.3%." in _evaluate(_evidence("final", base, 27.3), [])["overall"]["reasons"]
    assert _evaluate(_evidence("final", base, 7.0), [], missing_peaks=5)["overall"]["level"] == "expert"
    assert "5 observed peaks are not explained." in _evaluate(
        _evidence("final", base, 7.0), [], missing_peaks=5)["overall"]["reasons"]
    assert _evaluate(_evidence("final", base, 7.0, gof=4.5), [])["overall"]["level"] == "expert"
    ignored = _evaluate(_evidence("final", base, 7.0, gof=4.5), [], counts_like=False)
    assert ignored["overall"]["level"] == "high"
    severe = _evidence("final", base, 7.0)
    severe.refinement = _refinement(eps2=0.0150)
    assert _evaluate(severe, [])["overall"]["level"] == "expert"
    failed = _evaluate(_evidence("final", base, 7.0), [],
                       search_summary={"final_refinement_attempted": 4, "final_refinement_failed": 2})
    assert failed["overall"]["level"] == "expert"
    few = _evaluate(_evidence("final", base, 7.0), [],
                    search_summary={"final_refinement_attempted": 4, "final_refinement_failed": 1})
    assert few["overall"]["level"] == "high"


def test_overall_high_and_check_boundaries():
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    assert _evaluate(_evidence("final", base, 7.0, gof=3.0), [])["overall"]["level"] == "high"
    checked = _evaluate(_evidence("final", base, 7.0, gof=3.5), [])
    assert checked["overall"]["level"] == "check" and "GOF is 3.50." in checked["overall"]["reasons"]
    assert _evaluate(_evidence("final", base, 7.0, gof=3.5), [], counts_like=False)["overall"]["level"] == "high"
    assert _evaluate(_evidence("final", base, 7.0), [], missing_peaks=1)["overall"]["level"] == "high"
    assert _evaluate(_evidence("final", base, 7.0), [], missing_peaks=2)["overall"]["level"] == "check"
    warn = _evidence("final", base, 7.0)
    warn.refinement = _refinement(eps2=0.0100)
    assert _evaluate(warn, [])["overall"]["level"] == "check"
    info = _evidence("final", base, 7.0)
    info.refinement = _refinement(eps2=0.0055)
    assert _evaluate(info, [])["overall"]["level"] == "high"
    unknown = _evidence("final", base, 7.0)
    unknown.refinement = SimpleNamespace(
        lst_data=SimpleNamespace(), plot_data=_refinement().plot_data
    )
    assert _evaluate(unknown, [])["overall"]["level"] == "high"
    assert _evaluate(_evidence("final", {}, 7.0), [])["overall"]["level"] == "check"


def test_broad_background_prevents_high():
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    target = _evidence("final", base, 7.0)
    x = np.linspace(10, 80, 600)
    hump = 40 * np.exp(-0.5 * ((x - 40) / 5) ** 2)
    obs = 10 + hump + 100 * np.exp(-0.5 * ((x - 40) / 0.1) ** 2)
    target.refinement = SimpleNamespace(
        lst_data=SimpleNamespace(EPS2=-0.001),
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs, y_bkg=10 + hump),
    )
    block = _evaluate(target, [])
    assert block["broad_background"]["detected"] is True
    assert block["overall"]["level"] == "check"
    assert "Broad background hump: weight fractions refer to crystalline phases only." in block["overall"]["reasons"]


def test_hamilton_marks_unsupported_phase():
    def refinement(params):
        raw = f"x\nBGMN version 4.2.23, 4000 measured points, 50 peaks, {params} parameters\n"
        return _refinement(raw=raw)

    target = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 10.0, refinement=refinement(20))
    without = _evidence("candidate:1", {A: (100.0, 0.5)}, 10.005, number=1, source_index=1,
                        refinement=refinement(10))
    block = _evaluate(target, [without])
    phase_b = block["phases"][B]
    assert phase_b["evidence"]["hamilton"]["significant"] is False
    assert phase_b["evidence"]["alternative"] is not None
    assert phase_b["level"] == "ambiguous"
    assert any("Hamilton test" in reason for reason in phase_b["reasons"])

    clear = _evidence("candidate:1", {A: (100.0, 0.5)}, 11.5, number=1, source_index=1,
                      refinement=refinement(10))
    block = _evaluate(target, [clear])
    assert block["phases"][B]["evidence"]["hamilton"]["significant"] is True
    assert block["phases"][B]["level"] == "confident"
    # no raw_lst -> no Hamilton result
    plain = _evidence("final", {A: (60.0, 0.5), B: (40.0, 0.5)}, 10.0)
    assert _evaluate(plain, [clear])["phases"][B]["evidence"]["hamilton"] is None


def test_computed_structure_lattice_change():
    mp_phase = "Fe2O3_167_(mp_mp-19770)-0"
    target = _evidence("final", {mp_phase: (100.0, 0.5)}, 7.0, reference={mp_phase: {"a": 5.0, "b": 5.0, "c": 13.0}})
    target.phase_details[mp_phase]["lattice_parameters"] = {
        "a": {"value": 5.105, "esd": 0.001, "unit": "Å"},
        "c": {"value": 13.0, "esd": 0.001, "unit": "Å"},
    }
    block = _evaluate(target, [])
    evidence = block["phases"][mp_phase]["evidence"]
    assert evidence["computed_structure"] is True
    assert evidence["lattice_change_pct"] == pytest.approx(2.1, abs=0.05)
    assert block["phases"][mp_phase]["level"] == "ambiguous"
    assert any("changed by 2.1%" in reason for reason in block["phases"][mp_phase]["reasons"])
    target.phase_details[mp_phase]["lattice_parameters"]["a"]["value"] = 5.02
    assert _evaluate(target, [])["phases"][mp_phase]["level"] == "confident"
    cod = _evidence("final", {A: (100.0, 0.5)}, 7.0)
    assert _evaluate(cod, [])["phases"][A]["evidence"]["computed_structure"] is False


def test_evaluate_never_raises():
    broken = CandidateEvidence("final", None, None, None, None, {A: ("Fe2O3", 167)}, {A: None})
    block = cf.evaluate_confidence(
        broken, [object()], counts_like=None, missing_peaks=None, search_summary=None,
        instrument_profile=None,
    )
    assert block is None or block["overall"]["level"] in {"high", "check", "expert"}
    assert cf.confidence_for_document(SimpleNamespace(final_result=None)) is None
    assert cf.confidence_for_document(object()) is None


# --- integration -----------------------------------------------------------------

def test_summarize_document_attaches_confidence():
    from datetime import datetime

    from dara.server import api_router
    from dara.server import fit_cautions as fc

    fc.clear_lattice_flag_cache()
    name = "CaF2_225_(cod_2300449)-0"

    def refinement(rwp):
        phase = SimpleNamespace(
            spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=(0.5, 0.4),
            a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
            xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
        )
        return SimpleNamespace(
            lst_data=SimpleNamespace(
                rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
                phases_results={name: phase},
            ),
            plot_data=SimpleNamespace(x=np.array([20.0, 21.0, 22.0, 30.0])),
            get_phase_weights=lambda: {name: 1.0},
        )

    job = {
        "index": 4, "uuid": "u-conf", "status": "COMPLETED",
        "job": {"name": "x.xy", "function_kwargs": {}, "metadata": {}},
        "submitted_time": datetime(2026, 1, 1, 0, 0), "start_time": datetime(2026, 1, 1, 0, 1),
        "end_time": datetime(2026, 1, 1, 0, 3),
    }
    document = SimpleNamespace(
        results=[([[SimpleNamespace(filename=name)]], refinement(5.0))],
        grouped_phases=None, missing_peaks=None, extra_peaks=None, screening_rwps=None,
        final_result=refinement(4.0), best_rwp=4.0, precursors=[], phase_predictor=None,
        predict_kwargs=None, search_kwargs={}, final_refinement_params=None, input_cifs=[],
        xrd_data=SimpleNamespace(intensities=np.array([1.5, 2.5, 3.5]), intensity_unit="cps"),
    )
    payload = api_router.summarize_document(job, document, persist_summary=False)
    block = payload["final_result"]["confidence"]
    assert block["overall"]["level"] in {"high", "check", "expert"}
    assert list(block["phases"]) == ["CaF2_225_(cod_2300449)-0"]
    assert block["sample_displacement"]["level"] == "unknown"  # synthetic lst has no EPS2
    for entry in payload["all_results"]:
        assert entry["confidence"]["schema"] == 1
    assert cf.confidence_for_document(document)["schema"] == 1
    fc.clear_lattice_flag_cache()


def test_fill_weight_esd_uses_bgmn_q_goals_without_mutating_input():
    name = "CaF2_225_(cod_2300449)-0"
    refinement = SimpleNamespace(
        lst_data=SimpleNamespace(QCaF2225cod23004490=(0.6031, 0.0024), model_extra=None)
    )
    details = {name: {"weight_percent": 60.31, "weight_esd_percent": None}}
    filled = cf.fill_weight_esd(refinement, details)
    assert filled[name]["weight_esd_percent"] == pytest.approx(0.24)
    assert details[name]["weight_esd_percent"] is None
    keep = {name: {"weight_percent": 60.31, "weight_esd_percent": 0.5}}
    assert cf.fill_weight_esd(refinement, keep)[name]["weight_esd_percent"] == 0.5
    assert cf.fill_weight_esd(SimpleNamespace(), details)[name]["weight_esd_percent"] is None


# --- formula-level identity (task 22: Y2O3 + Fe2O3 + YFeO3) ------------------------------

Y_IA3, Y_I213 = "Y2O3_206_(cod_1513300)-0", "Y2O3_199_(cod_1010587)-None"
FE_167, FE_167B, FE_166 = (
    "Fe2O3_167_(cod_2108027)-0", "Fe2O3_167_(cod_5910082)-0", "Fe2O3_166_(cod_1011240)-None",
)
YFO = "YFeO3_62_(cod_2101386)-None"


def _task22_candidates():
    # hematite is refined as C12/c1 (number 15) in candidates 1 and 2
    rows = [
        (3.01, Y_IA3, FE_167), (3.12, Y_I213, FE_167), (3.24, Y_IA3, FE_167B),
        (3.30, Y_IA3, FE_166), (3.41, Y_I213, FE_166),
    ]
    out = []
    for number, (rwp, yttria, hematite) in enumerate(rows, start=1):
        names = [yttria, hematite, YFO]
        candidate = _evidence(
            f"candidate:{number - 1}", dict.fromkeys(names, (33.0, 0.7)), rwp,
            number=number, source_index=number - 1,
        )
        metadata = {hematite: {"spacegroup_number": 15}} if hematite == FE_167 else {}
        candidate.identities = {name: cf.phase_identity(name, metadata.get(name)) for name in names}
        out.append(candidate)
    return out


def test_setting_and_subgroup_variants_do_not_make_a_phase_ambiguous():
    displayed = _task22_candidates()
    block = _evaluate(displayed[0], displayed)
    for name in (Y_IA3, FE_167):
        phase = block["phases"][name]
        assert phase["level"] == "confident", phase["reasons"]
        assert phase["evidence"]["presence_fraction"] == pytest.approx(1.0)
        assert phase["evidence"]["alternative"] is None
        assert not any("without this phase" in reason for reason in phase["reasons"])
    # the space-group difference is reported as a structure-model ambiguity, not a missing phase
    model = block["phases"][Y_IA3]["evidence"]["structure_model_alternative"]
    assert model["spacegroup_number"] == 199 and model["own_spacegroup_number"] == 206
    assert model["polymorph"] is False and model["candidate_number"] == 2
    assert any("different structure model" in r and "present in both" in r
               for r in block["phases"][Y_IA3]["reasons"])
    hematite = block["phases"][FE_167]["evidence"]["structure_model_alternative"]
    assert hematite["spacegroup_number"] == 166
    assert block["overall"]["level"] == "high"


def test_different_crystal_family_is_a_polymorph_ambiguity():
    rutile, anatase = "TiO2_136_(cod_1)-0", "TiO2_141_(cod_2)-0"
    quartz = "SiO2_154_(cod_3)-0"
    target = _evidence("final", {rutile: (50.0, 0.5), quartz: (50.0, 0.5)}, 5.0)
    near = _evidence("candidate:1", {anatase: (50.0, 0.5), quartz: (50.0, 0.5)}, 5.2,
                     number=2, source_index=1)
    same = _evidence("candidate:0", {rutile: (50.0, 0.5), quartz: (50.0, 0.5)}, 5.0,
                     number=1, source_index=0)
    phase = _evaluate(target, [same, near])["phases"][rutile]
    assert phase["evidence"]["structure_model_alternative"]["polymorph"] is False  # same family
    cubic = "TiO2_225_(cod_4)-0"
    other = _evidence("candidate:1", {cubic: (50.0, 0.5), quartz: (50.0, 0.5)}, 5.2,
                      number=2, source_index=1)
    phase = _evaluate(target, [same, other])["phases"][rutile]
    assert phase["evidence"]["structure_model_alternative"]["polymorph"] is True
    assert phase["level"] == "ambiguous"
    assert phase["evidence"]["alternative"] is None  # TiO2 is still present in the alternative
    assert any("different polymorph" in reason for reason in phase["reasons"])
    assert not any("without this phase" in reason for reason in phase["reasons"])


def test_alternative_lacking_the_formula_is_still_reported_with_setting_variants():
    displayed = _task22_candidates()
    lacking = _evidence("candidate:9", {Y_IA3: (50.0, 0.7), YFO: (50.0, 0.7)}, 3.2,
                        number=6, source_index=9)
    block = _evaluate(displayed[0], displayed + [lacking])
    alternative = block["phases"][FE_167]["evidence"]["alternative"]
    assert alternative["candidate_number"] == 6
    assert block["phases"][FE_167]["level"] == "ambiguous"


def test_summarize_document_candidates_get_hamilton_and_displacement():
    from datetime import datetime

    from dara.server import api_router
    from dara.server import fit_cautions as fc

    fc.clear_lattice_flag_cache()
    first, second = "CaF2_225_(cod_2300449)-0", "NaCl_225_(cod_1000041)-0"

    def refinement(rwp, names, parameters):
        def phase():
            return SimpleNamespace(
                spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=(0.5, 0.4),
                a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
                xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
            )

        raw = f"BGMN version 4.2.23, 4000 measured points, 50 peaks, {parameters} parameters\n"
        return SimpleNamespace(
            lst_data=SimpleNamespace(
                rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
                phases_results={name: phase() for name in names}, EPS2=-0.001, raw_lst=raw,
            ),
            plot_data=SimpleNamespace(x=np.array([20.0, 21.0, 22.0, 30.0])),
            get_phase_weights=lambda: {name: 1.0 / len(names) for name in names},
        )

    both = refinement(5.0, [first, second], 30)
    only = refinement(9.0, [first], 20)
    job = {
        "index": 5, "uuid": "u-ham", "status": "COMPLETED",
        "job": {"name": "x.xy", "function_kwargs": {}, "metadata": {}},
        "submitted_time": datetime(2026, 1, 1, 0, 0), "start_time": datetime(2026, 1, 1, 0, 1),
        "end_time": datetime(2026, 1, 1, 0, 3),
    }
    document = SimpleNamespace(
        results=[
            ([[SimpleNamespace(filename=first), SimpleNamespace(filename=second)]], both),
            ([[SimpleNamespace(filename=first)]], only),
        ],
        grouped_phases=None, missing_peaks=None, extra_peaks=None, screening_rwps=None,
        final_result=both, best_rwp=5.0, precursors=[], phase_predictor=None,
        predict_kwargs=None, search_kwargs={"instrument_profile": "Aeris-fds-Pixcel1d-Medipix3"},
        final_refinement_params=None, input_cifs=[],
        xrd_data=SimpleNamespace(intensities=np.array([1.5, 2.5, 3.5]), intensity_unit="cps"),
    )
    payload = api_router.summarize_document(job, document, persist_summary=False)
    candidate = payload["all_results"][0]["confidence"]
    # regression: the candidate used to receive the (cifs, refinement) tuple, so every
    # refinement-based criterion silently stayed empty
    assert candidate["sample_displacement"]["level"] != "unknown"
    assert candidate["sample_displacement"]["eps2"] == pytest.approx(-0.001)
    hamilton = candidate["phases"][second]["evidence"]["hamilton"]
    assert hamilton is not None and hamilton["b"] == 10 and hamilton["dof"] == 3970
    assert payload["final_result"]["confidence"]["phases"][second]["evidence"]["hamilton"]
    fc.clear_lattice_flag_cache()


def test_phase_details_fill_weight_esd_for_orientation_refined_phases():
    from dara.server import api_router, task_summary

    name = "CaF2_225_(cod_2300449)-0"
    phase = SimpleNamespace(
        spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=0.5,  # mean value, no esd
        a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
        xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
    )
    refinement = SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=5.0, phases_results={name: phase},
            model_extra={"QCaF2225cod23004490": (1.0, 0.0031)},
        ),
    )
    details = api_router._phase_refinement_details(refinement)
    assert details[name]["weight_esd_percent"] == pytest.approx(0.31)
    digest = task_summary._phase_digests(refinement)[0]
    assert digest["weight_esd_percent"] == pytest.approx(0.31)


# --- classified unexplained peaks ---------------------------------------------------------

def _entry(tt, rel, cls, snr=20.0, hint=None):
    return {"two_theta": tt, "rel_height": rel, "snr": snr, "class": cls, "source": "peak",
            "attribution": None, "hint": hint}


def _summary(entries, hints=None, anode="cu"):
    counts = {"noise": 0, "minor": 0, "significant": 0, "instrument": 0}
    for entry in entries:
        counts[entry["class"]] += 1
    counts["total"] = len(entries)
    return {
        "missing_classified": entries, "counts_by_class": counts, "trace_hints": hints or [],
        "missing_significance": {"anode": anode},
    }


def _overall_with(summary, **kwargs):
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    return _evaluate(_evidence("final", base, 7.0), [], missing_peaks=len(summary.get("missing_classified", [])),
                     missing_summary=summary, **kwargs)["overall"]


def test_minor_unexplained_peaks_do_not_lower_the_verdict():
    summary = _summary([_entry(33.44, 0.0375, "minor", 5.4), _entry(35.89, 0.03, "minor", 4.3),
                        _entry(99.28, 0.029, "minor", 3.9)])
    overall = _overall_with(summary)
    assert overall["level"] == "high"
    assert overall["reasons"] == [
        "All 2 phases are supported by every criterion.",
        "3 minor unexplained peaks (up to 3.8% of the strongest peak) do not affect the verdict.",
    ]
    hinted = _summary([_entry(33.44, 0.0375, "minor")], hints=[
        {"formula": "Fe2O3", "spacegroup_symbol": "R-3c"}])
    assert _overall_with(hinted)["reasons"][-1] == (
        "1 minor unexplained peak (3.8% of the strongest peak) does not affect the verdict. "
        "Positions are consistent with trace Fe2O3 (R-3c)."
    )
    bare = _summary([_entry(33.44, 0.0375, "minor")], hints=[{"formula": "Fe2O3"}])
    assert _overall_with(bare)["reasons"][-1].endswith("Positions are consistent with trace Fe2O3.")


def test_one_significant_peak_asks_for_a_check():
    overall = _overall_with(_summary([_entry(31.93, 0.028, "significant", 16.3)]))
    assert overall["level"] == "check"
    assert overall["reasons"] == [
        "1 significant unexplained peak at 31.93° (2.8% of the strongest peak)."
    ]


def test_three_significant_peaks_still_check_but_four_or_a_large_sum_is_expert():
    three = _summary([_entry(30.0 + i, 0.03, "significant") for i in range(3)])
    overall = _overall_with(three)
    assert overall["level"] == "check"
    assert overall["reasons"][0] == (
        "3 significant unexplained peaks at 30.00°, 31.00°, 32.00° (largest 3.0% of the strongest peak)."
    )
    many = _summary([_entry(30.0 + i, 0.03 - i * 0.001, "significant") for i in range(4)])
    four = _overall_with(many)
    assert four["level"] == "expert"
    assert four["reasons"][0] == "4 significant unexplained peaks (largest 3.0% of the strongest peak)."
    long = _summary([_entry(30.0 + i, 0.021, "significant") for i in range(3)])
    assert _overall_with(long)["reasons"][0].endswith("30.00°, 31.00°, 32.00° (largest 2.1% of the strongest peak).")
    five = _summary([_entry(30.0 + i, 0.021, "significant") for i in range(5)])
    assert _overall_with(five)["level"] == "expert"
    big_sum = _summary([_entry(30.0, 0.11, "significant"), _entry(31.0, 0.10, "significant")])
    overall = _overall_with(big_sum)
    assert overall["level"] == "expert"
    assert overall["reasons"][0] == "2 significant unexplained peaks (largest 11.0% of the strongest peak)."
    single = _overall_with(_summary([_entry(30.0, 0.25, "significant")]))
    assert single["level"] == "expert"
    assert single["reasons"][0] == "1 significant unexplained peak at 30.00° (25.0% of the strongest peak)."
    sum_below = _summary([_entry(30.0, 0.10, "significant"), _entry(31.0, 0.099, "significant")])
    assert _overall_with(sum_below)["level"] == "check"


def test_instrument_lines_do_not_lower_the_verdict():
    one = _overall_with(_summary([_entry(32.43, 0.008, "instrument")]))
    assert one["level"] == "high"
    assert one["reasons"][-1] == "1 weak peak matches a Cu Kβ or W Lα1 line of a strong reflection."
    kb = dict(_entry(32.43, 0.008, "instrument"), attribution={"type": "kbeta"})
    wl = dict(_entry(34.45, 0.007, "instrument"), attribution={"type": "w_lalpha"})
    assert _overall_with(_summary([kb]))["reasons"][-1] == (
        "1 weak peak matches a Cu Kβ line of a strong reflection."
    )
    assert _overall_with(_summary([wl]))["reasons"][-1] == (
        "1 weak peak matches a W Lα1 line of a strong reflection."
    )
    assert _overall_with(_summary([kb, wl]))["reasons"][-1] == (
        "2 weak peaks match Cu Kβ or W Lα1 lines of strong reflections."
    )
    two = _overall_with(_summary([_entry(32.43, 0.008, "instrument"), _entry(34.45, 0.007, "instrument")]))
    assert two["reasons"][-1] == "2 weak peaks match Cu Kβ or W Lα1 lines of strong reflections."
    unknown = _overall_with(_summary([_entry(32.43, 0.008, "instrument")], anode=None))
    assert unknown["reasons"][-1] == "1 weak peak matches a Kβ or W Lα1 line of a strong reflection."


def test_info_reasons_follow_check_reasons_and_total_stays_at_four():
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    summary = _summary([_entry(30.0, 0.03, "significant"), _entry(33.0, 0.01, "minor")])
    block = _evaluate(_evidence("final", base, 7.0, gof=3.5), [], missing_peaks=2, missing_summary=summary)
    reasons = block["overall"]["reasons"]
    assert block["overall"]["level"] == "check"
    assert reasons[0] == "GOF is 3.50." and reasons[1].startswith("1 significant unexplained peak at 30.00°")
    assert reasons[2].startswith("1 minor unexplained peak")
    crowded = _evidence("final", base, 27.0, gof=5.0)
    block = _evaluate(crowded, [], missing_peaks=3, missing_summary=_summary(
        [_entry(30.0 + i, 0.03, "significant") for i in range(4)] + [_entry(60.0, 0.01, "minor")]))
    assert len(block["overall"]["reasons"]) == 4
    assert block["overall"]["level"] == "expert"


def test_malformed_missing_summary_falls_back_to_the_legacy_count():
    base = {A: (60.0, 0.5), B: (40.0, 0.5)}
    for summary in ({}, None, {"counts_by_class": "x"}, {"missing_classified": []}, []):
        block = _evaluate(_evidence("final", base, 7.0), [], missing_peaks=5, missing_summary=summary)
        assert block["overall"]["level"] == "expert"
        assert "5 observed peaks are not explained." in block["overall"]["reasons"]
        block = _evaluate(_evidence("final", base, 7.0), [], missing_peaks=2, missing_summary=summary)
        assert block["overall"]["level"] == "check"
    # classified payloads ignore the legacy count entirely
    clean = _summary([_entry(50.0, 0.001, "noise")] * 8)
    assert _evaluate(_evidence("final", base, 7.0), [], missing_peaks=8,
                     missing_summary=clean)["overall"]["level"] == "high"


def test_confidence_for_selection_classifies_the_final_result():
    from datetime import datetime  # noqa: F401  (kept local like the integration test above)

    name = "CaF2_225_(cod_2300449)-0"
    x = np.arange(10.0, 80.0, 0.01)
    rng = np.random.default_rng(0)
    calc = 1000 + 10000 * np.exp(-0.5 * ((x - 30) / 0.065) ** 2)
    obs = calc + rng.normal(0, 31.6, x.size) + 400 * np.exp(-0.5 * ((x - 40) / 0.065) ** 2)
    phase = SimpleNamespace(
        spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=(0.5, 0.4), a=(0.546, 0.0001), b=None,
        c=None, alpha=None, beta=None, gamma=None, xray_density=3.2, rphase=4.0, model_extra={}, unit="NM",
    )
    final = SimpleNamespace(
        lst_data=SimpleNamespace(rwp=5.0, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0,
                                 num_steps=3, phases_results={name: phase}),
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=calc, y_bkg=np.full_like(x, 1000.0)),
        refinement_metrics=SimpleNamespace(missing_peaks=[[40.0, 3.0]], extra_peaks=[],
                                           intensity_mismatch_peaks=[]),
        get_phase_weights=lambda: {name: 1.0},
    )
    document = SimpleNamespace(
        results=[([[SimpleNamespace(filename=name)]], final)], final_result=final, input_cifs=[],
        search_kwargs={"wavelength": "Cu"}, search_summary=None,
        xrd_data=SimpleNamespace(intensities=obs, angles=x, intensity_unit=None),
    )
    block = cf.confidence_for_document(document)
    assert block["overall"]["level"] == "check"
    assert any(reason.startswith("1 significant unexplained peak at 40.00°")
               for reason in block["overall"]["reasons"])
