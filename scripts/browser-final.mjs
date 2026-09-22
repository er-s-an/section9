import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const root=path.resolve(path.dirname(new URL(import.meta.url).pathname),'..');
const out=path.join(root,'artifacts/final-browser');await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});const p=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];p.on('pageerror',e=>errors.push(String(e)));p.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
const url='http://127.0.0.1:9019';
const state=async()=>(await p.request.get(url+'/api/state')).json();
const report={started_at:new Date().toISOString(),checks:[],errors};
async function wait(fn,label,t=90000){const start=Date.now();while(Date.now()-start<t){const r=await fn();if(r)return r;await p.waitForTimeout(400)}throw new Error('Timeout '+label)}
try{
 await p.request.post(url+'/api/agents/join',{data:{}});
 await p.goto(url,{waitUntil:'networkidle'});
 await p.getByLabel('客服消息').first().fill('耳机收到3天，已经激活，现在还可以无理由退货吗？运费谁付？');
 await p.getByRole('button',{name:'发送消息',exact:true}).click();
 await wait(async()=>/支持|可以|7 天|7天/.test(await p.locator('.chat-log').innerText().catch(()=>'')),'real customer response');
 await p.screenshot({path:path.join(out,'01-healthy-chat.png'),fullPage:true});
 report.checks.push({name:'real customer request from browser',passed:true});
 await p.getByRole('button',{name:'复合故障',exact:true}).click();
 const incident=await wait(async()=>(await state()).incident,'injected composite');
 const r=await wait(async()=>{const x=await(await p.request.get(url+'/api/runs/'+incident.id)).json();return ['resolved','failed'].includes(x.status)?x:null},'actual business close');
 assert.equal(r.status,'resolved');assert.ok(r.verification.passed);
 await wait(async()=>{const x=await(await p.request.get(url+'/api/runs/'+r.id)).json();return x.events.some(e=>e.event_type==='telemetry.span_received')},'real collector span persisted',40000);
 const detail=await(await p.request.get(url+'/api/runs/'+r.id)).json();
 await fs.writeFile(path.join(out,'composite-run.json'),JSON.stringify(detail,null,2));
 report.run={id:r.id,elapsed_s:r.elapsed_s,status:r.status,backend_trace_ids:detail.events.filter(e=>e.event_type==='telemetry.span_received').map(e=>e.payload.trace_id)};
 report.checks.push({name:'composite real restore and collector control persistence',passed:true});
 await p.screenshot({path:path.join(out,'02-composite-resolved.png'),fullPage:true});
 await p.getByRole('button',{name:'五行计分',exact:true}).click();await p.locator('.matrix-cell').first().waitFor();
 await p.locator('.matrix-cell:not([disabled])').first().click();await p.locator('.run-picker').waitFor();await p.locator('.run-picker button').nth(1).click();await p.locator('.drawer-body').waitFor();
 const ids=await p.locator('.detail-event').evaluateAll(es=>es.map(e=>e.querySelector('span')?.textContent));assert.equal(ids.length,new Set(ids).size);
 await p.screenshot({path:path.join(out,'03-score-evidence.png'),fullPage:true});
 await p.locator('.drawer-head button').click();await p.locator('.run-picker-head button').click();await p.screenshot({path:path.join(out,'04-scoreboard.png'),fullPage:true});
 await p.getByRole('button',{name:'办公室',exact:true}).click();await p.getByRole('button',{name:'重置实验室',exact:true}).click();await wait(async()=>!(await state()).incident,'final reset');
 await p.setViewportSize({width:1280,height:800});await p.waitForTimeout(600);assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await p.screenshot({path:path.join(out,'05-laptop-1280.png'),fullPage:true});
 await p.setViewportSize({width:1440,height:1000});await p.screenshot({path:path.join(out,'06-ready-home.png'),fullPage:true});
 report.checks.push({name:'score per-run chooser, unique evidence, 1280 layout and final clean reset',passed:true});assert.equal(errors.length,0);report.passed=true;
}catch(e){report.passed=false;report.failure=String(e.stack||e);process.exitCode=1;await p.screenshot({path:path.join(out,'failure.png'),fullPage:true});}
finally{report.finished_at=new Date().toISOString();await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report));}
