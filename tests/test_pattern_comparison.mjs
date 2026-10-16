import assert from 'node:assert/strict';
import test from 'node:test';
import { comparisonTraces, measuredAngularBounds, parseComparisonTaskIds } from '../src/dara/server/ui/src/components/patternComparison.mjs';

test('task selection is unique, positive and bounded', () => {
  assert.deepEqual(parseComparisonTaskIds('1,2,2,-3,NaN,, 8 '), [1, 2, 8]);
  assert.equal(parseComparisonTaskIds(Array.from({length: 30}, (_, i) => i + 1).join(',')).length, 20);
});

test('normalization and stacking preserve original measured values', () => {
  const patterns = [
    {task_id: 1, name: 'Before', angles: [10, 20], intensities: [10, 20]},
    {task_id: 2, name: 'After', angles: [12, 22], intensities: [5, 10]},
  ];
  const before = JSON.stringify(patterns);
  const traces = comparisonTraces(patterns);
  assert.deepEqual(traces[0].y, [50, 100]);
  assert.deepEqual(traces[1].y, [160, 210]);
  assert.deepEqual(traces[1].customdata, [5, 10]);
  assert.deepEqual(comparisonTraces(patterns, {normalize: false, offset: 0})[0].y, [10, 20]);
  assert.equal(JSON.stringify(patterns), before);
  assert.deepEqual(measuredAngularBounds(patterns), [10, 22]);
});

test('zero and negative intensity data remain finite', () => {
  const traces = comparisonTraces([
    {task_id: 1, name: 'Zero', angles: [10, 20], intensities: [0, 0]},
    {task_id: 2, name: 'Negative', angles: [10, 20], intensities: [-2, -4]},
  ], {normalize: true, offset: 0});
  assert.deepEqual(traces[0].y, [0, 0]);
  assert.deepEqual(traces[1].y, [-50, -100]);
  assert.equal(measuredAngularBounds([]), undefined);
});
