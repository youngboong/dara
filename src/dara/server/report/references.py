"""Bibliography used by the generated report and the export READMEs.

Bibliographic data were checked against the publishers' DOI records (Crossref).
The BGMN reference has no DOI; it is the program's standard citation.
"""

from __future__ import annotations

from html import escape

REFERENCES: dict[str, dict] = {
    "dara": {
        "text": (
            "Y. Fei, M. J. McDermott, C. L. Rom, S. Wang, G. Ceder, \"Dara: Automated "
            "multiple-hypothesis phase identification and refinement from powder X-ray "
            "diffraction\", Chem. Mater. 38 (2026) 1364–1376."
        ),
        "doi": "10.1021/acs.chemmater.5c02820",
    },
    "bgmn": {
        "text": (
            "J. Bergmann, P. Friedel, R. Kleeberg, \"BGMN — a new fundamental parameters "
            "based Rietveld program for laboratory X-ray sources, its use in quantitative "
            "analysis and structure investigations\", CPD Newsletter 20 (1998) 5–8."
        ),
        "doi": None,
    },
    "cod": {
        "text": (
            "S. Gražulis et al., \"Crystallography Open Database (COD): an open-access "
            "collection of crystal structures and platform for world-wide collaboration\", "
            "Nucleic Acids Res. 40 (2012) D420–D427."
        ),
        "doi": "10.1093/nar/gkr900",
    },
    "mp": {
        "text": (
            "A. Jain et al., \"Commentary: The Materials Project: A materials genome approach "
            "to accelerating materials innovation\", APL Mater. 1 (2013) 011002."
        ),
        "doi": "10.1063/1.4812323",
    },
    "pymatgen": {
        "text": (
            "S. P. Ong et al., \"Python Materials Genomics (pymatgen): A robust, open-source "
            "python library for materials analysis\", Comput. Mater. Sci. 68 (2013) 314–319."
        ),
        "doi": "10.1016/j.commatsci.2012.10.028",
    },
    "profex": {
        "text": (
            "N. Doebelin, R. Kleeberg, \"Profex: a graphical user interface for the Rietveld "
            "refinement program BGMN\", J. Appl. Cryst. 48 (2015) 1573–1580."
        ),
        "doi": "10.1107/S1600576715014685",
    },
    "hamilton": {
        "text": (
            "W. C. Hamilton, \"Significance tests on the crystallographic R factor\", "
            "Acta Cryst. 18 (1965) 502–510."
        ),
        "doi": "10.1107/S0365110X65001081",
    },
    "toby": {
        "text": (
            "B. H. Toby, \"R factors in Rietveld analysis: How good is good enough?\", "
            "Powder Diffr. 21 (2006) 67–70."
        ),
        "doi": "10.1154/1.2179804",
    },
}


def reference_text(key: str) -> str:
    """Plain-text reference with its DOI URL (for README files)."""
    entry = REFERENCES[key]
    doi = f" https://doi.org/{entry['doi']}" if entry["doi"] else ""
    return entry["text"] + doi


class Citations:
    """Numbers references in order of first citation."""

    def __init__(self) -> None:
        self._order: list[str] = []

    def cite(self, key: str) -> str:
        """Return ``[n]`` for ``key`` and remember it for the reference list."""
        if key not in REFERENCES:
            raise KeyError(key)
        if key not in self._order:
            self._order.append(key)
        return f"[{self._order.index(key) + 1}]"

    def __bool__(self) -> bool:
        return bool(self._order)

    def keys(self) -> list[str]:
        return list(self._order)

    def render_html(self) -> str:
        """Ordered list of the references that were cited."""
        if not self._order:
            return "<p>No references were cited.</p>"
        items = []
        for key in self._order:
            entry = REFERENCES[key]
            link = (
                f' <a href="https://doi.org/{escape(entry["doi"])}">'
                f'https://doi.org/{escape(entry["doi"])}</a>'
                if entry["doi"]
                else ""
            )
            items.append(f"<li>{escape(entry['text'])}{link}</li>")
        return "<ol>" + "".join(items) + "</ol>"
