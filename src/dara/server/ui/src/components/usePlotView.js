import React from 'react';
import { clampRange, finiteRange, viewChangeFromRelayout } from './plotNavigation.mjs';

// Same mouse controls as the refinement plot (RefinementPlotly): left-drag zooms a box,
// Shift-, right- or middle-drag pans 2θ, the wheel zooms 2θ about the cursor, double-click or
// reset restores the full view. Ranges live in React state so a reset always applies; zoom and
// pan stay inside the measured 2θ range.
export const NAVIGATION_CONFIG = {
  // RefinementPlotly handles the wheel itself (fixed step, clamped to the data).
  scrollZoom: false,
  doubleClick: false,
  // The help text covers double-click; no Plotly "Double-click to zoom back out" toast.
  showTips: false,
  modeBarButtonsToRemove: ['zoom2d', 'pan2d', 'select2d', 'lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
};

/** Plotly drag mode of every navigable plot: left-drag draws a zoom box. */
export const NAVIGATION_DRAGMODE = 'zoom';

export const NAVIGATION_HELP = 'Left-drag to zoom into a box, Shift- or right-drag to pan 2θ, mouse wheel to zoom 2θ at the cursor, double-click to reset. Hover shows 2θ and d-spacing.';

/** `full` is the measured 2θ range: the initial view and the limit of zoom and pan. */
export default function usePlotView(full = null) {
  const [xRange, setXRange] = React.useState(null);
  const [yRange, setYRange] = React.useState(null);
  const [revision, setRevision] = React.useState(0);
  const fullRange = finiteRange(full);
  const fullRef = React.useRef(fullRange);
  fullRef.current = fullRange;
  const reset = React.useCallback(() => {
    setXRange(null);
    setYRange(null);
    setRevision(value => value + 1);
  }, []);
  const onRelayout = React.useCallback(event => {
    const change = viewChangeFromRelayout(event, { full: fullRef.current });
    if ('xRange' in change) setXRange(change.xRange);
    if ('yRange' in change) setYRange(change.yRange);
  }, []);
  const shownX = xRange ? clampRange(xRange, fullRange) : fullRange;
  // Plotly writes zooms into the array it is given, so it never gets the full range itself.
  const xaxis = shownX ? { range: [...shownX], autorange: false } : { autorange: true };
  const yaxis = yRange ? { range: [Math.min(...yRange), Math.max(...yRange)], autorange: false } : { autorange: true };
  return {
    xRange, yRange, full: fullRange, xaxis, yaxis, revision, reset, onRelayout,
    setXRange: x => { setXRange(x); setYRange(null); },
  };
}
