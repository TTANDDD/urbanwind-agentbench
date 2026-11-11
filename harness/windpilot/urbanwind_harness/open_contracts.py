"""Public serialization discovery and optional, evidence-preserving delivery.

The compiler parses source; it never executes a grader or opens a reference.
Only supplied public assets supply dynamic role names and fixed identifiers.
The serializer is a convenience API, not an action/finish permission gate.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _Ref:
    document: str
    pointer: tuple[str, ...] = ()


def _root_path(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and node.id == "root":
        return ""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _root_path(node.left)
        if left is not None and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return "/".join(filter(None, (left, node.right.value)))
    return None


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set(value) | set().union(*(_strings(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_strings(v) for v in value), set())
    return set()


def _asset_map(assets: dict | list | None) -> dict:
    """Named assets are preferable; unnamed contracts remain explicitly limited."""
    if isinstance(assets, dict):
        return {str(k).replace("\\", "/"): v for k, v in assets.items()}
    return {f"unnamed_public_asset_{i}": v for i, v in enumerate(assets or [])}


def _public_cases(plan: dict | list | None, report: dict, assets: dict) -> list[dict]:
    rows = plan.get("cases", []) if isinstance(plan, dict) else (plan or [])
    rows = [r for r in rows if isinstance(r, dict) and isinstance(r.get("case_id"), str)]
    if not rows:
        rows = [{"case_id": name} for name in report.get("expected_case_ids", []) if isinstance(name, str)]
    by_id = {r["case_id"]: copy.deepcopy(r) for r in rows}
    # Only explicitly named identifier fields from public cases are propagated.
    for asset in assets.values():
        for row in asset.get("cases", []) if isinstance(asset, dict) else []:
            if isinstance(row, dict) and row.get("case_id") in by_id:
                for key in ("variant", "variant_key"):
                    if isinstance(row.get(key), str):
                        by_id[row["case_id"]].setdefault(key, row[key])
    return list(by_id.values())


class _Compiler:
    def __init__(self, source: str, public: dict):
        self.tree = ast.parse(source)
        self.public = public
        self.public_strings = _strings(public)
        self.refs: dict[str, set[_Ref]] = {}
        self.values: dict[str, Any] = {}
        self.objects: dict[tuple[str, tuple[str, ...]], dict] = {}
        self.unresolved: set[str] = set()
        self.nodes = list(ast.walk(self.tree))
        self.parents = {child: parent for parent in self.nodes for child in ast.iter_child_nodes(parent)}
        self.helpers = {n.name: n for n in self.nodes if isinstance(n, ast.FunctionDef)}
        self.helper_stack: set[str] = set()
        self.enums: dict[str, set[str]] = {}

    def object(self, ref: _Ref) -> dict:
        return self.objects.setdefault((ref.document, ref.pointer), {
            "fields_read": set(), "container_types": set(), "direct_evidence_roles": set(),
            "numeric_fields": set(), "boolean_fields": set(), "array_fields": set(),
            "exact_fields": set(),
        })

    def refs_of(self, node: ast.AST) -> set[_Ref]:
        if isinstance(node, ast.Name):
            return self.refs.get(node.id, set())
        if isinstance(node, ast.BoolOp):
            return set().union(*(self.refs_of(x) for x in node.values), set())
        if isinstance(node, (ast.Tuple, ast.List)):
            return set().union(*(self.refs_of(x) for x in node.elts), set())
        if isinstance(node, (ast.DictComp, ast.ListComp)) and node.generators:
            # Reindexing a submitted list by case_id preserves its case origin.
            return self.refs_of(node.generators[0].iter)
        if isinstance(node, ast.Subscript):
            keys = self.keys(node.slice) or ["*"]
            return {_Ref(r.document, r.pointer + (key,)) for r in self.refs_of(node.value) for key in keys}
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                keys = self.keys(node.args[0]) or ["*"]
                return {_Ref(r.document, r.pointer + (key,)) for r in self.refs_of(node.func.value) for key in keys}
            path = None
            if isinstance(node.func, ast.Name) and node.func.id in ("load", "read_json", "load_json") and node.args:
                path = _root_path(node.args[0])
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "loads" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "read_text":
                    path = _root_path(arg.func.value)
            if path and path.endswith(".json") and Path(path).name.lower() not in ("reference.json", "private_reference.json", "hidden_reference.json"):
                return {_Ref(path)}
        return set()

    def public_value(self, node: ast.AST) -> Any:
        # Strings are field names. Numeric literals, evaluator dict values and
        # private constants are deliberately not exported or evaluated.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.values.get(node.id)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self.public_value(x) for x in node.elts]
            return values if all(isinstance(v, str) for v in values) else None
        if isinstance(node, ast.Dict):
            result = {}
            for key, value in zip(node.keys, node.values):
                name = self.public_value(key) if key is not None else None
                if not isinstance(name, str):
                    return None
                result[name] = self.public_value(value)
            return result
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("items", "values", "keys"):
            return self.public_value(node.func.value)
        for ref in self.refs_of(node):
            value = self.public.get(ref.document)
            try:
                for part in ref.pointer:
                    value = value[part]
            except (KeyError, TypeError):
                continue
            if isinstance(value, (list, dict, str)):
                return value
        return None

    def keys(self, node: ast.AST) -> list[str]:
        value = self.public_value(node)
        if isinstance(value, str):
            return [value]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return value
        return []

    def bind(self, target: ast.AST, value: ast.AST) -> bool:
        changed = False
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for t, v in zip(target.elts, value.elts):
                changed |= self.bind(t, v)
        elif isinstance(target, ast.Name):
            refs = self.refs_of(value)
            changed = self.refs.get(target.id, set()) != refs
            self.refs[target.id] = refs
            val = self.public_value(value)
            # A source name constant is permitted only if it already appears
            # in supplied public data (e.g. a public primary metric id).
            if isinstance(value, ast.Constant) and val not in self.public_strings:
                val = None
            changed |= self.values.get(target.id) != val
            self.values[target.id] = val
        return changed

    def bind_iteration(self, target: ast.AST, iterable: ast.AST) -> bool:
        changed = False
        if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Name) and iterable.func.id == "zip":
            if isinstance(target, (ast.Tuple, ast.List)):
                for t, v in zip(target.elts, iterable.args):
                    changed |= self.bind_iteration(t, v)
            return changed
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(iterable, ast.Call):
            if isinstance(iterable.func, ast.Name) and iterable.func.id == "enumerate" and iterable.args:
                return self.bind_iteration(target.elts[-1], iterable.args[0])
            if isinstance(iterable.func, ast.Attribute) and iterable.func.attr == "items":
                return self.bind_iteration(target.elts[-1], iterable.func.value)
        if isinstance(target, ast.Name):
            values = self.public_value(iterable)
            if isinstance(values, list) and all(isinstance(v, str) for v in values):
                if self.values.get(target.id) != values:
                    self.values[target.id] = values
                    changed = True
            # Iterating explicit documents is not iterating their rows.
            refs = (self.refs_of(iterable) if isinstance(iterable, (ast.Tuple, ast.List))
                    else {_Ref(r.document, r.pointer + ("*",)) for r in self.refs_of(iterable)})
            old = len(self.refs.get(target.id, set()))
            self.refs.setdefault(target.id, set()).update(refs)
            changed |= len(self.refs[target.id]) != old
        return changed

    def access(self, node: ast.AST) -> tuple[set[_Ref], list[str]]:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            return self.refs_of(node.func.value), self.keys(node.args[0])
        if isinstance(node, ast.Subscript):
            return self.refs_of(node.value), self.keys(node.slice)
        return set(), []

    def _emit(self, node: ast.AST) -> None:
        refs, keys = self.access(node)
        for ref in refs:
            self.object(ref)["fields_read"].update(keys)
            if not keys:
                self.unresolved.add(f"{ref.document}:{'/'.join(ref.pointer)}:dynamic_field_at_line_{node.lineno}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance" and len(node.args) >= 2:
            kind = node.args[1]
            if isinstance(kind, ast.Name) and kind.id in ("dict", "list"):
                for ref in self.refs_of(node.args[0]):
                    self.object(ref)["container_types"].add("object" if kind.id == "dict" else "list")
        if isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            argument_refs, argument_keys = self.access(node.args[0])
            array_check = name.startswith("finite_") and any(s in name for s in ("array", "matrix", "cube"))
            if name in ("float", "int", "close", "isfinite", "finite_number", "finite_scalar") or array_check:
                category = "array_fields" if array_check else "numeric_fields"
                for ref in argument_refs:
                    self.object(ref)[category].update(argument_keys)
        if isinstance(node, ast.Compare):
            boolean_refs, boolean_keys = self.access(node.left)
            if any(isinstance(c, ast.Constant) and isinstance(c.value, bool) for c in node.comparators):
                for ref in boolean_refs:
                    self.object(ref)["boolean_fields"].update(boolean_keys)
            for key in boolean_keys:
                if key in ("kind", "field", "class", "object"):
                    for comparator in node.comparators:
                        self.enums.setdefault(key, set()).update(self.keys(comparator))
            if isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name) and node.left.func.id == "set" and node.left.args:
                for comparator in node.comparators:
                    exact = self.keys(comparator)
                    if exact and all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                        for ref in self.refs_of(node.left.args[0]):
                            self.object(ref)["exact_fields"].update(exact)
        # Evidence entries are typed dictionaries read for path + sha256.
        if refs and ("path" in keys or "sha256" in keys):
            for ref in refs:
                if ref.pointer and ref.pointer[-1] != "*":
                    self.object(_Ref(ref.document, ref.pointer[:-1]))["direct_evidence_roles"].add(ref.pointer[-1])

    def _with_iteration(self, target: ast.AST, iterable: ast.AST, visit) -> None:
        saved_refs, saved_values = copy.deepcopy(self.refs), copy.deepcopy(self.values)
        public = self.public_value(iterable)
        iterator = iterable.func.attr if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute) else None
        if isinstance(public, dict) and iterator == "items" and isinstance(target, (ast.Tuple, ast.List)):
            for key, value in list(public.items())[:256]:
                self.refs, self.values = copy.deepcopy(saved_refs), copy.deepcopy(saved_values)
                self.values[target.elts[0].id] = key
                self.values[target.elts[1].id] = value
                self.refs[target.elts[0].id] = self.refs[target.elts[1].id] = set()
                visit()
        elif isinstance(public, list) and public and all(isinstance(v, str) for v in public) and isinstance(target, ast.Name):
            for value in public[:256]:
                self.refs, self.values = copy.deepcopy(saved_refs), copy.deepcopy(saved_values)
                self.values[target.id] = value
                self.refs[target.id] = set()
                visit()
        else:
            self.bind_iteration(target, iterable)
            visit()
        self.refs, self.values = saved_refs, saved_values

    def _expression(self, node: ast.AST) -> None:
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
            def generator(index=0):
                if index == len(node.generators):
                    for part in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
                        self._expression(part)
                else:
                    item = node.generators[index]
                    self._expression(item.iter)
                    def body():
                        for condition in item.ifs:
                            self._expression(condition)
                        generator(index + 1)
                    self._with_iteration(item.target, item.iter, body)
            generator()
            return
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.stmt):
                self._expression(child)
        self._emit(node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.helpers and node.func.id not in self.helper_stack:
            function = self.helpers[node.func.id]
            if any(self.refs_of(arg) for arg in node.args):
                saved_refs, saved_values = copy.deepcopy(self.refs), copy.deepcopy(self.values)
                self.helper_stack.add(function.name)
                for parameter, argument in zip(function.args.args, node.args):
                    self.bind(ast.Name(id=parameter.arg), argument)
                self._block(function.body)
                self.helper_stack.remove(function.name)
                self.refs, self.values = saved_refs, saved_values

    def _block(self, statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, ast.Assign):
                self._expression(node.value)
                for target in node.targets:
                    self.bind(target, node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self._expression(node.value)
                self.bind(node.target, node.value)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            elif isinstance(node, ast.For):
                self._expression(node.iter)
                self._with_iteration(node.target, node.iter, lambda: self._block(node.body))
                self._block(node.orelse)
            else:
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, ast.stmt) and not isinstance(child, ast.ExceptHandler):
                        self._expression(child)
                for name in ("body", "orelse", "finalbody"):
                    body = getattr(node, name, None)
                    if isinstance(body, list):
                        self._block(body)
                for handler in getattr(node, "handlers", []):
                    self._block(handler.body)

    def compile(self) -> dict:
        self._block(self.tree.body)
        globals_refs, globals_values = copy.deepcopy(self.refs), copy.deepcopy(self.values)
        for function in self.helpers.values():
            self.refs, self.values = copy.deepcopy(globals_refs), copy.deepcopy(globals_values)
            self._block(function.body)
        documents = {}
        for (document, pointer), spec in sorted(self.objects.items()):
            if document in self.public or document.startswith("local_assets/"):
                continue
            public_spec = {key: sorted(value) for key, value in spec.items()}
            public_spec["container_type"] = (public_spec["container_types"][0] if len(spec["container_types"]) == 1 else "unresolved")
            if pointer and pointer[-1] in ("cases", "case_results") and public_spec["container_type"] == "object":
                public_spec["container_type"] = "object_by_case_id"
            documents.setdefault(document, {"root_type": "object", "objects": {}})["objects"]["/".join(pointer)] = public_spec
        for document, spec in documents.items():
            spec["root_fields_read"] = spec["objects"].get("", {}).get("fields_read", [])
            spec["case_containers"] = [key for key in ("case", "case_result", "cases", "case_results") if key in spec["root_fields_read"]]
            for key in spec["case_containers"]:
                obj = spec["objects"].setdefault(key, {"container_type": "unresolved", "fields_read": []})
                if obj["container_type"] == "unresolved":
                    self.unresolved.add(f"{document}:{key}:container_type_not_resolved")
        return documents


def compile_public_contract(grader_source: str, reporting: dict,
                            public_assets: dict | list | None = None,
                            case_plan: dict | list | None = None) -> dict:
    """Return enhanced submission_format and guide from supplied PUBLIC inputs.

    public_assets should map relative public paths to parsed JSON. No filesystem
    discovery is performed. A caller may append public precheck source to grader
    source, enabling provenance/metrics field discovery as well.
    """
    assets = _asset_map(public_assets)
    public = {**assets, "reporting_contract.json": reporting}
    if case_plan is not None:
        public["case_plan.json"] = case_plan
    compiler = _Compiler(grader_source, public)
    documents = compiler.compile()
    cases = _public_cases(case_plan, reporting, assets)
    fixed = {r["case_id"]: {k: r[k] for k in ("case_id", "variant", "variant_key") if isinstance(r.get(k), str)} for r in cases}
    raw_roles = sorted({role for asset in assets.values() if isinstance(asset, dict)
                        for name in ("required_raw_evidence", "required_raw_evidence_per_case")
                        for role in asset.get(name, []) if isinstance(role, str)})
    description = {
        "schema_version": "0.2.8-plan-review", "task_id": reporting.get("task_id"),
        "expected_case_ids": [r["case_id"] for r in cases], "documents": documents,
        "raw_evidence_roles": raw_roles, "fixed_public_identifiers": fixed,
        "serialization_enums": {k: sorted(v) for k, v in compiler.enums.items() if v},
        "unresolved_public_mapping": sorted(compiler.unresolved),
        "evidence_item_shape": {"path": "actual relative file path", "sha256": "SHA256 of actual bytes"},
        "scope": "Public serialization only. Observed field accesses can be conditional. No reference values, numerical certification or scores.",
    }
    guide = {
        "schema_version": description["schema_version"],
        "task_id": description["task_id"],
        "public_case_order": description["expected_case_ids"],
        "fixed_identifiers": fixed, "serialization": documents,
        "unresolved_public_mapping": description["unresolved_public_mapping"],
        "instructions": [
            "Use the listed container type: a case-id dictionary and a list are different interfaces.",
            "Register evidence roles at their stated object level, using existing files and current hashes.",
            "Keep fixed public variant identifiers separate from descriptions of actual software/model settings.",
            "All numbers, false/true gates, commands, times and software metadata must come from actual work.",
            "Unresolved mappings require consulting public files; the serializer cannot certify numerical correctness.",
        ],
    }
    return {"submission_format": description, "task_public_guide": guide}


def _objects_at(value: dict, pointer: str) -> list[dict]:
    values: list[Any] = [value]
    for part in filter(None, pointer.split("/")):
        next_values = []
        for item in values:
            if part == "*":
                next_values.extend(item.values() if isinstance(item, dict) else item if isinstance(item, list) else [])
            elif isinstance(item, dict) and part in item:
                next_values.append(item[part])
        values = next_values
    return [v for v in values if isinstance(v, dict)]


def _role_candidates(obj: dict, role: str) -> list[dict]:
    found = []
    for key in ("evidence", "raw_evidence", "required_evidence", "evidence_by_role", "registered_evidence", "evidence_items"):
        collection = obj.get(key)
        if isinstance(collection, dict) and isinstance(collection.get(role), dict):
            found.append(collection[role])
        elif isinstance(collection, list):
            found.extend(item for item in collection if isinstance(item, dict)
                         and any(item.get(k) == role for k in ("role", "role_id", "key")))
    return [item for item in found if isinstance(item.get("path"), str)]


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_delivery(documents: dict[str, dict], description: dict,
                       root: Path | None = None) -> dict:
    """Return copies with public wrappers/aliases; never invent physical evidence.

    No files are written. Existing direct values win; conflicting aliases are
    reported. Supplied numeric values and false gates are preserved. Metadata
    canonicalization only reformats metadata that is already present.
    """
    result = copy.deepcopy(documents)
    changes, errors = [], []
    for filename, form in description.get("documents", {}).items():
        value = result.get(filename)
        if not isinstance(value, dict):
            errors.append(f"{filename}:missing_object")
            continue
        for key in form.get("case_containers", []):
            spec = form.get("objects", {}).get(key, {})
            kind = spec.get("container_type")
            collection = value.get(key)
            if collection is None and key in ("case", "case_result") and isinstance(value.get("case_id"), str):
                identity = value.pop("task_id", None)
                case = copy.deepcopy(value)
                value.clear()
                if identity is not None:
                    value["task_id"] = identity
                value[key] = collection = case
                changes.append(f"{filename}:{key}:wrapped_existing_case")
            if kind == "object_by_case_id" and isinstance(collection, list):
                ids = [item.get("case_id") for item in collection if isinstance(item, dict)]
                if len(ids) == len(collection) and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids):
                    value[key] = {item["case_id"]: item for item in collection}
                    changes.append(f"{filename}:{key}:list_to_case_id_object")
                else:
                    errors.append(f"{filename}:{key}:ambiguous_case_identity")
            elif kind == "list" and isinstance(collection, dict):
                order = description.get("expected_case_ids", [])
                if set(collection) == set(order) and all(isinstance(collection[i], dict) and collection[i].get("case_id", i) == i for i in order):
                    value[key] = [dict(collection[i], case_id=i) for i in order]
                    changes.append(f"{filename}:{key}:case_id_object_to_public_order_list")
                else:
                    errors.append(f"{filename}:{key}:ambiguous_case_identity")
        for pointer, spec in form.get("objects", {}).items():
            for obj in _objects_at(value, pointer):
                if "task_id" in spec.get("fields_read", []) and description.get("task_id") is not None:
                    if obj.get("task_id") != description["task_id"]:
                        before = obj.get("task_id")
                        obj["task_id"] = description["task_id"]
                        changes.append({"document": filename, "pointer": pointer, "field": "task_id",
                                        "before": before, "after": obj["task_id"], "source": "public_reporting_contract"})
                for role in spec.get("direct_evidence_roles", []):
                    candidates = _role_candidates(obj, role)
                    if role not in obj and candidates:
                        if len({c["path"] for c in candidates}) == 1:
                            obj[role] = copy.deepcopy(candidates[0])
                            changes.append(f"{filename}:{pointer}:{role}:direct_alias")
                        else:
                            errors.append(f"{filename}:{pointer}:{role}:conflicting_paths")
                    elif isinstance(obj.get(role), dict) and any(c["path"] != obj[role].get("path") for c in candidates):
                        errors.append(f"{filename}:{pointer}:{role}:conflicting_paths")
                for field in spec.get("numeric_fields", []) + spec.get("boolean_fields", []):
                    if field in obj:
                        continue
                    candidates = [obj[k][field] for k in ("metrics", "gates", "diagnostics", "gate_diagnostics")
                                  if isinstance(obj.get(k), dict) and field in obj[k]]
                    valid = [v for v in candidates if (isinstance(v, bool) if field in spec.get("boolean_fields", []) else _finite_number(v))]
                    if valid and all(v == valid[0] for v in valid) and len(valid) == len(candidates):
                        obj[field] = valid[0]
                        changes.append(f"{filename}:{pointer}:{field}:copied_existing_scalar")
                    elif candidates:
                        errors.append(f"{filename}:{pointer}:{field}:invalid_or_conflicting_scalar")
                public = description.get("fixed_public_identifiers", {}).get(obj.get("case_id"), {})
                for field in spec.get("fields_read", []):
                    if field in public and obj.get(field) != public[field]:
                        before = obj.get(field)
                        obj[field] = public[field]
                        changes.append({"document": filename, "pointer": pointer, "field": field,
                                        "before": before, "after": public[field], "source": "public_case_plan"})
                exact = spec.get("exact_fields", [])
                if exact and all(field in obj for field in exact):
                    # Exact evidence-role dictionaries do not admit redundant
                    # case_id/wrapper keys. Preserve these in the audit result;
                    # arbitrary numerical/unknown extra fields are never dropped.
                    wrappers = {"case_id", "evidence", "raw_evidence", "required_evidence",
                                "evidence_by_role", "registered_evidence", "evidence_items"}
                    removed = {key: obj.pop(key) for key in list(obj) if key not in exact and key in wrappers}
                    if removed:
                        changes.append({"document": filename, "pointer": pointer,
                                        "removed_serialization_wrappers": removed,
                                        "source": "observed_exact_public_role_set"})
        # Generic provenance serialization, preserving the actual information.
        for obj in _walk_objects(value):
            if "command" in obj and "commands" not in obj and isinstance(obj["command"], str):
                obj["commands"] = [obj["command"]]
            if "metric_values" in obj and "values" not in obj and isinstance(obj["metric_values"], dict):
                obj["values"] = copy.deepcopy(obj["metric_values"])
            if isinstance(obj.get("software"), str):
                name = obj["software"]
                match = re.search(r"\b(openfoam|palm|python)\b", name, re.I)
                if match:
                    obj["software"] = [{"name": match.group(1).lower(), "reported": name}]
                else:
                    errors.append(f"{filename}:software_name_not_resolved_from_existing_metadata")
    if root is not None:
        _refresh_hashes(result, Path(root), errors)
    checked = validate_delivery(result, description)
    return {"documents": result, "changes": changes, "errors": sorted(set(errors + checked["errors"])),
            "scope": "Optional serialization; no raw-file edits, invented run proof or numerical certification."}


def _walk_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def validate_delivery(documents: dict, description: dict) -> dict:
    errors = []
    for filename, form in description.get("documents", {}).items():
        value = documents.get(filename)
        if not isinstance(value, dict):
            errors.append(f"{filename}:missing_object")
            continue
        for pointer, spec in form.get("objects", {}).items():
            if pointer in form.get("case_containers", []):
                obj = value.get(pointer)
                kind = spec.get("container_type")
                if kind == "list" and not isinstance(obj, list) or kind in ("object", "object_by_case_id") and not isinstance(obj, dict):
                    errors.append(f"{filename}:{pointer}:expected_{kind}")
                    continue
                cases = obj.values() if kind == "object_by_case_id" and isinstance(obj, dict) else obj if isinstance(obj, list) else [obj]
                ids = list(obj) if kind == "object_by_case_id" and isinstance(obj, dict) else [c.get("case_id") for c in cases if isinstance(c, dict)]
                expected = description.get("expected_case_ids", [])
                if expected and (len(ids) != len(set(ids)) or set(ids) != set(expected)):
                    errors.append(f"{filename}:{pointer}:case_identity_mismatch")
                if kind == "object_by_case_id" and isinstance(obj, dict) and any("case_id" in c and k != c.get("case_id") for k, c in obj.items() if isinstance(c, dict)):
                    errors.append(f"{filename}:{pointer}:case_key_identity_mismatch")
            for obj in _objects_at(value, pointer):
                if "task_id" in spec.get("fields_read", []) and description.get("task_id") is not None:
                    if obj.get("task_id") != description["task_id"]:
                        errors.append(f"{filename}:{pointer}:task_id_mismatch")
                exact = spec.get("exact_fields", [])
                if exact and set(obj) != set(exact):
                    errors.append(f"{filename}:{pointer}:exact_public_field_set_mismatch")
                for field in spec.get("numeric_fields", []):
                    if field not in obj or not _finite_number(obj[field]):
                        errors.append(f"{filename}:{pointer}:{field}:missing_or_nonfinite_number")
                for field in spec.get("boolean_fields", []):
                    if field not in obj or not isinstance(obj[field], bool):
                        errors.append(f"{filename}:{pointer}:{field}:missing_boolean")
                for role in spec.get("direct_evidence_roles", []):
                    item = obj.get(role)
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                        errors.append(f"{filename}:{pointer}:{role}:direct_path_hash_object_required")
    return {"errors": sorted(set(errors)), "scope": "Observed public serialization checks; no numerical acceptance."}


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _refresh_hashes(documents: dict, root: Path, errors: list[str]) -> None:
    root = root.resolve()
    done, visiting = set(), set()

    def refresh(name: str):
        if name in done:
            return
        if name in visiting:
            errors.append(f"{name}:cyclic_delivery_hash_dependency")
            return
        visiting.add(name)
        for item in _walk_objects(documents[name]):
            if not isinstance(item.get("path"), str) or "sha256" not in item:
                continue
            target = (root / item["path"]).resolve()
            if not target.is_relative_to(root):
                errors.append(f"{name}:{item['path']}:path_outside_delivery_root")
                continue
            relative = target.relative_to(root).as_posix()
            if relative in documents:
                refresh(relative)
                if relative in done:
                    item["sha256"] = hashlib.sha256(_bytes(documents[relative])).hexdigest()
            elif target.is_file():
                digest = hashlib.sha256()
                with target.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                item["sha256"] = digest.hexdigest()
            else:
                errors.append(f"{name}:{item['path']}:registered_file_missing")
        visiting.remove(name)
        done.add(name)

    for name in documents:
        refresh(name)


def write_delivery_atomic(root: Path, normalized: dict) -> dict:
    """Write only listed JSON documents atomically; reject unresolved validation.

    Atomicity is per file, not a multi-file transaction. Raw registered files are
    only read for hashes. Cycles/self hashes cannot be honestly materialized.
    """
    if normalized.get("errors"):
        raise ValueError("delivery_has_unresolved_errors:" + ";".join(normalized["errors"]))
    root = Path(root).resolve()
    documents = copy.deepcopy(normalized["documents"])
    fresh_errors: list[str] = []
    _refresh_hashes(documents, root, fresh_errors)
    if fresh_errors:
        raise ValueError("delivery_hashes_not_materializable:" + ";".join(fresh_errors))
    payloads = {}
    for name, value in documents.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".json":
            raise ValueError("delivery_json_path_outside_root:" + name)
        payloads[path] = _bytes(value)
    written = {}
    for path, payload in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        written[path.relative_to(root).as_posix()] = hashlib.sha256(payload).hexdigest()
    return {"written_sha256": written, "atomicity": "per_document", "raw_files_changed": False}
