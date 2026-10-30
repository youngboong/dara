"""Assemble the paper-style English report of one refinement (F5)."""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from html import escape

import numpy as np

from dara.server.report import html as H
from dara.server.report.figures import fit_figure
from dara.server.report.references import Citations

logger = logging.getLogger("dara.server.report")

NOT_RECORDED = "not recorded"
FIGURE_CAPTION = (
    "Figure 1. Observed (black), calculated (red) and background (grey) intensities, "
    "reflection positions of each phase (tick marks) and the difference curve (blue). "
    "Shaded intervals were excluded from refinement."
)
LIMITATIONS = (
    "Weight fractions refer to the crystalline phases in the model; amorphous or unidentified "
    "phases are not quantified. The esd values are least-squares estimates without systematic "
    "errors and are underestimated when the residuals are correlated{dw}. A low Rwp alone does "
    "not establish phase identity {toby}, and DFT-computed reference lattices can deviate from "
    "experiment by a few percent."
)


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------

def _finite(value) -> float | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _plain_label(name: str) -> str:
    from dara.server.fit_cautions import _display_name

    return _display_name(name)


def _dara_version() -> str:
    try:
        import dara

        return str(dara.__version__)
    except Exception:
        return "custom build"


def _radiation(search_settings: dict) -> tuple[str | None, float | None]:
    """Radiation label and wavelength (Å) from the recorded ``wavelength`` setting."""
    from dara.server.api_router import LAB_WAVELENGTHS_ANGSTROM

    value = search_settings.get("wavelength")
    if value is None or value == "":
        return None, None
    try:
        return "synchrotron", float(value) * 10
    except (TypeError, ValueError):
        pass
    name = str(value).strip().capitalize()
    wavelength = LAB_WAVELENGTHS_ANGSTROM.get(name)
    if wavelength is None:
        return None, None
    return f"{name} Kα", wavelength


def _data_description(refinement) -> tuple[str, str, str, str]:
    """Return (tmin, tmax, n_points, median_step) strings of the analysed range."""
    try:
        x = np.asarray(refinement.plot_data.x, dtype=float)
        x = np.sort(x[np.isfinite(x)])
        steps = np.diff(x)
        steps = steps[steps > 0]
        return (
            f"{x.min():.2f}", f"{x.max():.2f}", str(int(x.size)),
            f"{float(np.median(steps)):.4f}" if steps.size else NOT_RECORDED,
        )
    except Exception:
        return NOT_RECORDED, NOT_RECORDED, NOT_RECORDED, NOT_RECORDED


def weight_text(weight, esd) -> str:
    """``weight ± esd`` rounded to the precision of the esd (at least one decimal)."""
    weight, esd = _finite(weight), _finite(esd)
    if weight is None:
        return "—"
    if esd is None or esd < 0:
        return f"{weight:.1f}"
    decimals = max(1, min(4, -math.floor(math.log10(esd)))) if esd > 0 else 1
    return f"{weight:.{decimals}f} ± {esd:.{decimals}f}"


def _excluded_clause(excluded_ranges) -> str:
    intervals = []
    for interval in excluded_ranges or []:
        try:
            intervals.append(f"{float(interval[0]):.2f}–{float(interval[1]):.2f}°")
        except (TypeError, ValueError, IndexError):
            continue
    if not intervals:
        return ""
    noun = "interval" if len(intervals) == 1 else "intervals"
    return f", excluding {len(intervals)} {noun} ({', '.join(intervals)} 2θ)"


def _effective_mp_window(global_range, window) -> tuple[float, float] | None:
    """Lattice window the computed (MP) phases were actually refined with.

    ``structure_quality.widen_for_mp`` merges the stored MP window with the global lattice
    range (a wider global range wins); a fixed or explicit range is left unchanged, in which
    case no widening happened and ``None`` is returned.
    """
    try:
        from dara.structure_quality import widen_for_mp

        widened = widen_for_mp(global_range, (float(window[0]), float(window[1])))
    except Exception:
        widened = (float(window[0]), float(window[1]))
    if isinstance(widened, (list, tuple)) and len(widened) == 2:
        try:
            return float(widened[0]), float(widened[1])
        except (TypeError, ValueError):
            return None
    return None


def _refinement_text(params: dict, candidate_summary: dict) -> str:
    """Sentence on what was refined in the final refinement (never invented)."""
    from dara.refinement_controls import DEFAULT_PHASE_PARAMS

    params = {**DEFAULT_PHASE_PARAMS, **(params or {})}
    refined = ["scale factors"]
    fixed_lattice = params.get("lattice_range") == "fixed"
    lattice_range = params.get("lattice_range")
    if not fixed_lattice:
        if isinstance(lattice_range, (list, tuple)) and len(lattice_range) == 2:
            lo, hi = float(lattice_range[0]), float(lattice_range[1])
            text = f"{100 * lo:+.1f}% to {100 * hi:+.1f}% of the reference values"
        else:
            r = _finite(lattice_range)
            text = f"±{100 * r:.1f}% of the reference values" if r is not None else NOT_RECORDED
        refined.append(f"lattice parameters (bounded to {text})")
    texture = str(params.get("gewicht", "")).upper()
    orientation = ""
    match = re.fullmatch(r"SPHAR(\d)", texture)
    if match:
        refined.append(
            f"preferred orientation (spherical harmonics of order {match.group(1)})"
        )
    elif texture in {"0_0", "ISOTROPIC"}:
        orientation = " No preferred-orientation correction was applied."
    if str(params.get("b1", "")) != "fixed":
        refined.append("isotropic crystallite-size broadening")
    if str(params.get("k1", "")) != "fixed" or str(params.get("k2", "")) != "fixed":
        refined.append("microstrain broadening")
    refined += ["the background", "the specimen displacement (EPS2)"]
    listing = ", ".join(refined[:-1]) + ", and " + refined[-1]
    sentence = (
        f"In the final refinement, {listing} were refined, while the zero-point error was "
        "fixed at zero."
    )
    if fixed_lattice:
        sentence += " The lattice parameters were fixed at the reference values."
    sentence += orientation
    window = (candidate_summary or {}).get("mp_lattice_window") or {}
    applied = window.get("applied_to") or []
    bounds = window.get("range")
    if applied and isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        effective = _effective_mp_window(params.get("lattice_range"), bounds)
        if effective is not None:
            sentence += (
                " For Materials Project structures the lattice window was widened to "
                f"{effective[0]:+.1%} to {effective[1]:+.1%} (systematic DFT lattice deviation)."
            )
    return sentence


def _database_sources(document, task: dict, phase_sources: set[str]) -> tuple[list[str], bool, float | None]:
    """Databases used for candidates: (keys, user_cifs_used, e_hull cutoff)."""
    sources = [str(item).lower() for item in (getattr(document, "candidate_sources", None) or [])]
    kwargs = ((task.get("job") or {}).get("function_kwargs")) or {}
    if not sources:
        for db in kwargs.get("cif_dbs") or []:
            if isinstance(db, dict):
                cls = str(db.get("@class", ""))
                if cls.startswith("COD"):
                    sources.append("cod")
                elif cls.startswith("MaterialsProject"):
                    sources.append("mp")
    user = bool(kwargs.get("additional_cifs") or kwargs.get("cifs")) or "user" in phase_sources
    if not sources:
        sources = sorted(src for src in phase_sources if src in {"cod", "mp"})
    e_hull = _finite(kwargs.get("database_e_hull_filter")) if "mp" in sources else None
    return sources, user, e_hull


def _phase_sources(phase_names) -> set[str]:
    out = set()
    try:
        from dara.structure_quality import reference_provenance

        for name in phase_names:
            out.add(str(reference_provenance(name).get("source")))
    except Exception:
        for name in phase_names:
            lowered = str(name).lower()
            out.add("cod" if "cod" in lowered else "mp" if "(mp" in lowered else "user")
    return out


def _runtime_text(candidate_summary, search_summary) -> str:
    """Sentences about the pre-filter, screening time-outs and a time-limited search.

    Empty for documents that carry none of these fields (older runs).
    """
    candidates = candidate_summary if isinstance(candidate_summary, dict) else {}
    search = search_summary if isinstance(search_summary, dict) else {}

    def whole(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    sentences: list[str] = []
    prefilter = candidates.get("prefilter")
    prefilter = prefilter if isinstance(prefilter, dict) else {}
    excluded = whole(prefilter.get("excluded"))
    if prefilter.get("applied") and excluded > 0:
        sentences.append(
            f"Before screening, {excluded} of {whole(prefilter.get('before'))} candidate "
            "structures whose strongest calculated reflections were not found in the "
            "measured pattern were skipped (presence pre-filter)."
        )

    screening = candidates.get("screening")
    screening = screening if isinstance(screening, dict) else {}
    timed_out = len(screening.get("timed_out_candidates") or [])
    if timed_out:
        limits = screening.get("timeout_s")
        limit = limits.get("default") if isinstance(limits, dict) else limits
        try:
            limit_text = f"{float(limit):g}"
        except (TypeError, ValueError):
            limit_text = None
        sentences.append(
            f"{timed_out} candidate structure(s) exceeded the per-reference screening limit"
            + (f" of {limit_text} s" if limit_text else "")
            + " and were not considered."
        )

    if search.get("stopped_early"):
        try:
            budget = f" of {float(search['runtime_budget_s']) / 60:g} min"
        except (KeyError, TypeError, ValueError):
            budget = ""
        stage = {
            "screening": "reference screening",
            "search": "the phase-combination search",
            "final_refinement": "final refinement",
        }.get(search.get("stopped_at_stage"), "the search")
        sentences.append(
            f"The analysis stopped at the user-set time limit{budget} during {stage}; "
            "the reported result is the best found by then and may be incomplete."
        )
    return escape(" ".join(sentences))


def _methods_paragraph(
    *, citations: Citations, filename: str, search_settings: dict, refinement,
    excluded_ranges, document, task: dict, phase_names, bgmn_version: str | None,
    radius_mm: float | None,
) -> str:
    radiation, wavelength = _radiation(search_settings)
    if radiation and wavelength:
        recorded = f"recorded with {radiation} radiation (λ = {wavelength:.6f} Å)"
    else:
        recorded = "recorded (radiation and wavelength not recorded)"
    tmin, tmax, points, _ = _data_description(refinement)
    profile = search_settings.get("instrument_profile") or NOT_RECORDED
    radius = f", goniometer radius {radius_mm:g} mm" if radius_mm else ""
    excluded = _excluded_clause(excluded_ranges)

    parts = [
        f"The powder X-ray diffraction pattern ({escape(filename)}) was {recorded}. ",
        f"Data from {tmin}° to {tmax}° 2θ ({points} points) were analysed{excluded}. ",
    ]
    parts.append(
        f"Phases were identified and quantified by automated Rietveld refinement with DARA "
        f"{escape(_dara_version())} {citations.cite('dara')}, which uses "
        f"{escape('BGMN ' + bgmn_version if bgmn_version else 'BGMN')} {citations.cite('bgmn')} "
        f"with the fundamental-parameters profile “{escape(str(profile))}”{radius}. "
    )
    sources, user_cifs, e_hull = _database_sources(document, task, _phase_sources(phase_names))
    items = []
    if "cod" in sources:
        items.append(f"the Crystallography Open Database (COD) {citations.cite('cod')}")
    if "mp" in sources:
        item = f"the Materials Project {citations.cite('mp')}"
        if e_hull is not None:
            item += f" (energy above hull ≤ {e_hull:.3f} eV/atom)"
        items.append(item)
    if user_cifs:
        items.append("user-supplied CIF files")
    if items:
        listing = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        parts.append(
            f"Candidate structures came from {listing} and were processed with pymatgen "
            f"{citations.cite('pymatgen')}. "
        )
    else:
        parts.append("The sources of the candidate structures were not recorded. ")
    summary = getattr(document, "candidate_summary", None)
    runtime_text = _runtime_text(summary, getattr(document, "search_summary", None))
    if runtime_text:
        parts.append(runtime_text + " ")
    parts.append(
        escape(
            _refinement_text(
                getattr(document, "final_refinement_params", None),
                summary if isinstance(summary, dict) else {},
            )
        )
        + " "
    )
    parts.append("Weight fractions were calculated from the refined scale factors.")
    return "<p>" + "".join(parts) + "</p>"


# ---------------------------------------------------------------------------
# numbering and cell helpers
# ---------------------------------------------------------------------------

THIN = " "
MINUS = "−"
ANALYSIS_MODES = {
    "reaction": "Reaction-guided (precursors and temperature)",
    "elemental": "Elemental (phases of the listed elements)",
    "known_mixture": "Known mixture (phases of the listed precursors)",
    "selected_phases": "Selected phases",
}
LATTICE_SYMBOLS = {
    "a": "a", "b": "b", "c": "c", "alpha": "α", "beta": "β", "gamma": "γ",
}


class _Numbering:
    """Running section, sub-section and table numbers of one report."""

    def __init__(self) -> None:
        self.section = 0
        self.sub = 0
        self.tables = 0

    def h2(self, title: str) -> str:
        self.section += 1
        self.sub = 0
        return f"<h2>{self.section}. {escape(title)}</h2>"

    def h3(self, title: str) -> str:
        self.sub += 1
        return f"<h3>{self.section}.{self.sub} {escape(title)}</h3>"

    def caption(self, text: str) -> str:
        self.tables += 1
        return f"<b>Table {self.tables}.</b> {escape(text)}"


def _caption(numbering: "_Numbering | None", text: str) -> str | None:
    return numbering.caption(text) if numbering is not None else None


def _pm(text: str) -> str:
    """Thin spaces around ± (the table cells are no-break)."""
    return text.replace(" ± ", f"{THIN}±{THIN}")


def _signed(value: float, decimals: int) -> str:
    return f"{value:+.{decimals}f}".replace("-", MINUS)


def _value_esd(value, esd, *, plain_decimals: int = 5, esd_digits: int = 2) -> str:
    """``value ± esd`` rounded to ``esd_digits`` significant digits of the esd."""
    value, esd = _finite(value), _finite(esd)
    if value is None:
        return "—"
    if esd is None or esd <= 0:
        return f"{value:.{plain_decimals}f}"
    decimals = max(0, min(7, esd_digits - 1 - math.floor(math.log10(esd))))
    return f"{value:.{decimals}f} ± {esd:.{decimals}f}"


def _plain_name(phase: str, symbol: str | None = None) -> str:
    """Readable plain-text phase name; a merely appended space-group symbol is dropped."""
    label = _plain_label(phase)
    if symbol and label.endswith(f" · {symbol}"):
        return label[: -len(f" · {symbol}")]
    match = re.match(r"^(.+?)_(\d+)_\(", phase)
    if match and label.startswith(match.group(1) + " · "):
        return match.group(1)
    return label


def _name_html(phase: str) -> str:
    """Phase name with subscripted formula (no trailing space-group symbol)."""
    from dara.plot import phase_display_name

    shown = phase_display_name(phase)
    match = re.match(r"^(.+?)_(\d+)_\(", phase)
    if match:
        formula = re.sub(r"(\d+(?:\.\d+)?)", r"<sub>\1</sub>", escape(match.group(1)))
        if shown.startswith(formula + " · "):
            return formula
    return shown


def _spacegroup_cell(meta: dict) -> str:
    symbol = meta.get("spacegroup_symbol")
    if not symbol:
        return "—"
    text = re.sub(r"_(\d)", r"<sub>\1</sub>", escape(str(symbol)))
    number = meta.get("spacegroup_number")
    return text + (f"<br>No.{THIN}{escape(str(number))}" if number else "")


def _lattice_cell(details: dict) -> str:
    lines = []
    for key, value in (details.get("lattice_parameters") or {}).items():
        symbol = LATTICE_SYMBOLS.get(key, key)
        angle = value.get("unit") == "deg"
        text = _pm(_value_esd(
            value.get("value"), value.get("esd"), plain_decimals=3 if angle else 5,
        ))
        lines.append(f"<span><i>{escape(symbol)}</i> = {text}</span>")
    return "".join(lines) or "—"


def _crystallite_cell(details: dict, phase_result) -> str:
    """Refined crystallite size (nm), or a dash when the size broadening was not refined.

    BGMN prints the same start-value GrainSize for every phase whose size parameter stayed
    unrefined; such a value carries no esd and is not a result.
    """
    extra = getattr(phase_result, "model_extra", None) or {}
    b1_refined = isinstance(extra.get("B1"), (tuple, list))
    for item in details.get("grain_sizes") or []:
        value, esd = _finite(item.get("value")), _finite(item.get("esd"))
        if value is None:
            continue
        if esd is not None:
            return _pm(_value_esd(value, esd, plain_decimals=1))
        if b1_refined:
            return f"{value:.1f}"
    return "—"


def _reference_cell(phase: str) -> str:
    cod = re.search(r"cod[_-]?(\d+)", phase, re.I)
    mp = re.search(r"mp[-_](\d+)", phase, re.I)
    if cod:
        record = cod.group(1)
        return f'<a href="https://www.crystallography.net/cod/{record}.html">COD {record}</a>'
    if mp:
        record = mp.group(1)
        return (
            f'<a href="https://materialsproject.org/materials/mp-{record}">MP mp-{record}</a>'
        )
    return "User CIF"


# ---------------------------------------------------------------------------
# conditions
# ---------------------------------------------------------------------------

def _refinement_short(params: dict) -> str:
    """Compact description of what was refined in the final refinement."""
    from dara.refinement_controls import DEFAULT_PHASE_PARAMS

    params = {**DEFAULT_PHASE_PARAMS, **(params or {})}
    parts = []
    lattice_range = params.get("lattice_range")
    if lattice_range == "fixed":
        parts.append("lattice fixed")
    elif isinstance(lattice_range, (list, tuple)) and len(lattice_range) == 2:
        low, high = 100 * float(lattice_range[0]), 100 * float(lattice_range[1])
        parts.append(f"lattice {_signed(low, 0)} % to {_signed(high, 0)} %")
    elif _finite(lattice_range) is not None:
        parts.append(f"lattice ±{100 * _finite(lattice_range):g} %")
    texture = str(params.get("gewicht", "")).upper()
    match = re.fullmatch(r"SPHAR(\d)", texture)
    if match:
        parts.append(f"preferred orientation: spherical harmonics order {match.group(1)}")
    elif texture in {"0_0", "ISOTROPIC"}:
        parts.append("no preferred-orientation correction")
    if any(str(params.get(key, "")) != "fixed" for key in ("b1", "k1", "k2")):
        parts.append("peak broadening refined")
    if not parts:
        return NOT_RECORDED
    text = ", ".join(parts)
    return text[0].upper() + text[1:]


def _instrument_line(metadata: dict | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    tube = re.sub(r"\s*\(.*$", "", str(metadata.get("xray_tube") or "")).strip()
    items = [metadata.get("application_software"), tube, metadata.get("detector")]
    return "; ".join(str(item) for item in items if item) or None


def _completed_text(value) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value).replace("T", " ")[:16]


def _excluded_text(excluded_ranges) -> str:
    intervals = []
    for interval in excluded_ranges or []:
        try:
            intervals.append(f"{float(interval[0]):.2f}–{float(interval[1]):.2f}°")
        except (TypeError, ValueError, IndexError):
            continue
    return ", ".join(intervals)


def _conditions_table(
    numbering: _Numbering, *, document, task: dict, search_settings: dict, refinement,
    excluded_ranges, phase_names, metadata: dict | None,
) -> str:
    rows: list[tuple[str, str]] = []
    mode = getattr(document, "analysis_mode", None)
    if mode:
        rows.append(("Analysis mode", ANALYSIS_MODES.get(str(mode), str(mode))))
    elements = getattr(document, "elements", None)
    if elements:
        rows.append(("Elements", ", ".join(str(e) for e in elements)))
    precursors = getattr(document, "precursors", None)
    if precursors:
        rows.append(("Precursor formulas", ", ".join(str(p) for p in precursors)))
    sources, user_cifs, _ = _database_sources(document, task, _phase_sources(phase_names))
    names = [{"cod": "COD", "mp": "Materials Project"}.get(s, s) for s in sources]
    if user_cifs:
        names.append("user CIF files")
    if names:
        rows.append(("Databases", ", ".join(names)))
    rows.append(("Instrument profile", str(search_settings.get("instrument_profile") or NOT_RECORDED)))
    radiation, wavelength = _radiation(search_settings)
    rows.append((
        "Radiation",
        f"{radiation}, λ = {wavelength:.6f} Å" if radiation and wavelength else NOT_RECORDED,
    ))
    tmin, tmax, points, _ = _data_description(refinement)
    range_text = f"{tmin}–{tmax}° ({points} points)" if tmin != NOT_RECORDED else NOT_RECORDED
    excluded = _excluded_text(excluded_ranges)
    if excluded:
        range_text += f"; excluded {excluded}"
    rows.append(("2θ range analysed", range_text))
    rows.append((
        "Final refinement",
        _refinement_short(getattr(document, "final_refinement_params", None)),
    ))
    predict = getattr(document, "predict_kwargs", None) or {}
    kelvin = _finite(predict.get("temp")) if str(mode) == "reaction" else None
    if kelvin is not None:
        rows.append(("Reaction prediction", f"{kelvin - 273.15:.0f} °C ({kelvin:.0f} K)"))
    instrument = _instrument_line(metadata)
    if instrument:
        rows.append(("Instrument", instrument))
    completed = _completed_text(task.get("end_time"))
    if completed:
        rows.append(("Completed", completed))
    return H.table(
        ["Condition", "Value"], [[escape(k), escape(v)] for k, v in rows],
        caption=numbering.caption("Analysis conditions."), widths=[24, 76],
    )


# ---------------------------------------------------------------------------
# assessment
# ---------------------------------------------------------------------------

def _phase_notes(phases_confidence: dict, rwp: float | None, citations: Citations, name_of) -> list[str]:
    """One line per phase that is not Confident or has an alternative structure model."""
    from dara.server.confidence import (
        COMPUTED_LATTICE_CHANGE_PCT, MIN_WEIGHT_PERCENT, PRESENCE_CONFIDENT, SIGMA_CONFIDENT,
    )

    def fit(alt: dict) -> str:
        target = f" vs {rwp:.2f}%" if rwp is not None else ""
        return f"Rwp {alt['rwp']:.2f}%{target}"

    def who(alt: dict) -> str:
        number = alt.get("candidate_number")
        return f"Candidate {number}" if number is not None else "Another candidate"

    items = []
    for phase, entry in phases_confidence.items():
        evidence = entry.get("evidence") or {}
        alt = evidence.get("alternative")
        model = evidence.get("structure_model_alternative")
        if entry.get("level") == "confident" and not alt and not model:
            continue
        clauses = []
        if evidence.get("lattice_at_bound"):
            clauses.append("a lattice parameter ended at its refinement limit")
        weight, sigma = evidence.get("weight_percent"), evidence.get("weight_sigma")
        if weight is not None and weight < MIN_WEIGHT_PERCENT:
            clauses.append(f"weight fraction {weight:.1f} wt% is below {MIN_WEIGHT_PERCENT:g} wt%")
        if sigma is not None and sigma < SIGMA_CONFIDENT:
            clauses.append(f"weight is only {sigma:.1f} times its esd")
        presence, n = evidence.get("presence_fraction"), evidence.get("n_candidates")
        if presence is not None and presence < PRESENCE_CONFIDENT and n:
            clauses.append(f"present in {round(presence * n)} of {n} candidate models")
        if alt:
            replaced = ", ".join(alt.get("replaced_by") or []) or "other phases"
            clauses.append(
                f"{who(alt)} fits almost as well ({fit(alt)}) with {replaced} instead of this phase"
            )
        hamilton = evidence.get("hamilton")
        if hamilton and not hamilton.get("significant"):
            clauses.append(
                "its removal is not significant in Hamilton's R-ratio test "
                + citations.cite("hamilton")
            )
        change = evidence.get("lattice_change_pct")
        if evidence.get("computed_structure") and change is not None and change > COMPUTED_LATTICE_CHANGE_PCT:
            clauses.append(f"computed reference structure whose lattice changed by {change:.1f}% on refinement")
        if model:
            kind = "polymorph" if model.get("polymorph") else "structure model"
            clauses.append(
                f"{who(model)} fits almost as well ({fit(model)}) with another {kind} of this "
                f"compound (space group {model['spacegroup_number']} instead of "
                f"{model['own_spacegroup_number']})"
            )
        if not clauses:
            reasons = entry.get("reasons") or []
            clauses.append(str(reasons[0]).rstrip(".") if reasons else "no criterion could be evaluated")
        sentence = "; ".join(clauses)
        sentence = sentence[0].upper() + sentence[1:] + "."
        items.append(
            f"<li><b>{name_of(phase)}</b>: {escape(str(entry.get('label')))}. {escape(sentence)}</li>"
        )
    return items


def _interpretation(entry: dict, anode) -> str:
    """Plain-text interpretation of one classified unexplained peak."""
    attribution = entry.get("attribution")
    if isinstance(attribution, dict):
        parent = attribution.get("parent_two_theta")
        hkl = attribution.get("parent_hkl") or []
        line = (
            f"{str(anode).capitalize()} Kβ" if attribution.get("type") == "kbeta" and anode
            else "Kβ" if attribution.get("type") == "kbeta"
            else "W Lα1"
        )
        label = _plain_label(str(attribution.get("parent_phase") or ""))
        position = f" at {parent:.2f}°" if isinstance(parent, (int, float)) else ""
        return f"{line} line of {label} ({' '.join(str(v) for v in hkl)}){position}"
    hint = entry.get("hint")
    if isinstance(hint, dict) and hint.get("formula"):
        symbol = hint.get("spacegroup_symbol")
        return f"Consistent with trace {hint['formula']}" + (f" ({symbol})" if symbol else "")
    return "—"


def _unexplained_peaks_section(diagnostics, numbering: _Numbering | None = None) -> list[str]:
    """Significant and minor unexplained peaks; empty without classification data."""
    try:
        from dara.server import peak_significance as ps

        entries = (diagnostics or {}).get("missing_classified")
        if entries is None:
            return []
        shown = [
            e for e in entries if isinstance(e, dict) and e.get("class") in {"significant", "minor"}
        ]
        if not shown:
            return ["<p>No significant or minor unexplained peaks remain.</p>"]
        anode = ((diagnostics.get("missing_significance") or {}).get("anode")) or None
        rows = [
            [
                escape(f"{float(e['two_theta']):.2f}"),
                escape(f"{100 * float(e['rel_height']):.1f}"),
                escape(f"{float(e['snr']):.1f}"),
                escape(str(e.get("class", ""))),
                escape(_interpretation(e, anode)),
            ]
            for e in shown
        ]
        intro = (
            "Observed peaks not explained by the fitted phases; significant peaks are at least "
            f"{ps.SIGNIFICANT_REL * 100:.0f}% of the strongest peak with S/N ≥ {ps.SIGNIFICANT_SNR:.0f}."
        )
        return [
            f"<p>{escape(intro)}</p>",
            H.table(
                ["2θ (°)", "% of strongest", "S/N", "Class", "Interpretation"], rows,
                caption=_caption(numbering, "Unexplained observed peaks."),
                widths=[12, 17, 10, 14, 47], align="rrrll", nowrap="nnnnl", compact=True,
            ),
        ]
    except Exception:
        logger.debug("Unexplained-peak table failed", exc_info=True)
        return []


def _mass_balance_section(block: dict | None, numbering: _Numbering | None = None) -> list[str]:
    """One sentence and the element table (nothing when no precursor amounts were compared)."""
    if not block or block.get("status") != "evaluated":
        return []
    source = (
        "read from the pattern file name" if block.get("source") == "filename"
        else "entered with the submission"
    )
    verdict = {
        "ok": "agree within the tolerance",
        "caution": "differ moderately",
        "warning": "differ strongly",
    }.get(block.get("level"), "were compared")
    excluded = ", ".join(block.get("excluded_elements") or [])
    text = (
        f"The refined phases imply {block['observed_ratio']}, the precursor amounts ({source}) "
        f"imply {block['expected_ratio']}; the two {verdict} (largest deviation "
        f"{block['max_deviation']:.2f}"
        + (f"; {excluded} not compared" if excluded else "")
        + ")."
    )
    rows = [
        [escape(row["element"]), f"{row['expected']:.3f}", f"{row['observed']:.3f}",
         _signed(row["deviation"], 3)]
        for row in block.get("elements") or []
    ]
    return [
        f"<p>{escape(text)}</p>",
        H.table(
            ["Element", "Precursors", "Refined", "Deviation"], rows,
            caption=_caption(numbering, "Element mole fractions."),
            widths=[28, 24, 24, 24], align="lrrr", nowrap="nnnn", compact=True,
        ),
    ]


def _result_shown(selection, selection_label, is_final, adopted) -> str | None:
    """Short line naming a non-default result (version, candidate or adopted selection)."""
    if selection_label:
        match = re.match(r"^Version\s+(\d+)\s*[—–-]\s*(.*)$", selection_label)
        shown = (
            f"version {match.group(1)}" + (f" ({match.group(2)})" if match.group(2) else "")
            if match
            else selection_label
        )
    elif is_final:
        if not adopted:
            return None
        shown = "minimum-Rwp result"
    else:
        shown = f"candidate {selection + 1}"
    return f"Result shown: {shown}" + (", adopted by the user" if adopted else "")


def build_report_html(
    task_id: int, task: dict, document, selection, review: dict, *,
    selection_label: str | None = None, selection_key: str | None = None,
) -> str:
    """Return the complete report as one standalone HTML document.

    ``selection_label`` / ``selection_key`` describe a re-refinement version whose document view
    is passed as ``document`` with selection ``"best"`` (the label names the version in the
    byline; the key is compared with the adopted review selection).
    """
    from dara.server import api_router, review_router
    from dara.server.confidence import (
        confidence_for_selection, fill_weight_esd, instrument_radius_mm, raw_lst_counts,
    )
    from dara.server.fit_cautions import (
        broad_background_caution, displacement_caution, fit_cautions,
    )
    from dara.xrd import get_xrdml_metadata

    refinement = review_router._select_refinement(document, selection)
    stored_details = api_router._phase_refinement_details(refinement)
    phase_details = fill_weight_esd(refinement, stored_details)
    phase_metadata = api_router._refined_phase_metadata(refinement)
    quality = api_router._fit_quality(refinement)
    search_settings = getattr(document, "search_kwargs", None) or {}
    excluded_ranges = (search_settings.get("refinement_params") or {}).get("excluded_ranges")
    counts_like = api_router._counts_like(document)
    name = task.get("job", {}).get("name") or getattr(document, "task_label", None) or f"Task {task_id}"
    is_final = selection is None or selection == "best"
    adopted = review.get("selection") is not None and (
        review["selection"] == (selection_key or ("best" if selection is None else selection))
    )

    lattice_flags: list[dict] = []
    if is_final:
        try:
            lattice_flags = api_router._final_lattice_flags(task, document)
        except Exception:
            lattice_flags = []
    confidence = confidence_for_selection(
        document, None if is_final else selection, lattice_flags=lattice_flags,
        hint_mode="background",
    )
    diagnostics: dict = {}
    try:
        diagnostics = api_router._classified_peak_diagnostics(
            document, refinement, hint_mode="background"
        )
        cautions = fit_cautions(
            quality, stored_details, diagnostics,
            counts_like=counts_like, lattice_flags=lattice_flags,
        )
    except Exception:
        cautions = []
    for extra in (
        displacement_caution((confidence or {}).get("sample_displacement")),
        broad_background_caution((confidence or {}).get("broad_background")),
    ):
        if extra is not None:
            cautions.append(extra)
    mass_balance: dict = {}
    try:
        amounts, source = api_router._mass_balance_amounts(task, document)
        mass_balance = api_router._mass_balance_block(
            amounts, source,
            {phase: (details or {}).get("weight_percent") for phase, details in phase_details.items()},
            phase_details, getattr(document, "predict_kwargs", None) or {}, cautions, confidence,
        )
    except Exception:
        mass_balance = {}

    raw_lst = getattr(getattr(refinement, "lst_data", None), "raw_lst", None)
    counts = raw_lst_counts(raw_lst)
    radius = instrument_radius_mm(search_settings.get("instrument_profile"))
    citations = Citations()
    phases_confidence = (confidence or {}).get("phases") or {}
    numbering = _Numbering()
    phase_results = getattr(getattr(refinement, "lst_data", None), "phases_results", None) or {}

    def short_name(phase: str) -> str:
        return _plain_name(phase, (phase_metadata.get(phase) or {}).get("spacegroup_symbol"))

    # ---- title and byline -------------------------------------------------------------
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    parts = [
        f"<h1>XRD phase analysis — {escape(str(name))}</h1>",
        f'<p class="byline">Task {task_id} · {today} · DARA {escape(_dara_version())}</p>',
    ]
    shown = _result_shown(selection, selection_label, is_final, adopted)
    if shown:
        parts.append(f'<p class="byline">{escape(shown)}</p>')

    # ---- 1 summary ----------------------------------------------------------------------
    parts.append(numbering.h2("Summary"))
    sentences = []
    if confidence:
        overall = confidence["overall"]
        sentences.append(f"<b>Overall assessment: {escape(overall['label'])}.</b>")
        if overall.get("level") != "high":
            reasons = [
                r for r in overall.get("reasons") or [] if not str(r).startswith("Phase fractions imply")
            ]
            if reasons:
                sentences.append(escape(str(reasons[0])))
    quantified = []
    for phase, details in phase_details.items():
        weight = _finite(details.get("weight_percent"))
        amount = (
            _pm(weight_text(weight, details.get("weight_esd_percent"))) + " wt%"
            if weight is not None
            else "not quantified"
        )
        quantified.append(f"{escape(short_name(phase))} ({amount})")
    if quantified:
        listing = (
            quantified[0] if len(quantified) == 1
            else ", ".join(quantified[:-1]) + " and " + quantified[-1]
        )
        sentences.append(f"Quantified phases: {listing}.")
    gof, rwp = _finite(quality.get("gof")), _finite(quality.get("rwp"))
    if rwp is not None:
        sentences.append(f"Rwp = {rwp:.2f}%" + (f", GOF = {gof:.2f}." if gof is not None else "."))
    else:
        sentences.append("Rwp is not available for this model.")
    if mass_balance.get("status") == "evaluated" and mass_balance.get("level") in {"caution", "warning"}:
        sentences.append(
            "Mass balance: the refined phases imply "
            f"{escape(str(mass_balance['observed_ratio']))}, the precursors "
            f"{escape(str(mass_balance['expected_ratio']))} "
            f"(largest deviation {mass_balance['max_deviation']:.2f})."
        )
    parts.append("<p>" + " ".join(sentences) + "</p>")

    # ---- 2 experimental and methods ------------------------------------------------------
    parts.append(numbering.h2("Experimental and methods"))
    parts.append(_methods_paragraph(
        citations=citations, filename=str(name), search_settings=search_settings,
        refinement=refinement, excluded_ranges=excluded_ranges, document=document, task=task,
        phase_names=list(phase_details), bgmn_version=counts["bgmn_version"], radius_mm=radius,
    ))
    metadata = None
    xrdml = getattr(getattr(document, "xrd_data", None), "xrdml_dict", None)
    if xrdml:
        try:
            metadata = get_xrdml_metadata(xrdml)
        except Exception:
            metadata = None
    parts.append(_conditions_table(
        numbering, document=document, task=task, search_settings=search_settings,
        refinement=refinement, excluded_ranges=excluded_ranges,
        phase_names=list(phase_details), metadata=metadata,
    ))

    # ---- 3 results ----------------------------------------------------------------------
    parts.append(numbering.h2("Results"))
    try:
        figure = H.embed_svg(
            fit_figure(
                refinement, fmt="svg", width_mm=170, excluded_ranges=excluded_ranges,
                counts_like=counts_like,
            ),
            "Observed and calculated diffraction patterns with reflection positions and difference curve",
        )
    except Exception:
        logger.warning("Matplotlib figure failed; using the built-in SVG", exc_info=True)
        figure = review_router._fit_svg(refinement, excluded_ranges)
    parts.append(f"<figure>{figure}<figcaption>{escape(FIGURE_CAPTION)}</figcaption></figure>")

    def stat(key: str) -> str:
        number = _finite(quality.get(key))
        return f"{number:.2f}" if number is not None else "—"

    displacement = _finite(
        ((confidence or {}).get("sample_displacement") or {}).get("displacement_mm")
    )
    parts.append(H.table(
        ["Rwp (%)", "Rp (%)", "Rexp (%)", "GOF", "χ²", "Durbin–Watson d", "Displacement (mm)"],
        [[stat("rwp"), stat("rp"), stat("rexp"), stat("gof"), stat("chi_squared"),
          stat("durbin_watson"), _signed(displacement, 2) if displacement is not None else "—"]],
        caption=numbering.caption("Fit statistics."),
        widths=[11, 11, 11, 10, 10, 20, 27], align="rrrrrrr", nowrap="nnnnnnn", compact=True,
    ))

    phase_rows = []
    for phase, details in phase_details.items():
        meta = phase_metadata.get(phase, {})
        weight = _pm(weight_text(details.get("weight_percent"), details.get("weight_esd_percent")))
        verdict = escape((phases_confidence.get(phase) or {}).get("label") or "—")
        phase_rows.append([
            _name_html(phase), _spacegroup_cell(meta), weight, _lattice_cell(details),
            _crystallite_cell(details, phase_results.get(phase)), verdict, _reference_cell(phase),
        ])
    parts.append(H.table(
        ["Phase", "Space group", "wt% ± esd", "Lattice parameters (Å, °)",
         "Crystallite size (nm)", "Verdict", "Reference"],
        phase_rows,
        caption=numbering.caption("Phase quantification."),
        widths=[17, 11, 12, 23, 12, 12, 13], align="llrllll", nowrap="nnnnnnn",
        cell_classes={3: "lp"},
    ))
    parts.append(
        '<p class="note">Weight percentages are normalised to the crystalline phases in the '
        "model; ± is the least-squares esd. A dash means the quantity was not refined.</p>"
    )

    # ---- 4 assessment -------------------------------------------------------------------
    parts.append(numbering.h2("Assessment"))
    parts.append(numbering.h3("Phase verdicts"))
    if confidence:
        items = _phase_notes(phases_confidence, rwp, citations, lambda p: escape(short_name(p)))
        if items:
            parts.append("<ul>" + "".join(items) + "</ul>")
        else:
            parts.append("<p>All phases are supported by every criterion.</p>")
    else:
        parts.append("<p>The confidence assessment could not be computed for this result.</p>")

    mass_parts = _mass_balance_section(mass_balance, numbering)
    if mass_parts:
        parts.append(numbering.h3("Mass balance"))
        parts.extend(mass_parts)
    peak_parts = _unexplained_peaks_section(diagnostics, numbering)
    if peak_parts:
        parts.append(numbering.h3("Unexplained peaks"))
        parts.extend(peak_parts)

    classified = isinstance(diagnostics, dict) and diagnostics.get("missing_classified") is not None
    dropped = {"mass_balance"} | (
        {"missing_peaks_remain", "minor_unexplained_peaks"} if classified else set()
    )
    remaining = [c for c in cautions if c.get("code") not in dropped and c.get("message")]
    if remaining:
        parts.append(numbering.h3("Review cautions"))
        parts.append("<ul>" + "".join(
            "<li>" + ("<b>Warning:</b> " if c.get("level") == "warning" else "")
            + escape(str(c["message"])) + "</li>"
            for c in remaining
        ) + "</ul>")

    # ---- 5 limitations ------------------------------------------------------------------
    parts.append(numbering.h2("Limitations"))
    dw = _finite(quality.get("durbin_watson"))
    parts.append(
        "<p>"
        + escape(LIMITATIONS.format(
            dw=f" (Durbin–Watson d = {dw:.2f})" if dw is not None else "",
            toby=citations.cite("toby"),
        ))
        + "</p>"
    )

    # ---- 6 user notes and references ----------------------------------------------------
    notes = (review.get("notes") or "").strip()
    if notes:
        parts.append(numbering.h2("User review notes") + f'<div class="notes">{escape(notes)}</div>')
    parts.append(
        numbering.h2("References") + citations.render_html().replace("<ol>", '<ol class="refs">', 1)
    )
    return H.page(f"DARA report — {name}", "".join(parts))
