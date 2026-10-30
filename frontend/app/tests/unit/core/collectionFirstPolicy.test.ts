import { describe, expect, it } from 'vitest';
import { postEndpoint, readEndpoint } from '../../../src/api/client.ts';
import { ENDPOINTS, type EndpointId } from '../../../src/api/wire.ts';
import { collectionSource, policyIdCheck } from '../../../src/core/collectionPolicies.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import {
  profilePatchAnswer,
  sentPatch,
  settleProfilePatch,
} from '../../../src/core/profilePatch.ts';
import { gatherDefaults } from '../../../src/core/runTemplates.ts';
import { firstPolicyCase, firstPolicyCaseNames } from '../../support/collectionFirstPolicy.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { createFakeTransport } from '../../support/fakeTransport.ts';

const TEMPLATES = 'read_the_run_templates_that_carry_the_default_gathering_policies';
const CREATED = 'first_policy_created_from_the_farm_template_default';
const EMPTY = 'read_a_profile_with_no_gathering_policies';

describe('C-01 C5 F6C-R1-04 recorded first-policy family', () => {
  it.each(firstPolicyCaseNames)('C-01 C5 F6C-R1-04 fake transport preserves %s', async (name) => {
    const found = firstPolicyCase(name);
    const fake = createFakeTransport();
    const method = found.request.method === 'GET' ? 'GET' : 'POST';
    const path = found.request.path;
    // ApiPath is a template literal type; all fixture paths are checked before use.
    if (!path.startsWith('/api/')) throw new Error('Unexpected fixture path');
    const apiPath = `/api/${path.slice(5)}` satisfies `/api/${string}`;
    const endpoint: EndpointId =
      method === 'POST'
        ? 'profilePatch'
        : path === '/api/run-templates'
          ? 'runTemplates'
          : 'profileSource';
    fake.route(method, path, () => ({ status: found.status, body: found.body }));
    const result =
      method === 'GET'
        ? await readEndpoint(fake.transport, ENDPOINTS[endpoint], apiPath)
        : await postEndpoint(
            fake.transport,
            ENDPOINTS[endpoint],
            apiPath,
            JSON.stringify(found.request.body),
          );
    if (result.kind !== 'answered') throw new Error('Fixture answer was lost');
    expect(result.answer.contract).toBe('exact');
    expect(result.answer.issues).toEqual([]);
    expect(result.answer.body).toEqual(found.body);
    expect(fake.requests).toHaveLength(1);
    if (method === 'POST') {
      expect(JSON.parse(fake.requests[0]?.body ?? 'null')).toEqual(found.request.body);
      expect(profilePatchAnswer(result.answer).kind).toBe(
        found.status === 200 ? 'done' : 'refused',
      );
    } else if (endpoint === 'runTemplates') {
      expect(
        gatherDefaults(result.answer.body).map((entry) => [entry.templateId, entry.ref]),
      ).toEqual([
        ['farm', 'all_on'],
        ['tournament', 'all_on'],
        ['shard', 'gems_only'],
        ['dissonance', 'all_on'],
      ]);
    } else {
      expect(collectionSource(result.answer.body)?.revision).toBe(own(found.body, 'revision'));
    }
  });

  it('C-01 C5 F6C-R1-04 the farm default is the exact recorded create value without an added label', () => {
    const defaults = gatherDefaults(firstPolicyCase(TEMPLATES).body);
    expect(defaults[0]).toMatchObject({ templateId: 'farm', templateLabel: 'Farm', ref: 'all_on' });
    expect(defaults[0]?.value).toEqual(own(firstPolicyCase(CREATED).request.body, 'value'));
    expect(own(defaults[0]?.value, 'label')).toBeUndefined();
  });

  it.each([
    undefined,
    null,
    {},
    { ok: false, templates: [] },
    { templates: 'bad' },
    { templates: [{ id: 'farm', label: 'Farm' }] },
    {
      templates: [
        { id: 'farm', label: 'Farm', policies: { gather: { empty: {}, text: 'bad', array: [] } } },
      ],
    },
    { templates: [{ label: 'Farm', policies: { gather: { invalid: { ad_gems: true } } } }] },
  ])('C-01 C5 F6C-R1-04 unreadable or absent defaults yield nothing: %j', (body) => {
    expect(gatherDefaults(body)).toEqual([]);
  });

  it('C-01 C5 F6C-R1-04 taken ids from the source or settings are refused, including before the source catches up', () => {
    const source = collectionSource(
      firstPolicyCase('read_after_the_first_policy_holds_it_unbound').body,
    );
    const empty = collectionSource(firstPolicyCase(EMPTY).body);
    if (source === null || empty === null) throw new Error('Source missing');
    expect(policyIdCheck(' farm_default ', source)).toBe('id_taken');
    const settings = dashboardPagesCase('settings.populated_account_every_module').body;
    expect(policyIdCheck('all_on', empty, settings)).toBe('id_taken');
    expect(policyIdCheck('gems_only', empty, settings)).toBe('id_taken');
    expect(policyIdCheck('new_policy', empty, settings)).toBeNull();
    // Shape boundary: settings may list a gather policy without its module.
    expect(
      policyIdCheck('listed', empty, { policies: [{ section: 'gather', id: 'listed' }] }),
    ).toBe('id_taken');
  });

  it('C-01 C5 F6C-R1-04 only a later read holding the template value proves a lost first save applied, unbound', () => {
    const patch = sentPatch(firstPolicyCase(CREATED).request.body);
    if (patch === null) throw new Error('Patch missing');
    expect(settleProfilePatch(firstPolicyCase(EMPTY).body, patch)).toBeNull();
    const readback = firstPolicyCase('read_after_the_first_policy_holds_it_unbound').body;
    expect(settleProfilePatch(readback, patch)).toBe('applied');
    expect(collectionSource(readback)?.policies[0]?.usedBy).toEqual([]);
    expect(ownPath(readback, 'profile', 'policies', 'gather', 'farm_default')).toEqual(patch.value);
  });
});
