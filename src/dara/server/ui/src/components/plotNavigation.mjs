// Pure helpers for XRD plot navigation (kept separate so node tests can import them).

function toArray(values) {
  if (Array.isArray(values) || ArrayBuffer.isView(values)) return Array.from(values);
  if (!values?.bdata) return null;
  const bytes = Uint8Array.from(atob(values.bdata), c => c.charCodeAt(0));
  const type = { f8: Float64Array, f4: Float32Array, i4: Int32Array, i2: Int16Array,
    i1: Int8Array, u4: Uint32Array, u2: Uint16Array, u1: Uint8Array }[values.dtype];
  return type ? Array.from(new type(bytes.buffer)) : null;
}

// Bragg's law: d = λ / (2 sin θ), with 2θ in degrees and λ in Å.
export function dSpacing(twoTheta, wavelength) {
  const s = Math.sin((Number(twoTheta) * Math.PI) / 360);
  return s > 0 && wavelength > 0 ? wavelength / (2 * s) : NaN;
}

const D_TEXT = 'd %{customdata:.4f} Å';

// Return copies of scatter traces whose hover also shows the d-spacing.
// Traces with per-point customdata arrays (e.g. hkl) get d appended; others get
// d as their customdata. Traces with hoverinfo 'none'/'skip' are left alone.
export function addDSpacingHover(traces, wavelength) {
  if (!(wavelength > 0)) return traces;
  return traces.map(trace => {
    if (trace.hoverinfo === 'none' || trace.hoverinfo === 'skip') return trace;
    const xs = toArray(trace.x);
    if (!xs || !xs.length) return trace;
    const d = xs.map(x => dSpacing(x, wavelength));
    const name = trace.name || '';
    const existing = toArray(trace.customdata);
    let customdata;
    let dField;
    if (existing && Array.isArray(existing[0])) {
      const n = existing[0].length;
      customdata = existing.map((row, i) => [...row, d[i]]);
      dField = `d %{customdata[${n}]:.4f} Å`;
    } else if (!existing) {
      customdata = d;
      dField = D_TEXT;
    } else {
      return trace;  // scalar customdata used for something else: leave untouched
    }
    const template = trace.hovertemplate
      ? (trace.hovertemplate.includes('%{x')
        ? trace.hovertemplate.replace(/(2θ\s*%\{x[^}]*\}°?)/, `$1<br>${dField}`)
        : `${dField}<br>${trace.hovertemplate}`)
      : `2θ %{x:.4f}°<br>${dField}<br>${name}: %{y:.5g}<extra></extra>`;
    return { ...trace, customdata, hovertemplate: template };
  });
}

// Mouse navigation of the XRD plots (RefinementPlotly): left-drag draws a zoom box (Plotly
// 'zoom'; a 2θ band where the intensity axis is fixed), Shift-, right- or middle-drag pans 2θ,
// the wheel zooms 2θ about the cursor by a fixed step per notch. All of it stays inside the
// measured 2θ range.
export const WHEEL_STEP = 1.25;
export const MIN_SPAN_FRACTION = 1e-3;

const sorted = range => [Math.min(...range), Math.max(...range)];

/** Finite `[lo, hi]` (sorted) or `null`. */
export function finiteRange(range) {
  if (!Array.isArray(range) || range.length !== 2) return null;
  const values = range.map(Number);
  return values.every(Number.isFinite) && values[0] !== values[1] ? sorted(values) : null;
}

/**
 * `range` moved and shrunk to lie inside `full` (both 2θ windows): a window wider than the data
 * becomes the whole range, one that sticks out is shifted back in. Spans below
 * `MIN_SPAN_FRACTION` of `full` are widened about their centre. `null` when `range` is unusable.
 */
export function clampRange(range, full) {
  const wanted = finiteRange(range);
  const bounds = finiteRange(full);
  if (!wanted) return null;
  if (!bounds) return wanted;
  const [lo, hi] = bounds;
  const total = hi - lo;
  let span = Math.max(wanted[1] - wanted[0], total * MIN_SPAN_FRACTION);
  if (span >= total) return [lo, hi];
  let start = (wanted[0] + wanted[1]) / 2 - span / 2;
  if (wanted[1] - wanted[0] >= total * MIN_SPAN_FRACTION) start = wanted[0];
  start = Math.min(Math.max(start, lo), hi - span);
  return [start, start + span];
}

/** Whether `range` already lies inside `full` (with a tiny tolerance for float noise). */
export function insideRange(range, full) {
  const wanted = finiteRange(range);
  const bounds = finiteRange(full);
  if (!wanted || !bounds) return true;
  const eps = (bounds[1] - bounds[0]) * 1e-9;
  return wanted[0] >= bounds[0] - eps && wanted[1] <= bounds[1] + eps;
}

/**
 * Wheel notches of a `wheel` event (positive = towards the user = zoom out). A mouse notch is
 * one step whatever its pixel delta (browsers report 100–120 px, less when the page is zoomed);
 * small trackpad deltas give fractional steps. At most three steps per event.
 */
export function wheelNotches({ deltaY = 0, deltaMode = 0 } = {}) {
  const value = Number(deltaY) || 0;
  if (!value) return 0;
  const pixels = deltaMode === 1 ? value * 33 : deltaMode === 2 ? value * 400 : value;
  const size = Math.abs(pixels);
  const notches = size >= 40 ? Math.max(1, Math.round(size / 100)) : size / 100;
  return Math.sign(pixels) * Math.min(3, notches);
}

/** 2θ window after `notches` wheel steps about `center` (zoom in for negative notches), clamped. */
export function wheelZoomRange(range, center, notches, full) {
  const current = finiteRange(range);
  if (!current || !notches) return current;
  const factor = WHEEL_STEP ** notches;
  const anchor = Number.isFinite(center) ? Math.min(Math.max(center, current[0]), current[1]) : (current[0] + current[1]) / 2;
  return clampRange([anchor - (anchor - current[0]) * factor, anchor + (current[1] - anchor) * factor], full);
}

/** 2θ window moved by `delta` degrees, kept inside `full` without changing its width. */
export function panRange(range, delta, full) {
  const current = finiteRange(range);
  if (!current) return null;
  return clampRange([current[0] + delta, current[1] + delta], full);
}

/** Relayout payload that sets the 2θ window of the main panel and (when present) the residual panel. */
export function xRangeUpdate(range, { xaxis2 = false } = {}) {
  const update = { 'xaxis.range': [...range], 'xaxis.autorange': false };
  if (xaxis2) Object.assign(update, { 'xaxis2.range': [...range], 'xaxis2.autorange': false });
  return update;
}

// Read a Plotly relayout event into the React view state. Returns only the
// keys that changed: { xRange } (array or null for a full reset) and/or { yRange }.
// With `full` (the measured 2θ range) the 2θ window is clamped to it.
export function viewChangeFromRelayout(event, { full = null } = {}) {
  const change = {};
  if (event['xaxis.autorange'] || event['xaxis2.autorange']) change.xRange = null;
  const x0 = event['xaxis.range[0]'] ?? event['xaxis2.range[0]'] ?? event['xaxis.range']?.[0] ?? event['xaxis2.range']?.[0];
  const x1 = event['xaxis.range[1]'] ?? event['xaxis2.range[1]'] ?? event['xaxis.range']?.[1] ?? event['xaxis2.range']?.[1];
  if (x0 !== undefined && x1 !== undefined) {
    change.xRange = full ? clampRange([Number(x0), Number(x1)], full) : [Number(x0), Number(x1)];
  }
  if (event['yaxis.autorange']) change.yRange = null;
  const y0 = event['yaxis.range[0]'] ?? event['yaxis.range']?.[0];
  const y1 = event['yaxis.range[1]'] ?? event['yaxis.range']?.[1];
  if (y0 !== undefined && y1 !== undefined) change.yRange = [Number(y0), Number(y1)];
  return change;
}
