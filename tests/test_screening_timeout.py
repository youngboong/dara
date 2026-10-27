"""Tests for the BGMN timeout plumbing, the screening scheduler and the stoppable search loop."""

from __future__ import annotations

from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace

import numpy as np
import pytest
from treelib import Node

from dara import bgmn_worker, refine
from dara.cif2str import CIF2StrError
from dara.refine import RefinementPhase
from dara.search import tree as tree_module
from dara.search.core import run_expansion_loop
from dara.search.data_model import SearchNodeData
from dara.search.tree import BaseSearchTree, SearchTree, run_screening


def _load_make_result():
    """Reuse the synthetic RefinementResult builder of the frozen search-tree tests."""
    import importlib.util

    path = Path(__file__).with_name("test_search_tree.py")
    spec = importlib.util.spec_from_file_location("_search_tree_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._make_result


_make_result = _load_make_result()

LEGACY = {"default": 1200.0, "very_heavy": 1200.0, "source": "legacy"}


# --------------------------------------------------------------------------- #
# timeout plumbing
# --------------------------------------------------------------------------- #
def _worker():
    worker = object.__new__(bgmn_worker.BGMNWorker)
    worker.bgmn_path = Path("bgmn")
    return worker


def test_run_refinement_cmd_default_timeout(monkeypatch, tmp_path):
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bgmn_worker, "run", fake_run)
    _worker().run_refinement_cmd(tmp_path / "x.sav")
    assert seen["timeout"] == 1200
    assert bgmn_worker.DEFAULT_BGMN_TIMEOUT_S == 1200
    _worker().run_refinement_cmd(tmp_path / "x.sav", timeout=75)
    assert seen["timeout"] == 75.0


class _Sentinel(Exception):
    pass


def test_do_refinement_forwards_bgmn_timeout(monkeypatch, tmp_path):
    seen = {}

    class FakeWorker:
        def run_refinement_cmd(self, control_file, show_progress=False, timeout=None):
            seen["timeout"] = timeout
            raise _Sentinel

    monkeypatch.setattr(refine, "BGMNWorker", FakeWorker)
    monkeypatch.setattr(refine, "cif2str", lambda path, *a, **k: tmp_path / "phase.str")
    monkeypatch.setattr(refine, "generate_control_file", lambda **kwargs: tmp_path / "x.sav")
    pattern = tmp_path / "p.xy"
    pattern.write_text("10 1\n")
    for timeout in (None, 42):
        with pytest.raises(_Sentinel):
            refine.do_refinement(pattern, [tmp_path / "A.cif"], working_dir=tmp_path / "w", bgmn_timeout=timeout)
        assert seen["timeout"] == timeout


def test_do_refinement_no_saving_forwards_bgmn_timeout(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(refine, "do_refinement", lambda **kwargs: seen.update(kwargs) or "result")
    assert refine.do_refinement_no_saving(tmp_path / "p.xy", [], bgmn_timeout=33) == "result"
    assert seen["bgmn_timeout"] == 33
    refine.do_refinement_no_saving(tmp_path / "p.xy", [])
    assert seen["bgmn_timeout"] is None


# --------------------------------------------------------------------------- #
# remote_screen_refinement
# --------------------------------------------------------------------------- #
def _call_screen(monkeypatch, behaviour):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return behaviour()

    monkeypatch.setattr(tree_module, "do_refinement_no_saving", fake)
    function = tree_module.remote_screen_refinement._function
    out = function(Path("p.xy"), [Path("A.cif")], "Cu", "profile", {}, {}, bgmn_timeout=77)
    return out, seen


def test_remote_screen_ok_and_rpb_100(monkeypatch):
    good = SimpleNamespace(lst_data=SimpleNamespace(rpb=5.0))
    (result, outcome, duration, reason), seen = _call_screen(monkeypatch, lambda: good)
    assert (result, outcome, reason) == (good, "ok", None)
    assert duration >= 0 and seen["bgmn_timeout"] == 77
    bad = SimpleNamespace(lst_data=SimpleNamespace(rpb=100))
    (result, outcome, _, reason), _ = _call_screen(monkeypatch, lambda: bad)
    assert (result, outcome, reason) == (None, "error", "rpb_100")


def _raise(exc):
    def inner(*args, **kwargs):
        raise exc

    return inner


def test_remote_screen_maps_tolerated_exceptions(monkeypatch):
    (result, outcome, _, reason), _ = _call_screen(monkeypatch, _raise(TimeoutExpired("bgmn", 77)))
    assert result is None and outcome == "timeout" and reason.startswith("TimeoutExpired")
    for exc, name in ((RuntimeError("boom"), "RuntimeError"), (ValueError("bad"), "ValueError"),
                      (CIF2StrError("cif"), "CIF2StrError")):
        (result, outcome, _, reason), _ = _call_screen(monkeypatch, _raise(exc))
        assert result is None and outcome == "error" and reason.startswith(name)


def test_remote_screen_propagates_unexpected_exceptions(monkeypatch):
    monkeypatch.setattr(tree_module, "do_refinement_no_saving", _raise(KeyError("unexpected")))
    with pytest.raises(KeyError):
        tree_module.remote_screen_refinement._function(Path("p.xy"), [Path("A.cif")], "Cu", "p", {}, {})


def test_remote_screen_empty_phase_list():
    out = tree_module.remote_screen_refinement._function(Path("p.xy"), [], "Cu", "p", {}, {})
    assert out[0] is None and out[1] == "error"


def test_remote_do_refinement_keeps_legacy_call_without_timeout(monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(lst_data=SimpleNamespace(rpb=1.0))

    monkeypatch.setattr(tree_module, "do_refinement_no_saving", fake)
    function = tree_module.remote_do_refinement_no_saving._function
    function(Path("p.xy"), [Path("A.cif")], "Cu", "p", {}, {})
    assert "bgmn_timeout" not in seen
    function(Path("p.xy"), [Path("A.cif")], "Cu", "p", {}, {}, bgmn_timeout=9)
    assert seen["bgmn_timeout"] == 9


# --------------------------------------------------------------------------- #
# run_screening
# --------------------------------------------------------------------------- #
class Harness:
    """Fake submit/wait/get/cancel with a scripted completion order and clock."""

    def __init__(self, names, script, outcomes=None, clock_after_first_wait=None, boom=None):
        self.phases = [RefinementPhase.make(Path(f"/fake/{name}.cif")) for name in names]
        self.names = list(names)
        self.script = [list(batch) for batch in script]
        self.outcomes = outcomes or {}
        self.submitted: list[tuple[str, float]] = []
        self.cancelled: list[str] = []
        self.refs: dict[int, str] = {}
        self.by_name: dict[str, int] = {}
        self.clock = 0.0
        self.clock_after_first_wait = clock_after_first_wait
        self.waits = 0
        self.boom = boom
        self.progress_calls: list[tuple] = []

    def make_args(self, phase, timeout):
        return (phase.path.stem, timeout)

    def submit(self, args, timeout):
        ref = len(self.refs)
        self.refs[ref] = args[0]
        self.by_name[args[0]] = ref
        self.submitted.append((args[0], timeout))
        return ref

    def wait(self, pending):
        self.waits += 1
        if self.clock_after_first_wait is not None:
            self.clock = self.clock_after_first_wait
        batch = self.script.pop(0) if self.script else []
        ready = [self.by_name[name] for name in batch if self.by_name[name] in pending]
        return ready, [ref for ref in pending if ref not in ready]

    def get(self, ref):
        name = self.refs[ref]
        if name == self.boom:
            raise KeyError(name)
        outcome = self.outcomes.get(name, ("ok", 10.0, None))
        result = f"result:{name}" if outcome[0] == "ok" else None
        return result, outcome[0], outcome[1], outcome[2]

    def cancel(self, ref):
        self.cancelled.append(self.refs[ref])

    def run(self, **overrides):
        kwargs = {
            "timeouts": LEGACY,
            "weights": {},
            "priority": None,
            "deadline": None,
        }
        kwargs.update(overrides)
        return run_screening(
            self.phases, self.make_args, submit=self.submit, wait=self.wait, get=self.get,
            cancel=self.cancel, now=lambda: self.clock,
            progress=lambda *a: self.progress_calls.append(a), **kwargs,
        )


def test_outcome_mapping_report_and_progress():
    names = ["A", "B", "C", "D"]
    outcomes = {
        "B": ("timeout", 300.0, "TimeoutExpired: x"),
        "C": ("error", 5.0, "RuntimeError: bad"),
        "D": ("error", 2.0, "rpb_100"),
    }
    harness = Harness(names, [["C"], ["A", "D"], ["B"]], outcomes)
    weights = {p: (n, label) for p, n, label in zip(harness.phases, [100, 2500, None, 500], ["light", "very_heavy", "unknown", "heavy"])}
    results, report = harness.run(weights=weights)
    assert list(results) == harness.phases
    assert [results[p] for p in harness.phases] == ["result:A", None, None, None]
    assert (report["total"], report["screened"], report["ok"]) == (4, 4, 1)
    assert (report["timed_out"], report["failed"], report["not_screened_deadline"]) == (1, 2, 0)
    assert report["order"] == "heaviest_first"
    assert report["weights"] == {"light": 1, "heavy": 1, "very_heavy": 1, "unknown": 1}
    assert report["timed_out_candidates"] == [
        {"file": "B.cif", "n_reflections_est": 2500, "weight": "very_heavy", "timeout_s": 1200.0}
    ]
    assert {item["file"]: item["reason"] for item in report["failed_candidates"]} == {
        "C.cif": "RuntimeError: bad", "D.cif": "rpb_100",
    }
    assert report["duration_s"]["max"] == 300.0
    assert report["duration_s"]["total_cpu"] == pytest.approx(10 + 300 + 5 + 2)
    assert report["timeout_source"] == "legacy"
    assert harness.progress_calls == [
        ("Screening reference phases", 1, 4), ("Screening reference phases", 3, 4),
        ("Screening reference phases", 4, 4),
    ]


def test_submission_order_heaviest_first_and_unknown_last():
    names = ["light", "heavy", "unknown", "huge"]
    harness = Harness(names, [names])
    weights = dict(zip(harness.phases, [(100, "light"), (900, "heavy"), (None, "unknown"), (2000, "very_heavy")]))
    harness.run(weights=weights)
    assert [name for name, _ in harness.submitted] == ["huge", "heavy", "light", "unknown"]


def test_priority_order_needs_a_deadline():
    names = ["a", "b", "c"]
    priority = {"a": 0.1, "b": 0.9}
    weights_for = lambda h: dict(zip(h.phases, [(900, "heavy"), (100, "light"), (2000, "very_heavy")]))  # noqa: E731
    no_deadline = Harness(names, [names])
    _, report = no_deadline.run(weights=weights_for(no_deadline), priority=priority)
    assert [n for n, _ in no_deadline.submitted] == ["c", "a", "b"]
    assert report["order"] == "heaviest_first"
    with_deadline = Harness(names, [names])
    _, report = with_deadline.run(weights=weights_for(with_deadline), priority=priority, deadline=10_000.0)
    assert [n for n, _ in with_deadline.submitted] == ["b", "a", "c"]  # unknown priority last
    assert report["order"] == "priority"


def test_result_dict_order_equals_input_order_whatever_the_completion_order():
    names = ["a", "b", "c", "d"]
    harness = Harness(names, [["d"], ["b", "c"], ["a"]])
    results, _ = harness.run()
    assert list(results) == harness.phases


def test_timeouts_legacy_and_very_heavy():
    harness = Harness(["a", "b"], [["a", "b"]])
    weights = dict(zip(harness.phases, [(100, "light"), (3000, "very_heavy")]))
    _, report = harness.run(weights=weights)
    assert dict(harness.submitted) == {"a": 1200.0, "b": 1200.0}
    assert report["timeout_s"] == {"default": 1200.0, "very_heavy": 1200.0}
    harness = Harness(["a", "b"], [["a", "b"]])
    weights = dict(zip(harness.phases, [(100, "light"), (3000, "very_heavy")]))
    harness.run(weights=weights, timeouts={"default": 300.0, "very_heavy": 600.0, "source": "submission"})
    assert dict(harness.submitted) == {"a": 300.0, "b": 600.0}


def test_timeout_is_capped_by_the_remaining_budget_but_not_below_60():
    harness = Harness(["a"], [["a"]])
    harness.run(deadline=100.0)
    assert harness.submitted == [("a", 100.0)]
    harness = Harness(["a"], [["a"]])
    harness.clock = 90.0
    harness.run(deadline=100.0)
    assert harness.submitted == [("a", 60.0)]


def test_deadline_with_a_result_cancels_pending():
    harness = Harness(["a", "b", "c"], [["a"], ["b"]], clock_after_first_wait=500.0)
    results, report = harness.run(deadline=100.0)
    assert harness.cancelled == ["b", "c"]
    assert report["not_screened_deadline"] == 2
    assert report["not_screened_candidates"] == ["b.cif", "c.cif"]
    assert report["screened"] == 1
    assert [results[p] for p in harness.phases] == ["result:a", None, None]
    assert harness.waits == 1


def test_deadline_without_any_ok_result_keeps_waiting():
    outcomes = {"a": ("timeout", 60.0, "TimeoutExpired"), "b": ("error", 1.0, "rpb_100")}
    harness = Harness(["a", "b", "c"], [["a"], ["b"], ["c"]], outcomes, clock_after_first_wait=500.0)
    results, report = harness.run(deadline=100.0)
    assert harness.cancelled == []
    assert report["not_screened_deadline"] == 0
    assert report["ok"] == 1 and harness.waits == 3  # only c succeeded, the loop never stopped early


def test_unexpected_exception_cancels_pending_and_reraises():
    harness = Harness(["a", "b", "c", "d"], [["a", "b"]], boom="b")
    with pytest.raises(KeyError):
        harness.run()
    assert sorted(harness.cancelled) == ["c", "d"]


def test_empty_phase_list():
    harness = Harness([], [])
    results, report = harness.run()
    assert results == {} and report["total"] == 0 and report["duration_s"]["median"] is None


# --------------------------------------------------------------------------- #
# search loop
# --------------------------------------------------------------------------- #
class FakeNodeData:
    def __init__(self):
        self.status = "pending"


class FakeTree:
    """Expansion of node N yields the children given in ``children``."""

    def __init__(self, children):
        self.root = "root"
        self.children = children
        self.nodes = {name: SimpleNamespace(data=FakeNodeData()) for name in ["root", *[c for cs in children.values() for c in cs]]}
        self.added = []

    def get_node(self, nid):
        return self.nodes[nid]

    def add_subtree(self, anchor_nid, search_tree):
        self.added.append(anchor_nid)
        self.nodes[anchor_nid].data.status = "expanded"

    def get_expandable_children(self, nid):
        return list(self.children.get(nid, []))


def _loop(children, deadline, clock, max_worker=1):
    tree = FakeTree(children)
    submitted = []
    in_flight = []

    def submit(search_tree, nid):
        submitted.append(nid)
        in_flight.append(nid)
        return nid

    def wait(pending):
        done = [in_flight.pop(0)] if in_flight else []
        clock["t"] += 10.0
        return done, [p for p in pending if p not in done]

    def get(task):
        return SimpleNamespace(root=task)

    info = run_expansion_loop(
        tree, max_worker=max_worker, submit=submit, wait=wait, get=get,
        search_deadline=deadline, now=lambda: clock["t"],
    )
    return tree, submitted, info


def test_expansion_loop_without_deadline_expands_everything(monkeypatch):
    monkeypatch.setattr("dara.search.core.copy.deepcopy", lambda obj: obj)
    children = {"root": ["a", "b"], "a": ["c"]}
    tree, submitted, info = _loop(children, None, {"t": 0.0})
    assert sorted(submitted) == ["a", "b", "c", "root"]
    assert info == {"stopped": False, "unexpanded_nodes": 0}


def test_expansion_loop_stops_submitting_after_deadline(monkeypatch):
    monkeypatch.setattr("dara.search.core.copy.deepcopy", lambda obj: obj)
    children = {"root": ["a", "b", "c"], "a": ["d"]}
    tree, submitted, info = _loop(children, 5.0, {"t": 0.0})
    assert submitted == ["root"]  # deadline passed right after the root expansion
    assert info == {"stopped": True, "unexpanded_nodes": 3}
    assert [tree.nodes[n].data.status for n in ("a", "b", "c")] == ["stopped"] * 3


def test_expansion_loop_marks_children_of_inflight_expansions(monkeypatch):
    monkeypatch.setattr("dara.search.core.copy.deepcopy", lambda obj: obj)
    children = {"root": ["a", "b"], "a": ["d", "e"]}
    # one slot: root, then a (submitted before the deadline), whose children arrive after it
    clock = {"t": 0.0}
    tree, submitted, info = _loop(children, 15.0, clock, max_worker=2)
    assert "a" in submitted and "d" not in submitted and "e" not in submitted
    assert tree.nodes["d"].data.status == tree.nodes["e"].data.status == "stopped"
    assert info["stopped"] is True and info["unexpanded_nodes"] >= 2


# --------------------------------------------------------------------------- #
# stopped nodes are valid result leaves
# --------------------------------------------------------------------------- #
def test_get_search_results_reports_stopped_leaf():
    phase = RefinementPhase.make(Path("/fake/cifs/A.cif"))
    tree = BaseSearchTree(
        pattern_path=Path("/fake/p.xy"), all_phases_result={}, peak_obs=np.zeros((1, 2)),
        refine_params={}, phase_params={}, intensity_threshold=0.0, wavelength="Cu",
        instrument_profile="fake", express_mode=False, maximum_grouping_distance=0.1,
        max_phases=5, rpb_threshold=0.0, pinned_phases=[], bgmn_timeout=123,
    )
    assert tree.bgmn_timeout == 123
    tree.add_node(Node(identifier="root", data=SearchNodeData(
        current_result=None, current_phases=[], status="expanded")))
    tree.add_node(
        Node(identifier="leaf", data=SearchNodeData(
            current_result=_make_result(rwp=12.0), current_phases=[phase], status="stopped",
            group_id=0, fom=1.0)),
        parent="root",
    )
    tree.phases_grouped = {}
    results = SearchTree.get_search_results(tree)
    assert len(results) == 1
    assert results[0].phases[-1][0] == phase
    clone = tree._clone()
    assert clone.bgmn_timeout == 123
    assert BaseSearchTree.from_search_tree("root", tree).bgmn_timeout == 123
