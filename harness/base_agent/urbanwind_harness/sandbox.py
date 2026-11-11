from __future__ import annotations

import os
import hashlib
import json
import ctypes
import ctypes.util
try:
    import resource
except ModuleNotFoundError:
    resource=None
import signal
import subprocess
import time
import tempfile
from pathlib import Path


class Sandbox:
    """Fresh Linux namespaces; only public workspace and runtime are visible."""
    def __init__(self, workspace: Path, runtime: Path, cpu_set="192-207", public_inputs=None, trace_source=None, runtime_tools=None):
        if resource is None:
            raise RuntimeError('execution_requires_a_Linux_host')
        self.workspace, self.runtime = workspace.resolve(), runtime.resolve()
        self.cpu_set = cpu_set
        self.public_inputs = public_inputs
        self.trace_source = trace_source
        self.runtime_tools = Path(runtime_tools) if runtime_tools else Path(__file__).resolve().parents[1] / 'runtime-tools'
        if not (self.runtime_tools / 'ls').is_file():
            raise RuntimeError('qualified_runtime_ls_missing; run scripts/prepare_runtime_tools.py')
        manifest = json.loads((self.runtime_tools / 'manifest.json').read_text())
        if hashlib.sha256((self.runtime_tools / 'ls').read_bytes()).hexdigest() != manifest['binary_sha256']:
            raise RuntimeError('qualified_runtime_ls_hash_mismatch')
        if not (self.runtime / "etc/bashrc").is_file():
            raise ValueError("OpenFOAM_runtime_missing")

    def command(self, command: str, grader: Path | None = None, seccomp_fd=None, public_checker=None):
        argv = ["taskset", "-c", self.cpu_set, "bwrap", "--die-with-parent", "--new-session",
                "--unshare-all", "--clearenv", "--ro-bind", "/usr", "/usr"]
        for path in ("/bin", "/sbin", "/lib", "/lib64"):
            if Path(path).exists():
                if Path(path).is_symlink():
                    argv.extend(["--symlink", os.readlink(path), path])
                else:
                    argv.extend(["--ro-bind", path, path])
        for path in ("/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d",
                     "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/matplotlibrc", "/etc/fonts"):
            if Path(path).exists():
                argv.extend(["--ro-bind", path, path])
        # This host forbids mounting procfs inside user namespaces. Expose only
        # read-only hardware/status files, never host PIDs, fd, environ or root.
        argv.extend(["--tmpfs", "/proc", "--dir", "/proc/self"])
        # The host ls needs /proc/self/fd for its launcher. Overlay the pristine
        # installed-package binary instead of exposing host file descriptors.
        argv.extend(['--ro-bind', str((self.runtime_tools / 'ls').resolve()), '/usr/bin/ls'])
        for path in ("/proc/cpuinfo", "/proc/meminfo", "/proc/stat", "/proc/uptime", "/proc/self/status",
                     "/proc/sys/kernel/random/boot_id", "/sys/devices/system/cpu", "/sys/devices/system/node"):
            if Path(path).exists():
                argv.extend(["--ro-bind", path, path])
        argv.extend(["--dev", "/dev", "--tmpfs", "/tmp",
                     "--tmpfs", "/dev/shm",
                     "--dir", "/home/user", "--ro-bind", str(self.runtime), str(self.runtime),
                     "--bind", str(self.workspace), "/home/user/Desktop",
                     "--setenv", "HOME", "/home/user", "--setenv", "USER", "user",
                     "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
                     "--setenv", "OMP_NUM_THREADS", "1", "--setenv", "OPENBLAS_NUM_THREADS", "1",
                     "--setenv", "MPIR_CVAR_CH4_NETMOD", "ofi", "--setenv", "FI_PROVIDER", "shm",
                     "--setenv", "MKL_NUM_THREADS", "1", "--chdir", "/home/user/Desktop"])
        if grader is not None:
            argv.extend(["--ro-bind", str(grader.resolve()), "/opt/urbanwind-grader",
                         "--setenv", "URBANWIND_REFERENCE", "/opt/urbanwind-grader/reference.json",
                         "--setenv", "URBANWIND_EVAL_ROOT", "/home/user/Desktop"])
        if self.public_inputs is not None:
            for name in ("task_spec.json", "reporting_contract.json", "case_plan.json", "local_assets"):
                argv.extend(["--ro-bind", str((self.public_inputs/name).resolve()), "/home/user/Desktop/"+name])
        if self.trace_source is not None:
            argv.extend(['--ro-bind',str(self.trace_source.resolve()),'/home/user/Desktop/harness_trace'])
        if public_checker is not None:
            argv.extend(['--ro-bind',str(public_checker.resolve()),'/opt/urbanwind-public-check'])
        if seccomp_fd is not None:
            argv.extend(["--seccomp", str(seccomp_fd)])
        prefix = "source " + str(self.runtime) + "/etc/bashrc >/dev/null 2>&1; "
        prefix += "export PATH=" + str(self.runtime) + "/bin:${PATH}; "
        prefix += "export LD_LIBRARY_PATH=" + str(self.runtime) + "/lib/mpich-3.3:" + str(self.runtime) + "/lib:${LD_LIBRARY_PATH:-}; "
        return argv + ["--", "/bin/bash", "-c", prefix + command]

    def execute(self, command, log_dir: Path, timeout=600, grader=None, public_checker=None):
        log_dir.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        before_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        observed_cpu = {}
        with (log_dir / "stdout.txt").open("wb") as out, (log_dir / "stderr.txt").open("wb") as err, tempfile.TemporaryFile() as policy:
            affinity_policy(policy.fileno())
            policy.seek(0)
            process = subprocess.Popen(self.command(command, grader, policy.fileno(),public_checker), stdout=out, stderr=err,
                                       start_new_session=True, pass_fds=(policy.fileno(),))
            timed_out = False
            deadline = started + timeout
            while True:
                sample_process_cpu(process.pid, observed_cpu)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    break
                try:
                    process.wait(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        after_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        reaped_cpu = (after_cpu.ru_utime+after_cpu.ru_stime) - (before_cpu.ru_utime+before_cpu.ru_stime)
        # Namespace-init/MPI descendants are not reliably included in the
        # controller's RUSAGE_CHILDREN. Preserve host-observed per-process CPU
        # instead of reporting a 16-rank solve as a few milliseconds. Sampling
        # can miss short-lived processes, so this remains an explicit estimate.
        sampled_cpu = sum(observed_cpu.values())
        cpu_seconds = max(reaped_cpu, sampled_cpu)
        def tail(name):
            with (log_dir/name).open("rb") as handle:
                handle.seek(max(0, (log_dir/name).stat().st_size - 24000))
                return handle.read().decode(errors="replace")
        return {"exit_code": process.returncode, "timed_out": timed_out,
                "started_at_unix": time.time()-(time.monotonic()-started), "ended_at_unix": time.time(),
                "wall_seconds": time.monotonic()-started, "cpu_seconds": cpu_seconds,
                "cpu_measurement": {"method": "max(reaped CPU, sampled descendant own CPU)",
                    "sampling_interval_seconds": 0.2, "sampled_processes": len(observed_cpu),
                    "sampled_cpu_seconds": sampled_cpu, "reaped_cpu_seconds": reaped_cpu,
                    "limitation": "Estimate; short-lived descendants and time between samples can be missed."},
                "stdout": tail("stdout.txt"), "stderr": tail("stderr.txt")}


def sample_process_cpu(root_pid, observed):
    pending, seen = [root_pid], set()
    ticks = os.sysconf('SC_CLK_TCK')
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            fields = stat[stat.rfind(')')+2:].split()
            identity = (pid, fields[19])
            seconds = (int(fields[11])+int(fields[12]))/ticks
            observed[identity] = max(seconds, observed.get(identity, 0))
            pending.extend(int(value) for value in Path(f'/proc/{pid}/task/{pid}/children').read_text().split())
        except (OSError, ValueError, IndexError):
            continue


def affinity_policy(fd):
    # taskset sets the initial mask before bwrap installs this inherited filter.
    # The agent and MPI children cannot widen it using sched_setaffinity.
    library = ctypes.util.find_library('seccomp')
    if not library:
        raise RuntimeError('libseccomp_required_for_cpu_affinity_enforcement')
    lib=ctypes.CDLL(library)
    lib.seccomp_init.argtypes=[ctypes.c_uint32]
    lib.seccomp_init.restype=ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype=ctypes.c_int
    lib.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
    lib.seccomp_export_bpf.argtypes=[ctypes.c_void_p,ctypes.c_int]
    lib.seccomp_release.argtypes=[ctypes.c_void_p]
    context=lib.seccomp_init(0x7fff0000)
    if not context:
        raise RuntimeError('seccomp_init_failed')
    try:
        syscall=lib.seccomp_syscall_resolve_name(b'sched_setaffinity')
        if syscall<0 or lib.seccomp_rule_add(context,0x00050001,syscall,0)<0:
            raise RuntimeError('seccomp_affinity_rule_failed')
        if lib.seccomp_export_bpf(context,fd)<0:
            raise RuntimeError('seccomp_export_failed')
    finally:
        lib.seccomp_release(context)
