import asyncio
import json
import re
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urlsplit

import numpy as np
import pytest
from fastapi import FastAPI

from dara.server import review_router
from dara.server.report.builder import build_report_html
from dara.server.report.references import REFERENCES, Citations

A = "Fe2O3_167_(cod_1011163)-0"
B = "Al2O3_167_(cod_2300449)-0"


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def get(self, url):
        async def run():
            parts = urlsplit(url)
            messages = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                messages.append(message)

            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": "GET", "scheme": "http", "path": parts.path, "root_path": "",
                "query_string": parts.query.encode(), "server": ("test", 80),
                "client": ("test", 123), "headers": [],
            }, receive, send)
            start = next(item for item in messages if item["type"] == "http.response.start")
            raw = b"".join(
                item.get("body", b"") for item in messages if item["type"] == "http.response.body"
            )
            headers = {key.decode().title(): value.decode() for key, value in start["headers"]}
            return SimpleNamespace(
                status_code=start["status"], content=raw, headers=headers,
                text=raw.decode("utf-8", "replace"),
            )

        return asyncio.run(run())


def _refinement(rwp, names=(A,), parameters=20, eps2=(-0.001657, 3.3e-05)):
    def phase(weight):
        return SimpleNamespace(
            spacegroup_no=167, hermann_mauguin="R-3c", gewicht=(weight, 0.4),
            a=(0.5035, 0.0001), b=None, c=(1.375, 0.0002), alpha=None, beta=None, gamma=None,
            xray_density=3.2, rphase=4.0, model_extra={"GrainSize": (55.0, 3.0)}, unit="NM",
        )

    x = np.linspace(10, 80, 400)
    obs = 10 + 300 * np.exp(-0.5 * ((x - 33) / 0.15) ** 2)
    raw = (
        "Rietveld refinement to file(s) sample.xy\n"
        f"BGMN version 4.2.23, 4000 measured points, 78 peaks, {parameters} parameters\n"
    )
    weights = {name: 1.0 / len(names) for name in names}
    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8, rho=5.0, num_steps=3,
            phases_results={name: phase(w) for name, w in weights.items()},
            raw_lst=raw, EPS2=eps2,
        ),
        plot_data=SimpleNamespace(x=x, y_obs=obs, y_calc=obs + 1, y_bkg=np.full_like(x, 10.0)),
        get_phase_weights=lambda: weights,
    )


def _context(monkeypatch, *, final, results, wavelength="Cu"):
    task = {
        "index": 9, "uuid": "task-report", "status": "COMPLETED", "user": "Analyst",
        "job": {"name": "Sample.xy"},
    }
    doc = SimpleNamespace(
        final_result=final, best_rwp=final.lst_data.rwp,
        results=[([], refinement) for refinement in results],
        xrd_data=SimpleNamespace(), task_label="sample",
        candidate_sources=["cod", "mp"],
        search_kwargs={
            "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "wavelength": wavelength,
            "refinement_params": {"excluded_ranges": [[20.5, 21.5]]},
        },
    )

    class Store:
        def __init__(self, kind):
            self.kind = kind

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def query_one(self, criteria):
            if self.kind == "worker":
                return deepcopy(task) if criteria.get("index") == task["index"] else None
            return {"output": doc}

    monkeypatch.setattr(review_router, "get_worker_store", lambda: Store("worker"))
    monkeypatch.setattr(review_router, "get_result_store", lambda: Store("result"))
    monkeypatch.setattr(
        review_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda output: output)
    )
    app = FastAPI()
    app.include_router(review_router.router)
    return ASGIClient(app), task, doc


@pytest.fixture
def plain(monkeypatch):
    return _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0), _refinement(8.0)])


def _headings(html, level):
    return re.findall(rf"<h{level}>(.*?)</h{level}>", html)


def test_headings_present_in_order(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert re.search(r"<h1>X-ray powder diffraction analysis of Sample\.xy</h1>", html)
    assert _headings(html, 2) == [
        "Summary", "Experimental and methods", "Results",
        "Confidence assessment and review cautions", "Limitations", "User review notes",
        "References",
    ]
    assert "Recorded settings" in _headings(html, 3)
    assert {"Fit statistics", "Phase quantification and crystallography"} <= set(_headings(html, 3))
    assert "<figure>" in html and "Figure 1. Observed (black), calculated (red)" in html
    assert "Minimum-Rwp final result" in html


def test_methods_paragraph_and_references(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    methods = html.split("<h2>Experimental and methods</h2>")[1].split("<h3>")[0]
    for token in ("BGMN 4.2.23", "DARA", "Aeris-fds-Pixcel1d-Medipix3", "λ = 1.540598 Å", "Cu Kα"):
        assert token in methods
    assert "zero-point error was fixed at zero" in methods
    references = html.split("<h2>References</h2>")[1]
    assert references.count("<li>") >= 5
    assert "10.1021/acs.chemmater.5c02820" in references
    assert "<ol" in references
    # citation numbers in the text match the list length
    cited = set(re.findall(r"\[(\d+)\]", html.split("<h2>References</h2>")[0]))
    assert cited == {str(i) for i in range(1, references.count("<li>") + 1)}
    # DARA is cited first, Toby last (Limitations)
    assert references.index("Dara: Automated") < references.index("R factors in Rietveld")


def test_report_is_script_free_english_and_small(plain):
    client, _, _ = plain
    response = client.get("/api/task/9/report?best=true")
    html = response.text
    assert "plotly" not in html.lower()
    assert "<script" not in html.lower()
    assert not re.search("[가-힣]", html)
    assert len(response.content) < 3_000_000
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "<svg " in html and "Excluded from refinement" in html


def test_hamilton_reference_only_when_a_result_exists(monkeypatch, plain):
    client, _, _ = plain
    assert "Significance tests on the crystallographic R factor" not in client.get(
        "/api/task/9/report?best=true"
    ).text

    final = _refinement(10.0, names=(A, B), parameters=20)
    without_b = _refinement(10.005, names=(A,), parameters=10)
    client, _, _ = _context(monkeypatch, final=final, results=[final, without_b])
    html = client.get("/api/task/9/report?best=true").text
    assert "Significance tests on the crystallographic R factor" in html
    assert "Hamilton" in html and "not significant" in html
    assert not re.search("[가-힣]", html)
    # the references remain in citation order: Hamilton comes before Toby
    references = html.split("<h2>References</h2>")[1]
    assert references.index("Significance tests") < references.index("R factors in Rietveld")


def test_candidate_report_uses_selected_refinement(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?idx=1").text
    assert "Candidate source index 1 (stored result 2)" in html
    assert "The selected candidate model reached Rwp = 8.00%" in html


def test_summary_sentence_and_confidence_section(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert re.search(r"was quantified at 100\.0 ± 0\.\d wt% \(", html)
    assert "Overall assessment:" in html
    assert "Automated checks flag properties of the fit for review; they are not a verdict" in html
    assert "Refined specimen displacement: -0.24 mm" in html


def test_synchrotron_wavelength_and_unknown_radiation(monkeypatch):
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)], wavelength="0.0826"
    )
    html = client.get("/api/task/9/report?best=true").text
    assert "synchrotron radiation (λ = 0.826000 Å)" in html
    client, _, _ = _context(
        monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)], wavelength="Unobtainium"
    )
    assert "radiation and wavelength not recorded" in client.get("/api/task/9/report?best=true").text


def test_citations_registry_numbers_in_first_citation_order():
    citations = Citations()
    assert citations.cite("toby") == "[1]"
    assert citations.cite("dara") == "[2]"
    assert citations.cite("toby") == "[1]"
    html = citations.render_html()
    assert html.index("R factors") < html.index("Dara: Automated")
    with pytest.raises(KeyError):
        citations.cite("unknown")
    assert all(entry["text"] for entry in REFERENCES.values())
    assert REFERENCES["dara"]["doi"] == "10.1021/acs.chemmater.5c02820"
    assert REFERENCES["hamilton"]["doi"] == "10.1107/S0365110X65001081"
    assert REFERENCES["toby"]["doi"] == "10.1154/1.2179804"


def test_report_builder_can_be_called_directly():
    final = _refinement(7.0)
    doc = SimpleNamespace(
        final_result=final, best_rwp=7.0, results=[], xrd_data=SimpleNamespace(),
        search_kwargs={}, task_label="t",
    )
    html = build_report_html(
        3, {"job": {"name": "x.xy"}, "uuid": "u-direct"}, doc, "best",
        {"selection": None, "notes": "", "updated_at": None},
    )
    assert "<h2>Limitations</h2>" in html
    assert "recorded (radiation and wavelength not recorded)" in html
    assert json.dumps(html)  # serialisable text


def test_weight_text_rounds_to_the_precision_of_the_esd():
    from dara.server.report.builder import weight_text

    assert weight_text(25.8731, 0.6021) == "25.9 ± 0.6"
    assert weight_text(25.8731, 0.0421) == "25.87 ± 0.04"
    assert weight_text(25.8731, None) == "25.9"
    assert weight_text(None, 0.5) == "—"


def test_report_phase_table_uses_esd_precision(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert not re.search(r"\d\.\d{5,} ±", html)  # no 6-decimal weight percentages


def test_reported_mp_window_is_the_effective_one():
    from dara.server.report.builder import _effective_mp_window, _refinement_text

    window = [-0.045, 0.02]
    assert _effective_mp_window(0.02, window) == (-0.045, 0.02)  # default global range
    assert _effective_mp_window(0.05, window) == (-0.05, 0.05)  # wider global range wins
    assert _effective_mp_window("fixed", window) is None  # no widening happened
    summary = {"mp_lattice_window": {"range": window, "applied_to": ["Fe2O3_167_(mp_mp-1)-0"]}}
    assert "widened to -4.5% to +2.0%" in _refinement_text({"lattice_range": 0.02}, summary)
    assert "widened to -5.0% to +5.0%" in _refinement_text({"lattice_range": 0.05}, summary)
    assert "widened" not in _refinement_text({"lattice_range": "fixed"}, summary)


# --- unexplained observed peaks -------------------------------------------------------------

def _with_profile(refinement, injected, missing):
    x = np.arange(10.0, 80.0, 0.01)
    rng = np.random.default_rng(0)
    calc = 1000 + 10000 * np.exp(-0.5 * ((x - 33) / 0.065) ** 2)
    obs = calc + rng.normal(0, 31.6, x.size)
    for centre, height in injected:
        obs = obs + height * np.exp(-0.5 * ((x - centre) / 0.065) ** 2)
    refinement.plot_data = SimpleNamespace(x=x, y_obs=obs, y_calc=calc, y_bkg=np.full_like(x, 1000.0))
    refinement.refinement_metrics = SimpleNamespace(
        missing_peaks=missing, extra_peaks=[], intensity_mismatch_peaks=[]
    )
    return refinement


def test_report_lists_classified_unexplained_peaks(monkeypatch):
    final = _with_profile(_refinement(7.0), [(45.0, 150.0), (60.0, 20.0)], [[45.0, 3.0], [60.0, 1.0]])
    client, _, _ = _context(monkeypatch, final=final, results=[final])
    html = client.get("/api/task/9/report?best=true").text
    assert "<h3>Unexplained observed peaks</h3>" in html
    assert "classified by their height relative to the strongest peak" in html
    assert "at least 2% of the strongest peak and S/N ≥ 8" in html
    assert "% of strongest peak" in html and "Interpretation" in html
    assert "<td>45.00</td>" in html and "<td>minor</td>" in html
    assert "<td>60.00</td>" not in html  # a noise-level feature is summarised, not listed
    assert "1 further weak feature(s) are within the noise and are not listed." in html
    assert "minor_unexplained_peaks" in html


def test_report_without_profiles_has_no_unexplained_table(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert "Unexplained observed peaks" not in html
    assert "Confidence assessment and review cautions" in html
