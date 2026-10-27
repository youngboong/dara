"""Profex/BGMN bundle: ``.str`` phase files and a ``.sav`` control file that start at the DARA model."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger("dara.export")

CIF2STR_KEYS = ("lattice_range", "gewicht", "rp", "k1", "k2", "b1", "lebail")


def _safe_stem(name: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._()+-]+", "_", str(name)).strip("._") or "phase"


def refined_eps2(refinement) -> float | None:
    """Refined EPS2 of ``refinement`` (value only) or ``None``."""
    value = getattr(getattr(refinement, "lst_data", None), "EPS2", None)
    if value is None:
        extra = getattr(getattr(refinement, "lst_data", None), "model_extra", None) or {}
        value = extra.get("EPS2")
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def write_bgmn_bundle(refinement, document, out_dir: Path, reference_cifs=None) -> list[Path]:
    """Write BGMN starting files into ``out_dir``; return the created paths.

    ``out_dir`` must already contain ``pattern.xy``. Each refined phase is converted to a
    ``.str`` file with :func:`dara.cif2str.cif2str` using the final-refinement parameters, so
    the lattice parameters start at the refined values with the same relative bounds.
    """
    from dara.cif2str import cif2str
    from dara.generate_control_file import generate_control_file
    from dara.refinement_controls import DEFAULT_PHASE_PARAMS

    out_dir = Path(out_dir)
    pattern_path = out_dir / "pattern.xy"
    saved = getattr(document, "final_refinement_params", None)
    params = {
        key: value
        for key, value in {**DEFAULT_PHASE_PARAMS, **(saved or {})}.items()
        if key in CIF2STR_KEYS
    }
    search = getattr(document, "search_kwargs", None) or {}
    refine = search.get("refinement_params") or {}

    created: list[Path] = []
    str_paths: list[Path] = []
    failed: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        for name in refinement.lst_data.phases_results:
            cif_path = temp_dir / f"{name}.cif"
            try:
                cif_path.write_text(
                    refinement.export_structure(name).to(fmt="cif"), encoding="utf-8"
                )
            except (TypeError, ValueError, AttributeError):
                reference = next(
                    (c for c in reference_cifs or []
                     if Path(getattr(c, "filename", "") or "").stem == name),
                    None,
                )
                if reference is None or not getattr(reference, "orig_string", None):
                    failed.append(name)
                    continue
                cif_path.write_text(reference.orig_string, encoding="utf-8")
            try:
                str_paths.append(cif2str(cif_path, "", out_dir, **params))
            except Exception:
                logger.warning("Could not convert %s to a BGMN .str file", name, exc_info=True)
                failed.append(name)
    if not str_paths:
        raise ValueError("No refined phase could be converted to a BGMN .str file")

    eps2 = refined_eps2(refinement)
    control = generate_control_file(
        pattern_path=pattern_path,
        str_paths=str_paths,
        instrument_profile=search.get("instrument_profile", "Aeris-fds-Pixcel1d-Medipix3"),
        working_dir=out_dir,
        wmin=refine.get("wmin"),
        wmax=refine.get("wmax"),
        excluded_ranges=refine.get("excluded_ranges"),
        eps2=f"{eps2:.6f}_-0.05^0.05" if eps2 is not None else "0_-0.05^0.05",
        wavelength=search.get("wavelength", "Cu"),
    )
    created += str_paths + [control]
    for suffix in (".geq", ".ger"):
        created += [path for path in out_dir.glob(f"*{suffix}")]
    if failed:
        note = out_dir / "phases_not_converted.txt"
        lines = ["These refined phases could not be converted to BGMN .str files and are not "
                 "part of the control file:", *failed, ""]
        note.write_text(chr(10).join(lines), encoding="utf-8")
        created.append(note)
    return created
