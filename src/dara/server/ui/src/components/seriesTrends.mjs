// Pure helpers that turn the series detail payload into plot-ready arrays. Missing
// values stay ``null`` so Plotly draws a gap instead of a misleading zero.

export const LATTICE_PARAMETERS = ['a', 'b', 'c', 'alpha', 'beta', 'gamma'];
const ACTIVE_STATES = new Set(['READY', 'RUNNING', 'CANCELLING']);

const number = value => (value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value));
const isCompleted = item => item?.state === 'COMPLETED';
const phasesOf = item => (Array.isArray(item?.summary?.phases) ? item.summary.phases : []);

export const phaseKey = phase => phase?.label || phase?.name || '';

/** Whether the page should keep polling. */
export const hasActiveItems = items => (items || []).some(item => ACTIVE_STATES.has(item?.state));

/** Task ids in series order, optionally only the finished ones. */
export function taskIds(items, { completedOnly = false } = {}) {
  return (items || []).filter(item => !completedOnly || isCompleted(item)).map(item => item.task_id);
}

/** x values for every item: the parsed variable when every completed item has one, else order. */
export function xAxis(items) {
  const list = items || [];
  const completed = list.filter(isCompleted);
  const unit = list.map(item => item?.variable?.unit).find(Boolean);
  const useVariable = completed.length > 0 && completed.every(item => number(item.variable?.value) !== null);
  if (useVariable) {
    return {
      values: list.map(item => number(item.variable?.value)),
      title: unit || 'Variable',
      usesVariable: true,
    };
  }
  return {
    values: list.map((item, index) => (number(item?.order) ?? index) + 1),
    title: 'Sample order',
    usesVariable: false,
  };
}

/** One trace per phase identity: weight percent with its e.s.d. over the completed items. */
export function phaseTraces(items) {
  const list = (items || []).filter(isCompleted);
  const { values } = xAxis(items);
  const xs = new Map((items || []).map((item, index) => [item, values[index]]));
  const traces = new Map();
  list.forEach(item => {
    phasesOf(item).forEach(phase => {
      const label = phaseKey(phase);
      if (label && !traces.has(label)) traces.set(label, { label, x: [], y: [], err: [] });
    });
  });
  list.forEach(item => {
    const byLabel = new Map(phasesOf(item).map(phase => [phaseKey(phase), phase]));
    traces.forEach((trace, label) => {
      const phase = byLabel.get(label);
      trace.x.push(xs.get(item));
      trace.y.push(phase ? number(phase.weight_percent) : null);
      trace.err.push(phase ? number(phase.weight_esd_percent) : null);
    });
  });
  return [...traces.values()];
}

/** Phase identities available for the lattice plot, in first-seen order. */
export function phaseLabels(items) {
  return phaseTraces(items).map(trace => trace.label);
}

/** Lattice parameter (value and e.s.d.) of one phase identity across the completed items. */
export function latticeTraces(items, label, parameter) {
  const list = (items || []).filter(isCompleted);
  const { values } = xAxis(items);
  const xs = new Map((items || []).map((item, index) => [item, values[index]]));
  const trace = { label, parameter, x: [], y: [], err: [] };
  list.forEach(item => {
    const phase = phasesOf(item).find(candidate => phaseKey(candidate) === label);
    const entry = phase?.lattice?.[parameter];
    trace.x.push(xs.get(item));
    trace.y.push(number(entry?.value));
    trace.err.push(number(entry?.esd));
  });
  return trace;
}

/** Rwp and GOF over the completed items. */
export function fitTraces(items) {
  const list = (items || []).filter(isCompleted);
  const { values } = xAxis(items);
  const xs = new Map((items || []).map((item, index) => [item, values[index]]));
  return {
    x: list.map(item => xs.get(item)),
    rwp: list.map(item => number(item.summary?.final_rwp ?? item.summary?.best_rwp)),
    gof: list.map(item => number(item.summary?.gof)),
  };
}

/** {done, total} progress for the header. */
export function progress(items) {
  const list = items || [];
  return { done: list.filter(item => item?.state === 'COMPLETED').length, total: list.length };
}

export const MAX_SERIES_FILES = 50;

/** Rows for the submit-page preview table, in series order (1-based). */
export function seriesPreviewRows(preview) {
  const items = Array.isArray(preview?.items) ? preview.items : [];
  return [...items]
    .sort((a, b) => a.order - b.order)
    .map(item => ({
      filename: item.filename,
      order: item.order + 1,
      variableText: item.variable ? `${item.variable.value} ${item.variable.unit}` : '—',
    }));
}
