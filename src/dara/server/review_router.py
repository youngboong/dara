"""Human review and self-contained, printable result summaries.

Review metadata belongs to the task record, not the immutable fit output: adopting
a chemically plausible candidate never changes the minimum-Rwp result.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from html import escape
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from monty.json import MontyDecoder
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from dara.server.utils import get_result_store, get_worker_store

router = APIRouter(prefix="/api")


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: Literal["best"] | StrictInt | None = None
    notes: str = Field(default="", max_length=10000)


def _load_completed_task(task_id: int):
    with get_worker_store() as store:
        task = store.query_one({"index": task_id})
    if task is None:
        raise HTTPException(404, "Task not found")
    if task["status"] != "COMPLETED":
        raise HTTPException(409, "Only completed tasks can be reviewed or reported")
    with get_result_store() as store:
        stored = store.query_one({"uuid": task["uuid"]})
    if stored is None:
        raise HTTPException(404, "Task result not found")
    return task, MontyDecoder().process_decoded(stored["output"])


def _review_metadata(task: dict) -> dict:
    review = task.get("review") or {}
    return {
        "selection": review.get("selection"),
        "notes": review.get("notes", ""),
        "updated_at": review.get("updated_at"),
    }


def _select_refinement(document, selection):
    if selection is None or selection == "best":
        refinement = document.final_result
        if refinement is None:
            raise HTTPException(404, "Final refinement not found")
        return refinement
    if isinstance(selection, bool) or not isinstance(selection, int):
        raise HTTPException(422, "Selection must be 'best' or a candidate source index")
    if selection < 0 or selection >= len(document.results or []):
        raise HTTPException(404, "Search result index out of range")
    return document.results[selection][1]


@router.get("/task/{task_id}/review")
async def get_review(task_id: int):
    task, _ = _load_completed_task(task_id)
    return _review_metadata(task)


@router.put("/task/{task_id}/review")
async def save_review(task_id: int, update: ReviewUpdate):
    task, document = _load_completed_task(task_id)
    if update.selection is not None:
        _select_refinement(document, update.selection)
    review = {
        "selection": update.selection,
        "notes": update.notes,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Reload immediately before writing, preserving any unrelated task metadata.
    with get_worker_store() as store:
        current = store.query_one({"index": task_id})
        if current is None or current.get("uuid") != task.get("uuid"):
            raise HTTPException(404, "Task no longer exists")
        current["review"] = review
        store.update(current)
    return review


def _number(value, digits=5) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{numeric:.{digits}g}" if math.isfinite(numeric) else "—"


def _parameter(value: dict) -> str:
    raw = value.get("value")
    uncertainty = value.get("esd")
    text = _number(raw, 8)
    if uncertainty is not None and math.isfinite(float(uncertainty)):
        text += f" ± {_number(abs(float(uncertainty)), 2)}"
    return text + ("°" if value.get("unit") == "deg" else f" {value.get('unit', '')}")


def _table(headers: list[str], rows: list[list[str]]) -> str:
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{escape(header)}</th>" for header in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
        + "</tbody></table>"
    )


def _provenance(phase_name: str) -> str:
    try:
        from dara.structure_quality import reference_provenance

        info = reference_provenance(phase_name)
        label = info.get("label")
        if label:
            url = info.get("url")
            if url:
                return f'<a href="{escape(str(url), quote=True)}">{escape(str(label))}</a>'
            return escape(str(label))
    except Exception:  # structure_quality unavailable: use the plain name parser
        pass
    cod = re.search(r"cod[_-]?(\d+)", phase_name, re.I)
    mp = re.search(r"mp[-_](\d+)", phase_name, re.I)
    if cod:
        record = cod.group(1)
        return f'<a href="https://www.crystallography.net/cod/{record}.html">COD {record}</a>'
    if mp:
        record = mp.group(1)
        return f'<a href="https://materialsproject.org/materials/mp-{record}">Materials Project mp-{record}</a>'
    return "User-provided / source not recorded"


def _fit_svg(refinement, excluded_ranges=None) -> str:
    """Render full-resolution vectors, retaining narrow peaks and residuals."""
    data = refinement.plot_data
    x = np.asarray(data.x, dtype=float)
    observed = np.asarray(data.y_obs, dtype=float)
    calculated = np.asarray(data.y_calc, dtype=float)
    background = np.asarray(data.y_bkg, dtype=float)
    finite_x = x[np.isfinite(x)]
    if finite_x.size < 2:
        return "<p>Fit plot unavailable.</p>"
    low, high = float(finite_x.min()), float(finite_x.max())
    if high <= low:
        return "<p>Fit plot unavailable.</p>"
    values = np.concatenate((observed, calculated, background))
    values = values[np.isfinite(values)]
    y_low = min(0.0, float(values.min())) if values.size else 0.0
    y_high = max(float(values.max()), y_low + 1) if values.size else 1.0
    residual = observed - calculated
    finite_residual = residual[np.isfinite(residual)]
    r_max = max(float(np.max(np.abs(finite_residual))), 1.0) if finite_residual.size else 1.0
    left, right, top, bottom, r_top, r_bottom = 82, 1060, 50, 345, 410, 510
    scale_x = lambda value: left + (value - low) / (high - low) * (right - left)
    scale_y = lambda value: bottom - (value - y_low) / (y_high - y_low) * (bottom - top)
    scale_r = lambda value: (r_top + r_bottom) / 2 - value / r_max * (r_bottom - r_top) / 2
    steps = np.diff(finite_x)
    positive_steps = steps[steps > 0]
    gap_limit = float(np.median(positive_steps)) * 3 if positive_steps.size else float("inf")

    def path(values, scale):
        points, connected, previous_x = [], False, None
        for angle, value in zip(x, values):
            if not np.isfinite(angle) or not np.isfinite(value):
                connected = False
                continue
            if previous_x is not None and angle - previous_x > gap_limit:
                connected = False
            points.append(f"{'L' if connected else 'M'}{scale_x(angle):.2f},{scale(value):.2f}")
            connected, previous_x = True, angle
        return " ".join(points)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 565" role="img" aria-label="Observed and calculated diffraction patterns with separate difference panel">', '<rect width="1100" height="565" fill="white"/>']
    for label, color, label_x in (("Observed", "#111827", 82), ("Calculated", "#dc2626", 250), ("Background", "#94a3b8", 430), ("Difference (Obs − calc)", "#2563eb", 625)):
        out.append(f'<line x1="{label_x}" x2="{label_x + 25}" y1="20" y2="20" stroke="{color}" stroke-width="2"/><text x="{label_x + 32}" y="25" font-size="14">{label}</text>')
    for y0, y1 in ((top, bottom), (r_top, r_bottom)):
        out.append(f'<rect x="{left}" y="{y0}" width="{right-left}" height="{y1-y0}" fill="none" stroke="#64748b"/>')
        for interval in excluded_ranges or []:
            try:
                start, end = map(float, interval)
            except (TypeError, ValueError):
                continue
            if not np.isfinite([start, end]).all() or end <= low or start >= high:
                continue
            x0, x1 = scale_x(max(low, start)), scale_x(min(high, end))
            if x1 > x0:
                out.append(f'<rect x="{x0:.2f}" y="{y0}" width="{x1-x0:.2f}" height="{y1-y0}" fill="#e2e8f0"><title>Excluded from refinement</title></rect>')
    for y_values, scale, color in ((observed, scale_y, "#111827"), (calculated, scale_y, "#dc2626"), (background, scale_y, "#94a3b8"), (residual, scale_r, "#2563eb")):
        out.append(f'<path d="{path(y_values, scale)}" fill="none" stroke="{color}" stroke-width="1"/>')
    for value in np.linspace(low, high, 7):
        px = scale_x(value)
        for y_end in (bottom, r_bottom):
            out.append(f'<path d="M{px:.2f},{y_end}v6" stroke="#64748b"/><text x="{px:.2f}" y="{y_end+23}" text-anchor="middle" font-size="12">{value:.2f}</text>')
    for value in np.linspace(y_low, y_high, 5):
        out.append(f'<text x="{left-8}" y="{scale_y(value)+4:.2f}" text-anchor="end" font-size="12">{value:.4g}</text>')
    for value in (-r_max, 0, r_max):
        out.append(f'<text x="{left-8}" y="{scale_r(value)+4:.2f}" text-anchor="end" font-size="12">{value:.4g}</text>')
    out.extend((f'<line x1="{left}" x2="{right}" y1="{scale_r(0)}" y2="{scale_r(0)}" stroke="#94a3b8" stroke-dasharray="3 3"/>', '<text x="570" y="390" text-anchor="middle" font-size="14">2θ (°)</text>', '<text x="570" y="553" text-anchor="middle" font-size="14">2θ (°)</text>', '<text x="18" y="200" text-anchor="middle" transform="rotate(-90 18 200)" font-size="14">Intensity</text>', '<text x="18" y="460" text-anchor="middle" transform="rotate(-90 18 460)" font-size="14">Obs − calc</text>', '</svg>'))
    return "".join(out)


def _public_settings(value):
    """Never put API credentials into a portable report."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if re.search(r"api.?key|token|password|secret", str(key), re.I) else _public_settings(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_public_settings(item) for item in value]
    return value


def _render_report(task_id: int, task: dict, document, selection, review: dict) -> str:
    # Local import lets the application register this router without import cycles.
    from dara.server.report.builder import build_report_html

    return build_report_html(task_id, task, document, selection, review)


def _report_selection(idx, best: bool, review: dict):
    if idx is not None and best:
        raise HTTPException(422, "Choose either idx or best, not both")
    if idx is not None:
        return idx
    if best:
        return "best"
    return review["selection"] if review["selection"] is not None else "best"


@router.get("/task/{task_id}/report")
async def download_report(task_id: int, idx: int | None = Query(None, ge=0), best: bool = False):
    if idx is not None and best:
        raise HTTPException(422, "Choose either idx or best, not both")
    task, document = _load_completed_task(task_id)
    review = _review_metadata(task)
    selection = _report_selection(idx, best, review)
    html = _render_report(task_id, task, document, selection, review)
    suffix = "best" if selection == "best" else f"candidate_{selection + 1}"
    return Response(
        html,
        media_type="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="dara_task_{task_id}_{suffix}_report.html"',
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
            "X-Content-Type-Options": "nosniff",
        },
    )


_FIGURE_MEDIA = {"png": "image/png", "svg": "image/svg+xml"}


@router.get("/task/{task_id}/figure.{fmt}")
async def download_figure(
    task_id: int,
    fmt: str,
    idx: int | None = Query(None, ge=0),
    best: bool = False,
    dpi: int = 600,
    width_mm: float = 170,
):
    """Publication-style fit figure (PNG or SVG) of the selected refinement."""
    if fmt not in _FIGURE_MEDIA:
        raise HTTPException(404, "Unsupported figure format; use png or svg")
    if idx is not None and best:
        raise HTTPException(422, "Choose either idx or best, not both")
    task, document = _load_completed_task(task_id)
    review = _review_metadata(task)
    selection = _report_selection(idx, best, review)
    refinement = _select_refinement(document, selection)
    from dara.server.api_router import _counts_like
    from dara.server.report.figures import fit_figure

    search_settings = getattr(document, "search_kwargs", None) or {}
    excluded = (search_settings.get("refinement_params") or {}).get("excluded_ranges")
    try:
        content = fit_figure(
            refinement, fmt=fmt, dpi=dpi, width_mm=width_mm, excluded_ranges=excluded,
            counts_like=_counts_like(document),
        )
    except Exception as exc:
        raise HTTPException(500, f"Figure rendering failed: {exc}") from exc
    suffix = "best" if selection == "best" else f"candidate_{selection + 1}"
    return Response(
        content,
        media_type=_FIGURE_MEDIA[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="dara_task_{task_id}_{suffix}_figure.{fmt}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
