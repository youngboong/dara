import os

import pytest

psutil = pytest.importorskip("psutil")

from dara.server import worker


def test_server_gone_tracks_the_parent_process(monkeypatch):
    me = psutil.Process(os.getpid())
    monkeypatch.setattr(worker, "_SERVER", (me.pid, me.create_time()))
    assert worker._server_gone() is False
    # same pid with another creation time: the id was reused by a different process
    monkeypatch.setattr(worker, "_SERVER", (me.pid, me.create_time() - 100))
    assert worker._server_gone() is True


def test_server_gone_when_the_parent_no_longer_exists(monkeypatch):
    def missing(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", missing)
    monkeypatch.setattr(worker, "_SERVER", (123456, 1.0))
    assert worker._server_gone() is True


def test_unwatched_worker_never_reports_the_server_gone(monkeypatch):
    monkeypatch.setattr(worker, "_SERVER", None)
    assert worker._server_gone() is False
    assert worker._should_stop(None) is False
