import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  syntheticBefore,
  syntheticCase,
  syntheticStaleContext,
} from '../../../support/synthetic-action-fixtures.mjs';
import { copy } from '../../../../src/copy/catalogue.ts';
import { ownPath } from '../../../../src/core/read.ts';
import { selectRunsPage } from '../../../../src/store/selectors/runs.ts';
import { selectThisBattle } from '../../../../src/store/selectors/thisBattle.ts';
import { required } from '../../../support/required.ts';
import { e0Config, postBodies } from '../../../support/dataLayer.ts';
import { renderPage, type PageHarness } from '../dashboard/harness.tsx';

const ACTIVE = 'synthetic_active_two_instances';
const SAVE = 'synthetic_live_field_accepted';
const APPLIED = 'synthetic_live_field_read_applied';
const request = syntheticCase(SAVE).request;
const changes = ownPath(request.body, 'changes') as Record<string, number>;
const PATH = required(Object.keys(changes)[0]);
const TOKEN = required(PATH.split('.')[1]);

function action(page: PageHarness) {
  const found = selectThisBattle(page.data.store.getSnapshot()).actions?.find(
    (item) => item.token === TOKEN,
  );
  if (found === undefined) throw new Error('Missing synthetic action');
  return found;
}

function editor(page: PageHarness) {
  const found = selectRunsPage(page.data.store.getSnapshot()).live.editor;
  if (found === null) throw new Error('Missing live editor');
  return found;
}

function runCommandRow(page: PageHarness) {
  const found = [...page.view.container.querySelectorAll('.tp-battle__command')].find((item) =>
    item.textContent.includes(TOKEN),
  );
  if (found === undefined) throw new Error('Missing synthetic command row');
  return found;
}

async function openLive(page: PageHarness) {
  act(() => {
    window.location.hash = '#runs/live';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  });
  await page.advance(100);
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it('AC7 B1 descriptors render two Dashboard rows and the live field sends the exact fenced request, then readback applies it once', async () => {
  let source = syntheticCase(ACTIVE);
  const page = await renderPage({
    config: () => e0Config(true),
    newKey: () => String(ownPath(request.body, 'idempotency_key')),
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => source);
      fake.route('POST', request.path, () => syntheticCase(SAVE));
    },
  });
  await page.advance(100);
  const actions = selectThisBattle(page.data.store.getSnapshot()).actions;
  expect(
    actions?.filter((item) => item.token?.startsWith(required(TOKEN.split('@')[0]))),
  ).toHaveLength(2);
  const region = required(
    page.view.container.querySelector<HTMLElement>('[data-region="thisBattle"]'),
  );
  expect(within(region).getAllByRole('button', { name: 'Run now Synthetic probe' })).toHaveLength(
    2,
  );
  await openLive(page);
  const input = required(page.view.getAllByRole('textbox', { name: 'Synthetic probe Burst' })[0]);
  await page.user.clear(input);
  await page.user.type(input, String(changes[PATH]));
  expect(selectRunsPage(page.data.store.getSnapshot()).live.editor?.save.enabled).toBe(true);
  await page.user.click(page.view.getByRole('button', { name: 'Save changes to this battle' }));
  await page.advance(100);
  expect(postBodies(page.fake)).toEqual([request.body]);
  expect(page.fake.writes()[0]).toMatchObject({ method: request.method, path: request.path });
  expect(page.view.getByText('Sent to this battle - waiting to apply')).toBeInTheDocument();
  source = syntheticCase(APPLIED);
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(page.view.getByText('Applied to this battle', { exact: true })).toBeInTheDocument();
  expect(input).toHaveValue(String(changes[PATH]));
  await page.advance(10_000);
  expect(postBodies(page.fake)).toEqual([request.body]);
});

it('AC7 exact Run now request settles by its receipt, started read, and completed unconfirmed execution without resend', async () => {
  const write = syntheticCase('synthetic_run_now_accepted');
  let source = syntheticBefore('run');
  const page = await renderPage({
    config: () => e0Config(true),
    newKey: () => String(ownPath(write.request.body, 'idempotency_key')),
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => source);
      fake.route('POST', write.request.path, () => write);
    },
  });
  await page.advance(100);
  expect(action(page).runNow.enabled).toBe(true);
  await page.user.click(
    required(page.view.getAllByRole('button', { name: 'Run now Synthetic probe' })[0]),
  );
  await page.advance(100);
  expect(postBodies(page.fake)).toEqual([write.request.body]);
  expect(page.fake.writes()[0]).toMatchObject({ method: 'POST', path: write.request.path });
  expect(runCommandRow(page)).toHaveTextContent('Waiting');
  source = syntheticCase('synthetic_run_now_read_started');
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(runCommandRow(page)).toHaveTextContent('started; waiting for its result');
  source = syntheticCase('synthetic_run_now_read_completed_unconfirmed');
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(runCommandRow(page)).toHaveTextContent('Done');
  expect(runCommandRow(page)).toHaveTextContent('Finished, effect not confirmed');
  await page.advance(10_000);
  expect(postBodies(page.fake)).toEqual([write.request.body]);
});

it.each([
  ['stale context', syntheticStaleContext, 'action_context_mismatch'],
  ['newer schema', () => syntheticCase('synthetic_projection_newer_schema'), 'action_schema_newer'],
] as const)(
  'AC7 %s refuses visible sends and previously captured commands',
  async (_name, refused, reason) => {
    let source =
      reason === 'action_schema_newer'
        ? syntheticCase('synthetic_projection_newer_schema')
        : syntheticCase(ACTIVE);
    // Same recorded newer-schema battle, just before a driver upgrades its envelope.
    if (reason === 'action_schema_newer')
      Reflect.set(
        ownPath(source.body, 'battle', 'runtime', 'actions') as object,
        'schema_version',
        1,
      );
    const page = await renderPage({
      config: () => e0Config(true),
      before: (fake) => {
        fake.route('GET', '/api/battles/active', () => source);
      },
    });
    await page.advance(100);
    const run = action(page).runNow.command;
    expect(run).not.toBeNull();
    await openLive(page);
    const input = required(page.view.getAllByRole('textbox', { name: 'Synthetic probe Burst' })[0]);
    await page.user.clear(input);
    await page.user.type(input, '7');
    const save = editor(page).save.command;
    expect(save).not.toBeNull();
    source = refused();
    act(() => {
      page.data.commands.checkBattle();
    });
    await page.advance(100);
    expect(action(page).runNow).toMatchObject({ enabled: false, reason });
    expect(editor(page).save).toMatchObject({ enabled: false, reason });
    expect(
      page.view.getByRole('button', { name: 'Save changes to this battle' }),
    ).toHaveAccessibleDescription(copy.actions.blocked[reason].short);
    await page.user.type(input, '0');
    expect(input).toHaveValue('70');
    await act(() => page.data.commands.runNow(required(run)));
    expect(page.data.store.getSnapshot().notices.at(-1)).toMatchObject({
      kind: 'command_not_sent',
      reason,
    });
    await act(() => page.data.commands.saveLive(required(save)));
    expect(page.data.store.getSnapshot().notices.at(-1)).toMatchObject({
      kind: 'command_not_sent',
      reason,
    });
    expect(postBodies(page.fake)).toEqual([]);
  },
);

it.each(['unknown', 'lost'] as const)(
  'AC7 %s reply stays Unknown until the matching recorded read, and rejects a captured command',
  async (kind) => {
    const write = syntheticCase('synthetic_run_now_reply_unknown');
    let source = syntheticBefore('unknown');
    const page = await renderPage({
      config: () => e0Config(true),
      newKey: () => String(ownPath(write.request.body, 'idempotency_key')),
      before: (fake) => {
        fake.route('GET', '/api/battles/active', () => source);
        fake.route('POST', write.request.path, () => (kind === 'lost' ? 'hold' : write));
      },
    });
    await page.advance(100);
    const captured = action(page).runNow.command;
    await page.user.click(
      required(page.view.getAllByRole('button', { name: 'Run now Synthetic probe' })[0]),
    );
    if (kind === 'lost') page.fake.next(write.request.path).lose();
    await page.advance(100);
    expect(page.view.container.querySelector('[data-battle-outcome]')).toHaveTextContent(
      'No reply — outcome unknown',
    );
    expect(action(page).runNow.enabled).toBe(false);
    await act(() => page.data.commands.runNow(required(captured)));
    expect(page.data.store.getSnapshot().notices.at(-1)?.kind).toBe('command_not_sent');
    await page.advance(1000);
    expect(postBodies(page.fake)).toEqual([write.request.body]);
    source = syntheticCase('synthetic_run_now_reply_unknown_read');
    act(() => {
      page.data.commands.checkBattle();
    });
    await page.advance(100);
    expect(page.view.container.querySelector('[data-battle-outcome]')).toBeNull();
    expect(runCommandRow(page)).toHaveTextContent('Waiting');
    expect(postBodies(page.fake)).toEqual([write.request.body]);
  },
);
