import json
import tempfile
import unittest
from pathlib import Path
import test_open_runner as fixtures
from test_open_runner import response, tool
from urbanwind_harness.common import write, load
from urbanwind_harness.open_runner import _public_file, reviewer_evidence
from urbanwind_harness.open_contracts import compile_public_contract, validate_delivery, normalize_delivery
from urbanwind_harness.public_verification import summarize_artifact, verify_public, probe_observation, half_windows


class CompilerRegression(unittest.TestCase):
    def test_half_windows_include_shared_boundary_and_positive_exchange(self):
        result=half_windows([60,120,180],[[[0,0,-2]],[[0,0,4]],[[0,0,2]]],[[300],[302],[304]],[[60,120],[120,180]])
        self.assertEqual(result['60_120'],{'vertical_exchange_m_s':2,'temperature_k':301})
        self.assertEqual(result['120_180'],{'vertical_exchange_m_s':3,'temperature_k':303})
    def test_tuple_and_list_document_identity(self):
        for container in ('(a,b)', '[a,b]'):
            source = f'''a=load(root/'a.json')
b=load(root/'b.json')
for doc in {container}:
    if doc.get('task_id') != 'PRIVATE_UNEXPOSED_ID': raise ValueError()
'''
            form=compile_public_contract(source, {'task_id':'public-id'})['submission_format']
            for name in ('a.json','b.json'):
                self.assertIn('task_id',form['documents'][name]['root_fields_read'])
            self.assertNotIn('PRIVATE_UNEXPOSED_ID',json.dumps(form))
            self.assertEqual(len(validate_delivery({'a.json':{},'b.json':{}},form)['errors']),2)
            original={'a.json':{'computed':3.141},'b.json':{'task_id':'wrong'}}
            fixed=normalize_delivery(original,form)
            self.assertEqual(fixed['documents']['a.json'],{'computed':3.141,'task_id':'public-id'})
            self.assertEqual(original['b.json']['task_id'],'wrong')

    def test_absolute_guest_paths_and_large_json_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); write(p/'large.json',{'task_id':'demo','array':list(range(60000))})
            item=summarize_artifact(_public_file,p,'/home/user/Desktop/large.json')
            self.assertNotIn('omitted',item)
            self.assertEqual(item['structured_summary']['array']['array_summary']['shape'],[60000])
            self.assertEqual(item['structured_summary']['task_id'],'demo')
            self.assertIn('omitted',_public_file(p,'/home/user/Desktop/../secret'))
            self.assertIn('omitted',_public_file(p,'/etc/passwd'))

    def test_public_shape_probe_and_residual_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); c=p/'canonical'; w=p/'workspace'; c.mkdir(); w.mkdir()
            contract={'task_id':'demo','required_manifest':'evidence.json','response_points_m':[[1,.0625,2]],
                      'frozen_gates':{'max_last20_initial_residual_full':.0001},
                      'simulation_metrics':{'path':'results.json','array_shapes':{'velocity':[2,1,3]}}}
            write(c/'local_assets/demo_contract.json',contract)
            write(c/'reporting_contract.json',{'demo':contract})
            write(c/'submission_format.json',{'documents':{'results.json':{},'evidence.json':{}}})
            (w/'results.json').write_text(json.dumps({'case_results':[{'case_id':'a','velocity':[[[1,2,float('nan')]]]}]}))
            write(w/'evidence.json',{'case':{'response_U':{'path':'U','sha256':'unused'},'solver_log':{'path':'log','sha256':'unused'}}})
            (w/'U').write_text('# Probe 0 (1 0 2)\n1 (1 2 3)\n2 (1 2 3)\n')
            (w/'log').write_text('Solving for Ux, Initial residual = 0.0002, Final residual = 0.00001\n')
            check=verify_public(w,c,_public_file)
            self.assertTrue(any('shape_expected' in x for x in check['errors']))
            self.assertTrue(any('coordinates_differ' in x for x in check['errors']))
            self.assertTrue(any('raw_last20_residual' in x for x in check['errors']))
            (w/'U').write_text('# Probe 0 (1 0.0625 2)\n1 (1 2 3)\n2 (1 2 3)\n')
            self.assertFalse(any('coordinates_differ' in x for x in verify_public(w,c,_public_file)['errors']))


class PlanReviewTests(unittest.TestCase):
    fixture = fixtures.OpenRunnerTests.fixture
    factories = fixtures.OpenRunnerTests.factories
    grade = staticmethod(fixtures.OpenRunnerTests.grade)
    run_fixture = fixtures.OpenRunnerTests.run_fixture
    def test_plan_verify_repair_counts_all_roles_without_extra_budget(self):
        responses=[response(content='Plan all cases and verify.',tokens=5),
                   response([tool('done1','finish',{})]),
                   response(content='Repair missing task identity.',tokens=7),
                   response([tool('repair','shell',{'command':'repair_public_delivery'})]),
                   response([tool('done2','finish',{})]),
                   response(content='Recheck complete.',tokens=11),
                   response([tool('done3','finish',{})])]
        def checker(*args):return {'errors':['missing_identity'],'advisory_only':True},0.5
        with tempfile.TemporaryDirectory() as tmp:
            result, executions, requests=self.run_fixture(Path(tmp),responses,architecture='plan_review',structural_checker=checker)
            self.assertEqual(result['termination'],'agent_finished')
            self.assertEqual(result['steps'],5)
            self.assertEqual(result['planner_api_turns'],1)
            self.assertEqual(result['reviewer_api_turns'],2)
            self.assertEqual(result['api_turns'],7)
            self.assertEqual(result['usage']['total_tokens'],35)
            self.assertEqual(result['finish_deferrals'],2)
            self.assertEqual(executions[0][0],'repair_public_delivery')
            self.assertEqual((Path(tmp)/'run/workspace/agent_plan.md').read_text(),'Plan all cases and verify.')
            self.assertTrue(result['verification_checkpoint_done'])

    def test_forced_checkpoint_leaves_repair_budget(self):
        responses=[response(content='Plan'),response([tool(str(i),'shell',{'command':'work'}) for i in range(110)]),
                   response(content='Review actual work'),response([tool('done','finish',{})]),response(content='Final review')]
        with tempfile.TemporaryDirectory() as tmp:
            result, executions, requests=self.run_fixture(Path(tmp),responses,architecture='plan_review',structural_checker=lambda *args:({'errors':[]},0))
            self.assertEqual(result['status'],'evaluated')
            self.assertEqual(result['steps'],112)
            self.assertEqual(len(executions),109)
            self.assertTrue(result['verification_checkpoint_done'])
            self.assertEqual(result['finish_deferrals'],0)


if __name__=='__main__':unittest.main()
