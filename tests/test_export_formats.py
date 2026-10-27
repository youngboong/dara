import io
import os
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI

from dara.cif import Cif
from dara.server import export_router, review_router
from dara.xrd import load_pattern

from test_report import ASGIClient

DATA = Path(__file__).parent / "test_data"
PHASE = "BiFeO3_161_(cod_2300449)-0"
PROFILE = "Aeris-fds-Pixcel1d-Medipix3"


def _structure():
    return Cif.from_file(DATA / "BiFeO3.cif").to_structure()


def _document(*, eps2=(-0.001657, 3.3e-05), excluded=None):
    structure = _structure()
    a, b, c = structure.lattice.abc
    phase = SimpleNamespace(
        spacegroup_no=161, hermann_mauguin="R3c", gewicht=(1.0, 0.01),
        a=(a / 10, 0.00001), b=None, c=(c / 10, 0.00002), alpha=None, beta=None, gamma=None,
        xray_density=8.3, rphase=4.0, model_extra={"GrainSize": (60.0, 2.0)}, unit="NM",
    )
    x = np.linspace(10, 80, 400)
    obs = 10 + 100 * np.exp(-0.5 * ((x - 32) / 0.2) ** 2)

    def refinement(rwp):
        return SimpleNamespace(
            lst_data=SimpleNamespace(
                rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
                phases_results={PHASE: phase}, EPS2=eps2,
                raw_lst="Rietveld refinement\nBGMN version 4.2.23, 400 measured points, 5 peaks, 8 parameters\n",
            ),
            plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs, y_bkg=np.full_like(x, 10.0)),
            export_structure=lambda name: structure,
            get_phase_weights=lambda: {PHASE: 1.0},
        )

    refine = {"wmin": 12.0, "wmax": 78.0}
    if excluded:
        refine["excluded_ranges"] = excluded
    final = refinement(6.1)
    return SimpleNamespace(
        final_result=final, best_rwp=6.1, results=[([], refinement(6.5)), ([], final)],
        xrd_data=load_pattern(DATA / "BiFeO3.xy"), task_label="BiFeO3",
        search_kwargs={"instrument_profile": PROFILE, "wavelength": "Cu", "refinement_params": refine},
        final_refinement_params=None, input_cifs=[],
    ), a


def _members(content):
    return set(zipfile.ZipFile(io.BytesIO(content)).namelist())


def _read(content, name):
    return zipfile.ZipFile(io.BytesIO(content)).read(name).decode("utf-8")


@pytest.fixture
def client(monkeypatch):
    document, a = _document(excluded=[[20.0, 22.0]])
    task = {"index": 9, "uuid": "task-export", "status": "COMPLETED", "job": {"name": "BiFeO3.xy"}}

    class Store:
        def __init__(self, kind):
            self.kind = kind

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            if self.kind == "worker":
                return deepcopy(task) if criteria.get("index") == task["index"] else None
            return {"output": document}

    state = {"task": task, "a": a}
    monkeypatch.setattr(review_router, "get_worker_store", lambda: Store("worker"))
    monkeypatch.setattr(review_router, "get_result_store", lambda: Store("result"))
    monkeypatch.setattr(
        review_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda output: output)
    )
    app = FastAPI()
    app.include_router(export_router.router)
    return ASGIClient(app), state


def test_profex_bundle(client):
    http, _ = client
    response = http.get("/api/task/9/export/profex")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert "dara_task_9_profex.zip" in response.headers["Content-Disposition"]
    names = _members(response.content)
    assert {"pattern.xy", "README.txt", "dara_refinement.lst", f"refined_cifs/{PHASE}.cif"} <= names
    assert any(name.endswith(".str") for name in names)
    assert f"{PROFILE}.geq" in names
    sav_name = next(name for name in names if name.endswith(".sav"))
    sav = _read(response.content, sav_name)
    assert f"VERZERR={PROFILE}.geq" in sav
    assert "STRUC[1]=" in sav
    assert "EPS2=-0.001657_-0.05^0.05" in sav
    assert "WMIN=12.0" in sav and "CUT[1]=20:22" in sav
    assert "LAMBDA=CU" in sav
    readme = _read(response.content, "README.txt")
    assert "Profex" in readme and "bgmn pattern.sav" in readme and "6.10%" in readme
    assert "B1 and k1 start at 0" in readme and "SPHAR4" in readme and "not transferred" in readme
    assert "close to" not in readme
    str_text = _read(response.content, next(name for name in names if name.endswith(".str")))
    assert "SpacegroupNo=161" in str_text and "PARAM=A=" in str_text


@pytest.mark.parametrize("fmt", ["gsas2", "highscore"])
def test_cif_bundles(client, fmt):
    http, _ = client
    response = http.get(f"/api/task/9/export/{fmt}")
    assert response.status_code == 200
    names = _members(response.content)
    assert {"pattern.xy", "README.txt", f"refined_cifs/{PHASE}.cif"} <= names
    assert not any(name.endswith((".sav", ".str", ".inp")) for name in names)
    readme = _read(response.content, "README.txt")
    assert "two-column 2theta/intensity" in readme and "instrument" in readme
    assert f"dara_task_9_{fmt}.zip" in response.headers["Content-Disposition"]
    pattern = _read(response.content, "pattern.xy").splitlines()
    assert len(pattern[0].split()) == 2 and "." in pattern[0]


def test_topas_input(client):
    http, state = client
    response = http.get("/api/task/9/export/topas?idx=0")
    assert response.status_code == 200
    assert "dara_task_9_topas_candidate_1.zip" in response.headers["Content-Disposition"]
    names = _members(response.content)
    assert "dara_model.inp" in names and "pattern.xy" in names
    text = _read(response.content, "dara_model.inp")
    assert "' DARA starting model for TOPAS - review before use" in text
    assert 'xdd "pattern.xy"' in text
    assert "CuKa5(0.0001)" in text
    assert "bkg @ 0 0 0 0 0 0" in text
    assert "start_X 12" in text and "finish_X 78" in text and "exclude 20 22" in text
    assert "Zero_Error(@, 0)" in text and "LP_Factor(0)" in text
    assert 'space_group "R3c"' in text
    assert "scale @ 0.0001" in text
    assert "CS_L(@, 60.0)" in text
    structure = _structure()
    a, _, c = structure.lattice.abc
    assert f"a @ {a:.5f}" in text and f"c @ {c:.5f}" in text
    assert "b = Get(a);" in text and "ga 120" in text and "al 90" in text and "be 90" in text
    assert not any(macro in text for macro in ("Hexagonal(", "Cubic(", "Orthorhombic(", "Monoclinic("))
    assert "Rp 145" in text and "Rs 145" in text
    assert text.index("Rp 145") < text.index("Specimen_Displacement(@, -0.2403)")
    assert "site Bi1 x" in text and "occ Bi 1.0000" in text and "beq 0.5" in text
    assert text.count("\n        site ") == 3  # Bi, Fe, O (R3c)
    assert not any(ord(ch) > 127 for ch in text)


def test_topas_non_cu_and_unknown_radius(monkeypatch):
    from dara.export.topas_inp import crystal_system_of, topas_input

    document, _ = _document()
    document.search_kwargs.update(wavelength="Co", instrument_profile="user-abc")
    text = topas_input(document.final_result, document)
    assert "lam ymin_on_ymax 0.0001 la 1 lo 1.789010 lh 0.1" in text
    assert "Specimen_Displacement" not in text and "Rp " not in text
    assert "' Specimen displacement not transferred" in text
    assert "CuKa5" not in text
    document.search_kwargs["wavelength"] = "0.0826"
    assert "lo 0.826000" in topas_input(document.final_result, document)
    assert [crystal_system_of(n) for n in (1, 14, 62, 139, 166, 194, 225)] == [
        "triclinic", "monoclinic", "orthorhombic", "tetragonal", "trigonal", "hexagonal", "cubic",
    ]


def test_unknown_format_and_bad_index(client):
    http, state = client
    assert http.get("/api/task/9/export/excel").status_code == 404
    assert http.get("/api/task/9/export/profex?idx=5").status_code == 404
    assert http.get("/api/task/12345/export/profex").status_code == 404


def test_unfinished_task_is_409(monkeypatch, client):
    http, _ = client
    original = review_router.get_worker_store

    class Running:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            return {"index": 9, "uuid": "x", "status": "RUNNING", "job": {"name": "a"}}

    monkeypatch.setattr(review_router, "get_worker_store", lambda: Running())
    assert http.get("/api/task/9/export/profex").status_code == 409
    monkeypatch.setattr(review_router, "get_worker_store", original)


def test_profex_bundle_without_refined_eps2_uses_default_window():
    import tempfile

    from dara.export.bundles import build_bundle

    document, _ = _document(eps2=None)
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp)
        build_bundle("profex", document.final_result, document, out)
        sav = next(out.glob("*.sav")).read_text()
        assert "EPS2=0_-0.05^0.05" in sav


def test_failed_phase_conversion_is_reported(monkeypatch):
    import tempfile

    from dara.export import bgmn_bundle
    from dara.export.bundles import write_pattern_xy

    document, _ = _document()
    second = SimpleNamespace(**vars(document.final_result.lst_data.phases_results[PHASE]))
    document.final_result.lst_data.phases_results["Broken_1_(cod_1)-0"] = second

    def broken(name):
        if name == PHASE:
            return _structure()
        raise ValueError("no structure")

    document.final_result.export_structure = broken
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp)
        write_pattern_xy(document, out / "pattern.xy")
        created = bgmn_bundle.write_bgmn_bundle(document.final_result, document, out)
        note = out / "phases_not_converted.txt"
        assert note in created and "Broken_1_(cod_1)-0" in note.read_text()
        sav = next(out.glob("*.sav")).read_text()
        assert sav.count("STRUC[") == 1


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("DARA_RUN_SLOW") != "1", reason="real BGMN run (DARA_RUN_SLOW=1)")
def test_profex_bundle_runs_in_real_bgmn(tmp_path):
    from dara.bgmn_worker import BGMNWorker
    from dara.export.bundles import build_bundle
    from dara.result import get_result

    document, _ = _document()
    build_bundle("profex", document.final_result, document, tmp_path)
    control = next(tmp_path.glob("*.sav"))
    BGMNWorker().run_refinement_cmd(control)
    result = get_result(control)
    assert 0 < result.lst_data.rwp < 100


def test_topas_lattice_lines_are_explicit_keywords():
    from pymatgen.core import Lattice, Structure

    from dara.export.topas_inp import _lattice_lines

    def lines(system, lattice):
        return _lattice_lines(system, Structure(lattice, ["Si"], [[0, 0, 0]]))

    assert lines("cubic", Lattice.cubic(5.4)) == [
        "a @ 5.40000", "b = Get(a);", "c = Get(a);", "al 90", "be 90", "ga 90"]
    assert lines("tetragonal", Lattice.tetragonal(4.0, 6.0))[:3] == [
        "a @ 4.00000", "b = Get(a);", "c @ 6.00000"]
    assert lines("orthorhombic", Lattice.orthorhombic(3.0, 4.0, 5.0)) == [
        "a @ 3.00000", "b @ 4.00000", "c @ 5.00000", "al 90", "be 90", "ga 90"]
    assert lines("monoclinic", Lattice.monoclinic(3.0, 4.0, 5.0, 100.0)) == [
        "a @ 3.00000", "b @ 4.00000", "c @ 5.00000", "al 90", "be @ 100.00000", "ga 90"]
    assert lines("triclinic", Lattice.from_parameters(3, 4, 5, 80, 85, 95))[3:] == [
        "al @ 80.00000", "be @ 85.00000", "ga @ 95.00000"]
    rhombohedral = lines("trigonal", Lattice.rhombohedral(5.0, 55.0))
    assert rhombohedral[1:3] == ["b = Get(a);", "c = Get(a);"]
    assert rhombohedral[3:] == ["al @ 55.00000", "be = Get(al);", "ga = Get(al);"]
    # a parameter that violates the declared constraint stays free instead of being tied
    assert lines("cubic", Lattice.from_parameters(5.4, 5.5, 5.4, 90, 90, 90))[1] == "b @ 5.50000"


def test_topas_sites_follow_declared_space_group_not_higher_symmetry():
    from pymatgen.core import Lattice, Structure

    from dara.export.topas_inp import _asymmetric_sites

    # P2/m (10): atoms at (x, 0, z) have multiplicity 2; a symmetry finder reports a higher
    # symmetry for this near-orthorhombic cell, but the declared group must be honoured.
    lattice = Lattice.monoclinic(4.0, 5.0, 6.0, 90.0001)
    structure = Structure.from_spacegroup(
        10, lattice, ["Fe", "O"], [[0.21, 0, 0.33], [0.1, 0.0, 0.7]]
    )
    sites = _asymmetric_sites(structure, 10)
    assert len(sites) == 2
    rebuilt = Structure.from_spacegroup(
        10, lattice, [site.species for site in sites], [site.frac_coords for site in sites]
    )
    assert len(rebuilt) == len(structure)
    # an impossible group cannot reproduce the structure -> ValueError (caller falls back to P1)
    with pytest.raises(ValueError):
        _asymmetric_sites(Structure(Lattice.cubic(4.0), ["Fe", "Fe"], [[0, 0, 0], [0.13, 0.2, 0.31]]), 225)


def test_topas_falls_back_to_p1_when_symmetry_cannot_be_reproduced(monkeypatch):
    from dara.export import topas_inp

    document, _ = _document()
    monkeypatch.setattr(
        topas_inp, "_asymmetric_sites", lambda *args: (_ for _ in ()).throw(ValueError("x"))
    )
    text = topas_inp.topas_input(document.final_result, document)
    assert 'space_group "P1"' in text and "all atoms are listed in P1" in text
    assert text.count("\n        site ") == 30  # every atom of the hexagonal R3c cell
