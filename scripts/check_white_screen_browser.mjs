// Read-only browser diagnostic for the locally running DARA UI.
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const target = process.argv[2] || 'http://127.0.0.1:8898/';
const visualize = process.argv.includes('--visualize');
const output = path.resolve('artifacts/white-screen-qa');
await mkdir(output, { recursive: true });
const profile = await mkdtemp(path.join(output, 'chrome-'));
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' });
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await fn();
    if (result) return result;
    await delay(100);
  }
  throw new Error('Browser condition timed out');
}

let socket;
try {
  const port = await until(async () => {
    try { return (await readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; }
    catch { return null; }
  });
  const tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map(), exceptions = [], failedRequests = [], consoleErrors = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
    if (message.method === 'Network.loadingFailed') failedRequests.push(message.params);
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') consoleErrors.push(message.params);
    const callback = pending.get(message.id);
    if (!callback) return;
    pending.delete(message.id);
    if (message.error) callback.reject(new Error(JSON.stringify(message.error)));
    else callback.resolve(message.result);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const call = ++id;
    pending.set(call, { resolve, reject });
    socket.send(JSON.stringify({ id: call, method, params }));
  });
  const evaluate = async expression => {
    const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await cdp('Runtime.enable');
  await cdp('Network.enable');
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: target });
  await until(() => evaluate(`document.readyState === 'complete'`));
  await delay(2500);
  if (visualize) {
    await until(() => evaluate(`Array.from(document.querySelectorAll('a')).some(a => a.textContent.trim() === 'Visualize result')`), 30000);
    await evaluate(`Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === 'Visualize result').click()`);
    await until(() => evaluate(`!!document.querySelector('.js-plotly-plot')?._fullLayout || !!document.querySelector('.ant-alert-error')`), 30000);
    await delay(500);
  }
  const state = await evaluate(`({
    url: location.href,
    title: document.title,
    text: document.body?.innerText?.slice(0, 1000) || '',
    rootChildren: document.querySelector('#___gatsby')?.childElementCount ?? -1,
    bodyChildren: document.body?.childElementCount ?? -1,
    bodySize: document.body ? [document.body.scrollWidth, document.body.scrollHeight] : null
  })`);
  const screenshot = await cdp('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const screenshotPath = path.join(output, visualize ? 'visualize-result.png' : 'screen.png');
  await writeFile(screenshotPath, Buffer.from(screenshot.data, 'base64'));
  console.log(JSON.stringify({ state, exceptions, consoleErrors, failedRequests, screenshotPath }, null, 2));
} finally {
  socket?.close();
  chrome.kill();
}
