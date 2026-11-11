#!/usr/bin/env python3
"""Task-076 raw potential-temperature/hydrostatic OpenFOAM evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-076"
CASE_ID = "task-076-case-001"
PRIMARY_ID = "hydrostatic_initialization_residual"
NUMBER = r"[-+0-9.eE]+"
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


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_vectors(path: Path) -> list[tuple[float, float, float]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\binternalField\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\)\s*;", text, re.S)
    if not match:
        raise ValueError("vector_internal_field:" + path.name)
    values = [tuple(map(float, item)) for item in VECTOR.findall(match.group(2))]
    if len(values) != int(match.group(1)):
        raise ValueError("vector_internal_count:" + path.name)
    return values


def parse_scalars(path: Path) -> list[float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"\binternalField\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;", text, re.S)
    if not match:
        raise ValueError("scalar_internal_field:" + path.name)
    values = [float(value) for value in re.findall(NUMBER, match.group(2))]
    if len(values) != int(match.group(1)):
        raise ValueError("scalar_internal_count:" + path.name)
    return values


def theta(z: float, contract: dict) -> float:
    profile = contract["potential_temperature_profile"]
    theta0 = float(profile["mixed_layer"]["theta_k"])
    mixed_top = float(profile["mixed_layer"]["z_m"][1])
    inversion_top = float(profile["inversion_layer"]["z_m"][1])
    inversion_gradient = float(profile["inversion_layer"]["gradient_k_m"])
    free_gradient = float(profile["free_atmosphere"]["gradient_k_m"])
    if z <= mixed_top:
        return theta0
    if z <= inversion_top:
        return theta0 + inversion_gradient * (z - mixed_top)
    return theta0 + inversion_gradient * (inversion_top - mixed_top) + free_gradient * (z - inversion_top)


def inverse_theta_integral(z: float, contract: dict) -> float:
    profile = contract["potential_temperature_profile"]
    theta0 = float(profile["mixed_layer"]["theta_k"])
    mixed_top = float(profile["mixed_layer"]["z_m"][1])
    inversion_top = float(profile["inversion_layer"]["z_m"][1])
    inversion_gradient = float(profile["inversion_layer"]["gradient_k_m"])
    free_gradient = float(profile["free_atmosphere"]["gradient_k_m"])
    if z <= mixed_top:
        return z / theta0
    first = mixed_top / theta0
    if z <= inversion_top:
        return first + math.log(theta(z, contract) / theta0) / inversion_gradient
    theta_top = theta(inversion_top, contract)
    return first + math.log(theta_top / theta0) / inversion_gradient + math.log(theta(z, contract) / theta_top) / free_gradient


def target_pressure(z: float, contract: dict) -> float:
    constants = contract["thermodynamic_constants"]
    p0 = float(constants["reference_pressure_pa"])
    cp = float(constants["cp_j_kg_k"])
    kappa = float(constants["kappa_R_over_Cp"])
    gravity = float(constants["gravity_m_s2"])
    exner = 1.0 - gravity * inverse_theta_integral(z, contract) / cp
    return p0 * exner ** (1.0 / kappa)


def initial_metric(paths: dict, contract: dict) -> dict:
    centres = parse_vectors(paths["field_C0"])
    temperatures = parse_scalars(paths["field_T0"])
    pressures = parse_scalars(paths["field_p0"])
    reduced = parse_scalars(paths["field_p_rgh0"])
    if not (len(centres) == len(temperatures) == len(pressures) == len(reduced) == 141952):
        raise ValueError("initial_field_shape")
    constants = contract["thermodynamic_constants"]
    p0 = float(constants["reference_pressure_pa"])
    gas_constant = float(constants["specific_gas_constant_j_kg_k"])
    kappa = float(constants["kappa_R_over_Cp"])
    gravity = float(constants["gravity_m_s2"])
    theta_sq = pressure_sq = reduced_sq = 0.0
    for centre, temperature, pressure, p_rgh in zip(centres, temperatures, pressures, reduced):
        theta_actual = temperature * (p0 / pressure) ** kappa
        density = pressure / (gas_constant * temperature)
        closure = pressure + density * gravity * centre[2]
        theta_sq += (theta_actual - theta(centre[2], contract)) ** 2
        pressure_sq += (pressure - target_pressure(centre[2], contract)) ** 2
        reduced_sq += (p_rgh - closure) ** 2
    count = len(centres)
    theta_rmse = math.sqrt(theta_sq / count)
    pressure_rmse = math.sqrt(pressure_sq / count)
    reduced_rmse = math.sqrt(reduced_sq / count)
    return {
        "cell_count": count, "theta_rmse_k": theta_rmse, "pressure_rmse_pa": pressure_rmse,
        "p_rgh_closure_rmse_pa": reduced_rmse,
        PRIMARY_ID: max(theta_rmse, pressure_rmse / 100.0, reduced_rmse / 100.0),
    }


def profile_contract(path: Path, contract: dict) -> bool:
    rows = csv_rows(path)
    return len(rows) == 29 and all(
        abs(float(row["theta_target_k"]) - theta(float(row["z_m"]), contract)) <= 1e-9
        and abs(float(row["pressure_target_pa"]) - target_pressure(float(row["z_m"]), contract)) <= 1e-6
        for row in rows
    )


def probe(path: Path, expected_points: list[list[float]], vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in re.findall(NUMBER, match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates:" + path.name)
    times = []
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        time = float(line.split()[0])
        values = [list(map(float, item)) for item in VECTOR.findall(line)] if vector else [float(value) for value in re.findall(NUMBER, line)][1:]
        flat = [item for child in values for item in child] if vector else values
        if len(values) != len(points) or not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_shape_or_nonfinite:" + path.name)
        times.append(time); rows.append(values)
    if len(times) != 150 or abs(times[-1] - 30.0) > 1e-8 or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("probe_time_contract:" + path.name)
    return times, rows


def log_gates(paths: dict):
    solver = paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    mesh = paths["checkMesh_log"].read_text(encoding="utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"^Time = (%s)" % NUMBER, solver, re.M)]
    courant = [float(value) for value in re.findall(r"Courant Number mean: %s max: (%s)" % (NUMBER, NUMBER), solver)]
    continuity = [float(value) for value in re.findall(r"cumulative = (%s)" % NUMBER, solver)]
    cells = [int(value) for value in re.findall(r"cells:\s+(\d+)", mesh)]
    gates = {
        "mesh": "Mesh OK." in mesh and cells[-1:] == [141952],
        "final_time": bool(times) and abs(times[-1] - 30.0) <= 1e-8,
        "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "courant": bool(courant) and max(courant) <= 1.0,
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 1e-4,
    }
    return gates, max(courant), max(map(abs, continuity))


def window(times, velocities, temperatures, start, end):
    exchange = []; thermal = []
    for time, row_u, row_t in zip(times, velocities, temperatures):
        if start <= time <= end:
            exchange.extend(max(float(value[2]), 0.0) for value in row_u)
            thermal.extend(float(value) for value in row_t)
    return sum(exchange) / len(exchange), sum(thermal) / len(thermal)


def flatten(value):
    return [item for child in value for item in flatten(child)] if isinstance(value, list) else [float(value)]


def nmae(left, right, scale):
    a, b = flatten(left), flatten(right)
    if len(a) != len(b) or not a:
        raise ValueError("nmae_shape")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) / max(scale, 1e-30)


def finite_array(value, length):
    return isinstance(value, list) and len(value) == length and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value)


def finite_matrix(value, rows, cols):
    return isinstance(value, list) and len(value) == rows and all(finite_array(row, cols) for row in value)


def finite_cube(value, rows, points):
    return isinstance(value, list) and len(value) == rows and all(isinstance(row, list) and len(row) == points and all(finite_array(vector, 3) for vector in row) for row in value)


def evaluate_task(root: Path):
    root = root.resolve(); base = precheck(root, TASK_ID); errors = list(base.get("errors", []))
    raw_ok = report_ok = metric_ok = hidden_ok = False; recomputed = {}; hidden_delta = math.inf
    try:
        contract = load(root / "local_assets/potential_temperature_initialization_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "potential_temperature_evidence_manifest.json")
        reported = load(root / "potential_temperature_results.json")
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, reported, metrics)):
            raise ValueError("task_id_mismatch")
        case = evidence.get("case"); submitted = reported.get("case_result")
        if not isinstance(case, dict) or case.get("case_id") != CASE_ID or not isinstance(submitted, dict) or submitted.get("case_id") != CASE_ID:
            raise ValueError("case_id_mismatch")
        used = set(); paths = {}
        for key in contract["required_raw_evidence_per_case"]:
            item = case.get(key)
            if not isinstance(item, dict):
                raise ValueError("evidence_item:" + key)
            path = evidence_path(root, item.get("path"))
            if path in used or item.get("sha256") != sha256(path):
                raise ValueError("evidence_hash_or_duplicate:" + key)
            used.add(path); paths[key] = path
        initial = initial_metric(paths, contract)
        profile_ok = profile_contract(paths["profile_csv"], contract)
        with (root / "local_assets/thermodynamic_response_probes.csv").open(encoding="utf-8", newline="") as handle:
            points = [[float(row[key]) for key in ("x_m", "y_m", "z_m")] for row in csv.DictReader(handle)]
        times, velocities = probe(paths["probe_U"], points, True)
        ttimes, temperatures = probe(paths["probe_T"], points, False)
        first = window(times, velocities, temperatures, 10, 20); second = window(times, velocities, temperatures, 20, 30)
        exchange_drift = abs(second[0] - first[0]); temperature_drift = abs(second[1] - first[1])
        gates, max_co, max_cont = log_gates(paths)
        control = paths["controlDict"].read_text(encoding="utf-8", errors="replace")
        turbulence = paths["turbulenceProperties"].read_text(encoding="utf-8", errors="replace")
        thermophysics = paths["thermophysicalProperties"].read_text(encoding="utf-8", errors="replace")
        gravity_text = paths["gravity"].read_text(encoding="utf-8", errors="replace")
        raw_gates = {
            **gates, "profile": profile_ok, "initial_metric": initial[PRIMARY_ID] <= 1e-8,
            "times_aligned": times == ttimes,
            "temperature": min(value for row in temperatures for value in row) >= 250 and max(value for row in temperatures for value in row) <= 350,
            "bounded_response_drift": exchange_drift <= 0.1 and temperature_drift <= 1.0,
            "control": bool(re.search(r"\bendTime\s+30(?:\.0+)?\s*;", control)) and bool(re.search(r"\bdeltaT\s+0\.015\s*;", control)),
            "wale": "simulationType LES;" in turbulence and "LESModel WALE;" in turbulence,
            "thermophysics": "molWeight 28.9;" in thermophysics and "Cp 1000;" in thermophysics,
            "gravity": "value (0 0 -9.81);" in gravity_text,
            "mean_fields": all(paths[name].stat().st_size > 0 for name in ("mean_UMean", "mean_UPrime2Mean", "mean_TMean", "mean_TPrime2Mean")),
        }
        raw_ok = all(raw_gates.values())
        if not raw_ok:
            errors.append("raw_case_gate_failed")
        recomputed = {
            "case_id": CASE_ID, "time_s": times, "probe_U_m_s": velocities, "probe_T_k": temperatures,
            **initial, "vertical_exchange_drift_m_s": exchange_drift, "temperature_drift_k": temperature_drift,
            "max_courant": max_co, "max_abs_cumulative_continuity": max_cont, "all_run_gates_pass": raw_ok,
        }
        report_ok = finite_array(submitted.get("time_s"), 150) and finite_cube(submitted.get("probe_U_m_s"), 150, 24) and finite_matrix(submitted.get("probe_T_k"), 150, 24) and submitted.get("all_run_gates_pass") is True
        scalar_fields = ("theta_rmse_k", "pressure_rmse_pa", "p_rgh_closure_rmse_pa", PRIMARY_ID, "vertical_exchange_drift_m_s", "temperature_drift_k", "max_courant", "max_abs_cumulative_continuity")
        if report_ok:
            report_ok = max(nmae(submitted["time_s"], times, 30), nmae(submitted["probe_U_m_s"], velocities, 1.2), nmae(submitted["probe_T_k"], temperatures, 300)) <= 1e-8 and all(abs(float(submitted[key]) - float(recomputed[key])) <= max(1e-12, abs(float(recomputed[key])) * 1e-7) for key in scalar_fields)
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        value = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = isinstance(value, (int, float)) and not isinstance(value, bool) and abs(float(value) - initial[PRIMARY_ID]) <= 1e-15
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        reference = hidden["case_results"][0]
        hidden_delta = max(nmae(times, reference["time_s"], 30), nmae(velocities, reference["probe_U_m_s"], 1.2), nmae(temperatures, reference["probe_T_k"], 300), abs(initial[PRIMARY_ID] - reference[PRIMARY_ID]))
        hidden_ok = hidden_delta <= 1e-8
        if not hidden_ok:
            errors.append("hidden_numerical_fidelity")
    except Exception as exc:
        errors.append("potential_temperature_reference_check:" + str(exc))
    components = {
        "raw_openfoam_evidence": 15.0 if raw_ok and not base.get("errors") else 0.0,
        "reported_array_recomputation": 10.0 if report_ok and not base.get("errors") else 0.0,
        "primary_metric_recomputation": 5.0 if metric_ok and not base.get("errors") else 0.0,
        "hidden_numerical_fidelity": 10.0 if hidden_ok and not base.get("errors") else 0.0,
    }
    return {
        "task_id": TASK_ID, "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_potential_temperature_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"recomputed_case": recomputed, "hidden_max_scaled_delta": hidden_delta}, "precheck": base,
        "claim_boundary": "Checks a local analytic potential-temperature/hydrostatic initialization and raw OpenFOAM v2412 evidence; not atmospheric sounding or experimental/PIV/DNS/wind-tunnel/field truth.",
    }


def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
