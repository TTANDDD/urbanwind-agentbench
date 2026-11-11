#!/usr/bin/env python3
"""Task-075 raw surface-temperature/OpenFOAM numerical evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-075"
CASE_ID = "task-075-case-001"
PRIMARY_ID = "surface_temperature_alignment_rmse_K"
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


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def patch_block(text: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\s*\{", text)
    if not match:
        raise ValueError("missing_patch:" + name)
    start = text.find("{", match.start()); depth = 0
    for index in range(start, len(text)):
        if text[index] == "{": depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0: return text[start+1:index]
    raise ValueError("unclosed_patch:" + name)


def parse_table(path: Path):
    block = patch_block(path.read_text(encoding="utf-8", errors="replace"), "building")
    match = re.search(r"\buniformValue\s+table\s*\((.*?)\)\s*;", block, re.S)
    if not re.search(r"\btype\s+uniformFixedValue\s*;", block) or not match:
        raise ValueError("building_temperature_table")
    return [[float(a), float(b)] for a,b in re.findall(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)", match.group(1))]


def surface(path: Path):
    times=[]; values=[]
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        found=[float(value) for value in NUMBER.findall(line)]
        if len(found)>=2: times.append(found[0]); values.append(found[-1])
    if len(times)!=900 or abs(times[-1]-180.0)>1e-8 or any(b<=a for a,b in zip(times,times[1:])) or not all(math.isfinite(v) for v in values):
        raise ValueError("surface_series_contract")
    return times,values


def probe(path: Path, expected_points, vector: bool):
    text=path.read_text(encoding="utf-8",errors="replace")
    points=[[float(value) for value in NUMBER.findall(match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)",text,re.M)]
    if points!=expected_points: raise ValueError("probe_coordinates:"+path.name)
    times=[]; rows=[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        time=float(line.split()[0]); values=[list(map(float,item)) for item in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        flat=[item for child in values for item in child] if vector else values
        if len(values)!=len(points) or not all(math.isfinite(value) for value in flat): raise ValueError("probe_shape_or_nonfinite:"+path.name)
        times.append(time); rows.append(values)
    if len(times)!=900 or abs(times[-1]-180.0)>1e-8 or any(b<=a for a,b in zip(times,times[1:])): raise ValueError("probe_time_contract:"+path.name)
    return times,rows


def interpolate(pairs,time):
    for left,right in zip(pairs,pairs[1:]):
        if left[0]-1e-9<=time<=right[0]+1e-9: return left[1]+(time-left[0])/(right[0]-left[0])*(right[1]-left[1])
    if abs(time-pairs[-1][0])<=1e-9: return pairs[-1][1]
    raise ValueError("surface_time_outside_table")


def source_contract(paths):
    raw=csv_rows(paths["source_csv"]); aligned=csv_rows(paths["aligned_csv"])
    pairs=[[float(row["elapsed_s"]),float(row["aligned_temperature_k"])] for row in aligned]
    expected=[[0.0,302.0],[19.0,303.4],[47.0,304.625],[83.0,306.2],[131.0,304.7],[180.0,303.0]]
    missing=[row for row in raw if row.get("missing","").lower()=="true"]
    ok=len(raw)==6 and len(missing)==1 and float(missing[0]["elapsed_s"])==47.0 and raw[2]["temperature_k"]=="" and pairs==expected and parse_table(paths["field_T0"])==expected
    return ok,pairs


def log_gates(paths):
    solver=paths["solver_log"].read_text(encoding="utf-8",errors="replace"); mesh=paths["checkMesh_log"].read_text(encoding="utf-8",errors="replace")
    times=[float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)",solver,re.M)]
    courant=[float(value) for value in re.findall(r"Courant Number mean: [-+0-9.eE]+ max: ([-+0-9.eE]+)",solver)]
    continuity=[float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)",solver)]
    cells=[int(value) for value in re.findall(r"cells:\s+(\d+)",mesh)]
    gates={"mesh":"Mesh OK." in mesh and cells[-1:]==[141952],"final_time":bool(times) and abs(times[-1]-180.0)<=1e-8,"finite":"FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b",solver,re.I),"courant":bool(courant) and max(courant)<=1.0,"continuity":bool(continuity) and max(map(abs,continuity))<=1e-4}
    return gates,max(courant),max(map(abs,continuity))


def window(times,velocities,temperatures,start,end):
    exchange=[]; thermal=[]
    for time,row_u,row_t in zip(times,velocities,temperatures):
        if start<=time<=end: exchange.extend(max(float(value[2]),0.0) for value in row_u); thermal.extend(float(value) for value in row_t)
    return sum(exchange)/len(exchange),sum(thermal)/len(thermal)


def flatten(value): return [item for child in value for item in flatten(child)] if isinstance(value,list) else [float(value)]
def nmae(left,right,scale):
    a,b=flatten(left),flatten(right)
    if len(a)!=len(b) or not a: raise ValueError("nmae_shape")
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)/max(scale,1e-30)
def finite_array(value,length): return isinstance(value,list) and len(value)==length and all(isinstance(item,(int,float)) and not isinstance(item,bool) and math.isfinite(float(item)) for item in value)
def finite_matrix(value,rows,cols): return isinstance(value,list) and len(value)==rows and all(finite_array(row,cols) for row in value)
def finite_cube(value,rows,points): return isinstance(value,list) and len(value)==rows and all(isinstance(row,list) and len(row)==points and all(finite_array(vector,3) for vector in row) for row in value)


def evaluate_task(root: Path):
    root=root.resolve(); base=precheck(root,TASK_ID); errors=list(base.get("errors",[])); raw_ok=report_ok=metric_ok=hidden_ok=False; recomputed={}; hidden_delta=math.inf
    try:
        contract=load(root/"local_assets/surface_temperature_boundary_contract.json")
        hidden=load(Path(os.environ.get("URBANWIND_REFERENCE",f"/tmp/{TASK_ID}-reference.json")))
        evidence=load(root/"surface_temperature_evidence_manifest.json"); reported=load(root/"surface_temperature_results.json"); metrics=load(root/"metrics.json")
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
        source_ok,pairs=source_contract(paths); stimes,building_t=surface(paths["surface_T"])
        metric=math.sqrt(sum((value-interpolate(pairs,time))**2 for time,value in zip(stimes,building_t))/len(stimes))
        times,velocities=probe(paths["probe_U"],points,True); ttimes,temperatures=probe(paths["probe_T"],points,False)
        first=window(times,velocities,temperatures,60,120); second=window(times,velocities,temperatures,120,180)
        gates,max_co,max_cont=log_gates(paths)
        control=paths["controlDict"].read_text(encoding="utf-8",errors="replace"); turbulence=paths["turbulenceProperties"].read_text(encoding="utf-8",errors="replace")
        raw_gates={**gates,"source_and_table":source_ok,"surface_metric":metric<=1e-8,"times_aligned":times==ttimes,"temperature":min(value for row in temperatures for value in row)>=250 and max(value for row in temperatures for value in row)<=350,
                   "stationarity_exchange":abs(second[0]-first[0])<=0.05,"stationarity_temperature":abs(second[1]-first[1])<=0.5,
                   "control":bool(re.search(r"\bendTime\s+180(?:\.0+)?\s*;",control)) and bool(re.search(r"\bdeltaT\s+0\.015\s*;",control)) and "buildingTemperature" in control,
                   "wale":"simulationType LES;" in turbulence and "LESModel WALE;" in turbulence,
                   "mean_fields":all(paths[name].stat().st_size>0 for name in ("mean_UMean","mean_UPrime2Mean","mean_TMean","mean_TPrime2Mean"))}
        raw_ok=all(raw_gates.values())
        if not raw_ok: errors.append("raw_case_gate_failed")
        recomputed={"case_id":CASE_ID,"time_s":times,"building_time_s":stimes,"building_temperature_k":building_t,"probe_U_m_s":velocities,"probe_T_k":temperatures,PRIMARY_ID:metric,
                    "vertical_exchange_drift_m_s":abs(second[0]-first[0]),"temperature_drift_k":abs(second[1]-first[1]),"max_courant":max_co,"max_abs_cumulative_continuity":max_cont,"all_run_gates_pass":raw_ok}
        report_ok=finite_array(submitted.get("time_s"),900) and finite_array(submitted.get("building_time_s"),900) and finite_array(submitted.get("building_temperature_k"),900) and finite_cube(submitted.get("probe_U_m_s"),900,24) and finite_matrix(submitted.get("probe_T_k"),900,24) and submitted.get("all_run_gates_pass") is True
        if report_ok:
            report_ok=max(nmae(submitted["time_s"],times,180),nmae(submitted["building_time_s"],stimes,180),nmae(submitted["building_temperature_k"],building_t,300),nmae(submitted["probe_U_m_s"],velocities,1.2),nmae(submitted["probe_T_k"],temperatures,300))<=1e-8 and all(abs(float(submitted[key])-float(recomputed[key]))<=max(1e-9,abs(float(recomputed[key]))*1e-7) for key in (PRIMARY_ID,"vertical_exchange_drift_m_s","temperature_drift_k","max_courant","max_abs_cumulative_continuity"))
        if not report_ok: errors.append("reported_arrays_not_recomputed")
        value=metrics.get("values",{}).get(PRIMARY_ID); metric_ok=isinstance(value,(int,float)) and not isinstance(value,bool) and abs(float(value)-metric)<=1e-12
        if not metric_ok: errors.append("reported_metric_not_recomputed:"+PRIMARY_ID)
        reference=hidden["case_results"][0]
        hidden_delta=max(nmae(stimes,reference["building_time_s"],180),nmae(building_t,reference["building_temperature_k"],300),nmae(velocities,reference["probe_U_m_s"],1.2),nmae(temperatures,reference["probe_T_k"],300),abs(metric-reference[PRIMARY_ID]))
        hidden_ok=hidden_delta<=1e-8
        if not hidden_ok: errors.append("hidden_numerical_fidelity")
    except Exception as exc: errors.append("surface_temperature_reference_check:"+str(exc))
    components={"raw_openfoam_evidence":15.0 if raw_ok and not base.get("errors") else 0.0,"reported_array_recomputation":10.0 if report_ok and not base.get("errors") else 0.0,"primary_metric_recomputation":5.0 if metric_ok and not base.get("errors") else 0.0,"hidden_numerical_fidelity":10.0 if hidden_ok and not base.get("errors") else 0.0}
    return {"task_id":TASK_ID,"score":round(min(100.0,float(base.get("score",0.0))+sum(components.values())),4),"score_cap":100.0,"status":"openfoam_surface_temperature_numerical_reference_check","errors":errors,"components":components,"diagnostics":{"recomputed_case":recomputed,"hidden_max_scaled_delta":hidden_delta},"precheck":base,"claim_boundary":"Checks a local synthetic irregular/missing surface-temperature scenario and raw OpenFOAM v2412 evidence; not measured or experimental/PIV/DNS/wind-tunnel/field truth."}


def main():
    root=Path(os.environ.get("URBANWIND_EVAL_ROOT","/home/user/Desktop")); print(json.dumps(evaluate_task(root),ensure_ascii=False,sort_keys=True))


if __name__=="__main__": main()
