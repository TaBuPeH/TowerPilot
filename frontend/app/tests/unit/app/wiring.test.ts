import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { commandView } from '../../../src/core/commands.ts';
import { own, ownPath } from '../../../src/core/read.ts';
import { answeredBody } from '../../../src/store/reads.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import { selectReadPlan } from '../../../src/store/selectors/readPlan.ts';
import { selectThisBattle } from '../../../src/store/selectors/thisBattle.ts';
import { bootDashboard, E0_CONTEXT, e0Config, flush, fence } from '../../support/dataLayer.ts';
import { dashboardBody, dashboardCase } from '../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../support/fixture.ts';

/**
 * The wired data layer (app/wiring.ts) with the fake transport of OD-8,
 * including the cases the backend cannot produce (dashboard contract report,
 * `cannot_produce`): the dropped clear_flag answer and the overlapping read of
 * /api/battles/active.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('fake-transport cases the backend cannot produce', () => {
  it('a dropped clear_flag answer stays unconfirmed, is never resent, and a later stop_flag is only the current state', async () => {
    const listed = dashboardBody('status.stop_flag_true_with_a_listed_run') as Record<
      string,
      unknown
    >;
    let body: unknown = {
      ...listed,
      ...statusBody(sample('status.off'), { processes: listed['processes'] }),
    };
    const { fake, data } = bootDashboard({ status: () => body });
    await flush();
    await data.commands.clearFlag({ ...fence(data), operationId: 'op-run', expectedRevision: 2 });
    expect(fake.writes().map((entry) => JSON.parse(entry.body ?? '{}') as unknown)).toEqual([
      { action: 'clear_flag', operation_id: 'op-run', expected_revision: 2 },
    ]);
    // The flag file is removed on the server, then the answer never arrives at all.
    fake.last('/api/control').lose();
    const cleared = dashboardBody(
      'status.stop_flag_false_after_the_lost_clear_flag_answer',
    ) as Record<string, unknown>;
    body = { ...cleared, ...statusBody(sample('status.off'), { processes: cleared['processes'] }) };
    await vi.advanceTimersByTimeAsync(60_000);
    const line = selectOutcomes(data.store.getSnapshot()).lines[0];
    expect(line).toMatchObject({
      kind: 'clear_flag',
      phase: 'unconfirmed',
      lost: 'network',
      settledByRead: null,
      resendOffered: false,
    });
    expect(own(answeredBody(data.store.getSnapshot(), 'status'), 'stop_flag')).toBe(false);
    expect(data.machine.resend(line?.id ?? '')).toBe('not_resendable');
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });

  it('an overlapping /api/battles/active read (row before the close, commands after) settles a Run now as recorded, never as done, and sends nothing', async () => {
    // Admit from a recorded ready battle: pending controls/actions do not permit Run now.
    const active = dashboardCase(
      'battles_active.commands_controls_applied_with_overrides_action_failed',
    ).body as Record<string, unknown>;
    const battle = active['battle'] as Record<string, unknown>;
    const context = battle['context'] as Record<string, unknown>;
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/battles/active', () => ({ status: 200, body: active }));
      },
    });
    await flush();
    expect(data.store.getSnapshot().context).toEqual({
      instance: context['instance'],
      serial: context['serial'],
      account: context['account'],
      profile: context['profile'],
    });
    const runNow = selectThisBattle(data.store.getSnapshot()).routines.find(
      (row) => row.routine === 'ad_gems',
    )?.runNow;
    expect(runNow).toMatchObject({ enabled: true, reason: null });
    const command = runNow?.command;
    if (command === null || command === undefined) throw new Error('missing admitted Run now');
    expect(command).toEqual({
      ...fence(data),
      operationId: battle['operation_id'],
      battleId: battle['id'],
      generation: battle['generation'],
      expectedRevision: battle['control_revision'],
      action: 'ad_gems',
    });
    await data.commands.runNow(command);
    expect(fake.writes()).toHaveLength(1);
    expect(fake.writes()[0]?.path).toBe(`/api/operations/${command.operationId}/actions`);
    const sent = JSON.parse(fake.writes()[0]?.body ?? '{}') as Record<string, unknown>;
    expect(sent).toEqual({
      action: command.action,
      battle_id: command.battleId,
      generation: command.generation,
      expected_revision: command.expectedRevision,
      idempotency_key: 'key-0001',
    });
    fake.last(/\/actions$/).respond(500, '<!doctype html>');
    await flush();
    expect(selectOutcomes(data.store.getSnapshot()).lines[0]).toMatchObject({
      kind: 'run_now',
      phase: 'unconfirmed',
      settledByRead: null,
    });
    // Synthetic overlap: keep the recorded active battle row, but add a new expired command
    // for this admitted request. The prior command supplies only the record shape, not evidence.
    const recorded = (battle['commands'] as Record<string, unknown>[]).find(
      (entry) => entry['kind'] === 'action',
    );
    const overlapping = {
      ...active,
      battle: {
        ...battle,
        commands: [
          {
            ...recorded,
            id: 'overlapping-action',
            revision: command.expectedRevision,
            evidence: null,
            state: 'expired',
            reason: 'battle_ended',
            request_keys: [sent['idempotency_key']],
          },
        ],
      },
    };
    fake.route('GET', '/api/battles/active', () => ({ status: 200, body: overlapping }));
    await vi.advanceTimersByTimeAsync(3000);
    const line = selectOutcomes(data.store.getSnapshot()).lines[0];
    // Answered unknown (500): no answer can still arrive, so the read resolves the record.
    expect(line).toMatchObject({ kind: 'run_now', phase: 'confirmed', resendOffered: false });
    expect(line?.settledByRead).toBe('recorded');
    expect(data.machine.resend(line?.id ?? '')).toBe('not_unknown');
    const commands = ownPath(overlapping, 'battle', 'commands');
    expect(commandView(Array.isArray(commands) ? commands[0] : undefined).decision).toMatchObject({
      row: 'K6',
      line: 'battle_record_closed',
    });
    expect(fake.writes()).toHaveLength(1);
    data.stop();
  });
});

describe('INV-1 / OD-13 through the wired data layer (9.1 X2a, X2b)', () => {
  it('X2b: no read and no session record names the emulator: the Pause click sends nothing and says so; Turn input off still posts', async () => {
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
        transport.route('GET', '/api/config', () => ({ status: 502, body: 'Bad gateway' }));
        transport.route('GET', '/api/harvest', () => ({ status: 502, body: 'Bad gateway' }));
      },
    });
    await flush();
    await data.commands.pause();
    expect(
      fake.requests.filter((entry) => entry.method === 'GET').map((entry) => entry.path),
    ).toEqual(expect.arrayContaining(['/api/config', '/api/harvest']));
    expect(fake.writes()).toEqual([]);
    expect(selectOutcomes(data.store.getSnapshot()).notices).toMatchObject([
      { kind: 'pause_not_sent', reason: 'context_not_named' },
    ]);
    await data.commands.turnInputOff();
    expect(fake.writes().map((entry) => [entry.path, entry.body])).toEqual([
      ['/api/automation-access', '{"enabled":false}'],
    ]);
    data.stop();
  });

  it('X2a: the click reads config, sends the E0 body once, and the "not sent" line goes', async () => {
    let configOk = false;
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
        transport.route('GET', '/api/config', () =>
          configOk
            ? {
                status: 200,
                body: { active_instance: 'main', instances: { main: { serial: 'fake:1' } } },
              }
            : { status: 502, body: 'Bad gateway' },
        );
        transport.route('GET', '/api/harvest', () => ({ status: 502, body: 'Bad gateway' }));
      },
    });
    await flush();
    await data.commands.pause();
    configOk = true;
    await data.commands.pause();
    expect(fake.writes().map((entry) => JSON.parse(entry.body ?? '{}') as unknown)).toEqual([
      {
        enabled: false,
        expected_context: { instance: 'main', serial: 'fake:1' },
        idempotency_key: 'key-0001',
      },
    ]);
    expect(selectOutcomes(data.store.getSnapshot()).notices).toEqual([]);
    data.stop();
  });
});

describe('the read plan follows the store (spec 7.2)', () => {
  it('reads status at 500 ms while a write is unresolved and at 2500 ms otherwise', async () => {
    const { data } = bootDashboard();
    await vi.advanceTimersByTimeAsync(3000);
    expect(selectReadPlan(data.store.getSnapshot(), 'status').mode).toBe('idle');
    await data.commands.turnInputOff();
    expect(selectReadPlan(data.store.getSnapshot(), 'status').mode).toBe('active');
    data.stop();
  });

  it('an input answer asks for a later config read; a write answer asks for a later status read', async () => {
    const { fake, data } = bootDashboard();
    await vi.advanceTimersByTimeAsync(3000);
    const configReads = () => fake.requests.filter((entry) => entry.path === '/api/config').length;
    const statusReads = () => fake.requests.filter((entry) => entry.path === '/api/status').length;
    const config = configReads();
    const status = statusReads();
    await data.commands.turnInputOff();
    fake.last('/api/automation-access').respond(200, {
      enabled: false,
      ok: true,
      revocation: {
        config_persisted: true,
        errors: [],
        fenced: true,
        harvest: { paused: false, revision: null },
        latched: true,
        revoked_at: 1800000000.0,
        state: 'nothing_running',
        still_owned: [],
        stopped: [],
        stopping: [],
      },
    });
    await flush();
    expect(configReads()).toBe(config + 1);
    expect(statusReads()).toBe(status + 1);
    data.stop();
  });

  it('a visible tab again reads status, calibration, the active battle and the workspace data once', async () => {
    const { fake, data, visibility } = bootDashboard();
    await vi.advanceTimersByTimeAsync(3000);
    visibility.set(true);
    await vi.advanceTimersByTimeAsync(20_000);
    const before = fake.requests.length;
    visibility.set(false);
    await flush();
    const paths = fake.requests.slice(before).map((entry) => entry.path);
    expect(paths).toEqual(
      expect.arrayContaining([
        '/api/status',
        '/api/calibrate/status',
        '/api/battles/active',
        '/api/config',
        '/api/accounts',
        '/api/runs',
        '/api/wizard/tools',
        '/api/wizard/state',
      ]),
    );
    expect(data.store.getSnapshot().hidden).toBe(false);
    data.stop();
  });
});

describe('a failed first config read recovers (PE5 load flake, readPlan config retry)', () => {
  for (const failure of ['http', 'timeout'] as const) {
    it(`a ${failure} first config read is read again on the backoff until the context is confirmed, then config polling stops`, async () => {
      let fail = true;
      const { fake, data } = bootDashboard({
        before: (transport) => {
          transport.route('GET', '/api/config', () => {
            if (!fail) return { status: 200, body: e0Config() };
            // A timed-out read never answers; the poller aborts it after 5 s.
            return failure === 'http' ? { status: 502, body: 'Bad gateway' } : 'hold';
          });
        },
      });
      const configReads = () =>
        fake.requests.filter((entry) => entry.path === '/api/config').length;
      await flush();
      if (failure === 'timeout') await vi.advanceTimersByTimeAsync(5_000);
      expect(data.store.getSnapshot().reads.config.latest?.outcome.kind).toBe('failed');
      expect(data.store.getSnapshot().contextConfirmed).toBe(false);
      expect(selectReadPlan(data.store.getSnapshot(), 'config').retry).toBe('schedule');
      fail = false;
      await vi.advanceTimersByTimeAsync(120_000);
      expect(data.store.getSnapshot().contextConfirmed).toBe(true);
      expect(data.store.getSnapshot().context).toEqual(E0_CONTEXT);
      expect(selectReadPlan(data.store.getSnapshot(), 'config').retry).toBeUndefined();
      const settled = configReads();
      expect(settled).toBeGreaterThan(1);
      await vi.advanceTimersByTimeAsync(120_000);
      expect(configReads()).toBe(settled);
      data.stop();
    });
  }

  it('a failed config read in a confirmed context is not retried by the plan', async () => {
    let fail = false;
    const { fake, data } = bootDashboard({
      before: (transport) => {
        transport.route('GET', '/api/config', () =>
          fail ? { status: 502, body: 'Bad gateway' } : { status: 200, body: e0Config() },
        );
      },
    });
    await vi.advanceTimersByTimeAsync(3000);
    expect(data.store.getSnapshot().contextConfirmed).toBe(true);
    fail = true;
    await data.commands.turnInputOff();
    fake.last('/api/automation-access').respond(502, 'Bad gateway');
    await flush();
    const configReads = () => fake.requests.filter((entry) => entry.path === '/api/config').length;
    const after = configReads();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(configReads()).toBe(after);
    data.stop();
  });
});
