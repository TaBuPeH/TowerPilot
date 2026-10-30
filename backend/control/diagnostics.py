"""Bounded, read-only queries over local raw diagnostic events."""
import json
import math
from pathlib import Path

FILTERS = {'trace_id', 'request_id', 'operation_id', 'battle_id', 'attempt_id',
           'claim_attempt_id', 'sweep_id', 'span_id', 'window_id', 'offer_id', 'command_id', 'kind', 'instance'}
MAX_FILES = 64
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def log_root(root):
    """The application's local event store; no device or settings imports."""
    return Path(root) / 'logs'


def is_failure(record):
    """Include refusals/unconfirmed attempts without relabeling their raw outcome."""
    outcome = str(record.get('outcome', '')).lower()
    reason = str(record.get('reason', '')).lower()
    kind = str(record.get('kind', '')).lower()
    return bool(record.get('error') or record.get('exception_type')
                or record.get('confirmed') is False or record.get('ok') is False
                or record.get('level') in ('error', 'warning')
                or outcome in ('error', 'failed', 'refused', 'unconfirmed', 'timeout', 'blocked', 'skipped')
                or reason in ('timeout', 'unconfirmed', 'failed')
                or any(word in kind for word in ('_failed', '_error', '_refused', '_skip')))


def query(root, params):
    """Return the newest matching events with explicit bounded-scan coverage."""
    unknown = set(params) - FILTERS - {'limit', 'since', 'failures'}
    if unknown:
        raise ValueError('Unknown diagnostic filter: ' + ', '.join(sorted(unknown)))
    try:
        limit = int(params.get('limit', 250))
        since = float(params.get('since', 0))
    except (TypeError, ValueError) as exc:
        raise ValueError('Diagnostic limit and since must be numbers') from exc
    if not 1 <= limit <= 1000 or not math.isfinite(since) or since < 0:
        raise ValueError('Diagnostic limit must be 1–1000 and since a finite timestamp')
    if params.get('failures', '0') not in ('0', '1'):
        raise ValueError('failures must be 0 or 1')
    filters = {key: value for key, value in params.items() if key in FILTERS and value}
    if any(not isinstance(value, str) or len(value) > 200 for value in filters.values()):
        raise ValueError('Diagnostic filters must be text of at most 200 characters')
    directory = log_root(root).resolve()
    candidates, errors = [], []
    for path in directory.glob('*/events_*.jsonl'):
        try:
            if not path.resolve().is_relative_to(directory):
                errors.append({'file': path.name, 'reason': 'outside_log_root'})
                continue
            candidates.append((path.stat().st_mtime_ns, path))
        except OSError as exc:
            errors.append({'file': path.name, 'reason': str(exc)})
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    records, scanned, bytes_read, malformed = [], 0, 0, 0
    truncated = len(candidates) > MAX_FILES
    for _, path in candidates[:MAX_FILES]:
        remaining = MAX_TOTAL_BYTES - bytes_read
        if remaining <= 0:
            truncated = True
            break
        try:
            with path.open('rb') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                start = max(0, size - min(MAX_FILE_BYTES, remaining))
                stream.seek(start)
                data = stream.read(min(MAX_FILE_BYTES, remaining))
            scanned += 1
            bytes_read += len(data)
            truncated = truncated or start > 0
            if start:
                # The bounded tail may begin in the middle of a UTF-8 record.
                split = data.find(b'\n')
                start += split + 1 if split >= 0 else len(data)
                data = data[split + 1:] if split >= 0 else b''
            offset = start
            for line in data.splitlines(keepends=True):
                position, offset = offset, offset + len(line)
                if not line.endswith(b'\n'):
                    # A concurrent writer may not have finished its last record.
                    truncated = True
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError('Event is not an object')
                    timestamp = float(record.get('t', 0))
                    if not math.isfinite(timestamp):
                        raise ValueError('Event timestamp is not finite')
                except (UnicodeError, ValueError, TypeError):
                    malformed += 1
                    continue
                instance = record.get('instance', path.parent.name)
                def field(key):
                    if key == 'instance':
                        return instance
                    return record.get(key, '')
                def matches(key, value):
                    if key == 'attempt_id':
                        return value in (str(record.get('attempt_id', '')),
                                         str(record.get('claim_attempt_id', '')))
                    return str(field(key)) == value
                if timestamp < since or any(not matches(key, value) for key, value in filters.items()):
                    continue
                if params.get('failures') == '1' and not is_failure(record):
                    continue
                records.append({**record, '_log': {'file': path.relative_to(directory).as_posix(),
                                                  'offset': position, 'failure': is_failure(record)}})
        except OSError as exc:
            errors.append({'file': path.relative_to(directory).as_posix(), 'reason': str(exc)})
    records.sort(key=lambda row: (float(row.get('t', 0)), row['_log']['file'], row['_log']['offset']))
    matched = len(records)
    return {'events': records[-limit:], 'coverage': {'files_scanned': scanned, 'bytes_read': bytes_read,
            'matched_in_scan': matched, 'returned': min(matched, limit),
            'truncated': truncated or matched > limit, 'malformed_records': malformed,
            'errors': errors, 'max_files': MAX_FILES, 'max_bytes': MAX_TOTAL_BYTES}}
