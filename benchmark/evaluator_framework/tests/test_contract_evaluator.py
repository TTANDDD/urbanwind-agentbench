from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract_evaluator import evaluate  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ContractEvaluatorTests(unittest.TestCase):
    def make_submission(self, root: Path) -> None:
        task_id = "urbanwind-task-001"
        artifacts = [
            {"path": "design.json", "format": "json"},
            {"path": "result.json", "format": "json"},
            {"path": "run_manifest.json", "format": "json"},
            {"path": "metrics.json", "format": "json"},
            {"path": "evidence_manifest.json", "format": "json"},
            {"path": "decision.md", "format": "markdown"},
        ]
        write_json(root / "task_spec.json", {
            "task_id": task_id,
            "required_cases": 1,
            "required_solver_runs": 1,
            "required_artifacts": artifacts,
            "release_status": "scaffold_only",
        })
        write_json(root / "reporting_contract.json", {
            "run_manifest": "run_manifest.json",
            "metrics": "metrics.json",
            "decision": "decision.md",
            "decision_required_terms": ["证据", "限制", "结论"],
            "metric_ids": ["fixture_metric"],
            "metric_contracts": [{"id": "fixture_metric", "name_zh": "测试指标", "unit": "1", "formula": "fixture"}],
            "expected_case_ids": ["task-001-case-001"],
            "expected_solver_case_ids": ["task-001-case-001"],
            "required_solver_runs": 1,
        })
        raw = root / "raw.log"
        raw.write_text("real tool output fixture\n", encoding="utf-8")
        raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
        write_json(root / "design.json", {"task_id": task_id})
        write_json(root / "result.json", {"status": "fixture"})
        write_json(root / "run_manifest.json", {
            "task_id": task_id,
            "runs": [{
                "case_id": "task-001-case-001",
                "commands": ["fixture --run"],
                "software": [{"name": "OpenFOAM", "version": "fixture"}],
                "started_at": "2026-08-16T00:00:00+08:00",
                "ended_at": "2026-08-16T00:00:01+08:00",
                "exit_code": 0,
                "execution_role": "real_solver_run",
                "evidence": [{"path": "raw.log", "sha256": raw_hash}],
            }],
        })
        write_json(root / "metrics.json", {"units": {"fixture_metric": "1"}, "values": {"fixture_metric": 1.0}})
        write_json(root / "evidence_manifest.json", {"files": [{"path": "raw.log", "sha256": raw_hash}]})
        (root / "decision.md").write_text(("证据、限制和结论均已逐项登记。" * 30), encoding="utf-8")

    def test_complete_fixture_reaches_precheck_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            result = evaluate(root, "urbanwind-task-001")
            self.assertEqual(result["status"], "contract_precheck_only")
            self.assertEqual(result["score"], 60.0)
            self.assertEqual(result["errors"], [])

    def test_missing_raw_evidence_reduces_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            (root / "raw.log").unlink()
            result = evaluate(root, "urbanwind-task-001")
            self.assertLess(result["score"], 60.0)
            self.assertTrue(any(error.startswith("no_hashed_evidence") for error in result["errors"]))

    def test_task_binding_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            result = evaluate(root, "urbanwind-task-200")
            self.assertIn("task_id_mismatch:urbanwind-task-001", result["errors"])

    def test_artifact_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            spec = json.loads((root / "task_spec.json").read_text(encoding="utf-8"))
            spec["required_artifacts"][0]["path"] = "../outside.json"
            write_json(root / "task_spec.json", spec)
            result = evaluate(root, "urbanwind-task-001")
            self.assertTrue(any(error.startswith("path_escape") for error in result["errors"]))

    def test_missing_task_specific_metric_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            write_json(root / "metrics.json", {"units": {}, "values": {}})
            result = evaluate(root, "urbanwind-task-001")
            self.assertLess(result["score"], 60.0)
            self.assertIn("missing_finite_metric_value:fixture_metric", result["errors"])

    def test_wrong_case_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
            manifest["runs"][0]["case_id"] = "task-001-case-999"
            write_json(root / "run_manifest.json", manifest)
            result = evaluate(root, "urbanwind-task-001")
            self.assertLess(result["score"], 60.0)
            self.assertIn("run_case_ids_mismatch", result["errors"])

    def test_solver_evidence_contract_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
            manifest["runs"][0]["execution_role"] = "provided_data_or_tool_execution"
            write_json(root / "run_manifest.json", manifest)
            result = evaluate(root, "urbanwind-task-001")
            self.assertLess(result["score"], 60.0)
            self.assertTrue(any(error.startswith("insufficient_solver_evidence") for error in result["errors"]))

    def test_metric_unit_must_match_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_submission(root)
            write_json(root / "metrics.json", {"units": {"fixture_metric": "%"}, "values": {"fixture_metric": 1.0}})
            result = evaluate(root, "urbanwind-task-001")
            self.assertLess(result["score"], 60.0)
            self.assertTrue(any(error.startswith("metric_unit_mismatch:fixture_metric") for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
