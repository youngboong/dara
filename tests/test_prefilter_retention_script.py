"""Tests for scripts/check_prefilter_retention.py (no network, no BGMN, no Ray)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dara.benchmark.ground_truth import GTPhase
from dara.search import prefilter

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_prefilter_retention.py"
LAM1 = 1.540598
LAM2 = 1.544426
FAST = prefilter.PrefilterConfig(min_candidates=0, keep_top_n=0)


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("check_prefilter_retention", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CIF = """data_{name}
_symmetry_space_group_name_H-M 'P 1'
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
_cell_length_a {a}
_cell_length_b {a}
_cell_length_c {a}
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
{atoms}
"""


def salt(cation, a):
    atoms = [f"{cation}{i} {cation} {x} {y} {z} 1" for i, (x, y, z) in
             enumerate([(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)])]
    atoms += [f"Cl{i} Cl {x} {y} {z} 1" for i, (x, y, z) in
              enumerate([(.5, .5, .5), (.5, 0, 0), (0, .5, 0), (0, 0, .5)])]
    return CIF.format(name=f"{cation}Cl", a=a, atoms="\n".join(atoms))


class FakeDB:
    def __init__(self, name, files, calls):
        self.name, self.files, self.calls = name, files, calls

    def _write(self, dest_dir):
        for stem, text in self.files.items():
            (Path(dest_dir) / f"{stem}.cif").write_text(text)

    def get_cifs_by_chemsys(self, elements, e_hull_filter=None, copy_files=True, dest_dir=None):
        self.calls.append((self.name, "chemsys", tuple(elements)))
        self._write(dest_dir)

    def get_cifs_by_formulas(self, formulas, e_hull_filter=None, copy_files=True, dest_dir=None):
        self.calls.append((self.name, "formulas", tuple(formulas)))


# --------------------------------------------------------------------------- #
def test_pool_builder_order_dedup_and_screening(script, tmp_path):
    calls = []
    first = FakeDB("db1", {"NaCl_225_(cod_1)-0": salt("Na", 5.64), "KCl_225_(cod_2)-0": salt("K", 6.29)}, calls)
    second = FakeDB("db2", {"NaCl_225_(mp_mp-1)-0": salt("Na", 5.64)}, calls)  # duplicate of the first
    settings = {"cif_dbs": [first, second], "precursors": ["NaCl"], "elements": ["Cl", "Na", "K"],
                "analysis_mode": "elemental", "database_e_hull_filter": 0.05, "search_kwargs": {}, "additional_cifs": []}
    pool = script.build_pool(settings, "sample 1", tmp_path)
    assert [(c[0], c[1]) for c in calls] == [
        ("db1", "chemsys"), ("db2", "chemsys"), ("db1", "formulas"), ("db2", "formulas")
    ]
    assert pool["status"] == "ok"
    assert len(pool["paths"]) == 2  # equivalent structures merged
    assert pool["summary"]["pool"] == 2
    cached = script.build_pool(settings, "sample 1", tmp_path)
    assert cached["cached"] is True and [p.name for p in cached["paths"]] == [p.name for p in pool["paths"]]
    assert len(calls) == 4  # served from pool.json
    settings["analysis_mode"] = "reaction"
    assert script.build_pool(settings, "sample 2", tmp_path)["status"] == "reaction_mode_not_supported"


def test_known_mixture_pool_uses_formulas_only(script, tmp_path):
    calls = []
    db = FakeDB("db", {}, calls)
    settings = {"cif_dbs": [db], "precursors": ["NaCl"], "elements": [], "analysis_mode": "known_mixture",
                "database_e_hull_filter": 0.05, "search_kwargs": {}, "additional_cifs": []}
    script.build_pool(settings, "km", tmp_path)
    assert [c[1] for c in calls] == ["formulas"]


def test_gt_matching_honours_alternatives_and_space_groups(script):
    stems = ["Fe2O3_167_(cod_1)-0", "Fe2O3_14_(cod_2)-0", "FeO_225_(cod_3)-0", "ZnO_186_(cod_4)-0"]
    plain = GTPhase(formula="Fe2O3", spacegroup=167, wt_percent=100)
    full, formula_only = script.match_gt_phase(plain, stems, {})
    assert full == ["Fe2O3_167_(cod_1)-0"]
    assert formula_only == ["Fe2O3_167_(cod_1)-0", "Fe2O3_14_(cod_2)-0"]
    either = GTPhase(formula="Fe2O3", spacegroup_any_of=[14, 167], wt_percent=100)
    assert script.match_gt_phase(either, stems, {})[0] == ["Fe2O3_167_(cod_1)-0", "Fe2O3_14_(cod_2)-0"]
    alternative = GTPhase(formula="Fe2O3", accept_any_of=["ZnO"], wt_percent=100)
    assert "ZnO_186_(cod_4)-0" in script.match_gt_phase(alternative, stems, {})[0]
    any_sg = GTPhase(formula="FeO", wt_percent=100)
    assert script.match_gt_phase(any_sg, stems, {})[0] == ["FeO_225_(cod_3)-0"]
    absent = GTPhase(formula="CuO", wt_percent=100)
    assert script.match_gt_phase(absent, stems, {}) == ([], [])


# --------------------------------------------------------------------------- #
TRUE_TT = [22.0, 30.0, 38.0, 47.0, 55.0, 63.0, 72.0, 80.0]
TRUE_I = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2]
GAPS = [26.0, 34.0, 42.5, 51.0, 59.0, 67.5, 76.0]


def _sticks(tts, intensities):
    tts = np.asarray(tts, float)
    return list(zip(LAM1 / (2 * np.sin(np.radians(tts / 2))), intensities))


def _pattern():
    x = np.arange(10.0, 90.0, 0.01)
    y = 200.0 + 3000.0 / x
    sigma = 0.1 / 2.3548
    for d, intensity in _sticks(TRUE_TT, TRUE_I):
        for lam, weight in ((LAM1, 1.0), (LAM2, 0.5)):
            centre = 2 * np.degrees(np.arcsin(lam / (2 * d)))
            y += 20000.0 * intensity * weight * np.exp(-0.5 * ((x - centre) / sigma) ** 2)
    return SimpleNamespace(angles=x, intensities=np.random.default_rng(3).poisson(y).astype(float))


def _fake_simulator(table):
    def simulate(path, wavelength_a, two_theta_range):
        d = np.array([item[0] for item in table[Path(path).stem]])
        intensity = np.array([item[1] for item in table[Path(path).stem]]) * 100.0
        return 2 * np.degrees(np.arcsin(wavelength_a / (2 * d))), intensity, d

    return simulate


def _fake_gt(true_formula):
    submission = SimpleNamespace(analysis_mode="elemental", precursors=[], elements=["Fe", "O"], temperature_c=None,
                                 atmosphere="air", instrument_profile="p", wavelength="Cu")
    sample = SimpleNamespace(id="S1", pattern="x.xy", submission=submission,
                             phases=[GTPhase(formula=true_formula, spacegroup=167, wt_percent=100)])
    return SimpleNamespace(set="fake", aliases={}, samples=[sample])


def _run_main(script, monkeypatch, tmp_path, gt_formula, table):
    paths = [Path(f"/pool/{stem}.cif") for stem in table]
    monkeypatch.setattr("dara.benchmark.ground_truth.load_ground_truth", lambda name: _fake_gt(gt_formula))
    monkeypatch.setattr("dara.benchmark.ground_truth.repo_root", lambda: tmp_path)
    monkeypatch.setattr("dara.xrd.load_pattern", lambda path: _pattern())
    monkeypatch.setattr(script, "build_submission_job", lambda *a, **k: SimpleNamespace(function_kwargs={
        "cif_dbs": [], "precursors": [], "elements": ["Fe", "O"], "analysis_mode": "elemental",
        "database_e_hull_filter": 0.05, "search_kwargs": {}, "additional_cifs": []}))
    monkeypatch.setattr(script, "build_pool", lambda settings, sample_id, cache_dir, refresh=False: {
        "status": "ok", "paths": paths, "summary": {"pool": len(paths)}})
    monkeypatch.setattr(script, "make_cached_simulator", lambda cache_dir: _fake_simulator(table))
    original = script.evaluate_sample
    monkeypatch.setattr(script, "evaluate_sample", lambda *a, **k: original(*a, config=FAST, **k))
    out = tmp_path / "out"
    code = script.main(["--set", "pairwise", "--out", str(out), "--cache-dir", str(tmp_path / "cache"),
                        "--rel-floors", "0.01,0.03,0.05"])
    return code, json.loads((out / "retention.json").read_text()), out


def _decoy(seed):
    rng = np.random.default_rng(seed)
    return _sticks(np.array(GAPS) + rng.uniform(-0.5, 0.5, len(GAPS)), rng.uniform(0.3, 1.0, len(GAPS)))


def test_end_to_end_retained(script, monkeypatch, tmp_path):
    table = {"Fe2O3_167_(cod_1)-0": _sticks(TRUE_TT, TRUE_I), "Zn_1_(cod_2)-0": _decoy(1), "Ti_1_(cod_3)-0": _decoy(2)}
    code, result, out = _run_main(script, monkeypatch, tmp_path, "Fe2O3", table)
    assert code == 0
    assert set(result) == {"settings", "samples", "summary"}
    by_floor = result["summary"]["by_floor"]
    assert set(by_floor) == {"0.01", "0.03", "0.05"}
    entry = by_floor["0.03"]
    assert entry["gt_in_pool"] == 1 and entry["gt_retained"] == 1 and entry["retention"] == 1.0
    assert entry["samples_applied"] == 1
    assert entry["median_reduction_applied"] == pytest.approx(2 / 3)
    assert result["summary"]["gt_not_in_pool"] == []
    assert result["summary"]["decision"]["largest_safe_floor"] == 0.05
    sample = result["samples"][0]
    assert sample["gt"][0]["in_pool"] is True and sample["gt"][0]["retained"]["0.03"] is True
    assert sample["gt"][0]["best_score"] > 0.5
    assert (out / "retention.md").read_text().startswith("# Pre-filter retention")
    assert not (out / "retention.partial.json").exists()


def test_end_to_end_lost_phase_gives_exit_code_1(script, monkeypatch, tmp_path):
    # the pool's Fe2O3 entry has decoy peaks, so the (absent) real phase is "lost"
    table = {"Fe2O3_167_(cod_1)-0": _decoy(5), "Zn_1_(cod_2)-0": _decoy(6), "Ti_1_(cod_3)-0": _sticks(TRUE_TT, TRUE_I)}
    code, result, _ = _run_main(script, monkeypatch, tmp_path, "Fe2O3", table)
    assert code == 1
    entry = result["summary"]["by_floor"]["0.03"]
    assert entry["gt_in_pool"] == 1 and entry["gt_retained"] == 0 and entry["retention"] == 0.0
    assert entry["lost"][0]["formula"] == "Fe2O3"
    assert result["summary"]["decision"]["largest_safe_floor"] is None


def test_absent_gt_phase_is_reported_not_counted(script, monkeypatch, tmp_path):
    table = {"Fe2O3_167_(cod_1)-0": _sticks(TRUE_TT, TRUE_I), "Zn_1_(cod_2)-0": _decoy(7)}
    code, result, _ = _run_main(script, monkeypatch, tmp_path, "TiO2", table)
    assert code == 0
    assert result["summary"]["by_floor"]["0.03"]["gt_in_pool"] == 0
    assert result["summary"]["gt_not_in_pool"][0]["formula"] == "TiO2"


def test_cached_simulator_roundtrip(script, tmp_path, monkeypatch):
    calls = []

    def fake_simulate(path, wavelength_a, two_theta_range):
        calls.append(path)
        return np.array([1.0]), np.array([2.0]), np.array([3.0])

    monkeypatch.setattr(prefilter, "simulate_reflections", fake_simulate)
    cif = tmp_path / "A.cif"
    cif.write_text("data_A")
    simulate = script.make_cached_simulator(tmp_path / "cache")
    first = simulate(cif, 1.54, (5.0, 95.0))
    second = simulate(cif, 1.54, (5.0, 95.0))
    assert len(calls) == 1 and np.array_equal(first[0], second[0])
    simulate(cif, 1.54, (5.0, 90.0))
    assert len(calls) == 2
