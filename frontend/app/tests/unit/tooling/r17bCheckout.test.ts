// @vitest-environment node
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  DASHBOARD_FIXTURE_PATH,
  DASHBOARD_PAGES_FIXTURE_PATH,
  SYNTHETIC_ACTION_FIXTURE_PATH,
  familyHashes,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import {
  CONTRACT_INPUTS,
  PAGES_ACTIONS_DESCRIPTOR,
  familyIdentity,
  record,
} from '../../../scripts/generate-contract.mjs';
import { REPO } from '../../support/paths.ts';

/**
 * Approved e02b205 dashboard fixtures and the r17c-landed (b23bf8c) synthetic fixture.
 * Pins fail closed on changed or missing checkout bytes.
 */
describe('AC6 landed r17b checkout provenance', () => {
  it.each([
    [DASHBOARD_FIXTURE_PATH, 'a1adee857b2810076eb125970c5dd7f0e0d5439cb16e3e3560ef8c46e593c66c'],
    [
      DASHBOARD_PAGES_FIXTURE_PATH,
      'c2a1afdfc7a7adbdd5a8e321468ba917992e5436e7ca8f03a1073681149fc7cf',
    ],
    [
      SYNTHETIC_ACTION_FIXTURE_PATH,
      '46a200bb4d40204f7c7107ce394ab3ac34fcc2bd9b8944b371961439955da6d8',
    ],
  ])('pins the approved checkout bytes at %s', (file, sha256) => {
    expect(loadJsonWithIdentity(file).identity.sha256).toBe(sha256);
  });

  it('binds the actions generator directly to the checkout and the approved three families', () => {
    const input = CONTRACT_INPUTS.find(({ descriptor }) => descriptor === PAGES_ACTIONS_DESCRIPTOR);
    expect(input?.file).toBe(DASHBOARD_PAGES_FIXTURE_PATH);
    expect(familyIdentity(DASHBOARD_PAGES_FIXTURE_PATH, PAGES_ACTIONS_DESCRIPTOR)).toEqual({
      path: DASHBOARD_PAGES_FIXTURE_PATH,
      sha256: '86fbbe581e6fbe545016a280ff2d4bf688c45f410aea7f2007a9b17201586dc8',
      families: {
        action_executions: '271D75FE6AADB6E706D10C92A97E96E7836231742247ACB2D06ACABE22117558',
        action_executions_more: '9CC62825D8266E852BB11224624D3A60D90EBAF9C5EC9ABD44D3E1094F51589E',
        battle_actions_projection:
          'CA68FE30F3463847A682329EB65889BEB6F3956E7D08AEF624171F7EF48B18A8',
      },
    });
  });

  it('retains the synthetic fixture as test-only data, outside app contract generation', () => {
    const fixture = record(loadJsonWithIdentity(SYNTHETIC_ACTION_FIXTURE_PATH).fixture);
    expect(fixture['test_only']).toBe(true);
    expect(fixture['generated_by']).toBe('backend/tests/test_synthetic_action_contract.py');
    expect(CONTRACT_INPUTS.some(({ file }) => file === SYNTHETIC_ACTION_FIXTURE_PATH)).toBe(false);
    expect(familyHashes(SYNTHETIC_ACTION_FIXTURE_PATH)).toEqual({
      synthetic_action: '4FCD49C736E877EB86C4F97F9E6822D93CAE9E15FF0503B4DCD68912802E29F6',
    });
    const cases = record(fixture['cases']);
    expect(Object.keys(cases)).toEqual(['synthetic_action']);
    expect(Object.keys(record(cases['synthetic_action']))).toHaveLength(33);
  });

  it('AC7 the synthetic action id appears in no non-test frontend file', () => {
    const fixture = record(loadJsonWithIdentity(SYNTHETIC_ACTION_FIXTURE_PATH).fixture);
    const cases = record(record(fixture['cases'])['synthetic_action']);
    const request = record(record(cases['synthetic_run_now_accepted'])['request']);
    const token = record(request['body'])['action'];
    expect(typeof token).toBe('string');
    const id = String(token).split('@')[0];
    expect(id).toBeTruthy();
    // Includes untracked source, honors .gitignore (builds and dependencies are not source).
    const listFiles = (...options: string[]) =>
      execFileSync('git', ['ls-files', '-z', ...options, 'frontend'], {
        cwd: REPO,
        encoding: 'utf8',
      })
        .split('\0')
        .filter(Boolean);
    // A file the working tree already deleted (a pruned generated module) is still listed as
    // cached until its removal is staged; it holds no source, so it is not read.
    const deleted = new Set(listFiles('--deleted'));
    const files = listFiles('--cached', '--others', '--exclude-standard').filter(
      (file) => !deleted.has(file),
    );
    const found = files.filter(
      (file) =>
        !/(?:^|\/)(?:tests|e2e)(?:\/|$)|\.(?:test|spec)\.[cm]?[jt]sx?$/u.test(file) &&
        readFileSync(path.join(REPO, file)).includes(String(id)),
    );
    expect(found).toEqual([]);
  });
});
