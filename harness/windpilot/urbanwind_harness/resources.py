import os
import math
import time
from pathlib import Path
from .common import now
BASE = Path(__file__).resolve().parents[1]

def process(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rfind(')')+2:].split()
        return {'pid': int(pid), 'ppid': int(fields[1]), 'state': fields[0],
                'start_ticks': fields[19], 'comm': raw[raw.find('(')+1:raw.rfind(')')]}
    except (OSError, IndexError, ValueError):
        return None

def matches(pid, ticks):
    item = process(pid)
    return bool(item and item['start_ticks'] == str(ticks) and item['state'] != 'Z')

def descendants(pid):
    rows = [process(p.name) for p in Path('/proc').iterdir() if p.name.isdigit()]
    owned = {int(pid)}
    while True:
        added = {p['pid'] for p in rows if p and p['ppid'] in owned} - owned
        if not added:
            break
        owned |= added
    return [p for p in rows if p and p['pid'] in owned - {int(pid)} and p['state'] != 'Z']

def finite_result(value):
    score = value.get('score')
    return (value.get('status') == 'evaluated' and isinstance(score, (int, float))
            and not isinstance(score, bool) and math.isfinite(score) and 0 <= score <= 100)

def parse_cpus(text):
    output = set()
    for part in text.strip().split(','):
        if '-' in part:
            left, right = map(int, part.split('-'))
            output.update(range(left, right+1))
        elif part:
            output.add(int(part))
    return output

def topology():
    groups = {}
    for cpu in os.sched_getaffinity(0):
        p = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        siblings = tuple(sorted(parse_cpus((p/'thread_siblings_list').read_text())))
        groups[siblings] = max(siblings)
    return groups

def choose_slot(groups, occupied, external):
    candidates = [representative for siblings, representative in sorted(groups.items())
                  if not set(siblings) & (set(occupied) | set(external))]
    return candidates[:16] if len(candidates) >= 16 else None


def choose_shared_slot(groups, occupied, external, usage):
    """Prefer idle SMT threads, allowing external non-solver core sharing.

    Our tasks still reserve different physical cores from one another and
    from external OpenFOAM solvers. Observed background CPU load is recorded,
    not treated as a requirement for physical-core exclusivity.
    """
    blocked = set(occupied) | set(external)
    candidates = []
    for siblings in groups:
        if set(siblings) & blocked: continue
        known = [c for c in siblings if c in usage]
        if not known: continue
        cpu = min(known, key=lambda c: (usage[c], c))
        candidates.append((usage[cpu], cpu))
    candidates.sort()
    return [cpu for _, cpu in candidates[:16]] if len(candidates) >= 16 else None


def busy_cpus_from_samples(before, after, maximum_percent=20):
    """Reject unreadable/nonadvancing counters and busy SMT siblings alike."""
    busy, usage = set(), {}
    for cpu in set(before) | set(after):
        if cpu not in before or cpu not in after:
            busy.add(cpu); continue
        delta = [y - x for x, y in zip(before[cpu], after[cpu])]
        if len(delta) != 8 or min(delta) < 0 or sum(delta) <= 0:
            busy.add(cpu); continue
        value = 100 * (1 - (delta[3] + delta[4]) / sum(delta))
        usage[cpu] = round(value, 3)
        if value >= maximum_percent: busy.add(cpu)
    return busy, usage


def sample_cpu_load(interval=2):
    def snapshot():
        return {int(parts[0][3:]): list(map(int, parts[1:9]))
                for line in Path('/proc/stat').read_text().splitlines()
                if (parts := line.split())[0].startswith('cpu') and parts[0][3:].isdigit()}
    before = snapshot(); time.sleep(interval); after = snapshot()
    busy, usage = busy_cpus_from_samples(before, after)
    busy |= set(os.sched_getaffinity(0)) - set(usage)
    return {'busy_cpus': sorted(busy), 'cpu_usage_percent': usage,
            'interval_seconds': interval, 'maximum_percent': 20}

def resource_state(owned):
    external = set()
    conflicts = []
    solver_names = ('foam', 'pimplefo', 'simplefo', 'blockmesh', 'snappyhexmesh', 'checkmesh', 'decomposepar')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) in owned:
            continue
        item = process(p.name)
        if not item or item['state'] == 'Z' or not any(s in item['comm'].lower() for s in solver_names):
            continue
        try:
            cpus = os.sched_getaffinity(int(p.name))
        except OSError:
            # A solver with unreadable affinity must not silently be ignored.
            cpus = parse_cpus(next(x.split(':', 1)[1] for x in (p/'status').read_text().splitlines()
                                  if x.startswith('Cpus_allowed_list:')))
        external |= cpus
        conflicts.append({'pid': int(p.name), 'comm': item['comm'], 'cpus': sorted(cpus)})
    mem = next(int(x.split()[1]) for x in Path('/proc/meminfo').read_text().splitlines()
               if x.startswith('MemAvailable:'))*1024
    disk = os.statvfs(BASE)
    return {'at': now(), 'external_solver_cpus': sorted(external), 'external_solvers': conflicts,
            'memory_available_bytes': mem, 'disk_available_bytes': disk.f_bavail*disk.f_frsize}
