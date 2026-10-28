import React from 'react';
import createPlotlyComponent from 'react-plotly.js/factory';
import Plotly from 'plotly.js/lib/core';
import Scatter from 'plotly.js/lib/scatter';
import { boxZoomUpdate } from './plotNavigation.mjs';

// The refinement view only renders scatter traces. Importing react-plotly.js
// directly pulls every Plotly trace type (maps, 3-D, finance, etc.) into the
// lazy chunk and makes the first "Visualize result" click unnecessarily slow.
Plotly.register([Scatter]);

const Plot = createPlotlyComponent(Plotly);

const MIN_BOX_PX = 5;

// Mouse navigation for XRD plots: left-drag pans (layout dragmode 'pan'), the
// wheel zooms (config scrollZoom) and a right-drag draws a zoom box. Plotly has
// no right-button gesture, so the box is drawn here and applied with relayout.
function attachRightDragZoom(gd) {
  let start = null;
  let box = null;

  const subplotAt = (clientX, clientY) => {
    const full = gd._fullLayout;
    if (!full) return null;
    const rect = gd.getBoundingClientRect();
    const px = clientX - rect.left;
    const py = clientY - rect.top;
    const xa = full.xaxis;
    if (!xa || px < xa._offset || px > xa._offset + xa._length) return null;
    for (const name of ['yaxis', 'yaxis2']) {
      const ya = full[name];
      if (ya && py >= ya._offset && py <= ya._offset + ya._length) return { xa, ya, name, rect };
    }
    return null;
  };

  const clear = () => {
    if (box) box.remove();
    box = null;
    start = null;
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
    window.removeEventListener('keydown', onKey);
  };

  const geometry = event => {
    const { xa, ya, rect } = start.target;
    const clamp = (value, lo, hi) => Math.min(Math.max(value, lo), hi);
    const x0 = clamp(start.x - rect.left, xa._offset, xa._offset + xa._length);
    const x1 = clamp(event.clientX - rect.left, xa._offset, xa._offset + xa._length);
    // In the residual panel only 2θ is zoomed (a vertical band).
    const yOnly = start.target.name === 'yaxis';
    const y0 = yOnly ? clamp(start.y - rect.top, ya._offset, ya._offset + ya._length) : ya._offset;
    const y1 = yOnly ? clamp(event.clientY - rect.top, ya._offset, ya._offset + ya._length) : ya._offset + ya._length;
    return { x0, x1, y0, y1 };
  };

  function onMove(event) {
    if (!start) return;
    const { x0, x1, y0, y1 } = geometry(event);
    Object.assign(box.style, {
      left: `${Math.min(x0, x1)}px`, top: `${Math.min(y0, y1)}px`,
      width: `${Math.abs(x1 - x0)}px`, height: `${Math.abs(y1 - y0)}px`,
    });
  }

  function onUp(event) {
    if (!start) return;
    const { x0, x1, y0, y1 } = geometry(event);
    const { xa, ya, name } = start.target;
    clear();
    if (Math.abs(x1 - x0) < MIN_BOX_PX) return;
    const update = boxZoomUpdate({
      xRange: [xa.p2l(x0 - xa._offset), xa.p2l(x1 - xa._offset)],
      yRange: name === 'yaxis' && Math.abs(y1 - y0) >= MIN_BOX_PX
        ? [ya.p2l(y0 - ya._offset), ya.p2l(y1 - ya._offset)] : null,
    });
    Plotly.relayout(gd, update);
  }

  function onKey(event) {
    if (event.key === 'Escape') clear();
  }

  const onDown = event => {
    if (event.button !== 2) return;
    const target = subplotAt(event.clientX, event.clientY);
    if (!target) return;
    event.preventDefault();
    event.stopPropagation();
    start = { x: event.clientX, y: event.clientY, target };
    box = document.createElement('div');
    Object.assign(box.style, {
      position: 'absolute', pointerEvents: 'none', zIndex: 10,
      border: '1px dashed #1677ff', background: 'rgba(22,119,255,0.12)',
    });
    if (getComputedStyle(gd).position === 'static') gd.style.position = 'relative';
    gd.appendChild(box);
    onMove(event);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    window.addEventListener('keydown', onKey);
  };
  const onContextMenu = event => {
    if (subplotAt(event.clientX, event.clientY)) event.preventDefault();
  };

  // Capture phase: Plotly's own drag layer must not see the right button.
  gd.addEventListener('mousedown', onDown, true);
  gd.addEventListener('contextmenu', onContextMenu);
  return () => {
    clear();
    gd.removeEventListener('mousedown', onDown, true);
    gd.removeEventListener('contextmenu', onContextMenu);
  };
}

export default function RefinementPlotly({ onInitialized, ...props }) {
  const detach = React.useRef(null);
  React.useEffect(() => () => { if (detach.current) detach.current(); }, []);
  return <Plot {...props} onInitialized={(figure, gd) => {
    if (detach.current) detach.current();
    detach.current = attachRightDragZoom(gd);
    if (onInitialized) onInitialized(figure, gd);
  }} />;
}
