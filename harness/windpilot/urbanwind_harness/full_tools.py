"""Generic public-data utilities. No task IDs, reference data or scoring calls.

The CLI prints evidence; it never edits fields, fills missing observations,
certifies a physical gate, or writes a numerical submission on the agent's behalf.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def probe_statistics(path, start, end, components=3, lower_inclusive=False, epsilon=1e-8, indices=None):
    import numpy as np
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError('invalid_window')
    headers, times, rows, missing = [], [], [], set()
    with Path(path).open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            if line.lstrip().startswith('#'):
                if re.search(r'\bProbe\s+\d+\b', line):
                    headers.append(line.strip())
                if 'not found' in line.lower():
                    probe = re.search(r'\bProbe\s+(\d+)\b', line)
                    if not probe:
                        raise ValueError(f'unidentified_missing_probe:line_{number}')
                    missing.add(int(probe.group(1)))
                continue
            if not line.strip():
                continue
            # Parentheses are delimiters, never stripped with a fragile
            # fixed offset. Reject malformed tokens and inconsistent widths.
            words = line.replace('(', ' ').replace(')', ' ').split()
            try:
                values = [float(v) for v in words]
            except ValueError as exc:
                raise ValueError(f'invalid_numeric_token:line_{number}') from exc
            t = values[0]
            if not math.isfinite(t):
                raise ValueError(f'nonfinite_time:line_{number}')
            if times and t <= times[-1]:
                raise ValueError(f'duplicate_or_nonmonotone_time:line_{number}; explicitly reconcile restart files')
            if len(values) < 1 + components or (len(values)-1) % components:
                raise ValueError(f'invalid_row_shape:line_{number}')
            if rows and len(values)-1 != len(rows[0]):
                raise ValueError(f'inconsistent_row_width:line_{number}')
            times.append(t)
            rows.append(values[1:])
    if not rows:
        raise ValueError('no_probe_samples')
    a = np.asarray(rows, dtype=float).reshape(len(rows), -1, components)
    selected_indices = list(range(a.shape[1])) if indices is None else list(indices)
    if not selected_indices or len(set(selected_indices)) != len(selected_indices) or any(i < 0 or i >= a.shape[1] for i in selected_indices):
        raise ValueError('invalid_explicit_probe_indices')
    if missing & set(selected_indices):
        raise ValueError('missing_required_probe_location; do not fill zero or move coordinates')
    a = a[:, selected_indices]
    if not np.isfinite(a).all() or (abs(a) >= 1e100).any():
        raise ValueError('nonfinite_or_missing_sentinel; never replace with zero')
    t = np.asarray(times)
    lower = t >= start if lower_inclusive else t > start
    select = lower & (t <= end + epsilon)
    if not select.any():
        raise ValueError('empty_selected_window')
    middle = (start + end) / 2
    early = select & (t <= middle + epsilon)
    late = select & (t > middle + epsilon)
    mean = a[select].mean(axis=0)
    variance = ((a[select]-mean)**2).mean(axis=0)
    result = {'source': str(path), 'sha256': sha256(path), 'probe_headers': headers,
              'window': {'start': start, 'end': end, 'lower_inclusive': lower_inclusive,
                         'upper_epsilon': epsilon, 'variance_ddof': 0},
              'selected_probe_indices': selected_indices,
              'excluded_missing_probe_indices': sorted(missing-set(selected_indices)),
              'sample_count': int(select.sum()), 'first_time': float(t[select][0]),
              'last_time': float(t[select][-1]), 'probe_count': a.shape[1],
              'mean': mean.tolist(), 'variance': variance.tolist(),
              'half_window_counts': [int(early.sum()), int(late.sum())],
              'scope': 'Raw public sample arithmetic only. Select normalization/masks from the public contract.'}
    if components == 3:
        result['resolved_tke'] = (0.5 * variance.sum(axis=1)).tolist()
    if early.any() and late.any():
        m1, m2 = a[early].mean(axis=0), a[late].mean(axis=0)
        result['half_mean_abs_delta'] = abs(m1-m2).tolist()
        if components == 3:
            k1 = 0.5 * ((a[early]-m1)**2).mean(axis=0).sum(axis=1)
            k2 = 0.5 * ((a[late]-m2)**2).mean(axis=0).sum(axis=1)
            result['half_tke_abs_delta'] = abs(k1-k2).tolist()
    else:
        result['half_window_unknown'] = 'one or both halves contain no samples'
    return result


def log_statistics(path):
    values = {'time': [], 'courant': [], 'continuity': [], 'cells': [], 'ranks': []}
    end = mesh_ok = False
    fatal = []
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            if line.strip() == 'End':
                end = True
            if 'Mesh OK' in line:
                mesh_ok = True
            if 'FOAM FATAL' in line or 'Floating point exception' in line or 'Segmentation fault' in line:
                fatal.append(line.strip())
            for key, pattern in (
                ('time', r'^Time\s*=\s*('+NUMBER+r')'),
                ('courant', r'Courant Number.*?max:\s*('+NUMBER+r')'),
                ('continuity', r'time step continuity errors.*?cumulative\s*=\s*('+NUMBER+r')'),
                ('cells', r'^\s*cells:\s*(\d+)'),
                ('ranks', r'^\s*nProcs\s*:\s*(\d+)')):
                match = re.search(pattern, line)
                if match:
                    values[key].append(float(match.group(1)))
    return {'source': str(path), 'sha256': sha256(path), 'end_seen': end,
            'mesh_ok_seen': mesh_ok, 'fatal_lines': fatal[:12],
            'last_time': values['time'][-1] if values['time'] else None,
            'max_courant': max(values['courant']) if values['courant'] else None,
            'max_abs_cumulative_continuity': max(map(abs, values['continuity'])) if values['continuity'] else None,
            'courant_samples': len(values['courant']), 'continuity_samples': len(values['continuity']),
            'cells_observed': sorted(set(values['cells'])), 'nprocs_observed': sorted(set(values['ranks'])),
            'scope': 'Log evidence only. Missing diagnostics remain null; End/exit0 is not numerical acceptance.'}


def field_values(path, cell_count=None):
    import numpy as np
    text = Path(path).read_text(encoding='utf-8')
    if re.search(r'\bformat\s+binary\s*;', text):
        raise ValueError('binary_field_requires_native_conversion; source must be preserved')
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
    match = re.search(r'internalField\s+nonuniform\s+List<(scalar|vector|symmTensor|tensor)>\s+(\d+)\s*\((.*?)\)\s*;', text, re.S)
    if not match:
        uniform = re.search(r'internalField\s+uniform\s+([^;]+);', text)
        if not uniform or isinstance(cell_count, bool) or not isinstance(cell_count, int) or cell_count <= 0:
            raise ValueError('uniform_field_needs_actual_positive_mesh_cell_count')
        values = [float(x) for x in uniform.group(1).replace('(', ' ').replace(')', ' ').split()]
        if len(values) not in (1, 3, 6, 9) or not all(math.isfinite(x) for x in values):
            raise ValueError('invalid_uniform_field')
        return np.tile(values, (cell_count, 1))
    kind, count, body = match.groups()
    width = {'scalar': 1, 'vector': 3, 'symmTensor': 6, 'tensor': 9}[kind]
    words = body.replace('(', ' ').replace(')', ' ').split()
    a = np.asarray([float(x) for x in words], dtype=float)
    if a.size != int(count)*width or not np.isfinite(a).all():
        raise ValueError('invalid_internal_field_shape_or_nonfinite')
    return a.reshape(int(count), width)


def normalized_absolute_error(values, reference, scale, indices=None):
    """Uniform mean absolute difference / explicit PUBLIC scale.

    Arguments must come from public reference observations when allowed, or
    actual case-to-case comparisons. This function opens no reference files.
    """
    import numpy as np
    a, b = np.asarray(values, dtype=float), np.asarray(reference, dtype=float)
    if a.shape != b.shape or a.size == 0 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('metric_shape_or_finite_mismatch')
    if isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('public_positive_normalization_scale_required')
    if indices is not None:
        if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= a.shape[0] for i in indices):
            raise ValueError('invalid_explicit_metric_mask')
        a, b = a[indices], b[indices]
    return float(abs(a-b).mean()/scale)


def pointwise_case_spread(case_arrays, scale, indices=None):
    """Cross-case max-minus-min PER POINT, then uniform mean / public scale.

    Aggregation order is explicit: this is not max/min of case means.
    Use only if this matches the current public metric definition.
    """
    import numpy as np
    a = np.asarray(case_arrays, dtype=float)
    if a.ndim < 2 or a.shape[0] < 2 or a.size == 0 or not np.isfinite(a).all():
        raise ValueError('invalid_case_spread_arrays')
    if isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
        raise ValueError('public_positive_normalization_scale_required')
    if indices is not None:
        if not indices or len(set(indices)) != len(indices) or any(i < 0 or i >= a.shape[1] for i in indices):
            raise ValueError('invalid_explicit_metric_mask')
        a = a[:, indices]
    return float((a.max(axis=0)-a.min(axis=0)).mean()/scale)


def case_metadata(case):
    """Read actual controls using native dictionary resolution, never a report."""
    case = Path(case)
    controls, unknown = {}, []
    for relative, keys in (
        ('system/controlDict', ('application','startFrom','startTime','endTime','deltaT','adjustTimeStep','maxCo')),
        ('constant/turbulenceProperties', ('simulationType','LES/LESModel','RAS/RASModel')),
        ('constant/momentumTransport', ('simulationType','LES/model','RAS/model')),
        ('constant/transportProperties', ('nu',))):
        path = case/relative
        if not path.is_file():
            continue
        for key in keys:
            try:
                result = subprocess.run(['foamDictionary',str(path),'-entry',key,'-value'],
                                        text=True, capture_output=True, timeout=15, check=False)
                if result.returncode == 0:
                    controls[relative+':'+key] = result.stdout.strip()
                else:
                    unknown.append(relative+':'+key)
            except (OSError, subprocess.TimeoutExpired):
                unknown.append(relative+':'+key)
    owner = case/'constant/polyMesh/owner'
    cells = None
    if owner.is_file():
        with owner.open(encoding='utf-8', errors='replace') as stream:
            header = stream.read(16384)
        match = re.search(r'nCells\s*:\s*(\d+)', header)
        if match:
            cells = int(match.group(1))
    processors = sorted(p.name for p in case.glob('processor*')
                        if p.is_dir() and re.fullmatch(r'processor\d+',p.name))
    return {'case':str(case), 'observed_controls':controls,'unknown_controls':unknown,
            'owner_header_cell_count':cells, 'processor_directories':processors,
            'scope':'Actual native configuration and mesh metadata only. Directory count is not solver nProcs or numerical acceptance.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    q = sub.add_parser('probes')
    q.add_argument('path', type=Path)
    q.add_argument('--start', type=float, required=True)
    q.add_argument('--end', type=float, required=True)
    q.add_argument('--components', type=int, choices=(1, 3), default=3)
    q.add_argument('--lower-inclusive', action='store_true')
    q.add_argument('--epsilon', type=float, default=1e-8)
    q.add_argument('--indices', help='Explicit public-contract probe mask, comma-separated zero-based indices')
    q = sub.add_parser('log')
    q.add_argument('path', type=Path)
    q = sub.add_parser('hash')
    q.add_argument('path', type=Path)
    q = sub.add_parser('case')
    q.add_argument('path', type=Path)
    args = p.parse_args()
    if args.action == 'probes':
        indices = [int(x) for x in args.indices.split(',')] if args.indices else None
        result = probe_statistics(args.path, args.start, args.end, args.components, args.lower_inclusive, args.epsilon, indices)
    elif args.action == 'log':
        result = log_statistics(args.path)
    elif args.action == 'case':
        result = case_metadata(args.path)
    else:
        result = {'path': str(args.path), 'sha256': sha256(args.path)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
