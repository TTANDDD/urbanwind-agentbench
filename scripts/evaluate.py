"""Score existing submitted artifacts, keeping the scorer outside the agent process."""
import argparse, json, os, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--workspace',type=Path,required=True)
    a=p.parse_args();status=json.loads((ROOT/'benchmark/task_status.json').read_text())
    if a.task not in status['paper_evaluation_tasks']: p.error('This entry point supports the 30 paper tasks; inspect other task-specific dependencies separately.')
    t=ROOT/'benchmark'/a.task;task=json.loads((t/(a.task+'.json')).read_text(encoding='utf-8'))
    refs=[ROOT/'benchmark'/x['local_path'] for c in task['evaluator'].get('postconfig',[]) for x in c.get('parameters',{}).get('files',[]) if 'ground_truth/' in x['local_path']]
    if len(refs)>1 or any(not f.is_file() for f in refs): p.error('Scoring reference is missing or ambiguous')
    if not a.workspace.is_dir(): p.error('Workspace must already exist')
    env=dict(os.environ,URBANWIND_EVAL_ROOT=str(a.workspace.resolve()))
    env['PYTHONPATH']=str(t/'init_file')+os.pathsep+str(ROOT/'benchmark/evaluator_framework')
    if refs: env['URBANWIND_REFERENCE']=str(refs[0])
    raise SystemExit(subprocess.call([sys.executable,str(t/'eval.py')],env=env,cwd=a.workspace.resolve()))
if __name__=='__main__':main()
