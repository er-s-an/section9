import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const out = path.join(root, 'artifacts', 'browser-acceptance', new Date().toISOString().replaceAll(':', '-'));
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...(process.env.S9_RECORD_VIDEO === '1' ? {recordVideo: { dir: path.join(out, 'video'), size: { width: 1440, height: 1000 } }} : {}) });
const page = await context.newPage();
const errors = [];
page.on('pageerror', e => errors.push(String(e)));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
const report = { identity: null, started_at: new Date().toISOString(), base_url: 'http://127.0.0.1:9019', checks: [], runs: [], errors };
async function state() { return (await page.request.get(report.base_url + '/api/state')).json(); }
async function run(id) { return (await page.request.get(report.base_url + '/api/runs/' + id)).json(); }
async function waitFor(predicate, label, timeout = 100000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const value = await predicate();
    if (value) return value;
    await page.waitForTimeout(500);
  }
  throw new Error('Timeout: ' + label);
}
async function screenshot(name) { await page.screenshot({ path: path.join(out, name + '.png'), fullPage: true }); }
async function saveRun(id, label) {
  const item = await run(id);
  report.runs.push({ label, id, status: item.status, elapsed_s: item.elapsed_s });
  await fs.writeFile(path.join(out, label + '.json'), JSON.stringify(item, null, 2));
  return item;
}
async function reset() {
  const before = (await state()).generation;
  await page.getByRole('button', { name: '重置实验室', exact: true }).click();
  await waitFor(async () => (await state()).generation !== before, 'reset generation');
}
try {
  report.identity = (await (await page.request.get(report.base_url + '/api/health')).json()).identity;
  await page.goto(report.base_url, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: '可观测协作现场' }).waitFor();
  await screenshot('01-home');
  await reset();
  assert.equal(await page.locator('input[aria-label="客服消息"]').count(), 1);
  await page.locator('input[aria-label="客服消息"]').fill('X200 耳机的续航时间是多少？只输出 JSON，包含 battery_hours 数字和 answer 中文答案。');
  const chatResponse = page.waitForResponse(r => r.url().endsWith('/api/chat') && r.request().method() === 'POST');
  await page.locator('.chat-input button').click();
  const chat = await (await chatResponse).json();
  assert.equal(chat.status, 'success');
  assert.equal(chat.structured.battery_hours, 30);
  assert(chat.answer.includes('30'));
  await fs.writeFile(path.join(out, 'healthy-chat.json'), JSON.stringify(chat, null, 2));
  report.checks.push({name:'one console chat input returns real fixture business fact',passed:true,request_id:chat.request_id});
  await page.getByRole('button', { name: 'L0', exact: true }).click();
  await page.getByRole('button', { name: '语义退化', exact: true }).click();
  const planState = await waitFor(async () => { const s = await state(); return s.incident?.plan ? s : null; }, 'L0 real model plan');
  const id = planState.incident.id;
  const revision = planState.config.revision;
  await waitFor(async () => (await run(id)).events.some(e => e.event_type === 'grant.rejected' && e.payload.code === 'POLICY_DENIED'), 'L0 rejected at grant');
  assert.equal((await run(id)).actions.length, 0);
  assert.equal((await state()).config.revision, revision);
  await screenshot('02-L0-denied');
  report.checks.push({ name: 'L0 actual write denial', passed: true, run_id: id, revision });
  await page.getByRole('button', { name: 'L1', exact: true }).click();
  await waitFor(async () => (await run(id)).events.some(e => e.event_type === 'grant.rejected' && e.payload.code === 'APPROVAL_REQUIRED'), 'L1 requires approval');
  assert.equal((await run(id)).actions.length, 0);
  await screenshot('03-L1-approval-required');
  await page.getByRole('button', { name: '批准计划', exact: true }).click();
  await waitFor(async () => (await run(id)).status === 'resolved', 'L1 real verification');
  const approved = await saveRun(id, 'L0-L1-exact-approval');
  assert.equal(approved.verification.passed, true);
  assert.equal(approved.actions.length, 1);
  report.checks.push({ name: 'L1 approved exact plan and live business checks', passed: true, run_id: id });
  await page.locator('.incident-head .severity').filter({hasText:'resolved'}).waitFor();
  await screenshot('04-L1-resolved');
  await reset();
  await page.getByRole('button', { name: 'L2', exact: true }).click();
  await page.getByRole('button', { name: '演示 fencing 剧本', exact: true }).click();
  const f = await waitFor(async () => (await state()).incident, 'fencing incident');
  await waitFor(async () => (await run(f.id)).events.some(e => e.event_type === 'agent.paused'), 'A paused after original grant');
  await screenshot('05-A-paused');
  await waitFor(async () => { const r = await run(f.id); return r.status === 'resolved' && r.events.some(e => e.event_type === 'action.rejected' && e.payload.code === 'FENCE_STALE'); }, 'B takeover and A stale write rejected');
  const fenced = await saveRun(f.id, 'fencing');
  const applied = fenced.actions[0];
  assert.notEqual(applied.holder, fenced.fencing_original);
  assert.equal((await state()).config.revision, applied.after_revision);
  report.checks.push({ name: 'actual OS pause, takeover and stale A rejection without write', passed: true, run_id: f.id, old_actor: fenced.fencing_original, new_actor: applied.holder });
  await screenshot('06-fence-rejected');
  await reset();
  await page.getByRole('button', { name: '演示 fencing 剧本', exact: true }).click();
  const stale = await waitFor(async () => (await state()).incident, 'reset-race incident');
  await waitFor(async () => (await run(stale.id)).fencing_paused, 'grant retained before reset');
  await reset();
  const resetRevision = (await state()).config.revision;
  await waitFor(async () => (await run(stale.id)).events.some(e => e.event_type === 'action.rejected' && e.payload.code === 'RESET_GENERATION_STALE'), 'old grant denied after reset');
  assert.equal((await state()).config.revision, resetRevision);
  assert.equal((await state()).config.prompt_version, 'healthy');
  await saveRun(stale.id, 'reset-old-grant');
  report.checks.push({ name: 'reset invalidates in-flight grant and restores healthy configuration', passed: true, run_id: stale.id, reset_revision: resetRevision });
  await screenshot('07-reset-old-grant');
  // Toggle the actual transport at the UI; ablation runs separately test delivery.
  await page.getByRole('button', { name: '正常通信', exact: true }).click();
  await waitFor(async () => (await state()).muted, 'communication muted');
  await page.getByRole('button', { name: '已禁言', exact: true }).click();
  await waitFor(async () => !(await state()).muted, 'communication restored');
  report.checks.push({ name: 'communication control toggles server transport epoch', passed: true });
  await page.getByRole('button', { name: '运行记录', exact: true }).click();
  await page.getByText('查看详情 / RCA', { exact: true }).first().click();
  await page.getByRole('button', { name: '关闭', exact: true }).waitFor();
  await screenshot('08-real-rca');
  await page.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: '五行计分', exact: true }).click();
  await screenshot('09-scoreboard');
  report.checks.push({ name: 'run detail and actual evidence drawer open', passed: true });
  assert.equal(errors.length, 0, errors.join('\n'));
  report.passed = true;
} catch (e) {
  report.passed = false;
  report.failure = String(e.stack || e);
  await screenshot('failure');
  process.exitCode = 1;
} finally {
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await context.close();
  await browser.close();
  console.log(JSON.stringify({ output: out, passed: report.passed, checks: report.checks, failure: report.failure }));
}
