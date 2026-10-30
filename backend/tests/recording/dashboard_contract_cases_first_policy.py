"""Contract cases of the first gathering policy of an account that has none (Collection finding F6C-R1-04).

Family collection_first_policy. The page review found that a profile with no gathering policies offered only "Copy a policy",
with nothing to copy. A supported creation path exists today and needs no backend change:

  1. GET /api/run-templates publishes the shipped recipes. Each template carries its gathering policy at
     templates[i].policies.gather[<reference name>] (farm, tournament and dissonance: `all_on`; shard: `gems_only`): these are
     the authoritative defaults, so the browser invents none.
  2. POST /api/profile-patch {name, path: ['policies', 'gather', <new name>], value: <that policy>, expected_revision} creates the
     policy. The route creates a missing MAPPING step, so a profile whose `policies` has an empty `gather` map, or no `gather`
     key at all, takes its first policy the same way; the real validator then judges the whole profile.
  3. A run is bound to it by a second patch of ['blueprints', <run>, 'policies', 'gather'].

Two behaviours the page must know, both recorded: a patch to a name that already exists REPLACES that policy (it is not a
creation: the page must not offer a name that is taken, and sends expected_revision), and the empty policy `{}` is refused by
the validator (a gathering policy needs its gem delay).

The account is the world's populated profile with its gathering policies removed and no run bound to one.
"""
import copy

import yaml

from dashboard_contract_pages_world import populate

F = 'collection_first_policy'
PATCH = '/api/profile-patch'
GATHER = ['policies', 'gather']


def without_gathering(profile, *, keep_empty_map=True):
    """The account of the finding: no gathering policy and no run bound to one."""
    if keep_empty_map:
        profile['policies']['gather'] = {}
    else:
        profile['policies'].pop('gather')
    for blueprint in profile['blueprints'].values():
        blueprint.setdefault('policies', {}).pop('gather', None)


def source(w):
    return w.client.get('/api/profile-src/mine').get_json()


def patch(w, case, path, *, value=None, revision=None, delete=False, expect=None):
    body = dict(name='mine', path=path)
    if revision is not None:
        body['expected_revision'] = revision
    if delete:
        body['op'] = 'delete'
    else:
        body['value'] = value
    reply = w.post(F, case, PATCH, body)
    if expect is not None:
        assert reply.status_code == expect, (case, reply.status_code, reply.get_json())
    return reply


def template_policy(w, template='farm'):
    """The shipped default the page offers: exactly what GET /api/run-templates publishes for that template."""
    catalogue = w.client.get('/api/run-templates').get_json()
    entry = next(t for t in catalogue['templates'] if t['id'] == template)
    reference = entry['blueprint']['policies']['gather']
    return reference, copy.deepcopy(entry['policies']['gather'][reference])


def collection_first_policy_cases(gen):
    with gen.world('cfp_empty_map') as w:
        populate(w, mutate=without_gathering)
        read = w.get(F, 'read_a_profile_with_no_gathering_policies', '/api/profile-src/mine').get_json()
        assert read['ok'] and read['profile']['policies']['gather'] == {} and not read['problems'], read
        revision = read['revision']
        catalogue = w.get(F, 'read_the_run_templates_that_carry_the_default_gathering_policies', '/api/run-templates').get_json()
        assert {t['id'] for t in catalogue['templates']} == {'farm', 'tournament', 'shard', 'dissonance'}
        reference, policy = template_policy(w, 'farm')
        assert reference == 'all_on' and policy['ad_gems'] is True and policy['gem_delay_sec'] == [3, 10]
        stale = patch(w, 'first_policy_with_a_stale_revision_is_refused', GATHER + ['farm_default'], value=policy, revision='0' * 64, expect=409).get_json()
        assert stale['code'] == 'revision_conflict' and stale['revision'] == revision
        for case, value in (('first_policy_that_is_empty_is_refused', {}), ('first_policy_as_text_is_refused', 'all_on'),
                            ('first_policy_with_an_unknown_key_is_refused', dict(policy, speed=3)),
                            ('first_policy_without_its_gem_delay_is_refused', {key: v for key, v in policy.items() if key != 'gem_delay_sec'})):
            reply = patch(w, case, GATHER + ['farm_default'], value=value, revision=revision, expect=400).get_json()
            assert reply['ok'] is False and reply['error']
        assert source(w)['revision'] == revision
        w.get(F, 'read_after_the_refusals_nothing_changed', '/api/profile-src/mine')
        created = patch(w, 'first_policy_created_from_the_farm_template_default', GATHER + ['farm_default'], value=policy, revision=revision, expect=200).get_json()
        assert created['ok'] and created['revision'] != revision
        revision = created['revision']
        got = w.get(F, 'read_after_the_first_policy_holds_it_unbound', '/api/profile-src/mine').get_json()
        assert got['profile']['policies']['gather'] == {'farm_default': policy}
        bound = patch(w, 'the_first_policy_bound_to_a_run', ['blueprints', 'farm', 'policies', 'gather'], value='farm_default', revision=revision, expect=200).get_json()
        revision = bound['revision']
        _, gems = template_policy(w, 'shard')
        second = patch(w, 'a_second_policy_from_another_template', GATHER + ['gems_only_copy'], value=gems, revision=revision, expect=200).get_json()
        revision = second['revision']
        replaced = patch(w, 'a_name_that_is_taken_is_replaced_not_refused', GATHER + ['farm_default'], value=dict(policy, ad_gems=False), revision=revision, expect=200).get_json()
        assert replaced['ok']
        final = w.get(F, 'read_after_the_replacement', '/api/profile-src/mine').get_json()
        assert final['profile']['policies']['gather']['farm_default']['ad_gems'] is False and set(final['profile']['policies']['gather']) == {'farm_default', 'gems_only_copy'}
    with gen.world('cfp_no_key') as w:
        populate(w, mutate=lambda profile: without_gathering(profile, keep_empty_map=False))
        read = w.get(F, 'read_a_profile_with_no_gather_key_at_all', '/api/profile-src/mine').get_json()
        assert read['ok'] and 'gather' not in read['profile']['policies'], read
        _, policy = template_policy(w, 'farm')
        created = patch(w, 'first_policy_creates_the_missing_gather_map', GATHER + ['farm_default'], value=policy, revision=read['revision'], expect=200).get_json()
        assert created['ok']
        got = w.get(F, 'read_after_the_gather_map_was_created', '/api/profile-src/mine').get_json()
        assert got['profile']['policies']['gather'] == {'farm_default': policy}