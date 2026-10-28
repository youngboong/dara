"""Turn stored job tracebacks into short, actionable explanations."""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern, str, str]] = [
    (
        "interrupted",
        re.compile(
            r"Job process exited without a completed result|"
            r"Unexpected shutdown of the worker process|"
            r"KeyboardInterrupt",
            re.I,
        ),
        "The analysis was interrupted",
        "The server stopped or the worker crashed. Rerun the task; completed checkpoints were kept.",
    ),
    (
        "no_peaks",
        re.compile(r"No peaks are detected in the pattern", re.I),
        "No diffraction peaks were detected",
        "Check that the file holds 2θ/intensity data and that the analysis range covers the peaks.",
    ),
    (
        "no_candidates",
        re.compile(r"No reference CIFs were found", re.I),
        "No candidate structures were found",
        "Check the element list, enable COD/MP, or upload reference CIFs.",
    ),
    (
        "final_refinement_failed",
        re.compile(r"Final refinement failed for every candidate", re.I),
        "Final refinement failed for every candidate",
        "Try fewer/other reference phases, verify the instrument profile and wavelength, "
        "or relax refinement controls.",
    ),
    (
        "mp_key",
        re.compile(
            r"MP_API_KEY is not set|"
            r"(MPRest|Materials Project).{0,200}(401|403|api key|unauthori[sz]ed)",
            re.I | re.S,
        ),
        "Materials Project API key missing or rejected",
        "Re-enter the key when starting DARA (START.cmd), or switch MP off and use "
        "COD/uploaded CIFs.",
    ),
    (
        "bgmn_exit",
        re.compile(r"Error in BGMN refinement.*?exit code is (-?\d+)", re.I | re.S),
        "BGMN refinement program failed (exit code {0})",
        "Often caused by an unusual CIF or a profile/wavelength mismatch; remove "
        "suspicious CIFs and check the instrument profile.",
    ),
    (
        # Peak detection has its own fixed limit; the screening time limit does not apply to it.
        "eflech_timeout",
        re.compile(
            r"^.*eflech.*timed out after.*$|^.*TimeoutExpired.*eflech.*$",
            re.I | re.M,
        ),
        "Peak detection (EFLECH) timed out",
        "The peak search exceeded its fixed time limit, so a longer screening time limit will not help. "
        "Narrow the 2θ range, exclude noisy regions, or check the pattern for very dense or noisy data, "
        "then rerun.",
    ),
    (
        "timeout",
        re.compile(r"TimeoutExpired|timed out after", re.I),
        "A calculation step timed out",
        "Narrow the 2θ range or reduce candidate phases, then rerun.",
    ),
    (
        "network",
        re.compile(
            r"ConnectionError|Max retries exceeded|NameResolutionError|"
            r"getaddrinfo failed|URLError|SSLError|ReadTimeout|HTTPSConnectionPool",
            re.I,
        ),
        "Network error while contacting a structure database",
        "Check internet/proxy access to COD/Materials Project, or use uploaded CIFs.",
    ),
    (
        "ray",
        re.compile(
            r"RaySystemError|GCS server|Failed to start the dashboard|"
            r"Failed to start the raylet|ray\.init(?:\(\))? (?:failed|timed out)|"
            r"Could not (?:start|connect to) (?:the )?(?:Ray|GCS)",
            re.I,
        ),
        "The parallel worker (Ray) could not start",
        "Close other DARA windows or restart the PC; security software may block local ports.",
    ),
    (
        "memory",
        re.compile(r"MemoryError|Unable to allocate", re.I),
        "The computer ran out of memory",
        "Close other programs, reduce candidate phases/structures, and rerun.",
    ),
]

_LAST_LINE = re.compile(r"^(?:[\w.]+\.)?([A-Za-z_]\w*(?:Error|Exception|Interrupt|Exit|Warning)?)\s*:")


_EXCEPTION_LINE = re.compile(r"^\s*[\w.]+(?:Error|Exception|Interrupt)\b")


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    # Multi-line exceptions (e.g. pydantic ValidationError) end with a link line;
    # the last exception-looking line is the useful one.
    for line in reversed(lines):
        if _EXCEPTION_LINE.match(line):
            return line
    return lines[-1]


REMEDY_LABELS = {
    "timeout": "Retry with longer time limits",
    "mp_key": "Retry without Materials Project",
    "network": "Retry without Materials Project",
    "memory": "Retry with fewer MP structures and the candidate pre-filter",
    "no_candidates": "Retry with COD enabled and more MP structures",
    "final_refinement_failed": "Retry with lattice refinement limited to 3 %",
}


def _number(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number > 0 else default


def remedy_changes(code: str, settings: dict) -> dict | None:
    """New values for the ``_rerun_settings()["settings"]`` keys, or ``None`` if no remedy applies.

    Pure: ``settings`` is not modified. ``settings["_additional_cif_count"]`` (optional) is the
    number of uploaded reference CIFs that the rerun keeps.
    """
    if code not in REMEDY_LABELS or not isinstance(settings, dict):
        return None
    use_mp = bool(settings.get("use_mp"))
    use_cod = bool(settings.get("use_cod"))
    if code == "timeout":
        changes: dict = {
            "screening_timeout_s": min(
                7200, 2 * _number(settings.get("screening_timeout_s"), 1200)
            ),
        }
        minutes = _number(settings.get("max_analysis_minutes"), None)
        if minutes is not None:
            changes["max_analysis_minutes"] = min(1440, 1.5 * minutes)
        return changes
    if code in {"mp_key", "network"}:
        if settings.get("analysis_mode") == "reaction" or not use_mp:
            return None
        changes = {"use_mp": False}
        if not use_cod and not int(settings.get("_additional_cif_count") or 0):
            changes["use_cod"] = True
        return changes
    if code == "memory":
        changes = {"candidate_prefilter": True}
        if use_mp:
            current = int(_number(settings.get("max_mp_structures"), 100))
            changes["max_mp_structures"] = min(50, current)
        return changes
    if code == "no_candidates":
        changes = {"use_cod": True}
        if use_mp:
            current = int(_number(settings.get("max_mp_structures"), 100))
            changes["max_mp_structures"] = max(300, current)
        return changes
    return {"refinement_controls": {"lattice": "refine", "lattice_percent": 3}}


def _remedy(code: str) -> dict | None:
    label = REMEDY_LABELS.get(code)
    return {"label": label} if label else None


def summarize_failure(error_text: str | None) -> dict | None:
    """Return ``{"code", "title", "hint", "detail", "remedy"}`` or ``None`` for empty input.

    ``remedy`` is ``{"label"}`` when :func:`remedy_changes` can propose a fix, else ``None``.
    """
    if not error_text or not str(error_text).strip():
        return None
    text = str(error_text)
    last = _last_line(text)
    detail = last[:300]
    for code, pattern, title, hint in _RULES:
        match = pattern.search(text)
        if match:
            if "{0}" in title:
                title = title.format(match.group(1))
            return {
                "code": code, "title": title, "hint": hint, "detail": detail,
                "remedy": _remedy(code),
            }
    typed = _LAST_LINE.match(last)
    title = f"Analysis failed: {typed.group(1)}" if typed else "Analysis failed"
    return {
        "code": "unknown",
        "title": title,
        "hint": "See the technical details below.",
        "detail": detail,
        "remedy": None,
    }
