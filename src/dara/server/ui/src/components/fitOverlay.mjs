// Pure helpers for overlaying calculated fits of several tasks/versions on the compare page.
import { formatSelection, parseSelection } from './versions.mjs';

export const MAX_OVERLAY_FITS = 6;
export const FITS_STORAGE_KEY = 'dara.compareFits';

/** `'1:best,2:version:2,3:0'` -> `[{taskId, sel}]` (max 6, deduplicated, invalid entries skipped). */
export function parseFitsParam(text) {
  const seen = new Set();
  const fits = [];
  for (const raw of String(text || '').split(',')) {
    const part = raw.trim();
    const colon = part.indexOf(':');
    if (colon < 0) continue;
    const idText = part.slice(0, colon);
    if (!/^\d+$/.test(idText)) continue;
    const taskId = Number(idText);
    if (!Number.isSafeInteger(taskId) || taskId <= 0) continue;
    const sel = formatSelection(part.slice(colon + 1));
    if (sel === null) continue;
    const key = `${taskId}:${sel}`;
    if (seen.has(key)) continue;
    seen.add(key);
    fits.push({ taskId, sel });
    if (fits.length >= MAX_OVERLAY_FITS) break;
  }
  return fits;
}

/** Inverse of `parseFitsParam`. */
export function fitsParam(list) {
  return (list || []).map(({ taskId, sel }) => `${taskId}:${formatSelection(sel) ?? 'best'}`).join(',');
}

/** Short label of one fit, e.g. `#12 best`, `#12 v3`, `#12 cand. 2`. */
export function fitLabel(taskId, sel) {
  const parsed = parseSelection(sel);
  const suffix = parsed === 'best' || parsed === null ? 'best fit'
    : typeof parsed === 'number' ? `cand. ${parsed + 1}` : `v${parsed.version}`;
  return `#${taskId} ${suffix}`;
}

export function sameGrid(a, b, tol = 1e-7) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (!(Math.abs(a[i] - b[i]) <= tol)) return false;
  }
  return true;
}

/** Linear interpolation of `(xFrom, yFrom)` at `xTo`; `null` outside the source range. */
export function interpolate(xFrom, yFrom, xTo) {
  const n = Math.min(xFrom?.length || 0, yFrom?.length || 0);
  if (n === 0) return (xTo || []).map(() => null);
  const lo = xFrom[0];
  const hi = xFrom[n - 1];
  return (xTo || []).map(x => {
    if (!Number.isFinite(x) || x < lo || x > hi) return null;
    let a = 0;
    let b = n - 1;
    while (b - a > 1) {
      const mid = (a + b) >> 1;
      if (xFrom[mid] <= x) a = mid; else b = mid;
    }
    const x0 = xFrom[a];
    const x1 = xFrom[b];
    const y0 = yFrom[a];
    const y1 = yFrom[b];
    if (!Number.isFinite(y0) || !Number.isFinite(y1)) return null;
    if (x1 === x0) return y0;
    return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
  });
}

const peakOf = values => (values || []).reduce((m, v) => (Number.isFinite(v) ? Math.max(m, Math.abs(v)) : m), 0);

/** Plotly layout axes matching the traces from `overlayTraces`. */
export function overlayLayout({ showDifference, normalize }) {
  const upper = showDifference ? [0.3, 1] : [0, 1];
  return {
    xaxis: { domain: [0, 1], anchor: 'y', title: showDifference ? undefined : '2θ (°)', showline: true, mirror: true, ticks: 'outside', zeroline: false },
    yaxis: { domain: upper, title: normalize ? 'Normalized intensity + offset' : 'Intensity + offset', showline: true, ticks: 'outside', zeroline: false },
    ...(showDifference ? {
      xaxis2: { domain: [0, 1], anchor: 'y2', matches: 'x', title: '2θ (°)', showline: true, mirror: true, ticks: 'outside' },
      yaxis2: { domain: [0, 0.22], title: 'A − B', zeroline: true, showline: true },
    } : {}),
  };
}

/**
 * Plotly traces for fit curves (`/fit-curves` payloads with optional `label`).
 * Options: normalize (peak -> 100), offset (vertical shift per fit), showCalculated (default true),
 * showObserved, showMarkers (Bragg ticks below zero), showDifference (A − B of the first two fits).
 */
export function overlayTraces(curves, {
  normalize = true, offset = 0, showCalculated = true, showObserved = false,
  showMarkers = false, showDifference = false,
} = {}) {
  const traces = [];
  const scaled = [];
  (curves || []).forEach((curve, index) => {
    const label = curve.label || fitLabel(curve.task_id, curve.sel);
    const reference = peakOf(curve.y_obs) || peakOf(curve.y_calc);
    const factor = normalize && reference > 0 ? 100 / reference : 1;
    const shift = index * offset;
    const map = values => (values || []).map(v => (Number.isFinite(v) ? v * factor + shift : null));
    const calc = map(curve.y_calc);
    scaled.push({ label, x: curve.x, calc, factor, shift });
    if (showObserved) {
      traces.push({ type: 'scatter', mode: 'markers', name: `Observed ${label}`, x: curve.x, y: map(curve.y_obs),
        marker: { size: 3, opacity: 0.6 }, legendgroup: `fit${index}`, meta: 'observed' });
    }
    if (showCalculated) {
      traces.push({ type: 'scatter', mode: 'lines', name: `Calculated ${label}`, x: curve.x, y: calc,
        line: { width: 1.5 }, legendgroup: `fit${index}`, meta: 'calculated' });
    }
    if (showMarkers) {
      const ticks = Object.values(curve.reflections || {}).flat().filter(Number.isFinite);
      if (ticks.length) {
        const top = normalize ? 100 : peakOf(curve.y_obs) || peakOf(curve.y_calc) || 1;
        const row = shift - top * 0.04 * (index + 1);
        traces.push({ type: 'scatter', mode: 'markers', name: `Bragg ${label}`, x: ticks, y: ticks.map(() => row),
          marker: { symbol: 'line-ns', size: 10, line: { width: 1.2 } }, legendgroup: `fit${index}`,
          hovertemplate: '2θ %{x:.3f}°<extra>%{fullData.name}</extra>', meta: 'reflection' });
      }
    }
  });
  if (showDifference && scaled.length >= 2) {
    const [a, b] = scaled;
    // Offsets are display-only: the difference compares the fits without their vertical shift.
    const unshift = (values, item) => values.map(v => (v === null ? null : v - item.shift));
    const ya = unshift(a.calc, a);
    let yb = unshift(b.calc, b);
    const interpolated = !sameGrid(a.x, b.x);
    if (interpolated) yb = interpolate(b.x, yb, a.x);
    traces.push({
      type: 'scatter', mode: 'lines', name: `A − B${interpolated ? ' (interpolated)' : ''}`,
      x: a.x, y: ya.map((v, i) => (v === null || yb[i] === null || yb[i] === undefined ? null : v - yb[i])),
      xaxis: 'x2', yaxis: 'y2', line: { width: 1.2, color: '#c0392b' }, meta: 'difference',
    });
  }
  return traces;
}

/** Entries of the localStorage list (`'taskId:sel'` strings) as parsed fits. Never throws. */
export function readStoredFits(storage) {
  try {
    const raw = storage?.getItem(FITS_STORAGE_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? parseFitsParam(list.join(',')) : [];
  } catch (_) {
    return [];
  }
}

/** Append one fit to the stored list (deduplicated, max 6, newest kept). Returns the new list. */
export function appendStoredFit(storage, taskId, sel) {
  const current = readStoredFits(storage);
  const entry = { taskId: Number(taskId), sel: formatSelection(sel) ?? 'best' };
  const next = parseFitsParam(fitsParam([...current.filter(item => !(item.taskId === entry.taskId && item.sel === entry.sel)), entry].slice(-MAX_OVERLAY_FITS)));
  try {
    storage?.setItem(FITS_STORAGE_KEY, JSON.stringify(fitsParam(next).split(',').filter(Boolean)));
  } catch (_) {
    // Storage may be blocked; the in-memory result is still returned.
  }
  return next;
}

/** `/compare?tasks=..&fits=..` link for a list of fits. */
export function compareHref(fits) {
  const ids = [...new Set((fits || []).map(item => item.taskId))];
  return `/compare?tasks=${ids.join(',')}&fits=${encodeURIComponent(fitsParam(fits)).replace(/%3A/g, ':').replace(/%2C/g, ',')}`;
}
