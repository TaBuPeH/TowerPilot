import { fireEvent, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { expect, it, vi } from 'vitest';
import { ThisBattle } from '../../../../src/features/dashboard/thisBattle/ThisBattle.tsx';
import { selectThisBattle } from '../../../../src/store/selectors/thisBattle.ts';
import { selectShoppingLive } from '../../../../src/store/selectors/shoppingLive.ts';
import { entries, own } from '../../../../src/core/read.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { dashboardBody, dashboardPagesCase } from '../../../support/dashboardFixture.ts';
import { addBody, intent, regionState, tick } from '../../store/regionHelpers.ts';
import { renderPage, renderRegion } from './harness.tsx';
import { postBodies } from '../../../support/dataLayer.ts';
const sample = (name: string) => dashboardPagesCase(`shopping_queue.${name}`);
const state = () =>
  addBody(regionState(), 'battlesActive', sample('shopping_plan_read_applied').body);
it('SQ8 places each included upgrade Try once inside its applied queue row and sends each exact action once', async () => {
  const s = state();
  const h = renderRegion(s, <ThisBattle />);
  fireEvent.click(h.view.getByText('Queue for this battle', { exact: true }));
  const queueButtons = h.view.container.querySelectorAll('.tp-battle__queue ol > li button');
  expect(queueButtons).toHaveLength(2);
  expect(queueButtons[0]).toHaveAccessibleName('Try once Health');
  expect(queueButtons[1]).toHaveAccessibleName('Try once Damage');
  for (const [name, action] of [
    ['Shopping', 'shopping'],
    ['Attack', 'shopping:attack'],
    ['Defense', 'shopping:defense'],
    ['Utility', 'shopping:utility'],
    ['Damage', 'shopping:attack:damage'],
    ['Health', 'shopping:defense:health'],
  ]) {
    const button = h.view.getByRole('button', { name: `Try once ${name}` });
    const row = button.closest<HTMLLIElement>('ol > li');
    if (name === 'Damage' || name === 'Health') {
      if (row === null) throw new Error(`missing queue row for ${name}`);
      expect(within(row).getByText(name, { exact: true })).toBeVisible();
      expect(row).toHaveTextContent(/Tap cap:|No tap cap/);
      expect(within(row).getAllByRole('button', { name: `Try once ${name}` })).toHaveLength(1);
    } else {
      expect(row).toBeNull();
    }
    fireEvent.click(button);
    expect(h.commands.runNow).toHaveBeenLastCalledWith(
      expect.objectContaining({ action, expectedRevision: 1 }),
    );
  }
  expect(h.commands.runNow).toHaveBeenCalledTimes(6);
  expect(h.view.getByText(/Try once can spend cash/)).toBeVisible();
  expect(h.view.getAllByRole('link', { name: 'Edit this battle in Runs' })[0]).toHaveAttribute(
    'href',
    '#runs/live',
  );
  expect(textOutsideCatalogue(h.view.container)).toEqual([]);
  expect(await axe(h.view.container)).toHaveNoViolations();
});
it('SQ8 unsaved draft, stale applied queue, unknown action/control and sending action gates send nothing', () => {
  const s = state();
  const scope = selectShoppingLive(s).scope;
  const base = selectThisBattle(s).tryOnce[0]?.button.command;
  if (base === null || base === undefined) throw new Error('missing command');
  const blocked = [
    { ...s, runDrafts: { [scope]: { reserve: '900' } } },
    tick(s, 300),
    { ...s, intents: [intent(s, { kind: 'run_now', meta: base })] },
    { ...s, intents: [intent(s, { kind: 'live_controls', meta: base })] },
    { ...s, intents: [intent(s, { kind: 'run_now', meta: base, phase: 'sending' })] },
  ];
  for (const current of blocked) {
    const h = renderRegion(current, <ThisBattle />);
    fireEvent.click(h.view.getByText('Queue for this battle', { exact: true }));
    for (const name of ['Shopping', 'Damage', 'Health']) {
      const button = h.view.getByRole('button', { name: `Try once ${name}` });
      expect(button).toHaveAttribute('aria-disabled', 'true');
      expect(button).toHaveAccessibleDescription();
      expect(document.getElementById(button.getAttribute('aria-describedby') ?? '')).toBeVisible();
      fireEvent.click(button);
    }
    expect(h.commands.runNow).not.toHaveBeenCalled();
    h.view.unmount();
  }
});
it.each([
  'shopping_upgrade_requested',
  'shopping_upgrade_started',
  'shopping_upgrade_completed_without_purchase',
])('SQ8 %s reports server outcome and evidence without claiming a purchase', async (name) => {
  const h = renderRegion(
    addBody(regionState(), 'battlesActive', sample(name).body),
    <ThisBattle />,
  );
  const command = h.view.container.querySelector('.tp-battle__command');
  expect(command).toHaveTextContent('shopping:attack:damage');
  if (name.endsWith('without_purchase')) {
    expect(command).toHaveTextContent('Completed; no purchase is implied');
    expect(command).toHaveTextContent('unknown_wave');
    expect(command).not.toHaveTextContent(/purchases confirmed/);
  }
  expect(textOutsideCatalogue(h.view.container)).toEqual([]);
  expect(await axe(h.view.container)).toHaveNoViolations();
});
it('SQ8 legacy queue and a nonadvertised action are visibly disabled', () => {
  const legacy = addBody(
    regionState(),
    'battlesActive',
    dashboardBody('battles_active.legacy_shopping_plan'),
  );
  const h = renderRegion(legacy, <ThisBattle />);
  expect(h.view.getByRole('button', { name: 'Try once Attack' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  h.view.unmount();
  const body = sample('shopping_plan_read_applied').body;
  // Remove one advertised action only: ensure the page never invents permission from a catalogue entry.
  const unsupported = {
    ...Object.fromEntries(entries(body)),
    battle: {
      ...Object.fromEntries(entries(own(body, 'battle'))),
      capabilities: { actions: ['shopping'], controls: ['shopping_plan'] },
    },
  };
  const other = renderRegion(addBody(regionState(), 'battlesActive', unsupported), <ThisBattle />);
  fireEvent.click(other.view.getByText('Queue for this battle', { exact: true }));
  fireEvent.click(other.view.getByRole('button', { name: 'Try once Damage' }));
  expect(other.commands.runNow).not.toHaveBeenCalled();
});

/** Receipt worlds use their own battle; the read envelope is copied from the recorded shopping read. */
function preAction(name: string) {
  const body = sample(name).body;
  const battle = own(body, 'battle');
  return {
    ...Object.fromEntries(entries(sample('shopping_upgrade_requested').body)),
    ...Object.fromEntries(entries(body)),
    server_time: own(own(battle, 'runtime'), 'server_time'),
    battle: { ...Object.fromEntries(entries(battle)), commands: [] },
  };
}
it.each([
  ['whole', 'Shopping'],
  ['attack', 'Attack'],
  ['defense', 'Defense'],
  ['utility', 'Utility'],
  ['upgrade', 'Damage'],
])(
  'SQ8 %s uses run_now for exactly the advertised action and receipt blocks another attempt',
  async (kind, name) => {
    vi.useFakeTimers();
    try {
      const accepted = sample(`shopping_${kind}_accepted`);
      window.location.hash = '#dashboard';
      const page = await renderPage({
        newKey: () => 'sq-act',
        before: (fake) => {
          fake.route('GET', '/api/battles/active', () => ({
            status: 200,
            body: preAction(`shopping_${kind}_accepted`),
          }));
          fake.route('POST', accepted.request.path, () => accepted);
        },
      });
      await page.advance(100);
      await page.user.click(page.view.getByText('Queue for this battle', { exact: true }));
      if (kind === 'upgrade') {
        const row = page.view
          .getByRole('button', { name: `Try once ${name}` })
          .closest<HTMLLIElement>('ol > li');
        if (row === null) throw new Error('missing applied upgrade queue row');
        expect(within(row).getByText(name, { exact: true })).toBeVisible();
        expect(row).toHaveTextContent(/Tap cap:|No tap cap/);
      }
      await page.user.click(page.view.getByRole('button', { name: `Try once ${name}` }));
      await page.advance(100);
      expect(postBodies(page.fake, accepted.request.path)).toEqual([accepted.request.body]);
      const button = page.view.getByRole('button', { name: 'Try once Shopping' });
      expect(button).toHaveAttribute('aria-disabled', 'true');
      await page.user.click(button);
      await page.advance(2000);
      expect(postBodies(page.fake, accepted.request.path)).toHaveLength(1);
      page.view.unmount();
      page.data.stop();
    } finally {
      window.location.hash = '';
      vi.useRealTimers();
    }
  },
);
it('SQ8 unknown upgrade never repeats spending automatically and an explicit replay keeps the same request', async () => {
  vi.useFakeTimers();
  try {
    const unknown = sample('shopping_upgrade_reply_unknown');
    const replay = sample('shopping_upgrade_identical_replay');
    window.location.hash = '#dashboard';
    const page = await renderPage({
      newKey: () => 'sq-unknown',
      before: (fake) => {
        fake.route('GET', '/api/battles/active', () => ({
          status: 200,
          body: preAction('shopping_upgrade_identical_replay'),
        }));
        fake.route('POST', unknown.request.path, () => unknown);
      },
    });
    await page.advance(100);
    await page.user.click(page.view.getByText('Queue for this battle', { exact: true }));
    await page.user.click(page.view.getByRole('button', { name: 'Try once Damage' }));
    await page.advance(100);
    expect(postBodies(page.fake, unknown.request.path)).toEqual([unknown.request.body]);
    await page.advance(60_000);
    expect(postBodies(page.fake, unknown.request.path)).toHaveLength(1);
    expect(page.view.getByRole('button', { name: 'Try once Shopping' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    page.fake.route('POST', unknown.request.path, () => replay);
    await page.user.click(page.view.getByRole('button', { name: 'Send same request again' }));
    await page.advance(100);
    expect(postBodies(page.fake, unknown.request.path)).toEqual([
      unknown.request.body,
      unknown.request.body,
    ]);
    await page.advance(100);
    page.view.unmount();
    page.data.stop();
  } finally {
    window.location.hash = '';
    vi.useRealTimers();
  }
});
