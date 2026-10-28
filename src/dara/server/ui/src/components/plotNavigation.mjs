// Pure helpers for XRD plot navigation (kept separate so node tests can import them).

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
