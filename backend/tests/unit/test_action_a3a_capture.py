"""Explicit developer command: capture the A3a golden traces from the CURRENT code.

    TP_REGENERATE_ACTION_A3A_GOLDEN=1 python -m pytest backend/tests/unit/test_action_a3a_capture.py

Skipped in every normal run. The committed fixture was captured BEFORE the A3a producers were replaced by schedule policies (`captured_on` holds the
hashes of the callers it pinned); regenerating it after the migration would erase the proof, so the parity tests only ever compare."""
import os

import pytest

import action_a3a_golden as golden


@pytest.mark.skipif(not os.environ.get(golden.REGENERATE), reason='explicit developer command, never a test side effect')
def test_capture_the_a3a_golden_traces():
    document = golden.all_traces()
    again = golden.all_traces()
    for family in document:
        assert golden.roundtrip(document[family]) == golden.roundtrip(again[family]), f'{family} is not deterministic'
    golden.write(document)
    assert golden.load() == golden.roundtrip(document)
