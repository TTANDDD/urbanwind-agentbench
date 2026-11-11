import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.api import APIError
from urbanwind_harness.common import digest,write
from urbanwind_harness.runner import run_task


def fixture_bundle(root):
    bundle=root/'bundle';public=bundle/'public/task-041';grader=bundle/'trusted/task-041'
    public.mkdir(parents=True);grader.mkdir(parents=True)
    files={'task_spec.json':{'required_artifacts':[]},'reporting_contract.json':{},'case_plan.json':{},
           'local_assets/case_control_contract.json':{}}
    with tarfile.open(public/'init_file.tar.gz','w:gz') as handle:
        for name,value in files.items():
            data=json.dumps(value).encode();info=tarfile.TarInfo(name);info.size=len(data)
            handle.addfile(info,io.BytesIO(data))
        for name,data in [('local_assets/case_matrix.csv',b'case_id\n'),('contract_evaluator.py',b'# public fixture\n')]:
            info=tarfile.TarInfo(name);info.size=len(data);handle.addfile(info,io.BytesIO(data))
    write(public/'task.json',{'instruction':'Synthetic integration fixture, not benchmark evidence.'})
    for name in ['eval.py','contract_evaluator.py']:(grader/name).write_text('fixture')
    write(bundle/'manifest.json',{'tasks':[{'directory':'task-041',
        'public_archive_sha256':digest(public/'init_file.tar.gz'),'grader_sha256':digest(grader/'eval.py'),
        'precheck_sha256':digest(grader/'contract_evaluator.py'),'reference_sha256':None}]})
    return bundle


class GuidedRunnerTests(unittest.TestCase):
    def test_invalid_call_then_full_log_compaction_and_live_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=fixture_bundle(root);requests=[];timeouts=[]
            class FakeClient:
                def __init__(self,*args,**kwargs): pass
                def complete(self,messages,**kwargs):
                    requests.append(messages)
                    name=['unsupported','shell','finish'][len(requests)-1]
                    args={'command':'read fixture'} if name=='shell' else {'summary':'fixture'}
                    call={'id':str(len(requests)),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
                    return {'content':'','message':{'role':'assistant','content':'','tool_calls':[call]},'usage':{}}
            class FakeSandbox:
                def __init__(self,*args,**kwargs): pass
                def execute(self,command,log_dir,**kwargs):
                    public='public_checker' in kwargs
                    out=json.dumps({'errors':[],'warnings':[]}) if public else 'BEGIN'+('x'*30000)+'END'
                    if not public:timeouts.append(kwargs['timeout'])
                    log_dir.mkdir(parents=True);(log_dir/'stdout.txt').write_text(out);(log_dir/'stderr.txt').write_text('')
                    return {'exit_code':0,'timed_out':False,'wall_seconds':0,'cpu_seconds':0,'stdout':out,'stderr':''}
            with patch('urbanwind_harness.runner.DeepSeekClient',FakeClient),patch('urbanwind_harness.runner.Sandbox',FakeSandbox),patch('urbanwind_harness.runner.grade',return_value={'status':'scored','score':100,'success':True,'result':{'fixture_only':True}}):
                result=run_task(bundle,'task-041',root/'run',root/'unused.key',root/'runtime',condition='full',max_steps=3,active_seconds=2000)
            self.assertEqual(result['steps'],3)
            self.assertEqual(result['termination'],'agent_finished')
            self.assertEqual(timeouts,[120])
            raw=json.loads((root/'run/trace/002-observation.json').read_text())
            self.assertGreater(len(raw['execution']['stdout']),30000)
            tool=[m for m in requests[2] if m['role']=='tool' and m['tool_call_id']=='2'][0]
            self.assertLess(len(json.loads(tool['content'])['execution']['stdout']),4300)
            self.assertIn('Current public-contract progress',requests[2][-1]['content'])
            self.assertIn('Compiled public task brief',requests[0][1]['content'])

    def test_api_disconnect_becomes_preserved_null_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=fixture_bundle(root)
            class FailedClient:
                def __init__(self,*args,**kwargs):pass
                def complete(self,*args,**kwargs):raise APIError(None,'RemoteDisconnected',attempts=[{'request_usage':'unknown'}])
            with patch('urbanwind_harness.runner.DeepSeekClient',FailedClient),patch('urbanwind_harness.runner.Sandbox'),patch('urbanwind_harness.runner.grade',return_value={'status':'scored','score':0,'success':False,'result':{'fixture_only':True}}):
                result=run_task(bundle,'task-041',root/'run',root/'unused.key',root/'runtime',condition='full',active_seconds=2000)
            self.assertEqual(result['status'],'infra_incomplete')
            self.assertIsNone(result['score'])
            self.assertTrue((root/'run/result.json').exists())
            self.assertEqual(result['steps'],0)
            self.assertTrue((root/'run/trace/turn-001-api-error.json').exists())


if __name__=='__main__':unittest.main()
