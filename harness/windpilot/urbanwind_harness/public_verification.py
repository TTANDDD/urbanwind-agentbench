"""Deterministic checks against canonical PUBLIC inputs, never private answers.

All workspace reads are supplied by the runner's bounded no-follow reader.
Checks report observations and uncertainty; they never rewrite an artifact.
"""
from __future__ import annotations
import csv
import io
import json
import math
import re
from .acceptance import verify_acceptance


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def shape(value):
    if not isinstance(value, list):
        return []
    if not value:
        return [0]
    dimensions = [shape(v) for v in value]
    return [len(value)] + (dimensions[0] if all(v == dimensions[0] for v in dimensions) else ['ragged'])


def numeric_stats(value):
    numbers, nonfinite = [], 0
    def visit(v):
        nonlocal nonfinite
        if isinstance(v, list):
            for x in v: visit(x)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if math.isfinite(v): numbers.append(v)
            else: nonfinite += 1
    visit(value)
    return {'shape': shape(value), 'finite_count': len(numbers), 'nonfinite_count': nonfinite,
            'min': min(numbers) if numbers else None, 'max': max(numbers) if numbers else None}


def compact_json(value, depth=0):
    if isinstance(value, float) and not math.isfinite(value):
        return {'invalid_number': str(value)}
    if depth > 8:
        return {'summary': 'depth_limit', 'type': type(value).__name__}
    if isinstance(value, dict):
        return {k: compact_json(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > 24 and not any(isinstance(v, dict) for v in value[:25]):
            return {'array_summary': numeric_stats(value), 'first': compact_json(value[:1], depth + 1), 'last': compact_json(value[-1:], depth + 1)}
        return [compact_json(v, depth + 1) for v in value]
    return value


def read_json(reader, root, name):
    item = reader(root, name, max_bytes=128 * 1024 * 1024)
    if item.get('omitted'):
        return None
    try:
        return json.loads(item['text'])
    except (ValueError, KeyError):
        return None


def summarize_artifact(reader, root, name):
    item = reader(root, name, max_bytes=128 * 1024 * 1024)
    if item.get('omitted'):
        return item
    if len(item['text'].encode()) <= 48 * 1024:
        return item
    try:
        summary = compact_json(json.loads(item['text']))
        encoded = json.dumps(summary, ensure_ascii=False)
        if len(encoded.encode()) <= 96 * 1024:
            return {'path': name, 'sha256': item['sha256'], 'structured_summary': summary,
                    'scope': 'All dictionary fields retained; long arrays summarized with actual shapes/finite counts/extrema. Raw bytes kept in workspace.'}
    except ValueError:
        pass
    return {'path': name, 'sha256': item['sha256'], 'bytes': len(item['text'].encode()),
            'head': item['text'][:16000], 'tail': item['text'][-16000:],
            'scope': 'Explicit head/tail excerpt; middle is not reviewed.'}


NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
PROBE = re.compile(r'^\s*#\s*Probe\s+(\d+)\s*\(([^)]+)\)', re.M)


def fatal_log_evidence(text):
    """Keep real fatal events; the exact OpenFOAM trapFpe startup banner is benign."""
    banner = re.compile(r'^\s*(?:\[\d+\]\s*)?trapFpe:\s*Floating point exception trapping enabled\s*\(FOAM_SIGFPE\)\s*\.?\s*$', re.I)
    events, ignored = [], 0
    for number, line in enumerate(text.splitlines(), 1):
        if banner.fullmatch(line):
            ignored += 1
            continue
        if re.search(r'FOAM FATAL|Floating point exception|Segmentation fault', line, re.I):
            events.append({'line': number, 'text': line[:500]})
    return {'fatal_seen': bool(events), 'fatal_event_count': len(events),
            'fatal_event_examples': events[:8], 'benign_trapfpe_banner_count': ignored}


def log_observation(text):
    times = re.findall(r'^\s*Time\s*=\s*(' + NUMBER + r')\s*$', text, re.M)
    continuity = [float(v) for v in re.findall(r'cumulative = (' + NUMBER + ')', text)]
    cells = re.findall(r'^\s*cells:\s*(\d+)\s*$', text, re.M)
    ranks = re.findall(r'^\s*nProcs\s*:\s*(\d+)\s*$', text, re.M)
    return {'final_time': float(times[-1]) if times else None,
            'time_record_count': len(times),
            'max_abs_cumulative_continuity': max(map(abs, continuity)) if continuity else None,
            'continuity_record_count': len(continuity),
            'end_seen': bool(re.search(r'^\s*End\s*$', text, re.M)),
            'parallel_finalisation_seen': 'Finalising parallel run' in text,
            'cell_count': int(cells[-1]) if cells else None,
            'mesh_ok_seen': bool(re.search(r'^\s*Mesh OK\.\s*$', text, re.M)),
            'nprocs': int(ranks[-1]) if ranks else None,
            **fatal_log_evidence(text),
            'scope': 'Parsed raw log observations; completion text is not a captured solver exit code.'}


def probe_observation(text):
    coordinates = [[float(x) for x in re.findall(NUMBER, coords)] for _, coords in PROBE.findall(text)]
    times = []
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            try: times.append(float(line.split()[0]))
            except ValueError: pass
    return {'coordinates': coordinates, 'sample_count': len(times), 'first_time': times[0] if times else None,
            'last_time': times[-1] if times else None,
            'strictly_increasing_times': all(a < b for a, b in zip(times, times[1:]))}


def probe_rows(text, vector=False):
    times, rows = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        tokens = re.findall(NUMBER, line)
        if not tokens: continue
        values = [float(x) for x in tokens]
        times.append(values[0])
        rows.append([values[i:i+3] for i in range(1,len(values),3)] if vector else values[1:])
    return times, rows


def half_windows(times, velocity, temperatures, windows):
    if len(times) != len(velocity) or len(times) != len(temperatures):
        raise ValueError('unaligned_samples')
    output = {}
    for start, end in windows:
        vertical, thermal = [], []
        for time, u, t in zip(times, velocity, temperatures):
            if start <= time <= end:
                vertical.extend(max(v[2],0.0) for v in u)
                thermal.extend(t)
        if not vertical or not thermal: raise ValueError('empty_window')
        output[f'{start}_{end}']={'vertical_exchange_m_s':sum(vertical)/len(vertical),'temperature_k':sum(thermal)/len(thermal)}
    return output


def verify_public(root, canonical, reader):
    errors, warnings, observed = [], [], []
    report = read_json(reader, canonical, 'reporting_contract.json') or {}
    form = read_json(reader, canonical, 'submission_format.json') or {}
    contracts = []
    for path in sorted((canonical / 'local_assets').glob('*contract.json')):
        value = read_json(reader, canonical, 'local_assets/' + path.name)
        if isinstance(value, dict): contracts.append((path.name, value))
    documents = {name: read_json(reader, root, name) for name in form.get('documents', {})}
    for name, contract in contracts:
        gates = contract.get('frozen_gates', {})
        simulation = contract.get('simulation_metrics', {})
        # Some public contracts store the output schema in the reporting copy.
        for obj in walk(report):
            if obj.get('task_id') == contract.get('task_id') and isinstance(obj.get('simulation_metrics'), dict):
                simulation = obj['simulation_metrics']
                if simulation.get('path'):
                    value = documents.get(simulation['path'])
                    if value is None: value = read_json(reader, root, simulation['path'])
                    for row in walk(value):
                        if 'case_id' not in row: continue
                        for key, expected in simulation.get('array_shapes', {}).items():
                            if key not in row or shape(row[key]) != expected:
                                errors.append(f"{simulation['path']}:{row['case_id']}:{key}:shape_expected_{expected}")
                            elif numeric_stats(row[key])['nonfinite_count']:
                                errors.append(f"{simulation['path']}:{row['case_id']}:{key}:nonfinite")
        for filename, value in documents.items():
            for row in walk(value):
                label = f"{filename}:{row.get('case_id', 'root')}"
                if isinstance(row.get('last20_max_initial_residuals'), dict) and 'max_last20_initial_residual_full' in gates:
                    actual = row['last20_max_initial_residuals']
                    threshold = gates['max_last20_initial_residual_full']
                    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v > threshold for v in actual.values()):
                        errors.append(label + ':reported_residual_above_public_gate')
                if isinstance(row.get('max_abs_cumulative_continuity'), (int, float)) and 'max_abs_cumulative_continuity' in gates:
                    if not math.isfinite(row['max_abs_cumulative_continuity']) or abs(row['max_abs_cumulative_continuity']) > gates['max_abs_cumulative_continuity']:
                        errors.append(label + ':reported_continuity_above_public_gate')
        expected_points = contract.get('response_points_m')
        inlet_points = contract.get('profile_points_m')
        if inlet_points is None and isinstance(contract.get('profile_target_csv'),str):
            # Public target heights; coordinates are a declared input convention,
            # not a private solver response. Publication adds the convention.
            clarification=read_json(reader,canonical,'acceptance_contract.json') or {}
            xy=clarification.get('inlet_probe_xy_m')
            if xy:
                item=reader(canonical,contract['profile_target_csv'])
                if not item.get('omitted'):
                    try:inlet_points=[[xy[0],xy[1],float(r['z_m'])] for r in csv.DictReader(io.StringIO(item['text']))]
                    except (KeyError,ValueError):errors.append(name+':public_inlet_coordinates_unresolved')
        csv_name = contract.get('response_probe_csv') or contract.get('response_probes')
        if expected_points is None and isinstance(csv_name, str):
            item = reader(canonical, csv_name)
            if not item.get('omitted'):
                try:
                    expected_points = [[float(row[k]) for k in ('x_m', 'y_m', 'z_m')] for row in csv.DictReader(io.StringIO(item['text']))]
                except (KeyError, ValueError): warnings.append(name + ':public_probe_csv_not_resolved')
        manifest = contract.get('required_manifest')
        evidence = read_json(reader, root, manifest) if isinstance(manifest, str) else None
        if contract.get('half_windows_s'):
            for obj in walk(evidence):
                if not all(isinstance(obj.get(role),dict) for role in ('probe_U','probe_T')):continue
                u = reader(root,obj['probe_U'].get('path',''),max_bytes=64*1024*1024)
                t = reader(root,obj['probe_T'].get('path',''),max_bytes=64*1024*1024)
                if u.get('omitted') or t.get('omitted'):continue
                try:
                    times, us=probe_rows(u['text'],True); ttimes,ts=probe_rows(t['text'])
                    if times != ttimes:raise ValueError('velocity_temperature_times_differ')
                    result=half_windows(times,us,ts,contract['half_windows_s'])
                    observed.append({'case_id':obj.get('case_id'),'independently_recomputed_half_windows':result,'source':'registered raw U/T; inclusive endpoints; positive vertical exchange'})
                    value=read_json(reader,root,contract.get('required_result',''))
                    submitted_case = value.get('case_result', {}) if isinstance(value, dict) else {}
                    windows = list(result.values())
                    for field, metric in [('vertical_exchange_drift_m_s','vertical_exchange_m_s'),('temperature_drift_k','temperature_k')]:
                        actual = abs(windows[1][metric] - windows[0][metric])
                        stated = submitted_case.get(field)
                        if not isinstance(stated,(int,float)) or not math.isfinite(stated) or abs(stated-actual)>max(1e-9,abs(actual)*1e-7):
                            errors.append(f"{contract['required_result']}:{field}:raw_recomputation_mismatch")
                    for submitted in walk(value):
                        if not isinstance(submitted.get('half_window_means'),dict):continue
                        for window,metrics in result.items():
                            for metric,actual in metrics.items():
                                stated=submitted['half_window_means'].get(window,{}).get(metric)
                                if not isinstance(stated,(int,float)) or not math.isfinite(stated) or abs(stated-actual)>1e-7*max(1,abs(actual)):
                                    errors.append(f"{contract['required_result']}:{window}:{metric}:raw_recomputation_mismatch")
                except (ValueError,IndexError,TypeError) as exc:
                    errors.append('public_half_window_check:'+str(exc))
        seen = set()
        for obj in walk(evidence):
            for role, entry in obj.items():
                if not isinstance(entry, dict) or not isinstance(entry.get('path'), str): continue
                path = entry['path']
                if path in seen: continue
                seen.add(path)
                if 'probe' in role.lower() or role == 'response_U':
                    item = reader(root, path, max_bytes=64 * 1024 * 1024)
                    if item.get('omitted'):
                        warnings.append(path + ':raw_probe_unavailable'); continue
                    record = probe_observation(item['text'])
                    record.update({'path': path, 'role': role, 'sha256': item['sha256']})
                    coords = record['coordinates']
                    if not coords: warnings.append(path + ':no_probe_coordinate_header')
                    if inlet_points and role in ('probe_U', 'probe_k', 'probe_omega'):
                        matches = len(coords) == len(inlet_points) and all(len(a) == 3 and all(abs(x-y)<=1e-8 for x,y in zip(a,b)) for a,b in zip(coords,inlet_points))
                        record['public_inlet_coordinate_match'] = matches
                        record['expected_inlet_coordinate_count'] = len(inlet_points)
                        if not matches:
                            errors.append(path + ':coordinates_differ_from_public_inlet_probes')
                    if expected_points and role in ('response_U', 'probe_U', 'probe_T') and (role == 'response_U' or contract.get('response_probes')):
                        matches = len(coords) == len(expected_points) and all(len(a) == 3 and all(abs(x-y)<=1e-8 for x,y in zip(a,b)) for a,b in zip(coords,expected_points))
                        record['public_response_coordinate_match'] = matches
                        record['expected_response_coordinate_count'] = len(expected_points)
                        if not matches:
                            errors.append(path + ':coordinates_differ_from_public_response_probes')
                    if not record['strictly_increasing_times']: errors.append(path + ':non_increasing_sample_times')
                    observed.append(record)
                elif role == 'solver_log':
                    item = reader(root, path, max_bytes=128 * 1024 * 1024)
                    if item.get('omitted'):
                        warnings.append(path + ':raw_solver_log_unavailable'); continue
                    text = item['text']
                    residuals = {}
                    # Last 20 observations PER equation, not last 20 global log lines.
                    for field, number in re.findall(r'Solving for (\w+), Initial residual = (' + NUMBER + ')', text):
                        residuals.setdefault(field, []).append(float(number))
                    maxima = {k:max(v[-20:]) for k,v in residuals.items() if v}
                    threshold = gates.get('max_last20_initial_residual_full')
                    if threshold is not None:
                        required=('Ux','Uy','Uz','k','omega','p')
                        missing=[k for k in required if len(residuals.get(k,[]))<20]
                        if missing:errors.append(path+':missing_last20_residual_fields:'+','.join(missing))
                    if threshold is not None and any(v > threshold for v in maxima.values()):
                        errors.append(path + ':raw_last20_residual_above_public_gate')
                    if re.search(r'\b(?:nan|inf)\b',text,re.I) or 'FOAM FATAL' in text:
                        errors.append(path+':nonfinite_or_fatal_solver_log')
                    continuity=[float(v) for v in re.findall(r'cumulative = ('+NUMBER+')',text)]
                    limit=gates.get('max_abs_cumulative_continuity')
                    if limit is not None and (not continuity or max(map(abs,continuity))>limit):
                        errors.append(path+':raw_continuity_missing_or_above_public_gate')
                    observed.append({'path':path,'role':role,'sha256':item['sha256'],
                        'last20_initial_residual_by_field':maxima,
                        'last20_residual_threshold':threshold,
                        'max_abs_cumulative_continuity_threshold':limit,
                        'required_final_iteration':gates.get('required_final_iteration'),
                        **log_observation(text)})
                elif role == 'checkMesh_log':
                    item = reader(root, path, max_bytes=128 * 1024 * 1024)
                    if item.get('omitted'):
                        warnings.append(path + ':raw_checkMesh_log_unavailable'); continue
                    observed.append({'path':path,'role':role,'sha256':item['sha256'],
                        'required_cell_count':gates.get('required_cell_count'),
                        **log_observation(item['text'])})
    from .evidence_consistency import verify_evidence
    consistency=verify_evidence(root,canonical,reader)
    errors+=consistency['errors']
    acceptance=verify_acceptance(root,canonical,reader)
    errors+=acceptance['errors']
    from .primary_metrics import verify_primary_metrics
    primary=verify_primary_metrics(root,canonical,reader)
    errors+=primary['errors']
    return {'errors': sorted(set(errors)), 'warnings': sorted(set(warnings)), 'raw_observations': observed,'acceptance':acceptance,
            'evidence_consistency':consistency,
            'primary_metric_consistency':primary,
            'scope': 'Public shapes, finite values, declared thresholds and raw evidence only. Unsupported metric formulas/time-window semantics require agent recomputation; no hidden-array certification.'}
