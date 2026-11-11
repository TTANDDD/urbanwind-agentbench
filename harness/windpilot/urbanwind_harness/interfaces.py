"""Publish serialization interfaces, never reference data or computed answers."""
from __future__ import annotations
import ast
import json
from pathlib import Path


def literal_root_path(node):
    if isinstance(node, ast.Name) and node.id == 'root':
        return []
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = literal_root_path(node.left)
        if left is not None and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return left + [node.right.value]
    return None


def compile_interfaces(source: str, report: dict, contracts: list[dict]) -> dict:
    tree = ast.parse(source)
    loaded = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not call.args:
            continue
        if isinstance(call.func, ast.Name) and call.func.id == 'load':
            parts = literal_root_path(call.args[0])
        elif (isinstance(call.func, ast.Attribute) and call.func.attr == 'loads'
              and isinstance(call.args[0], ast.Call)
              and isinstance(call.args[0].func, ast.Attribute)
              and call.args[0].func.attr == 'read_text'):
            parts = literal_root_path(call.args[0].func.value)
        else:
            continue
        if not parts or len(parts) != 1 or not parts[0].endswith('.json'):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                loaded[target.id] = parts[0]
    loaded = {k: v for k, v in loaded.items() if v not in ('metrics.json', 'task_spec.json', 'reporting_contract.json')}
    documents = {}
    for owner, filename in loaded.items():
        fields, containers = set(), set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == owner
                    and node.func.attr == 'get' and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                field = node.args[0].value
                fields.add(field)
                if field in ('case', 'case_result', 'cases', 'case_results'):
                    containers.add(field)
        documents[filename] = {'root_fields_read': sorted(fields), 'case_containers': sorted(containers)}
    # Follow public JSON-object aliases, so nested primary-metric and evidence
    # wrappers are visible without exporting grader expressions or values.
    aliases = {name: (filename, ()) for name, filename in loaded.items()}
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    for _ in range(4):
        for node in assignments:
            call = node.value
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == 'get' and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in aliases and call.args
                    and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                filename, pointer = aliases[call.func.value.id]
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases[target.id] = (filename, pointer + (call.args[0].value,))
    nested_objects = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get' and isinstance(node.func.value, ast.Name)
                and node.func.value.id in aliases and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            filename, pointer = aliases[node.func.value.id]
            if pointer:
                nested_objects.setdefault(filename, {}).setdefault('/'.join(pointer), set()).add(node.args[0].value)
    literal_sequences = {}
    for node in assignments:
        if isinstance(node.value, (ast.Tuple, ast.List)) and all(
                isinstance(n, ast.Constant) and isinstance(n.value, str) for n in node.value.elts):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    literal_sequences[target.id] = [n.value for n in node.value.elts]
    evidence_mappings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.GeneratorExp) and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'get'
                and c.args and isinstance(c.args[0], ast.Constant) and c.args[0].value == 'task_id'
                for c in ast.walk(node.elt)):
            for generator in node.generators:
                for owner in getattr(generator.iter, 'elts', []):
                    if isinstance(owner, ast.Name) and owner.id in loaded:
                        fields = documents[loaded[owner.id]]['root_fields_read']
                        if 'task_id' not in fields:
                            fields.append('task_id')
                            fields.sort()
    case_fields, array_checks = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ('case', 'submitted', 'reported', 'evidence_case', 'reported_case')
                and node.func.attr == 'get' and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            case_fields.add(node.args[0].value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.startswith('finite_') and node.args
                and any(isinstance(item, ast.Attribute) and item.attr == 'get' for item in ast.walk(node.args[0]))):
            array_checks.add(ast.unparse(node))
    raw_roles = sorted({role for contract in contracts
                        for key in ('required_raw_evidence_per_case', 'required_raw_evidence')
                        for role in contract.get(key, []) if isinstance(role, str)})
    # Export field names and serialization enums only. Never export evaluator
    # expressions, reference values, scales, thresholds or computed answers.
    nested_fields, object_fields, enum_values = {}, {}, {}
    helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in helpers and node.args:
            arg = node.args[0]
            if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and arg.func.attr == 'get' and arg.args
                    and isinstance(arg.args[0], ast.Constant)
                    and isinstance(arg.args[0].value, str)):
                key = arg.args[0].value
                if 'diagnostic' in key:
                    required = set()
                    for child in ast.walk(helpers[node.func.id]):
                        if isinstance(child, ast.Dict):
                            required.update(k.value for k in child.keys
                                            if isinstance(k, ast.Constant) and isinstance(k.value, str))
                        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                                and child.func.attr == 'get' and child.args
                                and isinstance(child.args[0], ast.Constant)
                                and isinstance(child.args[0].value, str)):
                            required.add(child.args[0].value)
                    nested_fields.setdefault(key, set()).update(required)
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            names = [n.value for n in getattr(node.iter, 'elts', [])
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if isinstance(node.iter, ast.Name):
                names = literal_sequences.get(node.iter.id, [])
            if names:
                for child in ast.walk(node):
                    if (isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name)
                            and child.value.id in aliases and isinstance(child.slice, ast.Name)
                            and child.slice.id == node.target.id):
                        filename, pointer = aliases[child.value.id]
                        if pointer and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                           and n.func.attr == 'get' and n.args
                                           and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'sha256'
                                           for n in ast.walk(node)):
                            evidence_mappings.setdefault(filename, {}).setdefault('/'.join(pointer), set()).update(names)
                    if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                            and child.func.attr == 'get' and isinstance(child.func.value, ast.Name)
                            and child.args and isinstance(child.args[0], ast.Name)
                            and child.args[0].id == node.target.id):
                        # A loop that reads an evidence object and verifies its
                        # path/hash describes directly keyed evidence roles.
                        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                               and n.func.attr == 'get' and n.args
                               and isinstance(n.args[0], ast.Constant) and n.args[0].value == 'sha256'
                               for n in ast.walk(node)):
                            object_fields.setdefault(child.func.value.id, set()).update(names)
        if isinstance(node, ast.Compare):
            left = node.left
            if (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute)
                    and left.func.attr == 'get' and left.args
                    and isinstance(left.args[0], ast.Constant)
                    and left.args[0].value in ('kind', 'field', 'class', 'object')):
                values = [n.value for n in node.comparators
                          if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                enum_values.setdefault(left.args[0].value, set()).update(values)
    return {
        'schema_version': '0.2.6', 'task_id': report.get('task_id'),
        'expected_case_ids': report.get('expected_case_ids', []),
        'documents': documents, 'case_fields_read': sorted(case_fields),
        'array_checks_read_by_grader': sorted(array_checks), 'raw_evidence_roles': raw_roles,
        'nested_numeric_fields': {k: sorted(v) for k, v in nested_fields.items()},
        'direct_evidence_fields': {k: sorted(v) for k, v in object_fields.items()},
        'nested_object_fields': {filename: {pointer: sorted(fields) for pointer, fields in objects.items()}
                                 for filename, objects in nested_objects.items()},
        'evidence_mapping_roles': {filename: {pointer: sorted(fields) for pointer, fields in objects.items()}
                                   for filename, objects in evidence_mappings.items()},
        'serialization_enums': {k: sorted(v) for k, v in enum_values.items() if v},
        'evidence_item_shape': {'path': 'relative path to an existing raw file', 'sha256': 'actual SHA-256 of that file'},
        'scoring': {'public_artifact_and_provenance_max': 60, 'task_specific_max': 40,
                    'legacy_score_layers_in_reporting_contract': 'Descriptive legacy fields; not the active scoring implementation.'},
        'scope': 'Serialization interface extracted from evaluator field accesses and public contracts. No private reference arrays, completed results, or numerical certification.',
        'instructions': 'Preserve the exact container names. For example, a case_result object is nested under case_result, not flattened into the file root. Compute all numbers from actual outputs. Fields read in conditional branches are documentation; consult the full public task contract for their applicability.',
    }


def inspect_interfaces(root: Path, description: dict) -> dict:
    errors = []
    expected = description.get('expected_case_ids', [])
    for filename, form in description.get('documents', {}).items():
        path = root / filename
        try:
            if not path.is_file() or path.stat().st_size > 128 * 1024 * 1024:
                raise ValueError('missing_or_oversized')
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError('root_must_be_object')
            if 'task_id' in form['root_fields_read'] and value.get('task_id') != description['task_id']:
                errors.append(filename + ':task_id_mismatch')
            for pointer, roles in description.get('evidence_mapping_roles', {}).get(filename, {}).items():
                collection = value
                for key in pointer.split('/'):
                    collection = collection.get(key) if isinstance(collection, dict) else None
                if not isinstance(collection, dict):
                    errors.append(filename + ':' + pointer + ':expected_evidence_mapping')
                    continue
                for role in roles:
                    item = collection.get(role)
                    if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('sha256'), str):
                        errors.append(filename + ':' + pointer + ':' + role + ':path_sha256_object_required')
            for container in form['case_containers']:
                cases = value.get(container)
                if container in ('case', 'case_result'):
                    if not isinstance(cases, dict):
                        errors.append(filename + ':' + container + ':expected_nested_object')
                        continue
                    ids = [cases.get('case_id')]
                else:
                    if not isinstance(cases, list) or not all(isinstance(case, dict) for case in cases):
                        errors.append(filename + ':' + container + ':expected_list_of_objects')
                        continue
                    ids = [case.get('case_id') for case in cases]
                if len(ids) != len(set(ids)) or set(ids) != set(expected):
                    errors.append(filename + ':' + container + ':case_ids_mismatch')
                iterable = [cases] if isinstance(cases, dict) else cases
                for i, case in enumerate(iterable):
                    if not isinstance(case, dict):
                        continue
                    for key in description.get('nested_numeric_fields', {}):
                        if case.get('all_run_gates_pass') is True and key not in case:
                            errors.append(filename + ':' + container + ':' + str(i) + ':' + key + ':missing_diagnostics')
            # Root fields observed in the interface are pointers, not always
            # unconditional requirements. Identity and container checks above
            # are strict; optional fields are not blindly made mandatory.
            for key, fields in description.get('nested_numeric_fields', {}).items():
                def visit(obj, pointer=''):
                    if isinstance(obj, dict):
                        if key in obj:
                            diagnostics = obj[key]
                            if not isinstance(diagnostics, dict):
                                errors.append(filename + pointer + ':' + key + ':expected_object')
                            else:
                                import math
                                for field in fields:
                                    v = diagnostics.get(field)
                                    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                                        errors.append(filename + pointer + ':' + key + ':' + field + ':missing_or_nonfinite')
                        for k, child in obj.items():
                            visit(child, pointer + '/' + str(k))
                    elif isinstance(obj, list):
                        for i, child in enumerate(obj):
                            visit(child, pointer + '/' + str(i))
                visit(value)
            for container in form['case_containers']:
                cases = value.get(container)
                if isinstance(cases, dict):
                    cases = [cases]
                if not isinstance(cases, list):
                    continue
                # Apply role checks only to evidence documents, preserving
                # solver/report documents with different field contracts.
                if 'evidence' in filename:
                    required = set().union(*[set(v) for k, v in description.get('direct_evidence_fields', {}).items()
                                            if 'evidence' in k])
                    for i, case in enumerate(cases):
                        if not isinstance(case, dict):
                            continue
                        for role in sorted(required):
                            item = case.get(role)
                            if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('sha256'), str):
                                errors.append(filename + ':' + container + ':' + str(i) + ':' + role + ':direct_path_sha256_object_required')
            if isinstance(value.get('products'), list):
                allowed = description.get('serialization_enums', {}).get('kind', [])
                for i, product in enumerate(value['products']):
                    if isinstance(product, dict) and allowed and product.get('kind') not in allowed:
                        errors.append(filename + ':products:' + str(i) + ':kind_not_in_public_enum')
        except (OSError, ValueError, TypeError) as exc:
            errors.append(filename + ':' + str(exc))
    return {'errors': errors, 'scope': 'Public serialization checks only; no numerical score or reference access.'}
