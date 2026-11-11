import io
import json
import math
import hashlib
import os
import tempfile
import threading
import time
import tarfile
import unittest
from unittest import mock
from pathlib import Path

from urbanwind_harness.common import digest, load, write
from urbanwind_harness.open_runner import _public_file, history_messages, run_task


def tool(identity, name, arguments):
    return {"id": identity, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments)}}


def response(calls=None, content="", tokens=3, reasoning="Complete original reasoning is retained."):
    message = {"role": "assistant", "content": content, "reasoning_content": reasoning}
    if calls is not None:
        message["tool_calls"] = calls
    return {"content": content, "message": message, "provider_response": {
        "choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]},
        "usage": {"total_tokens": tokens}, "attempts": [{"status": 200, "usage": {"total_tokens": tokens}}]}


class OpenRunnerTests(unittest.TestCase):
    def fixture(self, path):
        bundle = path / "bundle"
        public, private = bundle / "public" / "task-fixture", bundle / "trusted" / "task-fixture"
        public.mkdir(parents=True)
        private.mkdir(parents=True)
        items = {"task_spec.json": {"required_artifacts": [{"path": "answer.json", "format": "json"}]},
                 "reporting_contract.json": {}, "case_plan.json": {},
                 "submission_format.json": {"documents": {}},
                 "agent_plan.md": "A complete persistent public plan.\n"}
        with tarfile.open(public / "init_file.tar.gz", "w:gz") as archive:
            for name, value in items.items():
                data = (value if isinstance(value, str) else json.dumps(value)).encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        write(public / "task.json", {"instruction": "An offline integration fixture, not a model evaluation."})
        (private / "eval.py").write_text("private evaluator fixture")
        (private / "contract_evaluator.py").write_text("private checker fixture")
        (private / "reference.json").write_text('{"secret":"PRIVATE_REFERENCE_DO_NOT_EXPOSE"}')
        write(bundle / "manifest.json", {"tasks": [{"directory": "task-fixture",
            "public_archive_sha256": digest(public / "init_file.tar.gz"),
            "grader_sha256": digest(private / "eval.py"),
            "precheck_sha256": digest(private / "contract_evaluator.py"),
            "reference_sha256": digest(private / "reference.json")}]})
        return bundle

    def factories(self, responses, executions, requests):
        pending = iter(responses)
        class Client:
            def __init__(self, *args, **kwargs):
                pass
            def complete(self, messages, **kwargs):
                requests.append((messages, kwargs))
                return next(pending)
        class Sandbox:
            def __init__(self, workspace, *args, **kwargs):
                self.workspace = workspace
            def execute(self, command, log_dir, **kwargs):
                self_outer.assertNotIn("grader", kwargs)
                executions.append((command, kwargs))
                log_dir.mkdir(parents=True)
                (log_dir / "stdout.txt").write_text(command)
                (log_dir / "stderr.txt").write_text("")
                return {"exit_code": 0, "timed_out": False, "cancelled": False,
                        "wall_seconds": 1.0, "cpu_seconds": 2.5, "stdout": command, "stderr": ""}
        self_outer = self
        return Client, Sandbox

    @staticmethod
    def grade(*args):
        return {"status": "scored", "score": 80.0, "success": False,
                "full_score": False, "error_free": True, "cpu_seconds_estimate": 0.1,
                "result": {"score": 80.0, "errors": []}}

    def run_fixture(self, path, responses, **overrides):
        executions, requests = [], []
        client, sandbox = self.factories(responses, executions, requests)
        arguments = {"bundle": self.fixture(path), "task_dir": "task-fixture", "run_dir": path / "run",
            "key_path": path / "not-read.key", "runtime": path / "runtime", "grading_timeout": 1,
            "active_seconds": 1000, "sandbox_factory": sandbox, "client_factory": client,
            "grade_function": self.grade}
        arguments.update(overrides)
        result = run_task(**arguments)
        return result, executions, requests

    def test_stage_case_declarations_optional_actual_cwd_and_full_history(self):
        first = response([tool("a", "run", {"command": "prepare_and_solve", "cwd": "cases/example"}),
                          tool("b", "shell", {"command": "plain_shell", "stage": "any_annotation"})],
                         reasoning="Unabridged " + "reasoning " * 5000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, executions, requests = self.run_fixture(root, [first, response(content="Finished with saved evidence.")])
            self.assertEqual([command for command, _ in executions], ["prepare_and_solve", "plain_shell"])
            self.assertEqual(executions[0][1]["cwd"], "cases/example")
            self.assertEqual(result["termination"], "agent_finished")
            self.assertEqual(result["steps"], 3)
            self.assertEqual(result["executed_shell_commands"], 2)
            self.assertEqual(result["tool_cpu_seconds"], 5.0)
            actor = [message for message in requests[1][0] if message["role"] == "assistant"]
            self.assertEqual(actor, [first["message"]])
            history_tools = [message["tool_call_id"] for message in requests[1][0] if message["role"] == "tool"]
            self.assertEqual(history_tools, ["a", "b"])
            self.assertIn("A complete persistent public plan.", requests[1][0][-1]["content"])
            self.assertEqual(load(root / "run" / "trace" / "turn-001-response.json"), first)
            for _, arguments in requests:
                self.assertNotIn("max_tokens", arguments)
                for name in ("temperature", "top_p", "seed", "thinking", "reasoning_effort", "max_completion_tokens"):
                    self.assertNotIn(name, arguments)

    def test_advisory_check_failure_never_vetoes_finish(self):
        checked = []
        def checker(*args):
            checked.append(True)
            return {"errors": ["missing_direct_evidence_role", "stale_message_is_not_a_gate"], "advisory_only": True}, 1.5
        with tempfile.TemporaryDirectory() as tmp:
            result, executions, requests = self.run_fixture(Path(tmp), [response([
                tool("check", "inspect", {}), tool("done", "finish", {"summary": "Submit anyway."})])],
                structural_checker=checker)
            self.assertEqual(checked, [True])
            self.assertEqual(result["termination"], "agent_finished")
            self.assertEqual(result["score"], 80.0)
            self.assertEqual(result["tool_cpu_seconds"], 1.5)
            self.assertEqual(executions, [])

    def test_two_public_only_reviews_count_in_action_api_and_token_budgets(self):
        responses = [response([tool("r1", "review", {"paths": ["../trusted/reference.json", "eval.py", "reference.json"]}),
            tool("r2", "review", {}), tool("r3", "review", {}), tool("done", "finish", {})]),
            response(content="First public advice.", tokens=5), response(content="Second public advice.", tokens=7)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, executions, requests = self.run_fixture(root, responses, max_steps=4)
            self.assertEqual(result["steps"], 4)
            self.assertEqual(result["actor_api_turns"], 1)
            self.assertEqual(result["reviewer_api_turns"], 2)
            self.assertEqual(result["api_turns"], 3)
            self.assertEqual(result["usage"]["total_tokens"], 15)
            self.assertEqual(result["reviewer_usage"]["total_tokens"], 12)
            self.assertEqual(executions, [])
            self.assertEqual(len(requests), 3)
            for messages, arguments in requests[1:]:
                self.assertIsNone(arguments["tools"])
                self.assertNotIn("PRIVATE_REFERENCE_DO_NOT_EXPOSE", json.dumps(messages))
                self.assertNotIn("max_tokens", arguments)
            self.assertIn("private_grading_artifact", json.dumps(requests[1][0]))
            self.assertEqual(load(root / "run" / "reviewer" / "001-response.json")["message"]["reasoning_content"],
                             "Complete original reasoning is retained.")
            self.assertIn("review_not_run", load(root / "run" / "trace" / "003-observation.json"))

    def test_cancellation_during_tool_preserves_cost_streams_and_never_grades(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            requests = []
            client, _ = self.factories([response([tool("long", "run", {"command": "long_tool"})])], [], requests)
            class CancellingSandbox:
                def __init__(self, workspace, *args, **kwargs):
                    self.workspace = workspace
                def execute(self, command, log_dir, **kwargs):
                    log_dir.mkdir(parents=True)
                    (log_dir / "stdout.txt").write_text("Partial original output\n")
                    (log_dir / "stderr.txt").write_text("Cancelled tool\n")
                    write(self.workspace.parent / "cancel.json", {"reason": "user_request"})
                    self_outer.assertTrue(kwargs["cancel_callback"]())
                    return {"exit_code": -15, "timed_out": False, "cancelled": True,
                            "cpu_seconds": 17.25, "wall_seconds": 3.0, "stdout": "Partial original output", "stderr": "Cancelled tool"}
            self_outer = self
            def forbidden_grade(*args):
                self.fail("A cancelled trajectory must not be graded.")
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=client,
                sandbox_factory=CancellingSandbox, grade_function=forbidden_grade)
            self.assertEqual(result["status"], "cancelled")
            self.assertIsNone(result["score"])
            self.assertEqual(result["tool_cpu_seconds"], 17.25)
            self.assertEqual(result["executed_shell_commands"], 1)
            self.assertEqual(load(path / "run" / "commands" / "001" / "execution.json")["exit_code"], -15)
            self.assertEqual((path / "run" / "public-trace" / "001" / "stdout.txt").read_text(), "Partial original output\n")

    def test_cancel_in_flight_api_is_prompt_and_usage_is_unknown(self):
        released = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            class WaitingClient:
                def __init__(self, *args, **kwargs):
                    pass
                def complete(self, *args, **kwargs):
                    write(path / "run" / "cancel.json", {"reason": "user_request"})
                    released.wait(5)
                    return response(content="Late reply")
            _, sandbox = self.factories([], [], [])
            def forbidden_grade(*args):
                self.fail("No grader on user cancellation.")
            began = time.monotonic()
            try:
                result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                    active_seconds=1000, grading_timeout=1, sandbox_factory=sandbox,
                    client_factory=WaitingClient, grade_function=forbidden_grade)
                self.assertLess(time.monotonic() - began, 2)
                self.assertEqual(result["status"], "cancelled")
                self.assertEqual(result["unknown_request_usage"][0]["usage"], "unknown")
                self.assertNotIn("total_tokens", result["usage"])
            finally:
                released.set()

    def test_native_infinity_diagnostic_is_preserved_raw_and_normalized_in_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            requests = []
            client, _ = self.factories([response(content="Saved; finish.")], [], requests)
            class GradeOnlySandbox:
                def __init__(self, *args, **kwargs):
                    pass
                def execute(self, command, log_dir, **kwargs):
                    self_outer.assertIn("grader", kwargs)
                    self_outer.assertTrue("-I" in command)
                    log_dir.mkdir(parents=True)
                    raw = json.dumps({"score": 60, "errors": ["raw_evidence_missing"],
                                      "diagnostics": {"hidden_delta": math.inf}})
                    (log_dir / "stdout.txt").write_text(raw)
                    (log_dir / "stderr.txt").write_text("")
                    return {"exit_code": 0, "timed_out": False, "cancelled": False,
                            "cpu_seconds": 0.5, "wall_seconds": 1, "stdout": raw, "stderr": ""}
            self_outer = self
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=client, sandbox_factory=GradeOnlySandbox)
            self.assertEqual(result["score"], 60.0)
            self.assertIsNone(result["grading"]["result"]["diagnostics"]["hidden_delta"])
            self.assertEqual(result["grading"]["result"]["missing_nonfinite_diagnostic_paths"], ["diagnostics.hidden_delta"])
            self.assertIn("Infinity", (path / "run" / "grading" / "stdout.txt").read_text())
            self.assertNotIn("Infinity", (path / "run" / "result.json").read_text())

    def test_persistent_backgrounds_close_before_grade_and_cleanup_cost_is_included_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            requests, events = [], []
            client, _ = self.factories([response(content="Submit saved evidence.")], [], requests)
            class PersistentSandbox:
                def __init__(self, *args, **kwargs):
                    pass
                def close(self, cancelled=False):
                    events.append("closed")
                    return {"status": "closed", "cpu_seconds": 4.5, "terminated_own_backgrounds": 1}
            def final_grade(*args):
                self.assertEqual(events, ["closed"])
                events.append("graded")
                return self.grade(*args)
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=client,
                sandbox_factory=PersistentSandbox, grade_function=final_grade)
            self.assertEqual(events, ["closed", "graded"])
            self.assertEqual(result["background_cleanup_cpu_seconds"], 4.5)
            self.assertEqual(result["tool_cpu_seconds"], 4.5)
            self.assertAlmostEqual(result["total_local_cpu_seconds_estimate"],
                                   4.5 + 0.1 + result["controller_cpu_seconds"])

    def test_rolling_history_retains_fifteen_complete_exchanges_and_no_reasoning_compaction(self):
        exchanges = [[{"role": "assistant", "content": str(i), "reasoning_content": "reason " * (i + 1)},
                      {"role": "tool", "tool_call_id": str(i), "content": "original observation"}] for i in range(20)]
        messages = history_messages("Public instruction", exchanges, 15)
        self.assertEqual(messages[2:], [message for exchange in exchanges[5:] for message in exchange])

    def test_initialization_time_and_child_cost_are_included_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            client, _ = self.factories([response(content="Finish.")], [], [])
            class InitializingSandbox:
                def __init__(self, *args, **kwargs):
                    time.sleep(0.025)
                    self.initialization_cpu_seconds = 6.25
                    self.initialization_wall_seconds = 0.025
                def close(self, **kwargs):
                    return {"status": "closed", "cpu_seconds": 1.0}
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=client,
                sandbox_factory=InitializingSandbox, grade_function=self.grade)
            self.assertGreaterEqual(result["wall_seconds"], 0.025)
            self.assertEqual(result["tool_cpu_seconds"], 7.25)
            self.assertEqual(result["initialization_cpu_seconds_estimate"], 6.25)
            self.assertAlmostEqual(result["total_local_cpu_seconds_estimate"],
                7.25 + 0.1 + result["controller_cpu_seconds"])

    def test_client_setup_failure_closes_created_environment_and_preserves_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            events = []
            class InitializingSandbox:
                initialization_cpu_seconds = 6.25
                initialization_wall_seconds = 0.02
                def __init__(self, *args, **kwargs):
                    events.append("created")
                def close(self, **kwargs):
                    events.append("closed")
                    return {"status": "closed", "cpu_seconds": 1.0}
            class MissingKeyClient:
                def __init__(self, *args, **kwargs):
                    raise FileNotFoundError("offline fixture has no credential")
            def forbidden_grade(*args):
                self.fail("Setup failure is not a model score.")
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=MissingKeyClient,
                sandbox_factory=InitializingSandbox, grade_function=forbidden_grade)
            self.assertEqual(events, ["created", "closed"])
            self.assertEqual(result["status"], "infra_incomplete")
            self.assertEqual(result["actor_api_turns"], 0)
            self.assertIsNone(result["score"])
            self.assertEqual(result["tool_cpu_seconds"], 7.25)
            self.assertTrue((path / "run" / "controller-error.json").is_file())

    def test_constructor_failure_is_saved_without_inventing_zero_setup_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            class FailedSandbox:
                def __init__(self, *args, **kwargs):
                    raise RuntimeError("offline namespace initialization failed")
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, sandbox_factory=FailedSandbox)
            self.assertEqual(result["status"], "infra_incomplete")
            self.assertEqual(result["environment_initialization"]["status"], "setup_failed")
            self.assertIsNone(result["initialization_cpu_seconds_estimate"])
            self.assertEqual(result["background_cleanup"]["status"], "environment_not_created")
            self.assertIsNone(result["tool_cpu_seconds"])
            self.assertIsNone(result["total_local_cpu_seconds_estimate"])
            self.assertEqual(result["known_tool_cpu_seconds_subtotal"], 0)
            self.assertGreaterEqual(result["known_local_cpu_seconds_subtotal"], result["controller_cpu_seconds"])
            self.assertFalse(result["local_cpu_cost_complete"])
            self.assertEqual(result["unknown_local_cpu_cost_components"], ["failed_environment_initialization"])

    @unittest.skipUnless(os.name != "nt" and hasattr(os, "O_NOFOLLOW"), "Native Linux no-follow path race")
    def test_public_file_never_reads_parent_replaced_by_private_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            root, outside = path / "public", path / "private"
            (root / "documents").mkdir(parents=True)
            outside.mkdir()
            (root / "documents" / "guide.txt").write_text("public original", encoding="utf-8")
            (outside / "guide.txt").write_text("PRIVATE_CONTENT_MUST_NOT_LEAK", encoding="utf-8")
            original_open, original_symlink = os.open, Path.is_symlink
            swapped = []
            def swap_once():
                if not swapped:
                    (root / "documents").rename(root / "old-documents")
                    (root / "documents").symlink_to(outside, target_is_directory=True)
                    swapped.append(True)
            def racing_open(name, *args, **kwargs):
                if name == "documents" and kwargs.get("dir_fd") is not None:
                    swap_once()
                return original_open(name, *args, **kwargs)
            def racing_symlink_check(candidate):
                result = original_symlink(candidate)
                if candidate == root / "documents" and not result:
                    swap_once()
                return result
            # The second hook reproduces the previous check/open race; the
            # openat hook places the same mutation immediately before new I/O.
            with mock.patch("urbanwind_harness.open_runner.os.open", side_effect=racing_open), \
                 mock.patch.object(Path, "is_symlink", racing_symlink_check):
                value = _public_file(root, "documents/guide.txt")
            self.assertEqual(swapped, [True])
            self.assertNotIn("PRIVATE_CONTENT_MUST_NOT_LEAK", json.dumps(value))
            self.assertEqual(value["omitted"], "symlink_not_read_by_controller")
            # A normal nested file remains readable, and its hash covers the
            # exact same bytes used for the returned public text.
            plain = _public_file(root, "old-documents/guide.txt")
            self.assertEqual(plain["text"], "public original")
            self.assertEqual(plain["sha256"], hashlib.sha256(plain["text"].encode()).hexdigest())

    def test_cancel_during_initialization_prevents_client_and_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            events = []
            class CancellingSetup:
                initialization_cpu_seconds = 2.5
                initialization_wall_seconds = 0.02
                def __init__(self, *args, **kwargs):
                    write(path / "run" / "cancel.json", {"reason": "user"})
                def close(self, cancelled=False):
                    events.append(cancelled)
                    return {"status": "closed", "cpu_seconds": 0.5}
            def forbidden_client(*args, **kwargs):
                self.fail("No provider setup or call after setup cancellation.")
            def forbidden_grade(*args):
                self.fail("No grade after setup cancellation.")
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, sandbox_factory=CancellingSetup,
                client_factory=forbidden_client, grade_function=forbidden_grade)
            self.assertEqual(events, [True])
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["actor_api_turns"], 0)
            self.assertEqual(result["tool_cpu_seconds"], 3.0)

    def test_host_forced_environment_close_is_infrastructure_failure_not_model_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            bundle = self.fixture(path)
            client, _ = self.factories([response([tool("run", "shell", {"command": "offline"})])], [], [])
            class ClosedEnvironment:
                def __init__(self, *args, **kwargs):
                    pass
                def execute(self, command, log_dir, **kwargs):
                    log_dir.mkdir(parents=True)
                    return {"exit_code": -15, "timed_out": True, "cancelled": False,
                        "environment_forced_closed": True, "cpu_seconds": 4.0, "stdout": "partial", "stderr": ""}
                def close(self, **kwargs):
                    return {"status": "already_closed", "cpu_seconds": 0.0}
            def forbidden_grade(*args):
                self.fail("A lost task environment must not get a model score.")
            result = run_task(bundle, "task-fixture", path / "run", path / "unused.key", path / "runtime",
                active_seconds=1000, grading_timeout=1, client_factory=client,
                sandbox_factory=ClosedEnvironment, grade_function=forbidden_grade)
            self.assertEqual(result["status"], "infra_incomplete")
            self.assertEqual(result["termination"], "controller_incomplete")
            self.assertIsNone(result["score"])
            self.assertEqual(result["tool_cpu_seconds"], 4.0)
            self.assertEqual(result["executed_shell_commands"], 1)
            saved = load(path / "run" / "commands" / "001" / "execution.json")
            self.assertTrue(saved["environment_forced_closed"])


if __name__ == "__main__":
    unittest.main()
