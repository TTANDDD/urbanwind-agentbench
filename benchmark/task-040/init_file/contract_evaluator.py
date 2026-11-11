#!/usr/bin/env python3
"""Structural preflight evaluator for UrbanWind-AgentBench scaffolds.

This evaluator intentionally does not claim numerical or physical validity.  It
checks whether a submission contains traceable artifacts that a future
task-specific evaluator can recompute.  Until real reference runs and hidden
physical checks are installed, the returned score is capped at 60/100.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.1"
SCORE_CAP = 60.0
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"missing_or_oversized:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def confined(root: Path, raw: str) -> Path:
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path_escape:{raw}")
    return candidate


def finite_metric_count(node: Any) -> int:
    if isinstance(node, dict):
        return sum(finite_metric_count(value) for value in node.values())
    if isinstance(node, list):
        return sum(finite_metric_count(value) for value in node)
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return int(math.isfinite(float(node)))
    return 0


def software_names(run: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    software = run.get("software", [])
    if not isinstance(software, list):
        return names
    for item in software:
        if isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]).strip().lower())
        elif isinstance(item, str):
            names.add(item.strip().lower())
    return names


def evaluate(root: Path, expected_task_id: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    components = {
        "artifact_presence": 0.0,
        "parseability": 0.0,
        "run_provenance": 0.0,
        "raw_evidence": 0.0,
        "metric_traceability": 0.0,
        "decision_trace": 0.0,
    }

    try:
        task_spec = load_json(root / "task_spec.json")
        reporting = load_json(root / "reporting_contract.json")
    except Exception as exc:  # fail closed when the trusted task contract is absent
        return {
            "schema_version": SCHEMA_VERSION,
            "score": 0.0,
            "score_cap": SCORE_CAP,
            "status": "invalid_task_contract",
            "errors": [str(exc)],
            "warnings": [],
            "components": components,
            "claim_boundary": "No physical or numerical validity was evaluated.",
        }

    task_id = str(task_spec.get("task_id", ""))
    if expected_task_id and task_id != expected_task_id:
        errors.append(f"task_id_mismatch:{task_id}")
    required = task_spec.get("required_artifacts", [])
    if not isinstance(required, list) or not required:
        errors.append("invalid_required_artifacts")
        required = []

    present = 0
    parseable = 0
    for artifact in required:
        if not isinstance(artifact, dict) or not artifact.get("path"):
            errors.append("invalid_artifact_contract")
            continue
        try:
            path = confined(root, str(artifact["path"]))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path.exists():
            present += 1
        else:
            errors.append(f"missing_artifact:{artifact['path']}")
            continue
        if artifact.get("format") == "json":
            try:
                load_json(path)
                parseable += 1
            except Exception as exc:
                errors.append(f"invalid_json:{artifact['path']}:{exc}")
        else:
            parseable += 1
    denominator = max(1, len(required))
    components["artifact_presence"] = 15.0 * present / denominator
    components["parseability"] = 10.0 * parseable / denominator

    manifest_path = root / str(reporting.get("run_manifest", "run_manifest.json"))
    manifest: dict[str, Any] = {}
    try:
        manifest = load_json(manifest_path)
        if manifest.get("task_id") != task_id:
            errors.append("manifest_task_id_mismatch")
        runs = manifest.get("runs", [])
        if not isinstance(runs, list):
            raise ValueError("runs_not_list")
        expected_case_ids = reporting.get("expected_case_ids", [])
        if not isinstance(expected_case_ids, list) or not expected_case_ids or not all(isinstance(item, str) for item in expected_case_ids):
            raise ValueError("invalid_expected_case_ids")
        required_cases = int(task_spec.get("required_cases", 0))
        if len(expected_case_ids) != required_cases or len(set(expected_case_ids)) != required_cases:
            raise ValueError("expected_case_ids_do_not_match_required_cases")
        run_case_ids = [run.get("case_id") for run in runs if isinstance(run, dict)]
        if len(run_case_ids) != len(set(run_case_ids)):
            errors.append("duplicate_run_case_ids")
        if set(run_case_ids) != set(expected_case_ids) or len(run_case_ids) != len(expected_case_ids):
            errors.append("run_case_ids_mismatch")

        expected_solver_case_ids = reporting.get("expected_solver_case_ids", [])
        required_solver_runs = int(reporting.get("required_solver_runs", task_spec.get("required_solver_runs", 0)))
        if (
            not isinstance(expected_solver_case_ids, list)
            or not all(isinstance(item, str) for item in expected_solver_case_ids)
            or len(expected_solver_case_ids) != required_solver_runs
            or len(set(expected_solver_case_ids)) != required_solver_runs
            or not set(expected_solver_case_ids).issubset(set(expected_case_ids))
        ):
            raise ValueError("invalid_expected_solver_case_ids")
        provenance_points = 0
        raw_points = 0
        valid_solver_runs = 0
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"run_not_object:{index}")
                continue
            required_keys = ("case_id", "commands", "software", "started_at", "ended_at", "exit_code")
            if all(key in run for key in required_keys):
                provenance_points += 1
            else:
                errors.append(f"incomplete_run_provenance:{index}")
            evidence = run.get("evidence", [])
            valid_evidence = 0
            if isinstance(evidence, list):
                for item in evidence:
                    if not isinstance(item, dict) or not item.get("path"):
                        continue
                    try:
                        path = confined(root, str(item["path"]))
                    except ValueError as exc:
                        errors.append(str(exc))
                        continue
                    claimed_hash = str(item.get("sha256", ""))
                    if path.is_file() and HEX64.fullmatch(claimed_hash) and sha256(path) == claimed_hash.lower():
                        valid_evidence += 1
                if valid_evidence:
                    raw_points += 1
                else:
                    errors.append(f"no_hashed_evidence:{index}")
            case_id = run.get("case_id")
            if case_id in expected_solver_case_ids:
                solver_names = software_names(run)
                solver_ok = bool(solver_names.intersection({"openfoam", "palm"}))
                if run.get("execution_role") != "real_solver_run":
                    errors.append(f"solver_role_missing:{case_id}")
                    solver_ok = False
                if run.get("exit_code") != 0:
                    errors.append(f"solver_exit_not_zero:{case_id}")
                    solver_ok = False
                if not solver_names.intersection({"openfoam", "palm"}):
                    errors.append(f"solver_software_missing:{case_id}")
                if valid_evidence <= 0:
                    solver_ok = False
                if solver_ok:
                    valid_solver_runs += 1
        if valid_solver_runs != required_solver_runs:
            errors.append(f"insufficient_solver_evidence:{valid_solver_runs}<{required_solver_runs}")
        run_denominator = max(1, required_cases)
        case_contract_ok = set(run_case_ids) == set(expected_case_ids) and len(run_case_ids) == len(expected_case_ids)
        components["run_provenance"] = min(15.0, 15.0 * provenance_points / run_denominator) if case_contract_ok else 0.0
        components["raw_evidence"] = min(10.0, 10.0 * raw_points / run_denominator)
    except Exception as exc:
        errors.append(f"invalid_run_manifest:{exc}")

    metrics_path = root / str(reporting.get("metrics", "metrics.json"))
    try:
        metrics = load_json(metrics_path)
        declared_units = metrics.get("units", {}) if isinstance(metrics, dict) else {}
        values = metrics.get("values", {}) if isinstance(metrics, dict) else {}
        contracts = reporting.get("metric_contracts", [])
        if not isinstance(contracts, list) or not contracts:
            raise ValueError("missing_metric_contracts")
        contract_by_id = {
            str(item.get("id")): item
            for item in contracts
            if isinstance(item, dict) and item.get("id") and item.get("unit")
        }
        expected_metric_ids = reporting.get("metric_ids", list(contract_by_id))
        if (
            not isinstance(expected_metric_ids, list)
            or len(expected_metric_ids) != len(set(expected_metric_ids))
            or set(expected_metric_ids) != set(contract_by_id)
        ):
            raise ValueError("metric_contract_ids_inconsistent")
        if not isinstance(declared_units, dict):
            declared_units = {}
        if not isinstance(values, dict):
            values = {}
        metric_errors = False
        for metric_id in expected_metric_ids:
            expected_unit = str(contract_by_id[metric_id]["unit"])
            if metric_id not in declared_units:
                errors.append(f"missing_metric_unit:{metric_id}")
                metric_errors = True
            elif str(declared_units[metric_id]) != expected_unit:
                errors.append(f"metric_unit_mismatch:{metric_id}:{declared_units[metric_id]}!={expected_unit}")
                metric_errors = True
            if metric_id not in values or finite_metric_count(values[metric_id]) <= 0:
                errors.append(f"missing_finite_metric_value:{metric_id}")
                metric_errors = True
        undeclared_values = set(values) - set(expected_metric_ids)
        if undeclared_values:
            warnings.append("undeclared_metric_values:" + ",".join(sorted(map(str, undeclared_values))))
        if not metric_errors:
            components["metric_traceability"] = 5.0
    except Exception as exc:
        errors.append(f"invalid_metrics:{exc}")

    decision_path = root / str(reporting.get("decision", "decision.md"))
    if decision_path.is_file():
        text = decision_path.read_text(encoding="utf-8", errors="replace")
        required_terms = reporting.get("decision_required_terms", [])
        matched = sum(1 for term in required_terms if str(term).lower() in text.lower())
        if len(text.strip()) >= 300 and matched == len(required_terms):
            components["decision_trace"] = 5.0
        else:
            errors.append("decision_trace_incomplete")
    else:
        errors.append("missing_decision")

    if task_spec.get("release_status") != "scaffold_only":
        warnings.append("unexpected_release_status_for_preflight_evaluator")
    total = min(SCORE_CAP, sum(components.values()))
    if errors:
        total = min(total, SCORE_CAP - 1.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "score": round(total, 4),
        "score_cap": SCORE_CAP,
        "status": "contract_precheck_only",
        "errors": errors,
        "warnings": warnings,
        "components": {key: round(value, 4) for key, value in components.items()},
        "claim_boundary": (
            "This score checks artifact structure and provenance only. It does not establish "
            "solver correctness, numerical convergence, physical fidelity, experimental agreement, "
            "or engineering optimality."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/home/user/Desktop")
    parser.add_argument("--task-id", default=None)
    args = parser.parse_args()
    result = evaluate(Path(args.root), args.task_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
