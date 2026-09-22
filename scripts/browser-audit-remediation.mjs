import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(path.join(here, '../frontend/package.json'))
const { chromium } = require('@playwright/test')

const BASE = process.env.BASE_URL || 'http://127.0.0.1:9019'
const stamp = new Date().toISOString().replace(/[:.]/g, '-')
const out = path.resolve(here, `../artifacts/audit-browser/${stamp}`)
await fs.mkdir(out, { recursive: true })
const report = { base: BASE, started_at: new Date().toISOString(), checks: [], failures: [], requests: [], console: [], page_errors: [], screenshots: [], health: null, state: null, runs: [] }
const check = (name, passed, details = {}) => {
  const row = { name, passed: Boolean(passed), ...details }
  report.checks.push(row)
  if (!passed) report.failures.push(row)
  console.log(`${passed ? 'PASS' : 'FAIL'} ${name}`)
  return passed
}
const write = async (name, value) => fs.writeFile(path.join(out, name), JSON.stringify(value, null, 2), 'utf8')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const getJson = async (request, endpoint) => {
  const response = await request.get(new URL(endpoint, BASE).toString())
  let body = null
  try { body = await response.json() } catch {}
  return { status: response.status(), body }
}
const postJson = async (request, endpoint, data) => {
  const response = await request.post(new URL(endpoint, BASE).toString(), { data })
  let body = null
  try { body = await response.json() } catch {}
  return { status: response.status(), body }
}
const isActive = state => Number(state?.dependencies?.model?.active_requests || 0) > 0 || Number(state?.victim?.active_requests || 0) > 0
const waitFor = async (fn, timeout = 30000, interval = 250) => {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await fn()
    if (value) return value
    await sleep(interval)
  }
  return null
}
const stateFrom = async request => (await getJson(request, '/api/state')).body
const newestRun = runs => Array.isArray(runs?.items) ? runs.items[0] : null

const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce' })
const page = await context.newPage()
page.on('console', msg => report.console.push({ type: msg.type(), text: msg.text() }))
page.on('pageerror', error => report.page_errors.push(String(error)))
page.on('request', request => {
  if (request.url().includes('/api/')) report.requests.push({ method: request.method(), url: request.url(), postData: request.postData(), at: new Date().toISOString() })
})
page.on('requestfailed', request => report.requests.push({ method: request.method(), url: request.url(), failed: request.failure()?.errorText || 'unknown', at: new Date().toISOString() }))

try {
  const health = await getJson(context.request, '/api/health')
  report.health = health.body
  await write('health.identity.json', { status: health.status, identity: health.body?.identity, dependencies: health.body?.dependencies })
  check('health identity available', health.status === 200 && Boolean(health.body?.identity), { status: health.status })

  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('input[aria-label="客服消息"]', { timeout: 20000 })
  const resetButton = page.getByRole('button', { name: '重置实验室' })
  const chatInput = page.locator('input[aria-label="客服消息"]').first()

  // Chat cancellation: use the real UI, wait until the backend reports active work, then reset.
  let before = await stateFrom(context.request)
  const oldGeneration = before?.generation
  await chatInput.fill('请说明已激活耳机是否仍可退货？')
  await page.locator('.chat-dock button').click()
  const activeBeforeReset = await waitFor(async () => {
    const state = await stateFrom(context.request)
    return isActive(state) ? state : null
  }, 30000)
  check('chat reaches active request', Boolean(activeBeforeReset), { active: activeBeforeReset?.dependencies?.model?.active_requests ?? activeBeforeReset?.dependencies?.victim?.active_requests ?? 0 })
  check('reset remains enabled during chat', await resetButton.isEnabled())
  const resetStarted = Date.now()
  let resetResponse
  const resetResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/reset') && response.request().method() === 'POST', { timeout: 20000 }).catch(() => null)
  await resetButton.click()
  resetResponse = await resetResponsePromise
  const resetMs = Date.now() - resetStarted
  const afterReset = await waitFor(async () => {
    const state = await stateFrom(context.request)
    return state?.generation !== oldGeneration && !isActive(state) ? state : null
  }, 30000)
  const cancellationText = await page.getByText('本轮已重置，请求已取消。').count()
  check('reset API responded', Boolean(resetResponse) && resetResponse.ok(), { elapsed_ms: resetMs, status: resetResponse?.status() })
  check('reset advances generation and releases active work', Boolean(afterReset), { old_generation: oldGeneration, new_generation: afterReset?.generation, elapsed_ms: resetMs })
  check('reset cancellation is visible', cancellationText > 0)
  await write('chat-reset.json', { old_generation: oldGeneration, new_generation: afterReset?.generation, active_before_reset: activeBeforeReset, active_after_reset: afterReset, reset_ms: resetMs, reset_status: resetResponse?.status(), cancellation_visible: cancellationText > 0 })

  // A fresh UI chat must still be accepted after reset; await its real response/error state.
  await chatInput.fill('重置后请确认客服通道已经恢复。')
  const chatResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/chat') && response.request().method() === 'POST', { timeout: 90000 }).catch(() => null)
  await page.locator('.chat-dock button').click()
  const chatResponse = await chatResponsePromise
  const freshChat = chatResponse ? await chatResponse.json() : null
  check('post-reset chat returns real business answer', Boolean(chatResponse?.ok()) && freshChat?.status === 'success' && Boolean(freshChat?.answer), { status: freshChat?.status, elapsed_s: freshChat?.elapsed_s })
  await write('post-reset-chat.json', freshChat)
  await resetButton.click().catch(() => {})
  await waitFor(async () => !(await stateFrom(context.request))?.incident, 15000)

  // Muted run: set the policy through the UI, then inject through the UI and inspect its persisted run/events.
  const communication = page.getByRole('button', { name: /正常通信|已禁言/ }).first()
  if ((await communication.innerText()).includes('已禁言')) await communication.click()
  await page.getByRole('button', { name: '正常通信' }).click()
  await page.waitForTimeout(300)
  const mutedToggle = page.getByRole('button', { name: '已禁言' })
  check('UI enters muted mode', await mutedToggle.count() === 1)
  let injectRequest
  const injectResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/inject') && response.request().method() === 'POST', { timeout: 20000 }).catch(() => null)
  await page.getByRole('button', { name: '复合故障' }).click()
  injectRequest = report.requests.slice().reverse().find(row => row.url.endsWith('/api/inject') && row.method === 'POST')
  const injectResponse = await injectResponsePromise
  check('muted UI injection sends condition=muted', injectRequest?.postData?.includes('"condition":"muted"'), { post_data: injectRequest?.postData, status: injectResponse?.status() })
  const mutedState = await waitFor(async () => { const s = await stateFrom(context.request); return s?.incident?.run_id ? s : null }, 20000)
  const mutedRunId = mutedState?.incident?.run_id
  const mutedRun = mutedRunId ? await waitFor(async () => { const r = (await getJson(context.request, `/api/runs/${mutedRunId}`)).body; return r?.events?.some(e => e.event_type === 'dialog.dropped') ? r : null }, 60000) : null
  const mutedEvents = mutedRun?.events || []
  check('muted run persists muted condition', mutedRun?.condition === 'muted', { run_id: mutedRunId, condition: mutedRun?.condition })
  check('muted run drops dialog and receives none', mutedEvents.some(e => e.event_type === 'dialog.dropped') && !mutedEvents.some(e => e.event_type === 'dialog.received'), { run_id: mutedRunId, event_types: [...new Set(mutedEvents.map(e => e.event_type))] })
  await write('muted-run.json', { run: mutedRun, events: mutedEvents })
  await resetButton.click()
  await waitFor(async () => !(await stateFrom(context.request))?.incident, 20000)

  // The UI must reject memory + muted before making an injection request; the backend must reject it too.
  const memoryButton = page.locator('.control-line').filter({hasText:'下轮 Playbook'}).getByRole('button')
  if ((await memoryButton.innerText()).includes('关闭')) await memoryButton.click()
  const communicationAfterReset = page.getByRole('button', { name: /正常通信|已禁言/ }).first()
  if ((await communicationAfterReset.innerText()).includes('正常通信')) await communicationAfterReset.click()
  await page.waitForTimeout(300)
  check('memory and muted are both enabled', (await page.getByRole('button', { name: '记忆复用' }).count()) === 1 && (await page.getByRole('button', { name: '已禁言' }).count()) === 1)
  const runsBeforeReject = await getJson(context.request, '/api/runs')
  const blockedRequestCount = report.requests.filter(row => row.url.endsWith('/api/inject') && row.method === 'POST').length
  await page.getByRole('button', { name: '复合故障' }).click()
  await page.waitForTimeout(400)
  const runsAfterReject = await getJson(context.request, '/api/runs')
  const blockedRequestCountAfter = report.requests.filter(row => row.url.endsWith('/api/inject') && row.method === 'POST').length
  check('UI blocks memory+muted without POST', blockedRequestCountAfter === blockedRequestCount && await page.getByText(/记忆复用与禁言不能同时注入/).count() > 0)
  const directRejected = await postJson(context.request, '/api/inject', { scenario: 'composite', condition: 'memory' })
  check('backend rejects memory+muted with 422', directRejected.status === 422, { status: directRejected.status, body: directRejected.body })
  check('rejected memory+muted does not create run', (runsAfterReject.body?.items?.length ?? 0) === (runsBeforeReject.body?.items?.length ?? 0))
  await resetButton.click()
  await waitFor(async () => !(await stateFrom(context.request))?.incident, 15000)

  // Versioned scoreboard: current and legacy are read from the real API; no fixture rows are invented.
  await page.getByRole('button', { name: '五行计分' }).click()
  await page.waitForSelector('#score-version', { timeout: 15000 })
  const currentScore = await getJson(context.request, '/api/scoreboard')
  check('current scoreboard identifies selected/current version', currentScore.status === 200 && currentScore.body?.selected_version === currentScore.body?.current_version, { selected: currentScore.body?.selected_version, current: currentScore.body?.current_version })
  const currentRows = currentScore.body?.rows || []
  const currentUnknown = currentRows.reduce((n,r)=>n+(r.unknown_usage_runs||0),0)
  check('current scoreboard exposes retained unknown usage', currentUnknown > 0 && await page.getByText(/未知usage/).count() > 0, {unknown_runs:currentUnknown})
  check('current version keeps unmeasured cells empty', currentRows.filter(r => (r.cells || []).some(c => c.n === 0 && (c.run_ids || []).length > 0)).length === 0)
  const options = await page.locator('#score-version option').evaluateAll(nodes => nodes.map(node => ({ value: node.value, label: node.textContent })))
  const legacy = options.find(option => option.value === 'legacy')
  if (legacy) {
    const scoreRequestsBefore = report.requests.filter(row => row.url.includes('/api/scoreboard')).length
    await page.locator('#score-version').selectOption('legacy')
    await page.waitForTimeout(500)
    const legacyScore = await getJson(context.request, '/api/scoreboard?version=legacy')
    check('legacy version selection requests legacy data', legacyScore.body?.selected_version === 'legacy' && report.requests.some(row => row.url.includes('/api/scoreboard?version=legacy')), { selected: legacyScore.body?.selected_version })
    const unknownRuns = (legacyScore.body?.rows || []).reduce((n,r)=>n+(r.unknown_usage_runs||0),0)
    check('legacy unknown usage agrees with real records', unknownRuns === 0 || await page.getByText(/未知usage/).count() > 0, {unknown_runs:unknownRuns, positive_unknown_render_test:unknownRuns>0})
    const scoreRequestsAfter = report.requests.filter(row => row.url.includes('/api/scoreboard')).length
    await page.waitForTimeout(4000)
    const scoreRequestsFinal = report.requests.filter(row => row.url.includes('/api/scoreboard')).length
    check('scoreboard polls while tab is open', scoreRequestsFinal > scoreRequestsAfter && scoreRequestsAfter >= scoreRequestsBefore, { before: scoreRequestsBefore, after_select: scoreRequestsAfter, after_wait: scoreRequestsFinal })
  } else {
    check('legacy version exists for unknown usage audit', false, { options })
  }

  await page.screenshot({path:path.join(out, 'scoreboard-unknown-usage.png'), fullPage:true})
  await page.getByRole('button', {name:'办公室', exact:true}).click()
  for (const [width, height] of [[1280, 800], [1440, 1000]]) {
    await page.setViewportSize({ width, height })
    await page.screenshot({ path: path.join(out, `layout-${width}x${height}.png`), fullPage: true })
    report.screenshots.push(`layout-${width}x${height}.png`)
  }
  const finalState = await stateFrom(context.request)
  report.state = finalState
  report.runs = (await getJson(context.request, '/api/runs')).body?.items || []
  await write('events-and-runs.json', { state: finalState, runs: report.runs, api_requests: report.requests })
} catch (error) {
  report.failures.push({ name: 'audit exception', passed: false, error: String(error), stack: error?.stack })
  try { await page.screenshot({ path: path.join(out, 'failure.png'), fullPage: true }); report.screenshots.push('failure.png') } catch {}
} finally {
  await context.request.put(BASE + '/api/memory', {data:{enabled:false}}).catch(()=>{})
  await context.request.post(BASE + '/api/reset', {data:{}}).catch(()=>{})
  report.finished_at = new Date().toISOString()
  report.console_errors = report.console.filter(item => item.type === 'error')
  await write('report.json', report)
  await fs.writeFile(path.join(out, 'report.md'), `# Browser remediation audit\n\n- Base: ${BASE}\n- Started: ${report.started_at}\n- Finished: ${report.finished_at}\n- Checks: ${report.checks.filter(x => x.passed).length}/${report.checks.length} passed\n- Failures: ${report.failures.length}\n- Console errors: ${report.console_errors.length}\n\n${report.checks.map(x => `- [${x.passed ? 'x' : ' '}] ${x.name}`).join('\n')}\n`, 'utf8')
  await browser.close()
  console.log(`Artifacts: ${out}`)
  if (report.failures.length) process.exitCode = 1
}
