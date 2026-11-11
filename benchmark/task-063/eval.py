#!/usr/bin/env python3
"""Task-063 raw roughness-diagnosis and repaired OpenFOAM evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-063"
PRIMARY_ID = "roughness_consistency_residual"
FAULTY_CASE = "task-063-case-001"
REPAIRED_CASE = "task-063-case-002"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


def load(path: Path, limit: int = 128 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + str(value))
    return path


def residual(values) -> float:
    return max(abs(left - right) for i, left in enumerate(values) for right in values[i + 1:]) / max(max(values), 1e-6)


def target_profile(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 52:
        raise ValueError("target_profile_count")
    points = [[-4.0, 0.0625, float(row["z_m"])] for row in rows]
    weights = [float(row["layer_thickness_m"]) for row in rows]
    targets = {"U": [float(row["target_Ux_m_s"]) for row in rows],
               "k": [float(row["target_k_m2_s2"]) for row in rows],
               "omega": [float(row["target_omega_s_1"]) for row in rows]}
    return points, weights, targets


def probe_data(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))]
              for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates_mismatch:" + path.name)
    times, rows = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [list(map(float, item)) for item in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        time_values = NUMBER.findall(line.split("(", 1)[0] if vector else line)
        if not time_values:
            continue
        if len(values) != 52:
            raise ValueError("probe_row_shape:" + path.name)
        flat = [value for item in values for value in item] if vector else values
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_nonfinite:" + path.name)
        times.append(float(time_values[0])); rows.append(values)
    if times != [float(value) for value in range(20, 401, 20)]:
        raise ValueError("probe_time_contract:" + path.name)
    return times, rows


def weighted_rmse(values, targets, weights):
    return math.sqrt(sum(weight * (value - target) ** 2 for value, target, weight in zip(values, targets, weights)) / sum(weights))


def log_gates(solver_path: Path, mesh_path: Path):
    solver = solver_path.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_path.read_text(encoding="utf-8", errors="replace")
    iterations = [float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)", solver, re.M)]
    continuity = [float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)", solver)]
    cells = re.findall(r"^\s*cells:\s+(\d+)\s*$", mesh, re.M)
    residuals = {}
    for field in ("Ux", "Uy", "Uz", "k", "omega", "p"):
        values = [float(value) for value in re.findall(r"Solving for " + field + r", Initial residual = ([-+0-9.eE]+)", solver)]
        residuals[field] = max(values[-20:]) if len(values) >= 20 else None
    gates = {
        "mesh": "Mesh OK." in mesh and cells[-1:] == ["365488"],
        "iteration": len(iterations) == 400 and iterations[-1:] == [400.0],
        "model": "Selecting RAS turbulence model kOmegaSST" in solver,
        "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
        "residuals": all(value is not None and math.isfinite(value) and value <= 1e-4 for value in residuals.values()),
        "no_iteration_cap": "No Iterations 1000" not in solver,
    }
    return {"gates": gates, "max_abs_cumulative_continuity": max(map(abs, continuity)) if continuity else None,
            "last20_max_initial_residuals": residuals}


def max_scaled_delta(left, right, scale):
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return max((max_scaled_delta(a, b, scale) for a, b in zip(left, right)), default=0.0)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) / max(float(scale), 1e-30)
    raise ValueError("hidden_shape_mismatch")


def finite_array(value, length):
    return isinstance(value, list) and len(value) == length and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value)


def finite_matrix(value, rows, columns):
    return isinstance(value, list) and len(value) == rows and all(finite_array(row, columns) for row in value)


def finite_cube(value, rows, points):
    return isinstance(value, list) and len(value) == rows and all(isinstance(row, list) and len(row) == points and all(finite_array(item, 3) for item in row) for row in value)


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    diagnosis_ok = evidence_ok = report_ok = metric_ok = hidden_ok = False
    recomputed = {}; hidden_delta = None
    try:
        contract = load(root / "local_assets" / "roughness_consistency_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        manifest = load(root / "roughness_evidence_manifest.json")
        report = load(root / "roughness_results.json")
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, manifest, report)):
            raise ValueError("task_id_mismatch")
        evidence = manifest.get("cases")
        if not isinstance(evidence, dict) or set(evidence) != {FAULTY_CASE, REPAIRED_CASE}:
            raise ValueError("evidence_case_set")
        required = {
            FAULTY_CASE: {"roughness_inputs", "preflight_diagnosis"},
            REPAIRED_CASE: {"roughness_inputs", "probe_U", "probe_k", "probe_omega", "solver_log", "checkMesh_log", "field_U", "field_nut", "controlDict"},
        }
        paths = {}; used = set()
        for case_id, names in required.items():
            items = evidence[case_id]
            if not isinstance(items, dict) or set(items) != names:
                raise ValueError("evidence_item_set:" + case_id)
            paths[case_id] = {}
            for key in names:
                item = items[key]
                path = evidence_path(root, item.get("path") if isinstance(item, dict) else None)
                if path in used or item.get("sha256") != sha256(path):
                    raise ValueError("evidence_hash_or_duplicate:" + case_id + ":" + key)
                used.add(path); paths[case_id][key] = path

        faulty_inputs = load(paths[FAULTY_CASE]["roughness_inputs"])
        diagnosis = load(paths[FAULTY_CASE]["preflight_diagnosis"])
        faulty_values = [float(faulty_inputs[key]) for key in ("z0_inlet_m", "z0_wall_m", "z0_map_m")]
        faulty_residual = residual(faulty_values)
        diagnosis_ok = (
            diagnosis.get("case_id") == FAULTY_CASE and diagnosis.get("execution_role") == "provided_data_or_tool_execution"
            and diagnosis.get("solver_launch_authorized") is False and diagnosis.get("solver_run_count") == 0
            and diagnosis.get("all_preflight_gates_pass") is True and all(diagnosis.get("gates", {}).values())
            and abs(float(diagnosis.get("recomputed_residual", math.inf)) - faulty_residual) <= 1e-12
            and abs(faulty_residual - float(contract["roughness_definition"]["faulty_visible_residual"])) <= 1e-12
        )
        if not diagnosis_ok:
            errors.append("faulty_preflight_not_recomputed")

        repaired_inputs = load(paths[REPAIRED_CASE]["roughness_inputs"])
        repaired_values = [float(repaired_inputs[key]) for key in ("z0_inlet_m", "z0_wall_m", "z0_map_m")]
        repaired_residual = residual(repaired_values)
        points, weights, targets = target_profile(root / contract["profile_target_csv"])
        times_u, inlet_u = probe_data(paths[REPAIRED_CASE]["probe_U"], points, True)
        times_k, inlet_k = probe_data(paths[REPAIRED_CASE]["probe_k"], points, False)
        times_o, inlet_o = probe_data(paths[REPAIRED_CASE]["probe_omega"], points, False)
        if not (times_u == times_k == times_o):
            raise ValueError("probe_time_alignment")
        profile_metrics = {
            "inlet_U_rmse_m_s": weighted_rmse([item[0] for item in inlet_u[-1]], targets["U"], weights),
            "inlet_k_rmse_m2_s2": weighted_rmse(inlet_k[-1], targets["k"], weights),
            "inlet_omega_rmse_s_1": weighted_rmse(inlet_o[-1], targets["omega"], weights),
        }
        logs = log_gates(paths[REPAIRED_CASE]["solver_log"], paths[REPAIRED_CASE]["checkMesh_log"])
        field_u = paths[REPAIRED_CASE]["field_U"].read_text(encoding="utf-8", errors="replace")
        field_nut = paths[REPAIRED_CASE]["field_nut"].read_text(encoding="utf-8", errors="replace")
        repaired_gates = {
            "roughness_residual": repaired_residual <= 1e-12,
            "inlet_z0": "atmBoundaryLayerInletVelocity" in field_u and "z0 uniform 0.01" in field_u,
            "wall_z0": "atmNutkWallFunction" in field_nut and "z0 uniform 0.01" in field_nut,
            "U_rmse": profile_metrics["inlet_U_rmse_m_s"] <= 1e-6,
            "k_rmse": profile_metrics["inlet_k_rmse_m2_s2"] <= 1e-6,
            "omega_rmse": profile_metrics["inlet_omega_rmse_s_1"] <= 1e-5,
            "transverse_zero": max(abs(item[1]) + abs(item[2]) for item in inlet_u[-1]) <= 1e-10,
            "logs": all(logs["gates"].values()),
        }
        evidence_ok = all(repaired_gates.values())
        if not evidence_ok:
            errors.append("repaired_raw_case_gate_failed")
        primary = max(abs(float(diagnosis["recomputed_residual"]) - faulty_residual), repaired_residual)
        recomputed = {"faulty": {"residual": faulty_residual, "diagnosis": diagnosis},
                      "repaired": {"residual": repaired_residual, "iterations": times_u, "inlet_U_m_s": inlet_u,
                                   "inlet_k_m2_s2": inlet_k, "inlet_omega_s_1": inlet_o, "profile_metrics": profile_metrics,
                                   "gates": repaired_gates, **logs}, "primary_metric": primary}
        reported_faulty = report.get("faulty", {})
        reported_repaired = report.get("repaired", {})
        report_ok = (
            abs(float(reported_faulty.get("residual", math.inf)) - faulty_residual) <= 1e-12
            and reported_faulty.get("solver_run_count") == 0
            and abs(float(reported_repaired.get("residual", math.inf)) - repaired_residual) <= 1e-12
            and finite_array(reported_repaired.get("iterations"), 20)
            and finite_cube(reported_repaired.get("inlet_U_m_s"), 20, 52)
            and finite_matrix(reported_repaired.get("inlet_k_m2_s2"), 20, 52)
            and finite_matrix(reported_repaired.get("inlet_omega_s_1"), 20, 52)
            and reported_repaired.get("all_run_gates_pass") is True
            and max_scaled_delta(reported_repaired["iterations"], times_u, 400.0) <= 1e-12
            and max_scaled_delta(reported_repaired["inlet_U_m_s"], inlet_u, 1.2) <= 1e-10
            and max_scaled_delta(reported_repaired["inlet_k_m2_s2"], inlet_k, 0.03) <= 1e-10
            and max_scaled_delta(reported_repaired["inlet_omega_s_1"], inlet_o, 10.0) <= 1e-10
        )
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        reported_primary = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = isinstance(reported_primary, (int, float)) and not isinstance(reported_primary, bool) and math.isfinite(float(reported_primary)) and abs(float(reported_primary) - primary) <= 1e-12
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        reference = hidden.get("case_results", {})
        hidden_faulty = reference.get("faulty", {})
        hidden_repaired = reference.get("repaired", {})
        hidden_delta = max(
            abs(float(hidden_faulty.get("residual", math.inf)) - faulty_residual),
            max_scaled_delta(times_u, hidden_repaired["iterations"], 400.0),
            max_scaled_delta(inlet_u, hidden_repaired["inlet_U_m_s"], 1.2),
            max_scaled_delta(inlet_k, hidden_repaired["inlet_k_m2_s2"], 0.03),
            max_scaled_delta(inlet_o, hidden_repaired["inlet_omega_s_1"], 10.0),
        )
        hidden_ok = hidden_delta <= 1e-8
        if not hidden_ok:
            errors.append("hidden_numerical_fidelity")
    except Exception as exc:
        errors.append("roughness_reference_check:" + str(exc))

    components = {
        "faulty_preflight_diagnosis": 8.0 if diagnosis_ok and not base.get("errors") else 0.0,
        "repaired_raw_openfoam_evidence": 12.0 if evidence_ok and not base.get("errors") else 0.0,
        "reported_array_recomputation": 8.0 if report_ok and not base.get("errors") else 0.0,
        "primary_metric_recomputation": 4.0 if metric_ok and not base.get("errors") else 0.0,
        "hidden_numerical_fidelity": 8.0 if hidden_ok and not base.get("errors") else 0.0,
    }
    return {"task_id": TASK_ID, "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
            "score_cap": 100.0, "status": "openfoam_roughness_diagnosis_repair_reference_check",
            "errors": errors, "components": components,
            "diagnostics": {"recomputed": recomputed, "hidden_max_scaled_delta": hidden_delta}, "precheck": base,
            "claim_boundary": "Checks one deterministic z0 mismatch diagnosis and one repaired OpenFOAM v2412 numerical run; not experimental/PIV/DNS/wind-tunnel/field truth or release validation."}


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
