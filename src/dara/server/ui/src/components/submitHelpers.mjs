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
