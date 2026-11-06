import { adoptedTag } from './versions.mjs';

const STATE_TEXT = {
  READY: 'Queued', RUNNING: 'Running', COMPLETED: 'Completed', FIZZLED: 'Failed',
  CANCELLING: 'Stopping', CANCELLED: 'Cancelled',
};

/** Practitioner wording of a job state ("READY" is a queued job that has not started). */
export function stateLabel(state) {
  return STATE_TEXT[state] || state || '—';
}

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

const OPTION_LABELS = { instrument_profile: 'Instrument profile', wavelength: 'Wavelength' };

/** "Instrument profile: … · Wavelength: Cu" for the saved search options (ranges are shown separately). */
export function searchOptionsText(options) {
  if (!options || typeof options !== 'object') return '—';
  const parts = Object.entries(options)
    .filter(([key, value]) => key !== 'refinement_params' && value !== null && value !== undefined && value !== '')
    .map(([key, value]) => {
      const label = OPTION_LABELS[key] || key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
      return `${label}: ${typeof value === 'object' ? JSON.stringify(value) : value}`;
    });
  return parts.length ? parts.join(' · ') : '—';
}
