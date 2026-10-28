"""Fast in-process re-refinement of a completed task ("quick refine").

The normal workflow re-refines a candidate by queueing a new task in the worker process,
which pays for a process hand-off, a document decode and EFLECH.  Here the same science
(``dara.final_refinement_spec`` mirrors ``dara.jobs``) runs in a small thread pool of the
server process: caches for the decoded document, the observed peaks and the generated
``.str`` files make a repeat start in well under a second, and the run time is BGMN's.

Threading rules:

* montydb stores are only opened on the event-loop thread (route code and ``_finish``);
* worker threads only read/write files below ``<montydb parent>/quick-refine/<task uuid>``;
* the process environment is not written after start-up (see ``ensure_bgmn_env``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import statistics
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from inspect import signature
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from monty.json import MontyDecoder
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from dara.analysis_range import normalize_excluded_ranges, validate_analysis_range
from dara.bgmn_worker import RefinementCancelled
from dara.cif import Cif
from dara.cif2str import cif2str
from dara.final_refinement_spec import build_final_refinement_spec
from dara.refine import do_refinement
from dara.refinement_controls import RefinementControls
from dara.selected_refinement import valid_reference_filename
from dara.server import versions
from dara.server.setting import get_dara_server_settings
from dara.server.utils import get_result_store, get_worker_store
from dara.utils import process_phase_name

logger = logging.getLogger("dara.server.quick_refine")

router = APIRouter(prefix="/api")

MAX_PHASES = 20
MAX_QUEUED = 8
FAILED_RUN_KEEP_S = 24 * 3600
JOB_ID_RE = re.compile(r"^q-[0-9a-f]{12}$")
COD_SOURCE_RE = re.compile(r"^cod:(\d{1,9})$")
LIBRARY_SOURCE_RE = re.compile(r"^library:([A-Za-z0-9_-]{1,64})$")
ANCHORS_MESSAGE = "Background anchor points are not supported yet."

_STAGES = ("Queued", "Preparing inputs", "BGMN refinement", "Peak diagnostics", "Storing version", "Done")
_ACTIVE = ("queued", "running")


# --------------------------------------------------------------------------- request
class QuickPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=200)
    source: str = Field(default="task", max_length=80)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value == "task" or COD_SOURCE_RE.match(value) or LIBRARY_SOURCE_RE.match(value):
            return value
        raise ValueError("source must be 'task', 'cod:<code>' or 'library:<id>'")


class QuickBackground(BaseModel):
    model_config = ConfigDict(extra="forbid")

    degree: int | None = Field(default=None, ge=2, le=20)
    excluded_ranges: list[list[float]] | None = None
    anchors: Any = None

    @field_validator("anchors")
    @classmethod
    def _no_anchors(cls, value):
        if value is not None:
            raise ValueError(ANCHORS_MESSAGE)
        return value

    @field_validator("excluded_ranges")
    @classmethod
    def _ranges(cls, value):
        if value is None:
            return None
        try:
            return normalize_excluded_ranges(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class QuickRefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: Annotated[StrictInt, Field(ge=0)] | str = "best"
    phases: list[QuickPhase] = Field(default_factory=list, max_length=MAX_PHASES)
    remove: list[str] = Field(default_factory=list, max_length=MAX_PHASES)
    controls: RefinementControls | None = None
    background: QuickBackground | None = None
    label: str | None = Field(default=None, max_length=120)
    origin: Literal["add_hint", "add_peak_search", "swap", "remove", "background", "manual"] = "manual"

    @field_validator("base")
    @classmethod
    def _base(cls, value):
        try:
            versions.parse_selection(value)  # 422 for anything outside the grammar
        except HTTPException as exc:
            raise ValueError(exc.detail) from exc
        return value

    @model_validator(mode="after")
    def _non_empty(self):
        if not (self.phases or self.remove or self.controls or self.background):
            raise ValueError("Nothing to re-refine: add or remove a phase, or change a setting.")
        return self


# --------------------------------------------------------------------------- cache paths
def cache_root() -> Path:
    return Path(get_dara_server_settings().montydb_path).resolve().parent / "quick-refine"


def task_cache(task_uuid: str) -> Path:
    return cache_root() / str(UUID(str(task_uuid)))


def _replace(source: Path | str, target: Path, *, attempts: int = 6) -> None:
    """os.replace that tolerates brief Windows locks (antivirus, a concurrent reader).

    Cache files are deterministic, so when the target already exists after the
    retries the existing copy is kept and the temporary file discarded.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                if Path(target).is_file():
                    try:
                        os.unlink(source)
                    except OSError:
                        pass
                    return
                raise
            time.sleep(0.05 * (2 ** attempt))


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        _replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- jobs
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class QuickJob:
    job_id: str
    task_id: int
    task_uuid: str
    label: str
    seq: int
    status: str = "queued"
    stage: str = "Queued"
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    eta_s: float | None = None
    error: str | None = None
    version: int | None = None
    timings: dict = field(default_factory=dict)
    # Strong reference to the decoded task document (or, failing that, the raw stored one),
    # taken at POST time so that cache eviction cannot fail a job that is still queued.
    discarded: bool = field(default=False, repr=False)  # its task was deleted
    document: Any = field(default=None, repr=False)
    raw: Any = field(default=None, repr=False)
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _started: float | None = field(default=None, repr=False)
    _finished: float | None = field(default=None, repr=False)

    def elapsed(self) -> float:
        if self._started is None:
            return 0.0
        return round((self._finished or time.monotonic()) - self._started, 3)

    def snapshot(self, position: int = 0) -> dict:
        return {
            "job_id": self.job_id,
            "task_id": self.task_id,
            "task_uuid": self.task_uuid,
            "status": self.status,
            "stage": self.stage,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "elapsed_s": self.elapsed(),
            "eta_s": self.eta_s,
            "error": self.error,
            "version": self.version,
            "label": self.label,
            # ``position`` is 1-based while queued (0 otherwise); ``ahead`` is the number of
            # active jobs in front of this one (a queued job with ahead == 0 starts next).
            "position": position,
            "ahead": max(0, int(position) - 1) if self.status == "queued" and position else 0,
            "timings": dict(self.timings),
        }


_LOCK = threading.RLock()
_JOBS: "OrderedDict[str, QuickJob]" = OrderedDict()
_SEQ = 0
_TASKS: set = set()  # strong references to the asyncio finish tasks


def _persist(job: QuickJob) -> None:
    if job.discarded:
        return  # the task (and its cache directory) is gone; do not recreate it
    try:
        path = task_cache(job.task_uuid) / "jobs" / f"{job.job_id}.json"
        _atomic_write(path, json.dumps(job.snapshot(self_position(job)), default=str))
    except Exception:  # noqa: BLE001 - persistence is diagnostics only
        logger.debug("Could not persist job %s", job.job_id, exc_info=True)


def self_position(job: QuickJob) -> int:
    """0 while running (or finished); otherwise 1 + the active jobs ahead in the queue."""
    with _LOCK:
        if job.status != "queued":
            return 0
        return 1 + sum(1 for other in _JOBS.values() if other.status in _ACTIVE and other.seq < job.seq)


def _update(job: QuickJob, **fields) -> None:
    with _LOCK:
        for key, value in fields.items():
            setattr(job, key, value)
        if fields.get("status") == "running" and job._started is None:
            job._started = time.monotonic()
            job.started_at = _now_iso()
        if fields.get("status") in ("done", "failed", "cancelled"):
            job._finished = time.monotonic()
    _persist(job)


def _new_job(task_id: int, task_uuid: str, label: str) -> QuickJob:
    global _SEQ  # noqa: PLW0603
    with _LOCK:
        _SEQ += 1
        job = QuickJob(f"q-{uuid4().hex[:12]}", task_id, task_uuid, label, _SEQ)
        _JOBS[job.job_id] = job
        while len(_JOBS) > 200:
            oldest = next(iter(_JOBS))
            if _JOBS[oldest].status in _ACTIVE:
                break
            _JOBS.pop(oldest)
    return job


def _queued_count() -> int:
    with _LOCK:
        return sum(1 for job in _JOBS.values() if job.status == "queued")


def _read_job_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _LOCK:
        live = data.get("job_id") in _JOBS
    if data.get("status") in _ACTIVE and not live:
        data = {**data, "status": "failed", "stage": "Done", "error": "Interrupted by a server restart."}
    return data


def _load_job_file(job_id: str) -> dict | None:
    """Job snapshot from disk (jobs of a previous server run, or evicted from memory)."""
    root = cache_root()
    if not root.is_dir():
        return None
    for path in root.glob(f"*/jobs/{job_id}.json"):
        data = _read_job_file(path)
        if data is not None:
            return data
    return None


def recover_interrupted(root: Path | None = None) -> int:
    """Rewrite jobs left ``queued``/``running`` by a previous server run as failed."""
    base = root if root is not None else cache_root()
    fixed = 0
    if not base.is_dir():
        return fixed
    for path in base.glob("*/jobs/q-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") in _ACTIVE:
            data.update(status="failed", stage="Done", error="Interrupted by a server restart.")
            try:
                _atomic_write(path, json.dumps(data, default=str))
                fixed += 1
            except OSError:
                pass
    return fixed


# --------------------------------------------------------------------------- executors
_REFINE: ThreadPoolExecutor | None = None
_PREP: ThreadPoolExecutor | None = None
_EXEC_LOCK = threading.Lock()


def _refine_pool() -> ThreadPoolExecutor:
    global _REFINE  # noqa: PLW0603
    with _EXEC_LOCK:
        if _REFINE is None:
            workers = max(1, int(os.getenv("DARA_QUICK_WORKERS", "1")))
            _REFINE = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dara-quick")
        return _REFINE


def _prep_pool() -> ThreadPoolExecutor:
    global _PREP  # noqa: PLW0603
    with _EXEC_LOCK:
        if _PREP is None:
            _PREP = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dara-quick-prep")
        return _PREP


def shutdown() -> None:
    """Cancel every job and stop the pools without waiting (called from the app lifespan)."""
    global _REFINE, _PREP  # noqa: PLW0603
    with _LOCK:
        for job in _JOBS.values():
            job.cancel.set()
    with _EXEC_LOCK:
        pools, _REFINE, _PREP = [_REFINE, _PREP], None, None
    for pool in pools:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------- document cache
class _DocumentCache:
    """Decoded documents of COMPLETED tasks (immutable), at most ``size`` of them."""

    def __init__(self, size: int = 4):
        self.size = size
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, Future]" = OrderedDict()

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._items

    def peek(self, key: str):
        """The decoded document if it is ready right now, else ``None`` (never blocks)."""
        with self._lock:
            future = self._items.get(key)
        if future is not None and future.done() and future.exception() is None:
            return future.result()
        return None

    def get(self, key: str, raw: dict | None, ready=None):
        """Decoded document for ``key``.

        ``ready`` is an already decoded document held by the caller (see ``QuickJob.document``);
        it is used, and re-cached, when the entry was evicted since the request was accepted.
        """
        with self._lock:
            future = self._items.get(key)
            if future is not None:
                self._items.move_to_end(key)
                owner = False
            else:
                if raw is None and ready is None:
                    raise RuntimeError("The stored task data is no longer cached; retry the request.")
                future = Future()
                self._items[key] = future
                while len(self._items) > self.size:
                    self._items.popitem(last=False)
                owner = True
        if owner:
            try:
                future.set_result(ready if ready is not None else MontyDecoder().process_decoded(raw))
            except BaseException as exc:
                with self._lock:
                    self._items.pop(key, None)
                future.set_exception(exc)
                raise
        return future.result()

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def discard(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)


_DOCUMENTS = _DocumentCache()


def _read_raw_document(task_uuid: str) -> dict:
    with get_result_store() as store:
        stored = store.query_one({"uuid": task_uuid})
    if not stored or "output" not in stored:
        raise HTTPException(404, "Task result not found")
    return stored["output"]


def _raw_for(task_uuid: str) -> dict | None:
    """Raw stored document unless the decoded one is cached already (event-loop thread)."""
    return None if _DOCUMENTS.has(task_uuid) else _read_raw_document(task_uuid)


def _document_handles(task_uuid: str) -> tuple:
    """``(decoded, raw)``: the decoded document if it is cached and ready, else the raw one.

    Exactly one is not ``None``; the caller keeps it alive so that cache eviction between
    accepting a request and running it cannot make the request fail (event-loop thread).
    """
    decoded = _DOCUMENTS.peek(task_uuid)
    if decoded is not None:
        return decoded, None
    return None, _read_raw_document(task_uuid)


# --------------------------------------------------------------------------- pattern / peaks / str caches
def _sha1(*parts: str) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _ensure_pattern(document, task_dir: Path) -> Path:
    path = task_dir / "pattern" / "xrd_data.xy"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"xrd_data.{uuid4().hex[:8]}.tmp.xy")
        document.xrd_data.to_xy_file(temp)
        _replace(temp, path)
    return path


def _refine_default(name: str):
    return signature(do_refinement).parameters[name].default if name in signature(do_refinement).parameters else None


def _profile_stamp(profile: str) -> str:
    if str(profile).startswith("user-"):
        try:
            from dara.instrument_profiles import registered_profile_path

            stat = Path(registered_profile_path(profile)).stat()
            return f"{stat.st_mtime_ns}:{stat.st_size}"
        except Exception:  # noqa: BLE001
            return "?"
    return ""


def _range_params(refinement_params: dict | None) -> dict:
    return {
        key: value
        for key, value in (refinement_params or {}).items()
        if key in {"wmin", "wmax", "excluded_ranges"}
    }


def _compute_peaks(pattern_path: Path, wavelength, profile, range_params: dict) -> pd.DataFrame:
    from dara.peak_detection import detect_peaks

    frame = detect_peaks(
        str(pattern_path), wavelength=wavelength, instrument_profile=str(profile), **range_params
    )
    return frame[["2theta", "intensity"]].reset_index(drop=True)


def _observed_peaks(task_dir: Path, pattern_path: Path, kwargs: dict, refinement_params: dict | None):
    """Observed peaks exactly as ``_attach_peak_markers`` would detect them (cached on disk)."""
    wavelength = kwargs.get("wavelength", _refine_default("wavelength") or "Cu")
    profile = kwargs.get("instrument_profile", _refine_default("instrument_profile"))
    ranges = _range_params(refinement_params)
    pattern_digest = hashlib.sha1(pattern_path.read_bytes()).hexdigest()
    key = _sha1(
        pattern_digest, json.dumps(wavelength, default=str), str(profile), _profile_stamp(str(profile)),
        json.dumps(ranges, sort_keys=True, default=str),
    )
    cache = task_dir / "peaks" / f"{key[:16]}.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return pd.DataFrame({"2theta": data["2theta"], "intensity": data["intensity"]})
    except (OSError, ValueError, KeyError):
        pass
    frame = _compute_peaks(pattern_path, wavelength, profile, ranges)
    try:
        _atomic_write(
            cache,
            json.dumps({"2theta": [float(v) for v in frame["2theta"]],
                        "intensity": [float(v) for v in frame["intensity"]]}),
        )
    except OSError:
        logger.debug("Could not cache observed peaks", exc_info=True)
    return frame


_STR_VOLATILE = re.compile(r"//\s*generated by pymatgen.*$", re.M)


def strip_str_timestamp(text: str) -> str:
    """``.str`` text without the generation-time comment (for byte comparisons)."""
    return _STR_VOLATILE.sub("// generated by pymatgen", text)


def _cached_str(task_dir: Path, stem: str, cif_text: str, params: dict) -> Path:
    key = _sha1(cif_text, json.dumps(params, sort_keys=True, default=str))[:16]  # short: Windows MAX_PATH
    out = task_dir / "str" / key / f"{stem}.str"
    if out.is_file():
        return out
    task_dir.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(dir=task_dir, prefix="str-"))
    try:
        cif_path = temp / f"{stem}.cif"
        cif_path.write_text(cif_text)
        cif2str(cif_path, "", temp, **params)
        out.parent.mkdir(parents=True, exist_ok=True)
        _replace(temp / f"{stem}.str", out)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return out


# --------------------------------------------------------------------------- phase planning
class QuickRefineError(ValueError):
    """The request cannot be run (reported as a failed job with this message)."""


@dataclass
class PhasePlan:
    stems: list[str]
    cifs: dict[str, Cif]
    removed: list[str]
    added: list[str]
    library_stems: list[str]


def _short_name(stem: str) -> str:
    return stem.split("_")[0] or stem


def default_label(request: QuickRefineRequest) -> str:
    if request.label:
        return request.label.strip()
    added = ", ".join(_short_name(p.ref) for p in request.phases)
    removed = ", ".join(_short_name(value) for value in request.remove)
    kind = request.origin
    if kind == "add_hint" and added:
        return f"Added {added} (hint)"
    if kind == "add_peak_search" and added:
        return f"Added {added} (peak search)"
    if kind == "swap" and added and removed:
        return f"Swapped {removed} for {added}"
    if kind == "remove" and removed:
        return f"Removed {removed}"
    if kind == "background":
        return "Background / excluded-range change"
    parts = []
    if added:
        parts.append(f"added {added}")
    if removed:
        parts.append(f"removed {removed}")
    if request.controls:
        parts.append("changed refinement controls")
    if request.background:
        parts.append("changed background")
    return ("Re-refinement: " + ", ".join(parts)) if parts else "Re-refinement"


def _group_heads(groups) -> list[str]:
    stems = []
    for group in groups:
        head = group[0] if isinstance(group, (list, tuple)) else group
        stems.append(str(head.filename))
    return stems


def base_phase_stems(document, selection, base_version: dict | None, refs: dict) -> list[str]:
    """Phase stems of the base fit (candidate, best result or earlier version)."""
    if isinstance(selection, tuple):
        return list((base_version or {}).get("phases") or [])
    if isinstance(selection, int):
        if selection >= len(document.results or []):
            raise QuickRefineError("Unknown candidate index")
        return _group_heads(document.results[selection][0])
    from dara.server.api_router import _search_result_key

    final = document.final_result
    if final is None:
        raise QuickRefineError("The task has no final result to start from.")
    wanted = _search_result_key(list(final.lst_data.phases_results))
    for groups, candidate in document.results or []:
        if _search_result_key(list(candidate.lst_data.phases_results)) == wanted:
            return _group_heads(groups)
    # Fall back to the BGMN phase names of the final fit.
    names = list(final.lst_data.phases_results)
    by_name = {process_phase_name(stem): stem for stem in refs}
    stems = [by_name[name] for name in names if name in by_name]
    if len(stems) != len(names):
        raise QuickRefineError("Could not identify the phases of the best result.")
    return stems


def _resolve_new_cif(item: QuickPhase, refs: dict, library: dict) -> tuple[str, Cif, str | None]:
    """Return ``(stem, cif, library_marker)`` for one requested phase."""
    if item.source == "task":
        cif = refs.get(item.ref)
        if cif is None:
            raise QuickRefineError(f"Reference {item.ref} is not available for this task.")
        return item.ref, cif, None
    cod = COD_SOURCE_RE.match(item.source)
    if cod:
        from dara import structure_db

        code = cod.group(1)
        if not re.fullmatch(rf"^.+_\(cod_{code}\)-\d+$", item.ref) or not valid_reference_filename(item.ref):
            raise QuickRefineError(f"Reference name {item.ref} does not match COD entry {code}.")
        path = structure_db.cod_cache_dir() / f"{code}.cif"
        if not structure_db.is_valid_cif_file(path):
            raise QuickRefineError(f"COD entry {code} is not in the local cache.")
        cif = Cif.from_file(path)
        cif.filename = item.ref
        return item.ref, cif, None
    match = LIBRARY_SOURCE_RE.match(item.source)
    cif = library.get(match.group(1)) if match else None
    if cif is None:
        raise QuickRefineError(f"Unknown reference library entry for {item.ref}.")
    stem = str(cif.name)
    if not valid_reference_filename(stem):
        raise QuickRefineError("The reference library CIF has an invalid name.")
    cif.filename = stem
    return stem, cif, stem


def plan_phases(
    document, selection, base_version: dict | None, request: QuickRefineRequest, library: dict
) -> PhasePlan:
    refs = versions.version_reference_map(document, base_version or {})
    stems = base_phase_stems(document, selection, base_version, refs)
    if not stems:
        raise QuickRefineError("The base fit has no phases.")
    removed: list[str] = []
    for value in request.remove:
        matches = [s for s in stems if value in (s, process_phase_name(s))]
        if not matches:
            raise QuickRefineError(f"Cannot remove {value}: it is not in the base phase set.")
        removed.append(matches[0])
    if len(set(removed)) != len(removed):
        raise QuickRefineError("A phase was listed for removal more than once.")
    kept = [s for s in stems if s not in removed]
    cifs = {s: refs[s] for s in kept if s in refs}
    missing = [s for s in kept if s not in cifs]
    if missing:
        raise QuickRefineError(f"Reference structure for {missing[0]} is not stored with this task.")
    added: list[str] = []
    library_stems: list[str] = []
    for item in request.phases:
        stem, cif, marker = _resolve_new_cif(item, refs, library)
        if stem in cifs or stem in added:
            raise QuickRefineError(f"{stem} is already part of the phase set.")
        cifs[stem] = cif
        added.append(stem)
        if marker:
            library_stems.append(marker)
    final = kept + added
    if not final:
        raise QuickRefineError("A refinement needs at least one phase.")
    if len(final) > MAX_PHASES:
        raise QuickRefineError(f"At most {MAX_PHASES} phases can be refined together.")
    for stem in final:
        if not valid_reference_filename(stem):
            raise QuickRefineError("Invalid reference CIF filename.")
    return PhasePlan(final, {s: cifs[s] for s in final}, removed, added, library_stems)


def merge_background(base_params: dict | None, background: QuickBackground | None) -> dict:
    """Refinement parameters with the requested background change applied.

    ``degree`` replaces ``background_degree`` (``None`` after an explicit ``null`` returns to
    BGMN's automatic choice); ``excluded_ranges`` are *added* to the ranges already excluded.
    """
    params = deepcopy(base_params or {})
    if background is None:
        return params
    given = background.model_fields_set
    if "degree" in given:
        if background.degree is None:
            params.pop("background_degree", None)
        else:
            params["background_degree"] = background.degree
    if background.excluded_ranges:
        params["excluded_ranges"] = normalize_excluded_ranges(
            list(params.get("excluded_ranges") or []) + background.excluded_ranges
        )
    return params


# --------------------------------------------------------------------------- the job body
def _remove_run_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_old_runs(task_dir: Path) -> None:
    runs = task_dir / "runs"
    if not runs.is_dir():
        return
    cutoff = time.time() - FAILED_RUN_KEEP_S
    for child in runs.iterdir():
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def _run_job(job: QuickJob, record: dict, raw: dict | None, base_version: dict | None,
             library: dict, request: QuickRefineRequest) -> dict:
    """Worker-thread body; returns the version payload (no store access here)."""
    if job.cancel.is_set():
        raise RefinementCancelled("Cancelled before it started.")
    t_start = time.monotonic()
    _update(job, status="running", stage="Preparing inputs")
    task_uuid = record["uuid"]
    task_dir = task_cache(task_uuid)
    document = _DOCUMENTS.get(task_uuid, raw if raw is not None else job.raw, job.document)
    job.raw = None  # decoded now; the cache (or this local) holds it from here on
    if job.discarded:  # the task was deleted while the document was being decoded
        _DOCUMENTS.discard(task_uuid)
        raise RefinementCancelled("The task was deleted.")
    selection = versions.parse_selection(request.base)
    plan = plan_phases(document, selection, base_version, request, library)

    base_params = (
        (base_version or {}).get("refinement_params")
        if base_version is not None
        else (getattr(document, "search_kwargs", None) or {}).get("refinement_params")
    )
    refinement_params = merge_background(base_params, request.background)
    if request.background and request.background.excluded_ranges:
        validate_analysis_range(
            np.asarray(document.xrd_data.angles, dtype=float),
            refinement_params.get("wmin"), refinement_params.get("wmax"),
            refinement_params.get("excluded_ranges"),
        )

    function_kwargs = (record.get("job") or {}).get("function_kwargs") or {}
    inherited_uploaded = list((base_version or {}).get("uploaded_stems") or [])
    uploaded = sorted(set(inherited_uploaded) | set(plan.library_stems))
    pattern_path = _ensure_pattern(document, task_dir)
    identity_paths = [Path(f"{stem}.cif") for stem in plan.stems]  # only the stems matter here
    spec = build_final_refinement_spec(
        document, function_kwargs, identity_paths,
        controls=request.controls,
        phase_params=(base_version or {}).get("phase_params") if base_version else None,
        overrides=(base_version or {}).get("overrides") if base_version else None,
        extra_additional_stems=uploaded,
        refinement_params=refinement_params,
    )
    effective = spec.effective_phase_params()

    peaks_future = _prep_pool().submit(
        _observed_peaks, task_dir, pattern_path, spec.refinement_kwargs,
        spec.refinement_kwargs.get("refinement_params"),
    )
    cif_texts = {stem: str(cif) for stem, cif in plan.cifs.items()}
    str_paths = [
        _cached_str(task_dir, stem, cif_texts[stem], effective[stem]) for stem in plan.stems
    ]
    if job.cancel.is_set():
        raise RefinementCancelled("Cancelled while preparing.")
    prepare_s = time.monotonic() - t_start

    marks: dict[str, float] = {}

    def lazy_peaks():
        marks["bgmn_done"] = time.monotonic()
        _update(job, stage="Peak diagnostics")
        return peaks_future.result()

    run_dir = task_dir / "runs" / job.job_id
    _update(job, stage="BGMN refinement")
    t_bgmn = time.monotonic()
    try:
        result = do_refinement(
            pattern_path=pattern_path,
            phases=str_paths,
            working_dir=run_dir,
            phase_params=spec.phase_params,
            show_progress=False,
            observed_peaks=lazy_peaks,
            cancel_event=job.cancel,
            **spec.refinement_kwargs,
        )
    except BaseException:
        if job.cancel.is_set():
            _remove_run_dir(run_dir)
        raise
    t_done = time.monotonic()
    bgmn_s = marks.get("bgmn_done", t_done) - t_bgmn
    diagnostics_s = t_done - marks.get("bgmn_done", t_done)
    _remove_run_dir(run_dir)

    refs_before = versions.document_references(document)
    new_cifs = [
        {"filename": stem, "cif_text": cif_texts[stem]}
        for stem in plan.stems
        if stem not in refs_before
    ]
    effective_final = {stem: effective[stem] for stem in plan.stems}
    record_out = {
        "job_id": job.job_id,
        "label": default_label(request),
        "origin": {
            "kind": request.origin,
            "base": versions.format_selection(selection),
            "added": plan.added,
            "removed": plan.removed,
        },
        "phases": plan.stems,
        "cifs": new_cifs,
        "uploaded_stems": uploaded,
        "controls": request.controls.model_dump() if request.controls else None,
        "background": (
            {
                "degree": refinement_params.get("background_degree"),
                "excluded_ranges": refinement_params.get("excluded_ranges") or [],
            }
            if request.background else (base_version or {}).get("background")
        ),
        "phase_params": versions.encode_json(spec.phase_params),
        "overrides": versions.encode_json(_spec_overrides(spec)),
        "refinement_params": versions.encode_json(refinement_params),
        "effective_phase_params": versions.encode_json(effective_final),
        "elapsed_s": None,
        "bgmn_s": round(bgmn_s, 3),
        "result": versions.encode_json(result),
    }
    if job.cancel.is_set():
        raise RefinementCancelled("Cancelled.")
    t_summary = time.monotonic()
    _update(job, stage="Peak diagnostics")
    extra_uploaded = [plan.cifs[s] for s in plan.library_stems if s in plan.cifs]
    record_out["summary"] = versions.build_version_summary(
        record, document, record_out, extra_uploaded=extra_uploaded
    )
    summary_s = time.monotonic() - t_summary
    total = time.monotonic() - t_start
    record_out["elapsed_s"] = round(total, 3)
    timings = {
        "prepare_s": round(prepare_s, 3),
        "bgmn_s": round(bgmn_s, 3),
        "diagnostics_s": round(diagnostics_s, 3),
        "summary_s": round(summary_s, 3),
        "total_s": round(total, 3),
    }
    _update(job, stage="Storing version", timings=timings)
    return record_out


def _spec_overrides(spec) -> dict:
    return dict(getattr(spec, "overrides", None) or {})


# --------------------------------------------------------------------------- scheduling
_NO_DEGREE = object()


def _record_degree(record: dict):
    """Explicit background degree a stored version was refined with (``None``: automatic)."""
    params = record.get("refinement_params")
    return params.get("background_degree") if isinstance(params, dict) else None


def effective_degree(request: QuickRefineRequest, base_version: dict | None, record: dict):
    """Background degree the new run will use (``None``: automatic)."""
    background = request.background
    if background is not None and "degree" in background.model_fields_set:
        return background.degree
    if base_version is not None:
        return _record_degree(base_version)
    params = (((record.get("job") or {}).get("function_kwargs") or {}).get("search_kwargs") or {})
    params = params.get("refinement_params")
    return params.get("background_degree") if isinstance(params, dict) else None


def _eta(task_uuid: str, n_phases: int, degree=_NO_DEGREE) -> float | None:
    """Median BGMN time of earlier versions; ``None`` ("estimating") when none is comparable.

    The run time grows with the number of background terms, so with a known ``degree`` only
    versions refined with the same degree count - a stale number would be misleading.
    """
    try:
        records = versions.list_versions(task_uuid)
    except Exception:  # noqa: BLE001
        return None
    if degree is not _NO_DEGREE:
        records = [r for r in records if _record_degree(r) == degree]
    same = [r["bgmn_s"] for r in records if r.get("bgmn_s") and len(r.get("phases") or []) == n_phases]
    anyv = [r["bgmn_s"] for r in records if r.get("bgmn_s")]
    pool = same or anyv
    return round(statistics.median(pool), 1) if pool else None


async def _finish(job: QuickJob, future, task_index: int) -> None:
    """Persist the outcome on the event-loop thread (the only thread allowed to open stores)."""
    try:
        await _finish_inner(job, future, task_index)
    finally:
        job.document = job.raw = None  # release the strong references
        if job.discarded:  # a running job may have written files after the task was deleted
            shutil.rmtree(task_cache(job.task_uuid), ignore_errors=True)


async def _finish_inner(job: QuickJob, future, task_index: int) -> None:
    try:
        payload = await future
    except RefinementCancelled:
        _update(job, status="cancelled", stage="Done", error=None)
        return
    except asyncio.CancelledError:
        _update(job, status="cancelled", stage="Done")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info("Quick refinement %s failed", job.job_id, exc_info=True)
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        _update(job, status="failed", stage="Done", error=message[:500])
        return
    if job.cancel.is_set() or job.discarded:
        _update(job, status="cancelled", stage="Done")
        return
    try:
        payload["created_at"] = versions.utc_now()
        number = versions.save_version(job.task_uuid, task_index, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store version for %s", job.job_id, exc_info=True)
        _update(job, status="failed", stage="Done", error=f"Could not store the result: {exc}"[:500])
        return
    _update(job, status="done", stage="Done", version=number)


def _load_completed(task_id: int) -> dict:
    with get_worker_store() as store:
        record = store.query_one({"index": task_id})
    if record is None:
        raise HTTPException(404, "Task not found")
    if record.get("status") != "COMPLETED":
        raise HTTPException(409, "Wait for the task to complete before re-refining.")
    return record


async def submit_quick_refine(task_id: int, request: QuickRefineRequest) -> dict:
    """Queue a quick refinement; returns as soon as the job is scheduled."""
    record = _load_completed(task_id)
    if _queued_count() >= MAX_QUEUED:
        raise HTTPException(429, "Too many re-refinements are waiting; try again shortly.")
    selection = versions.parse_selection(request.base)
    base_version = (
        versions.load_version(record["uuid"], selection[1]) if isinstance(selection, tuple) else None
    )
    library: dict = {}
    wanted = [m.group(1) for p in request.phases if (m := LIBRARY_SOURCE_RE.match(p.source))]
    if wanted:
        try:
            from dara.server.reference_library_router import load_library_cifs

            library = dict(zip(wanted, load_library_cifs(wanted)))
        except ImportError:
            raise HTTPException(400, "The reference CIF library is not available on this server.")
        except KeyError as exc:
            raise HTTPException(400, f"Unknown reference library CIF: {exc.args[0]}")
    refs_seen: set[str] = set()
    for item in request.phases:
        if item.ref in refs_seen:
            raise HTTPException(400, "Add each reference only once.")
        refs_seen.add(item.ref)
    decoded, raw = _document_handles(record["uuid"])
    job = _new_job(task_id, record["uuid"], default_label(request))
    job.document, job.raw = decoded, raw
    job.eta_s = _eta(
        record["uuid"], len(base_version["phases"]) if base_version else 0,
        effective_degree(request, base_version, record),
    )
    _persist(job)
    loop = asyncio.get_running_loop()
    try:
        future = loop.run_in_executor(
            _refine_pool(), _run_job, job, dict(record), raw, base_version, library, request
        )
    except RuntimeError as exc:
        _update(job, status="failed", stage="Done", error="The server is shutting down.")
        raise HTTPException(503, "The server is shutting down.") from exc
    task = asyncio.create_task(_finish(job, future, record["index"]))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    position = self_position(job)
    return {
        "job_id": job.job_id,
        "status": "queued",
        "eta_s": job.eta_s,
        "position": position,
        "ahead": max(0, position - 1),
    }


def warm_task(record: dict, raw: dict | None) -> None:
    """Pre-decode the document and pre-compute the observed peaks (best effort)."""

    def work():
        try:
            document = _DOCUMENTS.get(record["uuid"], raw)
            task_dir = task_cache(record["uuid"])
            pattern = _ensure_pattern(document, task_dir)
            kwargs = {
                key: value
                for key, value in (document.search_kwargs or {}).items()
                if key in {"wavelength", "instrument_profile"}
            }
            _observed_peaks(
                task_dir, pattern, kwargs, (document.search_kwargs or {}).get("refinement_params")
            )
        except Exception:  # noqa: BLE001
            logger.debug("Quick-refine warm-up failed", exc_info=True)

    try:
        _prep_pool().submit(work)
    except RuntimeError:
        pass


def maybe_warm(record: dict) -> None:
    """Event-loop helper used by the versions listing: warm a cold COMPLETED task."""
    if record.get("status") != "COMPLETED" or _DOCUMENTS.has(record["uuid"]):
        return
    try:
        warm_task(dict(record), _read_raw_document(record["uuid"]))
    except Exception:  # noqa: BLE001
        logger.debug("Could not start the quick-refine warm-up", exc_info=True)


# --------------------------------------------------------------------------- routes
@router.post("/task/{task_id}/quick-refine", status_code=202)
async def quick_refine(task_id: int, request: QuickRefineRequest):
    return await submit_quick_refine(task_id, request)


def _job_payload(job_id: str) -> dict:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "Unknown job")
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            return job.snapshot(self_position(job))
    data = _load_job_file(job_id)
    if data is None:
        raise HTTPException(404, "Unknown job")
    return data


@router.get("/quick-jobs/{job_id}")
async def get_quick_job(job_id: str):
    return _job_payload(job_id)


@router.post("/quick-jobs/{job_id}/cancel")
async def cancel_quick_job(job_id: str):
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "Unknown job")
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None and job.status in _ACTIVE:
            job.cancel.set()
            if job.status == "queued":
                _update(job, status="cancelled", stage="Done")
    return _job_payload(job_id)


@router.get("/task/{task_id}/quick-jobs")
async def task_quick_jobs(task_id: int):
    with get_worker_store() as store:
        record = store.query_one({"index": task_id})
    if record is None:
        raise HTTPException(404, "Task not found")
    seen: dict[str, dict] = {}
    try:
        directory = task_cache(record["uuid"]) / "jobs"
    except ValueError:
        directory = None
    if directory is not None and directory.is_dir():
        files = sorted(directory.glob("q-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        for path in files:
            data = _read_job_file(path)
            if data:
                seen[data["job_id"]] = data
    with _LOCK:
        for job in _JOBS.values():
            if job.task_uuid == record["uuid"]:
                seen[job.job_id] = job.snapshot(self_position(job))
    ordered = sorted(seen.values(), key=lambda item: item.get("created_at") or "", reverse=True)
    active = [item for item in ordered if item.get("status") in _ACTIVE]
    recent = [item for item in ordered if item.get("status") not in _ACTIVE][:10]
    return {"task_id": task_id, "active": active, "recent": recent, "jobs": active + recent}


def discard_task(task_uuid: str) -> dict:
    """Forget a deleted task: cancel its quick jobs, drop caches and delete its cache directory.

    Returns ``{"cancelled": n, "removed_cache": bool}``.  Event-loop thread.
    """
    uuid = str(UUID(str(task_uuid)))
    cancelled = 0
    with _LOCK:
        mine = [job for job in _JOBS.values() if job.task_uuid == uuid]
        for job in mine:
            job.discarded = True
            if job.status in _ACTIVE:
                job.cancel.set()
                cancelled += 1
                if job.status == "queued":
                    _update(job, status="cancelled", stage="Done")
    _DOCUMENTS.discard(uuid)
    target = task_cache(uuid)
    removed = False
    root = cache_root().resolve()
    # ``task_cache`` is root/<canonical uuid>; refuse anything that is not exactly that.
    if target.resolve().parent == root and target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
        removed = not target.exists()
    return {"cancelled": cancelled, "removed_cache": removed}


# --------------------------------------------------------------------------- shared document access
async def load_document(task_uuid: str):
    """Decoded document of a COMPLETED task, cached; decoding runs off the event loop.

    The stored document is read on the calling (event-loop) thread, as montydb requires.
    """
    ready, raw = _document_handles(task_uuid)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _DOCUMENTS.get, task_uuid, raw, ready)
