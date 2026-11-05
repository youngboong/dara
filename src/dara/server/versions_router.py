"""Versions of a completed task, raw fit curves and a two-fit comparison.

``sel`` everywhere is ``"best"``, a candidate index or ``"version:<n>"`` (``versions.parse_selection``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections import OrderedDict
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from dara.server import quick_refine, versions

logger = logging.getLogger("dara.server.versions")

router = APIRouter(prefix="/api")

BASELINE_LABEL = "Baseline (current code)"  # same label as versions.mjs
SIMILAR_RWP_POINTS = 0.1  # |delta Rwp| below this many percentage points counts as "similar"
_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}


# --------------------------------------------------------------------------- helpers
def _completed_task(task_id: int) -> dict:
    return quick_refine.completed_task(task_id)


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
    from dara.server.api_router import unexplained_count

    summary = record.get("summary") or {}
    quality = summary.get("fit_quality") or {}
    return {
        "version": record["version"],
        "label": versions.display_label(record),
        "origin": record.get("origin"),
        "created_at": record.get("created_at"),
        "rwp": summary.get("rwp"),
        "gof": quality.get("gof"),
        "phases": summary.get("phases") or record.get("phases") or [],
        "phase_stems": record.get("phases") or [],
        "weight_percent": summary.get("weight_percent") or {},
        "n_unexplained": unexplained_count(summary.get("peak_diagnostics")),
        "cautions_count": len(summary.get("cautions") or []),
        "elapsed_s": record.get("elapsed_s"),
        "controls": record.get("controls"),
        "background": record.get("background"),
        "refine_mode": record.get("refine_mode") or "full",
        "fixed_phases": record.get("fixed_phases") or [],
        "iteration_cap": record.get("iteration_cap"),
        "replaces": record.get("replaces"),
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
            from dara.server.api_router import _classified_peak_diagnostics, unexplained_count

            # same definition as the task card: significant + minor peaks
            unexplained = unexplained_count(_classified_peak_diagnostics(
                document, document.final_result, hint_mode="off"
            ))
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
        "refine_mode": "full",
        "fixed_phases": [],
        "iteration_cap": None,
        "replaces": None,
        "deletable": False,
    }


def _baseline_stale(task: dict, records: list[dict]) -> bool:
    """True when the stored result predates the current refinement code and has no baseline yet."""
    from dara.final_refinement_spec import REFINEMENT_CODE_DATE

    if any(
        record.get("label") == BASELINE_LABEL and not versions.is_quick_check(record)
        for record in records
    ):
        return False
    finished = task.get("end_time")
    if isinstance(finished, str):
        try:
            finished = datetime.fromisoformat(finished)
        except ValueError:
            return False
    if not isinstance(finished, datetime):
        return False
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return finished < datetime.fromisoformat(REFINEMENT_CODE_DATE)


# --------------------------------------------------------------------------- versions
async def _document_or_none(task: dict):
    """Decoded document (cached after the first call), or ``None`` when it cannot be loaded."""
    try:
        return await quick_refine.load_document(task["uuid"])
    except Exception:  # noqa: BLE001 - the listing works without it (no v1 peak count)
        logger.debug("Could not load the document of %s", task.get("uuid"), exc_info=True)
        return None


def _resolve_origin(record: dict, by_number: dict[int, dict]) -> dict | None:
    """``origin`` with a base that still exists.

    Older full fits recorded the quick check they replaced as their base, and the quick check
    is deleted when the full fit is stored. A base pointing at a deleted version is replaced by
    that version's own base (followed through chains); what the replaced quick check added or
    removed is carried over. ``by_number`` maps version numbers to records, tombstones included.
    """
    origin = record.get("origin")
    if not isinstance(origin, dict):
        return origin
    resolved = dict(origin)
    seen: set[int] = set()
    while True:
        try:
            selection = versions.parse_selection(resolved.get("base"))
        except HTTPException:
            return resolved
        if not versions.is_version(selection):
            return resolved
        number = selection[1]
        base = by_number.get(number)
        if base is not None and not base.get("deleted"):
            return resolved
        if number in seen or base is None:
            resolved["base"] = "best"
            return resolved
        seen.add(number)
        previous = base.get("origin") or {}
        if record.get("replaces") == number:
            for field in ("added", "removed"):
                merged = list(previous.get(field) or [])
                merged += [stem for stem in resolved.get(field) or [] if stem not in merged]
                resolved[field] = merged
        base_of_base = previous.get("base")
        resolved["base"] = "best" if base_of_base is None or base_of_base == "" else base_of_base


def _version_records(task_uuid: str) -> tuple[list[dict], dict[int, dict]]:
    """Live version records and a number -> record map that includes tombstones."""
    stored = versions.list_versions(task_uuid, include_deleted=True)
    by_number = {item.get("index"): item for item in stored}
    return [item for item in stored if not item.get("deleted")], by_number


@router.get("/task/{task_id}/versions")
async def list_task_versions(task_id: int):
    task = _completed_task(task_id)
    quick_refine.maybe_warm(task)
    # Load (not just peek at) the document so v1 gets its unexplained-peak count on a cold server.
    document = await _document_or_none(task)
    records, by_number = _version_records(task["uuid"])
    return {
        "task_id": task_id,
        "adopted": _adopted(task),
        "baseline_stale": _baseline_stale(task, records),
        "versions": [_original_entry(task, document)] + [
            {**_version_entry(r), "origin": _resolve_origin(r, by_number)} for r in records
        ],
    }


@router.get("/task/{task_id}/versions/{number}")
async def get_task_version(task_id: int, number: int):
    task = _completed_task(task_id)
    if number < 1:
        raise HTTPException(404, "Version not found")
    if number == 1:
        entry = _original_entry(task, await _document_or_none(task))
        return {**entry, "effective_phase_params": None, "final_result": None, "use_task_payload": True}
    record = versions.load_version(task["uuid"], number, light=True)
    _, by_number = _version_records(task["uuid"])
    return {
        **_version_entry(record),
        "origin": _resolve_origin(record, by_number),
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
    from dara.server.api_router import _phase_identity, unexplained_count

    quality = summary.get("fit_quality") or {}
    weights = summary.get("weight_percent") or {}
    return {
        "sel": versions.format_selection(selection),
        "label": meta.get("label"),
        "refine_mode": (meta.get("record") or {}).get("refine_mode") or "full",
        "rwp": _finite(summary.get("rwp", quality.get("rwp"))),
        "gof": _finite(quality.get("gof")),
        "chi_squared": _finite(quality.get("chi_squared")),
        "weight_percent": weights,
        # reduced formula + space group per phase, so the UI lines up the same compound
        # refined from different database records
        "phase_identity": {name: _phase_identity(name) for name in weights},
        "mass_balance": summary.get("mass_balance"),
        "n_unexplained": unexplained_count(summary.get("peak_diagnostics")),
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
        # a hypothesis merged into another displayed entry (other phase order or database record)
        from dara.server.api_router import (
            _b1_spec_resolver, _classified_peak_diagnostics, _counts_like, _fit_quality,
            _phase_refinement_details, _weight_percent, fit_cautions,
        )

        # the same cautions as a displayed entry gets in summarize_document
        quality = _fit_quality(refinement)
        diagnostics = _classified_peak_diagnostics(document, refinement, hint_mode="off")
        return {
            "rwp": refinement.lst_data.rwp,
            "fit_quality": quality,
            "peak_diagnostics": diagnostics,
            "weight_percent": _weight_percent(refinement),
            "cautions": fit_cautions(
                quality, _phase_refinement_details(refinement, _b1_spec_resolver(task, document)),
                diagnostics,
                counts_like=_counts_like(document),
            ),
        }
    record = meta["record"]
    return record.get("summary") or {}


@router.get("/task/{task_id}/fit-compare")
async def fit_compare(task_id: int, a: str = Query("best"), b: str = Query(...)):
    sel_a, sel_b = versions.parse_selection(a), versions.parse_selection(b)
    if sel_a == sel_b:
        raise HTTPException(400, "Select two different fits to compare.")
    task = _completed_task(task_id)
    document = await quick_refine.load_document(task["uuid"])
    ref_a, meta_a = versions.resolve_refinement(document, task, sel_a)
    ref_b, meta_b = versions.resolve_refinement(document, task, sel_b)

    from dara.server.candidate_comparison_router import compare_fits, masked

    fits = compare_fits(ref_a, ref_b, meta_a.get("refinement_params"), meta_b.get("refinement_params"))
    valid, observed = fits["valid"], fits["observed"]
    calc_a, calc_b = fits["a_calculated"], fits["b_calculated"]

    loop = asyncio.get_running_loop()
    summary_a, summary_b = await asyncio.gather(
        loop.run_in_executor(None, _side_summary, task, document, sel_a, meta_a, ref_a),
        loop.run_in_executor(None, _side_summary, task, document, sel_b, meta_b, ref_b),
    )
    rwp_a, rwp_b = float(ref_a.lst_data.rwp), float(ref_b.lst_data.rwp)
    delta = rwp_b - rwp_a
    better = "similar" if abs(delta) < SIMILAR_RWP_POINTS else ("a" if delta > 0 else "b")
    verdict = {"rwp_delta": round(delta, 6), "better": better}
    if any(versions.is_quick_check(meta.get("record")) for meta in (meta_a, meta_b)):
        # A quick check holds the base phases fixed: its Rwp is not comparable with a full fit.
        from dara.server.review_router import QUICK_CHECK_MESSAGE

        verdict.update(better="similar", note=QUICK_CHECK_MESSAGE)
    return {
        "task_id": task_id,
        "a": _lite(sel_a, meta_a, {**summary_a, "rwp": rwp_a}),
        "b": _lite(sel_b, meta_b, {**summary_b, "rwp": rwp_b}),
        "x": fits["x"].tolist(),
        "observed": masked(observed, valid),
        "a_calculated": masked(calc_a, valid),
        "b_calculated": masked(calc_b, valid),
        "a_residual": masked(observed - calc_a, valid),
        "b_residual": masked(observed - calc_b, valid),
        "a_only": fits["a_only"],
        "b_only": fits["b_only"],
        "common": fits["common"],
        "regions": fits["regions"],
        "verdict": verdict,
        "wavelength_angstrom": _wavelength(document),
    }
