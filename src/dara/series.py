"""Parse an ordering variable (temperature, time, ...) out of series file names.

The parsing is deliberately conservative: only a token that is *entirely* a number
followed by a known unit (``300C``, ``60min``, ``0p5wt%``) is accepted, so chemical
formulas such as ``Bi2O3-2MoO3`` never produce a variable. Tokens are separated by
underscores, spaces and hyphens (``ZnO-500C-2h`` has the tokens ``ZnO``, ``500C``, ``2h``). The variable is used for
ordering and plotting only; it never influences the analysis itself.
"""

from __future__ import annotations

import re
from pathlib import Path

UNIT_PATTERN = r"(degC|°C|C|K|min|h|s|MPa|GPa|bar|wt%|mol%|at%|%|V|x)"
_TOKEN_RE = re.compile(r"^(\d+(?:[.p]\d+)?)" + UNIT_PATTERN + r"$", re.IGNORECASE)
_CANONICAL_UNITS = {
    "degc": "°C",
    "°c": "°C",
    "c": "°C",
    "k": "K",
    "min": "min",
    "h": "h",
    "s": "s",
    "mpa": "MPa",
    "gpa": "GPa",
    "bar": "bar",
    "wt%": "wt%",
    "mol%": "mol%",
    "at%": "at%",
    "%": "%",
    "v": "V",
    "x": "x",
}
_EXTENSION_RE = re.compile(r"^\.[A-Za-z][A-Za-z0-9]{0,5}$")


def parse_variable_token(token: str) -> dict | None:
    """Return ``{"raw", "value", "unit"}`` for a full-token ``<number><unit>`` or ``None``."""
    match = _TOKEN_RE.match(token or "")
    if match is None:
        return None
    number, unit = match.group(1), match.group(2)
    # A bare "c" or "k" is ambiguous (centi-, kilo-, a typo); only the capital letters count.
    if unit.lower() in {"c", "k"} and unit not in {"C", "K"}:
        return None
    canonical = _CANONICAL_UNITS.get(unit.lower())
    if canonical is None:
        return None
    return {"raw": token, "value": float(number.replace("p", ".").replace("P", ".")), "unit": canonical}


def _stem(filename: str) -> str:
    """File name without a real extension (``a_0.5wt%`` keeps its decimal part)."""
    path = Path(str(filename).replace("\\", "/"))
    suffix = path.suffix
    return path.name[: -len(suffix)] if suffix and _EXTENSION_RE.match(suffix) else path.name


def _tokens(filename: str) -> list[str]:
    return [token for token in re.split(r"[_\s-]+", _stem(filename)) if token != ""]


def parse_series_variables(filenames: list[str]) -> dict:
    """Pick the first (token position, unit) slot that is present in every file and varies."""
    empty = {"unit": None, "slot": None, "variables": [None] * len(filenames)}
    if len(filenames) < 2:
        return empty
    per_file: list[dict[tuple[int, str], dict]] = []
    for name in filenames:
        slots: dict[tuple[int, str], dict] = {}
        for index, token in enumerate(_tokens(name)):
            parsed = parse_variable_token(token)
            if parsed is not None:
                slots[(index, parsed["unit"])] = parsed
        per_file.append(slots)
    common = set(per_file[0])
    for slots in per_file[1:]:
        common &= set(slots)
    for slot in sorted(common):
        values = {slots[slot]["value"] for slots in per_file}
        if len(values) > 1:
            return {
                "unit": slot[1],
                "slot": slot[0],
                "variables": [dict(slots[slot]) for slots in per_file],
            }
    return empty


def _natural_key(name: str) -> list:
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r"(\d+)", str(name))
        if part != ""
    ]


def series_order(filenames: list[str], variables: list[dict | None]) -> list[int]:
    """Order index per file: by variable value when all files have one, else natural name order."""
    count = len(filenames)
    if count and len(variables) == count and all(item is not None for item in variables):
        keys = [(item["value"], _natural_key(name)) for name, item in zip(filenames, variables)]
    else:
        keys = [_natural_key(name) for name in filenames]
    ranked = sorted(range(count), key=lambda index: keys[index])
    order = [0] * count
    for rank, index in enumerate(ranked):
        order[index] = rank
    return order
