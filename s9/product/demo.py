"""One local demonstration workflow reusing product v1 and the target adapter.

Business sessions are new records; investigations/signals/evidence/tasks remain
in ProductRegistry's existing v1 tables. No parallel Incident authority is added.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import socket
import time
import uuid

import httpx

from s9 import config
from s9.connectors.evomap import EvoMapSessions
from s9.connectors.langfuse import LangfuseConnector
from s9.product.registry import ProductError
from s9.product.external_runtime import ExternalRuntime
from s9.product.task_runtime import InvestigationTaskRuntime
from s9.product.v1_contracts import BudgetPolicy, ProposalVersion, ProposalState
from integrations.support_agent.observability import redact


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class DemoWorkflow:
    def __init__(self, product, client):
        self.product, self.registry, self.client = product, product.registry, client
        self.native = EvoMapSessions(self.registry, client)
        self.pending = set()
        self.analysis_locks = {}
        self.investigation_gate = asyncio.Lock()
        self.workspace = self.registry.create_workspace('Section9', workspace_id='section9-demo',
            budget=BudgetPolicy(token_limit=100000, validation_reserve_tokens=0, max_concurrency=2),
            idempotency_key='demo-workspace-v1')
        self.application = self.registry.create_application(self.workspace['id'], '客服助手', idempotency_key='demo-app-v1')
        self.wid, self.aid, self.env = self.workspace['id'], self.application['id'], 'local-test'
        self.connection = self.registry.create_connection(self.wid, self.aid, 'Langfuse',
            endpoint='http://127.0.0.1:9030', idempotency_key='demo-connection-v1')
        self.binding = self.registry.create_scope_binding(self.wid, self.aid, self.env, self.connection['id'],
            'application_trace', 'support-agent', read_scopes=['application-live', 'selected-langfuse-trace'],
            idempotency_key='demo-binding-v1')
        self.code_binding = self.registry.create_scope_binding(self.wid, self.aid, self.env, self.connection['id'],
            'application_source', 'support-agent-tools', read_scopes=['pinned-tools-source'], idempotency_key='demo-code-binding-v1')
        root = (config.DATA / 'demo-target').resolve()
        self.runtime = ExternalRuntime(product.repo, product.runtime_commit, 9150,
            root, root / 'application.log', root / 'business.sqlite')
        for job in self.jobs():
            if job['state'] in {'running', 'starting'}:
                job.update(state='interrupted', error='服务已重启。执行结果需要检查，请勿重复提交有副作用的任务。')
                self.save(job)
            if job.get('analysis_state') == 'running':
                job['analysis_state'] = 'interrupted'
                self.save(job)

    def save(self, job):
        job['updated_at'] = now()
        self.registry.put('business_task', job['id'], redact(job), project_id=self.wid, environment_id=self.env)

    def jobs(self):
        return self.registry.list('business_task', project_id=self.wid, environment_id=self.env)

    def get(self, job_id):
        job = self.registry.get('business_task', job_id, project_id=self.wid, environment_id=self.env)
        if job is None:
            raise ProductError('BUSINESS_TASK_NOT_FOUND', '任务不存在', 404)
        return job

    def detail(self, job_id):
        job = self.get(job_id)
        job['investigations'] = {}
        for mode, run_id in job.get('runs', {}).items():
            job['investigations'][mode] = self.registry.get_investigation_run_detail(self.wid, self.aid, self.env, run_id)
        native_id = job.get('runs', {}).get('swarm')
        job['collaboration'] = self.native.binding(self.wid, native_id) if native_id else None
        return job

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def close(self):
        for task in list(self.pending):
            task.cancel()
        await asyncio.gather(*self.pending, return_exceptions=True)
        await asyncio.to_thread(self.runtime.stop)

    async def start(self, message, key):
        if not message.strip():
            raise ProductError('EMPTY_TASK', '请输入要完成的任务', 422)
        job_id = 'business_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        previous = self.registry.get('business_task', job_id, project_id=self.wid, environment_id=self.env)
        if previous:
            if previous['message'] != message:
                raise ProductError('IDEMPOTENCY_CONFLICT', '重复请求对应了不同任务', 409)
            return previous
        if any(j['state'] in {'running', 'starting'} for j in self.jobs()):
            raise ProductError('BUSINESS_TASK_ACTIVE', '当前任务仍在执行，请等待结果', 409)
        job = {'id': job_id, 'message': message, 'state': 'starting', 'created_at': now(), 'events': [],
               'findings': [], 'observations': [], 'runs': {}, 'analysis_state': 'idle', 'source_commit': self.runtime.expected_commit,
               'environment': self.env, 'observation_state': 'connecting', 'reply': None, 'proposal': None}
        reference = self.product.repo / 'src/tools.py'
        if reference.exists():
            text = reference.read_text()
            job['application_reference'] = {'path': 'src/tools.py', 'source_commit': self.runtime.expected_commit,
                'sha256': hashlib.sha256(text.encode()).hexdigest(), 'content': text[:16000], 'truncated': len(text)>16000}
        self.save(job)
        self.spawn(self._business(job))
        return job

    async def _business(self, job):
        request_task = None
        try:
            if not self.product.adapter_metadata.get('observability_sha256'):
                raise ProductError('TARGET_INSTRUMENTATION_MISSING', '请先安装应用观测适配器', 503)
            if not self.runtime.status().get('owned'):
                with socket.socket() as port_check:
                    try:
                        port_check.bind(('127.0.0.1', self.runtime.port))
                    except OSError as exc:
                        raise ProductError('TARGET_PORT_BUSY', '业务端口已被其他服务占用，请检查后重试', 503) from exc
            await asyncio.to_thread(self.runtime.start, model_env=self.product._model_env())
            base = 'http://127.0.0.1:9150'
            async with httpx.AsyncClient(timeout=150, trust_env=False) as client:
                for _ in range(40):
                    try:
                        if (await client.get(base + '/health')).status_code == 200:
                            if not self.runtime.status().get('owned'):
                                raise ProductError('TARGET_OWNERSHIP_LOST', '无法确认业务进程归属，请检查应用连接', 503)
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(.25)
                else:
                    raise ProductError('TARGET_NOT_READY', '客服助手尚未就绪，请重试', 503)
                job['state'] = 'running'
                self.save(job)
                request_task = asyncio.create_task(client.post(base + '/chat', json={'message': job['message'], 'session_id': job['id']}))
                started = time.monotonic()
                while not request_task.done():
                    try:
                        feed = (await client.get(base + '/observations/' + job['id'], timeout=3)).json()
                        job['events'] = feed.get('events', [])
                        job['trace_id'] = feed.get('trace_id')
                        job['observation_state'] = 'watching' if job['events'] else 'waiting'
                        job['last_observed_at'] = now()
                        self.inspect(job)
                    except (httpx.RequestError, ValueError):
                        job['observation_state'] = 'interrupted'
                    job['elapsed_s'] = round(time.monotonic() - started, 1)
                    self.save(job)
                    await asyncio.sleep(.5)
                response = await request_task
                response.raise_for_status()
                body = response.json()
                job.update(state='completed', reply=body.get('reply'), business_result=redact(body),
                           trace_id=body.get('langfuse_trace_id') or job.get('trace_id'), elapsed_s=round(time.monotonic() - started, 1))
                feed = (await client.get(base + '/observations/' + job['id'])).json()
                job['events'] = feed.get('events', job['events'])
                self.inspect(job)
                self.save(job)
            await self.read_trace(job)
            if job['findings']:
                await self._analysis(job['id'], 'swarm')
        except asyncio.CancelledError:
            if request_task and not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            job = self.get(job['id'])
            if job['state'] != 'completed':
                job.update(state='interrupted', error='任务连接已中断，业务结果尚未确认。')
            elif job['observation_state'] != 'verified':
                job.update(observation_state='interrupted', error='业务已完成，观测同步被中断。')
            self.save(job)
            raise
        except Exception as exc:
            completed = job['state'] == 'completed'
            job.update(state='completed' if completed else 'failed',
                       error=exc.message if isinstance(exc, ProductError) else ('业务已完成，观测同步失败。' if completed else '任务未完成，请检查应用连接后重试。'), error_code=type(exc).__name__)
            if completed:
                job['observation_state'] = 'interrupted'
            self.save(job)

    @staticmethod
    def inspect(job):
        findings = {f['key']: f for f in job['findings']}
        for event in job['events']:
            if event.get('level') == 'ERROR':
                key = 'error:' + event['observation_id']
                findings[key] = {'key': key, 'summary': event['name'] + ' 执行出错，原因待核实', 'event_id': event['id'], 'at': event['at']}
        if job.get('elapsed_s', 0) > 30 and job['state'] == 'running':
            findings.setdefault('slow', {'key': 'slow', 'summary': '任务已超过 30 秒，需检查等待原因', 'at': now()})
        result = job.get('business_result') or {}
        if result.get('agent') == 'escalate':
            findings.setdefault('escalated', {'key': 'escalated', 'summary': '业务助手转交人工，需检查未能完成的原因', 'at': now()})
        job['findings'] = list(findings.values())

    async def read_trace(self, job):
        if not job.get('trace_id'):
            job['observation_state'] = 'unavailable'
            self.save(job)
            return
        async with LangfuseConnector() as connector:
            for _ in range(12):
                result = await connector.observations(trace_id=job['trace_id'], include_context=True, limit=100)
                scoped = [r for r in result['rows'] if r.get('trace_id') == job['trace_id']
                    and r.get('metadata', {}).get('project_id') == 'support-agent'
                    and r.get('metadata', {}).get('environment_id') == self.env
                    and r.get('metadata', {}).get('source_commit') == self.runtime.expected_commit
                    and r.get('metadata', {}).get('telemetry_origin') == 'application']
                job['observations'] = scoped
                job['observation_state'] = 'verified' if any(r['name'] == 'support-agent.chat' for r in scoped) else 'indexing'
                self.save(job)
                if job['observation_state'] == 'verified':
                    break
                await asyncio.sleep(2)

    async def analyze(self, job_id, mode, evidence_ids=None):
        job = self.get(job_id)
        if job['state'] != 'completed':
            raise ProductError('TASK_NOT_COMPLETE', '请等待业务任务完成后选择完整证据调查', 409)
        if job['observation_state'] != 'verified':
            raise ProductError('EVIDENCE_NOT_READY', '调用依据尚未同步完成，请稍后调查', 409)
        if job.get('analysis_state') == 'running':
            raise ProductError('INVESTIGATION_ACTIVE', '当前调查仍在进行', 409)
        if mode in job['runs']:
            existing = self.registry.get_investigation_run_detail(self.wid, self.aid, self.env, job['runs'][mode])
            if existing['task_graph']['state'] == 'complete' and (mode == 'single' or job.get('proposal')):
                return job
        if 'incident_id' not in job:
            known = {o['id'] for o in job['observations']}
            if evidence_ids is not None and (not evidence_ids or not set(evidence_ids).issubset(known)):
                raise ProductError('INVALID_EVIDENCE', '请选择当前任务已有的调用证据', 422)
            job['selected_evidence_ids'] = list(known) if evidence_ids is None else evidence_ids
        job['analysis_state'] = 'running'
        job.pop('analysis_error', None)
        self.save(job)
        self.spawn(self._analysis(job_id, mode))
        return job

    async def _analysis(self, job_id, mode):
        lock = self.analysis_locks.setdefault(job_id, asyncio.Lock())
        async with self.investigation_gate, lock:
            job = self.get(job_id)
            job['analysis_state'] = 'running'
            job.pop('analysis_error', None)
            self.save(job)
            started = time.monotonic()
            attempt_started_at = now()
            try:
                if 'incident_id' not in job:
                    context = {'business_task': job['message'], 'business_result': job.get('business_result'),
                               'findings': job['findings'], 'trace_id': job.get('trace_id'),
                               'observations': [o for o in job['observations'] if o['id'] in job.get('selected_evidence_ids', [x['id'] for x in job['observations']])], 'environment': self.env,
                               'source_commit': job['source_commit'], 'coverage': job['observation_state']}
                    # Keep the same bounded context for both arms, including truncation disclosure.
                    context = redact(context)
                    if len(json.dumps(context).encode()) > 70000:
                        context['observations'] = [{**r, 'input': str(r.get('input'))[:2000],
                            'output': str(r.get('output'))[:2000]} for r in context['observations'][:20]]
                        context['coverage'] = 'bounded_context'
                    job['evidence_sha256'] = digest({'context':context, 'application_reference':job.get('application_reference')})
                    ingested = self.registry.ingest_signals(self.wid, self.aid, self.env, self.binding['id'], [{
                        'source_id': job['id'], 'source_version': job['evidence_sha256'],
                        'deduplication_key': job['id'], 'signal_type': 'application_task', 'source_kind': 'chat',
                        'occurred_at': job['created_at'], 'observed_at': job['created_at'],
                        'summary': job['findings'][0]['summary'] if job['findings'] else '用户请求检查本次任务，尚未认定异常',
                        'source_context': context}], idempotency_key='evidence-v2-' + job['id'])
                    signal = (ingested.get('created') or ingested.get('items') or ingested.get('duplicates'))[0]
                    signals = [signal]
                    if job.get('application_reference'):
                        signals.extend(self.registry.ingest_signals(self.wid, self.aid, self.env, self.code_binding['id'], [{
                            'source_id': job['id'] + '-source', 'source_version': job['application_reference']['sha256'],
                            'deduplication_key': job['id'] + '-source', 'signal_type': 'application_source', 'source_kind': 'manual',
                            'occurred_at': job['created_at'], 'observed_at': job['created_at'], 'summary': '当前客服工具实现与业务政策，供独立核查',
                            'source_context': job['application_reference']}], idempotency_key='source-' + job['id'])['items'])
                    incident = self.registry.create_incident(self.wid, self.aid, self.env, job['message'][:200], 'low',
                        [{'signal_id': item['id'], 'expected_revision': item['revision']} for item in signals], idempotency_key='incident-' + job['id'])
                    job['incident_id'] = incident['id']
                incident = self.registry.get_incident(self.wid, self.aid, self.env, job['incident_id'])
                for existing_id in job['runs'].values():
                    old = self.registry.get_investigation_run_detail(self.wid, self.aid, self.env, existing_id)
                    if old['task_graph']['state'] == 'complete':
                        self.registry.complete_investigation_task_graph(self.wid, self.aid, self.env, existing_id)
                if mode not in job['runs']:
                    run = self.registry.create_investigation_run(self.wid, self.aid, self.env, job['incident_id'],
                        expected_incident_revision=incident['revision'], idempotency_key='run-' + mode + job['id'] + '-' + str(job.get('proposal_version', 1)), execution_mode=mode)
                    job['runs'][mode] = run['run']['id']
                    self.save(job)
                run_id = job['runs'][mode]
                detail = self.registry.get_investigation_run_detail(self.wid, self.aid, self.env, run_id)
                for task in detail['task_graph']['tasks']:
                    if task['state'] == 'failed':
                        self.registry.retry_investigation_task(self.wid, self.aid, self.env, run_id,
                            task['id'], '用户继续未完成调查，保留之前的尝试与用量',
                            expected_revision=task['revision'], idempotency_key='retry-' + uuid.uuid4().hex)
                runtime = InvestigationTaskRuntime(self.registry, self.client,
                    collaboration=self.native if mode == 'swarm' else None, feedback=job.get('review_feedback'))
                result = await runtime.execute(self.wid, self.aid, self.env, run_id)
                job = self.get(job_id)
                job.setdefault('comparison', {})[mode] = {'state': result['execution']['state'],
                    'elapsed_s': round(time.monotonic() - started, 1), 'evidence_sha256': job['evidence_sha256'],
                    'token_limit': self.workspace['budget']['token_limit'], 'model': config.MODEL}
                self.save(job)
                if result['execution']['state'] != 'complete':
                    raise ProductError('INVESTIGATION_INCOMPLETE', '调查暂时中断，进度和用量已保留。可以继续调查。', 502)
                self.registry.complete_investigation_task_graph(self.wid, self.aid, self.env, run_id)
                if mode == 'swarm':
                    synth = next(t for t in result['detail']['task_graph']['tasks'] if t['role'] == 'synthesizer')
                    proposal = {'version': job.get('proposal_version', 1), 'run_id': run_id, 'summary': synth['result']['summary'],
                                'evidence_ids': synth['result']['evidence_ids'], 'conclusion': synth['result']['conclusion'],
                                'scope': '调查建议审核，不自动修改业务系统', 'created_at': now()}
                    proposal['sha256'] = digest(proposal)
                    proposal['id'] = 'proposal_' + job['id'] + '_' + str(proposal['version'])
                    contract = ProposalVersion.model_validate({
                        'id': proposal['id'], 'workspace_id': self.wid, 'application_id': self.aid,
                        'environment_id': self.env, 'incident_id': job['incident_id'], 'run_id': run_id,
                        'revision': 1, 'created_at': proposal['created_at'], 'updated_at': proposal['created_at'],
                        'proposal_id': 'proposal_' + job['id'], 'version': proposal['version'],
                        'proposal_sha256': proposal['sha256'], 'target_refs': ['support-agent@' + job['source_commit']],
                        'actions': [{'action_id': 'review-recommendation', 'schema_ref': 'section9:readonly-recommendation:v1',
                            'target_ref': 'support-agent@' + job['source_commit'], 'payload_sha256': digest(synth['result'])}],
                        'risk_summary': '只读调查建议；认可不授权业务写入或自动发布。',
                        'rollback_conditions': ['退回本版本，依据审核意见生成新版本；原版本与用量保留。'],
                        'validation_plan_ref': 'investigation:' + run_id,
                        'budget_token_limit': self.workspace['budget']['token_limit'], 'state': ProposalState.READY_FOR_APPROVAL,
                    })
                    self.registry.put('proposal_version', proposal['id'],
                        {'contract': contract.model_dump(mode='json'), 'content': proposal},
                        project_id=self.wid, environment_id=self.env, incident_id=job['incident_id'])
                    job['proposal'] = proposal
                job['analysis_state'] = 'complete'
                self.save(job)
            except asyncio.CancelledError:
                job = self.get(job_id)
                job.update(analysis_state='interrupted', analysis_error='调查连接已中断，已完成步骤与用量保留。')
                self.save(job)
                raise
            except Exception as exc:
                job = self.get(job_id)
                job.update(analysis_state='failed', analysis_error=exc.message if isinstance(exc, ProductError) else '调查中断：' + type(exc).__name__)
                self.save(job)
            finally:
                latest = self.get(job_id)
                run_id = latest.get('runs', {}).get(mode)
                latest.setdefault('analysis_attempts', []).append({'mode': mode, 'run_id': run_id,
                    'started_at': attempt_started_at, 'finished_at': now(),
                    'elapsed_s': round(time.monotonic() - started, 1), 'state': latest['analysis_state']})
                if mode in latest.get('comparison', {}):
                    latest['comparison'][mode]['elapsed_s'] = round(sum(a['elapsed_s'] for a in latest['analysis_attempts'] if a['run_id'] == run_id), 1)
                self.save(latest)

    def review(self, job_id, proposal_sha, decision, reason):
        job = self.get(job_id)
        if not job.get('proposal') or job['proposal']['sha256'] != proposal_sha:
            raise ProductError('PROPOSAL_CHANGED', '方案已变化，请刷新后审核', 409)
        if job.get('review'):
            if job['review']['decision'] == decision and job['review']['reason'] == reason:
                return job
            raise ProductError('ALREADY_REVIEWED', '该版本已有审核记录', 409)
        job['review'] = {'proposal_sha256': proposal_sha, 'decision': decision, 'reason': reason,
                         'actor': 'local-operator', 'at': now()}
        if job['proposal'].get('id'):
            stored = self.registry.get('proposal_version', job['proposal']['id'], project_id=self.wid,
                                       environment_id=self.env, incident_id=job['incident_id'])
            if not stored or stored['contract']['proposal_sha256'] != proposal_sha:
                raise ProductError('PROPOSAL_CHANGED', '方案记录不匹配，请刷新后重试', 409)
            contract = ProposalVersion.model_validate_json(json.dumps(stored['contract']))
            contract = contract.model_copy(update={'state': ProposalState(decision), 'revision': contract.revision + 1, 'updated_at': now()})
            self.registry.put('proposal_version', job['proposal']['id'],
                {'contract': contract.model_dump(mode='json'), 'content': stored['content'], 'review': job['review']},
                project_id=self.wid, environment_id=self.env, incident_id=job['incident_id'])
        self.save(job)
        return job

    async def revise(self, job_id):
        job = self.get(job_id)
        if job.get('review', {}).get('decision') != 'rejected':
            raise ProductError('REVIEW_REQUIRED', '请先退回并填写需要补充的内容', 409)
        if job['analysis_state'] == 'running':
            raise ProductError('INVESTIGATION_ACTIVE', '调查正在进行', 409)
        job.setdefault('history', []).append({k: job.get(k) for k in ('proposal', 'review', 'runs', 'comparison')})
        job['review_feedback'] = job['review']['reason']
        job['proposal_version'] = job['proposal']['version'] + 1
        job.update(runs={}, comparison={}, proposal=None, review=None, analysis_state='running')
        self.save(job)
        self.spawn(self._analysis(job_id, 'swarm'))
        return job
