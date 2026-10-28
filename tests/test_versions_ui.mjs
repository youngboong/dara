import assert from 'node:assert/strict';
import {
  adoptedTag, codCodeFromStem, deltaStrip, deltaText, etaText, formatSelection, parseSelection,
  plotQuery, refSourceForHint, removeRequest, selectionLabel, swapRequest,
  alternativeOfVerdict, formatSpacegroup, spacegroupOfPhase, swapKind,
  BASELINE_LABEL, DRIFT_NOTE, backgroundRequest, baselineRequest, isMissingEndpoint, showsDriftNote,
} from '../src/dara/server/ui/src/components/versions.mjs';
import { adoptedLabel } from '../src/dara/server/ui/src/components/resultsList.mjs';

// --- parseSelection / formatSelection (table-driven) -----------------------------------------
const parseCases = [
  ['best', 'best'], [0, 0], [3, 3], ['3', 3], ['version:2', { version: 2 }], ['version:1', 'best'],
  [{ version: 4 }, { version: 4 }], [{ version: 1 }, 'best'],
  ['version:0', null], ['v:2', null], [true, null], [-1, null], [1.5, null], [null, null],
  [undefined, null], ['version:', null], [{ version: 0 }, null], ['', null],
];
for (const [input, expected] of parseCases) {
  assert.deepEqual(parseSelection(input), expected, `parseSelection(${JSON.stringify(input)})`);
}
assert.equal(formatSelection('best'), 'best');
assert.equal(formatSelection(2), 2);
assert.equal(formatSelection({ version: 3 }), 'version:3');
assert.equal(formatSelection('version:3'), 'version:3');
assert.equal(formatSelection('version:1'), 'best');
assert.equal(formatSelection('nonsense'), null);

// --- selectionLabel / plotQuery ---------------------------------------------------------------
const data = {
  all_results: [{ source_index: 0, rwp: 6.5 }, { source_index: 4, rwp: 7.1 }],
  versions: [{ version: 1, label: 'Original analysis' }, { version: 3, label: 'Added Fe3O4' }],
};
assert.equal(selectionLabel('best', data), 'Best fit');
assert.equal(selectionLabel(4, data), 'Candidate 2 — Rwp 7.1%');
assert.equal(selectionLabel(9, data), 'Candidate 10');
assert.equal(selectionLabel('version:3', data), 'v3 — Added Fe3O4');
assert.equal(selectionLabel('version:7', data), 'v7');
assert.equal(selectionLabel('bogus', data), '');
assert.equal(plotQuery('best'), '');
assert.equal(plotQuery(null), '');
assert.equal(plotQuery(2), '?idx=2');
assert.equal(plotQuery(0), '?idx=0');
assert.equal(plotQuery('version:3'), '?sel=version:3');
assert.equal(plotQuery('version:1'), '');

// --- cod reference parsing --------------------------------------------------------------------
assert.equal(codCodeFromStem('Fe3O4_227_(cod_9002316)-0'), '9002316');
assert.equal(codCodeFromStem('Fe3O4_227_(mp-19306)-0.0'), null);
assert.equal(codCodeFromStem('x_(cod_12)-notanumber'), null);
assert.deepEqual(refSourceForHint({ phase: 'Fe3O4_227_(cod_9002316)-0', source: 'cod' }),
  { ref: 'Fe3O4_227_(cod_9002316)-0', source: 'cod:9002316' });
assert.deepEqual(refSourceForHint({ phase: 'Fe3O4_227_(mp-19306)-0.0', source: 'candidate' }),
  { ref: 'Fe3O4_227_(mp-19306)-0.0', source: 'task' });
assert.deepEqual(refSourceForHint({ phase: 'MyUpload', source: 'input' }), { ref: 'MyUpload', source: 'task' });
assert.equal(refSourceForHint({ phase: 'Fe3O4_227_(cod_)-0', source: 'cod' }), null);
assert.equal(refSourceForHint({ phase: 'Fe3O4_227_(cod_)-0', source: 'candidate' }), null);
assert.equal(refSourceForHint({ source: 'cod' }), null);
assert.equal(refSourceForHint(null), null);

// --- swap / remove ----------------------------------------------------------------------------
const taskResults = [
  { source_index: 0, phases: [['CaF2_225_(cod_1)-0'], ['CaCO3_167_(cod_2)-0'], ['Cr2O3_167_(cod_3)-0']],
    phase_metadata: { 'CaCO3_167_(cod_2)-0': { spacegroup_symbol: 'R-3c' } } },
  { source_index: 1, phases: [['CaF2_225_(cod_1)-0'], ['C9F8_2_(cod_4)-None'], ['Cr2O3_167_(cod_3)-0'], ['CaCO3_14_(cod_5)-1']],
    phase_metadata: { 'CaCO3_14_(cod_5)-1': { spacegroup_symbol: 'P2_1/c' } } },
  { source_index: 2, phases: [['CaF2_225_(cod_1)-0'], ['Cr2O3_167_(cod_3)-0'], ['CaCO3_14_(cod_5)-1']] },
];
const taskBase = ['CaF2_225_(cod_1)-0', 'CaCO3_167_(cod_2)-0', 'Cr2O3_167_(cod_3)-0'];
const baseMeta = { 'CaCO3_167_(cod_2)-0': { spacegroup_symbol: 'R-3c' } };

// structure_model_alternative (task 18 shape): add only the same-compound phase of the other
// space group from candidate 2, never its unrelated C9F8.
const modelAlt = { candidate_number: 2, source_index: 1, spacegroup_number: 14, own_spacegroup_number: 167, polymorph: true };
const modelSwap = swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, modelAlt, taskResults, taskBase, baseMeta);
assert.deepEqual(modelSwap, {
  phases: [{ ref: 'CaCO3_14_(cod_5)-1', source: 'task' }], remove: ['CaCO3_167_(cod_2)-0'],
  origin: 'swap', label: 'Swapped CaCO3 (R-3c) for CaCO3 (P2₁/c)',
});
const finalSet = new Set(taskBase.filter(n => !modelSwap.remove.includes(n)).concat(modelSwap.phases.map(p => p.ref)));
assert.deepEqual([...finalSet].sort(), ['CaF2_225_(cod_1)-0', 'CaCO3_14_(cod_5)-1', 'Cr2O3_167_(cod_3)-0'].sort());
assert.ok(modelSwap.label.length <= 120);
assert.equal(swapKind(modelAlt), 'model');
// without symbols the label falls back to space-group numbers
assert.equal(swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, modelAlt, [{ source_index: 1, phases: taskResults[1].phases }], taskBase).label,
  'Swapped CaCO3 (SG 167) for CaCO3 (SG 14)');
// the candidate does not hold the target space group, or it is already present -> nothing to do
assert.equal(swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, { ...modelAlt, spacegroup_number: 62 }, taskResults, taskBase), null);
assert.equal(swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, modelAlt, taskResults, [...taskBase, 'CaCO3_14_(cod_5)-1']), null);
assert.equal(swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, { ...modelAlt, source_index: 99 }, taskResults, taskBase), null);

// alternative (different phase set): switch to exactly the candidate's phases
const setAlt = { candidate_number: 3, source_index: 2, rwp: 11.9, delta_rwp: 0.4, replaced_by: ['Calcite'] };
const setSwap = swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, setAlt, taskResults, taskBase);
assert.equal(swapKind(setAlt), 'phase_set');
assert.deepEqual(setSwap.remove, ['CaCO3_167_(cod_2)-0']);
assert.deepEqual(setSwap.phases, [{ ref: 'CaCO3_14_(cod_5)-1', source: 'task' }]);
assert.equal(setSwap.origin, 'swap');
assert.ok(setSwap.label.startsWith('Switched to candidate 3 phases') && setSwap.label.length <= 120);
// candidate 2 as a phase-set switch removes what it lacks and adds all of its extra phases
const bigSwap = swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, { candidate_number: 2, source_index: 1 }, taskResults, taskBase);
assert.deepEqual(bigSwap.remove, ['CaCO3_167_(cod_2)-0']);
assert.deepEqual(bigSwap.phases.map(p => p.ref), ['C9F8_2_(cod_4)-None', 'CaCO3_14_(cod_5)-1']);
// nothing to change, or the candidate still holds the clicked phase
assert.equal(swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, { candidate_number: 1, source_index: 0 }, taskResults, taskBase), null);
assert.equal(swapRequest({ phase: 'Cr2O3_167_(cod_3)-0' }, setAlt, taskResults, taskBase), null);
// long labels are clipped to what the server accepts
const longResults = [{ source_index: 5, phases: [['Aaaaaaaaaaaaaaaaaaaaaa_1_(x)-0'], ['Bbbbbbbbbbbbbbbbbbbbbbbbb_2_(y)-0'], ['Cccccccccccccccccccccccc_3_(z)-0'], ['Dddddddddddddddddddddddd_4_(w)-0'], ['Eeeeeeeeeeeeeeeeeeeeeeeeee_5_(v)-0']] }];
const longSwap = swapRequest({ phase: 'Q_9_(q)-0' }, { candidate_number: 6, source_index: 5 }, longResults, ['Q_9_(q)-0']);
assert.ok(longSwap.label.length <= 120);
assert.equal(spacegroupOfPhase('CaCO3_167_(cod_1)-0'), 167);
assert.equal(spacegroupOfPhase('MyUpload'), null);
assert.equal(formatSpacegroup('P2_1/c', 14), 'P2₁/c');
assert.equal(formatSpacegroup(null, 14), 'SG 14');

// the API nests the alternatives under verdict.evidence (regression: the Swap button never showed)
assert.deepEqual(alternativeOfVerdict({ level: 'ambiguous', evidence: { alternative: null, structure_model_alternative: modelAlt } }), modelAlt);
assert.deepEqual(alternativeOfVerdict({ evidence: { alternative: setAlt, structure_model_alternative: modelAlt } }), modelAlt);
assert.deepEqual(alternativeOfVerdict({ evidence: { alternative: setAlt, structure_model_alternative: null } }), setAlt);
assert.equal(alternativeOfVerdict({ evidence: { alternative: null, structure_model_alternative: null } }), null);
assert.equal(alternativeOfVerdict({ level: 'confident' }), null);
assert.equal(alternativeOfVerdict(undefined), null);
assert.deepEqual(alternativeOfVerdict({ alternative: setAlt }), setAlt, 'flat legacy shape still works');
assert.equal(swapRequest({ phase: 'B' }, null, taskResults, []), null);
assert.deepEqual(removeRequest('B_2_(mp-2)-0'),
  { phases: [], remove: ['B_2_(mp-2)-0'], origin: 'remove', label: 'Remove B' });

// --- deltaStrip / deltaText -------------------------------------------------------------------
const strip = deltaStrip(
  { fit_quality: { rwp: 7.1, gof: 2.0, chi_squared: 4.0 }, weight_percent: { A: 60, B: 40 },
    peak_diagnostics: { counts_by_class: { significant: 2, minor: 1 } } },
  { rwp: 6.2, gof: 1.8, chi_squared: 3.2, weight_percent: { A: 55, C: 5 }, n_unexplained: 1 });
const byKey = Object.fromEntries(strip.map(row => [row.key, row]));
assert.equal(byKey.rwp.better, true);
assert.ok(Math.abs(byKey.rwp.delta + 0.9) < 1e-9);
assert.equal(byKey.n_unexplained.a, 3);
assert.equal(byKey.n_unexplained.delta, -2);
assert.equal(byKey['wt:A'].delta, -5);
assert.equal(byKey['wt:B'].b, null);
assert.equal(byKey['wt:B'].delta, null);
assert.equal(byKey['wt:C'].a, null);
assert.equal(deltaText(byKey.rwp), '7.10 → 6.20 (−0.90)');
assert.equal(deltaText(byKey['wt:B']), '40.00 → —');
const empty = deltaStrip(null, undefined);
assert.equal(empty.find(row => row.key === 'rwp').delta, null);
assert.equal(empty.find(row => row.key === 'rwp').better, false);

// --- adoptedTag -------------------------------------------------------------------------------
assert.equal(adoptedTag('best'), null);
assert.equal(adoptedTag(null), null);
assert.equal(adoptedTag(0), 'candidate 1 adopted');
assert.equal(adoptedTag(2), 'candidate 3 adopted');
assert.equal(adoptedTag('version:4'), 'v4 adopted');
assert.equal(adoptedTag('version:1'), null);

// results list tag
assert.deepEqual(adoptedLabel('version:3'), { text: 'v3 adopted', color: 'geekblue' });
assert.equal(adoptedLabel('version:1'), null);
assert.equal(adoptedLabel('best').text, 'Best fit');
assert.equal(adoptedLabel(1).text, 'Candidate (stored #2)');
assert.equal(adoptedLabel('nonsense'), null);

// --- etaText ----------------------------------------------------------------------------------
assert.equal(etaText({ status: 'running', eta_s: 20, elapsed_s: 8 }), '≈ 12 s left');
assert.equal(etaText({ status: 'running', eta_s: 20, elapsed_s: 25 }), '≈ 1 s left');
assert.equal(etaText({ status: 'running', eta_s: null, elapsed_s: 3 }), 'estimating…', 'no stale number');
assert.equal(etaText({ status: 'running', eta_s: 200, elapsed_s: 5 }), '≈ 3 min 15 s left');
assert.equal(etaText({ status: 'queued', position: 3, ahead: 2 }), 'Queued (2 ahead)');
assert.equal(etaText({ status: 'queued', position: 1, ahead: 0 }), 'Starting…');
assert.equal(etaText({ status: 'queued', position: 2 }), 'Queued (1 ahead)', 'older records: position is 1-based');
assert.equal(etaText({ status: 'queued', position: 1 }), 'Starting…');
assert.equal(etaText({ status: 'queued', position: 0 }), 'Starting…');
assert.equal(etaText({ status: 'done' }), '');
assert.equal(etaText(null), '');

console.log('test_versions_ui ok');

// --- isMissingEndpoint: a 404 with the handler's own detail is shown, not "not available" ------
assert.equal(isMissingEndpoint(404, 'Not Found'), true);
assert.equal(isMissingEndpoint(404, ''), true);
assert.equal(isMissingEndpoint(404, undefined), true);
assert.equal(isMissingEndpoint(404, 'Request failed (HTTP 404).'), true);
assert.equal(isMissingEndpoint(405, 'Method Not Allowed'), true);
assert.equal(isMissingEndpoint(404, 'Version 99 not found'), false);
assert.equal(isMissingEndpoint(404, 'Task not found'), false);
assert.equal(isMissingEndpoint(500, 'Not Found'), false);
assert.equal(isMissingEndpoint(200, ''), false);

// --- backgroundRequest: Auto must be able to reset an explicit degree -------------------------
assert.equal(backgroundRequest({ auto: true, degree: 8, ranges: [] }), null, 'nothing to change');
assert.equal(backgroundRequest({ auto: true, degree: 8, ranges: [], baseDegree: null }), null);
assert.deepEqual(backgroundRequest({ auto: true, degree: 8, ranges: [], baseDegree: 12 }),
  { background: { degree: null, excluded_ranges: [] }, label: 'Background: automatic' });
assert.deepEqual(backgroundRequest({ auto: false, degree: 10, ranges: [] }),
  { background: { degree: 10, excluded_ranges: [] }, label: 'Background: 10 terms' });
assert.equal(backgroundRequest({ auto: false, degree: 12, ranges: [], baseDegree: 12 }), null, 'same degree, no ranges');
assert.deepEqual(backgroundRequest({ auto: false, degree: 12, ranges: [[20, 25]], baseDegree: 12 }),
  { background: { degree: 12, excluded_ranges: [[20, 25]] }, label: 'Excluded 20–25°' });
assert.deepEqual(backgroundRequest({ auto: true, degree: 8, ranges: [[20, 25], [30, 20]], baseDegree: null }),
  { background: { degree: null, excluded_ranges: [[20, 25]] }, label: 'Excluded 20–25°' });
assert.deepEqual(backgroundRequest({ auto: true, degree: 8, ranges: [[20, 25]], baseDegree: 9 }),
  { background: { degree: null, excluded_ranges: [[20, 25]] }, label: 'Background: automatic; Excluded 20–25°' });

// --- code drift: baseline re-refinement and the note under the delta strip --------------------
assert.deepEqual(baselineRequest(), { base: 'best', controls: {}, origin: 'manual', label: 'Baseline (current code)' });
assert.equal(BASELINE_LABEL, 'Baseline (current code)');
assert.equal(showsDriftNote({ version: 2, label: 'Added Fe3O4', origin: { base: 'best' } }), true);
assert.equal(showsDriftNote({ version: 2, label: 'x', origin: { base: 'version:1' } }), true);
assert.equal(showsDriftNote({ version: 3, label: 'x', origin: { base: 'version:2' } }), false);
assert.equal(showsDriftNote({ version: 3, label: 'x', origin: { base: 4 } }), true, 'stored candidates were refined at task time');
assert.equal(showsDriftNote({ version: 2, label: BASELINE_LABEL, origin: { base: 'best' } }), false);
assert.equal(showsDriftNote(null), false);
assert.equal(DRIFT_NOTE, 'v1 was refined when the task ran; re-refine the baseline to compare with the current code.');

// --- end to end on the shape of task 18 (verdict -> evidence -> swap request) -------------------
{
  const verdict = { level: 'ambiguous', label: 'Ambiguous', reasons: ['x'], evidence: {
    presence_fraction: 1, alternative: null, structure_model_alternative: modelAlt, hamilton: null } };
  const found = alternativeOfVerdict(verdict);
  assert.ok(found, 'the Swap button needs the alternative from verdict.evidence');
  const body = swapRequest({ phase: 'CaCO3_167_(cod_2)-0' }, found, taskResults, taskBase, baseMeta);
  const next = taskBase.filter(name => !body.remove.includes(name)).concat(body.phases.map(item => item.ref));
  assert.deepEqual(next.sort(), ['CaCO3_14_(cod_5)-1', 'CaF2_225_(cod_1)-0', 'Cr2O3_167_(cod_3)-0']);
  assert.equal(body.label, 'Swapped CaCO3 (R-3c) for CaCO3 (P2₁/c)');
}
