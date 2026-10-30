"""The gem collector raises its own priority class (one owner: runtime.priority); the restart task is not BelowNormal."""
import re

import pytest

from runtime import priority


def test_raise_asks_for_above_normal_only(monkeypatch):
    calls = []
    monkeypatch.setattr(priority.sys, 'platform', 'win32')
    monkeypatch.setattr(priority, 'ABOVE_NORMAL', 0x8000)
    monkeypatch.setattr(priority, '_set_own_class', calls.append)
    assert priority.raise_own_priority() == {'ok': True, 'priority': 'above_normal', 'reason': ''}
    assert calls == [0x8000]                      # never HIGH (0x80) or REALTIME (0x100)


def test_refusal_and_other_platforms_are_results_not_errors(monkeypatch):
    monkeypatch.setattr(priority.sys, 'platform', 'win32')
    monkeypatch.setattr(priority, 'ABOVE_NORMAL', 0x8000)

    def refuse(flag):
        raise OSError('access denied')
    monkeypatch.setattr(priority, '_set_own_class', refuse)
    result = priority.raise_own_priority()
    assert result['ok'] is False and 'access denied' in result['reason']
    monkeypatch.setattr(priority.sys, 'platform', 'linux')
    monkeypatch.setattr(priority, '_set_own_class', lambda flag: pytest.fail('no OS call off Windows'))
    assert priority.raise_own_priority()['reason'] == 'not_windows'


def test_main_logs_the_class_after_binding_the_instance_and_a_failure_never_stops_it(monkeypatch):
    from gem_collector_run import Exhausted, run
    import gem_collector as worker
    order = []
    monkeypatch.setattr(worker.settings, 'select_instance', lambda *a: order.append('select'))
    monkeypatch.setattr(worker.priority, '_set_own_class', lambda flag: (_ for _ in ()).throw(OSError('denied')))
    monkeypatch.setattr(worker.priority.sys, 'platform', 'win32')
    monkeypatch.setattr(worker.priority, 'ABOVE_NORMAL', 0x8000)
    events, _, _, _ = (None,) * 4
    try:
        events, *_ = run(monkeypatch, ['battle'])
    except Exhausted:
        pytest.fail('run swallows Exhausted')
    names = [name for name, kw in events]
    assert 'gem_collector_priority' in names and 'gem_collector_started' in names   # a refusal did not stop it
    assert dict(events)['gem_collector_priority']['ok'] is False

def test_textocr_warm_reader_uses_the_same_class():
    from vision import textocr
    assert textocr.URGENT_PRIORITY == priority.ABOVE_NORMAL
    assert 'ABOVE_NORMAL_PRIORITY_CLASS' not in open(textocr.__file__, encoding='utf-8').read()   # no second copy of the class


def test_restart_task_is_registered_at_normal_priority():
    from paths import RELEASE_TOOLS
    script = (RELEASE_TOOLS / 'register_dashboard_task.ps1').read_text(encoding='utf-8')
    settings = re.search(r'New-ScheduledTaskSettingsSet(?:[^\n]*`\n)*[^\n]*', script).group(0)
    assert re.search(r'-Priority\s+4\b', settings)   # the default 7 is BelowNormal, inherited by every child