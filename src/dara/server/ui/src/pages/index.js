import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Button, Form, Input, InputNumber, message, Modal, Radio, Select, Space, Switch, Upload } from 'antd';
import { MinusCircleOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { DEFAULT_CONTROLS, InstrumentManager, RefinementControlsEditor } from '../components/PracticalControls';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const { confirm } = Modal;
const { Dragger } = Upload;

const Container = styled.div`
  font-size: 1rem;
  display: flex;
  flex-direction: column;
  align-items: center;
  .center { text-align: center; }
`;

const INSTRUMENT_NAMES = [
  'synchrotron',
  'Aeris-fds-Pixcel1d-Medipix3',
  'LBL-d8-LynxEyeXE',
  'd8-solxe-fds-0600',
  'siemens-d5000-fds1mm',
  'RMS-D8-ADS-15-LynxEyeXE',
  'Rigaku-Miniflex-600-DTEXultra2-fds',
  'Rigaku-SmartLab-CBO-BB-FDS05deg',
  'PW3040-FDS-ADS-Xcelerator',
  'xpert-pixcel-0500',
];

const WAVELENGTHS = [
  ['Cu', 1.540598], ['Co', 1.789010], ['Cr', 2.289760], ['Fe', 1.936042],
];

const commaList = value => JSON.stringify(
  (value || '').split(',').map(item => item.trim()).filter(Boolean)
);
const uploadFile = value => value?.originFileObj || value;

function Submission() {
  const [form] = Form.useForm();
  const [analysisMode, setAnalysisMode] = useState('elemental');
  const [synchrotron, setSynchrotron] = useState(false);
  const [previousInstrument, setPreviousInstrument] = useState('Aeris-fds-Pixcel1d-Medipix3');
  const [instrumentNames, setInstrumentNames] = useState(INSTRUMENT_NAMES);
  const [instrumentDetails, setInstrumentDetails] = useState({});
  const [refinementControls, setRefinementControls] = useState(DEFAULT_CONTROLS);
  const selectedInstrument = Form.useWatch('instrument_profile', form);
  const [patternInspection, setPatternInspection] = useState(null);
  const [inspectingPattern, setInspectingPattern] = useState(false);
  const inspectionSequence = useRef(0);
  const [msg, setMsg] = useState(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [spinning, setSpinning] = useState(false);
  const [cloneSource, setCloneSource] = useState(null);
  const [loadingClone, setLoadingClone] = useState(false);

  useEffect(() => {
    fetch(`${URL}/instrument-profiles`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Profile list unavailable')))
      .then(data => {
        if (Array.isArray(data.profiles) && data.profiles.length) setInstrumentNames(data.profiles);
        setInstrumentDetails(data.details || {});
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const sourceId = new URLSearchParams(window.location.search).get('clone_task');
    if (!sourceId || !/^\d+$/.test(sourceId)) return;
    let active = true;
    setLoadingClone(true);
    fetch(`${URL}/task/${sourceId}/rerun-settings`)
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Saved input could not be loaded.');
        return data;
      })
      .then(data => {
        if (!active) return;
        const settings = data.settings;
        const isSync = typeof settings.wavelength === 'number';
        form.setFieldsValue({
          ...settings,
          elements: (settings.elements || []).join(', '),
          precursor_formulas: (settings.precursor_formulas || []).join(', '),
          synchrotron: isSync,
          wavelength_sync: isSync ? String(settings.wavelength) : undefined,
          wavelength_lab: isSync ? 'Cu' : settings.wavelength,
          excluded_ranges: settings.excluded_ranges || [],
          reuse_additional_cifs: true,
        });
        setAnalysisMode(settings.analysis_mode);
        setSynchrotron(isSync);
        setPreviousInstrument(settings.instrument_profile);
        setCloneSource(data);
      })
      .catch(error => { if (active) setMsg(String(error.message || error)); })
      .finally(() => { if (active) setLoadingClone(false); });
    return () => { active = false; };
  }, [form]);

  const inspectPattern = async info => {
    const fileValue = info?.fileList?.length ? info.file : null;
    const file = uploadFile(fileValue);
    form.setFieldValue('pattern_file', fileValue);
    const sequence = ++inspectionSequence.current;
    setPatternInspection(null);
    if (!file) {
      setInspectingPattern(false);
      return;
    }

    setInspectingPattern(true);
    const body = new FormData();
    body.append('pattern_file', file);
    try {
      const response = await fetch(`${URL}/pattern/inspect`, { method: 'POST', body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not inspect the pattern.');
      if (sequence !== inspectionSequence.current) return;

      const suggestion = data.profile_suggestion;
      const updates = {};
      if (suggestion?.profile) {
        updates.instrument_profile = suggestion.profile;
        setPreviousInstrument(suggestion.profile);
        setSynchrotron(false);
        updates.synchrotron = false;
      }
      if (data.recommended_wavelength && WAVELENGTHS.some(item => item[0] === data.recommended_wavelength)) {
        updates.wavelength_lab = data.recommended_wavelength;
      }
      form.setFieldsValue(updates);
      setPatternInspection(data);
    } catch (error) {
      if (sequence === inspectionSequence.current) {
        setPatternInspection({ error: String(error.message || error) });
      }
    } finally {
      if (sequence === inspectionSequence.current) setInspectingPattern(false);
    }
  };

  const onSubmit = async () => {
    let values;
    try {
      values = await form.validateFields();
    } catch (_) {
      return;
    }

    const formData = new FormData();
    formData.append('user', values.user);
    formData.append('analysis_mode', values.analysis_mode);
    formData.append('elements', commaList(values.elements));
    formData.append('precursor_formulas', commaList(values.precursor_formulas));
    formData.append('temperature', values.analysis_mode === 'reaction' ? values.temperature : -1);
    formData.append('atmosphere', values.atmosphere);
    formData.append('instrument_profile', values.instrument_profile);
    formData.append('refinement_controls', JSON.stringify(refinementControls));
    formData.append('wavelength', synchrotron ? Number(values.wavelength_sync) : values.wavelength_lab);
    formData.append('use_cod', values.use_cod);
    formData.append('use_mp', values.use_mp);
    formData.append('mp_e_hull_cutoff', values.mp_e_hull_cutoff);
    formData.append('max_mp_structures', values.max_mp_structures);
    if (values.pattern_file) formData.append('pattern_file', uploadFile(values.pattern_file));
    if (cloneSource) formData.append('source_task_id', cloneSource.source_task_id);
    formData.append('reuse_additional_cifs', values.reuse_additional_cifs !== false);
    if (values.two_theta_min != null) formData.append('two_theta_min', values.two_theta_min);
    if (values.two_theta_max != null) formData.append('two_theta_max', values.two_theta_max);
    formData.append('excluded_ranges', JSON.stringify(values.excluded_ranges || []));
    (values.additional_phases || []).forEach(file => formData.append('additional_phases', uploadFile(file)));

    const abortControl = new AbortController();
    setMsg(null);
    setSpinning(true);
    confirm({
      title: 'Do you want to submit the task?',
      onOk() {
        return fetch(`${URL}/submit`, { method: 'POST', body: formData, signal: abortControl.signal })
          .then(response => response.json())
          .then(data => {
            if (data.message === 'submitted') {
              form.resetFields();
              setCloneSource(null);
              setPatternInspection(null);
              setAnalysisMode('elemental');
              setSynchrotron(false);
              messageApi.open({ type: 'success', content: 'The refinement task was submitted.' });
            } else {
              setMsg(data.detail || JSON.stringify(data));
            }
          })
          .catch(error => { if (error.name !== 'AbortError') setMsg(String(error)); })
          .finally(() => setSpinning(false));
      },
      onCancel() { abortControl.abort(); setSpinning(false); },
    });
  };

  const modeMessage = analysisMode === 'reaction'
    ? 'Predict products from precursors, temperature, and atmosphere, then fit the XRD pattern.'
    : analysisMode === 'known_mixture'
      ? 'Only the phase formulas you provide are refined. Unknown impurities or reaction products will not be searched.'
      : 'Search plausible phases in the selected element system; expected formulas are prioritized.';

  return (
    <Layout hasSider={false} title="Submit">
      {contextHolder}
      <Container>
        {msg ? <Alert message={msg} type="error" showIcon closable style={{ width: '70%' }} /> : null}
        <h1>Dara practical phase analysis</h1>
        <Form
          labelCol={{ span: 9 }} wrapperCol={{ span: 15 }} style={{ width: '70%' }} form={form}
          initialValues={{
            analysis_mode: 'elemental', temperature: 800, atmosphere: 'air', use_cod: true,
            use_mp: true, mp_e_hull_cutoff: 0.05, max_mp_structures: 100,
            wavelength_lab: 'Cu', instrument_profile: 'Aeris-fds-Pixcel1d-Medipix3', synchrotron: false,
            excluded_ranges: [], reuse_additional_cifs: true,
          }}
          onValuesChange={(_, values) => setAnalysisMode(values.analysis_mode)}
        >
          <Form.Item label="User" name="user" rules={[{ required: true, message: 'Please input your username.' }]}>
            <Input placeholder="Username" />
          </Form.Item>
          {loadingClone ? <Alert type="info" showIcon message="Loading saved pattern and conditions..." style={{ marginBottom: 20 }} /> : null}
          {cloneSource ? (
            <Alert type="info" showIcon style={{ marginBottom: 20 }}
              message={`Copied from Task ${cloneSource.source_task_id}: ${cloneSource.filename}`}
              description={`The original pattern (${cloneSource.pattern.data_points.toLocaleString()} points) will be reused unless you upload a replacement. Edit any conditions below; the source task is unchanged.`}
            />
          ) : null}
          <Form.Item label={cloneSource ? 'Replacement pattern (optional)' : 'XRD pattern'} name="pattern_file" rules={[{ required: !cloneSource, message: 'Please upload an XRD pattern.' }]}
            extra="XRDML is recommended: its acquisition metadata can select the closest instrument profile and wavelength automatically.">
            <Dragger maxCount={1} beforeUpload={() => false} onChange={inspectPattern}
              accept=".xy,.xye,.xrdml,.raw,.rasx,.txt,.scn">
              <p className="ant-upload-drag-icon"><UploadOutlined /></p>
              <p className="ant-upload-text">Start by selecting the measured XRD file</p>
              <p className="ant-upload-hint">xrdml (metadata-aware) / xy(e) / raw / rasx / txt</p>
            </Dragger>
          </Form.Item>
          {inspectingPattern ? (
            <Alert type="info" showIcon message="Reading pattern and acquisition metadata..." style={{ marginBottom: 20 }} />
          ) : patternInspection?.error ? (
            <Alert type="error" showIcon message="Pattern could not be read" description={patternInspection.error} style={{ marginBottom: 20 }} />
          ) : patternInspection ? (
            <Alert
              type={patternInspection.profile_suggestion ? 'success' : 'warning'}
              showIcon
              message={patternInspection.profile_suggestion
                ? `Instrument settings applied: ${patternInspection.profile_suggestion.profile}`
                : 'Pattern loaded; select the instrument profile manually'}
              description={[
                patternInspection.metadata?.instrument_system || patternInspection.metadata?.instrument_control_software,
                patternInspection.metadata?.detector,
                patternInspection.recommended_wavelength ? `${patternInspection.recommended_wavelength} radiation` : null,
                Number.isFinite(patternInspection.two_theta_min) && Number.isFinite(patternInspection.two_theta_max)
                  ? `2θ ${patternInspection.two_theta_min.toFixed(2)}–${patternInspection.two_theta_max.toFixed(2)}°`
                  : null,
                patternInspection.data_points ? `${patternInspection.data_points.toLocaleString()} points` : null,
                patternInspection.profile_suggestion?.reason,
                patternInspection.profile_suggestion ? 'Confirm the selection if the actual slit or optical setup was different.' : null,
              ].filter(Boolean).join(' · ')}
              style={{ marginBottom: 20 }}
            />
          ) : null}
          <Form.Item label="Analysis mode" name="analysis_mode">
            <Radio.Group buttonStyle="solid">
              <Radio.Button value="elemental">Phase identification</Radio.Button>
              <Radio.Button value="known_mixture">Known-phase refinement</Radio.Button>
              <Radio.Button value="reaction">Reaction check</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Alert type={analysisMode === 'known_mixture' ? 'warning' : 'info'} showIcon message={modeMessage} style={{ marginBottom: 20 }} />
          <Form.Item label="Included elements" name="elements"
            rules={[{ required: analysisMode === 'elemental', message: 'Please input element symbols.' }]}
            tooltip="Example: In, La, Ti, O. In other modes they can be derived from formulas.">
            <Input placeholder="In, La, Ti, O" />
          </Form.Item>
          <Form.Item label={analysisMode === 'known_mixture' ? 'Phases to refine' : 'Precursor / expected formulas'}
            name="precursor_formulas" rules={[{ required: analysisMode !== 'elemental', message: 'Please input chemical formulas.' }]}
            tooltip="In elemental mode this is optional, but listed phases are guaranteed candidates.">
            <Input placeholder="In2O3, La(OH)3, TiO2" />
          </Form.Item>
          <Form.Item label="Temperature (°C)" name="temperature" hidden={analysisMode !== 'reaction'}
            rules={[{ required: analysisMode === 'reaction' }]}>
            <InputNumber min={27} max={1727} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="Atmosphere" name="atmosphere" hidden={analysisMode !== 'reaction'}>
            <Select options={[
              { value: 'air', label: 'Air (open O₂, pO₂ = 0.21 atm)' },
              { value: 'oxygen', label: 'Oxygen (open O₂, standard state)' },
              { value: 'inert', label: 'Inert / closed system' },
              { value: 'vacuum', label: 'Vacuum / closed system' },
              { value: 'reducing', label: 'Reducing / closed system' },
            ]} />
          </Form.Item>
          <Form.Item label="Reference databases">
            <Form.Item name="use_cod" valuePropName="checked" noStyle>
              <Switch checkedChildren="COD" unCheckedChildren="COD" />
            </Form.Item>
            <span style={{ margin: '0 14px 0 8px' }}>experimental</span>
            <Form.Item name="use_mp" valuePropName="checked" noStyle>
              <Switch checkedChildren="MP" unCheckedChildren="MP" />
            </Form.Item>
            <span style={{ marginLeft: 8 }}>computed supplement</span>
          </Form.Item>
          <Form.Item label="MP E-hull cutoff" name="mp_e_hull_cutoff" tooltip="eV/atom; lower values retain more plausible MP structures.">
            <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="Maximum MP structures" name="max_mp_structures">
            <InputNumber min={1} max={1000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="Instrument profile" name="instrument_profile"
            tooltip="For XRDML, DARA applies the closest bundled profile from the recorded instrument and detector. Always confirm unusual slit or optic setups.">
            <Select showSearch optionFilterProp="label" options={instrumentNames.map(name => ({ label: instrumentDetails[name] ? `${instrumentDetails[name].label} — ${instrumentDetails[name].verified ? 'operator verified' : 'unverified'}` : name, value: name }))} />
          </Form.Item>
          <div style={{marginBottom:16}}>
            <InstrumentManager onRegistered={profile => {
              setInstrumentNames(previous => [...previous, profile.id]);
              setInstrumentDetails(previous => ({...previous,[profile.id]:profile}));
              form.setFieldsValue({instrument_profile:profile.id});
            }} />
            {instrumentDetails[selectedInstrument] ? <Alert style={{marginTop:10}} type={instrumentDetails[selectedInstrument].verified ? 'info' : 'warning'} showIcon
              message={`${instrumentDetails[selectedInstrument].label} — ${instrumentDetails[selectedInstrument].verified ? 'verification declared by ' + instrumentDetails[selectedInstrument].verified_by : 'not verified against a standard'}`}
              description={<>{instrumentDetails[selectedInstrument].geometry} · {instrumentDetails[selectedInstrument].radiation}<br />{instrumentDetails[selectedInstrument].notes}</>} /> : null}
          </div>
          <RefinementControlsEditor value={refinementControls} onChange={setRefinementControls} />
          <Form.Item label="Synchrotron" name="synchrotron" valuePropName="checked">
            <Switch onChange={checked => {
              setSynchrotron(checked);
              if (checked) {
                setPreviousInstrument(form.getFieldValue('instrument_profile'));
                form.setFieldsValue({ instrument_profile: 'synchrotron' });
              } else {
                form.setFieldsValue({ instrument_profile: previousInstrument });
              }
            }} />
          </Form.Item>
          {!synchrotron ? (
            <Form.Item label="Wavelength" name="wavelength_lab" rules={[{ required: true }]}>
              <Select options={WAVELENGTHS.map(item => ({ value: item[0], label: `${item[0]} (${item[1].toFixed(4)} Å)` }))} />
            </Form.Item>
          ) : (
            <Form.Item label="Wavelength (nm)" name="wavelength_sync" rules={[{ required: true, pattern: /^\d+(\.\d+)?$/ }]}>
              <Input />
            </Form.Item>
          )}
          <Form.Item label="Analysis range · 2θ (°)" extra="Leave blank to use the measured range. Explicit limits apply to search and final refinement alike.">
            <Space wrap>
              <Form.Item name="two_theta_min" noStyle><InputNumber min={0} max={180} placeholder="Start" aria-label="Analysis start angle" /></Form.Item>
              <span>to</span>
              <Form.Item name="two_theta_max" noStyle><InputNumber min={0} max={180} placeholder="End" aria-label="Analysis end angle" /></Form.Item>
            </Space>
          </Form.Item>
          <Form.Item label="Excluded intervals · 2θ (°)" extra="Exclude confirmed holder/artifact regions only. These intervals are omitted from fitting and peak diagnostics; measured intensities are preserved.">
            <Form.List name="excluded_ranges">
              {(fields, { add, remove }) => (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {fields.map(field => (
                    <Space key={field.key} wrap>
                      <Form.Item name={[field.name, 0]} rules={[{ required: true, message: 'Start angle required' }]} style={{ marginBottom: 0 }}>
                        <InputNumber min={0} max={180} placeholder="Start" aria-label="Excluded interval start" />
                      </Form.Item>
                      <span>to</span>
                      <Form.Item name={[field.name, 1]} rules={[{ required: true, message: 'End angle required' }]} style={{ marginBottom: 0 }}>
                        <InputNumber min={0} max={180} placeholder="End" aria-label="Excluded interval end" />
                      </Form.Item>
                      <Button type="text" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} aria-label="Remove excluded interval" />
                    </Space>
                  ))}
                  <Button type="dashed" icon={<PlusOutlined />} onClick={() => add([null, null])}>Add excluded interval</Button>
                </Space>
              )}
            </Form.List>
          </Form.Item>
          {cloneSource?.additional_cif_count > 0 ? (
            <Form.Item label={`Reuse ${cloneSource.additional_cif_count} saved reference CIFs`} name="reuse_additional_cifs" valuePropName="checked">
              <Switch />
            </Form.Item>
          ) : null}
          <Form.Item label="Additional reference CIFs" name="additional_phases" valuePropName="fileList" getValueFromEvent={event => event?.fileList}>
            <Upload beforeUpload={() => false} multiple accept=".cif">
              <Button icon={<UploadOutlined />}>Select CIF files</Button>
            </Upload>
          </Form.Item>
          <Form.Item className="center" wrapperCol={{ span: 24 }}>
            <Button type="primary" size="large" onClick={onSubmit} disabled={spinning || loadingClone} loading={spinning}>Submit</Button>
          </Form.Item>
        </Form>
      </Container>
    </Layout>
  );
}

export default Submission;
