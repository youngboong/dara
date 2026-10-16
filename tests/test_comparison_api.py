import asyncio

import numpy as np
import pytest
from fastapi import HTTPException

from dara.server import comparison_router
from dara.xrd import XRDData


class StoredRecord:
    def __init__(self, record):
        self.record = record

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def query_one(self, criteria):
        return self.record


def _stores(monkeypatch, job, result=None):
    monkeypatch.setattr(comparison_router, "get_worker_store", lambda: StoredRecord(job))
    monkeypatch.setattr(comparison_router, "get_result_store", lambda: StoredRecord(result))


def test_comparison_uses_original_input_without_exposing_job(monkeypatch):
    original = XRDData([30, 10, 20], [3, 1, 2])
    job = {
        "uuid": "test", "index": 7,
        "job": {"name": r"C:\private\sample.xy", "function_kwargs": {
            "xrd_data": original.as_dict(), "search_kwargs": {"wavelength": "Cu"},
            "private_token": "not-returned",
        }},
    }
    _stores(monkeypatch, job, {"output": {"xrd_data": XRDData([10, 20], [99, 99]).as_dict()}})
    result = asyncio.run(comparison_router.measured_pattern(7))
    assert result["angles"] == [10, 20, 30]
    assert result["intensities"] == [1, 2, 3]
    assert result["name"] == "sample.xy"
    assert result["source"] == "original input"
    assert result["wavelength"] == "Cu"
    assert "private_token" not in str(result)
    assert "C:\\private" not in str(result)


def test_comparison_falls_back_to_result_input(monkeypatch):
    _stores(monkeypatch, {"uuid": "test", "job": {"name": "sample"}}, {
        "output": {"xrd_data": {"angles": [10, 20], "intensities": [1, 2]}, "search_kwargs": {"wavelength": 1.5406}},
    })
    result = asyncio.run(comparison_router.measured_pattern(7))
    assert result["source"] == "stored result input"
    assert result["wavelength"] == "1.5406"


def test_comparison_accepts_positional_pattern():
    pattern, settings = comparison_router._input_pattern({"job": {"function_args": ["pattern"], "function_kwargs": {"search_kwargs": {"wavelength": "Co"}}}})
    assert pattern == "pattern"
    assert settings["wavelength"] == "Co"


def test_comparison_filters_nonfinite_points_without_mutating_data():
    pattern = XRDData([10, 20, 30, 40], [1, np.nan, 3, np.inf])
    angles, intensities, dropped = comparison_router._pattern_arrays(pattern)
    assert angles == [10, 30]
    assert intensities == [1, 3]
    assert dropped == 2
    assert len(pattern.intensities) == 4


@pytest.mark.parametrize("job, result, code", [
    (None, None, 404),
    ({"uuid": "test", "job": {}}, None, 404),
    ({"uuid": "test", "job": {"function_kwargs": {"xrd_data": {"angles": [10, 20], "intensities": [1]}}}}, None, 422),
])
def test_comparison_returns_useful_errors(monkeypatch, job, result, code):
    _stores(monkeypatch, job, result)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(comparison_router.measured_pattern(7))
    assert exc.value.status_code == code
