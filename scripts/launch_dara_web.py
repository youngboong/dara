"""Open a startup page before importing DARA's scientific dependencies."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from windows_ca_bundle import apply_ca_bundle  # noqa: E402


PAGE = """<!doctype html><html><meta charset="utf-8"><title>Starting DARA</title>
<style>body{font:18px system-ui;max-width:850px;margin:70px auto;padding:24px;
background:#f5f8fc;color:#172b4d}pre{white-space:pre-wrap;overflow-wrap:anywhere;
background:white;padding:20px;border:1px solid #dce3ed}h1{color:#1763b0}</style>
<h1 id="title">Starting DARA...</h1>
<p id="message">Loading the analysis libraries. This page will open DARA automatically.</p>
<pre id="log"></pre><script>
async function update(){try{const r=await fetch('/status',{cache:'no-store'});
const s=await r.json();document.getElementById('log').textContent=s.log;
if(s.ready){location.replace(s.url);return;}
if(s.error){document.getElementById('title').textContent='DARA could not start';
document.getElementById('message').textContent=s.error;return;}
}catch(e){document.getElementById('message').textContent=
'START is no longer responding. Check the START window for details.';}
setTimeout(update,1000);}update();</script></html>"""


def make_handler(state, log_path):
    """Serve only startup status, on a separate loopback-only port."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/status":
                try:
                    with log_path.open("rb") as stream:
                        stream.seek(max(0, log_path.stat().st_size - 12000))
                        log = stream.read().decode("utf-8", errors="replace")
                except OSError:
                    log = "Waiting for the server process..."
                body = json.dumps({**state, "log": log}).encode()
                content_type = "application/json"
            elif self.path == "/":
                body = PAGE.encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def open_browser(url):
    """Try the default handler, then installed Windows browsers."""
    try:
        if webbrowser.open(url):
            return
    except OSError:
        pass
    for root in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            candidate = Path(os.environ.get(root, "")) / relative
            if candidate.is_file():
                subprocess.Popen([str(candidate), url])
                return
    raise RuntimeError(f"Could not open a browser automatically. Check your default browser settings. URL: {url}")


def server_ready(url):
    # Local requests must not go through a corporate HTTP proxy.
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
            return response.status == 200
    except (URLError, OSError):
        return False


def run_server():
    # Keep all heavy imports after the startup page is available. Also select
    # this checkout even when the environment contains the upstream wheel.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    print("Loading DARA and scientific libraries...", flush=True)
    from dara.cli import main

    print("Starting DARA web server...", flush=True)
    sys.argv = ["dara", "server"]
    main()


def launch(port=8898, timeout=180, no_browser=False, command=None):
    """Keep startup failures visible and supervise the owned server process."""
    url = f"http://127.0.0.1:{port}/"
    if server_ready(url):
        print(f"DARA is already running. Opening another window: {url}", flush=True)
        if not no_browser:
            open_browser(url)
        return 0
    state = {"url": url, "ready": False, "error": ""}
    log_dir = Path(__file__).resolve().parents[1] / "artifacts" / "launcher"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"server-{time.time_ns()}.log"
    status_server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, log_path))
    threading.Thread(target=status_server.serve_forever, daemon=True).start()
    status_url = f"http://127.0.0.1:{status_server.server_port}/"
    process = None
    try:
        print(f"Opening DARA startup page: {status_url}", flush=True)
        if not no_browser:
            open_browser(status_url)
        if server_ready(url):
            state["ready"] = True
        else:
            environment = {**os.environ, "DARA_SERVER_HOST": "127.0.0.1", "DARA_SERVER_PORT": str(port),
                           "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                           # Stop the server once every DARA browser window is closed.
                           "DARA_IDLE_SHUTDOWN": os.environ.get("DARA_IDLE_SHUTDOWN", "1")}
            # HTTPS-inspecting networks re-sign traffic with a root only Windows trusts.
            bundle = apply_ca_bundle(environment, Path(__file__).resolve().parents[1])
            if bundle is not None:
                print(f"Using CA certificates from Windows and certifi: {bundle}", flush=True)
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    command or [sys.executable, "-u", str(Path(__file__).resolve()), "--server"],
                    stdout=log, stderr=subprocess.STDOUT, env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            deadline = time.monotonic() + timeout
            last_progress = 0
            while not server_ready(url):
                if process.poll() is not None:
                    raise RuntimeError(f"Server exited with code {process.returncode}. See the startup page and log: {log_path}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Server startup timed out. See the startup page and log: {log_path}")
                if time.monotonic() - last_progress >= 5:
                    print(f"Loading DARA... progress is shown in your browser. Log: {log_path}", flush=True)
                    last_progress = time.monotonic()
                time.sleep(0.3)
            state["ready"] = True
        print(f"DARA is ready: {url}\nYou can open several DARA windows; run START again for another one.\n"
              "DARA stops automatically when every DARA browser window is closed and no analysis is running.\n"
              "Press Ctrl+C here to stop it immediately.", flush=True)
        # Remain available for the browser's polling/redirect and own server's
        # lifetime. No detached dara.exe black console is created.
        while process is not None and process.poll() is None:
            time.sleep(0.5)
        if process is None:
            time.sleep(5)
        return 0 if process is None else process.returncode
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        state["error"] = str(error)
        print(f"DARA startup failed: {error}", file=sys.stderr, flush=True)
        # Leave the failure page available while the user reads it.
        try:
            input("Press Enter after reviewing the error to close START...")
        except (EOFError, KeyboardInterrupt):
            pass
        return 1
    finally:
        status_server.shutdown()
        status_server.server_close()
        if process is not None and process.poll() is None:
            # Stop only children of the server launched by this invocation.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8898)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.server:
        run_server()
    else:
        sys.exit(launch(args.port, args.timeout, args.no_browser))
