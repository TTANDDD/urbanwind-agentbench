#!/usr/bin/env python3
"""Build the visible deterministic synthetic-inflow input for task-065.

This script creates OpenFOAM ``boundaryData`` from a frozen spectral contract.
It produces inputs and visible target statistics only; it does not create or
claim solver ground truth.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def time_name(value: float) -> str:
    if abs(value - round(value)) <= 1e-10:
        return str(int(round(value)))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def header(object_name: str, class_name: str, location: str) -> str:
    return (
        "FoamFile\n{\n"
        "    version 2.0;\n"
        "    format ascii;\n"
        f"    class {class_name};\n"
        f"    location \"{location}\";\n"
        f"    object {object_name};\n"
        "}\n"
    )


def write_points(path: Path, points: list[tuple[float, float, float]]) -> None:
    body = "\n".join(f"({x:.10g} {y:.10g} {z:.10g})" for x, y, z in points)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        header("points", "vectorField", "constant/boundaryData/inlet")
        + f"\n{len(points)}\n(\n{body}\n)\n",
        encoding="utf-8",
        newline="\n",
    )


def write_values(path: Path, values: list[list[float]], average: list[float]) -> None:
    body = "\n".join(f"({u:.10g} {v:.10g} {w:.10g})" for u, v, w in values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        header(
            "U",
            "vectorAverageField",
            f"constant/boundaryData/inlet/{path.parent.name}",
        )
        + f"\n{len(values)}\n(\n{body}\n)\n"
        + f"({average[0]:.10g} {average[1]:.10g} {average[2]:.10g})\n",
        encoding="utf-8",
        newline="\n",
    )


def patch_u(case_root: Path, mean: list[float]) -> None:
    text = f"""FoamFile{{version 2.0; format ascii; class volVectorField; object U;}}
dimensions [0 1 -1 0 0 0 0];
internalField uniform ({mean[0]:.10g} {mean[1]:.10g} {mean[2]:.10g});
boundaryField
{{
 inlet
 {{
  type timeVaryingMappedFixedValue;
  setAverage true;
  mapMethod nearest;
  offset (0 0 0);
  value uniform ({mean[0]:.10g} {mean[1]:.10g} {mean[2]:.10g});
 }}
 outlet{{type inletOutlet; inletValue uniform (0 0 0); value uniform ({mean[0]:.10g} {mean[1]:.10g} {mean[2]:.10g});}}
 sideLow{{type symmetryPlane;}}
 sideHigh{{type symmetryPlane;}}
 ground{{type noSlip;}}
 top{{type symmetryPlane;}}
 building{{type noSlip;}}
}}
"""
    path = case_root / "0/U"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def frequency_modes(contract: dict, component: int) -> list[dict]:
    period = float(contract["spectral_target"]["period_s"])
    seed = int(contract["seed"]) + 100000 * component
    result = []
    for band_index, (bounds, fraction) in enumerate(zip(
        contract["spectral_target"]["bands_hz"],
        contract["spectral_target"]["normalized_band_fractions"],
    )):
        low, high = map(float, bounds)
        bins = [index for index in range(1, int(period / (2 * contract["input_interval_s"])) + 1)
                if low <= index / period < high]
        if not bins:
            raise ValueError(f"empty spectral band {bounds}")
        for index in bins:
            rng = random.Random(seed + 4099 * index + 65537 * band_index)
            result.append({
                "frequency_hz": index / period,
                "energy": float(fraction) / len(bins),
                "phase": rng.uniform(0.0, 2.0 * math.pi),
                "ky": rng.choice([-3, -2, -1, 1, 2, 3]),
                "kz": rng.choice([-3, -2, -1, 1, 2, 3]),
            })
    return result


def build_values(contract: dict, end_time: float):
    x = float(contract["spatial_grid"]["x_m"])
    y_count = int(contract["spatial_grid"]["y_count"])
    z_count = int(contract["spatial_grid"]["z_count"])
    y0, y1 = map(float, contract["spatial_grid"]["y_range_m"])
    z0, z1 = map(float, contract["spatial_grid"]["z_range_m"])
    points = [
        (x, y0 + (y1 - y0) * yi / (y_count - 1), z0 + (z1 - z0) * zi / (z_count - 1))
        for yi in range(y_count) for zi in range(z_count)
    ]
    dt = float(contract["input_interval_s"])
    analysis_high = float(contract["analysis_window_s"][1])
    calibration_end = max(end_time, analysis_high)
    count = int(round(calibration_end / dt)) + 1
    output_count = int(round(end_time / dt)) + 1
    times = [index * dt for index in range(count)]
    modes = [frequency_modes(contract, component) for component in range(3)]
    y_span = y1 - y0
    z_span = z1 - z0
    raw = [[[0.0, 0.0, 0.0] for _ in points] for _ in times]
    for component in range(3):
        spatial = []
        for _, y, z in points:
            spatial.append([
                (math.cos(2 * math.pi * (mode["ky"] * (y - y0) / y_span + mode["kz"] * (z - z0) / z_span) + mode["phase"]),
                 math.sin(2 * math.pi * (mode["ky"] * (y - y0) / y_span + mode["kz"] * (z - z0) / z_span) + mode["phase"]))
                for mode in modes[component]
            ])
        temporal = [
            [(math.cos(2 * math.pi * mode["frequency_hz"] * time),
              math.sin(2 * math.pi * mode["frequency_hz"] * time)) for mode in modes[component]]
            for time in times
        ]
        amplitude = [math.sqrt(2.0 * mode["energy"]) for mode in modes[component]]
        for ti in range(len(times)):
            for pi in range(len(points)):
                raw[ti][pi][component] = sum(
                    amp * (space[0] * time_value[0] - space[1] * time_value[1])
                    for amp, space, time_value in zip(amplitude, spatial[pi], temporal[ti])
                )
            spatial_mean = sum(raw[ti][pi][component] for pi in range(len(points))) / len(points)
            for pi in range(len(points)):
                raw[ti][pi][component] -= spatial_mean

    analysis_low, analysis_high = map(float, contract["analysis_window_s"])
    selected_times = [index for index, time in enumerate(times) if analysis_low < time <= analysis_high + 1e-10]
    rms = list(map(float, contract["target_rms_m_s"]))
    scale = []
    for component in range(3):
        variance = sum(raw[ti][pi][component] ** 2 for ti in selected_times for pi in range(len(points))) / (len(selected_times) * len(points))
        if variance <= 1e-16:
            raise ValueError("degenerate synthetic component")
        scale.append(rms[component] / math.sqrt(variance))
    mean = list(map(float, contract["mean_velocity_m_s"]))
    values = [
        [[mean[component] + scale[component] * raw[ti][pi][component] for component in range(3)]
         for pi in range(len(points))]
        for ti in range(len(times))
    ]
    if min(vector[0] for frame in values for vector in frame) <= 0.2:
        raise ValueError("streamwise synthetic inlet contains a frozen nonphysical reversal")
    return times[:output_count], points, values[:output_count]


def band_statistics(series: list[float], dt: float, bands: list[list[float]]) -> dict:
    count = len(series)
    mean = sum(series) / count
    centered = [value - mean for value in series]
    variance = sum(value * value for value in centered) / count
    energies = []
    frequencies = []
    for index in range(1, count // 2 + 1):
        real = sum(value * math.cos(-2 * math.pi * index * sample / count) for sample, value in enumerate(centered))
        imag = sum(value * math.sin(-2 * math.pi * index * sample / count) for sample, value in enumerate(centered))
        energies.append(real * real + imag * imag)
        frequencies.append(index / (count * dt))
    total = sum(energies)
    fractions = [
        sum(energy for frequency, energy in zip(frequencies, energies) if float(low) <= frequency < float(high)) / total
        for low, high in bands
    ] if total > 1e-30 else [0.0 for _ in bands]
    denominator = sum(value * value for value in centered)
    positive = 0.0
    if denominator > 1e-30:
        for lag in range(count):
            correlation = sum(centered[index] * centered[index + lag] for index in range(count - lag)) / denominator
            if lag > 0 and correlation <= 0:
                break
            positive += correlation
    return {
        "mean_m_s": mean,
        "variance_m2_s2": variance,
        "normalized_psd_band_fractions": fractions,
        "integral_time_scale_s": dt * max(positive - 0.5, 0.0),
    }


def target_outputs(case_root: Path, contract: dict, times, values) -> None:
    probe_table = case_root / "synthetic_inflow_probe_points.csv"
    with probe_table.open(encoding="utf-8", newline="") as handle:
        probes = list(csv.DictReader(handle))
    indices = [int(row["boundary_point_index"]) for row in probes]
    low, high = map(float, contract["analysis_window_s"])
    selected = [ti for ti, time in enumerate(times) if low < time <= high + 1e-10]
    if len(selected) != int(round((high - low) / contract["input_interval_s"])):
        raise ValueError("analysis sample count does not match frozen contract")
    target_series = case_root / "synthetic_inflow_target_probe_series.csv"
    with target_series.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", "probe_id", "boundary_point_index", "Ux_m_s", "Uy_m_s", "Uz_m_s"])
        for ti in selected:
            for probe, point_index in zip(probes, indices):
                writer.writerow([f"{times[ti]:.10g}", probe["probe_id"], point_index,
                                 *(f"{value:.12g}" for value in values[ti][point_index])])
    bands = contract["spectral_target"]["bands_hz"]
    statistics = []
    for probe, point_index in zip(probes, indices):
        item = {"probe_id": probe["probe_id"], "boundary_point_index": point_index, "components": {}}
        for component, name in enumerate(("Ux", "Uy", "Uz")):
            series = [values[ti][point_index][component] for ti in selected]
            item["components"][name] = band_statistics(series, float(contract["input_interval_s"]), bands)
        statistics.append(item)
    write_json(case_root / "synthetic_inflow_target_statistics.json", {
        "task_id": contract["task_id"],
        "case_id": contract["case_id"],
        "analysis_window_s": contract["analysis_window_s"],
        "sample_count": len(selected),
        "sampling_interval_s": contract["input_interval_s"],
        "bands_hz": bands,
        "probe_statistics": statistics,
        "truth_boundary": contract["truth_boundary"],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--probe-table", type=Path)
    parser.add_argument("--end-time", type=float)
    args = parser.parse_args()
    case_root = args.case_root.resolve()
    contract_path = (args.contract or Path(__file__).with_name("synthetic_inflow_contract.json")).resolve()
    probe_source = (args.probe_table or Path(__file__).with_name("synthetic_inflow_probe_points.csv")).resolve()
    contract = load(contract_path)
    end_time = float(args.end_time if args.end_time is not None else contract["full_end_time_s"])
    if end_time <= 0 or end_time > float(contract["full_end_time_s"]):
        raise ValueError("end_time outside frozen input extent")
    case_root.mkdir(parents=True, exist_ok=True)
    (case_root / "synthetic_inflow_probe_points.csv").write_bytes(probe_source.read_bytes())
    (case_root / "synthetic_inflow_contract.json").write_bytes(contract_path.read_bytes())
    times, points, values = build_values(contract, end_time)
    boundary = case_root / "constant/boundaryData/inlet"
    write_points(boundary / "points", points)
    for time, frame in zip(times, values):
        write_values(boundary / time_name(time) / "U", frame, contract["mean_velocity_m_s"])
    patch_u(case_root, contract["mean_velocity_m_s"])
    if end_time >= float(contract["analysis_window_s"][1]):
        target_outputs(case_root, contract, times, values)
    files = [path for path in sorted(case_root.rglob("*")) if path.is_file()]
    write_json(case_root / "synthetic_inflow_generation_manifest.json", {
        "task_id": contract["task_id"],
        "case_id": contract["case_id"],
        "seed": contract["seed"],
        "end_time_s": end_time,
        "input_interval_s": contract["input_interval_s"],
        "boundary_point_count": len(points),
        "time_slice_count": len(times),
        "files": [{"path": path.relative_to(case_root).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)} for path in files],
        "experimental_claim": False,
    })
    print(json.dumps({"status": "PASS", "case_root": str(case_root), "points": len(points), "time_slices": len(times)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
