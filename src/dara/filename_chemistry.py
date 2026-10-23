"""Conservative formula suggestions from delimiter-separated measurement filenames."""
import re
from pathlib import PurePosixPath

from pymatgen.core import Composition, Element


def suggest_filename_chemistry(filename: str) -> dict:
    """Return candidate formulas/elements, never infer isolated element symbols.

    Full tokens only: temperatures, times and arbitrary labels must not contribute
    partial matches. Ambiguous elemental samples and symbolic stoichiometry require
    manual input. A syntactically valid compound is still only a suggestion.
    """
    name = PurePosixPath(filename.replace("\\", "/")).stem
    formulas, elements = [], []
    for token in re.split(r"[\s_+,;\-]+", name):
        token = re.sub(r"^\d+(?:\.\d+)?(?=[A-Z])", "", token)
        if not token or len(token) > 120:
            continue
        # Avoid labels such as SC/RT and do not search inside an arbitrary word.
        if not re.search(r"[a-z0-9]", token):
            continue
        parts = re.findall(r"[A-Z][a-z]?|\d+(?:\.\d+)?|[()]", token)
        if "".join(parts) != token:
            continue
        depth = 0
        previous = None
        valid = True
        symbols = []
        for part in parts:
            if part == "(":
                depth += 1
            elif part == ")":
                if depth == 0 or previous == "(":
                    valid = False
                    break
                depth -= 1
            elif part[0].isdigit():
                if previous is None or previous == "(" or previous[0].isdigit() or float(part) <= 0:
                    valid = False
                    break
            elif not Element.is_valid_symbol(part):
                valid = False
                break
            else:
                symbols.append(part)
            previous = part
        if not valid or depth or len(set(symbols)) < 2:
            continue
        try:
            Composition(token, strict=True)
        except (ValueError, TypeError):
            continue
        if token not in formulas:
            formulas.append(token)
        for symbol in symbols:
            if symbol not in elements:
                elements.append(symbol)
    return {"formulas": formulas, "elements": elements}
