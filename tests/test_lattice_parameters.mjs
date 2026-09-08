import assert from 'node:assert/strict';
import {
  formatParameterWithEsd,
  latticeParameterRows,
} from '../src/dara/server/ui/src/components/latticeParameters.mjs';

const cubic = latticeParameterRows({
  a: { value: 5.463821, esd: 0.000078, unit: 'Å' },
}, 225);
assert.deepEqual(cubic.map(row => row.label), ['a = b = c', 'α = β = γ']);
assert.equal(cubic[0].constrained, true);
assert.equal(cubic[1].fixed, true);
assert.equal(formatParameterWithEsd(cubic[0].parameter), '5.463821 ± 0.000078 Å');
assert.equal(formatParameterWithEsd(cubic[1].parameter), '90°');

const trigonal = latticeParameterRows({
  a: { value: 4.98948, esd: 0.00014, unit: 'Å' },
  c: { value: 17.06849, esd: 0.00055, unit: 'Å' },
}, 167);
assert.deepEqual(trigonal.map(row => row.label), ['a = b', 'c', 'α = β', 'γ']);
assert.equal(formatParameterWithEsd(trigonal[0].parameter), '4.98948 ± 0.00014 Å');
assert.equal(formatParameterWithEsd(trigonal[1].parameter), '17.06849 ± 0.00055 Å');
assert.equal(formatParameterWithEsd(trigonal[3].parameter), '120°');

const monoclinic = latticeParameterRows({
  a: { value: 6.3, esd: null, unit: 'Å' },
  b: { value: 4.9, esd: null, unit: 'Å' },
  c: { value: 8.1, esd: null, unit: 'Å' },
  beta: { value: 107.77, esd: 0.006, unit: 'deg' },
}, 14);
assert.deepEqual(monoclinic.map(row => row.label), ['a', 'b', 'c', 'α = γ', 'β']);
assert.equal(monoclinic.find(row => row.label === 'b').constrained, false);

console.log('Lattice symmetry constraints and e.s.d. formatting passed.');
