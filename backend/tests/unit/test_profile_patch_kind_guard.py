"""A run's `kind` is fixed once the run is added: POST /api/profile-patch refuses a patch that changes it (r12d; the r12c review, F6-11 RL12).

`kind` picks the runner and decides which fields the rest of the blueprint may carry. The route used to guard it only by accident: a change the
profile validator happened to refuse (an unknown kind, a kind without its required fields, the as_is equipment on a non-coin run, coin-only fields
the new kind may not carry) was refused, and one it did not refuse was SAVED - a coin run that kept no coin-only field became a tournament run
without anyone asking. The route now compares, for every run that exists both before and after the patch, the kind it had with the kind it has, and
refuses any difference with 400 {ok:false, code:"kind_change_refused", error, changes:[{blueprint, from_kind, to_kind}]}, writing nothing. The check
comes AFTER the validator, so a change the validator already refuses keeps the validator's own, more specific answer; and after the revision and
context checks, which are judged first. Adding a run (a name that did not exist) and removing one are not kind changes.

Every test drives the real Flask route over a temporary root (conftest `dash`): no live file, device, ledger or lease.
"""
import copy
import hashlib
from pathlib import Path

import pytest
import yaml

URL = '/api/profile-patch'
GATHER = {'shared': {'ad_gems': True, 'gem_delay_sec': [3, 10]}}


def profile_text():
    profile = dict(player={}, policies={'gather': copy.deepcopy(GATHER)}, blueprints={
        'farm': {'kind': 'coin', 'tier': 1, 'loadout': 'plain', 'policies': {'gather': 'shared'}},
        'spare': {'kind': 'coin', 'tier': 2, 'loadout': 'plain', 'label': 'Spare run', 'policies': {'gather': 'shared'}, 'max_wave': 400},
        'shards': {'kind': 'shard', 'tier': 3, 'loadout': 'plain', 'count': 5, 'policies': {'gather': 'shared'}}})
    return yaml.safe_dump(profile, sort_keys=False)


@pytest.fixture
def world(dash):
    root = Path(dash.ROOT)
    cfg = yaml.safe_load((root / 'config.yaml').read_text(encoding='utf-8'))
    cfg['loadouts'] = {'plain': {}}
    (root / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    (root / 'profiles' / 'mine.yaml').write_text(profile_text(), encoding='utf-8')
    return dash


def path_of(dash, name='mine'):
    return Path(dash.ROOT) / 'profiles' / f'{name}.yaml'


def stored(dash, name='mine'):
    return yaml.safe_load(path_of(dash, name).read_text(encoding='utf-8'))


def revision(dash, name='mine'):
    return hashlib.sha256(path_of(dash, name).read_bytes()).hexdigest()


def backups(dash):
    return sorted(p.name for p in (Path(dash.ROOT) / 'profiles').glob('mine.yaml.bak-*'))


def patch(dash, path, value=None, *, delete=False, name='mine', **extra):
    body = dict(name=name, path=path, expected_revision=revision(dash, name), **extra)
    if delete:
        body['op'] = 'delete'
    else:
        body['value'] = value
    return dash.app.test_client().post(URL, json=body)


def refused_kind(dash, reply, *changes):
    got = reply.get_json()
    assert reply.status_code == 400 and got['ok'] is False and got['code'] == 'kind_change_refused', got
    assert got['changes'] == [dict(blueprint=b, from_kind=f, to_kind=t) for b, f, t in changes] and got['error'], got
    return got


def untouched(dash, before, saved):
    assert path_of(dash).read_bytes() == before and backups(dash) == saved, 'a refused kind change changed the saved profile'


# ------------------------------------------------------------------------------------ the gap: a change the validator accepts
def test_a_coin_run_that_keeps_no_coin_only_field_cannot_become_a_shard_run(world):
    before, saved = path_of(world).read_bytes(), backups(world)
    got = refused_kind(world, patch(world, ['blueprints', 'farm', 'kind'], 'shard'), ('farm', 'coin', 'shard'))
    assert 'farm' in got['error'] and 'coin' in got['error'] and 'shard' in got['error']
    untouched(world, before, saved)
    assert stored(world)['blueprints']['farm']['kind'] == 'coin'


def test_without_the_guard_that_patch_would_have_been_valid(world):
    """The control that makes the test above mean something: the resulting profile is one the validator accepts."""
    data = stored(world)
    data['blueprints']['farm']['kind'] = 'shard'
    assert world._profile_check(data) == []


@pytest.mark.parametrize('value', [dict(kind='shard', tier=3, loadout='plain', count=2, policies={'gather': 'shared'}),
                                   dict(kind='tournament', tier=3, loadout='plain', policies={'gather': 'shared'})])
def test_a_whole_run_replaced_by_one_of_another_kind_is_refused(world, value):
    before, saved = path_of(world).read_bytes(), backups(world)
    refused_kind(world, patch(world, ['blueprints', 'farm'], value), ('farm', 'coin', value['kind']))
    untouched(world, before, saved)


def test_the_whole_blueprints_mapping_with_one_kind_changed_is_refused_and_names_it(world):
    blueprints = copy.deepcopy(stored(world)['blueprints'])
    blueprints['farm']['kind'] = 'shard'
    blueprints['shards']['kind'] = 'coin'
    blueprints['shards'].pop('count')
    before, saved = path_of(world).read_bytes(), backups(world)
    got = refused_kind(world, patch(world, ['blueprints'], blueprints), ('farm', 'coin', 'shard'), ('shards', 'shard', 'coin'))
    assert [c['blueprint'] for c in got['changes']] == ['farm', 'shards']             # sorted by run name, whatever order the request listed them in
    untouched(world, before, saved)


# ------------------------------------------------------------------------------------ what the validator already refuses keeps its answer
def test_a_kind_the_validator_refuses_keeps_the_validators_own_answer_and_nothing_is_written(world):
    before, saved = path_of(world).read_bytes(), backups(world)
    reply = patch(world, ['blueprints', 'farm', 'kind'], 'dissonance')
    got = reply.get_json()
    assert reply.status_code == 400 and 'code' not in got and got['problems'] and 'unknown kind' in got['error'], got
    deleted = patch(world, ['blueprints', 'farm', 'kind'], delete=True).get_json()
    assert 'code' not in deleted and deleted['problems'], deleted
    untouched(world, before, saved)


def test_a_coin_run_with_coin_only_fields_is_refused_by_the_validator_not_the_guard(world):
    reply = patch(world, ['blueprints', 'spare', 'kind'], 'shard')              # spare keeps max_wave, which a shard run may not carry
    assert reply.status_code == 400 and 'code' not in reply.get_json() and 'max_wave' in reply.get_json()['error']


# ------------------------------------------------------------------------------------ what is not a kind change
def test_setting_the_kind_it_already_has_is_not_a_change(world):
    reply = patch(world, ['blueprints', 'farm', 'kind'], 'coin')
    assert reply.status_code == 200 and reply.get_json()['ok'] is True and stored(world)['blueprints']['farm']['kind'] == 'coin'


def test_a_whole_run_replaced_by_one_of_the_same_kind_is_saved(world):
    reply = patch(world, ['blueprints', 'farm'], dict(kind='coin', tier=7, loadout='plain', policies={'gather': 'shared'}))
    assert reply.status_code == 200 and stored(world)['blueprints']['farm']['tier'] == 7


def test_every_other_field_of_a_run_is_still_saved(world):
    for path, value in ((['blueprints', 'farm', 'tier'], 9), (['blueprints', 'farm', 'label'], 'Daily coin farm'), (['blueprints', 'shards', 'count'], 12)):
        assert patch(world, path, value).status_code == 200, path
    data = stored(world)['blueprints']
    assert (data['farm']['tier'], data['farm']['label'], data['shards']['count']) == (9, 'Daily coin farm', 12)
    assert data['farm']['kind'] == 'coin' and data['shards']['kind'] == 'shard'


def test_a_run_that_did_not_exist_may_be_added_with_any_kind(world):
    reply = patch(world, ['blueprints', 'fresh'], dict(kind='shard', tier=4, loadout='plain', count=1, policies={'gather': 'shared'}))
    assert reply.status_code == 200 and stored(world)['blueprints']['fresh']['kind'] == 'shard'


def test_the_whole_blueprints_mapping_may_add_or_drop_a_run_without_touching_a_kind(world):
    blueprints = copy.deepcopy(stored(world)['blueprints'])
    blueprints['extra'] = dict(kind='tournament', tier=5, loadout='plain', policies={'gather': 'shared'})
    del blueprints['spare']
    reply = patch(world, ['blueprints'], blueprints)
    assert reply.status_code == 200 and sorted(stored(world)['blueprints']) == ['extra', 'farm', 'shards']


def test_a_run_may_be_removed_and_added_again_as_two_separate_requests(world):
    """The sanctioned way to change a run's type: two explicit acts, each judged on its own (a plan block that names the run blocks the first)."""
    assert patch(world, ['blueprints', 'farm'], delete=True).status_code == 200
    again = patch(world, ['blueprints', 'farm'], dict(kind='shard', tier=3, loadout='plain', count=1, policies={'gather': 'shared'}))
    assert again.status_code == 200 and stored(world)['blueprints']['farm']['kind'] == 'shard'


def test_a_patch_outside_the_blueprints_is_not_looked_at(world):
    assert patch(world, ['policies', 'gather', 'shared', 'ad_gems'], False).status_code == 200


def test_a_profile_without_blueprints_or_with_a_run_that_is_not_a_mapping_does_not_break_the_comparison(world):
    from player import playerprofile
    assert playerprofile.blueprint_kind_changes({}, {}) == [] and playerprofile.blueprint_kind_changes(None, {'blueprints': None}) == []
    assert playerprofile.blueprint_kind_changes({'blueprints': {'a': None, 'b': 5}}, {'blueprints': {'a': {'kind': 'coin'}, 'b': {'kind': 'shard'}}}) == [
        dict(blueprint='a', from_kind=None, to_kind='coin'), dict(blueprint='b', from_kind=None, to_kind='shard')]


# ------------------------------------------------------------------------------------ where the guard sits among the other checks
def test_a_stale_revision_is_judged_before_the_guard(world):
    reply = world.app.test_client().post(URL, json=dict(name='mine', path=['blueprints', 'farm', 'kind'], value='shard', expected_revision='0' * 64))
    assert reply.status_code == 409 and reply.get_json()['code'] == 'revision_conflict'


def test_the_context_fence_is_judged_before_the_guard(world):
    elsewhere = dict(instance='main', account=None, profile='someone_else', serial='fake:1')
    before = path_of(world).read_bytes()
    reply = patch(world, ['blueprints', 'farm', 'kind'], 'shard', expected_context=elsewhere)
    assert reply.status_code == 409 and reply.get_json()['code'] == 'context_changed' and path_of(world).read_bytes() == before


def test_with_a_matching_context_the_guard_still_refuses(world):
    here = dict(instance='main', account=None, profile='mine', serial='fake:1')
    refused_kind(world, patch(world, ['blueprints', 'farm', 'kind'], 'shard', expected_context=here), ('farm', 'coin', 'shard'))


def test_the_starter_and_a_scan_draft_are_refused_before_the_guard(world):
    for name in ('default', 'mine.draft'):
        path_of(world, name).write_text(profile_text(), encoding='utf-8')
        reply = patch(world, ['blueprints', 'farm', 'kind'], 'shard', name=name)
        assert reply.status_code == 409 and 'code' not in reply.get_json(), (name, reply.get_json())


def test_a_refused_kind_change_leaves_the_run_editable(world):
    patch(world, ['blueprints', 'farm', 'kind'], 'shard')
    saved = patch(world, ['blueprints', 'farm', 'tier'], 4)
    assert saved.status_code == 200 and stored(world)['blueprints']['farm']['tier'] == 4 and stored(world)['blueprints']['farm']['kind'] == 'coin'


# ------------------------------------------------------------------------------------ the profile layer's one definition of "a kind change"
def test_the_comparison_looks_only_at_runs_present_on_both_sides():
    from player import playerprofile
    before = {'blueprints': {'a': {'kind': 'coin'}, 'b': {'kind': 'shard'}, 'gone': {'kind': 'coin'}}}
    after = {'blueprints': {'a': {'kind': 'coin'}, 'b': {'kind': 'tournament'}, 'new': {'kind': 'cycle_quest'}}}
    assert playerprofile.blueprint_kind_changes(before, after) == [dict(blueprint='b', from_kind='shard', to_kind='tournament')]
    assert playerprofile.blueprint_kind_changes(after, before) == [dict(blueprint='b', from_kind='tournament', to_kind='shard')]
    assert playerprofile.blueprint_kind_changes(before, before) == []


def test_the_changes_are_sorted_by_run_name_whatever_the_order_of_the_mappings():
    from player import playerprofile
    before = {'blueprints': {'z': {'kind': 'coin'}, 'a': {'kind': 'coin'}, 'm': {'kind': 'coin'}}}
    after = {'blueprints': {'m': {'kind': 'shard'}, 'z': {'kind': 'shard'}, 'a': {'kind': 'shard'}}}
    assert [c['blueprint'] for c in playerprofile.blueprint_kind_changes(before, after)] == ['a', 'm', 'z']


def test_a_deleted_kind_is_a_change_to_none():
    from player import playerprofile
    assert playerprofile.blueprint_kind_changes({'blueprints': {'a': {'kind': 'coin', 'tier': 1}}}, {'blueprints': {'a': {'tier': 1}}}) == [dict(blueprint='a', from_kind='coin', to_kind=None)]
