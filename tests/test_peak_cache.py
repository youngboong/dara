import threading
import time

import numpy as np
import pandas as pd
import pytest

from dara import peak_detection


@pytest.fixture
def calls(monkeypatch, tmp_path):
    monkeypatch.setenv("DARA_PEAK_CACHE_DIR", str(tmp_path / "cache"))
    seen = []

    def fake(pattern, wavelength, instrument_profile, wmin, wmax, *rest):
        seen.append((wavelength, wmin, wmax))
        time.sleep(0.2)
        return pd.DataFrame({"2theta": [20.0 + len(seen)], "intensity": [100.0], "b1": [0.0], "b2": [0.0]})

    monkeypatch.setattr(peak_detection, "_detect_peaks", fake)
    return seen


def _pattern(tmp_path, text="10 1\n20 5\n30 1\n"):
    path = tmp_path / "p.xy"
    path.write_text(text)
    return path


def test_identical_inputs_run_eflech_once(calls, tmp_path):
    path = _pattern(tmp_path)
    first = peak_detection.detect_peaks(path, wmin=10, wmax=30)
    second = peak_detection.detect_peaks(str(path), wmin=10, wmax=30)
    assert len(calls) == 1
    pd.testing.assert_frame_equal(first, second)
    second.loc[0, "2theta"] = -1  # callers get copies
    assert peak_detection.detect_peaks(path, wmin=10, wmax=30).loc[0, "2theta"] == first.loc[0, "2theta"]


def test_any_changed_input_recomputes(calls, tmp_path):
    path = _pattern(tmp_path)
    peak_detection.detect_peaks(path, wmin=10, wmax=30)
    peak_detection.detect_peaks(path, wmin=10, wmax=25)
    peak_detection.detect_peaks(path, wavelength="Co", wmin=10, wmax=30)
    peak_detection.detect_peaks(path, wmin=10, wmax=30, excluded_ranges=[[14, 16]])
    _pattern(tmp_path, "10 1\n20 6\n30 1\n")  # same file name, new contents
    peak_detection.detect_peaks(path, wmin=10, wmax=30)
    assert len(calls) == 5


def test_arrays_are_not_cached(calls):
    data = np.array([[10.0, 1.0], [20.0, 5.0], [30.0, 1.0]])
    peak_detection.detect_peaks(data)
    peak_detection.detect_peaks(data)
    assert len(calls) == 2


def test_concurrent_callers_share_one_eflech_run(calls, tmp_path):
    path = _pattern(tmp_path)
    results = []
    threads = [threading.Thread(target=lambda: results.append(peak_detection.detect_peaks(path)))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1 and len(results) == 6
    assert all(frame.equals(results[0]) for frame in results)
