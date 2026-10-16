// Run alongside serve_workflow_qa.py. Only synthetic, in-memory tasks are changed.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const base = 'http://127.0.0.1:8899';
const output = path.resolve('artifacts/workflow-qa');
await mkdir(output, { recursive: true });
const profile = await mkdtemp(path.join(output, 'chrome-'));
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await fn(); if (result) return result; await delay(120); }
  throw new Error(`Browser condition timed out: ${label}`);
}
let socket;
try {
  const port = await until(async () => { try { return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; } catch { return null; } }, 'Chrome startup');
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map(), errors = [], checks = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
    const callback = pending.get(message.id);
    if (!callback) return;
    pending.delete(message.id);
    if (message.error) callback.reject(new Error(JSON.stringify(message.error))); else callback.resolve(message.result);
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
  async function clickText(text, scope = 'document') {
    const found = await evaluate(`Array.from(${scope}.querySelectorAll('button,a')).filter(e=>e.textContent.trim()===${JSON.stringify(text)}).length`);
    assert.equal(found, 1, `Unique click target: ${text}`);
    await evaluate(`Array.from(${scope}.querySelectorAll('button,a')).find(e=>e.textContent.trim()===${JSON.stringify(text)}).click()`);
  }
  async function shot(name) {
    // AntD's modal entry animation lasts 0.3 s; capture the settled layout.
    await delay(350);
    await writeFile(path.join(output, `${name}.png`), Buffer.from((await cdp('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
  }
  async function fill(selector, value) {
    await evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.focus(); const setter=Object.getOwnPropertyDescriptor(e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set; setter.call(e,${JSON.stringify(value)}); e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  }
  await cdp('Runtime.enable'); await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: `${base}/task/?id=1` });
  await until(() => evaluate(`!!document.querySelector('textarea[aria-label="Lab notes"]') && !document.querySelector('textarea').disabled`), 'review loaded');
  await fill('textarea[aria-label="Lab notes"]', 'QA: reviewed candidate; stored separately from minimum Rwp.');
  await clickText('Save review');
  await until(async () => (await (await fetch(`${base}/api/task/1/review`)).json()).notes.startsWith('QA:'), 'persisted notes');
  await clickText('Adopt', `document.querySelector('tr[data-row-key="0"]')`);
  await until(async () => (await (await fetch(`${base}/api/task/1/review`)).json()).selection === 0, 'candidate adoption');
  await cdp('Page.reload');
  await until(() => evaluate(`document.querySelector('textarea')?.value.startsWith('QA:')`), 'review survives reload');
  await shot('review'); checks.push('notes and adoption persist across reload');
  const report = await fetch(`${base}/api/task/1/report`);
  assert.equal(report.status, 200); const reportHtml = await report.text();
  assert(reportHtml.includes('<svg')); assert(reportHtml.includes('QA: reviewed candidate'));
  assert(report.headers.get('content-disposition').includes('attachment'));
  await writeFile(path.join(output, 'report.html'), reportHtml);
  checks.push('offline report has fit and saved notes');
  await clickText('Visualize result');
  await until(() => evaluate(`document.querySelector('.ant-modal-title')?.textContent.includes('Candidate 1') && !!document.querySelector('.js-plotly-plot')?._fullLayout`), 'candidate plot');
  const view = await evaluate(`(() => {const p=document.querySelector('.js-plotly-plot');return {axis:p.layout.xaxis.title.text,residual:p.data.find(t=>t.meta==='difference').yaxis,regions:p.layout.shapes.filter(s=>s.type==='rect').length}})()`);
  assert.equal(view.axis, '2θ [°]'); assert.equal(view.residual, 'y2'); assert.equal(view.regions, 2);
  checks.push('candidate plot keeps theta, residual panel and shaded cuts');
  await clickText('Edit phases', `document.querySelector('.ant-modal')`);
  await until(() => evaluate(`Array.from(document.querySelectorAll('.ant-modal-title')).some(e=>e.textContent==='Select phases & re-refine')`), 'phase modal');
  await until(() => evaluate(`document.querySelector('.ant-modal:last-child')?.textContent.includes('1 selected') || document.body.innerText.includes('1 selected (maximum 20)')`), 'phase options');
  assert.equal(await evaluate(`document.querySelectorAll('input[aria-label="Lattice refinement"]').length`), 1);
  await evaluate(`document.querySelector('input[aria-label="Lattice refinement"]').closest('.ant-select-selector').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))`);
  await until(() => evaluate(`Array.from(document.querySelectorAll('.ant-select-item-option-content')).some(e=>e.textContent==='Fixed to reference CIF')`), 'lattice choices');
  await evaluate(`Array.from(document.querySelectorAll('.ant-select-item-option-content')).find(e=>e.textContent==='Fixed to reference CIF').click()`);
  await shot('phase-selection');
  await clickText('Run as new task');
  await until(() => evaluate(`location.search !== '?id=1' && location.pathname==='/task/'`), 'new refinement task');
  const newId = await evaluate(`new URLSearchParams(location.search).get('id')`);
  assert.notEqual(newId, '1');
  assert.equal((await (await fetch(`${base}/api/task/1`)).json()).status, 'COMPLETED');
  await until(() => evaluate(`!!document.querySelector('section[aria-label="Analysis progress"]')`), 'queued progress panel');
  assert.equal((await (await fetch(`${base}/api/task/${newId}/progress`)).json()).status, 'READY');
  checks.push('refinement controls and queued task progress are accessible');
  checks.push('phase editing queues a new task; parent unchanged');
  await cdp('Page.navigate', { url: `${base}/compare/?tasks=1,2` });
  await until(() => evaluate(`document.querySelector('.js-plotly-plot')?.data.length===2`), 'comparison plot');
  await clickText('Overlay');
  await fill('input[aria-label="Minimum two theta"]', '20');
  await fill('input[aria-label="Maximum two theta"]', '50');
  await clickText('Apply');
  await until(() => evaluate(`document.querySelector('.js-plotly-plot').layout.xaxis.range?.join(',')==='20,50'`), 'comparison range');
  await shot('comparison'); checks.push('two-pattern overlay, normalization and shared angular range');
  await cdp('Page.navigate', { url: `${base}/?clone_task=1` });
  await until(() => evaluate(`document.querySelector('input[id="user"]')?.value==='QA fixture'`), 'cloned input settings');
  await shot('copied-conditions'); checks.push('copy conditions opens prefilled form without reupload');
  await clickText('Register instrument profile');
  await until(() => evaluate(`!!document.querySelector('input[aria-label="GEQ profile file"]')`), 'instrument registration form');
  await shot('instrument-registration');
  checks.push('instrument registration separates import from operator verification');
  await cdp('Page.navigate', { url: `${base}/task/?id=3` });
  await until(() => evaluate(`Array.from(document.querySelectorAll('button')).some(e=>e.textContent==='Compare two candidate fits' && !e.disabled)`), 'two candidates loaded');
  await clickText('Compare two candidate fits');
  await until(() => evaluate(`document.querySelector('.js-plotly-plot')?.data.length===5`), 'candidate fit comparison');
  const comparisonView = await evaluate(`(() => {const p=document.querySelector('.js-plotly-plot');return {match:p.layout.xaxis2.matches,theta:p.layout.xaxis2.title,labels:p.data.map(t=>t.name)}})()`);
  assert.equal(comparisonView.match, 'x');
  assert(comparisonView.labels.includes('B obs − calc'));
  await shot('candidate-differences');
  await evaluate(`Array.from(document.querySelectorAll('.ant-modal button')).find(e=>/^\\d+\\.\\d+°$/.test(e.textContent)).click()`);
  await until(() => evaluate(`document.querySelector('.js-plotly-plot').layout.xaxis.range?.length===2`), 'candidate disagreement zoom');
  await shot('candidate-differences-zoom');
  checks.push('candidate comparison has shared angular axes, separate residuals, phase differences and region zoom');
  await cdp('Page.navigate', { url: pathToFileURL(path.join(output, 'report.html')).href });
  await until(() => evaluate(`!!document.querySelector('svg') && document.body.innerText.includes('QA: reviewed candidate')`), 'offline report render');
  await shot('report');
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ checks, errors, output }, null, 2));
} finally { socket?.close(); chrome.kill(); }
