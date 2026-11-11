"""Read-only checks of PUBLIC observable declarations and actual raw evidence.

No task identifiers, grader imports, hidden arrays, generated PASS values or
workspace writes. Findings are repair advice; final scoring remains independent.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path

from .foam_dictionary import parse_dictionary

NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
VALUE = re.compile(NUMBER)
VECTOR = re.compile(r'\(\s*(' + NUMBER + r')\s+(' + NUMBER + r')\s+(' + NUMBER + r')\s*\)')
HEADER = re.compile(r'^\s*#\s*Probe\s+(\d+)\s+\(([^()]*)\)(?:[ \t]+at patch\b[^\n]*)?[ \t\r]*$', re.M)
ROLE_FIELDS = {
    'probe_U': ('inlet_U_m_s', 'probe_U_m_s'),
    'probe_k': ('inlet_k_m2_s2',), 'probe_omega': ('inlet_omega_s_1',),
    'response_U': ('response_U_m_s',), 'probe_T': ('probe_T_k','probe_concentration'),
    'probe_scalar': ('probe_concentration',), 'probe_C': ('probe_concentration',),
}
ALIASES = {
    'inlet_U': 'inlet_U_m_s', 'inlet_k': 'inlet_k_m2_s2', 'inlet_omega': 'inlet_omega_s_1',
    'response_U': 'response_U_m_s', 'probe_U': 'probe_U_m_s', 'probe_T': 'probe_T_k',
    'time': 'time_s', 'building_temperature': 'building_temperature_k',
}
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 400


def walk(value, pointer='', case=None, variants=None):
    if isinstance(value, dict):
        case = value.get('case_id', case)
        yield pointer, case, value
        for key, child in value.items():
            child_case = (variants or {}).get(key, case)
            if isinstance(key, str) and re.fullmatch(r'task-\d+-case-\d+', key):
                child_case = key
            yield from walk(child, pointer + '/' + str(key), child_case, variants)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer + '/' + str(index), case, variants)


def shape(value):
    if not isinstance(value, list): return []
    if not value: return [0]
    first = shape(value[0])
    return [len(value)] + (first if all(shape(v) == first for v in value) else ['ragged'])


def numeric(value):
    if isinstance(value, list): return all(numeric(v) for v in value)
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def select(value, path):
    """Resolve public schema wildcards without accepting an empty scalar match."""
    if not path:
        yield '', value
        return
    head, *tail = path
    items = enumerate(value) if isinstance(value, list) else value.items() if isinstance(value, dict) else []
    for key, child in items:
        if head == '*' or str(key) == head:
            for suffix, v in select(child, tail): yield '/' + str(key) + suffix, v


def probe_series(text, vector, expected_count=None):
    """Parse native point samples, preserving vector grouping and every row."""
    headers = HEADER.findall(text)
    indices = [int(i) for i, _ in headers]
    if indices and indices != list(range(len(indices))):
        raise ValueError('probe_header_indices_not_contiguous')
    coords = [[float(x) for x in VALUE.findall(s)] for _, s in headers]
    if any(len(p) != 3 or not numeric(p) for p in coords):
        raise ValueError('probe_coordinate_shape_or_nonfinite')
    count = expected_count if expected_count is not None else len(coords) or None
    times, rows = [], []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'): continue
        match = re.match(r'^(' + NUMBER + r')\s+(.*)$', line)
        if not match: raise ValueError('invalid_probe_row_at_line_' + str(line_number))
        t = float(match.group(1)); data = match.group(2)
        if vector:
            values = [[float(x) for x in m.groups()] for m in VECTOR.finditer(data)]
            if not values or VECTOR.sub('', data).strip():
                raise ValueError('vector_groups_required_at_line_' + str(line_number))
        else:
            tokens = data.split()
            try: values = [float(v.strip('()')) for v in tokens]
            except ValueError: raise ValueError('invalid_scalar_row_at_line_' + str(line_number)) from None
        if not numeric([t, values]): raise ValueError('nonfinite_probe_row_at_line_' + str(line_number))
        if count is None: count = len(values)
        if len(values) != count: raise ValueError('probe_count_expected_' + str(count) + '_at_line_' + str(line_number))
        times.append(t); rows.append(values)
    if not rows: raise ValueError('empty_probe_data')
    if any(a >= b for a, b in zip(times, times[1:])): raise ValueError('probe_times_not_strictly_increasing')
    if coords and len(coords) != count: raise ValueError('coordinate_count_differs_from_value_count')
    return {'coordinates': coords, 'times': times, 'values': rows}


def difference(stated, raw, pointer=''):
    """Serialization consistency tolerance, unrelated to any physical gate."""
    if shape(stated) != shape(raw):
        return {'reason': 'shape', 'submitted_shape': shape(stated), 'raw_shape': shape(raw)}
    if not numeric(stated): return {'reason': 'nonnumeric_or_nonfinite'}
    first = None; largest = 0.0; mismatches = 0
    def compare(a, b, p):
        nonlocal first, largest, mismatches
        if isinstance(a, list):
            for i, (x, y) in enumerate(zip(a, b)): compare(x, y, p + '/' + str(i))
        else:
            delta = abs(a-b); largest = max(largest, delta)
            if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
                mismatches += 1
                if first is None: first = {'pointer':p,'submitted':a,'raw':b,'absolute_delta':delta}
    compare(stated, raw, pointer)
    return {'reason':'values','mismatches':mismatches,'first_difference':first,'max_abs_delta':largest} if mismatches else None


def window_statistics(series, window, left_open=False):
    if not isinstance(window,list) or len(window)!=2 or not numeric(window) or window[0]>=window[1]:
        raise ValueError('explicit_valid_window_required')
    rows=[v for t,v in zip(series['times'],series['values'])
          if (window[0]<t if left_open else window[0]<=t) and t<=window[1]]
    if not rows:raise ValueError('empty_statistics_window')
    n=len(rows);points=len(rows[0]);means=[[0.,0.,0.] for _ in range(points)]
    for row in rows:
        for j,v in enumerate(row):
            for k in range(3):means[j][k]+=v[k]/n
    tke=[sum(sum((v-mean)**2 for v,mean in zip(row[j],means[j])) for row in rows)/(2*n) for j in range(points)]
    return {'sample_count':n,'mean_Ux_m_s':[v[0] for v in means],'mean_U_m_s':means,'resolved_tke_m2_s2':tke}


def statistical_consistency(root, report, documents, reads, fail, observations, unknown):
    """Match explicit case-bound native U streams to reported window statistics.

    No recursive workspace discovery and no choice among competing trajectories.
    Unspecified endpoint conventions remain explicit; they are not a new gate.
    """
    spec=report.get('simulation_result_contract',{}).get('case_results',{})
    if not spec:return 0
    known_ids=spec.get('exact_case_ids',report.get('expected_solver_case_ids',[]))
    refs={}
    for manifest in (report.get('evidence_manifest','evidence_manifest.json'),report.get('run_manifest','run_manifest.json')):
        try: evidence=reads.json(root,manifest)
        except (ValueError,KeyError):continue
        for _,case,obj in walk(evidence):
            items=[]
            if isinstance(obj.get('path'),str):items.append((str(obj.get('role','')),obj))
            for key,value in obj.items():
                if isinstance(value,dict) and isinstance(value.get('path'),str):items.append((key,value))
            for role,item in items:
                path=item['path'];parts=Path(path).parts
                if 'postProcessing' not in parts or not parts or parts[-1]!='U':continue
                if any(s in (role+' '+path).lower() for s in ('superseded','failed','archive','pilot','smoke')):continue
                identity=case or item.get('case_id')
                if identity is None:
                    matching=[c for c in known_ids if c in path]
                    identity=matching[0] if len(matching)==1 else known_ids[0] if len(known_ids)==1 else None
                if identity is None:
                    unknown.append({'file':path,'reason':'raw_statistics_case_binding_unresolved'});continue
                refs.setdefault(identity,{})[path]=item
    count=0
    for filename,value in documents.items():
        for pointer,case,row in walk(value):
            if not any(k in row for k in ('mean_Ux_m_s','mean_U_m_s','resolved_tke_m2_s2')):continue
            # Public array dimensions/counts, including generic LES reports.
            array_count=spec.get('probe_array_length',spec.get('array_length',spec.get('probe_count')))
            for key in ('mean_Ux_m_s','resolved_tke_m2_s2'):
                expected=spec.get('array_lengths',{}).get(key,array_count)
                if key in row and isinstance(expected,int) and shape(row[key])!=[expected]:
                    fail('statistical_array_shape',filename,pointer+'/'+key,expected=[expected],actual=shape(row[key]))
            if 'mean_U_m_s' in row and isinstance(array_count,int):
                if shape(row['mean_U_m_s']) not in ([array_count,3],[array_count*3]):
                    fail('statistical_vector_shape',filename,pointer+'/mean_U_m_s',expected=[[array_count,3],[array_count*3]],actual=shape(row['mean_U_m_s']))
            identity=case or (known_ids[0] if len(known_ids)==1 else None)
            candidates=refs.get(identity,{})
            if not candidates:
                unknown.append({'file':filename,'pointer':pointer,'reason':'case_bound_native_U_stream_not_registered'});continue
            # Restart chunks can overlap and function names can differ. Do not
            # silently concatenate or choose a stream based on report agreement.
            if len(candidates)!=1:
                unknown.append({'file':filename,'pointer':pointer,'reason':'multiple_raw_U_streams_require_explicit_binding','paths':list(candidates)[:8]});continue
            path,item=next(iter(candidates.items()))
            try:
                raw_item=reads.read(root,path)
                if item.get('sha256') and item['sha256']!=raw_item['sha256']:fail('raw_evidence_hash',path,expected=item['sha256'],actual=raw_item['sha256'])
                series=probe_series(raw_item['text'],True,array_count)
                window=row.get('window_s',spec.get('statistical_window_s'))
                frozen_window=spec.get('statistical_window_s')
                if frozen_window and window!=frozen_window:fail('public_statistics_window',filename,pointer+'/window_s',expected=frozen_window,actual=window)
                actual=window_statistics(series,window)
                convention='inclusive endpoints'
                # Where the public declaration omits endpoint semantics, report
                # the alternative explicitly instead of calling a 1-row edge a
                # physical failure or overwriting the evidence.
                if row.get('sample_count')!=actual['sample_count']:
                    alternative=window_statistics(series,window,True)
                    if row.get('sample_count')==alternative['sample_count']:
                        unknown.append({'file':filename,'pointer':pointer,'reason':'endpoint_policy_not_explicit','observed_report_policy':'left-open right-closed'})
                        actual=alternative;convention='left-open right-closed; public endpoint convention unresolved'
                    elif 'sample_count' in row:fail('raw_statistics_sample_count',filename,pointer+'/sample_count',expected=actual['sample_count'],actual=row['sample_count'])
                for field in ('mean_Ux_m_s','mean_U_m_s','resolved_tke_m2_s2'):
                    if field not in row:continue
                    expected=actual[field]
                    if field=='mean_U_m_s' and len(shape(row[field]))==1:expected=[x for v in expected for x in v]
                    diff=difference(row[field],expected,pointer+'/'+field);count+=1
                    if diff:fail('reported_raw_statistics_mismatch',filename,pointer+'/'+field,raw_file=path,**diff)
                observations.append({'case_id':identity,'raw_path':path,'window_s':window,'sample_count':actual['sample_count'],
                    'endpoint_policy':convention,'statistics':'arithmetic component means; resolved TKE = one half the sum of population component variances'})
            except (ValueError,KeyError,TypeError,IndexError) as exc:fail('raw_statistics_unresolved',path,actual=str(exc))
    return count


class ReadBudget:
    def __init__(self, reader):
        self.reader = reader; self.cache = {}; self.bytes = 0
    def read(self, root, name):
        key = (str(root), str(name))
        if key not in self.cache:
            if len(self.cache) >= MAX_FILES: raise ValueError('consistency_file_budget_exhausted')
            item = self.reader(root, name, max_bytes=min(MAX_FILE, MAX_TOTAL-self.bytes))
            if item.get('omitted'): raise ValueError('raw_unavailable:' + str(item['omitted']))
            self.bytes += len(item['text'].encode('utf-8'))
            if self.bytes > MAX_TOTAL: raise ValueError('consistency_byte_budget_exhausted')
            self.cache[key] = item
        return self.cache[key]
    def json(self, root, name): return json.loads(self.read(root, name)['text'])


def verify_evidence(root, canonical, reader, case_paths=None, include_reports=True):
    root, canonical = Path(root), Path(canonical)
    reads = ReadBudget(reader); findings = []; observations = []; unknown = []; finding_index={}
    def fail(code, file, pointer='', **detail):
        identity=(code,str(file),re.sub(r'/\d+(?=/|$)','/*',pointer))
        if identity in finding_index:
            previous=finding_index[identity];previous['occurrences']=previous.get('occurrences',1)+1
        else:
            finding={'code':code,'file':str(file),'pointer':pointer,**detail}
            finding_index[identity]=finding;findings.append(finding)
    def optional(name, source=canonical):
        try: return reads.json(source, name)
        except (ValueError, KeyError, TypeError): return {}
    report = optional('reporting_contract.json'); form = optional('submission_format.json')
    acceptance = optional('acceptance_contract.json')
    contracts = []
    for p in sorted((canonical/'local_assets').glob('*contract*.json')):
        c = optional('local_assets/' + p.name)
        if c: contracts.append(('local_assets/' + p.name, c))
    declarations = [('reporting_contract.json', report)] + contracts
    variants = {}
    for _, c in declarations:
        for _, _, ob in walk(c):
            if ob.get('case_id') and ob.get('variant_key'): variants[ob['variant_key']] = ob['case_id']
    sole_case = report.get('expected_solver_case_ids', [])
    sole_case = sole_case[0] if len(sole_case) == 1 else None
    documents = {}; result_rows = []
    if include_reports:
        for name, schema in form.get('documents', {}).items():
            if not any(k in name for k in ('result', 'simulation_metrics')): continue
            try: value = reads.json(root, name)
            except (ValueError, KeyError) as exc:
                fail('report_unavailable',name,actual=str(exc));continue
            documents[name] = value
            for pattern, obj in schema.get('objects', {}).items():
                kinds = obj.get('container_types', [])
                kind = obj.get('container_type')
                if kind not in ('list','object'):kind = kinds[0] if len(kinds)==1 else None
                if kind:
                    for pointer, actual in select(value, pattern.split('/') if pattern else []):
                        good = isinstance(actual, list if kind=='list' else dict)
                        if not good: fail('public_container_type',name,pointer,expected=kind,actual=type(actual).__name__)
            for pointer, case, obj in walk(value, case=sole_case, variants=variants):
                if 'case_id' in obj or any(isinstance(obj.get(k),list) for fields in ROLE_FIELDS.values() for k in fields) or any(isinstance(obj.get(k),list) for k in ('mean_Ux_m_s','mean_U_m_s','resolved_tke_m2_s2','probe_concentration')):
                    result_rows.append((name,pointer,case,obj))
        # Explicit public shape declarations, even when nested in result_contract.
        for source, c in declarations:
            for _, _, ob in walk(c):
                specs = dict(ob.get('array_shapes', {})) if isinstance(ob.get('array_shapes'), dict) else {}
                for k, v in ob.items():
                    if k.endswith('_shape') and isinstance(v,list):specs[ALIASES.get(k[:-6],k[:-6])] = v
                if not specs:continue
                target = ob.get('path')
                for filename, pointer, case, row in result_rows:
                    if target and filename != target:continue
                    for field, expected in specs.items():
                        if field not in row:continue
                        if shape(row[field]) != expected:fail('declared_array_shape',filename,pointer+'/'+field,expected=expected,actual=shape(row[field]),source=source)
                        elif not numeric(row[field]):fail('nonfinite_array',filename,pointer+'/'+field)
    handled_manifests = set(); raw_comparisons = 0
    for source, contract in declarations:
        manifest = contract.get('required_manifest')
        if not isinstance(manifest,str) or manifest in handled_manifests:continue
        handled_manifests.add(manifest)
        # Clarification documents augment the same manifest's public declarations.
        combined = dict(contract)
        for _, other in declarations:
            if other.get('required_manifest') == manifest:
                for k, v in other.items(): combined.setdefault(k,v)
        contract = combined
        try: evidence = reads.json(root,manifest)
        except (ValueError, KeyError) as exc:
            if include_reports:fail('evidence_manifest_unavailable',manifest,actual=str(exc))
            continue
        groups = {}
        for _, case, row in walk(evidence, case=sole_case or contract.get('case_id'), variants=variants):
            for role, item in row.items():
                if isinstance(item,dict) and isinstance(item.get('path'),str):
                    group=groups.setdefault(case,{})
                    if role in group and group[role]!=item:
                        fail('ambiguous_case_evidence',manifest,str(case)+'/'+role)
                    else:group[role]=item
        target_name = contract.get('profile_target_csv') or contract.get('profile_source') or contract.get('neutral_profile_csv')
        targets = []
        if target_name:
            try: targets = list(csv.DictReader(io.StringIO(reads.read(canonical,target_name)['text'])))
            except (ValueError, KeyError):unknown.append({'source':source,'reason':'public_profile_targets_unavailable'})
        expected_points = contract.get('profile_points_m')
        if not expected_points and targets:
            xy=acceptance.get('inlet_probe_xy_m')
            try:
                expected_points=[[float(r['x_m']),float(r['y_m']),float(r['z_m'])] for r in targets] if 'x_m' in targets[0] else [[xy[0],xy[1],float(r['z_m'])] for r in targets] if xy else None
            except (KeyError,TypeError,ValueError):pass
        iterations = next((contract[k] for k in ('profile_iterations','profile_sample_iterations','response_sample_iterations') if isinstance(contract.get(k),list)),None)
        for case, roles in groups.items():
            matching = [r for r in result_rows if r[2] == case]
            if not matching and case is None and len(result_rows)==1:matching=result_rows
            variant = next((v.get('variant_key') for v in contract.get('cases',[]) if v.get('case_id')==case),None)
            control = None; control_path = roles.get('controlDict',{}).get('path')
            if control_path:
                try:control=parse_dictionary(reads.read(root,control_path)['text'])
                except (ValueError,KeyError) as exc:fail('sampling_dictionary_unresolved',control_path,actual=str(exc))
            series = {}
            for role, fields in ROLE_FIELDS.items():
                item = roles.get(role)
                if not item:continue
                path=item['path']; vector=role in ('probe_U','response_U')
                inlet = role in ('probe_U','probe_k','probe_omega') and bool(expected_points or targets)
                count = len(expected_points) if inlet and expected_points else None
                try:
                    raw_item=reads.read(root,path)
                    if item.get('sha256') != raw_item['sha256']:fail('raw_evidence_hash',path,expected=item.get('sha256'),actual=raw_item['sha256'])
                    raw=probe_series(raw_item['text'],vector,count)
                except (ValueError,KeyError) as exc:
                    fail('raw_probe_parse',path,actual=str(exc));continue
                series[role]=raw
                coords=raw['coordinates']
                if inlet and expected_points and coords:
                    if coords != expected_points:
                        same_shape=shape(coords)==shape(expected_points)
                        delta=max(abs(x-y) for a,b in zip(coords,expected_points) for x,y in zip(a,b)) if same_shape else None
                        # Identity of declared probe locations is separate from a physical RMSE gate.
                        fail('public_probe_coordinate_identity',path,expected='declared public coordinate values',max_abs_delta_m=delta,
                             instruction='Inspect actual sampling geometry/precision; preserve the original raw header. Do not edit raw evidence to claim agreement.')
                if control is not None:
                    parts=Path(path).parts
                    function=parts[parts.index('postProcessing')+1] if 'postProcessing' in parts and parts.index('postProcessing')+1<len(parts) else None
                    functions=control.get('functions',{})
                    settings=functions.get(function) if isinstance(functions,dict) and function else None
                    if settings:
                        observation={'case_id':case,'file':control_path,'function':function,'role':role,
                            'type':settings.get('type'),'patch':settings.get('patch'),'patches':settings.get('patches'),
                            'interpolationScheme':settings.get('interpolationScheme'),'raw_path':path,'raw_samples':len(raw['times'])}
                        observations.append(observation)
                        explicit_patch=any('patchProbes' in str(v) for v in contract.get('raw_observables',[]))
                        if inlet and settings.get('type')=='probes':
                            item_={'code':'inlet_internal_field_sampling','file':control_path,'function':function,
                                'instruction':'probes samples internal fields. For a boundary observable use actual patch-field sampling; report the recorded observable, never substitute 0/ boundary settings.'}
                            if explicit_patch:fail(item_.pop('code'),control_path,function,**{k:v for k,v in item_.items() if k not in ('file','function')})
                            else:unknown.append(item_)
                        if inlet and settings.get('type')=='patchProbes' and settings.get('patches',settings.get('patch')) not in ('inlet','( inlet )'):
                            fail('inlet_patch_selection_unresolved',control_path,function,actual=settings.get('patches',settings.get('patch')))
                for filename,pointer,_,row in matching:
                    field=next((f for f in fields if f in row),None)
                    if field is None:continue
                    expected=raw['values'];stated=row[field]
                    # Some contracts request only the final profile, not the time series.
                    if shape(stated)==shape(expected[-1]) and contract.get('profile_iteration_count') is None and 'response_sample_count' not in contract:
                        expected=expected[-1]
                    diff=difference(stated,expected,pointer+'/'+field);raw_comparisons+=1
                    if diff:fail('report_raw_mismatch',filename,pointer+'/'+field,raw_file=path,**diff)
                    time_key=next((k for k in ('iterations','time_s') if k in row),None)
                    if time_key:
                        diff=difference(row[time_key],raw['times'],pointer+'/'+time_key)
                        if diff:fail('report_raw_times',filename,pointer+'/'+time_key,raw_file=path,**diff)
                    if inlet and iterations and raw['times']!=iterations:
                        fail('public_sample_iterations',path,expected=iterations,actual=raw['times'][:30],actual_count=len(raw['times']))
                # Public layer-weighted inlet metrics from actual recorded samples.
                if inlet and targets and len(targets)==len(raw['values'][-1]):
                    quantity={'probe_U':'U','probe_k':'k','probe_omega':'omega'}[role]
                    columns=({'U':['target_Ux_m_s',str(variant)+'_Ux_m_s'],
                              'k':['target_k_m2_s2','frozen_k_m2_s2'],
                              'omega':['target_omega_s_1','frozen_omega_s_1']})[quantity]
                    column=next((k for k in columns if k in targets[0]),None)
                    if column and 'layer_thickness_m' in targets[0]:
                        try:
                            weights=[float(r['layer_thickness_m']) for r in targets]
                            values=[v[0] for v in raw['values'][-1]] if vector else raw['values'][-1]
                            if not numeric(weights) or any(v<=0 for v in weights):raise ValueError('nonpositive_profile_weights')
                            rmse=math.sqrt(sum(w*(v-float(t[column]))**2 for w,v,t in zip(weights,values,targets))/sum(weights))
                            metric={'U':'inlet_U_rmse_m_s','k':'inlet_k_rmse_m2_s2','omega':'inlet_omega_rmse_s_1'}[quantity]
                            limit=contract.get('frozen_gates',{}).get('max_'+metric)
                            observations.append({'case_id':case,'raw_path':path,'metric':metric,'value':rmse,'public_limit':limit,'public_target':target_name,'aggregation':'layer-thickness weighted; final recorded sample'})
                            if limit is not None and rmse>limit:fail('raw_inlet_public_gate',path,metric,actual=rmse,expected_max=limit)
                            for filename,pointer,_,row in matching:
                                for child_pointer,_,child in walk(row,pointer):
                                    for key in (metric,'inlet_profile_rmse_ms') if quantity=='U' else (metric,):
                                        if key in child and isinstance(child[key],(float,int)):
                                            if not math.isfinite(child[key]) or not math.isclose(child[key],rmse,rel_tol=1e-7,abs_tol=1e-10):
                                                fail('reported_public_metric_mismatch',filename,child_pointer+'/'+key,raw_file=path,submitted=child[key],raw=rmse)
                        except (ValueError,TypeError,KeyError) as exc:fail('public_profile_recompute_unresolved',path,actual=str(exc))
            if series:
                timelines=[(k,v['times']) for k,v in series.items()]
                if any(t!=timelines[0][1] for _,t in timelines[1:]):fail('raw_field_time_alignment',manifest,str(case),roles=[k for k,_ in timelines])
    if include_reports:
        raw_comparisons+=statistical_consistency(root,report,documents,reads,fail,observations,unknown)
    if case_paths:
        for case_path in case_paths[:24]:
            filename=str(case_path).rstrip('/')+'/system/controlDict'
            try:
                control=parse_dictionary(reads.read(root,filename)['text'])
                for name,settings in control.get('functions',{}).items():
                    if not isinstance(settings,dict):continue
                    observations.append({'file':filename,'function':name,**{k:settings.get(k) for k in ('type','fields','patch','patches','interpolationScheme','writeControl','writeInterval')}})
                    if 'inlet' in name.lower() and settings.get('type')=='probes':
                        unknown.append({'code':'inlet_internal_field_sampling','file':filename,'function':name,
                            'instruction':'This samples the internal field; check the public observable before solving. Boundary settings cannot replace emitted raw probe values.'})
            except (ValueError,KeyError,TypeError) as exc:fail('sampling_dictionary_unresolved',filename,actual=str(exc))
    return {'errors':sorted(set(f"{v['file']}:{v['pointer']}:{v['code']}" for v in findings)),
        'findings':findings[:80],'finding_count':len(findings),'observations':observations[:160],
        'unknown_or_advisories':unknown[:40],'raw_report_comparisons':raw_comparisons,
        'read_files':len(reads.cache),'read_bytes':reads.bytes,'workspace_writes':0,
        'scope':'Public schema/container/shape, actual sampling object, strict raw row parsing, declared coordinates, raw-to-report arrays and public weighted inlet gates. Unsupported observables remain unknown. No grader, hidden reference, score or physical-threshold changes.'}
