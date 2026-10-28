import assert from 'node:assert/strict';
import {
  collectHints, hintText, hintsPending, observedPeak, peakFromClick, peakShapes, phaseGroupNames, previewTrace,
  updatePeakSelection,
} from '../src/dara/server/ui/src/components/peakSelection.mjs';

const plot = {
  data: [
    { name: 'Observed', x: [34.9, 35.0, 35.1, 35.2, 35.3, 40.0], y: [10, 40, 90, 60, 20, 5] },
    { name: 'Missing peaks', x: [35.42, 60] },
    { name: 'Fe3O4 (30 wt%)', meta: 'phase-profile', legendgroup: 'Fe3O4' },
    { name: 'Fe3O4 ticks', meta: 'reflection', legendgroup: 'Fe3O4' },
    { name: 'MgO', meta: 'phase-profile', legendgroup: 'MgO' },
  ],
};

// click mapping
assert.equal(peakFromClick(plot, 'Missing peaks', 35.4249), 35.42);
assert.equal(peakFromClick(plot, 'Observed', 35.3), 35.42, 'snaps to a missing peak within 0.3 deg');
assert.equal(peakFromClick(plot, 'Observed', 35.0), 35.1, 'local maximum within 0.15 deg');
assert.equal(peakFromClick(plot, 'Observed', 40.0), 40, 'no neighbours -> the point itself');
assert.equal(peakFromClick(plot, 'Calculated', 35.0), null);
assert.equal(peakFromClick(plot, 'Observed', NaN), null);
assert.equal(peakFromClick({ data: [{ name: 'Observed', x: { dtype: 'f8', bdata: Buffer.from(new Float64Array([1, 2]).buffer).toString('base64') }, y: [1, 5] }] }, 'Observed', 1.9), 2,
  'typed-array JSON is decoded');

// selection semantics
assert.deepEqual(updatePeakSelection([], 35.42), [35.42]);
assert.deepEqual(updatePeakSelection([35.42], 43.05), [43.05], 'plain click replaces');
assert.deepEqual(updatePeakSelection([35.42], 35.42), [], 'plain click on the only peak clears it');
assert.deepEqual(updatePeakSelection([43.05], 35.42, { shift: true }), [35.42, 43.05]);
assert.deepEqual(updatePeakSelection([35.42, 43.05], 35.42, { shift: true }), [43.05]);
const ten = Array.from({ length: 10 }, (_, i) => 20 + i);
assert.deepEqual(updatePeakSelection(ten, 50, { shift: true }), ten, 'max 10');
assert.deepEqual(updatePeakSelection(undefined, 50, { shift: true }), [50]);
assert.deepEqual(updatePeakSelection([1], NaN), [1]);

// shapes / preview
const shapes = peakShapes([35.42]);
assert.equal(shapes.length, 2);
assert.deepEqual(shapes.map(s => s.xref), ['x', 'x2']);
assert.equal(shapes[0].line.dash, 'dash');
assert.deepEqual(peakShapes(null), []);
const trace = previewTrace([[35.4, 100], [43, 50], [50, NaN]], 1000);
assert.deepEqual(trace.x, [35.4, 35.4, null, 43, 43, null]);
assert.deepEqual(trace.y, [0, 1000, null, 0, 500, null]);
assert.equal(previewTrace([[10, 10]], 100, { floor: 1 }).y[0], 1);
assert.equal(observedPeak(plot), 90);
assert.equal(observedPeak({ data: [] }), 1);
assert.deepEqual(phaseGroupNames(plot), ['Fe3O4', 'MgO']);

// hints
const diag = {
  missing_classified: [
    { two_theta: 35.4, hint: { phase: 'Fe3O4_227_(cod_1)-0', formula: 'Fe3O4', spacegroup_symbol: 'Fd-3m', matched_peaks: [35.42], source: 'cod' } },
    { two_theta: 43.0, hint: { phase: 'Fe3O4_227_(cod_1)-0', formula: 'Fe3O4', spacegroup_symbol: 'Fd-3m', matched_peaks: [43.05], source: 'cod' } },
    { two_theta: 50, hint: null },
  ],
  trace_hints: [{ phase: 'MgO_225_(mp-1)-0', formula: 'MgO', matched_peaks: [42.9] }],
};
const hints = collectHints(diag);
assert.equal(hints.length, 2);
assert.deepEqual(hints[0].matched_peaks, [35.42, 43.05]);
assert.equal(hintText(hints[0]), 'Trace Fe3O4 (Fd-3m) matches 35.42°, 43.05°');
assert.equal(hintText(hints[1]), 'Trace MgO matches 42.90°');
assert.deepEqual(collectHints(null), []);

console.log('test_peak_selection ok');

// --- hintsPending: the task page polls while the server still simulates references -------------
assert.equal(hintsPending(null), false);
assert.equal(hintsPending({}), false);
assert.equal(hintsPending({ final_result: { peak_diagnostics: { trace_hints: [] } }, all_results: [] }), false);
assert.equal(hintsPending({ final_result: { peak_diagnostics: { hints_pending: true } }, all_results: [] }), true);
assert.equal(hintsPending({ final_result: {}, all_results: [{ peak_diagnostics: { hints_pending: true } }] }), true);
assert.equal(hintsPending({ final_result: { peak_diagnostics: { hints_pending: false } }, all_results: [{}, null] }), false);
