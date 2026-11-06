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

// --- advanced submit options ----------------------------------------------------------------
/** Defaults of the options shown in the collapsible "Advanced" card of the submit form. */
export const ADVANCED_DEFAULTS = {
  use_cod: true, use_mp: true, mp_e_hull_cutoff: 0.05, max_mp_structures: 100,
  candidate_prefilter: true, screening_timeout_s: 300, max_analysis_minutes: null,
};

const ADVANCED_LABELS = {
  use_cod: 'COD', use_mp: 'MP', mp_e_hull_cutoff: 'E-hull cutoff', max_mp_structures: 'MP limit',
  candidate_prefilter: 'pre-filter', screening_timeout_s: 'screening limit', max_analysis_minutes: 'time limit',
};

const sameControls = (a, b) => Object.keys({ ...a, ...b }).every(key => a?.[key] === b?.[key]);

/** Labels of the advanced options that differ from their defaults. Fields not yet in the form
 * (``undefined``) count as default; a blank screening limit (``null``) is a change. */
export function advancedChanges(values, controls = null, defaultControls = null) {
  const v = values || {};
  const changed = [];
  for (const [key, fallback] of Object.entries(ADVANCED_DEFAULTS)) {
    const value = v[key];
    if (value === undefined) continue;
    if ((value ?? null) !== fallback) changed.push(ADVANCED_LABELS[key]);
  }
  if (controls && defaultControls && !sameControls(controls, defaultControls)) changed.push('refinement controls');
  if (Array.isArray(v.library_cif_ids) && v.library_cif_ids.length) changed.push('library CIFs');
  if (Array.isArray(v.additional_phases) && v.additional_phases.length) changed.push('additional CIFs');
  if (v.reuse_additional_cifs === false) changed.push('saved CIFs not reused');
  return changed;
}
