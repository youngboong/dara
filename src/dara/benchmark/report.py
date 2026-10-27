"""Markdown report and console tables for benchmark runs."""

from __future__ import annotations

from typing import Any

HEADLINE_METRICS: list[tuple[str, str, str]] = [
    ("n_samples", "Samples", "int"),
    ("n_scored", "Scored", "int"),
    ("n_failed", "Failed", "int"),
    ("phase_recall", "Phase recall (micro)", "frac"),
    ("phase_precision", "Phase precision (micro)", "frac"),
    ("macro_recall", "Phase recall (macro)", "frac"),
    ("fraction_exact_set", "Exact phase set", "frac"),
    ("fraction_strict_exact", "Exact phase set and space groups", "frac"),
    ("sg_mismatch_total", "Space-group mismatches", "int"),
    ("wt_mae_median", "Weight-fraction MAE, median (wt%)", "num"),
    ("wt_mae_p90", "Weight-fraction MAE, 90th percentile (wt%)", "num"),
    ("wt_abs_error_median", "Weight error per phase, median (wt%)", "num"),
    ("lattice_rel_error_median_pct", "Lattice error, median (%)", "num"),
    ("lattice_rel_error_p90_pct", "Lattice error, 90th percentile (%)", "num"),
    ("top5_contains_truth_fraction", "Truth within top 5 candidates", "frac"),
    ("runtime_median_s", "Runtime, median (s)", "num"),
    ("runtime_total_s", "Runtime, total (s)", "num"),
]

GROUP_COLUMNS: list[tuple[str, str, str]] = [
    ("n_samples", "n", "int"),
    ("n_failed", "failed", "int"),
    ("phase_recall", "recall", "frac"),
    ("phase_precision", "precision", "frac"),
    ("fraction_exact_set", "exact set", "frac"),
    ("fraction_strict_exact", "strict exact", "frac"),
    ("sg_mismatch_total", "SG mismatch", "int"),
    ("wt_mae_median", "wt MAE med", "num"),
    ("lattice_rel_error_median_pct", "lattice med %", "num"),
]


def fmt(value: Any, kind: str = "num") -> str:
    if value is None:
        return "-"
    if kind == "int":
        return str(int(value))
    if kind == "frac":
        return f"{float(value):.3f}"
    return f"{float(value):.2f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def headline_table(aggregate: dict) -> str:
    """Plain-text headline table for console output."""
    width = max(len(label) for _, label, _ in HEADLINE_METRICS)
    return "\n".join(
        f"{label.ljust(width)}  {fmt(aggregate.get(key), kind)}" for key, label, kind in HEADLINE_METRICS
    )


def _max_lattice(score: dict) -> float | None:
    values = [
        m["lattice_rel_error_pct"] for m in score.get("matches", [])
        if m.get("lattice_rel_error_pct") is not None
    ]
    return max(values) if values else None


def render_markdown(run_info: dict, scores: list[dict], aggregate: dict) -> str:
    settings = (run_info or {}).get("settings", {})
    out: list[str] = ["# DARA accuracy benchmark", ""]

    out += ["## Settings", ""]
    setting_rows = [
        ["Sets", ", ".join(settings.get("sets") or []) or "-"],
        ["Samples", str(len(scores))],
        ["Analysis mode", settings.get("mode") or "per sample (ground truth)"],
        ["Materials Project", "on" if settings.get("use_mp", True) else "off (non-standard: COD only)"],
        ["MP lattice window", "on" if settings.get("mp_lattice_window", True) else "off"],
        ["CIF validation", "on" if settings.get("cif_validation", True) else "off"],
        ["DARA version", str((run_info or {}).get("dara_version") or "-")],
        ["BGMN version", str((run_info or {}).get("bgmn_version") or "-")],
        ["Python / platform", f"{(run_info or {}).get('python', '-')} / {(run_info or {}).get('platform', '-')}"],
        ["Started", str((run_info or {}).get("started") or "-")],
        ["Ended", str((run_info or {}).get("ended") or "-")],
    ]
    out += _table(["Setting", "Value"], setting_rows) + [""]

    out += ["## Headline metrics", ""]
    out += _table(
        ["Metric", "Value"],
        [[label, fmt(aggregate.get(key), kind)] for key, label, kind in HEADLINE_METRICS],
    ) + [""]

    out += ["## Per group", ""]
    group_rows = [
        [name] + [fmt(core.get(key), kind) for key, _, kind in GROUP_COLUMNS]
        for name, core in (aggregate.get("by_group") or {}).items()
    ]
    out += _table(["Group"] + [label for _, label, _ in GROUP_COLUMNS], group_rows) + [""]

    calibration = aggregate.get("calibration") or {}
    out += ["## Calibration", "", "Overall confidence level versus the exact phase set:", ""]
    out += _table(
        ["Overall level", "Samples", "Exact-set fraction"],
        [[level, str(e["n"]), fmt(e["exact_set_fraction"], "frac")]
         for level, e in (calibration.get("overall") or {}).items()],
    ) + ["", "Per-phase verdict versus correct identification:", ""]
    out += _table(
        ["Phase verdict", "Phases", "Correct fraction"],
        [[level, str(e["n"]), fmt(e["tp_fraction"], "frac")]
         for level, e in (calibration.get("phase") or {}).items()],
    ) + [""]
    displacement = aggregate.get("displacement") or {}
    out += [
        "Sample displacement (predicted / reference): "
        f"n={displacement.get('n', 0)}, median ratio {fmt(displacement.get('median_ratio'))}, "
        f"median absolute difference {fmt(displacement.get('median_abs_diff_mm'))} mm.",
        "",
    ]

    out += ["## Per sample", ""]
    rows = []
    for s in scores:
        counts = f"{s['tp']}/{s['fp']}/{s['fn']}" if s.get("tp") is not None else "failed"
        rows.append([
            s["id"],
            "yes" if s.get("exact_set") else "no",
            counts,
            str(s["sg_mismatch"]) if s.get("sg_mismatch") is not None else "-",
            fmt(s.get("wt_mae")),
            fmt(_max_lattice(s)),
            fmt(s.get("rwp")),
            str(s.get("confidence_overall") or "-"),
            fmt(s.get("runtime_s"), "num"),
        ])
    out += _table(
        ["Sample", "Exact", "tp/fp/fn", "SG mismatches", "wt MAE", "Max lattice err %", "Rwp", "Confidence", "Runtime s"],
        rows,
    ) + [""]
    errors = [(s["id"], s.get("error")) for s in scores if s.get("status") != "scored"]
    if errors:
        out += ["Failed samples:", ""] + [f"- {sid}: {message}" for sid, message in errors] + [""]

    out += [
        "## Notes",
        "",
        "- Phase recall (micro): sum of correctly identified phases over all ground-truth phases; failed samples count all required phases as missed.",
        "- Phase precision (micro): correctly identified phases over all reported phases; allowed extra phases are not counted as false positives.",
        "- Phase recall (macro): mean of the per-sample recall.",
        "- Exact phase set: the sample has no false-positive and no missing phase; the fraction is over all samples.",
        "- Exact phase set and space groups: exact set with no space-group mismatch on the matched phases.",
        "- Space-group mismatches: matched phases whose space group is not among the accepted ones.",
        "- Weight-fraction MAE: mean absolute difference of weight percent over matched phases, both sides renormalised to 100 wt% over the matched phases.",
        "- Weight error per phase: the same differences pooled over all matched phases.",
        "- Lattice error: largest relative difference of a, b, c, alpha, beta, gamma to the reference refinement, with axis permutation for orthorhombic and monoclinic settings; pairs with a different cell setting are skipped.",
        "- Truth within top 5 candidates: any of the first five candidate hypotheses has the correct phase set.",
        "- Calibration: how often the reported confidence level agrees with a correct result.",
        "- Sample displacement: refined displacement compared with the reference Rietveld fit.",
        "- Runtime: wall-clock time of the in-process analysis per sample.",
        "",
    ]
    return "\n".join(out)
