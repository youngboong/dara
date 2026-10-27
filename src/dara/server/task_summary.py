"""Compact per-task summary stored on the task record for the results list."""

from __future__ import annotations

import math
import re
from html import unescape

SUMMARY_SCHEMA = 2


def _finite(value, digits: int | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits) if digits is not None else number


def _label(phase_name: str) -> str:
    try:
        from dara.plot import phase_display_name

        return unescape(re.sub(r"<[^>]+>", "", phase_display_name(phase_name)))
    except Exception:
        return str(phase_name)


def _provenance_digest(phase_name: str) -> dict:
    try:
        from dara.structure_quality import reference_provenance

        info = reference_provenance(phase_name)
        return {
            "source": info.get("source", "unknown"),
            "database_id": info.get("database_id"),
            "label": info.get("label"),
        }
    except Exception:
        from dara.server.table_export import _provenance

        return {"source": "unknown", "database_id": None, "label": _provenance(phase_name)}


def _phase_digests(final) -> list[dict]:
    """Per-phase quantification, lattice and provenance of the final refinement."""
    from dara.server.api_router import _phase_refinement_details, _refined_phase_metadata
    from dara.server.table_export import lattice_with_symmetry

    details = _phase_refinement_details(final)
    metadata = _refined_phase_metadata(final)
    out = []
    for name, detail in details.items():
        number = (metadata.get(name) or {}).get("spacegroup_number")
        lattice = lattice_with_symmetry(detail.get("lattice_parameters"), number)
        out.append({
            "name": str(name),
            "label": _label(name),
            "weight_percent": _finite(detail.get("weight_percent"), 4),
            "weight_esd_percent": _finite(detail.get("weight_esd_percent"), 4),
            "spacegroup_number": number,
            "lattice": {
                key: {"value": _finite(value, 6), "esd": _finite(esd, 6)}
                for key, (value, esd) in lattice.items()
            },
            "provenance": _provenance_digest(str(name)),
        })
    return out


def compact_summary(document, runtime_s: float | None) -> dict:
    """Return a small JSON-safe digest of a completed analysis document."""
    final = getattr(document, "final_result", None)
    final_rwp = gof = None
    phases: list[str] = []
    if final is not None:
        fit = final.lst_data
        final_rwp = _finite(fit.rwp)
        rexp = _finite(getattr(fit, "rexp", None))
        gof = _finite(final_rwp / rexp) if final_rwp is not None and rexp else None
        phases = [str(name) for name in fit.phases_results]
    phase_digests: list[dict] = []
    confidence_overall = None
    if final is not None:
        try:
            phase_digests = _phase_digests(final)
        except Exception:
            phase_digests = []
        try:
            from dara.server.confidence import confidence_for_document

            block = confidence_for_document(document)
            confidence_overall = ((block or {}).get("overall") or {}).get("level")
        except Exception:
            confidence_overall = None
    return {
        "schema": SUMMARY_SCHEMA,
        "best_rwp": _finite(getattr(document, "best_rwp", None)),
        "final_rwp": final_rwp,
        "gof": gof,
        "final_phases": phases,
        "final_phase_labels": [_label(name) for name in phases],
        "n_candidates": len(getattr(document, "results", None) or []),
        "runtime_s": _finite(runtime_s),
        "phases": phase_digests,
        "confidence_overall": confidence_overall,
    }


def summarize_stored_result(uuid: str, runtime_s: float | None) -> dict:
    """Read the stored result document for ``uuid`` and summarize it."""
    from monty.serialization import MontyDecoder

    from dara.server.utils import get_result_store

    with get_result_store() as result_store:
        stored = result_store.query_one({"uuid": uuid})
    if stored is None:
        raise LookupError(f"No stored result for {uuid}")
    return compact_summary(MontyDecoder().process_decoded(stored["output"]), runtime_s)


def runtime_seconds(start, end) -> float | None:
    """Seconds between two stored datetimes, tolerating naive/aware mixtures."""
    try:
        if start is None or end is None:
            return None
        if (start.tzinfo is None) != (end.tzinfo is None):
            start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
        return (end - start).total_seconds()
    except Exception:
        return None
