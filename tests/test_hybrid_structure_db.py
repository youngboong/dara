import asyncio
import inspect
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pymatgen.core import Element, Lattice, Structure
from starlette.datastructures import UploadFile

from dara import structure_db
from dara.cif import Cif
from dara.jobs import _distinct_phase_combinations
from dara.server import api_router
from dara.server.api_router import _parse_string_list, _safe_download_name, _search_result_key
from dara.prediction.rn import _stringify_mapping_keys
from dara.result import RefinementMetrics
from dara.structure_db import MaterialsProjectDatabase, deduplicate_cif_paths


def make_structure(scale=1.0):
    return Structure(Lattice.cubic(4.2 * scale), ["In", "In", "O", "O", "O"], [
        [0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25],
        [0.75, 0.25, 0.25], [0.25, 0.75, 0.25],
    ])


def test_pattern_inspection_returns_xrdml_profile_suggestion():
    path = (
        Path(__file__).parents[1]
        / "dataset"
        / "binary_reactions"
        / "NH4H2PO4-Na2CO3_200C_60min.xrdml"
    )
    upload = UploadFile(filename=path.name, file=BytesIO(path.read_bytes()))

    result = asyncio.run(api_router.inspect_pattern(upload))

    assert result["format"] == "xrdml"
    assert result["recommended_wavelength"] == "Cu"
    assert result["profile_suggestion"]["profile"] == "Aeris-fds-Pixcel1d-Medipix3"
    assert result["two_theta_min"] < result["two_theta_max"]
    assert result["filename_chemistry"]["elements"] == ["N", "H", "P", "O", "Na", "C"]


def test_mp_document_dedup_prefers_experimental_low_energy():
    database = MaterialsProjectDatabase()
    structure = make_structure()
    docs = [
        {"material_id": "mp-2", "structure": structure.copy(), "theoretical": True, "energy_above_hull": 0.0},
        {"material_id": "mp-1", "structure": structure.copy(), "theoretical": False, "energy_above_hull": 0.01},
    ]

    result = database._deduplicate_documents(docs)

    assert [doc["material_id"] for doc in result] == ["mp-1"]


def test_mp_query_requires_environment_key(monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="MP_API_KEY"):
        MaterialsProjectDatabase()._query(formulas=["In2O3"])


def test_cod_download_retries_three_times(monkeypatch):
    attempts = []
    delays = []

    def fail_download(*args, **kwargs):
        attempts.append((args, kwargs))
        raise ConnectionError("temporary COD connection reset")

    monkeypatch.setattr(structure_db.requests, "get", fail_download)
    monkeypatch.setattr(structure_db.time, "sleep", delays.append)

    with pytest.raises(ConnectionError, match="temporary COD connection reset"):
        structure_db.CODDatabase._download_cod("9009987")

    assert len(attempts) == 3
    assert delays == [0.5, 1.0]


def test_serialized_peak_markers_are_restored_as_arrays():
    metrics = RefinementMetrics(rwp=12.3, missing_peaks=[[25.1, 100.0]])

    assert isinstance(metrics.missing_peaks, np.ndarray)
    assert metrics.missing_peaks.shape == (1, 2)


def test_reaction_entry_metadata_keys_are_json_safe():
    cleaned = _stringify_mapping_keys({Element("Ti"): {Element("O"): 1}})

    assert cleaned == {"Ti": {"O": 1}}


def test_cross_database_dedup_prefers_user_then_cod(tmp_path):
    structure = make_structure()
    user_path = tmp_path / "trusted.cif"
    cod_path = tmp_path / "In2O3_206_(cod_1)-0.cif"
    mp_path = tmp_path / "In2O3_206_(mp_mp-1)-0.cif"
    for path in (user_path, cod_path, mp_path):
        Cif.from_structure(structure, filename=path.stem).to_file(path)

    paths, summary = deduplicate_cif_paths([mp_path, cod_path, user_path])

    assert paths == [user_path]
    assert summary["before"] == 3
    assert summary["after"] == 1
    assert summary["removed_duplicates"] == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('["In", "La", "Ti", "O"]', ["In", "La", "Ti", "O"]),
        ("In, La, Ti, O", ["In", "La", "Ti", "O"]),
        ("", []),
    ],
)
def test_parse_string_list(value, expected):
    assert _parse_string_list(value) == expected


def test_elemental_submission_contract(monkeypatch):
    pattern_path = Path(__file__).parent / "test_data" / "BiFeO3.xy"
    upload = UploadFile(filename=pattern_path.name, file=BytesIO(pattern_path.read_bytes()))
    monkeypatch.setattr(api_router, "add_job_to_queue", lambda job, user: 77)

    result = asyncio.run(
        api_router.submit(
            pattern_file=upload,
            user="test",
            precursor_formulas='["BiFeO3"]',
            elements='["Bi", "Fe", "O"]',
            analysis_mode="elemental",
            instrument_profile="Aeris-fds-Pixcel1d-Medipix3",
            wavelength="Cu",
            temperature=-1,
            atmosphere="air",
            use_cod=True,
            use_mp=False,
            mp_e_hull_cutoff=0.05,
            max_mp_structures=100,
            use_rxn_predictor=None,
            additional_phases=None,
        )
    )

    assert result == {"message": "submitted", "wf_id": 77}


def test_web_mp_limit_defaults_to_100():
    assert inspect.signature(api_router.submit).parameters["max_mp_structures"].default == 100


def test_download_filename_is_sanitized():
    assert _safe_download_name("Fe2O3 + TiO2 / result.xrdml") == "result"


def test_search_result_key_ignores_order_but_preserves_distinct_references():
    first = [
        "In2O3_206_(cod_2310009)-0",
        "TiO2_141_(cod_7206075)-0",
    ]
    reordered_with_other_record = [
        "TiO2_141_(cod_7206075)-0",
        "In2O3_206_(cod_1538147)-0",
    ]

    assert _search_result_key(first) != _search_result_key(reordered_with_other_record)
    assert _search_result_key(first) == _search_result_key(list(reversed(first)))
    assert _search_result_key(first) != _search_result_key(
        ["TiO2_141_(cod_7206075)-0", "In2O3_199_(cod_1010341)-None"]
    )


def test_distinct_phase_combinations_preserves_unverified_references():
    def phase(name):
        return SimpleNamespace(path=Path(f"{name}.cif"))

    caf2 = phase("CaF2_225_(cod_2300449)-0")
    calcite = phase("CaCO3_167_(cod_1547347)-0")
    calcite_duplicate = phase("CaCO3_167_(cod_9016703)-None")
    alternative = phase("CaCO3_14_(cod_2106394)-1")
    result = SimpleNamespace(
        phases=((caf2,), (calcite, calcite_duplicate, alternative)),
        foms=((0.9,), (0.8, 0.7, 0.6)),
        lattice_strains=((0.0,), (0.0, 0.0, 0.0)),
        refinement_result=SimpleNamespace(lst_data=SimpleNamespace(rwp=10.52)),
    )

    hypotheses = _distinct_phase_combinations([result], limit=5)
    identities = [
        tuple(sorted(item[0].path.stem.split("_(", 1)[0] for item in combination))
        for _, combination in hypotheses
    ]

    assert identities == [
        ("CaCO3_167", "CaF2_225"),
        ("CaCO3_167", "CaF2_225"),
        ("CaCO3_14", "CaF2_225"),
    ]


def test_distinct_phase_combinations_merge_only_structure_matched_cifs(tmp_path):
    first_path = tmp_path / "In2O3_206_(cod_1)-0.cif"
    second_path = tmp_path / "In2O3_206_(mp_2)-0.cif"
    structure = make_structure()
    Cif.from_structure(structure, filename=first_path.stem).to_file(first_path)
    Cif.from_structure(structure.copy(), filename=second_path.stem).to_file(second_path)

    def phase(path):
        return SimpleNamespace(path=path)

    result = SimpleNamespace(
        phases=((phase(first_path), phase(second_path)),),
        foms=((0.9, 0.8),),
        lattice_strains=((0.0, 0.0),),
        refinement_result=SimpleNamespace(lst_data=SimpleNamespace(rwp=5.0)),
    )

    hypotheses = _distinct_phase_combinations([result], limit=5)

    assert len(hypotheses) == 1
    assert hypotheses[0][1][0][0].path == first_path


class MemoryStore:
    def __init__(self, documents):
        self.documents = documents

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def query_one(self, criteria):
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in criteria.items())
            ),
            None,
        )

    def remove_docs(self, criteria):
        self.documents[:] = [
            document
            for document in self.documents
            if not all(document.get(key) == value for key, value in criteria.items())
        ]


def test_delete_result_removes_job_and_document(monkeypatch):
    jobs = MemoryStore([{"index": 7, "uuid": "abc", "status": "COMPLETED"}])
    results = MemoryStore([{"uuid": "abc", "output": {}}])
    monkeypatch.setattr(api_router, "get_worker_store", lambda: jobs)
    monkeypatch.setattr(api_router, "get_result_store", lambda: results)

    response = asyncio.run(api_router.delete_result(7))

    assert response == {"message": "deleted", "task_id": 7}
    assert jobs.documents == []
    assert results.documents == []


@pytest.mark.parametrize("status", ["READY", "RUNNING"])
def test_delete_result_refuses_active_tasks(monkeypatch, status):
    jobs = MemoryStore([{"index": 7, "uuid": "abc", "status": status}])
    monkeypatch.setattr(api_router, "get_worker_store", lambda: jobs)

    with pytest.raises(api_router.HTTPException, match="cannot be deleted"):
        asyncio.run(api_router.delete_result(7))
