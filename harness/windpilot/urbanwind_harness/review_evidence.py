"""Bounded views of public evidence; no evaluator or hidden-reference access."""
from __future__ import annotations
import json
import re
from pathlib import PurePosixPath


def referenced_json_paths(documents):
    """One hop from declared artifacts, never a recursive filesystem crawl."""
    seen = set()
    def walk(value):
        if isinstance(value, dict):
            name = value.get('path')
            digest = value.get('sha256')
            if isinstance(name, str) and isinstance(digest, str) and len(digest) == 64:
                path = PurePosixPath(name)
                if (path.suffix == '.json' and not path.is_absolute() and '..' not in path.parts
                        and '\\' not in name and ':' not in name
                        and path.name != 'reference.json' and 'grading' not in path.parts):
                    seen.add(name)
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    for document in documents:
        if isinstance(document.get('structured_summary'), dict):
            walk(document['structured_summary'])
        elif 'text' in document:
            try: walk(json.loads(document['text']))
            except (ValueError, TypeError): pass
    return sorted(seen)


def manifest_view(value):
    """Keep every run and receipt field; explicitly excerpt only command text."""
    def bounded(item, key=''):
        if isinstance(item, dict): return {k: bounded(v, k) for k, v in item.items()}
        if isinstance(item, list): return [bounded(v, key) for v in item]
        if isinstance(item, str) and key in ('command', 'commands') and len(item) > 400:
            return {'head': item[:250], 'tail': item[-100:], 'omitted_characters': len(item)-350,
                    'scope': 'Command excerpt only; exit_code is the recorded wrapper exit.'}
        return item
    return bounded(value)


def registered_execution_ids(value):
    result=set()
    for run in value.get('runs',[]) if isinstance(value,dict) else []:
        if not isinstance(run,dict):continue
        for name in run.get('execution_receipts',[]):
            if isinstance(name,str):
                match=re.fullmatch(r'harness_trace/(\d{3})/execution\.json',name)
                if match:result.add(match.group(1))
    return sorted(result)


def checks_view(checks, compact):
    """Do not lose later cases by applying a generic 16-item list limit."""
    output = compact(checks)
    numerical = checks.get('public_numerical_checks', checks)
    if isinstance(numerical, dict) and isinstance(numerical.get('raw_observations'), list):
        observations = []
        for row in numerical['raw_observations']:
            view = {k: v for k, v in row.items() if k != 'coordinates'}
            if 'coordinates' in row:
                view['coordinate_count'] = len(row['coordinates'])
                view['coordinates_scope'] = 'Coordinate arrays omitted; only explicit match results below are verified.'
            observations.append(compact(view))
        target = output.get('public_numerical_checks', output)
        target['raw_observations'] = observations
    return output


def shorter_prose(value):
    """Budget fallback retaining dictionary keys and all numeric observations."""
    if isinstance(value, dict):return {k:shorter_prose(v) for k,v in value.items()}
    if isinstance(value, list):return [shorter_prose(v) for v in value]
    if isinstance(value, str) and len(value.encode()) > 500:
        return {'text_head':value[:200], 'text_tail':value[-100:],
                'omitted_characters':max(0,len(value)-300), 'scope':'Explicit prose excerpt only.'}
    return value


def deterministic_feedback(checks):
    # Numeric failures are often outside acceptance.findings. Keep the complete
    # check result so an explicit review action never returns an empty diagnosis.
    return {'public_checks': checks, 'agent_judge': {
        'verdict': 'repair_required', 'source': 'deterministic_checks',
        'issues': (checks.get('public_numerical_checks', checks).get('acceptance', {}).get('findings', []) +
                   checks.get('public_numerical_checks', checks).get('evidence_consistency', {}).get('findings', []) +
                   checks.get('public_numerical_checks', checks).get('primary_metric_consistency', {}).get('findings', []))[:80],
        'blocking_errors': list(checks.get('errors', [])),
        'limitations': ['Resolve the reported public errors before consuming a model judgment.']}}
