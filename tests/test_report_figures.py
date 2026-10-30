import re
import struct

import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace

from dara.server import review_router
from dara.server.report.figures import fit_figure

from test_report import A, B, _context, _refinement


def _fit(with_peaks=True):
    x = np.linspace(10, 80, 800)
    obs = 20 + 800 * np.exp(-0.5 * ((x - 30) / 0.1) ** 2)
    peaks = pd.DataFrame({
        "2theta": [30.0, 45.0, 50.0, 60.0, 65.0],
        "phase": [A, A, B, B, B],
    })
    refinement = SimpleNamespace(
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs * 0.98, y_bkg=np.full_like(x, 20.0)),
        lst_data=SimpleNamespace(phases_results={A: object(), B: object()}),
    )
    if with_peaks:
        refinement.peak_data = peaks
    return refinement


def _png_size(content):
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", content[16:24])


def test_png_size_follows_width_and_dpi():
    content = fit_figure(_fit(), fmt="png", dpi=200, width_mm=100)
    width, height = _png_size(content)
    assert width == pytest.approx(100 / 25.4 * 200, abs=2)
    assert height == pytest.approx(width * 0.68, abs=3)
    assert content.startswith(b"\x89PNG")


def test_dpi_and_width_are_clamped():
    width, _ = _png_size(fit_figure(_fit(), fmt="png", dpi=5, width_mm=10))
    assert width == pytest.approx(40 / 25.4 * 72, abs=2)
    width, _ = _png_size(fit_figure(_fit(), fmt="png", dpi=72, width_mm=5000))
    assert width == pytest.approx(250 / 25.4 * 72, abs=2)


def test_svg_has_text_legend_and_one_tick_row_per_phase():
    svg = fit_figure(_fit(), fmt="svg", excluded_ranges=[[20.0, 25.0]]).decode("utf-8")
    assert "<svg" in svg
    assert "Excluded from refinement" in svg
    assert 'id="ticks-0"' in svg and 'id="ticks-1"' in svg and 'id="ticks-2"' not in svg
    for label in ("Observed", "Calculated", "Background", "Difference"):
        assert label in svg
    assert "Intensity (counts)" in svg
    assert "2θ (°)" in svg
    assert "Excluded from refinement" not in fit_figure(_fit(), fmt="svg").decode("utf-8")
    assert "Intensity (a.u.)" in fit_figure(_fit(), fmt="svg", counts_like=False).decode("utf-8")


def test_svg_is_deterministic_and_handles_missing_peak_data():
    first = fit_figure(_fit(), fmt="svg")
    assert first == fit_figure(_fit(), fmt="svg")
    svg = fit_figure(_fit(with_peaks=False), fmt="svg").decode("utf-8")
    assert "ticks-0" not in svg


def test_invalid_inputs_raise_value_error():
    with pytest.raises(ValueError):
        fit_figure(_fit(), fmt="gif")
    broken = _fit()
    broken.plot_data.x = np.array([np.nan, np.nan])
    with pytest.raises(ValueError):
        fit_figure(broken, fmt="png")


def test_phase_labels_override():
    svg = fit_figure(_fit(), fmt="svg", phase_labels={A: "Phase one", B: "Phase two"}).decode()
    assert "Phase one" in svg and "Phase two" in svg


# --- endpoint -----------------------------------------------------------------------

def test_figure_endpoint(monkeypatch):
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0), _refinement(8.0)]
    )
    png = client.get("/api/task/9/figure.png?dpi=100&width_mm=80")
    assert png.status_code == 200 and png.headers["Content-Type"] == "image/png"
    assert "dara_task_9_best_figure.png" in png.headers["Content-Disposition"]
    width, _ = _png_size(png.content)
    assert width == pytest.approx(80 / 25.4 * 100, abs=2)

    svg = client.get("/api/task/9/figure.svg?idx=1")
    assert svg.status_code == 200 and svg.headers["Content-Type"] == "image/svg+xml"
    assert "dara_task_9_candidate_2_figure.svg" in svg.headers["Content-Disposition"]
    assert b"Excluded from refinement" in svg.content

    assert client.get("/api/task/9/figure.gif").status_code == 404
    assert client.get("/api/task/9/figure.svg?idx=0&best=true").status_code == 422
    assert client.get("/api/task/9/figure.svg?idx=99").status_code == 404
    assert client.get("/api/task/77/figure.svg").status_code == 404


def test_figure_endpoint_reports_render_failures(monkeypatch):
    client, _, _ = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])

    def boom(*args, **kwargs):
        raise RuntimeError("no font")

    monkeypatch.setattr("dara.server.report.figures.fit_figure", boom)
    response = client.get("/api/task/9/figure.png")
    assert response.status_code == 500
    assert "Figure rendering failed: no font" in response.text


def test_report_falls_back_to_builtin_svg_when_matplotlib_fails(monkeypatch):
    client, _, _ = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])

    def boom(*args, **kwargs):
        raise RuntimeError("no font")

    monkeypatch.setattr("dara.server.report.builder.fit_figure", boom)
    html = client.get("/api/task/9/report?best=true").text
    assert "<svg " in html and "Excluded from refinement" in html
    # the figure caption stays below the figure; table captions sit above their tables
    assert re.search(r"</svg><figcaption>Figure 1\. ", html)
    assert re.search(r"<table><caption><b>Table 1\.</b> ", html)
    assert review_router._fit_svg  # fallback remains available
