"""Ordinary per-task CPU allocation, without command or workflow restrictions.

Usage (Linux host worker; the binary is compiled once outside model workspaces)::

    boundary = CpuAffinityBoundary('192-207', '/owned/tools/cpu_affinity_bridge')
    process = subprocess.Popen(
        ['taskset', '-c', '192-207', *boundary.command(bwrap_arguments)],
        pass_fds=boundary.pass_fds, start_new_session=True, ...)
    boundary.attach(process)  # before waiting for the guest service
    # ... ordinary guest commands, MPI and background jobs ...
    # Stop and reap the owned namespace before closing its allocation service.
    process.wait()
    evidence = boundary.close()

Only sched_setaffinity is intercepted. Requested cores are intersected with the
task allocation, matching cpuset-style narrowing; an empty intersection returns
EINVAL. The host reads the mask once and performs the operation itself, never
CONTINUEing a pointer syscall. No credential or private grader file is exposed.

This is an ordinary-resource boundary, not a malicious-tamper-proof sandbox:
guest-installed seccomp filters/ptrace and host network privileges are outside
its guarantee. The scheduler must still audit actual solver affinity. Closing
the listener while guest code runs makes future affinity calls fail ENOSYS.
"""
from __future__ import annotations

import array
import ctypes
import errno
import os
import select
import socket
import threading
import time
from pathlib import Path


MAX_MASK_BYTES = 1024


class _SeccompData(ctypes.Structure):
    _fields_ = [('nr', ctypes.c_int32), ('arch', ctypes.c_uint32),
                ('instruction_pointer', ctypes.c_uint64), ('args', ctypes.c_uint64 * 6)]


class _Notification(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint64), ('pid', ctypes.c_uint32),
                ('flags', ctypes.c_uint32), ('data', _SeccompData)]


class _Response(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint64), ('val', ctypes.c_int64),
                ('error', ctypes.c_int32), ('flags', ctypes.c_uint32)]


class _Iovec(ctypes.Structure):
    _fields_ = [('iov_base', ctypes.c_void_p), ('iov_len', ctypes.c_size_t)]


def _ioc(direction, number, size):
    # Linux asm-generic ioctl layout, shared by the qualified x86_64/aarch64 ABI.
    return (direction << 30) | (size << 16) | (ord('!') << 8) | number


_RECV = _ioc(3, 0, ctypes.sizeof(_Notification))
_SEND = _ioc(3, 1, ctypes.sizeof(_Response))
_VALID = _ioc(1, 2, ctypes.sizeof(ctypes.c_uint64))


def parse_cpu_set(value):
    if not isinstance(value, str):
        result = {int(cpu) for cpu in value}
    else:
        result = set()
        for component in value.split(','):
            part = component.strip().split('-')
            if len(part) == 1: result.add(int(part[0]))
            elif len(part) == 2:
                first, last = map(int, part)
                if first > last: raise ValueError('reversed_cpu_range')
                result.update(range(first, last + 1))
            else: raise ValueError('invalid_cpu_range')
    if not result or min(result) < 0 or max(result) >= MAX_MASK_BYTES * 8:
        raise ValueError('invalid_or_unrepresentable_cpu_allocation')
    return frozenset(result)


def _identity(tid):
    root = Path('/proc') / str(tid)
    # Stat fields after the final ')' start with field3; starttime is field22.
    stat = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    fields = {}
    for line in (root / 'status').read_text().splitlines():
        name, _, content = line.partition(':')
        if name in ('PPid', 'NSpid', 'Uid', 'Tgid'): fields[name] = content.split()
    return {'tid': tid, 'start_ticks': int(stat[19]),
            'ppid': int(fields['PPid'][0]), 'uid': int(fields['Uid'][0]),
            'tgid': int(fields['Tgid'][0]),
            'namespace': (root / 'ns/pid').stat().st_ino,
            'guest_tid': int(fields['NSpid'][-1])}


class CpuAffinityBoundary:
    def __init__(self, cpu_set, bridge_path):
        if os.name != 'posix' or not hasattr(os, 'sched_setaffinity'):
            raise RuntimeError('cpu_boundary_requires_qualified_linux')
        self.allowed = parse_cpu_set(cpu_set)
        if not self.allowed.issubset(os.sched_getaffinity(0)):
            raise RuntimeError('cpu_allocation_outside_host_worker_affinity')
        self.bridge_path = str(Path(bridge_path).resolve())
        if not os.access(self.bridge_path, os.X_OK):
            raise RuntimeError('compiled_cpu_affinity_bridge_missing')
        self.channel, self.child_channel = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener = None
        self.process = None
        self.thread = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.wake_read, self.wake_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        self.counts = {'requests': 0, 'accepted': 0, 'clamped': 0, 'refused': 0,
                       'vanished': 0, 'errors': 0}
        self.errno_counts, self.recent_requests = {}, []
        self.failure = None
        self.closed = False
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
        self.libc.ioctl.restype = ctypes.c_int
        self.libc.process_vm_readv.argtypes = [ctypes.c_int, ctypes.POINTER(_Iovec), ctypes.c_ulong,
                                              ctypes.POINTER(_Iovec), ctypes.c_ulong, ctypes.c_ulong]
        self.libc.process_vm_readv.restype = ctypes.c_ssize_t

    @property
    def pass_fds(self):
        return (self.child_channel.fileno(),)

    def command(self, arguments):
        if self.closed or self.process is not None: raise RuntimeError('cpu_boundary_already_used')
        return [self.bridge_path, '--listener-socket-fd', str(self.child_channel.fileno()),
                '--', *map(str, arguments)]

    def attach(self, process, timeout=10):
        """Receive the owned helper's listener, start service, and acknowledge exec."""
        if self.closed or self.process is not None: raise RuntimeError('cpu_boundary_already_used')
        self.process = process
        self.root_identity = _identity(process.pid)
        self.child_channel.close()
        self.channel.settimeout(timeout)
        received = []
        try:
            marker, controls, flags, _ = self.channel.recvmsg(1, socket.CMSG_SPACE(4 * 8), socket.MSG_CMSG_CLOEXEC)
            for level, kind, content in controls:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    handles = array.array('i')
                    handles.frombytes(content[:len(content) - len(content) % handles.itemsize])
                    received.extend(handles)
            if marker != b'A' or flags & socket.MSG_CTRUNC or len(received) != 1:
                raise RuntimeError('cpu_affinity_listener_handshake_invalid')
            self.listener = received.pop()
            if 'seccomp' not in os.readlink('/proc/self/fd/' + str(self.listener)):
                raise RuntimeError('cpu_affinity_listener_not_seccomp')
            self.thread = threading.Thread(target=self._serve, name='task-cpu-allocation', daemon=True)
            self.thread.start()
            self.channel.sendall(b'R')
        except BaseException:
            for descriptor in received: os.close(descriptor)
            self.close()
            raise
        finally:
            self.channel.close()

    def _ioctl(self, operation, value):
        if self.libc.ioctl(self.listener, operation, ctypes.byref(value)) < 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number))

    def _valid(self, notification):
        self._ioctl(_VALID, ctypes.c_uint64(notification.id))

    def _owned(self, identity):
        """Also refuse host-namespace targets unrelated to this owned launcher."""
        # A non-leader thread's PPid skips its own thread-group leader. Check
        # that leader first so ordinary worker threads are owned correctly.
        try: current = _identity(identity['tgid']) if identity['tgid'] != identity['tid'] else identity
        except (OSError, ValueError, KeyError): return False
        for _ in range(128):
            if current['tid'] == self.root_identity['tid']:
                return current['start_ticks'] == self.root_identity['start_ticks']
            if current['ppid'] <= 1: return False
            try: current = _identity(current['ppid'])
            except (OSError, ValueError, KeyError): return False
        return False

    def _target(self, sender, guest_tid):
        if guest_tid == 0 or guest_tid == sender['guest_tid']: return sender
        if guest_tid < 0: raise ProcessLookupError(errno.ESRCH, 'negative_guest_tid')
        # Match within the exact sender PID namespace, including non-leader TIDs.
        for process_path in Path('/proc').iterdir():
            if not process_path.name.isdigit(): continue
            try:
                if (process_path / 'ns/pid').stat().st_ino != sender['namespace']: continue
                tids = (process_path / 'task').iterdir()
                for tid_path in tids:
                    candidate = _identity(int(tid_path.name))
                    if candidate['namespace'] == sender['namespace'] and candidate['guest_tid'] == guest_tid:
                        if self._owned(candidate): return candidate
                        raise PermissionError(errno.EPERM, 'target_outside_owned_task')
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        raise ProcessLookupError(errno.ESRCH, 'guest_tid_not_found_in_sender_namespace')

    def _emulate(self, notification):
        self._valid(notification)
        sender = _identity(int(notification.pid))
        if not self._owned(sender): raise PermissionError(errno.EPERM, 'sender_outside_owned_task')
        guest_tid = ctypes.c_int32(notification.data.args[0] & 0xffffffff).value
        size, pointer = int(notification.data.args[1]), int(notification.data.args[2])
        if size == 0 or size > MAX_MASK_BYTES:
            raise OSError(errno.EINVAL, 'cpu_mask_size_outside_qualified_bound')
        buffer = ctypes.create_string_buffer(size)
        local = _Iovec(ctypes.cast(buffer, ctypes.c_void_p), size)
        remote = _Iovec(ctypes.c_void_p(pointer), size)
        copied = self.libc.process_vm_readv(sender['tid'], ctypes.byref(local), 1,
                                          ctypes.byref(remote), 1, 0)
        if copied != size:
            number = ctypes.get_errno() if copied < 0 else errno.EFAULT
            raise OSError(number or errno.EFAULT, 'cannot_read_guest_cpu_mask')
        requested = {byte_index * 8 + bit for byte_index, byte in enumerate(buffer.raw)
                     for bit in range(8) if byte & (1 << bit)}
        self.current_request.update({'guest_target_tid': guest_tid, 'requested_cpus': sorted(requested)})
        effective = requested & self.allowed
        if not effective: raise OSError(errno.EINVAL, 'requested_mask_has_no_allocated_cpu')
        target = self._target(sender, guest_tid)
        self._valid(notification)
        if _identity(target['tid']) != target: raise ProcessLookupError(errno.ESRCH, 'target_identity_changed')
        os.sched_setaffinity(target['tid'], effective)
        with self.lock:
            self.counts['accepted'] += 1
            self.counts['clamped'] += int(effective != requested)

    def _serve(self):
        poller = select.poll()
        poller.register(self.listener, select.POLLIN | select.POLLHUP | select.POLLERR)
        poller.register(self.wake_read, select.POLLIN)
        try:
            while not self.stop.is_set():
                for descriptor, events in poller.poll(100):
                    if descriptor == self.wake_read or self.stop.is_set(): return
                    if events & select.POLLIN:
                        notification = _Notification()
                        try: self._ioctl(_RECV, notification)
                        except OSError as error:
                            if error.errno in (errno.ENOENT, errno.EINTR): continue
                            raise
                        with self.lock: self.counts['requests'] += 1
                        self.current_request = {'sender_host_tid': int(notification.pid)}
                        number = 0
                        try: self._emulate(notification)
                        except OSError as error:
                            number = error.errno or errno.EIO
                            with self.lock:
                                self.counts['vanished' if number == errno.ENOENT else 'refused'] += 1
                        except (ValueError, KeyError):
                            number = errno.ESRCH
                            with self.lock: self.counts['refused'] += 1
                        response = _Response(id=notification.id, val=0, error=-number, flags=0)
                        with self.lock:
                            if number:
                                key = str(number)
                                self.errno_counts[key] = self.errno_counts.get(key, 0) + 1
                            self.recent_requests.append({**self.current_request, 'errno': number})
                            self.recent_requests[:] = self.recent_requests[-16:]
                        try: self._ioctl(_SEND, response)
                        except OSError as error:
                            if error.errno in (errno.ENOENT, errno.EINTR):
                                with self.lock: self.counts['vanished'] += 1
                            else: raise
                    if events & (select.POLLHUP | select.POLLERR): return
        except BaseException as error:
            self.failure = type(error).__name__ + ':' + str(error)
            with self.lock: self.counts['errors'] += 1
        finally:
            # One owner closes the listener, avoiding FD reuse while ioctl runs.
            if self.listener is not None:
                os.close(self.listener)
                self.listener = None

    def snapshot(self):
        with self.lock:
            counts = dict(self.counts)
            errno_counts, recent_requests = dict(self.errno_counts), list(self.recent_requests)
        return {'allocation': sorted(self.allowed), 'syscall': 'sched_setaffinity',
                'policy': 'requested mask intersect allocation; empty -> EINVAL',
                'other_syscalls': 'allowed', 'pointer_syscall_continue': False,
                'counts': counts, 'errno_counts': errno_counts, 'recent_requests': recent_requests,
                'listener_failure': self.failure,
                'listener_alive': bool(self.thread and self.thread.is_alive()),
                'limitation': 'Ordinary benchmark resource allocation; scheduler audits actual affinity; not malicious filter/ptrace tamper-proof.'}

    def close(self):
        if self.closed: return self.snapshot()
        self.closed = True
        self.stop.set()
        try: os.write(self.wake_write, b'x')
        except OSError: pass
        if self.thread:
            self.thread.join(timeout=1)
        elif self.listener is not None:
            os.close(self.listener); self.listener = None
        self.channel.close(); self.child_channel.close()
        if not self.thread or not self.thread.is_alive():
            os.close(self.wake_read); os.close(self.wake_write)
        else:
            # Closing/reaping the owned namespace is mandatory before close().
            # A lingering thread is reported, never silently labelled stopped.
            self.failure = self.failure or 'listener_did_not_stop_within_one_second'
        return self.snapshot()
