"""scripts/update_dara.ps1 against a local stand-in for the GitHub API and raw file host."""

import hashlib
import json
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
UPDATER = REPO_ROOT / "scripts" / "update_dara.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell is not available")
COMMIT = "c0ffee" * 6 + "c0ff"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    def __init__(self, files):
        self.files = dict(files)
        self.corrupt = set()
        self.raw_requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, kind="application/json"):
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/repos/o/r/commits/main":
                    body = {"sha": COMMIT, "commit": {"message": "Test update\n\nDetails",
                                                      "committer": {"date": "2026-11-03T01:02:03Z"}}}
                    return self._send(json.dumps(body).encode())
                if path == f"/repos/o/r/git/trees/{COMMIT}":
                    tree = [{"path": "src", "mode": "040000", "type": "tree", "sha": "0" * 40}]
                    tree += [{"path": p, "mode": "100644", "type": "blob", "sha": blob_sha(d), "size": len(d)}
                             for p, d in owner.files.items()]
                    return self._send(json.dumps({"sha": COMMIT, "truncated": False, "tree": tree}).encode())
                prefix = f"/o/r/{COMMIT}/"
                if path.startswith(prefix):
                    rel = urllib.parse.unquote(path[len(prefix):])
                    owner.raw_requests.append(rel)
                    if rel in owner.files:
                        data = owner.files[rel]
                        return self._send(data + b"x" if rel in owner.corrupt else data, "application/octet-stream")
                self.send_response(404)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def github():
    server = FakeGitHub({
        "same.txt": b"unchanged\n",
        "changed.txt": b"new content\n",
        "dir with space/new (1).txt": b"brand new\n",
        "START.cmd": b"@echo off\r\necho new start\r\n",
    })
    yield server
    server.close()


def _local(tmp_path, files):
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _run(tmp_path, github, *extra):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(UPDATER),
         "-RepoRoot", str(tmp_path), "-Repo", "o/r", "-ApiBase", github.base, "-RawBase", github.base, *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_downloads_only_changed_files_and_removes_files_gone_upstream(tmp_path, github):
    _local(tmp_path, {
        "same.txt": b"unchanged\n",
        "changed.txt": b"old content\n",
        "START.cmd": b"@echo off\r\necho old start\r\n",
        "gone.txt": b"removed upstream\n",
        "edited_gone.txt": b"edited by the user\n",
        "untracked.txt": b"user file\n",
        ".venv/keep.txt": b"environment\n",
    })
    manifest = {"commit": "old", "files": {"gone.txt": blob_sha(b"removed upstream\n"),
                                           "edited_gone.txt": blob_sha(b"original\n")}}
    (tmp_path / ".dara-update").mkdir()
    (tmp_path / ".dara-update" / "manifest.json").write_text(json.dumps(manifest))

    result = _run(tmp_path, github, "-Yes", "-Launcher", "START.cmd")

    assert result.returncode == 20, result.stdout + result.stderr
    assert sorted(github.raw_requests) == ["START.cmd", "changed.txt", "dir with space/new (1).txt"]
    assert (tmp_path / "changed.txt").read_bytes() == b"new content\n"
    assert (tmp_path / "dir with space" / "new (1).txt").read_bytes() == b"brand new\n"
    # The running launcher is staged next to itself instead of being overwritten.
    assert (tmp_path / "START.cmd").read_bytes() == b"@echo off\r\necho old start\r\n"
    assert (tmp_path / "START.cmd.new").read_bytes() == b"@echo off\r\necho new start\r\n"
    assert not (tmp_path / "gone.txt").exists()
    assert (tmp_path / "edited_gone.txt").exists()  # edited since it was installed: kept
    assert (tmp_path / "untracked.txt").exists() and (tmp_path / ".venv" / "keep.txt").exists()
    saved = json.loads((tmp_path / ".dara-update" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["commit"] == COMMIT and set(saved["files"]) == set(github.files)
    assert not (tmp_path / ".dara-update" / "staging").exists()

    (tmp_path / "START.cmd.new").replace(tmp_path / "START.cmd")
    github.raw_requests.clear()
    again = _run(tmp_path, github, "-Yes")
    assert again.returncode == 0, again.stdout + again.stderr
    assert github.raw_requests == []


def test_check_only_reports_without_changing_anything(tmp_path, github):
    _local(tmp_path, {"same.txt": b"unchanged\n", "changed.txt": b"old content\n"})
    result = _run(tmp_path, github, "-CheckOnly")
    assert result.returncode == 10, result.stdout + result.stderr
    assert "Test update" in result.stdout
    assert (tmp_path / "changed.txt").read_bytes() == b"old content\n"
    assert github.raw_requests == []


def test_a_corrupt_download_changes_nothing(tmp_path, github):
    _local(tmp_path, {"same.txt": b"unchanged\n", "changed.txt": b"old content\n"})
    github.corrupt.add("dir with space/new (1).txt")
    result = _run(tmp_path, github, "-Yes")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (tmp_path / "changed.txt").read_bytes() == b"old content\n"
    assert not (tmp_path / "dir with space").exists()
    assert not (tmp_path / ".dara-update" / "manifest.json").exists()


def test_unreachable_server_from_start_is_a_one_line_warning(tmp_path):
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(UPDATER),
         "-RepoRoot", str(tmp_path), "-ApiBase", "http://127.0.0.1:9", "-FromStart", "-TimeoutSec", "5"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 1
    assert "starting the current version" in result.stdout


def test_updater_is_utf8_with_bom():
    assert UPDATER.read_bytes().startswith(b"\xef\xbb\xbf")


def test_cmd_launchers_swap_themselves_on_one_line():
    # cmd.exe reads a batch file while running it; replacing the file must happen on the last line read.
    update = (REPO_ROOT / "UPDATE.cmd").read_text(encoding="ascii")
    last = [line for line in update.splitlines() if line.strip()][-1]
    assert "-Launcher UPDATE.cmd" in last and "UPDATE.cmd.new" in last and last.endswith("exit /b")
    start = (REPO_ROOT / "START.cmd").read_text(encoding="ascii")
    assert 'move /y "%~dp0START.cmd.new" "%~dp0START.cmd" >nul & "%~dp0START.cmd" %* & exit /b' in start


def test_crlf_copies_of_text_files_count_as_unchanged(tmp_path, github):
    # A Windows checkout with CRLF line endings is the same file Git stores with LF.
    _local(tmp_path, {"same.txt": b"unchanged\r\n", "changed.txt": b"new content\r\n",
                      "START.cmd": b"@echo off\r\necho new start\r\n",
                      "dir with space/new (1).txt": b"brand new\n"})
    result = _run(tmp_path, github, "-Yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert github.raw_requests == []
