import React from 'react';
import { Alert, Button, InputNumber, Space, Spin, Switch, Table, Typography } from 'antd';
import Loadable from 'react-loadable';
import Layout from '../components/Layout';
import { comparisonTraces, measuredAngularBounds, parseComparisonTaskIds } from '../components/patternComparison.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import usePlotView, { NAVIGATION_CONFIG, NAVIGATION_HELP } from '../components/usePlotView';
import { fitLabel, fitsParam, overlayLayout, overlayTraces, parseFitsParam, readStoredFits, FITS_STORAGE_KEY } from '../components/fitOverlay.mjs';
import { addDSpacingHover } from '../components/plotNavigation.mjs';
import { isMissingEndpoint } from '../components/versions.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const Plotly = Loadable({
  loader: () => import('../components/RefinementPlotly'),
  loading: ({ timedOut }) => timedOut ? <Alert type="error" message="Could not load the plot. Refresh to retry." /> : <Spin />,
  timeout: 20000,
});

function MeasuredComparePage({ location, banner = null }) {
  const taskQuery = new URLSearchParams(location.search).get('tasks') || '';
  const [patterns, setPatterns] = React.useState([]);
  const [errors, setErrors] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [normalize, setNormalize] = React.useState(true);
  const [offset, setOffset] = React.useState(110);
  const [minimum, setMinimum] = React.useState(null);
  const [maximum, setMaximum] = React.useState(null);
  const [range, setRange] = React.useState(undefined);
  const [viewRevision, setViewRevision] = React.useState(0);

  React.useEffect(() => {
    const controller = new AbortController();
    const ids = parseComparisonTaskIds(taskQuery);
    setLoading(true);
    setErrors([]);
    setPatterns([]);
    setRange(undefined);
    Promise.all(ids.map(async taskId => {
      try {
        const response = await fetch(`${URL}/task/${taskId}/pattern`, { signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
        return { pattern: body };
      } catch (error) {
        return { error: `Task ${taskId}: ${error.message}` };
      }
    })).then(outcomes => {
      if (controller.signal.aborted) return;
      const loaded = outcomes.filter(item => item.pattern).map(item => item.pattern);
      setPatterns(loaded);
      setErrors(outcomes.filter(item => item.error).map(item => item.error));
      const bounds = measuredAngularBounds(loaded);
      setMinimum(bounds ? bounds[0] : null);
      setMaximum(bounds ? bounds[1] : null);
      setLoading(false);
    });
    return () => controller.abort();
  }, [taskQuery]);

  const traces = React.useMemo(() => comparisonTraces(patterns, { normalize, offset }), [patterns, normalize, offset]);
  const wavelengths = new Set(patterns.map(pattern => pattern.wavelength).filter(Boolean));
  const missingRadiation = patterns.some(pattern => !pattern.wavelength);
  const validRange = minimum !== null && maximum !== null && Number.isFinite(minimum) && Number.isFinite(maximum) && minimum < maximum;
  const changeNormalization = value => {
    setNormalize(value);
    const largest = patterns.reduce((size, pattern) => pattern.intensities.reduce((maximumValue, intensity) => Math.max(maximumValue, Math.abs(intensity)), size), 0);
    setOffset(value ? 110 : Math.ceil(largest * 1.1));
    setViewRevision(previous => previous + 1);
  };
  const resetView = () => {
    const bounds = measuredAngularBounds(patterns);
    setRange(undefined);
    setMinimum(bounds ? bounds[0] : null);
    setMaximum(bounds ? bounds[1] : null);
    setViewRevision(previous => previous + 1);
  };

  return <Layout hasSider={false} title="Pattern comparison">
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {banner}
      <Space wrap><Typography.Title level={3} style={{ margin: 0 }}>Compare measured patterns</Typography.Title><Button href="/results">Back to Results</Button></Space>
      <Typography.Text type="secondary">Original measured patterns, not calculated fits. {NAVIGATION_HELP} Click a legend item to hide or show a sample. Controls change the view only.</Typography.Text>
      {errors.map(error => <Alert key={error} type="error" message={error} showIcon />)}
      {!loading && patterns.length < 2 && <Alert type="info" showIcon message="Select at least two completed tasks in Results to compare samples." />}
      {(wavelengths.size > 1 || missingRadiation) && patterns.length > 1 && <Alert type="warning" showIcon message="Check the radiation before comparing peak positions." description="The saved analysis wavelengths differ or are not recorded for every sample. Equal 2θ does not imply equal d-spacing when wavelengths differ." />}
      {patterns.some(pattern => pattern.dropped_points > 0) && <Alert type="warning" showIcon message="Non-finite points were omitted from the comparison view." />}
      <Space wrap size="large">
        <Space><Switch checked={normalize} onChange={changeNormalization} /><span>Normalize max |intensity| to 100</span></Space>
        <Space><label htmlFor="pattern-offset">Vertical offset</label><InputNumber id="pattern-offset" min={0} value={offset} onChange={value => setOffset(value ?? 0)} style={{ width: 115 }} /><Button onClick={() => setOffset(0)}>Overlay</Button></Space>
        <Space><label htmlFor="comparison-theta-min">2θ range (°)</label><InputNumber id="comparison-theta-min" aria-label="Minimum two theta" value={minimum} onChange={setMinimum} /><span>–</span><InputNumber aria-label="Maximum two theta" value={maximum} onChange={setMaximum} /><Button disabled={!validRange} onClick={() => { setRange([minimum, maximum]); setViewRevision(previous => previous + 1); }}>Apply</Button><Button onClick={resetView}>Reset view</Button></Space>
      </Space>
      {loading ? <Spin /> : patterns.length > 0 && <Plotly
        data={traces}
        layout={{
          autosize: true, height: 530 + patterns.length * 23,
          margin: { t: 25, l: 85, r: 25, b: 80 + patterns.length * 23 },
          xaxis: { title: '2θ (°)', range, autorange: !range, showline: true, mirror: true, ticks: 'outside', zeroline: false },
          yaxis: { title: normalize ? 'Normalized intensity + offset' : 'Measured intensity + offset', showline: true, ticks: 'outside', zeroline: false },
          legend: { orientation: 'h', x: 0, y: -0.18, xanchor: 'left', yanchor: 'top' },
          hovermode: 'closest', dragmode: 'pan', uirevision: `${taskQuery}-${viewRevision}`,
        }}
        onRelayout={event => {
          const change = viewChangeFromRelayout(event);
          if (change.xRange) { setRange(change.xRange); setMinimum(Number(Math.min(...change.xRange).toFixed(3))); setMaximum(Number(Math.max(...change.xRange).toFixed(3))); }
        }}
        onDoubleClick={resetView}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: 'dara-pattern-comparison' } }}
        style={{ width: '100%' }} useResizeHandler
      />}
      {patterns.length > 0 && <Table size="small" rowKey="task_id" dataSource={patterns} pagination={false} scroll={{ x: 640 }} columns={[
        { title: 'Task / sample', render: (_, row) => <a href={`/task?id=${row.task_id}`}>#{row.task_id} — {row.name}</a> },
        { title: '2θ range (°)', render: (_, row) => `${row.angles[0].toFixed(3)}–${row.angles[row.angles.length - 1].toFixed(3)}` },
        { title: 'Points', render: (_, row) => row.angles.length },
        { title: 'Analysis wavelength', dataIndex: 'wavelength', render: value => value || 'Not recorded' },
        { title: 'Pattern source', dataIndex: 'source' },
      ]} />}
    </Space>
  </Layout>;
}

function storedFits() {
  try {
    return readStoredFits(window.localStorage);
  } catch (_) {
    return [];
  }
}

function clearStoredFits() {
  try {
    window.localStorage.removeItem(FITS_STORAGE_KEY);
  } catch (_) {
    // Storage may be blocked; nothing to clear then.
  }
}

/** Calculated fits of several tasks/versions on one axis (`?fits=1:best,2:version:2`). */
function FitOverlayPage({ fits, measuredHref }) {
  const [curves, setCurves] = React.useState([]);
  const [errors, setErrors] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [normalize, setNormalize] = React.useState(true);
  const [offset, setOffset] = React.useState(0);
  const [showCalculated, setShowCalculated] = React.useState(true);
  const [showObserved, setShowObserved] = React.useState(false);
  const [showMarkers, setShowMarkers] = React.useState(false);
  const [showDifference, setShowDifference] = React.useState(fits.length >= 2);
  const view = usePlotView();
  const key = fitsParam(fits);

  React.useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setErrors([]);
    setCurves([]);
    Promise.all(fits.map(async ({ taskId, sel }) => {
      try {
        const response = await fetch(`${URL}/task/${taskId}/fit-curves?sel=${encodeURIComponent(sel)}`, { signal: controller.signal });
        let body = null;
        try { body = await response.json(); } catch (_) { /* non-JSON error page */ }
        const detail = typeof body?.detail === 'string' ? body.detail : '';
        if (!response.ok && isMissingEndpoint(response.status, detail)) throw new Error('Not available on this server version.');
        if (!response.ok) throw new Error(detail || `HTTP ${response.status}`);
        return { curve: { ...body, task_id: taskId, sel, label: fitLabel(taskId, sel) } };
      } catch (error) {
        return { error: `${fitLabel(taskId, sel)}: ${error.message}` };
      }
    })).then(outcomes => {
      if (controller.signal.aborted) return;
      setCurves(outcomes.filter(item => item.curve).map(item => item.curve));
      setErrors(outcomes.filter(item => item.error).map(item => item.error));
      setLoading(false);
      view.reset();
    });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const difference = showDifference && curves.length >= 2;
  const traces = React.useMemo(() => {
    const built = overlayTraces(curves, { normalize, offset, showCalculated, showObserved, showMarkers, showDifference: difference });
    const wavelength = Number(curves[0]?.wavelength_angstrom);
    return wavelength > 0 ? addDSpacingHover(built, wavelength) : built;
  }, [curves, normalize, offset, showCalculated, showObserved, showMarkers, difference]);
  const wavelengths = new Set(curves.map(curve => curve.wavelength_angstrom).filter(Boolean));
  const axes = overlayLayout({ showDifference: difference, normalize });

  return <Layout hasSider={false} title="Fit comparison">
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Space wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>Compare fits</Typography.Title>
        <Button href="/results">Back to Results</Button>
        <Button href={measuredHref}>Measured patterns only</Button>
        <Button onClick={() => { clearStoredFits(); window.location.href = measuredHref; }}>Clear stored comparison list</Button>
      </Space>
      <Typography.Text type="secondary">Calculated patterns from the stored refinements. {NAVIGATION_HELP} Controls change the view only. Rwp values are comparable only for fits of the same data with the same fitted range.</Typography.Text>
      {errors.map(error => <Alert key={error} type="error" message={error} showIcon />)}
      {wavelengths.size > 1 && <Alert type="warning" showIcon message="Check the radiation before comparing peak positions." description="These fits use different wavelengths. Equal 2θ does not imply equal d-spacing." />}
      {!loading && curves.length < 2 && !errors.length && <Alert type="info" showIcon message="Add at least two fits to compare." description="Use “Add to comparison” in the fit view of a task, then open the comparison." />}
      <Space wrap size="large">
        <Space><Switch checked={showCalculated} onChange={setShowCalculated} /><span>Show calculated</span></Space>
        <Space><Switch checked={showObserved} onChange={setShowObserved} /><span>Show observed</span></Space>
        <Space><Switch checked={showMarkers} onChange={setShowMarkers} /><span>Show phase markers</span></Space>
        <Space><Switch checked={showDifference} onChange={setShowDifference} disabled={curves.length < 2} /><span>Show difference A − B</span></Space>
        <Space><Switch checked={normalize} onChange={setNormalize} /><span>Normalize max intensity to 100</span></Space>
        <Space><label htmlFor="fit-offset">Vertical offset</label><InputNumber id="fit-offset" min={0} value={offset} onChange={value => setOffset(value ?? 0)} style={{ width: 115 }} /></Space>
        <Button onClick={() => view.reset()}>Reset view</Button>
      </Space>
      {loading ? <Spin /> : curves.length > 0 && <Plotly
        data={traces}
        layout={{
          autosize: true, height: 620 + (difference ? 140 : 0),
          margin: { t: 25, l: 85, r: 25, b: 90 },
          xaxis: { ...axes.xaxis, ...view.xaxis },
          yaxis: { ...axes.yaxis, ...(view.yaxis.range ? view.yaxis : {}) },
          ...(difference ? { xaxis2: { ...axes.xaxis2, ...view.xaxis }, yaxis2: axes.yaxis2 } : {}),
          legend: { orientation: 'h', x: 0, y: -0.2, xanchor: 'left', yanchor: 'top' },
          hovermode: 'closest', dragmode: 'pan', uirevision: `${key}-${view.revision}`,
        }}
        onRelayout={view.onRelayout}
        onDoubleClick={() => view.reset()}
        config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG, toImageButtonOptions: { format: 'svg', filename: 'dara-fit-comparison' } }}
        style={{ width: '100%' }} useResizeHandler
      />}
      {curves.length > 0 && <Table size="small" rowKey="label" dataSource={curves} pagination={false} scroll={{ x: 640 }} columns={[
        { title: 'Fit', dataIndex: 'label', render: (label, row) => <a href={`/task?id=${row.task_id}`}>{label}</a> },
        { title: 'Pattern', dataIndex: 'name' },
        { title: 'Rwp (%)', dataIndex: 'rwp', render: value => (value == null ? '—' : Number(value).toFixed(2)) },
        { title: 'Points', render: (_, row) => row.x?.length ?? '—' },
        { title: 'Wavelength (Å)', dataIndex: 'wavelength_angstrom', render: value => value || 'Not recorded' },
      ]} />}
    </Space>
  </Layout>;
}

export default function ComparePage({ location }) {
  const params = new URLSearchParams(location.search);
  const fits = parseFitsParam(params.get('fits'));
  const [stored, setStored] = React.useState([]);
  React.useEffect(() => { setStored(storedFits()); }, []);
  if (fits.length) {
    return <FitOverlayPage fits={fits} measuredHref={params.get('tasks') ? `/compare?tasks=${encodeURIComponent(params.get('tasks'))}` : '/compare'} />;
  }
  const banner = stored.length > 0 ? <Alert type="info" showIcon
    message={`${stored.length} fit${stored.length === 1 ? '' : 's'} in your comparison list`}
    action={<Button size="small" href={`/compare?tasks=${[...new Set(stored.map(item => item.taskId))].join(',')}&fits=${fitsParam(stored)}`}>Compare fits</Button>} /> : null;
  return <MeasuredComparePage location={location} banner={banner} />;
}
