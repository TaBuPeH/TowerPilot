"""Explicit developer command: capture the golden traces from the CURRENT code.

    TP_REGENERATE_ACTION_GOLDEN=1 python -m pytest backend/tests/unit/test_action_golden_capture.py

It is skipped in every normal run. The committed fixture was captured on the tree BEFORE the action modules
existed (`captured_on` holds the hashes of the callers it pinned); regenerating it after a migration would
erase the proof, so the parity tests only ever compare."""
import os

import pytest

import action_golden as golden
from harvest_support import make_env


@pytest.mark.skipif(not os.environ.get(golden.REGENERATE), reason='explicit developer command, never a test side effect')
def test_capture_the_golden_traces(tmp_path, monkeypatch, dash):
    baseline = golden.baseline_snapshots(tmp_path, dash)       # first: make_env below swaps the ledger modules' clock
    document = dict(version=1, captured_on=golden.source_hashes(), baseline=baseline,
                    ad_gems=golden.gem_traces('ad_gems'), floating_gems=golden.gem_traces('floating_gems'),
                    shopping=golden.shopping_traces())
    cases = iter(range(1000))
    make_h = lambda: make_env(tmp_path / f'case-{next(cases)}', monkeypatch)
    document['harvest'] = golden.harvest_traces(make_h())
    document['harvest'].update(golden.harvest_spacing_traces(make_h))
    golden.write(document)
    assert golden.load() == golden.roundtrip(document)