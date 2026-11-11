"""Current ancestry by PID start identity, including newly created children."""
def owned_by_identity(pid, roots, lookup, limit=128):
    seen = set()
    for _ in range(limit):
        row = lookup(pid)
        if not row or pid in seen: return False
        seen.add(pid)
        if pid in roots: return str(row['start_ticks']) == str(roots[pid])
        pid = row['ppid']
        if pid <= 1: return False
    return False


def filter_external(resources, roots, lookup):
    kept, own = [], []
    for row in resources['external_solvers']:
        (own if owned_by_identity(row['pid'], roots, lookup) else kept).append(row)
    result = dict(resources)
    result['external_solvers'] = kept
    result['external_solver_cpus'] = sorted({cpu for row in kept for cpu in row['cpus']})
    result['newly_observed_owned_solvers'] = own
    return result
