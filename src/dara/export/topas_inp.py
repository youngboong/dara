"""TOPAS input (.inp) starting model written from a DARA refinement."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("dara.export")

LAB_ANODES = {"Cu", "Co", "Cr", "Fe", "Mo"}


def _first_value(value):
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _wavelength(search: dict) -> tuple[str | None, float | None]:
    """Anode name (lab tubes) and wavelength in Å."""
    from dara.server.api_router import LAB_WAVELENGTHS_ANGSTROM

    value = search.get("wavelength", "Cu")
    try:
        return None, float(value) * 10  # synchrotron, nm in DARA
    except (TypeError, ValueError):
        pass
    name = str(value).strip().capitalize()
    return (name if name in LAB_ANODES else None), LAB_WAVELENGTHS_ANGSTROM.get(name)


def _range(document, refinement) -> tuple[float | None, float | None]:
    refine = (getattr(document, "search_kwargs", None) or {}).get("refinement_params") or {}
    lower, upper = refine.get("wmin"), refine.get("wmax")
    if lower is None or upper is None:
        try:
            import numpy as np

            x = np.asarray(refinement.plot_data.x, dtype=float)
            x = x[np.isfinite(x)]
            lower = x.min() if lower is None else lower
            upper = x.max() if upper is None else upper
        except Exception:
            pass
    return (None if lower is None else float(lower)), (None if upper is None else float(upper))


def crystal_system_of(number: int) -> str:
    """Crystal system from the space-group number."""
    bounds = (
        (2, "triclinic"), (15, "monoclinic"), (74, "orthorhombic"), (142, "tetragonal"),
        (167, "trigonal"), (194, "hexagonal"), (230, "cubic"),
    )
    for upper, name in bounds:
        if number <= upper:
            return name
    raise ValueError(f"Invalid space-group number: {number}")


def _close(x: float, y: float, tol: float = 1e-3) -> bool:
    return abs(x - y) <= tol * max(1.0, abs(y))


def _lattice_lines(crystal_system: str, structure) -> list[str]:
    """Explicit TOPAS lattice keywords; symmetry-implied parameters are tied or fixed.

    No ``topas.inc`` macro is used: only keywords that every TOPAS version understands
    (``a @ v``, ``b = Get(a);``, ``al 90``). A parameter is tied or fixed only when the
    structure's metric really satisfies the constraint, otherwise it stays free.
    """
    a, b, c = (float(v) for v in structure.lattice.abc)
    angles = [float(v) for v in structure.lattice.angles]
    tied = crystal_system in {"cubic", "tetragonal", "hexagonal", "trigonal"}
    rhombohedral = (
        crystal_system == "trigonal"
        and _close(a, b) and _close(a, c) and _close(angles[0], angles[1])
        and _close(angles[0], angles[2])
        and not (_close(angles[0], 90.0) and _close(angles[2], 120.0))
    )
    lines = [f"a @ {a:.5f}"]
    lines.append("b = Get(a);" if tied and _close(a, b) else f"b @ {b:.5f}")
    if (crystal_system == "cubic" and _close(a, c)) or rhombohedral:
        lines.append("c = Get(a);")
    else:
        lines.append(f"c @ {c:.5f}")
    if rhombohedral:
        lines += [f"al @ {angles[0]:.5f}", "be = Get(al);", "ga = Get(al);"]
        return lines
    if crystal_system in {"hexagonal", "trigonal"}:
        fixed = [90.0, 90.0, 120.0]
    elif crystal_system in {"cubic", "tetragonal", "orthorhombic"}:
        fixed = [90.0, 90.0, 90.0]
    elif crystal_system == "monoclinic":
        fixed = [90.0, None, 90.0]
    else:
        fixed = [None, None, None]
    for key, value, wanted in zip(("al", "be", "ga"), angles, fixed):
        if wanted is not None and _close(value, wanted, 1e-4):
            lines.append(f"{key} {wanted:g}")
        else:
            lines.append(f"{key} @ {value:.5f}")
    return lines


def _asymmetric_sites(structure, number: int) -> list:
    """Representative site of every orbit under the *declared* space group.

    ``refinement.export_structure`` expands the asymmetric unit with the refined space group,
    so grouping by that group's own operations returns exactly the atoms TOPAS will expand
    again from the ``space_group`` line. (A symmetry finder may report a higher symmetry and
    merge orbits, which would drop atoms.) Raises ``ValueError`` when the result does not
    reproduce the full structure.
    """
    import numpy as np

    from dara.plot import cached_spacegroup

    operations = cached_spacegroup(number).symmetry_ops
    coords = np.array([site.frac_coords for site in structure], dtype=float)
    assigned = np.zeros(len(structure), dtype=bool)
    representatives = []
    for index, site in enumerate(structure):
        if assigned[index]:
            continue
        images = np.array([op.operate(site.frac_coords) for op in operations], dtype=float)
        for other, other_site in enumerate(structure):
            if assigned[other] or not site.species.almost_equals(other_site.species):
                continue
            delta = images - coords[other]
            delta -= np.round(delta)
            if (np.abs(delta).max(axis=1) < 5e-4).any():
                assigned[other] = True
        assigned[index] = True
        representatives.append(site)
    from pymatgen.core import Structure

    rebuilt = Structure.from_spacegroup(
        sg=number,
        lattice=structure.lattice,
        species=[site.species for site in representatives],
        coords=[site.frac_coords for site in representatives],
        tol=2e-4,
    )
    if len(rebuilt) != len(structure):
        raise ValueError(
            f"asymmetric unit expands to {len(rebuilt)} atoms, expected {len(structure)}"
        )
    return representatives


def _site_lines(sites) -> list[str]:
    counters: dict[str, int] = {}
    lines = []
    for site in sites:
        occupancy = []
        for species, amount in site.species.items():
            symbol = getattr(species, "symbol", None) or str(species)
            occupancy.append((symbol, float(amount)))
        if not occupancy:
            continue
        label_element = occupancy[0][0]
        counters[label_element] = counters.get(label_element, 0) + 1
        x, y, z = (float(v) for v in site.frac_coords)
        occ = " ".join(f"occ {symbol} {amount:.4f}" for symbol, amount in occupancy)
        lines.append(
            f"site {label_element}{counters[label_element]} x {x:.5f} y {y:.5f} z {z:.5f} "
            f"{occ} beq 0.5"
        )
    return lines


def _crystallite_size_nm(phase_result) -> float | None:
    for key, raw in (getattr(phase_result, "model_extra", None) or {}).items():
        if str(key).lower().startswith("grainsize"):
            value = _first_value(raw)
            if value:
                return value
    return None


def topas_input(refinement, document) -> str:
    """Return the TOPAS input text (starting model; review before use)."""
    from dara.server.api_router import _spacegroup_symbol
    from dara.server.confidence import instrument_radius_mm, sample_displacement

    search = getattr(document, "search_kwargs", None) or {}
    refine = search.get("refinement_params") or {}
    anode, wavelength = _wavelength(search)
    lower, upper = _range(document, refinement)
    lines = [
        "' DARA starting model for TOPAS - review before use",
        "' Scale factors, peak shapes and the background are starting values only.",
        'xdd "pattern.xy"',
        "    bkg @ 0 0 0 0 0 0",
    ]
    if lower is not None:
        lines.append(f"    start_X {lower:g}")
    if upper is not None:
        lines.append(f"    finish_X {upper:g}")
    for interval in refine.get("excluded_ranges") or []:
        try:
            lines.append(f"    exclude {float(interval[0]):g} {float(interval[1]):g}")
        except (TypeError, ValueError, IndexError):
            continue
    if anode == "Cu":
        lines.append("    CuKa5(0.0001)")
    elif wavelength is not None:
        lines.append(f"    lam ymin_on_ymax 0.0001 la 1 lo {wavelength:.6f} lh 0.1")
    lines.append("    Zero_Error(@, 0)")
    profile = search.get("instrument_profile")
    radius = instrument_radius_mm(profile)
    displacement = sample_displacement(refinement.lst_data, profile)
    if radius is not None and displacement.get("displacement_mm") is not None:
        lines.append(f"    Rp {radius:g}")
        lines.append(f"    Rs {radius:g}")
        lines.append(f"    Specimen_Displacement(@, {displacement['displacement_mm']:.4f})")
    elif displacement.get("eps2") is not None:
        lines.append(
            "    ' Specimen displacement not transferred: the goniometer radius of the "
            "instrument profile is not known."
        )
    lines.append("    LP_Factor(0)")

    for name, phase in refinement.lst_data.phases_results.items():
        try:
            structure = refinement.export_structure(name)
            number = int(getattr(phase, "spacegroup_no", None) or 0)
            symbol = re.sub(r"_", "", _spacegroup_symbol(number))
            crystal_system = crystal_system_of(number)
        except Exception:
            logger.warning("TOPAS export skipped phase %s", name, exc_info=True)
            lines.append(f"    ' phase {name} could not be converted")
            continue
        try:
            sites = _site_lines(_asymmetric_sites(structure, number))
            note = None
        except Exception:
            logger.warning("TOPAS export of %s falls back to P1", name, exc_info=True)
            sites = _site_lines(list(structure))
            symbol, crystal_system = "P1", "triclinic"
            note = "' space group symmetry could not be reproduced; all atoms are listed in P1"
        size = _crystallite_size_nm(phase) or 100.0
        lines += ["    str", f'        phase_name "{name}"', f'        space_group "{symbol}"']
        if note:
            lines.append(f"        {note}")
        lines += [f"        {line}" for line in _lattice_lines(crystal_system, structure)]
        lines += [
            "        scale @ 0.0001",
            f"        CS_L(@, {size:.1f})",
        ]
        lines += [f"        {site}" for site in sites]
    return "\n".join(lines) + "\n"
