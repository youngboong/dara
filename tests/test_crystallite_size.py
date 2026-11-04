"""Crystallite size: screening windows must not leak, and the API must say why a size is missing."""

from pathlib import Path
from types import SimpleNamespace

from dara.refine import RefinementPhase
from dara.search.tree import SearchTree
from dara.server.api_router import (
    _b1_spec_resolver,
    _phase_refinement_details,
    crystallite_size_status,
)

SCREENING = {"gewicht": "0_0", "lattice_range": 0.01, "k1": "0_0^0.01", "k2": "fixed",
             "b1": "0_0^0.005", "rp": 4}


def _screening_result(stem, k1, b1):
    phase = SimpleNamespace(k1=k1, B1=b1)
    return SimpleNamespace(lst_data=SimpleNamespace(phases_results={stem: phase}, rwp=10.0))


def test_tree_warm_start_does_not_mutate_the_callers_phases(monkeypatch):
    shared = {"lattice_range": 0.05}  # e.g. additional_cif_params shared by uploaded CIFs
    uploaded = RefinementPhase(path=Path("/x/user.cif"), params=shared)
    library = RefinementPhase(path=Path("/x/lib.cif"))
    tree = object.__new__(SearchTree)
    tree.pinned_phases = []
    tree.cif_paths = [uploaded, library]
    tree.refinement_params = {"eps1": 0, "eps2": 0}
    tree.phase_params = dict(SCREENING)
    results = {
        uploaded: _screening_result("user", 0.004, (0.003, 0.0001)),
        library: _screening_result("lib", None, 0.005),
    }
    monkeypatch.setattr(tree, "_screen_phases", lambda phases: dict(results), raising=False)

    cleaned = tree._get_all_cleaned_phases_result()

    # the caller's objects (and the shared params dict) are untouched ...
    assert uploaded.params == {"lattice_range": 0.05} and shared == {"lattice_range": 0.05}
    assert library.params == {}
    # ... while the search itself still gets the warm starts inside the screening windows
    warm = {phase.path.stem: phase.params for phase in cleaned}
    assert warm["user"] == {"lattice_range": 0.05, "k1": "0.004000_0.0^0.01",
                            "b1": "0.003000_0.0^0.005"}
    assert warm["lib"]["k1"] == "0.000000_0.0^0.01"
    assert warm["lib"]["b1"] == "0.005000_0.0^0.005"
    assert set(cleaned) == {uploaded, library}


# --------------------------------------------------------------------------- API status
def _details(model_extra, raw_lst="", b1_spec=None, name="P"):
    phase = SimpleNamespace(
        gewicht=(1.0, 0.1), a=None, b=None, c=None, alpha=None, beta=None, gamma=None,
        xray_density=5.0, rphase=3.0, model_extra=model_extra,
    )
    lst = SimpleNamespace(phases_results={name: phase}, raw_lst=raw_lst)
    refinement = SimpleNamespace(lst_data=lst, plot_data=SimpleNamespace(x=[10.0]))
    return _phase_refinement_details(refinement, b1_spec)[name]


TASK9_LST = """Rietveld refinement to file(s) pattern.xy
BGMN version 4.2.23, 8301 measured points, 78 peaks, 54 parameters

Local parameters and GOALs for phase P
SpacegroupNo=225
UNIT=NM
k1=0.0100000
B1=0.00422+-0.00011
GrainSize(1,1,1)=99.8+-2.6
Atomic positions for phase P
---------------------------------------------------------
  4     0.0000  0.0000  0.0000     E=(CA+2(1.0000))

"""


def test_old_document_without_grainsize_key_falls_back_to_raw_lst():
    # documents parsed before 2026-09-08 (task 9) kept B1 but dropped GrainSize(1,1,1)
    details = _details({"B1": [0.00422, 0.00011], "k1": 0.01}, raw_lst=TASK9_LST)
    assert details["grain_sizes"] == [
        {"direction": "GrainSize(1,1,1)", "value": 99.8, "esd": 2.6, "unit": "nm"}
    ]
    assert details["crystallite_size_status"] == "refined"


def test_fallback_ignores_a_listing_without_the_phase():
    details = _details({"B1": [0.00422, 0.00011]}, raw_lst="no sections here")
    assert details["grain_sizes"] == []


def test_zero_b1_is_not_resolvable():
    details = _details({"k1": None, "B1": 0, "GrainSize(1,1,1)": None},
                       b1_spec=lambda name: "0_0^0.05")
    assert details["grain_sizes"] == []
    assert details["crystallite_size_status"] == "not_resolvable"


def test_screening_bound_signature_is_stale():
    # pre-fix final refinements: B1 pinned at the screening bound 0.005, start-value size
    extra = {"k1": 0.01, "B1": 0.005, "GrainSize(1,1,1)": 84.1811}
    details = _details(extra, b1_spec=lambda name: "0_0^0.05")
    assert details["grain_sizes"] == []
    assert details["crystallite_size_status"] == "stale"
    assert crystallite_size_status(extra, []) == "stale"  # no spec known


def test_upper_bound_of_the_final_window_is_at_limit():
    extra = {"B1": 0.05, "GrainSize(1,1,1)": 8.4}
    assert crystallite_size_status(extra, [], "0_0^0.05") == "at_limit"


def test_instrument_only_peak_width_is_not_refined():
    extra = {"k1": 0, "B1": 0, "GrainSize(1,1,1)": None}
    assert crystallite_size_status(extra, [], "fixed") == "not_refined"
    assert crystallite_size_status(extra, [], "0.01_0.01^0.01") == "not_refined"
    assert crystallite_size_status({}, []) == "not_refined"


def test_b1_spec_resolver_follows_the_jobs_recipe():
    document = SimpleNamespace(final_refinement_params={"b1": "0_0^0.05"})
    assert _b1_spec_resolver({}, document)("lib") == "0_0^0.05"
    job = {"job": {"function_kwargs": {"final_refinement_overrides": {"b1": "fixed"}}}}
    assert _b1_spec_resolver(job, document)("lib") == "fixed"
