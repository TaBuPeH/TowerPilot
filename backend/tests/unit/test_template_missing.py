"""Absent optional templates stay quiet and become usable after calibration."""
import cv2
import numpy as np
import pytest

from player import accounts
from vision import detect


def test_missing_template_does_not_call_decoder_and_can_be_added_later(tmp_path, monkeypatch, capfd):
    path = tmp_path / "learned.png"
    monkeypatch.setattr(accounts, "template_path", lambda *a: path)
    monkeypatch.setattr(detect, "_TPL_CACHE", {})
    for _ in range(3):
        with pytest.raises(detect.TemplateMissing):
            detect._tpl("learned.png")
    assert "imread_" not in capfd.readouterr().err
    expected = np.full((8, 8, 3), 123, dtype=np.uint8)
    assert cv2.imwrite(str(path), expected)
    np.testing.assert_array_equal(detect._tpl("learned.png"), expected)


def test_corrupt_template_is_still_missing(tmp_path, monkeypatch):
    path = tmp_path / "broken.png"
    path.write_text("not a PNG", encoding="utf-8")
    monkeypatch.setattr(accounts, "template_path", lambda *a: path)
    monkeypatch.setattr(detect, "_TPL_CACHE", {})
    with pytest.raises(detect.TemplateMissing):
        detect._tpl("broken.png")
