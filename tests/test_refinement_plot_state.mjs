import assert from 'node:assert/strict';
import { buildRefinementView } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';

const plot = { layout: { yaxis: {}, xaxis: {} }, data: [
  { name: 'Observed', x: [20, 30], y: [10, 100] },
  ...['A', 'B', 'C'].flatMap(group => [
    { name: group, meta: 'phase-profile', legendgroup: group, y: [5, 10], visible: 'legendonly' },
    { name: group, meta: 'reflection', legendgroup: group, x: [20, 30], y: [-20, -20], marker: { color: 'red' }, visible: 'legendonly' },
  ]),
] };
const closed = buildRefinementView(plot);
assert.equal(closed.rows.length, 0);
const opened = buildRefinementView(plot, { selection: { A: true, C: true } });
assert.deepEqual(opened.rows, ['A', 'C']);
assert(opened.layout.yaxis.range[0] < closed.layout.yaxis.range[0]);
assert(opened.layout.yaxis.tickvals.every(value => value >= 0));
assert.equal(opened.layout.xaxis.title.text, '2θ [°]');
const remaining = buildRefinementView(plot, { selection: { A: false, C: true } });
assert.deepEqual(remaining.rows, ['C']);
assert.equal(remaining.data[6].y[0], opened.data[2].y[0]); // no empty row after hiding A
const profilesOff = buildRefinementView(plot, { selection: { A: false, C: true }, showPhaseProfiles: false });
assert.equal(profilesOff.data[1].visible, 'legendonly');
assert.equal(profilesOff.data[5].visible, 'legendonly');
assert.equal(profilesOff.data[6].visible, true); // toolbar preserves C's selection
const log = buildRefinementView(plot, { selection: { C: true }, scale: 'log' });
assert(log.data[6].y.every(value => value > 0));
assert.equal(log.layout.annotations[0].y, Math.log10(log.data[6].y[0]));
assert.deepEqual(plot.data[6].y, [-20, -20]); // plotting state never mutates source
console.log('Legend selection, compact rows, nonnegative ticks, toolbar persistence and log scale passed.');
