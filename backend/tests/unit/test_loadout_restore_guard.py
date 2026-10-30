"""The restore values of a loadout are only editable where their ownership can be checked (r12b review R1).

`cards_restore` and `modules_restore` are keys of a loadout body in config.yaml. The profile validator checks a loadout's contents against the
account only for the loadouts a blueprint of the ACTIVE profile names; every other loadout is saved unchecked. A restore value saved there looks
verified to the page that sent it and is not. POST /api/loadout-patch therefore refuses - 409 `restore_loadout_unused`, nothing written - a body
that sets, changes or clears a restore key of a loadout the active profile does not use, or when there is no active profile to check against.
A body that leaves the restore keys as stored (the page sends whole bodies) is judged as it always was.

Every test drives the real Flask route over the temporary root of the conftest `dash` fixture: no live file, device, ledger or lease.
"""
import copy
import hashlib
from pathlib import Path

import pytest
import yaml

CARDS = ['main_farm', 'no_card']
GHOST_CARDS = {'cards': 'no_card', 'cards_restore': 'ghost_deck'}
OWNED_CARDS = {'cards': 'no_card', 'cards_restore': 'main_farm'}
GHOST_MODULES = {'cards': 'no_card', 'modules_restore': [['ghost_module', 'assist']]}
OWNED_MODULES = {'cards': 'no_card', 'modules_restore': [['black_hole_digestor', 'assist']]}


def profile_using(loadout):
    return dict(player=dict(card_presets=CARDS, modules_equipped=['black_hole_digestor'], modules_in_grid=[]),
                blueprints={'farm': {'kind': 'coin', 'tier': 1, 'loadout': loadout, 'policies': {'gather': 'shared'}}},
                policies={'gather': {'shared': {'ad_gems': True, 'gem_delay_sec': [3, 10]}}})


def furnish(dash, *, active='mine', spare=None):
    """Loadouts `used` (named by the active profile `mine`) and `spare` (named by the inactive profile `elsewhere` only)."""
    root = Path(dash.ROOT)
    cfg = yaml.safe_load((root / 'config.yaml').read_text(encoding='utf-8'))
    cfg['loadouts'] = {'used': {'cards': 'no_card'}, 'spare': spare if spare is not None else {'cards': 'no_card'}}
    if active:
        cfg['active_profile'] = active
    else:
        cfg.pop('active_profile', None)
    (root / 'config.yaml').write_text(yaml.safe_dump(cfg), encoding='utf-8')
    (root / 'profiles' / 'mine.yaml').write_text(yaml.safe_dump(profile_using('used')), encoding='utf-8')
    (root / 'profiles' / 'elsewhere.yaml').write_text(yaml.safe_dump(profile_using('spare')), encoding='utf-8')
    return dash


def config_bytes(dash):
    return Path(dash.CONFIG_PATH).read_bytes()


def revision(dash):
    return hashlib.sha256(config_bytes(dash)).hexdigest()


def backups(dash):
    return sorted(p.name for p in Path(dash.ROOT).glob('config.yaml.bak-*'))


def patch(dash, name, body, **extra):
    with dash.app.test_client() as client:
        return client.post('/api/loadout-patch', json=dict(name=name, body=body, expected_revision=revision(dash), **extra))


def refused_nothing_written(dash, reply, keys):
    got = reply.get_json()
    assert reply.status_code == 409 and got['ok'] is False and got['code'] == 'restore_loadout_unused', got
    assert got['restore_keys'] == keys and got['error']
    return got


@pytest.mark.parametrize('body,keys', [(GHOST_CARDS, ['cards_restore']), (OWNED_CARDS, ['cards_restore']),
                                       (GHOST_MODULES, ['modules_restore']), (OWNED_MODULES, ['modules_restore']),
                                       (dict(GHOST_CARDS, **GHOST_MODULES), ['cards_restore', 'modules_restore'])])
def test_restore_values_of_a_loadout_no_active_blueprint_uses_are_refused_and_nothing_is_written(dash, body, keys):
    furnish(dash)
    before, saved = config_bytes(dash), backups(dash)
    reply = patch(dash, 'spare', body)
    got = refused_nothing_written(dash, reply, keys)
    assert got['loadout'] == 'spare' and got['active_profile'] == 'mine'
    assert config_bytes(dash) == before and backups(dash) == saved


def test_a_valid_looking_value_is_refused_too_because_nothing_could_check_it(dash):
    """Ownership is the point, not the value: the owned preset is refused exactly like the ghost one."""
    furnish(dash)
    refused_nothing_written(dash, patch(dash, 'spare', OWNED_CARDS), ['cards_restore'])
    refused_nothing_written(dash, patch(dash, 'spare', GHOST_CARDS), ['cards_restore'])


@pytest.mark.parametrize('body', [GHOST_CARDS, OWNED_MODULES])
def test_with_no_active_profile_there_is_nothing_to_check_against_so_the_restore_values_are_refused(dash, body):
    furnish(dash, active=None)
    before = config_bytes(dash)
    reply = patch(dash, 'used', body)
    got = refused_nothing_written(dash, reply, ['cards_restore'] if 'cards_restore' in body else ['modules_restore'])
    assert got['active_profile'] is None and config_bytes(dash) == before


def test_a_loadout_only_an_inactive_profile_uses_is_not_used(dash):
    furnish(dash)                                                  # `elsewhere` names `spare`, but the active profile is `mine`
    before = config_bytes(dash)
    refused_nothing_written(dash, patch(dash, 'spare', OWNED_MODULES), ['modules_restore'])
    assert config_bytes(dash) == before


def test_clearing_a_stored_restore_value_of_an_unused_loadout_is_refused_too(dash):
    furnish(dash, spare={'cards': 'no_card', 'cards_restore': 'ghost_deck'})       # written before the guard existed
    before = config_bytes(dash)
    refused_nothing_written(dash, patch(dash, 'spare', {'cards': 'no_card'}), ['cards_restore'])
    assert config_bytes(dash) == before


def test_an_unused_loadout_is_still_saved_when_its_restore_keys_are_as_stored(dash):
    furnish(dash, spare={'cards': 'no_card', 'cards_restore': 'ghost_deck'})
    kept = patch(dash, 'spare', {'cards': 'main_farm', 'cards_restore': 'ghost_deck'})
    assert kept.status_code == 200 and kept.get_json()['loadouts']['spare'] == {'cards': 'main_farm', 'cards_restore': 'ghost_deck'}
    plain = patch(dash, 'spare', {'cards': 'no_card', 'cards_restore': 'ghost_deck', 'guardians': True})
    assert plain.status_code == 200


def test_an_unused_loadout_without_restore_keys_is_saved_as_it_always_was(dash):
    furnish(dash)
    saved = patch(dash, 'spare', {'cards': 'main_farm'})
    assert saved.status_code == 200 and saved.get_json()['loadouts']['spare'] == {'cards': 'main_farm'}
    assert yaml.safe_load(config_bytes(dash))['loadouts']['spare'] == {'cards': 'main_farm'}


@pytest.mark.parametrize('body', [OWNED_CARDS, OWNED_MODULES])
def test_the_loadout_the_active_profile_uses_still_saves_its_restore_values(dash, body):
    furnish(dash)
    saved = patch(dash, 'used', body)
    assert saved.status_code == 200 and saved.get_json()['loadouts']['used'] == body, saved.get_json()
    assert yaml.safe_load(config_bytes(dash))['loadouts']['used'] == body


@pytest.mark.parametrize('body', [GHOST_CARDS, GHOST_MODULES])
def test_the_validators_own_refusal_still_answers_for_the_loadout_the_active_profile_uses(dash, body):
    furnish(dash)
    before = config_bytes(dash)
    reply = patch(dash, 'used', body)
    assert reply.status_code == 400 and reply.get_json().get('code') is None and reply.get_json()['problems']
    assert config_bytes(dash) == before


def test_the_guard_comes_after_the_revision_and_the_loadout_name_and_before_the_validator(dash):
    furnish(dash)
    before = config_bytes(dash)
    with dash.app.test_client() as client:
        stale = client.post('/api/loadout-patch', json=dict(name='spare', body=GHOST_CARDS, expected_revision='0' * 64))
        missing = client.post('/api/loadout-patch', json=dict(name='nobody', body=GHOST_CARDS, expected_revision=revision(dash)))
        wrong_context = client.post('/api/loadout-patch', json=dict(name='spare', body=GHOST_CARDS, expected_revision=revision(dash),
                                                                    expected_context=dict(instance='main', account=None, profile='elsewhere', serial='fake:1')))
    assert stale.status_code == 409 and stale.get_json()['code'] == 'revision_conflict'
    assert missing.status_code == 404
    assert wrong_context.status_code == 409 and wrong_context.get_json()['code'] == 'context_changed'
    assert config_bytes(dash) == before


def test_an_unrelated_problem_of_the_active_profile_does_not_mask_the_guard_and_still_answers_an_ordinary_edit(dash):
    """The guard is judged before the validator's problems: a profile with a problem of its own (here a blueprint naming a loadout that does not exist) still gets the
    restore refusal, and an edit without restore changes still meets the validator exactly as it always did."""
    furnish(dash)
    broken = profile_using('used')
    broken['blueprints']['broken'] = {'kind': 'coin', 'tier': 1, 'loadout': 'no_such_loadout', 'policies': {'gather': 'shared'}}
    (Path(dash.ROOT) / 'profiles' / 'mine.yaml').write_text(yaml.safe_dump(broken), encoding='utf-8')
    before = config_bytes(dash)
    refused_nothing_written(dash, patch(dash, 'spare', OWNED_CARDS), ['cards_restore'])
    ordinary = patch(dash, 'spare', {'cards': 'main_farm'})
    assert ordinary.status_code == 400 and ordinary.get_json().get('code') is None and 'no_such_loadout' in ordinary.get_json()['error'], ordinary.get_json()
    assert config_bytes(dash) == before


def test_the_refusal_does_not_depend_on_the_validator_being_available(dash, monkeypatch):
    """Who may edit a restore value is decided from the profile's blueprints alone: when the validator cannot run, the person is still told the real reason
    (409), while an ordinary edit meets the 500 it always did. Nothing is written in either case."""
    furnish(dash)
    before = config_bytes(dash)

    def unavailable(*args, **kwargs):
        raise RuntimeError('the validator could not be loaded')
    monkeypatch.setattr(dash, '_profile_check', unavailable)
    refused_nothing_written(dash, patch(dash, 'spare', OWNED_MODULES), ['modules_restore'])
    assert patch(dash, 'spare', {'cards': 'main_farm'}).status_code == 500
    assert patch(dash, 'used', OWNED_CARDS).status_code == 500                       # the loadout a run uses is validated, so it needs the validator
    assert config_bytes(dash) == before


def test_a_profile_that_cannot_be_read_is_not_a_profile_that_uses_the_loadout(dash):
    furnish(dash)
    (Path(dash.ROOT) / 'profiles' / 'mine.yaml').write_text('- not\n- a mapping\n', encoding='utf-8')
    before = config_bytes(dash)
    refused_nothing_written(dash, patch(dash, 'used', OWNED_CARDS), ['cards_restore'])
    assert config_bytes(dash) == before


# ------------------------------------------------------------- the profile layer's one definition of "used"
def test_loadouts_in_use_is_every_distinct_named_loadout_in_blueprint_order():
    from player import playerprofile
    profile = {'blueprints': {'a': {'loadout': 'x'}, 'b': {'loadout': 'y'}, 'c': {'loadout': 'x'}, 'd': {}, 'e': {'loadout': 5}, 'f': None, 'g': {'loadout': None}}}
    assert playerprofile.loadouts_in_use(profile) == ['x', 'y']
    assert playerprofile.loadouts_in_use({}) == [] and playerprofile.loadouts_in_use({'blueprints': None}) == [] and playerprofile.loadouts_in_use(None) == []
    assert playerprofile.LOADOUT_RESTORE_KEYS == ('cards_restore', 'modules_restore')
