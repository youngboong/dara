"""Submission estimate endpoint, COD/MP counting helpers and the cost-model fallbacks."""

import asyncio
import sys
import time
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from dara import structure_db
from dara.server import api_router
from dara.structure_db import MaterialsProjectDatabase


_REAL_MP_CONFIGURED = api_router._mp_configured


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    api_router._MP_ESTIMATE_CACHE.clear()
    monkeypatch.setenv("MP_API_KEY", "test-key-not-real")
    monkeypatch.setattr(api_router, "_mp_configured", lambda: True)
    yield
    api_router._MP_ESTIMATE_CACHE.clear()


def _cod_rows(count, unique=None):
    unique = unique or count
    return [(f"X{i % unique}O", 1000000 + i, 12, 0.0) for i in range(count)]


def _mp_rows(count, unique=None, volume=100.0):
    unique = unique or count
    return [
        {"material_id": f"mp-{i}", "formula": f"Y{i % unique}O", "spacegroup": 14,
         "volume": volume, "nsites": 4, "e_above_hull": 0.0, "theoretical": False}
        for i in range(count)
    ]


class StubCost:
    """Deterministic stand-in for ``dara.search.candidate_cost``."""

    LIGHT_MAX = 400
    HEAVY_MAX = 1200
    DEFAULT_ASSUMED_REFLECTIONS = 300
    PREFILTER_MIN_CANDIDATES = 40
    PREFILTER_TYPICAL_KEEP_FRACTION = 0.85

    def __init__(self, expected_s=600, reflections=lambda volume: 200):
        self.expected_s = expected_s
        self.reflections = reflections
        self.calls = []

    def reflections_from_cell(self, volume, sg, wavelength, two_theta_max, *, primitive=False):
        assert primitive is True
        return self.reflections(volume)

    def estimate_runtime_s(self, n, reflection_counts, workers, *, data_points=None):
        self.calls.append((n, list(reflection_counts), workers, data_points))
        return {"low_s": self.expected_s / 2, "expected_s": self.expected_s,
                "high_s": self.expected_s * 2, "assumptions": ["stub assumption"]}


@pytest.fixture
def stub(monkeypatch):
    cost = StubCost()
    monkeypatch.setattr(api_router, "_candidate_cost_module", lambda: cost)
    return cost


@pytest.fixture
def databases(monkeypatch):
    state = SimpleNamespace(cod=_cod_rows(75, 62), mp=_mp_rows(99, 85), mp_calls=[], cod_calls=[])

    def cod(elements, precursors, cutoff, *, database=None, formulas_only=False):
        state.cod_calls.append((list(elements), list(precursors), cutoff, formulas_only))
        return state.cod

    def mp(self, elements, e_hull_filter=None, limit=None, timeout_s=15):
        state.mp_calls.append((list(elements), e_hull_filter, limit, timeout_s))
        return state.mp[:limit] if limit else state.mp

    monkeypatch.setattr(structure_db, "cod_candidate_rows", cod)
    monkeypatch.setattr(MaterialsProjectDatabase, "count_by_chemsys", mp)
    return state


def _estimate(**fields):
    if fields.pop("fresh", False):
        api_router._MP_ESTIMATE_CACHE.clear()
    fields.setdefault("elements", ["Bi", "V", "O"])
    return asyncio.run(api_router.estimate_submission(api_router.EstimateRequest(**fields)))


# --- endpoint ---------------------------------------------------------------------

def test_cod_only_when_mp_disabled(databases, stub):
    result = _estimate(use_mp=False)
    assert result["mp_status"] == "disabled"
    assert result["candidates"]["cod"] == 75 and result["candidates"]["mp"] is None
    assert result["candidates"]["total_before_dedup"] == 75
    assert result["reflections"] == {"median": None, "very_heavy": 0, "source": "default"}
    assert databases.mp_calls == []


def test_mp_key_missing_still_counts_cod(databases, stub, monkeypatch):
    monkeypatch.setattr(api_router, "_mp_configured", _REAL_MP_CONFIGURED)
    monkeypatch.delenv("MP_API_KEY", raising=False)
    result = _estimate()
    assert result["mp_status"] == "not_configured"
    assert result["candidates"]["cod"] == 75 and result["candidates"]["mp"] is None
    assert databases.mp_calls == []


def test_counts_dedup_and_reflections(databases, stub):
    result = _estimate()
    candidates = result["candidates"]
    assert (candidates["cod"], candidates["mp"]) == (75, 99)
    assert candidates["mp_capped"] is False  # 99 < 100
    assert candidates["total_before_dedup"] == 174
    assert candidates["expected_after_dedup"] == 148  # round(0.85 * 174)
    assert candidates["prefilter_expected_after"] == 148  # pre-filter off
    assert result["reflections"] == {"median": 200, "very_heavy": 0, "source": "mp"}
    assert result["mp_status"] == "ok"
    assert databases.cod_calls == [(["Bi", "O", "V"], [], 0.05, False)]
    assert databases.mp_calls[0][0] == ["Bi", "O", "V"] and databases.mp_calls[0][2] == 100
    assert "MP precursor-formula references are not counted." in result["assumptions"]
    assert result["assumptions"][0] == "stub assumption"
    assert "Duplicates are estimated, not computed." in result["assumptions"]
    assert result["elapsed_ms"] >= 0


def test_mp_capped_and_very_heavy(databases, stub):
    databases.mp = _mp_rows(50)
    stub.reflections = lambda volume: 1500
    result = _estimate(max_mp_structures=50)
    assert result["candidates"]["mp"] == 50 and result["candidates"]["mp_capped"] is True
    assert result["reflections"]["very_heavy"] == 50 and result["reflections"]["median"] == 1500


def test_dedup_never_below_unique_formula_sg(databases, stub):
    databases.cod, databases.mp = _cod_rows(10, 10), _mp_rows(10, 10)
    assert _estimate()["candidates"]["expected_after_dedup"] == 17  # round(0.85 * 20)
    databases.cod, databases.mp = _cod_rows(30, 30), _mp_rows(2, 2)
    assert _estimate(fresh=True)["candidates"]["expected_after_dedup"] == 30  # unique pairs win


def test_prefilter_expected_after(databases, stub):
    result = _estimate(candidate_prefilter=True)
    assert result["candidates"]["prefilter_expected_after"] == round(148 * 0.85)
    assert stub.calls[-1][0] == round(148 * 0.85)
    databases.cod, databases.mp = _cod_rows(10), _mp_rows(10)
    small = _estimate(candidate_prefilter=True, fresh=True)
    assert small["candidates"]["expected_after_dedup"] == 17
    assert small["candidates"]["prefilter_expected_after"] == 17  # not above the minimum
    databases.cod, databases.mp = _cod_rows(45), []
    near = _estimate(candidate_prefilter=True, fresh=True)
    assert near["candidates"]["expected_after_dedup"] == 45
    assert near["candidates"]["prefilter_expected_after"] == 40  # round(0.85 * 45) = 38 -> floor of 40


def test_slow_mp_times_out_and_is_not_cached(databases, stub, monkeypatch):
    monkeypatch.setattr(api_router, "MP_ESTIMATE_TIMEOUT_S", 0.05)

    def slow(self, *args, **kwargs):
        time.sleep(0.4)
        return databases.mp

    monkeypatch.setattr(MaterialsProjectDatabase, "count_by_chemsys", slow)
    result = _estimate()
    assert result["mp_status"] == "timeout"
    assert result["candidates"]["mp"] is None and result["candidates"]["cod"] == 75
    assert "Materials Project could not be reached; the estimate counts COD only." in result["suggestions"]


def test_mp_error_is_reported_not_raised(databases, stub, monkeypatch):
    def broken(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(MaterialsProjectDatabase, "count_by_chemsys", broken)
    result = _estimate()
    assert result["mp_status"] == "error"
    assert result["candidates"]["mp"] is None
    assert api_router._MP_ESTIMATE_CACHE == {}


def test_second_identical_call_uses_the_cache(databases, stub):
    _estimate()
    _estimate()
    assert len(databases.mp_calls) == 1
    _estimate(max_mp_structures=60)  # different key
    assert len(databases.mp_calls) == 2


def test_cache_expires(databases, stub, monkeypatch):
    _estimate()
    key = next(iter(api_router._MP_ESTIMATE_CACHE))
    stamp, rows = api_router._MP_ESTIMATE_CACHE[key]
    api_router._MP_ESTIMATE_CACHE[key] = (stamp - api_router.MP_ESTIMATE_TTL_S - 1, rows)
    _estimate()
    assert len(databases.mp_calls) == 2


def test_reaction_mode_has_no_counts_and_uses_forty(databases, stub):
    result = _estimate(analysis_mode="reaction", elements=[], precursor_formulas=["Bi2O3", "V2O5"])
    candidates = result["candidates"]
    assert candidates["note"] == "Candidates come from the predicted product set (typically 20-60 structures)."
    assert all(candidates[key] is None for key in ("cod", "mp", "total_before_dedup", "expected_after_dedup"))
    assert stub.calls[-1][0] == 40
    assert databases.cod_calls == [] and databases.mp_calls == []


def test_known_mixture_counts_only_the_formulas(databases, stub):
    result = _estimate(analysis_mode="known_mixture", elements=[], precursor_formulas=["Bi2O3", "BiVO4"])
    assert result["mp_status"] == "disabled"
    assert result["candidates"]["note"] == "Known-phase mode uses only the listed formulas."
    assert databases.cod_calls == [(["Bi", "O", "V"], ["Bi2O3", "BiVO4"], 0.05, True)]
    assert databases.mp_calls == []


@pytest.mark.parametrize(
    "expected_s, level",
    [(100, "ok"), (1800, "ok"), (1801, "long"), (5400, "long"), (5401, "very_long")],
)
def test_level_thresholds(databases, stub, expected_s, level):
    stub.expected_s = expected_s
    result = _estimate()
    assert result["level"] == level
    assert result["runtime_s"]["expected"] == expected_s
    assert result["runtime_s"]["low"] == round(expected_s / 2)
    assert result["workers"] >= 1


def test_suggestions_exact_and_ordered(databases, stub, monkeypatch):
    stub.expected_s = 6000

    def broken(self, *args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(MaterialsProjectDatabase, "count_by_chemsys", broken)
    result = _estimate(elements=["Bi", "V", "O", "Fe", "Li"], max_mp_structures=100)
    assert result["level"] == "very_long"
    assert result["suggestions"] == [
        "Lower 'Maximum MP structures' (for example to 50) to reduce the number of candidates.",
        "Restrict the element list to the elements expected in crystalline phases.",
        "Enable the candidate pre-filter to skip references whose strongest peaks are absent.",
        "Set a maximum analysis time to receive the best result found within that time.",
        "Materials Project could not be reached; the estimate counts COD only.",
    ]


def test_no_suggestions_when_everything_is_set(databases, stub):
    stub.expected_s = 7000
    result = _estimate(candidate_prefilter=True, max_analysis_minutes=60, max_mp_structures=40)
    assert result["suggestions"] == []


def test_validation_errors(databases, stub):
    for fields, detail in [
        ({"elements": ["Xx"]}, "Invalid element symbols"),
        ({"precursor_formulas": ["notaformula!"]}, "Invalid precursor formulas"),
        ({"analysis_mode": "nonsense"}, "Invalid analysis mode"),
        ({"mp_e_hull_cutoff": 2}, "MP E-hull cutoff must be 0 to 1 eV/atom"),
        ({"max_mp_structures": 0}, "MP structure limit must be 1 to 1000"),
        ({"elements": []}, "Elemental mode requires elements"),
    ]:
        with pytest.raises(HTTPException) as info:
            _estimate(**{"elements": ["Bi"], **fields})
        assert info.value.status_code == 400 and info.value.detail == detail


def test_elements_are_derived_from_precursors(databases, stub):
    _estimate(elements=[], precursor_formulas=["BiVO4"])
    assert databases.cod_calls[0][:2] == (["Bi", "O", "V"], ["BiVO4"])


def test_unavailable_cost_model_gives_unknown_level(databases, monkeypatch):
    monkeypatch.setitem(sys.modules, "dara.search.candidate_cost", None)
    result = _estimate()
    assert result["runtime_s"] is None and result["level"] == "unknown"
    assert result["workers"] is None
    assert result["candidates"]["expected_after_dedup"] == 148
    assert result["reflections"]["source"] == "default"
    assert [s for s in result["suggestions"] if "maximum analysis time" in s] == []


def test_real_cost_model_returns_a_range(databases):
    pytest.importorskip("dara.search.candidate_cost")
    result = _estimate(candidate_prefilter=True, wavelength="Cu", two_theta_max=80, data_points=4000)
    run = result["runtime_s"]
    assert run["low"] < run["expected"] < run["high"]
    assert result["reflections"]["source"] == "mp" and result["reflections"]["median"] > 0


# --- structure_db helpers ---------------------------------------------------------

class FakeCOD:
    def __init__(self, info):
        self.preparsed_info = info


COD_INFO = {
    "Bi": [("Bi", 1, 166, 0.0)],
    "O": [("O2", 2, 12, 0.0), ("O", 3, 1, None)],
    "Bi-O": [("Bi2O3", 10, 14, 0.0), ("Bi2O3", 11, 14, 0.04), ("Bi2O3", 12, 14, 0.2), ("BiO", 13, 1, None)],
    "O-V": [("V2O5", 20, 31, 0.0)],
    "Bi-O-V": [("BiVO4", 30, 15, 0.0), ("BiVO4", 31, 88, 0.01)],
}


def test_cod_candidate_rows_filters_like_the_job():
    rows = structure_db.cod_candidate_rows(["Bi", "O"], [], 0.05, database=FakeCOD(COD_INFO))
    ids = [row[1] for row in rows]
    assert ids == [1, 3, 10, 11, 13]  # gas O2 and e_hull 0.2 dropped; None e_hull kept
    assert rows[0] == ("Bi", 1, 166, 0.0)


def test_cod_candidate_rows_subsystems_and_precursor_union_is_unique():
    database = FakeCOD(COD_INFO)
    rows = structure_db.cod_candidate_rows(["Bi", "O", "V"], ["Bi2O3", "BiVO4"], 0.05, database=database)
    ids = [row[1] for row in rows]
    assert len(ids) == len(set(ids))
    assert set(ids) == {1, 3, 10, 11, 13, 20, 30, 31}


def test_cod_candidate_rows_formulas_only():
    database = FakeCOD(COD_INFO)
    rows = structure_db.cod_candidate_rows(["Bi", "O", "V"], ["Bi2O3"], 0.05, database=database,
                                           formulas_only=True)
    assert [row[1] for row in rows] == [10, 11]
    assert structure_db.cod_candidate_rows([], ["Not a formula!"], 0.05, database=database,
                                           formulas_only=True) == []


def test_cod_matches_the_real_file_map_filters():
    database = FakeCOD(COD_INFO)
    expected = structure_db.StructureDatabase._generate_file_map(
        SimpleNamespace(local_copy_found=True, name="cod", get_file_path=lambda code: f"{code}.cif"),
        [row for key in ("Bi", "O", "Bi-O") for row in COD_INFO[key]], 0.05, True,
    )
    rows = structure_db.cod_candidate_rows(["Bi", "O"], [], 0.05, database=database)
    assert len(rows) == len(expected)


def _fake_mp_client(monkeypatch, docs, record):
    class FakeRester:
        def __init__(self, **kwargs):
            record["init"] = kwargs
            def search(**query):
                record["query"] = query
                return docs

            self.materials = SimpleNamespace(summary=SimpleNamespace(search=search))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    client = types.ModuleType("mp_api.client")
    client.MPRester = FakeRester
    monkeypatch.setitem(sys.modules, "mp_api.client", client)
    if "mp_api" not in sys.modules:
        monkeypatch.setitem(sys.modules, "mp_api", types.ModuleType("mp_api"))


def test_count_by_chemsys_query_and_rows(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "test-key-not-real")
    record = {}
    docs = [
        SimpleNamespace(material_id="mp-1", formula_pretty="BiVO4",
                        symmetry=SimpleNamespace(number=15), energy_above_hull=0.0, volume=300.0,
                        nsites=24, theoretical=False),
        SimpleNamespace(material_id="mp-2", formula_pretty="O2",
                        symmetry=SimpleNamespace(number=12), energy_above_hull=0.0, volume=40.0,
                        nsites=2, theoretical=True),
    ]
    _fake_mp_client(monkeypatch, docs, record)
    rows = MaterialsProjectDatabase().count_by_chemsys(["V", "Bi", "O"], 0.07, 25, timeout_s=9)
    query = record["query"]
    assert "structure" not in query["fields"]
    assert query["fields"] == ["material_id", "formula_pretty", "symmetry", "energy_above_hull",
                               "volume", "nsites", "theoretical"]
    assert query["chunk_size"] == 25 and query["num_chunks"] == 1
    assert query["energy_above_hull"] == (0, 0.07)
    assert query["deprecated"] is False and query["_sort_fields"] == "energy_above_hull"
    assert sorted(query["chemsys"]) == sorted(
        ["Bi", "O", "V", "Bi-O", "Bi-V", "O-V", "Bi-O-V"]
    )
    assert record["init"]["timeout"] == 9 and record["init"]["mute_progress_bars"] is True
    assert rows == [{"material_id": "mp-1", "formula": "BiVO4", "spacegroup": 15, "volume": 300.0,
                     "nsites": 24, "e_above_hull": 0.0, "theoretical": False}]  # gas O2 dropped


def test_count_by_chemsys_requires_a_key(monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        MaterialsProjectDatabase().count_by_chemsys(["Bi"])
