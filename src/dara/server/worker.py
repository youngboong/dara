import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from traceback import format_exc

import ray
from jobflow.managers.local import run_locally
from monty.serialization import MontyDecoder

from dara.server.utils import get_job_store, get_result_store, get_worker_store

logger = logging.getLogger("dara.server.worker")


_SERVER = None  # (pid, create_time) of the server process that started this worker


def _watch_server() -> None:
    """Remember the parent (server) process so the worker can stop when it disappears."""
    global _SERVER  # noqa: PLW0603
    import psutil

    try:
        parent = psutil.Process(os.getppid())
        _SERVER = (parent.pid, parent.create_time())
    except (psutil.Error, OSError):
        _SERVER = None


def _server_gone() -> bool:
    """True when the server that started this worker has exited (killed or crashed).

    An orphaned worker would keep taking jobs from the same database as the worker of the
    next server start; the creation time guards against a reused process id.
    """
    if _SERVER is None:
        return False
    import psutil

    pid, created = _SERVER
    try:
        return psutil.Process(pid).create_time() != created
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        return False


def _should_stop(stop_event) -> bool:
    return (stop_event is not None and stop_event.is_set()) or _server_gone()


def worker_process(stop_event=None, settings=None):
    """Start the Ray worker process."""
    logger.info("Starting worker process for job execution...")
    if settings:
        for key, value in settings.items():
            if value is not None:
                os.environ[f"DARA_SERVER_{key.upper()}"] = str(value)
    if stop_event is not None:
        _watch_server()
    mark_running_jobs_as_fizzled()

    while not _should_stop(stop_event):
        for job_uuid in get_all_pending_jobs():
            logger.debug(f"Job {job_uuid} has started...")
            if _should_stop(stop_event):
                break
            run_job(job_uuid, stop_event)
        if stop_event is not None:
            stop_event.wait(3)
        else:
            time.sleep(3)


def _terminate_tree(process):
    """Terminate only descendants of this task, never unrelated server jobs."""
    import psutil

    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        # Stop spawning new children before terminating the snapshot.
        parent.suspend()
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
        psutil.wait_procs(children + [parent], timeout=5)
    except psutil.NoSuchProcess:
        pass
    process.wait(timeout=10)


JOB_LOG_NAME = "job.log"


def _exit_message(directory, returncode) -> str:
    """Error text for a job process that ended without a result: exit code plus its last output."""
    text = "Job process exited without a completed result. Completed checkpoints are retained."
    if returncode is not None:
        text += f" Exit code: {returncode}."
    try:
        lines = (directory / JOB_LOG_NAME).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    tail = [line for line in lines if line.strip()][-40:]
    if tail:
        text += "\nLast output of the job process:\n" + "\n".join(tail)
    return text


def run_job(uuid, stop_event=None):
    from dara.server.task_control_router import task_directory

    with get_worker_store() as store:
        record = store.query_one({"uuid": uuid})
        if not record or record["status"] != "READY":
            return
        record.update(status="RUNNING", start_time=datetime.now(timezone.utc))
        store.update(record)
    directory = task_directory(uuid)
    directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["DARA_TASK_PROGRESS_DIR"] = str(directory)
    process = None
    # Output of the job process goes to the task directory, so a crash without a Python
    # traceback (native crash, killed process) still leaves its reason behind.
    log = open(directory / JOB_LOG_NAME, "ab")  # noqa: SIM115 - closed in finally
    try:
        process = subprocess.Popen([sys.executable, "-m", "dara.server.job_runner", uuid], env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            with get_worker_store() as store:
                record = store.query_one({"uuid": uuid})
            if record["status"] in {"CANCELLING", "CANCELLED"} or (directory / "cancel.requested").exists() or _should_stop(stop_event):
                _terminate_tree(process)
                with get_worker_store() as store:
                    record = store.query_one({"uuid": uuid})
                    if record["status"] != "COMPLETED":
                        record.update(status="CANCELLED", end_time=datetime.now(timezone.utc))
                        store.update(record)
                return
            time.sleep(0.5)
    except Exception:
        if process is not None and process.poll() is None:
            _terminate_tree(process)
        logger.exception("Job supervisor failed")
    finally:
        log.close()
        with get_worker_store() as store:
            record = store.query_one({"uuid": uuid})
            if record and record["status"] in {"RUNNING", "CANCELLING"}:
                record.update(status="FIZZLED", end_time=datetime.now(timezone.utc),
                              error=_exit_message(directory, process.poll() if process is not None else None))
                store.update(record)


def execute_job(uuid):
    """Run a job remotely by its UUID."""
    from dara.task_progress import report_progress

    report_progress("Starting isolated refinement worker")
    with get_worker_store() as worker_store:
        job = worker_store.query_one(criteria={"uuid": uuid})
        try:
            # Own a local runtime: cancellation must not stop a separate cluster.
            if not ray.is_initialized():
                ray.init(address="local", runtime_env={"working_dir": None})
            with TemporaryDirectory() as tmp_dir:
                result = run_locally(
                    MontyDecoder().process_decoded(job["job"]),
                    raise_immediately=True,
                    store=get_job_store(get_result_store()),
                    root_dir=tmp_dir,
                )
            job = worker_store.query_one(criteria={"uuid": uuid})
            job["status"] = "COMPLETED"
            job["end_time"] = datetime.now(tz=timezone.utc)
            job["result"] = result
            try:
                from dara.server.task_summary import runtime_seconds, summarize_stored_result

                job["summary"] = summarize_stored_result(
                    uuid, runtime_seconds(job.get("start_time"), job["end_time"])
                )
            except Exception:
                logger.exception("Could not build the result summary for job %s", uuid)
            worker_store.update(job)
            return result
        except Exception:
            job = worker_store.query_one(criteria={"uuid": uuid})
            job["status"] = "FIZZLED"
            job["error"] = format_exc()
            job["end_time"] = datetime.now(tz=timezone.utc)
            worker_store.update(job)
        except KeyboardInterrupt:
            job["status"] = "FIZZLED"
            job["error"] = format_exc()
            worker_store.update(job)
            raise
        finally:
            ray.shutdown()


def add_job_to_queue(job, user):
    """Add a job to the queue for remote execution."""
    with get_worker_store() as worker_store:
        newest = next(
            iter(worker_store.query(sort={"index": -1}, limit=1)),
            None,
        )
        next_index = (newest["index"] if newest else 0) + 1
        worker_store.update(
            {
                "uuid": job.uuid,
                "job": job.as_dict(),
                "status": "READY",
                "submitted_time": datetime.now(tz=timezone.utc),
                "index": next_index,
                "user": user,
            }
        )
    return next_index  # task IDs remain unique even after older tasks are deleted


def get_all_pending_jobs(sort_by_submitted_time=False):
    """Add all pending jobs to the queue for remote execution."""
    with get_worker_store() as worker_store:
        jobs = worker_store.query(
            criteria={"status": "READY"},
            sort={"submitted_time": 1} if sort_by_submitted_time else None,
        )
        return [job["uuid"] for job in jobs]


def mark_running_jobs_as_fizzled():
    """Mark all running jobs as fizzled."""
    with get_worker_store() as worker_store:
        running_jobs = list(worker_store.query(criteria={"status": "RUNNING"})) + list(worker_store.query(criteria={"status": "CANCELLING"}))
        for job in running_jobs:
            job["status"] = "FIZZLED"
            job["end_time"] = datetime.now(tz=timezone.utc)
            job["error"] = "Unexpected shutdown of the worker process."
            worker_store.update(job)
            logger.warning(
                f"Job {job['uuid']} marked as fizzled due to previous shutdown."
            )
