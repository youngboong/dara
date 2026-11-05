"""In-process quick re-refinement: parity with the normal final refinement, latency, threading."""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from copy import deepcopy
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from monty.json import MontyEncoder
from pydantic import ValidationError

from dara import bgmn_worker, refine, selected_refinement
from dara.bgmn_worker import BGMNWorker, RefinementCancelled, ensure_bgmn_env
from dara.cif2str import cif2str
from dara.refinement_controls import RefinementControls
from dara.result import DiaResult
from dara.server import quick_refine, versions
from dara.server.quick_refine import QuickBackground, QuickPhase, QuickRefineRequest
from dara.xrd import XRDData
from test_selected_refinement import _document, _refined, references  # noqa: F401

TASK_UUID = str(uuid4())
FAKE_RWP = 5.0


class FakeStore:
    """Minimal montydb stand-in recording the threads that touch it."""

    def __init__(self, docs=None):
        self.docs = [deepcopy(d) for d in (docs or [])]
        self.threads: list[int] = []

    def __call__(self):
        return self

    def __enter__(self):
        self.threads.append(threading.get_ident())
        return self

    def __exit__(self, *args):
        return False

    @staticmethod
    def _match(doc, criteria):
        return all(doc.get(key) == value for key, value in criteria.items())

    @staticmethod
    def _project(doc, properties):
        if not properties:
            return deepcopy(doc)
        return {key: deepcopy(doc[key]) for key in properties if key in doc}

    def query_one(self, criteria, properties=None):
        for doc in self.docs:
            if self._match(doc, criteria):
                return self._project(doc, properties)
        return None

    def query(self, criteria, properties=None):
        return [self._project(d, properties) for d in self.docs if self._match(d, criteria)]

    def count(self, criteria):
        return sum(1 for d in self.docs if self._match(d, criteria))

    def remove_docs(self, criteria):
        self.docs = [d for d in self.docs if not self._match(d, criteria)]

    def update(self, docs, key=None):
        for doc in docs if isinstance(docs, list) else [docs]:
            for index, existing in enumerate(self.docs):
                if existing.get("uuid") == doc["uuid"] and existing.get("index") == doc["index"]:
                    self.docs[index] = deepcopy(doc)
                    break
            else:
                self.docs.append(deepcopy(doc))


@pytest.fixture
def env(monkeypatch, tmp_path, references):
    document = _document(references)
    angles = np.linspace(10, 30, 41)
    document.xrd_data = XRDData(angles, np.full(41, 5.0), errors=np.full(41, 1.0))
    stamp = datetime(2026, 9, 8, 1, 0, 0)
    record = {
        "index": 7, "uuid": TASK_UUID, "status": "COMPLETED", "user": "tester",
        "submitted_time": stamp, "start_time": stamp, "end_time": stamp,
        "job": {"name": "test.xy", "function_kwargs": {}, "metadata": {}},
    }
    workers = FakeStore([record])
    results = FakeStore([{"uuid": TASK_UUID, "output": json.loads(json.dumps(document, cls=MontyEncoder))}])
    version_store = FakeStore()
    monkeypatch.setattr(quick_refine, "get_worker_store", workers)
    monkeypatch.setattr(quick_refine, "get_result_store", results)
    monkeypatch.setattr(versions, "get_versions_store", version_store)
    short = Path(tempfile.mkdtemp(prefix="qr"))  # short path: Windows MAX_PATH with long stems
    monkeypatch.setattr(quick_refine, "cache_root", lambda: short / "quick-refine")
    monkeypatch.setattr(
        quick_refine, "_compute_peaks",
        lambda *args, **kwargs: pd.DataFrame({"2theta": [20.0], "intensity": [10.0]}),
    )
    quick_refine._DOCUMENTS.clear()
    quick_refine._JOBS.clear()
    yield {
        "document": document, "record": record, "workers": workers, "results": results,
        "versions": version_store, "cache": short / "quick-refine", "references": references,
    }
    shutil.rmtree(short, ignore_errors=True)
    quick_refine.shutdown()
    quick_refine._DOCUMENTS.clear()
    quick_refine._JOBS.clear()


def _fake_refinement(captured=None, delay=0.0, rwp=FAKE_RWP, threads=None):
    @wraps(refine.do_refinement)
    def fake(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        if threads is not None:
            threads.append(threading.get_ident())
        if delay:
            time.sleep(delay)
        names = [Path(p).stem for p in kwargs["phases"]]
        result = _refined([type("C", (), {"filename": name}) for name in names])
        result.lst_data.rwp = rwp
        return result

    return fake


async def _wait(job_id, states=("done", "failed", "cancelled"), timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = await quick_refine.get_quick_job(job_id)
        if job["status"] in states:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {states}")


def _run(coro):
    return asyncio.run(coro)


def _submit(request, task_id=7):
    return quick_refine.submit_quick_refine(task_id, request)


def _all_inherit():
    return QuickRefineRequest(controls=RefinementControls())


# --------------------------------------------------------------------------- parity
def test_parity_with_refine_selected_arguments_and_str(monkeypatch, env, tmp_path):
    references = env["references"]
    document = env["document"]
    captured_quick, captured_selected = [], []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(captured_quick))

    @wraps(refine.do_refinement)
    def selected_fake(**kwargs):
        captured_selected.append(kwargs)
        return _refined(references)

    monkeypatch.setattr(selected_refinement, "do_refinement", selected_fake)
    monkeypatch.chdir(tmp_path)
    maker = selected_refinement.SelectedPhaseRefinementMaker(name="parity")
    maker.make.original(
        maker, document.xrd_data, references, document.search_kwargs,
        document.final_refinement_params, {},
    )

    async def main():
        started = await _submit(_all_inherit())
        return await _wait(started["job_id"])

    job = _run(main())
    assert job["status"] == "done", job
    quick, selected = captured_quick[0], captured_selected[0]
    for key in ("phase_params", "wavelength", "instrument_profile"):
        assert quick[key] == selected[key], key
    # Same refinement settings; quick re-refinements only add the per-stage iteration cap.
    quick_params = dict(quick["refinement_params"])
    assert quick_params.pop("itmax") == quick_refine.QUICK_REFINE_ITMAX
    assert quick_params == selected["refinement_params"]
    assert quick["show_progress"] is False
    assert callable(quick["observed_peaks"])
    assert isinstance(quick["cancel_event"], threading.Event)

    # The .str passed to BGMN equals what do_refinement would generate (mod. the timestamp).
    for path, cif in zip(quick["phases"], references):
        assert path.suffix == ".str" and path.stem == cif.filename
        cif_dir = tmp_path / "plain" / cif.filename
        cif_dir.mkdir(parents=True)
        cif_path = cif_dir / f"{cif.filename}.cif"
        cif_path.write_text(str(cif))
        expected = cif2str(cif_path, "", cif_dir, **quick["phase_params"])
        assert quick_refine.strip_str_timestamp(path.read_text()) == quick_refine.strip_str_timestamp(
            expected.read_text()
        )

    stored = env["versions"].query({"uuid": TASK_UUID})
    assert len(stored) == 1 and stored[0]["version"] == 2
    assert stored[0]["summary"]["rwp"] == pytest.approx(FAKE_RWP, abs=1e-6)
    assert stored[0]["phases"] == [cif.filename for cif in references]
    assert stored[0]["effective_phase_params"][references[0].filename] == document.final_refinement_params


def test_observed_peaks_argument_gives_identical_metrics(monkeypatch, tmp_path, references):
    x = np.linspace(10, 80, 200)
    calc = 100 * np.exp(-((x - 30) ** 2) / 0.3) + 60 * np.exp(-((x - 50) ** 2) / 0.3)
    obs = calc + 80 * np.exp(-((x - 65) ** 2) / 0.3) + 2.0

    def build():
        result = _refined(references)
        result.plot_data = DiaResult(
            x=x.tolist(), y_obs=obs.tolist(), y_calc=calc.tolist(), y_bkg=[1.0] * len(x),
            structs={cif.filename: calc.tolist() for cif in references},
        )
        result.peak_data = pd.DataFrame(
            {"phase": [references[0].filename] * 2, "2theta": [30.0, 50.0], "intensity": [100.0, 60.0]}
        )
        return result

    observed = pd.DataFrame({"2theta": [30.0, 50.0, 65.0], "intensity": [100.0, 60.0, 80.0]})
    import dara.peak_detection as peak_detection

    monkeypatch.setattr(peak_detection, "detect_peaks", lambda *a, **k: observed.copy())
    reference_result = build()
    refine._attach_peak_markers(reference_result, tmp_path / "p.xy", "Cu", "dev")
    for variant in (observed, observed.to_numpy(), lambda: observed):
        monkeypatch.setattr(
            peak_detection, "detect_peaks",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("EFLECH must not run")),
        )
        candidate = build()
        refine._attach_peak_markers(candidate, tmp_path / "p.xy", "Cu", "dev", observed_peaks=variant)
        for name in ("missing_peaks", "extra_peaks", "intensity_mismatch_peaks"):
            a = getattr(reference_result.refinement_metrics, name)
            b = getattr(candidate.refinement_metrics, name)
            assert (a is None) == (b is None), name
            if a is not None:
                assert np.allclose(a, b, atol=1e-6), name
        assert candidate.refinement_metrics.rwp == reference_result.refinement_metrics.rwp
    assert reference_result.refinement_metrics.missing_peaks is not None  # the 65 degree peak


def test_observed_peaks_is_not_a_default_search_kwarg():
    from inspect import signature

    from dara.search import search_phases

    assert "observed_peaks" not in signature(search_phases).parameters
    assert "cancel_event" not in signature(search_phases).parameters
    source = (Path(quick_refine.__file__).parent / "api_router.py").read_text(encoding="utf-8")
    assert "observed_peaks" not in source


# --------------------------------------------------------------------------- latency / threads
def test_job_is_running_within_a_second_and_post_is_fast(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(delay=1.5))

    async def main():
        t0 = time.monotonic()
        started = await _submit(_all_inherit())
        post_s = time.monotonic() - t0
        t1 = time.monotonic()
        while True:
            job = await quick_refine.get_quick_job(started["job_id"])
            if job["status"] != "queued":
                break
            await asyncio.sleep(0.02)
        running_s = time.monotonic() - t1
        final = await _wait(started["job_id"])
        return post_s, running_s, started, final

    post_s, running_s, started, final = _run(main())
    assert started["status"] == "queued" and started["job_id"].startswith("q-")
    assert post_s < 0.5, post_s
    assert running_s < 1.0, running_s
    assert final["status"] == "done"
    assert final["timings"]["total_s"] >= 1.5
    assert {"prepare_s", "bgmn_s", "diagnostics_s", "summary_s"} <= set(final["timings"])


def test_store_writes_stay_on_the_event_loop_thread(monkeypatch, env):
    worker_threads: list[int] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(threads=worker_threads))
    loop_thread = {}

    async def main():
        loop_thread["id"] = threading.get_ident()
        started = await _submit(_all_inherit())
        return await _wait(started["job_id"])

    job = _run(main())
    assert job["status"] == "done"
    assert worker_threads and worker_threads[0] != loop_thread["id"]
    for store in (env["versions"], env["results"], env["workers"]):
        assert store.threads, "store untouched"
        assert set(store.threads) == {loop_thread["id"]}


def test_version_flow_remove_background_and_child_versions(monkeypatch, env):
    captured: list[dict] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(captured))
    references = env["references"]

    async def main():
        first = await _submit(
            QuickRefineRequest(
                remove=[references[1].filename], origin="remove",
                background=QuickBackground(degree=8, excluded_ranges=[[24.0, 26.0]]),
            )
        )
        v2 = await _wait(first["job_id"])
        assert v2["status"] == "done", v2
        second = await _submit(QuickRefineRequest(base="version:2", controls=RefinementControls(orientation="isotropic")))
        v3 = await _wait(second["job_id"])
        return v2, v3

    v2, v3 = _run(main())
    assert (v2["status"], v2["version"]) == ("done", 2), v2
    assert (v3["status"], v3["version"]) == ("done", 3), v3
    first, second = captured
    assert [p.stem for p in first["phases"]] == [references[0].filename]
    assert first["refinement_params"]["background_degree"] == 8
    # stored ranges (12-29 with 18-19 excluded) are kept and the new hump is added
    assert first["refinement_params"]["excluded_ranges"] == [[18.0, 19.0], [24.0, 26.0]]
    assert first["refinement_params"]["wmin"] == 12.0
    stored = {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}
    assert stored[2]["origin"] == {
        "kind": "remove", "base": "best", "added": [], "removed": [references[1].filename],
    }
    assert stored[2]["background"] == {"degree": 8, "excluded_ranges": [[18.0, 19.0], [24.0, 26.0]]}
    assert stored[2]["label"].startswith("Removed")
    # v3 starts from v2: same phases, background kept, controls applied on top
    assert [p.stem for p in second["phases"]] == [references[0].filename]
    assert second["refinement_params"]["background_degree"] == 8
    assert second["phase_params"]["gewicht"] == "0_0"
    assert stored[3]["origin"]["base"] == "version:2"
    assert stored[3]["overrides"] == {"gewicht": "0_0"}
    caution_codes = [c["code"] for c in stored[2]["summary"]["cautions"]]
    assert "background_model_changed" in caution_codes


# --------------------------------------------------------------------------- cancel
def test_queued_job_is_cancelled_before_it_starts(monkeypatch, env):
    calls: list[dict] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(calls, delay=0.8))

    async def main():
        first = await _submit(_all_inherit())
        second = await _submit(_all_inherit())
        assert (await quick_refine.get_quick_job(second["job_id"]))["status"] == "queued"
        cancelled = await quick_refine.cancel_quick_job(second["job_id"])
        again = await quick_refine.cancel_quick_job(second["job_id"])  # idempotent
        done = await _wait(first["job_id"])
        await asyncio.sleep(0.3)  # let the queued body run (it must bail out immediately)
        return cancelled, again, done, await quick_refine.get_quick_job(second["job_id"])

    cancelled, again, done, second = _run(main())
    assert cancelled["status"] == again["status"] == second["status"] == "cancelled"
    assert done["status"] == "done"
    assert len(calls) == 1
    assert len(env["versions"].query({"uuid": TASK_UUID})) == 1


def test_running_job_is_cancelled_through_the_event(monkeypatch, env):
    @wraps(refine.do_refinement)
    def waiting(**kwargs):
        if kwargs["cancel_event"].wait(20):
            raise RefinementCancelled("stop")
        raise AssertionError("never cancelled")

    monkeypatch.setattr(quick_refine, "do_refinement", waiting)

    async def main():
        started = await _submit(_all_inherit())
        job = await _wait(started["job_id"], states=("running",))
        await asyncio.sleep(0.2)
        await quick_refine.cancel_quick_job(started["job_id"])
        return job, await _wait(started["job_id"])

    running, final = _run(main())
    assert running["status"] == "running"
    assert final["status"] == "cancelled"
    assert env["versions"].query({"uuid": TASK_UUID}) == []


# --------------------------------------------------------------------------- validation
def test_request_schema_limits():
    with pytest.raises(ValidationError):
        QuickRefineRequest(phases=[QuickPhase(ref=f"p{i}") for i in range(21)])
    with pytest.raises(ValidationError):
        QuickRefineRequest(remove=[str(i) for i in range(21)])
    with pytest.raises(ValidationError, match="anchor"):
        QuickBackground(anchors=[[10, 1]])
    with pytest.raises(ValidationError):
        QuickBackground(degree=1)
    with pytest.raises(ValidationError):
        QuickBackground(excluded_ranges=[[30, 20]])
    with pytest.raises(ValidationError):
        QuickRefineRequest()  # nothing to do
    with pytest.raises(ValidationError):
        QuickRefineRequest(remove=["x"], base="version:0")
    with pytest.raises(ValidationError):
        QuickRefineRequest(remove=["x"], extra_field=1)
    with pytest.raises(ValidationError):
        QuickPhase(ref="a", source="http://evil")
    assert QuickRefineRequest(remove=["x"], base=3).base == 3


@pytest.mark.parametrize(
    "request_kwargs, message",
    [
        ({"remove": ["Unknown"]}, "Cannot remove Unknown"),
        ({"phases": [{"ref": "NotThere"}]}, "not available for this task"),
        ({"phases": [{"ref": "BiFeO3"}]}, "already part of the phase set"),
        ({"phases": [{"ref": "Fe3O4_(cod_1)-0", "source": "cod:123"}]}, "does not match COD entry"),
        ({"phases": [{"ref": "Fe3O4_227_(cod_123)-0", "source": "cod:123"}]}, "not in the local cache"),
        ({"remove": ["BiFeO3", "Bi2Fe4O9"]}, "at least one phase"),
        ({"remove": ["BiFeO3"], "base": 5}, "Unknown candidate index"),
    ],
)
def test_invalid_requests_fail_fast_with_a_message(monkeypatch, env, tmp_path, request_kwargs, message):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())
    monkeypatch.setenv("DARA_COD_CACHE_DIR", str(tmp_path / "no-cod"))
    request_kwargs = dict(request_kwargs)
    request_kwargs["phases"] = [QuickPhase(**item) for item in request_kwargs.get("phases", [])]

    async def main():
        t0 = time.monotonic()
        started = await _submit(QuickRefineRequest(**request_kwargs))
        job = await _wait(started["job_id"])
        return job, time.monotonic() - t0

    job, elapsed = _run(main())
    assert job["status"] == "failed"
    assert message in job["error"]
    assert elapsed < 5
    assert env["versions"].query({"uuid": TASK_UUID}) == []


def test_unfinished_unknown_and_library_errors(monkeypatch, env):
    env["workers"].docs[0]["status"] = "RUNNING"
    with pytest.raises(HTTPException) as exc:
        _run(_submit(_all_inherit()))
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        _run(_submit(_all_inherit(), task_id=99))
    assert exc.value.status_code == 404
    env["workers"].docs[0]["status"] = "COMPLETED"
    with pytest.raises(HTTPException) as exc:
        _run(_submit(QuickRefineRequest(phases=[QuickPhase(ref="x", source="library:missing")])))
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        _run(_submit(QuickRefineRequest(base="version:9", controls=RefinementControls())))
    assert exc.value.status_code == 404


def test_too_many_queued_jobs_get_429(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(delay=0.5))

    async def main():
        for _ in range(quick_refine.MAX_QUEUED + 1):  # one running + MAX_QUEUED waiting
            await _submit(_all_inherit())
        with pytest.raises(HTTPException) as exc:
            await _submit(_all_inherit())
        for job in list(quick_refine._JOBS.values()):
            await quick_refine.cancel_quick_job(job.job_id)
        return exc.value.status_code

    assert _run(main()) == 429


def test_unknown_job_ids_are_404():
    for bad in ("nope", "q-xyz", "../../etc/passwd", "q-0123456789abc"):
        with pytest.raises(HTTPException) as exc:
            _run(quick_refine.get_quick_job(bad))
        assert exc.value.status_code == 404


# --------------------------------------------------------------------------- restart / numbering
def test_interrupted_jobs_are_rewritten_as_failed(env):
    jobs_dir = env["cache"] / TASK_UUID / "jobs"
    jobs_dir.mkdir(parents=True)
    for name, status in (("q-aaaaaaaaaaaa", "running"), ("q-bbbbbbbbbbbb", "queued"), ("q-cccccccccccc", "done")):
        (jobs_dir / f"{name}.json").write_text(
            json.dumps({"job_id": name, "status": status, "stage": "Queued", "task_id": 7})
        )
    # visible as failed even before the start-up scan ran
    assert _run(quick_refine.get_quick_job("q-aaaaaaaaaaaa"))["error"] == "Interrupted by a server restart."
    assert quick_refine.recover_interrupted() == 2
    saved = {p.stem: json.loads(p.read_text()) for p in jobs_dir.glob("*.json")}
    assert saved["q-aaaaaaaaaaaa"]["status"] == "failed"
    assert saved["q-aaaaaaaaaaaa"]["error"] == "Interrupted by a server restart."
    assert saved["q-bbbbbbbbbbbb"]["status"] == "failed"
    assert saved["q-cccccccccccc"]["status"] == "done"
    listing = _run(quick_refine.task_quick_jobs(7))
    assert {item["job_id"] for item in listing["recent"]} == set(saved)


def test_version_numbers_are_never_reused(env):
    for _ in range(2):
        versions.save_version(TASK_UUID, 7, {"label": "x", "phases": [], "result": {"a": 1}})
    assert [v["version"] for v in versions.list_versions(TASK_UUID)] == [2, 3]
    versions.tombstone_version(TASK_UUID, 3)
    assert [v["version"] for v in versions.list_versions(TASK_UUID)] == [2]
    assert versions.next_version_number(TASK_UUID) == 4
    assert versions.save_version(TASK_UUID, 7, {"label": "y", "phases": []}) == 4
    stub = [d for d in env["versions"].docs if d["index"] == 3][0]
    assert stub["deleted"] is True and stub["result"] is None
    with pytest.raises(HTTPException) as exc:
        versions.load_version(TASK_UUID, 3)
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- planning helpers
def test_remove_accepts_bgmn_phase_names(env):
    references = env["references"]
    references[0].filename = "FeBiO3_161_(cod_2300000)-0"
    document = _document(references)
    from dara.utils import process_phase_name

    request = QuickRefineRequest(remove=[process_phase_name(references[0].filename)])
    plan = quick_refine.plan_phases(document, "best", None, request, {})
    assert plan.stems == [references[1].filename]
    assert plan.removed == [references[0].filename]


def test_merge_background_semantics():
    base = {"wmin": 12.0, "excluded_ranges": [[18.0, 19.0]], "background_degree": 6}
    merged = quick_refine.merge_background(base, QuickBackground(degree=9, excluded_ranges=[[18.5, 20.0]]))
    assert merged == {"wmin": 12.0, "excluded_ranges": [[18.0, 20.0]], "background_degree": 9}
    assert base["background_degree"] == 6  # input untouched
    assert "background_degree" not in quick_refine.merge_background(base, QuickBackground(degree=None))
    assert quick_refine.merge_background(base, QuickBackground(excluded_ranges=[[40, 41]]))["background_degree"] == 6
    assert quick_refine.merge_background(base, None) == base


def test_str_and_peak_caches_are_reused(monkeypatch, env, tmp_path):
    calls = []
    real = quick_refine.cif2str

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(quick_refine, "cif2str", counting)
    cif = env["references"][0]
    params = {"gewicht": "SPHAR4", "lattice_range": 0.02}
    task_dir = tmp_path / "t"
    first = quick_refine._cached_str(task_dir, cif.filename, str(cif), params)
    second = quick_refine._cached_str(task_dir, cif.filename, str(cif), params)
    third = quick_refine._cached_str(task_dir, cif.filename, str(cif), {**params, "lattice_range": 0.05})
    assert first == second != third and len(calls) == 2
    assert first.name == f"{cif.filename}.str"

    peak_calls = []
    monkeypatch.setattr(
        quick_refine, "_compute_peaks",
        lambda *a, **k: peak_calls.append(a) or pd.DataFrame({"2theta": [1.0], "intensity": [2.0]}),
    )
    pattern = tmp_path / "x.xy"
    pattern.write_text("10 1\n20 2\n30 1\n")
    a = quick_refine._observed_peaks(task_dir, pattern, {"wavelength": "Cu"}, {"wmin": 12.0})
    b = quick_refine._observed_peaks(task_dir, pattern, {"wavelength": "Cu"}, {"wmin": 12.0})
    c = quick_refine._observed_peaks(task_dir, pattern, {"wavelength": "Cu"}, {"wmin": 13.0})
    assert len(peak_calls) == 2 and a.equals(b) and not c.empty


# --------------------------------------------------------------------------- environment / BGMN
def test_ensure_bgmn_env_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("EFLECH", raising=False)
    folder = tmp_path / "BGMNwin"
    ensure_bgmn_env(folder)
    first = os.environ["PATH"]
    ensure_bgmn_env(folder)
    ensure_bgmn_env(folder)
    assert os.environ["PATH"] == first
    assert os.environ["PATH"].split(os.pathsep).count(folder.as_posix()) == 1
    assert os.environ["EFLECH"] == folder.as_posix()


def test_cancellable_bgmn_run_stops_within_a_second(monkeypatch, tmp_path):
    killed = threading.Event()

    class StubProcess:
        pid = 4242
        returncode = None

        def poll(self):
            return 1 if killed.is_set() else None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            killed.set()
            self.returncode = 1

    monkeypatch.setattr(bgmn_worker.subprocess, "Popen", lambda *a, **k: StubProcess())
    monkeypatch.setattr(bgmn_worker, "_kill_process_tree", lambda proc: proc.kill())
    worker = object.__new__(BGMNWorker)
    worker.bgmn_path = tmp_path / "bgmn"
    control = tmp_path / "x.sav"
    control.write_text("")
    event = threading.Event()
    threading.Timer(0.3, event.set).start()
    started = time.monotonic()
    with pytest.raises(RefinementCancelled):
        worker.run_refinement_cmd(control, cancel_event=event)
    assert time.monotonic() - started < 1.0
    assert killed.is_set()


def test_cancellable_bgmn_run_reports_failures_with_the_log(monkeypatch, tmp_path):
    class Failing:
        pid = 1
        returncode = 3

        def poll(self):
            return 3

        def wait(self, timeout=None):
            return 3

    def popen(cmd, cwd=None, stdout=None, stderr=None):
        stdout.write(b"BGMN says no")
        return Failing()

    monkeypatch.setattr(bgmn_worker.subprocess, "Popen", popen)
    worker = object.__new__(BGMNWorker)
    worker.bgmn_path = tmp_path / "bgmn"
    control = tmp_path / "x.sav"
    control.write_text("")
    with pytest.raises(RuntimeError, match="exit code is 3") as exc:
        worker.run_refinement_cmd(control, cancel_event=threading.Event())
    assert "BGMN says no" in str(exc.value)


def test_default_bgmn_path_still_uses_subprocess_run(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(bgmn_worker, "run", fake_run)
    monkeypatch.setattr(
        bgmn_worker.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("Popen must not be used")),
    )
    worker = object.__new__(BGMNWorker)
    worker.bgmn_path = tmp_path / "bgmn"
    control = tmp_path / "x.sav"
    control.write_text("")
    worker.run_refinement_cmd(control)
    assert seen["timeout"] == bgmn_worker.DEFAULT_BGMN_TIMEOUT_S and seen["capture_output"] is True


# --------------------------------------------------------------------------- queue position
def test_queue_reports_ahead_and_one_based_position(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(delay=0.6))

    async def main():
        first = await _submit(_all_inherit())
        second = await _submit(_all_inherit())
        third = await _submit(_all_inherit())
        snapshot = await quick_refine.get_quick_job(third["job_id"])
        for job_id in (first["job_id"], second["job_id"], third["job_id"]):
            await quick_refine.cancel_quick_job(job_id)
        return first, second, third, snapshot

    first, second, third, snapshot = _run(main())
    # ``position`` stays 1-based for compatibility; ``ahead`` counts the jobs in front.
    assert first["ahead"] == 0 and first["position"] in (0, 1)  # 0 once it is already running
    assert (second["position"], second["ahead"]) == (2, 1)
    assert (third["position"], third["ahead"]) == (3, 2)
    assert snapshot["status"] == "queued" and snapshot["ahead"] == snapshot["position"] - 1


def test_running_and_finished_jobs_have_zero_ahead(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())

    async def main():
        started = await _submit(_all_inherit())
        return await _wait(started["job_id"])

    final = _run(main())
    assert final["status"] == "done" and final["position"] == 0 and final["ahead"] == 0


# --------------------------------------------------------------------------- document cache race
@pytest.mark.parametrize("warm", [True, False])
def test_eviction_cannot_fail_a_queued_job(monkeypatch, env, warm):
    monkeypatch.setattr(quick_refine, "_DOCUMENTS", quick_refine._DocumentCache(size=1))
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(delay=0.4))

    async def main():
        if warm:
            await quick_refine.load_document(TASK_UUID)  # cached: the POST keeps the decoded doc
        first = await _submit(_all_inherit())
        second = await _submit(_all_inherit())
        quick_refine._DOCUMENTS.clear()  # evicted while both jobs are still waiting
        quick_refine._DOCUMENTS.discard(TASK_UUID)
        return await _wait(first["job_id"]), await _wait(second["job_id"])

    first, second = _run(main())
    assert first["status"] == "done", first
    assert second["status"] == "done", second
    assert all(job.document is None and job.raw is None for job in quick_refine._JOBS.values())


def test_document_cache_get_uses_the_ready_document_after_eviction():
    cache = quick_refine._DocumentCache(size=1)
    sentinel = object()
    assert cache.get("k", None, sentinel) is sentinel
    cache.discard("k")
    with pytest.raises(RuntimeError):
        cache.get("k", None)


# --------------------------------------------------------------------------- task deletion
def test_deleting_a_task_cleans_quick_refine_state(monkeypatch, env):
    from dara.server import api_router

    monkeypatch.setattr(api_router, "get_worker_store", env["workers"])
    monkeypatch.setattr(api_router, "get_result_store", env["results"])
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(delay=0.5))
    outsider = env["cache"] / "00000000-0000-0000-0000-000000000001"
    outsider.mkdir(parents=True)

    async def main():
        done = await _submit(_all_inherit())
        await _wait(done["job_id"])
        running = await _submit(_all_inherit())
        queued = await _submit(_all_inherit())
        await _wait(running["job_id"], states=("running",))
        assert quick_refine.task_cache(TASK_UUID).is_dir()
        assert env["versions"].query({"uuid": TASK_UUID})
        reply = await api_router.delete_result(7)
        return reply, await _wait(running["job_id"]), await _wait(queued["job_id"])

    reply, running, queued = _run(main())
    assert reply["message"] == "deleted"
    assert running["status"] == "cancelled" and queued["status"] == "cancelled"
    assert env["versions"].query({"uuid": TASK_UUID}) == []  # no orphan versions (or tombstones)
    assert env["workers"].docs == [] and env["results"].docs == []
    time.sleep(0.2)
    assert not quick_refine.task_cache(TASK_UUID).exists()  # nothing recreated by late jobs
    assert not quick_refine._DOCUMENTS.has(TASK_UUID)
    assert outsider.is_dir()  # other tasks' caches are untouched
    assert not any(job.status in ("queued", "running") for job in quick_refine._JOBS.values())


def test_discard_task_rejects_non_uuid_ids(env):
    with pytest.raises(ValueError):
        quick_refine.discard_task("../../etc")


# --------------------------------------------------------------------------- ETA vs background degree
def _eta_store(env, *entries, threads=None):
    threads = refine.single_refinement_threads() if threads is None else threads
    for number, entry in enumerate(entries, start=2):
        bgmn_s, degree, phases, mode = (*entry, "full")[:4]
        env["versions"].update([{
            "uuid": TASK_UUID, "index": number, "version": number, "deleted": False,
            "bgmn_s": bgmn_s, "phases": phases, "refine_mode": mode, "n_threads": threads,
            "refinement_params": {} if degree is None else {"background_degree": degree},
        }])


def test_eta_ignores_versions_refined_with_another_background_degree(env):
    _eta_store(env, (30.0, None, ["a", "b"]), (40.0, None, ["c", "d"]), (90.0, 12, ["a", "b"]),
               (80.0, 12, ["a", "b"]))
    threads = refine.single_refinement_threads()
    assert quick_refine._eta(TASK_UUID, 2, None, n_threads=threads) == (35.0, 2)
    assert quick_refine._eta(TASK_UUID, 2, 12, n_threads=threads) == (85.0, 2)
    assert quick_refine._eta(TASK_UUID, 2, 8, n_threads=threads) == (None, 0)  # nothing comparable
    assert quick_refine._eta(TASK_UUID, 2) == (60.0, 4)  # without a degree: every 2-phase full run


def test_eta_needs_two_runs_of_the_same_kind(env):
    _eta_store(env, (30.0, None, ["a", "b"]), (145.0, None, ["a", "b", "c"]),
               (12.0, None, ["a", "b", "c"], "fast"), (14.0, None, ["a", "b", "c"], "fast"))
    threads = refine.single_refinement_threads()
    # one comparable run is not enough, and other phase counts or modes never stand in
    assert quick_refine._eta(TASK_UUID, 2, None, n_threads=threads) == (None, 0)
    assert quick_refine._eta(TASK_UUID, 3, None, n_threads=threads) == (None, 0)
    assert quick_refine._eta(TASK_UUID, 3, None, refine_mode="fast", n_threads=threads) == (13.0, 2)
    # runs with another BGMN thread count are not comparable either
    assert quick_refine._eta(TASK_UUID, 3, None, refine_mode="fast", n_threads=threads + 1) == (None, 0)
    assert quick_refine._eta(TASK_UUID, None, None) == (None, 0)  # unknown resulting phase count


def test_effective_degree_follows_the_request_and_the_base(env):
    record = env["record"]
    auto = QuickRefineRequest(background=QuickBackground(degree=None, excluded_ranges=[[10.0, 12.0]]))
    explicit = QuickRefineRequest(background=QuickBackground(degree=9))
    untouched = QuickRefineRequest(background=QuickBackground(excluded_ranges=[[10.0, 12.0]]))
    plain = QuickRefineRequest(controls=RefinementControls())
    base = {"refinement_params": {"background_degree": 12}}
    assert quick_refine.effective_degree(auto, base, record) is None  # explicit Auto resets
    assert quick_refine.effective_degree(explicit, base, record) == 9
    assert quick_refine.effective_degree(untouched, base, record) == 12  # inherited
    assert quick_refine.effective_degree(plain, base, record) == 12
    assert quick_refine.effective_degree(plain, None, record) is None


def test_post_eta_comes_from_comparable_versions_only(monkeypatch, env):
    env["workers"].docs[0]["summary"] = {"final_phases": ["a", "b"]}  # the best fit has 2 phases
    _eta_store(env, (40.0, 12, ["a", "b"]), (50.0, 12, ["c", "d"]))
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())

    async def main():
        other = await _submit(QuickRefineRequest(background=QuickBackground(degree=5)))
        await _wait(other["job_id"])
        same = await _submit(QuickRefineRequest(background=QuickBackground(degree=12)))
        await _wait(same["job_id"])
        return other, same

    other, same = _run(main())
    assert (other["eta_s"], other["eta_n"]) == (None, 0)  # no earlier degree-5 run
    assert (same["eta_s"], same["eta_n"]) == (45.0, 2)


def test_deleting_versions_of_a_task_without_versions_keeps_the_real_store_usable(monkeypatch, tmp_path):
    # montydb: ``remove_docs`` on a never-written collection leaves a table-less file that makes
    # every later read/write fail, so the delete must not call it when there is nothing to remove.
    monkeypatch.setenv("DARA_SERVER_MONTYDB_PATH", str(tmp_path / "montydb"))
    task_uuid = str(uuid4())
    versions.delete_task_versions(task_uuid)
    assert versions.list_versions(task_uuid) == []
    assert versions.next_version_number(task_uuid) == 2
    number = versions.save_version(task_uuid, 7, {"label": "x"})
    assert [r["index"] for r in versions.list_versions(task_uuid)] == [number]
    versions.delete_task_versions(str(uuid4()))  # another task: nothing removed
    assert len(versions.list_versions(task_uuid)) == 1
    versions.delete_task_versions(task_uuid)
    assert versions.list_versions(task_uuid, include_deleted=True) == []


def test_replace_retries_windows_lock_and_keeps_existing(tmp_path, monkeypatch):
    from dara.server import quick_refine

    target = tmp_path / "pattern.xy"
    target.write_text("cached")
    source = tmp_path / "new.tmp"
    source.write_text("cached")
    calls = []

    def locked(src, dst):
        calls.append(1)
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(quick_refine.os, "replace", locked)
    monkeypatch.setattr(quick_refine.time, "sleep", lambda s: None)
    quick_refine._replace(source, target)
    assert len(calls) == 6
    assert target.read_text() == "cached" and not source.exists()

    missing = tmp_path / "missing.xy"
    source.write_text("x")
    import pytest

    with pytest.raises(PermissionError):
        quick_refine._replace(source, missing)


def test_queued_job_reports_what_it_waits_for():
    ahead = quick_refine._new_job(1, TASK_UUID, "Baseline (current code)")
    mine = quick_refine._new_job(1, TASK_UUID, "Added Bi4O7 (peak search)")
    try:
        quick_refine._update(ahead, status="running", stage="BGMN refinement")
        snap = mine.snapshot(quick_refine.self_position(mine))
        assert snap["ahead"] == 1
        assert snap["ahead_label"] == "Baseline (current code)"
        assert snap["ahead_stage"] == "BGMN refinement"
        assert snap["ahead_elapsed_s"] >= 0
        assert "ahead_label" not in ahead.snapshot(0)  # a running job waits for nothing
    finally:
        for job in (ahead, mine):
            quick_refine._update(job, status="cancelled")
            job.discarded = True


STR_TEXT = (
    "PHASE=Base1 // generated\n"
    "PARAM=A=0.53900_0.52822^0.54978 PARAM=C=1.34800_1.32104^1.37496 //\n"
    "RP=4 PARAM=k1=0_0^1 PARAM=k2=0_0^0.001 PARAM=B1=0_0^0.05 GEWICHT=SPHAR4 //\n"
)
LST_TEXT = (
    "EPS2=-0.001234+-0.000010\n"
    "Local parameters and GOALs for phase Base1\n"
    "A=0.540030+-0.000015\nC=1.3504+-0.0002\nk1=1.00000\nk2=0\nB1=0.0123+-0.0004\nGEWICHT=0.5+-0.01\n"
    "Atomic positions for phase Base1\n"
)


def test_fix_base_phases_fixes_only_refined_values_of_base_phases(tmp_path):
    base, new = tmp_path / "Base1.str", tmp_path / "New.str"
    base.write_text(STR_TEXT)
    new.write_text(STR_TEXT.replace("Base1", "New"))
    paths, fixed = quick_refine.fix_base_phases(tmp_path / "run", [base, new], ["Base1"], LST_TEXT)
    assert fixed == ["Base1"]
    text = paths[0].read_text()
    assert "A=0.540030 " in text and "C=1.3504 " in text and "k1=1.00000" in text and "B1=0.0123" in text
    assert "PARAM=" not in text.split("GEWICHT")[0]  # lattice and widths are constants now
    assert "GEWICHT=SPHAR4" in text  # scale and preferred orientation are still refined
    assert paths[1].read_text() == new.read_text()  # the added phase stays free
    assert base.read_text() == STR_TEXT  # the cached .str is not modified
    assert quick_refine.base_eps2(LST_TEXT) == pytest.approx(-0.001234)
    # A phase missing from the .lst stays free.
    _, none = quick_refine.fix_base_phases(tmp_path / "run2", [base], ["Base1"], "EPS2=0\n")
    assert none == []


def test_add_phase_is_a_fast_quick_check_and_refine_all_makes_the_full_fit(monkeypatch, env):
    captured: list[dict] = []
    texts: list[list[str]] = []
    fake = _fake_refinement(captured)

    def reading_fake(**kwargs):  # the run folder is removed afterwards: read the .str files now
        texts.append([Path(p).read_text() for p in kwargs["phases"]])
        return fake(**kwargs)

    monkeypatch.setattr(quick_refine, "do_refinement", reading_fake)
    references = env["references"]
    base_stem = references[0].filename

    def fake_base(document, selection, base_version):
        return type("R", (), {"lst_data": type("L", (), {"raw_lst": "EPS2=-0.001234\n"})()})()

    monkeypatch.setattr(quick_refine, "base_refinement", fake_base)
    monkeypatch.setattr(quick_refine, "_lst_section", lambda raw, phase: {"A": "0.5501"})

    async def main():
        removed = await _wait((await _submit(QuickRefineRequest(remove=[references[1].filename], origin="remove")))["job_id"])
        added = await _wait((await _submit(QuickRefineRequest(
            base="version:2", phases=[QuickPhase(ref=references[1].filename)], origin="add_hint")))["job_id"])
        full = await _wait((await _submit(QuickRefineRequest(base="version:3", origin="refine_all")))["job_id"])
        return removed, added, full

    removed, added, full = _run(main())
    assert [job["status"] for job in (removed, added, full)] == ["done"] * 3
    _, fast_run, full_run = captured
    assert fast_run["refinement_params"]["itmax"] == quick_refine.FAST_ADD_ITMAX == 15
    assert fast_run["refinement_params"]["eps2"] == pytest.approx(-0.001234)
    assert [Path(p).stem for p in fast_run["phases"]] == [base_stem, references[1].filename]
    base_text, new_text = texts[1]
    assert "A=0.5501" in base_text and "PARAM=A=" not in base_text
    # The added phase keeps its database lattice (ADDED_PHASE_LATTICE_RANGE); its scale is refined.
    assert "PARAM=A=" not in new_text and "GEWICHT" in new_text
    assert stored_narrow(env) == {3: [references[1].filename], 4: [references[1].filename]}
    assert full_run["refinement_params"]["itmax"] == quick_refine.QUICK_REFINE_ITMAX
    assert "eps2" not in full_run["refinement_params"] or isinstance(full_run["refinement_params"]["eps2"], str)
    assert [Path(p).stem for p in full_run["phases"]] == [base_stem, references[1].filename]
    stored = {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}
    assert stored[2]["refine_mode"] == "full" and stored[2]["fixed_phases"] == []
    assert stored[3]["refine_mode"] == "fast" and stored[3]["fixed_phases"] == [base_stem]
    assert stored[3]["iteration_cap"] == 15
    assert stored[4]["refine_mode"] == "full" and stored[4]["origin"]["kind"] == "refine_all"
    assert stored[4]["label"] == "All phases refined"
    assert "eps2" not in (stored[3]["refinement_params"] or {})  # the fixed value is not inherited
    # The full fit supersedes the quick check it was made from: v3 is deleted, v4 says so.
    assert stored[3]["deleted"] is True and stored[4]["replaces"] == 3
    assert (full["replaces"], full["reused"]) == (3, False)
    assert stored[4]["n_threads"] == refine.single_refinement_threads()
    # Nothing points at the deleted quick check: v4 is recorded against v3's own base (v2) and
    # keeps what v3 added.
    assert stored[3]["origin"]["base"] == "version:2"
    assert stored[4]["origin"]["base"] == "version:2"
    assert stored[4]["origin"]["added"] == [references[1].filename]
    assert stored[4]["origin"]["removed"] == []


def stored_narrow(env) -> dict:
    return {r["version"]: r.get("narrow_lattice_stems") for r in env["versions"].query({"uuid": TASK_UUID})
            if r.get("narrow_lattice_stems")}


def test_refine_all_only_starts_from_a_quick_check(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())
    references = env["references"]

    async def main():
        await _wait((await _submit(QuickRefineRequest(remove=[references[1].filename], origin="remove")))["job_id"])
        with pytest.raises(HTTPException) as refused:
            await _submit(QuickRefineRequest(base="version:2", origin="refine_all"))
        assert refused.value.status_code == 409 and "quick-check" in refused.value.detail
        with pytest.raises(HTTPException) as refused:
            await _submit(QuickRefineRequest(base="best", origin="refine_all"))
        assert refused.value.status_code == 409

    _run(main())


def test_identical_re_refinement_reuses_the_stored_version(monkeypatch, env):
    captured: list[dict] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(captured))
    references = env["references"]

    def remove():
        return QuickRefineRequest(remove=[references[1].filename], origin="remove")

    async def main():
        first = await _wait((await _submit(remove()))["job_id"])
        again = await _wait((await _submit(remove()))["job_id"])
        other = await _wait((await _submit(QuickRefineRequest(
            remove=[references[1].filename], origin="remove",
            background=QuickBackground(degree=6))))["job_id"])
        return first, again, other

    first, again, other = _run(main())
    assert (first["version"], first["reused"]) == (2, False)
    assert (again["status"], again["version"], again["reused"]) == ("done", 2, True)
    assert (other["version"], other["reused"]) == (3, False)  # another background is another fit
    assert len(captured) == 2  # BGMN did not run for the repeat
    stored = {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}
    assert set(stored) == {2, 3} and stored[2]["fit_key"] != stored[3]["fit_key"]


def test_reuse_ignores_deleted_versions_and_records_without_a_key(monkeypatch, env):
    captured: list[dict] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(captured))
    references = env["references"]

    def remove():
        return QuickRefineRequest(remove=[references[1].filename], origin="remove")

    async def main():
        await _wait((await _submit(remove()))["job_id"])
        versions.tombstone_version(TASK_UUID, 2)
        return await _wait((await _submit(remove()))["job_id"])

    again = _run(main())
    assert (again["version"], again["reused"]) == (3, False) and len(captured) == 2
    assert quick_refine._stored_versions_by_key(TASK_UUID) == {
        env["versions"].query({"uuid": TASK_UUID, "index": 3})[0]["fit_key"]: 3
    }


@pytest.mark.parametrize("worker_sees_it", [False, True])
def test_reuse_target_deleted_while_queued_runs_bgmn(monkeypatch, env, worker_sees_it):
    """The matched version is deleted after submission: refine and store instead of failing."""
    captured: list[dict] = []
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement(captured))
    references = env["references"]
    real_by_key = quick_refine._stored_versions_by_key

    def remove():
        return QuickRefineRequest(remove=[references[1].filename], origin="remove")

    async def main():
        await _wait((await _submit(remove()))["job_id"])
        stale = real_by_key(TASK_UUID)  # what the submission saw before the deletion
        versions.tombstone_version(TASK_UUID, 2)
        calls = iter([stale])
        monkeypatch.setattr(quick_refine, "_stored_versions_by_key", lambda uuid: next(calls, None) or real_by_key(uuid))
        if worker_sees_it:  # the worker's check passes; the version goes before the finish step
            monkeypatch.setattr(quick_refine, "_still_stored_check", lambda loop, uuid: lambda number: True)
        return await _wait((await _submit(remove()))["job_id"])

    again = _run(main())
    assert (again["status"], again["version"], again["reused"]) == ("done", 3, False)
    assert len(captured) == 2


def test_fit_key_changes_with_the_refinement_code(monkeypatch):
    arguments = dict(
        refine_mode="full", base="best", phase_params={}, overrides={}, effective={"A": {}},
        refinement_params={}, uploaded=[],
    )
    before = quick_refine.fit_key(["A"], {"A": "data_A"}, **arguments)
    assert quick_refine.fit_key(["A"], {"A": "data_A"}, **arguments) == before
    monkeypatch.setattr(quick_refine, "REFINEMENT_CODE_DATE", "2099-01-01T00:00:00+00:00")
    assert quick_refine.fit_key(["A"], {"A": "data_A"}, **arguments) != before


def test_child_versions_record_the_inherited_controls(monkeypatch, env):
    monkeypatch.setattr(quick_refine, "do_refinement", _fake_refinement())

    async def main():
        await _wait((await _submit(QuickRefineRequest(controls=RefinementControls(orientation="isotropic"))))["job_id"])
        await _wait((await _submit(QuickRefineRequest(
            base="version:2", background=QuickBackground(degree=7))))["job_id"])

    _run(main())
    stored = {r["version"]: r for r in env["versions"].query({"uuid": TASK_UUID})}
    assert stored[3]["controls"] == stored[2]["controls"] and stored[3]["controls"]["orientation"] == "isotropic"


def test_two_phases_with_one_bgmn_name_are_refused(monkeypatch, env):
    first = env["references"][0].filename
    twin = first + "_twin"
    base_version = {"phases": [first], "cifs": [{"filename": twin, "cif_text": str(env["references"][0])}]}
    request = QuickRefineRequest(base="version:2", phases=[QuickPhase(ref=twin)], origin="manual")
    original = quick_refine.process_phase_name
    monkeypatch.setattr(
        quick_refine, "process_phase_name", lambda stem: "SAME" if stem in (first, twin) else original(stem)
    )
    with pytest.raises(quick_refine.QuickRefineError, match="share the BGMN name SAME"):
        quick_refine.plan_phases(env["document"], ("version", 2), base_version, request, {})


def test_refine_all_keeps_the_phase_set():
    QuickRefineRequest(base="version:2", origin="refine_all")
    with pytest.raises(ValidationError):
        QuickRefineRequest(base="version:2", origin="refine_all", phases=[QuickPhase(ref="x")])


def test_quick_check_versions_cannot_be_adopted_or_reported(monkeypatch):
    from dara.server import review_router

    records = {2: {"refine_mode": "fast"}, 3: {"refine_mode": "full"}, 4: {}}
    monkeypatch.setattr(versions, "load_version", lambda uuid, n, light=False: records[n])
    task = {"uuid": TASK_UUID}
    with pytest.raises(HTTPException) as refused:
        review_router._refuse_quick_check(task, "version:2")
    assert refused.value.status_code == 409 and "Refine all phases" in refused.value.detail
    for selection in ("version:3", "version:4", "best", 0):
        review_router._refuse_quick_check(task, selection)  # full fits, older versions and v1 pass


def test_added_phases_keep_a_narrow_lattice_window():
    from dara.refinement_controls import RefinementControls

    plan = quick_refine.PhasePlan(["A", "B", "C"], {}, [], ["C"], [])
    add = quick_refine.QuickRefineRequest(phases=[{"ref": "C"}], origin="add_hint")
    assert quick_refine.narrow_lattice_stems(add, plan, None) == ["C"]
    # Refine all and later edits inherit it from the base version; removed phases drop out.
    refine_all = quick_refine.QuickRefineRequest(origin="refine_all")
    base = {"narrow_lattice_stems": ["C", "gone"]}
    assert quick_refine.narrow_lattice_stems(refine_all, plan, base) == ["C"]
    # A manual edit adds nothing new; an explicit lattice control wins over the narrow window.
    manual = quick_refine.QuickRefineRequest(phases=[{"ref": "C"}], origin="manual")
    assert quick_refine.narrow_lattice_stems(manual, plan, None) == []
    explicit = quick_refine.QuickRefineRequest(
        phases=[{"ref": "C"}], origin="add_hint", controls=RefinementControls(lattice="refine")
    )
    assert quick_refine.narrow_lattice_stems(explicit, plan, base) == []
