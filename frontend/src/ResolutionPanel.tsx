import { useEffect, useRef, useState } from 'react'
import { ArrowClockwise, Check, CircleNotch, ShieldCheck, WarningCircle, X } from '@phosphor-icons/react'

type CaseResult = { id?:string; status?:string; message?:string; reply?:string; result?:unknown; detail?:string; [key:string]:unknown }
export type Resolution = {
  status:string
  runbook_id?:string
  runbook_title?:string
  operator_selected?:boolean
  automation?:string
  plan_id?:string
  version?:number
  plan_sha256?:string
  old_policy?:string
  new_policy?:string
  source_commit?:string
  source_hash?:string
  configuration_sha256?:string
  payload?:unknown
  original_assertions?:{kind?:string;requests?:{tool?:string;order_id?:number|string}[];[key:string]:unknown}
  validation_plan?:{
    health_observations_required?:number
    policy_readback_required?:string
    readonly_business_probes?:CaseResult[]
    database_mutation_guard?:string
    rollback_policy?:string
  }
  rollback?:{old_policy?:string;manual_required?:boolean;[key:string]:unknown}
  approval?:{decision?:string;plan_sha256?:string;approver?:string;reason?:string;approved_at?:string;at?:string}|null
  execution?:{state?:string;id?:string;idempotency_key?:string;[key:string]:unknown}|null
  verification?:{
    status?:string
    health_observations?:CaseResult[]
    policy_readback?:unknown
    business_probes?:CaseResult[]
    database_guard?:unknown
    completed_at?:string|null
    [key:string]:unknown
  }
  recovery?:{status?:string;receipt_id?:string;policy_readback?:unknown;detail?:string;[key:string]:unknown}
  rollback_receipt?:{id?:string;status?:string;plan_sha256?:string;operator_triggered?:boolean;idempotency_key?:string;started_at?:string;completed_at?:string;restored_policy?:string|null;configuration_sha256?:string;detail?:string;[key:string]:unknown}|null
  lesson_candidate?:{id?:string;status?:string;title?:string;source_incident_id?:string;plan_sha256?:string;evidence?:unknown;review?:{decision?:string;reason?:string;at?:string;actor?:string}|null}|null
  [key:string]:unknown
}
const statusText:Record<string,string>={
  prepared:'等待你审核预案',awaiting_approval:'等待你审核预案',awaitingapproval:'等待你审核预案',rejected:'预案已退回',authorized:'等待你执行',running:'正在执行',outcome_unknown:'执行结果待核对',
  applied:'等待业务验证',validating:'正在运行验证',verified:'已验证',verification_failed:'验证未通过',rollback_running:'正在恢复原路由',rolled_back:'已回退到原路由',manual_required:'需要你检查',
}
const verificationText:Record<string,string>={not_run:'尚未验证',validating:'验证中',passed:'通过',failed:'未通过',unknown:'结果待核对'}
const recoveryText:Record<string,string>={not_needed:'尚无业务恢复记录',running:'业务恢复核对中',recovered:'保护路由业务验证通过',not_recovered:'保护路由业务验证未通过',unknown:'保护路由业务验证待核对',manual_required:'保护路由业务恢复需要检查',obsolete_after_rollback:'保护路由验证已因回退失效'}
const rollbackText:Record<string,string>={running:'正在恢复原路由',rolled_back:'已回退到原路由',unknown:'回退结果待核对'}
const resultText:Record<string,string>={passed:'通过',failed:'未通过',unknown:'待核实',succeeded:'完成',not_run:'未运行'}
const pretty=(value:unknown)=>typeof value==='string'?value:JSON.stringify(value,null,2)
const digest=(value?:string)=>value?`${value.slice(0,12)}…${value.slice(-8)}`:'尚未生成'
const policyName=(value:unknown)=>value==='legacy'?'原路由':value==='guarded'?'保护路由':null
function originalTaskSummary(assertions?:Resolution['original_assertions']){
  if(!assertions)return ''
  if(assertions.kind==='clarify_missing_order')return '复查原任务：缺少订单号时，客服助手是否会先向用户确认。'
  const labels:Record<string,string>={track_shipment:'物流状态',get_order_status:'订单状态',check_refund_eligibility:'退款资格'}
  const groups=new Map<string,Set<string>>()
  for(const request of assertions.requests??[]){
    const label=labels[request.tool??'']
    if(!label)continue
    const ids=groups.get(label)??new Set<string>()
    if(request.order_id!==undefined)ids.add(String(request.order_id))
    groups.set(label,ids)
  }
  if(!groups.size)return '复查原任务要求的业务查询是否能正常完成。'
  const items=[...groups].map(([label,ids])=>`${label}${ids.size?`（订单 ${[...ids].join('、')}）`:''}`)
  return `复查原任务的${items.join('、')}。`
}
async function send(id:string,action:string,body:unknown,key?:string){
  const response=await fetch(`/api/v1/demo/tasks/${encodeURIComponent(id)}/resolution/${action}`,{method:'POST',headers:{'Content-Type':'application/json',...(key?{'Idempotency-Key':key}:{})},body:JSON.stringify(body)})
  const payload=await response.json().catch(()=>({}))
  if(!response.ok)throw new Error(payload.error?.message??'操作未完成，请检查任务状态后重试')
  return payload
}
function ResultRows({title,rows}:{title:string;rows?:CaseResult[]}){
  if(!rows?.length)return <p className="s9-resolution-empty">暂无执行回执。</p>
  const labels:Record<string,string>={'order-status':'查询订单 1001 状态','refund-eligibility':'仅查询订单 1002 的退款资格',order_truth:'核对样例订单状态',refund_guardrail:'检查退款资格，且不提交退款',complaint_multi_intent:'同时处理订单查询与退款资格',original_replay:'复查原任务的查询要求'}
  return <div className="s9-resolution-cases"><h4>{title}</h4>{rows.map((row,i)=><article key={row.id??i}><div><strong>{labels[row.id??'']??row.message??row.id??`检查 ${i+1}`}</strong><span className={`s9-resolution-result is-${row.status??'unknown'}`}>{resultText[row.status??'unknown']??'未知'}</span></div>{row.reply&&<p className="s9-resolution-business-result">客服助手：{row.reply}</p>}{row.detail&&<p>{row.detail}</p>}{row.result!==undefined&&<pre>{pretty(row.result)}</pre>}</article>)}</div>
}
export default function ResolutionPanel({taskId,resolution,targetPolicy,ready,sourceCurrent,busy,onRefresh,onBusyChange}:{taskId:string;resolution?:Resolution|null;targetPolicy?:string;ready:boolean;sourceCurrent:boolean;busy:boolean;onRefresh:()=>Promise<unknown>;onBusyChange:(busy:boolean)=>void}){
  const [reason,setReason]=useState('')
  const [lessonReason,setLessonReason]=useState('验证证据充分，同意留作同版本后续调查参考')
  const [localError,setLocalError]=useState('')
  const keys=useRef<{execute?:string;rollback?:string}>({})
  useEffect(()=>{setReason('');setLessonReason('验证证据充分，同意留作同版本后续调查参考');setLocalError('');keys.current={}},[taskId])
  const state=resolution?.status
  const writable=sourceCurrent
  const act=async(action:string,body:unknown,keyName?:'execute'|'rollback')=>{
    onBusyChange(true);setLocalError('')
    if(keyName)keys.current[keyName]??=crypto.randomUUID()
    try{
      await send(taskId,action,body,keyName?keys.current[keyName]:undefined)
      if(keyName)delete keys.current[keyName]
      await onRefresh()
    }catch(error){setLocalError((error as Error).message);await onRefresh().catch(()=>{})}
    finally{onBusyChange(false)}
  }
  const planHash=resolution?.plan_sha256
  const active=busy
  const title='启用客服路由保护'
  const oldPolicy=resolution?.old_policy??'legacy'
  const newPolicy=resolution?.new_policy??'guarded'
  const policyLabel=(policy:string)=>policy==='legacy'?'原路由':policy==='guarded'?'保护路由':'目标路由'
  const planReady=!!planHash&&['prepared','rejected','authorized','running','outcome_unknown','applied','validating','verified','verification_failed','rollback_running','rolled_back','manual_required'].includes(state??'')
  return <section className="demo-panel s9-resolution" aria-label="处理与验证">
    <div className="demo-section-title"><h2><ShieldCheck size={20}/>处理与验证</h2><span className={`demo-pill s9-resolution-status is-${state??'not-prepared'}`}>{state?statusText[state]??`状态：${state}`:targetPolicy==='guarded'?'当前预案不适用':targetPolicy==='legacy'?'尚未查看预案':'路由状态未确认'}</span></div>
    <p className="s9-resolution-lead">调查建议提供核查依据；认可建议不等于修改授权。以下处置需你单独选择固定预案并授权。</p>
    {!writable&&<div className="s9-resolution-stale" role="status"><WarningCircle size={18}/><span>历史版本记录，当前应用已更新。原处置和验证回执仍可查看，但不能继续执行处置操作。</span></div>}
    {!resolution&&targetPolicy==='guarded'?<div className="s9-resolution-not-applicable">此任务已使用保护路由；当前固定预案不适用。其他处理需人工制定方案。</div>:!resolution&&targetPolicy!=='legacy'?<div className="s9-resolution-not-applicable">当前路由尚未确认，暂不能选择这个处理预案。</div>:<div className="s9-resolution-runbook"><div><span className="s9-resolution-kicker">人工选择的处置预案</span><h3>{title}</h3></div><span className="s9-resolution-fixed">本地应用</span>
      <div className="s9-resolution-flow"><span>{policyLabel(oldPolicy)}</span><b>→</b><span>{policyLabel(newPolicy)}</span></div>
      <p>把客服助手切换为保护路由，并重启本地演示应用使其生效。验证未通过时，你可以手动恢复原路由。</p>
    </div>}
    {planReady&&<div className="s9-resolution-plan">
      {originalTaskSummary(resolution?.original_assertions)&&<p className="s9-resolution-original"><strong>原任务复查</strong>{originalTaskSummary(resolution?.original_assertions)}</p>}
      <details className="demo-evidence"><summary>查看技术记录</summary><div className="s9-resolution-plan-detail">
        <p><strong>预案标识与版本</strong><code>{resolution?.runbook_id??'未知'} · {resolution?.runbook_title??'未知'} · 第 {resolution?.version??'未知'} 版</code></p>
        <p><strong>变更摘要</strong><code title={planHash}>{planHash??'未知'}</code></p>
        <p><strong>实际变更意图</strong><pre>{pretty(resolution?.payload??'策略配置：S9_SUPPORT_POLICY = guarded')}</pre></p>
        <p><strong>绑定的应用版本</strong><code>{resolution?.source_commit??'未知'} · {resolution?.source_hash??'未知'}</code></p>
        <p><strong>配置摘要</strong><code>{resolution?.configuration_sha256??'待生成'}</code></p>
        <p><strong>验证要求</strong><span>连续健康观察 {resolution?.validation_plan?.health_observations_required??5} 次；读取策略值并确认 {resolution?.validation_plan?.policy_readback_required??newPolicy}；运行只读业务用例；确认业务数据库摘要与行数不变。</span>{resolution?.validation_plan?.readonly_business_probes?.length?<ul>{resolution.validation_plan.readonly_business_probes.map((probe,index)=><li key={probe.id??index}>{probe.message??probe.id??`只读用例 ${index+1}`}</li>)}</ul>:null}<small>数据库保护：{resolution?.validation_plan?.database_mutation_guard??'比对数据库摘要与行数，确认未改变'}</small></p>
        <p><strong>回退边界</strong><span>仅在人工明确触发后恢复为 {policyLabel(String(resolution?.rollback?.old_policy??resolution?.rollback?.policy??oldPolicy))}；不会自动回退。</span></p>
      </div></details>
    </div>}
    {resolution?.approval&&<div className="s9-resolution-approval"><Check size={17}/><div><strong>{resolution.approval.decision==='approved'?'你已授权实施此预案':'预案已退回'}</strong><p>{resolution.approval.reason}</p><small>{resolution.approval.approver??'操作人'} · {(resolution.approval.approved_at??resolution.approval.at)?new Date((resolution.approval.approved_at??resolution.approval.at)!).toLocaleString():'时间未知'}</small><details className="s9-resolution-record"><summary>查看审批技术记录</summary><small>审批绑定的方案摘要：{resolution.approval.plan_sha256??'未知'}</small></details></div></div>}
    {!resolution&&targetPolicy==='legacy'&&<div className="s9-resolution-actions"><button className="demo-primary" disabled={active||!ready||!writable} onClick={()=>act('prepare',{runbook_id:'support-policy-legacy-to-guarded'})}>{active?<CircleNotch className="spinning"/>:<ShieldCheck size={17}/>}选择并查看处理预案</button><small>{!writable?'历史版本记录，当前应用已更新。':ready?'查看后再决定是否授权实施。':'完成调查后，可选择处理预案'}</small></div>}
    {state==='prepared'&&<><label className="demo-review-label" htmlFor={`resolution-reason-${taskId}`}>处置授权说明</label><textarea disabled={!writable} id={`resolution-reason-${taskId}`} value={reason} onChange={event=>setReason(event.target.value)} placeholder="说明你为何授权，或要求退回的原因" maxLength={1000}/><div className="demo-review-actions"><button disabled={active||!writable||!reason.trim()||!planHash} onClick={()=>act('review',{plan_sha256:planHash,decision:'rejected',reason:reason.trim()})}><X size={16}/>退回预案</button><button className="demo-primary" disabled={active||!writable||!reason.trim()||!planHash} onClick={()=>act('review',{plan_sha256:planHash,decision:'approved',reason:reason.trim()})}><Check size={16}/>授权实施此预案</button></div><small className="s9-resolution-caution">授权只适用于当前预案；实施前还需要你再次确认。</small></>}
    {state==='rejected'&&<div className="s9-resolution-actions"><button className="demo-primary" disabled={active||!ready||!writable} onClick={()=>act('prepare',{runbook_id:'support-policy-legacy-to-guarded'})}>{active?<CircleNotch className="spinning"/>:<ArrowClockwise size={17}/>}重新查看处理预案</button><small>{!writable?'历史版本记录，当前应用已更新。':ready?'退回的预案不能执行；重新查看后需重新审核授权。':'完成调查后，可选择处理预案'}</small></div>}
    {state==='authorized'&&<div className="s9-resolution-actions"><button className="demo-primary" disabled={active||!writable} onClick={()=>act('execute',{plan_sha256:planHash},'execute')}>{active?<CircleNotch className="spinning"/>:<ArrowClockwise size={17}/>}实施已授权的路由调整</button><small>将重启本地应用；如结果待核对，请先查询状态。</small></div>}
    {state==='running'&&<p className="s9-resolution-state"><CircleNotch className="spinning"/>正在应用固定策略变更，页面会同步最新回执。</p>}
    {state==='outcome_unknown'&&<div className="s9-resolution-unknown"><WarningCircle size={20}/><div><strong>暂时无法确认执行结果</strong><p>请先查询本地目标与执行记录。系统不会把超时直接当成成功，也不会自动重复实施。</p><button disabled={active||!writable} onClick={()=>act('reconcile',{})}>{active?<CircleNotch className="spinning"/>:<ArrowClockwise size={16}/>}查询实际状态</button></div></div>}
    {state==='applied'&&<div className="s9-resolution-actions"><button className="demo-primary" disabled={active||!writable} onClick={()=>act('verify',{plan_sha256:planHash})}>{active?<CircleNotch className="spinning"/>:<Check size={17}/>}检查路由和业务查询</button><small>会检查本地应用状态、原任务查询结果及数据未被修改。</small></div>}
    {state==='validating'&&<p className="s9-resolution-state"><CircleNotch className="spinning"/>正在执行健康观察与只读业务验证…</p>}
    {state==='verification_failed'&&<div className="s9-resolution-unknown"><WarningCircle size={20}/><div><strong>业务验证未通过</strong><p>请检查失败用例和回退边界。恢复旧策略需要你明确触发。</p></div></div>}
    {state==='verified'&&<p className="s9-resolution-success"><Check size={18}/>后端验证回执报告通过。请检查下方的业务用例与数据库保护结果。</p>}
    {state==='manual_required'&&<div className="s9-resolution-unknown"><WarningCircle size={20}/><div><strong>需要人工检查</strong><p>系统无法安全确认当前业务状态，请根据回执核查本地配置后再决定是否回退。</p></div></div>}
    {resolution?.execution&&<details className="s9-resolution-record"><summary>查看执行技术记录</summary><div className="s9-resolution-receipt"><span>状态：{resolution.execution.state??'未知'} · 记录号：{resolution.execution.id??'未知'}</span><span>配置摘要：{resolution.configuration_sha256??'未知'}</span></div></details>}
    {resolution?.verification&&<div className="s9-resolution-results"><div className="s9-resolution-subtitle"><h3>业务验证回执</h3><span className={`s9-resolution-result is-${resolution.verification.status??'unknown'}`}>{verificationText[resolution.verification.status??'unknown']??'状态未知'}</span></div><small>{resolution.verification.completed_at?`完成于 ${new Date(resolution.verification.completed_at).toLocaleString()}`:'尚无完成时间'}</small><ResultRows title="健康观察" rows={resolution.verification.health_observations}/><div className="s9-resolution-readbacks"><article><h4>策略读回</h4><p>{policyName((resolution.verification.policy_readback as Record<string,unknown>|null)?.policy)??'尚未确认'}</p></article><article><h4>业务数据库保护</h4><p>{(resolution.verification.database_guard as Record<string,unknown>|null)?.status==='passed'?'通过，数据未改变':(resolution.verification.database_guard as Record<string,unknown>|null)?.status==='failed'?'未通过，数据状态发生变化':'尚无回执'}</p><details className="s9-resolution-record"><summary>查看数据核对记录</summary><pre>{pretty(resolution.verification.database_guard??'尚无回执')}</pre></details></article></div><ResultRows title="只读业务用例" rows={resolution.verification.business_probes}/><details className="s9-resolution-record"><summary>查看验证技术记录</summary><small>验证记录号：{String(resolution.verification.id??'未知')} · 方案摘要：{planHash??'未知'}</small></details></div>}
    {resolution?.recovery&&<div className="s9-resolution-recovery"><div className="s9-resolution-subtitle"><h3>保护路由业务恢复证据</h3><span className={`s9-resolution-result is-${resolution.recovery.status??'unknown'}`}>{recoveryText[resolution.recovery.status??'unknown']??'状态未知'}</span></div><p>{resolution.recovery.status==='recovered'?'业务查询已按保护路由通过验证；这不是回退记录。':resolution.recovery.status==='obsolete_after_rollback'?'此保护路由业务恢复记录已因后续手动回退失效。':resolution.recovery.detail?'业务恢复核对有记录，请展开查看。':'暂无业务恢复记录。'}</p><details className="s9-resolution-record"><summary>查看业务恢复技术记录</summary><small>记录号：{resolution.recovery.receipt_id??'未知'}</small><pre>{pretty(resolution.recovery.policy_readback??'无策略读回记录')}</pre>{resolution.recovery.detail&&<pre>{resolution.recovery.detail}</pre>}</details></div>}
    {resolution?.rollback_receipt&&<div className="s9-resolution-recovery s9-resolution-rollback-receipt"><div className="s9-resolution-subtitle"><h3>人工回退回执</h3><span className={`s9-resolution-result is-${resolution.rollback_receipt.status??'unknown'}`}>{rollbackText[resolution.rollback_receipt.status??'unknown']??'回退状态未知'}</span></div><p>{resolution.rollback_receipt.status==='rolled_back'&&resolution.rollback_receipt.restored_policy==='legacy'?'回执确认本地应用已恢复到原路由。':resolution.rollback_receipt.status==='running'?'正在检查原路由是否恢复。':resolution.rollback_receipt.status==='unknown'?'目前无法确认原路由是否恢复，请检查本地应用状态。':'尚无明确的回退完成回执。'}</p><details className="s9-resolution-record"><summary>查看回退技术记录</summary><small>记录号：{resolution.rollback_receipt.id??'未知'} · {resolution.rollback_receipt.operator_triggered?'由人工触发':'触发方式未知'}</small><pre>{pretty(resolution.rollback_receipt)}</pre></details></div>}
    {['verified','verification_failed','manual_required'].includes(state??'')&&resolution?.rollback?.manual_required!==false&&<div className="demo-panel s9-resolution-rollback"><div><strong>人工回退</strong><p>手动恢复到原路由，并重启本地应用。此操作不会自动发生。</p></div><button disabled={active||!writable||!planHash||['rollback_running','rolled_back'].includes(state??'')} onClick={()=>act('rollback',{plan_sha256:planHash},'rollback')}>{active?<CircleNotch className="spinning"/>:<ArrowClockwise size={16}/>}回退到原路由</button></div>}
    {resolution?.recovery?.status==='running'&&<p className="s9-resolution-state"><CircleNotch className="spinning"/>正在核对回退结果…</p>}
    {resolution?.lesson_candidate&&<div className="s9-resolution-lesson">
      <div className="s9-resolution-subtitle"><h3>经验候选</h3><span className="s9-resolution-result">{resolution.lesson_candidate.review?.decision==='approved'?'已确认保存':resolution.lesson_candidate.review?.decision==='rejected'?'已拒绝保存':'待人工审阅'}</span></div>
      <details className="demo-evidence"><summary>{resolution.lesson_candidate.title??'客服助手支持路由策略回归'} · 查看来源与依据</summary><p>来源事故：{resolution.lesson_candidate.source_incident_id??'未知'} · 候选记录：{resolution.lesson_candidate.id??'未知'}</p><pre>{pretty(resolution.lesson_candidate.evidence)}</pre></details>
      <p className="s9-resolution-lesson-copy">经你确认后，这次经验只供同应用、同版本的后续调查参考；不会自动执行，也不会发布到 EvoMapHub。</p>
      {resolution.status==='verified'&&!resolution.lesson_candidate.review&&<><label className="demo-review-label" htmlFor={`lesson-reason-${taskId}`}>审核说明</label><textarea disabled={!writable} id={`lesson-reason-${taskId}`} value={lessonReason} onChange={event=>setLessonReason(event.target.value)} placeholder="补充保存或拒绝的理由" maxLength={1000}/><div className="demo-review-actions"><button disabled={active||!writable||!planHash||!lessonReason.trim()} onClick={()=>act('lesson-review',{plan_sha256:planHash,decision:'rejected',reason:lessonReason.trim()})}><X size={16}/>不保存这次经验</button><button className="demo-primary" disabled={active||!writable||!planHash||!lessonReason.trim()} onClick={()=>act('lesson-review',{plan_sha256:planHash,decision:'approved',reason:lessonReason.trim()})}><Check size={16}/>保存这次经验</button></div></>}
      {resolution.lesson_candidate.review&&<p className="s9-resolution-lesson-review">{resolution.lesson_candidate.review.actor??'操作人'} · {resolution.lesson_candidate.review.at?new Date(resolution.lesson_candidate.review.at).toLocaleString():'时间未知'}{resolution.lesson_candidate.review.reason?` · ${resolution.lesson_candidate.review.reason}`:''}</p>}
    </div>}
    {localError&&<div className="demo-error s9-resolution-error" role="alert"><WarningCircle size={18}/><span>{localError}</span><button disabled={active} onClick={()=>onRefresh()}>刷新状态</button></div>}
  </section>
}
