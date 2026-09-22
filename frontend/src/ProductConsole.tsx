import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ArrowClockwise,
  CheckCircle,
  CaretRight,
  FileText,
  GitBranch,
  Info,
  LinkSimple,
  LockKey,
  MagnifyingGlass,
  Pulse,
  ShieldCheck,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import './product-console.css'

export type ProductStatus =
  | 'ready'
  | 'degraded'
  | 'unknown'
  | 'permission_denied'
  | 'pending'

export type ProductAction =
  | { type: 'refresh'; source: 'product-console' }
  | { type: 'open_evidence'; evidenceId?: string }
  | { type: 'close_evidence' }
  | { type: 'select_issue'; issueId: string }
  | { type: 'select_incident'; incidentId: string }

export interface ProductSelection {
  id?: string | null
  name?: string | null
  label?: string | null
  [key: string]: unknown
}

export interface WorkflowState {
  status?: ProductStatus | null
  label?: string | null
  detail?: string | null
  owner?: string | null
  updatedAt?: string | null
  [key: string]: unknown
}

export interface EvidenceRecord {
  id: string
  label?: string | null
  summary?: string | null
  value?: string | number | boolean | null
  source?: string | null
  capturedAt?: string | null
  status?: ProductStatus | null
  technical?: unknown
  [key: string]: unknown
}

export interface ConnectionCapability {
  id: string
  label: string
  description?: string | null
  status?: ProductStatus | null
  detail?: string | null
  checkedAt?: string | null
  permission?: string | null
  technical?: unknown
  [key: string]: unknown
}

export interface IssueRecord {
  id: string
  title: string
  status?: ProductStatus | null
  severity?: string | null
  summary?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  evidence?: EvidenceRecord[] | null
  [key: string]: unknown
}

export interface IncidentRecord {
  id: string
  title?: string | null
  status?: ProductStatus | null
  summary?: string | null
  openedAt?: string | null
  updatedAt?: string | null
  repair?: WorkflowState | null
  verification?: WorkflowState | null
  approval?: WorkflowState | null
  governance?: GovernanceState | null
  evidence?: EvidenceRecord[] | null
  [key: string]: unknown
}

export interface GovernanceState {
  status?: ProductStatus | null
  policy?: string | null
  owner?: string | null
  reviewRequired?: boolean | null
  constraints?: string[] | null
  detail?: string | null
  [key: string]: unknown
}

export interface ProductWorkflow {
  repair?: WorkflowState | null
  verification?: WorkflowState | null
  approval?: WorkflowState | null
  [key: string]: unknown
}

export interface ProductConsoleSnapshot {
  status?: ProductStatus | null
  capturedAt?: string | null
  updatedAt?: string | null
  revision?: string | null
  generation?: string | null
  runtime?: { status?: string | null; port?: number | null } | null
  workflow?: ProductWorkflow | null
  repair?: WorkflowState | null
  verification?: WorkflowState | null
  approval?: WorkflowState | null
  governance?: GovernanceState | null
  evidence?: EvidenceRecord[] | null
  [key: string]: unknown
}

export interface ProductConsoleProps {
  snapshot?: ProductConsoleSnapshot | null
  loading?: boolean
  error?: string | null
  connections?: ConnectionCapability[] | null
  issues?: IssueRecord[] | null
  incidents?: IncidentRecord[] | null
  selectedProject?: ProductSelection | null
  selectedEnvironment?: ProductSelection | null
  onAction: (action: ProductAction) => void | Promise<void>
}

const STATUS_META: Record<ProductStatus, { label: string; hint: string }> = {
  ready: { label: '就绪', hint: '当前能力已报告可用' },
  degraded: { label: '降级', hint: '能力存在限制或部分不可用' },
  unknown: { label: '未知', hint: '尚未取得足够证据' },
  permission_denied: { label: '权限不足', hint: '当前身份没有所需权限' },
  pending: { label: '待处理', hint: '已有请求，但结果尚未确认' },
}

const STATUS_VALUES: ProductStatus[] = [
  'ready',
  'degraded',
  'unknown',
  'permission_denied',
  'pending',
]

const normalizeStatus = (value: unknown): ProductStatus => {
  return STATUS_VALUES.includes(value as ProductStatus)
    ? (value as ProductStatus)
    : 'unknown'
}

const displayValue = (value: unknown, fallback = '暂无记录'): string => {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return fallback
  }
}

const displaySelection = (value?: ProductSelection | null): string => {
  if (!value) return '未选择'
  return displayValue(value.name ?? value.label ?? value.id, '未命名')
}

const displayTime = (value?: string | null): string => {
  if (!value) return '时间未知'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

const serializeTechnical = (value: unknown): string => {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return '技术证据不可序列化'
  }
}

function StatusBadge({
  status,
  compact = false,
}: {
  status?: ProductStatus | null
  compact?: boolean
}) {
  const normalized = normalizeStatus(status)
  const meta = STATUS_META[normalized]
  return (
    <span
      className={`product-console__status product-console__status--${normalized}${compact ? ' product-console__status--compact' : ''}`}
      title={meta.hint}
    >
      <span className="product-console__status-dot" aria-hidden="true" />
      {meta.label}
    </span>
  )
}

function SectionHeading({
  eyebrow,
  title,
  detail,
  action,
}: {
  eyebrow: string
  title: string
  detail?: string
  action?: ReactNode
}) {
  return (
    <div className="product-console__section-heading">
      <div>
        <span className="product-console__eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        {detail ? <p>{detail}</p> : null}
      </div>
      {action ? <div className="product-console__section-action">{action}</div> : null}
    </div>
  )
}

function EmptyState({
  icon,
  title,
  detail,
}: {
  icon: ReactNode
  title: string
  detail: string
}) {
  return (
    <div className="product-console__empty" role="status">
      <span className="product-console__empty-icon">{icon}</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  )
}

function LoadingState({ label = '正在读取真实状态…' }: { label?: string }) {
  return (
    <div className="product-console__loading" role="status" aria-live="polite">
      <span className="product-console__loading-mark" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

function WorkflowCard({
  icon,
  title,
  state,
}: {
  icon: ReactNode
  title: string
  state?: WorkflowState | null
}) {
  const status = normalizeStatus(state?.status)
  return (
    <article className="product-console__workflow-card">
      <div className="product-console__workflow-icon" aria-hidden="true">
        {icon}
      </div>
      <div className="product-console__workflow-copy">
        <div className="product-console__workflow-title">
          <h3>{title}</h3>
          <StatusBadge status={status} compact />
        </div>
        <p>{displayValue(state?.label ?? state?.detail, '尚未收到该阶段状态')}</p>
        {state?.owner ? <small>负责人：{state.owner}</small> : null}
        {state?.updatedAt ? <small>更新于：{displayTime(state.updatedAt)}</small> : null}
      </div>
    </article>
  )
}

function TechnicalDetails({
  label,
  value,
}: {
  label: string
  value: unknown
}) {
  if (value === null || value === undefined || value === '') return null
  return (
    <details className="product-console__technical">
      <summary>
        <Info size={15} aria-hidden="true" />
        {label}
      </summary>
      <pre>{serializeTechnical(value)}</pre>
    </details>
  )
}

function EvidenceItem({ evidence }: { evidence: EvidenceRecord }) {
  return (
    <details className="product-console__evidence-item">
      <summary>
        <span className="product-console__evidence-title">
          <FileText size={16} aria-hidden="true" />
          <span>
            <strong>{displayValue(evidence.label ?? evidence.id, '未命名证据')}</strong>
            {evidence.summary ? <small>{evidence.summary}</small> : null}
          </span>
        </span>
        <StatusBadge status={evidence.status} compact />
      </summary>
      <div className="product-console__evidence-body">
        <dl>
          <div>
            <dt>来源</dt>
            <dd>{displayValue(evidence.source)}</dd>
          </div>
          <div>
            <dt>采集时间</dt>
            <dd>{displayTime(evidence.capturedAt)}</dd>
          </div>
          <div>
            <dt>记录值</dt>
            <dd className="product-console__breakable">{displayValue(evidence.value)}</dd>
          </div>
        </dl>
        <TechnicalDetails label="展开技术证据" value={evidence.technical} />
      </div>
    </details>
  )
}

export function ProductConsole({
  snapshot,
  loading = false,
  error = null,
  connections = [],
  issues = [],
  incidents = [],
  selectedProject,
  selectedEnvironment,
  onAction,
}: ProductConsoleProps) {
  const connectionItems = connections ?? []
  const runtimeStatus = snapshot?.runtime?.status ?? 'unknown'
  const connectionStatus: ProductStatus = connectionItems.length === 0
    ? 'unknown'
    : runtimeStatus !== 'running'
      ? 'degraded'
    : connectionItems.some((connection) => normalizeStatus(connection.status) === 'permission_denied')
      ? 'permission_denied'
      : connectionItems.some((connection) => normalizeStatus(connection.status) === 'degraded')
        ? 'degraded'
        : connectionItems.every((connection) => normalizeStatus(connection.status) === 'ready')
          ? 'ready'
          : 'unknown'
  const issueItems = issues ?? []
  const incidentItems = incidents ?? []
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null)
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const currentIncident = useMemo(() => {
    if (selectedIncidentId) {
      return incidentItems.find((incident) => incident.id === selectedIncidentId) ?? incidentItems[0] ?? null
    }
    return incidentItems[0] ?? null
  }, [incidentItems, selectedIncidentId])

  const currentIssue = useMemo(() => {
    if (selectedIssueId) {
      return issueItems.find((issue) => issue.id === selectedIssueId) ?? issueItems[0] ?? null
    }
    return issueItems[0] ?? null
  }, [issueItems, selectedIssueId])

  const allEvidence = useMemo(() => {
    const candidates = [
      ...(snapshot?.evidence ?? []),
      ...(currentIncident?.evidence ?? []),
      ...(currentIssue?.evidence ?? []),
    ]
    const seen = new Set<string>()
    return candidates.filter((evidence) => {
      if (seen.has(evidence.id)) return false
      seen.add(evidence.id)
      return true
    })
  }, [currentIncident, currentIssue, snapshot?.evidence])

  const selectedEvidence = selectedEvidenceId
    ? allEvidence.find((evidence) => evidence.id === selectedEvidenceId) ?? null
    : null

  const workflow = snapshot?.workflow ?? {}
  const repairState = snapshot?.repair ?? workflow.repair ?? currentIncident?.repair
  const verificationState = snapshot?.verification ?? workflow.verification ?? currentIncident?.verification
  const approvalState = snapshot?.approval ?? workflow.approval ?? currentIncident?.approval
  const governance = snapshot?.governance ?? currentIncident?.governance

  const hasSnapshot = Boolean(snapshot)
  const hasMainData = hasSnapshot || connectionItems.length > 0 || issueItems.length > 0 || incidentItems.length > 0

  const dispatchAction = (action: ProductAction, key: string = action.type) => {
    if (pendingAction) return
    setActionError(null)
    setPendingAction(key)
    try {
      const result = onAction(action)
      void Promise.resolve(result)
        .then(() => setPendingAction(null))
        .catch((reason: unknown) => {
          setActionError(reason instanceof Error ? reason.message : '操作未完成，请查看上游状态')
          setPendingAction(null)
        })
    } catch (reason: unknown) {
      setActionError(reason instanceof Error ? reason.message : '操作未完成，请查看上游状态')
      setPendingAction(null)
    }
  }

  const openEvidence = (evidenceId?: string) => {
    setSelectedEvidenceId(evidenceId ?? null)
    setDrawerOpen(true)
    dispatchAction({ type: 'open_evidence', evidenceId }, evidenceId ? `open-evidence-${evidenceId}` : 'open-evidence')
  }

  const closeEvidence = () => {
    setDrawerOpen(false)
    setSelectedEvidenceId(null)
    dispatchAction({ type: 'close_evidence' }, 'close-evidence')
  }

  return (
    <section className="product-console" aria-label="产品控制台">
      <header className="product-console__header">
        <div className="product-console__brand-block">
          <div className="product-console__brand-mark" aria-hidden="true">S9</div>
          <div>
            <p className="product-console__eyebrow">受控运营视图</p>
            <h1>产品控制台</h1>
            <p className="product-console__header-note">只呈现当前 props 提供的真实快照与回调结果。</p>
          </div>
        </div>
        <div className="product-console__header-actions">
          <div className="product-console__selection" aria-label="当前作用域">
            <span><strong>项目</strong>{displaySelection(selectedProject)}</span>
            <span><strong>环境</strong>{displaySelection(selectedEnvironment)}</span>
          </div>
          <button
            type="button"
            className="product-console__button product-console__button--secondary"
            onClick={() => dispatchAction({ type: 'refresh', source: 'product-console' }, 'refresh')}
            disabled={Boolean(pendingAction)}
          >
            <ArrowClockwise size={16} aria-hidden="true" />
            {pendingAction === 'refresh' ? '读取中…' : '刷新状态'}
          </button>
        </div>
      </header>

      <div className="product-console__state-row" aria-live="polite">
        <span className="product-console__state-label">快照状态</span>
        <StatusBadge status={snapshot?.status} />
        <span className="product-console__state-detail">
          {snapshot?.revision ? `修订 ${snapshot.revision}` : '未提供修订标识'}
          {` · 候选运行时：${runtimeStatus === 'running' ? '运行中' : runtimeStatus === 'stopped' ? '已停止' : '状态未知'}`}
          {snapshot?.capturedAt ? ` · 采集于 ${displayTime(snapshot.capturedAt)}` : ''}
        </span>
        {loading ? <span className="product-console__state-loading">正在同步…</span> : null}
      </div>

      {error ? (
        <div className="product-console__error" role="alert">
          <WarningCircle size={19} aria-hidden="true" />
          <div>
            <strong>真实状态读取失败</strong>
            <p>{error}</p>
          </div>
        </div>
      ) : null}

      {actionError ? (
        <div className="product-console__action-error" role="alert">
          <WarningCircle size={17} aria-hidden="true" />
          <span>回调未完成：{actionError}</span>
        </div>
      ) : null}

      {!hasMainData && loading ? (
        <LoadingState />
      ) : null}

      {!hasMainData && !loading && error ? (
        <div className="product-console__empty-shell">
          <EmptyState
            icon={<WarningCircle size={24} />}
            title="暂无可显示的控制台数据"
            detail="上游返回了错误，控制台不会用演示状态替代真实结果。"
          />
        </div>
      ) : null}

      <div className="product-console__grid">
        <section className="product-console__panel product-console__panel--connections">
          <SectionHeading
            eyebrow="01 / 连接"
            title="连接中心"
            detail={runtimeStatus === 'running'
              ? '能力与权限来自最近一次连接检查；当前候选运行时正在运行。'
              : '候选运行时当前未运行。下方能力是最近一次检查记录，不代表当前可用。'}
            action={<StatusBadge status={connectionStatus} compact />}
          />
          {loading && connectionItems.length === 0 ? <LoadingState label="正在读取连接能力…" /> : null}
          {!loading && connectionItems.length === 0 ? (
            <EmptyState
              icon={<LinkSimple size={24} />}
              title="没有连接能力记录"
              detail="未收到连接列表，当前不推断任何能力已就绪。"
            />
          ) : (
            <div className="product-console__connection-list" aria-label="连接能力列表">
              {connectionItems.map((connection) => (
                <article className="product-console__connection" key={connection.id}>
                  <div className="product-console__connection-icon" aria-hidden="true">
                    <LinkSimple size={18} />
                  </div>
                  <div className="product-console__connection-copy">
                    <div className="product-console__connection-title">
                      <h3>{connection.label}</h3>
                      <StatusBadge status={connection.status} compact />
                    </div>
                    <p>{displayValue(connection.description ?? connection.detail, '暂无连接说明')}</p>
                    <div className="product-console__meta-line">
                      <span>{connection.checkedAt ? `检查于 ${displayTime(connection.checkedAt)}` : '检查时间未知'}</span>
                      {connection.permission ? <span>权限：{connection.permission}</span> : null}
                    </div>
                    <TechnicalDetails label="展开连接技术信息" value={connection.technical} />
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="product-console__panel product-console__panel--workflow">
          <SectionHeading
            eyebrow="02 / 处理闭环"
            title="修复 / 验证 / 审批"
            detail="阶段没有上游状态时保持“未知”，不代替真实结论。"
          />
          <div className="product-console__workflow-list">
            <WorkflowCard icon={<GitBranch size={20} />} title="修复" state={repairState} />
            <WorkflowCard icon={<CheckCircle size={20} />} title="验证" state={verificationState} />
            <WorkflowCard icon={<ShieldCheck size={20} />} title="审批" state={approvalState} />
          </div>
          <div className="product-console__workflow-footnote">
            <LockKey size={16} aria-hidden="true" />
            <span>控制台不把按钮点击或本地过渡当成成功；最终状态需由父层快照回传。</span>
          </div>
        </section>

        <section className="product-console__panel product-console__panel--inbox">
          <SectionHeading
            eyebrow="03 / 收件箱"
            title="问题收件箱"
            detail={`${issueItems.length} 条由上游提供的问题记录`}
          />
          {loading && issueItems.length === 0 ? <LoadingState label="正在读取问题…" /> : null}
          {!loading && issueItems.length === 0 ? (
            <EmptyState
              icon={<MagnifyingGlass size={24} />}
              title="收件箱为空"
              detail="没有问题记录可供处理。"
            />
          ) : (
            <div className="product-console__scroll-list" role="list" aria-label="问题列表">
              {issueItems.map((issue) => (
                <button
                  type="button"
                  className={`product-console__list-row${currentIssue?.id === issue.id ? ' is-selected' : ''}`}
                  key={issue.id}
                  onClick={() => {
                    setSelectedIssueId(issue.id)
                    dispatchAction({ type: 'select_issue', issueId: issue.id }, `select-issue-${issue.id}`)
                  }}
                  disabled={Boolean(pendingAction)}
                  role="listitem"
                >
                  <span className="product-console__row-icon" aria-hidden="true"><WarningCircle size={18} /></span>
                  <span className="product-console__row-copy">
                    <strong>{issue.title}</strong>
                    <span>{displayValue(issue.summary, '暂无问题摘要')}</span>
                    <small>{issue.updatedAt ? `更新于 ${displayTime(issue.updatedAt)}` : '更新时间未知'}</small>
                  </span>
                  <span className="product-console__row-side">
                    {issue.severity ? <em>{issue.severity}</em> : null}
                    <StatusBadge status={issue.status} compact />
                    <CaretRight size={17} aria-hidden="true" />
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="product-console__panel product-console__panel--incidents">
          <SectionHeading
            eyebrow="04 / 事故"
            title="事故摘要"
            detail={`${incidentItems.length} 条由上游提供的事故记录`}
            action={
              <span className="product-console__live-label">
                <Pulse size={15} aria-hidden="true" /> 事实摘要
              </span>
            }
          />
          {loading && incidentItems.length === 0 ? <LoadingState label="正在读取事故…" /> : null}
          {!loading && incidentItems.length === 0 ? (
            <EmptyState
              icon={<Pulse size={24} />}
              title="暂无事故记录"
              detail="没有可供复盘的事故摘要；这不等于系统已确认健康。"
            />
          ) : (
            <div className="product-console__incident-list" role="list" aria-label="事故摘要列表">
              {incidentItems.map((incident) => (
                <button
                  type="button"
                  className={`product-console__incident-card${currentIncident?.id === incident.id ? ' is-selected' : ''}`}
                  key={incident.id}
                  onClick={() => {
                    setSelectedIncidentId(incident.id)
                    dispatchAction({ type: 'select_incident', incidentId: incident.id }, `select-incident-${incident.id}`)
                  }}
                  disabled={Boolean(pendingAction)}
                  role="listitem"
                >
                  <span className="product-console__incident-topline">
                    <span className="product-console__incident-id">{incident.id}</span>
                    <StatusBadge status={incident.status} compact />
                  </span>
                  <strong>{displayValue(incident.title, '未命名事故')}</strong>
                  <p>{displayValue(incident.summary, '暂无事故摘要')}</p>
                  <span className="product-console__meta-line">
                    <span>{incident.openedAt ? `开始于 ${displayTime(incident.openedAt)}` : '开始时间未知'}</span>
                    {incident.updatedAt ? <span>更新于 {displayTime(incident.updatedAt)}</span> : null}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="product-console__panel product-console__panel--governance">
        <SectionHeading
          eyebrow="05 / 治理"
          title="治理与证据"
          detail="治理规则、来源与技术证据只在抽屉中按需展开。"
          action={
            <button
              type="button"
              className="product-console__button product-console__button--primary"
              onClick={() => openEvidence()}
              disabled={Boolean(pendingAction)}
            >
              <FileText size={16} aria-hidden="true" />
              打开治理与证据
            </button>
          }
        />
        <div className="product-console__governance-summary">
          <div>
            <span className="product-console__summary-label">治理状态</span>
            <StatusBadge status={governance?.status} />
          </div>
          <div>
            <span className="product-console__summary-label">策略</span>
            <strong>{displayValue(governance?.policy, '未提供策略')}</strong>
          </div>
          <div>
            <span className="product-console__summary-label">复核要求</span>
            <strong>
              {governance?.reviewRequired === true
                ? '需要人工复核'
                : governance?.reviewRequired === false
                  ? '上游未要求复核'
                  : '未知'}
            </strong>
          </div>
          <div>
            <span className="product-console__summary-label">证据条数</span>
            <strong>{allEvidence.length} 条已传入</strong>
          </div>
        </div>
      </section>

      {!hasMainData && !loading && !error ? (
        <div className="product-console__empty-shell">
          <EmptyState
            icon={<Info size={24} />}
            title="等待真实快照"
            detail="请由父层传入 snapshot、连接能力、问题或事故记录。"
          />
        </div>
      ) : null}

      <footer className="product-console__footer">
        <span><LockKey size={14} aria-hidden="true" /> {hasSnapshot ? '状态由 Section9 产品 API 快照提供' : '尚未绑定真实产品 API'}</span>
        <span>{snapshot?.generation ? `代次 ${snapshot.generation}` : snapshot?.revision ? `源码 ${String(snapshot.revision).slice(0, 12)}` : '版本未知'}</span>
      </footer>

      {drawerOpen ? (
        <div className="product-console__drawer-layer">
          <div className="product-console__drawer-backdrop" aria-hidden="true" />
          <aside
            className="product-console__drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="product-console-drawer-title"
          >
            <header className="product-console__drawer-header">
              <div>
                <span className="product-console__eyebrow">详情 / 证据</span>
                <h2 id="product-console-drawer-title">治理与证据</h2>
              </div>
              <button
                type="button"
                className="product-console__icon-button"
                onClick={closeEvidence}
                disabled={Boolean(pendingAction)}
                aria-label="关闭治理与证据"
                title="关闭"
              >
                <X size={19} aria-hidden="true" />
              </button>
            </header>
            <div className="product-console__drawer-scroll">
              {selectedEvidence ? (
                <div className="product-console__drawer-focus">
                  <span className="product-console__eyebrow">当前证据</span>
                  <h3>{displayValue(selectedEvidence.label ?? selectedEvidence.id)}</h3>
                  <p>{displayValue(selectedEvidence.summary, '暂无摘要')}</p>
                </div>
              ) : null}

              <section className="product-console__drawer-section">
                <h3>治理边界</h3>
                <dl className="product-console__drawer-kv">
                  <div><dt>策略</dt><dd>{displayValue(governance?.policy, '未提供')}</dd></div>
                  <div><dt>责任人</dt><dd>{displayValue(governance?.owner, '未提供')}</dd></div>
                  <div><dt>限制</dt><dd>{displayValue(governance?.constraints, '未提供')}</dd></div>
                  <div><dt>说明</dt><dd>{displayValue(governance?.detail, '未提供')}</dd></div>
                </dl>
                <StatusBadge status={governance?.status} />
              </section>

              <section className="product-console__drawer-section">
                <div className="product-console__drawer-section-heading">
                  <h3>证据记录</h3>
                  <span>{allEvidence.length} 条</span>
                </div>
                {allEvidence.length === 0 ? (
                  <EmptyState
                    icon={<FileText size={22} />}
                    title="暂无证据"
                    detail="上游未提供证据记录；不会生成占位成功证据。"
                  />
                ) : (
                  <div className="product-console__evidence-list">
                    {allEvidence.map((evidence) => (
                      <EvidenceItem key={evidence.id} evidence={evidence} />
                    ))}
                  </div>
                )}
              </section>

              <section className="product-console__drawer-section">
                <div className="product-console__drawer-section-heading">
                  <h3>当前快照原始字段</h3>
                  <span>主动展开</span>
                </div>
                <TechnicalDetails label="展开 snapshot 技术字段" value={snapshot} />
              </section>
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  )
}

export default ProductConsole
