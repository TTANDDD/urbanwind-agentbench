import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.common import digest,write
from urbanwind_harness.runner import run_task


class RunnerIntegrationTests(unittest.TestCase):
    def test_multi_call_response_executes_in_order_and_grades_only_after_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            bundle=root/'bundle'
            public=bundle/'public/task-041'
            grader=bundle/'trusted/task-041'
            public.mkdir(parents=True)
            grader.mkdir(parents=True)
            files={'task_spec.json':{'required_artifacts':[]},'reporting_contract.json':{},
                'case_plan.json':{},'local_assets/case_control_contract.json':{}}
            with tarfile.open(public/'init_file.tar.gz','w:gz') as handle:
                for name,value in files.items():
                    data=json.dumps(value).encode()
                    info=tarfile.TarInfo(name)
                    info.size=len(data)
                    handle.addfile(info,io.BytesIO(data))
                data=b'case_id\n'
                info=tarfile.TarInfo('local_assets/case_matrix.csv')
                info.size=len(data)
                handle.addfile(info,io.BytesIO(data))
                data=b'# public integration fixture\n'
                info=tarfile.TarInfo('contract_evaluator.py')
                info.size=len(data)
                handle.addfile(info,io.BytesIO(data))
            write(public/'task.json',{'instruction':'Integration fixture, not a model evaluation.'})
            (grader/'eval.py').write_text('fixture')
            (grader/'contract_evaluator.py').write_text('fixture')
            write(bundle/'manifest.json',{'tasks':[{'directory':'task-041',
                'public_archive_sha256':digest(public/'init_file.tar.gz'),
                'grader_sha256':digest(grader/'eval.py'),'precheck_sha256':digest(grader/'contract_evaluator.py'),
                'reference_sha256':None}]})
            calls=[]
            requests=[]
            def call(identity,name,args):
                return {'id':identity,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}
            class FakeClient:
                def __init__(self,*args,**kwargs): pass
                def complete(self,messages,**kwargs):
                    requests.append(messages)
                    native=[call('first','shell',{'command':'first'}),call('second','shell',{'command':'second'})] if len(requests)==1 else [call('done','finish',{'summary':'saved'})]
                    return {'content':'','message':{'role':'assistant','content':'','tool_calls':native},'usage':{}}
            class FakeSandbox:
                def __init__(self,*args,**kwargs): pass
                def execute(self,command,log_dir,**kwargs):
                    calls.append(command)
                    log_dir.mkdir(parents=True)
                    (log_dir/'stdout.txt').write_text(command)
                    (log_dir/'stderr.txt').write_text('')
                    return {'exit_code':0,'timed_out':False,'wall_seconds':0,'cpu_seconds':0,'stdout':command,'stderr':''}
            def fake_grade(*args,**kwargs):
                self.assertEqual(calls,['first','second'])
                self.assertEqual(len(requests),2)
                return {'status':'scored','score':100,'success':True,'result':{'fixture_only':True}}
            with patch('urbanwind_harness.runner.DeepSeekClient',FakeClient),patch('urbanwind_harness.runner.Sandbox',FakeSandbox),patch('urbanwind_harness.runner.grade',fake_grade):
                result=run_task(bundle,'task-041',root/'run',root/'unused.key',root/'unused-runtime',condition='baseline',max_steps=3,active_seconds=2000)
            self.assertEqual(result['steps'],3)
            config=json.loads((root/'run/config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['api_provider'],'DeepSeek')
            self.assertNotIn('guidance_policy',config)
            self.assertFalse((root/'run/public-brief.json').exists())
            self.assertFalse((root/'run/progress.json').exists())
            self.assertEqual(result['api_turns'],2)
            self.assertEqual(result['termination'],'agent_finished')
            tool_messages=[message for message in requests[1] if message['role']=='tool']
            self.assertEqual([m['tool_call_id'] for m in tool_messages],['first','second'])
            self.assertEqual(json.loads(tool_messages[0]['content'])['execution']['stdout'],'first')
            self.assertEqual(json.loads(tool_messages[1]['content'])['execution']['stdout'],'second')


if __name__=='__main__':
    unittest.main()
