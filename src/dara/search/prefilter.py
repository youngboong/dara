"""Conservative pre-filter for candidate reference structures.

Screening runs one BGMN refinement per candidate, which dominates the run time
for broad databases. This module removes candidates that *cannot* be present in
the measured pattern, without running BGMN.

Statistic ("amount upper bound")
--------------------------------
A phase present in the sample with amount ``a`` shows its strong reflections
with heights proportional to their relative intensities ``I_rel`` (strongest = 1).
If the strongest peaks of a candidate fall where the pattern is flat, the data
only allow a tiny amount of it. For every strong predicted peak we divide the
largest net height seen in a small window around the (lattice-scaled and
shifted) position by ``I_rel``; the bound ``K`` is the ``k+1``-th smallest of
these ratios (``k = floor(outlier_fraction * n)`` worst peaks are ignored) and
is maximised over a small grid of lattice scales and zero shifts. ``K`` is the
largest amount (in counts) of the candidate that is consistent with the data.
A candidate is excluded only if ``K`` is below ``max(noise_k * sigma,
rel_floor * net_max)``. Candidates that cannot be judged (few strong peaks,
failed simulation, protected references) are always kept, and the best
``keep_top_n`` judged candidates are kept regardless.

The default state of the pre-filter is *off*; see ``resolve_prefilter_enabled``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d, uniform_filter1d

from dara.search import candidate_cost
from dara.utils import get_logger

logger = get_logger(__name__)

_MAX_EXCLUDED_REPORTED = 200


@dataclass(frozen=True)
class PrefilterConfig:
    min_candidates: int = 40  # applied only if len(candidates) > min_candidates
    keep_top_n: int = 40  # always keep the best-scoring judged candidates
    rel_floor: float = 0.03  # fraction of max net intensity (provisional)
    noise_k: float = 6.0  # floor >= noise_k * sigma
    strong_rel: float = 0.10  # predicted peak >= 10 % of the strongest in range
    max_strong: int = 15
    min_strong: int = 3
    outlier_fraction: float = 0.20  # k = floor(0.2*n) worst peaks ignored
    merge_deg: float = 0.10  # merge predicted reflections closer than this
    window_hw_deg: float = 0.08  # half width of the observed-max window
    scale_range: tuple[float, float] = (0.97, 1.03)  # d_obs = s * d_calc
    scale_step: float = 0.001
    shift_max_deg: float = 0.30  # displacement-like shift delta*cos(theta)
    shift_step_deg: float = 0.05
    edge_margin_deg: float = 0.5
    excluded_margin_deg: float = 0.3
    bkg_window_deg: float = 3.0
    smooth_half_width_deg: float = 0.015
    sim_timeout_s: float = 180.0
    batch_size: int = 8


@dataclass
class PrefilterOutcome:
    kept: list  # input objects, input order preserved, never copies
    report: dict  # JSON-safe, stored as candidate_summary["prefilter"]
    scores: dict[str, float | None] = field(default_factory=dict)  # stem -> K/net_max


@dataclass
class ObservedProfile:
    angles: np.ndarray
    window_max: np.ndarray
    lo: float
    hi: float
    step: float
    sigma: float
    net_max: float
    excluded: list
    n_points: int
    config: PrefilterConfig = field(default_factory=PrefilterConfig)

    @property
    def usable(self) -> bool:
        return (
            self.n_points >= 200
            and self.hi - self.lo >= 10.0
            and np.isfinite(self.net_max)
            and self.net_max > 0
            and np.isfinite(self.sigma)
        )

    @property
    def floor(self) -> float:
        return max(
            self.config.noise_k * self.sigma, self.config.rel_floor * self.net_max
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _path_of(candidate) -> Path:
    return Path(getattr(candidate, "path", candidate))


def _stem_of(candidate) -> str:
    return _path_of(candidate).stem


def stem_formula(stem: str) -> str | None:
    """Reduced formula encoded in a reference file stem (``Fe2O3_167_(cod_1)-0``)."""
    try:
        from pymatgen.core import Composition

        return Composition(stem.split("_")[0]).reduced_formula
    except Exception:  # noqa: BLE001
        return None


def _normalized_excluded(excluded_ranges) -> list:
    try:
        from dara.analysis_range import normalize_excluded_ranges

        return normalize_excluded_ranges(excluded_ranges)
    except Exception:  # noqa: BLE001
        return []


def _in_ranges(values: np.ndarray, ranges: list, margin: float = 0.0) -> np.ndarray:
    mask = np.zeros(values.shape, dtype=bool)
    for lower, upper in ranges:
        mask |= (values >= lower - margin) & (values <= upper + margin)
    return mask


def resolve_prefilter_enabled(
    requested: bool | None, analysis_mode: str, *, explicit_cifs: bool
) -> tuple[bool, str | None]:
    """Decide whether the pre-filter runs; returns ``(enabled, reason_not_enabled)``."""
    env = candidate_cost.prefilter_env()
    if env == "off":
        return False, "disabled_by_env"
    if analysis_mode == "known_mixture":
        return False, "known_mixture"
    if explicit_cifs:
        return False, "explicit_cifs"
    if requested is True:
        return True, None
    if requested is False:
        return False, "disabled"
    if env == "on":
        return True, None
    return False, "disabled"


# --------------------------------------------------------------------------- #
# observed pattern
# --------------------------------------------------------------------------- #
def observed_profile(
    angles, intensities, *, wmin=None, wmax=None, excluded_ranges=None, config: PrefilterConfig = PrefilterConfig()
) -> ObservedProfile:
    """Background-subtracted, locally maximised pattern used by the amount bound."""
    x = np.asarray(angles, dtype=float).ravel()
    y = np.asarray(intensities, dtype=float).ravel()
    n = min(len(x), len(y))
    x, y = x[:n], y[:n]
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    order = np.argsort(x, kind="stable")
    x, y = x[order], y[order]
    excluded = _normalized_excluded(excluded_ranges)
    empty = ObservedProfile(
        angles=x, window_max=y, lo=0.0, hi=0.0, step=0.0, sigma=float("nan"),
        net_max=0.0, excluded=excluded, n_points=0, config=config,
    )
    if len(x) < 5:
        return empty
    lo = max(float(x[0]), float(wmin) if wmin is not None else -math.inf)
    hi = min(float(x[-1]), float(wmax) if wmax is not None else math.inf)
    steps = np.diff(x)
    steps = steps[steps > 0]
    if not len(steps) or hi <= lo:
        return empty
    step = float(np.median(steps))

    m = max(1, 2 * round(config.smooth_half_width_deg / step) + 1)
    ys = uniform_filter1d(y, m, mode="nearest") if m > 1 else y
    width = max(5, round(config.bkg_window_deg / step))
    background = uniform_filter1d(minimum_filter1d(ys, width, mode="nearest"), width, mode="nearest")
    net = ys - background

    diffs = np.diff(y)
    mad = float(np.median(np.abs(diffs - np.median(diffs))))
    sigma = 1.4826 * mad / math.sqrt(2.0) / math.sqrt(m)
    window = max(1, round(2 * config.window_hw_deg / step)) + 1
    window_max = maximum_filter1d(net, window, mode="nearest")

    in_range = (x >= lo) & (x <= hi)
    valid = in_range & ~_in_ranges(x, excluded)
    net_max = float(net[valid].max()) if valid.any() else 0.0
    return ObservedProfile(
        angles=x, window_max=window_max, lo=lo, hi=hi, step=step, sigma=float(sigma),
        net_max=net_max, excluded=excluded, n_points=int(in_range.sum()), config=config,
    )


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
def simulate_reflections(
    path: Path, wavelength_a: float, two_theta_range: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(two_theta, intensity, d_hkl)`` of a CIF; raises on failure."""
    from dara.cif import Cif

    structure = Cif.from_file(path).to_structure()
    return simulate_structure_reflections(structure, wavelength_a, two_theta_range)


def simulate_structure_reflections(
    structure, wavelength_a: float, two_theta_range: tuple[float, float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(two_theta, intensity, d_hkl)`` of a pymatgen structure; raises on failure."""
    from pymatgen.analysis.diffraction.xrd import XRDCalculator

    pattern = XRDCalculator(wavelength=wavelength_a).get_pattern(
        structure, scaled=True, two_theta_range=tuple(two_theta_range)
    )
    d_values = np.array(
        [float(np.max(d)) if np.ndim(d) else float(d) for d in pattern.d_hkls]
    )
    return np.asarray(pattern.x, dtype=float), np.asarray(pattern.y, dtype=float), d_values


def _simulate_batch(paths: list[str], wavelength_a: float, two_theta_range: tuple[float, float]):
    out = []
    for path in paths:
        stem = Path(path).stem
        try:
            two_theta, intensity, d = simulate_reflections(Path(path), wavelength_a, two_theta_range)
            out.append((stem, two_theta, intensity, d))
        except Exception as exc:  # noqa: BLE001
            out.append((stem, None, None, None, f"{type(exc).__name__}: {str(exc)[:200]}"))
    return out


# --------------------------------------------------------------------------- #
# the bound
# --------------------------------------------------------------------------- #
def _merge_reflections(two_theta, intensity, d, merge_deg):
    order = np.argsort(two_theta, kind="stable")
    x, i, dd = two_theta[order], intensity[order], d[order]
    if len(x) == 0:
        return x, i, dd
    group = np.cumsum(np.r_[True, np.diff(x) > merge_deg]) - 1
    weight = np.bincount(group, i)
    safe = np.where(weight > 0, weight, 1.0)
    pos = np.bincount(group, i * x) / safe
    dsp = np.bincount(group, i * dd) / safe
    return pos, weight, dsp


def candidate_bound(
    profile: ObservedProfile,
    two_theta,
    intensity,
    d,
    *,
    wavelength_a: float,
    excluded_ranges=None,
    config: PrefilterConfig = PrefilterConfig(),
) -> dict:
    """Amount upper bound ``K`` of one candidate (``None`` if it cannot be judged)."""
    result = {"K": None, "n_strong": 0, "best_scale": None, "best_shift": None, "strong_2theta": []}
    two_theta = np.asarray(two_theta, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    d = np.asarray(d, dtype=float)
    ok = np.isfinite(two_theta) & np.isfinite(intensity) & np.isfinite(d) & (d > 0) & (intensity > 0)
    if not ok.any():
        return result
    x, weight, dsp = _merge_reflections(two_theta[ok], intensity[ok], d[ok], config.merge_deg)

    excluded = _normalized_excluded(excluded_ranges) if excluded_ranges is not None else profile.excluded
    lo, hi = profile.lo, profile.hi
    inside = (x > lo + config.edge_margin_deg) & (x < hi - config.edge_margin_deg)
    if not inside.any():
        return result
    rel = weight / weight[inside].max()
    usable = inside & ~_in_ranges(x, excluded, config.excluded_margin_deg) & (rel >= config.strong_rel)
    strong = np.where(usable)[0]
    strong = strong[np.argsort(-rel[strong], kind="stable")][: config.max_strong]
    n = len(strong)
    result["n_strong"] = int(n)
    result["strong_2theta"] = [round(float(v), 2) for v in x[strong][:5]]
    if n < config.min_strong:
        return result

    scales = np.arange(
        config.scale_range[0], config.scale_range[1] + config.scale_step / 2, config.scale_step
    )
    shifts = np.arange(
        -config.shift_max_deg, config.shift_max_deg + config.shift_step_deg / 2, config.shift_step_deg
    )
    s = scales[:, None, None]
    dz = shifts[None, :, None]
    d_strong = dsp[strong][None, None, :]
    arg = wavelength_a / (2.0 * s * d_strong)
    reachable = arg < 1.0
    t2 = 2.0 * np.degrees(np.arcsin(np.clip(arg, 0.0, 1.0)))
    pos = t2 + dz * np.cos(np.radians(t2 / 2.0))
    height = np.maximum(np.interp(pos, profile.angles, profile.window_max), 0.0)
    ratio = height / rel[strong][None, None, :]
    neutral = ~reachable | (pos < lo) | (pos > hi) | _in_ranges(pos, excluded, config.excluded_margin_deg)
    ratio = np.where(neutral, np.inf, ratio)
    ratio = np.sort(ratio, axis=-1)
    k = int(math.floor(config.outlier_fraction * n))
    grid = ratio[..., k]
    best = np.unravel_index(int(np.argmax(grid)), grid.shape)
    result["K"] = float(grid[best])
    result["best_scale"] = round(float(scales[best[0]]), 4)
    result["best_shift"] = round(float(shifts[best[1]]), 4)
    return result


# --------------------------------------------------------------------------- #
# decision (shared with scripts/check_prefilter_retention.py)
# --------------------------------------------------------------------------- #
def relative_floor(sigma: float, net_max: float, config: PrefilterConfig, rel_floor: float | None = None) -> float:
    """Exclusion threshold on ``K / net_max``."""
    rel = config.rel_floor if rel_floor is None else rel_floor
    noise = config.noise_k * sigma / net_max if net_max > 0 else 0.0
    return max(noise, rel)


def decide(
    scores: dict[str, float | None],
    floor_inputs: dict,
    config: PrefilterConfig = PrefilterConfig(),
    *,
    protected=(),
    rel_floor: float | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Apply the floor, protection and top-N safety net.

    ``scores`` maps stem -> ``K / net_max`` (``None`` = not judged), in input
    order; ``floor_inputs`` holds ``sigma`` and ``net_max``. Returns
    ``(excluded_stems, kept_reason)`` where ``kept_reason`` maps every kept stem
    to ``unjudged``, ``protected``, ``above_floor`` or ``kept_top_n``.
    """
    floor = relative_floor(floor_inputs["sigma"], floor_inputs["net_max"], config, rel_floor)
    protected = set(protected)
    judged = [stem for stem, score in scores.items() if score is not None]
    ranked = sorted(judged, key=lambda stem: -scores[stem])  # stable: ties keep input order
    top = set(ranked[: config.keep_top_n])
    excluded = []
    reasons = {}
    for stem, score in scores.items():
        if score is None:
            reasons[stem] = "unjudged"
        elif stem in protected:
            reasons[stem] = "protected"
        elif score >= floor:
            reasons[stem] = "above_floor"
        elif stem in top:
            reasons[stem] = "kept_top_n"
        else:
            excluded.append(stem)
    return excluded, reasons


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def _round(value, digits=4):
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _not_applied(candidates, enabled: bool, reason: str | None) -> PrefilterOutcome:
    kept = list(candidates)
    return PrefilterOutcome(
        kept=kept,
        report={
            "enabled": bool(enabled),
            "applied": False,
            "reason_not_applied": reason,
            "before": len(kept),
            "after": len(kept),
            "excluded": 0,
        },
        scores={},
    )


def _simulate_all(
    paths: list[Path],
    wavelength_a: float,
    two_theta_range: tuple[float, float],
    *,
    simulate,
    parallel: bool,
    config: PrefilterConfig,
) -> dict[str, tuple | str]:
    """stem -> (two_theta, intensity, d) or an error string ``"failed:..."``/``"timeout"``."""
    results: dict[str, tuple | str] = {}
    use_ray = False
    if parallel and simulate is None:
        try:
            import ray

            use_ray = ray.is_initialized()
        except Exception:  # noqa: BLE001
            use_ray = False

    if use_ray:
        import ray

        remote = ray.remote(num_cpus=1)(_simulate_batch)
        batches = [paths[i : i + config.batch_size] for i in range(0, len(paths), config.batch_size)]
        refs = [remote.remote([str(p) for p in batch], wavelength_a, two_theta_range) for batch in batches]
        done, pending = ray.wait(refs, num_returns=len(refs), timeout=config.sim_timeout_s)
        for ref in pending:
            try:
                ray.cancel(ref, force=False)
            except Exception:  # noqa: BLE001
                pass
        done_set = set(done)
        for batch, ref in zip(batches, refs):
            if ref not in done_set:
                for path in batch:
                    results[path.stem] = "timeout"
                continue
            try:
                items = ray.get(ref)
            except Exception as exc:  # noqa: BLE001
                for path in batch:
                    results[path.stem] = f"failed:{type(exc).__name__}: {str(exc)[:200]}"
                continue
            for item in items:
                if len(item) == 5:
                    results[item[0]] = f"failed:{item[4]}"
                else:
                    results[item[0]] = (item[1], item[2], item[3])
        return results

    function = simulate or simulate_reflections
    deadline = time.monotonic() + config.sim_timeout_s if parallel else None
    for path in paths:
        if deadline is not None and time.monotonic() > deadline:
            results[path.stem] = "timeout"
            continue
        try:
            results[path.stem] = tuple(function(path, wavelength_a, two_theta_range))
        except Exception as exc:  # noqa: BLE001
            results[path.stem] = f"failed:{type(exc).__name__}: {str(exc)[:200]}"
    return results


def prefilter_candidates(
    angles,
    intensities,
    candidates: list[Path],
    *,
    wavelength,
    wmin=None,
    wmax=None,
    excluded_ranges=None,
    protected_stems=(),
    protected_formulas=(),
    enabled: bool = True,
    config: PrefilterConfig = PrefilterConfig(),
    simulate=None,
    parallel: bool = True,
) -> PrefilterOutcome:
    """Remove candidates whose amount upper bound is below the floor.

    ``kept`` contains the *input objects* in input order. Anything that cannot
    be judged is kept. See the module docstring for the statistic.
    """
    started = time.perf_counter()
    candidates = list(candidates)
    if not enabled:
        return _not_applied(candidates, False, "disabled")
    if len(candidates) <= config.min_candidates:
        return _not_applied(candidates, True, "too_few_candidates")
    try:
        wavelength_a = candidate_cost.wavelength_angstrom(wavelength)
    except Exception:  # noqa: BLE001
        wavelength_a = float("nan")
    if not (0.3 <= wavelength_a <= 3.0):
        return _not_applied(candidates, True, "unsupported_wavelength")
    profile = observed_profile(
        angles, intensities, wmin=wmin, wmax=wmax, excluded_ranges=excluded_ranges, config=config
    )
    if not profile.usable:
        return _not_applied(candidates, True, "pattern_unsuitable")

    paths = [_path_of(c) for c in candidates]
    stems = [p.stem for p in paths]
    two_theta_range = (max(1.0, profile.lo - 6.0), min(178.0, profile.hi + 6.0))
    simulations = _simulate_all(
        paths, wavelength_a, two_theta_range, simulate=simulate, parallel=parallel, config=config
    )

    protected_stems = set(protected_stems or ())
    protected_reduced = set()
    for formula in protected_formulas or ():
        try:
            from pymatgen.core import Composition

            protected_reduced.add(Composition(formula).reduced_formula)
        except Exception:  # noqa: BLE001
            continue
    protected = set()
    for stem in stems:
        if stem in protected_stems or (protected_reduced and stem_formula(stem) in protected_reduced):
            protected.add(stem)

    scores: dict[str, float | None] = {}
    info: dict[str, dict] = {}
    counts = {"insufficient_peaks": 0, "simulation_failed": 0, "simulation_timeout": 0}
    for stem in stems:
        simulation = simulations.get(stem)
        if simulation == "timeout":
            counts["simulation_timeout"] += 1
            scores[stem] = None
            continue
        if simulation is None or isinstance(simulation, str):
            counts["simulation_failed"] += 1
            scores[stem] = None
            continue
        try:
            bound = candidate_bound(
                profile, *simulation, wavelength_a=wavelength_a,
                excluded_ranges=profile.excluded, config=config,
            )
        except Exception:  # noqa: BLE001
            counts["simulation_failed"] += 1
            scores[stem] = None
            continue
        info[stem] = bound
        if bound["K"] is None:
            counts["insufficient_peaks"] += 1
            scores[stem] = None
        else:
            scores[stem] = bound["K"] / profile.net_max

    excluded, reasons = decide(
        scores, {"sigma": profile.sigma, "net_max": profile.net_max}, config, protected=protected
    )
    excluded_set = set(excluded)
    kept = [c for c, stem in zip(candidates, stems) if stem not in excluded_set]

    from dara.structure_quality import reference_source

    excluded_entries = []
    for path, stem in zip(paths, stems):
        if stem not in excluded_set:
            continue
        bound = info[stem]
        excluded_entries.append(
            {
                "file": path.name,
                "source": reference_source(path.name),
                "score": _round(scores[stem]),
                "n_strong": bound["n_strong"],
                "best_scale": bound["best_scale"],
                "best_shift": bound["best_shift"],
                "strong_2theta": bound["strong_2theta"],
            }
        )
    excluded_entries.sort(key=lambda item: (item["score"] is None, item["score"] or 0.0))

    report = {
        "enabled": True,
        "applied": True,
        "reason_not_applied": None,
        "before": len(candidates),
        "after": len(kept),
        "excluded": len(excluded),
        "protected": len(protected),
        "insufficient_peaks": counts["insufficient_peaks"],
        "simulation_failed": counts["simulation_failed"],
        "simulation_timeout": counts["simulation_timeout"],
        "kept_top_n": sum(1 for reason in reasons.values() if reason == "kept_top_n"),
        "floor": {
            "rel_floor": config.rel_floor,
            "noise_k": config.noise_k,
            "sigma": _round(profile.sigma),
            "net_max": _round(profile.net_max),
            "value": _round(profile.floor),
        },
        "parameters": {
            "strong_rel": config.strong_rel,
            "max_strong": config.max_strong,
            "outlier_fraction": config.outlier_fraction,
            "scale_range": list(config.scale_range),
            "shift_max_deg": config.shift_max_deg,
            "window_hw_deg": config.window_hw_deg,
            "min_candidates": config.min_candidates,
            "keep_top_n": config.keep_top_n,
        },
        "duration_s": round(time.perf_counter() - started, 2),
        "excluded_candidates": excluded_entries[:_MAX_EXCLUDED_REPORTED],
    }
    logger.info(
        "Pre-filter kept %d of %d candidates (%d excluded, %.1f s).",
        len(kept), len(candidates), len(excluded), report["duration_s"],
    )
    return PrefilterOutcome(kept=kept, report=report, scores=scores)
