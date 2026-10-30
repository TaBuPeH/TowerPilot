import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { syntheticCase } from '../../../support/synthetic-action-fixtures.mjs';
import { collectionEvidence, gemFlags, runGemReport } from '../../../../src/core/collection.ts';
import { gemPolicy, policyCollects } from '../../../../src/core/gemSource.ts';
import { historyCollection } from '../../../../src/core/history.ts';
import { overlayView } from '../../../../src/core/overlay.ts';
import { ownPath } from '../../../../src/core/read.ts';
import { readHarvest } from '../../../../src/core/readability.ts';
import { collectGate } from '../../../../src/core/readiness.ts';
import { collectionGuard } from '../../../../src/core/guards.ts';
import { unknownFact } from '../../../../src/core/facts.ts';
import { selectGemSlot } from '../../../../src/store/selectors/gemSlot.ts';
import { required } from '../../../support/required.ts';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import { statusBody } from '../../../support/fixture.ts';
import { renderPage } from '../dashboard/harness.tsx';

// Enabled world (r17b): a run that already collects, with both instances enabled.
const STATUS = 'synthetic_harvest_on_keeps_collection_gem_only';
const BATTLE = 'synthetic_harvest_on_battle_read';
const ACCEPTED = 'synthetic_harvest_on_accepted';
// Collector world (r17c): a standalone collector Harvest launched; harvest.operation is present.
const COLLECTOR = {
  status: 'synthetic_collector_running_routines_include_the_instances',
  battle: 'synthetic_collector_battle_read',
  accepted: 'synthetic_collector_harvest_on_accepted',
} as const;
// Disabled world (r17c): both instances saved off before the battle starts, harvesting not set.
const DISABLED = {
  pre: 'synthetic_disabled_status_before_harvest_on',
  before: 'synthetic_disabled_before_harvest_on',
  accepted: 'synthetic_disabled_harvest_on_accepted',
  targets: 'synthetic_disabled_harvest_on_targets_the_gem_routines_only',
  statusAfter: 'synthetic_disabled_status_after_harvest_on',
  after: 'synthetic_disabled_after_harvest_on',
} as const;
const TOKEN = String(ownPath(syntheticCase('synthetic_run_now_accepted').request.body, 'action'));
const ID = required(TOKEN.split('@')[0]);
/** Counterfactual: remove only this test action, keeping every gem value and clock identical. */
function withoutSynthetic(value: unknown): unknown {
  if (Array.isArray(value))
    return value
      .filter((entry) =>
        typeof entry === 'string' ? !entry.includes(ID) : ownPath(entry, 'id') !== ID,
      )
      .map(withoutSynthetic);
  if (typeof value !== 'object' || value === null) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !key.includes(ID))
      .map(([key, entry]) => [key, withoutSynthetic(entry)]),
  );
}

function s1(status: unknown, battle: unknown) {
  const read = readHarvest(ownPath(status, 'harvest'));
  if (read.kind !== 'known') throw new Error('B1 harvest must pass D-1');
  const routines = ownPath(battle, 'effective', 'routines');
  const policy = gemPolicy(routines);
  const guard = collectionGuard({ scope: 'collection', blocks_launch: false }, 1);
  if (guard.kind !== 'permit') throw new Error('Missing test permit');
  const context = ownPath(battle, 'context');
  const preview = { blueprint: 'farm', context, settings: { routines } };
  return {
    overlay: overlayView(read.value),
    collection: collectionEvidence(read.value),
    runGems: runGemReport(read.value),
    flags: gemFlags(routines),
    source: policy,
    collects: policyCollects(policy),
    readiness: collectGate(
      guard.permit,
      { kind: 'answered', sameContext: true, body: preview },
      context,
      'bp_farm',
      false,
      unknownFact('test_ownership'),
    ),
    history: historyCollection(battle),
  };
}

/** The reader outputs for one recorded status paired with one recorded battle read. */
function recorded(status: string, battle: string) {
  return s1(syntheticCase(status).body, ownPath(syntheticCase(battle).body, 'battle'));
}

/** The same outputs after removing only the synthetic action: gem values, clocks and ids stay. */
function recordedWithout(status: string, battle: string) {
  return s1(
    withoutSynthetic(syntheticCase(status).body),
    withoutSynthetic(ownPath(syntheticCase(battle).body, 'battle')),
  );
}

/** Routine keys of the synthetic instances inside a `routines` record. */
function syntheticKeys(routines: unknown): string[] {
  return Object.keys(routines as object).filter((key) => key.includes(ID));
}

function setEnabled(routines: unknown, key: string, enabled: boolean): void {
  Reflect.set(ownPath(routines, key) as object, 'enabled', enabled);
}

/**
 * Transport metadata the recorded GET /api/status body does not carry (a
 * connected device, a fresh scan, a permitting collection guard), as at startup.
 */
function live(statusBodyOrHarvest: unknown): Record<string, unknown> {
  return {
    ...statusBody(ownPath(statusBodyOrHarvest, 'harvest')),
    device_state: 'device',
    collection_guard: { scope: 'collection', blocks_launch: false },
  };
}

const GEM_ROUTINES = ['ad_gems', 'floating_gems'];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('AC7 D-6 reaches the recorded collector operation and rests on the gem routines alone', () => {
  const status = syntheticCase(COLLECTOR.status).body;
  const battleRead = syntheticCase(COLLECTOR.battle).body;
  const operation = ownPath(status, 'harvest', 'operation');
  const routines = ownPath(operation, 'routines');
  // The collector's operation really carries all four routines: both gem routines and both instances (enabled).
  expect(Object.keys(routines as object).sort()).toEqual(
    ['ad_gems', 'floating_gems', ...syntheticKeys(routines)].sort(),
  );
  expect(syntheticKeys(routines)).toHaveLength(2);
  for (const key of syntheticKeys(routines)) expect(ownPath(routines, key, 'enabled')).toBe(true);
  expect(ownPath(routines, 'ad_gems', 'enabled')).toBe(true);
  expect(ownPath(routines, 'floating_gems', 'enabled')).toBe(false);
  // One world: the operation's battle is the battle the read publishes.
  expect(ownPath(operation, 'battle', 'id')).toBe(ownPath(battleRead, 'battle', 'id'));
  expect(ownPath(operation, 'battle', 'generation')).toBe(
    ownPath(battleRead, 'battle', 'generation'),
  );

  const result = recorded(COLLECTOR.status, COLLECTOR.battle);
  // D-6 is reached (operation present, not no_operation) and counts the gem routines only.
  expect(result.collection).toEqual({
    kind: 'known',
    value: {
      operationId: ownPath(operation, 'id'),
      battle: {
        id: ownPath(operation, 'battle', 'id'),
        generation: ownPath(operation, 'battle', 'generation'),
        publishedAt: 1800000002,
        ageSec: 0,
      },
      adGems: true,
      floatingGems: false,
      startedByCollectOnly: false,
    },
    observedAt: 1800000002,
  });
  expect(result.flags).toEqual({ adGems: true, floatingGems: false });
  expect(result.source).toMatchObject({
    adGems: { enabled: true },
    floatingGems: { enabled: false },
  });
  expect(result.collects).toBe(true);
  expect(result.history).toMatchObject({
    state: 'sampled',
    startedAt: 1800000000,
    throughAt: 1800000002,
  });
  expect(result).toEqual(recordedWithout(COLLECTOR.status, COLLECTOR.battle));

  // Adversarial derivatives of the same recording, instances left on throughout.
  const derive = (ad: boolean, floating: boolean) => {
    const body = syntheticCase(COLLECTOR.status).body;
    const battle = ownPath(syntheticCase(COLLECTOR.battle).body, 'battle');
    const operationRoutines = ownPath(body, 'harvest', 'operation', 'routines');
    const effective = ownPath(battle, 'effective', 'routines');
    for (const target of [operationRoutines, effective]) {
      setEnabled(target, 'ad_gems', ad);
      setEnabled(target, 'floating_gems', floating);
      for (const key of syntheticKeys(target)) expect(ownPath(target, key, 'enabled')).toBe(true);
    }
    return { out: s1(body, battle), bare: s1(withoutSynthetic(body), withoutSynthetic(battle)) };
  };
  const bothOff = derive(false, false);
  expect(bothOff.out.collection).toEqual({ kind: 'unknown', reason: 'no_gem_routine_on' });
  expect(bothOff.out.flags).toEqual({ adGems: false, floatingGems: false });
  expect(bothOff.out.collects).toBe(false);
  expect(bothOff.out.readiness).toEqual({ enabled: false, reason: 'gem_routines_off' });
  expect(bothOff.out).toEqual(bothOff.bare);
  const floatingOnly = derive(false, true);
  expect(floatingOnly.out.collection).toMatchObject({
    kind: 'known',
    value: { adGems: false, floatingGems: true },
  });
  expect(floatingOnly.out).toEqual(floatingOnly.bare);
  const bothOn = derive(true, true);
  expect(bothOn.out.collection).toMatchObject({
    kind: 'known',
    value: { adGems: true, floatingGems: true },
  });
  expect(bothOn.out).toEqual(bothOn.bare);
});

it('AC7 collector receipt and battle read keep the same gem-only readings; the receipt stops before an operation', () => {
  const receipt = syntheticCase(COLLECTOR.accepted);
  const status = syntheticCase(COLLECTOR.status).body;
  expect(receipt.request).toEqual({
    method: 'POST',
    path: '/api/harvest',
    body: {
      enabled: true,
      expected_context: { account: null, instance: 'main', profile: 'mine', serial: 'fake:1' },
      expected_revision: 0,
      idempotency_key: 'turn-on-collector',
      preset: 'bp_farm',
    },
  });
  // The receipt is the moment before the collector's operation exists: harvest.operation is null, so D-6
  // stays unknown (no_operation); it is not made known here.
  expect(ownPath(receipt.body, 'harvest', 'operation')).toBeNull();
  expect(ownPath(receipt.body, 'harvest', 'state')).toBe('starting');
  expect(ownPath(receipt.body, 'desired', 'request_key')).toBe(
    ownPath(receipt.request.body, 'idempotency_key'),
  );
  const early = recorded(COLLECTOR.accepted, COLLECTOR.battle);
  expect(early.collection).toEqual({ kind: 'unknown', reason: 'no_operation' });
  expect(early.flags).toEqual({ adGems: true, floatingGems: false });
  expect(early.collects).toBe(true);
  expect(early).toEqual(recordedWithout(COLLECTOR.accepted, COLLECTOR.battle));
  // The battle read publishes the instances beside the gem routines; neither widens the gem reading.
  const battle = ownPath(syntheticCase(COLLECTOR.battle).body, 'battle');
  expect(syntheticKeys(ownPath(battle, 'effective', 'routines'))).toHaveLength(2);
  expect(syntheticKeys(ownPath(battle, 'runtime', 'routines'))).toHaveLength(2);
  expect(ownPath(status, 'harvest', 'operation', 'battle', 'id')).toBe(ownPath(battle, 'id'));
});

it('AC7 the recorded collector reaches the Gem harvesting slot as Collecting gems, unchanged by the instances', async () => {
  let status: unknown = live(syntheticCase(COLLECTOR.status).body);
  let active: unknown = syntheticCase(COLLECTOR.battle).body;
  const page = await renderPage({
    config: () => e0Config(true),
    status: () => status,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: active }));
    },
  });
  await page.advance(100);
  const slot = () =>
    required(page.view.getByRole('heading', { name: 'Gem harvesting' }).closest('section'));
  const withVM = selectGemSlot(page.data.store.getSnapshot());
  // G3a needs D-6 and its confirmation: the operation row, not the run-owner row.
  expect(withVM.seen).toMatchObject({ row: 'G3a', word: 'running', line: 'collecting_gems' });
  expect(page.view.getAllByRole('button', { name: 'Run now Synthetic probe' })).toHaveLength(2);
  const withText = slot().textContent;
  status = live(withoutSynthetic(syntheticCase(COLLECTOR.status).body));
  active = withoutSynthetic(syntheticCase(COLLECTOR.battle).body);
  act(() => {
    page.data.commands.checkStatus();
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectGemSlot(page.data.store.getSnapshot())).toEqual(withVM);
  expect(slot().textContent).toBe(withText);
  expect(page.fake.writes()).toEqual([]);
});

it('AC7 disabled world: keyboard Turn on sends the exact fenced request; the initially disabled instances stay disabled', async () => {
  const receipt = syntheticCase(DISABLED.accepted);
  const preBody = syntheticCase(DISABLED.pre).body;
  const before = syntheticCase(DISABLED.before).body;
  const context = ownPath(before, 'battle', 'context');
  // The recorded pre-enable world: harvesting unset at revision 0, both instances off everywhere.
  expect(ownPath(preBody, 'harvest', 'desired', 'choice')).toBe('unset');
  expect(ownPath(preBody, 'harvest', 'desired', 'revision')).toBe(0);
  for (const routines of [
    ownPath(preBody, 'harvest', 'owner', 'routines'),
    ownPath(before, 'battle', 'effective', 'routines'),
    ownPath(before, 'battle', 'runtime', 'routines'),
  ]) {
    expect(syntheticKeys(routines)).toHaveLength(2);
    for (const key of syntheticKeys(routines))
      expect(ownPath(routines, key, 'enabled')).toBe(false);
  }
  const instanceRows = (
    ownPath(before, 'battle', 'runtime', 'actions', 'items') as unknown[]
  ).filter((item) => ownPath(item, 'id') === ID);
  expect(instanceRows).toHaveLength(2);
  for (const row of instanceRows) expect(ownPath(row, 'enabled')).toBe(false);

  let status: unknown = live(preBody);
  let active: unknown = before;
  const page = await renderPage({
    config: () => e0Config(true),
    status: () => status,
    newKey: () => String(ownPath(receipt.request.body, 'idempotency_key')),
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: active }));
      fake.route('POST', receipt.request.path, () => receipt);
    },
  });
  await page.advance(100);
  const slot = () =>
    required(page.view.getByRole('heading', { name: 'Gem harvesting' }).closest('section'));
  const turnOn = within(slot()).getByRole('button', { name: 'Turn on' });
  const offered = selectGemSlot(page.data.store.getSnapshot());
  expect(offered.choice.word).toBe('not_set');
  expect(offered.turnOn).toMatchObject({
    shown: true,
    enabled: true,
    command: { preset: 'bp_farm', expectedRevision: 0 },
  });
  act(() => {
    turnOn.focus();
  });
  expect(turnOn).toHaveFocus();
  await page.user.keyboard('{Enter}');
  await page.advance(100);
  expect(page.fake.writes()).toHaveLength(1);
  expect(page.fake.writes()[0]).toMatchObject({
    method: receipt.request.method,
    path: receipt.request.path,
  });
  expect(postBodies(page.fake)).toEqual([receipt.request.body]);
  // The fence is the recorded world's: the pre-enable revision and the battle's own context.
  expect(receipt.request.body).toMatchObject({
    enabled: true,
    preset: 'bp_farm',
    expected_revision: ownPath(preBody, 'harvest', 'desired', 'revision'),
    expected_context: {
      account: ownPath(context, 'account'),
      instance: ownPath(context, 'instance'),
      profile: ownPath(context, 'profile'),
      serial: ownPath(context, 'serial'),
    },
  });

  // The overlay is requested: the recorded status names only the gem routines as targets.
  status = live(syntheticCase(DISABLED.targets).body);
  act(() => {
    page.data.commands.checkStatus();
  });
  await page.advance(100);
  const requested = selectGemSlot(page.data.store.getSnapshot());
  expect(requested.choice.word).toBe('on');
  expect(requested.overlay).toMatchObject({ kind: 'reported', state: 'requested', pending: true });
  expect(requested.overlay?.routines.map((row) => [row.routine, row.target])).toEqual([
    ['ad_gems', true],
    ['floating_gems', true],
  ]);

  // The overlay is applied: the battle and harvest.owner read after it.
  status = live(syntheticCase(DISABLED.statusAfter).body);
  active = syntheticCase(DISABLED.after).body;
  act(() => {
    page.data.commands.checkStatus();
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(within(slot()).queryByRole('button', { name: 'Turn on' })).toBeNull();
  const withVM = selectGemSlot(page.data.store.getSnapshot());
  expect(withVM.choice.word).toBe('on');
  expect(withVM.overlay).toMatchObject({ kind: 'reported', state: 'applied', applied: true });
  expect(withVM.overlay?.routines.map((row) => [row.routine, row.target])).toEqual([
    ['ad_gems', true],
    ['floating_gems', true],
  ]);
  expect(withVM.seen).toMatchObject({ row: 'G4a', line: 'run_collects_gems' });
  const withText = slot().textContent;
  const withControls = [...slot().querySelectorAll('button, select')].map(
    (element) => element.outerHTML,
  );

  const battle = ownPath(active, 'battle');
  const command = required((ownPath(battle, 'commands') as Record<string, unknown>[])[0]);
  expect(ownPath(battle, 'commands')).toHaveLength(1);
  expect(ownPath(command, 'payload')).toEqual({ routines: { floating_gems: { enabled: true } } });
  expect(ownPath(command, 'applied_paths')).toEqual(['routines.floating_gems.enabled']);
  expect(ownPath(command, 'state')).toBe('applied');
  expect(ownPath(command, 'request_keys')).toEqual([
    ownPath(syntheticCase(DISABLED.statusAfter).body, 'harvest', 'overlay', 'request_key'),
  ]);
  // Both instances stayed disabled in the battle, its published routines, its action rows and the owner.
  const afterStatus = syntheticCase(DISABLED.statusAfter).body;
  for (const routines of [
    ownPath(battle, 'effective', 'routines'),
    ownPath(battle, 'runtime', 'routines'),
    ownPath(afterStatus, 'harvest', 'owner', 'routines'),
  ]) {
    expect(syntheticKeys(routines)).toHaveLength(2);
    for (const key of syntheticKeys(routines))
      expect(ownPath(routines, key, 'enabled')).toBe(false);
    expect(ownPath(routines, 'ad_gems', 'enabled')).toBe(true);
    expect(ownPath(routines, 'floating_gems', 'enabled')).toBe(true);
  }
  const afterRows = (ownPath(battle, 'runtime', 'actions', 'items') as unknown[]).filter(
    (item) => ownPath(item, 'id') === ID,
  );
  expect(afterRows).toHaveLength(2);
  for (const row of afterRows) expect(ownPath(row, 'enabled')).toBe(false);
  expect(ownPath(afterStatus, 'harvest', 'owner', 'gems')).toEqual({
    ad_gems: true,
    basis: 'published_routines',
    floating_gems: true,
  });

  status = live(withoutSynthetic(afterStatus));
  active = withoutSynthetic(active);
  act(() => {
    page.data.commands.checkStatus();
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectGemSlot(page.data.store.getSnapshot())).toEqual(withVM);
  expect(slot().textContent).toBe(withText);
  expect(
    [...slot().querySelectorAll('button, select')].map((element) => element.outerHTML),
  ).toEqual(withControls);
  expect(postBodies(page.fake)).toEqual([receipt.request.body]);
});

it('AC7 disabled-world recordings give gem-only readings at every step, with and without the instances', () => {
  // D-6 reads harvest.operation, which is null in this world (a run owns the device); the gem reading is
  // the run-owner report (EV-4), reached here with known values. D-6 itself is covered by the collector world.
  const steps = [
    {
      name: 'pre-enable status',
      status: DISABLED.pre,
      battle: DISABLED.before,
      run: [true, false],
      flags: [true, false],
      overlay: { kind: 'none', state: null },
    },
    {
      name: 'receipt',
      status: DISABLED.accepted,
      battle: DISABLED.before,
      run: [true, false],
      flags: [true, false],
      overlay: { kind: 'reported', state: 'not_requested' },
    },
    {
      name: 'requested',
      status: DISABLED.targets,
      battle: DISABLED.before,
      run: [true, false],
      flags: [true, false],
      overlay: { kind: 'reported', state: 'requested' },
    },
    {
      name: 'applied',
      status: DISABLED.statusAfter,
      battle: DISABLED.after,
      run: [true, true],
      flags: [true, true],
      overlay: { kind: 'reported', state: 'applied' },
    },
  ];
  for (const step of steps) {
    const result = recorded(step.status, step.battle);
    const harvest = ownPath(syntheticCase(step.status).body, 'harvest');
    for (const key of syntheticKeys(ownPath(harvest, 'owner', 'routines')))
      expect(ownPath(harvest, 'owner', 'routines', key, 'enabled'), step.name).toBe(false);
    expect(ownPath(harvest, 'operation'), step.name).toBeNull();
    expect(result.collection, step.name).toEqual({ kind: 'unknown', reason: 'no_operation' });
    expect(result.runGems, step.name).toMatchObject({
      kind: 'known',
      value: { adGems: step.run[0], floatingGems: step.run[1] },
    });
    expect(result.flags, step.name).toEqual({ adGems: step.flags[0], floatingGems: step.flags[1] });
    expect(result.collects, step.name).toBe(true);
    expect(result.overlay, step.name).toMatchObject(step.overlay);
    expect(
      result.overlay.routines.map((row) => row.routine),
      step.name,
    ).toEqual(GEM_ROUTINES);
    expect(result, step.name).toEqual(recordedWithout(step.status, step.battle));
  }
  // The receipt skips ad_gems as unchanged and targets floating_gems alone: nothing names an instance.
  const receipt = recorded(DISABLED.accepted, DISABLED.before);
  expect(receipt.overlay.routines).toEqual([
    { routine: 'ad_gems', target: true, skipped: 'unchanged' },
    { routine: 'floating_gems', target: true, skipped: null },
  ]);
});

it('AC7 enabled world: recorded read-side outputs and the Gem slot stay unchanged without the instances', async () => {
  let status: unknown = live(syntheticCase(STATUS).body);
  let active: unknown = syntheticCase(BATTLE).body;
  const page = await renderPage({
    config: () => e0Config(true),
    status: () => status,
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: active }));
    },
  });
  await page.advance(100);
  const slot = () =>
    required(page.view.getByRole('heading', { name: 'Gem harvesting' }).closest('section'));
  expect(page.view.getAllByRole('button', { name: 'Run now Synthetic probe' })).toHaveLength(2);
  expect(selectGemSlot(page.data.store.getSnapshot()).choice.word).toBe('on');
  expect(within(slot()).queryByRole('button', { name: 'Turn on' })).toBeNull();
  const withVM = selectGemSlot(page.data.store.getSnapshot());
  const withText = slot().textContent;
  const withControls = [...slot().querySelectorAll('button, select')].map(
    (element) => element.outerHTML,
  );
  for (const name of [ACCEPTED, STATUS]) {
    const body = syntheticCase(name).body;
    const battle = ownPath(syntheticCase(BATTLE).body, 'battle');
    // This world is run-owned: harvest.operation is null, so D-6 stays no_operation (the collector world covers D-6).
    expect(ownPath(body, 'harvest', 'operation')).toBeNull();
    expect(s1(body, battle).collection).toEqual({ kind: 'unknown', reason: 'no_operation' });
    expect(s1(body, battle)).toEqual(s1(withoutSynthetic(body), withoutSynthetic(battle)));
    expect(s1(body, battle).overlay.routines.map((row) => row.routine)).toEqual(GEM_ROUTINES);
  }
  const battle = ownPath(active, 'battle');
  const command = required((ownPath(battle, 'commands') as Record<string, unknown>[])[0]);
  expect(ownPath(command, 'payload')).toEqual({ routines: { floating_gems: { enabled: true } } });
  const acceptedOwner = ownPath(syntheticCase(ACCEPTED).body, 'harvest', 'owner', 'routines');
  const afterRoutines = ownPath(battle, 'runtime', 'routines');
  for (const key of syntheticKeys(acceptedOwner)) {
    expect(ownPath(afterRoutines, key, 'enabled')).toBe(ownPath(acceptedOwner, key, 'enabled'));
    expect(ownPath(battle, 'effective', 'routines', key, 'enabled')).toBe(true);
    expect(ownPath(command, 'applied_paths')).not.toContain(`routines.${key}.enabled`);
  }
  status = live(withoutSynthetic(syntheticCase(STATUS).body));
  active = withoutSynthetic(active);
  act(() => {
    page.data.commands.checkStatus();
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(selectGemSlot(page.data.store.getSnapshot())).toEqual(withVM);
  expect(slot().textContent).toBe(withText);
  expect(
    [...slot().querySelectorAll('button, select')].map((element) => element.outerHTML),
  ).toEqual(withControls);
  expect(page.fake.writes()).toEqual([]);
});

it('AC7 S1 only enabled synthetic routines never qualify as a gem source or Collect authority', () => {
  const active = syntheticCase(BATTLE).body;
  const battle = ownPath(active, 'battle');
  // Adversarial B1 derivative: both real gem routines off; both synthetic instances stay on.
  const routines = ownPath(battle, 'effective', 'routines') as Record<string, unknown>;
  for (const key of GEM_ROUTINES) Reflect.set(routines[key] as object, 'enabled', false);
  const result = s1(syntheticCase(STATUS).body, battle);
  expect(result.flags).toEqual({ adGems: false, floatingGems: false });
  expect(result.collects).toBe(false);
  expect(result.readiness).toEqual({ enabled: false, reason: 'gem_routines_off' });
  expect(result).toEqual(
    s1(withoutSynthetic(syntheticCase(STATUS).body), withoutSynthetic(battle)),
  );
});
