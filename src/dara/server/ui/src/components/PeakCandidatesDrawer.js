import React from 'react';
import { Alert, Button, Drawer, List, Progress, Space, Spin, Tag, Tooltip, Typography } from 'antd';
import { NOT_AVAILABLE, postJson } from './QuickRefineProgress';
import { formatSelection } from './versions.mjs';

const { Text } = Typography;
const POOLS = ['candidate', 'input', 'library', 'cod'];
const RETRY_MS = 3000;
const MAX_RETRIES = 5;

const SOURCE_LABEL = { task: 'This task', library: 'Library', cod: 'COD' };

function sourceKind(source) {
  return String(source || 'task').split(':')[0];
}

function mergeCandidates(previous, incoming) {
  const byRef = new Map(previous.map(item => [`${item.source}|${item.ref}`, item]));
  for (const item of incoming) byRef.set(`${item.source}|${item.ref}`, item);
  return [...byRef.values()].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
}

export function candidateFormula(candidate) {
  return candidate.formula || String(candidate.ref || candidate.name || '').replace(/_\d+_\(.*$/, '');
}

/** Reference candidates that explain the selected peaks (POST /task/{id}/peak-candidates). */
export default function PeakCandidatesDrawer({ taskId, sel, peaks, open, onClose, onPreview, previewRef, onAdd, busy }) {
  const [candidates, setCandidates] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState('');
  const peaksKey = (peaks || []).join(',');
  const selText = formatSelection(sel) ?? 'best';

  React.useEffect(() => {
    if (!open || !peaks?.length) return undefined;
    let active = true;
    let timer = null;
    const controller = new AbortController();
    setCandidates([]);
    setError('');
    setLoading(true);
    setPending(false);
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
  }, [open, taskId, selText, peaksKey]);

  React.useEffect(() => { if (!open && onPreview) onPreview(null); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return <Drawer title={`Candidates for ${(peaks || []).map(peak => `${Number(peak).toFixed(2)}°`).join(', ')}`} placement="right" width={480}
    open={open} onClose={onClose} keyboard={false} mask={false} destroyOnClose={false} className="dara-peak-drawer">
    {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
    {loading ? <Space direction="vertical" style={{ width: '100%', padding: 24, textAlign: 'center' }}>
      <Spin />
      <Text type="secondary">Searching references… (first search can take up to 15 s)</Text>
    </Space> : null}
    {!loading && !error && !candidates.length ? <Text type="secondary">
      No reference matched the selected peaks.{pending ? ' Still searching COD…' : ''}</Text> : null}
    {pending && candidates.length ? <Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>Still searching COD…</Text> : null}
    <List dataSource={candidates} rowKey={item => `${item.source}|${item.ref}`} renderItem={candidate => {
      const previewing = previewRef === `${candidate.source}|${candidate.ref}`;
      const disabledReason = candidate.cif_available === false ? 'The CIF file for this reference is not available locally.'
        : busy ? 'Another re-refinement is running.' : null;
      return <List.Item key={`${candidate.source}|${candidate.ref}`} actions={[
        <Button key="preview" size="small" disabled={!candidate.sticks?.length}
          onClick={() => onPreview(previewing ? null : { key: `${candidate.source}|${candidate.ref}`, sticks: candidate.sticks })}>
          {previewing ? 'Hide ticks' : 'Preview ticks'}
        </Button>,
        <Tooltip key="add" title={disabledReason}>
          <span><Button size="small" type="primary" disabled={Boolean(disabledReason)} onClick={() => onAdd(candidate)}>Add and re-refine</Button></span>
        </Tooltip>,
      ]}>
        <List.Item.Meta
          title={<Space wrap size="small">
            <strong>{candidateFormula(candidate)}</strong>
            {candidate.spacegroup_symbol ? <Text type="secondary">{candidate.spacegroup_symbol}{candidate.spacegroup_number ? ` (No. ${candidate.spacegroup_number})` : ''}</Text> : null}
            <Tag>{SOURCE_LABEL[sourceKind(candidate.source)] || candidate.source}</Tag>
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
  </Drawer>;
}
