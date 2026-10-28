from __future__ import annotations

import asyncio
import csv
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
import zipfile
from ast import literal_eval
from collections import OrderedDict
from dataclasses import dataclass, field
from copy import deepcopy
from functools import lru_cache
from io import BytesIO, StringIO
from pathlib import Path
from traceback import print_exc
from typing import Annotated, Optional

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from monty.serialization import MontyDecoder
from pydantic import BaseModel
from pymatgen.core import Composition, Element

from dara import structure_db
from dara.cif import Cif
from dara.analysis_range import validate_analysis_range
from dara.jobs import PhaseSearchMaker
from dara.plot import peaks_in_fit_range, spacegroup_symbol, visualize
from dara.server.data_quality import intensities_look_like_counts, pattern_quality
from dara.server.failure_summary import summarize_failure
from dara.server import confidence as confidence_module
from dara.server import peak_significance
from dara.server.fit_cautions import (
    broad_background_caution,
    cached_lattice_flags,
    displacement_caution,
    fit_cautions,
    lattice_bound_flags,
    lattice_range_resolver,
    search_cautions,
)
from dara.server import mass_balance as mass_balance_module
from dara.server.mass_balance import split_precursor_entries
from dara.server.table_export import (
    MULTI_TASK_EXTRA_COLUMNS,
    TABLE_COLUMNS,
    multi_task_rows,
    rows_to_csv,
    task_table_rows,
)
from dara.server.task_summary import (
    SUMMARY_SCHEMA,
    compact_summary,
    SUMMARY_REFRESH_GAVE_UP,
    refresh_stale_summary,
    runtime_seconds,
)
from dara.server.utils import convert_to_local_tz, get_result_store, get_worker_store
from dara.server.worker import (
    add_job_to_queue,
)
from dara.structure_db import CODDatabase, MaterialsProjectDatabase
from dara.utils import (
    get_compositional_clusters,
    get_head_of_compositional_cluster,
)
from dara.xrd import (
    get_xrdml_metadata,
    load_pattern,
    recommend_xrdml_instrument_profile,
)

logger = logging.getLogger("dara.server.api")

router = APIRouter(prefix="/api")


def _available_instrument_profiles() -> list[str]:
    device_dir = Path(__file__).parents[1] / "data" / "BGMN-Templates" / "Devices"
    return sorted(path.stem for path in device_dir.glob("*.geq"))


@lru_cache(maxsize=256)
def _spacegroup_symbol(number) -> str:
    """Hermann-Mauguin symbol of a space-group number (building the group is slow)."""
    return spacegroup_symbol(number)


def _refined_phase_metadata(refinement) -> dict[str, dict]:
    """Return user-facing crystallographic labels for refined phases."""
    metadata = {}
    for phase_name, phase_result in refinement.lst_data.phases_results.items():
        spacegroup_number = phase_result.spacegroup_no
        try:
            spacegroup_symbol = _spacegroup_symbol(spacegroup_number)
        except (TypeError, ValueError):
            spacegroup_symbol = phase_result.hermann_mauguin
        metadata[phase_name] = {
            "spacegroup_number": spacegroup_number,
            "spacegroup_symbol": spacegroup_symbol,
        }
    return metadata


def _value_and_esd(value) -> tuple[float | None, float | None]:
    """Return a BGMN value and its estimated standard deviation, when present."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return float(value[0]), float(value[1])
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value), None
    return None, None


def _fit_quality(refinement) -> dict:
    """Expose refinement statistics without inventing unavailable metrics."""
    fit = refinement.lst_data
    rexp = fit.rexp
    gof = fit.rwp / rexp if rexp else None
    angles = np.asarray(refinement.plot_data.x, dtype=float)
    angles = angles[np.isfinite(angles)]
    return {
        "rp": fit.rp,
        "rpb": fit.rpb,
        "r": fit.r,
        "rwp": fit.rwp,
        "rexp": rexp,
        "gof": gof,
        "chi_squared": gof**2 if gof is not None else None,
        "durbin_watson": fit.d,
        "one_minus_rho": fit.rho,
        "iterations": fit.num_steps,
        "two_theta_min": float(angles.min()) if angles.size else None,
        "two_theta_max": float(angles.max()) if angles.size else None,
        "data_points": int(angles.size),
    }


def _quantitative_phase_details(refinement) -> dict[str, dict]:
    """Return normalized phase fractions and propagated BGMN e.s.d. values."""
    raw = {
        name: _value_and_esd(phase.gewicht)
        for name, phase in refinement.lst_data.phases_results.items()
    }
    total = sum(value for value, _ in raw.values() if value is not None)
    if not total:
        return {}

    all_have_esd = all(error is not None for _, error in raw.values())
    details = {}
    for name, (value, error) in raw.items():
        if value is None:
            continue
        normalized_error = None
        if all_have_esd:
            variance = (((total - value) / total**2) * error) ** 2
            variance += sum(
                ((value / total**2) * other_error) ** 2
                for other_name, (_, other_error) in raw.items()
                if other_name != name
            )
            normalized_error = variance**0.5 * 100
        details[name] = {
            "weight_percent": value / total * 100,
            "weight_esd_percent": normalized_error,
        }
    return details


def _phase_refinement_details(refinement) -> dict[str, dict]:
    """Return crystallographic and profile values available in the BGMN output."""
    quantitative = _quantitative_phase_details(refinement)
    output = {}
    for name, phase in refinement.lst_data.phases_results.items():
        bgmn_unit = str(getattr(phase, "unit", "NM"))
        lattice = {}
        for key in ("a", "b", "c", "alpha", "beta", "gamma"):
            value, esd = _value_and_esd(getattr(phase, key, None))
            if value is None:
                continue
            unit = "deg" if key in {"alpha", "beta", "gamma"} else "Å"
            factor = 1 if unit == "deg" or bgmn_unit.upper() != "NM" else 10
            lattice[key] = {
                "value": value * factor,
                "esd": esd * factor if esd is not None else None,
                "unit": unit,
            }

        grain_sizes = []
        for key, raw_value in (phase.model_extra or {}).items():
            if not key.lower().startswith("grainsize"):
                continue
            value, esd = _value_and_esd(raw_value)
            if value is not None:
                grain_sizes.append({
                    "direction": key,
                    "value": value,
                    "esd": esd,
                    "unit": "nm" if bgmn_unit.upper() == "NM" else bgmn_unit,
                })

        output[name] = {
            **quantitative.get(name, {}),
            "lattice_parameters": lattice,
            "xray_density": phase.xray_density,
            "rphase": phase.rphase,
            "grain_sizes": grain_sizes,
        }
    try:
        # Orientation-refined phases report only a mean GEWICHT; take their weight esd from
        # BGMN's Q<phase> goals so every consumer (task page, tables, summary) agrees.
        return confidence_module.fill_weight_esd(refinement, output)
    except Exception:
        logger.debug("Weight esd could not be filled from Q goals", exc_info=True)
        return output


def _peak_diagnostics(refinement, missing=None, extra=None, mismatch=None) -> dict:
    """Return candidate diagnostic arrays and their display counts."""
    metrics = getattr(refinement, "refinement_metrics", None)
    if metrics is not None:
        missing = metrics.missing_peaks
        extra = metrics.extra_peaks
        mismatch = metrics.intensity_mismatch_peaks

    def as_list(value):
        if value is None:
            return None
        return peaks_in_fit_range(value, refinement.plot_data.x).tolist()

    values = {
        "missing": as_list(missing),
        "extra": as_list(extra),
        "intensity_mismatch": as_list(mismatch),
    }
    return {
        **values,
        "counts": {key: len(value or []) for key, value in values.items()},
    }


# ---------------------------------------------------------------------------
# size / signal-to-noise aware unexplained-peak classification
# ---------------------------------------------------------------------------
_REFERENCE_MEMO_MAX = 512
_REFERENCE_MEMO: "OrderedDict[tuple, tuple | None]" = OrderedDict()
_REFERENCE_MEMO_LOCK = threading.Lock()


@dataclass
class _PeakContext:
    """Per-document inputs of the missing-peak classification (built once)."""

    document: object
    wavelength: dict | None
    counts_like: bool | None
    range_params: dict
    hint_mode: str
    job_key: object = None
    _cod: object = field(default=None, repr=False)
    _cod_done: bool = field(default=False, repr=False)

    def measured_range(self, refinement=None) -> tuple[float, float] | None:
        """Angular range of the measurement (falls back to the refinement's profile)."""
        for source in (
            lambda: self.document.xrd_data.angles,
            lambda: refinement.plot_data.x,
            lambda: self.document.final_result.plot_data.x,
        ):
            try:
                values = np.asarray(source(), dtype=float)
                values = values[np.isfinite(values)]
                if values.size:
                    return float(values.min()), float(values.max())
            except Exception:
                continue
        return None

    def cod_patterns(self, refinement=None) -> list | None:
        """Cached COD reference patterns (one lookup per document)."""
        if self._cod_done:
            return self._cod
        window = self.measured_range(refinement)
        if self.wavelength is None or window is None:
            return None
        elements = list(getattr(self.document, "elements", None) or [])
        precursors = list(getattr(self.document, "precursors", None) or [])
        patterns = peak_significance.cod_reference_patterns(
            elements, precursors, self.wavelength["alpha1_a"], window, mode=self.hint_mode
        )
        if patterns is not None or self.hint_mode != "background":
            self._cod, self._cod_done = patterns, True
        return patterns


def _peak_context(document, hint_mode: str | None = None, job_key=None) -> _PeakContext:
    """Wavelength, count-type and analysis-range inputs shared by all candidates."""
    settings = getattr(document, "search_kwargs", None) or {}
    refinement_params = settings.get("refinement_params") or {}
    range_params = {}
    if isinstance(refinement_params, dict):
        range_params = {
            key: refinement_params[key]
            for key in ("wmin", "wmax", "excluded_ranges")
            if refinement_params.get(key) is not None
        }
    return _PeakContext(
        document=document,
        wavelength=peak_significance.resolve_wavelength(settings.get("wavelength", "Cu")),
        counts_like=_counts_like(document),
        range_params=range_params,
        hint_mode=hint_mode or peak_significance.hint_mode(),
        job_key=job_key if job_key is not None else id(document),
    )


_NOT_SIMULATED = object()


def _reference_key(ctx: _PeakContext, stem: str, window) -> tuple:
    return (
        ctx.job_key, stem, round(ctx.wavelength["alpha1_a"], 5),
        round(window[0], 1), round(window[1], 1),
    )


def _simulated_reference(cif, stem: str, ctx: _PeakContext, window, *, compute: bool = True):
    """Simulated ``(two_theta, intensity)`` of one reference CIF.

    With ``compute=False`` only the memo is consulted and ``_NOT_SIMULATED`` is returned for
    a CIF that has not been simulated yet. ``None`` means "cannot be simulated".
    """
    key = _reference_key(ctx, stem, window)
    with _REFERENCE_MEMO_LOCK:
        if key in _REFERENCE_MEMO:
            _REFERENCE_MEMO.move_to_end(key)
            return _REFERENCE_MEMO[key]
    if not compute:
        return _NOT_SIMULATED
    result = None
    try:
        from dara.search.prefilter import simulate_structure_reflections

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = cif.to_structure()
            if structure.volume <= peak_significance.HINT_MAX_VOLUME_A3:
                two_theta, intensity, _ = simulate_structure_reflections(
                    structure, ctx.wavelength["alpha1_a"], window
                )
                result = (np.asarray(two_theta, float), np.asarray(intensity, float))
    except Exception:
        logger.debug("Reference %s could not be simulated", stem, exc_info=True)
    with _REFERENCE_MEMO_LOCK:
        _REFERENCE_MEMO[key] = result
        while len(_REFERENCE_MEMO) > _REFERENCE_MEMO_MAX:
            _REFERENCE_MEMO.popitem(last=False)
    return result


def _simulate_references_in_background(todo: list, ctx: _PeakContext, window) -> None:
    """Fill the reference memo for ``todo`` ``[(cif, stem)]`` on the low-priority worker."""

    def job() -> None:
        for cif, stem in todo:
            if peak_significance.stop_requested():
                return
            _simulated_reference(cif, stem, ctx, window)
            peak_significance.yield_cpu()

    key = ("references", ctx.job_key, round(ctx.wavelength["alpha1_a"], 5),
           round(window[0], 1), round(window[1], 1))
    peak_significance.submit_background(key, job)


def _reference_patterns(document, refinement, ctx: _PeakContext) -> list[dict]:
    """Simulated reference patterns: candidate CIFs, input CIFs, then the COD cache.

    In ``background`` mode no structure is simulated on the calling thread: references that
    are not memoized yet are simulated by the background worker and appear in the hints of a
    later request. ``sync`` mode (benchmark, offline) simulates them immediately.
    """
    patterns: list[dict] = []
    window = ctx.measured_range(refinement)
    if ctx.wavelength is None or window is None:
        return patterns
    sources = [
        (cif, "candidate")
        for entry in getattr(document, "results", None) or []
        for cif in _flatten_cifs(entry[0])
    ] + [
        (cif, "input")
        for cif in _flatten_cifs(list(getattr(document, "input_cifs", None) or []))
    ]
    background = ctx.hint_mode == "background"
    seen: set[str] = set()
    todo: list = []
    for cif, source in sources:
        stem = Path(str(getattr(cif, "filename", "") or "")).stem
        if not stem or stem in seen:
            continue
        seen.add(stem)
        simulated = _simulated_reference(cif, stem, ctx, window, compute=not background)
        if simulated is _NOT_SIMULATED:
            todo.append((cif, stem))
            continue
        if simulated is None:
            continue
        formula, number = confidence_module.phase_identity(stem)
        patterns.append({
            "name": stem,
            "formula": formula or stem.split("_")[0],
            "spacegroup_number": number,
            "spacegroup_symbol": _spacegroup_symbol(number) if number else None,
            "source": source,
            "popularity": 1,
            "two_theta": simulated[0],
            "intensity": simulated[1],
        })
    if todo:
        _simulate_references_in_background(todo, ctx, window)
    patterns.extend(ctx.cod_patterns(refinement) or [])
    return patterns


def _classified_peak_diagnostics(
    document, refinement, missing=None, extra=None, mismatch=None, *,
    hint_mode: str | None = None, context: _PeakContext | None = None,
) -> dict:
    """``_peak_diagnostics`` plus the size/S-N classification of the missing peaks.

    The legacy keys are unchanged. The new keys (``missing_classified``,
    ``counts_by_class``, ``missing_significance``, ``trace_hints``) are omitted when the
    refinement profile is not available (old payloads), so consumers fall back to counts.
    ``hint_mode`` ("off", "background", "sync") overrides the context's trace-hint mode;
    "off" skips every reference simulation.
    """
    diag = _peak_diagnostics(refinement, missing, extra, mismatch)
    try:
        ctx = context or _peak_context(document, hint_mode)
        mode = hint_mode or ctx.hint_mode
        classified = peak_significance.classify_missing_peaks(
            refinement, diag["missing"], wavelength=ctx.wavelength,
            counts_like=ctx.counts_like, range_params=ctx.range_params,
        )
        if classified is None:
            return diag
        has_targets = any(
            entry["class"] in ("minor", "significant")
            for entry in classified["missing_classified"]
        )
        if has_targets and mode != "off" and ctx.wavelength is not None:
            if mode != ctx.hint_mode:
                ctx = _PeakContext(
                    document=ctx.document, wavelength=ctx.wavelength,
                    counts_like=ctx.counts_like, range_params=ctx.range_params,
                    hint_mode=mode, job_key=ctx.job_key,
                )
            patterns = _reference_patterns(document, refinement, ctx)
            classified = peak_significance.attach_trace_hints(classified, refinement, patterns)
        return {**diag, **classified}
    except Exception:
        logger.debug("Missing-peak classification failed", exc_info=True)
        return diag


def _parse_string_list(value: str | None) -> list[str]:
    """Accept JSON/Python lists or a comma-separated string from old clients."""
    if not value:
        return []
    try:
        parsed = literal_eval(value)
    except (ValueError, SyntaxError):
        parsed = value.split(",")
    if isinstance(parsed, str):
        parsed = parsed.split(",")
    if not isinstance(parsed, (list, tuple, set)):
        raise HTTPException(status_code=400, detail="Expected a comma-separated list")
    return [str(item).strip() for item in parsed if str(item).strip()]


@router.get("/instrument-profiles")
async def instrument_profiles():
    """Return the instrument profiles actually bundled with this server."""
    from dara.instrument_profiles import registered_profiles

    custom = registered_profiles()
    return {"profiles": _available_instrument_profiles() + list(custom), "details": custom}


@router.post("/pattern/inspect")
async def inspect_pattern(pattern_file: Annotated[UploadFile, File()]):
    """Validate an uploaded pattern and suggest XRDML acquisition settings."""
    from dara.filename_chemistry import suggest_filename_chemistry

    filename = Path(pattern_file.filename or "pattern").name
    try:
        with tempfile.TemporaryDirectory() as pattern_temp_dir:
            temp_path = Path(pattern_temp_dir) / filename
            with open(temp_path, "wb") as temp_file:
                shutil.copyfileobj(pattern_file.file, temp_file)
            pattern = load_pattern(temp_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")

    angles = np.asarray(pattern.angles, dtype=float)
    finite_angles = angles[np.isfinite(angles)]
    metadata = {}
    suggestion = None
    if temp_path.suffix.lower() == ".xrdml" and getattr(pattern, "xrdml_dict", None):
        metadata = get_xrdml_metadata(pattern.xrdml_dict)
        suggestion = recommend_xrdml_instrument_profile(metadata)
        available = set(_available_instrument_profiles())
        if suggestion and suggestion["profile"] not in available:
            suggestion = None

    # B's reader may add load warnings and an intensity unit; older readers do not.
    intensity_unit = getattr(pattern, "intensity_unit", None)
    load_warnings = [str(item) for item in (getattr(pattern, "load_warnings", None) or [])]
    try:
        quality_checks, quality_stats = pattern_quality(
            angles, np.asarray(pattern.intensities, dtype=float), intensity_unit
        )
    except Exception:
        quality_checks, quality_stats = [], {}

    return {
        "filename": filename,
        "format": temp_path.suffix.lower().lstrip("."),
        "two_theta_min": float(finite_angles.min()) if finite_angles.size else None,
        "two_theta_max": float(finite_angles.max()) if finite_angles.size else None,
        "data_points": int(angles.size),
        "metadata": metadata,
        "profile_suggestion": suggestion,
        "recommended_wavelength": metadata.get("radiation"),
        "filename_chemistry": suggest_filename_chemistry(filename),
        "intensity_unit": intensity_unit,
        "load_warnings": load_warnings,
        "quality_checks": quality_checks,
        "quality_stats": quality_stats,
    }


def _saved_task_input(task_id: int) -> tuple[dict, dict, object]:
    """Restore only a stored pattern/input snapshot, never execute a stored job."""
    with get_worker_store() as worker_store:
        record = worker_store.query_one({"index": task_id})
    if record is None:
        raise HTTPException(status_code=404, detail="Source task not found")
    raw_job = record.get("job", {})
    kwargs = deepcopy(raw_job.get("function_kwargs", {}))
    raw_pattern = kwargs.get("xrd_data")
    if raw_pattern is None and raw_job.get("function_args"):
        raw_pattern = raw_job["function_args"][0]
    if raw_pattern is None:
        raise HTTPException(status_code=409, detail="Original pattern is not available for this task")
    try:
        pattern = MontyDecoder().process_decoded(raw_pattern)
        if not hasattr(pattern, "angles") or not hasattr(pattern, "intensities"):
            raise ValueError("Not an XRD pattern")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="The saved original pattern could not be restored") from exc
    return record, kwargs, pattern


def _rerun_settings(record: dict, kwargs: dict, pattern) -> dict:
    """Return an explicit UI allowlist; credentials and private DB config stay server-side."""
    raw_job = record.get("job", {})
    saved = raw_job.get("metadata", {}).get("submission_settings", {})
    search = kwargs.get("search_kwargs") or {}
    refine = search.get("refinement_params") or {}
    predict = kwargs.get("predict_kwargs") or {}
    context = kwargs.get("context") or {}
    dbs = kwargs.get("cif_dbs") or []
    classes = {db.get("@class") for db in dbs if isinstance(db, dict)}
    mp_db = next((db for db in dbs if isinstance(db, dict) and db.get("@class") == "MaterialsProjectDatabase"), {})
    mode = saved.get("analysis_mode", kwargs.get("analysis_mode", "reaction" if predict else "elemental"))
    if mode not in {"elemental", "reaction", "known_mixture"}:
        mode = "elemental"
    settings = {
        "user": record.get("user", ""),
        "analysis_mode": mode,
        "elements": kwargs.get("elements") or context.get("elements") or [],
        "precursor_formulas": (
            saved.get("precursor_input") or kwargs.get("precursors") or context.get("precursors") or []
        ),
        "instrument_profile": search.get("instrument_profile", "Aeris-fds-Pixcel1d-Medipix3"),
        "wavelength": search.get("wavelength", "Cu"),
        "temperature": predict.get("temp", 1073) - 273,
        "atmosphere": saved.get("atmosphere", {"O_air": "air", "O": "oxygen"}.get(predict.get("open_elem"), "inert")),
        "use_cod": "CODDatabase" in classes or (not dbs and not kwargs.get("cifs") and not kwargs.get("additional_cifs")),
        "use_mp": "MaterialsProjectDatabase" in classes,
        "mp_e_hull_cutoff": kwargs.get("database_e_hull_filter", mp_db.get("e_hull_filter", 0.05)),
        "max_mp_structures": mp_db.get("max_structures", 100),
        "two_theta_min": refine.get("wmin"),
        "two_theta_max": refine.get("wmax"),
        "excluded_ranges": refine.get("excluded_ranges", []),
        # Old tasks ran without the pre-filter or limits: a faithful rerun keeps that.
        "candidate_prefilter": bool(kwargs.get("candidate_prefilter", False)),
        "screening_timeout_s": kwargs.get("screening_timeout_s"),
        "max_analysis_minutes": (
            kwargs["max_runtime_s"] / 60 if kwargs.get("max_runtime_s") else None
        ),
    }
    angles = np.asarray(pattern.angles, dtype=float)
    finite = angles[np.isfinite(angles)]
    return {
        "source_task_id": record["index"],
        "filename": saved.get("filename", raw_job.get("name", "saved_pattern.xy")),
        "settings": settings,
        "additional_cif_count": len(kwargs.get("additional_cifs") or kwargs.get("cifs") or []),
        "pattern": {
            "two_theta_min": float(finite.min()) if finite.size else None,
            "two_theta_max": float(finite.max()) if finite.size else None,
            "data_points": int(angles.size),
        },
    }


@router.get("/task/{task_id}/rerun-settings")
async def rerun_settings(task_id: int):
    return _rerun_settings(*_saved_task_input(task_id))


LAB_WAVELENGTHS_ANGSTROM = {
    "Cu": 1.540598, "Co": 1.789010, "Cr": 2.289760, "Fe": 1.936042, "Mo": 0.70930,
}
WAVELENGTH_TOLERANCE_ANGSTROM = 0.03


def _selected_wavelength_angstrom(wavelength: str) -> float | None:
    """Selected wavelength in Å: a lab target name, or a numeric value in nm."""
    text = str(wavelength).strip()
    try:
        return float(text) * 10
    except ValueError:
        return LAB_WAVELENGTHS_ANGSTROM.get(text.capitalize())


def _wavelength_mismatch(pattern, pattern_path: Path, wavelength: str) -> dict | None:
    """Describe a recorded-vs-selected wavelength disagreement for an XRDML upload."""
    if pattern_path.suffix.lower() != ".xrdml" or not getattr(pattern, "xrdml_dict", None):
        return None
    try:
        recorded = get_xrdml_metadata(pattern.xrdml_dict).get("k_alpha1_angstrom")
        selected = _selected_wavelength_angstrom(wavelength)
        if recorded is None or selected is None:
            return None
        recorded, selected = float(recorded), float(selected)
    except (TypeError, ValueError):
        return None
    if abs(recorded - selected) <= WAVELENGTH_TOLERANCE_ANGSTROM:
        return None
    return {
        "code": "wavelength_mismatch",
        "message": (
            f"The file records a wavelength of {recorded:.4f} Å, but {selected:.4f} Å "
            "is selected. A mismatch shifts all calculated peak positions."
        ),
        "recorded_angstrom": recorded,
        "selected_angstrom": selected,
    }


def build_submission_job(
    pattern,                                   # dara.xrd.XRDData (already loaded)
    name: str,
    *,
    analysis_mode: str = "",
    precursor_formulas: list[str] | str = "[]",
    elements: list[str] | str = "[]",
    instrument_profile: str = "Aeris-fds-Pixcel1d-Medipix3",
    wavelength: str | float = "Cu",
    temperature: int = -1,
    atmosphere: str = "air",
    use_cod: bool = True,
    use_mp: bool = True,
    mp_e_hull_cutoff: float = 0.05,
    max_mp_structures: int = 100,
    use_rxn_predictor: bool | None = None,
    additional_cifs: list | None = None,       # list[dara.cif.Cif], already parsed
    library_cif_ids: list[str] | str = "[]",   # reference library ids
    two_theta_min: float | None = None,
    two_theta_max: float | None = None,
    excluded_ranges: str | list = "[]",
    refinement_controls: str | dict = "{}",
    original_kwargs: dict | None = None,       # restored kwargs of a source task (clone), else None
    source_task_id: int | None = None,
    candidate_prefilter: bool | None = None,   # None keeps the legacy behaviour (no pre-filter)
    screening_timeout_s: float | None = None,  # None keeps the legacy 1200 s per reference
    max_analysis_minutes: float | None = None,  # None means no analysis time limit
):
    """Validate submission fields and return an unqueued jobflow Job.

    Raises ``HTTPException(400)`` on invalid input.
    """
    try:
        original_kwargs = original_kwargs if original_kwargs is not None else {}
        additional_cifs = list(additional_cifs or [])
        range_params = validate_analysis_range(
            pattern.angles, two_theta_min, two_theta_max, excluded_ranges
        )

        precursor_formulas = (
            list(precursor_formulas)
            if isinstance(precursor_formulas, (list, tuple))
            else _parse_string_list(precursor_formulas)
        )
        elements = (
            list(elements) if isinstance(elements, (list, tuple)) else _parse_string_list(elements)
        )
        library_ids = (
            [str(item) for item in library_cif_ids]
            if isinstance(library_cif_ids, (list, tuple))
            else _parse_string_list(library_cif_ids)
        )

        # Compatibility with the original web/API contract.
        if not analysis_mode:
            analysis_mode = "reaction" if use_rxn_predictor else "elemental"
            if analysis_mode == "elemental" and not elements:
                elements, precursor_formulas = precursor_formulas, []
        if analysis_mode not in {"reaction", "elemental", "known_mixture"}:
            raise HTTPException(status_code=400, detail="Invalid analysis mode")
        # Optional amounts ("2 TiO2", "TiO2:0.2g") feed only the mass-balance check;
        # every existing consumer keeps receiving plain formulas.
        precursor_input = list(precursor_formulas)
        try:
            precursor_formulas, precursor_amounts = split_precursor_entries(precursor_formulas)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid precursor formulas: {exc}")

        if library_ids:
            try:
                from dara.server.reference_library_router import load_library_cifs
            except ImportError:
                raise HTTPException(
                    status_code=400,
                    detail="The reference CIF library is not available on this server.",
                )
            try:
                additional_cifs.extend(load_library_cifs(library_ids))
            except KeyError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown reference library CIF: {exc.args[0]}",
                )

        try:
            [Composition(p) for p in precursor_formulas]
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid precursor formulas")
        try:
            elements = sorted({str(Element(element)) for element in elements})
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid element symbols")

        if not elements and precursor_formulas:
            elements = sorted(
                {
                    str(element)
                    for formula in precursor_formulas
                    for element in Composition(formula).elements
                }
            )

        if analysis_mode in {"reaction", "known_mixture"} and not precursor_formulas:
            raise HTTPException(
                status_code=400,
                detail="This analysis mode requires precursor/known-phase formulas",
            )
        if analysis_mode == "elemental" and not elements:
            raise HTTPException(status_code=400, detail="Elemental mode requires elements")
        if not 0 <= mp_e_hull_cutoff <= 1:
            raise HTTPException(status_code=400, detail="MP E-hull cutoff must be 0 to 1 eV/atom")
        if not 1 <= max_mp_structures <= 1000:
            raise HTTPException(status_code=400, detail="MP structure limit must be 1 to 1000")
        if max_analysis_minutes is not None and not 5 <= max_analysis_minutes <= 720:
            raise HTTPException(
                status_code=400,
                detail="Maximum analysis time must be between 5 and 720 minutes",
            )
        if screening_timeout_s is not None and not 60 <= screening_timeout_s <= 1200:
            raise HTTPException(
                status_code=400,
                detail="Screening time limit must be between 60 and 1200 seconds",
            )

        try:
            wavelength = float(wavelength)
        except ValueError:
            pass

        cif_dbs = []
        if use_cod:
            cif_dbs.append(CODDatabase())
        if use_mp:
            try:
                import mp_api  # noqa: F401
            except ImportError:
                raise HTTPException(
                    status_code=400,
                    detail="Materials Project support is unavailable. Please install mp_api.",
                )
            if not os.environ.get("MP_API_KEY"):
                raise HTTPException(
                    status_code=400,
                    detail="MP_API_KEY is not set in the server environment.",
                )
            cif_dbs.append(
                MaterialsProjectDatabase(
                    max_structures=max_mp_structures,
                    e_hull_filter=mp_e_hull_cutoff,
                )
            )
        if not cif_dbs and not additional_cifs:
            raise HTTPException(status_code=400, detail="Select COD, MP, or upload reference CIFs")

        search_kwargs = {**(original_kwargs.get("search_kwargs") or {}), **{
            "instrument_profile": instrument_profile,
            "wavelength": wavelength,
        }}
        inherited_refine = dict(search_kwargs.get("refinement_params") or {})
        for key in ("wmin", "wmax", "excluded_ranges"):
            inherited_refine.pop(key, None)
        inherited_refine.update(range_params)
        if inherited_refine:
            search_kwargs["refinement_params"] = inherited_refine
        else:
            search_kwargs.pop("refinement_params", None)
        if range_params:
            search_kwargs["enable_angular_cut"] = False
        if original_kwargs.get("analysis_mode") == "known_mixture" and analysis_mode != "known_mixture":
            search_kwargs.pop("max_phases", None)
        if analysis_mode == "known_mixture":
            search_kwargs["max_phases"] = min(len(precursor_formulas), 5)
        maker_kwargs = {
            "xrd_data": pattern,
            "precursors": precursor_formulas,
            "elements": elements,
            "analysis_mode": analysis_mode,
            "database_e_hull_filter": mp_e_hull_cutoff,
            "cif_dbs": cif_dbs,
            "additional_cifs": additional_cifs,
            "additional_cif_params": (
                deepcopy(original_kwargs["additional_cif_params"])
                if original_kwargs.get("additional_cif_params") is not None
                else {"lattice_range": 0.05}
            ),
            "search_kwargs": search_kwargs,
        }
        # Run-time controls are sent explicitly by the UI and never inherited from a
        # source task; omitted when None so API/benchmark jobs stay on the legacy path.
        if candidate_prefilter is not None:
            maker_kwargs["candidate_prefilter"] = bool(candidate_prefilter)
        if screening_timeout_s is not None:
            maker_kwargs["screening_timeout_s"] = float(screening_timeout_s)
        if max_analysis_minutes is not None:
            maker_kwargs["max_runtime_s"] = float(max_analysis_minutes) * 60
        if original_kwargs.get("final_refinement_params") is not None:
            maker_kwargs["final_refinement_params"] = deepcopy(original_kwargs["final_refinement_params"])
        from dara.refinement_controls import RefinementControls, apply_controls

        if isinstance(refinement_controls, dict):
            controls = RefinementControls.model_validate(refinement_controls)
        else:
            controls = RefinementControls.model_validate_json(refinement_controls)
        maker_kwargs["final_refinement_params"] = apply_controls(
            maker_kwargs.get("final_refinement_params"), controls)
        maker_kwargs["final_refinement_overrides"] = {
            **(original_kwargs.get("final_refinement_overrides") or {}),
            **apply_controls({}, controls),
        }

        if analysis_mode == "reaction":
            if not use_mp:
                raise HTTPException(
                    status_code=400,
                    detail="Reaction mode requires Materials Project",
                )
            if temperature < 27 or temperature > 1727:
                raise HTTPException(
                    status_code=400,
                    detail="Reaction temperature must be between 27 and 1727 °C",
                )
            open_element = {"air": "O_air", "oxygen": "O"}.get(atmosphere)
            job = PhaseSearchMaker(name=name, verbose=False, max_num_results=5).make(
                predict_kwargs={
                    "temp": temperature + 273,
                    "open_elem": open_element,
                    "e_hull_cutoff": mp_e_hull_cutoff,
                },
                **maker_kwargs,
            )
        else:
            job = PhaseSearchMaker(
                name=name, verbose=False, phase_predictor=None, max_num_results=5
            ).make(**maker_kwargs)
        job.metadata["submission_settings"] = {
            "filename": name, "analysis_mode": analysis_mode, "atmosphere": atmosphere,
            "source_task_id": source_task_id, "library_cif_ids": library_ids,
            "candidate_prefilter": candidate_prefilter,
            "screening_timeout_s": screening_timeout_s,
            "max_analysis_minutes": max_analysis_minutes,
        }
        if precursor_amounts:
            job.metadata["submission_settings"]["precursor_input"] = precursor_input
            job.metadata["submission_settings"]["precursor_amounts"] = precursor_amounts
        if instrument_profile.startswith("user-"):
            from dara.instrument_profiles import registered_profiles, registered_profile_path

            registered_profile_path(instrument_profile)
            job.metadata["instrument_profile_details"] = registered_profiles().get(instrument_profile)
        return job
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/submit")
async def submit(
    user: Annotated[str, Form()],
    pattern_file: Annotated[UploadFile | None, File()] = None,
    source_task_id: Annotated[int | None, Form()] = None,
    precursor_formulas: Annotated[str, Form()] = "[]",
    elements: Annotated[str, Form()] = "[]",
    analysis_mode: Annotated[str, Form()] = "",
    instrument_profile: Annotated[str, Form()] = "Aeris-fds-Pixcel1d-Medipix3",
    wavelength: Annotated[str, Form()] = "Cu",
    temperature: Annotated[int, Form()] = -1,
    atmosphere: Annotated[str, Form()] = "air",
    use_cod: Annotated[bool, Form()] = True,
    use_mp: Annotated[bool, Form()] = True,
    mp_e_hull_cutoff: Annotated[float, Form()] = 0.05,
    max_mp_structures: Annotated[int, Form()] = 100,
    use_rxn_predictor: Annotated[Optional[bool], Form()] = None,
    additional_phases: Annotated[list[UploadFile], File()] = None,
    reuse_additional_cifs: Annotated[bool, Form()] = True,
    two_theta_min: Annotated[float | None, Form()] = None,
    two_theta_max: Annotated[float | None, Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    refinement_controls: Annotated[str, Form()] = "{}",
    confirm_wavelength: Annotated[bool, Form()] = False,
    library_cif_ids: Annotated[str, Form()] = "[]",
    candidate_prefilter: Annotated[Optional[bool], Form()] = None,
    screening_timeout_s: Annotated[Optional[float], Form()] = None,
    max_analysis_minutes: Annotated[Optional[float], Form()] = None,
):
    try:
        original_kwargs = {}
        if source_task_id is not None:
            source_record, original_kwargs, pattern = _saved_task_input(source_task_id)
            name = _rerun_settings(source_record, original_kwargs, pattern)["filename"]
        if pattern_file is not None:
            name = Path(pattern_file.filename or "pattern.xy").name
            with tempfile.TemporaryDirectory() as pattern_temp_dir:
                temp_path = Path(pattern_temp_dir) / name
                with open(temp_path, "wb") as temp_file:
                    shutil.copyfileobj(pattern_file.file, temp_file)
                try:
                    pattern = load_pattern(temp_path)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"Could not read pattern: {exc}")
                if not confirm_wavelength:
                    mismatch = _wavelength_mismatch(pattern, temp_path, wavelength)
                    if mismatch:
                        raise HTTPException(status_code=400, detail=mismatch)
        elif source_task_id is None:
            raise HTTPException(status_code=400, detail="Upload an XRD pattern or select a source task")
        additional_cifs = []
        if source_task_id is not None and reuse_additional_cifs:
            additional_cifs = MontyDecoder().process_decoded(
                original_kwargs.get("additional_cifs") or original_kwargs.get("cifs") or []
            )
        if additional_phases:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir = Path(temp_dir)
                for phase in additional_phases:
                    phase_name = Path(phase.filename or "reference.cif").name
                    with open(temp_dir / phase_name, "wb") as f:
                        shutil.copyfileobj(phase.file, f)
                    cif_file = Cif.from_file(temp_dir / phase_name)
                    if not cif_file.data:
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid additional CIF file provided",
                        )
                    additional_cifs.append(cif_file)

        job = build_submission_job(
            pattern,
            name,
            analysis_mode=analysis_mode,
            precursor_formulas=precursor_formulas,
            elements=elements,
            instrument_profile=instrument_profile,
            wavelength=wavelength,
            temperature=temperature,
            atmosphere=atmosphere,
            use_cod=use_cod,
            use_mp=use_mp,
            mp_e_hull_cutoff=mp_e_hull_cutoff,
            max_mp_structures=max_mp_structures,
            use_rxn_predictor=use_rxn_predictor,
            additional_cifs=additional_cifs,
            library_cif_ids=library_cif_ids,
            two_theta_min=two_theta_min,
            two_theta_max=two_theta_max,
            excluded_ranges=excluded_ranges,
            refinement_controls=refinement_controls,
            original_kwargs=original_kwargs,
            source_task_id=source_task_id,
            candidate_prefilter=candidate_prefilter,
            screening_timeout_s=screening_timeout_s,
            max_analysis_minutes=max_analysis_minutes,
        )
        job_index = add_job_to_queue(job, user=user)
        return {"message": "submitted", "wf_id": job_index}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        print_exc()
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Submission estimate (candidate counts and run time before the job is queued)
# ---------------------------------------------------------------------------

MP_ESTIMATE_TIMEOUT_S = 15
MP_ESTIMATE_TTL_S = 600
_MP_ESTIMATE_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_MP_ESTIMATE_LOCKS: dict[tuple, threading.Lock] = {}
_MP_ESTIMATE_GUARD = threading.Lock()

# Fallbacks used when ``dara.search.candidate_cost`` is not available.
_COST_DEFAULTS = {
    "LIGHT_MAX": 400,
    "HEAVY_MAX": 1200,
    "DEFAULT_ASSUMED_REFLECTIONS": 300,
    "PREFILTER_MIN_CANDIDATES": 40,
    "PREFILTER_TYPICAL_KEEP_FRACTION": 0.85,
}
REACTION_ESTIMATE_CANDIDATES = 40
LONG_RUN_S = 1800
VERY_LONG_RUN_S = 5400
DEDUP_KEEP_FRACTION = 0.85


class EstimateRequest(BaseModel):
    analysis_mode: str = "elemental"
    elements: list[str] | str = []
    precursor_formulas: list[str] | str = []
    use_cod: bool = True
    use_mp: bool = True
    mp_e_hull_cutoff: float = 0.05
    max_mp_structures: int = 100
    candidate_prefilter: bool = False
    wavelength: str | float = "Cu"
    two_theta_max: float | None = None
    data_points: int | None = None
    max_analysis_minutes: float | None = None


def _candidate_cost_module():
    """The engine's cost model, or ``None`` while it is not installed."""
    try:
        return importlib.import_module("dara.search.candidate_cost")
    except (ImportError, AttributeError):
        return None


def _cost_constant(module, name: str):
    return getattr(module, name, _COST_DEFAULTS[name]) if module is not None else _COST_DEFAULTS[name]


def _mp_configured() -> bool:
    return bool(os.environ.get("MP_API_KEY")) and importlib.util.find_spec("mp_api") is not None


def _mp_rows_for_estimate(elements: list[str], cutoff: float, limit: int) -> list[dict]:
    """MP metadata rows for the estimate, cached for ``MP_ESTIMATE_TTL_S`` (failures are not)."""
    key = (tuple(sorted(elements)), cutoff, limit)
    with _MP_ESTIMATE_GUARD:
        lock = _MP_ESTIMATE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        cached = _MP_ESTIMATE_CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < MP_ESTIMATE_TTL_S:
            return cached[1]
        rows = MaterialsProjectDatabase(
            max_structures=limit, e_hull_filter=cutoff
        ).count_by_chemsys(
            list(key[0]), e_hull_filter=cutoff, limit=limit, timeout_s=MP_ESTIMATE_TIMEOUT_S
        )
        _MP_ESTIMATE_CACHE[key] = (time.monotonic(), rows)
        return rows


def _unique_formula_sg(pairs) -> int:
    return len({(str(formula), str(sg)) for formula, sg in pairs})


def _runtime_level(expected_s: float | None) -> str:
    if expected_s is None:
        return "unknown"
    if expected_s > VERY_LONG_RUN_S:
        return "very_long"
    if expected_s > LONG_RUN_S:
        return "long"
    return "ok"


def _reflection_summary(cost, mp_rows, wavelength, two_theta_max) -> tuple[dict, list[int]]:
    """Median reflection count and the number of very heavy MP structures."""
    summary = {"median": None, "very_heavy": 0, "source": "default"}
    counts: list[int] = []
    function = getattr(cost, "reflections_from_cell", None) if cost is not None else None
    if function is None or not mp_rows:
        return summary, counts
    for row in mp_rows:
        try:
            counts.append(
                int(
                    function(
                        row.get("volume"), row.get("spacegroup"), wavelength,
                        two_theta_max or 90, primitive=True,
                    )
                )
            )
        except Exception:
            continue
    if not counts:
        return summary, counts
    ordered = sorted(counts)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    heavy = _cost_constant(cost, "HEAVY_MAX")
    return {
        "median": round(median),
        "very_heavy": sum(count >= heavy for count in counts),
        "source": "mp",
    }, counts


def _runtime_estimate(cost, n_candidates, reflection_counts, data_points) -> tuple[dict | None, list[str]]:
    function = getattr(cost, "estimate_runtime_s", None) if cost is not None else None
    if function is None:
        return None, []
    try:
        from dara.hardware import detect_cpu_count

        workers = detect_cpu_count()
        estimate = function(n_candidates, reflection_counts, workers, data_points=data_points)
        return {
            "low_s": estimate["low_s"], "expected_s": estimate["expected_s"],
            "high_s": estimate["high_s"], "workers": workers,
        }, [str(item) for item in estimate.get("assumptions") or []]
    except Exception:
        logger.warning("Run-time estimate failed", exc_info=True)
        return None, []


async def _estimate_submission(request: EstimateRequest) -> dict:
    started = time.perf_counter()
    mode = request.analysis_mode
    if mode not in {"reaction", "elemental", "known_mixture"}:
        raise HTTPException(status_code=400, detail="Invalid analysis mode")
    precursors = (
        list(request.precursor_formulas)
        if isinstance(request.precursor_formulas, (list, tuple))
        else _parse_string_list(request.precursor_formulas)
    )
    elements = (
        list(request.elements)
        if isinstance(request.elements, (list, tuple))
        else _parse_string_list(request.elements)
    )
    try:
        precursors, _ = split_precursor_entries(precursors)
        [Composition(p) for p in precursors]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid precursor formulas")
    try:
        elements = sorted({str(Element(element)) for element in elements})
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid element symbols")
    if not elements and precursors:
        elements = sorted(
            {str(el) for formula in precursors for el in Composition(formula).elements}
        )
    if mode in {"reaction", "known_mixture"} and not precursors:
        raise HTTPException(
            status_code=400,
            detail="This analysis mode requires precursor/known-phase formulas",
        )
    if mode == "elemental" and not elements:
        raise HTTPException(status_code=400, detail="Elemental mode requires elements")
    if not 0 <= request.mp_e_hull_cutoff <= 1:
        raise HTTPException(status_code=400, detail="MP E-hull cutoff must be 0 to 1 eV/atom")
    if not 1 <= request.max_mp_structures <= 1000:
        raise HTTPException(status_code=400, detail="MP structure limit must be 1 to 1000")
    wavelength = request.wavelength
    try:
        wavelength = float(wavelength)
    except ValueError:
        pass

    cost = _candidate_cost_module()
    min_candidates = _cost_constant(cost, "PREFILTER_MIN_CANDIDATES")
    keep_fraction = _cost_constant(cost, "PREFILTER_TYPICAL_KEEP_FRACTION")
    cutoff = request.mp_e_hull_cutoff
    limit = request.max_mp_structures
    prefilter = bool(request.candidate_prefilter)

    candidates = {
        "cod": None, "mp": None, "mp_capped": False, "total_before_dedup": None,
        "expected_after_dedup": None, "prefilter_expected_after": None, "note": None,
    }
    mp_status = "disabled"
    mp_rows: list[dict] = []
    after: int | None = None

    if mode == "reaction":
        candidates["note"] = (
            "Candidates come from the predicted product set (typically 20-60 structures)."
        )
        n_runtime = REACTION_ESTIMATE_CANDIDATES
    else:
        known = mode == "known_mixture"
        use_mp = request.use_mp and not known
        if known:
            candidates["note"] = "Known-phase mode uses only the listed formulas."

        async def count_cod():
            if not request.use_cod:
                return None
            return await asyncio.to_thread(
                structure_db.cod_candidate_rows, elements, precursors, cutoff,
                formulas_only=known,
            )

        async def query_mp():
            if not use_mp:
                return "disabled", []
            if not _mp_configured():
                return "not_configured", []
            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(_mp_rows_for_estimate, elements, cutoff, limit),
                    MP_ESTIMATE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return "timeout", []
            except Exception:
                logger.warning("Materials Project estimate query failed", exc_info=True)
                return "error", []
            return "ok", rows

        cod_rows, (mp_status, mp_rows) = await asyncio.gather(count_cod(), query_mp())
        cod_pairs = [(row[0], row[2]) for row in cod_rows or []]
        mp_ok = mp_status == "ok"
        candidates["cod"] = len(cod_rows) if cod_rows is not None else None
        candidates["mp"] = len(mp_rows) if mp_ok else None
        candidates["mp_capped"] = bool(mp_ok and len(mp_rows) >= limit)
        total = (candidates["cod"] or 0) + (candidates["mp"] or 0)
        mp_pairs = [(row.get("formula"), row.get("spacegroup")) for row in mp_rows]
        after = min(
            total,
            max(
                round(DEDUP_KEEP_FRACTION * total),
                _unique_formula_sg(cod_pairs),
                _unique_formula_sg(mp_pairs),
            ),
        )
        candidates["total_before_dedup"] = total
        candidates["expected_after_dedup"] = after
        n_runtime = (
            max(min_candidates, round(after * keep_fraction))
            if prefilter and after > min_candidates
            else after
        )
        candidates["prefilter_expected_after"] = n_runtime

    reflections, reflection_counts = _reflection_summary(
        cost, mp_rows, wavelength, request.two_theta_max
    )
    runtime, cost_assumptions = _runtime_estimate(
        cost, n_runtime, reflection_counts, request.data_points
    )
    level = _runtime_level(runtime["expected_s"] if runtime else None)

    suggestions = []
    if level in {"long", "very_long"} and request.use_mp and request.max_mp_structures > 50:
        suggestions.append(
            "Lower 'Maximum MP structures' (for example to 50) to reduce the number of candidates."
        )
    if level == "very_long" and len(elements) >= 5:
        suggestions.append("Restrict the element list to the elements expected in crystalline phases.")
    if not prefilter and after is not None and after > min_candidates:
        suggestions.append(
            "Enable the candidate pre-filter to skip references whose strongest peaks are absent."
        )
    if level in {"long", "very_long"} and request.max_analysis_minutes is None:
        suggestions.append(
            "Set a maximum analysis time to receive the best result found within that time."
        )
    if mp_status in {"timeout", "error"}:
        suggestions.append("Materials Project could not be reached; the estimate counts COD only.")

    return {
        "analysis_mode": mode,
        "candidates": candidates,
        "reflections": reflections,
        "runtime_s": (
            {key: round(runtime[f"{key}_s"]) for key in ("low", "expected", "high")}
            if runtime else None
        ),
        "workers": runtime["workers"] if runtime else None,
        "level": level,
        "suggestions": suggestions,
        "mp_status": mp_status,
        "assumptions": cost_assumptions + [
            "MP precursor-formula references are not counted.",
            "Duplicates are estimated, not computed.",
        ],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


@router.post("/submission/estimate")
async def estimate_submission(request: EstimateRequest):
    """Estimate candidate counts and run time for the form values; never queues a job."""
    return await _estimate_submission(request)


def _warm_cod_database() -> None:
    """Load the COD index in the background so the first estimate is fast."""
    try:
        structure_db.shared_cod_database()
    except Exception:
        logger.debug("COD index warm-up failed", exc_info=True)


if "pytest" not in sys.modules and not os.environ.get("DARA_SKIP_COD_WARMUP"):
    threading.Thread(target=_warm_cod_database, name="cod-warmup", daemon=True).start()


def _counts_like(document) -> bool | None:
    """Whether the stored pattern looks like raw counts; ``None`` when unknown."""
    try:
        xrd_data = document.xrd_data
        return intensities_look_like_counts(
            xrd_data.intensities, getattr(xrd_data, "intensity_unit", None)
        )
    except Exception:
        return None


def _matching_candidate_cifs(document, refinement) -> list[Cif]:
    """CIFs of the displayed result whose phase set equals ``refinement``'s phases."""
    try:
        wanted = _search_result_key(list(refinement.lst_data.phases_results))
        for cifs, candidate in getattr(document, "results", None) or []:
            if _search_result_key(list(candidate.lst_data.phases_results)) == wanted:
                return _flatten_cifs(cifs)
    except Exception:
        logger.debug("Could not match the final result to a candidate", exc_info=True)
    return []


def _final_lattice_flags(job: dict, document) -> list[dict]:
    """Refined lattice parameters near their refinement bounds (cached per task).

    Each phase is checked against the range it was actually refined with: the global
    final-refinement ``lattice_range``, overridden per phase (user-uploaded CIFs use
    ``additional_cif_params``; explicit controls use ``final_refinement_overrides``;
    computed Materials Project structures use the widened ``mp_lattice_window``).
    """
    params = getattr(document, "final_refinement_params", None) or {}
    kwargs = ((job.get("job") or {}).get("function_kwargs")) or {}
    summary = getattr(document, "candidate_summary", None)
    mp_window = (summary if isinstance(summary, dict) else {}).get("mp_lattice_window")
    mp_window = mp_window.get("range") if isinstance(mp_window, dict) else None

    def compute():
        try:
            additional = MontyDecoder().process_decoded(
                deepcopy(kwargs.get("additional_cifs") or [])
            )
        except Exception:
            additional = []
        references = (
            list(getattr(document, "input_cifs", None) or [])
            + list(additional or [])
            + _matching_candidate_cifs(document, document.final_result)
        )
        resolver = lattice_range_resolver(
            params.get("lattice_range", 0.02),
            additional,
            kwargs.get("additional_cif_params"),
            kwargs.get("final_refinement_overrides"),
            mp_window=mp_window,
        )
        return lattice_bound_flags(document.final_result, references, resolver)

    return cached_lattice_flags(job.get("uuid"), compute)


def _backfill_summary(job: dict, document, runtime) -> None:
    """Persist the results-list summary for tasks finished before it existed."""
    existing = job.get("summary")
    if isinstance(existing, dict) and existing.get("schema", 0) >= SUMMARY_SCHEMA:
        return
    try:
        summary = compact_summary(document, runtime)
        with get_worker_store() as store:
            current = store.query_one({"index": job["index"]})
            if current is None or current.get("uuid") != job.get("uuid"):
                return
            current["summary"] = summary
            store.update(current)
    except Exception:
        logger.warning("Could not store the result summary", exc_info=True)


@router.get("/task/{task_id}")
async def result(task_id: int):
    # Deliberately not offloaded to a thread: montydb cannot be opened from several threads
    # at once (ConfigurationError / "Empty module name"), and every handler opens stores.
    # get the task state
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})

    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")

    job_name = job["job"]["name"]

    if job["status"] == "READY":
        return {
            "task_label": job_name,
            "status": job["status"],
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    if job["status"] in {"RUNNING", "CANCELLING", "CANCELLED"}:
        return {
            "task_label": job_name,
            "status": job["status"],
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "start_time": convert_to_local_tz(job.get("start_time", job["submitted_time"])).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }

    if job["status"] == "FIZZLED":
        def _stamp(key):
            value = job.get(key)
            if value is None:
                return None
            return convert_to_local_tz(value).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "task_label": job_name,
            "status": job["status"],  # FIZZLED
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "start_time": _stamp("start_time"),
            "end_time": _stamp("end_time"),
            "error_tb": job.get("error"),
            "error_summary": summarize_failure(job.get("error")),
        }

    with get_result_store() as result_store:
        d = result_store.query_one({"uuid": job["uuid"]})
    if d is None:
        raise HTTPException(status_code=404, detail="Task result not found")
    d = MontyDecoder().process_decoded(d["output"])
    return summarize_document(job, d)


def _confidence_blocks(
    document, evidence_rows, final_quality, final_details, final_metadata,
    final_diagnostics, final_lattice_flags, counts_like,
):
    """Confidence JSON for the final result and each displayed candidate (never raises)."""
    none_blocks = [None] * len(evidence_rows)
    try:
        displayed = []
        for number, (index, refinement, quality, details, metadata, _) in enumerate(
            evidence_rows, start=1
        ):
            cifs = _flatten_cifs(document.results[index][0])
            displayed.append(
                confidence_module.build_evidence(
                    f"candidate:{index}", number, index, refinement, quality, details,
                    metadata, cifs=cifs,
                )
            )
        final = document.final_result
        cif_pool = list(getattr(document, "input_cifs", None) or []) + _matching_candidate_cifs(
            document, final
        )
        target = confidence_module.build_evidence(
            "final", None, None, final, final_quality, final_details, final_metadata,
            lattice_flags=final_lattice_flags, cifs=cif_pool,
        )
        kwargs = dict(
            counts_like=counts_like,
            search_summary=getattr(document, "search_summary", None),
            instrument_profile=confidence_module.instrument_profile_of(document),
        )
        final_block = confidence_module.evaluate_confidence(
            target, displayed,
            missing_peaks=(final_diagnostics.get("counts") or {}).get("missing"),
            missing_summary=final_diagnostics, **kwargs,
        )
        blocks = [
            confidence_module.evaluate_confidence(
                evidence, displayed,
                missing_peaks=(row[5].get("counts") or {}).get("missing"),
                missing_summary=row[5], **kwargs,
            )
            for evidence, row in zip(displayed, evidence_rows)
        ]
        return final_block, blocks
    except Exception:
        logger.warning("Confidence could not be computed", exc_info=True)
        return None, none_blocks


def _mass_balance_amounts(job: dict, document) -> tuple[list[dict] | None, str | None]:
    """Precursor amounts from the submission, else from the pattern file name (never raises)."""
    try:
        raw_job = job.get("job") or {}
        saved = (raw_job.get("metadata") or {}).get("submission_settings") or {}
        return mass_balance_module.resolve_amounts(
            saved.get("precursor_amounts"),
            getattr(document, "precursors", None) or [],
            saved.get("filename") or raw_job.get("name"),
        )
    except Exception:
        logger.debug("Precursor amounts could not be resolved", exc_info=True)
        return None, None


def _mass_balance_block(
    amounts, source, weight_percent, phase_details, predict_kwargs, cautions, confidence
) -> dict:
    """Mass-balance JSON for one refinement; appends its caution and confidence reason."""
    block = mass_balance_module.mass_balance(
        amounts,
        weight_percent,
        weight_esd={
            name: (details or {}).get("weight_esd_percent")
            for name, details in (phase_details or {}).items()
        },
        open_element=(predict_kwargs or {}).get("open_elem"),
        source=source or "input",
    )
    message = mass_balance_module.mass_balance_message(block)
    if message:
        cautions.append({
            "code": "mass_balance",
            "level": "warning" if block["level"] == "warning" else "info",
            "message": message,
            "phase": None,
        })
        mass_balance_module.apply_to_confidence(confidence, block)
    return block


def summarize_document(job: dict, document, *, persist_summary: bool = True) -> dict:
    """Return the COMPLETED-task payload served by GET /api/task/{id}.

    ``job`` needs ``uuid``, ``index``, ``status``, ``submitted_time``, ``start_time``,
    ``end_time`` and ``job`` (``Job.as_dict()`` form: ``name``, ``function_kwargs``,
    ``metadata``). With ``persist_summary=False`` the worker store is never touched.
    """
    d = document
    counts_like = _counts_like(d)
    try:
        peak_context = _peak_context(d, job_key=job.get("uuid"))
    except Exception:  # classification is optional; fall back to the legacy diagnostics
        logger.debug("Peak context could not be built", exc_info=True)
        peak_context = None

    all_results = []
    evidence_rows = []
    equivalent_result_indexes: dict[tuple[str, ...], int] = {}

    for result_index, result in enumerate(d.results):
        try:
            result_weight_percent = {
                phase: round(fraction * 100, 4)
                for phase, fraction in result[1].get_phase_weights().items()
            }
        except (TypeError, ValueError, ZeroDivisionError):
            result_weight_percent = {}
        grouped_phases = deepcopy(d.grouped_phases[result_index]) if d.grouped_phases else None
        phases = [[cif.filename for cif in cifs] for cifs in result[0]]
        highlighted_phases = list(result[1].lst_data.phases_results)
        phase_metadata = _refined_phase_metadata(result[1])
        diagnostics = _classified_peak_diagnostics(
            d,
            result[1],
            d.missing_peaks[result_index] if d.missing_peaks else None,
            d.extra_peaks[result_index] if d.extra_peaks else None,
            (
                d.intensity_mismatch_peaks[result_index]
                if getattr(d, "intensity_mismatch_peaks", None)
                else None
            ),
            context=peak_context,
        )

        # A search path can reach exactly the same phase hypothesis in a different
        # insertion order. Treat database records with the same formula/space
        # group as one displayed phase, while preserving genuinely different
        # polymorphs as separate results.
        result_key = _search_result_key(highlighted_phases)
        if result_key in equivalent_result_indexes:
            existing = all_results[equivalent_result_indexes[result_key]]
            existing["equivalent_result_count"] += 1
            continue

        if not grouped_phases:  # for backward compatibility
            grouped_phases = []
            for phases_ in phases:
                grouped_phase = get_compositional_clusters(list(phases_))
                grouped_phase_with_head = [
                    (get_head_of_compositional_cluster(cluster), cluster)
                    for cluster in grouped_phase
                ]
                grouped_phases.append(grouped_phase_with_head)

        # convert composition into formula
        for i in range(len(grouped_phases)):
            for j in range(len(grouped_phases[i])):
                grouped_phases[i][j] = (
                    grouped_phases[i][j][0]
                    .reduced_composition.to_html_string()
                    .replace("<sub>1</sub>", ""),
                    grouped_phases[i][j][1],
                )

        equivalent_result_indexes[result_key] = len(all_results)
        fit_quality = _fit_quality(result[1])
        phase_details = _phase_refinement_details(result[1])
        all_results.append(
            {
                "rwp": result[1].lst_data.rwp,
                "screening_rwp": (
                    d.screening_rwps[result_index]
                    if d.screening_rwps and result_index < len(d.screening_rwps)
                    else result[1].lst_data.rwp
                ),
                "is_final_refined": bool(d.screening_rwps),
                "phases": phases,
                "highlighted_phases": highlighted_phases,
                "phase_metadata": phase_metadata,
                "fit_quality": fit_quality,
                "phase_details": phase_details,
                "peak_diagnostics": diagnostics,
                "cautions": fit_cautions(
                    fit_quality, phase_details, diagnostics, counts_like=counts_like
                ),
                "grouped_phases": grouped_phases,
                "weight_percent": result_weight_percent,
                "source_index": result_index,
                "equivalent_result_count": 1,
            }
        )
        evidence_rows.append(
            (result_index, result[1], fit_quality, phase_details, phase_metadata, diagnostics)
        )

    if d.final_result is None:
        raise HTTPException(status_code=404, detail="No search result returned")

    start_time = convert_to_local_tz(job["start_time"])
    end_time = convert_to_local_tz(job["end_time"])
    runtime = (end_time - start_time).total_seconds()
    predict_kwargs = d.predict_kwargs or {}
    try:
        weight_percent = {
            phase: round(fraction * 100, 4)
            for phase, fraction in d.final_result.get_phase_weights().items()
        }
    except (TypeError, ValueError, ZeroDivisionError):
        weight_percent = {}
    final_phase_metadata = _refined_phase_metadata(d.final_result)
    final_peak_diagnostics = _classified_peak_diagnostics(
        d, d.final_result, context=peak_context
    )
    final_fit_quality = _fit_quality(d.final_result)
    final_phase_details = _phase_refinement_details(d.final_result)
    final_lattice_flags = _final_lattice_flags(job, d)
    final_cautions = fit_cautions(
        final_fit_quality,
        final_phase_details,
        final_peak_diagnostics,
        counts_like=counts_like,
        lattice_flags=final_lattice_flags,
    )
    final_confidence, candidate_confidence = _confidence_blocks(
        d, evidence_rows, final_fit_quality, final_phase_details, final_phase_metadata,
        final_peak_diagnostics, final_lattice_flags, counts_like,
    )
    for block, cautions in [(final_confidence, final_cautions)] + [
        (candidate_confidence[i], all_results[i]["cautions"]) for i in range(len(all_results))
    ]:
        if not block:
            continue
        for extra in (
            displacement_caution(block.get("sample_displacement")),
            broad_background_caution(block.get("broad_background")),
        ):
            if extra is not None:
                cautions.append(extra)
    mass_amounts, mass_source = _mass_balance_amounts(job, d)
    final_mass_balance = _mass_balance_block(
        mass_amounts, mass_source, weight_percent, final_phase_details, predict_kwargs,
        final_cautions, final_confidence,
    )
    for i, entry in enumerate(all_results):
        entry["mass_balance"] = _mass_balance_block(
            mass_amounts, mass_source, entry["weight_percent"], entry["phase_details"],
            predict_kwargs, entry["cautions"], candidate_confidence[i],
        )
    # Search-level cautions describe the whole run, so they belong to the final result only.
    final_cautions.extend(
        search_cautions(
            getattr(d, "candidate_summary", None), getattr(d, "search_summary", None)
        )
    )
    for entry, block in zip(all_results, candidate_confidence):
        if block is not None:
            entry["confidence"] = block
    if persist_summary:
        _backfill_summary(job, d, runtime)
    return {
        "status": job["status"],
        "task_label": job["job"]["name"],
        "best_rwp": d.best_rwp,
        "final_result": {
            "rwp": d.final_result.lst_data.rwp,
            "phases": list(d.final_result.lst_data.phases_results),
            "weight_percent": weight_percent,
            "phase_metadata": final_phase_metadata,
            "fit_quality": final_fit_quality,
            "phase_details": final_phase_details,
            "peak_diagnostics": final_peak_diagnostics,
            "cautions": final_cautions,
            "mass_balance": final_mass_balance,
            **({"confidence": final_confidence} if final_confidence is not None else {}),
        },
        "all_results": all_results,
        "duplicate_results_removed": len(d.results) - len(all_results),
        "precursors": d.precursors,
        "elements": getattr(d, "elements", None),
        "analysis_mode": getattr(d, "analysis_mode", "reaction" if d.phase_predictor else "elemental"),
        "candidate_sources": getattr(d, "candidate_sources", []),
        "candidate_summary": getattr(d, "candidate_summary", {}),
        "search_summary": getattr(d, "search_summary", {}),
        "temperature": None if predict_kwargs.get("temp") is None else predict_kwargs["temp"] - 273,
        "use_rxn_predictor": d.phase_predictor is not None,
        "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": runtime,
        "additional_search_options": d.search_kwargs,
        "final_refinement_params": d.final_refinement_params,
        "instrument_profile_details": job["job"].get("metadata", {}).get("instrument_profile_details"),
    }
    raise HTTPException(status_code=404, detail="No phases identified in the pattern")


def _safe_download_name(value: str) -> str:
    """Return a filesystem-safe stem for a downloaded result archive."""
    stem = Path(value).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "dara_result"


def _phase_identity(filename: str) -> str:
    """Return the selected reference identity for already-deduplicated results."""
    return filename


def _search_result_key(phases: list[str]) -> tuple[str, ...]:
    """Canonicalize a selected phase hypothesis independent of insertion order."""
    return tuple(sorted(_phase_identity(phase) for phase in phases))


def _flatten_cifs(value) -> list[Cif]:
    """Flatten the candidate CIF grouping used in stored search results."""
    if isinstance(value, Cif):
        return [value]
    if isinstance(value, (list, tuple)):
        return [cif for item in value for cif in _flatten_cifs(item)]
    return []


def _write_refinement_files(
    output: zipfile.ZipFile,
    refinement,
    folder: str,
    reference_cifs: list[Cif],
    missing_peaks=None,
    extra_peaks=None,
) -> None:
    """Write one refinement hypothesis into an open result archive."""
    folder = f"{folder.strip('/')}/" if folder else ""
    phase_parameters = {
        name: phase.model_dump(by_alias=True, exclude={"atom_positions_string"})
        for name, phase in refinement.lst_data.phases_results.items()
    }
    output.writestr(
        f"{folder}phase_parameters.json",
        json.dumps(phase_parameters, ensure_ascii=False, indent=2, default=str),
    )

    plot_data = refinement.plot_data
    csv_buffer = StringIO(newline="")
    writer = csv.writer(csv_buffer)
    phase_names = list(plot_data.structs)
    writer.writerow(
        ["two_theta", "observed", "calculated", "background", "difference"]
        + phase_names
    )
    for idx, angle in enumerate(plot_data.x):
        writer.writerow(
            [
                angle,
                plot_data.y_obs[idx],
                plot_data.y_calc[idx],
                plot_data.y_bkg[idx],
                plot_data.y_obs[idx] - plot_data.y_calc[idx],
            ]
            + [plot_data.structs[name][idx] for name in phase_names]
        )
    output.writestr(f"{folder}refinement.csv", csv_buffer.getvalue())

    raw_lst = getattr(refinement.lst_data, "raw_lst", None)
    if raw_lst:
        output.writestr(f"{folder}refinement.lst", raw_lst)

    metrics = getattr(refinement, "refinement_metrics", None)
    figure = (
        refinement.visualize()
        if metrics is not None
        else visualize(
            result=refinement,
            missing_peaks=missing_peaks,
            extra_peaks=extra_peaks,
        )
    )
    output.writestr(
        f"{folder}plot.html",
        figure.to_html(full_html=True, include_plotlyjs="cdn"),
    )

    for phase_name in refinement.lst_data.phases_results:
        try:
            cif_text = refinement.export_structure(phase_name).to(fmt="cif")
        except (TypeError, ValueError, AttributeError):
            reference = next(
                (
                    cif
                    for cif in reference_cifs
                    if Path(cif.filename or "").stem == phase_name
                ),
                None,
            )
            if reference is None or not reference.orig_string:
                continue
            cif_text = reference.orig_string
        output.writestr(
            f"{folder}refined_cifs/{_safe_download_name(phase_name)}.cif",
            cif_text,
        )


def _make_result_archive(
    task_id: int,
    document,
    summary: dict,
    result_index: int | None = None,
    review_selection=None,
) -> BytesIO:
    """Build a ZIP for all hypotheses or one selected search result."""
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        if result_index is None:
            export_summary = summary
            description = (
                "final/: final re-refinement\n"
                "search_results/result_N/: every ranked search hypothesis\n"
            )
        else:
            export_summary = {
                key: value for key, value in summary.items() if key != "all_results"
            }
            displayed_index, displayed_result = next(
                (
                    (index, result)
                    for index, result in enumerate(summary["all_results"])
                    if result.get("source_index", index) == result_index
                ),
                (result_index, None),
            )
            if displayed_result is None:
                raise ValueError("Search result is not part of the displayed results")
            export_summary["search_result_number"] = displayed_index + 1
            export_summary["search_result"] = displayed_result
            description = f"Selected search hypothesis: {displayed_index + 1}\n"

        output.writestr(
            "summary.json",
            json.dumps(export_summary, ensure_ascii=False, indent=2, default=str),
        )
        output.writestr(
            "README.txt",
            "DARA result export\n"
            f"Task ID: {task_id}\n"
            f"{description}"
            "summary.json: task settings, phases, and Rwp values\n"
            "pattern.xy: submitted diffraction pattern\n"
            "results_table.csv: one row per candidate and phase (weight %, lattice "
            "parameters, fit statistics, cautions); present in the all-results archive\n"
            "Each result folder contains refinement.csv, phase_parameters.json, "
            "refinement.lst (raw BGMN listing), plot.html, and refined_cifs/.\n"
            "plot.html loads Plotly from its public CDN when opened.\n",
        )

        if result_index is None:
            try:
                table_rows = task_table_rows(
                    task_id, {"review": {"selection": review_selection}}, summary
                )
                output.writestr("results_table.csv", rows_to_csv(table_rows, TABLE_COLUMNS))
            except Exception:
                logger.warning("Could not write results_table.csv", exc_info=True)

        xy_columns = [document.xrd_data.angles, document.xrd_data.intensities]
        if document.xrd_data.errors is not None:
            xy_columns.append(document.xrd_data.errors)
        xy_buffer = StringIO()
        np.savetxt(xy_buffer, np.column_stack(xy_columns), fmt="%.8f")
        output.writestr("pattern.xy", xy_buffer.getvalue())

        if result_index is not None:
            candidate_cifs, candidate_result = document.results[result_index]
            _write_refinement_files(
                output,
                candidate_result,
                "",
                _flatten_cifs(candidate_cifs),
                document.missing_peaks[result_index] if document.missing_peaks else None,
                document.extra_peaks[result_index] if document.extra_peaks else None,
            )
        else:
            if document.final_result is not None:
                _write_refinement_files(
                    output,
                    document.final_result,
                    "final",
                    document.input_cifs or [],
                )
            displayed_results = summary.get("all_results", [])
            for displayed_idx, displayed_result in enumerate(displayed_results):
                idx = displayed_result.get("source_index", displayed_idx)
                candidate_cifs, candidate_result = document.results[idx]
                _write_refinement_files(
                    output,
                    candidate_result,
                    f"search_results/result_{displayed_idx + 1}",
                    _flatten_cifs(candidate_cifs),
                    document.missing_peaks[idx] if document.missing_peaks else None,
                    document.extra_peaks[idx] if document.extra_peaks else None,
                )

    archive.seek(0)
    return archive


@router.get("/task/{task_id}/download")
async def download_result(task_id: int, idx: int | None = Query(None, ge=0)):
    """Download all hypotheses or one selected search result as a ZIP archive."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if job["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only completed tasks can be downloaded")

    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": job["uuid"]})
    if stored is None:
        raise HTTPException(status_code=404, detail="Task result not found")
    document = MontyDecoder().process_decoded(stored["output"])
    if idx is not None and idx >= len(document.results or []):
        raise HTTPException(status_code=404, detail="Search result index out of range")
    summary = await result(task_id)
    archive = _make_result_archive(
        task_id,
        document,
        summary,
        result_index=idx,
        review_selection=(job.get("review") or {}).get("selection"),
    )
    suffix = "all_results" if idx is None else f"search_result_{idx + 1}"
    filename = (
        f"dara_task_{task_id}_{_safe_download_name(job['job']['name'])}_{suffix}.zip"
    )
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/task/{task_id}/table.csv")
async def download_task_table(task_id: int):
    """Flat CSV with one row per (candidate, phase) of a completed task."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if job["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only completed tasks can be exported")
    summary = await result(task_id)
    rows = task_table_rows(task_id, job, summary)
    filename = f"dara_task_{task_id}_{_safe_download_name(job['job']['name'])}_table.csv"
    return _csv_response(rows_to_csv(rows, TABLE_COLUMNS), filename)


def _parse_task_ids(ids: str | None) -> list[int]:
    """Parse ``1,2,3`` into unique ids in the given order (1 to 50 ids)."""
    parts = [part.strip() for part in (ids or "").split(",") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="Provide task ids, e.g. ids=1,2,3")
    parsed: list[int] = []
    for part in parts:
        if not re.fullmatch(r"\d{1,12}", part):
            raise HTTPException(status_code=400, detail=f"Invalid task id: {part[:20]}")
        value = int(part)
        if value not in parsed:
            parsed.append(value)
    if len(parsed) > 50:
        raise HTTPException(status_code=400, detail="Export at most 50 tasks at a time")
    return parsed


@router.get("/tasks/table.csv")
async def download_tasks_table(ids: str = Query(...)):
    """Final-result rows for several tasks; unfinished tasks get a status row."""
    task_ids = _parse_task_ids(ids)
    entries = []
    for task_id in task_ids:
        with get_worker_store() as worker_store:
            job = worker_store.query_one({"index": task_id})
        summary = None
        if job is not None and job["status"] == "COMPLETED":
            try:
                summary = await result(task_id)
            except HTTPException:
                summary = None
        entries.append((task_id, job, summary))
    rows = multi_task_rows(entries)
    return _csv_response(
        rows_to_csv(rows, TABLE_COLUMNS + MULTI_TASK_EXTRA_COLUMNS), "dara_tasks_table.csv"
    )


@router.delete("/task/{task_id}")
async def delete_result(task_id: int):
    """Delete a finished/failed task and its stored analysis document."""
    with get_worker_store() as worker_store:
        job = worker_store.query_one({"index": task_id})
        if job is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if job["status"] in {"READY", "RUNNING", "CANCELLING"}:
            raise HTTPException(status_code=409, detail="A queued or running task cannot be deleted")
        worker_store.remove_docs({"index": task_id})

    with get_result_store() as result_store:
        result_store.remove_docs({"uuid": job["uuid"]})
    _plot_json.cache_clear()
    from dara.server.task_control_router import task_directory
    try:
        checkpoint_dir = task_directory(job["uuid"])
    except ValueError:
        checkpoint_dir = None  # Legacy non-UUID records have no checkpoints.
    if checkpoint_dir is not None and checkpoint_dir.is_dir() and not checkpoint_dir.is_symlink():
        # UUID validation fixes the exact target below the checkpoint parent.
        shutil.rmtree(checkpoint_dir)
    return {"message": "deleted", "task_id": task_id}


@lru_cache(maxsize=24)
def _plot_json(uuid: str, idx: int | None) -> str:
    """Build an immutable completed-result plot once per server process."""
    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": uuid})
    if stored is None:
        raise HTTPException(status_code=404, detail="Task not found")
    document = MontyDecoder().process_decoded(stored["output"])
    if idx is None:
        if document.final_result is None:
            raise HTTPException(status_code=404, detail="Final result not found")
        figure = document.final_result.visualize()
    elif 0 <= idx < len(document.results or []):
        result = document.results[idx][1]
        missing_peaks = document.missing_peaks[idx] if document.missing_peaks else None
        extra_peaks = document.extra_peaks[idx] if document.extra_peaks else None
        figure = (
            result.visualize()
            if getattr(result, "refinement_metrics", None) is not None
            else visualize(result=result, missing_peaks=missing_peaks, extra_peaks=extra_peaks)
        )
    else:
        raise HTTPException(status_code=404, detail="Index out of range")
    # Kα1 wavelength lets the browser show d-spacings next to 2θ on hover.
    try:
        from dara.search.candidate_cost import wavelength_angstrom

        spec = (getattr(document, "search_kwargs", None) or {}).get("wavelength", "Cu")
        figure.update_layout(meta={"wavelength_angstrom": round(wavelength_angstrom(spec), 6)})
    except Exception:
        logger.debug("Plot wavelength unavailable", exc_info=True)
    return figure.to_json()


@router.get("/task/{task_id}/plot")
async def plot(task_id: int, idx: int | None = Query(None, ge=0)):
    with get_worker_store() as worker_store:
        task = worker_store.query_one({"index": task_id}, ["uuid", "status"])
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    payload = _plot_json(task["uuid"], idx)
    # Returning the serialized figure directly avoids FastAPI JSON-encoding the
    # JSON string a second time (extra copies, escaping and client-side parse).
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Cache-Control": "private, max-age=3600"},
    )


LIST_SUMMARY_REFRESH_MAX = 3  # stale summaries upgraded per list request (bounds the latency)


def _refresh_listed_summaries(tasks: list[dict]) -> dict[int, dict | None]:
    """Upgrade at most ``LIST_SUMMARY_REFRESH_MAX`` stale summaries of the listed tasks.

    The remaining stale tasks are refreshed by later requests; until then they keep their
    stored (older) summary. Runs inline like the series refresh (montydb is not thread-safe).
    """
    fresh: dict[int, dict | None] = {}
    budget = LIST_SUMMARY_REFRESH_MAX
    for task in tasks:
        if budget <= 0:
            break
        summary = task.get("summary")
        if task.get("status") != "COMPLETED" or (
            isinstance(summary, dict) and summary.get("schema", 0) >= SUMMARY_SCHEMA
        ):
            continue
        if not task.get("uuid") or task["uuid"] in SUMMARY_REFRESH_GAVE_UP:
            continue
        budget -= 1
        try:
            fresh[task["index"]] = refresh_stale_summary(task, lambda: get_worker_store())
        except Exception:
            logger.debug("List summary refresh failed", exc_info=True)
    return fresh


@router.get("/tasks")
async def get_all_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user: Optional[str] = None,
    name: Optional[str] = None,
):
    # Calculate the skip value based on the page and limit
    skip = (page - 1) * limit

    query_dict = {}
    if user is not None:
        query_dict["user"] = user
    if user == "unknown":
        query_dict["user"] = None
    if name is not None and name.strip():
        # Literal, case-insensitive substring match on the pattern file name.
        query_dict["job.name"] = {"$regex": re.escape(name.strip()), "$options": "i"}
    with get_worker_store() as worker_store:
        tasks = list(
            worker_store.query(
                criteria=query_dict, sort={"submitted_time": -1}, skip=skip, limit=limit
            )
        )

    tasks_result = []
    refreshed = _refresh_listed_summaries(tasks)

    for task in tasks:
        index = task["index"]
        state = task["status"]
        summary = refreshed.get(index, task.get("summary"))
        tasks_result.append(
            {
                "fw_id": index,
                "name": task["job"]["name"],
                "state": state,
                "created_on": convert_to_local_tz(task["submitted_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "user": task.get("user", "unknown"),
                "summary": summary if isinstance(summary, dict) else None,
                "series": ((task.get("job") or {}).get("metadata") or {}).get("series"),
                "adopted": (task.get("review") or {}).get("selection"),
                "error_summary": (
                    summarize_failure(task.get("error")) if state == "FIZZLED" else None
                ),
            }
        )

    with get_worker_store() as worker_store:
        total_tasks = worker_store.count(query_dict)

    # Return the tasks data
    return {
        "tasks": tasks_result,
        "total_tasks": total_tasks,
        "page": page,
        "total_page": (total_tasks + limit - 1) // limit,
    }
