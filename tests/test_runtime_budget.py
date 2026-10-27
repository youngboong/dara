"""Job-level tests for the pre-filter hook, the screening options and the run-time budget.

The search, the refinement and the pre-filter are replaced by fakes, following
``tests/test_structure_quality.py``.
"""

from __future__ import annotations

import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from dara import jobs
from dara.refine import RefinementPhase
from dara.search import prefilter

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


def salt(cation, a, anion="Cl"):
    atoms = []
    for i, (x, y, z) in enumerate([(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)]):
        atoms.append(f"{cation}{i} {cation} {x} {y} {z} 1")
    for i, (x, y, z) in enumerate([(.5, .5, .5), (.5, 0, 0), (0, .5, 0), (0, 0, .5)]):
        atoms.append(f"{anion}{i} {anion} {x} {y} {z} 1")
    return CIF.format(name=f"{cation}{anion}", a=a, atoms="\n".join(atoms))


class FakeLst:
    def __init__(self, rwp):
        self.rwp = rwp


class FakeRefinement:
    refinement_metrics = None

    def __init__(self, rwp):
        self.lst_data = FakeLst(rwp)


class FakeResult:
    """One search result with a single slot offering several alternative phases."""

    def __init__(self, phases, rwp=5.0):
        self.phases = (tuple(phases),)
        self.foms = (tuple(float(len(phases) - i) for i in range(len(phases))),)
        self.lattice_strains = (tuple(0.0 for _ in phases),)
        self.refinement_result = FakeRefinement(rwp)
        self.missing_peaks = None
        self.extra_peaks = None

    @property
    def grouped_phases(self):  # fresh lists on every access, like the real SearchResult
        return [[(p.path.stem.split("_")[0], [p.path]) for p in self.phases[0]]]

    def model_copy(self, deep=False, update=None):
        clone = FakeResult(list(self.phases[0]))
        clone.__dict__.update(self.__dict__)
        clone.__dict__.update(update or {})
        return clone


class FakeDatabase:
    name = "fake"

    def __init__(self, files):
        self.files = files

    def _write(self, dest_dir):
        for name, text in self.files.items():
            (Path(dest_dir) / f"{name}.cif").write_text(text)

    def get_cifs_by_chemsys(self, elements, e_hull_filter=None, copy_files=True, dest_dir=None):
        self._write(dest_dir)

    def get_cifs_by_formulas(self, formulas, e_hull_filter=None, copy_files=True, dest_dir=None):
        if getattr(self, "write_formulas", False):
            self._write(dest_dir)

    def as_dict(self):
        return {"fake": "database"}


class FakePattern:
    angles = None
    intensities = None

    def as_dict(self):
        return {"fake": "pattern"}

    def to_xy_file(self, path):
        Path(path).write_text("10 1\n20 2\n")


THREE = {
    "NaCl_225_(cod_5)-0": salt("Na", 5.64),
    "KCl_225_(cod_6)-0": salt("K", 6.29),
    "RbCl_225_(cod_7)-0": salt("Rb", 6.58),
}


def run_job(monkeypatch, tmp_path, files=THREE, *, search_returns=None, clock=None, refinement_hook=None,
            make_kwargs=None, write_formulas=False):
    monkeypatch.chdir(tmp_path)
    captured = {"search_calls": [], "refinements": []}

    def fake_search(pattern_path, phases, **kwargs):
        by_stem = {RefinementPhase.make(p).path.stem: RefinementPhase.make(p) for p in phases}
        captured["searched"] = sorted(by_stem)
        captured["search_kwargs"] = kwargs
        if search_returns is not None:
            return search_returns(by_stem)
        names = sorted(by_stem)[:3]
        return [FakeResult([by_stem[n] for n in names])]

    def fake_refinement(pattern_path, phases, working_dir, phase_params, **kwargs):
        captured["refinements"].append(kwargs)
        if refinement_hook:
            refinement_hook()
        return FakeRefinement(4.0 + len(captured["refinements"]))

    monkeypatch.setattr(jobs, "search_phases", fake_search)
    monkeypatch.setattr(jobs, "do_refinement", fake_refinement)
    monkeypatch.setattr(jobs, "PhaseSearchDocument", lambda **kwargs: type("Doc", (), kwargs)())
    if clock is not None:
        monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=clock))
    database = FakeDatabase(files)
    database.write_formulas = write_formulas
    kwargs = {"analysis_mode": "elemental", "elements": ["Cl", "K", "Na", "Rb"], "deduplicate_structures": False}
    kwargs.update(make_kwargs or {})
    job = jobs.PhaseSearchMaker(name="fake", phase_predictor=None).make(
        xrd_data=FakePattern(), cif_dbs=[database], **kwargs
    )
    function = types.MethodType(job.function.original, job.function.__self__)
    return function(*job.function_args, **job.function_kwargs), captured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DARA_CANDIDATE_PREFILTER", "DARA_SCREENING_TIMEOUT_S", "DARA_MAX_RUNTIME_S",
                 "DARA_MP_LATTICE_WINDOW", "DARA_CIF_VALIDATION"):
        monkeypatch.delenv(name, raising=False)


def test_default_call_is_legacy(monkeypatch, tmp_path):
    document, captured = run_job(monkeypatch, tmp_path, make_kwargs={"search_kwargs": {"bgmn_timeout": 5}})
    # search_kwargs is stored untouched (no new options leak into it)
    assert document.search_kwargs == {"bgmn_timeout": 5}
    kwargs = captured["search_kwargs"]
    assert kwargs["screening_timeouts"] == {"default": 1200.0, "very_heavy": 1200.0, "source": "legacy"}
    assert kwargs["screening_deadline"] is None
    assert kwargs["screening_priority"] is None
    assert kwargs["search_deadline"] is None
    assert kwargs["expansion_bgmn_timeout"] is None
    assert isinstance(kwargs["search_report"], dict)
    # the stray key is never forwarded to the final refinement
    assert captured["refinements"] and all("bgmn_timeout" not in call for call in captured["refinements"])

    prefilter_report = document.candidate_summary["prefilter"]
    assert prefilter_report["applied"] is False
    assert prefilter_report["enabled"] is False
    assert prefilter_report["reason_not_applied"] == "disabled"
    assert document.candidate_summary["screening"] == {}
    assert document.candidate_summary["repaired"] == []

    summary = document.search_summary
    assert summary["runtime_budget_s"] is None
    assert summary["stopped_early"] is False and summary["stopped_at_stage"] is None
    assert summary["unexpanded_nodes"] == 0 and summary["final_refinement_skipped"] == 0
    assert summary["screening_timed_out"] == 0 and summary["screening_not_screened"] == 0
    assert summary["prefilter_excluded"] == 0 and summary["candidates_screened_total"] == 0
    assert summary["elapsed_s"] >= 0
    assert summary["final_refinement_attempted"] == 3
    assert summary["final_refinement_succeeded"] == 3


def test_budget_skips_remaining_final_refinements(monkeypatch, tmp_path):
    state = {"t": 1000.0}
    document, captured = run_job(
        monkeypatch, tmp_path, clock=lambda: state["t"],
        refinement_hook=lambda: state.__setitem__("t", state["t"] + 10_000.0),
        make_kwargs={"max_runtime_s": 600},
    )
    kwargs = captured["search_kwargs"]
    assert kwargs["screening_deadline"] == pytest.approx(1000.0 + 0.55 * 600)
    assert kwargs["search_deadline"] == pytest.approx(1000.0 + 0.80 * 600)
    assert kwargs["expansion_bgmn_timeout"] == pytest.approx(150.0)
    assert kwargs["screening_priority"] is None  # pre-filter off: no scores
    summary = document.search_summary
    assert summary["runtime_budget_s"] == 600.0
    assert summary["final_refinement_attempted"] == 1
    assert summary["final_refinement_skipped"] == 2
    assert summary["final_refinement_succeeded"] == 1
    assert summary["stopped_early"] is True and summary["stopped_at_stage"] == "final_refinement"
    assert len(captured["refinements"]) == 1


def test_budget_floor_and_stage_reporting(monkeypatch, tmp_path):
    def search_with_report(pattern_path, phases, **kwargs):
        kwargs["search_report"].update(
            {"screening": {"total": 3, "timed_out": 1, "not_screened_deadline": 2},
             "search": {"stopped": True, "unexpanded_nodes": 4}}
        )
        by_stem = {RefinementPhase.make(p).path.stem: RefinementPhase.make(p) for p in phases}
        return [FakeResult([by_stem[n] for n in sorted(by_stem)[:2]])]

    document = _run_with_search(monkeypatch, tmp_path, search_with_report, make_kwargs={"max_runtime_s": 10})
    summary = document.search_summary
    assert summary["runtime_budget_s"] == 300.0  # budgets below five minutes are raised
    assert summary["stopped_early"] is True
    assert summary["stopped_at_stage"] == "screening"  # the earliest stage wins
    assert summary["screening_timed_out"] == 1 and summary["screening_not_screened"] == 2
    assert summary["unexpanded_nodes"] == 4 and summary["candidates_screened_total"] == 3
    assert document.candidate_summary["screening"]["total"] == 3


def _run_with_search(monkeypatch, tmp_path, search, make_kwargs=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jobs, "search_phases", search)
    monkeypatch.setattr(jobs, "do_refinement", lambda pattern_path, phases, working_dir, phase_params, **kw: FakeRefinement(4.0))
    monkeypatch.setattr(jobs, "PhaseSearchDocument", lambda **kwargs: type("Doc", (), kwargs)())
    kwargs = {"analysis_mode": "elemental", "elements": ["Cl", "K", "Na", "Rb"], "deduplicate_structures": False}
    kwargs.update(make_kwargs or {})
    job = jobs.PhaseSearchMaker(name="fake", phase_predictor=None).make(
        xrd_data=FakePattern(), cif_dbs=[FakeDatabase(THREE)], **kwargs
    )
    function = types.MethodType(job.function.original, job.function.__self__)
    return function(*job.function_args, **job.function_kwargs)


def many_files(count=45):
    return {f"NaCl{i:02d}_225_(cod_{i})-0": salt("Na", 5.64 + i * 0.001) for i in range(count)}


def test_prefilter_hook_drops_candidates_and_stores_report(monkeypatch, tmp_path):
    files = many_files()
    dropped = "NaCl07_225_(cod_7)-0"
    seen = {}

    def fake_prefilter(angles, intensities, candidates, **kwargs):
        seen.update(kwargs)
        seen["n"] = len(candidates)
        kept = [c for c in candidates if Path(c).stem != dropped]
        report = {"enabled": True, "applied": True, "reason_not_applied": None,
                  "before": len(candidates), "after": len(kept), "excluded": 1}
        return prefilter.PrefilterOutcome(kept=kept, report=report, scores={Path(c).stem: 0.5 for c in kept})

    monkeypatch.setattr(prefilter, "prefilter_candidates", fake_prefilter)
    document, captured = run_job(
        monkeypatch, tmp_path, files=files,
        make_kwargs={"candidate_prefilter": True, "elements": ["Na", "Cl"], "max_runtime_s": 900},
    )
    assert seen["n"] == 45 and seen["enabled"] is True
    assert seen["wavelength"] == "Cu" and seen["protected_formulas"] == []
    assert dropped not in captured["searched"] and len(captured["searched"]) == 44
    assert document.candidate_summary["prefilter"]["excluded"] == 1
    assert document.search_summary["prefilter_excluded"] == 1
    # scores are handed to the screening scheduler only when a budget is active
    assert captured["search_kwargs"]["screening_priority"] is not None


def test_prefilter_scores_not_passed_without_budget(monkeypatch, tmp_path):
    def fake_prefilter(angles, intensities, candidates, **kwargs):
        return prefilter.PrefilterOutcome(
            kept=list(candidates), report={"enabled": True, "applied": True, "excluded": 0},
            scores={Path(c).stem: 0.5 for c in candidates},
        )

    monkeypatch.setattr(prefilter, "prefilter_candidates", fake_prefilter)
    _, captured = run_job(monkeypatch, tmp_path, make_kwargs={"candidate_prefilter": True})
    assert captured["search_kwargs"]["screening_priority"] is None


def test_known_mixture_never_prefilters(monkeypatch, tmp_path):
    calls = []

    def fake_prefilter(angles, intensities, candidates, **kwargs):
        calls.append(kwargs["enabled"])
        return prefilter.PrefilterOutcome(kept=list(candidates), report={"enabled": False, "applied": False, "excluded": 0})

    monkeypatch.setattr(prefilter, "prefilter_candidates", fake_prefilter)
    document, _ = run_job(
        monkeypatch, tmp_path, write_formulas=True,
        make_kwargs={"candidate_prefilter": True, "analysis_mode": "known_mixture", "precursors": ["NaCl", "KCl"]},
    )
    assert calls == [False]
    assert document.candidate_summary["prefilter"]["reason_not_applied"] == "known_mixture"


def test_env_off_overrides_submission(monkeypatch, tmp_path):
    monkeypatch.setenv("DARA_CANDIDATE_PREFILTER", "off")
    calls = []

    def fake_prefilter(angles, intensities, candidates, **kwargs):
        calls.append((angles, kwargs["enabled"]))
        return prefilter.PrefilterOutcome(kept=list(candidates), report={"enabled": False, "applied": False, "excluded": 0})

    monkeypatch.setattr(prefilter, "prefilter_candidates", fake_prefilter)
    document, _ = run_job(monkeypatch, tmp_path, make_kwargs={"candidate_prefilter": True})
    assert calls == [(None, False)]
    assert document.candidate_summary["prefilter"]["reason_not_applied"] == "disabled_by_env"


def test_env_switches_supply_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DARA_SCREENING_TIMEOUT_S", "300")
    monkeypatch.setenv("DARA_MAX_RUNTIME_S", "1200")
    document, captured = run_job(monkeypatch, tmp_path)
    kwargs = captured["search_kwargs"]
    assert kwargs["screening_timeouts"] == {"default": 300.0, "very_heavy": 600.0, "source": "env"}
    assert kwargs["screening_deadline"] is not None
    assert document.search_summary["runtime_budget_s"] == 1200.0
    # an explicit submission value wins over the environment
    _, captured = run_job(monkeypatch, tmp_path, make_kwargs={"screening_timeout_s": 120})
    assert captured["search_kwargs"]["screening_timeouts"]["source"] == "submission"
    assert captured["search_kwargs"]["screening_timeouts"]["default"] == 120.0
