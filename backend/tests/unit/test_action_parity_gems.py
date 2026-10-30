"""Cadence parity of the two gem collectors (roadmap item 6, step A2-0 and every later step).

The floating-gem traces were re-captured on the tree after r14g (appearance-driven attempts, spacing only after unconfirmed input).

The traces in fixtures/action_golden_v1.json were captured on the unchanged tree. Replaying the same fake
clock, seeded random source and scripted device must reproduce every decision (window starts, skipped
windows, observation polls, claims, re-anchors), every random draw in its original order, and the full
collector state after each step. The explicit tests below name the behaviours the specification lists, so
a reader can see what the traces pin.
"""

import paths as test_paths
import hashlib
import json
from pathlib import Path

import pytest

import action_golden as golden

FAMILIES = ('ad_gems', 'floating_gems')


@pytest.fixture(scope='module')
def captured():
    expected = golden.load()
    # Keep the migration proof intact. Only R14i's intentional confirmation
    # timing differences have separate, versioned expectations.
    delta = json.loads((test_paths.FIXTURES / 'action_orbit_confirmation_v1.json').read_text(encoding='utf-8'))
    # The parent pin hashes the fixture's LF bytes, which is what the repository blob and
    # support/fixture_json.py write; a Windows checkout without eol=lf is normalised first.
    parent = golden.GOLDEN_PATH.read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(parent).hexdigest().upper() == delta['parent_fixture_sha256']
    for group, traces in delta['floating_gems'].items():
        expected['floating_gems'][group].update(traces)
    return expected


@pytest.fixture(scope='module')
def replayed():
    return {family: golden.roundtrip(golden.gem_traces(family)) for family in FAMILIES}


@pytest.mark.parametrize('seed', [str(seed) for seed in golden.APPEARANCE_FUZZ_SEEDS])
def test_a_random_sequence_in_which_the_gem_comes_and_goes_replays_the_captured_digests(captured, replayed, seed):
    expected, actual = captured['floating_gems']['fuzz_appearance'][seed], replayed['floating_gems']['fuzz_appearance'][seed]
    first = next((index for index, pair in enumerate(zip(expected['digests'], actual['digests'])) if pair[0] != pair[1]), None)
    assert first is None, f'floating_gems appearance seed {seed} diverges at step {first}'
    assert actual == expected


@pytest.mark.parametrize('family', FAMILIES)
def test_every_curated_scenario_replays_the_captured_trace(captured, replayed, family):
    for name, trace in captured[family]['curated'].items():
        assert replayed[family]['curated'][name] == trace, f'{family}: {name}'
    assert sorted(replayed[family]['curated']) == sorted(captured[family]['curated'])


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('seed', [str(seed) for seed in golden.FUZZ_SEEDS])
def test_a_random_sequence_of_host_operations_replays_the_captured_digests(captured, replayed, family, seed):
    expected, actual = captured[family]['fuzz'][seed], replayed[family]['fuzz'][seed]
    first = next((index for index, pair in enumerate(zip(expected['digests'], actual['digests'])) if pair[0] != pair[1]), None)
    assert first is None, f'{family} seed {seed} diverges at step {first}'
    assert actual == expected


def events(trace, name):
    return [(step['t'], event[1]) for step in trace for event in step.get('events', []) if event[0] == name]


def test_the_first_ad_gem_window_opens_at_once_and_each_window_draws_its_interval_from_the_seeded_range(captured):
    trace = captured['ad_gems']['curated']['first_window_at_once_then_idle_polls']
    opened = events(trace, 'ad_gems_window')
    first = next(step for step in trace if step.get('events'))
    assert first['op'][0] == 'poll' and opened[0][0] == first['t'] == trace[0]['t']     # the very first poll opens it
    window_draw, observation_draw = first['draws'][0], first['draws'][1]
    options = first['state']['options']
    assert window_draw[0] == 'uniform' and window_draw[1:3] == options['cooldown']      # the interval is drawn first,
    assert opened[0][1]['interval_sec'] == window_draw[3]                               # and it is the window's interval
    assert observation_draw[1:3] == options['poll']                                     # then the next observation poll


def test_a_long_gap_skips_whole_windows_with_no_catch_up_burst(captured):
    trace = captured['ad_gems']['curated']['long_gap_skips_whole_windows_without_a_burst']
    assert len(events(trace, 'ad_gems_windows_missed')) >= 1
    after_the_gap = [t for t, _ in events(trace, 'ad_gems_window') if t >= 5000]
    assert len(after_the_gap) <= 1                              # one window, never a burst of owed ones


def test_a_floating_gem_gap_owes_no_catch_up_and_the_next_attempt_waits_for_the_safety_backoff(captured):
    # since r14g an attempt opens when a gem is recognized, not on a cadence: there is nothing to skip and nothing to owe
    trace = captured['floating_gems']['curated']['long_gap_skips_whole_windows_without_a_burst']
    opened = [t for t, _ in events(trace, 'gem_orbit_window')]
    ended = [t for t, _ in events(trace, 'gem_orbit_claim')]
    assert opened[0] == trace[0]['t']                                      # the first poll opens it
    assert len(opened) == 2 and opened[1] > ended[0]                       # one attempt per eligible poll, never one inside another
    assert not [t for t in opened if ended[0] < t < ended[0] + 15]         # the poll right after the expiry waited for the safety backoff


def test_an_appearance_opens_an_attempt_and_nothing_else_does(captured):
    trace = captured['floating_gems']['curated']['no_gem_on_screen_opens_no_attempt_and_a_gem_that_appears_opens_one_at_once']
    appeared = next(step['t'] for step in trace if step['op'][0] == 'gem' and step['op'][1] is True)
    opened = [t for t, _ in events(trace, 'gem_orbit_window')]
    assert opened and opened[0] == appeared + 1 and all(t >= opened[0] for t in opened)     # none while the gem was away


@pytest.mark.parametrize('name', ['a_gem_over_an_unreadable_wave_counter_opens_no_attempt', 'a_gem_over_a_screen_that_is_not_the_battle_opens_no_attempt'])
def test_a_recognized_gem_opens_an_attempt_only_over_a_readable_wave_on_the_battle_screen(captured, name):
    trace = captured['floating_gems']['curated'][name]
    came_back = next(step['t'] for step in trace if step['op'][0] in ('wave', 'screen') and step['op'][1] is True)
    opened = [t for t, _ in events(trace, 'gem_orbit_window')]
    assert opened and opened[0] == came_back + 1 and not [t for t in opened if t <= came_back]      # none while the wave or the screen was not right
    assert not [step for step in trace if step['t'] <= came_back and step['taps']]


def test_only_input_that_was_not_confirmed_spaces_the_next_attempt(captured):
    unconfirmed = captured['floating_gems']['curated']['unconfirmed_taps_space_the_next_attempt_by_the_interval_from_its_start']
    first, second = [t for t, _ in events(unconfirmed, 'gem_orbit_window')][:2]
    assert second - first >= 960                                           # the interval counts from the attempt's start
    silent = captured['floating_gems']['curated']['an_attempt_that_sent_no_input_retries_after_the_safety_backoff_only']
    first, second = [t for t, _ in events(silent, 'gem_orbit_window')][:2]
    assert second - first < 960                                            # no input: the interval is not owed
    confirmed = captured['floating_gems']['curated']['a_confirmed_claim_resumes_watching_at_once_without_a_timed_window']
    opened = [t for t, _ in events(confirmed, 'gem_orbit_window')]
    claim = next(t for t, payload in events(confirmed, 'gem_orbit_claim') if payload['confirmed'] is True)
    assert opened[1] - claim <= 10                                         # watching resumed after the claim, not after an interval


@pytest.mark.parametrize('seed', [str(seed) for seed in golden.APPEARANCE_FUZZ_SEEDS])
def test_an_idle_tapper_is_gated_by_one_number(seed):
    """The policy decides on `next_window_at` while the host's gate at the top of `_poll` reads `next_at`. They are the same number
    whenever no attempt is open, which is what lets the policy's own spacing check stand beside the host's gate."""
    world = golden.World(int(seed))
    with golden.gem_world(world):
        tapper = golden.make_orbit(world)
        checked = 0
        for op in golden.fuzz_script(int(seed), appearance=True):
            golden.apply_op(world, tapper, op, golden.orbit_state)
            if tapper.attempt_started is None:
                assert tapper.next_at == tapper.next_window_at, (op, tapper.next_at, tapper.next_window_at)
                checked += 1
        assert checked > 50


def test_the_appearance_fuzz_traces_are_captured_for_the_tapper_only(captured):
    assert sorted(captured['floating_gems']['fuzz_appearance']) == [str(seed) for seed in golden.APPEARANCE_FUZZ_SEEDS]
    assert 'fuzz_appearance' not in captured['ad_gems']


@pytest.mark.parametrize('family,window', [('ad_gems', 'ad_gems_window'), ('floating_gems', 'gem_orbit_window')])
def test_a_manual_start_runs_a_window_even_while_disabled_and_is_refused_while_one_runs(captured, family, window):
    trace = captured[family]['curated']['manual_start_runs_while_disabled']
    assert trace[0]['op'] == ['configure', {'enabled': False}] and trace[1]['answer'] is True
    assert len(events(trace, window)) == 1
    refused = captured[family]['curated']['manual_start_reanchors_and_is_refused_while_a_window_runs']
    answers = [step['answer'] for step in refused if step['op'][0] == 'manual']
    assert False in answers
    if family == 'ad_gems':
        assert True in answers                  # the window had ended by the last request, on a cadence
    else:
        # an appearance-driven tapper opens an attempt at the first eligible poll, so every request that follows a poll meets a running one;
        # the request that is accepted is the one made while idle (the scenario with no gem on screen)
        idle = captured[family]['curated']['a_manual_request_opens_an_attempt_with_no_gem_on_screen']
        assert [step['answer'] for step in idle if step['op'][0] == 'manual'] == [True]
        assert len(events(idle, window)) == 1 and not idle[-1]['taps']
