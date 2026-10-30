"""Hard rule 4 at the combo handoff boundary: hold over any screen the handoff does not own.

Finite and offline: the screen, the clock and the logger are fakes. Nothing
reaches a device or a live port.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from scheduling import combo

ALLOWED = ['home', 'battle', 'game_stats', 'exit_battle', 'end_round']
MENUS = ['cards', 'modules', 'guild', 'tournament', 'tournament_stats', 'reward_listing', 'welcome_back', 'intro_sprint_end']


class Stop(Exception):
    """Raised by the fake sleep once the script has run out of polls."""


@pytest.fixture
def world(monkeypatch):
    """Script of identify results (str or Exception), a virtual clock, and recorded events."""
    from device import capture
    from vision import screen
    w = SimpleNamespace(script=[], events=[], sleeps=[], now=1000.0, grabs=0, taps=[])

    def grab():
        w.grabs += 1
        return object()

    def identify(frame):
        step = w.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(name=step)

    def sleep(sec):
        w.sleeps.append(sec)
        w.now += sec
        if not w.script:
            raise Stop()

    monkeypatch.setattr(capture, 'grab', grab)
    monkeypatch.setattr(screen, 'identify', identify)
    monkeypatch.setattr(combo.time, 'sleep', sleep)
    monkeypatch.setattr(combo.time, 'monotonic', lambda: w.now)
    monkeypatch.setattr(combo.logger, 'event', lambda kind, **f: w.events.append((kind, f)))
    from device import act
    monkeypatch.setattr(act, 'tap', lambda *a, **k: w.taps.append(a))
    return w


def kinds(w, kind):
    return [f for k, f in w.events if k == kind]


def hold_until_exhausted(w):
    """Run the hold; returns True when it released, False when it was still holding at the end."""
    try:
        combo._hold_while_unknown()
    except Stop:
        return False
    return True


def test_a_recognized_menu_is_held_with_a_repeating_visible_event_and_no_tap(world):
    world.script = ['cards'] * 4
    assert hold_until_exhausted(world) is False
    holds = kinds(world, 'combo_handoff_hold')
    assert [h['screen'] for h in holds] == ['cards'] * 4
    assert all(h['reason'] for h in holds)
    assert [h['held_sec'] for h in holds] == [0, 20, 40, 60]   # grows with the virtual clock
    assert world.sleeps == [combo.POLL] * 4
    assert world.taps == []
    assert kinds(world, 'combo_handoff_release') == []


@pytest.mark.parametrize('name', MENUS)
def test_every_other_recognized_screen_keeps_holding(world, name):
    world.script = [name] * 3
    assert hold_until_exhausted(world) is False
    assert len(kinds(world, 'combo_handoff_hold')) == 3
    assert world.taps == [] and kinds(world, 'combo_handoff_release') == []


@pytest.mark.parametrize('name', ALLOWED)
def test_each_handoff_screen_releases_at_once_and_silently(world, name):
    world.script = [name]
    assert hold_until_exhausted(world) is True
    assert world.events == [] and world.sleeps == []


def test_unknown_keeps_holding_and_says_why(world):
    world.script = ['unknown'] * 3
    assert hold_until_exhausted(world) is False
    holds = kinds(world, 'combo_handoff_hold')
    assert [h['screen'] for h in holds] == ['unknown'] * 3
    assert [h['held_sec'] for h in holds] == [0, 20, 40]
    assert world.taps == [] and kinds(world, 'combo_handoff_release') == []


def test_unknown_and_a_named_menu_give_different_reasons(world):
    world.script = ['unknown', 'cards']
    hold_until_exhausted(world)
    unknown, menu = kinds(world, 'combo_handoff_hold')
    assert unknown['reason'] and menu['reason'] and unknown['reason'] != menu['reason']

def test_screen_read_errors_keep_holding_and_never_release(world):
    world.script = [RuntimeError('adb hiccup'), OSError('gone'), RuntimeError('again')]
    assert hold_until_exhausted(world) is False
    assert len(kinds(world, 'combo_hold_err')) == 3
    assert kinds(world, 'combo_handoff_release') == [] and world.taps == []


@pytest.mark.parametrize('menu', ['cards', 'unknown'])
@pytest.mark.parametrize('allowed', ALLOWED)
def test_menu_then_handoff_screen_releases_exactly_once(world, menu, allowed):
    world.script = [menu, menu, allowed]
    assert hold_until_exhausted(world) is True
    assert len(kinds(world, 'combo_handoff_hold')) == 2
    release = kinds(world, 'combo_handoff_release')
    assert release == [{'screen': allowed, 'held_sec': 2 * int(combo.POLL)}]


def test_the_next_step_runs_once_and_only_after_the_release(world):
    """Mirrors run(): the hold, then the day-plan step (tourney.ensure_home) that taps."""
    world.script = ['cards', 'cards', 'home']
    steps = []
    real_event = combo.logger.event

    def spy(kind, **f):
        real_event(kind, **f)
        if kind == 'combo_handoff_release':
            steps.append('released')

    combo.logger.event = spy
    try:
        combo._hold_while_unknown()
    finally:
        combo.logger.event = real_event
    steps.append('ensure_home')
    assert steps == ['released', 'ensure_home']
    assert world.taps == [] and world.script == []

def test_a_read_error_then_a_handoff_screen_releases_without_a_tap(world):
    # Errors fail closed (no release while the screen is unreadable); the hold
    # event and held_sec clock start only once a screen is actually read.
    world.script = [RuntimeError('adb hiccup'), 'home']
    assert hold_until_exhausted(world) is True
    assert len(kinds(world, 'combo_hold_err')) == 1 and world.taps == []


def test_run_holds_before_adopting_and_before_the_handoff():
    """Both scheduler call sites of the hold stay in run() (the delegated tests stub the helper)."""
    tree = ast.parse(Path(combo.__file__).read_text(encoding='utf-8'))
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    calls = [(c.lineno, c.func.id) for c in ast.walk(run) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    holds = [ln for ln, name in calls if name == '_hold_while_unknown']
    handoff = [ln for ln, name in calls if name == '_block_handoff']
    assert len(holds) == 2 and handoff and min(handoff) > holds[1]
