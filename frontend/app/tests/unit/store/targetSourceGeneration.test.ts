import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { contextFromConfig } from '../../../src/api/context.ts';
import { own, stringList } from '../../../src/core/read.ts';
import { reduceDashboard } from '../../../src/store/dashboardStore.ts';
import { editorSource, selectEditingTarget } from '../../../src/store/selectors/editingTarget.ts';
import { readTriggers, selectReadPlan } from '../../../src/store/selectors/readPlan.ts';
import { dashboardPagesCase } from '../../support/dashboardFixture.ts';
import { bootDashboard, flush } from '../../support/dataLayer.ts';
import { storeHarness } from '../../support/storeHarness.ts';

const recorded = (name: string) => dashboardPagesCase(`profile_target.${name}`);
const SOURCE = '/api/profile-src/second';
const stops: (() => void)[] = [];

async function open(held = true) {
  const page = bootDashboard({
    config: () => recorded('inactive_editor_context_before_save').body,
    before: (fake) => {
      fake.route('GET', '/api/profile-files', () =>
        recorded('same_account_inactive_target_listed'),
      );
      fake.route('GET', SOURCE, () =>
        held ? 'hold' : recorded('same_account_inactive_target_source'),
      );
    },
  });
  stops.push(page.data.stop);
  await flush();
  page.data.commands.openCollection(true);
  await flush();
  return page;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  vi.useRealTimers();
});

for (const via of ['mine', 'return', 'same name'] as const) {
  it(`PE2 C-03 held second read cannot feed a new selection via ${via} in the same epoch`, async () => {
    const { data, fake } = await open();
    const epoch = data.store.getSnapshot().epoch;
    data.commands.pickEditingTarget({ epoch, name: 'second' });
    const generation = selectEditingTarget(data.store.getSnapshot()).generation;
    const old = fake.next(SOURCE, 'GET');
    if (via === 'mine') data.commands.pickEditingTarget({ epoch, name: 'mine' });
    if (via === 'return') data.commands.returnEditingToActive({ epoch });
    data.commands.pickEditingTarget({ epoch, name: 'second' });
    expect(data.store.getSnapshot().epoch).toBe(epoch);
    expect(editorSource(data.store.getSnapshot())).toBeNull();
    old.respond(200, recorded('same_account_inactive_target_source').body);
    await flush();
    expect(editorSource(data.store.getSnapshot())).toBeNull();
    expect(data.store.getSnapshot().reads.targetSource.latest).toMatchObject({
      targetSelection: { name: 'second', generation },
    });
    fake.next(SOURCE, 'GET').respond(200, recorded('inactive_target_read_after_save').body);
    await flush();
    expect(editorSource(data.store.getSnapshot())).toMatchObject({
      targetSelection: {
        name: 'second',
        generation: selectEditingTarget(data.store.getSnapshot()).generation,
      },
      outcome: { body: recorded('inactive_target_read_after_save').body },
    });
    expect(fake.writes()).toEqual([]);
  });
}

it('PE2 C-03 Return clears cached target evidence and returning to second rereads before rendering', async () => {
  const { data, fake } = await open(false);
  const epoch = data.store.getSnapshot().epoch;
  data.commands.pickEditingTarget({ epoch, name: 'second' });
  await flush();
  expect(editorSource(data.store.getSnapshot())).not.toBeNull();
  fake.route('GET', SOURCE, () => 'hold');
  data.commands.returnEditingToActive({ epoch });
  expect(data.store.getSnapshot().reads.targetSource).toEqual({
    latest: null,
    lastAnswered: null,
    failures: 0,
  });
  data.commands.pickEditingTarget({ epoch, name: 'second' });
  expect(editorSource(data.store.getSnapshot())).toBeNull();
  fake.next(SOURCE, 'GET').respond(200, recorded('inactive_target_read_after_save').body);
  await flush();
  expect(editorSource(data.store.getSnapshot())?.outcome).toMatchObject({
    body: recorded('inactive_target_read_after_save').body,
  });
  expect(fake.requests.filter((read) => read.path === SOURCE)).toHaveLength(2);
  expect(fake.writes()).toEqual([]);
});

it('PE2 C-03 a same-name pick changes the target read plan generation and requests a new read', async () => {
  const { data } = await open(false);
  const epoch = data.store.getSnapshot().epoch;
  data.commands.pickEditingTarget({ epoch, name: 'second' });
  await flush();
  const before = data.store.getSnapshot();
  data.commands.pickEditingTarget({ epoch, name: 'second' });
  const next = data.store.getSnapshot();
  expect(selectReadPlan(next, 'targetSource')).not.toEqual(selectReadPlan(before, 'targetSource'));
  expect(readTriggers(before, next)).toContain('targetSource');
  expect(editorSource(next)).toBeNull();
});

it('PE2 C-03 an older selection read still settles only its original named patch and revision', () => {
  const context = contextFromConfig(recorded('inactive_editor_context_before_save').body);
  const h = storeHarness({ context, config: recorded('inactive_editor_context_before_save').body });
  h.read('profileFiles', recorded('same_account_inactive_target_listed').body);
  const request = recorded('inactive_run_entry_label_saved').request.body;
  const revision = own(request, 'expected_revision');
  const patchPath = stringList(own(request, 'path'));
  const afterRevision = own(recorded('inactive_target_read_after_save').body, 'revision');
  if (typeof revision !== 'string' || typeof afterRevision !== 'string' || patchPath === undefined)
    throw new Error('recorded patch path or revision missing');
  const meta = {
    name: 'second',
    baseRevision: revision,
    patchPath,
    patchValue: own(request, 'value'),
    patchRemove: false,
  };
  h.intent({
    id: 'original-second',
    kind: 'profile_patch',
    phase: 'unconfirmed',
    lostReason: 'network',
    lostSeq: h.tick(),
    meta,
  });
  h.intent({
    id: 'different-name',
    kind: 'profile_patch',
    phase: 'unconfirmed',
    lostReason: 'network',
    lostSeq: h.tick(),
    meta: { ...meta, name: 'another' },
  });
  h.intent({
    id: 'unchanged-revision',
    kind: 'profile_patch',
    phase: 'unconfirmed',
    lostReason: 'network',
    lostSeq: h.tick(),
    meta: { ...meta, baseRevision: afterRevision },
  });
  let state = reduceDashboard(h.state(), { type: 'edit_target_pick', epoch: 0, name: 'second' });
  const generation = state.editTargetGeneration;
  const old = {
    ...h.read('targetSource', recorded('inactive_target_read_after_save').body),
    targetSelection: { name: 'second', generation },
  };
  state = reduceDashboard(state, { type: 'edit_target_return', epoch: 0 });
  state = reduceDashboard(state, { type: 'edit_target_pick', epoch: 0, name: 'second' });
  state = reduceDashboard(state, { type: 'read', read: old });
  expect(editorSource(state)).toBeNull();
  expect(state.readSettlements['original-second']?.settlement).toBe('applied');
  expect(state.readSettlements['different-name']).toBeUndefined();
  expect(state.readSettlements['unchanged-revision']).toBeUndefined();
});
