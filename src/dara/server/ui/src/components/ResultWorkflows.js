import React from 'react';
import { Alert, Button, Input, message, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { EditOutlined, InfoCircleOutlined, SaveOutlined } from '@ant-design/icons';
import { runAfterSuccess } from './submitHelpers.mjs';
import { API, NOT_AVAILABLE, postJson, requestJson } from './QuickRefineProgress';
import { DEFAULT_CONTROLS, RefinementControlsEditor } from './PracticalControls';
import { selectionQuery } from './confidence.mjs';
import { selectOptionGroups, selectionLabel, selectedPhasesRequest } from './versions.mjs';

async function responseJson(response) {
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (HTTP ${response.status}).`);
  return body;
}

export function useTaskReview(taskId) {
  const [saved, setSaved] = React.useState({ selection: null, notes: '' });
  const [selection, setSelection] = React.useState(null);
  const [notes, setNotes] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch(`${API}/task/${taskId}/review`, { signal: controller.signal })
      .then(responseJson).then(body => {
        setSaved(body); setSelection(body.selection); setNotes(body.notes); setError('');
      }).catch(e => { if (e.name !== 'AbortError') setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [taskId]);
  const save = async (choice = selection) => {
    setSaving(true);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/review`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection: choice, notes }),
      }));
      // Already saved on the server; UI follow-up problems must not look like a failed save.
      runAfterSuccess([
        () => setSaved(body), () => setSelection(body.selection), () => setError(''),
        () => message.success('Review saved.'),
      ]);
      return true;
    } catch (e) { message.error(e.message); return false; }
    finally { setSaving(false); }
  };
  return { saved, selection, setSelection, notes, setNotes, loading, saving, error, save,
    dirty: selection !== saved.selection || notes !== saved.notes };
}

/** Download the printable HTML report of `selection` (undefined = the saved adopted choice). */
export async function downloadReport(taskId, selection) {
  const hide = message.loading('Preparing the report…', 0);
  try {
    const response = await fetch(`${API}/task/${taskId}/report${selection === undefined ? '' : selectionQuery(selection)}`);
    if (!response.ok) await responseJson(response);
    const blob = await response.blob();
    const objectUrl = window.URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = `dara_task_${taskId}_${String(selection ?? 'adopted').replace(':', '_')}_report.html`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
    hide();
    message.success('Report downloaded. Open it in a browser to print or save as PDF.');
  } catch (e) { hide(); message.error(e.message || 'Report download failed.'); }
}

export function ReviewPanel({ data, review, versions = [] }) {
  // Quick checks are approximate fits: they are never offered for adoption.
  const groups = selectOptionGroups(data.all_results, versions, { adoptable: true, current: review.selection });
  const saved = review.saved.selection;
  const savedLabel = saved == null ? 'Not adopted yet' : selectionLabel(saved, { all_results: data.all_results, versions }) || String(saved);
  return <section aria-label="Result review" className="dara-panel" style={{ marginBottom: 20, padding: 16, border: '1px solid #d9d9d9', borderRadius: 6 }}>
    <h3>Adopted fit &amp; notes</h3>
    {review.error ? <Alert type="error" showIcon message={review.error} /> : null}
    <Space wrap style={{ marginBottom: 12 }}>
      <Tag color={saved == null ? 'default' : 'blue'}>{savedLabel}</Tag>
      <Select aria-label="Adopted fit" style={{ minWidth: 330, maxWidth: '100%' }}
        disabled={review.loading || !!review.error || review.saving}
        value={review.selection ?? 'unselected'}
        onChange={value => review.setSelection(value === 'unselected' ? null : value)}
        options={[{ value: 'unselected', label: 'No adopted result' }, ...groups]} />
    </Space>
    <Input.TextArea aria-label="Lab notes" value={review.notes} maxLength={10000} showCount rows={3}
      disabled={review.loading || !!review.error || review.saving} onChange={event => review.setNotes(event.target.value)}
      placeholder="Sample treatment, interpretation, reasons for choosing a fit, remaining unexplained peaks…" />
    <Space wrap style={{ marginTop: 18 }}>
      <Tooltip title="The report and downloads use the saved choice">
        <Button type="primary" icon={<SaveOutlined />} loading={review.saving} disabled={review.loading || !!review.error || !review.dirty} onClick={() => review.save()}>Save review</Button>
      </Tooltip>
      {review.dirty ? <Typography.Text type="warning">Unsaved changes</Typography.Text> : null}
    </Space>
  </section>;
}

/**
 * "Select phases…" button and checklist modal. The checked phases are submitted through the quick
 * re-refinement path (`onSubmit(request)`), which stores a new version of the same task.
 */
export function PhaseSelectionButton({ taskId, phases = [], renderPhase, busy = false, onSubmit, disabledReason = null, ...props }) {
  const [visible, setVisible] = React.useState(false);
  const [available, setAvailable] = React.useState([]);
  const [selected, setSelected] = React.useState([]);
  const [filter, setFilter] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [controls, setControls] = React.useState(DEFAULT_CONTROLS);
  const open = async () => {
    setVisible(true); setLoading(true); setFilter(''); setError(''); setAvailable([]); setSelected([]);
    setControls(DEFAULT_CONTROLS);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/phase-options`));
      setAvailable(body.phases);
      setSelected(phases.filter(phase => body.phases.includes(phase)));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };
  const request = selectedPhasesRequest({ current: phases, selected, available, controls, defaultControls: DEFAULT_CONTROLS });
  const submit = () => {
    if (!request) return;
    setVisible(false);
    onSubmit(request);
  };
  return <>
    <Tooltip title={disabledReason || 'Choose the phases to refine as a new version'}>
      <span><Button {...props} icon={<EditOutlined />} disabled={busy || props.disabled || Boolean(disabledReason)} onClick={open}>Select phases…</Button></span>
    </Tooltip>
    <Modal title="Select phases" open={visible} width={850}
      onCancel={() => setVisible(false)} onOk={submit} okText="Re-refine and compare"
      okButtonProps={{ disabled: loading || !request || selected.length > 20 }}>
      <Typography.Paragraph type="secondary">
        Checked phases are refined together as a new version.{' '}
        <Tooltip title="Uses the stored references of this task; the original result is kept. Check one reference per phase: equivalent CIFs are not extra components.">
          <InfoCircleOutlined />
        </Tooltip>
      </Typography.Paragraph>
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
      <Input.Search aria-label="Filter phase references" placeholder="Filter formula or COD / MP reference" value={filter} onChange={e => setFilter(e.target.value)} />
      <p style={{ marginTop: 8 }}>{`${selected.length} / 20`}</p>
      <Table size="small" loading={loading} rowKey="reference" pagination={{ pageSize: 8 }}
        rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: values => { setSelected(values); setError(''); } }}
        dataSource={available.filter(name => name.toLowerCase().includes(filter.toLowerCase())).map(reference => ({ reference }))}
        columns={[{ title: 'Reference phase', dataIndex: 'reference', render: ref => renderPhase ? renderPhase(ref) : ref },
          { title: 'Reference ID', dataIndex: 'reference', render: ref => <Typography.Text style={{ overflowWrap: 'anywhere' }} type="secondary">{ref}</Typography.Text> },
          { title: 'In this fit', dataIndex: 'reference', render: ref => phases.includes(ref) ? <Tag>Included</Tag> : null }]} />
      <RefinementControlsEditor value={controls} onChange={setControls} />
    </Modal>
  </>;
}

/** Queue a new analysis from a FIZZLED task. Navigates to the new task on success. */
export async function retryTask(taskId, applyRemedy) {
  try {
    const body = await postJson(`/task/${taskId}/retry`, { apply_remedy: Boolean(applyRemedy) });
    if (body?.wf_id == null) throw new Error('The server did not return the new task.');
    message.success(`Retry queued as task ${body.wf_id}.`);
    window.location.href = `/task/?id=${body.wf_id}`;
  } catch (e) {
    message.error(e.missing ? NOT_AVAILABLE : e.message);
  }
}

const changeText = value => (value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value));

/** Retry buttons of a failed task: suggested fix (after a confirmation of the changes) and a copy of the conditions. */
export function RetryActions({ taskId, errorSummary }) {
  const [busy, setBusy] = React.useState(false);
  const remedy = errorSummary?.remedy;
  const retryWithFix = async () => {
    setBusy(true);
    try {
      const plan = await requestJson(`/task/${taskId}/retry-plan`);
      if (plan.supported === false || !plan.changes) {
        message.info(plan.reason || 'No automatic fix applies to this failure. Use “Rerun”.');
        return;
      }
      Modal.confirm({
        title: `${plan.label || remedy?.label || 'Retry with suggested fix'}?`,
        width: 520,
        content: <div>
          <p>A new task is queued with these changes; the failed task is kept.</p>
          <ul>{Object.entries(plan.changes).map(([key, value]) => <li key={key}><code>{key}</code>{' → '}<code>{changeText(value)}</code></li>)}</ul>
        </div>,
        okText: 'Retry with fix',
        cancelText: 'Cancel',
        onOk: () => retryTask(taskId, true),
      });
    } catch (e) {
      message.error(e.missing ? NOT_AVAILABLE : e.message);
    } finally {
      setBusy(false);
    }
  };
  return <Space wrap style={{ marginBottom: 12 }}>
    {remedy?.label ? <Button type="primary" loading={busy} onClick={retryWithFix}>{`Retry with suggested fix (${remedy.label})`}</Button> : null}
    <Button type={remedy?.label ? 'default' : 'primary'} href={`/?clone_task=${taskId}`}>Rerun</Button>
  </Space>;
}
