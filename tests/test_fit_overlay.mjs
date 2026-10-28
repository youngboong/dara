import assert from 'node:assert/strict';
import {
  appendStoredFit, compareHref, fitLabel, fitsParam, interpolate, overlayLayout, overlayTraces,
  parseFitsParam, readStoredFits, sameGrid,
} from '../src/dara/server/ui/src/components/fitOverlay.mjs';

// --- parse / format ---------------------------------------------------------------------------
assert.deepEqual(parseFitsParam('1:best,2:version:2,3:0'),
  [{ taskId: 1, sel: 'best' }, { taskId: 2, sel: 'version:2' }, { taskId: 3, sel: 0 }]);
assert.equal(fitsParam(parseFitsParam('1:best,2:version:2,3:0')), '1:best,2:version:2,3:0');
assert.deepEqual(parseFitsParam('1:best,1:best,1:version:1'), [{ taskId: 1, sel: 'best' }], 'dedupe incl. version:1 == best');
assert.deepEqual(parseFitsParam('x:best,4,5:bogus,:best,0:best,6:version:3'), [{ taskId: 6, sel: 'version:3' }]);
assert.deepEqual(parseFitsParam(''), []);
assert.deepEqual(parseFitsParam(null), []);
const many = Array.from({ length: 9 }, (_, i) => `${i + 1}:best`).join(',');
assert.equal(parseFitsParam(many).length, 6, 'max 6');
assert.equal(fitLabel(4, 'version:2'), '#4 v2');
assert.equal(fitLabel(4, 'best'), '#4 best fit');
assert.equal(fitLabel(4, 1), '#4 cand. 2');

// --- grids / interpolation --------------------------------------------------------------------
assert.equal(sameGrid([1, 2, 3], [1, 2, 3]), true);
assert.equal(sameGrid([1, 2, 3], [1, 2, 3 + 1e-9]), true);
assert.equal(sameGrid([1, 2, 3], [1, 2, 3.001]), false);
assert.equal(sameGrid([1, 2, 3], [1, 2]), false);
assert.equal(sameGrid(null, [1]), false);
assert.deepEqual(interpolate([0, 10], [0, 100], [-1, 0, 2.5, 10, 10.5]), [null, 0, 25, 100, null]);
assert.deepEqual(interpolate([], [], [1, 2]), [null, null]);
assert.deepEqual(interpolate([0, 1, 2], [0, 10, 0], [0.5, 1.5]), [5, 5]);
assert.deepEqual(interpolate([0, 1], [0, 1], [NaN]), [null]);

// --- traces -----------------------------------------------------------------------------------
const curve = (task_id, sel, x, ys, extra = {}) => ({
  task_id, sel, x, y_obs: ys.map(v => v * 1.0), y_calc: ys, reflections: { P1: [1, 2] }, ...extra,
});
const A = curve(1, 'best', [0, 1, 2, 3], [0, 50, 100, 20]);
const B = curve(1, 'version:2', [0, 1, 2, 3], [0, 40, 100, 10]);
const same = overlayTraces([A, B], { normalize: false, showDifference: true });
assert.deepEqual(same.map(t => t.name), ['Calculated #1 best fit', 'Calculated #1 v2', 'A − B']);
assert.deepEqual(same[2].y, [0, 10, 0, 10]);
assert.equal(same[2].xaxis, 'x2');

const shifted = overlayTraces([A, B], { normalize: false, offset: 500, showDifference: true });
assert.deepEqual(shifted[1].y, [500, 540, 600, 510]);
assert.deepEqual(shifted[2].y, [0, 10, 0, 10], 'difference ignores display offset');

const normalized = overlayTraces([curve(1, 'best', [0, 1], [0, 200])], { normalize: true });
assert.deepEqual(normalized[0].y, [0, 100]);

// grid mismatch -> interpolated onto A's grid; outside B's range = null
const Bshort = curve(2, 'best', [1, 2], [40, 100]);
const mismatch = overlayTraces([A, Bshort], { normalize: false, showDifference: true });
const diff = mismatch[mismatch.length - 1];
assert.equal(diff.name, 'A − B (interpolated)');
assert.deepEqual(diff.x, [0, 1, 2, 3]);
assert.deepEqual(diff.y, [null, 10, 0, null]);

// toggles
assert.equal(overlayTraces([A], { showCalculated: false }).length, 0);
const withObs = overlayTraces([A], { showObserved: true });
assert.deepEqual(withObs.map(t => t.meta), ['observed', 'calculated']);
const marks = overlayTraces([A, B], { normalize: true, showMarkers: true });
const ticks = marks.filter(t => t.meta === 'reflection');
assert.equal(ticks.length, 2);
assert.ok(ticks[0].y[0] < 0 && ticks[1].y[0] < ticks[0].y[0], 'tick rows stack below zero');
assert.deepEqual(ticks[0].x, [1, 2]);
assert.equal(overlayTraces([A], { showDifference: true }).filter(t => t.meta === 'difference').length, 0, 'needs two fits');
assert.equal(overlayTraces([], {}).length, 0);
assert.ok(overlayLayout({ showDifference: true }).yaxis2);
assert.equal(overlayLayout({ showDifference: false }).yaxis2, undefined);

// --- localStorage helper ----------------------------------------------------------------------
const store = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v) }; };
const s = store();
assert.deepEqual(readStoredFits(s), []);
appendStoredFit(s, 4, 'best');
appendStoredFit(s, 4, 'version:2');
const list = appendStoredFit(s, 4, 'best');
assert.deepEqual(list, [{ taskId: 4, sel: 'version:2' }, { taskId: 4, sel: 'best' }]);
assert.deepEqual(readStoredFits(s), list);
assert.deepEqual(readStoredFits({ getItem() { throw new Error('blocked'); } }), []);
assert.doesNotThrow(() => appendStoredFit({ getItem() { return null; }, setItem() { throw new Error('quota'); } }, 1, 'best'));
assert.equal(compareHref(list), '/compare?tasks=4&fits=4:version:2,4:best');

console.log('test_fit_overlay ok');
