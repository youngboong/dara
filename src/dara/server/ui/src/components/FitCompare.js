import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Modal, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import usePlotView, { NAVIGATION_CONFIG, NAVIGATION_HELP } from './usePlotView';
import { addDSpacingHover } from './plotNavigation.mjs';
import { NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import { formatSelection, formulaOfPhase } from './versions.mjs';

const Plot = Loadable({ loader: () => import('./RefinementPlotly'), loading: () => <Spin /> });

const fixed = (value, digits = 2) => (value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits));
const count = value => (Array.isArray(value) ? value.length : value == null ? '—' : Number(value));
const massBalanceText = block => (!block ? '—' : block.level ? String(block.level) : block.verdict || block.status || '—');

function verdictTag(verdict) {
  if (!verdict) return null;
  const delta = verdict.rwp_delta == null ? '' : ` (ΔRwp ${verdict.rwp_delta > 0 ? '+' : verdict.rwp_delta < 0 ? '−' : ''}${Math.abs(verdict.rwp_delta).toFixed(2)})`;
  if (verdict.better === 'b') return <Tag color="green">B fits better{delta}</Tag>;
  if (verdict.better === 'a') return <Tag color="orange">A fits better{delta}</Tag>;
  return <Tag>Similar fit{delta}</Tag>;
}

function regionBounds(region) {
  if (Array.isArray(region.range) && region.range.length === 2) return region.range;
  if (Number.isFinite(region.start) && Number.isFinite(region.end)) return [region.start, region.end];
  return [region.two_theta - 0.5, region.two_theta + 0.5];
}

/** Side-by-side comparison of two fits (any selections) of one task. */
export default function FitCompare({ taskId, a, b, open, onClose, onAdoptB, adopting, onViewB }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const view = usePlotView();
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

  const labelA = data?.a?.label || String(selA);
  const labelB = data?.b?.label || String(selB);
  const weightPhases = data ? [...new Set([...Object.keys(data.a?.weight_percent || {}), ...Object.keys(data.b?.weight_percent || {})])] : [];
  const rows = data ? [
    { key: 'rwp', metric: 'Rwp (%)', a: fixed(data.a?.rwp), b: fixed(data.b?.rwp) },
    { key: 'gof', metric: 'GOF', a: fixed(data.a?.gof), b: fixed(data.b?.gof) },
    { key: 'chi', metric: 'χ²', a: fixed(data.a?.chi_squared), b: fixed(data.b?.chi_squared) },
    ...weightPhases.map(phase => ({ key: `wt:${phase}`, metric: `${formulaOfPhase(phase)} (wt%)`, title: phase,
      a: data.a?.weight_percent?.[phase] == null ? '—' : fixed(data.a.weight_percent[phase]),
      b: data.b?.weight_percent?.[phase] == null ? '—' : fixed(data.b.weight_percent[phase]) })),
    { key: 'mb', metric: 'Mass balance', a: massBalanceText(data.a?.mass_balance), b: massBalanceText(data.b?.mass_balance) },
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
  return <Modal title="Compare fits — same measured pattern" open={open} width="95vw" footer={null} onCancel={onClose}
    wrapClassName="dara-compare-modal" destroyOnClose>
    {error ? <Alert showIcon type="error" message={error} /> : !data ? <Spin /> : <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Tag color="blue">{`A: ${labelA}`}</Tag>
        <Tag color="orange">{`B: ${labelB}`}</Tag>
        {verdictTag(data.verdict)}
        <Tooltip title="Adopt fit B as the scientific choice and save the current lab notes">
          <Button type="primary" loading={adopting} disabled={!onAdoptB} onClick={() => onAdoptB(selB)}>Adopt B</Button>
        </Tooltip>
        <Tooltip title="Close without changing the adopted result. Fit B stays in the versions list.">
          <Button onClick={onClose}>Keep A</Button>
        </Tooltip>
        {onViewB ? <Button onClick={() => onViewB(selB)}>View B</Button> : null}
        <Button onClick={() => view.reset()}>Reset zoom</Button>
      </Space>
      <Table size="small" pagination={false} rowKey="key" dataSource={rows} columns={[
        { title: 'Metric', dataIndex: 'metric', render: (text, row) => (row.title ? <Tooltip title={row.title}>{text}</Tooltip> : text) },
        { title: `A — ${labelA}`, dataIndex: 'a' },
        { title: `B — ${labelB}`, dataIndex: 'b' },
      ]} />
      <p style={{ color: '#666', margin: '8px 0' }}>{NAVIGATION_HELP}</p>
      <Plot style={{ width: '100%', height: 620 }} useResizeHandler
        data={addDSpacingHover([
          { x: data.x, y: data.observed, name: 'Observed', type: 'scatter', mode: 'markers', marker: { size: 3, color: '#333' } },
          { x: data.x, y: data.a_calculated, name: 'A calculated', type: 'scatter', mode: 'lines', line: { color: '#1677ff' } },
          { x: data.x, y: data.b_calculated, name: 'B calculated', type: 'scatter', mode: 'lines', line: { color: '#e67700', dash: 'dash' } },
          { x: data.x, y: data.a_residual, name: 'A obs − calc', type: 'scatter', mode: 'lines', xaxis: 'x2', yaxis: 'y2', line: { color: '#1677ff' } },
          { x: data.x, y: data.b_residual, name: 'B obs − calc', type: 'scatter', mode: 'lines', xaxis: 'x2', yaxis: 'y2', line: { color: '#e67700', dash: 'dash' } },
        ], data.wavelength_angstrom)}
        layout={{ autosize: true, margin: { l: 80, r: 30, t: 50, b: 65 }, legend: { orientation: 'h', y: 1.1 }, hovermode: 'x unified', dragmode: 'pan',
          shapes,
          xaxis: { domain: [0, 1], anchor: 'y', title: '2θ (°)', showticklabels: true, ...view.xaxis },
          xaxis2: { domain: [0, 1], anchor: 'y2', matches: 'x', title: '2θ (°)', showticklabels: true, ...view.xaxis },
          yaxis: { domain: [0.43, 1], title: 'Intensity', ...view.yaxis }, yaxis2: { domain: [0, 0.26], title: 'Obs − calc', zeroline: true },
          uirevision: `${selA}-${selB}:${view.revision}` }}
        onRelayout={view.onRelayout} onDoubleClick={() => view.reset()}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: `task_${taskId}_fit_compare` } }} />
      {regions.length ? <>
        <p>Regions where the two calculated patterns differ most (click to zoom):</p>
        <Space wrap>{regions.map(region => <Button key={region.two_theta} onClick={() => {
          const [x0, x1] = regionBounds(region);
          view.setXRange([Math.max(data.x[0], x0 - 0.5), Math.min(data.x[data.x.length - 1], x1 + 0.5)]);
        }}>{Number(region.two_theta).toFixed(3)}°</Button>)}</Space>
      </> : null}
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
        These are model-disagreement regions, not proof of unique diagnostic reflections. Compare residuals, phase chemistry and physical plausibility together; lower Rwp alone does not prove correctness.
      </Typography.Paragraph>
    </>}
  </Modal>;
}
