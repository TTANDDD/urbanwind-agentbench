import argparse
import fcntl
import json
import os
import time
from pathlib import Path

from .common import digest, load, now, write
from .runner import CONDITIONS, run_task
from .report import summarize


def process_matches(pid, identity):
    try:
        stat=Path(f'/proc/{pid}/stat').read_text()
        return stat[stat.rfind(')')+2:].split()[19]==identity
    except (OSError,IndexError):
        return False


def main():
    p=argparse.ArgumentParser()
    for name in ('bundle','protocol','run-root','key-path','runtime'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--relay-url')
    p.add_argument('--cpu-set',default='192-207')
    p.add_argument('--conditions',nargs='+',choices=('baseline',),default=['baseline'])
    args=p.parse_args()
    protocol=load(args.protocol)
    protocol_hash=digest(args.protocol)
    if digest(args.bundle/'manifest.json')!=protocol['bundle_manifest_sha256']:
        raise ValueError('protocol_bundle_hash_mismatch')
    if args.conditions != ['baseline'] or protocol['conditions'] != ['baseline']:
        raise ValueError('condition_not_registered')
    code_root=Path(__file__).parent.parent
    for name,expected in protocol['code_hashes'].items():
        if digest(code_root/name)!=expected:
            raise ValueError('protocol_code_hash_mismatch:'+name)
    args.run_root.mkdir(parents=True,exist_ok=True)
    with (args.run_root/'controller.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        write(args.run_root/'dispatch.json',{'pid':os.getpid(),'started_at':now(),
            'protocol_sha256':digest(args.protocol),'conditions':args.conditions,'task_count':len(protocol['budgets'])})
        results=[]
        gate_path=args.run_root/'resource_gate.json'
        if gate_path.is_file():
            gate=load(gate_path)
            while process_matches(gate['pid'],gate['process_start_ticks']):
                write(args.run_root/'status.json',{'state':'waiting_for_physical_core_sibling',
                    'protected_reference_pid':gate['pid'],'completed':0,'at':now(),
                    'reason':'Other reference ranks occupy the same physical cores; leave them unchanged.'})
                time.sleep(30)
        for entry in protocol['budgets']:
            for condition in args.conditions:
                destination=args.run_root/(entry['task']+'--'+condition)
                attempts=[destination]+sorted(args.run_root.glob(destination.name+'--retry-*'))
                already_evaluated=False
                for attempt in attempts:
                    if not (attempt/'result.json').exists():
                        continue
                    previous=load(attempt/'result.json')
                    if load(attempt/'config.json').get('protocol_sha256')!=protocol_hash:
                        raise ValueError('existing_attempt_protocol_mismatch:'+attempt.name)
                    if previous['status']=='evaluated':
                        results.append(previous)
                        already_evaluated=True
                        break
                if already_evaluated:
                    continue
                if destination.exists():
                    # Preserve an interrupted attempt, never overwrite traces.
                    destination=args.run_root/(entry['task']+'--'+condition+'--retry-'+str(int(time.time())))
                write(args.run_root/'status.json',{'state':'running','task':entry['task'],'condition':condition,
                    'current_run':str(destination.resolve()),'completed':sum(r['status']=='evaluated' for r in results),'at':now()})
                try:
                    result=run_task(args.bundle,entry['task'],destination,args.key_path,args.runtime,
                        condition=condition,active_seconds=entry['active_seconds'],cpu_set=args.cpu_set,
                        max_steps=protocol['max_steps'],history=protocol['history_exchanges'],model=protocol['model'],
                        api_timeout=protocol['api_timeout_seconds'],api_retries=protocol['api_attempts'],
                        relay_url=args.relay_url,protocol_sha256=protocol_hash,
                        purpose='development')
                except Exception as exc:
                    write(args.run_root/'status.json',{'state':'infra_stopped','error':type(exc).__name__+':'+str(exc),
                        'task':entry['task'],'condition':condition,'at':now()})
                    raise
                results.append(result)
                write(args.run_root/'results.json',results)
                summarize(args.run_root,protocol)
                if result['status']=='infra_incomplete':
                    write(args.run_root/'status.json',{'state':'infra_stopped','task':entry['task'],
                        'condition':condition,'reason':result['termination_detail'],'completed':sum(r['status']=='evaluated' for r in results),'at':now()})
                    return
        write(args.run_root/'status.json',{'state':'complete','completed':sum(r['status']=='evaluated' for r in results),'at':now()})
        summarize(args.run_root,protocol)
        print(json.dumps({'state':'complete','completed':sum(r['status']=='evaluated' for r in results)}))


if __name__=='__main__':
    main()
