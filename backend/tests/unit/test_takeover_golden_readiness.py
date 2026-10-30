"""Golden answers of the pure readers T3a will touch, on the unchanged code (roadmap item 3, package T3-0): player/readiness.requirements over
a synthetic matrix of compiled runs, and vision/screen.in_tournament / identify over synthetic frames and templates written to a temporary
directory (the template path is redirected exactly where detect._tpl reads it). No game image is read."""
import json

import pytest

import takeover_golden as golden
from takeover_golden_misc import READINESS, SCREEN

READINESS_DOCUMENT, SCREEN_DOCUMENT = 'readiness_requirements', 'screen_readers'


def test_the_recorded_documents_hold_exactly_these_scenarios():
    golden.assert_registry(READINESS_DOCUMENT, READINESS)
    golden.assert_registry(SCREEN_DOCUMENT, SCREEN)


@pytest.mark.parametrize('scenario', sorted(READINESS))
def test_readiness_answers_what_was_recorded(scenario, tmp_path, monkeypatch):
    golden.assert_recorded(READINESS_DOCUMENT, scenario, READINESS[scenario](tmp_path, monkeypatch))


@pytest.mark.parametrize('scenario', sorted(SCREEN))
def test_the_screen_readers_answer_what_was_recorded(scenario, tmp_path, monkeypatch):
    golden.assert_recorded(SCREEN_DOCUMENT, scenario, SCREEN[scenario](tmp_path, monkeypatch))


def test_a_missing_template_reads_as_not_in_a_tournament():
    """The gap T3a closes: in_tournament cannot tell 'no tournament' from 'no template', both answer False."""
    readers = golden.recorded(SCREEN_DOCUMENT)['in_tournament_readers']
    assert readers['present_and_matching']['in_tournament'] is True
    for case in ('missing', 'unreadable', 'present_and_not_matching', 'present_matching_elsewhere_in_the_frame'):
        assert readers[case]['in_tournament'] is False
    assert readers['missing']['score_zero'] is True and readers['unreadable']['score_zero'] is True and readers['present_and_not_matching']['score_zero'] is False


def test_the_tournament_run_makes_the_badge_template_blocking_and_the_coin_run_does_not():
    matrix = golden.recorded(READINESS_DOCUMENT)['requirements_matrix']

    def row(name):
        return next(r for r in map(json.loads, matrix[name]['rows']) if r[0] == ['tourney/in_tournament.png'])
    assert row('tournament_setup')[2] is True and row('tournament_by_setup_flag_only')[2] is True
    assert row('coin_minimal')[2] is False and row('shard')[2] is False
