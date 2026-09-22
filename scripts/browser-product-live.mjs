// Real local UI checks; no model requests, fixture routes, or Pair starts.
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const out=path.resolve('artifacts/product-stage1',new Date().toISOString().replaceAll(':','-'));
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const report={kind:'real_local_browser_no_inference',checks:[],errors:[]};
page.on('pageerror',e=>report.errors.push(String(e)));
const base='http://127.0.0.1:9019';
const get=async p=>{const r=await page.request.get(base+p);assert(r.ok());return r.json()};
const check=async(name,fn)=>{await fn();report.checks.push({name,passed:true})};
async function waitFor(test){for(let i=0;i<50;i++){if(await test())return;await page.waitForTimeout(150)}throw Error('state transition timeout')}
try{
 report.identity=(await get('/api/health')).identity;
 const pair=(await get('/api/pairs/active')).active_pair;
 const primary=await get('/api/state');
 report.pair_id=pair?.pair_id;
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.getByText('实时处理控制',{exact:true}).waitFor();
 await check('primary and Pair scopes visible',async()=>{assert(await page.getByText('对照实验控制',{exact:true}).count());assert(await page.getByText('固定自动处理 · 独立运行',{exact:true}).count())});
 await check('primary L0 does not mutate Pair spec',async()=>{await page.getByRole('button',{name:'仅建议',exact:true}).click();await waitFor(async()=>(await get('/api/state')).autonomy==='L0'); if(pair)assert.deepEqual((await get('/api/pairs/'+pair.pair_id)).immutable_spec,pair.immutable_spec)});
 await page.screenshot({path:path.join(out,'primary-scope-L0.png'),fullPage:true});
 await page.getByRole('button',{name:({L0:'仅建议',L1:'确认后执行',L2:'自动处理'})[primary.autonomy],exact:true}).click();
 await waitFor(async()=>(await get('/api/state')).autonomy===primary.autonomy);
 await check('primary reset preserves Pair identity and state',async()=>{const before=await get('/api/state');await page.getByRole('button',{name:'重新开始本轮',exact:true}).click();await waitFor(async()=>(await get('/api/state')).generation!==before.generation);if(pair){const after=await get('/api/pairs/'+pair.pair_id);assert.equal(after.status,pair.status);assert.equal(after.swarm_run_id,pair.swarm_run_id)}});
 await page.setViewportSize({width:1280,height:800});
 await check('1280 notebook has no horizontal page overflow',async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)));
 await page.screenshot({path:path.join(out,'primary-1280.png'),fullPage:true});
 if(pair){
  await page.goto(base+'/showcase/swarm?pair_id='+pair.pair_id,{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:/协作团队/}).waitFor();
  await waitFor(async()=>await page.getByRole('combobox',{name:'选择历史对照记录'}).inputValue()===pair.pair_id);
  await check('complete evidence download is visible',async()=>{assert(await page.getByRole('link',{name:'导出证据 ↓'}).count())});
  const zip=await page.request.get(base+'/api/pairs/'+pair.pair_id+'/export.zip');assert(zip.ok());await fs.writeFile(path.join(out,'pair-evidence.zip'),await zip.body());
  await page.screenshot({path:path.join(out,'showcase-1280.png'),fullPage:true});
 }
 assert.equal(report.errors.length,0);
 report.passed=true;
}catch(e){report.passed=false;report.error=String(e.stack||e);process.exitCode=1;await page.screenshot({path:path.join(out,'failure.png'),fullPage:true})}
finally{await fs.writeFile(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({output:out,passed:report.passed,error:report.error}))}
