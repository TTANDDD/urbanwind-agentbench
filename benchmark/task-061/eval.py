#!/usr/bin/env python3
"""Task-061 raw neutral-ABL inlet OpenFOAM numerical-reference evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-061"
PRIMARY_ID = "inlet_profile_rmse_ms"
CASE_ID = "task-061-case-001"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


def load(path: Path, limit: int = 64 * 1024 * 1024):
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


def target_profile(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 52:
        raise ValueError("target_profile_count")
    points = [[float(row["x_m"]), float(row["y_m"]), float(row["z_m"])] for row in rows]
    weights = [float(row["layer_thickness_m"]) for row in rows]
    targets = {
        "U": [float(row["target_Ux_m_s"]) for row in rows],
        "k": [float(row["target_k_m2_s2"]) for row in rows],
        "omega": [float(row["target_omega_s_1"]) for row in rows],
    }
    flat = [value for point in points for value in point] + weights + [value for values in targets.values() for value in values]
    if not all(math.isfinite(value) for value in flat) or abs(sum(weights) - 9.0) > 1e-12:
        raise ValueError("invalid_target_profile")
    return points, weights, targets


def probe_data(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))]
              for match in re.finditer(r"^# Probe \d+ \(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates_mismatch:" + path.name)
    iterations, values = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            iteration = float(line.split()[0])
        except (ValueError, IndexError):
            continue
        row = [list(map(float, item)) for item in VECTOR.findall(line)] if vector \
            else [float(value) for value in NUMBER.findall(line)][1:]
        if len(row) != len(expected_points):
            raise ValueError("probe_row_shape:" + path.name)
        flat = [item for triplet in row for item in triplet] if vector else row
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_nonfinite:" + path.name)
        iterations.append(iteration)
        values.append(row)
    expected_iterations = [float(value) for value in range(20, 401, 20)]
    if iterations != expected_iterations:
        raise ValueError("probe_iteration_contract:" + path.name)
    return iterations, values


def weighted_rmse(values, targets, weights) -> float:
    return math.sqrt(sum(weight * (value - target) ** 2 for value, target, weight in zip(values, targets, weights)) / sum(weights))


def log_and_input_gates(paths: dict[str, Path], contract: dict) -> dict:
    solver = paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    mesh = paths["checkMesh_log"].read_text(encoding="utf-8", errors="replace")
    control = paths["controlDict"].read_text(encoding="utf-8", errors="replace")
    fields = {key: paths[key].read_text(encoding="utf-8", errors="replace") for key in ("field_U", "field_k", "field_omega", "field_nut", "field_p")}
    iterations = [float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)", solver, re.M)]
    continuity = [float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)", solver)]
    cells = [int(value) for value in re.findall(r"cells:\s+(\d+)", mesh)]
    initial_residuals = [float(value) for value in re.findall(r"Initial residual = ([-+0-9.eE]+)", solver)]
    bc_checks = {
        "U_inlet": "type atmBoundaryLayerInletVelocity;" in fields["field_U"],
        "U_top": "type fixedShearStress;" in fields["field_U"],
        "k_inlet": "type atmBoundaryLayerInletK;" in fields["field_k"],
        "omega_inlet": "type atmBoundaryLayerInletOmega;" in fields["field_omega"],
        "nut_ground": "type atmNutkWallFunction;" in fields["field_nut"],
        "p_top": "top{type zeroGradient;}" in re.sub(r"\s+", " ", fields["field_p"]),
    }
    gates = {
        "mesh_ok": "Mesh OK." in mesh and cells[-1:] == [int(contract["frozen_gates"]["required_cell_count"])],
        "final_iteration": bool(iterations) and iterations[-1] == float(contract["full_iterations"]),
        "finite_solver_log": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "continuity": bool(continuity) and max(abs(value) for value in continuity) <= float(contract["frozen_gates"]["max_abs_cumulative_continuity"]),
        "residuals_finite_bounded": bool(initial_residuals) and all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in initial_residuals),
        "control_contract": bool(re.search(r"\bapplication\s+simpleFoam\s*;", control)) and bool(re.search(r"\bendTime\s+400\s*;", control)),
        "boundary_conditions": all(bc_checks.values()),
    }
    return {
        "gates": gates,
        "boundary_condition_checks": bc_checks,
        "final_iteration": iterations[-1] if iterations else None,
        "max_abs_cumulative_continuity": max((abs(value) for value in continuity), default=None),
    }


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


def max_scaled_delta(left, right, scale):
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return max((max_scaled_delta(a, b, scale) for a, b in zip(left, right)), default=0.0)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) / max(float(scale), 1e-30)
    raise ValueError("hidden_shape_mismatch")


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    evidence_ok = report_ok = metric_ok = hidden_ok = False
    recomputed = {}
    hidden_delta = None
    try:
        contract = load(root / "local_assets" / "neutral_abl_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")), 128 * 1024 * 1024)
        manifest = load(root / "neutral_abl_evidence_manifest.json")
        report = load(root / "inlet_profile_results.json", 128 * 1024 * 1024)
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, manifest, report)):
            raise ValueError("task_id_mismatch")
        if manifest.get("case_id") != CASE_ID or report.get("case_id") != CASE_ID:
            raise ValueError("case_id_mismatch")
        evidence = manifest.get("evidence")
        required = (
            "probe_U", "probe_k", "probe_omega", "solver_log", "checkMesh_log",
            "field_U", "field_k", "field_omega", "field_nut", "field_p",
            "blockMeshDict", "controlDict",
        )
        if not isinstance(evidence, dict) or set(evidence) != set(required):
            raise ValueError("evidence_item_set")
        paths, used = {}, set()
        for key in required:
            item = evidence[key]
            if not isinstance(item, dict):
                raise ValueError("evidence_item:" + key)
            path = evidence_path(root, item.get("path"))
            if path in used or item.get("sha256") != sha256(path):
                raise ValueError("evidence_hash_or_duplicate:" + key)
            used.add(path)
            paths[key] = path

        expected_points, weights, targets = target_profile(root / contract["profile_source"])
        iterations_u, probe_u = probe_data(paths["probe_U"], expected_points, True)
        iterations_k, probe_k = probe_data(paths["probe_k"], expected_points, False)
        iterations_o, probe_o = probe_data(paths["probe_omega"], expected_points, False)
        if not (iterations_u == iterations_k == iterations_o):
            raise ValueError("probe_iteration_alignment")
        final_u = probe_u[-1]
        final_k = probe_k[-1]
        final_o = probe_o[-1]
        metrics_raw = {
            PRIMARY_ID: weighted_rmse([vector[0] for vector in final_u], targets["U"], weights),
            "inlet_k_rmse_m2_s2": weighted_rmse(final_k, targets["k"], weights),
            "inlet_omega_rmse_s_1": weighted_rmse(final_o, targets["omega"], weights),
        }
        profile_gates = {
            "U_rmse": metrics_raw[PRIMARY_ID] <= float(contract["frozen_gates"]["max_inlet_U_rmse_m_s"]),
            "k_rmse": metrics_raw["inlet_k_rmse_m2_s2"] <= float(contract["frozen_gates"]["max_inlet_k_rmse_m2_s2"]),
            "omega_rmse": metrics_raw["inlet_omega_rmse_s_1"] <= float(contract["frozen_gates"]["max_inlet_omega_rmse_s_1"]),
            "transverse_velocity_zero": max(abs(vector[1]) + abs(vector[2]) for vector in final_u) <= 1e-12,
        }
        log_gates = log_and_input_gates(paths, contract)
        if not all(profile_gates.values()) or not all(log_gates["gates"].values()):
            raise ValueError("raw_case_gate_failed")
        evidence_ok = True
        recomputed = {
            "case_id": CASE_ID,
            "iterations": iterations_u,
            "inlet_U_m_s": probe_u,
            "inlet_k_m2_s2": probe_k,
            "inlet_omega_s_1": probe_o,
            "metrics": metrics_raw,
            "profile_gates": profile_gates,
            **log_gates,
            "all_run_gates_pass": True,
        }

        report_ok = (
            finite_array(report.get("iterations"), 20)
            and finite_vector_cube(report.get("inlet_U_m_s"), 20, 52)
            and finite_matrix(report.get("inlet_k_m2_s2"), 20, 52)
            and finite_matrix(report.get("inlet_omega_s_1"), 20, 52)
            and report.get("all_run_gates_pass") is True
            and max_scaled_delta(report["iterations"], iterations_u, 400.0) <= 1e-12
            and max_scaled_delta(report["inlet_U_m_s"], probe_u, 1.2) <= 1e-10
            and max_scaled_delta(report["inlet_k_m2_s2"], probe_k, 0.03) <= 1e-10
            and max_scaled_delta(report["inlet_omega_s_1"], probe_o, 10.0) <= 1e-10
            and all(
                isinstance(report.get("metrics", {}).get(key), (int, float))
                and not isinstance(report["metrics"][key], bool)
                and abs(float(report["metrics"][key]) - value) <= max(1e-12, abs(value) * 1e-7)
                for key, value in metrics_raw.items()
            )
        )
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        reported_primary = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = (
            isinstance(reported_primary, (int, float)) and not isinstance(reported_primary, bool)
            and math.isfinite(float(reported_primary))
            and abs(float(reported_primary) - metrics_raw[PRIMARY_ID]) <= max(1e-12, abs(metrics_raw[PRIMARY_ID]) * 1e-7)
        )
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)

        reference = hidden["case_results"][0]
        if reference.get("case_id") != CASE_ID:
            raise ValueError("hidden_case_id")
        hidden_delta = max(
            max_scaled_delta(iterations_u, reference["iterations"], 400.0),
            max_scaled_delta(probe_u, reference["inlet_U_m_s"], 1.2),
            max_scaled_delta(probe_k, reference["inlet_k_m2_s2"], 0.03),
            max_scaled_delta(probe_o, reference["inlet_omega_s_1"], 10.0),
        )
        hidden_ok = hidden_delta <= 1e-8
        if not hidden_ok:
            errors.append("hidden_numerical_fidelity")
    except Exception as exc:
        errors.append("neutral_abl_reference_check:" + str(exc))

    components = {
        "raw_neutral_abl_evidence": 15.0 if evidence_ok and not base.get("errors") else 0.0,
        "reported_array_recomputation": 10.0 if report_ok and not base.get("errors") else 0.0,
        "primary_metric_recomputation": 5.0 if metric_ok and not base.get("errors") else 0.0,
        "hidden_numerical_fidelity": 10.0 if hidden_ok and not base.get("errors") else 0.0,
    }
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0,
        "status": "openfoam_neutral_abl_numerical_reference_check",
        "errors": errors,
        "components": components,
        "diagnostics": {"recomputed": recomputed, "hidden_max_scaled_delta": hidden_delta},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM v2412 neutral-ABL inlet evidence against a hidden numerical reference; not experimental/PIV/DNS/wind-tunnel/field truth or release validation.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
