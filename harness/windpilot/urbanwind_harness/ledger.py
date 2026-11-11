from __future__ import annotations

from pathlib import Path
import re

from .common import confined, digest
from .state import EvidenceState, fingerprint, recovery_plan


def file_matches(root,name,expected):
    try:
        path=confined(root,name)
        return path.is_file() and digest(path)==expected
    except (ValueError,OSError):
        return False


class Ledger:
    """Execution-bound reuse evidence. This does not certify CFD correctness."""
    def __init__(self):
        self.state = EvidenceState()
        self.executions = {}

    def refresh(self, root):
        events=self.state.refresh(root)
        for (case,stage),execution in self.executions.items():
            for name,expected in {**execution.get('extra_inputs',{}),**execution['outputs']}.items():
                if not file_matches(root,name,expected):
                    from .state import descendants
                    stale=descendants({stage})
                    self.state.cases[case]['stale'].update(stale)
                    events.append({'case':case,'changed_extra_input':name,'invalidated':sorted(stale)})
        return events

    def command_inputs(self, root, case_name, command, explicit=None):
        found={}
        if explicit is not None and (not isinstance(explicit,list) or not all(isinstance(name,str) for name in explicit)):
            raise ValueError('inputs_must_be_a_list_of_relative_paths')
        for name in set(explicit or []) | set(re.findall(r'[A-Za-z0-9_./-]+\.(?:py|sh)\b',command)):
            for candidate in (name,case_name+'/'+name):
                try:
                    path=confined(root,candidate)
                    if path.is_file() and not path.is_symlink():
                        found[path.relative_to(root.resolve()).as_posix()]=digest(path)
                        break
                except (ValueError,OSError):
                    continue
            else:
                if name in (explicit or []):
                    raise ValueError('declared_input_missing_or_unsafe:'+name)
        return found

    def record(self, root: Path, case_name, stage, outputs, result, step, before=None, extra_inputs=None):
        case = confined(root, case_name)
        case_name=case.relative_to(root.resolve()).as_posix()
        if case_name not in self.state.cases:
            raise ValueError("case_has_no_controlDict")
        if result["exit_code"] != 0 or result["timed_out"]:
            return {"recorded": False, "reason": "unsuccessful_execution"}
        after=fingerprint(case)
        if before is not None:
            required={'mesh':('geometry','configuration'), 'solve':('geometry','mesh','configuration'),
                'postprocess':('geometry','mesh','configuration'), 'deliver':()}[stage]
            def inputs_for(key, value):
                # decomposePar creates processor initial fields as mesh-stage
                # outputs. Canonical root physics must stay unchanged. Solve
                # stages still bind and compare all decomposed configuration.
                if stage == 'mesh' and key == 'configuration':
                    existing = {name for name, _ in before[key]}
                    return [item for item in value if not re.match(r'^processor\d+/', item[0]) or item[0] in existing]
                return value
            if any(inputs_for(key,before[key])!=inputs_for(key,after[key]) for key in required):
                return {'recorded':False,'reason':'inputs_changed_during_stage; prepare_inputs_in_a_separate_action'}
        for name,expected in (extra_inputs or {}).items():
            if not file_matches(root,name,expected):
                return {'recorded':False,'reason':'command_input_changed_during_stage:'+name}
        files = {}
        for name in outputs:
            path = confined(root, name)
            if not path.is_file() or path.is_symlink():
                return {"recorded": False, "reason": "missing_output:"+name}
            files[name] = digest(path)
        if not files:
            return {"recorded": False, "reason": "explicit_output_files_required"}
        self.executions[(case_name, stage)] = {
            "step": step, "input_fingerprints": after, "outputs": files,
            "extra_inputs":extra_inputs or {},
            "exit_code": result["exit_code"], "cpu_seconds": result["cpu_seconds"]}
        refreshed = {"mesh": ["geometry", "mesh"], "solve": ["configuration", "solve"],
                     "postprocess": ["postprocess"], "deliver": ["deliver"]}[stage]
        self.state.verified(case_name, refreshed)
        return {"recorded": True, "scope": "Recorded command succeeded; domain validity remains unverified."}

    def reusable(self, root: Path, case_name, stage):
        case_name=confined(root,case_name).relative_to(root.resolve()).as_posix()
        evidence = self.executions.get((case_name, stage))
        if evidence is None:
            return False
        case = confined(root, case_name)
        current = fingerprint(case)
        if current.get('unverified_dependencies'):
            return False
        needed = {"mesh": ("geometry", "mesh"), "solve": ("geometry", "mesh", "configuration"),
                  "postprocess": ("geometry", "mesh", "configuration", "postprocess")}[stage]
        if any(current[key] != evidence["input_fingerprints"][key] for key in needed):
            return False
        for name,expected in evidence.get('extra_inputs',{}).items():
            if not file_matches(root,name,expected):
                return False
        for name, expected in evidence["outputs"].items():
            if not file_matches(root,name,expected):
                return False
        return True

    def recovery(self, root, case_name, full_rerun=False):
        case_name=confined(root,case_name).relative_to(root.resolve()).as_posix()
        if case_name not in self.state.cases:
            raise ValueError("unknown_case")
        stale = set(self.state.cases[case_name]["stale"])
        reusable_solution = self.reusable(root, case_name, "solve")
        if not reusable_solution:
            stale.add("solve")
        if full_rerun:
            stale.add("geometry")
        result = recovery_plan(stale)
        result.update({"case": case_name, "reusable_recorded_solution": reusable_solution,
                       "parallel_preparation": "After physical/initial conditions change, regenerate decomposed processor fields according to the public task protocol before solving.",
                       "checkpoint_resume": "disabled_until_task-specific_restart_validator_is_available",
                       "scope": "Execution/input/output binding, not numerical acceptance."})
        return result

    def snapshot(self,compact=False):
        return {"cases": self.state.snapshot(), "recorded_executions": [
            {"case": case, "stage": stage, "step": data["step"], "outputs": sorted(data["outputs"]) if compact else data["outputs"]}
            for (case, stage), data in self.executions.items()]}
