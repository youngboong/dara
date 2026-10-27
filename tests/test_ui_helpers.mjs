import assert from 'node:assert/strict';
import {
  cautionSortValue, cautionTagColor, cautionTooltip, countCautions, sortCautions,
} from '../src/dara/server/ui/src/components/fitCautions.mjs';
import {
  adoptedLabel, formatFixed, phasesLabel, phasesTooltip, tasksQuery,
} from '../src/dara/server/ui/src/components/resultsList.mjs';
import {
  detailMessage, qualityAlertItems, qualityStatText, wavelengthMismatch,
} from '../src/dara/server/ui/src/components/dataQuality.mjs';

import {
  libraryOptionText, mpKeyTag, responseDetailText, validationText,
} from '../src/dara/server/ui/src/components/libraryHelpers.mjs';

// --- fit cautions -----------------------------------------------------------
const cautions = [
  { code: 'correlated_residuals', level: 'info', message: 'dw' },
  { code: 'poor_fit', level: 'warning', message: 'poor' },
  { code: 'missing_peaks_remain', level: 'info', message: 'missing' },
  { code: 'lattice_at_bound', level: 'warning', message: 'lattice' },
];
assert.deepEqual(sortCautions(cautions).map(item => item.code),
  ['poor_fit', 'lattice_at_bound', 'correlated_residuals', 'missing_peaks_remain']);
assert.deepEqual(sortCautions(undefined), []);
assert.deepEqual(countCautions(cautions), { total: 4, warnings: 2, info: 2 });
assert.deepEqual(countCautions([]), { total: 0, warnings: 0, info: 0 });
assert.equal(countCautions(undefined), null);
assert.equal(cautionTagColor(cautions), 'orange');
assert.equal(cautionTagColor([cautions[0]]), 'default');
assert.equal(cautionTagColor(undefined), 'default');
assert.equal(cautionTooltip(cautions), 'poor\nlattice\ndw\nmissing');
assert.ok(cautionSortValue(cautions) > cautionSortValue([cautions[0]]));
assert.ok(cautionSortValue([cautions[0]]) > cautionSortValue([]));
assert.equal(cautionSortValue(undefined), Infinity);

// --- results list -----------------------------------------------------------
assert.equal(formatFixed(6.2634), '6.26');
assert.equal(formatFixed(null), '—');
assert.equal(formatFixed(undefined), '—');
assert.equal(formatFixed(NaN), '—');
assert.deepEqual(adoptedLabel('best'), { text: 'Best fit', color: 'green' });
assert.equal(adoptedLabel(2).text, 'Candidate (stored #3)');
assert.equal(adoptedLabel(0).text, 'Candidate (stored #1)');
assert.equal(adoptedLabel(null), null);
assert.equal(adoptedLabel(undefined), null);
const summary = { final_phase_labels: ['Anatase', 'Rutile', 'A', 'B', 'C'], final_phases: ['x', 'y'] };
assert.equal(phasesLabel(summary), 'Anatase, Rutile, A, … (+2)');
assert.equal(phasesLabel(summary, 10), 'Anatase, Rutile, A, B, C');
assert.equal(phasesLabel({ final_phase_labels: [] }), null);
assert.equal(phasesLabel(null), null);
assert.equal(phasesTooltip(summary), 'x\ny');
assert.equal(phasesTooltip({}), null);
assert.equal(tasksQuery({ page: 2, limit: 10 }), 'page=2&limit=10');
assert.equal(tasksQuery({ page: 1, limit: 25, user: 'a b', name: 'BiFeO3 (x)', selected: [3, 4] }),
  'page=1&limit=25&user=a%20b&name=BiFeO3%20(x)&selected=3,4');

// --- data quality -----------------------------------------------------------
const inspection = {
  load_warnings: ['2 comment lines skipped'],
  quality_checks: [
    { code: 'coarse_step', level: 'warning', message: 'step' },
    { code: 'not_counts', level: 'info', message: 'counts' },
  ],
};
assert.deepEqual(qualityAlertItems(inspection), [
  { type: 'warning', message: 'step' },
  { type: 'info', message: 'counts' },
  { type: 'info', message: 'Reading note: 2 comment lines skipped' },
]);
assert.deepEqual(qualityAlertItems(null), []);
assert.equal(qualityStatText({ median_step: 0.02, span: 70, snr_db: 47.3 }), 'step 0.020°, span 70.0°, SNR ≈ 47 dB');
assert.equal(qualityStatText({ median_step: null, span: null, snr_db: null }), null);
assert.equal(qualityStatText(undefined), null);
assert.equal(wavelengthMismatch({ code: 'wavelength_mismatch', message: 'm' }).message, 'm');
assert.equal(wavelengthMismatch('text'), null);
assert.equal(detailMessage('plain'), 'plain');
assert.equal(detailMessage({ message: 'obj' }), 'obj');
assert.equal(detailMessage(undefined, 'fallback'), 'fallback');

// --- reference library and MP key status -----------------------------------------------
assert.equal(libraryOptionText({ formula: 'ZnO', spacegroup_number: 186, filename: 'zno.cif' }), 'ZnO (SG 186) — zno.cif');
assert.equal(libraryOptionText({ filename: 'x.cif' }), 'Unknown formula (SG ?) — x.cif');
assert.equal(validationText([{ code: 'a', message: 'one' }, { message: 'two' }, 'three']), 'one\ntwo\nthree');
assert.equal(validationText(undefined), '');
assert.equal(mpKeyTag(null), null);
assert.equal(mpKeyTag({ configured: false }).color, 'orange');
assert.equal(mpKeyTag({ configured: false }).text, 'MP key not configured');
assert.deepEqual(mpKeyTag({ configured: true, valid: true, checked_at: null }), { text: 'MP key valid', color: 'green', tooltip: null });
assert.deepEqual(mpKeyTag({ configured: true, valid: false, error: 'HTTP 403' }), { text: 'MP key rejected', color: 'red', tooltip: 'HTTP 403' });
assert.equal(mpKeyTag({ configured: true, valid: null, error: null }).text, 'MP key not verified');
assert.equal(mpKeyTag({ configured: true, valid: null, error: null }).tooltip, 'The key could not be checked (offline?).');
const fakeResponse = (status, body) => ({ status, json: async () => { if (body === undefined) throw new Error('no json'); return body; } });
assert.equal(await responseDetailText(fakeResponse(409, { detail: { message: 'Already in library', id: 'x' } })), 'Already in library');
assert.equal(await responseDetailText(fakeResponse(413, { detail: 'File too large' })), 'File too large');
assert.equal(await responseDetailText(fakeResponse(500)), 'Request failed (HTTP 500)');

console.log('UI helper tests passed');
