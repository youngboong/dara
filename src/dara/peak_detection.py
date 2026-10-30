from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from dara.analysis_range import analysis_mask, normalize_excluded_ranges
from dara.eflech_worker import EflechWorker

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import pandas as pd


#: Seconds a cached peak list is kept (the cache only saves repeated EFLECH runs).
PEAK_CACHE_MAX_AGE_S = 7 * 24 * 3600


def _peak_cache_dir() -> _Path:
    root = os.environ.get("DARA_PEAK_CACHE_DIR")
    return _Path(root) if root else _Path(tempfile.gettempdir()) / "dara-peak-cache"


def _file_digest(path: _Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cache_key(pattern, wavelength, instrument_profile, wmin, wmax, epsilon,
               possible_changes, excluded_ranges) -> str | None:
    """Key of everything that determines EFLECH's peak list, or ``None`` when not cacheable."""
    if isinstance(pattern, np.ndarray):
        return None
    try:
        path = _Path(pattern)
        profile = str(instrument_profile)
        profile_path = _Path(profile)
        parts = {
            "pattern": _file_digest(path),
            "suffix": path.suffix.lower(),
            "wavelength": wavelength,
            "profile": _file_digest(profile_path) if profile_path.is_file() else profile,
            "wmin": wmin, "wmax": wmax, "epsilon": epsilon,
            "possible_changes": possible_changes,
            "excluded": normalize_excluded_ranges(excluded_ranges),
        }
        return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def _read_cached(path: _Path):
    try:
        return pd.read_pickle(path)
    except Exception:  # noqa: BLE001 - a damaged entry is recomputed
        return None


def _prune_cache(directory: _Path) -> None:
    cutoff = time.time() - PEAK_CACHE_MAX_AGE_S
    for entry in directory.glob("*.pkl"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            pass


def detect_peaks(
    pattern: Path | np.ndarray,
    wavelength: Literal["Cu", "Co", "Cr", "Fe", "Mo"] | float = "Cu",
    instrument_profile: str | Path = "Aeris-fds-Pixcel1d-Medipix3",
    wmin: float = None,
    wmax: float = None,
    epsilon: float = None,
    possible_changes: str = None,
    show_progress: bool = False,
    nthreads: int = 8,
    timeout: int = 1800,
    excluded_ranges: list[list[float]] | None = None,
) -> pd.DataFrame:
    """Observed peaks of a pattern from EFLECH.

    Results for a pattern file are cached on disk by the file contents and every setting
    that affects EFLECH. A search refines dozens of references against the same pattern
    and each refinement asks for these peaks; running EFLECH again for each one (in
    parallel workers) was the dominant cost for dense patterns. The cached list is the
    one EFLECH returned for identical inputs, so results do not change. One worker runs
    EFLECH while the others wait for its result.
    """
    key = _cache_key(pattern, wavelength, instrument_profile, wmin, wmax, epsilon,
                     possible_changes, excluded_ranges)
    if key is None:
        return _detect_peaks(pattern, wavelength, instrument_profile, wmin, wmax, epsilon,
                             possible_changes, show_progress, nthreads, timeout, excluded_ranges)
    directory = _peak_cache_dir()
    target = directory / f"{key}.pkl"
    lock = directory / f"{key}.lock"
    deadline = time.monotonic() + timeout + 60
    while True:
        if target.is_file():
            cached = _read_cached(target)
            if cached is not None:
                return cached.copy()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Another worker is running EFLECH for the same inputs; a lock older than the
            # EFLECH timeout belongs to a worker that died and is taken over.
            try:
                if time.time() - lock.stat().st_mtime > timeout + 60:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                break
            time.sleep(0.5)
            continue
        except OSError:
            break
        os.close(handle)
        try:
            if target.is_file() and (cached := _read_cached(target)) is not None:
                return cached.copy()
            result = _detect_peaks(pattern, wavelength, instrument_profile, wmin, wmax, epsilon,
                                   possible_changes, show_progress, nthreads, timeout,
                                   excluded_ranges)
            try:
                partial = directory / f"{key}.{os.getpid()}.tmp"
                result.to_pickle(partial)
                os.replace(partial, target)
                _prune_cache(directory)
            except OSError:
                pass
            return result.copy()
        finally:
            try:
                lock.unlink()
            except OSError:
                pass
    # The cache could not be used (unwritable directory, stuck lock): run EFLECH directly.
    return _detect_peaks(pattern, wavelength, instrument_profile, wmin, wmax, epsilon,
                         possible_changes, show_progress, nthreads, timeout, excluded_ranges)


def _detect_peaks(
    pattern: Path | np.ndarray,
    wavelength: Literal["Cu", "Co", "Cr", "Fe", "Mo"] | float = "Cu",
    instrument_profile: str | Path = "Aeris-fds-Pixcel1d-Medipix3",
    wmin: float = None,
    wmax: float = None,
    epsilon: float = None,
    possible_changes: str = None,
    show_progress: bool = False,
    nthreads: int = 8,
    timeout: int = 1800,
    excluded_ranges: list[list[float]] | None = None,
) -> pd.DataFrame:
    eflech_worker = EflechWorker()
    exclusions = normalize_excluded_ranges(excluded_ranges)
    if exclusions:
        # EFLECH is run on each retained contiguous measurement interval. Do not
        # bridge holder peaks with invented intensities or concatenate angular gaps.
        from dara.xrd import load_pattern

        if isinstance(pattern, np.ndarray):
            data = np.asarray(pattern)
        else:
            measured = load_pattern(pattern)
            data = np.column_stack((measured.angles, measured.intensities))
        kept = np.flatnonzero(analysis_mask(data[:, 0], wmin, wmax, exclusions))
        segments = np.split(kept, np.flatnonzero(np.diff(kept) > 1) + 1)
        frames = []
        for segment in segments:
            if len(segment) < 3:
                continue
            frame = eflech_worker.run_peak_detection(
                pattern=data[segment], wavelength=wavelength,
                instrument_profile=instrument_profile, show_progress=show_progress,
                wmin=float(data[segment[0], 0]), wmax=float(data[segment[-1], 0]),
                epsilon=epsilon, possible_changes=possible_changes,
                nthreads=nthreads, timeout=timeout,
            )
            frames.append(frame)
        result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["2theta", "intensity", "b1", "b2"], dtype=float
        )
        return result.loc[analysis_mask(result["2theta"], wmin, wmax, exclusions)]
    return eflech_worker.run_peak_detection(
        pattern=pattern,
        wavelength=wavelength,
        instrument_profile=instrument_profile,
        show_progress=show_progress,
        wmin=wmin,
        wmax=wmax,
        epsilon=epsilon,
        possible_changes=possible_changes,
        nthreads=nthreads,
        timeout=timeout,
    )
