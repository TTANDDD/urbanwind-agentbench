#!/usr/bin/env python3
"""Task-065 raw archive, synthetic target and two-plane numerical evaluator."""
from __future__ import annotations

import json
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck
from task065_raw_evidence import (CASE_ID, CONTRACT_HASH, PRIMARY_ID, PROBES_HASH,
    TASK_ID, analyze_archive, close, digest, load, probe_table)

assert TASK_ID == 'urbanwind-task-065'


def evaluate_task(root):
    root = Path(root).resolve()
    baseline = precheck(root, TASK_ID)
    errors = list(baseline.get('errors', []))
    raw_ok = report_ok = hidden_ok = False
    recomputed = {}
    try:
        contract_path = root / 'local_assets/synthetic_inflow_contract.json'
        table_path = root / 'local_assets/synthetic_inflow_probe_points.csv'
        if digest(contract_path) != CONTRACT_HASH or digest(table_path) != PROBES_HASH:
            raise ValueError('visible_contract_changed')
        contract = load(contract_path)
        hidden = load(Path(os.environ.get('URBANWIND_REFERENCE', f'/tmp/{TASK_ID}-reference.json')))
        manifest = load(root / 'synthetic_inflow_evidence_manifest.json')
        reported = load(root / 'synthetic_inflow_results.json')
        metrics = load(root / 'metrics.json')
        if any(item.get('task_id') != TASK_ID for item in (hidden, manifest, reported, metrics)):
            raise ValueError('task_id_mismatch')
        evidence = manifest.get('case', {})
        submitted = reported.get('case_result', {})
        if evidence.get('case_id') != CASE_ID or submitted.get('case_id') != CASE_ID:
            raise ValueError('case_id_mismatch')
        item = evidence.get('raw_case_archive')
        if not isinstance(item, dict) or not {'path', 'sha256'} <= set(item):
            raise ValueError('evidence_item:raw_case_archive')
        relative = item['path']
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError('unsafe_evidence_path')
        archive = (root / relative).resolve()
        if root not in archive.parents or not archive.is_file() or archive.stat().st_size > 512 * 1024**2:
            raise ValueError('unsafe_missing_or_oversized_evidence')
        if digest(archive) != item['sha256']:
            raise ValueError('evidence_hash')
        raw = analyze_archive(archive, contract, probe_table(table_path), hidden['replay_input_hashes'])
        if not all(raw['gates'].values()):
            raise ValueError('raw_case_gate_failed:' + ','.join(key for key, passed in raw['gates'].items() if not passed))
        raw_ok = True
        recomputed = raw['validation']
        if not close(submitted.get('validation'), recomputed):
            raise ValueError('reported_result_not_recomputed')
        if (not close(metrics.get('values', {}).get(PRIMARY_ID), recomputed[PRIMARY_ID])
                or metrics.get('units', {}).get(PRIMARY_ID) != 'fraction'):
            raise ValueError('reported_metric_not_recomputed')
        report_ok = True
        reference = hidden['case_results'][0]
        if reference.get('case_id') != CASE_ID or hidden.get('experimental_claim') is not False:
            raise ValueError('hidden_identity')
        if not close(recomputed, reference['validation']):
            raise ValueError('hidden_probe_statistics')
        hidden_ok = True
    except Exception as exc:
        errors.append(str(exc))
    score = baseline.get('score', 0.0) + 25 * raw_ok + 10 * report_ok + 5 * hidden_ok
    return {'score': min(float(score), 100.0), 'errors': errors, 'contract_score': baseline.get('score', 0.0),
            'status': 'openfoam_synthetic_inflow_raw_evidence_reference_check',
            'raw_evidence_pass': raw_ok, 'reported_result_pass': report_ok, 'hidden_reference_pass': hidden_ok,
            'recomputed': recomputed,
            'claim_boundary': 'Fixed local synthetic inlet and matched OpenFOAM numerical response, not measured atmospheric turbulence or experimental truth.'}


if __name__ == '__main__':
    print(json.dumps(evaluate_task(os.environ.get('URBANWIND_EVAL_ROOT', '/home/user/Desktop')), ensure_ascii=False, indent=2))
