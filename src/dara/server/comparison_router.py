"""Read-only access to measured patterns for comparing completed analyses."""

from __future__ import annotations

import ntpath

import numpy as np
from fastapi import APIRouter, HTTPException
from monty.json import MontyDecoder

from dara.server.utils import get_result_store, get_worker_store

router = APIRouter(prefix="/api")


def _pattern_arrays(pattern):
    """Decode only the pattern, not a whole job with database credentials."""
    if isinstance(pattern, dict):
        pattern = MontyDecoder().process_decoded(pattern)
    if isinstance(pattern, dict):
        angles = pattern.get("angles")
        intensities = pattern.get("intensities")
    else:
        angles = getattr(pattern, "angles", None)
        intensities = getattr(pattern, "intensities", None)
    if angles is None or intensities is None:
        raise ValueError("The stored measured pattern is unavailable.")
    angles = np.asarray(angles, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    if angles.ndim != 1 or intensities.ndim != 1 or angles.size != intensities.size:
        raise ValueError("The stored pattern has inconsistent data columns.")
    finite = np.isfinite(angles) & np.isfinite(intensities)
    dropped = int(angles.size - np.count_nonzero(finite))
    angles, intensities = angles[finite], intensities[finite]
    if angles.size < 2:
        raise ValueError("The stored pattern has fewer than two finite points.")
    order = np.argsort(angles, kind="stable")
    return angles[order].tolist(), intensities[order].tolist(), dropped


def _input_pattern(job):
    """Handle both keyword and positional calls to PhaseSearchMaker.make."""
    serialized = job.get("job", {})
    kwargs = serialized.get("function_kwargs", {}) or {}
    pattern = kwargs.get("xrd_data")
    if pattern is None:
        args = serialized.get("function_args", []) or []
        if args:
            pattern = args[0]
    return pattern, kwargs.get("search_kwargs", {}) or {}


@router.get("/task/{task_id}/pattern")
async def measured_pattern(task_id: int):
    """Return original measured intensities, never calculated fit curves."""
    with get_worker_store() as store:
        job = store.query_one({"index": task_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    pattern, settings = _input_pattern(job)
    source = "original input"
    if pattern is None:
        with get_result_store() as store:
            stored = store.query_one({"uuid": job["uuid"]})
        output = (stored or {}).get("output", {})
        if isinstance(output, dict):
            pattern = output.get("xrd_data")
            settings = output.get("search_kwargs", {}) or settings
        else:
            pattern = getattr(output, "xrd_data", None)
            settings = getattr(output, "search_kwargs", None) or settings
        source = "stored result input"
    if pattern is None:
        raise HTTPException(status_code=404, detail="No measured pattern is stored for this task.")
    try:
        angles, intensities, dropped = _pattern_arrays(pattern)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    wavelength = settings.get("wavelength")
    if wavelength is not None:
        wavelength = str(wavelength)
    return {
        "task_id": task_id,
        "name": ntpath.basename(str(job.get("job", {}).get("name") or f"Task {task_id}")),
        "angles": angles,
        "intensities": intensities,
        "wavelength": wavelength,
        "wavelength_angstrom": _wavelength_angstrom(settings.get("wavelength")),
        "source": source,
        "dropped_points": dropped,
    }


def _wavelength_angstrom(spec):
    """Kα1 wavelength (Å) for hover d-spacings; None when not recorded."""
    if spec is None:
        return None
    try:
        from dara.search.candidate_cost import wavelength_angstrom

        return round(wavelength_angstrom(spec), 6)
    except Exception:
        return None
