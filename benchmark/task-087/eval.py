#!/usr/bin/env python3
"""Task-087 six-block stationarity-onset numerical evaluator."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-087"
PRIMARY_ID = "stationarity_onset_detected_s"
WINDOWS = [[float(start), float(start + 60)] for start in range(60, 420, 60)]
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


def onset_from_blocks(blocks) -> tuple[float | None, list[dict]]:
    pairs = []
    for first, second in zip(blocks, blocks[1:]):
        u = nmae(first["mean_Ux_m_s"], second["mean_Ux_m_s"], U_SCALE)
        k = nmae(first["resolved_tke_m2_s2"], second["resolved_tke_m2_s2"], TKE_SCALE)
        pairs.append({"earlier_window_s": first["window_s"], "later_window_s": second["window_s"],
                      "velocity_drift": u, "resolved_tke_drift": k,
                      "passes": u <= 0.05 and k <= 0.02})
    eligible = [index for index in range(len(pairs)) if all(pair["passes"] for pair in pairs[index:])]
    return (float(blocks[eligible[0]]["window_s"][0]) if eligible else None), pairs


def finite_gate_diagnostics(value) -> bool:
    limits = {
        "half_window_velocity_drift": 0.05,
        "half_window_tke_drift": 0.02,
        "max_courant": 1.0,
        "max_abs_continuity": 0.01,
    }
    return isinstance(value, dict) and all(
        isinstance(value.get(key), (int, float)) and not isinstance(value.get(key), bool)
        and math.isfinite(float(value[key])) and abs(float(value[key])) <= limit + 1e-12
        for key, limit in limits.items()
    )


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    fidelity = []
    pairs = []
    recomputed = {}
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
        if case.get("variant") != reference_case["variant"] or case.get("mpi_ranks") != 16:
            raise ValueError("variant_or_rank_mismatch")
        if case.get("all_run_gates_pass") is not True or not finite_gate_diagnostics(case.get("gate_diagnostics")):
            raise ValueError("run_gates_not_passed")
        blocks = case.get("blocks")
        hidden_blocks = reference_case.get("blocks")
        if not isinstance(blocks, list) or not isinstance(hidden_blocks, list) or len(blocks) != 6:
            raise ValueError("six_blocks_required")
        if int(hidden["probe_count"]) != 64:
            raise ValueError("hidden_probe_count_mismatch")
        for index, (block, hidden_block) in enumerate(zip(blocks, hidden_blocks)):
            if not isinstance(block, dict) or block.get("window_s") != WINDOWS[index]:
                raise ValueError("block_window_mismatch:" + str(index))
            samples = block.get("sample_count")
            if not isinstance(samples, int) or isinstance(samples, bool) or samples < 300:
                raise ValueError("block_samples_insufficient:" + str(index))
            if not finite_array(block.get("mean_Ux_m_s"), 64) \
                    or not finite_array(block.get("resolved_tke_m2_s2"), 64):
                raise ValueError("block_arrays_invalid:" + str(index))
            fidelity.append(0.5 * (
                nmae(block["mean_Ux_m_s"], hidden_block["mean_Ux_m_s"], U_SCALE)
                + nmae(block["resolved_tke_m2_s2"], hidden_block["resolved_tke_m2_s2"], TKE_SCALE)
            ))
        schema_ok = True
        detected, pairs = onset_from_blocks(blocks)
        if detected is None:
            raise ValueError("onset_not_detected")
        if case.get("onset_s") != detected:
            errors.append("reported_onset_not_recomputed")
        if abs(pairs[-1]["velocity_drift"] - float(case["gate_diagnostics"]["half_window_velocity_drift"])) > 1e-8 \
                or abs(pairs[-1]["resolved_tke_drift"] - float(case["gate_diagnostics"]["half_window_tke_drift"])) > 1e-8:
            errors.append("gate_drift_not_recomputed")
        reference_onset = float(hidden["stationarity_onset_reference_s"])
        value = detected
        onset_error = abs(detected - reference_onset)
        recomputed[PRIMARY_ID] = value
        reported = load(root / "metrics.json").get("values", {}).get(PRIMARY_ID)
        if not isinstance(reported, (int, float)) or isinstance(reported, bool) or not math.isfinite(float(reported)) \
                or abs(float(reported) - value) > 1e-8:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        elif not any(error in errors for error in ("reported_onset_not_recomputed", "gate_drift_not_recomputed")):
            metric_ok = True
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    components = {"result_schema_and_gates": 0.0, "metric_recomputation": 0.0,
                  "onset_reference_agreement": 0.0, "hidden_array_fidelity": 0.0}
    if not base.get("errors"):
        if schema_ok:
            components["result_schema_and_gates"] = 8.0
        if metric_ok:
            components["metric_recomputation"] = 8.0
            components["onset_reference_agreement"] = 4.0 * max(0.0, 1.0 - onset_error / 120.0)
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
        "diagnostics": {"recomputed_metrics": recomputed, "hidden_onset_error_s": onset_error if recomputed else None,
                        "adjacent_pairs": pairs,
                        "per_block_hidden_array_nmae": fidelity},
        "precheck": base,
        "claim_boundary": "Checks six-block onset rule and hidden OpenFOAM numerical arrays; not experimental truth or release-runtime reproducibility.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
