// Pure helpers for the reference CIF library and the Materials Project key status.

/** Option text for a library CIF: "{formula} (SG {n}) — {filename}". */
export function libraryOptionText(item) {
  const formula = item?.formula || 'Unknown formula';
  const group = item?.spacegroup_number == null ? 'SG ?' : `SG ${item.spacegroup_number}`;
  return `${formula} (${group}) — ${item?.filename ?? ''}`;
}

/** One line per validation message, for tooltips. */
export function validationText(validation) {
  return (Array.isArray(validation) ? validation : [])
    .map(entry => (typeof entry === 'string' ? entry : entry?.message))
    .filter(Boolean)
    .join('\n');
}

/** Tag descriptor for /api/mp-key/status, or ``null`` when the status is unavailable. */
export function mpKeyTag(status) {
  if (!status || typeof status !== 'object') return null;
  if (status.configured === false) {
    return {
      text: 'MP key not configured',
      color: 'orange',
      tooltip: 'Materials Project queries require MP_API_KEY in the server environment; restart START.cmd to enter a key.',
    };
  }
  if (status.valid === true) return { text: 'MP key valid', color: 'green', tooltip: status.checked_at ? `Checked ${status.checked_at}` : null };
  if (status.valid === false) return { text: 'MP key rejected', color: 'red', tooltip: status.error || null };
  return {
    text: 'MP key not verified',
    color: 'default',
    tooltip: status.error || 'The key could not be checked (offline?).',
  };
}

/** Error text from a failed response whose body may be ``{detail: string | {message}}``. */
export async function responseDetailText(response) {
  let detail = `Request failed (HTTP ${response.status})`;
  try {
    const body = await response.json();
    const value = body?.detail;
    if (typeof value === 'string') detail = value;
    else if (value && typeof value.message === 'string') detail = value.message;
  } catch (_) {
    // Keep the HTTP status when the body is not JSON.
  }
  return detail;
}
