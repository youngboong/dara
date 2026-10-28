"""Mass-balance check: do the refined phase fractions conserve the precursor cations?

A Rietveld fit can look excellent and still quantify wrongly (or identify a phase
with the wrong chemistry). If the precursor amounts are known, the element ratio
implied by the final phase fractions must match the element ratio of the
precursors for every element that cannot leave the sample. This module turns both
into element mole fractions and reports the largest absolute deviation.

Precursor amount syntax (``precursor_formulas`` submission field, one entry per
precursor, comma separated as before)::

    TiO2                 plain formula, amount unknown
    2 TiO2   2TiO2       leading molar coefficient (formula units, any positive number)
    2*TiO2               same, explicit multiplication sign
    TiO2:0.2004g         weighed mass in g (``mg`` also accepted)
    TiO2:2mol            amount in mol (``mmol`` also accepted)

Rules: with coefficients only, an entry without a coefficient counts as 1. As soon
as one entry carries a mass, every entry must carry a mass or a mol amount,
otherwise the amounts are treated as unknown. A plain list ("Fe2O3, TiO2") never
implies 1:1; the check is then skipped unless the pattern file name encodes the
amounts (see :func:`amounts_from_filename`).

Compared elements exclude O, H, C, N (lost or exchanged as gases) and the open
element of a reaction-mode run. At least two compared elements are required.

Thresholds (largest absolute deviation of an element mole fraction), calibrated on
the 20 pairwise-reaction ground truths (expert refinements, max deviation 0.20 for
a sample with volatile/unidentified losses, most < 0.10) and DARA benchmark runs:

    deviation <= 0.10   ok        (no message)
    0.10 - 0.20         caution   (info caution, listed as an informational reason)
    > 0.20              warning   (warning caution, overall confidence at least "check")
"""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath

OK_MAX = 0.10
WARNING_MIN = 0.20
VOLATILE_ELEMENTS = frozenset({"O", "H", "C", "N"})
ESD_FACTOR = 2.0

_COEFF = re.compile(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*\*?\s*(?=[A-Z(\[])")
_AMOUNT = re.compile(r"^(?P<value>\d+(?:\.\d+)?|\.\d+)\s*(?P<unit>mg|g|mmol|mol)?$", re.I)
_UNIT_FACTORS = {"g": ("g", 1.0), "mg": ("g", 1e-3), "mol": ("mol", 1.0), "mmol": ("mol", 1e-3)}


def _composition(formula: str):
    from pymatgen.core import Composition

    return Composition(formula)


def parse_precursor_entry(entry: str) -> dict:
    """Split ``"2 TiO2"`` / ``"TiO2:0.2g"`` into formula and amount (never validates chemistry).

    Returns ``{"formula", "coefficient", "amount", "unit", "input"}``; ``unit`` is ``"g"``,
    ``"mol"`` or ``None``. Raises ``ValueError`` for a malformed amount.
    """
    text = str(entry).strip()
    coefficient = amount = unit = None
    if ":" in text:
        text, raw_amount = (part.strip() for part in text.rsplit(":", 1))
        match = _AMOUNT.match(raw_amount)
        if not match:
            raise ValueError(f"Invalid precursor amount: {entry!r}")
        unit, factor = _UNIT_FACTORS[(match.group("unit") or "g").lower()]
        amount = float(match.group("value")) * factor
    match = _COEFF.match(text)
    if match:
        coefficient = float(match.group(1))
        text = text[match.end():].strip()
    if coefficient is not None and amount is not None:
        raise ValueError(f"Give either a coefficient or an amount, not both: {entry!r}")
    if (coefficient is not None and coefficient <= 0) or (amount is not None and amount <= 0):
        raise ValueError(f"Precursor amounts must be positive: {entry!r}")
    if not text:
        raise ValueError(f"Missing precursor formula: {entry!r}")
    return {
        "formula": text,
        "coefficient": coefficient,
        "amount": amount,
        "unit": unit,
        "input": str(entry).strip(),
    }


def split_precursor_entries(entries) -> tuple[list[str], list[dict] | None]:
    """Return ``(plain formulas, molar amounts or None)`` for the precursor field.

    The plain formulas go to every existing consumer (reaction prediction, known-mixture
    search, element derivation) exactly as before. ``amounts`` is a list of
    ``{"formula", "moles", "input"}`` (relative moles) or ``None`` when the entries do not
    define every amount. Raises ``ValueError`` for malformed amounts or formulas.
    """
    parsed = [parse_precursor_entry(entry) for entry in entries or []]
    formulas = [item["formula"] for item in parsed]
    return formulas, _amounts_from_parsed(parsed)


def _amounts_from_parsed(parsed: list[dict]) -> list[dict] | None:
    if not parsed:
        return None
    has_amount = any(item["amount"] is not None for item in parsed)
    has_coefficient = any(item["coefficient"] is not None for item in parsed)
    if not has_amount and not has_coefficient:
        return None
    if has_amount and any(item["amount"] is None for item in parsed):
        return None  # masses mixed with bare formulas or coefficients: incomplete
    amounts = []
    for item in parsed:
        if item["amount"] is not None and item["unit"] == "g":
            moles = item["amount"] / float(_composition(item["formula"]).weight)
        elif item["amount"] is not None:
            moles = item["amount"]
        else:
            moles = item["coefficient"] if item["coefficient"] is not None else 1.0
        amounts.append({"formula": item["formula"], "moles": moles, "input": item["input"]})
    return amounts


_FILENAME_MASS = re.compile(r"^\d+(?:p|\.)\d+$")
_FILENAME_COEFF = re.compile(r"^(\d+(?:p\d+)?)(?=[A-Z(])")


def amounts_from_filename(filename: str | None, precursors) -> dict | None:
    """Read precursor amounts from a benchmark-style file name, only when unambiguous.

    Supported: ``2Fe3O4-3Y2O3_1000C_60min`` (molar coefficients, missing = 1) and
    ``In2O3_0p3022-La(OH)3_0p5014-TiO2_0p2004--8min`` (grams, ``p`` = decimal point).
    Every precursor must match exactly one '-'-separated chunk, every formula chunk must
    be a precursor, and masses must be given for all or none. Returns
    ``{"amounts": [...], "basis": "molar"|"mass"}`` or ``None``.
    """
    precursors = [str(p).strip() for p in precursors or [] if str(p).strip()]
    if not filename or len(precursors) < 2:
        return None
    try:
        keys = [_composition(p).reduced_formula for p in precursors]
    except Exception:
        return None
    if len(set(keys)) != len(keys):
        return None
    stem = PurePosixPath(str(filename).replace("\\", "/")).name
    stem = re.sub(r"\.[A-Za-z][A-Za-z0-9]{0,6}$", "", stem)
    chunks = []
    for chunk in (c for c in stem.split("-") if c):
        parts = chunk.split("_")
        head = parts[0]
        coeff = None
        match = _FILENAME_COEFF.match(head)
        if match:
            coeff = float(match.group(1).replace("p", "."))
            head = head[match.end():]
        if not head or not head[0].isupper():
            continue
        try:
            key = _composition(head).reduced_formula
        except Exception:
            continue
        mass = None
        if len(parts) > 1 and _FILENAME_MASS.match(parts[1]):
            mass = float(parts[1].replace("p", "."))
        chunks.append({"key": key, "formula": head, "coefficient": coeff, "mass": mass})
    if len(chunks) != len(precursors):
        return None
    by_key = {}
    for chunk in chunks:
        if chunk["key"] in by_key:
            return None
        by_key[chunk["key"]] = chunk
    if set(by_key) != set(keys):
        return None
    masses = [by_key[key]["mass"] for key in keys]
    if any(m is not None for m in masses):
        if any(m is None for m in masses) or any(by_key[k]["coefficient"] for k in keys):
            return None
        parsed = [
            {"formula": p, "coefficient": None, "amount": m, "unit": "g", "input": f"{p}:{m:g}g"}
            for p, m in zip(precursors, masses)
        ]
        basis = "mass"
    else:
        parsed = []
        for p, key in zip(precursors, keys):
            coeff = by_key[key]["coefficient"]
            parsed.append({
                "formula": p, "coefficient": coeff or 1.0, "amount": None, "unit": None,
                "input": f"{coeff:g} {p}" if coeff else p,
            })
        basis = "molar"
    amounts = _amounts_from_parsed(parsed)
    return {"amounts": amounts, "basis": basis} if amounts else None


def phase_formula(name: str) -> str | None:
    """Chemical formula of a refined phase name such as ``TiO2_136_(cod_9007432)-10``."""
    token = str(name).split("_")[0].strip()
    if not token:
        return None
    try:
        comp = _composition(token)
    except Exception:
        return None
    return token if comp.num_atoms > 0 else None


def _element_moles(items) -> dict[str, float]:
    """``items`` = iterable of (formula, formula-unit moles)."""
    totals: dict[str, float] = {}
    for formula, moles in items:
        for element, count in _composition(formula).get_el_amt_dict().items():
            totals[element] = totals.get(element, 0.0) + moles * count
    return totals


def _fractions(moles: dict[str, float], elements: list[str]) -> dict[str, float] | None:
    total = sum(moles.get(e, 0.0) for e in elements)
    if total <= 0:
        return None
    return {e: moles.get(e, 0.0) / total for e in elements}


def _observed_moles(weights: dict[str, float], formulas: dict[str, str]) -> dict[str, float]:
    return _element_moles(
        (formulas[name], weight / float(_composition(formulas[name]).weight))
        for name, weight in weights.items()
    )


def _ratio_text(elements: list[str], fractions: dict[str, float]) -> str:
    return ":".join(elements) + " = " + ":".join(f"{fractions[e]:.2f}" for e in elements)


def _skip(reason: str, **extra) -> dict:
    return {"status": "skipped", "level": None, "reason": reason, **extra}


def mass_balance(
    amounts: list[dict] | None,
    weight_percent: dict | None,
    *,
    weight_esd: dict | None = None,
    open_element: str | None = None,
    source: str = "input",
) -> dict:
    """Compare precursor and phase-fraction element ratios.

    ``amounts`` are ``{"formula", "moles"}`` rows; ``weight_percent`` maps refined phase
    names to wt%. ``weight_esd`` (phase -> wt% esd) widens the tolerance by
    ``ESD_FACTOR`` sigma. Never raises; returns a ``status`` of ``"evaluated"`` or
    ``"skipped"`` with a ``reason``.
    """
    try:
        if not amounts:
            return _skip("Add precursor amounts (e.g. 2 TiO2) to enable the mass-balance check.")
        weights = {
            name: float(value) for name, value in (weight_percent or {}).items()
            if value is not None and math.isfinite(float(value)) and float(value) > 0
        }
        if not weights:
            return _skip("No phase fractions are available.")
        formulas = {}
        for name in weights:
            formula = phase_formula(name)
            if formula is None:
                return _skip(f"The chemical formula of phase {name} could not be read.")
            formulas[name] = formula
        excluded = set(VOLATILE_ELEMENTS)
        if open_element:
            excluded.add(str(open_element).split("_")[0])
        expected_moles = _element_moles((a["formula"], float(a["moles"])) for a in amounts)
        observed_moles = _observed_moles(weights, formulas)
        expected_elements = sorted(e for e in expected_moles if e not in excluded)
        if len(expected_elements) < 2:
            return _skip("Fewer than two non-volatile elements are present in the precursors.")
        extra = sorted(e for e in observed_moles if e not in excluded and e not in expected_moles)
        elements = expected_elements + extra
        expected = _fractions(expected_moles, elements)
        observed = _fractions(observed_moles, elements)
        if expected is None or observed is None:
            return _skip("The final phases contain none of the compared elements.")
        deviations = {e: observed[e] - expected[e] for e in elements}
        worst = max(elements, key=lambda e: abs(deviations[e]))
        max_dev = abs(deviations[worst])

        sigma = None
        if weight_esd and all(weight_esd.get(name) is not None for name in weights):
            variance = 0.0
            for name, esd in weight_esd.items():
                if name not in weights or not esd:
                    continue
                shifted = dict(weights)
                shifted[name] = max(shifted[name] + float(esd), 0.0)
                moved = _fractions(_observed_moles(shifted, formulas), elements)
                if moved is not None:
                    variance += (moved[worst] - observed[worst]) ** 2
            sigma = math.sqrt(variance)
        effective = max(0.0, max_dev - ESD_FACTOR * sigma) if sigma else max_dev
        level = "ok" if effective <= OK_MAX else "caution" if effective <= WARNING_MIN else "warning"
        return {
            "status": "evaluated",
            "level": level,
            "source": source,
            "max_deviation": round(max_dev, 4),
            "deviation_sigma": round(sigma, 4) if sigma is not None else None,
            "largest_deviation_element": worst,
            "elements": [
                {
                    "element": e,
                    "expected": round(expected[e], 4),
                    "observed": round(observed[e], 4),
                    "deviation": round(deviations[e], 4),
                }
                for e in elements
            ],
            "expected_ratio": _ratio_text(elements, expected),
            "observed_ratio": _ratio_text(elements, observed),
            "excluded_elements": sorted(excluded),
            "precursor_amounts": [
                {"formula": a["formula"], "moles": round(float(a["moles"]), 6), "input": a.get("input")}
                for a in amounts
            ],
            "thresholds": {"ok_max": OK_MAX, "warning_min": WARNING_MIN},
        }
    except Exception as exc:  # the check is advisory and must never break a result
        return _skip(f"The mass-balance check could not be computed ({exc}).")


def mass_balance_message(block: dict | None) -> str | None:
    """Non-prescriptive sentence for a caution/confidence reason, or ``None`` when OK."""
    if not block or block.get("status") != "evaluated" or block.get("level") == "ok":
        return None
    elements = [row["element"] for row in block["elements"]]
    observed = ":".join(f"{row['observed']:.2f}" for row in block["elements"])
    expected = ":".join(f"{row['expected']:.2f}" for row in block["elements"])
    source = " (amounts read from the file name)" if block.get("source") == "filename" else ""
    return (
        f"Phase fractions imply {':'.join(elements)} = {observed}, but the precursors{source} "
        f"imply {expected} (largest deviation {block['max_deviation']:.2f}). Possible "
        "unidentified or amorphous phase, volatile loss, or a quantification problem."
    )


def resolve_amounts(
    metadata_amounts: list[dict] | None, precursors, filename: str | None
) -> tuple[list[dict] | None, str | None]:
    """Explicit amounts from the submission win; otherwise try the file name."""
    if metadata_amounts:
        return metadata_amounts, "input"
    inferred = amounts_from_filename(filename, precursors)
    if inferred:
        return inferred["amounts"], "filename"
    return None, None


def apply_to_confidence(confidence: dict | None, block: dict | None) -> dict | None:
    """Fold a mass-balance result into a confidence block's overall verdict (in place).

    Warning -> at least "check" (an "expert" verdict is never raised by this check);
    caution -> an informational reason without changing the level.
    """
    message = mass_balance_message(block)
    if not confidence or not message:
        return confidence
    overall = confidence.get("overall")
    if not isinstance(overall, dict):
        return confidence
    reasons = list(overall.get("reasons") or [])
    reason = "Mass balance: " + message
    if block["level"] == "warning":
        if overall.get("level") == "high":
            overall["level"] = "check"
            overall["label"] = "Check recommended"
            reasons = [reason]
        else:
            reasons = reasons[:3] + [reason]
    elif len(reasons) < 4:
        reasons.append(reason)
    overall["reasons"] = reasons[:4]
    confidence["mass_balance"] = {
        k: block.get(k) for k in ("level", "max_deviation", "expected_ratio", "observed_ratio")
    }
    return confidence
