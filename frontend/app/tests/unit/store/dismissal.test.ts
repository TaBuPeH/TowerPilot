import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendClearFlag } from '../../../src/api/intents/clearFlag.ts';
import { sendHandoff } from '../../../src/api/intents/handoff.ts';
import { sendPause, sendTurnOn } from '../../../src/api/intents/harvest.ts';
import { sendInputOff, sendInputOn } from '../../../src/api/intents/input.ts';
import { sendKill } from '../../../src/api/intents/kill.ts';
import type { IntentMachine } from '../../../src/api/intents/machine.ts';
import { restoreUnresolved, STORAGE_PREFIX } from '../../../src/api/intents/persistence.ts';
import { sendReconnect } from '../../../src/api/intents/reconnect.ts';
import { sendRunNow } from '../../../src/api/intents/routineAction.ts';
import { sendCollect } from '../../../src/api/intents/start.ts';
import { sendLiveSave, sendSavedSave } from '../../../src/api/intents/runs.ts';
import { sendStop } from '../../../src/api/intents/stop.ts';
import { INTENT_KINDS, type IntentKind } from '../../../src/api/intents/types.ts';
import { sendRunAdd } from '../../../src/api/intents/runAdd.ts';
import { sendScheduleSave } from '../../../src/api/schedule.ts';
import { sendProfilePatch, sendSettingsSave } from '../../../src/api/intents/collection.ts';
import { sendEmulatorWrite } from '../../../src/api/intents/emulators.ts';
import {
  sendCalibrateApply,
  sendCalibrateObserve,
  sendCalibrateStart,
  sendCalibrateStop,
} from '../../../src/api/intents/recognition.ts';
import { selectAutomationSlot } from '../../../src/store/selectors/automationSlot.ts';
import { controlFacts } from '../../../src/store/selectors/controlFacts.ts';
import { selectFallback } from '../../../src/store/selectors/fallback.ts';
import { selectGemSlot } from '../../../src/store/selectors/gemSlot.ts';
import { selectOutcomes } from '../../../src/store/selectors/outcomes.ts';
import {
  bootDashboard,
  E0_CONTEXT,
  e0Config,
  fence,
  flush,
  machineHarness,
  postBodies,
} from '../../support/dataLayer.ts';
import { counter, createMemoryStorage } from '../../support/fakeTransport.ts';
import { sendProfileWrite, type ProfileWrite } from '../../../src/api/intents/profiles.ts';
import { sampleRecord, statusBody } from '../../support/fixture.ts';
import { answerOf, storeHarness } from '../../support/storeHarness.ts';

/**
 * R9-01, OD-10: Dismiss removes only the outcome line. It sends nothing and
 * clears no Known On, revocation, ownership or sequence-fence state, in the
 * page and across a reload: an unresolved record stays in session storage,
 * marked dismissed, until its outcome is known; it is restored hidden and
 * never resent; and an operation an owner notice named keeps its Stop.
 */
beforeEach(() => {
  vi.useFakeTimers({ now: 1_800_000_000_000 });
});
afterEach(() => {
  vi.useRealTimers();
});

function storedKeys(storage: ReturnType<typeof createMemoryStorage>): string[] {
  return Object.keys(storage.entries()).filter((key) => key.startsWith(STORAGE_PREFIX));
}

describe('R9-01 a dismissed Turn on keeps its D-8 fence across a reload', () => {
  const off = () => sampleRecord('status.off');
  const offRevision = () => Number((off()['desired'] as Record<string, unknown>)['revision']);

  /** Turn on at the read revision, its answer lost or still pending, its line dismissed, then a reload. */
  async function dismissedTurnOn(how: 'lost' | 'pending') {
    const storage = createMemoryStorage();
    const first = bootDashboard({ storage, status: () => statusBody(off()) });
    await flush();
    await first.data.commands.turnOn({
      ...fence(first.data),
      preset: 'bp_farm',
      expectedRevision: offRevision(),
    });
    if (how === 'lost') {
      first.fake.last('/api/harvest').lose();
      await flush();
    }
    const line = selectOutcomes(first.data.store.getSnapshot()).lines[0];
    expect(line).toMatchObject({ kind: 'turn_on' });
    first.data.commands.dismiss(line?.id ?? '');
    const before = first.data.store.getSnapshot();
    expect(selectOutcomes(before).lines).toEqual([]);
    expect(selectGemSlot(before).pause).toMatchObject({ shown: true, enabled: true });
    expect(storedKeys(storage)).toHaveLength(1);
    expect(first.fake.writes()).toHaveLength(1);
    first.data.stop();
    return storage;
  }

  it.each(['lost', 'pending'] as const)(
    'S-001 a %s Turn on, dismissed, then a reload: an Off read at its expected revision keeps Pause shown and enabled; the click sends the exact Pause',
    async (how) => {
      const storage = await dismissedTurnOn(how);
      const second = bootDashboard({ storage, status: () => statusBody(off()) });
      await flush();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(second.fake.writes()).toEqual([]);
      expect(second.data.machine.snapshot()).toMatchObject([
        {
          kind: 'turn_on',
          phase: 'unconfirmed',
          dismissed: true,
          restored: true,
          lostReason: how === 'lost' ? 'network' : 'page_reloaded',
          meta: { expectedRevision: offRevision() },
        },
      ]);
      const state = second.data.store.getSnapshot();
      expect(selectOutcomes(state).lines).toEqual([]);
      expect(controlFacts(state).unsettled).toEqual([{ expectedRevision: offRevision() }]);
      expect(selectGemSlot(state).pause).toEqual({
        shown: true,
        enabled: true,
        reason: null,
        sending: false,
        command: null,
      });
      await second.data.commands.pause();
      expect(postBodies(second.fake)).toEqual([
        {
          enabled: false,
          expected_context: { instance: 'main', serial: 'fake:1' },
          idempotency_key: 'key-0001',
        },
      ]);
      expect(second.fake.writes()[0]?.path).toBe('/api/harvest');
      second.data.stop();
    },
  );

  it('S-001 control: after the reload a read at a higher revision settles it (not applied) and releases the fence', async () => {
    const storage = await dismissedTurnOn('lost');
    const higher = off();
    (higher['desired'] as Record<string, unknown>)['revision'] = offRevision() + 1;
    const second = bootDashboard({ storage, status: () => statusBody(higher) });
    await flush();
    await vi.advanceTimersByTimeAsync(3000);
    expect(second.data.machine.snapshot()).toMatchObject([
      { kind: 'turn_on', phase: 'refused', readSettlement: 'not_applied', dismissed: true },
    ]);
    expect(storedKeys(storage)).toEqual([]);
    const state = second.data.store.getSnapshot();
    expect(controlFacts(state).unsettled).toEqual([]);
    expect(selectGemSlot(state).pause).toMatchObject({
      shown: false,
      enabled: false,
      reason: 'off_confirmed',
    });
    expect(second.fake.writes()).toEqual([]);
    second.data.stop();
  });
});

const OP = '00000003-0000-4000-8000-000000000000';
const profileWrite = (write: ProfileWrite) => (machine: IntentMachine) => {
  sendProfileWrite(machine, write, E0_CONTEXT, null);
};
const SENDERS: readonly (readonly [IntentKind, (machine: IntentMachine) => void])[] = [
  ['account_select', profileWrite({ kind: 'account_select', id: 'alpha' })],
  [
    'account_create',
    profileWrite({ kind: 'account_create', id: 'alpha', label: null, importCurrent: false }),
  ],
  ['account_tier', profileWrite({ kind: 'account_tier', maxTier: 12 })],
  ['profile_activate', profileWrite({ kind: 'profile_activate', name: 'second' })],
  [
    'profile_copy',
    profileWrite({
      kind: 'profile_copy',
      from: 'default',
      name: 'fresh',
      overwrite: false,
      existed: false,
    }),
  ],
  [
    'profile_promote',
    profileWrite({
      kind: 'profile_promote',
      draft: 'main.draft.yaml',
      name: 'promoted',
      base: 'default',
      overwrite: false,
      existed: false,
    }),
  ],
  [
    'schedule_save',
    (machine) => {
      sendScheduleSave(machine, E0_CONTEXT, 'r1', {});
    },
  ],
  [
    'settings_save',
    (machine) => {
      sendSettingsSave(
        machine,
        {
          settingsContext: { instance: 'main', account: null, profile: 'mine' },
          module: 'rewards',
          revision: 'r1',
          values: { daily_missions: true },
          draft: null,
        },
        E0_CONTEXT,
      );
    },
  ],
  [
    'run_add',
    (machine) => {
      sendRunAdd(
        machine,
        {
          runId: 'coin_two',
          revision: 'r1',
          template: 'farm',
          kind: 'coin',
          options: { label: 'Second coin run' },
          scope: 'add-scope',
          draft: { label: 'Second coin run' },
        },
        E0_CONTEXT,
      );
    },
  ],
  [
    'profile_patch',
    (machine) => {
      sendProfilePatch(
        machine,
        {
          target: 'patch:chores',
          profile: 'mine',
          path: ['policies', 'chores'],
          remove: false,
          value: [{ name: 'shatter', enabled: false }],
          revision: 'r1',
          draft: null,
        },
        E0_CONTEXT,
      );
    },
  ],
  ['pause', (machine) => sendPause(machine, E0_CONTEXT, counter('key'))],
  [
    'turn_on',
    (machine) =>
      sendTurnOn(
        machine,
        { preset: 'bp_farm', expectedRevision: 2, context: E0_CONTEXT },
        counter('key'),
      ),
  ],
  ['input_off', (machine) => sendInputOff(machine, E0_CONTEXT)],
  ['input_on', (machine) => sendInputOn(machine, E0_CONTEXT)],
  ['stop', (machine) => sendStop(machine, { operationId: OP, expectedRevision: 7 }, E0_CONTEXT)],
  ['kill', (machine) => sendKill(machine, { pid: 4242, created: 1790000000.5 }, E0_CONTEXT)],
  [
    'handoff',
    (machine) =>
      sendHandoff(
        machine,
        {
          operationId: OP,
          setupBattleId: 'battle-1',
          evidenceRevision: 'e'.repeat(64),
          attestation: 'new_manual_battle',
        },
        E0_CONTEXT,
      ),
  ],
  [
    'start',
    // Start's click first reads readiness (startClick); its record is the same launch body.
    (machine) =>
      machine.send({
        kind: 'start',
        target: 'launch',
        endpoint: 'control',
        path: '/api/control',
        body: {
          action: 'start',
          preset: 'bp_farm',
          expected_context: { ...E0_CONTEXT, rendering: null },
          idempotency_key: 'start-0001',
        },
        key: 'start-0001',
        meta: { preset: 'bp_farm' },
        context: E0_CONTEXT,
        supersedes: [],
      }),
  ],
  [
    'start_gems',
    (machine) =>
      sendCollect(
        machine,
        {
          preset: 'bp_farm',
          expected: { ...E0_CONTEXT, rendering: null },
          context: E0_CONTEXT,
        },
        counter('key'),
      ),
  ],
  [
    'clear_flag',
    (machine) => sendClearFlag(machine, { operationId: OP, expectedRevision: 2 }, E0_CONTEXT),
  ],
  ['reconnect', (machine) => sendReconnect(machine, E0_CONTEXT, E0_CONTEXT.serial)],
  [
    'adopt',
    (machine) =>
      sendEmulatorWrite(
        machine,
        {
          kind: 'adopt',
          baseRevision: 'r1',
          serial: '127.0.0.1:16384',
          adb: null,
          expectInstance: 'main',
          expectPlaceholder: false,
        },
        E0_CONTEXT,
      ),
  ],
  [
    'launch',
    (machine) =>
      sendEmulatorWrite(
        machine,
        { kind: 'launch', emulator: 'MuMu', index: '3', name: null },
        E0_CONTEXT,
      ),
  ],
  [
    'prepare',
    (machine) =>
      sendEmulatorWrite(machine, { kind: 'prepare', index: 'Pie64', name: null }, E0_CONTEXT),
  ],
  ['tools_install', (machine) => sendEmulatorWrite(machine, { kind: 'tools_install' }, E0_CONTEXT)],
  [
    'calibrate_start',
    (machine) =>
      sendCalibrateStart(
        machine,
        { kind: 'learn', battle: false, overwrite: false },
        { ...E0_CONTEXT, rendering: null },
        E0_CONTEXT,
        counter('key'),
      ),
  ],
  [
    'calibrate_observe',
    (machine) =>
      sendCalibrateObserve(
        machine,
        { watch: 30, run: null },
        { ...E0_CONTEXT, rendering: null },
        E0_CONTEXT,
        counter('key'),
      ),
  ],
  [
    'calibrate_apply',
    (machine) =>
      sendCalibrateApply(
        machine,
        { kind: 'operation', operationId: OP, expectedRevision: 3 },
        E0_CONTEXT,
        E0_CONTEXT,
      ),
  ],
  [
    'calibrate_stop',
    (machine) => sendCalibrateStop(machine, { operationId: OP, expectedRevision: 2 }, E0_CONTEXT),
  ],
  [
    'analysis_start',
    (machine) =>
      machine.send({
        kind: 'analysis_start',
        target: 'recognition:analysis',
        endpoint: 'analysisStart',
        path: '/api/screen-analysis/start',
        body: {
          expected_context: { ...E0_CONTEXT, rendering: null },
          idempotency_key: 'analysis-0001',
        },
        key: 'analysis-0001',
        meta: {},
        context: E0_CONTEXT,
        supersedes: [],
      }),
  ],
  [
    'template_write',
    (machine) =>
      machine.send({
        kind: 'template_write',
        target: 'recognition:template:buttons/retry.png',
        endpoint: 'templateWrite',
        path: '/api/template/buttons/retry.png',
        body: { ts: 'frame-1', x: 10, y: 20, w: 40, h: 30 },
        key: null,
        meta: { name: 'buttons/retry.png' },
        context: E0_CONTEXT,
        supersedes: [],
      }),
  ],
  [
    'run_save',
    (machine) =>
      sendSavedSave(
        machine,
        {
          blueprint: 'farm',
          context: { instance: 'main', account: null, profile: 'mine' },
          expectedRevision: 'r1',
          changes: { 'routines.ad_gems.interval_sec': 30 },
          draftScope: 'saved:ctx:bp_farm',
          draft: { 'routines.ad_gems.interval_sec': '030' },
        },
        E0_CONTEXT,
      ),
  ],
  [
    'live_controls',
    (machine) =>
      sendLiveSave(
        machine,
        {
          operationId: OP,
          battleId: 'battle-1',
          generation: 'gen-1',
          expectedRevision: 3,
          changes: { 'routines.ad_gems.enabled': false },
          draftScope: 'live:battle-1:gen-1',
          draft: { 'routines.ad_gems.enabled': false },
          battleLabel: 'Farm',
        },
        E0_CONTEXT,
        counter('key'),
      ),
  ],
  [
    'run_now',
    (machine) =>
      sendRunNow(
        machine,
        {
          operationId: OP,
          battleId: 'battle-1',
          generation: 'gen-1',
          expectedRevision: 0,
          action: 'ad_gems',
        },
        E0_CONTEXT,
        counter('key'),
      ),
  ],
];

describe('R9-01 every dismissible record keeps its safety state across a reload', () => {
  it('covers every write kind (F6R-01: against the authoritative INTENT_KINDS)', () => {
    expect(SENDERS.map(([kind]) => kind).sort()).toEqual([...INTENT_KINDS].sort());
  });

  it.each(SENDERS)(
    'OD-10 a lost %s, dismissed: kept in session storage, restored hidden with its facts, never resent',
    async (kind, send) => {
      const { fake, machine, storage } = machineHarness();
      send(machine);
      fake.next(/./, 'POST').lose();
      await flush();
      const record = machine.snapshot()[0];
      expect(record).toMatchObject({ kind, phase: 'unconfirmed' });
      machine.dismiss(record?.id ?? '');
      expect(restoreUnresolved(storage)).toMatchObject([
        { id: record?.id, kind, dismissed: true, restored: true, phase: 'unconfirmed' },
      ]);
      // The reload: reads fail, so nothing can settle the record; 60 s of polls send nothing.
      const reloaded = bootDashboard({
        storage,
        before: (transport) => {
          transport.route('GET', '/api/status', () => ({ status: 502, body: 'Bad gateway' }));
        },
      });
      await flush();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(reloaded.fake.writes()).toEqual([]);
      const state = reloaded.data.store.getSnapshot();
      expect(selectOutcomes(state).lines).toEqual([]);
      const facts = controlFacts(state);
      expect(facts.records.map((entry) => [entry.id, entry.dismissed])).toEqual([
        [record?.id, true],
      ]);
      // The facts each kind carries are all still there.
      if (kind === 'turn_on') expect(facts.unsettled).toEqual([{ expectedRevision: 2 }]);
      if (kind === 'input_on') {
        expect(facts.input.enables).toMatchObject([{ settled: false }]);
      }
      if (kind === 'stop') expect(facts.claims).toContainEqual({ operationId: OP, revision: 7 });
      // A resend is offered by no line, so none is sent.
      await reloaded.data.commands.resend(record?.id ?? '');
      expect(reloaded.fake.writes()).toEqual([]);
      reloaded.data.stop();
    },
  );

  it('S-048 control: an Allow game input whose answer settled it leaves storage', async () => {
    const { fake, machine, storage } = machineHarness();
    sendInputOn(machine, E0_CONTEXT);
    const ok = sampleRecord('automation_access.enable_ok');
    fake.next('/api/automation-access').respond(Number(ok['status']), ok['body']);
    await flush();
    machine.dismiss(machine.snapshot()[0]?.id ?? '');
    expect(storedKeys(storage)).toEqual([]);
  });
});

describe('OD-10 dismissing an owner notice keeps its operation Stop (INV-3)', () => {
  function refusedTurnOn() {
    const h = storeHarness({ config: e0Config(false) });
    // The last read does not show the owner: only the notice names it.
    h.read('status', statusBody(sampleRecord('status.off')));
    h.intent({ id: 'on', kind: 'turn_on', key: 'k-on', meta: { expectedRevision: 2 } });
    h.answer('on', answerOf(sampleRecord('refusals.device_owned_run')), 'refused');
    return h;
  }
  const owner = () =>
    (sampleRecord('refusals.device_owned_run')['body'] as Record<string, unknown>)[
      'owner'
    ] as Record<string, unknown>;

  it('S-070 the notice dismissed: its operation keeps a shown, enabled Stop with the exact command', () => {
    const h = refusedTurnOn();
    const recovery = owner()['recovery'] as Record<string, unknown>;
    const command = {
      kind: 'cancel',
      operationId: owner()['operation_id'],
      expectedRevision: recovery['expected_revision'],
      epoch: 0,
    };
    expect(selectOutcomes(h.state()).ownerNotice?.recovery).toMatchObject({
      kind: 'stop',
      command,
    });
    h.intent({
      ...h.state().intents[0],
      id: 'on',
      kind: 'turn_on',
      dismissed: true,
    });
    const state = h.state();
    expect(selectOutcomes(state).ownerNotice).toBeNull();
    const expected = {
      kind: 'cancel',
      shown: true,
      enabled: true,
      origin: 'owner_notice',
      command,
    };
    for (const stops of [selectAutomationSlot(state).stops, selectFallback(state).stops]) {
      expect(stops).toHaveLength(1);
      expect(stops[0]).toMatchObject(expected);
    }
  });

  it('S-070 control: a status read started after the notice that proves the operation stopped removes that Stop', () => {
    const h = refusedTurnOn();
    h.read('status', statusBody(sampleRecord('status.off')));
    expect(selectAutomationSlot(h.state()).stops).toEqual([]);
  });
});
