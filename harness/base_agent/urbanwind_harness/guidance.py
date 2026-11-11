"""Public-contract progress feedback; never reads a hidden grader or reference."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

from .common import load


GUIDED_POLICY = {
    "shell_seconds": 120,
    "postprocess_seconds": 300,
    "deliver_seconds": 120,
    "mesh_seconds": 1800,
    "delivery_reserve_seconds": 300,
    "delivery_reserve_actions": 20,
    "stagnation_actions": 8,
    "stdout_chars": 4000,
    "stderr_chars": 2000,
}


def public_brief(source: Path) -> dict:
    """Compile already-public requirements, preserving pointers to full contracts."""
    spec = load(source / "task_spec.json")
    report = load(source / "reporting_contract.json")
    paths = sorted(p for p in (source / "local_assets").glob("*contract*.json")
                   if p.is_file() and not p.is_symlink() and p.stat().st_size <= 2**20)
    selected = ("solver", "turbulence_model", "geometry", "full_iterations",
                "mpi_ranks", "mpi_ranks_per_case", "primary_metric", "frozen_gates",
                "required_raw_evidence", "required_raw_evidence_per_case",
                "required_manifest", "required_result", "profile_source",
                "neutral_profile_csv", "response_probe_csv", "path_policy")
    contracts = []
    for path in paths:
        value = load(path)
        if not isinstance(value, dict):
            continue
        contracts.append({"path": path.relative_to(source).as_posix(),
                          **{key: value[key] for key in selected if key in value}})
    return {
        "task_id": spec.get("task_id"),
        "required_artifacts": spec.get("required_artifacts", []),
        "expected_case_ids": report.get("expected_case_ids", []),
        "expected_solver_case_ids": report.get("expected_solver_case_ids", []),
        "required_solver_runs": report.get("required_solver_runs", 0),
        "metric_ids": report.get("metric_ids", []),
        "decision_required_terms": report.get("decision_required_terms", []),
        "public_contracts": contracts,
        "full_sources": ["task_spec.json", "reporting_contract.json", "case_plan.json",
                         "local_assets/case_matrix.csv"],
        "scope": "Only public requirements. No completed answers or numerical certification.",
        "instruction": "Read the cited contracts before constructing cases. Save truthful design and boundary records early. Use run for computation; prepare scripts/inputs separately from executing a bound stage. Compute metrics from actual raw outputs, never fill missing results with invented values.",
    }


def guided_timeout(action: dict, remaining: float) -> tuple[float, dict]:
    """Exploration cannot consume the complete solver/delivery budget."""
    stage = action.get("stage") if action.get("action") == "run" else "shell"
    if stage == "solve":
        reserve = min(GUIDED_POLICY["delivery_reserve_seconds"], max(0, remaining - 1))
        cap = max(1, remaining - reserve)
    else:
        reserve = 0
        cap = GUIDED_POLICY.get(stage + "_seconds", GUIDED_POLICY["shell_seconds"])
    requested = float(action.get("timeout_s", remaining))
    timeout = min(requested, cap, remaining)
    return timeout, {"stage": stage, "effective_timeout_seconds": timeout,
                     "delivery_reserve_seconds": reserve,
                     "scope": "Per declared tool/stage; a script can be mislabeled. Not a proof of useful computation."}


def shortened(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return text[:head] + "\n[Feedback shortened; full stdout/stderr is saved in harness_trace.]\n" + text[-(limit-head):]


def compact_feedback(observation: dict) -> dict:
    """Reduce model context only; original observation/log files remain complete."""
    value = copy.deepcopy(observation)
    execution = value.get("execution", {})
    for name, limit in (("stdout", GUIDED_POLICY["stdout_chars"]),
                        ("stderr", GUIDED_POLICY["stderr_chars"])):
        if isinstance(execution.get(name), str):
            original = len(execution[name])
            execution[name] = shortened(execution[name], limit)
            execution[name + "_original_feedback_chars"] = original
    return value


class ProgressGuide:
    def __init__(self, brief: dict):
        self.brief = brief
        self.last_signature = None
        self.unchanged_actions = 0
        self.command_counts = Counter()
        self.last_checks = {"missing_artifacts": [item['path'] for item in brief.get('required_artifacts', [])],
                            "malformed_artifacts": [], "matched_case_ids": []}
        self.last_schema_errors = None
        self.schema_observed_at = None
        self.latest = {"phase": "prepare", "instruction": brief["instruction"]}

    def observe(self, action, observation, ledger, step, max_steps, seconds_remaining):
        checks = observation.get("public_checks") or self.last_checks
        self.last_checks = checks
        if 'schema_check' in checks:
            self.last_schema_errors = checks['schema_check'].get('errors', [])
            self.schema_observed_at = checks['schema_check'].get('observed_before_action')
        executions = ledger.snapshot(compact=True).get("recorded_executions", [])
        # Schema-valid files and actually recorded stages matter, not shell exit 0 alone.
        signature = json.dumps({
            "missing": checks.get("missing_artifacts", []),
            "malformed": checks.get("malformed_artifacts", []),
            "cases": checks.get("matched_case_ids", []),
            "executions": [(e.get("case"), e.get("stage"), e.get("step")) for e in executions],
            "schema_errors": self.last_schema_errors,
        }, sort_keys=True)
        if signature == self.last_signature:
            self.unchanged_actions += 1
        else:
            self.unchanged_actions = 0
        self.last_signature = signature
        command = " ".join(action.get("command", "").split())
        repetitions = 0
        if command:
            identity = hashlib.sha256(command.encode()).hexdigest()
            self.command_counts[identity] += 1
            repetitions = self.command_counts[identity]
        remaining_actions = max(0, max_steps - step)
        missing = checks.get("missing_artifacts", [])
        execution = observation.get("execution", {})
        phase = "deliver" if remaining_actions <= GUIDED_POLICY["delivery_reserve_actions"] else "execute"
        priorities = []
        if observation.get("action_error"):
            priorities.append("Fix the reported tool/action error before repeating the action.")
        if observation.get("execution_rejected"):
            priorities.append("Correct the specific observed public configuration contradiction; do not bypass it through shell.")
        if execution.get("timed_out"):
            priorities.append("The declared tool exceeded its limit. Replace combinatorial exploration with a bounded algorithm, or use the proper recorded stage for genuine simulation.")
        if execution.get("exit_code") not in (None, 0):
            priorities.append("Read the saved stderr and relevant log; fix this failed execution before claiming a completed stage.")
        if self.unchanged_actions >= GUIDED_POLICY["stagnation_actions"] or repetitions >= 3:
            priorities.append("No new public delivery/execution milestone. Stop repeating broad file reads or unbounded searches; choose one missing requirement and produce its real evidence.")
        if phase == "deliver":
            priorities.append("Reserve the remaining actions for postprocessing, exact public schemas and a bounded decision. Save incomplete/failed evidence honestly; do not invent numerical results.")
        elif missing:
            priorities.append("Save truthful design/boundary records early, then generate required raw evidence with run. Do not leave all deliverables for the final action.")
        self.latest = {
            "phase": phase, "actions_remaining": remaining_actions,
            "seconds_remaining": max(0, seconds_remaining),
            "actions_without_new_public_milestone": self.unchanged_actions,
            "same_command_count": repetitions,
            "missing_artifacts": missing,
            "malformed_artifacts": checks.get("malformed_artifacts", []),
            "public_configuration_errors": checks.get("actual_comparison", {}).get("errors", []),
            "public_schema_errors": self.last_schema_errors,
            "schema_observed_before_action": self.schema_observed_at,
            "recorded_stages": [{"case": e.get("case"), "stage": e.get("stage"), "step": e.get("step")} for e in executions],
            "next_priorities": priorities,
            "scope": "Progress guidance, not hidden grading or verification of physical correctness.",
        }
        return self.latest
