// Standalone local browser QA fallback when the in-app browser is unavailable.
// Uses an isolated, headless Chrome profile; never opens a user's browser profile.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const output = path.resolve('artifacts/visualize-qa');
await mkdir(output, { recursive: true });
const profile = await mkdtemp(path.join(output, 'chrome-'));
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await delay(150); }
  throw new Error('Browser condition timed out');
}
let socket;
try {
  const port = await until(async () => { try { return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; } catch { return null; } });
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    if (!pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id); pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error))); else resolve(message.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const call = ++id; pending.set(call, { resolve, reject });
    socket.send(JSON.stringify({ id: call, method, params }));
  });
  async function evaluate(expression) {
    const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  await cdp('Runtime.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1150, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: 'http://127.0.0.1:8898/task/?id=10' });
  await until(() => evaluate(`Array.from(document.querySelectorAll('a')).some(a => a.textContent === 'Visualize result')`));
  const latticeDisplay = await until(() => evaluate(`(() => {
    const text = document.body.innerText;
    if (!text.includes('a = b = c') || !text.includes('a = b') || !text.includes('±')) return null;
    return { cubicEquality: true, hexagonalEquality: true, esd: true };
  })()`));
  await evaluate(`Array.from(document.querySelectorAll('a')).find(a => a.textContent === 'Visualize result').click()`);
  await until(() => evaluate(`document.querySelector('.ant-modal-title')?.textContent.includes('Candidate 1')`));
  await until(() => evaluate(`!!document.querySelector('.js-plotly-plot')?._fullLayout`));
  await evaluate(`document.querySelector('.js-plotly-plot').scrollIntoView({block:'center'})`);
  const readPlot = () => evaluate(`(() => { const p=document.querySelector('.js-plotly-plot'); return {
    rows: (p.layout.annotations||[]).length, range: p.layout.yaxis.range,
    ticks:p.layout.yaxis.tickvals, title:p.layout.xaxis.title.text,
    phases:p.data.filter(t=>t.meta==='reflection').map(t=>({name:t.name,y:t.y[0],visible:t.visible})),
    residual:p.data.find(t=>t.meta==='difference').yaxis
  } })()`);
  async function clickPhase(name) {
    const box = await evaluate(`(() => { const e=Array.from(document.querySelectorAll('.legend .traces')).find(e=>e.textContent.includes(${JSON.stringify(name)})); const r=e.querySelector('.legendtoggle').getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2} })()`);
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', ...box, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', ...box, button: 'left', clickCount: 1 });
  }
  const initial = await readPlot(); assert.equal(initial.rows, 0);
  await clickPhase('Fluorite'); await until(async () => (await readPlot()).rows === 1);
  await clickPhase('Eskolaite'); await until(async () => (await readPlot()).rows === 2);
  await clickPhase('Calcite'); await until(async () => (await readPlot()).rows === 3);
  const expanded = await readPlot();
  assert(expanded.ticks.every(t => t >= 0)); assert.equal(expanded.title, '2θ [°]');
  assert.equal(expanded.residual, 'y2');
  assert(expanded.range[0] < initial.range[0]);
  const clip = await evaluate(`(() => {const r=document.querySelector('.js-plotly-plot').getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height,scale:1}})()`);
  const screenshot = await cdp('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true });
  await writeFile(path.join(output, 'three-phases.png'), Buffer.from(screenshot.data, 'base64'));
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1100, height: 1150, deviceScaleFactor: 1, mobile: false });
  await until(() => evaluate(`document.querySelector('.js-plotly-plot')._fullLayout.width < 1100`));
  const narrowClip = await evaluate(`(() => {const r=document.querySelector('.js-plotly-plot').getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height,scale:1}})()`);
  await writeFile(path.join(output, 'three-phases-narrow.png'), Buffer.from((await cdp('Page.captureScreenshot', { format: 'png', clip:narrowClip, captureBeyondViewport: true })).data, 'base64'));
  await clickPhase('Eskolaite'); await until(async () => (await readPlot()).rows === 2);
  const hidden = await readPlot();
  assert(hidden.range[0] > expanded.range[0]);
  await clickPhase('Fluorite'); await until(async () => (await readPlot()).rows === 1);
  await clickPhase('Calcite'); await until(async () => (await readPlot()).rows === 0);
  const collapsed = await readPlot(); assert.deepEqual(collapsed.range, initial.range);
  await writeFile(path.join(output, 'closed-phases.png'), Buffer.from((await cdp('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  console.log(JSON.stringify({ latticeDisplay, initial, expanded, hidden, collapsed, errors, output }, null, 2));
} finally {
  socket?.close();
  chrome.kill();
}
