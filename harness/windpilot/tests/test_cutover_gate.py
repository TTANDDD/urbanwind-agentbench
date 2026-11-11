import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name!='nt':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import parallel_full as queue
    import start_when_ready
else:queue=None


@unittest.skipIf(queue is None,'Linux ownership and fcntl')
class DirectCutoverTests(unittest.TestCase):
    def test_user_cancelled_cleanly_closed_partial_control_is_eligible(self):
        self.assertTrue(queue.closure_policy(True,False,False,[],True)['ready'])

    def test_controller_alive_prevents_cutover(self):
        self.assertFalse(queue.closure_policy(True,True,False,[],True)['ready'])

    def test_worker_or_active_claim_prevents_cutover(self):
        self.assertFalse(queue.closure_policy(True,False,True,[],True)['ready'])
        self.assertFalse(queue.closure_policy(True,False,False,['a'],True)['ready'])

    def test_unresolved_cleanup_or_missing_authorization_prevents_cutover(self):
        self.assertFalse(queue.closure_policy(True,False,False,[],False)['ready'])
        self.assertFalse(queue.closure_policy(False,False,False,[],True)['ready'])

    def test_provider_block_not_bypassed(self):
        self.assertFalse(queue.closure_policy(True,False,False,[],True,True)['ready'])

    def fixture(self,p):
        new=p/'new';old=p/'old';new.mkdir();old.mkdir()
        tasks=['scored','cancelled','not-run']
        budgets={'model':'deepseek-flash'}
        (new/'original30-lock.json').write_text(json.dumps({'rows':[{'task':t,'old_config_budget':budgets} for t in tasks]}))
        for task,score,status in [('scored',80.,'evaluated'),('cancelled',None,'cancelled')]:
            d=old/'runs/matched30-v0212'/(task+'--open');d.mkdir(parents=True)
            (d/'config.json').write_text(json.dumps(budgets))
            (d/'result.json').write_text(json.dumps({'status':status,'score':score,'steps':10,'termination':'user_cancelled' if score is None else 'agent_finished'}))
            selected=old/'runs/matched30-v0212/selected-result-locks'/(task+'.json');selected.parent.mkdir(exist_ok=True)
            selected.write_text(json.dumps({'result_sha256':queue.sha(d/'result.json'),'config_sha256':queue.sha(d/'config.json')}))
        return new,old,tasks

    def test_capture_preserves_cancelled_and_not_run_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            new,old,tasks=self.fixture(Path(tmp))
            with patch.object(queue,'HERE',new),patch.object(queue,'PREDECESSOR',old):
                path=start_when_ready.capture_control({'tasks':tasks});data=queue.load(path)
            self.assertEqual([r['score'] for r in data['rows']],[80.,None,None])
            self.assertEqual(data['mean_scored_only'],80.)
            self.assertEqual(data['scored_count'],1)
            self.assertEqual(data['rows'][-1]['status'],'not_run_user_cutover')

    def test_locked_control_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            new,old,tasks=self.fixture(Path(tmp))
            with patch.object(queue,'HERE',new),patch.object(queue,'PREDECESSOR',old):
                path=start_when_ready.capture_control({'tasks':tasks})
                x=queue.load(path);x['rows'][0]['score']=99.;path.write_text(json.dumps(x))
                with self.assertRaisesRegex(RuntimeError,'control_lock_changed'):start_when_ready.capture_control({'tasks':tasks})

    def test_no_controller_spawn_until_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            with patch.object(queue,'HERE',p),patch.object(queue,'ROOT',p/'runs/new'),patch.object(queue,'verify',return_value={}),patch.object(queue,'predecessor_gate',return_value={'ready':False,'reason':'waiting_predecessor_exit'}),patch.object(start_when_ready.subprocess,'Popen') as spawn:
                result=start_when_ready.main();self.assertEqual(result['state'],'waiting_predecessor');spawn.assert_not_called()


if __name__=='__main__':unittest.main()
