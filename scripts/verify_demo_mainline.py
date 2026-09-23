#!/usr/bin/env python3
"""Opt-in real service verification. Calls paid/configured models through the UI API.

Uses read-only business questions in isolated demo data. No automatic retry of
business operations or failed model attempts. Reports preserve all attempts.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Explicitly authorize real model/native-session requests')
    parser.add_argument('--base',default='http://127.0.0.1:9160')
    parser.add_argument('--task',help='Continue verification of an existing business task; do not repeat business work')
    parser.add_argument('--question',default='订单 1001 已经签收 45 天，而且使用过了。请检查退款资格并说明依据，不要提交退款。')
    parser.add_argument('--takeover',action='store_true',help='Pause an investigator after handoff, then resume with another member')
    args=parser.parse_args()
    if not args.live:
        parser.error('--live is required; this verification calls real models')
    if urlsplit(args.base).hostname not in ('127.0.0.1','localhost'):
        parser.error('Only the local demo operator endpoint is supported')
    report={'started_at':datetime.now(timezone.utc).isoformat(),'checks':{},'failures':[]}
    root=Path(__file__).resolve().parents[1]/'.runtime/demo-validation'
    root.mkdir(parents=True,exist_ok=True)
    target=root/(time.strftime('%Y%m%d-%H%M%S')+'.json')
    with httpx.Client(trust_env=False,timeout=30,headers={'Origin':args.base}) as client:
        base=args.base+'/api/v1/demo'
        def get():
            response=client.get(base+'/tasks/'+task_id);response.raise_for_status();return response.json()
        def post(path,body):
            response=client.post(base+path,json=body,headers={'Idempotency-Key':'verify-'+uuid.uuid4().hex})
            response.raise_for_status();return response.json()
        try:
            task_id=args.task or post('/tasks',{'message':args.question})['id'];report['task_id']=task_id
            started=time.monotonic(); saw_live=False
            while time.monotonic()-started<200:
                job=get();saw_live |= job['state']=='running' and bool(job['events'])
                if job['state'] in ('failed','interrupted'):raise RuntimeError(job.get('error','business interrupted'))
                if job['observation_state']=='verified':break
                time.sleep(.75)
            else:raise RuntimeError('Application trace did not become ready')
            report['checks']['events_before_completion']=saw_live if not args.task else 'not_rechecked_existing_task'
            report['checks']['application_trace_verified']=True
            paused=False
            for mode in ('swarm','single'):
                post('/tasks/'+task_id+'/investigate',{'mode':mode})
                start=time.monotonic()
                while time.monotonic()-start<480:
                    job=get()
                    if mode=='swarm' and args.takeover and not paused and any(e['kind']=='handoff' and e['role']=='investigator' for e in (job.get('collaboration') or {}).get('events',[])):
                        post('/tasks/'+task_id+'/members/investigator/pause',{});paused=True
                    if job['analysis_state']=='failed':
                        report['failures'].append({'mode':mode,'message':job.get('analysis_error')})
                        if paused and mode=='swarm' and not report.get('resumed'):
                            post('/tasks/'+task_id+'/investigate',{'mode':'swarm'});report['resumed']=True
                        else:raise RuntimeError(job.get('analysis_error','analysis failed'))
                    if job['analysis_state']=='complete':break
                    time.sleep(1)
                else:raise RuntimeError('Investigation verification deadline exceeded')
                report['checks'][mode+'_complete']=True
            job=get()
            report['source_commit']=job['source_commit'];report['trace_id']=job.get('trace_id')
            report['checks']['same_evidence']=job['comparison']['single']['evidence_sha256']==job['comparison']['swarm']['evidence_sha256']
            report['checks']['same_budget']=job['comparison']['single']['token_limit']==job['comparison']['swarm']['token_limit']
            report['native_session_id']=job['collaboration']['session_id']
            report['checks']['native_takeover']=any(e['kind']=='takeover' for e in job['collaboration']['events']) if args.takeover else 'not_requested'
            report['arms']={mode:{'run_id':run['run']['id'],'run_state':run['run']['state'],
                'elapsed_s':job['comparison'][mode]['elapsed_s'],
                'task_states':[{'role':t['role'],'state':t['state'],'attempts':len(t['attempt_history'])+1} for t in run['task_graph']['tasks']],
                'known_tokens':sum(u['actual_tokens'] or 0 for u in run['model_usage']),
                'unknown_usage_requests':sum(u['actual_tokens'] is None for u in run['model_usage']),
                'summary':next(t['result']['summary'] for t in run['task_graph']['tasks'] if t['role']=='synthesizer')}
                for mode,run in job['investigations'].items()}
            failed_checks = [name for name, value in report['checks'].items() if value is False]
            if failed_checks:
                raise RuntimeError('Verification checks failed: ' + ', '.join(failed_checks))
            report['result']='passed'
        except Exception as exc:
            report['result']='failed';report['error']=str(exc)[:600]
        finally:
            report['finished_at']=datetime.now(timezone.utc).isoformat()
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'result':report.get('result'),'report':str(target),'checks':report['checks']},ensure_ascii=False))
    if report['result']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
