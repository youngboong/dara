// Keyboard shortcuts of the fit view. Pure: `actionForKey` maps a keydown event and the
// current view context to an action object; the React hook applies it.
import { formatSelection } from './versions.mjs';

export const SHORTCUT_HELP = [
  { keys: '← / →', action: 'Previous / next fit' },
  { keys: '1–9', action: 'Toggle phase n in the legend' },
  { keys: 'R', action: 'Reset zoom' },
  { keys: 'F', action: 'Fit intensity to view' },
  { keys: 'Esc', action: 'Clear peak selection, then close' },
  { keys: '?', action: 'Show shortcuts' },
];

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const TYPING_ROLES = new Set(['combobox', 'textbox', 'searchbox', 'spinbutton']);

/** True for elements where typed characters belong to the element, not to the app. */
export function isTypingTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || '').toUpperCase();
  if (TYPING_TAGS.has(tag)) return true;
  if (target.isContentEditable === true) return true;
  const attribute = name => (typeof target.getAttribute === 'function' ? target.getAttribute(name) : null);
  const editable = attribute('contenteditable');
  if (editable !== null && editable !== undefined && String(editable).toLowerCase() !== 'false') return true;
  const role = attribute('role') ?? target.role;
  return role ? TYPING_ROLES.has(String(role).toLowerCase()) : false;
}

function sameSelection(a, b) {
  return formatSelection(a) === formatSelection(b);
}

/**
 * `context` = `{options: [selection...], current: selection, phaseGroups: [name...],
 * peakSelectionCount: int, helpOpen: bool}`. Returns `null` or an action object.
 */
export function actionForKey(event, context = {}) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null;
  if (isTypingTarget(event.target)) return null;
  const key = event.key;
  if (key === 'Escape') {
    if (context.helpOpen) return { type: 'close-help' };
    if (context.peakSelectionCount > 0) return { type: 'clear-peaks' };
    return { type: 'close' };
  }
  if (key === 'ArrowLeft' || key === 'ArrowRight') {
    const options = context.options || [];
    if (options.length < 2) return null;
    let index = options.findIndex(option => sameSelection(option, context.current));
    if (index < 0) index = 0;
    const step = key === 'ArrowRight' ? 1 : -1;
    return { type: 'select', value: options[(index + step + options.length) % options.length] };
  }
  if (/^[1-9]$/.test(key)) {
    if (event.repeat) return null;
    const name = (context.phaseGroups || [])[Number(key) - 1];
    return name === undefined ? null : { type: 'toggle-phase', name };
  }
  if (key === 'r' || key === 'R') return event.repeat ? null : { type: 'reset' };
  if (key === 'f' || key === 'F') return event.repeat ? null : { type: 'fit-y' };
  if (key === '?') return event.repeat ? null : { type: 'help' };
  return null;
}
