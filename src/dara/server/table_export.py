"""Flat, spreadsheet-friendly (CSV) export of refined phases.

One row is written per (candidate, phase). Symmetry-constrained lattice values
are filled in from the space group so that every row carries a-gamma; filled
values have a blank e.s.d. because they were not refined independently.
"""

from __future__ import annotations

import csv
import re
from html import unescape
from io import StringIO

TABLE_COLUMNS: list[str] = [
    "task_id", "task_name", "candidate", "source_index", "adopted", "phase",
    "phase_label", "spacegroup_symbol", "spacegroup_number", "weight_percent",
    "weight_esd_percent", "a_angstrom", "a_esd", "b_angstrom", "b_esd",
    "c_angstrom", "c_esd", "alpha_deg", "alpha_esd", "beta_deg", "beta_esd",
    "gamma_deg", "gamma_esd", "crystallite_size_nm", "crystallite_size_esd_nm",
    "rwp", "rexp", "gof", "chi_squared", "durbin_watson", "missing_peaks",
    "missing_peaks_significant", "missing_peaks_minor", "extra_peaks", "two_theta_min",
    "two_theta_max", "cautions", "provenance", "verdict", "overall_confidence",
    "mass_balance_deviation",
]
MULTI_TASK_EXTRA_COLUMNS = ["user", "submitted_on"]
RHOMBOHEDRAL_GROUPS = {146, 148, 155, 160, 161, 166, 167}


def _format(value) -> str:
    if value is None or isinstance(value, bool):
        return "" if value is None else str(value)
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return ""
        return f"{number:.10g}"
    text = str(value)
    # Keep user-controlled text (file names) from being run as a spreadsheet formula.
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _provenance(phase_name: str) -> str:
    try:
        from dara.structure_quality import reference_provenance

        label = reference_provenance(phase_name).get("label")
        if label:
            return str(label)
    except Exception:  # W1 module missing or failing: fall back to the plain parser
        pass
    cod = re.search(r"cod[_-]?(\d+)", phase_name, re.I)
    mp = re.search(r"mp[-_](\d+)", phase_name, re.I)
    if cod:
        return f"COD {cod.group(1)}"
    if mp:
        return f"MP mp-{mp.group(1)}"
    return "user-provided"


def _phase_label(phase_name: str) -> str:
    try:
        from dara.plot import phase_display_name
        return unescape(re.sub(r"<[^>]+>", "", phase_display_name(phase_name)))
    except Exception:
        return phase_name


def _parameter(parameters: dict, key: str) -> tuple[float | None, float | None]:
    entry = (parameters or {}).get(key)
    if not isinstance(entry, dict):
        return None, None
    return entry.get("value"), entry.get("esd")


def lattice_with_symmetry(parameters: dict, spacegroup_number) -> dict[str, tuple]:
    """Return ``{a..gamma: (value, esd)}`` with symmetry-implied values filled in."""
    parameters = parameters or {}
    values = {key: _parameter(parameters, key) for key in ("a", "b", "c", "alpha", "beta", "gamma")}
    try:
        number = int(spacegroup_number)
    except (TypeError, ValueError):
        number = 0
    blank = (None, None)

    def first(*keys):
        return next((values[key] for key in keys if values[key][0] is not None), blank)

    if number in RHOMBOHEDRAL_GROUPS and values["alpha"][0] is not None:
        a = first("a", "b", "c")
        alpha = values["alpha"]
        values.update(a=a, b=(a[0], None), c=(a[0], None))
        values.update(beta=(alpha[0], None), gamma=(alpha[0], None))
    elif 195 <= number <= 230:
        a = first("a", "b", "c")
        values.update(a=a, b=(a[0], None), c=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 143 <= number <= 194:
        a = first("a", "b")
        values.update(a=a, b=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(120.0, None))
    elif 75 <= number <= 142:
        a = first("a", "b")
        values.update(a=a, b=(a[0], None))
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 16 <= number <= 74:
        values.update(alpha=(90.0, None), beta=(90.0, None), gamma=(90.0, None))
    elif 3 <= number <= 15:
        values.update(alpha=(90.0, None), gamma=(90.0, None))
    return values


def _is_adopted(selection, candidate_kind: str, source_index) -> bool:
    if selection is None:
        return False
    if candidate_kind == "final":
        return selection == "best"
    return (
        not isinstance(selection, bool)
        and isinstance(selection, int)
        and selection == source_index
    )


def _rows_for_result(
    task_id,
    task_name,
    candidate: str,
    source_index,
    adopted: bool,
    result: dict,
) -> list[dict]:
    quality = result.get("fit_quality") or {}
    details = result.get("phase_details") or {}
    metadata = result.get("phase_metadata") or {}
    diagnostics = result.get("peak_diagnostics") or {}
    counts = diagnostics.get("counts") or {}
    by_class = diagnostics.get("counts_by_class")
    by_class = by_class if isinstance(by_class, dict) else {}
    cautions = ";".join(c.get("code", "") for c in result.get("cautions") or [])
    confidence = result.get("confidence") if isinstance(result.get("confidence"), dict) else {}
    overall_level = ((confidence.get("overall") or {}).get("level")) or ""
    phase_verdicts = confidence.get("phases") or {}
    mass_balance = result.get("mass_balance") if isinstance(result.get("mass_balance"), dict) else {}
    mass_balance_deviation = mass_balance.get("max_deviation")
    phases = list(details) or list(result.get("highlighted_phases") or result.get("phases") or [])
    rows = []
    for phase in phases:
        if not isinstance(phase, str):
            continue
        phase_details = details.get(phase) or {}
        phase_meta = metadata.get(phase) or {}
        number = phase_meta.get("spacegroup_number")
        lattice = lattice_with_symmetry(phase_details.get("lattice_parameters"), number)
        grain = next(iter(phase_details.get("grain_sizes") or []), None) or {}
        row = {
            "task_id": task_id,
            "task_name": task_name,
            "candidate": candidate,
            "source_index": source_index,
            "adopted": "yes" if adopted else "",
            "phase": phase,
            "phase_label": _phase_label(phase),
            "spacegroup_symbol": phase_meta.get("spacegroup_symbol"),
            "spacegroup_number": number,
            "weight_percent": phase_details.get("weight_percent"),
            "weight_esd_percent": phase_details.get("weight_esd_percent"),
            "crystallite_size_nm": grain.get("value"),
            "crystallite_size_esd_nm": grain.get("esd"),
            "rwp": quality.get("rwp", result.get("rwp")),
            "rexp": quality.get("rexp"),
            "gof": quality.get("gof"),
            "chi_squared": quality.get("chi_squared"),
            "durbin_watson": quality.get("durbin_watson"),
            "missing_peaks": counts.get("missing"),
            "missing_peaks_significant": by_class.get("significant"),
            "missing_peaks_minor": by_class.get("minor"),
            "extra_peaks": counts.get("extra"),
            "two_theta_min": quality.get("two_theta_min"),
            "two_theta_max": quality.get("two_theta_max"),
            "cautions": cautions,
            "provenance": _provenance(phase),
            "verdict": ((phase_verdicts.get(phase) or {}).get("level")) or "",
            "overall_confidence": overall_level,
            "mass_balance_deviation": mass_balance_deviation,
        }
        for key, column in (
            ("a", "a_angstrom"), ("b", "b_angstrom"), ("c", "c_angstrom"),
            ("alpha", "alpha_deg"), ("beta", "beta_deg"), ("gamma", "gamma_deg"),
        ):
            value, esd = lattice[key]
            row[column] = value
            row[f"{key}_esd"] = esd
        rows.append(row)
    return rows


def task_table_rows(task_id: int, task: dict, summary: dict) -> list[dict]:
    """Rows for the final result and every displayed candidate of one task.

    ``task`` is the stored worker record (for the review selection) and
    ``summary`` the payload of ``GET /api/task/{id}``.
    """
    selection = ((task or {}).get("review") or {}).get("selection")
    name = summary.get("task_label") or ((task or {}).get("job") or {}).get("name") or ""
    rows: list[dict] = []
    final = summary.get("final_result")
    if final:
        rows += _rows_for_result(
            task_id, name, "final", None, _is_adopted(selection, "final", None), final
        )
    for number, candidate in enumerate(summary.get("all_results") or [], start=1):
        source_index = candidate.get("source_index", number - 1)
        rows += _rows_for_result(
            task_id, name, str(number), source_index,
            _is_adopted(selection, "candidate", source_index), candidate,
        )
    return rows


def multi_task_rows(entries: list[tuple[int, dict | None, dict | None]]) -> list[dict]:
    """Final-result rows for several tasks.

    ``entries`` holds ``(task_id, task_record_or_None, summary_or_None)``. A task
    without a completed summary yields one ``status: ...`` row.
    """
    from dara.server.utils import convert_to_local_tz

    rows: list[dict] = []
    for task_id, task, summary in entries:
        extra = {}
        if task:
            extra["user"] = task.get("user") or ""
            submitted = task.get("submitted_time")
            if submitted is not None:
                try:
                    extra["submitted_on"] = convert_to_local_tz(submitted).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    extra["submitted_on"] = str(submitted)
        if summary is None or not summary.get("final_result"):
            status = (task or {}).get("status") or "NOT_FOUND"
            row = {
                "task_id": task_id,
                "task_name": ((task or {}).get("job") or {}).get("name") or "",
                "candidate": f"status: {status}",
                **extra,
            }
            rows.append(row)
            continue
        selection = ((task or {}).get("review") or {}).get("selection")
        name = summary.get("task_label") or ((task or {}).get("job") or {}).get("name") or ""
        for row in _rows_for_result(
            task_id, name, "final", None, _is_adopted(selection, "final", None),
            summary["final_result"],
        ):
            rows.append({**row, **extra})
    return rows


def rows_to_csv(rows: list[dict], columns: list[str]) -> bytes:
    """UTF-8 (with BOM, for Excel) CSV with CRLF line endings."""
    buffer = StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_format(row.get(column)) for column in columns])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
