/** Alert items for the advisory pre-check block below the upload inspection. */
export function qualityAlertItems(inspection) {
  const warnings = (inspection?.load_warnings || []).map(text => ({
    type: 'info', message: `Reading note: ${text}`,
  }));
  const checks = (inspection?.quality_checks || []).map(item => ({
    type: item.level === 'warning' ? 'warning' : 'info', message: item.message,
  }));
  return [...checks, ...warnings];
}

/** Extra description fragments (step, span, SNR) for the existing inspection line. */
export function qualityStatText(stats) {
  if (!stats) return null;
  const parts = [];
  if (Number.isFinite(stats.median_step)) parts.push(`step ${stats.median_step.toFixed(3)}°`);
  if (Number.isFinite(stats.span)) parts.push(`span ${stats.span.toFixed(1)}°`);
  if (Number.isFinite(stats.snr_db)) parts.push(`SNR ≈ ${stats.snr_db.toFixed(0)} dB`);
  return parts.length ? parts.join(', ') : null;
}

/** Parse a `wavelength_mismatch` detail object from /api/submit, or return `null`. */
export function wavelengthMismatch(detail) {
  return detail && typeof detail === 'object' && detail.code === 'wavelength_mismatch' ? detail : null;
}

/** Text of an error `detail` that may be a string or an object with `message`. */
export function detailMessage(detail, fallback = '') {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message;
  return fallback;
}
