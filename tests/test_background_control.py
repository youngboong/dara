"""Optional native-BGMN background degree (``RU``) and the unchanged default control file."""

import shutil
from pathlib import Path

import pytest

from dara import refine
from dara.generate_control_file import generate_control_file

DATA = Path(__file__).parent / "test_data"
INSTRUMENT = "Aeris-fds-Pixcel1d-Medipix3"

# Captured from the code BEFORE ``background_degree`` existed (n_threads fixed to 1).
GOLDEN_DEFAULT = (
    "% Theoretical instrumental function\nVERZERR=Aeris-fds-Pixcel1d-Medipix3.geq\n% Wavelength\nLAMBDA=CU\n"
    "% Phases\nSTRUC[1]=BiFeO3.str\nSTRUC[2]=Bi2Fe4O9.str\n% Measured data\nVAL[1]=BiFeO3.xy\n"
    "% Result list output\nLIST=BiFeO3.lst\n% Peak list output\nOUTPUT=BiFeO3.par\n% Diagram output\n"
    "DIAGRAMM=BiFeO3.dia\n% Global parameters for zero point and sample displacement\nEPS1=0.0\n"
    "PARAM[1]=EPS2=0_-0.05^0.05\nNTHREADS=1\nPROTOKOLL=Y\nsum=BiFeO3+Bi2Fe4O9\nQBiFeO3=BiFeO3/sum\n"
    "QBi2Fe4O9=Bi2Fe4O9/sum\nGOAL[1]=QBiFeO3\nGOAL[2]=QBi2Fe4O9\n"
)
GOLDEN_RANGES = (
    "% Theoretical instrumental function\nVERZERR=Aeris-fds-Pixcel1d-Medipix3.geq\n% Wavelength\nLAMBDA=CO\n"
    "WMIN=10\nWMAX=80\nCUT[1]=24:31\n% Phases\nSTRUC[1]=BiFeO3.str\n% Measured data\nVAL[1]=BiFeO3.xy\n"
    "% Result list output\nLIST=BiFeO3.lst\n% Peak list output\nOUTPUT=BiFeO3.par\n% Diagram output\n"
    "DIAGRAMM=BiFeO3.dia\n% Global parameters for zero point and sample displacement\nEPS1=0.0\n"
    "PARAM[1]=EPS2=0_-0.05^0.05\nNTHREADS=1\nPROTOKOLL=Y\nsum=BiFeO3\nQBiFeO3=BiFeO3/sum\nGOAL[1]=QBiFeO3\n"
)


@pytest.fixture
def workdir(tmp_path):
    for name in ("BiFeO3.str", "Bi2Fe4O9.str", "BiFeO3.xy"):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path


def _control(workdir, **kwargs):
    strs = kwargs.pop("strs", ["BiFeO3.str", "Bi2Fe4O9.str"])
    return generate_control_file(
        workdir / "BiFeO3.xy", [workdir / name for name in strs], INSTRUMENT, workdir,
        n_threads=1, **kwargs,
    ).read_text()


def test_default_control_file_is_byte_identical(workdir):
    assert _control(workdir) == GOLDEN_DEFAULT
    assert _control(workdir, strs=["BiFeO3.str"], wmin=10, wmax=80,
                    excluded_ranges=[[24, 31]], wavelength="Co") == GOLDEN_RANGES


def test_background_degree_adds_exactly_one_line(workdir):
    text = _control(workdir, background_degree=8)
    assert text.splitlines().count("RU=8") == 1
    assert text.replace("RU=8\n", "") == GOLDEN_DEFAULT
    lines = text.splitlines()
    assert lines[lines.index("RU=8") + 1] == "% Phases"


def test_background_degree_follows_cut_lines(workdir):
    text = _control(workdir, strs=["BiFeO3.str"], wmin=10, wmax=80,
                    excluded_ranges=[[24, 31]], wavelength="Co", background_degree=5)
    assert "CUT[1]=24:31\nRU=5\n% Phases" in text


@pytest.mark.parametrize("value", [1, 21, 0, -3, 2.5, "8", True])
def test_background_degree_rejects_out_of_range(workdir, value):
    with pytest.raises(ValueError):
        _control(workdir, background_degree=value)


@pytest.mark.parametrize("value", [2, 20])
def test_background_degree_bounds_are_inclusive(workdir, value):
    assert f"RU={value}\n" in _control(workdir, background_degree=value)


def test_do_refinement_passes_background_degree_through_refinement_params(monkeypatch, workdir):
    captured = {}
    real = refine.generate_control_file

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    class StopHere(Exception):
        pass

    class FakeWorker:
        def run_refinement_cmd(self, control, **kwargs):
            captured["control_text"] = Path(control).read_text()
            raise StopHere

    monkeypatch.setattr(refine, "generate_control_file", spy)
    monkeypatch.setattr(refine, "BGMNWorker", FakeWorker)
    with pytest.raises(StopHere):
        refine.do_refinement(
            workdir / "BiFeO3.xy", [workdir / "BiFeO3.str"], working_dir=workdir / "run",
            instrument_profile=INSTRUMENT, refinement_params={"background_degree": 8},
        )
    assert captured["background_degree"] == 8
    assert "RU=8\n" in captured["control_text"]


def test_do_refinement_omits_cancel_kwarg_by_default(monkeypatch, workdir):
    calls = []

    class FakeWorker:
        def run_refinement_cmd(self, control, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("stop")

    monkeypatch.setattr(refine, "BGMNWorker", FakeWorker)
    with pytest.raises(RuntimeError):
        refine.do_refinement(workdir / "BiFeO3.xy", [workdir / "BiFeO3.str"],
                             working_dir=workdir / "run", instrument_profile=INSTRUMENT)
    assert calls and "cancel_event" not in calls[0]
