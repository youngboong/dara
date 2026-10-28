// Pure helpers for selecting peaks on the refinement plot (click -> candidates drawer).

export const MAX_SELECTED_PEAKS = 10;
export const MISSING_SNAP_DEG = 0.3;
export const LOCAL_MAX_DEG = 0.15;

function numbersOf(values) {
  // Plotly 6 may encode numeric arrays as typed-array JSON ({dtype, bdata}).
  if (Array.isArray(values) || ArrayBuffer.isView(values)) return Array.from(values);
  if (!values?.bdata) return [];
  const bytes = Uint8Array.from(atob(values.bdata), c => c.charCodeAt(0));
  const type = { f8: Float64Array, f4: Float32Array, i4: Int32Array, i2: Int16Array,
    i1: Int8Array, u4: Uint32Array, u2: Uint16Array, u1: Uint8Array }[values.dtype];
  return type ? Array.from(new type(bytes.buffer)) : [];
}

const round2 = value => Math.round(value * 100) / 100;

function traceByName(plot, name) {
  return (plot?.data || []).find(trace => trace.name === name);
}

/**
 * 2θ of the clicked peak, or `null`. Clicking a "Missing peaks" marker uses its own x; clicking
 * "Observed" snaps to the nearest missing-peak marker within 0.3 deg, else to the local maximum of
 * the observed intensity within ±0.15 deg. Other traces are ignored.
 */
export function peakFromClick(plot, traceName, x) {
  if (!Number.isFinite(Number(x))) return null;
  const clicked = Number(x);
  if (traceName === 'Missing peaks') return round2(clicked);
  if (traceName !== 'Observed') return null;
  const missing = numbersOf(traceByName(plot, 'Missing peaks')?.x).filter(Number.isFinite);
  let nearest = null;
  for (const value of missing) {
    if (Math.abs(value - clicked) <= MISSING_SNAP_DEG && (nearest === null || Math.abs(value - clicked) < Math.abs(nearest - clicked))) {
      nearest = value;
    }
  }
  if (nearest !== null) return round2(nearest);
  const observed = traceByName(plot, 'Observed');
  const xs = numbersOf(observed?.x);
  const ys = numbersOf(observed?.y);
  let best = null;
  for (let i = 0; i < xs.length && i < ys.length; i += 1) {
    if (Math.abs(xs[i] - clicked) <= LOCAL_MAX_DEG && Number.isFinite(ys[i]) && (best === null || ys[i] > ys[best])) best = i;
  }
  return round2(best === null ? clicked : xs[best]);
}

/** Plain click replaces the selection, shift+click adds or removes (max 10); sorted ascending. */
export function updatePeakSelection(current, peak, { shift = false, max = MAX_SELECTED_PEAKS } = {}) {
  if (!Number.isFinite(peak)) return current || [];
  const list = current || [];
  if (!shift) return list.length === 1 && Math.abs(list[0] - peak) < 1e-9 ? [] : [peak];
  if (list.some(value => Math.abs(value - peak) < 1e-9)) return list.filter(value => Math.abs(value - peak) >= 1e-9);
  if (list.length >= max) return list;
  return [...list, peak].sort((a, b) => a - b);
}

/** Dashed vertical lines in both subplots for the selected peaks. */
export function peakShapes(peaks) {
  const line = { color: '#d4380d', width: 1.2, dash: 'dash' };
  return (peaks || []).flatMap(x => [
    { type: 'line', xref: 'x', yref: 'y domain', x0: x, x1: x, y0: 0, y1: 1, line, layer: 'above' },
    { type: 'line', xref: 'x2', yref: 'y2 domain', x0: x, x1: x, y0: 0, y1: 1, line, layer: 'above' },
  ]);
}

/**
 * Temporary stick preview as one scatter trace (RefinementPlotly registers scatter only):
 * a vertical segment per `[2θ, relative intensity]` stick, scaled to `peakHeight`.
 */
export function previewTrace(sticks, peakHeight, { floor = 0, name = 'Preview ticks' } = {}) {
  const x = [];
  const y = [];
  for (const stick of sticks || []) {
    const [tt, rel] = stick;
    if (!Number.isFinite(tt) || !Number.isFinite(rel)) continue;
    x.push(tt, tt, null);
    y.push(floor, Math.max(floor, (Math.max(rel, 0) / 100) * peakHeight), null);
  }
  return { type: 'scatter', mode: 'lines', name, x, y, connectgaps: false, hoverinfo: 'skip',
    line: { color: '#722ed1', width: 2 }, meta: 'preview' };
}

/** Largest finite y of the "Observed" trace; `1` when absent. */
export function observedPeak(plot) {
  const ys = numbersOf(traceByName(plot, 'Observed')?.y).filter(Number.isFinite);
  return ys.length ? Math.max(1, ...ys) : 1;
}

/** Unique phase legend groups in trace order. */
export function phaseGroupNames(plot) {
  const names = [];
  for (const trace of plot?.data || []) {
    if (trace.meta === 'phase-profile' && trace.legendgroup && !names.includes(trace.legendgroup)) names.push(trace.legendgroup);
  }
  return names;
}

/** Unique trace hints of one result (missing-peak hints plus trace_hints), keyed by reference. */
export function collectHints(diagnostics) {
  const hints = new Map();
  const add = hint => {
    if (!hint || !hint.phase) return;
    const entry = hints.get(hint.phase);
    if (!entry) hints.set(hint.phase, { ...hint, matched_peaks: [...(hint.matched_peaks || [])] });
    else for (const peak of hint.matched_peaks || []) if (!entry.matched_peaks.includes(peak)) entry.matched_peaks.push(peak);
  };
  for (const item of diagnostics?.missing_classified || []) add(item?.hint);
  for (const hint of diagnostics?.trace_hints || []) add(hint);
  return [...hints.values()];
}

/**
 * True while the server is still simulating reference patterns for trace hints in the
 * background (`peak_diagnostics.hints_pending` of the final or any candidate result).
 */
export function hintsPending(task) {
  if (!task || typeof task !== 'object') return false;
  const results = [task.final_result, ...(Array.isArray(task.all_results) ? task.all_results : [])];
  return results.some(result => result?.peak_diagnostics?.hints_pending === true);
}

/** "Trace Fe3O4 (Fd-3m) matches 35.42°, 43.05°". */
export function hintText(hint) {
  const peaks = (hint.matched_peaks || []).map(peak => `${Number(peak).toFixed(2)}°`);
  const symbol = hint.spacegroup_symbol ? ` (${hint.spacegroup_symbol})` : '';
  return `Trace ${hint.formula || hint.phase}${symbol}${peaks.length ? ` matches ${peaks.join(', ')}` : ''}`;
}
