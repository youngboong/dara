import importlib
import logging
import multiprocessing
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dara.server.api_router import router
from dara.server.comparison_router import router as comparison_router
from dara.server.review_router import router as review_router
from dara.server.selected_refinement_router import router as selected_refinement_router
from dara.server.task_control_router import router as task_control_router
from dara.server.instrument_router import router as instrument_router
from dara.server.candidate_comparison_router import router as candidate_comparison_router
from dara.server.setting import get_dara_server_settings
from dara.server.worker import worker_process

try:
    multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass

_worker_process = None


def _must_revalidate_frontend(path: str, content_type: str) -> bool:
    """Return whether a Gatsby entry/manifest can reference changing hashes."""
    normalized = path.replace("\\", "/").lstrip("/")
    return (
        content_type.startswith("text/html")
        or normalized == "chunk-map.json"
        or normalized.startswith("page-data/")
    )


class FrontendStaticFiles(StaticFiles):
    """Serve hashed assets normally while preventing stale Gatsby entrypoints."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if _must_revalidate_frontend(path, content_type):
            # A cached HTML/page-data file can point at JavaScript chunks from a
            # previous build and leave an already-open browser tab completely
            # blank after deployment.
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


@asynccontextmanager
async def launch_worker_process(app: FastAPI):
    """Context manager to launch the worker process."""
    global _worker_process  # noqa: PLW0603
    stop_event = multiprocessing.Event()
    _worker_process = multiprocessing.Process(target=worker_process, daemon=True,
        args=(stop_event, get_dara_server_settings().model_dump()))
    _worker_process.start()
    try:
        yield
    finally:
        try:
            from dara.server import peak_significance

            peak_significance.shutdown()  # abort a running trace-hint simulation
        except Exception:
            logging.getLogger("dara.server.app").debug("Hint worker shutdown failed", exc_info=True)
        # A console Ctrl+C also reaches the worker process; Event.set() then blocks forever
        # waiting for the dead process to wake up, so it must not run on the shutdown thread.
        setter = threading.Thread(target=stop_event.set, daemon=True)
        setter.start()
        setter.join(timeout=2)
        _worker_process.join(timeout=1 if setter.is_alive() else 15)
        if _worker_process.is_alive():
            print("Terminating worker process...")
            _worker_process.terminate()


app = FastAPI(lifespan=launch_worker_process)

# Mount the frontend directory to the root path
frontend_dir = Path(__file__).parent / "ui" / "public"
app.include_router(router)
app.include_router(comparison_router)
app.include_router(review_router)
app.include_router(selected_refinement_router)
app.include_router(task_control_router)
app.include_router(instrument_router)
app.include_router(candidate_comparison_router)

_OPTIONAL_ROUTERS = (
    "dara.server.export_router",             # W2
    "dara.server.reference_library_router",  # W1
    "dara.server.mp_router",                 # W1
    "dara.server.series_router",             # W3
)
for _module_name in _OPTIONAL_ROUTERS:
    try:
        app.include_router(importlib.import_module(_module_name).router)
    except ModuleNotFoundError as exc:
        if exc.name != _module_name:
            raise
        logging.getLogger("dara.server.app").warning(
            "Optional router %s is not available", _module_name
        )

app.mount(
    "", FrontendStaticFiles(directory=frontend_dir.as_posix(), html=True), name="frontend"
)


def launch_app():
    """Main function to run the FastAPI application."""
    import uvicorn

    setting = get_dara_server_settings()
    uvicorn.run(
        app,
        host=setting.host,
        port=setting.port,
        workers=1,
        timeout_graceful_shutdown=1,
    )
