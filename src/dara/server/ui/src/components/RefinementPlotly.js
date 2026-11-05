import React from 'react';
import createPlotlyComponent from 'react-plotly.js/factory';
import Plotly from 'plotly.js/lib/core';
import Scatter from 'plotly.js/lib/scatter';
import {
  clampRange, finiteRange, insideRange, panRange, viewChangeFromRelayout, wheelNotches, wheelZoomRange, xRangeUpdate,
} from './plotNavigation.mjs';

// The refinement view only renders scatter traces. Importing react-plotly.js
// directly pulls every Plotly trace type (maps, 3-D, finance, etc.) into the
// lazy chunk and makes the first "Visualize result" click unnecessarily slow.
Plotly.register([Scatter]);

const Plot = createPlotlyComponent(Plotly);

const PAN_BUTTONS = new Set([1, 2]); // middle, right
const CLICK_SLOP_PX = 3;

function dataXBounds(gd) {
  let lo = Infinity;
  let hi = -Infinity;
  (gd._fullData || []).forEach(trace => {
    if (trace.visible === false || !trace.x) return;
    for (let i = 0; i < trace.x.length; i += 1) {
      const value = Number(trace.x[i]);
      if (Number.isFinite(value)) { lo = Math.min(lo, value); hi = Math.max(hi, value); }
    }
  });
  return lo < hi ? [lo, hi] : null;
}

/** `{xa, px}` of the main 2θ axis under a client position inside either panel, or `null`. */
function plotAreaTarget(gd, clientX, clientY) {
  const full = gd?._fullLayout;
  if (!full?.xaxis) return null;
  const rect = gd.getBoundingClientRect();
  const px = clientX - rect.left;
  const py = clientY - rect.top;
  const xa = full.xaxis;
  if (px < xa._offset || px > xa._offset + xa._length) return null;
  for (const name of ['yaxis', 'yaxis2']) {
    const ya = full[name];
    if (ya && py >= ya._offset && py <= ya._offset + ya._length) return { xa, px: px - xa._offset };
  }
  return null;
}

/**
 * 2θ under the mouse of a click, or `null`. Peak selection uses this instead of the hovered
 * point: Plotly's hover is throttled and picks the closest point in 2-D, so right after the mouse
 * moved it can still name the previous peak (or a point of another curve).
 */
export function cursorTwoTheta(gd, mouseEvent) {
  if (!mouseEvent || !Number.isFinite(mouseEvent.clientX)) return null;
  const target = plotAreaTarget(gd, mouseEvent.clientX, mouseEvent.clientY);
  if (!target) return null;
  const x = Number(target.xa.p2l(target.px));
  return Number.isFinite(x) ? x : null;
}

// Mouse navigation for XRD plots (see plotNavigation.mjs): Plotly draws the left-drag zoom
// box (layout dragmode 'zoom'); 2θ panning, the wheel and the range clamp are handled here,
// because Plotly's own pan and scroll zoom neither stop at the data nor use a fixed step.
function attachNavigation(gd, boundsRef) {
  let drag = null;
  let frame = null;
  let pending = null;

  const bounds = () => finiteRange(boundsRef.current) || dataXBounds(gd);
  const hasX2 = () => Boolean(gd._fullLayout?.xaxis2);
  const currentRange = () => finiteRange(gd._fullLayout?.xaxis?.range);

  const inPlotArea = (clientX, clientY) => plotAreaTarget(gd, clientX, clientY);

  // One relayout per animation frame: wheel and drag events come faster than Plotly redraws.
  // The timer is a fallback for pages that get no animation frames (hidden or throttled).
  const flush = () => {
    if (frame === null) return;
    window.cancelAnimationFrame(frame.raf);
    window.clearTimeout(frame.timer);
    frame = null;
    const next = pending;
    pending = null;
    const shown = currentRange();
    if (next && !(shown && Math.abs(shown[0] - next[0]) < 1e-12 && Math.abs(shown[1] - next[1]) < 1e-12)) {
      Plotly.relayout(gd, xRangeUpdate(next, { xaxis2: hasX2() }));
    }
  };
  const apply = range => {
    pending = range;
    if (frame !== null) return;
    frame = { raf: window.requestAnimationFrame(flush), timer: window.setTimeout(flush, 50) };
  };

  const stopDrag = () => {
    drag = null;
    gd.style.cursor = '';
    window.removeEventListener('mousemove', onMove, true);
    window.removeEventListener('mouseup', onUp, true);
  };

  function onMove(event) {
    if (!drag) return;
    event.preventDefault();
    if (!drag.moved && Math.abs(event.clientX - drag.x) < CLICK_SLOP_PX && Math.abs(event.clientY - drag.y) < CLICK_SLOP_PX) return;
    drag.moved = true;
    const degreesPerPixel = (drag.range[1] - drag.range[0]) / drag.length;
    apply(panRange(drag.range, -(event.clientX - drag.x) * degreesPerPixel, bounds()));
  }

  function onUp(event) {
    if (!drag) return;
    event.preventDefault();
    event.stopPropagation();
    const { moved, button } = drag;
    stopDrag();
    // Shift+click (several peaks) must still reach the plot: Plotly never saw this mousedown,
    // so emit its click event. The 2θ comes from the mouse position: `gd._hoverdata` can still
    // hold the previously hovered peak when the click follows a quick mouse move.
    const cursorX = !moved && button === 0 ? cursorTwoTheta(gd, event) : null;
    if (cursorX !== null) gd.emit('plotly_click', { points: gd._hoverdata || [], event, cursorX });
  }

  const onDown = event => {
    const pan = PAN_BUTTONS.has(event.button) || (event.button === 0 && event.shiftKey);
    if (!pan) return;
    const target = inPlotArea(event.clientX, event.clientY);
    const range = currentRange();
    if (!target || !range) return;
    // Capture phase: Plotly's drag layer must not start a zoom box or its own (unclamped) pan.
    event.preventDefault();
    event.stopPropagation();
    drag = { x: event.clientX, y: event.clientY, button: event.button, moved: false, range, length: Math.max(target.xa._length, 1) };
    gd.style.cursor = 'grabbing';
    window.addEventListener('mousemove', onMove, true);
    window.addEventListener('mouseup', onUp, true);
  };

  const onWheel = event => {
    const target = inPlotArea(event.clientX, event.clientY);
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    const range = pending || currentRange();
    const notches = wheelNotches(event);
    if (!range || !notches) return;
    const center = target.xa.p2l(target.px);
    apply(wheelZoomRange(range, center, notches, bounds()));
  };

  const onContextMenu = event => {
    if (inPlotArea(event.clientX, event.clientY)) event.preventDefault();
  };

  // Safety net for the gestures Plotly still handles (axis drags, box zoom): a 2θ window that
  // leaves the measured range is moved back inside it.
  const onRelayout = event => {
    const change = viewChangeFromRelayout(event || {});
    if (!change.xRange) return;
    const full = bounds();
    if (insideRange(change.xRange, full)) return;
    const fixed = clampRange(change.xRange, full);
    if (fixed) Plotly.relayout(gd, xRangeUpdate(fixed, { xaxis2: hasX2() }));
  };

  gd.addEventListener('mousedown', onDown, true);
  gd.addEventListener('wheel', onWheel, { capture: true, passive: false });
  gd.addEventListener('contextmenu', onContextMenu);
  if (typeof gd.on === 'function') gd.on('plotly_relayout', onRelayout);
  return () => {
    stopDrag();
    if (frame !== null) {
      window.cancelAnimationFrame(frame.raf);
      window.clearTimeout(frame.timer);
      frame = null;
    }
    gd.removeEventListener('mousedown', onDown, true);
    gd.removeEventListener('wheel', onWheel, { capture: true });
    gd.removeEventListener('contextmenu', onContextMenu);
    if (typeof gd.removeListener === 'function') gd.removeListener('plotly_relayout', onRelayout);
  };
}

/**
 * Plotly scatter plot with the DARA navigation. `xBounds` is the measured 2θ range that zoom
 * and pan stay inside (default: the extent of the plotted data).
 */
export default function RefinementPlotly({ onInitialized, onClick, xBounds = null, ...props }) {
  const detach = React.useRef(null);
  const graph = React.useRef(null);
  const boundsRef = React.useRef(xBounds);
  boundsRef.current = xBounds;
  React.useEffect(() => () => { if (detach.current) detach.current(); }, []);
  // Every click event carries `cursorX`, the 2θ under the mouse (see cursorTwoTheta).
  const handleClick = onClick ? event => onClick({
    ...event, cursorX: Number.isFinite(event?.cursorX) ? event.cursorX : cursorTwoTheta(graph.current, event?.event),
  }) : undefined;
  return <Plot {...props} onClick={handleClick} onInitialized={(figure, gd) => {
    if (detach.current) detach.current();
    graph.current = gd;
    detach.current = attachNavigation(gd, boundsRef);
    if (onInitialized) onInitialized(figure, gd);
  }} />;
}
