import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = require('@playwright/test');
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const out = path.join(root,'artifacts/browser-memory',new Date().toISOString().replaceAll(':','-'));
await fs.mkdir(out,{recursive:true});
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const url='http://127.0.0.1:9019';
const errors=[];
page.on('pageerror',e=>errors.push(String(e)));
const report={started_at:new Date().toISOString(),runs:[],checks:[],errors};
const state=async()=>(await page.request.get(url+'/api/state')).json();
async function wait(fn,label,timeout=90000){const start=Date.now();while(Date.now()-start<timeout){const r=await fn();if(r)return r;await page.waitForTimeout(400)}throw new Error('Timeout: '+label)}
async function reset(){const before=(await state()).generation;const start=Date.now();await page.getByRole('button',{name:'重置实验室',exact:true}).click();await wait(async()=>(await state()).generation!==before,'reset');return (Date.now()-start)/1000}
try{
 await page.goto(url,{waitUntil:'networkidle'});
 await reset();
 if(!(await state()).memory_enabled)await page.locator('.control-line').filter({hasText:'下轮 Playbook'}).getByRole('button').click();
 await wait(async()=>(await state()).memory_enabled,'real memory toggle');
 for(let i=0;i<2;i++){
  await page.getByRole('button',{name:'语义退化',exact:true}).click();
  const current=await wait(async()=>(await state()).incident,'new incident');
  const r=await wait(async()=>{const r=await (await page.request.get(url+'/api/runs/'+current.id)).json();return ['resolved','failed'].includes(r.status)?r:null},'real memory close');
  await wait(async()=>{const x=await(await page.request.get(url+'/api/runs/'+r.id)).json();return x.events.some(e=>e.event_type==='memory.recorded')?x:null},'official local GEP record');
  const detail=await(await page.request.get(url+'/api/runs/'+r.id)).json();
  await fs.writeFile(path.join(out,`round-${i+1}.json`),JSON.stringify(detail,null,2));
  assert.equal(detail.status,'resolved');assert.equal(detail.condition,'memory');assert.equal(detail.reuseapproved,true);
  report.runs.push({id:r.id,elapsed_s:r.elapsed_s,selected_id:detail.memory_used_id,passed:true});
  await page.screenshot({path:path.join(out,`round-${i+1}.png`),fullPage:true});
  report.checks.push({name:`round ${i+1} real match, model decision, execution, external verification and GEP write`,passed:true});
  report.reset_s=await reset();
 }
 await page.getByRole('button',{name:'Playbook',exact:true}).click();
 await page.locator('.playbook-card').first().waitFor();
 const books=await(await page.request.get(url+'/api/playbooks')).json();
 const used=books.items.find(x=>x.id===report.runs[1].selected_id);
 assert.ok(used.hit_count>=2);assert.ok(used.reuse_count>=2);assert.ok(used.success_count>=2);
 report.playbook=used;
 await page.screenshot({path:path.join(out,'playbook-counts.png'),fullPage:true});
 assert.equal(errors.length,0);
 report.passed=true;
}catch(error){report.passed=false;report.failure=String(error.stack||error);process.exitCode=1;await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});}
finally{await page.request.put(url+'/api/memory',{data:{enabled:false}});report.finished_at=new Date().toISOString();await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({out,...report}));}
