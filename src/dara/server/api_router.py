from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
import zipfile
from ast import literal_eval
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
from pymatgen.core import Composition, Element
from pymatgen.symmetry.groups import SpaceGroup

from dara.cif import Cif
from dara.analysis_range import validate_analysis_range
from dara.jobs import PhaseSearchMaker
from dara.plot import peaks_in_fit_range, visualize
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

router = APIRouter(prefix="/api")


def _available_instrument_profiles() -> list[str]:
    device_dir = Path(__file__).parents[1] / "data" / "BGMN-Templates" / "Devices"
    return sorted(path.stem for path in device_dir.glob("*.geq"))


def _refined_phase_metadata(refinement) -> dict[str, dict]:
    """Return user-facing crystallographic labels for refined phases."""
    metadata = {}
    for phase_name, phase_result in refinement.lst_data.phases_results.items():
        spacegroup_number = phase_result.spacegroup_no
        try:
            spacegroup_symbol = SpaceGroup.from_int_number(spacegroup_number).symbol
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

    return {
        "filename": filename,
        "format": temp_path.suffix.lower().lstrip("."),
        "two_theta_min": float(finite_angles.min()) if finite_angles.size else None,
        "two_theta_max": float(finite_angles.max()) if finite_angles.size else None,
        "data_points": int(angles.size),
        "metadata": metadata,
        "profile_suggestion": suggestion,
        "recommended_wavelength": metadata.get("radiation"),
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
        "precursor_formulas": kwargs.get("precursors") or context.get("precursors") or [],
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
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid file format")
        elif source_task_id is None:
            raise HTTPException(status_code=400, detail="Upload an XRD pattern or select a source task")
        range_params = validate_analysis_range(
            pattern.angles, two_theta_min, two_theta_max, excluded_ranges
        )

        precursor_formulas = _parse_string_list(precursor_formulas)
        elements = _parse_string_list(elements)

        # Compatibility with the original web/API contract.
        if not analysis_mode:
            analysis_mode = "reaction" if use_rxn_predictor else "elemental"
            if analysis_mode == "elemental" and not elements:
                elements, precursor_formulas = precursor_formulas, []
        if analysis_mode not in {"reaction", "elemental", "known_mixture"}:
            raise HTTPException(status_code=400, detail="Invalid analysis mode")

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
        if original_kwargs.get("final_refinement_params") is not None:
            maker_kwargs["final_refinement_params"] = deepcopy(original_kwargs["final_refinement_params"])
        from dara.refinement_controls import RefinementControls, apply_controls

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
            "source_task_id": source_task_id,
        }
        if instrument_profile.startswith("user-"):
            from dara.instrument_profiles import registered_profiles, registered_profile_path

            registered_profile_path(instrument_profile)
            job.metadata["instrument_profile_details"] = registered_profiles().get(instrument_profile)
        job_index = add_job_to_queue(job, user=user)
        return {"message": "submitted", "wf_id": job_index}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        print_exc()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/task/{task_id}")
async def result(task_id: int):
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
        return {
            "task_label": job_name,
            "status": job["status"],  # FIZZLED
            "submitted_on": convert_to_local_tz(job["submitted_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "start_time": convert_to_local_tz(job["start_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "end_time": convert_to_local_tz(job["end_time"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "error_tb": job["error"],
        }

    with get_result_store() as result_store:
        d = result_store.query_one({"uuid": job["uuid"]})
    if d is None:
        raise HTTPException(status_code=404, detail="Task result not found")
    d = MontyDecoder().process_decoded(d["output"])

    all_results = []
    equivalent_result_indexes: dict[tuple[str, ...], int] = {}

    for result_index, result in enumerate(d.results):
        try:
            result_weight_percent = {
                phase: round(fraction * 100, 4)
                for phase, fraction in result[1].get_phase_weights().items()
            }
        except (TypeError, ValueError, ZeroDivisionError):
            result_weight_percent = {}
        grouped_phases = d.grouped_phases[result_index] if d.grouped_phases else None
        phases = [[cif.filename for cif in cifs] for cifs in result[0]]
        highlighted_phases = list(result[1].lst_data.phases_results)
        phase_metadata = _refined_phase_metadata(result[1])
        diagnostics = _peak_diagnostics(
            result[1],
            d.missing_peaks[result_index] if d.missing_peaks else None,
            d.extra_peaks[result_index] if d.extra_peaks else None,
            (
                d.intensity_mismatch_peaks[result_index]
                if getattr(d, "intensity_mismatch_peaks", None)
                else None
            ),
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
                "fit_quality": _fit_quality(result[1]),
                "phase_details": _phase_refinement_details(result[1]),
                "peak_diagnostics": diagnostics,
                "grouped_phases": grouped_phases,
                "weight_percent": result_weight_percent,
                "source_index": result_index,
                "equivalent_result_count": 1,
            }
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
    final_peak_diagnostics = _peak_diagnostics(d.final_result)
    return {
        "status": job["status"],
        "task_label": job["job"]["name"],
        "best_rwp": d.best_rwp,
        "final_result": {
            "rwp": d.final_result.lst_data.rwp,
            "phases": list(d.final_result.lst_data.phases_results),
            "weight_percent": weight_percent,
            "phase_metadata": final_phase_metadata,
            "fit_quality": _fit_quality(d.final_result),
            "phase_details": _phase_refinement_details(d.final_result),
            "peak_diagnostics": final_peak_diagnostics,
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
            "Each result folder contains refinement.csv, phase_parameters.json, "
            "plot.html, and refined_cifs/.\n"
            "plot.html loads Plotly from its public CDN when opened.\n",
        )

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
    archive = _make_result_archive(task_id, document, summary, result_index=idx)
    suffix = "all_results" if idx is None else f"search_result_{idx + 1}"
    filename = (
        f"dara_task_{task_id}_{_safe_download_name(job['job']['name'])}_{suffix}.zip"
    )
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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


@router.get("/tasks")
async def get_all_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user: Optional[str] = None,
):
    # Calculate the skip value based on the page and limit
    skip = (page - 1) * limit

    query_dict = {}
    if user is not None:
        query_dict["user"] = user
    if user == "unknown":
        query_dict["user"] = None
    with get_worker_store() as worker_store:
        tasks = worker_store.query(
            criteria=query_dict, sort={"submitted_time": -1}, skip=skip, limit=limit
        )

    tasks_result = []

    for task in tasks:
        index = task["index"]
        state = task["status"]
        tasks_result.append(
            {
                "fw_id": index,
                "name": task["job"]["name"],
                "state": state,
                "created_on": convert_to_local_tz(task["submitted_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "user": task.get("user", "unknown"),
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
