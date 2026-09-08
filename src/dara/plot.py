from __future__ import annotations

from typing import TYPE_CHECKING
from html import escape
import re

import numpy as np
import plotly.graph_objects as go

if TYPE_CHECKING:
    from dara.result import RefinementResult


def phase_display_name(reference: str) -> str:
    """Readable phase names, with the database reference retained in hover."""
    match = re.match(r"^(.+?)_(\d+)_\(", reference)
    if not match:
        return escape(reference)
    formula, group = match.groups()
    common = {
        ("Al2O3", "167"): "Corundum", ("CaCO3", "62"): "Aragonite",
        ("CaCO3", "167"): "Calcite", ("CaF2", "225"): "Fluorite",
        ("Cr2O3", "167"): "Eskolaite", ("Fe2O3", "167"): "Hematite",
        ("Fe3O4", "227"): "Magnetite", ("Fe", "225"): "Austenite (γ-Fe)",
        ("Fe", "229"): "Ferrite (α-Fe)", ("In2O3", "206"): "Bixbyite-type",
        ("Mg(OH)2", "164"): "Brucite", ("TiO2", "61"): "Brookite",
        ("TiO2", "136"): "Rutile", ("TiO2", "141"): "Anatase",
        ("ZnO", "186"): "Zincite",
    }.get((formula, group))
    formula = re.sub(r"(\d+(?:\.\d+)?)", r"<sub>\1</sub>", escape(formula))
    if common:
        return f"{common} · {formula}"
    # Unknown common names still show formula and crystallographic identity.
    from pymatgen.symmetry.groups import SpaceGroup
    try:
        symbol = SpaceGroup.from_int_number(int(group)).symbol
        symbol = re.sub(r"_(\d)", r"<sub>\1</sub>", escape(symbol))
        return f"{formula} · {symbol}"
    except ValueError:
        return formula


def peaks_in_fit_range(peaks, angles):
    """Keep only finite diagnostic positions inside this refinement's range."""
    if peaks is None:
        return None
    values = np.asarray(peaks, dtype=float).reshape(-1, 2)
    angles = np.asarray(angles, dtype=float)
    angles = angles[np.isfinite(angles)]
    if not angles.size:
        return values[:0]
    return values[
        np.isfinite(values[:, 0])
        & (values[:, 0] >= angles.min())
        & (values[:, 0] <= angles.max())
    ]


def visualize(
    result: RefinementResult,
    diff_offset: bool = False,
    missing_peaks: list[list[float]] | np.ndarray | None = None,
    extra_peaks: list[list[float]] | np.ndarray | None = None,
    intensity_mismatch_peaks: list[list[float]] | np.ndarray | None = None,
    phase_colors: dict[str, str] | None = None,
):
    """Visualize the result from the refinement. It uses plotly as the backend engine.

    Args:
        intensity_mismatch_peaks: optional (N, 2) array of [2theta, 0.0] markers
            for matched peaks whose calculated height/area deviates from
            observed; drawn the same way as ``missing_peaks``/``extra_peaks``
            when provided.
    """
    colormap = [
        "#1f77b4",
        "#ff7f0e",
        "#2ca02c",
        "#d62728",
        "#9467bd",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
        "#bcbd22",
        "#17becf",
    ]

    plot_data = result.plot_data
    missing_peaks = peaks_in_fit_range(missing_peaks, plot_data.x)
    extra_peaks = peaks_in_fit_range(extra_peaks, plot_data.x)
    intensity_mismatch_peaks = peaks_in_fit_range(intensity_mismatch_peaks, plot_data.x)

    # Create a Plotly figure with size 800x600
    fig = go.Figure()

    # Adding scatter plot for observed data
    fig.add_trace(
        go.Scatter(
            x=plot_data.x,
            y=plot_data.y_obs,
            mode="markers",
            marker=dict(color="blue", size=3, symbol="cross-thin-open"),
            name="Observed",
        )
    )

    # Adding line plot for calculated data
    fig.add_trace(
        go.Scatter(
            x=plot_data.x,
            y=plot_data.y_calc,
            mode="lines",
            line=dict(color="green", width=2),
            name="Calculated",
        )
    )

    # Adding line plot for background
    fig.add_trace(
        go.Scatter(
            x=plot_data.x,
            y=plot_data.y_bkg,
            mode="lines",
            line=dict(color="#FF7F7F", width=2),
            name="Background",
            opacity=0.5,
        )
    )

    diff = np.array(plot_data.y_obs) - np.array(plot_data.y_calc)
    # Difference is always kept on its own scale. ``diff_offset`` remains in
    # the public signature for compatibility with older Python callers.
    fig.add_trace(
        go.Scatter(
            x=plot_data.x,
            y=diff,
            mode="lines",
            line=dict(color="#808080", width=1),
            name="Difference",
            opacity=0.9,
            xaxis="x2",
            yaxis="y2",
            meta="difference",
            hovertemplate="2θ %{x:.4f}°<br>Obs − calc: %{y:.5g}<extra>Difference</extra>",
        )
    )

    # if there is no phase weight, it will return an empty dictionary (not shown in the legend)
    try:
        weight_fractions = result.get_phase_weights()
    except (TypeError, ValueError, ZeroDivisionError):
        weight_fractions = {}

    peak_data = result.peak_data
    max_y = max(np.array(result.plot_data.y_obs) + np.array(result.plot_data.y_bkg))
    min_y_diff = min(
        np.array(result.plot_data.y_obs) - np.array(result.plot_data.y_calc)
    )
    # Adding dashed lines for phases
    for i, (phase_name, phase) in enumerate(plot_data.structs.items()):
        # add area under the curve between the curve and the plot_data["y_bkg"]
        color = (phase_colors or {}).get(phase_name, colormap[i % len(colormap)])

        label = phase_display_name(phase_name)
        name = (f"{label} ({weight_fractions[phase_name] * 100:.2f} wt%)"
                if phase_name in weight_fractions else label)
        fig.add_trace(
            go.Scatter(
                x=plot_data.x,
                y=plot_data.y_bkg,
                mode="lines",
                line=dict(color=color, width=0),
                fill=None,
                showlegend=False,
                hoverinfo="none",
                legendgroup=phase_name,
                meta="phase-profile",
                visible="legendonly",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=plot_data.x,
                y=np.array(phase) + np.array(plot_data.y_bkg),
                mode="lines",
                line=dict(color=color, width=1.5),
                fill="tonexty",
                name=name,
                visible="legendonly",
                legendgroup=phase_name,
                meta="phase-profile",
                hovertemplate=("2θ %{x:.4f}°<br>Phase + background: %{y:.5g}"
                               f"<br>{escape(phase_name)}<extra>{label}</extra>"),
            )
        )
        refl = peak_data[peak_data["phase"] == phase_name]["2theta"]
        phase_peaks = peak_data[peak_data["phase"] == phase_name]
        intensity = phase_peaks["intensity"]
        reflection_y = (i + 1) * -max_y * 0.1 + min_y_diff
        if {"h", "k", "l"}.issubset(phase_peaks.columns):
            hkl = list(zip(phase_peaks["h"], phase_peaks["k"], phase_peaks["l"]))
        else:
            hkl = [("?", "?", "?")] * len(phase_peaks)
        fig.add_trace(
            go.Scatter(
                x=refl,
                y=np.ones(len(refl)) * reflection_y,
                mode="markers",
                marker={
                    "symbol": 142,
                    "size": 5,
                    "color": color,
                },
                name=name,
                legendgroup=phase_name,
                showlegend=False,
                visible="legendonly",
                meta="reflection",
                customdata=[[h, k, l, value] for (h, k, l), value in zip(hkl, intensity)],
                hovertemplate=(
                    "2θ %{x:.4f}°<br>hkl (%{customdata[0]} %{customdata[1]} %{customdata[2]})"
                    "<br>Relative reflection intensity: %{customdata[3]:.4g}"
                    f"<br>{escape(phase_name)}<extra>{label}</extra>"
                ),
            )
        )

    if missing_peaks is not None:
        missing_peaks = np.array(missing_peaks).reshape(-1, 2)
        fig.add_trace(
            go.Scatter(
                x=missing_peaks[:, 0],
                y=np.zeros_like(missing_peaks[:, 0]),
                mode="markers",
                marker=dict(color="#f9726a", symbol=53, size=10, opacity=0.8),
                name="Missing peaks",
                showlegend=True,
                visible=True,
                meta="diagnostic",
                text=[f"{x:.2f}, {y:.2f}" for x, y in missing_peaks],
                hovertemplate="2θ %{x:.4f}°<br>Observed peak not explained<extra>Missing peak</extra>",
            )
        )

    if extra_peaks is not None:
        extra_peaks = np.array(extra_peaks).reshape(-1, 2)
        fig.add_trace(
            go.Scatter(
                x=extra_peaks[:, 0],
                y=np.zeros_like(extra_peaks[:, 0]),
                mode="markers",
                marker=dict(color="#335da0", symbol=53, size=10, opacity=0.8),
                name="Extra peaks",
                showlegend=True,
                visible=True,
                meta="diagnostic",
                text=[f"{x:.2f}, {y:.2f}" for x, y in extra_peaks],
                hovertemplate="2θ %{x:.4f}°<br>Calculated peak not observed<extra>Extra peak</extra>",
            )
        )

    if intensity_mismatch_peaks is not None:
        intensity_mismatch_peaks = np.array(intensity_mismatch_peaks).reshape(-1, 2)
        fig.add_trace(
            go.Scatter(
                x=intensity_mismatch_peaks[:, 0],
                y=np.zeros_like(intensity_mismatch_peaks[:, 0]),
                mode="markers",
                marker=dict(color="#FFD700", symbol=53, size=10, opacity=0.8),
                name="Intensity mismatch",
                showlegend=True,
                visible=True,
                meta="diagnostic",
                text=[f"{x:.2f}, {y:.2f}" for x, y in intensity_mismatch_peaks],
                hovertemplate="2θ %{x:.4f}°<br>Matched position, mismatched intensity<extra>Intensity mismatch</extra>",
            )
        )

    title = f"{result.lst_data.pattern_name} (Rwp={result.lst_data.rwp:.2f}%)"

    # Updating layout with titles and labels
    fig.update_layout(
        autosize=True,
        height=760,
        margin=dict(l=155, r=290, t=65, b=65),
        hovermode="closest",
        dragmode="zoom",
        xaxis=dict(
            range=[min(plot_data.x), max(plot_data.x)],
            domain=[0, 1],
            anchor="y",
            showline=True,
            linewidth=1,
            linecolor="black",
            mirror=True,
            showticklabels=True,
            title=dict(text="2θ [°]", standoff=8),
        ),
        title=title,
        yaxis_title="Intensity",
        legend_title="",
        font=dict(family="Arial, sans-serif", color="RebeccaPurple"),
        plot_bgcolor="white",
        yaxis=dict(domain=[0.36, 1], showline=True, linewidth=1,
                   linecolor="black", mirror=True, title="Intensity",
                   tickmode="array", tickvals=np.linspace(0, max_y, 6).tolist()),
        xaxis2=dict(range=[min(plot_data.x), max(plot_data.x)], domain=[0, 1],
                    anchor="y2", matches="x", title="2θ [°]", showline=True,
                    linewidth=1, linecolor="black", mirror=True),
        yaxis2=dict(domain=[0, 0.20], title="Obs − calc", showline=True,
                    linewidth=1, linecolor="black", mirror=True, zeroline=True,
                    zerolinecolor="#888"),
        legend=dict(orientation="v", x=1.02, xanchor="left", y=1,
                    yanchor="top", groupclick="togglegroup"),
        legend_tracegroupgap=1,
    )

    fig.add_shape(type="line", x0=min(plot_data.x), x1=max(plot_data.x),
                  y0=0, y1=0, xref="x2", yref="y2",
                  line=dict(color="#888", width=1))

    # add tick
    fig.update_xaxes(ticks="outside", tickwidth=1, tickcolor="black", ticklen=10)
    fig.update_yaxes(ticks="outside", tickwidth=1, tickcolor="black", ticklen=10)

    return fig
