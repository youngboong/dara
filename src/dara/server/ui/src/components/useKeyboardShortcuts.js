import React from 'react';
import { actionForKey } from './keyboardShortcuts.mjs';

export const FIT_MODAL_CLASS = 'dara-fit-modal';
export const HELP_MODAL_CLASS = 'dara-help-modal';

/** True when focus sits inside an antd modal other than the fit view (or its help overlay). */
export function foreignModalHasFocus(doc = document) {
  const active = doc.activeElement;
  const wrap = active && typeof active.closest === 'function' ? active.closest('.ant-modal-wrap') : null;
  if (!wrap) return false;
  return !wrap.classList.contains(FIT_MODAL_CLASS) && !wrap.classList.contains(HELP_MODAL_CLASS);
}

/**
 * One window `keydown` listener while `enabled`. `contextRef.current` is the context of
 * `actionForKey`; `handlers` maps action types to functions (always the latest closures).
 * `preventDefault` is called only when an action is returned.
 */
export default function useKeyboardShortcuts(enabled, contextRef, handlers) {
  const latest = React.useRef(handlers);
  latest.current = handlers;
  React.useEffect(() => {
    if (!enabled) return undefined;
    const onKeyDown = event => {
      if (event.defaultPrevented) return;
      if (foreignModalHasFocus()) return;
      // Esc during a right-drag zoom box only cancels the box (RefinementPlotly handles it).
      if (event.key === 'Escape' && document.querySelector('.js-plotly-plot [style*="dashed"]')) return;
      const action = actionForKey(event, contextRef.current || {});
      if (!action) return;
      const handler = latest.current?.[action.type];
      if (!handler) return;
      event.preventDefault();
      handler(action);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [enabled, contextRef]);
}
