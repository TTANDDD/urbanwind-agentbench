#!/usr/bin/env python3
"""Task-074 raw zoned/time-varying heat-flux OpenFOAM evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-074"
CASE_ID = "task-074-case-001"
PRIMARY_ID = "heat_flux_boundary_error"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


def load(path: Path, limit: int = 256 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("missing_or_oversized:" + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("invalid_evidence_path")
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("unsafe_missing_or_oversized_evidence:" + str(value))
    return path


def patch_block(text: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\s*\{", text)
    if not match:
        raise ValueError("missing_patch:" + name)
    start = text.find("{", match.start())
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{": depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0: return text[start + 1:index]
    raise ValueError("unclosed_patch:" + name)


def foam_list(text: str, vector: bool):
    match = re.search(r"\n\s*(\d+)\s*\n\s*\(\s*\n", text); end = text.rfind("\n)")
    if not match or end <= match.end(): raise ValueError("foam_list")
    body = text[match.end():end]
    rows = [tuple(map(float, item)) for item in VECTOR.findall(body)] if vector else [list(map(int, row.split())) for _, row in re.findall(r"(\d+)\(([^)]*)\)", body)]
    if len(rows) != int(match.group(1)): raise ValueError("foam_list_count")
    return rows


def patch_address(text: str, name: str):
    block = patch_block(text, name); start = re.search(r"startFace\s+(\d+)", block); count = re.search(r"nFaces\s+(\d+)", block)
    if not start or not count: raise ValueError("patch_address:" + name)
    return int(start.group(1)), int(count.group(1))


def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def face_geometry(face, points):
    origin = points[face[0]]; vector = [0.0, 0.0, 0.0]
    for index in range(1, len(face)-1):
        left = tuple(points[face[index]][axis]-origin[axis] for axis in range(3)); right = tuple(points[face[index+1]][axis]-origin[axis] for axis in range(3)); tri = cross(left, right)
        for axis in range(3): vector[axis] += 0.5*tri[axis]
    return math.sqrt(sum(value*value for value in vector)), tuple(sum(points[item][axis] for item in face)/len(face) for axis in range(3))


def parse_q_table(text: str, patch: str):
    q = patch_block(patch_block(text, patch), "q")
    values = re.search(r"\bvalues\s*\((.*?)\)\s*;", q, re.S)
    if not values or not re.search(r"\btype\s+table\s*;", q): raise ValueError("q_table:" + patch)
    return [[float(a), float(b)] for a, b in re.findall(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)", values.group(1))]


def geometry_metric(paths: dict, contract: dict):
    points = foam_list(paths["mesh_points"].read_text(encoding="utf-8", errors="replace"), True)
    faces = foam_list(paths["mesh_faces"].read_text(encoding="utf-8", errors="replace"), False)
    boundary = paths["mesh_boundary"].read_text(encoding="utf-8", errors="replace")
    temperature = paths["field_T0"].read_text(encoding="utf-8", errors="replace")
    knots = [float(value) for value in contract["heat_flux"]["knots_s"]]
    total = weighted = 0.0; zones = []
    for zone in contract["geometry"]["zones"]:
        name = zone["patch_name"]; start, count = patch_address(boundary, name); geometry = [face_geometry(face, points) for face in faces[start:start+count]]
        area = sum(item[0] for item in geometry); centroids = [item[1] for item in geometry]; observed = parse_q_table(temperature, name)
        expected = [[time, float(value)] for time, value in zip(knots, contract["heat_flux"]["q_w_m2"][name])]
        centroid_ok = bool(centroids) and all(float(zone["x_min_m"])-1e-9 <= item[0] <= float(zone["x_max_m"])+1e-9 and abs(item[2]) <= 1e-9 for item in centroids)
        area_ok = abs(area-float(zone["expected_area_m2"])) <= 1e-8
        error = sum(abs(a[1]-b[1]) for a,b in zip(observed,expected))/len(expected) if len(observed)==len(expected) and [item[0] for item in observed]==knots else math.inf
        total += area; weighted += area*error
        zones.append({"patch_name":name,"face_count":count,"area_m2":area,"centroid_ok":centroid_ok,"area_ok":area_ok,"q_table":observed,"schedule_mean_abs_error_w_m2":error})
    return {"zones":zones,"total_area_m2":total,PRIMARY_ID:weighted/total}


def probe(path: Path, expected_points, vector: bool):
    text = path.read_text(encoding="utf-8", errors="replace")
    points = [[float(value) for value in NUMBER.findall(match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)", text, re.M)]
    if points != expected_points: raise ValueError("probe_coordinates:" + path.name)
    times, rows = [], []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        time = float(line.split()[0]); values = [list(map(float,item)) for item in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        flat = [item for child in values for item in child] if vector else values
        if len(values)!=len(points) or not all(math.isfinite(value) for value in flat): raise ValueError("probe_shape_or_nonfinite:"+path.name)
        times.append(time); rows.append(values)
    if len(times)!=900 or abs(times[-1]-180.0)>1e-8 or any(b<=a for a,b in zip(times,times[1:])): raise ValueError("probe_time_contract:"+path.name)
    return times, rows


def window(times, velocities, temperatures, start, end):
    exchange=[]; thermal=[]
    for time,row_u,row_t in zip(times,velocities,temperatures):
        if start<=time<=end: exchange.extend(max(float(value[2]),0.0) for value in row_u); thermal.extend(float(value) for value in row_t)
    if not exchange or not thermal: raise ValueError("empty_window")
    return sum(exchange)/len(exchange),sum(thermal)/len(thermal)


def log_gates(paths: dict):
    solver=paths["solver_log"].read_text(encoding="utf-8",errors="replace"); mesh=paths["checkMesh_log"].read_text(encoding="utf-8",errors="replace")
    times=[float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)",solver,re.M)]; courant=[float(value) for value in re.findall(r"Courant Number mean: [-+0-9.eE]+ max: ([-+0-9.eE]+)",solver)]; continuity=[float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)",solver)]; cells=[int(value) for value in re.findall(r"cells:\s+(\d+)",mesh)]
    gates={"mesh":"Mesh OK." in mesh and cells[-1:]==[141952],"final_time":bool(times) and abs(times[-1]-180.0)<=1e-8,"finite":"FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b",solver,re.I),"courant":bool(courant) and max(courant)<=1.0,"continuity":bool(continuity) and max(map(abs,continuity))<=1e-4}
    return {"gates":gates,"max_courant":max(courant) if courant else None,"max_abs_cumulative_continuity":max(map(abs,continuity)) if continuity else None}


def finite_array(value,length): return isinstance(value,list) and len(value)==length and all(isinstance(item,(int,float)) and not isinstance(item,bool) and math.isfinite(float(item)) for item in value)
def finite_matrix(value,rows,cols): return isinstance(value,list) and len(value)==rows and all(finite_array(row,cols) for row in value)
def finite_cube(value,rows,points): return isinstance(value,list) and len(value)==rows and all(isinstance(row,list) and len(row)==points and all(finite_array(vector,3) for vector in row) for row in value)
def flatten(value): return [item for child in value for item in flatten(child)] if isinstance(value,list) else [float(value)]
def nmae(left,right,scale):
    a,b=flatten(left),flatten(right)
    if len(a)!=len(b) or not a: raise ValueError("nmae_shape")
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)/max(scale,1e-30)


def evaluate_task(root: Path):
    root=root.resolve(); base=precheck(root,TASK_ID); errors=list(base.get("errors",[])); raw_ok=report_ok=metric_ok=hidden_ok=False; recomputed={}; hidden_delta=math.inf
    try:
        contract=load(root/"local_assets/heat_flux_boundary_contract.json"); hidden=load(Path(os.environ.get("URBANWIND_REFERENCE",f"/tmp/{TASK_ID}-reference.json")))
        evidence=load(root/"heat_flux_evidence_manifest.json"); reported=load(root/"heat_flux_results.json"); metrics=load(root/"metrics.json")
        if any(item.get("task_id")!=TASK_ID for item in (contract,hidden,evidence,reported,metrics)): raise ValueError("task_id_mismatch")
        case=evidence.get("case"); submitted=reported.get("case_result")
        if not isinstance(case,dict) or case.get("case_id")!=CASE_ID or not isinstance(submitted,dict) or submitted.get("case_id")!=CASE_ID: raise ValueError("case_id_mismatch")
        used=set(); paths={}
        for key in contract["required_raw_evidence_per_case"]:
            item=case.get(key)
            if not isinstance(item,dict): raise ValueError("evidence_item:"+key)
            path=evidence_path(root,item.get("path"))
            if path in used or item.get("sha256")!=sha256(path): raise ValueError("evidence_hash_or_duplicate:"+key)
            used.add(path); paths[key]=path
        with (root/"local_assets/thermal_response_probes.csv").open(encoding="utf-8",newline="") as handle: probe_rows=list(csv.DictReader(handle))
        points=[[float(row[key]) for key in ("x_m","y_m","z_m")] for row in probe_rows]
        times,velocities=probe(paths["probe_U"],points,True); ttimes,temperatures=probe(paths["probe_T"],points,False)
        first=window(times,velocities,temperatures,60,120); second=window(times,velocities,temperatures,120,180); geometry=geometry_metric(paths,contract); logs=log_gates(paths)
        control=paths["controlDict"].read_text(encoding="utf-8",errors="replace"); turbulence=paths["turbulenceProperties"].read_text(encoding="utf-8",errors="replace")
        raw_gates={**logs["gates"],"times_aligned":times==ttimes,"temperature":min(value for row in temperatures for value in row)>=250 and max(value for row in temperatures for value in row)<=350,
                   "stationarity_exchange":abs(second[0]-first[0])<=0.05,"stationarity_temperature":abs(second[1]-first[1])<=0.5,
                   "geometry":len(geometry["zones"])==3 and all(item["area_ok"] and item["centroid_ok"] for item in geometry["zones"]) and abs(geometry["total_area_m2"]-304)<=1e-8,
                   "schedule":geometry[PRIMARY_ID]<=1e-10,"control":bool(re.search(r"\bendTime\s+180(?:\.0+)?\s*;",control)) and bool(re.search(r"\bdeltaT\s+0\.015\s*;",control)) and "timeStart 60;" in control,
                   "wale":"simulationType LES;" in turbulence and "LESModel WALE;" in turbulence,
                   "mean_fields":all(paths[name].stat().st_size>0 for name in ("mean_UMean","mean_UPrime2Mean","mean_TMean","mean_TPrime2Mean"))}
        raw_ok=all(raw_gates.values())
        if not raw_ok: errors.append("raw_case_gate_failed")
        recomputed={"case_id":CASE_ID,"time_s":times,"probe_U_m_s":velocities,"probe_T_k":temperatures,"geometry_and_metric":geometry,
                    "half_window_means":{"60_120":{"vertical_exchange_m_s":first[0],"temperature_k":first[1]},"120_180":{"vertical_exchange_m_s":second[0],"temperature_k":second[1]}},
                    "vertical_exchange_drift_m_s":abs(second[0]-first[0]),"temperature_drift_k":abs(second[1]-first[1]),"max_courant":logs["max_courant"],"max_abs_cumulative_continuity":logs["max_abs_cumulative_continuity"],"all_run_gates_pass":raw_ok}
        scalars=("vertical_exchange_drift_m_s","temperature_drift_k","max_courant","max_abs_cumulative_continuity")
        report_ok=finite_array(submitted.get("time_s"),900) and finite_cube(submitted.get("probe_U_m_s"),900,24) and finite_matrix(submitted.get("probe_T_k"),900,24) and submitted.get("all_run_gates_pass") is True
        if report_ok:
            report_ok=max(nmae(submitted["time_s"],times,180),nmae(submitted["probe_U_m_s"],velocities,1.2),nmae(submitted["probe_T_k"],temperatures,300))<=1e-8 and all(abs(float(submitted[key])-float(recomputed[key]))<=max(1e-9,abs(float(recomputed[key]))*1e-7) for key in scalars)
        if not report_ok: errors.append("reported_arrays_not_recomputed")
        primary=geometry[PRIMARY_ID]; value=metrics.get("values",{}).get(PRIMARY_ID); metric_ok=isinstance(value,(int,float)) and not isinstance(value,bool) and abs(float(value)-primary)<=1e-12
        if not metric_ok: errors.append("reported_metric_not_recomputed:"+PRIMARY_ID)
        reference=hidden["case_results"][0]
        hidden_delta=max(nmae(times,reference["time_s"],180),nmae(velocities,reference["probe_U_m_s"],1.2),nmae(temperatures,reference["probe_T_k"],300),abs(primary-reference["geometry_and_metric"][PRIMARY_ID]))
        hidden_ok=hidden_delta<=1e-8
        if not hidden_ok: errors.append("hidden_numerical_fidelity")
    except Exception as exc: errors.append("heat_flux_reference_check:"+str(exc))
    components={"raw_openfoam_evidence":15.0 if raw_ok and not base.get("errors") else 0.0,"reported_array_recomputation":10.0 if report_ok and not base.get("errors") else 0.0,"primary_metric_recomputation":5.0 if metric_ok and not base.get("errors") else 0.0,"hidden_numerical_fidelity":10.0 if hidden_ok and not base.get("errors") else 0.0}
    return {"task_id":TASK_ID,"score":round(min(100.0,float(base.get("score",0.0))+sum(components.values())),4),"score_cap":100.0,"status":"openfoam_zoned_heat_flux_numerical_reference_check","errors":errors,"components":components,"diagnostics":{"recomputed_case":recomputed,"hidden_max_scaled_delta":hidden_delta},"precheck":base,"claim_boundary":"Checks local synthetic zoned heat-flux configuration and raw OpenFOAM v2412 numerical evidence; not real-city or experimental/PIV/DNS/wind-tunnel/field truth."}


def main():
    root=Path(os.environ.get("URBANWIND_EVAL_ROOT","/home/user/Desktop")); print(json.dumps(evaluate_task(root),ensure_ascii=False,sort_keys=True))


if __name__=="__main__": main()
