from __future__ import annotations

import csv
import math
import re
from pathlib import Path

from .common import confined, digest, load
from .state import case_directories


TARGET_COLUMNS = {
    "aerodynamic_roughness": {"roughness_length_m"},
    "wind_direction": {"wind_direction_deg"},
    "atmospheric_stability": {"stability", "surface_heat_flux_w_m2"},
    "thermal_forcing": {"surface_heat_flux_w_m2"},
    "surface_heat_flux": {"surface_heat_flux_w_m2"},
    "sgs_model": {"sgs_model"}, "time_step": {"time_step_s"},
    "convection_scheme": {"convection_scheme"},
    "pressure_velocity_coupling": {"pressure_velocity_coupling"},
    "domain_decomposition": {"decomposition_method"},
    "decomposition_method": {"decomposition_method"},
    "restart_segmentation": {"restart_segments"}, "restart": {"restart_segments"},
    "source_configuration": {"source_configuration"},
    "scalar_source_configuration": {"source_configuration"},
    "inflow_profile_law": {"inflow_profile_law"},
    "top_boundary_condition": {"top_boundary_condition"},
    "software_variant": {"software_variant"},
}
IGNORED_COLUMNS = {"case_id", "variant", "controlled_factor", "controlled_level"}


def matrix_constraints(rows, controlled_factor):
    """Validate the public plan; actual solver configuration is checked separately."""
    errors = []
    if len({r.get("case_id") for r in rows}) != len(rows):
        errors.append("duplicate_case_id")
    allowed = TARGET_COLUMNS.get(controlled_factor)
    if len(rows) < 2:
        return {"status": "single_case", "errors": errors}
    if allowed is None:
        return {"status": "unknown_factor", "errors": errors, "controlled_factor": controlled_factor}
    for field in set().union(*(set(row) for row in rows)) - allowed - IGNORED_COLUMNS:
        values = {str(row.get(field, "")) for row in rows}
        if len(values) > 1:
            errors.append("non_target_diff:" + field)
    return {"status": "pass" if not errors else "violation", "errors": errors,
            "controlled_factor": controlled_factor, "allowed_columns": sorted(allowed)}


def strip_comments(text):
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)


def foam_values(text, names):
    text = strip_comments(text)
    return {name: re.findall(r"(?<!\w)"+re.escape(name)+r"\s+([^;{}]+);", text)
            for name in names if re.search(r"(?<!\w)"+re.escape(name)+r"\s+[^;{}]+;", text)}


def semantic_text(text):
    text = strip_comments(text)
    text = re.sub(r"\bFoamFile\s*\{[^{}]*\}", "", text)
    number = r"(?<![\w.])[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?(?![\w.])"
    return " ".join(re.sub(number, lambda match: format(float(match.group()), '.15g'), text).split())


def dictionary_block(text, name):
    text = strip_comments(text)
    match = re.search(r"\b"+re.escape(name)+r"\s*\{", text)
    if not match:
        return None
    depth, start = 1, match.end()
    for index in range(start, len(text)):
        depth += (text[index] == '{')-(text[index] == '}')
        if depth == 0:
            return semantic_text(text[start:index])
    return None


def mesh_comparison_text(text, controlled_factor):
    text = strip_comments(text)
    if controlled_factor == 'top_boundary_condition':
        # Only the declared target's patch TYPE is derived; its faces, vertices,
        # block sizes and every other patch remain in the comparison.
        match = re.search(r'\btop\s*\{', text)
        if match:
            depth, end = 1, match.end()
            while end < len(text) and depth:
                depth += (text[end] == '{') - (text[end] == '}')
                end += 1
            if depth == 0:
                patch = re.sub(r'\btype\s+\w+\s*;', 'type DECLARED_TOP_TARGET;',
                               text[match.end():end-1], count=1)
                text = text[:match.end()] + patch + text[end-1:]
    return semantic_text(text)


def actual_configuration(case: Path, controlled_factor=None):
    result = {}
    fields = {"system/controlDict": ("deltaT", "endTime", "application"),
              "constant/turbulenceProperties": ("simulationType", "RASModel", "LESModel"),
              "constant/momentumTransport": ("simulationType", "model"),
              "0/U": ("Uref", "Zref", "z0", "flowDir", "internalField")}
    for relative, names in fields.items():
        path = case / relative
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(case.resolve()):
            for key, value in foam_values(path.read_text(errors="replace"), names).items():
                if key != 'internalField':
                    result[relative+":"+key] = [semantic_text(item) for item in value]
            if relative == '0/U' and '0/U:Uref' not in result:
                raw = foam_values(path.read_text(errors='replace'), ('internalField',)).get('internalField', [])
                if raw and raw[0].strip().startswith('uniform ('):
                    vector = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", raw[0])
                    if len(vector) == 3:
                        result['0/U:uniform_speed_m_s'] = format(math.sqrt(sum(float(v)**2 for v in vector)), '.12g')
                inlet=dictionary_block(path.read_text(errors='replace'),'inlet')
                if inlet:
                    match=re.search(r'value\s+uniform\s*\(([^)]+)\)',inlet)
                    patch_type = re.search(r'\btype\s+(\w+)\s*;', inlet)
                    # A coded/time-varying BC's value uniform is an initial
                    # placeholder, not an observed reference inflow speed.
                    if match and patch_type and patch_type.group(1) == 'fixedValue':
                        vector=match.group(1).split()
                        try:
                            if len(vector)==3:
                                result['0/U:inlet_uniform_speed_m_s']=format(math.sqrt(sum(float(v)**2 for v in vector)),'.12g')
                        except ValueError:
                            pass
    for relative in ("system/blockMeshDict",):
        path = case / relative
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(case.resolve()):
            result[relative+":semantic_sha256"] = __import__("hashlib").sha256(
                mesh_comparison_text(path.read_text(errors="replace"), controlled_factor).encode()).hexdigest()
    path=case/'system/fvSolution'
    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(case.resolve()):
        for name in ('p','pFinal','U','UFinal','PIMPLE','PISO','SIMPLE'):
            value=dictionary_block(path.read_text(errors='replace'),name)
            if value is not None:
                result['system/fvSolution:'+name]=value
    path = case/'system/fvSchemes'
    if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(case.resolve()):
        text = path.read_text(errors='replace')
        for name in ('ddtSchemes','gradSchemes','divSchemes','laplacianSchemes','interpolationSchemes','snGradSchemes'):
            value = dictionary_block(text,name)
            if value is not None:
                result['system/fvSchemes:'+name] = value
    return result


def actual_comparison(cases, controlled_factor):
    """Only checks fields that are observed in every matched actual case.

    Missing fields are unknown, not a certificate of full configuration equality.
    Target/derived quantities not covered by this parser remain for domain audit.
    """
    if len(cases) < 2:
        return {"status": "insufficient_actual_cases", "errors": [], "coverage": []}
    observed = {name: actual_configuration(path, controlled_factor) for name, path in cases.items()}
    common = set.intersection(*(set(value) for value in observed.values()))
    union = set.union(*(set(value) for value in observed.values()))
    ignored = set()
    if controlled_factor == "aerodynamic_roughness":
        ignored.add("0/U:z0")
    if controlled_factor in ("wind_direction", "wind_direction_deg"):
        ignored.add("0/U:flowDir")
    if controlled_factor in ("time_step", "time_step_s"):
        ignored.add("system/controlDict:deltaT")
    if controlled_factor == "sgs_model":
        ignored.update(k for k in common if k.endswith(":LESModel") or k.endswith(":model"))
    if controlled_factor == "convection_scheme":
        ignored.add("system/fvSchemes:divSchemes")
    if controlled_factor == 'restart_segmentation':
        # Segment stop times may differ during a legitimate planned restart;
        # final common physical endpoint is checked by the independent grader.
        ignored.add('system/controlDict:endTime')
    checked = sorted(common - ignored)
    errors = ["inconsistent_field_presence:"+key for key in sorted(union-common)]
    for key in checked:
        values=[value[key] for value in observed.values()]
        if key=='system/fvSchemes:divSchemes' and controlled_factor=='sgs_model':
            # Transport equations of model-specific turbulence scalars are
            # allowed derived differences; U discretization stays frozen.
            values=[semantic_text(re.sub(r'div\(phi,(?:k|epsilon|omega|nuTilda)\)\s+[^;]+;', '',value)) for value in values]
        signatures = {str(value).strip() for value in values}
        if len(signatures) > 1:
            errors.append("actual_non_target_diff:" + key)
    return {"status": "violation" if errors else "partial_consistency",
            "errors": errors, "coverage": checked, "unverified": "Other fields and allowed derived changes are not certified. Inflow profiles with Uref are compared by Uref, not local profile-dependent velocities.",
            "case_names": sorted(cases)}


def expected_configuration_checks(cases,rows):
    errors,coverage=[],[]
    by_id={row.get('case_id'):row for row in rows}
    mappings={'reference_speed_m_s':('0/U:Uref','0/U:inlet_uniform_speed_m_s'),
        'time_step_s':('system/controlDict:deltaT',), 'roughness_length_m':('0/U:z0',)}
    for case_id,case in cases.items():
        actual=actual_configuration(case)
        row=by_id.get(case_id,{})
        for field,keys in mappings.items():
            if not str(row.get(field,'')).strip():
                continue
            key=next((key for key in keys if key in actual),None)
            if key is None:
                continue
            try:
                values=actual[key] if isinstance(actual[key],list) else [actual[key]]
                expected=float(row[field])
                if not all(math.isclose(float(value),expected,rel_tol=1e-10,abs_tol=1e-12) for value in values):
                    errors.append('actual_diff_from_public_plan:'+case_id+':'+field)
                coverage.append(case_id+':'+field)
            except (TypeError,ValueError):
                errors.append('unresolved_observed_scalar:'+case_id+':'+field)
    return {'errors':errors,'coverage':coverage,'scope':'Only directly observed scalar controls; absent/derived quantities are not certified.'}


def public_observation(root: Path, immutable_inputs: Path | None = None):
    source = immutable_inputs or root
    contract = load(source / "reporting_contract.json")
    spec = load(source / "task_spec.json")
    artifacts = spec.get("required_artifacts", [])
    missing, malformed = [], []
    for item in artifacts:
        raw = item.get("path")
        try:
            path = confined(root, raw)
            if not path.exists() or (path.is_file() and path.stat().st_size == 0):
                missing.append(raw)
            elif item.get("format") == "json":
                load(path)
        except (ValueError, OSError, TypeError):
            malformed.append(raw)
    control_path = source / "local_assets/case_control_contract.json"
    control = load(control_path) if control_path.exists() else {}
    matrix_path = source / "local_assets/case_matrix.csv"
    with matrix_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    factor = control.get("controlled_factor", "unknown")
    plan = matrix_constraints(rows, factor)
    discovered = case_directories(root)
    by_id = {}
    for case in discovered:
        relative = case.relative_to(root).as_posix()
        # An explicit case_id in a resolved config is preferred. Unmatched
        # directories never become silently paired based on sort order.
        identities = []
        # The public contract's exact case ID in the directory name is also
        # unambiguous. Never pair directories based on ordering.
        if case.name in contract.get("expected_case_ids", []):
            identities.append(case.name)
        for name in ("resolved_config.json", "case_config.json", "config.json"):
            config = case / name
            if config.is_file() and not config.is_symlink() and config.resolve().is_relative_to(case.resolve()):
                try:
                    identity = load(config).get("case_id")
                    if identity in contract.get("expected_case_ids", []):
                        identities.append(identity)
                except (ValueError, OSError, AttributeError):
                    pass
        if len(set(identities)) == 1:
            identity = identities[0]
            if identity in by_id:
                malformed.append("duplicate_actual_case_identity:"+identity)
            else:
                by_id[identity] = case
    comparison = actual_comparison(by_id, factor)
    expected_checks=expected_configuration_checks(by_id,rows)
    comparison['expected_value_checks']=expected_checks
    comparison['errors']+=expected_checks['errors']
    unmatched = sorted(set(contract.get("expected_case_ids", []))-set(by_id))
    if len(rows) > 1 and unmatched:
        comparison["errors"].append("unmatched_actual_cases:"+",".join(unmatched))
    return {"missing_artifacts": missing, "malformed_artifacts": malformed,
            "public_plan_check": plan, "actual_comparison": comparison,
            "actual_case_directories": [p.relative_to(root).as_posix() for p in discovered],
            "matched_case_ids": sorted(by_id),
            "unmatched_case_ids": unmatched,
            "expected_case_ids": contract.get("expected_case_ids", []),
            "finish_gate_pass": not missing and not malformed and not plan["errors"] and not comparison["errors"],
            "scope": "Public checks only; not hidden score or full numerical validity."}
