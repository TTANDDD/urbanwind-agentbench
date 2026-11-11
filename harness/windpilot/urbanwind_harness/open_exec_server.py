"""Credential-free execution in one persistent task namespace.

Output events are disk-spooled and sent nonblocking, so a slow receiver cannot
prevent deadlines or cancellation. Raw captures survive a disconnected client.
"""
from __future__ import annotations
import base64
import json
import math
import os
import resource
import selectors
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path


TERM_GRACE_SECONDS = 0.75
POST_EXIT_DRAIN_SECONDS = 0.2
POST_EXIT_DRAIN_BYTES = 1024 * 1024
DELIVERY_GRACE_SECONDS = 2.0
CHUNK_BYTES = 65536


class Outbox:
    """Ordered disk-backed event queue with constant memory consumption."""
    def __init__(self):
        self.file = tempfile.TemporaryFile(mode="w+b")
        self.written = self.sent = 0

    @property
    def pending(self):
        return self.written > self.sent

    def enqueue(self, value):
        encoded = (json.dumps(value, allow_nan=False) + "\n").encode()
        self.file.seek(self.written)
        self.file.write(encoded)
        self.file.flush()
        self.written += len(encoded)

    def flush(self, connection):
        if not self.pending:
            return
        self.file.seek(self.sent)
        block = self.file.read(min(CHUNK_BYTES, self.written - self.sent))
        try:
            count = connection.send(block)
        except (BlockingIOError, InterruptedError):
            return
        if not count:
            raise ConnectionError("Execution client stopped receiving")
        self.sent += count

    def close(self):
        self.file.close()


def send(connection, value, timeout=DELIVERY_GRACE_SECONDS):
    """Bounded control reply outside execution; never blocking sendall."""
    connection.setblocking(False)
    outbox = Outbox()
    selector = selectors.DefaultSelector()
    try:
        outbox.enqueue(value)
        selector.register(connection, selectors.EVENT_WRITE)
        deadline = time.monotonic() + timeout
        while outbox.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if selector.select(min(0.1, remaining)):
                outbox.flush(connection)
        return True
    finally:
        selector.close()
        outbox.close()


def _signal_group(process, number):
    # The command starts a new session: this PG belongs only to that command.
    try:
        os.killpg(process.pid, number)
    except ProcessLookupError:
        pass


def terminate(process, grace=TERM_GRACE_SECONDS):
    """TERM then KILL the owned PG, even if the initial shell already exited."""
    _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + max(0, grace)
    while time.monotonic() < deadline:
        time.sleep(min(0.05, deadline - time.monotonic()))
    # Waiting only for the shell would leave siblings which ignore TERM.
    _signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait(timeout=1)


def execute(connection, request):
    timeout = float(request["timeout"])
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Command timeout must be finite and positive")
    prefix, command = request["prefix"], request["command"]
    if not isinstance(prefix, str) or not isinstance(command, str):
        raise TypeError("Command and runtime prefix must be strings")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    if request.get("capture_dir"):
        capture_dir = Path(request["capture_dir"])
        capture_dir.mkdir(parents=True, exist_ok=False)
    else:
        capture_dir = Path(tempfile.mkdtemp(prefix="urbanwind-exec-"))
    # Only this command's public streams are captured; no private state is read.
    # Unbuffered files retain every captured block even when the host must
    # forcibly close the task namespace before this service can finish.
    captures = {name: (capture_dir / (name + ".txt")).open("wb", buffering=0) for name in ("stdout", "stderr")}
    counts = {"stdout": 0, "stderr": 0}
    process = None
    selector, outbox = selectors.DefaultSelector(), Outbox()
    connected = True
    timed_out = cancelled = drain_limited = False
    control = bytearray(request.get("initial_control", "").encode())
    open_streams = {}
    termination_started = foreground_exited_at = completed_at = None
    group_killed = False
    post_exit_bytes = 0
    outcome = None

    def connection_interest():
        if connected:
            selector.modify(connection, selectors.EVENT_READ | (selectors.EVENT_WRITE if outbox.pending else 0), "control")

    def stop_group():
        nonlocal termination_started
        if termination_started is None:
            termination_started = time.monotonic()
            _signal_group(process, signal.SIGTERM)

    def close_stream(stream):
        open_streams.pop(stream, None)
        try:
            selector.unregister(stream)
        except KeyError:
            pass
        stream.close()

    def capture(stream, name):
        nonlocal post_exit_bytes
        try:
            block = os.read(stream.fileno(), CHUNK_BYTES)
        except (BlockingIOError, InterruptedError):
            return
        if not block:
            close_stream(stream)
            return
        captures[name].write(block)
        counts[name] += len(block)
        if foreground_exited_at is not None:
            post_exit_bytes += len(block)
        if connected:
            outbox.enqueue({"event": name, "base64": base64.b64encode(block).decode()})

    def consume_control():
        nonlocal control, cancelled
        while b"\n" in control:
            line, remainder = bytes(control).split(b"\n", 1)
            control = bytearray(remainder)
            if json.loads(line).get("cancel"):
                cancelled = True
                stop_group()

    def disconnect():
        nonlocal connected, cancelled
        connected, cancelled = False, True
        try:
            selector.unregister(connection)
        except KeyError:
            pass
        stop_group()

    def cpu_used():
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        return after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime

    def save_outcome(value):
        (capture_dir / "execution.json").write_text(json.dumps(value, allow_nan=False) + "\n")

    try:
        process = subprocess.Popen(["/bin/bash", "-c", prefix + command],
            cwd=request.get("cwd") or "/home/user/Desktop", stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        connection.setblocking(False)
        selector.register(connection, selectors.EVENT_READ, "control")
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
            open_streams[stream] = name
        outbox.enqueue({"event": "started", "guest_pid": process.pid, "capture_dir": str(capture_dir)})
        consume_control()
        while outcome is None:
            current = time.monotonic()
            if termination_started is None and foreground_exited_at is None and current - started >= timeout:
                timed_out = True
                stop_group()
            if termination_started is not None and not group_killed and current - termination_started >= TERM_GRACE_SECONDS:
                _signal_group(process, signal.SIGKILL)
                group_killed = True
            code = process.poll()
            if code is not None and foreground_exited_at is None:
                foreground_exited_at = current
            if foreground_exited_at is not None and termination_started is None:
                if current - foreground_exited_at >= POST_EXIT_DRAIN_SECONDS or post_exit_bytes >= POST_EXIT_DRAIN_BYTES:
                    drain_limited = bool(open_streams)
                    for stream in list(open_streams):
                        close_stream(stream)
            # Detached children can hold pipes after this PG has been killed.
            # The enclosing task namespace owns cleanup of residual backgrounds.
            if group_killed and current - termination_started >= TERM_GRACE_SECONDS + POST_EXIT_DRAIN_SECONDS:
                drain_limited |= bool(open_streams)
                for stream in list(open_streams):
                    close_stream(stream)
            if code is not None and not open_streams and (termination_started is None or group_killed):
                outcome = {"event": "done", "exit_code": code, "timed_out": timed_out,
                    "cancelled": cancelled, "reaped_cpu_seconds": cpu_used(), "wall_seconds": current - started,
                    "capture_dir": str(capture_dir), "captured_bytes": counts,
                    "background_pipe_drain_limited": drain_limited,
                    "background_stream_scope": "Background jobs should redirect stdout/stderr; capture stops shortly after foreground exit."}
                save_outcome(outcome)
                if connected:
                    outbox.enqueue(outcome)
                completed_at = current
                break
            connection_interest()
            for key, mask in selector.select(0.05):
                if key.data == "control":
                    if mask & selectors.EVENT_READ:
                        try:
                            block = connection.recv(CHUNK_BYTES)
                        except (BlockingIOError, InterruptedError):
                            block = None
                        if block == b"":
                            disconnect()
                        elif block:
                            control.extend(block)
                            consume_control()
                    if connected and mask & selectors.EVENT_WRITE:
                        try:
                            outbox.flush(connection)
                        except (BrokenPipeError, ConnectionError, ConnectionResetError):
                            disconnect()
                else:
                    capture(key.fileobj, key.data)
    except Exception as exc:
        error = {"event": "error", "type": type(exc).__name__, "message": str(exc), "capture_dir": str(capture_dir)}
        if process is not None:
            terminate(process)
            group_killed = True
        deadline, read_bytes = time.monotonic() + POST_EXIT_DRAIN_SECONDS, 0
        for stream, name in list(open_streams.items()):
            while time.monotonic() < deadline and read_bytes < POST_EXIT_DRAIN_BYTES:
                previous = counts[name]
                try:
                    capture(stream, name)
                except (OSError, ValueError):
                    break
                delta = counts[name] - previous
                read_bytes += delta
                if not delta:
                    break
            if stream in open_streams:
                close_stream(stream)
        save_outcome({**error, "captured_bytes": counts, "timed_out": timed_out, "cancelled": cancelled,
                      "reaped_cpu_seconds": cpu_used(), "wall_seconds": time.monotonic() - started})
        if connected:
            try:
                outbox.enqueue(error)
            except OSError:
                connected = False
        completed_at = time.monotonic()
    finally:
        if process is not None and outcome is None and not group_killed:
            terminate(process)
        for stream in list(open_streams):
            close_stream(stream)
        for stream in captures.values():
            stream.flush()
            stream.close()
        # Deliver complete queued streams in order, but do not wait indefinitely
        # for a permanently stalled client. Original captures stay on disk.
        try:
            if connected:
                connection_interest()
                deadline = (completed_at or time.monotonic()) + DELIVERY_GRACE_SECONDS
                while connected and outbox.pending and time.monotonic() < deadline:
                    for key, mask in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                        if key.data == "control":
                            if mask & selectors.EVENT_READ:
                                try:
                                    block = connection.recv(CHUNK_BYTES)
                                except (BlockingIOError, InterruptedError):
                                    block = None
                                if block == b"":
                                    disconnect()
                                elif block:
                                    control.extend(block)
                                    consume_control()
                            if connected and mask & selectors.EVENT_WRITE:
                                outbox.flush(connection)
                    if cancelled and process is not None and not group_killed:
                        terminate(process)
                        group_killed = True
                        if outcome is not None:
                            outcome = {**outcome, "cancelled": True,
                                       "reaped_cpu_seconds": cpu_used()}
                            save_outcome(outcome)
                    connection_interest()
                if outbox.pending and process is not None:
                    if not group_killed:
                        terminate(process)
                        group_killed = True
                    save_outcome({**(outcome or {}), "delivery_incomplete": True,
                                  "cancelled": cancelled, "reaped_cpu_seconds": cpu_used(),
                                  "captured_bytes": counts, "capture_dir": str(capture_dir)})
        except Exception as exc:
            if process is not None:
                terminate(process)
            save_outcome({**(outcome or {}), "delivery_incomplete": True,
                          "delivery_error_type": type(exc).__name__,
                          "cancelled": cancelled, "reaped_cpu_seconds": cpu_used(),
                          "captured_bytes": counts, "capture_dir": str(capture_dir)})
        finally:
            selector.close()
            outbox.close()


def _read_request(connection, timeout=30):
    connection.settimeout(timeout)
    data = bytearray()
    while b"\n" not in data:
        block = connection.recv(CHUNK_BYTES)
        if not block:
            raise EOFError("Client disconnected before request")
        data.extend(block)
    line, trailing = bytes(data).split(b"\n", 1)
    return json.loads(line), trailing


def main():
    path = Path("/run/urbanwind-exec.sock")
    if path.exists():
        path.unlink()
    service = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    service.bind(str(path))
    service.listen(1)
    try:
        while True:
            connection, _ = service.accept()
            try:
                value, trailing = _read_request(connection)
                if value.get("operation") == "close":
                    send(connection, {"event": "closed"})
                    return
                if trailing:
                    value["initial_control"] = trailing.decode()
                execute(connection, value)
            except Exception as exc:
                try:
                    send(connection, {"event": "error", "type": type(exc).__name__, "message": str(exc)})
                except OSError:
                    pass
            finally:
                connection.close()
    finally:
        service.close()


if __name__ == "__main__":
    main()
