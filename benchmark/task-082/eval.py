#!/usr/bin/env python3
import csv
import json
import math
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck

TASK_ID = 'urbanwind-task-082'
TASK_NUMBER = 82
VALID_INDICES = [i for i in range(64) if i not in {6, 7, 8, 9, 10}]
U_SCALE = 1.2
TKE_SCALE = 1.44

def load(path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))

def finite_array(value):
    return isinstance(value, list) and len(value) == 64 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)) for x in value)

def avg(values):
    return sum(values) / len(values)

def nmae(values, reference, scale):
    return avg([abs(float(values[i]) - float(reference[i])) for i in VALID_INDICES]) / scale

def array_spread(cases):
    u = avg([max(float(c["mean_Ux_m_s"][i]) for c in cases) - min(float(c["mean_Ux_m_s"][i]) for c in cases) for i in VALID_INDICES]) / U_SCALE
    tke = avg([max(float(c["resolved_tke_m2_s2"][i]) for c in cases) - min(float(c["resolved_tke_m2_s2"][i]) for c in cases) for i in VALID_INDICES]) / TKE_SCALE
    return (u + tke) / 2.0

def evaluate_task(root):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    diagnostics = {}
    recomputed = {}
    fidelity = []
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
        ordered = []
        for expected_case in expected:
            case = submitted_by_id[expected_case["case_id"]]
            if case.get("variant") != expected_case["variant"]:
                raise ValueError("variant_mismatch:" + expected_case["case_id"])
            if case.get("all_run_gates_pass") is not True:
                raise ValueError("run_gates_not_passed:" + expected_case["case_id"])
            if not finite_array(case.get("mean_Ux_m_s")) or not finite_array(case.get("resolved_tke_m2_s2")):
                raise ValueError("invalid_case_arrays:" + expected_case["case_id"])
            if int(case.get("sample_count", 0)) < 300:
                raise ValueError("insufficient_samples:" + expected_case["case_id"])
            ordered.append(case)
            u_delta = nmae(case["mean_Ux_m_s"], expected_case["mean_Ux_m_s"], U_SCALE)
            tke_delta = nmae(case["resolved_tke_m2_s2"], expected_case["resolved_tke_m2_s2"], TKE_SCALE)
            fidelity.append((u_delta + tke_delta) / 2.0)

        if TASK_NUMBER in {82, 83}:
            with (root / "local_assets" / "reference_observations.csv").open(encoding="utf-8", newline="") as handle:
                observations = list(csv.DictReader(handle))
            reference_u = [float(row["u_m_s"]) for row in observations]
            reference_tke = [float(row["tke_m2_s2"]) for row in observations]
            per_case = {}
            for case in ordered:
                ue = nmae(case["mean_Ux_m_s"], reference_u, U_SCALE)
                te = nmae(case["resolved_tke_m2_s2"], reference_tke, TKE_SCALE)
                per_case[case["case_id"]] = {"velocity_nmae": ue, "turbulence_nmae": te, "combined_nmae": (ue + te) / 2.0}
        if TASK_NUMBER == 82:
            one = per_case[ordered[0]["case_id"]]
            recomputed = {"les_local_reference_velocity_nmae": one["velocity_nmae"], "les_local_reference_turbulence_nmae": one["turbulence_nmae"]}
        elif TASK_NUMBER == 83:
            selected = result.get("selected_case_id")
            if selected not in per_case:
                raise ValueError("selected_case_id_missing_or_invalid")
            best = min(item["combined_nmae"] for item in per_case.values())
            recomputed = {
                "sgs_reference_observable_nmae": avg([item["combined_nmae"] for item in per_case.values()]),
                "sgs_target_observable_spread": array_spread(ordered),
                "sgs_selected_model_regret": per_case[selected]["combined_nmae"] - best,
            }
        elif TASK_NUMBER == 85:
            recomputed = {"scheme_observable_spread": array_spread(ordered)}

        reported = load(root / "metrics.json").get("values", {})
        for metric_id, value in recomputed.items():
            if metric_id not in reported or abs(float(reported[metric_id]) - value) > max(1e-6, abs(value) * 1e-4):
                errors.append("reported_metric_not_recomputed:" + metric_id)
        diagnostics = {"recomputed_metrics": recomputed, "per_case_hidden_array_nmae": fidelity}
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    bonus_components = {"result_schema_and_gates": 0.0, "metric_recomputation": 0.0, "hidden_array_fidelity": 0.0}
    if not base.get("errors") and not any(error.startswith("numerical_reference_check:") for error in errors):
        bonus_components["result_schema_and_gates"] = 8.0
        if not any(error.startswith("reported_metric_not_recomputed:") for error in errors):
            bonus_components["metric_recomputation"] = 12.0
        if fidelity:
            average_fidelity = avg(fidelity)
            bonus_components["hidden_array_fidelity"] = 20.0 * max(0.0, 1.0 - average_fidelity / 0.15)
    bonus = sum(bonus_components.values())
    score = min(100.0, float(base.get("score", 0.0)) + bonus)
    return {
        "task_id": TASK_ID,
        "score": round(score, 4),
        "score_cap": 100.0,
        "status": "openfoam_numerical_reference_check",
        "errors": errors,
        "components": bonus_components,
        "diagnostics": diagnostics,
        "precheck": base,
        "claim_boundary": "Checks submitted arrays and recomputed task metrics against frozen OpenFOAM numerical references; it does not establish experimental truth or release runtime reproducibility.",
    }

def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))

if __name__ == "__main__":
    main()
