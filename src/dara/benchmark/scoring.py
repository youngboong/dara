"""Pure scoring of DARA results against curated ground truth.

``summary`` is the dict returned by ``dara.server.api_router.summarize_document``
(the same shape as ``GET /api/task/{id}``). Every optional field is read
defensively so older summaries still score.
"""

from __future__ import annotations

import itertools
import math
from functools import lru_cache
from typing import Any

import numpy as np

from dara.benchmark.ground_truth import GTAlias, GTPhase, GTSample

LATTICE_KEYS = ("a", "b", "c", "alpha", "beta", "gamma")
_FRACTION_TOLERANCE = 0.03
_VOLUME_RATIO_RANGE = (0.9, 1.1)


# --------------------------------------------------------------------------- #
# Formula handling
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=2048)
def _composition(formula: str):
    from pymatgen.core import Composition

    return Composition(formula).reduced_composition


def reduced_formula(formula: str | None) -> str | None:
    if not formula:
        return None
    try:
        return _composition(formula).reduced_formula
    except Exception:
        return None


def _alias_formula(pred_formula: str | None, aliases: dict) -> str | None:
    if not pred_formula or not aliases:
        return pred_formula
    for key, target in aliases.items():
        key_formula = reduced_formula(key) or key
        if key_formula == pred_formula or key == pred_formula:
            formula = target.formula if hasattr(target, "formula") else target["formula"]
            return reduced_formula(formula) or formula
    return pred_formula


def _compositions_match(first: str, second: str) -> bool:
    try:
        a, b = _composition(first), _composition(second)
    except Exception:
        return False
    if a.almost_equals(b, rtol=1e-3, atol=1e-8):
        return True
    if {str(e) for e in a.elements} != {str(e) for e in b.elements}:
        return False
    return max(abs(a.get_atomic_fraction(e) - b.get_atomic_fraction(e)) for e in a.elements) <= _FRACTION_TOLERANCE


def formula_matches(
    gt_formula: str,
    accept_any_of: list[str],
    pred_formula: str | None,
    aliases: dict,
) -> bool:
    """True when the predicted formula equals the truth or an accepted alternative."""
    pred = _alias_formula(reduced_formula(pred_formula) or pred_formula, aliases or {})
    if not pred:
        return False
    return any(_compositions_match(candidate, pred) for candidate in [gt_formula, *(accept_any_of or [])])


# --------------------------------------------------------------------------- #
# Predicted phases
# --------------------------------------------------------------------------- #
def _lattice_with_symmetry(parameters: dict, spacegroup) -> dict[str, float | None]:
    try:
        from dara.server.table_export import lattice_with_symmetry

        filled = lattice_with_symmetry(parameters or {}, spacegroup)
        return {key: (filled.get(key) or (None, None))[0] for key in LATTICE_KEYS}
    except Exception:
        out = {}
        for key in LATTICE_KEYS:
            entry = (parameters or {}).get(key)
            out[key] = entry.get("value") if isinstance(entry, dict) else None
        return out


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _block(summary: dict, candidate: int | None) -> dict:
    if candidate is None:
        return (summary or {}).get("final_result") or {}
    results = (summary or {}).get("all_results") or []
    return results[candidate] if 0 <= candidate < len(results) else {}


def predicted_phases(summary: dict, *, candidate: int | None = None) -> list[dict]:
    """Phases of the final result (or ``all_results[candidate]``) as flat records."""
    block = _block(summary, candidate)
    # ``all_results[i]["phases"]`` lists CIF names per slot; the refined phases are
    # ``highlighted_phases`` there, ``phases`` in ``final_result``.
    names = (block.get("highlighted_phases") if candidate is not None else block.get("phases")) or []
    details = block.get("phase_details") or {}
    metadata = block.get("phase_metadata") or {}
    weights = block.get("weight_percent") or {}
    confidence = (block.get("confidence") or {}).get("phases") or {}

    out = []
    for name in names:
        detail = details.get(name) or {}
        weight = _number(detail.get("weight_percent"))
        if weight is None:
            weight = _number(weights.get(name))
        spacegroup = (metadata.get(name) or {}).get("spacegroup_number")
        try:
            spacegroup = int(spacegroup) if spacegroup is not None else None
        except (TypeError, ValueError):
            spacegroup = None
        out.append({
            "name": name,
            "formula": reduced_formula(str(name).split("_")[0]),
            "spacegroup": spacegroup,
            "weight_percent": weight,
            "weight_esd_percent": _number(detail.get("weight_esd_percent")),
            "lattice": _lattice_with_symmetry(detail.get("lattice_parameters") or {}, spacegroup),
            "verdict": (confidence.get(name) or {}).get("level"),
        })
    return out


def _merge_duplicates(phases: list[dict]) -> list[dict]:
    """Merge predicted phases that share (reduced formula, space group)."""
    merged: dict[tuple, dict] = {}
    for phase in phases:
        key = (phase["formula"] or phase["name"], phase["spacegroup"])
        if key not in merged:
            merged[key] = {**phase, "merged_names": [phase["name"]]}
            continue
        target = merged[key]
        target["merged_names"].append(phase["name"])
        if target["weight_percent"] is not None or phase["weight_percent"] is not None:
            target["weight_percent"] = (target["weight_percent"] or 0.0) + (phase["weight_percent"] or 0.0)
        if target["weight_esd_percent"] is not None and phase["weight_esd_percent"] is not None:
            target["weight_esd_percent"] = math.hypot(target["weight_esd_percent"], phase["weight_esd_percent"])
    return list(merged.values())


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _sg_compatible(gt: GTPhase, pred_spacegroup: int | None) -> bool:
    allowed = {gt.spacegroup, *gt.spacegroup_any_of} - {None}
    if not allowed:
        return True
    return pred_spacegroup in allowed


def _match_phases(gt_phases: list[GTPhase], preds: list[dict], aliases: dict) -> list[tuple[int, int, bool]]:
    pairs = []
    for gi, gt in enumerate(gt_phases):
        for pi, pred in enumerate(preds):
            if not formula_matches(gt.formula, gt.accept_any_of, pred["formula"], aliases):
                continue
            compatible = _sg_compatible(gt, pred["spacegroup"])
            difference = abs((pred["weight_percent"] or 0.0) - gt.wt_percent)
            pairs.append((0 if compatible else 1, difference, gi, pi, compatible))
    pairs.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    chosen = []
    for _, _, gi, pi, compatible in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        chosen.append((gi, pi, compatible))
    return chosen


def _phase_counts(gt: GTSample, preds: list[dict], aliases: dict) -> dict:
    chosen = _match_phases(gt.phases, preds, aliases)
    matched_gt = {gi for gi, _, _ in chosen}
    matched_pred = {pi for _, pi, _ in chosen}
    allowed_found = []
    false_positives = []
    for pi, pred in enumerate(preds):
        if pi in matched_pred:
            continue
        if any(formula_matches(extra, [], pred["formula"], aliases) for extra in gt.allowed_extra):
            allowed_found.append(pred["name"])
        else:
            false_positives.append(pi)
    false_negatives = [
        gi for gi, phase in enumerate(gt.phases) if gi not in matched_gt and phase.required
    ]
    sg_mismatch = sum(1 for _, _, compatible in chosen if not compatible)
    return {
        "chosen": chosen,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "allowed_extra_found": allowed_found,
        "sg_mismatch": sg_mismatch,
    }


# --------------------------------------------------------------------------- #
# Lattice comparison
# --------------------------------------------------------------------------- #
def _lattice_error(gt: GTPhase, pred: dict) -> tuple[float | None, str | None]:
    """Maximum relative lattice error in percent, or ``(None, note)``."""
    reference = {k: v for k, v in gt.lattice.items() if k in LATTICE_KEYS and v}
    if not reference:
        return None, None
    predicted = {k: v for k, v in (pred.get("lattice") or {}).items() if v is not None}
    if not any(key in predicted for key in reference):
        return None, "no_predicted_lattice"

    lengths = ("a", "b", "c")
    candidates = [dict(predicted)]
    permutable = (
        all(key in reference for key in lengths)
        and all(key in predicted for key in lengths)
        and (gt.spacegroup is None or 3 <= gt.spacegroup <= 74)
    )
    if permutable:
        candidates = []
        for order in itertools.permutations(lengths):
            variant = dict(predicted)
            for target, source in zip(lengths, order):
                variant[target] = predicted[source]
            candidates.append(variant)

    def relative_errors(values: dict) -> dict[str, float]:
        return {
            key: abs(values[key] - reference[key]) / abs(reference[key]) * 100
            for key in reference
            if key in values
        }

    best = min(candidates, key=lambda values: max(relative_errors(values).values(), default=math.inf))

    shared = [key for key in lengths if key in reference and key in best]
    if shared:
        ratio = 1.0
        for key in shared:
            ratio *= best[key] / reference[key]
        if not _VOLUME_RATIO_RANGE[0] <= ratio <= _VOLUME_RATIO_RANGE[1]:
            return None, "setting_mismatch"
    errors = relative_errors(best)
    if not errors:
        return None, "no_predicted_lattice"
    return max(errors.values()), None


# --------------------------------------------------------------------------- #
# Sample scoring
# --------------------------------------------------------------------------- #
def _group(gt: GTSample) -> str:
    return "pairwise" if not gt.scan else f"precursor-{gt.scan}"


def _failed_score(gt: GTSample, set_name: str | None, error: str | None, runtime_s) -> dict:
    return {
        "id": gt.id,
        "set": set_name,
        "group": _group(gt),
        "status": "failed",
        "error": error or "No result",
        "n_required_phases": sum(1 for phase in gt.phases if phase.required),
        "tp": None, "fp": None, "fn": None, "sg_mismatch": None,
        "precision": None, "recall": None, "exact_set": False, "strict_exact": False,
        "allowed_extra_found": [], "matches": [], "false_positives": [],
        "false_negatives": [phase.formula for phase in gt.phases if phase.required],
        "wt_mae": None, "wt_max_abs": None, "rwp": None, "gof": None,
        "runtime_s": runtime_s, "top5_contains_truth": None,
        "confidence_overall": None, "displacement_mm_pred": None,
        "displacement_mm_ref": (gt.reference_fit.sample_displacement_mm if gt.reference_fit else None),
        "displacement_ratio": None, "displacement_diff_mm": None,
    }


def score_sample(
    gt: GTSample,
    summary: dict | None,
    *,
    aliases: dict[str, GTAlias] | dict | None = None,
    runtime_s: float | None = None,
    error: str | None = None,
    set_name: str | None = None,
) -> dict:
    """Score one sample; a ``None`` summary yields a failed record."""
    aliases = aliases or {}
    if summary is None:
        return _failed_score(gt, set_name, error, runtime_s)

    preds = _merge_duplicates(predicted_phases(summary))
    counts = _phase_counts(gt, preds, aliases)
    chosen = counts["chosen"]
    tp = len(chosen)
    fp = len(counts["false_positives"])
    fn = len(counts["false_negatives"])

    gt_total = sum(gt.phases[gi].wt_percent for gi, _, _ in chosen)
    pred_total = sum((preds[pi]["weight_percent"] or 0.0) for _, pi, _ in chosen)
    matches = []
    for gi, pi, compatible in chosen:
        gt_phase, pred = gt.phases[gi], preds[pi]
        wt_gt = gt_phase.wt_percent / gt_total * 100 if gt_total else None
        wt_pred = (
            (pred["weight_percent"] or 0.0) / pred_total * 100
            if pred_total and pred["weight_percent"] is not None
            else None
        )
        lattice_error, lattice_note = _lattice_error(gt_phase, pred)
        matches.append({
            "gt": gt_phase.formula,
            "pred": pred["name"],
            "pred_spacegroup": pred["spacegroup"],
            "sg_ok": compatible,
            "wt_gt": _round(wt_gt),
            "wt_pred": _round(wt_pred),
            "wt_gt_raw": gt_phase.wt_percent,
            "wt_pred_raw": pred["weight_percent"],
            "wt_abs_error": _round(abs(wt_pred - wt_gt)) if wt_gt is not None and wt_pred is not None else None,
            "lattice_rel_error_pct": _round(lattice_error, 4),
            "lattice_note": lattice_note,
            "verdict": pred["verdict"],
        })
    errors = [m["wt_abs_error"] for m in matches if m["wt_abs_error"] is not None]

    final_block = (summary.get("final_result") or {})
    fit_quality = final_block.get("fit_quality") or {}
    confidence = final_block.get("confidence") or {}
    displacement_pred = _number((confidence.get("sample_displacement") or {}).get("displacement_mm"))
    displacement_ref = gt.reference_fit.sample_displacement_mm if gt.reference_fit else None
    ratio = diff = None
    if displacement_pred is not None and displacement_ref is not None:
        diff = displacement_pred - displacement_ref
        ratio = displacement_pred / displacement_ref if displacement_ref else None

    exact_set = fp == 0 and fn == 0
    runtime = runtime_s if runtime_s is not None else _number(summary.get("runtime"))
    return {
        "id": gt.id,
        "set": set_name,
        "group": _group(gt),
        "status": "scored",
        "error": None,
        "n_required_phases": sum(1 for phase in gt.phases if phase.required),
        "tp": tp, "fp": fp, "fn": fn,
        "sg_mismatch": counts["sg_mismatch"],
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "exact_set": exact_set,
        "strict_exact": exact_set and counts["sg_mismatch"] == 0,
        "allowed_extra_found": counts["allowed_extra_found"],
        "matches": matches,
        "false_positives": [
            {
                "pred": preds[pi]["name"],
                "wt_pred": _round(preds[pi]["weight_percent"]),
                "verdict": preds[pi]["verdict"],
            }
            for pi in counts["false_positives"]
        ],
        "false_negatives": [gt.phases[gi].formula for gi in counts["false_negatives"]],
        "wt_mae": _round(sum(errors) / len(errors)) if errors else None,
        "wt_max_abs": _round(max(errors)) if errors else None,
        "rwp": _number(fit_quality.get("rwp", final_block.get("rwp"))),
        "gof": _number(fit_quality.get("gof")),
        "runtime_s": runtime,
        "top5_contains_truth": _top5_contains_truth(gt, summary, aliases),
        "confidence_overall": (confidence.get("overall") or {}).get("level"),
        "displacement_mm_pred": displacement_pred,
        "displacement_mm_ref": displacement_ref,
        "displacement_ratio": _round(ratio, 4),
        "displacement_diff_mm": _round(diff, 4),
    }


def _round(value, digits: int = 3):
    return None if value is None else round(float(value), digits)


def _top5_contains_truth(gt: GTSample, summary: dict, aliases: dict) -> bool:
    results = summary.get("all_results") or []
    indexes: list[int | None] = list(range(min(5, len(results)))) or [None]
    for index in indexes:
        preds = _merge_duplicates(predicted_phases(summary, candidate=index))
        counts = _phase_counts(gt, preds, aliases)
        if not counts["false_positives"] and not counts["false_negatives"]:
            return True
    return False


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def _p90(values: list[float]) -> float | None:
    return float(np.percentile(values, 90)) if values else None


def _core(scores: list[dict]) -> dict:
    scored = [s for s in scores if s.get("status") == "scored"]
    failed = [s for s in scores if s.get("status") != "scored"]
    tp = sum(s["tp"] for s in scored)
    fn = sum(s["fn"] for s in scored) + sum(s.get("n_required_phases") or 0 for s in failed)
    fp = sum(s["fp"] for s in scored)
    recalls = [s["tp"] / (s["tp"] + s["fn"]) for s in scored if s["tp"] + s["fn"]] + [0.0] * len(failed)
    n = len(scores)
    lattice = [
        m["lattice_rel_error_pct"]
        for s in scored for m in s["matches"]
        if m.get("lattice_rel_error_pct") is not None
    ]
    return {
        "n_samples": n,
        "n_scored": len(scored),
        "n_failed": len(failed),
        "phase_recall": tp / (tp + fn) if tp + fn else None,
        "phase_precision": tp / (tp + fp) if tp + fp else None,
        "macro_recall": float(np.mean(recalls)) if recalls else None,
        "fraction_exact_set": sum(1 for s in scores if s.get("exact_set")) / n if n else None,
        "fraction_strict_exact": sum(1 for s in scores if s.get("strict_exact")) / n if n else None,
        "sg_mismatch_total": sum(s["sg_mismatch"] for s in scored),
        "wt_mae_median": _median([s["wt_mae"] for s in scored if s.get("wt_mae") is not None]),
        "wt_mae_p90": _p90([s["wt_mae"] for s in scored if s.get("wt_mae") is not None]),
        "lattice_rel_error_median_pct": _median(lattice),
        "lattice_rel_error_p90_pct": _p90(lattice),
    }


def aggregate(scores: list[dict]) -> dict:
    """Headline metrics over a list of ``score_sample`` records."""
    scored = [s for s in scores if s.get("status") == "scored"]
    result = _core(scores)
    pooled = [
        m["wt_abs_error"] for s in scored for m in s["matches"] if m.get("wt_abs_error") is not None
    ]
    top5 = [s["top5_contains_truth"] for s in scored if s.get("top5_contains_truth") is not None]
    runtimes = [s["runtime_s"] for s in scores if s.get("runtime_s") is not None]
    result.update({
        "wt_abs_error_median": _median(pooled),
        "top5_contains_truth_fraction": sum(1 for v in top5 if v) / len(top5) if top5 else None,
        "runtime_median_s": _median(runtimes),
        "runtime_total_s": float(sum(runtimes)) if runtimes else None,
    })

    overall: dict[str, dict] = {}
    for s in scored:
        level = s.get("confidence_overall") or "none"
        entry = overall.setdefault(level, {"n": 0, "exact": 0})
        entry["n"] += 1
        entry["exact"] += 1 if s.get("exact_set") else 0
    phase: dict[str, dict] = {}
    for s in scored:
        for m in s["matches"]:
            entry = phase.setdefault(m.get("verdict") or "none", {"n": 0, "tp": 0})
            entry["n"] += 1
            entry["tp"] += 1
        for f in s["false_positives"]:
            entry = phase.setdefault(f.get("verdict") or "none", {"n": 0, "tp": 0})
            entry["n"] += 1
    result["calibration"] = {
        "overall": {
            level: {"n": e["n"], "exact_set_fraction": e["exact"] / e["n"]}
            for level, e in sorted(overall.items())
        },
        "phase": {
            level: {"n": e["n"], "tp_fraction": e["tp"] / e["n"]}
            for level, e in sorted(phase.items())
        },
    }

    ratios = [s["displacement_ratio"] for s in scored if s.get("displacement_ratio") is not None]
    diffs = [abs(s["displacement_diff_mm"]) for s in scored if s.get("displacement_diff_mm") is not None]
    result["displacement"] = {
        "n": len(diffs),
        "median_ratio": _median(ratios),
        "median_abs_diff_mm": _median(diffs),
    }

    groups: dict[str, list[dict]] = {}
    for s in scores:
        groups.setdefault(s.get("group") or "all", []).append(s)
    result["by_group"] = {name: _core(items) for name, items in sorted(groups.items())}
    return result


def phase_summary_for(summary: dict | None) -> list[dict[str, Any]]:
    """Compact predicted-phase listing (name, SG, wt%) for logs."""
    return [
        {"name": p["name"], "spacegroup": p["spacegroup"], "weight_percent": p["weight_percent"]}
        for p in _merge_duplicates(predicted_phases(summary or {}))
    ]
