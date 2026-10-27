const LEVEL_ORDER = { warning: 0, info: 1 };

/** Warnings first, otherwise keep the server's check order. */
export function sortCautions(cautions) {
  return (Array.isArray(cautions) ? cautions : [])
    .map((item, index) => ({ item, index }))
    .sort((a, b) => (LEVEL_ORDER[a.item.level] ?? 2) - (LEVEL_ORDER[b.item.level] ?? 2) || a.index - b.index)
    .map(({ item }) => item);
}

/** Counts used by the candidate table; `null` when the server sent no field (older builds). */
export function countCautions(cautions) {
  if (!Array.isArray(cautions)) return null;
  const warnings = cautions.filter(item => item.level === 'warning').length;
  return { total: cautions.length, warnings, info: cautions.length - warnings };
}

export function cautionSortValue(cautions) {
  const counts = countCautions(cautions);
  // Candidates without the field sort after those with one.
  return counts === null ? Infinity : counts.warnings * 1000 + counts.total;
}

export function cautionTagColor(cautions) {
  const counts = countCautions(cautions);
  return counts && counts.warnings > 0 ? 'orange' : 'default';
}

export function cautionTooltip(cautions) {
  return sortCautions(cautions).map(item => item.message).join('\n');
}
