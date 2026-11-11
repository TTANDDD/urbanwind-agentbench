#!/usr/bin/env python3
"""Task-079 raw-phi inlet/outlet mass-closure evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-079"
CASE_ID = "task-079-case-001"
PRIMARY_ID = "mass_flux_mismatch_percent"
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


def profile_targets(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 52:
        raise ValueError("profile_target_count")
    points = [[float(row[key]) for key in ("x_m", "y_m", "z_m")] for row in rows]
    weights = [float(row["layer_thickness_m"]) for row in rows]
    targets = {
        "U": [float(row["target_Ux_m_s"]) for row in rows],
        "k": [float(row["target_k_m2_s2"]) for row in rows],
        "omega": [float(row["target_omega_s_1"]) for row in rows],
    }
    return points, weights, targets


def probe(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [
        [float(value) for value in re.findall(NUMBER, match.group(1))]
        for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)
    ]
    if points != expected_points:
        raise ValueError("probe_coordinates:" + path.name)
    times, rows = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [list(map(float, item)) for item in VECTOR.findall(line)] if vector else [float(value) for value in re.findall(NUMBER, line)][1:]
        if len(values) != 52:
            raise ValueError("probe_shape:" + path.name)
        flat = [item for child in values for item in child] if vector else values
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_nonfinite:" + path.name)
        times.append(float(line.split()[0])); rows.append(values)
    if times != [float(value) for value in range(20, 401, 20)]:
        raise ValueError("probe_times:" + path.name)
    return times, rows


def weighted_rmse(values, targets, weights) -> float:
    return math.sqrt(sum(weight * (value - target) ** 2 for value, target, weight in zip(values, targets, weights)) / sum(weights))


def phi_values(path: Path, patch_name: str) -> list[float]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "dimensions      [0 3 -1 0 0 0 0];" not in text or "oriented        oriented;" not in text:
        raise ValueError("phi_dimensions_or_orientation")
    boundary = text.split("boundaryField", 1)[1]
    patch = re.search(r"\b" + re.escape(patch_name) + r"\s*\{(.*?)\n\s*\}", boundary, re.S)
    if not patch:
        raise ValueError("phi_patch:" + patch_name)
    values = re.search(r"value\s+nonuniform\s+List<scalar>\s*(\d+)\s*\((.*?)\)\s*;", patch.group(1), re.S)
    if not values:
        raise ValueError("phi_values:" + patch_name)
    parsed = [float(value) for value in re.findall(NUMBER, values.group(2))]
    if len(parsed) != int(values.group(1)) or not all(math.isfinite(value) for value in parsed):
        raise ValueError("phi_count_or_finite:" + patch_name)
    return parsed


def log_metrics(solver_path: Path, mesh_path: Path):
    solver = solver_path.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_path.read_text(encoding="utf-8", errors="replace")
    iterations = [float(value) for value in re.findall(r"^Time = (%s)" % NUMBER, solver, re.M)]
    continuity = [float(value) for value in re.findall(r"cumulative = (%s)" % NUMBER, solver)]
    cells = [int(value) for value in re.findall(r"cells:\s+(\d+)", mesh)]
    residuals = {}
    for field in ("Ux", "Uy", "Uz", "k", "omega", "p"):
        values = [float(value) for value in re.findall(r"Solving for " + field + r", Initial residual = (%s)" % NUMBER, solver)]
        residuals[field] = max(values[-20:]) if len(values) >= 20 else None
    gates = {
        "mesh": "Mesh OK." in mesh and cells[-1:] == [365488],
        "iteration": len(iterations) == 400 and iterations[-1:] == [400.0],
        "model": "Selecting RAS turbulence model kOmegaSST" in solver,
        "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
        "residuals": all(value is not None and math.isfinite(value) and value <= 1e-4 for value in residuals.values()),
        "no_iteration_cap": "No Iterations 1000" not in solver,
    }
    return {
        "iterations": iterations,
        "max_abs_cumulative_continuity": max(map(abs, continuity)) if continuity else None,
        "last20_max_initial_residuals": residuals,
        "gates": gates,
    }


def nmae(left, right, scale: float) -> float:
    if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right) or not left:
        raise ValueError("nmae_shape")
    return sum(abs(float(a) - float(b)) for a, b in zip(left, right)) / len(left) / max(scale, 1e-30)


def close(left, right, tolerance=1e-12) -> bool:
    return isinstance(left, (int, float)) and not isinstance(left, bool) and abs(float(left) - float(right)) <= max(tolerance, abs(float(right)) * 1e-8)


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    raw_ok = report_ok = metric_ok = hidden_ok = False
    recomputed = {}
    hidden_delta = math.inf
    try:
        contract = load(root / "local_assets/mass_flux_closure_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "mass_flux_evidence_manifest.json")
        reported = load(root / "mass_flux_results.json")
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, reported, metrics)):
            raise ValueError("task_id_mismatch")
        case = evidence.get("case")
        submitted = reported.get("case_result")
        if not isinstance(case, dict) or case.get("case_id") != CASE_ID or not isinstance(submitted, dict) or submitted.get("case_id") != CASE_ID:
            raise ValueError("case_id_mismatch")
        paths, used = {}, set()
        for key in contract["required_raw_evidence_per_case"]:
            item = case.get(key)
            if not isinstance(item, dict):
                raise ValueError("evidence_item:" + key)
            path = evidence_path(root, item.get("path"))
            if path in used or item.get("sha256") != sha256(path):
                raise ValueError("evidence_hash_or_duplicate:" + key)
            used.add(path); paths[key] = path
        inlet = phi_values(paths["field_phi400"], "inlet")
        outlet = phi_values(paths["field_phi400"], "outlet")
        inlet_sum = sum(inlet); outlet_sum = sum(outlet)
        mismatch = 100.0 * abs(inlet_sum + outlet_sum) / max(abs(inlet_sum), 1e-12)
        points, weights, targets = profile_targets(paths["profile_csv"])
        times_u, values_u = probe(paths["probe_U"], points, True)
        times_k, values_k = probe(paths["probe_k"], points, False)
        times_o, values_o = probe(paths["probe_omega"], points, False)
        profiles = {
            "inlet_U_rmse_m_s": weighted_rmse([row[0] for row in values_u[-1]], targets["U"], weights),
            "inlet_k_rmse_m2_s2": weighted_rmse(values_k[-1], targets["k"], weights),
            "inlet_omega_rmse_s_1": weighted_rmse(values_o[-1], targets["omega"], weights),
        }
        logs = log_metrics(paths["solver_log"], paths["checkMesh_log"])
        config = load(paths["resolved_config"])
        analysis = load(paths["run_analysis"])
        field_u = paths["field_U0"].read_text(encoding="utf-8", errors="replace")
        field_p = paths["field_p0"].read_text(encoding="utf-8", errors="replace")
        control = paths["controlDict"].read_text(encoding="utf-8", errors="replace")
        schemes = paths["fvSchemes"].read_text(encoding="utf-8", errors="replace")
        turbulence = paths["turbulenceProperties"].read_text(encoding="utf-8", errors="replace")
        raw_gates = {
            **logs["gates"],
            "phi_face_counts": len(inlet) == len(outlet) == 3224,
            "phi_signs": inlet_sum < 0.0 and outlet_sum > 0.0,
            "mass_flux_closure": mismatch <= 0.001,
            "profile_times": times_u == times_k == times_o,
            "profile_U": profiles["inlet_U_rmse_m_s"] <= 1e-6,
            "profile_k": profiles["inlet_k_rmse_m2_s2"] <= 1e-6,
            "profile_omega": profiles["inlet_omega_rmse_s_1"] <= 1e-5,
            "config": config.get("case_id") == CASE_ID and config.get("cell_count") == 365488 and config.get("end_time") == 400,
            "analysis": analysis.get("case_id") == CASE_ID and analysis.get("all_run_gates_pass") is True,
            "boundary_U": bool(re.search(r"inlet\s*\{\s*type\s+atmBoundaryLayerInletVelocity;", field_u, re.S)) and bool(re.search(r"outlet\s*\{\s*type\s+inletOutlet;", field_u, re.S)),
            "boundary_p": bool(re.search(r"outlet\s*\{\s*type\s+fixedValue;", field_p, re.S)),
            "control": bool(re.search(r"\bendTime\s+400(?:\.0+)?\s*;", control)),
            "scheme": "Gauss upwind" in schemes,
            "turbulence": "RASModel kOmegaSST;" in turbulence,
        }
        raw_ok = all(raw_gates.values())
        if not raw_ok:
            errors.append("raw_case_gate_failed")
        recomputed = {
            "case_id": CASE_ID,
            "raw_gates": raw_gates,
            "inlet_phi_m3_s": inlet,
            "outlet_phi_m3_s": outlet,
            "inlet_signed_flux_m3_s": inlet_sum,
            "outlet_signed_flux_m3_s": outlet_sum,
            "inlet_face_count": len(inlet),
            "outlet_face_count": len(outlet),
            PRIMARY_ID: mismatch,
            "profile_metrics": profiles,
            "max_abs_cumulative_continuity": logs["max_abs_cumulative_continuity"],
            "last20_max_initial_residuals": logs["last20_max_initial_residuals"],
            "all_run_gates_pass": raw_ok,
        }
        required_scalars = ("inlet_signed_flux_m3_s", "outlet_signed_flux_m3_s", PRIMARY_ID, "max_abs_cumulative_continuity")
        report_ok = submitted.get("all_run_gates_pass") is True and submitted.get("inlet_face_count") == 3224 and submitted.get("outlet_face_count") == 3224
        report_ok = report_ok and all(close(submitted.get(key), recomputed[key]) for key in required_scalars)
        report_ok = report_ok and all(close(submitted.get("profile_metrics", {}).get(key), value) for key, value in profiles.items())
        if not report_ok:
            errors.append("reported_result_not_recomputed")
        value = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = close(value, mismatch, 1e-15)
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        reference = hidden["case_results"][0]
        scale = max(sum(abs(value) for value in inlet) / len(inlet), 1e-12)
        hidden_delta = max(
            nmae(inlet, reference["inlet_phi_m3_s"], scale),
            nmae(outlet, reference["outlet_phi_m3_s"], scale),
            abs(mismatch - float(reference[PRIMARY_ID])),
        )
        hidden_ok = hidden_delta <= 1e-8
        if not hidden_ok:
            errors.append("hidden_numerical_fidelity")
    except Exception as exc:
        errors.append("mass_flux_reference_check:" + str(exc))
    components = {
        "raw_openfoam_evidence": 15.0 if raw_ok and not base.get("errors") else 0.0,
        "reported_result_recomputation": 10.0 if report_ok and not base.get("errors") else 0.0,
        "primary_metric_recomputation": 5.0 if metric_ok and not base.get("errors") else 0.0,
        "hidden_numerical_fidelity": 10.0 if hidden_ok and not base.get("errors") else 0.0,
    }
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0,
        "status": "openfoam_mass_flux_raw_phi_reference_check",
        "errors": errors,
        "components": components,
        "diagnostics": {"recomputed_case": recomputed, "hidden_max_scaled_delta": hidden_delta},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM v2412 phi evidence for a local incompressible numerical closure task; not experimental/PIV/DNS/wind-tunnel/field or compressible mass-flow truth.",
    }


def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
