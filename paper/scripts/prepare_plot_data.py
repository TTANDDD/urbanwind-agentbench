"""Prepare the small CSV files used by plot_figures.py.

Usage:
    python prepare_plot_data.py <per_task_merged.json> <tasks.jsonl> <out_dir> <verification_full.json>

per_task_merged.json : per-task results of the base agent (v0.2.2) and WindPilot (v0.2.14),
                       exported read-only from the 5070 server run directories.
tasks.jsonl          : UrbanWind-AgentBench task catalog (task-urbanwind/catalog/tasks.jsonl).
verification_full.json: full deterministic-check error strings per checkpoint (read-only export).
Fig. 13 data come from fetch_case_checks.py.
"""
import collections
import csv
import json
import random
import statistics
import sys
from pathlib import Path

FAMILY_EN = {
    "evidence_and_case_definition": "Evidence & case definition", "gis_and_geometry": "GIS & geometry",
    "mesh_and_numerics": "Mesh & numerics", "abl_and_boundary_conditions": "ABL & boundary conditions",
    "openfoam_rans_les_execution": "OpenFOAM RANS/LES", "palm_urban_microclimate": "PALM urban microclimate",
    "diagnosis_and_repair": "Diagnosis & repair", "validation_and_uncertainty": "Validation & uncertainty",
    "flow_diagnostics_and_risk": "Flow diagnostics & risk", "optimization_and_decision": "Optimization & decision",
}
INIT_EN = {"engineering_data_and_model": "Engineering data & model", "incomplete_template": "Incomplete template",
           "broken_case": "Broken case", "from_scratch": "From scratch", "runnable_baseline": "Runnable baseline"}
ANCHOR_EN = {"PROCEDURAL_CITY": "Procedural city", "SB4_LOCAL": "Single building (D×D×4D)", "LOCAL_MULTI": "Multi-building",
             "CUBE_LOCAL": "Cube array", "AIJ_DIRECT": "AIJ case", "LOCAL_DISPERSION": "Dispersion case",
             "LOCAL_3D_WIND": "3D wind field"}
ABL_SHORT = {"task-061": "Neutral log-law inlet", "task-062": "Profile-law check", "task-063": "Roughness repair",
             "task-064": "Monin–Obukhov inlet", "task-068": "Wind-direction rotation", "task-069": "Top boundary",
             "task-071": "Outlet repair", "task-072": "GIS roughness mapping", "task-074": "Surface heat flux",
             "task-075": "Surface temperature", "task-076": "Potential temperature", "task-078": "Precursor inflow",
             "task-079": "Mass-flux balance"}


def check_category(err):
    """Map a full deterministic-check error string to one of six categories (Fig. 14b)."""
    if any(k in err for k in ("missing_decision", "decision_trace_incomplete", "missing_artifact:decision.md")):
        return "Incomplete decision trace"
    if any(k in err for k in ("missing_artifact", "missing_or_oversized", "FileNotFoundError", "missing_or_not_regular_file",
                              "missing_object", "manifest_unavailable", "report_unavailable")):
        return "Missing or unreadable file"
    if "hash" in err:
        return "Stale evidence hash"
    if any(k in err for k in ("report_raw", "raw_statistics", "statistical_array_shape")):
        return "Report–raw inconsistency"
    if "missing_finite_metric_value" in err or "invalid_metrics" in err:
        return "Invalid metric value"
    if "above_public_gate" in err:
        return "Reported value above gate"
    return "Other"


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main(merged_path, catalog_path, out_dir, verification_path):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = json.loads(Path(merged_path).read_text(encoding="utf-8"))
    tasks = sorted(d)
    base = {t: d[t]["base"]["score_corrected"] for t in tasks}
    new = {t: d[t]["new"]["score"] for t in tasks}
    abl = [t for t in tasks if 61 <= int(t[5:]) <= 80]

    # Fig. 2 and Fig. 3: catalog statistics
    meta = [json.loads(line) for line in open(catalog_path, encoding="utf-8")]
    meta = [x.get("benchmark_metadata", x) for x in meta]
    cnt = collections.Counter((m["track"], m["professional_difficulty"]) for m in meta)
    write(out / "fig02_family_difficulty.csv", ["family", "D1", "D2", "D3", "D4", "D5"],
          [[FAMILY_EN[f]] + [cnt[(f, "D%d" % i)] for i in range(1, 6)] for f in FAMILY_EN])
    sw = collections.defaultdict(set)
    for m in meta:
        sw[FAMILY_EN[m["track"]]].update(str(s).lower() for s in m.get("software_chain") or [])
    write(out / "fig02_family_software.csv", ["family", "software"], [[f, ";".join(sorted(sw[f]))] for f in FAMILY_EN.values()])
    write(out / "fig03_init_type.csv", ["init_type", "count"],
          [[INIT_EN[k], v] for k, v in collections.Counter(m["init_type"] for m in meta).most_common()])
    write(out / "fig03_geometry.csv", ["geometry", "count"],
          [[ANCHOR_EN.get(k, k), v] for k, v in collections.Counter(m["case_anchor"] for m in meta).most_common()])

    # Fig. 7: paired mean difference with 95 % bootstrap CI
    rng = random.Random(20261024)
    rows = []
    for name, ts in (("All tasks", tasks), ("ABL tasks", abl)):
        diff = [new[t] - base[t] for t in ts]
        boots = sorted(statistics.mean(rng.choices(diff, k=len(diff))) for _ in range(10000))
        rows.append([name, len(ts), statistics.mean(base[t] for t in ts), statistics.mean(new[t] for t in ts),
                     statistics.mean(diff), boots[249], boots[9749]])
    write(out / "fig07_forest.csv", ["group", "n", "base_mean", "windpilot_mean", "diff", "ci_low", "ci_high"], rows)

    # Fig. 8: score distribution (task identity removed) and band counts
    write(out / "fig08_ecdf.csv", ["condition", "score"],
          [["Base agent", v] for v in sorted(base.values())] + [["WindPilot", v] for v in sorted(new.values())])
    band = lambda x: "<60" if x < 60 else "=60" if x == 60 else "60–80" if x < 80 else "80–99" if x < 100 else "100"
    cb, cn = collections.Counter(map(band, base.values())), collections.Counter(map(band, new.values()))
    write(out / "fig08_score_band.csv", ["band", "base", "windpilot"], [[b, cb[b], cn[b]] for b in ["<60", "=60", "60–80", "80–99", "100"]])

    # Fig. 9: ABL per-task scores
    write(out / "fig09_abl_tasks.csv", ["task", "name", "base", "windpilot"], [[t, ABL_SHORT[t], base[t], new[t]] for t in abl])

    # Fig. 10: action steps (and tokens, quoted in text)
    write(out / "fig10_steps_tokens.csv", ["task", "base_steps", "windpilot_steps", "base_tokens", "windpilot_tokens"],
          [[t, d[t]["base"]["steps"], d[t]["new"]["steps"], d[t]["base"]["tokens"], d[t]["new"]["tokens"]] for t in tasks])

    # Fig. 14: checkpoint positions, intercepted vs repaired by category, review statistics
    write(out / "fig14_checkpoints.csv", ["task", "step", "trigger", "has_error"],
          [[t, int(c["step"]), c["trigger"], int(bool(c["errors"]))] for t in tasks for c in d[t]["verification"]])
    inter, rep = collections.Counter(), collections.Counter()
    full = json.loads(Path(verification_path).read_text(encoding="utf-8"))
    for t in tasks:
        cks = sorted(full.get(t, []), key=lambda c: int(c["step"]))
        if not cks:
            continue
        final = set(cks[-1]["errors"])
        for e in {e for c in cks for e in c["errors"]}:
            k = check_category(e)
            inter[k] += 1
            rep[k] += e not in final
    write(out / "fig14_check_categories.csv", ["category", "intercepted", "repaired"], [[k, v, rep[k]] for k, v in inter.most_common()])
    judges = [j for t in tasks for j in d[t]["judge"]]
    write(out / "fig14_review_verdicts.csv", ["verdict", "count"], collections.Counter(j["verdict"] for j in judges).most_common())


if __name__ == "__main__":
    main(*sys.argv[1:5])
