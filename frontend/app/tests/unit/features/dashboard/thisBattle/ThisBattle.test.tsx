import { readFileSync } from 'node:fs';
import { act, fireEvent, screen, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { expect, it, vi } from 'vitest';
import { list, ownPath, entries } from '../../../../../src/core/read.ts';
import { ThisBattle } from '../../../../../src/features/dashboard/thisBattle/ThisBattle.tsx';
import { reduceDashboard } from '../../../../../src/store/dashboardStore.ts';
import { selectThisBattle } from '../../../../../src/store/selectors/thisBattle.ts';
import type { DashboardState } from '../../../../../src/store/types.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { dashboardBody, dashboardCase } from '../../../../support/dashboardFixture.ts';
import {
  addBody,
  addCase,
  addFailure,
  intent,
  regionState,
  tick,
} from '../../../store/regionHelpers.ts';
import { factValue, renderRegion, type RegionHarness } from '../harness.tsx';

const runtime = 'runtime_ad_orbit_shopping';
function state(name = runtime): DashboardState {
  return addCase(regionState(), 'battlesActive', `battles_active.${name}`);
}
function changed(changes: readonly [readonly string[], unknown][], name = runtime) {
  const body = dashboardBody(`battles_active.${name}`);
  for (const [path, value] of changes) {
    const parent = ownPath(body, ...path.slice(0, -1));
    const key = path.at(-1);
    if (typeof parent !== 'object' || parent === null || key === undefined)
      throw new Error('Invalid fixture path');
    Reflect.set(parent, key, value);
  }
  return addBody(regionState(), 'battlesActive', body);
}
function row(h: RegionHarness, name: string) {
  const element = h.view
    .getByText(name, { selector: 'strong' })
    .closest<HTMLElement>('.tp-battle__routine');
  if (element === null) throw new Error('Missing routine row');
  return within(element);
}
async function check(h: RegionHarness) {
  expect(h.reportFault).not.toHaveBeenCalled();
  expect(textOutsideCatalogue(h.view.container)).toEqual([]);
  expect(await axe(h.view.container)).toHaveNoViolations();
}
function noCalls(h: RegionHarness) {
  for (const command of Object.values(h.commands)) expect(command).not.toHaveBeenCalled();
}
function withIntent(s: DashboardState, patch: Parameters<typeof intent>[1] = {}) {
  const command = selectThisBattle(s).routines[0]?.runNow.command;
  if (command === null || command === undefined) throw new Error('Missing fixture command');
  return reduceDashboard(s, { type: 'intents', records: [intent(s, { meta: command, ...patch })] });
}

it.each([
  [runtime, 'Running', 'Up to date'],
  ['prepared', 'Waiting', 'prepared; waiting to observe the battle'],
  ['stale_report', 'Unknown', 'old report, 3 m ago'],
  [
    'commands_controls_requested_action_requested',
    'Waiting',
    'waiting for the automation to apply a change',
  ],
])('F-049: header reports scope, run and report evidence for %s', async (name, chip, line) => {
  const h = renderRegion(state(name), <ThisBattle />);
  const end = h.view.container.querySelector<HTMLElement>('.tp-region__end');
  if (end === null) throw new Error('Missing battle header');
  expect(factValue(end, 'Run')).toHaveTextContent('Farm');
  expect(h.view.getByRole('heading', { name: 'This battle' })).toBeVisible();
  expect(end).not.toHaveTextContent('this battle only');
  expect(end).toHaveTextContent(chip);
  expect(end).toHaveTextContent(line);
  expect(factValue(end, 'Wave')).toBeVisible();
  noCalls(h);
  await check(h);
});

it.each([
  'null_no_driver',
  'null_after_the_driver_ended_the_record',
  'null_after_reconcile_closed_record',
])('F-049: %s says only that no battle is tracked', async (name) => {
  const h = renderRegion(state(name), <ThisBattle />);
  expect(h.view.getByText('No tracked battle')).toBeVisible();
  expect(h.view.getByText('The game may still be in a battle')).toBeVisible();
  expect(h.view.queryByRole('button', { name: /^Run now .+/ })).not.toBeInTheDocument();
  expect(h.view.container.querySelector('.tp-chip')).toBeNull();
  expect(h.view.container).not.toHaveTextContent(/No battle running|battle ended/);
  await check(h);
});

it('F-049: waiting for first report and a failed read cannot claim Running', async () => {
  const initial = changed([[['battle', 'runtime'], null]]);
  const h = renderRegion(initial, <ThisBattle />);
  expect(h.view.getByText('waiting for first report')).toBeVisible();
  expect(h.view.container.querySelector('.tp-region__end')).toHaveTextContent('Waiting');
  h.setState(addFailure(tick(initial, 3), 'battlesActive'));
  expect(h.view.getByText('Battle: unknown')).toBeVisible();
  expect(h.view.getByText('last read 3 s ago')).toBeVisible();
  expect(h.view.container.querySelector('.tp-region__end')).not.toHaveTextContent('Running');
  noCalls(h);
  await check(h);
});

it.each(['replaced_generation', 'prepared'])(
  'F-049: %s drops the earlier battle action outcome',
  async (name) => {
    const initial = withIntent(state());
    const h = renderRegion(initial, <ThisBattle />);
    expect(
      h.view.getByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
    ).toBeVisible();
    expect(h.view.getByRole('status')).toHaveTextContent('No reply — outcome unknown');
    h.setState(addCase(initial, 'battlesActive', `battles_active.${name}`));
    expect(
      h.view.queryByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
    ).not.toBeInTheDocument();
    noCalls(h);
    await check(h);
  },
);

it('F-050: prepared settings are text with no countdown and disabled Run now', async () => {
  const h = renderRegion(state('prepared'), <ThisBattle />);
  expect(row(h, 'Ad gems').getByText('On when a wave starts')).toBeVisible();
  expect(row(h, 'Ad gems').getByText('every 12 m')).toBeVisible();
  expect(row(h, 'Floating gems').getByText('Off')).toBeVisible();
  for (const button of h.view.getAllByRole('button', { name: /^Run now .+/ })) {
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveAccessibleDescription('Run now needs an active tracked battle');
    fireEvent.click(button);
  }
  expect(h.view.queryByText(/^in \d/)).not.toBeInTheDocument();
  noCalls(h);
  await check(h);
});

it('F-055: countdown and ages advance with store ticks, independent of browser wall time', async () => {
  const initial = changed([
    [['battle', 'runtime', 'routines', 'floating_gems', 'next_due_at'], 1800000546],
  ]);
  const h = renderRegion(initial, <ThisBattle />);
  expect(row(h, 'Floating gems').getByText('in 9 m 04 s')).toBeVisible();
  const now = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 86400000);
  h.setState(tick(initial, 1));
  expect(row(h, 'Floating gems').getByText('in 9 m 03 s')).toBeVisible();
  now.mockRestore();
  h.setState(tick(initial, 16));
  expect(row(h, 'Floating gems').getByText('Old report')).toBeVisible();
  expect(row(h, 'Floating gems').queryByText(/^in \d/)).not.toBeInTheDocument();
  noCalls(h);
  await check(h);
});

it('F-055: a paused window names its time and the blocker reads as its word otherwise', async () => {
  const h = renderRegion(
    changed([[['battle', 'runtime', 'routines', 'ad_gems', 'enabled'], false]]),
    <ThisBattle />,
  );
  expect(row(h, 'Ad gems').getByText(/^Paused until /)).toBeVisible();
  h.setState(state('waiting_for_game_state_blocker'));
  expect(h.view.getAllByText('Waiting for the game screen').length).toBeGreaterThan(0);
  expect(h.view.queryByText('waiting_for_game_state')).toBeNull();
  await check(h);
});

it('F-055: reported observation states use selector codes and catalogue words', async () => {
  const h = renderRegion(
    changed([
      [['battle', 'runtime', 'routines', 'floating_gems', 'next_due_at'], null],
      [['battle', 'runtime', 'routines', 'floating_gems', 'state'], 'watching'],
    ]),
    <ThisBattle />,
  );
  expect(row(h, 'Floating gems').getByText('Watching')).toBeVisible();
  h.setState(
    changed([
      [['battle', 'runtime', 'routines', 'floating_gems', 'next_due_at'], null],
      [['battle', 'runtime', 'routines', 'floating_gems', 'state'], 'boundary_wait'],
    ]),
  );
  expect(row(h, 'Floating gems').getByText('Waiting for a run boundary')).toBeVisible();
  noCalls(h);
  await check(h);
});

it.each(['Ad gems', 'Floating gems', 'Shopping'])(
  'F-056: %s click passes exactly the VM command once; polls call nothing',
  async (name) => {
    const initial = state();
    const h = renderRegion(initial, <ThisBattle />);
    const selected = selectThisBattle(h.store.getSnapshot());
    const index = ['Ad gems', 'Floating gems', 'Shopping'].indexOf(name);
    const command = selected.routines[index]?.runNow.command;
    const button = row(h, name).getByRole('button', { name: `Run now ${name}` });
    expect(button).toHaveAttribute('data-tier', 'secondary');
    expect(button).not.toHaveAttribute('aria-disabled');
    fireEvent.click(button);
    expect(h.commands.runNow).toHaveBeenCalledExactlyOnceWith(command);
    button.focus();
    h.setState(addCase(tick(initial, 1), 'battlesActive', `battles_active.${runtime}`));
    expect(button).toHaveFocus();
    expect(h.commands.runNow).toHaveBeenCalledTimes(1);
    for (const [key, mock] of Object.entries(h.commands))
      if (key !== 'runNow') expect(mock).not.toHaveBeenCalled();
    await check(h);
  },
);

it.each([
  ['stale_report', 'Run now needs a fresh battle report'],
  ['commands_controls_requested_action_requested', 'Waiting for the automation to apply a change'],
])('F-056: %s visibly explains each disabled Run now', async (name, reason) => {
  const h = renderRegion(state(name), <ThisBattle />);
  const button = row(h, 'Ad gems').getByRole('button', { name: /^Run now .+/ });
  expect(button).toHaveAttribute('aria-disabled', 'true');
  expect(button).toHaveAccessibleDescription(reason);
  fireEvent.click(button);
  noCalls(h);
  await check(h);
});

it('F-056: an advertised open action blocks all three routines', async () => {
  const fixture = dashboardBody('battles_active.commands_controls_requested_action_requested');
  const h = renderRegion(
    changed([[['battle', 'commands'], ownPath(fixture, 'battle', 'commands')]]),
    <ThisBattle />,
  );
  for (const button of h.view.getAllByRole('button', { name: /^Run now .+/ })) {
    expect(button).toHaveAccessibleDescription('Another Run now is in progress');
    fireEvent.click(button);
  }
  noCalls(h);
  await check(h);
});

it('F-056: unsupported Shopping is named and disabled', async () => {
  const h = renderRegion(
    changed([
      [
        ['battle', 'capabilities', 'actions'],
        ['ad_gems', 'floating_gems'],
      ],
    ]),
    <ThisBattle />,
  );
  expect(row(h, 'Shopping').getAllByText('Not run by this automation').length).toBeGreaterThan(0);
  const button = row(h, 'Shopping').getByRole('button', { name: /^Run now .+/ });
  expect(button).toHaveAccessibleDescription(/Not run by this automation/);
  fireEvent.click(button);
  noCalls(h);
  await check(h);
});

it('F-057: confirmed claims, unconfirmed reason and missing results never invent a balance', async () => {
  const h = renderRegion(state(), <ThisBattle />);
  expect(row(h, 'Ad gems').getByText('Offer gone after tap')).toBeVisible();
  expect(row(h, 'Ad gems').getByText(/2 s ago/)).toBeVisible();
  expect(row(h, 'Floating gems').getByText('Unknown')).toBeVisible();
  fireEvent.click(row(h, 'Floating gems').getByText('Why'));
  expect(row(h, 'Floating gems').getByText('attempt_timeout')).toBeVisible();
  h.setState(
    changed([
      [['battle', 'runtime', 'routines', 'floating_gems', 'last_outcome', 'confirmed'], true],
      [['battle', 'runtime', 'routines', 'ad_gems', 'last_outcome'], null],
    ]),
  );
  expect(row(h, 'Floating gems').getByText('+2 confirmed')).toBeVisible();
  expect(row(h, 'Ad gems').getByText('No result yet')).toBeVisible();
  expect(h.view.container).not.toHaveTextContent(/gem total|balance/i);
  await check(h);
});

it.each([
  [
    'commands_controls_requested_action_requested',
    'sent; waiting for the automation to pick it up',
    'Waiting',
  ],
  ['commands_controls_pending_no_path_applied_action_started', 'waiting to apply', 'Waiting'],
  [
    'commands_controls_pending_no_path_applied_action_started',
    'started; waiting for its result',
    'Waiting',
  ],
  ['commands_controls_pending_some_paths_applied_action_completed', 'partly applied', 'Waiting'],
  ['commands_controls_applied_with_overrides_action_failed', 'Done', null],
  ['commands_controls_rejected_action_rejected', 'Command refused or failed', 'Failed'],
  ['commands_controls_expired_action_second_key_joined', 'expired', 'Failed'],
])('F-058: %s renders command %s by its wire state', async (name, line, chip) => {
  const h = renderRegion(state(name), <ThisBattle />);
  const commands = [...h.view.container.querySelectorAll('.tp-battle__command')];
  const command = commands.find((element) => element.textContent.includes(line));
  expect(command).toBeDefined();
  if (command === undefined) throw new Error('Missing command');
  if (chip === null) expect(command.querySelector('.tp-chip')).toBeNull();
  else expect(command).toHaveTextContent(chip);
  if (name.includes('overrides')) {
    expect(h.view.getByText('Changed for this battle')).toBeVisible();
    expect(h.view.getAllByRole('link', { name: 'Edit this battle in Runs' })[0]).toHaveAttribute(
      'href',
      '#runs/live',
    );
  }
  await check(h);
});

it('F-058: unknown command labels remain Unknown and completed actions show evidence', async () => {
  const source = dashboardBody(
    'battles_active.commands_controls_applied_with_overrides_action_failed',
  );
  const commands = ownPath(source, 'battle', 'commands');
  if (!Array.isArray(commands)) throw new Error('Missing fixture commands');
  const h = renderRegion(
    changed([
      [
        ['battle', 'commands'],
        [{ ...commands[0], state: 'Executing' }, commands[1]],
      ],
    ]),
    <ThisBattle />,
  );
  expect(h.view.getByText('command state not recognised').closest('li')).toHaveTextContent(
    'Unknown',
  );
  const completed = h.view.getByText('Done').closest('li');
  expect(completed).toHaveTextContent('Offer gone after tap');
  if (completed === null) throw new Error('Missing completed action');
  fireEvent.click(within(completed).getByText('Why'));
  expect(within(completed).getByText('offer_disappeared')).toBeVisible();
  await check(h);
});

it.each(['lost', '500'])(
  'F-059: %s stays Unknown, resends only on click and settles by its 200 receipt',
  async (kind) => {
    const answer = dashboardCase('battle_actions.admitted_then_the_readback_failed_unknown');
    const initial = withIntent(
      state(),
      kind === 'lost' ? {} : { answer: { status: answer.status, body: answer.body }, answerSeq: 2 },
    );
    const h = renderRegion(initial, <ThisBattle />);
    expect(
      h.view.getByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
    ).toBeVisible();
    expect(
      h.view
        .getByText('No reply — outcome unknown', { selector: '.tp-notice__line' })
        .closest('.tp-notice'),
    ).toHaveTextContent('Unknown');
    expect(
      row(h, 'Ad gems').getByRole('button', { name: /^Run now .+/ }),
    ).toHaveAccessibleDescription('An earlier Run now outcome is unknown');
    expect(h.view.getByRole('status')).toHaveTextContent('No reply — outcome unknown');
    const announcement = h.view.getByRole('status').firstChild;
    h.setState(addCase(tick(initial, 1), 'battlesActive', `battles_active.${runtime}`));
    expect(h.view.getByRole('status').firstChild).toBe(announcement);
    noCalls(h);
    fireEvent.click(h.view.getByRole('button', { name: 'Send same request again' }));
    expect(h.commands.resend).toHaveBeenCalledExactlyOnceWith('intent-1');
    expect(h.commands.runNow).not.toHaveBeenCalled();
    const receipt = dashboardCase('battle_actions.identical_replay_answered_from_the_record');
    h.setState(
      withIntent(state(), {
        phase: 'confirmed',
        answer: { status: 200, body: receipt.body },
        answerSeq: 20,
      }),
    );
    expect(
      h.view.queryByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
    ).not.toBeInTheDocument();
    expect(
      h.view
        .getByText('sent; waiting for the automation to pick it up', {
          selector: '.tp-battle__command > span',
        })
        .closest('li'),
    ).toHaveTextContent('Waiting');
    expect(
      h.view.queryByRole('button', { name: 'Send same request again' }),
    ).not.toBeInTheDocument();
    await check(h);
  },
);

it('F-059: unavailable read disables Resend, while Dismiss sends no action', async () => {
  const initial = withIntent(state());
  const h = renderRegion(addFailure(initial, 'battlesActive'), <ThisBattle />);
  const resend = h.view.getByRole('button', { name: 'Send same request again' });
  expect(resend).toHaveAccessibleDescription(
    'Send same request again needs an available battle read',
  );
  fireEvent.click(resend);
  noCalls(h);
  const dismiss = h.view.getByRole('button', { name: 'Dismiss' });
  expect(dismiss).toHaveAccessibleDescription('Dismiss (does not cancel the request)');
  fireEvent.click(dismiss);
  expect(h.commands.dismiss).toHaveBeenCalledExactlyOnceWith('intent-1');
  expect(h.commands.resend).not.toHaveBeenCalled();
  await check(h);
});

it('F-059: only a later command with the exact retained key settles the action', async () => {
  const initial = withIntent({ ...state(), contextConfirmed: true });
  const h = renderRegion(initial, <ThisBattle />);
  const body = dashboardBody(`battles_active.${runtime}`);
  const fixture = dashboardBody(
    'battles_active.commands_controls_applied_with_overrides_action_failed',
  );
  const source = list(ownPath(fixture, 'battle', 'commands'));
  if (source === undefined) throw new Error('Missing commands');
  const target = selectThisBattle(initial).routines[0]?.runNow.command;
  if (target === null || target === undefined) throw new Error('Missing target');
  const command = {
    ...Object.fromEntries(entries(source[1])),
    battle_id: target.battleId,
    generation: target.generation,
    operation_id: target.operationId,
    request_keys: ['unrelated-key'],
  };
  const battle = ownPath(body, 'battle');
  if (typeof battle !== 'object' || battle === null) throw new Error('Missing battle');
  Reflect.set(battle, 'commands', [command]);
  h.setState(addBody(tick(initial, 1), 'battlesActive', structuredClone(body)));
  expect(
    h.view.getByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
  ).toBeVisible();
  h.view.getByRole('button', { name: 'Send same request again' }).focus();
  command.request_keys = ['key-1'];
  h.setState(addBody(tick(initial, 2), 'battlesActive', structuredClone(body)));
  expect(
    h.view.queryByText('No reply — outcome unknown', { selector: '.tp-notice__line' }),
  ).not.toBeInTheDocument();
  expect(h.view.queryByRole('button', { name: 'Send same request again' })).not.toBeInTheDocument();
  expect(h.view.getByRole('heading', { name: 'This battle' })).toHaveFocus();
  expect(row(h, 'Ad gems').getByRole('button', { name: /^Run now .+/ })).not.toHaveAttribute(
    'aria-disabled',
  );
  expect(h.view.getByText('Done').closest('li')).toHaveTextContent('Offer gone after tap');
  noCalls(h);
  await check(h);
});

it('F-058: expired battle_ended means its record closed and keeps the reason under Why', async () => {
  const fixture = dashboardBody(
    'battles_active.commands_controls_expired_action_second_key_joined',
  );
  const commands = ownPath(fixture, 'battle', 'commands');
  if (!Array.isArray(commands)) throw new Error('Missing commands');
  const h = renderRegion(
    changed([[['battle', 'commands'], [{ ...commands[7], reason: 'battle_ended' }]]]),
    <ThisBattle />,
  );
  const line = h.view.getByText('expired: its battle record closed').closest('li');
  expect(line).toHaveTextContent('Failed');
  if (line === null) throw new Error('Missing expired command');
  fireEvent.click(within(line).getByText('Why'));
  expect(within(line).getByText('battle_ended')).toBeVisible();
  await check(h);
});

it.each([
  'unsupported_action_refused',
  'unknown_operation_refused',
  'changed_expected_revision_refused',
])('F-056: %s is a refusal that recorded nothing', async (name) => {
  const answer = dashboardCase(`battle_actions.${name}`);
  const h = renderRegion(
    withIntent(state(), { phase: 'refused', answer: { status: answer.status, body: answer.body } }),
    <ThisBattle />,
  );
  expect(
    h.view
      .getByText('Request refused — nothing recorded', { selector: '.tp-notice__line' })
      .closest('.tp-notice'),
  ).toHaveTextContent('Failed');
  expect(h.view.queryByRole('button', { name: 'Send same request again' })).not.toBeInTheDocument();
  await check(h);
});

it.each([null, 500])(
  'F-066: queue scope, rule, cadence and reserve %s name the correct editors',
  async (reserve) => {
    const h = renderRegion(
      changed([[['battle', 'effective', 'shopping_plan', 'cash_reserve'], reserve]]),
      <ThisBattle />,
    );
    expect(h.view.getByText('Queue for this battle')).toBeVisible();
    expect(h.view.getByText('Queue for this battle').closest('details')).not.toHaveAttribute(
      'open',
    );
    expect(factValue(h.view.container, 'Purchase rule')).toHaveTextContent(
      'Highest-priority affordable',
    );
    expect(factValue(h.view.container, 'Attempts per pass')).toHaveTextContent('One');
    expect(factValue(h.view.container, 'Cash reserve')).toHaveTextContent(
      reserve === null ? 'None' : '500',
    );
    for (const label of ['Purchase rule', 'Attempts per pass', 'Cash reserve'])
      expect(factValue(h.view.container, label)).toBeVisible();
    fireEvent.click(h.view.getByText('Queue for this battle'));
    expect(h.view.getByRole('link', { name: 'Edit this battle in Runs' })).toHaveAttribute(
      'href',
      '#runs/live',
    );
    expect(h.view.getByRole('link', { name: 'Saved settings in Runs' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^#runs\/saved\/[a-z_]+$/u),
    );
    expect(h.view.queryByRole('textbox')).not.toBeInTheDocument();
    await check(h);
  },
);

it('F-066: legacy shopping policy needs review and keeps both scoped links', async () => {
  const h = renderRegion(state('legacy_shopping_plan'), <ThisBattle />);
  expect(h.view.getByText('Shopping policy needs review')).toBeVisible();
  fireEvent.click(h.view.getByText('Queue for this battle'));
  expect(h.view.getAllByRole('link', { name: 'Edit this battle in Runs' }).length).toBeGreaterThan(
    0,
  );
  expect(h.view.getAllByRole('link', { name: 'Saved settings in Runs' }).length).toBeGreaterThan(0);
  await check(h);
});

it('F-067: first three names yield to the complete ordered queue, caps and weights; polls preserve open state', async () => {
  const initial = changed([
    [['battle', 'effective', 'shopping_plan', 'queue_mode'], 'weighted_split'],
    [
      ['battle', 'effective', 'shopping_plan', 'queue'],
      ['damage', 'health', 'attack_speed', 'range'].map((id, i) => ({
        id,
        enabled: true,
        tap_limit: i + 1,
        weight: i + 2,
      })),
    ],
  ]);
  const h = renderRegion(initial, <ThisBattle />);
  const summary = h.view.getByText('Queue for this battle').closest('summary');
  if (summary === null) throw new Error('Missing queue summary');
  expect(summary).toHaveTextContent('4');
  expect(summary.querySelectorAll('[data-server-text]')).toHaveLength(3);
  fireEvent.click(summary);
  const details = summary.closest('details');
  expect(details).toHaveAttribute('open');
  const items = h.view
    .getAllByRole('listitem')
    .filter((item) => item.parentElement?.tagName === 'OL');
  expect(items).toHaveLength(4);
  expect(items[0]).toHaveTextContent('Damage');
  expect(items[3]).toHaveTextContent('Range');
  expect(items[0]).toHaveTextContent('Tap cap: 1');
  expect(items[0]).toHaveTextContent('Weight: 2');
  const css = readFileSync('src/features/dashboard/thisBattle/thisBattle.css', 'utf8');
  expect(css).toMatch(
    /details\[open\]\s*>\s*summary\s+\.tp-disclosure__preview\s*\{\s*display:\s*none/u,
  );
  h.setState(tick(initial, 1));
  expect(summary.closest('details')).toBe(details);
  expect(details).toHaveAttribute('open');
  h.setState(
    addBody(
      tick(initial, 2),
      'battlesActive',
      initial.reads.battlesActive.latest?.outcome.kind === 'answered'
        ? initial.reads.battlesActive.latest.outcome.body
        : null,
    ),
  );
  expect(details).toBeInTheDocument();
  expect(details).toHaveAttribute('open');
  h.setState(state('replaced_generation'));
  const replacement = h.view.getByText('Queue for this battle').closest('details');
  expect(replacement).toBeInTheDocument();
  expect(replacement).not.toHaveAttribute('open');
  noCalls(h);
  await check(h);
});

it('F-067: empty queue says No upgrades selected', async () => {
  const h = renderRegion(
    changed([[['battle', 'effective', 'shopping_plan', 'queue'], []]]),
    <ThisBattle />,
  );
  fireEvent.click(h.view.getByText('Queue for this battle'));
  expect(h.view.getByText('No upgrades selected')).toBeVisible();
  await check(h);
});

it('F-068: shopper Why shows decision and confirmations as reported age without cash or price', async () => {
  const initial = changed([
    [
      ['battle', 'runtime', 'routines', 'shopping', 'last_outcome', 'decision'],
      { action: 'tap', reason: 'queue', upgrade: 'damage', cash: 12345, price: 777 },
    ],
    [
      ['battle', 'runtime', 'routines', 'shopping', 'last_outcome', 'pending_confirmation'],
      { upgrade: 'health', price: 888 },
    ],
    [
      ['battle', 'runtime', 'routines', 'shopping', 'last_outcome', 'last_confirmation'],
      { upgrade: 'range', outcome: 'confirmed', reason: 'verified', cash: 999 },
    ],
  ]);
  const h = renderRegion(initial, <ThisBattle />);
  const summary = h.view.container.querySelector('[data-focus-key="shopping-decision"]');
  if (summary === null) throw new Error('Missing shopping Why');
  fireEvent.click(summary);
  const detail = summary.closest('details');
  expect(detail).toHaveTextContent('Last decision');
  expect(detail).toHaveTextContent('reported 0 s ago');
  expect(detail).toHaveTextContent('tap');
  expect(detail).toHaveTextContent('Awaiting purchase confirmation');
  expect(detail).toHaveTextContent('verified');
  expect(summary.closest('.tp-battle__last')).toBeInTheDocument();
  expect(h.view.getByText('Queue for this battle').closest('.tp-battle__routine')).toBe(
    row(h, 'Shopping')
      .getByRole('button', { name: 'Run now Shopping' })
      .closest('.tp-battle__routine'),
  );
  expect(detail).not.toHaveTextContent(/cash|price|afford|12345|777|888|999/i);
  h.setState(tick(initial, 5));
  expect(detail).toHaveTextContent('reported 5 s ago');
  noCalls(h);
  await check(h);
});

it('F-058: completed Shopping projects sweep evidence and confirmed purchases through core', async () => {
  const source = dashboardBody(
    'battles_active.commands_controls_applied_with_overrides_action_failed',
  );
  const commands = list(ownPath(source, 'battle', 'commands'));
  if (commands === undefined) throw new Error('Missing commands');
  const command = {
    ...Object.fromEntries(entries(commands[1])),
    payload: { action: 'shopping' },
    evidence: { last_sweep: { confirmed_purchases: 3, reason: 'quantity_verified' } },
  };
  const h = renderRegion(changed([[['battle', 'commands'], [command]]]), <ThisBattle />);
  const line = h.view.getByText('Done').closest('li');
  expect(line).toHaveTextContent('3 purchases confirmed');
  if (line === null) throw new Error('Missing Shopping command');
  fireEvent.click(within(line).getByText('Why'));
  expect(within(line).getByText('quantity_verified')).toBeVisible();
  await check(h);
});

it('F-049: chip changes announce once while age ticks leave the live region unchanged', async () => {
  const initial = state();
  const h = renderRegion(initial, <ThisBattle />);
  expect(h.view.getByRole('status')).toBeEmptyDOMElement();
  h.setState(tick(initial, 16));
  expect(h.view.getByRole('status')).toHaveTextContent('Unknown');
  const message = h.view.getByRole('status').firstChild;
  h.setState(tick(initial, 17));
  expect(h.view.getByRole('status').firstChild).toBe(message);
  h.setState(addCase(tick(initial, 18), 'battlesActive', `battles_active.${runtime}`));
  expect(h.view.getByRole('status')).toHaveTextContent('Running');
  noCalls(h);
  await check(h);
});

it('F-059: outcome removal focuses next, previous and finally the region heading', async () => {
  let current = state();
  const records = selectThisBattle(current).routines.map((routine, index) =>
    intent(current, { id: `focus-${String(index)}`, meta: routine.runNow.command ?? {} }),
  );
  current = reduceDashboard(current, { type: 'intents', records });
  const h = renderRegion(current, <ThisBattle />);
  for (const [id, next] of [
    ['focus-1', 'focus-2'],
    ['focus-2', 'focus-0'],
    ['focus-0', null],
  ]) {
    const element = h.view.container.querySelector<HTMLElement>(`[data-battle-outcome="${id}"]`);
    if (element === null) throw new Error('Missing focused outcome');
    const button = within(element).getByRole('button', { name: 'Dismiss' });
    button.focus();
    fireEvent.click(button);
    current = reduceDashboard(current, {
      type: 'intents',
      records: current.intents.map((record) =>
        record.id === id ? { ...record, dismissed: true } : record,
      ),
    });
    h.setState(current);
    const target =
      next === null
        ? h.view.getByRole('heading', { name: 'This battle' })
        : h.view.container.querySelector(`[data-battle-outcome="${next}"] button`);
    expect(target).toHaveFocus();
  }
  expect(h.view.getByText('Dismissed')).toBeVisible();
  expect(h.commands.dismiss).toHaveBeenCalledTimes(3);
  expect(h.commands.runNow).not.toHaveBeenCalled();
  await check(h);
});

it('F-059: Dismissed stays for eight seconds and does not send an action', async () => {
  vi.useFakeTimers();
  const h = renderRegion(withIntent(state()), <ThisBattle />);
  fireEvent.click(h.view.getByRole('button', { name: 'Dismiss' }));
  act(() => {
    vi.advanceTimersByTime(7999);
  });
  expect(h.view.getByText('Dismissed')).toBeVisible();
  act(() => {
    vi.advanceTimersByTime(1);
  });
  expect(h.view.queryByText('Dismissed')).not.toBeInTheDocument();
  vi.useRealTimers();
  expect(h.commands.runNow).not.toHaveBeenCalled();
  await check(h);
});

it('F-069: unsupported reserve shows Blocked and both editors; quantity warning also stays visible', async () => {
  const h = renderRegion(state('legacy_shopping_plan'), <ThisBattle />);
  const line = h.view
    .getByText("This shopping policy doesn't protect your cash reserve")
    .closest<HTMLElement>('.tp-notice');
  expect(line).toHaveTextContent('Blocked');
  if (line === null) throw new Error('Missing warning');
  expect(within(line).getByRole('link', { name: 'Saved settings in Runs' })).toHaveAttribute(
    'href',
    expect.stringMatching(/^#runs\/saved\/[a-z_]+$/u),
  );
  expect(within(line).getByRole('link', { name: 'Edit this battle in Runs' })).toHaveAttribute(
    'href',
    '#runs/live',
  );
  expect(h.view.getByText('Reserve protection needs a verified purchase quantity')).toBeVisible();
  await check(h);
});

it.each([true, undefined])(
  'F-069: unverified purchase quantity independently shows Waiting with reserve_supported %s',
  async (supported) => {
    const h = renderRegion(
      changed([
        [
          ['battle', 'runtime', 'routines', 'shopping', 'last_outcome', 'reserve_supported'],
          supported,
        ],
        [
          [
            'battle',
            'runtime',
            'routines',
            'shopping',
            'last_outcome',
            'quantity_observation_supported',
          ],
          false,
        ],
      ]),
      <ThisBattle />,
    );
    const line = h.view
      .getByText('Reserve protection needs a verified purchase quantity')
      .closest<HTMLElement>('.tp-notice');
    expect(line).toHaveTextContent('Waiting');
    if (line === null) throw new Error('Missing quantity warning');
    fireEvent.click(within(line).getByRole('button', { name: 'About this message' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      "The shopper can't verify how many it bought yet, so it can't confirm your cash reserve is kept",
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(
      h.view.queryByText("This shopping policy doesn't protect your cash reserve"),
    ).not.toBeInTheDocument();
    await check(h);
  },
);
