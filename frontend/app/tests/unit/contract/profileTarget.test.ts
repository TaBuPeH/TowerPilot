// @vitest-environment node
import { expect, it } from 'vitest';
import { entries, own } from '../../../src/core/read.ts';
import { ENDPOINTS, parseAnswer } from '../../../src/api/wire.ts';
import {
  DASHBOARD_PAGES_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { moreCase, moreNames } from '../../support/profileTargetMore.ts';

const family = own(
  own(loadJsonWithIdentity(DASHBOARD_PAGES_FIXTURE_PATH).fixture, 'cases'),
  'profile_target',
);

it.each(moreNames)(
  'PE1 F6-14c profile_target_more.%s is bound to its recorded wire endpoint',
  (name) => {
    const found = moreCase(name);
    const endpoint =
      found.request.path === '/api/profile-files'
        ? ENDPOINTS.profileFiles
        : found.request.method === 'POST'
          ? ENDPOINTS.profilePatch
          : ENDPOINTS.profileSource;
    // The recorded list failure is the default HTML page, not a JSON answer.
    const raw = own(found.body, 'text');
    const answer = parseAnswer(
      endpoint,
      found.status,
      typeof raw === 'string' ? raw : JSON.stringify(found.body),
    );
    expect(answer.contract).toBe('exact');
    if (typeof raw === 'string') expect(answer.json).toBe(false);
  },
);
const endpoints = [
  ENDPOINTS.config,
  ENDPOINTS.profileFiles,
  ENDPOINTS.profileSource,
  ENDPOINTS.profilePatch,
  ENDPOINTS.profileActivate,
];

it.each(entries(family).map(([name]) => name))(
  'PE1 PE2 profile_target.%s is registered on its wire endpoint',
  (name) => {
    const found = dashboardPagesCase(`profile_target.${name}`);
    const paths: Readonly<Record<string, string>> = {
      '/api/config': 'config',
      '/api/profile-files': 'profileFiles',
      '/api/profile-patch': 'profilePatch',
      '/api/profile-activate': 'profileActivate',
    };
    const endpoint = endpoints.find(
      (entry) =>
        entry.method === found.request.method &&
        (found.request.path.startsWith('/api/profile-src/')
          ? entry.id === 'profileSource'
          : paths[found.request.path] === entry.id),
    );
    if (endpoint === undefined) throw new Error(`unregistered ${found.request.path}`);
    expect(parseAnswer(endpoint, found.status, JSON.stringify(found.body)).contract).toBe('exact');
  },
);
