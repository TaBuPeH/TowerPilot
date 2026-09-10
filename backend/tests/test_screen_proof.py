import numpy as np

from player.screen_proof import VisualAnchors


def test_visual_screen_proof_rejects_dimmed_modal_and_changed_tab():
    frame=np.random.default_rng(15).integers(50,250,(200,200,3),dtype=np.uint8)
    anchors=[{'rect':[10,10,60,40]}, {'rect':[100,10,60,40]}]
    proof=VisualAnchors()
    assert not proof.matches('screen',frame,anchors)
    proof.remember('screen',frame,anchors)
    assert proof.matches('screen',frame,anchors)
    assert not proof.matches('screen',frame//2,anchors)
    changed=frame.copy();changed[10:50,100:160]=0
    assert not proof.matches('screen',changed,anchors)


def test_scroll_below_anchors_does_not_require_another_text_read():
    frame=np.random.default_rng(6).integers(0,255,(200,200,3),dtype=np.uint8)
    anchors=[{'rect':[0,0,200,40]}]
    proof=VisualAnchors();proof.remember('screen',frame,anchors)
    frame[50:]=0
    assert proof.matches('screen',frame,anchors)
