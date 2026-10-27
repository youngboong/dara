"""Phase search module."""

from __future__ import annotations

import copy
import time
from collections import deque
from traceback import print_exc
from typing import TYPE_CHECKING, Callable, Literal

import ray

from dara.search.data_model import PeakMatchingStrategy
from dara.search.tree import BaseSearchTree, SearchTree
from dara.settings import DaraSettings

if TYPE_CHECKING:
    from pathlib import Path

    from dara.refine import RefinementPhase
    from dara.search.data_model import SearchResult

_DARA_SETTINGS = DaraSettings()
BGMN_N_THREADS = _DARA_SETTINGS.BGMN_N_THREADS
RAY_NUM_CPUS = _DARA_SETTINGS.RAY_NUM_CPUS

DEFAULT_PHASE_PARAMS = {
    "gewicht": "0_0",
    "lattice_range": 0.01,
    "k1": "0_0^0.01",
    "k2": "fixed",
    "b1": "0_0^0.005",
    "rp": 4,
}
DEFAULT_REFINEMENT_PARAMS = {"n_threads": BGMN_N_THREADS, "eps1": 0, "eps2": "0_-0.05^0.05"}
DEFAULT_PEAK_MATCHING_STRATEGY = PeakMatchingStrategy.default()


@ray.remote(num_cpus=0)
def _remote_expand_node(search_tree: BaseSearchTree) -> BaseSearchTree:
    """Expand a node in the search tree."""
    try:
        search_tree.expand_root()
        return search_tree
    except Exception as e:
        print_exc()
        raise e


def remote_expand_node(search_tree: SearchTree, nid: str) -> ray.ObjectRef:
    """Expand a node in the search tree."""
    subtree = BaseSearchTree.from_search_tree(root_nid=nid, search_tree=search_tree)
    return _remote_expand_node.remote(subtree)


def run_expansion_loop(
    search_tree: SearchTree,
    *,
    max_worker: float,
    submit: Callable,
    wait: Callable,
    get: Callable,
    search_deadline: float | None = None,
    now: Callable[[], float] = time.monotonic,
) -> dict:
    """Expand the search tree breadth-first with a bounded number of parallel tasks.

    With ``search_deadline`` (a ``time.monotonic`` value) no further node is
    submitted once it has passed: queued nodes are marked ``"stopped"`` so that
    they are reported as leaves, and in-flight expansions are awaited. The check
    runs in every iteration, so children produced by expansions that finish
    later are marked as well. Without a deadline this is the plain legacy loop.

    Returns ``{"stopped": bool, "unexpanded_nodes": int}``.
    """
    pending = [submit(search_tree, search_tree.root)]
    to_be_submitted = deque()
    unexpanded = 0

    while pending:
        done, pending = wait(pending)

        for task in done:
            remote_search_tree = get(task)
            remote_search_tree = copy.deepcopy(remote_search_tree)
            search_tree.add_subtree(
                anchor_nid=remote_search_tree.root, search_tree=remote_search_tree
            )
            for nid in search_tree.get_expandable_children(remote_search_tree.root):
                to_be_submitted.append(nid)

        if search_deadline is not None and now() >= search_deadline:
            while to_be_submitted:
                search_tree.get_node(to_be_submitted.popleft()).data.status = "stopped"
                unexpanded += 1

        while len(pending) < max_worker and to_be_submitted:
            nid = to_be_submitted.popleft()
            pending.append(submit(search_tree, nid))

    return {"stopped": unexpanded > 0, "unexpanded_nodes": unexpanded}


def search_phases(
    pattern_path: Path | str,
    phases: list[Path | str | RefinementPhase],
    pinned_phases: list[Path | str | RefinementPhase] | None = None,
    max_phases: int = 5,
    wavelength: Literal["Cu", "Co", "Cr", "Fe", "Mo"] | float = "Cu",
    instrument_profile: str | Path = "Aeris-fds-Pixcel1d-Medipix3",
    express_mode: bool = True,
    enable_angular_cut: bool = True,
    phase_params: dict[str, ...] | None = None,
    refinement_params: dict[str, ...] | None = None,
    return_search_tree: bool = False,
    record_peak_matcher_scores: bool = False,
    rpb_threshold: float | None = None,
    peak_matching_strategy: PeakMatchingStrategy
    | tuple[float, float, float, float] = DEFAULT_PEAK_MATCHING_STRATEGY,
    screening_timeouts: dict | None = None,
    screening_deadline: float | None = None,
    screening_priority: dict[str, float] | None = None,
    search_deadline: float | None = None,
    expansion_bgmn_timeout: float | None = None,
    search_report: dict | None = None,
) -> list[SearchResult] | SearchTree:
    """
    Search for the best phases to use for refinement.

    Args:
        pattern_path: the path to the pattern file. It has to be in `.xrdml`, `.xy`, `.raw`, or `.rasx` format
        phases: the paths to the CIF files
        pinned_phases: the paths to the pinned phases, which will be included in all the results
        max_phases: the maximum number of phases to refine
        wavelength: the wavelength of the X-ray. It can be either a float or one of the following strings:
            "Cu", "Co", "Cr", "Fe", "Mo", indicating the material of the X-ray source
        instrument_profile: the name of the instrument, or the path to the instrument configuration file (.geq)
        express_mode: whether to use express mode. In express mode, the phases will be grouped first before
            searching, which can significantly speed up the search process.
        enable_angular_cut: whether to enable angular cut, which will run the search on a reduced pattern range
            (wmin, wmax) to speed up the search process.
        phase_params: the parameters for the phase search
        refinement_params: the parameters for the refinement
        return_search_tree: whether to return the search tree. This is mainly used for debugging purposes.
        record_peak_matcher_scores: whether to record the peak matcher scores. This is mainly used for
            debugging purposes.
        rpb_threshold: the RPB threshold. If None, it will be automatically determined based on the pattern's SNR.
        peak_matching_strategy: the coefficients for peak matching score calculation. Can be a
            PeakMatchingStrategy model or a tuple of four floats
            (matched_coeff, wrong_intensity_coeff, missing_coeff, extra_coeff).
            If None, the default coefficients will be used.
        screening_timeouts: per-candidate BGMN time limits for the screening step,
            ``{"default", "very_heavy", "source"}``; None keeps the legacy 1200 s.
        screening_deadline: ``time.monotonic()`` value after which unfinished screening
            tasks are cancelled (once at least one candidate has been screened).
        screening_priority: stem -> pre-filter score, used to order the screening
            submissions when a deadline is active.
        search_deadline: ``time.monotonic()`` value after which no new tree node is expanded.
        expansion_bgmn_timeout: BGMN time limit (seconds) inside tree expansions.
        search_report: dict that is filled with ``"screening"`` and ``"search"`` summaries.
    """
    if not isinstance(peak_matching_strategy, PeakMatchingStrategy):
        peak_matching_strategy = PeakMatchingStrategy.from_tuple(peak_matching_strategy)

    if phase_params is None:
        phase_params = {}

    if refinement_params is None:
        refinement_params = {}

    if not ray.is_initialized():
        ray.init(runtime_env={"working_dir": None}, num_cpus=RAY_NUM_CPUS)

    phase_params = {**DEFAULT_PHASE_PARAMS, **phase_params}
    refinement_params = {**DEFAULT_REFINEMENT_PARAMS, **refinement_params}

    # build the search tree
    search_tree = SearchTree(
        pattern_path=pattern_path,
        cif_paths=phases,
        pinned_phases=pinned_phases,
        refine_params=refinement_params,
        phase_params=phase_params,
        wavelength=wavelength,
        instrument_profile=instrument_profile,
        express_mode=express_mode,
        enable_angular_cut=enable_angular_cut,
        max_phases=max_phases,
        rpb_threshold=rpb_threshold,
        record_peak_matcher_scores=record_peak_matcher_scores,
        peak_matching_strategy=peak_matching_strategy,
        screening_timeouts=screening_timeouts,
        screening_deadline=screening_deadline,
        screening_priority=screening_priority,
        expansion_bgmn_timeout=expansion_bgmn_timeout,
    )

    max_worker = ray.cluster_resources()["CPU"]
    loop_info = run_expansion_loop(
        search_tree,
        max_worker=max_worker,
        submit=remote_expand_node,
        wait=lambda pending: ray.wait(pending, timeout=0.5),
        get=ray.get,
        search_deadline=search_deadline,
    )
    if search_report is not None:
        search_report.update(
            {"screening": search_tree.screening_report, "search": loop_info}
        )

    if not return_search_tree:
        return search_tree.get_search_results()
    return search_tree
