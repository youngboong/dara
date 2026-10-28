"""Retry a failed task, optionally with the suggested fix for its failure cause."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from monty.json import MontyDecoder
from pydantic import BaseModel, ConfigDict

from dara.selected_refinement import SelectedPhaseRefinementMaker
from dara.server.api_router import _rerun_settings, _saved_task_input, build_submission_job
from dara.server.failure_summary import REMEDY_LABELS, remedy_changes, summarize_failure
from dara.server.worker import add_job_to_queue

logger = logging.getLogger("dara.server.retry")

router = APIRouter(prefix="/api")

# Limits enforced by ``build_submission_job``; a remedy never asks for more than it accepts.
SCREENING_TIMEOUT_RANGE_S = (60.0, 1200.0)
ANALYSIS_MINUTES_RANGE = (5.0, 720.0)
LEGACY_SCREENING_TIMEOUT_S = 1200.0  # what ``screening_timeout_s=None`` means (see dara.jobs)


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apply_remedy: bool = False


def _is_selected_phase(record: dict, kwargs: dict) -> bool:
    name = str((record.get("job") or {}).get("name") or "")
    return name.endswith(" [selected phases]") or (
        "context" in kwargs and "analysis_mode" not in kwargs
    )


def _clamp(changes: dict) -> dict:
    out = dict(changes)
    if "screening_timeout_s" in out:
        lo, hi = SCREENING_TIMEOUT_RANGE_S
        out["screening_timeout_s"] = float(min(hi, max(lo, out["screening_timeout_s"])))
    if "max_analysis_minutes" in out:
        lo, hi = ANALYSIS_MINUTES_RANGE
        out["max_analysis_minutes"] = float(min(hi, max(lo, out["max_analysis_minutes"])))
    return out


def _effective(key: str, value):
    """Value a setting has in effect: ``None`` timeout = legacy 1200 s, ``None`` minutes = no limit."""
    if key == "screening_timeout_s":
        return LEGACY_SCREENING_TIMEOUT_S if value is None else float(value)
    if key == "max_analysis_minutes":
        return None if value is None else float(value)
    return value


def _plan(task_id: int) -> dict:
    """Everything a retry needs; raises 404/409 like ``_saved_task_input``."""
    record, kwargs, pattern = _saved_task_input(task_id)
    status = record.get("status")
    selected = _is_selected_phase(record, kwargs)
    summary = summarize_failure(record.get("error"))
    code = summary["code"] if summary else None
    plan = {
        "record": record, "kwargs": kwargs, "pattern": pattern, "status": status,
        "selected": selected, "code": code,
        "label": REMEDY_LABELS.get(code) if code else None,
        "changes": None, "rerun": None, "settings": None,
    }
    if selected:
        return plan
    rerun = _rerun_settings(record, kwargs, pattern)
    settings = dict(rerun["settings"])
    plan["rerun"], plan["settings"] = rerun, settings
    if code:
        changes = remedy_changes(code, {**settings, "_additional_cif_count": rerun["additional_cif_count"]})
        if changes:
            changes = _clamp(changes)
            # Changes that equal the saved setting would not alter the run.
            # (``None`` means the legacy 1200 s / no limit, so compare effective values.)
            changes = {
                key: value for key, value in changes.items()
                if _effective(key, settings.get(key)) != _effective(key, value)
            }
        plan["changes"] = changes or None
    if plan["changes"] is None:
        plan["label"] = None
    return plan


@router.get("/task/{task_id}/retry-plan")
async def retry_plan(task_id: int):
    plan = _plan(task_id)
    supported, reason = True, None
    if plan["status"] != "FIZZLED":
        supported, reason = False, "Only failed tasks can be retried."
    elif plan["selected"]:
        reason = "No automatic remedy for selected-phase refinements."
    elif plan["changes"] is None:
        reason = "No automatic remedy for this failure."
    return {
        "code": plan["code"],
        "label": plan["label"],
        "changes": plan["changes"],
        "retry_unchanged": supported,
        "supported": supported,
        "reason": reason,
    }


def _requeue_selected(task_id: int, plan: dict) -> int:
    record = plan["record"]
    try:
        decoded = MontyDecoder().process_decoded(plan["kwargs"])
        job = SelectedPhaseRefinementMaker(name=record["job"]["name"]).make(**decoded)
    except Exception as exc:
        logger.exception("Selected-phase retry of task %s failed", task_id)
        raise HTTPException(409, "The saved selected-phase input could not be restored.") from exc
    details = (record["job"].get("metadata") or {}).get("instrument_profile_details")
    if details:
        job.metadata["instrument_profile_details"] = details
    job.metadata["retry_of"] = task_id
    return add_job_to_queue(job, record.get("user", ""))


@router.post("/task/{task_id}/retry")
async def retry_task(task_id: int, request: RetryRequest | None = None):
    request = request or RetryRequest()
    plan = _plan(task_id)
    if plan["status"] != "FIZZLED":
        raise HTTPException(409, "Only failed tasks can be retried.")
    if plan["selected"]:
        if request.apply_remedy:
            raise HTTPException(409, "No automatic remedy for selected-phase refinements.")
        return {"wf_id": _requeue_selected(task_id, plan), "applied": None, "retry_of": task_id}

    kwargs, rerun = plan["kwargs"], plan["rerun"]
    settings = dict(plan["settings"])
    user = settings.pop("user", "")
    applied = None
    if request.apply_remedy and plan["changes"]:
        settings.update(plan["changes"])
        applied = {"code": plan["code"], "label": plan["label"], "changes": plan["changes"]}
    additional_cifs = MontyDecoder().process_decoded(
        kwargs.get("additional_cifs") or kwargs.get("cifs") or []
    )
    job = build_submission_job(
        plan["pattern"],
        rerun["filename"],
        **settings,
        additional_cifs=additional_cifs,
        library_cif_ids="[]",
        original_kwargs=kwargs,
        source_task_id=task_id,
    )
    submission = job.metadata.setdefault("submission_settings", {})
    submission["retry_of"] = task_id
    submission["retry_remedy"] = plan["code"] if applied else None
    return {"wf_id": add_job_to_queue(job, user), "applied": applied, "retry_of": task_id}
