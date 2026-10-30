"""Explicit developer command: capture the dispatch golden traces from the CURRENT code.

    TP_REGENERATE_ACTION_DISPATCH_GOLDEN=1 python -m pytest backend/tests/unit/test_action_dispatch_capture.py

It is skipped in every normal run. The committed fixture was captured on the tree BEFORE the dispatch was derived from the
catalog (`captured_on` holds the hashes of the callers it pinned); regenerating it after the migration would erase the proof,
so the parity tests only ever compare."""
import os

import pytest

import action_dispatch_golden as golden


@pytest.mark.skipif(not os.environ.get(golden.REGENERATE), reason='explicit developer command, never a test side effect')
def test_capture_the_dispatch_golden_traces(tmp_path):
    document = golden.all_traces(tmp_path)
    again = golden.all_traces(tmp_path / 'again')
    for family in document:
        assert golden.roundtrip(document[family]) == golden.roundtrip(again[family]), f'{family} is not deterministic'
    golden.write(document)
    assert golden.load() == golden.roundtrip(document)
