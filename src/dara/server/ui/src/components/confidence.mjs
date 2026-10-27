// Pure helpers for the evidence-based confidence display (overall banner, per-phase
// verdicts, specimen displacement and candidate-screening tags). Every function accepts
// missing input because tasks stored before these features carry none of these keys.

const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const fixed = (value, digits) => Number(value).toFixed(digits);
const signed = (value, digits) => `${Number(value) >= 0 ? '+' : '−'}${Math.abs(Number(value)).toFixed(digits)}`;
const plural = (count, singular, pluralForm = `${singular}s`) => `${count} ${count === 1 ? singular : pluralForm}`;

const OVERALL_ALERT = { high: 'success', check: 'warning', expert: 'error' };
const OVERALL_COLOR = { high: 'green', check: 'gold', expert: 'red' };
const VERDICT_COLOR = { confident: 'green', ambiguous: 'gold', weak: 'red' };
const RANK = { high: 0, confident: 0, check: 1, ambiguous: 1, expert: 2, weak: 2 };

export const overallAlertType = level => OVERALL_ALERT[level] || 'info';
export const overallColor = level => OVERALL_COLOR[level] || 'default';
export const verdictColor = level => VERDICT_COLOR[level] || 'default';

/** Sort key: better evidence first; unknown levels last. */
export const levelRank = level => (level in RANK ? RANK[level] : 3);

/** Short evidence statements behind one phase verdict, in a fixed reading order. */
export function evidenceLines(evidence) {
  if (!evidence || typeof evidence !== 'object') return [];
  const lines = [];
  if (finite(evidence.presence_fraction)) {
    lines.push(`Present in ${Math.round(Number(evidence.presence_fraction) * 100)}% of candidates`);
  }
  if (finite(evidence.weight_sigma)) lines.push(`wt%/σ = ${fixed(evidence.weight_sigma, 1)}`);
  const alt = evidence.alternative;
  if (alt && finite(alt.rwp)) {
    const delta = finite(alt.delta_rwp) ? ` (+${fixed(Math.abs(Number(alt.delta_rwp)), 2)})` : '';
    const number = finite(alt.candidate_number) ? `Candidate ${alt.candidate_number}` : 'A candidate';
    lines.push(`${number} without this phase: Rwp ${fixed(alt.rwp, 2)}%${delta}`);
  }
  const hamilton = evidence.hamilton;
  if (hamilton && finite(hamilton.ratio) && finite(hamilton.critical)) {
    lines.push(`Hamilton test: R = ${fixed(hamilton.ratio, 3)} vs critical ${fixed(hamilton.critical, 3)} → ${
      hamilton.significant ? 'significant' : 'not significant'}`);
  }
  if (evidence.computed_structure) lines.push('Computed (MP) reference structure');
  if (evidence.lattice_at_bound) lines.push('Lattice parameter at refinement limit');
  if (finite(evidence.lattice_change_pct)) {
    lines.push(`Lattice changed by ${signed(evidence.lattice_change_pct, 2)}% from the reference`);
  }
  return lines;
}

/** Tag text, colour and tooltip for the sample-displacement estimate, or ``null``. */
export function displacementTag(sd) {
  if (!sd || typeof sd !== 'object') return null;
  let text = null;
  if (finite(sd.radius_mm) && finite(sd.displacement_mm)) {
    text = `Specimen displacement ${signed(sd.displacement_mm, 2)} mm`;
  } else if (finite(sd.eps2)) {
    text = `EPS2 ${fixed(sd.eps2, 5)}`;
  }
  if (!text) return null;
  const color = { info: 'blue', warning: 'orange', severe: 'red' }[sd.level] || 'default';
  const tooltip = [sd.message, sd.convention].filter(Boolean).join('\n') || null;
  return { text, color, tooltip };
}

const reasonText = reason => (typeof reason === 'string' ? reason : reason?.message || reason?.code || '');

/** Tags describing what candidate screening removed or widened (F2). */
export function screeningTags(candidateSummary) {
  const summary = candidateSummary || {};
  const tags = [];
  const rejected = Array.isArray(summary.rejected) ? summary.rejected : [];
  if (rejected.length) {
    tags.push({
      key: 'rejected',
      color: 'volcano',
      text: `${plural(rejected.length, 'CIF')} excluded by structure checks`,
      tooltip: rejected
        .map(item => `${item.file}: ${(item.reasons || []).map(reasonText).filter(Boolean).join('; ') || 'excluded'}`)
        .join('\n'),
    });
  }
  const computed = Array.isArray(summary.computed_only_phases) ? summary.computed_only_phases : [];
  if (computed.length) {
    tags.push({
      key: 'computed_only',
      color: 'purple',
      text: `${plural(computed.length, 'phase')} only available as computed structures`,
      tooltip: computed
        .map(item => `${item.formula ?? item.file}${item.spacegroup ? ` (SG ${item.spacegroup})` : ''}`)
        .join('\n'),
    });
  }
  const window = summary.mp_lattice_window;
  const applied = Array.isArray(window?.applied_to) ? window.applied_to : [];
  if (applied.length) {
    const range = Array.isArray(window.range) && window.range.length === 2 && window.range.every(finite)
      ? window.range.map(Number) : null;
    // Ranges are relative fractions (0.05 = 5 %); accept values already given in percent.
    const percent = range ? range.map(value => (Math.abs(range[1]) <= 1 ? value * 100 : value)) : null;
    const span = percent ? ` (${fixed(percent[0], 1)}%…${fixed(percent[1], 1)}%)` : '';
    tags.push({
      key: 'mp_window',
      color: 'default',
      text: `Widened lattice window for ${plural(applied.length, 'computed structure')}${span}`,
      tooltip: applied.join('\n'),
    });
  }

  // Run-time controls: pre-filter, screening time-outs, references skipped at the time limit.
  const prefilter = summary.prefilter;
  const excludedCount = Number(prefilter?.excluded);
  if (prefilter?.applied && Number.isFinite(excludedCount) && excludedCount > 0) {
    const items = Array.isArray(prefilter.excluded_candidates) ? prefilter.excluded_candidates : [];
    const lines = items.slice(0, 30).map(item => `${item.file}${finite(item.score) ? ` (${Math.round(Number(item.score) * 100)} %)` : ''}`);
    tags.push({
      key: 'prefilter',
      color: 'geekblue',
      text: `${plural(excludedCount, 'CIF')} skipped by pre-filter`,
      tooltip: ['Strongest calculated peaks not found in the pattern.', ...lines].join('\n') + (items.length > 30 ? '\n…' : ''),
    });
  }
  const screening = summary.screening;
  const timedOut = Array.isArray(screening?.timed_out_candidates) ? screening.timed_out_candidates : [];
  if (timedOut.length) {
    tags.push({
      key: 'screening_timeout',
      color: 'orange',
      text: `${plural(timedOut.length, 'reference')} timed out in screening`,
      tooltip: timedOut
        .map(item => `${item.file} (${item.weight ?? 'unknown weight'}, limit ${item.timeout_s} s)`)
        .join('\n'),
    });
  }
  const notScreened = Number(screening?.not_screened_deadline);
  if (Number.isFinite(notScreened) && notScreened > 0) {
    const names = Array.isArray(screening.not_screened_candidates) ? screening.not_screened_candidates : [];
    tags.push({
      key: 'not_screened',
      color: 'red',
      text: `${plural(notScreened, 'reference')} not screened (time limit)`,
      tooltip: names.slice(0, 30).join('\n') + (names.length > 30 ? '\n…' : ''),
    });
  }
  return tags;
}

/** Reasons list for a tooltip: overall or phase confidence object. */
export function reasonsText(confidenceItem) {
  const reasons = Array.isArray(confidenceItem?.reasons) ? confidenceItem.reasons : [];
  return reasons.map(reasonText).filter(Boolean);
}

export const EXPORT_FORMATS = [
  { key: 'profex', label: 'Profex / BGMN (.sav + .str)' },
  { key: 'topas', label: 'TOPAS (.inp starting model)' },
  { key: 'gsas2', label: 'GSAS-II (CIF + data)' },
  { key: 'highscore', label: 'HighScore (CIF + data)' },
];

/**
 * Query string for figure/export downloads. ``undefined`` lets the server pick the adopted
 * result (or the best fit); ``'best'`` forces the minimum-Rwp fit; an integer picks a candidate.
 */
export function selectionQuery(selection) {
  if (selection === 'best' || selection === null) return '?best=true';
  return Number.isInteger(selection) && selection >= 0 ? `?idx=${selection}` : '';
}

export const figureHref = (api, taskId, format, selection) =>
  `${api}/task/${taskId}/figure.${format}${selectionQuery(selection)}`;

export const exportHref = (api, taskId, format, selection) =>
  `${api}/task/${taskId}/export/${format}${selectionQuery(selection)}`;

const OVERALL_LABEL = { high: 'High confidence', check: 'Check recommended', expert: 'Expert review recommended' };
export const overallLabel = level => OVERALL_LABEL[level] || null;
