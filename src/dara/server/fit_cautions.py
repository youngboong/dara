"""Non-prescriptive automated cautions shown next to refinement statistics.

These are review flags, not verdicts: each message states a measurable property
of the fit and what it may imply, never that a result is right or wrong.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections import OrderedDict
from html import unescape
from pathlib import Path

logger = logging.getLogger("dara.server.fit_cautions")

POOR_GOF = 3.0
POOR_RWP = 25.0
MIN_WEIGHT_PERCENT = 1.0
LOW_DURBIN_WATSON = 1.0
LATTICE_BOUND_FRACTION = 0.9


def _display_name(name: str) -> str:
    try:
        from dara.plot import phase_display_name

        return unescape(re.sub(r"<[^>]+>", "", phase_display_name(name)))
    except Exception:
        return str(name)


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _caution(code: str, level: str, message: str, phase: str | None = None) -> dict:
    return {"code": code, "level": level, "message": message, "phase": phase}


def fit_cautions(
    fit_quality: dict,
    phase_details: dict,
    diagnostics: dict | None,
    *,
    counts_like: bool | None = None,
    lattice_flags: list[dict] | None = None,
) -> list[dict]:
    """Return advisory cautions for one refinement, in a fixed order."""
    fit_quality = fit_quality or {}
    phase_details = phase_details or {}
    cautions: list[dict] = []
    gof = _number(fit_quality.get("gof"))
    rwp = _number(fit_quality.get("rwp"))

    reasons = []
    if gof is not None and gof < 1.0:
        reasons.append(
            f"Rwp is below Rexp (GOF {gof:.2f} < 1); Rexp may be overestimated "
            "or the model may be over-parameterized."
        )
    if counts_like is False:
        reasons.append(
            "Intensities do not look like raw counts, so Rexp and GOF are not "
            "statistically meaningful."
        )
    if reasons:
        cautions.append(_caution("rexp_unreliable", "info", " ".join(reasons)))

    if (gof is not None and gof > POOR_GOF) or (rwp is not None and rwp > POOR_RWP):
        rwp_text = f"{rwp:.1f}" if rwp is not None else "n/a"
        gof_text = f"{gof:.2f}" if gof is not None else "n/a"
        cautions.append(
            _caution(
                "poor_fit",
                "warning",
                f"Fit residuals are high (Rwp {rwp_text}%, GOF {gof_text}); "
                "review the difference curve before relying on the result.",
            )
        )

    for phase, details in phase_details.items():
        details = details or {}
        weight = _number(details.get("weight_percent"))
        esd = _number(details.get("weight_esd_percent"))
        if weight is None:
            continue
        esd_text = f" (± {esd:.2f})" if esd is not None else ""
        if esd is not None and weight < 2 * esd:
            cautions.append(
                _caution(
                    "insignificant_phase",
                    "warning",
                    f"{_display_name(phase)}: {weight:.2f} wt%{esd_text} is not clearly "
                    "above its uncertainty; its presence is not established by this fit.",
                    phase,
                )
            )
        elif weight < MIN_WEIGHT_PERCENT:
            cautions.append(
                _caution(
                    "insignificant_phase",
                    "warning",
                    f"{_display_name(phase)}: {weight:.2f} wt%{esd_text} is below 1 wt%, "
                    "near the typical detection limit of laboratory XRD, so its presence may be "
                    "hard to confirm.",
                    phase,
                )
            )

    by_phase: dict[str, list[str]] = {}
    for flag in lattice_flags or []:
        if isinstance(flag, dict) and flag.get("phase") and flag.get("parameter"):
            by_phase.setdefault(flag["phase"], []).append(str(flag["parameter"]))
    for phase, parameters in by_phase.items():
        cautions.append(
            _caution(
                "lattice_at_bound",
                "warning",
                f"{_display_name(phase)}: lattice parameter {', '.join(parameters)} ended "
                "within 10% of its refinement limit; the reference structure may not match.",
                phase,
            )
        )

    durbin_watson = _number(fit_quality.get("durbin_watson"))
    if durbin_watson is not None and durbin_watson < LOW_DURBIN_WATSON:
        cautions.append(
            _caution(
                "correlated_residuals",
                "info",
                f"Durbin–Watson {durbin_watson:.2f} < 1 indicates strongly correlated "
                "residuals; esd values are likely underestimated.",
            )
        )

    missing = _number(((diagnostics or {}).get("counts") or {}).get("missing"))
    if missing is not None and missing >= 1:
        cautions.append(
            _caution(
                "missing_peaks_remain",
                "warning" if missing >= 3 else "info",
                f"{int(missing)} observed peak(s) are not explained by the fitted phases.",
            )
        )
    return cautions


def displacement_caution(displacement: dict | None) -> dict | None:
    """Caution for a large refined specimen displacement (``None`` when unremarkable)."""
    if not isinstance(displacement, dict):
        return None
    if displacement.get("level") not in {"warning", "severe"}:
        return None
    return _caution(
        "sample_displacement", "warning", str(displacement.get("message") or ""), None
    )


def broad_background_caution(info: dict | None) -> dict | None:
    """Information about a broad background hump (``None`` when not detected)."""
    if not isinstance(info, dict) or not info.get("detected"):
        return None
    width = _number(info.get("width_deg"))
    if width is None:
        return None
    return _caution(
        "broad_background",
        "info",
        f"The background contains a broad hump about {width:.0f}° wide; weight fractions "
        "refer to the crystalline phases in the model only, and amorphous content is not "
        "quantified.",
        None,
    )


def _count(value) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def search_cautions(candidate_summary: dict | None, search_summary: dict | None) -> list[dict]:
    """Cautions about how complete the candidate search was (empty for older documents).

    Describes the whole run (screening time-outs, an analysis stopped at its time limit,
    candidates skipped by the pre-filter), so it applies to the final result only.
    """
    candidate_summary = candidate_summary if isinstance(candidate_summary, dict) else {}
    search_summary = search_summary if isinstance(search_summary, dict) else {}
    cautions: list[dict] = []

    unavailable = candidate_summary.get("cod_unavailable")
    if isinstance(unavailable, (list, tuple)) and unavailable:
        count = len(unavailable)
        cautions.append(
            _caution(
                "references_unavailable",
                "warning",
                f"{count} COD reference structure{'s' if count != 1 else ''} could not be "
                f"downloaded and {'were' if count != 1 else 'was'} not considered.",
            )
        )

    screening = candidate_summary.get("screening")
    screening = screening if isinstance(screening, dict) else {}
    timed_out = [
        item for item in screening.get("timed_out_candidates") or [] if isinstance(item, dict)
    ]
    if timed_out:
        names = ", ".join(
            _display_name(Path(str(item.get("file", ""))).stem) for item in timed_out[:5]
        )
        if len(timed_out) > 5:
            names += ", …"
        cautions.append(
            _caution(
                "screening_timed_out",
                "warning",
                f"{len(timed_out)} reference structure(s) exceeded the screening time limit "
                f"and were not considered: {names}. If one of them is expected, refine it "
                "with Known-phase refinement.",
            )
        )

    if search_summary.get("stopped_early"):
        budget = _number(search_summary.get("runtime_budget_s"))
        limit = f" ({budget / 60:g} min)" if budget is not None else ""
        stage = search_summary.get("stopped_at_stage")
        if stage == "screening":
            missing = _count(search_summary.get("screening_not_screened")) or _count(
                screening.get("not_screened_deadline")
            )
            where = "reference screening" + (f" ({missing} references not screened)" if missing else "")
        elif stage == "search":
            where = "the phase-combination search"
        elif stage == "final_refinement":
            skipped = _count(search_summary.get("final_refinement_skipped"))
            where = "final refinement" + (f" ({skipped} candidates not refined)" if skipped else "")
        else:
            where = "the search"
        cautions.append(
            _caution(
                "search_incomplete",
                "warning",
                f"The analysis stopped at its time limit{limit} during {where}; the result "
                "is the best found by then and may be incomplete.",
            )
        )

    prefilter = candidate_summary.get("prefilter")
    prefilter = prefilter if isinstance(prefilter, dict) else {}
    excluded = _count(prefilter.get("excluded"))
    if prefilter.get("applied") and excluded > 0:
        cautions.append(
            _caution(
                "prefilter_applied",
                "info",
                f"{excluded} of {_count(prefilter.get('before'))} candidate references were "
                "skipped by the pre-filter because their strongest calculated peaks were not "
                "found in the pattern.",
            )
        )
    return cautions


# ---------------------------------------------------------------------------
# lattice_at_bound (best effort; never raises)
# ---------------------------------------------------------------------------

_PARAM_PATTERN = re.compile(
    r"PARAM=(A|B|C|ALPHA|BETA|GAMMA)=(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)\^(-?\d+(?:\.\d+)?)",
    re.I,
)
_LATTICE_FLAG_CACHE: OrderedDict[str, list[dict]] = OrderedDict()
_LATTICE_FLAG_CACHE_SIZE = 32


def _cif_keys(cif) -> set[str]:
    keys = set()
    for attr in ("filename", "name"):
        try:
            value = getattr(cif, attr, None)
        except Exception:
            value = None
        if value:
            keys.add(Path(str(value)).stem if attr == "filename" else str(value))
    return keys


def lattice_range_resolver(
    global_range, additional_cifs=None, additional_params=None, overrides=None, *, mp_window=None
):
    """Return ``f(cif) -> lattice_range`` mirroring how ``do_refinement`` merges settings.

    Precedence (lowest to highest), as in ``dara.refine.do_refinement``: the global
    final-refinement parameters, then per-phase parameters (``additional_cif_params``
    for user-supplied CIFs), then explicit ``final_refinement_overrides``.
    """
    extra_keys: set[str] = set()
    for cif in additional_cifs or []:
        extra_keys |= _cif_keys(cif)
    extra = additional_params if isinstance(additional_params, dict) else {}
    forced = overrides if isinstance(overrides, dict) else {}

    window = None
    if isinstance(mp_window, (list, tuple)) and len(mp_window) == 2:
        try:
            from dara.structure_quality import effective_lattice_range

            window = tuple(mp_window)
        except ImportError:  # W1 module not installed: keep the plain logic
            effective_lattice_range = None
    if window is not None:
        additional_names = set(extra_keys)

        def resolve(cif):
            filename = getattr(cif, "filename", None)
            keys = sorted(_cif_keys(cif))
            name = Path(str(filename)).stem if filename else (keys[0] if keys else "")
            try:
                return effective_lattice_range(
                    name, global_range,
                    additional_cif_names=additional_names,
                    additional_params=extra,
                    overrides=forced,
                    mp_window=window,
                )
            except Exception:
                logger.debug("effective_lattice_range failed", exc_info=True)
                value = global_range
                if extra_keys & _cif_keys(cif) and "lattice_range" in extra:
                    value = extra["lattice_range"]
                if "lattice_range" in forced:
                    value = forced["lattice_range"]
                return value

        return resolve

    def resolve(cif):
        value = global_range
        if extra_keys & _cif_keys(cif) and "lattice_range" in extra:
            value = extra["lattice_range"]
        if "lattice_range" in forced:
            value = forced["lattice_range"]
        return value

    return resolve


def lattice_bound_flags(refinement, reference_cifs, lattice_range) -> list[dict]:
    """Return refined lattice parameters that ended near their refinement bound.

    The bounds are regenerated from the reference CIF with the same
    ``lattice_range`` used for the final refinement. ``lattice_range`` may be a
    callable ``f(cif)`` (see :func:`lattice_range_resolver`) when phases use
    different ranges. Any problem yields ``[]``.
    """
    try:
        from dara.cif2str import cif2str

        flags: list[dict] = []
        phases = refinement.lst_data.phases_results
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for phase_name, phase in phases.items():
                if str(getattr(phase, "unit", "NM")).upper() != "NM":
                    continue
                cif = next(
                    (
                        item
                        for item in reference_cifs or []
                        if phase_name in _cif_keys(item)
                    ),
                    None,
                )
                if cif is None:
                    continue
                phase_range = lattice_range(cif) if callable(lattice_range) else lattice_range
                if phase_range is None or phase_range == "fixed":
                    continue
                try:
                    cif_path = tmp_dir / f"{phase_name}.cif"
                    cif_path.write_text(
                        getattr(cif, "orig_string", None) or str(cif), encoding="utf-8"
                    )
                    str_path = cif2str(cif_path, working_dir=tmp_dir, lattice_range=phase_range)
                    text = Path(str_path).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for key, start, low, high in _PARAM_PATTERN.findall(text):
                    start, low, high = float(start), float(low), float(high)
                    raw = getattr(phase, key.lower(), None)
                    if isinstance(raw, (tuple, list)) and raw:
                        raw = raw[0]
                    value = _number(raw)
                    if value is None:
                        continue
                    for bound in (low, high):
                        reach = abs(bound - start)
                        if reach <= 0:
                            continue
                        moved = value - start
                        if moved * (bound - start) > 0 and abs(moved) >= (
                            LATTICE_BOUND_FRACTION * reach
                        ):
                            flags.append({"phase": phase_name, "parameter": key.lower()})
                            break
        return flags
    except Exception:
        logger.debug("Lattice-bound check skipped", exc_info=True)
        return []


def cached_lattice_flags(task_uuid, compute) -> list[dict]:
    """Memoize ``compute()`` per task so repeated page loads do not rerun cif2str."""
    if task_uuid is not None and task_uuid in _LATTICE_FLAG_CACHE:
        _LATTICE_FLAG_CACHE.move_to_end(task_uuid)
        return _LATTICE_FLAG_CACHE[task_uuid]
    try:
        flags = list(compute() or [])
    except Exception:
        flags = []
    if task_uuid is not None:
        _LATTICE_FLAG_CACHE[task_uuid] = flags
        while len(_LATTICE_FLAG_CACHE) > _LATTICE_FLAG_CACHE_SIZE:
            _LATTICE_FLAG_CACHE.popitem(last=False)
    return flags


def clear_lattice_flag_cache(task_uuid=None) -> None:
    if task_uuid is None:
        _LATTICE_FLAG_CACHE.clear()
    else:
        _LATTICE_FLAG_CACHE.pop(task_uuid, None)
