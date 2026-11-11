import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from urbanwind_harness.api import APIError, PoeClient, action_from_text
from urbanwind_harness.checks import actual_comparison, expected_configuration_checks, matrix_constraints, public_observation
from urbanwind_harness.common import confined, extract, load, write
from urbanwind_harness.ledger import Ledger
from urbanwind_harness.protocol import action_from_response, feedback_messages
from urbanwind_harness.state import descendants, recovery_plan
from urbanwind_harness.state import fingerprint


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def case(self, name, speed="1.2", roughness="0.02", dt="0.01"):
        case = self.root/name
        (case/"system").mkdir(parents=True)
        (case/"0").mkdir()
        (case/"constant").mkdir()
        (case/"system/controlDict").write_text("application pimpleFoam; deltaT "+dt+"; endTime 420;")
        (case/"system/blockMeshDict").write_text("vertices ((0 0 0));")
        (case/"0/U").write_text("Uref "+speed+"; z0 "+roughness+"; flowDir (1 0 0);")
        return case

    def test_roughness_allows_target_but_rejects_hidden_speed_change(self):
        a = self.case("low", roughness="0.002")
        b = self.case("high", roughness="0.2")
        self.assertFalse(actual_comparison({"a": a, "b": b}, "aerodynamic_roughness")["errors"])
        (b/"0/U").write_text("Uref 2.4; z0 0.2; flowDir (1 0 0);")
        self.assertIn("actual_non_target_diff:0/U:Uref", actual_comparison({"a": a, "b": b}, "aerodynamic_roughness")["errors"])

    def test_missing_observed_field_is_not_equality(self):
        a, b = self.case("a"), self.case("b")
        (b/"0/U").write_text("z0 0.02; flowDir (1 0 0);")
        self.assertIn("inconsistent_field_presence:0/U:Uref", actual_comparison({"a": a, "b": b}, "time_step")["errors"])

    def test_numerical_formatting_is_not_a_physics_difference(self):
        a,b=self.case('a'),self.case('b')
        (b/'0/U').write_text('Uref 1.200000; z0 2e-2; flowDir (1.0 0.00 0);')
        self.assertFalse(actual_comparison({'a':a,'b':b},'time_step')['errors'])

    def test_uniform_inflow_speed_and_non_target_scheme_changes_detected(self):
        a,b=self.case('a'),self.case('b')
        (a/'0/U').write_text('internalField uniform (1.2 0 0);')
        (b/'0/U').write_text('internalField uniform (2.4 0 0);')
        self.assertIn('actual_non_target_diff:0/U:uniform_speed_m_s',actual_comparison({'a':a,'b':b},'aerodynamic_roughness')['errors'])
        (a/'system/fvSchemes').write_text('gradSchemes {default Gauss linear;} divSchemes {default none;}')
        (b/'system/fvSchemes').write_text('gradSchemes {default Gauss cubic;} divSchemes {default Gauss upwind;}')
        self.assertIn('actual_non_target_diff:system/fvSchemes:gradSchemes',actual_comparison({'a':a,'b':b},'convection_scheme')['errors'])

    def test_time_step_target_is_allowed(self):
        a, b = self.case("a"), self.case("b", dt="0.02")
        self.assertFalse(actual_comparison({"a": a, "b": b}, "time_step")["errors"])

    def test_matrix_cannot_change_non_target_seed(self):
        rows = [{"case_id": "a", "roughness_length_m": ".002", "seed": "7"},
                {"case_id": "b", "roughness_length_m": ".2", "seed": "8"}]
        self.assertIn("non_target_diff:seed", matrix_constraints(rows, "aerodynamic_roughness")["errors"])

    def test_unknown_factor_is_explicit(self):
        self.assertEqual(matrix_constraints([{"case_id": "a"}, {"case_id": "b"}], "new_factor")["status"], "unknown_factor")

    def test_equal_cases_can_still_violate_the_frozen_public_speed(self):
        a,b=self.case('a',speed='2.4'),self.case('b',speed='2.4')
        self.assertFalse(actual_comparison({'a':a,'b':b},'aerodynamic_roughness')['errors'])
        checked=expected_configuration_checks({'a':a,'b':b},[{'case_id':'a','reference_speed_m_s':'1.2'},{'case_id':'b','reference_speed_m_s':'1.2'}])
        self.assertEqual(len(checked['errors']),2)

    def test_configuration_invalidation_propagates(self):
        self.assertEqual(descendants({"configuration"}), {"configuration", "solve", "postprocess", "deliver"})
        self.assertEqual(descendants({"postprocess"}), {"postprocess", "deliver"})

    def test_recovery_requires_all_checkpoint_conditions(self):
        self.assertNotEqual(recovery_plan(["solve"], True, True, False, True)["action"], "resume_checkpoint")
        self.assertEqual(recovery_plan(["solve"], True, True, True, True)["action"], "resume_checkpoint")

    def test_postprocess_reuse_is_bound_to_actual_execution_and_files(self):
        case = self.case("a")
        output = case/"final-U"
        output.write_text("real execution fixture")
        ledger = Ledger()
        ledger.refresh(self.root)
        ledger.record(self.root, "a", "solve", ["a/final-U"], {"exit_code": 0, "timed_out": False, "cpu_seconds": 1}, 1)
        (case/"system/sampleDict").write_text("fields (U);")
        ledger.refresh(self.root)
        self.assertEqual(ledger.recovery(self.root, "a")["action"], "redo_postprocessing")
        (case/"0/U").write_text("Uref 2.4; z0 0.02; flowDir (1 0 0);")
        ledger.refresh(self.root)
        self.assertEqual(ledger.recovery(self.root, "a")["action"], "rerun_case")

    def test_changed_raw_output_cannot_be_reused(self):
        case = self.case("a")
        output = case/"final-U"
        output.write_text("original")
        ledger = Ledger()
        ledger.refresh(self.root)
        ledger.record(self.root, "a", "solve", ["a/final-U"], {"exit_code": 0, "timed_out": False, "cpu_seconds": 1}, 1)
        output.write_text("replaced")
        self.assertFalse(ledger.reusable(self.root, "a", "solve"))

    def test_processor_initial_field_is_a_real_input_dependency(self):
        case=self.case('a')
        (case/'processor0/0').mkdir(parents=True)
        field=case/'processor0/0/U'
        field.write_text('value uniform (1 0 0);')
        (case/'final-U').write_text('recorded')
        ledger=Ledger()
        ledger.refresh(self.root)
        ledger.record(self.root,'a','solve',['a/final-U'],{'exit_code':0,'timed_out':False,'cpu_seconds':1},1)
        field.write_text('value uniform (2 0 0);')
        ledger.refresh(self.root)
        self.assertFalse(ledger.reusable(self.root,'a','solve'))

    def test_untracked_external_include_never_establishes_reuse(self):
        case=self.case('a')
        (case/'system/fvSolution').write_text('#include "../../shared/scheme"')
        (case/'final-U').write_text('recorded')
        ledger=Ledger()
        ledger.refresh(self.root)
        ledger.record(self.root,'a','solve',['a/final-U'],{'exit_code':0,'timed_out':False,'cpu_seconds':1},1)
        self.assertFalse(ledger.reusable(self.root,'a','solve'))

    def test_failed_execution_never_establishes_reuse(self):
        self.case("a")
        ledger = Ledger()
        ledger.refresh(self.root)
        self.assertFalse(ledger.record(self.root, "a", "solve", [], {"exit_code": 1, "timed_out": False}, 1)["recorded"])

    def test_input_mutation_inside_solve_cannot_bind_old_output_to_new_physics(self):
        case=self.case('a')
        before=fingerprint(case)
        (case/'final-U').write_text('old physics result')
        (case/'0/U').write_text('Uref 2.4;')
        ledger=Ledger()
        ledger.refresh(self.root)
        result=ledger.record(self.root,'a','solve',['a/final-U'],{'exit_code':0,'timed_out':False,'cpu_seconds':1},1,before)
        self.assertFalse(result['recorded'])

    def test_postprocessor_script_change_invalidates_derived_evidence(self):
        case=self.case('a')
        script=self.root/'post.py'
        script.write_text('print(1)')
        (case/'result.json').write_text('{}')
        ledger=Ledger()
        ledger.refresh(self.root)
        inputs=ledger.command_inputs(self.root,'a','python3 post.py')
        ledger.record(self.root,'a','postprocess',['a/result.json'],{'exit_code':0,'timed_out':False,'cpu_seconds':1},1,extra_inputs=inputs)
        script.write_text('print(2)')
        ledger.refresh(self.root)
        self.assertIn('postprocess',ledger.state.cases['a']['stale'])

    def test_archive_escape_and_link_rejected(self):
        for number, (name, link) in enumerate((("../escape", False), ("link", True))):
            archive = self.root/f"{number}.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "/etc/passwd"
                    handle.addfile(info)
                else:
                    info.size = 1
                    handle.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                extract(archive, self.root/f"out{number}")

    def test_case_mappings_never_use_sorted_pairing(self):
        self.case("arbitrary")
        write(self.root/"task_spec.json", {"required_artifacts": []})
        write(self.root/"reporting_contract.json", {"expected_case_ids": ["a", "b"]})
        write(self.root/"local_assets/case_control_contract.json", {"controlled_factor": "time_step"})
        (self.root/"local_assets/case_matrix.csv").write_text("case_id,time_step_s\na,.01\nb,.02\n")
        observed = public_observation(self.root)
        self.assertFalse(observed["finish_gate_pass"])
        self.assertEqual(observed["unmatched_case_ids"], ["a", "b"])

    def test_json_nonfinite_rejected(self):
        path = self.root/"bad.json"
        path.write_text('{"value":NaN}')
        with self.assertRaises(ValueError):
            load(path)

    def test_api_omits_max_tokens_and_retains_provider_response(self):
        key = self.root/"dummy.key"
        key.write_text("test-secret")
        captured = []
        def fake(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": '{"action":"finish"}'}, "finish_reason": "stop"}], "model": "deepseek-v4.1-flash"}).encode())
        with patch("urllib.request.urlopen", fake):
            response = PoeClient(key).complete([{"role": "user", "content": "hello"}])
        self.assertNotIn("max_tokens", captured[0])
        self.assertNotIn("test-secret", json.dumps(captured))
        self.assertIn("provider_response", response)

    def test_truncated_response_is_preserved_and_never_executed(self):
        key = self.root/"dummy.key"
        key.write_text("test-secret")
        data = {"choices": [{"message": {"content": '{"action":"shell"'}, "finish_reason": "length"}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
            with self.assertRaises(APIError) as caught:
                PoeClient(key).complete([])
        self.assertEqual(caught.exception.response, data)

    def test_action_protocol(self):
        self.assertEqual(action_from_text('```json\n{"action":"inspect"}\n```')["action"], "inspect")
        with self.assertRaises(ValueError):
            action_from_text('{"action":"shell"}')

    def test_native_tool_call_and_corresponding_feedback(self):
        response = {"content": "", "message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "test-call", "type": "function", "function": {"name": "shell", "arguments": '{"command":"true"}'}}]}}
        self.assertEqual(action_from_response(response), {"action":"shell", "command":"true"})
        feedback = feedback_messages(response, {"exit_code": 0})
        self.assertEqual(feedback[1]["tool_call_id"], "test-call")
        self.assertEqual(feedback[1]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
