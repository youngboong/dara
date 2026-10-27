"""ZIP bundles for importing a DARA refinement into other programs."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import numpy as np

logger = logging.getLogger("dara.export")

FORMATS = ("profex", "topas", "gsas2", "highscore")

_PROGRAM_NAMES = {
    "profex": "Profex / BGMN",
    "topas": "TOPAS",
    "gsas2": "GSAS-II",
    "highscore": "HighScore",
}


def safe_name(value: str) -> str:
    """Filesystem-safe name for a phase."""
    return re.sub(r"[^A-Za-z0-9._()+-]+", "_", str(value)).strip("._") or "phase"


def write_pattern_xy(document, path: Path) -> Path:
    """Two-column (2θ, intensity) pattern with six decimals."""
    xrd = document.xrd_data
    angles = np.asarray(xrd.angles, dtype=float)
    intensities = np.asarray(xrd.intensities, dtype=float)
    np.savetxt(path, np.column_stack([angles, intensities]), fmt="%.6f")
    return path


def write_refined_cifs(refinement, out_dir: Path, reference_cifs=None) -> list[Path]:
    """Refined CIF per phase; falls back to the reference CIF text when the export fails."""
    folder = Path(out_dir) / "refined_cifs"
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in refinement.lst_data.phases_results:
        try:
            text = refinement.export_structure(name).to(fmt="cif")
        except (TypeError, ValueError, AttributeError):
            reference = next(
                (
                    cif
                    for cif in reference_cifs or []
                    if Path(getattr(cif, "filename", "") or "").stem == name
                ),
                None,
            )
            if reference is None or not getattr(reference, "orig_string", None):
                continue
            text = reference.orig_string
        target = folder / f"{safe_name(name)}.cif"
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def _reference_lines(*keys: str) -> str:
    from dara.server.report.references import reference_text

    return "\n".join(f"  {reference_text(key)}" for key in keys)


def _fit_summary(refinement) -> str:
    try:
        fit = refinement.lst_data
        return f"Rwp {float(fit.rwp):.2f}%, Rexp {float(fit.rexp):.2f}%"
    except Exception:
        return "fit statistics not available"


def readme_text(fmt: str, refinement, document) -> str:
    """English README of one bundle."""
    program = _PROGRAM_NAMES[fmt]
    lines = [
        f"DARA export for {program}",
        "=" * 40,
        "",
        "This archive contains the DARA refinement model as starting values for another",
        "Rietveld program. DARA itself used BGMN (instrument profile "
        f"\"{(getattr(document, 'search_kwargs', None) or {}).get('instrument_profile', 'not recorded')}\").",
        f"DARA fit of the exported model: {_fit_summary(refinement)}.",
        "",
        "Common files",
        "  pattern.xy           Measured pattern, two columns (2theta in degrees, intensity).",
        "  refined_cifs/*.cif   Crystal structure of each refined phase at the refined lattice",
        "                       parameters and atomic positions.",
        "  dara_refinement.lst  Raw BGMN listing of the DARA refinement.",
        "",
    ]
    if fmt == "profex":
        try:
            rwp = f"{float(refinement.lst_data.rwp):.2f}"
        except Exception:
            rwp = "the DARA value"
        lines += [
            "Profex / BGMN files",
            "  *.str     BGMN phase files; the lattice parameters start at the refined values",
            "            with the same relative bounds as in the DARA final refinement.",
            "  *.sav     BGMN control file (instrument profile, phases, EPS2 start value).",
            "  *.geq, *.ger  Instrument profile files copied from DARA.",
            "",
            "Open the .sav control file in Profex (Doebelin & Kleeberg, 2015) or run",
            "`bgmn pattern.sav`. The starting model uses the refined lattice parameters and",
            "scale factors of the DARA final model, but B1 and k1 start at 0 and the SPHAR4",
            "preferred-orientation coefficients are not transferred. The Rwp of the first",
            f"cycle will therefore differ from the DARA value ({rwp}%) until they are refined.",
            "",
            "Reference",
            _reference_lines("profex", "bgmn"),
        ]
    elif fmt == "topas":
        lines += [
            "TOPAS file",
            "  dara_model.inp  Starting model in TOPAS input syntax.",
            "",
            "The file is a starting point and must be reviewed before use: the space-group",
            "setting, the origin choice, the peak-shape model and the sign convention of the",
            "specimen displacement were not converted automatically from BGMN. Scale factors",
            "start at a nominal value and are refined by TOPAS. Instrument parameters of",
            "your diffractometer must be added.",
        ]
    else:
        lines += [
            f"Import into {program}",
            "  1. Import pattern.xy as a two-column 2theta/intensity powder histogram.",
            "  2. Add each file in refined_cifs/ as a phase.",
            "  3. Select the instrument parameters of your diffractometer; DARA's BGMN",
            "     instrument profile is not converted.",
            "",
            "No project file is written; scale factors, profile and background parameters",
            f"start from the defaults of {program}.",
        ]
    return "\n".join(lines) + "\n"


def write_common_files(refinement, document, out_dir: Path, reference_cifs=None) -> list[Path]:
    """``pattern.xy``, ``refined_cifs/``, ``dara_refinement.lst``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    created = [write_pattern_xy(document, out_dir / "pattern.xy")]
    created += write_refined_cifs(refinement, out_dir, reference_cifs)
    raw = getattr(refinement.lst_data, "raw_lst", None)
    if raw:
        listing = out_dir / "dara_refinement.lst"
        listing.write_text(raw, encoding="utf-8")
        created.append(listing)
    return created


def write_cif_bundle(
    refinement, document, out_dir: Path, *, fmt: str, reference_cifs=None
) -> list[Path]:
    """Generic bundle (GSAS-II, HighScore): pattern, refined CIFs, listing and README."""
    created = write_common_files(refinement, document, out_dir, reference_cifs)
    readme = Path(out_dir) / "README.txt"
    readme.write_text(readme_text(fmt, refinement, document), encoding="utf-8")
    return created + [readme]


def build_bundle(fmt: str, refinement, document, out_dir: Path, reference_cifs=None) -> list[Path]:
    """Write the bundle of ``fmt`` into ``out_dir`` and return all created files."""
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported export format: {fmt}")
    out_dir = Path(out_dir)
    if fmt in {"gsas2", "highscore"}:
        return write_cif_bundle(refinement, document, out_dir, fmt=fmt, reference_cifs=reference_cifs)
    created = write_common_files(refinement, document, out_dir, reference_cifs)
    if fmt == "profex":
        from dara.export.bgmn_bundle import write_bgmn_bundle

        created += write_bgmn_bundle(refinement, document, out_dir, reference_cifs)
    else:
        from dara.export.topas_inp import topas_input

        model = out_dir / "dara_model.inp"
        model.write_text(topas_input(refinement, document), encoding="utf-8")
        created.append(model)
    readme = out_dir / "README.txt"
    readme.write_text(readme_text(fmt, refinement, document), encoding="utf-8")
    return created + [readme]


def zip_directory(directory: Path) -> bytes:
    """Deflate every file below ``directory`` into an in-memory ZIP."""
    directory = Path(directory)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()
