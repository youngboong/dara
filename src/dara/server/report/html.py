"""HTML scaffolding for the paper-style report (no scripts, no external resources)."""

from __future__ import annotations

import re
from html import escape

SERIF = '"Latin Modern Roman","CMU Serif","Times New Roman",Georgia,serif'

STYLE = (
    f"body{{font:15px/1.5 {SERIF};color:#000;background:#fff;max-width:900px;margin:32px auto;"
    "padding:0 24px;font-variant-numeric:tabular-nums}"
    "h1{font-size:24px;font-weight:700;line-height:1.25;margin:0 0 6px}"
    "h2{font-size:17px;font-weight:700;margin:26px 0 8px}"
    "h3{font-size:15px;font-weight:700;margin:16px 0 4px}"
    "p{margin:6px 0}"
    "a{color:#000}"
    f"table{{border-collapse:collapse;width:100%;margin:8px 0 12px;font:13.5px/1.35 {SERIF};"
    "font-variant-numeric:tabular-nums}"
    "table.compact{width:auto;min-width:45%}table.compact td,table.compact th{padding-right:22px}"
    "table.compact th:last-child,table.compact td:last-child{padding-right:0}"
    "caption{caption-side:top;text-align:left;font-size:13px;padding:0 0 4px}"
    "th,td{padding:3px 8px;text-align:left;vertical-align:top;border:0;background:none}"
    "th:first-child,td:first-child{padding-left:0}th:last-child,td:last-child{padding-right:0}"
    "th{font-weight:600;vertical-align:bottom}"
    "thead tr:first-child th{border-top:1.5px solid #000}"
    "thead th{border-bottom:.75px solid #000}"
    "tbody tr:last-child td{border-bottom:1.5px solid #000}"
    "th.r,td.r{text-align:right}.nw{white-space:nowrap}"
    "td.lp span{display:block;white-space:nowrap}"
    "ul{margin:4px 0 8px;padding-left:20px}li{margin:3px 0}"
    "figure{margin:12px 0}figcaption{font-size:13px;margin-top:6px}"
    "svg{width:100%;height:auto}"
    ".byline{font-size:13px;margin:0}"
    ".note{font-size:12.5px;margin:-6px 0 12px}"
    ".notes{white-space:pre-wrap;margin:4px 0 4px 20px}"
    "ol.refs{padding-left:24px;font-size:13px}ol.refs li{margin:3px 0}"
    "@page{size:A4;margin:18mm}"
    "@media print{body{max-width:none;margin:0;padding:0;font-size:10.5pt}"
    "svg,tr,figure,li{break-inside:avoid}thead{display:table-header-group}"
    "h2,h3,caption{break-after:avoid}a{text-decoration:none}}"
)


def table(
    headers: list[str], rows: list[list[str]], *, caption: str | None = None,
    widths: list[float] | None = None, align: str | None = None,
    nowrap: str | None = None, cell_classes: dict[int, str] | None = None,
    compact: bool = False,
) -> str:
    """Booktabs-style table whose ``rows`` cells are already escaped HTML.

    ``widths`` are column percentages (a ``<colgroup>``), ``align`` one letter per column
    (``l`` or ``r``), ``nowrap`` one letter per column (``n`` = no line break) and
    ``cell_classes`` extra classes by column index. ``caption`` (escaped HTML) goes above.
    ``compact`` tables take only the width their content needs (no ``widths``).
    """
    count = len(headers)
    align = (align or "l" * count).ljust(count, "l")
    nowrap = (nowrap or "-" * count).ljust(count, "-")
    extra = cell_classes or {}

    def classes(index: int, header: bool = False) -> str:
        names = []
        if align[index] == "r":
            names.append("r")
        if nowrap[index] == "n" and not header:
            names.append("nw")
        if extra.get(index) and not header:
            names.append(extra[index])
        return f' class="{" ".join(names)}"' if names else ""

    colgroup = (
        "<colgroup>" + "".join(f'<col style="width:{w:g}%">' for w in widths) + "</colgroup>"
        if widths and not compact
        else ""
    )
    return (
        ('<table class="compact">' if compact else "<table>")
        + (f"<caption>{caption}</caption>" if caption else "")
        + colgroup
        + "<thead><tr>"
        + "".join(f"<th{classes(i, True)}>{escape(h)}</th>" for i, h in enumerate(headers))
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td{classes(i)}>{cell}</td>" for i, cell in enumerate(row)) + "</tr>"
            for row in rows
        )
        + "</tbody></table>"
    )


def wbr_after_underscores(text: str) -> str:
    """Escape ``text`` and allow a line break only after underscores."""
    return escape(text).replace("_", "_<wbr>")


def embed_svg(svg: bytes | str, label: str) -> str:
    """Inline an SVG document (drop the XML prolog and add an accessible name)."""
    text = svg.decode("utf-8") if isinstance(svg, bytes) else svg
    start = text.find("<svg")
    if start > 0:
        text = text[start:]
    if re.match(r"<svg[^>]*aria-label", text):
        return text
    return re.sub(
        r"<svg\b", f'<svg role="img" aria-label="{escape(label, quote=True)}"', text, count=1
    )


def page(title: str, body: str) -> str:
    """Complete standalone HTML document."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{STYLE}</style></head><body>{body}</body></html>"
    )
