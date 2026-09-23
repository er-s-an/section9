import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import './workspace-setup.css'

type Workspace = {
  id: string
  revision: number
  policy_revision: number
  name: string
  deployment_mode: string
  budget: { token_limit: number | null; validation_reserve_tokens: number; max_concurrency: number }
}
type Application = { id: string; name: string; owner_id: string | null }
type Connection = {
  id: string
  provider: string
  endpoint: string | null
  credential_ref: string | null
  capabilities: string[]
  status: string
  revision: number
  latest_check?: { outcome?: string; detail?: string; observed_count?: number; scope_confirmed?: boolean } | null
}
type Binding = {
  id: string
  environment_id: string
  connection_id: string
  resource_type: string
  external_resource_id: string
  read_scopes: string[]
  write_scopes: string[]
  status: string
  revision: number
  latest_check?: { outcome?: string; detail?: string; scope_confirmed?: boolean } | null
}
type Signal = {
  id: string
  revision: number
  source_binding_id: string
  source_id: string | null
  signal_type: string
  occurred_at: string
  summary: string
  status: string
}
type Incident = {
  id: string
  revision: number
  title: string
  severity: string
  state: string
  signal_ids: string[]
  merged_into_id?: string | null
  assignee_id?: string | null
}
type InvestigationRun = {
  id: string
  revision: number
  incident_id: string
  execution_mode: 'manual' | 'single' | 'swarm'
  state: string
  result_type: string | null
  input_snapshot_sha256: string
  policy_revision: number
  token_limit: number
  validation_reserve_tokens: number
}
type Evidence = { id: string; origin: string; source_version: string; content_sha256: string; coverage_complete: boolean }
type InvestigationTask = {
  id: string
  revision: number
  task_key: string
  title: string
  role: string
  state: string
  result: { summary: string; evidence_ids: string[]; conclusion?: string | null } | null
  failure_reason: string | null
}
type ModelUsage = {
  request_id: string
  task_id: string
  task_epoch: number
  provider: string
  model: string
  reserved_tokens: number
  actual_tokens: number | null
  state: string
  provider_called: boolean
  error_code: string | null
}
type Hypothesis = {
  id: string
  revision: number
  statement: string
  confidence: number
  support_evidence_ids: string[]
  counterevidence_ids: string[]
  state: string
}
type InvestigationRunDetail = {
  run: InvestigationRun
  input_snapshot: { incident: Incident; signals: Signal[] }
  evidence: Evidence[]
  hypotheses: Hypothesis[]
  task_graph?: { mode: 'single' | 'swarm'; state: string; tasks: InvestigationTask[] }
  model_usage?: ModelUsage[]
}
type SignalImportResult = {
  items: Signal[]
  created_count: number
  duplicate_count: number
  auto_linked_count?: number
  correlation_ambiguous_count?: number
  coverage: { complete: boolean; next_cursor: string | null; continuation_required: boolean; pages_read: number; rows_seen: number; invalid_rows: number; error?: string | null }
}
type SignalImportCheckpoint = {
  source_binding_id: string
  from_start_time: string
  to_start_time: string
  revision: number
  next_cursor: string | null
  complete: boolean
  coverage: SignalImportResult['coverage']
  updated_at: string
}
type SignalSyncMonitor = {
  id: string
  binding_id: string
  provider: string
  revision: number
  enabled: boolean
  interval_seconds: number
  status: string
  next_run_at: string | null
  last_successful_watermark: string | null
  current_window: { from: string; to: string; cursor: string | null } | null
  consecutive_failures: number
  last_run: { state: string; error_code?: string; created_count?: number; duplicate_count?: number; auto_linked_count?: number; correlation_ambiguous_count?: number; coverage?: { complete?: boolean; pages_read?: number; rows_seen?: number; error?: string | null }; coverage_gap?: { from: string; to: string; reason: string } | null } | null
}
type WorkspaceEvent = { event_id: string; sequence: number; event_type: string; occurred_at: string; resource_type: string; resource_id: string }
type ItemList<T> = { items: T[] }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）`)
  return payload as T
}

const idempotencyKey = () => globalThis.crypto?.randomUUID?.() ?? `s9-${Date.now()}-${Math.random().toString(16).slice(2)}`
const splitList = (value: string) => value.split(',').map(item => item.trim()).filter(Boolean)
const timeLabel = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const dateTimeInput = (value: Date) => new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
const initialFromTime = () => dateTimeInput(new Date(Date.now() - 60 * 60 * 1000))
const initialToTime = () => dateTimeInput(new Date())

export function WorkspaceSetup() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [applications, setApplications] = useState<Application[]>([])
  const [applicationId, setApplicationId] = useState('')
  const [connections, setConnections] = useState<Connection[]>([])
  const [bindings, setBindings] = useState<Binding[]>([])
  const [importCheckpoints, setImportCheckpoints] = useState<SignalImportCheckpoint[]>([])
  const [signalMonitors, setSignalMonitors] = useState<SignalSyncMonitor[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [investigationRuns, setInvestigationRuns] = useState<InvestigationRun[]>([])
  const [activeRunDetail, setActiveRunDetail] = useState<InvestigationRunDetail | null>(null)
  const [selectedSignalIds, setSelectedSignalIds] = useState<string[]>([])
  const [events, setEvents] = useState<WorkspaceEvent[]>([])
  const [after, setAfter] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const [workspaceName, setWorkspaceName] = useState('')
  const [tokenLimit, setTokenLimit] = useState('')
  const [workspaceBudgetDraft, setWorkspaceBudgetDraft] = useState('')
  const [applicationName, setApplicationName] = useState('')
  const [provider, setProvider] = useState('')
  const [endpoint, setEndpoint] = useState('')
  const [credentialRef, setCredentialRef] = useState('')
  const [capabilities, setCapabilities] = useState('')
  const [environmentId, setEnvironmentId] = useState('production')
  const [resourceType, setResourceType] = useState('project')
  const [externalResourceId, setExternalResourceId] = useState('')
  const [readScopes, setReadScopes] = useState('')
  const [writeScopes, setWriteScopes] = useState('')
  const [importBindingId, setImportBindingId] = useState('')
  const [monitorIntervalSeconds, setMonitorIntervalSeconds] = useState('300')
  const [importFrom, setImportFrom] = useState(initialFromTime)
  const [importTo, setImportTo] = useState(initialToTime)
  const [continuation, setContinuation] = useState<{ bindingId: string; from: string; to: string; cursor: string } | null>(null)
  const [importResult, setImportResult] = useState<SignalImportResult | null>(null)
  const [incidentTitle, setIncidentTitle] = useState('')
  const [incidentSeverity, setIncidentSeverity] = useState('medium')
  const [attachIncidentId, setAttachIncidentId] = useState('')
  const [signalAttachmentReason, setSignalAttachmentReason] = useState('')
  const [incidentReasons, setIncidentReasons] = useState<Record<string, string>>({})
  const [mergeTargets, setMergeTargets] = useState<Record<string, string>>({})
  const [splitSelections, setSplitSelections] = useState<Record<string, string[]>>({})
  const [splitTitles, setSplitTitles] = useState<Record<string, string>>({})
  const [severityDrafts, setSeverityDrafts] = useState<Record<string, string>>({})
  const [hypothesisStatement, setHypothesisStatement] = useState('')
  const [hypothesisConfidence, setHypothesisConfidence] = useState('0.5')
  const [supportEvidenceIds, setSupportEvidenceIds] = useState<string[]>([])
  const [counterevidenceIds, setCounterevidenceIds] = useState<string[]>([])
  const [hypothesisDecisionReason, setHypothesisDecisionReason] = useState('')
  const [investigationFinishReason, setInvestigationFinishReason] = useState('')
  const [taskRetryReason, setTaskRetryReason] = useState('')

  const selectedWorkspace = useMemo(() => workspaces.find(item => item.id === workspaceId) ?? null, [workspaces, workspaceId])
  const selectedApplication = useMemo(() => applications.find(item => item.id === applicationId) ?? null, [applications, applicationId])
  const importBinding = useMemo(() => bindings.find(item => item.id === importBindingId) ?? null, [bindings, importBindingId])
  const importConnection = useMemo(() => connections.find(item => item.id === importBinding?.connection_id) ?? null, [connections, importBinding])
  const canImportSignals = ['langfuse', 'github'].includes(importConnection?.provider.toLowerCase() ?? '')
    && importConnection?.status === 'connected' && importBinding?.status === 'confirmed'
    && importBinding.latest_check?.scope_confirmed === true && importBinding.latest_check?.outcome !== 'scope_mismatch'
  const canRunModelInvestigation = (selectedWorkspace?.budget.token_limit ?? 0) > 0

  const loadWorkspaces = useCallback(async (preserve = true) => {
    const result = await request<ItemList<Workspace>>('/api/v1/workspaces')
    setWorkspaces(result.items)
    setWorkspaceId(current => preserve && result.items.some(item => item.id === current) ? current : result.items[0]?.id ?? '')
  }, [])

  const loadContext = useCallback(async () => {
    if (!workspaceId) {
      setApplications([]); setApplicationId(''); setConnections([]); setBindings([]); setEvents([]); setAfter(0)
      return
    }
    const [applicationResult, eventResult] = await Promise.all([
      request<ItemList<Application>>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications`),
      request<{ items: WorkspaceEvent[]; next_after: number }>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/events?after=0&limit=100`),
    ])
    setApplications(applicationResult.items)
    setApplicationId(current => applicationResult.items.some(item => item.id === current) ? current : applicationResult.items[0]?.id ?? '')
    setEvents(eventResult.items)
    setAfter(eventResult.next_after)
  }, [workspaceId])

  const loadApplication = useCallback(async () => {
    if (!workspaceId || !applicationId) {
      setConnections([]); setBindings([]); setImportCheckpoints([]); setSignalMonitors([]); setSignals([]); setIncidents([]); setInvestigationRuns([]); setActiveRunDetail(null); setSelectedSignalIds([])
      setImportBindingId(''); setContinuation(null); setImportResult(null)
      return
    }
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    const [connectionResult, bindingResult, signalResult, incidentResult, checkpointResult, monitorResult, runResult] = await Promise.all([
      request<ItemList<Connection>>(`${root}/connections`),
      request<ItemList<Binding>>(`${root}/scope-bindings?environment_id=${encodeURIComponent(environmentId || 'production')}`),
      request<ItemList<Signal>>(`${root}/signals?environment_id=${encodeURIComponent(environmentId || 'production')}`),
      request<ItemList<Incident>>(`${root}/incidents?environment_id=${encodeURIComponent(environmentId || 'production')}`),
      request<ItemList<SignalImportCheckpoint>>(`${root}/signal-import-checkpoints?environment_id=${encodeURIComponent(environmentId || 'production')}`),
      request<ItemList<SignalSyncMonitor>>(`${root}/signal-monitors?environment_id=${encodeURIComponent(environmentId || 'production')}`),
      request<ItemList<InvestigationRun>>(`${root}/investigation-runs?environment_id=${encodeURIComponent(environmentId || 'production')}`),
    ])
    setConnections(connectionResult.items)
    setBindings(bindingResult.items)
    setSignals(signalResult.items ?? [])
    setIncidents(incidentResult.items ?? [])
    setInvestigationRuns(runResult.items ?? [])
    setActiveRunDetail(current => current && runResult.items?.some(item => item.id === current.run.id) ? current : null)
    setImportCheckpoints(checkpointResult.items ?? [])
    setSignalMonitors(monitorResult.items ?? [])
    setSelectedSignalIds(current => current.filter(id => signalResult.items?.some(item => item.id === id && item.status === 'new')))
    const resumable = (checkpointResult.items ?? []).find(checkpoint => !checkpoint.complete
      && bindingResult.items.some(binding => {
        const connection = connectionResult.items.find(item => item.id === binding.connection_id)
        return binding.id === checkpoint.source_binding_id && binding.status === 'confirmed'
          && ((binding.resource_type === 'langfuse_project' && connection?.provider.toLowerCase() === 'langfuse')
            || (binding.resource_type === 'github_repository' && connection?.provider.toLowerCase() === 'github'))
      }))
    if (resumable) {
      setImportBindingId(resumable.source_binding_id)
      setImportFrom(dateTimeInput(new Date(resumable.from_start_time)))
      setImportTo(dateTimeInput(new Date(resumable.to_start_time)))
      setContinuation(resumable.next_cursor ? {
        bindingId: resumable.source_binding_id,
        from: resumable.from_start_time,
        to: resumable.to_start_time,
        cursor: resumable.next_cursor,
      } : null)
    } else {
      setContinuation(null)
      setImportBindingId(current => bindingResult.items.some(item => item.id === current)
        ? current
        : bindingResult.items.find(item => item.resource_type === 'langfuse_project' && item.status === 'confirmed')?.id ?? '')
    }
  }, [workspaceId, applicationId, environmentId])

  const reload = useCallback(async () => {
    setError('')
    try { await loadWorkspaces(); await loadContext(); await loadApplication() }
    catch (cause) { setError((cause as Error).message) }
  }, [loadApplication, loadContext, loadWorkspaces])

  const refreshSignalMonitors = useCallback(async () => {
    if (!workspaceId || !applicationId) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    const result = await request<ItemList<SignalSyncMonitor>>(
      `${root}/signal-monitors?environment_id=${encodeURIComponent(environmentId || 'production')}`,
    )
    setSignalMonitors(result.items ?? [])
  }, [workspaceId, applicationId, environmentId])
  const hasEnabledSignalMonitor = signalMonitors.some(item => item.enabled)

  useEffect(() => { void loadWorkspaces(false).catch(cause => setError((cause as Error).message)) }, [loadWorkspaces])
  useEffect(() => { setWorkspaceBudgetDraft(selectedWorkspace?.budget.token_limit?.toString() ?? '') }, [selectedWorkspace?.id, selectedWorkspace?.budget.token_limit])
  useEffect(() => { void loadContext().catch(cause => setError((cause as Error).message)) }, [loadContext])
  useEffect(() => { void loadApplication().catch(cause => setError((cause as Error).message)) }, [loadApplication])
  useEffect(() => {
    if (!hasEnabledSignalMonitor) return
    const timer = window.setInterval(() => {
      void refreshSignalMonitors().catch(cause => setError((cause as Error).message))
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [hasEnabledSignalMonitor, refreshSignalMonitors])

  const mutate = async (operation: () => Promise<unknown>, success: string) => {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const outcome = await operation()
      await reload()
      setNotice(typeof outcome === 'string' ? outcome : success)
    }
    catch (cause) { setError((cause as Error).message) }
    finally { setBusy(false) }
  }

  const createWorkspace = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const token_limit = tokenLimit.trim() ? Number(tokenLimit) : null
    if (token_limit !== null && (!Number.isSafeInteger(token_limit) || token_limit < 0)) { setError('Token 上限必须是非负整数。'); return }
    void mutate(async () => {
      const result = await request<Workspace>('/api/v1/workspaces', {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ name: workspaceName, budget: { token_limit, validation_reserve_tokens: 0, max_concurrency: 1 }, deployment_mode: 'local' }),
      })
      setWorkspaceName(''); setTokenLimit(''); setWorkspaceId(result.id)
    }, '工作区已登记。')
  }

  const saveWorkspaceBudget = () => {
    if (!selectedWorkspace) return
    const token_limit = workspaceBudgetDraft.trim() ? Number(workspaceBudgetDraft) : null
    if (token_limit !== null && (!Number.isSafeInteger(token_limit) || token_limit < 0)) {
      setError('Token 上限必须是非负整数。'); return
    }
    void mutate(async () => {
      const updated = await request<Workspace>(`/api/v1/workspaces/${encodeURIComponent(selectedWorkspace.id)}/budget`, {
        method: 'PUT', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ expected_revision: selectedWorkspace.revision, budget: {
          ...selectedWorkspace.budget, token_limit,
        } }),
      })
      setWorkspaceBudgetDraft(updated.budget.token_limit?.toString() ?? '')
      return '工作区预算已更新。现有调查运行捕获了旧策略版本，需新建调查后才能调用模型。'
    }, '工作区预算已更新。')
  }

  const createApplication = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!workspaceId) return
    void mutate(async () => {
      const result = await request<Application>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() }, body: JSON.stringify({ name: applicationName }),
      })
      setApplicationName(''); setApplicationId(result.id)
    }, '应用已登记。')
  }

  const createConnection = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!workspaceId || !applicationId) return
    void mutate(async () => {
      await request<Connection>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}/connections`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ provider, endpoint: endpoint || null, credential_ref: credentialRef || null, capabilities: splitList(capabilities) }),
      })
      setProvider(''); setEndpoint(''); setCredentialRef(''); setCapabilities('')
    }, '连接登记为待验证。此操作没有访问外部服务。')
  }

  const createBinding = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!workspaceId || !applicationId) return
    const form = event.currentTarget
    const selectedConnectionId = new FormData(form).get('binding-connection')
    void mutate(async () => {
      await request<Binding>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}/scope-bindings`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, connection_id: selectedConnectionId, resource_type: resourceType, external_resource_id: externalResourceId, read_scopes: splitList(readScopes), write_scopes: splitList(writeScopes) }),
      })
      setExternalResourceId(''); setReadScopes(''); setWriteScopes('')
    }, '范围已登记为待验证。尚未核验外部资源或权限。')
  }

  const verifyBinding = (binding: Binding) => {
    const connection = connections.find(item => item.id === binding.connection_id)
    if (!connection || !workspaceId || !applicationId) return
    void mutate(async () => {
      const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
      const result = await request<{ check: { detail: string; outcome: string } }>(
        `${root}/connections/${encodeURIComponent(connection.id)}/scope-bindings/${encodeURIComponent(binding.id)}/verify`,
        {
          method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
          body: JSON.stringify({ environment_id: binding.environment_id,
            expected_connection_revision: connection.revision, expected_binding_revision: binding.revision }),
        },
      )
      return result.check.detail
    }, '连接读取检查已完成。')
  }

  const importLangfuseSignals = (resume = false) => {
    if (!workspaceId || !applicationId || !importBinding || !canImportSignals || !importConnection) return
    const from = resume && continuation?.bindingId === importBinding.id ? continuation.from : new Date(importFrom).toISOString()
    const to = resume && continuation?.bindingId === importBinding.id ? continuation.to : new Date(importTo).toISOString()
    const cursor = resume && continuation?.bindingId === importBinding.id ? continuation.cursor : undefined
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    const isGitHub = importConnection.provider.toLowerCase() === 'github'
    const importPath = `${root}/connections/${encodeURIComponent(importBinding.connection_id)}/scope-bindings/${encodeURIComponent(importBinding.id)}/signals/import${isGitHub ? '/github' : ''}`
    void mutate(async () => {
      const result = await request<SignalImportResult>(
        importPath,
        { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() }, body: JSON.stringify({
          environment_id: importBinding.environment_id,
          ...(isGitHub ? { from_updated_at: from, to_updated_at: to } : { from_start_time: from, to_start_time: to }),
          page_size: 100, max_pages: 5, ...(cursor ? { cursor } : {}),
        }) },
      )
      setImportResult(result)
      setContinuation(result.coverage.next_cursor
        ? { bindingId: importBinding.id, from, to, cursor: result.coverage.next_cursor }
        : null)
      const sourceLabel = isGitHub ? 'GitHub issue 和 pull request' : 'Langfuse 观察记录'
      return `${sourceLabel}导入完成：新增 ${result.created_count} 条、重复 ${result.duplicate_count} 条；${result.auto_linked_count ?? 0} 条同一外部对象的近期更新已追加到唯一的既有事故，${result.correlation_ambiguous_count ?? 0} 条因存在多个候选事故而保留待判断，其余新信号也留在收件箱。该规则不判断因果。`
    }, '')
  }

  const configureSignalMonitor = (enabled: boolean) => {
    if (!workspaceId || !applicationId || !importBinding || !canImportSignals) return
    const interval = Number(monitorIntervalSeconds)
    if (!Number.isSafeInteger(interval) || interval < 60 || interval > 86_400) {
      setError('自动读取间隔必须是 60 到 86400 秒。')
      return
    }
    const existing = signalMonitors.find(item => item.binding_id === importBinding.id)
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<SignalSyncMonitor>(`${root}/scope-bindings/${encodeURIComponent(importBinding.id)}/signal-monitor`, {
        method: 'PUT', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: importBinding.environment_id, enabled,
          interval_seconds: interval, expected_revision: existing?.revision ?? 0 }),
      })
    }, enabled ? '已启用只读自动读取；新监护从当前时间开始，不会自动导入历史积压。' : '自动读取已暂停；已有信号和运行记录保留。')
  }

  const createIncidentFromSignals = () => {
    if (!workspaceId || !applicationId || !incidentTitle.trim() || selectedSignalIds.length === 0) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    const selected = signals.filter(item => selectedSignalIds.includes(item.id) && item.status === 'new')
    void mutate(async () => {
      await request<Incident>(`${root}/incidents`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({
          environment_id: environmentId, title: incidentTitle.trim(), severity: incidentSeverity,
          signals: selected.map(item => ({ signal_id: item.id, expected_revision: item.revision })),
        }),
      })
      setIncidentTitle(''); setSelectedSignalIds([])
    }, '事故已建立，原始信号和审计事件均已保留。')
  }

  const attachSignalsToIncident = () => {
    const incident = incidents.find(item => item.id === attachIncidentId)
    if (!workspaceId || !applicationId || !incident || !signalAttachmentReason.trim()) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    const selected = signals.filter(item => selectedSignalIds.includes(item.id) && item.status === 'new')
    if (!selected.length) return
    void mutate(async () => {
      await request<Incident>(`${root}/incidents/${encodeURIComponent(incident.id)}/signals`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({
          environment_id: environmentId,
          expected_incident_revision: incident.revision,
          reason: signalAttachmentReason.trim(),
          signals: selected.map(item => ({ signal_id: item.id, expected_revision: item.revision })),
        }),
      })
      setSelectedSignalIds([])
      setSignalAttachmentReason('')
    }, '已按人工判断将信号关联到既有事故，信号、事故版本和审计事件已一并更新。')
  }

  const mergeIncident = (incident: Incident) => {
    const target = incidents.find(item => item.id === mergeTargets[incident.id])
    const reason = (incidentReasons[incident.id] ?? '').trim()
    if (!workspaceId || !applicationId || !target || !reason) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<{ source: Incident; target: Incident }>(`${root}/incidents/${encodeURIComponent(incident.id)}/merge`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, target_incident_id: target.id,
          expected_incident_revision: incident.revision, expected_target_revision: target.revision, reason }),
      })
      setIncidentReasons(current => ({ ...current, [incident.id]: '' }))
      setMergeTargets(current => ({ ...current, [incident.id]: '' }))
    }, '事故已合并；目标事故、迁移信号、来源事故和审计事件均已更新。')
  }

  const splitIncident = (incident: Incident) => {
    const selectedIds = splitSelections[incident.id] ?? []
    const selected = signals.filter(item => selectedIds.includes(item.id) && incident.signal_ids.includes(item.id))
    const reason = (incidentReasons[incident.id] ?? '').trim()
    const title = (splitTitles[incident.id] ?? '').trim()
    if (!workspaceId || !applicationId || !title || !reason || !selected.length) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<{ source: Incident; created: Incident }>(`${root}/incidents/${encodeURIComponent(incident.id)}/split`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_incident_revision: incident.revision,
          title, severity: incident.severity, reason,
          signals: selected.map(item => ({ signal_id: item.id, expected_revision: item.revision })) }),
      })
      setIncidentReasons(current => ({ ...current, [incident.id]: '' }))
      setSplitSelections(current => ({ ...current, [incident.id]: [] }))
      setSplitTitles(current => ({ ...current, [incident.id]: '' }))
    }, '已建立拆分事故；两侧信号关系、事故版本和审计事件均已原子更新。')
  }

  const updateIncidentAssignment = (incident: Incident, action: 'claim' | 'release') => {
    const reason = (incidentReasons[incident.id] ?? '').trim()
    if (!workspaceId || !applicationId || !reason) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<Incident>(`${root}/incidents/${encodeURIComponent(incident.id)}/assignment`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: incident.revision, action, reason }),
      })
      setIncidentReasons(current => ({ ...current, [incident.id]: '' }))
    }, action === 'claim' ? '事故已由本地操作员认领并记录审计事件。' : '本地操作员已释放事故认领。')
  }

  const updateIncidentSeverity = (incident: Incident) => {
    const severity = severityDrafts[incident.id] ?? incident.severity
    const reason = (incidentReasons[incident.id] ?? '').trim()
    if (!workspaceId || !applicationId || !reason || severity === incident.severity) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<Incident>(`${root}/incidents/${encodeURIComponent(incident.id)}/severity`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: incident.revision, severity, reason }),
      })
      setIncidentReasons(current => ({ ...current, [incident.id]: '' }))
    }, '事故严重度已调整并记入工作区事件。')
  }

  const transitionIncident = (incident: Incident, targetState: string) => {
    const reason = (incidentReasons[incident.id] ?? '').trim()
    if (!workspaceId || !applicationId || !reason) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<Incident>(`${root}/incidents/${encodeURIComponent(incident.id)}/transitions`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: incident.revision,
          target_state: targetState, reason }),
      })
      setIncidentReasons(current => ({ ...current, [incident.id]: '' }))
    }, '事故状态已更新并记入工作区事件。')
  }

  const startManualInvestigation = (incident: Incident) => {
    if (!workspaceId || !applicationId) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      const detail = await request<InvestigationRunDetail>(
        `${root}/incidents/${encodeURIComponent(incident.id)}/investigation-runs/manual`,
        { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
          body: JSON.stringify({ environment_id: environmentId, expected_incident_revision: incident.revision }) },
      )
      setActiveRunDetail(detail)
      setSupportEvidenceIds([]); setCounterevidenceIds([]); setHypothesisStatement('')
    }, '人工调查运行已启动；事故与信号已冻结为输入快照。')
  }

  const startModelInvestigation = (incident: Incident, mode: 'single' | 'swarm') => {
    if (!workspaceId || !applicationId) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      const detail = await request<InvestigationRunDetail>(
        `${root}/incidents/${encodeURIComponent(incident.id)}/investigation-runs`,
        { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
          body: JSON.stringify({ environment_id: environmentId, expected_incident_revision: incident.revision, mode }) },
      )
      setActiveRunDetail(detail)
      setSupportEvidenceIds([]); setCounterevidenceIds([]); setHypothesisStatement('')
    }, mode === 'single' ? '已创建单体模型调查；尚未调用模型。' : '已创建蜂群角色任务图；尚未调用模型。')
  }

  const openInvestigationRun = async (runId: string) => {
    if (!workspaceId || !applicationId) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    setError('')
    try { setActiveRunDetail(await request<InvestigationRunDetail>(`${root}/investigation-runs/${encodeURIComponent(runId)}?environment_id=${encodeURIComponent(environmentId)}`)) }
    catch (cause) { setError((cause as Error).message) }
  }

  const executeInvestigation = (run: InvestigationRun) => {
    if (!workspaceId || !applicationId || run.execution_mode === 'manual') return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      const result = await request<{ detail: InvestigationRunDetail; execution: { state: string; completed: number; remaining: number; error_code?: string; provider_called?: boolean } }>(
        `${root}/investigation-runs/${encodeURIComponent(run.id)}/execute`,
        { method: 'POST', body: JSON.stringify({ environment_id: environmentId }) },
      )
      setActiveRunDetail(result.detail)
      if (result.execution.state === 'complete') return `任务图已完成，本次处理 ${result.execution.completed} 个任务。结果仍需人工复核，事故恢复需要单独验证。`
      if (result.execution.provider_called) return `执行在模型调用后停止（${result.execution.error_code ?? 'TASK_EXECUTION_FAILED'}）。已记录用量状态；请检查任务和预算后再决定是否重试。`
      return `执行尚未完成（${result.execution.error_code ?? result.execution.state}）；本次没有发出模型请求，可修正配置或预算后重试。`
    }, '调查任务执行完成。')
  }

  const retryInvestigationTask = (task: InvestigationTask) => {
    if (!workspaceId || !applicationId || !activeRunDetail || !taskRetryReason.trim()) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}/tasks/${encodeURIComponent(task.id)}/retry`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: task.revision,
          reason: taskRetryReason.trim() }),
      })
      setActiveRunDetail(await request<InvestigationRunDetail>(
        `${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}?environment_id=${encodeURIComponent(environmentId)}`,
      ))
      setTaskRetryReason('')
    }, '失败任务已重新开放。旧尝试的已知或未知模型用量仍保留在预算账本。')
  }

  const createRunHypothesis = () => {
    if (!workspaceId || !applicationId || !activeRunDetail || !hypothesisStatement.trim()) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<Hypothesis>(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}/hypotheses`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_run_revision: activeRunDetail.run.revision,
          statement: hypothesisStatement.trim(), confidence: Number(hypothesisConfidence),
          support_evidence_ids: supportEvidenceIds, counterevidence_ids: counterevidenceIds }),
      })
      setActiveRunDetail(await request<InvestigationRunDetail>(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}?environment_id=${encodeURIComponent(environmentId)}`))
      setHypothesisStatement(''); setSupportEvidenceIds([]); setCounterevidenceIds([])
    }, '待复核假设已记录。')
  }

  const decideRunHypothesis = (hypothesis: Hypothesis, targetState: 'supported' | 'refuted' | 'unknown') => {
    if (!workspaceId || !applicationId || !activeRunDetail || !hypothesisDecisionReason.trim()) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      await request<Hypothesis>(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}/hypotheses/${encodeURIComponent(hypothesis.id)}/decision`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: hypothesis.revision,
          target_state: targetState, confidence: hypothesis.confidence,
          support_evidence_ids: hypothesis.support_evidence_ids,
          counterevidence_ids: hypothesis.counterevidence_ids, reason: hypothesisDecisionReason.trim() }),
      })
      setActiveRunDetail(await request<InvestigationRunDetail>(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}?environment_id=${encodeURIComponent(environmentId)}`))
      setHypothesisDecisionReason('')
    }, '人工假设判断已记录并审计。')
  }

  const finishManualInvestigation = (resultType: 'root_cause_identified' | 'needs_data' | 'none') => {
    if (!workspaceId || !applicationId || !activeRunDetail || !investigationFinishReason.trim()) return
    const root = `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/applications/${encodeURIComponent(applicationId)}`
    void mutate(async () => {
      const finished = await request<InvestigationRun>(`${root}/investigation-runs/${encodeURIComponent(activeRunDetail.run.id)}/finish`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey() },
        body: JSON.stringify({ environment_id: environmentId, expected_revision: activeRunDetail.run.revision,
          result_type: resultType, reason: investigationFinishReason.trim() }),
      })
      setActiveRunDetail(await request<InvestigationRunDetail>(`${root}/investigation-runs/${encodeURIComponent(finished.id)}?environment_id=${encodeURIComponent(environmentId)}`))
      setInvestigationFinishReason('')
    }, '调查运行已结束；事故是否恢复仍需单独判断。')
  }

  const refreshEvents = async () => {
    if (!workspaceId) return
    setError('')
    try {
      const result = await request<{ items: WorkspaceEvent[]; next_after: number }>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/events?after=${after}&limit=100`)
      setEvents(current => [...current, ...result.items]); setAfter(result.next_after)
    } catch (cause) { setError((cause as Error).message) }
  }

  return (
    <section className="workspace-setup" aria-label="Workspace v1 工作区配置">
      <header className="workspace-setup__heading">
        <div><span className="eyebrow">WORKSPACE V1 · 本地登记</span><h2>工作区配置</h2><p>建立工作区、应用、连接和资源范围。登记结果保存在本地，连接与权限需要后续真实验证。</p></div>
        <button type="button" onClick={() => void reload()} disabled={busy}>刷新记录</button>
      </header>
      <div className="workspace-setup__boundary" role="note"><strong>当前边界</strong><span>凭据只从本地环境变量读取、不保存密钥。验证与信号导入会发起明确的只读请求；导入记录不自动判定为异常，建立事故需要人工选择和确认。</span></div>
      {error && <p className="workspace-setup__message is-error" role="alert">{error}</p>}
      {notice && <p className="workspace-setup__message is-success" role="status">{notice}</p>}
      <div className="workspace-setup__columns">
        <div className="workspace-setup__primary">
          <section className="workspace-setup__card">
            <div className="workspace-setup__card-title"><div><span>01</span><h3>工作区</h3></div><span>{workspaces.length} 个</span></div>
            <label className="workspace-setup__field"><span>选择工作区</span><select aria-label="选择工作区" value={workspaceId} onChange={event => setWorkspaceId(event.target.value)}><option value="">请选择或新建</option>{workspaces.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select></label>
            {selectedWorkspace && <>
              <p className="workspace-setup__record">部署：本地 · Token 上限：{selectedWorkspace.budget.token_limit ?? '未设置'} · 校验预留：{selectedWorkspace.budget.validation_reserve_tokens} · 策略 r{selectedWorkspace.policy_revision}</p>
              <div className="workspace-setup__form"><div className="workspace-setup__inline"><label className="workspace-setup__field"><span>工作区 Token 上限</span><input aria-label="工作区 Token 上限" inputMode="numeric" value={workspaceBudgetDraft} onChange={event => setWorkspaceBudgetDraft(event.target.value)} placeholder="留空则禁用模型调用" /></label><button type="button" onClick={saveWorkspaceBudget} disabled={busy || (workspaceBudgetDraft.trim() === (selectedWorkspace.budget.token_limit?.toString() ?? ''))}>保存预算</button></div><small>策略变更会使现有调查运行停止获得模型调用授权；创建新调查后按新上限计量。</small></div>
            </>}
            <form onSubmit={createWorkspace} className="workspace-setup__form"><label className="workspace-setup__field"><span>新工作区名称</span><input required maxLength={300} value={workspaceName} onChange={event => setWorkspaceName(event.target.value)} placeholder="例如：客服运营" /></label><div className="workspace-setup__inline"><label className="workspace-setup__field"><span>Token 上限（可选）</span><input inputMode="numeric" value={tokenLimit} onChange={event => setTokenLimit(event.target.value)} placeholder="留空则禁用模型调用" /></label><button type="submit" disabled={busy || !workspaceName.trim()}>创建工作区</button></div></form>
          </section>
          <section className="workspace-setup__card">
            <div className="workspace-setup__card-title"><div><span>02</span><h3>应用</h3></div><span>{applications.length} 个</span></div>
            {applications.length > 0 ? <label className="workspace-setup__field"><span>选择应用</span><select aria-label="选择应用" value={applicationId} onChange={event => setApplicationId(event.target.value)}>{applications.map(item => <option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select></label> : <p className="workspace-setup__empty">{selectedWorkspace ? '此工作区尚无应用。' : '先选择或创建工作区。'}</p>}
            {selectedApplication && <p className="workspace-setup__record">应用 ID：{selectedApplication.id}</p>}
            <form onSubmit={createApplication} className="workspace-setup__form"><div className="workspace-setup__inline"><label className="workspace-setup__field"><span>新应用名称</span><input required maxLength={300} value={applicationName} onChange={event => setApplicationName(event.target.value)} placeholder="例如：订单助手" /></label><button type="submit" disabled={busy || !workspaceId || !applicationName.trim()}>创建应用</button></div></form>
          </section>
          <section className="workspace-setup__card">
            <div className="workspace-setup__card-title"><div><span>03</span><h3>外部连接</h3></div><span>{connections.length} 个 · 待验证</span></div>
            <form onSubmit={createConnection} className="workspace-setup__form workspace-setup__form--grid">
              <label className="workspace-setup__field"><span>服务商 / 连接器</span><input required maxLength={200} value={provider} onChange={event => { const next = event.target.value; setProvider(next); if (next.toLowerCase() === 'langfuse') setResourceType('langfuse_project'); else if (next.toLowerCase() === 'github') setResourceType('github_repository') }} placeholder="Langfuse 或 GitHub" /></label>
              <label className="workspace-setup__field"><span>服务地址（可选）</span><input type="url" maxLength={2000} value={endpoint} onChange={event => setEndpoint(event.target.value)} placeholder="https://…" /></label>
              <label className="workspace-setup__field"><span>凭据引用（非密钥）</span><input maxLength={500} value={credentialRef} onChange={event => setCredentialRef(event.target.value)} placeholder="例如：env://S9_OBSERVED_LANGFUSE" /><small>只登记引用，不要粘贴密钥。当前本地适配从 S9_OBSERVED_ 前缀环境变量读取。</small></label>
              <label className="workspace-setup__field"><span>声明能力（逗号分隔）</span><input value={capabilities} onChange={event => setCapabilities(event.target.value)} placeholder="read_traces, list_projects" /></label>
              <button type="submit" disabled={busy || !workspaceId || !applicationId || !provider.trim()}>登记连接</button>
            </form>
            <div className="workspace-setup__list">{connections.map(item => <article key={item.id}><div><strong>{item.provider} · {item.status === 'connected' ? '已连接' : item.status === 'permission_denied' ? '权限不足' : item.status === 'degraded' ? '连接降级' : '待验证'}</strong><small>{item.endpoint || (item.provider.toLowerCase() === 'github' ? '固定 GitHub REST API' : '默认 Langfuse Cloud 地址')} · {item.capabilities.join(', ') || '未声明能力'}</small>{item.latest_check?.detail && <small>{item.latest_check.detail}</small>}<code>{item.id}</code></div><span className="workspace-setup__pending">{item.status === 'connected' ? '只读可达' : item.status === 'permission_denied' ? '拒绝读取' : item.status === 'degraded' ? '待恢复' : '待验证'}</span></article>)}{connections.length === 0 && <p className="workspace-setup__empty">选择应用后登记服务连接。</p>}</div>
          </section>
          <section className="workspace-setup__card">
            <div className="workspace-setup__card-title"><div><span>04</span><h3>资源范围</h3></div><span>{bindings.length} 个 · 待验证</span></div>
            <form onSubmit={createBinding} className="workspace-setup__form workspace-setup__form--grid">
              <label className="workspace-setup__field"><span>环境标识</span><input required maxLength={200} value={environmentId} onChange={event => setEnvironmentId(event.target.value)} placeholder="例如：production" /></label>
              <label className="workspace-setup__field"><span>使用连接</span><select name="binding-connection" required disabled={!connections.length} defaultValue=""><option value="">{connections.length === 0 ? '请先登记连接' : '选择连接'}</option>{connections.map(item => <option key={item.id} value={item.id}>{item.provider} · {item.id}</option>)}</select></label>
              <label className="workspace-setup__field"><span>资源类型</span><input required maxLength={200} value={resourceType} onChange={event => setResourceType(event.target.value)} placeholder="project" /></label>
              <label className="workspace-setup__field"><span>外部资源 ID</span><input required maxLength={1000} value={externalResourceId} onChange={event => setExternalResourceId(event.target.value)} placeholder="实际项目 ID" /></label>
              <label className="workspace-setup__field"><span>读取权限声明</span><input value={readScopes} onChange={event => setReadScopes(event.target.value)} placeholder="read:traces" /></label>
              <label className="workspace-setup__field"><span>写入权限声明</span><input value={writeScopes} onChange={event => setWriteScopes(event.target.value)} placeholder="留空表示不声明写权限" /></label>
              <button type="submit" disabled={busy || !workspaceId || !applicationId || !connections.length || !externalResourceId.trim()}>登记范围</button>
            </form>
            <div className="workspace-setup__list">{bindings.map(item => <article key={item.id}><div><strong>{item.environment_id} · {item.resource_type}</strong><small>资源 {item.external_resource_id} · 读 {item.read_scopes.join(', ') || '无'} · 写 {item.write_scopes.join(', ') || '无'}</small>{item.latest_check?.detail && <small>{item.latest_check.detail}</small>}<code>{item.id}</code></div><span className="workspace-setup__pending">{item.status === 'confirmed' ? '范围已确认' : item.status === 'permission_denied' ? '权限不足' : '待验证'}</span><button type="button" className="workspace-setup__verify" onClick={() => verifyBinding(item)} disabled={busy || !connections.some(connection => connection.id === item.connection_id && ['langfuse', 'github'].includes(connection.provider.toLowerCase()))}>只读验证</button></article>)}{bindings.length === 0 && <p className="workspace-setup__empty">登记后会显示资源范围；登记不等于权限已核验。</p>}</div>
          </section>
          <section className="workspace-setup__card" aria-label="信号收件箱和事故">
            <div className="workspace-setup__card-title"><div><span>05</span><h3>信号收件箱与事故</h3></div><span>{signals.filter(item => item.status === 'new').length} 个待判断 · {incidents.length} 起事故 · {importCheckpoints.filter(item => !item.complete).length} 个窗口待续读</span></div>
            <p className="workspace-setup__record">只导入已验证资源范围内的 Langfuse observations 与 GitHub issues/pull requests。每条信号保留来源 ID 和版本；摘要不复制标题、正文、trace 名称或 prompt 内容，仍需人工判断。</p>
            <div className="workspace-setup__form workspace-setup__form--grid">
              <label className="workspace-setup__field"><span>信号读取范围</span><select aria-label="信号读取范围" value={importBindingId} onChange={event => setImportBindingId(event.target.value)}><option value="">选择已验证范围</option>{bindings.filter(item => ['langfuse_project', 'github_repository'].includes(item.resource_type)).map(item => { const connection = connections.find(value => value.id === item.connection_id); return <option key={item.id} value={item.id}>{connection?.provider} · {item.external_resource_id} · {item.status === 'confirmed' ? '已确认' : '待验证'}</option> })}</select></label>
              <label className="workspace-setup__field"><span>读取开始时间</span><input aria-label="读取开始时间" type="datetime-local" value={importFrom} onChange={event => { setImportFrom(event.target.value); setContinuation(null) }} /></label>
              <label className="workspace-setup__field"><span>读取结束时间</span><input aria-label="读取结束时间" type="datetime-local" value={importTo} onChange={event => { setImportTo(event.target.value); setContinuation(null) }} /></label>
              <div className="workspace-setup__inline">
                <button type="button" onClick={() => importLangfuseSignals(false)} disabled={busy || !canImportSignals || !importFrom || !importTo}>读取信号（只读）</button>
                {continuation?.bindingId === importBindingId && <button type="button" onClick={() => importLangfuseSignals(true)} disabled={busy || !canImportSignals}>继续读取下一页</button>}
              </div>
            </div>
            <section className="workspace-setup__monitor" aria-label="自动信号读取">
              <div><strong>自动信号读取</strong><p>仅在你启用后定时读取已验证范围，不调用模型，也不写入外部服务。首次从现在开始；断线会限速重试，权限或范围问题会暂停监护。停机超过 24 小时会标出未覆盖区间，不自动追跑整段积压。</p></div>
              <div className="workspace-setup__form workspace-setup__form--grid">
                <label className="workspace-setup__field"><span>读取间隔（秒）</span><input aria-label="自动读取间隔（秒）" type="number" min={60} max={86400} step={60} value={monitorIntervalSeconds} onChange={event => setMonitorIntervalSeconds(event.target.value)} /></label>
                {(() => {
                  const monitor = signalMonitors.find(item => item.binding_id === importBindingId)
                  return <button type="button" onClick={() => configureSignalMonitor(!monitor?.enabled)} disabled={busy || !canImportSignals || !importBinding}>
                    {monitor?.enabled ? '暂停自动读取' : monitor ? '重新启用自动读取' : '启用自动读取'}
                  </button>
                })()}
              </div>
              <div className="workspace-setup__list" aria-label="自动读取监护状态">
                {signalMonitors.length === 0 ? <p className="workspace-setup__empty">尚未启用自动读取。手动读取仍可单独使用。</p> : signalMonitors.map(monitor => {
                  const binding = bindings.find(item => item.id === monitor.binding_id)
                  const lastRun = monitor.last_run
                  const state = monitor.enabled
                    ? monitor.status === 'healthy' ? '运行正常' : monitor.status === 'partial' ? '读取未完成' : monitor.status === 'degraded' ? '等待重试' : monitor.status === 'running' ? '正在读取' : '已启用'
                    : monitor.status === 'blocked' ? '需处理后重新启用' : '已暂停'
                  return <article key={monitor.id}><div><strong>{monitor.provider} · {binding?.external_resource_id || monitor.binding_id} · {state}</strong>
                    <small>间隔 {monitor.interval_seconds} 秒 · 连续失败 {monitor.consecutive_failures} · 下次读取 {monitor.next_run_at ? timeLabel(monitor.next_run_at) : '未排期'}</small>
                    {lastRun && <small>最近一次：新增 {lastRun.created_count ?? 0}，重复 {lastRun.duplicate_count ?? 0}；自动追加到既有事故 {lastRun.auto_linked_count ?? 0}，多候选待判断 {lastRun.correlation_ambiguous_count ?? 0}；窗口覆盖 {lastRun.coverage?.complete && !lastRun.coverage_gap ? '完整' : '不完整'}{lastRun.error_code ? ` · ${lastRun.error_code}` : ''}{lastRun.coverage?.error ? ` · ${lastRun.coverage.error}` : ''}{lastRun.coverage_gap ? ` · 停机缺口 ${timeLabel(lastRun.coverage_gap.from)} 至 ${timeLabel(lastRun.coverage_gap.to)}` : ''}</small>}
                    <code>{monitor.id}</code></div></article>
                })}
              </div>
            </section>
            {importResult && <div className="workspace-setup__boundary" role="status"><strong>{importResult.coverage.complete ? '窗口读取完成' : '读取覆盖不完整'}</strong><span>本次新增 {importResult.created_count} 条、重复 {importResult.duplicate_count} 条；读取 {importResult.coverage.pages_read} 页，共见到 {importResult.coverage.rows_seen} 条。{importResult.coverage.invalid_rows ? ` 无效源记录 ${importResult.coverage.invalid_rows} 条。` : ''}{importResult.coverage.continuation_required ? ' 可继续读取下一页。' : ''}{importResult.coverage.error ? ` 错误：${importResult.coverage.error}` : ''}</span></div>}
            {signals.length === 0 ? <p className="workspace-setup__empty">还没有信号。先完成 Langfuse 或 GitHub 资源范围验证，再选择时间窗口读取。</p> : <div className="workspace-setup__list">{signals.map(item => <article key={item.id} className="workspace-setup__signal"><label><input type="checkbox" aria-label={`选择信号 ${item.id}`} checked={selectedSignalIds.includes(item.id)} disabled={item.status !== 'new' || busy} onChange={event => setSelectedSignalIds(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} /></label><div><strong>{item.signal_type} · {item.status === 'new' ? '待判断' : item.status === 'clustered' ? '已归并' : item.status}</strong><small>{item.summary}</small><small>来源 {item.source_id || '未提供'} · {timeLabel(item.occurred_at)}</small><code>{item.id}</code></div></article>)}</div>}
            <div className="workspace-setup__form workspace-setup__form--grid">
              <label className="workspace-setup__field"><span>事故标题</span><input aria-label="事故标题" maxLength={500} value={incidentTitle} onChange={event => setIncidentTitle(event.target.value)} placeholder="由你描述这批信号要调查的问题" /></label>
              <label className="workspace-setup__field"><span>严重级别</span><select aria-label="严重级别" value={incidentSeverity} onChange={event => setIncidentSeverity(event.target.value)}><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option><option value="info">信息</option></select></label>
              <button type="button" onClick={createIncidentFromSignals} disabled={busy || !incidentTitle.trim() || selectedSignalIds.length === 0}>将选中信号建立为事故</button>
            </div>
            <div className="workspace-setup__form workspace-setup__form--grid">
              <label className="workspace-setup__field"><span>关联到既有事故</span><select aria-label="关联到既有事故" value={attachIncidentId} onChange={event => setAttachIncidentId(event.target.value)} disabled={!incidents.some(item => ['open', 'investigating', 'needs_input'].includes(item.state))}><option value="">选择待处理事故</option>{incidents.filter(item => ['open', 'investigating', 'needs_input'].includes(item.state)).map(item => <option key={item.id} value={item.id}>{item.title} · {item.state} · r{item.revision}</option>)}</select></label>
              <label className="workspace-setup__field"><span>人工关联依据</span><input aria-label="人工关联依据" maxLength={1000} value={signalAttachmentReason} onChange={event => setSignalAttachmentReason(event.target.value)} placeholder="说明这些信号与事故的关联" /></label>
              <button type="button" onClick={attachSignalsToIncident} disabled={busy || !attachIncidentId || !signalAttachmentReason.trim() || selectedSignalIds.length === 0}>关联所选信号</button>
            </div>
            <p className="workspace-setup__record">轮询信号仅在同一已验证来源范围、同一外部对象出现新版本，且该对象过去 24 小时内只关联到一个活动事故时，才自动追加到该事故；多个候选或超出时限的信号留在待判断队列。此规则不判断因果，也不会按时间相近或不同来源合并。过期事故或已处理信号不会被静默改写。</p>
            {incidents.length > 0 && <div className="workspace-setup__list" aria-label="已登记事故">{incidents.map(item => {
              const actions = item.state === 'open'
                ? [{ label: '开始调查', state: 'investigating' }, { label: '等待资料', state: 'needs_input' }, { label: '判定非异常', state: 'dismissed' }]
                : item.state === 'investigating'
                  ? [{ label: '退回待处理', state: 'open' }, { label: '等待资料', state: 'needs_input' }, { label: '判定非异常', state: 'dismissed' }]
                  : item.state === 'needs_input'
                    ? [{ label: '继续调查', state: 'investigating' }, { label: '判定非异常', state: 'dismissed' }]
                    : []
              const stateLabel: Record<string, string> = {
                open: '待处理', investigating: '调查中', needs_input: '等待资料', dismissed: '已排除',
                awaiting_approval: '等待审批', remediating: '处理中', validating: '验证中', observing: '观察中', resolved: '已解决', merged: '已合并',
              }
              const runs = investigationRuns.filter(run => run.incident_id === item.id)
              const isActive = ['open', 'investigating', 'needs_input'].includes(item.state)
              const hasActiveRun = runs.some(run => run.state === 'queued' || run.state === 'running')
              const lifecycleTargets = incidents.filter(candidate => candidate.id !== item.id
                && ['open', 'investigating', 'needs_input'].includes(candidate.state))
              const movableSignals = signals.filter(signal => item.signal_ids.includes(signal.id) && signal.status === 'clustered')
              return <article key={item.id} className="workspace-setup__incident">
                <div><strong>{item.title}</strong><small>{item.severity} · {item.signal_ids.length} 条信号 · {stateLabel[item.state] ?? item.state}{item.assignee_id ? ` · 负责人：${item.assignee_id === 'local-operator' ? '本地操作员' : item.assignee_id}` : ' · 尚未认领'}{item.merged_into_id ? ` · 合并至 ${item.merged_into_id}` : ''} · 修订 {item.revision}</small><code>{item.id}</code></div>
                {(item.state === 'open' || item.state === 'investigating' || item.state === 'needs_input') && <div className="workspace-setup__incident-actions">
                  <button type="button" onClick={() => startManualInvestigation(item)} disabled={busy || runs.some(run => run.state === 'running')}>启动人工调查</button>
                  <button type="button" onClick={() => startModelInvestigation(item, 'single')} disabled={busy || !canRunModelInvestigation || runs.some(run => run.state === 'running')}>创建单体模型调查</button>
                  <button type="button" onClick={() => startModelInvestigation(item, 'swarm')} disabled={busy || !canRunModelInvestigation || runs.some(run => run.state === 'running')}>创建蜂群任务图</button>
                  {runs.map(run => <button key={run.id} type="button" onClick={() => void openInvestigationRun(run.id)} disabled={busy}>打开调查 · {run.state === 'running' ? '进行中' : run.result_type ?? run.state} · r{run.revision}</button>)}
                </div>}
                {(actions.length > 0 || (isActive && !hasActiveRun)) && <div className="workspace-setup__incident-actions">
                  <label className="workspace-setup__field"><span>本次事故操作理由</span><input aria-label={`事故 ${item.id} 操作理由`} maxLength={1000} value={incidentReasons[item.id] ?? ''} onChange={event => setIncidentReasons(current => ({ ...current, [item.id]: event.target.value }))} placeholder="状态变更、合并或拆分的判断依据" /></label>
                  <div className="workspace-setup__inline">{actions.map(action => <button key={action.state} type="button" onClick={() => transitionIncident(item, action.state)} disabled={busy || !(incidentReasons[item.id] ?? '').trim()}>{action.label}</button>)}
                    {isActive && !hasActiveRun && lifecycleTargets.length > 0 && <><label className="workspace-setup__field"><span>合并到事故</span><select aria-label={`事故 ${item.id} 合并目标`} value={mergeTargets[item.id] ?? ''} onChange={event => setMergeTargets(current => ({ ...current, [item.id]: event.target.value }))}><option value="">选择目标事故</option>{lifecycleTargets.map(target => <option key={target.id} value={target.id}>{target.title} · r{target.revision}</option>)}</select></label><button type="button" aria-label={`合并事故 ${item.id}`} onClick={() => mergeIncident(item)} disabled={busy || !mergeTargets[item.id] || !(incidentReasons[item.id] ?? '').trim()}>合并事故</button></>}
                  </div>
                </div>}
                {isActive && <div className="workspace-setup__incident-actions">
                  <span className="workspace-setup__record">{item.assignee_id ? `当前由${item.assignee_id === 'local-operator' ? '本地操作员' : item.assignee_id}负责` : '当前无人认领'} · 本地单操作者模式</span>
                  <div className="workspace-setup__inline">
                    <button type="button" aria-label={`事故 ${item.id} ${item.assignee_id ? '释放认领' : '认领'}`} onClick={() => updateIncidentAssignment(item, item.assignee_id ? 'release' : 'claim')} disabled={busy || !(incidentReasons[item.id] ?? '').trim() || (item.assignee_id !== null && item.assignee_id !== undefined && item.assignee_id !== 'local-operator')}>
                      {item.assignee_id === 'local-operator' ? '释放我的认领' : item.assignee_id ? '已由其他人认领' : '认领给本地操作员'}
                    </button>
                    <label className="workspace-setup__field"><span>调整严重度</span><select aria-label={`事故 ${item.id} 严重度`} value={severityDrafts[item.id] ?? item.severity} onChange={event => setSeverityDrafts(current => ({ ...current, [item.id]: event.target.value }))}><option value="critical">严重</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option><option value="info">信息</option></select></label>
                    <button type="button" aria-label={`保存事故 ${item.id} 严重级别`} onClick={() => updateIncidentSeverity(item)} disabled={busy || (severityDrafts[item.id] ?? item.severity) === item.severity || !(incidentReasons[item.id] ?? '').trim()}>保存严重度</button>
                  </div>
                </div>}
                {isActive && !hasActiveRun && movableSignals.length > 1 && <div className="workspace-setup__incident-actions">
                  <label className="workspace-setup__field"><span>拆分后的事故标题</span><input aria-label={`事故 ${item.id} 拆分标题`} maxLength={500} value={splitTitles[item.id] ?? ''} onChange={event => setSplitTitles(current => ({ ...current, [item.id]: event.target.value }))} placeholder="描述独立问题" /></label>
                  <div role="group" aria-label={`事故 ${item.id} 拆分信号`} className="workspace-setup__inline">{movableSignals.map(signal => <label key={signal.id}><input type="checkbox" aria-label={`事故 ${item.id} 拆分信号 ${signal.id}`} checked={(splitSelections[item.id] ?? []).includes(signal.id)} onChange={event => setSplitSelections(current => ({ ...current, [item.id]: event.target.checked ? [...(current[item.id] ?? []), signal.id] : (current[item.id] ?? []).filter(id => id !== signal.id) }))} />{signal.signal_type} · {signal.id}</label>)}</div>
                  <button type="button" aria-label={`拆分所选信号 ${item.id}`} onClick={() => splitIncident(item)} disabled={busy || !(splitTitles[item.id] ?? '').trim() || !(incidentReasons[item.id] ?? '').trim() || !(splitSelections[item.id] ?? []).length || (splitSelections[item.id] ?? []).length >= item.signal_ids.length}>拆分所选信号</button>
                </div>}
              </article>
            })}<p className="workspace-setup__record">单体与蜂群模式调用本地配置的模型，并受调查运行 Token 上限控制。蜂群模式按来源分区调查、先独立列反例、再复核和汇总；各角色使用同一配置模型的隔离请求，不代表 EvoMap 原生蜂群或独立模型。方案审批、修复执行与恢复验证尚未接通。人工确认根因不代表服务已经恢复。</p></div>}
            {activeRunDetail && <section className="workspace-setup__investigation" aria-label="调查工作台">
              <div className="workspace-setup__card-title"><div><span>RCA</span><h3>调查工作台</h3></div><span>{activeRunDetail.run.state === 'running' ? '进行中' : activeRunDetail.run.result_type ?? activeRunDetail.run.state}</span></div>
              <p className="workspace-setup__record">运行 {activeRunDetail.run.id} · 输入快照 {activeRunDetail.run.input_snapshot_sha256} · {activeRunDetail.evidence.length} 条证据。原始信号的时间窗覆盖均标为未证明。</p>
              {activeRunDetail.task_graph && <section className="workspace-setup__task-graph" aria-label="模型调查任务图">
                <div className="workspace-setup__card-title"><div><span>{activeRunDetail.task_graph.mode === 'swarm' ? 'SWARM' : 'SINGLE'}</span><h4>模型任务图 · {activeRunDetail.task_graph.state}</h4></div><span>{activeRunDetail.run.token_limit} Token 上限</span></div>
                <p className="workspace-setup__record">模型调用只会在点击执行时发生。供应商凭据从本机 EVOMAP_MODEL_API_KEY 读取，不会发送到浏览器。未知用量继续占用完整预留额度。</p>
                {activeRunDetail.run.state === 'running' && activeRunDetail.task_graph.state !== 'complete' && <>
                  <button type="button" onClick={() => executeInvestigation(activeRunDetail.run)} disabled={busy || !canRunModelInvestigation || selectedWorkspace?.policy_revision !== activeRunDetail.run.policy_revision}>执行模型调查任务图</button>
                  {selectedWorkspace?.policy_revision !== activeRunDetail.run.policy_revision && <p className="workspace-setup__record">工作区预算策略已变化；结束此运行后，按新策略创建新的调查运行。</p>}
                </>}
                <div className="workspace-setup__list">{activeRunDetail.task_graph.tasks.map(task => <article key={task.id}><div><strong>{task.title} · {task.state}</strong><small>{task.role}{task.result ? ` · ${task.result.summary}` : task.failure_reason ? ` · ${task.failure_reason}` : ''}</small><code>{task.task_key} · {task.id}</code></div>{task.state === 'failed' && <div className="workspace-setup__inline"><label className="workspace-setup__field"><span>重试理由</span><input aria-label={`任务 ${task.task_key} 重试理由`} value={taskRetryReason} maxLength={1000} onChange={event => setTaskRetryReason(event.target.value)} placeholder="如：确认服务恢复后重试" /></label><button type="button" onClick={() => retryInvestigationTask(task)} disabled={busy || !taskRetryReason.trim() || selectedWorkspace?.policy_revision !== activeRunDetail.run.policy_revision}>重新开放任务</button></div>}</article>)}</div>
                {(activeRunDetail.model_usage ?? []).length > 0 && <div className="workspace-setup__list" aria-label="模型 Token 用量">{activeRunDetail.model_usage?.map(usage => <article key={usage.request_id}><div><strong>{usage.model} · {usage.state}</strong><small>{usage.provider} · 预留 {usage.reserved_tokens} tokens · 实际 {usage.actual_tokens ?? '未知'}{usage.error_code ? ` · ${usage.error_code}` : ''}</small><code>{usage.request_id} · 任务 {usage.task_id} / epoch {usage.task_epoch}</code></div></article>)}</div>}
              </section>}
              <div className="workspace-setup__evidence-list" aria-label="冻结的调查证据">{activeRunDetail.evidence.map(evidence => {
                const signal = activeRunDetail.input_snapshot.signals.find(item => evidence.origin.endsWith(item.id))
                return <article key={evidence.id}><strong>{signal?.signal_type ?? '调查证据'}</strong><small>{signal?.summary ?? evidence.origin} · 来源版本 {evidence.source_version}</small><code>{evidence.id} · SHA-256 {evidence.content_sha256}</code><div className="workspace-setup__inline"><label><input type="checkbox" checked={supportEvidenceIds.includes(evidence.id)} onChange={event => { setSupportEvidenceIds(current => event.target.checked ? [...current, evidence.id] : current.filter(id => id !== evidence.id)); if (event.target.checked) setCounterevidenceIds(current => current.filter(id => id !== evidence.id)) }} />支持</label><label><input type="checkbox" checked={counterevidenceIds.includes(evidence.id)} onChange={event => { setCounterevidenceIds(current => event.target.checked ? [...current, evidence.id] : current.filter(id => id !== evidence.id)); if (event.target.checked) setSupportEvidenceIds(current => current.filter(id => id !== evidence.id)) }} />反证</label></div></article>
              })}</div>
              {activeRunDetail.run.state === 'running' && <>
                <div className="workspace-setup__form workspace-setup__form--grid">
                  <label className="workspace-setup__field"><span>调查假设</span><textarea maxLength={4000} value={hypothesisStatement} onChange={event => setHypothesisStatement(event.target.value)} placeholder="描述一个可由证据支持或推翻的解释" /></label>
                  <label className="workspace-setup__field"><span>人工置信度（0–1）</span><input type="number" min="0" max="1" step="0.05" value={hypothesisConfidence} onChange={event => setHypothesisConfidence(event.target.value)} /></label>
                  <button type="button" onClick={createRunHypothesis} disabled={busy || !hypothesisStatement.trim() || !Number.isFinite(Number(hypothesisConfidence)) || Number(hypothesisConfidence) < 0 || Number(hypothesisConfidence) > 1}>记录待复核假设</button>
                </div>
                {activeRunDetail.hypotheses.length > 0 && <div className="workspace-setup__hypotheses" aria-label="调查假设">{activeRunDetail.hypotheses.map(hypothesis => <article key={hypothesis.id}><strong>{hypothesis.statement}</strong><small>{hypothesis.state} · 人工置信度 {hypothesis.confidence} · 支持 {hypothesis.support_evidence_ids.length} / 反证 {hypothesis.counterevidence_ids.length}</small><code>{hypothesis.id}</code>{hypothesis.state === 'proposed' && <div className="workspace-setup__incident-actions"><label className="workspace-setup__field"><span>人工判断依据</span><input aria-label={`假设 ${hypothesis.id} 判断依据`} maxLength={1000} value={hypothesisDecisionReason} onChange={event => setHypothesisDecisionReason(event.target.value)} placeholder="引用证据并说明判断" /></label><div className="workspace-setup__inline"><button type="button" onClick={() => decideRunHypothesis(hypothesis, 'supported')} disabled={busy || !hypothesis.support_evidence_ids.length || !hypothesisDecisionReason.trim()}>人工支持</button><button type="button" onClick={() => decideRunHypothesis(hypothesis, 'refuted')} disabled={busy || !hypothesis.counterevidence_ids.length || !hypothesisDecisionReason.trim()}>人工反驳</button><button type="button" onClick={() => decideRunHypothesis(hypothesis, 'unknown')} disabled={busy || !hypothesisDecisionReason.trim()}>仍不确定</button></div></div>}</article>)}</div>}
                <label className="workspace-setup__field"><span>结束调查说明</span><input maxLength={1000} value={investigationFinishReason} onChange={event => setInvestigationFinishReason(event.target.value)} placeholder="记录结果边界或尚缺资料" /></label>
                <div className="workspace-setup__inline"><button type="button" onClick={() => finishManualInvestigation('root_cause_identified')} disabled={busy || !activeRunDetail.hypotheses.some(item => item.state === 'supported') || !investigationFinishReason.trim()}>记录人工确认的根因</button><button type="button" onClick={() => finishManualInvestigation('needs_data')} disabled={busy || !investigationFinishReason.trim()}>结束并等待资料</button><button type="button" onClick={() => finishManualInvestigation('none')} disabled={busy || !investigationFinishReason.trim()}>结束但不下结论</button></div>
              </>}
            </section>}
          </section>
        </div>
        <aside className="workspace-setup__card workspace-setup__events">
          <div className="workspace-setup__card-title"><div><span>LOG</span><h3>工作区事件</h3></div><button type="button" onClick={() => void refreshEvents()} disabled={!workspaceId}>读取更多</button></div>
          {!workspaceId ? <p className="workspace-setup__empty">创建或选择工作区后查看审计事件。</p> : events.length === 0 ? <p className="workspace-setup__empty">暂无事件。</p> : <ol>{events.map(item => <li key={item.event_id}><time>{timeLabel(item.occurred_at)}</time><strong>{item.event_type}</strong><small>{item.resource_type} · {item.resource_id}</small><code>#{item.sequence}</code></li>)}</ol>}
        </aside>
      </div>
    </section>
  )
}

export default WorkspaceSetup
