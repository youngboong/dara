import React from 'react';
import { Alert, Button, Dropdown, Modal, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { EllipsisOutlined } from '@ant-design/icons';
import { EXPORT_FORMATS, exportHref } from './confidence.mjs';
import { downloadReport } from './ResultWorkflows';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import { BASELINE_LABEL, QUICK_CHECK_HINT, REFINE_ALL_FIRST, comparisonBase, formulaOfPhase } from './versions.mjs';

const { Text } = Typography;

const ORIGIN_LABEL = {
  automatic: 'Automatic', add_hint: 'Hint', add_peak_search: 'Peak search', swap: 'Swap',
  remove: 'Remove', background: 'Background', manual: 'Manual', refine_all: 'Refine all',
};
const ORIGIN_COLOR = {
  automatic: 'default', add_hint: 'geekblue', add_peak_search: 'purple', swap: 'orange',
  remove: 'volcano', background: 'cyan', manual: 'blue', refine_all: 'green',
};

export const VERSIONS_POLL_MS = 15000;

/** Versions of one task. `available` is false when the server has no versions endpoint (404/405). */
export function useVersions(taskId) {
  const [versions, setVersions] = React.useState([]);
  const [available, setAvailable] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const [baselineStale, setBaselineStale] = React.useState(false);
  const mounted = React.useRef(true);
  const refresh = React.useCallback(async ({ silent = false } = {}) => {
    try {
      const body = await requestJson(`/task/${taskId}/versions`);
      if (!mounted.current) return [];
      const list = Array.isArray(body) ? body : (body?.versions || []);
      setVersions(list);
      setBaselineStale(!Array.isArray(body) && body?.baseline_stale === true);
      setAvailable(true);
      setError('');
      return list;
    } catch (e) {
      if (!mounted.current) return [];
      if (e.missing) setAvailable(false);
      else if (!silent) setError(e.message);
      return [];
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [taskId]);
  React.useEffect(() => {
    mounted.current = true;
    setLoading(true);
    refresh();
    return () => { mounted.current = false; };
  }, [refresh]);
  // Versions are also created from other tabs and by jobs that finish while the page is open:
  // refetch on window focus and every 15 s (a cheap endpoint) while the page is visible.
  React.useEffect(() => {
    const quiet = () => { if (!document.hidden) refresh({ silent: true }); };
    const handle = setInterval(quiet, VERSIONS_POLL_MS);
    window.addEventListener('focus', quiet);
    document.addEventListener('visibilitychange', quiet);
    return () => {
      clearInterval(handle);
      window.removeEventListener('focus', quiet);
      document.removeEventListener('visibilitychange', quiet);
    };
  }, [refresh]);
  const remove = React.useCallback(async n => {
    await requestJson(`/task/${taskId}/versions/${n}`, { method: 'DELETE' });
    return refresh();
  }, [taskId, refresh]);
  return { versions, available, loading, error, refresh, remove, baselineStale };
}

const fixed = (value, digits = 2) => (value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits));

function createdText(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : `Created ${date.toLocaleString('en-US')}`;
}

function phaseSummary(phases = []) {
  if (!phases.length) return '—';
  return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phases.join('\n')}</div>}><span>{phases.map(formulaOfPhase).join(', ')}</span></Tooltip>;
}

function originTag(row) {
  if (row.label === BASELINE_LABEL) return <Tag>Baseline</Tag>;
  const kind = row.origin?.kind;
  return <Tag color={ORIGIN_COLOR[kind] || 'default'}>{ORIGIN_LABEL[kind] || kind || '—'}</Tag>;
}

export default function VersionsPanel({ taskId, store, review, onView, onCompare, onRefineAll, refineBusy = false, extra }) {
  const { versions, available, loading, error, remove } = store;
  const [deleting, setDeleting] = React.useState(null);
  if (available === false) return null;
  const adopted = review?.saved?.selection;
  const confirmDelete = row => Modal.confirm({
    title: `Delete version ${row.version}?`,
    content: "Removes this version's refinement.",
    okText: 'Delete', okButtonProps: { danger: true }, cancelText: 'Cancel',
    async onOk() {
      setDeleting(row.version);
      try {
        await remove(row.version);
        message.success(`Version ${row.version} deleted.`);
      } catch (e) {
        message.error(e.missing ? NOT_AVAILABLE : e.message);
      } finally {
        setDeleting(null);
      }
    },
  });
  const isAdoptedRow = n => adopted === `version:${n}` || (n === 1 && adopted === 'best');
  const rows = versions.map(item => ({ ...item, key: item.version }));
  const columns = [
    { title: 'Version', dataIndex: 'version', width: 90, render: (n, row) => <Tooltip title={createdText(row.created_at)}>
      <span>{isAdoptedRow(n) ? <span style={{ color: '#faad14' }} aria-label="Adopted">★ </span> : null}{`v${n}`}</span>
    </Tooltip> },
    { title: 'Label', dataIndex: 'label', render: (label, row) => (row.version === 1 ? 'Best fit' : label || '—') },
    { title: 'Origin', key: 'origin', render: (_, row) => <Space size={4} wrap>
      {originTag(row)}
      {row.refine_mode === 'fast' ? <Tooltip title={QUICK_CHECK_HINT}><Tag color="gold">Quick check</Tag></Tooltip> : null}
    </Space> },
    { title: 'Rwp (%)', dataIndex: 'rwp', render: value => fixed(value) },
    { title: 'Phases', dataIndex: 'phases', render: phaseSummary },
    { title: 'Unexplained', dataIndex: 'n_unexplained', render: value => (value == null ? '—' : value) },
    { title: 'Actions', key: 'actions', render: (_, row) => {
      const sel = row.version === 1 ? 'best' : `version:${row.version}`;
      const isAdopted = adopted === sel;
      const quickCheck = row.refine_mode === 'fast';
      const deleteBlocked = row.version === 1 || row.deletable === false ? 'the best fit is kept'
        : adopted === sel ? 'un-adopt first' : null;
      const reportBlocked = quickCheck ? REFINE_ALL_FIRST.toLowerCase() : review.dirty ? 'save the review first' : null;
      const exportSel = row.version === 1 ? 'best' : sel;
      const items = [
        { key: 'report', disabled: Boolean(reportBlocked), label: reportBlocked ? `Report (${reportBlocked})` : 'Report (HTML)' },
        { key: 'export', label: 'Export for…', disabled: quickCheck, children: EXPORT_FORMATS.map(format => ({
          key: `export:${format.key}`, label: <a href={exportHref(API, taskId, format.key, exportSel)}>{format.label}</a> })) },
        { type: 'divider' },
        { key: 'delete', danger: true, disabled: Boolean(deleteBlocked), label: deleteBlocked ? `Delete (${deleteBlocked})` : 'Delete…' },
      ];
      const onMenu = ({ key }) => {
        if (key === 'report') downloadReport(taskId, exportSel);
        if (key === 'delete') confirmDelete(row);
      };
      return <Space size="small">
        <Button size="small" onClick={() => onView(row.version === 1 ? null : sel)}>View</Button>
        {row.version > 1 ? <Button size="small" onClick={() => onCompare(adopted != null && adopted !== sel ? adopted : comparisonBase('best', versions, row.version), sel)}>Compare</Button> : null}
        {quickCheck
          ? (onRefineAll ? <Tooltip title="Refine all phases, then compare">
            <Button size="small" type="primary" disabled={refineBusy} onClick={() => onRefineAll(row.version)}>Refine all phases</Button>
          </Tooltip> : null)
          : <Tooltip title="Adopt (saves notes too)">
            <span><Button size="small" disabled={review.loading || !!review.error || review.saving}
              onClick={() => review.save(sel)}>{isAdopted ? 'Adopted' : 'Adopt'}</Button></span>
          </Tooltip>}
        <Dropdown menu={{ items, onClick: onMenu }} trigger={['click']}>
          <Button size="small" icon={<EllipsisOutlined />} loading={deleting === row.version} aria-label={`More actions for version ${row.version}`} />
        </Dropdown>
      </Space>;
    } },
  ];
  return <section aria-label="Result versions" style={{ marginTop: 20, marginBottom: 20 }}>
    <h3 style={{ marginBottom: 8 }}>Versions</h3>
    {extra}
    {error ? <Alert type="error" showIcon style={{ marginBottom: 8 }} message={error} /> : null}
    <Table size="small" pagination={false} loading={loading} rowKey="version" dataSource={rows} tableLayout="auto"
      scroll={{ x: 'max-content' }} columns={columns} />
    {!loading && versions.length <= 1 ? <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
      No versions yet — use Add phase… in the fit view.</Text> : null}
  </section>;
}
