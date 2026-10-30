"""Optional incident replay: captured game pixels never ship in the source zip."""
import json
import os
from pathlib import Path

import cv2
import pytest

from interactions import ad_gems


@pytest.fixture
def local_frames(monkeypatch):
    configured = os.environ.get('TOWERPILOT_R15_FIXTURES')
    if not configured:
        pytest.skip('Set TOWERPILOT_R15_FIXTURES to the private local frame archive')
    root = Path(configured)
    assert root.is_dir(), root
    monkeypatch.setattr(ad_gems.settings, 'template_path', lambda rel: root / 'templates' / rel)
    return root


def test_captured_frames_with_real_windows_ocr(local_frames):
    # Real OCR, contours and digit matching; never connect to a device.
    cases = json.loads((local_frames / 'cases.json').read_text(encoding='utf-8'))
    assert cases
    for case in cases:
        frame = cv2.imread(str(local_frames / case['frame']))
        assert frame is not None, case['frame']
        result = ad_gems.find_claim_rect(frame)
        assert result == (tuple(case['rect']) if case['rect'] is not None else None), case['frame']
