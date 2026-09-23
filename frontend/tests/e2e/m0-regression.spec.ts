import { expect, test, type Page } from '@playwright/test'

const pair = (id: string, status: string, specHash: string) => ({
  pair_id: id,
  status,
  spec_hash: specHash,
  swarm_run_id: `run-${id}-swarm`,
  baseline_run_id: `run-${id}-baseline`,
  immutable_spec: { scenario: 'cost', model: 'fixture-model', budget_policy: { total_tokens_per_arm: 8000 } },
  comparison_integrity: { eligible: true },
  links: { swarm: `/showcase/swarm?pair_id=${id}`, baseline: `/showcase/baseline?pair_id=${id}` },
  created_at: '2026-09-23T00:00:00Z',
  started_at: null,
  ended_at: null,
})

const state = {
  generation: '1', autonomy: 'L0', muted: false, monitoring: false, memory_enabled: false,
  config: { revision: '1', prompt_version: 'fixture', context_multiplier: 1, max_output_tokens: 256, retry_limit: 1, retry_on_terminal: false },
  agents: [], incident: null, runs: [], metrics: { total: 0, success: 0, failures: 0, usage_tokens: 0 },
  dependencies: {}, playbooks: [], events: [], attract: { enabled: false, cycle: 0 }, model: 'fixture', version: 'test',
}

async function installEventSourceMock(page: Page) {
  await page.addInitScript(() => {
    const sources: any[] = []
    ;(window as any).__s9Sources = sources
    ;(window as any).__s9Emit = (payload: unknown) => {
      for (const source of sources) {
        for (const listener of source.listeners.update || []) listener({ data: JSON.stringify(payload) })
      }
    }
    class MockEventSource {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      readyState = 1
      onopen: ((event: Event) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      listeners: Record<string, ((event: any) => void)[]> = {}
      constructor(_url: string) {
        sources.push(this)
        setTimeout(() => this.onopen?.(new Event('open')), 0)
      }
      addEventListener(name: string, listener: (event: any) => void) {
        ;(this.listeners[name] ||= []).push(listener)
      }
      close() { this.readyState = 2 }
    }
    ;(window as any).EventSource = MockEventSource
  })
}

test('historical Pair selection remains the operation target across active polling', async ({ page }) => {
  const active = pair('pair-B', 'running', 'spec-B')
  const selected = pair('pair-A', 'ready', 'spec-A')
  const calls: { method: string; path: string; body: any }[] = []
  await installEventSourceMock(page)
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const body = request.postDataJSON?.() ?? null
    calls.push({ method: request.method(), path: url.pathname, body })
    if (url.pathname === '/api/state') return route.fulfill({ json: state })
    if (url.pathname === '/api/scoreboard') return route.fulfill({ json: { versions: [{ id: 'batch-1', n: 0 }], selected_version: 'batch-1', rows: [] } })
    if (url.pathname === '/api/pairs/active') return route.fulfill({ json: { active_pair: active } })
    if (url.pathname === '/api/pairs' && request.method() === 'GET') return route.fulfill({ json: { items: [active, selected] } })
    if (request.method() === 'POST' && url.pathname === '/api/pairs/pair-A/start') return route.fulfill({ json: { pair_id: 'pair-A', status: 'running' } })
    if (request.method() === 'POST' && url.pathname === '/api/pairs/pair-A/arms/swarm/reset') return route.fulfill({ json: { pair_id: 'pair-A', status: 'ready' } })
    if (url.pathname === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': keepalive\n\n' })
    return route.fulfill({ json: {} })
  })

  await page.goto('/?view=score')
  const history = page.getByRole('combobox', { name: '选择历史对照记录' })
  await expect(history).toHaveValue('pair-B')
  await history.selectOption('pair-A')
  await page.waitForTimeout(4_300)
  await expect(history).toHaveValue('pair-A')
  await expect(page.getByText(/当前运行.*pair-B.*操作目标.*pair-A/)).toBeVisible()

  await page.getByRole('button', { name: '开始对照' }).click()
  await expect.poll(() => calls.filter(call => call.path === '/api/pairs/pair-A/start').length).toBe(1)
  expect(calls.find(call => call.path === '/api/pairs/pair-A/start')?.body).toEqual({ expected_spec_hash: 'spec-A' })

  await page.getByText(/重置与维护/).click()
  await page.getByRole('button', { name: '重置协作团队' }).click()
  await page.getByRole('button', { name: '确认重置' }).click()
  await expect.poll(() => calls.filter(call => call.path === '/api/pairs/pair-A/arms/swarm/reset').length).toBe(1)
  expect(calls.some(call => call.path.includes('/pair-B/'))).toBe(false)
})

test('request outcomes and long logbook history stay truthful through paging and updates', async ({ page }) => {
  const p = pair('pair-history', 'completed', 'spec-history')
  const outcomeBySequence: Record<number, string> = { 247: 'success', 248: 'error', 249: 'cancelled', 250: 'unknown' }
  const makeEvent = (sequence: number) => ({
    event_id: `event-${sequence}`, sequence, scope: 'run', pair_id: p.pair_id, run_id: p.swarm_run_id,
    arm: 'swarm', event_type: outcomeBySequence[sequence] ? 'request.completed' : 'task.completed',
    occurred_at: new Date(1_800_000_000_000 + sequence * 1000).toISOString(), producer: 'victim', stage: 'diagnose',
    config_revision: '1', payload: outcomeBySequence[sequence]
      ? { status: outcomeBySequence[sequence], question: `question-${sequence}`, answer: outcomeBySequence[sequence] === 'success' ? 'success-answer' : undefined }
      : { summary: `history-${sequence}` },
  })
  let highWater = 250
  let byIdQuery: URLSearchParams | null = null
  await installEventSourceMock(page)
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname === '/api/pairs') return route.fulfill({ json: { items: [p] } })
    if (url.pathname === `/api/pairs/${p.pair_id}`) return route.fulfill({ json: p })
    if (url.pathname === `/api/pairs/${p.pair_id}/arms/swarm`) {
      const events = Array.from({ length: 200 }, (_, index) => makeEvent(highWater - 199 + index))
      return route.fulfill({ json: {
        pair_id: p.pair_id, run_id: p.swarm_run_id, arm: 'swarm', spec_hash: p.spec_hash,
        pair_status: 'completed', run: { status: 'resolved', elapsed_s: 12 }, config: {},
        stages: [{ id: 'diagnose', label: '原因分析', status: 'passed', source_event_ids: ['event-1', 'event-250'], started_at: '2026-09-23T00:00:00Z', ended_at: '2026-09-23T00:00:01Z' }],
        agents: [], events, as_of_sequence: highWater, usage: {}, dependencies: { model: { status: 'available' } },
        verification: {}, rca: {}, comparison_integrity: { eligible: true }, started_at: '2026-09-23T00:00:00Z',
        log_page: { has_more: highWater > 200, next_after: highWater - 200, range_start: highWater - 199, range_end: highWater, watermark: highWater, view: 'tail' },
      } })
    }
    if (url.pathname.endsWith('/events/by-id')) {
      byIdQuery = url.searchParams
      return route.fulfill({ json: { items: [makeEvent(1), makeEvent(250)], missing_event_ids: [] } })
    }
    if (url.pathname.endsWith('/logbook') && url.searchParams.has('before')) {
      const items = Array.from({ length: 50 }, (_, index) => makeEvent(index + 1))
      return route.fulfill({ json: { items, has_more: false, watermark: Number(url.searchParams.get('watermark')) } })
    }
    if (url.pathname.endsWith('/logbook') && url.searchParams.has('since')) return route.fulfill({ json: { items: [], has_more: false, watermark: highWater, unread_count: 1 } })
    if (url.pathname.endsWith('/logbook') && url.searchParams.has('after')) {
      const after = Number(url.searchParams.get('after'))
      return route.fulfill({ json: { items: after < highWater ? [makeEvent(after + 1)] : [], has_more: false, watermark: highWater } })
    }
    if (url.pathname.endsWith('/events')) return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': keepalive\n\n' })
    return route.fulfill({ json: {} })
  })

  await page.goto(`/showcase/swarm?pair_id=${p.pair_id}`)
  await expect(page.locator('.sc-log-request-success')).toHaveCount(1)
  await expect(page.locator('.sc-log-request-error')).toHaveCount(1)
  await expect(page.locator('.sc-log-request-cancelled')).toHaveCount(1)
  await expect(page.locator('.sc-log-request-unknown')).toHaveCount(1)
  const log = page.locator('.sc-log-scroll')
  await expect(log.getByText('本次请求失败，未生成回复')).toBeVisible()
  await expect(log.getByText('本次请求已取消，未生成回复')).toBeVisible()
  await expect(log.getByText('本次请求结果未知，未确认是否生成回复')).toBeVisible()
  expect(await page.locator('.sc-log-request-error summary>b').evaluate(node => getComputedStyle(node).color)).toBe('rgb(255, 156, 136)')
  expect(await page.locator('.sc-log-request-success summary>b').evaluate(node => getComputedStyle(node).color)).toBe('rgb(183, 219, 160)')
  const failureFilter = page.getByRole('combobox', { name: '活动记录筛选' })
  await failureFilter.selectOption('failure')
  await expect(page.locator('.sc-log-request-success')).toHaveCount(0)
  await expect(page.locator('.sc-log-request-error,.sc-log-request-cancelled,.sc-log-request-unknown')).toHaveCount(3)
  await failureFilter.selectOption('all')

  await page.locator('[data-stage="diagnose"]').click()
  await expect(page.getByRole('dialog', { name: '原因分析记录' })).toBeVisible()
  await expect(page.getByText('已关联 2 条事件')).toBeVisible()
  expect(byIdQuery?.getAll('event_id')).toEqual(['event-1', 'event-250'])
  await page.getByRole('button', { name: '关闭证据' }).click()

  await page.getByRole('button', { name: '加载更早记录' }).click()
  await expect(page.getByText(/已载入 250 条/)).toBeVisible()
  await expect(page.locator('[data-event-id="event-1"]')).toHaveCount(1)

  await expect(page.getByRole('button', { name: '继续跟随' })).toBeVisible()
  highWater = 251
  await page.evaluate(() => (window as any).__s9Emit({ pair_id: 'pair-history', run_id: 'run-pair-history-swarm', arm: 'swarm', sequence: 251 }))
  await expect(page.getByRole('button', { name: '1 条新活动 ↓' })).toBeVisible()
  await expect(page.locator('[data-event-id="event-251"]')).toHaveCount(1)
  await expect(page.getByText(/已载入 251 条/)).toBeVisible()
})

test('main event feed gives request failures, cancellations, and unknown outcomes distinct labels', async ({ page }) => {
  const statuses = ['success', 'error', 'cancelled', 'unrecognized']
  const events = statuses.map((status, index) => ({
    event_id: `main-${index}`, sequence: index + 1, event_type: 'request.completed', occurred_at: '2026-09-23T00:00:00Z',
    run_id: null, incident_id: null, producer: 'model', generation: '1', payload: { status },
  }))
  await installEventSourceMock(page)
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/state') return route.fulfill({ json: { ...state, events } })
    if (url.pathname === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': keepalive\n\n' })
    return route.fulfill({ json: {} })
  })
  await page.goto('/?view=overview')
  const feed = page.locator('.event-feed')
  await expect(feed.locator('.request-outcome-success')).toHaveCount(1)
  await expect(feed.locator('.request-outcome-error')).toHaveCount(1)
  await expect(feed.locator('.request-outcome-cancelled')).toHaveCount(1)
  await expect(feed.locator('.request-outcome-unknown')).toHaveCount(1)
  await expect(feed.getByText('本次请求失败，未生成回复')).toBeVisible()
  await expect(feed.getByText('请求已取消，未生成回复')).toBeVisible()
  await expect(feed.getByText('请求结果未知，未确认回复')).toBeVisible()
})
