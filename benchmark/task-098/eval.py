#!/usr/bin/env python3
"""Task-098 method-only decomposition numerical-array evaluator."""
from __future__ import annotations

import json
import math
import os
from itertools import combinations
from pathlib import Path

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-098"
PRIMARY_ID = "decomposition_reproducibility_error"
METHODS = ("simple", "scotch", "hierarchical")
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


def nmae(values, reference, scale: float) -> float:
    return sum(abs(float(a) - float(b)) for a, b in zip(values, reference)) / len(reference) / scale


def pairwise_metric(cases) -> tuple[float, list[dict]]:
    pairs = []
    for first, second in combinations(cases, 2):
        ux = nmae(first["mean_Ux_m_s"], second["mean_Ux_m_s"], U_SCALE)
        k = nmae(first["resolved_tke_m2_s2"], second["resolved_tke_m2_s2"], TKE_SCALE)
        pairs.append({"methods": [first["method"], second["method"]],
                      "velocity_component": ux, "tke_component": k,
                      "combined_difference": 0.5 * (ux + k)})
    return max(item["combined_difference"] for item in pairs), pairs


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
    pairs = []
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
        for index, expected_case in enumerate(expected):
            case = submitted_by_id[expected_case["case_id"]]
            if case.get("variant") != expected_case["variant"] or case.get("method") != METHODS[index]:
                raise ValueError("method_or_variant_mismatch:" + expected_case["case_id"])
            if case.get("window_s") != [300.0, 420.0]:
                raise ValueError("window_mismatch:" + expected_case["case_id"])
            if case.get("mpi_ranks") != 16:
                raise ValueError("rank_count_mismatch:" + expected_case["case_id"])
            samples = case.get("sample_count")
            if not isinstance(samples, int) or isinstance(samples, bool) or samples < 600:
                raise ValueError("insufficient_samples:" + expected_case["case_id"])
            if case.get("all_run_gates_pass") is not True or not finite_gate_diagnostics(case.get("gate_diagnostics")):
                raise ValueError("run_gates_not_passed:" + expected_case["case_id"])
            if not finite_array(case.get("mean_Ux_m_s"), count) or not finite_array(case.get("resolved_tke_m2_s2"), count):
                raise ValueError("invalid_arrays:" + expected_case["case_id"])
            ordered.append(case)
            fidelity.append(0.5 * (
                nmae(case["mean_Ux_m_s"], expected_case["mean_Ux_m_s"], U_SCALE)
                + nmae(case["resolved_tke_m2_s2"], expected_case["resolved_tke_m2_s2"], TKE_SCALE)
            ))
        schema_ok = True
        value, pairs = pairwise_metric(ordered)
        recomputed[PRIMARY_ID] = value
        reported = load(root / "metrics.json").get("values", {}).get(PRIMARY_ID)
        if not isinstance(reported, (int, float)) or isinstance(reported, bool) or not math.isfinite(float(reported)) \
                or abs(float(reported) - value) > max(1e-8, abs(value) * 1e-5):
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        else:
            metric_ok = True
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
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0,
        "status": "openfoam_numerical_reference_check",
        "errors": errors,
        "components": components,
        "diagnostics": {"recomputed_metrics": recomputed, "pairwise_differences": pairs,
                        "per_case_hidden_array_nmae": fidelity},
        "precheck": base,
        "claim_boundary": "Checks a method-only, fixed-rank metric and OpenFOAM numerical arrays; not experimental truth or distributable-runtime reproducibility.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
