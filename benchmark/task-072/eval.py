#!/usr/bin/env python3
"""Task-072 raw local-GIS/OpenFOAM roughness-mapping evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-072"; CASE_ID = "task-072-case-001"; PRIMARY_ID = "roughness_zone_mapping_accuracy"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


def load(path: Path, limit: int = 192 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit: raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""): value.update(block)
    return value.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute(): raise ValueError("invalid_evidence_path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + str(value))
    return path


def foam_list(text: str, vector: bool):
    match = re.search(r"\n\s*(\d+)\s*\n\s*\(\s*\n", text); end = text.rfind("\n)")
    if not match or end <= match.end(): raise ValueError("foam_list")
    body = text[match.end():end]
    rows = [tuple(map(float, row)) for row in VECTOR.findall(body)] if vector else [list(map(int, row.split())) for _, row in re.findall(r"(\d+)\(([^)]*)\)", body)]
    if len(rows) != int(match.group(1)): raise ValueError("foam_list_count")
    return rows


def patch_address(text: str, name: str):
    patch = re.search(r"\b" + re.escape(name) + r"\s*\{(.*?)\}", text, re.S)
    if not patch: raise ValueError("missing_patch:" + name)
    start = re.search(r"startFace\s+(\d+)", patch.group(1)); count = re.search(r"nFaces\s+(\d+)", patch.group(1))
    if not start or not count: raise ValueError("patch_address:" + name)
    return int(start.group(1)), int(count.group(1))


def patch_z0(text: str, name: str):
    patch = re.search(r"\b" + re.escape(name) + r"\s*\{(.*?)\}", text, re.S)
    value = re.search(r"\bz0\s+uniform\s+([-+0-9.eE]+)\s*;", patch.group(1) if patch else "")
    if not value: raise ValueError("missing_z0:" + name)
    return float(value.group(1))


def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def face_geometry(face, points):
    origin = points[face[0]]; area_vector = [0.0, 0.0, 0.0]
    for index in range(1, len(face)-1):
        left = tuple(points[face[index]][axis]-origin[axis] for axis in range(3)); right = tuple(points[face[index+1]][axis]-origin[axis] for axis in range(3))
        triangle = cross(left, right)
        for axis in range(3): area_vector[axis] += 0.5*triangle[axis]
    return math.sqrt(sum(value*value for value in area_vector)), tuple(sum(points[item][axis] for item in face)/len(face) for axis in range(3))


def point_in_zone(x, y, zone, tol):
    if zone == "west_low": return -4-tol <= x <= -0.5+tol and -6.2-tol <= y <= 6.2+tol
    if zone == "center_side": return -0.5-tol <= x <= 0.5+tol and -6.2-tol <= y <= 6.2+tol and abs(y) >= 0.5-tol
    if zone == "east_high": return 0.5-tol <= x <= 23+tol and -6.2-tol <= y <= 6.2+tol
    raise ValueError("unknown_zone:" + zone)


def mapping(paths, contract):
    points = foam_list(paths["mesh_points"].read_text(encoding="utf-8", errors="replace"), True)
    faces = foam_list(paths["mesh_faces"].read_text(encoding="utf-8", errors="replace"), False)
    boundary = paths["mesh_boundary"].read_text(encoding="utf-8", errors="replace"); nut = paths["field_nut"].read_text(encoding="utf-8", errors="replace")
    gates = contract["frozen_gates"]; zones = []; total = correct = 0.0
    for source in contract["zones"]:
        start, count = patch_address(boundary, source["patch_name"]); geometry = [face_geometry(face, points) for face in faces[start:start+count]]
        area = sum(item[0] for item in geometry); centroids = [item[1] for item in geometry]; z0 = patch_z0(nut, source["patch_name"])
        geometry_ok = bool(centroids) and all(point_in_zone(value[0], value[1], source["zone_id"], gates["centroid_tolerance_m"]) and abs(value[2]) <= gates["centroid_tolerance_m"] for value in centroids)
        area_ok = abs(area-source["expected_area_m2"]) <= gates["area_abs_tolerance_m2"]; z0_ok = abs(z0-source["z0_m"]) <= gates["z0_abs_tolerance_m"]
        accepted = geometry_ok and area_ok and z0_ok; total += area; correct += area if accepted else 0.0
        zones.append({"zone_id": source["zone_id"], "patch_name": source["patch_name"], "face_count": count, "area_m2": area, "mapped_z0_m": z0,
                      "geometry_ok": geometry_ok, "area_ok": area_ok, "z0_ok": z0_ok, "correct": accepted})
    return {"zones": zones, "correct_area_m2": correct, "total_ground_area_m2": total, PRIMARY_ID: correct/total}


def probe(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if points != expected_points: raise ValueError("probe_coordinates_mismatch:" + path.name)
    times, rows = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        if vector: values = [list(map(float, row)) for row in VECTOR.findall(line)]; head = line.split("(", 1)[0]
        else: numbers = [float(value) for value in NUMBER.findall(line)]; values = numbers[1:]; head = line
        time_values = NUMBER.findall(head)
        if not time_values: continue
        flat = [item for row in values for item in row] if vector else values
        if len(values) != len(expected_points) or not all(math.isfinite(value) for value in flat): raise ValueError("probe_shape_or_nonfinite:" + path.name)
        times.append(float(time_values[0])); rows.append(values)
    if times != [float(value) for value in range(20, 401, 20)]: raise ValueError("probe_time_contract:" + path.name)
    return times, rows


def weighted_rmse(values, targets, weights): return math.sqrt(sum(w*(a-b)**2 for a, b, w in zip(values, targets, weights))/sum(weights))


def log_gates(solver_path: Path, mesh_path: Path):
    solver = solver_path.read_text(encoding="utf-8", errors="replace"); mesh = mesh_path.read_text(encoding="utf-8", errors="replace")
    iterations = [float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)", solver, re.M)]; continuity = [float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)", solver)]
    residuals = {}
    for field in ("Ux", "Uy", "Uz", "k", "omega", "p"):
        values = [float(value) for value in re.findall(r"Solving for " + field + r", Initial residual = ([-+0-9.eE]+)", solver)]; residuals[field] = max(values[-20:]) if len(values) >= 20 else None
    cells = re.findall(r"^\s*cells:\s+(\d+)\s*$", mesh, re.M)
    gates = {"mesh": "Mesh OK." in mesh and cells[-1:] == ["365488"], "iteration": len(iterations)==400 and iterations[-1:]==[400.0],
             "model": "Selecting RAS turbulence model kOmegaSST" in solver, "finite": "FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b", solver, re.I),
             "continuity": bool(continuity) and max(map(abs, continuity)) <= 0.01,
             "residuals": all(value is not None and math.isfinite(value) and value <= 1e-4 for value in residuals.values()), "no_iteration_cap": "No Iterations 1000" not in solver}
    return {"gates": gates, "max_abs_cumulative_continuity": max(map(abs, continuity)) if continuity else None, "last20_max_initial_residuals": residuals}


def finite_array(value, length): return isinstance(value, list) and len(value)==length and all(isinstance(item,(int,float)) and not isinstance(item,bool) and math.isfinite(float(item)) for item in value)
def finite_matrix(value, rows, columns): return isinstance(value,list) and len(value)==rows and all(finite_array(row,columns) for row in value)
def finite_cube(value, rows, points): return isinstance(value,list) and len(value)==rows and all(isinstance(row,list) and len(row)==points and all(finite_array(vector,3) for vector in row) for row in value)
def flatten(value): return [item for child in value for item in flatten(child)] if isinstance(value,list) else [float(value)]
def nmae(left,right,scale):
    a,b=flatten(left),flatten(right)
    if len(a)!=len(b) or not a: raise ValueError("nmae_shape")
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)/max(scale,1e-30)


def evaluate_task(root: Path):
    root = root.resolve(); base = precheck(root, TASK_ID); errors = list(base.get("errors", [])); raw_ok = report_ok = metric_ok = hidden_ok = False; recomputed = {}
    try:
        contract = load(root / "local_assets/roughness_zone_mapping_contract.json"); hidden = load(Path(os.environ.get("URBANWIND_REFERENCE", f"/tmp/{TASK_ID}-reference.json")))
        evidence = load(root / "roughness_mapping_evidence_manifest.json"); reported = load(root / "roughness_mapping_results.json"); metrics = load(root / "metrics.json")
        if any(item.get("task_id") != TASK_ID for item in (hidden, evidence, reported)): raise ValueError("task_id_mismatch")
        evidence_case = evidence.get("case"); submitted = reported.get("case_result")
        if not isinstance(evidence_case,dict) or evidence_case.get("case_id")!=CASE_ID or not isinstance(submitted,dict) or submitted.get("case_id")!=CASE_ID: raise ValueError("case_id_mismatch")
        used=set(); paths={}
        for key in contract["required_raw_evidence_per_case"]:
            item=evidence_case.get(key)
            if not isinstance(item,dict): raise ValueError("evidence_item:"+key)
            path=evidence_path(root,item.get("path"))
            if path in used or item.get("sha256")!=sha256(path): raise ValueError("evidence_hash_or_duplicate:"+key)
            used.add(path); paths[key]=path
        source=load(paths["source_zones"])
        if len(source.get("features",[]))!=3: raise ValueError("geojson_feature_count")
        db=sqlite3.connect(paths["gis_output"])
        try:
            table=db.execute("select table_name from gpkg_contents where data_type='features'").fetchone()[0]
            gpkg_count=db.execute('select count(*) from "'+table.replace('"','""')+'"').fetchone()[0]
        finally: db.close()
        with (root/"local_assets/roughness_profile_targets.csv").open(encoding="utf-8",newline="") as handle: profiles=list(csv.DictReader(handle))
        with (root/"local_assets/roughness_response_probes.csv").open(encoding="utf-8",newline="") as handle: probes=list(csv.DictReader(handle))
        inlet_points=[[-4.0,0.0625,float(row["z_m"])] for row in profiles]; response_points=contract["response_points_m"]
        times,inlet_u=probe(paths["probe_U"],inlet_points,True); times_k,inlet_k=probe(paths["probe_k"],inlet_points,False); times_o,inlet_o=probe(paths["probe_omega"],inlet_points,False); times_r,response_u=probe(paths["response_U"],response_points,True)
        weights=[float(row["layer_thickness_m"]) for row in profiles]
        profile_metrics={"inlet_U_rmse_m_s":weighted_rmse([value[0] for value in inlet_u[-1]],[float(row["target_Ux_m_s"]) for row in profiles],weights),
                         "inlet_k_rmse_m2_s2":weighted_rmse(inlet_k[-1],[float(row["target_k_m2_s2"]) for row in profiles],weights),
                         "inlet_omega_rmse_s_1":weighted_rmse(inlet_o[-1],[float(row["target_omega_s_1"]) for row in profiles],weights)}
        mapped=mapping(paths,contract); logs=log_gates(paths["solver_log"],paths["checkMesh_log"]); control=paths["controlDict"].read_text(encoding="utf-8",errors="replace")
        group_means={}
        for group in sorted({row["group"] for row in probes}):
            indices=[index for index,row in enumerate(probes) if row["group"]==group]; group_means[group]=sum(response_u[-1][index][0] for index in indices)/len(indices)
        raw_gates={**logs["gates"],"geojson":len(source["features"])==3,"gpkg":gpkg_count==3,"time_alignment":times==times_k==times_o==times_r,
                   "mapping":mapped[PRIMARY_ID]==1.0 and abs(mapped["total_ground_area_m2"]-333.8)<=1e-8 and all(item["correct"] for item in mapped["zones"]),
                   "profile_U":profile_metrics["inlet_U_rmse_m_s"]<=1e-6,"profile_k":profile_metrics["inlet_k_rmse_m2_s2"]<=1e-6,"profile_omega":profile_metrics["inlet_omega_rmse_s_1"]<=1e-5,
                   "response":all(-5<=value<=5 for row in response_u for vector in row for value in vector) and max(group_means.values())-min(group_means.values())>1e-6,
                   "control":bool(re.search(r"\bendTime\s+400\s*;",control)) and "inletProfile" in control and "responseProbes" in control and "writeInterval 20;" in control}
        raw_ok=all(raw_gates.values());
        if not raw_ok: errors.append("raw_case_gate_failed")
        recomputed={"case_id":CASE_ID,"iterations":times,"inlet_U_m_s":inlet_u,"inlet_k_m2_s2":inlet_k,"inlet_omega_s_1":inlet_o,"response_U_m_s":response_u,
                    "profile_metrics":profile_metrics,"response_group_mean_Ux_m_s":group_means,"mapping":mapped,"max_abs_cumulative_continuity":logs["max_abs_cumulative_continuity"],
                    "last20_max_initial_residuals":logs["last20_max_initial_residuals"],"all_run_gates_pass":raw_ok}
        report_ok=(finite_array(submitted.get("iterations"),20) and finite_cube(submitted.get("inlet_U_m_s"),20,52) and finite_matrix(submitted.get("inlet_k_m2_s2"),20,52)
                   and finite_matrix(submitted.get("inlet_omega_s_1"),20,52) and finite_cube(submitted.get("response_U_m_s"),20,12) and submitted.get("all_run_gates_pass") is True
                   and max(nmae(submitted["iterations"],times,400),nmae(submitted["inlet_U_m_s"],inlet_u,1.2),nmae(submitted["inlet_k_m2_s2"],inlet_k,0.03),nmae(submitted["inlet_omega_s_1"],inlet_o,10),nmae(submitted["response_U_m_s"],response_u,1.2))<=1e-8)
        if not report_ok: errors.append("reported_arrays_not_recomputed")
        reported_primary=metrics.get("values",{}).get(PRIMARY_ID); metric_ok=isinstance(reported_primary,(int,float)) and not isinstance(reported_primary,bool) and abs(float(reported_primary)-mapped[PRIMARY_ID])<=1e-12
        if not metric_ok: errors.append("reported_metric_not_recomputed:"+PRIMARY_ID)
        reference=hidden["case_results"][0]
        hidden_delta=max(nmae(times,reference["iterations"],400),nmae(inlet_u,reference["inlet_U_m_s"],1.2),nmae(inlet_k,reference["inlet_k_m2_s2"],0.03),nmae(inlet_o,reference["inlet_omega_s_1"],10),nmae(response_u,reference["response_U_m_s"],1.2),abs(mapped[PRIMARY_ID]-reference["mapping"][PRIMARY_ID]))
        hidden_ok=hidden_delta<=1e-8
        if not hidden_ok: errors.append("hidden_numerical_fidelity")
    except Exception as exc: errors.append("roughness_mapping_reference_check:"+str(exc)); hidden_delta=math.inf
    components={"raw_gis_openfoam_evidence":15.0 if raw_ok and not base.get("errors") else 0.0,"reported_array_recomputation":10.0 if report_ok and not base.get("errors") else 0.0,
                "primary_metric_recomputation":5.0 if metric_ok and not base.get("errors") else 0.0,"hidden_numerical_fidelity":10.0 if hidden_ok and not base.get("errors") else 0.0}
    return {"task_id":TASK_ID,"score":round(min(100.0,float(base.get("score",0.0))+sum(components.values())),4),"score_cap":100.0,
            "status":"openfoam_roughness_mapping_numerical_reference_check","errors":errors,"components":components,
            "diagnostics":{"recomputed_case":recomputed,"hidden_max_scaled_delta":hidden_delta},"precheck":base,
            "claim_boundary":"Checks local synthetic GIS conversion, raw OpenFOAM v2412 mapping and numerical probe series; not real-city or experimental/PIV/DNS/wind-tunnel/field truth."}


def main():
    root=Path(os.environ.get("URBANWIND_EVAL_ROOT","/home/user/Desktop")); print(json.dumps(evaluate_task(root),ensure_ascii=False,sort_keys=True))


if __name__=="__main__": main()
