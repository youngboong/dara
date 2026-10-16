"""Validated 2-theta limits shared by fitting and peak diagnostics."""

from __future__ import annotations

import json

import numpy as np


def normalize_excluded_ranges(value=None) -> list[list[float]]:
    """Validate and merge closed exclusion intervals, in degrees 2-theta."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Excluded ranges must be a JSON list of [start, end] pairs") from exc
    if not isinstance(value, (list, tuple)):
        raise ValueError("Excluded ranges must be a list of [start, end] pairs")
    ranges = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each excluded range needs a start and end angle")
        try:
            lower, upper = map(float, pair)
        except (TypeError, ValueError) as exc:
            raise ValueError("Excluded angles must be finite numbers") from exc
        if not np.isfinite([lower, upper]).all() or not 0 <= lower < upper <= 180:
            raise ValueError("Excluded ranges must satisfy 0 <= start < end <= 180 degrees")
        ranges.append([lower, upper])
    merged = []
    for lower, upper in sorted(ranges):
        if merged and lower <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], upper)
        else:
            merged.append([lower, upper])
    return merged


def analysis_mask(angles, wmin=None, wmax=None, excluded_ranges=None) -> np.ndarray:
    """Return valid fitted positions, excluding interval boundaries as well."""
    angles = np.asarray(angles, dtype=float)
    mask = np.isfinite(angles)
    if wmin is not None:
        mask &= angles >= wmin
    if wmax is not None:
        mask &= angles <= wmax
    for lower, upper in normalize_excluded_ranges(excluded_ranges):
        mask &= (angles < lower) | (angles > upper)
    return mask


def validate_analysis_range(angles, wmin=None, wmax=None, excluded_ranges=None) -> dict:
    """Validate user limits against the measurement without modifying its data."""
    angles = np.asarray(angles, dtype=float)
    finite = angles[np.isfinite(angles)]
    if finite.size < 3:
        raise ValueError("The pattern must contain at least three valid angular positions")
    for label, bound in (("start", wmin), ("end", wmax)):
        if bound is not None and (not np.isfinite(bound) or not 0 <= bound <= 180):
            raise ValueError(f"Analysis {label} must be between 0 and 180 degrees")
    lower = float(finite.min()) if wmin is None else float(wmin)
    upper = float(finite.max()) if wmax is None else float(wmax)
    if lower >= upper:
        raise ValueError("Analysis start angle must be below the end angle")
    if upper < finite.min() or lower > finite.max():
        raise ValueError("Analysis range does not overlap the measured pattern")
    ranges = normalize_excluded_ranges(excluded_ranges)
    if np.count_nonzero(analysis_mask(angles, lower, upper, ranges)) < 3:
        raise ValueError("The analysis range and exclusions leave fewer than three data points")
    params = {}
    if wmin is not None:
        params["wmin"] = float(wmin)
    if wmax is not None:
        params["wmax"] = float(wmax)
    if ranges:
        params["excluded_ranges"] = ranges
    return params
