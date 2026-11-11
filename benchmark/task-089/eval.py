#!/usr/bin/env python3
"""Task-089 coordinate-joined synthetic-reference and numerical-array evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-089"
PRIMARY_ID = "local_reference_comparison_nmae"
U_SCALE = 1.2
TKE_SCALE = 1.44


def load(path: Path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def finite_array(value, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        and math.isfinite(float(item)) for item in value
    )


def coordinate(point):
    if not isinstance(point, list) or len(point) != 3 or not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value)) for value in point
    ):
        raise ValueError("invalid_probe_coordinate")
    return tuple(float(value) for value in point)


def finite_gate_diagnostics(value) -> bool:
    limits = {"half_window_velocity_drift": 0.05, "half_window_tke_drift": 0.02,
              "max_courant": 1.0, "max_abs_continuity": 0.01}
    return isinstance(value, dict) and all(
        isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool)
        and math.isfinite(float(value[key])) and abs(float(value[key])) <= limit + 1e-12
        for key, limit in limits.items()
    )


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    recomputed = {}
    fidelity = []
    schema_ok = False
    metric_ok = False
    try:
        hidden_path = Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json"))
        hidden = load(hidden_path)
        result = load(root / "simulation_metrics.json")
        if hidden.get("task_id") != TASK_ID or result.get("task_id") != TASK_ID:
            raise ValueError("simulation_task_id_mismatch")
        expected = hidden.get("case_results")
        submitted = result.get("case_results")
        if not isinstance(expected, list) or not isinstance(submitted, list) or len(expected) != 1 \
                or len(submitted) != 1 or submitted[0].get("case_id") != expected[0].get("case_id"):
            raise ValueError("case_ids_mismatch")
        reference_case, case = expected[0], submitted[0]
        if case.get("variant") != reference_case["variant"] or case.get("window_s") != [300.0, 420.0]:
            raise ValueError("variant_or_window_mismatch")
        samples = case.get("sample_count")
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 600:
            raise ValueError("insufficient_samples")
        if case.get("all_run_gates_pass") is not True or not finite_gate_diagnostics(case.get("gate_diagnostics")):
            raise ValueError("run_gates_not_passed")
        points = case.get("probe_points_m")
        if not isinstance(points, list) or not 59 <= len(points) <= 512:
            raise ValueError("probe_point_count_invalid")
        positions = [coordinate(point) for point in points]
        if len(positions) != len(set(positions)):
            raise ValueError("duplicate_probe_coordinates")
        if not finite_array(case.get("mean_Ux_m_s"), len(points)) \
                or not finite_array(case.get("resolved_tke_m2_s2"), len(points)):
            raise ValueError("invalid_case_arrays")
        local_ref = root / "local_assets" / "reference_observations.csv"
        if not local_ref.is_file() or hashlib.sha256(local_ref.read_bytes()).hexdigest() != hidden["local_reference_sha256"]:
            raise ValueError("local_reference_file_changed")
        with local_ref.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        reference = {tuple(float(row[key]) for key in ("x_m", "y_m", "z_m")): row for row in rows}
        required = {coordinate(point) for point in hidden["required_fluid_probe_points_m"]}
        if len(rows) != 64 or len(reference) != 64 or len(required) != 59 \
                or not required.issubset(reference):
            raise ValueError("reference_coordinate_contract_invalid")
        submitted_by_coordinate = {point: index for index, point in enumerate(positions)}
        if not required.issubset(submitted_by_coordinate):
            raise ValueError("required_fluid_probe_points_missing")
        csv_path = root / "comparison_line_p_U.csv"
        if not csv_path.is_file() or csv_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("missing_or_oversized_sampling_csv")
        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            expected_header = ["x_m", "y_m", "z_m", "Ux_m_s", "Uy_m_s", "Uz_m_s", "p_m2_s2"]
            if reader.fieldnames != expected_header:
                raise ValueError("sampling_csv_header_mismatch")
            csv_rows = list(reader)
        if len(csv_rows) != 59:
            raise ValueError("sampling_csv_row_count_mismatch")
        csv_by_coordinate = {}
        for row in csv_rows:
            point = tuple(float(row[key]) for key in ("x_m", "y_m", "z_m"))
            fields = [float(row[key]) for key in expected_header[3:]]
            if point in csv_by_coordinate or not all(math.isfinite(value) for value in point + tuple(fields)):
                raise ValueError("sampling_csv_coordinate_or_field_invalid")
            csv_by_coordinate[point] = fields
        if set(csv_by_coordinate) != required:
            raise ValueError("sampling_csv_required_coordinates_mismatch")
        if any(abs(csv_by_coordinate[point][0]
                   - float(case["mean_Ux_m_s"][submitted_by_coordinate[point]])) > 1e-4
               for point in required):
            raise ValueError("sampling_csv_Ux_not_consistent")
        schema_ok = True
        metric = sum(abs(float(case["mean_Ux_m_s"][submitted_by_coordinate[point]])
                         - float(reference[point]["u_m_s"])) for point in required) / len(required) / U_SCALE
        recomputed[PRIMARY_ID] = metric
        reported = load(root / "metrics.json").get("values", {}).get(PRIMARY_ID)
        if not isinstance(reported, (int, float)) or isinstance(reported, bool) or not math.isfinite(float(reported)) \
                or abs(float(reported) - metric) > max(1e-8, abs(metric) * 1e-5):
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        else:
            metric_ok = True
        hidden_positions = [coordinate(point) for point in reference_case["probe_points_m"]]
        hidden_by_coordinate = {point: index for index, point in enumerate(hidden_positions)}
        if not required.issubset(hidden_by_coordinate):
            raise ValueError("hidden_fluid_probe_points_missing")
        ux_delta = sum(abs(float(case["mean_Ux_m_s"][submitted_by_coordinate[point]])
                           - float(reference_case["mean_Ux_m_s"][hidden_by_coordinate[point]]))
                       for point in required) / len(required) / U_SCALE
        k_delta = sum(abs(float(case["resolved_tke_m2_s2"][submitted_by_coordinate[point]])
                          - float(reference_case["resolved_tke_m2_s2"][hidden_by_coordinate[point]]))
                      for point in required) / len(required) / TKE_SCALE
        for field_key in ("mean_Uy_m_s", "mean_Uz_m_s", "mean_p_m2_s2"):
            if not finite_array(reference_case.get(field_key), len(hidden_positions)):
                raise ValueError("hidden_sampling_field_invalid:" + field_key)
        uy_delta = sum(abs(csv_by_coordinate[point][1]
                           - float(reference_case["mean_Uy_m_s"][hidden_by_coordinate[point]]))
                       for point in required) / len(required) / U_SCALE
        uz_delta = sum(abs(csv_by_coordinate[point][2]
                           - float(reference_case["mean_Uz_m_s"][hidden_by_coordinate[point]]))
                       for point in required) / len(required) / U_SCALE
        p_delta = sum(abs(csv_by_coordinate[point][3]
                          - float(reference_case["mean_p_m2_s2"][hidden_by_coordinate[point]]))
                      for point in required) / len(required) / TKE_SCALE
        fidelity = [ux_delta, uy_delta, uz_delta, p_delta, k_delta]
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    components = {"result_schema_and_gates": 0.0, "metric_recomputation": 0.0,
                  "hidden_array_fidelity": 0.0}
    if not base.get("errors"):
        if schema_ok:
            components["result_schema_and_gates"] = 8.0
        if metric_ok:
            components["metric_recomputation"] = 12.0
        if fidelity:
            average = sum(fidelity) / len(fidelity)
            components["hidden_array_fidelity"] = 20.0 * max(0.0, 1.0 - average / 0.15)
    return {
        "task_id": TASK_ID, "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"recomputed_metrics": recomputed, "matched_fluid_points": 59 if schema_ok else 0,
                        "hidden_fluid_velocity_tke_nmae": fidelity},
        "precheck": base,
        "claim_boundary": "Coordinate-joined synthetic local reference and hidden OpenFOAM numerical arrays; not PIV/DNS, experimental truth or release-runtime reproducibility.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
