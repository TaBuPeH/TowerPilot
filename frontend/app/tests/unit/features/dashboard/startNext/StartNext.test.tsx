import { fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, expect, it, vi } from 'vitest';
import { own, ownPath } from '../../../../../src/core/read.ts';
import { StartNext } from '../../../../../src/features/dashboard/startNext/StartNext.tsx';
import { reduceDashboard } from '../../../../../src/store/dashboardStore.ts';
import { selectStartNext } from '../../../../../src/store/selectors/startNext.ts';
import type { DashboardState } from '../../../../../src/store/types.ts';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { addBody, addCase, addFailure, regionState } from '../../../store/regionHelpers.ts';
import { dashboardBody, dashboardCaseNames } from '../../../../support/dashboardFixture.ts';
import { e0Config, postBodies } from '../../../../support/dataLayer.ts';
import { sample, statusBody } from '../../../../support/fixture.ts';
import { renderPage, renderRegion } from '../harness.tsx';

function launchState(runCase = 'ready'): DashboardState {
  const guards = dashboardBody('status.guards_clear');
  return addBody(addCase(regionState(), 'runs', `runs.${runCase}`), 'status', {
    ...statusBody(sample('status.off')),
    setup_battle_guard: own(guards, 'setup_battle_guard'),
    collection_guard: own(guards, 'collection_guard'),
  });
}

function selected(state: DashboardState, run = 'bp_farm'): DashboardState {
  return reduceDashboard(state, { type: 'select_run', run });
}

async function audit(container: HTMLElement) {
  expect(textOutsideCatalogue(container)).toEqual([]);
  expect(await axe(container)).toHaveNoViolations();
}

afterEach(() => {
  vi.useRealTimers();
});

it('F-037: lists defined runs with a visible native picker; changes call only selectRun', async () => {
  const state = launchState('defined_and_undefined_with_labels');
  const { view, commands, setState } = renderRegion(state, <StartNext />);
  const picker = view.getByRole('combobox', { name: 'Run to start' });
  expect(picker.tagName).toBe('SELECT');
  expect(within(picker).getAllByRole('option')).toHaveLength(2);
  expect(view.queryByRole('option', { name: /Quest/ })).toBeNull();
  fireEvent.change(picker, { target: { value: 'farm' } });
  expect(commands.selectRun).toHaveBeenCalledExactlyOnceWith('farm');
  for (const [name, command] of Object.entries(commands)) {
    if (name !== 'selectRun') expect(command).not.toHaveBeenCalled();
  }
  const chosen = selected(state, 'farm');
  setState(chosen);
  picker.focus();
  setState(addBody(chosen, 'status', statusBody(sample('status.off'))));
  expect(picker).toHaveValue('farm');
  expect(picker).toHaveFocus();
  await audit(view.container);
});

it.each([
  [
    'ready default',
    { bp_farm: { ready: true }, bp_coin_default: { ready: true } },
    'bp_coin_default',
  ],
  ['first ready', { bp_farm: { ready: true }, bp_coin_default: { ready: false } }, 'bp_farm'],
  ['first defined', {}, 'bp_farm'],
] as const)(
  'F-037: chooses %s and keeps malformed readiness unknown',
  async (_name, readiness, expected) => {
    const body = dashboardBody('runs.ready');
    const run = ownPath(body, 'runs', 'bp_farm');
    const state = addBody(launchState(), 'runs', {
      runs: { bp_farm: run, bp_coin_default: run },
      readiness,
    });
    const { view } = renderRegion(state, <StartNext />);
    expect(view.getByRole('combobox')).toHaveValue(expected);
    if (_name === 'first defined')
      expect(view.getAllByText('readiness unknown').length).toBeGreaterThan(0);
    await audit(view.container);
  },
);

it.each(dashboardCaseNames('status'))(
  'F-038: status.%s keeps guard reasons and button permissions separate',
  async (name) => {
    const recorded = dashboardBody(`status.${name}`);
    const state = addBody(
      addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off'),
      'status',
      {
        ...(typeof recorded === 'object' && recorded !== null ? recorded : {}),
        harvest: sample('status.off'),
      },
    );
    const { view, commands } = renderRegion(state, <StartNext />);
    const vm = selectStartNext(state);
    for (const [action, label] of [
      ['start', 'Start playing'],
      ['collect', 'Collect gems only'],
    ] as const) {
      const button = view.getByRole('button', { name: label });
      const guard = own(recorded, action === 'start' ? 'setup_battle_guard' : 'collection_guard');
      expect(button.getAttribute('aria-disabled') === 'true').toBe(
        !vm[action].enabled || vm.launchBlocker !== null,
      );
      if (own(guard, 'blocks_launch') !== false)
        expect(button).toHaveAttribute('aria-disabled', 'true');
      if (!vm[action].enabled || vm.launchBlocker !== null) {
        const reasonId = button.getAttribute('aria-describedby');
        expect(reasonId).not.toBeNull();
        const reason = document.getElementById(reasonId ?? '');
        expect(reason).toBeVisible();
        // One reason shared by both buttons is said once, naming both.
        expect(reason?.textContent).toMatch(
          new RegExp(`^(${label}|Start playing and Collect gems only):`, 'u'),
        );
        expect(reason).toHaveTextContent('Blocked');
        if (typeof own(guard, 'code') === 'string')
          expect(reason).toHaveAttribute('data-guard-code', own(guard, 'code'));
        fireEvent.click(button);
        expect(commands[action]).not.toHaveBeenCalled();
      }
    }
    expect(view.container).not.toHaveTextContent('Start and Collect are off');
    await audit(view.container);
  },
);

it('F-038: released collection guard allows Collect while setup still blocks Start', async () => {
  const raw = dashboardBody('status.guards_setup_blocking_collection_released_by_handoff');
  const state = addBody(
    addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off'),
    'status',
    {
      ...(typeof raw === 'object' && raw !== null ? raw : {}),
      harvest: sample('status.off'),
    },
  );
  const { view, commands } = renderRegion(state, <StartNext />);
  expect(view.getByRole('button', { name: 'Start playing' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  fireEvent.click(view.getByRole('button', { name: 'Collect gems only' }));
  expect(commands.collect).toHaveBeenCalledExactlyOnceWith({
    preset: 'bp_farm',
    epoch: state.epoch,
  });
  expect(commands.start).not.toHaveBeenCalled();
  await audit(view.container);
});

it.each([
  ['not_ready_image_row', '1 screen images missing', 'exit battle', true],
  ['not_ready_config_row', 'Not ready', 'rois.wall_bar', false],
  [
    'not_ready_module_restoration',
    'Not ready',
    'Restore the original modules: return to Home and resume calibration',
    false,
  ],
  [
    'readiness_check_raised',
    "readiness couldn't be checked",
    'readiness check failed: the run could not be checked',
    false,
  ],
  [
    'setup_required_apply_scan',
    'Needs setup',
    'Your menu scan is complete. Save its discoveries to this account.',
    false,
  ],
  [
    'setup_required_battle_setup',
    'Needs setup',
    'Menu setup is complete. This run still needs battle controls and abilities verified.',
    false,
  ],
  [
    'setup_required_edit_run',
    'Needs setup',
    'Choose equipment available on this account for this run.',
    false,
  ],
] as const)(
  'F-039: %s shows the right readiness details and learning path',
  async (name, line, detail, hasImages) => {
    const { view, setState } = renderRegion(selected(launchState(name)), <StartNext />);
    const block = view.container.querySelector('.tp-start-next__readiness');
    expect(block).not.toBeNull();
    if (!(block instanceof HTMLElement)) throw new Error('Readiness block missing');
    expect(within(block).getByText(line)).toBeVisible();
    const disclosure = block.querySelector('summary');
    if (disclosure !== null) {
      await userEvent.click(disclosure);
      setState(selected(launchState(name)));
      expect(block.querySelector('details')).toHaveAttribute('open');
    }
    expect(within(block).getByText(detail)).toBeVisible();
    expect(within(block).getByText(detail).closest('[data-server-text]')).not.toBeNull();
    expect(block).not.toHaveTextContent('0 screen images missing');
    if (name === 'not_ready_config_row') expect(block).toHaveTextContent('Setting:');
    if (name === 'readiness_check_raised') expect(block).toHaveTextContent('Unknown');
    if (hasImages)
      expect(view.getByRole('link', { name: 'Learn missing screens' })).toHaveAttribute(
        'href',
        '#recognition?run=bp_farm',
      );
    else expect(view.queryByRole('link', { name: 'Learn missing screens' })).toBeNull();
    expect(
      view.getByRole('link', { name: 'Prepare runs on the Recognition page' }),
    ).toHaveAttribute('href', '#recognition?run=bp_farm');
    await audit(view.container);
  },
);

it('F-040: the sole primary Start sends the selected fenced command exactly once', async () => {
  const state = selected(launchState('not_ready_image_row'));
  const { view, commands } = renderRegion(state, <StartNext />);
  const button = view.getByRole('button', { name: 'Start playing' });
  expect(view.container.querySelectorAll('[data-tier="primary"]')).toHaveLength(1);
  expect(button).not.toHaveAttribute('aria-disabled');
  fireEvent.click(button);
  expect(commands.start).toHaveBeenCalledExactlyOnceWith({ preset: 'bp_farm', epoch: state.epoch });
  expect(commands.collect).not.toHaveBeenCalled();
  await audit(view.container);
});

it.each(['input_off', 'connection_blocked'] as const)(
  'F-038: explicit %s blocks both buttons even with clear independent guards',
  async (blocker) => {
    let state = addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off');
    state = addBody(state, 'config', e0Config(blocker !== 'input_off'));
    if (blocker === 'connection_blocked') {
      const guards = dashboardBody('status.guards_clear');
      state = addBody(state, 'status', {
        ...statusBody(sample('status.off')),
        device_state: 'offline',
        connected: false,
        setup_battle_guard: own(guards, 'setup_battle_guard'),
        collection_guard: own(guards, 'collection_guard'),
      });
    }
    const { view, commands } = renderRegion(state, <StartNext />);
    const reason = blocker === 'input_off' ? 'Game input is off' : 'The emulator is not connected';
    expect(view.getAllByText(reason)).toHaveLength(1);
    for (const [action, label] of [
      ['start', 'Start playing'],
      ['collect', 'Collect gems only'],
    ] as const) {
      const button = view.getByRole('button', { name: label });
      expect(button).toHaveAttribute('aria-disabled', 'true');
      // Both blocked for the same reason: one line names both buttons and describes each.
      expect(button).toHaveAccessibleDescription(
        `Start playing and Collect gems only: Blocked ${reason}`,
      );
      fireEvent.click(button);
      expect(commands[action]).not.toHaveBeenCalled();
    }
    await audit(view.container);
  },
);

it.each(['processes_linked_collector', 'processes_empty'] as const)(
  'F-041: %s qualifies the collector transfer hint',
  async (name) => {
    const body = dashboardBody(`status.${name}`);
    const state = addBody(launchState(), 'status', {
      ...(typeof body === 'object' && body !== null ? body : {}),
      harvest: sample('status.off'),
    });
    const { view } = renderRegion(state, <StartNext />);
    const hint = 'Start stops the gem collector first; the battle keeps playing';
    if (name === 'processes_linked_collector') expect(view.getByText(hint)).toBeVisible();
    else expect(view.queryByText(hint)).toBeNull();
    await audit(view.container);
  },
);

it('F-041: a collector record without a listed process remains qualified', async () => {
  const state = addBody(launchState(), 'status', statusBody(sample('status.collecting')));
  const { view } = renderRegion(state, <StartNext />);
  expect(
    view.getByText('A gem collector is still recorded; Start checks it before starting the run'),
  ).toBeVisible();
  expect(
    view.queryByText('Start stops the gem collector first; the battle keeps playing'),
  ).toBeNull();
  await audit(view.container);
});

it.each(dashboardCaseNames('run_controls'))(
  'F-042: run_controls.%s gates Collect and passes its exact command once',
  async (name) => {
    let state = addCase(launchState(), 'runControls', `run_controls.${name}`);
    if (name === 'combo_is_no_blueprint')
      state = selected(addCase(state, 'runs', 'runs.combo_entry'), 'combo');
    const { view, commands } = renderRegion(state, <StartNext />);
    const button = view.getByRole('button', { name: 'Collect gems only' });
    fireEvent.click(button);
    if (name === 'ad_gems_on_floating_off') {
      expect(button).not.toHaveAttribute('aria-disabled');
      expect(commands.collect).toHaveBeenCalledExactlyOnceWith({
        preset: 'bp_farm',
        epoch: state.epoch,
      });
    } else {
      expect(button).toHaveAttribute('aria-disabled', 'true');
      expect(commands.collect).not.toHaveBeenCalled();
    }
    if (name === 'combo_is_no_blueprint') expect(view.getByText('Pick a single run')).toBeVisible();
    expect(commands.start).not.toHaveBeenCalled();
    await audit(view.container);
  },
);

it('F-042: a failed saved preview closes Collect with its own visible reason', async () => {
  const state = addFailure(
    addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off'),
    'runControls',
    '/api/run-controls?blueprint=farm',
  );
  const { view, commands } = renderRegion(state, <StartNext />);
  fireEvent.click(view.getByRole('button', { name: 'Collect gems only' }));
  expect(view.getByText("Can't read this run's gem settings")).toBeVisible();
  expect(commands.collect).not.toHaveBeenCalled();
  await audit(view.container);
});

it.each(['preview_context_changed', 'status_stale', 'automation_recorded'] as const)(
  'F-042: %s prevents Collect without affecting the setup permit',
  async (scenario) => {
    let state = addCase(launchState(), 'runControls', 'run_controls.ad_gems_on_floating_off');
    if (scenario === 'preview_context_changed') {
      const preview = dashboardBody('run_controls.ad_gems_on_floating_off');
      state = addBody(
        state,
        'runControls',
        {
          ...(typeof preview === 'object' && preview !== null ? preview : {}),
          context: { instance: 'other', account: null, profile: 'mine' },
        },
        { path: '/api/run-controls?blueprint=farm' },
      );
    } else {
      const guards = dashboardBody('status.guards_clear');
      state = addBody(state, 'status', {
        ...statusBody(
          sample(scenario === 'automation_recorded' ? 'status.collecting' : 'status.off'),
          {
            ...(scenario === 'status_stale' ? { scan: { state: 'stale' } } : {}),
          },
        ),
        setup_battle_guard: own(guards, 'setup_battle_guard'),
        collection_guard: own(guards, 'collection_guard'),
      });
    }
    const { view, commands } = renderRegion(state, <StartNext />);
    const collect = view.getByRole('button', { name: 'Collect gems only' });
    expect(collect).toHaveAttribute('aria-disabled', 'true');
    expect(view.getByRole('button', { name: 'Start playing' })).not.toHaveAttribute(
      'aria-disabled',
    );
    fireEvent.click(collect);
    expect(commands.collect).not.toHaveBeenCalled();
    await audit(view.container);
  },
);

it.each([
  'ready_with_context',
  'not_ready_without_message',
  'context_other_than_the_page',
] as const)(
  'F-040: page readiness.%s permits at most one control POST and polls send none',
  async (readinessCase) => {
    vi.useFakeTimers();
    const guards = dashboardBody('status.guards_clear');
    const page = await renderPage({
      config: () => e0Config(true),
      status: () => ({
        ...statusBody(sample('status.off')),
        setup_battle_guard: own(guards, 'setup_battle_guard'),
        collection_guard: own(guards, 'collection_guard'),
      }),
      before: (fake) => {
        fake.route('GET', '/api/readiness?preset=bp_farm', () => ({
          status: 200,
          body: dashboardBody(`readiness.${readinessCase}`),
        }));
        fake.route('POST', '/api/control', () => ({
          status: 200,
          body: dashboardBody('control.start_accepted'),
        }));
      },
    });
    try {
      await page.advance(5000);
      expect(page.fake.writes()).toEqual([]);
      await page.user.click(page.view.getByRole('button', { name: 'Start playing' }));
      await page.advance();
      expect(postBodies(page.fake, '/api/control')).toEqual(
        readinessCase === 'ready_with_context'
          ? [
              {
                action: 'start',
                preset: 'bp_farm',
                expected_context: {
                  instance: 'main',
                  account: null,
                  serial: 'fake:1',
                  rendering: null,
                  profile: 'mine',
                },
                idempotency_key: 'key-0001',
              },
            ]
          : [],
      );
      const readinessIndex = page.fake.requests.findIndex(
        (request) => request.path === '/api/readiness?preset=bp_farm',
      );
      const postIndex = page.fake.requests.findIndex((request) => request.path === '/api/control');
      expect(readinessIndex).toBeGreaterThanOrEqual(0);
      if (readinessCase === 'ready_with_context') expect(readinessIndex).toBeLessThan(postIndex);
      else expect(postIndex).toBe(-1);
      await page.advance(10000);
      expect(postBodies(page.fake, '/api/control')).toHaveLength(
        readinessCase === 'ready_with_context' ? 1 : 0,
      );
      page.data.stop();
      vi.useRealTimers();
      await audit(page.view.container);
    } finally {
      page.data.stop();
      vi.useRealTimers();
    }
  },
);
