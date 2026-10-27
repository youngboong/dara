import assert from 'node:assert/strict';
import { runAfterSuccess } from '../src/dara/server/ui/src/components/submitHelpers.mjs';

// every step runs even if an earlier one throws, and nothing propagates
{
  const calls = [];
  const logged = [];
  const boom = new TypeError('n is not a function');
  const errors = runAfterSuccess([
    () => calls.push('reset'),
    () => { throw boom; },
    () => calls.push('notify'),
  ], (...args) => logged.push(args));
  assert.deepEqual(calls, ['reset', 'notify']);
  assert.deepEqual(errors, [boom]);
  assert.equal(logged.length, 1);
  assert.equal(logged[0][1], boom);
}

// no failures -> no errors, nothing logged
{
  const logged = [];
  assert.deepEqual(runAfterSuccess([() => {}, () => {}], (...a) => logged.push(a)), []);
  assert.equal(logged.length, 0);
}

// a throwing logger does not break the helper
assert.equal(runAfterSuccess([() => { throw new Error('x'); }], () => { throw new Error('log'); }).length, 1);

console.log('submit helpers ok');
