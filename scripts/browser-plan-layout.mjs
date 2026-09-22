import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const base=process.env.S9_LAYOUT_URL||'http://127.0.0.1:9024';
const out=new URL('../artifacts/audit-remediation/plan-layout/',import.meta.url);
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage();
const report={origin:'real_live_plan_read_only',base,checks:[]};
try {
  report.identity=await (await page.request.get(base+'/api/health')).json();
  await page.goto(base,{waitUntil:'domcontentloaded'});
  for(const width of [1440,1280]) {
    await page.setViewportSize({width,height:800});
    await page.locator('.plan pre').waitFor({timeout:60000});
    const metrics=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,plan:document.querySelector('.plan pre')?.textContent,planBox:document.querySelector('.plan')?.getBoundingClientRect().toJSON()}));
    const state=await (await page.request.get(base+'/api/state')).json();
    report.checks.push({...metrics,run_id:state.incident?.id});
    await page.screenshot({path:new URL(`plan-${width}.png`,out).pathname,fullPage:true});
    assert.ok(metrics.plan?.length>20,'requires a real long plan');
    assert.equal(metrics.scrollWidth,width,'document must fit viewport with actual plan');
    assert.ok(metrics.planBox.right<=width,'plan must stay within viewport');
  }
  report.passed=true;
} catch(error) {report.passed=false;report.error=String(error);process.exitCode=1;}
finally {await fs.writeFile(new URL('report.json',out),JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report));}
