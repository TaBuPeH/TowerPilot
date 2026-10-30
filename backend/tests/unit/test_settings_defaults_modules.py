"""Typed settings modules for the machine and emulator defaults (roadmap r12b / F6-15), offline over temporary files.

The catalogue (control/settings_catalog.py) gained seven modules - instance-defaults, active-instance, run-defaults, tournament-cards and the three
machine defaults loop-defaults, missions-defaults, fleet-defaults - and one field kind, `string-list`. The route (POST /api/settings) writes them like
every other module; the one module that needs more is active-instance, whose save must file the old emulator's account fields under the OLD account.
"""
import copy
from pathlib import Path

import pytest
import yaml

from control import settings_catalog
from test_settings_api import dash, save    # noqa: F401  (the fixture of the settings route tests: two emulators, two accounts)

NEW = ['loop-defaults', 'missions-defaults', 'fleet-defaults', 'instance-defaults', 'active-instance', 'run-defaults', 'tournament-cards']


def edit(dash, change):
    path = Path(dash.CONFIG_PATH)
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    change(cfg)
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')


def stored(dash):
    return yaml.safe_load(Path(dash.CONFIG_PATH).read_text(encoding='utf-8'))


def furnish(cfg):
    """Two emulators, each bound to an account that owns DIFFERENT loadouts, profile and tournament tweaks, and legacy machine defaults."""
    cfg['presets'] = {'normal_run': {'label': 'High Tier'}, 'coin_farm': {}, 'spare': {'defined': False}}
    cfg['preset'] = 'normal_run'
    cfg['loadouts'] = {'legacy_only': {}}
    cfg['tourney_card_tweaks'] = {'drop': ['legacy_card'], 'add': []}
    cfg['instances']['main'].update(label='Main', preset='coin_farm', autostart=False, loop={'fps': 5})
    cfg['instances']['other'].update(label='Other')
    cfg['loop'] = {'fps': 2, 'dry_run': False}
    cfg['accounts']['alice'].update(loadouts={'alice_load': {'cards': 'x'}}, tourney_card_tweaks={'drop': ['alice_drop'], 'add': ['alice_add']})
    cfg['accounts']['bob'].update(loadouts={'bob_load': {'cards': 'y'}}, tourney_card_tweaks={'drop': [], 'add': ['bob_add']})


@pytest.fixture
def world(dash):
    edit(dash, furnish)
    return dash


def get(dash):
    with dash.app.test_client() as client:
        return client.get('/api/settings').get_json()


def post(dash, snapshot, module, values, **override):
    body = dict(context=snapshot['context'], revision=snapshot['revision'], module=module, values=values, **override)
    with dash.app.test_client() as client:
        return client.post('/api/settings', json=body)


def module(snapshot, ident):
    return next(m for m in snapshot['modules'] if m['id'] == ident)


def value(snapshot, ident, key):
    return next(f for f in module(snapshot, ident)['fields'] if f['key'] == key)['value']


# ------------------------------------------------------------------------------ the string-list kind
LIST = settings_catalog.field('drop', 'Cards to drop', 'string-list', [], max_items=3)
CHOICE = settings_catalog.field('add', 'Cards', 'string-list', [], options=[{'value': 'cash', 'label': 'Cash'}, {'value': 'orb', 'label': 'Orb'}])


@pytest.mark.parametrize('good', [[], ['cash'], ['b', 'a', 'b'], ['a b', 'x-1']])
def test_a_string_list_is_ordered_and_nothing_is_changed_or_dropped(good):
    before = copy.deepcopy(good)
    assert settings_catalog.validate(LIST, good) is None and good == before


@pytest.mark.parametrize('bad', ['cash', None, 7, {'a': 1}, ('a',), [1], [None], [['a']], [True], [''], ['  '], ['a', ''], ['a', 2], ['a', 'b', 'c', 'd']])
def test_a_string_list_refuses_what_is_not_a_list_of_non_blank_strings_or_is_too_long(bad):
    with pytest.raises(ValueError, match='Cards to drop'):
        settings_catalog.validate(LIST, bad)


def test_a_string_list_with_options_accepts_only_those_names_and_a_list_without_options_accepts_any():
    settings_catalog.validate(CHOICE, ['orb', 'cash'])
    with pytest.raises(ValueError, match='allowed'):
        settings_catalog.validate(CHOICE, ['orb', 'ghost'])
    settings_catalog.validate(LIST, ['anything'])


def test_only_a_list_field_carries_a_length_limit_key():
    plain = settings_catalog.field('fps', 'Frames', 'number', 2)
    assert 'maxItems' not in plain and LIST['maxItems'] == 3 and settings_catalog.field('x', 'X', 'string-list', [])['maxItems'] is None


# ------------------------------------------------------------------------------------ the catalogue
def test_the_new_modules_are_listed_with_their_scope_storage_and_fields(world):
    snapshot = get(world)
    ids = [m['id'] for m in snapshot['modules']]
    assert ids[:ids.index('logging')] == ['tap', 'device-preview', 'loop', 'missions', 'fleet', *NEW]
    cfg = world.load_config()
    spec = {m['id']: m for m in settings_catalog.catalog(cfg, {}, True)}
    assert [(i, spec[i]['scope'], spec[i]['storage_path']) for i in NEW] == [
        ('loop-defaults', 'machine', ['loop']), ('missions-defaults', 'machine', ['missions']), ('fleet-defaults', 'machine', ['fleet']),
        ('instance-defaults', 'instance', ['instances', 'main']), ('active-instance', 'machine', []), ('run-defaults', 'machine', []),
        ('tournament-cards', 'machine', ['tourney_card_tweaks'])]
    assert all('storage_path' not in m for m in snapshot['modules'])                 # the public snapshot hides storage
    assert {i: [(f['key'], f['type']) for f in module(snapshot, i)['fields']] for i in NEW} == {
        'loop-defaults': [('fps', 'number'), ('dry_run', 'boolean')], 'missions-defaults': [('visit_delay_sec', 'range')],
        'fleet-defaults': [('hold_window', 'integer'), ('post_window', 'integer')],
        'instance-defaults': [('label', 'string'), ('preset', 'enum'), ('autostart', 'boolean')],
        'active-instance': [('active_instance', 'enum')], 'run-defaults': [('preset', 'enum')],
        'tournament-cards': [('drop', 'string-list'), ('add', 'string-list')]}
    assert all('maxItems' in f for f in module(snapshot, 'tournament-cards')['fields'])
    assert not any('maxItems' in f for m in snapshot['modules'] if m['id'] != 'tournament-cards' for f in m['fields'])


def test_the_defaults_modules_show_the_machines_own_values_not_the_selected_emulators_override(world):
    snapshot = get(world)
    assert value(snapshot, 'loop', 'fps') == 5 and value(snapshot, 'loop-defaults', 'fps') == 2          # override vs machine default
    assert value(snapshot, 'missions-defaults', 'visit_delay_sec') == [60, 600]
    assert value(snapshot, 'fleet-defaults', 'hold_window') == 100                                      # the field default: no `fleet` key at all


def test_the_preset_choices_are_the_runnable_presets_and_an_instance_may_keep_the_machine_fallback(world):
    snapshot = get(world)
    run = [o['value'] for o in module(snapshot, 'run-defaults')['fields'][0]['options']]
    instance = module(snapshot, 'instance-defaults')['fields'][1]['options']
    assert run == ['coin_farm', 'normal_run'] and [o['value'] for o in instance] == ['', 'coin_farm', 'normal_run']      # the file's order (yaml dumps sorted)
    assert [o['label'] for o in instance] == ['Use machine fallback', 'Coin Farm', 'High Tier']
    assert value(snapshot, 'run-defaults', 'preset') == 'normal_run' and value(snapshot, 'instance-defaults', 'preset') == 'coin_farm'
    assert world.runnable_presets(world.load_config()) == run == settings_catalog.runnable_presets(world.load_config())


def test_the_selected_emulator_choices_are_the_configured_emulators(world):
    snapshot = get(world)
    field = module(snapshot, 'active-instance')['fields'][0]
    assert field['value'] == 'main' and field['options'] == [{'value': 'main', 'label': 'Main'}, {'value': 'other', 'label': 'Other'}]


# ---------------------------------------------------------------------------------------- saving
def test_instance_defaults_write_only_the_selected_emulator(world):
    snapshot = get(world)
    reply = post(world, snapshot, 'instance-defaults', {'label': 'Renamed', 'preset': '', 'autostart': True})
    assert reply.status_code == 200 and reply.get_json()['ok'] is True
    cfg = stored(world)
    assert cfg['instances']['main'] == {'account': 'alice', 'label': 'Renamed', 'preset': '', 'autostart': True, 'loop': {'fps': 5}}
    assert cfg['instances']['other'] == {'account': 'bob', 'label': 'Other'}
    assert (value(reply.get_json(), 'instance-defaults', 'preset'), value(reply.get_json(), 'instance-defaults', 'autostart')) == ('', True)


@pytest.mark.parametrize('values', [{'label': 7}, {'preset': 'ghost'}, {'preset': 'spare'}, {'autostart': 'true'}, {'autostart': 1}, {'label': 'ok', 'preset': 'ghost'}])
def test_an_invalid_instance_default_writes_nothing(world, values):
    snapshot, before = get(world), Path(world.CONFIG_PATH).read_bytes()
    reply = post(world, snapshot, 'instance-defaults', values)
    assert reply.status_code == 400 and reply.get_json()['ok'] is False
    assert Path(world.CONFIG_PATH).read_bytes() == before


def test_run_defaults_write_the_machine_fallback_preset_and_refuse_a_placeholder(world):
    snapshot = get(world)
    assert post(world, snapshot, 'run-defaults', {'preset': 'spare'}).status_code == 400
    assert post(world, snapshot, 'run-defaults', {'preset': 'ghost'}).status_code == 400
    reply = post(world, snapshot, 'run-defaults', {'preset': 'coin_farm'})
    assert reply.status_code == 200 and stored(world)['preset'] == 'coin_farm' and stored(world)['instances']['main']['preset'] == 'coin_farm'


def test_tournament_cards_of_a_bound_account_are_saved_under_that_account_and_the_machine_default_is_kept(world):
    snapshot = get(world)
    assert value(snapshot, 'tournament-cards', 'drop') == ['alice_drop']
    reply = post(world, snapshot, 'tournament-cards', {'drop': ['x', 'y'], 'add': []})
    assert reply.status_code == 200
    cfg = stored(world)
    assert cfg['accounts']['alice']['tourney_card_tweaks'] == {'drop': ['x', 'y'], 'add': []}
    assert cfg['accounts']['bob']['tourney_card_tweaks'] == {'drop': [], 'add': ['bob_add']}
    assert cfg['tourney_card_tweaks'] == {'drop': ['legacy_card'], 'add': []}
    assert value(reply.get_json(), 'tournament-cards', 'drop') == ['x', 'y']


def test_tournament_cards_of_an_unbound_installation_are_the_machines_own(dash):
    edit(dash, lambda cfg: (cfg.pop('accounts'), cfg['instances'].update(main={}, other={}), cfg.update(active_profile='default', tourney_card_tweaks={'drop': ['a'], 'add': []})))
    snapshot = get(dash)
    assert post(dash, snapshot, 'tournament-cards', {'add': ['orb']}).status_code == 200
    assert stored(dash)['tourney_card_tweaks'] == {'drop': ['a'], 'add': ['orb']}


@pytest.mark.parametrize('values', [{'drop': ['ok'], 'add': [1]}, {'drop': 'cash'}, {'drop': ['']}, {'add': ['a'] * 51}, {'drop': None}])
def test_an_invalid_tournament_list_changes_neither_field(world, values):
    snapshot, before = get(world), Path(world.CONFIG_PATH).read_bytes()
    reply = post(world, snapshot, 'tournament-cards', values)
    assert reply.status_code == 400 and Path(world.CONFIG_PATH).read_bytes() == before


def test_machine_defaults_write_the_root_blocks_and_never_an_emulator_override(world):
    snapshot = get(world)
    for ident, values in (('loop-defaults', {'fps': 3, 'dry_run': True}), ('missions-defaults', {'visit_delay_sec': [90, 300]}), ('fleet-defaults', {'hold_window': 120, 'post_window': 130})):
        reply = post(world, snapshot, ident, values)
        assert reply.status_code == 200, (ident, reply.get_json())
        snapshot = reply.get_json()
    cfg = stored(world)
    assert cfg['loop'] == {'fps': 3, 'dry_run': True} and cfg['missions'] == {'visit_delay_sec': [90, 300]} and cfg['fleet'] == {'hold_window': 120, 'post_window': 130}
    assert cfg['instances']['main']['loop'] == {'fps': 5} and 'missions' not in cfg['instances']['main'] and 'fleet' not in cfg['instances']['main']
    assert value(snapshot, 'loop', 'fps') == 5 and value(snapshot, 'loop-defaults', 'fps') == 3


@pytest.mark.parametrize('ident,values', [('loop-defaults', {'fps': 'fast'}), ('loop-defaults', {'dry_run': 1}), ('missions-defaults', {'visit_delay_sec': [600, 60]}),
                                          ('fleet-defaults', {'hold_window': 1.5}), ('fleet-defaults', {'post_window': -1})])
def test_invalid_machine_defaults_write_nothing(world, ident, values):
    snapshot, before = get(world), Path(world.CONFIG_PATH).read_bytes()
    assert post(world, snapshot, ident, values).status_code == 400 and Path(world.CONFIG_PATH).read_bytes() == before


def test_a_stale_revision_is_refused_for_every_new_module(world):
    first = get(world)
    assert post(world, first, 'tap', {'jitter_px': 5}).status_code == 200
    for ident, values in (('instance-defaults', {'label': 'X'}), ('active-instance', {'active_instance': 'other'}), ('run-defaults', {'preset': 'coin_farm'}),
                          ('tournament-cards', {'drop': ['z']}), ('loop-defaults', {'fps': 3}), ('missions-defaults', {'visit_delay_sec': [1, 2]}), ('fleet-defaults', {'hold_window': 1})):
        before = Path(world.CONFIG_PATH).read_bytes()
        reply = post(world, first, ident, values)
        assert reply.status_code == 409, ident
        assert Path(world.CONFIG_PATH).read_bytes() == before, ident


# --------------------------------------------------------------------------- switching the emulator
def test_switching_the_selected_emulator_keeps_both_accounts_and_the_machine_defaults_exactly(world):
    snapshot, before = get(world), stored(world)
    reply = post(world, snapshot, 'active-instance', {'active_instance': 'other'})
    assert reply.status_code == 200 and reply.get_json()['ok'] is True
    after = stored(world)
    assert after['active_instance'] == 'other'
    assert after['accounts'] == before['accounts']                           # alice AND bob exactly as saved: neither received the other's fields
    for key in ('loadouts', 'tourney_card_tweaks', 'active_profile', 'instances', 'presets', 'loop', 'preset'):
        assert after[key] == before[key], key                                 # the legacy machine defaults and every emulator entry are unchanged
    assert reply.get_json()['context'] == {'instance': 'other', 'account': 'bob', 'profile': 'bob'}
    assert value(reply.get_json(), 'tournament-cards', 'add') == ['bob_add'] and value(get(world), 'instance-defaults', 'label') == 'Other'


def test_switching_back_and_forth_never_mixes_accounts(world):
    for target in ('other', 'main', 'other', 'main'):
        snapshot = get(world)
        before = stored(world)['accounts']
        assert post(world, snapshot, 'active-instance', {'active_instance': target}).status_code == 200
        assert stored(world)['accounts'] == before and stored(world)['active_instance'] == target


def test_a_switch_to_an_emulator_whose_account_is_unknown_is_refused_before_anything_is_written(world):
    edit(world, lambda cfg: cfg['instances']['other'].update(account='ghost'))
    snapshot, before = get(world), Path(world.CONFIG_PATH).read_bytes()
    reply = post(world, snapshot, 'active-instance', {'active_instance': 'other'})
    assert reply.status_code == 400 and 'Unknown account' in reply.get_json()['error']
    assert Path(world.CONFIG_PATH).read_bytes() == before and get(world)['context']['instance'] == 'main'


@pytest.mark.parametrize('values', [{'active_instance': 'ghost'}, {'active_instance': 1}, {'active_instance': None}, {'active_instance': ''}, {'active_instance': ['main']}])
def test_a_switch_to_an_unconfigured_emulator_is_refused(world, values):
    snapshot, before = get(world), Path(world.CONFIG_PATH).read_bytes()
    assert post(world, snapshot, 'active-instance', values).status_code == 400 and Path(world.CONFIG_PATH).read_bytes() == before


def sparse(cfg):
    """Accounts that store only what they were given: no loadouts, no tournament tweaks, a profile list nobody sorted, or no profile list at all."""
    cfg['accounts']['alice'] = {'active_profile': 'alice', 'profiles': ['zz_old', 'alice'], 'label': 'Alice'}
    cfg['accounts']['bob'] = {'active_profile': 'bob'}


def test_switching_the_selected_emulator_writes_only_the_selection_even_for_sparse_accounts(world):
    """r12b review R2: the switch used to run accounts.persist, which fills in the defaults of the OLD account ({} loadouts, {} tweaks, a sorted profile
    list) - an account record changed by a write that only selects an emulator. The whole stored file now differs from before in `active_instance` alone."""
    edit(world, sparse)
    for target in ('other', 'main', 'other'):
        snapshot, before = get(world), stored(world)
        reply = post(world, snapshot, 'active-instance', {'active_instance': target})
        assert reply.status_code == 200, reply.get_json()
        assert stored(world) == dict(before, active_instance=target)
        assert stored(world)['accounts'] == {'alice': {'active_profile': 'alice', 'profiles': ['zz_old', 'alice'], 'label': 'Alice'}, 'bob': {'active_profile': 'bob'}}
        for account in ('alice', 'bob'):
            assert yaml.safe_dump(stored(world)['accounts'][account]) == yaml.safe_dump(before['accounts'][account]), account


def test_the_answer_after_a_sparse_switch_is_the_new_emulators_context_with_defaults_only_in_the_reading(world):
    edit(world, sparse)
    reply = post(world, get(world), 'active-instance', {'active_instance': 'other'}).get_json()
    assert reply['context'] == {'instance': 'other', 'account': 'bob', 'profile': 'bob'}
    assert value(reply, 'tournament-cards', 'drop') == [] and value(reply, 'tournament-cards', 'add') == []        # the default is READ, never stored
    for account in ('alice', 'bob'):                    # alice is the OLD emulator's account: it is the record the switch used to fill in
        assert 'tourney_card_tweaks' not in stored(world)['accounts'][account] and 'loadouts' not in stored(world)['accounts'][account], account


def test_save_active_instance_selects_without_persisting_any_account(world):
    edit(world, sparse)
    before = stored(world)
    world.save_active_instance('other')
    assert stored(world) == dict(before, active_instance='other')


def test_a_save_that_edits_the_old_emulators_account_still_files_it_under_that_account(world):
    """The ordinary save is unchanged: accounts.persist files the fields read for the selected emulator under its own account."""
    cfg = world.load_config()
    cfg['loadouts'] = {'edited_for_alice': {}}
    world.save_config(cfg)
    after = stored(world)
    assert after['accounts']['alice']['loadouts'] == {'edited_for_alice': {}}
    assert after['accounts']['bob']['loadouts'] == {'bob_load': {'cards': 'y'}} and after['loadouts'] == {'legacy_only': {}}


# ---------------------------------------------------------------- the machine fallback preset is defined once
def test_dashboard_runnable_presets_is_the_catalogues_definition(dash, monkeypatch):
    assert dash.runnable_presets({'presets': {'a': {}, 'b': {'defined': False}, 'c': 'not a body'}}) == ['a']
    monkeypatch.setattr(settings_catalog, 'runnable_presets', lambda cfg: ['sentinel'])
    assert dash.runnable_presets({'presets': {'a': {}}}) == ['sentinel']                    # one definition: the dashboard asks the catalogue
