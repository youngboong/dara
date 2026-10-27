// Tell the server which DARA windows are open, so it can stop once all are closed.
const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const HEARTBEAT_MS = 15000;

function clientId() {
  try {
    let id = window.sessionStorage.getItem('dara-client-id');
    if (!id) {
      id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      window.sessionStorage.setItem('dara-client-id', id);
    }
    return id;
  } catch (_) {
    if (!window.__daraClientId) window.__daraClientId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return window.__daraClientId;
  }
}

function send(path, beacon) {
  const body = JSON.stringify({ client_id: clientId() });
  if (beacon && navigator.sendBeacon) {
    navigator.sendBeacon(`${API}${path}`, new Blob([body], { type: 'application/json' }));
    return;
  }
  fetch(`${API}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
    .catch(() => {});
}

// Start once per window; Gatsby keeps the page alive across client-side navigation.
export function startPresence() {
  if (typeof window === 'undefined' || window.__daraPresence) return;
  window.__daraPresence = true;
  send('/presence');
  window.setInterval(() => send('/presence'), HEARTBEAT_MS);
  window.addEventListener('pagehide', () => send('/presence/leave', true));
  // A page restored from the back/forward cache must announce itself again.
  window.addEventListener('pageshow', event => { if (event.persisted) send('/presence'); });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') send('/presence'); });
}
