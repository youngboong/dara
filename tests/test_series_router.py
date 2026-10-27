import asyncio
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from maggma.stores import MontyStore

from dara.server import series_router


class ASGIClient:
    """Exercise the real HTTP schema without the optional httpx dependency."""

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
            return SimpleNamespace(status_code=start["status"], json=lambda: json.loads(raw))

        return asyncio.run(run())

    def get(self, url):
        return self.request("GET", url)

    def post(self, url, json=None):
        return self.request("POST", url, json)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(series_router.router)
    return ASGIClient(app)


def test_preview_endpoint(client):
    response = client.post("/api/series/preview", {"filenames": ["ZnO_500C.xy", "ZnO_300C.xy", "ZnO_700C.xy"]})
    assert response.status_code == 200
    body = response.json()
    assert body["unit"] == "°C"
    by_name = {item["filename"]: item for item in body["items"]}
    assert [by_name[n]["order"] for n in ("ZnO_300C.xy", "ZnO_500C.xy", "ZnO_700C.xy")] == [0, 1, 2]
    assert by_name["ZnO_300C.xy"]["variable"]["value"] == 300
    plain = client.post("/api/series/preview", {"filenames": ["b.xy", "a.xy"]}).json()
    assert plain["unit"] is None and [item["order"] for item in plain["items"]] == [1, 0]


# --- submission ------------------------------------------------------------------------


class FakeQueue:
    def __init__(self):
        self.built = []
        self.queued = []

    def build(self, pattern, name, **fields):
        self.built.append((name, pattern, fields))
        return SimpleNamespace(name=name, metadata={})

    def queue(self, job, user):
        self.queued.append((job, user))
        return 100 + len(self.queued)


@pytest.fixture
def fake(monkeypatch):
    queue = FakeQueue()
    monkeypatch.setattr(series_router, "_build_job", queue.build)
    monkeypatch.setattr(series_router, "_queue", queue.queue)
    monkeypatch.setattr(series_router, "_wavelength_mismatch", lambda pattern, path, wavelength: None)

    def load(path):
        if "broken" in path.name:
            raise ValueError("not a pattern")
        return SimpleNamespace(name=path.name)

    monkeypatch.setattr(series_router, "load_pattern", load)
    return queue


def _uploads(*names):
    return [UploadFile(file=io.BytesIO(b"20 1\n30 2\n"), filename=name) for name in names]


def _submit(names, **kwargs):
    kwargs.setdefault("user", "amy")
    kwargs.setdefault("series_name", "Anneal")
    kwargs.setdefault("pattern_files", _uploads(*names))
    return asyncio.run(series_router.submit_series(**{**_defaults(), **kwargs}))


def _defaults():
    return {
        "precursor_formulas": "[]", "elements": '["Zn", "O"]', "analysis_mode": "elemental",
        "instrument_profile": "Aeris-fds-Pixcel1d-Medipix3", "wavelength": "Cu", "temperature": -1,
        "atmosphere": "air", "use_cod": True, "use_mp": False, "mp_e_hull_cutoff": 0.05,
        "max_mp_structures": 100, "additional_phases": None, "library_cif_ids": "[]",
        "two_theta_min": None, "two_theta_max": None, "excluded_ranges": "[]",
        "refinement_controls": "{}", "confirm_wavelength": False,
    }


def test_submit_three_files_queues_in_variable_order(fake):
    names = ["ZnO_700C.xy", "ZnO_300C.xy", "ZnO_500C.xy"]
    body = _submit(names)
    assert body["message"] == "submitted" and body["unit"] == "°C"
    assert [fake.queued[i][0].name for i in range(3)] == ["ZnO_300C.xy", "ZnO_500C.xy", "ZnO_700C.xy"]
    assert body["task_ids"] == [101, 102, 103]
    assert [v["value"] for v in body["variables"]] == [300, 500, 700]
    series_ids = {job.metadata["series"]["id"] for job, _ in fake.queued}
    assert series_ids == {body["series_id"]} and len(body["series_id"]) == 12
    first = fake.queued[0][0].metadata["series"]
    assert first["name"] == "Anneal" and first["order"] == 0 and first["size"] == 3
    assert first["variable"]["raw"] == "300C"
    assert all(user == "amy" for _, user in fake.queued)
    # Settings reach every job unchanged, with their own copy of the extra CIF list.
    for _, _, fields in fake.built:
        assert fields["elements"] == '["Zn", "O"]' and fields["additional_cifs"] == []
        assert fields["library_cif_ids"] == "[]"


def test_unreadable_file_rejects_everything_and_queues_nothing(fake):
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xy", "broken_500C.xy", "c_700C.xy"])
    assert caught.value.status_code == 400
    assert "broken_500C.xy" in caught.value.detail and "Could not read pattern" in caught.value.detail
    assert caught.value.detail.count("broken_500C.xy") == 1  # the file name is not repeated
    assert caught.value.detail.startswith("broken_500C.xy: Could not read pattern")
    assert fake.queued == []


def test_job_build_error_names_the_file_and_queues_nothing(fake, monkeypatch):
    def build(pattern, name, **fields):
        if "500" in name:
            raise HTTPException(status_code=400, detail="Invalid element symbols")
        return SimpleNamespace(name=name, metadata={})

    monkeypatch.setattr(series_router, "_build_job", build)
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xy", "a_500C.xy"])
    assert caught.value.detail == "a_500C.xy: Invalid element symbols"
    assert fake.queued == []


def test_wavelength_mismatch_without_confirmation_names_the_file(fake, monkeypatch):
    mismatch = {"code": "wavelength_mismatch", "message": "differs"}
    monkeypatch.setattr(
        series_router, "_wavelength_mismatch",
        lambda pattern, path, wavelength: mismatch if "500" in path.name else None,
    )
    with pytest.raises(HTTPException) as caught:
        _submit(["a_300C.xrdml", "a_500C.xrdml"])
    assert caught.value.detail["code"] == "wavelength_mismatch"
    assert caught.value.detail["filename"] == "a_500C.xrdml"
    assert fake.queued == []
    body = _submit(["a_300C.xrdml", "a_500C.xrdml"], confirm_wavelength=True)
    assert body["message"] == "submitted" and len(fake.queued) == 2


def test_file_count_limits_name_and_duplicates(fake):
    with pytest.raises(HTTPException) as too_many:
        _submit([f"s_{i}C.xy" for i in range(series_router.MAX_SERIES_FILES + 1)])
    assert too_many.value.status_code == 400
    with pytest.raises(HTTPException) as too_few:
        _submit(["only.xy"])
    assert too_few.value.status_code == 400
    with pytest.raises(HTTPException) as duplicate:
        _submit(["a.xy", "a.xy"])
    assert "a.xy" in duplicate.value.detail
    with pytest.raises(HTTPException) as blank:
        _submit(["a.xy", "b.xy"], series_name="   ")
    assert blank.value.status_code == 400
    with pytest.raises(HTTPException) as long_name:
        _submit(["a.xy", "b.xy"], series_name="x" * 121)
    assert long_name.value.status_code == 400
    assert fake.queued == []


def test_exactly_fifty_files_are_accepted(fake):
    body = _submit([f"s_{i}C.xy" for i in range(series_router.MAX_SERIES_FILES)])
    assert len(body["task_ids"]) == 50


# --- listing and detail ----------------------------------------------------------------


def _store(path):
    return MontyStore(
        database_name="dara", collection_name="jobs", database_path=str(path),
        storage="sqlite", key=["uuid", "index"],
    )


def _series(series_id, name, order, size, value):
    return {"id": series_id, "name": name, "order": order, "size": size,
            "variable": None if value is None else {"raw": f"{value}C", "value": float(value), "unit": "°C"}}


@pytest.fixture
def populated(tmp_path, monkeypatch):
    base = datetime(2026, 5, 1, 12, tzinfo=timezone.utc)
    schema2 = {"schema": 2, "best_rwp": 5.0, "gof": 1.5, "phases": [], "confidence_overall": "high"}
    records = [
        {"uuid": "u1", "index": 1, "status": "COMPLETED", "submitted_time": base,
         "start_time": base, "end_time": base.replace(minute=5),
         "job": {"name": "a_500C.xy", "metadata": {"series": _series("s1", "Anneal", 1, 3, 500)}},
         "summary": schema2},
        {"uuid": "u2", "index": 2, "status": "COMPLETED", "submitted_time": base,
         "start_time": base, "end_time": base.replace(minute=7),
         "job": {"name": "a_300C.xy", "metadata": {"series": _series("s1", "Anneal", 0, 3, 300)}},
         "summary": {"schema": 1, "best_rwp": 6.0, "gof": 1.9}},
        {"uuid": "u3", "index": 3, "status": "RUNNING", "submitted_time": base,
         "job": {"name": "a_700C.xy", "metadata": {"series": _series("s1", "Anneal", 2, 3, 700)}}},
        {"uuid": "u4", "index": 4, "status": "COMPLETED", "submitted_time": base.replace(day=9),
         "job": {"name": "b_1.xy", "metadata": {"series": _series("s2", "Later", 0, 2, None)}},
         "summary": schema2},
        {"uuid": "u5", "index": 5, "status": "READY", "submitted_time": base.replace(day=9),
         "job": {"name": "b_2.xy", "metadata": {"series": _series("s2", "Later", 1, 2, None)}}},
        {"uuid": "u6", "index": 6, "status": "COMPLETED", "submitted_time": base,
         "job": {"name": "single.xy", "metadata": {}}, "summary": schema2},
    ]
    with _store(tmp_path) as store:
        for record in records:
            store.update(record)
    monkeypatch.setattr(series_router, "get_worker_store", lambda: _store(tmp_path))
    series_router._summary_refresh_gave_up.clear()
    return tmp_path


def test_list_groups_series_newest_first(populated, client):
    body = client.get("/api/series").json()
    assert [item["series_id"] for item in body["series"]] == ["s2", "s1"]
    s1 = body["series"][1]
    assert s1["name"] == "Anneal" and s1["n_tasks"] == 3 and s1["n_completed"] == 2
    assert s1["unit"] == "°C" and s1["created_on"].startswith("2026-05-01")
    assert body["series"][0]["unit"] is None and body["series"][0]["n_completed"] == 1


def test_detail_sorted_by_order_and_stale_summary_refreshed(populated, client, monkeypatch):
    calls = []

    def fake_summarize(uuid, runtime_s):
        calls.append((uuid, runtime_s))
        return {"schema": 2, "best_rwp": 4.0, "gof": 1.2, "phases": [], "confidence_overall": "check"}

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", fake_summarize)
    body = client.get("/api/series/s1").json()
    assert body["name"] == "Anneal" and body["unit"] == "°C"
    assert [item["task_id"] for item in body["items"]] == [2, 1, 3]
    assert [item["order"] for item in body["items"]] == [0, 1, 2]
    assert body["items"][0]["variable"]["value"] == 300
    # Only the completed task with a schema-1 summary was refreshed (300 s runtime from the record).
    assert calls == [("u2", 420.0)]
    assert body["items"][0]["summary"]["confidence_overall"] == "check"
    assert body["items"][1]["summary"]["schema"] == 2
    assert body["items"][2]["state"] == "RUNNING" and body["items"][2]["summary"] is None
    # Persisted: a second call neither recomputes nor loses the new summary.
    again = client.get("/api/series/s1").json()
    assert calls == [("u2", 420.0)]
    assert again["items"][0]["summary"]["schema"] == 2
    with _store(populated) as store:
        assert store.query_one({"index": 2})["summary"]["best_rwp"] == 4.0


def test_refresh_failure_keeps_the_old_summary(populated, client, monkeypatch):
    def boom(uuid, runtime_s):
        raise LookupError("no stored result")

    monkeypatch.setattr("dara.server.task_summary.summarize_stored_result", boom)
    body = client.get("/api/series/s1").json()
    assert body["items"][0]["summary"]["schema"] == 1


def test_detail_unknown_series_is_404(populated, client):
    assert client.get("/api/series/nope").status_code == 404
