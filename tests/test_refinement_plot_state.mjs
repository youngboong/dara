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
assert.equal(opened.layout.xaxis.title.text, ''); // the 2θ title is under the Obs − calc panel only
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
  // Auto-Y starts the axis under the lowest shown intensity (1500), not at zero; a whole triangle fits.
  const height = fhi - flo;
  assert.ok(fullMarkers.y[0] - flo > 0.03 * height && 1500 - fullMarkers.y[0] > 0.03 * height);
  assert.equal(fullMarkers.cliponaxis, false);
  const withTicks = buildRefinementView({ ...plot, data: [...plot.data,
    { name: 'A', meta: 'reflection', legendgroup: 'A', x: [31], y: [0], marker: { color: '#000' } }] }, { selection: { A: true } });
  const tickY = withTicks.data.find(trace => trace.meta === 'reflection').y[0];
  assert.ok(tickY < withTicks.data.find(trace => trace.name === 'Missing peaks').y[0], 'Bragg rows sit below the markers');
  console.log('diagnostic marker position ok');
}

{
  // A user intensity range (box zoom with Auto-Y off) is the data window: the marker band and the
  // Bragg rows go below it, so nothing leaves the main panel or reaches the Obs − calc panel.
  const plot = {
    layout: { xaxis: { range: [10, 80] }, yaxis: {}, xaxis2: { range: [10, 80], matches: 'x' }, yaxis2: {} },
    data: [
      { name: 'Observed', x: [20, 30, 40, 50], y: [1500, 4000, 1600, 1550] },
      { name: 'Missing peaks', meta: 'diagnostic', x: [30, 45], y: [0, 0], mode: 'markers' },
      { name: 'A', meta: 'reflection', legendgroup: 'A', x: [31], y: [0], marker: { color: '#000' } },
      { name: 'B (12.0 wt%)', meta: 'reflection', legendgroup: 'B', x: [41], y: [0], marker: { color: '#111' } },
    ],
  };
  for (const yRange of [[1500, 5000], [-1345, 8980], [3000, 3100]]) {
    for (const fitY of [false, true]) {
      const view = buildRefinementView(plot, { selection: { A: true, B: true }, yRange, xRange: [25, 45], fitY });
      const [lo, hi] = view.layout.yaxis.range;
      assert.equal(hi, yRange[1], 'the window top is kept');
      const markers = view.data.find(trace => trace.name === 'Missing peaks').y[0];
      const rows = view.data.filter(trace => trace.meta === 'reflection').map(trace => trace.y[0]);
      assert(markers < yRange[0] && markers > lo, `markers ${markers} not in the band below ${yRange}`);
      assert(rows.every(y => y < markers && y > lo), `rows ${rows} outside ${lo}..${markers}`);
      assert.equal(view.layout.annotations.length, 2);
      assert(view.layout.annotations.every(note => note.y > lo && note.y < hi));
      assert(view.layout.yaxis.tickvals.every(value => value >= yRange[0] - 1e-9), 'no ticks in the band');
      assert.equal(view.layout.yaxis.fixedrange, false);
    }
  }
  // Log scale: the user range comes in log10 units; the bands stay inside too.
  const logView = buildRefinementView(plot, { selection: { A: true }, scale: 'log', yRange: [3, 3.7] });
  const [llo, lhi] = logView.layout.yaxis.range;
  assert.equal(lhi, 3.7);
  const logRow = Math.log10(logView.data.find(trace => trace.meta === 'reflection' && trace.visible === true).y[0]);
  const logMarker = Math.log10(logView.data.find(trace => trace.name === 'Missing peaks').y[0]);
  assert(logRow > llo && logRow < logMarker && logMarker < 3, `${llo} ${logRow} ${logMarker}`);
  // Narrow plots (<= 1280 px screens) get a compact legend instead of the fixed 290 px column.
  const narrow = buildRefinementView({ ...plot, layout: { ...plot.layout, margin: { l: 155, r: 290 } } }, { plotWidth: 1216 });
  assert.equal(narrow.layout.margin.r, 24);
  assert.equal(narrow.layout.legend.font.size, 10);
  const wide = buildRefinementView({ ...plot, layout: { ...plot.layout, margin: { l: 155, r: 290 } } }, { plotWidth: 1800 });
  assert.equal(wide.layout.margin.r, 290);
  console.log('user intensity window keeps markers, Bragg rows and labels inside the main panel ok');
}

{
  // The plot div sets the size; a fixed server height would override autosize.
  const sized = { layout: { height: 760, width: 1200, xaxis: { range: [10, 80] }, yaxis: {} },
    data: [{ name: 'Observed', x: [20, 30], y: [10, 100] }] };
  const view = buildRefinementView(sized);
  assert.equal(view.layout.height, undefined);
  assert.equal(view.layout.width, undefined);
  assert.equal(view.layout.autosize, true);
  assert.equal(sized.layout.height, 760); // source layout untouched
  console.log('layout follows the container size ok');
}

// --- audit fixes: exclusion window, pattern title, legend under a narrow plot ---------------------
import { exclusionViewRange, patternTitle, LEGEND_BELOW_WIDTH } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';
{
  assert.equal(exclusionViewRange(null, [10, 100]), null, 'no zoom: nothing to exclude');
  assert.equal(exclusionViewRange([9.9, 100.09], [9.9, 100.09]), null, 'the whole pattern is refused');
  assert.equal(exclusionViewRange([12, 95], [10, 100]), null, 'more than 90 % is refused');
  assert.deepEqual(exclusionViewRange([30, 20], [10, 100]), [20, 30]);
  assert.deepEqual(exclusionViewRange([20, 30], null), [20, 30]);

  assert.equal(patternTitle('xrd_data.xy (Rwp=2.96%)', 'Fe2O3_1000C.xrdml'), 'Fe2O3_1000C.xrdml (Rwp=2.96%)');
  assert.deepEqual(patternTitle({ text: 'xrd_data.xy (Rwp=2.96%)', x: 0.5 }, 'P'), { text: 'P (Rwp=2.96%)', x: 0.5 });
  assert.equal(patternTitle('xrd_data.xy (Rwp=2.96%)', null), 'xrd_data.xy (Rwp=2.96%)');
  const titled = buildRefinementView({ ...plot, layout: { ...plot.layout, title: { text: 'xrd_data.xy (Rwp=1.00%)' } } }, { title: 'S1' });
  assert.equal(titled.layout.title.text, 'S1 (Rwp=1.00%)');

  const below = buildRefinementView({ ...plot, layout: { ...plot.layout, margin: { l: 155, r: 290 } } }, { plotWidth: LEGEND_BELOW_WIDTH - 1 });
  assert.equal(below.layout.legend.orientation, 'h');
  assert.equal(below.layout.margin.r, 24);
  const beside = buildRefinementView({ ...plot, layout: { ...plot.layout, margin: { l: 155, r: 290 } } }, { plotWidth: LEGEND_BELOW_WIDTH });
  assert.notEqual(beside.layout.legend?.orientation, 'h');
  console.log('exclusion window, title and legend placement ok');
}

// --- legend under a narrow plot: rows reserve bottom margin, legend anchored at the figure bottom --
import { legendRowCount, LEGEND_ROW_PX } from '../src/dara/server/ui/src/components/refinementPlotState.mjs';
{
  const base = ['Observed', 'Calculated', 'Background', 'Difference', 'Missing peaks', 'Extra peaks', 'Intensity mismatch']
    .map(name => ({ name, x: [20, 30], y: [1, 2] }));
  const phases = n => Array.from({ length: n }, (_, i) => [
    { name: `Ph${i}<sub>2</sub>O<sub>3</sub> · Cmcm (12.34 wt%)`, meta: 'phase-profile', legendgroup: `P${i}`, x: [20, 30], y: [1, 2], visible: 'legendonly' },
    { name: `Ph${i} ticks`, meta: 'reflection', legendgroup: `P${i}`, showlegend: false, x: [20, 30], y: [0, 0], marker: { color: 'red' }, visible: 'legendonly' },
  ]).flat();
  assert.equal(legendRowCount([], 800), 0);
  assert.equal(legendRowCount([{ name: 'A' }, { name: 'B', showlegend: false }], 800), 1, 'hidden entries take no room');
  assert.equal(legendRowCount([{ name: 'A', legendgroup: 'g' }, { name: 'A2', legendgroup: 'g' }], 50), 1, 'a group is one entry');
  const few = legendRowCount([...base, ...phases(2)], 500);
  const many = legendRowCount([...base, ...phases(6)], 500);
  assert.ok(many > few, `more phases need more rows (${few} -> ${many})`);
  assert.ok(legendRowCount([...base, ...phases(6)], 300) >= many, 'a narrower plot needs at least as many rows');
  for (const [n, width] of [[4, 880], [6, 880], [4, 640], [6, 640]]) {
    const view = buildRefinementView({ ...plot, data: [...base, ...phases(n)], layout: { ...plot.layout, margin: { l: 155, r: 290, b: 65 } } }, { plotWidth: width });
    const rows = legendRowCount(view.data, width - 155 - 24);
    assert.equal(view.layout.margin.b, 65 + 10 + rows * LEGEND_ROW_PX, `${n} phases at ${width} px reserve every legend row`);
    assert.equal(view.layout.legend.yref, 'container');
    assert.equal(view.layout.legend.y, 0);
    assert.equal(view.layout.legend.yanchor, 'bottom');
  }
  console.log('legend rows reserve bottom margin ok');
}
