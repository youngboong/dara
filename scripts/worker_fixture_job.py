"""Small synthetic job for the isolated-worker smoke test (no diffraction/DB requests)."""
import time
from jobflow import job
from dara.task_progress import report_progress


@job
def bounded_job():
    report_progress("Synthetic worker smoke test", 0, 1)
    time.sleep(0.1)
    return {"worker_smoke": "completed"}
