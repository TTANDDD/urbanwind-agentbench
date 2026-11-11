"""Persistent, writable, network-enabled task OS; private grading stays separate.

The host denies procfs mounts. Do not describe this as a fully equivalent VM.
Only hardware metadata is exposed in /proc; no host PIDs, fd, root or environs.
"""
from __future__ import annotations
import base64
import json
import os
import shlex
import signal
import select
import socket
import subprocess
import threading
import time
import resource
from pathlib import Path
from .sandbox import Sandbox, sample_process_cpu
from .resources import descendants
from .cpu_boundary import CpuAffinityBoundary


DEFAULT_ROOTFS = Path('/home/abc/urbanwind-open-runtime-cloud-20261011/rootfs')


class OpenSandbox:
    def __init__(self, workspace, runtime, cpu_set='192-207', public_inputs=None,
                 trace_source=None, runtime_tools=None, rootfs_base=None):
        initialization_started = time.monotonic()
        before_copy_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.workspace, self.runtime = Path(workspace).resolve(), Path(runtime).resolve()
        self.cpu_set, self.public_inputs, self.trace_source = cpu_set, public_inputs, trace_source
        self.base = Path(rootfs_base or os.environ.get('URBANWIND_OPEN_ROOTFS', DEFAULT_ROOTFS))
        if not (self.base / 'usr/bin/python3').exists(): raise RuntimeError('qualified_open_rootfs_missing')
        self.rootfs = self.workspace.parent / 'task-os'
        if self.rootfs.exists(): raise RuntimeError('task_os_already_exists')
        self.rootfs.mkdir()
        subprocess.run(['cp', '-a', '--reflink=auto', str(self.base) + '/.', str(self.rootfs)], check=True)
        after_copy_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
        copy_cpu = after_copy_cpu.ru_utime + after_copy_cpu.ru_stime - before_copy_cpu.ru_utime - before_copy_cpu.ru_stime
        self.server_path = Path(__file__).with_name('open_exec_server.py').resolve()
        self.observed, self.accounted_cpu = {}, 0.0
        self.cpu_lock = threading.Lock()
        self.stop_sampling = threading.Event()
        self.closed = False
        self.legacy = Sandbox(self.workspace, self.runtime, cpu_set,
            public_inputs=public_inputs, trace_source=trace_source, runtime_tools=runtime_tools)
        self.socket_path = self.rootfs / 'run/urbanwind-exec.sock'
        for name in ('proc/self', 'sys', 'dev', 'tmp', 'run', 'home/user/Desktop', 'opt/harness'):
            (self.rootfs / name).mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists(): self.socket_path.unlink()
        self.boundary = CpuAffinityBoundary(cpu_set, Path(__file__).resolve().parent.parent / 'runtime-tools/cpu_affinity_bridge')
        argv = ['bwrap', '--die-with-parent', '--new-session',
            '--unshare-all', '--uid', '0', '--gid', '0', '--clearenv',
            '--bind', str(self.rootfs), '/', '--dev', '/dev', '--tmpfs', '/proc', '--dir', '/proc/self',
            '--ro-bind', str(self.runtime), str(self.runtime),
            '--bind', str(self.workspace), '/home/user/Desktop',
            '--ro-bind', str(self.server_path), '/opt/harness/open_exec_server.py',
            '--setenv', 'HOME', '/root', '--setenv', 'USER', 'root', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin',
            '--setenv', 'MPIR_CVAR_CH4_NETMOD', 'ofi', '--setenv', 'FI_PROVIDER', 'shm',
            '--chdir', '/home/user/Desktop']
        for path in ('/proc/cpuinfo', '/proc/meminfo', '/proc/stat', '/proc/uptime',
                     '/proc/self/status', '/proc/sys/kernel/random/boot_id',
                     '/sys/devices/system/cpu', '/sys/devices/system/node'):
            if Path(path).exists(): argv += ['--ro-bind', path, path]
        if public_inputs:
            for name in ('task_spec.json', 'reporting_contract.json', 'case_plan.json', 'submission_format.json',
                         'open_environment_guide.md', 'open_contracts.py', 'contract_evaluator.py', 'acceptance_contract.json', 'local_assets'):
                source = Path(public_inputs) / name
                if source.exists(): argv += ['--ro-bind', str(source.resolve()), '/home/user/Desktop/' + name]
            argv += ['--ro-bind', str(Path(public_inputs).resolve()), '/opt/urbanwind-public-check']
        if trace_source: argv += ['--ro-bind', str(Path(trace_source).resolve()), '/home/user/Desktop/harness_trace']
        self.prefix = 'source ' + shlex.quote(str(self.runtime / 'etc/bashrc')) + ' >/dev/null 2>&1; '
        self.prefix += 'export PATH=' + shlex.quote(str(self.runtime / 'bin')) + ':${PATH}; '
        self.prefix += 'export LD_LIBRARY_PATH=' + shlex.quote(str(self.runtime / 'lib/mpich-3.3')) + ':' + shlex.quote(str(self.runtime / 'lib')) + ':${LD_LIBRARY_PATH:-}; '
        logs = self.workspace.parent / 'environment'
        logs.mkdir()
        self.out = (logs / 'service.stdout.txt').open('wb')
        self.err = (logs / 'service.stderr.txt').open('wb')
        try:
            self.process = subprocess.Popen(['taskset', '-c', cpu_set, *self.boundary.command(argv + ['--', '/usr/bin/python3', '-u', '/opt/harness/open_exec_server.py'])],
                stdout=self.out, stderr=self.err, pass_fds=self.boundary.pass_fds, start_new_session=True)
            self.boundary.attach(self.process)
            self.sampler = threading.Thread(target=self._sample, daemon=True)
            self.sampler.start()
            deadline = time.monotonic() + 30
            while not self.socket_path.exists():
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('open_namespace_start_failed:' + (logs / 'service.stderr.txt').read_text(errors='replace'))
                time.sleep(.05)
            self._start_network(logs)
            self.initialization_cpu_seconds = copy_cpu + self._cpu_delta()
            self.initialization_wall_seconds = time.monotonic() - initialization_started
            self._write_environment(logs)
        except BaseException:
            self.close(); raise

    def _write_environment(self, logs):
        (logs / 'environment.json').write_text(json.dumps({'namespace_pid': self.process.pid,
            'initialization_cpu_seconds_estimate': self.initialization_cpu_seconds,
            'initialization_wall_seconds': self.initialization_wall_seconds,
            'rootfs': str(self.rootfs), 'cpu_set_initial_affinity': self.cpu_set,
            'network': 'isolated network namespace with slirp4netns Internet NAT; host loopback disabled', 'filesystem': 'writable owned task rootfs + task workspace',
            'background_processes': 'persistent until task closes; redirect job stdout/stderr to files', 'business_execution_gates': [],
            'procfs': 'limited read-only hardware metadata; host forbids full procfs mount',
            'cpu_affinity': self.boundary.snapshot(),
            'private_grader': 'separate namespace after persistent task environment is closed'}, indent=2))

    def _start_network(self, logs):
        candidates = []
        for row in descendants(self.process.pid):
            try:
                if row['comm'].startswith('python') and b'open_exec_server.py' in Path('/proc/' + str(row['pid']) + '/cmdline').read_bytes():
                    candidates.append(row['pid'])
            except OSError: pass
        if len(candidates) != 1: raise RuntimeError('owned_guest_network_identity_ambiguous')
        target = candidates[0]
        self.network_out = (logs / 'network.stdout.txt').open('wb')
        self.network_err = (logs / 'network.stderr.txt').open('wb')
        ready_read, ready_write = os.pipe()
        exit_read, self.network_exit_write = os.pipe()
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'LD_LIBRARY_PATH': str(self.base / 'usr/lib/x86_64-linux-gnu')}
        self.network_process = subprocess.Popen(['taskset', '-c', self.cpu_set,
            str(self.base / 'usr/bin/slirp4netns'), '--configure', '--disable-host-loopback',
            '--userns-path=/proc/' + str(target) + '/ns/user', '--ready-fd=' + str(ready_write),
            '--exit-fd=' + str(exit_read), str(target), 'tap0'], env=env,
            stdout=self.network_out, stderr=self.network_err, pass_fds=(ready_write, exit_read))
        os.close(ready_write); os.close(exit_read)
        try:
            if not select.select([ready_read], [], [], 15)[0] or os.read(ready_read, 1) != b'1':
                raise RuntimeError('guest_network_setup_failed:' + (logs / 'network.stderr.txt').read_text(errors='replace'))
        finally: os.close(ready_read)
        (self.rootfs / 'etc/resolv.conf').write_text('nameserver 10.0.2.3\n')

    def _sample(self):
        while not self.stop_sampling.wait(.2):
            with self.cpu_lock:
                sample_process_cpu(self.process.pid, self.observed)
                if hasattr(self, 'network_process'): sample_process_cpu(self.network_process.pid, self.observed)

    def _cpu_delta(self):
        with self.cpu_lock:
            sample_process_cpu(self.process.pid, self.observed)
            if hasattr(self, 'network_process'): sample_process_cpu(self.network_process.pid, self.observed)
            total = sum(self.observed.values())
            delta = max(0., total - self.accounted_cpu)
            self.accounted_cpu = total
        return delta

    def execute(self, command, log_dir, timeout=600, grader=None, public_checker=None,
                progress_callback=None, cwd=None, cancel_callback=None):
        if grader is not None:
            # Task code never shares a PID/mount namespace with the grader.
            if not self.closed: self.close()
            return self.legacy.execute(command, Path(log_dir), timeout=timeout, grader=grader,
                public_checker=public_checker, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if self.closed: raise RuntimeError('task_environment_closed')
        if self.boundary.failure: raise RuntimeError('task_cpu_allocation_service_failed:' + self.boundary.failure)
        log_dir = Path(log_dir); log_dir.mkdir(parents=True, exist_ok=False)
        started, timestamp = time.monotonic(), time.time()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # Linux AF_UNIX addresses have a 108-byte limit. A directory fd resolves
        # the same owned socket without depending on the long run-root name.
        socket_directory = os.open(self.socket_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: connection.connect('/proc/self/fd/' + str(socket_directory) + '/' + self.socket_path.name)
        finally: os.close(socket_directory)
        connection.settimeout(.2)
        connection.sendall((json.dumps({'command': command, 'cwd': cwd, 'timeout': timeout, 'prefix': self.prefix}) + '\n').encode())
        pending, cancelled, next_heartbeat, outcome = b'', False, started + 15, None
        cancel_deadline = None
        with (log_dir / 'stdout.txt').open('wb') as out, (log_dir / 'stderr.txt').open('wb') as err:
            try:
                while outcome is None:
                    if self.boundary.failure: raise RuntimeError('task_cpu_allocation_service_failed:' + self.boundary.failure)
                    if cancel_callback and cancel_callback() and not cancelled:
                        connection.sendall(b'{"cancel":true}\n'); cancelled = True
                        cancel_deadline = time.monotonic() + 5
                    if time.monotonic() > started + timeout + 8 or (cancel_deadline and time.monotonic() > cancel_deadline):
                        # Host authority does not depend on a cooperative guest.
                        cleanup = self.close(cancelled=cancelled)
                        outcome = {'exit_code': -signal.SIGTERM, 'timed_out': not cancelled,
                                   'cancelled': cancelled, 'reaped_cpu_seconds': cleanup['cpu_seconds'],
                                   'environment_forced_closed': True}
                        break
                    if progress_callback and time.monotonic() >= next_heartbeat:
                        progress_callback(time.monotonic() - started); next_heartbeat = time.monotonic() + 15
                    try: part = connection.recv(65536)
                    except socket.timeout:
                        if self.process.poll() is not None: raise RuntimeError('open_namespace_exited_during_command')
                        continue
                    if not part: raise RuntimeError('open_execution_stream_disconnected')
                    pending += part
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        value = json.loads(line)
                        if value['event'] in ('stdout', 'stderr'):
                            (out if value['event'] == 'stdout' else err).write(base64.b64decode(value['base64']))
                        elif value['event'] == 'error': raise RuntimeError('guest_execution_error:' + value['message'])
                        elif value['event'] == 'done': outcome = value
            except BaseException:
                self.close(cancelled=True); raise
            finally: connection.close()
        sampled = self._cpu_delta()
        def tail(name):
            with (log_dir / name).open('rb') as handle:
                handle.seek(max(0, (log_dir / name).stat().st_size - 24000))
                return handle.read().decode(errors='replace')
        return {'exit_code': outcome['exit_code'], 'timed_out': outcome['timed_out'], 'cancelled': outcome['cancelled'],
            'started_at_unix': timestamp, 'ended_at_unix': time.time(), 'wall_seconds': time.monotonic() - started,
            'cpu_seconds': max(sampled, outcome['reaped_cpu_seconds']),
            'environment_forced_closed': outcome.get('environment_forced_closed', False),
            'cpu_measurement': {'method': 'max(task sampled own-CPU increment, command reaped CPU)',
                'sampled_cpu_seconds': sampled, 'reaped_cpu_seconds': outcome['reaped_cpu_seconds'],
                'sampling_interval_seconds': .2, 'limitation': 'Estimate; includes persistent background CPU since prior accounting; short-lived process samples may be missed.'},
            'stdout': tail('stdout.txt'), 'stderr': tail('stderr.txt')}

    def close(self, cancelled=False):
        if self.closed: return {'cpu_seconds': 0., 'already_closed': True}
        self.closed = True
        before = self._cpu_delta() if hasattr(self, 'process') else 0.
        if hasattr(self, 'process') and self.process.poll() is None:
            # Only this owned namespace process group is signalled; namespace
            # init exit destroys its isolated descendants, including background jobs.
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL); self.process.wait()
        after = self._cpu_delta() if hasattr(self, 'process') else 0.
        if hasattr(self, 'network_exit_write'):
            os.close(self.network_exit_write)
        if hasattr(self, 'network_process') and self.network_process.poll() is None:
            self.network_process.terminate()
            try: self.network_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.network_process.kill(); self.network_process.wait()
        self.stop_sampling.set()
        if hasattr(self, 'sampler'): self.sampler.join(timeout=1)
        if hasattr(self, 'out'): self.out.close()
        if hasattr(self, 'err'): self.err.close()
        if hasattr(self, 'network_out'): self.network_out.close()
        if hasattr(self, 'network_err'): self.network_err.close()
        boundary_evidence = self.boundary.close() if hasattr(self, 'boundary') else None
        if hasattr(self, 'workspace') and (self.workspace.parent / 'environment').exists():
            (self.workspace.parent / 'environment/cpu-allocation-final.json').write_text(json.dumps(boundary_evidence, indent=2))
        return {'cpu_seconds': before + after, 'namespace_exit_code': self.process.returncode if hasattr(self, 'process') else None,
                'cancelled': cancelled, 'background_jobs_closed': True, 'sampling_estimate': True, 'cpu_boundary': boundary_evidence}
