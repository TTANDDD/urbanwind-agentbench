#!/usr/bin/env python3
"""Independent task-065 raw input/probe verification; no solver-wrapper imports."""
from __future__ import annotations

import csv
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import tarfile
import tempfile

TASK_ID = 'urbanwind-task-065'
CASE_ID = 'task-065-case-001'
PRIMARY_ID = 'inflow_spectral_band_error'
VERSION = 'urbanwind-openfoam-20261004-v45c-task065-synthetic-inflow'
CONTRACT_HASH = 'be15d51f004c054b3a032472d3c64f84944729ace9ce53249fd2596bd1aee052'
PROBES_HASH = '61183f2632104250f16498ce2e7f5bc35f5f1aa1a5f9913edaab716c3f0aa40c'
GENERATOR_HASH = 'ca2b9262701fcf6ebfae6278b6f88f13faf70fbdb7df847189c57f289bdce44a'
NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
VECTOR = re.compile(r'\(\s*(%s)\s+(%s)\s+(%s)\s*\)' % (NUMBER, NUMBER, NUMBER))


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def close(actual, expected, atol=1e-8):
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return actual == expected
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-8, abs_tol=atol)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(close(a, b, atol) for a, b in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(close(actual[key], value, atol) for key, value in expected.items())
    return False


def safe_extract(archive, destination):
    with tarfile.open(archive, 'r:gz') as handle:
        members = handle.getmembers()
        names = [member.name for member in members]
        if len(set(names)) != len(names) or sum(member.size for member in members) > 2 * 1024**3:
            raise ValueError('duplicate_or_oversized_archive')
        for member in members:
            target = (destination / member.name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError('unsafe_archive_path')
            if not (member.isfile() or member.isdir()):
                raise ValueError('unsafe_archive_type')
        handle.extractall(destination, filter='data')
    return [member.name for member in members if member.isfile()]


def probe_table(path):
    with Path(path).open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 16 or [row['probe_id'] for row in rows] != [f'p{i:02d}' for i in range(16)]:
        raise ValueError('probe_table_identity')
    return rows


def points_for(table, kind):
    return [[float(row[f'{kind}_x_m']), float(row['y_m']), float(row['z_m'])] for row in table]


def vector_field(path, count, average=False):
    text = Path(path).read_text(encoding='utf-8')
    body = text.split('}', 1)[1]
    if not re.match(r'\s*%d\s*\(' % count, body):
        raise ValueError('raw_vector_count:' + str(path))
    vectors = [list(map(float, values)) for values in VECTOR.findall(body)]
    if len(vectors) != count + int(average) or not all(math.isfinite(value) for row in vectors for value in row):
        raise ValueError('raw_vector_shape_or_finite:' + str(path))
    if average and not close(vectors[-1], [1.2, 0.0, 0.0], 1e-12):
        raise ValueError('boundary_average')
    return vectors[:count]


def read_probes(path, points):
    text = Path(path).read_text(encoding='utf-8')
    coordinates = [list(map(float, match[1:])) for match in re.findall(r'^#\s*Probe\s+(\d+)\s+\((%s)\s+(%s)\s+(%s)\)' % (NUMBER, NUMBER, NUMBER), text, re.M)]
    ids = [int(match) for match in re.findall(r'^#\s*Probe\s+(\d+)\s+\(', text, re.M)]
    if ids != list(range(16)) or not close(coordinates, points, 1e-12):
        raise ValueError('probe_coordinates')
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        vectors = [list(map(float, values)) for values in VECTOR.findall(line)]
        if len(vectors) != 16:
            raise ValueError('probe_shape')
        row = [float(line.split()[0])] + [value for vector in vectors for value in vector]
        if not all(math.isfinite(value) for value in row):
            raise ValueError('probe_nonfinite')
        rows.append(row)
    if not rows or abs(rows[-1][0] - 90.0) > 1e-7 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError('probe_time_order_or_end')
    return rows


@lru_cache(maxsize=1)
def dft_weights(count):
    return [(tuple(math.cos(-2 * math.pi * k * t / count) for t in range(count)),
             tuple(math.sin(-2 * math.pi * k * t / count) for t in range(count))) for k in range(1, count // 2 + 1)]


def series_statistics(series, bands):
    n = len(series)
    mean = math.fsum(series) / n
    centered = [value - mean for value in series]
    square_sum = math.fsum(value * value for value in centered)
    if square_sum <= 1e-30:
        raise ValueError('degenerate_synthetic_series')
    energies = [(math.fsum(a * b for a, b in zip(centered, cosines))**2 +
                 math.fsum(a * b for a, b in zip(centered, sines))**2) for cosines, sines in dft_weights(n)]
    total = math.fsum(energies)
    frequencies = [k / (n * 0.2) for k in range(1, n // 2 + 1)]
    fractions = [math.fsum(e for f, e in zip(frequencies, energies) if low <= f < high) / total for low, high in bands]
    positive = 0.0
    for lag in range(n):
        correlation = math.fsum(centered[i] * centered[i + lag] for i in range(n - lag)) / square_sum
        if lag and correlation <= 0:
            break
        positive += correlation
    return {'mean_m_s': mean, 'variance_m2_s2': square_sum / n,
            'normalized_psd_band_fractions': fractions, 'integral_time_scale_s': 0.2 * max(positive - 0.5, 0.0)}


def plane_statistics(rows, contract):
    selected = [row for row in rows if 30 < row[0] <= 90 + 1e-8]
    expected_times = [30 + (i + 1) * 0.2 for i in range(300)]
    if len(selected) != 300 or any(len(row) != 49 for row in selected) or not close([row[0] for row in selected], expected_times, 1e-8):
        raise ValueError('sample_count_or_each_interval')
    statistics = []
    for p in range(16):
        statistics.append({'probe_index': p, 'components': {
            name: series_statistics([row[1 + p * 3 + c] for row in selected], contract['spectral_target']['bands_hz'])
            for c, name in enumerate(('Ux', 'Uy', 'Uz'))}})
    return {'sample_count': 300, 'sampling_interval_s': 0.2, 'probe_statistics': statistics}


def compare_to_target(actual, target):
    spectra, mean_sq, stress, scales = [], [], [], []
    for a, t in zip(actual['probe_statistics'], target['probe_statistics']):
        for name in ('Ux', 'Uy', 'Uz'):
            ac, tc = a['components'][name], t['components'][name]
            spectra.extend(abs(v - w) for v, w in zip(ac['normalized_psd_band_fractions'], tc['normalized_psd_band_fractions']))
            mean_sq.append((ac['mean_m_s'] - tc['mean_m_s'])**2)
            stress.append(abs(ac['variance_m2_s2'] - tc['variance_m2_s2']) / max(abs(tc['variance_m2_s2']), 1e-8))
            scales.append(abs(ac['integral_time_scale_s'] - tc['integral_time_scale_s']) / max(abs(tc['integral_time_scale_s']), 0.1))
    return {'spectral_band_mae': math.fsum(spectra) / 240, 'mean_velocity_rmse_m_s': math.sqrt(math.fsum(mean_sq) / 48),
            'diagonal_reynolds_stress_relative_error': math.fsum(stress) / 48,
            'integral_time_scale_relative_error': math.fsum(scales) / 48}


def check_mean_field(path, name, components):
    text = Path(path).read_text(encoding='utf-8')
    match = re.search(r'internalField\s+nonuniform\s+List<\w+>\s+(\d+)\s*\((.*?)\)\s*;', text, re.S)
    return bool(match and int(match[1]) == 365488 and re.search(r'object\s+' + name + r'\s*;', text)
                and not re.search(r'\b(?:nan|inf)\b', text, re.I)
                and sum(1 for _ in re.finditer(NUMBER, match[2])) == 365488 * components)


def analyze_archive(archive, contract, table, input_hashes):
    with tempfile.TemporaryDirectory(prefix='task065-raw-audit-') as raw:
        case = Path(raw)
        names = safe_extract(archive, case)
        for relative, expected_hash in input_hashes.items():
            if not (case / relative).is_file() or digest(case / relative) != expected_hash:
                raise ValueError('frozen_input_changed:' + relative)
        cfg = load(case / 'resolved_config.json')
        analysis = load(case / 'run_analysis.json')
        log = (case / 'log.pimpleFoam').read_text(encoding='utf-8')
        mesh = (case / 'log.checkMesh').read_text(encoding='utf-8')
        controls = (case / 'system/controlDict').read_text(encoding='utf-8')
        inlet_boundary = (case / '0/U').read_text(encoding='utf-8')
        times = list(map(float, re.findall(r'^Time = (%s)' % NUMBER, log, re.M)))
        courant = list(map(float, re.findall(r'Courant Number mean: %s max: (%s)' % (NUMBER, NUMBER), log)))
        continuity = list(map(float, re.findall(r'cumulative = (%s)' % NUMBER, log)))
        final_dirs = {name.split('/', 1)[0] for name in names if name.endswith('/UMean')}
        if len(final_dirs) != 1 or abs(float(next(iter(final_dirs))) - 90) > 1e-7:
            raise ValueError('final_mean_field_directory')
        final_dir = next(iter(final_dirs))
        points = vector_field(case / 'constant/boundaryData/inlet/points', 221)
        boundary_rows = []
        indices = [int(row['boundary_point_index']) for row in table]
        for row, index in zip(table, indices):
            if not close(points[index], [-4.0, float(row['y_m']), float(row['z_m'])], 1e-10):
                raise ValueError('boundary_to_probe_mapping')
        boundary = case / 'constant/boundaryData/inlet'
        frames = sorted((path for path in boundary.iterdir() if path.is_dir()), key=lambda p: float(p.name))
        if len(frames) != 451 or not close([float(path.name) for path in frames], [i * 0.2 for i in range(451)], 1e-10):
            raise ValueError('boundary_time_slices')
        for frame in frames:
            if not 30 < float(frame.name) <= 90 + 1e-8:
                continue
            values = vector_field(frame / 'U', 221, average=True)
            boundary_rows.append([float(frame.name)] + [value for index in indices for value in values[index]])
        target = plane_statistics(boundary_rows, contract)
        with (case / 'synthetic_inflow_target_probe_series.csv').open(encoding='utf-8', newline='') as handle:
            source = list(csv.DictReader(handle))
        if len(source) != 4800 or any(not close([float(source[i * 16 + p][key]) for key in ('Ux_m_s', 'Uy_m_s', 'Uz_m_s')], row[1 + p * 3:4 + p * 3], 1e-8)
                                     or source[i * 16 + p]['probe_id'] != f'p{p:02d}' or int(source[i * 16 + p]['boundary_point_index']) != indices[p]
                                     or abs(float(source[i * 16 + p]['time_s']) - row[0]) > 1e-8
                                     for i, row in enumerate(boundary_rows) for p in range(16)):
            raise ValueError('visible_target_not_from_boundary_values')
        target_claim = load(case / 'synthetic_inflow_target_statistics.json')
        for p, item in enumerate(target['probe_statistics']):
            if target_claim['probe_statistics'][p]['probe_id'] != f'p{p:02d}' or not close(target_claim['probe_statistics'][p]['components'], item['components']):
                raise ValueError('target_statistics_not_recomputed')
        inlet = plane_statistics(read_probes(case / 'postProcessing/inletProbes/0/U', points_for(table, 'inlet')), contract)
        downstream = plane_statistics(read_probes(case / 'postProcessing/downstreamProbes/0/U', points_for(table, 'downstream')), contract)
        comparisons = {'inlet_comparison': compare_to_target(inlet, target), 'downstream_comparison': compare_to_target(downstream, target)}
        validation = {'inlet': inlet, 'downstream': downstream, **comparisons, PRIMARY_ID: comparisons['inlet_comparison']['spectral_band_mae']}
        limits = contract['frozen_gates']
        config_checks = {'version': VERSION, 'task_id': TASK_ID, 'case_id': CASE_ID, 'cell_count': 365488,
                         'mpi_ranks': 16, 'solver': 'pimpleFoam', 'model': 'WALE', 'dt': 0.02, 'end_time': 90,
                         'statistics_start': 30, 'smoke': False, 'synthetic_inflow_contract_sha256': CONTRACT_HASH,
                         'synthetic_inflow_generator_sha256': GENERATOR_HASH, 'synthetic_inflow_probe_table_sha256': PROBES_HASH}
        gates = {
            'frozen_inputs': True, 'target_boundary_consistency': True,
            'config': all(cfg.get(key) == value for key, value in config_checks.items()),
            'config_probe_coordinates': close(cfg.get('inlet_probe_points'), points_for(table, 'inlet')) and close(cfg.get('downstream_probe_points'), points_for(table, 'downstream')),
            'mesh': 'Mesh OK.' in mesh and re.findall(r'cells:\s+(\d+)', mesh)[-1:] == ['365488'],
            'end_time': len(times) == 4500 and abs(times[-1] - 90) < 1e-7 and all(abs((b - a) - 0.02) < 1e-7 for a, b in zip(times, times[1:])),
            'model': 'Selecting LES turbulence model WALE' in log and 'OPENFOAM=2412' in log and 'version=v2412' in log and 'nProcs : 16' in log,
            'finite_log': 'FOAM FATAL' not in log and not re.search(r'\b(?:nan|inf)\b', log, re.I) and bool(re.search(r'^End\s*$', log, re.M)),
            'courant': bool(courant) and max(courant) <= limits['max_courant'],
            'continuity': bool(continuity) and max(map(abs, continuity)) <= limits['max_abs_cumulative_continuity'],
            'mean_fields': check_mean_field(case / final_dir / 'UMean', 'UMean', 3) and check_mean_field(case / final_dir / 'UPrime2Mean', 'UPrime2Mean', 6),
            'statistics_window': 'timeStart 30;' in controls and 'endTime 90;' in controls and 'deltaT 0.02;' in controls,
            'mapped_inlet': all(token in inlet_boundary for token in ('type timeVaryingMappedFixedValue;', 'setAverage true;', 'mapMethod nearest;')),
            'analysis_report': analysis.get('version') == VERSION and analysis.get('case_id') == CASE_ID and analysis.get('all_run_gates_pass') is True and close(analysis.get('synthetic_inflow_validation'), validation),
            'sample_count': inlet['sample_count'] == downstream['sample_count'] == limits['minimum_sample_count'],
        }
        for plane, prefix in (('inlet_comparison', 'inlet'), ('downstream_comparison', 'downstream')):
            comparison = comparisons[plane]
            gates[prefix + '_mean'] = comparison['mean_velocity_rmse_m_s'] <= limits[prefix + '_mean_velocity_rmse_max_m_s']
            gates[prefix + '_stress'] = comparison['diagonal_reynolds_stress_relative_error'] <= limits[prefix + '_reynolds_stress_relative_error_max']
            gates[prefix + '_spectrum'] = comparison['spectral_band_mae'] <= limits['max_primary_metric' if prefix == 'inlet' else 'downstream_spectral_band_mae_max']
        gates['inlet_integral_scale'] = comparisons['inlet_comparison']['integral_time_scale_relative_error'] <= limits['inlet_integral_time_scale_relative_error_max']
        commands = load(case / 'run_commands.json')
        gates['commands'] = all(load(case / f'command.{name}.json').get('exit_code') == 0 for name in ('blockMesh', 'checkMesh', 'decomposePar', 'pimpleFoam', 'reconstructPar'))
        files = [{'path': name, 'bytes': (case / name).stat().st_size, 'sha256': digest(case / name)} for name in names]
        return {'case_id': CASE_ID, 'version': VERSION, 'gates': gates, 'validation': validation, 'target': target,
                'final_time': times[-1], 'max_courant': max(courant), 'max_abs_cumulative_continuity': max(map(abs, continuity)),
                'final_mean_field_directory': final_dir, 'file_count': len(files), 'files': files, 'run_commands': commands,
                'diagnostic_only_half_window_velocity_drift': analysis.get('half_window_velocity_drift'),
                'diagnostic_boundary': 'The task freezes inlet/downstream spectral gates for forced synthetic inflow, not generic wake-stationarity thresholds.'}
