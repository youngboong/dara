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

{
  const { REMEMBERED_FIELDS, pickRemembered, readRemembered, writeRemembered, forgetRemembered } = await import('../src/dara/server/ui/src/components/submitHelpers.mjs');
  const store = new Map();
  const storage = { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: k => store.delete(k) };
  const values = { user: 'Y.An', analysis_mode: 'reaction', temperature: 900, elements: ['Fe', 'O'],
    precursor_formulas: 'Fe2O3', instrument_profile: 'Aeris', wavelength_lab: 'Cu', two_theta_min: 10 };
  assert.ok(writeRemembered(storage, values, { lattice: 'fixed' }));
  const saved = readRemembered(storage);
  assert.deepEqual(saved.fields, { user: 'Y.An', analysis_mode: 'reaction', temperature: 900, instrument_profile: 'Aeris', wavelength_lab: 'Cu' });
  assert.deepEqual(saved.controls, { lattice: 'fixed' });
  assert.ok(!('elements' in saved.fields) && !('two_theta_min' in saved.fields), 'sample-specific inputs are not kept');
  assert.ok(REMEMBERED_FIELDS.includes('user'));
  assert.deepEqual(pickRemembered(null).fields, {});
  store.set('dara.submitDefaults.v1', '{broken');
  assert.equal(readRemembered(storage), null);
  forgetRemembered(storage);
  assert.equal(readRemembered(storage), null);
  assert.equal(readRemembered({ getItem: () => { throw new Error('blocked'); } }), null);
  assert.equal(writeRemembered({ setItem: () => { throw new Error('quota'); } }, values), false);
  console.log('remembered submit settings ok');
}

// advanced options: only real differences from the defaults are reported
{
  const { advancedChanges, ADVANCED_DEFAULTS } = await import('../src/dara/server/ui/src/components/submitHelpers.mjs');
  const controls = { lattice: 'inherit', lattice_percent: 2, orientation: 'inherit', peak_width: 'inherit' };
  assert.deepEqual(advancedChanges({}, controls, controls), []);
  assert.deepEqual(advancedChanges(null), []);
  assert.deepEqual(advancedChanges({ ...ADVANCED_DEFAULTS, library_cif_ids: [], additional_phases: [], reuse_additional_cifs: true }, { ...controls }, controls), []);
  assert.deepEqual(advancedChanges({ max_analysis_minutes: undefined, screening_timeout_s: undefined }), []);
  assert.deepEqual(advancedChanges({ screening_timeout_s: null }), ['screening limit']);
  assert.deepEqual(advancedChanges({ use_mp: false, max_analysis_minutes: 30 }), ['MP', 'time limit']);
  assert.deepEqual(advancedChanges({ mp_e_hull_cutoff: 0.1, candidate_prefilter: false }), ['E-hull cutoff', 'pre-filter']);
  assert.deepEqual(advancedChanges({}, { ...controls, orientation: 'SPHAR4' }, controls), ['refinement controls']);
  assert.deepEqual(advancedChanges({ library_cif_ids: ['a'], additional_phases: [{}], reuse_additional_cifs: false }),
    ['library CIFs', 'additional CIFs', 'saved CIFs not reused']);
  console.log('advanced changes ok');
}
