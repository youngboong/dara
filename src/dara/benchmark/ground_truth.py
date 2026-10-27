"""Curated ground truth for the DARA accuracy benchmark.

Two sets ship with the repository under ``dataset/ground_truth``:

* ``pairwise_reactions`` - 20 binary-reaction products (expert manual Rietveld).
* ``precursor_mixture`` - 40 weighed precursor mixtures (2-min and 8-min scans).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GTPhase(_Model):
    formula: str
    spacegroup: int | None = None
    spacegroup_any_of: list[int] = []
    accept_any_of: list[str] = []
    wt_percent: float
    weighed_mass_g: float | None = None
    lattice: dict[str, float] = {}
    required: bool = True
    source_label: str = ""
    source_reference: str = ""
    note: str = ""


class GTSubmission(_Model):
    analysis_mode: Literal["elemental", "reaction", "known_mixture"]
    precursors: list[str]
    elements: list[str]
    temperature_c: int | None = None
    atmosphere: str = "air"
    instrument_profile: str
    wavelength: str | float = "Cu"


class GTReferenceFit(_Model):
    rwp: float | None = None
    rexp: float | None = None
    gof: float | None = None
    durbin_watson: float | None = None
    sample_displacement_mm: float | None = None
    sample_displacement_mm_esd: float | None = None


class GTAlias(_Model):
    formula: str
    note: str = ""


class GTSample(_Model):
    id: str
    pattern: str
    submission: GTSubmission
    phases: list[GTPhase]
    allowed_extra: list[str] = []
    unknown_phase_present: bool
    fully_indexed: bool
    reference_fit: GTReferenceFit | None = None
    scan: str | None = None
    human_products: str = ""
    source: str


class GTSet(_Model):
    schema_: int = Field(alias="schema")
    set: str
    description: str
    aliases: dict[str, GTAlias] = {}
    samples: list[GTSample]


GT_FILES = {
    "pairwise": "pairwise_reactions.json",
    "precursor": "precursor_mixture.json",
}


def repo_root() -> Path:
    """Repository root (the folder holding ``dataset/``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "dataset" / "ground_truth").is_dir():
            return parent
    return here.parents[3]


def ground_truth_path(name: Literal["pairwise", "precursor"] | Path) -> Path:
    if isinstance(name, Path):
        return name
    if name not in GT_FILES:
        raise ValueError(f"Unknown ground-truth set: {name!r}")
    return repo_root() / "dataset" / "ground_truth" / GT_FILES[name]


def load_ground_truth(name: Literal["pairwise", "precursor"] | Path) -> GTSet:
    """Load one ground-truth set by short name or file path."""
    path = ground_truth_path(name)
    return GTSet.model_validate(json.loads(path.read_text(encoding="utf-8")))


def validate_ground_truth(gt: GTSet, repo_root: Path) -> list[str]:
    """Return a list of problems (empty when the set is valid)."""
    from pymatgen.core import Composition

    problems: list[str] = []
    seen: set[str] = set()

    def parses(formula: str) -> bool:
        try:
            return len(Composition(formula).elements) > 0
        except Exception:
            return False

    for sample in gt.samples:
        label = sample.id
        if sample.id in seen:
            problems.append(f"{label}: duplicate sample id")
        seen.add(sample.id)
        if not (Path(repo_root) / sample.pattern).is_file():
            problems.append(f"{label}: pattern file not found: {sample.pattern}")
        if not sample.phases:
            problems.append(f"{label}: no phases")
        total = sum(phase.wt_percent for phase in sample.phases)
        if abs(total - 100.0) > 0.5:
            problems.append(f"{label}: wt% sums to {total:.2f}, expected 100 +/- 0.5")
        for phase in sample.phases:
            for formula in [phase.formula, *phase.accept_any_of]:
                if not parses(formula):
                    problems.append(f"{label}: formula does not parse: {formula!r}")
            for number in [phase.spacegroup, *phase.spacegroup_any_of]:
                if number is not None and not 1 <= number <= 230:
                    problems.append(f"{label}: invalid space group {number} for {phase.formula}")
        for formula in sample.allowed_extra:
            if not parses(formula):
                problems.append(f"{label}: allowed_extra formula does not parse: {formula!r}")
    for alias, target in gt.aliases.items():
        if not parses(target.formula):
            problems.append(f"alias {alias}: formula does not parse: {target.formula!r}")
    return problems
