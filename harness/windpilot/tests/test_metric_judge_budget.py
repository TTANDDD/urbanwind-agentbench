import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urbanwind_harness.primary_metrics import verify_primary_metrics
from urbanwind_harness.agent_judge import build_payload,validate_verdict
from urbanwind_harness.common import write
from urbanwind_harness.open_runner import _public_file
from urbanwind_harness.review_evidence import deterministic_feedback
from test_evidence_review_regressions import document
import test_open_runner as fixtures
from test_open_runner import response,tool


class PrimaryMetricChecks(unittest.TestCase):
    def fixture(self,p,primary=True):
        c=p/'canonical';w=p/'workspace';c.mkdir();w.mkdir()
        write(c/'task_spec.json',{'required_artifacts':['mesh_metrics.json','metrics.json']})
        write(c/'reporting_contract.json',{'metric_contracts':[{'id':'errors','unit':'count'}]})
        value={'id':'errors','unit':'count','value':0}
        write(w/'mesh_metrics.json',{'primary_metric' if primary else 'metric':value})
        write(w/'metrics.json',{'values':{'errors':0},'units':{'errors':'count'},'primary_metric':value})
        return c,w
    def test_real_alias_failure_detected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w=self.fixture(Path(tmp),False);before={f.name:f.read_bytes() for f in w.iterdir()}
            r=verify_primary_metrics(w,c,_public_file)
            self.assertEqual(r['findings'][0]['pointer'],'/primary_metric')
            self.assertEqual(r['findings'][0]['observed_alias'],'metric')
            self.assertEqual(before,{f.name:f.read_bytes() for f in w.iterdir()})
            checks={'errors':r['errors'],'public_numerical_checks':{'primary_metric_consistency':r}}
            self.assertEqual(deterministic_feedback(checks)['agent_judge']['issues'],r['findings'])
    def test_consistent_primary_passes_without_requiring_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w=self.fixture(Path(tmp));self.assertFalse(verify_primary_metrics(w,c,_public_file)['errors'])
            write(w/'mesh_metrics.json',{'primary_metric':{'id':'errors','unit':'count','value':8}})
            write(w/'metrics.json',{'values':{'errors':8},'units':{'errors':'count'}})
            self.assertFalse(verify_primary_metrics(w,c,_public_file)['errors'])
    def test_invalid_values_units_and_report_mismatch(self):
        for field,bad in [('value',True),('value',float('nan')),('unit','wrong'),('id','other'),('value',7)]:
            with self.subTest(field=field,bad=bad),tempfile.TemporaryDirectory() as tmp:
                c,w=self.fixture(Path(tmp));raw=json.loads((w/'mesh_metrics.json').read_text());raw['primary_metric'][field]=bad
                (w/'mesh_metrics.json').write_text(json.dumps(raw));self.assertTrue(verify_primary_metrics(w,c,_public_file)['errors'])
    def test_unrelated_artifact_not_forced_into_mesh_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,w=self.fixture(Path(tmp));write(c/'task_spec.json',{'required_artifacts':['result.json']})
            (w/'mesh_metrics.json').unlink();self.assertFalse(verify_primary_metrics(w,c,_public_file)['errors'])


def large_evidence():
    runs=[{'case_id':'case-'+str(i),'commands':['python '+'x'*6000]*4,'exit_code':7 if i==21 else 0,
           'started_at':'t1','ended_at':'t2','notes':'long untrusted report '*1000,
           'evidence':[{'path':'raw-'+str(i),'sha256':hashlib.sha256(str(i).encode()).hexdigest()}]} for i in range(24)]
    return {'public_documents':[document('acceptance_contract.json',{'required':['case-'+str(i) for i in range(24)]})],
            'selected_public_artifacts':[document('run_manifest.json',{'runs':runs}),document('answer.json',{'observed':1})],
            'registered_execution_evidence':[{'path':'harness_trace/'+str(i).zfill(3)+'/execution.json','sha256':'b'*64,
                    'exit_code':9 if i==23 else 0,'stdout_tail':'x'*600,'stderr_tail':'e'*400,'command_excerpt':'run'} for i in range(24)]}


class JudgeBudgetTests(unittest.TestCase):
    def test_realistic_large_payload_keeps_all_cases_exits_and_hashes_under_old_cap(self):
        p=build_payload(large_evidence(),{'errors':[]});self.assertIsNotNone(p)
        self.assertLessEqual(len(json.dumps(p,ensure_ascii=False).encode()),96*1024)
        runs=p['evidence']['selected_public_artifacts'][0]['content']['runs']
        self.assertEqual(len(runs),24);self.assertEqual(runs[21]['exit_code'],7)
        self.assertEqual(runs[21]['evidence'][0]['path'],'raw-21')
        self.assertEqual(len(p['evidence']['registered_execution_evidence']),24)
        self.assertEqual(p['evidence']['registered_execution_evidence'][23]['exit_code'],9)
        self.assertIn('harness_trace/023/execution.json',p['evidence_hashes'])
    def test_changed_omitted_fields_invalidate_snapshot(self):
        e=large_evidence();first=build_payload(e,{'errors':[]});v=json.loads(e['selected_public_artifacts'][0]['text']);v['runs'][0]['notes']='changed';e['selected_public_artifacts'][0]=document('run_manifest.json',v)
        self.assertNotEqual(first['snapshot_sha256'],build_payload(e,{'errors':[]})['snapshot_sha256'])
    def test_hard_error_cannot_be_overruled_after_budgeting(self):
        p=build_payload(large_evidence(),{'errors':['primary_metric_missing']})
        v={'verdict':'ready_for_submission','issues':[],'checked_evidence':[{'file':'run_manifest.json','sha256':p['evidence_hashes']['run_manifest.json']}],'limitations':[]}
        self.assertFalse(validate_verdict(json.dumps(v),p)['valid'])


class LargeJudgeIntegrationTests(unittest.TestCase):
    fixture=fixtures.OpenRunnerTests.fixture
    factories=fixtures.OpenRunnerTests.factories
    grade=staticmethod(fixtures.OpenRunnerTests.grade)
    run_fixture=fixtures.OpenRunnerTests.run_fixture
    def test_oversized_evidence_reaches_one_real_mock_model_request(self):
        ev=large_evidence();ev['deterministic_public_verification']={'errors':[]}
        p=build_payload(ev,{'errors':[]})
        verdict={'verdict':'repair_required','issues':[{'file':'run_manifest.json','pointer':'/runs/21/exit_code','requirement':'successful actual run','observed':'exit 7','repair':'investigate preserved failure'}],
                 'checked_evidence':[{'file':'run_manifest.json','sha256':p['evidence_hashes']['run_manifest.json']}],'limitations':['public only']}
        with tempfile.TemporaryDirectory() as tmp,patch('urbanwind_harness.open_runner.reviewer_evidence',return_value=ev):
            r,_,requests=self.run_fixture(Path(tmp),[response([tool('r','review',{})]),response(content=json.dumps(verdict)),response([tool('f','finish',{})]),response(content=json.dumps(verdict))],architecture='verified_judge',structural_checker=lambda *a:({'errors':[]},0))
            self.assertGreaterEqual(r['reviewer_api_turns'],1);self.assertLessEqual(r['reviewer_api_turns'],2)
            self.assertIn('case-23',json.dumps(requests))
