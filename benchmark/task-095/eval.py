#!/usr/bin/env python3
"""Task-095 raw stability-regime city-LES numerical reference evaluator."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-095"
PRIMARY_ID = "les_stability_exchange_response"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)")


def load(path: Path, limit: int = 64 * 1024 * 1024):
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
    if root not in path.parents or not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + str(value))
    return path


def probe_data(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))]
              for match in re.finditer(r"^# Probe \d+ \(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates_mismatch:" + path.name)
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            time = float(line.split()[0])
        except (ValueError, IndexError):
            continue
        values = [list(map(float, value)) for value in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        if len(values) != len(expected_points):
            raise ValueError("probe_row_shape:" + path.name)
        flat = [item for vector_value in values for item in vector_value] if vector else values
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_nonfinite:" + path.name)
        rows.append((time, values))
    if len(rows) != 300 or abs(rows[-1][0] - 60.0) > 1e-8 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("probe_time_contract:" + path.name)
    return points, [row[0] for row in rows], [row[1] for row in rows]


def window_means(times, velocities, temperatures, start, end):
    exchange = []
    thermal = []
    for time, rows_u, row_t in zip(times, velocities, temperatures):
        if start <= time <= end:
            exchange.extend(max(float(vector[2]), 0.0) for vector in rows_u)
            thermal.extend(float(value) for value in row_t)
    if not exchange or not thermal:
        raise ValueError("empty_window")
    return sum(exchange) / len(exchange), sum(thermal) / len(thermal)


def log_gates(solver_log: Path, mesh_log: Path) -> dict:
    solver = solver_log.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_log.read_text(encoding="utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"^Time = ([\deE+.-]+)", solver, re.M)]
    courant = [float(value) for value in re.findall(r"Courant Number mean: [\deE+.-]+ max: ([\deE+.-]+)", solver)]
    continuity = [float(value) for value in re.findall(r"cumulative = ([\deE+.-]+)", solver)]
    cells = [int(value) for value in re.findall(r"cells:\s+(\d+)", mesh)]
    gates = {
        "mesh_ok": "Mesh OK." in mesh and cells[-1:] == [141952],
        "final_time": bool(times) and abs(times[-1] - 60.0) <= 1e-8,
        "finite_log": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "courant": bool(courant) and max(courant) <= 1.0,
        "continuity": bool(continuity) and max(abs(value) for value in continuity) <= 1e-4,
    }
    return {"gates": gates, "max_courant": max(courant) if courant else None,
            "max_abs_cumulative_continuity": max((abs(value) for value in continuity), default=None)}


def parse_case_input(control: Path, temperature: Path, expected_q: float) -> dict:
    control_text = control.read_text(encoding="utf-8", errors="replace")
    temperature_text = temperature.read_text(encoding="utf-8", errors="replace")
    dt = [float(value) for value in re.findall(r"\bdeltaT\s+([-+0-9.eE]+);", control_text)]
    end = [float(value) for value in re.findall(r"\bendTime\s+([-+0-9.eE]+);", control_text)]
    q = [float(value) for value in re.findall(r"\bq\s+uniform\s+([-+0-9.eE]+);", temperature_text)]
    return {"dt": dt, "end": end, "q": q,
            "pass": dt == [0.015] and end == [60.0] and q == [expected_q]}


def finite_array(value, length):
    return isinstance(value, list) and len(value) == length and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item))
        for item in value
    )


def finite_matrix(value, rows, columns):
    return isinstance(value, list) and len(value) == rows and all(finite_array(row, columns) for row in value)


def finite_vector_cube(value, rows, points):
    return isinstance(value, list) and len(value) == rows and all(
        isinstance(row, list) and len(row) == points and all(finite_array(vector, 3) for vector in row)
        for row in value
    )


def flatten(value):
    if isinstance(value, list):
        return [item for child in value for item in flatten(child)]
    return [float(value)]


def nmae(left, right, scale):
    left_values, right_values = flatten(left), flatten(right)
    if len(left_values) != len(right_values) or not left_values:
        raise ValueError("nmae_shape")
    return sum(abs(a - b) for a, b in zip(left_values, right_values)) / len(left_values) / max(scale, 1e-30)


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    verified = []
    recomputed_cases = []
    hidden_deltas = []
    report_ok = False
    metric_ok = False
    try:
        contract = load(root / "local_assets" / "stability_les_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")), 128 * 1024 * 1024)
        evidence = load(root / "stability_les_evidence_manifest.json")
        simulation = load(root / "simulation_metrics.json", 128 * 1024 * 1024)
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, simulation)):
            raise ValueError("task_id_mismatch")
        expected_cases = contract["cases"]
        ids = [item["case_id"] for item in expected_cases]
        evidence_cases = evidence.get("cases")
        reported_cases = simulation.get("case_results")
        if not isinstance(evidence_cases, list) or [item.get("case_id") for item in evidence_cases] != ids:
            raise ValueError("evidence_case_ids")
        if not isinstance(reported_cases, list) or [item.get("case_id") for item in reported_cases] != ids:
            raise ValueError("reported_case_ids")
        hidden_by_id = {item["case_id"]: item for item in hidden["case_results"]}
        used = set()
        report_ok = True
        for expected, evidence_case, reported in zip(expected_cases, evidence_cases, reported_cases):
            case_id = expected["case_id"]
            if (evidence_case.get("variant") != expected["variant"]
                    or reported.get("variant") != expected["variant"]
                    or evidence_case.get("stability_regime") != expected["stability_regime"]
                    or reported.get("stability_regime") != expected["stability_regime"]):
                raise ValueError("variant_mismatch:" + case_id)
            paths = {}
            for key in ("probe_U", "probe_T", "solver_log", "checkMesh_log", "controlDict", "initial_T"):
                item = evidence_case.get(key)
                if not isinstance(item, dict):
                    raise ValueError("evidence_item:" + case_id + ":" + key)
                path = evidence_path(root, item.get("path"))
                if path in used or item.get("sha256") != sha256(path):
                    raise ValueError("evidence_hash_or_duplicate:" + case_id + ":" + key)
                used.add(path)
                paths[key] = path
            _, times_u, probe_u = probe_data(paths["probe_U"], contract["probe_points_m"], True)
            _, times_t, probe_t = probe_data(paths["probe_T"], contract["probe_points_m"], False)
            if times_u != times_t:
                raise ValueError("probe_time_alignment:" + case_id)
            gates = log_gates(paths["solver_log"], paths["checkMesh_log"])
            case_input = parse_case_input(paths["controlDict"], paths["initial_T"], float(expected["ground_heat_flux_w_m2"]))
            early = window_means(times_u, probe_u, probe_t, 20.0, 40.0)
            late = window_means(times_u, probe_u, probe_t, 40.0, 60.0)
            overall = window_means(times_u, probe_u, probe_t, 20.0, 60.0)
            physical_gates = {
                "temperature_physical": 250.0 <= min(early[1], late[1]) <= max(early[1], late[1]) <= 350.0,
                "vertical_exchange_stationary": abs(late[0] - early[0]) <= 0.05,
                "temperature_stationary": abs(late[1] - early[1]) <= 0.5,
            }
            all_gates = all(gates["gates"].values()) and case_input["pass"] and all(physical_gates.values())
            if not all_gates:
                raise ValueError("raw_case_gate_failed:" + case_id)
            raw = {
                "case_id": case_id, "variant": expected["variant"],
                "stability_regime": expected["stability_regime"],
                "ground_heat_flux_w_m2": expected["ground_heat_flux_w_m2"],
                "time_s": times_u, "probe_U_m_s": probe_u, "probe_T_k": probe_t,
                "vertical_exchange_m_s": overall[0], "mean_probe_temperature_k": overall[1],
                "early_vertical_exchange_m_s": early[0], "late_vertical_exchange_m_s": late[0],
                "vertical_exchange_half_window_drift_m_s": abs(late[0] - early[0]),
                "mean_temperature_half_window_drift_k": abs(late[1] - early[1]),
                "max_courant": gates["max_courant"],
                "max_abs_cumulative_continuity": gates["max_abs_cumulative_continuity"],
                "all_run_gates_pass": True,
            }
            recomputed_cases.append(raw)
            verified.append(case_id)
            scalar_fields = [
                "ground_heat_flux_w_m2", "vertical_exchange_m_s", "mean_probe_temperature_k",
                "vertical_exchange_half_window_drift_m_s", "mean_temperature_half_window_drift_k",
                "max_courant", "max_abs_cumulative_continuity",
            ]
            if not (finite_array(reported.get("time_s"), 300)
                    and finite_vector_cube(reported.get("probe_U_m_s"), 300, 24)
                    and finite_matrix(reported.get("probe_T_k"), 300, 24)
                    and reported.get("all_run_gates_pass") is True):
                report_ok = False
            else:
                comparisons = [
                    nmae(reported["time_s"], times_u, 60.0),
                    nmae(reported["probe_U_m_s"], probe_u, 1.2),
                    nmae(reported["probe_T_k"], probe_t, 300.0),
                ]
                if any(value > 1e-8 for value in comparisons) or any(
                    not isinstance(reported.get(key), (int, float)) or isinstance(reported.get(key), bool)
                    or abs(float(reported[key]) - float(raw[key])) > max(1e-9, abs(float(raw[key])) * 1e-7)
                    for key in scalar_fields
                ):
                    report_ok = False
            reference = hidden_by_id[case_id]
            hidden_deltas.append(max(
                nmae(times_u, reference["time_s"], 60.0),
                nmae(probe_u, reference["probe_U_m_s"], 1.2),
                nmae(probe_t, reference["probe_T_k"], 300.0),
            ))
        recomputed_by_regime = {item["stability_regime"]: item for item in recomputed_cases}
        if set(recomputed_by_regime) != {"stable", "neutral", "unstable"}:
            raise ValueError("stability_regimes")
        neutral = recomputed_by_regime["neutral"]["vertical_exchange_m_s"]
        denominator = max(abs(neutral), 0.01)
        responses = {
            regime: (recomputed_by_regime[regime]["vertical_exchange_m_s"] - neutral) / denominator
            for regime in ("stable", "unstable")
        }
        primary = sum(abs(value) for value in responses.values()) / 2.0
        reported_primary = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = isinstance(reported_primary, (int, float)) and not isinstance(reported_primary, bool) \
            and math.isfinite(float(reported_primary)) and abs(float(reported_primary) - primary) <= 1e-10
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
    except Exception as exc:
        errors.append("stability_les_reference_check:" + str(exc))

    coverage = len(verified) / 3
    components = {"raw_stability_les_evidence": 0.0, "reported_array_recomputation": 0.0,
                  "primary_metric_recomputation": 0.0, "hidden_numerical_fidelity": 0.0}
    if not base.get("errors"):
        components["raw_stability_les_evidence"] = 12.0 * coverage
        if report_ok:
            components["reported_array_recomputation"] = 8.0
        if metric_ok:
            components["primary_metric_recomputation"] = 5.0
        if hidden_deltas:
            components["hidden_numerical_fidelity"] = 15.0 * max(0.0, 1.0 - sum(hidden_deltas) / len(hidden_deltas) / 0.05)
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0,
        "status": "openfoam_stability_les_numerical_reference_check",
        "errors": errors,
        "components": components,
        "diagnostics": {"verified_case_ids": verified, "case_coverage": coverage,
                        "recomputed_cases": recomputed_cases, "hidden_case_deltas": hidden_deltas},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM v2412 stable/neutral/unstable LES evidence and hidden numerical probe series; not experimental/PIV/DNS/wind-tunnel/field truth or release validation.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
