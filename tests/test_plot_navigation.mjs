import assert from 'node:assert/strict';
import { buildRefinementView } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';
import {
  clampRange, insideRange, panRange, viewChangeFromRelayout, wheelNotches, wheelZoomRange, xRangeUpdate, WHEEL_STEP,
} from '../src/dara/server/ui/src/components/plotNavigation.mjs';

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
// An explicit intensity zoom wins and frees the axis: it is the data window, shown in full.
const manual = buildRefinementView(plot, { xRange: [60, 80], yRange: [0, 500] });
assert.equal(manual.layout.yaxis.range[1], 500);
assert(manual.layout.yaxis.range[0] <= 0);
assert.equal(manual.layout.yaxis.fixedrange, false);
// The residual intensity never zooms, the 2θ window never leaves the data.
assert.equal(manual.layout.yaxis2.fixedrange, true);
assert.deepEqual(buildRefinementView(plot, { xRange: [0, 50] }).layout.xaxis.range, [10, 60]);
assert.deepEqual(buildRefinementView(plot, { xRange: [-50, 300] }).layout.xaxis.range, [10, 110]);
// One 2θ title, under the lower panel.
assert.equal(manual.layout.xaxis.title.text, '');
assert.equal(manual.layout.xaxis.showticklabels, true);
// Bragg rows stay just below the fitted window.
const rows = buildRefinementView(plot, { xRange: [60, 80], selection: { A: true } });
assert(rows.data[1].y[0] < 10 && rows.data[1].y[0] > -50, rows.data[1].y[0]);

// Clamp: zoom and pan stay inside the measured 2θ range.
const full = [9.9, 100.1];
const near = (a, b) => a.every((value, i) => Math.abs(value - b[i]) < 1e-9);
assert(near(clampRange([5.14, 104.82], full), full)); // wider than the data -> the data
assert(near(clampRange([0, 20], full), [9.9, 29.9])); // sticks out -> shifted back, same width
assert(near(clampRange([95, 110], full), [85.1, 100.1]));
assert(near(clampRange([40, 30], full), [30, 40])); // sorted
assert(near(clampRange([30, 40], null), [30, 40])); // no bounds known: unchanged
assert.equal(clampRange([30, 30], full), null);
assert.equal(clampRange([NaN, 3], full), null);
const tiny = clampRange([50, 50.00001], full);
assert(Math.abs(tiny[1] - tiny[0] - 0.0902) < 1e-6 && Math.abs((tiny[0] + tiny[1]) / 2 - 50.000005) < 1e-6);
assert(insideRange([20, 30], full) && !insideRange([5, 30], full) && insideRange([5, 30], null));

// Wheel: one notch is one fixed step (1.25x) about the cursor, whatever the pixel delta.
assert.equal(WHEEL_STEP, 1.25);
assert.equal(wheelNotches({ deltaY: 100 }), 1);
assert.equal(wheelNotches({ deltaY: -120 }), -1);
assert.equal(wheelNotches({ deltaY: 66.7 }), 1); // a notch at 150 % page zoom
assert.equal(wheelNotches({ deltaY: -3, deltaMode: 1 }), -1); // lines (Firefox)
assert.equal(wheelNotches({ deltaY: 4 }), 0.04); // trackpad: fractional
assert.equal(wheelNotches({ deltaY: 900 }), 3); // capped
assert.equal(wheelNotches({ deltaY: 0 }), 0);
const zoomIn = wheelZoomRange([20, 60], 30, -1, full);
assert(near(zoomIn, [30 - 10 / 1.25, 30 + 30 / 1.25])); // the cursor stays put
assert(Math.abs((zoomIn[1] - zoomIn[0]) - 40 / 1.25) < 1e-9);
assert(near(wheelZoomRange(zoomIn, 30, 1, full), [20, 60])); // in then out returns
assert(near(wheelZoomRange([10, 100], 50, 3, full), full)); // zoom out stops at the data
assert(near(wheelZoomRange([9.9, 30], 9.9, 2, full), [9.9, 9.9 + 20.1 * 1.5625])); // at the edge
assert.deepEqual(wheelZoomRange([20, 60], 30, 0, full), [20, 60]);
// Pan keeps the width and stops at the ends.
assert(near(panRange([20, 30], 5, full), [25, 35]));
assert(near(panRange([20, 30], -50, full), [9.9, 19.9]));
assert(near(panRange([90, 100], 50, full), [90.1, 100.1]));
// Both panels get the same 2θ window.
assert.deepEqual(xRangeUpdate([1, 2], { xaxis2: true }),
  { 'xaxis.range': [1, 2], 'xaxis.autorange': false, 'xaxis2.range': [1, 2], 'xaxis2.autorange': false });
assert.deepEqual(xRangeUpdate([1, 2]), { 'xaxis.range': [1, 2], 'xaxis.autorange': false });

// Relayout events -> view state.
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.range[0]': 20, 'xaxis.range[1]': 25 }), { xRange: [20, 25] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis2.range[0]': 20, 'xaxis2.range[1]': 25 }), { xRange: [20, 25] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.range': [1, 2], 'yaxis.range': [3, 4] }), { xRange: [1, 2], yRange: [3, 4] });
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.autorange': true, 'yaxis.autorange': true }), { xRange: null, yRange: null });
assert.deepEqual(viewChangeFromRelayout({ dragmode: 'pan' }), {});
assert.deepEqual(viewChangeFromRelayout({ 'xaxis2.range': [3, 4] }), { xRange: [3, 4] });
// With the data range, an axis drag past the data is clamped on the way into the state.
assert.deepEqual(viewChangeFromRelayout({ 'xaxis.range[0]': 0, 'xaxis.range[1]': 20 }, { full: [10, 110] }), { xRange: [10, 30] });
assert.deepEqual(viewChangeFromRelayout(xRangeUpdate([20, 30], { xaxis2: true }), { full: [10, 110] }), { xRange: [20, 30] });
console.log('Reset clears 2θ zoom, fit-to-view intensity, clamped wheel/pan/box navigation and relayout mapping passed.');

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
