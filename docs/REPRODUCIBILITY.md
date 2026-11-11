# Reproducing the released materials

## Saved results and figures

`python scripts/reproduce_summary.py` recomputes the main summaries from `paper/data/per_task_merged.json`. The baseline comparison uses its documented `score_corrected` field; the original `score` field is retained. `per_task_raw.json` and `base_082_099.json` retain the corresponding source exports. This is an analysis of saved development runs, not a new independent evaluation.

The expected rounded summaries are mean scores 62.5 and 72.5, numerical credit on 6 and 17 tasks, full scores on 0 and 3 tasks, and mean action counts 130.4 and 104.9. One run was recorded per task per condition. The study does not establish repeat-run variance or generalization to unseen cities.

Regenerate figure data with the original script:

```bash
python paper/scripts/prepare_plot_data.py paper/data/per_task_merged.json benchmark/catalog/tasks.jsonl build/figure-data paper/data/verification_full.json
```

`paper/data/` includes the existing figure CSVs, including the task-068 checks used by Fig. 13. `paper/scripts/plot_figures.py` contains the plotting functions; see its command-line entry point for the expected directories. Plotting needs NumPy and Matplotlib. The historical remote-data fetch script is not needed and is not shipped with host login details.

## Task packages and scoring

Run `python scripts/verify_release.py` to verify source-file hashes, all 200 task packages and the 30 paper-task reference mappings. Every task has a source `eval.py`; availability of that file does not imply a complete numerical reference or a successful evaluation.

`python scripts/prepare_bundle.py --output build/paper-bundle` prepares the 30 paper tasks. Use `--task task-061` to prepare one task. It retains original physics inputs and scorer/reference bytes and adds public serialization documentation derived from the source code. It refuses to overwrite an existing output directory. Generated bundle manifests record both original and regenerated public-archive hashes.

After an agent has produced artifacts, a local scoring entry point is:

```bash
python scripts/evaluate.py --task task-061 --workspace /absolute/path/to/submitted-artifacts
```

Task-041's scorer invokes `blockMesh` and `checkMesh`, so source the appropriate OpenFOAM environment first. Other task-specific dependencies are visible in their evaluators. A score from the structural evaluator alone is capped at 60 and does not establish physical validity.

**Scorer isolation matters:** only mount the selected bundle's `public/` material into an agent workspace. Keep `trusted/`, the repository checkout, API key files and other task references outside that environment. References are public research artifacts in this release; the released tasks are not an unseen held-out test set.

## WindPilot runtime

The source snapshots are `harness/windpilot` (v0.2.14 metric/judge-budget revision) and `harness/base_agent` (v0.2.2). The copied source bytes are retained. Some historical internal version strings still name their parent revision; use the source manifest and directory version as the publication provenance.

Actual execution requires a **Linux x86-64 host**, OpenFOAM **v2412**, the corresponding MPI runtime, Python, bubblewrap, slirp4netns, an independently prepared Ubuntu root filesystem, and appropriate user-namespace/seccomp permissions. These operating systems and solver distributions are not bundled. New hardware and current hosted-model behavior can differ from the historical experiments.

The included setup scripts prepare runtime prerequisites, not an experiment:

```bash
python harness/windpilot/scripts/prepare_open_rootfs.py --help
python harness/windpilot/scripts/prepare_runtime_tools.py --help
cc -O2 harness/windpilot/runtime-tools/cpu_affinity_bridge.c -o harness/windpilot/runtime-tools/cpu_affinity_bridge
```

The runtime-tools helper verifies a locally downloaded coreutils Debian package against the local APT index. Obtain that package from the Ubuntu repositories and pass its path with `--package`; no third-party executable is distributed here. `URBANWIND_OPEN_ROOTFS` must point to the prepared `rootfs` directory. The environment guide describes the original restricted-procfs runtime and its limitations.

On a qualified host, the WindPilot entry point is:

```bash
export PYTHONPATH="$PWD/harness/windpilot"
export URBANWIND_OPEN_ROOTFS=/absolute/path/to/prepared/rootfs
python -m urbanwind_harness.open_runner \
  --bundle "$PWD/build/paper-bundle" --task task-061 \
  --run-dir /absolute/path/to/a/new/run-directory \
  --runtime /absolute/path/to/OpenFOAM-v2412 \
  --key-path /secure/location/deepseek.key \
  --cpu-set YOUR_ASSIGNED_CPUS --architecture verified_judge \
  --model deepseek-flash --max-steps 150 --history 15 \
  --active-seconds YOUR_FROZEN_TASK_BUDGET --reviewer-calls 2 --purpose development
```

Replace all placeholders with locally qualified values. Use the task-specific frozen wall budget (5 hours plus twice historical solver time); the example does not invent those historical timing measurements. The base-agent entry point is `urbanwind_harness.runner --condition baseline` with `PYTHONPATH` pointing to `harness/base_agent`. It is a separate source version, not a toggle that makes a present-day run identical to the saved experiment.

This publication did not launch solver simulations or paid model requests. Local source/packaging checks and mocked unit tests do not certify a newly configured Linux environment. The complete historical rootfs, every raw solver field, and all machine-specific scheduling state are outside this snapshot.

## Local checks

```bash
python scripts/verify_release.py
python scripts/reproduce_summary.py
PYTHONPATH=harness/windpilot:harness/windpilot/tests python -m unittest \
  test_contracts test_open_contracts test_deepseek_api test_delivery_closure \
  test_evidence_consistency test_evidence_review_regressions \
  test_metric_judge_budget test_reporting test_runtime_guard test_verified_judge
```

The selected tests use fixtures/mocks and do not make paid model calls. Platform-dependent tests may be skipped. Broader historical integration and scheduler tests require their original Linux prerequisites.
