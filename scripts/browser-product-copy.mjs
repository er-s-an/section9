// Live read-only UI verification. No model calls or write requests are allowed.
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const out=path.resolve('artifacts/product-ux/20260922');await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const report={kind:'live_read_only_ui_no_inference',checks:[],errors:[],requests:[]};
const base='http://127.0.0.1:9019';
const check=async(name,fn)=>{await fn();report.checks.push({name,passed:true})};
const jargon=/\b(?:GEN|REV|SSE|PRIMARY SCOPE|PAIR SCOPE|cost-analysis|quality_mismatch|tool_stalled|spec_hash|event_id|run_id|prompt_version|heldout_semantic_policy|heldout_outside_return_window|terminal_tool_stops|acceptance_contract_complete|semantic_policy|no_stalled_requests)\b|\b(?:pair|run)_[a-z0-9]{10,}/;
page.on('pageerror',e=>report.errors.push(e.message));
await page.route('**/api/**',async route=>{assert.equal(route.request().method(),'GET','read-only audit cannot write');await route.continue()});
const get=async endpoint=>{const r=await page.request.get(base+endpoint);assert(r.ok());return r.json()};
const capture=async(name)=>{const text=await page.locator('body').innerText();await fs.writeFile(path.join(out,`${name}.txt`),text);assert(!jargon.test(text),`engineering markers in ${name}: ${text.match(jargon)?.[0]}`);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`horizontal overflow ${name}`);await page.screenshot({path:path.join(out,`${name}.png`),fullPage:true})};
try{
 report.identity=(await get('/api/health')).identity;
 const pair=(await get('/api/pairs/active')).active_pair;assert(pair);
 await page.goto(base);await page.getByRole('heading',{name:'实时处理控制',exact:true}).waitFor();
 await page.waitForFunction(()=>[...document.querySelectorAll('.s9-office-agent__preload')].length&&[...document.querySelectorAll('.s9-office-agent__preload')].every(i=>i.complete&&i.naturalWidth>0),{},{timeout:45000});
 await check('primary scope, real images, copy, 1440 layout',async()=>{assert(await page.getByRole('heading',{name:'对照实验控制',exact:true}).count());assert(await page.getByRole('button',{name:'正常通信',exact:true}).count());assert.equal(await page.locator('.s9-office-agent__fallback').first().isVisible(),false);await capture('product-after-1440')});
 await check('team disclosure and action identity',async()=>{await page.locator('.team-details>summary').click();assert(await page.getByRole('button',{name:'邀请成本专家',exact:true}).isVisible());assert(await page.getByRole('button',{name:'暂停修复员',exact:true}).isVisible());await page.locator('.team-details>summary').click()});
 await check('office member details',async()=>{await page.locator('[data-agent-id="sentry"]').click();assert(await page.locator('[data-testid="agent-inspector"]').isVisible());assert(!(await page.locator('[data-testid="agent-inspector"] details').getAttribute('open')));await page.getByRole('button',{name:'关闭角色详情'}).click()});
 await page.setViewportSize({width:1280,height:800});await capture('product-after-1280');report.checks.push({name:'1280 primary layout',passed:true});
 await page.getByRole('button',{name:'处理记录',exact:true}).click();await page.locator('.data-list article').first().waitFor();await capture('history-after');
 await check('history opens business details before raw records',async()=>{await page.getByRole('button',{name:'查看处理详情'}).first().click();await page.locator('.detail-drawer').waitFor();await capture('history-detail-after');await page.locator('.detail-drawer').getByRole('button',{name:'关闭',exact:true}).click()});
 await page.getByRole('button',{name:'处置经验',exact:true}).click();await page.locator('.playbook-card').first().waitFor();await capture('experience-after');report.checks.push({name:'experience copy',passed:true});
 await page.getByRole('button',{name:'验收评分',exact:true}).click();await page.locator('.matrix-cell').first().waitFor();assert(await page.getByText('待测',{exact:true}).count());await capture('score-after');report.checks.push({name:'scores retain untested state',passed:true});
 for(const arm of ['swarm','baseline']){
 await page.goto(`${base}/showcase/${arm}?pair_id=${pair.pair_id}`);await page.locator('.sc-stage').first().waitFor();await page.waitForFunction(()=>!document.querySelector('.sc-empty'));
 await check(`${arm} bound identity and product copy`,async()=>{assert.equal(await page.getByRole('combobox',{name:'选择历史对照记录'}).inputValue(),pair.pair_id);assert(await page.getByRole('heading',{name:new RegExp(arm==='swarm'?'协作团队':'独立助手')}).count());await capture(`showcase-${arm}-after`)});
 await page.locator('.sc-stage').first().click();await page.getByRole('dialog').waitFor();assert.equal(await page.locator('.sc-raw').getAttribute('open'),null);await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),0);
 assert(await page.getByRole('link',{name:'导出证据 ↓'}).count());
 }
 await page.goto(`${base}/showcase/baseline?pair_id=pair_ff221776dbe043cf`);await page.locator('.sc-stage').first().waitFor();assert(await page.locator('.sc-identity h1').innerText().then(t=>t.includes('失败')));await capture('showcase-failed-after');report.checks.push({name:'retained historical failure remains visible',passed:true});
 assert.deepEqual(report.errors,[]);report.passed=true;
}catch(e){report.passed=false;report.error=String(e.stack||e);process.exitCode=1;await page.screenshot({path:path.join(out,'product-copy-failure.png'),fullPage:true}).catch(()=>{})}
finally{await fs.writeFile(path.join(out,'product-copy-qa.json'),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,error:report.error,out}))}
