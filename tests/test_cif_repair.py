"""Tests for the CIF consistency check and automatic repair (duplicate atoms)."""

import shutil
import warnings
from pathlib import Path

import pytest
from pymatgen.core import Composition, Structure

from dara import structure_quality as sq

DATA = Path(__file__).parent / "test_data"
FIXTURES = DATA / "cif_repair"
COD_NAME = "Co2WO4_13_(cod_5910316)-0.cif"


def stage(tmp_path: Path, source: Path, name: str) -> Path:
    path = tmp_path / name
    shutil.copy(source, path)
    return path


def load(path: Path) -> Structure:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return Structure.from_file(path.as_posix(), site_tolerance=1e-3, occupancy_tolerance=100)


@pytest.fixture
def cod_wolframite(tmp_path):
    return stage(tmp_path, FIXTURES / "5910316.cif", COD_NAME)


# --------------------------------------------------------------------------- #
# The motivating case: COD 5910316
# --------------------------------------------------------------------------- #
def test_plan_detects_duplicates_and_reconciles_formula(cod_wolframite):
    plan = sq.plan_cif_repair(cod_wolframite)
    assert plan["needed"] and plan["ok"]
    assert set(plan["removed_labels"]) == {"Co2", "W2", "O2", "O3", "O4"}
    assert plan["declared_formula"] == "CoWO4"
    assert plan["repaired_formula"] == "CoWO4"
    assert plan["formula_mismatch"]
    # the plan itself writes nothing
    assert cod_wolframite.exists()


def test_screen_repairs_wolframite(cod_wolframite, tmp_path):
    repaired: list[dict] = []
    kept, rejected, warnings_ = sq.screen_candidate_cifs([cod_wolframite], repaired=repaired)

    assert rejected == []
    assert [p.name for p in kept] == ["CoWO4_13_(cod_5910316)-0.cif"]
    assert not cod_wolframite.exists()  # replaced in place (same directory)
    assert kept[0].parent == tmp_path

    assert len(repaired) == 1
    record = repaired[0]
    assert record["file_before"] == COD_NAME
    assert record["file_after"] == "CoWO4_13_(cod_5910316)-0.cif"
    assert set(record["removed_labels"]) == {"Co2", "W2", "O2", "O3", "O4"}
    assert record["reason"]
    # also visible as a warning so it is never silent
    assert any(
        reason["code"] == "cif_repaired" for item in warnings_ for reason in item["reasons"]
    )

    # what BGMN / naming see now: Co2W2O8 (Z = 2), reduced formula CoWO4
    structure = load(kept[0])
    assert structure.composition == Composition("Co2W2O8")
    assert structure.is_ordered
    assert min(d for row in structure.distance_matrix for d in row if d > 0) > 1.5

    from dara.utils import get_composition_from_filename

    assert get_composition_from_filename(kept[0]).reduced_formula == "CoWO4"

    # the repaired file is clean for the validator and idempotent
    assert sq.validate_cif(kept[0]) == []
    again: list[dict] = []
    kept2, rejected2, warnings2 = sq.screen_candidate_cifs(kept, repaired=again)
    assert kept2 == kept and rejected2 == [] and warnings2 == [] and again == []


def test_repaired_cif_converts_to_bgmn_structure(cod_wolframite):
    from dara.cif2str import cif2str

    kept, _, _ = sq.screen_candidate_cifs([cod_wolframite])
    text = Path(cif2str(kept[0])).read_text()
    assert "FORMULA=CoWO4" in text
    assert text.count("E=CO") == 1 and text.count("E=W ") == 1


# --------------------------------------------------------------------------- #
# Files that must stay untouched
# --------------------------------------------------------------------------- #
def test_clean_cif_is_untouched(tmp_path):
    path = stage(tmp_path, DATA / "BiFeO3.cif", "BiFeO3_161_(cod_1234567)-0.cif")
    before = path.read_bytes()
    plan = sq.plan_cif_repair(path)
    assert plan is not None and not plan["needed"] and not plan["formula_mismatch"]

    repaired: list[dict] = []
    kept, rejected, warnings_ = sq.screen_candidate_cifs([path], repaired=repaired)
    assert kept == [path] and rejected == [] and repaired == []
    assert not any(
        reason["code"] in {"duplicate_atoms", "inconsistent_formula", "cif_repaired"}
        for item in warnings_
        for reason in item["reasons"]
    )
    assert path.read_bytes() == before


def test_partial_occupancy_split_site_is_untouched(tmp_path):
    path = stage(tmp_path, FIXTURES / "split_site_NaCl.cif", "NaCl_1_(cod_7000001)-0.cif")
    before = path.read_bytes()
    plan = sq.plan_cif_repair(path)
    assert not plan["needed"] and not plan["formula_mismatch"]

    repaired: list[dict] = []
    kept, rejected, _ = sq.screen_candidate_cifs([path], repaired=repaired)
    assert kept == [path] and rejected == [] and repaired == []
    assert path.read_bytes() == before


def test_special_position_of_one_label_is_not_a_duplicate(tmp_path):
    # Na sits on the mirror plane x = 0: its two images coincide but are one atom.
    text = """data_mirror
_chemical_formula_sum 'Na Cl2'
_cell_length_a 5.64
_cell_length_b 5.64
_cell_length_c 5.64
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_symmetry_equiv_pos_as_xyz
x,y,z
-x,y,z
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0 0.1 0.2
Cl1 Cl 0.3 0.5 0.5
"""
    path = tmp_path / "NaCl2_6_(cod_7000002)-0.cif"
    path.write_text(text)
    plan = sq.plan_cif_repair(path)
    assert plan is not None and not plan["needed"] and not plan["formula_mismatch"]


def test_switch_off_leaves_everything_alone(cod_wolframite, monkeypatch):
    monkeypatch.setenv("DARA_CIF_VALIDATION", "off")
    before = cod_wolframite.read_bytes()
    repaired: list[dict] = []
    kept, rejected, warnings_ = sq.screen_candidate_cifs([cod_wolframite], repaired=repaired)
    assert kept == [cod_wolframite] and rejected == [] and warnings_ == [] and repaired == []
    assert cod_wolframite.read_bytes() == before


# --------------------------------------------------------------------------- #
# Not repairable / not eligible
# --------------------------------------------------------------------------- #
def test_formula_mismatch_without_duplicates_only_warns(tmp_path):
    path = stage(tmp_path, FIXTURES / "formula_mismatch_NaCl.cif", "NaCl_1_(cod_7000003)-0.cif")
    other = stage(tmp_path, DATA / "BiFeO3.cif", "BiFeO3_161_(cod_1234567)-0.cif")
    plan = sq.plan_cif_repair(path)
    assert not plan["needed"] and plan["formula_mismatch"]

    kept, rejected, warnings_ = sq.screen_candidate_cifs([path, other])
    assert path in kept and rejected == []
    codes = {
        reason["code"]
        for item in warnings_
        if item["file"] == path.name
        for reason in item["reasons"]
    }
    assert "inconsistent_formula" in codes
    assert path.exists()  # never rewritten


def test_unrepairable_duplicates_are_rejected_for_database_cifs(tmp_path):
    bad = stage(tmp_path, FIXTURES / "duplicate_NaCl_unfixable.cif", "NaCl_1_(cod_7000004)-0.cif")
    good = stage(tmp_path, DATA / "BiFeO3.cif", "BiFeO3_161_(cod_1234567)-0.cif")
    before = bad.read_bytes()

    repaired: list[dict] = []
    kept, rejected, _ = sq.screen_candidate_cifs([bad, good], repaired=repaired)
    assert kept == [good] and repaired == []
    assert len(rejected) == 1 and rejected[0]["file"] == bad.name
    assert {r["code"] for r in rejected[0]["reasons"] if r["severity"] == "reject"} == {
        "inconsistent_formula"
    }
    assert bad.read_bytes() == before


def test_reject_code_is_duplicate_atoms_without_formula_mismatch():
    plan = {"declared_formula": None, "formula_mismatch": False, "reason": "overlap"}
    issues = sq._promote_to_reject([sq._issue("duplicate_atoms", "warning", "x")], plan)
    assert [(i["code"], i["severity"]) for i in issues] == [("duplicate_atoms", "reject")]


def test_user_cif_is_only_warned_never_rewritten(cod_wolframite, tmp_path):
    # no database marker in the name -> user supplied
    user = tmp_path / "my_wolframite.cif"
    shutil.copy(cod_wolframite, user)
    other = stage(tmp_path, DATA / "BiFeO3.cif", "BiFeO3_161_(cod_1234567)-0.cif")
    before = user.read_bytes()

    repaired: list[dict] = []
    kept, rejected, warnings_ = sq.screen_candidate_cifs([user, other], repaired=repaired)
    assert user in kept and rejected == [] and repaired == []
    assert user.read_bytes() == before
    codes = {
        reason["code"]
        for item in warnings_
        if item["file"] == user.name
        for reason in item["reasons"]
    }
    assert "duplicate_atoms" in codes


def test_protected_database_named_cif_is_not_rewritten(cod_wolframite):
    before = cod_wolframite.read_bytes()
    repaired: list[dict] = []
    kept, rejected, _ = sq.screen_candidate_cifs(
        [cod_wolframite], protected_stems={cod_wolframite.stem}, repaired=repaired
    )
    assert kept == [cod_wolframite] and rejected == [] and repaired == []
    assert cod_wolframite.read_bytes() == before


def test_repair_without_report_list_still_warns(cod_wolframite):
    kept, _, warnings_ = sq.screen_candidate_cifs([cod_wolframite])
    assert kept[0].name.startswith("CoWO4_13_")
    assert any(r["code"] == "cif_repaired" for item in warnings_ for r in item["reasons"])


def test_order_of_candidates_is_preserved(cod_wolframite, tmp_path):
    a = stage(tmp_path, DATA / "BiFeO3.cif", "BiFeO3_161_(cod_1234567)-0.cif")
    b = stage(tmp_path, DATA / "Bi2Fe4O9.cif", "Bi2Fe4O9_55_(cod_1234568)-0.cif")
    kept, _, _ = sq.screen_candidate_cifs([a, cod_wolframite, b])
    assert [p.name for p in kept] == [
        a.name,
        "CoWO4_13_(cod_5910316)-0.cif",
        b.name,
    ]
