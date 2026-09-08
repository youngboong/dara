import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Button, Descriptions, Divider, message, Modal, Spin, Tag, List, Space, Switch, Tooltip, Typography, Table, Select } from 'antd';
import { DeleteOutlined, DownloadOutlined, InfoCircleOutlined } from '@ant-design/icons';
import Loadable from 'react-loadable'
import parse from 'html-react-parser';
import {STATE_COLOR} from './results';
import { buildRefinementView, isTraceSelected, legendKey } from '../components/refinementPlotState.mjs';
import { formatParameterWithEsd, latticeParameterRows } from '../components/latticeParameters.mjs';

const { Text } = Typography;

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
  loader: () => import(`react-plotly.js`),
  loading: ({ timedOut }) =>
    timedOut ? (
      <blockquote>Error: Loading Plotly timed out.</blockquote>
    ) : (
      <Spin />
    ),
  timeout: 10000,
})


function RefinementPlot({ task_id, plot_id }) {
  const [plot, setPlot] = React.useState(undefined);
  const [error, setError] = React.useState('');
  const [scale, setScale] = React.useState('linear');
  const [showDiagnostics, setShowDiagnostics] = React.useState(true);
  const [showPhaseProfiles, setShowPhaseProfiles] = React.useState(true);
  const [showReflections, setShowReflections] = React.useState(true);
  const [resetKey, setResetKey] = React.useState(0);
  const [selection, setSelection] = React.useState({});
  const [yRange, setYRange] = React.useState(null);
  const lastLegendKey = React.useRef(null);
  const sameLegendClicked = React.useRef(false);
  const request_url = plot_id === null ? `${URL}/task/${task_id}/plot` : `${URL}/task/${task_id}/plot?idx=${plot_id}`;
  React.useEffect(() => {
    setPlot(undefined);
    setError('');
    setSelection({});
    setYRange(null);
    if (task_id === undefined || plot_id === undefined) return;
    const controller = new AbortController();
    fetch(request_url, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`Could not load plot (HTTP ${response.status}).`);
        return response.json();
      })
      .then(response => {
        setPlot(typeof response === 'string' ? JSON.parse(response) : response);
      }).catch(error => { if (error.name !== 'AbortError') setError(error.message); });
    return () => controller.abort();
  }, [task_id, plot_id]);
  if (error) return <Alert type="error" showIcon message={error} />;
  if (!plot) return <Spin />;
  const view = buildRefinementView(plot, {
    selection, showDiagnostics, showPhaseProfiles, showReflections, scale, yRange,
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
  return <>
    <Space wrap style={{ marginBottom: 8 }}>
      <Text>Intensity scale</Text>
      <Select value={scale} onChange={value => { setScale(value); setYRange(null); }} style={{ width: 100 }}
        options={[{ value: 'linear', label: 'Linear' }, { value: 'log', label: 'Log' }]} />
      <Text>Phase profiles</Text><Switch checked={showPhaseProfiles} onChange={setShowPhaseProfiles} />
      <Text>Bragg positions</Text><Switch checked={showReflections} onChange={value => { setShowReflections(value); setYRange(null); }} />
      <Text>Diagnostics</Text><Switch checked={showDiagnostics} onChange={setShowDiagnostics} />
      <Button onClick={() => { setYRange(null); setResetKey(value => value + 1); }}>Reset zoom</Button>
    </Space>
    <div style={{ marginBottom: 8 }}>
      <Text type="secondary">Click a phase in the legend to show its profile and Bragg positions. Hover for 2θ and hkl; drag to zoom. The lower panel shows observed − calculated.</Text>
    </div>
    <Plotly key={`${task_id}-${plot_id}-${resetKey}`} data={view.data}
      layout={{ ...view.layout, autosize: true,
        uirevision: `${task_id}-${plot_id}-${resetKey}` }}
      onLegendClick={toggleLegend}
      onLegendDoubleClick={isolateLegend}
      onRelayout={event => {
        if (event['yaxis.autorange']) setYRange(null);
        else if (event['yaxis.range[0]'] !== undefined && event['yaxis.range[1]'] !== undefined)
          setYRange([event['yaxis.range[0]'], event['yaxis.range[1]']]);
      }}
      config={{ responsive: true, displaylogo: false,
        toImageButtonOptions: { format: 'svg', filename: `dara_${task_id}_${plot_id ?? 'best'}`, width: 1600, height: 900 } }}
      useResizeHandler style={{ height: '760px', width: '100%' }} />
  </>;
}

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
  return (
    <Space size="small" wrap>
      <Tag color={counts.missing ? 'volcano' : 'default'}>{`Missing ${counts.missing || 0}`}</Tag>
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

function SearchSummary({ summary = {} }) {
  if (!Object.keys(summary).length) return <Text type="secondary">Detailed search counts were not stored for this older result.</Text>;
  return <Space size="small" wrap>
    <Tag>{`${summary.screening_results_retained ?? 0} retained tree result(s)`}</Tag>
    <Tag>{`${summary.combinations_evaluated ?? 0} combinations evaluated`}</Tag>
    <Tag color="blue">{`${summary.structurally_distinct_considered ?? 0} distinct combinations considered`}</Tag>
    <Tag>{`${summary.equivalent_combinations_merged ?? 0} equivalent combinations merged`}</Tag>
    <Tag color="green">{`${summary.displayed_hypotheses ?? 0} displayed`}</Tag>
    {summary.final_refinement_failed ? <Tag color="volcano">{`${summary.final_refinement_failed} final refinement failed`}</Tag> : null}
  </Space>;
}

function parameterText(name, parameter) {
  const value = formatParameterWithEsd(parameter);
  return value ? `${name} = ${value}` : null;
}

function PhaseRefinementTable({ phases = [], metadata = {}, details = {} }) {
  const dataSource = phases.map(phase => ({ phase, ...(details?.[phase] || {}) }));
  return <Table size="small" pagination={false} rowKey="phase" scroll={{ x: 1000 }} dataSource={dataSource}
    columns={[
      { title: 'Phase', dataIndex: 'phase', width: 260,
        render: phase => <PhaseIdentity reference={phase} metadata={metadata?.[phase]} /> },
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
    ]} />;
}


function TaskDetail({ data, task_id }) {
  const [plot_id, setPlotId] = React.useState(undefined);
  const [showAlternatives, setShowAlternatives] = React.useState(false);
  const displayedResult = plot_id === null
    ? data.final_result
    : data.all_results.find((result, index) => (result.source_index ?? index) === plot_id);

  return (
    <>
      <TitleContriner>
        <h3>Task details (<a onClick={() => setPlotId(null)}>Visualize final result</a>)</h3>
        <DownloadButton taskId={task_id} type="primary" icon={<DownloadOutlined />}>
          Download all results
        </DownloadButton>
      </TitleContriner>
      <Divider />
      <Descriptions bordered column={2} size='small'>
        <Descriptions.Item label="Pattern file name" span={2}>{data.task_label}</Descriptions.Item>
        <Descriptions.Item label="Best_Rwp" span={2}><Tag color="green">{data.best_rwp} %</Tag></Descriptions.Item>
        <Descriptions.Item label="Analysis mode" span={2}>{({
          known_mixture: 'Known-phase refinement',
          elemental: 'Phase identification',
          reaction: 'Reaction check',
        })[data.analysis_mode] || data.analysis_mode || (data.use_rxn_predictor ? "Reaction check" : "Phase identification")}</Descriptions.Item>
        <Descriptions.Item label="Temperature" span={2}>{data.temperature || "None"}</Descriptions.Item>
        <Descriptions.Item label="Use reaction network?" span={2}>{data.use_rxn_predictor ? "Yes" : "No"}</Descriptions.Item>
        <Descriptions.Item label="Included elements" span={2}>{(data.elements || []).join(', ') || "None"}</Descriptions.Item>
        <Descriptions.Item label="Reference databases" span={2}>{(data.candidate_sources || []).map(source => source.toUpperCase()).join(' + ') || "Uploaded CIFs"}</Descriptions.Item>
        <Descriptions.Item label="Candidate CIFs" span={2}>{data.candidate_summary?.after ?? "Unknown"} retained ({data.candidate_summary?.removed_duplicates ?? 0} duplicates removed)</Descriptions.Item>
        <Descriptions.Item label="Hypothesis search" span={2}><SearchSummary summary={data.search_summary} /></Descriptions.Item>
        <Descriptions.Item label="Additional search options" span={2}><code style={{backgroundColor: 'transparent'}}>{JSON.stringify(data.additional_search_options)}</code></Descriptions.Item>
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
      <Text type="secondary">Uncertainties are BGMN estimated standard deviations where reported. Weight uncertainty is propagated after normalization and is omitted when unavailable.</Text>
      <div style={{ marginTop: 10 }}>
        <PhaseRefinementTable phases={data.final_result.phases} metadata={data.final_result.phase_metadata} details={data.final_result.phase_details} />
      </div>
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
      <Text type="secondary">Sort columns to compare candidates. Candidate numbers and downloads keep their original identity. Fractions are normalized over modeled crystalline phases; diagnostic counts are review flags, not confidence scores.</Text>
      <Table size="small" pagination={false} scroll={{ x: 900 }} style={{ marginTop: 12, marginBottom: 24 }}
        rowKey="source_index"
        dataSource={data.all_results.map((item, index) => ({ ...item, candidate_number: index + 1, source_index: item.source_index ?? index }))}
        columns={[
          { title: 'Candidate', dataIndex: 'candidate_number', render: n => `Candidate ${n}` },
          { title: 'Phases / wt%', render: (_, row) => <Space direction="vertical">{row.highlighted_phases.map(phase => <span key={phase}><PhaseIdentity reference={phase} compact /> {row.weight_percent?.[phase] != null ? `${Number(row.weight_percent[phase]).toFixed(2)} wt%` : ''}</span>)}</Space> },
          { title: 'Rwp (%)', dataIndex: 'rwp', sorter: (a,b) => a.rwp-b.rwp, defaultSortOrder: 'ascend', render: (value,row) => <>{value} <Text type="secondary">{row.is_final_refined ? 'Final' : 'Screening'}</Text></> },
          { title: 'GOF', sorter: (a,b) => (a.fit_quality?.gof ?? Infinity)-(b.fit_quality?.gof ?? Infinity), render: (_,row) => formatMetric(row.fit_quality?.gof) },
          ...[['missing','Missing'],['extra','Extra'],['intensity_mismatch','Intensity mismatch']].map(([key,title]) => ({ title, sorter: (a,b) => (a.peak_diagnostics?.counts?.[key] ?? Infinity)-(b.peak_diagnostics?.counts?.[key] ?? Infinity), render: (_,row) => row.peak_diagnostics?.counts?.[key] ?? '—' })),
          { title: 'No. of phases', sorter: (a,b) => a.highlighted_phases.length-b.highlighted_phases.length, render: (_,row) => row.highlighted_phases.length },
          { title: 'Actions', render: (_,row) => <Space><Button size="small" onClick={() => setPlotId(row.source_index)}>View fit</Button><DownloadButton taskId={task_id} resultIndex={row.source_index} size="small" icon={<DownloadOutlined />} aria-label={`Download candidate ${row.candidate_number}`} /></Space> },
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
                  <a onClick={() => setPlotId(result.source_index ?? index)}>Visualize result</a>
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
      <Modal title={plot_id === null ? 'Best fit' : `Candidate ${(data.all_results.findIndex((r,i) => (r.source_index ?? i) === plot_id)) + 1} — refinement`} open={plot_id !== undefined} onCancel={() => setPlotId(undefined)} footer={[]} width={"95vw"}>
        <Select aria-label="Displayed candidate" style={{ width: 300, marginBottom: 12 }} value={plot_id === null ? 'best' : plot_id}
          onChange={value => setPlotId(value === 'best' ? null : value)}
          options={[{value:'best',label:'Best fit'}, ...data.all_results.map((r,i) => ({ value:r.source_index ?? i,label:`Candidate ${i+1} — Rwp ${r.rwp}%` }))]} />
        {displayedResult ? <>
          <div style={{ marginBottom: 12 }}><FitQualitySummary quality={displayedResult.fit_quality} /></div>
          <PhaseRefinementTable phases={displayedResult.highlighted_phases || displayedResult.phases || []}
            metadata={displayedResult.phase_metadata} details={displayedResult.phase_details} />
          <Divider />
        </> : null}
        <RefinementPlot task_id={task_id} plot_id={plot_id} />
      </Modal>
    </>
  )
}

function TaskDetailedUnfinished({ data }) {
  const { status, submitted_on, start_time, end_time, error_tb } = data;
  return (<Descriptions bordered column={1} size='small'>
    <Descriptions.Item label="Name" span={1}>{data.task_label}</Descriptions.Item>
    <Descriptions.Item label="Status" span={1}><Tag color={STATE_COLOR[status]}>{status}</Tag></Descriptions.Item>
    <Descriptions.Item label="Submitted on" span={1}>{submitted_on}</Descriptions.Item>
    {start_time ? <Descriptions.Item label="Start time" span={1}>{start_time}</Descriptions.Item> : <></>}
    {end_time ? <Descriptions.Item label="End time" span={1}>{end_time}</Descriptions.Item> : <></>}
    {error_tb ? <Descriptions.Item label="Error Traceback" span={1}><div class="error-code"><pre style={{ whiteSpace: "break-spaces", fontSize: "0.9em" }}>{error_tb}</pre></div></Descriptions.Item> : <></>}
  </Descriptions>)
}


function TaskPage({ location }) {
  const queryParameters = new URLSearchParams(location.search)  
  const task_id = queryParameters.get('id')
  const [error, setError] = React.useState("");

  const [data, setData] = React.useState(undefined);
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
  }, [task_id]);

  return (
    <Layout hasSider={false} title="Result">
      <Container>
        <Title>
        <h2>Task {task_id}</h2>
        <Space>
          {data && data.status === 'COMPLETED' ? (
            <DownloadButton taskId={task_id} icon={<DownloadOutlined />}>Download all</DownloadButton>
          ) : null}
          {data && data.status !== 'READY' && data.status !== 'RUNNING' ? (
            <Button danger icon={<DeleteOutlined />} onClick={deleteTask}>Delete</Button>
          ) : null}
          <Button type="primary" href={`https://github.com/CederGroupHub/dara/issues/new`} target="_blank">Open Github Issue</Button>
        </Space>
        </Title>
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
          data && data.status !== "COMPLETED" ? <TaskDetailedUnfinished data={data} /> : <></>
        }
      </Container>
    </Layout>
  )
}

export default TaskPage;
