import fs from 'node:fs'
const source=fs.readFileSync(new URL('../frontend/src/main.tsx',import.meta.url),'utf8')
const checks=[
 ['muted condition',source.includes("condition:state.muted?'muted':state.memory_enabled?'memory':'swarm'")],
 ['memory+muted guard',source.includes('记忆复用与禁言不能同时注入')],
 ['reset independent busy',source.includes("disabled={busy==='reset'}")],
 ['chat abort epoch',source.includes('chatAbort')&&source.includes('chatEpoch')],
 ['unknown usage runs',source.includes('unknown_usage_runs')],
 ['scoreboard interval refresh',source.includes('window.setInterval(load,3000)')],
 ['scoreboard stale response guard',source.includes('alive&&seq===dataSeq.current')&&source.includes('controller.abort()')],
 ['scoreboard state refresh',source.includes('stateKey')&&source.includes('selectedVersion')&&source.includes('},[tab,stateKey,selectedVersion])')],
 ['scoreboard version query',source.includes('version=${encodeURIComponent(selectedVersion)}')&&source.includes('验收版本')],
 ['event generation filter',fs.existsSync(new URL('../frontend/src/OfficeScene.tsx',import.meta.url))]
]
for(const [name,ok] of checks){if(!ok)throw new Error(`frontend audit failed: ${name}`);console.log(`ok ${name}`)}
