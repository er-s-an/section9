import asyncio
from types import SimpleNamespace

import httpx
import pytest

from s9.product.demo import DemoWorkflow, digest
from s9.product.registry import ProductError, ProductRegistry
from s9.product.v1_contracts import BudgetPolicy


def workflow(tmp_path, monkeypatch):
    import s9.product.demo as demo
    monkeypatch.setattr(demo.config,'DATA',tmp_path/'data')
    repo=tmp_path/'target';(repo/'src').mkdir(parents=True)
    (repo/'src/tools.py').write_text('def get_order_status(order_id): return {"status":"delivered"}')
    product=SimpleNamespace(registry=ProductRegistry(tmp_path/'registry.sqlite'),repo=repo,runtime_commit='a'*40)
    service=DemoWorkflow(product,httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,json={}))))
    service.spawn=lambda coro:coro.close()
    return service


def test_business_idempotency_and_empty_input(tmp_path,monkeypatch):
    service=workflow(tmp_path,monkeypatch)
    async def run():
        with pytest.raises(ProductError,match='请输入'):
            await service.start('   ','empty')
        first=await service.start('查订单1001','k')
        assert (await service.start('查订单1001','k'))['id']==first['id']
        with pytest.raises(ProductError,match='不同任务'):
            await service.start('申请退款','k')
        with pytest.raises(ProductError,match='仍在执行'):
            await service.start('另一个订单','k2')
        assert service.runtime.data_path.is_absolute()
        assert first['application_reference']['source_commit']=='a'*40
        await service.client.aclose()
    asyncio.run(run())


def test_evidence_scope_and_approval_version_binding(tmp_path,monkeypatch):
    service=workflow(tmp_path,monkeypatch)
    async def run():
        job=await service.start('查询订单','key');job.update(state='completed',observation_state='verified',observations=[{'id':'local-evidence'}]);service.save(job)
        with pytest.raises(ProductError,match='已有的调用证据'):
            await service.analyze(job['id'],'swarm',['foreign-evidence'])
        await service.analyze(job['id'],'swarm',['local-evidence'])
        assert service.get(job['id'])['selected_evidence_ids']==['local-evidence']
        job=service.get(job['id']);job.update(analysis_state='complete',proposal={'version':1,'sha256':'a'*64,'summary':'查核来源'},runs={'swarm':'old-run'});service.save(job)
        with pytest.raises(ProductError,match='方案已变化'):
            service.review(job['id'],'b'*64,'approved','同意')
        service.review(job['id'],'a'*64,'rejected','补充独立证据')
        revised=await service.revise(job['id'])
        assert revised['proposal_version']==2
        assert revised['review_feedback']=='补充独立证据'
        assert revised['history'][0]['runs']=={'swarm':'old-run'}
        assert revised['review'] is None and revised['proposal'] is None
        await service.client.aclose()
    asyncio.run(run())


def test_live_error_deduplication_and_insufficient_observation(tmp_path,monkeypatch):
    job={'findings':[],'state':'running','elapsed_s':31,'events':[{'id':'e','observation_id':'o','name':'查询','level':'ERROR','at':'now'}]}
    DemoWorkflow.inspect(job);DemoWorkflow.inspect(job)
    assert len(job['findings'])==2
    service=workflow(tmp_path,monkeypatch)
    async def run():
        task=await service.start('查询','k');task.update(state='completed',observation_state='indexing');service.save(task)
        with pytest.raises(ProductError,match='尚未同步'):
            await service.analyze(task['id'],'swarm')
        await service.client.aclose()
    asyncio.run(run())


def test_full_analysis_reuses_v1_sources_and_freezes_selected_context(tmp_path,monkeypatch):
    import s9.product.demo as demo
    service=workflow(tmp_path,monkeypatch)
    class Runtime:
        def __init__(self, registry, client, **kwargs):self.registry=registry
        async def execute(self,w,a,e,r):
            detail=self.registry.get_investigation_run_detail(w,a,e,r)
            assert len(detail['evidence'])==2
            assert len([t for t in detail['task_graph']['tasks'] if t['role']=='investigator'])==2
            while detail['task_graph']['state'] != 'complete':
                tasks=detail['task_graph']['tasks']; succeeded={t['id'] for t in tasks if t['state']=='succeeded'}
                t=next(t for t in tasks if t['state']=='open' and set(t['dependencies']).issubset(succeeded))
                claim=self.registry.claim_investigation_task(w,a,e,r,t['id'],'test-worker',[t['capability']],expected_revision=t['revision'],lease_seconds=60,idempotency_key='claim-'+t['id'])
                ids=t['input_evidence_ids'];result={'summary':'根据已有证据形成待核查建议','evidence_ids':ids}
                if t['role']=='investigator':result['hypotheses']=[{'statement':'政策规则需确认','confidence':0.6,'support_evidence_ids':ids}]
                elif t['role']=='challenger_seed':result['challenge_questions']=[{'question':'适用规则是什么','evidence_ids':ids,'failure_condition':'规则不适用则假设不成立'}]
                elif t['role']=='challenger_review':result['review_verdict']='insufficient'
                else:result['conclusion']='needs_data'
                self.registry.finish_investigation_task(w,a,e,r,t['id'],'test-worker',claim['epoch'],result,None,expected_revision=claim['revision'],idempotency_key='finish-'+t['id'])
                detail=self.registry.get_investigation_run_detail(w,a,e,r)
            return {'detail':detail,'execution':{'state':'complete'}}
    monkeypatch.setattr(demo,'InvestigationTaskRuntime',Runtime)
    async def run():
        j=await service.start('核查退款','key');j.update(state='completed',observation_state='verified',business_result={'reply':'可以退款'},observations=[{'id':'selected','name':'资格工具','output':[{'detail':'x'*16000} for _ in range(5)]},{'id':'excluded','output':'not selected'}],selected_evidence_ids=['selected']);service.save(j)
        await service._analysis(j['id'],'swarm')
        done=service.get(j['id'])
        assert done['analysis_state']=='complete',done.get('analysis_error')
        run=service.registry.get_investigation_run_detail(service.wid,service.aid,service.env,done['runs']['swarm'])
        context=next(s['source_context'] for s in run['input_snapshot']['signals'] if s['source_kind']=='chat')
        assert [o['id'] for o in context['observations']]==['selected']
        assert context['coverage']=='bounded_context'
        assert done['proposal']['evidence_ids']
        assert run['run']['state']=='inconclusive'
        saved=service.registry.get('proposal_version',done['proposal']['id'],project_id=service.wid,environment_id=service.env,incident_id=done['incident_id'])
        assert saved['contract']['state']=='ready_for_approval'
        service.review(done['id'],done['proposal']['sha256'],'approved','认可调查建议')
        saved=service.registry.get('proposal_version',done['proposal']['id'],project_id=service.wid,environment_id=service.env,incident_id=done['incident_id'])
        assert saved['contract']['state']=='approved'
        # A completed graph remains idempotent even when another arm failed.
        done=service.get(j['id']);done['analysis_state']='failed';service.save(done)
        await service.analyze(j['id'],'swarm')
        assert service.get(j['id'])['proposal']['sha256']==done['proposal']['sha256']
        assert service.get(j['id'])['review']['decision']=='approved'
        assert service.registry.get_incident(service.wid,service.aid,service.env,done['incident_id'])['state'] != 'resolved'
        await service.client.aclose()
    asyncio.run(run())


def test_explicit_continue_retires_old_policy_run_and_preserves_usage_history(tmp_path,monkeypatch):
    import s9.product.demo as demo
    service=workflow(tmp_path,monkeypatch)
    class IncompleteRuntime:
        def __init__(self, registry, client, **kwargs): self.registry=registry
        async def execute(self,w,a,e,r):
            detail=self.registry.get_investigation_run_detail(w,a,e,r)
            return {'detail':detail,'execution':{'state':'failed'}}
    monkeypatch.setattr(demo,'InvestigationTaskRuntime',IncompleteRuntime)

    async def run():
        current=service.registry.get_workspace(service.wid)
        service.registry.update_workspace_budget(service.wid,
            BudgetPolicy(token_limit=100_000,validation_reserve_tokens=0,max_concurrency=2),
            expected_revision=current['revision'],idempotency_key='test-policy-rev-1')
        job=await service.start('核查订单退款','policy-change-test')
        job.update(state='completed',observation_state='verified',business_result={'reply':'请提供订单号'},
            observations=[{'id':'evidence-1','name':'support-agent.chat'}],selected_evidence_ids=['evidence-1'])
        service.save(job)
        await service._analysis(job['id'],'single')
        first=service.get(job['id'])
        old_id=first['runs']['single']
        old=service.registry.get_investigation_run_detail(service.wid,service.aid,service.env,old_id)
        assert old['run']['policy_revision']==2
        assert old['run']['token_limit']==100_000

        # Simulate spend already recorded by the old run; a policy update must not erase it.
        task=next(t for t in old['task_graph']['tasks'] if t['role']=='investigator')
        claim=service.registry.claim_investigation_task(service.wid,service.aid,service.env,old_id,
            task['id'],'test-worker',[task['capability']],expected_revision=task['revision'],
            lease_seconds=60,idempotency_key='policy-test-claim')
        service.registry.reserve_investigation_model_usage(service.wid,service.aid,service.env,old_id,
            task['id'],'test-worker',claim['epoch'],request_id='policy-test-usage',provider='test',model='test',
            prompt_sha256='a'*64,reserved_tokens=1000)
        service.registry.authorize_investigation_model_dispatch(service.wid,service.aid,service.env,old_id,
            task['id'],'test-worker',claim['epoch'],request_id='policy-test-usage')
        service.registry.settle_investigation_model_usage(service.wid,service.aid,request_id='policy-test-usage',
            actual_tokens=700,provider_called=True,error_code='MODEL_EMPTY')

        current=service.registry.get_workspace(service.wid)
        service.registry.update_workspace_budget(service.wid,
            BudgetPolicy(token_limit=250_000,validation_reserve_tokens=0,max_concurrency=2),
            expected_revision=current['revision'],idempotency_key='test-policy-rev-2')
        assert service.registry.get_workspace(service.wid)['policy_revision']==3
        assert service.registry.get_investigation_run_detail(service.wid,service.aid,service.env,old_id)['run']['policy_revision']==2
        # This call represents the user's explicit Continue click. There is no background resume.
        service.spawn=lambda coro: service.pending.add(asyncio.create_task(coro))
        await service.analyze(job['id'],'single')
        await asyncio.gather(*list(service.pending))
        continued=service.get(job['id'])
        new_id=continued['runs']['single']
        assert new_id != old_id
        assert continued['incident_id']==first['incident_id']
        old_after=service.registry.get_investigation_run_detail(service.wid,service.aid,service.env,old_id)
        new=service.registry.get_investigation_run_detail(service.wid,service.aid,service.env,new_id)
        assert old_after['run']['state']=='inconclusive'
        assert old_after['run']['policy_revision']==2 and old_after['run']['token_limit']==100_000
        assert old_after['model_usage'][0]['actual_tokens']==700
        assert new['run']['policy_revision']==3 and new['run']['token_limit']==250_000
        history=continued['run_history']['single']
        assert history[0]['run_id']==old_id
        assert history[0]['known_total_tokens']==700
        assert history[0]['model_usage'][0]['actual_tokens']==700
        assert continued['comparison']['single']['run_id']==new_id
        assert continued['comparison']['single']['policy_revision']==3
        assert continued['comparison']['single']['token_limit']==250_000
        assert continued['analysis_attempts'][-1]['run_id']==new_id
        await service.client.aclose()
    asyncio.run(run())
