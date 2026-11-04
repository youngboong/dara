// Crystallite-size cell of the phase table: the refined size, or one short reason.
// `crystallite_size_status` comes from the server (_phase_refinement_details).

const STATUS_TEXT = {
  not_resolvable: {
    text: 'Not resolvable',
    tooltip: 'No size broadening detected; crystallites are larger than this instrument can resolve.',
  },
  not_refined: {
    text: 'Not refined',
    tooltip: 'Peak width was held at the instrument profile; crystallite size was not refined.',
  },
  at_limit: {
    text: 'At refinement limit',
    tooltip: 'Peak broadening reached the upper bound of the refinement window; crystallites are very small and the size could not be determined.',
  },
  stale: {
    text: '—',
    tooltip: "Not determined in this run. Re-run 'Baseline (current code)' in Versions.",
  },
};

export function formatGrainSize(value) {
  const esd = value.esd == null ? '' : ` ± ${Number(value.esd).toFixed(1)}`;
  return `${Number(value.value).toFixed(1)}${esd} ${value.unit}`;
}

/** `{ text, tooltip }` for one phase-details row; tooltip is null for a refined size. */
export function crystalliteSizeCell(row) {
  if (row?.grain_sizes?.length) {
    return { text: row.grain_sizes.map(formatGrainSize).join('; '), tooltip: null };
  }
  // results stored before the status existed (old version summaries) fall back to a dash
  return STATUS_TEXT[row?.crystallite_size_status] || { text: '—', tooltip: null };
}
