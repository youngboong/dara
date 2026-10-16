/** Plot-only transformations. Measured arrays are never modified. */
export function parseComparisonTaskIds(value) {
  return [...new Set(String(value || '').split(',').map(item => item.trim())
    .filter(item => /^\d+$/.test(item)).map(Number)
    .filter(id => Number.isSafeInteger(id) && id > 0))].slice(0, 20);
}

export function comparisonTraces(patterns, { normalize = true, offset = 110 } = {}) {
  return patterns.map((pattern, index) => {
    const peak = pattern.intensities.reduce((maximum, value) => Math.max(maximum, Math.abs(value)), 0);
    const scale = normalize && peak > 0 ? 100 / peak : 1;
    const displacement = index * offset;
    return {
      type: 'scatter',
      mode: 'lines',
      name: `#${pattern.task_id} ${pattern.name.length > 65 ? `${pattern.name.slice(0, 62)}…` : pattern.name}`,
      meta: `#${pattern.task_id} ${pattern.name}`,
      x: pattern.angles,
      y: pattern.intensities.map(value => value * scale + displacement),
      customdata: pattern.intensities,
      line: { width: 1.25 },
      hovertemplate: '2θ %{x:.4f}°<br>Measured intensity %{customdata:.4g}<extra>%{meta}</extra>',
    };
  });
}

export function measuredAngularBounds(patterns) {
  let lower = Infinity;
  let upper = -Infinity;
  for (const pattern of patterns) {
    for (const angle of pattern.angles) {
      if (Number.isFinite(angle)) {
        lower = Math.min(lower, angle);
        upper = Math.max(upper, angle);
      }
    }
  }
  return lower < upper ? [lower, upper] : undefined;
}
