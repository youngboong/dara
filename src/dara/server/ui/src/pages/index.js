import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Alert, Button, Form, Input, InputNumber, message, Modal, Segmented, Select, Space, Switch, Table, Tag, Typography, Upload } from 'antd';
import { MinusCircleOutlined, PlusOutlined, RightOutlined, UploadOutlined } from '@ant-design/icons';
import { DEFAULT_CONTROLS, InstrumentManager, RefinementControlsEditor } from '../components/PracticalControls';
import { detailMessage, qualityAlertItems, qualityStatText, wavelengthMismatch } from '../components/dataQuality.mjs';
import { MpKeyStatus, ReferenceLibraryField, useReferenceLibrary } from '../components/ReferenceLibrary';
import { MAX_SERIES_FILES, seriesPreviewRows } from '../components/seriesTrends.mjs';
import { advancedChanges, forgetRemembered, readRemembered, runAfterSuccess, writeRemembered } from '../components/submitHelpers.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const { confirm } = Modal;
const { Dragger } = Upload;

// Layout: cards with labels above the inputs. From WIDE_LAYOUT px the form splits into a main
// column (sample, chemistry, instrument) and a sticky side column (run card, advanced options);
// below it the cards stack in the order sample, chemistry, instrument, advanced, run.
const PAGE_MAX_WIDTH = 1440;
const SINGLE_COLUMN_MAX_WIDTH = 960;
const WIDE_LAYOUT = 1200;

const Container = styled.div`
  font-size: 1rem;
  width: 100%;
  max-width: ${SINGLE_COLUMN_MAX_WIDTH}px;
  margin: 0 auto;
  .dara-submit-head { margin: 0 0 16px; }
  .dara-submit-head h1 { font-size: 1.5rem; font-weight: 600; margin: 0; }
  .dara-submit-head p { margin: 0; color: rgba(0, 0, 0, 0.55); }
  .dara-submit-layout { display: flex; flex-direction: column; gap: 16px; margin-bottom: 24px; }
  .dara-submit-main, .dara-submit-side { display: contents; }
  .dara-card-sample { order: 1; }
  .dara-card-chemistry { order: 2; }
  .dara-card-instrument { order: 3; }
  .dara-card-advanced { order: 4; }
  .dara-card-run { order: 5; }
  .dara-card {
    min-width: 0;
    background: #fff;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    padding: 16px 24px 0;
  }
  .dara-card-title {
    margin: 0 0 16px;
    font-size: 0.8125rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: rgba(0, 0, 0, 0.55);
  }
  .dara-subhead { font-weight: 500; margin-bottom: 8px; }
  .ant-form-item { margin-bottom: 16px; }
  .ant-form-vertical .ant-form-item-label { padding-bottom: 4px; }
  .ant-form-item-extra { font-size: 0.8125rem; }
  /* Notes inside a card fill the card instead of shrinking to their text. */
  .ant-alert, .dara-note-group { width: auto; max-width: none; }
  .dara-card .ant-alert { margin-bottom: 16px; }
  .dara-note-group > .ant-alert { margin-bottom: 4px; padding: 4px 12px; }
  .dara-field-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr));
    gap: 0 16px;
  }
  .dara-field-row.wide-cells { grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); }
  .dara-field-row > label { display: block; margin-bottom: 16px; }
  .dara-inline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .dara-inline > .grow { flex: 1 1 240px; min-width: 0; }
  .dara-run-summary { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 16px; margin: 0 0 16px; }
  .dara-run-summary dt { color: rgba(0, 0, 0, 0.55); }
  .dara-run-summary dd { margin: 0; overflow-wrap: anywhere; }
  .dara-run-summary .missing { color: rgba(0, 0, 0, 0.35); }
  .dara-run-hint { margin: 0 0 16px; color: rgba(0, 0, 0, 0.45); font-size: 0.8125rem; }
  .dara-advanced-toggle {
    display: flex; align-items: center; gap: 8px; width: 100%;
    margin: 0; padding: 0 0 16px; border: 0; background: none; cursor: pointer; text-align: left;
  }
  .dara-advanced-toggle .anticon { transition: transform 0.2s; color: rgba(0, 0, 0, 0.45); font-size: 0.75rem; }
  .dara-advanced-toggle[aria-expanded='true'] .anticon { transform: rotate(90deg); }
  .dara-advanced-toggle .dara-card-title { margin: 0; }
  .dara-advanced-toggle .note { margin-left: auto; color: rgba(0, 0, 0, 0.45); font-size: 0.8125rem; }
  .dara-advanced-body[hidden] { display: none; }
  @media (min-width: ${WIDE_LAYOUT}px) {
    max-width: ${PAGE_MAX_WIDTH}px;
    .dara-submit-layout {
      display: grid;
      grid-template-columns: minmax(0, 3fr) minmax(380px, 2fr);
      gap: 24px;
      align-items: start;
    }
    .dara-submit-main, .dara-submit-side { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    /* The run card stays in view while the main column scrolls; a side column taller than the
       window scrolls with the page (no nested scroll area). */
    .dara-submit-side { position: sticky; top: 16px; }
    .dara-card-run { order: 0; }
  }
`;

const MODE_LABELS = { elemental: 'Phase identification', known_mixture: 'Known-phase refinement', reaction: 'Reaction check' };

function Card({ name, title, children }) {
  return <section className={`dara-card dara-card-${name}`} aria-label={title}>
    <h2 className="dara-card-title">{title}</h2>
    {children}
  </section>;
}

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
const splitList = value => (value || '').split(',').map(item => item.trim()).filter(Boolean);

// 'm min' below one hour, 'h:mm h' from one hour on.
const formatDuration = seconds => {
  if (!Number.isFinite(seconds)) return '';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')} h`;
};
const ESTIMATE_ALERT_TYPE = { ok: 'info', long: 'warning', very_long: 'error', unknown: 'info' };

function EstimateAlert({ estimate, loading, prefilter }) {
  if (!estimate) return loading ? <Alert type="info" showIcon message="Estimating candidates and run time..." style={{ marginBottom: 16 }} /> : null;
  const { candidates = {}, runtime_s: runtime, workers, suggestions = [], level } = estimate;
  let message = null;
  if (candidates.expected_after_dedup != null) {
    const parts = [];
    if (candidates.cod != null) parts.push(`${candidates.cod} COD`);
    if (candidates.mp != null) parts.push(`${candidates.mp} MP`);
    message = `Estimated candidates: about ${candidates.expected_after_dedup}`
      + (parts.length ? ` (${parts.join(' + ')}${candidates.mp_capped ? ', MP limit reached' : ''})` : '')
      + (prefilter && candidates.prefilter_expected_after != null ? `, about ${candidates.prefilter_expected_after} after pre-filter` : '');
  } else {
    message = candidates.note || 'Estimated candidates unavailable';
  }
  return (
    <Alert
      style={{ marginBottom: 16, opacity: loading ? 0.6 : 1 }}
      showIcon
      type={ESTIMATE_ALERT_TYPE[level] || 'info'}
      message={message}
      description={(
        <div>
          {candidates.expected_after_dedup != null && candidates.note ? <div>{candidates.note}</div> : null}
          {runtime ? (
            <div>Estimated run time {formatDuration(runtime.low)}–{formatDuration(runtime.high)} (typically {formatDuration(runtime.expected)}) on {workers} workers.</div>
          ) : null}
          {suggestions.length ? <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>{suggestions.map(item => <li key={item}>{item}</li>)}</ul> : null}
        </div>
      )}
    />
  );
}

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
  const autoElements = useRef(null);
  const [msg, setMsg] = useState(null);
  const [spinning, setSpinning] = useState(false);
  const [cloneSource, setCloneSource] = useState(null);
  const [usingRemembered, setUsingRemembered] = useState(false);
  const browserStorage = () => (typeof window === 'undefined' ? null : window.localStorage);
  // Settings from the last submission in this browser (user, modes, databases, limits, instrument).
  const applyRemembered = saved => {
    if (!saved) return false;
    form.setFieldsValue(saved.fields);
    setAnalysisMode(saved.fields.analysis_mode ?? 'elemental');
    setSynchrotron(Boolean(saved.fields.synchrotron));
    if (saved.controls) setRefinementControls(saved.controls);
    setUsingRemembered(true);
    return true;
  };
  const resetToDefaults = () => {
    forgetRemembered(browserStorage());
    form.resetFields();
    setAnalysisMode('elemental');
    setSynchrotron(false);
    setRefinementControls(DEFAULT_CONTROLS);
    setUsingRemembered(false);
  };
  const [loadingClone, setLoadingClone] = useState(false);
  const [seriesMode, setSeriesMode] = useState(false);
  const [seriesPreview, setSeriesPreview] = useState(null);
  const seriesFiles = Form.useWatch('series_files', form);
  const firstSeriesFile = useRef(null);
  const library = useReferenceLibrary();
  const [estimate, setEstimate] = useState(null);
  const [estimating, setEstimating] = useState(false);
  const watchedMode = Form.useWatch('analysis_mode', form);
  const watchedElements = Form.useWatch('elements', form);
  const watchedPrecursors = Form.useWatch('precursor_formulas', form);
  const watchedUseCod = Form.useWatch('use_cod', form);
  const watchedUseMp = Form.useWatch('use_mp', form);
  const watchedEHull = Form.useWatch('mp_e_hull_cutoff', form);
  const watchedMaxMp = Form.useWatch('max_mp_structures', form);
  const watchedPrefilter = Form.useWatch('candidate_prefilter', form);
  const watchedMaxMinutes = Form.useWatch('max_analysis_minutes', form);
  const watchedWavelengthLab = Form.useWatch('wavelength_lab', form);
  const watchedWavelengthSync = Form.useWatch('wavelength_sync', form);
  const watchedThetaMax = Form.useWatch('two_theta_max', form);
  const watchedPattern = Form.useWatch('pattern_file', form);
  const watchedLibraryCifs = Form.useWatch('library_cif_ids', form);
  const watchedAdditional = Form.useWatch('additional_phases', form);
  const watchedReuseCifs = Form.useWatch('reuse_additional_cifs', form);
  const watchedThetaMin = Form.useWatch('two_theta_min', form);
  const watchedScreening = Form.useWatch('screening_timeout_s', form);

  // Advanced options are collapsed unless one of them differs from its default (remembered
  // settings, a cloned task, or a validation error there); the card never closes by itself.
  const changedAdvanced = advancedChanges({
    use_cod: watchedUseCod, use_mp: watchedUseMp, mp_e_hull_cutoff: watchedEHull, max_mp_structures: watchedMaxMp,
    candidate_prefilter: watchedPrefilter, screening_timeout_s: watchedScreening,
    max_analysis_minutes: watchedMaxMinutes, library_cif_ids: watchedLibraryCifs, additional_phases: watchedAdditional,
    reuse_additional_cifs: watchedReuseCifs,
  }, refinementControls, DEFAULT_CONTROLS);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const hasAdvancedChanges = changedAdvanced.length > 0;
  useEffect(() => { if (hasAdvancedChanges) setAdvancedOpen(true); }, [hasAdvancedChanges]);

  useEffect(() => {
    fetch(`${URL}/instrument-profiles`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Profile list unavailable')))
      .then(data => {
        if (Array.isArray(data.profiles) && data.profiles.length) setInstrumentNames(data.profiles);
        setInstrumentDetails(data.details || {});
      })
      .catch(() => {});
  }, []);

  // Candidate/run-time estimate: debounced, and the previous request is aborted.
  const inspectedThetaMax = patternInspection?.two_theta_max;
  const inspectedPoints = patternInspection?.data_points;
  useEffect(() => {
    const mode = watchedMode || 'elemental';
    const elementList = splitList(watchedElements);
    const precursorList = splitList(watchedPrecursors);
    if ((mode === 'elemental' && !elementList.length && !precursorList.length) || (mode !== 'elemental' && !precursorList.length)) {
      setEstimate(null);
      setEstimating(false);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setEstimating(true);
      const thetaMax = Number.isFinite(watchedThetaMax) ? watchedThetaMax : inspectedThetaMax;
      fetch(`${URL}/submission/estimate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({
          analysis_mode: mode,
          elements: elementList,
          precursor_formulas: precursorList,
          use_cod: watchedUseCod !== false,
          use_mp: watchedUseMp !== false,
          mp_e_hull_cutoff: watchedEHull ?? 0.05,
          max_mp_structures: watchedMaxMp ?? 100,
          candidate_prefilter: watchedPrefilter !== false,
          wavelength: synchrotron ? (String(watchedWavelengthSync || '') || 'Cu') : (watchedWavelengthLab || 'Cu'),
          two_theta_max: Number.isFinite(thetaMax) ? thetaMax : null,
          data_points: Number.isFinite(inspectedPoints) ? inspectedPoints : null,
          max_analysis_minutes: watchedMaxMinutes ?? null,
        }),
      })
        .then(response => response.ok ? response.json() : Promise.reject(new Error('Estimate unavailable')))
        .then(data => { setEstimate(data); setEstimating(false); })
        .catch(error => { if (error.name !== 'AbortError') { setEstimate(null); setEstimating(false); } });
    }, 600);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [watchedMode, watchedElements, watchedPrecursors, watchedUseCod, watchedUseMp, watchedEHull, watchedMaxMp,
    watchedPrefilter, watchedMaxMinutes, watchedWavelengthLab, watchedWavelengthSync, watchedThetaMax,
    synchrotron, inspectedThetaMax, inspectedPoints]);

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('clone_task')) return;
    applyRemembered(readRemembered(browserStorage()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
          candidate_prefilter: settings.candidate_prefilter === true,
          screening_timeout_s: settings.screening_timeout_s ?? null,
          max_analysis_minutes: settings.max_analysis_minutes ?? null,
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

  useEffect(() => {
    const names = (seriesFiles || []).map(item => item.name);
    if (!seriesMode || names.length < 2) {
      setSeriesPreview(null);
      return undefined;
    }
    const controller = new AbortController();
    fetch(`${URL}/series/preview`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames: names }), signal: controller.signal,
    })
      .then(response => response.ok ? response.json() : Promise.reject(new Error('Preview unavailable')))
      .then(setSeriesPreview)
      .catch(error => { if (error.name !== 'AbortError') setSeriesPreview({ error: String(error.message || error) }); });
    return () => controller.abort();
  }, [seriesMode, seriesFiles]);

  const onSeriesFilesChange = info => {
    // Only the first file is inspected; re-inspect when a different file becomes first.
    const first = info?.fileList?.[0];
    const key = first ? `${first.uid}:${first.name}` : null;
    if (key === firstSeriesFile.current) return;
    firstSeriesFile.current = key;
    inspectFile(first ? uploadFile(first) : null);
  };

  const toggleSeriesMode = checked => {
    setSeriesMode(checked);
    firstSeriesFile.current = null;
    form.setFieldsValue({ pattern_file: undefined, series_files: [] });
    setSeriesPreview(null);
    inspectFile(null);
  };

  const inspectPattern = info => {
    const fileValue = info?.fileList?.length ? info.file : null;
    form.setFieldValue('pattern_file', fileValue);
    return inspectFile(uploadFile(fileValue));
  };

  const inspectFile = async file => {
    const sequence = ++inspectionSequence.current;
    if (autoElements.current !== null && form.getFieldValue('elements') === autoElements.current) {
      form.setFieldValue('elements', '');
    }
    autoElements.current = null;
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
      const inferredElements = data.filename_chemistry?.elements || [];
      const applyElements = inferredElements.length > 0 && !(form.getFieldValue('elements') || '').trim();
      if (applyElements) {
        updates.elements = inferredElements.join(', ');
        autoElements.current = updates.elements;
      }
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
      setPatternInspection({ ...data, elementsAutoApplied: applyElements });
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
    } catch (error) {
      const advancedNames = ['use_cod', 'use_mp', 'mp_e_hull_cutoff', 'max_mp_structures', 'candidate_prefilter',
        'screening_timeout_s', 'max_analysis_minutes', 'library_cif_ids', 'additional_phases', 'reuse_additional_cifs'];
      if ((error?.errorFields || []).some(item => advancedNames.includes(item.name?.[0]))) setAdvancedOpen(true);
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
    formData.append('library_cif_ids', JSON.stringify(values.library_cif_ids || []));
    formData.append('candidate_prefilter', String(values.candidate_prefilter !== false));
    if (values.screening_timeout_s != null) formData.append('screening_timeout_s', values.screening_timeout_s);
    if (values.max_analysis_minutes != null) formData.append('max_analysis_minutes', values.max_analysis_minutes);
    if (seriesMode) {
      formData.append('series_name', (values.series_name || '').trim());
      (values.series_files || []).forEach(file => formData.append('pattern_files', uploadFile(file)));
    } else {
      if (values.pattern_file) formData.append('pattern_file', uploadFile(values.pattern_file));
      if (cloneSource) formData.append('source_task_id', cloneSource.source_task_id);
      formData.append('reuse_additional_cifs', values.reuse_additional_cifs !== false);
    }
    if (values.two_theta_min != null) formData.append('two_theta_min', values.two_theta_min);
    if (values.two_theta_max != null) formData.append('two_theta_max', values.two_theta_max);
    formData.append('excluded_ranges', JSON.stringify(values.excluded_ranges || []));
    (values.additional_phases || []).forEach(file => formData.append('additional_phases', uploadFile(file)));

    const abortControl = new AbortController();
    setMsg(null);
    setSpinning(true);
    const send = confirmedWavelength => {
      if (confirmedWavelength) formData.set('confirm_wavelength', 'true');
      return fetch(`${URL}${seriesMode ? '/submit-series' : '/submit'}`, { method: 'POST', body: formData, signal: abortControl.signal })
        .then(response => response.json())
        .then(data => {
          // Once the server confirms the submission, UI follow-up failures must never be reported as a failed submission.
          if (data.message === 'submitted' && data.series_id) {
            const seriesUrl = `/series/?id=${encodeURIComponent(data.series_id)}`;
            runAfterSuccess([
              () => writeRemembered(browserStorage(), form.getFieldsValue(true), refinementControls),
              () => message.success(`The series was submitted (${(data.task_ids || []).length} tasks).`),
              () => { window.location.href = seriesUrl; },
            ]);
            return false;
          }
          if (data.message === 'submitted') {
            const remembered = { fields: form.getFieldsValue(true), controls: refinementControls };
            runAfterSuccess([
              () => writeRemembered(browserStorage(), remembered.fields, remembered.controls),
              () => form.resetFields(),
              () => { autoElements.current = null; },
              () => setCloneSource(null),
              () => setPatternInspection(null),
              () => setAnalysisMode('elemental'),
              () => setSynchrotron(false),
              () => applyRemembered(readRemembered(browserStorage())),
              () => message.success('The refinement task was submitted.'),
            ]);
            return false;
          }
          const mismatch = wavelengthMismatch(data.detail);
          if (mismatch && !confirmedWavelength) {
            // The recorded and selected wavelengths differ: ask, never silently change either.
            Modal.confirm({
              title: 'The selected wavelength differs from the file',
              content: `${mismatch.filename ? `${mismatch.filename}: ` : ''}${mismatch.message} Submit with the selected wavelength anyway?`,
              okText: 'Submit anyway',
              cancelText: 'Go back and check',
              onOk() {
                setSpinning(true);
                return send(true)
                  .catch(error => { if (error.name !== 'AbortError') setMsg(String(error)); })
                  .finally(() => setSpinning(false));
              },
            });
            return true;
          }
          setMsg(detailMessage(data.detail, JSON.stringify(data)));
          return false;
        });
    };
    confirm({
      title: seriesMode ? `Submit ${(values.series_files || []).length} patterns as one series?` : 'Do you want to submit the task?',
      onOk() {
        return send(false)
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

  const instrumentLabel = name => (instrumentDetails[name]
    ? `${instrumentDetails[name].label} — ${instrumentDetails[name].verified ? 'operator verified' : 'unverified'}`
    : name);
  const missing = text => <span className="missing">{text}</span>;
  const seriesCount = (seriesFiles || []).length;
  const patternSummary = seriesMode
    ? (seriesCount ? `${seriesCount} files (series)` : missing('No files selected'))
    : watchedPattern?.name
      || (cloneSource ? `${cloneSource.filename} (from Task ${cloneSource.source_task_id})` : missing('No file selected'));
  const elementSummary = splitList(watchedElements).join(', ');
  const formulaSummary = splitList(watchedPrecursors).join(', ');
  const wavelengthSummary = synchrotron
    ? (watchedWavelengthSync ? `${watchedWavelengthSync} nm (synchrotron)` : missing('wavelength not set'))
    : (watchedWavelengthLab || 'Cu');
  const rangeSummary = watchedThetaMin != null || watchedThetaMax != null
    ? `2θ ${watchedThetaMin ?? 'start'}–${watchedThetaMax ?? 'end'}°`
    : 'measured 2θ range';
  const selected = instrumentDetails[selectedInstrument];

  return (
    <Layout hasSider={false} title="Submit">
      <Container>
        <div className="dara-submit-head">
          <h1>DARA practical phase analysis</h1>
        </div>
        <Form className="dara-submit-layout" layout="vertical" form={form}
          initialValues={{
            analysis_mode: 'elemental', temperature: 800, atmosphere: 'air', use_cod: true,
            use_mp: true, mp_e_hull_cutoff: 0.05, max_mp_structures: 100,
            wavelength_lab: 'Cu', instrument_profile: 'Aeris-fds-Pixcel1d-Medipix3', synchrotron: false,
            excluded_ranges: [], reuse_additional_cifs: true,
            candidate_prefilter: true, screening_timeout_s: 300, max_analysis_minutes: null,
          }}
          onValuesChange={(changed, values) => {
            setAnalysisMode(values.analysis_mode);
            if (Object.prototype.hasOwnProperty.call(changed, 'elements')) autoElements.current = null;
          }}
        >
          <div className="dara-submit-main">
            <Card name="sample" title="Sample & pattern">
              <div className="dara-field-row">
                <Form.Item label="User" name="user" rules={[{ required: true, message: 'Please input your username.' }]}
                  extra={usingRemembered && !cloneSource ? <span>
                    Filled in from your last submission in this browser.{' '}
                    <a onClick={resetToDefaults}>Reset to defaults</a>
                  </span> : null}>
                  <Input placeholder="Username" />
                </Form.Item>
                {!cloneSource && !loadingClone ? (
                  <Form.Item label="Submit as a series" tooltip="Analyse several patterns (for example a temperature or time series) with identical settings.">
                    <Switch checked={seriesMode} onChange={toggleSeriesMode} aria-label="Submit as a series" />
                  </Form.Item>
                ) : null}
              </div>
              {loadingClone ? <Alert type="info" showIcon message="Loading saved pattern and conditions..." /> : null}
              {cloneSource ? (
                <Alert type="info" showIcon
                  message={`Copied from Task ${cloneSource.source_task_id}: ${cloneSource.filename}`}
                  description={`The original pattern (${cloneSource.pattern.data_points.toLocaleString()} points) is reused unless you upload a replacement. Edit any conditions; the source task is unchanged.`}
                />
              ) : null}
              {seriesMode ? (
                <>
                  <Form.Item label="Series name" name="series_name" rules={[{ required: true, whitespace: true, message: 'Please enter a series name.' }, { max: 120 }]}>
                    <Input placeholder="e.g. ZnO anneal series" maxLength={120} />
                  </Form.Item>
                  <Form.Item label="XRD patterns" name="series_files" valuePropName="fileList" getValueFromEvent={event => event?.fileList}
                    rules={[{ required: true, type: 'array', min: 2, message: 'Please add at least two XRD patterns.' }]}
                    tooltip="XRDML is recommended: its acquisition metadata can select the closest instrument profile and wavelength automatically.">
                    <Dragger multiple maxCount={MAX_SERIES_FILES} beforeUpload={() => false} onChange={onSeriesFilesChange}
                      accept=".xy,.xye,.xrdml,.raw,.rasx,.txt,.scn,.ras,.brml,.csv,.dat,.asc">
                      <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                      <p className="ant-upload-text">Select all measured XRD files of the series</p>
                      <p className="ant-upload-hint">2 to {MAX_SERIES_FILES} files with the same format and acquisition settings</p>
                    </Dragger>
                  </Form.Item>
                  {seriesPreview?.error ? <Alert type="warning" showIcon message="Series order preview unavailable" description={seriesPreview.error} /> : null}
                  {seriesPreview?.items?.length ? (
                    <div style={{ marginBottom: 16 }}>
                      <div className="dara-subhead">Series order preview</div>
                      <Table size="small" pagination={false} rowKey="filename" dataSource={seriesPreviewRows(seriesPreview)}
                        columns={[
                          { title: 'Order', dataIndex: 'order', width: 80 },
                          { title: 'File', dataIndex: 'filename' },
                          { title: seriesPreview.unit ? `Variable (${seriesPreview.unit})` : 'Variable', dataIndex: 'variableText' },
                        ]} />
                      <Typography.Text type="secondary">
                        Files are analysed independently with identical settings; the variable is parsed from the file names for plotting only.
                      </Typography.Text>
                    </div>
                  ) : null}
                </>
              ) : (
                <Form.Item label={cloneSource ? 'Replacement pattern (optional)' : 'XRD pattern'} name="pattern_file" rules={[{ required: !cloneSource, message: 'Please upload an XRD pattern.' }]}
                  tooltip="XRDML is recommended: its acquisition metadata can select the closest instrument profile and wavelength automatically.">
                  <Dragger maxCount={1} beforeUpload={() => false} onChange={inspectPattern}
                    accept=".xy,.xye,.xrdml,.raw,.rasx,.txt,.scn,.ras,.brml,.csv,.dat,.asc">
                    <p className="ant-upload-drag-icon"><UploadOutlined /></p>
                    <p className="ant-upload-text">Start by selecting the measured XRD file</p>
                    <p className="ant-upload-hint">xrdml (metadata-aware) / xy(e) / txt / csv / dat / asc / ras / rasx / brml / raw</p>
                  </Dragger>
                </Form.Item>
              )}
              {inspectingPattern ? (
                <Alert type="info" showIcon message="Reading pattern and acquisition metadata..." />
              ) : patternInspection?.error ? (
                <Alert type="error" showIcon message={seriesMode ? 'First file could not be read' : 'Pattern could not be read'} description={patternInspection.error} />
              ) : patternInspection ? (
                <Alert
                  type={patternInspection.profile_suggestion ? 'success' : 'warning'}
                  showIcon
                  message={(seriesMode ? 'Inspection of the first file: ' : '') + (patternInspection.profile_suggestion
                    ? `Instrument settings applied: ${patternInspection.profile_suggestion.profile}`
                    : 'Pattern loaded; select the instrument profile manually')}
                  description={[
                    patternInspection.metadata?.instrument_system || patternInspection.metadata?.instrument_control_software,
                    patternInspection.metadata?.detector,
                    patternInspection.recommended_wavelength ? `${patternInspection.recommended_wavelength} radiation` : null,
                    Number.isFinite(patternInspection.two_theta_min) && Number.isFinite(patternInspection.two_theta_max)
                      ? `2θ ${patternInspection.two_theta_min.toFixed(2)}–${patternInspection.two_theta_max.toFixed(2)}°`
                      : null,
                    patternInspection.data_points ? `${patternInspection.data_points.toLocaleString()} points` : null,
                    qualityStatText(patternInspection.quality_stats),
                    patternInspection.profile_suggestion?.reason,
                    patternInspection.profile_suggestion ? 'Confirm the selection if the actual slit or optical setup was different.' : null,
                  ].filter(Boolean).join(' · ')}
                />
              ) : null}
              {patternInspection && !patternInspection.error && qualityAlertItems(patternInspection).length ? (
                <div className="dara-note-group" style={{ marginBottom: 16 }}>
                  <div className="dara-subhead">Data quality pre-check (advisory)</div>
                  {qualityAlertItems(patternInspection).map((item, index) => (
                    <Alert key={index} type={item.type} showIcon message={item.message} />
                  ))}
                </div>
              ) : null}
              {patternInspection?.filename_chemistry ? (
                <Alert type="info" showIcon
                  message={patternInspection.filename_chemistry.elements.length
                    ? `Elements recognised from the file name: ${patternInspection.filename_chemistry.elements.join(', ')}`
                    : 'No unambiguous chemical formula in the file name. Enter the elements manually.'}
                  description={patternInspection.filename_chemistry.elements.length
                    ? `Formulas: ${patternInspection.filename_chemistry.formulas.join(', ')}. ${patternInspection.elementsAutoApplied ? 'Added to Included elements.' : 'Existing element entries were kept.'} A suggestion from the file name only; compare it with the actual sample. Temperatures, times and single-letter symbols are never filled in.`
                    : 'Example: BiFeO3_800C_60min.xrdml → Bi, Fe, O. Check single-letter symbols such as C or S and ambiguous names before entering them.'}
                />
              ) : null}
            </Card>

            <Card name="chemistry" title="Chemistry">
              <Form.Item label="Analysis mode" name="analysis_mode">
                <Segmented block options={Object.entries(MODE_LABELS).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
              <Alert type={analysisMode === 'known_mixture' ? 'warning' : 'info'} showIcon message={modeMessage} />
              <div className="dara-field-row wide-cells">
                <Form.Item label="Included elements" name="elements"
                  rules={[{ required: analysisMode === 'elemental', message: 'Please input element symbols.' }]}
                  tooltip="Example: In, La, Ti, O. In other modes they can be derived from formulas.">
                  <Input placeholder="In, La, Ti, O" />
                </Form.Item>
                <Form.Item label={analysisMode === 'known_mixture' ? 'Phases to refine' : 'Precursor / expected formulas'}
                  name="precursor_formulas" rules={[{ required: analysisMode !== 'elemental', message: 'Please input chemical formulas.' }]}
                  tooltip="In elemental mode this is optional, but listed phases are guaranteed candidates. Optional amounts enable the mass-balance check: a molar coefficient before the formula (Fe2O3, 2 TiO2) or a weighed mass after a colon (In2O3:0.3022g, TiO2:0.2004g).">
                  <Input placeholder="In2O3, La(OH)3, TiO2" />
                </Form.Item>
              </div>
              <div className="dara-field-row wide-cells">
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
              </div>
            </Card>

            <Card name="instrument" title="Instrument & range">
              <Form.Item label="Instrument profile"
                tooltip="For XRDML, DARA applies the closest bundled profile from the recorded instrument and detector. Always confirm unusual slit or optic setups.">
                <div className="dara-inline">
                  <div className="grow">
                    <Form.Item name="instrument_profile" noStyle>
                      <Select showSearch optionFilterProp="label" aria-label="Instrument profile" style={{ width: '100%' }}
                        options={instrumentNames.map(name => ({ label: instrumentLabel(name), value: name }))} />
                    </Form.Item>
                  </div>
                  <InstrumentManager buttonLabel="Register profile…" buttonSize="middle" onRegistered={profile => {
                    setInstrumentNames(previous => [...previous, profile.id]);
                    setInstrumentDetails(previous => ({ ...previous, [profile.id]: profile }));
                    form.setFieldsValue({ instrument_profile: profile.id });
                  }} />
                </div>
              </Form.Item>
              {selected ? <Alert type={selected.verified ? 'info' : 'warning'} showIcon
                message={`${selected.label} — ${selected.verified ? 'verification declared by ' + selected.verified_by : 'not verified against a standard'}`}
                description={<>{selected.geometry} · {selected.radiation}<br />{selected.notes}</>} /> : null}
              <div className="dara-field-row">
                {!synchrotron ? (
                  <Form.Item label="Wavelength" name="wavelength_lab" rules={[{ required: true }]}>
                    <Select options={WAVELENGTHS.map(item => ({ value: item[0], label: `${item[0]} (${item[1].toFixed(4)} Å)` }))} />
                  </Form.Item>
                ) : (
                  <Form.Item label="Wavelength (nm)" name="wavelength_sync" rules={[{ required: true, pattern: /^\d+(\.\d+)?$/ }]}>
                    <Input />
                  </Form.Item>
                )}
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
              </div>
              <div className="dara-field-row wide-cells">
                <Form.Item label="Analysis range · 2θ (°)" tooltip="Leave blank to use the measured range. Explicit limits apply to search and final refinement alike.">
                  <Space>
                    <Form.Item name="two_theta_min" noStyle><InputNumber min={0} max={180} placeholder="Start" aria-label="Analysis start angle" style={{ width: 96 }} /></Form.Item>
                    <span>to</span>
                    <Form.Item name="two_theta_max" noStyle><InputNumber min={0} max={180} placeholder="End" aria-label="Analysis end angle" style={{ width: 96 }} /></Form.Item>
                  </Space>
                </Form.Item>
                <Form.Item label="Excluded intervals · 2θ (°)" tooltip="Exclude confirmed holder/artifact regions only. These intervals are omitted from fitting and peak diagnostics; measured intensities are preserved.">
                  <Form.List name="excluded_ranges">
                    {(fields, { add, remove }) => (
                      <Space direction="vertical" style={{ width: '100%' }}>
                        {fields.map(field => (
                          <Space key={field.key} align="start">
                            <Form.Item name={[field.name, 0]} rules={[{ required: true, message: 'Start angle required' }]} style={{ marginBottom: 0 }}>
                              <InputNumber min={0} max={180} placeholder="Start" aria-label="Excluded interval start" style={{ width: 96 }} />
                            </Form.Item>
                            <span style={{ lineHeight: '32px' }}>to</span>
                            <Form.Item name={[field.name, 1]} rules={[{ required: true, message: 'End angle required' }]} style={{ marginBottom: 0 }}>
                              <InputNumber min={0} max={180} placeholder="End" aria-label="Excluded interval end" style={{ width: 96 }} />
                            </Form.Item>
                            <Button type="text" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} aria-label="Remove excluded interval" />
                          </Space>
                        ))}
                        <Button type="dashed" icon={<PlusOutlined />} onClick={() => add([null, null])}>Add excluded interval</Button>
                      </Space>
                    )}
                  </Form.List>
                </Form.Item>
              </div>
            </Card>
          </div>

          <div className="dara-submit-side">
            <Card name="run" title="Run">
              <dl className="dara-run-summary">
                <dt>Pattern</dt><dd>{patternSummary}</dd>
                <dt>Mode</dt><dd>{MODE_LABELS[analysisMode] || analysisMode}</dd>
                <dt>Elements</dt><dd>{elementSummary || missing(analysisMode === 'elemental' ? 'Not entered' : 'From formulas')}</dd>
                {formulaSummary ? <><dt>{analysisMode === 'known_mixture' ? 'Phases' : 'Formulas'}</dt><dd>{formulaSummary}</dd></> : null}
                <dt>Instrument</dt><dd>{selectedInstrument ? instrumentLabel(selectedInstrument) : missing('Not selected')}</dd>
                <dt>Radiation</dt><dd>{wavelengthSummary} · {rangeSummary}</dd>
              </dl>
              {estimate || estimating
                ? <EstimateAlert estimate={estimate} loading={estimating} prefilter={watchedPrefilter !== false} />
                : <p className="dara-run-hint">{analysisMode === 'elemental' ? 'Enter elements or formulas' : 'Enter formulas'} to see the candidate and run-time estimate.</p>}
              {msg ? <Alert message={msg} type="error" showIcon closable onClose={() => setMsg(null)} /> : null}
              <Form.Item>
                <Button type="primary" size="large" block onClick={onSubmit} disabled={spinning || loadingClone} loading={spinning}>
                  {seriesMode ? `Submit series${seriesCount ? ` (${seriesCount} files)` : ''}` : 'Submit'}
                </Button>
              </Form.Item>
            </Card>

            <section className="dara-card dara-card-advanced" aria-label="Advanced options">
              <button type="button" className="dara-advanced-toggle" aria-expanded={advancedOpen} aria-controls="dara-advanced-body"
                onClick={() => setAdvancedOpen(open => !open)}>
                <RightOutlined />
                <h2 className="dara-card-title">Advanced</h2>
                {hasAdvancedChanges
                  ? <Tag color="blue" style={{ marginLeft: 'auto', marginRight: 0 }} title={changedAdvanced.join(', ')}>{changedAdvanced.length} changed</Tag>
                  : <span className="note">Databases, limits, refinement, extra CIFs</span>}
              </button>
              <div id="dara-advanced-body" className="dara-advanced-body" hidden={!advancedOpen}>
                <Form.Item label="Reference databases" tooltip="COD: experimental structures. MP: computed structures (Materials Project), used as a supplement.">
                  <Space wrap size={[16, 8]}>
                    <span><Form.Item name="use_cod" valuePropName="checked" noStyle>
                      <Switch checkedChildren="COD" unCheckedChildren="COD" />
                    </Form.Item><span style={{ marginLeft: 8 }}>experimental</span></span>
                    <span><Form.Item name="use_mp" valuePropName="checked" noStyle>
                      <Switch checkedChildren="MP" unCheckedChildren="MP" />
                    </Form.Item><span style={{ marginLeft: 8 }}>computed supplement</span></span>
                    <MpKeyStatus />
                  </Space>
                </Form.Item>
                <div className="dara-field-row">
                  <Form.Item label="MP E-hull cutoff" name="mp_e_hull_cutoff" tooltip="eV/atom; lower values retain more plausible MP structures.">
                    <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Maximum MP structures" name="max_mp_structures">
                    <InputNumber min={1} max={1000} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Reference screening limit (s)" name="screening_timeout_s"
                    tooltip="Maximum BGMN time per reference during screening; very large structures get twice this (at most 1200 s). Leave blank for the original 1200 s.">
                    <InputNumber min={60} max={1200} step={30} placeholder="1200" style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="Maximum analysis time (min)" name="max_analysis_minutes"
                    tooltip="When exceeded, the search stops and returns the best result found so far, marked as incomplete. Leave blank for no limit.">
                    <InputNumber min={5} max={720} placeholder="No limit" style={{ width: '100%' }} />
                  </Form.Item>
                </div>
                <Form.Item label="Candidate pre-filter" name="candidate_prefilter" valuePropName="checked"
                  tooltip="Skip references whose strongest calculated peaks are absent from the pattern. Uploaded, library and precursor phases are never skipped.">
                  <Switch />
                </Form.Item>
                <RefinementControlsEditor bare value={refinementControls} onChange={setRefinementControls} />
                {cloneSource?.additional_cif_count > 0 ? (
                  <Form.Item label={`Reuse ${cloneSource.additional_cif_count} saved reference CIFs`} name="reuse_additional_cifs" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                ) : null}
                <ReferenceLibraryField library={library} />
                <Form.Item label="Additional reference CIFs" name="additional_phases" valuePropName="fileList" getValueFromEvent={event => event?.fileList}>
                  <Upload beforeUpload={() => false} multiple accept=".cif">
                    <Button icon={<UploadOutlined />}>Select CIF files</Button>
                  </Upload>
                </Form.Item>
              </div>
            </section>
          </div>
        </Form>
      </Container>
    </Layout>
  );
}

export default Submission;
