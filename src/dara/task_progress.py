"""Local, per-job progress and completed-candidate checkpoints.

Disabled for ordinary Python use. Only the owning server job process writes;
Ray children must not overwrite the coordinator's progress.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from monty.json import MontyEncoder


def progress_directory():
    value = os.environ.get("DARA_TASK_PROGRESS_DIR")
    if not value or os.environ.get("DARA_TASK_OWNER_PID") != str(os.getpid()):
        return None
    return Path(value)


def report_progress(stage, completed=None, total=None):
    directory = progress_directory()
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    started = now
    try:
        # Keep the start time while the stage is unchanged (best effort).
        previous = json.loads((directory / "progress.json").read_text(encoding="utf-8"))
        if previous.get("stage") == stage and previous.get("stage_started_at"):
            started = previous["stage_started_at"]
    except (OSError, ValueError, AttributeError):
        pass
    data = {"stage": stage, "completed": completed, "total": total,
            "updated_at": now, "stage_started_at": started}
    temporary = directory / "progress.tmp"
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(directory / "progress.json")


def checkpoint_candidate(index, refinement, working_dir):
    """Publish only finished candidates, using an atomic archive rename."""
    directory = progress_directory()
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"candidate_{index}.tmp"
    with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
        archive.writestr("refinement.json", json.dumps(refinement, cls=MontyEncoder))
        archive.writestr("README.txt", "Completed refinement checkpoint, not a complete search result. Candidate numbers are execution order, not final Rwp ranking.\n")
        for path in Path(working_dir).iterdir():
            if path.is_file() and not path.is_symlink():
                archive.write(path, path.name)
    temporary.replace(directory / f"candidate_{index}.zip")
