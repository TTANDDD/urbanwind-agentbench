"""Native Linux stdlib process tests, no model API, runtime or CFD required."""
import base64
import json
import os
import shlex
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

if sys.platform.startswith("linux"):
    from urbanwind_harness import open_exec_server as server
else:
    server = None


def python_command(code):
    return shlex.quote(sys.executable) + " -c " + shlex.quote(code)


def alive(pid):
    try:
        fields = Path("/proc/" + str(pid) + "/stat").read_text().split()
        return fields[2] != "Z"
    except FileNotFoundError:
        return False


class Reader:
    def __init__(self, connection):
        self.connection, self.pending = connection, b""
        self.connection.settimeout(0.2)

    def one(self, timeout=5):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            if time.monotonic() >= deadline:
                raise TimeoutError("No execution event")
            try:
                part = self.connection.recv(65536)
            except socket.timeout:
                continue
            if not part:
                raise EOFError("Server disconnected")
            self.pending += part
        line, self.pending = self.pending.split(b"\n", 1)
        return json.loads(line)

    def terminal(self, timeout=5):
        events = []
        while True:
            value = self.one(timeout)
            events.append(value)
            if value["event"] in ("done", "error"):
                return events


@unittest.skipUnless(server is not None, "Native server tests require Linux")
class OpenExecServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="urbanwind-server-test-")
        self.root = Path(self.temporary.name)
        self.groups, self.connections, self.threads = [], [], []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        for pgid in self.groups:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for thread in self.threads:
            thread.join(timeout=4)
        self.temporary.cleanup()

    def start(self, command, timeout=10, initial_control=None):
        guest, client = socket.socketpair()
        guest.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        errors = []
        request = {"command": command, "prefix": "", "cwd": str(self.root),
                   "timeout": timeout, "capture_dir": str(self.root / ("capture-" + str(len(self.threads))))}
        if initial_control is not None:
            request["initial_control"] = initial_control
        def run():
            try:
                server.execute(guest, request)
            except BaseException as exc:
                errors.append(exc)
            finally:
                guest.close()
        thread = threading.Thread(target=run, daemon=True)
        self.connections.append(client)
        self.threads.append(thread)
        thread.start()
        reader = Reader(client)
        started = reader.one()
        self.assertEqual(started["event"], "started")
        self.groups.append(started["guest_pid"])
        return client, reader, thread, Path(started["capture_dir"]), errors

    def wait_child(self, path):
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.exists())
        return int(path.read_text())

    def assert_process_stopped(self, pid, timeout=1):
        # killpg(SIGKILL) is asynchronous. The already-reaped shell can finish
        # before a same-PG child is scheduled to complete its pending SIGKILL.
        # Wait for actual death (or zombie) under a hard bound; a live leak still
        # fails, with its state/group/pending signals retained in the message.
        deadline = time.monotonic() + timeout
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(.005)
        diagnostic = ""
        if alive(pid):
            try:
                diagnostic = (Path('/proc/' + str(pid) + '/stat').read_text() +
                              Path('/proc/' + str(pid) + '/status').read_text())
            except OSError:
                pass
        self.assertFalse(alive(pid), "Owned child still alive after bounded cleanup: " + diagnostic)

    def child_command(self, path, background=True):
        code = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(str(path)) + ").write_text(str(os.getpid()));time.sleep(60)"
        command = python_command(code)
        return command + " & wait" if background else command

    def test_full_stdout_and_stderr_match_original_disk_captures(self):
        out = bytes(range(256)) * 4000
        err = b"stderr original \x00\xff\n" * 12000
        code = "import sys;sys.stdout.buffer.write(bytes(range(256))*4000);sys.stdout.buffer.flush();sys.stderr.buffer.write(b'stderr original \\x00\\xff\\n'*12000);sys.stderr.buffer.flush()"
        _, reader, thread, capture, errors = self.start(python_command(code))
        events = reader.terminal()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        received_out = b"".join(base64.b64decode(item["base64"]) for item in events if item["event"] == "stdout")
        received_err = b"".join(base64.b64decode(item["base64"]) for item in events if item["event"] == "stderr")
        self.assertEqual(received_out, out)
        self.assertEqual(received_err, err)
        self.assertEqual((capture / "stdout.txt").read_bytes(), out)
        self.assertEqual((capture / "stderr.txt").read_bytes(), err)
        self.assertEqual(events[-1]["exit_code"], 0)
        self.assertFalse(events[-1]["timed_out"])

    def test_cancel_is_processed_while_client_stops_reading_flooded_output(self):
        pidfile = self.root / "flood.pid"
        code = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));\nwhile True: os.write(1,b'x'*65536);time.sleep(.001)"
        client, _, thread, capture, errors = self.start(python_command(code), timeout=20)
        child = self.wait_child(pidfile)
        time.sleep(.15)
        client.sendall(b'{"cancel":true}\n')
        began = time.monotonic()
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive(), "Output backpressure must not block cancel")
        self.assertLess(time.monotonic() - began, 4)
        self.assert_process_stopped(child)
        self.assertEqual(errors, [])
        result = json.loads((capture / "execution.json").read_text())
        self.assertTrue(result["cancelled"])
        self.assertGreater((capture / "stdout.txt").stat().st_size, 65536)
        self.assertEqual(result["captured_bytes"]["stdout"], (capture / "stdout.txt").stat().st_size)

    def test_guest_deadline_remains_live_under_output_backpressure(self):
        pidfile = self.root / "deadline.pid"
        code = "import os,signal;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(" + repr(str(pidfile)) + ").write_text(str(os.getpid()));\nwhile True: os.write(1,b'y'*65536)"
        _, _, thread, capture, errors = self.start(python_command(code), timeout=.25)
        child = self.wait_child(pidfile)
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive(), "Output backpressure must not block deadline")
        self.assert_process_stopped(child)
        self.assertEqual(errors, [])
        result = json.loads((capture / "execution.json").read_text())
        self.assertTrue(result["timed_out"])
        self.assertGreater((capture / "stdout.txt").stat().st_size, 0)

    def test_malformed_control_exception_kills_same_group_term_ignoring_child(self):
        pidfile = self.root / "malformed.pid"
        client, reader, thread, capture, errors = self.start(self.child_command(pidfile))
        child = self.wait_child(pidfile)
        self.assertNotEqual(child, self.groups[-1])
        client.sendall(b'{not-valid-json}\n')
        events = reader.terminal()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assert_process_stopped(child)
        self.assertEqual(errors, [])
        self.assertEqual(events[-1]["event"], "error")
        self.assertEqual(events[-1]["type"], "JSONDecodeError")
        self.assertTrue((capture / "execution.json").is_file())

    def test_disconnect_kills_same_group_term_ignoring_child(self):
        pidfile = self.root / "disconnect.pid"
        client, _, thread, capture, errors = self.start(self.child_command(pidfile))
        child = self.wait_child(pidfile)
        client.close()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assert_process_stopped(child)
        self.assertEqual(errors, [])
        self.assertTrue(json.loads((capture / "execution.json").read_text())["cancelled"])

    def test_successful_foreground_exit_keeps_redirected_background_alive(self):
        pidfile = self.root / "background.pid"
        command = self.child_command(pidfile, background=False) + " >background.out 2>background.err &"
        _, reader, thread, _, errors = self.start(command)
        events = reader.terminal()
        thread.join(timeout=1)
        child = self.wait_child(pidfile)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(events[-1]["exit_code"], 0)
        self.assertTrue(alive(child))
        self.assertEqual(os.getpgid(child), self.groups[-1])

    def test_cancel_during_queued_output_delivery_cleans_remaining_background(self):
        pidfile = self.root / "delivery-background.pid"
        command = self.child_command(pidfile, background=False) + " >background.out 2>background.err &\n"
        command += python_command("import os;os.write(1,b'q'*(2*1024*1024))")
        client, _, thread, capture, errors = self.start(command)
        child = self.wait_child(pidfile)
        deadline = time.monotonic() + 1
        while not (capture / "execution.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((capture / "execution.json").exists(), "Foreground should have exited before this cancel")
        client.sendall(b'{"cancel":true}\n')
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assert_process_stopped(child)
        self.assertEqual(errors, [])
        result = json.loads((capture / "execution.json").read_text())
        self.assertTrue(result["cancelled"])
        self.assertEqual((capture / "stdout.txt").stat().st_size, 2*1024*1024)

    def test_bounded_death_assertion_rejects_an_actual_live_background(self):
        pidfile = self.root / "live-assertion-counterexample.pid"
        command = self.child_command(pidfile, background=False) + " >background.out 2>background.err &"
        _, reader, thread, _, errors = self.start(command)
        reader.terminal()
        thread.join(timeout=1)
        child = self.wait_child(pidfile)
        self.assertEqual(errors, [])
        with self.assertRaises(AssertionError):
            self.assert_process_stopped(child, timeout=.05)

    def test_continuous_background_pipe_drain_is_bounded(self):
        code = "import os;\nwhile True: os.write(1,b'z'*65536)"
        _, reader, thread, _, errors = self.start(python_command(code) + " &")
        events = reader.terminal()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(events[-1]["background_pipe_drain_limited"])
        self.assertLess(events[-1]["wall_seconds"], 1.0)

    def test_cancel_coalesced_with_request_is_not_lost(self):
        _, reader, thread, _, errors = self.start("sleep 30", initial_control='{"cancel":true}\n')
        events = reader.terminal()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(events[-1]["cancelled"])


if __name__ == "__main__":
    unittest.main()
