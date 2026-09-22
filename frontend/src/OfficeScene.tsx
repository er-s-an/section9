import { useMemo, useState } from 'react'
import './office-room.css'
import { currentOfficeEvents } from './office-events'

export type OfficeAgent={id:string;role:string;name:string;status:string;heartbeat_at:string|null;task_id:string|null;detail:string;capabilities:string[]}
export type OfficeEvent={event_id:string;sequence:number;event_type:string;occurred_at:string;run_id:string|null;incident_id:string|null;producer:string;generation:string;payload:Record<string,any>}

const clock=(v:string|null)=>v?new Date(v).toLocaleTimeString('zh-CN',{hour12:false}):'—'
const safe=(v:any)=>v===null||v===undefined?'未知':typeof v==='string'?v:JSON.stringify(v)
const spriteFor=(id:string)=>id==='sentry'?1:id==='diagnoser'?2:id==='fixer-a'?3:id==='fixer-b'?4:id==='verifier'?2:id==='single'?3:4
const zoneFor=(id:string)=>id==='sentry'||id==='diagnoser'?'monitoring':id==='fixer-a'||id==='fixer-b'?'execution':id==='verifier'?'verification':id==='single'?'baseline':'cost'

export default function OfficeScene({agents,events,generation,activeRunId,incidentStatus,muted,connected}:{agents:OfficeAgent[];events:OfficeEvent[];generation:string;activeRunId:string|null;incidentStatus:string|null;muted:boolean;connected:boolean}){
 const [selected,setSelected]=useState<string|null>(null)
 const current=useMemo(()=>currentOfficeEvents(events,generation,activeRunId),[events,generation,activeRunId])
 const positions:Record<string,string>={sentry:'s9-office-agent--sentry',diagnoser:'s9-office-agent--diagnoser','fixer-a':'s9-office-agent--fixer-a','fixer-b':'s9-office-agent--fixer-b',verifier:'s9-office-agent--verifier',single:'s9-office-agent--single',cost:'s9-office-agent--cost'}
 const chosen=agents.find(a=>a.id===selected)
 const chosenEvents=chosen?current.filter(e=>e.producer===chosen.id&&(e.payload?.message||e.payload?.summary)).slice(0,3):[]
 return <section className="s9-office" aria-label="Section 9 实时办公室">
   <div className="s9-office__room" data-testid="office-room"><img className="s9-office__background" src="/vendor/star-office/office_bg.webp" alt="Star Office 像素办公室背景"/><div className="s9-office__label s9-office__label--left">MONITORING / 监测</div><div className="s9-office__label s9-office__label--center">CONSULT / 会诊与修复</div><div className="s9-office__label s9-office__label--server">SECTION 9 / 机房</div><div className="s9-office__label s9-office__label--rest">STANDBY / 待命室</div><div className="s9-office__badge">GEN {generation} · {connected?'LIVE':'OFFLINE'} · {muted?'已禁言':''}</div><img className="s9-office__desk s9-office__desk--left" src="/vendor/star-office/desk-v3.webp" alt="监测工位"/><img className="s9-office__desk s9-office__desk--center" src="/vendor/star-office/desk-v3.webp" alt="会诊工位"/><img className="s9-office__sofa" src="/vendor/star-office/sofa-idle-v3.png" alt="值班沙发"/>
     <div className="s9-office__archive">ARCHIVE<br/><span>CASE FILES / READ ONLY</span></div>
     <div className="s9-office__agents" role="group" aria-label="实时代理工位">{agents.map(a=><button key={a.id} data-agent-id={a.id} data-status={a.status} data-zone={zoneFor(a.id)} className={`s9-office-agent ${positions[a.id]||'s9-office-agent--cost'} s9-office-agent--${a.status}`} onClick={()=>setSelected(a.id)} aria-label={`${a.name} ${a.status}`}><span className="s9-office-agent__sprite" style={{backgroundImage:`url(/vendor/star-office/guest_anim_${spriteFor(a.id)}.webp)`}}/><strong>{a.name}</strong><small>{({idle:'待命',working:'工作中',paused:'已暂停',offline:'离线',starting:'启动中',resuming:'恢复中'} as Record<string,string>)[a.status]||'状态未知'}</small><em>{clock(a.heartbeat_at)}</em></button>)}</div>
   </div>
   <div className="s9-office__caption">角色位置按职责与实时状态映射：工作中靠近本人工作桌/机房，待命回到对应工位，暂停回到本人待命点。{muted&&<b>通信层：已禁言</b>}</div>
   {chosen&&<aside className="s9-office__details" data-testid="agent-inspector" aria-live="polite"><div><strong>{chosen.name}</strong><span>{chosen.role} · {chosen.status}</span><button onClick={()=>setSelected(null)} aria-label="关闭角色详情">×</button></div><p>心跳 {clock(chosen.heartbeat_at)} · 上报任务 {chosen.task_id||'—'} · 第 {generation} 轮</p><p>{chosen.detail||'暂无任务说明'}</p>{chosenEvents.length?chosenEvents.map(e=><article key={e.event_id}><code>{e.event_type} · {e.event_id}</code><span>{clock(e.occurred_at)}</span><p>{safe(e.payload?.message||e.payload?.summary)}</p></article>):<p className="s9-office__empty">本轮暂无该角色消息，以实时状态为准。</p>}</aside>}
   {incidentStatus&&<div className="s9-office__incident">INCIDENT / {incidentStatus}</div>}
 </section>
}
