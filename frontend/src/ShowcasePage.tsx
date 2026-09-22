import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowClockwise, LinkSimple, ShieldCheck, X } from '@phosphor-icons/react'
import './showcase.css'
import './showcase-product.css'

type ArmName = 'swarm' | 'baseline'
type Pair = { pair_id:string; status:string; spec_hash:string; swarm_run_id:string; baseline_run_id:string; immutable_spec:any; comparison_integrity:any; links:{swarm:string;baseline:string}; created_at:string; started_at:string|null; ended_at:string|null }
type Event = { event_id:string; sequence:number; scope:string; pair_id:string; run_id:string; arm:ArmName; event_type:string; occurred_at:string; producer:string; stage:string|null; config_revision:string; payload:any }
type Projection = { pair_id:string; run_id:string; arm:ArmName; spec_hash:string; pair_status:string; run:any; config:any; stages:any[]; agents:any[]; events:Event[]; as_of_sequence:number; usage:any; dependencies:any; verification:any; rca:any; comparison_integrity:any; started_at:string|null; log_page?:{has_more:boolean;next_after:number|null;range_start:number|null;range_end:number|null;watermark:number} }
const api=async<T,>(path:string,init?:RequestInit):Promise<T>=>{const r=await fetch(path,{...init,headers:{'Content-Type':'application/json',...(init?.headers||{})}});const b=await r.json();if(!r.ok)throw new Error(b?.error?.message||JSON.stringify(b?.detail)||`HTTP ${r.status}`);return b}
const post=(path:string,body:any={},key?:string)=>api<any>(path,{method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:undefined})
const labels:Record<string,string>={ready:'已准备',preparing:'准备中',running:'运行中',completed:'实验结束',resolved:'业务恢复',failed:'失败',interrupted:'已中断',reset:'已重置',setup_failed:'准备失败',injected:'等待检测',diagnosing:'诊断中',repairing:'修复中',verifying:'验收中',pending:'待测',waiting:'等待审批',cancelled:'已取消',passed:'通过',combined:'合并决策',skipped:'未执行',idle:'待命',working:'工作中',starting:'启动中',paused:'已暂停',offline:'离线',resuming:'恢复中',stopped:'本轮已结束'}
const label=(v:string)=>labels[v]||v||'未知'
const eventNames:Record<string,string>={
 'arm.prepared':'已做好运行准备','arm.started':'本轮已开始','chaos.injected':'异常已加入',
 'request.completed':'客服已回复','request.progress':'客服正在处理','model.queued':'等待推理服务','model.admitted':'开始准备分析','model.provider_started':'分析已开始','model.started':'分析已开始','model.completed':'分析完成',
 'agent.joined':'成员已加入','agent.process_started':'成员已就位','agent.status':'成员状态更新','agent.heartbeat':'成员状态更新','agent.request_rejected':'请求已拦截',
 'observation.liveness':'发现重复调用','observation.probe':'发现业务偏差','probe.completed':'业务检查完成','incident.opened':'开始处理异常','incident.updated':'补充异常线索','incident.closed':'事件已结案',
 'task.claimed':'已认领任务','task.completed':'任务已完成','dialog.received':'收到协作意见','telemetry.span_received':'调用记录已保存',
 'plan.proposed':'提交处置方案','execution.granted':'方案获准执行','action.applied':'修复已执行','verify.started':'开始独立验收','verification.completed':'验收完成','authority.revoked':'本轮处理权限已收回','memory.recorded':'处置经验已保存',
 'stage.started':'阶段已开始','stage.completed':'阶段已完成','failure.detected':'发现异常','repair.started':'开始修复','repair.completed':'修复完成','run.started':'本轮已开始','run.completed':'本轮已完成'}
const eventLabel=(v:string)=>eventNames[v]||'处理记录'
const modelLabel=(v:string)=>({available:'可用',degraded:'连接不稳定',unconfigured:'未配置',configured:'已配置',unknown:'待确认'}[v]||'待确认')
const scenarioLabel=(v:string)=>({composite:'多项异常',prompt:'回答偏离预期',cost:'处理成本升高',loop:'重复调用未结束'}[v]||'业务场景')
const producerLabel=(v:string)=>({system:'系统',monitor:'监护服务',verification:'验收服务',verifier:'验收员',model:'推理服务',sentry:'监测员',diagnoser:'诊断员','fixer-a':'修复员 A','fixer-b':'修复员 B',single:'独立助手',cost:'成本专家',operator:'操作员',victim:'小智客服',liveness:'活性监测',probe:'业务检查',collector:'调用记录',authority:'权限管理',memory:'经验库'}[v]||'协作服务')
const humanText=(value:any):string=>{if(value==null)return '暂无记录';if(typeof value!=='string')return '已记录';let text=value.split('只输出 JSON')[0].trim();const terms:Record<string,string>={quality_mismatch:'回答偏差',tool_stalled:'重复调用',cost_high:'成本升高',semantic_policy:'退货规则',no_stalled_requests:'调用停止情况',healthy:'正常规则',degraded:'异常规则',prompt_version:'回答规则',retry_limit:'重试上限',retry_on_terminal:'结束后继续重试'};for(const [key,label]of Object.entries(terms))text=text.replaceAll(key,label);return text.replace(/system\.[\w.]+/g,'异常版本').replace(/\b(?:run|event|task|plan|model|usage|grant|verify)_[a-z0-9_-]+\b/gi,'相关记录')}
const eventSummary=(e:Event)=>{
 const p=e.payload||{};
 if(e.event_type==='request.completed')return humanText(p.answer||'客服已完成本次回复');
 if(e.event_type==='verification.completed')return p.passed===true?'业务检查已通过':p.passed===false?'业务检查未通过':'验收结果已记录';
 if(e.event_type==='task.claimed')return `${producerLabel(e.producer)}开始处理${({diagnose:'原因分析',repair:'服务修复',verify:'结果验收'} as Record<string,string>)[p.kind]||'当前任务'}`;
 if(e.event_type==='dialog.received')return `${producerLabel(e.producer)}收到同伴的处理意见`;
 if(e.event_type==='agent.request_rejected')return '当前请求不具备有效处理权限，已被拦截';
 if(['plan.proposed','task.completed'].includes(e.event_type))return humanText(p.summary||eventLabel(e.event_type));
 if(e.event_type==='agent.status')return label(p.status);
 if(e.event_type==='observation.liveness')return `同一调用重复 ${p.steps??'多'} 次，仍未完成`;
 return eventLabel(e.event_type);
}
const time=(v:string|null)=>v?new Date(v).toLocaleTimeString('zh-CN',{hour12:false}):'—'
const relative=(v:string,start:string|null)=>start?`${((Date.parse(v)-Date.parse(start))/1000).toFixed(1)}s`:time(v)
const pretty=(v:any)=>JSON.stringify(v??{status:'待测'},null,2)
const failure=(e:Event)=>/failed|rejected|cancelled|dropped|interrupted|reset/.test(e.event_type)||e.payload?.usage_unknown

function usePairArm(pair:Pair|null,arm:ArmName){
 const [data,setData]=useState<Projection|null>(null),[status,setStatus]=useState('connecting'),[error,setError]=useState('')
 const reload=useRef<()=>void>(()=>{}),[receivedAt,setReceivedAt]=useState<number|null>(null)
 const id=pair?.pair_id,runId=pair?.[arm==='swarm'?'swarm_run_id':'baseline_run_id']
 useEffect(()=>{
  setData(null);setReceivedAt(null);setError('');if(!id||!runId){setStatus('empty');return}
  let dead=false,busy=false,again=false,source:EventSource|null=null,timer:number|undefined,last=0
  const abort=new AbortController()
  const load=async()=>{if(dead)return;if(busy){again=true;return}busy=true
   try{const snap=await api<Projection>(`/api/pairs/${encodeURIComponent(id)}/arms/${arm}`,{signal:abort.signal})
    if(dead)return
    if(snap.pair_id!==id||snap.arm!==arm||snap.run_id!==runId)throw new Error('快照身份不匹配，已拒绝显示')
    if(snap.as_of_sequence>=last){last=snap.as_of_sequence;setReceivedAt(Date.now());setData({...snap,events:snap.events.filter(e=>e.scope==='run'&&e.pair_id===id&&e.run_id===runId&&e.arm===arm)});setError('');if(source?.readyState===EventSource.OPEN)setStatus('live')}
    if(!source){source=new EventSource(`/api/pairs/${encodeURIComponent(id)}/events?arm=${arm}&after=${last}`)
     source.onopen=()=>{if(!dead){setStatus('live');schedule()}}
     source.onerror=()=>{if(!dead)setStatus('reconnecting')}
     source.addEventListener('update',ev=>{try{const e=JSON.parse((ev as MessageEvent).data);if(e.pair_id===id&&e.run_id===runId&&e.arm===arm)schedule()}catch{setError('事件格式错误')}})
    }
   }catch(e:any){if(!dead&&e.name!=='AbortError'){setError(e.message);setStatus('error')}}finally{busy=false;if(again&&!dead){again=false;schedule()}}
  }
  const schedule=()=>{if(!dead&&timer===undefined)timer=window.setTimeout(()=>{timer=undefined;void load()},120)}
  reload.current=()=>void load();void load()
  // Snapshot reconciliation also makes a silent event transport outage visible
  // without turning a read-only page into an experiment controller.
  const poll=window.setInterval(()=>void load(),4000)
  return()=>{dead=true;abort.abort();source?.close();window.clearTimeout(timer);window.clearInterval(poll);reload.current=()=>{}}
 },[id,runId,arm])
 return {data,status,error,receivedAt,refresh:()=>reload.current()}
}

export function ShowcasePage(){
 const arm:ArmName=location.pathname.endsWith('/baseline')?'baseline':'swarm'
 const [pairId,setPairId]=useState(()=>new URLSearchParams(location.search).get('pair_id'))
 const [pair,setPair]=useState<Pair|null>(null),[pairs,setPairs]=useState<Pair[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[drawer,setDrawer]=useState<{title:string;value:any}|null>(null)
 const hook=usePairArm(pair,arm),data=hook.data
 const [clock,setClock]=useState(Date.now())
 useEffect(()=>{const timer=window.setInterval(()=>setClock(Date.now()),1000);return()=>window.clearInterval(timer)},[])
 const age=hook.receivedAt===null?null:Math.max(0,Math.floor((clock-hook.receivedAt)/1000))
 const active=!!data?.run&&['injected','diagnosing','repairing','verifying'].includes(data.run.status)
 const modelStatus=data?.dependencies?.model?.status
 const returnedModels=[...new Set((data?.events||[]).filter(e=>e.event_type==='model.completed'&&e.payload.returned_model).map(e=>e.payload.returned_model))]

 useEffect(()=>{const abort=new AbortController();let live=true;setLoading(true);setError('');setPair(null)
  void(async()=>{try{const history=await api<{items:Pair[]}>('/api/pairs',{signal:abort.signal});if(!live)return;setPairs(history.items)
   let id=pairId;if(!id){const a=await api<{active_pair:Pair|null}>('/api/pairs/active',{signal:abort.signal});id=a.active_pair?.pair_id||null;if(id){historyReplace(id);setPairId(id);return}}
   if(id){const p=await api<Pair>(`/api/pairs/${encodeURIComponent(id)}`,{signal:abort.signal});if(live)setPair(p)}
  }catch(e:any){if(live&&e.name!=='AbortError')setError(e.message)}finally{if(live)setLoading(false)}})()
  return()=>{live=false;abort.abort()}
 },[pairId])
 const choose=(id:string)=>{if(!id)return;historyReplace(id);setPairId(id);setDrawer(null)}
 function historyReplace(id:string){history.replaceState({},'',`${location.pathname}?pair_id=${encodeURIComponent(id)}`)}
 const show=(title:string,value:any)=>setDrawer({title,value})
 const friendlyError=(message:string)=>message.includes('身份不匹配')?'这份记录与当前页面不一致，已停止展示。':message.includes('HTTP')?'暂时无法读取记录，请稍后重试。':message
 const status=data?.run?.status||data?.pair_status||pair?.status||'pending'
 const resultText=data?.run?.elapsed_s!=null?`${data.run.elapsed_s}s`:'尚未完成计时'
 return <div className="sc-page" data-pair-id={data?.pair_id||pair?.pair_id||""} data-run-id={data?.run_id||""}>
  <header className="sc-header"><a className="sc-brand" href="/">S9<span>公安九课<span>对照实验 · {arm==='swarm'?'协作团队':'独立助手'}</span></span></a><div className="sc-header-tools"><span>只读 · {active?'正在运行':data?.run?'历史记录':'尚未开始'}</span><span className={hook.status==='live'?'sc-good':'sc-wait'}>{({live:'● 实时连接',reconnecting:'↻ 连接中断 · 重连中',connecting:'◌ 正在连接',error:'! 读取失败',empty:'暂无记录'} as any)[hook.status]}</span><span className={age!==null&&age>10?'sc-bad':'sc-muted'}>{age===null?'等待更新':`更新于 ${age}s 前`}</span><button onClick={hook.refresh} aria-label="刷新对照记录"><ArrowClockwise/></button><a href={`/showcase/${arm==='swarm'?'baseline':'swarm'}${pair?`?pair_id=${pair.pair_id}`:''}`}>查看{arm==='swarm'?'独立助手':'协作团队'} ↗</a></div></header>
  {(error||hook.error)&&<div role="alert" className="sc-error">{friendlyError(error||hook.error)} <button onClick={hook.refresh}>重试读取</button></div>}
  {loading?<div className="sc-empty"><strong>正在读取对照记录…</strong><span>正在整理本次处理结果</span></div>:!pair?<div className="sc-empty"><h1>{error?'暂时无法打开这份记录':'还没有对照记录'}</h1><p>在工作空间发起一次对照实验，这里就能看到处理过程与结果。</p><a href="/">返回工作空间 →</a></div>:<>
   <section className="sc-identity"><div><span className="sc-eyebrow">对照实验 · {arm==='swarm'?'协作团队':'独立助手'}</span><h1>{arm==='swarm'?'协作团队':'独立助手'} <b className={status==='resolved'?'sc-good':/failed|reset/.test(status)?'sc-bad':'sc-wait'}>{label(status)}</b></h1><p className="sc-lede">{arm==='swarm'?'多个专业角色共同处理同一业务现场。':'同一业务现场由独立助手单独处理，用于客观对照。'}</p></div><div className="sc-ids"><select aria-label="选择历史对照记录" value={pair.pair_id} onChange={e=>choose(e.target.value)}>{pairs.map((p,i)=><option value={p.pair_id} key={p.pair_id}>记录 {pairs.length-i} · {label(p.pair_id===pair.pair_id?(data?.pair_status||p.status):p.status)}</option>)}</select><button onClick={()=>show('对照记录',{pair_id:pair.pair_id,run_id:data?.run_id||pair[arm==='swarm'?'swarm_run_id':'baseline_run_id'],spec_hash:pair.spec_hash,pair})}>记录详情</button></div><div className="sc-outcome"><strong>{resultText}</strong><small>场景：{scenarioLabel(pair.immutable_spec.scenario)} · 每侧预算 {pair.immutable_spec.budget_policy.total_tokens_per_arm} Token</small><small>{data?.comparison_integrity?.eligible===false?'⚠ 本次记录受过干预，暂不具备对照资格':'同一场景 · 独立状态与预算'}</small></div></section>
   <section className="sc-stages" aria-label="处理进度">{(data?.stages||[]).map((s:any,i:number)=><button key={s.id} className={`sc-stage sc-stage-${s.status}`} data-stage={s.id} data-status={s.status} onClick={()=>show(`${s.label}记录`,{...s,events:data?.events.filter(e=>s.source_event_ids.includes(e.event_id))})}><span className="sc-eyebrow">0{i+1} / {label(s.status)}</span><strong>{s.label}</strong><small>{time(s.started_at)}{s.ended_at&&s.ended_at!==s.started_at?` → ${time(s.ended_at)}`:''}</small><span className="sc-stage-detail">查看阶段记录</span></button>)}</section>
   <section className="sc-center">{arm==='swarm'?<SwarmOffice data={data} show={show}/>:<BaselineChain data={data} show={show}/>}<div className="sc-business"><span className="sc-eyebrow">业务现场 · 小智客服</span><strong>业务进展</strong><p>最近的客户提问与回复</p>{data?.events.filter(e=>e.event_type==='request.completed').slice(-2).map(e=><button key={e.event_id} onClick={()=>show('客服请求',e)}><small>{humanText(e.payload.question||'本轮业务请求')}</small><b>{e.payload.answer||e.payload.summary||'已收到业务回应'}</b><small>{time(e.occurred_at)}</small></button>)}{!data?.events.some(e=>e.event_type==='request.completed')&&<p className="sc-muted">等待本轮客服回复。</p>}<small>外部服务状态：{modelLabel(modelStatus)}{!active?' · 当前未发起新请求':''}</small><button className="sc-model-info" onClick={()=>show('推理服务',{requested_model:pair.immutable_spec.model,observed_models:returnedModels,dependency:data?.dependencies||{},meaning:'服务可用表示本侧最近一次真实请求成功；待确认不表示服务已验证。'})}>查看服务状态与来源</button></div></section>
   <Logbook events={data?.events||[]} startedAt={data?.started_at||null} cursor={data?.as_of_sequence||0} pairId={pair.pair_id} arm={arm} page={data?.log_page}/>
   <footer className="sc-evidence">{[['运行配置',data?.config],['资源与排队',data?.usage],['验收结果',data?.verification],['事件复盘',data?.rca],['失败与拒绝',data?.events.filter(failure)]].map(([title,value])=><button key={String(title)} onClick={()=>show(String(title),value)}>{String(title)} <span>查看 ↗</span></button>)}</footer>
  </>}
  {drawer&&<EvidenceDrawer {...drawer} onClose={()=>setDrawer(null)}/>}
 </div>
}

function SwarmOffice({data,show}:{data:Projection|null;show:(t:string,v:any)=>void}){
 const placed:[number,number][]=[]
 const [missingBackground,setMissingBackground]=useState(false),[missingSprites,setMissingSprites]=useState<Record<string,boolean>>({})
 const positions:Record<string,[number,number]>={monitor:[14,45],analysis:[32,40],meeting:[49,60],execution:[73,44],verification:[69,68],archive:[84,63],idle:[27,74]}
 return <div className="sc-office" aria-label="协作团队工作现场"><div className={'sc-office-fallback '+(missingBackground?'':'asset-fallback-hidden')} aria-label="办公室布局"><span>监测 · 分析</span><span>会诊桌</span><span>执行与验收机房</span></div><img src="/vendor/star-office/office_bg.webp" alt="协作团队工作现场" onLoad={()=>setMissingBackground(false)} onError={()=>setMissingBackground(true)}/><div className="sc-zone sc-zone-monitor">监测 · 分析</div><div className="sc-zone sc-zone-meeting">会诊桌</div><div className="sc-zone sc-zone-execution">执行与验收机房</div>{(data?.agents||[]).map((a:any,i:number)=>{let [x,y]=positions[a.position]||positions.idle;if(a.position==='idle'){x=12+i*18;y=65}else if(placed.some(([px,py])=>Math.abs(px-x)<17&&Math.abs(py-y)<32)){x=12+i*18;y=64}placed.push([x,y]);const src=`/vendor/star-office/guest_anim_${(i%4)+1}.webp`;const missing=missingSprites[a.id]===true;return <button key={a.id} data-agent-id={a.id} data-source-event={a.source_event_id||''} data-position-event={a.position_event_id||''} className="sc-actor" style={{left:`${x}%`,top:`${y}%`}} onClick={()=>show(a.name+'的处理记录',{agent:a,events:data?.events.filter(e=>e.event_id===a.source_event_id||e.event_id===a.position_event_id)})}><span className={`sc-sprite sc-sprite-${i%4+1}`}><img className="sc-sprite-preload" src={src} alt="" onLoad={()=>setMissingSprites(v=>({...v,[a.id]:false}))} onError={()=>setMissingSprites(v=>({...v,[a.id]:true}))}/><span className={'sc-sprite-fallback '+(missing?'asset-fallback-visible':'asset-fallback-hidden')}>{(a.name||a.id).slice(0,1)}</span></span><strong>{a.name}</strong><small>{label(a.status)} · {time(a.heartbeat_at)}</small></button>})}<div className="sc-room-note">点击成员，查看处理记录</div></div>
}
 function BaselineChain({data,show}:{data:Projection|null;show:(t:string,v:any)=>void}){const a=data?.agents.find(x=>x.id==='single');return <div className="sc-baseline"><div className="sc-solo"><span className="sc-sprite sc-sprite-3"/><strong>独立助手</strong><span>{label(a?.status||'pending')}</span><button onClick={()=>show('独立助手',a)}>查看处理记录 ↗</button></div><div className="sc-chain"><h2>同一份业务事实，由一位助手独立处理</h2><div><span>读取业务请求</span> → <span>诊断与计划<br/><small>一次独立决策</small></span> → <span>受控执行</span></div><p>↓ 独立验收服务</p><small>两侧使用同一场景、独立状态与预算，结果可直接对照。</small></div></div>}
function Logbook({events,startedAt,cursor,pairId,arm,page}:{events:Event[];startedAt:string|null;cursor:number;pairId:string;arm:ArmName;page?:Projection['log_page']}){
 const box=useRef<HTMLDivElement>(null),follow=useRef(true),previous=useRef(0)
 const [unread,setUnread]=useState(0),[filter,setFilter]=useState('all'),[paused,setPaused]=useState(false)
 useLayoutEffect(()=>{const added=Math.max(0,events.length-previous.current);previous.current=events.length;if(follow.current&&!paused){if(box.current)box.current.scrollTop=box.current.scrollHeight;setUnread(0)}else setUnread(x=>x+added)},[events.length,paused])
 const jump=()=>{follow.current=true;setPaused(false);setUnread(0);if(box.current)box.current.scrollTop=box.current.scrollHeight}
 return <section className="sc-log"><div className="sc-log-head"><strong>活动记录 <small>{events.length} 条记录{page?.has_more?' · 还有更早记录':''}</small></strong><div><select aria-label="活动记录筛选" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">全部活动</option><option value="failure">失败 / 拒绝 / 取消</option><option value="model">服务请求</option></select><button onClick={()=>setPaused(!paused)}>{paused?'继续跟随':'暂停自动滚动'}</button>{unread>0&&<button onClick={jump}>{unread} 条新活动 ↓</button>}<a className="sc-logbook-link" href={`/api/pairs/${encodeURIComponent(pairId)}/logbook?arm=${arm}&after=0&limit=200`} target="_blank" rel="noreferrer">查看完整记录 ↗</a><a className="sc-logbook-link" href={`/api/pairs/${encodeURIComponent(pairId)}/export.zip`}>导出证据 ↓</a></div></div><div className="sc-log-scroll" ref={box} onScroll={()=>{if(box.current)follow.current=box.current.scrollHeight-box.current.clientHeight-box.current.scrollTop<24}}>{events.filter(e=>filter==='all'||filter==='failure'&&failure(e)||filter==='model'&&e.event_type.startsWith('model.')).map(e=><details key={e.event_id} className={failure(e)?'sc-log-failure':''}><summary><time>{relative(e.occurred_at,startedAt)}</time><span>{producerLabel(e.producer)}</span><b>{eventLabel(e.event_type)}</b><span>{eventSummary(e)}</span></summary><div className="sc-log-detail"><span>原始记录</span><pre>{pretty(e)}</pre></div></details>)}{!events.length&&<p className="sc-muted">本轮尚未开始，暂无处理记录。</p>}</div></section>
}
const checkLabel=(v:string)=>({heldout_semantic_policy:'退货规则',heldout_outside_return_window:'超期退货',unaffected_product_fact:'商品信息',semantic_policy:'退货规则',cost_budget:'调用预算',no_stalled_requests:'调用正常结束',config_binding:'被测配置仍生效'}[v]||'业务检查')
function RecordSummary({value}:{value:any}){if(value==null)return <p>暂无相关记录。</p>;const a=value.agent;const events=Array.isArray(value)?value:value.events;const checks=value.checks||value.verification?.checks;return <div className="sc-record-summary">{a&&<p>{a.name} · {label(a.status)} · {time(a.heartbeat_at)}</p>}{value.event_type&&<><h3>{eventLabel(value.event_type)}</h3><p>{eventSummary(value)}</p></>}{value.summary&&<p>{humanText(value.summary)}</p>}{value.status&&<p>当前状态：{label(value.status)}</p>}{value.passed!==undefined&&<p>验收结果：{value.passed===true?'已通过':value.passed===false?'未通过':'待测'}</p>}{Array.isArray(checks)&&checks.map((c:any,i:number)=><p key={i}>{c.passed===true?'✓ 已通过':c.passed===false?'! 未通过':'待测'} · {checkLabel(c.name)}</p>)}{Array.isArray(events)&&events.filter((e:any)=>e.event_type).slice(-8).map((e:Event)=><article key={e.event_id}><strong>{eventLabel(e.event_type)}</strong><time>{time(e.occurred_at)}</time><p>{eventSummary(e)}</p></article>)}{!a&&!value.event_type&&!value.summary&&!value.status&&!checks&&!Array.isArray(events)&&<p>详细记录已保存，可在下方查看。</p>}</div>}
function EvidenceDrawer({title,value,onClose}:{title:string;value:any;onClose:()=>void}){const box=useRef<HTMLElement>(null);useEffect(()=>{const before=document.activeElement as HTMLElement;box.current?.querySelector('button')?.focus();const fn=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){const items=Array.from(box.current?.querySelectorAll<HTMLElement>('button,[tabindex="0"]')||[]);const index=items.indexOf(document.activeElement as HTMLElement);e.preventDefault();items[(index+(e.shiftKey?-1:1)+items.length)%items.length]?.focus()}};document.addEventListener('keydown',fn);return()=>{document.removeEventListener('keydown',fn);before?.focus()}},[]);return <div className="sc-backdrop" onMouseDown={e=>e.target===e.currentTarget&&onClose()}><aside ref={box} className="sc-drawer" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button onClick={onClose} aria-label="关闭证据"><X/></button></header><RecordSummary value={value}/><details className="sc-raw"><summary>查看原始记录</summary><pre tabIndex={0}>{pretty(value)}</pre></details></aside></div>}
export function PairConsole(){const [active,setActive]=useState<Pair|null>(null),[history,setHistory]=useState<Pair[]>([]),[scenario,setScenario]=useState('composite'),[busy,setBusy]=useState<string|null>(null),[err,setErr]=useState(''),[result,setResult]=useState('');const load=useCallback((clearError=true)=>Promise.all([api<{active_pair:Pair|null}>('/api/pairs/active'),api<{items:Pair[]}>('/api/pairs')]).then(([a,l])=>{setActive(a.active_pair);setHistory(l.items||[]);if(clearError)setErr('')}).catch(e=>setErr(e.message)),[]);useEffect(()=>{void load();const timer=window.setInterval(()=>void load(false),4000);return()=>window.clearInterval(timer)},[load]);const run=async(key:string,fn:()=>Promise<any>,success:string)=>{setBusy(key);setErr('');setResult('');try{await fn();setResult(success);await load(true)}catch(e:any){setErr(e.message||'操作未完成，请稍后重试。')}finally{setBusy(null)}};const create=()=>run('create',()=>post('/api/pairs',{scenario,seed:42},`console-${Date.now()}`),'已建立对照记录 · 自动处理');const start=()=>active&&run('start',()=>post(`/api/pairs/${active.pair_id}/start`,{expected_spec_hash:active.spec_hash}),'已开始对照记录 · 两侧独立运行');const reset=(path:string,target:string)=>active&&run(target,()=>post(path,{}),`已重置${target}`);const visibleError=err.includes('HTTP')?'暂时无法更新对照记录，请稍后重试。':err.includes('identity')||err.includes('身份')?'记录身份已变化，未执行本次操作。':err;return <section className="pair-console panel"><div className="panel-title"><span><LinkSimple/></span><strong>对照实验</strong><i/></div><div className="pair-console-row"><select value={scenario} onChange={e=>setScenario(e.target.value)} aria-label="选择对照场景"><option value="composite">多项异常</option><option value="prompt">回答偏离预期</option><option value="cost">处理成本升高</option><option value="loop">重复调用未结束</option></select><button onClick={create} disabled={!!busy}>{busy==='create'?'建立中…':'建立对照'}</button><button onClick={start} disabled={!!busy||!active||!['ready','created','pending'].includes(active.status)}>{busy==='start'?'开始中…':'开始对照'}</button></div><div className="pair-console-state">{visibleError||result||(active?`当前记录 · ${label(active.status)} · 自动处理`:'尚无当前对照记录')}</div>{active&&<><div className="pair-console-links"><a href={`/showcase/swarm?pair_id=${active.pair_id}`}>查看协作团队</a><a href={`/showcase/baseline?pair_id=${active.pair_id}`}>查看独立助手</a></div><div className="pair-console-row pair-console-secondary"><button onClick={()=>reset(`/api/pairs/${active.pair_id}/reset`,'本次对照')} disabled={!!busy}>重置本次对照</button><button onClick={()=>reset(`/api/pairs/${active.pair_id}/arms/swarm/reset`,'协作团队')} disabled={!!busy}>重置协作团队</button><button onClick={()=>reset(`/api/pairs/${active.pair_id}/arms/baseline/reset`,'独立助手')} disabled={!!busy}>重置独立助手</button></div></>}{history.length>0&&<select className="pair-history" value={active?.pair_id||''} onChange={e=>{const p=history.find(x=>x.pair_id===e.target.value);if(p)setActive(p)}} aria-label="选择历史对照记录"><option value="">历史对照记录</option>{history.map((p,i)=><option key={p.pair_id} value={p.pair_id}>记录 {history.length-i} · {label(p.status)}</option>)}</select>}</section>}
