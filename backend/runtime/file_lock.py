"""One OS byte lock on a file: the primitive every cross-process mutual exclusion in the backend uses.

It was written for the device lease (runtime/device_lease.py) and is extracted here, behaviour for behaviour, so the daily state
(scheduling/daystate.py) takes the same lock instead of a copy of it. The lock is advisory and taken on byte 0 of the file; the OS releases it when the holder
exits or is killed, so a crashed holder can never leave it held. Lock files are NEVER deleted: unlinking a locked inode would let a second process lock a new
file and own the same thing as the first.

Policy stays with the caller. `try_lock` never waits; the device lease uses it as it always did (never wait, never steal, never judge by a timestamp). `lock` waits a
bounded time, which the daily state uses; it does not steal either.
"""
import contextlib
import os
import time
from pathlib import Path


class LockBusy(OSError):
    """Another handle (this process's or another's) holds the lock."""


class LockTimeout(LockBusy):
    """The lock stayed held for the whole bounded wait."""


def try_lock(path) -> object:
    """Take the lock now or raise LockBusy. Returns the open handle that holds it; pass it to `unlock`.

    Errors other than "held" (the folder cannot be created, the file cannot be opened) propagate as they are."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b", buffering=0)
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            # Windows permits locking a byte beyond EOF.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise LockBusy(f"{path.name} is locked by another handle") from exc
    return handle


def unlock(handle) -> None:
    """Release a lock taken by `try_lock` or `lock`, and close the handle."""
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def lock(path, timeout: float, poll: float = 0.005) -> object:
    """Take the lock, retrying for at most `timeout` seconds; raises LockTimeout. The retry interval grows from `poll` to 50 ms."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    pause = poll
    while True:
        try:
            return try_lock(path)
        except LockBusy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LockTimeout(f"{Path(path).name} was still locked after {timeout:g} s") from None
            time.sleep(min(pause, remaining))
            pause = min(pause * 2, 0.05)


@contextlib.contextmanager
def held(path, timeout: float):
    """`with held(path, 10):` - the lock for the block, or LockTimeout."""
    handle = lock(path, timeout)
    try:
        yield handle
    finally:
        unlock(handle)
