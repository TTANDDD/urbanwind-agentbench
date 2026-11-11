#!/usr/bin/env python3
import csv
import json
import math
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck

TASK_NUMBER = 93
TASK_ID = "urbanwind-task-093"
U_SCALE = 1.2
TKE_SCALE = 1.44


def load(path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def finite_array(value, length):
    return (
        isinstance(value, list)
        and len(value) == length
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in value
        )
    )


def mean(values):
    return sum(values) / len(values)


def nmae(values, reference, scale):
    return mean([abs(float(left) - float(right)) for left, right in zip(values, reference)]) / scale


def metric_task091(root, ordered):
    with (root / "local_assets" / "array_reference_observations.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 96 or [int(row["point_id"]) for row in rows] != list(range(96)):
        raise ValueError("task091_local_reference_schema")
    case = ordered[0]
    u_reference = [float(row["u_ref_m_s"]) for row in rows]
    tke_reference = [float(row["resolved_tke_ref_m2_s2"]) for row in rows]
    return 0.5 * (
        nmae(case["mean_Ux_m_s"], u_reference, U_SCALE)
        + nmae(case["resolved_tke_m2_s2"], tke_reference, TKE_SCALE)
    ), {"local_reference_point_count": len(rows)}


def metric_task093(ordered):
    observations = []
    for case in ordered:
        observations.append(
            {
                "case_id": case["case_id"],
                "roughness_z0_m": float(case["roughness_z0_m"]),
                "target_observable": mean([float(value) for value in case["mean_Ux_m_s"]]) / U_SCALE,
            }
        )
    x_values = [math.log(item["roughness_z0_m"] / 1.0) for item in observations]
    y_values = [item["target_observable"] for item in observations]
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator <= 0:
        raise ValueError("roughness_slope_denominator")
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)) / denominator
    return slope, {"roughness_observations": observations}


def evaluate_task(root):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    fidelity = []
    recomputed = {}
    metric_diagnostics = {}
    schema_ok = False
    metric_ok = False
    try:
        hidden_path = Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json"))
        hidden = load(hidden_path)
        result = load(root / "simulation_metrics.json")
        if result.get("task_id") != TASK_ID:
            raise ValueError("simulation_task_id_mismatch")
        submitted = result.get("case_results")
        expected = hidden.get("case_results")
        if not isinstance(submitted, list) or not isinstance(expected, list):
            raise ValueError("case_results_not_list")
        expected_by_id = {case["case_id"]: case for case in expected}
        submitted_by_id = {case.get("case_id"): case for case in submitted if isinstance(case, dict)}
        if len(submitted_by_id) != len(submitted) or set(submitted_by_id) != set(expected_by_id):
            raise ValueError("simulation_case_ids_mismatch")
        probe_count = int(hidden["probe_count"])
        ordered = []
        for expected_case in expected:
            case = submitted_by_id[expected_case["case_id"]]
            if case.get("variant") != expected_case["variant"]:
                raise ValueError("variant_mismatch:" + expected_case["case_id"])
            if case.get("all_run_gates_pass") is not True:
                raise ValueError("run_gates_not_passed:" + expected_case["case_id"])
            if not finite_array(case.get("mean_Ux_m_s"), probe_count) or not finite_array(case.get("resolved_tke_m2_s2"), probe_count):
                raise ValueError("invalid_case_arrays:" + expected_case["case_id"])
            if int(case.get("sample_count", 0)) < int(hidden["minimum_sample_count"]):
                raise ValueError("insufficient_samples:" + expected_case["case_id"])
            if "roughness_z0_m" in expected_case:
                value = case.get("roughness_z0_m")
                if not isinstance(value, (int, float)) or isinstance(value, bool) or abs(float(value) - float(expected_case["roughness_z0_m"])) > 1e-12:
                    raise ValueError("roughness_value_mismatch:" + expected_case["case_id"])
            ordered.append(case)
            u_delta = nmae(case["mean_Ux_m_s"], expected_case["mean_Ux_m_s"], U_SCALE)
            tke_delta = nmae(case["resolved_tke_m2_s2"], expected_case["resolved_tke_m2_s2"], TKE_SCALE)
            fidelity.append((u_delta + tke_delta) / 2.0)
        schema_ok = True

        if TASK_NUMBER == 91:
            primary_id = "array_les_turbulence_nmae"
            value, metric_diagnostics = metric_task091(root, ordered)
        elif TASK_NUMBER == 93:
            primary_id = "roughness_sensitivity_slope"
            value, metric_diagnostics = metric_task093(ordered)
        else:
            raise ValueError("unsupported_task")
        recomputed[primary_id] = value
        reported = load(root / "metrics.json").get("values", {}).get(primary_id)
        if (
            not isinstance(reported, (int, float))
            or isinstance(reported, bool)
            or not math.isfinite(float(reported))
            or abs(float(reported) - value) > max(1e-8, abs(value) * 1e-5)
        ):
            errors.append("reported_metric_not_recomputed:" + primary_id)
        else:
            metric_ok = True
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    components = {"result_schema_and_gates": 0.0, "metric_recomputation": 0.0, "hidden_array_fidelity": 0.0}
    if not base.get("errors"):
        if schema_ok:
            components["result_schema_and_gates"] = 8.0
        if metric_ok:
            components["metric_recomputation"] = 12.0
        if fidelity:
            average_fidelity = mean(fidelity)
            components["hidden_array_fidelity"] = 20.0 * max(0.0, 1.0 - average_fidelity / 0.15)
    score = min(100.0, float(base.get("score", 0.0)) + sum(components.values()))
    return {
        "task_id": TASK_ID,
        "score": round(score, 4),
        "score_cap": 100.0,
        "status": "openfoam_numerical_reference_check",
        "errors": errors,
        "components": components,
        "diagnostics": {
            "recomputed_metrics": recomputed,
            "per_case_hidden_array_nmae": fidelity,
            **metric_diagnostics,
        },
        "precheck": base,
        "claim_boundary": (
            "Recomputes the task-specific visible metric and compares submitted arrays with frozen OpenFOAM numerical references; "
            "it does not establish experimental truth or release-runtime reproducibility."
        ),
    }


def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
