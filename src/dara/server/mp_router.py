"""Materials Project API key status for the web UI."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

router = APIRouter(prefix="/api")

CACHE_SECONDS = 600
FAILURE_CACHE_SECONDS = 30  # undecided results (network trouble) are retried soon
PROBE_TIMEOUT_SECONDS = 15
_AUTH_MARKERS = ("401", "403", "unauthorized", "unauthorised", "forbidden", "invalid api key", "api key")

# sha256(key) -> (monotonic time, lifetime in seconds, payload)
_cache: dict[str, tuple[float, float, dict]] = {}


def _probe_mp_key() -> None:
    """Make one small request with the configured key; raises on failure."""
    from mp_api.client import MPRester

    with MPRester(os.environ["MP_API_KEY"]) as client:
        client.materials.summary.search(material_ids=["mp-149"], fields=["material_id"])


def _clean(message: str, key: str) -> str:
    text = str(message).replace(key, "***") if key else str(message)
    text = " ".join(text.split())
    return text[:200]


def _classify(exc: BaseException, key: str) -> dict:
    text = f"{type(exc).__name__} {exc}".lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return {"valid": False, "error": "The Materials Project rejected this API key."}
    return {"valid": None, "error": "Could not reach the Materials Project: " + _clean(exc, key)}


@router.get("/mp-key/status")
async def mp_key_status(refresh: bool = Query(False)):
    key = os.environ.get("MP_API_KEY", "")
    if not key:
        return {"configured": False, "valid": None, "checked_at": None, "error": None}

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cached = _cache.get(digest)
    if cached and not refresh and time.monotonic() - cached[0] < cached[1]:
        return cached[2]

    try:
        await asyncio.wait_for(run_in_threadpool(_probe_mp_key), timeout=PROBE_TIMEOUT_SECONDS)
        outcome = {"valid": True, "error": None}
    except asyncio.TimeoutError:
        outcome = {"valid": None, "error": "The Materials Project did not answer within 15 seconds."}
    except Exception as exc:  # noqa: BLE001 - mapped to a status, never raised to the client
        outcome = _classify(exc, key)

    payload = {
        "configured": True,
        "valid": outcome["valid"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": outcome["error"],
    }
    lifetime = CACHE_SECONDS if payload["valid"] is not None else FAILURE_CACHE_SECONDS
    _cache[digest] = (time.monotonic(), lifetime, payload)
    return payload
