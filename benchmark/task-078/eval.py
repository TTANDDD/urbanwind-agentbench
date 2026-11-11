#!/usr/bin/env python3
"""Task-078 raw-probe precursor-statistics evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-078"
CASE_ID = "task-078-case-001"
PRIMARY_ID = "precursor_block_mean_drift"
NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
VECTOR = re.compile(r"\(\s*(%s)\s+(%s)\s+(%s)\s*\)" % (NUMBER, NUMBER, NUMBER))


def load(path: Path, limit: int = 256 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + str(value))
    return path


def expected_points(path: Path) -> list[list[float]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    points = [[float(row[key]) for key in ("x_m", "y_m", "z_m")] for row in rows]
    if len(points) != 64:
        raise ValueError("expected_probe_count")
    return points


def read_u_probes(path: Path, points: list[list[float]]) -> list[list[float]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    actual = [[float(value) for value in re.findall(NUMBER, match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if actual != points:
        raise ValueError("probe_coordinates")
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        vectors = [tuple(map(float, item)) for item in VECTOR.findall(line)]
        if len(vectors) != 64:
            raise ValueError("probe_shape")
        row = [float(line.split()[0])]
        for vector in vectors:
            row.extend(vector)
        if not all(math.isfinite(value) for value in row):
            raise ValueError("probe_nonfinite")
        rows.append(row)
    return rows


def block_statistics(rows: list[list[float]], lower: float, upper: float) -> dict:
    selected = [row for row in rows if lower < row[0] <= upper + 1e-8]
    if len(selected) != 300:
        raise ValueError("block_sample_count")
    times = [row[0] for row in selected]
    dt = sum(right - left for left, right in zip(times, times[1:])) / (len(times) - 1)
    if abs(dt - 0.2) > 1e-8:
        raise ValueError("sampling_interval")
    values = [[row[1 + 3 * probe] for probe in range(64)] for row in selected]
    means = [sum(row[index] for row in values) / len(values) for index in range(64)]
    centered = [[row[index] - means[index] for row in values] for index in range(64)]
    variances = [sum(value * value for value in series) / len(series) for series in centered]
    integral = []
    for series in centered:
        denominator = sum(value * value for value in series)
        if denominator <= 1e-30:
            integral.append(0.0)
            continue
        total = 0.0
        for lag in range(len(series)):
            correlation = sum(series[index] * series[index + lag] for index in range(len(series) - lag)) / denominator
            if lag and correlation <= 0.0:
                break
            total += correlation
        integral.append(0.2 * (total - 0.5))
    edges = [(0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.500000001)]
    frequencies = [index / (len(values) * 0.2) for index in range(1, len(values) // 2 + 1)]
    per_probe = []
    for series in centered:
        energies = []
        for frequency_index in range(1, len(values) // 2 + 1):
            real = sum(value * math.cos(-2.0 * math.pi * frequency_index * sample / len(values)) for sample, value in enumerate(series))
            imag = sum(value * math.sin(-2.0 * math.pi * frequency_index * sample / len(values)) for sample, value in enumerate(series))
            energies.append(real * real + imag * imag)
        total = sum(energies)
        per_probe.append([sum(energy for frequency, energy in zip(frequencies, energies) if low <= frequency < high) / total for low, high in edges])
    bands = [sum(item[index] for item in per_probe) / 64 for index in range(5)]
    return {
        "sample_count": 300,
        "sampling_interval_s": dt,
        "mean_Ux_m_s": means,
        "variance_Ux_m2_s2": variances,
        "mean_variance_Ux_m2_s2": sum(variances) / 64,
        "normalized_psd_band_fractions": bands,
        "integral_time_scale_s": sum(integral) / 64,
    }


def compare(first: dict, second: dict) -> tuple[dict, float]:
    components = {
        "mean_drift": sum(abs(a - b) for a, b in zip(first["mean_Ux_m_s"], second["mean_Ux_m_s"])) / 64 / 1.2,
        "variance_drift": sum(abs(a - b) for a, b in zip(first["variance_Ux_m2_s2"], second["variance_Ux_m2_s2"])) / 64 / 1.44,
        "spectrum_drift": 0.5 * sum(abs(a - b) for a, b in zip(first["normalized_psd_band_fractions"], second["normalized_psd_band_fractions"])),
        "integral_scale_drift": abs(first["integral_time_scale_s"] - second["integral_time_scale_s"]) / max(abs(second["integral_time_scale_s"]), 0.2),
    }
    return components, max(components.values())


def log_gates(solver_path: Path, mesh_path: Path) -> dict:
    solver = solver_path.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_path.read_text(encoding="utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"^Time = (%s)" % NUMBER, solver, re.M)]
    courant = [float(value) for value in re.findall(r"Courant Number mean: %s max: (%s)" % (NUMBER, NUMBER), solver)]
    continuity = [float(value) for value in re.findall(r"cumulative = (%s)" % NUMBER, solver)]
    cells = [int(value) for value in re.findall(r"cells:\s+(\d+)", mesh)]
    return {
        "mesh": "Mesh OK." in mesh and cells[-1:] == [365488],
        "end_time": bool(times) and abs(times[-1] - 420.0) <= 1e-6,
        "model": "Selecting LES turbulence model WALE" in solver,
        "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "courant": bool(courant) and max(courant) <= 1.0,
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
    }


def close(left, right, tolerance=1e-12) -> bool:
    return isinstance(left, (int, float)) and not isinstance(left, bool) and abs(float(left) - float(right)) <= max(tolerance, abs(float(right)) * 1e-8)


def vector_close(left, right, tolerance=1e-12) -> bool:
    return isinstance(left, list) and isinstance(right, list) and len(left) == len(right) and all(close(a, b, tolerance) for a, b in zip(left, right))


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    raw_ok = report_ok = hidden_ok = False
    recomputed = {}
    try:
        contract = load(root / "local_assets/precursor_statistics_contract.json")
        points = expected_points(root / "local_assets/precursor_probe_points.csv")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "precursor_evidence_manifest.json")
        reported = load(root / "precursor_results.json")
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, reported, metrics)):
            raise ValueError("task_id_mismatch")
        case = evidence.get("case")
        submitted = reported.get("case_result")
        if not isinstance(case, dict) or case.get("case_id") != CASE_ID or not isinstance(submitted, dict) or submitted.get("case_id") != CASE_ID:
            raise ValueError("case_id_mismatch")
        required = ("raw_u_probes", "solver_log", "checkMesh_log", "resolved_config", "run_analysis", "field_UMean", "field_UPrime2Mean")
        paths, used = {}, set()
        for key in required:
            item = case.get(key)
            if not isinstance(item, dict) or set(item) < {"path", "sha256"}:
                raise ValueError("evidence_item:" + key)
            path = evidence_path(root, item["path"])
            if sha256(path) != item["sha256"] or item["sha256"] in used:
                raise ValueError("evidence_hash_or_duplicate:" + key)
            used.add(item["sha256"]); paths[key] = path
        rows = read_u_probes(paths["raw_u_probes"], points)
        first = block_statistics(rows, 300.0, 360.0)
        second = block_statistics(rows, 360.0, 420.0)
        components, metric = compare(first, second)
        config = load(paths["resolved_config"])
        analysis = load(paths["run_analysis"])
        gates = {
            **log_gates(paths["solver_log"], paths["checkMesh_log"]),
            "config": config.get("version") == hidden.get("version") and config.get("case_id") == CASE_ID and config.get("mpi_ranks") == 16,
            "analysis": analysis.get("all_run_gates_pass") is True and all(analysis.get("gates", {}).values()),
            "mean_fields": paths["field_UMean"].stat().st_size > 0 and paths["field_UPrime2Mean"].stat().st_size > 0,
            "sample_counts": first["sample_count"] == second["sample_count"] == 300,
            "variability": min(first["mean_variance_Ux_m2_s2"], second["mean_variance_Ux_m2_s2"]) >= 0.005,
            "integral_scales": all(0.1 <= item["integral_time_scale_s"] <= 30 for item in (first, second)),
            "metric": metric <= float(contract["frozen_gates"]["max_primary_metric"]),
        }
        if not all(gates.values()):
            raise ValueError("raw_case_gate_failed")
        recomputed = {"blocks": [first, second], "components": components, PRIMARY_ID: metric, "gates": gates}
        raw_ok = True
        if not close(submitted.get(PRIMARY_ID), metric) or not all(close(submitted.get("components", {}).get(key), value) for key, value in components.items()):
            raise ValueError("reported_result_not_recomputed")
        if not close(metrics.get("values", {}).get(PRIMARY_ID), metric) or metrics.get("units", {}).get(PRIMARY_ID) != "fraction":
            raise ValueError("reported_metric_not_recomputed")
        report_ok = True
        reference = hidden["case_results"][0]
        if reference.get("case_id") != CASE_ID or reference.get("source_archive_sha256") != hidden.get("archive_sha256"):
            raise ValueError("hidden_identity")
        for index, block in enumerate((first, second)):
            expected = reference["blocks"][index]
            if not vector_close(block["mean_Ux_m_s"], expected["mean_Ux_m_s"]) or not vector_close(block["variance_Ux_m2_s2"], expected["variance_Ux_m2_s2"]):
                raise ValueError("hidden_block_arrays")
            if not vector_close(block["normalized_psd_band_fractions"], expected["normalized_psd_band_fractions"]) or not close(block["integral_time_scale_s"], expected["integral_time_scale_s"]):
                raise ValueError("hidden_block_statistics")
        if not close(metric, reference[PRIMARY_ID]):
            raise ValueError("hidden_primary_metric")
        hidden_ok = True
    except Exception as exc:
        errors.append(str(exc))
    score = float(base.get("score", 0.0))
    if raw_ok: score += 25.0
    if report_ok: score += 10.0
    if hidden_ok: score += 5.0
    return {
        "score": min(score, 100.0), "errors": errors, "contract_score": base.get("score", 0.0),
        "status": "openfoam_precursor_raw_probe_reference_check",
        "raw_evidence_pass": raw_ok, "reported_result_pass": report_ok, "hidden_reference_pass": hidden_ok,
        "recomputed": recomputed,
        "claim_boundary": "Task-specific raw-probe OpenFOAM numerical check; not a classical periodic precursor or experimental truth.",
    }


def main() -> int:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
