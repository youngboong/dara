"""Stop the server once every browser window has closed.

Each open DARA page sends a heartbeat with its own client id, so several windows
or tabs can share one server. Idle shutdown is opt-in (``DARA_IDLE_SHUTDOWN``):
the Windows START launcher enables it, a plain ``dara server`` does not.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Hidden tabs may throttle timers to about once per minute, so a missed
# heartbeat alone must not count as "closed" too quickly.
CLIENT_TIMEOUT_S = 150.0
# Grace after the last window closed explicitly (covers page reloads).
LEAVE_GRACE_S = 20.0
# Time allowed for the first window to connect after startup.
STARTUP_GRACE_S = 600.0
CHECK_INTERVAL_S = 5.0
ACTIVE_JOB_STATUSES = ("READY", "RUNNING", "CANCELLING")

_lock = threading.Lock()
_clients: dict[str, float] = {}
_last_leave: float | None = None
_started_at = time.monotonic()
_ever_connected = False


class Presence(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)


@router.post("/presence")
async def heartbeat(presence: Presence):
    """Record that a browser window is open."""
    global _ever_connected  # noqa: PLW0603
    with _lock:
        _clients[presence.client_id] = time.monotonic()
        _ever_connected = True
        return {"clients": len(_clients), "idle_shutdown": idle_shutdown_enabled()}


@router.post("/presence/leave")
async def leave(presence: Presence):
    """Forget a window that is closing (sent with navigator.sendBeacon)."""
    global _last_leave  # noqa: PLW0603
    with _lock:
        _clients.pop(presence.client_id, None)
        _last_leave = time.monotonic()
        return {"clients": len(_clients)}


def idle_shutdown_enabled() -> bool:
    return os.environ.get("DARA_IDLE_SHUTDOWN", "").strip().lower() in {"1", "on", "true", "yes"}


def active_clients(now: float | None = None) -> int:
    now = time.monotonic() if now is None else now
    with _lock:
        for client_id, seen in list(_clients.items()):
            if now - seen > CLIENT_TIMEOUT_S:
                del _clients[client_id]
        return len(_clients)


def should_shut_down(now: float, *, jobs_active: bool) -> bool:
    """Decide whether no window has been open long enough to stop the server."""
    if jobs_active or active_clients(now):
        return False
    with _lock:
        if not _ever_connected:
            return now - _started_at > STARTUP_GRACE_S
        last_leave = _last_leave
        newest = max(_clients.values(), default=None)
    # Every window either left explicitly or stopped sending heartbeats.
    if last_leave is not None and now - last_leave >= LEAVE_GRACE_S:
        return True
    return newest is None and last_leave is None


def _jobs_active() -> bool:
    # montydb must only be opened from the event-loop thread (concurrent opens fail).
    try:
        from dara.server.utils import get_worker_store

        with get_worker_store() as store:
            return store.count({"status": {"$in": list(ACTIVE_JOB_STATUSES)}}) > 0
    except Exception:
        logger.debug("Could not read job states; keeping the server alive", exc_info=True)
        return True


async def watchdog(request_exit: Callable[[], None]) -> None:
    """Call ``request_exit`` once no window is open and no analysis is queued or running."""
    import asyncio

    while True:
        await asyncio.sleep(CHECK_INTERVAL_S)
        now = time.monotonic()
        # Only consult the job store when no window is open.
        if active_clients(now) or not should_shut_down(now, jobs_active=False):
            continue
        if should_shut_down(now, jobs_active=_jobs_active()):
            print("All DARA windows are closed and no analysis is running. Stopping the server.", flush=True)
            request_exit()
            return
