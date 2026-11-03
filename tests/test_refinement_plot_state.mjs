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

{
  // Peak markers come at intensity 0; zooming to a window whose baseline is ~1500 must keep them
  // just under the visible range instead of far below it.
  const plot = {
    layout: { xaxis: { range: [10, 80] }, yaxis: {} },
    data: [
      { name: 'Observed', x: [20, 30, 40, 50], y: [1500, 4000, 1600, 1550] },
      { name: 'Missing peaks', meta: 'diagnostic', x: [30, 45], y: [0, 0], mode: 'markers' },
    ],
  };
  const zoomed = buildRefinementView(plot, { xRange: [25, 45], fitY: true });
  const markers = zoomed.data.find(trace => trace.name === 'Missing peaks');
  const [lo, hi] = zoomed.layout.yaxis.range;
  assert.ok(markers.y.every(y => y >= lo && y <= hi), `markers ${markers.y} outside ${lo}..${hi}`);
  assert.ok(markers.y[0] < 1600 && markers.y[0] > 1400, 'markers sit just under the visible baseline');
  const full = buildRefinementView(plot, {});
  const fullMarkers = full.data.find(trace => trace.name === 'Missing peaks');
  const [flo, fhi] = full.layout.yaxis.range;
  assert.ok(fullMarkers.y.every(y => y >= flo && y <= fhi));
  // A whole triangle fits: the markers keep clear of both the axis edge and the pattern.
  const height = fhi - flo;
  assert.ok(fullMarkers.y[0] - flo > 0.03 * height && 0 - fullMarkers.y[0] > 0.03 * height);
  assert.equal(fullMarkers.cliponaxis, false);
  const withTicks = buildRefinementView({ ...plot, data: [...plot.data,
    { name: 'A', meta: 'reflection', legendgroup: 'A', x: [31], y: [0], marker: { color: '#000' } }] }, { selection: { A: true } });
  const tickY = withTicks.data.find(trace => trace.meta === 'reflection').y[0];
  assert.ok(tickY < withTicks.data.find(trace => trace.name === 'Missing peaks').y[0], 'Bragg rows sit below the markers');
  console.log('diagnostic marker position ok');
}
