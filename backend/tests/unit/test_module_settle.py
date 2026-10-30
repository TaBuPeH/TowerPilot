import pytest
from player import module_descriptor as descriptor


def test_opening_animation_requires_two_consecutive_stable_bindings(monkeypatch):
    boxes = iter([None, [100, 200, 150, 150], [100, 210, 150, 150],
                  [100, 210, 150, 150]])
    def locate(*args):
        box = next(boxes)
        if box is None:
            raise RuntimeError('opening')
        return box
    monkeypatch.setattr(descriptor, 'locate_icon', locate)
    frames = iter(['moving', 'settling', 'stable'])
    assert descriptor.settled_panel(None, 'first', lambda: next(frames), lambda _: None) == 'stable'


def test_unmatched_description_never_becomes_verified(monkeypatch):
    def miss(*args):
        raise RuntimeError('different icon')
    monkeypatch.setattr(descriptor, 'locate_icon', miss)
    with pytest.raises(RuntimeError, match='did not settle'):
        descriptor.settled_panel(None, None, lambda: None, lambda _: None, attempts=3)
