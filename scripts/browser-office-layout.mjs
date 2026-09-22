import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;
const out = path.resolve('artifacts/office-reuse/final-layout');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const report = { at: new Date().toISOString(), errors: [], widths: [] };
page.on('pageerror', error => report.errors.push(String(error)));
try {
  await page.goto('http://127.0.0.1:9019/', { waitUntil: 'networkidle' });
  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(600);
    const room = await page.getByTestId('office-room').boundingBox();
    const agents = await page.locator('[data-agent-id]').evaluateAll(elements => elements.map(element => ({
      id: element.dataset.agentId, x: element.getBoundingClientRect().x,
      y: element.getBoundingClientRect().y, width: element.getBoundingClientRect().width,
      height: element.getBoundingClientRect().height,
    })));
    for (const agent of agents) {
      assert.ok(agent.x >= room.x && agent.y >= room.y && agent.x + agent.width <= room.x + room.width && agent.y + agent.height <= room.y + room.height, 'Agent within room: ' + agent.id);
    }
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.locator('[data-agent-id="diagnoser"]').click();
    const inspector = await page.getByTestId('agent-inspector').boundingBox();
    const afterClickRoom = await page.getByTestId('office-room').boundingBox();
    assert.ok(inspector.y >= afterClickRoom.y + afterClickRoom.height, 'Inspector must not cover room');
    await page.screenshot({ path: path.join(out, `inspector-${width}.png`), fullPage: true });
    await page.getByLabel('关闭角色详情', { exact: true }).click();
    await page.screenshot({ path: path.join(out, `office-${width}.png`), fullPage: true });
    report.widths.push({ width, role_count: agents.length, agents_inside_room: true, inspector_below_room: true });
  }
  await page.getByTestId('office-room').screenshot({ path: path.join(out, 'office-room.png') });
  report.accessibility = (await new AxeBuilder({ page }).include('.s9-office').withTags(['wcag2a', 'wcag2aa']).analyze()).violations;
  assert.equal(report.accessibility.length, 0);
  assert.equal(report.errors.length, 0);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = String(error.stack || error);
  process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  console.log(JSON.stringify(report));
}
