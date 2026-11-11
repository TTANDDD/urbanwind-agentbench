#!/usr/bin/env python3
import hashlib
import json
import math
import os
import re
from pathlib import Path

from contract_evaluator import evaluate as precheck

TASK_ID = "urbanwind-task-097"
PRIMARY_METRIC = "procedural_city_field_completeness"
U_SCALE = 1.2
TKE_SCALE = 1.44


def load(path, max_bytes=64 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


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


def nmae(values, reference, scale):
    return sum(abs(float(left) - float(right)) for left, right in zip(values, reference)) / len(reference) / scale


def safe_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("invalid_field_path")
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("field_path_outside_root") from exc
    if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("field_missing_unsafe_or_oversized:" + relative)
    return candidate


def valid_openfoam_field(root, entry, expected_class):
    path = safe_file(root, entry.get("path"))
    if digest(path) != entry.get("sha256"):
        raise ValueError("field_hash_mismatch:" + str(entry.get("field")))
    with path.open("rb") as handle:
        text = handle.read(32768).decode("utf-8", errors="replace")
    class_match = re.search(r"\bclass\s+([^;\s]+)\s*;", text)
    object_match = re.search(r"\bobject\s+([^;\s]+)\s*;", text)
    if not class_match or class_match.group(1) != expected_class:
        raise ValueError("field_class_mismatch:" + str(entry.get("field")))
    if not object_match or object_match.group(1) != entry.get("field"):
        raise ValueError("field_object_mismatch:" + str(entry.get("field")))


def evaluate_task(root):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    completeness = 0.0
    array_fidelity = None
    recomputed = {}
    try:
        hidden_path = Path(os.environ.get("URBANWIND_REFERENCE", "/tmp/urbanwind-task-097-reference.json"))
        hidden = load(hidden_path, 16 * 1024 * 1024)
        result = load(root / "simulation_metrics.json", 16 * 1024 * 1024)
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
        case = submitted_by_id["task-097-case-001"]
        reference_case = expected_by_id["task-097-case-001"]
        if case.get("variant") != reference_case.get("variant"):
            raise ValueError("variant_mismatch")
        if case.get("all_run_gates_pass") is not True:
            raise ValueError("run_gates_not_passed")
        end_time = case.get("end_time_s")
        if not isinstance(end_time, (int, float)) or isinstance(end_time, bool) or abs(float(end_time) - float(hidden["required_end_time_s"])) > 1e-6:
            raise ValueError("end_time_mismatch")

        manifest = case.get("field_manifest")
        if not isinstance(manifest, list):
            raise ValueError("field_manifest_not_list")
        by_pair = {}
        for entry in manifest:
            if not isinstance(entry, dict):
                continue
            pair = (entry.get("time_role"), entry.get("field"))
            if pair in by_pair:
                raise ValueError("duplicate_field_pair:" + str(pair))
            by_pair[pair] = entry
        valid_pairs = 0
        pair_errors = []
        for requirement in hidden["required_field_time_pairs"]:
            pair = (requirement["time_role"], requirement["field"])
            entry = by_pair.get(pair)
            if entry is None:
                pair_errors.append("missing_field_pair:" + pair[0] + ":" + pair[1])
                continue
            try:
                valid_openfoam_field(root, entry, requirement["class"])
                valid_pairs += 1
            except Exception as exc:
                pair_errors.append(str(exc))
        completeness = valid_pairs / len(hidden["required_field_time_pairs"])
        if pair_errors:
            errors.extend(pair_errors)
        if completeness < 1.0:
            errors.append("field_completeness_below_one")
        recomputed[PRIMARY_METRIC] = completeness

        probe_count = int(hidden["probe_count"])
        if not finite_array(case.get("mean_Ux_m_s"), probe_count) or not finite_array(case.get("resolved_tke_m2_s2"), probe_count):
            raise ValueError("invalid_case_arrays")
        if int(case.get("sample_count", 0)) < int(hidden["minimum_sample_count"]):
            raise ValueError("insufficient_samples")
        u_delta = nmae(case["mean_Ux_m_s"], reference_case["mean_Ux_m_s"], U_SCALE)
        tke_delta = nmae(case["resolved_tke_m2_s2"], reference_case["resolved_tke_m2_s2"], TKE_SCALE)
        array_fidelity = (u_delta + tke_delta) / 2.0

        metrics = load(root / "metrics.json")
        reported = metrics.get("values", {}).get(PRIMARY_METRIC)
        if (
            not isinstance(reported, (int, float))
            or isinstance(reported, bool)
            or not math.isfinite(float(reported))
            or abs(float(reported) - completeness) > 1e-9
        ):
            errors.append("reported_metric_not_recomputed:" + PRIMARY_METRIC)
    except Exception as exc:
        errors.append("numerical_reference_check:" + str(exc))

    components = {"field_completeness": 0.0, "hidden_array_fidelity": 0.0}
    if not base.get("errors"):
        if not any(error.startswith("reported_metric_not_recomputed:") for error in errors):
            components["field_completeness"] = 20.0 * completeness
        if array_fidelity is not None:
            components["hidden_array_fidelity"] = 20.0 * max(0.0, 1.0 - array_fidelity / 0.15)
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
            "hidden_array_nmae": array_fidelity,
        },
        "precheck": base,
        "claim_boundary": (
            "Checks the visible field/time contract, file hashes and OpenFOAM headers, then compares "
            "submitted probe statistics with a frozen OpenFOAM numerical reference. It does not establish "
            "experimental truth or release-runtime reproducibility."
        ),
    }


def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
