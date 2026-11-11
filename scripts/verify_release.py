"""Check copied-source hashes, task coverage, public archives and reference mappings."""
import ast, hashlib, json, tarfile
from pathlib import Path, PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
def load(p): return json.loads(p.read_text(encoding='utf-8'))
manifest=load(ROOT/'reproducibility/source_files.sha256.json')
for item in manifest['files']:
    p=ROOT/item['path']
    assert p.is_file(),item['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],item['path']
status=load(ROOT/'benchmark/task_status.json')
assert len(status['tasks'])==200 and status['case_count']==367
assert len(status['paper_evaluation_tasks'])==30
members=0
for row in status['tasks']:
    t=ROOT/'benchmark'/row['task']
    task=load(t/(t.name+'.json'))
    ast.parse((t/'eval.py').read_text(encoding='utf-8-sig'))
    with tarfile.open(t/'init_file/init_file.tar.gz','r:gz') as tf:
        for m in tf:
            p=PurePosixPath(m.name)
            assert not p.is_absolute() and '..' not in p.parts and not m.issym() and not m.islnk(),(t.name,m.name)
            assert 'ground_truth' not in p.parts and p.name!='eval.py',(t.name,m.name)
            members+=1
    if row['evaluated_in_paper']:
        refs=[x['local_path'] for cfg in task['evaluator'].get('postconfig',[]) for x in cfg.get('parameters',{}).get('files',[]) if 'ground_truth/' in x['local_path']]
        assert len(refs)<=1,(t.name,refs)
        assert all((ROOT/'benchmark'/r).is_file() for r in refs),(t.name,refs)
print(json.dumps({'source_hashes_verified':len(manifest['files']),'tasks':200,'cases':367,
                  'paper_tasks':30,'public_archive_members':members,'status':'passed'}))
