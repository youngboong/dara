"""Regenerate the benchmark ground-truth JSON files from the source spreadsheets.

Usage (repository root)::

    python scripts/convert_benchmark_xlsx.py --data-dir <folder with the .xlsx files>
    python scripts/convert_benchmark_xlsx.py --data-dir <folder> --check

The spreadsheets are read with the standard library only (zipfile + xml). The
phase identities of the pairwise set need human curation (the spreadsheet holds
free-text phase names); every non-trivial decision is recorded in ``CURATED``.
``--check`` regenerates the files in memory and exits 1 when the committed JSON
differs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "dataset" / "ground_truth"

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


# --------------------------------------------------------------------------- #
# Minimal xlsx reader (stdlib only)
# --------------------------------------------------------------------------- #
def _col_index(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + ord(char) - 64
    return number - 1


def read_xlsx(path: Path) -> dict[str, list[tuple[int, dict[int, str]]]]:
    """Return ``{sheet name: [(row number, {column index: text}), ...]}``."""
    archive = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in item.iter("{%s}t" % NS["m"])))
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in relations}
    sheets = {}
    for sheet in workbook.find("m:sheets", NS):
        target = targets[sheet.get("{%s}id" % NS["r"])].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        root = ET.fromstring(archive.read(target))
        rows = []
        for row in root.iter("{%s}row" % NS["m"]):
            cells: dict[int, str] = {}
            for cell in row.findall("m:c", NS):
                reference, kind = cell.get("r"), cell.get("t")
                value = cell.find("m:v", NS)
                column = _col_index(re.match(r"[A-Z]+", reference).group())
                if kind == "s" and value is not None:
                    text = shared[int(value.text)]
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter("{%s}t" % NS["m"]))
                elif value is not None:
                    text = value.text
                else:
                    continue
                cells[column] = text
            rows.append((int(row.get("r")), cells))
        sheets[sheet.get("name")] = rows
    return sheets


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elements(formulas: list[str]) -> list[str]:
    from pymatgen.core import Composition

    return sorted({str(element) for formula in formulas for element in Composition(formula).elements})


# --------------------------------------------------------------------------- #
# Curated phase identities of the pairwise set
# --------------------------------------------------------------------------- #
# sample id -> [(formula, space group, accept_any_of formulas, accepted space groups)] in the
# order of the phase rows of the spreadsheet block. Decisions:
#  * Spreadsheet phase names are free text ("Zinc Oxide"); formulas come from Source_file.
#  * Polymorph-ambiguous Nb oxides and the Sr-Fe-O perovskite family have spacegroup None
#    plus accept_any_of: the manual fit uses a representative model of a structural family.
#  * ZnCo2O4 also accepts Co3O4 (same spinel pattern); V2Cr0.11O5.16 is scored as V2O5.
#  * FeNbO4 accepts SG 13 or 60; WO3 14 or 2; BiVO4 88 or 15; GeO2 152 or 154.
CURATED = {
    "2Fe3O4-3Y2O3_1000C_60min": [("Y2O3", 206, [], []), ("Fe2O3", 167, [], []), ("YFeO3", 62, [], [])],
    "2FeC2O4(H2O)2-Y2O3_200C_60min": [("FeC2O4(H2O)2", 15, ["FeH4C2O6"], []), ("Y2O3", 206, [], [])],
    "Bi2O3-2MoO3_400C_60min": [("MoO3", 62, [], []), ("Bi2O3", 14, [], []), ("Bi2Mo3O12", 14, [], [])],
    "Bi2O3-Nb2O5_500C_60min": [("Bi2O3", 14, [], []), ("Nb2O5", None, ["Nb16.8O42", "Nb12O29"], [])],
    "Bi2O3-V2O5_400C_60min": [("Bi2O3", 14, [], []), ("BiVO4", 88, [], [88, 15]), ("V2O5", 59, [], [])],
    "CaCO3-NH4H2PO4_200C_60min": [("CaCO3", 167, [], [])],
    "CoO-WO3_900C_60min": [("CoWO4", 13, [], []), ("WO3", 14, [], [14, 2])],
    "CoO-ZnO_1100C_60min": [("ZnO", 186, [], []), ("ZnCo2O4", 227, ["Co3O4"], [])],
    "Cr2O3-2MnO_1100C_60min": [("MnCr2O4", 227, [], [])],
    "Cr2O3-2PbO_500C_60min": [("Pb2CrO5", 12, [], []), ("PbCrO4", 14, [], [])],
    "Cr2O3-2ZnO_1100C_60min": [("ZnCr2O4", 227, [], []), ("ZnO", 186, [], [])],
    "Cr2O3-V2O5_400C_60min": [("V2O5", 59, ["V2Cr0.11O5.16"], []), ("Cr2O3", 167, [], [])],
    "CuO-MnO_800C_60min": [("CuMn2O4", 227, [], []), ("CuO", 15, [], []), ("Mn2O3", 206, [], [])],
    "Fe2O3-2TiO2_1000C_60min": [("Fe2TiO5", 63, [], []), ("TiO2", 136, [], [])],
    "Fe2O3-Nb2O5_900C_60min": [
        ("Fe2O3", 167, [], []),
        ("FeNbO4", 13, ["Fe0.95Nb1.03O4"], [13, 60]),
        ("Nb2O5", None, ["Nb12O29"], []),
    ],
    "Fe3O4-3SrCO3_900C_60min": [
        ("SrCO3", 62, [], []),
        ("Sr8Fe8O23", None, ["SrFeO2.6", "SrFeO3", "SrFeO2.5", "Sr4Fe4O11", "SrFeO2.75"], []),
    ],
    "Fe3O4-3ZnO_1000C_60min": [("ZnO", 186, [], []), ("ZnFe2O4", 227, [], [])],
    "GeO2-ZnO_700C_60min": [("ZnO", 186, [], []), ("GeO2", 152, [], [152, 154]), ("Zn2GeO4", 148, [], [])],
    "NH4H2PO4-Na2CO3_200C_60min": [("Na2HPO4", 14, [], []), ("Na4P2O7", 19, [], [])],
    "V2O5-2ZnO_400C_60min": [("ZnO", 186, [], []), ("V2O5", 59, [], []), ("Zn2V2O7", 15, [], [])],
}

# Space group (and accepted settings) of each precursor in the mixture set.
# TiO2 accepts rutile (136) or anatase (141); NiO accepts cubic (225) or rhombohedral (166).
PRECURSOR_SG = {
    "In2O3": (206, []), "La(OH)3": (176, []), "TiO2": (None, [136, 141]), "MnCO3": (167, []),
    "Li2CO3": (15, []), "ZrO2": (14, []), "NiO": (225, [225, 166]), "Bi2O3": (14, []),
    "ZnO": (186, []), "V2O5": (59, []),
}

LATTICE_COLUMNS = {5: "a", 6: "b", 7: "c", 8: "alpha", 9: "beta", 10: "gamma"}
INSTRUMENT = "Aeris-fds-Pixcel1d-Medipix3"


def build_pairwise(data_dir: Path) -> dict:
    manual = read_xlsx(data_dir / "pairwise_reactions_manual_rietveld_summary.xlsx")
    bench = read_xlsx(data_dir / "pairwise_reactions-benchmark_summary_102125.xlsx")

    blocks: dict[str, list] = {}
    current = None
    for _, row in manual["Phase_summary"]:
        first = row.get(0, "")
        if first.endswith((".xy", ".xy_1")) or ".xy_" in first:
            current = re.sub(r"\.xy(_1)?$", "", first)
            blocks[current] = []
            continue
        if current is None or first in ("", "Phase Name"):
            continue
        blocks[current].append(row)
    notes = {}
    for _, row in manual["Notes on fits"][1:]:
        key = re.sub(r"\.xy(_1)?$", "", row.get(0, ""))
        if key:
            notes[key] = row
    human = {row[1].strip(): row for _, row in bench["Human"][1:]}

    samples = []
    for sample_id, identities in CURATED.items():
        rows = [row for row in blocks[sample_id] if _float(row.get(3)) is not None]
        unknown_row = any("Unidentified" in row.get(0, "") for row in blocks[sample_id])
        if len(rows) != len(identities):
            raise SystemExit(
                f"{sample_id}: spreadsheet has {len(rows)} phase rows, CURATED lists {len(identities)}"
            )
        reaction, temperature = sample_id.split("_")[0], int(sample_id.split("_")[1].rstrip("C"))
        precursors = [re.sub(r"^\d+(?=[A-Z])", "", item) for item in reaction.split("-")]
        summary = human[reaction.strip()]
        phases = []
        for row, (formula, spacegroup, accept, groups) in zip(rows, identities):
            lattice = {
                key: round(_float(row[column]), 6)
                for column, key in LATTICE_COLUMNS.items()
                if _float(row.get(column)) is not None
            }
            phases.append({
                "formula": formula,
                "spacegroup": spacegroup,
                "spacegroup_any_of": groups,
                "accept_any_of": accept,
                "wt_percent": round(_float(row[3]), 3),
                "lattice": lattice,
                "required": True,
                "source_label": row.get(0, "").strip(),
                "source_reference": row.get(1, "").strip(),
                "note": "",
            })
        note = notes.get(sample_id, {})
        samples.append({
            "id": sample_id,
            "pattern": f"dataset/binary_reactions/{sample_id}.xrdml",
            "submission": {
                "analysis_mode": "elemental",
                "precursors": precursors,
                "elements": _elements(precursors),
                "temperature_c": temperature,
                "atmosphere": "air",
                "instrument_profile": INSTRUMENT,
                "wavelength": "Cu",
            },
            "phases": phases,
            "allowed_extra": [],
            "unknown_phase_present": unknown_row or "unknown" in summary.get(3, ""),
            "fully_indexed": summary.get(4) == "1",
            "reference_fit": {
                "rwp": _float(note.get(6)), "rexp": _float(note.get(5)), "gof": _float(note.get(4)),
                "durbin_watson": _float(note.get(11)),
                "sample_displacement_mm": _float(note.get(12)),
                "sample_displacement_mm_esd": _float(note.get(13)),
            },
            "human_products": summary.get(3, "").strip(),
            "source": (
                "data/pairwise_reactions_manual_rietveld_summary.xlsx (Phase_summary, Notes on fits); "
                "data/pairwise_reactions-benchmark_summary_102125.xlsx (Human)"
            ),
        })
    return {
        "schema": 1,
        "set": "pairwise_reactions",
        "description": (
            "20 binary-reaction products (A-Lab, Aeris, Cu K-alpha, 8-min scans). Phases and wt% from "
            "expert manual Rietveld refinements; wt% are relative to identified crystalline phases."
        ),
        "aliases": {},
        "samples": samples,
    }


def build_precursor(repo: Path = REPO) -> dict:
    samples = []
    for path in sorted((repo / "dataset" / "precursor_mixture").glob("*--*min.xrdml")):
        composition, scan = path.stem.split("--")
        phases = []
        for token in composition.split("-"):
            formula, fraction = re.match(r"^(.+)_(\d+p\d+)$", token).groups()
            spacegroup, groups = PRECURSOR_SG[formula]
            phases.append({
                "formula": formula,
                "spacegroup": spacegroup,
                "spacegroup_any_of": groups,
                "accept_any_of": [],
                "weighed_mass_g": float(fraction.replace("p", ".")),
                "lattice": {},
                "required": True,
                "source_label": formula,
                "source_reference": "weighed precursor (file name)",
                "note": "",
            })
        total = sum(phase["weighed_mass_g"] for phase in phases)
        for phase in phases:
            phase["wt_percent"] = round(100 * phase["weighed_mass_g"] / total, 3)
        formulas = [phase["formula"] for phase in phases]
        samples.append({
            "id": path.stem,
            "pattern": f"dataset/precursor_mixture/{path.name}",
            "submission": {
                "analysis_mode": "elemental",
                "precursors": formulas,
                "elements": _elements(formulas),
                "temperature_c": None,
                "atmosphere": "air",
                "instrument_profile": INSTRUMENT,
                "wavelength": "Cu",
            },
            "phases": phases,
            # Bi2CO5 is a plausible minor phase in Bi2O3 + Li2CO3 mixtures (benchmark sheet notes).
            "allowed_extra": ["Bi2CO5"] if "Bi2O3" in composition and "Li2CO3" in composition else [],
            "unknown_phase_present": False,
            "fully_indexed": True,
            "reference_fit": None,
            "scan": scan,
            "human_products": "",
            "source": "file name (weighed fractions); dataset/README.md",
        })
    return {
        "schema": 1,
        "set": "precursor_mixture",
        "description": (
            "Weighed mixtures of 2-3 commercial precursors (Aeris, Cu K-alpha); 2-min and 8-min scans. "
            "Ground-truth wt% are the weighed masses encoded in the file names (e.g. 0p3022 = 0.3022 g), "
            "renormalised to 100 wt%."
        ),
        "aliases": {
            "Li4CO5": {
                "formula": "Li2CO3",
                "note": "ICSD 181261 is Li2CO3 mislabelled as Li4CO5 (benchmark sheet note).",
            }
        },
        "samples": samples,
    }


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def _describe(old: dict, new: dict) -> str:
    old_samples = {sample["id"]: sample for sample in old.get("samples", [])}
    new_samples = {sample["id"]: sample for sample in new.get("samples", [])}
    changed = sorted(
        key for key in old_samples.keys() & new_samples.keys() if old_samples[key] != new_samples[key]
    )
    added = sorted(new_samples.keys() - old_samples.keys())
    removed = sorted(old_samples.keys() - new_samples.keys())
    parts = []
    if changed:
        parts.append(f"{len(changed)} changed sample(s): {', '.join(changed[:5])}")
    if added:
        parts.append(f"{len(added)} new sample(s)")
    if removed:
        parts.append(f"{len(removed)} missing sample(s)")
    return "; ".join(parts) or "metadata differs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the benchmark ground-truth JSON files.")
    parser.add_argument("--data-dir", type=Path, required=True, help="Folder with the source .xlsx files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output folder")
    parser.add_argument(
        "--check", action="store_true", help="Compare with the committed JSON; exit 1 on differences"
    )
    args = parser.parse_args(argv)

    outputs = {
        "pairwise_reactions.json": build_pairwise(args.data_dir),
        "precursor_mixture.json": build_precursor(),
    }
    if args.check:
        differences = []
        for name, payload in outputs.items():
            path = args.out / name
            try:
                committed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                differences.append(f"{name}: cannot read committed file ({exc})")
                continue
            if committed != payload:
                differences.append(f"{name}: {_describe(committed, payload)}")
        if differences:
            print("Ground truth differs from the spreadsheets:")
            for line in differences:
                print("  " + line)
            return 1
        print("OK: committed ground truth matches the spreadsheets.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (args.out / name).write_text(_dump(payload), encoding="utf-8", newline="\n")
        print(f"wrote {args.out / name} ({len(payload['samples'])} samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
