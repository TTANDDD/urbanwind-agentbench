import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from urbanwind_harness.open_contracts import (
    compile_public_contract, normalize_delivery, validate_delivery,
    write_delivery_atomic,
)


PUBLIC = {"task_id": "public-task", "expected_case_ids": ["a", "b"],
          "primary_metric": {"id": "primary"}}
PLAN = {"cases": [{"case_id": "a", "variant": "first"},
                  {"case_id": "b", "variant": "second"}]}


class OpenContractTests(unittest.TestCase):
    def compile(self, source, assets=None, plan=PLAN, report=PUBLIC):
        return compile_public_contract(source, report, assets or {}, plan)["submission_format"]

    def test_case_id_dictionary_is_distinct_from_list(self):
        source = '''
manifest = load(root / "evidence.json")
reported = load(root / "results.json")
evidence = manifest.get("cases")
results = reported.get("case_results")
if not isinstance(evidence, dict): raise ValueError()
if not isinstance(results, list): raise ValueError()
for case in results:
    case.get("case_id")
'''
        form = self.compile(source)
        self.assertEqual(form["documents"]["evidence.json"]["objects"]["cases"]["container_type"], "object_by_case_id")
        self.assertEqual(form["documents"]["results.json"]["objects"]["case_results"]["container_type"], "list")
        docs = {"evidence.json": {"cases": [{"case_id": "b"}, {"case_id": "a"}]},
                "results.json": {"case_results": {"b": {"case_id": "b"}, "a": {"case_id": "a"}}}}
        prepared = normalize_delivery(docs, form)
        self.assertEqual(list(prepared["documents"]["evidence.json"]["cases"]), ["b", "a"])
        self.assertEqual([c["case_id"] for c in prepared["documents"]["results.json"]["case_results"]], ["a", "b"])
        self.assertFalse(prepared["errors"])
        self.assertIsInstance(docs["evidence.json"]["cases"], list)

    def test_public_dynamic_roles_are_direct_aliases_and_hashes_refresh(self):
        source = '''
contract = load(root / "local_assets" / "raw_contract.json")
evidence = load(root / "evidence.json")
case = evidence.get("case")
if not isinstance(case, dict): raise ValueError()
for role in contract["required_raw_evidence_per_case"]:
    item = case.get(role)
    if not isinstance(item, dict): raise ValueError()
    item.get("path")
    item.get("sha256")
'''
        form = self.compile(source, {"local_assets/raw_contract.json": {"required_raw_evidence_per_case": ["probe_U", "solver_log"]}},
                            {"cases": [{"case_id": "a"}]}, {"expected_case_ids": ["a"]})
        self.assertEqual(form["documents"]["evidence.json"]["objects"]["case"]["direct_evidence_roles"], ["probe_U", "solver_log"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "U").write_bytes(b"# raw coordinates\n0 (1 2 3)\n")
            (root / "log").write_bytes(b"real run record")
            before = (root / "U").read_bytes()
            documents = {"evidence.json": {"case": {"case_id": "a", "evidence": [
                {"role": "probe_U", "path": "U", "sha256": "stale"},
                {"role": "solver_log", "path": "log", "sha256": "stale"}]}}}
            prepared = normalize_delivery(documents, form, root)
            self.assertFalse(prepared["errors"])
            case = prepared["documents"]["evidence.json"]["case"]
            self.assertEqual(case["probe_U"]["sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(case["evidence"][0]["sha256"], case["probe_U"]["sha256"])
            written = write_delivery_atomic(root, prepared)
            self.assertEqual(written["written_sha256"]["evidence.json"], hashlib.sha256((root / "evidence.json").read_bytes()).hexdigest())
            self.assertEqual((root / "U").read_bytes(), before)

    def test_fixed_variant_only_comes_from_public_plan(self):
        source = '''
hidden = load(Path("reference.json"))
reported = load(root / "results.json")
submitted = reported.get("cases")
if not isinstance(submitted, list): raise ValueError()
for case in submitted:
    if case.get("variant") != hidden["private_expected_text"]: raise ValueError()
'''
        form = self.compile(source)
        self.assertNotIn("reference.json", form["documents"])
        self.assertNotIn("private_expected_text", json.dumps(form))
        documents = {"results.json": {"cases": [{"case_id": "a", "variant": "first (implementation detail)"},
                                                  {"case_id": "b", "variant": "second"}]}}
        normalized = normalize_delivery(documents, form)
        self.assertEqual(normalized["documents"]["results.json"]["cases"][0]["variant"], "first")
        self.assertEqual(documents["results.json"]["cases"][0]["variant"], "first (implementation detail)")

    def test_false_gate_is_preserved_and_bool_never_becomes_number(self):
        source = '''
reported = load(root / "results.json")
case = reported.get("case_result")
if not isinstance(case, dict): raise ValueError()
float(case.get("drift"))
if case.get("all_run_gates_pass") is not True: raise ValueError()
'''
        form = self.compile(source, plan={"cases": [{"case_id": "a"}]}, report={"expected_case_ids": ["a"]})
        docs = {"results.json": {"case_result": {"case_id": "a", "metrics": {"drift": .4}, "gates": {"all_run_gates_pass": False}}}}
        normalized = normalize_delivery(docs, form)
        obj = normalized["documents"]["results.json"]["case_result"]
        self.assertEqual(obj["drift"], .4)
        self.assertIs(obj["all_run_gates_pass"], False)
        self.assertFalse(normalized["errors"])
        docs["results.json"]["case_result"]["metrics"]["drift"] = False
        rejected = normalize_delivery(docs, form)
        self.assertTrue(any("invalid_or_conflicting_scalar" in e for e in rejected["errors"]))

    def test_unresolved_roles_are_explicit_not_guessed(self):
        source = '''
evidence = load(root / "evidence.json")
case = evidence.get("case")
for role in unknown_contract["role_names"]:
    case.get(role)
'''
        form = self.compile(source)
        self.assertTrue(form["unresolved_public_mapping"])
        self.assertEqual(form["documents"]["evidence.json"]["objects"]["case"]["direct_evidence_roles"], [])

    def test_numeric_scalar_loop_uses_public_primary_id(self):
        source = '''
PRIMARY_ID = "primary"
reported = load(root / "results.json")
case = reported.get("case_result")
fields = ("drift", PRIMARY_ID)
if not isinstance(case, dict): raise ValueError()
for key in fields:
    float(case[key])
'''
        form = self.compile(source)
        self.assertEqual(form["documents"]["results.json"]["objects"]["case_result"]["numeric_fields"], ["drift", "primary"])

    def test_unknown_metadata_never_invents_run_times_or_success(self):
        form = self.compile('manifest = load(root / "run_manifest.json")\nmanifest.get("runs")')
        docs = {"run_manifest.json": {"runs": [{"software": "unknown simulator", "command": "unknown_sim case"}]}}
        normalized = normalize_delivery(docs, form)
        run = normalized["documents"]["run_manifest.json"]["runs"][0]
        self.assertEqual(run["commands"], ["unknown_sim case"])
        for field in ("started_at", "ended_at", "exit_code", "execution_role"):
            self.assertNotIn(field, run)
        self.assertEqual(run["software"], "unknown simulator")
        self.assertTrue(normalized["errors"])

    def test_canonical_software_preserves_original_metadata(self):
        form = self.compile('manifest = load(root / "run_manifest.json")\nmanifest.get("runs")')
        docs = {"run_manifest.json": {"runs": [{"software": "OpenFOAM OpenCFD-v2412", "command": "pimpleFoam"}]}}
        run = normalize_delivery(docs, form)["documents"]["run_manifest.json"]["runs"][0]
        self.assertEqual(run["software"], [{"name": "openfoam", "reported": "OpenFOAM OpenCFD-v2412"}])
        self.assertNotIn("exit_code", run)

    def test_all_hash_mappings_and_interdocument_dependencies_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").write_bytes(b"raw")
            docs = {"result.json": {"raw": {"path": "raw", "sha256": "old"}},
                    "manifest.json": {"duplicate": {"path": "raw", "sha256": "different"},
                                      "result": {"path": "result.json", "sha256": "old"}}}
            normalized = normalize_delivery(docs, {"documents": {}}, root)
            written = write_delivery_atomic(root, normalized)
            delivered = json.loads((root / "manifest.json").read_text())
            self.assertEqual(delivered["result"]["sha256"], written["written_sha256"]["result.json"])
            self.assertEqual(delivered["duplicate"]["sha256"], hashlib.sha256(b"raw").hexdigest())

    def test_cycles_and_self_hashes_do_not_write_partial_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs = {"a.json": {"entry": {"path": "b.json", "sha256": "old"}},
                    "b.json": {"entry": {"path": "a.json", "sha256": "old"}}}
            normalized = normalize_delivery(docs, {"documents": {}}, root)
            self.assertTrue(any("cyclic_delivery" in e for e in normalized["errors"]))
            with self.assertRaises(ValueError):
                write_delivery_atomic(root, normalized)
            self.assertEqual(list(root.iterdir()), [])

    def test_conflicting_role_aliases_do_not_overwrite_direct_evidence(self):
        form = {"documents": {"evidence.json": {"case_containers": [], "objects": {
            "case": {"direct_evidence_roles": ["U"]}}}}}
        docs = {"evidence.json": {"case": {"U": {"path": "real", "sha256": "x"},
                                               "evidence": {"U": {"path": "other", "sha256": "y"}}}}}
        normalized = normalize_delivery(docs, form)
        self.assertEqual(normalized["documents"]["evidence.json"]["case"]["U"]["path"], "real")
        self.assertTrue(normalized["errors"])

    def test_number_unknown_from_private_constant_is_not_exported(self):
        form = self.compile('PRIVATE_TEXT = "secret answer"\nPRIVATE_NUMBER = 987.654\nreported = load(root / "results.json")\nreported.get("cases")')
        serialized = json.dumps(form)
        self.assertNotIn("secret answer", serialized)
        self.assertNotIn("987.654", serialized)

    def test_scoped_loop_variables_preserve_per_case_role_requirements(self):
        source = '''
FIRST = "a"
SECOND = "b"
manifest = load(root / "evidence.json")
cases = manifest.get("cases")
if not isinstance(cases, dict): raise ValueError()
required = {FIRST: {"diagnosis"}, SECOND: {"U", "solver_log"}}
for case_id, names in required.items():
    items = cases[case_id]
    for key in names:
        item = items[key]
        item.get("path")
        item.get("sha256")
unrelated = ("temperature", "drift")
for key in unrelated:
    unrelated_value = other[key]
'''
        form = self.compile(source)
        objects = form["documents"]["evidence.json"]["objects"]
        self.assertEqual(objects["cases/a"]["direct_evidence_roles"], ["diagnosis"])
        self.assertEqual(objects["cases/b"]["direct_evidence_roles"], ["U", "solver_log"])

    def test_reindex_comprehension_retains_case_interface(self):
        source = '''
reported = load(root / "results.json")
submitted = reported.get("cases")
if not isinstance(submitted, list): raise ValueError()
by_id = {case.get("case_id"): case for case in submitted}
for expected in unknown_reference:
    case = by_id[expected["case_id"]]
    case.get("variant")
    finite_array(case.get("mean_Ux"), unknown_length)
'''
        form = self.compile(source)
        self.assertIn("variant", form["documents"]["results.json"]["objects"]["cases/*"]["fields_read"])
        self.assertEqual(form["documents"]["results.json"]["objects"]["cases/*"]["array_fields"], ["mean_Ux"])

    def test_public_serialization_enum_does_not_copy_variant_comparator(self):
        source = '''
reported = load(root / "products.json")
products = reported.get("products")
for product in products:
    if product.get("kind") not in ("plane", "probes"): raise ValueError()
    if product.get("variant") != "private comparator text": raise ValueError()
'''
        form = self.compile(source)
        self.assertEqual(form["serialization_enums"], {"kind": ["plane", "probes"]})
        self.assertNotIn("private comparator text", json.dumps(form))

    def test_writer_rehashes_raw_file_changed_after_normalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw").write_bytes(b"first bytes")
            normalized = normalize_delivery({"a.json": {"item": {"path": "raw", "sha256": "old"}}}, {"documents": {}}, root)
            (root / "raw").write_bytes(b"latest actual bytes")
            write_delivery_atomic(root, normalized)
            actual = json.loads((root / "a.json").read_text())
            self.assertEqual(actual["item"]["sha256"], hashlib.sha256(b"latest actual bytes").hexdigest())

    def test_exact_case_role_mapping_uses_key_identity_and_preserves_wrapper_audit(self):
        source = '''
FIRST = "a"
manifest = load(root / "evidence.json")
cases = manifest.get("cases")
if not isinstance(cases, dict): raise ValueError()
required = {FIRST: {"diagnosis"}}
for case_id, names in required.items():
    items = cases[case_id]
    if set(items) != names: raise ValueError()
    for key in names:
        item = items[key]
        item.get("path")
        item.get("sha256")
'''
        form = self.compile(source, plan={"cases": [{"case_id": "a"}]}, report={"expected_case_ids": ["a"]})
        self.assertEqual(form["documents"]["evidence.json"]["objects"]["cases/a"]["exact_fields"], ["diagnosis"])
        docs = {"evidence.json": {"cases": [{"case_id": "a", "raw_evidence": {"diagnosis": {"path": "diagnosis.json", "sha256": "actual"}}}]}}
        normalized = normalize_delivery(docs, form)
        self.assertFalse(normalized["errors"])
        self.assertEqual(set(normalized["documents"]["evidence.json"]["cases"]["a"]), {"diagnosis"})
        self.assertTrue(any(isinstance(c, dict) and "removed_serialization_wrappers" in c for c in normalized["changes"]))
        self.assertIn("raw_evidence", docs["evidence.json"]["cases"][0])


if __name__ == "__main__":
    unittest.main()
