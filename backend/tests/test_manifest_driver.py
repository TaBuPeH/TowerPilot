import pytest

from player.manifest_driver import Driver, MappingStopped, catalogue, plan, validate


def test_tournament_defers_without_duplicating_battle_screens():
    p = validate()
    assert 'tournament_battle' not in p['screens']
    assert 'tournament' in p['screens']['battle']['contexts']
    edges = [e for e in p['steps'] if e['availability'] == 'tournament_open']
    assert edges and all(e['status'] in ('deferred', 'manual_only') for e in edges)
    entry = next(e for e in plan(tournament_open=True)['steps']
                 if e['action'] == 'battle')
    assert entry['status'] == 'manual_only'


def fixture_driver(screens=('home', 'home', 'cards'), hits=None):
    observed = iter(screens)
    found = iter(hits or [dict(rect=[100, 200, 50, 60], identity='cards', verified=True)]*2)
    taps, events, cuts = [], [], []
    driver = Driver(observe=lambda: (object(), next(observed)),
                    locate=lambda *args: next(found), tap=lambda *p: taps.append(p),
                    collect=lambda *args: cuts.append(args), publish=events.append,
                    check_stop=lambda: None)
    edge = dict(id='cards', source='home', destination='cards', action='cards',
                kind='navigate', availability='always')
    return driver, edge, taps, events, cuts


def test_verified_step_records_before_input_and_collects_destination():
    d, e, taps, events, cuts = fixture_driver()
    assert d.step(e)
    assert taps == [(125, 230)]
    assert [v['status'] for v in events] == ['input_pending', 'done']
    assert cuts[0][0] == 'cards'


def test_unexpected_destination_stops_without_retry_or_capture():
    d, e, taps, events, cuts = fixture_driver(('home', 'home', 'loading'))
    with pytest.raises(MappingStopped):
        d.step(e)
    assert len(taps) == 1 and not cuts
    assert events[-1]['status'] == 'uncertain'


def test_live_battle_is_never_navigated_by_home_scan():
    d, e, taps, events, cuts = fixture_driver(('battle',))
    with pytest.raises(MappingStopped):
        d.step(e)
    assert not taps and not cuts


@pytest.mark.parametrize('other', [None,
    dict(rect=[100, 210, 50, 60], identity='cards', verified=True),
    dict(rect=[100, 200, 50, 60], identity='store', verified=True),
    dict(rect=[100, 200, 50, 60], identity='cards', verified=False),
    dict(rect=[1070, 200, 50, 60], identity='cards', verified=True)])
def test_missing_moved_unproven_or_ambiguous_control_sends_no_input(other):
    first = dict(rect=[100, 200, 50, 60], identity='cards', verified=True)
    d, e, taps, events, cuts = fixture_driver(hits=[first, other])
    assert not d.step(e)
    assert not taps and not cuts


def test_closed_tournament_does_not_even_capture():
    d, e, taps, events, cuts = fixture_driver(())
    e['availability'] = 'tournament_open'
    assert not d.step(e)
    assert events[-1]['status'] == 'deferred'


def test_walk_visits_and_returns_home():
    current = ['home']
    collected = []
    edges = [dict(id='out', source='home', destination='cards', kind='navigate', availability='always'),
             dict(id='back', source='cards', destination='home', kind='navigate', availability='always')]
    def tap(*args):
        current[0] = 'cards' if current[0] == 'home' else 'home'
    d = Driver(observe=lambda: (None, current[0]),
               locate=lambda *a: dict(verified=True, identity='button', rect=[20,20,40,40]),
               tap=tap, collect=lambda name, frame: collected.append(name),
               publish=lambda row: None, check_stop=lambda: None)
    result = d.walk(edges)
    assert result['status'] == 'mapped'
    assert collected == ['cards', 'home']
