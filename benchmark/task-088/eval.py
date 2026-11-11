#!/usr/bin/env python3
"""Task-088 raw probe/plane sampling evaluator."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-088"
PRIMARY_ID = "sampling_location_coverage"
PRODUCT_IDS = ("probes_U", "probes_p", "pedestrianPlane_U", "pedestrianPlane_p",
               "wakePlane_U", "wakePlane_p")
TIMES = [60.0, 120.0, 180.0, 240.0, 300.0, 360.0, 420.0]
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def load(path: Path):
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def resolve_evidence(root: Path, relative) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + relative)
    return path


def probe_rows(path: Path, field: str, contract: dict) -> tuple[list[float], int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    header_points = []
    for match in re.finditer(r"^# Probe \d+ \(([^)]+)\)", text, re.M):
        values = [float(value) for value in NUMBER.findall(match.group(1))]
        if len(values) == 3:
            header_points.append(values)
    if header_points != contract["probe_points_m"]:
        raise ValueError("probe_coordinates_mismatch:" + field)
    width = 193 if field == "U" else 65
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(value) for value in NUMBER.findall(line)]
        if len(values) != width or not all(math.isfinite(value) for value in values):
            raise ValueError("probe_row_invalid:" + field)
        if contract["probe_window_s"][0] < values[0] <= contract["probe_window_s"][1] + 1e-8:
            rows.append(values)
    if len(rows) < int(contract["minimum_probe_samples"]):
        raise ValueError("probe_samples_insufficient:" + field)
    columns = width - 1
    return [sum(row[index] for row in rows) / len(rows) for index in range(1, width)], len(rows)


def plane_rows(path: Path, field: str, plane: str, contract: dict) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    width = 6 if field == "U" else 4
    expected_header = "# x y z  U_x U_y U_z" if field == "U" else "# x y z  p"
    if expected_header not in text:
        raise ValueError("plane_header_mismatch:" + plane + ":" + field)
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [float(value) for value in NUMBER.findall(line)]
        if len(values) != width or not all(math.isfinite(value) for value in values):
            raise ValueError("plane_row_invalid:" + plane + ":" + field)
        rows.append(values)
    requirement = contract["planes"][plane]
    if len(rows) < int(requirement["minimum_rows"]):
        raise ValueError("plane_rows_insufficient:" + plane + ":" + field)
    axis, fixed = (2, float(requirement["z_m"])) if "z_m" in requirement else (0, float(requirement["x_m"]))
    if any(abs(row[axis] - fixed) > 1e-9 for row in rows):
        raise ValueError("plane_geometry_mismatch:" + plane + ":" + field)
    value_count = 3 if field == "U" else 1
    means = [sum(row[3 + index] for row in rows) / len(rows) for index in range(value_count)]
    coords = [row[:3] for row in rows]
    coordinate_hash = hashlib.sha256(json.dumps(coords, separators=(",", ":")).encode()).hexdigest()
    return {"row_count": len(rows), "field_mean": means}, coordinate_hash


def normalized_delta(values, references, scale: float) -> float:
    return sum(abs(float(a) - float(b)) for a, b in zip(values, references)) / len(references) / scale


def evaluate_task(root: Path):
    root = root.resolve()
    base = precheck(root, TASK_ID)
    errors = list(base.get("errors", []))
    verified = []
    signatures = {}
    fidelity = []
    metric_ok = False
    try:
        contract = load(root / "local_assets" / "sampling_contract.json")
        hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        manifest = load(root / "sampling_manifest.json")
        if contract.get("task_id") != TASK_ID or hidden.get("task_id") != TASK_ID \
                or manifest.get("task_id") != TASK_ID:
            raise ValueError("task_id_mismatch")
        products = manifest.get("products")
        if not isinstance(products, list):
            raise ValueError("products_not_list")
        by_id = {item.get("product_id"): item for item in products if isinstance(item, dict)}
        if len(by_id) != len(products) or set(by_id) != set(PRODUCT_IDS):
            raise ValueError("sampling_product_ids_mismatch")
        used_paths = set()
        hidden_by_id = {item["product_id"]: item for item in hidden["products"]}
        plane_coordinate_hashes = {}
        for product_id in PRODUCT_IDS:
            item = by_id[product_id]
            if product_id.startswith("probes_"):
                field = product_id.rsplit("_", 1)[1]
                if item.get("kind") != "probes" or item.get("field") != field:
                    raise ValueError("probe_product_metadata:" + product_id)
                path = resolve_evidence(root, item.get("path"))
                if item.get("sha256") != sha256(path) or path in used_paths:
                    raise ValueError("probe_hash_or_duplicate:" + product_id)
                used_paths.add(path)
                means, samples = probe_rows(path, field, contract)
                signatures[product_id] = {"sample_count": samples, "field_mean_flat": means}
                hidden_item = hidden_by_id[product_id]
                fidelity.append(normalized_delta(means, hidden_item["field_mean_flat"],
                                                 1.2 if field == "U" else 1.44))
            else:
                plane, field = product_id.rsplit("_", 1)
                if item.get("kind") != "plane" or item.get("plane") != plane or item.get("field") != field:
                    raise ValueError("plane_product_metadata:" + product_id)
                outputs = item.get("outputs")
                if not isinstance(outputs, list) or len(outputs) != len(TIMES) \
                        or [float(output.get("time_s", -1)) for output in outputs] != TIMES:
                    raise ValueError("plane_times_mismatch:" + product_id)
                computed = []
                coordinate_hashes = []
                for output in outputs:
                    path = resolve_evidence(root, output.get("path"))
                    if output.get("sha256") != sha256(path) or path in used_paths:
                        raise ValueError("plane_hash_or_duplicate:" + product_id)
                    used_paths.add(path)
                    signature, coordinate_hash = plane_rows(path, field, plane, contract)
                    computed.append({"time_s": float(output["time_s"]), **signature})
                    coordinate_hashes.append(coordinate_hash)
                if len(set(coordinate_hashes)) != 1:
                    raise ValueError("plane_coordinates_change_over_time:" + product_id)
                plane_coordinate_hashes[(plane, field)] = coordinate_hashes[0]
                signatures[product_id] = {"outputs": computed}
                hidden_outputs = hidden_by_id[product_id]["outputs"]
                for actual, reference in zip(computed, hidden_outputs):
                    fidelity.append(normalized_delta(actual["field_mean"], reference["field_mean"],
                                                     1.2 if field == "U" else 1.44))
            verified.append(product_id)
        for plane in ("pedestrianPlane", "wakePlane"):
            if plane_coordinate_hashes[(plane, "U")] != plane_coordinate_hashes[(plane, "p")]:
                raise ValueError("plane_field_coordinates_mismatch:" + plane)
        coverage = len(verified) / len(PRODUCT_IDS)
        reported = load(root / "metrics.json").get("values", {}).get(PRIMARY_ID)
        if not isinstance(reported, (int, float)) or isinstance(reported, bool) or not math.isfinite(float(reported)) \
                or abs(float(reported) - coverage) > 1e-8:
            errors.append("reported_metric_not_recomputed:" + PRIMARY_ID)
        else:
            metric_ok = True
    except Exception as exc:
        errors.append("sampling_reference_check:" + str(exc))
        coverage = len(verified) / len(PRODUCT_IDS)

    components = {"sampling_schema_and_raw_evidence": 0.0, "metric_recomputation": 0.0,
                  "hidden_sampling_fidelity": 0.0}
    if not base.get("errors"):
        components["sampling_schema_and_raw_evidence"] = 12.0 * coverage
        if metric_ok:
            components["metric_recomputation"] = 8.0
        if fidelity:
            average = sum(fidelity) / len(fidelity)
            components["hidden_sampling_fidelity"] = 20.0 * max(0.0, 1.0 - average / 0.15)
    return {
        "task_id": TASK_ID, "score": round(min(100.0, float(base.get("score", 0.0)) + sum(components.values())), 4),
        "score_cap": 100.0, "status": "openfoam_numerical_reference_check",
        "errors": errors, "components": components,
        "diagnostics": {"verified_products": verified, "sampling_location_coverage": coverage,
                        "signatures": signatures, "hidden_signature_deltas": fidelity},
        "precheck": base,
        "claim_boundary": "Checks raw OpenFOAM probe/plane I/O and hidden numerical signatures; not PIV/DNS, experimental truth or release-runtime reproducibility.",
    }


def main() -> None:
    root = Path(os.environ.get("URBANWIND_EVAL_ROOT", "/home/user/Desktop"))
    print(json.dumps(evaluate_task(root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
