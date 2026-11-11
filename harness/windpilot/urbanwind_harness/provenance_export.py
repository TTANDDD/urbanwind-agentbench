"""Build run_manifest from immutable foreground execution receipts.

Case bindings are explicit. Ambiguous/background solver receipts are rejected;
we never turn a shell launch exit code or model assertion into solver success.
The CLI writes a new candidate; the actor inspects it before adopting it.
"""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,math,re

def load(p):return json.loads(p.read_text(encoding='utf-8'))
def contained(root,name):
    p=(root/name).resolve()
    if Path(name).is_absolute() or not p.is_relative_to(root.resolve()) or not p.is_file():raise ValueError('invalid_evidence_path:'+name)
    return p
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def iso(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('missing_recorded_timestamp')
    return datetime.fromtimestamp(value,timezone.utc).isoformat()

def export(root,bindings):
    root=Path(root).resolve();report=load(root/'reporting_contract.json');runtime=load(root/'harness_trace/runtime.json');runs=[]
    ids=[v.get('case_id') for v in bindings]
    if len(set(ids))!=len(ids) or set(ids)!=set(report['expected_case_ids']):raise ValueError('binding_case_coverage')
    for binding in bindings:
        case=binding['case_id'];records=[]
        for step in binding.get('execution_records',[]):
            if not re.fullmatch(r'\d{3}',str(step)):raise ValueError('invalid_trace_step')
            receipt=load(root/'harness_trace'/str(step)/'execution.json')
            if receipt.get('timed_out') or receipt.get('cancelled'):raise ValueError('incomplete_execution_receipt:'+str(step))
            if receipt.get('annotations',{}).get('case') not in (None,case):raise ValueError('case_binding_conflicts_with_receipt')
            if receipt.get('started_at_unix') is None or receipt.get('ended_at_unix') is None:raise ValueError('timestamps_not_recorded:'+str(step))
            if receipt['ended_at_unix']<receipt['started_at_unix']:raise ValueError('reversed_execution_time')
            records.append((str(step),receipt))
        if not records:raise ValueError('missing_execution_receipts')
        real_solver=case in report.get('expected_solver_case_ids',[])
        selected=next((r for step,r in records if step==binding.get('solver_record')),None)
        software=[]
        if real_solver:
            if not selected or selected.get('exit_code')!=0:raise ValueError('missing_successful_solver_receipt')
            output=selected.get('stdout','')+'\n'+selected.get('stderr','')
            if not ('OpenFOAM' in output and re.search(r'^Time = ',output,re.M) and re.search(r'^End\s*$',output,re.M)) or 'FOAM FATAL' in output:
                raise ValueError('foreground_solver_completion_not_in_immutable_receipt')
            executable=next((r for r in runtime.get('software',[]) if r.get('name') in ('simpleFoam','pimpleFoam') and r['name'] in selected.get('command','')),None)
            if not executable:raise ValueError('solver_executable_not_bound_to_runtime_receipt')
            software=[{'name':'openfoam','executable':executable,'source':'harness_trace/runtime.json'}]
        else:software=[{'name':'recorded_preflight','source_records':[step for step,_ in records]}]
        evidence=[]
        for name in binding.get('evidence_paths',[]):
            p=contained(root,name);evidence.append({'path':name,'sha256':digest(p)})
        if not evidence:raise ValueError('missing_evidence_paths')
        final=selected or records[-1][1]
        runs.append({'case_id':case,'commands':[r['command'] for _,r in records],'software':software,
            'started_at':iso(min(r['started_at_unix'] for _,r in records)),'ended_at':iso(max(r['ended_at_unix'] for _,r in records)),
            'exit_code':final['exit_code'],'execution_role':'real_solver_run' if real_solver else 'preflight_only',
            'evidence':evidence,'execution_receipts':['harness_trace/'+step+'/execution.json' for step,_ in records]})
    return {'task_id':report['task_id'],'runs':runs,'source':'Explicit case bindings plus immutable execution receipts; no fabricated run metadata.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('bindings');p.add_argument('--root',default='.');p.add_argument('--output',default='run_manifest.candidate.json');a=p.parse_args()
    root=Path(a.root).resolve();out=(root/a.output).resolve()
    if not out.is_relative_to(root) or out.exists():raise ValueError('candidate_output_must_be_new_and_inside_workspace')
    value=export(root,load(contained(root,a.bindings)))
    out.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'candidate':str(out),'cases':len(value['runs']),'review_before_adopting':True}))
if __name__=='__main__':main()
