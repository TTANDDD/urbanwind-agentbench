#!/usr/bin/env python3
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from contract_evaluator import evaluate as precheck

TASK_ID = "urbanwind-task-041"
PRIMARY_ID = "block_connectivity_error_count"
REQUIRED_PATCHES = {"inlet", "outlet", "sideLow", "sideHigh", "ground", "top", "building"}


def main():
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    diagnostics = {}
    try:
        metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
        value = float(metrics["values"][PRIMARY_ID])
        if not math.isfinite(value) or value != 0.0:
            errors.append("reported_block_connectivity_error_count_must_be_zero")
        if metrics.get("units", {}).get(PRIMARY_ID) != "errors":
            errors.append("primary_metric_unit_mismatch")
        result = json.loads((root / "mesh_metrics.json").read_text(encoding="utf-8"))
        primary = result.get("primary_metric", {})
        if primary.get("id") != PRIMARY_ID or primary.get("unit") != "errors" or float(primary.get("value")) != 0.0:
            errors.append("mesh_metrics_primary_contract_mismatch")
    except Exception as exc:
        errors.append(f"metric_recompute_input_error:{exc}")

    block_mesh = os.environ.get("URBANWIND_BLOCKMESH") or shutil.which("blockMesh")
    check_mesh = os.environ.get("URBANWIND_CHECKMESH") or shutil.which("checkMesh")
    case_dir = root / "openfoam_case"
    if not block_mesh or not check_mesh:
        errors.append("openfoam_mesh_tools_unavailable")
    elif not case_dir.is_dir():
        errors.append("openfoam_case_missing")
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="urbanwind-task041-") as raw:
                temp_case = Path(raw) / "case"
                shutil.copytree(case_dir, temp_case)
                block = subprocess.run([block_mesh, "-case", str(temp_case)], capture_output=True, text=True, timeout=120, check=False)
                check = subprocess.run([check_mesh, "-case", str(temp_case)], capture_output=True, text=True, timeout=120, check=False)
                combined = check.stdout + "\n" + check.stderr
                diagnostics = {
                    "blockMesh_exit_code": block.returncode,
                    "checkMesh_exit_code": check.returncode,
                    "mesh_ok": "Mesh OK." in combined,
                }
                if block.returncode != 0:
                    errors.append("blockMesh_reexecution_failed")
                if check.returncode != 0 or "Mesh OK." not in combined:
                    errors.append("checkMesh_reexecution_failed")
                boundary_text = (temp_case / "constant" / "polyMesh" / "boundary").read_text(encoding="utf-8", errors="replace")
                missing = sorted(name for name in REQUIRED_PATCHES if name not in boundary_text)
                if missing:
                    errors.append("required_boundary_patches_missing:" + ",".join(missing))
        except Exception as exc:
            errors.append(f"openfoam_reexecution_error:{exc}")

    bonus = 40.0 if not errors else 0.0
    score = min(100.0, float(base.get("score", 0.0)) + bonus)
    print(json.dumps({
        "task_id": TASK_ID,
        "score": score,
        "score_cap": 100.0,
        "status": "openfoam_mesh_reexecution_check",
        "errors": errors,
        "diagnostics": diagnostics,
        "precheck": base,
        "claim_boundary": "Re-executes blockMesh and checkMesh and verifies the required patches; it does not assert experimental flow validity."
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
