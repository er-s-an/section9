import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowClockwise, LinkSimple, ShieldCheck, X } from '@phosphor-icons/react'
import './showcase.css'

type ArmName = 'swarm' | 'baseline'
type Pair = { pair_id:string; status:string; spec_hash:string; swarm_run_id:string; baseline_run_id:string; immutable_spec:any; comparison_integrity:any; links:{swarm:string;baseline:string}; created_at:string; started_at:string|null; ended_at:string|null }
type Event = { event_id:string; sequence:number; scope:string; pair_id:string; run_id:string; arm:ArmName; event_type:string; occurred_at:string; producer:string; stage:string|null; config_revision:string; payload:any }
type Projection = { pair_id:string; run_id:string; arm:ArmName; spec_hash:string; pair_status:string; run:any; config:any; stages:any[]; agents:any[]; events:Event[]; as_of_sequence:number; usage:any; dependencies:any; verification:any; rca:any; comparison_integrity:any; started_at:string|null }
const api=async<T,>(path:string,init?:RequestInit):Promise<T>=>{const r=await fetch(path,{...init,headers:{'Content-Type':'application/json',...(init?.headers||{})}});const b=await r.json();if(!r.ok)throw new Error(b?.error?.message||JSON.stringify(b?.detail)||`HTTP ${r.status}`);return b}
const post=(path:string,body:any={},key?:string)=>api<any>(path,{method:'POST',body:JSON.stringify(body),headers:key?{'Idempotency-Key':key}:undefined})
const labels:Record<string,string>={ready:'已准备',preparing:'准备中',running:'运行中',completed:'实验结束',resolved:'业务恢复',failed:'失败',interrupted:'已中断',reset:'已重置',setup_failed:'准备失败',injected:'等待检测',diagnosing:'诊断中',repairing:'修复中',verifying:'验收中',pending:'待测',waiting:'等待审批',cancelled:'已取消',passed:'通过',combined:'合并决策',skipped:'未执行',idle:'待命',working:'工作中',starting:'启动中',paused:'已暂停',offline:'离线',resuming:'恢复中',stopped:'本轮已结束'}
const label=(v:string)=>labels[v]||v||'未知'
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
 const modelState=modelStatus==='available'?'AVAILABLE':modelStatus==='degraded'||modelStatus==='unconfigured'?'DEGRADED':'UNKNOWN'
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
 const status=data?.run?.status||data?.pair_status||pair?.status||'pending'
 const resultText=data?.run?.elapsed_s!=null?`${data.run.elapsed_s}s`:'尚未完成计时'
 return <div className="sc-page">
  <header className="sc-header"><a className="sc-brand" href="/">S9<span>公安九课<span>PAIRED EXPERIMENT / {arm.toUpperCase()}</span></span></a><div className="sc-header-tools"><span>只读 · {active?'LIVE RUN':data?.run?'HISTORICAL PAIR':'尚未启动'}</span><span className={hook.status==='live'?'sc-good':'sc-wait'}>{({live:'● 实时连接',reconnecting:'↻ 连接中断 · 重连中',connecting:'◌ 正在连接',error:'! 读取失败',empty:'暂无实验'} as any)[hook.status]}</span><span className={age!==null&&age>10?'sc-bad':'sc-muted'}>{age===null?'快照待取':`快照 ${age}s 前`}</span><button onClick={hook.refresh} aria-label="刷新快照"><ArrowClockwise/></button><a href={`/showcase/${arm==='swarm'?'baseline':'swarm'}${pair?`?pair_id=${pair.pair_id}`:''}`}>另一屏 ↗</a></div></header>
  {(error||hook.error)&&<div role="alert" className="sc-error">{error||hook.error} <button onClick={hook.refresh}>重试读取</button></div>}
  {loading?<div className="sc-empty">正在读取成对实验…</div>:!pair?<div className="sc-empty"><h1>{error?'无法打开此 Pair':'尚无成对实验'}</h1><p>请在原控制台创建并启动 Pair。打开此页不会调用模型。</p><a href="/">返回操作控制台 →</a></div>:<>
   <section className="sc-identity"><div><span className="sc-eyebrow">{arm==='swarm'?'协作实验室 / SWARM':'独立工作台 / SINGLE AGENT'}</span><h1>{arm==='swarm'?'多 Agent 协作':'单 Agent 基线'} <b className={status==='resolved'?'sc-good':/failed|reset/.test(status)?'sc-bad':'sc-wait'}>{label(status)}</b></h1></div><div className="sc-ids"><select aria-label="历史 Pair" value={pair.pair_id} onChange={e=>choose(e.target.value)}>{pairs.map(p=><option value={p.pair_id} key={p.pair_id}>{p.pair_id} · {label(p.pair_id===pair.pair_id?(data?.pair_status||p.status):p.status)}</option>)}</select><button onClick={()=>show('不可变 PairSpec 与来源',pair)}>RUN {data?.run_id||pair[arm==='swarm'?'swarm_run_id':'baseline_run_id']} · SPEC {pair.spec_hash.slice(0,12)}</button></div><div className="sc-outcome"><strong>{resultText}</strong><small>{pair.immutable_spec.scenario} / seed {pair.immutable_spec.seed} · 每侧 {pair.immutable_spec.budget_policy.total_tokens_per_arm} tokens</small><small>健康起点 {pair.immutable_spec.baseline_config_hash.slice(0,12)}</small><small>{data?.comparison_integrity?.eligible===false?'⚠ 已受干预／不具比较资格':'同规格 · 独立状态与预算'}</small></div></section>
   <section className="sc-stages" aria-label="真实阶段进度">{(data?.stages||[]).map((s:any,i:number)=><button key={s.id} className={`sc-stage sc-stage-${s.status}`} data-stage={s.id} data-status={s.status} onClick={()=>show(`${s.label} / 事件依据`,{...s,events:data?.events.filter(e=>s.source_event_ids.includes(e.event_id))})}><span className="sc-eyebrow">0{i+1} / {label(s.status)}</span><strong>{s.label}</strong><small>{time(s.started_at)}{s.ended_at&&s.ended_at!==s.started_at?` → ${time(s.ended_at)}`:''}</small><code>{s.source_event_ids?.[0]?.slice(0,12)||'等待真实事件'}</code></button>)}</section>
   <section className="sc-center">{arm==='swarm'?<SwarmOffice data={data} show={show}/>:<BaselineChain data={data} show={show}/>}<div className="sc-business"><span className="sc-eyebrow">被监控应用 / 小智客服</span><strong>业务证据</strong><p>本轮检测与独立验收请求</p>{data?.events.filter(e=>e.event_type==='request.completed').slice(-2).map(e=><button key={e.event_id} onClick={()=>show('客服请求原始证据',e)}><small>{e.payload.question||e.payload.purpose||'本轮业务请求'}</small><b>{e.payload.answer||e.payload.summary||e.event_type}</b><small>{e.event_id.slice(0,12)} · {time(e.occurred_at)}</small></button>)}{!data?.events.some(e=>e.event_type==='request.completed')&&<p className="sc-muted">尚无业务响应；不会用模拟答案填充。</p>}<small>远程 EvoMap API · 模型依赖 {modelState}{!active?'（当前未调用）':''}</small><button className="sc-model-info" onClick={()=>show('模型身份与依赖',{requested_model:pair.immutable_spec.model,observed_models:returnedModels,dependency:data?.dependencies||{},meaning:'AVAILABLE 仅表示本 arm 最近一次真实请求成功；UNKNOWN 不表示服务已验证。'})}>{pair.immutable_spec.model}<br/>{returnedModels.length===1?`返回：${returnedModels[0]}`:returnedModels.length>1?'⚠ 返回模型不一致':'模型身份待确认'}</button></div></section>
   <Logbook events={data?.events||[]} startedAt={data?.started_at||null} cursor={data?.as_of_sequence||0}/>
   <footer className="sc-evidence">{[['配置 diff',data?.config],['Usage / 排队',data?.usage],['验收 checks',data?.verification],['RCA',data?.rca],['失败与拒绝',data?.events.filter(failure)]].map(([title,value])=><button key={String(title)} onClick={()=>show(String(title),value)}>{String(title)} <span>↗</span></button>)}</footer>
  </>}
  {drawer&&<EvidenceDrawer {...drawer} onClose={()=>setDrawer(null)}/>}
 </div>
}

function SwarmOffice({data,show}:{data:Projection|null;show:(t:string,v:any)=>void}){
 const placed:[number,number][]=[]
 const positions:Record<string,[number,number]>={monitor:[14,45],analysis:[32,40],meeting:[49,60],execution:[73,44],verification:[69,68],archive:[84,63],idle:[27,74]}
 return <div className="sc-office" aria-label="当前 swarm 办公室"><img src="/vendor/star-office/office_bg.webp" alt="复用 Star-Office-UI 的像素实验室"/><div className="sc-zone sc-zone-monitor">监测 · 分析</div><div className="sc-zone sc-zone-meeting">会诊桌</div><div className="sc-zone sc-zone-execution">执行与验收机房</div>{(data?.agents||[]).map((a:any,i:number)=>{let [x,y]=positions[a.position]||positions.idle;if(a.position==='idle'){x=12+i*18;y=65}else if(placed.some(([px,py])=>Math.abs(px-x)<17&&Math.abs(py-y)<32)){x=12+i*18;y=64}placed.push([x,y]);return <button key={a.id} data-agent-id={a.id} data-source-event={a.source_event_id||''} data-position-event={a.position_event_id||''} className="sc-actor" style={{left:`${x}%`,top:`${y}%`}} onClick={()=>show(a.name+' / 活动依据',{agent:a,events:data?.events.filter(e=>e.event_id===a.source_event_id||e.event_id===a.position_event_id)})}><span className={`sc-sprite sc-sprite-${i%4+1}`}/><strong>{a.name}</strong><small>{label(a.status)} · {time(a.heartbeat_at)}</small></button>})}<div className="sc-room-note">位置为真实活动映射 · 点击角色查看 event_id</div></div>
}
function BaselineChain({data,show}:{data:Projection|null;show:(t:string,v:any)=>void}){const a=data?.agents.find(x=>x.id==='single');return <div className="sc-baseline"><div className="sc-solo"><span className="sc-sprite sc-sprite-3"/><strong>单 Agent</strong><span>{label(a?.status||'pending')}</span><button onClick={()=>show('单 Agent 活动来源',a)}>查看活动证据 ↗</button></div><div className="sc-chain"><h2>同一份原始业务事实，一位推理 Agent</h2><div><span>读取业务证据</span> → <span>诊断与计划<br/><small>同一次模型决策</small></span> → <span>受控执行</span></div><p>↓ 独立确定性验收服务</p><small>探针、权限检查和业务验收属于实验基础设施，不计为推理 Agent。</small></div></div>}
function Logbook({events,startedAt,cursor}:{events:Event[];startedAt:string|null;cursor:number}){
 const box=useRef<HTMLDivElement>(null),follow=useRef(true),previous=useRef(0)
 const [unread,setUnread]=useState(0),[filter,setFilter]=useState('all'),[paused,setPaused]=useState(false)
 useLayoutEffect(()=>{const added=Math.max(0,events.length-previous.current);previous.current=events.length;if(follow.current&&!paused){if(box.current)box.current.scrollTop=box.current.scrollHeight;setUnread(0)}else setUnread(x=>x+added)},[events.length,paused])
 const jump=()=>{follow.current=true;setPaused(false);setUnread(0);if(box.current)box.current.scrollTop=box.current.scrollHeight}
 return <section className="sc-log"><div className="sc-log-head"><strong>LOGBOOK <small>游标 {cursor} · {events.length} 条真实事件</small></strong><div><select aria-label="日志筛选" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">全部事件</option><option value="failure">失败 / 拒绝 / 取消</option><option value="model">模型排队 / 调用</option></select><button onClick={()=>setPaused(!paused)}>{paused?'恢复跟随':'暂停自动滚动'}</button>{unread>0&&<button onClick={jump}>{unread} 条新事件 ↓</button>}</div></div><div className="sc-log-scroll" ref={box} onScroll={()=>{if(box.current)follow.current=box.current.scrollHeight-box.current.clientHeight-box.current.scrollTop<24}}>{events.filter(e=>filter==='all'||filter==='failure'&&failure(e)||filter==='model'&&e.event_type.startsWith('model.')).map(e=><details key={e.event_id} className={failure(e)?'sc-log-failure':''}><summary><time>{relative(e.occurred_at,startedAt)}</time><span>{e.producer}</span><b>{e.event_type}</b><span>{e.payload.summary||e.payload.error||'记录已提交'}</span><code>{e.event_id.slice(0,12)}</code><small>rev {e.config_revision||'—'}</small></summary><pre>{pretty(e)}</pre></details>)}{!events.length&&<p className="sc-muted">尚无事件。实验只会由控制台启动。</p>}</div></section>
}
function EvidenceDrawer({title,value,onClose}:{title:string;value:any;onClose:()=>void}){const box=useRef<HTMLElement>(null);useEffect(()=>{const before=document.activeElement as HTMLElement;box.current?.querySelector('button')?.focus();const fn=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();if(e.key==='Tab'){const items=Array.from(box.current?.querySelectorAll<HTMLElement>('button,[tabindex="0"]')||[]);const index=items.indexOf(document.activeElement as HTMLElement);e.preventDefault();items[(index+(e.shiftKey?-1:1)+items.length)%items.length]?.focus()}};document.addEventListener('keydown',fn);return()=>{document.removeEventListener('keydown',fn);before?.focus()}},[onClose]);return <div className="sc-backdrop" onMouseDown={e=>e.target===e.currentTarget&&onClose()}><aside ref={box} className="sc-drawer" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button onClick={onClose} aria-label="关闭证据"><X/></button></header><p>此处显示本轮已记录的证据，查看不会产生模型调用。</p><pre tabIndex={0}>{pretty(value)}</pre></aside></div>}
export function PairConsole(){const [active,setActive]=useState<Pair|null>(null),[history,setHistory]=useState<Pair[]>([]),[scenario,setScenario]=useState('composite'),[busy,setBusy]=useState(false),[err,setErr]=useState('');const load=useCallback(()=>Promise.all([api<{active_pair:Pair|null}>('/api/pairs/active'),api<{items:Pair[]}>('/api/pairs')]).then(([a,l])=>{setActive(a.active_pair);setHistory(l.items||[])}).catch(e=>setErr(e.message)),[]);useEffect(()=>{load()},[load]);const create=async()=>{setBusy(true);try{const p=await post('/api/pairs',{scenario,seed:42},`console-${Date.now()}`);setActive(p);await load()}catch(e:any){setErr(e.message)}finally{setBusy(false)}};const start=async()=>{if(!active)return;setBusy(true);try{await post(`/api/pairs/${active.pair_id}/start`,{expected_spec_hash:active.spec_hash});await load()}catch(e:any){setErr(e.message)}finally{setBusy(false)}};const reset=async(path:string)=>{if(!active)return;setBusy(true);try{await post(path,{});await load()}catch(e:any){setErr(e.message)}finally{setBusy(false)}};return <section className="pair-console panel"><div className="panel-title"><span><LinkSimple/></span><strong>Pair showcase</strong><i/></div><div className="pair-console-row"><select value={scenario} onChange={e=>setScenario(e.target.value)} aria-label="Pair 场景"><option value="composite">复合故障</option><option value="prompt">语义退化</option><option value="cost">成本膨胀</option><option value="loop">工具死循环</option></select><button onClick={create} disabled={busy}>创建</button><button onClick={start} disabled={busy||!active||active.status!=='ready'}>开始</button></div><div className="pair-console-state">{err||(active?`${active.pair_id} · ${active.status}`:'尚无活动 Pair')}</div>{active&&<><div className="pair-console-links"><a href={`/showcase/swarm?pair_id=${active.pair_id}`}>SWARM</a><a href={`/showcase/baseline?pair_id=${active.pair_id}`}>BASELINE</a></div><div className="pair-console-row pair-console-secondary"><button onClick={()=>reset(`/api/pairs/${active.pair_id}/reset`)} disabled={busy}>重置 Pair</button><button onClick={()=>reset(`/api/pairs/${active.pair_id}/arms/swarm/reset`)} disabled={busy}>重置 SWARM</button><button onClick={()=>reset(`/api/pairs/${active.pair_id}/arms/baseline/reset`)} disabled={busy}>重置 BASE</button></div></>}{history.length>0&&<select className="pair-history" value={active?.pair_id||''} onChange={e=>{const p=history.find(x=>x.pair_id===e.target.value);if(p)setActive(p)}} aria-label="历史 Pair"><option value="">历史 Pair</option>{history.map(p=><option key={p.pair_id} value={p.pair_id}>{p.pair_id} · {p.status}</option>)}</select>}</section>}
