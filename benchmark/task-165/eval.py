#!/usr/bin/env python3
import json
import math
import os
from pathlib import Path
from contract_evaluator import evaluate as precheck

TASK_ID = 'urbanwind-task-165'
RESULT_ARTIFACT = 'flow_diagnostics.json'
METRIC_ID = 'roof_exchange_flux_error'
UNIT = 'fraction'
EXPECTED = 0.0006432901041666667
TOLERANCE = 1e-09

def main():
    root = Path(os.environ.get('URBANWIND_EVAL_ROOT', '/home/user/Desktop'))
    base = precheck(root, TASK_ID)
    errors = list(base.get('errors', []))
    try:
        metrics = json.loads((root / 'metrics.json').read_text(encoding='utf-8'))
        value = float(metrics['values'][METRIC_ID])
        if not math.isfinite(value) or abs(value - EXPECTED) > TOLERANCE:
            errors.append(f'reference_metric_mismatch:{value}!={EXPECTED}+-{TOLERANCE}')
        if metrics.get('units', {}).get(METRIC_ID) != UNIT:
            errors.append('reference_metric_unit_mismatch')
        result = json.loads((root / RESULT_ARTIFACT).read_text(encoding='utf-8'))
        primary = result.get('primary_metric', {})
        if primary.get('id') != METRIC_ID or primary.get('unit') != UNIT:
            errors.append('result_primary_metric_contract_mismatch')
        result_value = float(primary.get('value'))
        if not math.isfinite(result_value) or abs(result_value - EXPECTED) > TOLERANCE:
            errors.append('result_primary_metric_reference_mismatch')
    except Exception as exc:
        errors.append(f'local_reference_eval_error:{exc}')
    reference_points = 25.0 if not errors else 0.0
    score = min(85.0, float(base.get('score', 0.0)) + reference_points)
    print(json.dumps({
        'task_id': TASK_ID,
        'score': score,
        'score_cap': 85.0,
        'status': 'local_reference_metric_check',
        'errors': errors,
        'precheck': base,
        'claim_boundary': 'Checks the frozen local numerical reference metric; does not establish experimental or full physical validity.'
    }, ensure_ascii=False, sort_keys=True))

if __name__ == '__main__':
    main()
