"""Regression checks for startup progress, proxy handling and browser fallback."""

import importlib.util
import json
from pathlib import Path
import socket
import sys
import threading
from urllib.request import ProxyHandler, build_opener

spec = importlib.util.spec_from_file_location("web_launcher", Path(__file__).parents[1] / "scripts" / "launch_dara_web.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_status_page_shows_progress_and_readiness(tmp_path, monkeypatch):
    log = tmp_path / "startup.log"
    log.write_text("Loading libraries...", encoding="utf-8")
    state = {"url": "http://127.0.0.1:8898/", "ready": False, "error": ""}
    server = launcher.ThreadingHTTPServer(("127.0.0.1", 0), launcher.make_handler(state, log))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    opener = build_opener(ProxyHandler({}))
    try:
        # A corporate proxy must not hide a healthy localhost server.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        monkeypatch.setenv("no_proxy", "")
        assert launcher.server_ready(url)
        with opener.open(url) as response:
            page = response.read().decode()
        assert "location.replace(s.url)" in page
        with opener.open(url + "status") as response:
            status = json.load(response)
        assert not status["ready"]
        assert "Loading libraries" in status["log"]
        state.update(ready=True)
        with opener.open(url + "status") as response:
            assert json.load(response)["ready"]
    finally:
        server.shutdown()
        server.server_close()


def test_browser_failure_tries_installed_edge(tmp_path, monkeypatch):
    edge = tmp_path / "Microsoft/Edge/Application/msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.touch()
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda _url: False)
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args: calls.append(args))
    launcher.open_browser("http://127.0.0.1:8898/")
    assert calls == [[str(edge), "http://127.0.0.1:8898/"]]


def test_crashed_server_is_reported_instead_of_waiting_silently(monkeypatch, capsys):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    opened = []

    def browser_opener(url):
        with build_opener(ProxyHandler({})).open(url) as response:
            opened.append(response.read().decode())

    monkeypatch.setattr(launcher, "open_browser", browser_opener)
    result = launcher.launch(port=port, timeout=5,
                             command=[sys.executable, "-c", "print('test startup failure', flush=True); raise SystemExit(9)"])
    assert result == 1
    assert len(opened) == 1
    assert "Starting DARA..." in opened[0]
    assert "Server exited with code 9" in capsys.readouterr().err
