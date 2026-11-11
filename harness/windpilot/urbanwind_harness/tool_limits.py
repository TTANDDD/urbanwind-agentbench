"""Bound exploration independently of the long physical-solver task budget."""
import math

POLICY = {'shell': 120, 'mesh': 1800, 'postprocess': 300, 'deliver': 120,
          'solve_delivery_reserve': 300, 'heartbeat_seconds': 15}


def action_timeout(action, remaining):
    requested = float(action.get('timeout_s', remaining))
    if not math.isfinite(requested) or requested <= 0:
        raise ValueError('invalid_timeout')
    stage = action.get('stage') if action.get('action') == 'run' else 'shell'
    reserve = min(POLICY['solve_delivery_reserve'], max(0, remaining - 1)) if stage == 'solve' else 0
    cap = max(1, remaining - reserve) if stage == 'solve' else POLICY.get(stage, POLICY['shell'])
    timeout = min(requested, cap, remaining)
    return timeout, {'declared_stage': stage, 'effective_timeout_seconds': timeout,
                     'delivery_reserve_seconds': reserve,
                     'instruction': 'Use run(stage=solve) for a long intended solver run. For a short benchmark, change and verify endTime using foamDictionary before launching.'}
