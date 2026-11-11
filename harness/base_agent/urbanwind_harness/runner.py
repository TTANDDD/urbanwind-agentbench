from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from pathlib import Path

from .api import APIError, DeepSeekClient
from .checks import public_observation
from .common import confined, digest, extract, load, now, write
from .ledger import Ledger
from .protocol import TOOLS, action_from_call, action_from_response
from .sandbox import Sandbox
from .state import fingerprint
from .guidance import GUIDED_POLICY, ProgressGuide, compact_feedback, guided_timeout, public_brief


CONDITIONS = ("baseline", "full")
SYSTEM = """You are an engineering agent completing a public OpenFOAM benchmark task.
Use real tools and original raw numerical outputs; never invent logs or results.
Use indexed, linear-time mesh/field postprocessing where possible; avoid scanning
every mesh face separately for each cell.
All files are under /home/user/Desktop. OpenFOAM is initialized for every shell.
Networking is unavailable. Linux, Python, numpy, scipy and matplotlib are available.
Actual tool records are readable in the readonly harness_trace directory; use
these records for provenance and do not list them as newly produced stage outputs.
Use the provided functions. Calls are executed in their returned order and every
call counts toward the action budget. Prefer one call for a mutation; independent
reads may be batched. If function calling is unavailable, return one JSON object. The actions are:
{"action":"shell","command":"..."} for inspecting files, editing and general tools;
{"action":"run","stage":"mesh|solve|postprocess|deliver","case":"relative/case/path","command":"...","outputs":["relative/output/file"]} for a recorded stage;
{"action":"inspect"} for public artifact inspection;
{"action":"recover","case":"relative/case/path"} for an evidence-based recomputation recommendation;
{"action":"finish","summary":"..."} when all required artifacts are saved.
Use run for mesh/solver/postprocessing operations; run commands must be foreground,
because background processes end when that shell closes. Commands may run until the
remaining task time budget; specify timeout_s only if a shorter limit is appropriate.
The exact artifact paths in task_spec.json and other public contracts take priority.
Map actual cases with a case_id in resolved_config.json inside the case, or use an
exact expected case ID as directory name when no different artifact path is required.
Preserve public inputs. Record your provenance
and all task-specific outputs in the exact schemas required by the public contracts.
Execution records and public checks do not establish numerical or physical correctness.
The final hidden grader and reference answers are unavailable during the trajectory.
Save the required delivery artifacts before the action budget is exhausted.
"""


def history_messages(initial, exchanges, limit, guidance=None):
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": initial}] + [
        message for pair in exchanges[-limit:] for message in pair]
    if guidance is not None:
        messages.append({"role": "user", "content": "Current public-contract progress:\n" + json.dumps(guidance, ensure_ascii=False)})
    return messages


def condition_observation(root, canonical, condition):
    if condition == "baseline":
        return {"files": sorted(path.name for path in root.iterdir())}
    observation = public_observation(root, canonical)
    if condition in ("artifact-check", "no-comparison"):
        observation["finish_gate_pass"] = not observation["missing_artifacts"] and not observation["malformed_artifacts"]
        observation.pop("actual_comparison")
        observation.pop("public_plan_check")
    return observation


def grade(sandbox, grader, run_dir, timeout):
    # -I prevents the agent's PYTHONPATH/cwd from shadowing trusted imports. A
    # fixed trusted bootstrap explicitly adds only the grader's directory.
    command = "python3 -I -c \"import sys,runpy;sys.path.insert(0,'/opt/urbanwind-grader');runpy.run_path('/opt/urbanwind-grader/eval.py',run_name='__main__')\""
    execution = sandbox.execute(command, run_dir/"grading", timeout=timeout, grader=grader)
    write(run_dir/"grading/execution.json", execution)
    try:
        result = json.loads((run_dir/"grading/stdout.txt").read_text())
        if execution["exit_code"] != 0 or execution["timed_out"]:
            raise ValueError("grader_process_failed")
        score = float(result["score"])
        if not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("invalid_grader_score")
        return {"status": "scored", "score": score, "cpu_seconds_estimate": execution.get('cpu_seconds',0),
                "full_score": score == 100 and not result.get("errors"),
                "error_free": not result.get("errors"),
                "success": score == 100 and not result.get("errors"),
                "success_definition": "Legacy alias for full_score; not a universal physical-completion criterion.",
                "result": result}
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return {"status": "infra_incomplete", "score": None, "success": None,
            "cpu_seconds_estimate": execution.get('cpu_seconds',0), "error": str(exc)}


def public_schema_check(sandbox,canonical,run_dir,step,remaining):
    command="python3 -I -c \"import sys,json;from pathlib import Path;sys.path.insert(0,'/opt/urbanwind-public-check');from contract_evaluator import evaluate;r=evaluate(Path('/home/user/Desktop'));print(json.dumps({'errors':r.get('errors',[]),'warnings':r.get('warnings',[]),'scope':'Public structural/provenance checks only; no hidden reference or numerical score.'}))\""
    execution=sandbox.execute(command,run_dir/'public-checks'/f'{step:03d}',timeout=min(120,remaining),public_checker=canonical)
    if execution['exit_code']!=0 or execution['timed_out']:
        raise RuntimeError('public_checker_infrastructure_failure')
    result=json.loads(execution['stdout'])
    write(run_dir/'public-checks'/f'{step:03d}'/'result.json',result)
    return result,execution['cpu_seconds']


def execute_action(action, root, canonical, sandbox, ledger, condition, run_dir, step, started, active_seconds, grading_timeout):
    action_started,tool_wall=time.monotonic(),0.0
    cpu_seconds, check_seconds = 0.0, 0.0
    termination, detail = None, None
    observation = {}
    try:
        remaining = active_seconds-(time.monotonic()-started)-grading_timeout
        if remaining <= 0:
            termination = "active_time_budget"
            return observation, termination, detail, cpu_seconds, time.monotonic()-action_started
        before_check = time.monotonic()
        events = ledger.refresh(root)
        public_state = condition_observation(root, canonical, condition)
        complete_structure = not public_state.get('missing_artifacts') and not public_state.get('malformed_artifacts')
        guided_schema = condition == 'full' and (step == 1 or step % 5 == 0 or action['action'] in ('inspect', 'finish'))
        if condition!='baseline' and (complete_structure or guided_schema):
            schemas,checker_cpu=public_schema_check(sandbox,canonical,run_dir,step,remaining)
            cpu_seconds+=checker_cpu
            public_state['schema_check']=schemas
            public_state['schema_check']['observed_before_action'] = step
            public_state['finish_gate_pass']=public_state['finish_gate_pass'] and not schemas['errors']
        check_seconds += time.monotonic()-before_check
        if action["action"] == "finish":
            # Unknown stages do not become stale-result certificates. Only
            # previously recorded outputs can be rejected as invalidated.
            stale = {case: sorted(set(data["stale"]) & set(data["verified"]) & {"mesh", "solve", "postprocess", "deliver"})
                for case, data in ledger.state.cases.items()}
            stale = {case: stages for case, stages in stale.items() if stages}
            blocked = condition != "baseline" and not public_state["finish_gate_pass"]
            if condition in ("full", "no-comparison", "full-rerun", "full") and stale:
                blocked = True
            if not blocked:
                termination, detail = "agent_finished", action.get("summary")
                write(run_dir/"final-public-check.json", public_state)
                if condition == 'full':
                    observation = {"public_checks": public_state, "finish_accepted": True}
                return observation, termination, detail, cpu_seconds, time.monotonic()-action_started
            observation = {"finish_rejected": True, "public_checks": public_state, "stale_evidence": stale}
        elif action["action"] == "inspect":
            observation = {"public_checks": public_state}
        elif action["action"] == "recover":
            if condition in ("baseline", "artifact-check"):
                observation = {"instruction": "Inspect original files and choose a real tool action; no recovery planner is enabled for this condition."}
            else:
                observation = ledger.recovery(root, action["case"], full_rerun=condition == "full-rerun")
        else:
            if action["action"] == "run":
                action['case']=confined(root,action['case']).relative_to(root.resolve()).as_posix()
                if not isinstance(action.get("outputs", []), list):
                    raise ValueError("outputs_must_be_list")
                for name in action.get("outputs", []):
                    confined(root, name)
            comparison_enabled = condition in ("full", "full-rerun", "full")
            conflicts = public_state.get("actual_comparison", {}).get("errors", [])
            # Missing cases are reported, but do not prevent sequential
            # construction/execution. Observed contradictions block a run.
            conflicts = [item for item in conflicts if not item.startswith("unmatched_actual_cases:")]
            if action["action"] == "run" and action["stage"] == "solve" and comparison_enabled and conflicts:
                observation = {"execution_rejected": True, "reason": "actual_configuration_contradiction", "errors": conflicts}
            else:
                remaining = active_seconds-(time.monotonic()-started)-grading_timeout
                if remaining <= 0:
                    return {"budget_exhausted_before_execution": True}, "active_time_budget", None, cpu_seconds, time.monotonic()-action_started
                timeout = min(float(action.get("timeout_s", remaining)), remaining)
                if not math.isfinite(timeout) or timeout <= 0:
                    raise ValueError("invalid_timeout")
                timeout_policy = None
                if condition == 'full':
                    timeout, timeout_policy = guided_timeout(action, remaining)
                write(run_dir/"heartbeat.json", {"step": step, "state": "tool_running", "at": now(), "elapsed_seconds": time.monotonic()-started})
                before_inputs,extra_inputs=None,{}
                if action['action']=='run':
                    case=confined(root,action['case'])
                    if case.is_dir():
                        before_inputs=fingerprint(case)
                    extra_inputs=ledger.command_inputs(root,action['case'],action['command'],action.get('inputs'))
                    write(run_dir/'commands'/f'{step:03d}-inputs.json',{'case_fingerprints':before_inputs,'extra_inputs':extra_inputs})
                execution = sandbox.execute(action["command"], run_dir/"commands"/f"{step:03d}", timeout=timeout)
                tool_wall=execution['wall_seconds']
                write(run_dir/"commands"/f"{step:03d}"/"execution.json", {"command": action["command"], **execution})
                cpu_seconds += execution["cpu_seconds"]
                observation = {"execution": execution}
                if timeout_policy:
                    observation['tool_limits'] = timeout_policy
                check_start = time.monotonic()
                events += ledger.refresh(root)
                if action["action"] == "run":
                    # Include newly created scripts in the binding, while
                    # checking existing declared/script inputs across execution.
                    new_inputs=ledger.command_inputs(root,action['case'],action['command'],action.get('inputs'))
                    binding=ledger.record(root, action["case"], action["stage"], action.get("outputs", []), execution, step,before_inputs,extra_inputs)
                    if binding['recorded']:
                        ledger.executions[(action['case'],action['stage'])]['extra_inputs']=new_inputs
                    observation['binding']=binding
                check_seconds += time.monotonic()-check_start
        if condition in ("full", "no-comparison", "full-rerun", "full"):
            observation["state_events"] = events
            observation["evidence_state"] = ledger.snapshot(compact=True)
        if condition != "baseline":
            # Public contract visibility is the B1 baseline as well as the
            # method; it is never counted as a unique method contribution.
            latest=condition_observation(root, canonical, condition)
            if 'schema_check' in public_state:
                latest['schema_check']=public_state['schema_check']
                latest['finish_gate_pass']=latest['finish_gate_pass'] and not public_state['schema_check']['errors']
            observation["public_checks"] = latest
        if condition == "no-comparison" and "public_checks" in observation:
            observation["public_checks"] = {key: value for key, value in observation["public_checks"].items()
                if key not in ("actual_comparison", "public_plan_check")}
    except (ValueError, KeyError, OSError, TypeError) as exc:
        observation = {"action_error": type(exc).__name__+":"+str(exc)}
    return observation, termination, detail, cpu_seconds, max(0,time.monotonic()-action_started-tool_wall)


def run_task(bundle, task_dir, run_dir, key_path, runtime, condition="baseline",
             max_steps=150, history=15, active_seconds=18000, cpu_set="192-207",
             model="deepseek-flash", api_timeout=600, api_retries=3,
             max_tokens=None, grading_timeout=900, relay_url=None, purpose="exploratory", protocol_sha256=None):
    if condition not in CONDITIONS:
        raise ValueError("unknown_condition")
    run_dir.mkdir(parents=True, exist_ok=False)
    entry = next(t for t in load(bundle/"manifest.json")["tasks"] if t["directory"] == task_dir)
    public, grader = bundle/"public"/task_dir, bundle/"trusted"/task_dir
    hashes = {public/"init_file.tar.gz": entry["public_archive_sha256"],
              grader/"eval.py": entry["grader_sha256"],
              grader/"contract_evaluator.py": entry["precheck_sha256"]}
    if entry["reference_sha256"]:
        hashes[grader/"reference.json"] = entry["reference_sha256"]
    if entry.get('instruction_sha256'):
        hashes[public/'task.json'] = entry['instruction_sha256']
    if any(digest(path) != expected for path, expected in hashes.items()):
        raise ValueError("bundle_hash_mismatch")
    root = run_dir/"workspace"
    extract(public/"init_file.tar.gz", root)
    # Immutable controller copy is not mounted for the agent or final grader.
    canonical = run_dir/"public-contracts"
    canonical.mkdir()
    for name in ("task_spec.json", "reporting_contract.json", "case_plan.json"):
        target = canonical/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root/name, target)
    shutil.copy2(root/'contract_evaluator.py',canonical/'contract_evaluator.py')
    shutil.copytree(root/"local_assets", canonical/"local_assets")
    trace_source=run_dir/'public-trace'
    trace_source.mkdir()
    (root/'harness_trace').mkdir()
    sandbox = Sandbox(root, runtime, cpu_set, public_inputs=canonical,trace_source=trace_source)
    client = DeepSeekClient(key_path, model, timeout=api_timeout, retries=api_retries, relay_url=relay_url)
    ledger = Ledger()
    config = {"task": task_dir, "condition": condition, "model": model, "max_steps": max_steps,
        "history_exchanges": history, "active_seconds": active_seconds, "max_tokens": max_tokens,
        "api_timeout_seconds": api_timeout, "api_attempts": api_retries, "cpu_set": cpu_set,
        "started_at": now(), "bundle_manifest_sha256": digest(bundle/"manifest.json"),
        "protocol_sha256": protocol_sha256,
        "harness_code_sha256": {path.name:digest(path) for path in sorted(Path(__file__).parent.glob('*.py'))},
        "sampling_parameters": "Provider defaults; temperature, top_p and seed are not sent.",
        "purpose": purpose, "api_transport": "direct_official_https",
        "api_provider": "DeepSeek", "api_origin": "https://api.deepseek.com",
        "thinking_policy": "Provider defaults; preserve full assistant message including reasoning_content.",
        "checkpoint_resume": "disabled; task-specific validators not implemented"}
    write(run_dir/"config.json", config)
    config['harness_version'] = '0.2.2-deepseek-baseline'
    overlay = Path(__file__).resolve().parents[1] / 'runtime-tools' / 'ls'
    config['runtime_overlay_ls_sha256'] = digest(overlay) if overlay.is_file() else None
    write(run_dir/'config.json', config)
    software=[]
    for name in ('blockMesh','checkMesh','decomposePar','pimpleFoam','simpleFoam','reconstructPar','mpiexec'):
        executable=runtime/'bin'/name
        if executable.is_file():
            software.append({'name':name,'executable_path':str(executable),'sha256':digest(executable)})
    write(trace_source/'runtime.json',{'software':software,'source':'controller-observed executable files',
        'mpi_transport':{'MPIR_CVAR_CH4_NETMOD':'ofi','FI_PROVIDER':'shm'},'cpu_set':cpu_set})
    initial = load(public/"task.json")["instruction"] + "\nBudget: " + str(max_steps) + " actions and " + str(active_seconds) + " active seconds, including API, checks, tools and grading. Output tokens have no client-set limit."
    if condition in ("full", "no-comparison", "full-rerun", "full"):
        initial += "\nUse execution-bound state and recover recommendations to avoid reusing stale results. Mark per-stage output files explicitly in run actions."
    guide = None
    if condition == 'full':
        brief = public_brief(canonical)
        write(run_dir/'public-brief.json', brief)
        initial += "\nCompiled public task brief:\n" + json.dumps(brief, ensure_ascii=False)
        initial += "\nDeclared exploration/postprocess/delivery tools have bounded time; use run with the proper stage for long genuine solver work. These limits and the delivery reserve are recorded in config.json."
        guide = ProgressGuide(brief)
        config['guidance_policy'] = GUIDED_POLICY
        write(run_dir/'config.json', config)
    exchanges, total_usage = [], {}
    started = time.monotonic()
    started_controller_cpu = time.process_time()
    termination, detail = "step_budget", None
    cpu_seconds, check_seconds, step = 0.0, 0.0, 0
    turn = 0
    while step < max_steps:
        turn += 1
        remaining = active_seconds-(time.monotonic()-started)
        if remaining <= grading_timeout:
            termination = "active_time_budget"
            break
        write(run_dir/"heartbeat.json", {"step": step, "state": "api_wait", "at": now(), "elapsed_seconds": time.monotonic()-started})
        messages = history_messages(initial, exchanges, history, guide.latest if guide else None)
        write(run_dir/"trace"/f"turn-{turn:03d}-request.json", {"model": model, "messages": messages,
            "tools": TOOLS, "tool_choice": "auto", "parallel_tool_calls": False,
            **({"max_tokens": max_tokens} if max_tokens is not None else {})})
        client.timeout = min(api_timeout, max(1, int(remaining-grading_timeout)))
        client.deadline = started+active_seconds-grading_timeout
        try:
            response = client.complete(messages, max_tokens=max_tokens, tools=TOOLS)
            write(run_dir/"trace"/f"turn-{turn:03d}-response.json", response)
        except APIError as exc:
            write(run_dir/"trace"/f"turn-{turn:03d}-api-error.json", {"status": exc.status, "message": str(exc), "attempts": exc.attempts, "response": exc.response})
            termination = "active_time_budget" if time.monotonic()-started >= active_seconds-grading_timeout else "api_incomplete"
            detail = str(exc)
            break
        for key, value in response["usage"].items():
            if isinstance(value, (int, float)):
                total_usage[key] = total_usage.get(key, 0)+value
        calls = response.get("message", {}).get("tool_calls") or [None]
        feedback = [response.get("message") or {"role":"assistant","content":response["content"]}]
        for call in calls:
            if step >= max_steps or termination != "step_budget":
                observation={"not_executed":"task_already_terminated_or_action_budget"}
            else:
                step += 1
                action = {"action": "invalid"}
                try:
                    action = action_from_call(call) if call else action_from_response(response)
                    write(run_dir/"trace"/f"{step:03d}-action.json", action)
                    observation, ended, why, consumed_cpu, consumed_checks = execute_action(
                        action,root,canonical,sandbox,ledger,condition,run_dir,step,started,active_seconds,grading_timeout)
                    cpu_seconds += consumed_cpu
                    check_seconds += consumed_checks
                    if ended:
                        termination,detail=ended,why
                except (ValueError,KeyError,TypeError) as exc:
                    observation={"action_error":type(exc).__name__+":"+str(exc)}
                execution_dir=run_dir/'commands'/f'{step:03d}'
                if (execution_dir/'execution.json').is_file():
                    target=trace_source/f'{step:03d}'
                    target.mkdir()
                    for name in ('stdout.txt','stderr.txt','execution.json'):
                        shutil.copy2(execution_dir/name,target/name)
                    observation['recorded_execution_path']='harness_trace/'+f'{step:03d}'
                observation['budget']={'actions_used':step,'actions_remaining':max_steps-step,
                    'active_seconds_remaining':max(0,active_seconds-(time.monotonic()-started))}
                if guide:
                    observation['progress_guidance'] = guide.observe(action, observation, ledger, step, max_steps,
                        active_seconds-(time.monotonic()-started)-grading_timeout)
                    write(run_dir/'progress.json', guide.latest)
                write(run_dir/"trace"/f"{step:03d}-observation.json", observation)
            model_observation = compact_feedback(observation) if guide else observation
            if call:
                feedback.append({"role":"tool","tool_call_id":call["id"],"content":json.dumps(model_observation,ensure_ascii=False)})
            else:
                feedback.append({"role":"user","content":json.dumps(model_observation,ensure_ascii=False)})
        exchanges.append(feedback)
        write(run_dir/"heartbeat.json", {"step":step,"api_turn":turn,"state":"action_complete","at":now(),"elapsed_seconds":time.monotonic()-started})
        if termination != "step_budget":
            break
    write(run_dir/"ledger.json", ledger.snapshot())
    grading = grade(sandbox, grader, run_dir, min(grading_timeout, max(1, active_seconds-(time.monotonic()-started))))
    grading_cpu=grading.get('cpu_seconds_estimate',0)
    controller_cpu=time.process_time()-started_controller_cpu
    status = "infra_incomplete" if termination == "api_incomplete" or grading["status"] != "scored" else "evaluated"
    result = {"task": task_dir, "condition": condition, "model": model, "status": status,
        "termination": termination, "termination_detail": detail, "steps": step, "api_turns": turn,
        "score": grading["score"] if status == "evaluated" else None,
        "success": grading["success"] if status == "evaluated" else None,
        "full_score": grading.get("full_score", grading["success"]) if status == "evaluated" else None,
        "error_free": grading.get("error_free", not grading.get("result", {}).get("errors")) if status == "evaluated" else None,
        "grading": grading, "usage": total_usage, "tool_cpu_seconds": cpu_seconds,
        "grading_cpu_seconds_estimate": grading_cpu, "controller_cpu_seconds": controller_cpu,
        "total_local_cpu_seconds_estimate": cpu_seconds+grading_cpu+controller_cpu,
        "check_seconds": check_seconds, "wall_seconds": time.monotonic()-started,
        "finished_at": now(), "development_run": purpose == "development", "purpose": purpose}
    write(run_dir/"result.json", result)
    write(run_dir/"heartbeat.json", {"step": step, "state": status, "at": now()})
    return result


def main():
    p = argparse.ArgumentParser()
    for name in ("bundle", "run-dir", "key-path", "runtime"):
        p.add_argument("--"+name, type=Path, required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--condition", choices=("baseline",), default="baseline")
    p.add_argument("--max-steps", type=int, default=150)
    p.add_argument("--history", type=int, default=15)
    p.add_argument("--active-seconds", type=int, default=18000)
    p.add_argument("--cpu-set", default="192-207")
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--relay-url", default=None)
    p.add_argument("--purpose", choices=("development", "exploratory", "final"), default="exploratory")
    args = vars(p.parse_args())
    args["task_dir"] = args.pop("task")
    result = run_task(**args)
    print(json.dumps({key: result[key] for key in ("task", "condition", "status", "termination", "score", "steps")}), flush=True)


if __name__ == "__main__":
    main()
