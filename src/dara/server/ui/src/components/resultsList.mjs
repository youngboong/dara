import { adoptedTag } from './versions.mjs';

/** Two-decimal number or an em dash. */
export function formatFixed(value, digits = 2) {
  const number = Number(value);
  return value === null || value === undefined || !Number.isFinite(number) ? '—' : number.toFixed(digits);
}

/** Label for the "Adopted" column: `"best"`, a stored source index, `"version:n"`, or nothing. */
export function adoptedLabel(adopted) {
  if (adopted === 'best') return { text: 'Best fit', color: 'green' };
  if (Number.isInteger(adopted) && adopted >= 0) {
    return { text: `Candidate (stored #${adopted + 1})`, color: 'blue' };
  }
  const version = typeof adopted === 'string' ? adoptedTag(adopted) : null;
  return version ? { text: version, color: 'geekblue' } : null;
}

/** Comma-joined phase labels, shortened with an ellipsis after `limit` phases. */
export function phasesLabel(summary, limit = 3) {
  const labels = Array.isArray(summary?.final_phase_labels) ? summary.final_phase_labels : [];
  if (!labels.length) return null;
  const shown = labels.slice(0, limit).join(', ');
  return labels.length > limit ? `${shown}, … (+${labels.length - limit})` : shown;
}

export function phasesTooltip(summary) {
  const names = Array.isArray(summary?.final_phases) ? summary.final_phases : [];
  return names.length ? names.join('\n') : null;
}

/** Query string for the task list; empty values are omitted and text is encoded. */
export function tasksQuery({ page = 1, limit = 10, user, name, selected = [] } = {}) {
  const parts = [`page=${page}`, `limit=${limit}`];
  if (user) parts.push(`user=${encodeURIComponent(user)}`);
  if (name) parts.push(`name=${encodeURIComponent(name)}`);
  if (selected.length) parts.push(`selected=${selected.join(',')}`);
  return parts.join('&');
}
