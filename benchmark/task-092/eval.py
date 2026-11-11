#!/usr/bin/env python3
"""Task-092 raw three-direction OpenFOAM evaluator."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-092"
PRIMARY_ID = "wind_direction_weighted_probe_speed_ratio"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def load(path: Path, limit=16 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def evidence_path(root: Path, relative) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + relative)
    return path


def close_points(actual, expected) -> bool:
    return (isinstance(actual, list) and len(actual) == len(expected)
            and all(len(point) == 3 and all(abs(float(a) - float(b)) <= 1e-9 for a, b in zip(point, ref))
                    for point, ref in zip(actual, expected)))


def probe_statistics(path: Path, expected_points, angle: float, window) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))]
              for match in re.finditer(r"^# Probe \d+ \(([^)]+)\)", text, re.M)]
    if not close_points(points, expected_points):
        raise ValueError("probe_coordinates_mismatch")
    count = len(expected_points)
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(value) for value in NUMBER.findall(line)]
        if len(values) != 1 + 3 * count or not all(math.isfinite(value) for value in values):
            raise ValueError("probe_row_invalid")
        if float(window[0]) < values[0] <= float(window[1]) + 1e-8:
            rows.append(values)
    if len(rows) < 300:
        raise ValueError("probe_samples_insufficient")
    means = [sum(row[column] for row in rows) / len(rows) for column in range(1, 1 + 3 * count)]
    variances = [sum((row[column] - means[column - 1]) ** 2 for row in rows) / len(rows)
                 for column in range(1, 1 + 3 * count)]
    mean_vectors = [means[index:index + 3] for index in range(0, len(means), 3)]
    tke = [0.5 * sum(variances[index:index + 3]) for index in range(0, len(variances), 3)]
    radians = math.radians(angle)
    streamwise = [vector[0] * math.cos(radians) + vector[1] * math.sin(radians) for vector in mean_vectors]
    return {"sample_count": len(rows), "mean_U_m_s": mean_vectors,
            "resolved_tke_m2_s2": tke, "mean_streamwise_m_s": sum(streamwise) / count}


def log_gates(solver_log: Path, mesh_log: Path) -> dict:
    text = solver_log.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_log.read_text(encoding="utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"^Time = ([\deE+.-]+)", text, re.M)]
    courant = [float(value) for value in re.findall(r"Courant Number mean: [^\n]+ max: ([\deE+.-]+)", text)]
    continuity = [float(value) for value in re.findall(r"cumulative = ([\deE+.-]+)", text)]
    gates = {
        "mesh_ok": "Mesh OK." in mesh,
        "model_selected": "Selecting LES turbulence model WALE" in text,
        "finite_log": not bool(re.search(r"\b(nan|inf)\b", text, re.I)),
        "end_time": bool(times) and times[-1] >= 180.0 - 1e-7,
        "courant": bool(courant) and max(courant) <= 1.0,
        "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
    }
    return {"gates": gates, "max_courant": max(courant) if courant else None,
            "max_abs_continuity": max(map(abs, continuity)) if continuity else None}


def finite_vectors(value, length: int) -> bool:
    return (isinstance(value, list) and len(value) == length
            and all(isinstance(row, list) and len(row) == 3
                    and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                            and math.isfinite(float(item)) for item in row) for row in value))


def finite_array(value, length: int) -> bool:
    return (isinstance(value, list) and len(value) == length
            and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                    and math.isfinite(float(item)) for item in value))


def flat(vectors):
    return [float(value) for vector in vectors for value in vector]


def nmae(left, right, scale: float) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("comparison_shape")
    return sum(abs(float(a) - float(b)) for a, b in zip(left, right)) / len(left) / scale


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    verified = []
    raw_results = []
    hidden_deltas = []
    metric_ok = False
    report_ok = False
    try:
        contract = load(root / "local_assets" / "multi_direction_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "directional_evidence_manifest.json")
        simulation = load(root / "simulation_metrics.json")
        metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (contract, hidden, evidence, simulation)):
            raise ValueError("task_id_mismatch")
        expected_cases = contract["cases"]
        expected_ids = [item["case_id"] for item in expected_cases]
        evidence_cases = evidence.get("cases")
        reported_cases = simulation.get("case_results")
        if not isinstance(evidence_cases, list) or [item.get("case_id") for item in evidence_cases] != expected_ids:
            raise ValueError("evidence_case_ids")
        if not isinstance(reported_cases, list) or [item.get("case_id") for item in reported_cases] != expected_ids:
            raise ValueError("reported_case_ids")
        hidden_by_id = {item["case_id"]: item for item in hidden["case_results"]}
        used = set()
        reported_consistent = True
        for expected, evidence_case, reported in zip(expected_cases, evidence_cases, reported_cases):
            case_id = expected["case_id"]
            angle = float(expected["wind_direction_degrees"])
            if float(evidence_case.get("wind_direction_degrees", -999)) != angle \
                    or float(reported.get("wind_direction_degrees", -999)) != angle:
                raise ValueError("angle_mismatch:" + case_id)
            paths = {}
            for key in ("probe_U", "solver_log", "checkMesh_log"):
                item = evidence_case.get(key)
                if not isinstance(item, dict):
                    raise ValueError("evidence_item:" + case_id + ":" + key)
                path = evidence_path(root, item.get("path"))
                if path in used or item.get("sha256") != sha256(path):
                    raise ValueError("evidence_hash_or_duplicate:" + case_id + ":" + key)
                used.add(path)
                paths[key] = path
            stats = probe_statistics(paths["probe_U"], contract["probe_points_m"], angle,
                                     contract["statistical_window_s"])
            gates = log_gates(paths["solver_log"], paths["checkMesh_log"])
            if not all(gates["gates"].values()):
                raise ValueError("run_gate_failed:" + case_id)
            if not finite_vectors(reported.get("mean_U_m_s"), len(contract["probe_points_m"])) \
                    or not finite_array(reported.get("resolved_tke_m2_s2"), len(contract["probe_points_m"])):
                reported_consistent = False
            else:
                if nmae(flat(reported["mean_U_m_s"]), flat(stats["mean_U_m_s"]), 1.2) > 1e-8 \
                        or nmae(reported["resolved_tke_m2_s2"], stats["resolved_tke_m2_s2"], 1.44) > 1e-8 \
                        or int(reported.get("sample_count", -1)) != stats["sample_count"]:
                    reported_consistent = False
            reference = hidden_by_id[case_id]
            hidden_deltas.append(0.5 * (
                nmae(flat(stats["mean_U_m_s"]), flat(reference["mean_U_m_s"]), 1.2)
                + nmae(stats["resolved_tke_m2_s2"], reference["resolved_tke_m2_s2"], 1.44)
            ))
            raw_results.append({"case_id": case_id, "wind_direction_degrees": angle, **stats, **gates})
            verified.append(case_id)
        report_ok = reported_consistent
        primary = sum(item["mean_streamwise_m_s"] for item in raw_results) / len(raw_results) / 1.2
        value = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(float(value)) and abs(float(value) - primary) <= 1e-8)
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
    except Exception as exc:
        errors.append("direction_reference_check:" + str(exc))

    coverage = len(verified) / 3
    components = {"raw_directional_evidence": 0.0, "reported_array_recomputation": 0.0,
                  "primary_metric_recomputation": 0.0, "hidden_numerical_fidelity": 0.0}
    if not base.get("errors"):
        components["raw_directional_evidence"] = 12.0 * coverage
        if report_ok:
            components["reported_array_recomputation"] = 8.0
        if metric_ok:
            components["primary_metric_recomputation"] = 5.0
        if hidden_deltas:
            components["hidden_numerical_fidelity"] = 15.0 * max(0.0, 1.0 - sum(hidden_deltas) / len(hidden_deltas) / 0.15)
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"verified_case_ids": verified, "case_coverage": coverage,
                        "raw_results": raw_results, "hidden_case_deltas": hidden_deltas},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM direction evidence and hidden numerical arrays; not PIV/DNS, experimental truth or release-runtime reproducibility.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
