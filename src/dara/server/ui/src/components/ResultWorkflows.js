import React from 'react';
import { Alert, Button, Input, message, Modal, Select, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { CheckOutlined, EditOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons';
import { DEFAULT_CONTROLS, RefinementControlsEditor } from './PracticalControls';

const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

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
      setSaved(body); setSelection(body.selection); setError('');
      message.success('Adopted result and notes saved. The minimum-Rwp result is unchanged.');
    } catch (e) { message.error(e.message); }
    finally { setSaving(false); }
  };
  return { saved, selection, setSelection, notes, setNotes, loading, saving, error, save,
    dirty: selection !== saved.selection || notes !== saved.notes };
}

export function ReportDownloadButton({ taskId, selection, children = 'Download report', ...props }) {
  const [loading, setLoading] = React.useState(false);
  const download = async () => {
    setLoading(true);
    try {
      const query = selection === 'best' ? '?best=true' : Number.isInteger(selection) ? `?idx=${selection}` : '';
      const response = await fetch(`${API}/task/${taskId}/report${query}`);
      if (!response.ok) await responseJson(response);
      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = `dara_task_${taskId}_${selection ?? 'adopted'}_report.html`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => window.URL.revokeObjectURL(objectUrl), 1000);
      message.success('Report downloaded. Open it in a browser to print or save as PDF.');
    } catch (e) { message.error(e.message || 'Report download failed.'); }
    finally { setLoading(false); }
  };
  return <Button icon={<FileTextOutlined />} {...props} loading={loading} onClick={download}>{children}</Button>;
}

export function ReviewPanel({ taskId, data, review, onView }) {
  const candidates = data.all_results.map((candidate, index) => ({
    value: candidate.source_index ?? index,
    label: `Candidate ${index + 1} — Rwp ${candidate.rwp}%`,
  }));
  const savedLabel = review.saved.selection === 'best' ? 'Minimum-Rwp best fit'
    : candidates.find(item => item.value === review.saved.selection)?.label || 'Not adopted yet';
  return <section aria-label="Result review" style={{ marginBottom: 20, padding: 16, border: '1px solid #d9d9d9', borderRadius: 6 }}>
    <h3>Adopted result &amp; lab notes</h3>
    {review.error ? <Alert type="error" showIcon message={review.error} /> : null}
    <p><Tag color={review.saved.selection == null ? 'default' : 'blue'}>{savedLabel}</Tag>
      <Typography.Text type="secondary">Your scientific choice is saved separately from the automatic best fit.</Typography.Text></p>
    <Space wrap style={{ marginBottom: 12 }}>
      <Select aria-label="Adopted candidate" style={{ width: 330, maxWidth: '100%' }}
        disabled={review.loading || !!review.error || review.saving}
        value={review.selection ?? 'unselected'}
        onChange={value => review.setSelection(value === 'unselected' ? null : value)}
        options={[{ value: 'unselected', label: 'No adopted result' }, { value: 'best', label: `Minimum-Rwp best fit — ${data.best_rwp}%` }, ...candidates]} />
      <Button disabled={review.saved.selection == null} onClick={() => onView(review.saved.selection === 'best' ? null : review.saved.selection)}>View adopted fit</Button>
      <ReportDownloadButton taskId={taskId} disabled={review.loading || !!review.error || review.dirty || review.saving}>Report (saved choice)</ReportDownloadButton>
    </Space>
    <Input.TextArea aria-label="Lab notes" value={review.notes} maxLength={10000} showCount rows={3}
      disabled={review.loading || !!review.error || review.saving} onChange={event => review.setNotes(event.target.value)}
      placeholder="Sample treatment, interpretation, reasons for choosing a candidate, remaining unexplained peaks…" />
    <Space wrap style={{ marginTop: 18 }}>
      <Button type="primary" icon={<SaveOutlined />} loading={review.saving} disabled={review.loading || !!review.error || !review.dirty} onClick={() => review.save()}>Save review</Button>
      {review.dirty ? <Typography.Text type="warning">Unsaved changes — save before exporting the report.</Typography.Text> : null}
      <Typography.Text type="secondary">Report: printable, offline HTML with fit, phase fractions, lattice parameters and references.</Typography.Text>
    </Space>
  </section>;
}

export function AdoptButton({ review, selection, ...props }) {
  return <Tooltip title="Adopt this candidate and save the current lab notes">
    <Button {...props} icon={<CheckOutlined />} loading={review.saving} disabled={review.loading || !!review.error}
      onClick={() => review.save(selection)}>{review.saved.selection === selection ? 'Adopted' : 'Adopt'}</Button>
  </Tooltip>;
}

export function RefineSelectionButton({ taskId, selection = 'best', phases = [], renderPhase, ...props }) {
  const [controls, setControls] = React.useState(DEFAULT_CONTROLS);
  const [savedParams, setSavedParams] = React.useState(null);
  const [visible, setVisible] = React.useState(false);
  const [available, setAvailable] = React.useState([]);
  const [selected, setSelected] = React.useState([]);
  const [filter, setFilter] = React.useState('');
  const [loading, setLoading] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState('');
  const open = async () => {
    setControls(DEFAULT_CONTROLS); setSavedParams(null);
    setVisible(true); setLoading(true); setFilter(''); setError(''); setAvailable([]); setSelected([]);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/phase-options`));
      setAvailable(body.phases);
      setSavedParams(body.final_refinement_params);
      setSelected(phases.filter(phase => body.phases.includes(phase)));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };
  const submit = async () => {
    setSubmitting(true);
    try {
      const body = await responseJson(await fetch(`${API}/task/${taskId}/refine-selected`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phases: selected, selection, controls }),
      }));
      window.location.href = `/task/?id=${body.wf_id}`;
    } catch (e) { setError(e.message); setSubmitting(false); }
  };
  return <>
    <Tooltip title="Include/exclude stored reference phases and run a new refinement">
      <Button {...props} icon={<EditOutlined />} onClick={open}>Edit phases</Button>
    </Tooltip>
    <Modal title="Select phases & re-refine" open={visible} width={850}
      onCancel={() => !submitting && setVisible(false)} maskClosable={!submitting}
      onOk={submit} okText="Run as new task" confirmLoading={submitting}
      cancelButtonProps={{ disabled: submitting }} okButtonProps={{ disabled: loading || !available.length || !selected.length || selected.length > 20 }}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
        message="Only the checked phases will be refined together."
        description="Uses this task's original pattern, instrument, fit range, excluded intervals and final-refinement settings. No database search is repeated and the original result is kept. Select just one reference per intended phase; equivalent CIFs are not extra components." />
      {error ? <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} /> : null}
      <Input.Search aria-label="Filter phase references" placeholder="Filter formula or COD / MP reference" value={filter} onChange={e => setFilter(e.target.value)} />
      <RefinementControlsEditor value={controls} onChange={setControls} />
      {savedParams ? <details><summary>Saved final-refinement parameters</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(savedParams, null, 2)}</pre></details> : null}
      <p style={{ marginTop: 8 }}>{selected.length} selected (maximum 20)</p>
      <Table size="small" loading={loading} rowKey="reference" pagination={{ pageSize: 8 }}
        rowSelection={{ selectedRowKeys: selected, preserveSelectedRowKeys: true, onChange: values => { setSelected(values); setError(''); },
          getCheckboxProps: () => ({ disabled: submitting }) }}
        dataSource={available.filter(name => name.toLowerCase().includes(filter.toLowerCase())).map(reference => ({ reference }))}
        columns={[{ title: 'Reference phase', dataIndex: 'reference', render: ref => renderPhase ? renderPhase(ref) : ref },
          { title: 'Reference ID', dataIndex: 'reference', render: ref => <Typography.Text style={{ overflowWrap: 'anywhere' }} type="secondary">{ref}</Typography.Text> },
          { title: 'Starting candidate', dataIndex: 'reference', render: ref => phases.includes(ref) ? <Tag>Included</Tag> : null }]} />
    </Modal>
  </>;
}
