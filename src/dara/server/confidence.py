"""Evidence-based confidence for refined phases and whole refinements (F3).

Every statement produced here is a statement about evidence ("present in 2 of 5
candidate models"), never a claim that a result is right or wrong. The public
entry points never raise: a criterion that cannot be evaluated is reported as
``None`` and skipped; a total failure yields ``None`` so callers simply omit the
``confidence`` key.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

logger = logging.getLogger("dara.server.confidence")

SCHEMA = 1

PRESENCE_CONFIDENT = 0.6
SIGMA_CONFIDENT = 3.0
SIGMA_WEAK = 2.0
MIN_WEIGHT_PERCENT = 1.0
ALT_RWP_ABS = 0.5  # alternative if Rwp_alt - Rwp_target <= max(ALT_RWP_ABS, ALT_RWP_REL * Rwp_target)
ALT_RWP_REL = 0.05
HAMILTON_ALPHA = 0.01
COMPUTED_LATTICE_CHANGE_PCT = 1.5
GOF_HIGH = 3.0
GOF_EXPERT = 4.0
RWP_EXPERT = 25.0
MISSING_HIGH_MAX = 1
MISSING_EXPERT_MIN = 5
WEAK_MAJOR_WT = 5.0
FAILED_FRACTION_EXPERT = 0.5
SCREENING_MISSING_EXPERT_FRACTION = 0.25
DISP_INFO_MM, DISP_WARNING_MM, DISP_SEVERE_MM = 0.3, 1.0, 2.0
DISP_INFO_DEG, DISP_WARNING_DEG, DISP_SEVERE_DEG = 0.17, 0.55, 1.1  # |d2theta| at 2theta = 30 deg
EPS2_BOUND = 0.05
EPS2_AT_BOUND = 0.9 * EPS2_BOUND  # default refinement window "0_-0.05^0.05"
BROAD_BG_REL_HEIGHT = 0.15
BROAD_BG_MIN_WIDTH_DEG = 8.0

DISPLACEMENT_CONVENTION = "s = EPS2 x R (Delta2theta = -2 EPS2 cos theta)"
_DEVICE_DIR = Path(__file__).resolve().parents[1] / "data" / "BGMN-Templates" / "Devices"
_RADIUS_LINE = re.compile(r"^\s*R\s*=\s*([0-9.]+)\s*$")
_LST_COUNTS = re.compile(
    r"BGMN version ([\w.]+), (\d+) measured points, (\d+) peaks, (\d+) parameters"
)

LEVEL_LABELS = {"confident": "Confident", "ambiguous": "Ambiguous", "weak": "Weak evidence"}
OVERALL_LABELS = {
    "high": "High confidence",
    "check": "Check recommended",
    "expert": "Expert review recommended",
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _num(value) -> float | None:
    """Finite float or ``None`` (tuples take their first element)."""
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value_esd(value) -> tuple[float | None, float | None]:
    if isinstance(value, (tuple, list)):
        if len(value) >= 2:
            return _num(value[0]), _num(value[1])
        return (_num(value[0]) if value else None), None
    return _num(value), None


def _label(name: str) -> str:
    try:
        from dara.server.fit_cautions import _display_name

        return _display_name(name)
    except Exception:
        return str(name)


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value, digits=3):
    number = _num(value)
    return None if number is None else round(number, digits)


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def instrument_radius_mm(instrument_profile: str | None) -> float | None:
    """Goniometer radius (mm) from the bundled BGMN device ``.sav`` file, else ``None``."""
    if not instrument_profile or not re.fullmatch(r"[\w.\- ]+", str(instrument_profile)):
        return None
    try:
        stem = str(instrument_profile)
        for suffix in (".sav", ".SAV"):
            path = _DEVICE_DIR / f"{stem}{suffix}"
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = _RADIUS_LINE.match(line)
                if match:
                    return float(match.group(1))
    except (OSError, ValueError):
        return None
    return None


def raw_lst_counts(raw_lst: str | None) -> dict:
    """Parse the BGMN version line of a ``.lst`` listing."""
    empty = {"bgmn_version": None, "points": None, "peaks": None, "parameters": None}
    if not raw_lst or not isinstance(raw_lst, str):
        return empty
    match = _LST_COUNTS.search(raw_lst)
    if not match:
        return empty
    return {
        "bgmn_version": match.group(1),
        "points": int(match.group(2)),
        "peaks": int(match.group(3)),
        "parameters": int(match.group(4)),
    }


@lru_cache(maxsize=512)
def _reduced_formula(token: str) -> str | None:
    try:
        from pymatgen.core import Composition

        return Composition(token).reduced_formula
    except Exception:
        return None


def phase_identity(name: str, metadata: dict | None = None) -> tuple[str | None, int | None]:
    """Reduced formula and database space-group number of a refined phase name.

    The space group comes from the ``<formula>_<number>_(...)`` token of the reference name
    (the database record). The refined BGMN setting in ``metadata`` can differ for the same
    structure (hematite refined as C12/c1, number 15), so it is only a fallback.
    """
    parts = str(name).split("_")
    formula = _reduced_formula(parts[0]) if parts and parts[0] else None
    number = None
    if len(parts) > 1 and parts[1].isdigit():
        number = int(parts[1])
    if number is None:
        number = _int_or_none((metadata or {}).get("spacegroup_number"))
    return formula, number


def _crystal_family(number: int | None) -> str | None:
    """Coarse crystal family of a space-group number (setting/subgroup noise stays inside it)."""
    if number is None:
        return None
    for upper, family in ((2, "triclinic"), (15, "monoclinic"), (74, "orthorhombic"),
                          (142, "tetragonal"), (194, "hexagonal"), (230, "cubic")):
        if number <= upper:
            return family
    return None


def hamilton_test(
    rwp_full, rwp_restricted, n_points, params_full, params_restricted, alpha=HAMILTON_ALPHA
) -> dict | None:
    """Hamilton R-ratio test of the full model against a restricted (nested) model."""
    try:
        rwp_full, rwp_restricted = float(rwp_full), float(rwp_restricted)
        n_points, params_full = int(n_points), int(params_full)
        params_restricted = int(params_restricted)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(rwp_full) and math.isfinite(rwp_restricted)) or rwp_full <= 0:
        return None
    b = params_full - params_restricted
    dof = n_points - params_full
    if b < 1 or dof < 1:
        return None
    try:
        from scipy.stats import f as f_distribution

        critical = math.sqrt(b / dof * float(f_distribution.ppf(1 - alpha, b, dof)) + 1)
    except Exception:
        return None
    ratio = rwp_restricted / rwp_full
    return {
        "ratio": float(ratio),
        "critical": float(critical),
        "significant": bool(ratio > critical),
        "b": int(b),
        "dof": int(dof),
        "alpha": float(alpha),
    }


def _eps2_from(lst_data):
    for source in (lst_data, getattr(lst_data, "model_extra", None)):
        if source is None:
            continue
        if isinstance(source, dict):
            value = source.get("EPS2")
        else:
            value = getattr(source, "EPS2", None)
        if value is not None:
            return value
    return None


def sample_displacement(lst_data, instrument_profile: str | None) -> dict:
    """Interpret the refined BGMN EPS2 parameter as specimen displacement."""
    eps2, esd = _value_esd(_eps2_from(lst_data))
    radius = instrument_radius_mm(instrument_profile)
    result = {
        "eps2": eps2,
        "eps2_esd": esd,
        "radius_mm": radius,
        "displacement_mm": None,
        "displacement_mm_esd": None,
        "two_theta_shift_deg_at_30": None,
        "at_bound": False,
        "level": "unknown",
        "convention": DISPLACEMENT_CONVENTION,
        "message": "Refined specimen displacement is not available for this result.",
    }
    if eps2 is None:
        return result
    shift = -2 * eps2 * math.cos(math.radians(15)) * 180 / math.pi
    result["two_theta_shift_deg_at_30"] = shift
    if radius is not None:
        result["displacement_mm"] = eps2 * radius
        if esd is not None:
            result["displacement_mm_esd"] = abs(esd * radius)
    at_bound = abs(eps2) >= EPS2_AT_BOUND
    result["at_bound"] = at_bound
    if radius is not None:
        magnitude, ladder = abs(eps2 * radius), (DISP_INFO_MM, DISP_WARNING_MM, DISP_SEVERE_MM)
    else:
        magnitude, ladder = abs(shift), (DISP_INFO_DEG, DISP_WARNING_DEG, DISP_SEVERE_DEG)
    if at_bound or magnitude > ladder[2]:
        level = "severe"
    elif magnitude > ladder[1]:
        level = "warning"
    elif magnitude > ladder[0]:
        level = "info"
    else:
        level = "ok"
    result["level"] = level
    if radius is None:
        result["message"] = (
            f"Refined EPS2 = {eps2:.5f} (2θ shift {shift:+.3f}° at 30° 2θ); the goniometer "
            "radius of this profile is not known, so no displacement in mm is given."
        )
        return result
    base = f"Refined specimen displacement: {eps2 * radius:+.2f} mm (EPS2 = {eps2:.5f})"
    if level in {"ok", "info"}:
        message = base + "."
    elif level == "warning":
        message = base + " is large; check specimen height and the selected instrument profile."
    elif at_bound:
        message = (
            base + " reached the refinement limit; peak positions, lattice parameters and "
            "phase identification may be biased."
        )
    else:
        message = (
            base + " is very large; peak positions, lattice parameters and phase "
            "identification may be biased."
        )
    result["message"] = message
    return result


def broad_background(x, y_obs, y_bkg) -> dict:
    """Detect a broad (amorphous-like) hump in the refined background."""
    none = {"detected": False, "width_deg": None, "relative_height": None}
    try:
        x = np.asarray(x, dtype=float)
        y_obs = np.asarray(y_obs, dtype=float)
        y_bkg = np.asarray(y_bkg, dtype=float)
        if not (x.shape == y_obs.shape == y_bkg.shape):
            return none
        keep = np.isfinite(x) & np.isfinite(y_obs) & np.isfinite(y_bkg)
        x, y_obs, y_bkg = x[keep], y_obs[keep], y_bkg[keep]
        if x.size < 20:
            return none
        order = np.argsort(x)
        x, y_obs, y_bkg = x[order], y_obs[order], y_bkg[order]
        n_edge = max(5, int(round(0.02 * x.size)))
        x0, y0 = float(np.median(x[:n_edge])), float(np.median(y_bkg[:n_edge]))
        x1, y1 = float(np.median(x[-n_edge:])), float(np.median(y_bkg[-n_edge:]))
        if x1 <= x0:
            return none
        baseline = y0 + (x - x0) * (y1 - y0) / (x1 - x0)
        excess = y_bkg - baseline
        reference = float(np.percentile(y_obs - y_bkg, 99.5))
        if not reference > 0:
            return none
        peak = float(excess.max())
        if not peak > 0:
            return {"detected": False, "width_deg": None, "relative_height": 0.0}
        above = excess > 0.5 * peak
        width = 0.0
        start = None
        for index, flag in enumerate(above):
            if flag and start is None:
                start = index
            if (not flag or index == above.size - 1) and start is not None:
                end = index if flag else index - 1
                width = max(width, float(x[end] - x[start]))
                start = None
        detected = bool(
            peak > BROAD_BG_REL_HEIGHT * reference and width > BROAD_BG_MIN_WIDTH_DEG
        )
        return {
            "detected": detected,
            "width_deg": width if detected else None,
            "relative_height": peak / reference,
        }
    except Exception:
        logger.debug("Broad-background check skipped", exc_info=True)
        return none


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

@dataclass
class CandidateEvidence:
    key: str  # "final" or "candidate:<source_index>"
    number: int | None  # 1-based displayed candidate number (None for final)
    source_index: int | None
    rwp: float | None
    gof: float | None
    identities: dict[str, tuple[str | None, int | None]]  # phase name -> identity
    phase_details: dict  # _phase_refinement_details() output
    refinement: object | None = None
    lattice_flags: list[dict] = field(default_factory=list)  # [{"phase", "parameter"}]
    reference_lattices: dict[str, dict] = field(default_factory=dict)  # phase -> {"a","b","c"} (Å)
    refined_spacegroups: dict[str, int | None] = field(default_factory=dict)  # BGMN setting


def _phase_keys(candidate: CandidateEvidence) -> dict[str, tuple]:
    """Formula-level key of every phase: ``(reduced formula, ordinal)``.

    Phases are compared by reduced formula, not by space group: the same compound listed in
    another setting or as a subgroup (Fe2O3 R-3c / C2/c, Y2O3 Ia-3 / I2_13) is the same phase,
    and an alternative that only swaps the structure model is not a model "without this phase".
    The ordinal separates genuinely coexisting polymorphs of one formula in one model.
    """

    def group_of(identity):
        return (identity or (None, None))[1]

    keys: dict[str, tuple] = {}
    seen: dict[str, int] = {}
    ordered = sorted(
        candidate.identities.items(),
        key=lambda item: (group_of(item[1]) is None, group_of(item[1]) or 0, item[0]),
    )
    for name, identity in ordered:
        formula = identity[0] if identity and identity[0] is not None else None
        base = formula if formula is not None else "name:" + str(name)
        ordinal = seen.get(base, 0)
        seen[base] = ordinal + 1
        keys[name] = (base, ordinal)
    return keys


def _identity_set(candidate: CandidateEvidence) -> frozenset:
    return frozenset(_phase_keys(candidate).values())


def _is_computed(name: str) -> bool:
    try:
        from dara.structure_quality import reference_provenance  # W1 helper (optional)

        return bool(reference_provenance(name).get("computed"))
    except Exception:
        return "_(mp_" in name or "_(mp-" in name


def _lattice_change_pct(target: CandidateEvidence, phase: str) -> float | None:
    reference = target.reference_lattices.get(phase)
    if not reference:
        return None
    try:
        from dara.server.table_export import lattice_with_symmetry

        details = target.phase_details.get(phase) or {}
        # The refined lattice follows the setting BGMN used, not the database label.
        group = target.refined_spacegroups.get(phase)
        if group is None:
            group = target.identities.get(phase, (None, None))[1]
        refined = lattice_with_symmetry(details.get("lattice_parameters"), group)
    except Exception:
        return None
    changes = []
    for key in ("a", "b", "c"):
        ref, new = _num(reference.get(key)), refined.get(key, (None, None))[0]
        new = _num(new)
        if ref and new:
            changes.append(abs(new - ref) / ref * 100)
    if not changes:
        return None
    change = max(changes)
    # A change this large almost certainly means a different cell setting, not refinement.
    return change if change <= 12.0 else None


def _hamilton_for(target: CandidateEvidence, phase: str, displayed) -> dict | None:
    try:
        wanted = _identity_set(target) - {_phase_keys(target)[phase]}
        if not wanted:
            return None
        full = raw_lst_counts(getattr(getattr(target.refinement, "lst_data", None), "raw_lst", None))
        if full["points"] is None or full["parameters"] is None or target.rwp is None:
            return None
        best = None
        for candidate in displayed:
            if candidate is target or _identity_set(candidate) != wanted or candidate.rwp is None:
                continue
            restricted = raw_lst_counts(
                getattr(getattr(candidate.refinement, "lst_data", None), "raw_lst", None)
            )
            if restricted["parameters"] is None:
                continue
            outcome = hamilton_test(
                target.rwp, candidate.rwp, full["points"], full["parameters"], restricted["parameters"]
            )
            if outcome is not None and (best is None or candidate.rwp < best[0]):
                best = (candidate.rwp, outcome)
        return best[1] if best else None
    except Exception:
        return None


def _alternative_for(target: CandidateEvidence, phase: str, displayed) -> dict | None:
    if target.rwp is None:
        return None
    own = _identity_set(target)
    ident = _phase_keys(target)[phase]
    allowed = max(ALT_RWP_ABS, ALT_RWP_REL * target.rwp)
    best = None
    for candidate in displayed:
        if candidate is target or candidate.rwp is None:
            continue
        keys = _identity_set(candidate)
        if keys == own or ident in keys:
            continue  # same phases (possibly in another setting) or this phase is still there
        delta = candidate.rwp - target.rwp
        if delta <= allowed and (best is None or candidate.rwp < best.rwp):
            best = candidate
    if best is None:
        return None
    best_keys = _phase_keys(best)
    replaced = [_label(name) for name in best.identities if best_keys[name] not in own]
    number = best.number if best.number is not None else (
        best.source_index + 1 if best.source_index is not None else None
    )
    return {
        "candidate_number": number,
        "source_index": best.source_index,
        "rwp": float(best.rwp),
        "delta_rwp": float(best.rwp - target.rwp),
        "replaced_by": replaced,
    }


def _structure_model_alternative(target: CandidateEvidence, phase: str, displayed) -> dict | None:
    """Best near-equal candidate that holds the same compound with another space group."""
    if target.rwp is None:
        return None
    ident = _phase_keys(target)[phase]
    own_group = (target.identities.get(phase) or (None, None))[1]
    if own_group is None:
        return None
    allowed = max(ALT_RWP_ABS, ALT_RWP_REL * target.rwp)
    best = None
    for candidate in displayed:
        if candidate is target or candidate.rwp is None or candidate.rwp - target.rwp > allowed:
            continue
        for name, key in _phase_keys(candidate).items():
            group = (candidate.identities.get(name) or (None, None))[1]
            if key != ident or group is None or group == own_group:
                continue
            if best is None or candidate.rwp < best[0].rwp:
                best = (candidate, group)
    if best is None:
        return None
    candidate, group = best
    number = candidate.number if candidate.number is not None else (
        candidate.source_index + 1 if candidate.source_index is not None else None
    )
    return {
        "candidate_number": number,
        "source_index": candidate.source_index,
        "rwp": float(candidate.rwp),
        "delta_rwp": float(candidate.rwp - target.rwp),
        "spacegroup_number": int(group),
        "own_spacegroup_number": int(own_group),
        # A different crystal family is a real polymorph; within one family it is a
        # setting/subgroup variant of the same structure type.
        "polymorph": _crystal_family(group) != _crystal_family(own_group),
    }


def _phase_assessment(target: CandidateEvidence, phase: str, displayed) -> dict:
    ident = _phase_keys(target)[phase]
    details = target.phase_details.get(phase) or {}
    weight = _num(details.get("weight_percent"))
    esd = _num(details.get("weight_esd_percent"))
    sigma = weight / esd if (weight is not None and esd) else None

    n_candidates = len(displayed)
    presence = None
    containing = 0
    if n_candidates > 1:
        containing = sum(1 for c in displayed if ident in _identity_set(c))
        presence = containing / n_candidates

    try:
        alternative = _alternative_for(target, phase, displayed)
    except Exception:
        alternative = None
    try:
        model_alt = _structure_model_alternative(target, phase, displayed)
    except Exception:
        model_alt = None
    hamilton = _hamilton_for(target, phase, displayed)
    computed = _is_computed(phase)
    at_bound = any(flag.get("phase") == phase for flag in target.lattice_flags or [])
    change = _lattice_change_pct(target, phase)

    weak = (
        (weight is not None and weight < MIN_WEIGHT_PERCENT)
        or (sigma is not None and sigma < SIGMA_WEAK)
        or at_bound
    )
    confident = (
        not weak
        and (presence is None or presence >= PRESENCE_CONFIDENT)
        and (sigma is None or sigma >= SIGMA_CONFIDENT)
        and alternative is None
        and not (model_alt is not None and model_alt["polymorph"])
        and (hamilton is None or hamilton["significant"])
        and not (computed and change is not None and change > COMPUTED_LATTICE_CHANGE_PCT)
    )
    level = "weak" if weak else ("confident" if confident else "ambiguous")

    reasons: list[str] = []
    if at_bound:
        reasons.append("A lattice parameter ended at its refinement limit.")
    if weight is not None and weight < MIN_WEIGHT_PERCENT:
        reasons.append(f"Weight fraction {weight:.1f} wt% is below {MIN_WEIGHT_PERCENT:g} wt%.")
    if sigma is not None and sigma < SIGMA_CONFIDENT:
        reasons.append(
            f"Weight fraction is {sigma:.1f} times its estimated standard deviation."
        )
    if presence is not None and presence < PRESENCE_CONFIDENT:
        reasons.append(f"Present in {containing} of {n_candidates} candidate models.")
    if alternative is not None:
        number = alternative["candidate_number"]
        who = f"Candidate {number}" if number is not None else "An alternative candidate"
        reasons.append(
            f"{who} without this phase fits almost as well "
            f"(Rwp {alternative['rwp']:.1f}% vs {target.rwp:.1f}%)."
        )
    model_note = None
    if model_alt is not None:
        number = model_alt["candidate_number"]
        who = f"Candidate {number}" if number is not None else "An alternative candidate"
        kind = "polymorph" if model_alt["polymorph"] else "structure model"
        model_note = (
            f"{who} fits almost as well (Rwp {model_alt['rwp']:.1f}% vs {target.rwp:.1f}%) "
            f"with a different {kind} of this phase (space group "
            f"{model_alt['spacegroup_number']} instead of {model_alt['own_spacegroup_number']}); "
            "the compound itself is present in both."
        )
        reasons.append(model_note)
    if hamilton is not None and not hamilton["significant"]:
        reasons.append(
            "Removing this phase does not increase Rwp significantly "
            f"(Hamilton test, α = {hamilton['alpha']:g})."
        )
    if computed and change is not None and change > COMPUTED_LATTICE_CHANGE_PCT:
        reasons.append(
            "Reference structure is computed (Materials Project); its lattice changed by "
            f"{change:.1f}% on refinement."
        )
    if level == "confident":
        parts = []
        if presence is not None:
            parts.append(
                "Present in all candidate models"
                if containing == n_candidates
                else f"Present in {containing} of {n_candidates} candidate models"
            )
        if weight is not None:
            text = f"weight fraction {weight:.1f}"
            if esd is not None:
                text += f" ± {esd:.1f}"
            parts.append(text + " wt%")
        if hamilton is not None and hamilton["significant"]:
            parts.append(
                f"removing it increases Rwp significantly (Hamilton test, α = {hamilton['alpha']:g})"
            )
        if parts:
            sentence = "; ".join(parts)
            reasons = [sentence[0].upper() + sentence[1:] + "."]
            if model_note:
                reasons.append(model_note)
    if not reasons:
        reasons.append("No criterion could be evaluated for this phase.")
    return {
        "level": level,
        "label": LEVEL_LABELS[level],
        "reasons": reasons[:4],
        "evidence": {
            "presence_fraction": _round(presence, 3),
            "n_candidates": n_candidates,
            "weight_percent": _round(weight, 3),
            "weight_sigma": _round(sigma, 2),
            "alternative": alternative,
            "structure_model_alternative": model_alt,
            "hamilton": hamilton,
            "computed_structure": bool(computed),
            "lattice_at_bound": bool(at_bound),
            "lattice_change_pct": _round(change, 2),
        },
        "_weight": weight,
    }


def evaluate_confidence(
    target: CandidateEvidence,
    displayed: list[CandidateEvidence],
    *,
    counts_like: bool | None,
    missing_peaks: int | None,
    search_summary: dict | None,
    instrument_profile: str | None,
) -> dict | None:
    """Return the ``confidence`` JSON block for ``target`` (``None`` on total failure)."""
    try:
        displayed = list(displayed or [])
        phases: dict[str, dict] = {}
        weights: dict[str, float | None] = {}
        for phase in target.identities:
            try:
                assessed = _phase_assessment(target, phase, displayed)
            except Exception:
                logger.debug("Phase confidence failed for %s", phase, exc_info=True)
                continue
            weights[phase] = assessed.pop("_weight")
            phases[phase] = assessed

        lst_data = getattr(target.refinement, "lst_data", None)
        try:
            displacement = sample_displacement(lst_data, instrument_profile)
        except Exception:
            displacement = sample_displacement(None, instrument_profile)
        plot_data = getattr(target.refinement, "plot_data", None)
        try:
            background = broad_background(
                plot_data.x, plot_data.y_obs, plot_data.y_bkg
            )
        except Exception:
            background = {"detected": False, "width_deg": None, "relative_height": None}

        overall = _overall(
            target, phases, weights, displacement, background,
            counts_like=counts_like, missing_peaks=missing_peaks, search_summary=search_summary,
        )
        return {
            "schema": SCHEMA,
            "overall": overall,
            "phases": phases,
            "sample_displacement": displacement,
            "broad_background": background,
        }
    except Exception:
        logger.warning("Confidence evaluation failed", exc_info=True)
        return None


def _overall(target, phases, weights, displacement, background, *, counts_like, missing_peaks,
             search_summary) -> dict:
    missing = int(missing_peaks or 0)
    gof, rwp = target.gof, target.rwp
    expert: list[str] = []
    check: list[str] = []

    for name, assessed in phases.items():
        weight = weights.get(name)
        if assessed["level"] == "weak":
            text = f"Weak evidence for {_label(name)}"
            text += f" ({weight:.1f} wt%)." if weight is not None else "."
            (expert if weight is not None and weight >= WEAK_MAJOR_WT else check).append(text)
    if missing >= MISSING_EXPERT_MIN:
        expert.append(f"{missing} observed peaks are not explained.")
    if rwp is not None and rwp > RWP_EXPERT:
        expert.append(f"Rwp is {rwp:.1f}%.")
    if gof is not None and gof > GOF_EXPERT and counts_like is not False:
        expert.append(f"GOF is {gof:.2f}.")
    if displacement["level"] == "severe":
        expert.append(displacement["message"])
    summary = search_summary or {}
    attempted, failed = _num(summary.get("final_refinement_attempted")), _num(
        summary.get("final_refinement_failed")
    )
    if attempted and failed is not None and failed >= FAILED_FRACTION_EXPERT * attempted > 0:
        expert.append(
            f"The final refinement failed for {int(failed)} of {int(attempted)} candidates."
        )
    timed = int(_num(summary.get("screening_timed_out")) or 0)
    missing_refs = timed + int(_num(summary.get("screening_not_screened")) or 0)
    total_refs = int(_num(summary.get("candidates_screened_total")) or 0)
    if total_refs and missing_refs >= SCREENING_MISSING_EXPERT_FRACTION * total_refs:
        expert.append(
            f"{missing_refs} of {total_refs} reference structures were not screened (time limit)."
        )
    elif timed:
        check.append(f"{timed} reference structure(s) timed out during screening.")
    if summary.get("stopped_early"):
        check.append("The search stopped at its time limit; the result may be incomplete.")

    for name, assessed in phases.items():
        if assessed["level"] == "ambiguous":
            check.append(f"Ambiguous evidence for {_label(name)}.")
    if gof is not None and counts_like is not False and gof > GOF_HIGH:
        check.append(f"GOF is {gof:.2f}.")
    if MISSING_HIGH_MAX < missing < MISSING_EXPERT_MIN:
        check.append(f"{missing} observed peaks are not explained.")
    if displacement["level"] == "warning":
        check.append(displacement["message"])
    if background.get("detected"):
        check.append(
            "Broad background hump: weight fractions refer to crystalline phases only."
        )

    if expert:
        level, reasons = "expert", expert + check
    elif check or not phases:
        level, reasons = "check", check or ["No refined phases were found."]
    else:
        count = len(phases)
        level = "high"
        reasons = [
            "The phase is supported by every criterion."
            if count == 1
            else f"All {count} phases are supported by every criterion."
        ]
    return {"level": level, "label": OVERALL_LABELS[level], "reasons": reasons[:4]}


# ---------------------------------------------------------------------------
# building evidence from results
# ---------------------------------------------------------------------------

def reference_lattices_for(refinement, cifs, phase_names=None) -> dict[str, dict]:
    """Lattice (Å) of the input CIF of each *computed* phase; best effort."""
    out: dict[str, dict] = {}
    try:
        names = list(phase_names or refinement.lst_data.phases_results)
    except Exception:
        return out
    for name in names:
        if not _is_computed(name):
            continue
        cif = next(
            (
                item
                for item in cifs or []
                if name in {Path(str(getattr(item, "filename", "") or "")).stem,
                            str(getattr(item, "name", "") or "")}
            ),
            None,
        )
        if cif is None:
            continue
        try:
            a, b, c = cif.to_structure().lattice.abc
            out[name] = {"a": float(a), "b": float(b), "c": float(c)}
        except Exception:
            continue
    return out


def _q_key(name: str) -> str:
    return "Q" + re.sub(r"[^A-Za-z0-9]", "", str(name))


def fill_weight_esd(refinement, phase_details: dict) -> dict:
    """Copy of ``phase_details`` with missing weight esd taken from BGMN's ``Q<phase>`` goals.

    Refinements with preferred orientation report only a mean ``GEWICHT``; the weight
    fraction and its esd are then listed as global goals (``Q<phase>=0.6031+-0.0024``).
    The input is not modified.
    """
    filled = {name: dict(details or {}) for name, details in (phase_details or {}).items()}
    lst = getattr(refinement, "lst_data", None)
    if lst is None:
        return filled
    sources = [lst, getattr(lst, "model_extra", None)]
    for name, details in filled.items():
        if _num(details.get("weight_esd_percent")) is not None:
            continue
        key = _q_key(name)
        value = None
        for source in sources:
            if source is None:
                continue
            value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
            if value is not None:
                break
        fraction, esd = _value_esd(value)
        if fraction is not None and esd is not None and esd > 0:
            details["weight_esd_percent"] = esd * 100
    return filled


def build_evidence(
    key, number, source_index, refinement, fit_quality, phase_details, phase_metadata,
    *, lattice_flags=None, cifs=None,
) -> CandidateEvidence:
    identities = {
        name: phase_identity(name, (phase_metadata or {}).get(name))
        for name in (phase_details or {})
    }
    if not identities:
        try:
            identities = {
                name: phase_identity(name, (phase_metadata or {}).get(name))
                for name in refinement.lst_data.phases_results
            }
        except Exception:
            identities = {}
    return CandidateEvidence(
        key=key,
        number=number,
        source_index=source_index,
        rwp=_num((fit_quality or {}).get("rwp")),
        gof=_num((fit_quality or {}).get("gof")),
        identities=identities,
        phase_details=fill_weight_esd(refinement, phase_details),
        refinement=refinement,
        lattice_flags=list(lattice_flags or []),
        reference_lattices=reference_lattices_for(refinement, cifs, list(identities)),
        refined_spacegroups={
            name: _int_or_none(((phase_metadata or {}).get(name) or {}).get("spacegroup_number"))
            for name in identities
        },
    )


def instrument_profile_of(document) -> str | None:
    return (getattr(document, "search_kwargs", None) or {}).get("instrument_profile")


def confidence_for_selection(document, selection=None, *, lattice_flags=None) -> dict | None:
    """Confidence of the final result (``selection`` None/"best") or of one candidate.

    ``selection`` is a candidate source index (``document.results[selection]``). The
    comparison set is every deduplicated displayed candidate, as on the task page.
    """
    try:
        from dara.server.api_router import (
            _counts_like, _fit_quality, _flatten_cifs, _matching_candidate_cifs,
            _peak_diagnostics, _phase_refinement_details, _refined_phase_metadata,
            _search_result_key,
        )

        final = getattr(document, "final_result", None)
        displayed: list[CandidateEvidence] = []
        seen: set = set()
        for index, (cifs, refinement) in enumerate(getattr(document, "results", None) or []):
            key = _search_result_key(list(refinement.lst_data.phases_results))
            if key in seen:
                continue
            seen.add(key)
            displayed.append(
                build_evidence(
                    f"candidate:{index}", len(displayed) + 1, index, refinement,
                    _fit_quality(refinement), _phase_refinement_details(refinement),
                    _refined_phase_metadata(refinement), cifs=_flatten_cifs(cifs),
                )
            )
        if selection is None or selection == "best":
            if final is None:
                return None
            refinement, cif_pool, number, source = final, [], None, None
            cif_pool = list(getattr(document, "input_cifs", None) or []) + _matching_candidate_cifs(
                document, final
            )
            flags = lattice_flags
            key = "final"
        else:
            refinement = document.results[selection][1]
            cif_pool = _flatten_cifs(document.results[selection][0])
            number = next((c.number for c in displayed if c.source_index == selection), None)
            source, flags, key = selection, None, f"candidate:{selection}"
        quality = _fit_quality(refinement)
        target = build_evidence(
            key, number, source, refinement, quality, _phase_refinement_details(refinement),
            _refined_phase_metadata(refinement), lattice_flags=flags, cifs=cif_pool,
        )
        diagnostics = _peak_diagnostics(refinement)
        return evaluate_confidence(
            target, displayed,
            counts_like=_counts_like(document),
            missing_peaks=(diagnostics.get("counts") or {}).get("missing"),
            search_summary=getattr(document, "search_summary", None),
            instrument_profile=instrument_profile_of(document),
        )
    except Exception:
        logger.warning("Document confidence failed", exc_info=True)
        return None


def confidence_for_document(document, *, lattice_flags=None) -> dict | None:
    """Confidence of the final result against all (deduplicated) displayed candidates."""
    return confidence_for_selection(document, None, lattice_flags=lattice_flags)
