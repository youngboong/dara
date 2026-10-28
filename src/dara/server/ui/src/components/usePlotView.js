import React from 'react';
import { viewChangeFromRelayout } from './plotNavigation.mjs';

// Same mouse controls as the refinement plot: left-drag pans, right-drag zooms a
// box (RefinementPlotly), the wheel zooms, double-click or reset restores the
// full view. Ranges live in React state so a reset always applies.
export const NAVIGATION_CONFIG = {
  scrollZoom: true,
  doubleClick: false,
  modeBarButtonsToRemove: ['zoom2d', 'pan2d', 'select2d', 'lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
};

export const NAVIGATION_HELP = 'Left-drag to pan, right-drag to zoom into a box, mouse wheel to zoom, double-click to reset. Hover shows 2θ and d-spacing.';

export default function usePlotView(initialXRange = null) {
  const [xRange, setXRange] = React.useState(initialXRange);
  const [yRange, setYRange] = React.useState(null);
  const [revision, setRevision] = React.useState(0);
  const reset = React.useCallback((x = null) => {
    setXRange(x);
    setYRange(null);
    setRevision(value => value + 1);
  }, []);
  const onRelayout = React.useCallback(event => {
    const change = viewChangeFromRelayout(event);
    if ('xRange' in change) setXRange(change.xRange);
    if ('yRange' in change) setYRange(change.yRange);
  }, []);
  const xaxis = xRange ? { range: [Math.min(...xRange), Math.max(...xRange)], autorange: false } : { autorange: true };
  const yaxis = yRange ? { range: [Math.min(...yRange), Math.max(...yRange)], autorange: false } : { autorange: true };
  return { xRange, yRange, setXRange: x => { setXRange(x); setYRange(null); }, xaxis, yaxis, revision, reset, onRelayout };
}
