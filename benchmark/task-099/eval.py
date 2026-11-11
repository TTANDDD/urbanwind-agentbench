#!/usr/bin/env python3
"""Task-099 continuous/single/multi-restart numerical-array evaluator."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-099"
PRIMARY_ID = "restart_continuity_error"
U_SCALE = 1.2
U2_SCALE = 1.44
ACCEPTANCE_LIMIT = 0.05


def load(path: Path):
    if not path.is_file() or path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def finite_array(value, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        and math.isfinite(float(item)) for item in value
    )


def nmae(values, reference, scale: float) -> float:
    if len(values) != len(reference) or not reference:
        raise ValueError("nmae_shape")
    return sum(abs(float(a) - float(b)) for a, b in zip(values, reference)) / len(reference) / scale


def continuity_metric(cases: list[dict]) -> tuple[float, list[dict]]:
    baseline = cases[0]
    comparisons = []
    for case in cases[1:]:
        components = {
            "mean_U_vector_nmae": nmae(case["mean_U_m_s"], baseline["mean_U_m_s"], U_SCALE),
            "resolved_tke_nmae": nmae(
                case["resolved_tke_m2_s2"], baseline["resolved_tke_m2_s2"], U2_SCALE
            ),
            "mean_kinematic_pressure_nmae": nmae(
                case["mean_p_m2_s2"], baseline["mean_p_m2_s2"], U2_SCALE
            ),
        }
        comparisons.append({
            "baseline_case_id": baseline["case_id"],
            "restart_case_id": case["case_id"],
            **components,
            "restart_continuity_error": max(components.values()),
        })
    return max(item["restart_continuity_error"] for item in comparisons), comparisons


def finite_gate_diagnostics(value) -> bool:
    limits = {
        "half_window_velocity_drift": 0.05,
        "half_window_tke_drift": 0.02,
        "max_courant": 1.0,
        "max_abs_continuity": 0.01,
    }
    return isinstance(value, dict) and all(
        isinstance(value.get(key), (int, float))
        and not isinstance(value.get(key), bool)
        and math.isfinite(float(value[key]))
        and abs(float(value[key])) <= limit + 1e-12
        for key, limit in limits.items()
    )


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    fidelity = []
    recomputed = {}
    comparisons = []
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
        if not isinstance(expected, list) or not isinstance(submitted, list) or len(expected) != 3:
            raise ValueError("case_results_not_three")
        expected_by_id = {case["case_id"]: case for case in expected}
        submitted_by_id = {case.get("case_id"): case for case in submitted if isinstance(case, dict)}
        if len(submitted_by_id) != len(submitted) or set(submitted_by_id) != set(expected_by_id):
            raise ValueError("case_ids_mismatch")
        count = int(hidden["probe_count"])
        if count != 64:
            raise ValueError("hidden_probe_count_mismatch")
        ordered = []
        for expected_case in expected:
            case_id = expected_case["case_id"]
            case = submitted_by_id[case_id]
            for key in ("variant", "restart_mode", "segments_s"):
                if case.get(key) != expected_case[key]:
                    raise ValueError(f"{key}_mismatch:" + case_id)
            if case.get("window_s") != [360.0, 420.0]:
                raise ValueError("window_mismatch:" + case_id)
            if case.get("mpi_ranks") != 16:
                raise ValueError("rank_count_mismatch:" + case_id)
            samples = case.get("sample_count")
            if not isinstance(samples, int) or isinstance(samples, bool) or samples < 300:
                raise ValueError("insufficient_samples:" + case_id)
            if (case.get("continuation_checkpoint_count") != expected_case["continuation_checkpoint_count"]
                    or case.get("continuation_state_hash_records")
                    != expected_case["continuation_state_hash_records"]):
                raise ValueError("checkpoint_evidence_mismatch:" + case_id)
            if case.get("all_run_gates_pass") is not True or not finite_gate_diagnostics(
                    case.get("gate_diagnostics")):
                raise ValueError("run_gates_not_passed:" + case_id)
            if (not finite_array(case.get("mean_U_m_s"), 3 * count)
                    or not finite_array(case.get("resolved_tke_m2_s2"), count)
                    or not finite_array(case.get("mean_p_m2_s2"), count)):
                raise ValueError("invalid_arrays:" + case_id)
            ordered.append(case)
            fidelity.append(max(
                nmae(case["mean_U_m_s"], expected_case["mean_U_m_s"], U_SCALE),
                nmae(case["resolved_tke_m2_s2"], expected_case["resolved_tke_m2_s2"], U2_SCALE),
                nmae(case["mean_p_m2_s2"], expected_case["mean_p_m2_s2"], U2_SCALE),
            ))
        schema_ok = True
        value, comparisons = continuity_metric(ordered)
        recomputed[PRIMARY_ID] = value
        if value > ACCEPTANCE_LIMIT + 1e-12:
            errors.append("restart_continuity_gate_failed")
        reported = load(root / "metrics.json").get("values", {}).get(PRIMARY_ID)
        if (not isinstance(reported, (int, float)) or isinstance(reported, bool)
                or not math.isfinite(float(reported))
                or abs(float(reported) - value) > max(1e-8, abs(value) * 1e-5)):
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        else:
            metric_ok = value <= ACCEPTANCE_LIMIT + 1e-12
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    components = {
        "result_schema_and_gates": 0.0,
        "metric_recomputation": 0.0,
        "hidden_array_fidelity": 0.0,
    }
    if not base.get("errors"):
        if schema_ok:
            components["result_schema_and_gates"] = 8.0
        if metric_ok:
            components["metric_recomputation"] = 12.0
        if fidelity:
            average = sum(fidelity) / len(fidelity)
            components["hidden_array_fidelity"] = 20.0 * max(0.0, 1.0 - average / 0.15)
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0,
        "status": "openfoam_numerical_reference_check_restart",
        "errors": errors,
        "components": components,
        "diagnostics": {
            "recomputed_metrics": recomputed,
            "continuous_to_restart_comparisons": comparisons,
            "per_case_hidden_array_nmae": fidelity,
            "acceptance_limit": ACCEPTANCE_LIMIT,
        },
        "precheck": base,
        "claim_boundary": (
            "Checks frozen OpenFOAM restart segmentation, numerical arrays and a recomputed consistency metric; "
            "not experimental truth or distributable-runtime reproducibility."
        ),
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
