"""Allocate the existing task budget without changing model or scoring limits."""
import math

POLICY = {'default_command_seconds': 300, 'delivery_reserve_seconds': 600,
          'max_delivery_fraction': 0.2, 'actor_start_floor_seconds': 60,
          'short_task_floor_fraction': 0.02}

GUIDANCE = """Runtime allocation for this candidate: shell/run without timeout_s
has a 300-second foreground timeout. For a genuinely long solver or mesh command,
set timeout_s explicitly using an estimated duration and redirect full logs to files.
The effective timeout always leaves up to 600 seconds (at most 20% of currently
remaining execution time) for inspection, postprocessing and honest delivery.
This allocates your existing total time, not extra time. The task OS persists after
ordinary command timeout. Preserve partial logs; a timeout is not a successful solve.
Before a combinatorial search estimate the product of loop sizes and benchmark a
small bounded sample. Prefer deriving constraints, factoring, pruning or vectorized
search to a large nested enumeration. Save useful progress and impose a script-level
deadline; do not repeat the same expensive search unchanged after timeout.
Inspect available solver outputs and generate required delivery files incrementally.
When the runtime_budget says delivery, prioritize evidence and actual missing fields,
avoid new speculative computation and repeated CFD, and submit honest partial results.
The independent final grader still has its original reserved time; private answers
remain unavailable. Runtime advice does not authorize altering numerical evidence.
"""

def command_allocation(action, remaining):
    if not math.isfinite(remaining) or remaining <= 0:
        raise ValueError('no_finite_execution_time_remaining')
    requested = float(action.get('timeout_s', POLICY['default_command_seconds']))
    if not math.isfinite(requested) or requested <= 0:
        raise ValueError('timeout_must_be_finite_and_positive')
    reserve = min(POLICY['delivery_reserve_seconds'], remaining * POLICY['max_delivery_fraction'])
    effective = min(requested, remaining - reserve)
    return effective, {'requested_timeout_seconds': requested,
        'timeout_was_explicit': 'timeout_s' in action,
        'effective_timeout_seconds': effective,
        'execution_seconds_before_command': remaining,
        'delivery_seconds_reserved_from_current_budget': reserve,
        'total_budget_extended': False}

def runtime_budget(remaining, execution_total, actions_remaining):
    return {'execution_seconds_remaining': max(0, remaining),
        'actions_remaining': max(0, actions_remaining),
        'phase': 'delivery' if remaining <= max(600, 0.1 * execution_total) else 'execute',
        'default_command_seconds': POLICY['default_command_seconds'],
        'note': 'Execution time excludes the already-reserved independent grading time.'}

def actor_start_floor(execution_total):
    return min(POLICY['actor_start_floor_seconds'],
               max(0, execution_total * POLICY['short_task_floor_fraction']))
