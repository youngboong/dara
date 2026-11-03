/**
 * Run UI follow-up work after the server has confirmed a change (e.g. a submission).
 *
 * The server-side change is already done, so an exception in a follow-up step (form reset, state
 * updates, notifications) must never be reported to the user as a failure of the operation itself.
 * Every step runs even when an earlier one throws; failures are logged and returned.
 */
export function runAfterSuccess(steps, log = console.error) {
  const errors = [];
  for (const step of steps) {
    try {
      step();
    } catch (error) {
      errors.push(error);
      try { log('Post-success UI step failed (the operation itself succeeded):', error); } catch (_) { /* ignore */ }
    }
  }
  return errors;
}

// --- settings carried over from the last submission (per browser) ---------------------------
export const SUBMIT_DEFAULTS_KEY = 'dara.submitDefaults.v1';

/** Submit-form fields that stay the same between runs. Sample-specific inputs (pattern, elements,
 * precursors, 2θ range, excluded intervals, CIFs) are not carried over. */
export const REMEMBERED_FIELDS = [
  'user', 'analysis_mode', 'temperature', 'atmosphere', 'use_cod', 'use_mp', 'mp_e_hull_cutoff',
  'max_mp_structures', 'candidate_prefilter', 'screening_timeout_s', 'max_analysis_minutes',
  'instrument_profile', 'synchrotron', 'wavelength_lab', 'wavelength_sync',
];

export function pickRemembered(values, controls = null) {
  const fields = {};
  for (const key of REMEMBERED_FIELDS) {
    if (values && values[key] !== undefined) fields[key] = values[key];
  }
  return { fields, controls: controls && typeof controls === 'object' ? controls : null };
}

/** Saved settings, or ``null`` when there are none or storage is unavailable or corrupt. */
export function readRemembered(storage) {
  try {
    const parsed = JSON.parse(storage?.getItem(SUBMIT_DEFAULTS_KEY) || 'null');
    if (!parsed || typeof parsed !== 'object' || !parsed.fields || typeof parsed.fields !== 'object') return null;
    return pickRemembered(parsed.fields, parsed.controls);
  } catch (_) {
    return null;
  }
}

export function writeRemembered(storage, values, controls = null) {
  try {
    storage?.setItem(SUBMIT_DEFAULTS_KEY, JSON.stringify(pickRemembered(values, controls)));
    return true;
  } catch (_) {
    return false;
  }
}

export function forgetRemembered(storage) {
  try { storage?.removeItem(SUBMIT_DEFAULTS_KEY); } catch (_) { /* storage unavailable */ }
}
