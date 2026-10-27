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

# ---------------------------------------------------------------------------------------------
# Zero-prerequisite START launcher (pinned uv + managed CPython)
# ---------------------------------------------------------------------------------------------

import ast
import hashlib
import io
import re
import shutil
import subprocess
import textwrap
import urllib.error
import zipfile

import pytest

REPO_ROOT = Path(__file__).parents[1]
LAUNCHER_PS1 = REPO_ROOT / "scripts" / "start_dara_web.ps1"
POWERSHELL = shutil.which("powershell.exe")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is not available")


def _load_module(name, path):
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


ensure_bgmn = _load_module("ensure_bgmn", REPO_ROOT / "scripts" / "ensure_bgmn.py")
download_bgmn_module = _load_module("download_bgmn", REPO_ROOT / "src" / "dara" / "bgmn" / "download_bgmn.py")


def _run_powershell(script, timeout=120):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def test_launcher_pins_uv_release_and_hash():
    text = LAUNCHER_PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\$UvVersion\s*=\s*'(\d+\.\d+\.\d+)'", text)
    assert re.search(r"\$UvSha256\s*=\s*'([0-9a-f]{64})'", text)
    assert "releases/download/" in text
    assert "uv-x86_64-pc-windows-msvc.zip" in text
    assert "--no-bin" in text
    assert "--no-registry" in text
    for stale in ("..\\xrd", "xrd\\Scripts", "pip install dara-xrd"):
        assert stale not in text


def test_launcher_is_utf8_bom_when_non_ascii():
    data = LAUNCHER_PS1.read_bytes()
    if any(byte >= 0x80 for byte in data):
        assert data.startswith(b"\xef\xbb\xbf")


@needs_powershell
def test_launcher_parses_in_windows_powershell():
    script = (
        "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{LAUNCHER_PS1}',[ref]$null,[ref]$e); if($e){{$e|%{{$_.Message}}; exit 1}}"
    )
    result = _run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr


@needs_powershell
def test_setup_marker_roundtrip(tmp_path):
    repo = tmp_path / "fake repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (repo / "scripts" / "constraints-py312-win.txt").write_text("numpy==1.0\n", encoding="utf-8")
    (repo / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    script = textwrap.dedent(f"""
        $ErrorActionPreference = 'Stop'
        . '{LAUNCHER_PS1}'
        $repo = '{repo}'
        $out = @()
        $out += 'before=' + (Test-SetupMarker -RepoRoot $repo)
        Write-SetupMarker -RepoRoot $repo
        $out += 'after_write=' + (Test-SetupMarker -RepoRoot $repo)
        Add-Content -LiteralPath (Join-Path $repo 'pyproject.toml') -Value '# changed'
        $out += 'after_edit=' + (Test-SetupMarker -RepoRoot $repo)
        Write-SetupMarker -RepoRoot $repo
        $out += 'rewritten=' + (Test-SetupMarker -RepoRoot $repo)
        New-Item -ItemType File -Path (Join-Path $repo '.venv\\.dara-setup-pending') | Out-Null
        $out += 'with_pending=' + (Test-SetupMarker -RepoRoot $repo)
        $out
    """)
    result = _run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.strip().split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert values == {
        "before": "False",
        "after_write": "True",
        "after_edit": "False",
        "rewritten": "True",
        "with_pending": "False",
    }


@needs_powershell
def test_offline_fallback_accepts_working_venv_with_pending_file(tmp_path):
    """A failed reinstall removes the marker; a venv that still imports DARA stays usable offline."""
    repo = tmp_path / "fake repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".venv" / "Scripts").mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (repo / "scripts" / "constraints-py312-win.txt").write_text("numpy==1.0\n", encoding="utf-8")
    script = textwrap.dedent(f"""
        $ErrorActionPreference = 'Stop'
        . '{LAUNCHER_PS1}'
        $repo = '{repo}'
        $venv = Join-Path $repo '.venv'
        $script:ProbeResult = 0
        function Test-Installation {{ param([string]$RepoRoot, [switch]$Quiet) return $script:ProbeResult }}
        function Check {{ [bool](@(Test-ExistingEnvironmentUsable -RepoRoot $repo)[-1]) }}
        $out = @()
        $out += 'no_python=' + (Check)
        [System.IO.File]::WriteAllBytes((Join-Path $venv 'Scripts\\python.exe'), [byte[]]@())
        $out += 'no_state=' + (Check)
        New-Item -ItemType File -Path (Join-Path $venv '.dara-setup-pending') | Out-Null
        $out += 'pending=' + (Check)
        $script:ProbeResult = 7
        $out += 'pending_broken_imports=' + (Check)
        $script:ProbeResult = 0
        Remove-Item -LiteralPath (Join-Path $venv '.dara-setup-pending')
        New-Item -ItemType File -Path (Join-Path $venv '.dara-setup-complete') | Out-Null
        $out += 'stale_marker=' + (Check)
        $out
    """)
    result = _run_powershell(script)
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.strip().split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert values == {
        "no_python": "False",
        "no_state": "False",
        "pending": "True",
        "pending_broken_imports": "False",
        "stale_marker": "True",
    }


def test_offline_paths_use_the_existing_environment_check():
    text = LAUNCHER_PS1.read_text(encoding="utf-8-sig")
    # No-internet branch of setup and the -Offline reinstall failure both go through the helper.
    assert text.count("Test-ExistingEnvironmentUsable") >= 3
    assert "$Offline -and $code -ne 1" in text


def _make_bgmn_files(repo, names=None):
    folder = repo / "src" / "dara" / "bgmn" / "BGMNwin"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names or ensure_bgmn._required_names():
        (folder / name).write_bytes(b"x")
    return folder


def test_ensure_bgmn_present_does_not_download(tmp_path):
    _make_bgmn_files(tmp_path)
    calls = []
    stream = io.StringIO()
    assert ensure_bgmn.ensure_bgmn(tmp_path, downloader=calls.append, stream=stream) == 0
    assert calls == []
    assert "BGMN OK" in stream.getvalue()


def test_ensure_bgmn_is_case_insensitive(tmp_path):
    _make_bgmn_files(tmp_path, [name.lower() for name in ensure_bgmn._required_names()])
    assert ensure_bgmn.missing_executables(ensure_bgmn.bgmn_folder(tmp_path)) == []


def test_ensure_bgmn_downloads_when_missing(tmp_path):
    calls = []

    def fake_downloader(target):
        calls.append(Path(target))
        _make_bgmn_files(tmp_path)

    assert ensure_bgmn.ensure_bgmn(tmp_path, downloader=fake_downloader, stream=io.StringIO()) == 0
    assert calls == [tmp_path / "src" / "dara" / "bgmn"]


def test_ensure_bgmn_reports_download_failure(tmp_path):
    def failing_downloader(_target):
        raise urllib.error.URLError("offline")

    stream = io.StringIO()
    assert ensure_bgmn.ensure_bgmn(tmp_path, downloader=failing_downloader, stream=stream) == 3
    message = stream.getvalue()
    assert "BGMN" in message
    assert "다운로드" in message


def test_ensure_bgmn_offline_never_downloads(tmp_path):
    calls = []
    stream = io.StringIO()
    assert ensure_bgmn.ensure_bgmn(tmp_path, offline=True, downloader=calls.append, stream=stream) == 3
    assert calls == []
    assert "BGMN" in stream.getvalue()


def test_ensure_bgmn_fails_when_download_leaves_files_missing(tmp_path):
    assert ensure_bgmn.ensure_bgmn(tmp_path, downloader=lambda _target: None, stream=io.StringIO()) == 3


def test_download_bgmn_is_standard_library_only():
    tree = ast.parse((REPO_ROOT / "src" / "dara" / "bgmn" / "download_bgmn.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"requests", "tqdm"}


class _FakeResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"content-length": str(len(payload))}


class _FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def open(self, url, timeout=None):
        self.urls.append(url)
        return _FakeResponse(self.payload)


def _zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_download_bgmn_rejects_zip_slip(tmp_path):
    opener = _FakeOpener(_zip_bytes({"../evil.exe": b"x", "BGMNwin/BGMN.EXE": b"x"}))
    with pytest.raises(RuntimeError):
        download_bgmn_module.download_bgmn(tmp_path / "target", url="http://example.invalid/b.zip",
                                           expected_sha256=None, opener=opener)
    assert not (tmp_path / "evil.exe").exists()
    assert not (tmp_path / "target" / "BGMNwin").exists()


def test_download_bgmn_rejects_members_outside_bgmnwin(tmp_path):
    opener = _FakeOpener(_zip_bytes({"other/BGMN.EXE": b"x"}))
    with pytest.raises(RuntimeError):
        download_bgmn_module.download_bgmn(tmp_path, url="http://example.invalid/b.zip",
                                           expected_sha256=None, opener=opener)


def test_download_bgmn_rejects_wrong_checksum(tmp_path):
    opener = _FakeOpener(_zip_bytes({"BGMNwin/BGMN.EXE": b"x"}))
    with pytest.raises(RuntimeError):
        download_bgmn_module.download_bgmn(tmp_path, url="http://example.invalid/b.zip",
                                           expected_sha256="0" * 64, opener=opener)
    assert not (tmp_path / "BGMNwin").exists()


def test_download_bgmn_extracts_good_archive_and_keeps_existing_files(tmp_path):
    names = ["BGMN.EXE", "EFLECH.EXE", "TEIL.EXE", "OUTPUT.EXE"]
    payload = _zip_bytes({f"BGMNwin/{name}": b"binary" for name in names})
    (tmp_path / "BGMNwin").mkdir()
    (tmp_path / "BGMNwin" / "mine.txt").write_text("keep me", encoding="utf-8")
    opener = _FakeOpener(payload)
    folder = download_bgmn_module.download_bgmn(
        tmp_path, url="http://example.invalid/b.zip",
        expected_sha256=hashlib.sha256(payload).hexdigest(), opener=opener)
    assert folder == tmp_path / "BGMNwin"
    assert all((folder / name).read_bytes() == b"binary" for name in names)
    assert (folder / "mine.txt").read_text(encoding="utf-8") == "keep me"
    assert opener.urls == ["http://example.invalid/b.zip"]
    assert [item.name for item in tmp_path.iterdir()] == ["BGMNwin"]  # no temp files left behind
