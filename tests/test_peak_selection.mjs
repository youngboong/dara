import assert from 'node:assert/strict';
import {
  collectHints, hintText, hintsPending, observedPeak, peakFromClick, peakShapes, phaseGroupNames, previewTrace,
  updatePeakSelection, significantPeaks, matchStrength, autoMatchStrength, isSlowCandidate,
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

{
  const diagnostics = { missing_classified: [
    { two_theta: 31.9, class: 'significant', rel_height: 0.028 },
    { two_theta: 22.6, class: 'significant', rel_height: 0.30 },
    { two_theta: 40.1, class: 'minor', rel_height: 0.01 },
    { two_theta: 'x', class: 'significant', rel_height: 0.5 },
    { two_theta: 26.3, class: 'significant', rel_height: 0.16 },
  ] };
  assert.deepEqual(significantPeaks(diagnostics), [22.6, 26.3, 31.9]);
  // the largest peaks are kept when there are more than `max`, then shown in 2θ order
  assert.deepEqual(significantPeaks(diagnostics, { max: 2 }), [22.6, 26.3]);
  assert.deepEqual(significantPeaks(null), []);
  assert.deepEqual(significantPeaks({ missing: [1, 2] }), []);
  assert.equal(matchStrength({ score: 1, n_strong: 6, strong_unobserved: 0 }).label, 'Strong match');
  assert.equal(matchStrength({ score: 0.75 }).label, 'Strong match');
  // high score but 7 of 19 strong reflections not seen in the data: not strong
  assert.equal(matchStrength({ score: 0.82, n_strong: 19, strong_unobserved: 7 }).label, 'Possible match');
  assert.equal(matchStrength({ score: 0.6 }).label, 'Possible match');
  assert.equal(matchStrength({ score: 0.5 }).label, 'Weak match');
  assert.equal(matchStrength({}), null);
}

{
  // explains 4 of 10 significant peaks with 2 of 21 strong reflections unseen: possible
  assert.equal(autoMatchStrength({ matched: [1, 2, 3, 4], n_strong: 21, strong_unobserved: 2 }, 10).label, 'Possible match');
  // only 2 of 10 peaks: weak; 2 of 3 peaks: enough
  assert.equal(autoMatchStrength({ matched: [1, 2], n_strong: 6, strong_unobserved: 0 }, 10).label, 'Weak match');
  assert.equal(autoMatchStrength({ matched: [1, 2], n_strong: 6, strong_unobserved: 0 }, 3).label, 'Possible match');
  // the single significant peak explained with no contradiction
  assert.equal(autoMatchStrength({ matched: [1], n_strong: 3, strong_unobserved: 0 }, 1).label, 'Possible match');
  // many of its strong reflections missing from the data: weak
  assert.equal(autoMatchStrength({ matched: [1, 2, 3], n_strong: 8, strong_unobserved: 2 }, 5).label, 'Weak match');
  assert.equal(autoMatchStrength({}, 4).label, 'Weak match');
}

// Candidates with many reflections in range are flagged as slow to refine.
assert.equal(isSlowCandidate({ n_lines: 653 }), true);   // Bi4O7, P-1: 146 s
assert.equal(isSlowCandidate({ n_lines: 283 }), false);  // NaP5: 37 s
assert.equal(isSlowCandidate({}), false);                // older servers send no count
console.log('slow candidate flag ok');

// --- audit fixes: click position, single-peak strength, ordering --------------------------------
import { peakFromPlotClick, compareCandidates, CANDIDATES_SHOWN } from '../src/dara/server/ui/src/components/peakSelection.mjs';
{
  // A stale hovered point (Shift+click right after a mouse move) loses to the 2θ under the mouse.
  const stale = { points: [{ curveNumber: 0, x: 35.0 }], cursorX: 35.5 };
  assert.equal(peakFromPlotClick(plot, stale), 35.42, 'cursor 2θ snaps to the missing marker');
  assert.equal(peakFromPlotClick(plot, { points: [{ curveNumber: 0, x: 35.07 }] }), 35.1, 'no cursorX: hovered point');
  assert.equal(peakFromPlotClick(plot, { points: [{ curveNumber: 2, x: 35 }] }), null, 'other curves are ignored without cursorX');
  assert.equal(peakFromPlotClick(plot, { points: [{ curveNumber: 2, x: 35 }], cursorX: 35.12 }), 35.1, 'cursorX works on any curve');
  assert.equal(peakFromPlotClick(plot, { points: [] }), null);

  // One clicked peak is never a strong match.
  assert.equal(matchStrength({ score: 1, matched: [35.9], n_strong: 6, strong_unobserved: 0 }).label, 'Possible match');
  assert.equal(matchStrength({ score: 1, matched: [35.9], n_strong: 6, strong_unobserved: 3 }).label, 'Weak match');
  assert.equal(matchStrength({ score: 1, matched: [35.9, 43.1], n_strong: 6, strong_unobserved: 0 }).label, 'Strong match');
  assert.equal(matchStrength({ score: 0.5, matched: [35.9], n_strong: 6, strong_unobserved: 0 }).label, 'Weak match');

  const list = [{ ref: 'a', score: 1, n_strong: 10, strong_unobserved: 5 }, { ref: 'b', score: 0.5 }, { ref: 'c', score: 1, n_strong: 10, strong_unobserved: 0 }];
  assert.deepEqual([...list].sort(compareCandidates).map(item => item.ref), ['c', 'a', 'b']);
  assert.equal(CANDIDATES_SHOWN, 8);
  console.log('peak click position and strength ok');
}
