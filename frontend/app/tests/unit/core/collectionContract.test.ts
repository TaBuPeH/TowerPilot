import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer, type EndpointId } from '../../../src/api/wire.ts';
import {
  choreNames,
  choreRows,
  choresValue,
  collectionSource,
  copiedPolicy,
  policyIdCheck,
} from '../../../src/core/collectionPolicies.ts';
import {
  profilePatchAnswer,
  sentPatch,
  settleProfilePatch,
} from '../../../src/core/profilePatch.ts';
import {
  checkField,
  reviewModule,
  settingsModule,
  settingsSaveAnswer,
  settingsView,
  settleSettingsSave,
  type SettingsField,
} from '../../../src/core/settingsFields.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';

/**
 * The Collection page's rules (collection brief C1 to C6) and the shared settings-module
 * rules against every answer the backend recorded for them
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json, families settings and
 * collection_policies, and the vocabulary of profile_reads), each parsed at the app's wire
 * boundary first, as the page receives it.
 */
function wire(endpoint: EndpointId, found: DashboardCase) {
  const body = found.body as Record<string, unknown>;
  const text =
    Object.keys(body).length === 1 && typeof body['text'] === 'string'
      ? body['text']
      : JSON.stringify(body);
  return parseAnswer(ENDPOINTS[endpoint], found.status, text);
}

function endpointOf(found: DashboardCase): EndpointId | null {
  const { method, path } = found.request;
  if (path === '/api/settings') return method === 'GET' ? 'settings' : 'settingsSave';
  if (path === '/api/vocab') return 'vocab';
  if (path.startsWith('/api/profile-src/')) return 'profileSource';
  if (path === '/api/profile-patch') return 'profilePatch';
  return null;
}

const CASES = [
  ...['settings', 'collection_policies'].flatMap((family) =>
    dashboardPagesCaseNames(family).map((name) => `${family}.${name}`),
  ),
  ...dashboardPagesCaseNames('profile_reads')
    .map((name) => `profile_reads.${name}`)
    .filter((path) => dashboardPagesCase(path).request.path === '/api/vocab'),
].flatMap((path) => {
  const endpoint = endpointOf(dashboardPagesCase(path));
  return endpoint === null ? [] : [[path, endpoint] as const];
});

describe('the Collection contract at the wire boundary', () => {
  it.each(CASES)('C1-C6 %s is an exact %s answer and keeps every member', (path, endpoint) => {
    const parsed = wire(endpoint, dashboardPagesCase(path));
    expect(parsed.contract).toBe('exact');
    expect(parsed.issues).toEqual([]);
  });
});

const read = (path: string) => wire('settings', dashboardPagesCase(path)).body;
const EVERY = settingsView(read('settings.populated_account_every_module'));
function field(moduleId: string, path: string): SettingsField {
  const found = settingsModule(EVERY, moduleId)?.fields.find((entry) => entry.path === path);
  if (found === undefined) throw new Error(`${moduleId} ${path} missing`);
  return found;
}

describe('C2 C3 a field draft is judged against the server metadata, never clamped', () => {
  it.each([
    ['ad:all_on', 'gem_delay_sec', { 'gem_delay_sec#0': '3.5' }, 'not_whole', 0],
    ['ad:all_on', 'gem_delay_sec', { 'gem_delay_sec#1': '' }, 'empty', 1],
    [
      'ad:all_on',
      'gem_delay_sec',
      { 'gem_delay_sec#0': '12', 'gem_delay_sec#1': '4' },
      'reversed',
      null,
    ],
    ['ad:all_on', 'gem_delay_sec', { 'gem_delay_sec#1': '1e9' }, 'not_a_number', 1],
    ['ad:all_on', 'gem_delay_sec', { 'gem_delay_sec#1': 'Infinity' }, 'not_a_number', 1],
    [
      'ad:all_on',
      'ad_gem_options.edge_inset_px',
      { 'ad_gem_options.edge_inset_px': '4' },
      'below_min',
      null,
    ],
    [
      'ad:all_on',
      'ad_gem_options.edge_inset_px',
      { 'ad_gem_options.edge_inset_px': '6.5' },
      'not_whole',
      null,
    ],
    [
      'ad:all_on',
      'ad_gem_options.attempt_timeout_sec',
      { 'ad_gem_options.attempt_timeout_sec': '61' },
      'above_max',
      null,
    ],
    ['orbit:all_on', 'gem_orbit.center', { 'gem_orbit.center#0': '-1' }, 'below_min', 0],
    ['orbit:all_on', 'gem_orbit.center', { 'gem_orbit.center#1': '2560' }, 'above_max', 1],
    ['orbit:all_on', 'gem_orbit.center', { 'gem_orbit.center#0': '5,6' }, 'not_a_number', 0],
    [
      'orbit:all_on',
      'gem_orbit.interval_sec',
      { 'gem_orbit.interval_sec': '0' },
      'below_min',
      null,
    ],
    ['orbit:all_on', 'gem_orbit.taps', { 'gem_orbit.taps': 'three' }, 'not_a_number', null],
    ['orbit:all_on', 'gem_orbit.taps', { 'gem_orbit.taps': '21' }, 'above_max', null],
  ] as const)('C2 C3 %s %s %j is invalid: %s (part %s)', (moduleId, path, draft, reason, part) => {
    expect(checkField(field(moduleId, path), draft)).toEqual({ kind: 'invalid', reason, part });
  });

  it.each([
    ['ad:all_on', 'gem_delay_sec', { 'gem_delay_sec#0': '4', 'gem_delay_sec#1': '12' }, [4, 12]],
    ['ad:all_on', 'ad_gem_options.poll_sec', { 'ad_gem_options.poll_sec#0': '2.5' }, [2.5, 4]],
    [
      'orbit:all_on',
      'gem_orbit.center',
      { 'gem_orbit.center#0': '540', 'gem_orbit.center#1': '900' },
      [540, 900],
    ],
    ['orbit:all_on', 'gem_orbit.angle_deg', { 'gem_orbit.angle_deg': '-90' }, -90],
    ['gather:all_on', 'guild_store', { guild_store: true }, true],
  ] as const)('C2 C3 C4 %s %s %j sends %j', (moduleId, path, draft, value) => {
    expect(checkField(field(moduleId, path), draft)).toEqual({ kind: 'valid', value });
  });

  it('C2 an unedited or unchanged field is not sent; only changed fields are', () => {
    const ad = settingsModule(EVERY, 'ad:all_on');
    if (ad === null) throw new Error('ad module missing');
    expect(reviewModule(ad, {}).values).toEqual({});
    expect(reviewModule(ad, { 'gem_delay_sec#0': '3' }).values).toEqual({});
    expect(reviewModule(ad, { ad_gems: false, 'gem_delay_sec#0': '3' }).values).toEqual({
      ad_gems: false,
    });
  });

  it('C3 the orbit center bounds are the native layout the server reports (0 to 2559)', () => {
    expect(field('orbit:all_on', 'gem_orbit.center')).toMatchObject({
      min: 0,
      max: 2559,
      unit: 'px',
    });
  });
});

describe('C1 C2 C3 C4 every recorded settings save answer', () => {
  const posts = CASES.filter(([, endpoint]) => endpoint === 'settingsSave');
  it.each(posts)(
    'SR-4 %s is classified by its status and receipt; only a receipt is saved',
    (path) => {
      const found = dashboardPagesCase(path);
      const body = found.request.body;
      const sent = {
        module:
          typeof body === 'object' && body !== null
            ? (body as { module?: string }).module
            : undefined,
        values:
          typeof body === 'object' && body !== null
            ? (body as { values?: Record<string, unknown> }).values
            : undefined,
      };
      const answer = settingsSaveAnswer(wire('settingsSave', found), sent);
      if (found.status >= 500) expect(answer).toEqual({ kind: 'unknown' });
      else if (found.status === 400 || found.status === 409) expect(answer.kind).toBe('refused');
      else expect(answer.kind).toBe('saved');
    },
  );

  it('C1 a 200 that does not hold the sent value is no receipt: Unknown', () => {
    const found = dashboardPagesCase('settings.saved_rewards');
    expect(
      settingsSaveAnswer(wire('settingsSave', found), {
        module: 'rewards',
        values: { event_missions: true },
      }),
    ).toEqual({ kind: 'unknown' });
  });

  it('SR-4 a stale revision is refused with the server reason, before any value is judged', () => {
    const found = dashboardPagesCase('settings.stale_revision_is_judged_before_the_values');
    const answer = settingsSaveAnswer(wire('settingsSave', found), {});
    expect(answer).toMatchObject({ kind: 'refused', status: 409 });
  });
});

describe('SR-4 a later settings read settles a save whose answer was lost, never a resend', () => {
  const sent = { module: 'tap', values: { jitter_px: 6 }, revision: '0'.repeat(62) + '52' };
  const contextOf = (path: string) => {
    const named = settingsView(read(path))?.context;
    if (named == null) throw new Error(`${path} names no context`);
    return named;
  };
  it.each([
    ['settings.read_after_the_unanswered_save_holds_the_value', 'applied'],
    // F6C-R1-02: an unchanged revision cannot exclude a commit still to come: unknown.
    ['settings.read_after_the_failed_writes_the_revision_is_unchanged', null],
    ['settings.read_after_the_stale_refusal_holds_the_other_clients_save', null],
  ] as const)('F6C-R1-02 %s is %s', (path, expected) => {
    expect(settleSettingsSave(read(path), sent, contextOf(path))).toBe(expected);
  });

  it('F6C-R1-05 a read holding the values for another account, profile or emulator settles nothing', () => {
    const path = 'settings.read_after_the_unanswered_save_holds_the_value';
    const own = contextOf(path);
    expect(settleSettingsSave(read(path), sent, own)).toBe('applied');
    for (const other of [
      { ...own, account: 'beta' },
      { ...own, profile: 'theirs' },
      { ...own, instance: 'other' },
    ])
      expect(settleSettingsSave(read(path), sent, other)).toBeNull();
  });
});

const source = (path: string) => wire('profileSource', dashboardPagesCase(path)).body;

describe('C5 C6 the profile source: policies, the runs that use them, the chores', () => {
  it('C5 lists every gathering policy with the runs that use it', () => {
    const view = collectionSource(source('collection_policies.read_before_the_first_save'));
    expect(view?.policies.map((p) => [p.id, p.usedBy])).toEqual([
      ['all_on', ['farm']],
      ['gems_only', ['farm_deep', 'shards']],
      ['hands_off', ['tourney']],
    ]);
  });

  it('C5 a run naming a policy that does not exist is a problem to show', () => {
    const view = collectionSource(
      source('collection_policies.a_run_bound_to_a_missing_policy_is_a_problem_the_page_must_show'),
    );
    expect(view?.missing.length).toBeGreaterThan(0);
  });

  it('C5 a copy keeps every field of its source, unknown ones included, with the new label', () => {
    const view = collectionSource(source('collection_policies.read_before_the_first_save'));
    const policy = view?.policies.find((p) => p.id === 'all_on');
    if (policy === undefined || view === null) throw new Error('all_on missing');
    expect(copiedPolicy(policy, 'Weekend')).toEqual({
      ...(policy.body as object),
      label: 'Weekend',
    });
    expect(copiedPolicy(policy, '')).toEqual(policy.body);
    expect(policyIdCheck('weekend', view)).toBeNull();
    expect(policyIdCheck('all_on', view)).toBe('id_taken');
    expect(policyIdCheck('Weekend', view)).toBe('bad_id');
    expect(policyIdCheck('', view)).toBe('enter_id');
  });

  it('C6 chores are an opt-out list: unlisted known chores are on; switching one off appends it', () => {
    const view = collectionSource(source('collection_policies.read_before_the_first_save'));
    const names = choreNames(dashboardPagesCase('profile_reads.vocab_ok').body);
    expect(names).toEqual(['quest_scan', 'shatter', 'housekeeping']);
    expect(choreRows(view, names).map((row) => [row.name, row.enabled])).toEqual([
      ['quest_scan', true],
      ['shatter', true],
      ['housekeeping', true],
    ]);
    expect(choresValue(view, { shatter: false, quest_scan: false })).toEqual([
      { name: 'quest_scan', enabled: false },
      { name: 'shatter', enabled: false },
    ]);
  });
});

describe('C4 C5 C6 every recorded profile patch answer', () => {
  const posts = CASES.filter(([, endpoint]) => endpoint === 'profilePatch');
  it.each(posts)(
    'SR-4 %s is classified by its status; only a 200 with the profile is done',
    (path) => {
      const found = dashboardPagesCase(path);
      const answer = profilePatchAnswer(wire('profilePatch', found));
      if (found.status >= 500) expect(answer).toEqual({ kind: 'unknown' });
      else if (found.status >= 400) expect(answer.kind).toBe('refused');
      else expect(answer.kind).toBe('done');
    },
  );

  it('C5 deleting a policy a run uses is refused with the problems the validator found', () => {
    const answer = profilePatchAnswer(
      wire(
        'profilePatch',
        dashboardPagesCase('collection_policies.policy_deleted_while_a_run_uses_it'),
      ),
    );
    expect(answer.kind).toBe('refused');
    if (answer.kind === 'refused') expect(answer.problems.length).toBe(2);
  });

  it('SR-4 a later source read settles a lost patch: the chores read holds the sent list', () => {
    const patch = sentPatch(
      dashboardPagesCase('collection_policies.chores_reordered_and_one_switched_off').request.body,
    );
    if (patch === null) throw new Error('not a patch');
    expect(
      settleProfilePatch(
        source('collection_policies.read_after_the_saves_holds_every_change'),
        patch,
      ),
    ).toBe('applied');
    // F6C-R1-02: a read still at the patch's own revision cannot exclude a later commit.
    expect(
      settleProfilePatch(source('collection_policies.read_before_the_first_save'), {
        ...patch,
        revision:
          collectionSource(source('collection_policies.read_before_the_first_save'))?.revision ??
          null,
      }),
    ).toBeNull();
    // A read of another profile says nothing about this patch.
    expect(
      settleProfilePatch(source('collection_policies.read_after_the_saves_holds_every_change'), {
        ...patch,
        name: 'theirs',
      }),
    ).toBeNull();
  });

  it('SR-4 a delete is applied when the read no longer has the path', () => {
    const patch = sentPatch(
      dashboardPagesCase('collection_policies.policy_nothing_uses_deleted').request.body,
    );
    if (patch === null) throw new Error('not a patch');
    expect(patch.remove).toBe(true);
    expect(
      settleProfilePatch(source('collection_policies.read_before_the_first_save'), patch),
    ).toBe('applied');
  });
});
