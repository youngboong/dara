"""Check that the candidate pre-filter keeps every ground-truth phase.

For each benchmark sample the candidate pool is rebuilt exactly like a
production job (same databases, e-hull filter, de-duplication and CIF
screening as ``dara.jobs.PhaseSearchMaker.make``), the pre-filter is run once
with the smallest ``--rel-floors`` value and its decision is re-evaluated for
every grid value. A ground-truth phase is *retained* when at least one pool
candidate with the right formula (and a compatible space group) survives.

Needs network access (COD downloads, Materials Project) and ``MP_API_KEY`` in
the environment unless ``--no-mp`` is given. No BGMN, no Ray.

Example::

    python scripts/check_prefilter_retention.py --set all --out artifacts/retention

Outputs ``retention.json`` and ``retention.md`` in ``--out``. The exit code is 0
only if every ground-truth phase present in a pool is retained at the default
``PrefilterConfig.rel_floor`` and no sample failed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import pickle
import re
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

#: Replaced by tests; ``None`` means "import dara.server.api_router.build_submission_job".
build_submission_job = None

DEFAULT_FLOORS = (0.01, 0.02, 0.03, 0.05)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _builder():
    if build_submission_job is not None:
        return build_submission_job
    from dara.server.api_router import build_submission_job as real

    return real


def floor_key(value: float) -> str:
    return f"{value:g}"


def _json_default(value):
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _clean(value):
    """Replace non-finite floats so that the JSON is strict."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _decode(value):
    if isinstance(value, dict) or (isinstance(value, list) and value and isinstance(value[0], dict)):
        from monty.json import MontyDecoder

        return MontyDecoder().process_decoded(value)
    return value


def job_settings(job) -> dict:
    """Pool-relevant kwargs of a submission job (decoded)."""
    kwargs = job.function_kwargs
    names = ("cif_dbs", "precursors", "elements", "analysis_mode", "database_e_hull_filter",
             "search_kwargs", "additional_cifs")
    defaults = {"cif_dbs": None, "precursors": [], "elements": None, "analysis_mode": "reaction",
                "database_e_hull_filter": 0.05, "search_kwargs": {}, "additional_cifs": []}
    settings = {name: _decode(kwargs.get(name, defaults[name])) for name in names}
    settings["precursors"] = list(settings["precursors"] or [])
    settings["additional_cifs"] = list(settings["additional_cifs"] or [])
    settings["search_kwargs"] = dict(settings["search_kwargs"] or {})
    return settings


# --------------------------------------------------------------------------- #
# candidate pool
# --------------------------------------------------------------------------- #
def build_pool(settings: dict, sample_id: str, cache_dir: Path, *, refresh: bool = False) -> dict:
    """Rebuild the production candidate pool of one sample (cached in ``pool.json``).

    Returns ``{"status", "paths", "summary"}``; ``paths`` are the screened CIFs.
    """
    cache_dir = Path(cache_dir).resolve()
    sample_dir = cache_dir / re.sub(r"[^\w.\-]+", "_", sample_id)
    cifs_dir = sample_dir / "dara_cifs"
    pool_file = sample_dir / "pool.json"
    mode = settings["analysis_mode"]
    if mode == "reaction":
        return {"status": "reaction_mode_not_supported", "paths": [], "summary": {}}

    if pool_file.exists() and not refresh:
        data = json.loads(pool_file.read_text(encoding="utf-8"))
        paths = [cifs_dir / name for name in data["files"]]
        if all(path.exists() for path in paths):
            return {"status": "ok", "paths": paths, "summary": data["summary"], "cached": True}

    from dara.structure_db import deduplicate_cif_paths
    from dara.structure_quality import screen_candidate_cifs

    if cifs_dir.exists():
        import shutil

        shutil.rmtree(cifs_dir)
    cifs_dir.mkdir(parents=True)
    dbs = settings["cif_dbs"] or []
    precursors = settings["precursors"]
    elements = settings["elements"] or []
    e_hull = settings["database_e_hull_filter"]
    with contextlib.chdir(cache_dir):  # COD downloads go to the relative dara_downloaded_cifs
        if mode == "known_mixture":
            for db in dbs:
                db.get_cifs_by_formulas(precursors, e_hull_filter=e_hull, copy_files=True, dest_dir=cifs_dir.as_posix())
        else:
            for db in dbs:
                db.get_cifs_by_chemsys(elements, e_hull_filter=e_hull, copy_files=True, dest_dir=cifs_dir.as_posix())
            for db in dbs:
                db.get_cifs_by_formulas(precursors, e_hull_filter=e_hull, copy_files=True, dest_dir=cifs_dir.as_posix())
    additional = settings["additional_cifs"]
    for cif in additional:
        (cifs_dir / f"{cif.name}.cif").write_text(str(cif))

    paths = sorted(cifs_dir.glob("*.cif"))
    paths, summary = deduplicate_cif_paths(paths)
    paths, rejected, _warnings = screen_candidate_cifs(
        paths,
        allowed_elements=elements if mode == "elemental" else None,
        protected_stems={cif.name for cif in additional},
        repaired=[],
    )
    summary = dict(summary)
    summary["rejected"] = len(rejected)
    summary["pool"] = len(paths)
    paths = sorted(paths)
    pool_file.write_text(
        json.dumps({"files": [p.name for p in paths], "stems": [p.stem for p in paths], "summary": summary},
                   indent=1, default=_json_default),
        encoding="utf-8",
    )
    return {"status": "ok", "paths": paths, "summary": summary, "cached": False}


# --------------------------------------------------------------------------- #
# simulation cache
# --------------------------------------------------------------------------- #
def make_cached_simulator(cache_dir: Path):
    """``simulate_reflections`` with an on-disk cache keyed by file hash, wavelength and range."""
    from dara.search.prefilter import simulate_reflections

    folder = Path(cache_dir) / "sim"
    folder.mkdir(parents=True, exist_ok=True)

    def simulate(path, wavelength_a, two_theta_range):
        digest = hashlib.sha1(Path(path).read_bytes()).hexdigest()
        key = f"{digest}_{wavelength_a:.5f}_{two_theta_range[0]:.2f}_{two_theta_range[1]:.2f}.pkl"
        target = folder / key
        if target.exists():
            try:
                return pickle.loads(target.read_bytes())
            except Exception:  # noqa: BLE001 - corrupt cache entry: recompute
                pass
        value = simulate_reflections(path, wavelength_a, two_theta_range)
        target.write_bytes(pickle.dumps(value))
        return value

    return simulate


# --------------------------------------------------------------------------- #
# ground-truth matching
# --------------------------------------------------------------------------- #
def stem_spacegroup(stem: str) -> int | None:
    parts = stem.split("_")
    return int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None


def match_gt_phase(gt_phase, stems: list[str], aliases: dict) -> tuple[list[str], list[str]]:
    """``(formula_and_sg_matches, formula_only_matches)`` among pool stems."""
    from dara.benchmark.scoring import formula_matches
    from dara.search.prefilter import stem_formula

    allowed = {gt_phase.spacegroup, *gt_phase.spacegroup_any_of} - {None}
    formula_only = []
    full = []
    for stem in stems:
        if not formula_matches(gt_phase.formula, gt_phase.accept_any_of, stem_formula(stem), aliases):
            continue
        formula_only.append(stem)
        group = stem_spacegroup(stem)
        if not allowed or (group is not None and group in allowed):
            full.append(stem)
    return full, formula_only


# --------------------------------------------------------------------------- #
# one sample
# --------------------------------------------------------------------------- #
def evaluate_sample(gt_set, sample, pattern, settings, pool: dict, floors: list[float], *, cache_dir: Path,
                    config=None, simulate=None) -> dict:
    from dara.search import prefilter

    config = config or prefilter.PrefilterConfig()
    config = replace(config, rel_floor=min(floors))
    stems = [p.stem for p in pool["paths"]]
    record = {
        "id": sample.id, "set": gt_set.set, "status": pool["status"], "pool": len(stems),
        "pool_summary": pool.get("summary", {}), "applied": False, "reason_not_applied": None,
        "prefilter_duration_s": None, "per_floor": {}, "gt": [],
    }
    if pool["status"] != "ok":
        return record
    range_params = (settings["search_kwargs"].get("refinement_params") or {})
    submission = sample.submission
    started = time.perf_counter()
    outcome = prefilter.prefilter_candidates(
        getattr(pattern, "angles", None), getattr(pattern, "intensities", None), pool["paths"],
        wavelength=settings["search_kwargs"].get("wavelength", submission.wavelength),
        wmin=range_params.get("wmin"), wmax=range_params.get("wmax"),
        excluded_ranges=range_params.get("excluded_ranges"),
        protected_stems={cif.name for cif in settings["additional_cifs"]},
        protected_formulas=settings["precursors"], enabled=True, config=config,
        simulate=simulate or make_cached_simulator(cache_dir), parallel=False,
    )
    record["prefilter_duration_s"] = round(time.perf_counter() - started, 2)
    report = outcome.report
    record["applied"] = bool(report.get("applied"))
    record["reason_not_applied"] = report.get("reason_not_applied")
    for key in ("insufficient_peaks", "simulation_failed", "simulation_timeout", "floor"):
        if key in report:
            record[key] = report[key]

    protected = _protected_stems(stems, settings)
    excluded_by_floor: dict[str, set[str]] = {}
    for floor in floors:
        key = floor_key(floor)
        if record["applied"]:
            excluded, _ = prefilter.decide(
                outcome.scores, {"sigma": report["floor"]["sigma"], "net_max": report["floor"]["net_max"]},
                config, protected=protected, rel_floor=floor,
            )
        else:
            excluded = []
        excluded_by_floor[key] = set(excluded)
        record["per_floor"][key] = {
            "excluded": len(excluded), "kept": len(stems) - len(excluded),
            "reduction": (len(excluded) / len(stems)) if stems else 0.0,
        }

    for phase in sample.phases:
        full, formula_only = match_gt_phase(phase, stems, gt_set.aliases)
        scores = [outcome.scores.get(stem) for stem in full if outcome.scores.get(stem) is not None]
        entry = {
            "formula": phase.formula, "spacegroup": phase.spacegroup, "required": phase.required,
            "in_pool": bool(full), "in_pool_formula_only": bool(formula_only) and not full,
            "matches": full, "best_score": max(scores) if scores else None,
            "protected": any(stem in protected for stem in full),
            "retained": {
                key: any(stem not in excluded for stem in full) for key, excluded in excluded_by_floor.items()
            },
        }
        record["gt"].append(entry)
    return record


def _protected_stems(stems: list[str], settings: dict) -> set[str]:
    from pymatgen.core import Composition

    from dara.search.prefilter import stem_formula

    reduced = set()
    for formula in settings["precursors"]:
        try:
            reduced.add(Composition(formula).reduced_formula)
        except Exception:  # noqa: BLE001
            continue
    names = {cif.name for cif in settings["additional_cifs"]}
    return {stem for stem in stems if stem in names or (reduced and stem_formula(stem) in reduced)}


# --------------------------------------------------------------------------- #
# summary and reports
# --------------------------------------------------------------------------- #
def summarize(samples: list[dict], floors: list[float], default_floor: float) -> dict:
    by_floor = {}
    for floor in floors:
        key = floor_key(floor)
        in_pool = retained = 0
        lost = []
        applied = [s for s in samples if s.get("applied")]
        for sample in samples:
            for phase in sample["gt"]:
                if not phase["in_pool"]:
                    continue
                in_pool += 1
                if phase["retained"].get(key, True):
                    retained += 1
                else:
                    lost.append({"sample": sample["id"], "formula": phase["formula"], "matches": phase["matches"],
                                 "best_score": phase["best_score"]})
        reductions = [s["per_floor"][key]["reduction"] for s in applied if key in s["per_floor"]]
        durations = [s["prefilter_duration_s"] for s in samples if s.get("prefilter_duration_s") is not None]
        unprotected = [
            phase["best_score"]
            for sample in samples
            for phase in sample["gt"]
            if phase["in_pool"] and not phase["protected"] and phase["best_score"] is not None
            and phase["retained"].get(key, True)
        ]
        by_floor[key] = {
            "gt_in_pool": in_pool, "gt_retained": retained,
            "retention": (retained / in_pool) if in_pool else 1.0,
            "samples_applied": len(applied),
            "median_reduction_applied": statistics.median(reductions) if reductions else None,
            "max_reduction_applied": max(reductions) if reductions else None,
            "max_duration_s": max(durations) if durations else None,
            "min_best_score_unprotected": min(unprotected) if unprotected else None,
            "lost": lost,
        }
    chosen = None
    for floor in sorted(floors, reverse=True):
        entry = by_floor[floor_key(floor)]
        margin = entry["min_best_score_unprotected"]
        if entry["retention"] == 1.0 and (margin is None or margin >= 2 * floor):
            chosen = floor
            break
    not_in_pool = [
        {"sample": s["id"], "formula": p["formula"], "spacegroup": p["spacegroup"],
         "formula_in_pool_with_other_spacegroup": p["in_pool_formula_only"]}
        for s in samples for p in s["gt"] if not p["in_pool"]
    ]
    return {
        "default_floor": default_floor,
        "by_floor": by_floor,
        "decision": {"largest_safe_floor": chosen,
                     "rule": "largest floor with retention 1.0 and min best score of unprotected retained GT phases >= 2 x floor"},
        "gt_not_in_pool": not_in_pool,
        "samples_not_run": [{"sample": s["id"], "status": s["status"]} for s in samples if s["status"] != "ok"],
    }


def render_markdown(result: dict) -> str:
    floors = list(result["summary"]["by_floor"])
    lines = ["# Pre-filter retention", ""]
    lines += ["| floor | GT in pool | retained | retention | samples applied | median reduction | max time (s) |",
              "|---|---|---|---|---|---|---|"]
    for key in floors:
        entry = result["summary"]["by_floor"][key]
        median = entry["median_reduction_applied"]
        lines.append(
            f"| {key} | {entry['gt_in_pool']} | {entry['gt_retained']} | {entry['retention']:.1%} | "
            f"{entry['samples_applied']} | {'-' if median is None else f'{median:.1%}'} | "
            f"{'-' if entry['max_duration_s'] is None else entry['max_duration_s']} |"
        )
    lines += ["", f"Largest safe floor by the decision rule: **{result['summary']['decision']['largest_safe_floor']}**", ""]
    lines += ["| sample | pool | applied | " + " | ".join(f"kept@{k}" for k in floors) + " | lost GT phases |",
              "|---|---|---|" + "---|" * (len(floors) + 1)]
    default_key = floor_key(result["settings"]["default_floor"])
    for sample in result["samples"]:
        kept = " | ".join(
            str(sample["per_floor"].get(k, {}).get("kept", "-")) for k in floors
        )
        lost = ", ".join(
            phase["formula"] for phase in sample["gt"] if phase["in_pool"] and not phase["retained"].get(default_key, True)
        )
        status = sample["status"] if sample["status"] != "ok" else ("yes" if sample["applied"] else (sample["reason_not_applied"] or "no"))
        lines.append(f"| {sample['id']} | {sample['pool']} | {status} | {kept} | {lost or '-'} |")
    if result["summary"]["gt_not_in_pool"]:
        lines += ["", "## Ground-truth phases absent from the candidate pool", ""]
        for item in result["summary"]["gt_not_in_pool"]:
            lines.append(f"- {item['sample']}: {item['formula']} (SG {item['spacegroup']})")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", choices=["pairwise", "precursor", "all"], default="all", dest="gt_set")
    parser.add_argument("--ids", default=None, help="regular expression selecting sample ids")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/prefilter_cache"))
    parser.add_argument("--rel-floors", default=",".join(floor_key(f) for f in DEFAULT_FLOORS))
    parser.add_argument("--no-mp", action="store_true", help="skip the Materials Project database")
    parser.add_argument("--refresh", action="store_true", help="rebuild cached candidate pools")
    return parser.parse_args(argv)


def run(args) -> int:
    from dara.benchmark.ground_truth import load_ground_truth, repo_root
    from dara.search.prefilter import PrefilterConfig
    from dara.xrd import load_pattern

    floors = sorted({float(item) for item in str(args.rel_floors).split(",") if item.strip()})
    default_floor = PrefilterConfig().rel_floor
    if default_floor not in floors:
        floors = sorted({*floors, default_floor})
    names = ["pairwise", "precursor"] if args.gt_set == "all" else [args.gt_set]
    pattern_filter = re.compile(args.ids) if args.ids else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    root = repo_root()
    builder = _builder()

    samples = []
    for name in names:
        gt_set = load_ground_truth(name)
        for sample in gt_set.samples:
            if pattern_filter and not pattern_filter.search(sample.id):
                continue
            print(f"[{len(samples) + 1}] {sample.id}", flush=True)
            try:
                pattern = load_pattern(root / sample.pattern)
                submission = sample.submission
                job = builder(
                    pattern, Path(sample.pattern).name,
                    analysis_mode=submission.analysis_mode,
                    precursor_formulas=list(submission.precursors),
                    elements=list(submission.elements),
                    temperature=submission.temperature_c if submission.temperature_c is not None else -1,
                    atmosphere=submission.atmosphere,
                    instrument_profile=submission.instrument_profile,
                    wavelength=submission.wavelength,
                    use_cod=True, use_mp=not args.no_mp,
                )
                settings = job_settings(job)
                pool = build_pool(settings, sample.id, cache_dir, refresh=args.refresh)
                record = evaluate_sample(gt_set, sample, pattern, settings, pool, floors, cache_dir=cache_dir)
            except Exception as exc:  # noqa: BLE001 - one failing sample must not stop the run
                record = {"id": sample.id, "set": gt_set.set, "status": f"error: {type(exc).__name__}: {exc}",
                          "pool": 0, "applied": False, "per_floor": {}, "gt": []}
                print(f"    ERROR {record['status']}", flush=True)
            samples.append(record)
            (out / "retention.partial.json").write_text(
                json.dumps(_clean({"samples": samples}), indent=1, default=_json_default), encoding="utf-8"
            )

    summary = summarize(samples, floors, default_floor)
    result = {
        "settings": {"sets": names, "ids": args.ids, "rel_floors": floors, "default_floor": default_floor,
                     "use_mp": not args.no_mp, "cache_dir": str(cache_dir)},
        "samples": samples,
        "summary": summary,
    }
    result = _clean(result)
    (out / "retention.json").write_text(json.dumps(result, indent=1, default=_json_default), encoding="utf-8")
    (out / "retention.md").write_text(render_markdown(result), encoding="utf-8")
    (out / "retention.partial.json").unlink(missing_ok=True)

    entry = summary["by_floor"][floor_key(default_floor)]
    failed = [s for s in samples if str(s["status"]).startswith("error")]
    print(f"Retention at floor {default_floor:g}: {entry['gt_retained']}/{entry['gt_in_pool']} "
          f"({entry['retention']:.1%}); safe floor by rule: {summary['decision']['largest_safe_floor']}")
    return 0 if entry["retention"] == 1.0 and not failed else 1


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
