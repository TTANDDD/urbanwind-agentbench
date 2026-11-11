import hashlib,json,tempfile,unittest,os
from pathlib import Path
from unittest.mock import patch
import test_open_runner as fixtures
from test_open_runner import response,tool
from urbanwind_harness.foam_dictionary import entry,boundary_type,function_setting,uniform_number
from urbanwind_harness.agent_judge import build_payload,validate_verdict
from urbanwind_harness.acceptance import public_contract,verify_acceptance
from urbanwind_harness.open_runner import _public_file
from urbanwind_harness.common import write
from urbanwind_harness.provenance_export import export


class RealFailureRegressions(unittest.TestCase):
    def test_foam_spacing_comments_and_decoy_patch(self):
        text='// inlet { type fixedProfile; }\nboundaryField { wrong {type fixedProfile;} inlet { type /* comment */ atmBoundaryLayerInletVelocity ; z0 uniform 1e-2; } }'
        self.assertFalse(boundary_type(text,'inlet','fixedProfile'))
        self.assertTrue(boundary_type(text,'inlet','atmBoundaryLayerInletVelocity'))
        self.assertTrue(uniform_number(text,'inlet','z0',.01))
        control='endTime 400; writeInterval 20; functions { responseProbes { writeInterval 10; } }'
        self.assertFalse(function_setting(control,'responseProbes','writeInterval',20))
        self.assertTrue(function_setting(control.replace('10;','20 ;'),'responseProbes','writeInterval',20))
    def test_nested_sampling_list_and_unresolved_include(self):
        text='functions { sample { sets (cloud { type cloud; points ((1 2 3)); }); writeInterval 20; } }'
        self.assertTrue(function_setting(text,'sample','writeInterval',20))
        with self.assertRaises(ValueError):entry('#include "secret"\nendTime 400;','endTime')
    def test_real_062_spacing_gates(self):
        fixture=Path(__file__).resolve().parent/'fixtures/public-062-more-details.json'
        if not fixture.exists():self.skipTest('separate live-artifact replay runs on remote')
        for row in json.loads(fixture.read_text(encoding='utf-8'))['task-062']:
            u=row['field_U']['text'];c=row['controlDict']['text']
            self.assertTrue(function_setting(c,'responseProbes','writeInterval',20))
            if row['case'].endswith('001'):
                self.assertTrue(boundary_type(u,'inlet','fixedProfile'));self.assertEqual(entry(u,'boundaryField','inlet','profile'),'csvFile')
            else:self.assertTrue(uniform_number(u,'inlet','z0',.01))
    def test_064_bad_manifest_lists_missing_fields_and_csv_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c=p/'canonical';w=p/'workspace';c.mkdir();w.mkdir()
            report={'task_id':'urbanwind-task-064','expected_case_ids':['task-064-case-001'],'expected_solver_case_ids':['task-064-case-001']}
            write(c/'acceptance_contract.json',public_contract('task-064',report))
            write(c/'local_assets/a_contract.json',{'required_manifest':'evidence.json'})
            write(w/'run_manifest.json',{'runs':[{'case_id':'task-064-case-001','started_at':1,'ended_at':2,'exit_code':0,'evidence':{}}]})
            (w/'profile.csv').write_text('sample_id,z,Ux\n0,1,2\n')
            write(w/'evidence.json',{'cases':[{'case_id':'task-064-case-001','profile_csv':{'path':'profile.csv','sha256':hashlib.sha256((w/'profile.csv').read_bytes()).hexdigest()}}]})
            result=verify_acceptance(w,c,_public_file);findings=result['findings']
            self.assertTrue(any(x['pointer']=='/runs/0/commands' for x in findings))
            self.assertTrue(any(x['pointer']=='/runs/0/software' for x in findings))
            self.assertTrue(any(x['code']=='csv_layout' for x in findings))
            self.assertTrue(any(x['pointer']=='/runs/0/evidence' for x in findings))


class JudgeContractTests(unittest.TestCase):
    def payload(self,checks=None):return build_payload({'path':'answer.json','sha256':'a'*64,'text':'actual answer'},checks or {'errors':[]})
    def verdict(self):return {'verdict':'ready_for_submission','issues':[],'checked_evidence':[{'file':'answer.json','sha256':'a'*64}],'limitations':['public only']}
    def test_cannot_override_deterministic_failure_or_fake_hash(self):
        v=self.verdict()
        self.assertEqual(validate_verdict(json.dumps(v),self.payload({'errors':['bad residual']}))['verdict'],'insufficient_evidence')
        v['checked_evidence'][0]['sha256']='b'*64
        self.assertFalse(validate_verdict(json.dumps(v),self.payload())['valid'])
    def test_prose_and_missing_evidence_do_not_pass(self):
        self.assertFalse(validate_verdict('Looks good. PASS.',self.payload())['valid'])
        v=self.verdict();v['checked_evidence']=[]
        self.assertFalse(validate_verdict(json.dumps(v),self.payload())['valid'])
        v=self.verdict();v['verdict']='repair_required'
        self.assertFalse(validate_verdict(json.dumps(v),self.payload())['valid'])
    def test_valid_readiness_is_snapshot_specific(self):
        p=self.payload();v=validate_verdict(json.dumps(self.verdict()),p)
        self.assertTrue(v['valid'])
        changed=build_payload({'path':'answer.json','sha256':'b'*64,'text':'changed'}, {'errors':[]})
        self.assertNotEqual(changed['snapshot_sha256'],v['snapshot_sha256'])
        self.assertFalse(validate_verdict(json.dumps(self.verdict()),changed)['valid'])


class ProvenanceReceiptTests(unittest.TestCase):
    def test_foreground_receipt_builds_schema_but_background_launcher_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write(root/'reporting_contract.json',{'task_id':'demo','expected_case_ids':['a'],'expected_solver_case_ids':['a']})
            write(root/'harness_trace/runtime.json',{'software':[{'name':'simpleFoam','sha256':'f'*64,'executable_path':'/runtime/bin/simpleFoam'}]})
            r={'command':'simpleFoam','exit_code':0,'started_at_unix':1000,'ended_at_unix':1002,'stdout':'OpenFOAM\nTime = 400\nEnd\n','stderr':'','annotations':{'case':'a'}}
            write(root/'harness_trace/001/execution.json',r)
            (root/'solver.log').write_text(r['stdout'])
            bindings=[{'case_id':'a','execution_records':['001'],'solver_record':'001','evidence_paths':['solver.log']}]
            result=export(root,bindings);run=result['runs'][0]
            self.assertEqual(run['execution_role'],'real_solver_run')
            self.assertEqual(run['software'][0]['name'],'openfoam')
            self.assertIsInstance(run['evidence'],list)
            self.assertEqual(run['evidence'][0]['sha256'],hashlib.sha256((root/'solver.log').read_bytes()).hexdigest())
            r.update(command='simpleFoam > solver.log &',stdout='')
            write(root/'harness_trace/001/execution.json',r)
            with self.assertRaisesRegex(ValueError,'foreground_solver_completion'):export(root,bindings)


@unittest.skipIf(os.name=='nt','queue process ownership uses Linux fcntl')
class QueueFailureTests(unittest.TestCase):
    def test_only_local_output_truncation_is_isolated(self):
        from parallel_full import is_isolated_output_truncation
        self.assertTrue(is_isolated_output_truncation({'termination':'api_incomplete','termination_detail':'truncated_response_not_executed'}))
        for reason in ['authentication_or_balance_block','environment_forced_closed','worker_exit',None]:
            self.assertFalse(is_isolated_output_truncation({'termination':'api_incomplete','termination_detail':reason}))
        self.assertFalse(is_isolated_output_truncation({'termination':'user_cancelled','termination_detail':'truncated_response_not_executed'}))


class JudgeIntegrationTests(unittest.TestCase):
    fixture=fixtures.OpenRunnerTests.fixture
    factories=fixtures.OpenRunnerTests.factories
    grade=staticmethod(fixtures.OpenRunnerTests.grade)
    run_fixture=fixtures.OpenRunnerTests.run_fixture
    def test_hard_failures_do_not_spend_judge_calls_or_get_ready_status(self):
        answers=[response([tool('f'+str(i),'finish',{}) for i in range(150)])]
        with tempfile.TemporaryDirectory() as tmp:
            r,commands,requests=self.run_fixture(Path(tmp),answers,architecture='verified_judge',structural_checker=lambda *a:({'errors':['bad_coordinates']},0))
            self.assertEqual(r['termination'],'completed_with_unresolved_checks')
            self.assertEqual(r['reviewer_api_turns'],0)
            self.assertEqual(r['planner_api_turns'],0)
            self.assertEqual(r['finish_deferrals'],0)
            self.assertEqual(r['steps'],150)
            self.assertEqual(len(requests),1)
            self.assertEqual(r['score'],80) # Only the independent test grader assigns scores.
    def test_judge_is_integrated_and_costed(self):
        ev={'path':'answer.json','sha256':'a'*64,'text':'actual evidence','deterministic_public_verification':{'errors':[]}}
        verdict={'verdict':'ready_for_submission','issues':[],'checked_evidence':[{'file':'answer.json','sha256':'a'*64}],'limitations':['public only']}
        with tempfile.TemporaryDirectory() as tmp,patch('urbanwind_harness.open_runner.reviewer_evidence',return_value=ev):
            r,commands,requests=self.run_fixture(Path(tmp),[response([tool('f','finish',{})]),response(content=json.dumps(verdict),tokens=11)],architecture='verified_judge',structural_checker=lambda *a:({'errors':[]},0))
            self.assertEqual(r['termination'],'agent_finished')
            self.assertEqual(r['reviewer_api_turns'],1)
            self.assertEqual(r['usage']['total_tokens'],14)
            self.assertEqual(r['public_acceptance']['verdict'],'ready_for_submission')
            self.assertNotIn('PRIVATE_REFERENCE_DO_NOT_EXPOSE',json.dumps(requests))
    def test_three_deterministic_repairs_preserve_judge_and_remaining_actions(self):
        checks=iter([{'errors':['raw_report_mismatch']}] * 3 + [{'errors':[]}])
        ev={'path':'answer.json','sha256':'a'*64,'text':'raw-bound answer','deterministic_public_verification':{'errors':[]}}
        verdict={'verdict':'ready_for_submission','issues':[],'checked_evidence':[{'file':'answer.json','sha256':'a'*64}],'limitations':['public only']}
        answers=[response([tool('f'+str(i),'finish',{})]) for i in range(4)]
        answers.append(response(content=json.dumps(verdict)))
        with tempfile.TemporaryDirectory() as tmp,patch('urbanwind_harness.open_runner.reviewer_evidence',return_value=ev) as evidence:
            r,_,requests=self.run_fixture(Path(tmp),answers,architecture='verified_judge',structural_checker=lambda *a:(next(checks),0))
            self.assertEqual(r['termination'],'agent_finished')
            self.assertEqual(r['steps'],4)
            self.assertEqual(r['reviewer_api_turns'],1)
            self.assertEqual(r['finish_deferrals'],0)
            self.assertEqual(evidence.call_count,1)
            self.assertEqual(len(requests),5)
    def test_repaired_snapshot_requires_new_judgment(self):
        first={'path':'answer.json','sha256':'a'*64,'text':'wrong metric','deterministic_public_verification':{'errors':[]}}
        second={**first,'sha256':'b'*64,'text':'recomputed metric'}
        bad={'verdict':'repair_required','issues':[{'file':'answer.json','pointer':'/metric','requirement':'recompute from raw values','observed':'stated metric inconsistent','repair':'recompute, preserve raw data'}],'checked_evidence':[{'file':'answer.json','sha256':'a'*64}],'limitations':[]}
        good={'verdict':'ready_for_submission','issues':[],'checked_evidence':[{'file':'answer.json','sha256':'b'*64}],'limitations':['public only']}
        answers=[response([tool('f1','finish',{})]),response(content=json.dumps(bad)),response([tool('repair','shell',{'command':'recompute'})]),response([tool('f2','finish',{})]),response(content=json.dumps(good))]
        with tempfile.TemporaryDirectory() as tmp,patch('urbanwind_harness.open_runner.reviewer_evidence',side_effect=[first,second]):
            r,commands,requests=self.run_fixture(Path(tmp),answers,architecture='verified_judge',structural_checker=lambda *a:({'errors':[]},0))
            self.assertEqual(r['termination'],'agent_finished')
            self.assertEqual(r['reviewer_api_turns'],2)
            self.assertEqual(r['finish_deferrals'],1)
            self.assertEqual(commands[0][0],'recompute')

if __name__=='__main__':unittest.main()
