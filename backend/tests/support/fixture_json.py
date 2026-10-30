"""Shared compact writer for contract, action-golden, and frontend sample fixtures.

A recorded fixture holds thousands of cases, and `json.dumps(indent=1)` put every scalar of every
answer on its own line (693k lines for the pages fixture). This writer expands only the first
`INLINE_DEPTH` levels of nesting, one member per line, and writes each container below that on a
single line. For the case fixtures (`cases -> family -> case -> member`) that is one case member per
line, so a diff still names the case and the member that changed.

The layout carries no meaning for these JSON-compatible recorded values: parsing preserves their
data, with numbers spelled as `json.dumps` spells them (60.0 stays 60.0) and keys sorted. The fixtures
listed in `test_fixture_json.py`'s `RECORDED` use this layout through their recorders or contract
tests. Text is UTF-8 with LF line endings on every platform.
"""

import json
from pathlib import Path

INLINE_DEPTH = 4
_INDENT = ' '


def render_fixture_json(value) -> str:
    """The fixture text for `value`: sorted keys, members one per line down to `INLINE_DEPTH`, LF, one final newline."""
    return _render(value, 0) + '\n'


def write_fixture_json(path, value) -> str:
    """Write `value` to `path` as UTF-8 bytes with LF endings (bytes, never write_text: Windows would translate them); returns the text written."""
    text = render_fixture_json(value)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return text


def _flat(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _render(value, depth: int) -> str:
    if not isinstance(value, (dict, list, tuple)) or depth >= INLINE_DEPTH or not value:
        return _flat(value)
    pad, inner = _INDENT * depth, _INDENT * (depth + 1)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError('a recorded fixture names its members with strings')
        members = [f'{inner}{json.dumps(key, ensure_ascii=False)}: {_render(value[key], depth + 1)}' for key in sorted(value)]
        return '{\n' + ',\n'.join(members) + '\n' + pad + '}'
    return '[\n' + ',\n'.join(f'{inner}{_render(item, depth + 1)}' for item in value) + '\n' + pad + ']'
