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

export function buildRefinementView(plot, {
  selection = {}, showDiagnostics = true, showPhaseProfiles = true,
  showReflections = true, scale = 'linear', yRange = null,
} = {}) {
  const intensity = plot.data.filter(t => !['reflection', 'difference', 'diagnostic'].includes(t.meta))
    .flatMap(t => numbers(t.y)).filter(Number.isFinite);
  let maximum = 1;
  let minimum = 0;
  let smallestPositive = Infinity;
  for (const value of intensity) {
    maximum = Math.max(maximum, value);
    minimum = Math.min(minimum, value);
    if (value > 0) smallestPositive = Math.min(smallestPositive, value);
  }
  if (!Number.isFinite(smallestPositive)) smallestPositive = 1;
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
    if (trace.meta !== 'reflection' || !enabled) return { ...trace, visible };
    const row = rows.length;
    const position = scale === 'log'
      ? smallestPositive / (4 ** (row + 1))
      : minimum - maximum * 0.095 * (row + 1);
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
  const defaultRange = scale === 'log'
    ? [Math.log10(smallestPositive) - (rows.length + 0.5) * Math.log10(4), Math.log10(maximum) + 0.04]
    : [minimum - maximum * (rows.length ? 0.095 * (rows.length + 0.55) : 0.035), maximum * 1.05];
  const range = yRange || defaultRange;
  const yaxis = { ...plot.layout.yaxis, type: scale, range, autorange: false,
    // Hide only the upper panel's negative intensity ticks. Residuals retain
    // their signed values and automatic tick labels.
    tickmode: scale === 'log' ? 'auto' : 'array',
    tickvals: scale === 'log' ? undefined : positiveTicks(Math.min(...range), Math.max(...range)),
    ticktext: undefined,
  };
  const layout = { ...plot.layout, annotations, yaxis,
    xaxis: { ...plot.layout.xaxis, showticklabels: true, title: { text: '2θ [°]', standoff: 8 } },
  };
  return { data, layout, rows };
}
