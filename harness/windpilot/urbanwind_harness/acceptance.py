"""Explicit public acceptance rules shared by documentation and verification."""
from __future__ import annotations
import csv
import io
import json
import math
import re
from .foam_dictionary import entry, uniform_number


def public_contract(task, reporting):
    rules=[]
    def rule(role,path,expected,case=None):
        rules.append({'role':role,'pointer':path,'expected':expected,**({'case_id':case} if case else {})})
    def bc(role,patch,kind,case=None):rule(role,['boundaryField',patch,'type'],kind,case)
    if task=='task-061':
        for role,patch,kind in [('field_U','inlet','atmBoundaryLayerInletVelocity'),('field_U','top','fixedShearStress'),('field_k','inlet','atmBoundaryLayerInletK'),('field_omega','inlet','atmBoundaryLayerInletOmega'),('field_nut','ground','atmNutkWallFunction'),('field_p','top','zeroGradient')]:bc(role,patch,kind)
    if task=='task-062':
        bc('field_U','inlet','fixedProfile','task-062-case-001');rule('field_U',['boundaryField','inlet','profile'],'csvFile','task-062-case-001')
        bc('field_U','inlet','atmBoundaryLayerInletVelocity','task-062-case-002');rule('field_U',['boundaryField','inlet','z0'],'uniform 0.01','task-062-case-002')
    if task=='task-063':
        bc('field_U','inlet','atmBoundaryLayerInletVelocity');bc('field_nut','ground','atmNutkWallFunction')
        rule('field_U',['boundaryField','inlet','z0'],'uniform 0.01');rule('field_nut',['boundaryField','ground','z0'],'uniform 0.01')
    if task in ('task-064','task-068'):
        bc('field_U','inlet','fixedProfile')
        if task=='task-064':rule('field_U',['boundaryField','inlet','profile'],'csvFile')
        rule('field_U',['boundaryField','inlet','profileCoeffs','file'],'constant/profile_'+('mo' if task=='task-064' else 'power')+'.csv')
    if task=='task-071':bc('field_U0','outlet','inletOutlet');bc('field_p0','outlet','fixedValue')
    if task in ('task-061','task-062','task-063','task-064','task-068','task-069','task-071','task-072'):
        rule('controlDict',['endTime'],400)
    for function in ({'task-062':['responseProbes'],'task-064':['inletProfile'],'task-068':['inletProfile'],'task-069':['responseProbes'],'task-072':['inletProfile','responseProbes']}.get(task,[])):
        rule('controlDict',['functions',function,'writeInterval'],20)
    if task=='task-064':rule('controlDict',['functions','responseProbes'],None)
    if task=='task-074':rule('controlDict',['endTime'],180);rule('controlDict',['deltaT'],0.015)
    raw_json={}
    if task=='task-063':raw_json['roughness_inputs']=['z0_inlet_m','z0_wall_m','z0_map_m']
    if task=='task-071':raw_json['outlet_configuration']=['outlet_x_D','outlet_U_type','outlet_p_type']
    return {'schema_version':'0.2.9-public-acceptance','task_id':reporting['task_id'],
            'run_manifest':reporting.get('run_manifest','run_manifest.json'),
            'expected_case_ids':reporting.get('expected_case_ids',[]),
            'expected_solver_case_ids':reporting.get('expected_solver_case_ids',[]),
            'run_required_fields':['case_id','commands','software','started_at','ended_at','exit_code','evidence'],
            'software_item_required_fields':['name'],
            'solver_execution_role':'real_solver_run','solver_software_names':['openfoam','palm'],
            'run_manifest_export':{'command':'python3 provenance_export.py run_bindings.json',
                'binding_format':[{'case_id':'an expected case ID','execution_records':['three digit trace step, e.g. 025'],'solver_record':'the foreground solver trace step, for solver cases only','evidence_paths':['existing relative raw file path']}],
                'requirements':'All case IDs exactly once; immutable start/end/exit receipts; actual foreground OpenFOAM completion in the recorded stream. Use tee to preserve both the trace stream and raw solver log. Background launch success alone is insufficient.',
                'output':'run_manifest.candidate.json; inspect the candidate before adopting it'},
            'dictionary_rules':rules,'raw_json_root_fields':raw_json,
            'csv_roles':{'profile_csv':{'header':False,'columns':['z_m','Ux_m_s','Uy_m_s','Uz_m_s'],'rows':52}} if task=='task-064' else {},
            'scope':'Public format and existing acceptance conditions only; no hidden reference arrays or expected score. Other numerical checks remain required.'}


def walk_rows(value,case=None):
    if isinstance(value,dict):
        case=value.get('case_id',case)
        yield case,value
        for key,child in value.items():yield from walk_rows(child,key if re.fullmatch(r'task-\d+-case-\d+',str(key)) else case)
    elif isinstance(value,list):
        for child in value:yield from walk_rows(child,case)


def verify_acceptance(root,canonical,reader):
    findings=[];observed=[]
    def read(name,source=root):
        v=reader(source,name,max_bytes=128*1024*1024)
        if v.get('omitted'):raise ValueError(v.get('omitted'))
        return v
    try:contract=json.loads(read('acceptance_contract.json',canonical)['text'])
    except (ValueError,KeyError):return {'errors':[],'findings':[],'observations':[],'coverage':'acceptance_contract_unavailable'}
    def fail(file,pointer,expected,actual,code):
        findings.append({'code':code,'file':file,'pointer':pointer,'expected':expected,'actual':actual})
    manifest=contract['run_manifest']
    try:
        rm=json.loads(read(manifest)['text']);runs=rm.get('runs')
        if not isinstance(runs,list):fail(manifest,'/runs','list',type(runs).__name__,'run_container');runs=[]
        ids=[r.get('case_id') for r in runs if isinstance(r,dict)]
        if sorted(map(str,ids))!=sorted(contract['expected_case_ids']):fail(manifest,'/runs/*/case_id',contract['expected_case_ids'],ids,'case_coverage')
        for i,row in enumerate(runs):
            if not isinstance(row,dict):fail(manifest,f'/runs/{i}','object',type(row).__name__,'run_type');continue
            prefix=f'/runs/{i}'
            for key in contract['run_required_fields']:
                if key not in row:fail(manifest,prefix+'/'+key,'required',None,'missing_run_field')
            for key in ['commands','software','evidence']:
                if not isinstance(row.get(key),list) or not row.get(key):fail(manifest,prefix+'/'+key,'nonempty list',type(row.get(key)).__name__,'run_field_type')
            if row.get('case_id') in contract['expected_solver_case_ids']:
                if row.get('execution_role')!='real_solver_run':fail(manifest,prefix+'/execution_role','real_solver_run',row.get('execution_role'),'solver_role')
                names={str(x.get('name','')).lower() for x in row.get('software',[]) if isinstance(x,dict)} if isinstance(row.get('software'),list) else set()
                if not names.intersection(contract['solver_software_names']):fail(manifest,prefix+'/software/*/name',contract['solver_software_names'],sorted(names),'solver_software')
                if row.get('exit_code')!=0:fail(manifest,prefix+'/exit_code',0,row.get('exit_code'),'solver_exit')
    except (ValueError,KeyError,TypeError) as exc:fail(manifest,'/','readable manifest',str(exc),'manifest_unavailable')
    names=set()
    for p in (canonical/'local_assets').glob('*contract.json'):
        try:
            value=json.loads(read('local_assets/'+p.name,canonical)['text'])
            if value.get('required_manifest'):names.add(value['required_manifest'])
        except (ValueError,KeyError):continue
    seen=set()
    for name in sorted(names):
        try:evidence=json.loads(read(name)['text'])
        except (ValueError,KeyError):fail(name,'/','readable evidence manifest',None,'evidence_unavailable');continue
        for case,row in walk_rows(evidence):
            for role,item in row.items():
                if not isinstance(item,dict) or not isinstance(item.get('path'),str):continue
                path=item['path'];identity=(path,role,case)
                if identity in seen:continue
                seen.add(identity)
                relevant=[r for r in contract['dictionary_rules'] if r['role']==role and (not r.get('case_id') or r['case_id']==case)]
                if not relevant and role not in contract['raw_json_root_fields'] and role not in contract['csv_roles']:continue
                try:
                    file=read(path);text=file['text']
                    if item.get('sha256')!=file['sha256']:fail(path,'/sha256',file['sha256'],item.get('sha256'),'evidence_hash')
                    for rule in relevant:
                        actual=entry(text,*rule['pointer']);expected=rule['expected']
                        equal=actual==expected
                        if isinstance(expected,(int,float)):
                            try:equal=math.isfinite(float(actual)) and float(actual)==expected
                            except (TypeError,ValueError):equal=False
                        if isinstance(expected,str) and expected.startswith('uniform ') and len(rule['pointer'])==3:
                            equal=uniform_number(text,rule['pointer'][1],rule['pointer'][2],float(expected.split()[1]))
                        record={'file':path,'pointer':'/'+ '/'.join(rule['pointer']),'expected':expected,'actual':actual,'passed':equal,'sha256':file['sha256']}
                        observed.append(record)
                        if not equal:fail(path,record['pointer'],expected,actual,'dictionary_value')
                    if role in contract['raw_json_root_fields']:
                        obj=json.loads(text)
                        for field in contract['raw_json_root_fields'][role]:
                            if not isinstance(obj,dict) or field not in obj:fail(path,'/'+field,'required at root',None,'raw_json_field')
                    if role in contract['csv_roles']:
                        spec=contract['csv_roles'][role];rows=list(csv.reader(io.StringIO(text)))
                        good=len(rows)==spec['rows'] and all(len(r)==len(spec['columns']) for r in rows)
                        try:good=good and all(math.isfinite(float(x)) for r in rows for x in r)
                        except ValueError:good=False
                        if not good:fail(path,'/',spec,{'rows':len(rows),'first_row':rows[0] if rows else []},'csv_layout')
                except (ValueError,KeyError,TypeError) as exc:fail(path,'/','readable supported public evidence',str(exc),'evidence_parse')
    return {'errors':[f"{f['file']}:{f['pointer']}:{f['code']}" for f in findings], 'findings':findings,'observations':observed,'coverage':'Published acceptance rules only; original public and final numerical checks remain required.'}
