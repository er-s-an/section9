import { expect, test } from '@playwright/test'

const state = {
  generation: '1', autonomy: 'L0', muted: false, monitoring: false, memory_enabled: false,
  config: { revision: '1', prompt_version: 'fixture', context_multiplier: 1, max_output_tokens: 256, retry_limit: 1, retry_on_terminal: false },
  agents: [], incident: null, runs: [], metrics: { total: 0, success: 0, failures: 0, usage_tokens: 0 },
  dependencies: {}, playbooks: [], events: [], attract: { enabled: false, cycle: 0 }, model: 'fixture', version: 'test',
}

test('workspace setup creates scoped local records and keeps them pending until validation', async ({ page }) => {
  const workspaces: any[] = []
  const applications: any[] = []
  const connections: any[] = []
  const bindings: any[] = []
  const signals: any[] = []
  const incidents: any[] = []
  const investigationRuns: any[] = []
  const runDetails = new Map<string, any>()
  const checkpoints: any[] = []
  const monitors: any[] = []
  const events: any[] = []
  let githubImportCount = 0
  const writes: { path: string; key: string | undefined; body: any }[] = []
  const recordEvent = (event_type: string, resource_type: string, resource_id: string) => {
    const sequence = events.length + 1
    events.push({ event_id: `evt-${sequence}`, sequence, event_type, occurred_at: '2026-09-23T00:00:00Z', resource_type, resource_id })
  }

  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/state') return route.fulfill({ json: state })
    if (path === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': keepalive\n\n' })
    if (path === '/api/product/snapshot') return route.fulfill({ json: { status: 'ready', runtime: { status: 'running' }, connections: [], incidents: [], collaboration: {} } })
    if (path === '/api/v1/workspaces' && request.method() === 'GET') return route.fulfill({ json: { items: workspaces } })
    if (path === '/api/v1/workspaces' && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = { id: 'wsp-test', workspace_id: 'wsp-test', revision: 1, policy_revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', name: body.name, budget: body.budget, deployment_mode: body.deployment_mode }
      workspaces.push(item); recordEvent('workspace.created', 'workspace', item.id)
      return route.fulfill({ status: 201, json: item })
    }
    if (path === '/api/v1/workspaces/wsp-test/budget' && request.method() === 'PUT') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = workspaces[0]
      item.revision += 1; item.policy_revision += 1; item.budget = body.budget
      recordEvent('workspace.budget_updated', 'workspace', item.id)
      return route.fulfill({ json: item })
    }
    if (path === '/api/v1/workspaces/wsp-test/applications' && request.method() === 'GET') return route.fulfill({ json: { items: applications } })
    if (path === '/api/v1/workspaces/wsp-test/applications' && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = { id: 'app-test', workspace_id: 'wsp-test', revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', name: body.name, owner_id: null }
      applications.push(item); recordEvent('application.created', 'application', item.id)
      return route.fulfill({ status: 201, json: item })
    }
    if (path.endsWith('/events')) return route.fulfill({ json: { items: events, next_after: events.at(-1)?.sequence ?? 0 } })
    if (path.endsWith('/connections') && request.method() === 'GET') return route.fulfill({ json: { items: connections } })
    if (path.endsWith('/connections') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = { id: `conn-${connections.length + 1}`, workspace_id: 'wsp-test', application_id: 'app-test', revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', ...body, status: 'pending', checked_at: null }
      connections.push(item); recordEvent('connection.created', 'connection', item.id)
      return route.fulfill({ status: 201, json: item })
    }
    if (path.endsWith('/scope-bindings') && request.method() === 'GET') return route.fulfill({ json: { items: bindings } })
    if (path.endsWith('/scope-bindings') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = { id: `bind-${bindings.length + 1}`, workspace_id: 'wsp-test', application_id: 'app-test', revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', ...body, status: 'pending' }
      bindings.push(item); recordEvent('scope_binding.created', 'scope_binding', item.id)
      return route.fulfill({ status: 201, json: item })
    }
    if (path.endsWith('/signals') && request.method() === 'GET') return route.fulfill({ json: { items: signals } })
    if (path.endsWith('/incidents') && request.method() === 'GET') return route.fulfill({ json: { items: incidents } })
    if (path.endsWith('/signal-import-checkpoints') && request.method() === 'GET') return route.fulfill({ json: { items: checkpoints } })
    if (path.endsWith('/signal-monitors') && request.method() === 'GET') return route.fulfill({ json: { items: monitors } })
    if (/\/scope-bindings\/[^/]+\/signal-monitor$/.test(path) && request.method() === 'PUT') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const bindingId = path.match(/\/scope-bindings\/([^/]+)\/signal-monitor$/)?.[1]
      const existing = monitors.find(item => item.binding_id === bindingId)
      if ((existing?.revision ?? 0) !== body.expected_revision) return route.fulfill({ status: 409, json: { error: { message: '监护配置已变化，请刷新后重试' } } })
      const connection = connections.find(item => item.id === bindings.find(binding => binding.id === bindingId)?.connection_id)
      const monitor = { id: existing?.id ?? `mon-${monitors.length + 1}`, binding_id: bindingId, provider: connection?.provider.toLowerCase() ?? 'langfuse', revision: (existing?.revision ?? 0) + 1, enabled: body.enabled, interval_seconds: body.interval_seconds, status: body.enabled ? 'starting' : 'paused', next_run_at: body.enabled ? '2026-09-23T00:05:00Z' : null, last_successful_watermark: existing?.last_successful_watermark ?? '2026-09-23T00:00:00Z', current_window: null, consecutive_failures: 0, last_run: null }
      if (existing) Object.assign(existing, monitor); else monitors.push(monitor)
      recordEvent(body.enabled ? 'signal.monitor.enabled' : 'signal.monitor.paused', 'signal_sync_monitor', monitor.id)
      return route.fulfill({ json: monitor })
    }
    if (path.endsWith('/signals/import') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const continued = Boolean(body.cursor)
      const item = { id: continued ? 'sig-test-2' : 'sig-test', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: 'production', source_binding_id: 'bind-1', source_id: continued ? 'obs-2' : 'obs-1', source_version: 'v1', revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', deduplication_key: continued ? 'langfuse:obs-2' : 'langfuse:obs-1', signal_type: 'langfuse_observation', source_kind: 'poll', occurred_at: continued ? '2026-09-22T23:45:00Z' : '2026-09-22T23:30:00Z', observed_at: '2026-09-23T00:00:00Z', summary: 'Langfuse observation · 待人工判断业务含义', status: 'new' }
      signals.push(item); recordEvent('signals.ingested', 'scope_binding', 'bind-1')
      const checkpoint = { source_binding_id: 'bind-1', from_start_time: body.from_start_time, to_start_time: body.to_start_time, revision: continued ? 2 : 1, next_cursor: continued ? null : 'cursor-next', complete: continued, coverage: { complete: continued, next_cursor: continued ? null : 'cursor-next', continuation_required: !continued, pages_read: 1, rows_seen: 1, invalid_rows: 0 }, updated_at: '2026-09-23T00:00:00Z' }
      if (checkpoints.length) Object.assign(checkpoints[0], checkpoint); else checkpoints.push(checkpoint)
      return route.fulfill({ status: 201, json: { items: [item], created_count: 1, duplicate_count: 0, coverage: checkpoint.coverage, checkpoint } })
    }
    if (path.endsWith('/signals/import/github') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const isRepeat = githubImportCount++ > 0
      const item = { id: isRepeat ? 'sig-gh-2' : 'sig-gh-1', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: 'production', source_binding_id: 'bind-2', source_id: '42', source_version: isRepeat ? '2026-09-23T00:05:00Z' : '2026-09-23T00:00:00Z', revision: isRepeat ? 2 : 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', deduplication_key: isRepeat ? 'github-issue:stable-hash-v2' : 'github-issue:stable-hash', signal_type: 'github_issue', source_kind: 'poll', occurred_at: isRepeat ? '2026-09-23T00:05:00Z' : '2026-09-23T00:00:00Z', observed_at: '2026-09-23T00:05:00Z', summary: 'GitHub issue updated · 待人工判断业务含义', status: isRepeat ? 'clustered' : 'new' }
      signals.push(item); recordEvent('signals.ingested', 'scope_binding', 'bind-2')
      if (isRepeat) {
        const incident = incidents.find(candidate => candidate.id === 'inc-test')
        if (incident) { incident.signal_ids.push(item.id); incident.revision += 1 }
        recordEvent('signal.auto_linked', 'signal', item.id)
        recordEvent('incident.signal_auto_linked', 'incident', 'inc-test')
      }
      const coverage = { complete: true, next_cursor: null, continuation_required: false, pages_read: 1, rows_seen: 1, invalid_rows: 0, consistency: 'github_updated_at_ascending_best_effort' }
      const checkpoint = { source_binding_id: 'bind-2', from_start_time: body.from_updated_at, to_start_time: body.to_updated_at, revision: 1, next_cursor: null, complete: true, coverage, updated_at: '2026-09-23T00:00:00Z' }
      checkpoints.push(checkpoint)
      return route.fulfill({ status: 201, json: { items: [item], created_count: 1, duplicate_count: 0, auto_linked_count: isRepeat ? 1 : 0, correlation_ambiguous_count: 0, coverage, checkpoint } })
    }
    if (path.endsWith('/incidents') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const item = { id: `inc-test${incidents.length ? `-${incidents.length + 1}` : ''}`, workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', title: body.title, severity: body.severity, signal_ids: body.signals.map((selection: any) => selection.signal_id), state: 'open', outcome: null, merged_into_id: null, assignee_id: null }
      incidents.push(item)
      for (const signal of signals) if (item.signal_ids.includes(signal.id)) { signal.status = 'clustered'; signal.revision += 1 }
      recordEvent('incident.created', 'incident', item.id)
      return route.fulfill({ status: 201, json: item })
    }
    if (/\/incidents\/[^/]+\/assignment$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const match = path.match(/\/incidents\/([^/]+)\/assignment$/)
      const incident = incidents.find(item => item.id === match?.[1])
      if (!incident || incident.revision !== body.expected_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后重试认领' } } })
      incident.revision += 1
      incident.assignee_id = body.action === 'claim' ? 'local-operator' : null
      recordEvent(body.action === 'claim' ? 'incident.claimed' : 'incident.released', 'incident', incident.id)
      return route.fulfill({ json: incident })
    }
    if (/\/incidents\/[^/]+\/severity$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const match = path.match(/\/incidents\/([^/]+)\/severity$/)
      const incident = incidents.find(item => item.id === match?.[1])
      if (!incident || incident.revision !== body.expected_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后调整严重度' } } })
      incident.revision += 1; incident.severity = body.severity
      recordEvent('incident.severity_changed', 'incident', incident.id)
      return route.fulfill({ json: incident })
    }
    if (/\/incidents\/[^/]+\/split$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const match = path.match(/\/incidents\/([^/]+)\/split$/)
      const source = incidents.find(item => item.id === match?.[1])
      if (!source || source.revision !== body.expected_incident_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后再拆分' } } })
      const moved = body.signals.map((selection: any) => selection.signal_id)
      source.revision += 1
      source.signal_ids = source.signal_ids.filter((id: string) => !moved.includes(id))
      const created = { id: `inc-test-${incidents.length + 1}`, workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', title: body.title, severity: body.severity, signal_ids: moved, state: 'open', outcome: null, merged_into_id: null }
      incidents.push(created)
      recordEvent('incident.signals_split', 'incident', source.id)
      recordEvent('incident.created_by_split', 'incident', created.id)
      return route.fulfill({ json: { source, created } })
    }
    if (/\/incidents\/[^/]+\/merge$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const match = path.match(/\/incidents\/([^/]+)\/merge$/)
      const source = incidents.find(item => item.id === match?.[1])
      const target = incidents.find(item => item.id === body.target_incident_id)
      if (!source || !target || source.revision !== body.expected_incident_revision || target.revision !== body.expected_target_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后重试' } } })
      const moved = [...source.signal_ids]
      source.revision += 1; source.signal_ids = []; source.state = 'merged'; source.outcome = 'merged'; source.merged_into_id = target.id
      target.revision += 1; target.signal_ids.push(...moved)
      recordEvent('incident.merged', 'incident', source.id)
      recordEvent('incident.signals_merged', 'incident', target.id)
      return route.fulfill({ json: { source, target } })
    }
    if (/\/incidents\/[^/]+\/signals$/.test(path) && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const incident = incidents.find(item => path.includes(`/incidents/${item.id}/signals`))
      if (!incident || incident.revision !== body.expected_incident_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后重新关联信号' } } })
      incident.revision += 1
      incident.signal_ids.push(...body.signals.map((selection: any) => selection.signal_id))
      for (const selection of body.signals) {
        const signal = signals.find(item => item.id === selection.signal_id)
        if (signal && signal.status === 'new' && signal.revision === selection.expected_revision) {
          signal.status = 'clustered'; signal.revision += 1
        }
      }
      recordEvent('incident.signals_attached', 'incident', incident.id)
      return route.fulfill({ json: incident })
    }
    if (path.endsWith('/transitions') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const incident = incidents.find(item => path.includes(`/incidents/${item.id}/transitions`))
      if (!incident || incident.revision !== body.expected_revision) return route.fulfill({ status: 409, json: { error: { message: '事故已变化，请刷新后重新提交状态变更' } } })
      incident.revision += 1
      incident.state = body.target_state
      incident.outcome = body.target_state === 'dismissed' ? 'dismissed' : null
      recordEvent('incident.state_changed', 'incident', incident.id)
      return route.fulfill({ json: incident })
    }
    if (path.endsWith('/investigation-runs') && request.method() === 'GET') return route.fulfill({ json: { items: investigationRuns } })
    if (path.endsWith('/investigation-runs') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const incidentId = path.match(/\/incidents\/([^/]+)\/investigation-runs$/)?.[1]
      const incident = incidents.find(item => item.id === incidentId)
      const run = { id: 'run-ai', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', execution_mode: body.mode, input_snapshot_sha256: 'c'.repeat(64), policy_revision: workspaces[0].policy_revision, token_limit: workspaces[0].budget.token_limit, validation_reserve_tokens: 0, state: 'running', result_type: null, started_at: '2026-09-23T00:00:00Z', completed_at: null }
      const evidence = { id: 'ev-ai', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, run_id: run.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', source_binding_id: 'bind-1', source_version: 'v1', origin: 'section9-signal://sig-test', content_sha256: 'd'.repeat(64), coverage_complete: false }
      const task = (id: string, task_key: string, role: string, dependencies: string[]) => ({ id, workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, run_id: run.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', task_key, role, capability: role === 'investigator' ? 'evidence.investigate' : 'conclusion.synthesize', title: task_key, dependencies, input_evidence_ids: ['ev-ai'], epoch: 0, holder_id: null, lease_deadline: null, state: 'open', result: null, failure_reason: null, attempt_history: [] })
      const tasks = [task('task-ai-investigate', 'investigate', 'investigator', []), task('task-ai-synthesize', 'synthesize', 'synthesizer', ['task-ai-investigate'])]
      const graph = { id: 'graph-ai', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, run_id: run.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', mode: body.mode, graph_sha256: 'e'.repeat(64), state: 'ready', tasks }
      const detail = { run, input_snapshot: { incident, signals }, evidence: [evidence], hypotheses: [], task_graph: graph, model_usage: [] }
      investigationRuns.push(run); runDetails.set(run.id, detail)
      recordEvent('investigation_run.started', 'investigation_run', run.id)
      return route.fulfill({ status: 201, json: detail })
    }
    if (path.endsWith('/investigation-runs/manual') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const incident = incidents.find(item => path.includes(`/incidents/${item.id}/investigation-runs/manual`))
      const run = { id: 'run-test', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', execution_mode: 'manual', input_snapshot_sha256: 'a'.repeat(64), policy_revision: 1, token_limit: 0, validation_reserve_tokens: 0, state: 'running', result_type: null, started_at: '2026-09-23T00:00:00Z', completed_at: null }
      const evidence = { id: 'ev-test', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: incident.id, run_id: run.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', source_binding_id: 'bind-1', source_version: 'v1', origin: 'section9-signal://sig-test', source_occurred_at: '2026-09-22T23:30:00Z', captured_at: '2026-09-23T00:00:00Z', content_sha256: 'b'.repeat(64), classification: 'internal', raw_blob_ref: null, sanitized_blob_ref: null, coverage_complete: false }
      const detail = { run, input_snapshot: { incident, signals }, evidence: [evidence], hypotheses: [] }
      investigationRuns.push(run); runDetails.set(run.id, detail)
      recordEvent('investigation_run.started', 'investigation_run', run.id)
      recordEvent('evidence.captured', 'evidence', evidence.id)
      return route.fulfill({ status: 201, json: detail })
    }
    if (path.endsWith('/hypotheses') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const detail = runDetails.get('run-test')
      const hypothesis = { id: 'hyp-test', workspace_id: 'wsp-test', application_id: 'app-test', environment_id: body.environment_id, incident_id: detail.run.incident_id, run_id: detail.run.id, revision: 1, created_at: '2026-09-23T00:00:00Z', updated_at: '2026-09-23T00:00:00Z', statement: body.statement, confidence: body.confidence, support_evidence_ids: body.support_evidence_ids, counterevidence_ids: body.counterevidence_ids, state: 'proposed' }
      detail.hypotheses.push(hypothesis); recordEvent('hypothesis.proposed', 'hypothesis', hypothesis.id)
      return route.fulfill({ status: 201, json: hypothesis })
    }
    if (path.endsWith('/decision') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const hypothesis = runDetails.get('run-test').hypotheses[0]
      hypothesis.revision += 1; hypothesis.state = body.target_state; hypothesis.confidence = body.confidence
      recordEvent('hypothesis.decided', 'hypothesis', hypothesis.id)
      return route.fulfill({ json: hypothesis })
    }
    if (path.endsWith('/finish') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const runId = path.match(/\/investigation-runs\/([^/]+)\/finish$/)?.[1]
      const run = runDetails.get(runId)?.run
      run.revision += 1; run.state = 'succeeded'; run.result_type = body.result_type; run.completed_at = '2026-09-23T00:00:00Z'
      recordEvent('investigation_run.finished', 'investigation_run', run.id)
      return route.fulfill({ json: run })
    }
    if (path.endsWith('/execute') && request.method() === 'POST') {
      writes.push({ path, key: request.headers()['idempotency-key'], body: request.postDataJSON() })
      const detail = runDetails.get('run-ai')
      detail.task_graph.state = 'complete'
      detail.task_graph.tasks[0].state = 'succeeded'
      detail.task_graph.tasks[0].result = { summary: '模型发现一项待复核假设。', evidence_ids: ['ev-ai'], conclusion: null }
      detail.task_graph.tasks[1].state = 'succeeded'
      detail.task_graph.tasks[1].result = { summary: '证据仍需人工复核。', evidence_ids: ['ev-ai'], conclusion: 'needs_data' }
      detail.model_usage = [{ request_id: 'usage-ai', task_id: 'task-ai-investigate', task_epoch: 1, provider: 'mock-provider', model: 'mock-model', reserved_tokens: 1500, actual_tokens: 31, state: 'settled', provider_called: true, error_code: null }]
      recordEvent('investigation.task_graph.completed', 'investigation_task_graph', 'graph-ai')
      return route.fulfill({ json: { detail, execution: { state: 'complete', completed: 2, remaining: 0 } } })
    }
    if (path.endsWith('/investigation-runs/run-test') && request.method() === 'GET') return route.fulfill({ json: runDetails.get('run-test') })
    if (path.endsWith('/investigation-runs/run-ai') && request.method() === 'GET') return route.fulfill({ json: runDetails.get('run-ai') })
    if (path.endsWith('/verify') && request.method() === 'POST') {
      const body = request.postDataJSON()
      writes.push({ path, key: request.headers()['idempotency-key'], body })
      const match = path.match(/\/connections\/([^/]+)\/scope-bindings\/([^/]+)\/verify$/)
      const connectionIndex = connections.findIndex(item => item.id === match?.[1])
      const bindingIndex = bindings.findIndex(item => item.id === match?.[2])
      const connection = connections[connectionIndex]
      const binding = bindings[bindingIndex]
      const isGithub = connection.provider.toLowerCase() === 'github'
      const check = isGithub
        ? { checked_at: '2026-09-23T00:00:00Z', outcome: 'data', connection_status: 'connected', binding_status: 'confirmed', observed_count: 3, scope_confirmed: true, detail: 'GitHub 仓库已读取并绑定到默认分支提交' }
        : { checked_at: '2026-09-23T00:00:00Z', outcome: 'empty', connection_status: 'connected', binding_status: 'confirmed', observed_count: 0, scope_confirmed: true, detail: '连接已认证，但所选时间窗内没有观察记录' }
      connections[connectionIndex] = { ...connection, revision: connection.revision + 1, status: 'connected', checked_at: check.checked_at, latest_check: check }
      bindings[bindingIndex] = { ...binding, revision: binding.revision + 1, status: 'confirmed', checked_at: check.checked_at, latest_check: check }
      recordEvent('connection.check_completed', 'scope_binding', binding.id)
      return route.fulfill({ json: { connection: connections[connectionIndex], scope_binding: bindings[bindingIndex], check } })
    }
    return route.fulfill({ json: {} })
  })

  await page.clock.install()
  await page.goto('/?view=product&product_section=workspace')
  await expect(page.getByRole('heading', { name: '工作区配置' })).toBeVisible()
  await expect(page.getByText('验证与信号导入会发起明确的只读请求；导入记录不自动判定为异常，建立事故需要人工选择和确认。')).toBeVisible()

  await page.getByLabel('新工作区名称').fill('客服工作区')
  await page.getByRole('button', { name: '创建工作区' }).click()
  await expect(page.getByRole('combobox', { name: '选择工作区' })).toContainText('客服工作区')

  await page.getByLabel('新应用名称').fill('订单助手')
  await page.getByRole('button', { name: '创建应用' }).click()
  await expect(page.getByRole('combobox', { name: '选择应用' })).toContainText('订单助手')

  await page.getByLabel('服务商 / 连接器').fill('Langfuse')
  await page.getByLabel('服务地址（可选）').fill('https://langfuse.example.test')
  await page.getByLabel('凭据引用（非密钥）').fill('env://S9_OBSERVED_LANGFUSE')
  await page.getByLabel('声明能力（逗号分隔）').fill('read_traces')
  await page.getByRole('button', { name: '登记连接' }).click()
  await expect(page.getByText('连接登记为待验证。此操作没有访问外部服务。')).toBeVisible()
  await expect(page.getByText('Langfuse · 待验证')).toBeVisible()
  await expect(page.getByText('待验证').first()).toBeVisible()

  await page.getByLabel('使用连接').selectOption('conn-1')
  await page.getByLabel('外部资源 ID').fill('project-123')
  await page.getByLabel('读取权限声明').fill('read:traces')
  await page.getByRole('button', { name: '登记范围' }).click()
  await expect(page.getByText('范围已登记为待验证。尚未核验外部资源或权限。')).toBeVisible()
  await expect(page.getByText('production · langfuse_project')).toBeVisible()
  await expect(page.getByText('scope_binding.created')).toBeVisible()
  await page.getByRole('button', { name: '只读验证' }).click()
  await expect(page.getByText('连接已认证，但所选时间窗内没有观察记录').first()).toBeVisible()
  await expect(page.getByText('范围已确认')).toBeVisible()
  await expect(page.getByText('connection.check_completed')).toBeVisible()
  await page.getByRole('button', { name: '启用自动读取' }).click()
  await expect(page.getByText('已启用只读自动读取；新监护从当前时间开始，不会自动导入历史积压。')).toBeVisible()
  await expect(page.getByText('Langfuse · project-123 · 已启用')).toBeVisible()
  Object.assign(monitors[0], { status: 'healthy', last_run: { state: 'healthy', created_count: 3, duplicate_count: 0, auto_linked_count: 0, correlation_ambiguous_count: 0, coverage: { complete: true, pages_read: 1, rows_seen: 3 } } })
  await page.clock.fastForward(15_000)
  await expect(page.getByText('Langfuse · project-123 · 运行正常')).toBeVisible()
  await expect(page.getByText(/最近一次：新增 3，重复 0；自动追加到既有事故 0，多候选待判断 0；窗口覆盖 完整/)).toBeVisible()
  await page.getByRole('button', { name: '暂停自动读取' }).click()
  await expect(page.getByText('自动读取已暂停；已有信号和运行记录保留。')).toBeVisible()
  await expect(page.getByText('Langfuse · project-123 · 已暂停')).toBeVisible()
  await page.getByRole('button', { name: '读取信号（只读）' }).click()
  await expect(page.getByText(/Langfuse 观察记录导入完成：新增 1 条、重复 0 条；0 条同一外部对象的近期更新已追加/)).toBeVisible()
  await expect(page.getByText('Langfuse observation · 待人工判断业务含义')).toBeVisible()
  await expect(page.getByRole('button', { name: '继续读取下一页' })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: '工作区配置' })).toBeVisible()
  await expect(page.getByRole('button', { name: '继续读取下一页' })).toBeVisible()
  await page.getByRole('button', { name: '继续读取下一页' }).click()
  await expect(page.getByText('sig-test-2')).toBeVisible()
  await page.getByRole('checkbox', { name: '选择信号 sig-test', exact: true }).check()
  await page.getByLabel('事故标题').fill('Checkout trace failure')
  await page.getByRole('button', { name: '将选中信号建立为事故' }).click()
  await expect(page.getByText('Checkout trace failure', { exact: true })).toBeVisible()
  await expect(page.getByText('incident.created')).toBeVisible()
  await page.getByLabel('事故 inc-test 操作理由').fill('由本地操作员接手')
  await page.getByRole('button', { name: '事故 inc-test 认领' }).click()
  await expect(page.getByText('事故已由本地操作员认领并记录审计事件。')).toBeVisible()
  await page.getByLabel('事故 inc-test 操作理由').fill('用户影响范围已扩大')
  await page.getByLabel('事故 inc-test 严重度').selectOption('high')
  await page.getByRole('button', { name: '保存事故 inc-test 严重级别' }).click()
  await expect(page.getByText('事故严重度已调整并记入工作区事件。')).toBeVisible()
  await page.getByLabel('事故 inc-test 操作理由').fill('人工确认需要检查最近部署')
  await page.getByRole('button', { name: '开始调查' }).click()
  await expect(page.getByText(/调查中 .*修订 4/)).toBeVisible()
  await expect(page.getByText('incident.state_changed')).toBeVisible()
  await page.getByRole('button', { name: '启动人工调查' }).click()
  await expect(page.getByRole('region', { name: '调查工作台' })).toBeVisible()
  await page.getByLabel('支持').check()
  await page.getByLabel('调查假设').fill('最近发布导致结账异常')
  await page.getByRole('button', { name: '记录待复核假设' }).click()
  await expect(page.getByText('最近发布导致结账异常')).toBeVisible()
  await page.getByLabel('假设 hyp-test 判断依据').fill('时间窗口与信号记录相符')
  await page.getByRole('button', { name: '人工支持' }).click()
  await expect(page.getByText('supported · 人工置信度 0.5 · 支持 1 / 反证 0')).toBeVisible()
  await page.getByLabel('结束调查说明').fill('人工确认该根因，未执行任何修复')
  await page.getByRole('button', { name: '记录人工确认的根因' }).click()
  await expect(page.getByText('调查运行已结束；事故是否恢复仍需单独判断。')).toBeVisible()

  await page.getByLabel('服务商 / 连接器').fill('GitHub')
  await page.getByLabel('凭据引用（非密钥）').fill('env://S9_OBSERVED_GITHUB')
  await page.getByLabel('声明能力（逗号分隔）').fill('contents:read')
  await page.getByRole('button', { name: '登记连接' }).click()
  await expect(page.getByText('GitHub · 待验证')).toBeVisible()
  await page.getByLabel('使用连接').selectOption('conn-2')
  await expect(page.getByLabel('资源类型')).toHaveValue('github_repository')
  await page.getByLabel('外部资源 ID').fill('octocat/hello-world')
  await page.getByLabel('读取权限声明').fill('contents:read')
  await page.getByRole('button', { name: '登记范围' }).click()
  await expect(page.getByText('production · github_repository')).toBeVisible()
  await page.getByRole('button', { name: '只读验证' }).last().click()
  await expect(page.getByText('GitHub 仓库已读取并绑定到默认分支提交').first()).toBeVisible()
  await expect(page.getByText('范围已确认').last()).toBeVisible()
  await page.getByLabel('信号读取范围').selectOption('bind-2')
  await page.getByRole('button', { name: '读取信号（只读）' }).click()
  await expect(page.getByText(/GitHub issue 和 pull request导入完成：新增 1 条、重复 0 条；0 条同一外部对象的近期更新已追加/)).toBeVisible()
  await expect(page.getByText('sig-gh-1')).toBeVisible()
  await page.getByRole('checkbox', { name: '选择信号 sig-test-2', exact: true }).check()
  await page.getByLabel('事故标题').fill('Follow-up trace')
  await page.getByRole('button', { name: '将选中信号建立为事故' }).click()
  await expect(page.getByText('Follow-up trace', { exact: true })).toBeVisible()
  await page.getByLabel('事故 inc-test-2 合并目标').selectOption('inc-test')
  await page.getByLabel('事故 inc-test-2 操作理由').fill('复核后确认是同一结账故障')
  await page.getByRole('button', { name: '合并事故 inc-test-2' }).click()
  await expect(page.getByText('事故已合并；目标事故、迁移信号、来源事故和审计事件均已更新。')).toBeVisible()
  await expect(page.getByText(/Follow-up trace.*已合并/)).toBeVisible()

  await page.getByRole('checkbox', { name: '选择信号 sig-gh-1', exact: true }).check()
  await page.getByLabel('关联到既有事故', { exact: true }).selectOption('inc-test')
  await page.getByLabel('人工关联依据').fill('GitHub 报告与事故时间窗及服务范围一致')
  await page.getByRole('button', { name: '关联所选信号' }).click()
  await expect(page.getByText('已按人工判断将信号关联到既有事故，信号、事故版本和审计事件已一并更新。')).toBeVisible()
  await expect(page.getByText('incident.signals_attached')).toBeVisible()

  await page.getByRole('button', { name: '读取信号（只读）' }).click()
  await expect(page.getByText(/GitHub issue 和 pull request导入完成：新增 1 条、重复 0 条；1 条同一外部对象的近期更新已追加到唯一的既有事故/)).toBeVisible()
  await expect(page.getByText('github_issue · 已归并').first()).toBeVisible()
  await expect(page.getByText('incident.signal_auto_linked')).toBeVisible()

  await page.getByLabel('事故 inc-test 拆分信号 sig-gh-1').check()
  await page.getByLabel('事故 inc-test 拆分标题').fill('GitHub report follow-up')
  await page.getByLabel('事故 inc-test 操作理由').fill('复核后确认此报告需要单独调查')
  await page.getByRole('button', { name: '拆分所选信号 inc-test' }).click()
  await expect(page.getByText('已建立拆分事故；两侧信号关系、事故版本和审计事件均已原子更新。')).toBeVisible()
  await expect(page.getByText('GitHub report follow-up', { exact: true })).toBeVisible()

  expect(writes).toHaveLength(26)
  expect(writes.every(write => Boolean(write.key))).toBe(true)
  expect(new Set(writes.map(write => write.key)).size).toBe(26)
  expect(writes[2].body.credential_ref).toBe('env://S9_OBSERVED_LANGFUSE')
  expect(writes.some(write => write.body.credential_ref === 'env://S9_OBSERVED_GITHUB')).toBe(true)
  expect(JSON.stringify(writes)).not.toContain('Bearer ')
  expect(connections.every(item => item.status === 'connected')).toBe(true)
  expect(bindings.every(item => item.status === 'confirmed')).toBe(true)
})

test('operator sets a token budget and executes a model-backed investigation graph', async ({ page }) => {
  const workspace: any = { id: 'wsp-ai', workspace_id: 'wsp-ai', revision: 1, policy_revision: 1,
    name: '模型调查工作区', deployment_mode: 'local',
    budget: { token_limit: null, validation_reserve_tokens: 0, max_concurrency: 1 } }
  const application: any = { id: 'app-ai', workspace_id: workspace.id, revision: 1, name: '结账服务', owner_id: null }
  const incident: any = { id: 'inc-ai', revision: 1, title: '结账错误率升高', severity: 'high',
    state: 'open', signal_ids: ['sig-ai'], merged_into_id: null, assignee_id: null }
  const signal: any = { id: 'sig-ai', revision: 1, source_binding_id: 'bind-ai', source_id: 'manual-1',
    signal_type: 'manual_observation', occurred_at: '2026-09-23T00:00:00Z',
    summary: '发布后结账错误率上升', status: 'clustered' }
  const runs: any[] = []
  let detail: any = null
  let executeCalls = 0
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/state') return route.fulfill({ json: state })
    if (path === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': keepalive\n\n' })
    if (path === '/api/product/snapshot') return route.fulfill({ json: { status: 'ready', runtime: { status: 'running' }, connections: [], incidents: [], collaboration: {} } })
    if (path === '/api/v1/workspaces' && request.method() === 'GET') return route.fulfill({ json: { items: [workspace] } })
    if (path === '/api/v1/workspaces/wsp-ai/applications' && request.method() === 'GET') return route.fulfill({ json: { items: [application] } })
    if (path === '/api/v1/workspaces/wsp-ai/events') return route.fulfill({ json: { items: [], next_after: 0 } })
    if (path.endsWith('/connections') && request.method() === 'GET') return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/scope-bindings') && request.method() === 'GET') return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/signals') && request.method() === 'GET') return route.fulfill({ json: { items: [signal] } })
    if (path.endsWith('/incidents') && request.method() === 'GET') return route.fulfill({ json: { items: [incident] } })
    if (path.endsWith('/signal-import-checkpoints') && request.method() === 'GET') return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/signal-monitors') && request.method() === 'GET') return route.fulfill({ json: { items: [] } })
    if (path.endsWith('/investigation-runs') && request.method() === 'GET') return route.fulfill({ json: { items: runs } })
    if (path === '/api/v1/workspaces/wsp-ai/budget' && request.method() === 'PUT') {
      const body = request.postDataJSON()
      expect(body.expected_revision).toBe(workspace.revision)
      workspace.revision += 1; workspace.policy_revision += 1; workspace.budget = body.budget
      return route.fulfill({ json: workspace })
    }
    if (path.endsWith('/incidents/inc-ai/investigation-runs') && request.method() === 'POST') {
      const body = request.postDataJSON()
      expect(body.mode).toBe('single')
      incident.revision += 1; incident.state = 'investigating'
      const run = { id: 'run-ai', workspace_id: workspace.id, application_id: application.id,
        revision: 1, incident_id: incident.id, execution_mode: 'single', state: 'running',
        result_type: null, input_snapshot_sha256: 'a'.repeat(64), policy_revision: workspace.policy_revision,
        token_limit: workspace.budget.token_limit, validation_reserve_tokens: 0 }
      const evidence = { id: 'ev-ai', origin: 'section9-signal://sig-ai', source_version: 'v1',
        content_sha256: 'b'.repeat(64), coverage_complete: false }
      const tasks = [
        { id: 'task-ai-1', revision: 1, task_key: 'investigate', title: '调查信号', role: 'investigator',
          state: 'open', result: null, failure_reason: null },
        { id: 'task-ai-2', revision: 1, task_key: 'synthesize', title: '汇总结论', role: 'synthesizer',
          state: 'open', result: null, failure_reason: null },
      ]
      detail = { run, input_snapshot: { incident, signals: [signal] }, evidence: [evidence], hypotheses: [],
        task_graph: { mode: 'single', state: 'ready', tasks }, model_usage: [] }
      runs.push(run)
      return route.fulfill({ status: 201, json: detail })
    }
    if (path.endsWith('/execute') && request.method() === 'POST') {
      executeCalls += 1
      detail.task_graph.state = 'complete'
      detail.task_graph.tasks[0].state = 'succeeded'
      detail.task_graph.tasks[0].result = { summary: '发布后错误率上升，等待反例复核。', evidence_ids: ['ev-ai'] }
      detail.task_graph.tasks[1].state = 'succeeded'
      detail.task_graph.tasks[1].result = { summary: '需要发布前后对照数据。', evidence_ids: ['ev-ai'], conclusion: 'needs_data' }
      detail.model_usage = [{ request_id: 'usage-ai', task_id: 'task-ai-1', task_epoch: 1,
        provider: 'mock-provider', model: 'mock-model', reserved_tokens: 1800, actual_tokens: 37,
        state: 'settled', provider_called: true, error_code: null }]
      return route.fulfill({ json: { detail, execution: { state: 'complete', completed: 2, remaining: 0 } } })
    }
    return route.fulfill({ json: {} })
  })

  await page.goto('/?view=product&product_section=workspace')
  await expect(page.getByRole('heading', { name: '工作区配置' })).toBeVisible()
  const createRun = page.getByRole('button', { name: '创建单体模型调查' })
  await expect(createRun).toBeDisabled()
  await page.getByLabel('工作区 Token 上限').fill('20000')
  await page.getByRole('button', { name: '保存预算' }).click()
  await expect(page.getByText('工作区预算已更新。现有调查运行捕获了旧策略版本，需新建调查后才能调用模型。')).toBeVisible()
  await expect(createRun).toBeEnabled()
  await createRun.click()
  await expect(page.getByText('已创建单体模型调查；尚未调用模型。')).toBeVisible()
  await page.getByRole('button', { name: '执行模型调查任务图' }).click()
  await expect(page.getByText(/任务图已完成，本次处理 2 个任务/)).toBeVisible()
  await expect(page.getByText('模型任务图 · complete')).toBeVisible()
  await expect(page.getByText('mock-provider · 预留 1800 tokens · 实际 37')).toBeVisible()
  expect(executeCalls).toBe(1)
})
