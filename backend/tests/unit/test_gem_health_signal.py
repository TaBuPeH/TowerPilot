"""Gem-health signal (FOCUS #4): a recognizer that stops matching must say so.

Image-free. The orbit recognizer is stubbed to reject, or fed generated scenes; the evidence chain
(tapper -> binding -> BattleSession evidence -> harvest) runs for real; ad windows run against a real
temporary action ledger."""
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from control import harvest
from flows import shard
from interactions import ad_gems
from runtime import action_bindings
from runtime.action_session import ActionSession
from runtime.battle_session import BattleSession
from test_action_session import CONTEXT, clock, rows, session          # noqa: F401 - fixtures and the ledger reader
from test_ad_gems_incomplete_reads import ScriptedDelays, world        # noqa: F401 - fixture
from test_gem_orbit_cycle import cycle                                  # noqa: F401 - fixture
from test_orbit_gems import scene
from vision import orbit_gems, textocr


# ------------------------------------------------------------------ the recognizer's own account of a frame
def looked_at(frame):
    glance = orbit_gems.Glance()
    return orbit_gems.find_orbit_gem(frame, glance=glance), glance


def test_a_glance_never_changes_the_recognizers_answer():
    two = np.maximum(scene(), scene(center=(250, 600)))
    for frame in (scene(), scene(45), scene(kind='filled'), scene(kind='empty'), scene(kind='red'), two, scene()[::2, ::2]):
        found, _ = looked_at(frame)
        assert found == orbit_gems.find_orbit_gem(frame)


def test_the_glance_names_why_a_frame_was_not_recognized():
    hud = orbit_gems.manifest()['interface']['screens']['battle']['orbiting_gem']['hud_icon_rect']
    no_hud = scene()
    no_hud[hud[1]:hud[1]+hud[3], hud[0]:hud[0]+hud[2]] = 0
    cases = {
        'found': (scene(), 0),
        'glyph_rejected': (scene(kind='filled'), 1),       # a pink-bordered square whose interior is no gem
        'no_candidate': (scene(kind='red'), 0),            # no pink border: not even a near miss
        'hud_glyph': (no_hud, 0),
        'frame_shape': (scene()[::2, ::2], 0),
        'ambiguous': (np.maximum(scene(), scene(center=(250, 600))), 0),
    }
    for reason, (frame, near_misses) in cases.items():
        _, glance = looked_at(frame)
        assert (glance.reason, glance.near_misses) == (reason, near_misses), reason


def test_an_empty_square_is_a_near_miss_without_a_score():
    _, glance = looked_at(scene(kind='empty'))
    assert (glance.reason, glance.near_misses, glance.best_near_miss) == ('glyph_rejected', 1, None)
    _, glance = looked_at(scene(kind='filled'))
    assert glance.near_misses == 1 and (glance.best_near_miss is None or 0 <= glance.best_near_miss < .8)


# --------------------------------------------------------------------- the tapper counts what it senses
def rejecting(monkeypatch, *, near_misses=1, best=.62):
    """The recognizer stubbed to reject every frame, saying (as the real one does) that it saw a near miss."""
    def find(frame, *, glance=None, **kwargs):
        if glance is not None:
            glance.reason, glance.near_misses, glance.best_near_miss = 'glyph_rejected' if near_misses else 'no_candidate', near_misses, best
        return None
    monkeypatch.setattr(shard, 'find_orbit_gem', find)


def test_idle_frames_are_counted_with_their_near_misses_and_nothing_else_is_read(cycle, monkeypatch):
    state, tapper = cycle
    rejecting(monkeypatch)
    monkeypatch.setattr(shard.capture, 'grab', lambda: pytest.fail('idle sensing must not capture'))
    monkeypatch.setattr(shard.wave_reader, 'read_wave', lambda _: pytest.fail('no wave read on the idle path'))
    monkeypatch.setattr(shard.screen, 'identify', lambda _: pytest.fail('no classification on the idle path'))
    assert tapper.sensing() is None                       # nothing sensed yet is unknown, never zero
    for state.now in range(100, 110):
        tapper.poll(200)
    sensing = tapper.sensing()
    assert (sensing['frames'], sensing['recognized'], sensing['near_miss_frames']) == (10, 0, 10)
    assert sensing['reasons'] == {'glyph_rejected': 10} and sensing['best_near_miss'] == .62
    assert sensing['recent'] == dict(window=orbit_gems.RECENT_FRAMES, frames=10, recognized=0, near_miss_frames=10)
    assert state.taps == [] and tapper.attempt_started is None


def test_a_recognized_gem_is_counted_and_the_attempt_it_opens_senses_no_more(cycle):
    state, tapper = cycle
    tapper.poll(200)                                      # the default stub recognizes a gem and the attempt opens
    assert tapper.attempt_started is not None
    state.now = 101
    tapper.poll(200)
    sensing = tapper.sensing()
    assert (sensing['frames'], sensing['recognized'], sensing['near_miss_frames']) == (1, 1, 0)
    assert sensing['reasons'] == {'found': 1}


def test_frames_without_a_near_miss_are_quiet_and_a_disabled_tapper_senses_nothing(cycle, monkeypatch):
    state, tapper = cycle
    rejecting(monkeypatch, near_misses=0, best=None)
    for state.now in (100, 101, 102):
        tapper.poll(200)
    assert tapper.sensing()['near_miss_frames'] == 0 and tapper.sensing()['reasons'] == {'no_candidate': 3}
    tapper.enabled = False
    state.now = 103
    tapper.poll(200)
    assert tapper.sensing()['frames'] == 3


def test_the_recent_window_forgets_old_frames(cycle, monkeypatch):
    state, tapper = cycle
    rejecting(monkeypatch)
    for state.now in range(1000, 1000 + orbit_gems.RECENT_FRAMES + 30):
        tapper.poll(200)
    sensing = tapper.sensing()
    assert sensing['frames'] == orbit_gems.RECENT_FRAMES + 30
    assert sensing['recent']['frames'] == orbit_gems.RECENT_FRAMES


def test_a_heartbeat_is_logged_once_a_battle_minute_outside_any_window(cycle, monkeypatch):
    state, tapper = cycle
    rejecting(monkeypatch)
    contexts = []
    monkeypatch.setattr(shard.logger, 'event', lambda kind, **kw: (state.events.append((kind, kw)),
                                                                  kind == 'gem_orbit_sensing' and contexts.append(shard.logger.current_context())))
    for state.now in (100, 159, 300, 1060, 2000):
        tapper.poll(200)
    beats = [fields for kind, fields in state.events if kind == 'gem_orbit_sensing']
    assert [beat['frames'] for beat in beats] == [3, 4, 5]
    assert len(contexts) == 3 and all('window_id' not in context and 'attempt_id' not in context for context in contexts)
    assert beats[-1]['recent']['near_miss_frames'] == 5 and beats[-1]['best_near_miss'] == .62


# ------------------------------------------------- the evidence chain and the health decision
def published_evidence(tapper):
    """What BattleSession publishes as collection_evidence for a host whose orbit tapper is `tapper`."""
    binding = action_bindings.CollectorBinding(SimpleNamespace(gems=SimpleNamespace(orbit=tapper)), 'gems.orbit')
    session = object.__new__(BattleSession)
    session.battle, session.generation = dict(segments=[dict(started_at=0.0)], created_at=0.0, runtime={}), 'g1'
    return session._collection_evidence({}, 100.0, sensing=action_bindings.sensing_of({'floating_gems': binding}))


def battle_of(evidence, *, orbit=True):
    return dict(runtime=dict(routines={'ad_gems': dict(enabled=True), 'floating_gems': dict(enabled=orbit)}, collection_evidence=evidence))


def test_the_finder_is_suspected_within_n_rejected_frames(cycle, monkeypatch):
    state, tapper = cycle
    rejecting(monkeypatch)
    limit = harvest.FINDER_NEAR_MISS_FRAMES
    answers = []
    for state.now in range(100, 100 + limit + 2):
        tapper.poll(200)
        answers.append(harvest.finder_suspected(battle_of(published_evidence(tapper))))
    assert answers == [False] * (limit - 1) + [True] * 3


def test_a_recognition_in_the_window_clears_the_suspicion():
    def sensing(**recent):
        return dict(floating_gems=dict(frames=9, recent=dict(window=120, frames=9, recognized=0, near_miss_frames=9) | recent))
    suspect = battle_of(dict(sensing=sensing()))
    assert harvest.finder_suspected(suspect) is True
    assert harvest.finder_suspected(battle_of(dict(sensing=sensing(recognized=1)))) is False
    assert harvest.finder_suspected(battle_of(dict(sensing=sensing(near_miss_frames=harvest.FINDER_NEAR_MISS_FRAMES - 1)))) is False
    assert harvest.finder_suspected(battle_of(dict(sensing=sensing()), orbit=False)) is False     # a switched-off path is not suspected


@pytest.mark.parametrize('evidence', [None, {}, dict(sensing=None), dict(sensing={}), dict(sensing=dict(floating_gems=None)),
                                      dict(sensing=dict(floating_gems=dict(recent=None))),
                                      dict(sensing=dict(floating_gems=dict(recent=dict(frames='9', recognized=0, near_miss_frames=9))))])
def test_nothing_reported_is_unknown_never_a_clean_bill(evidence):
    assert harvest.finder_suspected(battle_of(evidence)) is None


def test_the_claims_carry_the_suspicion_for_the_status():
    evidence = dict(sensing=dict(floating_gems=dict(frames=9, recent=dict(window=120, frames=9, recognized=0, near_miss_frames=9))))
    claims = harvest.claim_summary([], [], dict(battle_of(evidence), created_at=0.0))
    assert claims['finder_suspected'] is True
    assert harvest.claim_summary([], [], dict(battle_of({}), created_at=0.0))['finder_suspected'] is None


# ------------------------------------------------------- ad windows say what they actually read
def ad_collector(world, ledger, delay=(15, 45)):
    collector = ad_gems.AdGemCollector(delay=delay, rng=ScriptedDelays([15], delay), recorder=ledger)
    collector.next_at = collector.next_window_at = world.now[0]
    return collector


def script_reads(monkeypatch, *outcomes):
    """find_claim_rect answers from `outcomes` in order: a reason string is an incomplete read, None absence."""
    pending = list(outcomes)

    def find(frame, **kwargs):
        outcome = pending.pop(0) if pending else None
        if isinstance(outcome, str):
            raise ad_gems.RecognitionIncomplete(outcome)
        return outcome
    monkeypatch.setattr(ad_gems, 'find_claim_rect', find)


def test_a_window_of_mostly_incomplete_reads_records_its_observation_counts(world, monkeypatch, session):
    script_reads(monkeypatch, 'ocr_timeout', 'ocr_timeout', None, 'recognition_deadline', None)
    collector = ad_collector(world, session.recorder('ad_gems'))
    for now in range(100, 170, 5):
        world.now[0] = now
        collector.poll(object())
    [window] = rows(session, action='ad_gems')
    assert (window['state'], window['outcome']['reason']) == ('no_op', 'no_offer_seen')
    # polls at 100..155 fall inside the 60 s window (the one at 160 closes it first): 12 reads, 3 of which could not finish
    assert window['refs']['observation'] == dict(reads=12, incomplete=3, absent=9)
    ended = [fields for kind, fields in world.events if kind == 'ad_gems_window_ended']
    assert ended[-1]['observation'] == window['refs']['observation']


def test_a_window_counts_only_reads_taken_while_it_is_open(world, monkeypatch, session):
    script_reads(monkeypatch)
    collector = ad_collector(world, session.recorder('ad_gems'))
    collector.next_at = collector.next_window_at = 1000.0           # the window opens later
    for now in (100, 105, 110):
        world.now[0] = now
        collector.poll(object())
    assert collector.counters['checks'] == 3 and rows(session, action='ad_gems') == []
    for now in range(1000, 1070, 5):
        world.now[0] = now
        collector.poll(object())
    [window] = rows(session, action='ad_gems')
    # the 3 passive checks before the window and the 2 after its close (1060, 1065) are not in it: 1000..1055 is 12 reads
    assert collector.counters['checks'] == 17 and window['refs']['observation']['reads'] == 12


def test_an_incomplete_read_logs_the_stage_timers_of_the_ocr_it_ran(world, monkeypatch):
    def find(frame, **kwargs):
        textocr._TIMING.update(path='warm', warm_sec=1.25, reclaim_sec=.5)
        raise ad_gems.RecognitionIncomplete('ocr_timeout')
    monkeypatch.setattr(ad_gems, 'find_claim_rect', find)
    collector = ad_collector(world, None)
    collector.poll(object())
    [event] = [fields for kind, fields in world.events if kind == 'ad_gems_recognition_incomplete']
    assert event['stages'] == dict(path='warm', warm_sec=1.25, reclaim_sec=.5)


def test_stage_timers_of_an_earlier_read_are_not_blamed_on_a_read_that_never_reached_ocr(world, monkeypatch):
    textocr._TIMING.update(path='cold', cold_sec=9.0)               # left behind by some earlier read
    monkeypatch.setattr(ad_gems, 'find_claim_rect', lambda frame, **kwargs: (_ for _ in ()).throw(ad_gems.RecognitionIncomplete('recognition_deadline')))
    collector = ad_collector(world, None)
    collector.poll(object())
    [event] = [fields for kind, fields in world.events if kind == 'ad_gems_recognition_incomplete']
    assert event['stages'] == {}


def test_an_incomplete_read_reports_where_its_non_ocr_time_went(world, monkeypatch):
    spent = {'now': 0.0}
    from runtime import stage_clock

    def advance(seconds, result):
        def call(frame, **kwargs):
            spent['now'] += seconds
            return result
        return call
    monkeypatch.setattr(ad_gems, 'clock', stage_clock.StageClock(now=lambda: spent['now']))
    monkeypatch.setattr(ad_gems.wave_reader, 'read_wave', advance(1.5, 100))
    monkeypatch.setattr(ad_gems.screen, 'identify', advance(2.0, SimpleNamespace(name='battle')))

    def find(frame, **kwargs):
        spent['now'] += 3.25
        raise ad_gems.RecognitionIncomplete('ocr_timeout')
    monkeypatch.setattr(ad_gems, 'find_claim_rect', find)
    collector = ad_collector(world, None)
    collector.poll(object())
    [event] = [fields for kind, fields in world.events if kind == 'ad_gems_recognition_incomplete']
    assert event['read_stages'] == {'ad.wave': 1.5, 'ad.screen': 2.0, 'ad.claim': 3.25}


# ------------------------------------------------------------ windows that never open leave a row
def test_a_window_the_loop_never_opened_leaves_one_ledger_row(world, monkeypatch, session):
    script_reads(monkeypatch)
    collector = ad_collector(world, session.recorder('ad_gems'))
    collector.next_at = collector.next_window_at = 100.0
    world.now[0] = 100.0 + 5 * 720                                   # the loop was away for several whole intervals
    collector.poll(object())
    [row] = rows(session, action='ad_gems')
    assert (row['state'], row['outcome']['reason'], row['outcome']['effect'], row['outcome']['attempts']) == ('no_op', 'window_missed', 'none', 0)
    assert row['refs'] == dict(missed=collector.counters['windows_missed']) and row['refs']['missed'] >= 1
    assert row['scheduled_for'] == pytest.approx(1000.0 + 100.0)    # the window it should have opened, on the wall clock
    assert collector.window_deadline is None and collector.recorder.execution_id is None


def test_windows_that_open_are_not_also_recorded_as_missed(world, monkeypatch, session):
    script_reads(monkeypatch)
    collector = ad_collector(world, session.recorder('ad_gems'))
    collector.poll(object())
    assert 'window_missed' not in [row['outcome']['reason'] for row in rows(session, action='ad_gems')]
    assert collector.window_deadline is not None


def test_a_missed_window_reads_as_a_window_that_ran_out_and_ends_no_streak():
    row = dict(state='no_op', outcome=dict(reason='window_missed'), started_at=50.0, finished_at=50.0)
    assert harvest.miss_cause(row) == dict(code='window_ran_out', at=50.0)
    closed = [dict(state='no_op', outcome=dict(reason='window_missed'), started_at=60.0, finished_at=60.0),
              dict(state='failed', outcome=dict(reason='offer_moved'), started_at=50.0, finished_at=50.0)]
    claims = harvest.claim_summary(closed, [], dict(created_at=0.0))
    assert claims['failure_streak'] == 1 and claims['cause']['code'] == 'window_ran_out'


# --------------------------------------------------------------------------- the OCR stage timers
def test_the_timers_name_the_path_and_split_warm_cold_and_reclaim(monkeypatch):
    class Unavailable:
        def recognize(self, entries, deadline):
            textocr._stage('reclaim_sec', textocr.time.monotonic())
            raise textocr._WarmUnavailable()
    monkeypatch.setattr(textocr, '_WARM', Unavailable())
    monkeypatch.setattr(textocr.settings, 'run_hidden', lambda *args, **kwargs: subprocess.CompletedProcess(
        args, 0, stdout=b'TP_OCR_IMAGE\t0\n0\t0\tCLAIM 6\n', stderr=b''))
    crop = np.zeros((20, 40, 3), np.uint8) + 255
    assert textocr.read_texts([crop], scales=(1,), timeout=5, raise_on_error=True, urgent=True) == ['CLAIM 6']
    stages = textocr.take_timing()
    assert stages['path'] == 'cold_after_warm_unavailable'
    assert {'warm_sec', 'reclaim_sec', 'cold_sec'} <= set(stages) and all(stages[name] >= 0 for name in ('warm_sec', 'reclaim_sec', 'cold_sec'))
    assert textocr.take_timing() == {}                                 # taken once


def test_a_warm_read_that_times_out_still_reports_its_time(monkeypatch):
    class Slow:
        def recognize(self, entries, deadline):
            raise subprocess.TimeoutExpired('Windows OCR warm reader', 0)
    monkeypatch.setattr(textocr, '_WARM', Slow())
    crop = np.zeros((20, 40, 3), np.uint8) + 255
    with pytest.raises(subprocess.TimeoutExpired):
        textocr.read_texts([crop], scales=(1,), timeout=5, raise_on_error=True, urgent=True)
    stages = textocr.take_timing()
    assert stages['path'] == 'warm' and stages['warm_sec'] >= 0 and 'cold_sec' not in stages


def test_the_warm_reader_times_its_reclaim(monkeypatch):
    reader = textocr._WarmReader()
    monkeypatch.setattr(reader, '_reclaim', lambda: False)             # an abandoned reader is still alive
    textocr.take_timing()
    with pytest.raises(textocr._WarmUnavailable):
        reader.recognize([], textocr.time.monotonic() + 5)
    assert 'reclaim_sec' in textocr.take_timing()
