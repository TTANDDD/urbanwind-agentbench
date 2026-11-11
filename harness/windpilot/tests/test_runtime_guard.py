import json
import tempfile
import unittest
from pathlib import Path
import test_open_runner as fixtures
from test_open_runner import response, tool
from urbanwind_harness.common import load
from urbanwind_harness.public_verification import log_observation
from urbanwind_harness.runtime_guard import command_allocation, runtime_budget, actor_start_floor

class FatalEvidenceTests(unittest.TestCase):
    def test_normal_banner_keeps_completion_and_numerical_observations(self):
        text='trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE).\nTime = 400\ntime step continuity errors : cumulative = 0.0002\nEnd\n'
        obs=log_observation(text)
        self.assertFalse(obs['fatal_seen'])
        self.assertEqual(obs['benign_trapfpe_banner_count'],1)
        self.assertEqual(obs['final_time'],400)
        self.assertEqual(obs['max_abs_cumulative_continuity'],0.0002)
        self.assertTrue(obs['end_seen'])

    def test_real_fatal_after_banner_remains_visible_with_line_evidence(self):
        banner='[0] trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE)\n'
        for error in ['--> FOAM FATAL IO ERROR: wrong field', 'Floating point exception (core dumped)', '[3] Segmentation fault', 'trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE) FOAM FATAL ERROR']:
            with self.subTest(error=error):
                obs=log_observation(banner+error)
                self.assertTrue(obs['fatal_seen'])
                self.assertEqual(obs['fatal_event_count'],1)
                self.assertEqual(obs['fatal_event_examples'][0]['line'],2)

class AllocationTests(unittest.TestCase):
    def test_unbounded_exploration_leaves_budget_for_recovery(self):
        timeout,record=command_allocation({'action':'shell','command':'python3 search.py'},18830)
        self.assertEqual(timeout,300)
        self.assertFalse(record['timeout_was_explicit'])
        self.assertFalse(record['total_budget_extended'])
        self.assertEqual(record['delivery_seconds_reserved_from_current_budget'],600)

    def test_long_physical_solve_keeps_explicit_duration(self):
        self.assertEqual(command_allocation({'timeout_s':8000,'stage':'solve'},18000)[0],8000)
        self.assertEqual(command_allocation({'timeout_s':99999},18000)[0],17400)
        self.assertEqual(command_allocation({'timeout_s':99999},100)[0],80)
        self.assertEqual(command_allocation({'timeout_s':2},100)[0],2)

    def test_invalid_timeout_and_deadline_are_not_executed(self):
        for value in [0,-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):command_allocation({'timeout_s':value},1000)
            with self.assertRaises(ValueError):command_allocation({},value)

    def test_deadline_feedback_excludes_grading_and_does_not_extend_limits(self):
        self.assertEqual(runtime_budget(700,10000,5)['phase'],'delivery')
        self.assertEqual(runtime_budget(7000,10000,5)['phase'],'execute')
        self.assertEqual(actor_start_floor(18000),60)
        self.assertAlmostEqual(actor_start_floor(10),0.2)

class RuntimeIntegrationTests(unittest.TestCase):
    fixture=fixtures.OpenRunnerTests.fixture
    grade=staticmethod(fixtures.OpenRunnerTests.grade)
    run_fixture=fixtures.OpenRunnerTests.run_fixture
    def factories(self,responses,executions,requests):
        client,base=fixtures.OpenRunnerTests.factories(self,responses,executions,requests)
        class Sandbox(base):
            def execute(self,command,log_dir,**kwargs):
                out=super().execute(command,log_dir,**kwargs)
                if command=='bounded_search':out.update(exit_code=-15,timed_out=True)
                return out
        return client,Sandbox

    def test_timeout_returns_to_actor_and_keeps_receipts_and_final_grading(self):
        answers=[response([tool('search','shell',{'command':'bounded_search'})]),
                 response([tool('recover','shell',{'command':'write_partial_results','timeout_s':5})])]
        answers += [response([tool('finish'+str(i),'finish',{}) for i in range(148)])]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            result,executions,requests=self.run_fixture(p,answers,architecture='verified_judge',
                structural_checker=lambda *a:({'errors':['incomplete_public_artifacts']},0))
            self.assertEqual(executions[0][1]['timeout'],300)
            self.assertEqual(executions[1][1]['timeout'],5)
            first=load(p/'run/trace/001-observation.json')
            self.assertTrue(first['execution']['timed_out'])
            self.assertIn('timeout_recovery',first)
            self.assertLess(first['runtime_budget']['execution_seconds_remaining'],999)
            self.assertEqual(load(p/'run/commands/001/execution.json')['runtime_allocation']['effective_timeout_seconds'],300)
            self.assertEqual(result['status'],'evaluated')
            self.assertEqual(result['steps'],150)
            self.assertEqual(result['actor_api_turns'],3)
            self.assertEqual(result['reviewer_api_turns'],0)
            self.assertIn('Current runtime_budget',json.dumps(requests[0][0]))
            self.assertNotIn('PRIVATE_REFERENCE_DO_NOT_EXPOSE',json.dumps(requests))

if __name__=='__main__':unittest.main()
