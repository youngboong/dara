"""Which reference structures explain the selected peak positions? (peak-candidate search)

``POST /api/task/{id}/peak-candidates`` ranks the task's candidate/input CIFs, the reference
library and the cached COD structures by how well their strong reflections cover the given
2θ positions (``peak_significance.rank_structures_for_peaks``). Store access happens on the
event-loop thread; all simulation and scoring runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dara.server import api_router, peak_significance, selected_refinement_router
from dara.server import confidence as confidence_module

try:  # WS-A: version-aware selections; absent -> "best"/index only
    from dara.server.versions import parse_selection, resolve_refinement
except Exception:  # pragma: no cover - depends on the installed server version
    parse_selection = None
    resolve_refinement = None

logger = logging.getLogger("dara.server.peak_candidates")

router = APIRouter(prefix="/api")

POOLS = ("candidate", "input", "library", "cod")
_COD_STEM = re.compile(r"_\(cod_(\d+)\)-\d+$")
_LIBRARY_MEMO_MAX = 256
_LIBRARY_MEMO: "OrderedDict[tuple, tuple | None]" = OrderedDict()
_LIBRARY_MEMO_LOCK = threading.Lock()


class PeakCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sel: StrictInt | str = "best"
    two_theta: list[float] = Field(min_length=1, max_length=10)
    tolerance_deg: float = Field(default=peak_significance.HINT_TOL_DEG, ge=0.02, le=0.5)
    pools: list[Literal["candidate", "input", "library", "cod"]] = Field(
        default_factory=lambda: list(POOLS), min_length=1, max_length=4
    )
    limit: int = Field(default=20, ge=1, le=50)


def _load(task_id):
    return selected_refinement_router._load(task_id)


def _resolve_refinement(document, record, sel):
    if parse_selection is not None and resolve_refinement is not None:
        refinement, _ = resolve_refinement(document, record, parse_selection(sel))
        return refinement
    if isinstance(sel, str) and sel != "best":
        raise HTTPException(422, "Versions are not available on this server.")
    from dara.server import review_router

    return review_router._select_refinement(document, sel)


# ---------------------------------------------------------------------------
# pools (worker thread; no store access)
# ---------------------------------------------------------------------------

def _task_patterns(document, refinement, ctx, window, pools) -> list[dict]:
    """Candidate / input CIFs of the task (memoized with the task page)."""
    sources = []
    if "candidate" in pools:
        sources += [
            (cif, "candidate")
            for entry in getattr(document, "results", None) or []
            for cif in api_router._flatten_cifs(entry[0])
        ]
    if "input" in pools:
        sources += [
            (cif, "input")
            for cif in api_router._flatten_cifs(list(getattr(document, "input_cifs", None) or []))
        ]
    patterns, seen = [], set()
    for cif, source in sources:
        stem = Path(str(getattr(cif, "filename", "") or "")).stem
        if not stem or stem in seen:
            continue
        seen.add(stem)
        simulated = api_router._simulated_reference(cif, stem, ctx, window, compute=True)
        if simulated is None or simulated is api_router._NOT_SIMULATED:
            continue
        formula, number = confidence_module.phase_identity(stem)
        patterns.append({
            "name": stem,
            "formula": formula or stem.split("_")[0],
            "spacegroup_number": number,
            "spacegroup_symbol": api_router._spacegroup_symbol(number) if number else None,
            "source": source,
            "origin": "task",
            "popularity": 1,
            "two_theta": simulated[0],
            "intensity": simulated[1],
        })
    return patterns


def _library_patterns(ctx, window) -> list[dict]:
    from dara.cif import Cif
    from dara.search.prefilter import simulate_structure_reflections
    from dara.server import reference_library_router as library

    patterns: list[dict] = []
    for item in library._read_registry():
        item_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(item_id, str) or not library._ID_PATTERN.match(item_id):
            continue
        try:
            path = library.library_directory() / f"{item_id}.cif"
            key = (
                item_id, path.stat().st_mtime_ns, round(ctx.wavelength["alpha1_a"], 5),
                round(window[0], 1), round(window[1], 1),
            )
        except (OSError, TypeError):
            continue
        with _LIBRARY_MEMO_LOCK:
            hit = key in _LIBRARY_MEMO
            cached = _LIBRARY_MEMO.get(key)
            if hit:
                _LIBRARY_MEMO.move_to_end(key)
        if not hit:
            cached = None
            try:
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    cif = Cif.from_file(path)
                    structure = cif.to_structure()
                    if structure.volume <= peak_significance.HINT_MAX_VOLUME_A3:
                        two_theta, intensity, _ = simulate_structure_reflections(
                            structure, ctx.wavelength["alpha1_a"], window
                        )
                        name = str(cif.name)
                        formula, number = confidence_module.phase_identity(name)
                        if not formula:
                            formula = structure.composition.reduced_formula
                        if number is None:
                            try:
                                number = int(structure.get_space_group_info(symprec=0.1)[1])
                            except Exception:
                                number = None
                        cached = (
                            name, formula, number,
                            np.asarray(two_theta, float), np.asarray(intensity, float),
                        )
            except Exception:
                logger.debug("Library CIF %s skipped", item_id, exc_info=True)
            with _LIBRARY_MEMO_LOCK:
                _LIBRARY_MEMO[key] = cached
                while len(_LIBRARY_MEMO) > _LIBRARY_MEMO_MAX:
                    _LIBRARY_MEMO.popitem(last=False)
        if cached is None:
            continue
        name, formula, number, two_theta, intensity = cached
        patterns.append({
            "name": name,
            "formula": formula,
            "spacegroup_number": number,
            "spacegroup_symbol": api_router._spacegroup_symbol(number) if number else None,
            "source": "library",
            "origin": f"library:{item_id}",
            "popularity": 1,
            "two_theta": two_theta,
            "intensity": intensity,
        })
    return patterns


def _cod_code(pattern) -> str | None:
    code = pattern.get("code")
    if code is None:
        match = _COD_STEM.search(str(pattern.get("name") or ""))
        code = match.group(1) if match else None
    return str(code) if code is not None else None


def _compute(document, refinement, request: PeakCandidatesRequest, ctx, window) -> dict:
    started = time.perf_counter()
    pools = set(request.pools)
    patterns = _task_patterns(document, refinement, ctx, window, pools)
    pools_used = [pool for pool in ("candidate", "input") if pool in pools]
    if "library" in pools:
        patterns += _library_patterns(ctx, window)
        pools_used.append("library")
    pending: list[str] = []
    if "cod" in pools:
        elements = list(getattr(document, "elements", None) or [])
        precursors = list(getattr(document, "precursors", None) or [])
        cod = (
            peak_significance.cod_reference_patterns(
                elements, precursors, ctx.wavelength["alpha1_a"], window, mode="background"
            )
            if elements
            else []
        )
        if cod is None:
            pending.append("cod")
        else:
            patterns += [dict(pattern, origin=f"cod:{_cod_code(pattern)}") for pattern in cod]
            pools_used.append("cod")

    noise_sigma, smoothing = peak_significance.noise_for(
        refinement, ctx.wavelength, ctx.counts_like, ctx.range_params
    )
    ranked = peak_significance.rank_structures_for_peaks(
        request.two_theta, refinement, patterns,
        noise_sigma=noise_sigma, smoothing_points=smoothing,
        tolerance_deg=request.tolerance_deg, limit=request.limit, range_params=ctx.range_params,
    )
    by_name: dict[str, dict] = {}
    for pattern in patterns:
        by_name.setdefault(str(pattern.get("name")), pattern)

    candidates = []
    for entry in ranked:
        pattern = by_name.get(entry["name"], {})
        origin = pattern.get("origin") or "task"
        if origin.startswith("cod:"):
            code = _cod_code(pattern)
            try:
                from dara import structure_db

                available = bool(code) and (structure_db.cod_cache_dir() / f"{code}.cif").is_file()
            except Exception:
                available = False
        else:
            available = True
        candidates.append({
            "ref": entry["name"],
            "source": "task" if origin == "task" else origin,
            "name": entry["name"],
            "formula": entry["formula"],
            "spacegroup_number": entry["spacegroup_number"],
            "spacegroup_symbol": entry["spacegroup_symbol"],
            "pattern_source": entry["pattern_source"],
            "matched": entry["matched"],
            "strong_unobserved": entry["strong_unobserved"],
            "n_strong": entry["n_strong"],
            "score": entry["score"],
            "cif_available": available,
            "sticks": entry["sticks"],
        })
    return {
        "pools_used": pools_used,
        "pools_pending": pending,
        "candidates": candidates,
        "compute_s": time.perf_counter() - started,
    }


@router.post("/task/{task_id}/peak-candidates")
async def peak_candidates(task_id: int, request: PeakCandidatesRequest):
    started = time.perf_counter()
    record, document = _load(task_id)  # store access: event-loop thread only
    refinement = _resolve_refinement(document, record, request.sel)
    ctx = api_router._peak_context(document, job_key=record.get("uuid"))
    window = ctx.measured_range(refinement)
    if ctx.wavelength is None or window is None:
        raise HTTPException(422, "The wavelength or the measured range of this task is unknown.")
    if any(not window[0] <= value <= window[1] for value in request.two_theta):
        raise HTTPException(
            422, f"2θ positions must lie inside the measured range {window[0]:.2f}-{window[1]:.2f}°."
        )
    result = await asyncio.to_thread(_compute, document, refinement, request, ctx, window)
    elapsed = time.perf_counter() - started
    logger.info(
        "peak-candidates task %s: %d targets, %d candidates, pending=%s, %.2f s",
        task_id, len(request.two_theta), len(result["candidates"]), result["pools_pending"],
        elapsed,
    )
    return {
        "task_id": task_id,
        "sel": request.sel,
        "two_theta": list(request.two_theta),
        "tolerance_deg": request.tolerance_deg,
        "pools_used": result["pools_used"],
        "pools_pending": result["pools_pending"],
        "candidates": result["candidates"],
        "elapsed_s": round(elapsed, 3),
    }
