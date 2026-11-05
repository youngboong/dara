import { addDSpacingHover, clampRange, finiteRange } from './plotNavigation.mjs';

// Keep legend selection in React state: Plotly's internal visibility is lost
// whenever a toolbar action supplies a fresh data array.
export function legendKey(trace, index) {
  return trace.legendgroup || `trace:${index}`;
}

export function isTraceSelected(trace, index, selection) {
  const explicit = selection[legendKey(trace, index)];
  if (explicit !== undefined) return explicit;
  if (trace.meta === 'phase-profile' || trace.meta === 'reflection') return false;
  return trace.visible !== false && trace.visible !== 'legendonly';
}

function numbers(values) {
  // Plotly 6 may encode numeric arrays as typed-array JSON.
  if (Array.isArray(values) || ArrayBuffer.isView(values)) return Array.from(values);
  if (!values?.bdata) return [];
  const bytes = Uint8Array.from(atob(values.bdata), c => c.charCodeAt(0));
  const type = { f8: Float64Array, f4: Float32Array, i4: Int32Array, i2: Int16Array,
    i1: Int8Array, u4: Uint32Array, u2: Uint16Array, u1: Uint8Array }[values.dtype];
  return type ? Array.from(new type(bytes.buffer)) : [];
}

function positiveTicks(lower, upper) {
  const start = Math.max(0, lower);
  const rough = Math.max(upper - start, 1e-9) / 5;
  const power = 10 ** Math.floor(Math.log10(rough));
  const factor = [1, 2, 2.5, 5, 10].find(v => v * power >= rough) || 10;
  const step = factor * power;
  const ticks = [];
  for (let value = Math.ceil(start / step) * step; value <= upper && ticks.length < 30; value += step) {
    ticks.push(Number(value.toPrecision(12)));
  }
  return ticks;
}

const withDCache = new WeakMap();

// Hover shows d-spacing when the server sent the Kα1 wavelength (layout.meta).
function withDSpacing(plot) {
  const wavelength = Number(plot.layout?.meta?.wavelength_angstrom);
  if (!(wavelength > 0)) return plot;
  if (!withDCache.has(plot)) withDCache.set(plot, { ...plot, data: addDSpacingHover(plot.data, wavelength) });
  return withDCache.get(plot);
}

function cloneLayout(layout) {
  // Plotly writes zoom ranges back into the layout objects it is given. The
  // cached server layout is shared between renders (and xaxis2 "matches" x),
  // so a shallow copy let an old 2θ zoom survive "Reset zoom".
  return typeof structuredClone === 'function' ? structuredClone(layout) : JSON.parse(JSON.stringify(layout));
}

function intensityStats(plot, isShown, xRange) {
  const [lo, hi] = xRange ? [Math.min(...xRange), Math.max(...xRange)] : [-Infinity, Infinity];
  let maximum = -Infinity;
  let minimum = Infinity;
  let smallestPositive = Infinity;
  plot.data.forEach((trace, index) => {
    if (['reflection', 'difference', 'diagnostic'].includes(trace.meta) || !isShown(trace, index)) return;
    const xs = numbers(trace.x);
    numbers(trace.y).forEach((value, i) => {
      if (!Number.isFinite(value) || xs[i] < lo || xs[i] > hi) return;
      maximum = Math.max(maximum, value);
      minimum = Math.min(minimum, value);
      if (value > 0) smallestPositive = Math.min(smallestPositive, value);
    });
  });
  return { maximum, minimum, smallestPositive };
}

/**
 * Plot title with the pattern name: the server titles the figure after BGMN's working file
 * (`xrd_data.xy (Rwp=2.96%)`), so the name part is replaced and the Rwp kept.
 */
export function patternTitle(title, name) {
  const text = typeof title === 'string' ? title : title?.text;
  if (!name || typeof text !== 'string') return title;
  const rwp = / \(Rwp=[^)]*\)$/.exec(text);
  const next = `${name}${rwp ? rwp[0] : ''}`;
  return typeof title === 'string' ? next : { ...title, text: next };
}

/** Largest share of the measured 2θ range that "Use current view" may exclude. */
export const MAX_EXCLUDED_SHARE = 0.9;

/**
 * 2θ window offered as an excluded interval ("Use current view"): only a real zoom, never the
 * whole pattern. `null` without a zoom or when the window covers more than 90 % of the
 * measured range (excluding it would leave almost nothing to fit).
 */
export function exclusionViewRange(xRange, fullRange) {
  const view = finiteRange(xRange);
  if (!view) return null;
  const [lo, hi] = [Math.min(...view), Math.max(...view)];
  const full = finiteRange(fullRange);
  if (full && hi - lo > MAX_EXCLUDED_SHARE * Math.abs(full[1] - full[0])) return null;
  return [lo, hi];
}

// Below this plot width the legend column gets smaller text and only the room it needs
// (the server reserves 290 px, which leaves too little for the pattern at 1280 px).
export const COMPACT_LEGEND_WIDTH = 1400;
// Below this plot width the legend is drawn in rows under the plot instead of beside it.
export const LEGEND_BELOW_WIDTH = 1200;
// Legend rows under a narrow plot: one row is about 22 px at font size 10, and an entry about
// 40 px (symbol and gap) plus 6 px per character.
export const LEGEND_ROW_PX = 22;
const LEGEND_FONT_SIZE = 10;
const LEGEND_ENTRY_PX = 40;
const LEGEND_CHAR_PX = 6;
const LEGEND_GAP_PX = 10; // between the 2θ title and the first legend row

/** Text of a legend entry without HTML tags (`TiFe<sub>2</sub>O<sub>5</sub>` -> `TiFe2O5`). */
const legendText = name => String(name ?? '').replace(/<[^>]*>/g, '');

/**
 * Rows a horizontal legend needs for `data` in `width` px: the entries Plotly shows (a trace with
 * `showlegend: false` has none, a legend group appears once) packed left to right.
 */
export function legendRowCount(data = [], width = 0) {
  const seen = new Set();
  const widths = [];
  (data || []).forEach(trace => {
    if (trace?.showlegend === false) return;
    if (trace?.legendgroup) {
      if (seen.has(trace.legendgroup)) return;
      seen.add(trace.legendgroup);
    }
    widths.push(LEGEND_ENTRY_PX + LEGEND_CHAR_PX * legendText(trace?.name).length);
  });
  if (!widths.length) return 0;
  const room = Math.max(Number(width) || 0, 200);
  let rows = 1;
  let used = 0;
  widths.forEach(entry => {
    if (used > 0 && used + entry > room) { rows += 1; used = 0; }
    used += entry;
  });
  return rows;
}

export function buildRefinementView(plot, {
  selection = {}, showDiagnostics = true, showPhaseProfiles = true,
  showReflections = true, scale = 'linear', yRange = null, xRange = null, fitY = true, plotWidth = null, title = null,
} = {}) {
  plot = withDSpacing(plot);
  const baseLayout = cloneLayout(plot.layout);
  // The plot div sets the size: a fixed server height would override autosize and
  // push the Obs − calc panel under the content below (or leave an empty band).
  delete baseLayout.height;
  delete baseLayout.width;
  baseLayout.autosize = true;
  const fullX = finiteRange(baseLayout.xaxis?.range);
  // The 2θ window never leaves the measured range.
  const xWindow = xRange ? (clampRange(xRange, fullX) || null) : null;
  const shown = (trace, index) => {
    if (!isTraceSelected(trace, index, selection)) return false;
    return trace.meta !== 'phase-profile' || showPhaseProfiles;
  };
  // A user intensity range (box zoom with Auto-Y off) is the data window: the marker band and
  // the Bragg rows are added below it, so they always stay inside the main panel.
  const userY = finiteRange(yRange);
  // Auto-Y scales the intensity axis to the shown traces in the visible 2θ window (the whole
  // pattern when not zoomed), so a high background does not leave the lower panel empty.
  // With Auto-Y off the axis starts at zero.
  let fitted = Boolean(fitY);
  let stats = fitted ? intensityStats(plot, shown, xWindow || null) : intensityStats(plot, () => true, null);
  if (!Number.isFinite(stats.maximum)) {
    fitted = false;
    stats = intensityStats(plot, () => true, null);
  }
  let maximum = Number.isFinite(stats.maximum) ? stats.maximum : 1;
  let minimum = fitted ? stats.minimum : Math.min(0, stats.minimum);
  if (!fitted) maximum = Math.max(1, maximum);
  let smallestPositive = stats.smallestPositive;
  if (!Number.isFinite(minimum)) minimum = 0;
  if (!Number.isFinite(smallestPositive)) smallestPositive = 1;
  if (userY) {
    if (scale === 'log') {
      smallestPositive = 10 ** userY[0];
      maximum = 10 ** userY[1];
    } else {
      [minimum, maximum] = userY;
    }
  }
  const span = Math.max(maximum - minimum, 1e-9);
  const unit = fitted || userY ? span : maximum;
  // Peak markers get their own band between the pattern and the Bragg rows, so the
  // observed points and the axis edge never cover them.
  const markersShown = showDiagnostics && plot.data.some((trace, index) =>
    trace.meta === 'diagnostic' && isTraceSelected(trace, index, selection) && numbers(trace.x).length);
  const markerBand = markersShown ? 0.08 : 0;
  const logMarkerBand = markersShown ? 4 : 1;
  const rows = [];
  const annotations = [];
  const data = plot.data.map((trace, index) => {
    const selected = isTraceSelected(trace, index, selection);
    let enabled = selected;
    if (trace.meta === 'diagnostic') enabled = selected && showDiagnostics;
    // Keep the phase legend clickable even when profile display is disabled.
    if (trace.meta === 'phase-profile') enabled = selected && showPhaseProfiles;
    if (trace.meta === 'reflection') enabled = selected && showReflections;
    const visible = enabled ? true : 'legendonly';
    if (trace.meta === 'diagnostic' && enabled) {
      // Peak markers arrive at intensity 0. Centre them in their band under the visible
      // minimum, so a zoom that rescales the intensity axis keeps them in view.
      const y = scale === 'log' ? smallestPositive / 2 : minimum - unit * markerBand / 2;
      return { ...trace, visible, cliponaxis: false, x: numbers(trace.x), y: numbers(trace.x).map(() => y) };
    }
    if (trace.meta !== 'reflection' || !enabled) return { ...trace, visible };
    const row = rows.length;
    const position = scale === 'log'
      ? smallestPositive / (logMarkerBand * 4 ** (row + 1))
      : minimum - unit * (markerBand + 0.095 * (row + 1));
    rows.push(trace.legendgroup);
    const label = trace.name.replace(/ \([^)]* wt%\)$/, '');
    annotations.push({
      x: 0, xref: 'paper', xanchor: 'right', xshift: -10,
      y: scale === 'log' ? Math.log10(position) : position, yref: 'y',
      text: label, showarrow: false, align: 'right',
      font: { size: 11, color: trace.marker.color },
    });
    return { ...trace, visible, x: numbers(trace.x), y: numbers(trace.x).map(() => position) };
  });
  const top = userY ? 0 : 1;
  const range = scale === 'log'
    ? [Math.log10(smallestPositive / logMarkerBand) - (rows.length + 0.5) * Math.log10(4), Math.log10(maximum) + 0.04 * top]
    : [minimum - unit * (markerBand + (rows.length ? 0.095 * (rows.length + 0.55) : 0.035)), maximum + unit * 0.05 * top];
  const yaxis = { ...baseLayout.yaxis, type: scale, range, autorange: false,
    // While the intensity axis follows the 2θ window, box zoom and pan act on 2θ only.
    fixedrange: Boolean(fitY && !userY),
    // Ticks cover the data window only (not the marker band and Bragg rows below it) and
    // hide the upper panel's negative intensities. Residuals keep their signed values.
    tickmode: scale === 'log' ? 'auto' : 'array',
    tickvals: scale === 'log' ? undefined : positiveTicks(minimum, range[1]),
    ticktext: undefined,
  };
  const xView = xWindow || fullX;
  // The 2θ title is drawn once, under the Obs − calc panel; the main panel keeps its tick
  // labels, which leaves a clear gap between the panels at small heights.
  const xaxis = { ...baseLayout.xaxis, showticklabels: true, title: { text: '' } };
  if (xView) Object.assign(xaxis, { range: [...xView], autorange: false });
  const layout = { ...baseLayout, annotations, yaxis, xaxis };
  if (title) layout.title = patternTitle(layout.title, title);
  if (layout.xaxis2 && xView) Object.assign(layout.xaxis2, { range: [...xView], autorange: false });
  // The residual intensity never zooms: a box there is a 2θ band.
  if (layout.yaxis2) layout.yaxis2 = { ...layout.yaxis2, fixedrange: true };
  const narrow = Number.isFinite(plotWidth) && plotWidth > 0;
  if (narrow && plotWidth < LEGEND_BELOW_WIDTH) {
    // Narrow plot (1366 px screen with the Add phase drawer open): a legend column would take a
    // third of the 2θ axis, so the legend goes in rows under the plot.
    // The bottom margin grows by the legend rows, and the legend sits at the bottom of the figure
    // (container coordinates), so it stays under the 2θ title and is never clipped.
    const margin = layout.margin || {};
    const rows = legendRowCount(data, plotWidth - (Number(margin.l) || 0) - 24);
    const bottom = Number.isFinite(Number(margin.b)) ? Number(margin.b) : 65;
    layout.margin = { ...margin, r: 24, b: bottom + LEGEND_GAP_PX + rows * LEGEND_ROW_PX };
    layout.legend = { ...(layout.legend || {}), orientation: 'h', xref: 'paper', x: 0, xanchor: 'left',
      yref: 'container', y: 0, yanchor: 'bottom', font: { ...(layout.legend?.font || {}), size: LEGEND_FONT_SIZE } };
  } else if (narrow && plotWidth < COMPACT_LEGEND_WIDTH) {
    // Plotly widens the right margin to the legend's own width when it needs more.
    layout.margin = { ...(layout.margin || {}), r: 24 };
    layout.legend = { ...(layout.legend || {}), font: { ...(layout.legend?.font || {}), size: 10 }, itemwidth: 30 };
  }
  return { data, layout, rows, fullXRange: fullX || null };
}
