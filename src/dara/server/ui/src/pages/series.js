import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Progress, Select, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { LineChartOutlined, TableOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import { STATE_COLOR } from './results';
import { overallColor, overallLabel } from '../components/confidence.mjs';
import { formatFixed, phasesLabel, phasesTooltip } from '../components/resultsList.mjs';
import {
  LATTICE_PARAMETERS, fitTraces, hasActiveItems, latticeTraces, phaseLabels, phaseTraces, progress,
  taskIds, xAxis,
} from '../components/seriesTrends.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const POLL_MS = 10000;
const MAX_COMPARE = 20;

const Plotly = Loadable({
  loader: () => import('react-plotly.js'),
  loading: ({ timedOut }) => timedOut ? <Alert type="error" message="Could not load the plot. Refresh to retry." /> : <Spin />,
  timeout: 20000,
});

const PARAMETER_LABEL = { a: 'a (Å)', b: 'b (Å)', c: 'c (Å)', alpha: 'α (°)', beta: 'β (°)', gamma: 'γ (°)' };

const baseLayout = (axis, yTitle, extra = {}) => ({
  autosize: true, height: 420, margin: { t: 20, l: 70, r: 70, b: 60 },
  xaxis: { title: axis.title, showline: true, mirror: true, ticks: 'outside', zeroline: false },
  yaxis: { title: yTitle, showline: true, mirror: true, ticks: 'outside', zeroline: false },
  legend: { orientation: 'h', x: 0, y: -0.25 },
  hovermode: 'closest',
  ...extra,
});

const config = name => ({ responsive: true, displaylogo: false, toImageButtonOptions: { format: 'svg', filename: name } });

const errorBars = values => ({ type: 'data', array: values, visible: values.some(value => value !== null) });

function ChartBlock({ title, note, children }) {
  return <section style={{ marginBottom: 28 }}>
    <h3 style={{ marginBottom: 4 }}>{title}</h3>
    {note ? <Typography.Text type="secondary">{note}</Typography.Text> : null}
    <div style={{ marginTop: 8 }}>{children}</div>
  </section>;
}

function PhaseChart({ items, axis }) {
  const traces = phaseTraces(items);
  if (!traces.length) {
    return <Alert type="info" showIcon message="Phase fractions are not available yet" description="They appear once tasks are completed. Results stored by an older version are summarised when this page is opened." />;
  }
  return <Plotly
    data={traces.map(trace => ({
      type: 'scatter', mode: 'lines+markers', name: trace.label, x: trace.x, y: trace.y,
      error_y: errorBars(trace.err), connectgaps: false,
    }))}
    layout={baseLayout(axis, 'Weight fraction (wt%)')}
    config={config('dara-series-phase-fractions')} style={{ width: '100%' }} useResizeHandler />;
}

function LatticeChart({ items, axis }) {
  const labels = phaseLabels(items);
  const [phase, setPhase] = React.useState(undefined);
  const [parameter, setParameter] = React.useState('a');
  const selected = labels.includes(phase) ? phase : labels[0];
  if (!labels.length) return <Alert type="info" showIcon message="Lattice parameters are not available yet" />;
  const trace = latticeTraces(items, selected, parameter);
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <label>Phase <Select aria-label="Phase" style={{ width: 260 }} value={selected} onChange={setPhase}
        options={labels.map(label => ({ value: label, label }))} /></label>
      <label>Parameter <Select aria-label="Lattice parameter" style={{ width: 120 }} value={parameter} onChange={setParameter}
        options={LATTICE_PARAMETERS.map(key => ({ value: key, label: PARAMETER_LABEL[key] }))} /></label>
    </Space>
    {trace.y.every(value => value === null)
      ? <Alert type="info" showIcon message={`${PARAMETER_LABEL[parameter]} is not reported for this phase.`} description="Parameters fixed by the crystal system are not listed." />
      : <Plotly
        data={[{ type: 'scatter', mode: 'lines+markers', name: `${selected} — ${parameter}`, x: trace.x, y: trace.y, error_y: errorBars(trace.err), connectgaps: false }]}
        layout={baseLayout(axis, PARAMETER_LABEL[parameter], { showlegend: false })}
        config={config(`dara-series-lattice-${parameter}`)} style={{ width: '100%' }} useResizeHandler />}
  </>;
}

function FitChart({ items, axis }) {
  const fit = fitTraces(items);
  if (!fit.x.length) return <Alert type="info" showIcon message="Fit statistics are not available yet" />;
  return <Plotly
    data={[
      { type: 'scatter', mode: 'lines+markers', name: 'Rwp (%)', x: fit.x, y: fit.rwp, connectgaps: false },
      { type: 'scatter', mode: 'lines+markers', name: 'GOF', x: fit.x, y: fit.gof, yaxis: 'y2', connectgaps: false },
    ]}
    layout={baseLayout(axis, 'Rwp (%)', { yaxis2: { title: 'GOF', overlaying: 'y', side: 'right', showline: true, ticks: 'outside', zeroline: false } })}
    config={config('dara-series-fit')} style={{ width: '100%' }} useResizeHandler />;
}

function ConfidenceCell({ summary }) {
  const label = overallLabel(summary?.confidence_overall);
  return label ? <Tag color={overallColor(summary.confidence_overall)}>{label}</Tag> : '—';
}

function SeriesDetail({ id }) {
  const [data, setData] = React.useState(undefined);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    let active = true;
    let timer = null;
    const load = () => fetch(`${URL}/series/${encodeURIComponent(id)}`)
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
        return body;
      })
      .then(body => {
        if (!active) return;
        setData(body);
        setError('');
        if (hasActiveItems(body.items)) timer = window.setTimeout(load, POLL_MS);
      })
      .catch(e => {
        if (!active) return;
        setError(String(e.message || e));
        setData(previous => previous ?? null);
      });
    setData(undefined);
    load();
    return () => { active = false; if (timer) window.clearTimeout(timer); };
  }, [id]);

  if (data === undefined) return <Spin size="large" />;
  if (data === null) return <Alert type="error" showIcon message="Series could not be loaded" description={error} />;

  const items = data.items || [];
  const axis = xAxis(items);
  const { done, total } = progress(items);
  const completedIds = taskIds(items, { completedOnly: true });
  const compareIds = completedIds.slice(0, MAX_COMPARE);
  const allIds = taskIds(items).slice(0, 50);

  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    {error ? <Alert type="warning" showIcon message={`Refresh failed: ${error}`} /> : null}
    <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
      <div>
        <h2 style={{ marginBottom: 4 }}>{data.name}</h2>
        <Space wrap>
          <Tag>{`${total} tasks`}</Tag>
          {data.unit ? <Tag color="geekblue">{`Variable: ${data.unit}`}</Tag> : <Tag>No variable parsed — ordered by file name</Tag>}
          {data.created_on ? <Tag>{`Submitted ${data.created_on}`}</Tag> : null}
        </Space>
      </div>
      <Space wrap>
        <Button icon={<TableOutlined />} href={`${URL}/tasks/table.csv?ids=${allIds.join(',')}`}>Export CSV</Button>
        <Button icon={<LineChartOutlined />} disabled={completedIds.length < 2}
          href={completedIds.length >= 2 ? `/compare?tasks=${compareIds.join(',')}` : undefined}>Compare patterns</Button>
      </Space>
    </div>
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Progress percent={total ? Math.round((done / total) * 100) : 0} showInfo={false} style={{ flex: '1 1 160px', minWidth: 0, margin: 0 }} />
        <Typography.Text style={{ whiteSpace: 'nowrap' }}>{`${done} / ${total} completed`}</Typography.Text>
      </div>
      {hasActiveItems(items) ? <Typography.Text type="secondary">Updating every {POLL_MS / 1000} s while tasks are queued or running.</Typography.Text> : null}
      {completedIds.length > MAX_COMPARE ? <div><Typography.Text type="secondary">{`Pattern comparison shows the first ${MAX_COMPARE} completed tasks.`}</Typography.Text></div> : null}
    </div>
    <Table size="small" rowKey="task_id" pagination={false} dataSource={items} scroll={{ x: 900 }}
      columns={[
        { title: 'Order', dataIndex: 'order', width: 70, render: value => value + 1 },
        { title: 'File', dataIndex: 'name', render: (text, row) => <a href={`/task/?id=${row.task_id}`}>{text}</a> },
        { title: data.unit ? `Variable (${data.unit})` : 'Variable', key: 'variable', render: (_, row) => row.variable?.raw ?? '—' },
        { title: 'Status', dataIndex: 'state', render: state => <Tag color={STATE_COLOR[state] || 'default'}>{state}</Tag> },
        { title: 'Rwp (%)', key: 'rwp', render: (_, row) => formatFixed(row.summary?.final_rwp ?? row.summary?.best_rwp) },
        { title: <Tooltip title="Goodness of fit = Rwp / Rexp of the final refinement">GOF</Tooltip>, key: 'gof', render: (_, row) => formatFixed(row.summary?.gof) },
        { title: 'Confidence', key: 'confidence', render: (_, row) => <ConfidenceCell summary={row.summary} /> },
        { title: 'Phases', key: 'phases', width: 280, render: (_, row) => {
          const label = phasesLabel(row.summary, 4);
          return label ? <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phasesTooltip(row.summary)}</div>}><span>{label}</span></Tooltip> : '—';
        } },
      ]} />
    {done < 2 ? <Alert type="info" showIcon message="Trend charts need at least two completed tasks." /> : null}
    <ChartBlock title="Phase fractions" note="Weight fractions are relative to the modelled crystalline phases; error bars are BGMN estimated standard deviations. Gaps mean the phase was not in the final result of that sample.">
      <PhaseChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Lattice parameters" note="Per phase identity; error bars are BGMN estimated standard deviations.">
      <LatticeChart items={items} axis={axis} />
    </ChartBlock>
    <ChartBlock title="Fit quality" note="Rwp and goodness of fit of the final refinement of each sample.">
      <FitChart items={items} axis={axis} />
    </ChartBlock>
  </Space>;
}

function SeriesList() {
  const [data, setData] = React.useState(undefined);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    fetch(`${URL}/series`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error(`HTTP ${response.status}`)))
      .then(body => setData(body.series || []))
      .catch(e => { setError(String(e.message || e)); setData([]); });
  }, []);
  return <>
    <h2>Series</h2>
    <Typography.Paragraph type="secondary">
      A series is a set of patterns analysed independently with identical settings. Submit one from the Submit page with “Submit as a series”.
    </Typography.Paragraph>
    {error ? <Alert type="error" showIcon message="Series list unavailable" description={error} style={{ marginBottom: 12 }} /> : null}
    {data === undefined ? <Spin /> : <Table size="small" rowKey="series_id" dataSource={data} pagination={{ pageSize: 20, hideOnSinglePage: true }}
      locale={{ emptyText: 'No series have been submitted yet.' }}
      columns={[
        { title: 'Name', dataIndex: 'name', render: (text, row) => <a href={`/series/?id=${encodeURIComponent(row.series_id)}`}>{text || row.series_id}</a> },
        { title: 'Created', dataIndex: 'created_on' },
        { title: 'Tasks', dataIndex: 'n_tasks' },
        { title: 'Completed', dataIndex: 'n_completed' },
        { title: 'Variable', dataIndex: 'unit', render: value => value || '—' },
      ]} />}
  </>;
}

export default function SeriesPage({ location }) {
  const id = new URLSearchParams(location.search).get('id');
  return <Layout hasSider={false} title="Series">
    {id ? <SeriesDetail id={id} /> : <SeriesList />}
  </Layout>;
}
