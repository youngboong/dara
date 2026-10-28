"""Versions of a completed task, raw fit curves and a two-fit comparison.

``sel`` everywhere is ``"best"``, a candidate index or ``"version:<n>"`` (``versions.parse_selection``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections import OrderedDict
from datetime import datetime

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from dara.analysis_range import analysis_mask
from dara.server import quick_refine, versions
from dara.server.utils import convert_to_local_tz, get_worker_store

logger = logging.getLogger("dara.server.versions")

router = APIRouter(prefix="/api")

SIMILAR_RWP_POINTS = 0.1  # |delta Rwp| below this many percentage points counts as "similar"
_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}


# --------------------------------------------------------------------------- helpers
def _completed_task(task_id: int) -> dict:
    with get_worker_store() as store:
        task = store.query_one({"index": task_id})
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.get("status") != "COMPLETED":
        raise HTTPException(409, "Only completed tasks have versions")
    return task


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value is not None else None


def _adopted(task: dict):
    return (task.get("review") or {}).get("selection")


def _finite(value, digits: int | None = None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def _sig(values, digits: int = 6) -> list:
    """Floats rounded to ``digits`` significant digits; non-finite values become ``None``."""
    out = []
    for value in np.asarray(values, dtype=float).tolist():
        out.append(float(f"{value:.{digits}g}") if math.isfinite(value) else None)
    return out


def _wavelength(document):
    from dara.server.candidate_comparison_router import _wavelength_angstrom

    return _wavelength_angstrom((getattr(document, "search_kwargs", None) or {}).get("wavelength"))


def _version_entry(record: dict) -> dict:
    summary = record.get("summary") or {}
    quality = summary.get("fit_quality") or {}
    counts = (summary.get("peak_diagnostics") or {}).get("counts") or {}
    return {
        "version": record["version"],
        "label": record.get("label"),
        "origin": record.get("origin"),
        "created_at": record.get("created_at"),
        "rwp": summary.get("rwp"),
        "gof": quality.get("gof"),
        "phases": summary.get("phases") or record.get("phases") or [],
        "phase_stems": record.get("phases") or [],
        "weight_percent": summary.get("weight_percent") or {},
        "n_unexplained": counts.get("missing"),
        "cautions_count": len(summary.get("cautions") or []),
        "elapsed_s": record.get("elapsed_s"),
        "controls": record.get("controls"),
        "background": record.get("background"),
        "deletable": True,
    }


def _original_entry(task: dict, document=None) -> dict:
    summary = task.get("summary") if isinstance(task.get("summary"), dict) else {}
    digests = summary.get("phases") or []
    names = summary.get("final_phases") or [d.get("name") for d in digests]
    weights = {d.get("name"): d.get("weight_percent") for d in digests if d.get("name")}
    unexplained = None
    if document is not None:
        try:
            from dara.server.api_router import _peak_diagnostics

            unexplained = _peak_diagnostics(document.final_result)["counts"]["missing"]
        except Exception:  # noqa: BLE001
            unexplained = None
    return {
        "version": 1,
        "label": "Original analysis",
        "origin": {"kind": "automatic"},
        "created_at": _iso(task.get("end_time")),
        "rwp": summary.get("final_rwp"),
        "gof": summary.get("gof"),
        "phases": names,
        "phase_stems": [],
        "weight_percent": weights,
        "n_unexplained": unexplained,
        "cautions_count": None,
        "elapsed_s": None,
        "controls": None,
        "background": None,
        "deletable": False,
    }


# --------------------------------------------------------------------------- versions
@router.get("/task/{task_id}/versions")
async def list_task_versions(task_id: int):
    task = _completed_task(task_id)
    quick_refine.maybe_warm(task)
    document = quick_refine._DOCUMENTS.peek(task["uuid"])
    records = versions.list_versions(task["uuid"])
    return {
        "task_id": task_id,
        "adopted": _adopted(task),
        "versions": [_original_entry(task, document)] + [_version_entry(r) for r in records],
    }


@router.get("/task/{task_id}/versions/{number}")
async def get_task_version(task_id: int, number: int):
    task = _completed_task(task_id)
    if number < 1:
        raise HTTPException(404, "Version not found")
    if number == 1:
        entry = _original_entry(task)
        return {**entry, "effective_phase_params": None, "final_result": None, "use_task_payload": True}
    record = versions.load_version(task["uuid"], number, light=True)
    return {
        **_version_entry(record),
        "effective_phase_params": record.get("effective_phase_params"),
        "refinement_params": record.get("refinement_params"),
        "final_result": record.get("summary"),
    }


@router.delete("/task/{task_id}/versions/{number}")
async def delete_task_version(task_id: int, number: int):
    task = _completed_task(task_id)
    if number == 1:
        raise HTTPException(400, "The original analysis cannot be deleted")
    if number < 1:
        raise HTTPException(404, "Version not found")
    versions.load_version(task["uuid"], number, light=True)  # 404 when missing/deleted
    if _adopted(task) == f"version:{number}":
        raise HTTPException(409, "This version is the adopted result; adopt another result first.")
    versions.tombstone_version(task["uuid"], number)
    try:
        from dara.server import api_router

        api_router._version_plot_json.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    return {"task_id": task_id, "version": number, "deleted": True}


# --------------------------------------------------------------------------- fit curves
async def _selection(task_id: int, sel: str | None):
    task = _completed_task(task_id)
    document = await quick_refine.load_document(task["uuid"])
    refinement, meta = versions.resolve_refinement(document, task, sel)
    return task, document, refinement, meta


def _reflections(refinement) -> dict[str, list]:
    peaks = refinement.peak_data
    out: dict[str, list] = {}
    if peaks is None or "phase" not in getattr(peaks, "columns", []):
        return out
    for name, group in peaks.groupby("phase", sort=False):
        out[str(name)] = _sig(group["2theta"].to_numpy(dtype=float), 6)
    return out


@router.get("/task/{task_id}/fit-curves")
async def fit_curves(task_id: int, sel: str | None = Query("best")):
    selection = versions.parse_selection(sel if isinstance(sel, str) else "best")
    task, document, refinement, meta = await _selection(task_id, selection)
    data = refinement.plot_data
    params = meta.get("refinement_params") or {}
    payload = {
        "task_id": task_id,
        "name": (task.get("job") or {}).get("name"),
        "sel": versions.format_selection(selection),
        "x": _sig(data.x),
        "y_obs": _sig(data.y_obs),
        "y_calc": _sig(data.y_calc),
        "y_bkg": _sig(data.y_bkg),
        "phases": {str(name): _sig(values) for name, values in (data.structs or {}).items()},
        "reflections": _reflections(refinement),
        "rwp": _finite(refinement.lst_data.rwp),
        "wavelength_angstrom": _wavelength(document),
        "excluded_ranges": params.get("excluded_ranges") or [],
    }
    return JSONResponse(payload, headers=_CACHE_HEADERS)


# --------------------------------------------------------------------------- fit comparison
_TASK_PAYLOADS: "OrderedDict[str, dict]" = OrderedDict()
_TASK_PAYLOADS_LOCK = threading.Lock()


def _task_payload(task: dict, document) -> dict:
    """``summarize_document`` payload of the original analysis (LRU of 4, per task)."""
    key = task["uuid"]
    with _TASK_PAYLOADS_LOCK:
        if key in _TASK_PAYLOADS:
            _TASK_PAYLOADS.move_to_end(key)
            return _TASK_PAYLOADS[key]
    from dara.server.api_router import summarize_document

    payload = summarize_document(task, document, persist_summary=False)
    with _TASK_PAYLOADS_LOCK:
        _TASK_PAYLOADS[key] = payload
        while len(_TASK_PAYLOADS) > 4:
            _TASK_PAYLOADS.popitem(last=False)
    return payload


def _lite(selection, meta: dict, summary: dict) -> dict:
    quality = summary.get("fit_quality") or {}
    counts = (summary.get("peak_diagnostics") or {}).get("counts") or {}
    return {
        "sel": versions.format_selection(selection),
        "label": meta.get("label"),
        "rwp": _finite(summary.get("rwp", quality.get("rwp"))),
        "gof": _finite(quality.get("gof")),
        "chi_squared": _finite(quality.get("chi_squared")),
        "weight_percent": summary.get("weight_percent") or {},
        "mass_balance": summary.get("mass_balance"),
        "n_unexplained": counts.get("missing"),
        "cautions": summary.get("cautions") or [],
    }


def _side_summary(task, document, selection, meta, refinement) -> dict:
    if selection == "best":
        return _task_payload(task, document)["final_result"]
    if isinstance(selection, int):
        payload = _task_payload(task, document)
        for entry in payload.get("all_results", []):
            if entry.get("source_index") == selection:
                return entry
        # a duplicate hypothesis folded into another displayed entry
        from dara.server.api_router import _fit_quality, _peak_diagnostics

        return {
            "rwp": refinement.lst_data.rwp,
            "fit_quality": _fit_quality(refinement),
            "peak_diagnostics": _peak_diagnostics(refinement),
            "weight_percent": {},
            "cautions": [],
        }
    record = meta["record"]
    return record.get("summary") or {}


def _valid_mask(x: np.ndarray, *param_sets) -> np.ndarray:
    keep = np.isfinite(x)
    for params in param_sets:
        keep &= analysis_mask(
            x, **{k: params[k] for k in ("wmin", "wmax", "excluded_ranges") if params.get(k) is not None}
        )
    return keep


@router.get("/task/{task_id}/fit-compare")
async def fit_compare(task_id: int, a: str = Query("best"), b: str = Query(...)):
    sel_a, sel_b = versions.parse_selection(a), versions.parse_selection(b)
    if sel_a == sel_b:
        raise HTTPException(400, "Select two different fits to compare.")
    task = _completed_task(task_id)
    document = await quick_refine.load_document(task["uuid"])
    ref_a, meta_a = versions.resolve_refinement(document, task, sel_a)
    ref_b, meta_b = versions.resolve_refinement(document, task, sel_b)

    x = np.asarray(ref_a.plot_data.x, dtype=float)
    other = np.asarray(ref_b.plot_data.x, dtype=float)
    if x.shape != other.shape or not np.allclose(x, other, rtol=0, atol=1e-7):
        raise HTTPException(
            409, "These fits use different angular grids; direct pointwise comparison is unavailable."
        )
    observed = np.asarray(ref_a.plot_data.y_obs, dtype=float)
    if not np.allclose(observed, ref_b.plot_data.y_obs, rtol=1e-7, atol=1e-7):
        raise HTTPException(409, "These fits use different observed intensities.")
    calc_a = np.asarray(ref_a.plot_data.y_calc, dtype=float)
    calc_b = np.asarray(ref_b.plot_data.y_calc, dtype=float)
    valid = _valid_mask(x, meta_a.get("refinement_params") or {}, meta_b.get("refinement_params") or {})
    valid &= np.isfinite(observed) & np.isfinite(calc_a) & np.isfinite(calc_b)

    regions: list[dict] = []
    difference = np.where(valid, np.abs(calc_a - calc_b), -1)
    for index in np.argsort(difference)[::-1]:
        if not valid[index] or difference[index] <= 0:
            continue
        if all(abs(float(x[index]) - item["two_theta"]) >= 0.5 for item in regions):
            regions.append(
                {"two_theta": float(x[index]), "calculated_difference": float(difference[index])}
            )
        if len(regions) == 5:
            break

    from dara.server.api_router import _search_result_key

    def identities(result):
        return {_search_result_key([name]): name for name in result.lst_data.phases_results}

    ids_a, ids_b = identities(ref_a), identities(ref_b)

    def masked(array):
        return [float(v) if keep else None for v, keep in zip(array, valid)]

    loop = asyncio.get_running_loop()
    summary_a = await loop.run_in_executor(None, _side_summary, task, document, sel_a, meta_a, ref_a)
    summary_b = await loop.run_in_executor(None, _side_summary, task, document, sel_b, meta_b, ref_b)
    rwp_a, rwp_b = float(ref_a.lst_data.rwp), float(ref_b.lst_data.rwp)
    delta = rwp_b - rwp_a
    better = "similar" if abs(delta) < SIMILAR_RWP_POINTS else ("a" if delta > 0 else "b")
    return {
        "task_id": task_id,
        "a": _lite(sel_a, meta_a, {**summary_a, "rwp": rwp_a}),
        "b": _lite(sel_b, meta_b, {**summary_b, "rwp": rwp_b}),
        "x": x.tolist(),
        "observed": masked(observed),
        "a_calculated": masked(calc_a),
        "b_calculated": masked(calc_b),
        "a_residual": masked(observed - calc_a),
        "b_residual": masked(observed - calc_b),
        "a_only": [ids_a[key] for key in ids_a.keys() - ids_b.keys()],
        "b_only": [ids_b[key] for key in ids_b.keys() - ids_a.keys()],
        "common": [ids_a[key] for key in ids_a.keys() & ids_b.keys()],
        "regions": regions,
        "verdict": {"rwp_delta": round(delta, 6), "better": better},
        "wavelength_angstrom": _wavelength(document),
    }
