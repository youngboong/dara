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

// Relayout payload for a right-drag zoom box. Ranges are in axis ("linear")
// coordinates, so log intensity axes get log10 values as Plotly expects.
export function boxZoomUpdate({ xRange, yRange = null }) {
  const sorted = range => [Math.min(...range), Math.max(...range)];
  const update = { 'xaxis.range': sorted(xRange), 'xaxis.autorange': false };
  if (yRange) Object.assign(update, { 'yaxis.range': sorted(yRange), 'yaxis.autorange': false });
  return update;
}

// Read a Plotly relayout event into the React view state. Returns only the
// keys that changed: { xRange } (array or null for a full reset) and/or { yRange }.
export function viewChangeFromRelayout(event) {
  const change = {};
  if (event['xaxis.autorange'] || event['xaxis2.autorange']) change.xRange = null;
  const x0 = event['xaxis.range[0]'] ?? event['xaxis2.range[0]'] ?? event['xaxis.range']?.[0];
  const x1 = event['xaxis.range[1]'] ?? event['xaxis2.range[1]'] ?? event['xaxis.range']?.[1];
  if (x0 !== undefined && x1 !== undefined) change.xRange = [Number(x0), Number(x1)];
  if (event['yaxis.autorange']) change.yRange = null;
  const y0 = event['yaxis.range[0]'] ?? event['yaxis.range']?.[0];
  const y1 = event['yaxis.range[1]'] ?? event['yaxis.range']?.[1];
  if (y0 !== undefined && y1 !== undefined) change.yRange = [Number(y0), Number(y1)];
  return change;
}
