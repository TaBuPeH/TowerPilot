import cv2
import numpy as np
from vision import installed_art


def test_installed_art_is_restricted_to_manifest_region(monkeypatch):
    # Procedural feature-rich artwork, no game image in the test suite.
    rng=np.random.default_rng(12)
    source=rng.integers(0,256,(120,120,3),dtype=np.uint8)
    source=cv2.GaussianBlur(source,(3,3),0)
    monkeypatch.setattr(installed_art,'_images',lambda *args:(source,))
    monkeypatch.setattr(installed_art,'manifest',lambda:{'asset_bindings':{'sw':{'search':[10,1300,170,140]}}})
    frame=np.zeros((2560,1080,3),np.uint8)
    frame[1305:1425,30:150]=source
    assert installed_art.match(frame,'sw')[0]
    frame[:]=0
    frame[1460:1580,30:150]=source  # same art in ability row is NOT immunity
    assert not installed_art.match(frame,'sw')[0]
    assert not installed_art.match(np.zeros((100,100,3),np.uint8),'sw')[0]
