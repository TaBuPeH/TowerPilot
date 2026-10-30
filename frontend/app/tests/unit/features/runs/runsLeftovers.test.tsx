import { act, within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { entries, list, own, ownPath } from '../../../../src/core/read.ts';
import { serverDateText } from '../../../../src/copy/format.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { e0Config } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  dashboardPagesCase,
  type DashboardCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * F6-11 Runs leftovers (backend/logs/reviews/ui-pages/f6-11-runs-leftovers-brief.md) on the
 * whole wired app, replaying the recorded live-change family
 * (backend/tests/fixtures/dashboard_contract_pages_v1.json, run_controls_live).
 */
// Each case renders a whole page (axe included); under the full parallel suite 5 s is not enough.
vi.setConfig({ testTimeout: 30_000 });

const live = (name: string) => dashboardPagesCase(`run_controls_live.${name}`);
const BATTLE = /^\/api\/battles\/active$/u;
const RESET = live('accepted_null_resets_a_path_to_the_battles_starting_value');
const RESET_KEY = (RESET.request.body as { idempotency_key: string }).idempotency_key;

/**
 * The battle the recorded reset was sent to: revision 2 with its ad-gem numbers applied.
 * Modified recorded case: no read was recorded between the numbers change and the reset,
 * so the battle of `accepted_numbers_after_the_previous_change_applied` (control revision 2)
 * gets the driver's application of it (applied revision 2, applied overrides = overrides).
 */
function battleBeforeReset(): DashboardCase {
  const answered = live('accepted_numbers_after_the_previous_change_applied');
  const read = live('read_after_the_driver_applied_the_change');
  const battle = structuredClone((answered.body as { battle: Record<string, unknown> }).battle);
  battle['applied_revision'] = battle['control_revision'];
  battle['applied_overrides'] = structuredClone(battle['overrides']);
  const body = { ...(read.body as Record<string, unknown>), battle };
  return { ...read, body };
}

/**
 * The battle read right after the recorded reset, before the driver applied it. Modified
 * recorded case: the reset's own answer battle (control revision 3, command with key live-5
 * still requested) in the shape of a battle read; no read after the reset was recorded.
 */
function battleAfterResetListed(): DashboardCase {
  const read = live('read_after_the_driver_applied_the_change');
  const battle = structuredClone((RESET.body as { battle: Record<string, unknown> }).battle);
  return { ...read, body: { ...(read.body as Record<string, unknown>), battle } };
}

/**
 * The battle after the driver applied the recorded reset. Modified recorded case: the reset's
 * own answer (control revision 3, override of interval_sec removed, key live-5 listed) gets the
 * driver's application of it, as read_after_the_driver_applied_the_change records one: applied
 * revision 3, applied overrides = overrides, applied and effective controls = requested
 * controls (interval_sec is the starting 720), and the reset's command applied with its path.
 */
function battleAfterResetApplied(): DashboardCase {
  const read = battleAfterResetListed();
  const battle = (read.body as { battle: Record<string, unknown> }).battle;
  battle['applied_revision'] = battle['control_revision'];
  battle['applied_overrides'] = structuredClone(battle['overrides']);
  battle['applied_controls'] = structuredClone(battle['requested_controls']);
  battle['effective'] = structuredClone(battle['requested_controls']);
  const commands = battle['commands'] as Record<string, unknown>[];
  Object.assign(commands.at(-1) ?? {}, {
    state: 'applied',
    applied_paths: ['routines.ad_gems.interval_sec'],
  });
  return read;
}

async function confirmReset(page: Awaited<ReturnType<typeof renderPage>>) {
  const row = liveRegion().querySelector<HTMLElement>(
    '[data-path="routines.ad_gems.interval_sec"]',
  );
  if (row === null) throw new Error('no reset row');
  await page.user.click(within(row).getByRole('button', { name: /starting value/u }));
  await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting value' }));
  await page.advance(100);
}

async function openLive(read: () => DashboardCase, options: PageOptions = {}) {
  window.location.hash = '#runs/live';
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    config: () => e0Config(true),
    ...options,
    before: (fake) => {
      fake.route('GET', BATTLE, () => {
        const found = read();
        return { status: found.status, body: found.body };
      });
      options.before?.(fake);
    },
  });
  await page.advance(1_000);
  return page;
}

function liveRegion(): HTMLElement {
  const found = document.querySelector<HTMLElement>('[data-region="runsLive"]');
  if (found === null) throw new Error('live region missing');
  return found;
}

function dialog(): HTMLElement {
  const found = document.querySelector<HTMLElement>('dialog[open]');
  if (found === null) throw new Error('no open dialog');
  return found;
}

const resetRows = () => [...liveRegion().querySelectorAll<HTMLElement>('[data-path]')];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('F6-11 RL1 reset one live override', () => {
  it('RL1 a battle with no override offers no reset', async () => {
    await openLive(() => live('read_before_the_first_change'));
    expect(resetRows()).toEqual([]);
    expect(within(liveRegion()).queryByRole('button', { name: /starting value/u })).toBeNull();
  });

  it('RL1 every applied override of the battle is listed with its reset; a waiting one is not', async () => {
    await openLive(() => live('read_after_several_changes_overrides_and_applied_values'));
    expect(resetRows().map((row) => row.dataset['path'])).toEqual([
      'routines.ad_gems.enabled',
      'routines.ad_gems.window_sec',
      'routines.floating_gems.enabled',
      'routines.floating_gems.interval_sec',
      'routines.floating_gems.tap_interval_sec',
      'routines.shopping.enabled',
      'routines.shopping.interval_sec',
      'routines.rewards.enabled',
    ]);
    window.location.hash = '';
  });

  it('RL1 an override still waiting to apply offers no reset yet', async () => {
    await openLive(() => live('read_after_the_change_lists_the_command_and_its_key'));
    expect(resetRows()).toEqual([]);
  });

  it('RL1 Cancel sends nothing; a confirmed reset sends the recorded body and key once', async () => {
    const page = await openLive(battleBeforeReset, { newKey: () => RESET_KEY });
    const row = liveRegion().querySelector<HTMLElement>(
      '[data-path="routines.ad_gems.interval_sec"]',
    );
    if (row === null) throw new Error('no reset row');
    await page.user.click(within(row).getByRole('button', { name: /starting value/u }));
    expect(within(dialog()).getByText(/can differ from the saved settings/u)).toBeInTheDocument();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    await page.advance(1_000);
    expect(page.fake.writes()).toEqual([]);
    page.fake.route('POST', /live-controls$/u, () => ({ status: RESET.status, body: RESET.body }));
    await page.user.click(within(row).getByRole('button', { name: /starting value/u }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting value' }));
    await page.advance(1_000);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(1);
    expect(writes[0]?.path).toBe(RESET.request.path);
    expect(JSON.parse(writes[0]?.body ?? 'null')).toEqual(RESET.request.body);
    expect(within(liveRegion()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    await page.advance(30_000);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL1 a reset whose answer is lost stays unknown and is never resent on its own; the person may resend the identical request', async () => {
    const page = await openLive(battleBeforeReset, { newKey: () => RESET_KEY });
    page.fake.route('POST', /live-controls$/u, () => 'hold');
    const row = liveRegion().querySelector<HTMLElement>(
      '[data-path="routines.ad_gems.interval_sec"]',
    );
    if (row === null) throw new Error('no reset row');
    await page.user.click(within(row).getByRole('button', { name: /starting value/u }));
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting value' }));
    await page.advance(100);
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(60_000);
    expect(page.fake.writes()).toHaveLength(1);
    // While its outcome is unknown, a second reset of this battle waits.
    const again = liveRegion().querySelector<HTMLElement>(
      '[data-path="routines.ad_gems.interval_sec"]',
    );
    expect(
      within(again ?? liveRegion()).getByRole('button', { name: /starting value/u }),
    ).toHaveAttribute('aria-disabled', 'true');
    page.fake.route('POST', /live-controls$/u, () => ({ status: RESET.status, body: RESET.body }));
    await page.user.click(
      within(liveRegion()).getByRole('button', { name: 'Send same request again' }),
    );
    await page.advance(1_000);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(2);
    expect(writes[1]?.body).toBe(writes[0]?.body);
  });

  it('RL1 a confirmed reset reads back: the override and its reset are gone and the field holds the starting value', async () => {
    let read = battleBeforeReset;
    const page = await openLive(() => read(), { newKey: () => RESET_KEY });
    page.fake.route('POST', /live-controls$/u, () => ({ status: RESET.status, body: RESET.body }));
    await confirmReset(page);
    expect(page.fake.writes()).toHaveLength(1);
    read = battleAfterResetApplied;
    await page.advance(5_000);
    expect(liveRegion().querySelector('[data-path="routines.ad_gems.interval_sec"]')).toBeNull();
    const field = within(liveRegion()).getByRole('textbox', { name: /Ad gems Every/u });
    expect(field).toHaveValue('720');
    // Only the battle's other overrides are still listed.
    expect(resetRows().map((row) => row.dataset['path'])).toEqual([
      'routines.ad_gems.enabled',
      'routines.ad_gems.window_sec',
    ]);
    // F6-11-IR-01: the reset's own line follows the read: applied, no longer waiting.
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(within(liveRegion()).queryByText('Sent to this battle - waiting to apply')).toBeNull();
    await page.advance(30_000);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL1 a lost reset stays unknown on an unchanged read and settles only on a read of its battle listing its key', async () => {
    let read = battleBeforeReset;
    const page = await openLive(() => read(), { newKey: () => RESET_KEY });
    page.fake.route('POST', /live-controls$/u, () => 'hold');
    await confirmReset(page);
    page.fake.next(/live-controls$/u, 'POST').lose();
    // Unchanged reads of the same battle cannot prove the reset failed.
    await page.advance(10_000);
    expect(within(liveRegion()).getByText('No reply - it may have been recorded')).toBeVisible();
    // A read of another battle does not settle it either.
    read = () => live('read_after_the_unknown_lists_the_key');
    await page.advance(5_000);
    expect(within(liveRegion()).queryByText('A later read lists this change')).toBeNull();
    // The battle it was sent to, listing its key, settles it as recorded; nothing was resent.
    read = battleAfterResetListed;
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('A later read lists this change')).toBeVisible();
    // F6-11-IR-01: once that battle lists its command applied, the line says so.
    read = battleAfterResetApplied;
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it.each([
    ['another battle replaces it', { id: '000000ff-0000-4000-8000-000000000000' }],
    ['its control revision changes', { control_revision: 9, applied_revision: 9 }],
  ])(
    'RL1 an open confirmation closes and sends nothing when %s',
    async (_name, change: Record<string, unknown>) => {
      let read = battleBeforeReset;
      const page = await openLive(() => read(), { newKey: () => RESET_KEY });
      const row = liveRegion().querySelector<HTMLElement>(
        '[data-path="routines.ad_gems.interval_sec"]',
      );
      if (row === null) throw new Error('no reset row');
      await page.user.click(within(row).getByRole('button', { name: /starting value/u }));
      expect(dialog()).toBeVisible();
      // Modified recorded case: the same battle read with another identity or revision
      // (no recorded read replaces a battle while a reset is being confirmed).
      read = () => {
        const before = battleBeforeReset();
        const body = before.body as { battle: Record<string, unknown> };
        return { ...before, body: { ...body, battle: { ...body.battle, ...change } } };
      };
      await page.advance(5_000);
      expect(document.querySelector('dialog[open]')).toBeNull();
      await page.advance(30_000);
      expect(page.fake.writes()).toEqual([]);
    },
  );

  it('RL1 the reset list uses catalogue words and passes axe', async () => {
    const page = await openLive(() =>
      live('read_after_several_changes_overrides_and_applied_values'),
    );
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

describe('F6-11 RL2 reset every override of this battle at once (r12 candidate run_controls_live_reset_all)', () => {
  const all = (name: string) => dashboardPagesCase(`run_controls_live_reset_all.${name}`);
  const RESET_ALL = all('accepted_null_for_every_override_resets_them_together');
  const BEFORE = () => all('read_before_the_reset_two_overrides_are_in_force');
  const AFTER = () => all('read_after_every_override_was_reset');
  const resetAllButton = () =>
    within(liveRegion()).getByRole('button', {
      name: "Use the battle's starting values for all 2",
    });

  it('RL2 a battle with two overrides offers one reset of both; a battle with one does not', async () => {
    const page = await openLive(BEFORE);
    expect(resetAllButton()).toBeVisible();
    page.view.unmount();
    await openLive(() => all('read_after_the_partial_reset_one_override_remains'));
    expect(resetRows().map((row) => row.dataset['path'])).toEqual(['routines.ad_gems.window_sec']);
    expect(
      within(liveRegion()).queryByRole('button', { name: /starting values for all/u }),
    ).toBeNull();
  });

  it('RL2 Cancel sends nothing; a confirmed reset of all sends the recorded body once, then reads back applied', async () => {
    let read = BEFORE;
    const page = await openLive(() => read(), { newKey: () => 'rlr-2' });
    await page.user.click(resetAllButton());
    expect(
      within(dialog())
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['Ad gemsEvery', 'Floating gemsOn']);
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    await page.advance(1_000);
    expect(page.fake.writes()).toEqual([]);
    page.fake.route('POST', /live-controls$/u, () => ({
      status: RESET_ALL.status,
      body: RESET_ALL.body,
    }));
    await page.user.click(resetAllButton());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(100);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(1);
    expect(writes[0]?.path).toBe(RESET_ALL.request.path);
    expect(JSON.parse(writes[0]?.body ?? 'null')).toEqual(RESET_ALL.request.body);
    expect(within(liveRegion()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    // The recorded read after it: empty routine mappings, no override left, the command applied.
    read = AFTER;
    await page.advance(5_000);
    expect(resetRows()).toEqual([]);
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(within(liveRegion()).getByRole('textbox', { name: /^Ad gems Every$/u })).toHaveValue(
      '720',
    );
    await page.advance(30_000);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL2 a reset of all whose answer is lost stays unknown, is never resent, and settles on the read listing its key', async () => {
    let read = BEFORE;
    const page = await openLive(() => read(), { newKey: () => 'rlr-2' });
    page.fake.route('POST', /live-controls$/u, () => 'hold');
    await page.user.click(resetAllButton());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(100);
    page.fake.next(/live-controls$/u, 'POST').lose();
    await page.advance(10_000);
    expect(within(liveRegion()).getByText('No reply - it may have been recorded')).toBeVisible();
    expect(resetAllButton()).toHaveAttribute('aria-disabled', 'true');
    read = AFTER;
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL2 a refused reset of all keeps both overrides and is not resent', async () => {
    const page = await openLive(() => all('read_after_the_refusals_both_overrides_remain'));
    // The recorded 409 was produced for a request with the older control revision 0; the
    // page sends the read's revision 1, and the recorded refusal answers it.
    const stale = all('reset_all_with_a_stale_control_revision_refused');
    page.fake.route('POST', /live-controls$/u, () => ({ status: stale.status, body: stale.body }));
    await page.user.click(resetAllButton());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('Not sent - nothing was recorded')).toBeVisible();
    expect(resetRows().map((row) => row.dataset['path'])).toEqual([
      'routines.ad_gems.interval_sec',
      'routines.floating_gems.enabled',
    ]);
    await page.advance(30_000);
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('RL2 an open confirmation of all closes and sends nothing when another battle replaces it', async () => {
    let read = BEFORE;
    const page = await openLive(() => read());
    await page.user.click(resetAllButton());
    expect(dialog()).toBeVisible();
    read = () => all('read_after_the_refusals_both_overrides_remain');
    await page.advance(5_000);
    expect(document.querySelector('dialog[open]')).toBeNull();
    await page.advance(30_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('RL2 the reset of all and its confirmation use catalogue words and pass axe', async () => {
    const page = await openLive(BEFORE);
    await page.user.click(resetAllButton());
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

describe('RL2 recorded mixed shopping and routine resets', () => {
  const mixed = (name: string) => dashboardPagesCase(`run_controls_live_reset_all_mixed.${name}`);
  const allButton = () =>
    within(liveRegion()).getByRole('button', {
      name: "Use the battle's starting values for all 3",
    });

  it('RL2 mixed reset sends every leaf as null once; acceptance waits for the same-battle applied read', async () => {
    let source = mixed('read_before_the_reset_the_mixed_overrides_are_in_force');
    const accepted = mixed('accepted_null_for_shopping_plan_and_routine_overrides');
    const page = await openLive(() => source, { newKey: () => 'mx-3' });
    const items = () => liveRegion().querySelector('[data-reset-all-items]');
    expect(items()?.querySelectorAll('li')).toHaveLength(3);
    await page.user.click(allButton());
    expect(within(dialog()).getAllByRole('listitem')).toHaveLength(3);
    expect(within(dialog()).getByText('Shopping plan')).toBeVisible();
    await page.user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    expect(page.fake.writes()).toEqual([]);
    page.fake.route('POST', accepted.request.path, () => accepted);
    await page.user.click(allButton());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(100);
    expect(JSON.parse(page.fake.writes()[0]?.body ?? 'null')).toEqual(accepted.request.body);
    expect(within(liveRegion()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    source = mixed('read_after_the_reset_was_accepted_before_it_applies');
    await page.advance(5_000);
    expect(within(liveRegion()).queryByText('Applied to this battle')).toBeNull();
    source = mixed('read_after_the_mixed_reset_applied');
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(resetRows()).toEqual([]);
    expect(items()).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  }, 15_000);

  it('RL2 reset of all waits for the config read that confirms the context; a late confirmation never closes an offered dialog', async () => {
    const source = mixed('read_before_the_reset_the_mixed_overrides_are_in_force');
    const page = await openLive(() => source, {
      before: (fake) => {
        fake.route('GET', '/api/config', () => 'hold');
      },
    });
    // The battle read answered; the config read that names the page context has not.
    expect(
      liveRegion().querySelector('[data-reset-all-items]')?.querySelectorAll('li'),
    ).toHaveLength(3);
    expect(allButton()).toHaveAttribute('aria-disabled', 'true');
    expect(
      within(liveRegion()).getAllByText('Waiting for the emulator, account and profile to be read.')
        .length,
    ).toBeGreaterThan(0);
    for (const row of resetRows())
      expect(within(row).getByRole('button', { name: /starting value/u })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
    await page.user.click(allButton());
    expect(document.querySelector('dialog[open]')).toBeNull();
    page.fake.next('/api/config', 'GET').respond(200, e0Config(true));
    await page.advance(1_000);
    expect(allButton()).not.toHaveAttribute('aria-disabled', 'true');
    await page.user.click(allButton());
    expect(within(dialog()).getAllByRole('listitem')).toHaveLength(3);
    await page.advance(5_000);
    expect(within(dialog()).getAllByRole('listitem')).toHaveLength(3);
    expect(page.fake.writes()).toEqual([]);
  });
  it('RL2 stale mixed refusal preserves the complete list and raw draft without resending', async () => {
    const source = mixed('read_after_the_refusal_both_kinds_of_override_remain');
    const refused = mixed('mixed_reset_with_a_stale_control_revision_refused');
    const page = await openLive(() => source, { newKey: () => 'mx-s' });
    const input = within(liveRegion()).getByRole('textbox', { name: 'Shopping Every' });
    await page.user.clear(input);
    await page.user.type(input, '77');
    // Race replay: the recorded refusal used revision 1; the currently displayed read is 2.
    // Keep both responses intact and prove the client sends its read's revision, then retains edits.
    page.fake.route('POST', refused.request.path, () => refused);
    await page.user.click(allButton());
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(5_000);
    expect(input).toHaveValue('77');
    expect(
      liveRegion().querySelector('[data-reset-all-items]')?.querySelectorAll('li'),
    ).toHaveLength(3);
    expect(within(liveRegion()).getByText('Not sent - nothing was recorded')).toBeVisible();
    expect(liveRegion().querySelector('[data-outcome]')?.textContent).toContain(
      'Control revision changed',
    );
    expect(JSON.parse(page.fake.writes()[0]?.body ?? 'null')).toEqual({
      ...Object.fromEntries(Object.entries(refused.request.body as Record<string, unknown>)),
      expected_revision: 2,
    });
    await page.advance(30_000);
    expect(page.fake.writes()).toHaveLength(1);
  });
});

describe('F6-11-IR-01 a live change follows later reads of its own battle', () => {
  const SWITCH_OFF = live('accepted_switch_off');
  const outcomeText = () =>
    liveRegion().querySelector<HTMLElement>('[data-outcome]')?.dataset['outcome'] ?? null;

  async function switchOff(read: { current: () => DashboardCase }) {
    const page = await openLive(() => read.current(), { newKey: () => 'live-1' });
    page.fake.route('POST', /live-controls$/u, () => ({
      status: SWITCH_OFF.status,
      body: SWITCH_OFF.body,
    }));
    await page.user.click(within(liveRegion()).getByRole('checkbox', { name: 'Ad gems On' }));
    await page.user.click(
      within(liveRegion()).getByRole('button', { name: 'Save changes to this battle' }),
    );
    await page.advance(100);
    expect(JSON.parse(page.fake.writes()[0]?.body ?? 'null')).toEqual(SWITCH_OFF.request.body);
    return page;
  }

  it('IR-01 waiting while its battle lists the command requested; "Applied to this battle" once a read lists it applied (recorded reads)', async () => {
    const read = { current: () => live('read_before_the_first_change') };
    const page = await switchOff(read);
    expect(within(liveRegion()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    read.current = () => live('read_after_the_change_lists_the_command_and_its_key');
    await page.advance(5_000);
    expect(outcomeText()).toBe('live_recorded');
    expect(within(liveRegion()).getByText('Sent to this battle - waiting to apply')).toBeVisible();
    read.current = () => live('read_after_the_driver_applied_the_change');
    await page.advance(5_000);
    expect(outcomeText()).toBe('live_applied');
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(within(liveRegion()).queryByText('Sent to this battle - waiting to apply')).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('IR-01 a read of the same battle id in another generation listing an applied command does not settle it', async () => {
    const read = { current: () => live('read_before_the_first_change') };
    const page = await switchOff(read);
    // Modified recorded case: the applied read with another generation on the battle and
    // its command (no recorded read replaces the generation after this change).
    read.current = () => {
      const applied = live('read_after_the_driver_applied_the_change');
      const body = applied.body as {
        battle: Record<string, unknown> & { commands: Record<string, unknown>[] };
      };
      const generation = '000000ff-0000-4000-8000-000000000000';
      body.battle['generation'] = generation;
      for (const command of body.battle.commands) command['generation'] = generation;
      return applied;
    };
    await page.advance(5_000);
    expect(within(liveRegion()).queryByText('Applied to this battle')).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  });

  it('IR-01 a command its battle rejected reads Not applied with a Failed word (never "waiting")', async () => {
    const read = { current: () => live('read_before_the_first_change') };
    const page = await switchOff(read);
    // Modified recorded case: the read listing the command, with the state and reason the
    // recorded battles_active.commands_controls_rejected_action_rejected gives a rejected
    // controls command (no run_controls_live read records a rejection).
    read.current = () => {
      const listed = live('read_after_the_change_lists_the_command_and_its_key');
      const body = listed.body as { battle: { commands: Record<string, unknown>[] } };
      Object.assign(body.battle.commands[0] ?? {}, { state: 'rejected', reason: 'driver_refused' });
      return listed;
    };
    await page.advance(5_000);
    expect(outcomeText()).toBe('live_not_applied');
    expect(
      within(liveRegion()).getByText('Not applied - this battle rejected the change or it expired'),
    ).toBeVisible();
    expect(liveRegion().querySelector('[data-outcome] [data-state="failed"]')).not.toBeNull();
    expect(within(liveRegion()).queryByText('Sent to this battle - waiting to apply')).toBeNull();
    expect(page.fake.writes()).toHaveLength(1);
  });
});

const recordedBattle = (name: string) => () => dashboardCase(`battles_active.${name}`);
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
const detailsOf = (routine: string): HTMLDetailsElement => {
  const body = liveRegion().querySelector(`[data-routine-details="${routine}"]`);
  const found = body?.closest('details');
  if (found === null || found === undefined) throw new Error(`no ${routine} details`);
  return found;
};
const rowsOf = (routine: string): Record<string, string> =>
  Object.fromEntries(
    [...detailsOf(routine).querySelectorAll('.tp-run-details__row')].flatMap((row) => {
      const facts = row.querySelectorAll('.tp-facts__item');
      return facts.length > 0
        ? [...facts].map((fact) => [
            fact.querySelector('dt')?.textContent ?? '',
            fact.querySelector('dd')?.textContent ?? '',
          ])
        : [
            [
              row.querySelector('dt span')?.textContent ?? '',
              row.querySelector('dd')?.textContent ?? '',
            ],
          ];
    }),
  );

describe('F6-11 RL7 battle details in the live section', () => {
  it('RL7 each reported routine has its own closed "battle details" disclosure', async () => {
    await openLive(recordedBattle('runtime_ad_orbit_shopping'));
    const summaries = [
      ...liveRegion().querySelectorAll<HTMLElement>(
        '[data-part="runsDetails"] .tp-tile__body > details > summary',
      ),
    ];
    expect(summaries.map((s) => s.textContent)).toEqual([
      'Abilities: battle details',
      'Ad gems: battle details',
      'Floating gems: battle details',
      'Repeat: battle details',
      'Rescue: battle details',
      'Rewards: battle details',
      'Shopping: battle details',
    ]);
    expect(summaries.every((s) => !(s.parentElement as HTMLDetailsElement).open)).toBe(true);
  });

  it('RL7 Ad gems shows the recorded offer, timing, counts and the Runtime link', async () => {
    await openLive(recordedBattle('runtime_ad_orbit_shopping'));
    expect(rowsOf('ad_gems')).toEqual({
      Offer: 'Not knownnot_observed',
      'First seen': 'Not seen yet',
      Appeared: 'Not known; the first sighting is not when it appeared',
      'Last look at the battle': 'No look recorded',
      Collection: 'Window closes in 18 s',
      'Next look': 'Due now, waiting for the automation',
      'Latest failed attempt': 'None recorded',
      Attempts: '0',
      Confirmed: '1',
      Skipped: '0',
      'Windows missed': '0',
      'Schedule wait': 'Not reported',
      'Response delay': 'Not reported',
      Verification: '2 s',
    });
    // The reason is the server's code, shown as sent.
    expect(detailsOf('ad_gems').querySelector('[data-server-text="id"]')?.textContent).toBe(
      'not_observed',
    );
    const link = within(detailsOf('ad_gems')).getByRole('link', {
      name: 'Events of this battle on Runtime',
    });
    expect(link).toHaveAttribute(
      'href',
      '#runtime?battle_id=00000015-0000-4000-8000-000000000000&operation_id=00000016-0000-4000-8000-000000000000&offer_id=battles_runtime-offer',
    );
  });

  it('RL7 Floating gems with no recorded refusal says so; other routines show only their link', async () => {
    await openLive(recordedBattle('runtime_ad_orbit_shopping'));
    expect(rowsOf('floating_gems')).toEqual({ 'Latest refusal': 'None recorded' });
    expect(rowsOf('shopping')).toEqual({});
    expect(
      within(detailsOf('shopping')).getByRole('link', { name: 'Events of this battle on Runtime' }),
    ).toHaveAttribute(
      'href',
      '#runtime?battle_id=00000015-0000-4000-8000-000000000000&operation_id=00000016-0000-4000-8000-000000000000',
    );
  });

  it("RL7 each routine's reported values are the server's own object, shown as sent", async () => {
    const recorded = dashboardCase('battles_active.runtime_ad_orbit_shopping');
    await openLive(() => recorded);
    const raw = detailsOf('ad_gems').querySelector('[data-server-text="detail"]');
    const routines = (
      recorded.body as { battle: { runtime: { routines: Record<string, unknown> } } }
    ).battle.runtime.routines;
    expect(JSON.parse(raw?.textContent ?? 'null')).toEqual(routines['ad_gems']);
  });

  it('RL7 a report that is not fresh marks the details as last known', async () => {
    await openLive(recordedBattle('stale_report'));
    expect(within(liveRegion()).getByText('Last known values')).toBeInTheDocument();
    expect(detailsOf('ad_gems')).toBeInstanceOf(HTMLDetailsElement);
  });

  it('RL7 a fresh report has no last-known note; no tracked battle shows no details', async () => {
    const page = await openLive(recordedBattle('runtime_ad_orbit_shopping'));
    expect(within(liveRegion()).queryByText('Last known values')).toBeNull();
    page.view.unmount();
    await openLive(recordedBattle('null_no_driver'));
    expect(liveRegion().querySelector('[data-part="runsDetails"]')).toBeNull();
  });

  it('RL7 a newest battle read that fails marks the retained details last known at once', async () => {
    let failing = false;
    const recorded = dashboardCase('battles_active.runtime_ad_orbit_shopping');
    const page = await openLive(() =>
      failing ? { ...recorded, status: 502, body: 'upstream unavailable' } : recorded,
    );
    expect(within(liveRegion()).queryByText('Last known values')).toBeNull();
    failing = true;
    await page.advance(5_000);
    // The retained report is only seconds old, yet the newest read failed.
    expect(within(liveRegion()).getByText('Last known values')).toBeVisible();
    expect(detailsOf('ad_gems')).toBeInstanceOf(HTMLDetailsElement);
    failing = false;
    await page.advance(5_000);
    expect(within(liveRegion()).queryByText('Last known values')).toBeNull();
  });

  it('RL7 the recorded seen offer shows server dates with ages, its appearance bounds, failure, counts and timing', async () => {
    await openLive(() =>
      dashboardPagesCase('battle_details_runtime.ad_gems_offer_seen_and_failed_attempt'),
    );
    const rows = rowsOf('ad_gems');
    const age = (at: number) => new RegExp(`^${escape(serverDateText(at))} \\(\\d+ s ago\\)$`, 'u');
    expect(rows['Offer']).toBe('Seen on the last battle screen');
    expect(rows['First seen']).toMatch(age(1_799_999_991));
    expect(rows['Last look at the battle']).toMatch(age(1_799_999_999));
    expect(rows['Appeared']).toMatch(
      new RegExp(
        `^After ${escape(serverDateText(1_799_999_986))} \\(\\d+ s ago\\), seen by ${escape(serverDateText(1_799_999_991))} \\(\\d+ s ago\\); 5 s between looks$`,
        'u',
      ),
    );
    expect(rows['Collection']).toMatch(/^Next window /u);
    expect(rows['Latest failed attempt']).toMatch(
      new RegExp(
        `^offer_still_visible${escape(serverDateText(1_800_000_000))} \\(\\d+ s ago\\)$`,
        'u',
      ),
    );
    expect(rows).toMatchObject({
      Attempts: '1',
      Confirmed: '0',
      Skipped: '0',
      'Windows missed': '0',
      'Schedule wait': 'Not reported',
      'Response delay': 'Not reported',
      Verification: '2 s',
    });
    expect(
      within(detailsOf('ad_gems')).getByRole('link', { name: 'Events of this battle on Runtime' }),
    ).toHaveAttribute(
      'href',
      expect.stringMatching(/offer_id=bd_ad_offer_failed-offer&since=1799999931$/u),
    );
  });

  it('RL7 the recorded Floating gems refusal shows its reason, server date and age', async () => {
    await openLive(() =>
      dashboardPagesCase('battle_details_runtime.floating_gems_refusal_recorded'),
    );
    expect(rowsOf('floating_gems')['Latest refusal']).toMatch(
      new RegExp(
        `^invalid_orbit_arc${escape(serverDateText(1_800_000_000))} \\(\\d+ s ago\\)$`,
        'u',
      ),
    );
  });

  // Modified recorded case: no recorded sighting has a fractional observation interval;
  // the recorded offer's earlier look is moved half a second.
  it('RL7 a fractional appearance interval is shown to a tenth of a second', async () => {
    const seen = dashboardPagesCase('battle_details_runtime.ad_gems_offer_seen_and_failed_attempt');
    const ad = (
      seen.body as { battle: { runtime: { routines: Record<string, Record<string, unknown>> } } }
    ).battle.runtime.routines['ad_gems'];
    if (ad !== undefined) ad['appearance_after_at'] = 1_799_999_988.5;
    await openLive(() => seen);
    expect(rowsOf('ad_gems')['Appeared']).toMatch(/; 2\.5 s between looks$/u);
  });

  it('RL7 the opened details use catalogue words and pass axe', async () => {
    const page = await openLive(recordedBattle('runtime_ad_orbit_shopping'));
    for (const details of liveRegion().querySelectorAll('details')) details.open = true;
    expect(textOutsideCatalogue(page.view.container)).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });
});

it.each([false, true])(
  'RL2 F6-11-P3-01 lost receipt with restored intent %s checks retained reset paths despite incomplete listed payload',
  async (reload) => {
    const mixed = (name: string) => dashboardPagesCase('run_controls_live_reset_all_mixed.' + name);
    const before = mixed('read_before_the_reset_the_mixed_overrides_are_in_force');
    const accepted = mixed('accepted_null_for_shopping_plan_and_routine_overrides');
    let source = before;
    let page = await openLive(() => source, { newKey: () => 'mx-3' });
    page.fake.route('POST', accepted.request.path, () => 'hold');
    await page.user.click(
      within(liveRegion()).getByRole('button', {
        name: "Use the battle's starting values for all 3",
      }),
    );
    await page.user.click(within(dialog()).getByRole('button', { name: 'Use starting values' }));
    await page.advance(100);
    await act(async () => {
      page.fake.next(accepted.request.path).lose();
      await Promise.resolve();
    });
    await page.advance(100);
    const writes = page.fake.writes();
    expect(writes).toHaveLength(1);
    if (reload) {
      const storage = page.storage;
      page.view.unmount();
      page.data.stop();
      page = await openLive(() => source, { storage });
    }
    const record = page.data.machine.snapshot().findLast((item) => item.kind === 'live_controls');
    expect(record?.restored).toBe(reload);
    expect(record?.meta.resetPaths).toEqual(
      entries(own(accepted.request.body, 'changes')).map(([path]) => path),
    );
    const applied = mixed('read_after_the_mixed_reset_applied');
    for (const payload of [{}, { shopping_plan: null }]) {
      // Fault injection: a matching applied command loses reset payload while applied leaves remain.
      source = {
        ...applied,
        body: {
          ...Object.fromEntries(entries(applied.body)),
          battle: {
            ...Object.fromEntries(entries(own(applied.body, 'battle'))),
            applied_overrides: ownPath(before.body, 'battle', 'applied_overrides'),
            commands: (list(ownPath(applied.body, 'battle', 'commands')) ?? []).map((command) => ({
              ...Object.fromEntries(entries(command)),
              payload,
            })),
          },
        },
      };
      await page.advance(5_000);
      expect(within(liveRegion()).queryByText('Applied to this battle')).toBeNull();
      expect(within(liveRegion()).getByText('A later read lists this change')).toBeVisible();
    }
    source = applied;
    await page.advance(5_000);
    expect(within(liveRegion()).getByText('Applied to this battle')).toBeVisible();
    expect(page.fake.writes()).toHaveLength(reload ? 0 : 1);
  },
);
