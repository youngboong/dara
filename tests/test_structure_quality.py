"""Tests for reference provenance, the MP lattice window and CIF screening."""

import types
from pathlib import Path

import pytest

from dara import structure_quality as sq
from dara.refine import RefinementPhase

NACL = """data_NaCl
_symmetry_space_group_name_H-M 'P 1'
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
_cell_length_a {a}
_cell_length_b 5.64
_cell_length_c 5.64
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
Na1 Na 0 0 0 {occ}
Na2 Na 0 0.5 0.5 1
Na3 Na 0.5 0 0.5 1
Na4 Na 0.5 0.5 0 1
Cl1 {cl} 0.5 0.5 0.5 1
Cl2 {cl} 0.5 0 0 1
Cl3 {cl} 0 0.5 0 1
Cl4 {cl} 0 0 0.5 1
"""


def write_cif(tmp_path: Path, name: str, *, a="5.64", occ="1", cl="Cl", text=None) -> Path:
    path = tmp_path / f"{name}.cif"
    path.write_text(text if text is not None else NACL.format(a=a, occ=occ, cl=cl))
    return path


# --------------------------------------------------------------------------- #
# Part A
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name, source",
    [
        ("Fe2O3_167_(cod_1011163)-0", "cod"),
        ("Fe2O3_167_(mp_mp-19770)-0", "mp"),
        ("Fe2O3_167_(icsd_12345)-0", "icsd"),
        ("Si_227_(mp-149)-0", "mp"),
        ("Fe2O3_167", "user"),
        ("Fe2O3_167_(COD_1011163)-12.cif", "cod"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_reference_source(name, source):
    assert sq.reference_source(name) == source


def test_provenance_cod():
    info = sq.reference_provenance("Fe2O3_167_(cod_1011163)-0")
    assert info == {
        "source": "cod",
        "database_id": "1011163",
        "computed": False,
        "label": "Experimental structure (COD 1011163)",
        "url": "https://www.crystallography.net/cod/1011163.html",
        "e_above_hull_mev": 0,
    }


def test_provenance_mp_variants():
    info = sq.reference_provenance("Fe2O3_167_(mp_mp-19770)-23")
    assert info["database_id"] == "mp-19770"
    assert info["computed"] is True
    assert info["label"] == "Computed structure (Materials Project mp-19770)"
    assert info["url"] == "https://materialsproject.org/materials/mp-19770"
    assert info["e_above_hull_mev"] == 23
    legacy = sq.reference_provenance("Si_227_(mp-149)-0")
    assert legacy["database_id"] == "mp-149"
    assert legacy["source"] == "mp"
    assert sq.reference_provenance("Si_227_(mp_mp-149)-None")["e_above_hull_mev"] is None


def test_provenance_icsd_user_unknown():
    icsd = sq.reference_provenance("Fe2O3_167_(icsd_12345)-0")
    assert icsd["label"] == "Experimental structure (ICSD 12345)"
    assert icsd["computed"] is False and icsd["url"] is None
    user = sq.reference_provenance("Fe2O3_167")
    assert user["source"] == "user"
    assert user["label"] == "User-supplied CIF (source not recorded)"
    assert user["database_id"] is None and user["url"] is None
    for empty in ("", None):
        unknown = sq.reference_provenance(empty)
        assert unknown["source"] == "unknown"
        assert unknown["label"] == "Source not recorded"


def test_widen_for_mp():
    assert sq.widen_for_mp(0.02) == (-0.045, 0.02)
    assert sq.widen_for_mp(0.05) == (-0.05, 0.05)
    assert sq.widen_for_mp(0.0) == (-0.045, 0.02)
    assert sq.widen_for_mp("fixed") == "fixed"
    assert sq.widen_for_mp((-0.01, 0.01)) == (-0.01, 0.01)
    assert sq.widen_for_mp({"a": 0.01}) == {"a": 0.01}
    assert sq.MP_LATTICE_WINDOW == (-0.045, 0.02)


def test_effective_lattice_range_precedence():
    mp = "Fe2O3_167_(mp_mp-1)-0"
    cod = "Fe2O3_167_(cod_1)-0"
    assert sq.effective_lattice_range(cod, 0.02) == 0.02
    assert sq.effective_lattice_range(mp, 0.02) == 0.02  # no window given
    assert sq.effective_lattice_range(mp, 0.02, mp_window=sq.MP_LATTICE_WINDOW) == (-0.045, 0.02)
    assert sq.effective_lattice_range(cod, 0.02, mp_window=sq.MP_LATTICE_WINDOW) == 0.02
    assert sq.effective_lattice_range(
        mp, 0.02, mp_window=sq.MP_LATTICE_WINDOW,
        additional_cif_names=[mp], additional_params={"lattice_range": 0.1},
    ) == 0.1
    # additional params only count for names in additional_cif_names
    assert sq.effective_lattice_range(
        mp, 0.02, mp_window=sq.MP_LATTICE_WINDOW,
        additional_cif_names=["other"], additional_params={"lattice_range": 0.1},
    ) == (-0.045, 0.02)
    assert sq.effective_lattice_range(
        mp, 0.02, mp_window=sq.MP_LATTICE_WINDOW,
        additional_cif_names=[mp], additional_params={"lattice_range": 0.1},
        overrides={"lattice_range": 0.001},
    ) == 0.001


@pytest.mark.parametrize("value", ["0", "off", "FALSE", "No"])
def test_switches_off(monkeypatch, value):
    monkeypatch.setenv("DARA_MP_LATTICE_WINDOW", value)
    monkeypatch.setenv("DARA_CIF_VALIDATION", value)
    assert sq.mp_window_enabled() is False
    assert sq.cif_validation_enabled() is False


def test_switches_default_on(monkeypatch):
    monkeypatch.delenv("DARA_MP_LATTICE_WINDOW", raising=False)
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    assert sq.mp_window_enabled() is True
    assert sq.cif_validation_enabled() is True
    monkeypatch.setenv("DARA_MP_LATTICE_WINDOW", "on")
    assert sq.mp_window_enabled() is True


def _paths(*names):
    return [Path("/x") / f"{name}.cif" for name in names]


def test_apply_mp_window_widens_only_mp():
    phases = _paths("A_1_(mp_mp-1)-0", "B_2_(cod_2)-0", "C_3")
    out, applied = sq.apply_mp_lattice_window(phases, {"lattice_range": 0.02}, enabled=True)
    assert applied == ["A_1_(mp_mp-1)-0"]
    assert out[0].params == {"lattice_range": (-0.045, 0.02)}
    assert out[1].params == {} and out[2].params == {}
    assert all(isinstance(p, RefinementPhase) for p in out)


def test_apply_mp_window_respects_existing_and_switches():
    mp = Path("/x/A_1_(mp_mp-1)-0.cif")
    own = RefinementPhase(path=mp, params={"lattice_range": 0.1, "b1": "0_0^1"})
    out, applied = sq.apply_mp_lattice_window([own], {"lattice_range": 0.02}, enabled=True)
    assert applied == [] and out[0].params["lattice_range"] == 0.1
    out, applied = sq.apply_mp_lattice_window([mp], {"lattice_range": 0.02}, enabled=False)
    assert applied == [] and out[0].params == {}
    out, applied = sq.apply_mp_lattice_window([mp], {"lattice_range": "fixed"}, enabled=True)
    assert applied == [] and out[0].params == {}
    out, applied = sq.apply_mp_lattice_window([mp], {}, enabled=True)  # default 0.02
    assert applied == ["A_1_(mp_mp-1)-0"]
    out, applied = sq.apply_mp_lattice_window([mp], {"lattice_range": 0.05}, enabled=True)
    assert out[0].params == {"lattice_range": (-0.05, 0.05)}


# --------------------------------------------------------------------------- #
# Part B
# --------------------------------------------------------------------------- #
def codes(issues):
    return {(item["code"], item["severity"]) for item in issues}


def test_valid_cif_has_no_issues(tmp_path):
    assert sq.validate_cif(write_cif(tmp_path, "NaCl_225_(cod_1)-0")) == []


def test_occupancy_reject_and_warning(tmp_path):
    bad = sq.validate_cif(write_cif(tmp_path, "bad", occ="1.3"))
    assert ("occupancy_sum_gt_1", "reject") in codes(bad)
    message = next(i["message"] for i in bad if i["code"] == "occupancy_sum_gt_1")
    assert message == "Site occupancies sum to 1.30 on at least one site (> 1.10)."
    mild = sq.validate_cif(write_cif(tmp_path, "mild", occ="1.05"))
    assert ("occupancy_sum_gt_1", "warning") in codes(mild)
    assert not any(i["severity"] == "reject" for i in mild)


def test_zero_lattice_rejected(tmp_path):
    issues = sq.validate_cif(write_cif(tmp_path, "zero", a="0"))
    assert ("negative_or_zero_lattice", "reject") in codes(issues)


def test_unphysical_density_rejected(tmp_path):
    text = """data_big
_symmetry_space_group_name_H-M 'P 1'
_cell_length_a 1000
_cell_length_b 1000
_cell_length_c 1000
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
H1 H 0 0 0 1
"""
    issues = sq.validate_cif(write_cif(tmp_path, "big", text=text))
    assert ("unphysical_density", "reject") in codes(issues)


def test_dummy_species_rejected(tmp_path):
    issues = sq.validate_cif(write_cif(tmp_path, "dummy", cl="X"))
    assert ("dummy_species", "reject") in codes(issues)


def test_unparseable_file_is_only_a_warning(tmp_path):
    path = tmp_path / "junk.cif"
    path.write_text("this is not a cif")
    assert codes(sq.validate_cif(path)) == {("parse_error", "warning")}


def test_elements_outside_system_warning(tmp_path):
    path = write_cif(tmp_path, "NaCl_225")
    assert sq.validate_cif(path, allowed_elements=["Na", "Cl", "O"]) == []
    issues = sq.validate_cif(path, allowed_elements=["Na"])
    assert codes(issues) == {("elements_outside_system", "warning")}
    assert "Cl" in issues[0]["message"]


def test_too_many_sites_warning(tmp_path, monkeypatch):
    path = write_cif(tmp_path, "cell")
    monkeypatch.setattr(sq, "_MAX_SITES", 4)
    assert ("too_many_sites", "warning") in codes(sq.validate_cif(path))


def test_screen_rejects_and_keeps(tmp_path, monkeypatch):
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    good = write_cif(tmp_path, "NaCl_225_(cod_1)-0")
    bad = write_cif(tmp_path, "NaCl_225_(mp_mp-9)-0", occ="1.3")
    warn = write_cif(tmp_path, "NaCl_225_(icsd_2)-0", occ="1.05")
    kept, rejected, warnings = sq.screen_candidate_cifs([good, bad, warn])
    assert kept == [good, warn]
    assert [r["file"] for r in rejected] == [bad.name]
    assert rejected[0]["source"] == "mp"
    assert rejected[0]["reasons"][0]["code"] == "occupancy_sum_gt_1"
    assert [w["file"] for w in warnings] == [warn.name]


def test_screen_protects_user_cifs(tmp_path, monkeypatch):
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    good = write_cif(tmp_path, "NaCl_225_(cod_1)-0")
    user = write_cif(tmp_path, "mine", occ="1.3")
    kept, rejected, warnings = sq.screen_candidate_cifs([good, user], protected_stems={"mine"})
    assert kept == [good, user] and rejected == []
    assert warnings[0]["file"] == user.name
    assert warnings[0]["reasons"][0]["severity"] == "warning"


def test_screen_keeps_all_when_everything_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    a = write_cif(tmp_path, "a_1_(mp_mp-1)-0", occ="1.3")
    b = write_cif(tmp_path, "b_1_(mp_mp-2)-0", a="0")
    kept, rejected, warnings = sq.screen_candidate_cifs([a, b])
    assert kept == [a, b] and rejected == []
    assert len(warnings) == 2
    assert all(r["severity"] == "warning" for w in warnings for r in w["reasons"])


def test_screen_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DARA_CIF_VALIDATION", "off")
    bad = write_cif(tmp_path, "bad", occ="1.3")
    assert sq.screen_candidate_cifs([bad]) == ([bad], [], [])


def test_screen_caps_warnings(tmp_path, monkeypatch):
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    paths = [tmp_path / f"w{i}.cif" for i in range(105)]
    monkeypatch.setattr(
        sq, "validate_cif",
        lambda path, allowed_elements=None: [
            {"code": "occupancy_sum_gt_1", "severity": "warning", "message": "x"}
        ],
    )
    kept, rejected, warnings = sq.screen_candidate_cifs(paths)
    assert len(kept) == 105 and len(warnings) == 100


def test_computed_only_phases(tmp_path):
    paths = [
        tmp_path / "Fe2O3_167_(mp_mp-19770)-0.cif",
        tmp_path / "Fe2O3_167_(cod_1011163)-0.cif",
        tmp_path / "NiO_225_(mp_mp-19009)-0.cif",
        tmp_path / "NiO_225_(mp_mp-1)-3.cif",
        tmp_path / "TiO2_136_(cod_5)-0.cif",
        tmp_path / "custom.cif",
    ]
    for path in paths:
        path.write_text("")
    assert sq.computed_only_phases(paths) == [
        {"formula": "NiO", "spacegroup": 225, "file": "NiO_225_(mp_mp-1)-3"}
    ]


# --------------------------------------------------------------------------- #
# Job-level hooks (search and refinement are replaced by fakes)
# --------------------------------------------------------------------------- #
class _FakeLst:
    def __init__(self, rwp):
        self.rwp = rwp


class _FakeRefinement:
    refinement_metrics = None

    def __init__(self, rwp):
        self.lst_data = _FakeLst(rwp)


class _FakeSearchResult:
    def __init__(self, phases, rwp=5.0):
        self.phases = tuple((phase,) for phase in phases)
        self.foms = tuple((1.0,) for _ in phases)
        self.lattice_strains = tuple((0.0,) for _ in phases)
        self.refinement_result = _FakeRefinement(rwp)
        self.missing_peaks = None
        self.extra_peaks = None
        self.grouped_phases = [[(p.path.stem.split("_")[0], [p.path])] for p in phases]

    def model_copy(self, deep=False, update=None):
        clone = _FakeSearchResult([group[0] for group in self.phases])
        clone.__dict__.update(self.__dict__)
        clone.__dict__.update(update or {})
        return clone


class _FakeDatabase:
    name = "fake"

    def __init__(self, files):
        self.files = files

    def get_cifs_by_chemsys(self, elements, e_hull_filter=None, copy_files=True, dest_dir=None):
        for name, text in self.files.items():
            (Path(dest_dir) / f"{name}.cif").write_text(text)

    def get_cifs_by_formulas(self, *args, **kwargs):
        return None

    def as_dict(self):
        return {"fake": "database"}


class _FakePattern:
    def as_dict(self):
        return {"fake": "pattern"}

    def to_xy_file(self, path):
        Path(path).write_text("10 1\n20 2\n")


def _salt(element, a):
    return NACL.format(a=a, occ="1", cl="Cl").replace("Na", element).replace("5.64", str(a))


def _run_job(monkeypatch, tmp_path):
    from dara import jobs

    monkeypatch.chdir(tmp_path)
    files = {
        "NaCl_225_(cod_5)-0": _salt("Na", 5.64),
        "KCl_225_(mp_mp-23193)-0": _salt("K", 6.29),
        "Bad_1_(mp_mp-9)-0": NACL.format(a="5.64", occ="1.3", cl="Cl"),
    }
    captured = {}

    def fake_search(pattern_path, phases, **kwargs):
        by_stem = {RefinementPhase.make(p).path.stem: RefinementPhase.make(p) for p in phases}
        captured["searched"] = sorted(by_stem)
        return [_FakeSearchResult([by_stem["KCl_225_(mp_mp-23193)-0"], by_stem["NaCl_225_(cod_5)-0"]])]

    def fake_refinement(pattern_path, phases, working_dir, phase_params, **kwargs):
        captured["phases"] = list(phases)
        captured["params"] = phase_params
        return _FakeRefinement(4.0)

    monkeypatch.setattr(jobs, "search_phases", fake_search)
    monkeypatch.setattr(jobs, "do_refinement", fake_refinement)
    monkeypatch.setattr(jobs, "PhaseSearchDocument", lambda **kwargs: type("Doc", (), kwargs)())
    job = jobs.PhaseSearchMaker(name="fake", phase_predictor=None).make(
        xrd_data=_FakePattern(), cif_dbs=[_FakeDatabase(files)], analysis_mode="elemental",
        elements=["Cl", "K", "Na"],
    )
    # Call the undecorated method the way jobflow does, without the output schema.
    function = types.MethodType(job.function.original, job.function.__self__)
    document = function(*job.function_args, **job.function_kwargs)
    return document, captured


def test_job_hooks_screen_and_widen_mp_phases(monkeypatch, tmp_path):
    monkeypatch.delenv("DARA_MP_LATTICE_WINDOW", raising=False)
    monkeypatch.delenv("DARA_CIF_VALIDATION", raising=False)
    document, captured = _run_job(monkeypatch, tmp_path)
    summary = document.candidate_summary
    assert [item["file"] for item in summary["rejected"]] == ["Bad_1_(mp_mp-9)-0.cif"]
    assert "Bad_1_(mp_mp-9)-0" not in captured["searched"]
    assert summary["computed_only_phases"] == [
        {"formula": "KCl", "spacegroup": 225, "file": "KCl_225_(mp_mp-23193)-0"}
    ]
    params = {phase.path.stem: phase.params for phase in captured["phases"]}
    assert params["KCl_225_(mp_mp-23193)-0"] == {"lattice_range": (-0.045, 0.02)}
    assert params["NaCl_225_(cod_5)-0"] == {}
    assert summary["mp_lattice_window"] == {
        "enabled": True, "range": [-0.045, 0.02], "applied_to": ["KCl_225_(mp_mp-23193)-0"],
    }
    assert captured["params"]["lattice_range"] == 0.02  # the global range is unchanged


def test_job_hooks_respect_kill_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("DARA_MP_LATTICE_WINDOW", "off")
    monkeypatch.setenv("DARA_CIF_VALIDATION", "off")
    document, captured = _run_job(monkeypatch, tmp_path)
    summary = document.candidate_summary
    assert summary["rejected"] == [] and summary["validation_warnings"] == []
    assert "Bad_1_(mp_mp-9)-0" in captured["searched"]
    assert all(phase.params == {} for phase in captured["phases"])
    assert summary["mp_lattice_window"] == {"enabled": False, "range": None, "applied_to": []}
