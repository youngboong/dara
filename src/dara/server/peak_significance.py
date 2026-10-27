"""Size and signal-to-noise aware classification of unexplained observed peaks.

BGMN's "missing" list contains every observed feature that the fitted phases do not
reproduce, including noise spikes, duplicated positions and tiny shoulders. This module
measures each entry on the smoothed residual ``y_obs - y_calc`` and classifies it by

* its height relative to the strongest net peak of the pattern (``rel_height``),
* its height relative to the point-to-point noise of the smoothed profile (``snr``),
* whether it sits at the Kbeta or W La1 position of a strong reflection (instrument line).

Peaks that are neither noise nor instrument lines are *minor* or *significant*; only
significant ones are meant to lower the overall assessment. For minor/significant peaks a
list of reference structures (fitted candidates, user input, the local COD cache) can be
matched to suggest trace phases. Hints never change a verdict.

The public functions never raise: any error yields ``None`` or the unchanged input.
"""

from __future__ import annotations

import atexit
import logging
import math
import os
import sys
import threading
import time
import warnings
from collections import OrderedDict

import numpy as np

logger = logging.getLogger("dara.server.peak_significance")

SCHEMA = 1
WINDOW_HALF_DEG = 0.15  # height window around a flagged position
SMOOTH_HALF_DEG = 0.025  # running-mean half width
DEDUPE_DEG = 0.05  # entries (and residual maxima) closer than this are one feature
NOISE_REL = 0.005  # noise if rel < NOISE_REL or snr < NOISE_SNR
NOISE_SNR = 3.0
SIGNIFICANT_REL = 0.02  # significant if rel >= 0.02 and snr >= 8
SIGNIFICANT_SNR = 8.0
W_LALPHA1_A = 1.47639
SATELLITE_TOL_DEG = 0.15
SATELLITE_MAX_PARENT_FRACTION = 0.05
PARENT_MIN_REL_INTENSITY = 0.20
HINT_TOL_DEG = 0.20
HINT_MERGE_DEG = 0.10
HINT_STRONG = 20.0  # % of strongest stick in range
HINT_MAX_VOLUME_A3 = 4000.0
HINT_MAX_POOL = 200
TRACE_HINTS_MAX = 3
HINT_OBSERVED_SIGMA = 3.0

_RANGE_KEYS = ("wmin", "wmax", "excluded_ranges")


# ---------------------------------------------------------------------------
# wavelength
# ---------------------------------------------------------------------------

def resolve_wavelength(spec) -> dict | None:
    """``{"alpha1_a", "kbeta_a", "anode"}`` of an anode name or a numeric wavelength (nm)."""
    try:
        from dara.utils import ANODE_LINES_A

        if isinstance(spec, str):
            key = spec.strip().lower()
            if key in ANODE_LINES_A:
                lines = ANODE_LINES_A[key]
                return {"alpha1_a": float(lines[0]), "kbeta_a": float(lines[2]), "anode": key}
        if isinstance(spec, bool) or spec is None:
            return None
        value = float(spec)
        if not math.isfinite(value) or value <= 0:
            return None
        return {"alpha1_a": value * 10.0, "kbeta_a": None, "anode": None}
    except Exception:
        logger.debug("Wavelength could not be resolved", exc_info=True)
        return None


def instrument_line_phrase(entries, anode) -> str:
    """Name of the instrument line(s) of ``instrument`` entries, e.g. ``Cu Kβ`` or ``W Lα1``.

    Falls back to ``[Cu ]Kβ or W Lα1`` when the entries carry no attribution type.
    """
    kinds = set()
    for entry in entries or []:
        attribution = entry.get("attribution") if isinstance(entry, dict) else None
        if isinstance(attribution, dict) and attribution.get("type") in {"kbeta", "w_lalpha"}:
            kinds.add(attribution["type"])
    beta = f"{str(anode).capitalize()} Kβ" if anode else "Kβ"
    if kinds == {"kbeta"}:
        return beta
    if kinds == {"w_lalpha"}:
        return "W Lα1"
    return f"{beta} or W Lα1"


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

def _smoother(step: float):
    from scipy.ndimage import uniform_filter1d

    m = max(1, 2 * int(round(SMOOTH_HALF_DEG / step)) + 1)
    if m > 1:
        return m, (lambda values: uniform_filter1d(values, m, mode="nearest"))
    return m, (lambda values: values)


def _clean_range_params(range_params) -> dict:
    if not isinstance(range_params, dict):
        return {}
    return {key: range_params[key] for key in _RANGE_KEYS if range_params.get(key) is not None}


def _profile_arrays(refinement):
    plot = getattr(refinement, "plot_data", None)
    arrays = []
    for name in ("x", "y_obs", "y_calc", "y_bkg"):
        value = getattr(plot, name, None)
        if value is None:
            return None
        arrays.append(np.asarray(value, dtype=float).ravel())
    if len({a.shape for a in arrays}) != 1:
        return None
    return arrays


def _parents(refinement, x, net, mask, reference_height):
    """Strong reflections of the fitted phases inside the analysis range."""
    peak_data = getattr(refinement, "peak_data", None)
    if peak_data is None or not hasattr(peak_data, "columns") or len(peak_data) == 0:
        return None
    needed = {"2theta", "intensity", "h", "k", "l", "phase"}
    if not needed <= set(peak_data.columns):
        return None
    tt = np.asarray(peak_data["2theta"], dtype=float)
    inten = np.asarray(peak_data["intensity"], dtype=float)
    phase = np.asarray(peak_data["phase"], dtype=object)
    keep = np.isfinite(tt) & np.isfinite(inten)
    strong = np.zeros(len(tt), dtype=bool)
    for name in {phase[i] for i in np.flatnonzero(keep)}:
        members = keep & (phase == name)
        top = float(np.max(inten[members]))
        strong |= members & (inten >= PARENT_MIN_REL_INTENSITY * top)
    strong &= _inside_mask(tt, x, mask)
    index = np.flatnonzero(strong)
    if index.size == 0:
        return None
    return {
        "tt": tt[index],
        "phase": phase[index],
        "hkl": [
            (int(peak_data["h"].iloc[i]), int(peak_data["k"].iloc[i]), int(peak_data["l"].iloc[i]))
            for i in index
        ],
        "rel": np.full(index.size, np.nan),  # observed relative height, filled lazily
    }


def _inside_mask(values, x, mask) -> np.ndarray:
    """Whether the nearest profile point of each position is part of the analysis range."""
    values = np.asarray(values, dtype=float)
    finite = np.isfinite(values)
    nearest = np.clip(np.searchsorted(x, np.where(finite, values, 0.0)), 1, len(x) - 1)
    pick = np.where(
        np.abs(x[nearest] - values) < np.abs(x[nearest - 1] - values), nearest, nearest - 1
    )
    return finite & mask[pick]


def _attribute(tt0, rel, parents, wavelength, x, net, mask, reference_height):
    """Kbeta / W Lalpha1 satellite attribution of one peak (``None`` when none applies)."""
    lines = []
    if wavelength.get("kbeta_a"):
        lines.append(("kbeta", float(wavelength["kbeta_a"])))
    lines.append(("w_lalpha", W_LALPHA1_A))
    alpha1 = float(wavelength["alpha1_a"])
    theta = np.radians(parents["tt"] / 2.0)
    best = None
    for kind, lam in lines:
        s = np.sin(theta) * lam / alpha1
        valid = s < 1.0
        satellite = np.full(s.shape, np.nan)
        satellite[valid] = 2.0 * np.degrees(np.arcsin(s[valid]))
        near = np.flatnonzero(valid & (np.abs(satellite - tt0) <= SATELLITE_TOL_DEG))
        for i in near:
            if not math.isfinite(parents["rel"][i]):
                w = mask & (np.abs(x - parents["tt"][i]) <= WINDOW_HALF_DEG)
                parents["rel"][i] = (
                    float(np.max(net[w])) / reference_height if w.any() else -1.0
                )
            parent_rel = parents["rel"][i]
            if not math.isfinite(parent_rel) or parent_rel <= 0:
                continue
            if rel <= SATELLITE_MAX_PARENT_FRACTION * parent_rel:
                if best is None or parent_rel > best["parent_rel_intensity"]:
                    best = {
                        "type": kind,
                        "wavelength_a": round(lam, 5),
                        "parent_phase": str(parents["phase"][i]),
                        "parent_two_theta": round(float(parents["tt"][i]), 3),
                        "parent_hkl": [int(v) for v in parents["hkl"][i]],
                        "parent_rel_intensity": round(float(parent_rel), 4),
                    }
    return best


def classify_missing_peaks(
    refinement, missing, *, wavelength, counts_like, range_params=None
) -> dict | None:
    """Classify the entries of ``diagnostics["missing"]`` (``None`` if it cannot be done)."""
    try:
        return _classify(refinement, missing, wavelength, counts_like, range_params)
    except Exception:
        logger.debug("Missing-peak classification failed", exc_info=True)
        return None


def _classify(refinement, missing, wavelength, counts_like, range_params):
    from dara.analysis_range import analysis_mask

    if missing is None:
        return None
    arrays = _profile_arrays(refinement)
    if arrays is None:
        return None
    x, y_obs, y_calc, y_bkg = arrays
    params = _clean_range_params(range_params)
    mask = analysis_mask(x, **params) & np.isfinite(y_obs) & np.isfinite(y_calc) & np.isfinite(y_bkg)
    if int(np.count_nonzero(mask)) < 10:
        return None
    step = float(np.median(np.diff(x[mask])))
    if not math.isfinite(step) or step <= 0:
        return None
    m, smooth = _smoother(step)
    net = smooth(np.where(np.isfinite(y_obs - y_bkg), y_obs - y_bkg, 0.0))
    resid = smooth(np.where(np.isfinite(y_obs - y_calc), y_obs - y_calc, 0.0))
    reference_height = float(np.max(net[mask]))
    if not math.isfinite(reference_height) or reference_height <= 0:
        return None
    d = np.diff(y_obs[mask])
    sigma_pt = float(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(2.0))
    if not math.isfinite(sigma_pt):
        return None

    entries = []
    for item in missing:
        try:
            tt0 = float(item[0])
            intensity = float(item[1]) if len(item) > 1 and item[1] is not None else float("nan")
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(tt0):
            continue
        if not analysis_mask([tt0], **params)[0]:
            continue
        w = mask & (np.abs(x - tt0) <= WINDOW_HALF_DEG)
        if not w.any():
            continue
        window_index = np.flatnonzero(w)
        top = int(window_index[int(np.argmax(resid[window_index]))])
        height = max(float(resid[top]), 0.0)
        obs_height = float(np.max(net[w]))
        sigma_local = sigma_pt
        if counts_like is True:
            sigma_local = max(sigma_pt, math.sqrt(max(float(np.interp(tt0, x, y_calc)), 0.0)))
        sigma_s = sigma_local / math.sqrt(m)
        entries.append({
            "tt": tt0,
            "max_tt": float(x[top]),
            "height": height,
            "obs_height": obs_height,
            "rel": height / reference_height,
            "snr": height / sigma_s if sigma_s > 0 else 0.0,
            "source": "residual_region" if intensity == 0.0 else "peak",
        })

    # One residual feature counts once: entries are merged when they are listed at nearly the
    # same position or when their windows found the same residual maximum (two listed peaks
    # 0.06-0.15 deg apart would otherwise both take the maximum of the shared feature).
    kept: list[dict] = []
    for entry in sorted(entries, key=lambda e: (-e["height"], abs(e["tt"] - e["max_tt"]))):
        if all(
            abs(entry["tt"] - other["tt"]) > DEDUPE_DEG
            and abs(entry["max_tt"] - other["max_tt"]) > DEDUPE_DEG
            for other in kept
        ):
            kept.append(entry)
    kept.sort(key=lambda e: e["tt"])

    parents = None
    parents_ready = False
    out = []
    for entry in kept:
        rel, snr = entry["rel"], entry["snr"]
        attribution = None
        if rel < NOISE_REL or snr < NOISE_SNR:
            cls = "noise"
        else:
            if wavelength and wavelength.get("anode"):
                if not parents_ready:
                    parents = _parents(refinement, x, net, mask, reference_height)
                    parents_ready = True
                if parents is not None:
                    attribution = _attribute(
                        entry["tt"], rel, parents, wavelength, x, net, mask, reference_height
                    )
            if attribution is not None:
                cls = "instrument"
            elif rel >= SIGNIFICANT_REL and snr >= SIGNIFICANT_SNR:
                cls = "significant"
            else:
                cls = "minor"
        out.append({
            "two_theta": round(entry["tt"], 3),
            "height": round(entry["height"], 1),
            "obs_height": round(entry["obs_height"], 1),
            "rel_height": round(rel, 4),
            "snr": round(snr, 1),
            "class": cls,
            "source": entry["source"],
            "attribution": attribution,
            "hint": None,
        })

    counts = {"noise": 0, "minor": 0, "significant": 0, "instrument": 0}
    for item in out:
        counts[item["class"]] += 1
    counts["total"] = len(out)
    significant = [e["rel_height"] for e in out if e["class"] == "significant"]
    minor = [e["rel_height"] for e in out if e["class"] == "minor"]
    wl = wavelength or {}
    return {
        "missing_classified": out,
        "counts_by_class": counts,
        "missing_significance": {
            "schema": SCHEMA,
            "reference_height": round(reference_height, 1),
            "noise_sigma": round(sigma_pt, 1),
            "smoothing_points": m,
            "significant_rel_sum": round(float(sum(significant)), 4),
            "largest_significant_rel": max(significant) if significant else None,
            "largest_minor_rel": max(minor) if minor else None,
            "wavelength_a": wl.get("alpha1_a"),
            "anode": wl.get("anode"),
            "thresholds": {
                "noise_rel": NOISE_REL,
                "noise_snr": NOISE_SNR,
                "significant_rel": SIGNIFICANT_REL,
                "significant_snr": SIGNIFICANT_SNR,
            },
        },
        "trace_hints": [],
    }


# ---------------------------------------------------------------------------
# trace-phase hints
# ---------------------------------------------------------------------------

def _lst_param(lst_data, name: str) -> float:
    from dara.server.confidence import _num

    for source in (lst_data, getattr(lst_data, "model_extra", None)):
        if source is None:
            continue
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        number = _num(value)
        if number is not None:
            return number
    return 0.0


def _merged_sticks(tt, intensity):
    order = np.argsort(tt, kind="stable")
    tt, intensity = tt[order], intensity[order]
    if tt.size == 0:
        return tt, intensity
    group = np.cumsum(np.r_[True, np.diff(tt) > HINT_MERGE_DEG]) - 1
    weight = np.bincount(group, intensity)
    safe = np.where(weight > 0, weight, 1.0)
    position = np.bincount(group, intensity * tt) / safe
    top = float(np.max(weight))
    if top <= 0:
        return position[:0], weight[:0]
    return position, 100.0 * weight / top


def _spacegroup_label(number):
    try:
        from dara.plot import spacegroup_symbol

        return spacegroup_symbol(int(number))
    except Exception:
        return None


def attach_trace_hints(classified, refinement, patterns) -> dict:
    """Fill ``hint`` / ``trace_hints`` from reference ``patterns`` (never raises)."""
    try:
        _attach(classified, refinement, patterns)
    except Exception:
        logger.debug("Trace hints failed", exc_info=True)
    return classified


def _attach(classified, refinement, patterns):
    from dara.server.confidence import phase_identity
    from dara.utils import angular_correction

    if not isinstance(classified, dict) or not patterns:
        return
    entries = classified.get("missing_classified") or []
    targets = [e for e in entries if e.get("class") in ("minor", "significant")]
    if not targets:
        return
    arrays = _profile_arrays(refinement)
    if arrays is None:
        return
    x, y_obs, _, y_bkg = arrays
    lo, hi = float(np.nanmin(x)), float(np.nanmax(x))
    significance = classified.get("missing_significance") or {}
    m = int(significance.get("smoothing_points") or 1)
    sigma_s = float(significance.get("noise_sigma") or 0.0) / math.sqrt(max(m, 1))
    net = np.where(np.isfinite(y_obs - y_bkg), y_obs - y_bkg, 0.0)
    if m > 1:
        from scipy.ndimage import uniform_filter1d

        net = uniform_filter1d(net, m, mode="nearest")

    lst_data = getattr(refinement, "lst_data", None)
    eps1, eps2 = _lst_param(lst_data, "EPS1"), _lst_param(lst_data, "EPS2")
    refined = set()
    for name in list(getattr(lst_data, "phases_results", None) or {}):
        refined.add(phase_identity(name))
    target_tt = np.array([t["two_theta"] for t in targets], dtype=float)

    matches = []
    for pattern in patterns:
        try:
            formula = str(pattern["formula"])
            number = pattern.get("spacegroup_number")
            if (formula, number) in refined:
                continue
            tt = np.asarray(pattern["two_theta"], dtype=float).ravel()
            inten = np.asarray(pattern["intensity"], dtype=float).ravel()
            if tt.shape != inten.shape or tt.size == 0:
                continue
            tt = tt + angular_correction(tt, eps1, eps2)
            keep = np.isfinite(tt) & np.isfinite(inten) & (tt >= lo) & (tt <= hi) & (inten > 0)
            if not keep.any():
                continue
            position, rel_int = _merged_sticks(tt[keep], inten[keep])
            strong = rel_int >= HINT_STRONG
            if not strong.any():
                continue
            s_pos, s_int = position[strong], rel_int[strong]
            hit = np.abs(s_pos[:, None] - target_tt[None, :]) <= HINT_TOL_DEG
            matched_targets = np.flatnonzero(hit.any(axis=0))
            n_matched = int(matched_targets.size)
            need = 1 if s_pos.size == 1 else 2
            if n_matched < need:
                continue
            top = int(np.argmax(rel_int))
            top_pos = float(position[top])
            top_ok = bool(np.any(np.abs(top_pos - target_tt) <= HINT_TOL_DEG))
            if not top_ok:
                w = np.abs(x - top_pos) <= WINDOW_HALF_DEG
                top_ok = bool(w.any() and sigma_s > 0 and np.max(net[w]) >= HINT_OBSERVED_SIGMA * sigma_s)
            if not top_ok:
                continue
            summed = float(np.sum(s_int[hit.any(axis=1)]))
            matches.append({
                "rank": (
                    -n_matched, "." in formula, -int(pattern.get("popularity") or 0), -summed,
                ),
                "targets": set(int(i) for i in matched_targets),
                "hint": {
                    "phase": str(pattern.get("name")),
                    "formula": formula,
                    "spacegroup_number": number,
                    "spacegroup_symbol": pattern.get("spacegroup_symbol")
                    or (_spacegroup_label(number) if number is not None else None),
                    "matched_peaks": [float(target_tt[i]) for i in matched_targets],
                    "source": pattern.get("source"),
                },
            })
        except Exception:
            logger.debug("Reference pattern skipped", exc_info=True)
    if not matches:
        return
    matches.sort(key=lambda item: item["rank"])

    for index, entry in enumerate(targets):
        for match in matches:
            if index in match["targets"]:
                entry["hint"] = dict(match["hint"])
                break
    distinct, seen = [], set()
    for match in matches:
        key = (match["hint"]["formula"], match["hint"]["spacegroup_number"])
        if key in seen:
            continue
        seen.add(key)
        distinct.append(dict(match["hint"]))
        if len(distinct) >= TRACE_HINTS_MAX:
            break
    classified["trace_hints"] = distinct


# ---------------------------------------------------------------------------
# reference pools and the background worker
# ---------------------------------------------------------------------------

_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()  # key -> (patterns, stored at monotonic s)
_CACHE_MAX = 16
EMPTY_RETRY_S = 300.0  # an empty pool (nothing cached yet) is looked up again after this
FAILURE_COOLDOWN_S = 60.0  # a failed simulation is not retried before this
PENDING_MAX = 8  # queued background jobs; the oldest are dropped beyond this
YIELD_S = 0.01  # pause after each simulated structure so request threads get the GIL

_FAILED: dict = {}  # key -> monotonic time of the last failure
_PENDING: set = set()  # keys that are queued or running
_QUEUE: "OrderedDict[object, object]" = OrderedDict()  # key -> job callable
_LOCK = threading.Lock()
_COND = threading.Condition(_LOCK)
_STOP = threading.Event()
_WORKER: threading.Thread | None = None
_ACTIVE = None  # key of the running job


class _Stopped(Exception):
    """The worker was asked to stop while a simulation was running."""


def stop_requested() -> bool:
    return _STOP.is_set()


def yield_cpu() -> None:
    """Give request threads a chance to take the GIL between two heavy steps."""
    time.sleep(YIELD_S)


def _lower_thread_priority() -> None:
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
        elif hasattr(os, "setpriority"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except Exception:
        logger.debug("Worker priority could not be lowered", exc_info=True)


def _worker_loop() -> None:
    global _ACTIVE
    _lower_thread_priority()
    while True:
        with _COND:
            while not _QUEUE and not _STOP.is_set():
                _COND.wait()
            if _STOP.is_set():
                return
            key, job = _QUEUE.popitem(last=False)
            _ACTIVE = key
        try:
            job()
        except Exception:
            logger.debug("Background job failed", exc_info=True)
        finally:
            with _COND:
                _PENDING.discard(key)
                _ACTIVE = None
                _COND.notify_all()
        time.sleep(0)


def submit_background(key, job) -> bool:
    """Queue ``job`` (a callable) once per ``key`` on the single daemon worker.

    A key that is already queued or running is not queued again. At most ``PENDING_MAX``
    jobs wait; beyond that the oldest are dropped (their keys can be submitted again).
    """
    global _WORKER
    if _STOP.is_set():
        return False
    with _COND:
        if key in _PENDING:
            return False
        _PENDING.add(key)
        _QUEUE[key] = job
        while len(_QUEUE) > PENDING_MAX:
            dropped, _ = _QUEUE.popitem(last=False)
            _PENDING.discard(dropped)
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(
                target=_worker_loop, name="dara-trace-hints", daemon=True
            )
            _WORKER.start()
        _COND.notify_all()
    return True


def wait_idle(timeout: float = 30.0) -> bool:
    """Wait until no background job is queued or running (tests, benchmark)."""
    deadline = time.monotonic() + timeout
    with _COND:
        while _QUEUE or _ACTIVE is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _COND.wait(remaining)
    return True


def shutdown(timeout: float = 2.0) -> None:
    """Stop the worker: drop the queue, abort a running simulation and join briefly."""
    _STOP.set()
    with _COND:
        _QUEUE.clear()
        _PENDING.clear()
        _COND.notify_all()
    worker = _WORKER
    if worker is not None and worker.is_alive() and worker is not threading.current_thread():
        worker.join(timeout)


atexit.register(_STOP.set)


def hint_mode() -> str:
    """``off``, ``sync`` or ``background`` (env ``DARA_TRACE_HINTS``; off under pytest)."""
    value = (os.environ.get("DARA_TRACE_HINTS") or "").strip().lower()
    if value in {"background", "sync", "off"}:
        return value
    return "off" if "pytest" in sys.modules else "background"


def _simulate_cod_pool(elements, precursors, wavelength_a, two_theta_range) -> list[dict]:
    from dara.cif import Cif
    from dara.search.prefilter import simulate_structure_reflections
    from dara.server.confidence import _reduced_formula
    from dara.structure_db import cod_candidate_rows, cod_cache_dir

    rows = cod_candidate_rows(list(elements), list(precursors), 0.1)
    cache_dir = cod_cache_dir()
    unique: "OrderedDict[tuple, dict]" = OrderedDict()
    for row in rows:
        formula, code, sg = row[0], row[1], row[2]
        path = cache_dir / f"{code}.cif"
        if not path.is_file():
            continue
        try:
            reduced = _reduced_formula(str(formula)) or str(formula)
            number = int(sg) if sg is not None else None
        except Exception:
            continue
        key = (reduced, number)
        if key in unique:
            unique[key]["popularity"] += 1
        else:
            unique[key] = {"path": path, "code": code, "formula": reduced, "sg": number,
                           "popularity": 1}
    patterns: list[dict] = []
    for item in list(unique.values())[:HINT_MAX_POOL]:
        if _STOP.is_set():
            raise _Stopped()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                structure = Cif.from_file(item["path"]).to_structure()
                if structure.volume > HINT_MAX_VOLUME_A3:
                    continue
                tt, intensity, _ = simulate_structure_reflections(
                    structure, wavelength_a, tuple(two_theta_range)
                )
            patterns.append({
                "name": f"{item['formula']}_{item['sg']}_(cod_{item['code']})-0",
                "formula": item["formula"],
                "spacegroup_number": item["sg"],
                "spacegroup_symbol": _spacegroup_label(item["sg"]) if item["sg"] else None,
                "source": "cod",
                "popularity": item["popularity"],
                "two_theta": np.asarray(tt, dtype=float),
                "intensity": np.asarray(intensity, dtype=float),
            })
        except Exception:
            logger.debug("COD reference %s skipped", item.get("code"), exc_info=True)
        if _STOP.is_set():
            raise _Stopped()
        yield_cpu()
    return patterns


def _store(key, patterns) -> None:
    with _LOCK:
        _CACHE[key] = (patterns, time.monotonic())
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
        _FAILED.pop(key, None)


def _background_job(key, args) -> None:
    try:
        patterns = _simulate_cod_pool(*args)
    except _Stopped:
        return
    except Exception:
        # Not cached: a failure (or an interrupted run) must not stick; retry after a cooldown.
        logger.debug("Background COD simulation failed", exc_info=True)
        with _LOCK:
            _FAILED[key] = time.monotonic()
        return
    _store(key, patterns)


def cod_reference_patterns(
    elements, precursors, wavelength_a, two_theta_range, *, mode
) -> list | None:
    """Simulated patterns of cached COD structures; ``None`` while not available."""
    try:
        if mode not in {"sync", "background"} or not elements:
            return None
        lo, hi = float(two_theta_range[0]), float(two_theta_range[1])
        key = (
            frozenset(str(e) for e in elements),
            tuple(sorted(str(p) for p in (precursors or []))),
            round(float(wavelength_a), 5), round(lo, 1), round(hi, 1),
        )
        now = time.monotonic()
        with _LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                patterns, stored_at = cached
                if patterns or now - stored_at < EMPTY_RETRY_S:
                    _CACHE.move_to_end(key)
                    return list(patterns)
                del _CACHE[key]  # an empty pool is looked up again (new COD downloads)
            failed_at = _FAILED.get(key)
            if (
                mode == "background"
                and failed_at is not None
                and now - failed_at < FAILURE_COOLDOWN_S
            ):
                return None
        args = (list(elements), list(precursors or []), float(wavelength_a), (lo, hi))
        if mode == "sync":
            try:
                patterns = _simulate_cod_pool(*args)
            except Exception:
                logger.debug("COD simulation failed", exc_info=True)
                return []
            _store(key, patterns)
            return list(patterns)
        submit_background(key, lambda: _background_job(key, args))
        return None
    except Exception:
        logger.debug("COD reference patterns failed", exc_info=True)
        return None
