import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const out = path.join(root, 'artifacts', 'browser-extra', new Date().toISOString().replaceAll(':', '-'));
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const report = { started_at: new Date().toISOString(), checks: [], errors: [] };
page.on('pageerror', e => report.errors.push(String(e)));
const url = 'http://127.0.0.1:9019';
const state = async () => (await page.request.get(url + '/api/state')).json();
const run = async id => (await page.request.get(url + '/api/runs/' + id)).json();
async function wait(check, label, limit = 100000) { const start = Date.now(); while (Date.now() - start < limit) { const r = await check(); if (r) return r; await page.waitForTimeout(500); } throw new Error('Timeout: ' + label); }
const shot = name => page.screenshot({ path: path.join(out, name + '.png'), fullPage: true });
async function reset() { const gen = (await state()).generation; await page.getByRole('button', { name: '重置实验室', exact: true }).click(); await wait(async () => (await state()).generation !== gen, 'reset'); }
const compose = (action, service) => execFileSync('docker', ['compose', '--project-name', 'section9-observe', '--env-file', 'infra/.env', '-f', 'infra/docker-compose.yml', action, service], { cwd: root, stdio: 'pipe', timeout: 45000 });
let collectorStopped = false;
try {
  await page.goto(url, { waitUntil: 'networkidle' });
  await reset();
  await page.getByRole('button', { name: '成本膨胀', exact: true }).click();
  const incident = await wait(async () => { const s = await state(); return s.incident?.symptoms?.length ? s.incident : null; }, 'cost observed');
  await page.getByRole('button', { name: '成本专家加入', exact: true }).click();
  await wait(async () => { const r = await run(incident.id); return r.status === 'resolved' && r.events.some(e => e.event_type === 'task.claimed' && e.producer === 'cost') && r.events.some(e => e.event_type === 'dialog.received' && e.producer === 'cost'); }, 'capability join and real cost contribution');
  const joined = await run(incident.id);
  await fs.writeFile(path.join(out, 'cost-expert.json'), JSON.stringify(joined, null, 2));
  report.checks.push({ name: 'cost specialist discovered, autonomously claimed, real model message', passed: true, run_id: incident.id });
  await shot('cost-expert-joined');
  await reset();
  await page.getByRole('button', { name: '工具死循环', exact: true }).click();
  const loop = await wait(async () => { const s = await state(); return s.incident?.scenario === 'loop' ? s.incident : null; }, 'loop injected');
  await wait(async () => (await run(loop.id)).events.some(e => e.event_type === 'request.progress' && e.payload.step_index >= 4), 'actual loop progress');
  await reset();
  const revision = (await state()).config.revision;
  await page.waitForTimeout(5000);
  const clean = await state();
  assert.equal(clean.config.revision, revision);
  assert.equal(clean.victim.stalled_requests, 0);
  assert.equal(clean.config.retry_on_terminal, false);
  await fs.writeFile(path.join(out, 'reset-loop.json'), JSON.stringify(await run(loop.id), null, 2));
  report.checks.push({ name: 'reset cancels old tool loop and late model result cannot change configuration', passed: true, revision });
  await shot('reset-loop-clean');
  // Only this project's collector; keep Langfuse, databases and unrelated containers intact.
  compose('stop', 'otel-collector'); collectorStopped = true;
  await wait(async () => (await state()).dependencies.collector.status === 'degraded', 'real collector outage visible', 30000);
  await wait(async () => (await page.locator('.deps').innerText()).includes('collector'), 'dependency panel');
  await page.waitForTimeout(9000);
  await shot('collector-degraded');
  report.checks.push({ name: 'actual collector shutdown shows degraded dependency', passed: true });
  compose('start', 'otel-collector'); collectorStopped = false;
  await wait(async () => (await state()).dependencies.collector.status === 'available', 'collector restored', 45000);
  report.checks.push({ name: 'collector restored without resetting application or deleting volumes', passed: true });
  await shot('collector-recovered');
  assert.equal(report.errors.length, 0);
  report.passed = true;
} catch (error) {
  report.passed = false; report.failure = String(error.stack || error); process.exitCode = 1; await shot('failure');
} finally {
  if (collectorStopped) compose('start', 'otel-collector');
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify({ output: out, ...report }));
}
