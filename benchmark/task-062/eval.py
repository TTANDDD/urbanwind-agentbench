#!/usr/bin/env python3
"""Task-062 raw inlet-profile-law numerical reference evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-062"
PRIMARY_ID = "profile_model_response_difference"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


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
              for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates_mismatch:" + path.name)
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [list(map(float, match)) for match in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        time_values = NUMBER.findall(line.split("(", 1)[0] if vector else line)
        if not time_values:
            continue
        time = float(time_values[0])
        if len(values) != len(expected_points):
            raise ValueError("probe_row_shape:" + path.name)
        flat = [item for vector_value in values for item in vector_value] if vector else values
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("probe_nonfinite:" + path.name)
        rows.append((time, values))
    expected_times = [float(value) for value in range(20, 401, 20)]
    if [row[0] for row in rows] != expected_times:
        raise ValueError("probe_time_contract:" + path.name)
    return [row[0] for row in rows], [row[1] for row in rows]


def weighted_rmse(values, targets, weights):
    return math.sqrt(sum(w * (a - b) ** 2 for a, b, w in zip(values, targets, weights)) / sum(weights))


def log_gates(solver_path: Path, mesh_path: Path):
    solver = solver_path.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_path.read_text(encoding="utf-8", errors="replace")
    iterations = [float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)", solver, re.M)]
    continuity = [float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)", solver)]
    residuals = {}
    for field in ("Ux", "Uy", "Uz", "k", "omega", "p"):
        values = [float(value) for value in re.findall(r"Solving for " + field + r", Initial residual = ([-+0-9.eE]+)", solver)]
        residuals[field] = max(values[-20:]) if len(values) >= 20 else None
    cells = re.findall(r"^\s*cells:\s+(\d+)\s*$", mesh, re.M)
    gates = {
        "mesh": "Mesh OK." in mesh and cells[-1:] == ["365488"],
        "iteration": len(iterations) == 400 and iterations[-1:] == [400.0],
        "model": "Selecting RAS turbulence model kOmegaSST" in solver,
        "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
        "residuals": all(value is not None and math.isfinite(value) and value <= 1e-4 for value in residuals.values()),
        "no_iteration_cap": "No Iterations 1000" not in solver,
    }
    return {
        "gates": gates,
        "max_abs_cumulative_continuity": max(map(abs, continuity)) if continuity else None,
        "last20_max_initial_residuals": residuals,
    }


def boundary_gates(field_u: Path, control: Path, variant_key: str):
    u = field_u.read_text(encoding="utf-8", errors="replace")
    c = control.read_text(encoding="utf-8", errors="replace")
    return {
        "variant_boundary": ("fixedProfile" in u and "profile csvFile" in u and "atmBoundaryLayerInletVelocity" not in u)
        if variant_key == "power" else ("atmBoundaryLayerInletVelocity" in u and "z0 uniform 0.01" in u and "fixedProfile" not in u),
        "control": bool(re.search(r"\bendTime\s+400\s*;", c)) and "responseProbes" in c and "writeInterval 20;" in c,
    }


def finite_array(value, length):
    return isinstance(value, list) and len(value) == length and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in value)


def finite_matrix(value, rows, columns):
    return isinstance(value, list) and len(value) == rows and all(finite_array(row, columns) for row in value)


def finite_cube(value, rows, points):
    return isinstance(value, list) and len(value) == rows and all(isinstance(row, list) and len(row) == points and all(finite_array(vector, 3) for vector in row) for row in value)


def flatten(value):
    if isinstance(value, list):
        return [item for child in value for item in flatten(child)]
    return [float(value)]


def nmae(left, right, scale):
    a, b = flatten(left), flatten(right)
    if len(a) != len(b) or not a:
        raise ValueError("nmae_shape")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a) / max(scale, 1e-30)


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    verified, recomputed, hidden_deltas = [], [], []
    report_ok = metric_ok = False
    try:
        contract = load(root / "local_assets" / "profile_law_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")), 128 * 1024 * 1024)
        evidence = load(root / "profile_law_evidence_manifest.json")
        reported = load(root / "profile_law_results.json", 128 * 1024 * 1024)
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, reported)):
            raise ValueError("task_id_mismatch")
        expected_cases = contract["cases"]
        ids = [item["case_id"] for item in expected_cases]
        evidence_cases = evidence.get("cases")
        reported_cases = reported.get("case_results")
        if not isinstance(evidence_cases, list) or [item.get("case_id") for item in evidence_cases] != ids:
            raise ValueError("evidence_case_ids")
        if not isinstance(reported_cases, list) or [item.get("case_id") for item in reported_cases] != ids:
            raise ValueError("reported_case_ids")
        target_rows = list(csv.DictReader((root / "local_assets" / "profile_law_targets.csv").open(encoding="utf-8", newline="")))
        inlet_points = [[-4.0, 0.0625, float(row["z_m"])] for row in target_rows]
        response_points = contract["response_points_m"]
        weights = [float(row["layer_thickness_m"]) for row in target_rows]
        hidden_by_id = {item["case_id"]: item for item in hidden["case_results"]}
        used = set()
        report_ok = True
        for expected, evidence_case, submitted in zip(expected_cases, evidence_cases, reported_cases):
            case_id, variant_key = expected["case_id"], expected["variant_key"]
            if evidence_case.get("variant_key") != variant_key or submitted.get("variant_key") != variant_key:
                raise ValueError("variant_mismatch:" + case_id)
            paths = {}
            for key in contract["required_raw_evidence_per_case"]:
                item = evidence_case.get(key)
                if not isinstance(item, dict):
                    raise ValueError("evidence_item:" + case_id + ":" + key)
                path = evidence_path(root, item.get("path"))
                if path in used or item.get("sha256") != sha256(path):
                    raise ValueError("evidence_hash_or_duplicate:" + case_id + ":" + key)
                used.add(path); paths[key] = path
            iterations, inlet_u = probe_data(paths["probe_U"], inlet_points, True)
            times_k, inlet_k = probe_data(paths["probe_k"], inlet_points, False)
            times_o, inlet_o = probe_data(paths["probe_omega"], inlet_points, False)
            response_times, response_u = probe_data(paths["response_U"], response_points, True)
            if not (iterations == times_k == times_o == response_times):
                raise ValueError("probe_time_alignment:" + case_id)
            target_key = "power_Ux_m_s" if variant_key == "power" else "log_Ux_m_s"
            profile_metrics = {
                "inlet_U_rmse_m_s": weighted_rmse([value[0] for value in inlet_u[-1]], [float(row[target_key]) for row in target_rows], weights),
                "inlet_k_rmse_m2_s2": weighted_rmse(inlet_k[-1], [float(row["frozen_k_m2_s2"]) for row in target_rows], weights),
                "inlet_omega_rmse_s_1": weighted_rmse(inlet_o[-1], [float(row["frozen_omega_s_1"]) for row in target_rows], weights),
            }
            log = log_gates(paths["solver_log"], paths["checkMesh_log"])
            boundary = boundary_gates(paths["field_U"], paths["controlDict"], variant_key)
            raw_gates = {
                **log["gates"], **boundary,
                "U_profile": profile_metrics["inlet_U_rmse_m_s"] <= 1e-6,
                "k_profile": profile_metrics["inlet_k_rmse_m2_s2"] <= 1e-6,
                "omega_profile": profile_metrics["inlet_omega_rmse_s_1"] <= 1e-5,
                "response_range": all(-3.0 <= value <= 3.0 for row in response_u for vector in row for value in vector),
            }
            if not all(raw_gates.values()):
                raise ValueError("raw_case_gate_failed:" + case_id)
            raw = {
                "case_id": case_id, "variant": expected["variant"], "variant_key": variant_key,
                "iterations": iterations, "inlet_U_m_s": inlet_u, "inlet_k_m2_s2": inlet_k,
                "inlet_omega_s_1": inlet_o, "response_U_m_s": response_u,
                "profile_metrics": profile_metrics,
                "max_abs_cumulative_continuity": log["max_abs_cumulative_continuity"],
                "last20_max_initial_residuals": log["last20_max_initial_residuals"],
                "all_run_gates_pass": True,
            }
            recomputed.append(raw); verified.append(case_id)
            if not (finite_array(submitted.get("iterations"), 20)
                    and finite_cube(submitted.get("inlet_U_m_s"), 20, 52)
                    and finite_matrix(submitted.get("inlet_k_m2_s2"), 20, 52)
                    and finite_matrix(submitted.get("inlet_omega_s_1"), 20, 52)
                    and finite_cube(submitted.get("response_U_m_s"), 20, 48)
                    and submitted.get("all_run_gates_pass") is True):
                report_ok = False
            else:
                if max(
                    nmae(submitted["iterations"], iterations, 400.0),
                    nmae(submitted["inlet_U_m_s"], inlet_u, 1.2),
                    nmae(submitted["inlet_k_m2_s2"], inlet_k, 0.03),
                    nmae(submitted["inlet_omega_s_1"], inlet_o, 10.0),
                    nmae(submitted["response_U_m_s"], response_u, 1.2),
                ) > 1e-8:
                    report_ok = False
            reference = hidden_by_id[case_id]
            hidden_deltas.append(max(
                nmae(iterations, reference["iterations"], 400.0),
                nmae(inlet_u, reference["inlet_U_m_s"], 1.2),
                nmae(inlet_k, reference["inlet_k_m2_s2"], 0.03),
                nmae(inlet_o, reference["inlet_omega_s_1"], 10.0),
                nmae(response_u, reference["response_U_m_s"], 1.2),
            ))
        by_variant = {item["variant_key"]: item for item in recomputed}
        indices = [by_variant["power"]["iterations"].index(float(value)) for value in contract["response_iterations"]]
        probe_groups = list(csv.DictReader((root / "local_assets" / "profile_response_probes.csv").open(encoding="utf-8", newline="")))
        deltas = {"pedestrian_layer": [], "roof_layer": []}
        for index in indices:
            for probe_index, point in enumerate(probe_groups):
                deltas[point["group"]].append(abs(by_variant["power"]["response_U_m_s"][index][probe_index][0] - by_variant["log"]["response_U_m_s"][index][probe_index][0]))
        means = {key: sum(values) / len(values) for key, values in deltas.items()}
        primary = 0.5 * (means["pedestrian_layer"] + means["roof_layer"]) / 1.2
        reported_primary = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = isinstance(reported_primary, (int, float)) and not isinstance(reported_primary, bool) and math.isfinite(float(reported_primary)) and abs(float(reported_primary) - primary) <= 1e-10
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
    except Exception as exc:
        errors.append("profile_law_reference_check:" + str(exc))
    coverage = len(verified) / 2
    components = {"raw_profile_law_evidence": 0.0, "reported_array_recomputation": 0.0, "primary_metric_recomputation": 0.0, "hidden_numerical_fidelity": 0.0}
    if not base.get("errors"):
        components["raw_profile_law_evidence"] = 12.0 * coverage
        if report_ok: components["reported_array_recomputation"] = 8.0
        if metric_ok: components["primary_metric_recomputation"] = 5.0
        if hidden_deltas: components["hidden_numerical_fidelity"] = 15.0 * max(0.0, 1.0 - sum(hidden_deltas) / len(hidden_deltas) / 0.05)
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_profile_law_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"verified_case_ids": verified, "case_coverage": coverage, "recomputed_cases": recomputed, "hidden_case_deltas": hidden_deltas},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM v2412 power/log inlet profile evidence and hidden numerical probe series; not experimental/PIV/DNS/wind-tunnel/field truth or release validation.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
