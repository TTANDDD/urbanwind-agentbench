"""Fit evidence views inside the existing Judge cap without extra API calls."""
import copy
import hashlib
import json


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def text_view(value, limit=200):
    if not isinstance(value, str) or len(value.encode()) <= limit: return value
    data=value.encode(); head=data[:limit//2].decode(errors='ignore');tail=data[-limit//4:].decode(errors='ignore')
    return {'head':head,'tail':tail,'omitted_bytes':len(data)-len((head+tail).encode()),'scope':'Explicit text excerpt only.'}


def manifest_budget_view(value):
    """Every run/case, exit and evidence hash survives; no invented successes."""
    if not isinstance(value, dict) or not isinstance(value.get('runs'), list): return value
    keep=('case_id','execution_role','started_at','ended_at','exit_code','solver_exit_code',
          'timed_out','cancelled','solver_record','execution_receipts','evidence','software','commands')
    runs=[]
    for row in value['runs']:
        if not isinstance(row,dict):runs.append(row);continue
        item={k:copy.deepcopy(row[k]) for k in keep if k in row}
        if 'commands' in item:
            commands=item['commands'];item['commands']=[text_view(x,160) for x in commands] if isinstance(commands,list) else text_view(commands,160)
        if isinstance(item.get('software'),list):
            item['software']=[{k:text_view(v,160) for k,v in x.items() if k in ('name','version','path','executable_path','sha256')} if isinstance(x,dict) else x for x in item['software']]
        item['omitted_fields']=[k for k in row if k not in keep]
        item['scope']='All cases, exits and evidence references retained; omitted fields were not reviewed. Wrapper exit is not solver exit.'
        runs.append(item)
    return {'task_id':value.get('task_id'),'runs':runs,'run_count':len(runs),
            'omitted_root_fields':[k for k in value if k not in ('task_id','runs')],
            'source_view_sha256':hashlib.sha256(encoded(value)).hexdigest()}


def fit_payload(payload, budget=80*1024):
    if len(encoded(payload)) <= budget:return payload
    result=copy.deepcopy(payload)
    result['budget_view']={'source_view_sha256':hashlib.sha256(encoded(payload)).hexdigest(),
        'original_bytes':len(encoded(payload)),'hard_input_limit_bytes':96*1024,
        'scope':'Explicit budgeted views; omitted material is not checked evidence. No extra model calls or higher size cap.'}
    evidence=result['evidence'];seen={}
    for group in ('public_documents','selected_public_artifacts'):
        for item in evidence.get(group,[]):
            key=(item.get('path'),item.get('sha256'))
            if key[0] and key[1] and key in seen:
                for field in ('content','excerpt','head','tail'):item.pop(field,None)
                item['same_content_as']=seen[key];item['content_scope']='Same file and SHA256 as the earlier supplied view.'
            elif key[0] and key[1]:seen[key]=group+'/'+str(item['path'])
            if item.get('path')=='run_manifest.json' and 'content' in item:
                item['content']=manifest_budget_view(item['content'])
                item['content_scope']='All run identities, exit states, timing and evidence hashes; commands excerpted and other omitted fields listed.'
    for item in evidence.get('registered_execution_evidence',[]):
        for field,limit in [('stdout_tail',240),('stderr_tail',160),('command_excerpt',180)]:
            if field in item:item[field]=text_view(item[field],limit)
        item['scope']='Immutable receipt identity/hash and exit/timing retained; stream excerpts only. Wrapper exit does not certify solver completion.'
    for item in evidence.get('latest_execution_evidence',[]):
        for field in ('stdout_tail','stderr_tail','command'):
            if field in item:item[field]=text_view(item[field],160)
    if len(encoded(result)) <= budget:return result
    # Spend the final prompt budget on contracts, checks and receipts. Every
    # artifact remains hash-addressed; original bytes stay on disk untouched.
    for item in evidence.get('selected_public_artifacts',[]):
        if item.get('path')=='run_manifest.json':continue
        if 'excerpt' in item:item['excerpt']=text_view(item['excerpt'],400)
        if 'content' in item and len(encoded(item['content']))>900:
            original=item.pop('content');item['content_excerpt']=text_view(encoded(original).decode(),600)
            item['omitted_content_view_sha256']=hashlib.sha256(encoded(original)).hexdigest()
            item['content_scope']='Explicit excerpt; omitted contents were not reviewed.'
    if len(encoded(result)) <= budget:return result
    # An exceptional oversized document still gets a real Judge request with
    # honest limits, instead of silently consuming finish deferrals at zero calls.
    for group in ('selected_public_artifacts','public_documents'):
        for item in evidence.get(group,[]):
            if len(encoded(result)) <= budget:break
            if item.get('path') in ('acceptance_contract.json','run_manifest.json'):continue
            for field in ('content','excerpt','content_excerpt','head','tail'):item.pop(field,None)
            item['omitted']='judge_input_budget_material_not_reviewed'
        if len(encoded(result)) <= budget:break
    result['checks'].setdefault('blocking_unknowns',[]).append('Judge budget omitted material; do not claim readiness for facts not covered by complete deterministic observations.')
    return result
