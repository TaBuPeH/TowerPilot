"""The recorded-fixture writer: the layout is compact and deterministic, and it never changes what the JSON says."""

import json

import pytest

import paths as test_paths
from fixture_json import INLINE_DEPTH, render_fixture_json, write_fixture_json

# Recorded fixtures written by the writer; the text on disk must be exactly what the writer renders from its own content.
RECORDED = [
    test_paths.FIXTURES / name for name in (
        'dashboard_contract_pages_v1.json', 'dashboard_contract_v1.json', 'harvest_status_v1.json',
        'synthetic_action_contract_v1.json', 'action_golden_v1.json', 'action_a3a_golden_v1.json',
        'action_dispatch_golden_v1.json')
] + [test_paths.FRONTEND_FIXTURES / name for name in ('abilities-samples.json', 'equipment-samples.json', 'schedule-race-samples.json')]

DOCUMENT = {
    'version': 1,
    'cases': {'family': {'case_b': {'status': 200, 'body': {'rate': 60.0, 'tags': ['x', {'k': None}], 'name': 'é'}},
                          'case_a': {'request': {'z': 1, 'a': [1.5, True]}}}},
    'empty': {}, 'none': [],
}


def test_the_text_parses_back_to_exactly_the_value_written():
    text = render_fixture_json(DOCUMENT)
    assert json.loads(text) == DOCUMENT
    assert '60.0' in text and 'é' in text, 'numbers keep json.dumps spelling and text stays UTF-8'
    assert render_fixture_json(json.loads(text)) == text


def test_keys_are_sorted_and_insertion_order_does_not_matter():
    shuffled = {'b': 1, 'a': {'d': 1, 'c': 2}}
    assert render_fixture_json(shuffled) == render_fixture_json({'a': {'c': 2, 'd': 1}, 'b': 1})
    assert render_fixture_json(shuffled).index('"a"') < render_fixture_json(shuffled).index('"b"')


def test_one_case_member_per_line_and_everything_below_on_one_line():
    assert INLINE_DEPTH == 4
    assert render_fixture_json(DOCUMENT) == (
        '{\n'
        ' "cases": {\n'
        '  "family": {\n'
        '   "case_a": {\n'
        '    "request": {"a": [1.5, true], "z": 1}\n'
        '   },\n'
        '   "case_b": {\n'
        '    "body": {"name": "é", "rate": 60.0, "tags": ["x", {"k": null}]},\n'
        '    "status": 200\n'
        '   }\n'
        '  }\n'
        ' },\n'
        ' "empty": {},\n'
        ' "none": [],\n'
        ' "version": 1\n'
        '}\n')


def test_the_expanded_levels_hold_arrays_one_item_per_line():
    assert render_fixture_json({'a': [1, [2, 3]]}) == '{\n "a": [\n  1,\n  [\n   2,\n   3\n  ]\n ]\n}\n'


def test_members_must_be_named_by_strings():
    with pytest.raises(TypeError):
        render_fixture_json({1: 'x'})


def test_write_creates_the_folder_and_writes_lf_bytes(tmp_path):
    target = tmp_path / 'deeper' / 'fixture.json'
    text = write_fixture_json(target, DOCUMENT)
    raw = target.read_bytes()
    assert raw == text.encode('utf-8') and b'\r' not in raw and raw.endswith(b'}\n') and not raw.endswith(b'\n\n')


@pytest.mark.parametrize('path', RECORDED, ids=lambda path: path.name)
def test_every_recorded_fixture_is_the_writers_own_text(path):
    raw = path.read_bytes()
    assert raw == render_fixture_json(json.loads(raw.decode('utf-8'))).encode('utf-8'), f'{path.name} was not written by support/fixture_json.py'
