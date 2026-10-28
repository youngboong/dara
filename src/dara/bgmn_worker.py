"""APIs for running BGMN executable from Python."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from subprocess import run

from dara.bgmn.download_bgmn import download_bgmn
from dara.utils import get_logger

logger = get_logger(__name__)

#: Hard limit (seconds) for one BGMN run unless the caller passes ``timeout``.
DEFAULT_BGMN_TIMEOUT_S = 1200


class RefinementCancelled(RuntimeError):
    """Raised when a cancellable BGMN run is stopped through its cancel event."""


def ensure_bgmn_env(folder: Path) -> None:
    """Idempotently expose the BGMN folder through ``EFLECH`` and ``PATH``.

    The first call behaves exactly like the former per-instance update; repeated
    calls (every worker instantiation) no longer grow ``PATH`` or rewrite it.
    """
    posix = Path(folder).as_posix()
    if os.environ.get("EFLECH") != posix:
        os.environ["EFLECH"] = posix
    current = os.environ.get("PATH", "")
    if posix not in current.split(os.pathsep):
        os.environ["PATH"] = current + os.pathsep + posix


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 - best effort, fall back to kill()
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


class BGMNWorker:
    """API for BGMN executable."""

    def __init__(self):
        self.bgmn_folder = (Path(__file__).parent / "bgmn" / "BGMNwin").absolute()

        self.bgmn_path = self.bgmn_folder / "bgmn"

        if not self.bgmn_path.exists() and not self.bgmn_path.with_suffix(".exe").exists():
            logger.warning("BGMN executable not found. Downloading BGMN.")
            download_bgmn()

        ensure_bgmn_env(self.bgmn_folder)

    def run_refinement_cmd(
        self,
        control_file: Path,
        show_progress: bool = False,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ):
        """
        Run refinement via BGMN executable.

        Args:
            control_file: the path to the control file (.sav)
            show_progress: whether to show the progress in the console
            timeout: seconds before the BGMN subprocess is killed; ``None`` keeps
                the legacy limit ``DEFAULT_BGMN_TIMEOUT_S``
            cancel_event: optional event; when set the BGMN process tree is killed and
                ``RefinementCancelled`` is raised. ``None`` keeps the original blocking path.
        """
        if cancel_event is not None:
            self._run_cancellable(control_file, timeout, cancel_event)
            return
        cp = run(
            [self.bgmn_path.as_posix(), control_file.absolute().as_posix()],
            cwd=control_file.parent.absolute().as_posix(),
            capture_output=not show_progress,
            check=False,
            timeout=DEFAULT_BGMN_TIMEOUT_S if timeout is None else float(timeout),
        )
        if cp.returncode:
            raise RuntimeError(
                f"Error in BGMN refinement for {control_file}. The exit code is {cp.returncode}\n"
                f"{cp.stdout}\n"
                f"{cp.stderr}"
            )

    def _run_cancellable(
        self,
        control_file: Path,
        timeout: float | None,
        cancel_event: threading.Event,
    ) -> None:
        limit = DEFAULT_BGMN_TIMEOUT_S if timeout is None else float(timeout)
        log_path = control_file.parent / "bgmn.log"
        cmd = [self.bgmn_path.as_posix(), control_file.absolute().as_posix()]
        with open(log_path, "wb") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=control_file.parent.absolute().as_posix(),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            started = time.monotonic()
            while True:
                if proc.poll() is not None:
                    break
                if cancel_event.is_set():
                    _kill_process_tree(proc)
                    _reap(proc)
                    raise RefinementCancelled("Refinement cancelled.")
                if time.monotonic() - started > limit:
                    _kill_process_tree(proc)
                    _reap(proc)
                    raise subprocess.TimeoutExpired(cmd, limit)
                cancel_event.wait(0.25)
        if proc.returncode:
            try:
                tail = log_path.read_text(errors="replace")[-4000:]
            except OSError:
                tail = ""
            raise RuntimeError(
                f"Error in BGMN refinement for {control_file}. The exit code is {proc.returncode}\n"
                f"{tail}\n"
            )


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
