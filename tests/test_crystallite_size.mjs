import assert from 'node:assert/strict';
import { crystalliteSizeCell } from '../src/dara/server/ui/src/components/crystalliteSize.mjs';

const refined = crystalliteSizeCell({
  grain_sizes: [{ direction: 'GrainSize(1,1,1)', value: 47.67, esd: 0.53, unit: 'nm' }],
  crystallite_size_status: 'refined',
});
assert.deepEqual(refined, { text: '47.7 ± 0.5 nm', tooltip: null });

const noEsd = crystalliteSizeCell({ grain_sizes: [{ value: 84.18, esd: null, unit: 'nm' }] });
assert.equal(noEsd.text, '84.2 nm');

const unresolvable = crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'not_resolvable' });
assert.equal(unresolvable.text, 'Not resolvable');
assert.match(unresolvable.tooltip, /larger than this instrument can resolve/);

const stale = crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'stale' });
assert.equal(stale.text, '—');
assert.equal(stale.tooltip, "Not determined in this run. Re-run 'Baseline (current code)' in Versions.");

assert.equal(crystalliteSizeCell({ grain_sizes: [], crystallite_size_status: 'not_refined' }).text, 'Not refined');
// summaries stored before the status existed
assert.deepEqual(crystalliteSizeCell({ grain_sizes: [] }), { text: '—', tooltip: null });
assert.deepEqual(crystalliteSizeCell(undefined), { text: '—', tooltip: null });

console.log('crystallite size cell tests passed');
