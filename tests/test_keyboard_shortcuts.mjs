import assert from 'node:assert/strict';
import { SHORTCUT_HELP, actionForKey, isTypingTarget } from '../src/dara/server/ui/src/components/keyboardShortcuts.mjs';

const ctx = (extra = {}) => ({
  options: ['best', 0, 1, 'version:3'], current: 'best', phaseGroups: ['A', 'B', 'C'],
  peakSelectionCount: 0, helpOpen: false, ...extra,
});
const key = (k, extra = {}) => ({ key: k, target: { tagName: 'DIV' }, ...extra });

assert.equal(SHORTCUT_HELP.length, 6);
assert.ok(SHORTCUT_HELP.every(row => row.keys && row.action));

// arrows: step and wrap-around
assert.deepEqual(actionForKey(key('ArrowRight'), ctx()), { type: 'select', value: 0 });
assert.deepEqual(actionForKey(key('ArrowLeft'), ctx()), { type: 'select', value: 'version:3' });
assert.deepEqual(actionForKey(key('ArrowRight'), ctx({ current: 'version:3' })), { type: 'select', value: 'best' });
assert.deepEqual(actionForKey(key('ArrowLeft'), ctx({ current: 0 })), { type: 'select', value: 'best' });
assert.deepEqual(actionForKey(key('ArrowRight'), ctx({ current: { version: 3 } })), { type: 'select', value: 'best' });
assert.deepEqual(actionForKey(key('ArrowRight'), ctx({ current: 'unknown' })), { type: 'select', value: 0 });
assert.equal(actionForKey(key('ArrowRight'), ctx({ options: ['best'] })), null);
assert.equal(actionForKey(key('ArrowRight'), ctx({ options: [] })), null);

// digits
assert.deepEqual(actionForKey(key('1'), ctx()), { type: 'toggle-phase', name: 'A' });
assert.deepEqual(actionForKey(key('3'), ctx()), { type: 'toggle-phase', name: 'C' });
assert.equal(actionForKey(key('4'), ctx()), null, 'out of range');
assert.equal(actionForKey(key('0'), ctx()), null);
assert.equal(actionForKey(key('1', { repeat: true }), ctx()), null, 'digit repeat ignored');

// letters
assert.deepEqual(actionForKey(key('r'), ctx()), { type: 'reset' });
assert.deepEqual(actionForKey(key('R'), ctx()), { type: 'reset' });
assert.deepEqual(actionForKey(key('f'), ctx()), { type: 'fit-y' });
assert.deepEqual(actionForKey(key('F'), ctx()), { type: 'fit-y' });
assert.deepEqual(actionForKey(key('?', { shiftKey: true }), ctx()), { type: 'help' });
assert.equal(actionForKey(key('x'), ctx()), null);

// Escape precedence: help > peaks > close
assert.deepEqual(actionForKey(key('Escape'), ctx({ helpOpen: true, peakSelectionCount: 2 })), { type: 'close-help' });
assert.deepEqual(actionForKey(key('Escape'), ctx({ peakSelectionCount: 2 })), { type: 'clear-peaks' });
assert.deepEqual(actionForKey(key('Escape'), ctx()), { type: 'close' });

// modifiers
for (const mod of ['ctrlKey', 'metaKey', 'altKey']) {
  assert.equal(actionForKey(key('r', { [mod]: true }), ctx()), null, mod);
  assert.equal(actionForKey(key('ArrowRight', { [mod]: true }), ctx()), null, mod);
}

// typing targets
const typing = [
  { tagName: 'INPUT' }, { tagName: 'textarea' }, { tagName: 'SELECT' },
  { tagName: 'DIV', isContentEditable: true },
  { tagName: 'DIV', getAttribute: n => (n === 'contenteditable' ? 'true' : null) },
  { tagName: 'DIV', getAttribute: n => (n === 'role' ? 'combobox' : null) },
  { tagName: 'DIV', getAttribute: n => (n === 'role' ? 'textbox' : null) },
];
for (const target of typing) {
  assert.equal(isTypingTarget(target), true, JSON.stringify(target));
  assert.equal(actionForKey({ key: 'r', target }, ctx()), null);
  assert.equal(actionForKey({ key: 'Escape', target }, ctx()), null);
}
assert.equal(isTypingTarget({ tagName: 'DIV', getAttribute: n => (n === 'contenteditable' ? 'false' : null) }), false);
assert.equal(isTypingTarget({ tagName: 'BUTTON' }), false);
assert.equal(isTypingTarget(null), false);
assert.equal(actionForKey(null, ctx()), null);
assert.deepEqual(actionForKey({ key: 'r', target: null }, {}), { type: 'reset' });

console.log('test_keyboard_shortcuts ok');
