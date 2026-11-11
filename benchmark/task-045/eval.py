#!/usr/bin/env python3
"""Public three-level mesh constraints plus isolated actual tool replay, no GT identity matching."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,math,os,re,shutil,subprocess,tempfile
import numpy as np
from contract_evaluator import evaluate as precheck
from task045_mesh_inputs import contract
from task045_mesh_geometry import inspect_mesh,inspect_study,cell_boxes
TASK='urbanwind-task-045';PRIMARY='refinement_ratio_consistency';METRICS=[PRIMARY,'evidence_provenance_fraction','hard_gate_pass_fraction']
DICTS=['blockMeshDict','controlDict','fvSchemes','fvSolution','decomposeParDict']
RAW=['constant/polyMesh/'+x for x in ['points','faces','owner','neighbour','boundary']]+['system/'+x for x in DICTS]+['log.'+x for x in ['blockMesh','checkMesh','decomposePar','checkMesh.parallel']]+['commands.json']
def load(p):
    p=Path(p)
    if p.stat().st_size>16*1024**2:raise ValueError('oversized JSON')
    return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()
def confined(root,raw):
    p=Path(raw)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('unsafe relative path')
    out=root/p
    if any(x.is_symlink() for x in [out,*out.parents] if x==root or root in x.parents):raise ValueError('symlink evidence')
    if root not in out.resolve().parents:raise ValueError('escaped root')
    return out
def utc(raw):
    t=datetime.fromisoformat(raw)
    if t.tzinfo is None:raise ValueError('timestamp timezone required')
    return t
def signature(case):
    raw=cell_boxes(case);boxes=np.round(np.c_[raw[8],raw[9]],9);order=np.lexsort(tuple(boxes[:,i] for i in reversed(range(6))))
    return hashlib.sha256(boxes[order].astype('<f8').tobytes()).hexdigest()
def safe_dictionaries(case,stage,c):
    for name in DICTS:
        text=(case/'system'/name).read_text(encoding='utf-8')
        if len(text)>2_000_000 or re.search(r'#|\b(codeStream|libs|functions|dynamicCode|coded)\b|\$',text):raise ValueError('code-bearing or oversized dictionary')
    counts=re.findall(r'hex\s*\([^)]*\)\s*\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\)',(case/'system/blockMeshDict').read_text())
    if not counts or sum(math.prod(map(int,x)) for x in counts)>c['stages'][stage]['cells_max']:raise ValueError('input cell budget')
    if not re.search(r'numberOfSubdomains\s+16\s*;',(case/'system/decomposeParDict').read_text()):raise ValueError('16 partitions required')
def replay(case,stage,c):
    if os.environ.get('WM_PROJECT_VERSION')!='v2412':raise RuntimeError('pinned OpenCFD v2412 unavailable')
    tools={n:shutil.which(n) for n in ['blockMesh','checkMesh','decomposePar','mpiexec']}
    if not all(tools.values()):raise RuntimeError('mesh tools unavailable')
    prefix=Path(tools['blockMesh']).resolve().parent
    if any(Path(x).resolve().parent!=prefix for x in tools.values()):raise RuntimeError('mesh tools and packaged MPI must share pinned prefix')
    safe_dictionaries(case,stage,c)
    with tempfile.TemporaryDirectory(prefix='urbanwind-task045-replay-') as raw:
        fresh=Path(raw)/'case';(fresh/'system').mkdir(parents=True)
        for name in DICTS:shutil.copy2(case/'system'/name,fresh/'system'/name)
        chain=[('blockMesh',[tools['blockMesh']]),('checkMesh',[tools['checkMesh'],'-constant','-noZero','-allGeometry','-allTopology']),('decomposePar',[tools['decomposePar'],'-no-fields']),('checkMesh.parallel',[tools['mpiexec'],'-n','16',tools['checkMesh'],'-parallel','-constant','-noZero','-allGeometry','-allTopology'])]
        for name,args in chain:
            with (fresh/('log.'+name)).open('w') as log:r=subprocess.run(args,cwd=fresh,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            if r.returncode:raise ValueError('replay '+name+' failed')
        result=inspect_mesh(fresh,c,stage)
        if result['status']!='PASS':raise ValueError('replay public gates rejected')
        if signature(fresh)!=signature(case):raise ValueError('submitted mesh differs from its own dictionary replay')
        result['reexecution_verified']=True
        return result
def evaluate(root):
    root=Path(root).resolve();base=precheck(root,TASK);errors=list(base['errors']);diagnostics={};c=contract()
    try:
        spec=load(root/'task_spec.json');reporting=load(root/'reporting_contract.json')
        if spec['required_cases']!=3 or spec['required_solver_runs']!=0 or reporting['expected_case_ids']!=c['case_ids'] or reporting.get('expected_solver_case_ids')!=[]:raise ValueError('three tool cases, zero CFD contract changed')
        if reporting.get('three_level_mesh_geometry_contract')!=c:raise ValueError('public scientific contract changed')
        for name in ['design.json','mesh_metrics.json','run_manifest.json','metrics.json','evidence_manifest.json','decision.md']:
            if not (root/name).is_file():raise ValueError('missing artifact '+name)
        if load(root/'design.json').get('geometry')!=c['geometry']:raise ValueError('design geometry differs from public geometry')
        runs=load(root/'run_manifest.json')['runs']
        if len(runs)!=3 or [x.get('case_id') for x in runs]!=c['case_ids']:raise ValueError('ordered coarse/medium/fine case IDs required')
        indexed={}
        for row in load(root/'evidence_manifest.json')['files']:
            raw=row['path'];p=confined(root,raw)
            if raw in indexed:raise ValueError('duplicate evidence path')
            if not p.is_file() or p.stat().st_size>128*1024**2 or sha(p)!=row['sha256']:raise ValueError('evidence missing/oversized/hash mismatch:'+raw)
            indexed[raw]=row['sha256']
        directories={};linked_union={};all_commands=[]
        for i,(run,stage) in enumerate(zip(runs,['coarse','medium','fine'])):
            if run.get('execution_role')!='provided_data_or_tool_execution' or run.get('exit_code')!=0 or run.get('solver_runs')!=0:raise ValueError('tool role/solver count mismatch')
            if not any(isinstance(x,dict) and x.get('name','').lower()=='openfoam' and x.get('version')=='v2412' for x in run['software']):raise ValueError('pinned software missing')
            own={stage:run.get('tool_case_directory','mesh_study/'+stage)}
            if i==0:own={'qualification':run.get('qualification_case_directory','mesh_study/qualification'),**own}
            linked={x['path']:x['sha256'] for x in run['evidence']}
            if len(linked)!=len(run['evidence']):raise ValueError('duplicate run evidence')
            expected={key:val for key,val in indexed.items() if any(key.startswith(relative.rstrip('/')+'/') for relative in own.values())}
            if linked!=expected or set(linked)&set(linked_union):raise ValueError('case evidence ownership/hash mismatch')
            linked_union.update(linked);chronological=[];recorded=[]
            for substage,relative in own.items():
                case=confined(root,relative)
                if relative in directories.values() or any(case==confined(root,p) or case in confined(root,p).parents or confined(root,p) in case.parents for p in directories.values()):raise ValueError('overlapping or duplicate stage directories')
                directories[substage]=relative
                for rel in RAW:
                    if (Path(relative)/rel).as_posix() not in indexed:raise ValueError('unregistered raw evidence '+rel)
                if (case/'three_level_mesh_contract.json').is_file() and load(case/'three_level_mesh_contract.json')!=c:raise ValueError('case altered gates')
                records=load(case/'commands.json')
                if len(records)!=4 or [r['name'] for r in records]!=c['tool_sequence']:raise ValueError('real tool sequence missing')
                for row in records:
                    start=utc(row['start_utc']);end=utc(row['end_utc'])
                    if row['exit_code']!=0 or not isinstance(row['argv'],list) or end<start or (chronological and start<chronological[-1]):raise ValueError('tool failure or chronology')
                    chronological.append(end)
                recorded+=records;safe_dictionaries(case,substage,c);result=inspect_mesh(case,c,substage);diagnostics[substage]=result
                if result['status']!='PASS':raise ValueError(substage+' raw gates rejected:'+','.join(k for k,v in result['gates'].items() if not v))
            if run['commands']!=recorded or utc(run['started_at'])>utc(recorded[0]['start_utc']) or utc(run['ended_at'])<utc(recorded[-1]['end_utc']):raise ValueError('run chronology/command linkage invalid')
            all_commands+=recorded
        if linked_union!=indexed:raise ValueError('unowned evidence')
        comparison=inspect_study(diagnostics,c);diagnostics['study']=comparison
        if comparison['status']!='PASS':raise ValueError('study gates rejected:'+','.join(k for k,v in comparison['gates'].items() if not v))
        reported=load(root/'metrics.json');primary=load(root/'mesh_metrics.json')['primary_metric'];value=comparison[PRIMARY]
        for metric in METRICS:
            number=float(reported['values'][metric]);wanted=value if metric==PRIMARY else 1.
            if isinstance(reported['values'][metric],bool) or not math.isfinite(number) or abs(number-wanted)>1e-8 or reported['units'][metric]!='fraction':raise ValueError('reported metric/unit mismatch:'+metric)
        if primary['id']!=PRIMARY or primary['unit']!='fraction' or abs(float(primary['value'])-value)>1e-8:raise ValueError('mesh_metrics primary mismatch')
        if not errors:
            regenerated={stage:replay(confined(root,relative),stage,c) for stage,relative in directories.items()};diagnostics['replay_study']=inspect_study(regenerated,c)
            if diagnostics['replay_study']['status']!='PASS':raise ValueError('replayed study gates rejected')
            diagnostics['all_four_stages_reexecuted']=True
    except Exception as e:errors.append(type(e).__name__+':'+str(e))
    return {'task_id':TASK,'score':100. if not errors else 0.,'score_cap':100.,'status':'openfoam_three_grid_constraint_reexecution_check','errors':errors,'diagnostics':diagnostics,'precheck':base,'claim_boundary':c['truth_boundary'],'comparison':'Raw topology and its own dictionary replay under public constraints; no GT cell identity matching.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=os.environ.get('URBANWIND_EVAL_ROOT','/home/user/Desktop'));a=p.parse_args();print(json.dumps(evaluate(a.root),ensure_ascii=False,sort_keys=True))
