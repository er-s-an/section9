import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import playwright from '../frontend/node_modules/playwright/index.js';
const { chromium } = playwright;

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const out = path.join(root, 'artifacts', 'observability-browser');
const base = 'http://127.0.0.1:9030';

function readEnv() {
  const values = {};
  return fs.readFile(path.join(root, 'infra', '.env'), 'utf8').then(text => {
    for (const line of text.split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
      const [key, ...rest] = trimmed.split('=');
      let value = rest.join('=').trim();
      if (value.startsWith('"') && value.endsWith('"')) value = value.slice(1, -1);
      values[key] = value;
    }
    return values;
  });
}

async function visibleLink(page, predicate) {
  for (const link of await page.locator('a').all()) {
    if (!(await link.isVisible().catch(() => false))) continue;
    const href = await link.getAttribute('href').catch(() => null);
    const text = (await link.innerText().catch(() => '')).trim();
    if (predicate({ href: href || '', text })) return link;
  }
  return null;
}

const env = await readEnv();
await fs.mkdir(out, { recursive: true });
const errors = [];
const responses = [];
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
page.on('console', message => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});
page.on('response', response => {
  if (response.status() >= 400 && response.url().includes('/api/')) responses.push({ status: response.status(), url: response.url() });
});

const report = { checked_at: new Date().toISOString(), base, pages: [], project: null, trace: null, errors, api_errors: responses };
try {
  await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(5000);
  report.pages.push({ stage: 'initial', url: page.url(), title: await page.title(), text: (await page.locator('body').innerText()).slice(0, 2500) });
  const email = page.locator('input[type="email"], input[name*="email" i]').first();
  const password = page.locator('input[type="password"]').first();
  if (await email.count() && await password.count()) {
    // Credentials stay in this process and are never included in report/screenshot.
    await email.fill(env.LANGFUSE_INIT_USER_EMAIL || '');
    await password.fill(env.LANGFUSE_INIT_USER_PASSWORD || '');
    const submit = page.getByRole('button', { name: /sign in|log in|登录|continue/i }).first();
    if (await submit.count()) await submit.click();
    else await password.press('Enter');
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(3000);
  }
  await page.screenshot({ path: path.join(out, '01-authenticated-home.png'), fullPage: true });
  report.pages.push({ stage: 'authenticated-home', url: page.url(), title: await page.title(), text: (await page.locator('body').innerText()).slice(0, 4000) });

  let projectLink = await visibleLink(page, ({ href, text }) => href.includes('section9-observability') || /Section9 Observability/i.test(text));
  if (projectLink) {
    await projectLink.click();
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(3000);
  }
  report.project = { url: page.url(), title: await page.title(), text: (await page.locator('body').innerText()).slice(0, 6000) };
  await page.screenshot({ path: path.join(out, '02-project.png'), fullPage: true });

  // From the project landing page open the trace list first, then select a
  // concrete trace link. A list link alone is not detail evidence.
  let traceLink = await visibleLink(page, ({ href }) => /\/traces\/?$/.test(href));
  if (traceLink) {
    await traceLink.click();
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(2500);
  }
  traceLink = await visibleLink(page, ({ href }) => /\/traces\/[^/?#]+/.test(href));
  if (!traceLink) {
    const row = page.locator('tr').filter({ hasText: /s9\.(probe:verify|user|diagnose|repair)/ }).last();
    if (await row.count() && await row.isVisible().catch(() => false)) traceLink = row;
  }
  if (traceLink) {
    await traceLink.click();
    await page.waitForLoadState('domcontentloaded').catch(() => {});
    await page.waitForTimeout(3000);
  }
  const jsonTab = page.getByText('JSON', { exact: true }).last();
  if (await jsonTab.count() && await jsonTab.isVisible().catch(() => false)) {
    await jsonTab.click();
    await page.waitForTimeout(500);
  }
  const traceText = await page.locator('body').innerText();
  const ids = [...new Set((traceText.match(/run_[a-z0-9]{8,}/gi) || []))];
  const selected = new URL(page.url()).searchParams.get('traceId');
  report.trace = { url: page.url(), title: await page.title(), selected_trace_id: selected, run_ids: ids, text: traceText.slice(0, 10000) };
  await page.screenshot({ path: path.join(out, '03-trace-observation.png'), fullPage: true });
} catch (error) {
  report.status = 'error';
  report.error = `${error.name}: ${error.message}`;
} finally {
  report.errors = errors;
  report.api_errors = responses;
  await fs.writeFile(path.join(out, 'browser-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}

console.log(JSON.stringify({
  status: report.status || 'completed',
  project_url: report.project?.url || null,
  trace_url: report.trace?.url || null,
  run_ids: report.trace?.run_ids || [],
  screenshots: ['01-authenticated-home.png', '02-project.png', '03-trace-observation.png'],
  report: path.join(out, 'browser-report.json'),
  api_error_count: report.api_errors.length,
  page_error_count: report.errors.length,
}));
