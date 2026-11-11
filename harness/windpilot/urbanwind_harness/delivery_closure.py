"""Public-input delivery closure. No evaluator, reference, model, or solver access.

Repairs only missing task identity and lossless integer count serialization in
already existing required output documents. Raw evidence and physical gates are
never synthesized. All changes have byte-preserving before/after receipts.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
from pathlib import Path

PUBLISHER = {'task_spec.json', 'reporting_contract.json', 'case_plan.json',
             'submission_format.json', 'task_public_guide.json', 'acceptance_contract.json',
             'input_manifest.json', 'runtime_contract.json'}
COUNT_FIELDS = {'sample_count', 'n_samples', 'number_of_samples'}
MAX_JSON_BYTES = 128 * 1024 * 1024


def walk(value, pointer=''):
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            yield from walk(child, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, pointer + '/' + str(index))


def safe_path(root, name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('invalid_relative_path')
    if name.startswith('/home/user/Desktop/'):
        name = name[len('/home/user/Desktop/'):]
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or ':' in name:
        raise ValueError('path_outside_workspace')
    root = Path(root).resolve()
    target = root.joinpath(path).resolve()
    if target != root and root not in target.parents:
        raise ValueError('path_outside_workspace')
    return target


def read_json(root, name):
    target = safe_path(root, name)
    if target.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('json_exceeds_explicit_read_budget')
    return json.loads(target.read_text(encoding='utf-8'))


def public_view(canonical):
    """Project only public declarations, not historical failure IDs or answers."""
    canonical = Path(canonical)
    sources = {}
    for name in ('task_spec.json', 'reporting_contract.json', 'submission_format.json'):
        try:
            sources[name] = read_json(canonical, name)
        except FileNotFoundError:
            sources[name] = {}
    report, spec, form = (sources[n] for n in ('reporting_contract.json', 'task_spec.json', 'submission_format.json'))
    identity = report.get('task_id') or spec.get('task_id')
    if report.get('task_id') and spec.get('task_id') and report['task_id'] != spec['task_id']:
        raise ValueError('conflicting_public_task_identity')
    documents = set(form.get('documents', {}))
    for item in spec.get('required_artifacts', []):
        name = item.get('path') if isinstance(item, dict) else item
        if isinstance(name, str) and name.endswith('.json'):
            documents.add(name)
    for key in ('metrics', 'evidence_manifest', 'run_manifest'):
        if isinstance(report.get(key), str):
            documents.add(report[key])
    requirements = []
    for source, value in list(sources.items()):
        for pointer, obj in walk(value):
            for key in ('required_manifest', 'required_result', 'manifest_path'):
                if isinstance(obj.get(key), str) and obj[key].endswith('.json'):
                    documents.add(obj[key])
            if isinstance(obj.get('path'), str) and obj['path'].endswith('.json') and ('result_contract' in pointer or 'simulation' in pointer):
                documents.add(obj['path'])
            selected = {k: v for k, v in obj.items() if k in (
                'required_fields', 'exact_fields', 'array_shapes', 'minimum_sample_count',
                'minimum_probe_samples', 'statistical_window_s', 'half_windows_s',
                'solver', 'frozen_gates', 'metric_reconstruction', 'task_specific_metric_formula',
                'path', 'required_manifest', 'required_result') or k.endswith('_shape')}
            if selected:
                # Full numerical response arrays are not loaded by this helper.
                requirements.append({'source': source, 'pointer': pointer, 'requirements': selected})
    documents = sorted(n for n in documents if isinstance(n, str) and n.endswith('.json')
                       and n not in PUBLISHER and not n.startswith('local_assets/'))
    for name in documents:
        safe_path(canonical, name)
    return {'task_id': identity, 'documents': documents,
            'case_ids': report.get('expected_case_ids', []), 'public_requirements': requirements,
            'scope': 'Existing public task files only; no private grader or reference. Preserve all numerical gates.'}


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare_delivery(root, canonical, receipt_dir):
    """One atomic write per changed document; byte backups are never overwritten."""
    root, receipt_dir = Path(root), Path(receipt_dir)
    view = public_view(canonical)
    errors, changes, documents, original = [], [], {}, {}
    for name in view['documents']:
        try:
            target = safe_path(root, name)
            data = target.read_bytes()
            if len(data) > MAX_JSON_BYTES:
                raise ValueError('json_exceeds_explicit_read_budget')
            obj = json.loads(data)
            if not isinstance(obj, dict):
                errors.append(name + ':required_output_root_not_object')
                continue
            # Preserve malformed/nonfinite output unchanged and report it.
            _encoded(obj)
            documents[name], original[name] = copy.deepcopy(obj), data
            if view['task_id']:
                if 'task_id' not in obj:
                    documents[name]['task_id'] = view['task_id']
                    changes.append({'file': name, 'pointer': '/task_id', 'before': 'ABSENT',
                                    'after': view['task_id'], 'source': 'public_task_identity'})
                elif obj['task_id'] != view['task_id']:
                    errors.append(name + ':/task_id:conflicting_identity_not_repaired')
            for pointer, row in walk(documents[name]):
                for key in COUNT_FIELDS & row.keys():
                    value = row[key]
                    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and 0 <= value <= 2**53:
                        row[key] = int(value)
                        changes.append({'file': name, 'pointer': pointer + '/' + key,
                                        'before': value, 'after': row[key], 'source': 'lossless_count_serialization'})
                    elif not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        errors.append(name + ':' + pointer + '/' + key + ':invalid_count_not_repaired')
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            errors.append(name + ':' + type(exc).__name__ + ':' + str(exc))
    # Update only references which matched actual pre-repair bytes. Wrong hashes
    # remain errors; they are not laundered into valid provenance.
    encoded, visiting = {}, set()
    def encode(name):
        if name in encoded:
            return encoded[name]
        if name in visiting:
            raise ValueError('cyclic_document_hash_dependencies')
        visiting.add(name)
        for pointer, row in walk(documents[name]):
            if not isinstance(row.get('path'), str) or not isinstance(row.get('sha256'), str):
                continue
            reference = row['path']
            if reference not in documents or reference == name:
                continue
            old_hash, stated = _sha(original[reference]), row['sha256']
            new_hash = _sha(encode(reference))
            if stated != old_hash:
                errors.append(name + ':' + pointer + ':stale_hash_not_repaired')
            elif new_hash != old_hash:
                row['sha256'] = new_hash
                changes.append({'file': name, 'pointer': pointer + '/sha256', 'before': stated,
                                'after': new_hash, 'source': 'matched_original_bytes_then_metadata_change'})
        visiting.remove(name)
        # Retain original bytes when semantic content did not change.
        encoded[name] = original[name] if documents[name] == json.loads(original[name]) and not any(c['file'] == name for c in changes) else _encoded(documents[name])
        return encoded[name]
    try:
        for name in documents:
            encode(name)
    except (ValueError, RecursionError) as exc:
        errors.append(str(exc))
        encoded = {}  # Reject the whole write set for an ambiguous hash graph.
    written = []
    # Identity conflicts or malformed documents do not prevent independent safe
    # repairs in other files; the errors still block readiness.
    for name, after in encoded.items():
        before = original[name]
        if after == before:
            continue
        receipt_dir.mkdir(parents=True, exist_ok=True)
        backup = receipt_dir / (_sha(before) + '.before.json')
        if not backup.exists():
            with backup.open('xb') as handle:
                handle.write(before)
        target = safe_path(root, name)
        temporary = target.with_name(target.name + '.closure.tmp')
        with temporary.open('xb') as handle:
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        written.append({'file': name, 'before_sha256': _sha(before), 'after_sha256': _sha(after),
                        'backup': str(backup), 'before_bytes': len(before), 'after_bytes': len(after)})
    result = {'errors': sorted(set(errors)), 'changes': changes, 'writes': written,
              'required_documents': view['documents'], 'physical_values_or_gates_synthesized': False,
              'scope': 'Missing public root identity and lossless count types only; raw solver evidence unchanged.'}
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / 'receipt.json').write_bytes(_encoded(result))
    return result


def sampling_preflight(root, canonical, case_paths):
    """Read explicitly selected cases; advise before solving, never modify them."""
    from .foam_dictionary import entry
    view = public_view(canonical)
    records, errors = [], []
    if not isinstance(case_paths, list) or len(case_paths) > 24 or any(not isinstance(p, str) for p in case_paths):
        return {'errors': ['case_paths_must_be_at_most_24_relative_strings'], 'cases': []}
    def number(text):
        try:
            value = float(text)
            return value if math.isfinite(value) else None
        except (ValueError, TypeError):
            return None
    for name in case_paths:
        try:
            path = safe_path(root, name.rstrip('/') + '/system/controlDict')
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('controlDict_too_large')
            text = path.read_text(encoding='utf-8')
            record = {'case_path': name, 'controlDict_sha256': _sha(text.encode()),
                      'settings': {k: entry(text, k) for k in ('application', 'startTime', 'endTime', 'deltaT', 'adjustTimeStep')},
                      'sampling': []}
            # Read named blocks using the same public dictionary parser as the
            # established checker; no shell or OpenFOAM command is executed.
            functions = entry(text, 'functions') or {}
            candidates = [k for k,v in functions.items() if isinstance(v, dict)]
            for function in sorted(candidates):
                values = {k: entry(text, 'functions', function, k) for k in
                          ('type', 'fields', 'patch', 'patches', 'interpolationScheme',
                           'writeControl', 'writeInterval', 'timeStart', 'timeEnd')}
                if not values.get('type'):
                    continue
                interval = number(values['writeInterval'])
                begin = number(values['timeStart'])
                if begin is None:
                    begin = number(record['settings']['startTime'])
                end = number(values['timeEnd'])
                if end is None:
                    end = number(record['settings']['endTime'])
                dt = number(record['settings']['deltaT'])
                period = interval if values['writeControl'] in ('runTime', 'adjustableRunTime') else interval * dt if values['writeControl'] == 'timeStep' and interval and dt else None
                estimate = math.floor((end - begin) / period + 1e-8) if period and period > 0 and begin is not None and end is not None and end >= begin else None
                record['sampling'].append({'name': function, **values,
                    'estimated_intervals': estimate, 'endpoint_policy': 'Estimate only; actual emitted timestamps determine count. Initial endpoint may add one row.'})
            records.append(record)
        except (OSError, ValueError, TypeError) as exc:
            errors.append(str(name) + ':' + str(exc))
    return {'cases': records, 'errors': errors, 'public_requirements': view['public_requirements'],
            'instruction': 'Compare actual write settings with public shapes, minimum counts and time windows before long solves. Preserve physics. Avoid gratuitously dense output. Unsupported timing semantics remain unknown.',
            'solver_calls': 0, 'writes': 0}
