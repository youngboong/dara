"""Series batch analysis: submit several patterns as one ordered series and inspect it."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from dara.cif import Cif
from dara.series import parse_series_variables, series_order
from dara.server.utils import convert_to_local_tz, get_worker_store
from dara.xrd import load_pattern

logger = logging.getLogger("dara.server.series")

router = APIRouter(prefix="/api")

MAX_SERIES_FILES = 50
MIN_SERIES_FILES = 2
SUMMARY_SCHEMA_REQUIRED = 2
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Tasks whose summary could not be upgraded to the current schema; do not decode
# their stored documents again on every poll of the series page.
_summary_refresh_gave_up: set[str] = set()


def _build_job(pattern, name, **fields):
    """Build one analysis job (wrapper so tests can replace it)."""
    from dara.server.api_router import build_submission_job

    return build_submission_job(pattern, name, **fields)


def _queue(job, user):
    """Queue a job and return its index (wrapper so tests can replace it)."""
    from dara.server.worker import add_job_to_queue

    return add_job_to_queue(job, user=user)


def _wavelength_mismatch(pattern, path, wavelength):
    """Recorded-vs-selected wavelength check (wrapper so tests can replace it)."""
    from dara.server.api_router import _wavelength_mismatch as check

    return check(pattern, path, wavelength)


class SeriesPreviewRequest(BaseModel):
    filenames: list[str] = Field(default_factory=list, max_length=MAX_SERIES_FILES)


def _preview(filenames: list[str]) -> dict:
    parsed = parse_series_variables(filenames)
    order = series_order(filenames, parsed["variables"])
    return {
        "unit": parsed["unit"],
        "items": [
            {"filename": name, "order": position, "variable": variable}
            for name, position, variable in zip(filenames, order, parsed["variables"])
        ],
    }


@router.post("/series/preview")
async def preview_series(request: SeriesPreviewRequest):
    """Parse the ordering variable from file names without touching any file."""
    return _preview([Path(name).name for name in request.filenames])


def _prefixed(exc: HTTPException, filename: str) -> HTTPException:
    if isinstance(exc.detail, str):
        return HTTPException(status_code=exc.status_code, detail=f"{filename}: {exc.detail}")
    return exc


@router.post("/submit-series")
async def submit_series(
    user: Annotated[str, Form()],
    series_name: Annotated[str, Form()],
    pattern_files: Annotated[list[UploadFile], File()],
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
    additional_phases: Annotated[list[UploadFile], File()] = None,
    library_cif_ids: Annotated[str, Form()] = "[]",
    two_theta_min: Annotated[float | None, Form()] = None,
    two_theta_max: Annotated[float | None, Form()] = None,
    excluded_ranges: Annotated[str, Form()] = "[]",
    refinement_controls: Annotated[str, Form()] = "{}",
    confirm_wavelength: Annotated[bool, Form()] = False,
    candidate_prefilter: Annotated[Optional[bool], Form()] = None,
    screening_timeout_s: Annotated[Optional[float], Form()] = None,
    max_analysis_minutes: Annotated[Optional[float], Form()] = None,
):
    """Validate and build every job first, then queue them all in series order."""
    name = (series_name or "").strip()
    if not 1 <= len(name) <= 120:
        raise HTTPException(status_code=400, detail="Series name must be 1 to 120 characters")
    files = list(pattern_files or [])
    if not MIN_SERIES_FILES <= len(files) <= MAX_SERIES_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A series needs {MIN_SERIES_FILES} to {MAX_SERIES_FILES} pattern files",
        )
    filenames = [Path(item.filename or "pattern.xy").name for item in files]
    duplicates = sorted({item for item in filenames if filenames.count(item) > 1})
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail=f"Duplicate file names in the series: {', '.join(duplicates)}",
        )

    try:
        cifs = []
        if additional_phases:
            with tempfile.TemporaryDirectory() as temp_dir:
                for phase in additional_phases:
                    phase_name = Path(phase.filename or "reference.cif").name
                    with open(Path(temp_dir) / phase_name, "wb") as handle:
                        shutil.copyfileobj(phase.file, handle)
                    cif_file = Cif.from_file(Path(temp_dir) / phase_name)
                    if not cif_file.data:
                        raise HTTPException(status_code=400, detail="Invalid additional CIF file provided")
                    cifs.append(cif_file)

        fields = {
            "analysis_mode": analysis_mode,
            "precursor_formulas": precursor_formulas,
            "elements": elements,
            "instrument_profile": instrument_profile,
            "wavelength": wavelength,
            "temperature": temperature,
            "atmosphere": atmosphere,
            "use_cod": use_cod,
            "use_mp": use_mp,
            "mp_e_hull_cutoff": mp_e_hull_cutoff,
            "max_mp_structures": max_mp_structures,
            "library_cif_ids": library_cif_ids,
            "two_theta_min": two_theta_min,
            "two_theta_max": two_theta_max,
            "excluded_ranges": excluded_ranges,
            "refinement_controls": refinement_controls,
            "candidate_prefilter": candidate_prefilter,
            "screening_timeout_s": screening_timeout_s,
            "max_analysis_minutes": max_analysis_minutes,
        }
        jobs = []
        for upload, filename in zip(files, filenames):
            try:
                with tempfile.TemporaryDirectory() as pattern_dir:
                    pattern_path = Path(pattern_dir) / filename
                    with open(pattern_path, "wb") as handle:
                        shutil.copyfileobj(upload.file, handle)
                    try:
                        pattern = load_pattern(pattern_path)
                    except Exception as exc:
                        raise HTTPException(
                            status_code=400, detail=f"Could not read pattern: {exc}"  # prefixed below
                        )
                    if not confirm_wavelength:
                        mismatch = _wavelength_mismatch(pattern, pattern_path, wavelength)
                        if mismatch:
                            raise HTTPException(status_code=400, detail={**mismatch, "filename": filename})
                jobs.append(_build_job(pattern, filename, **fields, additional_cifs=list(cifs)))
            except HTTPException as exc:
                raise _prefixed(exc, filename) from exc
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"{filename}: {exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Series submission failed", exc_info=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed = parse_series_variables(filenames)
    order = series_order(filenames, parsed["variables"])
    series_id = uuid4().hex[:12]
    for job, position, variable in zip(jobs, order, parsed["variables"]):
        job.metadata["series"] = {
            "id": series_id,
            "name": name,
            "order": position,
            "size": len(jobs),
            "variable": variable,
        }
    queued = sorted(range(len(jobs)), key=lambda index: order[index])
    task_ids = [_queue(jobs[index], user) for index in queued]
    return {
        "message": "submitted",
        "series_id": series_id,
        "task_ids": task_ids,
        "variables": [parsed["variables"][index] for index in queued],
        "unit": parsed["unit"],
    }


# --- listing -------------------------------------------------------------------------

_LIST_PROPERTIES = ["index", "status", "submitted_time", "job.metadata.series", "job.name"]
_DETAIL_PROPERTIES = [
    "index", "uuid", "status", "submitted_time", "start_time", "end_time", "summary",
    "job.metadata.series", "job.name",
]


def _series_of(record: dict) -> dict | None:
    series = ((record.get("job") or {}).get("metadata") or {}).get("series")
    return series if isinstance(series, dict) and series.get("id") else None


def _query_series(properties: list[str], series_id: str | None = None) -> list[dict]:
    criteria = (
        {"job.metadata.series.id": series_id}
        if series_id
        else {"job.metadata.series": {"$exists": True}}
    )
    with get_worker_store() as store:
        try:
            records = list(store.query(criteria=criteria, properties=properties))
        except Exception:
            logger.info("Series query failed; scanning all tasks", exc_info=True)
            records = list(store.query({}, properties=properties))
    return [record for record in records if (series := _series_of(record)) and (not series_id or series["id"] == series_id)]


def _stamp(value) -> str | None:
    if value is None:
        return None
    try:
        return convert_to_local_tz(value).strftime(TIME_FORMAT)
    except Exception:
        return None


def _unit_of(variables) -> str | None:
    return next((item.get("unit") for item in variables if isinstance(item, dict)), None)


@router.get("/series")
async def list_series():
    """Series overview, newest first."""
    groups: dict[str, dict] = {}
    for record in _query_series(_LIST_PROPERTIES):
        series = _series_of(record)
        group = groups.setdefault(
            series["id"],
            {"series_id": series["id"], "name": series.get("name") or "", "times": [], "n_tasks": 0,
             "n_completed": 0, "variables": []},
        )
        group["n_tasks"] += 1
        group["n_completed"] += record.get("status") == "COMPLETED"
        group["variables"].append(series.get("variable"))
        if record.get("submitted_time") is not None:
            group["times"].append(record["submitted_time"])
    items = []
    for group in groups.values():
        created = min(group["times"]) if group["times"] else None
        items.append((
            created,
            {
                "series_id": group["series_id"],
                "name": group["name"],
                "created_on": _stamp(created),
                "n_tasks": group["n_tasks"],
                "n_completed": group["n_completed"],
                "unit": _unit_of(group["variables"]),
            },
        ))
    items.sort(key=lambda pair: (_sort_time(pair[0]), pair[1]["series_id"]), reverse=True)
    return {"series": [item for _, item in items]}


def _sort_time(value) -> float:
    try:
        return value.timestamp() if value.tzinfo else value.replace(tzinfo=None).timestamp()
    except Exception:
        return 0.0


def _refreshed_summary(record: dict) -> dict | None:
    """Return a schema-2 summary for a completed task, upgrading and persisting if needed."""
    summary = record.get("summary")
    summary = summary if isinstance(summary, dict) else None
    uuid = record.get("uuid")
    if record.get("status") != "COMPLETED":
        return summary
    if summary is not None and summary.get("schema", 0) >= SUMMARY_SCHEMA_REQUIRED:
        return summary
    if not uuid or uuid in _summary_refresh_gave_up:
        return summary
    try:
        from dara.server.task_summary import runtime_seconds, summarize_stored_result

        fresh = summarize_stored_result(uuid, runtime_seconds(record.get("start_time"), record.get("end_time")))
    except Exception:
        logger.info("Could not refresh the summary of task %s", record.get("index"), exc_info=True)
        _summary_refresh_gave_up.add(uuid)
        return summary
    if not isinstance(fresh, dict):
        return summary
    if fresh.get("schema", 0) < SUMMARY_SCHEMA_REQUIRED:
        _summary_refresh_gave_up.add(uuid)
    try:
        with get_worker_store() as store:
            current = store.query_one({"index": record["index"]})
            if current is not None and current.get("uuid") == uuid:
                current["summary"] = fresh
                store.update(current)
    except Exception:
        logger.warning("Could not store the refreshed summary", exc_info=True)
    return fresh


@router.get("/series/{series_id}")
async def get_series(series_id: str):
    records = _query_series(_DETAIL_PROPERTIES, series_id)
    if not records:
        raise HTTPException(status_code=404, detail="Series not found")
    entries = []
    for record in records:
        series = _series_of(record)
        entries.append({
            "task_id": record["index"],
            "name": (record.get("job") or {}).get("name"),
            "order": series.get("order", 0),
            "variable": series.get("variable"),
            "state": record.get("status"),
            "summary": _refreshed_summary(record),
            "_time": record.get("submitted_time"),
        })
    entries.sort(key=lambda item: (item["order"], item["task_id"]))
    times = [item.pop("_time") for item in entries if item["_time"] is not None]
    for item in entries:
        item.pop("_time", None)
    first = _series_of(records[0])
    return {
        "series_id": series_id,
        "name": first.get("name") or "",
        "unit": _unit_of([item["variable"] for item in entries]),
        "created_on": _stamp(min(times)) if times else None,
        "items": entries,
    }
