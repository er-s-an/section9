import { useMemo, useState } from 'react'
import './office-room.css'
import './office-product.css'
import { currentOfficeEvents } from './office-events'

export type OfficeAgent={id:string;role:string;name:string;status:string;heartbeat_at:string|null;task_id:string|null;detail:string;capabilities:string[]}
export type OfficeEvent={event_id:string;sequence:number;event_type:string;occurred_at:string;run_id:string|null;incident_id:string|null;producer:string;generation:string;payload:Record<string,any>}

const clock=(v:string|null)=>v?new Date(v).toLocaleTimeString('zh-CN',{hour12:false}):'—'
const safe=(v:any)=>v===null||v===undefined?'未知':typeof v==='string'?v:JSON.stringify(v)
const spriteFor=(id:string)=>id==='sentry'||id==='observer'?1:id==='diagnoser'?2:id==='fixer-a'?3:id==='fixer-b'?4:id==='verifier'?2:id==='single'?3:4
const zoneFor=(id:string)=>id==='observer'||id==='investigator'||id==='sentry'||id==='diagnoser'?'monitoring':id==='fixer-a'||id==='fixer-b'?'execution':id==='verifier'?'verification':id==='single'?'baseline':'cost'
const roleNames:Record<string,string>={observer:'观察员',investigator:'调查员',reviewer:'复核员',coordinator:'协调员',sentry:'监测员',monitor:'监测员',diagnoser:'诊断员',diagnose:'诊断员','fixer-a':'修复员 A','fixer-b':'修复员 B',fixer:'修复员',repair:'修复员',verifier:'验收员',verify:'验收员',single:'独立助手',cost:'成本专家'}
const statusNames:Record<string,string>={idle:'待命',working:'处理中',paused:'已暂停',offline:'离线',starting:'准备中',resuming:'恢复中',diagnosing:'分析中',repairing:'执行中',verifying:'验收中',blocked:'等待处理'}
const zoneNames:Record<string,string>={monitoring:'监测区',consult:'会诊区',execution:'执行区',verification:'验收区',baseline:'独立处理区',cost:'成本分析区'}
const eventNames:Record<string,string>={'task.discovered':'发现待处理事项','task.claimed':'开始处理事项','diagnosis.completed':'完成原因分析','message.sent':'已同步处理意见','plan.created':'形成处置方案','plan.approved':'处置方案已获批准','action.applied':'完成修复动作','verification.completed':'完成验收','run.resolved':'事件已恢复','run.failed':'事件仍需关注'}
const displayRole=(a:OfficeAgent)=>roleNames[a.id]||roleNames[a.role]||a.name||'专项助手'
const displayStatus=(status:string)=>statusNames[status]||'状态待确认'
const humanMessage=(e:OfficeEvent)=>typeof e.payload?.message==='string'&&/message|chat|note|意见/i.test(e.event_type)?e.payload.message:null
const displayEvent=(e:OfficeEvent)=>humanMessage(e)||eventNames[e.event_type]||'有一条新消息'
const displayDetail=(a:OfficeAgent)=>a.status==='offline'?'离线，等待重新连接':a.detail||'暂无说明'
const eventDetail=(e:OfficeEvent)=>humanMessage(e)||eventNames[e.event_type]||'有一条新消息'
const incidentLabel=(status:string)=>({injected:'已发现',diagnosing:'分析中',repairing:'处理中',awaiting_approval:'待批准',verifying:'验收中',resolved:'已恢复',failed:'需关注',blocked:'等待处理'} as Record<string,string>)[status]||'处理中'

export default function OfficeScene({agents,events,generation,activeRunId,incidentStatus,muted,connected,investigation=false}:{investigation?:boolean;agents:OfficeAgent[];events:OfficeEvent[];generation:string;activeRunId:string|null;incidentStatus:string|null;muted:boolean;connected:boolean}){
 const [selected,setSelected]=useState<string|null>(null)
 const [missingBackground,setMissingBackground]=useState(false),[missingSprites,setMissingSprites]=useState<Record<string,boolean>>({})
 const current=useMemo(()=>currentOfficeEvents(events,generation,activeRunId),[events,generation,activeRunId])
 const positions:Record<string,string>={observer:'s9-office-agent--sentry',investigator:'s9-office-agent--diagnoser',reviewer:'s9-office-agent--verifier',coordinator:'s9-office-agent--fixer-a',sentry:'s9-office-agent--sentry',diagnoser:'s9-office-agent--diagnoser','fixer-a':'s9-office-agent--fixer-a','fixer-b':'s9-office-agent--fixer-b',verifier:'s9-office-agent--verifier',single:'s9-office-agent--single',cost:'s9-office-agent--cost'}
 const chosen=agents.find(a=>a.id===selected)
 const chosenEvents=chosen?current.filter(e=>e.producer===chosen.id&&(e.payload?.message||e.payload?.summary)).slice(0,3):[]
 return <section className="s9-office" aria-label="Section 9 实时办公室">
   <div className="s9-office__room" data-testid="office-room"><div className={'s9-office__background-fallback '+(missingBackground?'':'asset-fallback-hidden')} aria-label="办公室布局"><span>监测区</span><span>会诊区</span><span>{investigation?'复核区':'执行与验收区'}</span><span>待命区</span></div><img className="s9-office__background" src="/vendor/star-office/office_bg.webp" alt="Star Office 像素办公室背景" onLoad={()=>setMissingBackground(false)} onError={()=>setMissingBackground(true)}/><div className="s9-office__label s9-office__label--left">监测区</div><div className="s9-office__label s9-office__label--center">会诊区</div><div className="s9-office__label s9-office__label--server">{investigation?'复核区':'执行与验收区'}</div><div className="s9-office__label s9-office__label--rest">待命区</div><div className="s9-office__badge"><span>{connected?'数据已连接':'连接中断'}</span>{muted&&<span>通信已暂停</span>}</div><img className="s9-office__desk s9-office__desk--left" src="/vendor/star-office/desk-v3.webp" alt="监测工位"/><img className="s9-office__desk s9-office__desk--center" src="/vendor/star-office/desk-v3.webp" alt="会诊工位"/><img className="s9-office__sofa" src="/vendor/star-office/sofa-idle-v3.png" alt="值班沙发"/>
     <div className="s9-office__archive">{investigation?'调查记录':'处置经验'}<br/><span>事件复盘记录 · 只读</span></div>
     <div className="s9-office__agents" role="group" aria-label="实时角色工位">{agents.map(a=>{const missing=missingSprites[a.id]===true;const src=`/vendor/star-office/guest_anim_${spriteFor(a.id)}.webp`;const role=displayRole(a);return <button key={a.id} data-agent-id={a.id} data-status={a.status} data-zone={zoneFor(a.id)} className={`s9-office-agent ${positions[a.id]||'s9-office-agent--cost'} s9-office-agent--${a.status}`} onClick={()=>setSelected(a.id)} aria-label={`${role}，${displayStatus(a.status)}`}><span className="s9-office-agent__sprite" style={{backgroundImage:`url(${src})`}}><img className="s9-office-agent__preload" src={src} alt="" onLoad={()=>setMissingSprites(v=>({...v,[a.id]:false}))} onError={()=>setMissingSprites(v=>({...v,[a.id]:true}))}/><span className={'s9-office-agent__fallback '+(missing?'asset-fallback-visible':'asset-fallback-hidden')}>{(a.name||role||'专项助手').slice(0,1)}</span></span><strong>{role}</strong><small>{displayStatus(a.status)}</small><em>{clock(a.heartbeat_at)}</em></button>})}</div>
   </div>
   <div className="s9-office__caption">点击角色，查看当前状态与最新消息。{muted&&<b>通信已暂停</b>}</div>
   {chosen&&<aside className="s9-office__details" data-testid="agent-inspector" aria-live="polite"><div><strong>{displayRole(chosen)}</strong><span>{zoneNames[zoneFor(chosen.id)]||'当前工位'} · {displayStatus(chosen.status)}</span><button onClick={()=>setSelected(null)} aria-label="关闭角色详情">×</button></div><p className="s9-office__latest">{chosenEvents.length?displayEvent(chosenEvents[0]):displayDetail(chosen)}</p>{chosenEvents.length?chosenEvents.map(e=><article key={e.event_id}><span>{displayEvent(e)}</span><time>{clock(e.occurred_at)}</time><p>{eventDetail(e)}</p></article>):<p className="s9-office__empty">暂无新消息。</p>}<details className="s9-office__raw"><summary>查看原始记录</summary><p>角色标识：{chosen.id} · 任务标识：{chosen.task_id||'—'} · 轮次：{generation}</p>{chosenEvents.map(e=><p key={`raw-${e.event_id}`}>{e.event_type} · {e.event_id} · {clock(e.occurred_at)}</p>)}</details></aside>}
   {incidentStatus&&<div className="s9-office__incident">当前事件：{incidentLabel(incidentStatus)}</div>}
 </section>
}
