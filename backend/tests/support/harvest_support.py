"""Shared scaffolding for the harvesting tests: one fake wall clock for the ledger
modules and the lifecycle host, a temporary-ledger environment, and builders for
collectors, runs and battles. Plain functions; each test module wraps them in
its own fixtures.
"""

import paths as test_paths
import json
import os
import sqlite3
import time
from types import SimpleNamespace

from control import harvest, operations
from player.run_controls import resolve
from towerpilot_workflows import battles as ledger_battles
from towerpilot_workflows import harvest as ledger_h
from towerpilot_workflows import operations as ledger_ops
from towerpilot_workflows.actions import Actions, new_record
from towerpilot_workflows.battles import Battles

NOW = 1_800_000_000.0
CTX = ('instance', 'account', 'serial', 'profile')


def decided(stop):
    """A Stop reply's `stop` without `by_this_request`: how the stop stands, not what this request did."""
    return {key: value for key, value in stop.items() if key != 'by_this_request'}


class Clock:
    """One wall clock for the ledger modules and the lifecycle host, so a test
    that says 'a minute later' means the same minute to the fence and the ledger."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


class _VerifiedProcess:
    """The process inspect_worker hands back for a worker the test declared alive: psutil.Process(pid)
    (the test's own psutil double where make_psutil installed one), opened only when something is
    actually done to it, because most callers only ask whether the worker runs."""

    def __init__(self, pid):
        self.pid, self._process = pid, None

    def __getattr__(self, name):
        if self._process is None:
            import psutil
            self._process = psutil.Process(self.pid)
        return getattr(self._process, name)


def fake_inspect(answer):
    """A double for control.operations.inspect_worker, the one place a recorded worker is opened.
    answer(worker) is True, False (proven gone) or None (unknown)."""
    def inspect(worker):
        verdict = answer(worker)
        if verdict is not True:
            return verdict, None
        return True, _VerifiedProcess((worker or {}).get('pid'))   # a test may declare a never-recorded worker alive
    return inspect


class FakeProc:
    """One process the fake operating system knows. `handles` counts the open kernel handles that name it."""

    def __init__(self, pid, created, ppid=0):
        self.pid, self.created, self.ppid = pid, created, ppid
        self.alive, self.handles = True, 0


class FakeOS:
    """The process table beneath psutil AND the kernel handles runtime.process_handle uses.

    It keeps the one Windows rule the destructive path relies on: while a handle to a process is
    open, its pid is NOT reissued, even after the process exited (reuse() is refused). Everything else
    a test needs to be hostile is a hook: watch(fn) runs fn(event, pid) after every kernel call
    ('open', 'created', 'parent', 'wait', 'terminate', 'close', 'parents'), which is where a schedule puts the
    exit of one process and the arrival of another on its pid.

    table     {pid: creation time} of the processes that are running now
    kills     [(pid, created)] of every process terminated, by the kernel or by psutil
    denied    pids the kernel and psutil refuse to inspect (AccessDenied)
    lingering a terminated process does not exit
    stand_ins pids that exist on demand with creation time 1.0 (the fake workers of the harvest tests)
    cmdlines  {pid: argv} for the processes psutil is asked the command line of (a process without one has none)"""

    def __init__(self):
        self.procs, self.denied, self.kills, self.cmdlines = {}, set(), [], {}
        self.lingering, self.stand_ins, self.watchers, self.calls = False, set(), [], []

    @property
    def table(self):
        return {pid: proc.created for pid, proc in self.procs.items() if proc.alive}

    def start(self, pid, created, ppid=0):
        self.procs[pid] = FakeProc(pid, created, ppid)

    swap = start     # the pid changes hands (a test that holds no kernel handle)

    def exit(self, pid):
        proc = self.procs.get(pid)
        if proc is not None:
            proc.alive = False
            if not proc.handles:
                del self.procs[pid]

    def reuse(self, pid, created, ppid=0):
        """The pid is reissued to a NEW process: only after the old one exited and every handle to it is
        closed, which is the kernel's own rule. Returns False (nothing happens) when a handle still pins it."""
        proc = self.procs.get(pid)
        if proc is not None and proc.handles:
            return False
        self.procs.pop(pid, None)
        self.cmdlines.pop(pid, None)             # the newcomer has a command line of its own, not its predecessor's
        self.start(pid, created, ppid)
        return True

    def watch(self, fn):
        self.watchers.append(fn)
        return fn

    def event(self, name, pid):
        self.calls.append((name, pid))
        for fn in list(self.watchers):
            fn(name, pid)


class FakeKernel:
    """runtime.process_handle's kernel32/ntdll boundary over a FakeOS. Same methods as Win32Kernel."""

    def __init__(self, os_):
        self.os, self.handles, self._next = os_, {}, 100

    def _proc(self, handle):
        return self.handles[handle]

    def open(self, pid):
        from runtime.process_handle import KernelError
        os_ = self.os
        if pid in os_.stand_ins and pid not in os_.procs:
            os_.start(pid, 1.0)
        if pid in os_.denied:
            raise KernelError('OpenProcess', 5)
        proc = os_.procs.get(pid)
        if proc is None:
            raise KernelError('OpenProcess', 87)
        self._next += 4
        self.handles[self._next] = proc
        proc.handles += 1
        os_.event('open', pid)
        return self._next

    def created(self, handle):
        proc = self._proc(handle)
        value = proc.created
        self.os.event('created', proc.pid)
        return value

    def parent_pid(self, handle):
        proc = self._proc(handle)
        value = proc.ppid
        self.os.event('parent', proc.pid)
        return value

    def wait(self, handle, timeout_ms):
        proc = self._proc(handle)
        done = not proc.alive
        self.os.event('wait', proc.pid)
        return done

    def terminate(self, handle):
        from runtime.process_handle import KernelError
        proc = self._proc(handle)
        if not proc.alive:
            raise KernelError('TerminateProcess', 5)
        self.os.kills.append((proc.pid, proc.created))
        if not self.os.lingering:
            proc.alive = False
        self.os.event('terminate', proc.pid)

    def close(self, handle):
        proc = self.handles.pop(handle)
        proc.handles -= 1
        if not proc.alive and not proc.handles and self.os.procs.get(proc.pid) is proc:
            del self.os.procs[proc.pid]
        self.os.event('close', proc.pid)       # from here the pid may be reissued if no other handle names it

    def parents(self):
        table = {pid: proc.ppid for pid, proc in self.os.procs.items() if proc.alive}
        self.os.event('parents', None)
        return table

    def open_handles(self):
        return len(self.handles)


def install_kernel(monkeypatch, os_=None):
    """Put a FakeKernel (over os_, or a fresh empty FakeOS) where runtime.process_handle looks for the
    kernel, so nothing a test does can reach the real one. Returns the FakeOS."""
    from runtime import process_handle
    os_ = os_ or FakeOS()
    monkeypatch.setattr(process_handle, 'KERNEL', FakeKernel(os_))
    return os_


def fake_platform(monkeypatch):
    """A FakeOS under BOTH the kernel and psutil: the real psutil.Process (identity check, parents,
    is_running) runs over the process table a test controls, and runtime.process_handle runs over a
    FakeKernel on the same table. This test process stays a real, readable process. No real process is
    inspected or signalled."""
    import psutil
    os_ = install_kernel(monkeypatch)
    os_.start(os.getpid(), psutil.Process().create_time())

    class Platform:
        def __init__(self, pid):
            self.pid = pid

        def _check(self):
            if self.pid in os_.denied:
                raise psutil.AccessDenied(self.pid)
            proc = os_.procs.get(self.pid)
            if proc is None or not proc.alive:
                raise psutil.NoSuchProcess(self.pid)
            return proc

        def create_time(self, fast_only=False):
            return self._check().created

        def ppid(self):
            return self._check().ppid

        def cmdline(self):
            self._check()
            return list(os_.cmdlines.get(self.pid, []))

        def kill(self):
            proc = self._check()
            os_.kills.append((proc.pid, proc.created))   # a psutil kill is a defect now: the tests assert on it
            proc.alive = False

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(psutil._psplatform, 'Process', Platform)
    monkeypatch.setattr(psutil, '_ppid_map', lambda: {pid: proc.ppid for pid, proc in os_.procs.items() if proc.alive})
    return os_


def delegated_lease(monkeypatch, serial, host='127.0.0.1', port=5037):
    """A delegated scheduler child's lease on REAL lock files (the device-lease directory is a temporary one): it
    holds the activity lock and nobody holds the primary lock (the supervisor that took it has exited). This
    process is the current owner, so device_lease.child_environment() promotes it exactly as a scheduler child
    that is asked to supervise does. The caller releases it (lease.release()) when the test is over."""
    from runtime import device_lease
    key = device_lease._device_key(serial, host, port)
    path = device_lease._stem(key)
    lease = device_lease.Lease(key, path, device_lease._lock(path.with_suffix('.activity')), delegated=True)
    path.with_suffix('.activity.json').write_text(json.dumps(lease.grant()))
    monkeypatch.setattr(device_lease, '_current', lease)
    monkeypatch.setattr(device_lease, 'ensure_current', lambda: lease)
    return lease


def promote_after_the_primary_probe(monkeypatch, lease):
    """The schedule of review finding H2R7-01 on real locks: the moment a reader has let go of the PRIMARY
    lock it probed (and before it probes the activity lock), the delegated child is promoted by the real
    device_lease.child_environment(), which takes the primary lock and only then lets go of the activity
    lock. Returns the list that says whether the promotion happened and when. A reader that keeps the free
    primary probe taken until it has probed the activity lock too is not interleaved: the promotion happens
    after its answer."""
    import os as _os
    from runtime import device_lease
    real, fired = device_lease._unlock, []

    def unlock(handle):
        real(handle)
        if not fired and _os.path.normcase(str(handle.name)) == _os.path.normcase(str(lease.path)):
            fired.append(True)
            device_lease.child_environment()
    monkeypatch.setattr(device_lease, '_unlock', unlock)
    return fired


def gather_of(source):
    """The gathering block of the test run: its flags and an edit counter that changes the policy revision."""
    return {'ad_gems': source['ad'], 'gem_orbit': {'enabled': source['floating']},
            'gem_delay_sec': [3, 10 + source.get('edit', 0)]}


def harvest_body(env, preset='bp_farm', revision=1, key='harvest-launch-1', **changes):
    """The request an automatic collector launch carries: the policy revision and the
    frozen envelope derived from the same gathering block, as the dashboard builds them."""
    gather = gather_of(env.source)
    body = dict(preset=preset, harvest=True, desire_revision=revision,
                policy_revision=ledger_h.gem_policy_revision(preset, gather),
                battle_envelope={'preset': {'gather': gather}}, idempotency_key=key)
    body.update(changes)
    return body


def policy(ad=True, floating=False):
    return dict(ad_gems=dict(enabled=ad, interval_sec=30, window_sec=15),
                floating_gems=dict(enabled=floating, interval_sec=20, window_sec=10, tap_interval_sec=2))


def make_env(tmp_path, monkeypatch):
    from device import adbclient
    monkeypatch.setattr(adbclient, '_HOST', ('127.0.0.1', 5037))
    alive = {'value': True}
    clock = Clock(NOW)
    for module in (ledger_ops, ledger_battles, ledger_h, harvest):
        monkeypatch.setattr(module, 'time', clock)
    monkeypatch.setattr(operations, 'inspect_worker', fake_inspect(lambda worker: alive['value'] and worker is not None))
    cfg = dict(active_instance='main', active_profile='mine',
               instances={'main': {'serial': 'fake:1', 'allow_taps': True},
                          'other': {'serial': 'fake:2', 'allow_taps': True}}, presets={})
    env = SimpleNamespace(root=tmp_path, cfg=cfg, spawned=[], rows=[], events=[], keys=iter(range(1, 10_000)),
                          adb={'state': 'device'}, refusal=None, alive=alive, monkeypatch=monkeypatch, clock=clock,
                          source=dict(valid=True, ad=True, floating=False, edit=0), frozen=None)
    env.policy = lambda preset='bp_farm': ledger_h.gem_policy_revision(preset, gather_of(env.source))

    def resolve_source(cfg_, preset):
        s = env.source
        if not s['valid']:
            return dict(preset=preset, label=None, profile=cfg_.get('active_profile'), valid=False,
                        code='gathering_disabled', message='Enable Ad Gems or timed orbit clicks.',
                        policy_revision=None, policy=None, edit=dict(page='runs', blueprint='farm'))
        return dict(preset=preset, label='Farm', profile=cfg_.get('active_profile'), valid=True, code=None,
                    message=None, policy_revision=env.policy(preset), policy=policy(s['ad'], s['floating']),
                    edit=dict(page='runs', blueprint='farm'))

    def spawn(cmd, **kwargs):
        env.spawned.append(cmd)
        return SimpleNamespace(pid=os.getpid())

    def prepare(cfg_, preset):
        if env.refusal:
            raise harvest.Refused(*env.refusal)
        env.frozen = gather_of(env.source)   # the one compile this launch freezes
        return harvest.Prepared(['python', 'gem_collector.py', '--instance', cfg_['active_instance'], '--preset', preset],
                                ledger_h.gem_policy_revision(preset, env.frozen))

    def launch(c, body, cmd):
        frozen = env.frozen if env.frozen is not None else gather_of(env.source)
        envelope = {'preset': {'gather': json.loads(json.dumps(frozen))}}
        return operations.launch(tmp_path, c, 'gems', dict(body, battle_envelope=envelope), cmd, spawn)

    def tick(now=NOW, cfg_=None, **overrides):
        clock.now = now
        args = dict(processes=list(env.rows), resolve_source=resolve_source, prepare=prepare, launch=launch,
                    device_state=lambda identity: env.adb['state'], now=now,
                    event=lambda kind, **fields: env.events.append((kind, fields)))
        args.update(overrides)
        return harvest.tick(tmp_path, cfg_ or cfg, **args)

    def status(now=NOW, cfg_=None, **overrides):
        clock.now = now
        args = dict(processes=list(env.rows), resolve_source=resolve_source, now=now)
        args.update(overrides)
        return harvest.status(tmp_path, cfg_ or cfg, **args)
    env.tick, env.status, env.resolve_source, env.prepare, env.spawn, env.launch = tick, status, resolve_source, prepare, spawn, launch
    return env


def make_psutil(monkeypatch):
    """The process boundary of cancel() for the harvest tests: the pids in state.stand_ins (and this test
    process, which the fake workers use as their pid) are stand-in workers with creation time 1.0, stopped
    through a FakeKernel that only records. Reading them through psutil answers like a running process
    with no family; a psutil terminate/kill of anything fails the test, because stopping goes through
    runtime.process_handle. Set lingering to keep a worker alive after it was terminated. Nothing real is
    ever terminated."""
    import psutil
    from runtime import process_handle
    os_ = install_kernel(monkeypatch)
    os_.stand_ins = {1, 4242, os.getpid()}      # the kernel knows this process too: fake workers use its pid
    psutil_stand_ins = {1, 4242}                 # psutil keeps REAL behaviour for this process (a worker bootstrap asks about itself)
    real = psutil.Process

    class Proc(real):
        def __init__(self, pid=None):
            self._stand_in = pid in psutil_stand_ins
            if self._stand_in:
                self._pid = pid
            else:
                super().__init__(pid)

        def children(self, recursive=False):
            return []

        def parents(self):
            return [] if self._stand_in else super().parents()

        def create_time(self):
            return 1.0 if self._stand_in else super().create_time()

        def is_running(self):
            return True if self._stand_in else super().is_running()

        def terminate(self):
            raise AssertionError('a process must be stopped through runtime.process_handle, never psutil.terminate')

        kill = terminate
    monkeypatch.setattr(psutil, 'Process', Proc)

    class State:
        os = os_
        kernel = process_handle.KERNEL

        @property
        def terminated(self):
            return [pid for pid, _ in os_.kills]

        @property
        def stand_ins(self):
            return os_.stand_ins

        @property
        def lingering(self):
            return os_.lingering

        @lingering.setter
        def lingering(self, value):
            os_.lingering = value
    return State()


def identity(h, cfg=None):
    return harvest.identity_for(cfg or h.cfg)


def request(h, enabled=True, preset='bp_farm', *, key=None, expected=None, cfg=None, context=None):
    ident = identity(h, cfg)
    body = dict(enabled=enabled, idempotency_key=key or f'req-{next(h.keys):06d}',
                expected_context={name: ident[name] for name in (CTX if enabled else ('instance', 'serial'))})
    if context:
        body['expected_context'].update(context)
    if enabled:
        body.update(preset=preset, expected_revision=current_revision(h, cfg) if expected is None else expected)
    elif expected is not None:
        body['expected_revision'] = expected
    return body


def current_revision(h, cfg=None):
    with ledger_h.readonly(operations.ledger_path(h.root)) as db:
        choice = None if db is None else ledger_h.read_choice(db, identity(h, cfg)['device_key'])
    return choice['revision'] if choice else 0


def choose(h, enabled=True, preset='bp_farm', cfg=None, **kwargs):
    return harvest.apply_choice(h.root, cfg or h.cfg, request(h, enabled, preset, cfg=cfg, **kwargs),
                                resolve_source=h.resolve_source, now=NOW)


def active_ops(h):
    return operations.ledger(h.root).active(identity(h)['device_key'])


def finish_last(h, status='failed', ran=5.0, now=NOW, error=None):
    """End the last launched collector, as the worker or reconciliation would."""
    store = operations.ledger(h.root)
    op = active_ops(h)[0]
    if op['status'] == 'accepted':
        op = store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})
    if status == 'cancelled':
        op = store.transition(op['id'], op['revision'], 'cancelling')
    op = store.transition(op['id'], op['revision'], status, **({'error': error} if error else {}))
    with store._db() as db:   # pin its lifetime for the backoff arithmetic
        body = dict(op, created_at=now - ran, updated_at=now)
        db.execute('UPDATE operations SET body=? WHERE id=?', (json.dumps(body, sort_keys=True), op['id']))
    return body


def schema(path):
    db = sqlite3.connect(path)
    try:
        return sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        db.close()


def running(h, op, pid=4242):
    store = operations.ledger(h.root)
    return store.transition(op['id'], op['revision'], 'running', worker={'pid': pid, 'creation_time': 1})


def _collector(h, *, harvest_owned=True, revision=1):
    store = operations.ledger(h.root)
    context = operations.context(h.root, h.cfg, {'preset': 'bp_farm'}, None)
    request_ = {'preset': 'bp_farm', 'harvest': harvest_owned}
    if harvest_owned:
        request_['desire_revision'] = revision
    op, _ = store.accept('gems', context, request_, f'collector-{next(h.keys)}')
    return store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})


def routine(enabled, state='scheduled', **extra):
    """A gem routine record as the driver publishes it (the shape is the driver's; only some fields matter here)."""
    return dict(enabled=enabled, state=state if enabled else 'off', next_due_at=NOW + 20 if enabled else None,
                window_ends_at=None, last_outcome=None, blocker=None if enabled else 'disabled', server_time=NOW - 1, **extra)


def _battle(h, op, *, status='active', observing=True, published=NOW - 1, generation='g1', routines='default',
            controls=None, gather=None, evidence=None):
    store = Battles(operations.ledger_path(h.root))
    preset = {'gather': gather if gather is not None else {'ad_gems': True}}
    context = operations.context(h.root, h.cfg, {}, None)
    battle = store.create(op['id'], context, dict(preset=preset, controls=resolve(preset, {})),
                          generation=generation, origin='adopted')
    if status == 'active':
        store.activate(battle['id'], generation, {'controls': controls if controls is not None else [], 'actions': []})
    if routines == 'default':
        routines = {'ad_gems': {'enabled': True}, 'floating_gems': {'enabled': False}}
    published_at = {} if published is None else {'server_time': published}
    extra = {} if evidence is None else {'collection_evidence': evidence}
    store.update_status(battle['id'], generation, dict(
        observation_status='observing' if observing else 'waiting_for_game_state', routines=routines,
        **published_at, **extra))
    return store, store.get(battle['id'])


def ad_window(h, battle, result, reason=None, *, at, attempts=1):
    """One closed ad-gem window recorded under this battle, as the collector's recorder leaves it
    (a confirmed claim, an attempt nobody could confirm, or a window with no offer)."""
    effect = 'confirmed' if result == 'succeeded' else 'unconfirmed' if attempts else 'none'
    host = dict(driver='collector', operation_id=battle['operation_id'], battle_id=battle['id'], generation=battle['generation'])
    body = new_record(device=battle['context']['device_key'], action='ad_gems', instance='ad_gems', trigger='automatic',
                      occurrence_key=f'window:{at}', host=host,
                      context=dict(device_key=battle['context']['device_key'], account=battle['context'].get('account')),
                      started_at=at - 20)
    return Actions(operations.ledger_path(h.root)).record_terminal(
        body, result=result, reason=reason, effect=effect, attempts=attempts, finished_at=at)


def _run(h):
    store = operations.ledger(h.root)
    context = operations.context(h.root, h.cfg, {}, None)
    op, _ = store.accept('run', context, {}, f'run-{next(h.keys)}')
    return store.transition(op['id'], op['revision'], 'running', worker={'pid': 1, 'creation_time': 1})


FULL = [f'routines.{r}.{f}' for r in ('ad_gems', 'floating_gems') for f in ('enabled', 'interval_sec', 'window_sec')]
OFF_ROUTINES = {'ad_gems': {'enabled': False}, 'floating_gems': {'enabled': False}}


def harvest_overlay_key(battle, revision):
    return f"{ledger_h.OVERLAY_PREFIX}{battle['id']}-{battle['generation']}-{revision}"


class Reached(Exception):
    """Raised by the fake frame grab: the worker got past every guard."""


class EnvSpawn:
    """Process creation stand-in that keeps the environment the worker would have inherited."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), dict(kwargs.get('env') or {})))
        return SimpleNamespace(pid=os.getpid())


def run_worker(root, monkeypatch, call, frame=None):
    """Run the real runtime.operation_worker bootstrap in-process on the real
    backend/gem_collector.py (accepted context restored, lease acquired, guards
    evaluated), with the process table and frames faked (by default the first frame
    grab raises Reached; pass frame to feed the collector's loop instead).
    Returns (order, events, error)."""
    import sys
    from pathlib import Path

    from device import capture
    from runtime import logger, operation_worker, runners
    backend = test_paths.BACKEND
    cmd, env = call
    assert cmd[1:3] == ['-m', 'runtime.operation_worker'] and Path(cmd[3]).name == 'gem_collector.py'
    for key in ('TOWER_OPERATION_ID', 'TOWER_OPERATION_DB'):
        monkeypatch.setenv(key, env[key])
    for key in ('TOWER_BATTLE_ID', 'TOWER_BATTLE_GENERATION', 'TOWER_DEVICE_DELEGATION'):
        monkeypatch.delenv(key, raising=False)
    order, events = [], []

    def runners_table(target=None):
        order.append('runners')
        return iter(())

    def first_frame():
        order.append('frame')
        raise Reached('frame')
    monkeypatch.setattr(runners, 'other_runners', runners_table)
    monkeypatch.setattr(capture, 'grab', frame or first_frame)
    real_event = logger.event

    def event(kind, **fields):
        events.append((kind, dict(fields), dict(logger.current_context()) if hasattr(logger, 'current_context') else {}))
        return real_event(kind, **fields)
    monkeypatch.setattr(logger, 'event', event)
    monkeypatch.setattr(sys, 'argv', ['operation_worker', str(backend / 'gem_collector.py'), *cmd[4:]])
    error = None
    try:
        operation_worker._main()
    except BaseException as exc:   # noqa: BLE001 - classified by the caller
        error = exc
    return order, events, error


def sealed_unresolved(h):
    """A setup battle that was never verified back on Home, sealed as the worker seals it."""
    import hashlib
    from pathlib import Path
    context = operations.context(h.root, h.cfg, {}, None)
    store = operations.ledger(h.root)
    op, _ = store.accept('calibrate', context, {'bootstrap': True, 'flows': True})
    op = store.transition(op['id'], op['revision'], 'running')
    state = {'operation_id': op['id'],
             'setup_battle': {'id': 'battle-1', 'status': 'unknown', 'operation_id': op['id'],
                              'reason': "No fresh matching control for 'open intro sprint dialog'"}}
    raw = json.dumps(state).encode()
    path = Path(context['artifact_dir']) / 'operations' / op['id'] / 'calibrate_state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return store.transition(op['id'], op['revision'], 'failed', result_refs={'calibrate_state.json': {
        'path': str(path), 'revision': hashlib.sha256(raw).hexdigest()}})
