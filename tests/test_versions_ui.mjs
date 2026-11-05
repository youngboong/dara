import assert from 'node:assert/strict';
import {
  adoptedTag, codCodeFromStem, deltaStrip, deltaText, etaText, formatSelection, isQuickCheck, progressLabel, refineAllRequest, parseSelection,
  plotQuery, refSourceForHint, removeRequest, selectionLabel,
  alternativeOfVerdict, formatSpacegroup, spacegroupOfPhase,
  progressView, runText, existingPhase, refineAllBase, versionReadyText, versionGone, replacementOf,
  availableComparisonBase, weightRows,
  BASELINE_LABEL, backgroundRequest, displayedCandidateOf, referenceVariants, baselineRequest, baselineVersion, comparisonBase, isMissingEndpoint, needsBaseline, selectOptionGroups, selectedPhasesRequest,
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
// an index in no displayed row: name the stored number, not a displayed position
assert.equal(selectionLabel(9, data), 'Candidate (stored #10)');

// --- merged results (same phases, other database record) ---------------------------------------
const merged = {
  all_results: [
    { source_index: 1, rwp: 6.87, merged_results: [
      { source_index: 0, rwp: 7.35, differing_phases: ['Bi2O3_14_(cod_1010004)-0'] },
      { source_index: 3, rwp: 7.4, differing_phases: [] },
    ] },
    { source_index: 2, rwp: 7.0, merged_results: [] },
  ],
};
assert.equal(selectionLabel(0, merged), 'Candidate 1 · other reference (cod_1010004) — Rwp 7.35%');
assert.equal(selectionLabel(3, merged), 'Candidate 1 (stored #4) — Rwp 7.4%');
assert.equal(selectionLabel(1, merged), 'Candidate 1 — Rwp 6.87%');
assert.equal(selectionLabel(2, merged), 'Candidate 2 — Rwp 7%');
assert.equal(displayedCandidateOf(merged.all_results, 0).number, 1);
assert.equal(displayedCandidateOf(merged.all_results, 0).member.rwp, 7.35);
assert.equal(displayedCandidateOf(merged.all_results, 2).member, null);
assert.equal(displayedCandidateOf(merged.all_results, 8), null);
assert.deepEqual(referenceVariants(merged.all_results[0]).map(m => m.source_index), [0]);
const mergedOptions = selectOptionGroups(merged.all_results, [])[1].options;
// the other record follows its candidate; a reordering of the same references is not listed
assert.deepEqual(mergedOptions.map(o => o.value), [1, 0, 2]);
assert.equal(mergedOptions[1].label, 'Candidate 1 · other reference (cod_1010004) — Rwp 7.35%');
assert.equal(selectionLabel('version:3', data), 'v3 — Added Fe3O4');
// A version missing from the loaded list was deleted (replaced quick check, deleted version).
assert.equal(selectionLabel('version:7', data), 'v7 (deleted)');
assert.equal(selectionLabel('version:7', { versions: [] }), 'v7');
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

// --- alternatives / remove -------------------------------------------------------------------
// structure_model_alternative (task 18 shape) and alternative (different phase set): both are stored
// candidates; the phase table only links to them.
const modelAlt = { candidate_number: 2, source_index: 1, spacegroup_number: 14, own_spacegroup_number: 167, polymorph: true };
const setAlt = { candidate_number: 3, source_index: 2, rwp: 11.9, delta_rwp: 0.4, replaced_by: ['Calcite'] };
assert.equal(spacegroupOfPhase('CaCO3_167_(cod_1)-0'), 167);
assert.equal(spacegroupOfPhase('MyUpload'), null);
assert.equal(formatSpacegroup('P2_1/c', 14), 'P2₁/c');
assert.equal(formatSpacegroup(null, 14), 'SG 14');

// the API nests the alternatives under verdict.evidence (regression: the alternative was never found)
assert.deepEqual(alternativeOfVerdict({ level: 'ambiguous', evidence: { alternative: null, structure_model_alternative: modelAlt } }), modelAlt);
assert.deepEqual(alternativeOfVerdict({ evidence: { alternative: setAlt, structure_model_alternative: modelAlt } }), modelAlt);
assert.deepEqual(alternativeOfVerdict({ evidence: { alternative: setAlt, structure_model_alternative: null } }), setAlt);
assert.equal(alternativeOfVerdict({ evidence: { alternative: null, structure_model_alternative: null } }), null);
assert.equal(alternativeOfVerdict({ level: 'confident' }), null);
assert.equal(alternativeOfVerdict(undefined), null);
assert.deepEqual(alternativeOfVerdict({ alternative: setAlt }), setAlt, 'flat legacy shape still works');
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
// a total estimate, never a countdown; nothing without a comparable estimate
assert.equal(etaText({ status: 'running', eta_s: 20, elapsed_s: 8 }), 'about 20 s in total');
assert.equal(etaText({ status: 'running', eta_s: 20, elapsed_s: 20 }), 'about 20 s in total');
assert.equal(etaText({ status: 'running', eta_s: 20, elapsed_s: 25 }), 'taking longer than usual');
assert.equal(etaText({ status: 'running', eta_s: null, elapsed_s: 3 }), '', 'no estimate, no text');
assert.equal(etaText({ status: 'running', eta_s: 0, elapsed_s: 3 }), '');
assert.equal(etaText({ status: 'running', eta_s: 200, elapsed_s: 5 }), 'about 3 min in total');
assert.equal(etaText({ status: 'queued', position: 3, ahead: 2 }), 'Queued (2 ahead)');
assert.equal(etaText({ status: 'queued', position: 1, ahead: 0 }), 'Starting…');
assert.equal(etaText({ status: 'queued', position: 2 }), 'Queued (1 ahead)', 'older records: position is 1-based');
assert.equal(etaText({ status: 'queued', position: 1 }), 'Starting…');
assert.equal(etaText({ status: 'queued', position: 0 }), 'Starting…');
assert.equal(etaText({ status: 'queued', ahead: 1, ahead_label: 'Baseline (current code)', ahead_elapsed_s: 95 }),
  'Waiting for Baseline (current code) (running 1 min 35 s)');
assert.equal(etaText({ status: 'queued', ahead: 2, ahead_label: 'Removed X', ahead_elapsed_s: 0 }), 'Waiting for Removed X + 1 more');
assert.equal(progressLabel('Added Bi4O7 (peak search)'), 'Adding Bi4O7 (peak search)');
assert.equal(progressLabel('Swapped A for B'), 'Swapping A for B');
assert.equal(progressLabel('Baseline (current code)'), 'Baseline (current code)');
assert.equal(etaText({ status: 'done' }), '');
assert.equal(etaText(null), '');

// --- progressView: one time value per state, no capped percentage ------------------------------
{
  const queued = progressView({ status: 'queued', ahead: 1, ahead_label: 'Baseline (current code)', ahead_elapsed_s: 17 }, 40);
  assert.equal(queued.stage, 'Waiting for Baseline (current code) (running 17 s)');
  assert.equal(queued.time, '', 'queued: the queue text is the only time');
  assert.equal(queued.percent, null);
  assert.equal(progressView({ status: 'starting' }, 3).time, '');
  const unknown = progressView({ status: 'running', stage: 'Refining', eta_s: null, eta_n: 0 }, 12.4);
  assert.deepEqual(unknown, { stage: 'Refining', time: '12 s', note: '', percent: null, basis: null });
  assert.equal(progressView({ status: 'running', eta_s: null }, 0).time, '0 s');
  const known = progressView({ status: 'running', stage: 'Refining', eta_s: 40, eta_n: 3 }, 10);
  assert.equal(known.note, 'about 40 s in total');
  assert.equal(known.percent, 25);
  assert.equal(known.basis, 'Estimate from 3 similar earlier runs');
  const late = progressView({ status: 'running', eta_s: 40, eta_n: 2 }, 55);
  assert.equal(late.note, 'taking longer than usual');
  assert.equal(late.percent, null, 'past the estimate: indeterminate bar, never stuck at 95 %');
  assert.equal(late.time, '55 s');
  assert.equal(progressView(null), null);
}

// --- runText ------------------------------------------------------------------------------------
assert.equal(runText('2026-10-25T09:02:42.305000', 3060), '2026-10-25 09:02, 51 min');
assert.equal(runText('2026-10-25 09:02:42', 42.4), '2026-10-25 09:02, 42 s');
assert.equal(runText('2026-10-25 09:02:42', 7300), '2026-10-25 09:02, 2 h 2 min');
assert.equal(runText('', null), '—');
assert.equal(runText(undefined, 30), '30 s');

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

// --- code drift: automatic baseline ---------------------------------------------------------
assert.deepEqual(baselineRequest(), { base: 'best', controls: {}, origin: 'manual', label: 'Baseline (current code)' });
assert.equal(BASELINE_LABEL, 'Baseline (current code)');
const withBaseline = [{ version: 1 }, { version: 2, label: BASELINE_LABEL }, { version: 3, label: 'x' }];
assert.equal(baselineVersion(withBaseline).version, 2);
assert.equal(baselineVersion([{ version: 1 }]), null);
assert.equal(comparisonBase('best', withBaseline), 'version:2');
assert.equal(comparisonBase('best', [{ version: 1 }]), 'best');
assert.equal(comparisonBase('version:3', withBaseline), 'version:3');
assert.equal(comparisonBase(4, withBaseline), 4);
assert.equal(needsBaseline(true, [{ version: 1 }], { base: 'best', phases: [] }), true);
assert.equal(needsBaseline(false, [{ version: 1 }], { base: 'best' }), false);
assert.equal(needsBaseline(true, withBaseline, { base: 'best' }), false);
assert.equal(needsBaseline(true, [{ version: 1 }], { base: 'version:3' }), false);
assert.equal(needsBaseline(true, [{ version: 1 }], { base: 'best', label: BASELINE_LABEL }), false);
const groups = selectOptionGroups([{ source_index: 4, rwp: 7.1 }, { rwp: 8 }], withBaseline);
assert.deepEqual(groups.map(g => g.label), ['Best fit', 'Candidates', 'Versions']);
assert.deepEqual(groups[1].options.map(o => o.value), [4, 1]);
assert.deepEqual(groups[2].options.map(o => o.value), ['version:2', 'version:3']);
assert.deepEqual(selectOptionGroups([], [{ version: 1 }]).map(g => g.label), ['Best fit', 'Candidates']);

// --- "Select phases" checklist -> quick re-refinement body ------------------------------------
{
  const current = ['A_1_(x)-0', 'B_2_(y)-0'];
  const available = ['A_1_(x)-0', 'B_2_(y)-0', 'C_3_(z)-0'];
  assert.equal(selectedPhasesRequest({ current, selected: current, available }), null);
  assert.equal(selectedPhasesRequest({ current, selected: [], available }), null, 'nothing left to refine');
  assert.deepEqual(selectedPhasesRequest({ current, selected: ['A_1_(x)-0', 'C_3_(z)-0'], available }), {
    phases: [{ ref: 'C_3_(z)-0', source: 'task' }], remove: ['B_2_(y)-0'], origin: 'manual',
    label: 'Selected phases: A, C',
  });
  // displayed phases that were not offered in the checklist are left alone
  assert.deepEqual(selectedPhasesRequest({ current: [...current, 'D_4_(w)-0'], selected: ['A_1_(x)-0'], available }).remove, ['B_2_(y)-0']);
  // changed refinement controls alone make a request; unchanged defaults do not
  const defaults = { lattice: 'inherit', lattice_percent: 2, orientation: 'inherit', peak_width: 'inherit' };
  assert.equal(selectedPhasesRequest({ current, selected: current, available, controls: defaults, defaultControls: defaults }), null);
  const fixed = { ...defaults, lattice: 'fixed' };
  const onlyControls = selectedPhasesRequest({ current, selected: current, available, controls: fixed, defaultControls: defaults });
  assert.deepEqual(onlyControls.controls, fixed);
  assert.deepEqual(onlyControls.remove, []);
  assert.equal(onlyControls.label, 'Changed refinement controls');
}

// Quick checks (fast Add phase) and the full re-refinement made from them.
const quickRows = [{ version: 1 }, { version: 2, refine_mode: 'fast' }, { version: 3, refine_mode: 'full' }];
assert.equal(isQuickCheck(quickRows, 'version:2'), true);
assert.equal(isQuickCheck(quickRows, 'version:3'), false);
assert.equal(isQuickCheck(quickRows, 'best'), false);
assert.equal(isQuickCheck(quickRows, 0), false);
assert.deepEqual(refineAllRequest(2, [{ version: 2, label: 'Added Fe2O3' }]), { base: 'version:2', origin: 'refine_all', label: 'Added Fe2O3 · all phases refined' });
assert.equal(refineAllRequest(2).label, 'All phases refined');

// the adopted-fit select never offers a quick check
{
  const adoptGroups = selectOptionGroups([{ source_index: 0, rwp: 7 }], quickRows, { adoptable: true });
  assert.deepEqual(adoptGroups[2].options.map(o => o.value), ['version:3']);
  assert.deepEqual(selectOptionGroups([], quickRows)[2].options.map(o => o.value), ['version:2', 'version:3']);
  assert.deepEqual(selectOptionGroups([], [{ version: 1 }, { version: 2, refine_mode: 'fast' }], { adoptable: true }).map(g => g.label),
    ['Best fit', 'Candidates']);
  // a legacy adopted quick check is shown readably, but cannot be chosen again
  const legacyGroups = selectOptionGroups([], quickRows, { adoptable: true, current: 'version:2' });
  assert.deepEqual(legacyGroups[2].options.map(o => [o.value, o.label, Boolean(o.disabled)]),
    [['version:2', 'v2 (quick check)', true], ['version:3', legacyGroups[2].options[1].label, false]]);
  assert.deepEqual(selectOptionGroups([], quickRows, { adoptable: true, current: 'version:3' })[2].options.map(o => o.value), ['version:3']);
}

// the full fit of a quick check is compared with the quick check's own base (the check is removed)
{
  const rows = [{ version: 1 }, { version: 2, label: BASELINE_LABEL },
    { version: 3, refine_mode: 'fast', origin: { kind: 'add_peak_search', base: 'best' } },
    { version: 4, refine_mode: 'fast', origin: { kind: 'add_hint', base: 'version:3' } },
    { version: 5, refine_mode: 'fast', origin: { kind: 'add_hint', base: 2 } }];
  assert.equal(refineAllBase(rows, 3), 'version:2', 'best -> the baseline of the same code');
  assert.equal(refineAllBase(rows, 4), 'version:3');
  assert.equal(refineAllBase(rows, 5), 2);
  assert.equal(refineAllBase([{ version: 1 }], 9), 'best');
}

// a refined quick check is removed by the server: views and comparisons move to its full fit
{
  const rows = [{ version: 1 }, { version: 3, refine_mode: 'full' },
    { version: 5, origin: { kind: 'refine_all', base: 'version:4' } }, { version: 7, replaces: 6 }];
  assert.equal(versionGone(rows, 'version:4'), true);
  assert.equal(versionGone(rows, 'version:3'), false);
  assert.equal(versionGone(rows, 'best'), false);
  assert.equal(versionGone(rows, 2), false);
  assert.equal(versionGone([], 'version:4'), false, 'not loaded yet');
  assert.equal(replacementOf(rows, 4), 'version:5');
  assert.equal(replacementOf(rows, 6), 'version:7');
  assert.equal(replacementOf(rows, 3), 'best');
}

// duplicate requests reuse a version
assert.equal(versionReadyText(4, { reused: true }), 'Same fit as v4 — opened it');
assert.equal(versionReadyText(1, { reused: true }), 'Same fit as the best fit — opened it');
assert.equal(versionReadyText(5, {}, 6.123), 'Version v5 ready (Rwp 6.12 %)');
assert.equal(versionReadyText(5, null, null), 'Version v5 ready');

// phases already in the fit or added by a version are not offered again
{
  const rows = [{ version: 1, phases: ['A'] }, { version: 3, origin: { added: ['B'] } }, { version: 6, origin: { added: ['B', 'C'] } }];
  assert.deepEqual(existingPhase('A', ['A'], rows), { label: 'In this fit', version: null });
  assert.deepEqual(existingPhase('B', ['A'], rows), { label: 'In v6', version: 6 });
  assert.deepEqual(existingPhase('C', [], rows), { label: 'In v6', version: 6 });
  assert.equal(existingPhase('D', ['A'], rows), null);
  assert.equal(existingPhase(undefined, ['A'], rows), null);
  assert.equal(existingPhase('A', undefined, undefined), null);
}
console.log('quick check helpers ok');

// --- a base that is gone (replaced quick check, deleted version) ------------------------------
{
  // v8 is the full fit of quick check v7 (deleted): its stored base may still say version:7.
  const rows = [{ version: 1 }, { version: 2, label: BASELINE_LABEL }, { version: 6 }, { version: 8, replaces: 7 }];
  assert.equal(availableComparisonBase('version:7', rows, 8), 'version:2'); // gone -> best -> baseline
  assert.equal(availableComparisonBase('version:6', rows, 8), 'version:6');
  assert.equal(availableComparisonBase('best', rows, 2), 'best');
  assert.equal(availableComparisonBase(undefined, [{ version: 1 }], 3), 'best');
  assert.equal(availableComparisonBase(3, rows, 8), 3); // candidates are never "gone"
  // The adopted select keeps a deleted adopted version readable instead of a raw `version:7`.
  const groups = selectOptionGroups([], rows, { adoptable: true, current: 'version:7' });
  const deleted = groups.find(group => group.label === 'Deleted');
  assert.deepEqual(deleted.options.map(o => [o.value, o.disabled]), [['version:7', true]]);
  assert.match(deleted.options[0].label, /^v7 \(deleted\)/);
  assert.equal(selectOptionGroups([], rows, { adoptable: true, current: 'version:6' }).find(g => g.label === 'Deleted'), undefined);
  assert.equal(selectOptionGroups([], rows, { current: 'version:7' }).find(g => g.label === 'Deleted'), undefined);
}
console.log('missing bases ok');

// --- weightRows: one row per compound in the fit comparison -----------------------------------
{
  const A1 = 'Bi2O3_14_(cod_9012546)-0';
  const A2 = 'Bi2O3_14_(cod_1010004)-0';
  const B = 'Nb16.8O42_55_(cod_2106534)-None';
  const best = { weight_percent: { [A2]: 50.34, [B]: 49.66 }, phase_identity: { [A2]: 'Bi2O3_14', [B]: 'Nb16.8O42_55' } };
  const member = { weight_percent: { [A1]: 49.92, [B]: 50.08 }, phase_identity: { [A1]: 'Bi2O3_14', [B]: 'Nb16.8O42_55' } };
  const rows = weightRows(best, member);
  assert.deepEqual(rows.map(row => [row.formula, row.a, row.b]), [['Bi2O3', 50.34, 49.92], ['Nb16.8O42', 49.66, 50.08]]);
  assert.deepEqual(rows[0].names, [A2, A1]);
  // without the server map the formula + space group of the name still match
  assert.equal(weightRows({ weight_percent: best.weight_percent }, { weight_percent: member.weight_percent }).length, 2);
  // the server's reduced formula joins differently written formulas
  assert.equal(weightRows({ weight_percent: { 'Bi4O6_14_(cod_1)-0': 50 }, phase_identity: { 'Bi4O6_14_(cod_1)-0': 'Bi2O3_14' } },
    { weight_percent: { [A1]: 50 }, phase_identity: { [A1]: 'Bi2O3_14' } }).length, 1);
  // other polymorphs and unparsable names stay apart
  assert.equal(weightRows({ weight_percent: { 'TiO2_136_(cod_1)-0': 60 } }, { weight_percent: { 'TiO2_141_(cod_2)-0': 60 } }).length, 2);
  assert.deepEqual(weightRows({ weight_percent: { BiFeO3: 60 } }, { weight_percent: { BiFeO3: 55, Other: 45 } })
    .map(row => [row.key, row.a, row.b]), [['name:BiFeO3', 60, 55], ['name:Other', null, 45]]);
  // two phases of one compound in a fit are paired by weight and numbered
  const two = weightRows({ weight_percent: { [A1]: 20, [A2]: 70, [B]: 10 } }, { weight_percent: { [A1]: 80, [B]: 20 } });
  assert.deepEqual(two.map(row => [row.formula, row.a, row.b]), [['Bi2O3 #1', 70, 80], ['Bi2O3 #2', 20, null], ['Nb16.8O42', 10, 20]]);
  assert.equal(new Set(two.map(row => row.key)).size, 3);
  console.log('fit comparison weight rows ok');
}

// --- audit fixes: labels, deltas, deep link, alternative link, phase options ---------------------
import {
  addedLabel, alternativeLink, deltaBaseText, fitFromSearch, phaseLabels, searchWithFit, versionLabel, versionPhaseOptions,
} from '../src/dara/server/ui/src/components/versions.mjs';
{
  // ±0.00 instead of −0.00 / +0.00
  assert.equal(deltaText({ a: 1.3, b: 1.296, delta: -0.004, digits: 2 }), '1.30 → 1.30 (±0.00)');
  assert.equal(deltaText({ a: 1.3, b: 1.304, delta: 0.004, digits: 2 }), '1.30 → 1.30 (±0.00)');
  assert.equal(deltaText({ a: 1.3, b: 1.29, delta: -0.01, digits: 2 }), '1.30 → 1.29 (−0.01)');

  // the base of the delta strip is named
  const results = [{ source_index: 0, rwp: 3, merged_results: [{ source_index: 1, differing_phases: ['X_1_(cod_5)-0'] }] }, { source_index: 2, rwp: 4 }];
  assert.equal(deltaBaseText('best'), 'vs best fit:');
  assert.equal(deltaBaseText('version:2'), 'vs v2:');
  assert.equal(deltaBaseText(2, results), 'vs candidate 2:');
  assert.equal(deltaBaseText(1, results), 'vs candidate 1 (other reference):');

  // a merged result is not "candidate 2"
  const merged = alternativeLink({ candidate_number: 2, source_index: 1 }, results, null);
  assert.equal(merged.text, 'View candidate 1 (other reference)');
  assert.equal(merged.title, 'Show Candidate 1 · other reference (cod_5)');
  assert.equal(merged.sel, 1);
  assert.equal(alternativeLink({ source_index: 2 }, results, null).text, 'View candidate 2');
  assert.equal(alternativeLink({ source_index: 2 }, results, 2), null, 'the fit already shown');
  assert.equal(alternativeLink({ candidate_number: 3 }, results, null), null);

  // same formula twice
  const versions = [{ version: 1, phases: ['TiO2_136_(cod_1)-0'] }, { version: 11, label: 'Added Fe2O3 · all phases refined', phases: ['Fe2O3_167_(cod_2)-0'], origin: { added: ['Fe2O3_167_(cod_2)-0'] } }];
  assert.equal(addedLabel('Fe2O3', { symbol: 'R-3m', ref: 'Fe2O3_166_(cod_3)-0' }, { displayedPhases: ['TiO2_136_(cod_1)-0'], versions }), 'Added Fe2O3 (R-3m)');
  assert.equal(addedLabel('Fe2O3', { ref: 'Fe2O3_166_(cod_3)-0' }, { versions }), 'Added Fe2O3 (SG 166)');
  assert.equal(addedLabel('Fe3O4', { symbol: 'Fd-3m', ref: 'Fe3O4_227_(cod_4)-0' }, { displayedPhases: ['TiO2_136_(cod_1)-0'], versions }), 'Added Fe3O4');
  const twins = [...versions, { version: 14, label: 'Added Fe2O3 · all phases refined', origin: { added: ['Fe2O3_166_(cod_3)-0'] } }];
  assert.equal(versionLabel(twins[1], twins), 'Added Fe2O3 · all phases refined (SG 167)');
  assert.equal(versionLabel(twins[2], twins), 'Added Fe2O3 · all phases refined (SG 166)');
  assert.equal(versionLabel(versions[1], versions), 'Added Fe2O3 · all phases refined', 'unique labels stay as they are');
  assert.equal(selectionLabel('version:14', { versions: twins }), 'v14 — Added Fe2O3 · all phases refined (SG 166)');
  assert.deepEqual(phaseLabels(['Bi2O3_14_(cod_1)-0', 'Nb16.8O42_1_(cod_2)-0', 'Bi2O3_15_(cod_3)-0']),
    ['Bi2O3 (SG 14)', 'Nb16.8O42', 'Bi2O3 (SG 15)']);
  const y2o3 = weightRows({ weight_percent: { 'Y2O3_206_(cod_1)-0': 90 } }, { weight_percent: { 'Y2O3_199_(cod_2)-0': 88 } },
    name => (name.includes('206') ? 'Ia-3' : 'I2_13'));
  assert.deepEqual(y2o3.map(row => row.formula), ['Y2O3 (Ia-3)', 'Y2O3 (I2₁3)']);

  // deep link of the fit view
  assert.equal(fitFromSearch('?id=9'), undefined);
  assert.equal(fitFromSearch('?id=9&fit=best'), null);
  assert.equal(fitFromSearch('?id=9&fit=version:12'), 'version:12');
  assert.equal(fitFromSearch('?id=9&fit=version%3A12'), 'version:12');
  assert.equal(fitFromSearch('?id=9&fit=3'), 3);
  assert.equal(fitFromSearch('?id=9&fit=version:1'), null);
  assert.equal(fitFromSearch('?id=9&fit=nonsense'), undefined);
  assert.equal(searchWithFit('?id=9', 'version:12'), '?id=9&fit=version:12');
  assert.equal(searchWithFit('?id=9&fit=3', null), '?id=9&fit=best');
  assert.equal(searchWithFit('?id=9&fit=3', undefined), '?id=9');
  assert.equal(searchWithFit('?id=9', 0), '?id=9&fit=0');

  // "Select phases" also offers COD references added in versions
  assert.deepEqual(versionPhaseOptions(['A_1_(x)-0'], [{ version: 1 }, { version: 3, origin: { added: ['Fe2O3_166_(cod_9001)-0', 'Lib_1_(lib)-0'] } }]),
    [{ ref: 'A_1_(x)-0', source: 'task' }, { ref: 'Fe2O3_166_(cod_9001)-0', source: 'cod:9001' }]);
  const withCod = selectedPhasesRequest({ current: ['A_1_(x)-0'], selected: ['A_1_(x)-0', 'Fe2O3_166_(cod_9001)-0'],
    available: ['A_1_(x)-0', 'Fe2O3_166_(cod_9001)-0'], sources: { 'Fe2O3_166_(cod_9001)-0': 'cod:9001' } });
  assert.deepEqual(withCod.phases, [{ ref: 'Fe2O3_166_(cod_9001)-0', source: 'cod:9001' }]);
  console.log('audit label, delta, deep link and phase option fixes ok');
}

// --- follow-ups: repeated formulas in deltas and labels, stored-# wording, symbol lookup ----------
import { phaseSymbolLookup } from '../src/dara/server/ui/src/components/versions.mjs';
{
  const symbols = phaseSymbolLookup({
    metadata: [{ 'Fe2O3_167_(cod_2)-0': { spacegroup_symbol: 'R-3c' } }, null],
    symbols: [{ 'Fe2O3_166_(cod_3)-0': 'R-3m', 'Fe2O3_167_(cod_2)-0': 'ignored' }, undefined],
  });
  assert.equal(symbols('Fe2O3_167_(cod_2)-0'), 'R-3c', 'metadata first');
  assert.equal(symbols('Fe2O3_166_(cod_3)-0'), 'R-3m');
  assert.equal(symbols('nope'), null);

  // two Fe2O3 rows in the delta strip are told apart
  const strip = deltaStrip({ weight_percent: { 'TiO2_136_(cod_1)-0': 70, 'Fe2O3_167_(cod_2)-0': 30 } },
    { weight_percent: { 'TiO2_136_(cod_1)-0': 68, 'Fe2O3_167_(cod_2)-0': 29, 'Fe2O3_166_(cod_3)-0': 3 } }, symbols);
  assert.deepEqual(strip.filter(row => row.key.startsWith('wt:')).map(row => row.label),
    ['TiO2 wt%', 'Fe2O3 (R-3c) wt%', 'Fe2O3 (R-3m) wt%']);
  assert.deepEqual(deltaStrip({ weight_percent: { 'Fe2O3_167_(cod_2)-0': 1 } }, { weight_percent: { 'Fe2O3_166_(cod_3)-0': 1 } })
    .filter(row => row.key.startsWith('wt:')).map(row => row.label), ['Fe2O3 (SG 167) wt%', 'Fe2O3 (SG 166) wt%']);

  // the Select phases label does not repeat a formula
  const selected = selectedPhasesRequest({ current: ['TiO2_136_(cod_1)-0', 'Fe2O3_167_(cod_2)-0'],
    selected: ['TiO2_136_(cod_1)-0', 'Fe2O3_167_(cod_2)-0', 'Fe2O3_166_(cod_3)-0'],
    available: ['TiO2_136_(cod_1)-0', 'Fe2O3_167_(cod_2)-0', 'Fe2O3_166_(cod_3)-0'], symbolOf: symbols });
  assert.equal(selected.label, 'Selected phases: TiO2, Fe2O3 (R-3c), Fe2O3 (R-3m)');

  // a member that only differs in phase order is "stored #N", like mergedLabel
  const order = [{ source_index: 0, rwp: 3, merged_results: [{ source_index: 4, differing_phases: [] }] }];
  assert.equal(alternativeLink({ source_index: 4 }, order, null).text, 'View candidate 1 (stored #5)');
  assert.equal(alternativeLink({ source_index: 4 }, order, null).title, 'Show Candidate 1 (stored #5)');
  assert.equal(deltaBaseText(4, order), 'vs candidate 1 (stored #5):');

  // version rows carry their own symbols; a lookup fills in the rest
  const rows = [
    { version: 11, label: 'Added Fe2O3', origin: { added: ['Fe2O3_167_(cod_2)-0'] }, phase_symbols: { 'Fe2O3_167_(cod_2)-0': 'R-3c' } },
    { version: 14, label: 'Added Fe2O3', origin: { added: ['Fe2O3_166_(cod_3)-0'] } },
  ];
  assert.equal(versionLabel(rows[0], rows), 'Added Fe2O3 (R-3c)');
  assert.equal(versionLabel(rows[1], rows), 'Added Fe2O3 (SG 166)');
  assert.equal(versionLabel(rows[1], rows, symbols), 'Added Fe2O3 (R-3m)');
  console.log('follow-up label fixes ok');
}
