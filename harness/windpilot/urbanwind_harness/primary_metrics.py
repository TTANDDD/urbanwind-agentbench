"""Read-only primary-metric serialization; no score or fixed numerical answers."""
import json
import math


def verify_primary_metrics(root, canonical, reader):
    findings, observations = [], []
    def read(base, name):
        item = reader(base, name, max_bytes=128 * 1024 * 1024)
        if item.get('omitted'): raise ValueError(item['omitted'])
        value = json.loads(item['text'])
        if not isinstance(value, dict): raise ValueError('root_must_be_object')
        return value, item.get('sha256')
    def fail(code, file, pointer, **detail):
        findings.append({'code': code, 'file': file, 'pointer': pointer, **detail})
    try: spec, _ = read(canonical, 'task_spec.json')
    except (ValueError, TypeError, KeyError):
        return {'errors': [], 'findings': [], 'observations': [], 'scope': 'Canonical artifact declaration unavailable.'}
    required = [x.get('path') if isinstance(x, dict) else x for x in spec.get('required_artifacts', [])]
    if 'mesh_metrics.json' not in required:
        return {'errors': [], 'findings': [], 'observations': [], 'scope': 'Mesh metric document not required.'}
    try:
        raw, digest = read(root, 'mesh_metrics.json'); primary = raw.get('primary_metric')
        if not isinstance(primary, dict):
            fail('required_root_primary_metric', 'mesh_metrics.json', '/primary_metric',
                 observed_alias='metric' if 'metric' in raw else None,
                 repair='Provide an object with id, unit and finite numeric value derived from verified raw evidence; preserve values and recompute hashes. The metric alias is not this field.')
        else:
            report, _ = read(canonical, 'reporting_contract.json')
            contracts = {x['id']: x for x in report.get('metric_contracts', []) if isinstance(x, dict) and isinstance(x.get('id'), str)}
            name, unit, value = (primary.get(k) for k in ('id', 'unit', 'value'))
            if not isinstance(name, str) or not name or contracts and name not in contracts:
                fail('primary_metric_id_not_declared', 'mesh_metrics.json', '/primary_metric/id')
            if not isinstance(unit, str) or not unit or isinstance(name, str) and name in contracts and unit != contracts[name].get('unit'):
                fail('primary_metric_unit_mismatch', 'mesh_metrics.json', '/primary_metric/unit')
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                fail('primary_metric_finite_numeric_value_required', 'mesh_metrics.json', '/primary_metric/value')
            metrics, mdigest = read(root, 'metrics.json')
            key = name if isinstance(name, str) else ''
            stated = metrics.get('values', {}).get(key)
            if isinstance(stated, bool) or not isinstance(stated, (int, float)) or not math.isfinite(stated) or stated != value:
                fail('primary_metric_report_value_mismatch', 'metrics.json', '/values/' + key)
            if metrics.get('units', {}).get(key) != unit:
                fail('primary_metric_report_unit_mismatch', 'metrics.json', '/units/' + key)
            if isinstance(metrics.get('primary_metric'), dict):
                for field in ('id', 'unit', 'value'):
                    if metrics['primary_metric'].get(field) != primary.get(field):
                        fail('primary_metric_cross_document_mismatch', 'metrics.json', '/primary_metric/' + field)
            observations.append({'file': 'mesh_metrics.json', 'sha256': digest, 'metrics_sha256': mdigest,
                                 'id': name, 'unit': unit, 'value': value, 'raw_recomputation_certified': False})
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        fail('primary_metric_document_unavailable', 'mesh_metrics.json', '/primary_metric', detail=str(exc))
    return {'errors': [x['file'] + ':' + x['code'] + ':' + x['pointer'] for x in findings],
            'findings': findings, 'observations': observations, 'workspace_writes': 0,
            'scope': 'User-authorized primary_metric serialization and public-unit/cross-report equality only. No grader, hidden reference or fixed numerical value.'}
