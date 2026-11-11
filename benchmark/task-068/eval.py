#!/usr/bin/env python3
"""Task-068 raw coordinate-transform and OpenFOAM inlet-reference evaluator."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re

from contract_evaluator import evaluate as precheck


TASK_ID = "urbanwind-task-068"
CASE_ID = "task-068-case-001"
PRIMARY_ID = "vector_tensor_roundtrip_error"
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
VECTOR = re.compile(r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


def load(path: Path, limit: int = 128 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > limit: raise ValueError("missing_or_oversized:"+path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    value=hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda:handle.read(1024*1024),b""): value.update(block)
    return value.hexdigest()


def evidence_path(root: Path, value) -> Path:
    if not isinstance(value,str) or not value or Path(value).is_absolute(): raise ValueError("invalid_evidence_path")
    path=(root/value).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size>256*1024*1024: raise ValueError("unsafe_missing_or_oversized_evidence:"+str(value))
    return path


def matvec(matrix,vector): return [sum(matrix[i][j]*vector[j] for j in range(3)) for i in range(3)]


def transpose(matrix): return [list(row) for row in zip(*matrix)]


def matmul(a,b): return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def norm(vector): return math.sqrt(sum(value*value for value in vector))


def rel_vec(a,b): return norm([x-y for x,y in zip(a,b)])/max(norm(b),1e-12)


def rel_tensor(a,b):
    return math.sqrt(sum((a[i][j]-b[i][j])**2 for i in range(3) for j in range(3)))/max(math.sqrt(sum(b[i][j]**2 for i in range(3) for j in range(3))),1e-12)


def source_rows(path: Path):
    with path.open(encoding="utf-8",newline="") as handle:
        return [{key:int(value) if key=="sample_id" else float(value) for key,value in row.items()} for row in csv.DictReader(handle)]


def probe_data(path: Path, expected_points, vector: bool):
    text=path.read_text(encoding="utf-8",errors="replace")
    points=[[float(value) for value in NUMBER.findall(match.group(1))] for match in re.finditer(r"^#\s*Probe\s+\d+\s+\(([^)]+)\)",text,re.M)]
    if points!=expected_points: raise ValueError("probe_coordinates_mismatch:"+path.name)
    times,rows=[],[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        values=[list(map(float,match)) for match in VECTOR.findall(line)] if vector else [float(value) for value in NUMBER.findall(line)][1:]
        time_values=NUMBER.findall(line.split("(",1)[0] if vector else line)
        if not time_values: continue
        if len(values)!=len(expected_points): raise ValueError("probe_row_shape:"+path.name)
        flat=[item for v in values for item in v] if vector else values
        if not all(math.isfinite(value) for value in flat): raise ValueError("probe_nonfinite:"+path.name)
        times.append(float(time_values[0])); rows.append(values)
    if times!=[float(value) for value in range(20,401,20)]: raise ValueError("probe_time_contract:"+path.name)
    return times,rows


def weighted_vector_rmse(values,targets,weights): return math.sqrt(sum(w*sum((a-b)**2 for a,b in zip(value,target)) for w,value,target in zip(weights,values,targets))/sum(weights))


def weighted_scalar_rmse(values,targets,weights): return math.sqrt(sum(w*(a-b)**2 for w,a,b in zip(weights,values,targets))/sum(weights))


def max_scaled_delta(left,right,scale):
    if isinstance(left,list) and isinstance(right,list) and len(left)==len(right): return max((max_scaled_delta(a,b,scale) for a,b in zip(left,right)),default=0.0)
    if isinstance(left,(int,float)) and isinstance(right,(int,float)): return abs(float(left)-float(right))/max(float(scale),1e-30)
    raise ValueError("hidden_shape_mismatch")


def log_and_input_gates(paths,contract):
    solver=paths["solver_log"].read_text(encoding="utf-8",errors="replace"); mesh=paths["checkMesh_log"].read_text(encoding="utf-8",errors="replace")
    iterations=[float(value) for value in re.findall(r"^Time = ([-+0-9.eE]+)",solver,re.M)]
    continuity=[float(value) for value in re.findall(r"cumulative = ([-+0-9.eE]+)",solver)]
    residuals={}
    for field in ("Ux","Uy","Uz","k","omega","p"):
        values=[float(value) for value in re.findall(r"Solving for "+field+r", Initial residual = ([-+0-9.eE]+)",solver)]
        residuals[field]=max(values[-20:]) if len(values)>=20 else None
    cells=re.findall(r"^\s*cells:\s+(\d+)\s*$",mesh,re.M); field_u=paths["field_U"].read_text(encoding="utf-8",errors="replace")
    control=paths["controlDict"].read_text(encoding="utf-8",errors="replace")
    gates={"mesh":"Mesh OK." in mesh and cells[-1:]==["365488"],"iteration":len(iterations)==400 and iterations[-1:]==[400.0],
           "model":"Selecting RAS turbulence model kOmegaSST" in solver,"finite":"FOAM FATAL" not in solver and not re.search(r"\b(?:nan|inf)\b",solver,re.I),
           "continuity":bool(continuity) and max(map(abs,continuity))<=float(contract["frozen_gates"]["max_abs_cumulative_continuity"]),
           "residuals":all(value is not None and math.isfinite(value) and value<=float(contract["frozen_gates"]["max_last20_initial_residual_full"]) for value in residuals.values()),
           "no_iteration_cap":"No Iterations 1000" not in solver,"fixed_profile":"fixedProfile" in field_u and 'file "constant/profile_power.csv";' in field_u,
           "control":bool(re.search(r"\bendTime\s+400\s*;",control)) and "inletProfile" in control and "writeInterval 20;" in control}
    return {"gates":gates,"final_iteration":iterations[-1] if iterations else None,"max_abs_cumulative_continuity":max(map(abs,continuity)) if continuity else None,"last20_max_initial_residuals":residuals}


def recompute(root: Path, paths, contract):
    rows=source_rows(root/contract["visible_source_fields"]["velocity_profile"])
    matrix=contract["coordinate_convention"]["R_geo_to_solver"]; inverse=transpose(matrix)
    targets_geo=[[row["U_geo_east_m_s"],row["U_geo_north_m_s"],row["U_geo_up_m_s"]] for row in rows]
    targets_solver=[matvec(matrix,value) for value in targets_geo]; weights=[row["layer_thickness_m"] for row in rows]
    points=[[-4.0,0.0625,row["z_m"]] for row in rows]
    times_u,probes_u=probe_data(paths["probe_U"],points,True); times_k,probes_k=probe_data(paths["probe_k"],points,False); times_o,probes_o=probe_data(paths["probe_omega"],points,False)
    if not (times_u==times_k==times_o): raise ValueError("probe_iteration_alignment")
    observed=probes_u[-1]; observed_geo=[matvec(inverse,value) for value in observed]
    roundtrips=[rel_vec(matvec(inverse,matvec(matrix,value)),value) for value in targets_geo]
    q=contract["visible_source_fields"]["heat_flux_geo_W_m2"]; roundtrips.append(rel_vec(matvec(inverse,matvec(matrix,q)),q))
    tensor=contract["visible_source_fields"]["reynolds_stress_geo_m2_s2"]; tensor_solver=matmul(matmul(matrix,tensor),inverse); tensor_back=matmul(matmul(inverse,tensor_solver),matrix)
    roundtrips.append(rel_tensor(tensor_back,tensor))
    p=contract["profile_parameters"]; ustar=p["Uref_m_s"]*p["kappa"]/math.log((p["Zref_m"]+p["z0_m"])/p["z0_m"])
    target_k=ustar**2/math.sqrt(p["Cmu"]); target_o=[ustar/(p["kappa"]*math.sqrt(p["Cmu"])*(row["z_m"]+p["z0_m"])) for row in rows]
    components={"E_roundtrip_visible":max(roundtrips),"E_solver_inlet":weighted_vector_rmse(observed,targets_solver,weights)/1.2,
                "E_inverse_projected_inlet":weighted_vector_rmse(observed_geo,targets_geo,weights)/1.2,
                "inlet_k_rmse_m2_s2":weighted_scalar_rmse(probes_k[-1],[target_k]*52,weights),"inlet_omega_rmse_s_1":weighted_scalar_rmse(probes_o[-1],target_o,weights)}
    components[PRIMARY_ID]=max(components[key] for key in ("E_roundtrip_visible","E_solver_inlet","E_inverse_projected_inlet"))
    return {"iterations":times_u,"inlet_U_m_s":probes_u,"inlet_k_m2_s2":probes_k,"inlet_omega_s_1":probes_o,"components":components}


def evaluate_task(root: Path):
    root=root.resolve(); base=precheck(root,TASK_ID); errors=list(base.get("errors",[])); evidence_ok=report_ok=metric_ok=hidden_ok=False; recomputed={}; hidden_delta=None
    try:
        contract=load(root/"local_assets"/"coordinate_transform_contract.json")
        hidden=load(Path(os.environ.get("URBANWIND_REFERENCE",f"/tmp/{TASK_ID}-reference.json")))
        manifest=load(root/"coordinate_transform_evidence_manifest.json"); report=load(root/"coordinate_transform_results.json"); metrics=load(root/"metrics.json")
        if any(item.get("task_id")!=TASK_ID for item in (contract,hidden,manifest,report)): raise ValueError("task_id_mismatch")
        lock=hidden.get("contract_lock",{})
        if any(contract.get(key)!=lock.get(key) for key in ("coordinate_convention","visible_source_fields","profile_parameters","frozen_gates","primary_metric")) or sha256(root/contract["visible_source_fields"]["velocity_profile"])!=lock.get("source_profile_sha256"):
            raise ValueError("visible_contract_lock_mismatch")
        if manifest.get("case_id")!=CASE_ID or report.get("case_id")!=CASE_ID: raise ValueError("case_id_mismatch")
        required=("probe_U","probe_k","probe_omega","solver_log","checkMesh_log","field_U","profile_csv","controlDict")
        evidence=manifest.get("evidence")
        if not isinstance(evidence,dict) or set(evidence)!=set(required): raise ValueError("evidence_item_set")
        paths={}; used=set()
        for key in required:
            item=evidence[key]; path=evidence_path(root,item.get("path"))
            if path in used or item.get("sha256")!=sha256(path): raise ValueError("evidence_hash_or_duplicate:"+key)
            used.add(path); paths[key]=path
        recomputed=recompute(root,paths,contract); log_gates=log_and_input_gates(paths,contract)
        components=recomputed["components"]; gates={**log_gates["gates"],"visible_roundtrip":components["E_roundtrip_visible"]<=contract["frozen_gates"]["max_visible_roundtrip_error"],
               "solver_inlet":components["E_solver_inlet"]<=contract["frozen_gates"]["max_solver_inlet_normalized_rmse"],
               "inverse_projected_inlet":components["E_inverse_projected_inlet"]<=contract["frozen_gates"]["max_inverse_projected_inlet_normalized_rmse"],
               "inlet_k":components["inlet_k_rmse_m2_s2"]<=contract["frozen_gates"]["max_inlet_k_rmse_m2_s2"],
               "inlet_omega":components["inlet_omega_rmse_s_1"]<=contract["frozen_gates"]["max_inlet_omega_rmse_s_1"]}
        if not all(gates.values()): raise ValueError("raw_case_gate_failed")
        evidence_ok=True; recomputed.update(log_gates); recomputed["all_run_gates_pass"]=True
        report_ok=(report.get("all_run_gates_pass") is True and max_scaled_delta(report.get("iterations"),recomputed["iterations"],400)<=1e-12
                   and max_scaled_delta(report.get("inlet_U_m_s"),recomputed["inlet_U_m_s"],1.2)<=1e-10
                   and max_scaled_delta(report.get("inlet_k_m2_s2"),recomputed["inlet_k_m2_s2"],0.03)<=1e-10
                   and max_scaled_delta(report.get("inlet_omega_s_1"),recomputed["inlet_omega_s_1"],10)<=1e-10
                   and all(abs(float(report.get("components",{}).get(key,math.inf))-value)<=max(1e-12,abs(value)*1e-7) for key,value in components.items()))
        if not report_ok: errors.append("reported_arrays_not_recomputed")
        value=metrics.get("values",{}).get(PRIMARY_ID); metric_ok=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value)) and abs(float(value)-components[PRIMARY_ID])<=max(1e-12,abs(components[PRIMARY_ID])*1e-7)
        if not metric_ok: errors.append("reported_metric_not_recomputed:"+PRIMARY_ID)
        reference=hidden["case_results"][0]
        hidden_delta=max(max_scaled_delta(recomputed["iterations"],reference["iterations"],400),max_scaled_delta(recomputed["inlet_U_m_s"],reference["inlet_U_m_s"],1.2),
                         max_scaled_delta(recomputed["inlet_k_m2_s2"],reference["inlet_k_m2_s2"],0.03),max_scaled_delta(recomputed["inlet_omega_s_1"],reference["inlet_omega_s_1"],10))
        hidden_ok=hidden_delta<=1e-8
        if not hidden_ok: errors.append("hidden_numerical_fidelity")
    except Exception as exc: errors.append("coordinate_transform_reference_check:"+str(exc))
    components_score={"raw_coordinate_transform_evidence":15.0 if evidence_ok and not base.get("errors") else 0.0,"reported_array_recomputation":10.0 if report_ok and not base.get("errors") else 0.0,
                      "primary_metric_recomputation":5.0 if metric_ok and not base.get("errors") else 0.0,"hidden_numerical_fidelity":10.0 if hidden_ok and not base.get("errors") else 0.0}
    return {"task_id":TASK_ID,"score":round(min(100.0,float(base.get("score",0.0))+sum(components_score.values())),4),"score_cap":100.0,
            "status":"openfoam_coordinate_transform_numerical_reference_check","errors":errors,"components":components_score,
            "diagnostics":{"recomputed":recomputed,"hidden_max_scaled_delta":hidden_delta},"precheck":base,
            "claim_boundary":"Checks visible coordinate algebra and raw OpenFOAM transformed-velocity inlet evidence against a hidden numerical reference; heat flux and stress are not solved fields and this is not experimental truth or release validation."}


def main():
    root=Path(os.environ.get("URBANWIND_EVAL_ROOT","/home/user/Desktop")); print(json.dumps(evaluate_task(root),ensure_ascii=False,sort_keys=True))


if __name__=="__main__": main()
