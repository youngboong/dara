"""Cheap cost estimates and runtime-option resolution for candidate references.

Pure functions only: no Ray, no network. They are used by

* the screening scheduler (``dara.search.tree``) to start the heaviest
  candidates first and to give very large cells a longer BGMN time limit,
* the submission UI (through ``estimate_runtime_s``) to show a rough run time,
* ``dara.jobs`` to resolve the optional environment switches.

The "weight" of a candidate is the estimated number of unique Bragg
reflections in the fitted 2-theta range; BGMN run time grows with that count.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path
from statistics import median

from dara.utils import get_logger

logger = get_logger(__name__)

LIGHT_MAX = 400  # n_reflections < 400 -> "light"
HEAVY_MAX = 1200  # 400 <= n < 1200 -> "heavy"; >= 1200 -> "very_heavy"
DEFAULT_ASSUMED_REFLECTIONS = 300
PREFILTER_MIN_CANDIDATES = 40
PREFILTER_TYPICAL_KEEP_FRACTION = 0.85  # used only for the submission estimate
LEGACY_SCREENING_TIMEOUT_S = 1200

SCREENING_TIMEOUT_MIN_S = 60
MIN_BUDGET_S = 300

_OFF_WORDS = {"0", "off", "false", "no", "legacy"}
_ON_WORDS = {"1", "on", "true", "yes"}


# --------------------------------------------------------------------------- #
# reflection counts
# --------------------------------------------------------------------------- #
def wavelength_angstrom(wavelength: str | float) -> float:
    """Wavelength in angstrom for a target name ("Cu") or a value in nm."""
    from dara.utils import get_wavelength

    return float(get_wavelength(wavelength)) * 10.0


@lru_cache(maxsize=512)
def _group_divisor(sg_number: int, primitive: bool) -> int:
    from pymatgen.symmetry.groups import PointGroup, SpaceGroup

    space_group = SpaceGroup.from_int_number(int(sg_number))
    point_group = PointGroup(space_group.point_group)
    has_inversion = any(
        _is_minus_identity(op.rotation_matrix) for op in point_group.symmetry_ops
    )
    order = point_group.order if primitive else space_group.order
    return int(order) * (1 if has_inversion else 2)


def _is_minus_identity(matrix) -> bool:
    import numpy as np

    return bool(np.allclose(np.asarray(matrix, dtype=float), -np.eye(3), atol=1e-6))


def reflections_from_cell(
    volume_a3: float,
    sg_number: int,
    wavelength,
    two_theta_max: float,
    *,
    primitive: bool = False,
) -> int:
    """Estimate the number of unique reflections up to ``two_theta_max``.

    ``R = 2 sin(2theta_max / 2) / lambda`` and ``N = 4/3 pi R^3 V`` count the
    reciprocal lattice points of the given cell inside the limiting sphere.
    Dividing by the space-group order (which includes centring translations for
    a conventional cell) leaves the symmetry-unique reflections; a further
    factor 2 merges Friedel pairs when the point group has no inversion.
    """
    import numpy as np

    if volume_a3 is None or not np.isfinite(volume_a3) or volume_a3 <= 0:
        raise ValueError("Cell volume must be positive")
    sg_number = int(sg_number)
    if not 1 <= sg_number <= 230:
        raise ValueError(f"Invalid space-group number: {sg_number}")
    lam = wavelength_angstrom(wavelength)
    radius = 2.0 * math.sin(math.radians(float(two_theta_max) / 2.0)) / lam
    count = 4.0 / 3.0 * math.pi * radius**3 * float(volume_a3)
    return max(1, round(count / _group_divisor(sg_number, bool(primitive))))


def _stem_spacegroup(stem: str) -> int | None:
    parts = stem.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        number = int(parts[1])
        if 1 <= number <= 230:
            return number
    return None


def estimate_reflections(path_or_structure, wavelength, two_theta_max: float) -> int | None:
    """Reflection count of a CIF file or structure; ``None`` if it cannot be read."""
    try:
        structure = path_or_structure
        sg_number = None
        if not hasattr(structure, "volume"):
            from dara.cif import Cif

            path = Path(getattr(path_or_structure, "path", path_or_structure))
            structure = Cif.from_file(path).to_structure()
            sg_number = _stem_spacegroup(path.stem)
        if sg_number is None:
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

            sg_number = SpacegroupAnalyzer(structure, symprec=0.1).get_space_group_number()
        return reflections_from_cell(structure.volume, sg_number, wavelength, two_theta_max)
    except Exception:  # noqa: BLE001 - an estimate must never break a job
        return None


def classify_weight(n: int | None) -> str:
    """``light`` / ``heavy`` / ``very_heavy`` / ``unknown`` for a reflection count."""
    if n is None:
        return "unknown"
    if n < LIGHT_MAX:
        return "light"
    if n < HEAVY_MAX:
        return "heavy"
    return "very_heavy"


# --------------------------------------------------------------------------- #
# run-time estimate
# --------------------------------------------------------------------------- #
def estimate_runtime_s(
    n_candidates: int,
    reflection_counts: list[int],
    n_workers: int,
    *,
    data_points: int | None = None,
) -> dict:
    """Rough wall-clock estimate of a complete run (heuristic v1, to be calibrated).

    Formula (seconds)::

        counts      = given reflection counts, missing entries filled with the
                      median of the given ones (or 300 when none are given)
        point_f     = max(0.5, (data_points or 8000) / 15000)
        t_i         = 20 * (1 + n_i / 300) * point_f          # one screening run
        screening   = sum(t_i) / workers + max(t_i)
        tree        = 3 * ceil(min(0.6 * n, 2 * workers) / workers) * 2.5 * median(t_i)
        final       = 5 * 90
        eflech      = 120
        expected    = eflech + screening + tree + final
        low, high   = 0.5 * expected, 2 * expected
    """
    n_candidates = int(n_candidates or 0)
    workers = max(1, int(n_workers or 1))
    if n_candidates <= 0:
        return {
            "low_s": 0,
            "expected_s": 0,
            "high_s": 0,
            "assumptions": ["No candidate references: nothing to estimate."],
        }
    given = [int(n) for n in (reflection_counts or []) if n is not None and n > 0]
    fill = int(median(given)) if given else DEFAULT_ASSUMED_REFLECTIONS
    counts = given[:n_candidates]
    counts += [fill] * (n_candidates - len(counts))

    point_factor = max(0.5, (data_points or 8000) / 15000)
    times = [20.0 * (1.0 + n / 300.0) * point_factor for n in counts]
    screening = sum(times) / workers + max(times)
    tree = (
        3 * math.ceil(min(0.6 * n_candidates, 2 * workers) / workers) * 2.5 * median(times)
    )
    final = 5 * 90
    eflech = 120
    expected = eflech + screening + tree + final
    return {
        "low_s": int(round(0.5 * expected)),
        "expected_s": int(round(expected)),
        "high_s": int(round(2.0 * expected)),
        "assumptions": [
            f"{n_candidates} candidate references screened on {workers} parallel worker(s).",
            (
                f"Per-reference cost 20 s x (1 + reflections/300) x {point_factor:.2f} "
                f"(pattern length factor); reflection counts known for {len(given)} reference(s), "
                f"{fill} assumed for the rest."
            ),
            "Phase-combination search assumed to need three rounds of expansions.",
            "Final refinement assumed for 5 hypotheses at 90 s each; peak detection 120 s.",
            "Heuristic estimate (0.5x to 2x range); real run time depends on the data and the machine.",
        ],
    }


# --------------------------------------------------------------------------- #
# environment switches
# --------------------------------------------------------------------------- #
def env_setting(name: str) -> str | float | None:
    """Parse an optional environment switch.

    Returns ``"off"``, ``"on"``, a positive ``float`` or ``None`` (unset, empty or
    unrecognised; garbage logs a warning).
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if text in _OFF_WORDS:
        return "off"
    if text in _ON_WORDS:
        return "on"
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and math.isfinite(number) and number > 0:
        return number
    logger.warning("Ignoring unrecognised value %r of environment variable %s.", raw, name)
    return None


def prefilter_env() -> str | None:
    """``"off"``, ``"on"`` or ``None`` for ``DARA_CANDIDATE_PREFILTER``."""
    value = env_setting("DARA_CANDIDATE_PREFILTER")
    return value if value in {"off", "on"} else None


def _legacy_timeouts() -> dict:
    return {
        "default": float(LEGACY_SCREENING_TIMEOUT_S),
        "very_heavy": float(LEGACY_SCREENING_TIMEOUT_S),
        "source": "legacy",
    }


def resolve_screening_timeouts(requested: float | None) -> dict:
    """Per-candidate screening timeouts.

    Returns ``{"default", "very_heavy", "source"}``. The env switch ``off``
    forces the legacy 1200 s everywhere; otherwise a submitted value wins over
    ``DARA_SCREENING_TIMEOUT_S``; with neither, the legacy value is used.
    """
    env = env_setting("DARA_SCREENING_TIMEOUT_S")
    if env == "off":
        return _legacy_timeouts()
    source = None
    value = None
    if requested is not None:
        try:
            value = float(requested)
            source = "submission"
        except (TypeError, ValueError):
            value = None
        if value is not None and not math.isfinite(value):
            value = None
    if value is None and isinstance(env, float):
        value, source = env, "env"
    if value is None or source is None:
        return _legacy_timeouts()
    timeout = min(float(LEGACY_SCREENING_TIMEOUT_S), max(float(SCREENING_TIMEOUT_MIN_S), value))
    return {
        "default": timeout,
        "very_heavy": min(2.0 * timeout, float(LEGACY_SCREENING_TIMEOUT_S)),
        "source": source,
    }


def resolve_budget_s(requested: float | None) -> float | None:
    """Run-time budget in seconds, or ``None`` for no limit (minimum 300 s)."""
    env = env_setting("DARA_MAX_RUNTIME_S")
    if env == "off":
        return None
    value = None
    if requested is not None:
        try:
            value = float(requested)
        except (TypeError, ValueError):
            value = None
        if value is not None and (not math.isfinite(value) or value <= 0):
            value = None
    if value is None and isinstance(env, float):
        value = env
    if value is None:
        return None
    return max(float(MIN_BUDGET_S), value)


def describe_weights(counts: list[int | None]) -> dict[str, int]:
    """Histogram of weight classes for a list of reflection counts."""
    out = {"light": 0, "heavy": 0, "very_heavy": 0, "unknown": 0}
    for n in counts:
        out[classify_weight(n)] += 1
    return out
