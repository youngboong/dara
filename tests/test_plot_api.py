import asyncio
import json
from types import SimpleNamespace

from dara.server import api_router


class MemoryStore:
    def __init__(self, row, calls=None):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def query_one(self, *_args, **_kwargs):
        if self.calls is not None:
            self.calls.append(1)
        return self.row


def test_plot_json_is_cached_for_completed_result(monkeypatch):
    calls = []
    renders = []
    figure = SimpleNamespace(to_json=lambda: '{"data":[],"layout":{}}')
    refinement = SimpleNamespace(visualize=lambda: renders.append(1) or figure)
    document = SimpleNamespace(final_result=refinement, results=[])

    monkeypatch.setattr(
        api_router, "get_result_store",
        lambda: MemoryStore({"uuid": "cached-result", "output": document}, calls),
    )
    monkeypatch.setattr(
        api_router, "MontyDecoder",
        lambda: SimpleNamespace(process_decoded=lambda value: value),
    )
    api_router._plot_json.cache_clear()
    try:
        first = api_router._plot_json("cached-result", None)
        second = api_router._plot_json("cached-result", None)
    finally:
        api_router._plot_json.cache_clear()

    assert first == second
    assert len(calls) == 1
    assert len(renders) == 1


def test_plot_endpoint_returns_direct_cacheable_json(monkeypatch):
    payload = '{"data":[],"layout":{"title":"fit"}}'
    monkeypatch.setattr(
        api_router, "get_worker_store",
        lambda: MemoryStore({"uuid": "result-id", "status": "COMPLETED"}),
    )
    monkeypatch.setattr(api_router, "_plot_json", lambda uuid, idx: payload)

    response = asyncio.run(api_router.plot(7, 0))

    assert json.loads(response.body) == {"data": [], "layout": {"title": "fit"}}
    assert response.body.startswith(b"{")
    assert response.media_type == "application/json"
    assert response.headers["cache-control"] == "private, max-age=3600"
