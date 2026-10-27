"""Publication-style Rietveld fit figure (observed, calculated, background, ticks, difference)."""

from __future__ import annotations

import io
import threading
from typing import Literal

import numpy as np

_RENDER_LOCK = threading.Lock()

DPI_RANGE = (72, 1200)
WIDTH_MM_RANGE = (40.0, 250.0)
HEIGHT_RATIO = 0.68
FONT_SIZE = 8
EXCLUDED_LABEL = "Excluded from refinement"


_SUBSCRIPTS = str.maketrans("0123456789.", "₀₁₂₃₄₅₆₇₈₉.")


def _default_label(name: str) -> str:
    """Readable phase label with formula subscripts as Unicode characters."""
    try:
        import re
        from html import unescape

        from dara.plot import phase_display_name
        html = phase_display_name(name)
        html = re.sub(
            r"<sub>([^<]*)</sub>", lambda match: match.group(1).translate(_SUBSCRIPTS), html
        )
        return unescape(re.sub(r"<[^>]+>", "", html))
    except Exception:
        return str(name)


def _reflection_positions(refinement, names: list[str]) -> dict[str, np.ndarray]:
    """2θ of every reflection per refined phase (empty when peak data are unavailable)."""
    positions: dict[str, np.ndarray] = {name: np.array([]) for name in names}
    try:
        import pandas as pd

        frame = pd.DataFrame(refinement.peak_data)
        if "2theta" not in frame:
            return positions
        for index, name in enumerate(names):
            if "phase" in frame:
                chosen = frame.loc[frame["phase"] == name, "2theta"]
            else:
                chosen = frame.loc[frame.get("phase_idx") == index, "2theta"]
            values = np.asarray(chosen, dtype=float)
            positions[name] = values[np.isfinite(values)]
    except Exception:
        pass
    return positions


def _clean_ranges(ranges, low: float, high: float) -> list[tuple[float, float]]:
    cleaned = []
    for interval in ranges or []:
        try:
            start, end = float(interval[0]), float(interval[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not (np.isfinite(start) and np.isfinite(end)) or end <= low or start >= high:
            continue
        cleaned.append((max(start, low), min(end, high)))
    return cleaned


def fit_figure(
    refinement,
    *,
    fmt: Literal["png", "svg"],
    dpi: int = 600,
    width_mm: float = 170.0,
    excluded_ranges=None,
    counts_like: bool | None = None,
    phase_labels: dict[str, str] | None = None,
) -> bytes:
    """Render the refinement fit as PNG or SVG bytes (deterministic for equal input)."""
    if fmt not in {"png", "svg"}:
        raise ValueError(f"Unsupported figure format: {fmt}")
    dpi = int(min(max(int(dpi), DPI_RANGE[0]), DPI_RANGE[1]))
    width_mm = float(min(max(float(width_mm), WIDTH_MM_RANGE[0]), WIDTH_MM_RANGE[1]))

    data = refinement.plot_data
    x = np.asarray(data.x, dtype=float)
    observed = np.asarray(data.y_obs, dtype=float)
    calculated = np.asarray(data.y_calc, dtype=float)
    background = np.asarray(data.y_bkg, dtype=float)
    finite_x = x[np.isfinite(x)]
    if finite_x.size < 2 or float(finite_x.max()) <= float(finite_x.min()):
        raise ValueError("The refinement has no usable 2θ range to plot")
    low, high = float(finite_x.min()), float(finite_x.max())
    difference = observed - calculated

    names = list(refinement.lst_data.phases_results)
    labels = {name: (phase_labels or {}).get(name) or _default_label(name) for name in names}
    reflections = _reflection_positions(refinement, names)
    spans = _clean_ranges(excluded_ranges, low, high)

    import matplotlib

    try:
        matplotlib.use("Agg")
    except Exception:
        pass
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    width_in = width_mm / 25.4
    colors = [matplotlib.colormaps["tab10"](i % 10) for i in range(max(len(names), 1))]
    rc = {
        "font.size": FONT_SIZE,
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans"],
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "svg.fonttype": "none",
        "svg.hashsalt": "dara-report",
        "axes.unicode_minus": True,
    }
    buffer = io.BytesIO()
    with _RENDER_LOCK, matplotlib.rc_context(rc):
        figure = Figure(figsize=(width_in, width_in * HEIGHT_RATIO), layout="constrained")
        FigureCanvasAgg(figure)
        rows = max(len(names), 1)
        grid = figure.add_gridspec(
            3, 1, height_ratios=[6.0, max(0.5, 0.42 * rows), 1.7], hspace=0.04
        )
        ax_main = figure.add_subplot(grid[0])
        ax_ticks = figure.add_subplot(grid[1], sharex=ax_main)
        ax_diff = figure.add_subplot(grid[2], sharex=ax_main)

        ax_main.plot(x, observed, color="black", lw=0.6, label="Observed", zorder=3)
        ax_main.plot(x, calculated, color="#d62728", lw=0.8, label="Calculated", zorder=4)
        ax_main.plot(x, background, color="#7f7f7f", lw=0.6, label="Background", zorder=2)
        ax_diff.plot(x, difference, color="#1f77b4", lw=0.6, label="Difference", zorder=3)
        ax_diff.axhline(0.0, color="#7f7f7f", lw=0.5, ls="--", zorder=1)

        for index, name in enumerate(names):
            positions = reflections.get(name)
            if positions is not None and positions.size:
                ax_ticks.plot(
                    positions, np.full(positions.shape, float(index)),
                    linestyle="none", marker="|", markersize=5, markeredgewidth=0.6,
                    color=colors[index], gid=f"ticks-{index}",
                )
        ax_ticks.set_ylim(rows - 0.5, -0.5)
        ax_ticks.set_yticks(list(range(len(names))))
        ax_ticks.set_yticklabels([labels[name] for name in names], fontsize=FONT_SIZE - 1)
        for tick, color in zip(ax_ticks.get_yticklabels(), colors):
            tick.set_color(color)
        ax_ticks.tick_params(axis="y", length=0)
        ax_ticks.tick_params(axis="x", labelbottom=False, length=0)
        for side in ("top", "right"):
            ax_ticks.spines[side].set_visible(False)

        for axis in (ax_main, ax_ticks, ax_diff):
            for start, end in spans:
                axis.axvspan(start, end, color="#d9d9d9", alpha=0.6, lw=0, zorder=0)
        ax_main.tick_params(axis="x", labelbottom=False)

        ax_main.set_xlim(low, high)
        ax_main.set_ylabel(
            "Intensity (counts)" if counts_like is not False else "Intensity (a.u.)"
        )
        ax_diff.set_ylabel("Difference")
        ax_diff.set_xlabel("2θ (°)")
        for axis in (ax_main, ax_diff):
            axis.margins(y=0.05)

        handles = [
            Line2D([], [], color="black", lw=0.6),
            Line2D([], [], color="#d62728", lw=0.8),
            Line2D([], [], color="#7f7f7f", lw=0.6),
            Line2D([], [], color="#1f77b4", lw=0.6),
        ]
        legend_labels = ["Observed", "Calculated", "Background", "Difference"]
        if spans:
            handles.append(Patch(facecolor="#d9d9d9", alpha=0.6, lw=0))
            legend_labels.append(EXCLUDED_LABEL)
        ax_main.legend(
            handles, legend_labels, loc="upper right", frameon=False, fontsize=FONT_SIZE - 1,
            handlelength=1.6,
        )

        if fmt == "png":
            figure.savefig(buffer, format="png", dpi=dpi, metadata={"Software": None})
        else:
            figure.savefig(buffer, format="svg", metadata={"Date": None, "Creator": "DARA"})
    return buffer.getvalue()
