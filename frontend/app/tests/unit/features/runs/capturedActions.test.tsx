import { act, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { copy } from '../../../../src/copy/catalogue.ts';
import { ownPath } from '../../../../src/core/read.ts';
import type {
  ResetLiveCommand,
  RunNowCommand,
  SaveLiveCommand,
} from '../../../../src/store/commands.ts';
import { selectRunsPage } from '../../../../src/store/selectors/runs.ts';
import { selectThisBattle } from '../../../../src/store/selectors/thisBattle.ts';
import { bootDashboard, e0Config, postBodies } from '../../../support/dataLayer.ts';
import { dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { renderPage } from '../dashboard/harness.tsx';

const PATH = 'routines.ad_gems.interval_sec';
type Kind = 'save' | 'reset' | 'reset all' | 'Run now';
const KINDS: readonly Kind[] = ['save', 'reset', 'reset all', 'Run now'];

/** Recorded override with a second applied leaf, so both reset forms are offered. */
function reading() {
  const body = dashboardPagesCase(
    'battle_actions_projection.a_live_control_changes_the_value_in_force',
  ).body;
  for (const key of ['overrides', 'applied_overrides', 'effective']) {
    const fields = ownPath(body, 'battle', key, 'routines', 'ad_gems') as Record<string, unknown>;
    fields['window_sec'] = 30;
  }
  return body;
}

function old(body: unknown) {
  const actions = ownPath(body, 'battle', 'runtime', 'actions') as Record<string, unknown>;
  actions['observed_at'] = Number(ownPath(body, 'battle', 'runtime', 'server_time')) - 2;
}

type Data = ReturnType<typeof bootDashboard>['data'];

function editor(data: Data) {
  const found = selectRunsPage(data.store.getSnapshot()).live.editor;
  if (found === null) throw new Error('missing live editor');
  return found;
}

function capture(data: Data, kind: Kind) {
  const live = editor(data);
  const command =
    kind === 'save'
      ? live.save.command
      : kind === 'reset'
        ? live.resets.find((item) => item.path === PATH)?.reset.command
        : kind === 'reset all'
          ? live.resetAll?.reset.command
          : selectThisBattle(data.store.getSnapshot()).actions?.find(
              (item) => item.token === 'ad_gems',
            )?.runNow.command;
  if (command === null || command === undefined) throw new Error(`missing ${kind} command`);
  const invoke = () => {
    if (kind === 'save') return data.commands.saveLive(command as SaveLiveCommand);
    if (kind === 'Run now') return data.commands.runNow(command as RunNowCommand);
    return data.commands.resetLive(command as ResetLiveCommand);
  };
  return { command, invoke };
}

function edit(data: Data, value = '00600.50') {
  data.commands.editDraft(editor(data).scope, PATH, value);
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

it.each(KINDS)(
  'Q2 captured %s is refused after current-to-old; the draft and visible reason survive',
  async (kind) => {
    let source = reading();
    window.location.hash = '#runs/live';
    const page = await renderPage({
      config: () => e0Config(true),
      before: (fake) => {
        fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
        fake.route('POST', /\/api\/operations\//u, () => 'hold');
      },
    });
    await page.advance(100);
    act(() => {
      edit(page.data);
    });
    const captured = capture(page.data, kind);
    source = reading();
    old(source);
    act(() => {
      page.data.commands.checkBattle();
    });
    await page.advance(100);
    const live = editor(page.data);
    expect(live.save).toMatchObject({ enabled: false, reason: 'action_report_old' });
    expect(live.resets.find((item) => item.path === PATH)?.reset).toMatchObject({
      enabled: false,
      reason: 'action_report_old',
    });
    expect(live.resetAll?.reset).toMatchObject({ enabled: false, reason: 'action_report_old' });
    expect(
      selectThisBattle(page.data.store.getSnapshot()).actions?.find(
        (item) => item.token === 'ad_gems',
      )?.runNow,
    ).toMatchObject({ enabled: false, reason: 'action_report_old' });
    await act(captured.invoke);
    await page.advance(100);
    expect(postBodies(page.fake)).toEqual([]);
    expect(page.data.store.getSnapshot().notices.at(-1)).toMatchObject({
      kind: 'command_not_sent',
      reason: 'action_report_old',
    });
    const region = page.view.container.querySelector<HTMLElement>('[data-region="runsLive"]');
    if (region === null) throw new Error('missing Runs region');
    expect(within(region).getByRole('textbox', { name: 'Ad gems Interval' })).toHaveValue(
      '00600.50',
    );
    expect(
      within(region).getByRole('button', { name: 'Save changes to this battle' }),
    ).toHaveAccessibleDescription(copy.runs.reasons.action_report_old.short);
  },
);

it('F2 old-to-current retains raw routine typing and only an explicit Save sends its exact body once', async () => {
  let source = reading();
  old(source);
  window.location.hash = '#runs/live';
  const page = await renderPage({
    config: () => e0Config(true),
    newKey: () => 'captured-save',
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
      fake.route('POST', /live-controls$/u, () => 'hold');
    },
  });
  await page.advance(100);
  const input = page.view.getByRole('textbox', { name: 'Ad gems Interval' });
  await page.user.clear(input);
  await page.user.type(input, '00600.50');
  expect(input).toHaveValue('00600.50');
  expect(postBodies(page.fake)).toEqual([]);
  source = reading();
  act(() => {
    page.data.commands.checkBattle();
  });
  await page.advance(100);
  expect(input).toHaveValue('00600.50');
  expect(editor(page.data).save.enabled).toBe(true);
  expect(postBodies(page.fake)).toEqual([]);
  await page.user.click(page.view.getByRole('button', { name: 'Save changes to this battle' }));
  await page.advance(100);
  expect(postBodies(page.fake)).toEqual([
    {
      battle_id: ownPath(source, 'battle', 'id'),
      generation: ownPath(source, 'battle', 'generation'),
      expected_revision: 1,
      idempotency_key: 'captured-save',
      changes: { [PATH]: 600.5 },
    },
  ]);
});

async function boot(readBody: () => unknown = reading) {
  let source = readBody();
  const page = bootDashboard({
    config: () => e0Config(true),
    before: (fake) => {
      fake.route('GET', '/api/battles/active', () => ({ status: 200, body: source }));
      fake.route('POST', /\/api\/operations\//u, () => 'hold');
    },
  });
  await vi.advanceTimersByTimeAsync(100);
  // Live reads are only polled while their page is open.
  page.data.commands.checkBattle();
  await vi.advanceTimersByTimeAsync(100);
  edit(page.data);
  return {
    ...page,
    change: async (mutate: (body: unknown) => void) => {
      source = readBody();
      mutate(source);
      page.data.commands.checkBattle();
      await vi.advanceTimersByTimeAsync(100);
    },
  };
}

it.each(['ad_gems', 'floating_gems'])(
  'captured %s Run now refuses old readings',
  async (action) => {
    const page = await boot();
    try {
      const button = selectThisBattle(page.data.store.getSnapshot()).actions?.find(
        (item) => item.token === action,
      )?.runNow;
      expect(button?.enabled).toBe(true);
      const command = button?.command;
      if (command === null || command === undefined) throw new Error('missing Run now command');
      await page.change(old);
      await page.data.commands.runNow(command);
      expect(postBodies(page.fake)).toEqual([]);
      expect(page.data.store.getSnapshot().notices.at(-1)?.reason).toBe('action_report_old');
    } finally {
      page.data.stop();
    }
  },
);

it('the shopping row Run now retains Try once queue eligibility and refuses a captured command', async () => {
  const page = await boot(
    () =>
      dashboardPagesCase('battle_actions_projection.idle_ad_and_floating_gems_due_shopping_waiting')
        .body,
  );
  try {
    const vm = selectThisBattle(page.data.store.getSnapshot());
    const button = vm.actions?.find((item) => item.token === 'shopping')?.runNow;
    expect(button?.enabled).toBe(true);
    expect(vm.tryOnce.find((item) => item.action === 'shopping')?.button.enabled).toBe(true);
    const command = button?.command;
    if (command === null || command === undefined) throw new Error('missing shopping Run now');
    // Test-local: an unreadable queue closes the existing Try once admission gate.
    await page.change((body) => {
      const effective = ownPath(body, 'battle', 'effective') as Record<string, unknown>;
      effective['shopping_plan'] = null;
    });
    expect(
      selectThisBattle(page.data.store.getSnapshot()).tryOnce.find(
        (item) => item.action === 'shopping',
      )?.button.enabled,
    ).toBe(false);
    await page.data.commands.runNow(command);
    expect(postBodies(page.fake)).toEqual([]);
    await page.change(() => {});
    await page.data.commands.runNow(command);
    expect(postBodies(page.fake)).toEqual([expect.objectContaining({ action: 'shopping' })]);
  } finally {
    page.data.stop();
  }
});

it.each(KINDS)(
  'a current captured %s sends once, then cannot race its pending write',
  async (kind) => {
    const page = await boot();
    try {
      const captured = capture(page.data, kind);
      await captured.invoke();
      await captured.invoke();
      expect(postBodies(page.fake)).toHaveLength(1);
    } finally {
      page.data.stop();
    }
  },
);

it.each(['00700.25', 'not-a-number', '900'])(
  'a captured Save reserves its original body while newer typing %s survives',
  async (newer) => {
    const page = await boot();
    try {
      const captured = capture(page.data, 'save');
      edit(page.data, newer);
      await captured.invoke();
      expect(postBodies(page.fake)).toEqual([
        expect.objectContaining({ changes: { [PATH]: 600.5 } }),
      ]);
      expect(page.data.store.getSnapshot().runDrafts[editor(page.data).scope]).toEqual({
        [PATH]: newer,
      });
    } finally {
      page.data.stop();
    }
  },
);

it.each(
  KINDS.flatMap((kind) =>
    ['id', 'operation_id', 'generation', 'revision'].map((field) => ({ kind, field })),
  ),
)('captured $kind refuses changed target $field', async ({ kind, field }) => {
  const page = await boot();
  try {
    const captured = capture(page.data, kind);
    await page.change((body) => {
      const battle = ownPath(body, 'battle') as Record<string, unknown>;
      if (field === 'revision') {
        battle['control_revision'] = 2;
        battle['applied_revision'] = 2;
      } else battle[field] = `${String(battle[field])}-changed`;
    });
    await captured.invoke();
    expect(postBodies(page.fake)).toEqual([]);
  } finally {
    page.data.stop();
  }
});

it.each(KINDS)('captured %s refuses a now-ineligible path or action', async (kind) => {
  const page = await boot();
  try {
    const captured = capture(page.data, kind);
    await page.change((body) => {
      if (kind === 'save') {
        const caps = ownPath(body, 'battle', 'capabilities') as Record<string, unknown>;
        caps['controls'] = (caps['controls'] as string[]).filter((path) => path !== PATH);
      } else if (kind === 'Run now') {
        const caps = ownPath(body, 'battle', 'capabilities') as Record<string, unknown>;
        caps['actions'] = [];
      } else {
        const items = ownPath(body, 'battle', 'runtime', 'actions', 'items') as Record<
          string,
          unknown
        >[];
        const ad = items.find((item) => item['key'] === 'ad_gems');
        const fields = ownPath(ad, 'descriptor', 'fields') as Record<string, unknown>[];
        const interval = fields.find((field) => field['path'] === 'interval_sec');
        if (interval === undefined) throw new Error('missing interval descriptor');
        interval['nullable'] = false;
      }
    });
    await captured.invoke();
    expect(postBodies(page.fake)).toEqual([]);
  } finally {
    page.data.stop();
  }
});
