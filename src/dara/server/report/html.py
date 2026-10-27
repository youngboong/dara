"""HTML scaffolding for the paper-style report (no scripts, no external resources)."""

from __future__ import annotations

import re
from html import escape

STYLE = (
    "body{font:14px/1.55 Georgia,'Times New Roman',serif;color:#1a1a1a;max-width:960px;"
    "margin:32px auto;padding:0 24px}"
    "h1{font-size:25px;line-height:1.25;margin:0 0 8px}"
    "h2{font-size:18px;margin:28px 0 8px;border-bottom:1px solid #cbd5e1;padding-bottom:3px}"
    "h3{font-size:15px;margin:18px 0 6px}"
    "table{border-collapse:collapse;width:100%;margin:10px 0;font:13px/1.4 system-ui,Arial,sans-serif}"
    "th,td{padding:6px 8px;border:1px solid #cbd5e1;text-align:left;vertical-align:top;"
    "overflow-wrap:anywhere}"
    "th{background:#f1f5f9}"
    "pre{font:12px/1.4 ui-monospace,Consolas,monospace;white-space:pre-wrap;margin:0;"
    "overflow-wrap:anywhere}"
    "small,.muted{color:#475569}"
    "figure{margin:14px 0}figcaption{font-size:13px;color:#334155;margin-top:6px}"
    "svg{width:100%;height:auto}"
    ".byline{color:#475569;font-size:13px}"
    ".notes{white-space:pre-wrap;background:#f8fafc;padding:12px;border:1px solid #cbd5e1}"
    ".verdict{font-weight:600}"
    "ol.refs{padding-left:22px}ol.refs li,ol li{margin:4px 0}"
    "@media print{body{max-width:none;margin:0;padding:0;font-size:10pt}"
    "svg,tr,figure{break-inside:avoid}thead{display:table-header-group}"
    "h2,h3{break-after:avoid}a{color:inherit;text-decoration:none}}"
)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Table whose ``rows`` cells are already escaped HTML."""
    return (
        "<table><thead><tr>"
        + "".join(f"<th>{escape(header)}</th>" for header in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
        + "</tbody></table>"
    )


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
