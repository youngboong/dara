"""Seed the DARA COD download cache from CIFs that are already on disk.

DARA caches COD structures downloaded from www.crystallography.net as ``<cod_id>.cif``
(see ``dara.structure_db.cod_cache_dir``; override with ``DARA_COD_CACHE_DIR``). This
script walks one or more folder trees and copies every COD CIF it finds into that cache
so that later analyses do not need to download them again. Recognised files:

* raw COD files named ``<id>.cif`` (digits only, e.g. ``dara_downloaded_cifs/1011163.cif``);
* DARA-renamed files containing ``(cod_<id>)``, e.g. ``Fe1O1_225_(cod_2311020)-12.cif``.

Only valid CIFs (non-empty, ``data_`` header and cell) are copied; existing valid cache
entries are never overwritten and files are written atomically.

Usage:
    python scripts/seed_cod_cache.py SOURCE [SOURCE ...] [--cache-dir DIR] [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_RENAMED = re.compile(r"\(cod_(\d+)\)", re.I)


def cod_id_from_name(path: Path) -> str | None:
    """COD id for a raw ``<id>.cif`` or a renamed ``..._(cod_<id>)-....cif`` file, else None."""
    if path.suffix.lower() != ".cif":
        return None
    if path.stem.isdigit():
        return path.stem
    match = _RENAMED.search(path.stem)
    return match.group(1) if match else None


def seed_cache(sources, cache_dir, *, dry_run: bool = False) -> dict[str, int]:
    """Copy COD CIFs found under ``sources`` into ``cache_dir`` as ``<id>.cif``.

    Returns counts: ``seeded`` (new files), ``already_cached``, ``invalid`` (skipped
    because the file is not a valid CIF), ``scanned`` (COD-like files seen).
    """
    from dara.structure_db import atomic_write_text, is_valid_cif_file

    cache_dir = Path(cache_dir)
    candidates: list[tuple[str, Path]] = []
    for source in sources:
        for path in Path(source).rglob("*.cif"):
            cod_id = cod_id_from_name(path)
            if cod_id is not None:
                candidates.append((cod_id, path))
    # Raw "<id>.cif" files first: they are the originals the renamed copies derive from.
    candidates.sort(key=lambda item: (not item[1].stem.isdigit(), item[0], str(item[1])))

    counts = {"scanned": len(candidates), "seeded": 0, "already_cached": 0, "invalid": 0}
    done: set[str] = set()
    for cod_id, path in candidates:
        if cod_id in done:
            continue
        destination = cache_dir / f"{cod_id}.cif"
        if is_valid_cif_file(destination):
            counts["already_cached"] += 1
            done.add(cod_id)
            continue
        if not is_valid_cif_file(path):
            counts["invalid"] += 1
            continue
        done.add(cod_id)
        if not dry_run:
            atomic_write_text(destination, path.read_text(encoding="utf-8", errors="replace"))
        counts["seeded"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("sources", nargs="+", type=Path, help="folder trees to scan for COD CIFs")
    parser.add_argument("--cache-dir", type=Path, help="cache folder (default: DARA's COD cache)")
    parser.add_argument("--dry-run", action="store_true", help="count only; copy nothing")
    args = parser.parse_args(argv)

    from dara.structure_db import cod_cache_dir

    cache_dir = args.cache_dir or cod_cache_dir()
    counts = seed_cache(args.sources, cache_dir, dry_run=args.dry_run)
    print(f"COD cache: {cache_dir}")
    print(
        f"scanned {counts['scanned']} COD CIF file(s): seeded {counts['seeded']}, "
        f"already cached {counts['already_cached']}, invalid {counts['invalid']}"
        + (" (dry run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
