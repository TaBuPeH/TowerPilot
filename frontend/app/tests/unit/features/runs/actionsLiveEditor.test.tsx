import { within } from '@testing-library/react';
import { axe } from 'jest-axe';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ownPath } from '../../../../src/core/read.ts';
import { textOutsideCatalogue } from '../../copy/renderedText.ts';
import { e0Config } from '../../../support/dataLayer.ts';
import {
  dashboardCase,
  type DashboardCase,
  dashboardPagesCase,
} from '../../../support/dashboardFixture.ts';
import { sample, statusBody } from '../../../support/fixture.ts';
import { renderPage, type PageOptions } from '../dashboard/harness.tsx';

/**
 * A2-7 AC2 (brief backend/logs/reviews/ui-pages/a2-7-actions-brief.md): the Runs live editor
 * built from each item's descriptor, on the whole wired app with the landed r17b projection
 * reads (tests/support/dashboardFixture.ts). Test-local edits of a recorded body are named where
 * they are made.
 */
vi.setConfig({ testTimeout: 30_000 });

const P = (name: string) => dashboardPagesCase(`battle_actions_projection.${name}`);
const VALUES = () => P('values_carry_every_field_in_force');
const OVERRIDE = () => P('a_live_control_changes_the_value_in_force');
const STALE = () => P('projection_fails_later_the_last_projection_stays_and_its_stamps_stop');
const NEWER = () => P('projection_written_by_a_newer_driver_is_read_only');
const COMPAT = () => P('projection_never_built_the_actions_key_is_absent');
const WRITE = dashboardCase('battle_actions.live_controls_accepted_command_requested');

/** A recorded read with `edit` applied to its battle (test-local change). */
function editedBattle(found: DashboardCase, edit: (battle: Record<string, unknown>) => void) {
  edit(ownPath(found.body, 'battle') as Record<string, unknown>);
  return found;
}

function itemsOf(battle: Record<string, unknown>): Record<string, unknown>[] {
  return ownPath(battle, 'runtime', 'actions', 'items') as Record<string, unknown>[];
}

function fieldOf(battle: Record<string, unknown>, key: string, path: string) {
  const item = itemsOf(battle).find((found) => found['key'] === key);
  const fields = ownPath(item, 'descriptor', 'fields') as Record<string, unknown>[];
  const field = fields.find((found) => found['path'] === path);
  if (field === undefined) throw new Error(`no field ${key}.${path}`);
  return field;
}

async function openLive(read: () => DashboardCase, options: PageOptions = {}) {
  window.location.hash = '#runs/live';
  const page = await renderPage({
    status: () => statusBody(sample('status.off')),
    config: () => e0Config(true),
    ...options,
    before: (fake) => {
      fake.route('GET', /^\/api\/battles\/active$/u, () => {
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

function row(routine: string): HTMLElement {
  const found = liveRegion().querySelector<HTMLElement>(`.tp-run-row[data-routine="${routine}"]`);
  if (found === null) throw new Error(`no row ${routine}`);
  return found;
}

/** The accessible names of a row's controls, in document order. */
function controlNames(routine: string): string[] {
  return [...row(routine).querySelectorAll<HTMLInputElement>('input')].map((input) =>
    (input.getAttribute('aria-labelledby') ?? '')
      .split(' ')
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' '),
  );
}

const save = () =>
  within(liveRegion()).getByRole('button', { name: 'Save changes to this battle' });

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  window.location.hash = '';
  vi.useRealTimers();
});

describe('AC2 the Runs live editor from the action descriptors', () => {
  it('AC2 each item shows its live fields in descriptor order with the descriptor labels, units and ranges', async () => {
    const page = await openLive(VALUES);
    expect(controlNames('ad_gems')).toEqual([
      'Ad gems Enabled',
      'Ad gems Interval',
      'Ad gems Window',
    ]);
    expect(controlNames('floating_gems')).toEqual([
      'Floating gems Enabled',
      'Floating gems Retry spacing after unconfirmed taps',
      'Floating gems Maximum floating gem claim attempt',
      'Floating gems Time between tap sets while seeking',
    ]);
    expect(controlNames('shopping')).toEqual(['Shopping Enabled', 'Shopping Interval']);
    const tap = within(row('floating_gems')).getByRole('textbox', {
      name: 'Floating gems Time between tap sets while seeking',
    });
    expect(tap).toHaveValue('5');
    expect(tap).toHaveAccessibleDescription('0.5 to 30 s');
    expect(
      within(row('floating_gems')).getByRole('checkbox', { name: 'Floating gems Enabled' }),
    ).not.toBeChecked();
    // Every label is visible beside its control; no fixed column header names them.
    expect(liveRegion().querySelector('.tp-run-columns')).toBeNull();
    expect(liveRegion().querySelector('[data-labels="descriptor"]')).not.toBeNull();
    // The S4 gates the projection does not describe keep their one switch row, last.
    const rows = [...liveRegion().querySelectorAll<HTMLElement>('.tp-run-rows > li')];
    expect(rows.at(-1)?.classList.contains('tp-run-row--switches')).toBe(true);
    expect(
      within(rows.at(-1) ?? liveRegion())
        .getAllByRole('checkbox')
        .map((box) => box.closest('label')?.textContent),
    ).toEqual(['Rewards', 'Abilities', 'Rescue', 'Repeat']);
    expect(textOutsideCatalogue(liveRegion())).toEqual([]);
    expect(page.fake.writes()).toEqual([]);
    vi.useRealTimers();
    expect(await axe(page.view.container)).toHaveNoViolations();
  });

  it('AC2 a value outside the descriptor bounds stays typed with its error and range, and nothing is sent', async () => {
    const page = await openLive(VALUES);
    const tap = () =>
      within(liveRegion()).getByRole('textbox', {
        name: 'Floating gems Time between tap sets while seeking',
      });
    await page.user.clear(tap());
    await page.user.type(tap(), '0.4');
    expect(tap()).toHaveValue('0.4');
    expect(tap()).toHaveAttribute('aria-invalid', 'true');
    expect(tap()).toHaveAccessibleDescription('0.5 to 30 s Below the allowed range');
    expect(save()).toHaveAttribute('aria-disabled', 'true');
    expect(within(liveRegion()).getByText('Fix the values marked above')).toBeVisible();
    await page.advance(10_000);
    expect(tap()).toHaveValue('0.4');
    expect(page.fake.writes()).toEqual([]);
  });

  it('AC2 a valid edit sends exactly the routines.<key>.<path> it changed, once', async () => {
    const page = await openLive(VALUES, { newKey: () => 'ac2-1' });
    page.fake.route('POST', /live-controls$/u, () => 'hold');
    await page.user.click(
      within(liveRegion()).getByRole('checkbox', { name: 'Floating gems Enabled' }),
    );
    await page.user.click(save());
    await page.advance(100);
    const battle = ownPath(VALUES().body, 'battle');
    const writes = page.fake.writes();
    expect(writes).toHaveLength(1);
    expect(writes[0]?.path).toBe(
      `/api/operations/${String(ownPath(battle, 'operation_id'))}/live-controls`,
    );
    const sent: unknown = JSON.parse(writes[0]?.body ?? 'null');
    // The recorded write of the same change (D battle_actions.live_controls_accepted_command_requested)
    // sends the same `changes`; ids and revision are this battle's.
    expect(sent).toEqual({
      battle_id: ownPath(battle, 'id'),
      generation: ownPath(battle, 'generation'),
      expected_revision: 0,
      idempotency_key: 'ac2-1',
      changes: ownPath(WRITE.request.body, 'changes'),
    });
  });

  it('AC2 a number edit inside the bounds sends that one path with the typed value', async () => {
    const page = await openLive(VALUES, { newKey: () => 'ac2-2' });
    page.fake.route('POST', /live-controls$/u, () => 'hold');
    const tap = within(liveRegion()).getByRole('textbox', {
      name: 'Floating gems Time between tap sets while seeking',
    });
    await page.user.clear(tap);
    await page.user.type(tap, '2.5');
    await page.user.click(save());
    await page.advance(100);
    expect(JSON.parse(page.fake.writes()[0]?.body ?? 'null')).toMatchObject({
      changes: { 'routines.floating_gems.tap_interval_sec': 2.5 },
    });
  });

  it('AC2 the starting-value reset is offered only where the descriptor field is nullable', async () => {
    const page = await openLive(OVERRIDE);
    const reset = () =>
      within(
        liveRegion().querySelector<HTMLElement>('[data-path="routines.ad_gems.interval_sec"]') ??
          liveRegion(),
      ).getByRole('button', { name: /^Use the battle's starting value/u });
    expect(reset()).not.toHaveAttribute('aria-disabled', 'true');
    page.view.unmount();
    // Test-local: the same battle with interval_sec described as not nullable.
    await openLive(() =>
      editedBattle(OVERRIDE(), (battle) => {
        fieldOf(battle, 'ad_gems', 'interval_sec')['nullable'] = false;
      }),
    );
    expect(reset()).toHaveAttribute('aria-disabled', 'true');
    expect(reset()).toHaveAccessibleDescription('This setting has no starting value to go back to');
  });

  it('AC2 an item that is not its action only instance and names no key is read-only with its reason', async () => {
    // Test-local: a second ad gems instance without a published key (brief gap G2).
    await openLive(() =>
      editedBattle(VALUES(), (battle) => {
        const items = itemsOf(battle);
        const other = structuredClone(items[0]) as Record<string, unknown>;
        delete other['key'];
        other['instance'] = 'ad_gems_two';
        items.push(other);
      }),
    );
    const readOnly = liveRegion().querySelector<HTMLElement>('[data-unaddressable]');
    expect(readOnly?.dataset['unaddressable']).toBe('control_path_unpublished');
    expect(readOnly?.textContent).toContain('Ad gems');
    expect(readOnly?.textContent).toContain("Its settings can't be changed here");
    expect(readOnly?.querySelector('input')).toBeNull();
    expect(controlNames('ad_gems')).toEqual([
      'Ad gems Enabled',
      'Ad gems Interval',
      'Ad gems Window',
    ]);
  });

  it('AC2 an action whose only item names no key is only its read-only row', async () => {
    // Test-local: the ad gems item without its key and with another instance name.
    await openLive(() =>
      editedBattle(VALUES(), (battle) => {
        const ad = itemsOf(battle).find((item) => item['key'] === 'ad_gems');
        if (ad === undefined) throw new Error('no ad gems item');
        delete ad['key'];
        ad['instance'] = 'ad_gems_one';
      }),
    );
    expect(liveRegion().querySelector('.tp-run-row[data-routine="ad_gems"]')).toBeNull();
    expect(liveRegion().querySelector('[data-unaddressable]')?.textContent).toContain('Ad gems');
    expect(within(liveRegion()).queryByRole('checkbox', { name: /^Ad gems/u })).toBeNull();
  });

  it('AC2 a field type the page does not know is shown as not recognised and offers no input', async () => {
    await openLive(() =>
      editedBattle(VALUES(), (battle) => {
        fieldOf(battle, 'ad_gems', 'window_sec')['type'] = 'duration';
      }),
    );
    expect(controlNames('ad_gems')).toEqual(['Ad gems Enabled', 'Ad gems Interval']);
    const cell = row('ad_gems').querySelector<HTMLElement>('[data-unrecognised]');
    expect(cell?.textContent).toContain('Window');
    expect(cell?.textContent).toContain('Not recognised');
    expect(cell?.textContent).toContain('duration');
  });

  it('AC2 old action readings close Save and the resets with action_report_old (root decision Q2)', async () => {
    const page = await openLive(STALE);
    const interval = within(liveRegion()).getByRole('textbox', { name: 'Ad gems Interval' });
    await page.user.clear(interval);
    await page.user.type(interval, '600');
    expect(save()).toHaveAttribute('aria-disabled', 'true');
    expect(save()).toHaveAccessibleDescription(
      'The action readings are old; Run now and changes wait for a current reading',
    );
    const resets = within(liveRegion()).queryAllByRole('button', { name: /starting value/u });
    expect(resets.length).toBeGreaterThan(0);
    for (const button of resets) expect(button).toHaveAttribute('aria-disabled', 'true');
    await page.advance(10_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('AC2 readings written by a newer version close Save with action_schema_newer', async () => {
    const page = await openLive(NEWER);
    await page.user.click(within(liveRegion()).getByRole('checkbox', { name: 'Ad gems Enabled' }));
    expect(save()).toHaveAccessibleDescription(
      'A newer version wrote these action readings; Run now and changes are not offered',
    );
    await page.advance(10_000);
    expect(page.fake.writes()).toEqual([]);
  });

  it('AC2 a battle without a projection keeps the copy labels and the column header', async () => {
    await openLive(COMPAT);
    expect(liveRegion().querySelector('.tp-run-columns')).not.toBeNull();
    expect(controlNames('ad_gems')).toEqual(['Ad gems On', 'Ad gems Every', 'Ad gems Window']);
    expect(controlNames('floating_gems')).toEqual([
      'Floating gems On',
      'Floating gems Every',
      'Floating gems Window',
      'Floating gems Tap every',
    ]);
  });

  it('AC2 test-local: a unit other than seconds is shown as sent, with no duration; a field without one shows none', async () => {
    await openLive(() =>
      editedBattle(VALUES(), (battle) => {
        fieldOf(battle, 'ad_gems', 'interval_sec')['unit'] = 'taps';
        fieldOf(battle, 'ad_gems', 'window_sec')['unit'] = null;
      }),
    );
    const interval = within(row('ad_gems')).getByRole('textbox', { name: 'Ad gems Interval' });
    expect(interval).toHaveAccessibleDescription('1 to 3600 taps');
    const cell = interval.closest<HTMLElement>('.tp-run-cell');
    expect(cell?.querySelector('.tp-run-cell__unit')?.textContent).toBe('taps');
    expect(cell?.querySelector('.tp-run-cell__duration')).toBeNull();
    const window = within(row('ad_gems')).getByRole('textbox', { name: 'Ad gems Window' });
    expect(window).toHaveAccessibleDescription('5 to 60');
    expect(window.closest('.tp-run-cell')?.querySelector('.tp-run-cell__unit')).toBeNull();
    expect(textOutsideCatalogue(liveRegion())).toEqual([]);
  });

  it('AC2 the duration beside a seconds value is one unbroken note', async () => {
    await openLive(VALUES);
    const note = row('ad_gems').querySelector<HTMLElement>('.tp-run-cell__duration');
    expect(note?.textContent).toBe('= 12 m');
  });
});
