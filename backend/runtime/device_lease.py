"""Process-lifetime OS ownership of an ADB server/serial, across checkouts.

No timestamp or heartbeat permits stealing ownership. Lock files are never
deleted: unlinking a locked inode would create a second independent owner.
"""
import atexit
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

from runtime import file_lock


class DeviceBusy(RuntimeError):
    pass


class InputRevoked(DeviceBusy):
    """A person revoked automation input for this device (tap permission off)."""


_ROOT = (Path(os.environ.get("PROGRAMDATA", tempfile.gettempdir())) / "TowerPilot" / "device-leases"
         if os.name == "nt" else Path(tempfile.gettempdir()) / "towerpilot-device-leases")
_ENV = "TOWER_DEVICE_DELEGATION"
INPUT_GATE = 1   # the one advertised gate version: what grant() writes and what lease_state accepts
_current = None
_guard = threading.RLock()
_gate_depth = 0


def _lock(path):
    """The OS lock of runtime/file_lock.py, taken once and never waited for: a busy lock is another owner (policy of the lease: never wait, never steal)."""
    try:
        return file_lock.try_lock(path)
    except file_lock.LockBusy as exc:
        raise DeviceBusy("Another process owns this emulator") from exc


def _unlock(handle):
    file_lock.unlock(handle)


class Lease:
    def __init__(self, key, path, handle, delegated=False):
        self.key, self.path, self.handle = key, path, handle
        self.delegated = delegated
        self.pid = os.getpid()
        self.token = secrets.token_hex(32)
        import psutil
        process = psutil.Process()
        self.creation_time = process.create_time()
        self.launchers = {}
        for parent in process.parents():
            try:
                if ("python" in parent.name().lower()
                        and parent.cmdline()[1:] == process.cmdline()[1:]):
                    self.launchers[str(parent.pid)] = parent.create_time()
            except psutil.Error:
                continue

    def grant(self):
        # input_gate: this owner dispatches every input through input_scope(),
        # so a revocation recorded by revoke_input() reaches it and fence_input()
        # can prove that no call is still in flight. Owners without the field
        # predate the gate and cannot be fenced.
        return {"key": self.key, "pid": self.pid, "token": self.token,
                "creation_time": self.creation_time, "launchers": self.launchers,
                "input_gate": INPUT_GATE}

    def check(self):
        if self.handle.closed or self.pid != os.getpid():
            raise DeviceBusy("Device lease is no longer owned by this process")
        if not self.delegated:
            # A delegated scheduler child owns input until it exits.
            probe = _lock(self.path.with_suffix(".activity"))
            _unlock(probe)
        return self

    def release(self):
        global _current
        with _guard:
            if not self.handle.closed:
                _unlock(self.handle)
            if _current is self:
                _current = None

    def __enter__(self):
        return self.check()

    def __exit__(self, *exc):
        self.release()


def _device_key(serial, host="127.0.0.1", port=5037):
    """Use one endpoint identity for acquisition and legacy-owner discovery."""
    if not serial:
        raise ValueError("Cannot own a device without an ADB serial")
    host = str(host).lower()
    if host in {"localhost", "::1"}:
        host = "127.0.0.1"
    return host, int(port), str(serial)


def acquire(serial, host="127.0.0.1", port=5037):
    """Acquire once per process, refusing a second bound device."""
    global _current
    key = _device_key(serial, host, port)
    with _guard:
        if _current is not None:
            if _current.key != key:
                raise DeviceBusy("One device per process: start a separate worker")
            return _current.check()
        digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
        path = _ROOT / (digest + ".lock")
        delegated = False
        inherited = os.environ.get(_ENV)
        if inherited:
            try:
                grant = json.loads(inherited)
                metadata = json.loads(path.with_suffix(".json").read_text())
                import psutil
                ancestors = {p.pid for p in psutil.Process().parents()}
                delegated = (grant == metadata and grant["key"] == list(key)
                             and grant["pid"] in ancestors)
            except (ValueError, OSError, KeyError, TypeError):
                delegated = False
        if delegated:
            handle = _lock(path.with_suffix(".activity"))
            # Parent must still own primary lock. If it exited before the
            # child starts, normal acquisition is required instead.
            try:
                if json.loads(path.with_suffix(".json").read_text()) != grant:
                    raise DeviceBusy("Device delegation was superseded")
            except BaseException:
                _unlock(handle)
                raise
            try:
                probe = _lock(path)
            except DeviceBusy:
                # The activity lock already excludes every fresh owner even
                # if the supervisor exits immediately after this check.
                pass
            else:
                _unlock(probe)
                _unlock(handle)
                delegated = False
        if not delegated:
            handle = _lock(path)
            try:
                probe = _lock(path.with_suffix(".activity"))
                _unlock(probe)
            except BaseException:
                _unlock(handle)
                raise
        lease = Lease(key, path, handle, delegated)
        try:
            metadata_path = path.with_suffix(".activity.json" if delegated else ".json")
            metadata_path.write_text(json.dumps(lease.grant()))
        except BaseException:
            _unlock(handle)
            raise
        _current = lease
        return lease


def ensure_current():
    """Fence input with the actual configured socket endpoint and serial."""
    import settings
    from device import adbclient
    return acquire(settings.instance()["serial"], *adbclient._HOST)


def prepare_instance(name=None):
    """Restore accepted context and own the target before binding/config writes."""
    import settings
    from device import adbclient
    from runtime.operations import restore_worker_context
    restore_worker_context(settings, name)
    return acquire(settings.instance(name)["serial"], *adbclient._HOST)


def _stem(key):
    return _ROOT / (hashlib.sha256(json.dumps(list(key)).encode()).hexdigest() + ".lock")


def _lock_wait(path, timeout):
    """Take a short-held lock, waiting for its current holder to finish."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _lock(path)
        except DeviceBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)


def revocation(key):
    """The recorded revocation for a device, or None. An unreadable marker is
    still a revocation: only an explicit enable removes it."""
    path = _stem(key).with_suffix(".revoked")
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text())
        return record if isinstance(record, dict) else {"unreadable": True}
    except (OSError, ValueError):
        return {"unreadable": True}


def is_revoked(serial, host="127.0.0.1", port=5037):
    return bool(serial) and revocation(_device_key(serial, host, port)) is not None


def revoke_input(serial, host="127.0.0.1", port=5037, reason="tap_permission_revoked"):
    """Refuse every later game input on this device, from every accepted worker
    and delegated child that dispatches through input_scope(). Atomic; the
    observation loop and the battle are untouched. Call fence_input() to learn
    when calls already in flight have finished."""
    key = _device_key(serial, host, port)
    path = _stem(key).with_suffix(".revoked")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"key": list(key), "reason": reason, "revoked_at": time.time(), "token": secrets.token_hex(8)}
    temporary = path.with_suffix(".revoked.tmp")
    try:
        temporary.write_text(json.dumps(record))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    # Success is what a reader sees, not that the calls returned.
    stored = revocation(key)
    if stored is None or stored.get("token") != record["token"]:
        raise OSError("The revocation marker could not be read back after it was written")
    return record


def clear_revocation(serial, host="127.0.0.1", port=5037):
    """An explicit tap enable: input is permitted again."""
    _stem(_device_key(serial, host, port)).with_suffix(".revoked").unlink(missing_ok=True)


def fence_input(serial, host="127.0.0.1", port=5037, timeout=3.0):
    """True once every input call that began before the revocation has ended.

    Every dispatch holds the device's input gate from its revocation check to
    its last byte, so taking the gate after revoke_input() proves that nothing
    already started is still on the wire and everything later sees the
    revocation. False when a call is still in flight after the timeout.
    """
    path = _stem(_device_key(serial, host, port)).with_suffix(".input")
    try:
        gate = _lock_wait(path, timeout)
    except DeviceBusy:
        return False
    _unlock(gate)
    return True


def lease_state(serial, host="127.0.0.1", port=5037):
    """Who holds this device, without taking it: {owned, delegated, input_gate, holders}.

    "Not owned" is ONE observation of both locks, never two. child_environment() promotes a delegated child
    by taking the primary lock BEFORE it lets go of the activity lock, so the device is never unowned, but a
    reader that probed the primary lock, let it go, and only then probed the activity lock could see the
    primary free (before the promotion) and the activity free (after it) and report an owned device free.
    A free primary is therefore kept taken while the activity lock is probed: no promotion (nor any owner,
    all of whom hold the primary lock or the activity lock) can slip between the two probes, and when both
    probes succeeded there was a moment at which this reader held both: nobody else held either.

    input_gate: 'enforced' when the owner dispatches through the gate,
    'unsupported' when it predates it, None when nobody owns the device.
    holders: [{pid, role}] for each held lock whose metadata names a LIVE process whose pid and
    creation time both match (role 'primary' or 'delegated'). A holder whose metadata is missing,
    corrupt or stale is simply not named: the lock is still held, so `owned` stays true. Nothing
    is ever signalled on the strength of this list."""
    key = _device_key(serial, host, port)
    path = _stem(key)
    held = []   # (lock held, the metadata file that holder must have written)
    free_primary = None
    try:
        for lock_path, metadata in ((path, path.with_suffix(".json")),
                                    (path.with_suffix(".activity"), path.with_suffix(".activity.json"))):
            try:
                probe = _lock(lock_path)
            except DeviceBusy:
                held.append((lock_path, metadata))
            else:
                if lock_path == path:
                    free_primary = probe    # a free primary stays taken until the activity probe is done (below)
                else:
                    _unlock(probe)
    finally:
        if free_primary is not None:
            _unlock(free_primary)
    if not held:
        return {"owned": False, "delegated": False, "input_gate": None, "holders": []}
    # Every lock that is held must belong to a holder that positively advertises
    # the gate. A holder whose metadata is missing, corrupt, stale (its process
    # is gone) or pre-gate proves nothing, so it is reported 'unsupported'.
    enforced = all(_advertises_gate(metadata) for _, metadata in held)
    holders = []
    for lock, metadata in held:
        named = _live_holder(metadata)
        if named is not None:
            holders.append({"pid": named, "role": "delegated" if lock.suffix == ".activity" else "primary"})
    return {"owned": True, "delegated": any(lock.suffix == ".activity" for lock, _ in held),
            "input_gate": "enforced" if enforced else "unsupported", "holders": holders}


def _live_holder(metadata):
    """The pid a lock's metadata names, only when that process is running with the recorded creation
    time (a read-only question); otherwise None."""
    from runtime import process_identity
    try:
        grant = json.loads(metadata.read_text())
        pid, created = grant["pid"], grant["creation_time"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return pid if process_identity.matches(pid, created) else None


def _advertises_gate(metadata):
    """True only when this file names a live process (pid and creation time
    match) that declares, with the exact advertised token, that it dispatches input
    through the gate. Truthiness proves nothing: "false", 2 or 1.0 are not it."""
    from runtime import process_identity
    try:
        grant = json.loads(metadata.read_text())
        pid, created, gate = grant["pid"], grant["creation_time"], grant["input_gate"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    if not (gate is True or (type(gate) is int and gate == INPUT_GATE)):
        return False
    return process_identity.matches(pid, created)   # a read-only question: nothing is acted on


def lease_free(serial, host="127.0.0.1", port=5037):
    """True when no process owns this device, which proves a stopped worker is gone."""
    return not lease_state(serial, host, port)["owned"]


@contextmanager
def input_scope(*, game_input=True):
    """Fence the complete input call against scheduler-child takeover and
    against a person's revocation. game_input=False (an ad owner's force-stop)
    still queues behind the gate but is not game input."""
    global _gate_depth
    with _guard:
        lease = ensure_current()
        # Re-entrant inside one process: only the outermost call takes the gate.
        gate = _lock_wait(_stem(lease.key).with_suffix(".input"), 3.0) if _gate_depth == 0 else None
        _gate_depth += 1
        try:
            if game_input and revocation(lease.key) is not None:
                raise InputRevoked("Automation input was revoked for this emulator")
            if lease.delegated:
                yield lease
            else:
                handle = _lock(lease.path.with_suffix(".activity")) if _gate_depth == 1 else None
                try:
                    yield lease
                finally:
                    if handle is not None:
                        _unlock(handle)
        finally:
            _gate_depth -= 1
            if gate is not None:
                _unlock(gate)


def child_environment():
    """Delegate activity to one scheduler child while reserving ownership."""
    lease = ensure_current()
    if lease.delegated:
        # A one-shot supervisor (tier_ladder) may finish by spawning combo.
        # Retain activity ownership until its primary reservation is released,
        # then promote in place; no unowned interval exists between processes.
        deadline = time.monotonic() + 5
        while True:
            try:
                primary = _lock(lease.path)
            except DeviceBusy:
                if time.monotonic() >= deadline:
                    raise DeviceBusy("The supervising owner must exit before this worker can supervise")
                time.sleep(.05)
                continue
            activity = lease.handle
            lease.handle, lease.delegated = primary, False
            try:
                lease.path.with_suffix(".json").write_text(json.dumps(lease.grant()))
            except BaseException:
                lease.handle, lease.delegated = activity, True
                _unlock(primary)
                raise
            _unlock(activity)
            break
    env = dict(os.environ)
    env.pop("TOWER_OPERATION_ID", None)
    env.pop("TOWER_OPERATION_DB", None)
    env.pop("TOWER_BATTLE_ID", None)
    env.pop("TOWER_BATTLE_GENERATION", None)
    env[_ENV] = json.dumps(lease.grant())
    return env


def owns_different_device(pid, target=None):
    """Prove a discovered runner belongs to another leased ADB endpoint.

    Missing, stale or unreadable proof stays conservative. A PID alone cannot
    identify an owner after process exit and PID reuse.
    """
    import settings
    import psutil
    from device import adbclient
    if target is None:
        target = (*adbclient._HOST, settings.instance().get("serial"))
    host, port, serial = target
    if not serial:
        return False
    target = _device_key(serial, host, port)
    try:
        metadata_files = list(_ROOT.glob("*.json"))
        if not metadata_files:
            return False
        started = psutil.Process(pid).create_time()
    except (OSError, psutil.Error):
        return False
    for path in metadata_files:
        try:
            owner = json.loads(path.read_text())
            identified = ((owner.get("pid") == pid and owner.get("creation_time") == started)
                          or owner.get("launchers", {}).get(str(pid)) == started)
            if not identified or tuple(owner.get("key", ())) == target:
                continue
            try:
                lock_path = (path.with_suffix("") if path.name.endswith(".activity.json")
                             else path.with_suffix(".lock"))
                probe = _lock(lock_path)
            except DeviceBusy:
                return True
            else:
                _unlock(probe)
        except (OSError, ValueError, TypeError):
            continue
    return False


def release_current():
    if _current is not None:
        _current.release()


atexit.register(release_current)
