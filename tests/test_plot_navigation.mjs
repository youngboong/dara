import assert from 'node:assert/strict';
import { buildRefinementView } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';
import { boxZoomUpdate, viewChangeFromRelayout } from '../src/dara/server/ui/src/components/plotNavigation.mjs';

const x = Array.from({ length: 101 }, (_, i) => 10 + i);
const y = x.map(t => (t === 30 ? 1000 : t === 70 ? 60 : 10));
const plot = {
  layout: {
    xaxis: { range: [10, 110] }, yaxis: {},
    xaxis2: { range: [10, 110], matches: 'x' }, yaxis2: {},
  },
  data: [
    { name: 'Observed', x, y },
    { name: 'A', meta: 'reflection', legendgroup: 'A', x: [30, 70], y: [-1, -1], marker: { color: 'red' } },
  ],
};

// Reset bug: Plotly writes zoom ranges into the layout objects it is handed.
// Every view must get its own copy, so a stale 2θ zoom cannot survive a reset.
const zoomed = buildRefinementView(plot, { xRange: [60, 80] });
zoomed.layout.xaxis2.range = [60, 80];
zoomed.layout.xaxis.range[0] = 61;
assert.deepEqual(plot.layout.xaxis2.range, [10, 110]);
assert.deepEqual(plot.layout.xaxis.range, [10, 110]);
const reset = buildRefinementView(plot);
assert.deepEqual(reset.layout.xaxis.range, [10, 110]);
assert.deepEqual(reset.layout.xaxis2.range, [10, 110]);

// The 2θ window drives both panels.
const windowed = buildRefinementView(plot, { xRange: [80, 60] });
assert.deepEqual(windowed.layout.xaxis.range, [60, 80]);
assert.deepEqual(windowed.layout.xaxis2.range, [60, 80]);

// Fit intensity to view: the weak 60-count peak fills the panel instead of the
// 1000-count peak outside the window; the intensity axis follows 2θ only.
assert(windowed.layout.yaxis.range[1] < 100, windowed.layout.yaxis.range);
assert(reset.layout.yaxis.range[1] > 1000);
assert.equal(windowed.layout.yaxis.fixedrange, true);
const unfitted = buildRefinementView(plot, { xRange: [60, 80], fitY: false });
assert(unfitted.layout.yaxis.range[1] > 1000);
assert.equal(unfitted.layout.yaxis.fixedrange, false);
// An explicit intensity zoom wins and frees the axis for panning.
const manual = buildRefinementView(plot, { xRange: [60, 80], yRange: [0, 500] });
assert.deepEqual(manual.layout.yaxis.range, [0, 500]);
assert.equal(manual.layout.yaxis.fixedrange, false);
// Bragg rows stay just below the fitted window.
const rows = buildRefinementView(plot, { xRange: [60, 80], selection: { A: true } });
assert(rows.data[1].y[0] < 10 && rows.data[1].y[0] > -50, rows.data[1].y[0]);

// Right-drag box -> relayout payload (sorted ranges, y only in the main panel).
assert.deepEqual(boxZoomUpdate({ xRange: [40, 30], yRange: [5, 1] }),
  { 'xaxis.range': [30, 40], 'xaxis.autorange': false, 'yaxis.range': [1, 5], 'yaxis.autorange': false });
assert.deepEqual(boxZoomUpdate({ xRange: [30, 40] }), { 'xaxis.range': [30, 40], 'xaxis.autorange': false });

// Relayout events -> view state.
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.range[0]': 20, 'xaxis.range[1]': 25 }), { xRange: [20, 25] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis2.range[0]': 20, 'xaxis2.range[1]': 25 }), { xRange: [20, 25] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.range': [1, 2], 'yaxis.range': [3, 4] }), { xRange: [1, 2], yRange: [3, 4] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.autorange': true, 'yaxis.autorange': true }), { xRange: null, yRange: null });
assert.deepEqual(viewChangeFromRelayout({ dragmode: 'pan' }), {});
console.log('Reset clears 2θ zoom, fit-to-view intensity, box zoom payload and relayout mapping passed.');

// d-spacing on hover (Bragg's law, Cu Kα1).
import { addDSpacingHover, dSpacing } from '../src/dara/server/ui/src/components/plotNavigation.mjs';
import { comparisonTraces } from '../src/dara/server/ui/src/components/patternComparison.mjs';
const cu = 1.540598;
assert(Math.abs(dSpacing(28.443, cu) - 3.1355) < 1e-3); // Si (111)
const hovered = addDSpacingHover([
  { name: 'Observed', x: [20, 40], y: [1, 2] },
  { name: 'Hkl', x: [30], y: [0], customdata: [[1, 1, 1, 50]], hovertemplate: '2θ %{x:.4f}°<br>hkl (%{customdata[0]})' },
  { name: 'Diff', x: [20], y: [0], hovertemplate: '2θ %{x:.4f}°<br>Obs − calc: %{y:.5g}' },
  { name: 'Hidden', x: [20], y: [0], hoverinfo: 'none' },
], cu);
assert.equal(hovered[0].customdata.length, 2);
assert(hovered[0].hovertemplate.includes('d %{customdata:.4f} Å'));
assert.deepEqual(hovered[1].customdata[0].slice(0, 4), [1, 1, 1, 50]);
assert(hovered[1].hovertemplate.includes('d %{customdata[4]:.4f} Å'));
assert(hovered[2].hovertemplate.startsWith('2θ %{x:.4f}°<br>d %{customdata:.4f} Å'));
assert.equal(hovered[3].customdata, undefined);
assert.equal(addDSpacingHover(hovered, 0), hovered); // unknown wavelength: unchanged
const withMeta = buildRefinementView({ ...plot, layout: { ...plot.layout, meta: { wavelength_angstrom: cu } } });
assert(withMeta.data[0].hovertemplate.includes('d %{customdata'));
const traces = comparisonTraces([{ task_id: 1, name: 'a', angles: [28.443], intensities: [5], wavelength_angstrom: cu },
  { task_id: 2, name: 'b', angles: [28.443], intensities: [5] }]);
assert(Math.abs(traces[0].customdata[0][1] - 3.1355) < 1e-3);
assert.deepEqual(traces[1].customdata, [5]); // no wavelength: unchanged behaviour
console.log('d-spacing hover passed.');
