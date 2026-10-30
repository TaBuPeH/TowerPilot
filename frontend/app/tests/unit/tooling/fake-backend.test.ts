// @vitest-environment node
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, IncomingMessage, ServerResponse } from 'node:http';
import { connect, Server, Socket } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Writable } from 'node:stream';
import { crc32, inflateSync } from 'node:zlib';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createApiHandler,
  loadFixture,
  resetAfterClientClose,
  startServer,
} from '../../support/fake-backend/fake-backend.mjs';
import { SCENARIOS, type Scenario, type WriteMode } from '../../support/fake-backend/scenarios.mjs';
import {
  DASHBOARD_FIXTURE_PATH,
  loadJsonWithIdentity,
} from '../../../scripts/contract-fixture.mjs';
import {
  ENDPOINTS,
  parseAnswer,
  type ReadEndpointId,
  type WriteEndpointId,
} from '../../../src/api/wire.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  dashboardPagesCaseNames,
} from '../../support/dashboardFixture.ts';
import { LOADED_ENDPOINTS } from '../../support/loadedContract.ts';

/**
 * Real HTTP objects, entirely in memory: tests/unit/setup.ts refuses sockets (R2h).
 * Loopback TCP has crashed Windows Vitest workers natively; sockets belong in
 * Playwright, whose fake backend is a separate process.
 */
type Handler = ReturnType<typeof createApiHandler>;
interface Reply {
  status: number;
  bytes: Buffer;
  text: string;
  body: unknown;
  cookies: readonly string[];
  location: string | number | readonly string[] | undefined;
  type: string | number | readonly string[] | undefined;
  allow: string | number | readonly string[] | undefined;
}
function request(
  api: Handler,
  method: string,
  url: string,
  { cookie = '', body = '' }: { cookie?: string; body?: string } = {},
) {
  const req = new IncomingMessage(new Socket());
  req.method = method;
  req.url = url;
  req.headers.cookie = cookie;
  req.httpVersion = '1.1';
  req.httpVersionMajor = 1;
  req.httpVersionMinor = 1;
  req.complete = true;
  const res = new ServerResponse(req);
  const wire: Buffer[] = [];
  const sink = new Writable({
    write(chunk: Buffer, _encoding: BufferEncoding, done: (error?: Error | null) => void) {
      wire.push(chunk);
      done();
    },
  });
  res.assignSocket(sink as unknown as Socket);
  const reply = new Promise<Reply>((resolve, reject) => {
    res.on('error', reject);
    res.on('finish', () => {
      const raw = Buffer.concat(wire);
      const bytes = raw.subarray(raw.indexOf('\r\n\r\n') + 4);
      const text = bytes.toString('utf8');
      let body: unknown = text;
      try {
        body = JSON.parse(text) as unknown;
      } catch {
        /* HTML errors and redirects are not JSON. */
      }
      const cookies = res.getHeader('set-cookie');
      resolve({
        status: res.statusCode,
        bytes,
        text,
        body,
        cookies: Array.isArray(cookies) ? cookies : [],
        location: res.getHeader('location'),
        type: res.getHeader('content-type'),
        allow: res.getHeader('allow'),
      });
    });
  });
  api(req, res);
  req.push(Buffer.from(body));
  req.push(null);
  return { req, res, reply };
}
function send(
  api: Handler,
  method: string,
  url: string,
  options: { cookie?: string; body?: string } = {},
): Promise<Reply> {
  return request(api, method, url, options).reply;
}
function cookiesOf(reply: Reply): string {
  return reply.cookies.map((value) => value.split(';')[0]).join('; ');
}

const fixture = loadFixture();
const anyNumber: unknown = expect.any(Number);
const anyArray: unknown = expect.any(Array);
const anyString: unknown = expect.any(String);
const dashboard = loadJsonWithIdentity(DASHBOARD_FIXTURE_PATH).fixture as {
  cases: Record<string, Record<string, { status: number; body: unknown }>>;
};
const detail = dashboard.cases['battle_detail']?.['ended_commands_expired_battle_ended']?.body as {
  battle: { id: string };
};
const READS: readonly (readonly [ReadEndpointId, string])[] = [
  ['status', '/api/status'],
  ['calibrate', '/api/calibrate/status'],
  ['battlesActive', '/api/battles/active'],
  ['schedule', '/api/schedule/status?instance=main'],
  ['battles', '/api/battles?limit=3&instance=main'],
  ['battleDetail', `/api/battles/${detail.battle.id}`],
  ['config', '/api/config'],
  ['accounts', '/api/accounts'],
  ['runs', '/api/runs'],
  ['wizardTools', '/api/wizard/tools'],
  ['wizardState', '/api/wizard/state'],
  ['wizardEmulators', '/api/wizard/emulators'],
  ['runControls', '/api/run-controls?blueprint=farm'],
  ['readiness', '/api/readiness?preset=bp_farm'],
  ['harvest', '/api/harvest'],
];
const WRITES: readonly (readonly [WriteEndpointId, string, string])[] = [
  ['harvestWrite', '/api/harvest', '{ "enabled": false, "expected_revision": 1 }\n'],
  [
    'harvestWrite',
    '/api/harvest',
    '{"enabled":true,"preset":"bp_farm","idempotency_key":"enable"}',
  ],
  ['automationAccess', '/api/automation-access', '{ "enabled": false }'],
  ['automationAccess', '/api/automation-access', '{ "enabled": true }'],
  ['cancel', '/api/operations/operation-1/cancel', '{"expected_revision":2}'],
  ['control', '/api/control', '{"action":"start","preset":"bp_farm","idempotency_key":"start"}'],
  [
    'control',
    '/api/control',
    '{"action":"start_gems","preset":"bp_farm","idempotency_key":"gems"}',
  ],
  ['control', '/api/control', '{"action":"kill","pid":4400,"created":1799990001.25}'],
  ['control', '/api/control', '{"action":"clear_flag","operation_id":"op","expected_revision":2}'],
  [
    'handoff',
    '/api/setup-battle/collection-handoff',
    '{"operation_id":"setup","setup_battle_id":"battle","evidence_revision":"rev","attestation":"new_manual_battle"}',
  ],
  ['reconnect', '/api/wizard/reconnect', '{}'],
  [
    'actions',
    '/api/operations/operation-1/actions',
    '{"battle_id":"battle","generation":"g1","expected_revision":0,"idempotency_key":"run","action":"ad_gems"}',
  ],
  [
    'liveControls',
    '/api/operations/operation-1/live-controls',
    '{"battle_id":"battle","generation":"g1","expected_revision":0,"idempotency_key":"ctl","changes":{"routines.ad_gems.enabled":false}}',
  ],
  ['diagnostics', '/api/diagnostics/client', '{"kind":"render_fault","message":"synthetic"}'],
];
afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

/**
 * Scenarios that serve bodies outside the generated contract on purpose: an incomplete Stop target
 * (F4-01), and the AC7 B1 recordings, whose synthetic routine instances the contract does not know.
 */
const MALFORMED: Readonly<Record<string, readonly string[]>> = {
  x9: ['status'],
  x9_taps: ['status'],
  stopping_no_revision: ['status', 'harvest'],
  ...Object.fromEntries(
    Object.keys(SCENARIOS)
      .filter((name) => name.startsWith('a2_7_b1_'))
      .map((name) => [name, ['battlesActive']]),
  ),
};

describe.each(Object.entries(SCENARIOS))('scenario %s', (name) => {
  it.each(READS)('serves %s with the page contract', async (id, path) => {
    const reply = await send(createApiHandler(fixture), 'GET', path, {
      cookie: `tp_fake_scenario=${name}`,
    });
    const parsed = parseAnswer(ENDPOINTS[id], reply.status, reply.text);
    // Deliberate drift: the generated contracts record no 502 answer for /status or /config.
    const failing = SCENARIOS[name]?.get?.[id] === '502';
    if (failing) {
      expect(reply.status).toBe(502);
      expect(reply.type).toBe('text/html; charset=utf-8');
      expect(parsed.json).toBe(false);
      expect(parsed.contract).toBe('drift');
    } else if (MALFORMED[name]?.includes(id) === true) {
      // The page still gets a 200 JSON answer; only the deliberately broken field drifts.
      expect(reply.status).toBe(200);
      expect(parsed.json).toBe(true);
      expect(parsed.contract).toBe('drift');
    } else {
      expect(reply.status).toBe(200);
      expect(parsed.contract, `${name} ${id}: ${parsed.issues.join(', ')}`).toBe('exact');
    }
  });
});

describe.each<WriteMode>(['ok', 'refused', 'unknown', 'drop', 'hold'])('write mode %s', (mode) => {
  it.each(WRITES)('records and answers %s %s %s', async (id, path, body) => {
    const api = createApiHandler(fixture, {
      scenario: 'test',
      scenarios: { test: { description: 'Mode probe', harvest: 'collecting', writeMode: mode } },
    });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=test'));
    if (mode === 'hold') vi.useFakeTimers();
    const sent = request(api, 'POST', path, { cookie, body });
    await new Promise<void>((resolve) => sent.req.on('end', resolve));
    if (mode === 'drop' || mode === 'hold') {
      expect(sent.req.socket.destroyed).toBe(mode === 'drop');
      expect(sent.res.writableEnded).toBe(false);
      if (mode === 'hold') {
        vi.advanceTimersByTime(15_001);
        expect(sent.res.writableEnded).toBe(false);
      }
    } else {
      const reply = await sent.reply;
      const parsed = parseAnswer(LOADED_ENDPOINTS[id], reply.status, reply.text);
      expect(parsed.contract, `${id}: ${parsed.issues.join(', ')}`).toBe(
        mode === 'unknown' && id === 'diagnostics' ? 'drift' : 'exact',
      );
      if (mode === 'unknown') expect(reply.status).toBeGreaterThanOrEqual(500);
      else if (mode === 'ok') expect(reply.status).toBeLessThan(300);
      else if (id === 'automationAccess' && body.includes('false')) {
        expect(reply.status).toBe(200); // Revocation can report a refusing owner in a 200 answer.
        expect(reply.body).toMatchObject({ revocation: { still_owned: anyArray } });
      } else expect(reply.status).toBeGreaterThanOrEqual(400);
    }
    expect((await send(api, 'GET', '/__fake/posts', { cookie })).body).toEqual({
      posts: id === 'diagnostics' ? [] : [{ method: 'POST', path, body, time: anyNumber }],
      diagnostics: id === 'diagnostics' ? [{ method: 'POST', path, body, time: anyNumber }] : [],
    });
  });
});

describe('controls and composition', () => {
  it('serves matching details for the battles listed by the active-routines preset', async () => {
    const api = createApiHandler(fixture, { scenario: 'active_battle_routines' });
    const history = (await send(api, 'GET', '/api/battles?limit=3&instance=main')).body as {
      battles: { id: string }[];
    };
    expect(history.battles.length).toBeGreaterThan(0);
    for (const battle of history.battles) {
      const reply = await send(api, 'GET', `/api/battles/${encodeURIComponent(battle.id)}`);
      expect(reply.status).toBe(200);
      expect(reply.body).toMatchObject({ battle: { id: battle.id } });
      expect(parseAnswer(ENDPOINTS.battleDetail, reply.status, reply.text).contract).toBe('exact');
    }
  });
  it('defaults to collecting; accepts the fallback scenario and old query links', async () => {
    const api = createApiHandler(fixture);
    expect((await send(api, 'GET', '/api/status')).body).toMatchObject({
      harvest: { code: 'collecting' },
    });
    expect((await send(api, 'GET', '/api/status?scenario=status_failed')).body).toMatchObject({
      harvest: { code: 'status_failed' },
    });
    expect(
      (await send(createApiHandler(fixture, { scenario: 'idle' }), 'GET', '/api/status')).body,
    ).toMatchObject({ harvest: { desired: { choice: 'off' } }, processes: [] });
  });
  it('uses Connect originalUrl when the /api mount strips req.url', async () => {
    const api = createApiHandler(fixture);
    const mounted: Handler = (req, res) => {
      req.originalUrl = req.url;
      req.url = req.url?.slice('/api'.length);
      api(req, res);
    };
    const status = await send(mounted, 'GET', '/api/status');
    expect(parseAnswer(LOADED_ENDPOINTS.status, status.status, status.text).contract).toBe('exact');
    expect(
      (await send(mounted, 'POST', '/api/harvest', { body: '{"enabled":false}' })).status,
    ).toBe(200);
    expect((await send(mounted, 'POST', '/api/nope')).status).toBe(404);
  });
  it('switches the scenario of a session in place with /__fake/set (a warm change; no redirect, no reset)', async () => {
    const api = createApiHandler(fixture);
    const used = await send(api, 'GET', '/__fake/use?scenario=idle');
    const session = cookiesOf(used)
      .split('; ')
      .filter((part) => part.startsWith('tp_fake_session='))
      .join('; ');
    const set = await send(api, 'GET', '/__fake/set?scenario=cold_502', {
      cookie: cookiesOf(used),
    });
    expect(set.status).toBe(200);
    expect(set.cookies).toEqual([expect.stringContaining('tp_fake_scenario=cold_502; Path=/;')]);
    const status = await send(api, 'GET', '/api/status', {
      cookie: `${session}; tp_fake_scenario=cold_502`,
    });
    expect(status.status).toBe(502);
  });
  it('sets independent scenario/session cookies, redirects, and composes the sketch evidence', async () => {
    const api = createApiHandler(fixture);
    const first = await send(api, 'GET', '/__fake/use?scenario=idle');
    const second = await send(api, 'GET', '/__fake/use?scenario=sketch');
    expect(first.status).toBe(302);
    expect(first.location).toBe('/ui/app/');
    expect(first.cookies).toEqual([
      expect.stringContaining('tp_fake_scenario=idle; Path=/;'),
      expect.stringContaining('tp_fake_session='),
    ]);
    expect(cookiesOf(first)).not.toBe(cookiesOf(second));
    expect(
      (await send(api, 'GET', '/api/status', { cookie: cookiesOf(first) })).body,
    ).toMatchObject({ processes: [], harvest: { desired: { choice: 'off' } } });
    const manual = fixture.status['manual_collector'] as {
      server_time: number;
      operation: { id: string; revision: number };
    };
    const sketch = await send(api, 'GET', '/api/status', { cookie: cookiesOf(second) });
    expect(sketch.body).toMatchObject({
      harvest: {
        desired: { choice: 'unset' },
        operation: {
          harvest_managed: false,
          battle: { fresh: true, publication_valid: true },
          routines: { ad_gems: { enabled: true } },
        },
      },
      processes: [
        { operation_id: manual.operation.id, operation_revision: manual.operation.revision },
      ],
      process_refresh: { state: 'fresh', age_sec: 0, last_success_at: manual.server_time },
      setup_battle_guard: { blocks_launch: true },
    });
    const stop = request(api, 'POST', '/api/operations/collector/cancel', {
      cookie: cookiesOf(second),
      body: '{"expected_revision":2}',
    });
    await new Promise<void>((resolve) => stop.req.on('end', resolve));
    expect(stop.res.writableEnded).toBe(false);
    expect((await send(api, 'GET', '/__fake/posts', { cookie: cookiesOf(first) })).body).toEqual({
      posts: [],
      diagnostics: [],
    });
  });
  it('resets only this session and records malformed and unknown POSTs', async () => {
    const api = createApiHandler(fixture);
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=idle'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=idle'));
    await send(api, 'POST', '/api/harvest', { cookie: a, body: '{"enabled":false}' });
    await send(api, 'POST', '/api/diagnostics/client', { cookie: a, body: '{}' });
    expect((await send(api, 'POST', '/api/nope', { cookie: b, body: '{}' })).status).toBe(404);
    expect((await send(api, 'POST', '/api/harvest', { cookie: b, body: 'bad JSON' })).status).toBe(
      400,
    );
    expect((await send(api, 'GET', '/__fake/reset', { cookie: a })).body).toEqual({ ok: true });
    expect((await send(api, 'GET', '/__fake/posts', { cookie: a })).body).toEqual({
      posts: [],
      diagnostics: [],
    });
    expect((await send(api, 'GET', '/__fake/posts', { cookie: b })).body).toMatchObject({
      posts: [{ body: '{}' }, { body: 'bad JSON' }],
      diagnostics: [],
    });
  });
  it.each(READS)('can make any GET return 502 (%s)', async (id, path) => {
    const api = createApiHandler(fixture, {
      scenario: 'test',
      scenarios: { test: { description: '502 probe', harvest: 'off', get: { [id]: '502' } } },
    });
    const reply = await send(api, 'GET', path);
    expect(reply.status).toBe(502);
    expect(reply.text).toContain('<title>502 Bad Gateway</title>');
  });
  it('selects non-200 fixture cases, preserves HTML, and respects full query paths', async () => {
    const api = createApiHandler(fixture, {
      scenario: 'test',
      scenarios: {
        test: {
          description: 'Case probe',
          harvest: 'off',
          get: {
            accounts: 'accounts.read_error',
            readiness: 'readiness.unknown_preset',
            '/api/run-controls?blueprint=nope': 'run_controls.unknown_blueprint',
          },
        },
      },
    });
    const accounts = await send(api, 'GET', '/api/accounts');
    expect(accounts.status).toBe(500);
    expect(parseAnswer(LOADED_ENDPOINTS.accounts, accounts.status, accounts.text).contract).toBe(
      'exact',
    );
    expect((await send(api, 'GET', '/api/readiness?preset=nope')).status).toBe(400);
    expect((await send(api, 'GET', '/api/run-controls?blueprint=nope')).status).toBe(400);
    expect((await send(api, 'GET', '/api/run-controls?blueprint=farm')).status).toBe(200);
  });
  it('preserves the fixture scan only when requested', async () => {
    for (const scan of ['fresh', 'fixture'] as const) {
      const scenario: Scenario = {
        description: 'Scan probe',
        harvest: 'off',
        get: { status: 'status.process_refresh_stale' },
        scan,
      };
      const reply = await send(
        createApiHandler(fixture, { scenario: 'test', scenarios: { test: scenario } }),
        'GET',
        '/api/status',
      );
      expect(reply.body).toMatchObject({
        process_refresh: scan === 'fresh' ? { state: 'fresh', age_sec: 0 } : { state: 'stale' },
      });
    }
  });
  it('merges scenario operation fields into the harvest sample (a missing revision)', async () => {
    const api = createApiHandler(fixture, { scenario: 'stopping_no_revision' });
    for (const path of ['/api/status', '/api/harvest']) {
      const body = (await send(api, 'GET', path)).body as { harvest: { operation: unknown } };
      expect(body.harvest.operation).toMatchObject({ revision: null, id: anyString });
    }
  });
  it('RUN-05 RUN-07 answers the saved-run save from the pages fixture; the writing session then reads the saved run, reset reads the old one', async () => {
    const pages = dashboardPagesCase;
    const api = createApiHandler(fixture, { scenario: 'runs_saved' });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=runs_saved'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=runs_saved'));
    const before = pages('run_controls_save.read_before_the_first_save');
    expect(
      (await send(api, 'GET', '/api/run-controls?blueprint=farm', { cookie: a })).body,
    ).toEqual(before.body);
    const body = JSON.stringify(pages('run_controls_save.saved_switch_off').request.body);
    const saved = await send(api, 'POST', '/api/run-controls', { cookie: a, body });
    expect(saved.status).toBe(200);
    expect(saved.body).toEqual(pages('run_controls_save.saved_switch_off').body);
    expect(
      (await send(api, 'GET', '/api/run-controls?blueprint=farm', { cookie: a })).body,
    ).toEqual(pages('run_controls_save.read_after_the_save_holds_the_new_values').body);
    expect(
      (await send(api, 'GET', '/api/run-controls?blueprint=farm', { cookie: b })).body,
    ).toEqual(before.body);
    await send(api, 'GET', '/__fake/reset', { cookie: a });
    expect(
      (await send(api, 'GET', '/api/run-controls?blueprint=farm', { cookie: a })).body,
    ).toEqual(before.body);
  });
  it('RUN-07 a text 500 save is sent as the HTML page the fixture records; a dropped save still switches the read', async () => {
    const pages = dashboardPagesCase;
    const api = createApiHandler(fixture, { scenario: 'runs_saved_unanswered' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=runs_saved_unanswered'));
    const failed = await send(api, 'POST', '/api/run-controls', { cookie, body: '{}' });
    expect(failed.status).toBe(500);
    expect(failed.text).toBe(
      (pages('run_controls_save.saved_then_the_answer_failed_500').body as { text: string }).text,
    );
    expect((await send(api, 'GET', '/api/run-controls?blueprint=farm', { cookie })).body).toEqual(
      pages('run_controls_save.read_after_the_unanswered_save_holds_every_sent_value').body,
    );
  });
  it('P1 answers the account selection from the pages fixture; that session then reads Alpha in the config and the accounts, another session still reads none chosen', async () => {
    const pages = dashboardPagesCase;
    const api = createApiHandler(fixture, { scenario: 'profiles' });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=profiles'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=profiles'));
    const none = dashboardCase('accounts.none_active').body;
    expect((await send(api, 'GET', '/api/accounts', { cookie: a })).body).toEqual(none);
    const recorded = pages('accounts_write.select_an_existing_account');
    const selected = await send(api, 'POST', '/api/accounts', {
      cookie: a,
      body: JSON.stringify(recorded.request.body),
    });
    expect([selected.status, selected.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/accounts', { cookie: a })).body).toEqual(
      pages('accounts_write.read_after_select').body,
    );
    expect((await send(api, 'GET', '/api/config', { cookie: a })).body).toEqual(
      pages('config_state.populated_accounts_and_loadouts').body,
    );
    expect((await send(api, 'GET', '/api/accounts', { cookie: b })).body).toEqual(none);
  });
  it('P2 P1 the failed creation and the owned device answer their recorded refusals and never switch the context', async () => {
    const pages = dashboardPagesCase;
    const failed = createApiHandler(fixture, { scenario: 'profiles_create_failed' });
    const cookie = cookiesOf(
      await send(failed, 'GET', '/__fake/use?scenario=profiles_create_failed'),
    );
    const create = pages(
      'accounts_write.create_then_the_config_save_failed_leaves_the_profile_file',
    );
    const answer = await send(failed, 'POST', '/api/accounts', {
      cookie,
      body: JSON.stringify(create.request.body),
    });
    expect([answer.status, answer.body]).toEqual([create.status, create.body]);
    expect((await send(failed, 'GET', '/api/profile-files', { cookie })).body).toEqual(
      pages('accounts_write.profile_files_after_the_failed_create_list_the_orphan').body,
    );
    const owned = createApiHandler(fixture, { scenario: 'profiles_owned' });
    const ownedCookie = cookiesOf(await send(owned, 'GET', '/__fake/use?scenario=profiles_owned'));
    const refused = await send(owned, 'POST', '/api/accounts', {
      cookie: ownedCookie,
      body: JSON.stringify({ id: 'alpha' }),
    });
    expect(refused.status).toBe(409);
    expect((await send(owned, 'GET', '/api/accounts', { cookie: ownedCookie })).body).toEqual(
      dashboardCase('accounts.none_active').body,
    );
  });
  it('serves a scenario process list verbatim (9.1 X9: {pid: 9009, operation_id: 7})', async () => {
    const reply = await send(createApiHandler(fixture, { scenario: 'x9' }), 'GET', '/api/status');
    expect(reply.body).toMatchObject({
      processes: [{ pid: 9009, operation_id: 7 }],
      process_refresh: { state: 'fresh', age_sec: 0 },
    });
  });
  it('switches only the writing session to a follow-up sample on success; reset clears it', async () => {
    const scenario: Scenario = {
      description: 'Pause settles',
      harvest: 'collecting',
      writes: { 'harvest:false': { after: 'off' } },
    };
    const api = createApiHandler(fixture, { scenario: 'test', scenarios: { test: scenario } });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=test'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=test'));
    await send(api, 'POST', '/api/harvest', { cookie: a, body: '{"enabled":false}' });
    expect((await send(api, 'GET', '/api/status', { cookie: a })).body).toMatchObject({
      harvest: { desired: { choice: 'off' } },
    });
    expect((await send(api, 'GET', '/api/harvest', { cookie: b })).body).toMatchObject({
      harvest: { desired: { choice: 'on' } },
    });
    await send(api, 'GET', '/__fake/reset', { cookie: a });
    expect((await send(api, 'GET', '/api/harvest', { cookie: a })).body).toMatchObject({
      harvest: { desired: { choice: 'on' } },
    });
  });
  it('overrides write cases per endpoint and action', async () => {
    const api = createApiHandler(fixture, { scenario: 'setup_guard_block' });
    for (const action of ['start', 'start_gems'])
      expect(
        (await send(api, 'POST', '/api/control', { body: JSON.stringify({ action }) })).body,
      ).toMatchObject({ code: 'setup_battle_unresolved' });
    expect(
      (await send(api, 'POST', '/api/control', { body: '{"action":"clear_flag"}' })).body,
    ).toEqual({ ok: true });
  });
  it.each([
    '/api/nope',
    '/api/status?scenario=nope',
    '/__fake/use?scenario=nope',
    '/__fake/nope',
    '/api/battles/nope',
  ])('answers unknown paths/scenarios 404: %s', async (path) => {
    expect((await send(createApiHandler(fixture), 'GET', path)).status).toBe(404);
  });
  it.each(['POST', 'HEAD', 'PUT', 'DELETE'])('keeps controls GET only (%s)', async (method) => {
    const reply = await send(createApiHandler(fixture), method, '/__fake/reset');
    expect(reply.status).toBe(405);
    expect(reply.allow).toBe('GET');
  });
  it.each(['PUT', 'PATCH', 'DELETE'])('refuses API method %s', async (method) => {
    expect((await send(createApiHandler(fixture), method, '/api/status')).status).toBe(405);
  });
  it('refuses real targets from options or environment before opening a listener', () => {
    for (const options of [
      { target: 'http://localhost:9999' },
      { realBackendTarget: 'http://localhost:9999' },
      { env: { TOWER_PILOT_DEV_BACKEND: 'http://localhost:9999' } },
    ]) {
      expect(() => createApiHandler(fixture, options)).toThrow(/refuses real-backend targets/);
      expect(() => startServer({ port: 0, ...options })).toThrow(/refuses real-backend targets/);
    }
    vi.stubEnv('TOWER_PILOT_DEV_BACKEND', 'http://localhost:9999');
    expect(() => createApiHandler(fixture)).toThrow(/never forwards/);
    expect(() => startServer({ port: 0 })).toThrow(/never forwards/);
  });
  it('refuses to start without a build or a known default scenario', () => {
    expect(() => startServer({ port: 0, buildDir: 'does-not-exist-f2-step0' })).toThrow(
      /app is not built/,
    );
    expect(() => createApiHandler(fixture, { scenario: 'nope' })).toThrow(/No fixture scenario/);
  });
  it('answers a client FIN with a reset before http sends its own FIN, so no TIME_WAIT is left', () => {
    const socket = new Socket();
    const order: string[] = [];
    socket.on('end', () => order.push('http end'));
    vi.spyOn(socket, 'resetAndDestroy').mockImplementation(() => {
      order.push('reset');
      return socket;
    });
    resetAfterClientClose(socket);
    socket.emit('end');
    expect(order).toEqual(['reset', 'http end']);
  });
  it('the standalone server resets every connection the client has closed', () => {
    const buildDir = mkdtempSync(join(tmpdir(), 'tp-fake-build-'));
    try {
      writeFileSync(join(buildDir, 'index.html'), '<!doctype html>');
      // Server#listen is refused in unit tests (R2h); only the registered listeners matter here.
      vi.spyOn(Server.prototype, 'listen').mockImplementation(function (this: Server) {
        return this;
      });
      const server = startServer({ port: 0, buildDir });
      expect(server.listeners('connection')).toContain(resetAfterClientClose);
    } finally {
      rmSync(buildDir, { recursive: true, force: true });
    }
  });
});
describe('Collection fixture routes', () => {
  it.each([
    ['/api/settings', 'settings.populated_account_every_module'],
    ['/api/vocab', 'profile_reads.vocab_ok'],
  ])('C1 C6 defaults %s to its recorded body', async (path, reference) => {
    const reply = await send(createApiHandler(fixture), 'GET', path);
    const recorded = dashboardPagesCase(reference);
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });

  it('C1 answers the settings save from the pages fixture; that session then reads the saved settings, another session the old ones', async () => {
    const api = createApiHandler(fixture, { scenario: 'collection' });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=collection'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=collection'));
    const before = dashboardPagesCase('settings.populated_account_every_module').body;
    expect((await send(api, 'GET', '/api/settings', { cookie: a })).body).toEqual(before);
    const recorded = dashboardPagesCase('settings.saved_rewards');
    const saved = await send(api, 'POST', '/api/settings', {
      cookie: a,
      body: JSON.stringify(recorded.request.body),
    });
    expect([saved.status, saved.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/settings', { cookie: a })).body).toEqual(
      dashboardPagesCase('settings.read_after_every_module_was_saved').body,
    );
    expect((await send(api, 'GET', '/api/settings', { cookie: b })).body).toEqual(before);
    await send(api, 'GET', '/__fake/reset', { cookie: a });
    expect((await send(api, 'GET', '/api/settings', { cookie: a })).body).toEqual(before);
  });

  it('C5 the in-use delete answers its recorded 400 with problems and changes no read', async () => {
    const api = createApiHandler(fixture, { scenario: 'collection_delete_in_use' });
    const cookie = cookiesOf(
      await send(api, 'GET', '/__fake/use?scenario=collection_delete_in_use'),
    );
    const recorded = dashboardPagesCase('collection_policies.policy_deleted_while_a_run_uses_it');
    const refused = await send(api, 'POST', '/api/profile-patch', {
      cookie,
      body: JSON.stringify(recorded.request.body),
    });
    expect(refused.status).toBe(400);
    expect(refused.body).toEqual(recorded.body);
    expect(refused.body).toMatchObject({ problems: anyArray });
    expect((await send(api, 'GET', '/api/profile-src/mine', { cookie })).body).toEqual(
      dashboardPagesCase('collection_policies.read_before_the_first_save').body,
    );
    expect((await send(api, 'GET', '/api/settings', { cookie })).body).toEqual(
      dashboardPagesCase('settings.populated_account_every_module').body,
    );
  });

  it('C2 a dropped settings save records the POST, sends no answer, and the next read holds the value', async () => {
    const api = createApiHandler(fixture, { scenario: 'collection_save_lost' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=collection_save_lost'));
    const body = JSON.stringify(
      dashboardPagesCase('settings.saved_then_the_answer_failed_500').request.body,
    );
    const sent = request(api, 'POST', '/api/settings', { cookie, body });
    await new Promise<void>((resolve) => sent.req.on('end', resolve));
    expect(sent.req.socket.destroyed).toBe(true);
    expect(sent.res.writableEnded).toBe(false);
    expect((await send(api, 'GET', '/api/settings', { cookie })).body).toEqual(
      dashboardPagesCase('settings.read_after_the_unanswered_save_holds_the_value').body,
    );
    expect((await send(api, 'GET', '/__fake/posts', { cookie })).body).toEqual({
      posts: [{ method: 'POST', path: '/api/settings', body, time: anyNumber }],
      diagnostics: [],
    });
  });

  it('C1 the starter settings save answers the recorded refusal and keeps the read-only modules', async () => {
    const api = createApiHandler(fixture, { scenario: 'collection_starter' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=collection_starter'));
    const before = dashboardPagesCase('settings.starter_profile_active_profile_modules_read_only');
    expect((await send(api, 'GET', '/api/settings', { cookie })).body).toEqual(before.body);
    const recorded = dashboardPagesCase(
      'settings.starter_profile_active_a_profile_module_is_refused',
    );
    const refused = await send(api, 'POST', '/api/settings', {
      cookie,
      body: JSON.stringify(recorded.request.body),
    });
    expect([refused.status, refused.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/settings', { cookie })).body).toEqual(before.body);
  });

  it('C5 a stale settings save answers the recorded 409 and reads the other clients save', async () => {
    const api = createApiHandler(fixture, { scenario: 'collection_stale' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=collection_stale'));
    const recorded = dashboardPagesCase('settings.stale_revision_another_client_saved_first');
    const refused = await send(api, 'POST', '/api/settings', {
      cookie,
      body: JSON.stringify(recorded.request.body),
    });
    expect(refused.status).toBe(409);
    expect(refused.body).toEqual(recorded.body);
    expect((await send(api, 'GET', '/api/settings', { cookie })).body).toEqual(
      dashboardPagesCase('settings.read_after_the_stale_refusal_holds_the_other_clients_save').body,
    );
  });

  it.each([
    ['C6', 'collection', 'collection_policies.chores_reordered_and_one_switched_off'],
    ['C5', 'collection_policy_added', 'collection_policies.policy_added_that_no_run_uses'],
  ])(
    '%s %s answers its recorded profile patch and updates only that sessions source',
    async (_id, scenario, reference) => {
      const api = createApiHandler(fixture, { scenario });
      const a = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const b = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const recorded = dashboardPagesCase(reference);
      const saved = await send(api, 'POST', '/api/profile-patch', {
        cookie: a,
        body: JSON.stringify(recorded.request.body),
      });
      expect([saved.status, saved.body]).toEqual([recorded.status, recorded.body]);
      expect((await send(api, 'GET', '/api/profile-src/mine', { cookie: a })).body).toEqual(
        dashboardPagesCase('collection_policies.read_after_the_saves_holds_every_change').body,
      );
      expect((await send(api, 'GET', '/api/profile-src/mine', { cookie: b })).body).toEqual(
        dashboardPagesCase('collection_policies.read_before_the_first_save').body,
      );
    },
  );

  it.each([
    ['ok', 'settings.saved_rewards'],
    ['refused', 'settings.stale_revision_another_client_saved_first'],
    ['unknown', 'settings.saved_then_the_answer_failed_500'],
  ] as const)(
    'C1 C5 settingsSave mode %s uses its recorded default case',
    async (mode, reference) => {
      const api = createApiHandler(fixture, {
        scenario: 'test',
        scenarios: {
          test: { description: 'Settings mode probe', harvest: 'off', writeMode: mode },
        },
      });
      const recorded = dashboardPagesCase(reference);
      const reply = await send(api, 'POST', '/api/settings', {
        body: JSON.stringify(recorded.request.body),
      });
      expect(reply.status).toBe(recorded.status);
      if (mode === 'unknown') {
        expect(reply.type).toBe('text/html; charset=utf-8');
        expect(reply.text).toBe((recorded.body as { text: string }).text);
      } else expect(reply.body).toEqual(recorded.body);
    },
  );
});

describe('Emulators fixture routes', () => {
  it('M2 answers the adopt from the pages fixture; that session then reads the config naming the serial, another session the old one', async () => {
    const api = createApiHandler(fixture, { scenario: 'emulators' });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=emulators'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=emulators'));
    const before = dashboardPagesCase('config_state.read_before_the_save');
    const stateBefore = dashboardCase('wizard_state.after_scan');
    expect((await send(api, 'GET', '/api/config', { cookie: a })).body).toEqual(before.body);
    const recorded = dashboardPagesCase('emulator_actions.adopt_a_serial_saved_and_connected');
    const saved = await send(api, 'POST', '/api/wizard/adopt', {
      cookie: a,
      body: JSON.stringify(recorded.request.body),
    });
    expect([saved.status, saved.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/config', { cookie: a })).body).toEqual(
      dashboardPagesCase('emulator_actions.config_after_the_adopt_names_the_serial').body,
    );
    expect((await send(api, 'GET', '/api/wizard/state', { cookie: a })).body).toEqual(
      dashboardPagesCase('emulator_actions.wizard_state_after_the_adopt_holds_the_receipt').body,
    );
    expect((await send(api, 'GET', '/api/config', { cookie: b })).body).toEqual(before.body);
    expect((await send(api, 'GET', '/api/wizard/state', { cookie: b })).body).toEqual(
      stateBefore.body,
    );
    await send(api, 'GET', '/__fake/reset', { cookie: a });
    expect((await send(api, 'GET', '/api/config', { cookie: a })).body).toEqual(before.body);
    expect((await send(api, 'GET', '/api/wizard/state', { cookie: a })).body).toEqual(
      stateBefore.body,
    );
  });

  it('M3 a dropped launch records the POST and sends no answer', async () => {
    const api = createApiHandler(fixture, { scenario: 'emulators_launch_lost' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=emulators_launch_lost'));
    const body = JSON.stringify(
      dashboardPagesCase('emulator_actions.launch_a_vm_no_instance_uses_skips_the_boot_pipeline')
        .request.body,
    );
    const sent = request(api, 'POST', '/api/wizard/launch', { cookie, body });
    await new Promise<void>((resolve) => sent.req.on('end', resolve));
    expect(sent.req.socket.destroyed).toBe(true);
    expect(sent.res.writableEnded).toBe(false);
    expect((await send(api, 'GET', '/__fake/posts', { cookie })).body).toEqual({
      posts: [{ method: 'POST', path: '/api/wizard/launch', body, time: anyNumber }],
      diagnostics: [],
    });
  });

  it('M5 the install answers 202 and the next tools read is installed', async () => {
    const api = createApiHandler(fixture, { scenario: 'emulators' });
    const a = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=emulators'));
    const b = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=emulators'));
    const idle = dashboardPagesCase('emulator_tools.tools_idle_before_any_install');
    expect((await send(api, 'GET', '/api/wizard/tools', { cookie: a })).body).toEqual(idle.body);
    const recorded = dashboardPagesCase('emulator_tools.install_accepted_202_the_download_starts');
    const saved = await send(api, 'POST', '/api/wizard/tools/install', {
      cookie: a,
      body: JSON.stringify(recorded.request.body),
    });
    expect(saved.status).toBe(202);
    expect([saved.status, saved.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/wizard/tools', { cookie: a })).body).toEqual(
      dashboardPagesCase('emulator_tools.tools_installed_after_the_install').body,
    );
    expect((await send(api, 'GET', '/api/wizard/tools', { cookie: b })).body).toEqual(idle.body);
    await send(api, 'GET', '/__fake/reset', { cookie: a });
    expect((await send(api, 'GET', '/api/wizard/tools', { cookie: a })).body).toEqual(idle.body);
  });

  it.each(['', '?serial=emulator-5554', '?serial=127.0.0.1%3A7777&display=1'])(
    'M6 the resolution route answers its recorded case for any serial query %s',
    async (query) => {
      const reply = await send(createApiHandler(fixture), 'GET', `/api/wizard/resolution${query}`);
      const recorded = dashboardPagesCase('emulator_inventory.resolution_portrait_reference');
      expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
    },
  );

  it('M8 the owned scenario refuses reconnect with the recorded owner body', async () => {
    const api = createApiHandler(fixture, { scenario: 'emulators_owned' });
    const recorded = dashboardCase('reconnect.refused_while_a_runner_is_listed');
    const reply = await send(api, 'POST', '/api/wizard/reconnect', {
      body: JSON.stringify(recorded.request.body),
    });
    expect(reply.status).toBe(409);
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });

  it.each([
    ['M2', '/api/wizard/adopt', 'ok', 'emulator_actions.adopt_a_serial_saved_and_connected'],
    [
      'M2',
      '/api/wizard/adopt',
      'refused',
      'emulator_actions.adopt_the_instance_changed_since_the_scan',
    ],
    [
      'M2',
      '/api/wizard/adopt',
      'unknown',
      'emulator_actions.adopt_the_config_could_not_be_written_500',
    ],
    [
      'M3',
      '/api/wizard/launch',
      'ok',
      'emulator_actions.launch_a_vm_no_instance_uses_skips_the_boot_pipeline',
    ],
    [
      'M3',
      '/api/wizard/launch',
      'refused',
      'emulator_actions.launch_refused_while_a_runner_owns_the_device',
    ],
    [
      'M3',
      '/api/wizard/launch',
      'unknown',
      'emulator_actions.launch_the_manager_could_not_be_started_500',
    ],
    [
      'M4',
      '/api/wizard/bluestacks/prepare',
      'ok',
      'emulator_actions.prepare_changes_the_adb_switch_and_the_framebuffer',
    ],
    [
      'M4',
      '/api/wizard/bluestacks/prepare',
      'refused',
      'emulator_actions.prepare_refused_while_bluestacks_is_running',
    ],
    [
      'M5',
      '/api/wizard/tools/install',
      'ok',
      'emulator_tools.install_accepted_202_the_download_starts',
    ],
    [
      'M5',
      '/api/wizard/tools/install',
      'refused',
      'emulator_tools.install_refused_while_a_runner_owns_the_device',
    ],
  ] as const)(
    '%s %s mode %s answers its recorded default case',
    async (_id, path, mode, reference) => {
      const api = createApiHandler(fixture, {
        scenario: 'test',
        scenarios: {
          test: { description: 'Emulators mode probe', harvest: 'off', writeMode: mode },
        },
      });
      const recorded = dashboardPagesCase(reference);
      const reply = await send(api, 'POST', path, { body: JSON.stringify(recorded.request.body) });
      expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
    },
  );

  it.each([
    [
      'M2',
      'emulators_not_connected',
      '/api/wizard/adopt',
      'emulator_actions.adopt_saved_but_not_connected',
    ],
    [
      'M2',
      'emulators_two_running',
      '/api/wizard/adopt',
      'emulator_actions.adopt_the_instance_changed_since_the_scan',
    ],
    [
      'M2',
      'emulators_owned',
      '/api/wizard/adopt',
      'emulator_actions.adopt_refused_while_a_runner_owns_the_device',
    ],
    [
      'M3',
      'emulators_launch_failed',
      '/api/wizard/launch',
      'emulator_actions.launch_the_manager_reported_a_failure',
    ],
    [
      'M3',
      'emulators_owned',
      '/api/wizard/launch',
      'emulator_actions.launch_refused_while_a_runner_owns_the_device',
    ],
  ])(
    '%s %s answers its recorded write and reads the resulting config',
    async (_id, scenario, path, reference) => {
      const api = createApiHandler(fixture, { scenario });
      const cookie = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const recorded = dashboardPagesCase(reference);
      const reply = await send(api, 'POST', path, {
        cookie,
        body: JSON.stringify(recorded.request.body),
      });
      expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
      expect((await send(api, 'GET', '/api/config', { cookie })).body).toEqual(
        dashboardPagesCase(
          scenario === 'emulators_not_connected'
            ? 'emulator_actions.config_after_the_adopt_names_the_serial'
            : 'config_state.read_before_the_save',
        ).body,
      );
    },
  );

  it('M8 reconnect offline answers the recorded retries outcome', async () => {
    const api = createApiHandler(fixture, { scenario: 'emulators_reconnect_offline' });
    const recorded = dashboardCase('reconnect.answered_offline_after_the_retries');
    const reply = await send(api, 'POST', '/api/wizard/reconnect', {
      body: JSON.stringify(recorded.request.body),
    });
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });

  it.each([
    [
      'M1 M2',
      'emulators_two_running',
      '/api/wizard/emulators',
      'emulator_inventory.two_running_vms_the_configured_one_is_down_no_automatic_choice',
    ],
    [
      'M1',
      'emulators_placeholder',
      '/api/wizard/emulators',
      'emulator_inventory.no_serial_configured_yet_placeholder',
    ],
    [
      'M5',
      'emulators_tools_downloading',
      '/api/wizard/tools',
      'emulator_tools.tools_downloading_with_progress',
    ],
    [
      'M5',
      'emulators_tools_error',
      '/api/wizard/tools',
      'emulator_tools.tools_error_state_after_a_failed_install',
    ],
    [
      'M6',
      'emulators_resolution_landscape',
      '/api/wizard/resolution?serial=emulator-5554',
      'emulator_inventory.resolution_landscape_is_not_portrait',
    ],
  ])('%s %s reads its recorded pages case', async (_id, scenario, path, reference) => {
    const recorded = dashboardPagesCase(reference);
    const reply = await send(createApiHandler(fixture, { scenario }), 'GET', path);
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });

  it.each([
    ['emulators', 'wizard_emulators.instances_with_names_and_ports'],
    ['emulators_scan_error', 'wizard_emulators.scan_error'],
    ['emulators_none', 'wizard_emulators.none'],
  ])('M1 %s reads its recorded v1 discovery case', async (scenario, reference) => {
    const recorded = dashboardCase(reference);
    const reply = await send(
      createApiHandler(fixture, { scenario }),
      'GET',
      '/api/wizard/emulators',
    );
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });
});

describe('Recognition fixture routes', () => {
  it.each([
    ['/api/calibrate/status', 'recognition_reads.calibrate_status_of_the_first_account'],
    [
      '/api/calibrate/status?operation_id=chosen',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    ['/api/readiness?preset=bp_farm', 'readiness.ready_with_context'],
    ['/api/wizard/templates', 'recognition_reads.templates_of_the_first_account'],
    ['/api/wizard/required', 'recognition_reads.required_images_some_captured'],
    ['/api/catalogue/modules', 'recognition_reads.module_catalogue_nothing_learned'],
    ['/api/wizard/scan-plan', 'recognition_reads.scan_plan_of_the_first_account'],
    ['/api/wizard/scan-plan?preset=bp_farm', 'recognition_reads.scan_plan_for_a_run'],
    ['/api/wizard/scan-plan?preset=', 'recognition_reads.scan_plan_for_a_run'],
    ['/api/screen-analysis/status', 'screen_analysis.status_idle_nothing_was_ever_analysed'],
    [
      '/api/screen-analysis/status?operation_id=chosen',
      'screen_analysis.status_idle_nothing_was_ever_analysed',
    ],
  ])('%s defaults to the recorded Recognition GET %s', async (path, reference) => {
    const recorded = reference.startsWith('readiness.')
      ? dashboardCase(reference)
      : dashboardPagesCase(reference);
    const reply = await send(createApiHandler(fixture), 'GET', path);
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });

  it.each([
    [
      'recognition',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_running',
      '/api/calibrate/status',
      'calibrate_writes.read_the_running_calibration_by_operation',
    ],
    [
      'recognition_awaiting_apply',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    ],
    [
      'recognition_apply_refused',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    ],
    [
      'recognition_apply_lost',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
    ],
    [
      'recognition_setup_finished',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_setup_battle_finished_and_home_verified_with_target_evidence',
    ],
    [
      'recognition_failed',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_failed_keeps_the_error_and_the_evidence_that_was_sealed',
    ],
    [
      'recognition_aborted',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_aborted_by_a_person_keeps_the_partial_evidence',
    ],
    [
      'recognition_restore_pending',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_restoration_required_with_the_restore_file_present',
    ],
    [
      'recognition_artifact_error',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_report_changed_after_it_was_sealed_is_unavailable',
    ],
    [
      'recognition_taps_off',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_taps_not_allowed',
    ],
    [
      'recognition_owned',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_live_battle',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_images_missing',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_start_lost',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_observe_refused',
      '/api/calibrate/status',
      'calibrate_writes.read_the_running_calibration_by_operation',
    ],
    [
      'recognition_untracked',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_shared_files_of_an_untracked_calibration',
    ],
    [
      'recognition_analysis',
      '/api/screen-analysis/status',
      'screen_analysis.status_done_with_matches_and_an_ambiguous_one',
    ],
    [
      'recognition_analysis_failed',
      '/api/screen-analysis/status',
      'screen_analysis.status_failed_operation_is_an_error_with_its_message',
    ],
    [
      'recognition_crop_refused',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_crop_exists',
      '/api/calibrate/status',
      'recognition_reads.calibrate_status_of_the_first_account',
    ],
    [
      'recognition_frame_failed',
      '/api/frame.png?ts=t1',
      'recognition_images.frame_png_capture_failed_502',
    ],
  ])('%s reads its recorded state and shared baseline', async (scenario, path, reference) => {
    const api = createApiHandler(fixture, { scenario });
    const cookie = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
    const recorded = dashboardPagesCase(reference);
    for (const query of path.includes('?') ? [''] : ['', '?operation_id=selected']) {
      const reply = await send(api, 'GET', path + query, { cookie });
      expect(reply.status).toBe(recorded.status);
      if (reference === 'recognition_images.frame_png_capture_failed_502') {
        expect(reply.type).toBe('text/html; charset=utf-8');
        expect(reply.text).toBe((recorded.body as { text: string }).text);
      } else expect(reply.body).toEqual(recorded.body);
    }
    const config =
      scenario === 'recognition_taps_off'
        ? dashboardCase('config.taps_false')
        : dashboardPagesCase('config_state.read_before_the_save');
    expect((await send(api, 'GET', '/api/config', { cookie })).body).toEqual(config.body);
    expect((await send(api, 'GET', '/api/runs', { cookie })).body).toEqual(
      dashboardCase('runs.ready').body,
    );
    expect((await send(api, 'GET', '/api/battles/active', { cookie })).body).toEqual(
      dashboardCase(
        scenario === 'recognition_live_battle'
          ? 'battles_active.runtime_ad_orbit_shopping'
          : 'battles_active.null_no_driver',
      ).body,
    );
    const status = dashboardCase(
      scenario === 'recognition_owned'
        ? 'status.processes_two_rows_same_runner'
        : 'status.processes_empty',
    ).body as { processes: unknown };
    expect((await send(api, 'GET', '/api/status', { cookie })).body).toMatchObject({
      processes: status.processes,
    });
  });

  it('calibrateOperation overrides only operation queries and a follow-up read overrides both', async () => {
    const api = createApiHandler(fixture, {
      scenario: 'test',
      scenarios: {
        test: {
          description: 'Recognition query selection',
          harvest: 'off',
          get: {
            calibrate: 'recognition_reads.calibrate_status_of_the_first_account',
            calibrateOperation:
              'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
          },
          writes: {
            calibrateApply: {
              case: 'calibrate_writes.apply_awaiting_apply_is_applied',
              reads: { calibrate: 'calibrate_writes.read_the_applied_calibration_by_operation' },
            },
          },
        },
      },
    });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=test'));
    expect((await send(api, 'GET', '/api/calibrate/status', { cookie })).body).toEqual(
      dashboardPagesCase('recognition_reads.calibrate_status_of_the_first_account').body,
    );
    expect(
      (await send(api, 'GET', '/api/calibrate/status?operation_id=selected', { cookie })).body,
    ).toEqual(
      dashboardPagesCase(
        'recognition_reads.calibrate_status_awaiting_apply_with_the_sealed_evidence',
      ).body,
    );
    await send(api, 'POST', '/api/calibrate/apply', { cookie, body: '{}' });
    for (const query of ['', '?operation_id=selected']) {
      expect((await send(api, 'GET', `/api/calibrate/status${query}`, { cookie })).body).toEqual(
        dashboardPagesCase('calibrate_writes.read_the_applied_calibration_by_operation').body,
      );
    }
  });

  it.each([
    [
      'recognition',
      'calibrate_writes.start_bootstrap_with_the_action_capture_flows',
      '/api/calibrate/status',
      'calibrate_writes.read_the_running_calibration_by_operation',
    ],
    [
      'recognition',
      'calibrate_writes.observe_watch_30_seconds',
      '/api/calibrate/status',
      'calibrate_writes.read_the_running_observation_by_operation',
    ],
    [
      'recognition',
      'calibrate_writes.stop_running_calibration_asks_the_worker_to_stop',
      '/api/calibrate/status',
      'calibrate_writes.read_the_calibration_that_was_asked_to_stop',
    ],
    [
      'recognition',
      'calibrate_writes.apply_awaiting_apply_is_applied',
      '/api/calibrate/status',
      'calibrate_writes.read_the_applied_calibration_by_operation',
    ],
    [
      'recognition_running',
      'calibrate_writes.stop_running_calibration_asks_the_worker_to_stop',
      '/api/calibrate/status',
      'calibrate_writes.read_the_calibration_that_was_asked_to_stop',
    ],
    [
      'recognition_awaiting_apply',
      'calibrate_writes.apply_awaiting_apply_is_applied',
      '/api/calibrate/status',
      'calibrate_writes.read_the_applied_calibration_by_operation',
    ],
    [
      'recognition',
      'screen_analysis.start_accepted',
      '/api/screen-analysis/status',
      'screen_analysis.status_done_with_matches_and_an_ambiguous_one',
    ],
    [
      'recognition_analysis',
      'screen_analysis.start_accepted',
      '/api/screen-analysis/status',
      'screen_analysis.status_done_with_matches_and_an_ambiguous_one',
    ],
    [
      'recognition_analysis_failed',
      'screen_analysis.start_when_the_worker_cannot_be_started_is_a_text_500',
      '/api/screen-analysis/status',
      'screen_analysis.read_the_operation_of_the_failed_start',
    ],
    [
      'recognition',
      'recognition_images.save_a_crop_of_the_cached_frame',
      '/api/wizard/templates',
      'recognition_images.read_the_template_list_after_the_save',
    ],
  ])(
    '%s answers %s, records one POST and isolates its follow-up read',
    async (scenario, reference, readPath, followup) => {
      const api = createApiHandler(fixture, { scenario });
      const a = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const b = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const before = (await send(api, 'GET', readPath, { cookie: b })).body;
      const recorded = dashboardPagesCase(reference);
      const body = JSON.stringify(recorded.request.body);
      const reply = await send(api, 'POST', recorded.request.path, { cookie: a, body });
      expect(reply.status).toBe(recorded.status);
      if (reference === 'screen_analysis.start_when_the_worker_cannot_be_started_is_a_text_500') {
        expect(reply.text).toBe((recorded.body as { text: string }).text);
        expect(reply.type).toBe('text/html; charset=utf-8');
      } else expect(reply.body).toEqual(recorded.body);
      for (const query of ['', '?operation_id=selected']) {
        expect((await send(api, 'GET', readPath + query, { cookie: a })).body).toEqual(
          dashboardPagesCase(followup).body,
        );
      }
      expect((await send(api, 'GET', readPath, { cookie: b })).body).toEqual(before);
      expect((await send(api, 'GET', '/__fake/posts', { cookie: a })).body).toEqual({
        posts: [{ method: 'POST', path: recorded.request.path, body, time: anyNumber }],
        diagnostics: [],
      });
      await send(api, 'GET', '/__fake/reset', { cookie: a });
      expect((await send(api, 'GET', readPath, { cookie: a })).body).toEqual(before);
      expect((await send(api, 'GET', '/__fake/posts', { cookie: a })).body).toEqual({
        posts: [],
        diagnostics: [],
      });
    },
  );

  it.each([
    ['recognition_apply_refused', 'calibrate_writes.apply_stale_revision_409'],
    ['recognition_taps_off', 'calibrate_writes.start_taps_off_409'],
    ['recognition_owned', 'calibrate_writes.start_while_a_run_owns_the_device_409'],
    ['recognition_owned', 'calibrate_writes.observe_while_a_run_owns_the_device_409'],
    ['recognition_images_missing', 'calibrate_writes.start_without_the_navigation_images_409'],
    ['recognition_observe_refused', 'calibrate_writes.observe_while_a_calibration_is_running_409'],
    ['recognition_untracked', 'calibrate_writes.apply_saved_observations_of_no_operation'],
    ['recognition_crop_refused', 'recognition_images.save_from_a_frame_that_left_the_cache_409'],
    [
      'recognition_crop_exists',
      'recognition_images.save_over_an_existing_template_without_the_overwrite_flag_409',
    ],
  ])('%s answers and records %s without changing reads', async (scenario, reference) => {
    const api = createApiHandler(fixture, { scenario });
    const cookie = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
    const before = (await send(api, 'GET', '/api/calibrate/status', { cookie })).body;
    const templatesBefore = (await send(api, 'GET', '/api/wizard/templates', { cookie })).body;
    const recorded = dashboardPagesCase(reference);
    const body = JSON.stringify(recorded.request.body);
    const reply = await send(api, 'POST', recorded.request.path, { cookie, body });
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
    expect((await send(api, 'GET', '/api/calibrate/status', { cookie })).body).toEqual(before);
    expect((await send(api, 'GET', '/api/wizard/templates', { cookie })).body).toEqual(
      templatesBefore,
    );
    expect((await send(api, 'GET', '/__fake/posts', { cookie })).body).toEqual({
      posts: [{ method: 'POST', path: recorded.request.path, body, time: anyNumber }],
      diagnostics: [],
    });
  });

  it.each([
    [
      'recognition_start_lost',
      'calibrate_writes.start_bootstrap_with_the_action_capture_flows',
      'calibrate_writes.read_the_latest_calibration_of_the_context_after_a_lost_answer',
    ],
    [
      'recognition_apply_lost',
      'calibrate_writes.apply_awaiting_apply_is_applied',
      'calibrate_writes.read_the_applied_calibration_by_operation',
    ],
  ])(
    '%s drops its answer, records one POST, and settles through %s',
    async (scenario, reference, followup) => {
      const api = createApiHandler(fixture, { scenario });
      const a = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const b = cookiesOf(await send(api, 'GET', `/__fake/use?scenario=${scenario}`));
      const before = (await send(api, 'GET', '/api/calibrate/status', { cookie: b })).body;
      const recorded = dashboardPagesCase(reference);
      const body = JSON.stringify(recorded.request.body);
      const sent = request(api, 'POST', recorded.request.path, { cookie: a, body });
      await new Promise<void>((resolve) => sent.req.on('end', resolve));
      expect(sent.req.socket.destroyed).toBe(true);
      expect(sent.res.writableEnded).toBe(false);
      for (const query of ['', '?operation_id=selected']) {
        expect(
          (await send(api, 'GET', `/api/calibrate/status${query}`, { cookie: a })).body,
        ).toEqual(dashboardPagesCase(followup).body);
      }
      expect((await send(api, 'GET', '/api/calibrate/status', { cookie: b })).body).toEqual(before);
      expect((await send(api, 'GET', '/__fake/posts', { cookie: a })).body).toEqual({
        posts: [{ method: 'POST', path: recorded.request.path, body, time: anyNumber }],
        diagnostics: [],
      });
      await send(api, 'GET', '/__fake/reset', { cookie: a });
      expect((await send(api, 'GET', '/api/calibrate/status', { cookie: a })).body).toEqual(before);
    },
  );

  it.each([
    [
      '/api/screen-analysis/frame.png',
      'screen_analysis.frame_of_a_finished_analysis_by_its_operation',
    ],
    [
      '/api/screen-analysis/frame.png?operation_id=chosen&revision=stale',
      'screen_analysis.frame_of_a_finished_analysis_by_its_operation',
    ],
    ['/api/frame.png?ts=t1', 'recognition_images.frame_png_remembered_under_the_token'],
    ['/api/frame.png', 'recognition_images.frame_png_remembered_under_the_token'],
    ['/api/template/buttons/retry.png', 'recognition_images.read_a_captured_template'],
    [
      '/api/artwork/buttons/retry.png',
      'recognition_images.artwork_a_text_badge_for_a_control_the_game_draws_as_text',
    ],
  ])('%s serves a valid synthetic PNG of the recorded dimensions', async (path, reference) => {
    const recorded = dashboardPagesCase(reference);
    const binary = (
      recorded.body as {
        binary: { width: number; height: number; content_type: string; cache_control?: string };
      }
    ).binary;
    const sent = request(createApiHandler(fixture), 'GET', path);
    const reply = await sent.reply;
    expect(reply.status).toBe(recorded.status);
    expect(reply.type).toBe(binary.content_type);
    expect(sent.res.getHeader('cache-control')).toBe(binary.cache_control ?? 'no-store');
    expect(reply.bytes.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    expect(reply.bytes.readUInt32BE(16)).toBe(binary.width);
    expect(reply.bytes.readUInt32BE(20)).toBe(binary.height);
    const data: Buffer[] = [];
    const types: string[] = [];
    for (let offset = 8; offset < reply.bytes.length;) {
      const length = reply.bytes.readUInt32BE(offset);
      const type = reply.bytes.toString('ascii', offset + 4, offset + 8);
      types.push(type);
      const payload = reply.bytes.subarray(offset + 8, offset + 8 + length);
      expect(reply.bytes.readUInt32BE(offset + 8 + length)).toBe(
        crc32(Buffer.concat([Buffer.from(type), payload])),
      );
      if (type === 'IDAT') data.push(payload);
      offset += length + 12;
    }
    expect(types).toEqual(['IHDR', 'IDAT', 'IEND']);
    expect(inflateSync(Buffer.concat(data)).length).toBe((binary.width + 1) * binary.height);
  });

  it('a crop updates its recorded template, list and scan plan, and reset restores them', async () => {
    const api = createApiHandler(fixture, { scenario: 'recognition' });
    const cookie = cookiesOf(await send(api, 'GET', '/__fake/use?scenario=recognition'));
    const crop = dashboardPagesCase('recognition_images.save_a_crop_of_the_cached_frame');
    await send(api, 'POST', crop.request.path, { cookie, body: JSON.stringify(crop.request.body) });
    for (const query of ['', '?preset=bp_farm']) {
      expect((await send(api, 'GET', `/api/wizard/scan-plan${query}`, { cookie })).body).toEqual(
        dashboardPagesCase(
          'recognition_images.read_the_scan_plan_after_the_save_shows_the_cut_screen_and_its_verification',
        ).body,
      );
    }
    const saved = dashboardPagesCase('recognition_images.read_the_saved_template').body as {
      binary: { width: number; height: number };
    };
    const image = await send(api, 'GET', crop.request.path, { cookie });
    expect(image.type).toBe('image/png');
    expect([image.bytes.readUInt32BE(16), image.bytes.readUInt32BE(20)]).toEqual([
      saved.binary.width,
      saved.binary.height,
    ]);
    await send(api, 'GET', '/__fake/reset', { cookie });
    expect((await send(api, 'GET', '/api/wizard/scan-plan', { cookie })).body).toEqual(
      dashboardPagesCase('recognition_reads.scan_plan_of_the_first_account').body,
    );
    const original = dashboardPagesCase('recognition_images.read_a_captured_template').body as {
      binary: { width: number; height: number };
    };
    const reset = await send(api, 'GET', crop.request.path, { cookie });
    expect([reset.bytes.readUInt32BE(16), reset.bytes.readUInt32BE(20)]).toEqual([
      original.binary.width,
      original.binary.height,
    ]);
  });

  it.each([
    [
      'screen_analysis.frame_of_an_analysis_with_no_saved_screenshot_409',
      '/api/screen-analysis/frame.png?operation_id=chosen',
    ],
    ['screen_analysis.frame_of_an_unfinished_analysis_409', '/api/screen-analysis/frame.png'],
    ['screen_analysis.frame_with_no_operation_and_no_file_404', '/api/screen-analysis/frame.png'],
    [
      'recognition_images.frame_png_display_that_is_not_a_number_400',
      '/api/frame.png?display=invalid',
    ],
  ])('image route sends the recorded JSON refusal %s', async (reference, path) => {
    const recorded = dashboardPagesCase(reference);
    const api = createApiHandler(fixture, {
      scenario: 'test',
      scenarios: {
        test: { description: 'Recorded image refusal', harvest: 'off', get: { [path]: reference } },
      },
    });
    const reply = await send(api, 'GET', path);
    expect(reply.type).toBe('application/json');
    expect([reply.status, reply.body]).toEqual([recorded.status, recorded.body]);
  });
});

describe('test workers', () => {
  it('refuse to open TCP sockets (R2h)', () => {
    expect(() => connect(9, '127.0.0.1')).toThrow(/R2h/);
    expect(() => createServer().listen(0, '127.0.0.1')).toThrow(/R2h/);
  });
});

describe('T3 T4 T5 T6 Runtime recorded fake-backend reads', () => {
  it.each(['missing', 'wrong'])(
    'T1 T2 F6T-R1-03 Runtime refuses a %s revision with recorded refusal and keeps predecessor settings',
    async (revision) => {
      const api = createApiHandler(fixture, { scenario: 'runtime_save_loop' });
      const saved = dashboardPagesCase('settings.saved_loop');
      const body = { ...(saved.request.body as object) } as Record<string, unknown>;
      if (revision === 'missing') delete body['revision'];
      else body['revision'] = 'wrong-revision';
      const reply = await send(api, 'POST', '/api/settings', { body: JSON.stringify(body) });
      const refused = dashboardPagesCase(
        revision === 'missing'
          ? 'settings.revision_missing'
          : 'settings.stale_revision_another_client_saved_first',
      );
      expect([reply.status, reply.body]).toEqual([refused.status, refused.body]);
      const read = await send(api, 'GET', '/api/settings', { cookie: cookiesOf(reply) });
      expect(read.body).toEqual(dashboardPagesCase('settings.saved_device_preview').body);
    },
  );
  it.each([
    ['runtime_save_loop', 'settings.saved_loop'],
    ['runtime_save_tap', 'settings.saved_tap'],
  ])('T1 T2 %s writes and reads the recorded save case', async (scenario, reference) => {
    const recorded = dashboardPagesCase(reference);
    const api = createApiHandler(fixture, { scenario });
    const saved = await send(api, 'POST', '/api/settings', {
      body: JSON.stringify(recorded.request.body),
    });
    expect([saved.status, saved.body]).toEqual([recorded.status, recorded.body]);
    const read = await send(api, 'GET', '/api/settings', { cookie: cookiesOf(saved) });
    expect(read.body).toEqual(recorded.body);
  });
  it('T3 Runtime default is the recorded source-match case', async () => {
    const result = await send(
      createApiHandler(fixture, { scenario: 'runtime' }),
      'GET',
      '/api/runtime',
    );
    const recorded = dashboardPagesCase(
      'runtime_state.idle_server_build_matches_the_source_on_disk',
    );
    expect([result.status, result.body]).toEqual([recorded.status, recorded.body]);
  });
  it.each(
    dashboardPagesCaseNames('runtime_diagnostics').filter((name) =>
      dashboardPagesCase(`runtime_diagnostics.${name}`).request.path.includes('?'),
    ),
  )('T4 exact recorded query %s supplies its actual answer, including refusals', async (name) => {
    const recorded = dashboardPagesCase(`runtime_diagnostics.${name}`);
    const result = await send(
      createApiHandler(fixture, { scenario: 'runtime' }),
      'GET',
      recorded.request.path,
    );
    expect([result.status, result.body]).toEqual([recorded.status, recorded.body]);
  });
  it('T4 unmatched query falls back to the recorded default, without inventing an answer', async () => {
    const result = await send(
      createApiHandler(fixture, { scenario: 'runtime' }),
      'GET',
      '/api/diagnostics/events?kind=unrecorded',
    );
    expect(result.body).toEqual(
      dashboardPagesCase('runtime_diagnostics.every_event_oldest_first_with_log_coordinates').body,
    );
  });
  it.each([
    [
      'runtime_source_changed',
      '/api/runtime',
      'runtime_state.server_started_before_the_source_changed_restart_needed',
    ],
    [
      'runtime_worker_unknown',
      '/api/runtime',
      'runtime_state.worker_that_cannot_be_verified_is_unknown_not_dead',
    ],
    [
      'runtime_truncated',
      '/api/diagnostics/events',
      'runtime_diagnostics.malformed_lines_are_counted_and_a_half_written_last_line_is_truncation',
    ],
    [
      'runtime_read_errors',
      '/api/diagnostics/events',
      'runtime_diagnostics.one_file_cannot_be_read_the_others_are_returned_and_the_error_is_listed',
    ],
    [
      'runtime_writer_failure',
      '/api/diagnostics/events',
      'runtime_diagnostics.writer_health_reports_failed_writes',
    ],
  ])('T3 T5 Runtime scenario %s is a recorded answer', async (scenario, route, reference) => {
    const result = await send(createApiHandler(fixture, { scenario }), 'GET', route);
    expect(result.body).toEqual(dashboardPagesCase(reference).body);
  });
});
