#!/usr/bin/env python3
"""Generate small deterministic, answer-free local inputs for every task."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import shutil
from pathlib import Path
from typing import Any


GENERATOR_VERSION = "urbanwind-local-assets-v3"


CONTROLLED_FACTOR_BY_TASK = {
    56: "geometry_scale_only",
    39: "geometry_representation",
    43: "near_wall_mesh_design",
    45: "mesh_resolution",
    60: "mesh_resolution",
    62: "inflow_profile_law",
    64: "atmospheric_stability",
    65: "synthetic_turbulence_boundaryData",
    69: "top_boundary_condition",
    70: "lateral_boundary_condition",
    72: "spatial_roughness_zone_mapping",
    74: "spatial_and_temporal_surface_sensible_heat_flux",
    75: "irregular_missing_surface_temperature_alignment",
    76: "potential_temperature_hydrostatic_initialization",
    73: "wall_treatment",
    77: "software_inflow_target",
    78: "precursor_statistics_window_convergence",
    79: "inlet_outlet_mass_flux_closure",
    83: "sgs_model",
    84: "time_step",
    85: "convection_scheme",
    86: "pressure_velocity_coupling",
    92: "wind_direction",
    93: "aerodynamic_roughness",
    94: "surface_heat_flux",
    95: "atmospheric_stability",
    96: "scalar_source_configuration",
    98: "decomposition_method",
    99: "restart_segmentation",
    100: "openfoam_version",
    106: "domain_nesting_role",
    108: "atmospheric_stability",
    109: "surface_heat_flux",
    110: "radiation_scheme",
    111: "wind_direction",
    114: "restart_mode",
    116: "canopy_drag_multiplier",
    117: "urban_heat_forcing",
    118: "simulation_software",
    120: "wind_direction",
    174: "wind_direction_scenario",
    180: "scenario",
    181: "orientation_and_wind_direction",
    182: "spacing_and_wind_direction",
    183: "height_gradient_design",
    184: "porosity_and_wind_direction",
    185: "corridor_width_and_wind_direction",
    186: "setback_design",
    187: "podium_design",
    188: "roof_design",
    189: "ground_opening_design",
    190: "tree_configuration",
    191: "albedo_design",
    192: "heat_source_layout",
    193: "design_and_wind_direction",
    194: "design_and_atmospheric_stability",
    196: "fidelity_and_candidate",
    197: "design_and_simulation_software",
    199: "candidate_and_robustness_check",
    200: "design_software_and_forcing",
}


def _case_control_values(task_number: int, index: int, variant: str) -> dict[str, Any]:
    """Return a one-factor-at-a-time case contract.

    Every non-target physical condition is frozen.  The explicit columns make
    it possible to audit that a comparison task has not accidentally changed
    wind, thermal forcing, solver settings, and geometry at the same time.
    """
    value: dict[str, Any] = {
        "wind_direction_deg": 0.0,
        "reference_speed_m_s": 1.2,
        "stability": "neutral",
        "surface_heat_flux_w_m2": 0.0,
        "sgs_model": "",
        "time_step_s": "",
        "convection_scheme": "",
        "pressure_velocity_coupling": "",
        "roughness_length_m": "",
        "source_configuration": "",
        "decomposition_method": "",
        "restart_segments": "",
        "software_variant": "",
        "controlled_factor": CONTROLLED_FACTOR_BY_TASK.get(task_number, "task_variant"),
        "controlled_level": variant,
    }

    # Only tasks whose stated factor includes direction may change direction.
    if task_number in {92, 111, 120, 174, 181, 182, 184, 185, 193, 200}:
        direction_levels = [0.0, 22.5, 45.0]
        if task_number in {181, 182, 184, 185, 193}:
            value["wind_direction_deg"] = direction_levels[index % 3]
        elif task_number == 200:
            condition_index = index % 6
            if condition_index < 3:
                value["wind_direction_deg"] = direction_levels[condition_index]
        else:
            value["wind_direction_deg"] = direction_levels[index % 3]

    # Stability comparisons freeze wind and heat flux and change stability only.
    if task_number in {64, 95, 108, 194}:
        levels = ["stable", "neutral", "unstable"]
        value["stability"] = levels[index % 3]
    if task_number == 200 and index % 6 in {3, 4}:
        value["stability"] = "neutral" if index % 6 == 3 else "unstable"

    # Thermal-forcing comparisons freeze direction, speed, and stability.
    if task_number in {94, 109}:
        value["surface_heat_flux_w_m2"] = [0.0, 50.0, 150.0][index % 3]
    elif task_number == 117:
        value["surface_heat_flux_w_m2"] = [25.0, 50.0, 100.0][index % 3]
    elif task_number == 200 and index % 6 == 5:
        value["surface_heat_flux_w_m2"] = 150.0

    if task_number == 83:
        value["sgs_model"] = ["WALE", "Smagorinsky", "dynamicKEqn"][index]
    elif task_number == 84:
        value["time_step_s"] = [0.04, 0.02, 0.01][index]
    elif task_number == 85:
        value["convection_scheme"] = ["limitedLinearV_1", "upwind_first_order"][index]
    elif task_number == 86:
        value["pressure_velocity_coupling"] = ["reference", "coupling_A", "coupling_B"][index]
    elif task_number == 93:
        value["roughness_length_m"] = [0.002, 0.02, 0.2][index]
    elif task_number == 96:
        value["source_configuration"] = ["source_A", "source_B", "source_C"][index]
    elif task_number == 98:
        value["decomposition_method"] = ["simple", "scotch", "hierarchical"][index]
    elif task_number in {99, 114}:
        levels = [1, 2, 4] if task_number == 99 else [1, 2]
        value["restart_segments"] = levels[index]
    elif task_number in {77, 118, 197}:
        value["software_variant"] = "OpenFOAM" if "OpenFOAM" in variant else "PALM"
    elif task_number == 100:
        value["software_variant"] = ["frozen_openfoam", "candidate_openfoam"][index]
    elif task_number == 196:
        value["software_variant"] = "RANS" if variant.startswith("RANS") else "LES"
    elif task_number == 200:
        value["software_variant"] = "OpenFOAM" if "OpenFOAM" in variant else "PALM"
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _geometry(task_number: int) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for row in range(4):
        for col in range(4):
            x0 = 0.5 + 2.0 * col + (0.5 if row % 2 else 0.0)
            y0 = 0.5 + 2.0 * row
            ring = [[x0, y0], [x0 + 1.0, y0], [x0 + 1.0, y0 + 1.0], [x0, y0 + 1.0], [x0, y0]]
            features.append({
                "type": "Feature",
                "properties": {
                    "building_id": f"b-{row:02d}-{col:02d}",
                    "height_m": round(1.0 + 0.25 * ((row + col + task_number) % 4), 3),
                    "source": GENERATOR_VERSION,
                },
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            })
    if task_number == 21:
        features[0]["geometry"]["coordinates"] = [[[0.5, 0.5], [1.5, 1.5], [1.5, 0.5], [0.5, 1.5], [0.5, 0.5]]]
        features[0]["properties"]["injected_fault"] = "self_intersection"
    elif task_number == 24:
        features[1]["geometry"]["coordinates"] = [[[1.0, 0.75], [2.0, 0.75], [2.0, 1.75], [1.0, 1.75], [1.0, 0.75]]]
        features[1]["properties"]["injected_fault"] = "overlap_with_b-00-00"
    return {
        "type": "FeatureCollection",
        "name": f"procedural-city-task-{task_number:03d}",
        "crs_contract": {"type": "local_cartesian", "unit": "m", "axis": "x-east,y-north,z-up"},
        "features": features,
    }


def _box_stl() -> str:
    vertices = {
        "000": (-0.5, -0.5, 0.0), "100": (0.5, -0.5, 0.0), "110": (0.5, 0.5, 0.0), "010": (-0.5, 0.5, 0.0),
        "001": (-0.5, -0.5, 4.0), "101": (0.5, -0.5, 4.0), "111": (0.5, 0.5, 4.0), "011": (-0.5, 0.5, 4.0),
    }
    faces = [
        ("000", "110", "100"), ("000", "010", "110"),
        ("001", "101", "111"), ("001", "111", "011"),
        ("000", "100", "101"), ("000", "101", "001"),
        ("010", "011", "111"), ("010", "111", "110"),
        ("000", "001", "011"), ("000", "011", "010"),
        ("100", "110", "111"), ("100", "111", "101"),
    ]
    lines = ["solid building"]
    for a, b, c in faces:
        lines.extend(["  facet normal 0 0 0", "    outer loop"])
        for key in (a, b, c):
            x, y, z = vertices[key]
            lines.append(f"      vertex {x:g} {y:g} {z:g}")
        lines.extend(["    endloop", "  endfacet"])
    lines.append("endsolid building")
    return "\n".join(lines) + "\n"


def _block_mesh_dict() -> str:
    """Create a conformal multiblock mesh with the D x D x 4D solid removed."""
    xs = (-4.0, -0.5, 0.5, 23.0)
    ys = (-6.2, -0.5, 0.5, 6.2)
    zs = (0.0, 4.0, 9.0)
    nx = (7, 2, 45)
    ny = (12, 2, 12)
    nz = (8, 10)

    def vid(i: int, j: int, k: int) -> int:
        return k * len(xs) * len(ys) + j * len(xs) + i

    def cell_vertices(i: int, j: int, k: int) -> tuple[int, ...]:
        return (
            vid(i, j, k), vid(i + 1, j, k), vid(i + 1, j + 1, k), vid(i, j + 1, k),
            vid(i, j, k + 1), vid(i + 1, j, k + 1), vid(i + 1, j + 1, k + 1), vid(i, j + 1, k + 1),
        )

    vertices = [f"    ({x:g} {y:g} {z:g})" for z in zs for y in ys for x in xs]
    blocks: list[str] = []
    faces: dict[str, list[tuple[int, int, int, int]]] = {
        "inlet": [], "outlet": [], "sideLow": [], "sideHigh": [],
        "ground": [], "top": [], "building": [],
    }
    for k in range(2):
        for j in range(3):
            for i in range(3):
                if k == 0 and i == 1 and j == 1:
                    continue
                b = cell_vertices(i, j, k)
                blocks.append(
                    f"    hex ({' '.join(map(str, b))}) ({nx[i]} {ny[j]} {nz[k]}) simpleGrading (1 1 1)"
                )
                if i == 0:
                    faces["inlet"].append((b[0], b[4], b[7], b[3]))
                if i == 2:
                    faces["outlet"].append((b[1], b[2], b[6], b[5]))
                if j == 0:
                    faces["sideLow"].append((b[0], b[1], b[5], b[4]))
                if j == 2:
                    faces["sideHigh"].append((b[3], b[7], b[6], b[2]))
                if k == 0:
                    faces["ground"].append((b[0], b[3], b[2], b[1]))
                if k == 1:
                    faces["top"].append((b[4], b[5], b[6], b[7]))
                if k == 0 and i == 0 and j == 1:
                    faces["building"].append((b[1], b[2], b[6], b[5]))
                if k == 0 and i == 2 and j == 1:
                    faces["building"].append((b[0], b[4], b[7], b[3]))
                if k == 0 and i == 1 and j == 0:
                    faces["building"].append((b[3], b[7], b[6], b[2]))
                if k == 0 and i == 1 and j == 2:
                    faces["building"].append((b[0], b[1], b[5], b[4]))
                if k == 1 and i == 1 and j == 1:
                    faces["building"].append((b[0], b[3], b[2], b[1]))

    patch_types = {
        "inlet": "patch", "outlet": "patch", "sideLow": "symmetryPlane",
        "sideHigh": "symmetryPlane", "ground": "wall", "top": "symmetryPlane",
        "building": "wall",
    }
    boundary_lines: list[str] = []
    for name, patch_faces in faces.items():
        boundary_lines.extend([
            f"    {name}", "    {", f"        type {patch_types[name]};", "        faces",
            "        (",
            *[f"            ({' '.join(map(str, face))})" for face in patch_faces],
            "        );", "    }",
        ])

    return "\n".join([
        "FoamFile", "{", "    format ascii;", "    class dictionary;", "    object blockMeshDict;", "}",
        "convertToMeters 1;", "vertices", "(", *vertices, ");", "blocks", "(", *blocks, ");",
        "edges ();", "boundary", "(", *boundary_lines, ");", "mergePatchPairs ();", "",
    ])


def _write_openfoam_template(base: Path) -> None:
    _write_text(base / "system" / "blockMeshDict", _block_mesh_dict())
    sample_points = "\n".join(f"            ({-2.0 + 0.25 * i:g} 0.5 1)" for i in range(64))
    _write_text(base / "system" / "sampleDict", f"""FoamFile{{format ascii; class dictionary; object sampleDict;}}
type sets;
libs (sampling);
interpolationScheme cellPoint;
setFormat csv;
sets
(
    comparison_line
    {{
        type cloud;
        axis xyz;
        points
        (
{sample_points}
        );
    }}
);
fields (U p);
""")
    _write_text(base / "system" / "controlDict", """FoamFile{format ascii; class dictionary; object controlDict;}
application pimpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 10;
deltaT 0.01;
writeControl runTime;
writeInterval 1;
runTimeModifiable true;
functions
{
    sampleLine
    {
        type sets;
        libs (sampling);
        writeControl writeTime;
        interpolationScheme cellPoint;
        setFormat csv;
        sets
        (
            comparison_line
            {
                type cloud;
                axis xyz;
                points
                (
{sample_points}
                );
            }
        );
        fields (U p);
    }
}
""".replace("{sample_points}", sample_points))
    _write_text(base / "system" / "fvSchemes", """FoamFile{format ascii; class dictionary; object fvSchemes;}
ddtSchemes{default Euler;}
gradSchemes{default Gauss linear;}
divSchemes
{
    default none;
    div(phi,U) Gauss linearUpwind grad(U);
    div(phi,k) Gauss upwind;
    div(phi,omega) Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes{default Gauss linear corrected;}
interpolationSchemes{default linear;}
snGradSchemes{default corrected;}
""")
    _write_text(base / "system" / "fvSolution", """FoamFile{format ascii; class dictionary; object fvSolution;}
solvers
{
    p{solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0.05;}
    pFinal{solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0;}
    U{solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0.05;}
    UFinal{solver smoothSolver; smoother symGaussSeidel; tolerance 1e-7; relTol 0;}
}
PIMPLE{nOuterCorrectors 2; nCorrectors 2; nNonOrthogonalCorrectors 1;}
""")
    _write_text(base / "constant" / "transportProperties", """FoamFile{format ascii; class dictionary; object transportProperties;}
transportModel Newtonian;
nu [0 2 -1 0 0 0 0] 1e-4;
""")
    _write_text(base / "constant" / "turbulenceProperties", """FoamFile{format ascii; class dictionary; object turbulenceProperties;}
simulationType laminar;
""")
    _write_text(base / "0" / "U", """FoamFile{format ascii; class volVectorField; object U;}
dimensions [0 1 -1 0 0 0 0];
internalField uniform (1.2 0 0);
boundaryField{inlet{type fixedValue; value uniform (1.2 0 0);} outlet{type zeroGradient;} sideLow{type symmetryPlane;} sideHigh{type symmetryPlane;} ground{type noSlip;} top{type symmetryPlane;} building{type noSlip;}}
""")
    _write_text(base / "0" / "p", """FoamFile{format ascii; class volScalarField; object p;}
dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField{inlet{type zeroGradient;} outlet{type fixedValue; value uniform 0;} sideLow{type symmetryPlane;} sideHigh{type symmetryPlane;} ground{type zeroGradient;} top{type symmetryPlane;} building{type zeroGradient;}}
""")
    _write_text(base / "constant" / "triSurface" / "building.stl", _box_stl())


def _write_palm_template(base: Path) -> None:
    _write_text(base / "urbanwind_p3d", """&initialization_parameters
 nx = 79, ny = 79, nz = 47,
 dx = 1.0, dy = 1.0, dz = 1.0,
 origin_date_time = '2026-01-01 00:00:00 +00',
 initializing_actions = 'set_constant_profiles',
 ug_surface = 1.2, vg_surface = 0.0,
 neutral = .TRUE.,
/
&runtime_parameters
 end_time = 600.0,
 dt_run_control = 60.0,
/
""")
    _write_csv(base / "static_driver_table.csv", ["x_m", "y_m", "building_height_m", "surface_class", "lad"], [
        [x, y, 4.0 if 20 <= x < 30 and 20 <= y < 30 else 0.0, 1, 0.0]
        for x in range(0, 80, 10) for y in range(0, 80, 10)
    ])


def generate_local_assets(
    init_dir: Path,
    task_number: int,
    track_number: int,
    task_id: str,
    title: str,
    anchor: dict[str, Any],
    cases: list[dict[str, Any]],
    software: list[str],
    required_inputs: list[str],
    init_type: str,
) -> list[Path]:
    asset_dir = (init_dir / "local_assets").resolve()
    if asset_dir.parent != init_dir.resolve():
        raise RuntimeError("unsafe local asset path")
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True)
    rng = random.Random(202609030000 + task_number)

    case_rows = []
    case_headers = [
        "case_id", "variant", "seed", "wind_direction_deg", "reference_speed_m_s",
        "stability", "surface_heat_flux_w_m2", "sgs_model", "time_step_s",
        "convection_scheme", "pressure_velocity_coupling", "roughness_length_m",
        "source_configuration", "decomposition_method", "restart_segments",
        "software_variant", "controlled_factor", "controlled_level",
    ]
    for index, case in enumerate(cases):
        controls = _case_control_values(task_number, index, str(case["variant"]))
        # The seed is frozen within a comparison task so stochastic inflow is
        # not an unadvertised second factor.
        row = {
            "case_id": case["case_id"],
            "variant": case["variant"],
            "seed": 202609030000 + task_number * 100,
            **controls,
        }
        case_rows.append([row[header] for header in case_headers])
    _write_csv(asset_dir / "case_matrix.csv", case_headers, case_rows)
    _write_json(asset_dir / "case_control_contract.json", {
        "schema_version": "1.0",
        "task_id": task_id,
        "controlled_factor": CONTROLLED_FACTOR_BY_TASK.get(task_number, "task_variant"),
        "policy": "Freeze every non-target physical and numerical condition; vary only the factor named by the task variant.",
        "frozen_defaults": {
            "reference_speed_m_s": 1.2,
            "wind_direction_deg": "0.0 unless wind direction is the controlled factor",
            "stability": "neutral unless atmospheric stability is the controlled factor",
            "surface_heat_flux_w_m2": "0.0 unless thermal forcing is the controlled factor",
            "seed": 202609030000 + task_number * 100,
        },
        "case_ids": [case["case_id"] for case in cases],
        "answer_free": True,
    })

    _write_csv(asset_dir / "mesh_levels.csv", ["level", "nominal_cell_size_m", "estimated_cells", "refinement_ratio", "role"], [
        ["coarse", 1.0, 75000, 2.0, "locally_generated_mesh_target"],
        ["medium", 0.5, 600000, 2.0, "locally_generated_mesh_target"],
        ["fine", 0.25, 4800000, 2.0, "locally_generated_mesh_target"],
    ])
    field_headers = ["point_id", "x_m", "y_m", "z_m", "u_m_s", "v_m_s", "w_m_s", "tke_m2_s2", "temperature_k", "concentration", "vertical_flux", "truth_type"]
    reference_rows = [
        [
            f"p-{ix:02d}-{iy:02d}-{iz:02d}", x, y, z,
            round(1.2 * (1.0 - 0.35 * math.exp(-((x - 3.0) / 3.0) ** 2)) * (z / 4.0) ** 0.12, 8),
            round(0.04 * math.sin(y / 2.0), 8), round(0.02 * math.cos(x / 3.0), 8),
            round(0.06 + 0.015 * math.sin((x + y) / 4.0), 8), round(300.0 + 0.01 * z + 0.2 * math.sin(x / 5.0), 8),
            round(math.exp(-((x - 2.0) ** 2 + (y - 2.0) ** 2) / 20.0) / (1.0 + z), 8),
            round(0.01 * math.sin((x + y + z) / 3.0), 8), "synthetic_numerical_reference",
        ]
        for ix, x in enumerate(range(-2, 10, 2))
        for iy, y in enumerate(range(0, 8, 2))
        for iz, z in enumerate((0.5, 1.0, 2.0, 4.0))
    ]
    _write_csv(asset_dir / "reference_field.csv", field_headers, reference_rows)
    model_rows = []
    for row_index, row in enumerate(reference_rows):
        changed = list(row)
        changed[4] = round(float(row[4]) * (1.0 + 0.025 * math.sin(row_index / 7.0)), 8)
        changed[5] = round(float(row[5]) + 0.006 * math.cos(row_index / 9.0), 8)
        changed[6] = round(float(row[6]) - 0.004 * math.sin(row_index / 11.0), 8)
        changed[7] = round(float(row[7]) * (1.0 + 0.05 * math.cos(row_index / 8.0)), 8)
        changed[8] = round(float(row[8]) + 0.08 * math.sin(row_index / 13.0), 8)
        changed[9] = round(float(row[9]) * (1.0 + 0.04 * math.sin(row_index / 6.0)), 8)
        changed[10] = round(float(row[10]) + 0.001 * math.cos(row_index / 10.0), 8)
        changed[11] = "deterministic_local_model_field"
        model_rows.append(changed)
    _write_csv(asset_dir / "model_field.csv", field_headers, model_rows)
    _write_csv(asset_dir / "mesh_solution_series.csv", ["level", "cell_size_m", "observable", "source_type"], [
        ["coarse", 1.0, 0.86, "deterministic_local_numerical_solution"],
        ["medium", 0.5, 0.94, "deterministic_local_numerical_solution"],
        ["fine", 0.25, 0.98, "deterministic_local_numerical_solution"],
    ])
    _write_csv(asset_dir / "reference_spectrum.csv", ["frequency_hz", "model_energy", "reference_energy", "source_type"], [
        [round(index * 0.02, 6), round(1.0 / (1.0 + (index * 0.02 / 0.2) ** 2) * (1.0 + 0.03 * math.sin(index)), 10), round(1.0 / (1.0 + (index * 0.02 / 0.2) ** 2), 10), "deterministic_analytic_spectrum"]
        for index in range(1, 101)
    ])
    _write_csv(asset_dir / "control_points.csv", ["point_id", "source_x_m", "source_y_m", "reference_x_m", "reference_y_m", "group"], [
        [f"cp-{index:02d}", x, y, x + 0.2, y - 0.1, f"group-{index % 4}"]
        for index, (x, y) in enumerate(((x, y) for x in range(4) for y in range(4)), start=1)
    ])
    _write_csv(asset_dir / "active_learning_data.csv", ["candidate_id", "feature_1", "feature_2", "observed_utility", "is_initial_train"], [
        [f"candidate-{index:02d}", round(index / 20.0, 6), round(math.sin(index / 3.0), 8), "" if index > 6 else round(0.5 + 0.25 * math.sin(index / 2.0), 8), int(index <= 6)]
        for index in range(1, 21)
    ])
    _write_csv(asset_dir / "velocity_timeseries.csv", ["sample", "time_s", "u_m_s", "v_m_s", "w_m_s", "temperature_k", "source_type"], [
        [
            index, round(index * 0.1, 6),
            round(1.2 + 0.12 * math.sin(2 * math.pi * 0.2 * index * 0.1) + 0.025 * math.sin(2 * math.pi * 1.1 * index * 0.1), 8),
            round(0.05 * math.cos(2 * math.pi * 0.2 * index * 0.1), 8),
            round(0.03 * math.sin(2 * math.pi * 0.35 * index * 0.1), 8),
            round(300.0 + 0.4 * math.sin(2 * math.pi * 0.01 * index * 0.1), 8),
            "deterministic_synthetic_timeseries",
        ]
        for index in range(512)
    ])
    _write_csv(asset_dir / "mask_and_weights.csv", ["cell_id", "x_m", "y_m", "pedestrian_mask", "road_mask", "population_weight", "area_weight_m2"], [
        [f"c-{ix:02d}-{iy:02d}", x, y, int((ix + iy) % 3 != 0), int(ix % 2 == 0), round(1.0 + 0.2 * ((ix + 2 * iy) % 5), 6), 1.0]
        for ix, x in enumerate(range(8)) for iy, y in enumerate(range(8))
    ])
    wind_rows = []
    raw_weights = [1.0 + 0.25 * math.cos(math.radians(direction - 45.0)) for direction in range(0, 360, 22)]
    weight_sum = sum(raw_weights)
    for direction, weight in zip(range(0, 360, 22), raw_weights):
        wind_rows.append([direction, round(weight / weight_sum, 10), round(2.0 + 0.8 * math.sin(math.radians(direction)) ** 2, 8), "deterministic_local_climatology"])
    _write_csv(asset_dir / "wind_climate.csv", ["direction_deg", "probability", "reference_speed_m_s", "source_type"], wind_rows)
    _write_json(asset_dir / "reference_diagnostics.json", {
        "task_id": task_id,
        "source_type": "deterministic_local_numerical_reference",
        "experimental_claim": False,
        "diagnostics": {
            "reference_strouhal": 0.11,
            "reference_reattachment_length_D": 2.7,
            "reference_spectral_peak_hz": 0.2,
            "reference_integral_length_scale_m": 0.85,
            "reference_low_wind_threshold_m_s": 0.6,
            "reference_high_wind_threshold_m_s": 3.0,
            "vortex_centers_m": [[2.0, 0.5, 1.0], [5.0, 2.0, 1.5]],
            "graph_edges": [["block-01", "block-02", 0.35], ["block-02", "block-03", 0.28]],
        },
    })
    _write_json(asset_dir / "optional_public_metadata.json", {
        "name": "AIJ Urban Wind Environment Case A",
        "record_url": "https://zenodo.org/records/15050148",
        "mode": "optional_one_click_download",
        "required_for_task": False,
        "fallback": "Use bundled deterministic local inputs when offline or when the optional record is not selected.",
        "bundled": False,
    })
    _write_json(asset_dir / "runtime_contract.json", {
        "schema_version": "1.0",
        "task_id": task_id,
        "runtime_image_status": "to_be_pinned_before_reference_generation",
        "required_software": software,
        "version_record_fields": ["name", "version", "executable_path", "sha256", "container_digest"],
        "reference_runtime_label": "urbanwind-reference-runtime",
        "candidate_runtime_label": "urbanwind-candidate-runtime",
        "note": "This local file is a version-record contract, not evidence that the software has run.",
    })
    _write_json(asset_dir / "source_inventory.json", {
        "schema_version": "1.0",
        "task_id": task_id,
        "assets": [
            {"id": "local-generated-inputs", "role": "required_task_input", "availability": "bundled", "origin": GENERATOR_VERSION, "experimental": False},
            {"id": "local-numerical-reference", "role": "visible_validation_input", "availability": "bundled", "origin": "deterministic_formula", "experimental": False},
            {"id": "aij-case-a", "role": "optional_empirical_enhancement", "availability": "one_click_public_optional", "url": "https://zenodo.org/records/15050148", "required": False},
        ],
    })

    local_files: list[Path] = [asset_dir / "case_matrix.csv", asset_dir / "case_control_contract.json"]
    if track_number == 1:
        _write_text(
            asset_dir / "source_spec.md",
            f"# {title}\n\n本文件由{GENERATOR_VERSION}本地生成。所有数值均为任务输入合同，不是实验测量。\n\n"
            "## 冻结锚点参数\n\n```json\n"
            + json.dumps(anchor.get("known", {}), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n```\n",
        )
        _write_json(asset_dir / "field_schema.json", {"required": ["case_id", "source", "unit", "coordinate_system", "value"], "task_id": task_id})
        local_files.extend([asset_dir / "source_spec.md", asset_dir / "field_schema.json"])
    if track_number in {2, 3, 4, 5, 6, 7, 9, 10}:
        _write_json(asset_dir / "city_geometry.geojson", _geometry(task_number))
        local_files.append(asset_dir / "city_geometry.geojson")
    if track_number in {2, 6, 9, 10}:
        _write_csv(asset_dir / "terrain_and_surface.csv", ["x_m", "y_m", "terrain_z_m", "roughness_m", "albedo", "heat_capacity_j_m2_k"], [
            [x, y, "" if task_number == 27 and (x, y) in {(10, 10), (15, 10), (10, 15)} else round(0.2 * math.sin(x / 8.0) * math.cos(y / 8.0), 6), 0.1 + 0.02 * ((x + y) % 3), 0.18 + 0.02 * (x % 4), 1.5e6]
            for x in range(0, 40, 5) for y in range(0, 40, 5)
        ])
        local_files.append(asset_dir / "terrain_and_surface.csv")
    if track_number in {3, 5, 7, 10} or "openfoam" in software:
        _write_openfoam_template(asset_dir / "openfoam_base")
        if task_number == 42:
            from task042_mesh_inputs import generate as generate_snappy_inputs
            generate_snappy_inputs(asset_dir / "openfoam_base")
        if task_number == 44:
            from task044_mesh_inputs import generate as generate_wake_template
            generate_wake_template(asset_dir / "openfoam_base")
        if task_number == 46:
            from task046_mesh_inputs import generate as generate_periodic_template, geojson
            generate_periodic_template(asset_dir / "openfoam_base")
            _write_json(asset_dir / "city_geometry.geojson", geojson())
        local_files.extend(path for path in (asset_dir / "openfoam_base").rglob("*") if path.is_file())
    if track_number == 6 or "palm" in software or "palm_csd" in software:
        _write_palm_template(asset_dir / "palm_base")
        local_files.extend(path for path in (asset_dir / "palm_base").rglob("*") if path.is_file())
    if track_number in {4, 5, 6, 7, 8, 9, 10}:
        profile_rows = []
        for z in [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0]:
            speed = 1.2 * (z / 4.0) ** 0.2
            profile_rows.append([z, round(speed, 8), round(0.12 * speed, 8), 300.0 + 0.003 * z])
        _write_csv(asset_dir / "boundary_profile.csv", ["z_m", "u_mean_m_s", "u_rms_m_s", "potential_temperature_k"], profile_rows)
        local_files.append(asset_dir / "boundary_profile.csv")
    if track_number in {5, 8, 9}:
        rows = []
        for index in range(64):
            x = -2.0 + 0.25 * index
            u = 1.2 * (1.0 - 0.55 * math.exp(-((x - 2.0) / 2.5) ** 2))
            rows.append([index, round(x, 6), 0.5, 1.0, round(u, 8), round(0.03 * math.sin(index / 5), 8), round(0.08 + 0.02 * math.cos(index / 7), 8), "synthetic_numerical_observation"])
        _write_csv(asset_dir / "reference_observations.csv", ["point_id", "x_m", "y_m", "z_m", "u_m_s", "v_m_s", "tke_m2_s2", "truth_type"], rows)
        local_files.append(asset_dir / "reference_observations.csv")
    if init_type == "broken_case":
        fault_payloads = {
            21: ("self_intersecting_polygon", "local_assets/city_geometry.geojson", {"feature": "b-00-00"}),
            24: ("overlapping_buildings", "local_assets/city_geometry.geojson", {"features": ["b-00-00", "b-00-01"]}),
            27: ("terrain_voids", "local_assets/terrain_and_surface.csv", {"void_cells": [[10, 10], [15, 10], [10, 15]]}),
            56: ("millimetre_metre_scale_error", "local_assets/openfoam_base/system/blockMeshDict", {"declared_scale": 0.001}),
            57: ("surface_normal_and_closure_error", "local_assets/openfoam_base/constant/triSurface/building.stl", {"symptom": "open_or_reversed_faces"}),
            63: ("roughness_wall_function_mismatch", "local_assets/boundary_profile.csv", {"inlet_z0_m": 0.1, "wall_z0_m": 0.01}),
            71: ("outlet_reverse_flow_instability", "local_assets/openfoam_base/0/U", {"symptom": "negative_outlet_normal_velocity"}),
            121: ("mesh_hole_leak", "local_assets/openfoam_base/constant/triSurface/building.stl", {"symptom": "open_surface_leak"}),
            122: ("high_non_orthogonality", "local_assets/mesh_levels.csv", {"observed_max_non_orthogonality_deg": 83.0}),
            123: ("negative_cell_volume", "local_assets/mesh_levels.csv", {"negative_cell_count": 4}),
            124: ("boundary_patch_name_mismatch", "local_assets/openfoam_base/system/blockMeshDict", {"expected": "inlet", "actual": "inlt"}),
            125: ("millimetre_metre_scale_error", "local_assets/openfoam_base/system/blockMeshDict", {"declared_scale": 0.001}),
            126: ("inlet_reference_height_offset", "local_assets/boundary_profile.csv", {"contract_reference_height_m": 4.0, "applied_reference_height_m": 3.0}),
            127: ("roughness_inconsistency", "local_assets/boundary_profile.csv", {"inlet_z0_m": 0.1, "ground_z0_m": 0.03}),
            128: ("pressure_divergence", "local_assets/openfoam_base/system/fvSolution", {"symptom": "unbounded_pressure_residual"}),
            129: ("courant_number_explosion", "local_assets/openfoam_base/system/controlDict", {"deltaT_s": 2.0, "target_Co_max": 0.8}),
            130: ("invalid_sgs_parameter", "local_assets/openfoam_base/system/fvSchemes", {"coefficient": -0.2}),
            131: ("undersized_domain", "local_assets/openfoam_base/system/blockMeshDict", {"downstream_margin_D": 2.0}),
            132: ("outlet_reverse_flow", "local_assets/openfoam_base/0/U", {"reverse_area_fraction": 0.25}),
            133: ("mass_flux_imbalance", "local_assets/reference_diagnostics.json", {"inlet_flux": 1.0, "outlet_flux": 0.82}),
            134: ("statistics_window_too_short", "local_assets/velocity_timeseries.csv", {"available_duration_s": 51.1, "required_duration_s": 200.0}),
            135: ("missing_probe_and_plane", "local_assets/task_input_values.json", {"missing_objects": ["wake_probe", "pedestrian_plane"]}),
            136: ("incomplete_parallel_reconstruction", "local_assets/task_input_values.json", {"missing_processor_time": "processor3/10/U"}),
            137: ("palm_static_driver_dimension_mismatch", "local_assets/palm_base/static_driver_table.csv", {"x": 8, "y": 7}),
            138: ("palm_nonfinite_output", "local_assets/reference_field.csv", {"nonfinite_variable": "temperature", "sample": 17}),
            139: ("restart_time_axis_gap", "local_assets/velocity_timeseries.csv", {"gap_start_s": 25.5, "gap_duration_s": 1.0}),
            140: ("cross_software_axis_flip", "local_assets/city_geometry.geojson", {"axis": "y", "operation": "sign_flip"}),
        }
        fault_family, target_file, parameters = fault_payloads.get(task_number, ("task_specific_injected_fault", "local_assets/task_input_values.json", {"seed": 202609030000 + task_number}))
        _write_json(asset_dir / "fault_manifest.json", {
            "task_id": task_id,
            "fault_seed": 202609030000 + task_number,
            "fault_family": fault_family,
            "target_file": target_file,
            "observed_fault_parameters": parameters,
            "injected": True,
            "expected_agent_action": "diagnose and repair without changing unrelated settings",
            "answer_included": False,
        })
        local_files.append(asset_dir / "fault_manifest.json")
    if track_number == 10:
        candidate_rows = []
        for index in range(20):
            angle = (index % 5) * 11.25
            spacing = 1.0 + (index // 5) * 0.25
            ventilation = 0.45 + 0.18 * math.sin(index * 0.7) + rng.uniform(-0.015, 0.015)
            heat = 0.55 - 0.12 * math.cos(index * 0.5) + rng.uniform(-0.015, 0.015)
            risk = 0.20 + 0.10 * abs(math.sin(index * 0.4))
            candidate_rows.append([f"candidate-{index + 1:02d}", angle, spacing, round(ventilation, 8), round(heat, 8), round(risk, 8)])
        _write_csv(asset_dir / "candidate_library.csv", ["candidate_id", "orientation_deg", "spacing_H", "ventilation_proxy", "heat_penalty", "high_wind_risk"], candidate_rows)
        _write_json(asset_dir / "constraints.json", {"max_high_wind_risk": 0.30, "min_spacing_H": 1.0, "objective_directions": {"ventilation_proxy": "maximize", "heat_penalty": "minimize"}})
        local_files.extend([asset_dir / "candidate_library.csv", asset_dir / "constraints.json"])

    task_input_values = {
        "schema_version": "1.0",
        "task_id": task_id,
        "source_type": "deterministic_local_generation",
        "experimental_claim": False,
        "roles": [
            {
                "role_id": f"input-{index:03d}",
                "label": role,
                "seed": 202609030000 + task_number * 100 + index,
                "values": [round(rng.uniform(0.05, 1.0), 8) for _ in range(16)],
                "value_semantics": "local_contract_parameter_or_synthetic_reference_component",
            }
            for index, role in enumerate(required_inputs, start=1)
        ],
    }
    _write_json(asset_dir / "task_input_values.json", task_input_values)

    def role_paths(role: str) -> list[str]:
        key = role.lower()
        candidates: list[str]
        if "来源文档" in role:
            candidates = ["local_assets/source_spec.md"]
        elif "字段Schema" in role:
            candidates = ["local_assets/field_schema.json"]
        elif "运行版本" in role or "软件版本" in role:
            candidates = ["local_assets/runtime_contract.json"]
        elif any(token in role for token in ("能谱", "频带", "窗函数")):
            candidates = ["local_assets/velocity_timeseries.csv", "local_assets/reference_spectrum.csv", "local_assets/reference_diagnostics.json"]
        elif any(token in role for token in ("时间序列", "采样频率", "阵风", "Bootstrap", "块长度", "统计窗口")):
            candidates = ["local_assets/velocity_timeseries.csv", "local_assets/reference_diagnostics.json"]
        elif any(token in role for token in ("风气候", "风向权重", "联合分布", "多风向评价权重")):
            candidates = ["local_assets/wind_climate.csv"]
        elif any(token in role for token in ("掩膜", "像元", "人口", "道路", "测点", "禁布区", "面积权重", "位置")):
            candidates = ["local_assets/mask_and_weights.csv"]
        elif any(token in role for token in ("网格", "加密比", "体素", "粗中细")):
            candidates = ["local_assets/mesh_levels.csv", "local_assets/mesh_solution_series.csv"]
        elif "openfoam" in key:
            candidates = ["local_assets/openfoam_base/system/controlDict", "local_assets/openfoam_base/system/blockMeshDict"]
        elif any(token in key for token in ("palm", "netcdf")):
            candidates = ["local_assets/palm_base/urbanwind_p3d", "local_assets/palm_base/static_driver_table.csv"]
        elif any(token in role for token in ("几何", "GIS", "建筑", "城市", "地形", "街区", "廊道", "用地", "表面")):
            candidates = ["local_assets/city_geometry.geojson", "local_assets/terrain_and_surface.csv"]
        elif any(token in role for token in ("候选", "Pareto", "搜索空间", "约束", "方案", "设计")):
            candidates = ["local_assets/candidate_library.csv", "local_assets/constraints.json", "local_assets/active_learning_data.csv"]
        elif any(token in role for token in ("边界", "入口", "剖面", "热力强迫", "热通量")):
            candidates = ["local_assets/boundary_profile.csv", "local_assets/case_matrix.csv"]
        elif any(token in role for token in ("场", "观测", "应力", "通量", "浓度", "温度", "热负荷", "暴露", "涡", "剪切", "源项", "动量")):
            candidates = ["local_assets/model_field.csv", "local_assets/reference_field.csv", "local_assets/reference_diagnostics.json"]
        elif any(token in role for token in ("参考", "冻结", "规则", "算法", "版本", "门槛", "效用", "指标")):
            candidates = ["local_assets/reference_diagnostics.json", "local_assets/task_input_values.json"]
        else:
            candidates = ["local_assets/task_input_values.json"]
        existing = [candidate for candidate in candidates if (init_dir / candidate).is_file()]
        return existing or ["local_assets/task_input_values.json"]

    _write_json(asset_dir / "input_role_manifest.json", {
        "schema_version": "1.0",
        "task_id": task_id,
        "generator_version": GENERATOR_VERSION,
        "required_external_inputs": [],
        "all_required_roles_materialized": True,
        "roles": [
            {"role_id": f"input-{index:03d}", "label": role, "paths": role_paths(role), "origin": "deterministic_local_generation"}
            for index, role in enumerate(required_inputs, start=1)
        ],
    })

    optional_public = [{
        "name": "AIJ UWE Case A",
        "record_url": "https://zenodo.org/records/15050148",
        "mode": "optional_one_click_download",
        "required_for_task": False,
    }]
    generated = sorted(path for path in asset_dir.rglob("*") if path.is_file())
    _write_json(asset_dir / "generation_manifest.json", {
        "schema_version": "1.0",
        "generator_version": GENERATOR_VERSION,
        "task_id": task_id,
        "task_number": task_number,
        "anchor_name": anchor["name"],
        "seed": 202609030000 + task_number,
        "data_origin": "deterministic_local_generation",
        "experimental_claim": False,
        "answer_free": True,
        "optional_public_data": optional_public,
        "files": [{"path": path.relative_to(init_dir).as_posix(), "bytes": path.stat().st_size, "sha256": _sha256(path)} for path in generated],
    })
    return sorted(path for path in asset_dir.rglob("*") if path.is_file())
