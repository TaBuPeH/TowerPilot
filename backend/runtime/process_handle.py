"""Destroying a process is bound to a kernel handle, never to a pid.

Why psutil is not enough. `psutil.Process.terminate()` re-checks (pid, creation time)
and then calls the platform kill, which on Windows is `TerminateProcess` on a handle it
opens BY PID at that moment. A pid that passes to another process after the last
creation-time read is therefore killed in its place. The same hole is in a family:
`children(recursive=True)` names children from a parent-pid snapshot and builds the
objects afterwards, and a child pid reused in between becomes "a child".

The rule here is the kernel's own. An open process handle keeps its pid from being
reused for as long as it is held, so a process verified THROUGH a handle is the process
that is terminated through that same handle: there is no gap between the check and the
kill.

  * open_verified opens the pid ONCE (PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
    | SYNCHRONIZE), reads the creation time of THAT handle (GetProcessTimes) and compares
    it with the recorded one. A different creation time, an exited process or no process at
    all is PROVEN gone; anything the kernel will not tell (access denied, any other error,
    an unusable identity) is UNKNOWN. Nothing is signalled on either.
  * a family is only ever walked from a handle that is already held. A candidate named by
    the parent-pid snapshot is opened, and ON ITS OWN HANDLE its parent pid
    (NtQueryInformationProcess, InheritedFromUniqueProcessId) must be the verified parent's
    and its creation time must not be earlier than the parent's: a process that was started
    before the parent existed cannot be its child, whatever its recorded parent number says.
    A candidate that fails is skipped and reported, never signalled. A held handle pins
    every member's pid, dead or alive, so the relationships stay valid while the family is
    stopped, which also lets a second pass catch a child spawned meanwhile.
  * TerminateProcess and the wait are called on the held handles. psutil stays what it is
    good for: read-only questions (is it alive, what is its command line).

Why handles and not a Job Object. A Job Object assigned at spawn would cover exactly the
family atomically, but the workers are launched in their own console so that they outlive the
dashboard: a job the dashboard owns would have to be named and re-opened by whichever
dashboard stops the worker (two hosts share one ledger), its name would have to be recorded
in the operation (a ledger change), workers started before this code and adopted runners
would still need the handle-bound path, and a job that kills on close would end every
worker with the dashboard. One mechanism that covers all of them, with no new persisted
state, meets the guarantee that matters (only the verified family is ever terminated).

Windows only. Elsewhere there is no kernel to ask, so nothing is verified and nothing is
terminated (every call answers unverified)."""
import ctypes
import os
import time
from dataclasses import dataclass, field

from runtime import process_identity

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
ACCESS = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE

ERROR_ACCESS_DENIED = 5
ERROR_NO_MORE_FILES = 18       # the normal end of a Toolhelp process listing; any other failure is not an end
ERROR_INVALID_PARAMETER = 87   # OpenProcess on a pid no process has
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
STOP_TIMEOUT = 3.0             # seconds, one shared deadline for the whole family
MAX_PASSES = 5                 # signal -> wait -> look again: a family that is still growing after this is not settled
_EPOCH_AS_FILETIME_SEC = 11644473600   # seconds between 1601-01-01 and 1970-01-01


class KernelError(OSError):
    """A kernel call failed. `code` is the Win32 error (or the NTSTATUS for ntdll)."""

    def __init__(self, op, code):
        super().__init__(f'{op} failed with code {code}')
        self.op, self.code = op, code

    @property
    def absent(self):
        """OpenProcess found no process with that pid."""
        return self.op == 'OpenProcess' and self.code == ERROR_INVALID_PARAMETER

    @property
    def denied(self):
        return self.code == ERROR_ACCESS_DENIED


# Everything a kernel call (or the arguments it is given) can raise. ctypes.ArgumentError is NOT an OSError or a
# ValueError: a call whose arguments ctypes cannot convert raises it AFTER OpenProcess has handed out a handle, so
# every path that holds a handle must treat it like any other kernel failure (unverified, handle closed).
KERNEL_ERRORS = (KernelError, OSError, ValueError, OverflowError, TypeError, ctypes.ArgumentError)


class Win32Kernel:
    """The kernel32/ntdll calls this module needs and nothing else; every other line here is
    policy. Tests replace the whole object with a fake of the same methods."""

    def __init__(self):
        from ctypes import wintypes
        self._w = wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        nt = ctypes.WinDLL('ntdll')
        handle, dword, bool_ = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
        k32.OpenProcess.argtypes, k32.OpenProcess.restype = (dword, bool_, dword), handle
        k32.CloseHandle.argtypes, k32.CloseHandle.restype = (handle,), bool_
        k32.TerminateProcess.argtypes, k32.TerminateProcess.restype = (handle, wintypes.UINT), bool_
        k32.WaitForSingleObject.argtypes, k32.WaitForSingleObject.restype = (handle, dword), dword
        k32.GetProcessTimes.argtypes = (handle,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k32.GetProcessTimes.restype = bool_
        k32.CreateToolhelp32Snapshot.argtypes, k32.CreateToolhelp32Snapshot.restype = (dword, dword), handle
        k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = (handle, ctypes.c_void_p)
        k32.Process32FirstW.restype = k32.Process32NextW.restype = bool_
        nt.NtQueryInformationProcess.argtypes = (handle, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
        nt.NtQueryInformationProcess.restype = ctypes.c_long
        self._k32, self._nt = k32, nt

        class ProcessBasicInformation(ctypes.Structure):
            _fields_ = [('ExitStatus', ctypes.c_void_p), ('PebBaseAddress', ctypes.c_void_p),
                        ('AffinityMask', ctypes.c_void_p), ('BasePriority', ctypes.c_void_p),
                        ('UniqueProcessId', ctypes.c_void_p), ('InheritedFromUniqueProcessId', ctypes.c_void_p)]

        class ProcessEntry(ctypes.Structure):
            _fields_ = [('dwSize', dword), ('cntUsage', dword), ('th32ProcessID', dword),
                        ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', dword), ('cntThreads', dword),
                        ('th32ParentProcessID', dword), ('pcPriClassBase', wintypes.LONG), ('dwFlags', dword),
                        ('szExeFile', wintypes.WCHAR * 260)]
        self._info, self._entry = ProcessBasicInformation, ProcessEntry

    def _fail(self, op):
        return KernelError(op, ctypes.get_last_error())

    def open(self, pid):
        handle = self._k32.OpenProcess(ACCESS, False, pid)
        if not handle:
            raise self._fail('OpenProcess')
        return handle

    def created(self, handle):
        """Creation time of the process behind `handle`, in epoch seconds (what psutil reports)."""
        times = [self._w.FILETIME() for _ in range(4)]
        if not self._k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise self._fail('GetProcessTimes')
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return ticks / 10_000_000 - _EPOCH_AS_FILETIME_SEC

    def parent_pid(self, handle):
        info = self._info()
        status = self._nt.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), None)
        if status != 0:
            raise KernelError('NtQueryInformationProcess', status & 0xFFFFFFFF)
        return int(info.InheritedFromUniqueProcessId or 0)

    def wait(self, handle, timeout_ms):
        """True when the process has exited, False when it is still running after timeout_ms."""
        result = self._k32.WaitForSingleObject(handle, int(timeout_ms))
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise self._fail('WaitForSingleObject')

    def terminate(self, handle):
        if not self._k32.TerminateProcess(handle, 1):
            raise self._fail('TerminateProcess')

    def close(self, handle):
        self._k32.CloseHandle(handle)

    def parents(self):
        """{pid: parent pid} of every process at this instant: only CANDIDATES for a family,
        each of which is verified again on its own handle before anything is done to it."""
        ctypes.set_last_error(0)
        snapshot = self._k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise self._fail('CreateToolhelp32Snapshot')
        table = {}
        try:
            entry = self._entry()
            entry.dwSize = ctypes.sizeof(entry)
            ctypes.set_last_error(0)
            more = self._k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[entry.th32ProcessID] = entry.th32ParentProcessID
                ctypes.set_last_error(0)
                more = self._k32.Process32NextW(snapshot, ctypes.byref(entry))
            # The listing ends when a call returns FALSE. ERROR_NO_MORE_FILES is the only normal end; any
            # other error means the listing stopped part-way (or never started), so the table is NOT the
            # family and must not be used as if it were.
            code = ctypes.get_last_error()
            if code != ERROR_NO_MORE_FILES:
                raise KernelError('Process32NextW' if table else 'Process32FirstW', code)
        finally:
            self._k32.CloseHandle(snapshot)   # on every path
        return table


class NoKernel:
    """Where there is no Windows kernel to ask: nothing can be verified, so nothing is done."""

    def _no(self, *args, **kwargs):
        raise KernelError('OpenProcess', ERROR_ACCESS_DENIED)

    open = created = parent_pid = wait = terminate = close = parents = _no


KERNEL = None   # the Win32Kernel, created on first use; tests install a fake here


def kernel():
    global KERNEL
    if KERNEL is None:
        KERNEL = Win32Kernel() if os.name == 'nt' else NoKernel()
    return KERNEL


class Held:
    """A process the kernel is keeping for us: its pid cannot be reused while `handle` is open."""
    __slots__ = ('pid', 'handle', 'created', '_kernel')

    def __init__(self, kern, pid, handle, created):
        self._kernel, self.pid, self.handle, self.created = kern, pid, handle, created

    def close(self):
        handle, self.handle = self.handle, None
        if handle is not None:
            try:
                self._kernel.close(handle)
            except Exception:   # noqa: BLE001 - nothing useful to do about a handle that will not close
                pass


@dataclass
class Stop:
    """What happened to one stop request.

    state  gone        PROVEN gone: the recorded process is not there (no such process, another process
                       owns the pid, it had already exited). Nothing was signalled. It says nothing about
                       descendants it left behind: with the parent gone its pid is no longer pinned, so
                       they cannot be attributed to it. The device lease is the check for those.
           unverified  the process (or its family) cannot be proven: its identity is not the recorded one,
                       or the family could not be listed (reason family_enumeration_failed), or it was still
                       growing after MAX_PASSES (reason family_not_settled). Not a statement that it is gone,
                       and never `stopped`. `signalled` says what had been done before it was known.
           stopped     every verified member was signalled, has exited, and a last look found no new member.
           stopping    something was signalled and a member has not exited, could not be signalled, or a
                       candidate could not be read: the stop is not confirmed.
    reason             why the state is unverified: family_enumeration_failed | family_not_settled | None.
    signalled          the pids TerminateProcess was called on, each through its own verified handle.
    skipped            candidates that failed verification and were never signalled: {pid, reason}.
    errors             what the kernel refused: {pid, step, code}.
    survivors          verified members that are still running when the stop ends (signalled and not yet
                       exited, or found too late to be signalled): the pids a person has to look at.
    incomplete         a candidate could not be opened or read, so the family may be larger than the
                       members that were verified (the stop is then never `stopped`)."""
    state: str = 'stopping'
    reason: str = None
    signalled: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    survivors: list = field(default_factory=list)
    incomplete: bool = False

    @property
    def confirmed(self):
        return self.state in ('gone', 'stopped')


def open_verified(kern, pid, created):
    """(answer, Held|None, error|None): True with the Held process, False (PROVEN gone) or None
    (UNKNOWN, with the KernelError when the kernel gave one). The pid is opened once."""
    if not process_identity.usable(pid, created):
        return None, None, None
    try:
        handle = kern.open(pid)
    except KernelError as exc:
        return (False if exc.absent else None), None, exc
    except KERNEL_ERRORS as exc:
        return None, None, exc
    held, keep = Held(kern, pid, handle, None), False
    try:
        held.created = kern.created(handle)
        if not process_identity.same_created(held.created, created):
            return False, None, None   # another process owns this pid now: the recorded one is gone
        if kern.wait(handle, 0):
            return False, None, None   # it exited (its handle is the last thing that names it)
        keep = True
        return True, held, None
    except KERNEL_ERRORS as exc:
        return None, None, exc
    finally:
        if not keep:   # every way out but the verified one, including an error nobody expected
            held.close()


def _code(exc):
    return getattr(exc, 'code', type(exc).__name__)


def _verified_child(kern, parent, pid, stop, rejected):
    """The Held child when `pid` really is a descendant of `parent` (checked on the child's own
    handle), else None. Failures are recorded on `stop`; only an unreadable candidate leaves the
    family unconfirmed, a process that is plainly not a child is just skipped."""
    try:
        handle = kern.open(pid)
    except KernelError as exc:
        if exc.absent:
            return None   # it exited before it could be opened: nothing of it left to stop
        _skip(stop, rejected, pid, 'access_denied' if exc.denied else 'open_failed', 'open', exc.code, incomplete=True)
        return None
    except KERNEL_ERRORS as exc:
        _skip(stop, rejected, pid, 'open_failed', 'open', _code(exc), incomplete=True)
        return None
    member, keep = Held(kern, pid, handle, None), False
    try:
        member.created = kern.created(handle)
        parent_pid = kern.parent_pid(handle)
        if parent_pid != parent.pid or not member.created >= parent.created:
            _skip(stop, rejected, pid, 'not_a_descendant')
            return None
        keep = True
        return member
    except KERNEL_ERRORS as exc:
        _skip(stop, rejected, pid, 'unreadable', 'verify', _code(exc), incomplete=True)
        return None
    finally:
        if not keep:   # the handle is closed on every path that does not hand the member back
            member.close()


def _skip(stop, seen, pid, reason, step=None, code=None, incomplete=False):
    """Record a candidate that was NOT signalled, once however many passes see it. `incomplete` marks a
    candidate that could not be judged (as opposed to one that was judged and is not a descendant)."""
    if (pid, reason) not in seen:
        seen.add((pid, reason))
        stop.skipped.append(dict(pid=pid, reason=reason))
        if step is not None:
            stop.errors.append(dict(pid=pid, step=step, code=code))
    if incomplete:
        stop.incomplete = True


def _collect(kern, members, stop, rejected):
    """Walk the family from the handles already held and append every verified descendant to
    `members` (parents before children). False when the parent table cannot be read: the family is then
    UNKNOWN (reason family_enumeration_failed), whatever else was done."""
    try:
        table = kern.parents()
    except KERNEL_ERRORS as exc:
        stop.errors.append(dict(pid=None, step='enumerate', code=_code(exc)))
        stop.reason = 'family_enumeration_failed'
        return False
    held = {member.pid for member in members}
    index = 0
    while index < len(members):
        parent = members[index]
        index += 1
        for pid in sorted(p for p, ppid in table.items() if ppid == parent.pid and p not in held and p != parent.pid):
            member = _verified_child(kern, parent, pid, stop, rejected)
            if member is not None:
                held.add(pid)
                members.append(member)
    return True


def _signal(kern, member, stop):
    """TerminateProcess through the member's own handle. False when it could not be done."""
    try:
        if kern.wait(member.handle, 0):
            return True   # already exited
        kern.terminate(member.handle)
        stop.signalled.append(member.pid)
        return True
    except KERNEL_ERRORS as exc:
        try:
            if kern.wait(member.handle, 0):
                return True   # it exited while we were calling: that is the outcome we wanted
        except KERNEL_ERRORS:
            pass
        stop.errors.append(dict(pid=member.pid, step='terminate', code=_code(exc)))
        return False


def _running(kern, members):
    """The pids of verified members that have not exited, read now. A member whose state cannot be read
    counts as running."""
    alive = []
    for member in members:
        try:
            if not kern.wait(member.handle, 0):
                alive.append(member.pid)
        except KERNEL_ERRORS:
            alive.append(member.pid)
    return alive


def _wait(kern, members, deadline, stop, *, record=True):
    """True when every member has exited by the shared deadline. record=False only waits (a grace
    period is not a failure)."""
    confirmed = True
    for member in members:
        remaining = max(0, int((deadline - time.monotonic()) * 1000))
        try:
            if not kern.wait(member.handle, remaining):
                if record:
                    stop.errors.append(dict(pid=member.pid, step='wait', code='still_running'))
                confirmed = False
        except KERNEL_ERRORS as exc:
            if record:
                stop.errors.append(dict(pid=member.pid, step='wait', code=_code(exc)))
            confirmed = False
    return confirmed


def creation_time(pid):
    """The creation time of the process that owns `pid` now, read from a handle opened just for this.
    ONLY for a process the caller itself spawned and still holds as a live subprocess.Popen: that
    object's handle keeps the pid from being reissued, so what owns it is the child. Anything found
    by a scan or read from a record has its own identity to compare (terminate_tree(pid, created)).
    Raises KernelError when the kernel will not say."""
    kern = kernel()
    handle = kern.open(pid)
    try:
        return kern.created(handle)
    finally:
        try:
            kern.close(handle)
        except Exception:   # noqa: BLE001
            pass


def terminate_owned(pid, **kwargs):
    """terminate_tree for a process the caller spawned and still holds as a Popen (see creation_time).
    A pid the kernel cannot be asked about is `unverified`: nothing is signalled."""
    try:
        created = creation_time(pid)
    except KernelError as exc:
        if exc.absent:
            return Stop('gone')
        return Stop('unverified', errors=[dict(pid=pid, step='open', code=exc.code)])
    except KERNEL_ERRORS as exc:
        return Stop('unverified', errors=[dict(pid=pid, step='open', code=_code(exc))])
    return terminate_tree(pid, created, **kwargs)


def terminate_tree(pid, created, *, descendants=True, timeout=STOP_TIMEOUT, grace=0.0):
    """Terminate the process (pid, created) and, with descendants, every verified descendant of it.
    Returns a Stop; see its docstring. Everything is done through handles opened here and closed
    before returning. `grace` first gives the verified members that many seconds to exit by
    themselves (a helper whose connection was just closed); only the ones still running are signalled
    after it, and `timeout` is then the time they get to exit."""
    kern = kernel()
    answer, root, error = open_verified(kern, pid, created)
    if answer is False:
        return Stop('gone')
    if answer is None:
        stop = Stop('unverified')
        if error is not None:
            stop.errors.append(dict(pid=pid, step='open', code=_code(error)))
        return stop
    stop, rejected, members = Stop('stopping'), set(), [root]
    try:
        if descendants and not _collect(kern, members, stop, rejected):
            stop.state = 'unverified'   # nothing was signalled yet: the family is unknown
            return stop
        if grace:
            _wait(kern, members, time.monotonic() + grace, stop, record=False)
        deadline = time.monotonic() + timeout
        confirmed, pending = True, list(members)
        for number in range(1, MAX_PASSES + 1):
            # signal -> wait -> look again, from the SAME held handles. Looking AFTER the wait matters: every
            # member has exited by then (or is named a survivor), so a child it spawned up to its last
            # instant is visible through its pinned pid. A child found now may itself spawn before it is
            # signalled, which is why this repeats until a look finds nothing new.
            for member in reversed(pending):   # children before the parent that would respawn them
                confirmed = _signal(kern, member, stop) and confirmed
            confirmed = _wait(kern, pending, deadline, stop) and confirmed
            if not descendants:
                pending = []
                break
            before = len(members)
            if not _collect(kern, members, stop, rejected):
                stop.state = 'unverified'   # the family cannot be listed: whatever was signalled, it is not known to be gone
                stop.survivors = _running(kern, members)
                return stop
            pending = members[before:]
            if not pending:
                break
        if pending:   # still growing after the last pass: members found but never signalled
            stop.state, stop.reason = 'unverified', 'family_not_settled'
            stop.survivors = _running(kern, members)
            return stop
        stop.survivors = _running(kern, members)   # the final verification: nothing verified may still be running
        stop.state = 'stopped' if confirmed and not stop.incomplete and not stop.survivors else 'stopping'
        return stop
    finally:
        for member in members:
            member.close()
