import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { sendProfileWrite, type ProfileWrite } from '../../../../src/api/intents/profiles.ts';
import { machineHarness, postBodies } from '../../../support/dataLayer.ts';
import { ENDPOINTS, parseAnswer } from '../../../../src/api/wire.ts';
import { profilesContextCases } from '../../../support/profiles-context.ts';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const context = { instance: 'main', account: 'alpha', profile: 'mine', serial: 'fake:1' } as const;

it.each(profilesContextCases)(
  'F6PE-P02 retains genuine r10 context_fence.%s at the Profiles wire boundary',
  (_name, found) => {
    const endpoint =
      found.request.path === '/api/accounts'
        ? 'accountsWrite'
        : found.request.path === '/api/account-tier'
          ? 'accountTier'
          : found.request.path === '/api/profile-activate'
            ? 'profileActivate'
            : found.request.path === '/api/profile-copy'
              ? 'profileCopy'
              : 'profilePromote';
    const parsed = parseAnswer(ENDPOINTS[endpoint], found.status, JSON.stringify(found.body));
    expect(parsed.contract).toBe('exact');
    expect(parsed.body).toEqual(found.body);
    expect(parsed.issues).toEqual([]);
  },
);

it.each<readonly [ProfileWrite, string, Readonly<Record<string, unknown>>]>([
  [{ kind: 'account_select', id: 'beta' }, '/api/accounts', { id: 'beta' }],
  [
    { kind: 'account_create', id: 'alpha_clone', label: null, importCurrent: true },
    '/api/accounts',
    { id: 'alpha_clone', create: true, import_current: true },
  ],
  [{ kind: 'account_tier', maxTier: 7 }, '/api/account-tier', { max_tier: 7 }],
  [{ kind: 'profile_activate', name: 'second' }, '/api/profile-activate', { name: 'second' }],
  [
    { kind: 'profile_copy', from: 'mine', name: 'copy', overwrite: false, existed: false },
    '/api/profile-copy',
    { from: 'mine', name: 'copy' },
  ],
  [
    {
      kind: 'profile_promote',
      draft: 'mine.draft.yaml',
      name: 'promoted',
      base: 'mine',
      overwrite: false,
      existed: false,
    },
    '/api/profile-promote',
    { draft: 'mine.draft.yaml', name: 'promoted', base: 'mine' },
  ],
])('F6PE-P02 %j sends the full reviewed context exactly once to %s', async (write, path, body) => {
  const { machine, fake } = machineHarness({ context });
  sendProfileWrite(machine, write, context, null);
  expect(postBodies(fake, path)).toEqual([{ ...body, expected_context: context }]);
  expect(machine.snapshot()[0]?.context).toEqual(context);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(postBodies(fake, path)).toHaveLength(1);
  machine.dispose();
});
