"""Status, cancellation and download of completed refinement checkpoints."""
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from dara.server.setting import get_dara_server_settings
from dara.server.utils import get_worker_store

router = APIRouter(prefix="/api")


def task_directory(uuid):
    # Never use a caller-supplied path as an artifact directory.
    return Path(get_dara_server_settings().montydb_path).resolve().parent / "task-checkpoints" / str(UUID(str(uuid)))


def _record(task_id):
    with get_worker_store() as store:
        record = store.query_one({"index": task_id})
    if not record:
        raise HTTPException(404, "Task not found")
    return record


def _as_utc(value):
    """Datetime from a stored value; naive datetimes are UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _seconds_since(value, now):
    moment = _as_utc(value)
    return None if moment is None else max(0.0, (now - moment).total_seconds())


def _runtime_fields(record, state, now=None) -> dict:
    """Elapsed time, time budget and stage ETA (each ``None`` when unknown)."""
    now = now or datetime.now(timezone.utc)
    running = record.get("status") in {"RUNNING", "CANCELLING"}
    elapsed = _seconds_since(record.get("start_time"), now) if running else None
    kwargs = ((record.get("job") or {}).get("function_kwargs")) or {}
    budget = kwargs.get("max_runtime_s") if isinstance(kwargs, dict) else None
    stage_elapsed = _seconds_since(state.get("stage_started_at"), now) if running else None
    completed, total = state.get("completed"), state.get("total")
    eta = None
    if (
        stage_elapsed is not None
        and isinstance(completed, (int, float)) and isinstance(total, (int, float))
        and 0 < completed < total
    ):
        eta = round(stage_elapsed / completed * (total - completed))
    return {
        "elapsed_s": None if elapsed is None else round(elapsed),
        "budget_s": budget if isinstance(budget, (int, float)) else None,
        "stage_elapsed_s": None if stage_elapsed is None else round(stage_elapsed),
        "eta_s": eta,
    }


@router.get("/task/{task_id}/progress")
async def progress(task_id: int):
    record = _record(task_id)
    directory = task_directory(record["uuid"])
    try:
        state = json.loads((directory / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"stage": record["status"], "completed": None, "total": None}
    return {**state, "status": record["status"],
            "completed_candidates": len(list(directory.glob("candidate_*.zip"))),
            **_runtime_fields(record, state)}


@router.post("/task/{task_id}/cancel")
async def cancel(task_id: int):
    with get_worker_store() as store:
        record = store.query_one({"index": task_id})
        if not record:
            raise HTTPException(404, "Task not found")
        if record["status"] == "READY":
            directory = task_directory(record["uuid"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "cancel.requested").touch()
            record.update(status="CANCELLED", end_time=datetime.now(timezone.utc))
        elif record["status"] == "RUNNING":
            directory = task_directory(record["uuid"])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "cancel.requested").touch()
            record["status"] = "CANCELLING"
        elif record["status"] not in {"CANCELLING", "CANCELLED"}:
            raise HTTPException(409, "This task has already finished.")
        store.update(record)
    return {"status": record["status"]}


@router.get("/task/{task_id}/checkpoints")
async def checkpoints(task_id: int):
    directory = task_directory(_record(task_id)["uuid"])
    paths = sorted(directory.glob("candidate_*.zip"))
    if not paths:
        raise HTTPException(404, "No fully refined candidates have been saved yet.")
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.name)
    output.seek(0)
    return StreamingResponse(output, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="task_{task_id}_partial_candidates.zip"'})
