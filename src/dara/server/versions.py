"""Re-refinement versions of a completed task, and the unified selection grammar.

A *selection* names one refinement of a task:

* ``"best"`` - the minimum-Rwp final result (also called version 1),
* an ``int`` - a stored candidate (source index of ``document.results``),
* ``"version:<n>"`` - a re-refinement saved by the quick-refine executor (n >= 2).

Version records live in the ``versions`` collection (``uuid`` = task uuid,
``index`` = version number).  Numbers are never reused: deleting a version leaves a
tombstone.  All store access in this module must happen on the event-loop thread.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from monty.json import MontyDecoder, MontyEncoder

from dara.server.utils import get_versions_store

VERSIONS_COLLECTION = "versions"
SEL_RE = re.compile(r"^version:([1-9]\d{0,5})$")
_SELECTION_MESSAGE = "Selection must be 'best', a candidate index or 'version:<n>'"

#: Fields kept when listing versions (the heavy ``result`` blob is excluded).
LIGHT_FIELDS = [
    "uuid", "index", "task_index", "version", "deleted", "created_at", "label", "origin",
    "phases", "controls", "background", "phase_params", "overrides", "refinement_params",
    "effective_phase_params", "elapsed_s", "bgmn_s", "summary",
]


def parse_selection(value: Any):
    """Normalise a selection to ``"best"``, a candidate ``int`` or ``("version", n)``."""
    if value is None:
        return "best"
    if isinstance(value, bool):
        raise HTTPException(422, _SELECTION_MESSAGE)
    if isinstance(value, int):
        if value < 0:
            raise HTTPException(422, _SELECTION_MESSAGE)
        return value
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "version":
        return parse_selection(f"version:{value[1]}")
    if isinstance(value, str):
        text = value.strip()
        if text == "best":
            return "best"
        if re.fullmatch(r"\d{1,9}", text):
            return int(text)
        match = SEL_RE.match(text)
        if match:
            number = int(match.group(1))
            return "best" if number == 1 else ("version", number)
    raise HTTPException(422, _SELECTION_MESSAGE)


def format_selection(selection) -> str | int:
    """Inverse of :func:`parse_selection`."""
    if isinstance(selection, tuple):
        return f"version:{selection[1]}"
    return selection


def is_version(selection) -> bool:
    return isinstance(selection, tuple) and selection[0] == "version"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_json(value):
    """Round-trip through Monty so numpy/pydantic content becomes plain JSON data."""
    return json.loads(json.dumps(value, cls=MontyEncoder))


# --------------------------------------------------------------------------- store
def load_version(task_uuid: str, number: int, *, light: bool = False) -> dict:
    """Stored version record (404 when missing or deleted).  Event-loop thread only."""
    with get_versions_store() as store:
        record = store.query_one(
            {"uuid": task_uuid, "index": number}, LIGHT_FIELDS if light else None
        )
    if not record or record.get("deleted"):
        raise HTTPException(404, f"Version {number} not found")
    return record


def list_versions(task_uuid: str, *, include_deleted: bool = False) -> list[dict]:
    """Version records (without the heavy ``result``) sorted by number."""
    with get_versions_store() as store:
        records = list(store.query({"uuid": task_uuid}, properties=LIGHT_FIELDS))
    records.sort(key=lambda item: item.get("index", 0))
    return records if include_deleted else [item for item in records if not item.get("deleted")]


def next_version_number(task_uuid: str) -> int:
    """Next free number; deleted numbers are never reused (tombstones count)."""
    with get_versions_store() as store:
        used = [
            item.get("index", 1)
            for item in store.query({"uuid": task_uuid}, properties=["index"])
        ]
    return max([1, *used]) + 1


def save_version(task_uuid: str, task_index: int, payload: dict) -> int:
    """Persist a finished re-refinement as the next version; returns its number."""
    number = next_version_number(task_uuid)
    record = {
        **payload,
        "uuid": task_uuid,
        "index": number,
        "version": number,
        "task_index": task_index,
        "deleted": False,
        "created_at": payload.get("created_at") or utc_now(),
    }
    with get_versions_store() as store:
        store.update([record])
    return number


def tombstone_version(task_uuid: str, number: int) -> None:
    """Soft-delete: keep a stub so the number is not reused, drop the heavy payload."""
    with get_versions_store() as store:
        record = store.query_one({"uuid": task_uuid, "index": number})
        if not record:
            raise HTTPException(404, f"Version {number} not found")
        record["deleted"] = True
        record["result"] = None
        store.update([record])


def delete_task_versions(task_uuid: str) -> None:
    """Remove every version record (tombstones included) of a deleted task."""
    with get_versions_store() as store:
        # ``remove_docs`` on a collection that was never written creates a table-less file that
        # breaks every later read and write, so only remove when there is something to remove.
        if store.count({"uuid": task_uuid}):
            store.remove_docs({"uuid": task_uuid})


# --------------------------------------------------------------------------- decoding
def version_refinement(record: dict):
    """``RefinementResult`` stored in a version record."""
    return MontyDecoder().process_decoded(deepcopy(record["result"]))


def version_cifs(record: dict) -> dict:
    """References added by a version (not part of the document), ``stem -> Cif``."""
    from dara.cif import Cif

    out = {}
    for item in record.get("cifs") or []:
        try:
            cif = Cif.from_str(item["cif_text"])
            cif.filename = str(item["filename"])
            out[cif.filename] = cif
        except Exception:  # noqa: BLE001 - one broken CIF must not hide the version
            continue
    return out


def document_references(document) -> dict:
    from dara.server.selected_refinement_router import _references

    return _references(document)


def version_reference_map(document, record: dict) -> dict:
    refs = dict(document_references(document))
    refs.update(version_cifs(record))
    return refs


def _best_cifs(document) -> list:
    from dara.server.api_router import _matching_candidate_cifs

    return list(getattr(document, "input_cifs", None) or []) + _matching_candidate_cifs(
        document, document.final_result
    )


def resolve_refinement(document, task, selection):
    """Return ``(refinement, meta)`` for a selection.

    ``meta`` keys: ``kind`` (best/candidate/version), ``label``, ``cifs`` (reference CIFs of
    the refined phases), ``refinement_params``, ``phase_params`` and ``version``.
    """
    sel = parse_selection(selection)
    base_params = (getattr(document, "search_kwargs", None) or {}).get("refinement_params") or {}
    if sel == "best":
        if document.final_result is None:
            raise HTTPException(404, "Final refinement not found")
        return document.final_result, {
            "kind": "best", "label": "Original analysis", "cifs": _best_cifs(document),
            "refinement_params": base_params, "version": None,
            "phase_params": getattr(document, "final_refinement_params", None),
        }
    if isinstance(sel, int):
        if sel >= len(document.results or []):
            raise HTTPException(404, "Search result index out of range")
        from dara.server.api_router import _flatten_cifs

        return document.results[sel][1], {
            "kind": "candidate", "label": f"Candidate {sel + 1}",
            "cifs": _flatten_cifs(document.results[sel][0]),
            "refinement_params": base_params, "version": None,
            "phase_params": getattr(document, "final_refinement_params", None),
        }
    number = sel[1]
    if task is None:
        raise HTTPException(422, "A version selection needs the task")
    record = load_version(task["uuid"], number)
    refs = version_reference_map(document, record)
    return version_refinement(record), {
        "kind": "version",
        "label": record.get("label") or f"Version {number}",
        "cifs": [refs[stem] for stem in record.get("phases") or [] if stem in refs],
        "refinement_params": record.get("refinement_params") or {},
        "phase_params": record.get("phase_params"),
        "version": number,
        "record": record,
    }


def document_view(document, task, record: dict):
    """The analysis document as if the version were its final result (summaries, reports)."""
    refinement = version_refinement(record)
    refs = version_reference_map(document, record)
    cifs = [refs[stem] for stem in record.get("phases") or [] if stem in refs]
    added = list(version_cifs(record).values())
    search_kwargs = deepcopy(getattr(document, "search_kwargs", None) or {})
    params = record.get("refinement_params")
    if params:
        search_kwargs["refinement_params"] = deepcopy(params)
    else:
        search_kwargs.pop("refinement_params", None)
    return document.model_copy(
        update={
            "final_result": refinement,
            "final_refinement_params": deepcopy(record.get("phase_params"))
            or getattr(document, "final_refinement_params", None),
            "search_kwargs": search_kwargs,
            "results": [([[cif] for cif in cifs], refinement)],
            "grouped_phases": None,
            "screening_rwps": None,
            "missing_peaks": None,
            "extra_peaks": None,
            "intensity_mismatch_peaks": None,
            "foms": None,
            "lattice_strains": None,
            "best_rwp": refinement.lst_data.rwp,
            "input_cifs": list(getattr(document, "input_cifs", None) or []) + added,
        }
    )


def version_job_view(task: dict, tag, overrides: dict | None, extra_cifs=None) -> dict:
    """Worker-record look-alike with a distinct uuid so per-task memos are not shared."""
    job = dict(task.get("job") or {})
    kwargs = dict(job.get("function_kwargs") or {})
    if overrides is not None:
        kwargs["final_refinement_overrides"] = overrides
    if extra_cifs:
        try:
            existing = list(
                MontyDecoder().process_decoded(deepcopy(kwargs.get("additional_cifs") or [])) or []
            )
        except Exception:  # noqa: BLE001
            existing = []
        kwargs["additional_cifs"] = existing + list(extra_cifs)
    job["function_kwargs"] = kwargs
    return {**task, "uuid": f"{task['uuid']}#{tag}", "job": job}


def build_version_summary(task: dict, document, record: dict, *, extra_uploaded=None) -> dict:
    """Summary (shape of ``GET /api/task/{id}`` ``final_result``) of a version record.

    ``extra_uploaded`` are reference-library CIFs treated like uploaded files (their
    lattice range is read from ``additional_cif_params``).  Runs in a worker thread: it
    never touches a store.
    """
    from dara.server import fit_cautions
    from dara.server.api_router import summarize_document

    view = document_view(document, task, record)
    job_view = version_job_view(
        task, record.get("job_id") or record.get("version"), record.get("overrides"), extra_uploaded
    )
    summary = dict(summarize_document(job_view, view, persist_summary=False)["final_result"])
    quality = summary.get("fit_quality") or {}
    summary.update(
        rexp=quality.get("rexp"), gof=quality.get("gof"), chi_squared=quality.get("chi_squared")
    )
    if record.get("background"):
        summary["cautions"] = list(summary.get("cautions") or []) + [
            fit_cautions._caution(
                "background_model_changed",
                "info",
                "Background model or excluded ranges changed from the original analysis; "
                "compare Rwp only with fits that use the same ranges.",
                None,
            )
        ]
    return encode_json(summary)
