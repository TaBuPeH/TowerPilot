"""Shared stdlib-only JSONL diagnostics and scoped correlation. No device state."""
import json
import os
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_started = time.strftime("%Y%m%d_%H%M%S")
_process_id = uuid.uuid4().hex
_session_id = os.environ.get('TOWER_SESSION_ID') or _process_id
_sequence = 0
_write_errors = 0
_last_write_error = None
_lock = threading.RLock()
_context = ContextVar('diagnostic_context', default=None)


def file_name():
    return f"events_{_started}_{os.getpid()}_{_process_id[:8]}.jsonl"


def health():
    """Expose this process's persistent write failures even without stderr."""
    with _lock:
        return {'process_id': _process_id, 'pid': os.getpid(),
                'write_errors': _write_errors,
                'last_error': dict(_last_write_error) if _last_write_error else None}


def current_context():
    """Return a copy of the active correlation fields (safe to pass to workers)."""
    inherited = {key: os.environ[env] for key, env in (
        ('operation_id', 'TOWER_OPERATION_ID'), ('request_id', 'TOWER_REQUEST_ID'),
        ('trace_id', 'TOWER_TRACE_ID'), ('parent_span_id', 'TOWER_PARENT_SPAN_ID'),
    ) if os.environ.get(env)}
    return {'trace_id': _session_id, **inherited, **(_context.get() or {})}


@contextmanager
def context(**fields):
    """Nest correlation fields without leaking them into other threads/requests."""
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield current_context()
    finally:
        _context.reset(token)


def write_event(path_resolver, kind: str, **fields):
    """Append one raw record; diagnostics must never cause an input to be retried.

    If the local file is unavailable, emit the record and storage failure to
    stderr. Keep returning the record so business behavior stays unchanged.
    """
    global _sequence, _write_errors, _last_write_error
    with _lock:
        _sequence += 1
        wall = time.time()
        rec = {**current_context(), **fields, 't': round(wall, 3), 'kind': kind,
               'event_id': uuid.uuid4().hex, 'process_id': _process_id,
               'session_id': _session_id, 'pid': os.getpid(), 'seq': _sequence,
               'wall_time': wall, 'monotonic_ns': time.monotonic_ns()}
        try:
            line = json.dumps(rec, default=str, ensure_ascii=True) + '\n'
            path = Path(path_resolver())
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as f:
                f.write(line)
        except Exception as exc:
            _write_errors += 1
            _last_write_error = {'exception_type': type(exc).__name__, 'message': str(exc),
                                 't': rec['t'], 'event_id': rec['event_id']}
            # Logging is deliberately best-effort at the control boundary. A
            # failure after successful adb dispatch must never prompt a retry.
            fallback = {'kind': 'diagnostic_write_error', 'error_type': type(exc).__name__,
                        'error': str(exc), 'record': rec}
            try:
                message = json.dumps(fallback, default=str, ensure_ascii=True) + '\n'
            except (TypeError, ValueError):
                message = f'diagnostic_write_error: {exc!r}; record={rec!r}\n'
            for stream in (sys.stderr, sys.stdout):
                if stream is None:
                    continue
                try:
                    stream.write(message)
                    stream.flush()
                    break
                except (OSError, ValueError, TypeError):
                    continue
            else:
                try:
                    os.write(2, message.encode('utf-8', errors='replace'))
                except OSError:
                    # pythonw may have no streams or fd 2. No output channel
                    # remains; do not turn a log failure into repeated input.
                    pass
    return rec


def event_to(directory, kind: str, **fields):
    """Write to an explicit local log directory without importing runtime settings."""
    return write_event(lambda: Path(directory) / file_name(), kind, **fields)


class Attempt:
    """An invocation outcome, finalized on scope exit, after exceptions are known."""

    def __init__(self, name, fields):
        self.name = name
        self.fields = fields
        self.attempt_id = uuid.uuid4().hex
        self.span_id = self.attempt_id
        self.outcome = None
        self.result = {}

    def finish(self, outcome, **fields):
        self.outcome = outcome
        self.result.update(fields)


@contextmanager
def attempt(name, *, emit, **fields):
    """Record start/end and exceptions for one invocation, preserving exceptions.

    Call finish('refused'/'dry_run'/'dispatched'/...) for domain outcomes. A
    normal return defaults to 'completed'; it does not imply game reward success.
    """
    handle = Attempt(name, fields)
    parent = current_context().get('span_id') or current_context().get('parent_span_id')
    started = time.monotonic_ns()
    with context(span_id=handle.span_id, parent_span_id=parent, attempt_id=handle.attempt_id):
        emit('attempt_start', **{**fields, 'action': name})
        error = {}
        try:
            yield handle
        except BaseException as exc:
            clean_exit = isinstance(exc, SystemExit) and exc.code in (None, 0) and handle.outcome == 'exited'
            if not clean_exit:
                error = {'exception_type': type(exc).__name__, 'exception_message': str(exc),
                         'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
            if not clean_exit and handle.outcome not in (
                    'refused', 'failed', 'failure', 'error', 'cancelled', 'interrupted', 'restoration_required'):
                handle.outcome = 'error'
            raise
        finally:
            emit('attempt_end', **{**fields, **handle.result, **error,
                  'action': name, 'outcome': handle.outcome or 'completed',
                  'duration_ms': round((time.monotonic_ns() - started) / 1_000_000, 3)})
