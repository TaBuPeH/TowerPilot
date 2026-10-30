import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import {
  bodyMembers,
  ENDPOINTS,
  parseAnswer,
  projectBody,
  typesOf,
  type Endpoint,
  type EndpointId,
} from '../../../src/api/wire.ts';
import { readHarvest } from '../../../src/core/readability.ts';
import {
  dashboardCase,
  dashboardCaseNames,
  dashboardFamilies,
} from '../../support/dashboardFixture.ts';
import { sample, sampleNames, sampleRecord, statusBody } from '../../support/fixture.ts';
import { LOADED_ENDPOINTS } from '../../support/loadedContract.ts';

/**
 * The API boundary (spec 7.1): every answer of both loaded fixtures parses
 * through the endpoint's generated strict schemas as `exact` and keeps every
 * member at the wire level; a member of a type the contract does not record
 * is dropped (Unknown) while the rest of the body stays; and the strict level
 * alone would refuse ordinary production values, which is why the page acts
 * on the wire level.
 */
const endpoints = LOADED_ENDPOINTS;

const DASHBOARD_ENDPOINTS: Readonly<Record<string, EndpointId>> = {
  accounts: 'accounts',
  battle_actions: 'actions',
  battle_detail: 'battleDetail',
  battles_active: 'battlesActive',
  battles_history: 'battles',
  calibrate_status: 'calibrate',
  config: 'config',
  control: 'control',
  control_unknown_outcomes: 'control',
  diagnostics_client: 'diagnostics',
  handoff: 'handoff',
  readiness: 'readiness',
  reconnect: 'reconnect',
  run_controls: 'runControls',
  runs: 'runs',
  schedule_status: 'schedule',
  wizard_emulators: 'wizardEmulators',
  wizard_state: 'wizardState',
  wizard_tools: 'wizardTools',
};

function bodyText(body: unknown): string {
  const raw = typeof body === 'object' && body !== null && !Array.isArray(body) ? body : null;
  const keys = raw === null ? [] : Object.keys(raw);
  // The fixtures record a non-JSON page as {text}; on the wire it is that text.
  if (keys.length === 1 && keys[0] === 'text') {
    const value: unknown = Object.values(raw ?? {})[0];
    if (typeof value === 'string') return value;
  }
  return JSON.stringify(body);
}

function expectExact(endpoint: Endpoint, status: number, body: unknown) {
  const answer = parseAnswer(endpoint, status, bodyText(body));
  expect(answer.issues).toEqual([]);
  expect(answer.contract).toBe('exact');
  expect(answer.body).toEqual(body);
}

describe('the wire level is derived from the generated strict schemas', () => {
  it('knows every schema kind the generated modules use', () => {
    for (const endpoint of Object.values(ENDPOINTS)) {
      expect(endpoint.members.size).toBeGreaterThan(0);
    }
    expect(typesOf(z.union([z.literal(1), z.null(), z.enum(['a'])]))).toEqual([
      'number',
      'null',
      'string',
    ]);
    expect(() => typesOf(z.date())).toThrow(/does not know/);
  });

  it('records each member with every JSON type a variant allows', () => {
    const members = bodyMembers([
      z.strictObject({ status: z.literal(200), body: z.strictObject({ a: z.string() }) }),
      z.strictObject({
        status: z.literal(409),
        body: z.strictObject({ a: z.null(), b: z.number() }),
      }),
    ]);
    expect([...(members.get('a') ?? [])].sort()).toEqual(['null', 'string']);
    expect([...(members.get('b') ?? [])]).toEqual(['number']);
  });
});

describe('every fixture answer is exact at the strict level and whole at the wire level', () => {
  for (const [family, endpointId] of Object.entries(DASHBOARD_ENDPOINTS)) {
    it.each(dashboardCaseNames(family))(`${family}.%s`, (name) => {
      const recorded = dashboardCase(`${family}.${name}`);
      expectExact(endpoints[endpointId], recorded.status, recorded.body);
    });
  }

  it.each(dashboardCaseNames('write_unknown_outcomes'))('write_unknown_outcomes.%s', (name) => {
    const recorded = dashboardCase(`write_unknown_outcomes.${name}`);
    const endpointId: EndpointId = recorded.request.path.endsWith('/cancel')
      ? 'cancel'
      : recorded.request.path === '/api/automation-access'
        ? 'automationAccess'
        : recorded.request.path === '/api/setup-battle/collection-handoff'
          ? 'handoff'
          : recorded.request.path === '/api/config'
            ? 'config'
            : 'cancel';
    expectExact(endpoints[endpointId], recorded.status, recorded.body);
  });

  it('covers every Dashboard fixture family (R8-09): each is parsed by a test of this file', () => {
    const covered = new Set([
      ...Object.keys(DASHBOARD_ENDPOINTS),
      // GET /api/status needs its harvest from the other fixture: the next test.
      'status',
      'write_unknown_outcomes',
    ]);
    expect(dashboardFamilies().length).toBeGreaterThan(covered.size - 1);
    expect(dashboardFamilies().filter((family) => !covered.has(family))).toEqual([]);
  });

  it.each(dashboardCaseNames('status'))(
    'GET /api/status root case status.%s with a readable harvest',
    (name) => {
      const recorded = dashboardCase(`status.${name}`);
      expect(recorded.status).toBe(200);
      expectExact(endpoints.status, 200, {
        ...(recorded.body as object),
        harvest: sample('status.off'),
      });
    },
  );

  it.each(sampleNames('status'))('GET /api/status with harvest status.%s', (name) => {
    const body = {
      ...(dashboardCase('status.processes_empty').body as object),
      harvest: sample(`status.${name}`),
    };
    expectExact(endpoints.status, 200, body);
  });

  it.each(sampleNames('status'))('GET /api/harvest with status.%s', (name) => {
    expectExact(endpoints.harvest, 200, { ok: true, harvest: sample(`status.${name}`) });
  });

  const families: readonly (readonly [string, EndpointId])[] = [
    ['post_harvest', 'harvestWrite'],
    ['automation_access', 'automationAccess'],
    ['stop_operation.cancel', 'cancel'],
    ['stop_operation.kill', 'control'],
    ['start_transfer', 'control'],
  ];
  for (const [family, endpointId] of families) {
    it.each(sampleNames(family))(`${family}.%s`, (name) => {
      const answer = sampleRecord(`${family}.${name}`);
      expectExact(endpoints[endpointId], Number(answer['status']), answer['body']);
    });
  }

  it.each(sampleNames('refusals'))(
    'refusals.%s through every route that refuses for an owner',
    (name) => {
      const answer = sampleRecord(`refusals.${name}`);
      for (const endpointId of ['harvestWrite', 'automationAccess', 'cancel', 'control'] as const) {
        expectExact(endpoints[endpointId], Number(answer['status']), answer['body']);
      }
    },
  );
});

describe('unknown wire values become Unknown, never a guess', () => {
  it('drops only the malformed member; the others stay for core to judge', () => {
    const body = statusBody(sample('status.collecting'));
    const answer = parseAnswer(ENDPOINTS.status, 200, JSON.stringify({ ...body, processes: 'x' }));
    expect(answer.contract).toBe('drift');
    expect(answer.issues).toContain('dropped:processes');
    expect(answer.body).toEqual({
      harvest: body['harvest'],
      process_refresh: body['process_refresh'],
    });
    expect(readHarvest((answer.body as Record<string, unknown>)['harvest']).kind).toBe('known');
  });

  it('drops a member the contract does not know', () => {
    const answer = parseAnswer(
      ENDPOINTS.config,
      200,
      JSON.stringify({ active_instance: 'main', surprise: 1 }),
    );
    expect(answer.body).toEqual({ active_instance: 'main' });
    expect(answer.issues).toContain('dropped:surprise');
  });

  it('keeps a body that is not JSON as {text}, and refuses a body that is not an object', () => {
    expect(parseAnswer(ENDPOINTS.control, 500, '<!doctype html>')).toMatchObject({
      json: false,
      body: { text: '<!doctype html>' },
    });
    expect(parseAnswer(ENDPOINTS.status, 200, '[1]').body).toBeUndefined();
    expect(projectBody(null, ENDPOINTS.status.members).dropped).toEqual(['body_not_object']);
  });

  it('freezes what it keeps', () => {
    const answer = parseAnswer(
      ENDPOINTS.status,
      200,
      JSON.stringify(statusBody(sample('status.collecting'))),
    );
    expect(Object.isFrozen(answer.body)).toBe(true);
    expect(Object.isFrozen((answer.body as Record<string, unknown>)['harvest'])).toBe(true);
  });

  it('the strict level refuses ordinary production values; the wire level keeps them (why runtime is wire)', () => {
    const config = dashboardCase('config.taps_false').body as Record<string, unknown>;
    // An install whose emulator is not named like the fixture's, with an account bound.
    const production = {
      ...config,
      active_instance: 'bluestacks',
      instances: { bluestacks: { serial: '127.0.0.1:5555', allow_taps: false, account: 'acct1' } },
    };
    const answer = parseAnswer(ENDPOINTS.config, 200, JSON.stringify(production));
    expect(answer.contract).toBe('drift');
    expect(answer.body).toEqual(production);
    const harvest = sampleRecord('status.off');
    harvest['identity'] = { ...(harvest['identity'] as object), account: 'acct1' };
    const status = parseAnswer(ENDPOINTS.harvest, 200, JSON.stringify({ ok: true, harvest }));
    expect(status.contract).toBe('drift');
    expect(readHarvest((status.body as Record<string, unknown>)['harvest']).kind).toBe('known');
  });
});
