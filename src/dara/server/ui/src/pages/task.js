import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Badge, Button, Col, Descriptions, Divider, Grid, List, message, Modal, Row, Select, Space, Spin, Switch, Table, Tag, Tooltip, Typography } from 'antd';
import { DeleteOutlined, DownloadOutlined, InfoCircleOutlined, LineChartOutlined, SearchOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable'
import parse from 'html-react-parser';
import {STATE_COLOR} from './results';
import { buildRefinementView, isTraceSelected, legendKey } from '../components/refinementPlotState.mjs';
import { viewChangeFromRelayout } from '../components/plotNavigation.mjs';
import { NAVIGATION_CONFIG, NAVIGATION_DRAGMODE } from '../components/usePlotView';
import { formatParameterWithEsd, latticeParameterRows } from '../components/latticeParameters.mjs';
import { crystalliteSizeCell } from '../components/crystalliteSize.mjs';
import { PhaseSelectionButton, RetryActions, ReviewPanel, useTaskReview } from '../components/ResultWorkflows';
import { TaskProgress } from '../components/PracticalControls';
import FitCautions from '../components/FitCautionsPanel';
import { cautionSortValue, cautionTagColor, cautionTooltip, countCautions } from '../components/fitCautions.mjs';
import { levelRank, missingCell, unexplainedSummary, unexplainedTooltipLines } from '../components/confidence.mjs';
import { ConfidenceBanner, DisplacementTag, DownloadMenu, OverallTag, ScreeningTags, VerdictTag } from '../components/ConfidenceViews';
import VersionsPanel, { useVersions } from '../components/VersionsPanel';
import QuickRefineProgress, { API, NOT_AVAILABLE, postJson, requestJson, useQuickRefine } from '../components/QuickRefineProgress';
import PeakCandidatesDrawer, { DRAWER_WIDTH } from '../components/PeakCandidatesDrawer';
import FitCompare from '../components/FitCompare';
import BackgroundEditor from '../components/BackgroundEditor';
import useKeyboardShortcuts, { FIT_MODAL_CLASS, HELP_MODAL_CLASS } from '../components/useKeyboardShortcuts';
import { MOUSE_HELP, SHORTCUT_HELP } from '../components/keyboardShortcuts.mjs';
import {
  collectHints, hintsPending, observedPeak, peakFromClick, peakShapes, phaseGroupNames, previewTrace, significantPeaks, updatePeakSelection,
} from '../components/peakSelection.mjs';
import { searchOptionsText } from '../components/resultsList.mjs';
import {
  BASELINE_LABEL, QUICK_CHECK_HINT, REFINE_ALL_FIRST, alternativeOfVerdict, availableComparisonBase, baselineRequest,
  deltaStrip, deltaText, displayedCandidateOf,
  formulaOfPhase, isQuickCheck, needsBaseline, parseSelection, plotQuery, refineAllBase, refineAllRequest, removeRequest,
  referenceVariants, replacementOf, runText, selectOptionGroups, selectionLabel, swapKind, swapRequest, versionGone, versionReadyText,
} from '../components/versions.mjs';

const { Text } = Typography;
const HINT_POLL_MS = 3000;
const HINT_POLL_MAX = 20; // 60 s

const URL = API;

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
  // Width of the plot: below COMPACT_LEGEND_WIDTH the legend column is made narrower.
  const [plotWidth, setPlotWidth] = React.useState(null);
  const containerRef = React.useRef(null);
  React.useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const measure = () => setPlotWidth(Math.round(element.getBoundingClientRect().width) || null);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [plot]);
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
    selection, showDiagnostics, showPhaseProfiles, showReflections, scale, yRange, xRange, fitY, plotWidth,
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
      <Text>Scale</Text>
      <Select value={scale} onChange={value => { setScale(value); setYRange(null); }} style={{ width: 90 }}
        options={[{ value: 'linear', label: 'Linear' }, { value: 'log', label: 'Log' }]} />
      <Tooltip title="Phase profiles"><Text>Profiles</Text></Tooltip><Switch checked={showPhaseProfiles} onChange={setShowPhaseProfiles} />
      <Tooltip title="Bragg positions"><Text>Bragg</Text></Tooltip><Switch checked={showReflections} onChange={value => { setShowReflections(value); setYRange(null); }} />
      <Tooltip title="Unexplained-peak and diagnostic markers"><Text>Markers</Text></Tooltip><Switch checked={showDiagnostics} onChange={setShowDiagnostics} />
      <Tooltip title="Fit the intensity axis to the visible 2θ range (F)"><Text>Auto-Y</Text></Tooltip><Switch checked={fitY} onChange={value => { setFitY(value); setYRange(null); }} />
      <Button onClick={resetView}>Reset zoom</Button>
      <Tooltip title={`Left-drag to zoom into a box (2θ only while Auto-Y is on), Shift- or right-drag to pan 2θ, mouse wheel to zoom 2θ at the cursor, double-click or Reset zoom to show the whole pattern. Zoom and pan stop at the measured range. Click a phase in the legend to show its profile and Bragg positions; hover for 2θ, d-spacing and hkl. The lower panel shows observed − calculated.${onPeakClick ? ' Click a missing-peak marker or an observed point to search references for that peak (Shift+click to select several).' : ''}`}>
        <InfoCircleOutlined aria-label="Plot navigation help" style={{ color: '#888' }} />
      </Tooltip>
      <Text type="secondary">Press ? for shortcuts</Text>
    </Space>
    <div ref={containerRef} style={{ width: '100%' }}>
    <Plotly key={`${task_id}-${plot_id}`} data={data} xBounds={view.fullXRange}
      layout={{ ...view.layout, autosize: true,
        shapes: [...(view.layout.shapes || []), ...excludedRanges.flatMap(([x0, x1]) => [
          { type: 'rect', xref: 'x', yref: 'y domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
          { type: 'rect', xref: 'x2', yref: 'y2 domain', x0, x1, y0: 0, y1: 1, fillcolor: 'rgba(130,130,130,0.15)', line: { width: 0 }, layer: 'below' },
        ]), ...peakShapes(peaks)],
        dragmode: NAVIGATION_DRAGMODE,
        uirevision: `${task_id}-${plot_id}-${resetKey}` }}
      onLegendClick={toggleLegend}
      onLegendDoubleClick={isolateLegend}
      onDoubleClick={resetView}
      onClick={handleClick}
      onRelayout={event => {
        const change = viewChangeFromRelayout(event, { full: view.fullXRange });
        if ('xRange' in change) setXRange(change.xRange);
        // A y box (Auto-Y off) becomes the data window; the bands are drawn below it.
        if ('yRange' in change) setYRange(change.yRange);
      }}
      // Ranges are React state: Plotly's own reset/autoscale buttons would only
      // restore the state they were last given, so Reset zoom replaces them.
      config={{ responsive: true, displaylogo: false, ...NAVIGATION_CONFIG,
        toImageButtonOptions: { format: 'svg', filename: `dara_${task_id}_${String(plot_id ?? 'best').replace(':', '_')}`, width: 1600, height: 900 } }}
      useResizeHandler style={{ height: 'clamp(520px, calc(100vh - 330px), 1200px)', width: '100%' }} />
    </div>
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
                  <Tooltip title="Name format: formula_space group_(database ID)-energy above hull. 'None' means that energy metadata is unavailable.">
                    <code className="highlight" style={{ overflowWrap: 'anywhere' }}>{selectedPhase}</code>
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
                  ) : <Text type="secondary">{`+ ${alternatives.length} alternative matching CIF reference${alternatives.length === 1 ? "" : "s"} hidden`}</Text>
                ) : null}
              </div>
            }
          />
        </List.Item>);
      }}
    />
  );
}

// The same phases refined with other database records, merged into this candidate.
function MergedReferenceList({ items, show, taskId, onView }) {
  if (!items.length) return null;
  if (!show) {
    const rwps = items.map(item => Number(item.rwp)).filter(Number.isFinite);
    const range = rwps.length ? ` (Rwp ${rwps.length > 1 ? `${Math.min(...rwps)}–${Math.max(...rwps)}` : rwps[0]} %)` : '';
    return <Text type="secondary">{`+ ${items.length} other reference entr${items.length === 1 ? 'y' : 'ies'}${range}`}</Text>;
  }
  return <Space direction="vertical" size={2}>
    {items.map(item => <Space key={item.source_index} size="small" wrap>
      <Text type="secondary" style={{ overflowWrap: 'anywhere' }}>{`${item.differing_phases.join(' + ')} — Rwp ${item.rwp} %`}</Text>
      <Button size="small" onClick={() => onView(item.source_index)}>View fit</Button>
      <Tooltip title="Download (ZIP)"><DownloadButton taskId={taskId} resultIndex={item.source_index} size="small" icon={<DownloadOutlined />} aria-label={`Download stored result ${item.source_index + 1}`} /></Tooltip>
    </Space>)}
  </Space>;
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
    ? '—' : Number(value).toFixed(digits);
}

/** Rwp, GOF and DW; the other statistics are in the Rwp tooltip. */
function FitQualitySummary({ quality }) {
  if (!quality) return <Text type="secondary">Fit statistics unavailable</Text>;
  const details = [
    `Rp ${formatMetric(quality.rp)}%`, `Rexp ${formatMetric(quality.rexp)}%`, `χ² ${formatMetric(quality.chi_squared)}`,
    `2θ ${formatMetric(quality.two_theta_min)}–${formatMetric(quality.two_theta_max)}°`,
    `${quality.data_points ?? '—'} points, ${quality.iterations ?? '—'} iterations`,
  ];
  return <Space size="small" wrap>
    <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{details.join('\n')}</div>}><Tag color="green">Rwp {formatMetric(quality.rwp)}%</Tag></Tooltip>
    <Tooltip title="Goodness of fit = Rwp / Rexp"><Tag color={quality.gof > 2 ? 'orange' : 'blue'}>GOF {formatMetric(quality.gof)}</Tag></Tooltip>
    <Tooltip title="Durbin–Watson statistic for residual correlation"><Tag>DW {formatMetric(quality.durbin_watson)}</Tag></Tooltip>
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
  return <Table size="small" pagination={false} rowKey="phase" scroll={{ x: 'max-content' }} tableLayout="auto" dataSource={dataSource}
    columns={[
      { title: 'Phase', dataIndex: 'phase', width: 260,
        render: phase => <PhaseIdentity reference={phase} metadata={metadata?.[phase]} /> },
      ...verdictColumn,
      { title: <Tooltip title="Normalized over modeled crystalline phases; amorphous and unidentified material are excluded. ± is the BGMN estimated standard deviation, propagated after normalization."><span>Quantity</span></Tooltip>,
        render: (_, row) => row.weight_percent == null ? '—' : <span>{Number(row.weight_percent).toFixed(2)} wt%{row.weight_esd_percent == null ? '' : ` ± ${Number(row.weight_esd_percent).toFixed(2)}`}</span> },
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
          </Space> : '—';
        } },
      { title: 'Density', render: (_, row) => row.xray_density == null ? '—' : `${Number(row.xray_density).toFixed(3)} g/cm³` },
      { title: 'Rphase', render: (_, row) => row.rphase == null ? '—' : `${Number(row.rphase).toFixed(2)}%` },
      { title: 'Crystallite size', render: (_, row) => {
        const cell = crystalliteSizeCell(row);
        return cell.tooltip ? <Tooltip title={cell.tooltip}><span>{cell.text}</span></Tooltip> : cell.text;
      } },
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
      <Text type="secondary">vs base:</Text>
      {shown.map(row => <Tag key={row.key} color={row.delta === null || row.key.startsWith('wt:') ? 'default' : row.better ? 'green' : row.delta > 0 ? 'orange' : 'default'}>
        {`${row.label} ${deltaText(row)}`}
      </Tag>)}
    </Space>
  </div>;
}

function ShortcutHelp({ open, onClose }) {
  return <Modal title="Keyboard shortcuts" open={open} onCancel={onClose} footer={null} keyboard={false}
    wrapClassName={HELP_MODAL_CLASS} width={420}>
    <Table size="small" pagination={false} rowKey="keys" dataSource={SHORTCUT_HELP}
      columns={[{ title: 'Keys', dataIndex: 'keys', width: 110, render: keys => <code>{keys}</code> }, { title: 'Action', dataIndex: 'action' }]} />
    <Table size="small" pagination={false} rowKey="keys" dataSource={MOUSE_HELP} style={{ marginTop: 12 }}
      columns={[{ title: 'Mouse (plot)', dataIndex: 'keys', width: 110, render: keys => <code>{keys}</code> }, { title: 'Action', dataIndex: 'action' }]} />
  </Modal>;
}

const ANALYSIS_MODE = {
  known_mixture: 'Known-phase refinement',
  elemental: 'Phase identification',
  reaction: 'Reaction check',
  selected_phases: 'User-selected phase refinement',
};

const DESCRIPTION_COLUMNS = { xs: 1, sm: 1, md: 1, lg: 2, xl: 2, xxl: 3 };
const BREAKPOINTS = ['xxl', 'xl', 'lg', 'md', 'sm', 'xs'];

/** Column count for the current screen (antd's rule); a full-row span must match it. */
function useDescriptionColumns() {
  const screens = Grid.useBreakpoint();
  const hit = BREAKPOINTS.find(key => screens[key]);
  return hit ? DESCRIPTION_COLUMNS[hit] : DESCRIPTION_COLUMNS.xxl;
}

function InstrumentText({ details }) {
  if (!details) return null;
  const extra = [details.geometry, details.radiation, details.notes].filter(Boolean).join(' · ');
  return <Tooltip title={extra || null}>
    <span>{details.label} <Tag color={details.verified ? 'green' : 'orange'}>{details.verified ? 'operator-verified' : 'unverified'}</Tag></span>
  </Tooltip>;
}

function TaskDetail({ data, task_id }) {
  const [plot_id, setPlotIdRaw] = React.useState(undefined);
  const setPlotId = React.useCallback(value => setPlotIdRaw(normalizePlotId(value)), []);
  const [showAlternatives, setShowAlternatives] = React.useState(false);
  const store = useVersions(task_id);
  const columns = useDescriptionColumns();
  const savedReview = useTaskReview(task_id);
  // Quick checks are approximate fits: every adoption path (review select, versions table,
  // compare window) goes through this one guard.
  const review = {
    ...savedReview,
    save: async (choice = savedReview.selection) => {
      if (isQuickCheck(store.versions, choice)) {
        message.warning(`${QUICK_CHECK_HINT} ${REFINE_ALL_FIRST}.`);
        return false;
      }
      return savedReview.save(choice);
    },
  };
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
    const text = versionReadyText(n, job, item?.rwp);
    const sel = n === 1 ? null : `version:${n}`;
    if (meta.afterDone === 'compare') {
      // A duplicate request compares the existing version it matched.
      if (job?.reused) message.info(text);
      else message.success(text);
      setCompare({ a: availableComparisonBase(meta.base ?? 'best', list, n), b: sel ?? 'best' });
    } else if (job?.reused) {
      // A duplicate request: nothing new was refined, the existing version is opened instead.
      message.info(text);
      setPlotId(sel);
    } else if (plotIdRef.current !== undefined) {
      message.success(text);
      setPlotId(sel);
    } else {
      message.success(<span>{`${text} `}<a onClick={() => setPlotId(sel)}>View</a></span>);
    }
  }, [refreshVersions, setPlotId]);
  const quick = useQuickRefine(task_id, { onVersion: handleVersion });
  const quickShown = quick.available !== false && store.available !== false;

  // A version can disappear (deleted, or a quick check replaced by its full fit): move the fit
  // view and the compare window to its replacement instead of showing a missing version.
  React.useEffect(() => {
    if (store.loading) return;
    if (versionGone(store.versions, plot_id)) {
      setPlotId(replacementOf(store.versions, parseSelection(plot_id).version));
    }
    setCompare(previous => {
      if (!previous) return previous;
      const fix = sel => (versionGone(store.versions, sel) ? replacementOf(store.versions, parseSelection(sel).version) : sel);
      const next = { a: fix(previous.a), b: fix(previous.b) };
      return next.a === previous.a && next.b === previous.b ? previous : next;
    });
  }, [store.versions, store.loading, plot_id, setPlotId]);

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
  // Deltas against 'best' use the baseline version (same code as the new version) when it exists;
  // a base that is gone (replaced quick check, deleted version) falls back to the best fit.
  const effectiveBase = versionData ? availableComparisonBase(versionData.origin?.base ?? 'best', store.versions, versionNumber) : null;
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
        .catch(error => {
          // Only the displayed version is worth an error; a missing base just leaves out the deltas.
          if (active && n === versionNumber) setVersionError(error.missing ? NOT_AVAILABLE : error.message);
        });
    });
    return () => { active = false; };
  }, [task_id, versionNumber, baseVersionNumber, versionDetails]);
  // "Find phases for the unexplained peaks" opens the fit and the drawer together.
  const openDrawerWithFit = React.useRef(false);
  React.useEffect(() => {
    // A different fit has different peaks: drop the selection, any tick preview and an old error.
    setPeaks([]);
    setPreview(null);
    setVersionError('');
    setDrawerOpen(openDrawerWithFit.current && plot_id !== undefined);
    openDrawerWithFit.current = false;
  }, [plot_id]);

  // A merged result (other database record) shows its own Rwp, weights and fit statistics.
  const candidateSummary = index => {
    const found = displayedCandidateOf(data.all_results, index);
    return found ? (found.member || found.result) : undefined;
  };
  const displayedResult = plot_id === undefined ? undefined
    : plot_id === null ? data.final_result
      : versionNumber ? versionData?.final_result
        : candidateSummary(plot_id);
  const findSummary = sel => {
    const parsed = parseSelection(sel);
    if (parsed === 'best' || parsed === null) return data.final_result;
    if (typeof parsed === 'number') return candidateSummary(parsed);
    return versionDetails[parsed.version]?.final_result;
  };
  const baseSummary = versionData ? findSummary(effectiveBase) : null;
  const deltaRows = versionData?.final_result && baseSummary ? deltaStrip(baseSummary, versionData.final_result) : [];
  const displayedPhases = displayedResult ? (displayedResult.highlighted_phases || displayedResult.phases || []) : [];
  // Explicit background polynomial degree of the displayed fit (null: BGMN chooses it).
  const baseDegree = versionNumber ? (versionData?.background?.degree ?? null)
    : (data.final_refinement_params?.background_degree ?? null);

  // Set only while the baseline check below is in flight, so a failed baseline is retried later.
  const baselineChecking = React.useRef(false);
  const runQuick = async (body, meta = {}) => {
    if (quick.busy || baselineChecking.current) {
      message.info('A re-refinement is already running for this task.');
      return;
    }
    const request = { base: currentSel, ...body };
    if (store.baselineStale && parseSelection(request.base) === 'best') {
      // The stored result predates the current refinement code: queue a baseline with today's code
      // first (silently; the server runs jobs in order) so comparisons use the same code. The
      // current versions and queue decide, so a baseline that failed is queued again.
      baselineChecking.current = true;
      try {
        const [list, jobs] = await Promise.all([
          refreshVersions({ silent: true }),
          requestJson(`/task/${task_id}/quick-jobs`).catch(() => null),
        ]);
        const queue = Array.isArray(jobs) ? jobs : (jobs?.jobs || jobs?.active || []);
        const baselineQueued = queue.some(job => ['queued', 'running'].includes(job.status) && job.label === BASELINE_LABEL);
        if (!baselineQueued && needsBaseline(true, list.length ? list : store.versions, request)) {
          await postJson(`/task/${task_id}/quick-refine`, baselineRequest());
        }
      } catch (_) {
        // Not queued; the user's own job still runs and the next one tries again.
      } finally {
        baselineChecking.current = false;
      }
    }
    quick.start(request, { base: currentSel, afterDone: 'view', ...meta });
  };
  // Fast Add-phase versions keep the base phases fixed; this makes the full fit (which replaces the
  // quick check) and compares it with the quick check's own base.
  const refineAll = n => runQuick(refineAllRequest(n), { afterDone: 'compare', base: refineAllBase(store.versions, n) });
  const quickCheck = versionData?.refine_mode === 'fast';
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
      {swap ? <Tooltip title="Swap structure model, refine, compare">
        <Button size="small" disabled={quick.busy} onClick={() => runQuick(swap, { afterDone: 'compare' })}>Swap</Button>
      </Tooltip> : null}
      {otherSet ? <Tooltip title={`Show candidate ${otherSet.candidate_number ?? otherSet.source_index + 1}`}>
        <Button size="small" onClick={() => setPlotId(otherSet.source_index)}>{`View candidate ${otherSet.candidate_number ?? otherSet.source_index + 1}`}</Button>
      </Tooltip> : null}
      {displayedPhases.length >= 2 ? <Tooltip title="Remove, refine, compare">
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
  const [suggestionCount, setSuggestionCount] = React.useState(0);
  const clearPeaks = () => { setPeaks([]); setPreview(null); setDrawerOpen(false); };
  const addCandidate = candidate => {
    setDrawerOpen(false);
    setPreview(null);
    runQuick({
      phases: [{ ref: candidate.ref, source: candidate.source }], origin: 'add_peak_search',
      label: `Added ${candidate.formula || formulaOfPhase(candidate.ref)} (peak search)`,
    });
  };
  // A quick check is changed only through "Refine all phases"; the drawer stays closed on it.
  const drawerShown = drawerOpen && plot_id !== undefined && !quickCheck;

  const options = [
    'best',
    ...data.all_results.flatMap((result, index) => [result.source_index ?? index, ...referenceVariants(result).map(m => m.source_index)]),
    ...store.versions.filter(item => item.version > 1).map(item => `version:${item.version}`),
  ];
  const keyContext = React.useRef({});
  keyContext.current = { options, current: currentSel, phaseGroups, peakSelectionCount: peaks.length, helpOpen, drawerOpen: drawerShown };
  useKeyboardShortcuts(plot_id !== undefined && !compare, keyContext, {
    select: action => setPlotId(action.value === 'best' ? null : action.value),
    'toggle-phase': action => plotRef.current?.togglePhase(action.name),
    reset: () => plotRef.current?.reset(),
    'fit-y': () => plotRef.current?.toggleFitY(),
    help: () => setHelpOpen(true),
    'close-help': () => setHelpOpen(false),
    'close-drawer': () => setDrawerOpen(false),
    'clear-peaks': clearPeaks,
    close: () => setPlotId(undefined),
  });
  const modalTitle = plot_id === undefined ? 'Fit'
    : plot_id === null ? 'Best fit'
      : versionNumber ? selectionLabel(plot_id, { versions: store.versions })
        : selectionLabel(plot_id, { all_results: data.all_results }).split(' — ')[0];
  const selectOptions = selectOptionGroups(data.all_results, store.versions);
  const [backgroundOpen, setBackgroundOpen] = React.useState(false);
  const firstOtherCandidate = data.all_results.length
    ? (data.all_results[1] ?? data.all_results[0]).source_index ?? (data.all_results.length > 1 ? 1 : 0) : null;
  const reportBlocked = review.loading || review.error ? 'unavailable' : review.dirty ? 'save the review first' : null;
  const downloadZip = () => downloadResultArchive(task_id)
    .then(() => message.success('Result ZIP downloaded.'))
    .catch(error => message.error(error.message || 'Could not download the result.'));
  const searchSummary = data.search_summary || {};
  const excludedRanges = data.additional_search_options?.refinement_params?.excluded_ranges || [];
  const hasBanner = Boolean(data.final_result?.confidence?.overall?.level);
  const addPhaseTip = quickCheck ? REFINE_ALL_FIRST
    : suggestionCount ? `${suggestionCount} possible or strong match${suggestionCount === 1 ? '' : 'es'} for the significant unexplained peaks` : null;

  return (
    <>
      <TitleContriner>
        <Space wrap align="center">
          <h3 style={{ margin: 0 }}>Task details</h3>
          <Button type="primary" icon={<LineChartOutlined />} onMouseEnter={() => preloadPlot(task_id, null)}
            onFocus={() => preloadPlot(task_id, null)} onClick={() => setPlotId(null)}>View fit</Button>
        </Space>
        <Tooltip title="Downloads use the adopted fit (or the best fit)" placement="left">
          <span><DownloadMenu taskId={task_id} reportBlocked={reportBlocked} extraItems={[
            { key: 'csv', label: <a href={`${URL}/task/${task_id}/table.csv`}>Table (CSV)</a> },
            { key: 'zip', label: 'All results (ZIP)', onClick: downloadZip },
          ]} /></span>
        </Tooltip>
      </TitleContriner>
      <Divider style={{ margin: '12px 0' }} />
      {/* Confidence banner and review side by side on wide screens, stacked below 1200 px. */}
      <Row gutter={[16, 12]} className="dara-summary-row" style={{ marginBottom: 12 }}>
        {hasBanner ? <Col xs={24} xl={12}>
          <ConfidenceBanner confidence={data.final_result?.confidence} omitPeakReasons style={{ marginBottom: 0 }}
            action={significantPeaks(data.final_result?.peak_diagnostics).length
              ? <Button size="small" icon={<SearchOutlined />} onClick={() => { openDrawerWithFit.current = true; setPlotId(null); }}>
                Find phases for the unexplained peaks
              </Button> : null} />
        </Col> : null}
        <Col xs={24} xl={hasBanner ? 12 : 24}>
          <ReviewPanel data={data} review={review} versions={store.versions} style={{ marginBottom: 0 }} />
        </Col>
      </Row>
      <Descriptions bordered column={columns} size='small'>
        <Descriptions.Item label="Pattern file">{data.task_label}</Descriptions.Item>
        <Descriptions.Item label="Analysis mode">{ANALYSIS_MODE[data.analysis_mode] || data.analysis_mode || (data.use_rxn_predictor ? "Reaction check" : "Phase identification")}</Descriptions.Item>
        <Descriptions.Item label="Run">
          {runText(data.start_time, data.runtime)}
          {searchSummary.stopped_early ? <Tag color="red" style={{ marginLeft: 8 }}>{`Stopped at time limit (${STOP_STAGE_LABEL[searchSummary.stopped_at_stage] || 'search'})`}</Tag> : null}
        </Descriptions.Item>
        <Descriptions.Item label="Fit quality">
          <Space size="small" wrap>
            <FitQualitySummary quality={data.final_result.fit_quality} />
            <DisplacementTag confidence={data.final_result.confidence} />
          </Space>
        </Descriptions.Item>
        <Descriptions.Item label="Peak diagnostics">
          <PeakDiagnosticTags diagnostics={data.final_result.peak_diagnostics} />
        </Descriptions.Item>
      </Descriptions>
      <FitCautions cautions={data.final_result.cautions} besideBanner={hasBanner} />
      <details style={{ marginTop: 8 }}>
        <summary>Analysis settings</summary>
        <Descriptions bordered column={columns} size='small' style={{ marginTop: 8 }}>
          <Descriptions.Item label="Temperature">{data.temperature || "None"}</Descriptions.Item>
          <Descriptions.Item label="Reaction network">{data.use_rxn_predictor ? "Yes" : "No"}</Descriptions.Item>
          <Descriptions.Item label="Included elements">{(data.elements || []).join(', ') || "None"}</Descriptions.Item>
          <Descriptions.Item label="Reference databases">{(data.candidate_sources || []).map(source => source.toUpperCase()).join(' + ') || "Uploaded CIFs"}</Descriptions.Item>
          <Descriptions.Item label="Candidate CIFs">{data.candidate_summary?.after ?? "Unknown"} retained ({data.candidate_summary?.removed_duplicates ?? 0} duplicates removed)
            <ScreeningTags candidateSummary={data.candidate_summary} />
          </Descriptions.Item>
          <Descriptions.Item label="Precursors">{(data.precursors || []).join(', ') || "None"}</Descriptions.Item>
          <Descriptions.Item label="Search options">{searchOptionsText(data.additional_search_options)}</Descriptions.Item>
          {excludedRanges.length ? <Descriptions.Item label="Excluded 2θ">
            <Tooltip title="Shaded in the fit plot; omitted from fitting">
              <span>{excludedRanges.map(([lo, hi]) => `${lo}–${hi}°`).join(', ')}</span>
            </Tooltip>
          </Descriptions.Item> : null}
          {data.instrument_profile_details ? <Descriptions.Item label="Instrument">
            <InstrumentText details={data.instrument_profile_details} />
          </Descriptions.Item> : null}
          {searchSummary.parent_task_id ? <Descriptions.Item label="Derived from">
            <a href={`/task/?id=${searchSummary.parent_task_id}`}>Task {searchSummary.parent_task_id}</a>
          </Descriptions.Item> : null}
          <Descriptions.Item label="Hypothesis search" span={columns}><SearchSummary summary={searchSummary} /></Descriptions.Item>
        </Descriptions>
      </details>
      <h3 style={{ marginTop: 16 }}>Phases (best fit)</h3>
      <PhaseRefinementTable phases={data.final_result.phases} metadata={data.final_result.phase_metadata} details={data.final_result.phase_details} confidence={data.final_result.confidence} />
      <VersionsPanel taskId={task_id} store={store} review={review}
        onView={setPlotId} onCompare={(a, b) => setCompare({ a, b })} onRefineAll={quickShown ? refineAll : null} refineBusy={quick.busy}
        extra={quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null} />
      <ResultTitleContainer>
        <Space wrap align="baseline">
          <h3 style={{ marginTop: 8 }}>{`Candidates (${data.all_results.length})`}</h3>
          {data.duplicate_results_removed > 0 ? <Tooltip title="Results with the same phases (same formula and space group) reached in another order or with other database records. Only the best-Rwp copy is listed; polymorphs stay separate.">
            <Text type="secondary">{`${data.duplicate_results_removed} equivalent result${data.duplicate_results_removed === 1 ? '' : 's'} merged`}</Text>
          </Tooltip> : null}
        </Space>
        <Button disabled={firstOtherCandidate === null}
          onClick={() => setCompare({ a: 'best', b: firstOtherCandidate })}>Compare fits</Button>
      </ResultTitleContainer>
      <Table size="small" pagination={false} scroll={{ x: 'max-content' }} tableLayout="auto" style={{ marginTop: 4, marginBottom: 12 }}
        rowKey="source_index"
        dataSource={data.all_results.map((item, index) => ({ ...item, candidate_number: index + 1, source_index: item.source_index ?? index }))}
        columns={[
          { title: 'Candidate', dataIndex: 'candidate_number', render: n => `Candidate ${n}` },
          { title: 'Phases / wt%', render: (_, row) => <Space direction="vertical">{row.highlighted_phases.map(phase => <span key={phase}><PhaseIdentity reference={phase} compact /> {row.weight_percent?.[phase] != null ? `${Number(row.weight_percent[phase]).toFixed(2)} wt%` : ''}</span>)}</Space> },
          { title: 'Rwp (%)', dataIndex: 'rwp', sorter: (a,b) => a.rwp-b.rwp, defaultSortOrder: 'ascend', render: (value,row) => <>{value} <Text type="secondary">{row.is_final_refined ? 'Final' : 'Screening'}</Text></> },
          { title: 'GOF', sorter: (a,b) => (a.fit_quality?.gof ?? Infinity)-(b.fit_quality?.gof ?? Infinity), render: (_,row) => formatMetric(row.fit_quality?.gof) },
          { title: 'Missing', sorter: (a,b) => missingCell(a.peak_diagnostics).sortValue-missingCell(b.peak_diagnostics).sortValue, render: (_,row) => missingCell(row.peak_diagnostics).text },
          ...[['extra','Extra'],['intensity_mismatch','Intensity mismatch']].map(([key,title]) => ({ title, sorter: (a,b) => (a.peak_diagnostics?.counts?.[key] ?? Infinity)-(b.peak_diagnostics?.counts?.[key] ?? Infinity), render: (_,row) => row.peak_diagnostics?.counts?.[key] ?? '—' })),
          { title: <Tooltip title="Automated review flags; hover a count to read them.">Cautions</Tooltip>,
            sorter: (a,b) => cautionSortValue(a.cautions)-cautionSortValue(b.cautions),
            render: (_,row) => {
              const counts = countCautions(row.cautions);
              if (!counts) return '—';
              return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{counts.total ? cautionTooltip(row.cautions) : 'No automated cautions.'}</div>}>
                <Tag color={cautionTagColor(row.cautions)}>{counts.total}</Tag>
              </Tooltip>;
            } },
          { title: <Tooltip title="Hover for the reasons.">Confidence</Tooltip>,
            sorter: (a,b) => levelRank(a.confidence?.overall?.level)-levelRank(b.confidence?.overall?.level),
            render: (_,row) => <OverallTag confidence={row.confidence} /> },
          { title: 'No. of phases', sorter: (a,b) => a.highlighted_phases.length-b.highlighted_phases.length, render: (_,row) => row.highlighted_phases.length },
          { title: 'Actions', render: (_,row) => <Space><Button type="primary" size="small" icon={<LineChartOutlined />} onMouseEnter={() => preloadPlot(task_id, row.source_index)} onFocus={() => preloadPlot(task_id, row.source_index)} onClick={() => setPlotId(row.source_index)}>View fit</Button><Tooltip title="Download (ZIP)"><DownloadButton taskId={task_id} resultIndex={row.source_index} size="small" icon={<DownloadOutlined />} aria-label={`Download candidate ${row.candidate_number}`} /></Tooltip></Space> },
        ]} />
      <div style={{ marginBottom: 8 }}>
        Show alternative CIFs
        <Tooltip title="Other database records of the same phases: matching references grouped per phase slot, and merged results refined with other records. Name format: formula_space group_(database ID)-energy above hull ('None': no energy metadata).">
          <InfoCircleOutlined style={{ marginLeft: 4 }} />
        </Tooltip>
        <Switch checked={showAlternatives} onChange={setShowAlternatives} size="small" style={{ marginLeft: 8 }} />
      </div>
      {/* One candidate per full-width row; the label column keeps a fixed share at every width. */}
      <Descriptions bordered column={2} size='small'>
        {
          data.all_results.map((result, index) => {
            const resultIndex = result.source_index ?? index;
            return (
              <Descriptions.Item key={index} span={2} labelStyle={{ width: "25%" }} label={<>
                <Space size="small" wrap>
                  <span>{`Candidate ${index + 1}`}</span>
                  <Tag color={index === 0 ? "green" : "blue"}>
                    {`${result.is_final_refined ? "Final " : ""}Rwp ${result.rwp} %`}
                  </Tag>
                  {result.is_final_refined && Number(result.screening_rwp) !== Number(result.rwp) ? (
                    <Tooltip title="Fast restricted refinement used to rank candidate phase sets before the final refinement.">
                      <Tag>{`Screening Rwp ${result.screening_rwp} %`}</Tag>
                    </Tooltip>
                  ) : null}
                  {index === 0 && result.is_final_refined ? <Tag color="gold">Best</Tag> : null}
                  <PeakDiagnosticTags diagnostics={result.peak_diagnostics} />
                </Space>
                <Space size="small" style={{ marginTop: 6, display: 'flex' }}>
                  <Button type="primary" size="small" icon={<LineChartOutlined />}
                    onMouseEnter={() => preloadPlot(task_id, resultIndex)} onFocus={() => preloadPlot(task_id, resultIndex)}
                    onClick={() => setPlotId(resultIndex)}>View fit</Button>
                  <Tooltip title="Download (ZIP)">
                    <DownloadButton taskId={task_id} resultIndex={resultIndex} type="text" size="small" shape="circle"
                      aria-label={`Download candidate ${index + 1}`} icon={<DownloadOutlined />} />
                  </Tooltip>
                </Space>
              </>}>
                <PhaseList phases={result.phases} highlighted_phases={result.highlighted_phases} grouped_phases={result.grouped_phases} show_alternatives={showAlternatives} weight_percent={result.weight_percent} phase_metadata={result.phase_metadata} />
                <MergedReferenceList items={referenceVariants(result)} show={showAlternatives} taskId={task_id} onView={setPlotId} />
              </Descriptions.Item>
            );
          })
        }
      </Descriptions>
      {/* The Add phase drawer sits beside the fit instead of covering the plot. */}
      <Modal title={modalTitle} open={plot_id !== undefined} onCancel={() => setPlotId(undefined)} footer={null}
        width={drawerShown ? `calc(100vw - ${DRAWER_WIDTH} - 48px)` : 'calc(100vw - 32px)'}
        style={{ top: 16, ...(drawerShown ? { marginRight: `calc(${DRAWER_WIDTH} + 16px)`, marginLeft: 'auto' } : {}) }}
        bodyStyle={{ padding: '12px 16px' }}
        keyboard={false} wrapClassName={FIT_MODAL_CLASS}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Select aria-label="Displayed fit" style={{ minWidth: 360, maxWidth: '40vw' }} value={currentSel}
            onChange={value => setPlotId(value === 'best' ? null : value)} options={selectOptions} />
          {quickShown && displayedResult ? <>
            <Tooltip title={addPhaseTip}>
              <Badge count={quickCheck ? 0 : suggestionCount} size="small" offset={[-4, 2]}>
                <span><Button type={!quickCheck && (suggestionCount || collectHints(displayedResult?.peak_diagnostics).length) ? 'primary' : 'default'}
                  disabled={quickCheck} onClick={() => setDrawerOpen(true)}>Add phase…</Button></span>
              </Badge>
            </Tooltip>
            <PhaseSelectionButton taskId={task_id} phases={displayedPhases} renderPhase={renderReference} busy={quick.busy}
              disabledReason={quickCheck ? REFINE_ALL_FIRST : null}
              onSubmit={request => runQuick(request, { afterDone: 'compare' })} />
            <Tooltip title={quickCheck ? REFINE_ALL_FIRST : 'Background polynomial and excluded 2θ intervals'}>
              <span><Button disabled={quick.busy || quickCheck} onClick={() => setBackgroundOpen(true)}>Background…</Button></span>
            </Tooltip>
            <BackgroundEditor open={backgroundOpen} onOpenChange={setBackgroundOpen} busy={quick.busy} disabled={quick.busy} baseDegree={baseDegree}
              getView={() => plotRef.current?.getViewRange?.() || null}
              onSubmit={(background, label) => runQuick({ phases: [], background, origin: 'background', label })} />
          </> : null}
          {displayedResult ? <DownloadMenu taskId={task_id} selection={currentSel} label="Download this fit"
            blocked={quickCheck ? REFINE_ALL_FIRST : null} reportBlocked={reportBlocked} /> : null}
        </Space>
        {quickShown ? <QuickRefineProgress job={quick.job} onCancel={quick.cancel} onDismiss={quick.dismiss} /> : null}
        {versionError ? <Alert type="error" showIcon message={versionError} style={{ marginBottom: 12 }} /> : null}
        {versionNumber && !versionData && !versionError ? <Spin /> : null}
        {displayedResult ? <>
          {quickCheck ? <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={QUICK_CHECK_HINT}
            action={quickShown ? <Button size="small" type="primary" disabled={quick.busy} onClick={() => refineAll(versionNumber)}>Refine all phases</Button> : null} /> : null}
          {versionNumber ? <DeltaStrip rows={deltaRows} /> : null}
          <Space size="small" wrap style={{ marginBottom: 12 }}>
            <FitQualitySummary quality={displayedResult.fit_quality} />
            <OverallTag confidence={displayedResult.confidence} />
            <DisplacementTag confidence={displayedResult.confidence} />
          </Space>
        </> : null}
        {peaks.length ? <Space wrap style={{ marginBottom: 8 }}>
          <Text type="secondary">Selected peaks:</Text>
          {peaks.map(peak => <Tag key={peak} closable color="volcano"
            onClose={event => { event.preventDefault(); onPeakClick(peak, true); }}>{`${peak.toFixed(2)}°`}</Tag>)}
          <Button size="small" onClick={() => setDrawerOpen(true)}>Show candidates</Button>
          <Button size="small" onClick={clearPeaks}>Clear</Button>
        </Space> : null}
        <RefinementPlot ref={plotRef} task_id={task_id} plot_id={plot_id}
          excludedRanges={[...excludedRanges, ...(versionData?.background?.excluded_ranges || [])]}
          peaks={peaks} previewSticks={preview?.sticks || null} onGroups={setPhaseGroups}
          onPeakClick={quickShown && !quickCheck ? onPeakClick : undefined} />
        {displayedResult ? <>
          <Divider />
          <PhaseRefinementTable phases={displayedPhases}
            metadata={displayedResult.phase_metadata} details={displayedResult.phase_details} confidence={displayedResult.confidence}
            rowActions={quickShown && !quickCheck ? rowActions : null} />
          <FitCautions cautions={displayedResult.cautions} style={{ margin: '12px 0' }} />
        </> : null}
      </Modal>
      <PeakCandidatesDrawer taskId={task_id} sel={currentSel} peaks={peaks} open={drawerShown}
        diagnostics={displayedResult?.peak_diagnostics} onAddHint={(hint, target) => { setDrawerOpen(false); addHint(hint, target); }}
        active={plot_id !== undefined && !quickCheck} onSuggestions={setSuggestionCount}
        displayedPhases={displayedPhases} versions={store.versions} onOpenVersion={n => setPlotId(n === 1 ? null : `version:${n}`)}
        onClose={() => setDrawerOpen(false)} onPreview={setPreview} previewRef={preview?.key} onAdd={addCandidate} busy={quick.busy} />
      <FitCompare taskId={task_id} a={compare?.a} b={compare?.b} open={Boolean(compare)} onClose={() => setCompare(null)}
        adopting={review.saving} allResults={data.all_results} versions={store.versions} onSelect={(a, b) => setCompare({ a, b })}
        onAdoptB={async sel => { if (await review.save(sel)) setCompare(null); }}
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
          {data && data.status !== 'FIZZLED' ? <Button href={`/?clone_task=${task_id}`}>Rerun</Button> : null}
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
