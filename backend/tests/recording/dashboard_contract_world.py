"""The isolated world the Dashboard contract is generated in.

Everything the dashboard touches is replaced by a controllable stand-in, so
backend/tests/unit/test_dashboard_contract.py can drive the REAL Flask routes of
backend/dashboard.py and publish what they answer:

  * the backend root (config, profile, wizard state, ledger, device leases) is a
    fresh temporary directory per scenario;
  * time is one fixed clock for the ledger modules and the dashboard;
  * the operating system beneath psutil is a table the scenario fills (pids, creation
    times, command lines), so the process scan, worker verification and the runner kill
    run their real code over it and nothing real is ever inspected or signalled;
  * the adb server and the emulator's TCP port are an in-memory socket, so every
    device_state is produced by the real adbclient parser;
  * process creation (Popen) records the command and registers a fake worker.

Nothing here reads the live ledger, a device, a lease, a process or a running dashboard.
"""
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import psutil

from harvest_support import NOW, Clock

ADB_SERVER = ('127.0.0.1', 5037)


class ContractClock(Clock):
    """The fixed clock; a sleep only moves it (nothing in a scenario waits)."""

    def sleep(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


# ------------------------------------------------------------ the process table
class ProcessTable:
    """The operating system beneath psutil: pid -> what owns it now.

    The platform layer of psutil.Process is replaced, so the real psutil.Process (its identity
    check, its terminate/reuse guard) runs over rows the scenario controls. `runner` rows are
    what runtime.runners.other_runners lists (a python process whose argv names a runner
    script). Nothing real is inspected: this test process is the only real pid and it stays
    readable."""

    def __init__(self, monkeypatch, root):
        self.rows, self.denied, self.refuse_kill, self.stubborn, self.signalled = {}, set(), set(), set(), []
        self.cmdline_denied = set()
        self.kernel = self._install_kernel(monkeypatch)
        self.root = str(root)
        self.mp = monkeypatch
        me = psutil.Process()
        self.start(os.getpid(), me.create_time(), ['python', 'pytest'])
        table = self

        class Platform:
            def __init__(self, pid):
                self.pid = pid

            def _check(self):
                if self.pid in table.denied:
                    raise psutil.AccessDenied(self.pid)
                if self.pid not in table.rows:
                    raise psutil.NoSuchProcess(self.pid)
                return table.rows[self.pid]

            def create_time(self, fast_only=False):
                return self._check()['created']

            def ppid(self):
                return self._check()['ppid']

            def cmdline(self):
                row = self._check()
                if self.pid in table.cmdline_denied:
                    raise psutil.AccessDenied(self.pid)   # the command line cannot be read: the scan row gets no identity
                return list(row['cmdline'])

            def cwd(self):
                self._check()
                return table.root

            def name(self):
                return Path(self._check()['cmdline'][0]).name

            def kill(self):
                row = self._check()
                if self.pid in table.refuse_kill:
                    raise psutil.AccessDenied(self.pid)
                table.signalled.append((self.pid, row['created']))
                if self.pid not in table.stubborn:
                    table.rows.pop(self.pid)

            def wait(self, timeout=None):
                if self.pid in table.stubborn:
                    raise psutil.TimeoutExpired(timeout, self.pid)
                return 0
        self.mp.setattr(psutil._psplatform, 'Process', Platform)
        self.mp.setattr(psutil, '_ppid_map', lambda: {})
        self.mp.setattr(psutil, 'wait_procs',
                        lambda family, timeout=None: (([], list(family)) if any(p.pid in table.stubborn for p in family)
                                                     else (list(family), [])))

    def _install_kernel(self, monkeypatch):
        """A tree whose destructive process actions go through runtime.process_handle's kernel32 boundary gets a
        fake kernel over this same table (the psutil layer above keeps serving the reads), so a stop or a kill
        signals nothing real. A tree without process_handle has nothing to install."""
        try:
            from harvest_support import install_kernel
            from runtime import process_handle
        except ImportError:
            return None
        os_ = install_kernel(monkeypatch)
        kernel, table = process_handle.KERNEL, self
        real_terminate = kernel.terminate

        def terminate(handle):
            if kernel._proc(handle).pid in table.refuse_kill:
                raise process_handle.KernelError('TerminateProcess', 5)   # ERROR_ACCESS_DENIED
            return real_terminate(handle)
        monkeypatch.setattr(kernel, 'terminate', terminate)
        return os_

    def make_stubborn(self, pid):
        """A signalled process does not exit."""
        self.stubborn.add(pid)
        if self.kernel is not None:
            self.kernel.lingering = True

    def refuse_termination(self, pid):
        """The process can be inspected but not terminated (access denied)."""
        self.refuse_kill.add(pid)

    def start(self, pid, created, cmdline=None, ppid=0, runner=False):
        self.rows[pid] = dict(created=created, cmdline=list(cmdline or ['python', 'worker']), ppid=ppid, runner=runner)
        if self.kernel is not None:
            self.kernel.start(pid, created, ppid)

    def swap(self, pid, created, **fields):
        """The pid changes hands: another process now owns it."""
        self.start(pid, created, **fields)

    def exit(self, pid):
        self.rows.pop(pid, None)
        if self.kernel is not None:
            self.kernel.exit(pid)

    def runners(self):
        """runtime.runners.other_runners, over the table: (pid, script basename, argv)."""
        from runtime.runners import RUNNER_SCRIPTS
        for pid, row in sorted(self.rows.items()):
            script = next((Path(a).name for a in row['cmdline'] if Path(a).name in RUNNER_SCRIPTS), None)
            if row['runner'] and script:
                yield pid, script, list(row['cmdline'])


class Spawner:
    """subprocess.Popen: records the command and registers a fake worker, starts nothing."""

    def __init__(self, table, clock):
        self.table, self.clock, self.calls, self.fail, self.pid = table, clock, [], None, 4242

    def __call__(self, cmd, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.calls.append([str(part) for part in cmd])
        pid = self.pid + len(self.calls) - 1
        self.table.start(pid, NOW - 60 + len(self.calls), cmd, runner=True)
        return SimpleNamespace(pid=pid)


# ------------------------------------------------------------------ the adb server
def okay(text):
    return b'OKAY' + b'%04x' % len(text) + text.encode()


def fail(text):
    return b'FAIL' + b'%04x' % len(text) + text.encode()


class FakeSocket:
    """One adb host-service conversation: the request goes in, the scripted reply comes out."""

    def __init__(self, answer):
        self.answer, self.buffer, self.closed = answer, b'', False

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.buffer += self.answer(data[4:].decode())

    def recv(self, n):
        chunk, self.buffer = self.buffer[:n], self.buffer[n:]
        return chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeAdb:
    """The adb server at 127.0.0.1:5037 and the emulator's TCP port, in memory.

    `state` is the device state the server reports for the configured serial: device,
    offline, unauthorized, missing, unknown (a reply that is not the protocol) or unconfigured
    is decided by the dashboard itself. `server` is None (answering), or an exception class the
    connect raises (ConnectionRefusedError, TimeoutError): body-identical `no_server`.
    `port` is whether the emulator's TCP port accepts a connection. `listing` is what
    host:devices lists."""

    def __init__(self, monkeypatch):
        self.state, self.server, self.port, self.listing = 'device', None, True, []
        self.requests = []
        monkeypatch.setattr(socket, 'create_connection', self.create_connection)

    def create_connection(self, address, timeout=None):
        if tuple(address) == ADB_SERVER:
            if self.server is not None:
                raise self.server('adb server unreachable')
            return FakeSocket(self.reply)
        if self.port:
            return FakeSocket(lambda payload: b'')
        raise ConnectionRefusedError('emulator port closed')

    def reply(self, payload):
        self.requests.append(payload)
        if payload.endswith(':get-state'):
            serial = payload.split(':', 1)[1].rsplit(':get-state', 1)[0]
            if self.state == 'device' or self.state == 'offline':
                return okay(self.state)
            if self.state == 'unauthorized':
                return fail('device unauthorized.\nThis adb server\'s $ADB_VENDOR_KEYS is not set')
            if self.state == 'missing':
                return fail(f"device '{serial}' not found")
            return b'JUNK'
        if payload == 'host:devices':
            return okay(''.join(f'{serial}\tdevice\n' for serial in self.listing))
        if payload.startswith('host:connect:'):
            return okay('connected to ' + payload.split(':', 2)[2])
        return fail('unsupported request')


def completed(stdout='', stderr='', returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)
