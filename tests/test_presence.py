import asyncio

import pytest

from dara.server import presence


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(presence, "_clients", {})
    monkeypatch.setattr(presence, "_last_leave", None)
    monkeypatch.setattr(presence, "_ever_connected", False)
    monkeypatch.setattr(presence, "_started_at", 0.0)


def beat(client_id, at, monkeypatch):
    monkeypatch.setattr(presence.time, "monotonic", lambda: at)
    asyncio.run(presence.heartbeat(presence.Presence(client_id=client_id)))


def leave(client_id, at, monkeypatch):
    monkeypatch.setattr(presence.time, "monotonic", lambda: at)
    asyncio.run(presence.leave(presence.Presence(client_id=client_id)))


def test_waits_for_first_window_during_startup_grace():
    assert not presence.should_shut_down(60.0, jobs_active=False)
    assert presence.should_shut_down(presence.STARTUP_GRACE_S + 1, jobs_active=False)


def test_stays_up_while_any_window_is_open(monkeypatch):
    beat("a", 10.0, monkeypatch)
    beat("b", 11.0, monkeypatch)
    leave("a", 20.0, monkeypatch)
    assert not presence.should_shut_down(60.0, jobs_active=False)


def test_stops_after_last_window_leaves_and_grace_passes(monkeypatch):
    beat("a", 10.0, monkeypatch)
    leave("a", 20.0, monkeypatch)
    assert not presence.should_shut_down(20.0 + presence.LEAVE_GRACE_S - 1, jobs_active=False)
    assert presence.should_shut_down(20.0 + presence.LEAVE_GRACE_S, jobs_active=False)


def test_reload_within_grace_keeps_server(monkeypatch):
    beat("a", 10.0, monkeypatch)
    leave("a", 20.0, monkeypatch)
    beat("a", 22.0, monkeypatch)
    assert not presence.should_shut_down(60.0, jobs_active=False)


def test_window_that_stops_beating_times_out(monkeypatch):
    beat("a", 10.0, monkeypatch)
    assert not presence.should_shut_down(10.0 + presence.CLIENT_TIMEOUT_S - 1, jobs_active=False)
    assert presence.should_shut_down(10.0 + presence.CLIENT_TIMEOUT_S + 1, jobs_active=False)


def test_running_analysis_keeps_server(monkeypatch):
    beat("a", 10.0, monkeypatch)
    leave("a", 20.0, monkeypatch)
    assert not presence.should_shut_down(500.0, jobs_active=True)


def test_idle_shutdown_is_opt_in(monkeypatch):
    monkeypatch.delenv("DARA_IDLE_SHUTDOWN", raising=False)
    assert not presence.idle_shutdown_enabled()
    monkeypatch.setenv("DARA_IDLE_SHUTDOWN", "1")
    assert presence.idle_shutdown_enabled()


def test_watchdog_requests_exit_when_idle(monkeypatch):
    calls = []
    monkeypatch.setattr(presence, "CHECK_INTERVAL_S", 0)
    monkeypatch.setattr(presence, "_jobs_active", lambda: False)
    monkeypatch.setattr(presence, "_ever_connected", True)
    monkeypatch.setattr(presence, "_last_leave", 0.0)
    monkeypatch.setattr(presence.time, "monotonic", lambda: presence.LEAVE_GRACE_S + 5)
    asyncio.run(asyncio.wait_for(presence.watchdog(lambda: calls.append(1)), timeout=2))
    assert calls == [1]


def test_routes_are_registered():
    from test_app_routes import _paths

    assert {"/api/presence", "/api/presence/leave"} <= set(_paths())
