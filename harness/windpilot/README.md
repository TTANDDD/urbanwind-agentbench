# WindPilot v0.2.14 source snapshot

This directory retains the `urbanwind_harness` source and tests from the metric/judge-budget revision used for the paper's full WindPilot condition. See the root [reproduction guide](../../docs/REPRODUCIBILITY.md) for setup and the distinction between saved-run analysis and new experiments.

Main modules: `open_runner.py` orchestrates execution; `open_contracts.py` compiles public formats; `public_verification.py`, `evidence_consistency.py`, `primary_metrics.py` and `delivery_closure.py` check delivered evidence; `agent_judge.py`, `review_evidence.py` and `judge_budget.py` implement bounded acceptance review; `open_sandbox.py` and `sandbox.py` isolate execution and final grading. The shared source manifest records exact copied file hashes.

Runtime executables and server-specific queue launchers are not included. Existing historical test files are retained, including integration tests that require their original host prerequisites; use the documented portable test selection for this snapshot.

