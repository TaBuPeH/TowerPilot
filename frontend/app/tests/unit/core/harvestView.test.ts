import { describe, expect, it } from 'vitest';
import { automationSlot } from '../../../src/core/automation.ts';
import { gemCause, gemSeen, gemSlot, turnOnView } from '../../../src/core/harvestView.ts';
import { configRead, sampleRecord, statusBody } from '../../support/fixture.ts';
import { contextOf, rec, TURN_ON_READY } from '../../support/core.ts';

const ALLOW = { blocks_launch: false, scope: 'collection', code: null, message: null };

describe('S-034 desired and observed are separate facts', () => {
  it('S-034 manual_collector: choice Not set beside Running, with its operation Stop; never "On"', () => {
    const body = statusBody(sampleRecord('status.manual_collector'));
    const view = gemSlot(contextOf(body), null, []);
    expect(view.choice.word).toBe('not_set');
    expect(view.seen).toMatchObject({ row: 'G3a', word: 'running', evidence: 'EV-3' });
    const operation = rec(sampleRecord('status.manual_collector')['operation']);
    const stops = automationSlot({
      status: contextOf(body),
      extraClaims: [],
      lastIdleAgeSec: null,
    }).rows.map((row) => row.stop);
    expect(stops).toEqual([
      { kind: 'cancel', operationId: operation['id'], expectedRevision: operation['revision'] },
    ]);
    expect(view.seen.params).toMatchObject({ collectOnly: true });
  });

  it('S-034 stopping with desired Off gives choice Off with Stopping', () => {
    const harvest = sampleRecord('status.stopping');
    rec(harvest['desired'])['choice'] = 'off';
    rec(harvest['desired'])['enabled'] = false;
    const view = gemSlot(contextOf(statusBody(harvest)), null, []);
    expect([view.choice.word, view.seen.word]).toEqual(['off', 'stopping']);
  });

  it('S-034 control: collecting shows On and Running', () => {
    const view = gemSlot(contextOf(statusBody(sampleRecord('status.collecting'))), null, []);
    expect([view.choice.word, view.seen.word]).toEqual(['on', 'running']);
  });

  it('S-034 the chip never comes from the choice: Off with nothing seen is Idle by EV-5, not by the choice', () => {
    expect(gemSeen(contextOf(statusBody(sampleRecord('status.off'))))).toMatchObject({
      row: 'G5f',
      evidence: 'EV-5',
    });
  });
});

describe("S-037 when harvesting can't act its cause is stated and nothing reads Collecting", () => {
  const samples: [string, string][] = [
    ['reconciler_inactive', 'reconciler_inactive'],
    ['reconciler_failing', 'reconciler_failing'],
    ['circuit_open', 'blocked'],
    ['backing_off', 'backing_off'],
    ['setup_guard_block', 'blocked'],
    ['taps_disabled', 'blocked'],
    ['input_revoked', 'blocked'],
    ['emulator_offline', 'blocked'],
    ['policy_invalid', 'source_invalid'],
  ];

  it.each(samples)(
    'S-037 %s states its cause, keeps Pause while On, never green',
    (name, cause) => {
      const body = statusBody(sampleRecord(`status.${name}`));
      const context = contextOf(body);
      const view = gemSlot(context, null, []);
      expect(view.seen.word).not.toBe('running');
      expect(gemCause(context, view.seen)).toMatchObject({ kind: cause });
      expect(view.pause).toBe(true);
    },
  );

  it('S-037 backing_off gives the retry time from server values only; policy_invalid its Edit link', () => {
    const backing = sampleRecord('status.backing_off');
    const context = contextOf(statusBody(backing));
    expect(gemCause(context, gemSeen(context))).toEqual({
      kind: 'backing_off',
      retryInSec: (backing['next_attempt_at'] as number) - (backing['server_time'] as number),
    });
    const invalid = contextOf(statusBody(sampleRecord('status.policy_invalid')));
    expect(gemCause(invalid, gemSeen(invalid))).toMatchObject({
      kind: 'source_invalid',
      edit: rec(sampleRecord('status.policy_invalid')['source'])['edit'],
    });
  });

  it('S-037 a blocked state carries its own code', () => {
    const context = contextOf(statusBody(sampleRecord('status.circuit_open')));
    expect(gemSeen(context)).toMatchObject({ row: 'G5d', params: { code: 'collector_failing' } });
  });
});

describe('S-104 Turn on and Collect only with a validated source and a permitting collection_guard', () => {
  it('S-104 policy_invalid: Turn on disabled with the source message and its Edit link', () => {
    const harvest = sampleRecord('status.policy_invalid');
    const view = turnOnView(contextOf(statusBody(harvest)), ALLOW, TURN_ON_READY);
    expect(view).toMatchObject({
      offered: false,
      reason: 'source_invalid',
      sourceEdit: rec(harvest['source'])['edit'],
    });
  });

  it('S-104 source_changed carries "the source changed since On"', () => {
    const view = turnOnView(
      contextOf(statusBody(sampleRecord('status.source_changed'))),
      ALLOW,
      TURN_ON_READY,
    );
    expect(view).toMatchObject({ offered: true, sourceChanged: true });
  });

  it('S-104 a blocking or missing collection guard withholds Turn on (S-100)', () => {
    const body = statusBody(sampleRecord('status.off'));
    expect(
      turnOnView(
        contextOf(body),
        {
          ...ALLOW,
          blocks_launch: true,
          code: 'setup_battle_unresolved',
        },
        TURN_ON_READY,
      ),
    ).toMatchObject({
      offered: false,
      reason: 'guard',
      guard: { kind: 'blocks', code: 'setup_battle_unresolved' },
    });
    expect(turnOnView(contextOf(body), undefined, TURN_ON_READY)).toMatchObject({
      offered: false,
      reason: 'guard',
      guard: { kind: 'unconfirmed', reason: 'guard_missing' },
    });
  });

  it('S-104 control: status.off with a valid source offers Turn on with the revision the person saw', () => {
    const harvest = sampleRecord('status.off');
    expect(turnOnView(contextOf(statusBody(harvest)), ALLOW, TURN_ON_READY)).toMatchObject({
      offered: true,
      expectedRevision: rec(harvest['desired'])['revision'],
      sourceChanged: false,
    });
  });
});

describe('F-027 Turn on needs every prerequisite, each with its own reason', () => {
  const off = () => statusBody(sampleRecord('status.off'));

  it('F-027 X9: a malformed linked row fails D-4: Turn on refused "status_incomplete", with taps off or allowed', () => {
    const body = statusBody(sampleRecord('status.off'), {
      processes: [{ pid: 9009, operation_id: 7 }],
    });
    for (const allowTaps of [false, true]) {
      expect(
        turnOnView(contextOf(body), ALLOW, { ...TURN_ON_READY, config: configRead(allowTaps) }),
      ).toMatchObject({ offered: false, reason: 'status_incomplete' });
    }
  });

  it('F-027 a stale process scan fails D-4 too', () => {
    const body = statusBody(sampleRecord('status.off'), {
      scan: { state: 'stale', error: null, age_sec: 180, last_success_at: 0 },
    });
    expect(turnOnView(contextOf(body), ALLOW, TURN_ON_READY)).toMatchObject({
      offered: false,
      reason: 'status_incomplete',
    });
  });

  it.each([
    ['adb_offline', 'not_connected'],
    ['connection_unconfirmed', 'not_connected'],
  ] as const)('F-027 connection %s: Turn on refused "%s"', (connection, reason) => {
    expect(turnOnView(contextOf(off()), ALLOW, { ...TURN_ON_READY, connection })).toMatchObject({
      offered: false,
      reason,
    });
  });

  it('F-027 taps off, and an unreadable config, each refuse Turn on with their own reason', () => {
    expect(
      turnOnView(contextOf(off()), ALLOW, { ...TURN_ON_READY, config: configRead(false) }),
    ).toMatchObject({ offered: false, reason: 'taps_off' });
    expect(turnOnView(contextOf(off()), ALLOW, { ...TURN_ON_READY, config: null })).toMatchObject({
      offered: false,
      reason: 'taps_unknown',
    });
  });

  it.each([
    ['sending', 'request_in_flight'],
    ['unresolved', 'request_unresolved'],
  ] as const)('F-027 a harvest request %s refuses Turn on "%s"', (harvestRequest, reason) => {
    expect(turnOnView(contextOf(off()), ALLOW, { ...TURN_ON_READY, harvestRequest })).toMatchObject(
      { offered: false, reason },
    );
  });

  it('F-027 control: every prerequisite in place offers Turn on with the revision the person saw', () => {
    expect(turnOnView(contextOf(off()), ALLOW, TURN_ON_READY)).toMatchObject({ offered: true });
  });
});
