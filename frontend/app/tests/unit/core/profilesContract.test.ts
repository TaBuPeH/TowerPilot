import { describe, expect, it } from 'vitest';
import { ENDPOINTS, parseAnswer, type EndpointId } from '../../../src/api/wire.ts';
import {
  activationCheck,
  checkTier,
  destinationCheck,
  profileFilesView,
  profileSourceView,
  profileWriteAnswer,
  settleProfileWrite,
  settlementScope,
  type ProfileWriteKind,
  type SentProfileWrite,
} from '../../../src/core/profiles.ts';
import {
  dashboardPagesCase,
  dashboardPagesCaseNames,
  type DashboardCase,
} from '../../support/dashboardFixture.ts';

/**
 * The Profiles page's rules (profiles brief P1 to P5) against every answer the
 * backend recorded for them (backend/tests/fixtures/dashboard_contract_pages_v1.json,
 * families config_state, profile_reads, accounts_write, profile_files_write),
 * each parsed at the app's wire boundary first, as the page receives it.
 */
const FAMILIES = ['config_state', 'profile_reads', 'accounts_write', 'profile_files_write'];

function wire(endpoint: EndpointId, found: DashboardCase) {
  const body = found.body as Record<string, unknown>;
  const text =
    Object.keys(body).length === 1 && typeof body['text'] === 'string'
      ? body['text']
      : JSON.stringify(body);
  return parseAnswer(ENDPOINTS[endpoint], found.status, text);
}

/** The endpoint the page reads or writes a recorded request through, else null (not this page's). */
function endpointOf(found: DashboardCase): EndpointId | null {
  const { method, path } = found.request;
  if (method === 'GET') {
    if (path === '/api/config') return 'config';
    if (path === '/api/accounts') return 'accounts';
    if (path === '/api/profile-files') return 'profileFiles';
    if (path.startsWith('/api/profile-src/')) return 'profileSource';
    return null;
  }
  if (path === '/api/accounts') return 'accountsWrite';
  if (path === '/api/account-tier') return 'accountTier';
  if (path === '/api/profile-activate') return 'profileActivate';
  if (path === '/api/profile-copy') return 'profileCopy';
  if (path === '/api/profile-promote') return 'profilePromote';
  return null;
}

function all(): readonly (readonly [string, EndpointId])[] {
  return FAMILIES.flatMap((family) =>
    dashboardPagesCaseNames(family).flatMap((name) => {
      const path = `${family}.${name}`;
      const endpoint = endpointOf(dashboardPagesCase(path));
      return endpoint === null ? [] : [[path, endpoint] as const];
    }),
  );
}

const text = (value: unknown): value is string => typeof value === 'string';

/**
 * What the page records for a write whose body has the page's own form
 * (src/api/intents/profiles.ts), else null: the page never sends a non-JSON
 * body, a missing or non-text name, an unbinding (null name) or a promotion
 * tied to a scan operation (Recognition's).
 */
function pageWrite(
  found: DashboardCase,
): { readonly kind: ProfileWriteKind; readonly sent: SentProfileWrite } | null {
  const body = found.request.body;
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return null;
  const fields = body as Record<string, unknown>;
  const keys = Object.keys(fields).sort().join(',');
  switch (found.request.path) {
    case '/api/accounts':
      if (!text(fields['id']) || fields['id'] === '') return null;
      if (keys === 'id') return { kind: 'account_select', sent: { name: fields['id'] } };
      if (fields['create'] !== true) return null;
      if (fields['label'] !== undefined && !text(fields['label'])) return null;
      if (fields['import_current'] !== undefined && fields['import_current'] !== true) return null;
      return { kind: 'account_create', sent: { name: fields['id'] } };
    case '/api/account-tier': {
      const tier = fields['max_tier'];
      if (keys !== 'max_tier' || typeof tier !== 'number' || !Number.isInteger(tier)) return null;
      return { kind: 'account_tier', sent: { maxTier: tier } };
    }
    case '/api/profile-activate':
      if (keys !== 'name' || !text(fields['name']) || fields['name'] === '') return null;
      return { kind: 'profile_activate', sent: { name: fields['name'] } };
    case '/api/profile-copy':
      if (!text(fields['from']) || !text(fields['name'])) return null;
      if (!['from,name', 'from,name,overwrite'].includes(keys)) return null;
      return {
        kind: 'profile_copy',
        sent: { name: fields['name'], existed: fields['overwrite'] === true },
      };
    case '/api/profile-promote':
      if (!text(fields['draft']) || !text(fields['name']) || !text(fields['base'])) return null;
      if (!['base,draft,name', 'base,draft,name,overwrite'].includes(keys)) return null;
      return {
        kind: 'profile_promote',
        sent: { name: fields['name'], existed: fields['overwrite'] === true },
      };
    default:
      return null;
  }
}

describe('the Profiles contract at the wire boundary', () => {
  it.each(all())('P1-P5 %s is an exact %s answer and keeps every member', (path, endpoint) => {
    const parsed = wire(endpoint, dashboardPagesCase(path));
    expect(parsed.contract).toBe('exact');
    expect(parsed.issues).toEqual([]);
  });
});

const WRITES = all().filter(([path]) => dashboardPagesCase(path).request.method === 'POST');

describe('P1 P2 P3 P5 every recorded Profiles write answer', () => {
  it('covers every write family of the page, each with page-form bodies', () => {
    const kinds = new Set(
      WRITES.flatMap(([path]) => {
        const write = pageWrite(dashboardPagesCase(path));
        return write === null ? [] : [write.kind];
      }),
    );
    expect([...kinds].sort()).toEqual([
      'account_create',
      'account_select',
      'account_tier',
      'profile_activate',
      'profile_copy',
      'profile_promote',
    ]);
  });

  it.each(WRITES)(
    'P1-P5 SR-4 %s is classified by its status and receipt; only a receipt is done',
    (path, endpoint) => {
      const found = dashboardPagesCase(path);
      const write = pageWrite(found);
      if (write === null) return;
      const parsed = wire(endpoint, found);
      const answer = profileWriteAnswer(write.kind, parsed, write.sent);
      if (found.status >= 500) expect(answer).toEqual({ kind: 'unknown' });
      else if ([400, 404, 409].includes(found.status)) expect(answer.kind).toBe('refused');
      else expect(answer.kind).toBe('done');
    },
  );

  it('P2 a creation sent as "Alpha" is done by the answer naming "alpha" (the server stores ids in lower case)', () => {
    const found = dashboardPagesCase('accounts_write.create_a_fresh_account');
    const answer = profileWriteAnswer('account_create', wire('accountsWrite', found), {
      name: 'Alpha',
    });
    expect(answer.kind).toBe('done');
  });

  it('P1 a 200 that names another account than the one sent is no receipt: Unknown', () => {
    const found = dashboardPagesCase('accounts_write.select_an_existing_account');
    expect(
      profileWriteAnswer('account_select', wire('accountsWrite', found), { name: 'beta' }),
    ).toEqual({ kind: 'unknown' });
  });

  it('P2 SR-4 the creation refused after its profile file was written is refused, with the server reason, and never claims nothing changed', () => {
    const found = dashboardPagesCase(
      'accounts_write.create_then_the_config_save_failed_leaves_the_profile_file',
    );
    const answer = profileWriteAnswer('account_create', wire('accountsWrite', found), {
      name: 'alpha',
    });
    expect(answer).toEqual({
      kind: 'refused',
      code: null,
      error: 'config.yaml could not be written',
      problems: [],
    });
    // The orphan file is in the next files read; the account is not in the accounts read.
    const files = profileFilesView(
      wire(
        'profileFiles',
        dashboardPagesCase('accounts_write.profile_files_after_the_failed_create_list_the_orphan'),
      ).body,
    );
    expect(files?.files.map((file) => file.name)).toContain('account_alpha');
  });

  it('P5 a promotion saved with problems is done with its problems and is not active', () => {
    const found = dashboardPagesCase(
      'profile_files_write.promotion_returns_the_problems_it_does_not_enforce_them',
    );
    const answer = profileWriteAnswer('profile_promote', wire('profilePromote', found), {
      name: 'promoted',
      existed: false,
    });
    expect(answer.kind).toBe('done');
    if (answer.kind === 'done') expect(answer.problems.length).toBeGreaterThan(0);
  });

  it('P5 an activation answered 500 is Unknown even though its body says ok: false', () => {
    const found = dashboardPagesCase('profile_files_write.activate_write_failed_500');
    expect(
      profileWriteAnswer('profile_activate', wire('profileActivate', found), { name: 'second' }),
    ).toEqual({ kind: 'unknown' });
  });
});

describe('SR-4 a later read settles a write whose answer was lost, never a resend', () => {
  const accounts = (name: string) => wire('accounts', dashboardPagesCase(name)).body;
  const files = (name: string) => wire('profileFiles', dashboardPagesCase(name)).body;

  it.each([
    ['account_select', { name: 'alpha' }, 'accounts_write.read_after_select', 'applied'],
    ['account_select', { name: 'alpha' }, 'accounts_write.read_before_any_account', null],
    [
      'account_create',
      { name: 'Alpha' },
      'accounts_write.read_after_create_names_the_account_active',
      'applied',
    ],
    [
      'account_create',
      { name: 'alpha' },
      'accounts_write.read_after_the_failed_create_no_account',
      null,
    ],
    ['account_tier', { maxTier: 12 }, 'accounts_write.read_after_the_tier', null],
    [
      'account_tier',
      { maxTier: 12 },
      'accounts_write.read_after_the_failed_tier_write_unchanged',
      null,
    ],
  ] as const)('P1 P2 P3 %s %j read by %s is %s', (kind, sent, read, expected) => {
    expect(settleProfileWrite(kind, sent, { accounts: accounts(read), files: null })).toBe(
      expected,
    );
  });

  it.each([
    ['mine', 1, 'mine', 1, 'applied'],
    ['mine', 1, 'theirs', 1, null],
    ['mine', 1, 'mine', 12, null],
    ['mine', 1, 'mine', undefined, null],
    [null, 1, 'mine', 1, null],
  ] as const)(
    'F6P-R2-01 tier %s/%s is confirmed only by its own explicit source %s/%s',
    (profile, maxTier, name, savedTier, expected) => {
      const found = dashboardPagesCase(
        'accounts_write.profile_source_after_the_tier_records_the_operator_as_provenance',
      );
      const body = found.body as Record<string, unknown>;
      const saved = body['profile'] as Record<string, unknown>;
      const source = {
        ...body,
        name,
        profile: { ...saved, player: savedTier === undefined ? {} : { max_tier: savedTier } },
      };
      expect(
        settleProfileWrite(
          'account_tier',
          { profile, maxTier },
          { accounts: accounts('accounts_write.read_before_the_tier'), files: null, source },
        ),
      ).toBe(expected);
    },
  );

  it.each([
    [
      'profile_activate',
      { name: 'second' },
      'profile_files_write.files_after_the_activation_name_the_active_profile',
      'applied',
    ],
    [
      'profile_activate',
      { name: 'second' },
      'profile_files_write.files_after_the_failed_activation_unchanged',
      null,
    ],
    [
      'profile_copy',
      { name: 'fresh', existed: false },
      'profile_files_write.files_after_the_copy_list_it_and_copy_activates_nothing',
      'applied',
    ],
    [
      'profile_copy',
      { name: 'fresh', existed: true },
      'profile_files_write.files_after_the_copy_list_it_and_copy_activates_nothing',
      null,
    ],
    [
      'profile_promote',
      { name: 'promoted', existed: false },
      'profile_files_write.files_after_the_failed_promotion_have_no_new_profile',
      null,
    ],
  ] as const)(
    'P5 %s %j read by %s is %s (an overwrite stays unknown)',
    (kind, sent, read, expected) => {
      expect(settleProfileWrite(kind, sent, { accounts: null, files: files(read) })).toBe(expected);
    },
  );

  it('P5 SR-4 an unreadable read settles nothing', () => {
    expect(
      settleProfileWrite('account_select', { name: 'alpha' }, { accounts: null, files: null }),
    ).toBeNull();
    expect(
      settleProfileWrite(
        'profile_copy',
        { name: 'spare', existed: false },
        { accounts: null, files: null },
      ),
    ).toBeNull();
  });
});

describe('P3 the tier is judged as typed, never clamped or rounded', () => {
  it.each([
    ['', { kind: 'invalid', reason: 'empty' }],
    ['0', { kind: 'invalid', reason: 'out_of_range' }],
    ['21', { kind: 'invalid', reason: 'out_of_range' }],
    ['3.5', { kind: 'invalid', reason: 'not_whole' }],
    ['-2', { kind: 'invalid', reason: 'not_whole' }],
    ['twelve', { kind: 'invalid', reason: 'not_whole' }],
    ['1', { kind: 'valid', value: 1 }],
    [' 20 ', { kind: 'valid', value: 20 }],
  ] as const)('P3 %j is %j', (raw, expected) => {
    expect(checkTier(raw)).toEqual(expected);
  });
});

describe('F6P-03 F6P-04 which context settles a write, and which destinations a copy or promotion may write', () => {
  it.each([
    ['account_select', 'emulator'],
    ['account_create', 'emulator'],
    ['account_tier', 'account_and_profile'],
    ['profile_activate', 'account'],
    ['profile_copy', 'account'],
    ['profile_promote', 'account'],
  ] as const)('SR-4 %s is settled only by a read under the same %s', (kind, scope) => {
    expect(settlementScope(kind)).toBe(scope);
  });

  const listed = profileFilesView(
    wire(
      'profileFiles',
      dashboardPagesCase('profile_reads.profile_files_with_a_draft_and_the_starter'),
    ).body,
  );

  it.each([
    ['', false, 'enter_name'],
    ['default', true, 'starter_name'],
    ['mine', true, 'active_name'],
    ['theirs', false, 'confirm_overwrite'],
    ['theirs', true, null],
    ['spare', false, null],
  ] as const)('P5 destination %j with consent %s is %s', (name, consented, expected) => {
    if (listed === null) throw new Error('list not readable');
    expect(destinationCheck(name, listed, consented)).toBe(expected);
  });
});

describe('P5 activation is offered only for a checked profile of the same name', () => {
  const source = (name: string) =>
    profileSourceView(wire('profileSource', dashboardPagesCase(name)).body);
  const listed = profileFilesView(
    wire(
      'profileFiles',
      dashboardPagesCase('profile_reads.profile_files_with_a_draft_and_the_starter'),
    ).body,
  );
  const file = (name: string) => listed?.files.find((entry) => entry.name === name) ?? null;

  it.each([
    ['mine', 'profile_reads.profile_src_valid', { kind: 'ready' }],
    ['mine', 'profile_reads.profile_src_with_problems', { kind: 'blocked', reason: 'problems' }],
    [
      'mine',
      'profile_reads.profile_src_validator_unavailable_is_reported_not_hidden',
      { kind: 'blocked', reason: 'validator_unavailable' },
    ],
    ['default', 'profile_reads.profile_src_valid', { kind: 'blocked', reason: 'unreadable' }],
  ] as const)('P5 %s checked by %s is %j', (name, read, expected) => {
    expect(activationCheck(file(name), source(read))).toEqual(expected);
  });

  it('P5 a scan draft is never ready, whatever its source says', () => {
    const draft = listed?.files.find((entry) => entry.draft) ?? null;
    expect(draft).not.toBeNull();
    expect(activationCheck(draft, source('profile_reads.profile_src_of_a_draft'))).toEqual({
      kind: 'blocked',
      reason: 'draft',
    });
  });
});
