"""Stable codes on the refusals of POST /api/run-templates/add (r12c; root request (a) of the F6-11 part 2 review).

A page that adds a run must tell "refused, nothing was written" from "unknown, read the profile". The route answered every failure 400/409 with only
a text, so a page could not rely on any of them. A request that carries a guard - `expected_revision` and/or `expected_context` - now gets a `code` on
every refusal the route can prove happened BEFORE anything was written:

  id_taken                   400  the run identifier (or a policy that identifier would create) exists already
  recipe_refused             400  the recipe or the choices were refused (bad identifier, unknown template, unsupported choice, ...)
  validation_failed          400  the profile validator refused the result; the answer also carries `problems`
  profile_unavailable        400/409  the profile cannot be added to: no such profile, unreadable, not a mapping, the starter or a scan draft
  write_failed_nothing_saved 400  the write failed AND the profile file is byte-identical to what it was before the attempt

revision_conflict, context_changed and invalid_request (409/409/400) are unchanged. A failure after which the profile file differs from before, or
cannot be read back, has NO code: the outcome is unknown and the page settles it by reading the profile. A request without a guard is answered
exactly as it always was, without a code. Every test drives the real Flask route over a temporary root (conftest `dash`): no live file, device,
ledger or lease.
"""
import hashlib
import os

import pytest
import yaml

from test_run_template_add_guards import URL, add, backups, path_of, revision, runs, world   # noqa: F401  (the fixture and helpers of the guard tests)

ALPHA = dict(instance='main', account='alpha', profile='mine', serial='fake:1')
CODES = {'id_taken', 'recipe_refused', 'validation_failed', 'profile_unavailable', 'write_failed_nothing_saved'}


def post(dash, **body):
    return dash.app.test_client().post(URL, json=body)


def raw(dash, text):
    return dash.app.test_client().post(URL, data=text, content_type='application/json')


def guarded(dash, how, **body):
    """The same request with each kind of guard: the revision, the context, both."""
    if how in ('revision', 'both'):
        body['expected_revision'] = revision(dash, body.get('profile') if isinstance(body.get('profile'), str) and os.path.exists(path_of(dash, body['profile'])) else 'mine')
    if how in ('context', 'both'):
        body['expected_context'] = ALPHA
    return post(dash, **body)


HOWS = ('revision', 'context', 'both')


def recipe(**extra):
    base = dict(profile='mine', template='farm', run_id='fresh', options={'label': 'Fresh run'})
    base.update(extra)
    return base


def mine_bytes(dash):
    return open(path_of(dash), 'rb').read()


# ---------------------------------------------------------------------------------------------------------- the classes of refusal
@pytest.mark.parametrize('how', HOWS)
@pytest.mark.parametrize('request_body,status,code', [
    (recipe(run_id='farm'), 400, 'id_taken'),                                                   # the run exists
    (recipe(run_id='Fresh Run'), 400, 'recipe_refused'),                                        # not an identifier
    (recipe(run_id=''), 400, 'recipe_refused'),
    (recipe(run_id=7), 400, 'recipe_refused'),
    (recipe(template='ghost'), 400, 'recipe_refused'),                                          # unknown template
    (recipe(template=None), 400, 'recipe_refused'),
    (recipe(options={'speed': 3}), 400, 'recipe_refused'),                                      # unsupported choice
    (recipe(options=['plain']), 400, 'recipe_refused'),                                         # choices not an object
    (recipe(options={'enable_uw': 'yes'}), 400, 'recipe_refused'),
    (recipe(template='shard', options={'loadout': 'plain', 'enable_uw': True}), 400, 'recipe_refused'),
    (recipe(template='dissonance', options={'loadout': 'plain', 'dissonant_tab': 'sideways'}), 400, 'recipe_refused'),
    (recipe(template='dissonance', options={'loadout': 'plain', 'perk_bans': 'coins'}), 400, 'recipe_refused'),
    (recipe(options={'tier': 0}), 400, 'validation_failed'),                                    # the validator refuses the result
    (recipe(options={'loadout': 'ghost'}), 400, 'validation_failed'),
    (recipe(template='tournament', options={'label': 'Tourney'}), 400, 'validation_failed'),    # its default loadout does not exist
    (recipe(profile='ghost'), 400, 'profile_unavailable'),                                      # no such profile
    (recipe(profile='../outside'), 400, 'profile_unavailable'),
    (recipe(profile=5), 400, 'profile_unavailable'),
    (recipe(profile=None), 400, 'profile_unavailable'),
])
def test_a_guarded_refusal_before_the_write_carries_its_code_and_writes_nothing(world, how, request_body, status, code):
    before, saved = mine_bytes(world), backups(world)
    unguarded = post(world, **request_body)
    reply = guarded(world, how, **request_body)
    assert reply.status_code == status == unguarded.status_code, (reply.get_json(), unguarded.get_json())
    got = reply.get_json()
    assert got['ok'] is False and got['code'] == code and got['code'] in CODES, got
    assert {k: v for k, v in got.items() if k != 'code'} == unguarded.get_json(), 'the code is the only difference from the unguarded answer'
    assert 'code' not in unguarded.get_json()
    assert mine_bytes(world) == before and backups(world) == saved


@pytest.mark.parametrize('how', HOWS)
def test_the_validators_problems_stay_in_the_answer(world, how):
    got = guarded(world, how, **recipe(options={'tier': 0})).get_json()
    assert got['code'] == 'validation_failed' and got['problems'] and got['error'] == got['problems'][0]


@pytest.mark.parametrize('how', HOWS)
def test_a_copied_policy_that_exists_already_is_an_identifier_that_is_taken(world, how):
    path = path_of(world)
    profile = yaml.safe_load(open(path, encoding='utf-8'))
    profile['policies'].setdefault('gather', {})['pre__all_on'] = {'ad_gems': True}                          # the policy the farm recipe would copy for a run named `pre`
    open(path, 'w', encoding='utf-8').write(yaml.safe_dump(profile, sort_keys=False))
    before = mine_bytes(world)
    reply = guarded(world, how, **recipe(run_id='pre', options={'label': 'Pre run'}))
    assert reply.status_code == 400 and reply.get_json()['code'] == 'id_taken' and 'already exists' in reply.get_json()['error']
    assert mine_bytes(world) == before


@pytest.mark.parametrize('how', HOWS)
def test_the_starter_and_a_scan_draft_are_profiles_that_cannot_be_added_to(world, how):
    base = open(path_of(world), 'rb').read()
    for name in ('default', 'mine.draft'):
        open(path_of(world, name), 'wb').write(base)
    for name in ('default', 'mine.draft'):
        before = open(path_of(world, name), 'rb').read()
        reply = guarded(world, how, **recipe(profile=name))
        assert reply.status_code == 409 and reply.get_json()['code'] == 'profile_unavailable', (name, reply.get_json())
        assert open(path_of(world, name), 'rb').read() == before


@pytest.mark.parametrize('how', ('context', 'both'))
def test_a_profile_file_that_cannot_be_read_as_a_profile_is_unavailable(world, how):
    for name, text in (('broken', 'blueprints: [unclosed\n'), ('listed', '- a\n'), ('empty', '')):
        open(path_of(world, name), 'w', encoding='utf-8').write(text)
        reply = guarded(world, how, **recipe(profile=name))
        assert reply.status_code == 400 and reply.get_json()['code'] == 'profile_unavailable', (name, reply.get_json())
        assert open(path_of(world, name), 'r', encoding='utf-8').read() == text
    open(path_of(world, 'latin'), 'wb').write(b'label: caf\xe9\n')                           # not UTF-8: reading it raises ValueError, not OSError
    reply = guarded(world, how, **recipe(profile='latin'))
    assert reply.status_code == 400 and reply.get_json()['code'] == 'profile_unavailable', reply.get_json()


def test_a_revision_guard_on_a_profile_that_is_not_there_is_unavailable(world):
    reply = post(world, **recipe(profile='ghost', expected_revision='0' * 64))
    assert reply.status_code == 400 and reply.get_json()['code'] == 'profile_unavailable'


# ---------------------------------------------------------------------------------------------------------- the write
def failing_save(dash, monkeypatch, *, write_first=False, remove_after=False):
    real = dash._save_yaml_backup

    def save(path, data):
        if write_first:
            real(path, data)
        if remove_after:
            os.remove(path)
        raise OSError('profiles/mine.yaml could not be written')
    monkeypatch.setattr(dash, '_save_yaml_backup', save)


@pytest.mark.parametrize('how', HOWS)
def test_a_write_that_failed_and_left_the_file_as_it_was_says_so(world, monkeypatch, how):
    failing_save(world, monkeypatch)
    before = mine_bytes(world)
    reply = guarded(world, how, **recipe())
    assert reply.status_code == 400
    got = reply.get_json()
    assert got['code'] == 'write_failed_nothing_saved' and got['error'] == 'profiles/mine.yaml could not be written'
    assert mine_bytes(world) == before


@pytest.mark.parametrize('how', HOWS)
def test_a_write_that_failed_after_the_file_changed_has_no_code(world, monkeypatch, how):
    failing_save(world, monkeypatch, write_first=True)
    before = mine_bytes(world)
    reply = guarded(world, how, **recipe())
    assert reply.status_code == 400 and 'code' not in reply.get_json(), reply.get_json()
    assert mine_bytes(world) != before and 'fresh' in runs(world)


def test_a_failed_write_whose_file_cannot_be_read_back_has_no_code(world, monkeypatch):
    failing_save(world, monkeypatch, remove_after=True)
    reply = guarded(world, 'both', **recipe())
    assert reply.status_code == 400 and 'code' not in reply.get_json(), reply.get_json()


def test_the_revision_read_after_a_committed_write_failing_is_not_a_refusal(world, monkeypatch):
    """The file is written, then every read of it fails (the one that makes the answer's `revision`, and the one that would prove nothing was
    saved): the add happened, so the answer cannot be a refusal and has no code."""
    real, real_path = world._save_yaml_backup, world.Path

    class Unreadable:
        def __init__(self, target):
            pass

        def read_bytes(self):
            raise OSError('the saved profile could not be read back')

    def save_then_lose_the_reads(path, data):
        backup = real(path, data)
        monkeypatch.setattr(world, 'Path', Unreadable)
        return backup
    monkeypatch.setattr(world, '_save_yaml_backup', save_then_lose_the_reads)
    reply = post(world, **recipe(expected_revision=revision(world)))
    assert reply.status_code == 400 and 'code' not in reply.get_json(), reply.get_json()
    monkeypatch.setattr(world, 'Path', real_path)
    assert 'fresh' in runs(world)


# ---------------------------------------------------------------------------------------------------------- what is unchanged
def test_a_request_without_a_guard_never_gets_a_code(world, monkeypatch):
    cases = [recipe(run_id='farm'), recipe(run_id='Fresh Run'), recipe(options={'tier': 0}), recipe(profile='ghost'), recipe(profile='../outside')]
    for body in cases:
        got = post(world, **body).get_json()
        assert got['ok'] is False and 'code' not in got, (body, got)
    failing_save(world, monkeypatch)
    assert 'code' not in post(world, **recipe()).get_json()
    listed = raw(world, '[1]')
    assert listed.status_code == 400 and 'code' not in listed.get_json()


def test_the_guards_keep_their_own_codes_and_their_order(world):
    stale = post(world, **recipe(run_id='Fresh Run', expected_revision='0' * 64))
    assert stale.status_code == 409 and stale.get_json()['code'] == 'revision_conflict'                 # the revision is judged before the recipe
    assert stale.get_json()['revision'] == revision(world)
    other = dict(ALPHA, account='beta', profile='theirs')
    changed = post(world, **recipe(run_id='Fresh Run', expected_revision='0' * 64, expected_context=other))
    assert changed.status_code == 409 and changed.get_json()['code'] == 'context_changed'               # the context before the revision
    malformed = post(world, **recipe(expected_context=None))
    assert malformed.status_code == 400 and malformed.get_json()['code'] == 'invalid_request'


@pytest.mark.parametrize('how', HOWS)
def test_a_successful_add_has_no_code(world, how):
    reply = guarded(world, how, **recipe())
    assert reply.status_code == 200 and reply.get_json()['ok'] is True and 'code' not in reply.get_json() and 'fresh' in runs(world)


# ---------------------------------------------------------------------------------------------------------- the recipe layer
def test_the_recipe_layer_names_an_identifier_that_is_taken_and_nothing_else():
    from player import run_templates
    profile = {'blueprints': {'farm': {}}, 'policies': {}}
    with pytest.raises(run_templates.RunIdTaken) as taken:
        run_templates.instantiate(profile, 'farm', 'farm', {})
    assert isinstance(taken.value, ValueError) and 'already exists' in str(taken.value)
    for bad in (('ghost', 'new_run', {}), ('farm', 'Not Valid', {}), ('farm', 'new_run', {'speed': 1}), ('farm', 'new_run', [])):
        with pytest.raises(ValueError) as refused:
            run_templates.instantiate(profile, *bad)
        assert not isinstance(refused.value, run_templates.RunIdTaken), bad
