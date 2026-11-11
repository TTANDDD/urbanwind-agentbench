"""Small native Linux qualification; no model/API/CFD evaluation is launched.

Run after copying the new cpu_boundary module and compiled C bridge to an owned
qualification directory. This tests actual kernel calls and a two-rank MPI
launch, not simulated syscall records. Receipt includes exact stdout/stderr.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from urbanwind_harness.cpu_boundary import CpuAffinityBoundary, parse_cpu_set


GUEST = r'''
import ctypes, errno, json, os, subprocess, sys, threading
allowed = set(map(int, sys.argv[1].split(',')))
outside = int(sys.argv[2])
first = min(allowed)
evidence = {'pid':os.getpid(), 'initial':sorted(os.sched_getaffinity(0)), 'checks':{}}
os.sched_setaffinity(0, set(range(256)))
evidence['checks']['widen_clamped'] = sorted(os.sched_getaffinity(0))
assert set(evidence['checks']['widen_clamped']) == allowed
os.sched_setaffinity(os.getpid(), {first})
evidence['checks']['nonzero_self_narrow'] = sorted(os.sched_getaffinity(0))
assert evidence['checks']['nonzero_self_narrow'] == [first]
os.sched_setaffinity(0, allowed)
try: os.sched_setaffinity(0, {outside})
except OSError as error: evidence['checks']['outside_errno'] = error.errno
else: raise AssertionError('outside_only_succeeded')
assert evidence['checks']['outside_errno'] == errno.EINVAL
try: os.sched_setaffinity(2147483647, {first})
except OSError as error: evidence['checks']['unknown_tid_errno'] = error.errno
else: raise AssertionError('unknown_tid_succeeded')
assert evidence['checks']['unknown_tid_errno'] == errno.ESRCH
libc = ctypes.CDLL(None, use_errno=True)
libc.sched_setaffinity.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
libc.sched_setaffinity.restype = ctypes.c_int
assert libc.sched_setaffinity(0, 128, ctypes.c_void_p(1)) == -1
evidence['checks']['invalid_pointer_errno'] = ctypes.get_errno()
assert evidence['checks']['invalid_pointer_errno'] == errno.EFAULT
child_code = "import json,os,sys; print(os.getpid(),flush=True); sys.stdin.readline(); print(json.dumps(sorted(os.sched_getaffinity(0))),flush=True)"
child = subprocess.Popen([sys.executable, '-c', child_code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
child_pid = int(child.stdout.readline())
os.sched_setaffinity(child_pid, {first})
child.stdin.write('inspect\n'); child.stdin.flush()
evidence['checks']['nonzero_child_narrow'] = json.loads(child.stdout.readline())
assert evidence['checks']['nonzero_child_narrow'] == [first]
assert child.wait(timeout=5) == 0
ready, inspect = threading.Event(), threading.Event()
thread_id, thread_affinity = [], []
def other_thread():
    thread_id.append(threading.get_native_id()); ready.set(); inspect.wait(5)
    thread_affinity.extend(sorted(os.sched_getaffinity(0)))
thread = threading.Thread(target=other_thread); thread.start(); assert ready.wait(5)
os.sched_setaffinity(thread_id[0], {first}); inspect.set(); thread.join(5)
assert not thread.is_alive() and thread_affinity == [first]
evidence['checks']['nonzero_thread_narrow'] = thread_affinity
print(json.dumps(evidence, sort_keys=True))
'''

MPI_GUEST = r'''
import json, os, sys
rank = os.environ.get('PMI_RANK',os.environ.get('OMPI_COMM_WORLD_RANK'))
initial = sorted(os.sched_getaffinity(0))
allowed = sorted(map(int, sys.argv[1].split(',')))
# Exercise an actual rank process's binding syscall inside the MPI launch.
# This does not assert that hwloc process-binding works without full procfs.
os.sched_setaffinity(os.getpid(), {allowed[int(rank) % len(allowed)]})
print(json.dumps({'pid':os.getpid(),'rank':rank,'initial_affinity':initial,
                  'affinity':sorted(os.sched_getaffinity(0))}),flush=True)
'''


def namespace_arguments(directory, rootfs, runtime=None):
    # Never inherit host /etc/ld.so.preload. Qualification rootfs is read-only;
    # writable transient MPI/Python paths are isolated for this small test.
    result = ['bwrap', '--die-with-parent', '--new-session', '--unshare-all', '--share-net',
              '--uid', '0', '--gid', '0', '--ro-bind', str(rootfs), '/', '--dev', '/dev',
              '--tmpfs', '/tmp', '--tmpfs', '/run', '--tmpfs', '/sys', '--tmpfs', '/proc', '--dir', '/proc/self',
              '--bind', str(directory), '/tmp/qualification']
    if runtime:
        result += ['--ro-bind', str(runtime), '/tmp/runtime', '--setenv', 'LD_LIBRARY_PATH',
                   '/tmp/runtime/lib/mpich-3.3:/tmp/runtime/lib',
                   '--setenv', 'MPIR_CVAR_CH4_NETMOD', 'ofi', '--setenv', 'FI_PROVIDER', 'shm']
    for path in ('/proc/cpuinfo', '/proc/meminfo', '/proc/self/status',
                 '/sys/devices/system/cpu', '/sys/devices/system/node'):
        if Path(path).exists(): result += ['--ro-bind', path, path]
    return result + ['--chdir', '/tmp/qualification', '--']


def run(boundary, command, cpus, timeout=30):
    process = subprocess.Popen(['taskset', '-c', cpus, *boundary.command(command)],
                               pass_fds=boundary.pass_fds, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, text=True)
    try:
        boundary.attach(process)
        stdout, stderr = process.communicate(timeout=timeout)
        code = process.returncode
    except BaseException:
        # Kill only the newly created qualification launcher/session.
        import signal
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        raise
    finally:
        started = time.monotonic()
        snapshot = boundary.close()
        snapshot['close_seconds'] = time.monotonic() - started
    return {'exit_code': code, 'stdout': stdout, 'stderr': stderr, 'boundary': snapshot}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpus', required=True)
    parser.add_argument('--bridge', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rootfs', type=Path, required=True)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--mpi-executable', default='/usr/bin/mpiexec')
    parser.add_argument('--mpi-binding', help='Optional Hydra binding; default uses ordinary unmodified launch')
    args = parser.parse_args()
    directory = args.output.resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    allowed = parse_cpu_set(args.cpus)
    outside = next(cpu for cpu in sorted(os.sched_getaffinity(0)) if cpu not in allowed)
    cpustr = ','.join(map(str, sorted(allowed)))
    (directory / 'cpu_guest_checks.py').write_text(GUEST)
    (directory / 'cpu_mpi_probe.py').write_text(MPI_GUEST)
    receipt = {'created_at_unix': time.time(), 'kernel': platform.release(),
               'machine': platform.machine(), 'host_initial_affinity': sorted(os.sched_getaffinity(0)),
               'allocation': sorted(allowed), 'outside_test_cpu': outside,
               'bridge_sha256': hashlib.sha256(args.bridge.read_bytes()).hexdigest(),
               'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'new_model_calls': 0, 'new_cfd_calls': 0, 'cases': {}, 'passed': False}
    try:
        direct = run(CpuAffinityBoundary(allowed, args.bridge),
                     [sys.executable, str(directory / 'cpu_guest_checks.py'), cpustr, str(outside)], cpustr)
        receipt['cases']['native_host_namespace'] = direct
        assert direct['exit_code'] == 0, direct
        assert not direct['boundary']['listener_alive'] and direct['boundary']['close_seconds'] < 1.3
        namespace = namespace_arguments(directory, args.rootfs, args.runtime)
        guest = run(CpuAffinityBoundary(allowed, args.bridge),
                    namespace + ['/usr/bin/python3', '/tmp/qualification/cpu_guest_checks.py', cpustr, str(outside)], cpustr)
        receipt['cases']['native_guest_pid_namespace'] = guest
        assert guest['exit_code'] == 0, guest
        assert not guest['boundary']['listener_alive'] and guest['boundary']['close_seconds'] < 1.3
        # Same real MPI launcher as the host installation; no CFD computation.
        mpi_executable = args.mpi_executable
        if args.runtime and Path(mpi_executable).is_relative_to(args.runtime):
            mpi_executable = '/tmp/runtime/' + str(Path(mpi_executable).relative_to(args.runtime))
        mpi_argv = [mpi_executable]
        if args.mpi_binding: mpi_argv += ['-bind-to', args.mpi_binding]
        mpi_argv += ['-n', '2', '/usr/bin/python3', '/tmp/qualification/cpu_mpi_probe.py', cpustr]
        mpi = run(CpuAffinityBoundary(allowed, args.bridge), namespace + mpi_argv, cpustr)
        receipt['cases']['native_mpi_two_rank_binding'] = mpi
        assert mpi['exit_code'] == 0, mpi
        ranks = [json.loads(line) for line in mpi['stdout'].splitlines() if line.startswith('{')]
        assert len(ranks) == 2 and all(set(rank['affinity']) <= allowed and rank['affinity'] for rank in ranks), ranks
        assert mpi['boundary']['counts']['accepted'] >= 2, 'MPI did not exercise affinity binding'
        assert not mpi['boundary']['listener_alive']
        receipt['passed'] = True
    finally:
        receipt['ended_at_unix'] = time.time()
        args.output.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({'passed':receipt['passed'], 'receipt':str(args.output),
                      'cases':{name:value['exit_code'] for name,value in receipt['cases'].items()}}))


if __name__ == '__main__': main()
