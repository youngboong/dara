"""Tests for the candidate pre-filter (amount upper bound)."""

from pathlib import Path

import numpy as np
import pytest

from dara.search import prefilter as pf

LAM1 = 1.540598
LAM2 = 1.544426
ENV = "DARA_CANDIDATE_PREFILTER"

TRUE_TWO_THETA = [22.0, 30.0, 38.0, 47.0, 55.0, 63.0, 72.0, 80.0]
TRUE_INTENSITY = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2]
GAPS = [26.0, 34.0, 42.5, 51.0, 59.0, 67.5, 76.0]  # centres of regions without true peaks
FAST = pf.PrefilterConfig(min_candidates=0, keep_top_n=0)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def sticks(two_thetas, intensities):
    d = LAM1 / (2 * np.sin(np.radians(np.asarray(two_thetas, float) / 2)))
    return list(zip(d, intensities))


def synthetic_pattern(table, scale=1.0, hump=False, seed=1, amplitude=20000.0):
    """Gaussian peaks (FWHM 0.1 deg) with Ka2 copies, background and Poisson noise."""
    x = np.arange(10.0, 90.0, 0.01)
    y = 200.0 + 3000.0 / x
    sigma = 0.1 / 2.3548
    for d, intensity in table:
        for lam, weight in ((LAM1, 1.0), (LAM2, 0.5)):
            arg = lam / (2 * scale * d)
            if arg >= 1:
                continue
            centre = 2 * np.degrees(np.arcsin(arg))
            y += amplitude * intensity * weight * np.exp(-0.5 * ((x - centre) / sigma) ** 2)
    if hump:
        y += 3000.0 * np.exp(-0.5 * ((x - 25.0) / (8.0 / 2.3548)) ** 2)
    return x, np.random.default_rng(seed).poisson(y).astype(float)


def fake_simulator(table, failing=()):
    def simulate(path, wavelength_a, two_theta_range):
        stem = Path(path).stem
        if stem in failing:
            raise RuntimeError("cannot simulate")
        d = np.array([item[0] for item in table[stem]])
        intensity = np.array([item[1] for item in table[stem]]) * 100.0
        two_theta = 2 * np.degrees(np.arcsin(wavelength_a / (2 * d)))
        return two_theta, intensity, d

    return simulate


def decoy(jitter_seed, count=7):
    rng = np.random.default_rng(jitter_seed)
    centres = np.array(GAPS[:count]) + rng.uniform(-0.5, 0.5, count)
    return sticks(centres, rng.uniform(0.3, 1.0, count))


TRUE_STICKS = sticks(TRUE_TWO_THETA, TRUE_INTENSITY)


def run(table, pattern, candidates=None, **kwargs):
    angles, counts = pattern
    candidates = candidates or [Path(f"{stem}.cif") for stem in table]
    kwargs.setdefault("config", FAST)
    return pf.prefilter_candidates(
        angles, counts, candidates, wavelength="Cu", simulate=fake_simulator(table, kwargs.pop("failing", ())),
        parallel=False, **kwargs,
    )


@pytest.mark.parametrize("scale", [1.0, 1.025])
def test_true_phase_kept_decoys_excluded(scale):
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Decoy_1_(cod_2)-0": decoy(1), "Decoy_2_(cod_3)-0": decoy(2)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS, scale=scale))
    assert [p.stem for p in outcome.kept] == ["True_1_(cod_1)-0"]
    assert outcome.report["applied"] is True
    assert outcome.report["excluded"] == 2
    assert outcome.scores["True_1_(cod_1)-0"] > 0.5
    assert outcome.scores["Decoy_1_(cod_2)-0"] < 0.05
    names = [item["file"] for item in outcome.report["excluded_candidates"]]
    assert set(names) == {"Decoy_1_(cod_2)-0.cif", "Decoy_2_(cod_3)-0.cif"}


def test_decoy_excluded_and_kept_order_equals_input_order():
    table = {
        "A_1_(cod_1)-0": decoy(3),
        "True_1_(cod_2)-0": TRUE_STICKS,
        "B_1_(cod_3)-0": decoy(4),
        "Other_1_(cod_4)-0": sticks(TRUE_TWO_THETA[:5], TRUE_INTENSITY[:5]),
    }
    candidates = [Path(f"{stem}.cif") for stem in table]
    outcome = run(table, synthetic_pattern(TRUE_STICKS), candidates=candidates)
    assert outcome.kept == [candidates[1], candidates[3]]
    assert all(kept is original for kept, original in zip(outcome.kept, [candidates[1], candidates[3]]))


def test_protected_stem_and_formula_are_kept():
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Fe2O3_167_(cod_2)-0": decoy(5), "Zn_1_(cod_3)-0": decoy(6)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS), protected_stems={"Zn_1_(cod_3)-0"}, protected_formulas=["Fe2O3"])
    assert {p.stem for p in outcome.kept} == set(table)
    assert outcome.report["protected"] == 2
    assert outcome.report["excluded"] == 0
    assert outcome.scores["Fe2O3_167_(cod_2)-0"] is not None  # still scored


def test_too_few_candidates_with_default_config():
    table = {f"Decoy{i}_1_(cod_{i})-0": decoy(i) for i in range(10)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS), config=pf.PrefilterConfig())
    assert outcome.report["applied"] is False
    assert outcome.report["reason_not_applied"] == "too_few_candidates"
    assert len(outcome.kept) == 10


def test_disabled_returns_input_without_touching_pattern():
    candidates = [Path("A.cif"), Path("B.cif")]
    outcome = pf.prefilter_candidates(None, None, candidates, wavelength="Cu", enabled=False)
    assert outcome.kept == candidates and outcome.kept is not candidates
    assert outcome.report["enabled"] is False
    assert outcome.report["applied"] is False
    assert outcome.report["reason_not_applied"] == "disabled"
    assert outcome.scores == {}


def test_simulation_failure_keeps_candidate():
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Broken_1_(cod_2)-0": decoy(7), "Decoy_1_(cod_3)-0": decoy(8)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS), failing=("Broken_1_(cod_2)-0",))
    assert {p.stem for p in outcome.kept} == {"True_1_(cod_1)-0", "Broken_1_(cod_2)-0"}
    assert outcome.report["simulation_failed"] == 1
    assert outcome.scores["Broken_1_(cod_2)-0"] is None


def test_top_n_safety_net_keeps_exactly_forty():
    table = {f"D{i:02d}_1_(cod_{i})-0": decoy(100 + i) for i in range(60)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS), config=pf.PrefilterConfig())
    assert outcome.report["applied"] is True
    assert len(outcome.kept) == 40
    assert outcome.report["kept_top_n"] == 40
    assert outcome.report["excluded"] == 20
    assert [p.stem for p in outcome.kept] == [p.stem for p in outcome.kept if p.stem in table]  # input order
    positions = [list(table).index(p.stem) for p in outcome.kept]
    assert positions == sorted(positions)


def test_strongest_peak_in_excluded_range_still_kept():
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Decoy_1_(cod_2)-0": decoy(9)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS), excluded_ranges=[[21.0, 23.0]])
    assert [p.stem for p in outcome.kept] == ["True_1_(cod_1)-0"]
    assert outcome.report["excluded"] == 1


def test_broad_amorphous_hump_does_not_remove_true_phase():
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Decoy_1_(cod_2)-0": decoy(10)}
    outcome = run(table, synthetic_pattern(TRUE_STICKS, hump=True))
    assert "True_1_(cod_1)-0" in {p.stem for p in outcome.kept}


def test_few_strong_peaks_are_never_judged():
    few = sticks([25.0, 41.0, 60.0, 77.0], [1.0, 0.9, 0.05, 0.04])  # two peaks above 10 %
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Few_1_(cod_2)-0": few}
    outcome = run(table, synthetic_pattern(TRUE_STICKS))
    assert {p.stem for p in outcome.kept} == set(table)
    assert outcome.report["insufficient_peaks"] == 1
    assert outcome.scores["Few_1_(cod_2)-0"] is None


def test_wavelength_in_angstrom_instead_of_nm_is_unsupported():
    table = {"A_1_(cod_1)-0": decoy(11)}
    angles, counts = synthetic_pattern(TRUE_STICKS)
    outcome = pf.prefilter_candidates(
        angles, counts, [Path("A_1_(cod_1)-0.cif")], wavelength=15.4, config=FAST,
        simulate=fake_simulator(table), parallel=False,
    )
    assert outcome.report["applied"] is False
    assert outcome.report["reason_not_applied"] == "unsupported_wavelength"
    assert len(outcome.kept) == 1


def test_unsuitable_pattern_is_not_filtered():
    table = {"A_1_(cod_1)-0": decoy(12)}
    outcome = pf.prefilter_candidates(
        np.arange(10, 15, 0.01), np.full(500, 100.0), [Path("A_1_(cod_1)-0.cif")],
        wavelength="Cu", config=FAST, simulate=fake_simulator(table), parallel=False,
    )
    assert outcome.report["reason_not_applied"] == "pattern_unsuitable"


def test_report_schema():
    table = {"True_1_(cod_1)-0": TRUE_STICKS, "Decoy_1_(cod_2)-0": decoy(13)}
    report = run(table, synthetic_pattern(TRUE_STICKS)).report
    assert set(report) >= {
        "enabled", "applied", "reason_not_applied", "before", "after", "excluded", "protected",
        "insufficient_peaks", "simulation_failed", "simulation_timeout", "kept_top_n", "floor",
        "parameters", "duration_s", "excluded_candidates",
    }
    assert set(report["floor"]) == {"rel_floor", "noise_k", "sigma", "net_max", "value"}
    assert report["floor"]["value"] >= report["floor"]["rel_floor"] * report["floor"]["net_max"] - 1e-2
    assert set(report["parameters"]) >= {"strong_rel", "max_strong", "outlier_fraction", "scale_range", "keep_top_n"}
    entry = report["excluded_candidates"][0]
    assert set(entry) == {"file", "source", "score", "n_strong", "best_scale", "best_shift", "strong_2theta"}
    assert entry["source"] == "cod"
    import json

    json.dumps(report)  # JSON-safe


def test_decide_is_shared_and_respects_floor():
    scores = {"a": 0.5, "b": 0.001, "c": None, "d": 0.002}
    inputs = {"sigma": 0.01, "net_max": 1000.0}
    config = pf.PrefilterConfig(keep_top_n=0)
    excluded, reasons = pf.decide(scores, inputs, config, protected={"d"})
    assert excluded == ["b"]
    assert reasons == {"a": "above_floor", "c": "unjudged", "d": "protected"}
    excluded, _ = pf.decide(scores, inputs, config, rel_floor=0.0001)
    assert excluded == []


@pytest.mark.parametrize(
    ("requested", "mode", "explicit", "env", "expected"),
    [
        (True, "elemental", False, None, (True, None)),
        (False, "elemental", False, None, (False, "disabled")),
        (None, "elemental", False, None, (False, "disabled")),
        (None, "elemental", False, "on", (True, None)),
        (True, "elemental", False, "off", (False, "disabled_by_env")),
        (None, "elemental", False, "off", (False, "disabled_by_env")),
        (True, "known_mixture", False, None, (False, "known_mixture")),
        (True, "elemental", True, None, (False, "explicit_cifs")),
        (True, "reaction", False, None, (True, None)),
        (False, "elemental", False, "on", (False, "disabled")),
    ],
)
def test_resolve_prefilter_enabled(monkeypatch, requested, mode, explicit, env, expected):
    if env is not None:
        monkeypatch.setenv(ENV, env)
    assert pf.resolve_prefilter_enabled(requested, mode, explicit_cifs=explicit) == expected


def test_stem_formula():
    assert pf.stem_formula("Fe2O3_167_(cod_1)-0") == "Fe2O3"
    assert pf.stem_formula("Bi2VO5.5_14_(mp_mp-1)-0") is not None
    assert pf.stem_formula("not-a-formula_1_x") is None


def _cif_text(name, a, atoms):
    lines = [
        f"data_{name}",
        "_symmetry_space_group_name_H-M 'P 1'",
        "loop_", "_symmetry_equiv_pos_as_xyz", "x,y,z",
        f"_cell_length_a {a}", f"_cell_length_b {a}", f"_cell_length_c {a}",
        "_cell_angle_alpha 90", "_cell_angle_beta 90", "_cell_angle_gamma 90",
        "loop_", "_atom_site_label", "_atom_site_type_symbol",
        "_atom_site_fract_x", "_atom_site_fract_y", "_atom_site_fract_z", "_atom_site_occupancy",
    ]
    lines += [f"{label} {symbol} {x} {y} {z} 1" for label, symbol, x, y, z in atoms]
    return "\n".join(lines) + "\n"


def test_real_simulation_two_structures(tmp_path):
    fcc = [(f"Na{i}", "Na", *xyz) for i, xyz in enumerate([(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)])]
    fcc += [(f"Cl{i}", "Cl", *xyz) for i, xyz in enumerate([(.5, .5, .5), (.5, 0, 0), (0, .5, 0), (0, 0, .5)])]
    nacl = tmp_path / "NaCl_225_(cod_1)-0.cif"
    nacl.write_text(_cif_text("NaCl", 5.64, fcc))
    cscl = tmp_path / "CsCl_221_(cod_2)-0.cif"
    cscl.write_text(_cif_text("CsCl", 4.12, [("Cs1", "Cs", 0, 0, 0), ("Cl1", "Cl", .5, .5, .5)]))

    two_theta, intensity, d = pf.simulate_reflections(nacl, LAM1, (5.0, 95.0))
    assert len(two_theta) == len(intensity) == len(d) > 5
    table = list(zip(d, intensity / intensity.max()))
    pattern = synthetic_pattern(table)
    outcome = pf.prefilter_candidates(
        *pattern, [nacl, cscl], wavelength="Cu", config=pf.PrefilterConfig(min_candidates=1, keep_top_n=0),
        parallel=False,
    )
    assert outcome.report["applied"] is True
    assert nacl in outcome.kept
    assert outcome.scores[nacl.stem] > 0.5
    assert outcome.scores[nacl.stem] > outcome.scores[cscl.stem]
