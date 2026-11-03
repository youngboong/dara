import React from 'react';
import { Alert, Button, Drawer, List, Progress, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { NOT_AVAILABLE, postJson } from './QuickRefineProgress';
import { formatSelection, refSourceForHint } from './versions.mjs';
import { autoMatchStrength, collectHints, hintText, matchStrength, significantPeaks } from './peakSelection.mjs';

const { Text } = Typography;
const POOLS = ['candidate', 'input', 'library', 'cod'];
const RETRY_MS = 3000;
const MAX_RETRIES = 20; // the COD pool can take ~1 min to load the first time
const AUTO_SHOWN = 5;

const SOURCE_LABEL = { task: 'This task', library: 'Library', cod: 'COD' };

function sourceKind(source) {
  return String(source || 'task').split(':')[0];
}

function mergeCandidates(previous, incoming) {
  const byRef = new Map(previous.map(item => [`${item.source}|${item.ref}`, item]));
  for (const item of incoming) byRef.set(`${item.source}|${item.ref}`, item);
  return [...byRef.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
}

const peakList = peaks => peaks.map(peak => `${Number(peak).toFixed(2)}°`).join(', ');

export function candidateFormula(candidate) {
  return candidate.formula || String(candidate.ref || candidate.name || '').replace(/_\d+_\(.*$/, '');
}

/** Candidates explaining `peaks` (POST /task/{id}/peak-candidates), re-polled while COD loads. */
function usePeakCandidates(taskId, selText, peaks, enabled) {
  const [candidates, setCandidates] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState('');
  const peaksKey = (peaks || []).join(',');

  React.useEffect(() => {
    setCandidates([]);
    setError('');
    setPending(false);
    if (!enabled || !peaks?.length) { setLoading(false); return undefined; }
    let active = true;
    let timer = null;
    const controller = new AbortController();
    setLoading(true);
    const run = async attempt => {
      try {
        const body = await postJson(`/task/${taskId}/peak-candidates`,
          { sel: selText, two_theta: peaks, pools: POOLS }, { signal: controller.signal });
        if (!active) return;
        setCandidates(previous => (attempt === 0 ? mergeCandidates([], body.candidates || []) : mergeCandidates(previous, body.candidates || [])));
        setLoading(false);
        const waiting = (body.pools_pending || []).includes('cod');
        setPending(waiting && attempt < MAX_RETRIES);
        if (waiting && attempt < MAX_RETRIES) timer = setTimeout(() => run(attempt + 1), RETRY_MS);
      } catch (e) {
        if (!active || e.name === 'AbortError') return;
        setLoading(false);
        setPending(false);
        setError(e.missing ? NOT_AVAILABLE : e.message);
      }
    };
    run(0);
    return () => { active = false; controller.abort(); if (timer) clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, taskId, selText, peaksKey]);

  return { candidates, loading, pending, error };
}

function CandidateList({ search, peaks, limit, previewRef, onPreview, onAdd, busy, emptyText, strengthOf = matchStrength }) {
  const { candidates, loading, pending, error } = search;
  if (error) return <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />;
  if (loading) {
    return <Space direction="vertical" style={{ width: '100%', padding: 24, textAlign: 'center' }}>
      <Spin />
      <Text type="secondary">Searching references… (first search can take up to 15 s)</Text>
    </Space>;
  }
  if (!candidates.length) return <Text type="secondary">{emptyText}{pending ? ' Still searching COD…' : ''}</Text>;
  const shown = limit ? candidates.slice(0, limit) : candidates;
  return <>
    {pending ? <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>Still searching COD…</Text> : null}
    <List dataSource={shown} rowKey={item => `${item.source}|${item.ref}`} renderItem={candidate => {
      const previewing = previewRef === `${candidate.source}|${candidate.ref}`;
      const disabledReason = candidate.cif_available === false ? 'The CIF file for this reference is not available locally.'
        : busy ? 'Another re-refinement is running.' : null;
      const strength = strengthOf(candidate);
      return <List.Item key={`${candidate.source}|${candidate.ref}`} actions={[
        <Button key="preview" size="small" disabled={!candidate.sticks?.length}
          onClick={() => onPreview(previewing ? null : { key: `${candidate.source}|${candidate.ref}`, sticks: candidate.sticks })}>
          {previewing ? 'Hide ticks' : 'Preview ticks'}
        </Button>,
        <Tooltip key="add" title={disabledReason}>
          <span><Button size="small" type="primary" disabled={Boolean(disabledReason)} onClick={() => onAdd(candidate)}>Add</Button></span>
        </Tooltip>,
      ]}>
        <List.Item.Meta
          title={<Space wrap size="small">
            <strong>{candidateFormula(candidate)}</strong>
            {candidate.spacegroup_symbol ? <Text type="secondary">{candidate.spacegroup_symbol}{candidate.spacegroup_number ? ` (No. ${candidate.spacegroup_number})` : ''}</Text> : null}
            <Tag>{SOURCE_LABEL[sourceKind(candidate.source)] || candidate.source}</Tag>
            {strength ? <Tooltip title="Strong: explains the selected peaks with no contradicting reflection. Possible: explains several peaks with few contradictions; add it and compare the fits. Weak: only some peaks overlap, often by chance.">
              <Tag color={strength.color}>{strength.label}</Tag>
            </Tooltip> : null}
          </Space>}
          description={<div>
            <Progress percent={Math.max(0, Math.min(100, Math.round((candidate.score ?? 0) * 100)))} size="small" showInfo={false} />
            <Text type="secondary">
              {`Score ${Number(candidate.score ?? 0).toFixed(2)} · matched ${candidate.matched?.length ?? 0} of ${peaks.length} peak${peaks.length === 1 ? '' : 's'} · ${candidate.n_strong ?? 0} strong reflections`}
              {candidate.strong_unobserved ? ` · ${candidate.strong_unobserved} not seen in the data` : ''}
            </Text>
            <div><Text type="secondary" style={{ fontSize: '0.85em', overflowWrap: 'anywhere' }}>{candidate.ref}</Text></div>
          </div>} />
      </List.Item>;
    }} />
    {limit && candidates.length > limit ? <Text type="secondary">{`${candidates.length - limit} more with lower scores. Select peaks in the plot to narrow the search.`}</Text> : null}
  </>;
}

/**
 * "Add phase" drawer: trace-phase hints and an automatic search for the significant unexplained
 * peaks (both suggested by DARA), plus a search for the peaks clicked in the plot.
 * `onSuggestions(n)` reports how many automatic candidates are at least a possible match.
 */
export default function PeakCandidatesDrawer({
  taskId, sel, peaks, open, onClose, onPreview, previewRef, onAdd, busy, diagnostics = null, onAddHint, onSuggestions,
  active = true,
}) {
  const selText = formatSelection(sel) ?? 'best';
  const autoPeaks = significantPeaks(diagnostics);
  // Runs as soon as the fit is shown (`active`), so the "Add phase…" button can announce matches.
  const auto = usePeakCandidates(taskId, selText, autoPeaks, active && autoPeaks.length > 0);
  const clicked = usePeakCandidates(taskId, selText, peaks, open && peaks?.length > 0);
  const autoStrength = candidate => autoMatchStrength(candidate, autoPeaks.length);
  const useful = auto.candidates.filter(candidate => autoStrength(candidate).label === 'Possible match').length;

  React.useEffect(() => { if (onSuggestions) onSuggestions(auto.loading ? 0 : useful); }, [auto.loading, useful]); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => { if (!open && onPreview) onPreview(null); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const listProps = { previewRef, onPreview, onAdd, busy };
  const hints = collectHints(diagnostics);
  return <Drawer title="Add phase" placement="right" width={480}
    open={open} onClose={onClose} keyboard={false} mask={false} destroyOnClose={false} className="dara-peak-drawer">
    <Typography.Title level={5} style={{ marginTop: 0 }}>Suggested by DARA</Typography.Title>
    {hints.length ? <div aria-label="Trace phase hints" style={{ marginBottom: 12 }}>
      {hints.map(hint => {
        const target = refSourceForHint(hint);
        return <div key={hint.phase} style={{ marginBottom: 4 }}>
          <Space wrap size="small">
            <Text>{hintText(hint)}</Text>
            <Tooltip title={!target ? 'No local CIF is available for this reference.' : busy ? 'A re-refinement is already running.' : 'Add this phase and refine again as a new version'}>
              <span><Button size="small" type="primary" disabled={!target || busy || !onAddHint} onClick={() => onAddHint(hint, target)}>Add</Button></span>
            </Tooltip>
          </Space>
        </div>;
      })}
    </div> : <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>No trace-phase suggestions for this fit.</Text>}
    <div aria-label="Significant unexplained peaks" style={{ marginBottom: 16 }}>
      <Text strong style={{ display: 'block', marginBottom: 4 }}>
        {autoPeaks.length ? `Matches for the significant unexplained peaks (${peakList(autoPeaks)})` : 'Matches for the significant unexplained peaks'}
      </Text>
      {autoPeaks.length
        ? <CandidateList search={auto} peaks={autoPeaks} limit={AUTO_SHOWN} emptyText="No reference matched these peaks." strengthOf={autoStrength} {...listProps} />
        : <Text type="secondary">This fit has no significant unexplained peaks.</Text>}
    </div>
    <Typography.Title level={5}>{peaks?.length ? `Search by clicked peaks (${peakList(peaks)})` : 'Search by clicked peaks'}</Typography.Title>
    {peaks?.length
      ? <CandidateList search={clicked} peaks={peaks} emptyText="No reference matched the selected peaks." {...listProps} />
      : <Text type="secondary">Click unexplained peaks in the plot (Shift+click for several) to search for matching phases.</Text>}
  </Drawer>;
}
