"""Download a refinement as a starting model for Profex/BGMN, TOPAS, GSAS-II or HighScore."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from dara.server import review_router

logger = logging.getLogger("dara.server.export")

router = APIRouter(prefix="/api")

EXPORT_FORMATS = ("profex", "topas", "gsas2", "highscore")


def _reference_cifs(document, index: int | None) -> list:
    from dara.server.api_router import _flatten_cifs, _matching_candidate_cifs

    if index is not None:
        return _flatten_cifs(document.results[index][0])
    cifs = list(getattr(document, "input_cifs", None) or [])
    return cifs + _matching_candidate_cifs(document, document.final_result)


@router.get("/task/{task_id}/export/{fmt}")
async def export_task(task_id: int, fmt: str, idx: int | None = Query(None, ge=0)):
    """ZIP archive with the final result (default) or candidate ``idx`` for another program."""
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(404, f"Unsupported export format: {fmt}")
    task, document = review_router._load_completed_task(task_id)
    if idx is None:
        refinement = document.final_result
        if refinement is None:
            raise HTTPException(404, "Final refinement not found")
    else:
        if idx >= len(document.results or []):
            raise HTTPException(404, "Search result index out of range")
        refinement = document.results[idx][1]

    from dara.export.bundles import build_bundle, zip_directory

    try:
        with tempfile.TemporaryDirectory(prefix="dara_export_") as temp:
            out_dir = Path(temp) / "bundle"
            out_dir.mkdir()
            build_bundle(fmt, refinement, document, out_dir, _reference_cifs(document, idx))
            content = zip_directory(out_dir)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Export to %s failed", fmt, exc_info=True)
        raise HTTPException(500, f"Export failed: {exc}") from exc
    suffix = "" if idx is None else f"_candidate_{idx + 1}"
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="dara_task_{task_id}_{fmt}{suffix}.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )
