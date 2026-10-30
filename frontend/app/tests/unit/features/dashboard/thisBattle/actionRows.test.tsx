import { within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { expect, it } from 'vitest';
import { ownPath } from '../../../../../src/core/read.ts';
import { ThisBattle } from '../../../../../src/features/dashboard/thisBattle/ThisBattle.tsx';
import { textOutsideCatalogue } from '../../../copy/renderedText.ts';
import { dashboardPagesCase } from '../../../../support/dashboardFixture.ts';
import { addBody, addCase, regionState } from '../../../store/regionHelpers.ts';
import { renderRegion, type RegionHarness } from '../harness.tsx';

const P = 'battle_actions_projection';

function render(name: string, edit?: (body: Record<string, unknown>) => void): RegionHarness {
  const body = dashboardPagesCase(`${P}.${name}`).body as Record<string, unknown>;
  edit?.(body);
  return renderRegion(
    addBody(regionState(), 'battlesActive', body, { path: '/api/battles/active' }),
    <ThisBattle />,
  );
}

function items(body: Record<string, unknown>): Record<string, unknown>[] {
  return ownPath(body, 'battle', 'runtime', 'actions', 'items') as Record<string, unknown>[];
}

function row(h: RegionHarness, name: string) {
  const element = h.view
    .getByText(name, { selector: 'strong bdi' })
    .closest<HTMLElement>('.tp-battle__routine');
  if (element === null) throw new Error(`Missing action row ${name}`);
  return within(element);
}

async function check(h: RegionHarness) {
  expect(h.reportFault).not.toHaveBeenCalled();
  expect(textOutsideCatalogue(h.view.container)).toEqual([]);
  expect(await axe(h.view.container)).toHaveNoViolations();
}

it('AC1 idle: one row per item named by its label, with the blocker, Due and the countdown', async () => {
  const h = render('idle_ad_and_floating_gems_due_shopping_waiting');
  const names = [...h.view.container.querySelectorAll('.tp-battle__name')].map(
    (n) => n.textContent,
  );
  expect(names).toEqual(['Ad gems', 'Floating gems', 'Shopping']);
  expect(row(h, 'Ad gems').getByText('Not seen on screen yet')).toBeInTheDocument();
  expect(row(h, 'Floating gems').getByText('Due')).toBeInTheDocument();
  expect(row(h, 'Shopping').getByText('in 18 s')).toBeInTheDocument();
  expect(row(h, 'Ad gems').getByText('On')).toBeInTheDocument();
  expect(row(h, 'Ad gems').getByText('Settings')).toBeInTheDocument();
  const settings = h.view.container.querySelector('.tp-battle__settings');
  expect(settings).toHaveTextContent('Interval720s');
  expect(settings).toHaveTextContent('Window60s');
  await check(h);
});

it('AC1 a finished execution shows its result, its effect, its age and its reason under Why', async () => {
  const h = render('a_finished_execution_is_the_last_of_its_instance');
  const ad = row(h, 'Ad gems');
  expect(ad.getByText('Succeeded')).toBeInTheDocument();
  expect(ad.getByText('Offer gone after tap')).toBeInTheDocument();
  expect(ad.queryByText('Effect confirmed')).toBeNull();
  expect(ad.getByText('2 s ago')).toBeInTheDocument();
  expect(ad.getByText('offer_disappeared')).toBeInTheDocument();
  expect(ad.queryByText('Claimed')).toBeNull();
  await check(h);
});

it('AC1 a failed unconfirmed execution reads finished with the effect not confirmed', async () => {
  const h = render('a_failed_unconfirmed_execution_is_the_last_of_its_instance');
  const ad = row(h, 'Ad gems');
  expect(ad.getByText('Failed')).toBeInTheDocument();
  expect(ad.getByText('Finished, effect not confirmed')).toBeInTheDocument();
  expect(ad.queryByText('Effect confirmed')).toBeNull();
  await check(h);
});

it('AC1 running shows its window end; Off shows no countdown', async () => {
  const running = render('ad_window_open_is_running');
  expect(row(running, 'Ad gems').getByText('Running, window ends in 18 s')).toBeInTheDocument();
  await check(running);
  running.view.unmount();
  const off = render('ad_gems_paused_by_a_control_is_disabled');
  const ad = row(off, 'Ad gems');
  expect(ad.getAllByText('Off')).toHaveLength(2);
  expect(ad.queryByText(/^in /u)).toBeNull();
  await check(off);
});

it('AC1 a test-local item with an unknown id renders from its descriptor alone', async () => {
  const h = render('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
    const list = items(body);
    const probe = JSON.parse(JSON.stringify(list[1])) as Record<string, unknown>;
    Object.assign(probe, {
      id: 'tidal_probe',
      instance: 'tidal_probe',
      key: 'tidal_probe',
      label: 'Tidal probe',
    });
    list.push(probe);
  });
  const probe = row(h, 'Tidal probe');
  expect(probe.getByText('Due')).toBeInTheDocument();
  expect(probe.getByRole('button', { name: 'Run now Tidal probe' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(probe.getByText('Not run by this automation')).toBeInTheDocument();
  await check(h);
});

it('AC3 Run now sends the item key once through the command flow', () => {
  const h = render('idle_ad_and_floating_gems_due_shopping_waiting');
  h.view.getByRole('button', { name: 'Run now Floating gems' }).click();
  expect(h.commands.runNow).toHaveBeenCalledTimes(1);
  expect(h.commands.runNow).toHaveBeenCalledWith(
    expect.objectContaining({ action: 'floating_gems', expectedRevision: 0 }),
  );
});

it('AC3 a completed Run now shows the outcome of the execution it started', async () => {
  const h = render('run_now_completed_links_its_execution');
  const command = h.view.container.querySelector('.tp-battle__command');
  expect(command).toHaveTextContent('Done');
  expect(command).toHaveTextContent('Outcome');
  expect(command).toHaveTextContent('Offer gone after tap');
  expect(command).not.toHaveTextContent('Effect confirmed');
  expect(command).not.toHaveTextContent('Claimed');
  expect(row(h, 'Ad gems').getByText('from Run now')).toBeInTheDocument();
  await check(h);
});

it('AC3 separates the shared sentence from an unrecorded descriptor Run now reason', async () => {
  // Test-local: no recorded descriptor publishes a reason string (brief gap B11).
  const h = render('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
    const floating = items(body).find((item) => item['key'] === 'floating_gems');
    Object.assign(ownPath(floating, 'descriptor', 'capabilities') as object, {
      run_now: 'needs a calibrated offer button',
    });
  });
  const floating = row(h, 'Floating gems');
  expect(
    floating.getByRole('button', { name: 'Run now Floating gems' }),
  ).toHaveAccessibleDescription(
    /^Run now is not offered for this action — needs a calibrated offer button/u,
  );
  expect(floating.getByText('needs a calibrated offer button')).toBeVisible();
  await check(h);
});

it('AC3 one open action disables every Run now; differing reasons stay with their rows', async () => {
  const h = render('run_now_completed_links_its_execution', (body) => {
    const commands = ownPath(body, 'battle', 'commands') as Record<string, unknown>[];
    Object.assign(commands[0] ?? {}, { state: 'started' });
    Object.assign(ownPath(body, 'battle', 'capabilities') as object, {
      actions: ['ad_gems', 'floating_gems', 'shopping'],
    });
  });
  expect(h.view.getAllByText('Another Run now is in progress')).toHaveLength(2);
  for (const name of ['Run now Ad gems', 'Run now Floating gems']) {
    const button = h.view.getByRole('button', { name });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveAccessibleDescription('Another Run now is in progress');
  }
  // Shopping waits for its queue (S2), so the region has no single shared reason.
  expect(h.view.getByRole('button', { name: 'Run now Shopping' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(h.view.container.querySelector('#battle-run-now-reason')).toBeNull();
  await check(h);
});

it('AC5 the stale case: last known 2 s ago on every item, no Due, Run now off with one reason and a check read', async () => {
  const h = render('projection_fails_later_the_last_projection_stays_and_its_stamps_stop');
  expect(h.view.getAllByText('Last known, 2 s ago')).toHaveLength(3);
  expect(h.view.queryByText('Due')).toBeNull();
  expect(h.view.queryByText('Action readings stopped updating')).toBeNull();
  expect(
    h.view.getAllByText(
      'The action readings are old; Run now and changes wait for a current reading',
    ),
  ).toHaveLength(1);
  const shared = h.view.container.querySelector('#battle-run-now-reason');
  expect(shared).toBeVisible();
  expect(shared).toHaveTextContent(
    'The action readings are old; Run now and changes wait for a current reading',
  );
  const buttons = [
    ...['Run now Ad gems', 'Run now Floating gems', 'Run now Shopping'],
    ...['Try once Shopping', 'Try once Attack', 'Try once Defense', 'Try once Utility'],
  ];
  for (const name of buttons)
    expect(h.view.getByRole('button', { name })).toHaveAccessibleDescription(
      /^The action readings are old; Run now and changes wait for a current reading/u,
    );
  h.view.getByRole('button', { name: 'Check this battle' }).click();
  expect(h.commands.checkBattle).toHaveBeenCalledTimes(1);
  h.view.getByRole('button', { name: 'Run now Ad gems' }).click();
  expect(h.commands.runNow).not.toHaveBeenCalled();
  await check(h);
});

it('AC5 a newer schema and another profile are read only with their reasons', async () => {
  const newer = render('projection_written_by_a_newer_driver_is_read_only');
  expect(newer.view.queryByText('Action readings are read only')).toBeNull();
  expect(newer.view.getAllByText('Not current')).toHaveLength(3);
  expect(
    newer.view.getByText(
      'A newer version wrote these action readings; Run now and changes are not offered',
    ),
  ).toBeVisible();
  expect(newer.view.getByRole('button', { name: 'Run now Ad gems' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(newer.view.getByRole('button', { name: 'Run now Ad gems' })).toHaveAccessibleDescription(
    'A newer version wrote these action readings; Run now and changes are not offered',
  );
  await check(newer);
  newer.view.unmount();
  const foreign = render('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
    Object.assign(ownPath(body, 'battle', 'runtime', 'actions', 'context') as object, {
      account: 'someone',
    });
  });
  expect(foreign.view.queryByText('Action readings are read only')).toBeNull();
  expect(foreign.view.container.querySelector('#battle-run-now-reason')).toBeVisible();
  expect(foreign.view.getByRole('button', { name: 'Run now Ad gems' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  await check(foreign);
});

it('AC5 an unrecognised state reads not recognised with the code under Why', async () => {
  const h = render('idle_ad_and_floating_gems_due_shopping_waiting', (body) => {
    Object.assign(items(body)[1] ?? {}, { state: 'hibernating' });
  });
  const floating = row(h, 'Floating gems');
  expect(floating.getByText('Not recognised')).toBeInTheDocument();
  expect(floating.getAllByText('hibernating').length).toBeGreaterThan(0);
  expect(
    floating.getByText(
      'Run now is off: this action reports a code this version does not recognise',
    ),
  ).toBeInTheDocument();
  await check(h);
});

it('AC5 D stale_report keeps the old-report header and one shared gate reason', async () => {
  const h = renderRegion(
    addCase(regionState(), 'battlesActive', 'battles_active.stale_report'),
    <ThisBattle />,
  );
  expect(h.view.container.querySelector('.tp-region__end')).toHaveTextContent(
    'old report, 3 m ago',
  );
  expect(h.view.getAllByText('Run now needs a fresh battle report')).toHaveLength(1);
  expect(h.view.queryByText('Action readings stopped updating')).toBeNull();
  await check(h);
});
