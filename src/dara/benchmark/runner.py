"""Run DARA on the benchmark samples and score the results.

A run writes one folder::

    benchmarks/<run_id>/
        run.json          settings, versions, timing
        samples/<id>/     document.json.gz, summary.json, score.json, error.txt
        progress.log      one line per finished sample
        scores.json       all per-sample scores and the aggregate
        report.md         human-readable report

The pipeline is executed in-process with ``jobflow.run_locally`` so that no
server is needed; it is the same job the web app builds
(``api_router.build_submission_job``) and is summarised by the same
``api_router.summarize_document`` that serves ``GET /api/task/{id}``.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import sys
import tempfile
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dara.benchmark.ground_truth import GTSample, GTSet, load_ground_truth, repo_root
from dara.benchmark.report import render_markdown
from dara.benchmark.scoring import aggregate, score_sample

_API_ERROR = (
    "The benchmark runner needs api_router.build_submission_job / summarize_document (W2 refactor)."
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _json_default(value: Any):
    """JSON fallback for numpy scalars/arrays, datetimes, paths and sets."""
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, default=_json_default, ensure_ascii=False), encoding="utf-8")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_api():
    try:
        from dara.server.api_router import build_submission_job, summarize_document
    except ImportError as exc:  # pragma: no cover - exercised only without the refactor
        raise RuntimeError(_API_ERROR) from exc
    return build_submission_job, summarize_document


@contextlib.contextmanager
def _environment(changes: dict[str, str | None]):
    """Temporarily set (or remove, with ``None``) environment variables."""
    previous = {key: os.environ.get(key) for key in changes}
    try:
        for key, value in changes.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run_id(set_names: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{'+'.join(set_names)}"


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def default_execute(job, root_dir: Path):
    """Run a jobflow job locally and return its output document."""
    from jobflow import JobStore
    from jobflow.managers.local import run_locally
    from maggma.stores import MemoryStore
    from monty.json import MontyDecoder

    responses = run_locally(
        job,
        store=JobStore(MemoryStore()),
        root_dir=str(root_dir),
        raise_immediately=True,
        log=False,
    )
    output = responses[job.uuid][1].output
    if isinstance(output, dict):
        output = MontyDecoder().process_decoded(output)
    return output


def _ensure_ray() -> bool:
    """Start a local Ray runtime like the worker does; True when started here."""
    import ray

    if ray.is_initialized():
        return False
    ray.init(address="local", runtime_env={"working_dir": None})
    return True


def run_sample(
    gt_set: GTSet,
    sample: GTSample,
    sample_dir: Path,
    *,
    mode: str | None = None,
    use_mp: bool = True,
    keep_work: bool = False,
    execute_fn: Callable | None = None,
) -> dict:
    """Run and score one sample; always returns a score record (never raises)."""
    sample_dir = Path(sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)
    runner = execute_fn or default_execute
    started = _utc_now()
    document = None
    summary = None
    error = None
    try:
        build_submission_job, summarize_document = _load_api()
        from dara.xrd import load_pattern

        pattern_path = repo_root() / sample.pattern
        pattern = load_pattern(pattern_path)
        submission = sample.submission
        job = build_submission_job(
            pattern,
            Path(sample.pattern).name,
            analysis_mode=mode or submission.analysis_mode,
            precursor_formulas=list(submission.precursors),
            elements=list(submission.elements),
            temperature=submission.temperature_c if submission.temperature_c is not None else -1,
            atmosphere=submission.atmosphere,
            instrument_profile=submission.instrument_profile,
            wavelength=submission.wavelength,
            use_cod=True,
            use_mp=use_mp,
        )
        started = _utc_now()
        if keep_work:
            work_dir = sample_dir / "work"
            work_dir.mkdir(exist_ok=True)
            document = runner(job, work_dir)
        else:
            with tempfile.TemporaryDirectory() as temp:
                document = runner(job, Path(temp))
        finished = _utc_now()
        runtime_s = (finished - started).total_seconds()
        record = {
            "uuid": job.uuid, "index": None, "status": "COMPLETED", "job": job.as_dict(),
            "submitted_time": started, "start_time": started, "end_time": finished,
        }
        summary = summarize_document(record, document, persist_summary=False)
    except BaseException as exc:  # noqa: BLE001 - a failed sample must not stop the run
        if isinstance(exc, KeyboardInterrupt):
            raise
        error = f"{type(exc).__name__}: {exc}"
        (sample_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        runtime_s = (_utc_now() - started).total_seconds()

    if document is not None:
        try:
            from monty.serialization import dumpfn

            dumpfn(document, sample_dir / "document.json.gz")
        except Exception:  # noqa: BLE001
            (sample_dir / "document_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    if summary is not None:
        _dump_json(sample_dir / "summary.json", summary)

    score = score_sample(
        sample, summary, aliases=gt_set.aliases, runtime_s=runtime_s, error=error, set_name=gt_set.set
    )
    score["bgmn_version"] = _bgmn_version(document) if document is not None else None
    _dump_json(sample_dir / "score.json", score)
    return score


def _progress_line(score: dict) -> str:
    counts = (
        f"{score['tp']}/{score['fp']}/{score['fn']}" if score.get("tp") is not None else "-/-/-"
    )
    mae = score.get("wt_mae")
    runtime = score.get("runtime_s")
    return " | ".join([
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        score["id"],
        score["status"],
        f"{runtime:.1f}" if runtime is not None else "-",
        counts,
        f"{mae:.2f}" if mae is not None else "-",
    ])


def _bgmn_version(document) -> str | None:
    try:
        text = document.final_result.lst_data.raw_lst
        match = re.search(r"BGMN version\s+([\w.]+)", text)
        return match.group(1) if match else None
    except Exception:  # noqa: BLE001
        return None


def _dara_version() -> str | None:
    try:
        import dara

        return dara.__version__
    except Exception:  # noqa: BLE001
        return None


def run_benchmark(
    samples: list[tuple[GTSet, GTSample]],
    out_dir: Path,
    *,
    resume: bool = True,
    mode: str | None = None,
    use_mp: bool = True,
    mp_window: bool = True,
    cif_validation: bool = True,
    keep_work: bool = False,
    execute_fn: Callable | None = None,
    log: Callable[[str], Any] = print,
) -> dict:
    """Run all samples into ``out_dir`` and return ``{"scores", "aggregate", "run"}``."""
    out_dir = Path(out_dir)
    sample_root = out_dir / "samples"
    sample_root.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    settings = {
        "samples": [sample.id for _, sample in samples],
        "sets": sorted({gt_set.set for gt_set, _ in samples}),
        "mode": mode,
        "resume": resume,
        "use_mp": use_mp,
        "label": None if use_mp else "non-standard: COD only",
        "mp_lattice_window": mp_window,
        "cif_validation": cif_validation,
        "keep_work": keep_work,
    }
    run_info: dict[str, Any] = {
        "settings": settings,
        "dara_version": _dara_version(),
        "bgmn_version": None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "started": started.isoformat(timespec="seconds"),
        "ended": None,
    }
    _dump_json(out_dir / "run.json", run_info)

    env = {
        "DARA_MP_LATTICE_WINDOW": None if mp_window else "off",
        "DARA_CIF_VALIDATION": None if cif_validation else "off",
    }
    scores: list[dict] = []
    started_ray = False
    with _environment(env):
        try:
            if execute_fn is None:
                started_ray = _ensure_ray()
            total = len(samples)
            for number, (gt_set, sample) in enumerate(samples, start=1):
                sample_dir = sample_root / sample.id
                score_path = sample_dir / "score.json"
                if resume and score_path.is_file():
                    try:
                        scores.append(json.loads(score_path.read_text(encoding="utf-8")))
                        log(f"[{number}/{total}] {sample.id}: skipped (already scored)")
                        continue
                    except ValueError:
                        pass
                log(f"[{number}/{total}] {sample.id}: running")
                score = run_sample(
                    gt_set, sample, sample_dir, mode=mode, use_mp=use_mp,
                    keep_work=keep_work, execute_fn=execute_fn,
                )
                scores.append(score)
                with open(out_dir / "progress.log", "a", encoding="utf-8") as handle:
                    handle.write(_progress_line(score) + "\n")
                log(
                    f"[{number}/{total}] {sample.id}: {score['status']} "
                    f"tp/fp/fn={score.get('tp')}/{score.get('fp')}/{score.get('fn')}"
                )
        finally:
            if started_ray:
                import ray

                ray.shutdown()

    run_info["ended"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_info["bgmn_version"] = _first_bgmn_version(scores)
    result = finalize_run(out_dir, scores, run_info)
    return result


def _first_bgmn_version(scores: list[dict]) -> str | None:
    return next((s["bgmn_version"] for s in scores if s.get("bgmn_version")), None)


def finalize_run(out_dir: Path, scores: list[dict], run_info: dict) -> dict:
    """Write ``run.json``, ``scores.json`` and ``report.md`` for a set of scores."""
    out_dir = Path(out_dir)
    summary = aggregate(scores)
    _dump_json(out_dir / "run.json", run_info)
    _dump_json(out_dir / "scores.json", {"scores": scores, "aggregate": summary})
    (out_dir / "report.md").write_text(render_markdown(run_info, scores, summary), encoding="utf-8")
    return {"scores": scores, "aggregate": summary, "run": run_info}


# --------------------------------------------------------------------------- #
# re-scoring
# --------------------------------------------------------------------------- #
def _gt_index(gt_sets: list[GTSet]) -> dict[str, tuple[GTSet, GTSample]]:
    return {sample.id: (gt_set, sample) for gt_set in gt_sets for sample in gt_set.samples}


def score_from_run(run_dir: Path, *, gt_sets: list[GTSet] | None = None) -> dict:
    """Re-score the stored ``summary.json`` files of a run with the current ground truth."""
    run_dir = Path(run_dir)
    gt_sets = gt_sets or [load_ground_truth("pairwise"), load_ground_truth("precursor")]
    index = _gt_index(gt_sets)
    scores = []
    for sample_dir in sorted((run_dir / "samples").glob("*")):
        if sample_dir.name not in index:
            continue
        gt_set, sample = index[sample_dir.name]
        summary_path = sample_dir / "summary.json"
        old_score = {}
        if (sample_dir / "score.json").is_file():
            try:
                old_score = json.loads((sample_dir / "score.json").read_text(encoding="utf-8"))
            except ValueError:
                old_score = {}
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            scores.append(score_sample(
                sample, summary, aliases=gt_set.aliases,
                runtime_s=old_score.get("runtime_s"), set_name=gt_set.set,
            ))
        else:
            error_path = sample_dir / "error.txt"
            error = old_score.get("error") or (
                error_path.read_text(encoding="utf-8").strip().splitlines()[-1]
                if error_path.is_file() and error_path.read_text(encoding="utf-8").strip()
                else "No result"
            )
            scores.append(score_sample(
                sample, None, aliases=gt_set.aliases,
                runtime_s=old_score.get("runtime_s"), error=error, set_name=gt_set.set,
            ))
    run_info = {}
    if (run_dir / "run.json").is_file():
        try:
            run_info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        except ValueError:
            run_info = {}
    return {"scores": scores, "aggregate": aggregate(scores), "run": run_info}


def score_from_tasks(
    selector: str,
    *,
    montydb_path: str | None = None,
    gt_sets: list[GTSet] | None = None,
) -> dict:
    """Score completed server tasks selected by ids ("12,13") or ``regex:<pattern>`` on the job name."""
    if montydb_path:
        os.environ["DARA_SERVER_MONTYDB_PATH"] = str(montydb_path)
    from monty.json import MontyDecoder

    from dara.server.utils import get_result_store, get_worker_store

    _, summarize_document = _load_api()
    gt_sets = gt_sets or [load_ground_truth("pairwise"), load_ground_truth("precursor")]
    by_stem = {Path(sample.pattern).stem: (gt_set, sample) for gt_set in gt_sets for sample in gt_set.samples}

    selector = selector.strip()
    pattern = None
    wanted_ids: set[int] = set()
    if selector.lower().startswith("regex:"):
        pattern = re.compile(selector[6:])
    else:
        wanted_ids = {int(item) for item in selector.split(",") if item.strip()}

    scores = []
    with get_worker_store() as worker_store, get_result_store() as result_store:
        tasks = list(worker_store.query({"status": "COMPLETED"}))
        for task in sorted(tasks, key=lambda item: item.get("index") or 0):
            name = (task.get("job") or {}).get("name", "")
            if pattern is not None:
                if not pattern.search(name):
                    continue
            elif task.get("index") not in wanted_ids:
                continue
            match = by_stem.get(Path(name).stem)
            if match is None:
                continue
            gt_set, sample = match
            result = result_store.query_one({"uuid": task["uuid"]})
            try:
                document = MontyDecoder().process_decoded(result["output"])
                summary = summarize_document(task, document, persist_summary=False)
                runtime = summary.get("runtime")
                scores.append(score_sample(
                    sample, summary, aliases=gt_set.aliases, runtime_s=runtime, set_name=gt_set.set
                ))
            except Exception as exc:  # noqa: BLE001
                scores.append(score_sample(
                    sample, None, aliases=gt_set.aliases, error=f"{type(exc).__name__}: {exc}",
                    set_name=gt_set.set,
                ))
    return {
        "scores": scores,
        "aggregate": aggregate(scores),
        "run": {"settings": {"source": f"server tasks ({selector})"}},
    }
