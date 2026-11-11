import http.client
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.api import APIError, PoeClient
from urbanwind_harness.checks import actual_comparison, expected_configuration_checks
from urbanwind_harness.guidance import ProgressGuide, compact_feedback, guided_timeout, public_brief
from urbanwind_harness.ledger import Ledger
from urbanwind_harness.runner import history_messages


class GuidanceTests(unittest.TestCase):
    def test_exploratory_shell_cannot_spend_whole_remaining_task(self):
        timeout, policy=guided_timeout({'action':'shell','command':'python3 search.py'},18000)
        self.assertEqual(timeout,120)
        self.assertEqual(policy['stage'],'shell')

    def test_recorded_solver_gets_long_budget_and_delivery_reserve(self):
        timeout, policy=guided_timeout({'action':'run','stage':'solve'},18000)
        self.assertEqual(timeout,17700)
        self.assertEqual(policy['delivery_reserve_seconds'],300)

    def test_user_requested_short_timeout_and_near_exhaustion_respected(self):
        self.assertEqual(guided_timeout({'action':'run','stage':'solve','timeout_s':10},18000)[0],10)
        self.assertEqual(guided_timeout({'action':'run','stage':'solve'},5)[0],1)

    def test_feedback_shortening_preserves_raw_observation_and_error_tail(self):
        raw={'execution':{'stdout':'START'+('x'*30000)+'IMPORTANT END','stderr':'y'*9000+'ERROR END'}}
        compact=compact_feedback(raw)
        self.assertGreater(len(raw['execution']['stdout']),30000)
        self.assertTrue(compact['execution']['stdout'].startswith('START'))
        self.assertTrue(compact['execution']['stdout'].endswith('IMPORTANT END'))
        self.assertTrue(compact['execution']['stderr'].endswith('ERROR END'))
        self.assertLess(len(json.dumps(compact)),6600)

    def test_repeated_inspection_and_budget_pressure_produce_actionable_feedback(self):
        guide=ProgressGuide({'instruction':'read public contract'})
        observation={'public_checks':{'missing_artifacts':['metrics.json'],'matched_case_ids':[]}}
        for step in range(1,11):
            state=guide.observe({'action':'shell','command':'cat task_spec.json'},observation,Ledger(),step,30,900)
        self.assertGreaterEqual(state['actions_without_new_public_milestone'],8)
        self.assertEqual(state['same_command_count'],10)
        self.assertEqual(state['phase'],'deliver')
        self.assertTrue(any('Stop repeating' in s for s in state['next_priorities']))

    def test_progress_state_survives_history_window_eviction(self):
        messages=history_messages('original task',[[{'role':'user','content':'old'}]],1,{'phase':'deliver'})
        self.assertEqual(messages[1]['content'],'original task')
        self.assertIn('deliver',messages[-1]['content'])

    def test_public_brief_never_opens_trusted_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'local_assets').mkdir()
            (root/'task_spec.json').write_text(json.dumps({'task_id':'fixture','required_artifacts':[{'path':'metrics.json'}]}))
            (root/'reporting_contract.json').write_text('{}')
            (root/'local_assets/input_contract.json').write_text(json.dumps({'solver':'simpleFoam','hidden_answer':'DO NOT EXPOSE'}))
            value=public_brief(root)
            self.assertIn('simpleFoam',json.dumps(value))
            self.assertNotIn('DO NOT EXPOSE',json.dumps(value))


class ConfigurationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def case(self,name,mesh_type='patch',faces='(4 5 6 7)',inlet_type='fixedValue',speed='1.2'):
        case=self.root/name;(case/'system').mkdir(parents=True);(case/'0').mkdir()
        (case/'system/blockMeshDict').write_text('vertices ((0 0 0)); blocks (); boundary (top {type '+mesh_type+'; faces ('+faces+');} ground {type wall;faces ();});')
        (case/'0/U').write_text('internalField uniform (0 0 0); boundaryField {inlet {type '+inlet_type+'; value uniform ('+speed+' 0 0);}}')
        return case

    def test_top_target_patch_type_difference_is_allowed(self):
        a=self.case('a');b=self.case('b',mesh_type='symmetryPlane')
        self.assertFalse(actual_comparison({'a':a,'b':b},'top_boundary_condition')['errors'])

    def test_mesh_geometry_difference_is_still_rejected_for_top_target(self):
        a=self.case('a');b=self.case('b',mesh_type='symmetryPlane',faces='(4 6 5 7)')
        self.assertTrue(actual_comparison({'a':a,'b':b},'top_boundary_condition')['errors'])

    def test_unrelated_factor_cannot_change_top_patch_type(self):
        a=self.case('a');b=self.case('b',mesh_type='symmetryPlane')
        self.assertTrue(actual_comparison({'a':a,'b':b},'aerodynamic_roughness')['errors'])

    def test_coded_boundary_placeholder_is_not_reference_speed(self):
        a=self.case('a',inlet_type='codedFixedValue',speed='0.396599')
        result=expected_configuration_checks({'a':a},[{'case_id':'a','reference_speed_m_s':'1.2'}])
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['coverage'],[])

    def test_actual_fixed_velocity_mismatch_is_still_rejected(self):
        a=self.case('a',speed='2.4')
        self.assertTrue(expected_configuration_checks({'a':a},[{'case_id':'a','reference_speed_m_s':'1.2'}])['errors'])


class APITransportTests(unittest.TestCase):
    def test_disconnect_is_bounded_and_returns_structured_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'key';key.write_text('fixture-not-a-credential')
            client=PoeClient(key,retries=3)
            with patch('urllib.request.urlopen',side_effect=http.client.RemoteDisconnected()),patch('time.sleep'):
                with self.assertRaises(APIError) as caught:
                    client.complete([{'role':'user','content':'fixture'}])
            self.assertEqual(len(caught.exception.attempts),3)
            self.assertIsNone(caught.exception.status)
            self.assertEqual(caught.exception.attempts[-1]['request_usage'],'unknown')

    def test_invalid_json_response_uses_same_bounded_retry_path(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self,*args): return False
        with tempfile.TemporaryDirectory() as tmp:
            key=Path(tmp)/'key';key.write_text('fixture-not-a-credential')
            client=PoeClient(key,retries=2)
            with patch('urllib.request.urlopen',side_effect=lambda *a,**k:Response(b'{')),patch('time.sleep'):
                with self.assertRaises(APIError) as caught:
                    client.complete([])
            self.assertEqual(len(caught.exception.attempts),2)


if __name__=='__main__':
    unittest.main()
