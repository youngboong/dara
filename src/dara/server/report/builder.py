"""Assemble the paper-style English report of one refinement (F5)."""

from __future__ import annotations

import json
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
    "Weight fractions are relative to the crystalline phases included in the model; "
    "amorphous or unidentified phases are not quantified. The esd values are least-squares "
    "estimates that do not include systematic errors such as microabsorption, "
    "preferred-orientation misfit or structure-model errors, and they are underestimated when "
    "the residuals are serially correlated{dw}. Reference structures computed by density "
    "functional theory may have lattice parameters that differ from experiment by a few "
    "percent. A low Rwp alone does not establish phase identity {toby}."
)
CAUTION_SENTENCE = (
    "Automated checks flag properties of the fit for review; they are not a verdict on the result."
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
                " For computed (Materials Project) structures the lattice window was widened to "
                f"{effective[0]:+.1%} to {effective[1]:+.1%} to allow for the systematic "
                "deviation of DFT lattice parameters."
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
    instrument = (
        f"; the goniometer radius of the instrument profile is {radius_mm:g} mm"
        if radius_mm
        else ""
    )
    tmin, tmax, points, step = _data_description(refinement)
    profile = search_settings.get("instrument_profile") or NOT_RECORDED
    excluded = _excluded_clause(excluded_ranges)

    parts = [
        f"The powder X-ray diffraction pattern ({escape(filename)}) was {recorded}{instrument}. ",
        f"Data from {tmin}° to {tmax}° 2θ ({points} points, median step {step}°) were "
        f"analysed{excluded}. ",
    ]
    dara_ref = citations.cite("dara")
    parts.append(
        f"Phase identification and quantitative Rietveld refinement were performed "
        f"automatically with DARA {escape(_dara_version())} {dara_ref}, which uses "
        f"{escape('BGMN ' + bgmn_version if bgmn_version else 'BGMN')} {citations.cite('bgmn')} with the "
        f"fundamental-parameters instrument profile “{escape(str(profile))}”. "
    )
    sources, user_cifs, e_hull = _database_sources(document, task, _phase_sources(phase_names))
    items = []
    if "cod" in sources:
        items.append(f"the Crystallography Open Database (COD) {citations.cite('cod')}")
    if "mp" in sources:
        item = f"the Materials Project {citations.cite('mp')}"
        if e_hull is not None:
            item += f", with an energy-above-hull cutoff of {e_hull:.3f} eV/atom"
        items.append(item)
    if user_cifs:
        items.append("user-supplied CIF files")
    if items:
        listing = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        parts.append(f"Candidate crystal structures were taken from {listing}. ")
        parts.append(f"Crystal structures were processed with pymatgen {citations.cite('pymatgen')}. ")
    else:
        parts.append("The sources of the candidate crystal structures were not recorded. ")
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
    parts.append(
        "Weight fractions were calculated from the refined scale factors and normalised to the "
        "crystalline phases in the model."
    )
    return "<p>" + "".join(parts) + "</p>"


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------

def _hamilton_cell(evidence: dict) -> str:
    hamilton = evidence.get("hamilton")
    if not hamilton:
        return "—"
    outcome = "significant" if hamilton.get("significant") else "not significant"
    return escape(
        f"R-ratio {hamilton['ratio']:.3f} vs. critical value {hamilton['critical']:.3f} "
        f"(α = {hamilton['alpha']:g}): {outcome}"
    )


def _alternative_cell(evidence: dict) -> str:
    alternative = evidence.get("alternative")
    if not alternative:
        return "None within tolerance"
    number = alternative.get("candidate_number")
    who = f"Candidate {number}" if number is not None else "Candidate"
    replaced = ", ".join(alternative.get("replaced_by") or []) or "other phases"
    return escape(
        f"{who} (Rwp {alternative['rwp']:.2f}%, {alternative['delta_rwp']:+.2f}) "
        f"contains {replaced} instead of this phase"
    )


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


def _unexplained_peaks_section(diagnostics) -> list[str]:
    """Table of the classified unexplained observed peaks (empty without classification)."""
    try:
        from dara.server import peak_significance as ps

        entries = [
            e for e in (diagnostics or {}).get("missing_classified") or [] if isinstance(e, dict)
        ]
        if not entries:
            return []
        shown = [e for e in entries if e.get("class") != "noise"]
        noise = len(entries) - len(shown)
        if not shown:
            return []
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
            "Observed peaks not explained by the fitted phases were classified by their height "
            "relative to the strongest peak and by their signal-to-noise ratio. Only significant "
            f"peaks (at least {ps.SIGNIFICANT_REL * 100:.0f}% of the strongest peak and "
            f"S/N ≥ {ps.SIGNIFICANT_SNR:.0f}) lower the overall assessment."
        )
        out = [
            "<h3>Unexplained observed peaks</h3>",
            f"<p>{escape(intro)}</p>",
            H.table(["2θ (°)", "% of strongest peak", "S/N", "Class", "Interpretation"], rows),
        ]
        if noise:
            out.append(
                f"<p>{escape(f'{noise} further weak feature(s) are within the noise and are not listed.')}</p>"
            )
        return out
    except Exception:
        logger.debug("Unexplained-peak table failed", exc_info=True)
        return []


def build_report_html(task_id: int, task: dict, document, selection, review: dict) -> str:
    """Return the complete report as one standalone HTML document."""
    from dara.plot import phase_display_name
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
    if is_final:
        chosen = "Minimum-Rwp final result"
    else:
        chosen = f"Candidate source index {selection} (stored result {selection + 1})"
    adopted = review.get("selection") is not None and (
        review["selection"] == ("best" if selection is None else selection)
    )
    state = "User-adopted result" if adopted else "Not marked as the adopted result"

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

    raw_lst = getattr(getattr(refinement, "lst_data", None), "raw_lst", None)
    counts = raw_lst_counts(raw_lst)
    radius = instrument_radius_mm(search_settings.get("instrument_profile"))
    citations = Citations()
    phases_confidence = (confidence or {}).get("phases") or {}

    # ---- title and byline -------------------------------------------------------------
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"<h1>X-ray powder diffraction analysis of {escape(str(name))}</h1>",
        f'<p class="byline">Task {task_id} · Generated {generated} · {escape(chosen)} · '
        f"{state}<br>Minimum Rwp retained in the analysis: "
        f"{escape(review_router._number(getattr(document, 'best_rwp', None)))}%</p>",
    ]

    # ---- summary ----------------------------------------------------------------------
    summary = ["<h2>Summary</h2>"]
    if confidence:
        overall = confidence["overall"]
        summary.append(
            f'<p><span class="verdict">Overall assessment: {escape(overall["label"])}.</span> '
            + " ".join(escape(reason) for reason in overall["reasons"])
            + "</p>"
        )
    sentences = []
    for phase, details in phase_details.items():
        meta = phase_metadata.get(phase, {})
        weight = _finite(details.get("weight_percent"))
        esd = _finite(details.get("weight_esd_percent"))
        symbol = meta.get("spacegroup_symbol") or "space group not recorded"
        number = meta.get("spacegroup_number")
        sg = f"{symbol}, No. {number}" if number else str(symbol)
        verdict = (phases_confidence.get(phase) or {}).get("label")
        amount = (
            f"was quantified at {weight_text(weight, esd)} wt%"
            if weight is not None
            else "could not be quantified"
        )
        sentences.append(
            f"{escape(_plain_label(phase))} ({escape(str(sg))}) {amount}"
            + (f" ({escape(verdict)})." if verdict else ".")
        )
    gof = quality.get("gof")
    rwp = _finite(quality.get("rwp"))
    gof_text = f"GOF = {gof:.2f}" if _finite(gof) is not None else "GOF not available"
    model_word = "final" if is_final else "selected candidate"
    fit_sentence = (
        f"The {model_word} model reached Rwp = {rwp:.2f}% ({gof_text})."
        if rwp is not None
        else "Rwp is not available for this model."
    )
    summary.append("<p>" + " ".join(sentences + [escape(fit_sentence)]) + "</p>")
    parts += summary

    # ---- experimental and methods -----------------------------------------------------
    parts.append("<h2>Experimental and methods</h2>")
    methods = _methods_paragraph(
        citations=citations, filename=str(name), search_settings=search_settings,
        refinement=refinement, excluded_ranges=excluded_ranges, document=document, task=task,
        phase_names=list(phase_details), bgmn_version=counts["bgmn_version"], radius_mm=radius,
    )
    parts.append(methods)
    conditions = {
        "Analysis mode": getattr(document, "analysis_mode", None),
        "Precursor formulas": getattr(document, "precursors", None),
        "Elements": getattr(document, "elements", None),
        "Candidate databases": getattr(document, "candidate_sources", None),
        "Search / analysis settings": getattr(document, "search_kwargs", None),
        "Final refinement settings": getattr(document, "final_refinement_params", None),
        "Reaction prediction settings (temperature in K)": getattr(document, "predict_kwargs", None),
        "Submitted": task.get("submitted_time"),
        "Completed": task.get("end_time"),
        "Submitted by": task.get("user"),
    }
    metadata = getattr(document.xrd_data, "xrdml_dict", None)
    if metadata:
        conditions["Recorded acquisition metadata"] = get_xrdml_metadata(metadata)
    condition_rows = []
    for label, value in conditions.items():
        if value is None or value == [] or value == {}:
            continue
        value_text = (
            json.dumps(review_router._public_settings(value), ensure_ascii=False, default=str, indent=2)
            if isinstance(value, (list, dict))
            else str(value)
        )
        condition_rows.append([escape(label), f"<pre>{escape(value_text)}</pre>"])
    parts.append("<h3>Recorded settings</h3>")
    parts.append(H.table(["Condition", "Recorded value"], condition_rows))

    # ---- results ----------------------------------------------------------------------
    parts.append("<h2>Results</h2>")
    phase_labels = {phase: _plain_label(phase) for phase in phase_details}
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

    quality_rows = [
        [label, escape(review_router._number(quality.get(key)))]
        for label, key in (
            ("Rwp (%)", "rwp"), ("Rp (%)", "rp"), ("Rexp (%)", "rexp"),
            ("Goodness of fit: Rwp/Rexp", "gof"), ("χ²", "chi_squared"),
            ("Durbin–Watson d", "durbin_watson"), ("2θ minimum (°)", "two_theta_min"),
            ("2θ maximum (°)", "two_theta_max"), ("Fit data points", "data_points"),
        )
    ]
    parts.append("<h3>Fit statistics</h3>" + H.table(["Metric", "Value"], quality_rows))

    phase_rows = []
    for phase, details in phase_details.items():
        meta = phase_metadata.get(phase, {})
        lattice = "<br>".join(
            f"{escape(key)} = {escape(review_router._parameter(value))}"
            for key, value in details.get("lattice_parameters", {}).items()
        ) or "—"
        fraction = weight_text(details.get("weight_percent"), details.get("weight_esd_percent"))
        symbol = meta.get("spacegroup_symbol") or "—"
        number = meta.get("spacegroup_number")
        sg = f"{symbol}" + (f" (No. {number})" if number else "")
        sizes = "<br>".join(
            f"{escape(str(item.get('direction', '')))}: "
            f"{escape(review_router._number(item.get('value'), 4))}"
            + (
                f" ± {escape(review_router._number(item.get('esd'), 2))}"
                if item.get("esd") is not None
                else ""
            )
            for item in details.get("grain_sizes") or []
        ) or "—"
        verdict = escape((phases_confidence.get(phase) or {}).get("label") or "—")
        phase_rows.append([
            phase_display_name(phase), escape(sg), escape(fraction), lattice, sizes,
            f'<span class="verdict">{verdict}</span>',
            review_router._provenance(phase) + f"<br><small>{escape(phase)}</small>",
        ])
    parts.append(
        "<h3>Phase quantification and crystallography</h3>"
        + H.table(
            ["Phase", "Space group (No.)", "wt% ± esd", "Lattice parameters",
             "Crystallite size (nm)", "Verdict", "Reference structure"],
            phase_rows,
        )
    )
    parts.append(
        '<p class="muted">Weight percentages are normalized among the fitted crystalline phases, '
        "not absolute sample fractions or amorphous content. ± values are refinement estimated "
        "standard deviations where available, not total measurement uncertainty. Only "
        "independently reported lattice parameters are listed; omitted symmetry-constrained "
        "parameters are not missing measurements.</p>"
    )

    # ---- confidence ---------------------------------------------------------------------
    parts.append("<h2>Confidence assessment and review cautions</h2>")
    if confidence:
        rows = []
        any_hamilton = False
        for phase, entry in phases_confidence.items():
            evidence = entry.get("evidence") or {}
            any_hamilton = any_hamilton or bool(evidence.get("hamilton"))
            presence = evidence.get("presence_fraction")
            presence_text = (
                f"{presence * evidence['n_candidates']:.0f} of {evidence['n_candidates']} candidates"
                if presence is not None
                else "Not applicable"
            )
            sigma = evidence.get("weight_sigma")
            weight = evidence.get("weight_percent")
            weight_cell = (
                f"{weight:.1f} wt%" + (f", {sigma:.1f} σ" if sigma is not None else "")
                if weight is not None
                else "—"
            )
            computed = "Computed (DFT)" if evidence.get("computed_structure") else "Experimental or user-supplied"
            if evidence.get("lattice_change_pct") is not None:
                computed += f"; lattice change {evidence['lattice_change_pct']:.1f}%"
            rows.append([
                escape(_plain_label(phase)), escape(presence_text), escape(weight_cell),
                _alternative_cell(evidence), _hamilton_cell(evidence), escape(computed),
                f'<span class="verdict">{escape(entry["label"])}</span><br>'
                + "<br>".join(f"<small>{escape(reason)}</small>" for reason in entry["reasons"]),
            ])
        intro = (
            "The evidence for each phase was assessed from its presence in the candidate models, "
            "the ratio of its weight fraction to the estimated standard deviation, the existence "
            "of an alternative model that fits almost as well, and the nature of the reference "
            "structure."
        )
        if any_hamilton:
            intro += (
                " Where a candidate without the phase was available, the significance of its "
                f"removal was tested with Hamilton's R-ratio test {citations.cite('hamilton')}."
            )
        parts.append(f"<p>{escape(intro)}</p>")
        parts.append(
            H.table(
                ["Phase", "Presence", "Weight / σ", "Alternative model", "Hamilton test",
                 "Reference structure", "Verdict"],
                rows,
            )
        )
        displacement = confidence.get("sample_displacement") or {}
        if displacement.get("message"):
            parts.append(f"<p>{escape(displacement['message'])}</p>")
    else:
        parts.append("<p>The confidence assessment could not be computed for this result.</p>")
    parts.extend(_unexplained_peaks_section(diagnostics))
    if cautions:
        parts.append(
            H.table(
                ["Level", "Check", "Detail"],
                [
                    [escape(str(item.get("level", ""))), escape(str(item.get("code", ""))),
                     escape(str(item.get("message", "")))]
                    for item in cautions
                ],
            )
        )
    else:
        parts.append("<p>No automated cautions.</p>")
    parts.append(f'<p class="muted">{escape(CAUTION_SENTENCE)}</p>')

    # ---- limitations ----------------------------------------------------------------------
    parts.append("<h2>Limitations</h2>")
    dw = _finite(quality.get("durbin_watson"))
    parts.append(
        "<p>"
        + escape(
            LIMITATIONS.format(
                dw=f" (Durbin–Watson d = {dw:.2f})" if dw is not None else "",
                toby=citations.cite("toby"),
            )
        )
        + "</p>"
    )

    # ---- user notes and references ---------------------------------------------------------
    notes = escape(review.get("notes") or "No review notes recorded.")
    updated = escape(review.get("updated_at") or "Not saved")
    parts.append(
        f'<h2>User review notes</h2><p class="muted">Last saved: {updated}. Notes apply to this '
        f'task; report selection: {escape(chosen)}.</p><div class="notes">{notes}</div>'
    )
    parts.append("<h2>References</h2>" + citations.render_html().replace("<ol>", '<ol class="refs">', 1))
    return H.page(f"DARA report — {name}", "".join(parts))
