"""Advisory data-quality checks for an uploaded XRD pattern.

The checks never block a submission: they only flag properties of the measured
data that may affect background fitting, peak detection or the reliability of
Rexp/GOF.
"""

from __future__ import annotations

import numpy as np

COARSE_STEP_DEG = 0.05
SHORT_SPAN_DEG = 25.0
LOW_SNR_DB = 35.0
MIN_POINTS_FOR_SNR = 200


def intensities_look_like_counts(intensities, unit: str | None = None) -> bool:
    """Return whether intensities plausibly are raw integer-like counts.

    An explicit ``unit`` from the file reader wins. Otherwise normalized data
    (maximum <= 1) and mostly non-integer values are treated as count rates.
    """
    if unit == "cps":
        return False
    if unit == "counts":
        return True
    values = np.asarray(intensities, dtype=float).ravel()
    values = values[np.isfinite(values)]
    if values.size == 0:
        return True
    if float(values.max()) <= 1.0:
        return False
    fractional = np.abs(values - np.round(values)) > 1e-6
    return not float(fractional.mean()) > 0.5


def pattern_quality(
    angles, intensities, intensity_unit: str | None = None
) -> tuple[list[dict], dict]:
    """Return ``(checks, stats)`` describing the measured pattern."""
    angles = np.asarray(angles, dtype=float).ravel()
    intensities = np.asarray(intensities, dtype=float).ravel()
    checks: list[dict] = []

    def add(code: str, level: str, message: str) -> None:
        checks.append({"code": code, "level": level, "message": message})

    finite_pair = np.isfinite(angles) & np.isfinite(intensities)
    finite_angles = angles[np.isfinite(angles)]
    finite_intensities = intensities[np.isfinite(intensities)]

    bad = int(np.count_nonzero(~finite_pair) + np.count_nonzero(intensities[finite_pair] < 0))
    if bad:
        add(
            "negative_or_nan",
            "warning",
            f"{bad} point(s) have negative or non-finite intensity; "
            "they may distort background and Rexp.",
        )

    steps = np.diff(finite_angles)
    non_increasing = int(np.count_nonzero(steps <= 0))
    if non_increasing:
        add(
            "nonmonotonic",
            "warning",
            f"2θ values are not strictly increasing ({non_increasing} step(s) ≤ 0).",
        )

    positive_steps = steps[steps > 0]
    median_step = float(np.median(positive_steps)) if positive_steps.size else None
    if median_step is not None and median_step > COARSE_STEP_DEG:
        add(
            "coarse_step",
            "warning",
            f"Median 2θ step is {median_step:.3f}° (> 0.05°); "
            "narrow peaks may be under-sampled.",
        )

    span = float(finite_angles.max() - finite_angles.min()) if finite_angles.size else None
    if span is not None and span < SHORT_SPAN_DEG:
        add(
            "short_range",
            "warning",
            f"Measured 2θ span is only {span:.1f}°; phase identification may be ambiguous.",
        )

    snr_db = None
    if finite_intensities.size >= MIN_POINTS_FOR_SNR:
        try:
            from dara.utils import estimate_snr

            value = float(estimate_snr(finite_intensities))
            snr_db = value if np.isfinite(value) else None
        except Exception:
            snr_db = None
    if snr_db is not None and snr_db < LOW_SNR_DB:
        add(
            "low_snr",
            "warning",
            f"Estimated signal-to-noise is low ({snr_db:.0f} dB); weak phases may be missed.",
        )

    if not intensities_look_like_counts(intensities, intensity_unit):
        add(
            "not_counts",
            "info",
            "Intensities look like count rates or normalized values, not raw counts; "
            "Rexp/GOF assume counting statistics and may be misleading.",
        )

    stats = {
        "median_step": median_step,
        "span": span,
        "snr_db": snr_db,
        "data_points": int(angles.size),
    }
    return checks, stats
