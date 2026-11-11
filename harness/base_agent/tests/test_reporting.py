import json
import tempfile
import unittest
from pathlib import Path

from urbanwind_harness.common import write
from urbanwind_harness.report import summarize
from urbanwind_harness.runner import grade


class ReportingTests(unittest.TestCase):
    def test_continuous_score_is_not_binary_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            class FakeSandbox:
                def execute(self, command, log_dir, **kwargs):
                    log_dir.mkdir()
                    (log_dir/'stdout.txt').write_text(json.dumps({'score': 92.5, 'errors': []}))
                    return {'exit_code': 0, 'timed_out': False}
            result = grade(FakeSandbox(), root/'unused', root, 10)
            self.assertTrue(result['error_free'])
            self.assertFalse(result['full_score'])
            self.assertEqual(result['score'], 92.5)

    def test_infrastructure_and_missing_tasks_are_never_imputed_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root/'dispatch.json', {'conditions': ['baseline', 'full'], 'protocol_sha256': 'fixture'})
            write(root/'results.json', [
                {'task': 'a', 'condition': 'baseline', 'status': 'evaluated', 'score': 10},
                {'task': 'a', 'condition': 'full', 'status': 'evaluated', 'score': 30},
                {'task': 'b', 'condition': 'baseline', 'status': 'infra_incomplete', 'score': None}])
            protocol = {'budgets': [{'task': 'a'}, {'task': 'b'}], 'main_holdout_tasks': ['a', 'b'],
                'development_tasks': [], 'metrics': {}}
            result = summarize(root, protocol)
            self.assertEqual(result['evaluated_runs'], 2)
            self.assertEqual(result['planned_runs'], 4)
            self.assertFalse(result['complete'])
            self.assertEqual(result['conditions']['baseline']['mean_score_on_evaluated_tasks'], 10)
            self.assertEqual(result['paired_scores']['baseline_to_full_holdout29']['pairs'], 1)
            self.assertEqual(result['paired_scores']['baseline_to_full_holdout29']['mean_score_difference'], 20)
