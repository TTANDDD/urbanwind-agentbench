import json
import tempfile
import unittest
from pathlib import Path

from urbanwind_harness.full_tools import probe_statistics, field_values, log_statistics, normalized_absolute_error, pointwise_case_spread
from urbanwind_harness.interfaces import compile_interfaces, inspect_interfaces
from urbanwind_harness.ledger import Ledger
from urbanwind_harness.state import fingerprint


class FullGeneral(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def put(self, name, text):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p
    def test_scalar_vector_exact_window_and_population_tke(self):
        p = self.put('U', '# Probe 0 (1 2 3)\n0 (99 0 0)\n1 (1 2 3)\n2 (3 4 5)\n3 (5 6 7)\n')
        result = probe_statistics(p, 0, 2)
        self.assertEqual(result['sample_count'], 2)
        self.assertEqual(result['mean'], [[2, 3, 4]])
        self.assertEqual(result['resolved_tke'], [1.5])
        result = probe_statistics(p, 0, 2, lower_inclusive=True)
        self.assertEqual(result['sample_count'], 3)
        p = self.put('T', '1 2 4\n2 4 6\n')
        self.assertEqual(probe_statistics(p, 0, 2, components=1)['mean'], [[3], [5]])
    def test_missing_sentinel_cannot_be_zero_filled(self):
        p = self.put('bad', '# Probe 0 Not Found\n1 (-1e300 -1e300 -1e300)\n')
        with self.assertRaises(ValueError): probe_statistics(p, 0, 2)
        p = self.put('mixed', '# Probe 1 Not Found\n1 (1 2 3) (-1e300 -1e300 -1e300)\n2 (3 4 5) (-1e300 -1e300 -1e300)\n')
        r = probe_statistics(p, 0, 2, indices=[0])
        self.assertEqual(r['selected_probe_indices'], [0])
        self.assertEqual(r['excluded_missing_probe_indices'], [1])
    def test_bad_rows_and_restart_overlap_fail(self):
        for value in ('1 (1 2)\n', '1 (1 2 3)\n1 (2 3 4)\n', '1 (nan 1 2)\n', '1 (1 2 3)\n2 (3 4 5) (1 2 3)\n'):
            with self.subTest(value=value):
                p = self.put('U', value)
                with self.assertRaises(ValueError): probe_statistics(p, 0, 2)
    def test_field_uniform_nonuniform_and_bad_count(self):
        p = self.put('field', 'FoamFile {format ascii;}\ninternalField nonuniform List<vector>\n2\n(\n(1 2 3)\n(4 5 6)\n)\n;\n')
        self.assertEqual(field_values(p).tolist(), [[1,2,3],[4,5,6]])
        p = self.put('uniform', 'internalField uniform (1 2 3);')
        self.assertEqual(field_values(p, 2).tolist(), [[1,2,3],[1,2,3]])
        with self.assertRaises(ValueError): field_values(p)
        p = self.put('binary', 'FoamFile {format binary;}\ninternalField uniform 0;')
        with self.assertRaises(ValueError): field_values(p, 2)
    def test_log_unknown_is_not_zero(self):
        p = self.put('log', 'nProcs : 16\nTime = 2\nEnd\n')
        result = log_statistics(p)
        self.assertTrue(result['end_seen'])
        self.assertIsNone(result['max_courant'])
        self.assertIsNone(result['max_abs_cumulative_continuity'])
    def test_explicit_public_metric_mask_scale_and_aggregation(self):
        self.assertEqual(normalized_absolute_error([1,3,999], [0,1,0], 2, [0,1]), .75)
        # Opposite per-point changes have equal case means but nonzero spread.
        self.assertEqual(pointwise_case_spread([[0,2],[2,0]], 2), 1)
        with self.assertRaises(ValueError): normalized_absolute_error([1,2], [1], 2)
        with self.assertRaises(ValueError): normalized_absolute_error([1,2], [1,2], 0)
    def test_interface_generic_nested_diagnostics_and_enum(self):
        source = '''
def finite_gate_diagnostics(value):
    limits = {"drift": 0.05, "courant": 1.0}
    return all(value.get(key) <= limit for key, limit in limits.items())
def evaluate_task(root):
    simulation = load(root / "simulation_metrics.json")
    evidence = load(root / "evidence_manifest.json")
    if any(item.get("task_id") != TASK_ID for item in (simulation, evidence)):
        raise ValueError("id")
    submitted = simulation.get("case_results")
    for case in submitted:
        finite_gate_diagnostics(case.get("gate_diagnostics"))
    products = evidence.get("products")
    for item in products:
        if item.get("kind") != "probes": raise ValueError("kind")
'''
        description = compile_interfaces(source, {'task_id':'arbitrary-public-id','expected_case_ids':['case-A']}, [])
        self.assertEqual(description['nested_numeric_fields']['gate_diagnostics'], ['courant','drift'])
        self.assertIn('task_id', description['documents']['simulation_metrics.json']['root_fields_read'])
        text = json.dumps(description)
        self.assertNotIn('0.05', text)  # no evaluator thresholds exported
        self.put('simulation_metrics.json', json.dumps({'task_id':'arbitrary-public-id','case_results':[{'case_id':'case-A','all_run_gates_pass':True,'gate_diagnostics':{'courant':.1}}]}))
        self.put('evidence_manifest.json', json.dumps({'task_id':'arbitrary-public-id','products':[{'kind':'probe'}]}))
        errors = inspect_interfaces(self.root, description)['errors']
        self.assertTrue(any('drift' in x for x in errors))
        self.assertTrue(any('kind_not' in x for x in errors))
    def prepare_case(self):
        case = self.root/'case'
        self.put('case/system/controlDict', 'endTime 10;')
        self.put('case/0/U', 'internalField uniform (1 0 0);')
        ledger = Ledger()
        ledger.refresh(self.root)
        return case, ledger
    def test_json_loads_nested_metric_and_literal_evidence_alias(self):
        source = '''
def evaluate_task(root):
    result = json.loads((root / "mesh_metrics.json").read_text())
    primary = result.get("primary_metric", {})
    if primary.get("id") != PRIMARY_ID or primary.get("unit") != "errors" or float(primary.get("value")) != 0:
        raise ValueError("contract")
    manifest = load(root / "raw_manifest.json")
    evidence = manifest.get("evidence")
    required = ("probe_U", "solver_log")
    for key in required:
        item = evidence[key]
        if item.get("sha256") != sha256(item.get("path")): raise ValueError("hash")
'''
        d = compile_interfaces(source, {}, [])
        self.assertEqual(d['nested_object_fields']['mesh_metrics.json']['primary_metric'], ['id','unit','value'])
        self.assertEqual(d['evidence_mapping_roles']['raw_manifest.json']['evidence'], ['probe_U','solver_log'])
        self.put('mesh_metrics.json', '{}')
        self.put('raw_manifest.json', '{"raw_evidence":{}}')
        self.assertTrue(any('expected_evidence_mapping' in x for x in inspect_interfaces(self.root,d)['errors']))
    def test_mesh_new_decomposed_initial_fields_are_outputs(self):
        case, ledger = self.prepare_case()
        before = fingerprint(case)
        self.put('case/processor0/0/U', 'internalField uniform (1 0 0);')
        self.put('case/log.mesh', 'Mesh OK')
        ledger.refresh(self.root)
        result = ledger.record(self.root, 'case', 'mesh', ['case/log.mesh'], {'exit_code':0,'timed_out':False,'cpu_seconds':1}, 1, before)
        self.assertTrue(result['recorded'])
    def test_root_physics_and_existing_processor_changes_still_reject(self):
        for changed in ('case/0/U', 'case/processor0/0/U'):
            with self.subTest(changed=changed):
                case, ledger = self.prepare_case()
                self.put('case/processor0/0/U', 'internalField uniform (1 0 0);')
                before = fingerprint(case)
                self.put(changed, 'internalField uniform (9 0 0);')
                self.put('case/log.mesh', 'Mesh OK')
                ledger.refresh(self.root)
                result = ledger.record(self.root, 'case', 'mesh', ['case/log.mesh'], {'exit_code':0,'timed_out':False,'cpu_seconds':1}, 1, before)
                self.assertFalse(result['recorded'])
    def test_solve_checks_decomposed_inputs(self):
        case, ledger = self.prepare_case()
        before = fingerprint(case)
        self.put('case/processor0/0/U', 'internalField uniform (1 0 0);')
        self.put('case/log.solve', 'End')
        ledger.refresh(self.root)
        result = ledger.record(self.root, 'case', 'solve', ['case/log.solve'], {'exit_code':0,'timed_out':False,'cpu_seconds':1}, 1, before)
        self.assertFalse(result['recorded'])


if __name__ == '__main__': unittest.main()
