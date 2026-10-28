import React from 'react';
import { Alert, Button, Dropdown, Modal, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { DeleteOutlined, DownOutlined, ExportOutlined } from '@ant-design/icons';
import { EXPORT_FORMATS } from './confidence.mjs';
import { ReportDownloadButton } from './ResultWorkflows';
import { API, NOT_AVAILABLE, requestJson } from './QuickRefineProgress';
import { adoptedTag } from './versions.mjs';

const { Text } = Typography;

const ORIGIN_LABEL = {
  automatic: 'Automatic', add_hint: 'Hint', add_peak_search: 'Peak search', swap: 'Swap',
  remove: 'Remove', background: 'Background', manual: 'Manual',
};
const ORIGIN_COLOR = {
  automatic: 'default', add_hint: 'geekblue', add_peak_search: 'purple', swap: 'orange',
  remove: 'volcano', background: 'cyan', manual: 'blue',
};

export const VERSIONS_POLL_MS = 15000;

/** Versions of one task. `available` is false when the server has no versions endpoint (404/405). */
export function useVersions(taskId) {
  const [versions, setVersions] = React.useState([]);
  const [available, setAvailable] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const mounted = React.useRef(true);
  const refresh = React.useCallback(async ({ silent = false } = {}) => {
    try {
      const body = await requestJson(`/task/${taskId}/versions`);
      if (!mounted.current) return [];
      const list = Array.isArray(body) ? body : (body?.versions || []);
      setVersions(list);
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
  return { versions, available, loading, error, refresh, remove };
}

const fixed = (value, digits = 2) => (value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits));

function createdText(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('en-US');
}

function phaseSummary(phases = []) {
  if (!phases.length) return '—';
  const names = phases.map(name => String(name).replace(/_\d+_\(.*$/, ''));
  return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phases.join('\n')}</div>}><span>{names.join(', ')}</span></Tooltip>;
}

export default function VersionsPanel({ taskId, store, review, onView, onCompare, extra, onRebaseline, rebaselineBusy }) {
  const { versions, available, loading, error, remove } = store;
  const [deleting, setDeleting] = React.useState(null);
  if (available === false) return null;
  const adopted = review?.saved?.selection;
  const tag = adoptedTag(adopted);
  const confirmDelete = row => Modal.confirm({
    title: `Delete version ${row.version}?`,
    content: 'The stored refinement of this version is removed. The version number is not reused.',
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
  const rows = versions.map(item => ({ ...item, key: item.version }));
  const columns = [
    { title: 'Version', dataIndex: 'version', width: 80, render: n => `v${n}` },
    { title: 'Label', dataIndex: 'label', render: (label, row) => <>{label || '—'}{adopted === `version:${row.version}` || (row.version === 1 && adopted === 'best') ? <Tag color="blue" style={{ marginLeft: 6 }}>Adopted</Tag> : null}</> },
    { title: 'Origin', key: 'origin', render: (_, row) => <Tag color={ORIGIN_COLOR[row.origin?.kind] || 'default'}>{ORIGIN_LABEL[row.origin?.kind] || row.origin?.kind || '—'}</Tag> },
    { title: 'Rwp (%)', dataIndex: 'rwp', render: value => fixed(value) },
    { title: 'GOF', dataIndex: 'gof', render: value => fixed(value) },
    { title: 'Phases', dataIndex: 'phases', render: phaseSummary },
    { title: 'Unexplained', dataIndex: 'n_unexplained', render: value => (value == null ? '—' : value) },
    { title: 'Created', dataIndex: 'created_at', render: createdText },
    { title: 'Actions', key: 'actions', render: (_, row) => {
      const sel = row.version === 1 ? 'best' : `version:${row.version}`;
      const isAdopted = adopted === sel;
      const deleteDisabled = row.deletable === false || row.version === 1 || adopted === `version:${row.version}`;
      const deleteHint = row.version === 1 ? 'The original analysis cannot be deleted.'
        : adopted === `version:${row.version}` ? 'Un-adopt first' : 'Delete this version';
      const items = EXPORT_FORMATS.map(format => ({ key: format.key,
        label: <a href={`${API}/task/${taskId}/export/${format.key}?${row.version === 1 ? 'best=true' : `sel=version:${row.version}`}`}>{format.label}</a> }));
      return <Space wrap size="small">
        <Button size="small" onClick={() => onView(row.version === 1 ? null : sel)}>View</Button>
        {row.version > 1 ? <Button size="small" onClick={() => onCompare(adopted && adopted !== sel ? adopted : 'best', sel)}>Compare</Button> : null}
        <Tooltip title="Adopt this version and save the current lab notes">
          <Button size="small" disabled={review.loading || !!review.error || review.saving}
            onClick={() => review.save(sel)}>{isAdopted ? 'Adopted' : 'Adopt'}</Button>
        </Tooltip>
        <ReportDownloadButton taskId={taskId} selection={sel} size="small"
          disabled={review.loading || review.saving || review.dirty || !!review.error}>Report</ReportDownloadButton>
        <Dropdown menu={{ items }} trigger={['click']}>
          <Button size="small" icon={<ExportOutlined />}>Export <DownOutlined /></Button>
        </Dropdown>
        <Tooltip title={deleteHint}>
          <Button size="small" danger icon={<DeleteOutlined />} aria-label={`Delete version ${row.version}`}
            disabled={deleteDisabled} loading={deleting === row.version} onClick={() => confirmDelete(row)} />
        </Tooltip>
      </Space>;
    } },
  ];
  return <section aria-label="Result versions" style={{ marginTop: 20, marginBottom: 20 }}>
    <Space wrap align="baseline" style={{ marginBottom: 8 }}>
      <h3 style={{ margin: 0 }}>Versions</h3>
      {tag ? <Tag color="blue">{tag}</Tag> : null}
      <Text type="secondary">Re-refinements made from this result. The original analysis stays unchanged.</Text>
      {onRebaseline ? <Tooltip title="Refine the original phase set again with the current code, so later versions can be compared with a baseline made by the same code (v1 was refined when the task ran).">
        <Button size="small" disabled={rebaselineBusy} onClick={onRebaseline}>Re-refine baseline</Button>
      </Tooltip> : null}
    </Space>
    {extra}
    {error ? <Alert type="error" showIcon style={{ marginBottom: 8 }} message={error} /> : null}
    <Table size="small" pagination={false} loading={loading} rowKey="version" dataSource={rows} scroll={{ x: 1000 }}
      columns={columns} />
    {!loading && versions.length <= 1 ? <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
      No re-refinements yet. Use “Add and re-refine” on a hint, click a peak, or edit phases in the fit view.</Text> : null}
  </section>;
}
