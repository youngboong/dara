import React from 'react';
import { Alert, Button, Checkbox, Form, Input, InputNumber, message, Modal, Progress, Select, Space, Tag } from 'antd';

const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
export const DEFAULT_CONTROLS = { lattice: 'inherit', lattice_percent: 2, orientation: 'inherit', peak_width: 'inherit' };

export function RefinementControlsEditor({ value = DEFAULT_CONTROLS, onChange }) {
  const update = patch => onChange({ ...value, ...patch });
  return <section aria-label="Final refinement controls" style={{ padding: 12, border: '1px solid #ddd', marginBottom: 16 }}>
    <h4>Final refinement controls</h4>
    <p>Applies to every final candidate. “Keep saved/default” preserves the original settings. Fast candidate screening is unchanged.</p>
    <Space wrap align="start">
      <label>Lattice parameters<br /><Select aria-label="Lattice refinement" value={value.lattice} style={{ width: 200 }} onChange={lattice => update({ lattice })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'fixed',label:'Fixed to reference CIF'}, {value:'refine',label:'Refine within bounds'}]} /></label>
      {value.lattice === 'refine' ? <label>Allowed lattice change (±%)<br /><InputNumber aria-label="Lattice bound percent" min={0.01} max={10} step={0.5} value={value.lattice_percent} onChange={lattice_percent => update({ lattice_percent })} /></label> : null}
      <label>Preferred orientation<br /><Select aria-label="Preferred orientation" value={value.orientation} style={{ width: 220 }} onChange={orientation => update({ orientation })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'isotropic',label:'None (random powder)'}, {value:'SPHAR2',label:'Spherical harmonics: order 2'}, {value:'SPHAR4',label:'Spherical harmonics: order 4'}]} /></label>
      <label>Sample peak broadening<br /><Select aria-label="Peak broadening" value={value.peak_width} style={{ width: 220 }} onChange={peak_width => update({ peak_width })}
        options={[{value:'inherit',label:'Keep saved/default'}, {value:'refine',label:'Refine K1, K2 and B1'}, {value:'instrument_only',label:'Fixed K1 = K2 = B1 = 0'}]} /></label>
    </Space>
    <p style={{ marginTop: 12, marginBottom: 0, color: '#666' }}>More flexible models can lower Rwp without proving the phase assignment. Instrument-only broadening is appropriate only when sample broadening is negligible.</p>
  </section>;
}

export function TaskProgress({ taskId, onStatusChange }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  React.useEffect(() => {
    let active = true; let timer;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const response = await fetch(`${API}/task/${taskId}/progress`, { signal: controller.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || 'Progress unavailable');
        if (!active) return;
        setData(body); setError(''); onStatusChange?.(body.status);
        if (['READY','RUNNING','CANCELLING'].includes(body.status)) timer = setTimeout(poll, 3000);
      } catch (e) { if (active) { setError(e.message); timer = setTimeout(poll, 5000); } }
    };
    poll(); return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [taskId, onStatusChange]);
  const cancel = () => Modal.confirm({ title: 'Stop this analysis?',
    content: 'The active calculation will be terminated. Fully refined candidate checkpoints remain downloadable; the interrupted candidate is not a valid result.',
    okText: 'Stop analysis', okButtonProps: { danger: true }, async onOk() {
      setBusy(true);
      try {
        const response = await fetch(`${API}/task/${taskId}/cancel`, { method: 'POST' });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail);
        setData(previous => ({...previous, status: body.status})); onStatusChange?.(body.status);
      } catch (e) { message.error(e.message); }
      finally { setBusy(false); }
    }});
  if (error) return <Alert type="warning" message={error} />;
  if (!data || data.status === 'COMPLETED') return null;
  return <section aria-label="Analysis progress" style={{ padding: 16, border: '1px solid #ddd', marginBottom: 16 }}>
    <Space wrap><Tag>{data.status}</Tag><strong>{data.stage}</strong>
      {['READY','RUNNING','CANCELLING'].includes(data.status) ? <Button danger loading={busy} disabled={data.status === 'CANCELLING'} onClick={cancel}>{data.status === 'CANCELLING' ? 'Stopping…' : 'Stop analysis'}</Button> : null}
      {data.completed_candidates > 0 ? <Button href={`${API}/task/${taskId}/checkpoints`}>Download completed candidates ({data.completed_candidates})</Button> : null}
    </Space>
    {data.total > 0 ? <><Progress percent={Math.round(100 * data.completed / data.total)} status={data.status === 'RUNNING' ? 'active' : 'normal'} /><p>{data.completed} / {data.total} candidate attempts finished (not overall progress).</p></> : <p style={{ marginTop: 8 }}>This stage has no reliable percentage estimate.</p>}
    {data.completed_candidates > 0 ? <p>Checkpoints contain completed refinements only, numbered by execution order; final ranking may change.</p> : null}
  </section>;
}

export function InstrumentManager({ onRegistered }) {
  const [open, setOpen] = React.useState(false);
  const [form] = Form.useForm();
  const [files, setFiles] = React.useState({});
  const [busy, setBusy] = React.useState(false);
  const submit = async () => {
    try {
      const values = await form.validateFields();
      if (!files.geq) throw new Error('Select a GEQ profile file.');
      const data = new FormData();
      Object.entries(values).forEach(([key,value]) => data.append(key, value ?? ''));
      Object.entries(files).forEach(([key,value]) => { if (value) data.append(key, value); });
      setBusy(true);
      const response = await fetch(`${API}/instrument-profiles`, {method:'POST',body:data});
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check required profile fields.');
      onRegistered(result); setOpen(false); form.resetFields(); setFiles({}); message.success('Profile registered and selected.');
    } catch (e) { if (e.message) message.error(e.message); }
    finally { setBusy(false); }
  };
  return <><Button size="small" onClick={() => setOpen(true)}>Register instrument profile</Button>
    <Modal title="Register a calibrated instrument profile" open={open} onCancel={() => !busy && setOpen(false)} onOk={submit} confirmLoading={busy} destroyOnClose>
      <Alert showIcon type="warning" message="Importing a file is not automatic calibration."
        description="Use a BGMN GEQ profile generated for the actual optics and detector. Supply its matching GER when available. An XRDML instrument name alone cannot verify the optics." style={{marginBottom:16}} />
      <Form form={form} layout="vertical" initialValues={{verified:false}}>
        <Form.Item name="label" label="Instrument / profile name" rules={[{required:true}]}><Input maxLength={120} /></Form.Item>
        <Form.Item name="geometry" label="Geometry, slits and detector" rules={[{required:true}]}><Input maxLength={250} /></Form.Item>
        <Form.Item name="radiation" label="Radiation (e.g. Cu Kα)" rules={[{required:true}]}><Input maxLength={60} /></Form.Item>
        <p><label>Compiled GEQ (required)<input aria-label="GEQ profile file" type="file" accept=".geq" onChange={e => setFiles({...files,geq:e.target.files[0]})} /></label></p>
        <p><label>Matching GER (optional)<input aria-label="GER profile file" type="file" accept=".ger" onChange={e => setFiles({...files,ger:e.target.files[0]})} /></label></p>
        <Form.Item name="verified" valuePropName="checked"><Checkbox>Verified against a standard by an operator</Checkbox></Form.Item>
        <Form.Item name="verified_by" label="Verified by"><Input maxLength={120} /></Form.Item>
        <Form.Item name="notes" label="Standard, calibration date, validated 2θ range and notes"><Input.TextArea rows={3} maxLength={2000} /></Form.Item>
      </Form>
    </Modal></>;
}
