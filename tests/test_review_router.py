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


class ASGIClient:
    """Exercise the actual HTTP schema without an optional httpx dependency."""

    def __init__(self, app):
        self.app = app

    def request(self, method, url, payload=None):
        async def run():
            parts = urlsplit(url)
            body = json.dumps(payload).encode() if payload is not None else b""
            messages = []

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            async def send(message):
                messages.append(message)

            await self.app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": parts.path, "root_path": "",
                "query_string": parts.query.encode(), "server": ("test", 80),
                "client": ("test", 123), "headers": [(b"content-type", b"application/json")],
            }, receive, send)
            start = next(item for item in messages if item["type"] == "http.response.start")
            raw = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            headers = {key.decode().title(): value.decode() for key, value in start["headers"]}
            return SimpleNamespace(status_code=start["status"], text=raw.decode(), headers=headers, json=lambda: json.loads(raw))

        return asyncio.run(run())

    def get(self, url):
        return self.request("GET", url)

    def put(self, url, json=None):
        return self.request("PUT", url, json)


def _refinement(rwp=5.0):
    phase = SimpleNamespace(
        spacegroup_no=225, hermann_mauguin="Fm-3m", gewicht=1.0,
        a=(0.546, 0.0001), b=None, c=None, alpha=None, beta=None, gamma=None,
        xray_density=3.2, rphase=4.0, model_extra={},
    )
    return SimpleNamespace(
        lst_data=SimpleNamespace(
            rwp=rwp, rexp=2.0, rp=3.0, rpb=4.0, r=5.0, d=1.8,
            rho=5.0, num_steps=3,
            phases_results={"CaF2_225_(cod_2300449)-0": phase},
        ),
        plot_data=SimpleNamespace(
            x=np.array([20.0, 21.0, 22.0, 30.0]),
            y_obs=np.array([2.0, 100.0, 3.0, 1.0]),
            y_calc=np.array([2.0, 98.0, 4.0, 1.0]),
            y_bkg=np.array([1.0, 1.0, 1.0, 1.0]),
        ),
    )


@pytest.fixture
def context(monkeypatch):
    task = {
        "index": 9, "uuid": "task-9", "status": "COMPLETED", "user": "Analyst",
        "job": {"name": 'Sample <script>alert("sample")</script>.xrdml'},
    }
    doc = SimpleNamespace(
        final_result=_refinement(3.0), best_rwp=3.0,
        results=[([], _refinement(5.0)), ([], _refinement(6.0))],
        xrd_data=SimpleNamespace(), task_label="sample",
        search_kwargs={"instrument_profile": "Test", "refinement_params": {"excluded_ranges": [[20.5, 21.5]]}, "api_key": "not-for-report"},
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
                return deepcopy(task) if criteria.get("index") == task.get("index") else None
            return {"output": doc}

        def update(self, value):
            task.clear()
            task.update(deepcopy(value))

    monkeypatch.setattr(review_router, "get_worker_store", lambda: Store("worker"))
    monkeypatch.setattr(review_router, "get_result_store", lambda: Store("result"))
    monkeypatch.setattr(review_router, "MontyDecoder", lambda: SimpleNamespace(process_decoded=lambda output: output))
    app = FastAPI()
    app.include_router(review_router.router)
    return ASGIClient(app), task, doc


def test_review_persists_without_replacing_minimum_rwp(context):
    client, task, doc = context
    assert client.get("/api/task/9/review").json() == {"selection": None, "notes": "", "updated_at": None}
    response = client.put("/api/task/9/review", json={"selection": 1, "notes": "Chemistry supports candidate 2."})
    assert response.status_code == 200
    assert response.json()["updated_at"]
    assert client.get("/api/task/9/review").json()["selection"] == 1
    assert task["job"]["name"].startswith("Sample")
    assert doc.best_rwp == 3.0
    assert doc.final_result.lst_data.rwp == 3.0
    assert client.put("/api/task/9/review", json={"selection": None, "notes": "Keep note"}).json()["selection"] is None


@pytest.mark.parametrize("selection,status", [(-1, 404), (2, 404), (True, 422), ("1", 422), (1.2, 422)])
def test_invalid_review_selection_is_not_saved(context, selection, status):
    client, task, _ = context
    assert client.put("/api/task/9/review", json={"selection": selection}).status_code == status
    assert "review" not in task


def test_notes_length_and_task_state_validation(context):
    client, task, _ = context
    assert client.put("/api/task/9/review", json={"notes": "x" * 10001}).status_code == 422
    assert client.get("/api/task/999/review").status_code == 404
    task["status"] = "RUNNING"
    assert client.get("/api/task/9/review").status_code == 409
    assert client.put("/api/task/9/review", json={"selection": "best"}).status_code == 409
    assert client.get("/api/task/9/report").status_code == 409


def test_report_defaults_to_adoption_and_escapes_all_user_content(context):
    client, _, _ = context
    client.put("/api/task/9/review", json={"selection": 1, "notes": "<script>alert('note')</script>"})
    response = client.get("/api/task/9/report")
    assert response.status_code == 200
    assert "candidate_2_report.html" in response.headers["Content-Disposition"]
    assert "Result shown: candidate 2, adopted by the user" in response.text
    assert '<script>' not in response.text
    assert "&lt;script&gt;" in response.text
    assert '<svg ' in response.text
    assert "COD 2300449" in response.text
    assert "±" in response.text
    assert "Excluded from refinement" in response.text
    assert "not-for-report" not in response.text
    assert "plotly" not in response.text.lower()
    assert "Result shown: minimum-Rwp result" not in response.text
    best = client.get("/api/task/9/report?best=true")
    assert "best_report.html" in best.headers["Content-Disposition"]
    assert "adopted" not in best.text.lower().split("<h2>")[0]
    assert "candidate_1_report.html" in client.get("/api/task/9/report?idx=0").headers["Content-Disposition"]
    assert client.get("/api/task/9/report?idx=0&best=true").status_code == 422
    assert client.get("/api/task/9/report?idx=100").status_code == 404


def test_svg_does_not_connect_large_angular_gaps():
    svg = review_router._fit_svg(_refinement())
    # Each observed/calculated/background/difference path starts a fresh segment
    # at 30 degrees after the excluded 22-to-30 gap.
    curves = re.findall(r'<path d="([^"]+)" fill="none"', svg)
    assert len(curves) == 4
    assert all("M1060.00," in curve for curve in curves)


def test_provenance_supports_mp_and_escapes_unknown_sources():
    assert "Materials Project mp-149" in review_router._provenance("Si_227_(mp-149)-0")
    assert "not recorded" in review_router._provenance("user_supplied_phase")


def test_report_lists_automated_cautions_with_escaped_text(context):
    client, _, doc = context
    doc.final_result = _refinement(40.0)  # high Rwp and GOF 20 trigger a poor-fit flag
    response = client.get("/api/task/9/report?best=true")
    assert "Review cautions</h3>" in response.text
    assert "Fit residuals are high" in response.text
    clean = client.get("/api/task/9/report?idx=0")
    assert "Review cautions" not in clean.text and "Fit residuals are high" not in clean.text
