#!/usr/bin/env python3
"""Task-096 raw passive-scalar OpenFOAM evaluator."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-096"
EVALUATOR_KIND = "openfoam_numerical_reference_check"
PRIMARY_ID = "scalar_mass_balance_error_percent"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
END_TIME = 20.0
DT = 0.02
SOURCE_RATE = 0.01
LIMIT_PERCENT = 2.0


def load(path: Path, limit=32 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + value)
    return path


def numeric_rows(path: Path, expected_columns: int | None = None) -> list[list[float]]:
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = [float(value) for value in NUMBER.findall(line)]
        if expected_columns is not None and len(row) != expected_columns:
            raise ValueError("row_shape:" + path.name)
        if len(row) < 2 or not all(math.isfinite(value) for value in row):
            raise ValueError("row_nonfinite:" + path.name)
        result.append(row)
    if not result:
        raise ValueError("empty_data:" + path.name)
    return result


def exact_times(rows: list[list[float]]) -> bool:
    expected = [round(0.1 * index, 10) for index in range(1, 201)]
    return len(rows) == 200 and all(abs(row[0] - value) <= 1e-9 for row, value in zip(rows, expected))


def integrate(rows: list[list[float]]) -> float:
    points = [(0.0, 0.0)] + [(row[0], row[-1]) for row in rows]
    return sum(0.5 * (a[1] + b[1]) * (b[0] - a[0]) for a, b in zip(points, points[1:]))


def probe_data(path: Path, expected_points) -> tuple[list[float], list[list[float]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))]
              for match in re.finditer(r"^# Probe \d+ \(([^)]+)\)", text, re.M)]
    if points != expected_points:
        raise ValueError("probe_coordinates_mismatch")
    rows = numeric_rows(path, 1 + len(expected_points))
    if not exact_times(rows):
        raise ValueError("probe_times_mismatch")
    return [row[0] for row in rows], [row[1:] for row in rows]


def log_gates(solver_log: Path, topo_log: Path, mesh_log: Path) -> dict:
    solver = solver_log.read_text(encoding="utf-8", errors="replace")
    topo = topo_log.read_text(encoding="utf-8", errors="replace")
    mesh = mesh_log.read_text(encoding="utf-8", errors="replace")
    times = [float(value) for value in re.findall(r"^Time = ([\deE+.-]+)", solver, re.M)]
    residuals = [float(value) for value in re.findall(
        r"Solving for T, Initial residual = ([\deE+.-]+)", solver
    )]
    mins = [float(value) for value in re.findall(r"min\(T\)\s*=\s*([\deE+.-]+)", solver)]
    maxs = [float(value) for value in re.findall(r"max\(T\)\s*=\s*([\deE+.-]+)", solver)]
    source_cells = [int(value) for value in re.findall(r"sourceCells now size (\d+)", topo)]
    source_zones = [int(value) for value in re.findall(r"sourceZone now size (\d+)", topo)]
    selected = [int(value) for value in re.findall(r"selected (\d+) cell\(s\)", solver)]
    volumes = [float(value) for value in re.findall(r"with volume ([\deE+.-]+)", solver)]
    gates = {
        "mesh_ok": "Mesh OK." in mesh,
        "final_time": bool(times) and abs(times[-1] - END_TIME) <= 1e-9,
        "finite_log": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
        "residual_count": len(residuals) == int(END_TIME / DT)
        and all(math.isfinite(value) for value in residuals),
        "source_zone": source_cells[-1:] == [8] and source_zones[-1:] == [8]
        and selected[-1:] == [8] and volumes[-1:] == [0.125],
        "nonnegative_scalar": bool(mins) and min(mins) >= -1e-8,
        "positive_scalar": bool(maxs) and max(maxs) > 0,
    }
    return {"gates": gates, "source_cell_count": source_cells[-1] if source_cells else None,
            "source_volume_m3": volumes[-1] if volumes else None,
            "scalar_min": min(mins) if mins else None, "scalar_max": max(maxs) if maxs else None}


def finite_array(value, length: int) -> bool:
    return isinstance(value, list) and len(value) == length and all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        and math.isfinite(float(item)) for item in value
    )


def finite_matrix(value, rows: int, columns: int) -> bool:
    return isinstance(value, list) and len(value) == rows and all(finite_array(row, columns) for row in value)


def nmae(left, right, scale: float) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("nmae_shape")
    return sum(abs(float(a) - float(b)) for a, b in zip(left, right)) / len(left) / max(scale, 1e-30)


def flatten(matrix):
    return [float(value) for row in matrix for value in row]


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    verified = []
    recomputed_cases = []
    hidden_deltas = []
    report_ok = False
    metric_ok = False
    try:
        contract = load(root / "local_assets" / "passive_scalar_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "scalar_evidence_manifest.json")
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
        report_ok = True
        for expected, evidence_case, reported in zip(expected_cases, evidence_cases, reported_cases):
            case_id = expected["case_id"]
            if evidence_case.get("variant") != expected["variant"] or reported.get("variant") != expected["variant"]:
                raise ValueError("variant_mismatch:" + case_id)
            if reported.get("source_center_m") != expected["source_center_m"]:
                raise ValueError("source_center_mismatch:" + case_id)
            paths = {}
            for key in ("storage", "outlet_flux", "probe_T", "solver_log", "topoSet_log", "checkMesh_log"):
                item = evidence_case.get(key)
                if not isinstance(item, dict):
                    raise ValueError("evidence_item:" + case_id + ":" + key)
                path = evidence_path(root, item.get("path"))
                if path in used or item.get("sha256") != sha256(path):
                    raise ValueError("evidence_hash_or_duplicate:" + case_id + ":" + key)
                used.add(path)
                paths[key] = path
            storage_rows = numeric_rows(paths["storage"], 2)
            flux_rows = numeric_rows(paths["outlet_flux"], 2)
            if not exact_times(storage_rows) or not exact_times(flux_rows):
                raise ValueError("function_times_mismatch:" + case_id)
            times, probes = probe_data(paths["probe_T"], contract["probe_points_m"])
            gates = log_gates(paths["solver_log"], paths["topoSet_log"], paths["checkMesh_log"])
            if not all(gates["gates"].values()):
                raise ValueError("run_gate_failed:" + case_id)
            storage = [row[-1] for row in storage_rows]
            outlet_flux = [row[-1] for row in flux_rows]
            source = SOURCE_RATE * END_TIME
            outflow = integrate(flux_rows)
            final_storage = storage[-1]
            balance = 100 * abs(source - outflow - final_storage) / source
            if balance > LIMIT_PERCENT:
                raise ValueError("mass_balance_gate:" + case_id)
            raw = {
                "case_id": case_id, "variant": expected["variant"],
                "source_center_m": expected["source_center_m"], "time_s": times,
                "storage_scalar_m3": storage, "outlet_scalar_flux_m3_s": outlet_flux,
                "probe_concentration": probes, "source_scalar_m3": source,
                "outflow_scalar_m3": outflow, "final_storage_scalar_m3": final_storage,
                "scalar_mass_balance_error_percent": balance, **gates,
            }
            recomputed_cases.append(raw)
            verified.append(case_id)
            if not (finite_array(reported.get("time_s"), 200)
                    and finite_array(reported.get("storage_scalar_m3"), 200)
                    and finite_array(reported.get("outlet_scalar_flux_m3_s"), 200)
                    and finite_matrix(reported.get("probe_concentration"), 200, 8)):
                report_ok = False
            else:
                checks = [
                    nmae(reported["time_s"], times, END_TIME),
                    nmae(reported["storage_scalar_m3"], storage, source),
                    nmae(reported["outlet_scalar_flux_m3_s"], outlet_flux,
                         max(max(map(abs, outlet_flux)), 1e-6)),
                    nmae(flatten(reported["probe_concentration"]), flatten(probes), 0.1),
                ]
                scalar_fields = {
                    "source_scalar_m3": source, "outflow_scalar_m3": outflow,
                    "final_storage_scalar_m3": final_storage,
                    PRIMARY_ID: balance, "source_cell_count": 8, "source_volume_m3": 0.125,
                }
                if any(value > 1e-8 for value in checks) or any(
                    not isinstance(reported.get(key), (int, float))
                    or isinstance(reported.get(key), bool)
                    or abs(float(reported[key]) - value) > max(1e-10, abs(value) * 1e-7)
                    for key, value in scalar_fields.items()
                ) or reported.get("all_run_gates_pass") is not True:
                    report_ok = False
            reference = hidden_by_id[case_id]
            hidden_deltas.append(max(
                nmae(storage, reference["storage_scalar_m3"], source),
                nmae(outlet_flux, reference["outlet_scalar_flux_m3_s"],
                     max(max(map(abs, reference["outlet_scalar_flux_m3_s"])), 1e-6)),
                nmae(flatten(probes), flatten(reference["probe_concentration"]), 0.1),
            ))
        primary = max(item[PRIMARY_ID] for item in recomputed_cases)
        reported_primary = metrics.get("values", {}).get(PRIMARY_ID)
        metric_ok = isinstance(reported_primary, (int, float)) and not isinstance(reported_primary, bool) \
            and math.isfinite(float(reported_primary)) and abs(float(reported_primary) - primary) <= 1e-8
        if not report_ok:
            errors.append("reported_arrays_not_recomputed")
        if not metric_ok:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
    except Exception as exc:
        errors.append("passive_scalar_reference_check:" + str(exc))

    coverage = len(verified) / 3
    components = {"raw_scalar_evidence": 0.0, "reported_array_recomputation": 0.0,
                  "primary_metric_recomputation": 0.0, "hidden_numerical_fidelity": 0.0}
    if not base.get("errors"):
        components["raw_scalar_evidence"] = 12.0 * coverage
        if report_ok:
            components["reported_array_recomputation"] = 8.0
        if metric_ok:
            components["primary_metric_recomputation"] = 5.0
        if hidden_deltas:
            components["hidden_numerical_fidelity"] = 15.0 * max(
                0.0, 1.0 - sum(hidden_deltas) / len(hidden_deltas) / 0.15
            )
    return {
        "task_id": TASK_ID,
        "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_passive_scalar_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"verified_case_ids": verified, "case_coverage": coverage,
                        "recomputed_cases": recomputed_cases, "hidden_case_deltas": hidden_deltas,
                        "mass_balance_limit_percent": LIMIT_PERCENT},
        "precheck": base,
        "claim_boundary": (
            "Checks raw scalarTransportFoam evidence, mass closure and hidden numerical time series; "
            "not PIV, DNS, experiment, or distributable-runtime reproducibility."
        ),
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
