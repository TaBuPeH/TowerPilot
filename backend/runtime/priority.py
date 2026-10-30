"""This process's Windows priority class: the one owner of "one class above normal".

CPU-bound work at normal priority elsewhere on the machine (builds, test suites) starves a short-deadline loop. The
gem collector raises its own class at startup; the warm OCR reader is started at the same class (`vision.textocr`).
Never HIGH or REALTIME: those can starve the system. Failure or a non-Windows host is a result, never an exception."""
import subprocess
import sys

ABOVE_NORMAL = getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0)


def _set_own_class(flag):
    """The OS call, kept apart so tests fake it. Raises OSError when Windows refuses."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    if not kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), flag):
        raise ctypes.WinError(ctypes.get_last_error())


def raise_own_priority():
    """Raise this process to ABOVE_NORMAL. Returns {'ok': bool, 'priority': 'above_normal'|None, 'reason': str}."""
    if sys.platform != "win32" or not ABOVE_NORMAL:
        return {"ok": False, "priority": None, "reason": "not_windows"}
    try:
        _set_own_class(ABOVE_NORMAL)
    except Exception as exc:
        return {"ok": False, "priority": None, "reason": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "priority": "above_normal", "reason": ""}