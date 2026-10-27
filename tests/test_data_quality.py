import asyncio
from io import BytesIO

import numpy as np
import pytest
from fastapi import HTTPException, UploadFile

from dara.server import api_router
from dara.server.data_quality import intensities_look_like_counts, pattern_quality


def _clean_pattern(step=0.02, low=10.0, high=80.0, seed=0):
    rng = np.random.default_rng(seed)
    angles = np.arange(low, high + step / 2, step)
    signal = np.full_like(angles, 80.0)
    for center in (25.0, 38.0, 44.0, 57.0, 64.0):
        signal += 8000.0 * np.exp(-0.5 * ((angles - center) / 0.12) ** 2)
    return angles, rng.poisson(signal).astype(float)


def _codes(checks):
    return {item["code"] for item in checks}


def test_clean_counts_pattern_has_no_warnings():
    angles, intensities = _clean_pattern()
    checks, stats = pattern_quality(angles, intensities)
    assert [item for item in checks if item["level"] == "warning"] == []
    assert checks == []
    assert stats["data_points"] == angles.size
    assert stats["median_step"] == pytest.approx(0.02)
    assert stats["span"] == pytest.approx(70.0)
    assert stats["snr_db"] > 35


def test_coarse_step_and_short_range():
    angles, intensities = _clean_pattern(step=0.1)
    checks, _ = pattern_quality(angles, intensities)
    assert "coarse_step" in _codes(checks)
    angles, intensities = _clean_pattern(low=10.0, high=25.0)
    checks, stats = pattern_quality(angles, intensities)
    assert "short_range" in _codes(checks)
    assert stats["span"] == pytest.approx(15.0)


def test_negative_nan_and_nonmonotonic():
    angles, intensities = _clean_pattern()
    intensities = intensities.copy()
    intensities[5] = -3.0
    intensities[9] = np.nan
    checks, _ = pattern_quality(angles, intensities)
    bad = next(item for item in checks if item["code"] == "negative_or_nan")
    assert bad["level"] == "warning" and bad["message"].startswith("2 point(s)")
    assert "nonmonotonic" not in _codes(checks)

    shuffled = angles.copy()
    shuffled[[10, 11]] = shuffled[[11, 10]]
    checks, _ = pattern_quality(shuffled, np.nan_to_num(intensities))
    assert "nonmonotonic" in _codes(checks)


def test_not_counts_is_info_only():
    angles, intensities = _clean_pattern()
    checks, _ = pattern_quality(angles, intensities / 7.3)
    item = next(item for item in checks if item["code"] == "not_counts")
    assert item["level"] == "info"
    checks, _ = pattern_quality(angles, intensities, "cps")
    assert "not_counts" in _codes(checks)
    checks, _ = pattern_quality(angles, intensities / 7.3, "counts")
    assert "not_counts" not in _codes(checks)


def test_counts_heuristics():
    assert not intensities_look_like_counts([0.1, 0.5, 1.0])
    assert not intensities_look_like_counts([10.2, 11.7, 13.1, 14.9])
    assert intensities_look_like_counts([10, 11, 130, 140])
    assert intensities_look_like_counts([])


def test_pure_noise_triggers_low_snr():
    rng = np.random.default_rng(3)
    angles = np.arange(10, 80, 0.02)
    checks, stats = pattern_quality(angles, rng.poisson(100, angles.size).astype(float))
    assert "low_snr" in _codes(checks)
    assert stats["snr_db"] < 35


def _upload(data: bytes, name="x.xy"):
    return UploadFile(file=BytesIO(data), filename=name)


def test_inspect_pattern_reports_quality_fields():
    angles, intensities = _clean_pattern(step=0.1, low=10, high=20)
    text = "\n".join(f"{a:.3f} {int(i)}" for a, i in zip(angles, intensities)).encode()
    payload = asyncio.run(api_router.inspect_pattern(_upload(text)))
    assert payload["data_points"] == angles.size
    assert isinstance(payload["load_warnings"], list)
    assert "intensity_unit" in payload
    codes = {item["code"] for item in payload["quality_checks"]}
    assert {"coarse_step", "short_range"} <= codes
    assert set(payload["quality_stats"]) == {"median_step", "span", "snr_db", "data_points"}
    assert payload["format"] == "xy"


def test_inspect_pattern_rejects_garbage():
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api_router.inspect_pattern(_upload(b"\x00\x01garbage not a pattern\xff")))
    assert caught.value.status_code == 400
    assert str(caught.value.detail).startswith("Could not read pattern")


def test_wavelength_mismatch_detection(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setattr(api_router, "get_xrdml_metadata", lambda _: {"k_alpha1_angstrom": 1.5406})
    pattern = SimpleNamespace(xrdml_dict={"x": 1})
    path = Path("a.xrdml")
    assert api_router._wavelength_mismatch(pattern, path, "Cu") is None
    assert api_router._wavelength_mismatch(pattern, path, "0.15406") is None  # nm, synchrotron field
    mismatch = api_router._wavelength_mismatch(pattern, path, "Co")
    assert mismatch["code"] == "wavelength_mismatch"
    assert mismatch["recorded_angstrom"] == pytest.approx(1.5406)
    assert mismatch["selected_angstrom"] == pytest.approx(1.78901)
    assert api_router._wavelength_mismatch(pattern, Path("a.xy"), "Co") is None
    assert api_router._wavelength_mismatch(SimpleNamespace(), path, "Co") is None
    assert api_router._wavelength_mismatch(pattern, path, "Unobtainium") is None
