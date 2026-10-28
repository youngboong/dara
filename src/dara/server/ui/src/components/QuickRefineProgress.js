import React from 'react';
import { Alert, Button, Progress, Space, message } from 'antd';
import { etaText, isMissingEndpoint } from './versions.mjs';

export const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
export const NOT_AVAILABLE = 'Not available on this server version.';

/** Error carrying the HTTP status so callers can hide features on 404/405. */
export class ApiError extends Error {
  constructor(text, status) {
    super(text);
    this.status = status;
  }
  get missing() {
    return isMissingEndpoint(this.status, this.message);
  }
}

function detailText(body, status) {
  const detail = body?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => (typeof item === 'string' ? item : item?.msg)).filter(Boolean).join('; ') || `Request failed (HTTP ${status}).`;
  }
  return `Request failed (HTTP ${status}).`;
}

/** `fetch` + JSON with `ApiError` on a non-2xx response. */
export async function requestJson(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  let body = null;
  try {
    body = await response.json();
  } catch (_) {
    // Not JSON (for example a proxy error page); the status is still reported.
  }
  if (!response.ok) throw new ApiError(detailText(body, response.status), response.status);
  return body;
}

export function postJson(path, payload, options = {}) {
  return requestJson(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}), ...options });
}

const ACTIVE = new Set(['queued', 'running']);
const TERMINAL = new Set(['done', 'failed', 'cancelled']);
const POLL_MS = 500;
const MAX_POLL_ERRORS = 12;

/**
 * One quick re-refinement at a time per task. The hook lives on the task page (not in the fit
 * modal), so progress continues when the modal is closed; an active job is resumed after a reload.
 * `onVersion(n, job, meta)` runs once when a job finishes with a stored version.
 */
export function useQuickRefine(taskId, { onVersion } = {}) {
  const [job, setJob] = React.useState(null);
  const [available, setAvailable] = React.useState(null);
  const handlers = React.useRef({ onVersion });
  handlers.current = { onVersion };
  const metaRef = React.useRef(new Map());
  const timer = React.useRef(null);
  const mounted = React.useRef(true);
  const pollingId = React.useRef(null);

  const stopPolling = React.useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    pollingId.current = null;
  }, []);

  const finish = React.useCallback(record => {
    const meta = metaRef.current.get(record.job_id) || {};
    metaRef.current.delete(record.job_id);
    if (record.status === 'done') {
      setJob(null);
      if (record.version != null) handlers.current.onVersion?.(record.version, record, meta);
    } else if (record.status === 'cancelled') {
      setJob(null);
      message.info('Re-refinement cancelled.');
    } else {
      setJob(record);
    }
  }, []);

  const poll = React.useCallback((jobId, failures = 0) => {
    if (pollingId.current !== null && pollingId.current !== jobId) return;
    pollingId.current = jobId;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      timer.current = null;
      let record;
      try {
        record = await requestJson(`/quick-jobs/${jobId}`);
      } catch (error) {
        if (!mounted.current || pollingId.current !== jobId) return;
        if (error.status === 404 || failures + 1 >= MAX_POLL_ERRORS) {
          stopPolling();
          setJob({ job_id: jobId, status: 'failed', stage: 'Failed',
            error: error.status === 404 ? 'The job is no longer known to the server.' : `Lost contact with the server: ${error.message}` });
        } else {
          poll(jobId, failures + 1);
        }
        return;
      }
      if (!mounted.current || pollingId.current !== jobId) return;
      if (TERMINAL.has(record.status)) {
        stopPolling();
        finish(record);
      } else {
        setJob(previous => ({ ...previous, ...record, receivedAt: Date.now() }));
        poll(jobId, 0);
      }
    }, POLL_MS);
  }, [finish, stopPolling]);

  React.useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    // Resume a job that was started before a reload.
    requestJson(`/task/${taskId}/quick-jobs`, { signal: controller.signal })
      .then(body => {
        const list = Array.isArray(body) ? body : (body?.jobs || body?.active || []);
        const active = list.find(item => ACTIVE.has(item.status));
        if (!mounted.current) return;
        setAvailable(true);
        if (active) {
          setJob({ ...active, receivedAt: Date.now() });
          poll(active.job_id);
        }
      })
      .catch(error => {
        if (error.name === 'AbortError') return;
        if (error.missing && mounted.current) setAvailable(false);
      });
    return () => {
      mounted.current = false;
      controller.abort();
      stopPolling();
    };
  }, [taskId, poll, stopPolling]);

  const busy = Boolean(job && (job.status === 'starting' || ACTIVE.has(job.status)));

  const start = React.useCallback(async (body, meta = {}) => {
    if (busy) return null;
    setJob({ status: 'starting', stage: 'Starting…', label: body.label, elapsed_s: 0, receivedAt: Date.now() });
    try {
      const accepted = await postJson(`/task/${taskId}/quick-refine`, body);
      if (!mounted.current) return accepted;
      setAvailable(true);
      metaRef.current.set(accepted.job_id, { ...meta, request: body });
      setJob({ ...accepted, status: accepted.status || 'queued', stage: accepted.stage || 'Queued',
        label: body.label, elapsed_s: 0, receivedAt: Date.now() });
      poll(accepted.job_id);
      return accepted;
    } catch (error) {
      if (!mounted.current) return null;
      if (error.missing) {
        setAvailable(false);
        setJob(null);
        message.warning(NOT_AVAILABLE);
      } else {
        setJob({ status: 'failed', stage: 'Failed', error: error.message, label: body.label });
      }
      return null;
    }
  }, [busy, taskId, poll]);

  const cancel = React.useCallback(async () => {
    const id = job?.job_id;
    if (!id) return;
    try {
      await postJson(`/quick-jobs/${id}/cancel`, {});
    } catch (error) {
      message.error(error.message);
    }
  }, [job]);

  const dismiss = React.useCallback(() => setJob(null), []);

  return { job, busy, available, start, cancel, dismiss };
}

/** Inline progress strip for the active quick job (not a modal). */
export default function QuickRefineProgress({ job, onCancel, onDismiss, style }) {
  const [now, setNow] = React.useState(Date.now());
  const running = job && (job.status === 'starting' || ACTIVE.has(job.status));
  React.useEffect(() => {
    if (!running) return undefined;
    const handle = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(handle);
  }, [running]);
  if (!job) return null;
  if (job.status === 'failed') {
    return <Alert type="error" showIcon closable onClose={onDismiss} style={{ marginBottom: 12, ...style }}
      message={job.label ? `Re-refinement failed: ${job.label}` : 'Re-refinement failed'}
      description={job.error || 'The server did not report a reason.'} />;
  }
  const sinceReport = job.receivedAt ? Math.max(0, (now - job.receivedAt) / 1000) : 0;
  const elapsed = Number(job.elapsed_s || 0) + (running ? sinceReport : 0);
  const live = { ...job, elapsed_s: elapsed };
  const etaRaw = etaText(live);
  const known = job.status === 'running' && job.eta_s != null && Number(job.eta_s) > 0;
  const percent = known ? Math.min(95, Math.round((elapsed / Number(job.eta_s)) * 100)) : undefined;
  const queued = job.status === 'queued';
  // A queued job shows its queue position (or "Starting…") instead of the generic stage name.
  const stage = job.status === 'starting' ? 'Starting…'
    : queued && etaRaw ? etaRaw : job.stage || (queued ? 'Queued' : 'Working');
  const eta = !queued && etaRaw && etaRaw !== stage ? etaRaw : '';
  return <Alert type="info" showIcon style={{ marginBottom: 12, ...style }}
    message={<Space wrap>
      <strong>{job.label || 'Re-refining'}</strong>
      <span>{stage}</span>
      <span>{`${Math.round(elapsed)} s`}</span>
      {eta ? <span>{eta}</span> : null}
    </Space>}
    description={<Space style={{ width: '100%' }} align="center">
      <div style={{ width: 'min(520px, 60vw)' }}>
        {known
          ? <Progress percent={percent} size="small" status="active" showInfo={false} />
          : <Progress percent={100} size="small" status="active" showInfo={false} strokeColor="#91caff" />}
      </div>
      <Button size="small" onClick={onCancel} disabled={!job.job_id}>Cancel</Button>
    </Space>} />;
}
