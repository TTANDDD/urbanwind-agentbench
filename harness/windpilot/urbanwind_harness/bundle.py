from __future__ import annotations

import shutil
from pathlib import Path

from .common import digest, load, write


def build_bundle(workspace: Path, snapshot: Path, destination: Path):
    manifest = load(snapshot)
    dataset = workspace / manifest["dataset_root_relative_to_workspace"]
    destination.mkdir(parents=True, exist_ok=False)
    tasks = []
    for entry in manifest["tasks"]:
        folder = dataset / entry["directory"]
        task_file = folder / (entry["directory"]+".json")
        if digest(task_file) != entry["source_task_spec_sha256"]:
            raise ValueError("task_changed:"+entry["directory"])
        for item in entry["public_input_files"]:
            if digest(dataset/item["path"]) != item["sha256"]:
                raise ValueError("input_changed:"+item["path"])
        if digest(dataset/entry["trusted_evaluator"]["path"]) != entry["trusted_evaluator"]["sha256"]:
            raise ValueError("evaluator_changed:"+entry["directory"])
        task = load(task_file)
        public = destination/"public"/entry["directory"]
        public.mkdir(parents=True)
        shutil.copy2(folder/"init_file/init_file.tar.gz", public/"init_file.tar.gz")
        write(public/"task.json", {"task_id": entry["task_id"], "instruction": task["instruction"]})
        grader = destination/"trusted"/entry["directory"]
        grader.mkdir(parents=True)
        shutil.copy2(folder/"eval.py", grader/"eval.py")
        shutil.copy2(folder/"init_file/contract_evaluator.py", grader/"contract_evaluator.py")
        references = []
        for config in task["evaluator"].get("postconfig", []):
            for item in config.get("parameters", {}).get("files", []):
                if "ground_truth/" in item["local_path"]:
                    references.append(dataset/item["local_path"])
        if len(references) > 1:
            raise ValueError("multiple_reference_files_need_explicit_mapping")
        if references:
            shutil.copy2(references[0], grader/"reference.json")
        tasks.append({"task_id": entry["task_id"], "directory": entry["directory"],
            "task_mode": entry["task_mode"], "required_solver_runs": entry["required_solver_runs"],
            "public_archive_sha256": digest(public/"init_file.tar.gz"),
            "instruction_sha256": digest(public/"task.json"),
            "grader_sha256": digest(grader/"eval.py"),
            "precheck_sha256": digest(grader/"contract_evaluator.py"),
            "reference_sha256": digest(grader/"reference.json") if references else None})
    write(destination/"manifest.json", {"schema_version": "0.1", "source_snapshot_sha256": digest(snapshot),
        "task_count": len(tasks), "tasks": tasks,
        "agent_access": "public archive and instruction only; trusted files mounted after termination"})
    return destination/"manifest.json"
