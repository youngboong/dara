"""Materials Project key status endpoint (the probe is always replaced)."""

import asyncio

import pytest

from dara.server import mp_router


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    mp_router._cache.clear()
    yield
    mp_router._cache.clear()


def call(refresh=False):
    return asyncio.run(mp_router.mp_key_status(refresh=refresh))


def fake_probe(monkeypatch, behaviour=None):
    calls = []

    def probe():
        calls.append(1)
        if behaviour is not None:
            raise behaviour

    monkeypatch.setattr(mp_router, "_probe_mp_key", probe)
    return calls


def test_not_configured_makes_no_request(monkeypatch):
    monkeypatch.delenv("MP_API_KEY", raising=False)
    calls = fake_probe(monkeypatch)
    assert call() == {"configured": False, "valid": None, "checked_at": None, "error": None}
    assert calls == []
    monkeypatch.setenv("MP_API_KEY", "")
    assert call()["configured"] is False
    assert calls == []


def test_valid_key_is_cached(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "secret-key-123")
    calls = fake_probe(monkeypatch)
    first = call()
    assert first["configured"] is True and first["valid"] is True and first["error"] is None
    assert first["checked_at"].endswith("+00:00")
    assert call() == first
    assert len(calls) == 1


def test_refresh_bypasses_cache(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "secret-key-123")
    calls = fake_probe(monkeypatch)
    call()
    call(refresh=True)
    assert len(calls) == 2


def test_changed_key_is_probed_again(monkeypatch):
    calls = fake_probe(monkeypatch)
    monkeypatch.setenv("MP_API_KEY", "key-one")
    call()
    monkeypatch.setenv("MP_API_KEY", "key-two")
    call()
    assert len(calls) == 2


def test_authentication_failure(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "secret-key-123")
    fake_probe(monkeypatch, RuntimeError("REST query returned with error status code 403. secret-key-123"))
    result = call()
    assert result["configured"] is True and result["valid"] is False
    assert "rejected" in result["error"]
    assert "secret-key-123" not in str(result)


def test_network_failure_is_undecided_and_hides_key(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "secret-key-123")
    fake_probe(monkeypatch, ConnectionError("Failed to connect to api.materialsproject.org with secret-key-123"))
    result = call()
    assert result["valid"] is None
    assert result["error"].startswith("Could not reach the Materials Project")
    assert "secret-key-123" not in str(result)


def test_timeout(monkeypatch):
    monkeypatch.setenv("MP_API_KEY", "secret-key-123")
    monkeypatch.setattr(mp_router, "PROBE_TIMEOUT_SECONDS", 0.05)

    def slow():
        import time

        time.sleep(0.5)

    monkeypatch.setattr(mp_router, "_probe_mp_key", slow)
    result = call()
    assert result["valid"] is None and "did not answer" in result["error"]


def test_route_is_registered():
    assert any(route.path == "/api/mp-key/status" for route in mp_router.router.routes)
