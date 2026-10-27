"""Reference-structure provenance and quality management.

Part A: where a reference CIF came from (COD / Materials Project / ICSD / user)
and the lattice-parameter window applied to computed (Materials Project)
structures during the final refinement.

Part B: sanity checks on candidate CIFs before they enter the phase search.
Only clearly unusable structures are rejected; anything uncertain is reported
as a warning so that recall is never reduced silently.

Two kill switches keep the behaviour measurable:

* ``DARA_MP_LATTICE_WINDOW=off`` disables the wider lattice window for MP phases.
* ``DARA_CIF_VALIDATION=off`` disables candidate CIF screening (and the automatic
  repair of structurally inconsistent database CIFs, see Part C).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from dara.utils import get_logger

if TYPE_CHECKING:
    from dara.refine import RefinementPhase

logger = get_logger(__name__)

# Final-refinement lattice window (fractional change) applied to computed
# (Materials Project) structures: DFT cells are typically a few percent too
# large or small (PBE overestimates), so the lower bound is wider. The upper
# bound equals the default final-refinement range so the window is always a
# superset of the one used for experimental references.
MP_LATTICE_WINDOW: tuple[float, float] = (-0.045, 0.02)

DEFAULT_FINAL_LATTICE_RANGE = 0.02
_OFF_VALUES = {"0", "off", "false", "no"}

_MARKER = re.compile(
    r"_\((cod|mp|icsd)[_-]([^)]+)\)(?:-(-?\d+|None))?$", re.IGNORECASE
)
_LOOSE_MARKER = re.compile(r"_\((cod|mp|icsd)[_-]", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Part A: provenance and lattice window
# --------------------------------------------------------------------------- #
def _stem(name_or_path) -> str:
    if name_or_path is None:
        return ""
    text = str(name_or_path).strip()
    if not text:
        return ""
    if text.lower().endswith(".cif"):
        text = Path(text).name[:-4]
    else:
        # A directory part never belongs to a phase name.
        text = Path(text).name if ("/" in text or "\\" in text) else text
    return text


def reference_source(name_or_path) -> str:
    """Return ``cod``, ``mp``, ``icsd``, ``user`` or ``unknown`` (empty name)."""
    stem = _stem(name_or_path)
    if not stem:
        return "unknown"
    match = _MARKER.search(stem) or _LOOSE_MARKER.search(stem)
    return match.group(1).lower() if match else "user"


def _normalise_mp_id(raw: str) -> str:
    raw = raw.strip()
    if raw.lower().startswith("mp-"):
        return "mp-" + raw[3:]
    return f"mp-{raw}"


def reference_provenance(name: str) -> dict:
    """Describe where a reference structure came from, from its file/phase name."""
    stem = _stem(name)
    result = {
        "source": "unknown",
        "database_id": None,
        "computed": False,
        "label": "Source not recorded",
        "url": None,
        "e_above_hull_mev": None,
    }
    if not stem:
        return result
    match = _MARKER.search(stem)
    if match is None:
        loose = _LOOSE_MARKER.search(stem)
        result["source"] = loose.group(1).lower() if loose else "user"
        if loose is None:
            result["label"] = "User-supplied CIF (source not recorded)"
        else:
            result["label"] = {
                "cod": "Experimental structure (COD)",
                "mp": "Computed structure (Materials Project)",
                "icsd": "Experimental structure (ICSD)",
            }[result["source"]]
            result["computed"] = result["source"] == "mp"
        return result

    source = match.group(1).lower()
    raw_id = match.group(2).strip()
    hull = match.group(3)
    result["source"] = source
    result["e_above_hull_mev"] = (
        int(hull) if hull is not None and hull.lower() != "none" else None
    )
    if source == "mp":
        database_id = _normalise_mp_id(raw_id)
        result.update(
            database_id=database_id,
            computed=True,
            label=f"Computed structure (Materials Project {database_id})",
            url=f"https://materialsproject.org/materials/{database_id}",
        )
    elif source == "cod":
        result.update(
            database_id=raw_id,
            label=f"Experimental structure (COD {raw_id})",
            url=f"https://www.crystallography.net/cod/{raw_id}.html",
        )
    else:
        result.update(
            database_id=raw_id,
            label=f"Experimental structure (ICSD {raw_id})",
        )
    return result


def _switch(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in _OFF_VALUES


def mp_window_enabled() -> bool:
    """True unless ``DARA_MP_LATTICE_WINDOW`` is 0/off/false/no."""
    return _switch("DARA_MP_LATTICE_WINDOW")


def cif_validation_enabled() -> bool:
    """True unless ``DARA_CIF_VALIDATION`` is 0/off/false/no."""
    return _switch("DARA_CIF_VALIDATION")


def widen_for_mp(global_range, window: tuple[float, float] = MP_LATTICE_WINDOW):
    """Widen a scalar lattice range to cover the MP window.

    Strings (``"fixed"``), tuples and dicts are returned unchanged.
    """
    if isinstance(global_range, bool) or not isinstance(global_range, (int, float)):
        return global_range
    radius = abs(float(global_range))
    return (min(-radius, window[0]), max(radius, window[1]))


def effective_lattice_range(
    phase_name: str,
    global_range,
    *,
    additional_cif_names: Iterable[str] = (),
    additional_params: dict | None = None,
    overrides: dict | None = None,
    mp_window: tuple[float, float] | None = None,
):
    """Lattice range a phase is refined with.

    Precedence (low to high): global range; MP widening (only when
    ``mp_window`` is given and the phase is an MP structure); the additional
    CIF parameters (only for user-supplied CIF names); per-phase overrides.
    """
    value = global_range
    if mp_window is not None and reference_source(phase_name) == "mp":
        value = widen_for_mp(value, mp_window)
    if (
        additional_params
        and "lattice_range" in additional_params
        and phase_name in set(additional_cif_names or ())
    ):
        value = additional_params["lattice_range"]
    if overrides and "lattice_range" in overrides:
        value = overrides["lattice_range"]
    return value


def apply_mp_lattice_window(
    phases,
    global_params: dict | None,
    *,
    enabled: bool,
    window: tuple[float, float] = MP_LATTICE_WINDOW,
) -> tuple[list[RefinementPhase], list[str]]:
    """Give MP phases a wider per-phase ``lattice_range`` for the final refinement.

    Phases with their own ``lattice_range`` and non-MP phases are untouched.
    Returns the new phase list and the stems of the phases that were widened.
    """
    from dara.refine import RefinementPhase

    made = [RefinementPhase.make(phase) for phase in phases]
    if not enabled:
        return made, []

    global_range = (global_params or {}).get("lattice_range", DEFAULT_FINAL_LATTICE_RANGE)
    widened_range = widen_for_mp(global_range, window)
    if widened_range == global_range:
        return made, []

    out: list[RefinementPhase] = []
    applied: list[str] = []
    for phase in made:
        if (
            reference_source(phase.path.stem) == "mp"
            and "lattice_range" not in phase.params
        ):
            out.append(
                RefinementPhase(
                    path=phase.path,
                    params={**phase.params, "lattice_range": widened_range},
                )
            )
            applied.append(phase.path.stem)
        else:
            out.append(phase)
    return out, applied


# --------------------------------------------------------------------------- #
# Part B: CIF validation and candidate screening
# --------------------------------------------------------------------------- #
_MAX_SITES = 400
_MAX_WARNINGS = 100
_DENSITY_RANGE = (0.5, 25.0)
_OCC_REJECT = 1.10
_OCC_WARN = 1.02
_MIN_VOLUME = 1e-6


def _issue(code: str, severity: str, message: str) -> dict:
    return {"code": code, "severity": severity, "message": message}


def _number(value) -> float | None:
    if value is None:
        return None
    text = re.sub(r"\(\d*\)$", "", str(value).strip())
    try:
        return float(text)
    except ValueError:
        return None


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _raw_blocks(path: Path) -> list[dict]:
    """Raw key/value data of every CIF block (empty when unreadable)."""
    try:
        from dara.cif import Cif

        cif = Cif.from_file(path)
        return [dict(block.data) for block in cif.data.values()]
    except Exception:
        return []


def _raw_issues(blocks: list[dict]) -> list[dict]:
    """Checks that do not need a successfully built structure."""
    issues: list[dict] = []
    for data in blocks:
        lengths = [_number(data.get(f"_cell_length_{axis}")) for axis in "abc"]
        if all(value is not None for value in lengths) and any(
            value <= 0 for value in lengths
        ):
            issues.append(
                _issue(
                    "negative_or_zero_lattice",
                    "reject",
                    "The unit cell has a zero or negative lattice length.",
                )
            )
            break

    for data in blocks:
        occupancies = [_number(item) for item in _as_list(data.get("_atom_site_occupancy"))]
        if not occupancies:
            continue
        coords = [
            _as_list(data.get(f"_atom_site_fract_{axis}")) for axis in "xyz"
        ]
        groups = _as_list(data.get("_atom_site_disorder_group"))
        sums: dict[tuple, float] = {}
        for index, occ in enumerate(occupancies):
            if occ is None:
                continue
            if all(len(c) > index for c in coords):
                position = tuple(
                    round(_number(c[index]) or 0.0, 3) for c in coords
                )
            else:
                position = (index,)
            group = groups[index] if index < len(groups) else ""
            key = (position, str(group))
            sums[key] = sums.get(key, 0.0) + occ
        biggest = max(sums.values(), default=0.0)
        if biggest > _OCC_REJECT:
            issues.append(
                _issue(
                    "occupancy_sum_gt_1",
                    "reject",
                    f"Site occupancies sum to {biggest:.2f} on at least one site (> {_OCC_REJECT:.2f}).",
                )
            )
        elif biggest > _OCC_WARN:
            issues.append(
                _issue(
                    "occupancy_sum_gt_1",
                    "warning",
                    f"Site occupancies sum to {biggest:.2f} on at least one site (> {_OCC_WARN:.2f}).",
                )
            )
        break

    for data in blocks:
        symbols = _as_list(data.get("_atom_site_type_symbol"))
        if _has_dummy_symbol(symbols):
            issues.append(
                _issue(
                    "dummy_species",
                    "reject",
                    "The structure contains a dummy (non-element) species.",
                )
            )
            break
    return issues


def _has_dummy_symbol(symbols: list) -> bool:
    from pymatgen.core.periodic_table import DummySpecies, get_el_sp

    for raw in symbols:
        text = str(raw).strip()
        if not text:
            continue
        try:
            if isinstance(get_el_sp(text), DummySpecies):
                return True
            continue
        except Exception:
            pass
        try:
            DummySpecies(text)
            return True
        except Exception:
            continue
    return False


def validate_cif(
    path: Path,
    *,
    allowed_elements: Iterable[str] | None = None,
    plan: dict | None = None,
) -> list[dict]:
    """Return the quality issues of one CIF (empty when it looks fine).

    ``plan`` is an optional pre-computed :func:`plan_cif_repair` result, to avoid
    analysing the same file twice.
    """
    path = Path(path)
    blocks = _raw_blocks(path)
    issues = _raw_issues(blocks)
    rejected = any(item["severity"] == "reject" for item in issues)
    if not rejected:
        issues.extend(consistency_issues(path, plan=plan))

    structure = None
    try:
        import warnings

        from dara.cif import Cif

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = Cif.from_file(path).to_structure()
    except Exception as exc:
        if not rejected:
            issues.append(
                _issue(
                    "parse_error",
                    "warning",
                    f"The structure could not be built by pymatgen ({type(exc).__name__}); "
                    "it will still be tried in the search.",
                )
            )
    if structure is None:
        return issues

    existing = {item["code"] for item in issues}
    try:
        from pymatgen.core.periodic_table import DummySpecies

        n_sites = len(structure)
        if n_sites == 0:
            issues.append(_issue("no_sites", "reject", "The structure has no atomic sites."))
            return issues

        dummy = "dummy_species" in existing or any(
            isinstance(species, DummySpecies) for species in structure.composition.keys()
        )
        if dummy and "dummy_species" not in existing:
            issues.append(
                _issue(
                    "dummy_species",
                    "reject",
                    "The structure contains a dummy (non-element) species.",
                )
            )

        lattice = structure.lattice
        if (
            min(lattice.a, lattice.b, lattice.c) <= 0
            or lattice.volume <= _MIN_VOLUME
        ) and "negative_or_zero_lattice" not in existing:
            issues.append(
                _issue(
                    "negative_or_zero_lattice",
                    "reject",
                    "The unit cell has a zero or negative size.",
                )
            )
        elif not dummy:
            density = float(structure.density)
            if density < _DENSITY_RANGE[0] or density > _DENSITY_RANGE[1]:
                issues.append(
                    _issue(
                        "unphysical_density",
                        "reject",
                        f"The computed density is {density:.2f} g/cm3, outside "
                        f"{_DENSITY_RANGE[0]}-{_DENSITY_RANGE[1]:g} g/cm3.",
                    )
                )

        if n_sites > _MAX_SITES:
            issues.append(
                _issue(
                    "too_many_sites",
                    "warning",
                    f"The cell has {n_sites} sites (> {_MAX_SITES}); refinement may be slow.",
                )
            )

        if allowed_elements is not None:
            allowed = {str(element) for element in allowed_elements}
            present = {element.symbol for element in structure.composition.elements}
            outside = sorted(present - allowed)
            if outside:
                issues.append(
                    _issue(
                        "elements_outside_system",
                        "warning",
                        "The structure contains elements outside the selected system: "
                        + ", ".join(outside)
                        + ".",
                    )
                )
    except Exception as exc:  # defensive: never fail the job because of a check
        logger.warning("CIF validation of %s was incomplete: %s", path, exc)
    return issues


# --------------------------------------------------------------------------- #
# Part C: CIF consistency check and automatic repair
# --------------------------------------------------------------------------- #
# Some database CIFs (typically transcribed from old tables such as Wyckoff's
# "Structure of Crystals") list symmetry-equivalent atoms explicitly in the
# atom loop *and* give the space-group operations. Expanding such a file puts
# several atoms on the same position, which doubles the scattering power and
# changes the composition (e.g. COD 5910316, CoWO4 -> "Co2WO4").
#
# The check is deliberately conservative: it only acts when atoms of the same
# element that come from DIFFERENT atom-loop labels sit closer than
# ``_DUP_DISTANCE`` (special positions of one label and partial-occupancy
# disorder are fine), and it repairs only database CIFs whose repaired
# composition reproduces ``_chemical_formula_sum`` (or that have no such tag
# and become overlap-free).
_DUP_DISTANCE = 0.5  # Angstrom
_ORBIT_TOL = 0.05  # Angstrom, merges images of one label on a special position
_DISORDER_OCC_SUM = 1.02
_FORMULA_TOL = 0.01  # tolerance on atomic fractions
_MAX_LABELS = 300
_MAX_IMAGES = 4000
_MAX_PRUNE_LABELS = 14
_REPAIRABLE_SOURCES = {"cod", "icsd", "mp"}


def _element_symbol(*candidates) -> str | None:
    from pymatgen.core.periodic_table import Element

    for raw in candidates:
        match = re.match(r"([A-Z][a-z]?)", str(raw or "").strip())
        if not match:
            continue
        symbol = match.group(1)
        if Element.is_valid_symbol(symbol):
            return symbol
        if Element.is_valid_symbol(symbol[0]):
            return symbol[0]
    return None


def _load_model(path: Path) -> dict | None:
    """Expand the atom loop of ``path`` with its symmetry operations.

    Returns ``None`` whenever the file cannot be analysed reliably.
    """
    import warnings

    import numpy as np
    from pymatgen.core import Lattice
    from pymatgen.io.cif import CifParser

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parser = CifParser(str(path))
        blocks = [
            block
            for block in parser._cif.data.values()
            if "_atom_site_fract_x" in block.data
        ]
        if len(blocks) != 1:
            return None
        block = blocks[0]
        data = block.data
        try:
            ops = parser.get_symops(block)
        except Exception:
            return None
    if not ops:
        return None

    cell = [_number(data.get(f"_cell_length_{axis}")) for axis in "abc"] + [
        _number(data.get(f"_cell_angle_{axis}")) for axis in ("alpha", "beta", "gamma")
    ]
    if any(value is None for value in cell) or any(value <= 0 for value in cell[:3]):
        return None
    try:
        lattice = Lattice.from_parameters(*cell)
    except Exception:
        return None
    if lattice.volume <= _MIN_VOLUME:
        return None

    columns = [_as_list(data.get(f"_atom_site_fract_{axis}")) for axis in "xyz"]
    n_rows = len(columns[0])
    if n_rows < 2 or n_rows > _MAX_LABELS or any(len(c) != n_rows for c in columns):
        return None
    labels = [str(item) for item in _as_list(data.get("_atom_site_label"))]
    if len(labels) != n_rows:
        labels = [f"atom{i + 1}" for i in range(n_rows)]
    symbols = _as_list(data.get("_atom_site_type_symbol"))
    occupancies = _as_list(data.get("_atom_site_occupancy"))
    groups = _as_list(data.get("_atom_site_disorder_group"))

    rows = []
    total_images = 0
    for index in range(n_rows):
        coords = [_number(column[index]) for column in columns]
        if any(value is None for value in coords):
            return None
        element = _element_symbol(
            symbols[index] if len(symbols) == n_rows else None, labels[index]
        )
        if element is None:
            return None
        occ = 1.0
        if len(occupancies) == n_rows:
            parsed = _number(occupancies[index])
            occ = 1.0 if parsed is None else parsed
        group = str(groups[index]).strip() if len(groups) == n_rows else ""
        images = np.array([op.operate(coords) for op in ops], dtype=float) % 1.0
        images = np.round(images, 8) % 1.0
        if len(images) > 1:
            distances = lattice.get_all_distances(images, images)
            keep: list[int] = []
            for i in range(len(images)):
                if all(distances[i, j] > _ORBIT_TOL for j in keep):
                    keep.append(i)
            images = images[keep]
        total_images += len(images)
        rows.append(
            {
                "label": labels[index],
                "element": element,
                "occ": occ,
                "group": group if group not in {".", "?"} else "",
                "images": images,
            }
        )
    if total_images > _MAX_IMAGES:
        return None
    return {
        "parser": parser,
        "block": block,
        "lattice": lattice,
        "rows": rows,
        "rotations": [np.array(op.rotation_matrix, dtype=float) for op in ops],
    }


def _compatible(a: dict, b: dict) -> bool:
    """True when two rows may legitimately share a position (disorder)."""
    if a["group"] and b["group"] and a["group"] != b["group"]:
        return True
    return a["occ"] + b["occ"] <= _DISORDER_OCC_SUM


def _find_overlaps(model: dict, active: list[int]):
    """Return ``(redundant, partial)`` among the ``active`` rows.

    ``redundant`` maps a later row to the first row it duplicates (all of its
    images lie on atoms of earlier rows); ``partial`` holds rows with only some
    images on earlier atoms (not safely removable).
    """
    import numpy as np

    lattice = model["lattice"]
    rows = model["rows"]
    redundant: dict[int, int] = {}
    partial: dict[int, int] = {}
    kept: list[int] = []
    for j in active:
        row = rows[j]
        others = [
            i
            for i in kept
            if rows[i]["element"] == row["element"] and not _compatible(rows[i], row)
        ]
        if others:
            covered = np.zeros(len(row["images"]), dtype=bool)
            first = None
            for i in others:
                hits = (
                    lattice.get_all_distances(row["images"], rows[i]["images"]).min(axis=1)
                    < _DUP_DISTANCE
                )
                if hits.any() and first is None:
                    first = i
                covered |= hits
            if covered.all():
                redundant[j] = first
                continue
            if covered.any():
                partial[j] = first
        kept.append(j)
    return redundant, partial


def _composition(model: dict, active) -> dict[str, float]:
    out: dict[str, float] = {}
    for index in active:
        row = model["rows"][index]
        out[row["element"]] = out.get(row["element"], 0.0) + len(row["images"]) * row["occ"]
    return out


def _declared_composition(model: dict):
    from pymatgen.core import Composition

    raw = model["block"].data.get("_chemical_formula_sum")
    if raw is None:
        return None
    try:
        comp = Composition(str(raw).strip().strip("'\""))
    except Exception:
        return None
    # Hydrogen is routinely missing from the atom list (X-ray structures,
    # AMCSD "attached hydrogens") although it is in the formula: do not count
    # that as a mismatch.
    present = {row["element"] for row in model["rows"]}
    amounts = {
        str(el): float(amt)
        for el, amt in comp.items()
        if str(el) in present or str(el) not in {"H", "D", "T"}
    }
    if not amounts:
        return None
    return Composition(amounts)


def _fractions(amounts: dict) -> dict[str, float]:
    total = sum(amounts.values())
    return {k: v / total for k, v in amounts.items()} if total > 0 else {}


def _same_fractions(parsed: dict, declared: dict) -> bool:
    left, right = _fractions(parsed), _fractions(declared)
    if set(left) != set(right):
        return False
    return all(abs(left[k] - right[k]) <= _FORMULA_TOL for k in left)


def _format_formula(amounts: dict) -> str:
    from pymatgen.core import Composition

    try:
        return Composition({k: v for k, v in amounts.items() if v > 0}).reduced_formula
    except Exception:
        return ""


def _is_twin(model: dict, i: int, j: int) -> bool:
    """True when row ``j`` is row ``i`` seen through a point operation of the group.

    That is the signature of a table that lists the second (rotated) copy of an
    equivalent set in a different origin setting: the positions are not related
    by the listed operations (rotation *and* translation) but by their rotation
    part alone.
    """
    import numpy as np

    rows = model["rows"]
    lattice = model["lattice"]
    a, b = rows[i], rows[j]
    if a["element"] != b["element"] or len(a["images"]) != len(b["images"]):
        return False
    if abs(a["occ"] - b["occ"]) > 1e-6:
        return False
    for rotation in model["rotations"]:
        moved = (a["images"] @ rotation.T) % 1.0
        forward = lattice.get_all_distances(b["images"], moved).min(axis=1)
        backward = lattice.get_all_distances(moved, b["images"]).min(axis=1)
        if np.all(forward < 0.1) and np.all(backward < 0.1):
            return True
    return False


def _prune_to_formula(model: dict, active: list[int], declared) -> list[int] | None:
    """Rows to additionally drop so that the composition equals ``declared``.

    Covers files that list a whole equivalent set twice, the second copy in
    positions that are not related to the first by the listed operations (e.g.
    Co1/Co2 in COD 5910316). Only whole labels that are point-operation twins
    (:func:`_is_twin`) of a label that stays are dropped, later labels first,
    and only when an exact match of the formula is possible.
    """
    reduced = {str(el): float(amt) for el, amt in declared.reduced_composition.items()}
    rows = model["rows"]
    per_element: dict[str, list[int]] = {}
    for index in active:
        per_element.setdefault(rows[index]["element"], []).append(index)
    if set(per_element) != set(reduced):
        return None
    if any(len(items) > _MAX_PRUNE_LABELS for items in per_element.values()):
        return None
    weights = {i: len(rows[i]["images"]) * rows[i]["occ"] for i in active}
    current = _composition(model, active)
    k_max = int(min(current[e] / reduced[e] for e in reduced) + 1e-6)

    def pick(items: list[int], target: float) -> list[int] | None:
        def dfs(pos: int, remaining: float):
            if abs(remaining) <= 1e-3:
                return []
            if pos >= len(items) or remaining < 0:
                return None
            take = dfs(pos + 1, remaining - weights[items[pos]])
            if take is not None:
                return [items[pos], *take]
            return dfs(pos + 1, remaining)

        return dfs(0, target)

    for k in range(k_max, 0, -1):
        kept_rows: list[int] = []
        for element, items in per_element.items():
            part = pick(items, k * reduced[element])
            if part is None:
                break
            kept_rows.extend(part)
        else:
            dropped = sorted(set(active) - set(kept_rows))
            if all(
                any(_is_twin(model, i, j) for i in kept_rows) for j in dropped
            ):
                return dropped
    return None


def plan_cif_repair(path: Path) -> dict | None:
    """Analyse one CIF for duplicate atoms / formula mismatch (nothing is written).

    Returns ``None`` when the file cannot be analysed. Otherwise a dict with:

    ``needed``            duplicate atoms were found (the CIF is a repair candidate)
    ``ok``                the repair reproduces ``_chemical_formula_sum`` cleanly
    ``duplicates``        ``[{"label", "duplicate_of", "full"}]``
    ``removed_labels``    atom-loop labels dropped by the repair
    ``declared_formula`` / ``parsed_formula`` / ``repaired_formula``  reduced formulas
    ``formula_mismatch``  parsed composition differs from ``_chemical_formula_sum``
    ``reason``            human-readable summary
    """
    try:
        model = _load_model(Path(path))
    except Exception as exc:
        logger.debug("CIF consistency analysis of %s failed: %s", path, exc)
        return None
    if model is None:
        return None

    rows = model["rows"]
    all_rows = list(range(len(rows)))
    declared = _declared_composition(model)
    declared_amounts = (
        {str(el): float(amt) for el, amt in declared.items()} if declared else None
    )
    parsed = _composition(model, all_rows)
    plan: dict = {
        "needed": False,
        "ok": True,
        "duplicates": [],
        "removed_labels": [],
        "declared_formula": _format_formula(declared_amounts) if declared_amounts else None,
        "parsed_formula": _format_formula(parsed),
        "repaired_formula": None,
        "formula_mismatch": bool(
            declared_amounts and not _same_fractions(parsed, declared_amounts)
        ),
        "reason": "",
        "_model": model,
        "_removed": [],
    }

    redundant, partial = _find_overlaps(model, all_rows)
    duplicates = [
        {"label": rows[j]["label"], "duplicate_of": rows[i]["label"], "full": True}
        for j, i in redundant.items()
    ] + [
        {"label": rows[j]["label"], "duplicate_of": rows[i]["label"], "full": False}
        for j, i in partial.items()
    ]
    if not duplicates:
        return plan

    plan["needed"] = True
    plan["duplicates"] = duplicates
    active = list(all_rows)
    removed: set[int] = set()
    for _ in range(4):  # chains of duplicates settle quickly
        redundant, _partial = _find_overlaps(model, active)
        if not redundant:
            break
        removed.update(redundant)
        active = [i for i in active if i not in removed]

    composition = _composition(model, active)
    matches = declared_amounts is None or _same_fractions(composition, declared_amounts)
    if not matches and declared is not None:
        extra = _prune_to_formula(model, active, declared)
        if extra:
            removed.update(extra)
            active = [i for i in active if i not in removed]
            composition = _composition(model, active)
            matches = _same_fractions(composition, declared_amounts)
    _redundant_left, partial = _find_overlaps(model, active)

    plan["removed_labels"] = [rows[i]["label"] for i in sorted(removed)]
    plan["repaired_formula"] = _format_formula(composition)
    plan["_removed"] = sorted(removed)
    plan["ok"] = bool(matches and not partial and not _redundant_left and removed)
    labels = ", ".join(d["label"] for d in duplicates)
    if plan["ok"]:
        plan["reason"] = (
            "Symmetry-equivalent atoms were listed explicitly in the atom loop "
            f"({labels}); removed {', '.join(plan['removed_labels'])} so the composition "
            f"becomes {plan['repaired_formula']}"
            + (
                f" (matches _chemical_formula_sum {plan['declared_formula']})."
                if declared_amounts
                else "."
            )
        )
    elif not matches:
        plan["reason"] = (
            f"Duplicate atoms were found ({labels}) but removing them gives "
            f"{plan['repaired_formula']}, not the declared {plan['declared_formula']}."
        )
    else:
        plan["reason"] = (
            f"Atoms of different atom-loop entries overlap ({labels}) and cannot be "
            "removed cleanly."
        )
    return plan


def consistency_issues(path: Path, *, plan: dict | None = None) -> list[dict]:
    """Warnings for duplicate atoms or a formula that disagrees with the atoms."""
    if plan is None:
        plan = plan_cif_repair(path)
    if not plan:
        return []
    if plan["needed"]:
        labels = ", ".join(d["label"] for d in plan["duplicates"][:8])
        text = (
            "Atom-loop entries overlap after applying the symmetry operations "
            f"({labels}); the composition parsed from the atoms is {plan['parsed_formula']}"
        )
        if plan["declared_formula"]:
            text += f" while _chemical_formula_sum is {plan['declared_formula']}"
        issues = [_issue("duplicate_atoms", "warning", text + ".")]
        if plan["declared_formula"] and not plan["ok"] and plan["formula_mismatch"]:
            issues.append(_issue("inconsistent_formula", "warning", plan["reason"]))
        return issues
    if plan["formula_mismatch"]:
        return [
            _issue(
                "inconsistent_formula",
                "warning",
                f"The composition parsed from the atoms ({plan['parsed_formula']}) differs "
                f"from _chemical_formula_sum ({plan['declared_formula']}).",
            )
        ]
    return []


def _repaired_stem(path: Path, text: str) -> str:
    """File stem for the repaired CIF: regenerate the formula and space-group part."""
    import warnings

    from pymatgen.io.cif import CifParser

    from dara.cif import get_formula_with_disorder

    stem = Path(path).stem
    parts = stem.split("_")
    if len(parts) < 3 or not parts[1].isdigit():
        return stem
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = CifParser.from_str(text).parse_structures(primitive=False)[0]
        formula = get_formula_with_disorder(structure)
        try:
            spacegroup = structure.get_space_group_info()[1]
        except Exception:
            spacegroup = parts[1]
    except Exception:
        return stem
    return "_".join([formula, str(spacegroup), *parts[2:]])


def repair_cif(path: Path, plan: dict | None = None) -> dict | None:
    """Apply a successful repair plan: write the repaired CIF next to ``path``.

    The original file is replaced (removed when the name changed). Returns the
    ``repaired`` record or ``None`` when nothing was repaired.
    """
    path = Path(path)
    if plan is None:
        plan = plan_cif_repair(path)
    if not plan or not plan.get("needed") or not plan.get("ok"):
        return None

    model = plan["_model"]
    block = model["block"]
    removed = set(plan["_removed"])
    keep = [i not in removed for i in range(len(model["rows"]))]
    removed_labels = set(plan["removed_labels"])

    for loop in block.loops:
        if "_atom_site_fract_x" in loop:
            for key in loop:
                values = block.data.get(key)
                if isinstance(values, list) and len(values) == len(keep):
                    block.data[key] = [v for v, flag in zip(values, keep) if flag]
        elif "_atom_site_aniso_label" in loop:
            labels = block.data.get("_atom_site_aniso_label")
            if isinstance(labels, list):
                mask = [str(label) not in removed_labels for label in labels]
                for key in loop:
                    values = block.data.get(key)
                    if isinstance(values, list) and len(values) == len(mask):
                        block.data[key] = [v for v, flag in zip(values, mask) if flag]

    text = str(block) + "\n"
    new_stem = _repaired_stem(path, text)
    new_path = path.with_name(new_stem + path.suffix)
    new_path.write_text(text)
    if new_path != path:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Could not remove %s after repair: %s", path, exc)
    logger.info("Repaired CIF %s -> %s: %s", path.name, new_path.name, plan["reason"])
    return {
        "file_before": path.name,
        "file_after": new_path.name,
        "reason": plan["reason"],
        "removed_labels": list(plan["removed_labels"]),
        "formula_before": plan["parsed_formula"],
        "formula_after": plan["repaired_formula"],
        "source": reference_source(path.stem),
    }


def _record(path: Path, issues: list[dict]) -> dict:
    return {"file": path.name, "source": reference_source(path.stem), "reasons": issues}


def _promote_to_reject(issues: list[dict], plan: dict) -> list[dict]:
    """Turn the consistency warnings of an unrepairable CIF into a rejection."""
    code = (
        "inconsistent_formula"
        if plan.get("declared_formula") and plan.get("formula_mismatch")
        else "duplicate_atoms"
    )
    out = [item for item in issues if item["code"] not in {"duplicate_atoms", "inconsistent_formula"}]
    out.append(_issue(code, "reject", plan.get("reason") or "The atom list is inconsistent."))
    return out


def screen_candidate_cifs(
    paths: list[Path],
    *,
    allowed_elements: Iterable[str] | None = None,
    protected_stems: Iterable[str] = (),
    repaired: list[dict] | None = None,
) -> tuple[list[Path], list[dict], list[dict]]:
    """Repair or drop clearly unusable candidate CIFs.

    Returns ``(kept, rejected, warnings)``. User-supplied CIFs (``protected_stems``
    and file names without a database marker) are never rejected or rewritten,
    and if every candidate would be rejected all are kept (with their issues
    reported as warnings) so the analysis can still run.

    Database CIFs (COD/ICSD/MP) that list symmetry-equivalent atoms explicitly
    are repaired in place (see :func:`plan_cif_repair`); each repair is appended
    to ``repaired`` (pass a list; store it as ``candidate_summary["repaired"]``)
    and also reported as a ``cif_repaired`` entry in ``warnings``. Repaired
    files keep the database id but get a regenerated formula/space-group name,
    so the returned paths may differ from the input ones.
    """
    paths = list(paths)
    if not cif_validation_enabled():
        return paths, [], []

    protected = set(protected_stems or ())
    allowed = list(allowed_elements) if allowed_elements is not None else None
    kept: list[Path] = []
    rejected: list[dict] = []
    warnings: list[dict] = []
    rejected_paths: list[tuple[Path, list[dict]]] = []
    original_order: dict[Path, int] = {}

    for position, path in enumerate(paths):
        path = Path(path)
        plan = None
        try:
            plan = plan_cif_repair(path)
        except Exception as exc:
            logger.warning("Could not analyse CIF %s: %s", path, exc)
        eligible = bool(
            plan
            and plan["needed"]
            and path.stem not in protected
            and reference_source(path.stem) in _REPAIRABLE_SOURCES
        )
        repair_note: dict | None = None
        if eligible and plan["ok"]:
            try:
                record = repair_cif(path, plan)
            except Exception as exc:
                logger.warning("Could not repair CIF %s: %s", path, exc)
                record = None
            if record is not None:
                if repaired is not None:
                    repaired.append(record)
                repair_note = record
                path = path.with_name(record["file_after"])
                plan = None  # re-analysed (and expected clean) below
        original_order[path] = position

        try:
            issues = validate_cif(path, allowed_elements=allowed)
        except Exception as exc:
            logger.warning("Could not validate CIF %s: %s", path, exc)
            issues = []
        if eligible and plan is not None and not plan["ok"]:
            issues = _promote_to_reject(issues, plan)
        if repair_note is not None:
            issues = [
                _issue(
                    "cif_repaired",
                    "warning",
                    f"{repair_note['file_before']} was repaired to {repair_note['file_after']}: "
                    f"{repair_note['reason']}",
                ),
                *issues,
            ]
        rejects = [item for item in issues if item["severity"] == "reject"]
        if rejects and path.stem in protected:
            issues = [
                {**item, "severity": "warning"} if item["severity"] == "reject" else item
                for item in issues
            ]
            rejects = []
        if rejects:
            rejected_paths.append((path, issues))
            continue
        kept.append(path)
        if issues:
            warnings.append(_record(path, issues))

    if not kept and rejected_paths:
        # Never leave the search without candidates because of the screening.
        for path, issues in rejected_paths:
            demoted = [
                {**item, "severity": "warning"} if item["severity"] == "reject" else item
                for item in issues
            ]
            warnings.append(_record(path, demoted))
        return sorted(original_order, key=original_order.get), [], warnings[:_MAX_WARNINGS]

    kept_set = set(kept)
    # Preserve the original order of the surviving paths.
    kept = sorted(kept_set, key=lambda p: original_order[p])
    for path, issues in rejected_paths:
        rejected.append(_record(path, issues))
    return kept, rejected, warnings[:_MAX_WARNINGS]


def computed_only_phases(paths: list[Path]) -> list[dict]:
    """Phases (formula, space group) whose only candidates are computed MP structures."""
    groups: dict[tuple[str, int], list[Path]] = {}
    for path in paths:
        stem = Path(path).stem
        parts = stem.split("_")
        if len(parts) < 3:
            continue
        try:
            spacegroup = int(parts[1])
        except ValueError:
            continue
        groups.setdefault((parts[0], spacegroup), []).append(Path(path))

    out = []
    for (formula, spacegroup), members in sorted(groups.items()):
        if members and all(reference_source(member.stem) == "mp" for member in members):
            out.append(
                {
                    "formula": formula,
                    "spacegroup": spacegroup,
                    "file": sorted(member.stem for member in members)[0],
                }
            )
    return out
