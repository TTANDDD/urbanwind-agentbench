import importlib.util
import json
import sys
import tempfile
import tarfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.interfaces import compile_interfaces, inspect_interfaces
from urbanwind_harness.tool_limits import action_timeout
from urbanwind_harness.runner import grade


class TrajectoryRepairTests(unittest.TestCase):
    def test_missing_nonfinite_diagnostic_does_not_crash_finite_score_record(self):
        class FixtureSandbox:
            def execute(self, command, log_dir, **kwargs):
                log_dir.mkdir(parents=True)
                (log_dir/'stdout.txt').write_text('{"score":45,"errors":["case_id_mismatch"],"diagnostics":{"hidden_delta":Infinity}}')
                return {'exit_code': 0, 'timed_out': False, 'cpu_seconds': 0}
        with tempfile.TemporaryDirectory() as directory:
            result = grade(FixtureSandbox(), Path(directory)/'grader', Path(directory)/'run', 30)
            self.assertEqual(result['score'], 45)
            self.assertIsNone(result['result']['diagnostics']['hidden_delta'])
            self.assertEqual(result['result']['missing_nonfinite_diagnostic_paths'], ['diagnostics.hidden_delta'])
            json.dumps(result, allow_nan=False)

    def test_shell_is_bounded_but_intended_solver_keeps_its_budget(self):
        self.assertEqual(action_timeout({'action': 'shell', 'timeout_s': 9000}, 10000)[0], 120)
        self.assertEqual(action_timeout({'action': 'run', 'stage': 'solve'}, 2000)[0], 1700)
        with self.assertRaises(ValueError):
            action_timeout({'action': 'shell', 'timeout_s': float('nan')}, 1000)

    def test_flat_case_does_not_satisfy_nested_case_contract(self):
        source = """def evaluate(root):
 evidence=load(root/'evidence.json')
 reported=load(root/'results.json')
 case=evidence.get('case')
 submitted=reported.get('case_result')
 hidden=load(Path('/private/reference.json'))
"""
        fmt = compile_interfaces(source, {'task_id': 'task', 'expected_case_ids': ['case-1']}, [])
        self.assertNotIn('reference.json', fmt['documents'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('evidence.json', 'results.json'):
                (root/name).write_text(json.dumps({'case_id': 'case-1'}))
            errors = inspect_interfaces(root, fmt)['errors']
            self.assertEqual(len(errors), 2)
            (root/'evidence.json').write_text(json.dumps({'case': {'case_id': 'case-1'}}))
            (root/'results.json').write_text(json.dumps({'case_result': {'case_id': 'case-1'}}))
            self.assertEqual(inspect_interfaces(root, fmt)['errors'], [])

    def test_evidence_parse_failure_cannot_keep_eight_report_points(self):
        workspace = Path(__file__).resolve().parents[2]
        task = workspace/'task-urbanwind/task-062'
        base = workspace/'harness-v0.2.3-deepseek'
        if not (base/'scripts/prepare_revision.py').is_file():
            base = Path('/home/abc/urbanwind-harness-v0.2.3-deepseek-20261007')
        spec = importlib.util.spec_from_file_location('revision_builder', base/'scripts/prepare_revision.py')
        builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        original_bundle = Path('/home/abc/urbanwind-harness-v0.1-20261004/bundle')
        source_path = task/'eval.py' if task.is_dir() else original_bundle/'trusted/task-062/eval.py'
        source = builder.repair_evaluator(source_path.read_text(encoding='utf-8'))
        stub = types.ModuleType('contract_evaluator')
        stub.evaluate = lambda *args: {'score': 60.0, 'errors': []}
        with patch.dict(sys.modules, {'contract_evaluator': stub}):
            module = types.ModuleType('repaired_task062')
            exec(compile(source, 'repaired_task062', 'exec'), module.__dict__)
        root = task/'init_file'
        if not root.is_dir():
            fixture = tempfile.TemporaryDirectory()
            self.addCleanup(fixture.cleanup)
            root = Path(fixture.name)
            with tarfile.open(original_bundle/'public/task-062/init_file.tar.gz') as archive:
                for name in ('local_assets/profile_law_contract.json', 'local_assets/profile_law_targets.csv'):
                    member = next(m for m in archive.getmembers() if m.name.lstrip('./') == name)
                    target = root/name; target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
        contract = json.loads((root/'local_assets/profile_law_contract.json').read_text(encoding='utf-8'))
        fake = {
            'profile_law_contract.json': contract,
            'urbanwind-task-062-reference.json': {'task_id': module.TASK_ID, 'case_results': []},
            'profile_law_evidence_manifest.json': {'task_id': module.TASK_ID, 'cases': [
                {'case_id': c['case_id'], 'variant_key': c['variant_key']} for c in contract['cases']]},
            'profile_law_results.json': {'task_id': module.TASK_ID, 'case_results': [
                {'case_id': c['case_id'], 'variant_key': c['variant_key']} for c in contract['cases']]},
            'metrics.json': {},
        }
        module.load = lambda path, *args: fake[path.name]
        result = module.evaluate_task(root)
        self.assertEqual(result['score'], 60.0)
        self.assertEqual(result['components']['reported_array_recomputation'], 0.0)
        self.assertEqual(result['diagnostics']['verified_case_ids'], [])
        self.assertTrue(any('probe_U' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()
