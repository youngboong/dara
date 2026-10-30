import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Button, Descriptions, Divider, Dropdown, message, Modal, Spin, Tag, List, Space, Switch, Tooltip, Typography, Table, Select } from 'antd';
import { DeleteOutlined, DownOutlined, DownloadOutlined, InfoCircleOutlined, TableOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable'
import parse from 'html-react-parser';
import {STATE_COLOR} from './results';
import { buildRefinementView, isTraceSelected, legendKey } from '../components/refinementPlotState.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import { formatParameterWithEsd, latticeParameterRows } from '../components/latticeParameters.mjs';
import { PhaseSelectionButton, ReportDownloadButton, RetryActions, ReviewPanel, useTaskReview } from '../components/ResultWorkflows';
import { TaskProgress } from '../components/PracticalControls';
import FitCautions from '../components/FitCautionsPanel';
import { cautionSortValue, cautionTagColor, cautionTooltip, countCautions } from '../components/fitCautions.mjs';
import { levelRank, missingCell, unexplainedSummary, unexplainedTooltipLines } from '../components/confidence.mjs';
import { ConfidenceBanner, DisplacementTag, FigureExportActions, OverallTag, ScreeningTags, VerdictTag } from '../components/ConfidenceViews';
import VersionsPanel, { useVersions } from '../components/VersionsPanel';
import QuickRefineProgress, { NOT_AVAILABLE, postJson, requestJson, useQuickRefine } from '../components/QuickRefineProgress';
import PeakCandidatesDrawer from '../components/PeakCandidatesDrawer';
import FitCompare from '../components/FitCompare';
import BackgroundEditor from '../components/BackgroundEditor';
import useKeyboardShortcuts, { FIT_MODAL_CLASS, HELP_MODAL_CLASS } from '../components/useKeyboardShortcuts';
import { SHORTCUT_HELP } from '../components/keyboardShortcuts.mjs';
import {
  collectHints, hintText, hintsPending, observedPeak, peakFromClick, peakShapes, phaseGroupNames, previewTrace, updatePeakSelection,
} from '../components/peakSelection.mjs';
import {
  alternativeOfVerdict, baselineRequest, comparisonBase, deltaStrip, deltaText, formatSelection, needsBaseline, parseSelection,
  plotQuery, removeRequest, selectOptionGroups, selectionLabel, swapKind, swapRequest,
} from '../components/versions.mjs';

const { Text } = Typography;
const HINT_POLL_MS = 3000;
const HINT_POLL_MAX = 20; // 60 s

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

async function downloadResultArchive(taskId, resultIndex) {
  const query = resultIndex === undefined ? '' : `?idx=${resultIndex}`;
  const response = await fetch(`${URL}/task/${taskId}/download${query}`);
  if (!response.ok) {
    let detail = `Download failed (HTTP ${response.status})`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail);
  }

  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = filenameMatch?.[1] || `dara_task_${taskId}.zip`;
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

function DownloadButton({ taskId, resultIndex, children, ...buttonProps }) {
  const [loading, setLoading] = React.useState(false);
  const download = async () => {
    setLoading(true);
    try {
      await downloadResultArchive(taskId, resultIndex);
      message.success('Result ZIP downloaded.');
    } catch (error) {
      message.error(error.message || 'Could not download the result.');
    } finally {
      setLoading(false);
    }
  };
  return <Button {...buttonProps} loading={loading} onClick={download}>{children}</Button>;
}

const Container = styled.div`
& code {
  background-color: rgba(0, 0, 0, 0.1);
  border-radius: 6px;
  padding: .2em .4em;
  font-size: 85%;
  text-align: center;
}

.no-highlight {
  color: #555;
  background-color: rgba(0, 0, 0, 0.03);
}

.highlight {
  color: black;
}

.representative-phase {
  font-weight: bold;
  color: black;
  font-size: 1em;
  border-radius: 6px;
  border: 1px solid #555;
  padding: .2em .4em;
}

.phase-identity {
  display: inline-flex;
  flex-direction: column;
  line-height: 1.35;
}

.phase-identity-primary {
  color: #111;
  font-weight: 600;
}

.phase-identity-secondary {
  color: #666;
  font-size: 0.86em;
  font-weight: normal;
}

.phases-container {
  line-height: 2;
}

.error-code {
  background-color: #f9f9f9;
  padding: 10px;
  border-radius: 6px;
  border: 1px solid #ddd;
  font-color: #333;
  overflow-x: scroll;
}
`

const SpinContainer = styled.div`
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  width: 100%;
`

const TitleContriner = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`

const ResultTitleContainer = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`

const Title = styled.div`
  display: flex;
  justify-content: space-between;
  align-items: center;
`


const Plotly = Loadable({
  loader: () => import('../components/RefinementPlotly'),
  loading: ({ timedOut }) =>
    timedOut ? (
      <blockquote>Error: Loading Plotly timed out.</blockquote>
    ) : (
      <Spin />
    ),
  timeout: 10000,
})

const plotRequests = new Map();

function plotUrl(taskId, plotId) {
  return `${URL}/task/${taskId}/plot${plotQuery(plotId === null ? 'best' : plotId)}`;
}

function loadPlot(taskId, plotId) {
  const requestUrl = plotUrl(taskId, plotId);
  if (!plotRequests.has(requestUrl)) {
    const request = fetch(requestUrl)
      .then(response => {
        if (!response.ok) throw new Error(`Could not load plot (HTTP ${response.status}).`);
        return response.json();
      })
      // Older servers returned a JSON string. Keep compatibility while the
      // direct JSON response rolls out.
      .then(response => typeof response === 'string' ? JSON.parse(response) : response)
      .catch(error => {
        plotRequests.delete(requestUrl);
        throw error;
      });
    // Bound retained plot data when an operator reviews many tasks in one tab.
    if (plotRequests.size >= 24) plotRequests.delete(plotRequests.keys().next().value);
    plotRequests.set(requestUrl, request);
  }
  return plotRequests.get(requestUrl);
}

function preloadPlot(taskId, plotId) {
  Plotly.preload();
  if (taskId !== undefined && plotId !== undefined) loadPlot(taskId, plotId).catch(() => {});
}


const RefinementPlot = React.forwardRef(function RefinementPlot({
  task_id, plot_id, excludedRanges = [], peaks = [], onPeakClick, previewSticks = null, onGroups,
}, ref) {
  const [plot, setPlot] = React.useState(undefined);
  const [error, setError] = React.useState('');
  const [scale, setScale] = React.useState('linear');
  const [showDiagnostics, setShowDiagnostics] = React.useState(true);
  const [showPhaseProfiles, setShowPhaseProfiles] = React.useState(true);
  const [showReflections, setShowReflections] = React.useState(true);
  const [resetKey, setResetKey] = React.useState(0);
  const [selection, setSelection] = React.useState({});
  const [yRange, setYRange] = React.useState(null);
  const [xRange, setXRange] = React.useState(null);
  const [fitY, setFitY] = React.useState(true);
  const lastLegendKey = React.useRef(null);
  const sameLegendClicked = React.useRef(false);
  const resetView = () => { setXRange(null); setYRange(null); setResetKey(value => value + 1); };
  // Keyboard shortcuts act through this handle; it always reads the latest state.
  const latest = React.useRef({});
  latest.current = { plot, xRange };
  React.useImperativeHandle(ref, () => ({
    reset: () => { setXRange(null); setYRange(null); setResetKey(value => value + 1); },
    toggleFitY: () => { setFitY(value => !value); setYRange(null); },
    togglePhase: name => {
      const current = latest.current.plot;
      const index = (current?.data || []).findIndex(trace => trace.legendgroup === name && trace.meta === 'phase-profile');
      if (index < 0) return;
      const trace = current.data[index];
      const key = legendKey(trace, index);
      setSelection(previous => ({ ...previous, [key]: !isTraceSelected(trace, index, previous) }));
      setYRange(null);
    },
    getViewRange: () => latest.current.xRange || latest.current.plot?.layout?.xaxis?.range || null,
  }), []);
  React.useEffect(() => {
    setPlot(undefined);
    setError('');
    setSelection({});
    setYRange(null);
    setXRange(null);
    if (onGroups) onGroups([]);
    if (task_id === undefined || plot_id === undefined) return;
    let active = true;
    loadPlot(task_id, plot_id)
      .then(response => { if (active) { setPlot(response); if (onGroups) onGroups(phaseGroupNames(response)); } })
      .catch(error => { if (active) setError(error.message); });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task_id, plot_id]);
  if (error) return <Alert type="error" showIcon message={error} />;
  if (!plot) return <Spin />;
  const view = buildRefinementView(plot, {
    selection, showDiagnostics, showPhaseProfiles, showReflections, scale, yRange, xRange, fitY,
  });
  const toggleLegend = event => {
    const index = event.curveNumber;
    const trace = plot.data[index];
    const key = legendKey(trace, index);
    sameLegendClicked.current = lastLegendKey.current === key;
    lastLegendKey.current = key;
    setSelection(previous => ({ ...previous,
      [legendKey(trace, index)]: !isTraceSelected(trace, index, previous) }));
    setYRange(null);
    return false;
  };
  const isolateLegend = event => {
    // Plotly can count quick clicks on different legend items as a double
    // click. Only isolate when both clicks really targeted the same item.
    if (!sameLegendClicked.current) return false;
    const selectedKey = legendKey(plot.data[event.curveNumber], event.curveNumber);
    setSelection(Object.fromEntries(plot.data.map((trace, index) => [legendKey(trace, index),
      trace.legendgroup ? legendKey(trace, index) === selectedKey : isTraceSelected(trace, index, selection)])));
    setYRange(null);
    return false;
  };
  const handleClick = event => {
    if (!onPeakClick) return;
    const point = event?.points?.[0];
    const trace = point ? plot.data[point.curveNumber] : null;
    if (!trace) return;
    const peak = peakFromClick(plot, trace.name, point.x);
    if (peak !== null) onPeakClick(peak, Boolean(event.event?.shiftKey));
  };
  const data = previewSticks?.length
    ? [...view.data, previewTrace(previewSticks, observedPeak(plot) * 0.8, { floor: scale === 'log' ? 1 : 0 })]
    : view.data;
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <Text>Intensity scale</Text>
      <Select value={scale} onChange={value => { setScale(value); setYRange(null); }} style={{ width: 100 }}
        options={[{ value: 'linear', label: 'Linear' }, { value: 'log', label: 'Log' }]} />
      <Text>Phase profiles</Text><Switch checked={showPhaseProfiles} onChange={setShowPhaseProfiles} />
      <Text>Bragg positions</Text><Switch checked={showReflections} onChange={value => { setShowReflections(value); setYRange(null); }} />
      <Text>Diagnostics</Text><Switch checked={showDiagnostics} onChange={setShowDiagnostics} />
      <Text>Fit intensity to view</Text><Switch checked={fitY} onChange={value => { setFitY(value); setYRange(null); }} />
      <Button onClick={resetView}>Reset zoom</Button>
    </Space>
    <div style={{ marginBottom: 8 }}>
      <Text type="secondary">Left-drag to pan, right-drag to zoom into a box, mouse wheel to zoom 2θ, double-click or Reset zoom to show the whole pattern. Click a phase in the legend to show its profile and Bragg positions; hover for 2θ, d-spacing and hkl. The lower panel shows observed − calculated.{onPeakClick ? ' Click a missing-peak marker or an observed point to search references for that peak (Shift+click to select several).' : ''} Press ? for shortcuts.</Text>
    </div>
    <Plotly key={`${task_id}-${plot_id}`} data={data}
      layout={{ ...view.layout, autosize: true,
        shapes: [...(view.layout.shapes || []), ...excludedRanges.flatMap(([x0, x1]) => [
          { type: 'rect', xref: 'x', yref: 'y domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
          { type: 'rect', xref: 'x2', yref: 'y2 domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
        ]), ...peakShapes(peaks)],
        dragmode: 'pan',
        uirevision: `${task_id}-${plot_id}-${resetKey}` }}
      onLegendClick={toggleLegend}
      onLegendDoubleClick={isolateLegend}
      onDoubleClick={resetView}
      onClick={handleClick}
      onRelayout={event => {
        const change = viewChangeFromRelayout(event);
        if ('xRange' in change) setXRange(change.xRange);
        if ('yRange' in change) setYRange(change.yRange);
      }}
      config={{ responsive: true, displaylogo: false, scrollZoom: true, doubleClick: false,
        // Ranges are React state: Plotly's own reset/autoscale buttons would only
        // restore the state they were last given, so Reset zoom replaces them.
        modeBarButtonsToRemove: ['zoom2d', 'pan2d', 'select2d', 'lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
        toImageButtonOptions: { format: 'svg', filename: `dara_${task_id}_${String(plot_id ?? 'best').replace(':', '_')}`, width: 1600, height: 900 } }}
      useResizeHandler style={{ height: '760px', width: '100%' }} />
  </>;
});

const COMMON_PHASES = {
  'Al2O3:167': { name: 'Corundum', structure: 'Trigonal' },
  'CaCO3:62': { name: 'Aragonite', structure: 'Orthorhombic' },
  'CaCO3:167': { name: 'Calcite', structure: 'Trigonal' },
  'CaF2:225': { name: 'Fluorite', structure: 'Cubic' },
  'Cr2O3:167': { name: 'Eskolaite', structure: 'Trigonal' },
  'Fe2O3:167': { name: 'Hematite', structure: 'Trigonal' },
  'Fe3O4:227': { name: 'Magnetite', structure: 'Cubic' },
  'Fe:225': { name: 'Austenite (γ-Fe)', structure: 'FCC' },
  'Fe:229': { name: 'Ferrite (α-Fe)', structure: 'BCC' },
  'In2O3:206': { name: 'Bixbyite-type', structure: 'Cubic' },
  'Mg(OH)2:164': { name: 'Brucite', structure: 'Trigonal' },
  'TiO2:61': { name: 'Brookite', structure: 'Orthorhombic' },
  'TiO2:136': { name: 'Rutile', structure: 'Tetragonal' },
  'TiO2:141': { name: 'Anatase', structure: 'Tetragonal' },
  'ZnO:186': { name: 'Zincite', structure: 'Hexagonal' },
};

function crystalSystem(spaceGroup) {
  if (!spaceGroup) return undefined;
  if (spaceGroup <= 2) return 'Triclinic';
  if (spaceGroup <= 15) return 'Monoclinic';
  if (spaceGroup <= 74) return 'Orthorhombic';
  if (spaceGroup <= 142) return 'Tetragonal';
  if (spaceGroup <= 167) return 'Trigonal';
  if (spaceGroup <= 194) return 'Hexagonal';
  return 'Cubic';
}

function parsePhaseReference(reference = '') {
  const match = reference.match(/^(.+?)_(\d+)_\(([^)]+)\)(?:-(.*))?$/);
  if (!match) return { formula: reference };
  const [, formula, spaceGroupText, databaseId] = match;
  const spaceGroup = Number(spaceGroupText);
  return {
    formula,
    spaceGroup,
    databaseId,
    crystalSystem: crystalSystem(spaceGroup),
    common: COMMON_PHASES[`${formula}:${spaceGroup}`],
  };
}

function ChemicalFormula({ formula }) {
  const parts = String(formula || '').split(/(\d+(?:\.\d+)?)/g).filter(Boolean);
  return <>{parts.map((part, index) => /^\d/.test(part) ? <sub key={index}>{part}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
}

function SpaceGroupSymbol({ symbol }) {
  const parts = String(symbol || '').split(/(_\d+)/g).filter(Boolean);
  return <>{parts.map((part, index) => part.startsWith('_') ? <sub key={index}>{part.slice(1)}</sub> : <React.Fragment key={index}>{part}</React.Fragment>)}</>;
}

function PhaseIdentity({ reference, formulaHtml, metadata, compact = false }) {
  const parsedPhase = parsePhaseReference(reference);
  const spaceGroup = metadata?.spacegroup_number || parsedPhase.spaceGroup;
  const phase = {
    ...parsedPhase,
    spaceGroup,
    crystalSystem: crystalSystem(spaceGroup),
    common: COMMON_PHASES[`${parsedPhase.formula}:${spaceGroup}`],
  };
  const formula = formulaHtml ? parse(formulaHtml) : <ChemicalFormula formula={phase.formula} />;
  const primary = phase.common ? <>{phase.common.name} · {formula}</> : formula;
  const structure = phase.common?.structure || phase.crystalSystem;
  const spaceGroupLabel = metadata?.spacegroup_symbol
    ? <><SpaceGroupSymbol symbol={metadata.spacegroup_symbol} /> {`(No. ${phase.spaceGroup})`}</>
    : phase.spaceGroup ? `Space group No. ${phase.spaceGroup}` : null;
  const secondary = [structure, spaceGroupLabel]
    .filter(Boolean)
    .map((item, index) => <React.Fragment key={index}>{index ? ' · ' : ''}{item}</React.Fragment>);

  return (
    <span className="phase-identity" style={{ display: 'inline-flex', flexDirection: 'column', lineHeight: 1.35 }}>
      <span className="phase-identity-primary" style={{ fontWeight: 600 }}>{primary}</span>
      {!compact && secondary ? <span className="phase-identity-secondary" style={{ fontSize: '0.86em', color: '#666' }}>{secondary}</span> : null}
    </span>
  );
}

function PhaseList({ phases, highlighted_phases, grouped_phases, show_alternatives, weight_percent, phase_metadata }) {
  return (
    <List
      size="small"
      dataSource={phases}
      renderItem={(item, index) => {
        const selectedPhase = highlighted_phases[index];
        const weight = weight_percent?.[selectedPhase];
        const groups = grouped_phases?.[index] || [];
        const selectedGroup = groups.find(group => group[1].includes(selectedPhase));
        const alternatives = [...new Set(item.filter(phase => phase !== selectedPhase))];
        return (<List.Item key={index}>
          <List.Item.Meta
            title={<Space>
              <span>{`Phase ${index + 1}`}</span>
              <span className='representative-phase'>
                <PhaseIdentity reference={selectedPhase} formulaHtml={selectedGroup?.[0]} metadata={phase_metadata?.[selectedPhase]} />
              </span>
              {weight !== undefined ? <Tag color="blue">{Number(weight).toFixed(2)} wt%</Tag> : null}
            </Space>}
            description={
              <div className='phases-container'>
                <div>
                  <Text type="secondary">Selected reference: </Text>
                  <Tooltip title="Name format: formula_space group_(database_ID)-energy above hull. 'None' means that energy metadata is unavailable.">
                    <code className="highlight">{selectedPhase}</code>
                  </Tooltip>
                </div>
                {alternatives.length ? (
                  show_alternatives ? (
                    <div>
                      <Text type="secondary">Alternative matching CIFs: </Text>
                      {alternatives.map((phase, alternativeIndex) => (
                        <span key={phase}>{alternativeIndex ? " | " : ""}<code className="no-highlight">{phase}</code></span>
                      ))}
                    </div>
                  ) : <Text type="secondary">+ {alternatives.length} alternative matching CIF reference{alternatives.length === 1 ? "" : "s"} hidden</Text>
                ) : null}
              </div>
            }
          />
        </List.Item>);
      }}
    />
  );
}

function PeakDiagnosticTags({ diagnostics }) {
  if (!diagnostics) return <Text type="secondary">Diagnostics unavailable</Text>;
  const counts = diagnostics?.counts || {};
  const unexplained = unexplainedSummary(diagnostics);
  return (
    <Space size="small" wrap>
      {unexplained ? (
        <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{unexplainedTooltipLines(diagnostics).join('\n') || 'No unexplained peaks.'}</div>}>
          <Tag color={unexplained.color}>{unexplained.text}</Tag>
        </Tooltip>
      ) : (
        <Tag color={counts.missing ? 'volcano' : 'default'}>{`Missing ${counts.missing || 0}`}</Tag>
      )}
      <Tag color={counts.extra ? 'geekblue' : 'default'}>{`Extra ${counts.extra || 0}`}</Tag>
      <Tag color={counts.intensity_mismatch ? 'gold' : 'default'}>{`Intensity mismatch ${counts.intensity_mismatch || 0}`}</Tag>
    </Space>
  );
}

function formatMetric(value, digits = 2) {
  return value === null || value === undefined || Number.isNaN(Number(value))
    ? 'Unavailable' : Number(value).toFixed(digits);
}

function FitQualitySummary({ quality }) {
  if (!quality) return <Text type="secondary">Fit statistics unavailable</Text>;
  return <Space size="small" wrap>
    <Tooltip title="Weighted profile residual"><Tag color="green">Rwp {formatMetric(quality.rwp)}%</Tag></Tooltip>
    <Tooltip title="Profile residual"><Tag>Rp {formatMetric(quality.rp)}%</Tag></Tooltip>
    <Tooltip title="Statistically expected Rwp"><Tag>Rexp {formatMetric(quality.rexp)}%</Tag></Tooltip>
    <Tooltip title="Goodness of fit = Rwp / Rexp; values near 1 are ideal only when the statistical model is appropriate."><Tag color={quality.gof > 2 ? 'orange' : 'blue'}>GOF {formatMetric(quality.gof)}</Tag></Tooltip>
    <Tooltip title="χ² = (Rwp / Rexp)²"><Tag>χ² {formatMetric(quality.chi_squared)}</Tag></Tooltip>
    <Tooltip title="Durbin–Watson statistic for residual correlation"><Tag>DW {formatMetric(quality.durbin_watson)}</Tag></Tooltip>
    <Tag>{`2θ ${formatMetric(quality.two_theta_min)}–${formatMetric(quality.two_theta_max)}°`}</Tag>
    <Tag>{`${quality.data_points ?? '—'} points`}</Tag>
    <Tag>{`${quality.iterations ?? '—'} iterations`}</Tag>
  </Space>;
}

const STOP_STAGE_LABEL = { screening: 'reference screening', search: 'phase search', final_refinement: 'final refinement' };

function SearchSummary({ summary = {} }) {
  if (!Object.keys(summary).length) return <Text type="secondary">Detailed search counts were not stored for this older result.</Text>;
  return <Space size="small" wrap>
    <Tag>{`${summary.screening_results_retained ?? 0} retained tree result(s)`}</Tag>
    <Tag>{`${summary.combinations_evaluated ?? 0} combinations evaluated`}</Tag>
    <Tag color="blue">{`${summary.structurally_distinct_considered ?? 0} distinct combinations considered`}</Tag>
    <Tag>{`${summary.equivalent_combinations_merged ?? 0} equivalent combinations merged`}</Tag>
    <Tag color="green">{`${summary.displayed_hypotheses ?? 0} displayed`}</Tag>
    {summary.final_refinement_failed ? <Tag color="volcano">{`${summary.final_refinement_failed} final refinement failed`}</Tag> : null}
    {summary.stopped_early ? <Tag color="red">{`Stopped at time limit (${STOP_STAGE_LABEL[summary.stopped_at_stage] || 'search'})`}</Tag> : null}
    {summary.final_refinement_skipped ? <Tag color="orange">{`${summary.final_refinement_skipped} final refinement(s) skipped (time limit)`}</Tag> : null}
  </Space>;
}

function parameterText(name, parameter) {
  const value = formatParameterWithEsd(parameter);
  return value ? `${name} = ${value}` : null;
}

function PhaseRefinementTable({ phases = [], metadata = {}, details = {}, confidence = null, rowActions = null }) {
  const dataSource = phases.map(phase => ({ phase, ...(details?.[phase] || {}) }));
  const verdictColumn = confidence?.phases ? [{
    title: <Tooltip title="Evidence-based assessment of whether each phase is supported by the data. Hover a tag for the reasons."><span>Verdict</span></Tooltip>,
    key: 'verdict', width: 150,
    sorter: (a, b) => levelRank(confidence.phases[a.phase]?.level) - levelRank(confidence.phases[b.phase]?.level),
    render: (_, row) => <VerdictTag verdict={confidence.phases[row.phase]} /> }] : [];
  return <Table size="small" pagination={false} rowKey="phase" scroll={{ x: 1000 }} dataSource={dataSource}
    columns={[
      { title: 'Phase', dataIndex: 'phase', width: 260,
        render: phase => <PhaseIdentity reference={phase} metadata={metadata?.[phase]} /> },
      ...verdictColumn,
      { title: <Tooltip title="Normalized over modeled crystalline phases; amorphous and unidentified material are excluded."><span>Quantity</span></Tooltip>,
        render: (_, row) => row.weight_percent == null ? 'Unavailable' : <span>{Number(row.weight_percent).toFixed(2)} wt%{row.weight_esd_percent == null ? '' : ` ± ${Number(row.weight_esd_percent).toFixed(2)}`}</span> },
      { title: <Tooltip title="± values are BGMN estimated standard deviations. Equalities and fixed angles are imposed by the reported space group, not independently refined."><span>Lattice parameters</span></Tooltip>, width: 420,
        render: (_, row) => {
          const spaceGroup = metadata?.[row.phase]?.spacegroup_number;
          const values = latticeParameterRows(row.lattice_parameters || {}, spaceGroup);
          return values.length ? <Space direction="vertical" size={1}>
            {values.map((value, index) => <span key={`${value.label}-${index}`}>
              {parameterText(value.label, value.parameter)}
              {value.constrained ? <Tooltip title={value.fixed
                ? 'Fixed by space-group symmetry; this was not an independently refined parameter.'
                : 'The additional axis value is constrained equal by space-group symmetry.'}>
                <Tag style={{ marginLeft: 6 }} color="default">constrained</Tag>
              </Tooltip> : null}
            </span>)}
          </Space> : 'Unavailable';
        } },
      { title: 'Density', render: (_, row) => row.xray_density == null ? 'Unavailable' : `${Number(row.xray_density).toFixed(3)} g/cm³` },
      { title: 'Rphase', render: (_, row) => row.rphase == null ? 'Unavailable' : `${Number(row.rphase).toFixed(2)}%` },
      { title: 'Crystallite size', render: (_, row) => row.grain_sizes?.length
        ? row.grain_sizes.map(value => `${Number(value.value).toFixed(1)}${value.esd == null ? '' : ` ± ${Number(value.esd).toFixed(1)}`} ${value.unit}`).join('; ')
        : 'Not refined/reported' },
      ...(rowActions ? [{ title: 'Actions', key: 'row-actions', width: 160, render: (_, row) => rowActions(row) }] : []),
    ]} />;
}


function normalizePlotId(value) {
  if (value === undefined) return undefined;
  if (value === null || value === 'best') return null;
  const parsed = parseSelection(value);
  if (parsed === null) return undefined;
  if (parsed === 'best') return null;
  return typeof parsed === 'number' ? parsed : `version:${parsed.version}`;
}

function DeltaStrip({ rows }) {
  const shown = rows.filter(row => row.a !== null || row.b !== null);
  if (!shown.length) return null;
  return <div style={{ marginBottom: 8 }}>
    <Space wrap size={[4, 4]}>
      <Text type="secondary">Change from the base fit:</Text>
      {shown.map(row => <Tag key={row.key} color={row.delta === null || row.key.startsWith('wt:') ? 'default' : row.better ? 'green' : row.delta > 0 ? 'orange' : 'default'}>
        {`${row.label} ${deltaText(row)}`}
      </Tag>)}
    </Space>
  </div>;
}

function HintText({ diagnostics }) {
  const hints = collectHints(diagnostics);
  if (!hints.length) return null;
  return <div style={{ marginTop: 8 }} aria-label="Trace phase hints">
    {hints.map(hint => <div key={hint.phase}><Text type="secondary">{hintText(hint)}</Text></div>)}
    <Text type="secondary">{'Use “Add phase…” to add one of these.'}</Text>
  </div>;
}

function ShortcutHelp({ open, onClose }) {
  return <Modal title="Keyboard shortcuts" open={open} onCancel={onClose} footer={null} keyboard={false}
    wrapClassName={HELP_MODAL_CLASS} width={420}>
    <Table size="small" pagination={false} rowKey="keys" dataSource={SHORTCUT_HELP}
      columns={[{ title: 'Keys', dataIndex: 'keys', width: 110, render: keys => <code>{keys}</code> }, { title: 'Action', dataIndex: 'action' }]} />
  </Modal>;
}

function TaskDetail({ data, task_id }) {
  const [plot_id, setPlotIdRaw] = React.useState(undefined);
  const setPlotId = React.useCallback(value => setPlotIdRaw(normalizePlotId(value)), []);
  const [showAlternatives, setShowAlternatives] = React.useState(false);
  const review = useTaskReview(task_id);
  const store = useVersions(task_id);
  const [versionDetails, setVersionDetails] = React.useState({});
  const [versionError, setVersionError] = React.useState('');
  const [compare, setCompare] = React.useState(null);
  const [peaks, setPeaks] = React.useState([]);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [preview, setPreview] = React.useState(null);
  const [helpOpen, setHelpOpen] = React.useState(false);
  const [phaseGroups, setPhaseGroups] = React.useState([]);
  const plotRef = React.useRef(null);
  const plotIdRef = React.useRef(plot_id);
  plotIdRef.current = plot_id;
  const peaksRef = React.useRef(peaks);
  peaksRef.current = peaks;
  const refreshVersions = store.refresh;

  const handleVersion = React.useCallback(async (n, job, meta = {}) => {
    const list = await refreshVersions();
    const item = list.find(entry => entry.version === n);
    const rwpText = item?.rwp != null && Number.isFinite(Number(item.rwp)) ? ` (Rwp ${Number(item.rwp).toFixed(2)} %)` : '';
    if (meta.afterDone === 'compare') {
      message.success(`Version v${n} ready${rwpText}`);
      setCompare({ a: comparisonBase(meta.base ?? 'best', list, n), b: `version:${n}` });
    } else if (plotIdRef.current !== undefined) {
      message.success(`Version v${n} ready${rwpText}`);
      setPlotId(`version:${n}`);
    } else {
      message.success(<span>{`Version v${n} ready${rwpText} `}<a onClick={() => setPlotId(`version:${n}`)}>View</a></span>);
    }
  }, [refreshVersions, setPlotId]);
  const quick = useQuickRefine(task_id, { onVersion: handleVersion });
  const quickShown = quick.available !== false && store.available !== false;

  const firstPlotId = data.all_results[0]?.source_index ?? (data.all_results.length ? 0 : null);
  React.useEffect(() => {
    const warm = () => preloadPlot(task_id, firstPlotId);
    if (window.requestIdleCallback) {
      const handle = window.requestIdleCallback(warm, { timeout: 1500 });
      return () => window.cancelIdleCallback(handle);
    }
    const handle = window.setTimeout(warm, 250);
    return () => window.clearTimeout(handle);
  }, [task_id, firstPlotId]);
  const referenceMetadata = Object.assign({}, data.final_result.phase_metadata,
    ...data.all_results.map(result => result.phase_metadata));
  const renderReference = reference => <PhaseIdentity reference={reference} metadata={referenceMetadata[reference]} />;

  const currentSel = plot_id === null || plot_id === undefined ? 'best' : plot_id;
  const versionNumber = typeof plot_id === 'string' ? (parseSelection(plot_id)?.version ?? null) : null;
  const versionData = versionNumber ? versionDetails[versionNumber] : null;
  // Deltas against 'best' use the baseline version (same code as the new version) when it exists.
  const effectiveBase = versionData ? comparisonBase(versionData.origin?.base ?? 'best', store.versions, versionNumber) : null;
  const baseParsed = effectiveBase !== null ? parseSelection(effectiveBase) : null;
  const baseVersionNumber = baseParsed && typeof baseParsed === 'object' ? baseParsed.version : null;
  React.useEffect(() => {
    const wanted = [versionNumber, baseVersionNumber].filter(n => n && !versionDetails[n]);
    if (!wanted.length) return undefined;
    let active = true;
    setVersionError('');
    wanted.forEach(n => {
      requestJson(`/task/${task_id}/versions/${n}`)
        .then(body => { if (active) setVersionDetails(previous => ({ ...previous, [n]: body })); })
        .catch(error => { if (active) setVersionError(error.missing ? NOT_AVAILABLE : error.message); });
    });
    return () => { active = false; };
  }, [task_id, versionNumber, baseVersionNumber, versionDetails]);
  React.useEffect(() => {
    // A different fit has different peaks: drop the selection and any tick preview.
    setPeaks([]);
    setPreview(null);
    setDrawerOpen(false);
  }, [plot_id]);

  const displayedResult = plot_id === undefined ? undefined
    : plot_id === null ? data.final_result
      : versionNumber ? versionData?.final_result
        : data.all_results.find((result, index) => (result.source_index ?? index) === plot_id);
  const findSummary = sel => {
    const parsed = parseSelection(sel);
    if (parsed === 'best' || parsed === null) return data.final_result;
    if (typeof parsed === 'number') return data.all_results.find((result, index) => (result.source_index ?? index) === parsed);
    return versionDetails[parsed.version]?.final_result;
  };
  const baseSummary = versionData ? findSummary(effectiveBase) : null;
  const deltaRows = versionData?.final_result && baseSummary ? deltaStrip(baseSummary, versionData.final_result) : [];
  const displayedPhases = displayedResult ? (displayedResult.highlighted_phases || displayedResult.phases || []) : [];
  // Explicit background polynomial degree of the displayed fit (null: BGMN chooses it).
  const baseDegree = versionNumber ? (versionData?.background?.degree ?? null)
    : (data.final_refinement_params?.background_degree ?? null);

  const baselineQueued = React.useRef(false);
  const runQuick = async (body, meta = {}) => {
    if (quick.busy) {
      message.info('A re-refinement is already running for this task.');
      return;
    }
    const request = { base: currentSel, ...body };
    if (!baselineQueued.current && needsBaseline(store.baselineStale, store.versions, request)) {
      // The stored result predates the current refinement code: queue a baseline with today's code
      // first (silently; the server runs jobs in order) so comparisons use the same code.
      baselineQueued.current = true;
      try {
        await postJson(`/task/${task_id}/quick-refine`, baselineRequest());
      } catch (_) {
        baselineQueued.current = false; // not queued; the user's own job still runs
      }
    }
    quick.start(request, { base: currentSel, afterDone: 'view', ...meta });
  };
  const addHint = (hint, target) => runQuick({
    phases: [target], origin: 'add_hint', label: `Added ${hint.formula || hint.phase} (hint)`,
  });
  // The API nests the alternatives under `verdict.evidence`.
  const alternativeOf = phase => alternativeOfVerdict(displayedResult?.confidence?.phases?.[phase]);
  const rowActions = row => {
    const alternative = alternativeOf(row.phase);
    const swap = alternative ? swapRequest({ phase: row.phase }, alternative, data.all_results, displayedPhases, displayedResult?.phase_metadata) : null;
    const otherSet = alternative && swapKind(alternative) === 'phase_set' && alternative.source_index != null ? alternative : null;
    return <Space size="small" wrap>
      {swap ? <Tooltip title={`Replace this phase with the other structure model of the same compound from candidate ${alternative.candidate_number ?? ''}, refine again, then compare`}>
        <Button size="small" disabled={quick.busy} onClick={() => runQuick(swap, { afterDone: 'compare' })}>Swap</Button>
      </Tooltip> : null}
      {otherSet ? <Tooltip title="A candidate phase set explains this region with other phases; this shows its stored fit without refining again">
        <Button size="small" onClick={() => setPlotId(otherSet.source_index)}>{`View candidate ${otherSet.candidate_number ?? otherSet.source_index + 1}`}</Button>
      </Tooltip> : null}
      {displayedPhases.length >= 2 ? <Tooltip title="Refine again without this phase, then compare">
        <Button size="small" disabled={quick.busy} onClick={() => runQuick(removeRequest(row.phase), { afterDone: 'compare' })}>Remove</Button>
      </Tooltip> : null}
    </Space>;
  };

  const onPeakClick = (peak, shift) => {
    const next = updatePeakSelection(peaksRef.current, peak, { shift });
    setPeaks(next);
    setDrawerOpen(next.length > 0);
    if (!next.length) setPreview(null);
  };
  const clearPeaks = () => { setPeaks([]); setPreview(null); setDrawerOpen(false); };
  const addCandidate = candidate => {
    setDrawerOpen(false);
    setPreview(null);
    runQuick({
      phases: [{ ref: candidate.ref, source: candidate.source }], origin: 'add_peak_search',
      label: `Added ${candidate.formula || String(candidate.ref).replace(/_\d+_\(.*$/, '')} (peak search)`,
    });
  };

  const options = [
    'best',
    ...data.all_results.map((result, index) => result.source_index ?? index),
    ...store.versions.filter(item => item.version > 1).map(item => `version:${item.version}`),
  ];
  const keyContext = React.useRef({});
  keyContext.current = { options, current: currentSel, phaseGroups, peakSelectionCount: peaks.length, helpOpen };
  useKeyboardShortcuts(plot_id !== undefined && !compare, keyContext, {
    select: action => setPlotId(action.value === 'best' ? null : action.value),
    'toggle-phase': action => plotRef.current?.togglePhase(action.name),
    reset: () => plotRef.current?.reset(),
    'fit-y': () => plotRef.current?.toggleFitY(),
    help: () => setHelpOpen(true),
    'close-help': () => setHelpOpen(false),
    'clear-peaks': clearPeaks,
    close: () => setPlotId(undefined),
  });
  const modalTitle = plot_id === undefined ? 'Fit'
    : plot_id === null ? 'Best fit'
      : versionNumber ? selectionLabel(plot_id, { versions: store.versions })
        : `Candidate ${(data.all_results.findIndex((r, i) => (r.source_index ?? i) === plot_id)) + 1} — refinement`;
  const selectOptions = selectOptionGroups(data.all_results, store.versions);
  const [backgroundOpen, setBackgroundOpen] = React.useState(false);
  const firstOtherCandidate = data.all_results.length
    ? (data.all_results[1] ?? data.all_results[0]).source_index ?? (data.all_results.length > 1 ? 1 : 0) : null;

  return (
    <>
      <TitleContriner>
        <h3>Task details (<a onMouseEnter={() => preloadPlot(task_id, null)} onFocus={() => preloadPlot(task_id, null)} onClick={() => setPlotId(null)}>Visualize final result</a>)</h3>
        <Space wrap>
          <DownloadButton taskId={task_id} type="primary" icon={<DownloadOutlined />}>
            Download all results
          </DownloadButton>
          <Button icon={<TableOutlined />} href={`${URL}/task/${task_id}/table.csv`}>Table (CSV)</Button>
          <FigureExportActions taskId={task_id} />
        </Space>
      </TitleContriner>
      <Divider />
      <ConfidenceBanner confidence={data.final_result?.confidence} />
      <ReviewPanel taskId={task_id} data={data} review={review} versions={store.versions} />
      <Descriptions bordered column={2} size='small'>
        <Descriptions.Item label="Pattern file name" span={2}>{data.task_label}</Descriptions.Item>
        <Descriptions.Item label="Best_Rwp" span={2}><Tag color="green">{data.best_rwp} %</Tag></Descriptions.Item>
        <Descriptions.Item label="Analysis mode" span={2}>{({
          known_mixture: 'Known-phase refinement',
          elemental: 'Phase identification',
          reaction: 'Reaction check',
          selected_phases: 'User-selected phase refinement',
        })[data.analysis_mode] || data.analysis_mode || (data.use_rxn_predictor ? "Reaction check" : "Phase identification")}</Descriptions.Item>
        <Descriptions.Item label="Temperature" span={2}>{data.temperature || "None"}</Descriptions.Item>
        <Descriptions.Item label="Use reaction network?" span={2}>{data.use_rxn_predictor ? "Yes" : "No"}</Descriptions.Item>
        <Descriptions.Item label="Included elements" span={2}>{(data.elements || []).join(', ') || "None"}</Descriptions.Item>
        <Descriptions.Item label="Reference databases" span={2}>{(data.candidate_sources || []).map(source => source.toUpperCase()).join(' + ') || "Uploaded CIFs"}</Descriptions.Item>
        <Descriptions.Item label="Candidate CIFs" span={2}>{data.candidate_summary?.after ?? "Unknown"} retained ({data.candidate_summary?.removed_duplicates ?? 0} duplicates removed)
          <ScreeningTags candidateSummary={data.candidate_summary} />
        </Descriptions.Item>
        <Descriptions.Item label="Hypothesis search" span={2}><SearchSummary summary={data.search_summary} /></Descriptions.Item>
        <Descriptions.Item label="Additional search options" span={2}><code style={{backgroundColor: 'transparent'}}>{JSON.stringify(data.additional_search_options)}</code></Descriptions.Item>
        {data.additional_search_options?.refinement_params?.excluded_ranges?.length ? <Descriptions.Item label="Excluded 2θ intervals" span={2}>
          {data.additional_search_options.refinement_params.excluded_ranges.map(([lo, hi]) => `${lo}–${hi}°`).join(', ')} (shaded in the fit plot; omitted from fitting)
        </Descriptions.Item> : null}
        {data.search_summary?.parent_task_id ? <Descriptions.Item label="Derived from" span={2}>
          <a href={`/task/?id=${data.search_summary.parent_task_id}`}>Task {data.search_summary.parent_task_id}</a> — selected-phase refinement; original result retained
        </Descriptions.Item> : null}
        <Descriptions.Item label="Precursor / expected formulas" span={2}>{(data.precursors || []).join(', ') || "None"}</Descriptions.Item>
        <Descriptions.Item label="Final phases" span={2}>
          <Space size="large" wrap>
            {data.final_result.phases.map(item => <PhaseIdentity key={item} reference={item} metadata={data.final_result.phase_metadata?.[item]} />)}
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="Fit diagnostics" span={2}>
          <PeakDiagnosticTags diagnostics={data.final_result.peak_diagnostics} />
        </Descriptions.Item>
        <Descriptions.Item label="Refinement quality" span={2}>
          <FitQualitySummary quality={data.final_result.fit_quality} />
          <div style={{ marginTop: 4 }}><DisplacementTag confidence={data.final_result.confidence} /></div>
          <FitCautions cautions={data.final_result.cautions} />
        </Descriptions.Item>
        <Descriptions.Item label={<span>
          Quantitative phase analysis
          <Tooltip title="Rietveld weight fractions normalized to 100% across the identified crystalline phases. Amorphous or unidentified material is not included.">
            <InfoCircleOutlined style={{marginLeft: "4px"}} />
          </Tooltip>
        </span>} span={2}>
          {Object.keys(data.final_result.weight_percent || {}).length ? (
            Object.entries(data.final_result.weight_percent).map(([phase, weight]) => (
              <div key={phase} style={{ marginBottom: "8px" }}>
                <Space>
                  <PhaseIdentity reference={phase} metadata={data.final_result.phase_metadata?.[phase]} />
                  <Tag color="blue">{Number(weight).toFixed(2)} wt%
                    {data.final_result.phase_details?.[phase]?.weight_esd_percent == null
                      ? '' : ` ± ${Number(data.final_result.phase_details[phase].weight_esd_percent).toFixed(2)}`}
                  </Tag>
                </Space>
              </div>
            ))
          ) : "Unavailable"}
        </Descriptions.Item>
        <Descriptions.Item label="Start time" span={1}>{data.start_time}</Descriptions.Item>
        <Descriptions.Item label="End time" span={1}>{data.end_time}</Descriptions.Item>
        <Descriptions.Item label="Duration" span={2}><Tag color="cyan">{data.runtime.toFixed(2)} s</Tag></Descriptions.Item>
      </Descriptions>
      <h3 style={{ marginTop: 20 }}>Final refined phase parameters</h3>
      <Space wrap style={{ marginBottom: 12 }}>
        <ReportDownloadButton taskId={task_id} selection="best" disabled={review.loading || review.saving || review.dirty || !!review.error}>Best-fit report</ReportDownloadButton>
      </Space>
      <Text type="secondary">Uncertainties are BGMN estimated standard deviations where reported. Weight uncertainty is propagated after normalization and is omitted when unavailable.</Text>
      <div style={{ marginTop: 10 }}>
        <PhaseRefinementTable phases={data.final_result.phases} metadata={data.final_result.phase_metadata} details={data.final_result.phase_details} confidence={data.final_result.confidence} />
      </div>
      <VersionsPanel taskId={task_id} store={store} review={review}
        onView={setPlotId} onCompare={(a, b) => setCompare({ a, b })}
        extra={quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null} />
      <ResultTitleContainer>
        <h3 style={{ marginTop: "16px" }}>{`Distinct candidate phase sets (${data.all_results.length} shown, up to 5)`}</h3>
        <div>
          Show alternative CIF references
          <Tooltip title="The selected CIF is shown by default. Enable this to inspect other database records that DARA grouped as matching references for the same phase slot.">
            <InfoCircleOutlined style={{marginLeft: "4px"}}/>
          </Tooltip> : 
          <Switch checked={showAlternatives} onChange={setShowAlternatives} style={{marginLeft: "4px"}}/>
        </div>
      </ResultTitleContainer>
      <Divider />
      <h3>Candidate comparison</h3>
      {data.instrument_profile_details ? <Alert type={data.instrument_profile_details.verified ? 'info' : 'warning'} showIcon style={{marginBottom:12}}
        message={`Instrument: ${data.instrument_profile_details.label} — ${data.instrument_profile_details.verified ? 'operator-verified' : 'unverified'}`}
        description={<>{data.instrument_profile_details.geometry} · {data.instrument_profile_details.radiation}<br />{data.instrument_profile_details.notes}</>} /> : null}
      <Button disabled={firstOtherCandidate === null} style={{ marginBottom: 8 }}
        onClick={() => setCompare({ a: 'best', b: firstOtherCandidate })}>Compare fits</Button>
      {data.final_refinement_params ? <details style={{marginBottom:12}}><summary>Final-refinement settings used</summary><pre>{JSON.stringify(data.final_refinement_params, null, 2)}</pre></details> : null}
      <Text type="secondary">Sort columns to compare candidates. Candidate numbers and downloads keep their original identity. Fractions are normalized over modeled crystalline phases; diagnostic counts are review flags, not confidence scores.</Text>
      <Table size="small" pagination={false} scroll={{ x: 900 }} style={{ marginTop: 12, marginBottom: 24 }}
        rowKey="source_index"
        dataSource={data.all_results.map((item, index) => ({ ...item, candidate_number: index + 1, source_index: item.source_index ?? index }))}
        columns={[
          { title: 'Candidate', dataIndex: 'candidate_number', render: n => `Candidate ${n}` },
          { title: 'Phases / wt%', render: (_, row) => <Space direction="vertical">{row.highlighted_phases.map(phase => <span key={phase}><PhaseIdentity reference={phase} compact /> {row.weight_percent?.[phase] != null ? `${Number(row.weight_percent[phase]).toFixed(2)} wt%` : ''}</span>)}</Space> },
          { title: 'Rwp (%)', dataIndex: 'rwp', sorter: (a,b) => a.rwp-b.rwp, defaultSortOrder: 'ascend', render: (value,row) => <>{value} <Text type="secondary">{row.is_final_refined ? 'Final' : 'Screening'}</Text></> },
          { title: 'GOF', sorter: (a,b) => (a.fit_quality?.gof ?? Infinity)-(b.fit_quality?.gof ?? Infinity), render: (_,row) => formatMetric(row.fit_quality?.gof) },
          { title: 'Missing', sorter: (a,b) => missingCell(a.peak_diagnostics).sortValue-missingCell(b.peak_diagnostics).sortValue, render: (_,row) => {
            const text = missingCell(row.peak_diagnostics).text;
            const hints = quickShown ? collectHints(row.peak_diagnostics) : [];
            return hints.length ? <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{[...hints.map(hintText), '(open the fit view to add)'].join('\n')}</div>}><span>{text}</span></Tooltip> : text;
          } },
          ...[['extra','Extra'],['intensity_mismatch','Intensity mismatch']].map(([key,title]) => ({ title, sorter: (a,b) => (a.peak_diagnostics?.counts?.[key] ?? Infinity)-(b.peak_diagnostics?.counts?.[key] ?? Infinity), render: (_,row) => row.peak_diagnostics?.counts?.[key] ?? '—' })),
          { title: <Tooltip title="Automated review flags for this candidate; hover a count to read them. These are not a verdict.">Cautions</Tooltip>,
            sorter: (a,b) => cautionSortValue(a.cautions)-cautionSortValue(b.cautions),
            render: (_,row) => {
              const counts = countCautions(row.cautions);
              if (!counts) return '—';
              return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{counts.total ? cautionTooltip(row.cautions) : 'No automated cautions.'}</div>}>
                <Tag color={cautionTagColor(row.cautions)}>{counts.total}</Tag>
              </Tooltip>;
            } },
          { title: <Tooltip title="Overall evidence assessment for this candidate. Hover for the reasons.">Confidence</Tooltip>,
            sorter: (a,b) => levelRank(a.confidence?.overall?.level)-levelRank(b.confidence?.overall?.level),
            render: (_,row) => <OverallTag confidence={row.confidence} /> },
          { title: 'No. of phases', sorter: (a,b) => a.highlighted_phases.length-b.highlighted_phases.length, render: (_,row) => row.highlighted_phases.length },
          { title: 'Actions', render: (_,row) => <Space wrap><Button size="small" onMouseEnter={() => preloadPlot(task_id, row.source_index)} onFocus={() => preloadPlot(task_id, row.source_index)} onClick={() => setPlotId(row.source_index)}>View fit</Button><DownloadButton taskId={task_id} resultIndex={row.source_index} size="small" icon={<DownloadOutlined />} aria-label={`Download candidate ${row.candidate_number}`} /></Space> },
        ]} />
      {data.duplicate_results_removed > 0 ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: "12px" }}
          message={`${data.duplicate_results_removed} equivalent duplicate search path${data.duplicate_results_removed === 1 ? "" : "s"} merged`}
          description="DARA can reach the same phase set in different phase-addition orders. Only one copy is shown; different compositions or space groups remain separate."
        />
      ) : null}
      <Descriptions bordered column={2} size='small'>
        {
          data.all_results.map((result, index) => {
            return (
              <Descriptions.Item key={index} labelStyle={{ width: "25%" }} label={<>
                <Space size="small" wrap>
                  <span>{`Candidate ${index + 1}`}</span>
                  <Tag color={index === 0 ? "green" : "blue"}>
                    {`${result.is_final_refined ? "Final " : ""}Rwp ${result.rwp} %`}
                  </Tag>
                  {result.is_final_refined && Number(result.screening_rwp) !== Number(result.rwp) ? (
                    <Tooltip title="Fast restricted refinement used to generate and rank candidate phase sets before full final refinement.">
                      <Tag>{`Screening Rwp ${result.screening_rwp} %`}</Tag>
                    </Tooltip>
                  ) : null}
                  {index === 0 && result.is_final_refined ? <Tag color="gold">Best</Tag> : null}
                  <PeakDiagnosticTags diagnostics={result.peak_diagnostics} />
                </Space><br />
                <Space size="small">
                  <a onMouseEnter={() => preloadPlot(task_id, result.source_index ?? index)}
                    onFocus={() => preloadPlot(task_id, result.source_index ?? index)}
                    onClick={() => setPlotId(result.source_index ?? index)}>Visualize result</a>
                  <Tooltip title={`Download search result ${index + 1}`}>
                    <DownloadButton
                      taskId={task_id}
                      resultIndex={result.source_index ?? index}
                      type="text"
                      size="small"
                      shape="circle"
                      aria-label={`Download search result ${index + 1}`}
                      icon={<DownloadOutlined />}
                    />
                  </Tooltip>
                </Space>
              </>} span={2}>
                {/* {result.phases.map((item, index) => (<>{index !== 0 ? <br /> : <></>}<strong>Phase {index + 1}: </strong>{item.map((i, idx) => (<span>{idx !== 0 ? " | " : ""}<code>{i}</code></span>))}</>))} */}
                {result.equivalent_result_count > 1 ? <Tag color="default">{result.equivalent_result_count} equivalent paths combined</Tag> : null}
                <PhaseList phases={result.phases} highlighted_phases={result.highlighted_phases} grouped_phases={result.grouped_phases} show_alternatives={showAlternatives} weight_percent={result.weight_percent} phase_metadata={result.phase_metadata} />
              </Descriptions.Item>
            );
          }
          )
        }
      </Descriptions>
      <Modal title={modalTitle} open={plot_id !== undefined} onCancel={() => setPlotId(undefined)} footer={[]} width={"95vw"}
        keyboard={false} wrapClassName={FIT_MODAL_CLASS}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Select aria-label="Displayed candidate" style={{ width: 360 }} value={currentSel}
            onChange={value => setPlotId(value === 'best' ? null : value)} options={selectOptions} />
          {quickShown && displayedResult ? <>
            <Button onClick={() => setDrawerOpen(true)}>Add phase…</Button>
            <PhaseSelectionButton taskId={task_id} phases={displayedPhases} renderPhase={renderReference} busy={quick.busy}
              onSubmit={request => runQuick(request, { afterDone: 'compare' })} />
            <Dropdown trigger={['click']} menu={{ items: [{ key: 'background', label: 'Background…', disabled: quick.busy }],
              onClick: ({ key }) => { if (key === 'background') setBackgroundOpen(true); } }}>
              <Button>Advanced <DownOutlined /></Button>
            </Dropdown>
            <BackgroundEditor open={backgroundOpen} onOpenChange={setBackgroundOpen} busy={quick.busy} disabled={quick.busy} baseDegree={baseDegree}
              getView={() => plotRef.current?.getViewRange?.() || null}
              onSubmit={(background, label) => runQuick({ phases: [], background, origin: 'background', label })} />
          </> : null}
          <Tooltip title="Left and right arrow keys step through the fits; press ? for all shortcuts"><Text type="secondary">← / → to switch fits</Text></Tooltip>
        </Space>
        {quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null}
        {versionError ? <Alert type="error" showIcon message={versionError} style={{ marginBottom: 12 }} /> : null}
        {versionNumber && !versionData && !versionError ? <Spin /> : null}
        {displayedResult ? <>
          <Space wrap style={{ marginBottom: 12 }}>
            <ReportDownloadButton taskId={task_id} selection={currentSel} disabled={review.loading || review.saving || review.dirty || !!review.error}>This fit's report</ReportDownloadButton>
            <FigureExportActions taskId={task_id} selection={currentSel} />
          </Space>
          {versionNumber ? <DeltaStrip rows={deltaRows} /> : null}
          <div style={{ marginBottom: 12 }}>
            <FitQualitySummary quality={displayedResult.fit_quality} />
            <div style={{ marginTop: 4 }}>
              <OverallTag confidence={displayedResult.confidence} />
              <DisplacementTag confidence={displayedResult.confidence} />
            </div>
            <FitCautions cautions={displayedResult.cautions} />
            {quickShown ? <HintText diagnostics={displayedResult.peak_diagnostics} /> : null}
          </div>
          <PhaseRefinementTable phases={displayedPhases}
            metadata={displayedResult.phase_metadata} details={displayedResult.phase_details} confidence={displayedResult.confidence}
            rowActions={quickShown ? rowActions : null} />
          <Divider />
        </> : null}
        {peaks.length ? <Space wrap style={{ marginBottom: 8 }}>
          <Text type="secondary">Selected peaks:</Text>
          {peaks.map(peak => <Tag key={peak} closable color="volcano"
            onClose={event => { event.preventDefault(); onPeakClick(peak, true); }}>{`${peak.toFixed(2)}°`}</Tag>)}
          <Button size="small" onClick={() => setDrawerOpen(true)}>Show candidates</Button>
          <Button size="small" onClick={clearPeaks}>Clear</Button>
        </Space> : null}
        <RefinementPlot ref={plotRef} task_id={task_id} plot_id={plot_id}
          excludedRanges={[...(data.additional_search_options?.refinement_params?.excluded_ranges || []), ...(versionData?.background?.excluded_ranges || [])]}
          peaks={peaks} previewSticks={preview?.sticks || null} onGroups={setPhaseGroups}
          onPeakClick={quickShown ? onPeakClick : undefined} />
      </Modal>
      <PeakCandidatesDrawer taskId={task_id} sel={currentSel} peaks={peaks} open={drawerOpen && plot_id !== undefined}
        diagnostics={displayedResult?.peak_diagnostics} onAddHint={(hint, target) => { setDrawerOpen(false); addHint(hint, target); }}
        onClose={() => setDrawerOpen(false)} onPreview={setPreview} previewRef={preview?.key} onAdd={addCandidate} busy={quick.busy} />
      <FitCompare taskId={task_id} a={compare?.a} b={compare?.b} open={Boolean(compare)} onClose={() => setCompare(null)}
        adopting={review.saving} allResults={data.all_results} versions={store.versions} onSelect={(a, b) => setCompare({ a, b })}
        onAdoptB={async sel => { await review.save(sel); setCompare(null); }}
        onViewB={sel => { setCompare(null); setPlotId(sel); }} />
      <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </>
  )
}

function TaskDetailedUnfinished({ data, taskId }) {
  const { status, submitted_on, start_time, end_time, error_tb, error_summary } = data;
  return (<>
    {error_summary ? <Alert type="error" showIcon style={{ marginBottom: 12 }}
      message={error_summary.title} description={error_summary.hint} /> : null}
    {status === 'FIZZLED' ? <RetryActions taskId={taskId} errorSummary={error_summary} /> : null}
    <Descriptions bordered column={1} size='small'>
    <Descriptions.Item label="Name" span={1}>{data.task_label}</Descriptions.Item>
    <Descriptions.Item label="Status" span={1}><Tag color={STATE_COLOR[status]}>{status}</Tag></Descriptions.Item>
    <Descriptions.Item label="Submitted on" span={1}>{submitted_on}</Descriptions.Item>
    {start_time ? <Descriptions.Item label="Start time" span={1}>{start_time}</Descriptions.Item> : <></>}
    {end_time ? <Descriptions.Item label="End time" span={1}>{end_time}</Descriptions.Item> : <></>}
    {error_tb ? <Descriptions.Item label="Error details" span={1}>
      <details>
        <summary>Technical details (traceback)</summary>
        <div className="error-code"><pre style={{ whiteSpace: "break-spaces", fontSize: "0.9em" }}>{error_tb}</pre></div>
      </details>
    </Descriptions.Item> : <></>}
  </Descriptions>
  </>)
}


function TaskPage({ location }) {
  const queryParameters = new URLSearchParams(location.search)  
  const task_id = queryParameters.get('id')
  const [error, setError] = React.useState("");

  const [data, setData] = React.useState(undefined);
  const [statusRevision, setStatusRevision] = React.useState('');
  const handleStatusChange = React.useCallback(status => setStatusRevision(status), []);
  const deleteTask = () => {
    Modal.confirm({
      title: `Delete task ${task_id}?`,
      content: 'This permanently removes the stored result.',
      okText: 'Delete',
      okButtonProps: { danger: true },
      cancelText: 'Cancel',
      async onOk() {
        const response = await fetch(`${URL}/task/${task_id}`, { method: 'DELETE' });
        if (!response.ok) {
          const body = await response.json();
          throw new Error(body.detail || 'Could not delete the result.');
        }
        window.location.href = '/results';
      },
    });
  };
  React.useEffect(() => {
    // handle 404 error
    fetch(`${URL}/task/${task_id}`)
      .then(response => {
        if (response.status === 404 || response.status === 400) {
          return response.json().then(data => setError(data.detail)).then(() => null);
        }
        return response.json();
      })
      .then(data => setData(data));
  }, [task_id, statusRevision]);

  // Trace hints are simulated in the background after the first load; poll the task summary
  // (every 3 s, for at most 60 s) until the server reports no pending hints.
  const hintsArePending = Boolean(data && data.status === 'COMPLETED' && hintsPending(data));
  React.useEffect(() => {
    if (!hintsArePending) return undefined;
    let cancelled = false;
    let timer = null;
    let attempts = 0;
    const step = () => {
      timer = setTimeout(async () => {
        attempts += 1;
        let more = true;
        try {
          const response = await fetch(`${URL}/task/${task_id}`);
          if (response.ok) {
            const next = await response.json();
            if (cancelled) return;
            if (next && next.status === 'COMPLETED') setData(next);
            more = hintsPending(next);
          }
        } catch (_) { /* keep the current data; try again until the time is up */ }
        if (!cancelled && more && attempts < HINT_POLL_MAX) step();
      }, HINT_POLL_MS);
    };
    step();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [task_id, hintsArePending]);

  return (
    <Layout hasSider={false} title="Result">
      <Container>
        <Title>
        <h2>Task {task_id}</h2>
        <Space>
          {data && data.status !== 'FIZZLED' ? <Button href={`/?clone_task=${task_id}`}>Copy conditions &amp; rerun</Button> : null}
          {data && data.status === 'COMPLETED' ? (
            <DownloadButton taskId={task_id} icon={<DownloadOutlined />}>Download all</DownloadButton>
          ) : null}
          {data && !['READY','RUNNING','CANCELLING'].includes(data.status) ? (
            <Button danger icon={<DeleteOutlined />} onClick={deleteTask}>Delete</Button>
          ) : null}
        </Space>
        </Title>
        {data && data.status !== 'COMPLETED' ? <TaskProgress taskId={task_id} onStatusChange={handleStatusChange} /> : null}
        {
          data === null ? <h3>{error}</h3> : <></>
        }
        {
          data === undefined ? <SpinContainer><Spin size='large'></Spin></SpinContainer> : <></> 
        }
        {
          data && data.status === "COMPLETED" ? <TaskDetail data={data} task_id={task_id} /> : <></> 
        }
        {
          data && data.status !== "COMPLETED" ? <TaskDetailedUnfinished data={data} taskId={task_id} /> : <></>
        }
      </Container>
    </Layout>
  )
}

export default TaskPage;
