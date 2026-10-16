"""User-directed phase selection, stored as a new queued refinement task."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from monty.json import MontyDecoder
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dara.cif import Cif
from dara.refinement_controls import RefinementControls, apply_controls
from dara.selected_refinement import SelectedPhaseRefinementMaker, valid_reference_filename
from dara.server.utils import get_result_store, get_worker_store
from dara.server.worker import add_job_to_queue

router = APIRouter(prefix="/api")


def _load(task_id):
    with get_worker_store() as store:
        record = store.query_one({"index": task_id})
    if record is None:
        raise HTTPException(404, "Task not found")
    if record["status"] != "COMPLETED":
        raise HTTPException(409, "Wait for the task to complete before re-refining.")
    with get_result_store() as store:
        stored = store.query_one({"uuid": record["uuid"]})
    if not stored:
        raise HTTPException(404, "Task result not found")
    return record, MontyDecoder().process_decoded(stored["output"])


def _references(document):
    references = {}

    def collect(value):
        if isinstance(value, Cif):
            if value.filename:
                references.setdefault(value.filename, value)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    for groups, _ in document.results or []:
        collect(groups)
    collect(document.input_cifs or [])
    return references


class SelectedRefinementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phases: list[str] = Field(min_length=1, max_length=20)
    selection: Literal["best"] | StrictInt = "best"
    controls: RefinementControls | None = None


@router.get("/task/{task_id}/phase-options")
async def phase_options(task_id: int):
    _, document = _load(task_id)
    references = _references(document)
    return {"phases": list(references), "max_phases": 20,
            "final_refinement_params": document.final_refinement_params}


@router.post("/task/{task_id}/refine-selected")
async def refine_selected(task_id: int, request: SelectedRefinementRequest):
    record, document = _load(task_id)
    if isinstance(request.selection, int) and not 0 <= request.selection < len(document.results or []):
        raise HTTPException(400, "Unknown candidate index")
    if len(set(request.phases)) != len(request.phases):
        raise HTTPException(400, "Select each reference only once.")
    references = _references(document)
    if any(name not in references for name in request.phases):
        raise HTTPException(400, "A selected reference is not present in this task.")
    if any(not valid_reference_filename(name) for name in request.phases):
        raise HTTPException(400, "Invalid stored reference filename.")
    maker = SelectedPhaseRefinementMaker(name=f"{record['job']['name']} [selected phases]")
    job = maker.make(
        xrd_data=document.xrd_data,
        cifs=[references[name] for name in request.phases],
        search_kwargs=document.search_kwargs or {},
        final_refinement_params=apply_controls(document.final_refinement_params, request.controls),
        context={
            "parent_task_id": task_id, "parent_selection": request.selection,
            "precursors": document.precursors, "elements": document.elements,
            "candidate_sources": document.candidate_sources,
        },
    )
    profile_details = record["job"].get("metadata", {}).get("instrument_profile_details")
    if profile_details:
        job.metadata["instrument_profile_details"] = profile_details
    task_index = add_job_to_queue(job, record.get("user", ""))
    return {"message": "submitted", "wf_id": task_index, "parent_task_id": task_id}
