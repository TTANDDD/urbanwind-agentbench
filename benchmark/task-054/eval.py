#!/usr/bin/env python3
"""Staged partition evaluator. 100 requires raw proof AND real isolated replay."""
from pathlib import Path
from datetime import datetime
import argparse,hashlib,json,math,os,re,shutil,subprocess,tempfile
from contract_evaluator import evaluate as precheck
from task054_partition_inputs import contract,generate
from task054_raw_partition import inspect_partition,require
TASK='urbanwind-task-054';PRIMARY='cell_load_imbalance_ratio';METRICS=[PRIMARY,'evidence_provenance_fraction','hard_gate_pass_fraction']
RAW=['constant/polyMesh/'+n for n in ['points','faces','owner','neighbour','boundary']]+['system/blockMeshDict','system/decomposeParDict','log.blockMesh','log.checkMesh','log.decomposePar','commands.json']+[
    f'processor{r}/constant/polyMesh/'+n for r in range(4) for n in ['points','faces','owner','neighbour','boundary','cellProcAddressing','faceProcAddressing','pointProcAddressing','boundaryProcAddressing']]
def load(p):
    p=Path(p);require(p.is_file() and p.stat().st_size<16*1024**2,'JSON size/presence');return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1024**2),b''):h.update(b)
    return h.hexdigest()
def confined(root,raw):
    p=Path(raw);require(not p.is_absolute() and '..' not in p.parts and p.parts,'relative evidence path')
    out=root/p;require(root in out.resolve().parents,'evidence path escape')
    require(not any(q.is_symlink() for q in [out,*out.parents] if q==root or root in q.parents),'symlink evidence')
    return out
def utc(s):
    d=datetime.fromisoformat(s);require(d.tzinfo is not None,'timezone required');return d
def safe_dictionaries(case,method):
    for name in ['blockMeshDict','decomposeParDict']:
        s=(case/'system'/name).read_text(encoding='utf-8');require(len(s)<2_000_000 and not re.search(r'#|\$|\b(codeStream|libs|functions|dynamicCode|coded)\b',s),'unsafe executed dictionary')
    s=(case/'system/blockMeshDict').read_text();counts=re.findall(r'hex\s*\([^)]*\)\s*\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\)',s)
    require(counts and sum(math.prod(map(int,r)) for r in counts)==141952,'fixed input mesh cell budget')
    d=(case/'system/decomposeParDict').read_text();require(re.search(r'numberOfSubdomains\s+4\s*;',d) and re.search(r'method\s+'+method+r'\s*;',d),'pinned rank count and declared method')
def audit_submission(root):
    """Returns diagnostics only; passing this function is NOT benchmark success."""
    root=Path(root).resolve();c=contract();base=precheck(root,TASK);errors=list(base['errors']);diagnostics={};directories={}
    try:
        spec=load(root/'task_spec.json');report=load(root/'reporting_contract.json')
        require(spec['required_cases']==1 and spec['required_solver_runs']==0 and report['expected_case_ids']==c['case_ids'] and report['expected_solver_case_ids']==[],'one comparison case zero flow runs')
        require(report.get('partition_geometry_contract')==c,'altered trusted public contract')
        require(load(root/'design.json').get('geometry')==c['geometry'],'design geometry')
        runs=load(root/'run_manifest.json')['runs'];require(len(runs)==1 and runs[0]['case_id']==c['case_ids'][0],'comparison case identity')
        run=runs[0];require(run.get('solver_runs')==0 and run.get('execution_role')=='provided_data_or_tool_execution' and run.get('exit_code')==0,'tool role/CFD count/exit')
        require(any(isinstance(s,dict) and s.get('name','').lower()=='openfoam' and s.get('version')=='v2412' for s in run['software']),'pinned software provenance')
        evidence=load(root/'evidence_manifest.json')['files'];indexed={}
        for row in evidence:
            raw=row['path'];p=confined(root,raw);require(raw not in indexed,'duplicate evidence path')
            require(p.is_file() and p.stat().st_size<128*1024**2 and sha(p)==row['sha256'],'missing/oversized/hash-mismatched evidence');indexed[raw]=row['sha256']
        linked={r['path']:r['sha256'] for r in run['evidence']};require(len(linked)==len(run['evidence']) and linked==indexed,'case evidence ownership/hash linkage')
        subruns=run['method_subruns'];require([r['method'] for r in subruns]==c['methods'],'two ordered distinct methods')
        used=set();all_commands=[]
        for sub in subruns:
            method=sub['method'];relative=sub['tool_case_directory'];case=confined(root,relative)
            require(not any(case==q or case in q.parents or q in case.parents for q in directories.values()),'overlapping method directories');directories[method]=case
            for name in RAW:require((Path(relative)/name).as_posix() in indexed,'missing raw file '+name)
            owned={r for r in indexed if r.startswith(relative.rstrip('/')+'/')};require(not used&owned,'method evidence overlap');used|=owned
            commands=load(case/'commands.json');require(commands==sub['commands'],'subrun command linkage')
            require([r['name'] for r in commands] in [c['original_tool_sequence'],c['replay_tool_sequence']],'required real tool chain')
            end=None
            for row in commands:
                start=utc(row['start_utc']);finish=utc(row['end_utc']);require(row['exit_code']==0 and finish>=start and (end is None or start>=end),'command chronology/failure')
                require(isinstance(row['argv'],list) and row['argv'] and Path(row['argv'][0]).name in ['blockMesh','checkMesh','decomposePar','mpiexec'],'command/tool mismatch')
                log=case/('log.'+row['name']);require(log.is_file() and sha(log)==row['log_sha256'],'command hash linkage')
                require(indexed.get(log.relative_to(root).as_posix())==row['log_sha256'],'command log must belong to hashed evidence')
                source=log.read_text(encoding='utf-8');require('v2412' in source and 'End' in source,'missing completed pinned tool log')
                if row['name']=='checkMesh':require('-allGeometry' in row['argv'] and '-allTopology' in row['argv'],'full serial check flags')
                end=finish
            all_commands+=commands;safe_dictionaries(case,method);diagnostics[method]=inspect_partition(case,method)
        require(used==set(indexed),'unowned raw evidence');require(run['commands']==all_commands,'comparison commands not full subrun chain')
        require(utc(run['started_at'])<=min(utc(r['start_utc']) for r in all_commands) and utc(run['ended_at'])>=max(utc(r['end_utc']) for r in all_commands),'comparison run chronology')
        require(diagnostics['simple']['geometry']['raw_serial_topology_sha256']==diagnostics['scotch']['geometry']['raw_serial_topology_sha256'],'different serial mesh in comparison')
        values=load(root/'metrics.json');mesh_metrics=load(root/'mesh_metrics.json');reported=values['values'][PRIMARY]
        require(isinstance(reported,dict) and set(reported)==set(c['methods']),'primary must report both methods, not selected winner')
        for method in c['methods']:
            value=reported[method];require(not isinstance(value,bool) and math.isfinite(float(value)) and abs(float(value)-diagnostics[method][PRIMARY])<=1e-8,'primary metric differs from raw '+method)
            wanted={k:diagnostics[method][k] for k in ['cells_per_rank','communication_faces_per_rank','cell_load_imbalance_ratio','communication_face_imbalance','unique_processor_faces']}
            require(mesh_metrics['methods'][method]==wanted,'mesh_metrics raw summary '+method)
        for metric in METRICS:
            require(values['units'][metric]=='fraction','metric unit')
            if metric!=PRIMARY:require(not isinstance(values['values'][metric],bool) and values['values'][metric]==1.,'auxiliary full gate/provenance fraction')
    except Exception as exc:errors.append(type(exc).__name__+':'+str(exc))
    return {'task_id':TASK,'status':'RAW_SUBMISSION_AUDIT_ONLY_NOT_BENCHMARK_ACCEPTANCE','errors':errors,'diagnostics':diagnostics,'precheck':base},directories
def replay(case,method):
    require(os.environ.get('WM_PROJECT_VERSION')=='v2412','pinned OpenCFD v2412 unavailable')
    tools={x:shutil.which(x) for x in ['blockMesh','checkMesh','decomposePar','mpiexec']};require(all(tools.values()),'mesh tools unavailable')
    prefix=Path(tools['blockMesh']).resolve().parent;require(all(Path(v).resolve().parent==prefix for v in tools.values()),'tools and packaged MPI must share pinned prefix')
    safe_dictionaries(case,method)
    with tempfile.TemporaryDirectory(prefix='urbanwind-task054-replay-') as raw:
        fresh=Path(raw)/'case';generate(fresh)
        # Only these two safe submitted dictionaries are executable inputs. No
        # submitted fields, libraries, functionObjects or arbitrary scripts run.
        for name in ['blockMeshDict','decomposeParDict']:shutil.copy2(case/'system'/name,fresh/'system'/name)
        chain=[('blockMesh',[tools['blockMesh']]),('checkMesh',[tools['checkMesh'],'-constant','-noZero','-allGeometry','-allTopology']),
               ('decomposePar',[tools['decomposePar'],'-no-fields']),('checkMesh.parallel',[tools['mpiexec'],'-n','4',tools['checkMesh'],'-parallel','-constant','-noZero','-allGeometry','-allTopology'])]
        for name,args in chain:
            with (fresh/('log.'+name)).open('w') as log:r=subprocess.run(args,cwd=fresh,stdout=log,stderr=subprocess.STDOUT,timeout=180)
            require(r.returncode==0,'isolated replay failed '+name)
        require('Mesh OK.' in (fresh/'log.checkMesh.parallel').read_text() and 'Failed' not in (fresh/'log.checkMesh.parallel').read_text(),'fresh parallel mesh check')
        actual=inspect_partition(fresh,method);submitted=inspect_partition(case,method)
        require(actual['geometry']['raw_serial_topology_sha256']==submitted['geometry']['raw_serial_topology_sha256'],'raw serial mesh differs from its own submitted dictionary replay')
        # Scotch is allowed to choose a different valid partition on replay.
        # Both outputs undergo full addressing proofs; no GT assignment matching.
        actual['real_isolated_replay_verified']=True;actual['parallel_checkMesh_pass']=True
        actual['tools_sha256']={k:sha(v) for k,v in tools.items()};return actual
def evaluate(root):
    result,directories=audit_submission(root);errors=result['errors'];reexecuted={}
    if not errors:
        try:
            for method,case in directories.items():reexecuted[method]=replay(case,method)
        except Exception as exc:errors.append(type(exc).__name__+':'+str(exc))
    return {**result,'status':'task_specific_partition_constraint_reexecution_check','score':100. if not errors and len(reexecuted)==2 else 0.,'score_cap':100.,
        'reexecution':reexecuted,'claim_boundary':contract()['truth_boundary'],'comparison':'Public geometry and complete mapping constraints; no hidden assignment, byte equality or fixed winning method.'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=os.environ.get('URBANWIND_EVAL_ROOT','/home/user/Desktop'));a=p.parse_args();print(json.dumps(evaluate(a.root),ensure_ascii=False,sort_keys=True))
