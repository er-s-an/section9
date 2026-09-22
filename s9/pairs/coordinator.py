from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import zipfile

from s9.pairs.catalog import PairCatalog
from s9.pairs.contracts import ARMS, FAULTS, content_config, freeze_spec
from s9.pairs.journal import PairJournal
from s9.pairs.projection import project_arm
from s9.pairs.runtime import ArmRuntime, RuntimeContext
from s9.store import ACTIVE, Rejected, digest, now, uid


class PairCoordinator:
    def __init__(self, root, gateway, telemetry, identity):
        self.root, self.gateway, self.telemetry, self.identity = root, gateway, telemetry, identity
        root.mkdir(parents=True, exist_ok=True)
        self.catalog = PairCatalog(root / 'catalog.sqlite')
        self.journal = PairJournal(root / 'journal.sqlite')
        self.runtimes = {}
        self.lock = asyncio.Lock()
        self.task = None

    def runtime(self, pair, arm):
        key = (pair['pair_id'], arm)
        if key not in self.runtimes:
            rid = pair[arm + '_run_id']
            context = RuntimeContext(pair['pair_id'], rid, arm, pair['spec_hash'], self.root / 'pairs' / pair['pair_id'] / arm / rid)
            self.runtimes[key] = ArmRuntime(context, pair['immutable_spec']['baseline_config_snapshot'],
                                            gateway=self.gateway, telemetry=self.telemetry, identity=self.identity, spec=pair['immutable_spec'])
        return self.runtimes[key]

    async def boot(self):
        # A restart never resumes old worker leases or replaces one arm in place.
        for pair in self.catalog.list():
            for arm in ARMS:
                runtime = self.runtime(pair, arm)
                runtime.store.recover_usage(reason='SERVER_RESTARTED')
            if pair['status'] in {'preparing', 'ready', 'running'} or any(
                    (self.runtime(pair, arm).store.run(pair[arm + '_run_id']) or {}).get('status') in ACTIVE for arm in ARMS):
                for arm in ARMS:
                    runtime = self.runtime(pair, arm)
                    runtime.store.fail_run(pair[arm + '_run_id'], 'SERVER_RESTARTED')
                    runtime.store.emit('pair.interrupted', {'summary': '服务重启中断原 Pair；完整重跑需新建 Pair'}, run_id=pair[arm + '_run_id'])
                    self.journal.ingest(pair, arm, runtime.store)
                self.catalog.update(pair['pair_id'], status='setup_failed' if pair['status'] == 'preparing' else 'interrupted',
                                    ended_at=now(), comparison_integrity={'eligible': False, 'reasons': ['SERVER_RESTARTED']})
        self.task = asyncio.create_task(self.watch())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for pair in self.catalog.list():
            if any((self.runtime(pair, arm).store.run(pair[arm + '_run_id']) or {}).get('status') in ACTIVE for arm in ARMS):
                await self.reset(pair['pair_id'], reason='SERVER_STOPPED')
        for runtime in self.runtimes.values():
            await runtime.stop()

    async def create(self, request, key=None):
        async with self.lock:
            if request.previous_pair_id:
                self.catalog.get(request.previous_pair_id)
            spec = freeze_spec(request, self.identity)
            pair, fresh = self.catalog.create(spec, request.model_dump(), key)
            if not fresh:
                return pair
            try:
                for arm in ARMS:
                    runtime = self.runtime(pair, arm)
                    if digest(content_config(runtime.store.current_config())) != spec['baseline_config_hash']:
                        raise Rejected('INITIAL_CONFIG_MISMATCH', '两侧健康起点不一致')
                    runtime.store.emit('arm.prepared', {'summary': '独立配置、权限和预算账户已准备；尚未调用模型'}, run_id=pair[arm + '_run_id'])
                    self.journal.ingest(pair, arm, runtime.store)
                return self.catalog.update(pair['pair_id'], status='ready')
            except Exception as exc:
                self.catalog.update(pair['pair_id'], status='setup_failed', ended_at=now(),
                                    failure_reason=type(exc).__name__, comparison_integrity={'eligible': False, 'reasons': ['PREPARATION_FAILED']})
                raise

    async def start(self, pair_id, expected_spec_hash):
        async with self.lock:
            pair = self.catalog.get(pair_id)
            if pair['spec_hash'] != expected_spec_hash:
                raise Rejected('SPEC_MISMATCH', '启动规格与冻结 Pair 不同')
            if pair['status'] != 'ready':
                raise Rejected('PAIR_NOT_READY', 'Pair 只能显式启动一次；完整重跑需新建 Pair')
            if any((self.runtime(p, arm).store.run(p[arm + '_run_id']) or {}).get('status') in ACTIVE
                   for p in self.catalog.list() for arm in ARMS):
                raise Rejected('PAIR_ACTIVE', '一次只允许一个成对实验，以限制远程模型消耗')
            spec = pair['immutable_spec']
            identity_fields = ('backend_source_hash', 'frontend_build_hash', 'dependency_lock_hash', 'fixture_hash', 'acceptance_contract_hash')
            if any(spec['source_identity'][k] != self.identity[k] for k in identity_fields):
                raise Rejected('SOURCE_MISMATCH', '来源发生变化，需新建 Pair')
            if spec['fault_spec'] != FAULTS[spec['scenario']]:
                raise Rejected('FAULT_MISMATCH', '冻结故障与执行器不一致')
            barrier = uid('barrier')
            try:
                for arm in ARMS:
                    core = self.runtime(pair, arm).prepare_core()
                    await core.start()
                # Separate transactions, with worker execution gated until both commits.
                applied = {}
                for arm in ARMS:
                    core = self.runtime(pair, arm).core
                    await core.inject(spec['scenario'], 'swarm' if arm == 'swarm' else 'single', spec['seed'],
                                      environment='showcase', run_id=pair[arm + '_run_id'], schedule=False)
                    applied[arm] = now()
                    with core.store.tx() as db:
                        run = core.store.get(db, 'runs', pair[arm + '_run_id'])
                        run['manifest'].update(pair_id=pair_id, arm=arm, spec_hash=pair['spec_hash'],
                                               pair_spec=spec, baseline_config_hash=spec['baseline_config_hash'],
                                               injected_config_hash=digest(content_config(core.store.current_config(db))))
                        core.store.save(db, 'runs', run)
                hashes = [digest(content_config(self.runtime(pair, arm).store.current_config())) for arm in ARMS]
                if len(set(hashes)) != 1:
                    raise Rejected('INJECTED_CONFIG_MISMATCH', '故障后的两侧起点不一致')
                pair = self.catalog.update(pair_id, status='running', started_at=now(), start_barrier_id=barrier,
                                           fault_applied_at=applied, model_calls_started=True)
                release = []
                for arm in ARMS:
                    core = self.runtime(pair, arm).core
                    core.execution_enabled = True
                    stamp = time.monotonic()
                    release.append(stamp)
                    core.store.emit('arm.started', {'barrier_id': barrier, 'summary': '共同启动屏障已释放'}, run_id=pair[arm + '_run_id'])
                    core.activate_run(pair[arm + '_run_id'])
                return self.catalog.update(pair_id, start_skew_ms=round((max(release)-min(release))*1000, 3))
            except Exception as exc:
                for arm in ARMS:
                    runtime = self.runtime(pair, arm)
                    runtime.store.fail_run(pair[arm + '_run_id'], 'PAIR_SETUP_FAILED')
                    if runtime.core:
                        await runtime.core.reset()
                    await runtime.stop()
                    self.journal.ingest(pair, arm, runtime.store)
                self.catalog.update(pair_id, status='setup_failed', ended_at=now(), failure_reason=type(exc).__name__,
                                    comparison_integrity={'eligible': False, 'reasons': ['START_PREPARATION_FAILED']})
                raise

    def refresh(self, pair_id):
        pair = self.catalog.get(pair_id)
        for arm in ARMS:
            self.journal.ingest(pair, arm, self.runtime(pair, arm).store)
        if pair['status'] == 'running':
            runs = [self.runtime(pair, arm).store.run(pair[arm + '_run_id']) for arm in ARMS]
            if all(r and r['status'] not in ACTIVE for r in runs):
                responses = {arm: [e['payload'] for e in self.journal.read_all(pair_id, arm)
                                   if e['event_type'] == 'model.completed' and e['payload'].get('request_id')]
                             for arm in ARMS}
                returned_models = {e.get('returned_model') for rows in responses.values() for e in rows} - {None}
                reasons = []
                if len(returned_models) > 1:
                    reasons.append('PROVIDER_MODEL_IDENTITY_MISMATCH')
                if any(not rows or any(not e.get('returned_model') for e in rows) for rows in responses.values()):
                    reasons.append('PROVIDER_MODEL_IDENTITY_UNCONFIRMED')
                pair = self.catalog.update(pair_id, status='completed', ended_at=now(),
                    comparison_integrity={'eligible': not reasons, 'reasons': reasons,
                                          'observed_provider_models': sorted(returned_models),
                                          'start_skew_ms': pair.get('start_skew_ms'), 'shared_hardware': True,
                                          'cache_controlled': False, 'budget_accounts': 'independent'})
        if pair['status'] == 'interrupted' and not pair.get('arms_ended_at'):
            runs = [self.runtime(pair, arm).store.run(pair[arm + '_run_id']) for arm in ARMS]
            if all(not r or r['status'] not in ACTIVE for r in runs):
                pair = self.catalog.update(pair_id, arms_ended_at=now())
        return pair

    async def watch(self):
        while True:
            for pair in self.catalog.list():
                if pair['status'] in {'running', 'interrupted'}:
                    self.refresh(pair['pair_id'])
                # Preserve active other arm after a scoped intervention.
                for arm in ARMS:
                    runtime = self.runtimes.get((pair['pair_id'], arm))
                    if runtime and runtime.core:
                        run = runtime.store.run(pair[arm + '_run_id'])
                        if run and run['status'] not in ACTIVE and not runtime.core.model.status().get('active_requests') and not runtime.core.verifying:
                            await runtime.stop()
                            self.journal.ingest(pair, arm, runtime.store)
            await asyncio.sleep(.5)

    async def reset(self, pair_id, arm=None, reason='OPERATOR_RESET'):
        async with self.lock:
            pair = self.catalog.get(pair_id)
            results = {}
            for target in (ARMS if arm is None else (arm,)):
                runtime = self.runtime(pair, target)
                if runtime.core:
                    runtime.core.execution_enabled = False
                    results[target] = await runtime.core.reset()
                    await runtime.stop()
                else:
                    results[target] = runtime.store.reset()
                runtime.store.emit('pair.interrupted', {'summary': reason, 'target_arm': arm}, run_id=pair[target + '_run_id'])
                self.journal.ingest(pair, target, runtime.store)
            self.catalog.update(pair_id, status='interrupted', ended_at=now(),
                                comparison_integrity={'eligible': False, 'reasons': [reason], 'interrupted_arm': arm})
            return {'pair_id': pair_id, 'status': 'interrupted', 'results': results, 'rerun_requires_new_pair': True}

    def snapshot(self, pair_id, arm):
        pair = self.refresh(pair_id)
        runtime = self.runtime(pair, arm)
        rid = pair[arm + '_run_id']
        with runtime.store.tx() as db:
            agents = [json.loads(r[0]) for r in db.execute('SELECT data FROM agents')]
        watermark = self.journal.watermark(pair_id, arm)
        events = self.journal.read_all(pair_id, arm, watermark=watermark)
        # All reads are synchronous on the authority event loop; nothing can
        # mutate between journal import, run read and cursor publication.
        projected = project_arm(pair, arm, runtime.store.run(rid), events, config=runtime.store.current_config(), agents=agents,
                           usage=runtime.store.usage_records(rid), dependencies=runtime.core.snapshot()['dependencies'] if runtime.core else {},
                           as_of_sequence=watermark)
        tail = events[-200:]
        projected['events'] = tail
        projected['log_page'] = {'range_start': int(tail[0]['sequence']) if tail else watermark,
                                 'range_end': int(tail[-1]['sequence']) if tail else watermark,
                                 'next_after': int(tail[-1]['sequence']) if tail else watermark,
                                 'has_more': bool(tail and int(tail[0]['sequence']) > 0 and len(events) > len(tail)),
                                 'watermark': watermark, 'view': 'tail'}
        return projected

    @staticmethod
    def _export_redact(value):
        sensitive = ('token_hash', 'access_token', 'refresh_token', 'secret', 'credential',
                     'password', 'authorization', 'api_key', 'apikey', 'private_key')
        if isinstance(value, dict):
            return {k: ('[REDACTED]' if any(x in str(k).lower() for x in sensitive)
                        else PairCoordinator._export_redact(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [PairCoordinator._export_redact(v) for v in value]
        return value

    def export_zip(self, pair_id):
        """Build a bounded, deterministic, read-only evidence export in memory."""
        pair = self.refresh(pair_id)
        files = {}
        def add(path, value):
            files[path] = json.dumps(self._export_redact(value), ensure_ascii=False, sort_keys=True, indent=2).encode()
        add('pair.json', {'pair_id': pair_id, 'status': pair.get('status'), 'created_at': pair.get('created_at'),
                          'started_at': pair.get('started_at'), 'ended_at': pair.get('ended_at'),
                          'comparison_integrity': pair.get('comparison_integrity'),
                          'source_identity': pair['immutable_spec']['source_identity']})
        add('spec.json', pair.get('immutable_spec', {}))
        counts = {}
        for arm in ARMS:
            runtime = self.runtime(pair, arm)
            rid = pair[arm + '_run_id']
            watermark = self.journal.watermark(pair_id, arm)
            events = self.journal.read_all(pair_id, arm, watermark=watermark)
            add(f'{arm}/run.json', runtime.store.run(rid) or {})
            add(f'{arm}/config.json', runtime.store.current_config())
            usage = runtime.store.usage_records(rid)
            add(f'{arm}/usage.json', usage)
            for page, start in enumerate(range(0, len(events), 500)):
                add(f'{arm}/events-{page:04d}.json', events[start:start + 500])
            counts[arm] = {'events': len(events), 'usage_records': len(usage),
                           'event_pages': (len(events) + 499) // 500, 'watermark': watermark,
                           'range_start': int(events[0]['sequence']) if events else watermark,
                           'range_end': int(events[-1]['sequence']) if events else watermark}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
        manifest = {'schema_version': '1', 'pair_id': pair_id, 'spec': {'file': 'spec.json'},
                    'run': {arm: f'{arm}/run.json' for arm in ARMS},
                    'config': {arm: f'{arm}/config.json' for arm in ARMS},
                    'usage': {arm: f'{arm}/usage.json' for arm in ARMS}, 'counts': counts,
                    'hashes': hashes, 'source_identity': self._export_redact(
                        (pair.get('immutable_spec') or {}).get('source_identity', pair.get('source_identity', {}))),
                    'export': {'read_only': True, 'model_calls': False, 'events_page_size': 500}}
        add('manifest.json', manifest)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                archive.writestr(name, files[name])
        return output.getvalue(), f'{pair_id}-evidence.zip'

    def authenticate(self, token):
        for runtime in self.runtimes.values():
            if runtime.core:
                try:
                    return runtime.core, runtime.store.authenticate(token)
                except Rejected:
                    pass
        raise Rejected('UNAUTHENTICATED', '无效或已停止的 arm 工作身份', 401)

    def telemetry_store(self, pair_id, run_id, arm):
        try:
            pair = self.catalog.get(pair_id)
        except Rejected:
            return None
        if arm not in ARMS or pair[arm + '_run_id'] != run_id:
            return None
        return self.runtime(pair, arm).store
