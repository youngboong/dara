"""Command line interface of the accuracy benchmark (``scripts/run_benchmark.py``)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dara.benchmark.ground_truth import (
    GTSample,
    GTSet,
    load_ground_truth,
    repo_root,
    validate_ground_truth,
)
from dara.benchmark.report import fmt, headline_table

# metric, label, kind, direction ("down" = a drop is a regression), threshold
REGRESSION_GATES = [
    ("phase_recall", "phase recall", "drop", 0.02),
    ("fraction_exact_set", "fraction of exact phase sets", "drop", 0.05),
    ("wt_mae_median", "median weight MAE (wt%)", "rise", 0.5),
    ("lattice_rel_error_median_pct", "median lattice error (%)", "rise", 0.1),
    ("n_failed", "failed samples", "rise_eq", 1),
]

COMPARE_METRICS = [
    "n_failed", "phase_recall", "phase_precision", "macro_recall", "fraction_exact_set",
    "fraction_strict_exact", "sg_mismatch_total", "wt_mae_median", "wt_mae_p90",
    "wt_abs_error_median", "lattice_rel_error_median_pct", "top5_contains_truth_fraction",
    "runtime_median_s",
]


def _parse_switch(value: str) -> bool:
    return value.lower() in {"on", "true", "1", "yes"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_benchmark", description="DARA accuracy benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-gt", help="Validate the ground-truth files")

    run = sub.add_parser("run", help="Run DARA on benchmark samples and score the results")
    run.add_argument("--set", dest="set_name", choices=["pairwise", "precursor", "all"], default="all")
    run.add_argument("--ids", help="Regex selecting sample ids")
    run.add_argument("--limit", type=int, help="Run at most N samples")
    run.add_argument("--out", type=Path, help="Output folder (default benchmarks/<run id>)")
    run.add_argument("--no-resume", action="store_true", help="Re-run samples that already have a score")
    run.add_argument("--mode", choices=["elemental", "reaction", "known_mixture"])
    run.add_argument("--mp-window", choices=["on", "off"], default="on")
    run.add_argument("--cif-validation", choices=["on", "off"], default="on")
    run.add_argument("--keep-work", action="store_true", help="Keep the BGMN working folders")
    run.add_argument("--no-mp", action="store_true", help="Non-standard: use COD only")

    score = sub.add_parser("score", help="Re-score stored results")
    source = score.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-run", type=Path, help="A benchmark run folder")
    source.add_argument("--from-tasks", help='Server task ids "12,13" or "regex:<pattern>" on the job name')
    score.add_argument("--montydb", help="Path of the server database (with --from-tasks)")
    score.add_argument("--write", action="store_true", help="Write scores.json and report.md")
    score.add_argument("--out", type=Path, help="Output folder for --write (default: the run folder)")

    compare = sub.add_parser("compare", help="Compare two runs; exit 1 on a regression")
    compare.add_argument("run_a", type=Path)
    compare.add_argument("run_b", type=Path)
    return parser


def _load_sets(name: str) -> list[GTSet]:
    names = ["pairwise", "precursor"] if name == "all" else [name]
    return [load_ground_truth(item) for item in names]


def select_samples(
    gt_sets: list[GTSet], *, ids: str | None = None, limit: int | None = None
) -> list[tuple[GTSet, GTSample]]:
    pattern = re.compile(ids) if ids else None
    chosen = [
        (gt_set, sample)
        for gt_set in gt_sets
        for sample in gt_set.samples
        if pattern is None or pattern.search(sample.id)
    ]
    return chosen[:limit] if limit is not None else chosen


def _cmd_validate(args) -> int:
    root = repo_root()
    total = 0
    failed = False
    for name in ("pairwise", "precursor"):
        gt_set = load_ground_truth(name)
        problems = validate_ground_truth(gt_set, root)
        total += len(gt_set.samples)
        for problem in problems:
            print(f"PROBLEM [{gt_set.set}] {problem}")
        failed = failed or bool(problems)
    if failed:
        return 1
    print(f"OK: {total} samples")
    return 0


def _cmd_run(args) -> int:
    if not args.no_mp and not os.environ.get("MP_API_KEY"):
        print(
            "MP_API_KEY is not set. The benchmark needs Materials Project structures; "
            "set MP_API_KEY or pass --no-mp (non-standard: COD only).",
            file=sys.stderr,
        )
        return 2
    gt_sets = _load_sets(args.set_name)
    samples = select_samples(gt_sets, ids=args.ids, limit=args.limit)
    if not samples:
        print("No samples match the selection.", file=sys.stderr)
        return 2
    from dara.benchmark.runner import run_benchmark

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + "+".join(sorted({g.set for g, _ in samples}))
    out_dir = args.out or (repo_root() / "benchmarks" / run_id)
    print(f"Running {len(samples)} sample(s) into {out_dir}")
    result = run_benchmark(
        samples,
        out_dir,
        resume=not args.no_resume,
        mode=args.mode,
        use_mp=not args.no_mp,
        mp_window=args.mp_window == "on",
        cif_validation=args.cif_validation == "on",
        keep_work=args.keep_work,
    )
    print()
    print(headline_table(result["aggregate"]))
    print(f"\nReport: {out_dir / 'report.md'}")
    return 0


def _cmd_score(args) -> int:
    from dara.benchmark import runner

    if args.from_run:
        result = runner.score_from_run(args.from_run)
        target = args.out or args.from_run
    else:
        result = runner.score_from_tasks(args.from_tasks, montydb_path=args.montydb)
        target = args.out
    if not result["scores"]:
        print("No samples were scored.", file=sys.stderr)
        return 2
    print(headline_table(result["aggregate"]))
    if args.write:
        if target is None:
            print("--write needs --out with --from-tasks.", file=sys.stderr)
            return 2
        Path(target).mkdir(parents=True, exist_ok=True)
        runner.finalize_run(Path(target), result["scores"], result["run"])
        print(f"\nWritten to {target}")
    return 0


def _load_run(path: Path) -> dict:
    scores_path = path / "scores.json"
    if scores_path.is_file():
        data = json.loads(scores_path.read_text(encoding="utf-8"))
        return {"scores": data["scores"], "aggregate": data["aggregate"]}
    from dara.benchmark import runner

    return runner.score_from_run(path)


def compare_runs(first: dict, second: dict) -> tuple[list[list[str]], list[str], list[str], list[str]]:
    """Return ``(metric rows, regressions, newly exact, newly wrong)``."""
    a, b = first["aggregate"], second["aggregate"]
    rows = []
    for key in COMPARE_METRICS:
        old, new = a.get(key), b.get(key)
        delta = None if old is None or new is None else new - old
        rows.append([
            key,
            fmt(old, "num"),
            fmt(new, "num"),
            "-" if delta is None else f"{delta:+.3f}",
        ])

    regressions = []
    for key, label, kind, threshold in REGRESSION_GATES:
        old, new = a.get(key), b.get(key)
        if old is None or new is None:
            continue
        delta = new - old
        if kind == "drop" and -delta > threshold:
            regressions.append(f"{label} dropped by {-delta:.3f} (limit {threshold})")
        elif kind == "rise" and delta > threshold:
            regressions.append(f"{label} rose by {delta:.3f} (limit {threshold})")
        elif kind == "rise_eq" and delta >= threshold:
            regressions.append(f"{label} rose by {delta:.0f}")

    old_scores = {s["id"]: s for s in first["scores"]}
    new_scores = {s["id"]: s for s in second["scores"]}
    newly_exact, newly_wrong = [], []
    for sid in sorted(old_scores.keys() & new_scores.keys()):
        was, now = bool(old_scores[sid].get("exact_set")), bool(new_scores[sid].get("exact_set"))
        if now and not was:
            newly_exact.append(sid)
        elif was and not now:
            newly_wrong.append(sid)
    return rows, regressions, newly_exact, newly_wrong


def _cmd_compare(args) -> int:
    first, second = _load_run(args.run_a), _load_run(args.run_b)
    rows, regressions, newly_exact, newly_wrong = compare_runs(first, second)
    width = max(len(row[0]) for row in rows)
    print(f"{'metric'.ljust(width)}  {'A':>10}  {'B':>10}  {'B-A':>10}")
    for name, old, new, delta in rows:
        print(f"{name.ljust(width)}  {old:>10}  {new:>10}  {delta:>10}")
    print("\nNewly exact: " + (", ".join(newly_exact) or "none"))
    print("Newly wrong: " + (", ".join(newly_wrong) or "none"))
    if regressions:
        print("\nREGRESSION:")
        for line in regressions:
            print("  - " + line)
        return 1
    print("\nNo regression gate tripped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "validate-gt": _cmd_validate,
        "run": _cmd_run,
        "score": _cmd_score,
        "compare": _cmd_compare,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
