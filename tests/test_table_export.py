import asyncio
import csv
import io
import zipfile
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from dara.server import api_router
from dara.server.table_export import (
    MULTI_TASK_EXTRA_COLUMNS,
    TABLE_COLUMNS,
    multi_task_rows,
    rows_to_csv,
    task_table_rows,
)


def _result(rwp, phase="CaF2_225_(cod_2300449)-0", with_a_only=True, cautions=()):
    lattice = {"a": {"value": 5.463821, "esd": 0.000078, "unit": "Å"}}
    return {
        "rwp": rwp,
        "fit_quality": {
            "rwp": rwp, "rexp": 2.0, "gof": rwp / 2.0, "chi_squared": (rwp / 2.0) ** 2,
            "durbin_watson": 1.8, "two_theta_min": 10.0, "two_theta_max": 80.0,
        },
        "phase_metadata": {phase: {"spacegroup_number": 225, "spacegroup_symbol": "Fm-3m"}},
        "phase_details": {
            phase: {
                "weight_percent": 100.0, "weight_esd_percent": 0.5,
                "lattice_parameters": lattice,
                "grain_sizes": [{"direction": "GrainSize(1,1,1)", "value": 52.0, "esd": 3.0, "unit": "nm"}],
            }
        },
        "peak_diagnostics": {"counts": {"missing": 1, "extra": 0, "intensity_mismatch": 0}},
        "cautions": [{"code": code} for code in cautions],
        "highlighted_phases": [phase],
    }


def _summary():
    candidates = [_result(5.0, cautions=("poor_fit", "correlated_residuals")), _result(6.0)]
    candidates[0]["source_index"] = 3
    candidates[1]["source_index"] = 7
    return {
        "task_label": "sample.xrdml", "final_result": _result(4.0, cautions=("poor_fit",)),
        "all_results": candidates,
    }


def test_rows_fill_symmetry_and_follow_review_selection():
    rows = task_table_rows(5, {"review": {"selection": 7}}, _summary())
    assert [row["candidate"] for row in rows] == ["final", "1", "2"]
    assert [row["source_index"] for row in rows] == [None, 3, 7]
    assert [row["adopted"] for row in rows] == ["", "", "yes"]
    row = rows[0]
    assert (row["a_angstrom"], row["b_angstrom"], row["c_angstrom"]) == (5.463821, 5.463821, 5.463821)
    assert row["a_esd"] == 0.000078 and row["b_esd"] is None and row["c_esd"] is None
    assert (row["alpha_deg"], row["beta_deg"], row["gamma_deg"]) == (90.0, 90.0, 90.0)
    assert row["crystallite_size_nm"] == 52.0 and row["crystallite_size_esd_nm"] == 3.0
    assert row["missing_peaks"] == 1 and row["cautions"] == "poor_fit"
    assert rows[1]["cautions"] == "poor_fit;correlated_residuals"
    assert "COD 2300449" in row["provenance"]
    assert row["phase_label"].startswith("Fluorite")
    assert "<" not in row["phase_label"]

    best_rows = task_table_rows(5, {"review": {"selection": "best"}}, _summary())
    assert [row["adopted"] for row in best_rows] == ["yes", "", ""]
    assert all(row["adopted"] == "" for row in task_table_rows(5, {}, _summary()))


def test_hexagonal_and_monoclinic_and_rhombohedral_fill():
    from dara.server.table_export import lattice_with_symmetry

    hexagonal = lattice_with_symmetry(
        {"a": {"value": 4.9, "esd": 0.1}, "c": {"value": 17.0, "esd": 0.2}}, 194
    )
    assert hexagonal["b"] == (4.9, None) and hexagonal["gamma"] == (120.0, None)
    assert hexagonal["c"] == (17.0, 0.2)
    mono = lattice_with_symmetry(
        {k: {"value": v, "esd": 0.01} for k, v in dict(a=1, b=2, c=3, beta=101).items()}, 14
    )
    assert mono["alpha"] == (90.0, None) and mono["beta"] == (101, 0.01)
    rhombo = lattice_with_symmetry(
        {"a": {"value": 5.0, "esd": 0.1}, "alpha": {"value": 55.0, "esd": 0.2}}, 166
    )
    assert rhombo["c"] == (5.0, None) and rhombo["gamma"] == (55.0, None)


def test_csv_bytes_bom_header_and_precision():
    rows = task_table_rows(5, {}, _summary())
    data = rows_to_csv(rows, TABLE_COLUMNS)
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed[0] == TABLE_COLUMNS
    assert len(parsed) == 1 + len(rows)
    record = dict(zip(parsed[0], parsed[1]))
    assert record["a_angstrom"] == "5.463821" and record["b_esd"] == ""
    assert record["task_name"] == "sample.xrdml"


def test_csv_neutralises_spreadsheet_formulas():
    rows = [{"task_id": 1, "task_name": "=HYPERLINK(1)", "candidate": "final"}]
    parsed = list(csv.reader(io.StringIO(rows_to_csv(rows, TABLE_COLUMNS).decode("utf-8-sig"))))
    assert parsed[1][1].startswith("'=")


def test_multi_task_rows_include_status_rows():
    entries = [
        (1, {"user": "me", "status": "COMPLETED", "job": {"name": "a.xy"}, "review": {"selection": "best"}}, _summary()),
        (2, {"user": "you", "status": "RUNNING", "job": {"name": "b.xy"}}, None),
        (3, None, None),
    ]
    rows = multi_task_rows(entries)
    assert [row["candidate"] for row in rows] == ["final", "status: RUNNING", "status: NOT_FOUND"]
    assert rows[0]["adopted"] == "yes" and rows[0]["user"] == "me"
    assert rows[1]["task_name"] == "b.xy" and rows[1].get("phase") is None
    data = rows_to_csv(rows, TABLE_COLUMNS + MULTI_TASK_EXTRA_COLUMNS)
    assert data.decode("utf-8-sig").splitlines()[0].endswith("user,submitted_on")


def test_task_id_parser():
    assert api_router._parse_task_ids("3, 1,3,2") == [3, 1, 2]
    for bad in ("1,x", "", "1,,-2", "1.5"):
        with pytest.raises(HTTPException) as caught:
            api_router._parse_task_ids(bad)
        assert caught.value.status_code == 400
    with pytest.raises(HTTPException):
        api_router._parse_task_ids(",".join(str(i) for i in range(1, 52)))
    assert len(api_router._parse_task_ids(",".join(str(i) for i in range(1, 51)))) == 50


class _Store:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria, *args):
        return next((r for r in self.records if r.get("index") == criteria.get("index")), None)


def test_csv_endpoints(monkeypatch):
    records = [
        {"index": 1, "status": "COMPLETED", "uuid": "u1", "job": {"name": "a b.xy"}, "user": "me"},
        {"index": 2, "status": "RUNNING", "uuid": "u2", "job": {"name": "b.xy"}},
    ]
    monkeypatch.setattr(api_router, "get_worker_store", lambda: _Store(records))

    async def fake_result(task_id):
        return _summary()

    monkeypatch.setattr(api_router, "result", fake_result)
    response = asyncio.run(api_router.download_task_table(1))
    assert response.media_type.startswith("text/csv")
    assert 'filename="dara_task_1_a_b_table.csv"' in response.headers["content-disposition"]
    assert response.body.startswith(b"\xef\xbb\xbf")
    for task_id, status in ((2, 409), (99, 404)):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(api_router.download_task_table(task_id))
        assert caught.value.status_code == status

    multi = asyncio.run(api_router.download_tasks_table(ids="1,2,99"))
    lines = multi.body.decode("utf-8-sig").splitlines()
    assert len(lines) == 1 + 1 + 1 + 1
    assert lines[0].endswith("user,submitted_on")
    assert "status: RUNNING" in lines[2] and "status: NOT_FOUND" in lines[3]
    assert multi.headers["content-disposition"].endswith('dara_tasks_table.csv"')
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.download_tasks_table(ids="1,x"))
    assert caught.value.status_code == 400


def _fake_refinement(raw_lst="BGMN listing"):
    phase = SimpleNamespace(model_dump=lambda **kwargs: {"a": 0.5})
    return SimpleNamespace(
        lst_data=SimpleNamespace(raw_lst=raw_lst, phases_results={"CaF2_225_(cod_2300449)-0": phase}),
        plot_data=SimpleNamespace(
            x=np.array([10.0, 20.0]), y_obs=np.array([1.0, 2.0]), y_calc=np.array([1.0, 2.0]),
            y_bkg=np.array([0.0, 0.0]), structs={"CaF2_225_(cod_2300449)-0": np.array([1.0, 1.0])},
        ),
        visualize=lambda: SimpleNamespace(to_html=lambda **kwargs: "<html></html>"),
        refinement_metrics=object(),
        export_structure=lambda name: SimpleNamespace(to=lambda fmt: "data_x"),
    )


def test_archive_contains_lst_and_results_table():
    document = SimpleNamespace(
        xrd_data=SimpleNamespace(
            angles=np.array([10.0, 20.0]), intensities=np.array([1.0, 2.0]), errors=None
        ),
        final_result=_fake_refinement(),
        input_cifs=[],
        results=[([], _fake_refinement("candidate listing"))],
        missing_peaks=None,
        extra_peaks=None,
    )
    summary = _summary()
    summary["all_results"] = summary["all_results"][:1]
    summary["all_results"][0]["source_index"] = 0
    archive = api_router._make_result_archive(5, document, summary, review_selection=0)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        assert {"final/refinement.lst", "search_results/result_1/refinement.lst", "results_table.csv"} <= names
        assert bundle.read("final/refinement.lst") == b"BGMN listing"
        table = bundle.read("results_table.csv")
        assert table.startswith(b"\xef\xbb\xbf")
        rows = list(csv.DictReader(io.StringIO(table.decode("utf-8-sig"))))
        assert [row["candidate"] for row in rows] == ["final", "1"]
        assert [row["adopted"] for row in rows] == ["", "yes"]
        readme = bundle.read("README.txt").decode()
        assert "refinement.lst" in readme and "results_table.csv" in readme

    single = api_router._make_result_archive(5, document, summary, result_index=0)
    with zipfile.ZipFile(single) as bundle:
        assert "refinement.lst" in bundle.namelist()
        assert "results_table.csv" not in bundle.namelist()
