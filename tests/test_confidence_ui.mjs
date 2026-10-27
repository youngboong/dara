import assert from 'node:assert/strict';
import {
  EXPORT_FORMATS, displacementTag, evidenceLines, exportHref, figureHref, selectionQuery, levelRank, overallAlertType, overallColor, reasonsText,
  screeningTags, verdictColor,
} from '../src/dara/server/ui/src/components/confidence.mjs';

// --- levels ------------------------------------------------------------------
assert.equal(overallAlertType('high'), 'success');
assert.equal(overallAlertType('check'), 'warning');
assert.equal(overallAlertType('expert'), 'error');
assert.equal(overallAlertType(undefined), 'info');
assert.equal(overallColor('high'), 'green');
assert.equal(overallColor('check'), 'gold');
assert.equal(overallColor('expert'), 'red');
assert.equal(overallColor(null), 'default');
assert.equal(verdictColor('confident'), 'green');
assert.equal(verdictColor('ambiguous'), 'gold');
assert.equal(verdictColor('weak'), 'red');
assert.equal(verdictColor('nope'), 'default');
assert.ok(levelRank('high') < levelRank('check'));
assert.ok(levelRank('check') < levelRank('expert'));
assert.ok(levelRank('confident') < levelRank('ambiguous') && levelRank('ambiguous') < levelRank('weak'));
assert.ok(levelRank('expert') < levelRank(undefined));
assert.deepEqual(reasonsText({ reasons: ['a', 'b'] }), ['a', 'b']);
assert.deepEqual(reasonsText(undefined), []);

// --- evidence lines --------------------------------------------------------------
const evidence = {
  presence_fraction: 0.8, n_candidates: 5, weight_percent: 31.2, weight_sigma: 78.04,
  alternative: { candidate_number: 2, source_index: 3, rwp: 7.9, delta_rwp: 0.3, replaced_by: ['x'] },
  hamilton: { ratio: 1.21, critical: 1.003, significant: true, b: 12, dof: 3980, alpha: 0.01 },
  computed_structure: true, lattice_at_bound: true, lattice_change_pct: 1.6,
};
assert.deepEqual(evidenceLines(evidence), [
  'Present in 80% of candidates',
  'wt%/σ = 78.0',
  'Candidate 2 without this phase: Rwp 7.90% (+0.30)',
  'Hamilton test: R = 1.210 vs critical 1.003 → significant',
  'Computed (MP) reference structure',
  'Lattice parameter at refinement limit',
  'Lattice changed by +1.60% from the reference',
]);
assert.deepEqual(evidenceLines({ presence_fraction: 0.4, hamilton: { ratio: 1, critical: 1.2, significant: false } }), [
  'Present in 40% of candidates',
  'Hamilton test: R = 1.000 vs critical 1.200 → not significant',
]);
assert.deepEqual(evidenceLines({ alternative: null, hamilton: null, weight_sigma: null }), []);
assert.deepEqual(evidenceLines(undefined), []);

// --- displacement ----------------------------------------------------------------
assert.deepEqual(
  displacementTag({ eps2: -0.0017, radius_mm: 145, displacement_mm: -0.24, level: 'info', message: 'm', convention: 'c' }),
  { text: 'Specimen displacement −0.24 mm', color: 'blue', tooltip: 'm\nc' },
);
assert.equal(displacementTag({ displacement_mm: 0.5, radius_mm: 145, level: 'ok' }).text, 'Specimen displacement +0.50 mm');
assert.equal(displacementTag({ displacement_mm: 0.5, radius_mm: 145, level: 'ok' }).color, 'default');
assert.equal(displacementTag({ displacement_mm: 2.5, radius_mm: 145, level: 'severe' }).color, 'red');
assert.equal(displacementTag({ displacement_mm: 1.1, radius_mm: 145, level: 'warning' }).color, 'orange');
const noRadius = displacementTag({ eps2: -0.0017, radius_mm: null, displacement_mm: null, level: 'unknown' });
assert.equal(noRadius.text, 'EPS2 -0.00170');
assert.equal(noRadius.color, 'default');
assert.equal(noRadius.tooltip, null);
assert.equal(displacementTag(null), null);
assert.equal(displacementTag({ level: 'ok' }), null);

// --- screening tags --------------------------------------------------------------
assert.deepEqual(screeningTags(undefined), []);
assert.deepEqual(screeningTags({ rejected: [], computed_only_phases: [] }), []);
const tags = screeningTags({
  rejected: [
    { file: 'A.cif', source: 'cod', reasons: [{ code: 'x', severity: 'error', message: 'No atoms' }, { code: 'y', message: 'Bad cell' }] },
    { file: 'B.cif', reasons: [] },
  ],
  computed_only_phases: [{ formula: 'Li2O', spacegroup: 225, file: 'mp-1.cif' }],
  mp_lattice_window: { enabled: true, range: [0.02, 0.05], applied_to: ['mp-1'] },
});
assert.deepEqual(tags.map(tag => tag.key), ['rejected', 'computed_only', 'mp_window']);
assert.equal(tags[0].text, '2 CIFs excluded by structure checks');
assert.equal(tags[0].color, 'volcano');
assert.equal(tags[0].tooltip, 'A.cif: No atoms; Bad cell\nB.cif: excluded');
assert.equal(tags[1].text, '1 phase only available as computed structures');
assert.equal(tags[1].color, 'purple');
assert.equal(tags[1].tooltip, 'Li2O (SG 225)');
assert.equal(tags[2].text, 'Widened lattice window for 1 computed structure (2.0%…5.0%)');
assert.equal(screeningTags({ mp_lattice_window: { enabled: true, range: null, applied_to: ['a', 'b'] } })[0].text,
  'Widened lattice window for 2 computed structures');
assert.deepEqual(screeningTags({ mp_lattice_window: { enabled: true, range: [0.02, 0.05], applied_to: [] } }), []);

// --- run-time control tags --------------------------------------------------------
assert.deepEqual(screeningTags({ prefilter: { applied: false, excluded: 3 } }), []);
assert.deepEqual(screeningTags({ prefilter: { applied: true, excluded: 0 }, screening: { timed_out_candidates: [], not_screened_deadline: 0 } }), []);
assert.deepEqual(screeningTags({ rejected: [], screening: {} }), []);
const many = Array.from({ length: 35 }, (_, i) => ({ file: `P${i}_1_(cod_${i})-0.cif`, score: 0.0123 * i }));
const runtimeTags = screeningTags({
  prefilter: { applied: true, excluded: 35, before: 120, excluded_candidates: many },
  screening: {
    timed_out_candidates: [{ file: 'Big_2_(mp_1)-0.cif', weight: 'very_heavy', timeout_s: 600 },
      { file: 'Huge_2_(mp_2)-0.cif', weight: 'heavy', timeout_s: 300 }],
    not_screened_deadline: 1,
    not_screened_candidates: ['Late_1_(cod_9)-0.cif'],
  },
});
assert.deepEqual(runtimeTags.map(tag => tag.key), ['prefilter', 'screening_timeout', 'not_screened']);
assert.equal(runtimeTags[0].text, '35 CIFs skipped by pre-filter');
assert.equal(runtimeTags[0].color, 'geekblue');
const prefilterLines = runtimeTags[0].tooltip.split('\n');
assert.equal(prefilterLines[0], 'Strongest calculated peaks not found in the pattern.');
assert.equal(prefilterLines[1], 'P0_1_(cod_0)-0.cif (0 %)');
assert.equal(prefilterLines[2], 'P1_1_(cod_1)-0.cif (1 %)');
assert.equal(prefilterLines.length, 1 + 30 + 1);
assert.equal(prefilterLines[31], '…');
assert.equal(runtimeTags[1].text, '2 references timed out in screening');
assert.equal(runtimeTags[1].color, 'orange');
assert.equal(runtimeTags[1].tooltip, 'Big_2_(mp_1)-0.cif (very_heavy, limit 600 s)\nHuge_2_(mp_2)-0.cif (heavy, limit 300 s)');
assert.equal(runtimeTags[2].text, '1 reference not screened (time limit)');
assert.equal(runtimeTags[2].color, 'red');
assert.equal(runtimeTags[2].tooltip, 'Late_1_(cod_9)-0.cif');
assert.equal(screeningTags({ prefilter: { applied: true, excluded: 1, excluded_candidates: [{ file: 'A.cif', score: 0.5 }] } })[0].text,
  '1 CIF skipped by pre-filter');
assert.equal(screeningTags({ prefilter: { applied: true, excluded: 1, excluded_candidates: [{ file: 'A.cif', score: 0.5 }] } })[0].tooltip,
  'Strongest calculated peaks not found in the pattern.\nA.cif (50 %)');

// --- download links --------------------------------------------------------------
assert.equal(selectionQuery(undefined), '');
assert.equal(selectionQuery('best'), '?best=true');
assert.equal(selectionQuery(null), '?best=true');
assert.equal(selectionQuery(3), '?idx=3');
assert.equal(selectionQuery(0), '?idx=0');
assert.equal(selectionQuery(-1), '');
assert.equal(figureHref('/api', 7, 'png'), '/api/task/7/figure.png');
assert.equal(figureHref('/api', 7, 'svg', 2), '/api/task/7/figure.svg?idx=2');
assert.equal(exportHref('/api', 7, 'topas', 'best'), '/api/task/7/export/topas?best=true');
assert.deepEqual(EXPORT_FORMATS.map(item => item.key), ['profex', 'topas', 'gsas2', 'highscore']);

console.log('confidence UI tests passed');
