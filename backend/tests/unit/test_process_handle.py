"""Destroying a process is bound to a kernel handle, never to a pid (runtime.process_handle).

Two layers. The first runs over a FakeKernel (harvest_support) that keeps the one Windows rule the
design stands on: while a handle to a process is open its pid is NOT reissued, even after the process
exited. Hostile schedules put the exit of a process and the arrival of another on its pid at EVERY kernel
call, which is exactly what Astra's round-4 probes did to psutil (identity_reuse_after_last_ctime and
family_child_reuse_after_ppid_snapshot); a stable run and a replacement that was already there before the
check are the controls. The second layer, at the bottom, drives the real Win32 kernel over child processes
this test starts itself and nothing else."""
import ctypes
import os
import subprocess
import sys
import time

import psutil
import pytest

from harvest_support import FakeKernel, fake_platform, install_kernel
from runtime import process_handle, process_identity
from runtime.process_handle import KernelError, terminate_tree

ROOT_PID, CHILD, GRAND, OUTSIDE, LATE = 910001, 910002, 910003, 910004, 910005
T0 = 1_790_000_000.123456


@pytest.fixture
def os_(monkeypatch):
    return install_kernel(monkeypatch)


@pytest.fixture
def kernel():
    return process_handle.kernel()


def tree(os_):
    """worker -> child -> grandchild, and a process that has nothing to do with them."""
    os_.start(ROOT_PID, T0, ppid=100)
    os_.start(CHILD, T0 + 1, ppid=ROOT_PID)
    os_.start(GRAND, T0 + 2, ppid=CHILD)
    os_.start(OUTSIDE, T0 - 100, ppid=100)


def killed(os_):
    return [pid for pid, _ in os_.kills]


def nothing_left_open(kernel):
    assert kernel.open_handles() == 0, 'a kernel handle was leaked'


# ====================================================== H2R4-01: one process, verified and killed through one handle
def test_control_a_stable_process_is_verified_and_terminated_through_its_handle(os_, kernel):
    os_.start(ROOT_PID, T0)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopped' and stop.signalled == [ROOT_PID] and os_.kills == [(ROOT_PID, T0)]
    nothing_left_open(kernel)


def test_control_a_replacement_already_owning_the_pid_before_the_check_is_never_signalled(os_, kernel):
    os_.start(ROOT_PID, T0 + 100)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'gone' and os_.kills == [] and os_.table == {ROOT_PID: T0 + 100}
    nothing_left_open(kernel)


@pytest.mark.parametrize('exits', [False, True], ids=['worker-keeps-running', 'worker-exits-too'])
@pytest.mark.parametrize('moment', ['open', 'created', 'wait', 'terminate', 'close'])
def test_a_pid_reissued_after_any_kernel_call_cannot_make_the_replacement_the_target(os_, kernel, moment, exits):
    """Astra's schedule: after the LAST creation-time read the worker exits and a replacement takes its pid.
    Here that is tried after every kernel call. The handle pins the pid, so the kernel refuses the reissue
    (until the handle is closed, which is the last thing done) and the only process that can be signalled is
    the worker itself, whenever the replacement arrives."""
    os_.start(ROOT_PID, T0)
    attempts = []

    @os_.watch
    def hostile(event, pid):
        if event == moment and pid == ROOT_PID and not attempts:
            if exits:
                os_.exit(ROOT_PID)
            attempts.append(os_.reuse(ROOT_PID, T0 + 100))

    stop = terminate_tree(ROOT_PID, T0)
    if moment != 'close':
        assert attempts == [False], 'the pid was reissued while a handle named it'
    assert all(created == T0 for _, created in os_.kills), 'a replacement generation was signalled'
    assert stop.state in ('stopped', 'gone')
    nothing_left_open(kernel)


def test_a_worker_that_exited_before_it_could_be_signalled_is_gone_and_nothing_is_signalled(os_, kernel):
    os_.start(ROOT_PID, T0)
    os_.watch(lambda event, pid: os_.exit(ROOT_PID) if event == 'open' else None)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'gone' and os_.kills == []
    nothing_left_open(kernel)


def test_a_process_that_exits_while_it_is_being_terminated_counts_as_stopped(os_, kernel):
    os_.start(ROOT_PID, T0)
    real = FakeKernel.terminate

    def exits_first(self, handle):
        os_.exit(ROOT_PID)           # it dies on its own an instant before TerminateProcess
        return real(self, handle)    # which then fails: access denied on an exited process
    os_.watch(lambda *a: None)
    process_handle.KERNEL.terminate = exits_first.__get__(process_handle.KERNEL)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopped' and stop.errors == []
    nothing_left_open(kernel)


def test_a_process_the_kernel_will_not_open_is_unverified_and_nothing_is_signalled(os_, kernel):
    os_.start(ROOT_PID, T0)
    os_.denied.add(ROOT_PID)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'unverified' and os_.kills == [] and stop.errors == [dict(pid=ROOT_PID, step='open', code=5)]
    nothing_left_open(kernel)


def test_a_pid_nobody_owns_is_gone(os_, kernel):
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'gone' and os_.kills == [] and os_.calls == []


@pytest.mark.parametrize('pid, created', [
    (0, T0), (-1, T0), (True, T0), ('910001', T0), (None, T0), (2 ** 32, T0), (ROOT_PID, None), (ROOT_PID, float('nan')),
    (ROOT_PID, float('inf')), (ROOT_PID, -1.0), (ROOT_PID, True), (ROOT_PID, 10 ** 400), (ROOT_PID, '1790000000.1')])
def test_an_identity_that_cannot_be_judged_never_reaches_the_kernel(os_, kernel, pid, created):
    os_.start(ROOT_PID, T0)
    stop = terminate_tree(pid, created)
    assert stop.state == 'unverified' and os_.calls == [] and os_.kills == []


def test_a_creation_time_that_cannot_be_read_is_unverified_and_the_handle_is_closed(os_, kernel, monkeypatch):
    os_.start(ROOT_PID, T0)
    monkeypatch.setattr(process_handle.kernel(), 'created', lambda handle: (_ for _ in ()).throw(KernelError('GetProcessTimes', 5)))
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'unverified' and os_.kills == []
    nothing_left_open(kernel)


def test_a_process_that_will_not_exit_is_stopping_and_says_which(os_, kernel):
    os_.start(ROOT_PID, T0)
    os_.lingering = True
    stop = terminate_tree(ROOT_PID, T0, timeout=0)
    assert stop.state == 'stopping' and not stop.confirmed
    assert stop.errors == [dict(pid=ROOT_PID, step='wait', code='still_running')]
    nothing_left_open(kernel)


def test_a_process_the_kernel_refuses_to_terminate_is_stopping_and_the_rest_are_still_signalled(os_, kernel, monkeypatch):
    tree(os_)
    real = process_handle.kernel().terminate
    refused = []

    def terminate(handle):
        if process_handle.kernel().handles[handle].pid == CHILD:
            refused.append(CHILD)
            raise KernelError('TerminateProcess', 5)
        return real(handle)
    monkeypatch.setattr(process_handle.kernel(), 'terminate', terminate)
    stop = terminate_tree(ROOT_PID, T0)
    assert refused == [CHILD] and stop.state == 'stopping' and killed(os_) == [GRAND, ROOT_PID]
    assert dict(pid=CHILD, step='terminate', code=5) in stop.errors
    nothing_left_open(kernel)


# ====================================================== H2R4-02: a family is only ever walked from a held handle
def test_control_a_stable_family_is_stopped_deepest_first_and_an_unrelated_process_is_left_alone(os_, kernel):
    tree(os_)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopped' and killed(os_) == [GRAND, CHILD, ROOT_PID]
    assert os_.table == {OUTSIDE: T0 - 100} and stop.skipped == []
    nothing_left_open(kernel)


def test_a_child_pid_reissued_after_the_parent_snapshot_to_a_process_outside_the_tree_is_skipped_not_killed(os_, kernel):
    """Astra's family schedule: the snapshot names CHILD; before its handle is opened CHILD exits and its pid
    goes to an unrelated process whose parent is OUTSIDE the tree. The handle opened on the replacement says
    its parent is not the worker, so it is skipped and reported."""
    tree(os_)
    done = []

    @os_.watch
    def hostile(event, pid):
        if event == 'parents' and not done:
            os_.exit(CHILD)
            done.append(os_.reuse(CHILD, T0 + 50, ppid=OUTSIDE))

    stop = terminate_tree(ROOT_PID, T0)
    assert done == [True], 'the schedule did not run: the child had no handle yet, so its pid may be reissued'
    assert killed(os_) == [ROOT_PID], 'a process outside the verified tree was signalled'
    assert CHILD in os_.table and os_.table[CHILD] == T0 + 50 and OUTSIDE in os_.table
    assert stop.skipped == [dict(pid=CHILD, reason='not_a_descendant')] and stop.incomplete is False
    nothing_left_open(kernel)


def test_a_replacement_that_really_is_a_child_of_the_worker_belongs_to_the_family(os_, kernel):
    """The control of the same schedule: the new owner of the child pid was started BY the worker, after the
    worker. That is a descendant, and it is stopped with the rest."""
    tree(os_)
    os_.watch(lambda event, pid: (os_.exit(CHILD), os_.reuse(CHILD, T0 + 50, ppid=ROOT_PID)) if event == 'parents' and CHILD in os_.table and os_.table[CHILD] == T0 + 1 else None)
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [CHILD, ROOT_PID]
    # The old grandchild's parent NUMBER is the replacement's, but it started before the replacement did, so
    # it cannot be that process's child: the creation-time check keeps a stale parent edge from adopting it.
    assert stop.skipped == [dict(pid=GRAND, reason='not_a_descendant')] and os_.table[GRAND] == T0 + 2


def test_a_process_whose_parent_number_is_the_workers_but_that_started_before_the_worker_is_not_its_child(os_, kernel):
    tree(os_)
    os_.watch(lambda event, pid: (os_.exit(CHILD), os_.reuse(CHILD, T0 - 5, ppid=ROOT_PID)) if event == 'parents' and os_.table.get(CHILD) == T0 + 1 else None)
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [ROOT_PID] and stop.skipped == [dict(pid=CHILD, reason='not_a_descendant')]


def test_the_worker_reissued_to_another_process_after_the_snapshot_still_cannot_adopt_replacement_children(os_, kernel):
    """Root-reuse-after-snapshot: the root's handle was opened first, so its pid cannot be reissued at the snapshot."""
    tree(os_)
    attempts = []
    os_.watch(lambda event, pid: attempts.append(os_.reuse(ROOT_PID, T0 + 77, ppid=100)) if event == 'parents' else None)
    stop = terminate_tree(ROOT_PID, T0)
    assert attempts and not any(attempts) and killed(os_) == [GRAND, CHILD, ROOT_PID] and stop.state == 'stopped'


def test_the_worker_that_exits_after_the_snapshot_still_leads_to_its_children(os_, kernel):
    """Astra H2R4-03 case 1: the worker exits after the parent snapshot. Its handle keeps its pid, so its
    children are still attributable to it and are stopped; the stop is not settled by the worker's absence."""
    tree(os_)
    os_.watch(lambda event, pid: os_.exit(ROOT_PID) if event == 'parents' else None)
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [GRAND, CHILD] and stop.state == 'stopped'
    assert not any(proc.alive for pid, proc in os_.procs.items() if pid in (CHILD, GRAND))
    nothing_left_open(kernel)


def test_a_child_spawned_while_the_family_is_being_stopped_is_found_by_the_second_pass(os_, kernel):
    """Astra H2R4-03 case 2: a late child behind the worker. The first pass signals the family; the second
    walks from the same held handles and finds and stops what appeared meanwhile."""
    tree(os_)
    spawned = []

    @os_.watch
    def late(event, pid):
        if event == 'terminate' and not spawned:
            spawned.append(os_.start(LATE, T0 + 3, ppid=CHILD))
    stop = terminate_tree(ROOT_PID, T0)
    assert LATE in killed(os_) and stop.state == 'stopped' and os_.table == {OUTSIDE: T0 - 100}
    nothing_left_open(kernel)


def test_a_candidate_the_kernel_will_not_open_leaves_the_stop_unconfirmed_and_is_named(os_, kernel):
    tree(os_)
    os_.denied.add(CHILD)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopping' and stop.incomplete is True and killed(os_) == [ROOT_PID]
    assert stop.skipped == [dict(pid=CHILD, reason='access_denied')] and dict(pid=CHILD, step='open', code=5) in stop.errors
    nothing_left_open(kernel)


def test_a_family_that_cannot_be_listed_is_unverified_and_nothing_is_signalled(os_, kernel, monkeypatch):
    tree(os_)
    monkeypatch.setattr(process_handle.kernel(), 'parents', lambda: (_ for _ in ()).throw(KernelError('CreateToolhelp32Snapshot', 5)))
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'unverified' and os_.kills == [] and ROOT_PID in os_.table
    assert stop.errors == [dict(pid=None, step='enumerate', code=5)]
    nothing_left_open(kernel)


def test_descendants_false_stops_the_one_process_only(os_, kernel):
    tree(os_)
    stop = terminate_tree(ROOT_PID, T0, descendants=False)
    assert stop.state == 'stopped' and killed(os_) == [ROOT_PID] and CHILD in os_.table and 'parents' not in [c[0] for c in os_.calls]


def test_a_grandchild_behind_an_intermediate_that_is_already_gone_cannot_be_attributed_and_is_not_touched(os_, kernel):
    """The honest limit of a handle walk: once an intermediate parent has exited and nobody holds its pid, its
    children cannot be tied to the worker. They are not signalled. The device lease (control.operations)
    is what keeps such a survivor from being reported as stopped."""
    os_.start(ROOT_PID, T0, ppid=100)
    os_.start(GRAND, T0 + 2, ppid=CHILD)      # its parent CHILD no longer exists
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [ROOT_PID] and GRAND in os_.table and stop.state == 'stopped'


def test_a_grace_period_lets_a_helper_exit_by_itself_before_it_is_signalled(os_, kernel):
    os_.start(ROOT_PID, T0)
    os_.watch(lambda event, pid: os_.exit(ROOT_PID) if event == 'wait' and not os_.kills and len([c for c in os_.calls if c[0] == 'wait']) == 2 else None)
    stop = terminate_tree(ROOT_PID, T0, grace=0.05)
    assert stop.state == 'stopped' and os_.kills == []


def test_terminate_owned_reads_the_identity_from_a_handle_and_never_signals_a_pid_nobody_has(os_, kernel):
    os_.start(ROOT_PID, T0)
    assert process_handle.terminate_owned(ROOT_PID).state == 'stopped' and killed(os_) == [ROOT_PID]
    assert process_handle.terminate_owned(ROOT_PID + 1).state == 'gone'
    os_.start(OUTSIDE, T0)
    os_.denied.add(OUTSIDE)
    assert process_handle.terminate_owned(OUTSIDE).state == 'unverified' and killed(os_) == [ROOT_PID]
    nothing_left_open(kernel)


# ====================================================== the same guarantees against the REAL Windows kernel
needs_windows = pytest.mark.skipif(os.name != 'nt', reason='the handle design is the Windows kernel')
SLEEP = 'import time; time.sleep(60)'

# ====================================================== the family listing: only ERROR_NO_MORE_FILES is a normal end
class Toolhelp:
    """kernel32's Toolhelp listing, scripted: each step is (returns TRUE?, GetLastError after it, (pid, parent) it writes)."""

    def __init__(self, steps, snapshot=0x77):
        self.steps, self.snapshot, self.closed, self.calls = list(steps), snapshot, [], []

    def CreateToolhelp32Snapshot(self, flags, pid):
        self.calls.append('snapshot')
        return self.snapshot

    def _step(self, name, ref):
        import ctypes
        self.calls.append(name)
        ok, error, row = self.steps.pop(0)
        if row is not None:
            ref._obj.th32ProcessID, ref._obj.th32ParentProcessID = row
        ctypes.set_last_error(error)
        return ok

    def Process32FirstW(self, snapshot, ref):
        return self._step('first', ref)

    def Process32NextW(self, snapshot, ref):
        return self._step('next', ref)

    def CloseHandle(self, handle):
        self.closed.append(handle)


def listing(steps, **kwargs):
    from runtime.process_handle import Win32Kernel
    kernel_ = Win32Kernel()
    kernel_._k32 = Toolhelp(steps, **kwargs)
    return kernel_


NO_MORE = 18


@needs_windows
def test_a_listing_that_ends_with_no_more_files_is_the_whole_table():
    kernel_ = listing([(True, 0, (10, 1)), (True, 0, (11, 10)), (False, NO_MORE, None)])
    assert kernel_.parents() == {10: 1, 11: 10} and kernel_._k32.closed == [0x77]


@needs_windows
@pytest.mark.parametrize('error', [5, 24, 6, 0], ids=['access-denied', 'bad-length', 'invalid-handle', 'no-error-recorded'])
def test_a_listing_that_stops_part_way_for_any_other_reason_is_an_error_not_a_short_table(error):
    kernel_ = listing([(True, 0, (10, 1)), (True, 0, (11, 10)), (False, error, None)])
    with pytest.raises(KernelError) as caught:
        kernel_.parents()
    assert caught.value.op == 'Process32NextW' and caught.value.code == error
    assert kernel_._k32.closed == [0x77], 'the snapshot handle must be closed on the error path too'


@needs_windows
@pytest.mark.parametrize('error', [5, 24], ids=['access-denied', 'bad-length'])
def test_a_listing_that_cannot_start_is_an_error_not_an_empty_table(error):
    kernel_ = listing([(False, error, None)])
    with pytest.raises(KernelError) as caught:
        kernel_.parents()
    assert caught.value.op == 'Process32FirstW' and caught.value.code == error and kernel_._k32.closed == [0x77]


@needs_windows
def test_an_empty_listing_that_ends_normally_is_an_empty_table():
    kernel_ = listing([(False, NO_MORE, None)])
    assert kernel_.parents() == {} and kernel_._k32.closed == [0x77]


@needs_windows
def test_a_snapshot_that_cannot_be_taken_is_an_error_and_there_is_nothing_to_close():
    import ctypes
    kernel_ = listing([], snapshot=ctypes.c_void_p(-1).value)
    with pytest.raises(KernelError, match='CreateToolhelp32Snapshot'):
        kernel_.parents()
    assert kernel_._k32.closed == []


@needs_windows
def test_the_snapshot_is_closed_when_the_listing_itself_raises():
    kernel_ = listing([(True, 0, (10, 1))])
    kernel_._k32.Process32NextW = lambda snapshot, ref: (_ for _ in ()).throw(OSError('boom'))
    with pytest.raises(OSError):
        kernel_.parents()
    assert kernel_._k32.closed == [0x77]


@needs_windows
def test_a_stale_no_more_files_from_an_earlier_call_does_not_hide_a_real_failure():
    import ctypes
    ctypes.set_last_error(NO_MORE)                                  # left over from some earlier listing on this thread
    kernel_ = listing([(False, 0, None)])                           # Process32First fails and records no error at all
    with pytest.raises(KernelError):
        kernel_.parents()


def test_a_family_that_cannot_be_listed_is_unverified_with_the_reason_named_and_nothing_signalled(os_, kernel, monkeypatch):
    tree(os_)
    monkeypatch.setattr(process_handle.kernel(), 'parents', lambda: (_ for _ in ()).throw(KernelError('Process32NextW', 5)))
    stop = terminate_tree(ROOT_PID, T0)
    assert (stop.state, stop.reason) == ('unverified', 'family_enumeration_failed') and not stop.confirmed
    assert os_.kills == [] and stop.errors == [dict(pid=None, step='enumerate', code=5)]
    nothing_left_open(kernel)


def test_a_listing_that_fails_after_the_family_was_signalled_is_never_reported_stopped(os_, kernel, monkeypatch):
    """The first listing worked, the worker and its children were signalled, and the look afterwards fails: the
    family can no longer be shown to be gone, so the stop is unverified (and what was signalled is on record)."""
    tree(os_)
    real = process_handle.kernel().parents
    calls = []

    def parents():
        calls.append(1)
        if len(calls) > 1:
            raise KernelError('Process32NextW', 5)
        return real()
    monkeypatch.setattr(process_handle.kernel(), 'parents', parents)
    stop = terminate_tree(ROOT_PID, T0)
    assert (stop.state, stop.reason) == ('unverified', 'family_enumeration_failed') and not stop.confirmed
    assert killed(os_) == [GRAND, CHILD, ROOT_PID] and stop.signalled == [GRAND, CHILD, ROOT_PID]
    nothing_left_open(kernel)


# ====================================================== late children: look again until a look finds nothing new
def test_control_a_settled_family_takes_one_pass_and_one_look_that_finds_nothing(os_, kernel):
    tree(os_)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopped' and stop.survivors == [] and stop.reason is None
    assert [call for call in os_.calls if call[0] == 'parents'] == [('parents', None)] * 2        # the first listing, the look after the wait


def test_a_child_found_late_that_spawns_again_before_it_is_signalled_is_found_by_the_next_look(os_, kernel):
    """Astra H2R5: a child captured in the second pass spawns another before it is signalled. The old code
    looked twice and reported stopped with that grandchild still alive."""
    tree(os_)
    spawned = {}

    @os_.watch
    def hostile(event, pid):
        if event == 'terminate' and 'late' not in spawned:
            spawned['late'] = os_.start(LATE, T0 + 3, ppid=CHILD)                  # appears while the first pass is signalling
        if event == 'open' and pid == LATE and 'grandchild' not in spawned:
            spawned['grandchild'] = os_.start(LATE + 1, T0 + 4, ppid=LATE)         # LATE spawns before it can be signalled
    stop = terminate_tree(ROOT_PID, T0)
    assert LATE in killed(os_) and LATE + 1 in killed(os_), 'the grandchild of the late child was not stopped'
    assert stop.state == 'stopped' and stop.survivors == [] and os_.table == {OUTSIDE: T0 - 100}
    nothing_left_open(kernel)


def test_a_family_that_keeps_growing_is_unverified_with_its_survivors_named_after_the_bound(os_, kernel):
    """Every member that is opened spawns a new child before it can be signalled. The loop is bounded
    (MAX_PASSES); what it found last was never signalled and is named."""
    os_.start(ROOT_PID, T0, ppid=100)
    counter = iter(range(ROOT_PID + 1000, ROOT_PID + 2000))
    parents_of = {}

    @os_.watch
    def keep_spawning(event, pid):
        if event in ('open', 'created') and pid not in parents_of:
            parents_of[pid] = next(counter)
            os_.start(parents_of[pid], T0 + len(parents_of), ppid=pid)
    stop = terminate_tree(ROOT_PID, T0)
    assert (stop.state, stop.reason) == ('unverified', 'family_not_settled') and not stop.confirmed
    assert len(stop.signalled) == process_handle.MAX_PASSES + 1       # the worker and its first child in pass 1, one more in each later pass
    assert len(stop.survivors) == 1 and stop.survivors[0] in os_.table, 'the survivor must be named and really alive'
    assert not (set(stop.survivors) & set(stop.signalled)), 'a survivor that was never signalled is not in the signalled list'
    nothing_left_open(kernel)


def test_a_signalled_member_that_is_still_running_at_the_end_is_named_a_survivor_and_the_stop_is_not_confirmed(os_, kernel):
    tree(os_)
    os_.lingering = True
    stop = terminate_tree(ROOT_PID, T0, timeout=0)
    assert stop.state == 'stopping' and not stop.confirmed and sorted(stop.survivors) == sorted([ROOT_PID, CHILD, GRAND])


def test_the_final_check_is_what_stops_a_dead_looking_family_from_being_called_stopped(os_, kernel, monkeypatch):
    """Every kernel answer says the members exited when they are waited for, but one is running when the stop ends
    (it was revived by another process sharing the pid's handle table in this fake): `stopped` needs the last look."""
    tree(os_)
    real_wait = process_handle.kernel().wait
    state = {'final': False}

    def wait(handle, timeout_ms):
        proc = process_handle.kernel().handles[handle]
        if state['final'] and proc.pid == CHILD:
            return False
        return real_wait(handle, timeout_ms)
    monkeypatch.setattr(process_handle, '_running', lambda kern, members: (state.update(final=True), [m.pid for m in members if not wait(m.handle, 0)])[1])
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopping' and stop.survivors == [CHILD]


# ====================================================== H2R5-08: ctypes.ArgumentError is a kernel failure too
def argument_error_from(monkeypatch, call, *, pid=None, after_kills=False):
    """Make one kernel call raise ctypes.ArgumentError (for one pid or every pid; with after_kills only once
    something has been terminated). It is neither an OSError nor a ValueError, and ctypes raises it AFTER
    OpenProcess has already handed out a handle, so a path that does not expect it leaks that handle."""
    kern = process_handle.kernel()
    real = getattr(kern, call)

    def raising(subject, *args):
        who = subject if call == 'open' else kern.handles[subject].pid
        if (pid is None or who == pid) and (not after_kills or kern.os.kills):
            raise ctypes.ArgumentError('argument 1: <class \'TypeError\'>: wrong type')
        return real(subject, *args)
    monkeypatch.setattr(kern, call, raising)
    return raising


def test_argument_error_is_part_of_the_one_kernel_error_tuple():
    assert ctypes.ArgumentError in process_handle.KERNEL_ERRORS and not issubclass(ctypes.ArgumentError, OSError)
    assert issubclass(KernelError, process_handle.KERNEL_ERRORS)


@pytest.mark.parametrize('call', ['open', 'created', 'wait'])
def test_an_argument_error_while_verifying_a_worker_is_unverified_nothing_is_signalled_and_no_handle_leaks(os_, kernel, monkeypatch, call):
    os_.start(ROOT_PID, T0)
    argument_error_from(monkeypatch, call)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'unverified' and os_.kills == [] and stop.errors == [dict(pid=ROOT_PID, step='open', code='ArgumentError')]
    nothing_left_open(kernel)


def test_an_argument_error_from_terminate_leaves_the_stop_unconfirmed_and_the_rest_are_still_signalled(os_, kernel, monkeypatch):
    tree(os_)
    argument_error_from(monkeypatch, 'terminate', pid=ROOT_PID)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopping' and not stop.confirmed and killed(os_) == [GRAND, CHILD]
    assert dict(pid=ROOT_PID, step='terminate', code='ArgumentError') in stop.errors
    nothing_left_open(kernel)


@pytest.mark.parametrize('call', ['created', 'parent_pid'])
def test_an_argument_error_while_verifying_a_child_skips_it_names_it_and_closes_its_handle(os_, kernel, monkeypatch, call):
    tree(os_)
    argument_error_from(monkeypatch, call, pid=CHILD)
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [ROOT_PID] and stop.state == 'stopping' and stop.incomplete
    assert stop.skipped == [dict(pid=CHILD, reason='unreadable')]
    assert dict(pid=CHILD, step='verify', code='ArgumentError') in stop.errors
    nothing_left_open(kernel)


def test_an_argument_error_while_opening_a_child_names_it_and_the_stop_is_not_confirmed(os_, kernel, monkeypatch):
    tree(os_)
    argument_error_from(monkeypatch, 'open', pid=CHILD)
    stop = terminate_tree(ROOT_PID, T0)
    assert killed(os_) == [ROOT_PID] and stop.state == 'stopping' and stop.skipped == [dict(pid=CHILD, reason='open_failed')]
    nothing_left_open(kernel)


def test_an_argument_error_from_the_family_listing_is_unverified_and_nothing_is_signalled(os_, kernel, monkeypatch):
    tree(os_)
    monkeypatch.setattr(kernel, 'parents', lambda: (_ for _ in ()).throw(ctypes.ArgumentError('argument 1: wrong type')))
    stop = terminate_tree(ROOT_PID, T0)
    assert (stop.state, stop.reason) == ('unverified', 'family_enumeration_failed') and os_.kills == []
    nothing_left_open(kernel)


def test_an_argument_error_while_waiting_after_the_signal_names_the_survivors_and_closes_every_handle(os_, kernel, monkeypatch):
    tree(os_)
    argument_error_from(monkeypatch, 'wait', after_kills=True)
    stop = terminate_tree(ROOT_PID, T0)
    assert stop.state == 'stopping' and not stop.confirmed and sorted(stop.survivors) == sorted([ROOT_PID, CHILD, GRAND])
    nothing_left_open(kernel)


@pytest.mark.parametrize('call', ['open', 'created'])
def test_terminate_owned_treats_an_argument_error_as_unverified_and_never_leaks_the_handle(os_, kernel, monkeypatch, call):
    os_.start(ROOT_PID, T0)
    argument_error_from(monkeypatch, call)
    stop = process_handle.terminate_owned(ROOT_PID)
    assert stop.state == 'unverified' and os_.kills == [] and stop.errors == [dict(pid=ROOT_PID, step='open', code='ArgumentError')]
    nothing_left_open(kernel)


def test_an_error_nobody_expected_after_the_open_still_closes_the_handle(os_, kernel, monkeypatch):
    """The handle is closed in a `finally`, not only by the except clauses that name the expected errors."""
    os_.start(ROOT_PID, T0)
    monkeypatch.setattr(kernel, 'created', lambda handle: (_ for _ in ()).throw(RuntimeError('not a kernel error')))
    with pytest.raises(RuntimeError):
        terminate_tree(ROOT_PID, T0)
    nothing_left_open(kernel)


def spawn(code=SLEEP, **kwargs):
    return subprocess.Popen([sys._base_executable, '-c', code], **kwargs)


@pytest.fixture
def children():
    """Child processes this test started, and nothing else, killed at the end whatever happens."""
    started = []
    yield started
    for child in started:
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        except Exception:   # noqa: BLE001
            pass


def alive(pid):
    try:
        return psutil.Process(pid).is_running()
    except psutil.NoSuchProcess:
        return False


@needs_windows
def test_the_real_kernel_reads_the_same_start_time_psutil_does(real_kernel, children):
    """The creation time compared with a recorded one is read from the handle; the record came from psutil.
    Over this process and a few children they agree within process_identity.same_created (a few floating-point
    steps of one 100 ns FILETIME tick), which is the evidence for that tolerance."""
    for child in [spawn() for _ in range(4)]:
        children.append(child)
    time.sleep(0.3)
    deltas = []
    for pid in [os.getpid()] + [child.pid for child in children]:
        handle = real_kernel.open(pid)
        try:
            raw = real_kernel.created(handle)
        finally:
            real_kernel.close(handle)
        reading = psutil.Process(pid).create_time()
        deltas.append(abs(raw - reading))
        assert process_identity.same_created(raw, reading)
        assert psutil.Process(pid).create_time() == reading       # two psutil reads are bit-identical
    import math
    assert max(deltas) <= process_identity.CREATED_ULPS * math.ulp(reading) / 2   # well inside the allowance


@needs_windows
def test_the_real_kernel_stops_a_child_it_verified_and_leaves_its_sibling_alone(real_kernel, children):
    target, sibling = spawn(), spawn()
    children.extend([target, sibling])
    time.sleep(0.3)
    created = psutil.Process(target.pid).create_time()
    stop = terminate_tree(target.pid, created)
    assert stop.state == 'stopped' and stop.signalled == [target.pid]
    assert target.wait(timeout=5) is not None and sibling.poll() is None and alive(sibling.pid)
    assert terminate_tree(target.pid, created).state == 'gone'           # a second stop finds nothing


@needs_windows
def test_the_real_kernel_refuses_a_creation_time_that_is_not_the_childs(real_kernel, children):
    child = spawn()
    children.append(child)
    time.sleep(0.3)
    created = psutil.Process(child.pid).create_time()
    for wrong in (created + 100, created - 100, created + 0.5):
        assert terminate_tree(child.pid, wrong).state == 'gone'
    assert child.poll() is None and alive(child.pid)


@needs_windows
def test_the_real_kernel_stops_a_three_level_family_and_only_that(real_kernel, children):
    grandchild_code = SLEEP
    child_code = ('import subprocess, sys, time\n'
                  f'g = subprocess.Popen([sys._base_executable, "-c", {grandchild_code!r}])\n'
                  'print(g.pid, flush=True)\ntime.sleep(60)')
    parent_code = ('import subprocess, sys, time\n'
                   f'c = subprocess.Popen([sys._base_executable, "-c", {child_code!r}], stdout=subprocess.PIPE, text=True)\n'
                   'print(c.pid, c.stdout.readline().strip(), flush=True)\ntime.sleep(60)')
    parent = spawn(parent_code, stdout=subprocess.PIPE, text=True)
    bystander = spawn()
    children.extend([parent, bystander])
    child_pid, grandchild_pid = (int(x) for x in parent.stdout.readline().split())
    time.sleep(0.3)
    created = psutil.Process(parent.pid).create_time()
    stop = terminate_tree(parent.pid, created)
    members = [parent.pid, child_pid, grandchild_pid]
    assert stop.state == 'stopped' and sorted(stop.signalled) == sorted(members) and stop.signalled[-1] == parent.pid
    time.sleep(0.2)
    assert not any(alive(pid) for pid in members)
    assert bystander.poll() is None and alive(bystander.pid)


def marked_identities(marker):
    """(pid, creation time) of every running process whose command line carries this test's unique marker: the
    processes this test started. Each is ONE fact. psutil fixes a Process object's identity (pid and creation
    time) when it opens the pid and is_running() asks the operating system again whether that same process still
    owns it, so a pid that passed to another process between the pid listing, the command-line read and the
    check is dropped, never recorded with the newcomer's creation time. (psutil.process_iter is not used: it
    hands back Process objects cached from an earlier call, whose pid may belong to someone else by now.)"""
    found = []
    for pid in psutil.pids():
        try:
            process = psutil.Process(pid)
            if any(marker in part for part in (process.cmdline() or [])) and process.is_running():
                found.append((process.pid, process.create_time()))
        except psutil.Error:
            continue
    return found


def marked_processes(marker):
    return [pid for pid, _ in marked_identities(marker)]


def stop_identities(identities):
    """Stop exactly the processes that were found, each through a kernel handle verified against the creation
    time it was found with (process_handle.terminate_tree): a pid that passed to another process since is
    'gone' for that identity and nothing is signalled. A bare psutil.Process(pid).kill() would signal whatever
    owns the pid by then."""
    for pid, created in identities:
        terminate_tree(pid, created, descendants=False)


def stop_marked(marker, passes=5):
    """Clean up what this test started, whatever happened: find, stop, and look again (a family that was still
    growing may have added members meanwhile), a bounded number of times."""
    for _ in range(passes):
        found = marked_identities(marker)
        if not found:
            return
        stop_identities(found)


# ====================================================== H2R7-06: a test's own cleanup never signals a process it did not find
MARKER = 'cleanup-marker-0123'


def marked_world(monkeypatch, *processes):
    """The fake operating system under BOTH psutil and the kernel, with these (pid, creation time) processes
    running and carrying the marker on their command line, and nothing else listed."""
    os_ = fake_platform(monkeypatch)
    os_.exit(os.getpid())
    monkeypatch.setattr(psutil, 'pids', lambda: sorted(os_.table))
    for pid, created in processes:
        os_.start(pid, created, ppid=100)
        os_.cmdlines[pid] = ['python', '-c', f'# {MARKER}']
    return os_


def test_control_cleanup_stops_the_marked_process_it_found_through_its_handle(monkeypatch):
    os_ = marked_world(monkeypatch, (ROOT_PID, T0))
    assert marked_identities(MARKER) == [(ROOT_PID, T0)] and marked_processes(MARKER) == [ROOT_PID]
    stop_marked(MARKER)
    assert os_.kills == [(ROOT_PID, T0)] and os_.table == {}
    assert process_handle.kernel().open_handles() == 0


def test_cleanup_never_signals_a_process_that_took_the_pid_after_the_scan(monkeypatch):
    """The review's schedule (H2R7-06): enumeration yields (910001, T0); before the cleanup acts, that process
    exits and another one is started at the same pid at T0 + 100. The cleanup holds the identity it found, so
    the kernel handle it opens is verified against T0 and the newcomer is left alone. The old cleanup
    (psutil.Process(pid).kill()) signalled whoever owned the pid."""
    os_ = marked_world(monkeypatch, (ROOT_PID, T0))
    found = marked_identities(MARKER)
    os_.exit(ROOT_PID)
    assert os_.reuse(ROOT_PID, T0 + 100)
    stop_identities(found)
    assert os_.kills == [] and os_.table == {ROOT_PID: T0 + 100}
    assert process_handle.kernel().open_handles() == 0


def test_the_same_schedule_through_the_whole_cleanup(monkeypatch):
    os_ = marked_world(monkeypatch, (ROOT_PID, T0), (CHILD, T0 + 1))
    this = sys.modules[__name__]
    real = this.marked_identities
    swapped = []

    def discover_then_swap(marker):
        found = real(marker)
        if not swapped:
            swapped.append(True)
            os_.exit(ROOT_PID)
            os_.reuse(ROOT_PID, T0 + 100)             # only the first process loses its pid after the scan
        return found
    monkeypatch.setattr(this, 'marked_identities', discover_then_swap)
    stop_marked(MARKER)
    assert os_.kills == [(CHILD, T0 + 1)] and os_.table == {ROOT_PID: T0 + 100}


def test_a_pid_that_passes_to_another_process_while_it_is_being_listed_is_not_recorded(monkeypatch):
    """Between the command-line read and the check the pid changes hands: the command line belonged to the old
    process, so nothing is recorded (psutil's is_running() compares the identity fixed at open)."""
    os_ = marked_world(monkeypatch, (ROOT_PID, T0))
    real = psutil.Process.cmdline
    swapped = []

    def cmdline(self):
        read = real(self)
        if not swapped:
            swapped.append(True)
            os_.exit(ROOT_PID)
            os_.reuse(ROOT_PID, T0 + 100)
        return read
    monkeypatch.setattr(psutil.Process, 'cmdline', cmdline)
    assert marked_identities(MARKER) == [] and swapped == [True]
    stop_marked(MARKER)
    assert os_.kills == [] and os_.table == {ROOT_PID: T0 + 100}


def test_control_a_process_that_already_had_the_pid_before_the_scan_is_neither_found_nor_signalled(monkeypatch):
    os_ = marked_world(monkeypatch)
    os_.start(ROOT_PID, T0 + 100, ppid=100)
    os_.cmdlines[ROOT_PID] = ['python', '-c', 'something else']
    assert marked_identities(MARKER) == []
    stop_marked(MARKER)
    assert os_.kills == []


@needs_windows
@pytest.mark.parametrize('round_', [1, 2, 3])
def test_the_real_kernel_never_calls_a_family_stopped_while_a_late_descendant_is_alive(real_kernel, children, round_):
    """A worker keeps starting children, and each child starts a grandchild of its own a moment after it starts:
    new members keep appearing while the family is being stopped. Whatever the timing, `stopped` must mean that
    nothing of the family is running. The processes carry a marker unique to this test, so that is checkable and
    so anything left over is cleaned up (they are all this test's own)."""
    import uuid
    marker = f'late-{uuid.uuid4().hex}'
    leaf = f'import time\n# {marker}\ntime.sleep(60)'
    child = ('import subprocess, sys, time\n' f'# {marker}\n'
             f'subprocess.Popen([sys._base_executable, "-c", {leaf!r}])\n'
             'time.sleep(60)')
    worker = ('import subprocess, sys, time\n' f'# {marker}\n'
              'print("up", flush=True)\n'
              'for _ in range(40):\n'
              f'    subprocess.Popen([sys._base_executable, "-c", {child!r}])\n'
              '    time.sleep(0.04)\n'
              'time.sleep(60)')
    process = spawn(worker, stdout=subprocess.PIPE, text=True)
    children.append(process)
    try:
        assert process.stdout.readline().strip() == 'up'
        time.sleep(0.2 * round_)                       # the family is mid-growth
        created = psutil.Process(process.pid).create_time()
        stop = terminate_tree(process.pid, created)
        time.sleep(0.3)
        left = marked_processes(marker)
        if stop.state == 'stopped':
            assert left == [], f'reported stopped with {left} still running'
            assert stop.survivors == []
        else:
            assert stop.state in ('stopping', 'unverified') and not stop.confirmed
            assert set(stop.survivors) <= set(left) or stop.survivors == []
    finally:
        stop_marked(marker)                             # only identities this test found carrying its marker, through held handles


@needs_windows
def test_the_real_kernel_says_a_pid_nobody_has_is_gone(real_kernel):
    stop = terminate_tree(2 ** 32 - 6, T0)
    assert stop.state == 'gone'
    answer, held, error = process_handle.open_verified(real_kernel, 2 ** 32 - 6, T0)
    assert answer is False and held is None and error.absent
