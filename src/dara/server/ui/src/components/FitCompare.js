import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Modal, Select, Space, Spin, Table, Tag, Tooltip } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import usePlotView, { NAVIGATION_CONFIG, NAVIGATION_DRAGMODE, NAVIGATION_HELP } from './usePlotView';
import { addDSpacingHover } from './plotNavigation.mjs';
import { NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import {
  QUICK_CHECK_HINT, REFINE_ALL_FIRST, formatSelection, isQuickCheck, phaseSymbolLookup, selectOptionGroups, selectionLabel, weightRows,
} from './versions.mjs';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });

const fixed = (value, digits = 2) => (value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits));
const count = value => (Array.isArray(value) ? value.length : value == null ? '—' : Number(value));
// A skipped check (no precursor amounts) has no level: show nothing rather than "skipped".
const massBalanceText = block => (block?.level ? String(block.level) : '—');

const DISCLAIMER = 'Lower Rwp alone does not prove correctness: compare residuals, phase chemistry and physical plausibility too.';

function verdictTag(verdict) {
  if (!verdict) return null;
  const delta = verdict.rwp_delta == null ? '' : ` (ΔRwp ${verdict.rwp_delta > 0 ? '+' : verdict.rwp_delta < 0 ? '−' : ''}${Math.abs(verdict.rwp_delta).toFixed(2)})`;
  const tag = verdict.better === 'b' ? <Tag color="green">B fits better{delta}</Tag>
    : verdict.better === 'a' ? <Tag color="orange">A fits better{delta}</Tag>
      : <Tag>Similar fit{delta}</Tag>;
  // A quick-check note is already the warning banner: the tooltip keeps to the general caveat.
  return <Tooltip title={DISCLAIMER}>{tag}</Tooltip>;
}

function regionBounds(region) {
  if (Array.isArray(region.range) && region.range.length === 2) return region.range;
  if (Number.isFinite(region.start) && Number.isFinite(region.end)) return [region.start, region.end];
  return [region.two_theta - 0.5, region.two_theta + 0.5];
}

/**
 * Side-by-side comparison of two fits (any selections) of one task; the single comparison window.
 * A and B are chosen with the selects (`onSelect(a, b)` updates the parent's state). A quick check
 * on either side is flagged, and B cannot be adopted while it is one.
 */
export default function FitCompare({ taskId, a, b, open, onClose, onAdoptB, adopting, onViewB, allResults = [], versions = [], onSelect }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  // The view starts at (and never leaves) the measured 2θ range, not Plotly's padded autorange.
  const xs = data?.x || [];
  const view = usePlotView(xs.length > 1 ? [Number(xs[0]), Number(xs[xs.length - 1])] : null);
  const selA = formatSelection(a);
  const selB = formatSelection(b);
  React.useEffect(() => {
    if (!open || selA === null || selB === null) return undefined;
    const controller = new AbortController();
    setData(null);
    setError('');
    view.reset();
    requestJson(`/task/${taskId}/fit-compare?a=${encodeURIComponent(selA)}&b=${encodeURIComponent(selB)}`, { signal: controller.signal })
      .then(setData)
      .catch(e => { if (e.name !== 'AbortError') setError(e.missing ? NOT_AVAILABLE : e.message); });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, taskId, selA, selB]);

  const groups = selectOptionGroups(allResults, versions);
  const names = { all_results: allResults, versions };
  const labelA = selectionLabel(selA, names) || String(selA);
  const labelB = selectionLabel(selB, names) || String(selB);
  const quickA = isQuickCheck(versions, selA) || data?.a?.refine_mode === 'fast';
  const quickB = isQuickCheck(versions, selB) || data?.b?.refine_mode === 'fast';
  // one row per compound: the same phase from another database record shares it
  const symbolOf = phaseSymbolLookup({ metadata: allResults.map(result => result.phase_metadata),
    symbols: [data?.a?.phase_symbols, data?.b?.phase_symbols, ...versions.map(item => item.phase_symbols)] });
  const weights = data ? weightRows(data.a, data.b, symbolOf) : [];
  const massBalance = data && (data.a?.mass_balance?.level || data.b?.mass_balance?.level);
  const rows = data ? [
    { key: 'rwp', metric: 'Rwp (%)', a: fixed(data.a?.rwp), b: fixed(data.b?.rwp) },
    { key: 'gof', metric: 'GOF', a: fixed(data.a?.gof), b: fixed(data.b?.gof) },
    ...weights.map(row => ({ key: `wt:${row.key}`, metric: `${row.formula} (wt%)`, title: row.names.join(' / '),
      a: fixed(row.a), b: fixed(row.b) })),
    ...(massBalance ? [{ key: 'mb', metric: 'Mass balance', a: massBalanceText(data.a?.mass_balance), b: massBalanceText(data.b?.mass_balance) }] : []),
    { key: 'unexplained', metric: 'Unexplained peaks', a: count(data.a?.n_unexplained), b: count(data.b?.n_unexplained) },
    { key: 'cautions', metric: 'Cautions', a: count(data.a?.cautions), b: count(data.b?.cautions) },
  ] : [];
  const regions = data?.regions || [];
  const shapes = regions.flatMap(region => {
    const [x0, x1] = regionBounds(region);
    return [
      { type: 'rect', xref: 'x', yref: 'y domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(255,193,7,0.18)', line: { width: 0 }, layer: 'below' },
      { type: 'rect', xref: 'x2', yref: 'y2 domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(255,193,7,0.18)', line: { width: 0 }, layer: 'below' },
    ];
  });
  const adoptBlocked = quickB ? REFINE_ALL_FIRST : null;
  return <Modal title="Compare fits" open={open} width="calc(100vw - 32px)" style={{ top: 16 }} bodyStyle={{ padding: '12px 16px' }}
    footer={null} onCancel={onClose} wrapClassName="dara-compare-modal" destroyOnClose>
    <Space wrap style={{ marginBottom: 12 }}>
      <Tag color="blue">A</Tag>
      <Select aria-label="Fit A" style={{ minWidth: 340, maxWidth: '40vw' }} value={selA ?? undefined} options={groups}
        onChange={value => onSelect?.(value, selB)} disabled={!onSelect} />
      <Tag color="orange">B</Tag>
      <Select aria-label="Fit B" style={{ minWidth: 340, maxWidth: '40vw' }} value={selB ?? undefined} options={groups}
        onChange={value => onSelect?.(selA, value)} disabled={!onSelect} />
    </Space>
    {error ? <Alert showIcon type="error" message={error} /> : !data ? <Spin /> : <>
      {quickA || quickB ? <Alert type="warning" showIcon style={{ marginBottom: 12 }}
        message={`${quickA && quickB ? 'A and B are quick checks' : quickA ? 'A is a quick check' : 'B is a quick check'}: ${QUICK_CHECK_HINT.replace(/^Quick check — /, '')}`} /> : null}
      <Space wrap style={{ marginBottom: 12 }}>
        {verdictTag(data.verdict)}
        <Tooltip title={adoptBlocked}>
          <span><Button type="primary" loading={adopting} disabled={!onAdoptB || Boolean(adoptBlocked)} onClick={() => onAdoptB(selB)}>Adopt B</Button></span>
        </Tooltip>
        {onViewB ? <Button onClick={() => onViewB(selB)}>View B</Button> : null}
        <Button onClick={() => view.reset()}>Reset zoom</Button>
        <Tooltip title={NAVIGATION_HELP}><InfoCircleOutlined aria-label="Plot navigation help" style={{ color: '#888' }} /></Tooltip>
      </Space>
      <Table size="small" pagination={false} rowKey="key" dataSource={rows} tableLayout="auto" columns={[
        { title: 'Metric', dataIndex: 'metric', render: (text, row) => (row.title ? <Tooltip title={row.title}>{text}</Tooltip> : text) },
        { title: `A — ${labelA}`, dataIndex: 'a' },
        { title: `B — ${labelB}`, dataIndex: 'b' },
      ]} />
      <Plot style={{ width: '100%', height: 'clamp(480px, calc(100vh - 420px), 1100px)', marginTop: 8 }} useResizeHandler
        data={addDSpacingHover([
          { x: data.x, y: data.observed, name: 'Observed', type: 'scatter', mode: 'markers', marker: { size: 3, color: '#333' } },
          { x: data.x, y: data.a_calculated, name: 'A calculated', type: 'scatter', mode: 'lines', line: { color: '#1677ff' } },
          { x: data.x, y: data.b_calculated, name: 'B calculated', type: 'scatter', mode: 'lines', line: { color: '#e67700', dash: 'dash' } },
          { x: data.x, y: data.a_residual, name: 'A obs − calc', type: 'scatter', mode: 'lines', xaxis: 'x2', yaxis: 'y2', line: { color: '#1677ff' } },
          { x: data.x, y: data.b_residual, name: 'B obs − calc', type: 'scatter', mode: 'lines', xaxis: 'x2', yaxis: 'y2', line: { color: '#e67700', dash: 'dash' } },
        ], data.wavelength_angstrom)}
        layout={{ autosize: true, margin: { l: 80, r: 30, t: 50, b: 65 }, legend: { orientation: 'h', y: 1.1 }, hovermode: 'x unified', dragmode: NAVIGATION_DRAGMODE,
          shapes,
          // One 2θ title, under the lower panel: the upper panel keeps its tick labels only.
          xaxis: { domain: [0, 1], anchor: 'y', showticklabels: true, ...view.xaxis },
          xaxis2: { domain: [0, 1], anchor: 'y2', matches: 'x', title: '2θ (°)', showticklabels: true, ...view.xaxis },
          yaxis: { domain: [0.40, 1], title: 'Intensity', ...view.yaxis },
          // The residual intensity stays fixed: box zoom there is a 2θ band.
          yaxis2: { domain: [0, 0.28], title: 'Obs − calc', zeroline: true, fixedrange: true },
          uirevision: `${selA}-${selB}:${view.revision}` }}
        xBounds={view.full} onRelayout={view.onRelayout} onDoubleClick={() => view.reset()}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: `task_${taskId}_fit_compare` } }} />
      {regions.length ? <Space wrap style={{ marginTop: 8 }}>
        <Tooltip title="Model-disagreement regions, not proof of unique diagnostic reflections. Click to zoom.">
          <span>Largest differences:</span>
        </Tooltip>
        {regions.map(region => <Button key={region.two_theta} size="small" onClick={() => {
          const [x0, x1] = regionBounds(region);
          view.setXRange([Math.max(data.x[0], x0 - 0.5), Math.min(data.x[data.x.length - 1], x1 + 0.5)]);
        }}>{`${Number(region.two_theta).toFixed(3)}°`}</Button>)}
      </Space> : null}
    </>}
  </Modal>;
}
