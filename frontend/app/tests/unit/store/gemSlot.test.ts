import { describe, expect, it } from 'vitest';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { selectInputSlot } from '../../../src/store/selectors/inputSlot.ts';
import { e0Config } from '../../support/dataLayer.ts';
import { dashboardBody } from '../../support/dashboardFixture.ts';
import { sample, sampleRecord, serverTimeOf, statusBody } from '../../support/fixture.ts';
import { storeHarness } from '../../support/storeHarness.ts';

/** Control / Gem harvesting from the store (S-030, S-034). */
function collectingWith(change: (harvest: Record<string, unknown>) => void) {
  const harvest = sampleRecord('status.collecting');
  change(harvest);
  const h = storeHarness({ config: e0Config(false) });
  h.read('status', statusBody(harvest));
  return selectGemSlot(h.state());
}

const battle = (harvest: Record<string, unknown>) =>
  (harvest['operation'] as Record<string, unknown>)['battle'] as Record<string, unknown>;

describe('S-030 green Collecting only with D-6 for the current operation', () => {
  const mutations: readonly [string, (harvest: Record<string, unknown>) => void][] = [
    ['observation_age_sec 180', (harvest) => (battle(harvest)['observation_age_sec'] = 180)],
    ['server_time removed', (harvest) => Reflect.deleteProperty(harvest, 'server_time')],
    [
      'published_at in the future',
      (harvest) => (battle(harvest)['published_at'] = serverTimeOf(harvest) + 3600),
    ],
    ['publication_valid false', (harvest) => (battle(harvest)['publication_valid'] = false)],
    ['battle id an object', (harvest) => (battle(harvest)['id'] = {})],
    [
      'routines []',
      (harvest) => ((harvest['operation'] as Record<string, unknown>)['routines'] = []),
    ],
    ['input_revoked "true"', (harvest) => (harvest['input_revoked'] = 'true')],
    [
      'worker_alive null',
      (harvest) => ((harvest['operation'] as Record<string, unknown>)['worker_alive'] = null),
    ],
    ['battle fresh false', (harvest) => (battle(harvest)['fresh'] = false)],
  ];

  it.each(mutations)('S-030 %s: never green, Pause present', (_name, change) => {
    const slot = collectingWith(change);
    expect(slot.seen.word).not.toBe('running');
    expect(slot.pause).toMatchObject({ shown: true, enabled: true });
  });

  it('S-030 control: the unmodified sample is green (EV-3) with Pause', () => {
    const slot = selectGemSlotOf(sample('status.collecting'));
    expect(slot.seen).toMatchObject({ word: 'running', evidence: 'EV-3' });
    expect(slot.pause.shown).toBe(true);
  });
});

function selectGemSlotOf(harvest: unknown) {
  const h = storeHarness({ config: e0Config(false) });
  h.read('status', statusBody(harvest));
  return selectGemSlot(h.state());
}

describe('S-034 desired choice and observed state are separate facts', () => {
  it('S-034 a manual collector: choice Not set beside Running, with its operation Stop; never "On"', () => {
    const h = storeHarness({ config: e0Config(false) });
    h.read('status', statusBody(sample('status.manual_collector')));
    const slot = selectGemSlot(h.state());
    expect(slot.choice.word).toBe('not_set');
    expect(slot.seen.word).toBe('running');
    const stop = selectAutomationSlot(h.state()).rows.find((row) => row.stop.kind === 'cancel');
    expect(stop?.stop).toMatchObject({ shown: true, enabled: true });
    expect(stop?.stop.command).toMatchObject({ kind: 'cancel', epoch: 0 });
    expect(selectInputSlot(h.state()).turnOff).toMatchObject({ shown: true, enabled: true });
  });

  it('S-034 control: the collecting sample shows On and Collecting', () => {
    const slot = selectGemSlotOf(sample('status.collecting'));
    expect(slot.choice.word).toBe('on');
    expect(slot.seen.word).toBe('running');
  });

  it('S-034 Turn on is offered only with a readable revision, a valid source and a permitting collection guard', () => {
    // The independent decision: a root collection guard that permits collection (guards_clear).
    const clear = dashboardBody('status.guards_clear') as Record<string, unknown>;
    const blocking = dashboardBody(
      'status.guards_setup_and_collection_blocking_the_same_operation',
    ) as Record<string, unknown>;
    const offHarvest = sampleRecord('status.off');
    const desired = offHarvest['desired'] as Record<string, unknown>;
    // F-027: the other prerequisites in place (taps allowed, the emulator connected).
    const withGuard = (guard: unknown) => {
      const h = storeHarness({ config: e0Config(true) });
      const body: Record<string, unknown> = statusBody(offHarvest);
      body['device_state'] = 'device';
      if (guard !== undefined) body['collection_guard'] = guard;
      h.read('status', body);
      return selectGemSlot(h.state());
    };
    expect(withGuard(clear['collection_guard']).turnOn).toEqual({
      shown: true,
      enabled: true,
      reason: null,
      sending: false,
      command: { preset: desired['preset'], expectedRevision: desired['revision'], epoch: 0 },
    });
    // Missing, blocking, or a guard of the wrong scope (the run guard): shown, never enabled.
    for (const guard of [undefined, blocking['collection_guard'], clear['setup_battle_guard']]) {
      expect(withGuard(guard).turnOn).toMatchObject({
        shown: true,
        enabled: false,
        reason: 'guard',
        command: null,
      });
    }
    const off = selectGemSlotOf(sample('status.off'));
    expect(off.turnOn.shown).toBe(true);
    const invalid = selectGemSlotOf(sample('status.policy_invalid'));
    expect(invalid.turnOn.enabled).toBe(false);
    const unknown = selectGemSlotOf(sample('status.status_failed'));
    expect(unknown.turnOn.shown).toBe(false);
    expect(unknown.pause.shown).toBe(true);
  });
});

describe('F-027 the store feeds every Turn on prerequisite to core', () => {
  const clear = (dashboardBody('status.guards_clear') as Record<string, unknown>)[
    'collection_guard'
  ];
  function turnOnOf(options: {
    readonly allowTaps: boolean;
    readonly processes?: unknown;
    readonly deviceState?: string;
    readonly after?: (h: ReturnType<typeof storeHarness>) => void;
  }) {
    const h = storeHarness({ config: e0Config(options.allowTaps) });
    const body: Record<string, unknown> = statusBody(
      sampleRecord('status.off'),
      options.processes === undefined ? {} : { processes: options.processes },
    );
    body['device_state'] = options.deviceState ?? 'device';
    body['collection_guard'] = clear;
    h.read('status', body);
    options.after?.(h);
    return selectGemSlot(h.state()).turnOn;
  }
  const x9 = [{ pid: 9009, operation_id: 7 }];

  it('F-027 X9 with taps off, and with taps allowed: Turn on shown, disabled "status_incomplete", no command', () => {
    for (const allowTaps of [false, true]) {
      expect(turnOnOf({ allowTaps, processes: x9 }), String(allowTaps)).toEqual({
        shown: true,
        enabled: false,
        reason: 'status_incomplete',
        sending: false,
        command: null,
      });
    }
  });

  it('F-027 taps off, an offline emulator and an unresolved harvest request each disable Turn on with their own reason', () => {
    expect(turnOnOf({ allowTaps: false })).toMatchObject({ enabled: false, reason: 'taps_off' });
    expect(turnOnOf({ allowTaps: true, deviceState: 'offline' })).toMatchObject({
      enabled: false,
      reason: 'not_connected',
    });
    expect(
      turnOnOf({
        allowTaps: true,
        // A Turn on whose answer was lost; no later read has settled it yet.
        after: (h) => {
          h.intent({
            id: 'lost',
            kind: 'turn_on',
            target: 'harvest',
            phase: 'unconfirmed',
            lostReason: 'network',
            meta: { expectedRevision: 2 },
          });
        },
      }),
    ).toMatchObject({ enabled: false, reason: 'request_unresolved', command: null });
  });

  it('F-027 control: a D-4 read, connected, taps allowed, a clear guard and no open request enable Turn on with the exact command', () => {
    const desired = sampleRecord('status.off')['desired'] as Record<string, unknown>;
    expect(turnOnOf({ allowTaps: true })).toEqual({
      shown: true,
      enabled: true,
      reason: null,
      sending: false,
      command: { preset: desired['preset'], expectedRevision: desired['revision'], epoch: 0 },
    });
  });
});
