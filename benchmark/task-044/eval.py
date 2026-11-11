#!/usr/bin/env python3
"""Constraint-based raw-mesh evaluator; never compare a mesh with GT cell IDs."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,math,os,re,shutil,subprocess,tempfile
import numpy as np
from contract_evaluator import evaluate as precheck
from task044_mesh_inputs import contract
from task044_mesh_geometry import inspect_mesh
from task042_raw_mesh import read_mesh

TASK='urbanwind-task-044'
PRIMARY='wake_refinement_coverage'
METRICS=[PRIMARY,'evidence_provenance_fraction','hard_gate_pass_fraction']
DICTS=['blockMeshDict','controlDict','fvSchemes','fvSolution','decomposeParDict']
RAW=['constant/polyMesh/'+x for x in ['points','faces','owner','neighbour','boundary']]+['system/'+x for x in DICTS]+['log.'+x for x in ['blockMesh','checkMesh','decomposePar','checkMesh.parallel']]+['commands.json']

def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
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
    if t.tzinfo is None:raise ValueError('timestamp must have timezone')
    return t
def signature(case):
    p,l,s,v,o,n,b=read_mesh(case);count=int(max(o.max(),n.max() if len(n) else 0))+1
    lo=np.full((count,3),np.inf);hi=np.full((count,3),-np.inf)
    fl=np.minimum.reduceat(p[v],s,axis=0);fh=np.maximum.reduceat(p[v],s,axis=0)
    np.minimum.at(lo,o,fl);np.maximum.at(hi,o,fh)
    np.minimum.at(lo,n,fl[:len(n)]);np.maximum.at(hi,n,fh[:len(n)])
    boxes=np.round(np.c_[lo,hi],9);order=np.lexsort(tuple(boxes[:,i] for i in reversed(range(6))))
    return hashlib.sha256(boxes[order].astype('<f8').tobytes()).hexdigest()

def safe_dictionaries(case):
    for name in DICTS:
        text=(case/'system'/name).read_text(encoding='utf-8')
        if len(text)>2_000_000 or re.search(r'#|\b(codeStream|libs|functions|dynamicCode|coded)\b|\$',text):raise ValueError('code-bearing or oversized dictionary')
    block=(case/'system/blockMeshDict').read_text()
    counts=re.findall(r'hex\s*\([^)]*\)\s*\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\)',block)
    if not counts or sum(math.prod(map(int,x)) for x in counts)>contract()['gates']['cells_max']:raise ValueError('blockMesh input cell budget')
    d=(case/'system/decomposeParDict').read_text()
    if not re.search(r'numberOfSubdomains\s+16\s*;',d):raise ValueError('requires 16 partitions')

def replay(case,stage,c):
    if os.environ.get('WM_PROJECT_VERSION')!='v2412':raise RuntimeError('pinned OpenCFD v2412 environment unavailable')
    names=['blockMesh','checkMesh','decomposePar','mpiexec'];tools={n:shutil.which(n) for n in names}
    if not all(tools.values()):raise RuntimeError('mesh tools unavailable')
    prefix=Path(tools['blockMesh']).resolve().parent
    if any(Path(x).resolve().parent!=prefix for x in tools.values()):raise RuntimeError('mesh tools and packaged MPI must share pinned prefix')
    safe_dictionaries(case)
    with tempfile.TemporaryDirectory(prefix='urbanwind-task044-replay-') as raw:
        fresh=Path(raw)/'case';(fresh/'system').mkdir(parents=True)
        for name in DICTS:shutil.copy2(case/'system'/name,fresh/'system'/name)
        chain=[('blockMesh',[tools['blockMesh']]),('checkMesh',[tools['checkMesh'],'-constant','-noZero','-allGeometry','-allTopology']),
               ('decomposePar',[tools['decomposePar'],'-no-fields']),('checkMesh.parallel',[tools['mpiexec'],'-n','16',tools['checkMesh'],'-parallel','-constant','-noZero','-allGeometry','-allTopology'])]
        for name,args in chain:
            with (fresh/('log.'+name)).open('w') as log:
                r=subprocess.run(args,cwd=fresh,stdout=log,stderr=subprocess.STDOUT,timeout=180,check=False)
            if r.returncode:raise ValueError('replay '+name+' failed')
        analysis=inspect_mesh(fresh,c,stage)
        if analysis['status']!='PASS':raise ValueError('replay public gates rejected:'+','.join(k for k,v in analysis['gates'].items() if not v))
        if signature(fresh)!=signature(case):raise ValueError('submitted raw mesh differs from its dictionary replay')
        analysis['reexecution_verified']=True
        return analysis

def evaluate(root):
    root=Path(root).resolve();base=precheck(root,TASK);errors=list(base['errors']);diagnostics={};c=contract()
    try:
        spec=load(root/'task_spec.json');reporting=load(root/'reporting_contract.json')
        if spec['required_cases']!=1 or spec['required_solver_runs']!=0 or reporting['expected_case_ids']!=[c['case_id']]:raise ValueError('case/tool-only contract changed')
        if reporting.get('wake_mesh_geometry_contract')!=c:raise ValueError('public scientific contract changed')
        for name in ['design.json','mesh_metrics.json','run_manifest.json','metrics.json','evidence_manifest.json','decision.md']:
            if not (root/name).is_file():raise ValueError('missing required artifact '+name)
        manifest=load(root/'run_manifest.json');runs=manifest['runs']
        if len(runs)!=1 or runs[0]['case_id']!=c['case_id']:raise ValueError('one tool case required')
        run=runs[0]
        if run.get('execution_role')!='provided_data_or_tool_execution' or run.get('exit_code')!=0 or run.get('solver_runs',0)!=0:raise ValueError('tool execution role mismatch')
        if not any(isinstance(x,dict) and x.get('name','').lower()=='openfoam' and x.get('version')=='v2412' for x in run['software']):raise ValueError('pinned software provenance missing')
        directories={'qualification':run.get('qualification_case_directory','qualification_case'),'full':'wake_mesh_case'}
        evidence=load(root/'evidence_manifest.json')['files'];indexed={}
        for row in evidence:
            raw=row['path'];p=confined(root,raw)
            if raw in indexed:raise ValueError('duplicate evidence path')
            if not p.is_file() or p.stat().st_size>128*1024**2 or sha(p)!=row['sha256']:raise ValueError('evidence missing, oversized or hash mismatch:'+raw)
            indexed[raw]=row['sha256']
        linked={x['path']:x['sha256'] for x in run['evidence']}
        if len(linked)!=len(run['evidence']) or linked!=indexed:raise ValueError('run evidence and manifest differ')
        chronological=[]
        for stage,relative in directories.items():
            case=confined(root,relative)
            if not case.is_dir():raise ValueError('missing '+stage+' case')
            for rel in RAW:
                path=(Path(relative)/rel).as_posix()
                if path not in indexed:raise ValueError('raw evidence unregistered:'+path)
            if (case/'wake_mesh_contract.json').is_file() and load(case/'wake_mesh_contract.json')!=c:raise ValueError('case attempted to alter gates')
            records=load(case/'commands.json')
            if len(records)!=4 or [x['name'] for x in records]!=c['tool_sequence']:raise ValueError('required real tool sequence missing')
            for row in records:
                if row['exit_code']!=0 or not isinstance(row['argv'],list):raise ValueError('tool exit/provenance failure')
                start=utc(row['start_utc']);end=utc(row['end_utc'])
                if end<start or (chronological and start<chronological[-1]):raise ValueError('tool chronology invalid')
                chronological.append(end)
            safe_dictionaries(case)
            result=inspect_mesh(case,c,stage);diagnostics[stage]=result
            if result['status']!='PASS':raise ValueError(stage+' raw public gates rejected:'+','.join(k for k,v in result['gates'].items() if not v))
        if utc(run['started_at'])>utc(load(confined(root,directories['qualification'])/'commands.json')[0]['start_utc']) or utc(run['ended_at'])<chronological[-1]:raise ValueError('run timestamps do not enclose commands')
        reported=load(root/'metrics.json');result=load(root/'mesh_metrics.json');value=diagnostics['full'][PRIMARY]
        for metric in METRICS:
            number=float(reported['values'][metric]);wanted=value if metric==PRIMARY else 1.
            if isinstance(reported['values'][metric],bool) or not math.isfinite(number) or abs(number-wanted)>1e-8 or reported['units'][metric]!='fraction':raise ValueError('reported metric/unit mismatch:'+metric)
        primary=result['primary_metric']
        if primary['id']!=PRIMARY or primary['unit']!='fraction' or abs(float(primary['value'])-value)>1e-8:raise ValueError('mesh_metrics primary mismatch')
        if not errors:
            for stage,relative in directories.items():diagnostics[stage+'_replay']=replay(confined(root,relative),stage,c)
    except Exception as e:errors.append(type(e).__name__+':'+str(e))
    return {'task_id':TASK,'score':100. if not errors else 0.,'score_cap':100.,'status':'openfoam_wake_mesh_constraint_reexecution_check',
        'errors':errors,'diagnostics':diagnostics,'precheck':base,'claim_boundary':c['truth_boundary'],
        'comparison':'Public constraints and raw geometry replay; no GT cell identity or file equality is required.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=os.environ.get('URBANWIND_EVAL_ROOT','/home/user/Desktop'));a=p.parse_args()
    print(json.dumps(evaluate(a.root),ensure_ascii=False,sort_keys=True))
