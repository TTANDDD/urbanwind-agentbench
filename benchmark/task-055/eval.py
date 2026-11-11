"""UrbanWind task055 v53 trusted-judge entry; data-only submissions."""
import argparse,hashlib,json,os,sys
from pathlib import Path
EXPECTED_BINDING_SHA='a5cfbdbc57ae87f8941304849b866a22866ef2b302df81d0435b80fe6c8d747a'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb')as f:
  for b in iter(lambda:f.read(65536),b''):h.update(b)
 return h.hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--submission',type=Path,default=Path('/home/user/Desktop'));p.add_argument('--request-id',required=True);p.add_argument('--resume-claim',action='store_true');p.add_argument('--operator-binding',type=Path,default=Path(__file__).parent/'ground_truth/private_judge_v53/operator-binding-v4.json');a=p.parse_args()
 result={'status':'STOP','score':None,'native_calls':0,'errors':[]}
 try:
  if not sys.flags.isolated:raise ValueError('trusted judge must use isolated Python -I')
  if a.operator_binding.is_symlink()or sha(a.operator_binding)!=EXPECTED_BINDING_SHA:raise ValueError('private operator binding bytes differ')
  binding=json.loads(a.operator_binding.read_text());package=Path(__file__).parent/'judge_v53_v4'
  if package.is_symlink()or {p.name for p in package.iterdir()}!=set(binding['package_sources_sha256']):raise ValueError('unexpected judge import candidate')
  for name,h in binding['package_sources_sha256'].items():
   if Path(name).name!=name or(package/name).is_symlink()or sha(package/name)!=h:raise ValueError('installed judge helper bytes differ')
  numeric=Path(binding['numeric_environment']);mp=numeric/'manifest.json'
  if sha(mp)!=binding['numeric_manifest_sha256']:raise ValueError('numeric runtime manifest differs')
  nm=json.loads(mp.read_text())
  if sha(sys.executable)!=nm['python_sha256']:raise ValueError('bound judge Python differs')
  for rel,item in nm['files'].items():
   f=numeric/rel
   if not f.is_relative_to(numeric)or f.is_symlink()or sha(f)!=item['sha256']:raise ValueError('numeric runtime bytes differ')
  if {p.relative_to(numeric).as_posix()for p in numeric.rglob('*')if p.is_file()}!=set(nm['files'])|{'manifest.json'}:raise ValueError('unexpected numeric import candidate')
  for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='1'
  sys.path[:]=[p for p in sys.path if 'site-packages'not in p.lower()]
  sys.path.append(str(numeric));sys.path.insert(0,str(package))
  binding['_actual_binding_sha256']=EXPECTED_BINDING_SHA
  from task055_v53_formal_backend_v4 import evaluate
  result=evaluate(a.submission,binding,a.request_id,a.resume_claim)
 except Exception as e:result['errors'].append(type(e).__name__+': '+str(e))
 print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
 raise SystemExit(0 if result['score']==100 else 1)
if __name__=='__main__':main()
