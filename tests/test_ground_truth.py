"""Ground-truth files, models and the xlsx converter."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dara.benchmark import ground_truth as gtmod
from dara.benchmark.ground_truth import GTSet, load_ground_truth, validate_ground_truth

ROOT = gtmod.repo_root()
DATA_DIR = Path(r"C:\Users\young\Desktop\KRICT\DARA\data")


@pytest.fixture(scope="module")
def pairwise():
    return load_ground_truth("pairwise")


@pytest.fixture(scope="module")
def precursor():
    return load_ground_truth("precursor")


def test_sets_load_and_validate(pairwise, precursor):
    assert isinstance(pairwise, GTSet) and isinstance(precursor, GTSet)
    assert len(pairwise.samples) == 20
    assert len(precursor.samples) == 40
    assert validate_ground_truth(pairwise, ROOT) == []
    assert validate_ground_truth(precursor, ROOT) == []


def test_precursor_weights_are_normalised_masses(precursor):
    sample = next(s for s in precursor.samples if s.id.startswith("In2O3_0p3022-La(OH)3"))
    weights = {p.formula: p.wt_percent for p in sample.phases}
    assert weights["In2O3"] == pytest.approx(30.10, abs=0.01)
    assert weights["La(OH)3"] == pytest.approx(49.94, abs=0.01)
    assert weights["TiO2"] == pytest.approx(19.96, abs=0.01)
    assert {s.scan for s in precursor.samples} == {"2min", "8min"}
    assert precursor.aliases["Li4CO5"].formula == "Li2CO3"
    bi = next(s for s in precursor.samples if "Bi2O3" in s.id and "Li2CO3" in s.id)
    assert bi.allowed_extra == ["Bi2CO5"]


def test_pairwise_curation_decisions(pairwise):
    samples = {s.id: s for s in pairwise.samples}
    zn = {p.formula: p for p in samples["CoO-ZnO_1100C_60min"].phases}
    assert zn["ZnCo2O4"].accept_any_of == ["Co3O4"]
    nb = {p.formula: p for p in samples["Bi2O3-Nb2O5_500C_60min"].phases}
    assert nb["Nb2O5"].spacegroup is None and nb["Nb2O5"].accept_any_of
    assert all(s.submission.analysis_mode == "elemental" for s in pairwise.samples)
    assert samples["CoO-ZnO_1100C_60min"].reference_fit.sample_displacement_mm is not None


def test_validation_detects_problems(pairwise):
    broken = pairwise.model_copy(deep=True)
    broken.samples[0].phases[0].wt_percent += 5
    broken.samples[0].phases[0].formula = "NotAFormula!!"
    broken.samples[1].phases[0].spacegroup = 300
    broken.samples[1].pattern = "dataset/missing.xrdml"
    broken.samples[2].id = broken.samples[3].id
    problems = "\n".join(validate_ground_truth(broken, ROOT))
    assert "wt% sums" in problems
    assert "formula does not parse" in problems
    assert "invalid space group 300" in problems
    assert "pattern file not found" in problems
    assert "duplicate sample id" in problems


def test_models_forbid_unknown_fields(pairwise):
    payload = json.loads((ROOT / "dataset" / "ground_truth" / "pairwise_reactions.json").read_text("utf-8"))
    payload["samples"][0]["surprise"] = 1
    with pytest.raises(ValidationError):
        GTSet.model_validate(payload)


def test_unknown_set_name():
    with pytest.raises(ValueError):
        load_ground_truth("nope")


@pytest.mark.skipif(not DATA_DIR.is_dir(), reason="source spreadsheets are not available")
def test_converter_check_passes():
    spec = importlib.util.spec_from_file_location("convert_benchmark_xlsx", ROOT / "scripts" / "convert_benchmark_xlsx.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--data-dir", str(DATA_DIR), "--check"]) == 0
