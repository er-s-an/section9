import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');

const base = process.env.S9_BASE_URL || 'http://127.0.0.1:9019';
const pair = { pair_id: 'pair-scope-fixture', status: 'ready', spec_hash: 'spec-123', swarm_run_id: 'run-swarm', baseline_run_id: 'run-baseline', immutable_spec: { scenario: 'composite', seed: 42, budget_policy: { total_tokens_per_arm: 1000 }, baseline_config_hash: 'healthy', model: 'fixture' }, comparison_integrity: {}, links: {}, created_at: new Date().toISOString(), started_at: null, ended_at: null };
const projection = arm => ({ pair_id: pair.pair_id, run_id: arm === 'swarm' ? pair.swarm_run_id : pair.baseline_run_id, arm, spec_hash: pair.spec_hash, pair_status: 'ready', run: { status: 'completed', elapsed_s: 1 }, config: {}, stages: [], agents: [{ id: arm === 'swarm' ? 'sentry' : 'single', name: arm === 'swarm' ? '哨兵' : '单 Agent', status: 'idle', position: 'idle' }], events: [], as_of_sequence: 3, usage: {}, dependencies: {}, verification: { passed: false }, rca: {}, comparison_integrity: {}, started_at: pair.created_at, log_page: { has_more: false, next_after: null, range_start: 1, range_end: 3, watermark: 3 } });
const state = { generation: 'scope-fixture', autonomy: 'L2', muted: false, monitoring: false, memory_enabled: false, config: { revision: 'r1' }, agents: [{ id: 'sentry', role: 'monitor', name: '哨兵', status: 'idle', heartbeat_at: null, task_id: null, detail: '', capabilities: [] }], incident: { id: 'primary-run', status: 'failed', scenario: 'prompt', symptoms: ['fixture'], plan: null, verification: { passed: false }, elapsed_s: 1 }, runs: [], metrics: {}, dependencies: {}, playbooks: [], events: [], attract: { enabled: false, cycle: 0 }, model: 'fixture', version: 'test' };

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
let activeReads = 0;
let assetsMissing = false;
await page.route('**/api/**', async route => {
  const req = route.request();
  assert.equal(req.method(), 'GET', `scope audit must not write: ${req.method()} ${req.url()}`);
  const url = new URL(req.url());
  let body = {};
  if (url.pathname === '/api/state') body = state;
  else if (url.pathname === '/api/events') body = '';
  else if (url.pathname === '/api/pairs/active') { activeReads++; body = { active_pair: pair }; }
  else if (url.pathname === '/api/pairs') body = { items: [pair] };
  else if (url.pathname.endsWith('/arms/swarm')) { activeReads++; body = projection('swarm'); }
  else if (url.pathname.endsWith('/arms/baseline')) { activeReads++; body = projection('baseline'); }
  else if (url.pathname.startsWith('/api/pairs/') && url.pathname.split('/').length === 4) body = pair;
  else body = {};
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
});
await page.route('**/vendor/star-office/**', async route => {
  if (assetsMissing) return route.abort();
  // A tiny valid image proves the success path without downloading or mutating assets.
  await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') });
});
await page.goto(base + '/', { waitUntil: 'domcontentloaded' });
await page.getByText('实时处理控制', { exact: true }).waitFor();
await page.getByText('对照实验控制', { exact: true }).waitFor();
await page.waitForTimeout(500);
assert(await page.getByText('重新开始本轮', { exact: true }).count());
assert(await page.getByText('固定自动处理 · 独立运行', { exact: true }).count());
assert(await page.getByText('验收 未通过', { exact: true }).count());
assert(await page.locator('.s9-office__background-fallback.asset-fallback-hidden').count(), 'fallback hidden when background asset loads');
assert.equal(await page.locator('.s9-office-agent__fallback').first().isVisible(), false, 'loaded sprite must not be covered by fallback');
assetsMissing = true;
await page.reload({ waitUntil: 'domcontentloaded' });
await page.getByText('实时处理控制', { exact: true }).waitFor();
await page.waitForTimeout(500);
assert(await page.locator('.s9-office__background-fallback:not(.asset-fallback-hidden)').count(), 'fallback visible when background asset is missing');
assert.equal(await page.locator('.s9-office-agent__fallback').first().isVisible(), true, 'missing sprite must show its fallback');
await page.waitForTimeout(4500);
assert(activeReads >= 2, `expected Pair panel polling, got ${activeReads} reads`);
console.log(JSON.stringify({ passed: true, checks: ['scope labels', 'primary failed acceptance', 'Pair polling', 'missing asset fallback'], pair_arm_reads: activeReads }));
await browser.close();
