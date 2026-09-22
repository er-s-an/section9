import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const base=process.env.S9_BASE_URL||'http://127.0.0.1:9019';
const out=path.resolve('artifacts/showcase-browser',new Date().toISOString().replaceAll(':','-'));
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1280,height:800}});
const pages={swarm:await context.newPage(),baseline:await context.newPage()};
const report={base,started_at:new Date().toISOString(),checks:[],pairs:[],errors:[],mutations:[],identity:null};
for(const [arm,page] of Object.entries(pages)){
 page.on('pageerror',e=>report.errors.push({arm,error:String(e)}));
 page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))report.mutations.push({arm,method:r.method(),url:r.url()})});
}
async function api(url,body){const response=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw Error(JSON.stringify(value));return value}
async function wait(fn,label,limit=185000){const start=Date.now();while(Date.now()-start<limit){const v=await fn();if(v)return v;await new Promise(r=>setTimeout(r,350))}throw Error('Timeout '+label)}
const check=(name,evidence)=>report.checks.push({name,passed:true,evidence});
async function savePair(pair,label){const data={pair:await api('/api/pairs/'+pair.pair_id),arms:{}};for(const arm of ['swarm','baseline'])data.arms[arm]=await api(`/api/pairs/${pair.pair_id}/arms/${arm}`);await fs.writeFile(path.join(out,label+'.json'),JSON.stringify(data,null,2));report.pairs.push({pair_id:pair.pair_id,label,status:data.pair.status,arms:Object.fromEntries(Object.entries(data.arms).map(([a,v])=>[a,{run_id:v.run_id,status:v.run?.status,elapsed_s:v.run?.elapsed_s,usage:v.usage.known_tokens,unknown:v.usage.unknown_count}]))});return data}
try{
 report.identity=(await api('/api/health')).identity;
 const initial=await api('/api/pairs');
 for(const [arm,page] of Object.entries(pages)){await page.goto(base+'/showcase/'+arm);await page.waitForTimeout(500)}
 if(!initial.items.length){for(const p of Object.values(pages))await p.getByText('尚无成对实验',{exact:true}).waitFor();check('honest_empty_without_model_or_mutation',(await api('/api/pairs')).items.length===0)}
 const unknown=await context.newPage();await unknown.goto(base+'/showcase/swarm?pair_id=missing');await unknown.getByRole('heading',{name:'无法打开此 Pair'}).waitFor();assert.equal((await fetch(base+'/api/pairs/missing')).status,404);assert.equal((await fetch(base+'/api/nonexistent-showcase')).status,404);check('unknown_pair_and_api_not_swallowed',true);await unknown.close();
 const consolePage=await context.newPage();await consolePage.goto(base);const controls=consolePage.locator('.pair-console');await controls.getByLabel('Pair 场景').selectOption('composite');await controls.getByRole('button',{name:'创建',exact:true}).click();
 const pair=await wait(async()=>{const p=(await api('/api/pairs/active')).active_pair;return p&&p.status==='ready'&&!initial.items.some(x=>x.pair_id===p.pair_id)?p:null},'pair created');
 const ready=await savePair(pair,'01-ready');assert.notEqual(pair.swarm_run_id,pair.baseline_run_id);assert.equal(ready.arms.swarm.config.baseline_hash,ready.arms.baseline.config.baseline_hash);assert.equal(ready.arms.swarm.usage.records.length,0);assert.equal(ready.arms.baseline.usage.records.length,0);check('create_freezes_two_run_ids_no_provider',pair.spec_hash);
 for(const [arm,page] of Object.entries(pages)){await page.goto(base+pair.links[arm]);await page.locator('.sc-stages button').first().waitFor();await page.screenshot({path:path.join(out,`02-${arm}-ready.png`)});assert.equal(new URL(page.url()).searchParams.get('pair_id'),pair.pair_id)}
 await controls.getByRole('button',{name:'开始',exact:true}).click();
 await wait(async()=>{const p=await api('/api/pairs/'+pair.pair_id);return p.status==='running'},'simultaneous running');
 const started=await savePair(pair,'03-running');assert(started.arms.swarm.run&&started.arms.baseline.run);assert.equal(started.arms.swarm.run.generation,started.arms.baseline.run.generation);check('two_active_arms_same_generation',true);
 await pages.swarm.screenshot({path:path.join(out,'04-swarm-running.png')});
 for(const p of Object.values(pages))await p.getByRole('button',{name:'暂停自动滚动',exact:true}).click();
 await context.setOffline(true);await new Promise(r=>setTimeout(r,1200));await context.setOffline(false);
 const ended=await wait(async()=>{const p=await api('/api/pairs/'+pair.pair_id);return p.status==='completed'||p.status==='interrupted'?p:null},'paired terminal');
 const final=await savePair(ended,'05-terminal');
 for(const [arm,page] of Object.entries(pages)){
  await page.getByRole('button',{name:'刷新快照'}).click();
  await wait(async()=>Number((await page.locator('.sc-log-head small').innerText()).match(/游标 (\d+)/)?.[1])>=final.arms[arm].as_of_sequence,'SSE snapshot catchup '+arm,20000);
  const ids=await page.locator('.sc-log details summary code').allTextContents();assert.equal(new Set(ids).size,ids.length);check('reconnect_no_duplicate_'+arm,{shown:ids.length,cursor:final.arms[arm].as_of_sequence});
  await page.getByRole('button',{name:/条新事件/}).waitFor();check('paused_scroll_keeps_receiving_'+arm,true);await page.getByRole('button',{name:/条新事件/}).click();
  for(const size of [{width:1280,height:800},{width:1440,height:1000},{width:1024,height:640}]){await page.setViewportSize(size);await page.screenshot({path:path.join(out,`06-${arm}-${size.width}.png`)});const metrics=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,evidenceBottom:document.querySelector('.sc-evidence').getBoundingClientRect().bottom,height:innerHeight,logHeight:document.querySelector('.sc-log-scroll').clientHeight}));assert(metrics.scrollWidth<=metrics.width);if(size.height>=720)assert(metrics.evidenceBottom<=metrics.height);else assert(metrics.evidenceBottom>0);assert(metrics.logHeight>=100);check(`layout_${arm}_${size.width}`,metrics)}
  await page.getByRole('button',{name:'验收 checks'}).click();await page.getByRole('dialog').waitFor();await page.keyboard.press('Tab');assert.equal(await page.locator('.sc-drawer pre').evaluate(e=>e===document.activeElement),true);await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);check('drawer_keyboard_'+arm,true);
  await page.reload();await page.locator('.sc-log-head small').waitFor();assert.equal(new URL(page.url()).searchParams.get('pair_id'),pair.pair_id);
 }
 assert(final.arms.swarm.events.every(e=>e.pair_id===pair.pair_id&&e.arm==='swarm'&&e.run_id===pair.swarm_run_id));assert(final.arms.baseline.agents.length===1&&final.arms.baseline.agents[0].id==='single');check('scope_and_single_reasoning_actor',true);
 for(const arm of ['swarm','baseline']){const projection=final.arms[arm];const eventIds=new Set(projection.events.map(e=>e.event_id));assert(projection.stages.every(s=>s.source_event_ids.every(id=>eventIds.has(id))));check('stage_evidence_'+arm,projection.stages.map(s=>({id:s.id,status:s.status,events:s.source_event_ids.length})))}
 const old=initial.items.find(p=>p.pair_id!==pair.pair_id);
 if(old){const page=pages.swarm;const pattern=`**/api/pairs/${pair.pair_id}/arms/swarm`;await page.route(pattern,async route=>{const response=await route.fetch();await new Promise(r=>setTimeout(r,800));await route.fulfill({response}).catch(()=>{})});await page.getByRole('button',{name:'刷新快照'}).click();await page.getByLabel('历史 Pair').selectOption(old.pair_id);await wait(async()=>await page.locator('.sc-ids button').innerText().then(x=>x.includes(old.swarm_run_id)).catch(()=>false),'switch history');await page.waitForTimeout(1500);assert((await page.locator('.sc-ids button').innerText()).includes(old.swarm_run_id));assert.equal(new URL(page.url()).searchParams.get('pair_id'),old.pair_id);await page.unroute(pattern);check('late_old_scope_response_cannot_replace_selected_pair',old.pair_id);await page.getByLabel('历史 Pair').selectOption(pair.pair_id);await wait(async()=>await page.locator('.sc-ids button').innerText().then(x=>x.includes(pair.swarm_run_id)).catch(()=>false),'return current');}
 assert.equal(report.mutations.length,0);assert.equal(report.errors.length,0);check('read_only_showcase_no_mutations',true);
 check('real_model_business_outcomes',Object.fromEntries(Object.entries(final.arms).map(([a,s])=>[a,s.run.status])));
 // Keep failed results. A failing arm is an experimental outcome, not a QA pass
 // requirement; the report records it without silently rerunning a single side.
} catch(e){report.failure=String(e);report.checks.push({name:'browser_suite',passed:false,error:String(e)});process.exitCode=1}
finally{report.ended_at=new Date().toISOString();await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({out,checks:report.checks.length,pairs:report.pairs,errors:report.errors,failure:report.failure},null,2))}
