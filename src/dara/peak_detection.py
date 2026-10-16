from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from dara.analysis_range import analysis_mask, normalize_excluded_ranges
from dara.eflech_worker import EflechWorker

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import pandas as pd


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
