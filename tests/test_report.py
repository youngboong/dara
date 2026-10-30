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
    assert re.search(r"<h1>XRD phase analysis — Sample\.xy</h1>", html)
    assert re.search(r"Task 9 · \d{4}-\d{2}-\d{2} · DARA ", html)
    # numbered section headings; the notes section is omitted without notes
    assert _headings(html, 2) == [
        "1. Summary", "2. Experimental and methods", "3. Results", "4. Assessment",
        "5. Limitations", "6. References",
    ]
    assert "4.1 Phase verdicts" in _headings(html, 3)
    captions = re.findall(r"<caption><b>(Table \d+)\.</b> (.*?)</caption>", html)
    assert [number for number, _ in captions][:3] == ["Table 1", "Table 2", "Table 3"]
    assert [text for _, text in captions][:3] == [
        "Analysis conditions.", "Fit statistics.", "Phase quantification.",
    ]
    assert "<figure>" in html and "Figure 1. Observed (black), calculated (red)" in html
    # no adopted/non-default selection: no "Result shown" line, no old status wording
    assert "Result shown" not in html and "Minimum-Rwp" not in html
    assert "Recorded settings" not in html and "<pre>" not in html


def test_methods_paragraph_and_references(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    methods = html.split("<h2>2. Experimental and methods</h2>")[1].split("<table")[0]
    for token in ("BGMN 4.2.23", "DARA", "Aeris-fds-Pixcel1d-Medipix3", "λ = 1.540598 Å", "Cu Kα"):
        assert token in methods
    assert "zero-point error was fixed at zero" in methods
    references = html.split("<h2>6. References</h2>")[1]
    assert references.count("<li>") >= 5
    assert "10.1021/acs.chemmater.5c02820" in references
    assert "<ol" in references
    # citation numbers in the text match the list length
    cited = set(re.findall(r"\[(\d+)\]", html.split("<h2>6. References</h2>")[0]))
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
    # the one-line verdict note for the weakly supported phase names the alternative candidate
    verdicts = html.split("<h3>4.1 Phase verdicts</h3>")[1].split("<h3>")[0].split("<h2>")[0]
    assert "<li>" in verdicts and "fits almost as well" in verdicts
    assert "All phases are supported by every criterion" not in verdicts
    assert not re.search("[가-힣]", html)
    # the references remain in citation order: Hamilton comes before Toby
    references = html.split("References</h2>")[1]
    assert references.index("Significance tests") < references.index("R factors in Rietveld")


def test_candidate_report_uses_selected_refinement(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?idx=1").text
    assert "Result shown: candidate 2" in html
    assert "Rwp = 8.00%" in html


def test_summary_sentence_and_confidence_section(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    summary = html.split("<h2>1. Summary</h2>")[1].split("<h2>")[0]
    assert re.search(r"Quantified phases: .*100\.0\u2009±\u20090\.\d wt%", summary)
    assert "Overall assessment:" in summary and "GOF = " in summary
    # Fit-statistics table: two-decimal values and the refined displacement
    fit = html.split("Fit statistics.</caption>")[1].split("</table>")[0]
    assert '<td class="r nw">7.00</td>' in fit and "−0.24" in fit
    assert "Durbin–Watson d" in fit and "χ²" in fit
    # one-sentence verdict line instead of the old confidence table
    assert "All phases are supported by every criterion." in html
    assert "Hamilton test" not in html and "Weight / σ" not in html


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
    assert "<h2>5. Limitations</h2>" in html
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
    assert "Unexplained peaks</h3>" in html
    assert "at least 2% of the strongest peak with S/N ≥ 8" in html
    assert "% of strongest" in html and "Interpretation" in html
    assert '<td class="r nw">45.00</td>' in html and '<td class="nw">minor</td>' in html
    assert '<td class="r nw">60.00</td>' not in html  # a noise-level feature is not listed
    assert "further weak feature" not in html
    # the peak rows are not repeated as a caution bullet
    assert "minor unexplained peak(s)" not in html


def test_report_without_profiles_has_no_unexplained_table(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    assert "Unexplained peaks" not in html
    assert "4. Assessment" in html


# --- booktabs layout, conditions table and phase table --------------------------------------

def _plain_doc():
    return SimpleNamespace(
        final_result=_refinement(7.0), best_rwp=7.0, results=[], xrd_data=SimpleNamespace(),
        search_kwargs={}, task_label="t",
    )


def test_report_style_is_black_and_white_booktabs(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "overflow-wrap:anywhere" not in style
    assert "Latin Modern Roman" in style and "tabular-nums" in style
    assert "1.5px solid #000" in style and ".75px solid #000" in style
    assert not re.findall(r"#(?!000\b|fff\b)[0-9a-fA-F]{3,6}\b", style)  # only black and white
    assert "<colgroup>" in html and 'style="width:' in html


def test_analysis_conditions_table_is_human_readable(monkeypatch):
    client, task, doc = _context(monkeypatch, final=_refinement(7.0), results=[_refinement(7.0)])
    doc.analysis_mode = "reaction"
    doc.elements = ["Fe", "O"]
    doc.precursors = ["Fe2O3"]
    doc.final_refinement_params = {"gewicht": "SPHAR4", "lattice_range": 0.02}
    doc.predict_kwargs = {"temp": 1273}
    task["end_time"] = "2026-09-09 04:32:02.661000"
    html = client.get("/api/task/9/report?best=true").text
    conditions = html.split("Analysis conditions.</caption>")[1].split("</table>")[0]
    rows = dict(re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td></tr>", conditions))
    assert rows["Analysis mode"].startswith("Reaction-guided")
    assert rows["Elements"] == "Fe, O" and rows["Precursor formulas"] == "Fe2O3"
    assert rows["Databases"] == "COD, Materials Project"
    assert rows["Instrument profile"] == "Aeris-fds-Pixcel1d-Medipix3"
    assert rows["Radiation"] == "Cu Kα, λ = 1.540598 Å"
    assert "excluded 20.50–21.50°" in rows["2θ range analysed"]
    assert rows["Final refinement"] == (
        "Lattice ±2 %, preferred orientation: spherical harmonics order 4, peak broadening refined"
    )
    assert rows["Reaction prediction"] == "1000 °C (1273 K)"
    assert rows["Completed"] == "2026-09-09 04:32"
    assert "Instrument" not in rows  # no acquisition metadata recorded
    assert "Analyst" not in html and "Submitted" not in html


def test_phase_table_columns_and_reference_labels(plain):
    client, _, _ = plain
    html = client.get("/api/task/9/report?best=true").text
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    headers = re.findall(r"<th(?: [^>]*)?>(.*?)</th>", table)
    assert [h.replace("\u00a0", " ") for h in headers] == [
        "Phase", "Space group", "wt% ± esd", "Lattice parameters (Å, °)",
        "Crystallite size (nm)", "Verdict", "Reference",
    ]
    assert "COD 1011163" in table and "Fe2O3_167_(cod_1011163)-0" not in html
    assert "GrainSize" not in html
    assert "55.0\u2009±\u20093.0" in table  # refined crystallite size: value ± esd only
    assert "<span><i>a</i> = " in table and "<span><i>c</i> = " in table


def test_unrefined_grain_size_is_not_reported_for_every_phase(monkeypatch):
    final = _refinement(10.0, names=(A, B))
    for phase in final.lst_data.phases_results.values():
        # BGMN prints the same start-value GrainSize, without esd, for unrefined phases
        phase.model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": 0.005}
    client, _, _ = _context(monkeypatch, final=final, results=[final])
    html = client.get("/api/task/9/report?best=true").text
    table = html.split("Phase quantification.</caption>")[1].split("</table>")[0]
    assert "84.18" not in table
    assert table.count('<td class="nw">—</td>') >= 2


def test_lattice_cell_uses_greek_letters_and_esd_precision():
    from dara.server.report import builder

    cell = builder._lattice_cell({"lattice_parameters": {
        "a": {"value": 8.73139, "esd": 0.00099, "unit": "Å"},
        "beta": {"value": 122.44, "esd": 0.013, "unit": "deg"},
    }})
    assert "<i>β</i> = 122.440\u2009±\u20090.013" in cell and "beta" not in cell
    assert "<i>a</i> = 8.73139\u2009±\u20090.00099" in cell
    assert cell.count("<span>") == 2  # one parameter per line


def test_user_notes_only_when_recorded_and_escaped(plain):
    client, _, _ = plain
    assert "User review notes" not in client.get("/api/task/9/report?best=true").text
    html = build_report_html(
        3, {"job": {"name": "x.xy"}, "uuid": "u"}, _plain_doc(), "best",
        {"selection": None, "notes": "<b>check</b> peaks", "updated_at": None},
    )
    assert "6. User review notes" in html and "&lt;b&gt;check&lt;/b&gt; peaks" in html
