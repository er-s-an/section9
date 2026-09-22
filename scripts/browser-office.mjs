import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const out = path.join(root, 'artifacts/office-reuse', new Date().toISOString().replaceAll(':', '-'));
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const url = 'http://127.0.0.1:9019';
const report = { started_at: new Date().toISOString(), checks: [], errors: [], network_errors: [] };
page.on('pageerror', error => report.errors.push(String(error)));
page.on('response', response => { if (response.status() >= 400) report.network_errors.push({ url: response.url(), status: response.status() }); });
const state = async () => (await page.request.get(url + '/api/state')).json();
const agent = id => page.locator(`[data-agent-id="${id}"]`);
const snapshot = async () => page.locator('[data-agent-id]').evaluateAll(elements => elements.map(element => ({
  id: element.dataset.agentId, status: element.dataset.status, zone: element.dataset.zone,
  x: Math.round(element.getBoundingClientRect().x), y: Math.round(element.getBoundingClientRect().y),
})));
async function wait(fn, label, timeout = 90000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const result = await fn();
    if (result) return result;
    await page.waitForTimeout(350);
  }
  throw new Error('Timeout: ' + label);
}
let ownsRun = false;
try {
  const initial = await state();
  assert.ok(!initial.incident, 'Start this office QA with an idle laboratory');
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.getByTestId('office-room').waitFor();
  assert.equal(await page.locator('[data-agent-id]').count(), initial.agents.length);
  const assets = await page.getByTestId('office-room').locator('img').evaluateAll(images => images.map(img => ({ src: img.getAttribute('src'), loaded: img.complete && img.naturalWidth > 0 })));
  assert.ok(assets.length >= 3 && assets.every(asset => asset.loaded), 'Real room and furniture images loaded');
  report.assets = assets;
  report.idle_positions = await snapshot();
  assert.ok(new Set(report.idle_positions.map(item => item.y)).size >= 3, 'Agents distributed spatially, not one horizontal row');
  await page.screenshot({ path: path.join(out, '01-office-desktop.png'), fullPage: true });
  await page.getByTestId('office-room').screenshot({ path: path.join(out, '02-office-room.png') });
  await agent('diagnoser').focus();
  await page.keyboard.press('Enter');
  await page.getByTestId('agent-inspector').waitFor();
  assert.match(await page.getByTestId('agent-inspector').innerText(), /诊断员/);
  report.checks.push({ name: 'real assets, distributed roles and keyboard inspector', passed: true });

  await page.getByRole('button', { name: '暂停 A', exact: true }).click();
  await wait(async () => await agent('fixer-a').getAttribute('data-status') === 'paused', 'actual paused agent');
  assert.equal((await state()).agents.find(item => item.id === 'fixer-a').status, 'paused');
  await agent('fixer-a').click();
  await page.screenshot({ path: path.join(out, '03-paused-agent.png'), fullPage: true });
  await page.getByRole('button', { name: '恢复 A', exact: true }).click();
  await wait(async () => await agent('fixer-a').getAttribute('data-status') !== 'paused', 'agent resumed');
  const communication = page.locator('.control-line').filter({ hasText: '消息层' }).getByRole('button');
  await communication.click();
  await wait(async () => (await state()).muted, 'real communication muted');
  await wait(async () => /禁言/.test(await page.getByTestId('office-room').innerText()), 'muted state rendered');
  await communication.click();
  await wait(async () => !(await state()).muted, 'communication restored');
  report.checks.push({ name: 'pause, resume and communication mapped from authority state', passed: true });

  await page.getByRole('button', { name: '复合故障', exact: true }).click();
  ownsRun = true;
  const incident = await wait(async () => (await state()).incident, 'actual injected incident');
  const active = await wait(async () => {
    const positions = await snapshot();
    return positions.some(item => ['working', 'diagnosing', 'repairing', 'verifying'].includes(item.status)) ? positions : null;
  }, 'working role visible');
  report.working_positions = active;
  await page.screenshot({ path: path.join(out, '04-real-work.png'), fullPage: true });
  const detail = await wait(async () => {
    const run = await (await page.request.get(url + '/api/runs/' + incident.id)).json();
    return ['resolved', 'failed'].includes(run.status) ? run : null;
  }, 'real composite closed');
  report.run = { id: detail.id, status: detail.status, elapsed_s: detail.elapsed_s, generation: detail.generation };
  await fs.writeFile(path.join(out, 'composite-run.json'), JSON.stringify(detail, null, 2));
  assert.equal(detail.status, 'resolved');
  assert.ok(detail.verification?.passed);
  await agent('diagnoser').click();
  await page.screenshot({ path: path.join(out, '05-agent-evidence.png'), fullPage: true });
  report.inspector = await page.getByTestId('agent-inspector').innerText();
  report.checks.push({ name: 'real model composite run restored and independent business verification passed', passed: true });

  const generation = (await state()).generation;
  await page.getByRole('button', { name: '重置实验室', exact: true }).click();
  await wait(async () => (await state()).generation !== generation, 'reset generation');
  await wait(async () => await page.locator('.top-meta').innerText().then(text => text.includes('GEN ' + (Number(generation) + 1))), 'new generation rendered');
  ownsRun = false;
  await agent('diagnoser').click();
  report.after_reset_inspector = await page.getByTestId('agent-inspector').innerText();
  for (const event of detail.events || []) assert.ok(!report.after_reset_inspector.includes(event.event_id), 'No prior run event remains after reset');
  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(600);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow at ' + width);
    await page.screenshot({ path: path.join(out, `06-ready-${width}.png`), fullPage: true });
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  report.reduced_motion = await page.locator('[data-agent-id]').first().evaluate(element => ({ transition: getComputedStyle(element).transitionDuration }));
  report.checks.push({ name: 'reset event boundary, 1280/1440 layouts and reduced-motion inspection', passed: true });
  assert.equal(report.errors.length, 0);
  assert.equal(report.network_errors.length, 0);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = String(error.stack || error);
  process.exitCode = 1;
  await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true });
} finally {
  await page.request.post(url + '/api/agents/fixer-a/resume', { data: {} });
  await page.request.put(url + '/api/communication', { data: { muted: false } });
  if (ownsRun) await page.request.post(url + '/api/reset', { data: {} });
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ out, ...report }));
}
