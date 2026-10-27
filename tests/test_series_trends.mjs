import assert from 'node:assert/strict';
import {
  MAX_SERIES_FILES, fitTraces, hasActiveItems, latticeTraces, phaseLabels, phaseTraces, progress,
  seriesPreviewRows, taskIds, xAxis,
} from '../src/dara/server/ui/src/components/seriesTrends.mjs';

const lattice = (a, esd) => ({ a: { value: a, esd }, c: { value: a * 1.6, esd: esd * 2 } });
const zno = (wt, esd, a) => ({ name: 'ZnO_186_(cod_1)-0', label: 'Zincite · ZnO', weight_percent: wt, weight_esd_percent: esd, lattice: lattice(a, 0.0001) });
const item = (id, order, value, state, phases, extra = {}) => ({
  task_id: id, name: `s${id}`, order,
  variable: value === null ? null : { raw: `${value}C`, value, unit: '°C' },
  state, summary: phases === null ? null : { schema: 2, final_rwp: 7 + id, best_rwp: 6 + id, gof: 2 + id / 10, phases, ...extra },
});

const items = [
  item(11, 0, 300, 'COMPLETED', [zno(90, 0.5, 3.25), { name: 'Z', label: 'Zn2SiO4', weight_percent: 10, weight_esd_percent: 0.4 }]),
  item(12, 1, 500, 'COMPLETED', [zno(100, 0.2, 3.26)]),
  item(13, 2, 700, 'RUNNING', null),
];

// --- x axis ----------------------------------------------------------------------
let axis = xAxis(items);
assert.deepEqual(axis.values, [300, 500, 700]);
assert.equal(axis.title, '°C');
assert.equal(axis.usesVariable, true);
const noVariable = xAxis([item(1, 0, null, 'COMPLETED', []), item(2, 1, 5, 'COMPLETED', [])]);
assert.deepEqual(noVariable.values, [1, 2]);
assert.equal(noVariable.title, 'Sample order');
// An unfinished item without a variable does not force the order axis.
const partial = xAxis([item(1, 0, 10, 'COMPLETED', []), item(2, 1, null, 'READY', null)]);
assert.equal(partial.usesVariable, true);
assert.equal(xAxis([]).title, 'Sample order');
assert.equal(xAxis(undefined).values.length, 0);

// --- phases ----------------------------------------------------------------------
const traces = phaseTraces(items);
assert.deepEqual(traces.map(trace => trace.label), ['Zincite · ZnO', 'Zn2SiO4']);
assert.deepEqual(traces[0], { label: 'Zincite · ZnO', x: [300, 500], y: [90, 100], err: [0.5, 0.2] });
// A phase missing from one sample is a gap (null), never a zero.
assert.deepEqual(traces[1], { label: 'Zn2SiO4', x: [300, 500], y: [10, null], err: [0.4, null] });
assert.deepEqual(phaseLabels(items), ['Zincite · ZnO', 'Zn2SiO4']);
// Merge by label across different reference names, with a name fallback.
const merged = phaseTraces([
  item(1, 0, 1, 'COMPLETED', [{ name: 'A_1', label: 'Anatase', weight_percent: 5 }]),
  item(2, 1, 2, 'COMPLETED', [{ name: 'A_2', label: 'Anatase', weight_percent: 6 }, { name: 'Raw_name', weight_percent: 7 }]),
]);
assert.deepEqual(merged.map(trace => trace.label), ['Anatase', 'Raw_name']);
assert.deepEqual(merged[0].y, [5, 6]);
assert.deepEqual(merged[1].y, [null, 7]);
// Schema-1 summaries have no phases.
assert.deepEqual(phaseTraces([{ task_id: 1, order: 0, state: 'COMPLETED', summary: { schema: 1 } }]), []);

// --- lattice ---------------------------------------------------------------------
assert.deepEqual(latticeTraces(items, 'Zincite · ZnO', 'a'), { label: 'Zincite · ZnO', parameter: 'a', x: [300, 500], y: [3.25, 3.26], err: [0.0001, 0.0001] });
const missingLattice = latticeTraces(items, 'Zn2SiO4', 'a');
assert.deepEqual(missingLattice.y, [null, null]);
assert.deepEqual(latticeTraces(items, 'Zincite · ZnO', 'beta').y, [null, null]);

// --- fit -------------------------------------------------------------------------
const fit = fitTraces(items);
assert.deepEqual(fit.x, [300, 500]);
assert.deepEqual(fit.rwp, [18, 19]);
assert.deepEqual(fit.gof.map(value => Math.round(value * 10) / 10), [3.1, 3.2]);
assert.deepEqual(fitTraces([item(1, 0, 1, 'COMPLETED', null)]).rwp, [null]);

// --- ids and progress ----------------------------------------------------------------
assert.deepEqual(taskIds(items), [11, 12, 13]);
assert.deepEqual(taskIds(items, { completedOnly: true }), [11, 12]);
assert.deepEqual(taskIds(undefined), []);
assert.equal(hasActiveItems(items), true);
assert.equal(hasActiveItems(items.slice(0, 2)), false);
assert.deepEqual(progress(items), { done: 2, total: 3 });

// --- submit-page preview -----------------------------------------------------------
assert.equal(MAX_SERIES_FILES, 50);
assert.deepEqual(seriesPreviewRows({ unit: '°C', items: [
  { filename: 'b.xy', order: 1, variable: { raw: '500C', value: 500, unit: '°C' } },
  { filename: 'a.xy', order: 0, variable: { raw: '300C', value: 300, unit: '°C' } },
  { filename: 'c.xy', order: 2, variable: null },
] }), [
  { filename: 'a.xy', order: 1, variableText: '300 °C' },
  { filename: 'b.xy', order: 2, variableText: '500 °C' },
  { filename: 'c.xy', order: 3, variableText: '—' },
]);
assert.deepEqual(seriesPreviewRows(null), []);

console.log('series trend tests passed');
