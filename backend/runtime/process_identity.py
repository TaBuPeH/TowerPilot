"""Is this process the one a record names?

A pid alone names nothing: the operating system reuses it. A process is its pid AND its
creation time. Anything that stops, signals or waits for a RECORDED process (an
operation's worker, a listed runner) must therefore:

  1. open the pid exactly ONCE,
  2. compare that object's creation time with the recorded one, and
  3. act on THAT SAME object, never on a second one opened by pid afterwards.

For READING (is it alive, what is its parent) the object is a psutil.Process (open_verified
below). For DESTROYING (terminate, kill, a family stop) a psutil.Process is not enough: its
terminate re-opens the pid at kill time, so a pid reused after its last creation-time read is
killed in its place. Destructive actions go through runtime.process_handle, which verifies and
terminates through one kernel handle (an open handle keeps the pid from being reused).

The answer is three-valued, never two:
  True   the recorded process is running (the verified object is returned with it);
  False  PROVEN gone: no such process, a different process now owns the pid, or it exited;
  None   UNKNOWN: the identity cannot be proven (access denied, any other OS/psutil failure, a
         recorded identity that is malformed, non-finite or lacks its creation time). Unknown
         is never read as gone and never acted on: nothing is terminated on a guess."""
import math

CREATED_ULPS = 8   # how many floating-point steps two readings of ONE process start may differ by; see same_created
MAX_PID = 0xFFFFFFFF   # a Windows process id is a 32-bit DWORD
MAX_CREATED = 253402300799.0   # 9999-12-31T23:59:59Z: no process starts later, so a larger number is not a time


def usable_pid(pid):
    """An integer pid in 1..MAX_PID. A bool, a string, 0, a negative or an absurdly large number
    names no process. Never raises."""
    return type(pid) is int and 0 < pid <= MAX_PID


def usable_created(created):
    """A creation time that can identify a process: a finite number above zero and no later than
    MAX_CREATED (not a bool, NaN, infinity, a string, None, or an integer too large to be a float,
    whose conversion would raise). Never raises."""
    if type(created) not in (int, float):
        return False
    try:
        value = float(created)
    except OverflowError:   # an int beyond the float range (10**400)
        return False
    return math.isfinite(value) and 0 < value <= MAX_CREATED


def usable(pid, created):
    """A recorded identity that can be judged at all."""
    return usable_pid(pid) and usable_created(created)


def same_created(left, right):
    """Do two creation times name the same process start? Both must be usable.

    The tolerance is arithmetic, not a time window. Windows stores a process start as a FILETIME in 100 ns
    ticks. psutil converts it to a float of epoch seconds in C; runtime.process_handle reads the same
    FILETIME from a handle and converts it in Python. A double at today's epoch has a step (ulp) of
    about 2.4e-7 s, i.e. 2.4 ticks, so the two correct conversions of ONE tick can land a few steps
    apart. Measured on this machine over this process and 12 children, psutil and the handle differed by
    at most 4 steps (9.5e-7 s) and exactly 0 between two psutil reads; CREATED_ULPS = 8 doubles that margin
    and is about 1.9e-6 s. It has to stay far below the time any other process needs to take the same pid:
    the earlier owner must exit and its pid be reissued (milliseconds at the very least), which is why a
    replacement a millisecond later is NOT the same process. How fast a pid is reissued in practice was
    not measured; the argument is only that it cannot be reissued within microseconds."""
    if not (usable_created(left) and usable_created(right)):
        return False
    return abs(left - right) <= CREATED_ULPS * math.ulp(max(left, right))


def open_verified(pid, created):
    """(answer, process): see the module docstring. process is the verified psutil.Process
    when answer is True and None otherwise. Opens the pid once. The object is for READING
    (its family, its pid): nothing may be terminated, killed or signalled through it, because
    psutil re-opens the pid to do that. Destructive actions are runtime.process_handle's."""
    if not usable(pid, created):
        return None, None
    import psutil
    try:
        process = psutil.Process(pid)
        if not same_created(process.create_time(), created):
            return False, None   # another process owns this pid now: the recorded one is gone
        return (True, process) if process.is_running() else (False, None)
    except psutil.NoSuchProcess:   # includes ZombieProcess
        return False, None
    except (psutil.Error, OSError, ValueError, OverflowError, TypeError):
        return None, None


def pid_absent(pid):
    """True only when `pid` is a usable pid that NO process currently has (NoSuchProcess). Anything
    else, including a process that exists but cannot be inspected, is False: absence is proven,
    never assumed. It says nothing about WHICH process the pid once belonged to, so it can only
    corroborate a record that has no creation time; it never identifies a live process."""
    if not usable_pid(pid):
        return False
    import psutil
    try:
        psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError, ValueError, OverflowError, TypeError):
        return False
    return False


def matches(pid, created):
    """True only when the recorded process is running and proven to be the recorded one.
    For read-only questions; to ACT on a process, use runtime.process_handle."""
    return open_verified(pid, created)[0] is True
