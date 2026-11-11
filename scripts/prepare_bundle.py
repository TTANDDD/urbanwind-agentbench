"""Build a fresh, hash-checked public/trusted bundle from the released paper subset."""
import argparse, hashlib, io, json, shutil, sys, tarfile
from pathlib import Path, PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'harness/windpilot'))
from urbanwind_harness.open_contracts import compile_public_contract
from urbanwind_harness.acceptance import public_contract
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def encode(obj):return (json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--task',action='append')
    a=parser.parse_args();status=json.loads((ROOT/'benchmark/task_status.json').read_text())
    selected=a.task or status['paper_evaluation_tasks']
    if not set(selected)<=set(status['paper_evaluation_tasks']):parser.error('Select from the 30 paper tasks')
    if len(set(selected))!=len(selected):parser.error('Duplicate task')
    a.output.mkdir(parents=True,exist_ok=False);entries=[]
    for name in selected:
        t=ROOT/'benchmark'/name;task=json.loads((t/(name+'.json')).read_text(encoding='utf-8'))
        public=a.output/'public'/name;trusted=a.output/'trusted'/name
        public.mkdir(parents=True);trusted.mkdir(parents=True)
        for fn,src in [('eval.py',t/'eval.py'),('contract_evaluator.py',t/'init_file/contract_evaluator.py')]:shutil.copy2(src,trusted/fn)
        refs=[ROOT/'benchmark'/x['local_path'] for c in task['evaluator'].get('postconfig',[]) for x in c.get('parameters',{}).get('files',[]) if 'ground_truth/' in x['local_path']]
        if len(refs)>1:raise ValueError('Ambiguous reference: '+name)
        if refs:shutil.copy2(refs[0],trusted/'reference.json')
        with tarfile.open(t/'init_file/init_file.tar.gz','r:gz') as old:
            files={}
            for m in old:
                path=PurePosixPath(m.name)
                if path.is_absolute() or '..' in path.parts or m.issym() or m.islnk():raise ValueError('Unsafe archive')
                if m.isfile():files[m.name.removeprefix('./')]=old.extractfile(m).read()
        report=json.loads(files['reporting_contract.json']);plan=json.loads(files['case_plan.json'])
        assets={n:json.loads(b) for n,b in files.items() if n.startswith('local_assets/') and n.endswith('.json')}
        compiled=compile_public_contract((trusted/'eval.py').read_text(encoding='utf-8')+'\n'+(trusted/'contract_evaluator.py').read_text(encoding='utf-8'),report,assets,plan)
        acceptance=public_contract(name,report)
        if name in ('task-061','task-062','task-063','task-064','task-068','task-069','task-071','task-072'):acceptance['inlet_probe_xy_m']=[-4.0,0.0625]
        run_spec={'root_type':'object','root_fields_read':['task_id','runs'],'case_containers':['runs'],
            'objects':{'':{'fields_read':['task_id','runs'],'container_type':'object'},'runs':{'fields_read':[],'container_type':'list'},'runs/*':{'fields_read':acceptance['run_required_fields'],'container_type':'object'}},
            'required_fields':acceptance['run_required_fields'],'types':{'runs':'list','runs/*/software':'list of {name,...}','runs/*/commands':'list','runs/*/evidence':'list of {path,sha256}'}}
        compiled['submission_format']['documents'][acceptance['run_manifest']]=run_spec
        compiled['task_public_guide']['serialization'][acceptance['run_manifest']]=run_spec
        additions={k+'.json':encode(v) for k,v in compiled.items()}
        additions['acceptance_contract.json']=encode(acceptance)
        additions['open_environment_guide.md']=(ROOT/'harness/windpilot/ENVIRONMENT_GUIDE.md').read_bytes()
        for helper in ('open_contracts.py','provenance_export.py','full_tools.py'):
            additions[helper]=(ROOT/'harness/windpilot/urbanwind_harness'/helper).read_bytes()
        files.update(additions)
        with tarfile.open(public/'init_file.tar.gz','w:gz') as archive:
            for path,data in sorted(files.items()):
                m=tarfile.TarInfo(path);m.mode=0o644;m.size=len(data);m.mtime=0;archive.addfile(m,io.BytesIO(data))
        (public/'task.json').write_bytes(encode({'task_id':task['benchmark_metadata']['task_id'],'instruction':task['instruction']}))
        entries.append({'directory':name,'task_id':task['benchmark_metadata']['task_id'],
            'task_mode':task['benchmark_metadata']['task_mode'],'required_solver_runs':task['benchmark_metadata']['required_solver_runs'],
            'public_archive_sha256':digest(public/'init_file.tar.gz'),'instruction_sha256':digest(public/'task.json'),
            'grader_sha256':digest(trusted/'eval.py'),'precheck_sha256':digest(trusted/'contract_evaluator.py'),
            'reference_sha256':digest(trusted/'reference.json') if refs else None,
            'original_public_archive_sha256':digest(t/'init_file/init_file.tar.gz')})
    (a.output/'manifest.json').write_bytes(encode({'schema_version':'public-research-snapshot-20261111','task_count':len(entries),'tasks':entries,
        'scope':'Regenerated public bundle; original physics/scorer/reference bytes preserved. Not the exact historical run bundle.',
        'agent_access':'Mount only public task input; trusted scorers/references remain outside the agent environment.'}))
    print(json.dumps({'output':str(a.output),'task_count':len(entries),'model_calls':0,'solver_calls':0}))
if __name__=='__main__':main()
