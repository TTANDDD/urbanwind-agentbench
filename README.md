# UrbanWind-AgentBench and WindPilot

Research materials for **Does a completed simulation run mean a usable urban wind result for AI agents?**

[Project website](https://ttanddd.github.io/urbanwind-agentbench/) · [Task catalog](benchmark/catalog/) · [WindPilot](harness/windpilot/) · [Paper data](paper/data/) · [Reproduction guide](docs/REPRODUCIBILITY.md)

UrbanWind-AgentBench contains **200 tasks and 367 cases across 10 engineering families**. WindPilot is an execution framework around an LLM that combines public contracts, execution provenance, deterministic verification and bounded acceptance review. The paper compares two execution configurations on **30 development tasks**, using one recorded run per task and condition.

This repository is a public research snapshot dated **2026-11-11**. The task catalog and the evaluated subset have different coverage. The original task status fields are preserved; publishing this snapshot does not upgrade a task to `release_valid`.

| Contents | Location |
|---|---|
| 200 task descriptions, public contracts and generated inputs | [`benchmark/task-001` … `task-200`](benchmark/) |
| 200 task scorers and common structural evaluator | [`benchmark/`](benchmark/) and [`evaluator_framework`](benchmark/evaluator_framework/) |
| Catalog, case list, schemas and task status | [`benchmark/catalog/`](benchmark/catalog/), [`task_status.json`](benchmark/task_status.json) |
| OpenFOAM reference arrays/metrics and provenance metadata | `benchmark/task-*/ground_truth/`; coverage below |
| WindPilot v0.2.14 source and tests | [`harness/windpilot/`](harness/windpilot/) |
| Base-agent v0.2.2 source and tests | [`harness/base_agent/`](harness/base_agent/) |
| Per-task results, verification records and figure CSVs | [`paper/data/`](paper/data/) |
| Figure preparation and plotting scripts | [`paper/scripts/`](paper/scripts/) |
| Byte-level provenance of copied research files | [`reproducibility/source_files.sha256.json`](reproducibility/source_files.sha256.json) |

## Reference coverage

| Current local reference status | Tasks | Published contents |
|---|---:|---|
| OpenFOAM numerical references | 30 | Arrays, metrics, provenance and status JSON |
| OpenFOAM tool references | 6 | Available compact metrics, provenance and status JSON |
| Deterministic local scaffold references | 85 | Public inputs/scorers and status; unvalidated hidden targets omitted |
| Reference not generated | 79 | Public inputs/scorers and status |

The **paper subset is 29 numerical-reference tasks plus task-041 (meshing)**, not all 36 reference-ready tasks. It contains 13 ABL tasks, 16 RANS/LES tasks and 1 meshing task. The full list is in [`task_status.json`](benchmark/task_status.json). None of the 200 source tasks is currently marked `release_valid`. Numerical solver references are not experimental, wind-tunnel, PIV or independently held-out field measurements.

Large historical runtime/raw archives, operating-system images, solver executables, private credentials and third-party PDFs are not included. [`omitted_reference_artifacts.json`](benchmark/omitted_reference_artifacts.json) lists omitted reference artifacts. Consequently, this snapshot supports inspection, reproduction of the published summary statistics and preparation of new local evaluations; it is **not a complete archive of every historical solver field or a one-command reconstruction of the original server**.

## Quick start without model or solver calls

Python 3.10+ is sufficient for these checks:

```bash
git clone https://github.com/TTANDDD/urbanwind-agentbench.git
cd urbanwind-agentbench
python scripts/verify_release.py
python scripts/reproduce_summary.py
python scripts/prepare_bundle.py --output build/paper-bundle
```

The bundle separates `public/` agent inputs from `trusted/` scorers/references. It is regenerated from this public snapshot and is not represented as the exact frozen historical run bundle. For a new evaluation, keep the repository and `trusted/` directory outside the agent environment. Published references are accessible to researchers and must not be treated as a sealed unseen test set.

See [the reproduction guide](docs/REPRODUCIBILITY.md) for Linux/OpenFOAM requirements, scoring commands, source-version boundaries and figure regeneration. Saved-run statistics are not new model experiments.

## Citation and arXiv

The project page has a reserved **arXiv** button. No arXiv identifier has been assigned in this repository yet. Once available, run `python scripts/set_arxiv.py YOUR_ARXIV_ID` and publish the resulting changes.

```bibtex
@misc{qu2026urbanwind,
  title = {Does a completed simulation run mean a usable urban wind result for AI agents?},
  author = {Qu, Zhongke and Zhang, Yunwei and Su, Junwei and Zhang, Yabin and Li, Chengwei and Shui, Qingxiang and Qu, Hailong and Gu, Zhaolin},
  year = {2026},
  url = {https://ttanddd.github.io/urbanwind-agentbench/}
}
```

## Rights and provenance

Original software is released under the [MIT License](LICENSE), and original task/data/figure materials under [CC BY 4.0](LICENSES/DATA-CC-BY-4.0.txt). See [RIGHTS.md](RIGHTS.md) for precise scope and attribution. Optional external sources remain links; this repository does not relicense third-party materials. The generated inputs are not experimental observations.
