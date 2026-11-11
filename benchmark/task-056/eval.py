"""Task056 v54 isolated TRUSTED HOST entry, NOT VM release or launch authority."""
import argparse,ctypes,hashlib,json,os,sys
from pathlib import Path
MANIFEST_SHA='4eb1ab21787224269b74a2e966ce575a0d6ddc1b911b627ee571225b8c65704f'
HERE=Path(__file__).resolve().parent
JUDGE=HERE/'judge_v54_v1'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def require(ok,msg):
 if not ok:raise ValueError(msg)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--submission',type=Path,required=True);a=ap.parse_args()
 require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'isolated -I -B -S required')
 require(sha(sys.executable)=='f77193cf0405ab440c39324bdb2f8864596321c1df888adbbe357f3d760f4716','pinned local host Python')
 for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):os.environ[k]='1'
 from ctypes import wintypes
 kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=wintypes.HANDLE;h=kernel.GetCurrentProcess()
 kernel.SetPriorityClass.argtypes=[wintypes.HANDLE,wintypes.DWORD]
 kernel.GetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
 kernel.SetProcessAffinityMask.argtypes=[wintypes.HANDLE,ctypes.c_size_t]
 own=ctypes.c_size_t();system=ctypes.c_size_t()
 require(kernel.SetPriorityClass(h,0x4000) and kernel.GetProcessAffinityMask(h,ctypes.byref(own),ctypes.byref(system)) and own.value,'own below-normal affinity')
 require(kernel.SetProcessAffinityMask(h,own.value&-own.value),'own single inherited CPU')
 require(sha(JUDGE/'manifest.json')==MANIFEST_SHA,'compiled operator source/slot manifest pin')
 manifest=json.loads((JUDGE/'manifest.json').read_text())
 require({p.name for p in JUDGE.iterdir()}==set(manifest['files'])|{'manifest.json'},'exact private installed package')
 for n,pin in manifest['files'].items():
  p=JUDGE/n
  require(p.is_file() and not p.is_symlink() and not p.is_junction() and p.stat().st_nlink==1 and sha(p)==pin,'frozen installed source/slot bytes')
 slot=json.loads((JUDGE/'operator-slot.json').read_text())
 require(slot['scope']=='ONE_ACTUAL_CLOSED_FULL7_TRUSTED_HOST_SLOT_NO_NEW_EXECUTION','host slot only')
 for p,pin in slot['qualification_evidence_sha256'].items():require(sha(p)==pin,'actual positive/refusal evidence pin')
 dep=Path(slot['private_numpy_root'])
 require(sha(dep/'manifest.json')==slot['private_numpy_manifest_sha256'],'private numeric manifest')
 for n,item in json.loads((dep/'manifest.json').read_text())['files'].items():
  p=dep/n
  require(p.is_file() and not p.is_symlink() and not p.is_junction() and p.stat().st_nlink==1 and p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],'actual private numeric source/runtime bytes')
 sys.path[:0]=[str(JUDGE),str(dep)]
 from task056_full7_entry_v1 import Full7Context
 result=Full7Context(slot['binding_path'],slot['binding_sha256']).inspect(a.submission.absolute())
 require(result['status']=='PASS_FULL7_CANDIDATE_RAW_DIAGNOSTIC_NOT_INSTALLED_SCORE' and result['job_id']==slot['job_id'],'all7 current closed job and all51/50 recomputation')
 return {'status':'PASS_TRUSTED_HOST_CLOSED_FULL7_ENTRY_NOT_REFERENCE_ACCEPTANCE','score':100,
  'task_id':'urbanwind-task-056','job_id':slot['job_id'],'all_conjunctive_gates':True,
  'actual_metrics':result['actual_metrics'],'complete_solver_log':result['complete_CFD']['solver_log'],
  'all51_volume_states':True,'all50_native_phi':True,'phi_reconstructed':False,
  'operator_binding_sha256':slot['binding_sha256'],'installed_manifest_sha256':MANIFEST_SHA,
  'new_native_calls':0,'new_CFD':0,'reference_ready':False,'release_valid':False,
  'agent_performance_claimed':False,'original_owner_completion_borrowed':False,
  'boundary':'Local protected host entry for this prospectively registered full7 job; no portable VM hidden harness, experimental truth, future launch or generic submitted completion authority.'}
if __name__=='__main__':
 try:result=main()
 except Exception as exc:
  print(json.dumps({'status':'REJECT_DATA_OR_EVIDENCE_NOT_NUMERICAL_ZERO','score':0,'error':str(exc),
   'new_native_calls':0,'new_CFD':0,'reference_ready':False,'release_valid':False}),flush=True)
  raise SystemExit(1)
 print(json.dumps(result,ensure_ascii=False,sort_keys=True),flush=True)
