"""runtime/file_lock.py: the OS byte lock extracted from the device lease. The lease's own tests (test_device_lease.py, test_input_revocation.py) still pass
unchanged and prove the lease kept its policy; these prove the primitive."""

import paths as test_paths
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from runtime import file_lock

BACKEND = test_paths.BACKEND


def test_a_second_handle_cannot_take_a_held_lock_and_can_after_it_is_released(tmp_path):
    path = tmp_path / "a.lock"
    first = file_lock.try_lock(path)
    try:
        with pytest.raises(file_lock.LockBusy):
            file_lock.try_lock(path)
    finally:
        file_lock.unlock(first)
    file_lock.unlock(file_lock.try_lock(path))          # free again; and the file was not deleted
    assert path.exists()


def test_the_folder_is_created_and_a_failure_that_is_not_busy_propagates_unchanged(tmp_path):
    file_lock.unlock(file_lock.try_lock(tmp_path / "new" / "deeper" / "x.lock"))
    assert (tmp_path / "new" / "deeper" / "x.lock").exists()
    (tmp_path / "plain").write_text("a file, not a folder")
    with pytest.raises(OSError) as raised:
        file_lock.try_lock(tmp_path / "plain" / "x.lock")
    assert not isinstance(raised.value, file_lock.LockBusy)


def test_lock_waits_for_the_holder_and_takes_it_when_it_is_released(tmp_path):
    path = tmp_path / "w.lock"
    holder = file_lock.try_lock(path)
    threading.Timer(0.3, file_lock.unlock, args=(holder,)).start()
    started = time.monotonic()
    file_lock.unlock(file_lock.lock(path, timeout=5))
    assert 0.2 < time.monotonic() - started < 3, "it must wait for the release, not fail at once and not run to the timeout"


def test_lock_gives_up_after_its_timeout_and_never_steals(tmp_path):
    path = tmp_path / "t.lock"
    holder = file_lock.try_lock(path)
    try:
        started = time.monotonic()
        with pytest.raises(file_lock.LockTimeout):
            file_lock.lock(path, timeout=0.3)
        assert 0.25 < time.monotonic() - started < 2
        with pytest.raises(file_lock.LockBusy):
            file_lock.try_lock(path)                      # the holder still has it
    finally:
        file_lock.unlock(holder)


def test_held_releases_the_lock_when_the_block_raises(tmp_path):
    path = tmp_path / "h.lock"
    kept = []
    with pytest.raises(RuntimeError):
        with file_lock.held(path, timeout=1) as handle:
            kept.append(handle)               # keep the handle alive: otherwise garbage collection would close it and release the lock by accident
            raise RuntimeError("the block failed")
    assert kept[0].closed, "the lock must be released by held() itself"
    file_lock.unlock(file_lock.try_lock(path))


def test_the_os_releases_the_lock_when_the_holder_is_killed(tmp_path):
    path = tmp_path / "k.lock"
    code = ("import sys, time; from runtime import file_lock; h = file_lock.try_lock(sys.argv[1]); print('locked', flush=True); time.sleep(60)")
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], cwd=str(BACKEND.parent), stdout=subprocess.PIPE, text=True,
                             env=dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1"))
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(file_lock.LockBusy):
            file_lock.try_lock(path)
    finally:
        child.kill()                                      # the child this test started
        child.wait(timeout=30)
    file_lock.unlock(file_lock.lock(path, timeout=10))
